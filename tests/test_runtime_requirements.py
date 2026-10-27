"""Tests for Docker runtime detection without requiring a Docker daemon."""
from __future__ import annotations

import io
import subprocess
from types import SimpleNamespace

import pytest

import runtime_requirements as requirements


def _configure(monkeypatch):
    monkeypatch.setattr(requirements, "_image_name", lambda: "test-sandbox:latest")
    monkeypatch.setattr(requirements, "_expected_sandbox_identity", lambda: "expected-identity", raising=False)


def test_reports_missing_docker_cli_before_running_a_command(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(requirements.docker_cli, "resolve", lambda: None)
    monkeypatch.setattr(
        requirements,
        "_run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not run")),
    )

    status = requirements.check_docker()

    assert status.ok is False
    assert status.code == "docker_unavailable"
    assert status.action == "retry"
    assert "PATH" in status.details


def test_probes_the_daemon_with_the_resolved_cli_and_a_timeout(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(requirements.docker_cli, "resolve", lambda: "/opt/docker/bin/docker")
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout="27.5.1\n", stderr="")

    monkeypatch.setattr(requirements, "_run", fake_run)

    status = requirements.check_docker()

    assert status.ok is True
    assert calls == [
        (
            ["version", "--format", "{{.Server.Version}}"],
            {"timeout": requirements.DOCKER_PROBE_TIMEOUT_SECONDS},
        )
    ]


