"""Runtime dependency checks for Docker-backed sandbox support."""
from __future__ import annotations

import logging
import subprocess
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import advanced_config
import build_info
import docker_cli

log = logging.getLogger(__name__)
PROJECT_ROOT = build_info.resource_root()
DOCKER_PROBE_TIMEOUT_SECONDS = 5
_SANDBOX_BUILD_LOCK = threading.Lock()


@dataclass(frozen=True)
class RequirementStatus:
    """Human- and API-friendly dependency state."""

    ok: bool
    code: str
    title: str
    message: str
    action: str
    image: str
    details: str = ""

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "code": self.code,
            "title": self.title,
            "message": self.message,
            "action": self.action,
            "image": self.image,
            "details": self.details,
        }


def _image_name() -> str:
    configured = str(advanced_config.load_advanced_config()["sandbox_image"])
    return build_info.canonical_image_reference(configured)


def _expected_sandbox_identity() -> str:
    return build_info.sandbox_identity(PROJECT_ROOT)


def _run(
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    docker_args = args[1:] if args and args[0] == "docker" else args
    return docker_cli.run(docker_args, cwd=cwd, timeout=timeout)


def _docker_unavailable(image: str, details: str) -> RequirementStatus:
    return RequirementStatus(
        ok=False,
        code="docker_unavailable",
        title="Docker is not available",
        message="Install Docker Desktop, then return to Lumen and click Retry.",
        action="retry",
        image=image,
        details=details,
    )


def _image_check_failed(image: str, details: str) -> RequirementStatus:
    return RequirementStatus(
        False, "sandbox_image_check_failed", "Lumen could not check its tools",
        "Docker returned a temporary error while checking the sandbox. Click Retry.",
        "retry", image, details,
    )


def check_docker() -> RequirementStatus:
    """Return whether both the Docker CLI and daemon are usable.

    Locating the executable separately gives a precise installation error,
    while querying the server version verifies that the selected Docker
    context can actually reach a daemon.  The short timeout prevents a stuck
    Docker Desktop or remote context from hanging app startup indefinitely.
    """
    image = _image_name()
    docker = docker_cli.resolve()
    if docker is None:
        return _docker_unavailable(image, "The docker executable was not found on PATH.")

    try:
        result = _run(
            ["version", "--format", "{{.Server.Version}}"],
            timeout=DOCKER_PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return RequirementStatus(
            ok=False,
            code="docker_not_running",
            title="Docker is not responding",
            message="Start or restart Docker, then click Retry.",
            action="retry",
            image=image,
            details=f"Docker did not respond within {DOCKER_PROBE_TIMEOUT_SECONDS} seconds.",
        )
    except OSError as exc:
        return _docker_unavailable(image, str(exc))

    if result.returncode != 0:
        can_start = docker_cli.docker_desktop_installed()
        return RequirementStatus(
            ok=False,
            code="docker_not_running",
            title="Docker is not ready",
            message=(
                "Docker Desktop is installed but is not running. Click Start Docker."
                if can_start
                else "Start Docker and make sure this user can access it, then click Retry."
            ),
            action="start_docker" if can_start else "retry",
            image=image,
            details=(result.stderr or result.stdout).strip(),
        )

    return RequirementStatus(
        ok=True,
        code="ok",
        title="Docker is ready",
        message="Docker is running.",
        action="continue",
        image=image,
    )


def check_sandbox_image() -> RequirementStatus:
    """Return whether the configured sandbox image exists and is compatible."""
    image = _image_name()
    docker_status = check_docker()
    if not docker_status.ok:
        return docker_status

    try:
        list_result = _run(
            ["image", "ls", "--quiet", "--no-trunc", image],
            timeout=DOCKER_PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return _image_check_failed(image, str(exc))
    if list_result.returncode != 0:
        return _image_check_failed(image, (list_result.stderr or list_result.stdout).strip())
    if not list_result.stdout.strip():
        return RequirementStatus(
            ok=False,
            code="sandbox_image_missing",
            title="Lumen tools need to be installed",
            message=(
                "Click Install Lumen Tools. Docker will download the required "
                "components and build the sandbox on this device."
            ),
            action="build",
            image=image,
        )

    try:
        identity_result = _run(
            [
                "image",
                "inspect",
                "--format",
                f'{{{{index .Config.Labels "{build_info.CONTAINER_IDENTITY_LABEL}"}}}}',
                image,
            ],
            timeout=DOCKER_PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return _image_check_failed(image, str(exc))
    if identity_result.returncode != 0:
        return _image_check_failed(image, (identity_result.stderr or identity_result.stdout).strip())
    try:
        expected_identity = _expected_sandbox_identity()
    except OSError as exc:
        return RequirementStatus(
            ok=False,
            code="sandbox_image_check_failed",
            title="Lumen's bundled tools are incomplete",
            message="Required sandbox build files could not be read. Reinstall Lumen.",
            action="retry",
            image=image,
            details=str(exc),
        )
    image_identity = identity_result.stdout.strip()
    if image_identity != expected_identity:
        return RequirementStatus(
            ok=False,
            code="sandbox_image_outdated",
            title="Lumen tools need to be updated",
            message="Click Install Lumen Tools to rebuild them from the bundled Dockerfile.",
            action="build",
            image=image,
            details=(
                f"Installed tools identity: {image_identity or 'legacy/unidentified'}\n"
                f"Required tools identity: {expected_identity}"
            ),
        )

    return RequirementStatus(
        ok=True,
        code="ok",
        title="Lumen is ready",
        message="Docker is running and the sandbox image is available.",
        action="continue",
        image=image,
    )


def check_requirements() -> RequirementStatus:
    """Return the first unmet runtime requirement, or ok."""
    return check_sandbox_image()


def build_sandbox_image_stream():
    """Stream docker build output as (event, data) tuples for SSE.

    Yields:
        ("log",   {"line": str})          – one line of build output
        ("done",  RequirementStatus.as_dict())  – build succeeded
        ("error", RequirementStatus.as_dict())  – build failed
    """
    if not _SANDBOX_BUILD_LOCK.acquire(blocking=False):
        yield "error", RequirementStatus(
            ok=False,
            code="sandbox_image_build_in_progress",
            title="Lumen tools are already being installed",
            message="Wait for the current installation to finish, then click Retry.",
            action="retry",
            image=_image_name(),
        ).as_dict()
        return

    proc = None
    try:
        initial_status = check_requirements()
        if initial_status.ok:
            yield "done", initial_status.as_dict()
            return
        if initial_status.code not in {"sandbox_image_missing", "sandbox_image_outdated"}:
            yield "error", initial_status.as_dict()
            return

        image = _image_name()
        try:
            identity = _expected_sandbox_identity()
        except OSError as exc:
            yield "error", RequirementStatus(
                ok=False,
                code="sandbox_image_build_failed",
                title="Lumen's bundled tools are incomplete",
                message="Required sandbox build files could not be read. Reinstall Lumen.",
                action="retry",
                image=image,
                details=str(exc),
            ).as_dict()
            return
        cmd = [
            "build",
            "--progress=plain",
            "--build-arg", f"LUMEN_SANDBOX_IDENTITY={identity}",
            "-f", "Dockerfile.sandbox",
            "-t", image,
            ".",
        ]

        try:
            proc = docker_cli.popen(cmd, cwd=PROJECT_ROOT)
        except OSError as exc:
            yield "error", RequirementStatus(
                ok=False,
                code="docker_unavailable",
                title="Docker is not available",
                message="Please install/start Docker, then try again.",
                action="retry",
                image=image,
                details=str(exc),
            ).as_dict()
            return

        output_lines = deque(maxlen=50)
        if proc.stdout is None:
            raise RuntimeError("Docker build output stream was not created")
        for line in proc.stdout:
            line = line.rstrip("\n")
            output_lines.append(line[-8192:])
            yield "log", {"line": line}

        proc.wait()

        if proc.returncode != 0:
            yield "error", RequirementStatus(
                ok=False,
                code="sandbox_image_build_failed",
                title="Sandbox image build failed",
                message="The sandbox image could not be built. Check the details below, then try again.",
                action="build",
                image=image,
                details="\n".join(output_lines),
            ).as_dict()
            return

        log.info("[startup] sandbox image '%s' built successfully", image)
        status = check_requirements()
        yield "done" if status.ok else "error", status.as_dict()
    finally:
        try:
            if proc is not None and proc.poll() is None:
                log.warning("[startup] sandbox installation stream ended early; terminating docker build")
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
        finally:
            try:
                if proc is not None and proc.stdout is not None:
                    proc.stdout.close()
            finally:
                _SANDBOX_BUILD_LOCK.release()


def start_docker_desktop() -> RequirementStatus:
    """Launch Docker Desktop after an explicit user action."""
    image = _image_name()
    try:
        result = docker_cli.start_docker_desktop()
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        return RequirementStatus(
            ok=False,
            code="docker_start_failed",
            title="Docker could not be started",
            message="Open Docker Desktop manually, then click Retry.",
            action="retry",
            image=image,
            details=str(exc),
        )
    if result.returncode != 0:
        return RequirementStatus(
            ok=False,
            code="docker_start_failed",
            title="Docker could not be started",
            message="Open Docker Desktop manually, then click Retry.",
            action="retry",
            image=image,
            details=(result.stderr or result.stdout).strip(),
        )
    return RequirementStatus(
        ok=False,
        code="docker_starting",
        title="Docker is starting",
        message="Lumen is waiting for Docker Desktop to become ready.",
        action="retry",
        image=image,
    )
