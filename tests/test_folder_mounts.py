"""Folder sharing validation, persistence, lifecycle and Docker permission checks."""
import csv
import subprocess
import threading
from unittest.mock import patch

import pytest

import container_service as containers
import folder_mounts
import sandbox_runtime
import store


def test_create_mounts_are_readonly_shared_and_persisted(client, tmp_lumen, tmp_path):
    source = tmp_path / 'my project'; source.mkdir()
    response = client.post('/api/folders', json={'name': 'Project', 'mounts': [{'source': str(source)}]})
    assert response.status_code == 201
    folder = response.json
    mount = folder['mounts'][0]
    assert mount == {'source': str(source.resolve()), 'name': 'my-project', 'read_only': True}
    assert store.get_folder(folder['id'])['mounts'] == [mount]
    first = store.create('First', folder['id'])
    second = store.create('Second', folder['id'])
    assert folder_mounts.runtime_mounts(store.runtime_id(first['id'])) == [mount]
    assert folder_mounts.runtime_mounts(store.runtime_id(second['id'])) == [mount]
    assert folder_mounts.runtime_mounts(store.create()['id']) == []


@pytest.mark.parametrize('mounts', [None, {}, ['bad'], [{'source': '/does-not-exist'}],
    [{'source': 'relative'}], [{'source': '\x00'}]])
def test_invalid_mounts_return_400(client, mounts):
    assert client.post('/api/folders', json={'mounts': mounts}).status_code == 400


def test_validation_prevents_target_escape_and_ambiguous_permissions(tmp_path):
    for extra in [{'name': '../escape'}, {'name': '/etc'}, {'read_only': 'false'}]:
        with pytest.raises(ValueError):
            folder_mounts.normalize_mounts([{'source': str(tmp_path), **extra}])
    with pytest.raises(ValueError, match='once'):
        folder_mounts.normalize_mounts([{'source': str(tmp_path)}] * 2)
    file = tmp_path / 'file'; file.write_text('x')
    with pytest.raises(ValueError, match='Not a directory'):
        folder_mounts.normalize_mounts([{'source': str(file)}])


def test_names_are_unique_and_symlinks_are_resolved(tmp_path):
    first = tmp_path / 'one' / '_repo'; first.mkdir(parents=True)
    second = tmp_path / 'two' / '_repo'; second.mkdir(parents=True)
    link = tmp_path / 'link'; link.symlink_to(first, target_is_directory=True)
    mounts = folder_mounts.normalize_mounts([{'source': str(link)}, {'source': str(second)}])
    assert mounts[0]['source'] == str(first.resolve())
    assert mounts[0]['name'] != mounts[1]['name']
    assert folder_mounts.normalize_mounts(mounts) == mounts


def test_edit_permissions_remove_and_preserve_mounts(client, tmp_path):
    folder = client.post('/api/folders', json={'mounts': [{'source': str(tmp_path)}]}).json
    url = f"/api/folders/{folder['id']}"
    mount = {**folder['mounts'][0], 'read_only': False}
    with patch.object(containers, 'stop_container') as stop:
        assert client.put(url, json={'mounts': [mount]}).json['mounts'] == [mount]
        stop.assert_called_once_with(f"folder_{folder['id']}", strict=True)
        stop.reset_mock()
        assert client.put(url, json={'system_prompt': 'Instructions'}).json['mounts'] == [mount]
        stop.assert_not_called()
        assert client.put(url, json={'mounts': []}).json['mounts'] == []
        stop.assert_called_once()


def test_active_chat_blocks_mount_changes(client, tmp_path):
    folder = store.create_folder()
    conv = store.create(folder_id=folder['id'])
    conv['active_stream_id'] = 'stream'; store.save(conv['id'], conv)
    with patch.object(containers, 'stop_container') as stop:
        response = client.put(f"/api/folders/{folder['id']}", json={'mounts': [{'source': str(tmp_path)}]})
        assert response.status_code == 409
        assert not store.get_folder(folder['id'])['mounts']
        stop.assert_not_called()


def test_busy_runtime_blocks_changes(client, tmp_path):
    folder = store.create_folder()
    entered, release = threading.Event(), threading.Event()
    def operation():
        with sandbox_runtime.runtime_operation(f"folder_{folder['id']}"):
            entered.set(); release.wait(5)
    thread = threading.Thread(target=operation); thread.start(); entered.wait(5)
    try:
        assert client.put(f"/api/folders/{folder['id']}", json={'mounts': [{'source': str(tmp_path)}]}).status_code == 409
    finally:
        release.set(); thread.join()


