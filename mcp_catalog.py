"""MCP server catalog — pre-configured templates for popular MCP servers.

Each CatalogEntry defines everything about an MCP server except user-specific
credentials. The app shows these as one-tap install options.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict


@dataclass
class CredentialField:
    key: str            # Where the value goes (env var name or header name)
    label: str          # Human-readable label shown in the app
    placement: str      # "env" | "header"
    help_text: str = ""
    help_url: str = ""
    value_prefix: str = ""  # Prepended to user input (e.g. "Bearer ")


@dataclass
class CatalogEntry:
    id: str
    display_name: str
    description: str
    transport: str              # "stdio" | "http"
    command: str | None = None
    args: list[str] | None = None
    url: str | None = None
    credentials: list[CredentialField] = field(default_factory=list)
    setup_note: str | None = None
    doc_url: str | None = None


# ---------------------------------------------------------------------------
# The catalog
# ---------------------------------------------------------------------------

CATALOG: list[CatalogEntry] = [
    CatalogEntry(
        id="playwright",
        display_name="Playwright",
        description="Browser automation — navigate pages, take screenshots, interact with elements",
        transport="stdio",
        command="npx",
        args=["-y", "@anthropic-ai/mcp-playwright"],
        doc_url="https://github.com/anthropics/anthropic-mcp-playwright",
    ),
    CatalogEntry(
        id="firebase",
        display_name="Firebase",
        description="Manage Firebase projects, Firestore, Auth users, security rules, and Data Connect",
        transport="stdio",
        command="npx",
        args=["-y", "firebase-tools@latest", "mcp"],
        setup_note="Requires 'firebase login' on the server. Run it in a terminal on your Mac Mini first.",
        doc_url="https://firebase.google.com/docs/ai-assistance/mcp-server",
    ),
    CatalogEntry(
        id="github",
        display_name="GitHub",
        description="Search repos, manage issues and PRs, read code, create branches",
        transport="http",
        url="https://api.githubcopilot.com/mcp/",
        credentials=[
            CredentialField(
                key="Authorization",
                label="Personal Access Token",
                placement="header",
                help_text="Create a fine-grained PAT with the repos/permissions you need.",
                help_url="https://github.com/settings/tokens?type=beta",
                value_prefix="Bearer ",
            ),
        ],
        doc_url="https://github.com/github/github-mcp-server",
    ),
]


def get_catalog(installed_names: set[str]) -> list[dict]:
    """Return catalog entries with an `installed` flag."""
    result = []
    for entry in CATALOG:
        d = asdict(entry)
        d["installed"] = entry.id in installed_names
        result.append(d)
    return result
