"""MCP process launch helpers — all servers run in the per-chat Docker container."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import container_service

CONTAINER_WORKDIR = "/workspace"


class ContainerConversationRequired(RuntimeError):
    """Raised when a container-runtime MCP server is used without a chat id."""


def conversation_working_directory(conversation_id: str) -> Path:
    """Return the host-side workspace mounted as /workspace in containers."""
    return container_service.conversation_workspace(conversation_id)


def apply_workspace_process_options(
    params: dict[str, Any],
    env: dict[str, Any],
    *,
    server_name: str = "",
    server_config: dict | None = None,
    conv_id: str = "",
) -> None:
    """Mutate StdioServerParameters kwargs to run the MCP server in the conversation container."""
    server_config = server_config or {}
    if not conv_id:
        raise ContainerConversationRequired(
            f"MCP server '{server_name}' requires a conversation to be open."
        )

    container_service.ensure_container(conv_id)

    explicit_env = server_config.get("env", {})
    container_env = {
        **{str(k): str(v) for k, v in explicit_env.items()},
        "PWD": CONTAINER_WORKDIR,
    }

    command, args = container_service.wrap_command_for_exec(
        conv_id,
        params["command"],
        [str(a) for a in params.get("args", [])],
        env=container_env,
    )
    params["command"] = command
    params["args"] = args

    env.clear()
    env.update(os.environ)
    params.pop("cwd", None)
