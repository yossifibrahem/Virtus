from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

import docker_cli


def test_explicit_docker_path_has_priority(tmp_path, monkeypatch):
    explicit = tmp_path / "docker"
    explicit.write_text("#!/bin/sh\n")
    explicit.chmod(0o755)
    monkeypatch.setenv("VIRTUS_DOCKER_PATH", str(explicit))
    monkeypatch.setattr(docker_cli.shutil, "which", lambda _name: "/another/docker")
    assert docker_cli.resolve() == str(explicit)


def test_macos_candidates_include_docker_desktop_bundle(monkeypatch):
    monkeypatch.setattr(docker_cli.sys, "platform", "darwin")
    monkeypatch.setattr(docker_cli.shutil, "which", lambda _name: None)
    assert "/Applications/Docker.app/Contents/Resources/bin/docker" in docker_cli.candidate_paths()


def test_windows_candidates_cover_official_install_modes(monkeypatch):
    monkeypatch.setattr(docker_cli.sys, "platform", "win32")
    monkeypatch.setattr(docker_cli.shutil, "which", lambda _name: None)
    monkeypatch.setenv("ProgramFiles", r"C:\Program Files")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\User\AppData\Local")

    candidates = docker_cli.candidate_paths()

    assert str(Path(r"C:\Program Files") / "Docker" / "Docker" / "resources" / "bin" / "docker.exe") in candidates
    assert str(Path(r"C:\Users\User\AppData\Local") / "Docker" / "resources" / "bin" / "docker.exe") in candidates


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process flags only")
def test_windows_docker_commands_are_windowless(monkeypatch):
    calls = []
    monkeypatch.setattr(docker_cli.sys, "platform", "win32")
    monkeypatch.setattr(docker_cli, "executable", lambda: r"C:\docker.exe")
    monkeypatch.setattr(docker_cli.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))

    docker_cli.run(["version"], timeout=2)

    kwargs = calls[0][1]
    assert kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW
    assert kwargs["startupinfo"].dwFlags & subprocess.STARTF_USESHOWWINDOW


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Docker Desktop only")
def test_windows_starts_docker_through_desktop_cli(monkeypatch):
    monkeypatch.setattr(docker_cli.sys, "platform", "win32")
    monkeypatch.setattr(docker_cli, "docker_desktop_installed", lambda: True)
    calls = []
    monkeypatch.setattr(
        docker_cli,
        "run",
        lambda args, timeout=None: calls.append((args, timeout)) or subprocess.CompletedProcess(args, 0, "", ""),
    )

    result = docker_cli.start_docker_desktop()

    assert result.returncode == 0
    assert calls == [(["desktop", "start", "--detach", "--timeout", "10"], 12)]


def test_resolve_returns_none_when_candidates_are_missing(monkeypatch):
    monkeypatch.setattr(docker_cli, "candidate_paths", lambda: ["/definitely/missing/docker"])
    assert docker_cli.resolve() is None


def test_cli_output_handles_unicode_paths_and_non_utf8_diagnostics(monkeypatch):
    monkeypatch.setattr(docker_cli, 'executable', lambda: sys.executable)
    script = "import sys; sys.stdout.buffer.write('مشروع'.encode()); sys.stderr.buffer.write(b'bad\\xff')"
    result = docker_cli.run(['-c', script])
    assert result.returncode == 0 and result.stdout == 'مشروع' and result.stderr == 'bad\ufffd'
