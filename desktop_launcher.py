"""macOS menu-bar launcher for the frozen Virtus web application."""
from __future__ import annotations

import fcntl
import errno
from functools import partial
import logging
import os
from pathlib import Path
import subprocess
import threading
import webbrowser

import rumps
from waitress import create_server

import app as virtus_app
import build_info
import desktop_runtime
import runtime_requirements


HOST = "127.0.0.1"
PORT = build_info.DESKTOP_PORT
BASE_URL = f"http://{HOST}:{PORT}"
VIRTUS_DIR = Path.home() / ".virtus"
LOG_DIR = VIRTUS_DIR / "logs"
LOG_FILE = LOG_DIR / "virtus.log"
LOCK_FILE = Path(
    os.getenv("VIRTUS_DESKTOP_LOCK_FILE", str(VIRTUS_DIR / "desktop.lock"))
)
NETWORK_SETTINGS_FILE = VIRTUS_DIR / "desktop-network.json"
MENU_BAR_ICON = build_info.resource_root() / "static" / "virtus-logo-dark.png"


configure_logging = partial(desktop_runtime.configure_logging, LOG_FILE)
_health_payload = partial(desktop_runtime.health_payload, BASE_URL)
_wait_for_health = partial(desktop_runtime.wait_for_health, BASE_URL)
_port_available = partial(desktop_runtime.port_available, HOST, PORT)


def _validate_frozen_runtime() -> None:
    desktop_runtime.validate_frozen_runtime(MENU_BAR_ICON)


class SingleInstance:
    def __init__(self) -> None:
        VIRTUS_DIR.mkdir(parents=True, exist_ok=True)
        self._handle = LOCK_FILE.open("a+")

    def acquire(self) -> bool:
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._handle.seek(0)
            self._handle.truncate()
            self._handle.write(str(os.getpid()))
            self._handle.flush()
            return True
        except BlockingIOError:
            return False

    def close(self) -> None:
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()


