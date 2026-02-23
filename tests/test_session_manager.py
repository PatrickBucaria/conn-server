"""Tests for SessionManager — conversation tracking and message history."""

import json

import pytest

from conn_server.session_manager import SessionManager, Conversation


class TestSessionManagerConversations:
    """Test conversation CRUD operations."""

    def test_create_conversation(self, tmp_config_dir):
        sm = SessionManager()
        conv = sm.create_conversation("conv_1", "Test conversation")

        assert conv.id == "conv_1"
        assert conv.name == "Test conversation"
        assert conv.claude_session_id is None
        assert conv.working_dir is None
        assert conv.created_at != ""
        assert conv.last_message_at != ""

    def test_create_conversation_with_working_dir(self, tmp_config_dir):
        sm = SessionManager()
        conv = sm.create_conversation("conv_1", "Test", working_dir="/projects/foo")

        assert conv.working_dir == "/projects/foo"

    def test_get_conversation(self, tmp_config_dir):
        sm = SessionManager()
        sm.create_conversation("conv_1", "Test")

        result = sm.get_conversation("conv_1")
        assert result is not None
        assert result.name == "Test"

    def test_get_conversation_not_found(self, tmp_config_dir):
        sm = SessionManager()
        assert sm.get_conversation("nonexistent") is None

    def test_list_conversations_sorted_by_last_message(self, tmp_config_dir):
        sm = SessionManager()
        sm.create_conversation("conv_old", "Old")
        sm.create_conversation("conv_new", "New")

        result = sm.list_conversations()
        assert len(result) == 2
        # Newest first
        assert result[0]["id"] == "conv_new"
        assert result[1]["id"] == "conv_old"

    def test_delete_conversation(self, tmp_config_dir):
        sm = SessionManager()
        sm.create_conversation("conv_1", "Test")

        assert sm.delete_conversation("conv_1") is True
        assert sm.get_conversation("conv_1") is None

    def test_delete_conversation_not_found(self, tmp_config_dir):
        sm = SessionManager()
        assert sm.delete_conversation("nonexistent") is False

    def test_delete_conversation_removes_history(self, tmp_config_dir):
        sm = SessionManager()
        sm.create_conversation("conv_1", "Test")
        sm.append_history("conv_1", {"role": "user", "text": "hello"})

        history_file = tmp_config_dir["history_dir"] / "conv_1.jsonl"
        assert history_file.exists()

        sm.delete_conversation("conv_1")
        assert not history_file.exists()

    def test_rename_conversation(self, tmp_config_dir):
        sm = SessionManager()
        sm.create_conversation("conv_1", "Old name")
        sm.rename_conversation("conv_1", "New name")

        conv = sm.get_conversation("conv_1")
        assert conv.name == "New name"

    def test_rename_nonexistent_conversation(self, tmp_config_dir):
        sm = SessionManager()
        # Should not raise
        sm.rename_conversation("nonexistent", "Name")

    def test_update_session_id(self, tmp_config_dir):
        sm = SessionManager()
        sm.create_conversation("conv_1", "Test")
        sm.update_session_id("conv_1", "session_abc")

        conv = sm.get_conversation("conv_1")
        assert conv.claude_session_id == "session_abc"

    def test_update_session_id_updates_last_message_at(self, tmp_config_dir):
        sm = SessionManager()
        conv = sm.create_conversation("conv_1", "Test")
        original_time = conv.last_message_at

        import time
        time.sleep(0.01)
        sm.update_session_id("conv_1", "session_abc")

        updated = sm.get_conversation("conv_1")
        assert updated.last_message_at >= original_time


class TestSessionManagerPermissions:
    """Test per-conversation allowed_tools."""

    def test_create_conversation_with_allowed_tools(self, tmp_config_dir):
        sm = SessionManager()
        conv = sm.create_conversation("conv_1", "Test", allowed_tools=["Read", "Grep"])

        assert conv.allowed_tools == ["Read", "Grep"]

    def test_create_conversation_default_allowed_tools(self, tmp_config_dir):
        sm = SessionManager()
        conv = sm.create_conversation("conv_1", "Test")

        assert conv.allowed_tools is None

    def test_update_allowed_tools(self, tmp_config_dir):
        sm = SessionManager()
        sm.create_conversation("conv_1", "Test")

        result = sm.update_allowed_tools("conv_1", ["Read", "Write"])
        assert result is True

        conv = sm.get_conversation("conv_1")
        assert conv.allowed_tools == ["Read", "Write"]

    def test_update_allowed_tools_not_found(self, tmp_config_dir):
        sm = SessionManager()
        assert sm.update_allowed_tools("nonexistent", ["Read"]) is False

    def test_allowed_tools_persist_across_instances(self, tmp_config_dir):
        sm1 = SessionManager()
        sm1.create_conversation("conv_1", "Test", allowed_tools=["Bash", "Glob"])

        sm2 = SessionManager()
        conv = sm2.get_conversation("conv_1")
        assert conv.allowed_tools == ["Bash", "Glob"]

    def test_allowed_tools_in_list_conversations(self, tmp_config_dir):
        sm = SessionManager()
        sm.create_conversation("conv_1", "Test", allowed_tools=["Read"])

        convs = sm.list_conversations()
        assert convs[0]["allowed_tools"] == ["Read"]

    def test_backward_compatibility_no_allowed_tools(self, tmp_config_dir):
        """Existing sessions.json without allowed_tools should load fine."""
        import json
        from conn_server.config import SESSIONS_FILE

        # Write a sessions.json without the allowed_tools field
        data = {"conversations": [{
            "id": "old_conv",
            "name": "Old",
            "claude_session_id": None,
            "created_at": "2024-01-01T00:00:00+00:00",
            "last_message_at": "2024-01-01T00:00:00+00:00",
            "working_dir": None,
        }]}
        with open(SESSIONS_FILE, "w") as f:
            json.dump(data, f)

        sm = SessionManager()
        conv = sm.get_conversation("old_conv")
        assert conv is not None
        assert conv.allowed_tools is None


