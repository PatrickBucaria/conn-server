"""Conversation tracking, session IDs, and message history."""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path

from .config import SESSIONS_FILE, HISTORY_DIR

# Conversation IDs must be alphanumeric with hyphens/underscores (used in file paths)
CONVERSATION_ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$")


@dataclass
class Conversation:
    id: str
    name: str
    claude_session_id: str | None = None
    created_at: str = ""
    last_message_at: str = ""
    working_dir: str | None = None
    allowed_tools: list[str] | None = None
    mcp_servers: list[str] | None = None
    git_worktree_path: str | None = None
    original_working_dir: str | None = None
    model: str | None = None
    agent: str | None = None


class SessionManager:
    def __init__(self):
        self._conversations: dict[str, Conversation] = {}
        self._load()

    def _load(self):
        if SESSIONS_FILE.exists():
            with open(SESSIONS_FILE) as f:
                data = json.load(f)
            for c in data.get("conversations", []):
                conv = Conversation(**c)
                self._conversations[conv.id] = conv

    def _save(self):
        data = {"conversations": [asdict(c) for c in self._conversations.values()]}
        content = json.dumps(data, indent=2)
        fd = os.open(str(SESSIONS_FILE), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, content.encode())
        finally:
            os.close(fd)

    def list_conversations(self) -> list[dict]:
        return sorted(
            [asdict(c) for c in self._conversations.values()],
            key=lambda c: c["last_message_at"],
            reverse=True,
        )

    def create_conversation(self, conversation_id: str, name: str, working_dir: str | None = None, allowed_tools: list[str] | None = None, mcp_servers: list[str] | None = None, model: str | None = None, agent: str | None = None) -> Conversation:
        _validate_conversation_id(conversation_id)
        # Idempotent: if conversation already exists, return it without overwriting.
        # This prevents duplicate new_conversation messages (e.g. from client race
        # conditions) from destroying session state (claude_session_id, worktree).
        existing = self._conversations.get(conversation_id)
        if existing:
            return existing
        now = _iso_now()
        conv = Conversation(
            id=conversation_id,
            name=name,
            created_at=now,
            last_message_at=now,
            working_dir=working_dir,
            allowed_tools=allowed_tools,
            mcp_servers=mcp_servers,
            model=model,
            agent=agent,
        )
        self._conversations[conversation_id] = conv
        self._save()
        return conv

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        return self._conversations.get(conversation_id)

    def update_session_id(self, conversation_id: str, claude_session_id: str):
        conv = self._conversations.get(conversation_id)
        if conv:
            conv.claude_session_id = claude_session_id
            conv.last_message_at = _iso_now()
            self._save()

    def update_allowed_tools(self, conversation_id: str, allowed_tools: list[str]) -> bool:
        conv = self._conversations.get(conversation_id)
        if conv:
            conv.allowed_tools = allowed_tools
            self._save()
            return True
        return False

    def update_mcp_servers(self, conversation_id: str, mcp_servers: list[str]) -> bool:
        conv = self._conversations.get(conversation_id)
        if conv:
            conv.mcp_servers = mcp_servers
            self._save()
            return True
        return False

    def update_worktree(self, conversation_id: str, worktree_path: str | None, original_dir: str | None) -> bool:
        conv = self._conversations.get(conversation_id)
        if conv:
            conv.git_worktree_path = worktree_path
            conv.original_working_dir = original_dir
            self._save()
            return True
        return False

    def get_worktrees_for_project(self, project_dir: str) -> list[Conversation]:
        """Return all conversations with worktrees targeting the given project directory."""
        return [
            c for c in self._conversations.values()
            if c.git_worktree_path and c.original_working_dir == project_dir
        ]

    def rename_conversation(self, conversation_id: str, new_name: str):
        conv = self._conversations.get(conversation_id)
        if conv:
            conv.name = new_name
            self._save()

    def delete_conversation(self, conversation_id: str) -> bool:
        if conversation_id in self._conversations:
            del self._conversations[conversation_id]
            self._save()
            # Delete history file
            history_file = HISTORY_DIR / f"{conversation_id}.jsonl"
            if history_file.exists():
                history_file.unlink()
            return True
        return False

    def append_history(self, conversation_id: str, entry: dict):
        """Append a message to the conversation's JSONL history."""
        _validate_conversation_id(conversation_id)
        HISTORY_DIR.mkdir(mode=0o700, exist_ok=True)
        history_file = HISTORY_DIR / f"{conversation_id}.jsonl"
        entry["timestamp"] = _iso_now()
        line = json.dumps(entry) + "\n"
        # Open with restricted permissions (creates as 0600, appends if exists)
        fd = os.open(str(history_file), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode())
        finally:
            os.close(fd)

    def get_history(self, conversation_id: str) -> list[dict]:
        """Read all history entries for a conversation."""
        history_file = HISTORY_DIR / f"{conversation_id}.jsonl"
        if not history_file.exists():
            return []
        entries = []
        with open(history_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries


def _validate_conversation_id(conversation_id: str):
    """Validate conversation ID format to prevent path traversal."""
    if not CONVERSATION_ID_PATTERN.match(conversation_id):
        raise ValueError(
            f"Invalid conversation ID '{conversation_id}': must be 1-128 alphanumeric "
            f"characters, hyphens, or underscores"
        )


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
