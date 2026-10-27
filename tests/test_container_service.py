"""
Tests for container_service.py — pure/non-Docker functions only.

Docker subprocess calls are never made here. The functions under test are:
  _safe_id              — sanitises conv_id for use in container names (security)
  container_name        — full container name construction
  wrap_command_for_exec — builds the docker-exec argv (env injection, ordering)
  _is_name_conflict     — race-condition guard for concurrent container creation
  _volume_args          — volume flag construction
"""
from __future__ import annotations

import container_service
import json
import subprocess
import pytest


# ---------------------------------------------------------------------------
# _safe_id
# ---------------------------------------------------------------------------

class TestSafeId:
    """
    _safe_id() feeds directly into container names passed to `docker run`.
    Any character that Docker or the shell might misinterpret must be
    replaced with an underscore.
    """

    def test_uuid_with_hyphens_unchanged(self):
        uid = "550e8400-e29b-41d4-a716-446655440000"
        assert container_service._safe_id(uid) == uid

    def test_alphanumeric_unchanged(self):
        assert container_service._safe_id("abc123") == "abc123"

    def test_spaces_replaced_with_underscore(self):
        assert "_" in container_service._safe_id("my conv id")
        assert " " not in container_service._safe_id("my conv id")

    def test_special_chars_replaced(self):
        result = container_service._safe_id("conv/with;bad&chars")
        assert "/" not in result
        assert ";" not in result
        assert "&" not in result

    def test_empty_string_returns_default(self):
        assert container_service._safe_id("") == "default"

    def test_none_like_falsy_returns_default(self):
        # None would cause AttributeError upstream; only empty string tested here
        assert container_service._safe_id("") == "default"

    def test_underscores_and_hyphens_kept(self):
        assert container_service._safe_id("my_conv-1") == "my_conv-1"

    def test_output_safe_for_docker_name(self):
        """Result must contain only characters Docker accepts in container names."""
        import re
        result = container_service._safe_id("weird: chars! here@")
        assert re.match(r"^[a-zA-Z0-9_-]+$", result), f"Unsafe name: {result!r}"


# ---------------------------------------------------------------------------
# container_name
# ---------------------------------------------------------------------------

class TestContainerName:

    def test_includes_prefix(self):
        name = container_service.container_name("abc")
        assert name.startswith(container_service.CONTAINER_PREFIX)

    def test_includes_sanitised_id(self):
        name = container_service.container_name("my conv")
        assert "my_conv" in name or "my conv" not in name  # space removed

    def test_special_chars_in_id_sanitised(self):
        name = container_service.container_name("conv/slash")
        assert "/" not in name


# ---------------------------------------------------------------------------
# wrap_command_for_exec
# ---------------------------------------------------------------------------

