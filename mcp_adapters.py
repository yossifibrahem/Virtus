"""MCP process launch helpers — all servers run in the per-chat Docker container."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import container_service
from docker_path_utils import make_volume_spec, translate_arg_for_container

log = logging.getLogger(__name__)

WORKSPACE_ROOT = container_service.CONTAINERS_ROOT
CONTAINER_WORKDIR = "/workspace"


class ContainerConversationRequired(RuntimeError):
    """Raised when a container-runtime MCP server is used without a chat id."""


def conversation_working_directory(conversation_id: str) -> Path:
    """Return the host-side workspace mounted as /workspace in containers."""
    return container_service.conversation_workspace(conversation_id)


def expand_config_env(env: dict | None) -> dict:
    """Expand user-friendly paths in explicit MCP env config."""
    return {
        str(key): os.path.expanduser(str(value)) if isinstance(value, str) else value
        for key, value in (env or {}).items()
    }


def apply_workspace_process_options(
    params: dict[str, Any],
    env: dict[str, Any],
    *,
    server_name: str = "",
    server_config: dict | None = None,
    conv_id: str = "",
) -> None:
    """Mutate StdioServerParameters kwargs to run the MCP server in the conversation container."""
    _apply_container(params, env, server_name, server_config or {}, conv_id)


def _apply_container(
    params: dict[str, Any],
    env: dict[str, Any],
    server_name: str,
    server_config: dict,
    conv_id: str,
) -> None:
    if not conv_id:
        raise ContainerConversationRequired(
            f"MCP server '{server_name}' requires a conversation to be open."
        )

    extra_volumes = extract_host_mounts(server_config)
    container_service.ensure_container(conv_id, extra_volumes=extra_volumes)

    explicit_env = expand_config_env(server_config.get("env", {}))
    container_env = {
        **{str(k): str(v) for k, v in explicit_env.items()},
        "PWD": CONTAINER_WORKDIR,
    }

    command, args = container_service.wrap_command_for_exec(
        conv_id,
        params["command"],
        [translate_arg_for_container(a) for a in params.get("args", [])],
        env=container_env,
    )
    params["command"] = command
    params["args"] = args

    env.clear()
    env.update(os.environ)
    params.pop("cwd", None)


def extract_host_mounts(server_config: dict) -> list[str]:
    """Mount absolute script/project paths used by a container-runtime server."""
    volumes: list[str] = []
    seen: set[str] = set()

    for arg in server_config.get("args", []):
        if not isinstance(arg, str):
            continue
        path = Path(os.path.expanduser(arg))
        if not path.is_absolute():
            continue

        mount_src = find_project_root(path) or (path.parent if path.suffix else path)
        if not mount_src.exists():
            log.warning(
                "[mcp] cannot mount missing path for container server: %s", mount_src
            )
            continue

        src = str(mount_src)
        if src not in seen:
            seen.add(src)
            volumes.append(make_volume_spec(src))

    return volumes


def find_project_root(path: Path) -> Path | None:
    """Find a nearby project root without accidentally mounting /home or /."""
    candidate = path.parent if path.is_file() else path
    markers = {"node_modules", "package.json", "pyproject.toml", "setup.py"}

    for _ in range(6):
        if any((candidate / marker).exists() for marker in markers):
            return candidate
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    return None
