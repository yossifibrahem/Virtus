"""Windows tray launcher for the frozen Lumen web application."""
from __future__ import annotations

import ctypes
import errno
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

import pystray
from PIL import Image, ImageDraw
from waitress import create_server

import app as lumen_app
import build_info
import runtime_requirements


HOST = "127.0.0.1"
PORT = build_info.DESKTOP_PORT
BASE_URL = f"http://{HOST}:{PORT}"
LUMEN_DIR = Path(os.getenv("LUMEN_DESKTOP_DATA_DIR", str(Path.home() / ".lumen")))
LOG_DIR = LUMEN_DIR / "logs"
LOG_FILE = LOG_DIR / "lumen.log"
WINDOWS_ICON = build_info.resource_root() / "Lumen.ico"
MUTEX_NAME = r"Local\com.lumen.chat.desktop"
QUIT_DEADLINE_SECONDS = 15.0

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
_kernel32.CreateMutexW.restype = ctypes.c_void_p
_kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
_kernel32.CloseHandle.restype = ctypes.c_bool


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
    """Import lazy dependencies and verify the complete Docker build context."""
    from mcp import ClientSession, StdioServerParameters  # noqa: F401
    from mcp.client.stdio import stdio_client  # noqa: F401
    from openai import OpenAI  # noqa: F401

    root = build_info.resource_root()
    required = [
        root / "templates" / "index.html",
        root / "static" / "js" / "app.js",
        root / "Dockerfile.sandbox",
        root / ".dockerignore",
    ]
    if build_info.is_frozen():
        required.append(WINDOWS_ICON)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(f"Frozen application resources are missing: {', '.join(missing)}")
    build_info.sandbox_identity(root)


class SingleInstance:
    """Use a kernel mutex that is released automatically if the process dies."""

    def __init__(self) -> None:
        self._handle = None

    def acquire(self) -> bool:
        if self._handle is not None:
            return True
        ctypes.set_last_error(0)
        handle = _kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            _kernel32.CloseHandle(handle)
            return False
        self._handle = handle
        return True

    def close(self) -> None:
        if self._handle is not None:
            _kernel32.CloseHandle(self._handle)
            self._handle = None


class DesktopServer:
    def __init__(self, instance_lock: SingleInstance) -> None:
        self._instance_lock = instance_lock
        self._server = None
        self._server_thread: threading.Thread | None = None
        self._server_stopping = threading.Event()

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

    def stop_server(self, *, deadline: float | None = None) -> None:
        stop_deadline = deadline or (time.monotonic() + 5.0)
        if self._server is not None:
            self._server_stopping.set()
            self._server.close()
            dispatcher = getattr(self._server, "task_dispatcher", None)
            if dispatcher is not None:
                dispatcher.shutdown(timeout=max(0.0, min(5.0, stop_deadline - time.monotonic())))
        if self._server_thread is not None:
            self._server_thread.join(timeout=max(0.0, stop_deadline - time.monotonic()))

    def open_lumen(self, *_args) -> None:
        webbrowser.open(BASE_URL)


def _windows_icon_image() -> Image.Image:
    if WINDOWS_ICON.is_file():
        with Image.open(WINDOWS_ICON) as image:
            return image.convert("RGBA")
    image = Image.new("RGBA", (64, 64), "#171713")
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 10, 54, 54), fill="#fff3d6", outline="#120f0a", width=4)
    draw.rectangle((20, 24, 28, 32), fill="#d96c4b")
    draw.rectangle((36, 24, 44, 32), fill="#d96c4b")
    draw.rectangle((24, 42, 40, 46), fill="#120f0a")
    draw.rectangle((28, 4, 36, 12), fill="#d96c4b")
    return image


def _windows_alert(title: str, message: str) -> None:
    ctypes.windll.user32.MessageBoxW(None, message, title, 0x00000040)


def _force_exit(code: int) -> None:
    os._exit(code)


class LumenTray(DesktopServer):
    def __init__(self, instance_lock: SingleInstance) -> None:
        super().__init__(instance_lock)
        self._quit_requested = threading.Event()
        self._process_exit_ready = threading.Event()
        self._shutdown_thread: threading.Thread | None = None
        self._icon = pystray.Icon(
            "lumen-ai-chat",
            _windows_icon_image(),
            "Lumen AI Chat",
            menu=pystray.Menu(
                pystray.MenuItem("Open Lumen", self.open_lumen, default=True),
                pystray.MenuItem("Docker Status", self.docker_status),
                pystray.MenuItem("Open Logs", self.open_logs),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit Lumen", self.quit_lumen),
            ),
        )

    def run(self) -> None:
        try:
            self._icon.run()
        finally:
            self._process_exit_ready.set()

    def docker_status(self, *_args) -> None:
        status = runtime_requirements.check_requirements()
        _windows_alert(status.title, status.message)

    def open_logs(self, *_args) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(LOG_DIR)  # type: ignore[attr-defined]

    def _watch_shutdown(self, deadline: float) -> None:
        remaining = max(0.0, deadline - time.monotonic())
        if self._process_exit_ready.wait(timeout=remaining):
            return
        logging.critical("desktop shutdown exceeded %.0f-second deadline", QUIT_DEADLINE_SECONDS)
        logging.shutdown()
        _force_exit(1)

    def _finish_shutdown(self, deadline: float) -> None:
        try:
            logging.info("desktop shutdown: stopping local server")
            self.stop_server(deadline=deadline)
            logging.info("desktop shutdown: closing MCP sessions and containers")
            lumen_app._shutdown_containers(deadline=deadline)
        except Exception:
            logging.exception("desktop shutdown cleanup failed")
        finally:
            try:
                logging.info("desktop shutdown: releasing instance mutex")
                self._instance_lock.close()
            except Exception:
                logging.exception("desktop instance mutex cleanup failed")
            logging.info("desktop shutdown: cleanup complete; stopping tray")
            self._icon.stop()

    def quit_lumen(self, *_args) -> None:
        if self._quit_requested.is_set():
            return
        self._quit_requested.set()
        deadline = time.monotonic() + QUIT_DEADLINE_SECONDS
        threading.Thread(
            target=self._watch_shutdown,
            args=(deadline,),
            name="lumen-shutdown-watchdog",
            daemon=True,
        ).start()
        self._shutdown_thread = threading.Thread(
            target=self._finish_shutdown,
            args=(deadline,),
            name="lumen-windows-shutdown",
            daemon=True,
        )
        self._shutdown_thread.start()


def main() -> int:
    configure_logging()
    instance = SingleInstance()
    if not instance.acquire():
        if _wait_for_health(timeout=5):
            webbrowser.open(BASE_URL)
            return 0
        _windows_alert(
            "Lumen is starting",
            "Another Lumen process is already running. Try again in a moment.",
        )
        return 1

    tray = None
    try:
        tray = LumenTray(instance)
        tray.start_server()
        if os.getenv("LUMEN_DESKTOP_SMOKE_TEST", "") == "1":
            _validate_frozen_runtime()
            tray.stop_server()
            instance.close()
            lumen_app._shutdown_done = True
            return 0
        tray.run()
        if tray._shutdown_thread is not None:
            tray._shutdown_thread.join(timeout=1)
        return 0
    except SystemExit:
        instance.close()
        return 0
    except Exception as exc:
        logging.exception("desktop startup failed")
        if tray is not None:
            try:
                tray.stop_server()
            except Exception:
                logging.exception("desktop server cleanup failed")
        instance.close()
        _windows_alert("Lumen could not start", str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
