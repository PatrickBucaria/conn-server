"""Tests for the EventForwarder — maps Claude stream-json to our WebSocket protocol."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from server import EventForwarder, _summarize_tool_input


@pytest.fixture
def mock_websocket():
    """Create a mock WebSocket that records sent messages."""
    ws = AsyncMock()
    ws.client_state = "CONNECTED"
    sent_messages = []

    async def capture_send(data):
        sent_messages.append(data)

    # Patch _send to capture messages instead of using real websocket
    return ws, sent_messages


@pytest.fixture
def forwarder():
    return EventForwarder()


class TestTextDeltaForwarding:
    @pytest.mark.asyncio
    async def test_text_delta_forwarded(self, forwarder, mock_websocket):
        ws, sent = mock_websocket
        event = {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "Hello"},
        }

        with patch("server._send", new_callable=AsyncMock) as mock_send:
            result = await forwarder.forward(ws, event, "conv_1")

        assert result is not None
        assert result["type"] == "text_delta"
        assert result["text"] == "Hello"
        assert result["conversation_id"] == "conv_1"

    @pytest.mark.asyncio
    async def test_text_delta_sets_streaming_flag(self, forwarder, mock_websocket):
        ws, _ = mock_websocket
        event = {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "Hi"},
        }

        with patch("server._send", new_callable=AsyncMock):
            await forwarder.forward(ws, event, "conv_1")

        assert forwarder._saw_streaming_events is True


class TestToolUseForwarding:
    @pytest.mark.asyncio
    async def test_tool_start_with_immediate_input(self, forwarder, mock_websocket):
        ws, _ = mock_websocket
        event = {
            "type": "content_block_start",
            "content_block": {
                "type": "tool_use",
                "name": "Read",
                "input": {"file_path": "/tmp/test.py"},
            },
        }

        with patch("server._send", new_callable=AsyncMock) as mock_send:
            result = await forwarder.forward(ws, event, "conv_1")

        assert result is not None
        assert result["type"] == "tool_start"
        assert result["tool"] == "Read"
        assert result["input_summary"] == "/tmp/test.py"
        assert forwarder._tool_start_sent is True

    @pytest.mark.asyncio
    async def test_tool_start_deferred_when_no_input(self, forwarder, mock_websocket):
        ws, _ = mock_websocket
        event = {
            "type": "content_block_start",
            "content_block": {
                "type": "tool_use",
                "name": "Bash",
                "input": {},
            },
        }

        with patch("server._send", new_callable=AsyncMock) as mock_send:
            result = await forwarder.forward(ws, event, "conv_1")

        # Should not have sent anything yet — waiting for input_json_delta
        assert result is None
        mock_send.assert_not_called()
        assert forwarder._active_tool_name == "Bash"
        assert forwarder._tool_start_sent is False

    @pytest.mark.asyncio
    async def test_tool_input_accumulation_sends_start(self, forwarder, mock_websocket):
        ws, _ = mock_websocket

        # First: content_block_start with empty input
        start_event = {
            "type": "content_block_start",
            "content_block": {"type": "tool_use", "name": "Bash", "input": {}},
        }
        with patch("server._send", new_callable=AsyncMock):
            await forwarder.forward(ws, start_event, "conv_1")

        # Then: input_json_delta with complete JSON
        delta_event = {
            "type": "content_block_delta",
            "delta": {
                "type": "input_json_delta",
                "partial_json": '{"command": "ls -la"}',
            },
        }
        with patch("server._send", new_callable=AsyncMock) as mock_send:
            result = await forwarder.forward(ws, delta_event, "conv_1")

        assert result is not None
        assert result["type"] == "tool_start"
        assert result["tool"] == "Bash"
        assert "ls -la" in result["input_summary"]
        assert forwarder._tool_start_sent is True

    @pytest.mark.asyncio
    async def test_tool_input_accumulation_partial_json(self, forwarder, mock_websocket):
        ws, _ = mock_websocket

        # content_block_start
        start_event = {
            "type": "content_block_start",
            "content_block": {"type": "tool_use", "name": "Read", "input": {}},
        }
        with patch("server._send", new_callable=AsyncMock):
            await forwarder.forward(ws, start_event, "conv_1")

        # First partial — not enough to parse
        delta1 = {
            "type": "content_block_delta",
            "delta": {"type": "input_json_delta", "partial_json": '{"file_'},
        }
        with patch("server._send", new_callable=AsyncMock) as mock_send:
            result = await forwarder.forward(ws, delta1, "conv_1")
        assert result is None  # Can't parse yet

        # Second partial — still not parseable
        delta2 = {
            "type": "content_block_delta",
            "delta": {"type": "input_json_delta", "partial_json": 'path": "/tmp/'},
        }
        with patch("server._send", new_callable=AsyncMock):
            result = await forwarder.forward(ws, delta2, "conv_1")
        assert result is None

        # Final partial — now parseable
        delta3 = {
            "type": "content_block_delta",
            "delta": {"type": "input_json_delta", "partial_json": 'test.py"}'},
        }
        with patch("server._send", new_callable=AsyncMock):
            result = await forwarder.forward(ws, delta3, "conv_1")
        assert result is not None
        assert result["input_summary"] == "/tmp/test.py"

    @pytest.mark.asyncio
    async def test_tool_done_sends_start_if_not_sent(self, forwarder, mock_websocket):
        ws, _ = mock_websocket

        # content_block_start with no input
        start_event = {
            "type": "content_block_start",
            "content_block": {"type": "tool_use", "name": "Glob", "input": {}},
        }
        with patch("server._send", new_callable=AsyncMock):
            await forwarder.forward(ws, start_event, "conv_1")

        # content_block_stop — should send tool_start then tool_done
        stop_event = {"type": "content_block_stop"}
        send_calls = []
        async def capture_send(ws, data):
            send_calls.append(data)

        with patch("server._send", side_effect=capture_send):
            result = await forwarder.forward(ws, stop_event, "conv_1")

        assert len(send_calls) == 2
        assert send_calls[0]["type"] == "tool_start"
        assert send_calls[0]["tool"] == "Glob"
        assert send_calls[1]["type"] == "tool_done"

    @pytest.mark.asyncio
    async def test_tool_done_after_start_already_sent(self, forwarder, mock_websocket):
        ws, _ = mock_websocket

        # content_block_start with input (sends immediately)
        start_event = {
            "type": "content_block_start",
            "content_block": {
                "type": "tool_use",
                "name": "Read",
                "input": {"file_path": "/tmp/test.py"},
            },
        }
        with patch("server._send", new_callable=AsyncMock):
            await forwarder.forward(ws, start_event, "conv_1")

        # content_block_stop — should only send tool_done
        stop_event = {"type": "content_block_stop"}
        send_calls = []
        async def capture_send(ws, data):
            send_calls.append(data)

        with patch("server._send", side_effect=capture_send):
            await forwarder.forward(ws, stop_event, "conv_1")

        assert len(send_calls) == 1
        assert send_calls[0]["type"] == "tool_done"

    @pytest.mark.asyncio
    async def test_content_block_stop_without_tool_ignored(self, forwarder, mock_websocket):
        ws, _ = mock_websocket
        # Stop event when no tool is active
        event = {"type": "content_block_stop"}

        with patch("server._send", new_callable=AsyncMock) as mock_send:
            result = await forwarder.forward(ws, event, "conv_1")

        assert result is None
        mock_send.assert_not_called()


class TestAssistantFallback:
    @pytest.mark.asyncio
    async def test_assistant_event_used_when_no_streaming(self, forwarder, mock_websocket):
        ws, _ = mock_websocket
        event = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Hello from assistant"},
                ],
            },
        }

        with patch("server._send", new_callable=AsyncMock) as mock_send:
            result = await forwarder.forward(ws, event, "conv_1")

        assert result is not None
        assert result["type"] == "text_delta"
        assert result["text"] == "Hello from assistant"

    @pytest.mark.asyncio
    async def test_assistant_event_ignored_when_streaming_seen(self, forwarder, mock_websocket):
        ws, _ = mock_websocket

        # First see a streaming delta
        delta = {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "Hi"},
        }
        with patch("server._send", new_callable=AsyncMock):
            await forwarder.forward(ws, delta, "conv_1")

        # Then get assistant event — should be ignored
        assistant = {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "Hello from assistant"}],
            },
        }
        with patch("server._send", new_callable=AsyncMock) as mock_send:
            result = await forwarder.forward(ws, assistant, "conv_1")

        assert result is None
        mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_assistant_fallback_with_tool_use(self, forwarder, mock_websocket):
        ws, _ = mock_websocket
        event = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Let me read that"},
                    {"type": "tool_use", "name": "Read", "input": {"file_path": "/test.py"}},
                ],
            },
        }

        send_calls = []
        async def capture_send(ws, data):
            send_calls.append(data)

        with patch("server._send", side_effect=capture_send):
            await forwarder.forward(ws, event, "conv_1")

        types = [c["type"] for c in send_calls]
        assert "text_delta" in types
        assert "tool_start" in types
        assert "tool_done" in types


class TestToolInputSummarizer:
    def test_read_file_path(self):
        assert _summarize_tool_input("Read", {"file_path": "/tmp/test.py"}) == "/tmp/test.py"

    def test_glob_pattern(self):
        assert _summarize_tool_input("Glob", {"pattern": "**/*.py"}) == "**/*.py"

    def test_grep_pattern(self):
        assert _summarize_tool_input("Grep", {"pattern": "TODO"}) == "TODO"

    def test_grep_path(self):
        assert _summarize_tool_input("Grep", {"path": "/src"}) == "/src"

    def test_edit_file_path(self):
        assert _summarize_tool_input("Edit", {"file_path": "/tmp/edit.py"}) == "/tmp/edit.py"

    def test_write_file_path(self):
        assert _summarize_tool_input("Write", {"file_path": "/tmp/new.py"}) == "/tmp/new.py"

    def test_bash_command(self):
        assert _summarize_tool_input("Bash", {"command": "ls -la"}) == "ls -la"

    def test_bash_long_command_truncated(self):
        long_cmd = "x" * 100
        result = _summarize_tool_input("Bash", {"command": long_cmd})
        assert len(result) <= 83  # 80 + "..."
        assert result.endswith("...")

    def test_empty_tool_name(self):
        assert _summarize_tool_input(None, {"anything": "value"}) == ""
        assert _summarize_tool_input("", {"anything": "value"}) == ""

    def test_unknown_tool_uses_str_repr(self):
        result = _summarize_tool_input("CustomTool", {"key": "value"})
        assert "key" in result
        assert "value" in result

    def test_read_empty_input(self):
        assert _summarize_tool_input("Read", {}) == ""

    def test_bash_empty_command(self):
        assert _summarize_tool_input("Bash", {}) == ""
