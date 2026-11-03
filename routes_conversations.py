"""Conversation CRUD routes."""
from __future__ import annotations

import re
import subprocess

from flask import Blueprint, abort, jsonify, request

import container_service
import store

blueprint = Blueprint("conversations", __name__)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _body(*text_fields: str) -> dict:
    body = request.get_json(silent=True)
    if body is None:
        return {}
    if not isinstance(body, dict):
        abort(400, description="Expected a JSON object")
    for field in text_fields:
        if field in body and body[field] is not None and not isinstance(body[field], str):
            abort(400, description=f"{field} must be a string or null")
    return body


def _bad_conv_id(conv_id: str):
    if not _UUID_RE.match(conv_id):
        return jsonify({"error": "Not found"}), 404
    return None


def _delete_runtime(runtime_id: str, *, remove_workspace: bool = True):
    """Confirm Docker access is revoked before removing the owned workspace."""
    try:
        container_service.stop_container(runtime_id, strict=True)
        if remove_workspace:
            container_service.delete_workspace(runtime_id)
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        return jsonify({"error": f"Could not delete the sandbox. {exc}"}), 503
    return None


@blueprint.route("/api/conversations", methods=["GET"])
def list_conversations():
    return jsonify(store.list_all())


@blueprint.route("/api/conversations/activity", methods=["GET"])
def conversation_activity():
    return jsonify(store.list_active())


@blueprint.route("/api/conversations", methods=["POST"])
def create_conversation():
    body = _body("title", "folder_id")
    folder_id = body.get("folder_id")
    if folder_id and not store.get_folder(folder_id):
        return jsonify({"error": "Folder not found"}), 404
    return jsonify(store.create(body.get("title", "New Conversation"), folder_id)), 201


@blueprint.route("/api/folders", methods=["GET"])
def list_folders():
    return jsonify(store.list_folders())


@blueprint.route("/api/folders", methods=["POST"])
def create_folder():
    body = _body("name", "system_prompt")
    return jsonify(store.create_folder(
        body.get("name", "New Folder"),
        body.get("system_prompt", ""),
    )), 201


@blueprint.route("/api/folders/<folder_id>", methods=["PUT"])
def update_folder(folder_id: str):
    if err := _bad_conv_id(folder_id):
        return err
    body = _body("name", "system_prompt")
    changes = {key: body[key] for key in ("name", "system_prompt") if key in body}
    updated = store.update_folder(folder_id, **changes)
    if updated is None:
        return jsonify({"error": "Not found"}), 404
    return jsonify(updated)


@blueprint.route("/api/folders/<folder_id>", methods=["DELETE"])
def delete_folder(folder_id: str):
    if err := _bad_conv_id(folder_id):
        return err
    if not store.get_folder(folder_id):
        return jsonify({"error": "Not found"}), 404
    conversations = store.folder_conversations(folder_id)
    if any(c.get("active_stream_id") for c in conversations):
        return jsonify({"error": "Stop active chats before deleting this Folder"}), 409
    runtime_id = f"folder_{folder_id}"
    if err := _delete_runtime(runtime_id):
        return err
    conversation_ids = [c["id"] for c in conversations]
    store.delete_folder(folder_id)
    return jsonify({"ok": True, "deleted_conversation_ids": conversation_ids})


@blueprint.route("/api/conversations/<conv_id>", methods=["GET"])
def get_conversation(conv_id: str):
    if err := _bad_conv_id(conv_id):
        return err
    data = store.load(conv_id)
    if not data:
        return jsonify({"error": "Not found"}), 404
    return jsonify(data)


@blueprint.route("/api/conversations/<conv_id>/workspace", methods=["GET"])
def get_conversation_workspace(conv_id: str):
    if err := _bad_conv_id(conv_id):
        return err
    return jsonify({"working_directory": str(store.working_directory(conv_id))})


