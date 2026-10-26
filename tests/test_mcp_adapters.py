"""
Tests for mcp_adapters.py — container launch parameter mutation,
explicit container environment and path handling.

Docker is never called; `container_service.ensure_container` and
`container_service.wrap_command_for_exec` are always mocked.
"""
from __future__ import annotations

import os
import pytest
from unittest.mock import patch, MagicMock

import mcp_adapters
from mcp_adapters import ContainerConversationRequired


# ---------------------------------------------------------------------------
# apply_workspace_process_options
# ---------------------------------------------------------------------------

def _mock_wrap_result(conv_id: str, command: str, args: list):
    """Canonical return value from wrap_command_for_exec."""
    return ("docker", ["exec", "-i", f"lumen-chat-{conv_id}", command, *args])


class TestApplyWorkspaceProcessOptions:

    def _call(self, params, env, *, conv_id="testconv", server_config=None):
        with patch("mcp_adapters.container_service.ensure_container") as mock_ensure, \
             patch("mcp_adapters.container_service.wrap_command_for_exec") as mock_wrap:
            mock_ensure.return_value = MagicMock()
            mock_wrap.return_value = _mock_wrap_result(
                conv_id, params["command"], params.get("args", [])
            )
            mcp_adapters.apply_workspace_process_options(
                params,
                env,
                server_name="test-server",
                server_config=server_config or {},
                conv_id=conv_id,
            )
            return mock_ensure, mock_wrap

    def test_raises_container_conv_required_without_conv_id(self):
        with pytest.raises(ContainerConversationRequired):
            mcp_adapters.apply_workspace_process_options(
                {"command": "npx", "args": []},
                {},
                server_name="srv",
                conv_id="",
            )

    def test_command_becomes_docker(self):
        params = {"command": "node", "args": ["server.js"]}
        self._call(params, {})
        assert params["command"] == "docker"

    def test_args_contain_exec(self):
        params = {"command": "node", "args": ["server.js"]}
        self._call(params, {})
        assert "exec" in params["args"]

    def test_cwd_removed_from_params(self):
        params = {"command": "npx", "args": [], "cwd": "/some/path"}
        self._call(params, {})
        assert "cwd" not in params

    def test_env_dict_cleared_then_repopulated(self):
        params = {"command": "npx", "args": []}
        env = {"STALE_VAR": "old_value"}
        self._call(params, env)
        # env is cleared and refilled from os.environ; STALE_VAR only
        # survives if it's also in os.environ
        if "STALE_VAR" not in os.environ:
            assert "STALE_VAR" not in env

    def test_ensure_container_called_with_conv_id(self):
        params = {"command": "npx", "args": []}
        mock_ensure, _ = self._call(params, {}, conv_id="myconv")
        mock_ensure.assert_called_once()
        call_args = mock_ensure.call_args
        assert "myconv" in call_args.args or "myconv" in str(call_args)

    def test_wrap_command_called_with_original_command(self):
        params = {"command": "python3", "args": ["-m", "server"]}
        _, mock_wrap = self._call(params, {})
        wrap_call = mock_wrap.call_args
        # The original command is passed to wrap_command_for_exec
        assert "python3" in str(wrap_call)

    def test_explicit_env_vars_forwarded(self):
        params = {"command": "npx", "args": []}
        env = {}
        server_config = {"env": {"MY_KEY": "my_value"}}
        with patch("mcp_adapters.container_service.ensure_container"), \
             patch("mcp_adapters.container_service.wrap_command_for_exec") as mock_wrap:
            mock_wrap.return_value = ("docker", [])
            mcp_adapters.apply_workspace_process_options(
                params, env,
                server_name="srv",
                server_config=server_config,
                conv_id="c1",
            )
        # PWD must be in the env passed to wrap so the shell starts in /workspace.
        # Server-specific working-directory variables must be configured explicitly.
        wrap_kwargs = mock_wrap.call_args.kwargs
        passed_env = wrap_kwargs.get("env", {})
        assert passed_env.get("PWD") == "/workspace"
        assert "WORKING_DIR" not in passed_env


def test_mcp_paths_and_env_are_container_values():
    params = {"command": "node", "args": ["/workspace/server.js"]}
    with patch("container_service.ensure_container") as ensure, patch(
        "container_service.wrap_command_for_exec", return_value=("docker", [])
    ) as wrap:
        mcp_adapters.apply_workspace_process_options(
            params, {}, conv_id="chat", server_config={"env": {"HOME": "/root", "DATA": "~/data"}}
        )
    ensure.assert_called_once_with("chat")
    assert wrap.call_args.kwargs["env"]["DATA"] == "~/data"
    assert wrap.call_args.args[2] == ["/workspace/server.js"]
