"""Regression coverage for the approved architecture behavior, without real Docker."""
import asyncio
import copy
import json
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest
import advanced_config
import builtin_tools
import chat_turn_service as turns
import container_service as containers
import memory_service as memory
import routes_chat
import storage_safeguards as storage
import store
from mcp_session_pool import McpSessionPool


def test_memory_concurrent_writes_have_one_winner():
    revision = memory.snapshot()['revision']
    def write(value):
        try:
            return memory.update(value, revision)['content']
        except memory.Conflict:
            return 'conflict'
    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(write, ['Alice', 'Bob']))
    assert results.count('conflict') == 1
    assert memory.snapshot()['content'] in ('Alice', 'Bob')
    assert json.loads(memory.MEMORY_FILE.with_suffix('.history.json').read_text())[0]['content'] == ''


def test_memory_disable_preserves_data_rejects_tool_and_never_reads(monkeypatch):
    state = memory.update('Alice', memory.snapshot()['revision'])
    memory.set_enabled(False)
    assert 'update_memory' not in [s['function']['name'] for s in builtin_tools.available_schemas()]
    with pytest.raises(ValueError, match='disabled'):
        builtin_tools.invoke('unused', 'update_memory', {'content': 'Bob', 'revision': state['revision']})
    monkeypatch.setattr(memory, '_raw', lambda: pytest.fail('Disabled memory must not be read for injection'))
    original = [{'role': 'user', 'content': 'Hello'}]
    assert turns._inject_memory(original) is original


def test_memory_api_conflicts_clear_toggle_and_history(client):
    state = client.get('/api/memory').json
    updated = client.put('/api/memory', json={'content': 'Alice', 'revision': state['revision']}).json
    assert client.put('/api/memory', json={'content': 'Bob', 'revision': state['revision']}).status_code == 409
    assert client.put('/api/memory', json={'enabled': False}).json['content'] == 'Alice'
    assert client.put('/api/memory', json={'enabled': 'yes'}).status_code == 400
    assert client.put('/api/memory', json={'content': '', 'revision': updated['revision']}).json['content'] == ''
    assert memory.enabled() is False
    assert memory.MEMORY_FILE.with_suffix('.settings.json').exists()
    for n in range(12):
        memory.update(str(n), memory.snapshot()['revision'])
    assert len(json.loads(memory.MEMORY_FILE.with_suffix('.history.json').read_text())) == 8


def test_memory_conflict_returns_current_data_and_rejects_host_paths():
    stale = memory.snapshot()['revision']
    memory.update('Current', stale)
    result = builtin_tools.invoke('unused', 'update_memory', {'content': 'Stale', 'revision': stale})
    assert json.loads(result['text'])['content'] == 'Current'
    for args in ({'path': '/tmp/arbitrary', 'content': 'x', 'revision': stale},
                 {'content': 'x'*32769, 'revision': stale}):
        with pytest.raises(ValueError):
            builtin_tools.invoke('unused', 'update_memory', args)


def test_legacy_bind_inode_is_detached_and_bytes_preserved():
    memory.MEMORY_FILE.write_text('Legacy name')
    with memory.MEMORY_FILE.open('r+b') as old_bind:
        memory.isolate_legacy_file()
        old_bind.seek(0)
        old_bind.write(b'Changed old')
        old_bind.flush()
        assert memory.MEMORY_FILE.read_text() == 'Legacy name'
    assert '/memory.md' not in str(containers._volume_args(memory.MEMORY_FILE.parent))


def valid_runtime(workspace):
    return {'State': {'Status': 'running'}, 'Mounts': [{'Type': 'bind', 'Source': str(workspace),
        'Destination': '/workspace', 'RW': True}],
        'Config': {'WorkingDir': '/workspace'},
        'HostConfig': {'Memory': 512*1024**2, 'NanoCpus': 10**9, 'PidsLimit': 256,
            'Privileged': False, 'CapDrop': ['ALL'], 'CapAdd': list(containers._CAPABILITIES),
            'SecurityOpt': ['no-new-privileges:true']}}


@pytest.mark.parametrize('key,value', [
    ('Memory', 0), ('NanoCpus', 0), ('PidsLimit', -1), ('Privileged', True),
    ('CapAdd', ['SYS_ADMIN']), ('CapDrop', []), ('SecurityOpt', []),
    ('Devices', [{'PathOnHost': '/dev/sda'}]), ('PidMode', 'host'),
    ('IpcMode', 'host'), ('UTSMode', 'host'), ('UsernsMode', 'host'), ('CgroupnsMode', 'host'),
    ('DeviceRequests', [{'Count': -1}]), ('SecurityOpt', ['no-new-privileges', 'seccomp=unconfined'])])
