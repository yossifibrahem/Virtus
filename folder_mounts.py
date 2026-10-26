"""Explicit Folder-owned host sharing and native directory selection."""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

_PICKER_LOCK = threading.Lock()
_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$")


def _host_directory(source):
    if not source or '\x00' in source:
        raise ValueError("Choose an absolute host directory path")
    try:
        path = Path(source).expanduser()
        if not path.is_absolute():
            raise ValueError("Choose an absolute host directory path")
        path = path.resolve(strict=True)
        if not path.is_dir():
            raise ValueError(f"Not a directory: {source}")
        return path
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"Directory is unavailable: {source}") from exc


def normalize_mounts(value):
    if not isinstance(value, list) or len(value) > 32:
        raise ValueError("Mounts must be a list of at most 32 directories")
    mounts, names, sources = [], set(), set()
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get('source'), str):
            raise ValueError("Each mount needs a host directory path")
        source = item['source']
        path = _host_directory(source)
        source = str(path)
        if source in sources:
            raise ValueError("A directory can only be mounted once in a Folder")
        read_only = item.get('read_only', True)
        if not isinstance(read_only, bool):
            raise ValueError("Read only must be true or false")
        name = item.get('name')
        if name is None:
            base = re.sub(r'[^a-zA-Z0-9._-]', '-', path.name).strip('.-_')[:70] or 'directory'
            name, suffix = base, 2
            while name in names:
                name, suffix = f'{base}-{suffix}', suffix + 1
        if not isinstance(name, str) or not _NAME.fullmatch(name) or name in names:
            raise ValueError("Mount names must be unique simple directory names")
        names.add(name)
        sources.add(source)
        mounts.append({'source': source, 'name': name, 'read_only': read_only})
    return mounts


def runtime_mounts(runtime_id):
    """Never infer host sharing from a chat or MCP configuration."""
    if not runtime_id.startswith('folder_'):
        return []
    import store
    folder = store.get_folder(runtime_id[len('folder_'):])
    mounts = (folder or {}).get('mounts', [])
    # Inputs were validated when saved; only host path availability can change.
    for mount in mounts:
        if str(_host_directory(mount['source'])) != mount['source']:
            raise ValueError('A mounted directory changed location; select it again in Folder settings')
    return mounts


def choose_directory():
    """Open a host OS dialog without running a shell or accepting executable input."""
    if not _PICKER_LOCK.acquire(blocking=False):
        raise ValueError('A directory chooser is already open')
    try:
        if sys.platform == 'darwin':
            command = ['/usr/bin/osascript', '-e',
                       'try\nPOSIX path of (choose folder with prompt "Share a directory with this Lumen Folder")\non error number -128\nreturn ""\nend try']
        elif sys.platform == 'win32':
            command = ['powershell.exe', '-NoProfile', '-STA', '-Command',
                       'Add-Type -AssemblyName System.Windows.Forms; '
                       '$dialog = New-Object System.Windows.Forms.FolderBrowserDialog; '
                       '$dialog.Description = "Share a directory with this Lumen Folder"; '
                       '$dialog.ShowNewFolderButton = $false; '
                       'try { if ($dialog.ShowDialog() -eq "OK") { '
                       '[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; '
                       '[Console]::Write($dialog.SelectedPath) } } finally { $dialog.Dispose() }']
        elif shutil.which('zenity'):
            command = ['zenity', '--file-selection', '--directory', '--title=Share a directory with Lumen']
        elif shutil.which('kdialog'):
            command = ['kdialog', '--getexistingdirectory', str(Path.home())]
        else:
            raise ValueError('No native directory chooser is available. Enter the host directory path instead.')
        options = {'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0)} if sys.platform == 'win32' else {}
        result = subprocess.run(command, capture_output=True, encoding='utf-8', errors='replace', timeout=300, **options)
        if result.returncode == 1 and sys.platform not in ('darwin', 'win32'):
            return None  # Linux dialog cancellation
        if result.returncode:
            raise ValueError('Could not open the directory chooser. Enter the host directory path instead.')
        return result.stdout.rstrip('\r\n') or None
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError('Directory chooser unavailable or timed out. Enter the host directory path instead.') from exc
    finally:
        _PICKER_LOCK.release()
