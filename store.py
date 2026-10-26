"""
Conversation store — file-system CRUD.

All persistence is isolated here.  Nothing in this module imports Flask;
routes call these functions and decide what HTTP status to return.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fs_utils import atomic_replace
from mcp_adapters import conversation_working_directory

CONVERSATIONS_DIR = Path.home() / ".lumen" / "conversations"
CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)

FOLDERS_FILE = Path.home() / ".lumen" / "folders.json"

IMAGES_DIR = Path.home() / ".lumen" / "images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)


_SAFE_IMAGE_EXTENSIONS = {'png', 'jpeg', 'webp', 'gif'}
_SAFE_NAME = re.compile(r'^[a-f0-9]{64}\.(png|jpeg|webp|gif)$')

_index: list[dict] | None = None
_index_lock = threading.RLock()
_folders_lock = threading.RLock()


def _conversation_summary(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
        return {
            "id":                path.stem,
            "title":             data.get("title", "Untitled"),
            "folder_id":         data.get("folder_id"),
            "updated_at":        data.get("updated_at") or data.get("created_at"),
            "working_directory": str(conversation_working_directory(runtime_id(path.stem, data))),
        }
    except Exception:
        return None


def invalidate_index() -> None:
    global _index
    with _index_lock:
        _index = None


def _build_index_summaries() -> list[dict]:
    summaries = []
    paths = sorted(CONVERSATIONS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in paths:
        summary = _conversation_summary(path)
        if summary:
            summaries.append(summary)
    return summaries


def _update_index_for(conv_id: str, data: dict) -> None:
    global _index
    with _index_lock:
        if _index is None:
            return
        summary = {
            "id": conv_id,
            "title": data.get("title", "Untitled"),
            "folder_id": data.get("folder_id"),
            "updated_at": data.get("updated_at") or data.get("created_at"),
            "working_directory": str(conversation_working_directory(runtime_id(conv_id, data))),
        }
        _index = [item for item in _index if item.get("id") != conv_id]
        _index.insert(0, summary)


def _remove_index_entry(conv_id: str) -> None:
    global _index
    with _index_lock:
        if _index is not None:
            _index = [item for item in _index if item.get("id") != conv_id]


# ── Image storage ─────────────────────────────────────────────────────────────

def save_image(data_b64: str, media_type: str) -> str:
    """Decode a supported image type, persist by SHA-256 hash, and return filename."""
    ext = (media_type.split("/")[-1].split(";")[0] or "png").lower()
    if ext == "jpg":
        ext = "jpeg"
    if ext not in _SAFE_IMAGE_EXTENSIONS:
        raise ValueError(f"Unsupported image type: {media_type or 'unknown'}")
    try:
        raw = base64.b64decode(data_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Invalid image data") from exc

    name = hashlib.sha256(raw).hexdigest() + "." + ext
    path = IMAGES_DIR / name
    if not path.exists():
        path.write_bytes(raw)
    return name


def get_image_path(name: str) -> Path | None:
    """Return the Path for a stored image, or None if it doesn't exist / is unsafe."""
    if not _SAFE_NAME.match(name):
        return None
    path = IMAGES_DIR / name
    return path if path.exists() else None


# ── Internal helpers ──────────────────────────────────────────────────────────

def _path(conv_id: str) -> Path:
    return CONVERSATIONS_DIR / f"{conv_id}.json"


def working_directory(conv_id: str) -> Path:
    """Return the MCP workspace for a chat (shared by chats in one folder)."""
    return conversation_working_directory(runtime_id(conv_id))


def runtime_id(conv_id: str, data: dict | None = None) -> str:
    """Return the container/workspace identity used by a conversation."""
    conversation = data if data is not None else load(conv_id)
    folder_id = conversation.get("folder_id") if conversation else None
    return f"folder_{folder_id}" if folder_id else conv_id


def _read_folders() -> list[dict]:
    if not FOLDERS_FILE.exists():
        return []
    try:
        data = json.loads(FOLDERS_FILE.read_text())
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _write_folders(folders: list[dict]) -> None:
    FOLDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = FOLDERS_FILE.with_suffix(f".tmp-{uuid.uuid4().hex}")
    tmp_path.write_text(json.dumps(folders, indent=2))
    atomic_replace(tmp_path, FOLDERS_FILE)


def list_folders() -> list[dict]:
    with _folders_lock:
        return list(_read_folders())


def get_folder(folder_id: str) -> dict | None:
    return next((folder for folder in list_folders() if folder.get("id") == folder_id), None)


def create_folder(name: str = "New Folder", system_prompt: str = "") -> dict:
    now = datetime.now(timezone.utc).isoformat()
    folder = {
        "id": str(uuid.uuid4()),
        "name": name or "New Folder",
        "system_prompt": system_prompt or "",
        "created_at": now,
        "updated_at": now,
    }
    with _folders_lock:
        folders = _read_folders()
        folders.append(folder)
        _write_folders(folders)
    return folder


def update_folder(
    folder_id: str,
    name: str | None = None,
    system_prompt: str | None = None,
) -> dict | None:
    with _folders_lock:
        folders = _read_folders()
        folder = next((item for item in folders if item.get("id") == folder_id), None)
        if folder is None:
            return None
        if name is not None:
            folder["name"] = name or "Untitled Folder"
        if system_prompt is not None:
            folder["system_prompt"] = system_prompt
        folder["updated_at"] = datetime.now(timezone.utc).isoformat()
        _write_folders(folders)
        return dict(folder)


def delete_folder(folder_id: str) -> bool:
    with _folders_lock:
        folders = _read_folders()
        remaining = [item for item in folders if item.get("id") != folder_id]
        if len(remaining) == len(folders):
            return False
        _write_folders(remaining)
        return True


def folder_conversations(folder_id: str) -> list[dict]:
    return [conv for conv in list_all() if conv.get("folder_id") == folder_id]


# ── Public API ────────────────────────────────────────────────────────────────

def list_all() -> list[dict]:
    """Return cached conversation summaries sorted by most-recently modified."""
    global _index
    with _index_lock:
        if _index is not None:
            return list(_index)

    summaries = _build_index_summaries()

    with _index_lock:
        if _index is None:
            _index = summaries
        return list(_index or [])


def load(conv_id: str) -> dict | None:
    path = _path(conv_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def save(conv_id: str, data: dict) -> dict:
    """Stamp updated_at, write atomically, and return the saved data."""
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    path = _path(conv_id)
    tmp_path = path.with_suffix(f".tmp-{uuid.uuid4().hex}")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    atomic_replace(tmp_path, path)
    _update_index_for(conv_id, data)
    return data


def delete(conv_id: str) -> bool:
    path = _path(conv_id)
    if path.exists():
        path.unlink()
        _remove_index_entry(conv_id)
        return True
    return False


def create(title: str = "New Conversation", folder_id: str | None = None) -> dict:
    """Create, persist, and return a blank conversation."""
    conv_id = str(uuid.uuid4())
    data = {
        "id":                conv_id,
        "title":             title,
        "messages":          [],
        "created_at":        datetime.now(timezone.utc).isoformat(),
    }
    if folder_id:
        data["folder_id"] = folder_id
    data["working_directory"] = str(conversation_working_directory(runtime_id(conv_id, data)))
    return save(conv_id, data)
