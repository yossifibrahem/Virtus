from __future__ import annotations

import errno
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

pytest.importorskip("rumps")

import desktop_launcher


def _bare_menu():
    return object.__new__(desktop_launcher.VirtusMenuBar)


def test_desktop_lock_defaults_to_virtus_data_directory():
    assert desktop_launcher.LOCK_FILE == Path.home() / ".virtus" / "desktop.lock"


def test_menu_bar_uses_bundled_virtus_artwork():
    menu = desktop_launcher.VirtusMenuBar(object())
    assert desktop_launcher.MENU_BAR_ICON.is_file()
    assert menu.icon == str(desktop_launcher.MENU_BAR_ICON)
    assert menu.title is None


def test_port_collision_with_virtus_reopens_existing_app(monkeypatch):
    opened = []
    monkeypatch.setattr(desktop_launcher, "_port_available", lambda: False)
    monkeypatch.setattr(
        desktop_launcher,
        "_health_payload",
        lambda: {"app": desktop_launcher.build_info.APP_ID},
    )
    monkeypatch.setattr(desktop_launcher.webbrowser, "open", opened.append)

    with pytest.raises(SystemExit) as stopped:
        _bare_menu().start_server()

    assert stopped.value.code == 0
    assert opened == [desktop_launcher.BASE_URL]


def test_port_collision_with_unrelated_service_is_actionable(monkeypatch):
    monkeypatch.setattr(desktop_launcher, "_port_available", lambda: False)
    monkeypatch.setattr(desktop_launcher, "_health_payload", lambda: {"app": "other"})

    with pytest.raises(RuntimeError, match="already in use by another application"):
        _bare_menu().start_server()


def test_stop_server_closes_waitress_and_joins_thread():
    calls = []
    menu = _bare_menu()
    menu._server_stopping = threading.Event()
    menu._server = SimpleNamespace(close=lambda: calls.append("close"))
    menu._server_thread = SimpleNamespace(join=lambda timeout: calls.append(("join", timeout)))

    menu.stop_server()

    assert calls == ["close", ("join", 5)]


def test_expected_waitress_close_race_is_suppressed():
    menu = _bare_menu()
    menu._server_stopping = threading.Event()
    menu._server_stopping.set()
    menu._server = SimpleNamespace(
        run=lambda: (_ for _ in ()).throw(OSError(errno.EBADF, "closed"))
    )

    menu._run_server()


def test_smoke_mode_starts_then_stops_without_entering_menu_loop(monkeypatch):
    events = []

    class FakeInstance:
        def acquire(self):
            events.append("lock")
            return True

        def close(self):
            events.append("unlock")

    class FakeMenu:
        def __init__(self, instance):
            assert isinstance(instance, FakeInstance)

        def start_server(self):
            events.append("start")

        def stop_server(self):
            events.append("stop")

        def run(self):
            raise AssertionError("menu loop must not run during a smoke test")

    monkeypatch.setenv("VIRTUS_DESKTOP_SMOKE_TEST", "1")
    monkeypatch.setattr(desktop_launcher, "configure_logging", lambda: None)
    monkeypatch.setattr(
        desktop_launcher,
        "_validate_frozen_runtime",
        lambda: events.append("validate"),
    )
    monkeypatch.setattr(desktop_launcher, "SingleInstance", FakeInstance)
    monkeypatch.setattr(desktop_launcher, "VirtusMenuBar", FakeMenu)

    assert desktop_launcher.main() == 0
    assert events == ["lock", "start", "validate", "stop", "unlock"]


def test_frozen_runtime_validation_finds_bundled_assets():
    desktop_launcher._validate_frozen_runtime()
