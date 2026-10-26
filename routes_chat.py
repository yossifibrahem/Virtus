"""Chat streaming, cancel, approve, settings, and models routes."""
from __future__ import annotations

import threading
import uuid
import re
import store

from flask import Blueprint, jsonify, request, session, abort

import app_config
import advanced_config
import chat_turn_service
import streaming as stream_module

blueprint = Blueprint("chat", __name__)

# stream_id -> cancellation event for POST /api/chat/cancel
_cancel_events: dict[str, threading.Event] = {}

# stream_id -> replayable state for reconnect/reload while a backend turn is active
_active_streams: dict[str, dict] = {}
_registry_lock = threading.RLock()


def _body() -> dict:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        abort(400, description="Expected a JSON object")
    return body


def _stream_state(stream_id: str, conv_id: str = "") -> dict:
    if state := _active_streams.get(stream_id):
        return state
    state = {
        "stream_id": stream_id,
        "conv_id": conv_id,
        "events": [],
        "approvals": {},
        "done": False,
        "started": False,
        "lock": threading.Lock(),
        "condition": threading.Condition(threading.Lock()),
    }
    _active_streams[stream_id] = state
    return state


def _publish_stream_event(state: dict, payload: dict) -> None:
    # Encode once for all readers and freeze mutable turn histories for replay.
    event = stream_module.sse_event(payload)
    with state["condition"]:
        call_id = payload.get("call_id")
        if payload.get("type") == "tool_approval_required":
            state["approvals"][call_id] = len(state["events"])
        elif payload.get("type") == "tool_result":
            index = state["approvals"].pop(call_id, None)
            if index is not None:
                # Keep replay cursors stable without asking again for completed calls.
                state["events"][index] = ": approval resolved\n\n"
        state["events"].append(event)
        state["condition"].notify_all()


def _finish_stream_state(state: dict) -> None:
    with state["condition"]:
        state["done"] = True
        state["condition"].notify_all()
    with _registry_lock:
        _active_streams.pop(state.get("stream_id"), None)


def _run_chat_turn(state: dict, body: dict, cancel_event: threading.Event) -> None:
    stream_id = state["stream_id"]
    publish = lambda payload: _publish_stream_event(state, payload)
    try:
        chat_turn_service.run_persistent_chat_turn(body, cancel_event, stream_id, publish)
    finally:
        _cancel_events.pop(stream_id, None)
        _finish_stream_state(state)


@blueprint.route("/api/chat/stream", methods=["POST"])
def chat_stream():
    body = _body()
    stream_id = body.get("stream_id") or str(uuid.uuid4())
    conv_id = body.get("conv_id", "")
    attach_only = bool(body.get("attach"))
    owner = session.get("client_id", "test")
    if not isinstance(stream_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', stream_id):
        return jsonify({"error": "Invalid stream id"}), 400
    if not isinstance(conv_id, str) or not re.fullmatch(r'[a-fA-F0-9-]{36}', conv_id) or not store.load(conv_id):
        return jsonify({"error": "Conversation not found"}), 404
    with _registry_lock:
        existing = _active_streams.get(stream_id)
        if existing and (existing.get("owner") != owner or existing.get("conv_id") != conv_id):
            return jsonify({"error": "Stream not found"}), 404
        if not attach_only and any(s.get("conv_id") == conv_id and not s.get("done") and s.get("stream_id") != stream_id for s in _active_streams.values()):
            return jsonify({"error": "Conversation already has an active turn"}), 409

        if attach_only and stream_id not in _active_streams:
            return jsonify({"error": "Stream not found"}), 404

        cancel_event = _cancel_events.setdefault(stream_id, threading.Event())
        state = _stream_state(stream_id, conv_id)
        state["owner"] = owner

    if not attach_only:
        with state["lock"]:
            if not state["started"]:
                state["started"] = True
                threading.Thread(
                    target=_run_chat_turn,
                    args=(state, body, cancel_event),
                    daemon=True,
                ).start()

    def event_stream():
        cursor = 0
        while True:
            with state["condition"]:
                if cursor >= len(state["events"]) and not state["done"]:
                    state["condition"].wait(timeout=15)
                pending = state["events"][cursor:]
                cursor += len(pending)
                done = state["done"] and cursor >= len(state["events"])

            for event in pending:
                yield event
            if not pending and not done:
                yield ": keep-alive\n\n"
            if done:
                yield "data: [DONE]\n\n"
                break

    return stream_module.make_streaming_response(event_stream())


@blueprint.route("/api/chat/cancel", methods=["POST"])
def chat_cancel():
    stream_id = _body().get("stream_id", "")
    if not isinstance(stream_id, str):
        return jsonify({"error": "Invalid stream id"}), 400
    state = _active_streams.get(stream_id)
    if not state or state.get("owner") != session.get("client_id", "test"):
        return jsonify({"error": "Stream not found"}), 404
    event = _cancel_events.get(stream_id)
    if not event:
        return jsonify({"ok": False, "reason": "stream not found"}), 404
    event.set()
    return jsonify({"ok": True})


@blueprint.route("/api/chat/approve", methods=["POST"])
def chat_approve():
    body = _body()
    stream_id = body.get("stream_id", "")
    if not isinstance(stream_id, str):
        return jsonify({"error": "Invalid stream id"}), 400
    state = _active_streams.get(stream_id)
    if not state or state.get("owner") != session.get("client_id", "test"):
        return jsonify({"error": "Stream not found"}), 404
    call_id = body.get("call_id", "")
    approved = bool(body.get("approved", False))
    chat_turn_service.resolve_tool_approval(stream_id, call_id, approved)
    return jsonify({"ok": True})


@blueprint.route("/api/settings", methods=["GET"])
def get_api_settings():
    return jsonify(app_config.public_config())


@blueprint.route("/api/settings", methods=["POST"])
def save_api_settings():
    try:
        return jsonify(app_config.save_config(_body()))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@blueprint.route("/api/container-settings", methods=["GET"])
def get_container_settings():
    return jsonify(advanced_config.public_advanced_config())


@blueprint.route("/api/container-settings", methods=["POST"])
def save_container_settings():
    try:
        return jsonify(advanced_config.save_advanced_config(_body()))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@blueprint.route("/api/models", methods=["POST"])
def fetch_models():
    try:
        client = chat_turn_service.openai_client(_body())
        try:
            models = sorted(m.id for m in client.models.list())
        finally:
            client.close()
        return jsonify({"models": models})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
