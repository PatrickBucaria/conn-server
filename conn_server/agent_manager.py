"""Agent management — reads/writes Claude Code agent .md files in ~/.claude/agents/."""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

AGENTS_DIR = Path.home() / ".claude" / "agents"

# Agent name: lowercase letters, digits, hyphens (1-64 chars)
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


@dataclass
class AgentInfo:
    name: str
    description: str
    prompt: str = ""
    model: str | None = None
    tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    permission_mode: str | None = None
    mcp_servers: list[str] | None = None
    max_turns: int | None = None


class AgentManager:
    def __init__(self, agents_dir: Path | None = None):
        self._dir = agents_dir or AGENTS_DIR

    def list_agents(self) -> list[dict]:
        """List all agents (without full prompt text for brevity)."""
        if not self._dir.exists():
            return []
        agents = []
        for path in sorted(self._dir.glob("*.md")):
            try:
                agent = self._parse_file(path)
                agents.append(self._to_summary(agent))
            except Exception:
                continue  # Skip malformed files
        return agents

    def get_agent(self, name: str) -> AgentInfo | None:
        """Get full agent details including prompt."""
        path = self._dir / f"{name}.md"
        if not path.exists():
            return None
        try:
            return self._parse_file(path)
        except Exception:
            return None

    def create_agent(self, agent: AgentInfo) -> AgentInfo:
        """Create a new agent .md file."""
        _validate_agent(agent)
        path = self._dir / f"{agent.name}.md"
        if path.exists():
            raise ValueError(f"Agent '{agent.name}' already exists")
        self._dir.mkdir(parents=True, exist_ok=True)
        path.write_text(self._to_markdown(agent))
        return agent

    def update_agent(self, name: str, agent: AgentInfo) -> AgentInfo | None:
        """Update an existing agent .md file."""
        path = self._dir / f"{name}.md"
        if not path.exists():
            return None
        _validate_agent(agent)
        # If name changed, remove old file
        if agent.name != name:
            new_path = self._dir / f"{agent.name}.md"
            if new_path.exists():
                raise ValueError(f"Agent '{agent.name}' already exists")
            path.unlink()
            path = new_path
        path.write_text(self._to_markdown(agent))
        return agent

    def delete_agent(self, name: str) -> bool:
        """Delete an agent .md file."""
        path = self._dir / f"{name}.md"
        if path.exists():
            path.unlink()
            return True
        return False

    def _parse_file(self, path: Path) -> AgentInfo:
        """Parse a markdown file with YAML frontmatter into AgentInfo."""
        content = path.read_text()
        frontmatter, body = _parse_frontmatter(content)

        name = frontmatter.get("name", path.stem)
        description = frontmatter.get("description", "")

        # Parse tools — can be comma-separated string or already a list
        tools = _parse_string_list(frontmatter.get("tools"))
        disallowed_tools = _parse_string_list(frontmatter.get("disallowedTools"))
        mcp_servers = _parse_string_list(frontmatter.get("mcpServers"))

        max_turns = frontmatter.get("maxTurns")
        if max_turns is not None:
            max_turns = int(max_turns)

        return AgentInfo(
            name=name,
            description=description,
            prompt=body.strip(),
            model=frontmatter.get("model"),
            tools=tools,
            disallowed_tools=disallowed_tools,
            permission_mode=frontmatter.get("permissionMode"),
            mcp_servers=mcp_servers,
            max_turns=max_turns,
        )

    def _to_markdown(self, agent: AgentInfo) -> str:
        """Convert AgentInfo to markdown with YAML frontmatter."""
        lines = ["---"]
        lines.append(f"name: {agent.name}")
        lines.append(f"description: {agent.description}")
        if agent.tools:
            lines.append(f"tools: {', '.join(agent.tools)}")
        if agent.disallowed_tools:
            lines.append(f"disallowedTools: {', '.join(agent.disallowed_tools)}")
        if agent.model:
            lines.append(f"model: {agent.model}")
        if agent.permission_mode:
            lines.append(f"permissionMode: {agent.permission_mode}")
        if agent.mcp_servers:
            lines.append(f"mcpServers: {', '.join(agent.mcp_servers)}")
        if agent.max_turns is not None:
            lines.append(f"maxTurns: {agent.max_turns}")
        lines.append("---")
        lines.append("")
        if agent.prompt:
            lines.append(agent.prompt)
            lines.append("")
        return "\n".join(lines)

    def _to_summary(self, agent: AgentInfo) -> dict:
        """Convert to dict for list responses (includes all fields)."""
        d = {
            "name": agent.name,
            "description": agent.description,
            "model": agent.model,
            "tools": agent.tools,
            "disallowed_tools": agent.disallowed_tools,
            "permission_mode": agent.permission_mode,
            "mcp_servers": agent.mcp_servers,
            "max_turns": agent.max_turns,
        }
        # Remove None values for cleaner JSON
        return {k: v for k, v in d.items() if v is not None}


def _validate_agent(agent: AgentInfo):
    """Validate agent fields."""
    if not NAME_PATTERN.match(agent.name):
        raise ValueError(
            f"Invalid agent name '{agent.name}': must be lowercase letters, digits, "
            f"and hyphens (1-64 chars, must start with a letter)"
        )
    if not agent.description:
        raise ValueError("Agent description is required")

    valid_models = {"sonnet", "opus", "haiku", "inherit"}
    if agent.model and agent.model not in valid_models:
        raise ValueError(f"Invalid model '{agent.model}': must be one of {valid_models}")

    valid_permission_modes = {"default", "plan", "acceptEdits", "dontAsk", "bypassPermissions"}
    if agent.permission_mode and agent.permission_mode not in valid_permission_modes:
        raise ValueError(
            f"Invalid permission mode '{agent.permission_mode}': must be one of {valid_permission_modes}"
        )


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from markdown content.

    Returns (frontmatter_dict, body_text).
    Simple parser — no PyYAML dependency needed for flat key-value pairs.
    """
    if not content.startswith("---"):
        return {}, content

    # Find the closing ---
    end = content.find("---", 3)
    if end == -1:
        return {}, content

    yaml_block = content[3:end].strip()
    body = content[end + 3:]

    frontmatter = {}
    for line in yaml_block.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        colon_idx = line.find(":")
        if colon_idx == -1:
            continue
        key = line[:colon_idx].strip()
        value = line[colon_idx + 1:].strip()
        # Remove surrounding quotes if present
        if (value.startswith('"') and value.endswith('"')) or \
           (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        frontmatter[key] = value

    return frontmatter, body


def _parse_string_list(value) -> list[str] | None:
    """Parse a comma-separated string or list into a list of strings."""
    if value is None:
        return None
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        return [item.strip() for item in value.split(",") if item.strip()]
    return None
