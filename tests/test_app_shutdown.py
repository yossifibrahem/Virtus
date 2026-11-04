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


def test_startup_with_empty_or_alternate_database_preserves_docker(monkeypatch, tmp_path):
    import store
    from unittest.mock import Mock

    docker = Mock(side_effect=AssertionError("Startup must not enumerate or delete Docker resources"))
    monkeypatch.setattr(app.container_service, '_run', docker)
    monkeypatch.setattr(app.runtime_requirements, 'check_requirements', lambda:
        app.runtime_requirements.RequirementStatus(True, 'ok', 'Ready', 'Ready', 'continue', 'sandbox'))
    for index in range(2):
        monkeypatch.setattr(store, 'DATABASE_FILE', tmp_path / f'database-{index}.sqlite3')
        if index:
            store.create_folder('Unrelated folder')
            store.create('Unrelated chat')
        # No ownership inference from either an empty or unrelated database.
        flask_app = app.create_app()
        assert flask_app.test_client().get('/health').status_code == 200
    docker.assert_not_called()


def test_smoke_app_never_accesses_database_docker_or_lifecycle(monkeypatch):
    import store
    from unittest.mock import Mock

    forbidden = Mock(side_effect=AssertionError("Smoke test touched application state"))
    monkeypatch.setenv('VIRTUS_DESKTOP_SMOKE_TEST', '1')
    monkeypatch.setattr(store, '_connection', forbidden)
    monkeypatch.setattr(app.runtime_requirements, 'check_requirements', forbidden)
    monkeypatch.setattr(app.container_service, 'start_reaper', forbidden)
    monkeypatch.setattr(app.container_service, 'stop_all_containers', forbidden)
    monkeypatch.setattr(app.mcp_service, 'close_all_persistent_pools', forbidden)
    monkeypatch.setattr(app, '_register_shutdown_cleanup', forbidden)
    monkeypatch.setattr(app, '_shutdown_done', False)
    flask_app = app.create_app()
    flask_app.config['DISABLE_REQUEST_SECURITY'] = True
    with flask_app.test_client() as client:
        assert client.get('/health').json['version'] == app.build_info.APP_VERSION
        assert client.get('/static/js/app.js').status_code == 200
        assert client.get('/api/conversations').status_code == 404
        assert client.get('/').status_code == 404
    app._shutdown_containers()
    assert app._shutdown_done is False
    forbidden.assert_not_called()
