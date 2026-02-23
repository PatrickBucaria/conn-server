"""Tests for per-project custom instructions."""

import json
import pytest
from httpx import AsyncClient, ASGITransport

from conn_server.project_config import get_project_config, get_custom_instructions, set_custom_instructions
from conn_server.session_manager import SessionManager
from conn_server.server import app


# --- Unit tests for project_config module ---

def test_get_config_unset(tmp_config_dir):
    """Returns empty instructions for unconfigured project."""
    config = get_project_config("/some/project")
    assert config["path"] == "/some/project"
    assert config["custom_instructions"] == ""


def test_get_custom_instructions_returns_none_when_empty(tmp_config_dir):
    """Returns None when no custom instructions set."""
    assert get_custom_instructions("/some/project") is None


def test_set_and_get_custom_instructions(tmp_config_dir):
    """Round-trip: set instructions then read them back."""
    path = "/Users/pat/Projects/MyApp"
    instructions = "Always use TypeScript. Follow REST naming conventions."
    set_custom_instructions(path, instructions)
    assert get_custom_instructions(path) == instructions

    config = get_project_config(path)
    assert config["path"] == path
    assert config["custom_instructions"] == instructions


def test_clear_instructions_with_empty_string(tmp_config_dir):
    """Setting empty string effectively clears instructions."""
    path = "/Users/pat/Projects/MyApp"
    set_custom_instructions(path, "Some instructions")
    assert get_custom_instructions(path) == "Some instructions"

    set_custom_instructions(path, "")
    assert get_custom_instructions(path) is None


def test_special_characters_in_instructions(tmp_config_dir):
    """Instructions with special characters survive round-trip."""
    path = "/Users/pat/Projects/MyApp"
    instructions = 'Use "double quotes" and \'single quotes\'.\nNewlines too.\n\tAnd tabs.'
    set_custom_instructions(path, instructions)
    assert get_custom_instructions(path) == instructions


def test_overwrite_existing_instructions(tmp_config_dir):
    """Updating instructions overwrites previous value."""
    path = "/Users/pat/Projects/MyApp"
    set_custom_instructions(path, "First version")
    set_custom_instructions(path, "Second version")
    assert get_custom_instructions(path) == "Second version"


# --- REST endpoint tests ---

@pytest.fixture
def test_client(tmp_config_dir):
    from conn_server.server import sessions as _old
    from unittest.mock import patch
    sm = SessionManager()
    with patch.object(app, "_sessions", sm, create=True), \
         patch("conn_server.server.sessions", sm):
        yield {
            "client": AsyncClient(transport=ASGITransport(app=app), base_url="http://test"),
            "token": tmp_config_dir["token"],
            "projects_dir": tmp_config_dir["projects_dir"],
        }


@pytest.mark.asyncio
async def test_get_project_config_endpoint_empty(test_client):
    """GET /projects/config returns empty instructions for unconfigured project."""
    client = test_client["client"]
    token = test_client["token"]
    async with client:
        resp = await client.get(
            "/projects/config",
            params={"path": "/some/project"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["custom_instructions"] == ""


@pytest.mark.asyncio
async def test_put_project_config_endpoint(test_client):
    """PUT /projects/config saves and returns updated instructions."""
    client = test_client["client"]
    token = test_client["token"]
    headers = {"Authorization": f"Bearer {token}"}
    async with client:
        resp = await client.put(
            "/projects/config",
            json={"path": "/some/project", "custom_instructions": "Always use Python 3.12"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["custom_instructions"] == "Always use Python 3.12"

        # Verify it persists via GET
        resp2 = await client.get(
            "/projects/config",
            params={"path": "/some/project"},
            headers=headers,
        )
        assert resp2.json()["custom_instructions"] == "Always use Python 3.12"


@pytest.mark.asyncio
async def test_project_config_requires_auth(test_client):
    """Endpoints reject requests without valid auth."""
    client = test_client["client"]
    async with client:
        resp = await client.get("/projects/config", params={"path": "/x"})
        assert resp.status_code in (401, 403)

        resp = await client.put("/projects/config", json={"path": "/x", "custom_instructions": "y"})
        assert resp.status_code in (401, 403)
