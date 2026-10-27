"""
Tests for store.py — filesystem-backed conversation CRUD and image storage.

All tests use the `tmp_lumen` fixture from conftest.py so they never touch
the real ~/.lumen directory.
"""
from __future__ import annotations


import base64
import hashlib
import pytest

import store
from tests.conftest import make_png_b64


# ---------------------------------------------------------------------------
# save_image
# ---------------------------------------------------------------------------

class TestSaveImage:
    def test_returns_sha256_dot_ext_filename(self, tmp_lumen):
        b64 = make_png_b64()
        raw = base64.b64decode(b64)
        expected = hashlib.sha256(raw).hexdigest() + ".png"
        assert store.save_image(b64, "image/png") == expected

    def test_file_is_written_to_images_dir(self, tmp_lumen):
        name = store.save_image(make_png_b64(), "image/png")
        assert (tmp_lumen["images_dir"] / name).exists()

    def test_idempotent_on_duplicate_content(self, tmp_lumen):
        b64 = make_png_b64()
        n1 = store.save_image(b64, "image/png")
        n2 = store.save_image(b64, "image/png")
        assert n1 == n2
        assert len(list(tmp_lumen["images_dir"].iterdir())) == 1

    def test_jpeg_extension_preserved(self, tmp_lumen):
        name = store.save_image(make_png_b64(), "image/jpeg")
        assert name.endswith(".jpeg")

    def test_jpg_normalised_to_jpeg(self, tmp_lumen):
        name = store.save_image(make_png_b64(), "image/jpg")
        assert name.endswith(".jpeg")

    def test_webp_and_gif_accepted(self, tmp_lumen):
        for media_type in ("image/webp", "image/gif"):
            name = store.save_image(make_png_b64(), media_type)
            assert name.endswith(("." + media_type.split("/")[1]))

    def test_unsupported_type_raises_value_error(self, tmp_lumen):
        with pytest.raises(ValueError, match="Unsupported image type"):
            store.save_image(make_png_b64(), "image/bmp")

    def test_invalid_base64_raises_value_error(self, tmp_lumen):
        with pytest.raises(ValueError, match="Invalid image data"):
            store.save_image("not-valid-base64!!!", "image/png")

    def test_empty_base64_stores_empty_file(self, tmp_lumen):
        # base64.b64decode("") == b"" which is valid bytes; store saves it normally
        name = store.save_image("", "image/png")
        assert name.endswith(".png")
        assert (tmp_lumen["images_dir"] / name).exists()


# ---------------------------------------------------------------------------
# get_image_path
# ---------------------------------------------------------------------------

class TestGetImagePath:
    def test_returns_path_for_known_image(self, tmp_lumen):
        name = store.save_image(make_png_b64(), "image/png")
        path = store.get_image_path(name)
        assert path is not None
        assert path.exists()

    def test_returns_none_for_nonexistent_hash(self, tmp_lumen):
        assert store.get_image_path("a" * 64 + ".png") is None

    def test_rejects_path_traversal(self, tmp_lumen):
        assert store.get_image_path("../../etc/passwd") is None

    def test_rejects_non_hex_prefix(self, tmp_lumen):
        assert store.get_image_path("notahexstring.png") is None

    def test_rejects_wrong_extension(self, tmp_lumen):
        # 64 hex chars but .exe extension — should be filtered by regex
        assert store.get_image_path("a" * 64 + ".exe") is None

    def test_rejects_name_with_slash(self, tmp_lumen):
        assert store.get_image_path("subdir/" + "a" * 64 + ".png") is None


# ---------------------------------------------------------------------------
# create / load / save / delete / list_all
# ---------------------------------------------------------------------------

class TestCreate:
    def test_fields_present(self, tmp_lumen):
        conv = store.create("My Chat")
        assert "id" in conv
        assert conv["title"] == "My Chat"
        assert conv["messages"] == []
        assert "created_at" in conv

    def test_file_persisted_on_disk(self, tmp_lumen):
        conv = store.create("disk-check")
        assert (tmp_lumen["conv_dir"] / f"{conv['id']}.json").exists()

    def test_chats_in_folder_share_runtime_but_keep_separate_messages(self, tmp_lumen):
        folder = store.create_folder("Project")
        first = store.create("First", folder["id"])
        second = store.create("Second", folder["id"])
        first["messages"] = [{"role": "user", "content": "only first"}]
        store.save(first["id"], first)

        assert store.runtime_id(first["id"]) == store.runtime_id(second["id"])
        assert store.working_directory(first["id"]) == store.working_directory(second["id"])
        assert store.load(second["id"])["messages"] == []


class TestFolders:
    def test_folder_crud(self, tmp_lumen):
        folder = store.create_folder("Project")
        assert store.get_folder(folder["id"])["name"] == "Project"
        assert store.update_folder(folder["id"], "Renamed")["name"] == "Renamed"
        updated = store.update_folder(folder["id"], system_prompt="Answer as a researcher.")
        assert updated["name"] == "Renamed"
        assert updated["system_prompt"] == "Answer as a researcher."
        assert store.delete_folder(folder["id"]) is True
        assert store.get_folder(folder["id"]) is None


