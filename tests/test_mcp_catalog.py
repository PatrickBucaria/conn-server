"""Tests for MCP server catalog."""

import pytest

from conn_server.mcp_catalog import CATALOG, get_catalog, CatalogEntry, CredentialField
from conn_server.mcp_config import McpConfigManager, McpServer


class TestCatalogData:
    """Verify the bundled catalog entries are well-formed."""

    def test_catalog_has_entries(self):
        assert len(CATALOG) >= 13

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

    def test_sentry_entry(self):
        entry = next(e for e in CATALOG if e.id == "sentry")
        assert entry.transport == "http"
        assert entry.url == "https://mcp.sentry.dev/mcp"
        assert entry.credentials == []
        assert entry.setup_note  # OAuth note

    def test_figma_entry(self):
        entry = next(e for e in CATALOG if e.id == "figma")
        assert entry.transport == "http"
        assert entry.url == "https://mcp.figma.com/mcp"
        assert entry.credentials == []
        assert entry.setup_note  # OAuth note

    def test_linear_entry(self):
        entry = next(e for e in CATALOG if e.id == "linear")
        assert entry.transport == "http"
        assert entry.url == "https://mcp.linear.app/mcp"
        assert len(entry.credentials) == 1
        assert entry.credentials[0].placement == "header"

    def test_notion_entry(self):
        entry = next(e for e in CATALOG if e.id == "notion")
        assert entry.transport == "stdio"
        assert entry.command == "npx"
        assert len(entry.credentials) == 1
        assert entry.credentials[0].key == "NOTION_TOKEN"
        assert entry.credentials[0].placement == "env"

    def test_slack_entry(self):
        entry = next(e for e in CATALOG if e.id == "slack")
        assert entry.transport == "stdio"
        assert len(entry.credentials) == 2
        keys = {c.key for c in entry.credentials}
        assert keys == {"SLACK_BOT_TOKEN", "SLACK_TEAM_ID"}

    def test_brave_search_entry(self):
        entry = next(e for e in CATALOG if e.id == "brave-search")
        assert entry.transport == "stdio"
        assert len(entry.credentials) == 1
        assert entry.credentials[0].key == "BRAVE_API_KEY"

    def test_postgres_entry(self):
        entry = next(e for e in CATALOG if e.id == "postgres")
        assert entry.transport == "stdio"
        assert len(entry.credentials) == 1
        assert entry.credentials[0].key == "DATABASE_URL"
        assert entry.setup_note  # Read-only note

    def test_no_credential_servers(self):
        """Fetch, Memory, Sequential Thinking should have no credentials."""
        for sid in ("fetch", "memory", "sequential-thinking"):
            entry = next(e for e in CATALOG if e.id == sid)
            assert entry.transport == "stdio"
            assert entry.command == "npx"
            assert entry.credentials == []


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
