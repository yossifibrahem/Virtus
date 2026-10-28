"""Native contracts, worker behavior, transport boundaries, and media ordering."""
import json
import os
from pathlib import Path
import threading
from unittest.mock import Mock
import pytest
import builtin_tools as native
import sandbox_runtime
import sandbox_tools as worker
import container_service
import store
import tool_media


@pytest.mark.parametrize('name,args', [
    ('bash', {'command': 'ls', 'timeout': True}), ('bash', {'command': 'ls', 'timeout': 601}),
    ('bash', {'command': ''}), ('view', {'path': 'a', 'host': '/'}), ('view', []),
    ('write', {'path': 'a', 'content': 1}), ('edit', {'path': 'a', 'old_text': '', 'new_text': ''}),
    ('edit', {'path': 'a', 'old_text': 'x', 'new_text': '', 'replace_all': 'true'}),
    ('view', {'path': '\0'}), ('write', {'path': 'a', 'content': 'x' * (native.FILE_LIMIT + 1)}),
])
def test_validation(name, args):
    with pytest.raises(ValueError):
        native.validate(name, args)


def test_write_edit_exact_and_newlines(tmp_path):
    path = tmp_path / 'nested' / 'file.txt'
    worker.write(path, 'one\r\ntwo\r\none\r\n')
    path.chmod(0o640)
    with pytest.raises(ValueError, match='2 matches'):
        worker.write(path, 'three', 'one')
    with pytest.raises(ValueError, match='not found'):
        worker.write(path, '', 'missing')
    worker.write(path, 'three', 'one', True)
    assert path.read_bytes() == b'three\r\ntwo\r\nthree\r\n'
    assert path.stat().st_mode & 0o777 == 0o640
    worker.write(path, '')
    assert path.read_bytes() == b''


def test_concurrent_edit_rejected(tmp_path, monkeypatch):
    path = tmp_path / 'file'
    path.write_text('before')
    original = worker.read_bytes
    def read_and_modify(*args):
        raw = original(*args)
        path.write_text('concurrent change')
        return raw
    monkeypatch.setattr(worker, 'read_bytes', read_and_modify)
    with pytest.raises(ValueError, match='concurrently'):
        worker.write(path, 'after', 'before')
    assert path.read_text() == 'concurrent change'


@pytest.mark.parametrize('content', ['hello', 'hello\n', 'first\nlast', 'first\nlast\n'])
def test_complete_lines_are_not_marked_truncated(tmp_path, content):
    path = tmp_path / 'text'
    path.write_text(content)
    assert worker.view(path)['text'] == '\n'.join(
        f'{number}: {line}' for number, line in enumerate(content.splitlines(), 1)
    )


@pytest.mark.parametrize('ending', ['', '\n'])
@pytest.mark.parametrize('length', [7, 8, 9, 20])
def test_line_truncation_requires_omitted_text(tmp_path, monkeypatch, ending, length):
    # A small line cap makes the per-line marker visible within the output cap.
    monkeypatch.setattr(worker, 'TEXT_LIMIT', 8)
    path = tmp_path / 'text'
    path.write_text('x' * length + ending)
    result = worker.view(path)['text']
    expected = '1: ' + 'x' * min(length, 8)
    if length > 8:
        expected += ' [line truncated]'
    assert result == expected


def test_ranges_and_long_lines(tmp_path):
    path = tmp_path / 'text'
    path.write_text(''.join(f'line {n}\n' for n in range(1, 501)))
    result = worker.view(path, '20:21')['text']
    assert result.startswith('20: line 20\n21: line 21')
    assert '22:221' in result
    assert '201:400' in worker.view(path)['text']
    path.write_text('x' * 1000000 + '\nlast\n')
    assert len(worker.view(path)['text'].encode()) <= worker.TEXT_LIMIT
    assert worker.view(path, '2:2')['text'] == '2: last'


@pytest.mark.parametrize('value', ['0:2', '2:1', '1:201', '1', 'a:b', True])
def test_invalid_ranges(value, tmp_path):
    path = tmp_path / 'a'; path.write_text('a')
    with pytest.raises(ValueError):
        worker.view(path, value)


def test_directory_depth_hidden_and_limit(tmp_path):
    (tmp_path / '.secret').write_text('secret')
    (tmp_path / 'node_modules').mkdir()
    deep = tmp_path / 'one' / 'two'; deep.mkdir(parents=True)
    (deep / 'invisible').write_text('x')
    (deep.parent / 'visible').write_text('x')
    result = worker.view(tmp_path)['text']
    assert 'one/' in result and 'two/' in result and 'visible' in result
    assert 'invisible' not in result and '.secret' not in result and 'node_modules' not in result
    for n in range(220):
        (tmp_path / str(n)).touch()
    assert 'truncated' in worker.view(tmp_path)['text']


