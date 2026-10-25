"""macOS menu-bar launcher for the frozen Lumen web application."""
from __future__ import annotations

import fcntl
import errno
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
import webbrowser

import rumps
from waitress import create_server

import app as lumen_app
import build_info
import runtime_requirements


HOST = "127.0.0.1"
PORT = build_info.DESKTOP_PORT
BASE_URL = f"http://{HOST}:{PORT}"
LUMEN_DIR = Path.home() / ".lumen"
LOG_DIR = LUMEN_DIR / "logs"
LOG_FILE = LOG_DIR / "lumen.log"
LOCK_FILE = Path(
    os.getenv("LUMEN_DESKTOP_LOCK_FILE", str(LUMEN_DIR / "desktop.lock"))
)
MENU_BAR_ICON = build_info.resource_root() / "static" / "favicon.svg"


def configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(handler)


def _health_payload(timeout: float = 1.0) -> dict | None:
    try:
        with urllib.request.urlopen(f"{BASE_URL}/health", timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None
    return payload if isinstance(payload, dict) else None


def _wait_for_health(timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = _health_payload()
        if payload and payload.get("app") == build_info.APP_ID:
            return True
        time.sleep(0.25)
    return False


def _port_available() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((HOST, PORT))
        except OSError:
            return False
    return True


def _validate_frozen_runtime() -> None:
    """Import lazy critical dependencies and verify bundled web resources."""
    from mcp import ClientSession, StdioServerParameters  # noqa: F401
    from mcp.client.stdio import stdio_client  # noqa: F401
    from openai import OpenAI  # noqa: F401

    root = build_info.resource_root()
    required = [
        root / "templates" / "index.html",
        root / "static" / "js" / "app.js",
        MENU_BAR_ICON,
        root / "Dockerfile.sandbox",
        root / ".dockerignore",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(f"Frozen application resources are missing: {', '.join(missing)}")


class SingleInstance:
    def __init__(self) -> None:
        LUMEN_DIR.mkdir(parents=True, exist_ok=True)
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


class LumenMenuBar(rumps.App):
    def __init__(self, instance_lock: SingleInstance) -> None:
        super().__init__(
            "Lumen AI Chat",
            icon=str(MENU_BAR_ICON),
            template=False,
            quit_button=None,
        )
        self._instance_lock = instance_lock
        self._server = None
        self._server_thread: threading.Thread | None = None
        self._server_stopping = threading.Event()
        self.menu = [
            rumps.MenuItem("Open Lumen", callback=self.open_lumen),
            rumps.MenuItem("Docker Status", callback=self.docker_status),
            rumps.MenuItem("Open Logs", callback=self.open_logs),
            None,
            rumps.MenuItem("Quit Lumen", callback=self.quit_lumen),
        ]

    def start_server(self) -> None:
        if not _port_available():
            payload = _health_payload()
            if payload and payload.get("app") == build_info.APP_ID:
                webbrowser.open(BASE_URL)
                raise SystemExit(0)
            raise RuntimeError(
                f"Port {PORT} is already in use by another application. "
                "Close it or set LUMEN_DESKTOP_PORT to a stable free port."
            )

        flask_app = lumen_app.create_app()
        self._server = create_server(flask_app, host=HOST, port=PORT, threads=8)
        self._server_stopping.clear()
        self._server_thread = threading.Thread(
            target=self._run_server,
            name="lumen-waitress",
            daemon=True,
        )
        self._server_thread.start()
        if not _wait_for_health():
            raise RuntimeError("The local Lumen server did not become ready within 30 seconds.")
        if os.getenv("LUMEN_DESKTOP_NO_BROWSER", "") != "1":
            webbrowser.open(BASE_URL)

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
            dispatcher = getattr(self._server, "task_dispatcher", None)
            if dispatcher is not None:
                dispatcher.shutdown(timeout=5)
            self._server.close()
        if self._server_thread is not None:
            self._server_thread.join(timeout=5)

    def open_lumen(self, _sender=None) -> None:
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

    def quit_lumen(self, _sender=None) -> None:
        try:
            self.stop_server()
            lumen_app._shutdown_containers()
        finally:
            self._instance_lock.close()
            rumps.quit_application()


def main() -> int:
    configure_logging()
    instance = SingleInstance()
    if not instance.acquire():
        if _wait_for_health(timeout=5):
            webbrowser.open(BASE_URL)
            return 0
        rumps.alert(
            title="Lumen is starting",
            message="Another Lumen process is already running. Try again in a moment.",
        )
        return 1

    menu_app = LumenMenuBar(instance)
    try:
        menu_app.start_server()
        if os.getenv("LUMEN_DESKTOP_SMOKE_TEST", "") == "1":
            _validate_frozen_runtime()
            menu_app.stop_server()
            instance.close()
            return 0
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
            title="Lumen could not start",
            message=str(exc),
            ok="Close",
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
