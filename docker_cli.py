"""Resolve and invoke Docker reliably from shells and desktop applications."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable


MAC_DOCKER_APP = Path("/Applications/Docker.app")


def _windowless_process_kwargs() -> dict:
    """Prevent console executables from flashing a window on Windows."""
    if sys.platform != "win32":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "startupinfo": startupinfo,
    }


def _windows_docker_roots() -> list[Path]:
    roots: list[Path] = []
    program_files = os.getenv("ProgramFiles", "").strip()
    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    if program_files:
        roots.append(Path(program_files) / "Docker" / "Docker")
    if local_app_data:
        roots.append(Path(local_app_data) / "Docker")
    return roots


def candidate_paths() -> list[str]:
    candidates: list[str] = []
    explicit = os.getenv("VIRTUS_DOCKER_PATH", "").strip()
    if explicit:
        candidates.append(explicit)

    discovered = shutil.which("docker")
    if discovered:
        candidates.append(discovered)

    if sys.platform == "darwin":
        candidates.extend([
            "/usr/local/bin/docker",
            "/opt/homebrew/bin/docker",
            "/Applications/Docker.app/Contents/Resources/bin/docker",
            str(Path.home() / ".docker" / "bin" / "docker"),
        ])
    elif sys.platform == "win32":
        candidates.extend(
            str(root / "resources" / "bin" / "docker.exe")
            for root in _windows_docker_roots()
        )
        candidates.append(str(Path.home() / ".docker" / "bin" / "docker.exe"))

    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        expanded = os.path.expanduser(candidate)
        if expanded not in seen:
            seen.add(expanded)
            unique.append(expanded)
    return unique


def resolve() -> str | None:
    for candidate in candidate_paths():
        path = Path(candidate)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def executable() -> str:
    resolved = resolve()
    if not resolved:
        raise FileNotFoundError("The Docker command could not be found.")
    return resolved


def argv(args: Iterable[str]) -> list[str]:
    return [executable(), *[str(arg) for arg in args]]


def run(
    args: Iterable[str],
    *,
    timeout: float | None = None,
    cwd: Path | str | None = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv(args),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        cwd=cwd,
        **_windowless_process_kwargs(),
    )


def popen(
    args: Iterable[str],
    *,
    cwd: Path | str | None = None,
) -> subprocess.Popen:
    return subprocess.Popen(
        argv(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
        **_windowless_process_kwargs(),
    )


def docker_desktop_installed() -> bool:
    if sys.platform == "darwin":
        return MAC_DOCKER_APP.is_dir()
    if sys.platform == "win32":
        return any((root / "Docker Desktop.exe").is_file() for root in _windows_docker_roots())
    return False


def start_docker_desktop() -> subprocess.CompletedProcess:
    if sys.platform == "darwin":
        if not docker_desktop_installed():
            raise FileNotFoundError("Docker Desktop is not installed in /Applications.")
        return subprocess.run(
            ["/usr/bin/open", "-a", "Docker"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    if sys.platform == "win32":
        if not docker_desktop_installed():
            raise FileNotFoundError("Docker Desktop is not installed in a standard location.")
        return run(["desktop", "start", "--detach", "--timeout", "10"], timeout=12)
    raise RuntimeError("Starting Docker Desktop is only supported on macOS and Windows.")
