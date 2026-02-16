"""Tests for per-conversation process management and concurrency."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.websockets import WebSocketState

import server as srv


@pytest.fixture(autouse=True)
def clean_global_state():
    """Ensure clean global state for each test."""
    srv.active_processes.clear()
    srv.conversation_locks.clear()
    yield
    srv.active_processes.clear()
    srv.conversation_locks.clear()


class TestConversationLocks:
    def test_creates_new_lock(self):
        lock = srv._get_conversation_lock("conv_1")
        assert isinstance(lock, asyncio.Lock)
        assert "conv_1" in srv.conversation_locks

    def test_returns_same_lock_for_same_id(self):
        lock1 = srv._get_conversation_lock("conv_1")
        lock2 = srv._get_conversation_lock("conv_1")
        assert lock1 is lock2

    def test_creates_distinct_locks_for_different_ids(self):
        lock1 = srv._get_conversation_lock("conv_1")
        lock2 = srv._get_conversation_lock("conv_2")
        assert lock1 is not lock2


class TestCancelConversationProcess:
    @pytest.mark.asyncio
    async def test_cancel_nonexistent_returns_false(self):
        result = await srv._cancel_conversation_process("conv_nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_already_finished_returns_false(self):
        proc = MagicMock()
        proc.returncode = 0  # Already finished
        srv.active_processes["conv_1"] = proc
        result = await srv._cancel_conversation_process("conv_1")
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_running_process_terminates(self):
        proc = MagicMock()
        proc.returncode = None  # Still running
        proc.terminate = MagicMock()
        proc.kill = MagicMock()

        wait_future = asyncio.get_event_loop().create_future()
        wait_future.set_result(None)
        proc.wait = AsyncMock(return_value=None)

        srv.active_processes["conv_1"] = proc
        result = await srv._cancel_conversation_process("conv_1")
        assert result is True
        proc.terminate.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_does_not_affect_other_conversations(self):
        proc1 = MagicMock()
        proc1.returncode = None
        proc1.terminate = MagicMock()
        proc1.wait = AsyncMock(return_value=None)

        proc2 = MagicMock()
        proc2.returncode = None
        proc2.terminate = MagicMock()

        srv.active_processes["conv_1"] = proc1
        srv.active_processes["conv_2"] = proc2

        await srv._cancel_conversation_process("conv_1")
        proc1.terminate.assert_called_once()
        proc2.terminate.assert_not_called()


class TestCancelAllProcesses:
    @pytest.mark.asyncio
    async def test_cancel_all_empty_dict(self):
        await srv._cancel_all_processes()
        # Should not raise

    @pytest.mark.asyncio
    async def test_cancel_all_terminates_all(self):
        procs = {}
        for i in range(3):
            proc = MagicMock()
            proc.returncode = None
            proc.terminate = MagicMock()
            proc.wait = AsyncMock(return_value=None)
            srv.active_processes[f"conv_{i}"] = proc
            procs[f"conv_{i}"] = proc

        await srv._cancel_all_processes()
        for cid, proc in procs.items():
            proc.terminate.assert_called_once()


class TestHandleCancel:
    @pytest.mark.asyncio
    async def test_cancel_with_conversation_id(self):
        proc = MagicMock()
        proc.returncode = None
        proc.terminate = MagicMock()
        proc.wait = AsyncMock(return_value=None)
        srv.active_processes["conv_1"] = proc

        ws = AsyncMock()
        ws.client_state = WebSocketState.CONNECTED

        await srv._handle_cancel(ws, {"conversation_id": "conv_1"})
        proc.terminate.assert_called_once()
        # Should have sent a cancelled event with conversation_id
        ws.send_text.assert_called()
        import json
        sent = json.loads(ws.send_text.call_args[0][0])
        assert sent["type"] == "cancelled"
        assert sent["conversation_id"] == "conv_1"

    @pytest.mark.asyncio
    async def test_cancel_without_conversation_id_cancels_all(self):
        procs = {}
        for cid in ["conv_1", "conv_2"]:
            proc = MagicMock()
            proc.returncode = None
            proc.terminate = MagicMock()
            proc.wait = AsyncMock(return_value=None)
            srv.active_processes[cid] = proc
            procs[cid] = proc

        ws = AsyncMock()
        ws.client_state = WebSocketState.CONNECTED

        await srv._handle_cancel(ws, {})
        for proc in procs.values():
            proc.terminate.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_no_process_returns_error(self):
        ws = AsyncMock()
        ws.client_state = WebSocketState.CONNECTED

        await srv._handle_cancel(ws, {"conversation_id": "conv_nonexistent"})
        import json
        sent = json.loads(ws.send_text.call_args[0][0])
        assert sent["type"] == "error"
        assert "No active process" in sent["detail"]


class TestConcurrentLocking:
    @pytest.mark.asyncio
    async def test_two_conversations_can_lock_simultaneously(self):
        """Verify that two different conversations can hold their locks at the same time."""
        lock1 = srv._get_conversation_lock("conv_1")
        lock2 = srv._get_conversation_lock("conv_2")

        await lock1.acquire()
        # Lock2 should be acquirable even though lock1 is held
        acquired = lock2.locked()
        assert not acquired  # lock2 is NOT locked
        await lock2.acquire()
        assert lock1.locked()
        assert lock2.locked()

        lock1.release()
        lock2.release()
