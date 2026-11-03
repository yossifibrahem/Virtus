"""
Tests for SQLite conversation/folder CRUD and chat-owned image storage.

All tests use the `tmp_virtus` fixture from conftest.py so they never touch
the real ~/.virtus directory.
"""
from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing, contextmanager

import pytest

import store
from tests.conftest import make_png_bytes


@contextmanager
def _database():
    """Commit/roll back test SQL and explicitly close each connection afterward."""
    with closing(sqlite3.connect(store.DATABASE_FILE)) as connection:
        with connection:
            yield connection


# ---------------------------------------------------------------------------
# Chat-owned images
# ---------------------------------------------------------------------------

class TestSaveImage:
    def test_returns_sha256_dot_ext_reference(self, image_chat):
        raw = make_png_bytes()
        expected = hashlib.sha256(raw).hexdigest() + ".png"
        assert store.save_image(image_chat, raw, "image/png") == expected

    def test_bytes_and_mime_type_are_stored_in_sqlite(self, image_chat):
        ref = store.save_image(image_chat, make_png_bytes(), "image/png")
        assert store.get_image(image_chat, ref) == {"mime_type": "image/png", "data": make_png_bytes()}
        with _database() as connection:
            assert connection.execute("SELECT typeof(data) FROM images").fetchone() == ("blob",)

    def test_reupload_in_same_chat_keeps_one_row(self, image_chat):
        n1 = store.save_image(image_chat, make_png_bytes(), "image/png")
        n2 = store.save_image(image_chat, make_png_bytes(), "image/png")
        assert n1 == n2
        with _database() as connection:
            assert connection.execute("SELECT COUNT(*) FROM images").fetchone() == (1,)

    @pytest.mark.parametrize("media_type,extension", [
        ("image/png", "png"), ("image/jpeg", "jpeg"), ("image/jpg", "jpeg"),
        ("image/webp", "webp"), ("image/gif", "gif"),
    ])
    def test_supported_mime_types(self, image_chat, media_type, extension):
        ref = store.save_image(image_chat, make_png_bytes(), media_type)
        assert ref.endswith("." + extension)
        assert store.get_image(image_chat, ref)["mime_type"] == "image/" + extension

    def test_unsupported_type_raises_value_error(self, image_chat):
        with pytest.raises(ValueError, match="Unsupported image type"):
            store.save_image(image_chat, make_png_bytes(), "image/bmp")

    def test_upload_to_deleted_chat_does_not_store_an_image(self, image_chat):
        store.delete(image_chat)
        assert store.save_image(image_chat, make_png_bytes(), "image/png") is None
        with _database() as connection:
            assert connection.execute("SELECT COUNT(*) FROM images").fetchone() == (0,)


class TestGetImage:
    @pytest.mark.parametrize("ref", ["missing", "../../etc/passwd", "' OR 1=1 --", ["invalid"]])
    def test_invalid_or_unknown_reference_returns_none(self, image_chat, ref):
        assert store.get_image(image_chat, ref) is None

    def test_each_chat_owns_its_copy_and_deletion_removes_only_that_copy(self, image_chat):
        other = store.create("Other")["id"]
        ref = store.save_image(image_chat, make_png_bytes(), "image/png")
        assert store.get_image(other, ref) is None
        store.save_image(other, make_png_bytes(), "image/png")
        with _database() as connection:
            assert connection.execute("SELECT COUNT(*) FROM images").fetchone() == (2,)
        store.delete(image_chat)
        assert store.get_image(image_chat, ref) is None
        assert store.get_image(other, ref)["data"] == make_png_bytes()

    def test_history_edits_keep_the_chats_images(self, image_chat):
        ref = store.save_image(image_chat, make_png_bytes(), "image/png")
        store.update(image_chat, {"messages": [{"role": "user", "content": [{"type": "image_ref", "ref": ref}]}]})
        store.update(image_chat, {"messages": []})
        assert store.get_image(image_chat, ref)["data"] == make_png_bytes()


# ---------------------------------------------------------------------------
# create / load / update / delete / list_all
# ---------------------------------------------------------------------------

