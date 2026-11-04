"""Container command construction and lifecycle with Docker mocked out."""
from __future__ import annotations

import container_service
import json
import subprocess
import pytest


@pytest.mark.parametrize("value,expected", [
    ("550e8400-e29b-41d4-a716-446655440000", "550e8400-e29b-41d4-a716-446655440000"),
    ("my_conv-1", "my_conv-1"),
    ("conv/with;bad&chars", "conv_with_bad_chars"),
    ("my conv", "my_conv"),
    ("", "default"),
])
def test_container_names_sanitize_ids(value, expected):
    assert container_service._safe_id(value) == expected
    assert container_service.container_name(value) == container_service.CONTAINER_PREFIX + expected


@pytest.mark.parametrize("env", [None, {}])
def test_exec_without_environment_has_no_env_flags(env):
    _, args = container_service.wrap_command_for_exec("c1", "npx", [], env=env)
    assert "--env" not in args


def test_exec_command_preserves_options_environment_and_argument_order(monkeypatch):
    monkeypatch.setattr(container_service.docker_cli, "executable", lambda: "/opt/docker/bin/docker")
    command, args = container_service.wrap_command_for_exec(
        "c1", "node", ["server.js", "--port", "3000"], env={"MY_VAR": "hello", "PORT": "8080"},
    )
    assert command == "/opt/docker/bin/docker"
    assert args[:2] == ["exec", "-i"]
    assert args[args.index("--workdir") + 1] == "/workspace"
    name_index = args.index(container_service.container_name("c1"))
    assert args[name_index + 1:] == ["node", "server.js", "--port", "3000"]
    env_indexes = [i for i, arg in enumerate(args) if arg == "--env"]
    assert {args[i + 1] for i in env_indexes} == {"MY_VAR=hello", "PORT=8080"}
    assert all(i < name_index for i in env_indexes)


@pytest.mark.parametrize("message,expected", [
    ('The container name "/virtus-chat-abc" is already in use', True),
    ("CONFLICT with existing container", True),
    ("OCI runtime error: exec failed", False),
    ("No such image: virtus-sandbox", False),
    ("", False),
])
def test_name_conflict_detection(message, expected):
    assert container_service._is_name_conflict(message) is expected


class TestVolumeArgs:
    """
    Verifies the --volume flag list passed to `docker run`.
    """
    from pathlib import Path

    def test_workspace_volume_always_first(self):
        assert container_service._volume_args('test-workspace') == ['--mount', 'type=volume,src=test-workspace,dst=/workspace,volume-nocopy']

    def test_implicit_host_mounts_rejected(self):
        with pytest.raises(ValueError, match="Implicit host mounts"):
            container_service.ensure_container("chat", ["/host:/host:ro"])


class TestContainerImageIdentity:
    def test_run_command_labels_container_with_configured_image(self, tmp_path, monkeypatch):
        monkeypatch.setattr(container_service.build_info, "sandbox_identity", lambda: "sandbox-digest")
        monkeypatch.setattr(container_service._adv_cfg, "load_advanced_config", lambda: {
            "sandbox_image": "registry.example/virtus:1.2.3",
            "container_memory": "512m",
            "container_cpus": "1",
            "container_network": "bridge",
        })

        command = container_service._docker_run_command("virtus-chat-test", tmp_path)

        label = f"{container_service.build_info.CONTAINER_BUILD_LABEL}=registry.example/virtus:1.2.3"
        assert ["--label", label] == command[command.index("--label"):command.index("--label") + 2]
        identity_label = f"{container_service.build_info.CONTAINER_IDENTITY_LABEL}=sandbox-digest"
        assert identity_label in command

    @pytest.mark.parametrize('change', ['image_tag', 'image_id', 'identity', 'mount', None])
    def test_existing_container_validation_uses_two_docker_calls(self, tmp_path, monkeypatch, change):
        monkeypatch.setattr(container_service, 'workspace_volume', lambda cid: 'test-workspace')
        monkeypatch.setattr(container_service.build_info, 'sandbox_identity', lambda: 'identity')
        monkeypatch.setattr(container_service.build_info, 'APP_VERSION', '99.0.0-desktop-only')
        monkeypatch.setattr(container_service._adv_cfg, 'load_advanced_config', lambda: {
            'sandbox_image': 'virtus-sandbox', 'container_memory': '512m',
            'container_cpus': '1', 'container_network': 'bridge'})
        info = {
            'State': {'Status': 'running'}, 'Image': 'sha256:current',
            'HostConfig': {'Memory': 512*1024**2, 'NanoCpus': 1000000000,
                'PidsLimit': 256, 'Privileged': False, 'CapDrop': ['ALL'],
                'CapAdd': ['CHOWN', 'DAC_OVERRIDE', 'SETUID', 'SETGID'],
                'SecurityOpt': ['no-new-privileges']},
            'Config': {'WorkingDir': '/workspace', 'Labels': {
                container_service.build_info.CONTAINER_BUILD_LABEL: 'virtus-sandbox:latest',
                container_service.build_info.CONTAINER_IDENTITY_LABEL: 'identity'}},
            'Mounts': [{'Type': 'volume', 'RW': True, 'Name': 'test-workspace', 'Destination': '/workspace'}],
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
        monkeypatch.setattr(container_service, 'stop_container', lambda cid, strict=False: removed.append(cid))
        assert container_service.ensure_container('conversation').status == 'running'
        assert removed == (['conversation'] if change else [])
        assert len(calls) == (4 if change else 2)
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
