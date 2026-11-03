from __future__ import annotations

import errno
import sys
import threading
from types import SimpleNamespace

import pytest

if sys.platform != "darwin":
    pytest.skip("macOS desktop host only", allow_module_level=True)

pytest.importorskip("rumps")

import desktop_launcher


def _bare_menu():
    return object.__new__(desktop_launcher.VirtusMenuBar)


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
    menu._server_thread = SimpleNamespace(
        join=lambda timeout: calls.append(("join", timeout)), is_alive=lambda: False,
    )

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


def test_network_toggle_persists_and_preserves_flask_app(monkeypatch, tmp_path):
    path = tmp_path / "network.json"
    monkeypatch.setattr(desktop_launcher, "NETWORK_SETTINGS_FILE", path)
    menu = _bare_menu()
    menu._lan_access = False
    menu._lan_menu = SimpleNamespace(state=0)
    menu._flask_app = object()
    original_app = menu._flask_app
    events = []
    menu.stop_server = lambda: events.append("stop")
    menu._start_listener = lambda: events.append(menu._lan_access)
    menu.show_network_address = lambda: events.append("address")
    menu.toggle_lan_access()
    assert desktop_launcher.desktop_runtime.load_lan_access(path)
    assert menu._lan_menu.state == 1
    menu.toggle_lan_access()
    assert not desktop_launcher.desktop_runtime.load_lan_access(path)
    assert menu._lan_menu.state == 0
    assert menu._flask_app is original_app
    assert events == ["stop", True, "address", "stop", False]


def test_network_toggle_rolls_back_when_binding_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop_launcher, "NETWORK_SETTINGS_FILE", tmp_path / "network.json")
    alerts = []
    monkeypatch.setattr(desktop_launcher.rumps, "alert", lambda **kw: alerts.append(kw))
    menu = _bare_menu()
    menu._lan_access = False
    menu._lan_menu = SimpleNamespace(state=0)
    menu.stop_server = lambda: None
    attempts = []

    def start():
        attempts.append(menu._lan_access)
        if menu._lan_access:
            raise OSError("Address already in use")

    menu._start_listener = start
    menu.toggle_lan_access()
    assert attempts == [True, False]
    assert not menu._lan_access
    assert menu._lan_menu.state == 0
    assert "Address already in use" in alerts[0]["message"]


def test_listener_uses_selected_interface_and_limits_allowed_hosts(monkeypatch):
    menu = _bare_menu()
    menu._flask_app = SimpleNamespace(config={})
    menu._server_stopping = threading.Event()
    bindings = []
    monkeypatch.setattr(desktop_launcher, "create_server", lambda app, **kw: bindings.append(kw))
    monkeypatch.setattr(desktop_launcher.desktop_runtime, "lan_addresses", lambda: ["192.168.1.5"])
    monkeypatch.setattr(desktop_launcher, "_wait_for_health", lambda: True)
    monkeypatch.setattr(desktop_launcher.threading, "Thread", lambda **kw: SimpleNamespace(start=lambda: None))
    for enabled, host, allowed in [(True, "0.0.0.0", ["192.168.1.5"]), (False, "127.0.0.1", [])]:
        menu._lan_access = enabled
        menu._start_listener()
        assert bindings[-1]["host"] == host
        assert menu._flask_app.config["VIRTUS_LAN_HOSTS"] == allowed
