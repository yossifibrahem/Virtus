"""
Flask route integration tests for Lumen AI Chat.

Uses the `client` fixture (Flask test client against an isolated test app).
Docker and OpenAI calls are mocked throughout.

Coverage:
  - GET/POST/PUT/DELETE /api/conversations
  - GET /api/conversations/<id>/workspace and /container
  - GET/POST /api/mcp/config
  - POST/GET /api/images
  - GET /api/conversations/<id>/files (list + content + download)
  - POST /api/chat/stream (attach, cancel, approve)
"""
from __future__ import annotations

import os
import re
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

import store
from tests.conftest import make_png_b64


# ===========================================================================
# Conversation CRUD
# ===========================================================================

class TestListConversations:

    def test_empty_list(self, client, tmp_lumen):
        resp = client.get("/api/conversations")
        assert resp.status_code == 200
        assert resp.json == []

    def test_includes_created_conversation(self, client, tmp_lumen):
        store.create("Alpha")
        resp = client.get("/api/conversations")
        assert any(c["title"] == "Alpha" for c in resp.json)


class TestCreateConversation:

    def test_returns_201_with_id(self, client, tmp_lumen):
        resp = client.post("/api/conversations",
                           json={"title": "Named"},
                           content_type="application/json")
        assert resp.status_code == 201
        assert "id" in resp.json
        assert resp.json["title"] == "Named"

    def test_default_title_when_missing(self, client, tmp_lumen):
        resp = client.post("/api/conversations",
                           json={},
                           content_type="application/json")
        assert resp.status_code == 201
        assert resp.json.get("title")  # non-empty

    def test_create_in_folder(self, client, tmp_lumen):
        folder = store.create_folder("Shared")
        first = client.post("/api/conversations", json={"folder_id": folder["id"]}).json
        second = client.post("/api/conversations", json={"folder_id": folder["id"]}).json
        assert first["folder_id"] == folder["id"]
        assert first["working_directory"] == second["working_directory"]


class TestFolders:
    def test_folder_routes(self, client, tmp_lumen):
        created = client.post("/api/folders", json={"name": "Project"})
        assert created.status_code == 201
        folder_id = created.json["id"]
        assert any(item["id"] == folder_id for item in client.get("/api/folders").json)
        assert client.put(f"/api/folders/{folder_id}", json={"name": "Renamed"}).json["name"] == "Renamed"

    def test_folder_instructions_update_without_renaming(self, client, tmp_lumen):
        folder = store.create_folder("Project")
        updated = client.put(
            f"/api/folders/{folder['id']}",
            json={"system_prompt": "Always cite the workspace files."},
        )
        assert updated.status_code == 200
        assert updated.json["name"] == "Project"
        assert updated.json["system_prompt"] == "Always cite the workspace files."

    def test_deleting_folder_deletes_chats_and_cleans_shared_runtime(self, client, tmp_lumen):
        folder = store.create_folder("Shared")
        first = store.create("Delete me", folder["id"])
        second = store.create("Delete me too", folder["id"])
        unrelated = store.create("Keep me")
        with patch("container_service.stop_container") as stop, \
             patch("container_service.delete_workspace") as delete_workspace:
            response = client.delete(f"/api/folders/{folder['id']}")
        assert response.status_code == 200
        assert set(response.json["deleted_conversation_ids"]) == {first["id"], second["id"]}
        assert store.load(first["id"]) is None
        assert store.load(second["id"]) is None
        assert store.load(unrelated["id"]) is not None
        assert store.get_folder(folder["id"]) is None
        stop.assert_called_once_with(f"folder_{folder['id']}")
        delete_workspace.assert_called_once_with(f"folder_{folder['id']}")


class TestGetConversation:

    def test_returns_200_for_existing(self, client, tmp_lumen):
        conv = store.create("get-me")
        resp = client.get(f"/api/conversations/{conv['id']}")
        assert resp.status_code == 200
        assert resp.json["title"] == "get-me"

    def test_returns_404_for_missing(self, client, tmp_lumen):
        resp = client.get("/api/conversations/does-not-exist")
        assert resp.status_code == 404

    def test_response_includes_working_directory(self, client, tmp_lumen):
        conv = store.create("wd-check")
        resp = client.get(f"/api/conversations/{conv['id']}")
        assert "working_directory" in resp.json


