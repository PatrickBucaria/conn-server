"""Tests for PreviewManager and preview REST endpoints."""

import asyncio
import json
import socket
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

from conn_server.preview_manager import PreviewManager, PREVIEW_PORT_MIN, PREVIEW_PORT_MAX
from conn_server.server import app
from conn_server.session_manager import SessionManager


# ---- PreviewManager unit tests ----

class TestPreviewManagerDetectCommand:
    def test_detect_npm_dev(self, tmp_path):
        pm = PreviewManager()
        pkg = {"scripts": {"dev": "vite"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        cmd = pm._detect_command(str(tmp_path), 8100)
        assert cmd == ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "8100"]

    def test_detect_npm_start(self, tmp_path):
        pm = PreviewManager()
        pkg = {"scripts": {"start": "react-scripts start"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        cmd = pm._detect_command(str(tmp_path), 8100)
        assert cmd == ["npm", "start"]

    def test_detect_django(self, tmp_path):
        pm = PreviewManager()
        (tmp_path / "manage.py").write_text("")
        cmd = pm._detect_command(str(tmp_path), 8100)
        assert cmd == [sys.executable, "manage.py", "runserver", "0.0.0.0:8100"]

    def test_detect_flask(self, tmp_path):
        pm = PreviewManager()
        (tmp_path / "app.py").write_text("")
        cmd = pm._detect_command(str(tmp_path), 8100)
        assert cmd == [sys.executable, "-m", "flask", "run", "--host", "0.0.0.0", "--port", "8100"]

    def test_detect_static_html(self, tmp_path):
        pm = PreviewManager()
        (tmp_path / "index.html").write_text("<html></html>")
        cmd = pm._detect_command(str(tmp_path), 8100)
        assert "http.server" in cmd
        assert str(tmp_path) in cmd

    def test_detect_dist_folder(self, tmp_path):
        pm = PreviewManager()
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<html></html>")
        cmd = pm._detect_command(str(tmp_path), 8100)
        assert "http.server" in cmd
        assert str(dist) in cmd

    def test_detect_unknown_raises(self, tmp_path):
        pm = PreviewManager()
        with pytest.raises(RuntimeError, match="Could not detect"):
            pm._detect_command(str(tmp_path), 8100)


class TestPreviewManagerFindPort:
    def test_find_free_port_returns_in_range(self):
        pm = PreviewManager()
        port = pm._find_free_port()
        assert PREVIEW_PORT_MIN <= port <= PREVIEW_PORT_MAX

    def test_find_free_port_skips_occupied(self):
        pm = PreviewManager()
        # Find a free port first, then occupy it and verify the next one differs
        first_free = pm._find_free_port()
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", first_free))
        try:
            second_free = pm._find_free_port()
            assert second_free != first_free
            assert PREVIEW_PORT_MIN <= second_free <= PREVIEW_PORT_MAX
        finally:
            s.close()


class TestCanPreview:
    def test_npm_dev_project(self, tmp_path):
        pkg = {"scripts": {"dev": "vite"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        assert PreviewManager.can_preview(str(tmp_path)) is True

    def test_npm_start_project(self, tmp_path):
        pkg = {"scripts": {"start": "react-scripts start"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        assert PreviewManager.can_preview(str(tmp_path)) is True

    def test_django_project(self, tmp_path):
        (tmp_path / "manage.py").write_text("")
        assert PreviewManager.can_preview(str(tmp_path)) is True

    def test_flask_project(self, tmp_path):
        (tmp_path / "app.py").write_text("")
        assert PreviewManager.can_preview(str(tmp_path)) is True

    def test_static_html(self, tmp_path):
        (tmp_path / "index.html").write_text("<html></html>")
        assert PreviewManager.can_preview(str(tmp_path)) is True

    def test_dist_html(self, tmp_path):
        (tmp_path / "dist").mkdir()
        (tmp_path / "dist" / "index.html").write_text("<html></html>")
        assert PreviewManager.can_preview(str(tmp_path)) is True

    def test_not_previewable(self, tmp_path):
        assert PreviewManager.can_preview(str(tmp_path)) is False

    def test_package_json_no_scripts(self, tmp_path):
        (tmp_path / "package.json").write_text(json.dumps({"name": "foo"}))
        assert PreviewManager.can_preview(str(tmp_path)) is False


class TestPreviewManagerLifecycle:
    def test_get_preview_returns_none_when_empty(self):
        pm = PreviewManager()
        assert pm.get_preview("nonexistent") is None

    def test_list_previews_empty(self):
        pm = PreviewManager()
        assert pm.list_previews() == []

    @pytest.mark.asyncio
    async def test_stop_returns_false_when_no_preview(self):
        pm = PreviewManager()
        assert await pm.stop("nonexistent") is False

    @pytest.mark.asyncio
    async def test_start_and_stop_static_server(self, tmp_path):
        """Integration test: start a real http.server preview and stop it."""
        pm = PreviewManager()
        (tmp_path / "index.html").write_text("<html><body>test</body></html>")

        info = await pm.start(
            conversation_id="test-conv",
            working_dir=str(tmp_path),
        )

        assert PREVIEW_PORT_MIN <= info.port <= PREVIEW_PORT_MAX
        assert info.conversation_id == "test-conv"
        assert pm.get_preview("test-conv") is not None
        assert len(pm.list_previews()) == 1

        # Verify the server is actually responding
        reader, writer = await asyncio.open_connection("127.0.0.1", info.port)
        writer.close()
        await writer.wait_closed()

        # Stop it
        stopped = await pm.stop("test-conv")
        assert stopped is True
        assert pm.get_preview("test-conv") is None
        assert len(pm.list_previews()) == 0

    @pytest.mark.asyncio
    async def test_start_replaces_existing(self, tmp_path):
        """Starting a preview for the same conversation stops the old one."""
        pm = PreviewManager()
        (tmp_path / "index.html").write_text("<html></html>")

        info1 = await pm.start("test-conv", str(tmp_path))
        port1 = info1.port

        info2 = await pm.start("test-conv", str(tmp_path))
        port2 = info2.port

        # Should have replaced
        assert len(pm.list_previews()) == 1
        assert pm.get_preview("test-conv").port == port2

        await pm.stop_all()

    @pytest.mark.asyncio
    async def test_stop_all(self, tmp_path):
        pm = PreviewManager()
        (tmp_path / "index.html").write_text("<html></html>")

        await pm.start("conv-1", str(tmp_path))
        await pm.start("conv-2", str(tmp_path))
        assert len(pm.list_previews()) == 2

        await pm.stop_all()
        assert len(pm.list_previews()) == 0

    @pytest.mark.asyncio
    async def test_explicit_command(self, tmp_path):
        """Starting with an explicit command uses it instead of auto-detection."""
        pm = PreviewManager()
        (tmp_path / "index.html").write_text("<html></html>")

        # Use a dynamic free port instead of hardcoding
        port = pm._find_free_port()
        info = await pm.start(
            "test-conv",
            str(tmp_path),
            command=[sys.executable, "-m", "http.server", str(port), "--directory", str(tmp_path), "--bind", "0.0.0.0"],
        )
        assert info is not None
        await pm.stop_all()


# ---- REST endpoint tests ----

@pytest.fixture
def test_client(tmp_config_dir):
    with patch("conn_server.server.sessions", SessionManager()), \
         patch("conn_server.server.previews", PreviewManager()):
        transport = ASGITransport(app=app)
        yield AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def headers(tmp_config_dir):
    return {"Authorization": f"Bearer {tmp_config_dir['token']}"}


class TestPreviewEndpoints:
    @pytest.mark.asyncio
    async def test_start_preview_requires_auth(self, test_client):
        async with test_client as client:
            response = await client.post("/preview/start", json={"conversation_id": "test"})
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_start_preview_404_no_conversation(self, test_client, headers):
        async with test_client as client:
            response = await client.post(
                "/preview/start",
                json={"conversation_id": "nonexistent"},
                headers=headers,
            )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_stop_preview_requires_auth(self, test_client):
        async with test_client as client:
            response = await client.post("/preview/stop", json={"conversation_id": "test"})
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_stop_preview_404_no_preview(self, test_client, headers):
        async with test_client as client:
            response = await client.post(
                "/preview/stop",
                json={"conversation_id": "nonexistent"},
                headers=headers,
            )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_check_preview_requires_auth(self, test_client):
        async with test_client as client:
            response = await client.get("/preview/check/test")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_check_preview_404_no_conversation(self, test_client, headers):
        async with test_client as client:
            response = await client.get("/preview/check/nonexistent", headers=headers)
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_check_preview_true(self, test_client, headers, tmp_config_dir):
        project_dir = tmp_config_dir["projects_dir"] / "web-app"
        project_dir.mkdir()
        (project_dir / "index.html").write_text("<html></html>")
        from conn_server.server import sessions
        sessions.create_conversation("check-conv", "Test", working_dir=str(project_dir))
        async with test_client as client:
            response = await client.get("/preview/check/check-conv", headers=headers)
        assert response.status_code == 200
        assert response.json()["previewable"] is True

    @pytest.mark.asyncio
    async def test_check_preview_false(self, test_client, headers, tmp_config_dir):
        project_dir = tmp_config_dir["projects_dir"] / "no-web"
        project_dir.mkdir()
        from conn_server.server import sessions
        sessions.create_conversation("no-web-conv", "Test", working_dir=str(project_dir))
        async with test_client as client:
            response = await client.get("/preview/check/no-web-conv", headers=headers)
        assert response.status_code == 200
        assert response.json()["previewable"] is False

    @pytest.mark.asyncio
    async def test_preview_status_empty(self, test_client, headers):
        async with test_client as client:
            response = await client.get("/preview/status", headers=headers)
        assert response.status_code == 200
        assert response.json()["previews"] == []

    @pytest.mark.asyncio
    async def test_preview_status_requires_auth(self, test_client):
        async with test_client as client:
            response = await client.get("/preview/status")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_start_preview_with_static_project(self, test_client, headers, tmp_config_dir):
        """Integration test: create a conversation with a static project, start preview."""
        project_dir = tmp_config_dir["projects_dir"] / "test-app"
        project_dir.mkdir()
        (project_dir / "index.html").write_text("<html><body>Hello</body></html>")

        async with test_client as client:
            # Create a conversation first (via the conversations endpoint pattern)
            from conn_server.server import sessions
            sessions.create_conversation("test-conv", "Test", working_dir=str(project_dir))

            # Start preview
            response = await client.post(
                "/preview/start",
                json={"conversation_id": "test-conv"},
                headers=headers,
            )
            assert response.status_code == 200
            data = response.json()
            assert "port" in data
            assert PREVIEW_PORT_MIN <= data["port"] <= PREVIEW_PORT_MAX

            # Check status
            status_response = await client.get("/preview/status", headers=headers)
            assert len(status_response.json()["previews"]) == 1

            # Stop preview
            stop_response = await client.post(
                "/preview/stop",
                json={"conversation_id": "test-conv"},
                headers=headers,
            )
            assert stop_response.status_code == 200

            # Verify stopped
            status_response = await client.get("/preview/status", headers=headers)
            assert len(status_response.json()["previews"]) == 0