def test_reports_an_unreachable_or_inaccessible_daemon(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(requirements.docker_cli, "resolve", lambda: "/usr/bin/docker")
    monkeypatch.setattr(requirements.docker_cli, "docker_desktop_installed", lambda: False)
    monkeypatch.setattr(
        requirements,
        "_run",
        lambda args, **kwargs: subprocess.CompletedProcess(
            args,
            1,
            stdout="",
            stderr="permission denied while trying to connect to the Docker daemon",
        ),
    )

    status = requirements.check_docker()

    assert status.ok is False
    assert status.code == "docker_not_running"
    assert "access" in status.message
    assert "permission denied" in status.details


def test_offers_to_start_docker_desktop_when_installed(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(requirements.docker_cli, "resolve", lambda: "/Applications/Docker.app/docker")
    monkeypatch.setattr(requirements.docker_cli, "docker_desktop_installed", lambda: True)
    monkeypatch.setattr(
        requirements,
        "_run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 1, stdout="", stderr="not running"),
    )

    status = requirements.check_docker()

    assert status.code == "docker_not_running"
    assert status.action == "start_docker"


def test_times_out_instead_of_hanging_app_startup(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(requirements.docker_cli, "resolve", lambda: "/usr/bin/docker")

    def time_out(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(requirements, "_run", time_out)

    status = requirements.check_docker()

    assert status.ok is False
    assert status.code == "docker_not_running"
    assert status.title == "Docker is not responding"
    assert "5 seconds" in status.details


def test_reports_an_executable_that_cannot_be_started(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(requirements.docker_cli, "resolve", lambda: "/usr/bin/docker")
    monkeypatch.setattr(
        requirements,
        "_run",
        lambda args, **kwargs: (_ for _ in ()).throw(PermissionError("not executable")),
    )

    status = requirements.check_docker()

    assert status.ok is False
    assert status.code == "docker_unavailable"
    assert status.details == "not executable"


def test_missing_image_uses_first_run_build_action(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(requirements, "check_docker", lambda: requirements.RequirementStatus(
        True, "ok", "Docker", "ready", "continue", "test-sandbox"
    ))
    monkeypatch.setattr(
        requirements,
        "_run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 0, stdout="", stderr=""),
    )

    status = requirements.check_sandbox_image()
    assert status.code == "sandbox_image_missing"
    assert status.action == "build"


def test_missing_image_uses_build_action_in_frozen_app(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(requirements, "check_docker", lambda: requirements.RequirementStatus(
        True, "ok", "Docker", "ready", "continue", "test-sandbox"
    ))
    monkeypatch.setattr(requirements.build_info, "is_frozen", lambda: True)
    monkeypatch.setattr(
        requirements,
        "_run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 0, stdout="", stderr=""),
    )

    assert requirements.check_sandbox_image().action == "build"


def test_outdated_image_uses_rebuild_action(monkeypatch):
    _configure(monkeypatch)
    ready = requirements.RequirementStatus(True, "ok", "Ready", "ready", "continue", "test-sandbox")
    monkeypatch.setattr(requirements, "check_docker", lambda: ready)
    monkeypatch.setattr(
        requirements,
        "_run",
        lambda args, **kwargs: subprocess.CompletedProcess(
            args,
            0,
            stdout="old-identity\n" if "inspect" in args else "sha256:image\n",
            stderr="",
        ),
    )

    status = requirements.check_sandbox_image()

    assert status.code == "sandbox_image_outdated"
    assert status.action == "build"
    assert "expected-identity" in status.details


def test_image_query_failure_is_retryable_not_missing(monkeypatch):
    _configure(monkeypatch)
    ready = requirements.RequirementStatus(True, "ok", "Ready", "ready", "continue", "test-sandbox:latest")
    monkeypatch.setattr(requirements, "check_docker", lambda: ready)
    monkeypatch.setattr(
        requirements,
        "_run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 1, stdout="", stderr="daemon interrupted"),
    )

    status = requirements.check_sandbox_image()

    assert status.code == "sandbox_image_check_failed"
    assert status.action == "retry"


def test_image_query_timeout_is_retryable_not_missing(monkeypatch):
    _configure(monkeypatch)
    ready = requirements.RequirementStatus(True, "ok", "Ready", "ready", "continue", "test-sandbox:latest")
    monkeypatch.setattr(requirements, "check_docker", lambda: ready)

    def time_out(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(requirements, "_run", time_out)

    status = requirements.check_sandbox_image()

    assert status.code == "sandbox_image_check_failed"
    assert status.action == "retry"


def test_matching_identity_accepts_existing_image(monkeypatch):
    _configure(monkeypatch)
    ready = requirements.RequirementStatus(True, "ok", "Ready", "ready", "continue", "test-sandbox:latest")
    monkeypatch.setattr(requirements, "check_docker", lambda: ready)
    monkeypatch.setattr(
        requirements,
        "_run",
        lambda args, **kwargs: subprocess.CompletedProcess(
            args,
            0,
            stdout="expected-identity\n" if "inspect" in args else "sha256:image\n",
            stderr="",
        ),
    )

    assert requirements.check_sandbox_image().ok is True


def test_build_stream_uses_bundled_context_and_identity_label(monkeypatch, tmp_path):
    _configure(monkeypatch)
    ready = requirements.RequirementStatus(True, "ok", "Ready", "ready", "continue", "test-sandbox")
    missing = requirements.RequirementStatus(
        False,
        "sandbox_image_missing",
        "Missing",
        "Install tools",
        "build",
        "test-sandbox",
    )
    monkeypatch.setattr(requirements, "PROJECT_ROOT", tmp_path)
    statuses = iter([missing, ready])
    monkeypatch.setattr(requirements, "check_requirements", lambda: next(statuses))
    calls = []

    class FakeProcess:
        def __init__(self):
            self.stdout = io.StringIO("building\n")
            self.returncode = None

        def wait(self, timeout=None):
            self.returncode = 0
            return 0

        def poll(self):
            return self.returncode

    def fake_popen(args, *, cwd=None):
        calls.append((args, cwd))
        return FakeProcess()

    monkeypatch.setattr(requirements.docker_cli, "popen", fake_popen)

    events = list(requirements.build_sandbox_image_stream())

    assert events[-1][0] == "done"
    assert calls[0][1] == tmp_path
    assert "LUMEN_SANDBOX_IDENTITY=expected-identity" in calls[0][0]
    assert "test-sandbox:latest" in calls[0][0]


def test_build_stream_does_not_rebuild_ready_image(monkeypatch):
    _configure(monkeypatch)
    ready = requirements.RequirementStatus(True, "ok", "Ready", "ready", "continue", "test-sandbox")
    monkeypatch.setattr(requirements, "check_requirements", lambda: ready)
    monkeypatch.setattr(
        requirements.docker_cli,
        "popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("build must not start")),
    )

    events = list(requirements.build_sandbox_image_stream())

    assert events == [("done", ready.as_dict())]


def test_build_stream_rejects_concurrent_install(monkeypatch):
    _configure(monkeypatch)
    requirements._SANDBOX_BUILD_LOCK.acquire()
    try:
        events = list(requirements.build_sandbox_image_stream())
    finally:
        requirements._SANDBOX_BUILD_LOCK.release()

    assert events[0][0] == "error"
    assert events[0][1]["code"] == "sandbox_image_build_in_progress"


def test_interrupted_build_stream_terminates_docker_process(monkeypatch):
    _configure(monkeypatch)
    missing = requirements.RequirementStatus(
        False,
        "sandbox_image_missing",
        "Missing",
        "Install tools",
        "build",
        "test-sandbox",
    )
    monkeypatch.setattr(requirements, "check_requirements", lambda: missing)

    class Output:
        closed = False

        def __iter__(self):
            return iter(["building\n", "still building\n"])

        def close(self):
            self.closed = True

    class FakeProcess:
        def __init__(self):
            self.stdout = Output()
            self.returncode = None
            self.terminated = False

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9

    process = FakeProcess()
    monkeypatch.setattr(requirements.docker_cli, "popen", lambda *args, **kwargs: process)

    stream = requirements.build_sandbox_image_stream()
    assert next(stream)[0] == "log"
    stream.close()

    assert process.terminated is True
    assert process.stdout.closed is True
    assert requirements._SANDBOX_BUILD_LOCK.acquire(blocking=False) is True
    requirements._SANDBOX_BUILD_LOCK.release()


def test_start_docker_desktop_reports_starting(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(
        requirements.docker_cli,
        "start_docker_desktop",
        lambda: subprocess.CompletedProcess(["open"], 0, stdout="", stderr=""),
    )

    status = requirements.start_docker_desktop()

    assert status.code == "docker_starting"
    assert status.action == "retry"


@pytest.mark.parametrize("returncode", [0, 1])
def test_build_keeps_only_diagnostic_tail_and_reports_failed_verification(monkeypatch, returncode):
    _configure(monkeypatch)
    missing = requirements.RequirementStatus(False, "sandbox_image_missing", "Missing", "Install", "build", "test")
    outdated = requirements.RequirementStatus(False, "sandbox_image_outdated", "Outdated", "Retry", "build", "test")
    statuses = iter([missing, outdated])
    monkeypatch.setattr(requirements, "check_requirements", lambda: next(statuses))
    process = SimpleNamespace(
        stdout=io.StringIO("".join(f"line {n}\n" for n in range(200))),
        returncode=returncode, wait=lambda: returncode, poll=lambda: returncode,
    )
    monkeypatch.setattr(requirements.docker_cli, "popen", lambda *args, **kwargs: process)

    events = list(requirements.build_sandbox_image_stream())

    assert events[-1][0] == "error"
    if returncode:
        assert events[-1][1]["details"].splitlines() == [f"line {n}" for n in range(150, 200)]
    else:
        assert events[-1][1]["code"] == "sandbox_image_outdated"
    assert process.stdout.closed


def test_build_cleanup_failure_still_closes_output_and_releases_lock(monkeypatch):
    _configure(monkeypatch)
    missing = requirements.RequirementStatus(False, "sandbox_image_missing", "Missing", "Install", "build", "test")
    monkeypatch.setattr(requirements, "check_requirements", lambda: missing)

    def failed_terminate():
        raise OSError("Docker exited during cleanup")

    process = SimpleNamespace(stdout=io.StringIO("building\n"), poll=lambda: None, terminate=failed_terminate)
    monkeypatch.setattr(requirements.docker_cli, "popen", lambda *args, **kwargs: process)
    stream = requirements.build_sandbox_image_stream()
    assert next(stream)[0] == "log"
    with pytest.raises(OSError, match="during cleanup"):
        stream.close()
    assert process.stdout.closed
    assert requirements._SANDBOX_BUILD_LOCK.acquire(blocking=False)
    requirements._SANDBOX_BUILD_LOCK.release()
