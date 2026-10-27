"""Shared host-side probes and resource checks for both desktop launchers."""
from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import socket
import time
import urllib.request

import build_info


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