def test_deleted_host_directory_does_not_get_recreated(tmp_lumen, tmp_path):
    source = tmp_path / 'shared'; source.mkdir()
    folder = store.create_folder(mounts=folder_mounts.normalize_mounts([{'source': str(source)}]))
    source.rmdir()
    with pytest.raises(ValueError, match='unavailable'):
        containers.ensure_container(f"folder_{folder['id']}")
    assert not source.exists()


def test_folder_deletion_preserves_host_files(client, tmp_path):
    source = tmp_path / 'shared'; source.mkdir()
    file = source / 'precious.txt'; file.write_text('keep')
    folder = client.post('/api/folders', json={'mounts': [{'source': str(source), 'read_only': False}]}).json
    rid = f"folder_{folder['id']}"
    workspace = containers.conversation_workspace(rid)
    (workspace / 'internal.txt').write_text('internal')
    with patch.object(containers, 'stop_container'):
        assert client.delete(f"/api/folders/{folder['id']}").status_code == 200
    assert file.read_text() == 'keep'
    assert not workspace.exists()


def test_docker_mount_csv_and_reuse_match_includes_permissions(tmp_path, monkeypatch):
    monkeypatch.setattr(containers, '_memory_volume_spec', lambda: None)
    source = tmp_path / 'commas, and "quotes"'; source.mkdir()
    mounts = folder_mounts.normalize_mounts([{'source': str(source)}])
    args = containers._volume_args(tmp_path, mounts)
    spec = args[args.index('--mount') + 1]
    fields = next(csv.reader([spec]))
    assert f'source={source}' in fields and 'readonly' in fields
    assert 'bind-recursive=readonly' in fields
    actual = [{'Type': 'bind', 'Source': str(tmp_path), 'Destination': '/workspace', 'RW': True},
              {'Type': 'bind', 'Source': str(source), 'Destination': '/workspace/mounts/'+mounts[0]['name'], 'RW': False}]
    assert containers._mounts_match(actual, tmp_path, mounts)
    actual[1]['RW'] = True
    assert not containers._mounts_match(actual, tmp_path, mounts)
    assert not containers._mounts_match(actual, tmp_path)


def test_browse_route_and_cancel(client, monkeypatch):
    monkeypatch.setattr(folder_mounts, 'choose_directory', lambda: '/chosen/path')
    assert client.post('/api/folders/browse-directory', json={}).json == {'path': '/chosen/path'}
    monkeypatch.setattr(folder_mounts, 'choose_directory', lambda: None)
    assert client.post('/api/folders/browse-directory', json={}).json == {'path': None}


@pytest.mark.parametrize('platform', ['darwin', 'win32', 'linux'])
def test_native_picker_uses_fixed_argv(monkeypatch, platform):
    monkeypatch.setattr(folder_mounts.sys, 'platform', platform)
    monkeypatch.setattr(folder_mounts.shutil, 'which', lambda name: '/usr/bin/zenity')
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, '/chosen/directory\n', '')
    monkeypatch.setattr(folder_mounts.subprocess, 'run', run)
    assert folder_mounts.choose_directory() == '/chosen/directory'
    assert calls[0][0][0] == {'darwin': '/usr/bin/osascript', 'win32': 'powershell.exe', 'linux': 'zenity'}[platform]
    assert not calls[0][1].get('shell')


@pytest.mark.parametrize('failure', [RuntimeError('Docker refused removal'), OSError('Docker unavailable'),
                                    subprocess.TimeoutExpired('docker', 30)])
def test_failed_permission_revocation_does_not_save_new_settings(client, tmp_path, failure):
    folder = client.post('/api/folders', json={'mounts': [{'source': str(tmp_path), 'read_only': False}]}).json
    updated = [{**folder['mounts'][0], 'read_only': True}]
    with patch.object(containers, 'stop_container', side_effect=failure):
        response = client.put(f"/api/folders/{folder['id']}", json={'name': 'Unsaved', 'mounts': updated})
    assert response.status_code == 503
    assert store.get_folder(folder['id']) == folder
    assert 'not changed' in response.json['error']


