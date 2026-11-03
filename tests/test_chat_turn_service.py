"""Chat-turn orchestration, argument validation, and persistence with mocked providers."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

import chat_turn_service as svc


def test_image_fallback_preserves_history_and_covers_tool_rounds(tmp_virtus, monkeypatch):
    import copy
    import threading
    import store
    from conftest import make_png_b64, make_png_bytes

    conv = store.create()
    ref = store.save_image(conv['id'], make_png_bytes(), 'image/png')
    messages = [
        {'role': 'user', 'content': [{'type': 'text', 'text': 'Read this'}, {'type': 'image_ref', 'ref': ref}]},
        {'role': 'assistant', 'content': 'Earlier answer'},
        {'role': 'user', 'content': [{'type': 'image_ref', 'ref': ref}]},
    ]
    original = copy.deepcopy(messages)
    monkeypatch.setattr(svc, 'openai_client', lambda body: MagicMock())
    monkeypatch.setattr(svc, '_inject_memory', lambda messages: messages)
    monkeypatch.setattr(svc.builtin_tools, 'invoke', lambda *a: {
        'text': 'Page 1: extracted PDF text', 'images': [{'data': make_png_b64()}]})
    requests, events = [], []
    def stream(*args, **kwargs):
        requests.append(copy.deepcopy({k: v for k, v in kwargs.items() if k != 'cancel_event'}))
        if len(requests) == 1:
            yield {'type': 'error', 'code': 'image_inputs_unsupported', 'message': 'Unsupported images'}
        elif len(requests) == 2:
            yield {'type': 'tool_calls', 'calls': [{'id': 'one', 'function': {
                'name': 'view', 'arguments': '{"path":"document.pdf"}'}}]}
        else:
            yield {'type': 'text', 'content': 'Answer from text'}
    monkeypatch.setattr(svc.stream_module, 'stream_chat_completion', stream)
    body = {'conv_id': conv['id'], 'messages': messages, 'conversation_messages': messages,
            'auto_generate_titles': False, 'model': 'local-model', 'temperature': .3}
    svc.run_persistent_chat_turn(body, threading.Event(), 'fallback', events.append)
    assert len(requests) == 3, events
    assert requests[0]['messages'][0]['content'][1]['type'] == 'image_url'
    for request in requests[1:]:
        assert all(isinstance(m.get('content'), (str, type(None))) for m in request['messages'])
        assert 'Read this' in request['messages'][0]['content']
        assert 'Image unavailable' in request['messages'][2]['content']
        assert request['model'] == 'local-model' and request['temperature'] == .3
        assert request['tools'] == requests[0]['tools']
    assert 'extracted PDF text' in requests[2]['messages'][-2]['content']
    assert 'Image unavailable' in requests[2]['messages'][-1]['content']
    saved = store.load(conv['id'])
    assert saved['messages'][:3] == original
    assert messages == original
    assert saved['messages'][-2]['_tool_media']
    assert saved['messages'][-1]['content'] == 'Answer from text'
    assert len([e for e in events if e['type'] == 'status']) == 1
    assert [e for e in events if e['type'] == 'status'][0] in saved['displayLog']
    assert not any(e['type'] == 'error' for e in events)

    # A new turn probes images again rather than remembering an old model's limitation.
    requests.clear()
    svc.run_persistent_chat_turn(body, threading.Event(), 'new-turn', lambda e: None)
    assert requests[0]['messages'][0]['content'][1]['type'] == 'image_url'


@pytest.mark.parametrize('mode', ['retry_failure', 'text', 'reasoning', 'tool_start', 'unrelated', 'no_images', 'cancel', 'deadline'])
def test_image_fallback_does_not_loop_or_retry_after_output(tmp_virtus, monkeypatch, mode):
    import threading
    from types import SimpleNamespace
    import store
    cancel = threading.Event()
    conv = store.create()
    messages = [{'role': 'user', 'content': [{'type': 'image_url', 'image_url': {'url': 'data:...'}}]}]
    if mode == 'no_images':
        messages = [{'role': 'user', 'content': 'Hello'}]
    monkeypatch.setattr(svc, 'openai_client', lambda body: MagicMock())
    monkeypatch.setattr(svc, '_inject_memory', lambda messages: messages)
    clock = SimpleNamespace(now=0.)
    if mode == 'deadline':
        monkeypatch.setattr(svc, 'time', SimpleNamespace(monotonic=lambda: clock.now))
    calls, events = [], []
    def stream(*args, **kwargs):
        calls.append(kwargs)
        if mode in {'text', 'reasoning', 'tool_start'}:
            yield {'type': mode, 'content': 'Partial', 'name': 'view'}
        if mode == 'cancel':
            cancel.set()
        if mode == 'deadline':
            clock.now = float('inf')
        yield {'type': 'error', 'message': 'Failure',
               'code': 'other' if mode == 'unrelated' else 'image_inputs_unsupported'}
    monkeypatch.setattr(svc.stream_module, 'stream_chat_completion', stream)
    svc.run_persistent_chat_turn({'conv_id': conv['id'], 'messages': messages,
                                  'auto_generate_titles': False}, cancel, 'guard', events.append)
    assert len(calls) == (2 if mode == 'retry_failure' else 1)
    assert len([e for e in events if e['type'] == 'status']) == (1 if mode == 'retry_failure' else 0)
    assert any(e['type'] == 'error' for e in events)
    if mode == 'text':
        assert store.load(conv['id'])['messages'][-1]['content'] == 'Partial'


def test_stream_snapshots_preserve_manual_renames_and_settings(tmp_virtus):
    import store

    conversation = store.create("Original")
    recorder = svc.TurnRecorder(conversation["id"], "Original", [], "stream-1")
    recorder.save([], force=True)
    store.update(conversation["id"], {"title": "My title", "system_prompt": "My instructions"})
    recorder.messages.append({"role": "assistant", "content": "Partial"})
    recorder.save([], force=True)
    recorder.update_title("Generated title")
    assert recorder.finalize([]) == "My title"
    saved = store.load(conversation["id"])
    assert saved["title"] == "My title"
    assert saved["system_prompt"] == "My instructions"
    assert saved["messages"] == recorder.messages
    assert not saved["streaming"] and "active_stream_id" not in saved


def test_turn_save_does_not_recreate_a_deleted_chat(tmp_virtus):
    import store

    conversation = store.create()
    store.delete(conversation["id"])
    with pytest.raises(ValueError, match="Conversation no longer exists"):
        svc._save_turn(conversation["id"], "Stale", [], [])
    assert store.list_all() == []


class TestParseStreamPayload:
    """Streaming internals are dict-based; SSE encoding happens only at the route boundary."""

    def test_dict_event_is_returned(self):
        payload = {"type": "text", "content": "hi"}
        assert svc._parse_stream_payload(payload) is payload


    def test_sse_string_is_ignored(self):
        assert svc._parse_stream_payload('data: {"type": "text", "content": "hi"}') is None

    def test_non_dict_is_ignored(self):
        assert svc._parse_stream_payload(None) is None
        assert svc._parse_stream_payload(123) is None


class TestSafeToolArgs:
    """
    Invalid model arguments must never turn into executable default arguments.
    """

    def test_valid_json_returns_dict(self):
        assert svc._safe_tool_args('{"cmd": "ls", "path": "/"}') == {"cmd": "ls", "path": "/"}

    def test_empty_string_returns_empty_dict(self):
        assert svc._safe_tool_args("") == {}

    @pytest.mark.parametrize("raw", ['{bad json', '[]', 'null', '1', None, 'x' * (svc.MAX_TOOL_ARGUMENT_BYTES + 1)],
                             ids=['malformed', 'array', 'null', 'number', 'missing', 'oversized'])
    def test_invalid_arguments_are_rejected(self, raw):
        with pytest.raises(ValueError):
            svc._safe_tool_args(raw)

    def test_none_like_empty_returns_empty_dict(self):
        assert svc._safe_tool_args("{}") == {}

    def test_nested_object_preserved(self):
        args = '{"options": {"flag": true, "count": 3}}'
        assert svc._safe_tool_args(args) == {"options": {"flag": True, "count": 3}}


class TestToolCallMessage:
    """
    Constructs the assistant message that gets appended to api_messages for
    the next model turn. The OpenAI API is strict about this format.
    """

    def _make_call(self, call_id: str, name: str, args: str = "{}") -> dict:
        return {"id": call_id, "function": {"name": name, "arguments": args}}


    def test_each_call_has_openai_shape(self):
        msg = svc._tool_call_message([self._make_call("c1", "read_file", '{"path":"/f"}')], None)
        tc = msg["tool_calls"][0]
        assert tc["id"] == "c1"
        assert tc["type"] == "function"
        assert tc["function"]["name"] == "read_file"
        assert tc["function"]["arguments"] == '{"path":"/f"}'

    def test_multiple_calls_all_included(self):
        calls = [self._make_call("c1", "bash"), self._make_call("c2", "write_file")]
        msg = svc._tool_call_message(calls, None)
        assert len(msg["tool_calls"]) == 2
        names = {tc["function"]["name"] for tc in msg["tool_calls"]}
        assert names == {"bash", "write_file"}

    def test_content_set_when_provided(self):
        msg = svc._tool_call_message([self._make_call("c1", "bash")], "partial text")
        assert msg["content"] == "partial text"

    def test_content_none_when_not_provided(self):
        msg = svc._tool_call_message([self._make_call("c1", "bash")], None)
        assert msg["content"] is None


class TestTurnRecorder:
    """
    TurnRecorder throttles disk writes during streaming to avoid hammering the
    filesystem on every token. The key contracts are:
      - force=True always writes regardless of throttle window
      - Within the throttle window, writes are skipped regardless of chunk size
      - finalize() always writes with no active stream_id
    """

    def _make_recorder(self) -> svc.TurnRecorder:
        return svc.TurnRecorder(conv_id="stub", title="T", messages=[], stream_id="s1")

    def test_force_true_always_saves(self):
        recorder = self._make_recorder()
        with patch("chat_turn_service._save_turn") as mock_save:
            recorder.save([], force=True)
            recorder.save([], force=True)
        assert mock_save.call_count == 2

    def test_rapid_small_saves_throttled(self):
        """Calls within the 0.75s window with no size growth must be skipped."""
        recorder = self._make_recorder()
        with patch("chat_turn_service._save_turn") as mock_save:
            recorder.save([], force=True)  # first call seeds the timer
            recorder.save([], reasoning="a", text="b")  # within window, small delta
            recorder.save([], reasoning="a", text="b")  # still within window
        # Only the forced first call should have persisted
        assert mock_save.call_count == 1

    def test_large_chunks_do_not_bypass_throttle(self):
        """Large chunks still respect the 0.75s persistence window."""
        recorder = self._make_recorder()
        big_text = "x" * 600
        with patch("chat_turn_service._save_turn") as mock_save:
            recorder.save([], force=True)
            recorder.save([], text=big_text)
        assert mock_save.call_count == 1

    def test_finalize_calls_save_turn_without_stream_id(self):
        recorder = self._make_recorder()
        with patch("chat_turn_service._save_turn") as mock_save:
            recorder.finalize([])
        _, kwargs = mock_save.call_args
        # _save_turn(conv_id, title, messages, display_log) — no stream_id kwarg
        assert "stream_id" not in kwargs or kwargs.get("stream_id") == ""

    def test_update_title_changes_saved_title(self):
        recorder = self._make_recorder()
        recorder.update_title("New Title")
        with patch("chat_turn_service._save_turn") as mock_save:
            recorder.save([], force=True)
        _, title_arg, *_ = mock_save.call_args.args
        assert title_arg == "New Title"


def test_model_error_preserves_partial_response_and_closes_client(tmp_virtus, monkeypatch):
    import threading
    import store
    conv = store.create()
    client = MagicMock()
    monkeypatch.setattr(svc, 'openai_client', lambda body: client)
    monkeypatch.setattr(svc, '_inject_memory', lambda messages: messages)
    monkeypatch.setattr(svc.stream_module, 'stream_chat_completion', lambda *a, **k: iter([
        {'type': 'text', 'content': 'Partial'}, {'type': 'error', 'message': 'Disconnected'}]))
    events = []
    svc.run_persistent_chat_turn({'conv_id': conv['id']}, threading.Event(), 'error-test', events.append)
    saved = store.load(conv['id'])
    assert saved['messages'][-1]['content'] == 'Partial'
    assert not saved['streaming']
    assert len([event for event in events if event['type'] == 'error']) == 1
    assert events[-1]['type'] == 'assistant_done'
    assert events[-1]['messages'] == saved['messages']
    assert events[-1]['displayLog'] == saved['displayLog']
    client.close.assert_called_once()


@pytest.mark.parametrize('phase', ['headers', 'body', 'title'])
def test_cancel_interrupts_http_and_saves_partial_history(tmp_virtus, monkeypatch, phase):
    import asyncio
    import threading
    import time
    import httpx
    from openai import AsyncOpenAI
    import store

    cancel = threading.Event()
    interrupted = []

    async def stall():
        cancel.set()
        try:
            await asyncio.sleep(2)
        except asyncio.CancelledError:
            interrupted.append(True)
            raise

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"Partial"}}]}\n\n'
            await stall()

    async def handle(request):
        if phase != 'body':
            await stall()
        return httpx.Response(200, stream=Body())

    transport = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(svc.stream_module, '_async_client', lambda client: AsyncOpenAI(
        api_key='test', base_url='http://model.test/v1', max_retries=0, http_client=transport))
    client = MagicMock()
    client.with_options.return_value = client
    monkeypatch.setattr(svc, 'openai_client', lambda body: client)
    if phase == 'title':
        monkeypatch.setattr(svc.stream_module, 'stream_chat_completion',
                            lambda *a, **k: iter([{'type': 'text', 'content': 'Partial'}]))
    conv = store.create()
    events = []
    started = time.monotonic()
    svc.run_persistent_chat_turn({
        'conv_id': conv['id'], 'conversation_messages': [{'role': 'user', 'content': 'Test'}],
        'auto_generate_titles': phase == 'title',
    }, cancel, 'cancel-test', events.append)
    assert time.monotonic() - started < 1.5
    assert interrupted and transport.is_closed
    saved = store.load(conv['id'])
    assert not saved.get('active_stream_id') and not saved['streaming']
    assert saved['displayLog'][-1]['content'] == 'Response stopped.'
    assert events[-1]['type'] == 'assistant_done'
    assert events[-1]['messages'] == saved['messages']
    if phase != 'headers':
        assert saved['messages'][-1]['content'] == 'Partial'


def test_one_api_client_is_reused_across_tool_iterations(tmp_virtus, monkeypatch):
    import threading
    import store
    conv = store.create()
    client = MagicMock()
    create = MagicMock(return_value=client)
    monkeypatch.setattr(svc, 'openai_client', create)
    monkeypatch.setattr(svc, '_inject_memory', lambda messages: messages)
    clients = []
    def stream(current, **kwargs):
        clients.append(current)
        if len(clients) == 1:
            yield {'type': 'tool_calls', 'calls': [{'id': 'one', 'function': {
                'name': 'view', 'arguments': '{"path":"x"}'}}]}
        else:
            yield {'type': 'text', 'content': 'Done'}
    monkeypatch.setattr(svc.stream_module, 'stream_chat_completion', stream)
    monkeypatch.setattr(svc.builtin_tools, 'invoke', lambda *a: {'text': 'File'})
    svc.run_persistent_chat_turn({'conv_id': conv['id']}, threading.Event(), 'reuse-test', lambda event: None)
    assert clients == [client, client]
    create.assert_called_once()
    client.close.assert_called_once()


@pytest.mark.parametrize('mode', ['cancel_native', 'cancel_approval', 'runtime_failure', 'invalid_native', 'invalid_mcp'])
def test_tool_batches_always_have_matching_results(tmp_virtus, monkeypatch, mode):
    import threading
    import store
    from contextlib import contextmanager

    conv = store.create()
    cancel = threading.Event()
    client = MagicMock()
    pool = MagicMock()
    monkeypatch.setattr(svc, 'openai_client', lambda body: client)
    monkeypatch.setattr(svc, '_inject_memory', lambda messages: messages)
    monkeypatch.setattr(svc.mcp_service, 'get_persistent_pool', lambda runtime: pool)
    native = mode in {'cancel_native', 'invalid_native'}
    name = 'view' if native else 'external'
    calls = [{'id': str(i), 'function': {'name': name, 'arguments': '{bad' if mode.startswith('invalid') else '{}'}}
             for i in range(2)]
    requests = []
    def stream(*args, **kwargs):
        requests.append(kwargs['messages'])
        if len(requests) == 1:
            yield {'type': 'text', 'content': 'Checking files'}
            yield {'type': 'tool_calls', 'calls': calls}
        else:
            yield {'type': 'text', 'content': 'Finished'}
    def invoke(*args):
        cancel.set()
        return {'text': 'first result'}
    def approval(*args):
        cancel.set()
        return False
    @contextmanager
    def failed_runtime(*args):
        raise ValueError('Container unavailable')
        yield
    native_invoke = MagicMock(side_effect=invoke)
    monkeypatch.setattr(svc.builtin_tools, 'invoke', native_invoke)
    monkeypatch.setattr(svc.tool_approval, 'request_tool_approval', approval)
    monkeypatch.setattr(svc.sandbox_runtime, 'runtime_operation', failed_runtime)
    monkeypatch.setattr(svc.stream_module, 'stream_chat_completion', stream)
    events = []
    def publish(event):
        if event['type'] == 'assistant_done':
            saved = store.load(conv['id'])
            assert not saved['streaming']
            assert saved['messages'] == event['messages']
        events.append(event)
    svc.run_persistent_chat_turn({
        'conv_id': conv['id'],
        'mcp_tool_meta': [] if native else [{'name': name, 'server': 'example', 'autoApprove': mode != 'cancel_approval'}],
    }, cancel, 'batch-test', publish)
    saved = store.load(conv['id'])
    results = [m for m in saved['messages'] if m['role'] == 'tool']
    assert [m['tool_call_id'] for m in results] == ['0', '1']
    assert len([m for m in saved['messages'] if m.get('content') == 'Checking files']) == 1
    assert native_invoke.call_count == (1 if mode == 'cancel_native' else 0)
    pool.invoke_tool.assert_not_called()
    if cancel.is_set():
        assert len(requests) == 1
        assert saved['displayLog'][-1]['content'] == 'Response stopped.'
    else:
        assert len(requests) == 2
        assert all(m['content'].startswith('Error:') for m in results)
    assert events[-1]['type'] == 'assistant_done'
    client.close.assert_called_once()


def test_unexpected_stream_exception_keeps_partial_text(tmp_virtus, monkeypatch):
    import threading
    import store
    conv = store.create()
    monkeypatch.setattr(svc, 'openai_client', lambda body: MagicMock())
    monkeypatch.setattr(svc, '_inject_memory', lambda messages: messages)
    def stream(*args, **kwargs):
        yield {'type': 'text', 'content': 'Keep this'}
        raise RuntimeError('Broken iterator')
    monkeypatch.setattr(svc.stream_module, 'stream_chat_completion', stream)
    svc.run_persistent_chat_turn({'conv_id': conv['id']}, threading.Event(), 'broken', lambda event: None)
    assert store.load(conv['id'])['messages'] == [{'role': 'assistant', 'content': 'Keep this'}]


def test_uploaded_image_refs_expand_only_at_provider_boundary(tmp_virtus):
    import store
    import tool_media
    from conftest import make_png_b64, make_png_bytes
    conversation = store.create()
    ref = store.save_image(conversation['id'], make_png_bytes(), 'image/png')
    messages = [{'role': 'user', 'content': [{'type': 'image_ref', 'ref': ref}]}]
    expanded = tool_media.expand(messages, conversation['id'])
    part = expanded[0]['content'][0]
    assert part == {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + make_png_b64()}}
    assert messages[0]['content'][0] == {'type': 'image_ref', 'ref': ref}
    with pytest.raises(ValueError, match='image no longer available'):
        tool_media.expand([{'role': 'user', 'content': [{'type': 'image_ref', 'ref': 'missing'}]}], '')


def test_memory_injection_supports_system_text_blocks(monkeypatch):
    svc.memory_service.update('Remember this', svc.memory_service.snapshot()['revision'])
    blocks = [{"type": "text", "text": "System instructions"}]
    original = [{"role": "system", "content": blocks}]
    messages = svc._inject_memory(original)
    assert messages[0]['content'][0] == blocks[0]
    assert 'Remember this' in messages[0]['content'][1]['text']
    assert 'subordinate' in messages[0]['content'][1]['text']
    assert original[0]["content"] is blocks
    assert len(blocks) == 1