class TestLoad:
    def test_loads_existing_conversation(self, tmp_lumen):
        conv = store.create("load-me")
        loaded = store.load(conv["id"])
        assert loaded is not None
        assert loaded["id"] == conv["id"]
        assert loaded["title"] == "load-me"

    def test_returns_none_for_missing_id(self, tmp_lumen):
        assert store.load("does-not-exist-xyz") is None

    def test_returns_none_for_corrupt_json(self, tmp_lumen):
        bad_path = tmp_lumen["conv_dir"] / "bad.json"
        bad_path.write_text("{not valid json")
        assert store.load("bad") is None


class TestSave:
    def test_stamps_updated_at(self, tmp_lumen):
        conv = store.create("stamp")
        conv["title"] = "Stamped"
        saved = store.save(conv["id"], conv)
        assert "updated_at" in saved

    def test_write_is_atomic_no_tmp_files_left(self, tmp_lumen):
        conv = store.create("atomic")
        store.save(conv["id"], conv)
        assert list(tmp_lumen["conv_dir"].glob("*.tmp-*")) == []

    def test_persists_arbitrary_fields(self, tmp_lumen):
        conv = store.create("fields")
        conv["custom_key"] = "custom_value"
        store.save(conv["id"], conv)
        assert store.load(conv["id"])["custom_key"] == "custom_value"


class TestDelete:
    def test_delete_existing_returns_true(self, tmp_lumen):
        conv = store.create("bye")
        assert store.delete(conv["id"]) is True

    def test_file_is_gone_after_delete(self, tmp_lumen):
        conv = store.create("gone")
        store.delete(conv["id"])
        assert store.load(conv["id"]) is None

    def test_delete_nonexistent_returns_false(self, tmp_lumen):
        assert store.delete("ghost-9999") is False


class TestListAll:
    def test_empty_when_no_conversations(self, tmp_lumen):
        assert store.list_all() == []

    def test_includes_all_created_conversations(self, tmp_lumen):
        ids = {store.create(f"conv-{i}")["id"] for i in range(3)}
        listed_ids = {c["id"] for c in store.list_all()}
        assert ids.issubset(listed_ids)

    def test_sorted_newest_first(self, tmp_lumen):
        """list_all() sorts by mtime descending — the most recently modified conv is first."""
        import time
        a = store.create("alpha")
        time.sleep(0.02)  # ensure distinct mtime
        b = store.create("beta")
        ids = [c["id"] for c in store.list_all()]
        assert ids.index(b["id"]) < ids.index(a["id"])

    def test_corrupt_files_are_skipped(self, tmp_lumen):
        store.create("good")
        (tmp_lumen["conv_dir"] / "corrupt.json").write_text("{{{bad")
        result = store.list_all()
        assert all("id" in r for r in result)

    def test_concurrent_saves_keep_cached_index_consistent(self, tmp_lumen):
        from concurrent.futures import ThreadPoolExecutor, as_completed

        # Prime the cache so each save updates the in-memory index rather than
        # falling back to a later full rebuild. This is the race-prone path.
        assert store.list_all() == []

        def create_one(i: int) -> str:
            return store.create(f"threaded-{i}")["id"]

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(create_one, i) for i in range(40)]
            created_ids = {future.result() for future in as_completed(futures)}

        listed_ids = {item["id"] for item in store.list_all()}
        assert created_ids.issubset(listed_ids)

    def test_index_mutators_acquire_lock(self, tmp_lumen, monkeypatch):
        class RecordingLock:
            def __init__(self):
                self.entries = 0

            def __enter__(self):
                self.entries += 1

            def __exit__(self, exc_type, exc, tb):
                return False

        lock = RecordingLock()
        monkeypatch.setattr(store, "_index_lock", lock)
        monkeypatch.setattr(store, "_index", [])

        store._update_index_for("conv-1", {"title": "One"})
        store._remove_index_entry("conv-1")

        assert lock.entries == 2


def test_conversations_use_utf8_even_with_windows_locale(tmp_lumen, monkeypatch):
    """Model output must persist when the system text encoding cannot represent it."""
    from pathlib import Path
    import store

    original_open = Path.open
    def windows_open(path, mode='r', buffering=-1, encoding=None, errors=None, newline=None):
        if 'b' not in mode and encoding in (None, 'locale'):
            encoding = 'cp1252'
        return original_open(path, mode, buffering, encoding, errors, newline)
    monkeypatch.setattr(Path, 'open', windows_open)
    text = 'مرحبا 🌍'
    conversation = store.create(text)
    conversation['messages'] = [{'role': 'assistant', 'content': text}]
    store.save(conversation['id'], conversation)
    assert store.load(conversation['id'])['messages'][0]['content'] == text
    store.invalidate_index()
    assert store.list_all()[0]['title'] == text

    # Legacy Windows conversations remain readable and migrate on their next save.
    path = tmp_lumen['conv_dir'] / (conversation['id'] + '.json')
    path.write_bytes('{"title":"Café","messages":[]}'.encode('cp1252'))
    legacy = store.load(conversation['id'])
    assert legacy['title'] == 'Café'
    store.save(conversation['id'], legacy)
    assert 'Café'.encode('utf-8') in path.read_bytes()


def test_listed_summaries_cannot_corrupt_cached_runtime_ownership(tmp_lumen):
    conversation = store.create("Original")
    listed = store.list_all()
    listed[0]["title"] = "Changed"
    listed[0]["folder_id"] = "unrelated"
    assert store.list_all()[0]["title"] == "Original"
    assert store.list_all()[0]["folder_id"] is None
    assert store.runtime_id(conversation["id"]) == conversation["id"]