class TestWrapCommandForExec:
    """
    wrap_command_for_exec builds the argv for `docker exec`. The exact shape
    of this list is what MCP servers receive — getting it wrong means broken
    tool calls.
    """

    def test_returns_resolved_docker_as_command(self, monkeypatch):
        monkeypatch.setattr(container_service.docker_cli, "executable", lambda: "/opt/docker/bin/docker")
        cmd, _ = container_service.wrap_command_for_exec("c1", "npx", ["-y", "@fs"])
        assert cmd == "/opt/docker/bin/docker"

    def test_first_arg_is_exec(self):
        _, args = container_service.wrap_command_for_exec("c1", "npx", [])
        assert args[0] == "exec"

    def test_interactive_flag_present(self):
        _, args = container_service.wrap_command_for_exec("c1", "npx", [])
        assert "-i" in args

    def test_workdir_set_to_workspace(self):
        _, args = container_service.wrap_command_for_exec("c1", "npx", [])
        assert "--workdir" in args
        idx = args.index("--workdir")
        assert args[idx + 1] == "/workspace"

    def test_container_name_in_args(self):
        _, args = container_service.wrap_command_for_exec("c1", "npx", ["-y"])
        expected_name = container_service.container_name("c1")
        assert expected_name in args

    def test_original_command_follows_container_name(self):
        _, args = container_service.wrap_command_for_exec("c1", "node", ["server.js"])
        name = container_service.container_name("c1")
        idx = args.index(name)
        assert args[idx + 1] == "node"

    def test_original_args_appended_after_command(self):
        _, args = container_service.wrap_command_for_exec("c1", "node", ["server.js", "--port", "3000"])
        assert args[-3:] == ["server.js", "--port", "3000"]

    def test_env_vars_injected_as_env_flags(self):
        _, args = container_service.wrap_command_for_exec(
            "c1", "npx", [], env={"MY_VAR": "hello", "PORT": "8080"}
        )
        # Each env entry must appear as --env KEY=VALUE
        assert "--env" in args
        env_pairs = [args[i + 1] for i, a in enumerate(args) if a == "--env"]
        assert any(p.startswith("MY_VAR=") for p in env_pairs)
        assert any(p.startswith("PORT=") for p in env_pairs)

    def test_env_vars_appear_before_container_name(self):
        """--env flags must come before the container name, not after."""
        _, args = container_service.wrap_command_for_exec(
            "c1", "npx", [], env={"K": "V"}
        )
        name = container_service.container_name("c1")
        env_idx = next(i for i, a in enumerate(args) if a == "--env")
        name_idx = args.index(name)
        assert env_idx < name_idx

    def test_no_env_produces_no_env_flags(self):
        _, args = container_service.wrap_command_for_exec("c1", "npx", [], env=None)
        assert "--env" not in args

    def test_empty_env_dict_produces_no_env_flags(self):
        _, args = container_service.wrap_command_for_exec("c1", "npx", [], env={})
        assert "--env" not in args


# ---------------------------------------------------------------------------
# _is_name_conflict
# ---------------------------------------------------------------------------

class TestIsNameConflict:
    """
    Guards against the race where two Flask workers try to create the same
    container simultaneously. Must parse Docker's error messages reliably.
    """

    def test_already_in_use_detected(self):
        assert container_service._is_name_conflict(
            'Error response from daemon: Conflict. The container name "/lumen-chat-abc" is already in use'
        )

    def test_conflict_keyword_detected(self):
        assert container_service._is_name_conflict("conflict with existing container")

    def test_case_insensitive(self):
        assert container_service._is_name_conflict("CONFLICT: name already in use")
        assert container_service._is_name_conflict("Already In Use by container xyz")

    def test_unrelated_error_not_detected(self):
        assert not container_service._is_name_conflict("OCI runtime error: exec failed")
        assert not container_service._is_name_conflict("")
        assert not container_service._is_name_conflict("No such image: lumen-sandbox")


# ---------------------------------------------------------------------------
# _volume_args
# ---------------------------------------------------------------------------

class TestVolumeArgs:
    """
    Verifies the --volume flag list passed to `docker run`.
    """
    from pathlib import Path

    def test_workspace_volume_always_first(self, tmp_path):
        import docker_path_utils

        result = container_service._volume_args(tmp_path)
        assert result[0] == "--volume"
        assert result[1].startswith(docker_path_utils.host_path_to_docker_src(str(tmp_path)))
        assert ":/workspace" in result[1]

    def test_implicit_host_mounts_rejected(self):
        with pytest.raises(ValueError, match="Implicit host mounts"):
            container_service.ensure_container("chat", ["/host:/host:ro"])

    def test_no_extra_volumes_include_workspace_and_memory(self, tmp_path, monkeypatch):
        import docker_path_utils

        monkeypatch.setattr(
            container_service,
            "_memory_volume_spec",
            lambda: "/host/memory.md:/memory.md:rw",
        )
        result = container_service._volume_args(tmp_path)
        assert result == [
            "--volume",
            f"{docker_path_utils.host_path_to_docker_src(str(tmp_path))}:/workspace",
            "--volume",
            "/host/memory.md:/memory.md:rw",
        ]

    def test_windows_workspace_source_uses_forward_slashes(self, monkeypatch):
        import docker_path_utils
        from pathlib import PureWindowsPath

        monkeypatch.setattr(docker_path_utils.sys, "platform", "win32")
        monkeypatch.setattr(container_service, "_memory_volume_spec", lambda: None)

        result = container_service._volume_args(
            PureWindowsPath(r"C:\Users\User\.lumen\containers\abc"),
        )

        assert result == [
            "--volume",
            "C:/Users/User/.lumen/containers/abc:/workspace",
        ]