@blueprint.route("/api/conversations/<conv_id>/container", methods=["GET"])
def get_container_status(conv_id: str):
    if err := _bad_conv_id(conv_id):
        return err
    runtime_id = store.runtime_id(conv_id)
    return jsonify({
        "conv_id": conv_id,
        "runtime_id": runtime_id,
        "container_name": container_service.container_name(runtime_id),
        "status": container_service.get_status(runtime_id),
        "workspace": '/workspace',
        "volume_name": container_service.workspace_volume(runtime_id),
    })


@blueprint.route("/api/conversations/<conv_id>", methods=["PUT"])
def update_conversation(conv_id: str):
    if err := _bad_conv_id(conv_id):
        return err
    allowed_fields = {"title", "system_prompt", "messages", "displayLog", "folder_id"}
    body = _body("title", "system_prompt", "folder_id")
    for key in ("messages", "displayLog"):
        if key in body and (not isinstance(body[key], list) or any(not isinstance(item, dict) for item in body[key])):
            return jsonify({"error": f"{key} must be an array of objects"}), 400
    data = store.load(conv_id)
    if data is None:
        return jsonify({"error": "Not found"}), 404
    if data.get("active_stream_id") and "folder_id" in body and body["folder_id"] != data.get("folder_id"):
        return jsonify({"error": "Stop the active turn before moving this chat"}), 409
    old_runtime_id = store.runtime_id(conv_id, data)
    if "folder_id" in body and body["folder_id"] and not store.get_folder(body["folder_id"]):
        return jsonify({"error": "Folder not found"}), 404
    changes = {key: body[key] for key in allowed_fields if key in body}
    remove = ()
    if "folder_id" in changes and not changes["folder_id"]:
        changes.pop("folder_id")
        remove = ("folder_id",)
    new_folder_id = body.get("folder_id", data.get("folder_id"))
    new_runtime_id = store.runtime_id(conv_id, {"folder_id": new_folder_id})
    if "folder_id" in body:
        changes["working_directory"] = '/workspace'
    remove_old_runtime = old_runtime_id != new_runtime_id and old_runtime_id == conv_id
    if remove_old_runtime:
        if err := _delete_runtime(old_runtime_id, remove_workspace=False):
            return err
    saved = store.update(conv_id, changes, remove=remove, protect_active_history=True)
    if saved is None:
        return jsonify({"error": "Not found"}), 404
    if remove_old_runtime:
        container_service.delete_workspace(old_runtime_id)
    return jsonify(saved)


@blueprint.route("/api/conversations/<conv_id>", methods=["DELETE"])
def delete_conversation(conv_id: str):
    if err := _bad_conv_id(conv_id):
        return err
    data = store.load(conv_id)
    if not data:
        return jsonify({"error": "Not found"}), 404
    if data.get("active_stream_id"):
        return jsonify({"error": "Stop the active turn before deleting this chat"}), 409
    runtime_id = store.runtime_id(conv_id, data)
    if runtime_id == conv_id:
        if err := _delete_runtime(runtime_id):
            return err
    store.delete(conv_id)
    return jsonify({"ok": True})


@blueprint.route("/api/danger/delete-all", methods=["POST"])
def danger_delete_all():
    """Delete every conversation, its container, and its workspace volume."""
    conversations = store.list_all()
    if any(c.get("active_stream_id") for c in conversations):
        return jsonify({"error": "Stop active chats before deleting conversations"}), 409
    runtime_ids = {store.runtime_id(c["id"], c) for c in conversations}
    folders = store.list_folders()
    runtime_ids.update(f"folder_{folder['id']}" for folder in folders)
    errors = []
    for runtime_id in runtime_ids:
        try:
            container_service.stop_container(runtime_id, strict=True)
        except Exception as exc:
            errors.append(f"{runtime_id}: {exc}")
    if errors:
        return jsonify({"ok": False, "errors": errors}), 500
    for runtime_id in runtime_ids:
        try:
            container_service.delete_workspace(runtime_id)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            return jsonify({"ok": False, "errors": [f"{runtime_id}: {exc}"]}), 500
    store.delete_all([conv["id"] for conv in conversations], [folder["id"] for folder in folders])
    return jsonify({"ok": True, "deleted": len(conversations)})
