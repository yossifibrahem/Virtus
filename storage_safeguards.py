"""Best-effort storage accounting; safeguards, not hard filesystem quotas."""
from __future__ import annotations
import json
import subprocess
from contextlib import contextmanager
from contextvars import ContextVar

import advanced_config
import container_service


class StorageLimit(ValueError):
    pass


_RECOVERY = ContextVar('virtus_storage_recovery', default=False)


@contextmanager
def recovery_operation():
    """Permit app-owned read workers to recover an over-limit workspace.

    Callers must restrict this to known non-growing operations, never arbitrary
    Bash or MCP execution. The context is local to the request and always reset.
    """
    token = _RECOVERY.set(True)
    try:
        yield
    finally:
        _RECOVERY.reset(token)


def check(runtime_id):
    if _RECOVERY.get():
        return {'storage_recovery': True}
    cfg = advanced_config.load_advanced_config()
    minimum = cfg.get('min_free_disk_bytes', 512 * 1024**2)
    maximum = cfg.get('max_workspace_bytes', 10 * 1024**3)
    container_service.ensure_container(runtime_id)
    try:
        probe = container_service._run(['docker', 'exec', container_service.container_name(runtime_id),
            '/opt/venv/bin/python', '-I', '/opt/virtus/sandbox_files.py', '--usage', str(maximum)], timeout=10)
        if probe.returncode:
            raise ValueError(probe.stderr.strip() or 'Storage probe failed')
        result = json.loads(probe.stdout)
        for key in ('workspace_bytes', 'workspace_free_bytes', 'docker_free_bytes'):
            if type(result[key]) is not int or result[key] < 0:
                raise ValueError('Invalid storage usage')
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError) as exc:
        raise StorageLimit(f'Could not check sandbox storage: {exc}') from exc
    if result['workspace_bytes'] > maximum:
        raise StorageLimit('Managed workspace storage limit reached. Free managed workspace storage before continuing; existing files were retained.')
    if min(result['workspace_free_bytes'], result['docker_free_bytes']) < minimum:
        raise StorageLimit('Sandbox filesystem has critically low free space. Free workspace or Docker storage before continuing; existing files were retained.')
    return result
