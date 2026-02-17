"""Tests for REST API endpoints."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient, ASGITransport

from mcp_config import McpConfigManager
from server import app
from session_manager import SessionManager


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
    with patch("server.sessions", SessionManager()), \
         patch("server.mcp_servers", McpConfigManager()):
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
        import server
        server.sessions.create_conversation("conv_git", "Test", working_dir=str(project_dir))

        async with test_client as client:
            response = await client.get("/conversations", headers=headers)
        convs = response.json()["conversations"]
        conv = next(c for c in convs if c["id"] == "conv_git")
        assert conv["git_branch"] == "feature"

    @pytest.mark.asyncio
    async def test_conversations_null_branch_for_non_git(self, test_client, headers, tmp_config_dir):
        """Conversations without a git working_dir should have null git_branch."""
        import server
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
                files={"file": ("test.txt", b"hello", "text/plain")},
            )
        assert response.status_code == 400

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
