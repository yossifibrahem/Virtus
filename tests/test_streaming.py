"""
Tests for streaming.py — SSE event formatting and OpenAI stream event ordering.

The OpenAI client and its stream are fully mocked so no network calls are made.
Tests verify:
  - sse_event encodes correctly
  - text / reasoning / tool_start / tool_calls / done ordering
  - cancellation stops the stream cleanly
  - runtime errors become SSE error events
"""
from __future__ import annotations

import json
import threading
from unittest.mock import MagicMock

import streaming


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_delta(
    *,
    content: str | None = None,
    reasoning: str | None = None,
    tool_calls: list | None = None,
) -> MagicMock:
    delta = MagicMock()
    delta.content = content
    delta.tool_calls = tool_calls
    delta.reasoning_content = reasoning
    return delta


def _make_chunk(
    *,
    content: str | None = None,
    reasoning: str | None = None,
    tool_calls: list | None = None,
    finish_reason: str | None = None,
) -> MagicMock:
    delta = _make_delta(content=content, reasoning=reasoning, tool_calls=tool_calls)
    choice = MagicMock()
    choice.delta = delta
    choice.finish_reason = finish_reason
    chunk = MagicMock()
    chunk.choices = [choice]
    return chunk


def _tool_call_chunk(index: int, call_id: str, name: str, args: str = "") -> MagicMock:
    tc = MagicMock()
    tc.index = index
    tc.id = call_id
    tc.function = MagicMock()
    tc.function.name = name
    tc.function.arguments = args
    return tc


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


# ---------------------------------------------------------------------------
# sse_event
# ---------------------------------------------------------------------------

class TestSseEvent:

    def test_json_round_trips(self):
        """Full format: data prefix, JSON body, double newline."""
        payload = {"type": "tool_start", "name": "bash", "extra": 42}
        raw = streaming.sse_event(payload)
        assert raw.startswith("data: ") and raw.endswith("\n\n")
        assert json.loads(raw[len("data: "):].strip()) == payload


# ---------------------------------------------------------------------------
# Text and reasoning events
# ---------------------------------------------------------------------------

class TestTextAndReasoningEvents:

    def test_content_chunk_emits_text_event(self):
        events = _run_stream([_make_chunk(content="Hello")])
        text_events = [e for e in events if e.get("type") == "text"]
        assert len(text_events) == 1
        assert text_events[0]["content"] == "Hello"

    def test_multiple_content_chunks_all_emitted(self):
        chunks = [_make_chunk(content=c) for c in ["Hello", " ", "world"]]
        events = _run_stream(chunks)
        text_events = [e for e in events if e.get("type") == "text"]
        assert len(text_events) == 3
        assert "".join(e["content"] for e in text_events) == "Hello world"

    def test_reasoning_chunk_emits_reasoning_event(self):
        events = _run_stream([_make_chunk(reasoning="let me think")])
        reasoning = [e for e in events if e.get("type") == "reasoning"]
        assert len(reasoning) == 1
        assert reasoning[0]["content"] == "let me think"

    def test_reasoning_before_text_in_ordering(self):
        chunks = [_make_chunk(reasoning="thinking"), _make_chunk(content="answer")]
        events = _run_stream(chunks)
        types = [e["type"] for e in events if e["type"] in ("reasoning", "text")]
        assert types == ["reasoning", "text"]

    def test_none_content_does_not_emit_text(self):
        events = _run_stream([_make_chunk(content=None)])
        assert not any(e.get("type") == "text" for e in events)


# ---------------------------------------------------------------------------
# Done event
# ---------------------------------------------------------------------------

class TestDoneEvent:

    def test_done_emitted_on_empty_stream(self):
        events = _run_stream([])
        assert any(e["type"] == "done" for e in events)

    def test_done_is_last_event_after_text(self):
        events = _run_stream([_make_chunk(content="hello")])
        assert events[-1]["type"] == "done"
        assert any(e["type"] == "text" for e in events)


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


# ---------------------------------------------------------------------------
# Tool call events
# ---------------------------------------------------------------------------

