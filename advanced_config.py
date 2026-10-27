"""Server-side advanced configuration for container and file-handling settings.

Values are resolved in priority order:
  1. Environment variable (highest — never overwritten by the UI)
  2. ~/.lumen/advanced_config.json  (written by the UI)
  3. Hardcoded default              (lowest)

The public_advanced_config() helper marks every env-locked key so the browser
can disable the corresponding form field and show an informative hint.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
import uuid
from pathlib import Path

import build_info
from fs_utils import atomic_replace

CONFIG_DIR = Path.home() / ".lumen"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

ADVANCED_CONFIG_FILE = Path(
    os.getenv("LUMEN_ADVANCED_CONFIG_FILE", str(CONFIG_DIR / "advanced_config.json"))
)

_CACHE_TTL = float(os.getenv("LUMEN_CONFIG_CACHE_TTL", "5"))
_cache: dict | None = None
_cache_at: float = 0.0
_cache_lock = threading.RLock()
_cache_path: Path | None = None

# ── Allowed keys and their types ──────────────────────────────────────────────

_INT_KEYS: frozenset[str] = frozenset({
    "container_idle_timeout",
    "max_file_preview_bytes",
    "max_file_list_entries",
    "max_upload_bytes",
})

# Hardcoded defaults (lowest priority).
_HARDCODED_DEFAULTS: dict = {
    "sandbox_image":          build_info.DEFAULT_SANDBOX_IMAGE,
    "container_memory":       "512m",
    "container_cpus":         "1",
    "container_network":      "bridge",
    "container_idle_timeout": 600,
    "max_file_preview_bytes": 512 * 1024,
    "max_file_list_entries":  500,
    "max_upload_bytes":       50 * 1024 * 1024,
}

_ALLOWED_KEYS: frozenset[str] = frozenset(_HARDCODED_DEFAULTS.keys())

# Env-var names that map to each allowed key.
_ENV_NAMES: dict[str, str] = {
    "sandbox_image":          "LUMEN_SANDBOX_IMAGE",
    "container_memory":       "LUMEN_CONTAINER_MEMORY",
    "container_cpus":         "LUMEN_CONTAINER_CPUS",
    "container_network":      "LUMEN_CONTAINER_NETWORK",
    "container_idle_timeout": "LUMEN_CONTAINER_IDLE_TIMEOUT",
    "max_file_preview_bytes": "LUMEN_MAX_FILE_PREVIEW_BYTES",
    "max_file_list_entries":  "LUMEN_MAX_FILE_LIST_ENTRIES",
    "max_upload_bytes":       "LUMEN_MAX_UPLOAD_BYTES",
}

# Snapshot env values at import time so we know which keys are "locked" by
# the operator.  These are never overwritten by UI saves.
_ENV_LOCKED: dict[str, str] = {
    key: val
    for key, env_name in _ENV_NAMES.items()
    if (val := os.getenv(env_name)) is not None
}


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _invalidate_cache() -> None:
    global _cache, _cache_at
    with _cache_lock:
        _cache = None
        _cache_at = 0.0


def _cast(key: str, raw) -> int | str:
    if key in _INT_KEYS:
        value = int(raw)
        ceilings = {'max_file_preview_bytes': 4*1024*1024,
                    'max_file_list_entries': 5000, 'max_upload_bytes': 50*1024*1024}
        if isinstance(raw, bool) or value < (0 if key == 'container_idle_timeout' else 1):
            raise ValueError('Expected a positive integer (or zero for idle timeout)')
        return min(value, ceilings.get(key, value))
    value = str(raw).strip()
    if not value:
        raise ValueError('Value must not be empty')
    if key == 'container_network' and (value == 'host' or value.startswith('container:')):
        raise ValueError('Host/shared container network namespaces are not allowed')
    if key == 'container_cpus' and (not math.isfinite(float(value)) or float(value) <= 0):
        raise ValueError('CPU limit must be positive and finite')
    return value


# ── Public API ────────────────────────────────────────────────────────────────

def _read_config() -> dict:
    try:
        loaded = json.loads(ADVANCED_CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            return {key: value for key, value in loaded.items() if key in _ALLOWED_KEYS}
    except (OSError, UnicodeError, json.JSONDecodeError):
        pass
    return {}


def load_advanced_config() -> dict:
    """Return an independent cached snapshot with environment overrides."""
    global _cache, _cache_at, _cache_path
    now = time.monotonic()
    with _cache_lock:
        if _cache is None or now - _cache_at >= _CACHE_TTL or _cache_path != ADVANCED_CONFIG_FILE:
            data = dict(_HARDCODED_DEFAULTS)
            for layer in (_read_config(), _ENV_LOCKED):
                for key, raw in layer.items():
                    try:
                        data[key] = _cast(key, raw)
                    except (ValueError, TypeError, OverflowError):
                        pass
            _cache = data
            _cache_at = time.monotonic()
            _cache_path = ADVANCED_CONFIG_FILE
        return dict(_cache)


def public_advanced_config() -> dict:
    """Return config safe for the browser.

    Each key is accompanied by a ``<key>_env_locked`` boolean so the
    frontend can disable editing and show the provenance.
    """
    cfg = load_advanced_config()
    result: dict = {}
    for key in _ALLOWED_KEYS:
        result[key] = cfg[key]
        result[f"{key}_env_locked"] = key in _ENV_LOCKED
    return result


def save_advanced_config(update: dict) -> dict:
    """Persist non-env-locked keys atomically to advanced_config.json.

    Keys whose values are fixed by an environment variable are silently
    ignored — the operator-set value always takes precedence.
    """
    if not isinstance(update, dict):
        raise ValueError("Config update must be a JSON object")

    with _cache_lock:
        current = _read_config()
        for key in _ALLOWED_KEYS.intersection(update).difference(_ENV_LOCKED):
            try:
                current[key] = _cast(key, update[key])
            except (ValueError, TypeError, OverflowError):
                raise ValueError(f"Invalid value for '{key}': {update[key]!r}") from None

        ADVANCED_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = ADVANCED_CONFIG_FILE.with_suffix(f".tmp-{uuid.uuid4().hex}")
        tmp.write_text(json.dumps(current, indent=2), encoding="utf-8")
        atomic_replace(tmp, ADVANCED_CONFIG_FILE)
        _invalidate_cache()
        return public_advanced_config()
