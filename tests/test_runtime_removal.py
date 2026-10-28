"""Container removal and folder request validation."""
import subprocess

import pytest

import container_service as containers


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


@pytest.mark.parametrize('body', [[], ['bad'], 'invalid', True, 42])
def test_non_object_folder_requests_are_rejected(client, body):
    assert client.post('/api/folders', json=body).status_code == 400


def test_default_container_removal_uses_one_docker_call(monkeypatch):
    calls = []
    monkeypatch.setattr(containers, '_run', lambda args: (calls.append(args) or subprocess.CompletedProcess(args, 0, '', '')))
    monkeypatch.setattr(containers, '_invalidate_mcp_pool', lambda rid: None)
    containers.stop_container('test')
    assert calls == [['docker', 'rm', '-f', containers.container_name('test')]]