def test_binary_and_fifo(tmp_path):
    path = tmp_path / 'binary'; path.write_bytes(b'\0\xff')
    with pytest.raises(ValueError):
        worker.view(path)
    fifo = tmp_path / 'fifo'; os.mkfifo(fifo)
    with pytest.raises(ValueError, match='regular'):
        worker.view(fifo)


def test_notebook_is_not_executed(tmp_path):
    path = tmp_path / 'a.ipynb'
    path.write_text(json.dumps({'cells': [{'cell_type': 'code', 'source': ['raise Exception()'],
                                         'outputs': [{'text': ['saved output']}]}]}))
    result = worker.view(path)['text']
    assert 'raise Exception()' in result and 'saved output' in result


def test_capture_keeps_head_tail_bounded():
    capture = sandbox_runtime.Capture(32768, True)
    capture.add(b'HEAD' + b'x' * 1000000 + b'TAIL')
    assert capture.value().startswith(b'HEAD') and capture.value().endswith(b'TAIL')
    assert len(capture.value()) <= 32768


def test_tool_uses_structured_stdin_never_host_shell(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(container_service, 'ensure_container', lambda *a: SimpleNamespace(reset_reason=''))
    call = Mock(return_value=(0, b'{"text":"ok"}', b''))
    monkeypatch.setattr(sandbox_runtime, 'docker_operation', call)
    native.invoke('runtime', 'view', {'path': '$(touch /host);file'})
    args = call.call_args.args
    assert args[1] == ['/opt/venv/bin/python', '-I', '/opt/virtus/sandbox_tools.py']
    assert json.loads(args[2])['args']['path'] == '$(touch /host);file'


def test_cancel_before_lock_does_not_execute(monkeypatch):
    call = Mock(); monkeypatch.setattr(sandbox_runtime, 'docker_operation', call)
    cancel = threading.Event(); cancel.set()
    with pytest.raises(ValueError, match='cancelled'):
        native.invoke('runtime', 'bash', {'command': 'echo x'}, cancel)
    call.assert_not_called()


def test_media_conversation_scoping_and_order(tmp_virtus):
    a, b = store.create(), store.create()
    import base64
    refs = tool_media.save(a['id'], [{'data': base64.b64encode(b'\xff\xd8\xfftest').decode()}])
    with pytest.raises(ValueError):
        tool_media.path(b['id'], refs[0])
    messages = [{'role': 'tool', 'content': 'image', '_tool_media': refs}, {'role': 'tool', 'content': 'other'}]
    expanded = tool_media.expand(messages, a['id'])
    assert [m['role'] for m in expanded] == ['tool', 'tool', 'user']
    assert '_tool_media' not in expanded[0]
    assert len(messages) == 2


def test_unexpected_mount_rejected(monkeypatch, tmp_path):
    from types import SimpleNamespace
    mounts = [{'Type': 'bind', 'RW': True, 'Destination': '/workspace', 'Source': str(tmp_path)},
              {'Type': 'bind', 'RW': True, 'Destination': '/host', 'Source': '/'}]
    monkeypatch.setattr(container_service, '_run', lambda args: SimpleNamespace(returncode=0, stdout=json.dumps(mounts)))
    assert not container_service._mounts_match(mounts, tmp_path)
    mounts.pop()
    assert container_service._mounts_match(mounts, tmp_path)


def test_native_schemas_injected_without_mcp(tmp_virtus, monkeypatch):
    import chat_turn_service as turns
    conv = store.create()
    observed = []
    def stream(*args, **kwargs):
        observed.append(kwargs)
        if len(observed) == 1:
            yield {'type': 'tool_calls', 'calls': [{'id': 'call1', 'function': {'name': 'write', 'arguments': '{"path":"x","content":"ok"}'}}]}
        else:
            yield {'type': 'text', 'content': 'done'}
        yield {'type': 'done'}
    monkeypatch.setattr(turns.stream_module, 'stream_chat_completion', stream)
    monkeypatch.setattr(turns, 'openai_client', lambda body: None)
    monkeypatch.setattr(turns, '_inject_memory', lambda messages: messages)
    invoke = Mock(return_value={'text': 'Wrote x'})
    monkeypatch.setattr(native, 'invoke', invoke)
    events = []
    turns.run_persistent_chat_turn({'conv_id': conv['id'], 'messages': [], 'auto_generate_titles': False}, threading.Event(), 'test', events.append)
    assert {t['function']['name'] for t in observed[0]['tools']} == native.NAMES
    invoke.assert_called_once()
    assert any(e['type'] == 'tool_result' for e in events)
    assert not any(e['type'] == 'tool_approval' for e in events)


def test_runtime_serialization_and_idle_exemption(monkeypatch):
    import time
    entered = threading.Event()
    release = threading.Event()
    order = []
    def first():
        with sandbox_runtime.runtime_operation('shared'):
            order.append('first')
            entered.set()
            release.wait(2)
    def second():
        entered.wait(2)
        with sandbox_runtime.runtime_operation('shared'):
            order.append('second')
    a = threading.Thread(target=first); b = threading.Thread(target=second)
    a.start(); b.start(); entered.wait(2)
    assert sandbox_runtime.is_active('shared')
    assert order == ['first']
    release.set(); a.join(2); b.join(2)
    assert order == ['first', 'second']
    assert not sandbox_runtime.is_active('shared')


def test_forged_worker_media_is_rejected(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(container_service, 'ensure_container', lambda *a: SimpleNamespace(reset_reason=''))
    result = json.dumps({'text': 'ok', 'images': [{'mime': 'text/html', 'data': 'PHNjcmlwdD4='}]}).encode()
    monkeypatch.setattr(sandbox_runtime, 'docker_operation', lambda *a, **k: (0, result, b''))
    with pytest.raises(ValueError, match='image'):
        native.invoke('runtime', 'view', {'path': 'x.png'})


def test_shared_memory_read_is_bounded(tmp_path, monkeypatch):
    import memory_service
    path = tmp_path / 'memory.json'
    with path.open('wb') as f:
        f.write(b'x' * 32768)
        f.truncate(100000000)
    monkeypatch.setattr(memory_service, 'MEMORY_FILE', path)
    with pytest.raises(ValueError, match='too large'):
        memory_service.read()


def test_nested_runtime_operations_stay_active():
    with sandbox_runtime.runtime_operation('nested'):
        with sandbox_runtime.runtime_operation('nested'):
            assert sandbox_runtime.is_active('nested')
        assert sandbox_runtime.is_active('nested')
        cancel = threading.Event(); cancel.set()
        with pytest.raises(ValueError):
            with sandbox_runtime.runtime_operation('nested', cancel):
                pass
        assert sandbox_runtime.is_active('nested')
    assert not sandbox_runtime.is_active('nested')


def test_idle_reaper_cannot_stop_an_active_runtime(monkeypatch):
    stopped = Mock()
    monkeypatch.setattr(container_service._adv_cfg, 'load_advanced_config', lambda: {'container_idle_timeout': 1})
    monkeypatch.setattr(container_service, 'get_status', lambda cid: 'running')
    monkeypatch.setattr(container_service, 'stop_container_process', stopped)
    entered, release = threading.Event(), threading.Event()
    def active():
        with sandbox_runtime.runtime_operation('busy'):
            entered.set()
            release.wait(2)
    thread = threading.Thread(target=active)
    thread.start()
    try:
        assert entered.wait(2)
        container_service._last_used['busy'] = 0
        container_service._reap_once()
        stopped.assert_not_called()
    finally:
        release.set()
        thread.join(2)


def test_runtime_locks_are_reclaimed():
    import gc
    with sandbox_runtime.runtime_operation('temporary-lock'):
        assert 'temporary-lock' in sandbox_runtime._LOCKS
    gc.collect()
    assert 'temporary-lock' not in sandbox_runtime._LOCKS


def test_tool_media_batches_read_conversation_once(tmp_virtus, monkeypatch):
    import base64
    from unittest.mock import patch

    conversation = store.create('Images')
    images = [{'data': base64.b64encode(b'image-' + bytes([i])).decode()} for i in range(3)]
    with patch.object(store, 'load', wraps=store.load) as load:
        refs = tool_media.save(conversation['id'], images)
        assert load.call_count == 1
    messages = [{'role': 'tool', 'content': 'images', '_tool_media': refs}]
    with patch.object(store, 'load', wraps=store.load) as load:
        expanded = tool_media.expand(messages, conversation['id'])
        assert load.call_count == 1
    assert len(expanded[-1]['content']) == 4
