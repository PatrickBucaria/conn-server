"""Tests for MCP server configuration management."""

import json
import os

import pytest

from conn_server.mcp_config import McpConfigManager, McpServer


class TestMcpConfigManager:
    """CRUD operations on MCP server definitions."""

    def test_add_stdio_server(self, tmp_config_dir):
        mgr = McpConfigManager()
        server = McpServer(
            name="sentry",
            display_name="Sentry",
            transport="stdio",
            command="npx",
            args=["-y", "@sentry/mcp-server"],
            env={"SENTRY_AUTH_TOKEN": "secret123"},
        )
        result = mgr.add_server(server)
        assert result.name == "sentry"
        assert result.transport == "stdio"

    def test_add_http_server(self, tmp_config_dir):
        mgr = McpConfigManager()
        server = McpServer(
            name="github",
            display_name="GitHub",
            transport="http",
            url="https://api.githubcopilot.com/mcp/",
        )
        result = mgr.add_server(server)
        assert result.name == "github"
        assert result.url == "https://api.githubcopilot.com/mcp/"

    def test_add_duplicate_server_raises(self, tmp_config_dir):
        mgr = McpConfigManager()
        server = McpServer(name="test", display_name="Test", transport="http", url="https://example.com")
        mgr.add_server(server)
        with pytest.raises(ValueError, match="already exists"):
            mgr.add_server(server)

    def test_add_server_invalid_name(self, tmp_config_dir):
        mgr = McpConfigManager()
        server = McpServer(name="bad name!", display_name="Bad", transport="http", url="https://example.com")
        with pytest.raises(ValueError, match="Invalid server name"):
            mgr.add_server(server)

    def test_add_server_invalid_transport(self, tmp_config_dir):
        mgr = McpConfigManager()
        server = McpServer(name="test", display_name="Test", transport="grpc", command="grpc-server")
        with pytest.raises(ValueError, match="Invalid transport"):
            mgr.add_server(server)

    def test_add_stdio_without_command_raises(self, tmp_config_dir):
        mgr = McpConfigManager()
        server = McpServer(name="test", display_name="Test", transport="stdio")
        with pytest.raises(ValueError, match="requires 'command'"):
            mgr.add_server(server)

    def test_add_http_without_url_raises(self, tmp_config_dir):
        mgr = McpConfigManager()
        server = McpServer(name="test", display_name="Test", transport="http")
        with pytest.raises(ValueError, match="requires 'url'"):
            mgr.add_server(server)

    def test_list_servers_masks_env(self, tmp_config_dir):
        mgr = McpConfigManager()
        mgr.add_server(McpServer(
            name="sentry", display_name="Sentry", transport="stdio",
            command="npx", env={"TOKEN": "sk-super-secret-key-12345"},
        ))
        servers = mgr.list_servers()
        assert len(servers) == 1
        assert servers[0]["env"]["TOKEN"] == "sk-s...2345"

    def test_list_servers_masks_short_env(self, tmp_config_dir):
        mgr = McpConfigManager()
        mgr.add_server(McpServer(
            name="test", display_name="Test", transport="stdio",
            command="cmd", env={"KEY": "short"},
        ))
        servers = mgr.list_servers()
        assert servers[0]["env"]["KEY"] == "***"

    def test_get_server(self, tmp_config_dir):
        mgr = McpConfigManager()
        mgr.add_server(McpServer(name="test", display_name="Test", transport="stdio", command="cmd"))
        assert mgr.get_server("test") is not None
        assert mgr.get_server("nonexistent") is None

    def test_update_server(self, tmp_config_dir):
        mgr = McpConfigManager()
        mgr.add_server(McpServer(name="test", display_name="Test", transport="stdio", command="cmd"))
        updated = mgr.update_server("test", {"display_name": "Updated Name", "command": "new-cmd"})
        assert updated.display_name == "Updated Name"
        assert updated.command == "new-cmd"

    def test_update_server_not_found(self, tmp_config_dir):
        mgr = McpConfigManager()
        assert mgr.update_server("nonexistent", {"display_name": "X"}) is None

    def test_update_server_ignores_name_change(self, tmp_config_dir):
        mgr = McpConfigManager()
        mgr.add_server(McpServer(name="test", display_name="Test", transport="stdio", command="cmd"))
        updated = mgr.update_server("test", {"name": "renamed"})
        assert updated.name == "test"

    def test_remove_server(self, tmp_config_dir):
        mgr = McpConfigManager()
        mgr.add_server(McpServer(name="test", display_name="Test", transport="stdio", command="cmd"))
        assert mgr.remove_server("test") is True
        assert mgr.get_server("test") is None

    def test_remove_server_not_found(self, tmp_config_dir):
        mgr = McpConfigManager()
        assert mgr.remove_server("nonexistent") is False

    def test_toggle_server(self, tmp_config_dir):
        mgr = McpConfigManager()
        mgr.add_server(McpServer(name="test", display_name="Test", transport="stdio", command="cmd"))
        assert mgr.toggle_server("test", False) is True
        assert mgr.get_server("test").enabled is False
        assert mgr.toggle_server("test", True) is True
        assert mgr.get_server("test").enabled is True

    def test_toggle_server_not_found(self, tmp_config_dir):
        mgr = McpConfigManager()
        assert mgr.toggle_server("nonexistent", True) is False

    def test_get_enabled_servers(self, tmp_config_dir):
        mgr = McpConfigManager()
        mgr.add_server(McpServer(name="a", display_name="A", transport="stdio", command="cmd"))
        mgr.add_server(McpServer(name="b", display_name="B", transport="stdio", command="cmd", enabled=False))
        mgr.add_server(McpServer(name="c", display_name="C", transport="http", url="https://example.com"))
        enabled = mgr.get_enabled_servers()
        assert len(enabled) == 2
        assert {s.name for s in enabled} == {"a", "c"}

    def test_get_server_names(self, tmp_config_dir):
        mgr = McpConfigManager()
        mgr.add_server(McpServer(name="alpha", display_name="A", transport="stdio", command="cmd"))
        mgr.add_server(McpServer(name="beta", display_name="B", transport="http", url="https://example.com"))
        assert set(mgr.get_server_names()) == {"alpha", "beta"}

    def test_persistence_across_instances(self, tmp_config_dir):
        mgr1 = McpConfigManager()
        mgr1.add_server(McpServer(name="test", display_name="Test", transport="stdio", command="cmd"))
        mgr2 = McpConfigManager()
        assert mgr2.get_server("test") is not None
        assert mgr2.get_server("test").command == "cmd"


