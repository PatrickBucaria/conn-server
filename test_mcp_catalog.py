"""Tests for MCP server catalog."""

import pytest

from mcp_catalog import CATALOG, get_catalog, CatalogEntry, CredentialField
from mcp_config import McpConfigManager, McpServer


class TestCatalogData:
    """Verify the bundled catalog entries are well-formed."""

    def test_catalog_has_entries(self):
        assert len(CATALOG) >= 3

    def test_all_entries_have_required_fields(self):
        for entry in CATALOG:
            assert entry.id
            assert entry.display_name
            assert entry.description
            assert entry.transport in ("stdio", "http", "sse")
            if entry.transport == "stdio":
                assert entry.command
            else:
                assert entry.url

    def test_catalog_ids_unique(self):
        ids = [e.id for e in CATALOG]
        assert len(ids) == len(set(ids))

    def test_playwright_entry(self):
        entry = next(e for e in CATALOG if e.id == "playwright")
        assert entry.transport == "stdio"
        assert entry.command == "npx"
        assert entry.credentials == []

    def test_firebase_entry(self):
        entry = next(e for e in CATALOG if e.id == "firebase")
        assert entry.transport == "stdio"
        assert entry.command == "npx"
        assert entry.credentials == []
        assert entry.setup_note  # Should have setup instructions

    def test_github_entry(self):
        entry = next(e for e in CATALOG if e.id == "github")
        assert entry.transport == "http"
        assert entry.url
        assert len(entry.credentials) == 1
        cred = entry.credentials[0]
        assert cred.placement == "header"
        assert cred.value_prefix == "Bearer "


class TestGetCatalog:
    """The get_catalog() helper returns entries with installed flags."""

    def test_no_installed(self):
        result = get_catalog(set())
        assert len(result) == len(CATALOG)
        assert all(e["installed"] is False for e in result)

    def test_one_installed(self):
        result = get_catalog({"playwright"})
        pw = next(e for e in result if e["id"] == "playwright")
        gh = next(e for e in result if e["id"] == "github")
        assert pw["installed"] is True
        assert gh["installed"] is False

    def test_all_installed(self):
        ids = {e.id for e in CATALOG}
        result = get_catalog(ids)
        assert all(e["installed"] is True for e in result)

    def test_unknown_installed_names_ignored(self):
        result = get_catalog({"nonexistent-server"})
        assert all(e["installed"] is False for e in result)

    def test_returns_all_fields(self):
        result = get_catalog(set())
        gh = next(e for e in result if e["id"] == "github")
        assert gh["display_name"] == "GitHub"
        assert gh["transport"] == "http"
        assert gh["url"]
        assert len(gh["credentials"]) == 1
        assert gh["credentials"][0]["key"] == "Authorization"
        assert gh["doc_url"]


class TestCatalogEndpoint:
    """Integration test for GET /mcp/catalog via McpConfigManager."""

    def test_installed_flag_reflects_existing_servers(self, tmp_config_dir):
        mgr = McpConfigManager()
        mgr.add_server(McpServer(
            name="playwright",
            display_name="Playwright",
            transport="stdio",
            command="npx",
            args=["-y", "@anthropic-ai/mcp-playwright"],
        ))
        installed = set(mgr.get_server_names())
        result = get_catalog(installed)
        pw = next(e for e in result if e["id"] == "playwright")
        fb = next(e for e in result if e["id"] == "firebase")
        assert pw["installed"] is True
        assert fb["installed"] is False