class TestUpdateConversation:

    def test_updates_title(self, client, tmp_lumen):
        conv = store.create("old")
        resp = client.put(f"/api/conversations/{conv['id']}",
                          json={"title": "new"},
                          content_type="application/json")
        assert resp.status_code == 200
        assert resp.json["title"] == "new"
        assert resp.json["id"] == conv["id"]  # id must be preserved

    def test_update_ignores_internal_field_overwrites(self, client, tmp_lumen):
        conv = store.create("locked")
        original_messages = [{"role": "user", "content": "safe"}]
        store.save(conv["id"], {**conv, "messages": original_messages, "active_stream_id": "stream-1"})

        resp = client.put(f"/api/conversations/{conv['id']}",
                          json={
                              "title": "renamed",
                              "messages": [{"role": "assistant", "content": "hacked"}],
                              "active_stream_id": "evil",
                              "id": "changed",
                          },
                          content_type="application/json")

        assert resp.status_code == 200
        saved = store.load(conv["id"])
        assert saved["id"] == conv["id"]
        assert saved["title"] == "renamed"
        assert saved["messages"] == original_messages
        assert saved["active_stream_id"] == "stream-1"


class TestDeleteConversation:

    def test_returns_ok_true(self, client, tmp_lumen):
        conv = store.create("delete-me")
        with patch("container_service.stop_container"), \
             patch("container_service.delete_workspace"):
            resp = client.delete(f"/api/conversations/{conv['id']}")
        assert resp.status_code == 200
        assert resp.json["ok"] is True

    def test_conversation_gone_after_delete(self, client, tmp_lumen):
        conv = store.create("gone")
        with patch("container_service.stop_container"), \
             patch("container_service.delete_workspace"):
            client.delete(f"/api/conversations/{conv['id']}")
        assert store.load(conv["id"]) is None

    def test_returns_404_for_nonexistent(self, client, tmp_lumen):
        with patch("container_service.stop_container"), \
             patch("container_service.delete_workspace"):
            resp = client.delete("/api/conversations/ghost-99")
        assert resp.status_code == 404

    def test_shared_runtime_survives_until_last_folder_chat_is_deleted(self, client, tmp_lumen):
        folder = store.create_folder("Shared")
        first = store.create("One", folder["id"])
        second = store.create("Two", folder["id"])
        with patch("container_service.stop_container") as stop, \
             patch("container_service.delete_workspace") as delete_workspace:
            client.delete(f"/api/conversations/{first['id']}")
            stop.assert_not_called()
            delete_workspace.assert_not_called()
            client.delete(f"/api/conversations/{second['id']}")
            stop.assert_called_once_with(f"folder_{folder['id']}")


class TestConversationMetaRoutes:

    def test_workspace_returns_absolute_path(self, client, tmp_lumen):
        conv = store.create("ws")
        resp = client.get(f"/api/conversations/{conv['id']}/workspace")
        assert resp.status_code == 200
        wd = resp.json.get("working_directory", "")
        assert os.path.isabs(wd)  # must be an absolute host path (works on Windows too)

    def test_container_status_for_known_conv(self, client, tmp_lumen):
        conv = store.create("container")
        with patch("container_service.get_status", return_value="missing"):
            resp = client.get(f"/api/conversations/{conv['id']}/container")
        assert resp.status_code == 200
        assert resp.json["status"] == "missing"
        assert resp.json["conv_id"] == conv["id"]


# ===========================================================================
# MCP config
# ===========================================================================