class TestMcpConfigFileGeneration:
    """Temp --mcp-config file generation for Claude CLI."""

    def test_write_stdio_config(self, tmp_config_dir):
        mgr = McpConfigManager()
        mgr.add_server(McpServer(
            name="sentry", display_name="Sentry", transport="stdio",
            command="npx", args=["-y", "@sentry/mcp-server"],
            env={"SENTRY_TOKEN": "abc"},
        ))
        path = mgr.write_mcp_config_file(["sentry"])
        assert path is not None
        with open(path) as f:
            config = json.load(f)
        assert "mcpServers" in config
        assert "sentry" in config["mcpServers"]
        sentry = config["mcpServers"]["sentry"]
        assert sentry["type"] == "stdio"
        assert sentry["command"] == "npx"
        assert sentry["args"] == ["-y", "@sentry/mcp-server"]
        assert sentry["env"] == {"SENTRY_TOKEN": "abc"}
        os.unlink(path)

    def test_write_http_config(self, tmp_config_dir):
        mgr = McpConfigManager()
        mgr.add_server(McpServer(
            name="github", display_name="GitHub", transport="http",
            url="https://api.githubcopilot.com/mcp/",
            headers={"Authorization": "Bearer ghp_xxx"},
        ))
        path = mgr.write_mcp_config_file(["github"])
        assert path is not None
        with open(path) as f:
            config = json.load(f)
        gh = config["mcpServers"]["github"]
        assert gh["type"] == "http"
        assert gh["url"] == "https://api.githubcopilot.com/mcp/"
        assert gh["headers"]["Authorization"] == "Bearer ghp_xxx"
        os.unlink(path)

    def test_write_multiple_servers(self, tmp_config_dir):
        mgr = McpConfigManager()
        mgr.add_server(McpServer(name="a", display_name="A", transport="stdio", command="cmd-a"))
        mgr.add_server(McpServer(name="b", display_name="B", transport="http", url="https://b.example.com"))
        path = mgr.write_mcp_config_file(["a", "b"])
        assert path is not None
        with open(path) as f:
            config = json.load(f)
        assert set(config["mcpServers"].keys()) == {"a", "b"}
        os.unlink(path)

    def test_write_skips_disabled_servers(self, tmp_config_dir):
        mgr = McpConfigManager()
        mgr.add_server(McpServer(name="a", display_name="A", transport="stdio", command="cmd"))
        mgr.add_server(McpServer(name="b", display_name="B", transport="stdio", command="cmd", enabled=False))
        path = mgr.write_mcp_config_file(["a", "b"])
        assert path is not None
        with open(path) as f:
            config = json.load(f)
        assert list(config["mcpServers"].keys()) == ["a"]
        os.unlink(path)

    def test_write_returns_none_for_no_matches(self, tmp_config_dir):
        mgr = McpConfigManager()
        assert mgr.write_mcp_config_file(["nonexistent"]) is None

    def test_write_returns_none_for_all_disabled(self, tmp_config_dir):
        mgr = McpConfigManager()
        mgr.add_server(McpServer(name="a", display_name="A", transport="stdio", command="cmd", enabled=False))
        assert mgr.write_mcp_config_file(["a"]) is None

    def test_write_skips_unknown_servers(self, tmp_config_dir):
        mgr = McpConfigManager()
        mgr.add_server(McpServer(name="real", display_name="Real", transport="stdio", command="cmd"))
        path = mgr.write_mcp_config_file(["real", "fake"])
        assert path is not None
        with open(path) as f:
            config = json.load(f)
        assert list(config["mcpServers"].keys()) == ["real"]
        os.unlink(path)


