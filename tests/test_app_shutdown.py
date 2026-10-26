from __future__ import annotations

import threading
import time

import app


def test_shutdown_starts_mcp_and_container_cleanup_concurrently(monkeypatch):
    mcp_started = threading.Event()
    containers_started = threading.Event()
    release = threading.Event()

    def close_mcp(*, deadline):
        mcp_started.set()
        assert release.wait(timeout=2)

    def stop_containers(*, deadline):
        containers_started.set()
        assert release.wait(timeout=2)
        return []

    monkeypatch.setattr(app, "_shutdown_done", False)
    monkeypatch.setattr(app.mcp_service, "close_all_persistent_pools", close_mcp)
    monkeypatch.setattr(app.container_service, "stop_all_containers", stop_containers)

    worker = threading.Thread(
        target=app._shutdown_containers,
        kwargs={"deadline": time.monotonic() + 2},
    )
    worker.start()
    assert mcp_started.wait(timeout=1)
    assert containers_started.wait(timeout=1)
    release.set()
    worker.join(timeout=2)

    assert not worker.is_alive()


def test_shutdown_is_idempotent(monkeypatch):
    calls = []
    monkeypatch.setattr(app, "_shutdown_done", False)
    monkeypatch.setattr(app.mcp_service, "close_all_persistent_pools", lambda **kwargs: calls.append("mcp"))
    monkeypatch.setattr(app.container_service, "stop_all_containers", lambda **kwargs: calls.append("docker") or [])

    app._shutdown_containers(deadline=time.monotonic() + 1)
    app._shutdown_containers(deadline=time.monotonic() + 1)

    assert sorted(calls) == ["docker", "mcp"]


def test_startup_keeps_runtime_for_empty_folder(tmp_lumen, monkeypatch):
    import store
    folder = store.create_folder('Project')
    known = []
    monkeypatch.setattr(app.container_service, 'cleanup_stale', lambda ids: known.extend(ids) or [])
    app._cleanup_stale_containers()
    assert known == [f"folder_{folder['id']}"]
