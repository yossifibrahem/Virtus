"""Workspace file and image routes."""
from __future__ import annotations

import re

from flask import Blueprint, jsonify, request, send_file

import store
import workspace_service
import container_files
import tool_media

blueprint = Blueprint("files", __name__)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _bad_conv_id(conv_id: str):
    if not _UUID_RE.match(conv_id):
        return jsonify({"error": "Not found"}), 404
    return None


def _json_result(result: tuple[dict, int]):
    payload, status = result
    return jsonify(payload), status


@blueprint.route("/api/conversations/<conv_id>/files", methods=["GET", "POST"])
def conversation_files(conv_id: str):
    if err := _bad_conv_id(conv_id):
        return err
    if request.method == "GET":
        return _json_result(container_files.conversation(conv_id, "list", request.args.get("path", "")))

    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "No files uploaded"}), 400
    return _json_result(container_files.uploads(conv_id, files))


@blueprint.route("/api/conversations/<conv_id>/files/content", methods=["GET"])
def get_conversation_file_content(conv_id: str):
    if err := _bad_conv_id(conv_id):
        return err
    return _json_result(container_files.conversation(conv_id, "preview", request.args.get("path", "")))


@blueprint.route("/api/conversations/<conv_id>/files/download", methods=["GET"])
def download_conversation_file(conv_id: str):
    if err := _bad_conv_id(conv_id):
        return err
    return container_files.download_response(*container_files.conversation(conv_id, "download", request.args.get("path", "")))


@blueprint.route("/api/folders/<folder_id>/files", methods=["GET"])
def folder_files(folder_id: str):
    if err := _bad_conv_id(folder_id):
        return err
    return _json_result(container_files.folder(folder_id, "list", request.args.get("path", "")))


@blueprint.route("/api/folders/<folder_id>/files/content", methods=["GET"])
def get_folder_file_content(folder_id: str):
    if err := _bad_conv_id(folder_id):
        return err
    return _json_result(container_files.folder(folder_id, "preview", request.args.get("path", "")))


@blueprint.route("/api/folders/<folder_id>/files/download", methods=["GET"])
def download_folder_file(folder_id: str):
    if err := _bad_conv_id(folder_id):
        return err
    return container_files.download_response(*container_files.folder(folder_id, "download", request.args.get("path", "")))


@blueprint.route("/api/images", methods=["POST"])
def upload_image():
    body = request.get_json(silent=True) or {}
    try:
        name = store.save_image(body.get("data", ""), body.get("media_type", "image/png"))
        return jsonify({"ref": name, "url": f"/api/images/{name}"})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400


@blueprint.route("/api/images/<name>", methods=["GET"])
def serve_image(name: str):
    path = store.get_image_path(name)
    if not path:
        return jsonify({"error": "Not found"}), 404
    ext = name.rsplit(".", 1)[-1]
    return send_file(path, mimetype=f"image/{'jpeg' if ext == 'jpeg' else ext}")


@blueprint.route("/api/conversations/<conv_id>/tool-media/<ref>")
def tool_image(conv_id, ref):
    if err := _bad_conv_id(conv_id):
        return err
    try:
        return send_file(tool_media.path(conv_id, ref), mimetype="image/jpeg")
    except ValueError:
        return jsonify({"error": "Image not found"}), 404
