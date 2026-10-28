from __future__ import annotations

import errno
import sys
import threading
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows desktop host only")
pytest.importorskip("pystray")

import windows_desktop_launcher as launcher


def _bare_server():
    return object.__new__(launcher.DesktopServer)


def test_named_mutex_is_exclusive_and_reusable():
    first = launcher.SingleInstance()
    second = launcher.SingleInstance()
    third = launcher.SingleInstance()
    try:
        assert first.acquire()
        assert not second.acquire()
        first.close()
        assert third.acquire()
    finally:
        third.close()
        second.close()
        first.close()


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


def test_smoke_mode_uses_windows_host_without_tray_loop(monkeypatch):
    events = []

    class FakeInstance:
        def acquire(self):
            events.append("lock")
            return True

        def close(self):
            events.append("unlock")

    class FakeTray:
        _shutdown_thread = None

        def __init__(self, instance):
            assert isinstance(instance, FakeInstance)

        def start_server(self):
            events.append("start")

        def stop_server(self):
            events.append("stop")

        def run(self):
            raise AssertionError("tray loop must not run in smoke mode")

    monkeypatch.setenv("VIRTUS_DESKTOP_SMOKE_TEST", "1")
    monkeypatch.setattr(launcher, "configure_logging", lambda: None)
    monkeypatch.setattr(launcher, "SingleInstance", FakeInstance)
    monkeypatch.setattr(launcher, "VirtusTray", FakeTray)
    monkeypatch.setattr(launcher, "_validate_frozen_runtime", lambda: events.append("validate"))
    monkeypatch.setattr(launcher.virtus_app, "_shutdown_done", False)

    assert launcher.main() == 0
    assert events == ["lock", "start", "validate", "stop", "unlock"]