class VirtusMenuBar(rumps.App):
    def __init__(self, instance_lock: SingleInstance) -> None:
        super().__init__(
            "Virtus AI Chat",
            icon=str(MENU_BAR_ICON),
            template=False,
            quit_button=None,
        )
        self._instance_lock = instance_lock
        self._server = None
        self._server_thread: threading.Thread | None = None
        self._server_stopping = threading.Event()
        self._flask_app = None
        self._lan_access = desktop_runtime.load_lan_access(NETWORK_SETTINGS_FILE)
        self._lan_menu = rumps.MenuItem("Allow local network access", callback=self.toggle_lan_access)
        self._lan_menu.state = int(self._lan_access)
        self.menu = [
            rumps.MenuItem("Open Virtus", callback=self.open_virtus),
            self._lan_menu,
            rumps.MenuItem("Show network address", callback=self.show_network_address),
            rumps.MenuItem("Docker Status", callback=self.docker_status),
            rumps.MenuItem("Open Logs", callback=self.open_logs),
            None,
            rumps.MenuItem("Quit Virtus", callback=self.quit_virtus),
        ]

    def start_server(self) -> None:
        lan_access = getattr(self, "_lan_access", False)
        available = (desktop_runtime.port_available("0.0.0.0", PORT)
                     if lan_access else _port_available())
        if not available:
            payload = _health_payload()
            if payload and payload.get("app") == build_info.APP_ID:
                webbrowser.open(BASE_URL)
                raise SystemExit(0)
            raise RuntimeError(
                f"Port {PORT} is already in use by another application. "
                "Close it or set VIRTUS_DESKTOP_PORT to a stable free port."
            )

        self._flask_app = virtus_app.create_app()
        self._start_listener()
        if os.getenv("VIRTUS_DESKTOP_NO_BROWSER", "") != "1":
            webbrowser.open(BASE_URL)

    def _start_listener(self) -> None:
        self._flask_app.config["VIRTUS_LAN_HOSTS"] = (
            desktop_runtime.lan_addresses() if self._lan_access else []
        )
        self._server = create_server(
            self._flask_app, host="0.0.0.0" if self._lan_access else HOST,
            port=PORT, threads=8,
        )
        self._server_stopping.clear()
        self._server_thread = threading.Thread(
            target=self._run_server,
            name="virtus-waitress",
            daemon=True,
        )
        self._server_thread.start()
        if not _wait_for_health():
            raise RuntimeError("The local Virtus server did not become ready within 30 seconds.")

    def toggle_lan_access(self, _sender=None) -> None:
        previous = self._lan_access
        try:
            self.stop_server()
            self._lan_access = not previous
            self._start_listener()
            desktop_runtime.save_lan_access(NETWORK_SETTINGS_FILE, self._lan_access)
        except Exception as exc:
            logging.exception("Could not change local network access")
            try:
                self.stop_server()
                self._lan_access = previous
                self._start_listener()
            except Exception:
                logging.exception("Could not restore desktop listener")
                rumps.alert(title="Virtus needs to restart", message=str(exc))
                return
            rumps.alert(title="Could not change network access", message=str(exc))
            return
        self._lan_menu.state = int(self._lan_access)
        if self._lan_access:
            self.show_network_address()

    def show_network_address(self, _sender=None) -> None:
        if not self._lan_access:
            message = "Enable ‘Allow local network access’ in the Virtus menu first."
        else:
            addresses = desktop_runtime.lan_addresses()
            self._flask_app.config["VIRTUS_LAN_HOSTS"] = addresses
            urls = "\n".join(f"http://{address}:{PORT}" for address in addresses)
            message = (f"On another device on the same network, open:\n\n{urls}\n\n"
                       "Devices on this network can access your chats, files, and tools. "
                       "Keep your Mac awake."
                       if urls else "No network address found. Connect your Mac to Wi-Fi or Ethernet and try again.")
        rumps.alert(title="Local network access", message=message)

    def _run_server(self) -> None:
        try:
            self._server.run()
        except OSError as exc:
            if self._server_stopping.is_set() and exc.errno == errno.EBADF:
                return
            raise

    def stop_server(self) -> None:
        if self._server is not None:
            self._server_stopping.set()
            self._server.close()
            dispatcher = getattr(self._server, "task_dispatcher", None)
            if dispatcher is not None:
                dispatcher.shutdown(timeout=5)
        if self._server_thread is not None:
            self._server_thread.join(timeout=5)
            if self._server_thread.is_alive():
                raise RuntimeError("The previous server is still stopping. Try again in a moment.")

    def open_virtus(self, _sender=None) -> None:
        webbrowser.open(BASE_URL)

    def docker_status(self, _sender=None) -> None:
        status = runtime_requirements.check_requirements()
        rumps.alert(
            title=status.title,
            message=status.message,
            ok="OK",
        )

    def open_logs(self, _sender=None) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["/usr/bin/open", str(LOG_DIR)])

    def quit_virtus(self, _sender=None) -> None:
        try:
            self.stop_server()
            virtus_app._shutdown_containers()
        finally:
            self._instance_lock.close()
            rumps.quit_application()


def main() -> int:
    if build_info.is_desktop_smoke_test():
        return desktop_runtime.run_smoke_test(_validate_frozen_runtime)
    configure_logging()
    instance = SingleInstance()
    if not instance.acquire():
        if _wait_for_health(timeout=5):
            webbrowser.open(BASE_URL)
            return 0
        rumps.alert(
            title="Virtus is starting",
            message="Another Virtus process is already running. Try again in a moment.",
        )
        return 1

    menu_app = VirtusMenuBar(instance)
    try:
        menu_app.start_server()
        menu_app.run()
        return 0
    except SystemExit:
        instance.close()
        return 0
    except Exception as exc:
        logging.exception("desktop startup failed")
        try:
            menu_app.stop_server()
        except Exception:
            logging.exception("desktop server cleanup failed")
        instance.close()
        rumps.alert(
            title="Virtus could not start",
            message=str(exc),
            ok="Close",
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