class TestMcpConfigRoutes:

    @pytest.fixture(autouse=True)
    def _isolate_mcp_json(self, tmp_path, monkeypatch):
        monkeypatch.setattr("mcp_service.MCP_CONFIG_FILE", tmp_path / "mcp.json")

    def test_get_returns_empty_when_no_file(self, client, tmp_lumen):
        resp = client.get("/api/mcp/config")
        assert resp.status_code == 200
        assert resp.json == {"mcpServers": {}}

    def test_post_saves_config(self, client, tmp_lumen):
        config = {"mcpServers": {"fs": {"command": "npx", "args": []}}}
        resp = client.post("/api/mcp/config",
                           json=config,
                           content_type="application/json")
        assert resp.status_code == 200
        assert resp.json["ok"] is True

    def test_post_then_get_roundtrip(self, client, tmp_lumen):
        config = {"mcpServers": {"bash": {"command": "bash", "args": []}}}
        client.post("/api/mcp/config", json=config, content_type="application/json")
        resp = client.get("/api/mcp/config")
        assert resp.json == config

    def test_post_invalid_config_returns_400(self, client, tmp_lumen):
        resp = client.post("/api/mcp/config",
                           json={"mcpServers": "not-an-object"},
                           content_type="application/json")
        assert resp.status_code == 400
        assert "error" in resp.json

    def test_post_accepts_python_server_with_any_name(self, client, tmp_lumen):
        config = {"mcpServers": {"agent_tools": {"command": "python", "args": ["server.py"]}}}
        resp = client.post("/api/mcp/config", json=config)
        assert resp.status_code == 200
        assert client.get("/api/mcp/config").json == config

    def test_no_config_discovers_no_tools_or_container(self, client, tmp_lumen, monkeypatch):
        from unittest.mock import Mock

        ensure = Mock()
        stop = Mock()
        fetch = Mock()
        monkeypatch.setattr("container_service.ensure_container", ensure)
        monkeypatch.setattr("container_service.stop_container_process", stop)
        monkeypatch.setattr("mcp_service.fetch_tools", fetch)
        resp = client.get("/api/mcp/tools")
        assert resp.status_code == 200
        assert resp.json == []
        ensure.assert_not_called()
        stop.assert_not_called()
        fetch.assert_not_called()

    def test_unconfigured_server_call_returns_404(self, client, tmp_lumen):
        resp = client.post("/api/mcp/call", json={"server": "agent_tools", "tool": "read"})
        assert resp.status_code == 404


# ===========================================================================
# Image upload and serving
# ===========================================================================

class TestImageRoutes:

    def test_upload_valid_png_returns_ref_and_url(self, client, tmp_lumen):
        resp = client.post("/api/images",
                           json={"data": make_png_b64(), "media_type": "image/png"},
                           content_type="application/json")
        assert resp.status_code == 200
        assert "ref" in resp.json
        assert resp.json["url"].startswith("/api/images/")

    def test_upload_invalid_base64_returns_400(self, client, tmp_lumen):
        resp = client.post("/api/images",
                           json={"data": "not-base64!!!", "media_type": "image/png"},
                           content_type="application/json")
        assert resp.status_code == 400
        assert "error" in resp.json

    def test_upload_unsupported_media_type_returns_400(self, client, tmp_lumen):
        resp = client.post("/api/images",
                           json={"data": make_png_b64(), "media_type": "image/bmp"},
                           content_type="application/json")
        assert resp.status_code == 400

    def test_serve_uploaded_image_200(self, client, tmp_lumen):
        upload = client.post("/api/images",
                             json={"data": make_png_b64(), "media_type": "image/png"},
                             content_type="application/json")
        name = upload.json["ref"]
        resp = client.get(f"/api/images/{name}")
        assert resp.status_code == 200
        assert resp.content_type.startswith("image/")

    def test_serve_nonexistent_image_returns_404(self, client, tmp_lumen):
        resp = client.get("/api/images/" + "a" * 64 + ".png")
        assert resp.status_code == 404

    def test_serve_unsafe_image_name_returns_404(self, client, tmp_lumen):
        resp = client.get("/api/images/../../etc/passwd")
        # Flask will 404 or redirect — either way, not 200
        assert resp.status_code in (400, 404)

    def test_same_content_deduplicated(self, client, tmp_lumen):
        b64 = make_png_b64()
        r1 = client.post("/api/images", json={"data": b64, "media_type": "image/png"},
                         content_type="application/json")
        r2 = client.post("/api/images", json={"data": b64, "media_type": "image/png"},
                         content_type="application/json")
        assert r1.json["ref"] == r2.json["ref"]


# ===========================================================================
# Workspace file routes
# ===========================================================================