class TestCreate:
    def test_fields_present(self, tmp_virtus):
        conv = store.create("My Chat")
        assert "id" in conv
        assert conv["title"] == "My Chat"
        assert conv["messages"] == []
        assert "created_at" in conv

    def test_database_persisted_on_disk(self, tmp_virtus):
        conv = store.create("disk-check")
        assert tmp_virtus["database_file"].read_bytes().startswith(b"SQLite format 3\x00")
        with _database() as connection:
            assert connection.execute("SELECT title FROM conversations WHERE id = ?", (conv["id"],)).fetchone() == ("disk-check",)

    def test_chats_in_folder_share_runtime_but_keep_separate_messages(self, tmp_virtus):
        folder = store.create_folder("Project")
        first = store.create("First", folder["id"])
        second = store.create("Second", folder["id"])
        store.update(first["id"], {"messages": [{"role": "user", "content": "only first"}]})

        assert store.runtime_id(first["id"]) == store.runtime_id(second["id"])
        assert store.working_directory(first["id"]) == store.working_directory(second["id"])
        assert store.load(second["id"])["messages"] == []


class TestFolders:
    def test_folder_crud(self, tmp_virtus):
        folder = store.create_folder("Project")
        assert store.get_folder(folder["id"])["name"] == "Project"
        assert store.update_folder(folder["id"], "Renamed")["name"] == "Renamed"
        updated = store.update_folder(folder["id"], system_prompt="Answer as a researcher.")
        assert updated["name"] == "Renamed"
        assert updated["system_prompt"] == "Answer as a researcher."
        assert store.delete_folder(folder["id"]) is True
        assert store.get_folder(folder["id"]) is None


class TestLoad:
    def test_loads_existing_conversation(self, tmp_virtus):
        conv = store.create("load-me")
        loaded = store.load(conv["id"])
        assert loaded is not None
        assert loaded["id"] == conv["id"]
        assert loaded["title"] == "load-me"

    def test_returns_none_for_missing_id(self, tmp_virtus):
        assert store.load("does-not-exist-xyz") is None

    def test_database_corruption_is_reported(self, tmp_virtus):
        tmp_virtus["database_file"].write_bytes(b"not a SQLite database")
        with pytest.raises(sqlite3.DatabaseError):
            store.load("anything")


class TestUpdate:
    def test_stamps_updated_at(self, tmp_virtus):
        conv = store.create("stamp")
        saved = store.update(conv["id"], {"title": "Stamped"})
        assert saved["updated_at"] > conv["updated_at"]

    def test_failed_history_save_rolls_back_metadata_and_all_entries(self, tmp_virtus):
        conv = store.create("Original")
        changed = {"title": "Must roll back", "messages": [
            {"role": "user", "content": "Written first"}, {"cannot_serialize": object()},
        ]}
        with pytest.raises(TypeError):
            store.update(conv["id"], changed)
        assert store.load(conv["id"]) == conv
        assert store.list_all()[0]["title"] == "Original"

    def test_persists_arbitrary_fields(self, tmp_virtus):
        conv = store.create("fields")
        store.update(conv["id"], {"custom_key": "custom_value"})
        assert store.load(conv["id"])["custom_key"] == "custom_value"


class TestDelete:
    def test_delete_existing_returns_true(self, tmp_virtus):
        conv = store.create("bye")
        assert store.delete(conv["id"]) is True

    def test_history_is_gone_after_delete(self, tmp_virtus):
        conv = store.create("gone")
        store.update(conv["id"], {"messages": [{"role": "user", "content": "delete"}]})
        store.delete(conv["id"])
        assert store.load(conv["id"]) is None
        with _database() as connection:
            assert connection.execute("SELECT COUNT(*) FROM conversation_entries").fetchone()[0] == 0

    def test_delete_nonexistent_returns_false(self, tmp_virtus):
        assert store.delete("ghost-9999") is False


class TestListAll:
    def test_empty_when_no_conversations(self, tmp_virtus):
        assert store.list_all() == []

    def test_includes_all_created_conversations(self, tmp_virtus):
        ids = {store.create(f"conv-{i}")["id"] for i in range(3)}
        listed_ids = {c["id"] for c in store.list_all()}
        assert ids.issubset(listed_ids)

    def test_sorted_newest_first(self, tmp_virtus):
        """Creation and later edits both update the database sort order."""
        a = store.create("alpha")
        b = store.create("beta")
        ids = [c["id"] for c in store.list_all()]
        assert ids.index(b["id"]) < ids.index(a["id"])
        store.update(a["id"], {"title": "Updated alpha"})
        assert store.list_all()[0]["id"] == a["id"]

    def test_summaries_do_not_deserialize_histories(self, tmp_virtus, monkeypatch):
        conv = store.create("Summary")
        folder = store.create_folder("Project")
        store.update(conv["id"], {"folder_id": folder["id"], "messages": [{"content": "large history"}]})
        monkeypatch.setattr(store.json, "loads", lambda *args: pytest.fail("History was read"))
        assert store.list_all()[0]["title"] == "Summary"
        assert store.folder_conversations(folder["id"])[0]["id"] == conv["id"]

    def test_concurrent_first_connections_and_saves_remain_visible(self, tmp_virtus):
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def create_one(i: int) -> str:
            return store.create(f"threaded-{i}")["id"]

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(create_one, i) for i in range(40)]
            created_ids = {future.result() for future in as_completed(futures)}

        listed_ids = {item["id"] for item in store.list_all()}
        assert created_ids.issubset(listed_ids)


