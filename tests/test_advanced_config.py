from __future__ import annotations

import json

import advanced_config


def _isolate(tmp_path, monkeypatch):
    path = tmp_path / "advanced_config.json"
    monkeypatch.setattr(advanced_config, "ADVANCED_CONFIG_FILE", path)
    monkeypatch.setattr(advanced_config, "_cache", None)
    monkeypatch.setattr(advanced_config, "_cache_at", 0.0)
    monkeypatch.setattr(advanced_config, "_ENV_LOCKED", {})
    return path


def test_uses_local_sandbox_default_when_file_is_absent(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(
        advanced_config.build_info,
        "DEFAULT_SANDBOX_IMAGE",
        "lumen-sandbox:latest",
    )

    assert advanced_config.load_advanced_config()["sandbox_image"] == "lumen-sandbox:latest"


def test_uses_stored_sandbox_image_verbatim(tmp_path, monkeypatch):
    path = _isolate(tmp_path, monkeypatch)
    path.write_text(json.dumps({"sandbox_image": "custom-sandbox:7"}))

    assert advanced_config.load_advanced_config()["sandbox_image"] == "custom-sandbox:7"


def test_rejects_invalid_values_on_save(tmp_path, monkeypatch):
    import pytest
    _isolate(tmp_path, monkeypatch)
    for update in [{'container_cpus': 'nan'}, {'max_file_list_entries': -1},
                   {'max_file_preview_bytes': True}, {'container_memory': ''}]:
        with pytest.raises(ValueError):
            advanced_config.save_advanced_config(update)


def test_limits_match_worker_capabilities(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    result = advanced_config.save_advanced_config({
        'max_upload_bytes': 200*1024*1024, 'max_file_list_entries': 10000,
        'max_file_preview_bytes': 20*1024*1024, 'container_idle_timeout': 0})
    assert result['max_upload_bytes'] == 50*1024*1024
    assert result['max_file_preview_bytes'] == 4*1024*1024
    assert result['max_file_list_entries'] == 5000
    assert result['container_idle_timeout'] == 0


def test_loaded_settings_cannot_mutate_cached_sandbox_limits(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    loaded = advanced_config.load_advanced_config()
    loaded["container_cpus"] = "9"
    assert advanced_config.load_advanced_config()["container_cpus"] == "1"


def test_changed_config_path_is_not_hidden_by_cache(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    advanced_config.save_advanced_config({"container_cpus": "2"})
    monkeypatch.setattr(advanced_config, "ADVANCED_CONFIG_FILE", tmp_path / "other" / "advanced.json")
    assert advanced_config.load_advanced_config()["container_cpus"] == "1"
    advanced_config.save_advanced_config({"container_cpus": "3"})
    assert advanced_config.load_advanced_config()["container_cpus"] == "3"


def test_invalid_environment_override_preserves_valid_saved_value(tmp_path, monkeypatch):
    path = _isolate(tmp_path, monkeypatch)
    path.write_text(json.dumps({"container_cpus": "2"}))
    monkeypatch.setattr(advanced_config, "_ENV_LOCKED", {"container_cpus": "invalid"})
    assert advanced_config.load_advanced_config()["container_cpus"] == "2"


def test_legacy_network_values_are_ignored(tmp_path, monkeypatch):
    path = _isolate(tmp_path, monkeypatch)
    path.write_text(json.dumps({'container_network': 'host'}))
    assert 'container_network' not in advanced_config.load_advanced_config()
    assert 'container_network' not in advanced_config.save_advanced_config({'container_network': 'none'})