class TestWorkspaceFileRoutes:

    @pytest.fixture(autouse=True)
    def worker_transport(self, monkeypatch, tmp_lumen):
        # Exercise the actual worker and route contract with only Docker replaced.
        import sandbox_files
        import builtin_tools
        import json
        def transport(runtime_id, argv, payload, **kwargs):
            root = tmp_lumen["containers_dir"] / runtime_id
            root.mkdir(parents=True, exist_ok=True)
            monkeypatch.setattr(sandbox_files, 'ROOT', root)
            def resolve(value):
                raw = str(value or '')
                if raw.startswith('/workspace'):
                    raw = str(root) + raw[len('/workspace'):]
                target = (root / raw).resolve()
                if target != root and root not in target.parents:
                    raise ValueError('Path escapes workspace')
                return target
            monkeypatch.setattr(sandbox_files, 'resolve', resolve)
            try:
                result = sandbox_files.execute(json.loads(payload))
            except FileNotFoundError:
                result = {'error': 'File not found', 'status': 404}
            except ValueError as exc:
                result = {'error': str(exc), 'status': 400}
            return 0, json.dumps(result).encode(), b''
        monkeypatch.setattr(builtin_tools, 'docker_operation', transport)

    def _make_workspace(self, tmp_lumen) -> tuple[str, Path]:
        conv = store.create("file-test")
        conv_id = conv["id"]
        root = tmp_lumen["containers_dir"] / conv_id
        root.mkdir(parents=True, exist_ok=True)
        return conv_id, root

    def _make_folder_workspace(self, tmp_lumen) -> tuple[str, Path]:
        folder = store.create_folder("folder-files")
        root = tmp_lumen["containers_dir"] / f"folder_{folder['id']}"
        root.mkdir(parents=True, exist_ok=True)
        return folder["id"], root

    def test_list_unknown_conv_returns_error(self, client, tmp_lumen):
        resp = client.get("/api/conversations/ghost/files")
        # Service returns ({"error": ...}, 404) which route passes through
        assert resp.status_code in (404, 500)

    def test_list_files_returns_200_with_entries(self, client, tmp_lumen):
        conv_id, root = self._make_workspace(tmp_lumen)
        (root / "readme.md").write_text("# hi")
        with patch("workspace_service.workspace_root", return_value=root):
            resp = client.get(f"/api/conversations/{conv_id}/files")
        assert resp.status_code == 200
        assert "entries" in resp.json

    def test_file_content_returns_text(self, client, tmp_lumen):
        conv_id, root = self._make_workspace(tmp_lumen)
        (root / "script.py").write_text("x = 42")
        with patch("workspace_service.workspace_root", return_value=root):
            resp = client.get(
                f"/api/conversations/{conv_id}/files/content",
                query_string={"path": "/workspace/script.py"},
            )
        assert resp.status_code == 200
        assert resp.json["content"] == "x = 42"

    def test_file_download_returns_attachment(self, client, tmp_lumen):
        conv_id, root = self._make_workspace(tmp_lumen)
        (root / "data.txt").write_text("download me")
        with patch("workspace_service.workspace_root", return_value=root):
            resp = client.get(
                f"/api/conversations/{conv_id}/files/download",
                query_string={"path": "/workspace/data.txt"},
            )
        assert resp.status_code == 200
        assert b"download me" in resp.data

    def test_download_nonexistent_file_returns_404(self, client, tmp_lumen):
        conv_id, root = self._make_workspace(tmp_lumen)
        with patch("workspace_service.workspace_root", return_value=root):
            resp = client.get(
                f"/api/conversations/{conv_id}/files/download",
                query_string={"path": "/workspace/ghost.txt"},
            )
        assert resp.status_code == 404

    def test_list_with_traversal_path_returns_400(self, client, tmp_lumen):
        conv_id, root = self._make_workspace(tmp_lumen)
        with patch("workspace_service.workspace_root", return_value=root):
            resp = client.get(
                f"/api/conversations/{conv_id}/files",
                query_string={"path": "../escape"},
            )
        assert resp.status_code == 400

    def test_folder_workspace_lists_shared_files_without_a_conversation(self, client, tmp_lumen):
        folder_id, root = self._make_folder_workspace(tmp_lumen)
        (root / "shared.md").write_text("# shared")

        resp = client.get(f"/api/folders/{folder_id}/files")

        assert resp.status_code == 200
        assert [entry["name"] for entry in resp.json["entries"]] == ["shared.md"]

    def test_folder_workspace_previews_and_downloads_shared_file(self, client, tmp_lumen):
        folder_id, root = self._make_folder_workspace(tmp_lumen)
        (root / "notes.txt").write_text("folder notes")

        preview = client.get(
            f"/api/folders/{folder_id}/files/content",
            query_string={"path": "/workspace/notes.txt"},
        )
        download = client.get(
            f"/api/folders/{folder_id}/files/download",
            query_string={"path": "/workspace/notes.txt"},
        )

        assert preview.status_code == 200
        assert preview.json["content"] == "folder notes"
        assert download.status_code == 200
        assert download.data == b"folder notes"

    def test_missing_folder_workspace_returns_404(self, client, tmp_lumen):
        missing_id = "00000000-0000-0000-0000-000000000000"
        assert client.get(f"/api/folders/{missing_id}/files").status_code == 404