@pytest.mark.parametrize('stderr, raises', [('permission denied', True), ('Cannot connect to the Docker daemon', True),
                                           ('Error response from daemon: No such container: gone', False), ('', False)])
def test_strict_removal_distinguishes_missing_container_from_daemon_errors(monkeypatch, stderr, raises):
    calls, invalidated = [], []
    def run(argv):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1 if stderr else 0, '', stderr)
    monkeypatch.setattr(containers, '_run', run)
    monkeypatch.setattr(containers, '_invalidate_mcp_pool', invalidated.append)
    if raises:
        with pytest.raises(RuntimeError):
            containers.stop_container('test', strict=True)
        assert not invalidated
    else:
        containers.stop_container('test', strict=True)
        assert invalidated == ['test']
    assert calls == [['docker', 'rm', '-f', containers.container_name('test')]]


def test_rename_with_unchanged_missing_mount_does_not_restart_runtime(client, tmp_path):
    source = tmp_path / 'disconnected'; source.mkdir()
    folder = client.post('/api/folders', json={'mounts': [{'source': str(source)}]}).json
    source.rmdir()
    with patch.object(containers, 'stop_container') as stop:
        response = client.put(f"/api/folders/{folder['id']}", json={'name': 'Renamed', 'mounts': folder['mounts']})
    assert response.status_code == 200
    assert response.json['name'] == 'Renamed'
    assert response.json['mounts'] == folder['mounts']
    stop.assert_not_called()


def test_replaced_mount_source_symlink_does_not_silently_share_a_different_directory(tmp_lumen, tmp_path):
    source = tmp_path / 'original'; source.mkdir()
    other = tmp_path / 'other'; other.mkdir()
    folder = store.create_folder(mounts=folder_mounts.normalize_mounts([{'source': str(source)}]))
    source.rmdir(); source.symlink_to(other, target_is_directory=True)
    with pytest.raises(ValueError, match='changed location'):
        folder_mounts.runtime_mounts(f"folder_{folder['id']}")


@pytest.mark.parametrize('body', [[], ['bad'], 'invalid', True, 42])
def test_non_object_folder_requests_are_rejected(client, body):
    assert client.post('/api/folders', json=body).status_code == 400


def test_readonly_container_reuse_requires_recursive_policy():
    shared = [{'name': 'repo', 'read_only': True}]
    assert not containers._shared_mount_policy_matches({}, shared)
    info = {'HostConfig': {'Mounts': [{'Target': '/workspace/mounts/repo', 'ReadOnly': True}]}}
    assert not containers._shared_mount_policy_matches(info, shared)
    info['HostConfig']['Mounts'][0]['BindOptions'] = {'ReadOnlyForceRecursive': True}
    assert containers._shared_mount_policy_matches(info, shared)
    assert containers._shared_mount_policy_matches({}, [{'name': 'repo', 'read_only': False}])


def test_mount_change_uses_summary_active_state_without_reading_chat_histories(client, tmp_path, monkeypatch):
    folder = store.create_folder()
    conv = store.create(folder_id=folder['id'])
    store.list_all()  # Build the lightweight index before checking runtime state.
    with patch.object(store, 'load', side_effect=AssertionError('History should not be read')), \
         patch.object(containers, 'stop_container'):
        assert client.put(f"/api/folders/{folder['id']}", json={'mounts': [{'source': str(tmp_path)}]}).status_code == 200
    conv['active_stream_id'] = 'active'; store.save(conv['id'], conv)
    with patch.object(store, 'load', side_effect=AssertionError('History should not be read')):
        assert client.put(f"/api/folders/{folder['id']}", json={'mounts': []}).status_code == 409
    conv.pop('active_stream_id'); store.save(conv['id'], conv)
    assert not store.list_all()[0]['active_stream_id']
    store.invalidate_index()
    assert not store.list_all()[0]['active_stream_id']


def test_default_container_removal_uses_one_docker_call(monkeypatch):
    calls = []
    monkeypatch.setattr(containers, '_run', lambda args: (calls.append(args) or subprocess.CompletedProcess(args, 0, '', '')))
    monkeypatch.setattr(containers, '_invalidate_mcp_pool', lambda rid: None)
    containers.stop_container('test')
    assert calls == [['docker', 'rm', '-f', containers.container_name('test')]]