class TestContainerImageIdentity:
    def test_run_command_labels_container_with_configured_image(self, tmp_path, monkeypatch):
        monkeypatch.setattr(container_service, "_memory_volume_spec", lambda: None)
        monkeypatch.setattr(container_service.build_info, "sandbox_identity", lambda: "sandbox-digest")
        monkeypatch.setattr(container_service._adv_cfg, "load_advanced_config", lambda: {
            "sandbox_image": "registry.example/lumen:1.2.3",
            "container_memory": "512m",
            "container_cpus": "1",
            "container_network": "bridge",
        })

        command = container_service._docker_run_command("lumen-chat-test", tmp_path)

        label = f"{container_service.build_info.CONTAINER_BUILD_LABEL}=registry.example/lumen:1.2.3"
        assert ["--label", label] == command[command.index("--label"):command.index("--label") + 2]
        identity_label = f"{container_service.build_info.CONTAINER_IDENTITY_LABEL}=sandbox-digest"
        assert identity_label in command

    @pytest.mark.parametrize('change', ['image_tag', 'image_id', 'identity', 'mount', None])
    def test_existing_container_validation_uses_two_docker_calls(self, tmp_path, monkeypatch, change):
        monkeypatch.setattr(container_service, '_workspace', lambda cid: tmp_path)
        monkeypatch.setattr(container_service, '_memory_volume_spec', lambda: None)
        monkeypatch.setattr(container_service.build_info, 'sandbox_identity', lambda: 'identity')
        monkeypatch.setattr(container_service.build_info, 'APP_VERSION', '99.0.0-desktop-only')
        monkeypatch.setattr(container_service._adv_cfg, 'load_advanced_config', lambda: {
            'sandbox_image': 'lumen-sandbox', 'container_memory': '512m',
            'container_cpus': '1', 'container_network': 'bridge'})
        info = {
            'State': {'Status': 'running'}, 'Image': 'sha256:current',
            'Config': {'Labels': {
                container_service.build_info.CONTAINER_BUILD_LABEL: 'lumen-sandbox:latest',
                container_service.build_info.CONTAINER_IDENTITY_LABEL: 'identity'}},
            'Mounts': [{'Type': 'bind', 'RW': True, 'Source': str(tmp_path), 'Destination': '/workspace'}],
        }
        if change == 'image_tag':
            info['Config']['Labels'][container_service.build_info.CONTAINER_BUILD_LABEL] = 'old-tag'
        elif change == 'identity':
            info['Config']['Labels'][container_service.build_info.CONTAINER_IDENTITY_LABEL] = 'old-identity'
        elif change == 'image_id':
            info['Image'] = 'sha256:old'
        elif change == 'mount':
            info['Mounts'].append({'Type': 'bind', 'RW': True, 'Source': '/', 'Destination': '/host'})
        calls, removed = [], []
        def run(args):
            calls.append(args)
            output = json.dumps(info) if args[1] == 'inspect' else 'sha256:current'
            return subprocess.CompletedProcess(args, 0, output, '')
        monkeypatch.setattr(container_service, '_run', run)
        monkeypatch.setattr(container_service, 'stop_container', removed.append)
        assert container_service.ensure_container('conversation').status == 'running'
        assert removed == (['conversation'] if change else [])
        assert len(calls) == (3 if change else 2)
        assert calls[0][1] == 'inspect' and calls[1][1:3] == ['image', 'inspect']


class TestShutdownTimeouts:
    def test_stop_all_uses_bounded_docker_calls(self, monkeypatch):
        import subprocess

        calls = []

        def fake_run(args, *, timeout=None):
            calls.append((args, timeout))
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        monkeypatch.setattr(container_service, "_run", fake_run)

        assert container_service.stop_all_containers() == []
        assert calls == [(
            ["docker", "ps", "--filter", f"name={container_service.CONTAINER_PREFIX}", "--format", "{{.Names}}"],
            container_service.DOCKER_SHUTDOWN_TIMEOUT_SECONDS,
        )]

    def test_stop_all_returns_when_docker_times_out(self, monkeypatch):
        import subprocess

        def time_out(args, *, timeout=None):
            raise subprocess.TimeoutExpired(args, timeout)

        monkeypatch.setattr(container_service, "_run", time_out)

        assert container_service.stop_all_containers() == []


