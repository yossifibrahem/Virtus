"""Isolated persistence, mocked startup, and shared file helpers."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_persistent_pools(monkeypatch):
    """Close worker threads before monkeypatch restores test doubles."""
    import mcp_service
    close_pools = mcp_service.close_all_persistent_pools
    close_pools()
    monkeypatch.setattr("mcp_service._persistent_pools", {})
    yield
    close_pools()


@pytest.fixture
def tmp_virtus(tmp_path):
    """Paths used by the isolated database and fake Docker volume worker."""
    containers_dir = tmp_path / "containers"
    containers_dir.mkdir()

    database_file = tmp_path / "virtus.sqlite3"


    return {
        "database_file": database_file,
        "containers_dir": containers_dir,
        "root": tmp_path,
    }


@pytest.fixture
def image_chat(tmp_virtus):
    import store
    return store.create("Images")["id"]


@pytest.fixture
def app(tmp_virtus, monkeypatch):
    """Create a test app without Docker startup checks."""
    import app as app_module

    ready_status = app_module.runtime_requirements.RequirementStatus(
        ok=True,
        code="ok",
        title="Virtus is ready",
        message="Docker is running and the sandbox image is available.",
        action="continue",
        image="virtus-sandbox",
    )

    monkeypatch.setattr(
        app_module.runtime_requirements,
        "check_requirements",
        lambda: ready_status,
    )

    flask_app = app_module.create_app()

    flask_app.config["TESTING"] = True
    flask_app.config["DISABLE_REQUEST_SECURITY"] = True
    return flask_app


@pytest.fixture
def client(app):
    """Flask test client wired to the isolated test app."""
    return app.test_client()


def make_png_bytes() -> bytes:
    """Return a valid 1×1 red PNG as raw bytes."""
    return (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def make_png_b64() -> str:
    """Return a valid 1×1 PNG as a base64 string (no line breaks)."""
    import base64
    return base64.b64encode(make_png_bytes()).decode()


@pytest.fixture(autouse=True)
def _isolate_persistent_state(tmp_path, monkeypatch):
    import app as app_module
    import store
    import memory_service
    import advanced_config
    import container_service
    monkeypatch.setattr(container_service, 'start_reaper', lambda: None)
    monkeypatch.setattr(app_module, '_register_shutdown_cleanup', lambda: None)
    monkeypatch.setattr(store, 'DATABASE_FILE', tmp_path / 'virtus.sqlite3')
    monkeypatch.setattr(container_service, '_last_used', {})
    monkeypatch.setattr(memory_service, 'MEMORY_FILE', tmp_path / 'memory.json')
    monkeypatch.setattr(advanced_config, 'ADVANCED_CONFIG_FILE', tmp_path / 'advanced_config.json')
    advanced_config._invalidate_cache()


@pytest.fixture(autouse=True)
def mock_volume_storage(monkeypatch, tmp_path, request):
    if request.node.path.name == 'test_builtin_docker.py':
        return
    import container_service
    import storage_safeguards
    import sandbox_files
    import subprocess
    import json
    def probe(args, **kwargs):
        if '--usage' not in args:
            raise AssertionError(f'Unexpected Docker call: {args}')
        runtime_id = args[2].removeprefix(container_service.CONTAINER_PREFIX)
        root = tmp_path / 'containers' / runtime_id
        root.mkdir(parents=True, exist_ok=True)
        previous = sandbox_files.ROOT
        try:
            sandbox_files.ROOT = root
            result = sandbox_files.storage_usage(int(args[-1]))
        finally:
            sandbox_files.ROOT = previous
        return subprocess.CompletedProcess(args, 0, json.dumps(result), '')
    if request.node.name.startswith(('test_turn_tool_budget', 'test_memory_disable', 'test_turn_wall_budget')) or request.node.path.name not in ('test_container_service.py', 'test_runtime_removal.py', 'test_app_shutdown.py', 'test_storage_safeguards.py', 'test_architecture_decisions.py'):
        monkeypatch.setattr(storage_safeguards.container_service, 'ensure_container', lambda rid: None)
        monkeypatch.setattr(storage_safeguards.container_service, '_run', probe)
