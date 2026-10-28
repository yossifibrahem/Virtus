"""Startup and runtime-environment routes.

Handles Docker availability checks, sandbox image builds, and the setup
screen served before the main app is ready. Kept separate from conversation
routes because these routes are about the host environment, not user data.

Routes
------
GET  /                                      – app shell or setup screen
GET  /health                                – liveness probe
GET  /api/startup/requirements              – current requirement status (JSON)
POST /api/startup/build-sandbox-image/stream – protected streaming build log (SSE)
POST /api/startup/start-docker              – explicitly launch Docker Desktop
"""
from __future__ import annotations

import hmac
import ipaddress
import json
import secrets
import time
from urllib.parse import urlsplit

from flask import Blueprint, Response, jsonify, render_template, request, session, stream_with_context

import runtime_requirements
import build_info

blueprint = Blueprint("startup", __name__)
_STARTUP_TOKEN_KEY = "_virtus_startup_token"
_STARTUP_TOKEN_ISSUED_KEY = "_virtus_startup_token_issued"
_STARTUP_TOKEN_TTL_SECONDS = 30 * 60


def _is_loopback_host(hostname: str | None) -> bool:
    normalized = (hostname or "").rstrip(".").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _effective_port(parts) -> int | None:
    try:
        if parts.port is not None:
            return parts.port
    except ValueError:
        return None
    return 443 if parts.scheme == "https" else 80 if parts.scheme == "http" else None


def _is_same_loopback_origin() -> bool:
    """Accept browser actions only from this exact loopback web origin."""
    origin = request.headers.get("Origin", "").strip()
    if not origin or origin == "null":
        return False
    fetch_site = request.headers.get("Sec-Fetch-Site", "").strip().lower()
    if fetch_site and fetch_site not in {"same-origin", "none"}:
        return False

    try:
        origin_parts = urlsplit(origin)
        request_parts = urlsplit(f"{request.scheme}://{request.host}")
    except ValueError:
        return False

    if not _is_loopback_host(request_parts.hostname):
        return False
    return (
        origin_parts.scheme == request_parts.scheme
        and (origin_parts.hostname or "").lower() == (request_parts.hostname or "").lower()
        and _effective_port(origin_parts) == _effective_port(request_parts)
    )


def _issue_startup_token() -> str:
    token = secrets.token_urlsafe(32)
    session[_STARTUP_TOKEN_KEY] = token
    session[_STARTUP_TOKEN_ISSUED_KEY] = time.time()
    return token


def _validate_startup_action() -> str | None:
    """Return an error string unless this is a confirmed same-origin action."""
    if not _is_same_loopback_origin():
        return "Startup actions are allowed only from Virtus's local setup page."

    supplied = request.headers.get("X-Virtus-Startup-Token", "")
    expected = session.get(_STARTUP_TOKEN_KEY, "")
    try:
        age = time.time() - float(session.get(_STARTUP_TOKEN_ISSUED_KEY, 0))
    except (TypeError, ValueError):
        age = _STARTUP_TOKEN_TTL_SECONDS + 1
    if (
        not supplied
        or not expected
        or age < 0
        or age > _STARTUP_TOKEN_TTL_SECONDS
        or not hmac.compare_digest(str(supplied), str(expected))
    ):
        return "The startup confirmation expired. Reload the setup page and try again."

    # Sliding expiry lets a user start Docker and then install the tools without
    # losing confirmation while Docker Desktop is still becoming ready.
    session[_STARTUP_TOKEN_ISSUED_KEY] = time.time()
    return None


def _startup_action_denied():
    error = _validate_startup_action()
    if error:
        return jsonify({"error": error}), 403
    return None


@blueprint.route("/")
def index():
    status = runtime_requirements.check_requirements()
    if not status.ok:
        return render_template(
            "startup_requirements.html",
            status=status.as_dict(),
            startup_token=_issue_startup_token(),
        )
    return render_template("index.html")


@blueprint.route("/health")
def health():
    """Minimal liveness probe for container orchestrators and load balancers."""
    return jsonify({
        "ok": True,
        "app": build_info.APP_ID,
        "version": build_info.APP_VERSION,
    })


@blueprint.route("/api/startup/requirements", methods=["GET"])
def startup_requirements():
    status = runtime_requirements.check_requirements()
    http_status = 200 if status.ok else 503
    return jsonify(status.as_dict()), http_status


@blueprint.route("/api/startup/build-sandbox-image/stream", methods=["POST"])
def build_sandbox_image_stream():
    """Stream docker build output line-by-line as SSE.

    Event types emitted:
      log    – one line of docker build stdout/stderr
      done   – build finished successfully; data is a RequirementStatus JSON
      error  – build failed;              data is a RequirementStatus JSON
    """
    denied = _startup_action_denied()
    if denied:
        return denied

    def _generate():
        for event, data in runtime_requirements.build_sandbox_image_stream():
            yield f"event: {event}\ndata: {json.dumps(data)}\n\n"

    return Response(
        stream_with_context(_generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@blueprint.route("/api/startup/start-docker", methods=["POST"])
def start_docker_desktop():
    denied = _startup_action_denied()
    if denied:
        return denied
    status = runtime_requirements.start_docker_desktop()
    http_status = 202 if status.code == "docker_starting" else 500
    return jsonify(status.as_dict()), http_status
