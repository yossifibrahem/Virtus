"""SQLite persistence for conversations, folders, and chat-owned images.

The store is independent of Flask. Each operation owns a connection and a short
transaction; connections are never shared between request and streaming threads.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from mcp_adapters import conversation_working_directory

DATABASE_FILE = Path(os.path.expanduser(os.getenv(
    "VIRTUS_DATABASE_FILE", str(Path.home() / ".virtus" / "virtus.sqlite3"))))

_IMAGE_TYPES = {'image/png', 'image/jpeg', 'image/webp', 'image/gif'}
_HISTORY_KEYS = ("messages", "displayLog")
_SCHEMA_VERSION = 2
_schema_lock = threading.Lock()
_IMAGES_SCHEMA = """CREATE TABLE images (
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    id TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    data BLOB NOT NULL,
    PRIMARY KEY (conversation_id, id)
) WITHOUT ROWID"""
_SCHEMA = (
    """CREATE TABLE folders (
        id TEXT PRIMARY KEY NOT NULL,
        name TEXT NOT NULL,
        system_prompt TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE conversations (
        id TEXT PRIMARY KEY NOT NULL,
        title TEXT,
        folder_id TEXT REFERENCES folders(id) ON DELETE CASCADE,
        active_stream_id TEXT,
        updated_at TEXT NOT NULL,
        metadata TEXT NOT NULL
    )""",
    "CREATE INDEX conversations_updated ON conversations(updated_at DESC, id)",
    "CREATE INDEX conversations_folder ON conversations(folder_id, updated_at DESC, id)",
    """CREATE INDEX conversations_active ON conversations(updated_at DESC, id)
       WHERE active_stream_id IS NOT NULL""",
    """CREATE TABLE conversation_entries (
        conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        kind TEXT NOT NULL CHECK (kind IN ('messages', 'displayLog')),
        position INTEGER NOT NULL CHECK (position >= 0),
        payload TEXT NOT NULL,
        PRIMARY KEY (conversation_id, kind, position)
    ) WITHOUT ROWID""",
    _IMAGES_SCHEMA,
)


def _initialize(connection: sqlite3.Connection) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version == _SCHEMA_VERSION:
        return
    if version not in (0, 1):
        raise RuntimeError(f"Unsupported conversation database version: {version}")
    with _schema_lock:
        # WAL is persistent and is set outside the schema transaction. It lets
        # sidebar/history reads proceed while a background turn saves output.
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("BEGIN IMMEDIATE")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            for statement in _SCHEMA:
                connection.execute(statement)
        elif version == 1:
            connection.execute(_IMAGES_SCHEMA)
        elif version != _SCHEMA_VERSION:
            raise RuntimeError(f"Unsupported conversation database version: {version}")
        connection.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
        connection.commit()


@contextmanager
def _connection(*, write: bool = False):
    DATABASE_FILE.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_FILE, timeout=10, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        _initialize(connection)
        connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _summary(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["working_directory"] = str(conversation_working_directory(runtime_id(data["id"], data)))
    return data


def _load(connection: sqlite3.Connection, conv_id: str) -> dict | None:
    row = connection.execute("SELECT metadata FROM conversations WHERE id = ?", (conv_id,)).fetchone()
    if row is None:
        return None
    data = json.loads(row["metadata"])
    for entry in connection.execute(
        """SELECT kind, payload FROM conversation_entries
           WHERE conversation_id = ? ORDER BY kind, position""", (conv_id,)
    ):
        data[entry["kind"]].append(json.loads(entry["payload"]))
    return data


def _write(connection: sqlite3.Connection, conv_id: str, data: dict, histories: dict) -> None:
    # The metadata contains empty placeholders for histories present in the API
    # document. The actual entries live in separate rows, including branch data.
    metadata = dict(data)
    for key in _HISTORY_KEYS:
        if key in metadata:
            metadata[key] = []
    connection.execute(
        """INSERT INTO conversations (id, title, folder_id, active_stream_id, updated_at, metadata)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET title=excluded.title, folder_id=excluded.folder_id,
               active_stream_id=excluded.active_stream_id, updated_at=excluded.updated_at,
               metadata=excluded.metadata""",
        (conv_id, data.get("title", "Untitled"), data.get("folder_id"),
         data.get("active_stream_id"), data["updated_at"], _json(metadata)),
    )
    for kind, entries in histories.items():
        if not isinstance(entries, list):
            raise ValueError(f"{kind} must be an array")
        # Only changed/new entries are written. Streaming snapshots and branch
        # saves may replace a suffix; unchanged earlier rows remain untouched.
        connection.executemany(
            """INSERT INTO conversation_entries (conversation_id, kind, position, payload)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(conversation_id, kind, position) DO UPDATE SET payload=excluded.payload
               WHERE conversation_entries.payload != excluded.payload""",
            ((conv_id, kind, index, _json(entry)) for index, entry in enumerate(entries)),
        )
        connection.execute(
            "DELETE FROM conversation_entries WHERE conversation_id = ? AND kind = ? AND position >= ?",
            (conv_id, kind, len(entries)),
        )


# ── Image storage ─────────────────────────────────────────────────────────────

def save_image(conv_id: str, data: bytes, media_type: str) -> str | None:
    """Save image bytes owned by one chat; return None if that chat was deleted."""
    if not isinstance(media_type, str):
        raise ValueError("Invalid image type")
    media_type = media_type.split(";", 1)[0].strip().lower()
    if media_type == "image/jpg":
        media_type = "image/jpeg"
    if media_type not in _IMAGE_TYPES:
        raise ValueError(f"Unsupported image type: {media_type or 'unknown'}")
    ref = hashlib.sha256(data).hexdigest() + "." + media_type.split("/")[1]
    with _connection(write=True) as connection:
        if connection.execute("SELECT 1 FROM conversations WHERE id = ?", (conv_id,)).fetchone() is None:
            return None
        connection.execute(
            "INSERT OR IGNORE INTO images (conversation_id, id, mime_type, data) VALUES (?, ?, ?, ?)",
            (conv_id, ref, media_type, data),
        )
    return ref


def get_image(conv_id: str, ref: str) -> dict | None:
    """Read image bytes only within the owning chat."""
    if not isinstance(ref, str):
        return None
    with _connection() as connection:
        row = connection.execute(
            "SELECT mime_type, data FROM images WHERE conversation_id = ? AND id = ?", (conv_id, ref)
        ).fetchone()
        return dict(row) if row is not None else None


# ── Runtime ownership ─────────────────────────────────────────────────────────

def working_directory(conv_id: str) -> Path:
    """Return the MCP workspace for a chat (shared by chats in one folder)."""
    return conversation_working_directory(runtime_id(conv_id))


def runtime_id(conv_id: str, data: dict | None = None) -> str:
    """Return the container/workspace identity used by a conversation."""
    conversation = data if data is not None else load(conv_id)
    folder_id = conversation.get("folder_id") if conversation else None
    return f"folder_{folder_id}" if folder_id else conv_id


# ── Folders ───────────────────────────────────────────────────────────────────

def list_folders() -> list[dict]:
    with _connection() as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM folders ORDER BY rowid")]


def get_folder(folder_id: str) -> dict | None:
    with _connection() as connection:
        row = connection.execute("SELECT * FROM folders WHERE id = ?", (folder_id,)).fetchone()
        return dict(row) if row is not None else None


def create_folder(name: str = "New Folder", system_prompt: str = "") -> dict:
    folder = {"id": str(uuid.uuid4()), "name": name or "New Folder",
              "system_prompt": system_prompt or "", "created_at": _now()}
    folder["updated_at"] = folder["created_at"]
    with _connection(write=True) as connection:
        connection.execute(
            """INSERT INTO folders (id, name, system_prompt, created_at, updated_at)
               VALUES (:id, :name, :system_prompt, :created_at, :updated_at)""", folder,
        )
    return folder


def update_folder(folder_id: str, name: str | None = None, system_prompt: str | None = None) -> dict | None:
    with _connection(write=True) as connection:
        row = connection.execute("SELECT * FROM folders WHERE id = ?", (folder_id,)).fetchone()
        if row is None:
            return None
        folder = dict(row)
        if name is not None:
            folder["name"] = name or "Untitled Folder"
        if system_prompt is not None:
            folder["system_prompt"] = system_prompt
        folder["updated_at"] = _now()
        connection.execute(
            """UPDATE folders SET name=:name, system_prompt=:system_prompt, updated_at=:updated_at
               WHERE id=:id""", folder,
        )
        return folder


def delete_folder(folder_id: str) -> bool:
    """Atomically delete a folder and its chats, after the caller removes its runtime."""
    with _connection(write=True) as connection:
        return connection.execute("DELETE FROM folders WHERE id = ?", (folder_id,)).rowcount > 0


def folder_conversations(folder_id: str) -> list[dict]:
    with _connection() as connection:
        rows = connection.execute(
            """SELECT id, title, folder_id, active_stream_id, updated_at FROM conversations
               WHERE folder_id = ? ORDER BY updated_at DESC, id""", (folder_id,)
        ).fetchall()
    return [_summary(row) for row in rows]


# ── Conversations ─────────────────────────────────────────────────────────────

def list_all() -> list[dict]:
    """Query indexed summaries without loading or deserializing chat histories."""
    with _connection() as connection:
        rows = connection.execute(
            """SELECT id, title, folder_id, active_stream_id, updated_at FROM conversations
               ORDER BY updated_at DESC, id"""
        ).fetchall()
    return [_summary(row) for row in rows]


def load(conv_id: str) -> dict | None:
    with _connection() as connection:
        return _load(connection, conv_id)


def list_active() -> list[dict]:
    """Return active IDs for sidebar polling without loading histories/workspaces."""
    with _connection() as connection:
        return [dict(row) for row in connection.execute(
            """SELECT id, active_stream_id FROM conversations
               WHERE active_stream_id IS NOT NULL AND active_stream_id != ''
               ORDER BY updated_at DESC, id"""
        )]


def update(conv_id: str, changes: dict, *, remove: tuple[str, ...] = (),
           protect_active_history: bool = False, expected_title: str | None = None) -> dict | None:
    """Merge a partial edit with current metadata in one write transaction.

    History arrays are replaced only when supplied. Streaming callers can compare
    the turn's original title so a concurrent manual rename remains authoritative.
    An update never recreates a conversation that has been deleted.
    """
    with _connection(write=True) as connection:
        row = connection.execute("SELECT metadata FROM conversations WHERE id = ?", (conv_id,)).fetchone()
        if row is None:
            return None
        data = json.loads(row["metadata"])
        changes = dict(changes)
        if "title" in changes:
            changes["title"] = changes["title"] or "Untitled"
        if protect_active_history and data.get("active_stream_id"):
            for key in _HISTORY_KEYS:
                changes.pop(key, None)
        if expected_title is not None and data.get("title") != expected_title:
            changes.pop("title", None)
        for key in remove:
            data.pop(key, None)
        data.update(changes)
        data.update(id=conv_id, updated_at=_now())
        histories = {key: changes[key] for key in _HISTORY_KEYS if key in changes}
        histories.update({key: [] for key in remove if key in _HISTORY_KEYS and key not in changes})
        _write(connection, conv_id, data, histories)
        if all(key in changes or key not in data for key in _HISTORY_KEYS):
            return data
        return _load(connection, conv_id)


def delete(conv_id: str) -> bool:
    with _connection(write=True) as connection:
        return connection.execute("DELETE FROM conversations WHERE id = ?", (conv_id,)).rowcount > 0


def delete_all(conversation_ids: list[str], folder_ids: list[str]) -> None:
    """Delete a cleanup snapshot atomically; preserve unrelated chats created later."""
    with _connection(write=True) as connection:
        connection.executemany("DELETE FROM conversations WHERE id = ?", ((key,) for key in conversation_ids))
        connection.executemany("DELETE FROM folders WHERE id = ?", ((key,) for key in folder_ids))


def create(title: str = "New Conversation", folder_id: str | None = None) -> dict:
    conv_id = str(uuid.uuid4())
    data = {"id": conv_id, "title": title or "New Conversation", "messages": [], "created_at": _now()}
    if folder_id:
        data["folder_id"] = folder_id
    data["working_directory"] = str(conversation_working_directory(runtime_id(conv_id, data)))
    with _connection(write=True) as connection:
        data["updated_at"] = _now()
        _write(connection, conv_id, data, {"messages": []})
    return data