def test_effective_configuration_rejected_in_both_reuse_paths(tmp_lumen, monkeypatch, key, value):
    rid = 'test'
    workspace = containers.conversation_workspace(rid)
    info = valid_runtime(workspace)
    monkeypatch.setattr(containers, '_container_image_changed', lambda *args: False)
    assert containers._runtime_matches(info, workspace, [])
    info['HostConfig'][key] = value
    assert not containers._runtime_matches(info, workspace, [])
    monkeypatch.setattr(containers, '_inspect_container', lambda *args: info)
    with pytest.raises(RuntimeError, match='incompatible'):
        containers._reuse_conflicting_container(rid)
    removed = []
    monkeypatch.setattr(containers, 'stop_container', lambda cid, strict=False: removed.append((cid, strict)))
    monkeypatch.setattr(containers, '_run', lambda args: subprocess.CompletedProcess(args, 0, 'new', ''))
    assert containers.ensure_container(rid).status == 'running'
    assert removed == [(rid, True)]


def test_network_is_ignored_and_memory_mount_is_incompatible(tmp_lumen, monkeypatch):
    workspace = containers.conversation_workspace('test')
    info = valid_runtime(workspace)
    monkeypatch.setattr(containers, '_container_image_changed', lambda *args: False)
    info['HostConfig']['NetworkMode'] = 'custom-docker-network'
    assert containers._runtime_matches(info, workspace, [])
    assert '--network' not in containers._docker_run_command('test', workspace)
    info['Mounts'].append({'Type': 'bind', 'Source': str(memory.MEMORY_FILE), 'Destination': '/memory.md', 'RW': True})
    assert not containers._runtime_matches(info, workspace, [])


@pytest.mark.parametrize('user', ['', 'root', '0', '0:0', 'root:root', 'root:0', '0:root'])
def test_equivalent_root_users_do_not_reset_the_environment(tmp_lumen, monkeypatch, user):
    workspace = containers.conversation_workspace('root-user')
    info = valid_runtime(workspace)
    info['Config']['User'] = user
    monkeypatch.setattr(containers, '_container_image_changed', lambda *args: False)
    assert containers._runtime_matches(info, workspace, [])
    info['Config']['User'] = '1000:1000'
    assert not containers._runtime_matches(info, workspace, [])


def test_created_runtime_satisfies_reuse_policy_with_nondefault_daemon_settings(tmp_lumen, monkeypatch):
    rid = 'creation-reuse'
    workspace = containers.conversation_workspace(rid)
    inspected = None
    created = []
    monkeypatch.setattr(containers, '_inspect_container', lambda *args: copy.deepcopy(inspected))
    monkeypatch.setattr(containers, '_container_image_changed', lambda *args: False)
    removed = MagicMock()
    monkeypatch.setattr(containers, 'stop_container', removed)

    def run(command):
        nonlocal inspected
        assert command[:2] == ['docker', 'run']
        created.append(command)
        inspected = valid_runtime(workspace)
        # A custom image may declare USER root; the daemon may default to host
        # IPC/cgroup namespaces. Creation must explicitly override those defaults.
        inspected['Config']['User'] = command[command.index('--user') + 1] if '--user' in command else 'root'
        for flag, key in [('--ipc', 'IpcMode'), ('--cgroupns', 'CgroupnsMode')]:
            inspected['HostConfig'][key] = command[command.index(flag) + 1] if flag in command else 'host'
        return subprocess.CompletedProcess(command, 0, 'created', '')

    monkeypatch.setattr(containers, '_run', run)
    assert containers.ensure_container(rid).status == 'running'
    assert containers.ensure_container(rid).status == 'running'
    assert len(created) == 1
    removed.assert_not_called()


def test_reconciliation_never_interrupts_other_active_operations(tmp_lumen, monkeypatch):
    import sandbox_runtime
    workspace = containers.conversation_workspace('test')
    info = valid_runtime(workspace); info['HostConfig']['Memory'] = 0
    monkeypatch.setattr(containers, '_container_image_changed', lambda *args: False)
    monkeypatch.setattr(containers, '_inspect_container', lambda *args: info)
    stop = MagicMock(); monkeypatch.setattr(containers, 'stop_container', stop)
    ready, finish = threading.Event(), threading.Event()
    def hold():
        with sandbox_runtime.runtime_operation('test'):
            ready.set(); finish.wait(3)
    worker = threading.Thread(target=hold); worker.start(); assert ready.wait(1)
    try:
        with pytest.raises(RuntimeError, match='idle'):
            containers.ensure_container('test')
        stop.assert_not_called()
    finally:
        finish.set(); worker.join(2)


