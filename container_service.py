"""Docker-backed runtime for per-conversation MCP containers."""
from __future__ import annotations

import logging
import csv
import io
import json
import os
import re
import shutil
import subprocess
import threading
import weakref
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Literal

import advanced_config as _adv_cfg
import build_info
import docker_cli
import folder_mounts
import memory_service
from docker_path_utils import host_path_to_docker_src, parse_volume_source

log = logging.getLogger(__name__)

CONTAINERS_ROOT = Path(os.path.expanduser(os.getenv("LUMEN_CONTAINERS_ROOT", "~/.lumen/containers")))
CONTAINERS_ROOT.mkdir(parents=True, exist_ok=True)

CONTAINER_PREFIX = os.getenv("LUMEN_CONTAINER_PREFIX", "lumen-chat-")
DISCOVERY_CONTAINER_ID = "mcp-discovery"
DOCKER_SHUTDOWN_TIMEOUT_SECONDS = 5

ContainerStatus = Literal["running", "stopped", "missing"]

_CONTAINER_LOCKS = weakref.WeakValueDictionary()
_CONTAINER_LOCKS_GUARD = threading.Lock()

# Last-activity timestamps for idle reaping (conv_id → monotonic seconds).
_last_used: dict[str, float] = {}
_last_used_lock = threading.Lock()


@dataclass(frozen=True)
class ContainerInfo:
    conv_id: str
    name: str
    workspace: Path
    status: ContainerStatus


