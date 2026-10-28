"""Server-side application configuration for API provider settings.

Sensitive values such as API keys are stored on the server, not in browser
localStorage or chat request bodies.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

from fs_utils import atomic_replace

CONFIG_DIR = Path.home() / ".virtus"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = Path(os.getenv("VIRTUS_CONFIG_FILE", str(CONFIG_DIR / "config.json")))

DEFAULT_API_BASE = "https://api.openai.com/v1"

_ALLOWED_KEYS = {"api_base", "api_key"}

# Cache provider settings between requests while picking up manual file edits.
_CONFIG_TTL = float(os.getenv("VIRTUS_CONFIG_CACHE_TTL", "5"))
_config_cache: dict | None = None
_config_cache_at: float = 0.0
_config_cache_env: tuple[str, str, str] | None = None  # snapshot of env vars at cache time
_config_cache_lock = threading.RLock()
_config_cache_path: Path | None = None


def _env_snapshot() -> tuple[str, str, str]:
    """Capture the three env vars that override file-based config."""
    return (
        os.getenv("OPENAI_API_KEY", ""),
        os.getenv("OPENAI_BASE_URL", ""),
        os.getenv("OPENAI_API_BASE", ""),
    )


def _invalidate_cache() -> None:
    global _config_cache, _config_cache_at, _config_cache_env
    with _config_cache_lock:
        _config_cache = None
        _config_cache_at = 0.0
        _config_cache_env = None


def _read_config() -> dict:
    try:
        loaded = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            return {k: v for k, v in loaded.items() if k in _ALLOWED_KEYS}
    except (OSError, UnicodeError, json.JSONDecodeError):
        pass
    return {}


def load_config() -> dict:
    """Load an independent settings snapshot with environment overrides."""
    global _config_cache, _config_cache_at, _config_cache_env, _config_cache_path
    now = time.monotonic()
    current_env = _env_snapshot()
    with _config_cache_lock:
        if (_config_cache is None or now - _config_cache_at >= _CONFIG_TTL
                or _config_cache_env != current_env or _config_cache_path != CONFIG_FILE):
            data = _read_config()
            if current_env[0]:
                data["api_key"] = current_env[0]
            if env_base := current_env[1] or current_env[2]:
                data["api_base"] = env_base
            data.setdefault("api_base", DEFAULT_API_BASE)
            data.setdefault("api_key", "")
            _config_cache = data
            _config_cache_at = time.monotonic()
            _config_cache_env = current_env
            _config_cache_path = CONFIG_FILE
        return dict(_config_cache)


def public_config() -> dict:
    """Return non-sensitive config metadata safe for the browser."""
    cfg = load_config()
    return {
        "api_base": cfg.get("api_base") or DEFAULT_API_BASE,
        "has_api_key": bool(cfg.get("api_key")),
    }


def save_config(update: dict) -> dict:
    """Persist allowed server-side settings atomically.

    An empty API key means "leave the existing saved key unchanged" so the UI
    can save the base URL without forcing users to re-enter their key.
    """
    if not isinstance(update, dict):
        raise ValueError("Config update must be a JSON object")

    with _config_cache_lock:
        current = _read_config()
        if "api_base" in update:
            current["api_base"] = str(update.get("api_base") or DEFAULT_API_BASE).strip() or DEFAULT_API_BASE
        if "api_key" in update and str(update.get("api_key") or "").strip():
            current["api_key"] = str(update.get("api_key") or "").strip()

        current.setdefault("api_base", DEFAULT_API_BASE)
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = CONFIG_FILE.with_suffix(f".tmp-{uuid.uuid4().hex}")
        tmp_path.write_text(json.dumps(current, indent=2), encoding="utf-8")
        atomic_replace(tmp_path, CONFIG_FILE)
        _invalidate_cache()
        return public_config()
