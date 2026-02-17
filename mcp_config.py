"""MCP (Model Context Protocol) server configuration management.

Stores MCP server definitions in ~/.claude-remote/mcp_servers.json and
generates temporary --mcp-config files for Claude CLI invocations.
"""
from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass, asdict, field
from pathlib import Path

from config import CONFIG_DIR

MCP_SERVERS_FILE = CONFIG_DIR / "mcp_servers.json"

# Valid transport types
VALID_TRANSPORTS = {"stdio", "http", "sse"}

# Name must be alphanumeric, hyphens, underscores (1-64 chars)
NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


@dataclass
class McpServer:
    name: str
    display_name: str
    transport: str  # "stdio" | "http" | "sse"
    command: str | None = None  # For stdio
    args: list[str] | None = None  # For stdio
    url: str | None = None  # For http/sse
    headers: dict[str, str] | None = None  # For http/sse
    env: dict[str, str] | None = None
    enabled: bool = True


class McpConfigManager:
    def __init__(self):
        self._servers: dict[str, McpServer] = {}
        self._load()

    def _load(self):
        if MCP_SERVERS_FILE.exists():
            with open(MCP_SERVERS_FILE) as f:
                data = json.load(f)
            for s in data.get("servers", []):
                server = McpServer(**s)
                self._servers[server.name] = server

    def _save(self):
        data = {"servers": [asdict(s) for s in self._servers.values()]}
        MCP_SERVERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(MCP_SERVERS_FILE, "w") as f:
            json.dump(data, f, indent=2)

    def list_servers(self) -> list[dict]:
        """List all servers with env values masked."""
        result = []
        for s in self._servers.values():
            d = asdict(s)
            if d.get("env"):
                d["env"] = {k: _mask_value(v) for k, v in d["env"].items()}
            result.append(d)
        return result

    def get_server(self, name: str) -> McpServer | None:
        return self._servers.get(name)

    def add_server(self, server: McpServer) -> McpServer:
        _validate_server(server)
        if server.name in self._servers:
            raise ValueError(f"Server '{server.name}' already exists")
        self._servers[server.name] = server
        self._save()
        return server

    def update_server(self, name: str, updates: dict) -> McpServer | None:
        server = self._servers.get(name)
        if not server:
            return None

        for key, value in updates.items():
            if key == "name":
                continue  # Can't rename via update
            if hasattr(server, key):
                setattr(server, key, value)

        _validate_server(server)
        self._save()
        return server

    def remove_server(self, name: str) -> bool:
        if name in self._servers:
            del self._servers[name]
            self._save()
            return True
        return False

    def toggle_server(self, name: str, enabled: bool) -> bool:
        server = self._servers.get(name)
        if server:
            server.enabled = enabled
            self._save()
            return True
        return False

    def get_enabled_servers(self) -> list[McpServer]:
        return [s for s in self._servers.values() if s.enabled]

    def get_server_names(self) -> list[str]:
        """Return all server names."""
        return list(self._servers.keys())

    def write_mcp_config_file(self, server_names: list[str]) -> str | None:
        """Write a temp JSON file in Claude CLI --mcp-config format.

        Only includes servers that exist and are globally enabled.
        Returns the file path, or None if no servers matched.
        """
        mcp_servers = {}
        for name in server_names:
            server = self._servers.get(name)
            if not server or not server.enabled:
                continue

            entry: dict = {}
            if server.transport == "stdio":
                entry["type"] = "stdio"
                if server.command:
                    entry["command"] = server.command
                if server.args:
                    entry["args"] = server.args
            else:
                entry["type"] = server.transport
                if server.url:
                    entry["url"] = server.url
            if server.headers:
                entry["headers"] = server.headers
            if server.env:
                entry["env"] = server.env

            mcp_servers[name] = entry

        if not mcp_servers:
            return None

        config = {"mcpServers": mcp_servers}

        # Write to a temp file that persists until the process ends
        tmp = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            prefix="mcp_config_",
            delete=False,
        )
        json.dump(config, tmp, indent=2)
        tmp.close()
        return tmp.name


def _validate_server(server: McpServer):
    """Validate server fields, raising ValueError on invalid input."""
    if not NAME_PATTERN.match(server.name):
        raise ValueError(
            f"Invalid server name '{server.name}': must be 1-64 alphanumeric characters, hyphens, or underscores"
        )

    if server.transport not in VALID_TRANSPORTS:
        raise ValueError(f"Invalid transport '{server.transport}': must be one of {VALID_TRANSPORTS}")

    if server.transport == "stdio":
        if not server.command:
            raise ValueError("stdio transport requires 'command'")
    else:
        if not server.url:
            raise ValueError(f"{server.transport} transport requires 'url'")


def _mask_value(value: str) -> str:
    """Mask a secret value for API responses."""
    if len(value) <= 8:
        return "***"
    return value[:4] + "..." + value[-4:]
