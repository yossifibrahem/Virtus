"""Tests for server-side API provider config storage."""
from __future__ import annotations

import json

import app_config


import pytest


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    monkeypatch.setattr(app_config, "CONFIG_FILE", config_file)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    return config_file


def test_load_config_defaults_when_file_absent():
    assert app_config.load_config() == {
        "api_base": app_config.DEFAULT_API_BASE,
        "api_key": "",
    }


def test_save_config_writes_allowed_keys_atomically(isolated_config):
    result = app_config.save_config({
        "api_base": "https://example.test/v1",
        "api_key": "sk-test",
        "ignored": "nope",
    })

    assert result == {"api_base": "https://example.test/v1", "has_api_key": True}
    assert json.loads(isolated_config.read_text()) == {
        "api_base": "https://example.test/v1",
        "api_key": "sk-test",
    }
    assert list(isolated_config.parent.glob("*.tmp-*")) == []


def test_public_config_redacts_api_key(isolated_config):
    isolated_config.write_text(json.dumps({
        "api_base": "https://example.test/v1",
        "api_key": "sk-secret",
    }))

    assert app_config.public_config() == {
        "api_base": "https://example.test/v1",
        "has_api_key": True,
    }


def test_blank_api_key_preserves_existing_saved_key(isolated_config):
    app_config.save_config({"api_key": "sk-existing", "api_base": "https://one.test/v1"})
    app_config.save_config({"api_key": "", "api_base": "https://two.test/v1"})

    saved = json.loads(isolated_config.read_text())
    assert saved["api_key"] == "sk-existing"
    assert saved["api_base"] == "https://two.test/v1"


def test_environment_variables_take_precedence(isolated_config, monkeypatch):
    isolated_config.write_text(json.dumps({
        "api_base": "https://saved.test/v1",
        "api_key": "sk-saved",
    }))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://env.test/v1")

    assert app_config.load_config() == {
        "api_base": "https://env.test/v1",
        "api_key": "sk-env",
    }


def test_save_config_rejects_non_object():
    with pytest.raises(ValueError, match="JSON object"):
        app_config.save_config(["bad"])


def test_loaded_config_cannot_mutate_cached_credentials(isolated_config):
    app_config.save_config({"api_key": "sk-original"})
    loaded = app_config.load_config()
    loaded["api_key"] = "sk-changed"
    assert app_config.load_config()["api_key"] == "sk-original"


def test_changing_config_file_does_not_reuse_previous_cache(isolated_config, tmp_path, monkeypatch):
    app_config.save_config({"api_key": "sk-original"})
    monkeypatch.setattr(app_config, "CONFIG_FILE", tmp_path / "other" / "config.json")
    assert app_config.load_config()["api_key"] == ""
    app_config.save_config({"api_key": "sk-other"})
    assert app_config.load_config()["api_key"] == "sk-other"