def test_unicode_conversation_and_folder_round_trip(tmp_virtus):
    text = 'مرحبا 🌍'
    folder = store.create_folder(text, system_prompt=text)
    conversation = store.create(text, folder['id'])
    store.update(conversation['id'], {'messages': [{'role': 'assistant', 'content': text}]})
    assert store.load(conversation['id'])['messages'][0]['content'] == text
    assert store.list_all()[0]['title'] == text
    assert store.get_folder(folder['id'])['system_prompt'] == text


def test_listed_summaries_cannot_corrupt_runtime_ownership(tmp_virtus):
    conversation = store.create("Original")
    listed = store.list_all()
    listed[0]["title"] = "Changed"
    listed[0]["folder_id"] = "unrelated"
    assert store.list_all()[0]["title"] == "Original"
    assert store.list_all()[0]["folder_id"] is None
    assert store.runtime_id(conversation["id"]) == conversation["id"]


def test_history_round_trip_preserves_branches_media_and_truncated_suffixes(tmp_virtus):
    conversation = store.create("Branches")
    first = {"role": "user", "content": [{"type": "image_ref", "ref": "a.png"}]}
    tool = {"role": "tool", "tool_call_id": "call-1", "content": "Page", "_tool_media": ["b.jpeg"]}
    branch = {"type": "message", "branch": {"kind": "assistant", "active": 0, "variants": [
        {"messages": [tool], "displayLog": [{"type": "thinking", "content": "Hidden variant"}]},
        {"messages": [{"role": "assistant", "content": "Alternative"}], "displayLog": []},
    ]}}
    store.update(conversation["id"], {"messages": [first, tool], "displayLog": [branch, {"type": "status"}]})
    saved = store.load(conversation["id"])
    assert saved["messages"] == [first, tool]
    assert saved["displayLog"][0] == branch
    store.update(conversation["id"], {"messages": [first], "displayLog": [branch]})
    assert store.load(conversation["id"])["messages"] == [first]
    assert store.load(conversation["id"])["displayLog"] == [branch]


def test_stream_snapshots_only_write_changed_entries_and_metadata_edits_preserve_history(tmp_virtus):
    conversation = store.create("Original")
    first = {"role": "user", "content": "Question"}
    partial = {"role": "assistant", "content": "Partial"}
    store.update(conversation["id"], {"messages": [first, partial], "displayLog": []})
    with _database() as connection:
        connection.executescript("""
            CREATE TABLE changed_entries (kind TEXT, position INTEGER);
            CREATE TRIGGER record_entry_update AFTER UPDATE ON conversation_entries
            BEGIN INSERT INTO changed_entries VALUES (new.kind, new.position); END;
        """)
    store.update(conversation["id"], {"title": "Renamed", "system_prompt": "Instructions"})
    finished = {**partial, "content": "Partial answer finished"}
    store.update(conversation["id"], {"messages": [first, finished], "displayLog": []})
    with _database() as connection:
        assert connection.execute("SELECT * FROM changed_entries").fetchall() == [("messages", 1)]
    saved = store.load(conversation["id"])
    assert saved["title"] == "Renamed"
    assert saved["system_prompt"] == "Instructions"
    assert saved["messages"] == [first, finished]


def test_concurrent_partial_edits_preserve_each_others_fields(tmp_virtus):
    from concurrent.futures import ThreadPoolExecutor

    conversation = store.create()
    def edit(index):
        return store.update(conversation["id"], {f"field_{index}": index})
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(edit, range(24)))
    saved = store.load(conversation["id"])
    assert all(saved[f"field_{index}"] == index for index in range(24))


def test_concurrent_folder_edits_preserve_name_and_instructions(tmp_virtus):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    folder = store.create_folder()
    ready = Barrier(2)
    def edit(changes):
        ready.wait(timeout=5)
        store.update_folder(folder["id"], **changes)
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(edit, [{"name": "Renamed"}, {"system_prompt": "Keep these instructions"}]))
    saved = store.get_folder(folder["id"])
    assert saved["name"] == "Renamed"
    assert saved["system_prompt"] == "Keep these instructions"


def test_missing_folder_cannot_leave_an_orphan_chat(tmp_virtus):
    with pytest.raises(sqlite3.IntegrityError):
        store.create("Orphan", "missing-folder")
    assert store.list_all() == []


