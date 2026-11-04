"""Shared host-side probes and resource checks for both desktop launchers."""
from __future__ import annotations

import json
import ipaddress
import errno
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import socket
import tempfile
import threading
import time
import urllib.request
from collections.abc import Callable

import build_info
from fs_utils import atomic_replace


def run_smoke_test(validate_resources: Callable[[], None]) -> int:
    """Check the packaged server without desktop state or Docker access.

    Use an OS-assigned loopback port and a private Waitress socket map, never
    the running app's listener, instance lock, logs, or LAN preferences.
    """
    if not build_info.is_desktop_smoke_test():
        raise RuntimeError("Desktop smoke-test mode is required.")

    from waitress import create_server
    import app

    server = None
    worker = None
    stopping = threading.Event()
    try:
        validate_resources()
        server = create_server(app.create_app(), host="127.0.0.1", port=0,
                               threads=1, map={})

        def serve() -> None:
            try:
                server.run()
            except OSError as exc:
                if not (stopping.is_set() and exc.errno == errno.EBADF):
                    raise

        worker = threading.Thread(target=serve, name="virtus-package-smoke", daemon=True)
        worker.start()
        base_url = f"http://127.0.0.1:{server.effective_port}"
        if not wait_for_health(base_url, timeout=5):
            raise RuntimeError("The packaged server did not become ready.")
        payload = health_payload(base_url)
        if not payload or payload.get("version") != build_info.APP_VERSION:
            raise RuntimeError("The packaged server returned the wrong version.")
        return 0
    except Exception:
        logging.exception("Desktop package smoke test failed")
        return 1
    finally:
        stopping.set()
        if server is not None:
            server.close()
            server.task_dispatcher.shutdown(timeout=5)
        if worker is not None:
            worker.join(timeout=5)


def load_lan_access(path: Path) -> bool:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("lan_access") is True
    except (OSError, ValueError, AttributeError):
        return False


def save_lan_access(path: Path, enabled: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        json.dump({"lan_access": enabled}, handle)
    try:
        atomic_replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def lan_addresses() -> list[str]:
    """Find local IPv4 addresses without sending network traffic."""
    addresses = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            addresses.add(probe.getsockname()[0])
    except OSError:
        pass
    try:
        addresses.update(info[4][0] for info in socket.getaddrinfo(
            socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM
        ))
    except OSError:
        pass
    return sorted(address for address in addresses
                  if not ipaddress.ip_address(address).is_loopback
                  and not ipaddress.ip_address(address).is_unspecified)


def configure_logging(log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for previous in root.handlers[:]:
        root.removeHandler(previous)
        previous.close()
    root.addHandler(handler)


def health_payload(base_url: str, timeout: float = 1.0) -> dict | None:
    try:
        with urllib.request.urlopen(f"{base_url}/health", timeout=timeout) as response:
            payload = json.loads(response.read(4096).decode("utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def wait_for_health(base_url: str, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while (remaining := deadline - time.monotonic()) > 0:
        payload = health_payload(base_url, timeout=min(1.0, remaining))
        if payload and payload.get("app") == build_info.APP_ID:
            return True
        time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
    return False


def port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def validate_frozen_runtime(*extra_resources: Path) -> None:
    """Import lazy dependencies and verify the complete Docker build context."""
    from mcp import ClientSession, StdioServerParameters  # noqa: F401
    from mcp.client.stdio import stdio_client  # noqa: F401
    from openai import OpenAI  # noqa: F401

    root = build_info.resource_root()
    required = [root / "templates" / "index.html", root / "static" / "js" / "app.js", *extra_resources]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(f"Frozen application resources are missing: {', '.join(missing)}")
    build_info.sandbox_identity(root)
