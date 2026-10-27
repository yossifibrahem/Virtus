"""Best-effort storage accounting; safeguards, not hard filesystem quotas."""
from __future__ import annotations
import json
import os
import shutil
import subprocess
from contextlib import contextmanager
from contextvars import ContextVar

import advanced_config
import container_service


class StorageLimit(ValueError):
    pass


_RECOVERY = ContextVar('lumen_storage_recovery', default=False)


@contextmanager
def recovery_operation():
    """Permit app-owned read/delete workers to recover an over-limit workspace.

    Callers must restrict this to known non-growing operations, never arbitrary
    Bash or MCP execution. The context is local to the request and always reset.
    """
    token = _RECOVERY.set(True)
    try:
        yield
    finally:
        _RECOVERY.reset(token)


def check(runtime_id, *, docker=False):
    if _RECOVERY.get():
        return {'storage_recovery': True}
    cfg = advanced_config.load_advanced_config()
    root = container_service.CONTAINERS_ROOT
    minimum = cfg.get('min_free_disk_bytes', 512 * 1024**2)
    maximum = cfg.get('max_workspace_bytes', 10 * 1024**3)
    free = shutil.disk_usage(root).free
    if free < minimum:
        raise StorageLimit('Managed workspace filesystem has critically low free space. Use the file panel to download or delete managed workspace files before continuing; existing files were retained.')
    # Do not follow symlinks or traverse external host shares.
    total = 0
    workspace = root / container_service._safe_id(runtime_id)
    for directory, dirs, files in os.walk(workspace, followlinks=False):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(directory, d))]
        for name in files:
            try:
                total += os.lstat(os.path.join(directory, name)).st_size
            except FileNotFoundError:
                continue
            if total > maximum:
                raise StorageLimit('Managed workspace storage limit reached. Use the file panel to download or delete managed workspace files before continuing; existing files were retained.')
    result = {'workspace_bytes': total, 'workspace_free_bytes': free}
    if docker:
        # Docker Desktop may use a separate VM disk. Query that filesystem too.
        try:
            probe = container_service._run(['docker', 'exec', container_service.container_name(runtime_id),
                '/opt/venv/bin/python', '-I', '-c',
                'import json,shutil;print(json.dumps(shutil.disk_usage("/")._asdict()))'], timeout=3)
            if probe.returncode == 0:
                usage = json.loads(probe.stdout)
                result['docker_free_bytes'] = int(usage['free'])
                result['docker_used_bytes'] = int(usage['used'])
                if usage['free'] < minimum:
                    raise StorageLimit('Docker filesystem has critically low free space. Shared runtime stopped; existing files were retained.')
        except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError) as exc:
            if isinstance(exc, StorageLimit):
                raise
            result['docker_storage_unavailable'] = True
    return result