def test_deleting_folder_cascades_only_its_chats_and_histories(tmp_virtus):
    folder = store.create_folder("Delete")
    chats = [store.create("One", folder["id"]), store.create("Two", folder["id"])]
    keep = store.create("Keep")
    for chat in [*chats, keep]:
        store.update(chat["id"], {"messages": [{"role": "user", "content": "Question"}]})
    assert store.delete_folder(folder["id"])
    assert all(store.load(chat["id"]) is None for chat in chats)
    assert store.load(keep["id"])
    with _database() as connection:
        assert connection.execute("SELECT conversation_id FROM conversation_entries").fetchall() == [(keep["id"],)]


def test_failed_folder_delete_rolls_back_all_cascades(tmp_virtus):
    folder = store.create_folder()
    chats = [store.create("One", folder["id"]), store.create("Two", folder["id"])]
    with _database() as connection:
        connection.executescript("""
            CREATE TRIGGER fail_folder_delete AFTER DELETE ON folders
            BEGIN SELECT RAISE(ABORT, 'delete failed'); END;
        """)
    with pytest.raises(sqlite3.IntegrityError, match="delete failed"):
        store.delete_folder(folder["id"])
    assert store.get_folder(folder["id"])
    assert all(store.load(chat["id"]) == chat for chat in chats)


def test_partial_update_cannot_resurrect_a_deleted_conversation(tmp_virtus):
    conversation = store.create()
    store.delete(conversation["id"])
    assert store.update(conversation["id"], {"title": "Stale writer"}) is None
    assert store.list_all() == []


def test_activity_polling_only_reads_active_metadata(tmp_virtus, monkeypatch):
    store.create("Idle")
    active = store.create("Active")
    store.update(active["id"], {"active_stream_id": "stream-1"})
    monkeypatch.setattr("container_service.ensure_container", lambda *args: pytest.fail("Docker was accessed"))
    monkeypatch.setattr(store.json, "loads", lambda *args: pytest.fail("History was loaded"))
    assert store.list_active() == [{"id": active["id"], "active_stream_id": "stream-1"}]


def test_active_history_protection_uses_current_database_state(tmp_virtus):
    conversation = store.create()
    history = [{"role": "user", "content": "Saved by worker"}]
    store.update(conversation["id"], {"messages": history, "active_stream_id": "stream-1"})
    saved = store.update(conversation["id"], {"title": "Rename", "messages": [], "displayLog": []},
                         protect_active_history=True)
    assert saved["messages"] == history
    assert saved["title"] == "Rename"
    assert saved["active_stream_id"] == "stream-1"


def test_future_schema_is_rejected_without_changing_data(tmp_virtus):
    conversation = store.create()
    with _database() as connection:
        connection.execute("PRAGMA user_version=999")
    with pytest.raises(RuntimeError, match="Unsupported conversation database version"):
        store.create("Must not write")
    with _database() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 999
        assert connection.execute("SELECT id FROM conversations").fetchall() == [(conversation["id"],)]


def test_database_path_override_and_persistence_across_processes(tmp_virtus):
    import json
    import os
    import subprocess
    import sys

    folder = store.create_folder("Project")
    conversation = store.create("Saved", folder["id"])
    ref = store.save_image(conversation["id"], make_png_bytes(), "image/png")
    result = subprocess.run([sys.executable, "-c", """
import json, store
chat = store.load(store.list_all()[0]['id'])
print(json.dumps({'folder': store.list_folders()[0], 'chat': chat,
                  'image': store.get_image(chat['id'], __import__('sys').argv[1])['data'].hex()}))
""", ref], env={**os.environ, "HOME": str(tmp_virtus["root"]), "USERPROFILE": str(tmp_virtus["root"]),
             "VIRTUS_DATABASE_FILE": str(store.DATABASE_FILE)},
        capture_output=True, text=True, check=True, timeout=15)
    assert json.loads(result.stdout) == {"folder": folder, "chat": conversation, "image": make_png_bytes().hex()}


def test_existing_sqlite_database_gets_image_table_without_changing_chats(tmp_virtus):
    folder = store.create_folder("Project")
    conversation = store.create("Saved", folder["id"])
    with _database() as connection:
        connection.execute("DROP TABLE images")
        connection.execute("PRAGMA user_version=1")
    assert store.load(conversation["id"]) == conversation
    assert store.get_folder(folder["id"]) == folder
    ref = store.save_image(conversation["id"], make_png_bytes(), "image/png")
    assert store.get_image(conversation["id"], ref)["data"] == make_png_bytes()
    with _database() as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (2,)
