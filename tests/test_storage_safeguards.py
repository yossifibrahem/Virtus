"""Volume mount policy and direct, bounded storage probes."""
import subprocess
from unittest.mock import Mock

import pytest
import container_service as containers
import storage_safeguards as storage
import sandbox_files


@pytest.mark.parametrize('change', ['bind', 'name', 'readonly', 'extra', None])
def test_workspace_mount_policy(change):
    mount = {'Type': 'volume', 'Name': 'owned', 'Destination': '/workspace', 'RW': True}
    mounts = [mount]
    if change == 'bind': mount['Type'] = 'bind'
    if change == 'name': mount['Name'] = 'other'
    if change == 'readonly': mount['RW'] = False
    if change == 'extra': mounts.append(dict(mount))
    assert containers._mounts_match(mounts, 'owned') is (change is None)


@pytest.mark.parametrize('response', [None, 'invalid', '{}', '[]', '{"workspace_bytes":true}'])
def test_probe_failures_are_explicit(monkeypatch, response):
    monkeypatch.setattr(containers, 'ensure_container', lambda rid: None)
    def run(args, **kwargs):
        assert kwargs == {'timeout': 10}
        if response is None: raise subprocess.TimeoutExpired(args, 10)
        return subprocess.CompletedProcess(args, 0, response, '')
    monkeypatch.setattr(containers, '_run', run)
    with pytest.raises(storage.StorageLimit, match='Could not check sandbox storage'):
        storage.check('test')


@pytest.mark.parametrize('error', ['', 'No such volume: gone', 'volume is in use', 'daemon unavailable'])
def test_volume_removal_errors(monkeypatch, error):
    run = Mock(return_value=subprocess.CompletedProcess([], bool(error), '', error))
    monkeypatch.setattr(containers, '_run', run)
    if error and not error.startswith('No such volume'):
        with pytest.raises(RuntimeError): containers.delete_workspace('test')
    else:
        containers.delete_workspace('test')
    run.assert_called_once_with(['docker', 'volume', 'rm', containers.workspace_volume('test')])


def test_usage_stops_at_limit_without_following_symlinks(tmp_path, monkeypatch):
    root = tmp_path / 'workspace'; root.mkdir()
    (root / 'large').write_bytes(b'x'*20)
    outside = tmp_path / 'outside'; outside.mkdir(); (outside / 'file').write_bytes(b'x'*1000)
    (root / 'link').symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(sandbox_files, 'ROOT', root)
    assert sandbox_files.storage_usage(10)['workspace_bytes'] == 20
    assert sandbox_files.storage_usage(10000)['workspace_bytes'] == 20


def test_orphan_cleanup_preserves_known_and_discovery_volumes(monkeypatch):
    known = ['folder_empty']
    names = [containers.workspace_volume(rid) for rid in [*known, containers.DISCOVERY_CONTAINER_ID, 'orphan']]
    calls = []
    def run(args):
        calls.append(args)
        output = '\n'.join(names) if args[1:3] == ['volume', 'ls'] else ''
        return subprocess.CompletedProcess(args, 0, output, '')
    monkeypatch.setattr(containers, '_run', run)
    containers.cleanup_stale(known)
    assert [a for a in calls if a[1:3] == ['volume', 'rm']] == [['docker', 'volume', 'rm', names[-1]]]