# ---------------------------------------------------------------------------
# Idle reaper
# ---------------------------------------------------------------------------

class TestTouch:
    def test_touch_records_conv_id(self):
        import time
        container_service._last_used.clear()
        before = time.monotonic()
        container_service._touch("conv-abc")
        after = time.monotonic()
        assert "conv-abc" in container_service._last_used
        assert before <= container_service._last_used["conv-abc"] <= after

    def test_touch_updates_existing_entry(self):
        container_service._last_used["conv-abc"] = 0.0
        container_service._touch("conv-abc")
        assert container_service._last_used["conv-abc"] > 0.0

    def test_multiple_convs_tracked_independently(self):
        container_service._last_used.clear()
        container_service._touch("conv-1")
        container_service._touch("conv-2")
        assert "conv-1" in container_service._last_used
        assert "conv-2" in container_service._last_used


class TestReapOnce:
    def setup_method(self):
        container_service._last_used.clear()

    def test_stops_idle_running_container(self, monkeypatch):
        stopped = []
        monkeypatch.setattr(container_service._adv_cfg, "load_advanced_config", lambda: {"container_idle_timeout": 300})
        monkeypatch.setattr(container_service, "get_status", lambda cid: "running")
        monkeypatch.setattr(container_service, "stop_container_process", lambda cid: stopped.append(cid))

        container_service._last_used["old-conv"] = 0.0   # very old
        container_service._reap_once()

        assert "old-conv" in stopped

    def test_removes_reaped_entry_from_last_used(self, monkeypatch):
        monkeypatch.setattr(container_service._adv_cfg, "load_advanced_config", lambda: {"container_idle_timeout": 300})
        monkeypatch.setattr(container_service, "get_status", lambda cid: "running")
        monkeypatch.setattr(container_service, "stop_container_process", lambda cid: None)

        container_service._last_used["old-conv"] = 0.0
        container_service._reap_once()

        assert "old-conv" not in container_service._last_used

    def test_skips_non_idle_container(self, monkeypatch):
        import time
        stopped = []
        monkeypatch.setattr(container_service._adv_cfg, "load_advanced_config", lambda: {"container_idle_timeout": 300})
        monkeypatch.setattr(container_service, "get_status", lambda cid: "running")
        monkeypatch.setattr(container_service, "stop_container_process", lambda cid: stopped.append(cid))

        container_service._last_used["fresh-conv"] = time.monotonic()  # just touched
        container_service._reap_once()

        assert "fresh-conv" not in stopped
        assert "fresh-conv" in container_service._last_used   # entry kept

    def test_skips_already_stopped_container(self, monkeypatch):
        stopped = []
        monkeypatch.setattr(container_service._adv_cfg, "load_advanced_config", lambda: {"container_idle_timeout": 300})
        monkeypatch.setattr(container_service, "get_status", lambda cid: "stopped")
        monkeypatch.setattr(container_service, "stop_container_process", lambda cid: stopped.append(cid))

        container_service._last_used["idle-conv"] = 0.0
        container_service._reap_once()

        assert stopped == []                              # stop_container_process not called
        assert "idle-conv" not in container_service._last_used   # entry still cleaned up

    def test_skips_discovery_container(self, monkeypatch):
        stopped = []
        monkeypatch.setattr(container_service._adv_cfg, "load_advanced_config", lambda: {"container_idle_timeout": 300})
        monkeypatch.setattr(container_service, "get_status", lambda cid: "running")
        monkeypatch.setattr(container_service, "stop_container_process", lambda cid: stopped.append(cid))

        container_service._last_used[container_service.DISCOVERY_CONTAINER_ID] = 0.0
        container_service._reap_once()

        assert container_service.DISCOVERY_CONTAINER_ID not in stopped

    def test_disabled_when_idle_timeout_zero(self, monkeypatch):
        stopped = []
        monkeypatch.setattr(container_service._adv_cfg, "load_advanced_config", lambda: {"container_idle_timeout": 0})
        monkeypatch.setattr(container_service, "get_status", lambda cid: "running")
        monkeypatch.setattr(container_service, "stop_container_process", lambda cid: stopped.append(cid))

        container_service._last_used["old-conv"] = 0.0
        container_service._reap_once()

        assert stopped == []

    def test_only_reaps_idle_not_fresh(self, monkeypatch):
        import time
        stopped = []
        monkeypatch.setattr(container_service._adv_cfg, "load_advanced_config", lambda: {"container_idle_timeout": 300})
        monkeypatch.setattr(container_service, "get_status", lambda cid: "running")
        monkeypatch.setattr(container_service, "stop_container_process", lambda cid: stopped.append(cid))

        container_service._last_used["old-conv"] = 0.0
        container_service._last_used["fresh-conv"] = time.monotonic()
        container_service._reap_once()

        assert stopped == ["old-conv"]
        assert "fresh-conv" in container_service._last_used


