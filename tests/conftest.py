"""
Shared pytest fixtures for Virtus AI Chat tests.

Key responsibilities:
  - Redirect all filesystem paths (conversations, images, containers) to tmp_path
    so tests never touch ~/.virtus.
  - Create a Flask test app with runtime requirement checks mocked as ready.
  - Expose a `client` fixture for HTTP integration tests.
"""
from __future__ import annotations

import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def _clear_persistent_pools(monkeypatch):
    """Reset the persistent MCP session pool registry before every test.

    The registry is module-level state in mcp_service.  Without this fixture,
    a pool created by one test could leak into the next test, causing
    unexpected reuse or interference.

    Pools must be closed before the registry is replaced, not just discarded.
    Discarding a live pool without closing it leaks its worker thread.
    """
    import mcp_service
    # Close any pools left over from a previous test before wiping the registry.
    mcp_service.close_all_persistent_pools()
    monkeypatch.setattr("mcp_service._persistent_pools", {})


# ---------------------------------------------------------------------------
# Filesystem isolation
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_virtus(tmp_path, monkeypatch):
    """
    Redirect every Virtus storage path to an isolated temp directory.

    Patches module-level constants in `store` and `container_service` so that
    all fixture-scoped tests read and write to `tmp_path` only.

    Returns a dict with the three directory Paths for use in individual tests.
    """
    conv_dir = tmp_path / "conversations"
    images_dir = tmp_path / "images"
    containers_dir = tmp_path / "containers"
    conv_dir.mkdir()
    images_dir.mkdir()
    containers_dir.mkdir()

    monkeypatch.setattr("store.CONVERSATIONS_DIR", conv_dir)
    monkeypatch.setattr("store.IMAGES_DIR", images_dir)
    monkeypatch.setattr("store.FOLDERS_FILE", tmp_path / "folders.json")
    monkeypatch.setattr("container_service.CONTAINERS_ROOT", containers_dir)

    import store
    store.invalidate_index()

    return {
        "conv_dir": conv_dir,
        "images_dir": images_dir,
        "containers_dir": containers_dir,
        "root": tmp_path,
    }


# ---------------------------------------------------------------------------
# Flask app / test client
# ---------------------------------------------------------------------------

@pytest.fixture
def app(tmp_virtus, monkeypatch):
    """
    Create a Flask test application with Docker-backed startup checks mocked out.

    `create_app()` now asks runtime_requirements.check_requirements() for a
    non-exiting status object, then cleans stale containers only when that
    status is ok. Patch the new requirement entry point directly so tests do
    not need Docker or a locally built sandbox image.
    """
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

    with patch("container_service.cleanup_stale", return_value=[]):
        flask_app = app_module.create_app()

    flask_app.config["TESTING"] = True
    flask_app.config["DISABLE_REQUEST_SECURITY"] = True
    return flask_app


@pytest.fixture
def client(app):
    """Flask test client wired to the isolated test app."""
    return app.test_client()


# ---------------------------------------------------------------------------
# Helpers shared across test modules
# ---------------------------------------------------------------------------

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
def isolate_global_settings(tmp_path, monkeypatch):
    import memory_service
    import advanced_config
    import container_service
    monkeypatch.setattr(container_service, '_last_used', {})
    monkeypatch.setattr(memory_service, 'MEMORY_FILE', tmp_path / 'memory.json')
    monkeypatch.setattr(advanced_config, 'ADVANCED_CONFIG_FILE', tmp_path / 'advanced_config.json')
    advanced_config._invalidate_cache()
