from __future__ import annotations

import errno
import sys
import threading
from types import SimpleNamespace

import pytest

if sys.platform != "win32":
    pytest.skip("Windows desktop host only", allow_module_level=True)

pytest.importorskip("pystray")

import windows_desktop_launcher as launcher


def _bare_server():
    return object.__new__(launcher.DesktopServer)


def test_port_collision_with_virtus_reopens_existing_app(monkeypatch):
    opened = []
    monkeypatch.setattr(launcher, "_port_available", lambda: False)
    monkeypatch.setattr(launcher, "_health_payload", lambda: {"app": launcher.build_info.APP_ID})
    monkeypatch.setattr(launcher.webbrowser, "open", opened.append)

    with pytest.raises(SystemExit) as stopped:
        _bare_server().start_server()

    assert stopped.value.code == 0
    assert opened == [launcher.BASE_URL]


def test_expected_waitress_close_race_is_suppressed():
    server = _bare_server()
    server._server_stopping = threading.Event()
    server._server_stopping.set()
    server._server = SimpleNamespace(
        run=lambda: (_ for _ in ()).throw(OSError(errno.EBADF, "closed"))
    )

    server._run_server()


def test_server_closes_listener_before_waiting_for_workers():
    events = []
    server = _bare_server()
    server._server_stopping = threading.Event()
    server._server_thread = None
    server._server = SimpleNamespace(
        close=lambda: events.append("close-listener"),
        task_dispatcher=SimpleNamespace(
            shutdown=lambda timeout: events.append("stop-workers")
        ),
    )

    server.stop_server()

    assert events == ["close-listener", "stop-workers"]


def test_tray_remains_visible_until_cleanup_finishes(monkeypatch):
    events = []
    cleanup_started = threading.Event()
    release_cleanup = threading.Event()

    class FakeIcon:
        visible = True

        def stop(self):
            events.append("stop-icon")

    tray = object.__new__(launcher.VirtusTray)
    tray._icon = FakeIcon()
    tray._instance_lock = SimpleNamespace(close=lambda: events.append("unlock"))

    def stop_server(*, deadline):
        events.append("stop-server")
        cleanup_started.set()
        assert release_cleanup.wait(timeout=2)

    tray.stop_server = stop_server
    monkeypatch.setattr(
        launcher.virtus_app,
        "_shutdown_containers",
        lambda *, deadline: events.append("stop-containers"),
    )

    worker = threading.Thread(target=tray._finish_shutdown, args=(launcher.time.monotonic() + 2,))
    worker.start()
    assert cleanup_started.wait(timeout=1)
    assert tray._icon.visible
    assert "stop-icon" not in events
    release_cleanup.set()
    worker.join(timeout=2)

    assert events == ["stop-server", "stop-containers", "unlock", "stop-icon"]


def test_smoke_mode_bypasses_desktop_state(monkeypatch):
    events = []
    def forbidden(*args, **kwargs):
        raise AssertionError("Smoke mode must not initialize desktop state")
    monkeypatch.setenv("VIRTUS_DESKTOP_SMOKE_TEST", "1")
    monkeypatch.setattr(launcher, "configure_logging", forbidden)
    monkeypatch.setattr(launcher, "SingleInstance", forbidden)
    monkeypatch.setattr(launcher, "VirtusTray", forbidden)
    def smoke(validate):
        assert validate is launcher._validate_frozen_runtime
        events.append("smoke")
        return 0
    monkeypatch.setattr(launcher.desktop_runtime, "run_smoke_test", smoke)
    assert launcher.main() == 0
    assert events == ["smoke"]