class TestSessionManagerMcpServers:
    """MCP server fields on Conversation objects."""

    def test_create_conversation_with_mcp_servers(self, tmp_config_dir):
        from conn_server.session_manager import SessionManager
        sm = SessionManager()
        conv = sm.create_conversation("conv_1", "Test", mcp_servers=["sentry", "github"])
        assert conv.mcp_servers == ["sentry", "github"]

    def test_create_conversation_default_mcp_servers(self, tmp_config_dir):
        from conn_server.session_manager import SessionManager
        sm = SessionManager()
        conv = sm.create_conversation("conv_1", "Test")
        assert conv.mcp_servers is None

    def test_update_mcp_servers(self, tmp_config_dir):
        from conn_server.session_manager import SessionManager
        sm = SessionManager()
        sm.create_conversation("conv_1", "Test")
        result = sm.update_mcp_servers("conv_1", ["postgres"])
        assert result is True
        conv = sm.get_conversation("conv_1")
        assert conv.mcp_servers == ["postgres"]

    def test_update_mcp_servers_not_found(self, tmp_config_dir):
        from conn_server.session_manager import SessionManager
        sm = SessionManager()
        assert sm.update_mcp_servers("nonexistent", ["sentry"]) is False

    def test_mcp_servers_persist_across_instances(self, tmp_config_dir):
        from conn_server.session_manager import SessionManager
        sm1 = SessionManager()
        sm1.create_conversation("conv_1", "Test", mcp_servers=["sentry"])
        sm2 = SessionManager()
        conv = sm2.get_conversation("conv_1")
        assert conv.mcp_servers == ["sentry"]

    def test_backward_compatibility_no_mcp_servers(self, tmp_config_dir):
        """Existing sessions.json without mcp_servers should load fine."""
        from conn_server.session_manager import SessionManager
        sessions_file = tmp_config_dir["sessions_file"]
        data = {
            "conversations": [{
                "id": "old_conv",
                "name": "Old",
                "claude_session_id": None,
                "created_at": "2024-01-01T00:00:00+00:00",
                "last_message_at": "2024-01-01T00:00:00+00:00",
                "working_dir": None,
                "allowed_tools": None,
                "git_worktree_path": None,
                "original_working_dir": None,
            }]
        }
        sessions_file.write_text(json.dumps(data))
        sm = SessionManager()
        conv = sm.get_conversation("old_conv")
        assert conv is not None
        assert conv.mcp_servers is None
