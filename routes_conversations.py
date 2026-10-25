"""Conversation CRUD routes."""
from __future__ import annotations

import re

from flask import Blueprint, jsonify, request

import container_service
import store

blueprint = Blueprint("conversations", __name__)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _body() -> dict:
    return request.get_json(silent=True) or {}


def _bad_conv_id(conv_id: str):
    if not _UUID_RE.match(conv_id):
        return jsonify({"error": "Not found"}), 404
    return None


@blueprint.route("/api/conversations", methods=["GET"])
def list_conversations():
    return jsonify(store.list_all())


@blueprint.route("/api/conversations", methods=["POST"])
def create_conversation():
    body = _body()
    folder_id = body.get("folder_id")
    if folder_id and not store.get_folder(folder_id):
        return jsonify({"error": "Folder not found"}), 404
    return jsonify(store.create(body.get("title", "New Conversation"), folder_id)), 201


@blueprint.route("/api/folders", methods=["GET"])
def list_folders():
    return jsonify(store.list_folders())


@blueprint.route("/api/folders", methods=["POST"])
def create_folder():
    body = _body()
    return jsonify(store.create_folder(
        body.get("name", "New Folder"),
        body.get("system_prompt", ""),
    )), 201


@blueprint.route("/api/folders/<folder_id>", methods=["PUT"])
def update_folder(folder_id: str):
    if err := _bad_conv_id(folder_id):
        return err
    body = _body()
    folder = store.update_folder(
        folder_id,
        name=body.get("name") if "name" in body else None,
        system_prompt=body.get("system_prompt") if "system_prompt" in body else None,
    )
    return jsonify(folder) if folder else (jsonify({"error": "Not found"}), 404)


@blueprint.route("/api/folders/<folder_id>", methods=["DELETE"])
def delete_folder(folder_id: str):
    if err := _bad_conv_id(folder_id):
        return err
    if not store.get_folder(folder_id):
        return jsonify({"error": "Not found"}), 404
    if any((store.load(c["id"]) or {}).get("active_stream_id") for c in store.folder_conversations(folder_id)):
        return jsonify({"error": "Stop active chats before deleting this Folder"}), 409
    runtime_id = f"folder_{folder_id}"
    conversation_ids = [
        summary["id"] for summary in store.folder_conversations(folder_id)
    ]
    for conversation_id in conversation_ids:
        store.delete(conversation_id)
    store.delete_folder(folder_id)
    container_service.stop_container(runtime_id)
    container_service.delete_workspace(runtime_id)
    return jsonify({"ok": True, "deleted_conversation_ids": conversation_ids})


@blueprint.route("/api/conversations/<conv_id>", methods=["GET"])
def get_conversation(conv_id: str):
    if err := _bad_conv_id(conv_id):
        return err
    data = store.load(conv_id)
    if not data:
        return jsonify({"error": "Not found"}), 404
    data.setdefault("working_directory", str(store.working_directory(conv_id)))
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
        "workspace": str(store.working_directory(conv_id)),
    })


@blueprint.route("/api/conversations/<conv_id>", methods=["PUT"])
def update_conversation(conv_id: str):
    if err := _bad_conv_id(conv_id):
        return err
    allowed_fields = {"title", "system_prompt", "messages", "displayLog", "folder_id"}
    body = _body()
    data = store.load(conv_id)
    if data is None:
        return jsonify({"error": "Not found"}), 404
    if data.get("active_stream_id") and "folder_id" in body and body["folder_id"] != data.get("folder_id"):
        return jsonify({"error": "Stop the active turn before moving this chat"}), 409
    old_runtime_id = store.runtime_id(conv_id, data)
    if "folder_id" in body and body["folder_id"] and not store.get_folder(body["folder_id"]):
        return jsonify({"error": "Folder not found"}), 404
    for key in allowed_fields:
        if key in body:
            if data.get("active_stream_id") and key in {"messages", "displayLog"}:
                continue
            if key == "folder_id" and not body[key]:
                data.pop(key, None)
            else:
                data[key] = body[key]
    data["id"] = conv_id
    new_runtime_id = store.runtime_id(conv_id, data)
    data["working_directory"] = str(container_service.conversation_workspace(new_runtime_id))
    saved = store.save(conv_id, data)
    if old_runtime_id != new_runtime_id and not any(
        store.runtime_id(item["id"]) == old_runtime_id for item in store.list_all()
    ):
        container_service.stop_container(old_runtime_id)
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
    store.delete(conv_id)
    if not any(store.runtime_id(item["id"]) == runtime_id for item in store.list_all()):
        container_service.stop_container(runtime_id)
        container_service.delete_workspace(runtime_id)
    return jsonify({"ok": True})


@blueprint.route("/api/danger/delete-all", methods=["POST"])
def danger_delete_all():
    """Delete every conversation, its container, and its workspace directory."""
    conversations = store.list_all()
    if any((store.load(c["id"]) or {}).get("active_stream_id") for c in conversations):
        return jsonify({"error": "Stop active chats before deleting conversations"}), 409
    errors = []
    for conv in conversations:
        conv_id = conv.get("id")
        if not conv_id:
            continue
        try:
            runtime_id = store.runtime_id(conv_id)
            store.delete(conv_id)
            container_service.stop_container(runtime_id)
            container_service.delete_workspace(runtime_id)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{conv_id}: {exc}")
    if errors:
        return jsonify({"ok": False, "errors": errors}), 500
    for folder in store.list_folders():
        store.delete_folder(folder["id"])
    return jsonify({"ok": True, "deleted": len(conversations)})