class TestToolCallEvents:

    def _tool_stream(self, name: str = "bash", args: str = '{"cmd":"ls"}'):
        """Build a minimal chunks list for a single tool call."""
        tc = _tool_call_chunk(0, "call-1", name, args)
        tool_chunk = _make_chunk(tool_calls=[tc])
        finish_chunk = _make_chunk(finish_reason="tool_calls")
        return [tool_chunk, finish_chunk]

    def test_tool_start_emitted_once(self):
        events = _run_stream(self._tool_stream("bash"))
        tool_starts = [e for e in events if e.get("type") == "tool_start"]
        assert len(tool_starts) == 1
        assert tool_starts[0]["name"] == "bash"

    def test_tool_calls_emitted_on_finish_reason(self):
        events = _run_stream(self._tool_stream())
        tool_calls = [e for e in events if e.get("type") == "tool_calls"]
        assert len(tool_calls) == 1
        assert isinstance(tool_calls[0]["calls"], list)

    def test_tool_calls_contain_id_and_function(self):
        events = _run_stream(self._tool_stream("my_tool"))
        call_data = next(e for e in events if e.get("type") == "tool_calls")["calls"][0]
        assert "id" in call_data
        assert "function" in call_data
        assert call_data["function"]["name"] == "my_tool"

    def test_tool_start_emitted_before_tool_calls(self):
        events = _run_stream(self._tool_stream())
        types = [e["type"] for e in events if e["type"] in ("tool_start", "tool_calls")]
        assert types.index("tool_start") < types.index("tool_calls")

    def test_tool_name_accumulated_across_delta_chunks(self):
        """Tool name arriving across multiple delta chunks merges correctly."""
        # First chunk: partial name "ba"
        tc1 = _tool_call_chunk(0, "call-1", "ba", "")
        chunk1 = _make_chunk(tool_calls=[tc1])

        # Second chunk: rest of name "sh"
        tc2 = MagicMock()
        tc2.index = 0
        tc2.id = ""
        tc2.function = MagicMock()
        tc2.function.name = "sh"
        tc2.function.arguments = '{"x":1}'
        chunk2 = _make_chunk(tool_calls=[tc2])

        finish_chunk = _make_chunk(finish_reason="tool_calls")

        events = _run_stream([chunk1, chunk2, finish_chunk])
        tool_calls_event = next(e for e in events if e.get("type") == "tool_calls")
        assert tool_calls_event["calls"][0]["function"]["name"] == "bash"


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------

class TestCancellation:

    def test_cancelled_stream_still_emits_done(self):
        events = _run_stream([_make_chunk(content="hi")], cancel=True)
        assert any(e["type"] == "done" for e in events)

    def test_cancel_mid_stream_closes_openai_stream(self):
        client = MagicMock()
        cancel_event = threading.Event()

        mock_stream = MagicMock()
        call_count = 0

        def chunk_iter():
            nonlocal call_count
            # Set cancel after first chunk
            yield _make_chunk(content="first")
            cancel_event.set()
            yield _make_chunk(content="second")  # should not appear

        mock_stream.__iter__ = MagicMock(return_value=chunk_iter())
        mock_stream.close = MagicMock()
        client.chat.completions.create.return_value = mock_stream

        raw = list(streaming.stream_chat_completion(
            client, "gpt-4o", [], [], cancel_event
        ))
        events = raw

        # The cancel fires after "first", so "second" might or might not appear,
        # but close() must have been called.
        mock_stream.close.assert_called_once()


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:

    def test_api_exception_emits_error_event(self):
        client = MagicMock()
        cancel_event = threading.Event()
        client.chat.completions.create.side_effect = RuntimeError("API failure")

        raw = list(streaming.stream_chat_completion(
            client, "gpt-4o", [], [], cancel_event
        ))
        events = raw

        error_events = [e for e in events if e.get("type") == "error"]
        assert len(error_events) == 1
        assert "API failure" in error_events[0]["message"]

    def test_error_followed_by_done(self):
        client = MagicMock()
        cancel_event = threading.Event()
        client.chat.completions.create.side_effect = ConnectionError("network")

        raw = list(streaming.stream_chat_completion(
            client, "gpt-4o", [], [], cancel_event
        ))
        events = raw
        types = [e["type"] for e in events]
        assert "error" in types
        assert "done" in types
        assert types.index("error") < types.index("done")

    def test_chunk_with_no_choices_skipped(self):
        """Chunks where choices is empty/None must not crash the loop."""
        bad_chunk = MagicMock()
        bad_chunk.choices = []
        good_chunk = _make_chunk(content="ok")
        events = _run_stream([bad_chunk, good_chunk])
        text_events = [e for e in events if e.get("type") == "text"]
        assert len(text_events) == 1

    def test_two_simultaneous_tool_calls_both_in_event(self):
        """Parallel tool calls (index 0 and 1) must both appear in tool_calls event."""
        tc0 = _tool_call_chunk(0, "call-0", "bash", '{"cmd":"ls"}')
        tc1 = _tool_call_chunk(1, "call-1", "read_file", '{"path":"/f"}')
        multi_chunk = _make_chunk(tool_calls=[tc0, tc1])
        finish_chunk = _make_chunk(finish_reason="tool_calls")

        events = _run_stream([multi_chunk, finish_chunk])
        tool_calls_event = next(e for e in events if e.get("type") == "tool_calls")
        calls = tool_calls_event["calls"]
        names = {c["function"]["name"] for c in calls}
        assert names == {"bash", "read_file"}

    def test_two_tool_starts_emitted_for_two_parallel_calls(self):
        tc0 = _tool_call_chunk(0, "call-0", "bash", "")
        tc1 = _tool_call_chunk(1, "call-1", "write_file", "")
        chunk = _make_chunk(tool_calls=[tc0, tc1])
        finish = _make_chunk(finish_reason="tool_calls")

        events = _run_stream([chunk, finish])
        starts = [e for e in events if e.get("type") == "tool_start"]
        names = {e["name"] for e in starts}
        assert "bash" in names
        assert "write_file" in names


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