def _safe_id(conv_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", conv_id or "default")


def container_name(conv_id: str) -> str:
    return f"{CONTAINER_PREFIX}{_safe_id(conv_id)}"


def _container_lock(conv_id: str) -> threading.Lock:
    key = _safe_id(conv_id)
    with _CONTAINER_LOCKS_GUARD:
        lock = _CONTAINER_LOCKS.get(key)
        if lock is None:
            lock = _CONTAINER_LOCKS[key] = threading.Lock()
        return lock


def _workspace(conv_id: str) -> Path:
    path = CONTAINERS_ROOT / _safe_id(conv_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _run(
    args: list[str],
    *,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    docker_args = args[1:] if args and args[0] == "docker" else args
    return docker_cli.run(docker_args, timeout=timeout if timeout is not None else 30)

# mcp_service is imported lazily to avoid the circular import:
# mcp_service → mcp_adapters → container_service → mcp_service.

def _invalidate_mcp_pool(conv_id: str) -> None:
    """Close the persistent MCP session pool for a conversation.

    Called whenever the container process is stopped or removed so that
    cached stdio sessions (which point at dead docker-exec processes) are
    discarded before the next tool call.
    """
    import mcp_service
    mcp_service.close_persistent_pool(conv_id)


# Internal alias kept so the rest of this module can call _volume_source
# without knowing about docker_path_utils.
_volume_source = parse_volume_source


def _inspect_container(name: str) -> dict | None:
    result = _run(["docker", "inspect", "--format", "{{json .}}", name])
    if result.returncode:
        return None
    info = json.loads(result.stdout)
    if not isinstance(info, dict):
        raise RuntimeError(f"Invalid container metadata for {name}")
    return info


def _mounts_match(mounts: list[dict], workspace: Path, shared: list[dict] | None = None) -> bool:
    expected = {"/workspace": (host_path_to_docker_src(str(workspace)), True)}
    memory_spec = _memory_volume_spec()
    if memory_spec:
        expected[memory_service.CONTAINER_PATH] = (_volume_source(memory_spec), True)
    for mount in shared or []:
        expected[f"/workspace/mounts/{mount['name']}"] = (host_path_to_docker_src(mount["source"]), not mount["read_only"])
    actual = {m["Destination"]: (m["Source"], m.get("RW")) for m in mounts if m.get("Type") == "bind"}
    return len(mounts) == len(expected) and actual == expected


def _shared_mount_policy_matches(info: dict, shared: list[dict]) -> bool:
    readonly = {
        mount.get("Target") for mount in info.get("HostConfig", {}).get("Mounts") or []
        if mount.get("ReadOnly") is True
        and (mount.get("BindOptions") or {}).get("ReadOnlyForceRecursive") is True
    }
    return all(f"/workspace/mounts/{mount['name']}" in readonly
               for mount in shared if mount["read_only"])


def _get_tagged_image_id(image: str) -> str:
    result = _run(["docker", "image", "inspect", "--format", "{{.Id}}", image])
    return result.stdout.strip() if result.returncode == 0 else ""


def _container_image_changed(info: dict, expected_image: str) -> bool:
    expected_image = build_info.canonical_image_reference(expected_image)
    labels = info.get("Config", {}).get("Labels") or {}
    tagged_image_id = _get_tagged_image_id(expected_image)
    return (
        labels.get(build_info.CONTAINER_BUILD_LABEL) != expected_image
        or labels.get(build_info.CONTAINER_IDENTITY_LABEL) != build_info.sandbox_identity()
        or not tagged_image_id
        or info.get("Image") != tagged_image_id
    )


def _memory_volume_spec() -> str | None:
    """Return a Docker volume spec for memory.md, creating it from template if needed."""
    try:
        memory_file = memory_service.ensure_file()
        return f"{host_path_to_docker_src(str(memory_file))}:{memory_service.CONTAINER_PATH}:rw"
    except OSError:
        return None


def _volume_args(workspace: Path, shared: list[dict] | None = None) -> list[str]:
    workspace_source = host_path_to_docker_src(str(workspace))
    memory_spec = _memory_volume_spec()
    specs = [f"{workspace_source}:/workspace"]
    if memory_spec:
        specs.append(memory_spec)
    args = [item for spec in specs for item in ("--volume", spec)]
    for mount in shared or []:
        # Docker --mount uses CSV fields; quote paths containing commas/quotes.
        output = io.StringIO()
        fields = ["type=bind", f"source={host_path_to_docker_src(mount['source'])}",
                  f"target=/workspace/mounts/{mount['name']}", "bind-propagation=rprivate"]
        if mount['read_only']:
            fields.extend(['readonly', 'bind-recursive=readonly'])
        csv.writer(output, lineterminator='\n').writerow(fields)
        args.extend(['--mount', output.getvalue()[:-1]])
    return args


def get_status(conv_id: str) -> ContainerStatus:
    result = _run(["docker", "inspect", "--format", "{{.State.Status}}", container_name(conv_id)])
    if result.returncode != 0:
        return "missing"
    return "running" if result.stdout.strip() == "running" else "stopped"


def ensure_container(conv_id: str, extra_volumes: list[str] | None = None) -> ContainerInfo:
    """Create/start the chat container, safely serialized per conversation."""
    if extra_volumes:
        raise ValueError("Implicit host mounts are disabled; use container paths in MCP configuration")
    _touch(conv_id)
    with _container_lock(conv_id):
        return _ensure_container_locked(conv_id)


def _ensure_container_locked(conv_id: str) -> ContainerInfo:
    name = container_name(conv_id)
    workspace = _workspace(conv_id)
    shared = folder_mounts.runtime_mounts(conv_id)

    info = _inspect_container(name)
    if info is not None:
        expected_image = build_info.canonical_image_reference(
            str(_adv_cfg.load_advanced_config()["sandbox_image"])
        )
        image_changed = _container_image_changed(info, expected_image)
        if (image_changed or not _mounts_match(info.get("Mounts", []), workspace, shared)
                or not _shared_mount_policy_matches(info, shared)):
            if image_changed:
                log.info("[container] %s uses an outdated sandbox image; recreating", name)
            stop_container(conv_id)
        elif info["State"]["Status"] != "running":
            _start_existing(name)
            return ContainerInfo(conv_id, name, workspace, "running")
        else:
            return ContainerInfo(conv_id, name, workspace, "running")

    result = _run(_docker_run_command(name, workspace, shared))
    if result.returncode == 0:
        log.info("[container] created %s (%s)", name, result.stdout.strip()[:12])
        return ContainerInfo(conv_id, name, workspace, "running")

    # Python locks solve same-process races. This handles multi-process Flask or
    # a manual docker run that wins between inspect and create.
    if _is_name_conflict(result.stderr):
        return _reuse_conflicting_container(conv_id)

    raise RuntimeError(f"Failed to create container {name}: {result.stderr.strip()}")


def _docker_run_command(name: str, workspace: Path, shared: list[dict] | None = None) -> list[str]:
    cfg = _adv_cfg.load_advanced_config()
    image = build_info.canonical_image_reference(str(cfg["sandbox_image"]))
    network = str(cfg["container_network"])
    if network == "host" or network.startswith("container:"):
        raise ValueError("Host/shared container network namespaces are not allowed")
    return [
        "docker", "run",
        "--detach",
        "--name", name,
        "--label", f"{build_info.CONTAINER_BUILD_LABEL}={image}",
        "--label", f"{build_info.CONTAINER_IDENTITY_LABEL}={build_info.sandbox_identity()}",
        *_volume_args(workspace, shared),
        "--workdir", "/workspace",
        "--pids-limit", "256",
        "--memory", str(cfg["container_memory"]),
        "--cpus",   str(cfg["container_cpus"]),
        "--network", str(cfg["container_network"]),
        "--cap-drop", "ALL",
        "--cap-add", "CHOWN",
        "--cap-add", "DAC_OVERRIDE",
        "--cap-add", "SETUID",
        "--cap-add", "SETGID",
        "--security-opt", "no-new-privileges",
        image,
    ]


def _start_existing(name: str) -> None:
    log.info("[container] starting stopped container %s", name)
    result = _run(["docker", "start", name])
    if result.returncode != 0:
        raise RuntimeError(f"Failed to start container {name}: {result.stderr.strip()}")


def _is_name_conflict(stderr: str) -> bool:
    text = stderr.lower()
    return "already in use" in text or "conflict" in text


def _reuse_conflicting_container(conv_id: str) -> ContainerInfo:
    name = container_name(conv_id)
    info = _inspect_container(name)
    if info is None:
        raise RuntimeError(f"Container {name} name conflict, but docker inspect cannot find it")

    shared = folder_mounts.runtime_mounts(conv_id)
    if (not _mounts_match(info.get("Mounts", []), _workspace(conv_id), shared)
            or not _shared_mount_policy_matches(info, shared)):
        raise RuntimeError(f"Container {name} already exists with unexpected mounts")
    expected_image = build_info.canonical_image_reference(
        str(_adv_cfg.load_advanced_config()["sandbox_image"])
    )
    if _container_image_changed(info, expected_image):
        raise RuntimeError(
            f"Container {name} already exists but uses a different sandbox image"
        )
    if info["State"]["Status"] != "running":
        _start_existing(name)
    log.info("[container] reused concurrently-created %s", name)
    return ContainerInfo(conv_id, name, _workspace(conv_id), "running")


def stop_container_process(conv_id: str) -> bool:
    """Stop a running container but keep it available for quick reuse."""
    name = container_name(conv_id)
    if get_status(conv_id) != "running":
        return True
    # PID 1 sleeps indefinitely; SIGTERM would only add Docker's grace delay.
    result = _run(["docker", "kill", name])
    if result.returncode != 0:
        log.warning("[container] could not stop %s: %s", name, result.stderr.strip())
        return False
    log.info("[container] stopped %s", name)
    # The container process is gone; any open MCP stdio sessions are dead.
    _invalidate_mcp_pool(conv_id)
    return True


def stop_container(conv_id: str, *, strict: bool = False) -> None:
    name = container_name(conv_id)
    result = _run(["docker", "rm", "-f", name])
    if result.returncode and "no such container" not in result.stderr.lower():
        if strict:
            raise RuntimeError(f"Could not remove sandbox {name}: {result.stderr.strip()}")
        log.warning("[container] could not remove %s: %s", name, result.stderr.strip())
    # Invalidate any cached MCP sessions — they point at dead docker-exec
    # processes after the container is gone.
    _invalidate_mcp_pool(conv_id)


def cleanup_stale(known_ids: list[str]) -> list[str]:
    result = _run([
        "docker", "ps", "-a",
        "--filter", f"name={CONTAINER_PREFIX}",
        "--format", "{{.Names}}",
    ])
    if result.returncode != 0:
        log.warning("[container] cleanup skipped: %s", result.stderr.strip())
        return []

    known_names = {container_name(cid) for cid in known_ids}
    known_names.add(container_name(DISCOVERY_CONTAINER_ID))
    removed: list[str] = []
    for name in (line.strip() for line in result.stdout.splitlines()):
        if not name.startswith(CONTAINER_PREFIX) or name in known_names:
            continue
        result = _run(["docker", "rm", "-f", name])
        if result.returncode == 0:
            removed.append(name[len(CONTAINER_PREFIX):])
        else:
            log.warning("[container] could not remove stale %s: %s", name, result.stderr.strip())
    return removed


def wrap_command_for_exec(
    conv_id: str,
    command: str,
    args: list[str],
    *,
    env: Mapping[str, str] | None = None,
) -> tuple[str, list[str]]:
    """Return a docker-exec stdio command for an MCP server process."""
    _touch(conv_id)
    docker_args = ["exec", "-i", "--workdir", "/workspace"]
    for key, value in (env or {}).items():
        docker_args += ["--env", f"{key}={value}"]
    docker_args += [container_name(conv_id), command, *args]
    return docker_cli.executable(), docker_args


def conversation_workspace(conv_id: str) -> Path:
    return _workspace(conv_id)


def delete_workspace(conv_id: str) -> None:
    path = CONTAINERS_ROOT / _safe_id(conv_id)
    if not path.exists():
        return
    if not path.is_dir():
        raise RuntimeError(f"Refusing to delete non-directory workspace: {path}")
    shutil.rmtree(path)
    log.info("[container] deleted workspace %s", path)


def _remaining_shutdown_timeout(deadline: float | None) -> float:
    if deadline is None:
        return float(DOCKER_SHUTDOWN_TIMEOUT_SECONDS)
    return max(0.01, min(float(DOCKER_SHUTDOWN_TIMEOUT_SECONDS), deadline - time.monotonic()))


def stop_all_containers(*, deadline: float | None = None) -> list[str]:
    """Kill every running lumen-chat-* container.

    Called on app shutdown so containers are not left running indefinitely
    after the process exits.  Uses ``docker kill`` (immediate SIGKILL) rather
    than ``docker stop`` because the sandbox runs ``sleep infinity`` which
    ignores SIGTERM, making the grace period of ``docker stop`` pure wasted
    time.  Containers are left in the stopped state rather than removed so
    that ``ensure_container()`` can restart them quickly on the next launch;
    ``cleanup_stale()`` at startup handles any orphaned ones.
    """
    if deadline is not None and deadline <= time.monotonic():
        log.warning("[container] shutdown deadline expired before cleanup started")
        return []
    try:
        result = _run([
            "docker", "ps",
            "--filter", f"name={CONTAINER_PREFIX}",
            "--format", "{{.Names}}",
        ], timeout=_remaining_shutdown_timeout(deadline))
    except subprocess.TimeoutExpired:
        log.warning("[container] shutdown cleanup timed out while listing containers")
        return []
    if result.returncode != 0:
        log.warning("[container] shutdown cleanup skipped: %s", result.stderr.strip())
        return []

    names = [
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip().startswith(CONTAINER_PREFIX)
    ]
    if not names:
        return []

    if deadline is not None and deadline <= time.monotonic():
        log.warning("[container] shutdown deadline expired before containers could be killed")
        return []
    try:
        r = _run(
            ["docker", "kill", *names],
            timeout=_remaining_shutdown_timeout(deadline),
        )
    except subprocess.TimeoutExpired:
        log.warning("[container] shutdown timed out while killing containers")
        return []
    if r.returncode == 0:
        log.info("[container] shutdown: killed %s", names)
    else:
        log.warning("[container] shutdown: could not kill containers: %s", r.stderr.strip())
    return names if r.returncode == 0 else []


# ---------------------------------------------------------------------------
# Idle reaper
# ---------------------------------------------------------------------------

def _touch(conv_id: str) -> None:
    """Record that conv_id was just active, resetting its idle clock."""
    with _last_used_lock:
        _last_used[conv_id] = time.monotonic()


def _reap_once() -> None:
    """Stop every conversation container that has been idle beyond the configured timeout.

    The discovery container is intentionally excluded — it is already managed
    by stop_container_process() in the /api/mcp/tools route.
    """
    idle_timeout = _adv_cfg.load_advanced_config()["container_idle_timeout"]
    if idle_timeout <= 0:
        return

    now = time.monotonic()
    with _last_used_lock:
        candidates = [
            (cid, ts) for cid, ts in _last_used.items()
            if cid != DISCOVERY_CONTAINER_ID and (ts <= 0 or now - ts > idle_timeout)
        ]

    for conv_id, candidate_ts in candidates:
        import sandbox_runtime
        lock = sandbox_runtime.runtime_lock(conv_id)
        if sandbox_runtime.is_active(conv_id) or not lock.acquire(blocking=False):
            continue
        try:
            with _last_used_lock:
                current_ts = _last_used.get(conv_id)
                current_now = time.monotonic()
                if (
                    current_ts is None
                    or current_ts != candidate_ts
                    or (current_ts > 0 and current_now - current_ts <= idle_timeout)
                ):
                    continue

            if get_status(conv_id) == "running":
                log.info("[container] idle timeout reached for %s; stopping", conv_id)
                if stop_container_process(conv_id) is False:
                    continue  # Keep tracking it so the next sweep retries.

            with _last_used_lock:
                if _last_used.get(conv_id) == candidate_ts:
                    _last_used.pop(conv_id, None)
        finally:
            lock.release()


def _reap_idle_containers() -> None:
    """Background daemon loop: check for idle containers every 60 seconds."""
    while True:
        time.sleep(60)
        try:
            _reap_once()
        except Exception:
            log.exception("[container] error in idle reaper")


# Daemon thread — exits automatically when the main process exits. It is
# started explicitly from app.create_app() so tests can import this module
# without spawning background threads.
_reaper_thread: threading.Thread | None = None
_reaper_started = False
_reaper_lock = threading.Lock()


def start_reaper() -> None:
    global _reaper_thread, _reaper_started
    with _reaper_lock:
        if _reaper_started:
            return
        _reaper_thread = threading.Thread(
            target=_reap_idle_containers,
            name="container-idle-reaper",
            daemon=True,
        )
        _reaper_thread.start()
        _reaper_started = True
