"""Tests for REST API endpoints."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient, ASGITransport

from conn_server.agent_manager import AgentManager
from conn_server.mcp_config import McpConfigManager
from conn_server.server import app, _validate_tool_spec
from conn_server.session_manager import SessionManager


def _init_git_repo(path, branch="main"):
    """Helper to create a minimal git repo at the given path."""
    subprocess.run(["git", "init", "-b", branch, str(path)], capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(path), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(path), capture_output=True, check=True)
    (path / "README.md").write_text("test")
    subprocess.run(["git", "add", "."], cwd=str(path), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=str(path), capture_output=True, check=True)


@pytest.fixture
def test_client(tmp_config_dir):
    """Create an async test client with patched config."""
    # Also patch the global sessions object in server module.
    # Must use yield (not return) so the patch stays active during the test.
    with patch("conn_server.server.sessions", SessionManager()), \
         patch("conn_server.server.mcp_servers", McpConfigManager()), \
         patch("conn_server.server.agents", AgentManager(agents_dir=tmp_config_dir["agents_dir"])):
        transport = ASGITransport(app=app)
        yield AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def headers(tmp_config_dir):
    return {"Authorization": f"Bearer {tmp_config_dir['token']}"}


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_no_auth_required(self, test_client):
        async with test_client as client:
            response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "uptime_seconds" in data

    @pytest.mark.asyncio
    async def test_health_returns_uptime(self, test_client):
        async with test_client as client:
            response = await client.get("/health")
        assert response.json()["uptime_seconds"] >= 0


class TestConversationsEndpoint:
    @pytest.mark.asyncio
    async def test_list_conversations_empty(self, test_client, headers):
        async with test_client as client:
            response = await client.get("/conversations", headers=headers)
        assert response.status_code == 200
        assert response.json()["conversations"] == []

    @pytest.mark.asyncio
    async def test_list_conversations_requires_auth(self, test_client):
        async with test_client as client:
            response = await client.get("/conversations")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_list_conversations_rejects_bad_token(self, test_client):
        async with test_client as client:
            response = await client.get(
                "/conversations",
                headers={"Authorization": "Bearer wrong-token"},
            )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_delete_conversation_not_found(self, test_client, headers):
        async with test_client as client:
            response = await client.delete("/conversations/nonexistent", headers=headers)
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_conversation_requires_auth(self, test_client):
        async with test_client as client:
            response = await client.delete("/conversations/any-id")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_get_history_not_found(self, test_client, headers):
        async with test_client as client:
            response = await client.get("/conversations/nonexistent/history", headers=headers)
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_conversations_include_git_branch(self, test_client, headers, tmp_config_dir):
        """Conversations with a git repo working_dir should include git_branch."""
        project_dir = tmp_config_dir["projects_dir"] / "GitProject"
        project_dir.mkdir()
        _init_git_repo(project_dir, branch="feature")

        # Create a conversation pointing to the git project
        import conn_server.server as server
        server.sessions.create_conversation("conv_git", "Test", working_dir=str(project_dir))

        async with test_client as client:
            response = await client.get("/conversations", headers=headers)
        convs = response.json()["conversations"]
        conv = next(c for c in convs if c["id"] == "conv_git")
        assert conv["git_branch"] == "feature"

    @pytest.mark.asyncio
    async def test_conversations_null_branch_for_non_git(self, test_client, headers, tmp_config_dir):
        """Conversations without a git working_dir should have null git_branch."""
        import conn_server.server as server
        server.sessions.create_conversation("conv_plain", "Test", working_dir=str(tmp_config_dir["projects_dir"]))

        async with test_client as client:
            response = await client.get("/conversations", headers=headers)
        convs = response.json()["conversations"]
        conv = next(c for c in convs if c["id"] == "conv_plain")
        assert conv["git_branch"] is None


class TestProjectsEndpoint:
    @pytest.mark.asyncio
    async def test_list_projects_includes_all_projects(self, test_client, headers, tmp_config_dir):
        async with test_client as client:
            response = await client.get("/projects", headers=headers)
        assert response.status_code == 200
        projects = response.json()["projects"]
        assert len(projects) >= 1
        assert projects[0]["name"] == "All Projects"

    @pytest.mark.asyncio
    async def test_list_projects_shows_subdirectories(self, test_client, headers, tmp_config_dir):
        # Create some project dirs
        (tmp_config_dir["projects_dir"] / "ProjectA").mkdir()
        (tmp_config_dir["projects_dir"] / "ProjectB").mkdir()

        async with test_client as client:
            response = await client.get("/projects", headers=headers)
        projects = response.json()["projects"]
        names = [p["name"] for p in projects]
        assert "ProjectA" in names
        assert "ProjectB" in names

    @pytest.mark.asyncio
    async def test_list_projects_excludes_hidden_dirs(self, test_client, headers, tmp_config_dir):
        (tmp_config_dir["projects_dir"] / ".hidden").mkdir()
        (tmp_config_dir["projects_dir"] / "Visible").mkdir()

        async with test_client as client:
            response = await client.get("/projects", headers=headers)
        names = [p["name"] for p in response.json()["projects"]]
        assert ".hidden" not in names
        assert "Visible" in names

    @pytest.mark.asyncio
    async def test_list_projects_requires_auth(self, test_client):
        async with test_client as client:
            response = await client.get("/projects")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_create_project(self, test_client, headers, tmp_config_dir):
        async with test_client as client:
            response = await client.post(
                "/projects",
                headers=headers,
                json={"name": "NewProject"},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "NewProject"
        assert (tmp_config_dir["projects_dir"] / "NewProject").is_dir()

    @pytest.mark.asyncio
    async def test_create_project_strips_whitespace(self, test_client, headers, tmp_config_dir):
        async with test_client as client:
            response = await client.post(
                "/projects",
                headers=headers,
                json={"name": "  SpacedProject  "},
            )
        assert response.status_code == 200
        assert response.json()["name"] == "SpacedProject"
        assert (tmp_config_dir["projects_dir"] / "SpacedProject").is_dir()

    @pytest.mark.asyncio
    async def test_create_project_duplicate_returns_409(self, test_client, headers, tmp_config_dir):
        (tmp_config_dir["projects_dir"] / "Existing").mkdir()

        async with test_client as client:
            response = await client.post(
                "/projects",
                headers=headers,
                json={"name": "Existing"},
            )
        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_create_project_empty_name_returns_400(self, test_client, headers):
        async with test_client as client:
            response = await client.post(
                "/projects",
                headers=headers,
                json={"name": ""},
            )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_create_project_path_traversal_returns_400(self, test_client, headers):
        async with test_client as client:
            for bad_name in ["../escape", "foo/bar", "foo\\bar", ".hidden"]:
                response = await client.post(
                    "/projects",
                    headers=headers,
                    json={"name": bad_name},
                )
                assert response.status_code == 400, f"Expected 400 for name: {bad_name}"

    @pytest.mark.asyncio
    async def test_create_project_requires_auth(self, test_client):
        async with test_client as client:
            response = await client.post("/projects", json={"name": "Test"})
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_projects_include_git_branch(self, test_client, headers, tmp_config_dir):
        project_dir = tmp_config_dir["projects_dir"] / "GitProject"
        project_dir.mkdir()
        _init_git_repo(project_dir, branch="develop")

        async with test_client as client:
            response = await client.get("/projects", headers=headers)
        projects = response.json()["projects"]
        git_project = next(p for p in projects if p["name"] == "GitProject")
        assert git_project["git_branch"] == "develop"

    @pytest.mark.asyncio
    async def test_non_git_project_has_null_branch(self, test_client, headers, tmp_config_dir):
        (tmp_config_dir["projects_dir"] / "PlainDir").mkdir()

        async with test_client as client:
            response = await client.get("/projects", headers=headers)
        projects = response.json()["projects"]
        plain = next(p for p in projects if p["name"] == "PlainDir")
        assert plain["git_branch"] is None


class TestUploadEndpoint:
    @pytest.mark.asyncio
    async def test_upload_rejects_unsupported_extension(self, test_client, headers):
        async with test_client as client:
            response = await client.post(
                "/upload?conversation_id=conv_1",
                headers=headers,
                files={"file": ("test.exe", b"hello", "application/octet-stream")},
            )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_upload_accepts_txt(self, test_client, headers, tmp_config_dir):
        async with test_client as client:
            response = await client.post(
                "/upload?conversation_id=conv_1",
                headers=headers,
                files={"file": ("notes.txt", b"hello world", "text/plain")},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["path"].endswith(".txt")

    @pytest.mark.asyncio
    async def test_upload_accepts_pdf(self, test_client, headers, tmp_config_dir):
        async with test_client as client:
            response = await client.post(
                "/upload?conversation_id=conv_1",
                headers=headers,
                files={"file": ("doc.pdf", b"%PDF-1.4", "application/pdf")},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["path"].endswith(".pdf")

    @pytest.mark.asyncio
    async def test_upload_accepts_jpg(self, test_client, headers, tmp_config_dir):
        async with test_client as client:
            response = await client.post(
                "/upload?conversation_id=conv_1",
                headers=headers,
                files={"file": ("photo.jpg", b"\xff\xd8\xff\xe0", "image/jpeg")},
            )
        assert response.status_code == 200
        data = response.json()
        assert "path" in data
        assert data["path"].endswith(".jpg")

    @pytest.mark.asyncio
    async def test_upload_accepts_png(self, test_client, headers):
        async with test_client as client:
            response = await client.post(
                "/upload?conversation_id=conv_1",
                headers=headers,
                files={"file": ("image.png", b"\x89PNG", "image/png")},
            )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_upload_requires_auth(self, test_client):
        async with test_client as client:
            response = await client.post(
                "/upload?conversation_id=conv_1",
                files={"file": ("photo.jpg", b"\xff\xd8", "image/jpeg")},
            )
        assert response.status_code == 401


class TestActiveConversationsEndpoint:
    @pytest.mark.asyncio
    async def test_active_conversations_empty(self, test_client, headers):
        async with test_client as client:
            response = await client.get("/conversations/active", headers=headers)
        assert response.status_code == 200
        assert response.json()["active_conversation_ids"] == []

    @pytest.mark.asyncio
    async def test_active_conversations_requires_auth(self, test_client):
        async with test_client as client:
            response = await client.get("/conversations/active")
        assert response.status_code == 401


class TestRestartEndpoint:
    @pytest.mark.asyncio
    async def test_restart_requires_auth(self, test_client):
        async with test_client as client:
            response = await client.post("/restart")
        assert response.status_code == 401


class TestMcpServersEndpoint:
    @pytest.mark.asyncio
    async def test_list_mcp_servers_empty(self, test_client, headers):
        async with test_client as client:
            response = await client.get("/mcp/servers", headers=headers)
        assert response.status_code == 200
        assert response.json()["servers"] == []

    @pytest.mark.asyncio
    async def test_list_mcp_servers_requires_auth(self, test_client):
        async with test_client as client:
            response = await client.get("/mcp/servers")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_add_stdio_server(self, test_client, headers):
        async with test_client as client:
            response = await client.post("/mcp/servers", headers=headers, json={
                "name": "sentry",
                "display_name": "Sentry",
                "transport": "stdio",
                "command": "npx",
                "args": ["-y", "@sentry/mcp-server"],
                "env": {"SENTRY_TOKEN": "secret"},
            })
        assert response.status_code == 200
        assert response.json()["server"] == "sentry"

    @pytest.mark.asyncio
    async def test_add_http_server(self, test_client, headers):
        async with test_client as client:
            response = await client.post("/mcp/servers", headers=headers, json={
                "name": "github",
                "display_name": "GitHub",
                "transport": "http",
                "url": "https://api.github.com/mcp/",
            })
        assert response.status_code == 200
        assert response.json()["server"] == "github"

    @pytest.mark.asyncio
    async def test_add_server_invalid_name(self, test_client, headers):
        async with test_client as client:
            response = await client.post("/mcp/servers", headers=headers, json={
                "name": "bad name!",
                "transport": "stdio",
                "command": "cmd",
            })
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_add_duplicate_server(self, test_client, headers):
        async with test_client as client:
            await client.post("/mcp/servers", headers=headers, json={
                "name": "test", "transport": "stdio", "command": "cmd",
            })
            response = await client.post("/mcp/servers", headers=headers, json={
                "name": "test", "transport": "stdio", "command": "cmd",
            })
        assert response.status_code == 400
        assert "already exists" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_add_and_list_masks_env(self, test_client, headers):
        async with test_client as client:
            await client.post("/mcp/servers", headers=headers, json={
                "name": "test", "transport": "stdio", "command": "cmd",
                "env": {"TOKEN": "sk-super-secret-key"},
            })
            response = await client.get("/mcp/servers", headers=headers)
        servers = response.json()["servers"]
        assert len(servers) == 1
        assert servers[0]["env"]["TOKEN"] == "sk-s...-key"

    @pytest.mark.asyncio
    async def test_delete_server(self, test_client, headers):
        async with test_client as client:
            await client.post("/mcp/servers", headers=headers, json={
                "name": "test", "transport": "stdio", "command": "cmd",
            })
            response = await client.delete("/mcp/servers/test", headers=headers)
        assert response.status_code == 200
        assert response.json()["deleted"] == "test"

    @pytest.mark.asyncio
    async def test_delete_server_not_found(self, test_client, headers):
        async with test_client as client:
            response = await client.delete("/mcp/servers/nonexistent", headers=headers)
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_toggle_server(self, test_client, headers):
        async with test_client as client:
            await client.post("/mcp/servers", headers=headers, json={
                "name": "test", "transport": "stdio", "command": "cmd",
            })
            response = await client.post("/mcp/servers/test/toggle", headers=headers, json={
                "enabled": False,
            })
        assert response.status_code == 200
        assert response.json()["enabled"] is False

    @pytest.mark.asyncio
    async def test_toggle_server_not_found(self, test_client, headers):
        async with test_client as client:
            response = await client.post("/mcp/servers/nonexistent/toggle", headers=headers, json={
                "enabled": True,
            })
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_update_server(self, test_client, headers):
        async with test_client as client:
            await client.post("/mcp/servers", headers=headers, json={
                "name": "test", "transport": "stdio", "command": "cmd",
            })
            response = await client.put("/mcp/servers/test", headers=headers, json={
                "name": "test", "display_name": "Updated", "transport": "stdio", "command": "new-cmd",
            })
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_update_server_not_found(self, test_client, headers):
        async with test_client as client:
            response = await client.put("/mcp/servers/nonexistent", headers=headers, json={
                "name": "nonexistent", "transport": "stdio", "command": "cmd",
            })
        assert response.status_code == 404


class TestServeFileEndpoint:
    """Tests for GET /files — serves image files back to the mobile client."""

    @pytest.mark.asyncio
    async def test_serve_png_file(self, test_client, headers, tmp_config_dir):
        # Create a PNG file in the uploads dir
        uploads = tmp_config_dir["uploads_dir"]
        img = uploads / "screenshot.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        async with test_client as client:
            response = await client.get(f"/files?path={img}", headers=headers)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_serve_jpg_file(self, test_client, headers, tmp_config_dir):
        uploads = tmp_config_dir["uploads_dir"]
        img = uploads / "photo.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 50)

        async with test_client as client:
            response = await client.get(f"/files?path={img}", headers=headers)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_serve_rejects_non_image(self, test_client, headers, tmp_config_dir):
        uploads = tmp_config_dir["uploads_dir"]
        txt = uploads / "secret.txt"
        txt.write_text("password123")

        async with test_client as client:
            response = await client.get(f"/files?path={txt}", headers=headers)
        assert response.status_code == 403
        assert "not allowed" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_serve_rejects_py_file(self, test_client, headers, tmp_config_dir):
        uploads = tmp_config_dir["uploads_dir"]
        py = uploads / "server.py"
        py.write_text("import os")

        async with test_client as client:
            response = await client.get(f"/files?path={py}", headers=headers)
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_serve_file_not_found(self, test_client, headers):
        # Path outside allowed directories returns 403 before reaching 404
        async with test_client as client:
            response = await client.get("/files?path=/nonexistent/image.png", headers=headers)
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_serve_rejects_path_traversal(self, test_client, headers):
        async with test_client as client:
            response = await client.get("/files?path=/../../../etc/passwd.png", headers=headers)
        assert response.status_code in (400, 403)

    @pytest.mark.asyncio
    async def test_serve_rejects_outside_allowed_dirs(self, test_client, headers):
        """Files outside uploads_dir/working_dir/tmp are rejected.

        /tmp is an allowed dir, so we use a fake path outside all allowed roots.
        The file doesn't need to exist — the allowed-dir check happens before
        the existence check.
        """
        async with test_client as client:
            response = await client.get("/files?path=/opt/secret/photo.png", headers=headers)
        assert response.status_code == 403
        assert "outside allowed" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_serve_requires_auth(self, test_client):
        async with test_client as client:
            response = await client.get("/files?path=/tmp/image.png")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_serve_rejects_bad_token(self, test_client):
        async with test_client as client:
            response = await client.get(
                "/files?path=/tmp/image.png",
                headers={"Authorization": "Bearer wrong-token"},
            )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_serve_with_token_query_param(self, test_client, tmp_config_dir):
        uploads = tmp_config_dir["uploads_dir"]
        img = uploads / "token-test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
        token = tmp_config_dir["token"]

        async with test_client as client:
            response = await client.get(f"/files?path={img}&token={token}")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_serve_rejects_bad_token_query_param(self, test_client, tmp_config_dir):
        uploads = tmp_config_dir["uploads_dir"]
        img = uploads / "bad-token.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)

        async with test_client as client:
            response = await client.get(f"/files?path={img}&token=wrong-token")
        assert response.status_code == 403


class TestValidateToolSpec:
    """Tests for _validate_tool_spec — accepts bare tool names and pattern syntax."""

    def test_bare_tool_names(self):
        for tool in ["Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch"]:
            assert _validate_tool_spec(tool) is True

    def test_tool_with_pattern(self):
        assert _validate_tool_spec("Bash(git:*)") is True
        assert _validate_tool_spec("Bash(npm install:*)") is True
        assert _validate_tool_spec("Bash(docker:*)") is True

    def test_tool_with_multiple_colons(self):
        assert _validate_tool_spec("Bash(git commit -m:*)") is True

    def test_invalid_tool_name(self):
        assert _validate_tool_spec("NotATool") is False
        assert _validate_tool_spec("bash") is False  # case-sensitive
        assert _validate_tool_spec("") is False

    def test_invalid_tool_with_pattern(self):
        assert _validate_tool_spec("NotATool(foo:*)") is False

    def test_edit_with_pattern(self):
        assert _validate_tool_spec("Edit(*.py)") is True


class TestUpdateEndpoint:
    @pytest.mark.asyncio
    async def test_update_check_no_release(self, test_client, headers):
        async with test_client as client:
            response = await client.get("/update/check", headers=headers)
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_update_check_with_release(self, test_client, headers, tmp_config_dir):
        releases_dir = tmp_config_dir["releases_dir"]
        version_data = {
            "versionCode": 42,
            "versionName": "1.0.0-dev.42",
            "buildDate": "2026-02-20T00:00:00Z",
            "notes": "Test build",
        }
        (releases_dir / "version.json").write_text(json.dumps(version_data))

        async with test_client as client:
            response = await client.get("/update/check", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data["versionCode"] == 42
        assert data["versionName"] == "1.0.0-dev.42"

    @pytest.mark.asyncio
    async def test_update_check_requires_auth(self, test_client):
        async with test_client as client:
            response = await client.get("/update/check")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_update_download_no_apk(self, test_client, headers):
        async with test_client as client:
            response = await client.get("/update/download", headers=headers)
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_update_download_with_apk(self, test_client, headers, tmp_config_dir):
        releases_dir = tmp_config_dir["releases_dir"]
        apk = releases_dir / "latest.apk"
        apk.write_bytes(b"\x00" * 100)

        async with test_client as client:
            response = await client.get("/update/download", headers=headers)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_update_download_with_token_param(self, test_client, tmp_config_dir):
        releases_dir = tmp_config_dir["releases_dir"]
        apk = releases_dir / "latest.apk"
        apk.write_bytes(b"\x00" * 100)
        token = tmp_config_dir["token"]

        async with test_client as client:
            response = await client.get(f"/update/download?token={token}")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_update_download_rejects_bad_token(self, test_client, tmp_config_dir):
        releases_dir = tmp_config_dir["releases_dir"]
        apk = releases_dir / "latest.apk"
        apk.write_bytes(b"\x00" * 100)

        async with test_client as client:
            response = await client.get("/update/download?token=wrong")
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_update_download_requires_auth(self, test_client):
        async with test_client as client:
            response = await client.get("/update/download")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_releases_empty(self, test_client, headers):
        async with test_client as client:
            response = await client.get("/update/releases", headers=headers)
        assert response.status_code == 200
        assert response.json()["releases"] == []

    @pytest.mark.asyncio
    async def test_releases_with_manifest(self, test_client, headers, tmp_config_dir):
        releases_dir = tmp_config_dir["releases_dir"]
        manifest = [
            {"versionCode": 95, "versionName": "1.0.0-dev.95", "buildDate": "2026-02-21T02:40:41Z", "notes": "Build 95", "filename": "conn-v1.0.0-dev.95.apk"},
            {"versionCode": 94, "versionName": "1.0.0-dev.94", "buildDate": "2026-02-20T20:00:00Z", "notes": "Build 94", "filename": "conn-v1.0.0-dev.94.apk"},
        ]
        (releases_dir / "releases.json").write_text(json.dumps(manifest))

        async with test_client as client:
            response = await client.get("/update/releases", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data["releases"]) == 2
        assert data["releases"][0]["versionCode"] == 95

    @pytest.mark.asyncio
    async def test_releases_requires_auth(self, test_client):
        async with test_client as client:
            response = await client.get("/update/releases")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_download_specific_file(self, test_client, headers, tmp_config_dir):
        releases_dir = tmp_config_dir["releases_dir"]
        apk = releases_dir / "conn-v1.0.0-dev.95.apk"
        apk.write_bytes(b"\x00" * 100)

        async with test_client as client:
            response = await client.get("/update/download/conn-v1.0.0-dev.95.apk", headers=headers)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_download_specific_file_not_found(self, test_client, headers):
        async with test_client as client:
            response = await client.get("/update/download/nonexistent.apk", headers=headers)
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_download_specific_file_path_traversal(self, test_client, headers):
        async with test_client as client:
            response = await client.get("/update/download/..%2F..%2Fetc%2Fpasswd", headers=headers)
        assert response.status_code in (400, 404)

    @pytest.mark.asyncio
    async def test_download_specific_file_with_token(self, test_client, tmp_config_dir):
        releases_dir = tmp_config_dir["releases_dir"]
        apk = releases_dir / "conn-v1.0.0-dev.95.apk"
        apk.write_bytes(b"\x00" * 100)
        token = tmp_config_dir["token"]

        async with test_client as client:
            response = await client.get(f"/update/download/conn-v1.0.0-dev.95.apk?token={token}")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_download_specific_file_requires_auth(self, test_client):
        async with test_client as client:
            response = await client.get("/update/download/conn-v1.0.0-dev.95.apk")
        assert response.status_code == 401


class TestAgentsEndpoint:
    @pytest.mark.asyncio
    async def test_list_agents_empty(self, test_client, headers):
        async with test_client as client:
            response = await client.get("/agents", headers=headers)
        assert response.status_code == 200
        assert response.json()["agents"] == []

    @pytest.mark.asyncio
    async def test_list_agents_requires_auth(self, test_client):
        async with test_client as client:
            response = await client.get("/agents")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_create_agent(self, test_client, headers):
        async with test_client as client:
            response = await client.post("/agents", headers=headers, json={
                "name": "reviewer",
                "description": "Code reviewer",
                "prompt": "You review code.",
                "model": "sonnet",
                "tools": ["Read", "Grep"],
            })
        assert response.status_code == 200
        assert response.json()["agent"] == "reviewer"

    @pytest.mark.asyncio
    async def test_create_and_list(self, test_client, headers):
        async with test_client as client:
            await client.post("/agents", headers=headers, json={
                "name": "reviewer",
                "description": "Code reviewer",
                "model": "sonnet",
            })
            response = await client.get("/agents", headers=headers)
        agents = response.json()["agents"]
        assert len(agents) == 1
        assert agents[0]["name"] == "reviewer"
        assert agents[0]["description"] == "Code reviewer"

    @pytest.mark.asyncio
    async def test_create_duplicate(self, test_client, headers):
        async with test_client as client:
            await client.post("/agents", headers=headers, json={
                "name": "reviewer", "description": "First",
            })
            response = await client.post("/agents", headers=headers, json={
                "name": "reviewer", "description": "Second",
            })
        assert response.status_code == 400
        assert "already exists" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_create_invalid_name(self, test_client, headers):
        async with test_client as client:
            response = await client.post("/agents", headers=headers, json={
                "name": "Bad Name!", "description": "test",
            })
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_get_agent(self, test_client, headers):
        async with test_client as client:
            await client.post("/agents", headers=headers, json={
                "name": "reviewer",
                "description": "Code reviewer",
                "prompt": "You review code.",
                "model": "sonnet",
                "tools": ["Read", "Grep"],
            })
            response = await client.get("/agents/reviewer", headers=headers)
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "reviewer"
        assert data["prompt"] == "You review code."
        assert data["model"] == "sonnet"
        assert data["tools"] == ["Read", "Grep"]

    @pytest.mark.asyncio
    async def test_get_agent_not_found(self, test_client, headers):
        async with test_client as client:
            response = await client.get("/agents/nonexistent", headers=headers)
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_update_agent(self, test_client, headers):
        async with test_client as client:
            await client.post("/agents", headers=headers, json={
                "name": "reviewer", "description": "Original",
            })
            response = await client.put("/agents/reviewer", headers=headers, json={
                "name": "reviewer", "description": "Updated", "prompt": "New prompt",
            })
        assert response.status_code == 200
        assert response.json()["agent"] == "reviewer"

    @pytest.mark.asyncio
    async def test_update_agent_not_found(self, test_client, headers):
        async with test_client as client:
            response = await client.put("/agents/nonexistent", headers=headers, json={
                "name": "nonexistent", "description": "test",
            })
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_agent(self, test_client, headers):
        async with test_client as client:
            await client.post("/agents", headers=headers, json={
                "name": "reviewer", "description": "test",
            })
            response = await client.delete("/agents/reviewer", headers=headers)
        assert response.status_code == 200
        assert response.json()["deleted"] == "reviewer"

    @pytest.mark.asyncio
    async def test_delete_agent_not_found(self, test_client, headers):
        async with test_client as client:
            response = await client.delete("/agents/nonexistent", headers=headers)
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_create_requires_auth(self, test_client):
        async with test_client as client:
            response = await client.post("/agents", json={
                "name": "reviewer", "description": "test",
            })
        assert response.status_code == 401


class TestSendImageEndpoint:
    @pytest.mark.asyncio
    async def test_requires_auth(self, test_client):
        async with test_client as client:
            response = await client.post("/send-image", json={"path": "/tmp/auto-mobile/screenshots/test.png"})
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_rejects_path_outside_allowed_dir(self, test_client, headers):
        async with test_client as client:
            response = await client.post("/send-image", json={"path": "/etc/passwd"}, headers=headers)
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_rejects_path_traversal(self, test_client, headers):
        async with test_client as client:
            response = await client.post(
                "/send-image",
                json={"path": "/tmp/auto-mobile/screenshots/../../etc/passwd"},
                headers=headers,
            )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_rejects_nonexistent_file(self, test_client, headers):
        async with test_client as client:
            response = await client.post(
                "/send-image",
                json={"path": "/tmp/auto-mobile/screenshots/nonexistent.png"},
                headers=headers,
            )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_rejects_non_image_extension(self, test_client, headers, tmp_path):
        # Create a real file with a disallowed extension
        screenshots_dir = Path("/tmp/auto-mobile/screenshots")
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        test_file = screenshots_dir / "test_bad_ext.txt"
        test_file.write_text("not an image")
        try:
            async with test_client as client:
                response = await client.post(
                    "/send-image",
                    json={"path": str(test_file)},
                    headers=headers,
                )
            assert response.status_code == 403
        finally:
            test_file.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_no_active_conversation_returns_404(self, test_client, headers):
        # No conversation_id provided and no active processes
        screenshots_dir = Path("/tmp/auto-mobile/screenshots")
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        test_file = screenshots_dir / "test_no_conv.png"
        test_file.write_bytes(b"\x89PNG")
        try:
            async with test_client as client:
                response = await client.post(
                    "/send-image",
                    json={"path": str(test_file)},
                    headers=headers,
                )
            assert response.status_code == 404
            assert "No active conversation" in response.json()["detail"]
        finally:
            test_file.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_success_with_explicit_conversation_id(self, test_client, headers):
        screenshots_dir = Path("/tmp/auto-mobile/screenshots")
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        test_file = screenshots_dir / "test_success.png"
        test_file.write_bytes(b"\x89PNG")
        try:
            async with test_client as client:
                response = await client.post(
                    "/send-image",
                    json={"path": str(test_file), "conversation_id": "conv_123"},
                    headers=headers,
                )
            assert response.status_code == 200
            data = response.json()
            assert data["ok"] is True
            assert data["conversation_id"] == "conv_123"
        finally:
            test_file.unlink(missing_ok=True)
