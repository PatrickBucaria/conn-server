"""MCP server catalog — pre-configured templates for popular MCP servers.

Each CatalogEntry defines everything about an MCP server except user-specific
credentials. The app shows these as one-tap install options.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path


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
    default_env: dict[str, str] | None = None  # Env vars to include when installing (non-secret)
    setup_note: str | None = None
    doc_url: str | None = None


# ---------------------------------------------------------------------------
# The catalog
# ---------------------------------------------------------------------------

CATALOG: list[CatalogEntry] = [
    # -- No credentials (tap to install) --
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
        id="fetch",
        display_name="Fetch",
        description="Fetch web pages and convert to markdown — useful for reading docs and APIs",
        transport="stdio",
        command="npx",
        args=["-y", "@anthropic-ai/mcp-fetch"],
        doc_url="https://github.com/anthropics/anthropic-mcp-fetch",
    ),
    CatalogEntry(
        id="memory",
        display_name="Memory",
        description="Persistent knowledge graph — save and recall information across conversations",
        transport="stdio",
        command="npx",
        args=["-y", "@anthropic-ai/mcp-memory"],
        doc_url="https://github.com/anthropics/anthropic-mcp-memory",
    ),
    CatalogEntry(
        id="sequential-thinking",
        display_name="Sequential Thinking",
        description="Dynamic problem-solving through structured thought chains with branching and revision",
        transport="stdio",
        command="npx",
        args=["-y", "@anthropic-ai/mcp-sequential-thinking"],
        doc_url="https://github.com/anthropics/anthropic-mcp-sequential-thinking",
    ),
    CatalogEntry(
        id="auto-mobile",
        display_name="AutoMobile",
        description="Android device automation — tap, type, scroll, observe screens, manage emulators",
        transport="stdio",
        command="npx",
        args=["-y", "auto-mobile@latest"],
        default_env={"ANDROID_HOME": str(Path.home() / "Library/Android/sdk")},
        setup_note="Requires an Android emulator or device with USB debugging enabled on the server.",
        doc_url="https://github.com/zillow/auto-mobile",
    ),
    # -- Setup note (confirm before install) --
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
        id="sentry",
        display_name="Sentry",
        description="Search issues, view stack traces, analyze error trends and performance data",
        transport="http",
        url="https://mcp.sentry.dev/mcp",
        setup_note="First use will open an OAuth popup in your browser to authorize Sentry access.",
        doc_url="https://docs.sentry.io/product/sentry-mcp/",
    ),
    CatalogEntry(
        id="figma",
        display_name="Figma",
        description="Read design files, inspect layouts, extract component details and styles",
        transport="http",
        url="https://mcp.figma.com/mcp",
        setup_note="First use will open an OAuth popup to authorize Figma access. Requires a Dev Mode seat.",
        doc_url="https://help.figma.com/hc/en-us/articles/32132100833559",
    ),
    # -- Credentials required --
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
    CatalogEntry(
        id="linear",
        display_name="Linear",
        description="Find, create, and update issues, projects, and comments in Linear",
        transport="http",
        url="https://mcp.linear.app/mcp",
        credentials=[
            CredentialField(
                key="Authorization",
                label="API Key",
                placement="header",
                help_text="Create a personal API key in Linear Settings → API.",
                help_url="https://linear.app/settings/api",
                value_prefix="Bearer ",
            ),
        ],
        doc_url="https://linear.app/docs/mcp",
    ),
    CatalogEntry(
        id="notion",
        display_name="Notion",
        description="Search pages, read content, create and update pages in your Notion workspace",
        transport="stdio",
        command="npx",
        args=["-y", "@notionhq/notion-mcp-server"],
        credentials=[
            CredentialField(
                key="NOTION_TOKEN",
                label="Integration Token",
                placement="env",
                help_text="Create an internal integration and copy the token.",
                help_url="https://www.notion.so/profile/integrations",
            ),
        ],
        doc_url="https://developers.notion.com/docs/mcp",
    ),
    CatalogEntry(
        id="slack",
        display_name="Slack",
        description="Read channels, search messages, summarize threads, post messages",
        transport="stdio",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-slack"],
        credentials=[
            CredentialField(
                key="SLACK_BOT_TOKEN",
                label="Bot Token",
                placement="env",
                help_text="Create a Slack app with bot scopes, install to workspace, copy the xoxb- token.",
                help_url="https://api.slack.com/apps",
            ),
            CredentialField(
                key="SLACK_TEAM_ID",
                label="Team ID",
                placement="env",
                help_text="Your workspace ID (starts with T). Find it in Slack workspace settings.",
            ),
        ],
        doc_url="https://github.com/modelcontextprotocol/servers/tree/main/src/slack",
    ),
    CatalogEntry(
        id="brave-search",
        display_name="Brave Search",
        description="Web search, local business search, news, and AI-powered summaries",
        transport="stdio",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-brave-search"],
        credentials=[
            CredentialField(
                key="BRAVE_API_KEY",
                label="API Key",
                placement="env",
                help_text="Register for a Brave Search API account and create an API key. Free tier available.",
                help_url="https://brave.com/search/api/",
            ),
        ],
        doc_url="https://github.com/modelcontextprotocol/servers/tree/main/src/brave-search",
    ),
    CatalogEntry(
        id="postgres",
        display_name="PostgreSQL",
        description="Read-only access to PostgreSQL — inspect schemas and run SELECT queries",
        transport="stdio",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-postgres"],
        credentials=[
            CredentialField(
                key="DATABASE_URL",
                label="Connection URL",
                placement="env",
                help_text="postgresql://user:pass@host:5432/dbname",
            ),
        ],
        setup_note="Grants read-only access. All queries run in a READ ONLY transaction.",
        doc_url="https://github.com/modelcontextprotocol/servers/tree/main/src/postgres",
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
