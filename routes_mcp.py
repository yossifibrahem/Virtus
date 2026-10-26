"""MCP config, tool discovery, and direct tool-call routes."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

import container_service
import mcp_service
import store
import builtin_tools
import sandbox_runtime
import re
from mcp_adapters import ContainerConversationRequired

blueprint = Blueprint("mcp", __name__)


def _body() -> dict:
    return request.get_json(silent=True) or {}


@blueprint.route("/api/mcp/config", methods=["GET"])
def get_mcp_config():
    return jsonify(mcp_service.load_config())


@blueprint.route("/api/mcp/config", methods=["POST"])
def save_mcp_config():
    try:
        mcp_service.save_config(_body())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True})


@blueprint.route("/api/mcp/tools", methods=["GET"])
def list_mcp_tools():
    requested_conv_id = request.args.get("conv_id", "")
    if requested_conv_id and (not re.fullmatch(r"[a-fA-F0-9-]{36}", requested_conv_id) or not store.load(requested_conv_id)):
        return jsonify({"error": "Conversation not found"}), 404
    conv_id = store.runtime_id(requested_conv_id) if requested_conv_id else container_service.DISCOVERY_CONTAINER_ID
    tools: list[dict] = []
    skipped: list[dict] = []
    discovery_mode = not requested_conv_id

    configs = mcp_service.load_config().get("mcpServers", {})
    if not configs:
        return jsonify([])

    if discovery_mode:
        container_service.ensure_container(conv_id)

    try:
        for name, cfg in configs.items():
            try:
                tools.extend(mcp_service.run_async(mcp_service.fetch_tools(name, cfg, conv_id=conv_id)))
            except ContainerConversationRequired as exc:
                skipped.append({"server": name, "reason": str(exc)})
    finally:
        if discovery_mode:
            container_service.stop_container_process(conv_id)

    if any(t.get("name") in builtin_tools.NAMES for t in tools):
        return jsonify({"error": "MCP tool name conflicts with built-in bash/read/write/edit"}), 409
    return jsonify({"tools": tools, "skipped": skipped} if skipped else tools)


@blueprint.route("/api/mcp/call", methods=["POST"])
def call_mcp_tool():
    body = _body()
    server_name = body.get("server", "")
    server_config = mcp_service.find_server(server_name)
    if not server_config:
        return jsonify({"error": f"MCP server '{server_name}' not found"}), 404

    requested_conv_id = body.get("conv_id", "")
    if not isinstance(requested_conv_id, str) or not re.fullmatch(r"[a-fA-F0-9-]{36}", requested_conv_id) or not store.load(requested_conv_id):
        return jsonify({"error": "Conversation not found"}), 404
    if body.get("tool") in builtin_tools.NAMES:
        return jsonify({"error": "Reserved built-in tool name"}), 409
    conv_id = store.runtime_id(requested_conv_id)
    try:
        with sandbox_runtime.runtime_operation(conv_id):
            result = mcp_service.run_async(mcp_service.invoke_tool(
                server_name,
                server_config,
                body.get("tool", ""),
                body.get("arguments", {}),
                conv_id=conv_id,
            ))
    except ContainerConversationRequired as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"result": result})
