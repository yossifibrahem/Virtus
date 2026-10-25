"""
Lumen Chatbot — Flask entry point.

Wires together the app factory, CORS, and route blueprints. Docker
availability and the sandbox image are checked at startup, but unmet
requirements are shown in a friendly browser setup screen instead of
terminating the process.
"""
import atexit
import logging
import signal
import os
import secrets
import sys
import threading
import time

from flask import Flask
from flask_cors import CORS

import build_info
import routes_conversations
import routes_chat
import routes_mcp
import routes_files
import routes_startup
import container_service
import mcp_service
import runtime_requirements

log = logging.getLogger(__name__)

# Track whether we have already registered the shutdown handler so that
# multiple create_app() calls in tests do not stack duplicate registrations.
_shutdown_registered = False
_shutdown_done = False
_shutdown_lock = threading.Lock()
SHUTDOWN_CLEANUP_SECONDS = 10


def create_app() -> Flask:
    startup_status = runtime_requirements.check_requirements()
    _log_startup_requirement_status(startup_status)
    assets_root = build_info.resource_root()
    app = Flask(
        __name__,
        template_folder=str(assets_root / "templates"),
        static_folder=str(assets_root / "static"),
    )
    app.config.update(
        SECRET_KEY=os.getenv("LUMEN_SECRET_KEY") or secrets.token_bytes(32),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
    )
    app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("LUMEN_MAX_CONTENT_LENGTH", str(60 * 1024 * 1024)))
    allowed_origins = [
        origin.strip()
        for origin in os.getenv(
            "LUMEN_CORS_ORIGINS",
            "http://localhost:8080,http://127.0.0.1:8080,"
            "http://localhost:38492,http://127.0.0.1:38492",
        ).split(",")
        if origin.strip()
    ]
    CORS(app, origins=allowed_origins)
    import request_security
    request_security.install(app)
    app.register_blueprint(routes_startup.blueprint)
    app.register_blueprint(routes_conversations.blueprint)
    app.register_blueprint(routes_chat.blueprint)
    app.register_blueprint(routes_mcp.blueprint)
    app.register_blueprint(routes_files.blueprint)
    container_service.start_reaper()
    if startup_status.ok:
        _cleanup_stale_containers()
    else:
        log.info("[startup] stale container cleanup skipped until requirements are ready")
    _register_shutdown_cleanup()
    return app


def _register_shutdown_cleanup() -> None:
    """Register container stop hooks for normal exit and SIGTERM.

    ``atexit`` fires on ``sys.exit()`` and ``KeyboardInterrupt`` (Ctrl-C) but
    *not* on ``SIGTERM``.  The explicit ``SIGTERM`` handler covers production
    deployments (gunicorn, systemd, Docker stop) where the process receives
    SIGTERM rather than a keyboard interrupt.
    """
    global _shutdown_registered
    if _shutdown_registered:
        return
    _shutdown_registered = True

    atexit.register(_shutdown_containers)

    original_sigterm = signal.getsignal(signal.SIGTERM)

    def _sigterm_handler(signum, frame):
        _shutdown_containers()
        # Restore the previous handler and re-raise so gunicorn / the shell
        # can observe the signal and perform its own teardown.
        signal.signal(signal.SIGTERM, original_sigterm)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _sigterm_handler)


def _log_startup_requirement_status(status) -> None:
    """Log runtime requirement state without aborting the web UI startup."""
    if status.ok:
        log.info("[startup] %s", status.message)
    else:
        log.warning("[startup] %s %s", status.title, status.details)


def _cleanup_stale_containers() -> None:
    """Remove lumen-chat-* Docker containers whose conversation no longer exists."""
    try:
        import store
        known_ids = list({store.runtime_id(c["id"]) for c in store.list_all()})
        removed = container_service.cleanup_stale(known_ids)
        if removed:
            log.info("[startup] removed %d stale container(s): %s", len(removed), removed)
    except Exception as exc:
        log.warning("[startup] stale container cleanup skipped: %s", exc)


def _shutdown_containers(*, deadline: float | None = None) -> None:
    """Kill all running lumen-chat-* containers on app shutdown.

    Non-fatal: a failure to reach Docker (e.g. Docker daemon itself was
    stopped) is logged as a warning and does not prevent the process from
    exiting cleanly.  The guard prevents double execution when both the
    SIGTERM handler and atexit fire in the same shutdown sequence.
    """
    global _shutdown_done
    with _shutdown_lock:
        if _shutdown_done:
            return
        _shutdown_done = True

    shutdown_deadline = deadline or (time.monotonic() + SHUTDOWN_CLEANUP_SECONDS)

    def _close_mcp() -> None:
        try:
            mcp_service.close_all_persistent_pools(deadline=shutdown_deadline)
        except Exception as exc:
            log.warning("[shutdown] MCP pool teardown failed: %s", exc)

    def _stop_containers() -> None:
        try:
            stopped = container_service.stop_all_containers(deadline=shutdown_deadline)
            if stopped:
                log.info("[shutdown] stopped %d container(s): %s", len(stopped), stopped)
        except Exception as exc:
            log.warning("[shutdown] container stop failed: %s", exc)

    workers = [
        threading.Thread(target=_close_mcp, name="lumen-shutdown-mcp", daemon=True),
        threading.Thread(target=_stop_containers, name="lumen-shutdown-containers", daemon=True),
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=max(0.0, shutdown_deadline - time.monotonic()))
    unfinished = [worker.name for worker in workers if worker.is_alive()]
    if unfinished:
        log.warning("[shutdown] deadline reached with cleanup still running: %s", unfinished)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    host = os.getenv("LUMEN_HOST", "127.0.0.1")
    port = int(os.getenv("LUMEN_PORT", "8080"))
    debug = os.getenv("LUMEN_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
    create_app().run(debug=debug, host=host, port=port, threaded=True)