# ===========================================================================
# Chat stream routes (minimal — no real OpenAI call)
# ===========================================================================

class TestChatStreamRoutes:

    def test_cancel_unknown_stream_returns_404(self, client, tmp_lumen):
        resp = client.post("/api/chat/cancel",
                           json={"stream_id": "ghost-stream"},
                           content_type="application/json")
        assert resp.status_code == 404
        assert "error" in resp.json

    def test_attach_to_nonexistent_stream_returns_404(self, client, tmp_lumen):
        resp = client.post("/api/chat/stream",
                           json={"stream_id": "ghost-stream", "attach": True},
                           content_type="application/json")
        assert resp.status_code == 404

    def test_approve_rejects_unknown_stream(self, client, tmp_lumen):
        # Unknown streams cannot be approved.
        resp = client.post("/api/chat/approve",
                           json={"stream_id": "ghost", "call_id": "c1", "approved": True},
                           content_type="application/json")
        assert resp.status_code == 404


# ===========================================================================
# Models route
# ===========================================================================

class TestModelsRoute:

    def test_returns_model_list(self, client, tmp_lumen):
        mock_model = MagicMock()
        mock_model.id = "gpt-4o"
        mock_client = MagicMock()
        mock_client.models.list.return_value = [mock_model]

        with patch("chat_turn_service.openai_client", return_value=mock_client):
            resp = client.post("/api/models",
                               json={"api_key": "sk-test"},
                               content_type="application/json")
        assert resp.status_code == 200
        assert "models" in resp.json
        assert "gpt-4o" in resp.json["models"]

    def test_bad_api_key_returns_400(self, client, tmp_lumen):
        mock_client = MagicMock()
        mock_client.models.list.side_effect = Exception("invalid api key")

        with patch("chat_turn_service.openai_client", return_value=mock_client):
            resp = client.post("/api/models",
                               json={"api_key": "bad"},
                               content_type="application/json")
        assert resp.status_code == 400
        assert "error" in resp.json


# ===========================================================================
# Index route
# ===========================================================================

class TestIndexRoute:

    def test_renders_app_shell(self, client, tmp_lumen):
        resp = client.get("/")
        assert resp.status_code == 200
        # Must contain the top-level script tag that bootstraps the app
        assert b"app.js" in resp.data

    def test_renders_startup_screen_when_requirement_missing(self, client, monkeypatch):
        import runtime_requirements

        missing = runtime_requirements.RequirementStatus(
            ok=False,
            code="docker_not_running",
            title="Docker is not running",
            message="Please start Docker, then click Retry.",
            action="retry",
            image="lumen-sandbox",
        )
        monkeypatch.setattr(runtime_requirements, "check_requirements", lambda: missing)

        resp = client.get("/")

        assert resp.status_code == 200
        assert b"Docker is not running" in resp.data
        assert b"Please start Docker, then click Retry." in resp.data
        assert b'name="lumen-startup-token"' in resp.data


# ===========================================================================
# Startup requirement routes
# ===========================================================================

