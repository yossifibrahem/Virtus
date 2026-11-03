"""Provider event parsing, ordering, cancellation, and errors without network access."""
from __future__ import annotations

import json
import threading
from unittest.mock import MagicMock
from types import SimpleNamespace

import pytest
import streaming


@pytest.mark.parametrize('status,message,expected', [
    (400, 'The provided messages contain images, but current_model does not support image inputs.', True),
    (400, 'Image inputs are not supported by this model.', True),
    (422, 'This model does not support images.', True),
    (400, 'Invalid image data.', False),
    (400, 'Image exceeds maximum size.', False),
    (400, 'Invalid messages.', False),
    (401, 'This model does not support image inputs.', False),
    (500, 'This model does not support image inputs.', False),
])
def test_image_capability_error_code(status, message, expected):
    import httpx
    from openai import APIStatusError
    client = MagicMock()
    client.chat.completions.create.side_effect = APIStatusError(
        message, response=httpx.Response(status, request=httpx.Request('POST', 'http://model.test')),
        body={'error': {'message': message, 'type': 'invalid_request_error'}},
    )
    events = list(streaming.stream_chat_completion(client, 'local', [], [], threading.Event()))
    assert events[0]['type'] == 'error'
    assert (events[0].get('code') == 'image_inputs_unsupported') is expected
    assert events[-1] == {'type': 'done'}


@pytest.fixture(autouse=True)
def mock_provider_chunks(monkeypatch):
    """These unit tests cover event parsing; transport cancellation is tested separately."""
    def chunks(client, request, cancel):
        stream = client.chat.completions.create(**request)
        try:
            for chunk in stream:
                if cancel.is_set():
                    break
                yield chunk
        finally:
            stream.close()
    monkeypatch.setattr(streaming, '_cancellable_chunks', chunks)


def _make_chunk(*, content=None, reasoning=None, tool_calls=None, finish_reason=None):
    delta = SimpleNamespace(content=content, reasoning_content=reasoning, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)])


def _tool_call_chunk(index, call_id, name, args=""):
    return SimpleNamespace(index=index, id=call_id, function=SimpleNamespace(name=name, arguments=args))


def _run_stream(chunks: list, cancel: bool = False) -> list[dict]:
    """Run stream_chat_completion with mocked OpenAI client and return parsed events."""
    client = MagicMock()
    cancel_event = threading.Event()
    if cancel:
        cancel_event.set()

    mock_stream = MagicMock()
    mock_stream.__iter__ = MagicMock(return_value=iter(chunks))
    mock_stream.close = MagicMock()
    client.chat.completions.create.return_value = mock_stream

    return list(streaming.stream_chat_completion(
        client=client,
        model="gpt-4o",
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        cancel_event=cancel_event,
    ))


class TestSseEvent:

    def test_json_round_trips(self):
        """Full format: data prefix, JSON body, double newline."""
        payload = {"type": "tool_start", "name": "bash", "extra": 42}
        raw = streaming.sse_event(payload)
        assert raw.startswith("data: ") and raw.endswith("\n\n")
        assert json.loads(raw[len("data: "):].strip()) == payload


def test_text_and_reasoning_preserve_content_and_order():
    events = _run_stream([
        _make_chunk(reasoning="thinking"),
        _make_chunk(content="Hello"), _make_chunk(), _make_chunk(content=" world"),
    ])
    assert events == [
        {"type": "reasoning", "content": "thinking"},
        {"type": "text", "content": "Hello"},
        {"type": "text", "content": " world"},
        {"type": "done"},
    ]


@pytest.mark.parametrize("cancelled", [False, True])
def test_empty_or_cancelled_stream_finishes(cancelled):
    chunks = [_make_chunk(content="ignored")] if cancelled else []
    assert _run_stream(chunks, cancel=cancelled) == [{"type": "done"}]


class TestRequestOptions:

    def test_timeout_is_forwarded_to_openai_request(self):
        client = MagicMock()
        mock_stream = MagicMock()
        mock_stream.__iter__ = MagicMock(return_value=iter([]))
        client.chat.completions.create.return_value = mock_stream

        list(streaming.stream_chat_completion(
            client,
            "gpt-4o",
            [],
            [],
            threading.Event(),
            timeout=12.5,
        ))

        assert client.chat.completions.create.call_args.kwargs["timeout"] == 12.5


def test_parallel_tool_calls_preserve_names_arguments_and_event_order():
    events = _run_stream([
        _make_chunk(tool_calls=[_tool_call_chunk(0, "one", "ba", '{"command":')]),
        _make_chunk(tool_calls=[_tool_call_chunk(0, "", "sh", '"ls"}')]),
        _make_chunk(tool_calls=[_tool_call_chunk(1, "two", "view", '{"path":"/f"}')]),
        _make_chunk(finish_reason="tool_calls"),
    ])
    assert [event["type"] for event in events] == ["tool_start", "tool_start", "tool_calls", "done"]
    calls = events[2]["calls"]
    assert calls == [
        {"id": "one", "function": {"name": "bash", "arguments": '{"command":"ls"}'}},
        {"id": "two", "function": {"name": "view", "arguments": '{"path":"/f"}'}},
    ]


def test_cancel_mid_stream_stops_output_and_closes_provider():
    client = MagicMock()
    cancel = threading.Event()

    def chunks():
        yield _make_chunk(content="first")
        cancel.set()
        yield _make_chunk(content="second")

    stream = client.chat.completions.create.return_value
    stream.__iter__.return_value = chunks()
    events = list(streaming.stream_chat_completion(client, "test-model", [], [], cancel))
    assert events == [{"type": "text", "content": "first"}, {"type": "done"}]
    stream.close.assert_called_once()


@pytest.mark.parametrize("failure", [RuntimeError("API failure"), TimeoutError("API failure")])
def test_provider_errors_are_followed_by_done(failure):
    client = MagicMock()
    client.chat.completions.create.side_effect = failure
    events = list(streaming.stream_chat_completion(client, "test-model", [], [], threading.Event()))
    assert events == [{"type": "error", "message": "API failure"}, {"type": "done"}]


def test_chunks_without_choices_are_skipped():
    events = _run_stream([SimpleNamespace(choices=[]), _make_chunk(content="ok")])
    assert events == [{"type": "text", "content": "ok"}, {"type": "done"}]


def test_fragmented_tool_arguments_are_preserved_for_parallel_calls():
    chunks = [
        _make_chunk(tool_calls=[_tool_call_chunk(0, "one", "write", '{"content":"')]),
        _make_chunk(tool_calls=[_tool_call_chunk(1, "two", "view", '{"path":"')]),
        *[_make_chunk(tool_calls=[_tool_call_chunk(0, "", "", "مرحبا")]) for _ in range(100)],
        _make_chunk(tool_calls=[_tool_call_chunk(1, "", "", '/workspace"}')]),
        _make_chunk(tool_calls=[_tool_call_chunk(0, "", "", '"}')]),
        _make_chunk(finish_reason="tool_calls"),
    ]
    calls = next(event["calls"] for event in _run_stream(chunks) if event["type"] == "tool_calls")
    assert json.loads(calls[0]["function"]["arguments"]) == {"content": "مرحبا" * 100}
    assert json.loads(calls[1]["function"]["arguments"]) == {"path": "/workspace"}