# ---------------------------------------------------------------------------
# discovery container cleanup protection
# ---------------------------------------------------------------------------

class TestCleanupStaleDiscoveryContainer:
    """
    The settings/tools discovery container is reusable and intentionally kept
    stopped between discovery runs, so stale cleanup must never delete it.
    """

    def test_cleanup_stale_keeps_discovery_container(self, monkeypatch):
        import subprocess

        discovery_name = container_service.container_name(
            container_service.DISCOVERY_CONTAINER_ID
        )
        active_name = container_service.container_name("active-chat")
        stale_name = container_service.container_name("old-chat")
        calls: list[list[str]] = []

        def fake_run(args):
            calls.append(args)
            if args[:3] == ["docker", "ps", "-a"]:
                return subprocess.CompletedProcess(
                    args,
                    0,
                    stdout=f"{discovery_name}\n{active_name}\n{stale_name}\n",
                    stderr="",
                )
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        monkeypatch.setattr(container_service, "_run", fake_run)

        removed = container_service.cleanup_stale(["active-chat"])

        assert removed == ["old-chat"]
        rm_calls = [args for args in calls if args[:3] == ["docker", "rm", "-f"]]
        assert rm_calls == [["docker", "rm", "-f", stale_name]]


def test_failed_idle_stop_remains_tracked_and_retries(monkeypatch):
    monkeypatch.setattr(container_service._adv_cfg, 'load_advanced_config', lambda: {'container_idle_timeout': 1})
    monkeypatch.setattr(container_service, 'get_status', lambda cid: 'running')
    attempts = []
    monkeypatch.setattr(container_service, 'stop_container_process', lambda cid: attempts.append(cid) or False)
    monkeypatch.setattr(container_service, '_last_used', {'idle-failed': 0})
    container_service._reap_once()
    container_service._reap_once()
    assert attempts == ['idle-failed', 'idle-failed']
    assert 'idle-failed' in container_service._last_used


def test_failed_container_stop_keeps_live_mcp_sessions(monkeypatch):
    from unittest.mock import Mock
    monkeypatch.setattr(container_service, 'get_status', lambda cid: 'running')
    monkeypatch.setattr(container_service, '_run', lambda args: subprocess.CompletedProcess(args, 1, '', 'daemon unavailable'))
    invalidate = Mock()
    monkeypatch.setattr(container_service, '_invalidate_mcp_pool', invalidate)
    assert container_service.stop_container_process('idle-failed') is False
    invalidate.assert_not_called()


def test_idle_stop_kills_without_docker_sigterm_grace_delay(monkeypatch):
    from unittest.mock import Mock
    monkeypatch.setattr(container_service, 'get_status', lambda cid: 'running')
    run = Mock(return_value=subprocess.CompletedProcess([], 0, '', ''))
    invalidate = Mock()
    monkeypatch.setattr(container_service, '_run', run)
    monkeypatch.setattr(container_service, '_invalidate_mcp_pool', invalidate)
    assert container_service.stop_container_process('idle') is True
    run.assert_called_once_with(['docker', 'kill', container_service.container_name('idle')])
    invalidate.assert_called_once_with('idle')
