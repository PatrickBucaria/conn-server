"""Conversation tracking, session IDs, and message history."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path

from config import SESSIONS_FILE, HISTORY_DIR


@dataclass
class Conversation:
    id: str
    name: str
    claude_session_id: str | None = None
    created_at: str = ""
    last_message_at: str = ""
    working_dir: str | None = None


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
        with open(SESSIONS_FILE, "w") as f:
            json.dump(data, f, indent=2)

    def list_conversations(self) -> list[dict]:
        return sorted(
            [asdict(c) for c in self._conversations.values()],
            key=lambda c: c["last_message_at"],
            reverse=True,
        )

    def create_conversation(self, conversation_id: str, name: str, working_dir: str | None = None) -> Conversation:
        now = _iso_now()
        conv = Conversation(
            id=conversation_id,
            name=name,
            created_at=now,
            last_message_at=now,
            working_dir=working_dir,
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
        HISTORY_DIR.mkdir(exist_ok=True)
        history_file = HISTORY_DIR / f"{conversation_id}.jsonl"
        entry["timestamp"] = _iso_now()
        with open(history_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

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


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