class TestStartupRequirementRoutes:

    @staticmethod
    def _issue_startup_token(client, monkeypatch):
        import runtime_requirements

        missing = runtime_requirements.RequirementStatus(
            ok=False,
            code="docker_not_running",
            title="Docker is not running",
            message="Please start Docker.",
            action="start_docker",
            image="lumen-sandbox",
        )
        monkeypatch.setattr(runtime_requirements, "check_requirements", lambda: missing)
        response = client.get("/")
        match = re.search(rb'name="lumen-startup-token" content="([^"]+)"', response.data)
        assert match is not None
        return match.group(1).decode()

    @staticmethod
    def _startup_headers(token, origin="http://localhost"):
        return {
            "Origin": origin,
            "Sec-Fetch-Site": "same-origin" if origin == "http://localhost" else "cross-site",
            "X-Lumen-Startup-Token": token,
        }

    def test_health_identifies_lumen_and_version(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json["ok"] is True
        assert resp.json["app"] == "lumen-ai-chat"
        assert resp.json["version"]

    def test_requirements_returns_503_when_not_ready(self, client, monkeypatch):
        import runtime_requirements

        missing = runtime_requirements.RequirementStatus(
            ok=False,
            code="sandbox_image_missing",
            title="The Lumen sandbox image has not been built",
            message="Click Build Sandbox Image.",
            action="build",
            image="lumen-sandbox",
        )
        monkeypatch.setattr(runtime_requirements, "check_requirements", lambda: missing)

        resp = client.get("/api/startup/requirements")

        assert resp.status_code == 503
        assert resp.json["code"] == "sandbox_image_missing"
        assert resp.json["action"] == "build"

    def test_start_docker_route_returns_accepted(self, client, monkeypatch):
        import runtime_requirements

        token = self._issue_startup_token(client, monkeypatch)
        starting = runtime_requirements.RequirementStatus(
            ok=False,
            code="docker_starting",
            title="Docker is starting",
            message="Waiting for Docker.",
            action="retry",
            image="lumen-sandbox",
        )
        monkeypatch.setattr(runtime_requirements, "start_docker_desktop", lambda: starting)

        resp = client.post(
            "/api/startup/start-docker",
            headers=self._startup_headers(token),
        )

        assert resp.status_code == 202
        assert resp.json["code"] == "docker_starting"

    def test_start_docker_rejects_missing_confirmation(self, client, monkeypatch):
        import runtime_requirements

        start = MagicMock()
        monkeypatch.setattr(runtime_requirements, "start_docker_desktop", start)

        response = client.post("/api/startup/start-docker")

        assert response.status_code == 403
        start.assert_not_called()

    def test_start_docker_rejects_cross_origin_request(self, client, monkeypatch):
        import runtime_requirements

        token = self._issue_startup_token(client, monkeypatch)
        start = MagicMock()
        monkeypatch.setattr(runtime_requirements, "start_docker_desktop", start)

        response = client.post(
            "/api/startup/start-docker",
            headers=self._startup_headers(token, origin="https://attacker.example"),
        )

        assert response.status_code == 403
        start.assert_not_called()

    def test_build_stream_rejects_get(self, client):
        response = client.get("/api/startup/build-sandbox-image/stream")
        assert response.status_code == 405

    def test_build_stream_requires_confirmation_token(self, client, monkeypatch):
        import runtime_requirements

        build = MagicMock()
        monkeypatch.setattr(runtime_requirements, "build_sandbox_image_stream", build)

        response = client.post(
            "/api/startup/build-sandbox-image/stream",
            headers={"Origin": "http://localhost", "Sec-Fetch-Site": "same-origin"},
        )

        assert response.status_code == 403
        build.assert_not_called()

    def test_build_stream_accepts_confirmed_same_origin_post(self, client, monkeypatch):
        import runtime_requirements

        token = self._issue_startup_token(client, monkeypatch)
        done = runtime_requirements.RequirementStatus(
            ok=True,
            code="ok",
            title="Lumen is ready",
            message="Ready.",
            action="continue",
            image="lumen-sandbox",
        )
        monkeypatch.setattr(
            runtime_requirements,
            "build_sandbox_image_stream",
            lambda: iter([("log", {"line": "building"}), ("done", done.as_dict())]),
        )

        response = client.post(
            "/api/startup/build-sandbox-image/stream",
            headers=self._startup_headers(token),
        )

        assert response.status_code == 200
        assert response.mimetype == "text/event-stream"
        assert b"event: log" in response.data
        assert b"event: done" in response.data

# ===========================================================================
# MCP tools discovery
# ===========================================================================

class TestMcpToolsDiscoveryRoutes:

    def test_no_conversation_uses_reusable_discovery_container(self, client, tmp_lumen):
        config = {"mcpServers": {"fs": {"command": "npx", "args": []}}}

        def fake_fetch(server_name, server_config, *, conv_id=""):
            return {"server_name": server_name, "conv_id": conv_id}

        def fake_run_async(payload):
            return [{
                "server": payload["server_name"],
                "name": "list_files",
                "description": "List files",
                "inputSchema": {},
            }]

        with (
            patch("mcp_service.load_config", return_value=config),
            patch("mcp_adapters.extract_host_mounts", return_value=[]),
            patch("container_service.ensure_container") as ensure_container,
            patch("container_service.stop_container_process") as stop_container_process,
            patch("mcp_service.fetch_tools", new=MagicMock(side_effect=fake_fetch)) as fetch_tools,
            patch("mcp_service.run_async", side_effect=fake_run_async),
        ):
            resp = client.get("/api/mcp/tools")

        assert resp.status_code == 200
        assert resp.json == [{
            "server": "fs",
            "name": "list_files",
            "description": "List files",
            "inputSchema": {},
        }]
        ensure_container.assert_called_once_with(
            "mcp-discovery",
            extra_volumes=[],
        )
        fetch_tools.assert_called_once_with(
            "fs",
            config["mcpServers"]["fs"],
            conv_id="mcp-discovery",
        )
        stop_container_process.assert_called_once_with("mcp-discovery")

    def test_no_conversation_mounts_deduplicated_discovery_volumes(self, client, tmp_lumen):
        config = {
            "mcpServers": {
                "a": {"command": "node", "args": ["/srv/a/server.js"]},
                "b": {"command": "node", "args": ["/srv/b/server.js"]},
            }
        }

        def fake_extract(cfg):
            if cfg is config["mcpServers"]["a"]:
                return [
                    "/host/shared:/host/shared:ro",
                    "/host/unique:/host/unique:ro",
                ]
            return ["/host/shared:/host/shared:ro"]

        with (
            patch("mcp_service.load_config", return_value=config),
            patch("mcp_adapters.extract_host_mounts", side_effect=fake_extract),
            patch("container_service.ensure_container") as ensure_container,
            patch("container_service.stop_container_process"),
            patch("mcp_service.fetch_tools", new=MagicMock(return_value=object())),
            patch("mcp_service.run_async", return_value=[]),
        ):
            resp = client.get("/api/mcp/tools")

        assert resp.status_code == 200
        ensure_container.assert_called_once_with(
            "mcp-discovery",
            extra_volumes=[
                "/host/shared:/host/shared:ro",
                "/host/unique:/host/unique:ro",
            ],
        )

    def test_existing_conversation_keeps_real_conversation_id(self, client, tmp_lumen):
        config = {"mcpServers": {"bash": {"command": "bash", "args": []}}}

        def fake_fetch(server_name, server_config, *, conv_id=""):
            return {"server_name": server_name, "conv_id": conv_id}

        def fake_run_async(payload):
            return [{
                "server": payload["server_name"],
                "name": "run",
                "description": "",
                "inputSchema": {},
            }]

        with (
            patch("mcp_service.load_config", return_value=config),
            patch("container_service.ensure_container") as ensure_container,
            patch("container_service.stop_container_process") as stop_container_process,
            patch("mcp_service.fetch_tools", new=MagicMock(side_effect=fake_fetch)) as fetch_tools,
            patch("mcp_service.run_async", side_effect=fake_run_async),
        ):
            conv = store.create()
            resp = client.get("/api/mcp/tools", query_string={"conv_id": conv["id"]})

        assert resp.status_code == 200
        ensure_container.assert_not_called()
        stop_container_process.assert_not_called()
        fetch_tools.assert_called_once_with(
            "bash",
            config["mcpServers"]["bash"],
            conv_id=conv["id"],
        )

    def test_skipped_server_reason_is_preserved(self, client, tmp_lumen):
        from mcp_adapters import ContainerConversationRequired

        config = {"mcpServers": {"remote": {"command": "npx", "args": []}}}

        with (
            patch("mcp_service.load_config", return_value=config),
            patch("mcp_adapters.extract_host_mounts", return_value=[]),
            patch("container_service.ensure_container"),
            patch("container_service.stop_container_process") as stop_container_process,
            patch("mcp_service.fetch_tools", new=MagicMock(return_value=object())),
            patch(
                "mcp_service.run_async",
                side_effect=ContainerConversationRequired("needs a conversation"),
            ),
        ):
            resp = client.get("/api/mcp/tools")

        assert resp.status_code == 200
        assert resp.json == {
            "tools": [],
            "skipped": [{"server": "remote", "reason": "needs a conversation"}],
        }
        stop_container_process.assert_called_once_with("mcp-discovery")