def test_disk_accounting_skips_symlinks_and_checks_docker_fs(tmp_lumen, monkeypatch, tmp_path):
    workspace = containers.conversation_workspace('test')
    (workspace / 'owned').write_bytes(b'abc')
    external = tmp_path / 'external'; external.mkdir(); (external / 'other').write_bytes(b'x'*2000)
    (workspace / 'link').symlink_to(external, target_is_directory=True)
    assert storage.check('test')['workspace_bytes'] < 2000
    monkeypatch.setattr(containers, '_run', lambda *args, **kwargs: subprocess.CompletedProcess([], 0, '{"free":1,"used":20}', ''))
    with pytest.raises(storage.StorageLimit, match='Docker filesystem'):
        storage.check('test', docker=True)
    advanced_config.save_advanced_config({'max_workspace_bytes': 1})
    with pytest.raises(storage.StorageLimit, match='workspace storage'):
        storage.check('test')


def test_mcp_cancellation_stops_actual_coroutine(monkeypatch):
    pool = McpSessionPool('', tool_timeout=10)
    started, cleaned, cancel = threading.Event(), threading.Event(), threading.Event()
    async def invoke(*args):
        started.set()
        try:
            await asyncio.sleep(30)
        finally:
            cleaned.set()
    monkeypatch.setattr(pool, '_invoke', invoke)
    def stop():
        assert started.wait(2); cancel.set()
    stopper = threading.Thread(target=stop); stopper.start()
    try:
        with pytest.raises(RuntimeError, match='cancelled'):
            pool.invoke_tool('server', {}, 'tool', {}, cancel_event=cancel)
        assert cleaned.wait(1)
    finally:
        pool.close(timeout=2); stopper.join(2)
    assert not pool._thread.is_alive()


def test_turn_tool_budget_keeps_coherent_history(tmp_lumen, monkeypatch):
    conv = store.create()
    advanced_config.save_advanced_config({'turn_tool_limit': 1})
    monkeypatch.setattr(turns, 'openai_client', lambda body: MagicMock())
    invoke = MagicMock(return_value={'text': 'ok'}); monkeypatch.setattr(builtin_tools, 'invoke', invoke)
    counter = iter(range(5))
    def stream(*args, **kwargs):
        index = next(counter)
        yield {'type': 'tool_calls', 'calls': [{'id': str(index), 'function': {'name': 'update_memory', 'arguments': '{}'}}]}
        yield {'type': 'done'}
    monkeypatch.setattr(turns.stream_module, 'stream_chat_completion', stream)
    events = []
    turns.run_persistent_chat_turn({'conv_id': conv['id'], 'auto_generate_titles': False}, threading.Event(), 'budget', events.append)
    assert invoke.call_count == 1
    saved = store.load(conv['id'])
    calls = [m for m in saved['messages'] if m['role'] == 'assistant']
    results = [m for m in saved['messages'] if m['role'] == 'tool']
    assert len(calls) == len(results) == 2
    assert 'cancelled' in results[-1]['content']
    assert any('tool-call budget' in e.get('message', '') for e in events)
    assert events[-1]['type'] == 'assistant_done'


def test_replay_is_bounded_and_requests_authoritative_history():
    state = routes_chat._stream_state('test-bounded', 'conversation')
    cancel = threading.Event(); routes_chat._cancel_events['test-bounded'] = cancel
    try:
        for _ in range(12):
            routes_chat._publish_stream_event(state, {'type': 'text', 'content': 'x'*1024*1024})
        routes_chat._publish_stream_event(state, {'type': 'assistant_done', 'messages': ['x'*9*1024*1024]})
        assert cancel.is_set()
        assert sum(len(e.encode()) for e in state['events']) < 8*1024*1024 + 2048
        assert 'history_reload' in state['events'][-1]
    finally:
        routes_chat._active_streams.pop('test-bounded', None)
        routes_chat._cancel_events.pop('test-bounded', None)