class TestSessionManagerWorktree:
    """Test worktree tracking fields."""

    def test_create_conversation_default_worktree_fields(self, tmp_config_dir):
        sm = SessionManager()
        conv = sm.create_conversation("conv_1", "Test")

        assert conv.git_worktree_path is None
        assert conv.original_working_dir is None

    def test_update_worktree(self, tmp_config_dir):
        sm = SessionManager()
        sm.create_conversation("conv_1", "Test")

        result = sm.update_worktree("conv_1", "/tmp/worktree/conv_1", "/projects/foo")
        assert result is True

        conv = sm.get_conversation("conv_1")
        assert conv.git_worktree_path == "/tmp/worktree/conv_1"
        assert conv.original_working_dir == "/projects/foo"

    def test_update_worktree_not_found(self, tmp_config_dir):
        sm = SessionManager()
        assert sm.update_worktree("nonexistent", "/tmp/wt", "/tmp/orig") is False

    def test_worktree_fields_persist(self, tmp_config_dir):
        sm1 = SessionManager()
        sm1.create_conversation("conv_1", "Test")
        sm1.update_worktree("conv_1", "/tmp/wt/conv_1", "/projects/foo")

        sm2 = SessionManager()
        conv = sm2.get_conversation("conv_1")
        assert conv.git_worktree_path == "/tmp/wt/conv_1"
        assert conv.original_working_dir == "/projects/foo"

    def test_worktree_fields_in_list_conversations(self, tmp_config_dir):
        sm = SessionManager()
        sm.create_conversation("conv_1", "Test")
        sm.update_worktree("conv_1", "/tmp/wt/conv_1", "/projects/foo")

        convs = sm.list_conversations()
        assert convs[0]["git_worktree_path"] == "/tmp/wt/conv_1"
        assert convs[0]["original_working_dir"] == "/projects/foo"

    def test_backward_compatibility_no_worktree_fields(self, tmp_config_dir):
        """Existing sessions.json without worktree fields should load fine."""
        from conn_server.config import SESSIONS_FILE

        data = {"conversations": [{
            "id": "old_conv",
            "name": "Old",
            "claude_session_id": None,
            "created_at": "2024-01-01T00:00:00+00:00",
            "last_message_at": "2024-01-01T00:00:00+00:00",
            "working_dir": None,
            "allowed_tools": None,
        }]}
        with open(SESSIONS_FILE, "w") as f:
            json.dump(data, f)

        sm = SessionManager()
        conv = sm.get_conversation("old_conv")
        assert conv is not None
        assert conv.git_worktree_path is None
        assert conv.original_working_dir is None


class TestSessionManagerPersistence:
    """Test that data survives re-instantiation (file I/O)."""

    def test_conversations_persist_across_instances(self, tmp_config_dir):
        sm1 = SessionManager()
        sm1.create_conversation("conv_1", "Persistent")

        sm2 = SessionManager()
        conv = sm2.get_conversation("conv_1")
        assert conv is not None
        assert conv.name == "Persistent"

    def test_session_id_persists(self, tmp_config_dir):
        sm1 = SessionManager()
        sm1.create_conversation("conv_1", "Test")
        sm1.update_session_id("conv_1", "session_xyz")

        sm2 = SessionManager()
        conv = sm2.get_conversation("conv_1")
        assert conv.claude_session_id == "session_xyz"


class TestSessionManagerHistory:
    """Test JSONL message history."""

    def test_append_and_get_history(self, tmp_config_dir):
        sm = SessionManager()
        sm.append_history("conv_1", {"role": "user", "text": "hello"})
        sm.append_history("conv_1", {"role": "assistant", "text": "hi there"})

        history = sm.get_history("conv_1")
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[0]["text"] == "hello"
        assert "timestamp" in history[0]
        assert history[1]["role"] == "assistant"

    def test_get_history_empty(self, tmp_config_dir):
        sm = SessionManager()
        assert sm.get_history("nonexistent") == []

    def test_history_appends_incrementally(self, tmp_config_dir):
        sm = SessionManager()
        sm.append_history("conv_1", {"role": "user", "text": "msg1"})
        sm.append_history("conv_1", {"role": "user", "text": "msg2"})
        sm.append_history("conv_1", {"role": "user", "text": "msg3"})

        history = sm.get_history("conv_1")
        assert len(history) == 3
        assert [h["text"] for h in history] == ["msg1", "msg2", "msg3"]

    def test_history_is_valid_jsonl(self, tmp_config_dir):
        sm = SessionManager()
        sm.append_history("conv_1", {"role": "user", "text": "hello"})
        sm.append_history("conv_1", {"role": "assistant", "text": "world"})

        history_file = tmp_config_dir["history_dir"] / "conv_1.jsonl"
        lines = history_file.read_text().strip().split("\n")
        assert len(lines) == 2
        for line in lines:
            parsed = json.loads(line)
            assert "role" in parsed
            assert "text" in parsed
            assert "timestamp" in parsed
