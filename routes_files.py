"""Workspace file and image routes."""
from __future__ import annotations

import base64
import re

from flask import Blueprint, Response, jsonify, request

import store
import container_files

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


@blueprint.route("/api/folders/<folder_id>/files", methods=["GET"])
def folder_files(folder_id: str):
    if err := _bad_conv_id(folder_id):
        return err
    return _json_result(container_files.folder(folder_id, "list", request.args.get("path", "")))


@blueprint.route("/api/conversations/<workspace_id>/files/<action>", defaults={"scope": "conversation"})
@blueprint.route("/api/folders/<workspace_id>/files/<action>", defaults={"scope": "folder"})
def workspace_file(scope, workspace_id, action):
    if err := _bad_conv_id(workspace_id):
        return err
    operation = container_files.conversation if scope == "conversation" else container_files.folder
    path = request.args.get("path", "")
    if action == "content":
        return _json_result(operation(workspace_id, "preview", path,
            range=request.args.get("range"), version=request.args.get("version")))
    if action == "download":
        return container_files.download_response(*operation(workspace_id, "download", path))
    if action == "pdf":
        return container_files.pdf_response(*operation(workspace_id, "pdf", path,
            byte_range=request.headers.get("Range"), if_range=request.headers.get("If-Range"),
            if_none_match=request.headers.get("If-None-Match")))
    return jsonify({"error": "Not found"}), 404


@blueprint.route("/api/conversations/<conv_id>/images", methods=["POST"])
def upload_image(conv_id):
    if err := _bad_conv_id(conv_id):
        return err
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Expected a JSON object"}), 400
    try:
        raw = base64.b64decode(body.get("data", ""), validate=True)
        ref = store.save_image(conv_id, raw, body.get("media_type", "image/png"))
    except (ValueError, TypeError) as exc:
        return jsonify({"error": str(exc)}), 400
    if ref is None:
        return jsonify({"error": "Conversation not found"}), 404
    return jsonify({"ref": ref, "url": f"/api/conversations/{conv_id}/images/{ref}"})


@blueprint.route("/api/conversations/<conv_id>/images/<ref>")
@blueprint.route("/api/conversations/<conv_id>/tool-media/<ref>")
def serve_image(conv_id, ref):
    if err := _bad_conv_id(conv_id):
        return err
    image = store.get_image(conv_id, ref)
    if image is None:
        return jsonify({"error": "Image not found"}), 404
    response = Response(image["data"], mimetype=image["mime_type"])
    response.set_etag(ref)
    return response.make_conditional(request)