def test_memory_disable_takes_effect_in_an_ongoing_loop(tmp_lumen, monkeypatch):
    conv = store.create()
    memory.update('Remembered', memory.snapshot()['revision'])
    observed = []
    def stream(*args, **kwargs):
        observed.append(kwargs)
        if len(observed) == 1:
            yield {'type': 'tool_calls', 'calls': [{'id': 'm', 'function': {'name': 'update_memory', 'arguments': '{}'}}]}
        else:
            yield {'type': 'text', 'content': 'Done'}
        yield {'type': 'done'}
    def disable(*args):
        memory.set_enabled(False)
        return {'text': 'Disabled'}
    monkeypatch.setattr(turns, 'openai_client', lambda body: None)
    monkeypatch.setattr(turns.stream_module, 'stream_chat_completion', stream)
    monkeypatch.setattr(builtin_tools, 'invoke', disable)
    turns.run_persistent_chat_turn({'conv_id': conv['id']}, threading.Event(), 'toggle', lambda event: None)
    assert 'Remembered' in str(observed[0]['messages'])
    assert 'Remembered' not in str(observed[1]['messages'])
    assert 'update_memory' not in str(observed[1]['tools'])


def test_turn_wall_budget_actually_cancels_wait(tmp_lumen, monkeypatch):
    conv = store.create()
    advanced_config.save_advanced_config({'turn_timeout_seconds': 1})
    closed = threading.Event()
    client = MagicMock(); client.close.side_effect = closed.set
    monkeypatch.setattr(turns, 'openai_client', lambda body: client)
    def stream(*args, **kwargs):
        assert closed.wait(3), 'Watchdog must interrupt the client'
        yield {'type': 'done'}
    monkeypatch.setattr(turns.stream_module, 'stream_chat_completion', stream)
    events = []; start = time.monotonic()
    turns.run_persistent_chat_turn({'conv_id': conv['id']}, threading.Event(), 'wall', events.append)
    assert time.monotonic() - start < 2.5
    assert any('wall-clock budget' in e.get('message', '') for e in events)
    assert not store.load(conv['id']).get('active_stream_id')


def test_persistent_anyio_session_scopes_survive_calls_and_cancel(monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    import anyio
    entered, cleaned, cancel = threading.Event(), threading.Event(), threading.Event()
    pool = McpSessionPool('', tool_timeout=10)
    calls = []
    @asynccontextmanager
    async def transport():
        async with anyio.create_task_group():
            try:
                yield
            finally:
                cleaned.set()
    cm = transport()
    async def get_session(*args):
        if not calls:
            await cm.__aenter__()
        async def call_tool(*args):
            calls.append(1)
            if len(calls) == 2:
                entered.set()
                await asyncio.sleep(30)
            return SimpleNamespace(content=[SimpleNamespace(text='ok')])
        return SimpleNamespace(call_tool=call_tool)
    async def close():
        if not cleaned.is_set():
            await cm.__aexit__(None, None, None)
    monkeypatch.setattr(pool, '_get_session', get_session)
    monkeypatch.setattr(pool, '_close_async', close)
    try:
        assert pool.invoke_tool('server', {}, 'tool', {}) == 'ok'
        def stop():
            assert entered.wait(2); cancel.set()
        stopper = threading.Thread(target=stop); stopper.start()
        with pytest.raises(RuntimeError, match='cancelled'):
            pool.invoke_tool('server', {}, 'tool', {}, cancel_event=cancel)
        stopper.join(2)
        assert cleaned.is_set()
    finally:
        pool.close(timeout=2)
    assert not pool._thread.is_alive()


def test_low_disk_interrupts_running_command_without_waiting_for_exit(tmp_lumen, monkeypatch):
    import sys
    import sandbox_runtime
    import docker_cli
    from types import SimpleNamespace
    check_calls, kills, processes = [], [], []
    def check(*args, **kwargs):
        check_calls.append(1)
        if len(check_calls) > 1:
            raise storage.StorageLimit('critically low space')
    monkeypatch.setattr(storage, 'check', check)
    monkeypatch.setattr(containers, 'ensure_container', lambda *args: SimpleNamespace(reset_reason=''))
    monkeypatch.setattr(containers, '_invalidate_mcp_pool', lambda *args: None)
    monkeypatch.setattr(docker_cli, 'argv', lambda *args: [sys.executable, '-c', 'import time; time.sleep(30)'])
    monkeypatch.setattr(docker_cli, 'run', lambda args, **kwargs: (kills.append(args) or subprocess.CompletedProcess(args, 0, '', '')))
    original_popen = subprocess.Popen
    def spawn(*args, **kwargs):
        proc = original_popen(*args, **kwargs); processes.append(proc); return proc
    monkeypatch.setattr(sandbox_runtime.subprocess, 'Popen', spawn)
    start = time.monotonic()
    with pytest.raises(ValueError, match='critically low space.*shared runtime interrupted'):
        sandbox_runtime.docker_operation('disposable-test', [], timeout=20)
    assert time.monotonic() - start < 6
    assert kills[0][0] == 'kill'
    assert processes[0].poll() is not None
