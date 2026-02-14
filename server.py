"""FastAPI bridge server — WebSocket endpoint wrapping claude -p subprocess."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Header, HTTPException
from starlette.websockets import WebSocketState

from auth import verify_token
from config import load_config, get_working_dir
from session_manager import SessionManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Global state
claude_lock = asyncio.Lock()
active_process: asyncio.subprocess.Process | None = None
start_time: float = 0
sessions = SessionManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global start_time
    start_time = time.time()
    config = load_config()
    logger.info(f"Server starting — token: {config['auth_token'][:8]}...")
    yield
    logger.info("Server shutting down")


app = FastAPI(lifespan=lifespan)


# ---------- REST endpoints ----------

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - start_time),
    }


@app.get("/conversations")
async def list_conversations(authorization: str = Header(None)):
    _verify_rest_auth(authorization)
    return {"conversations": sessions.list_conversations()}


@app.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, authorization: str = Header(None)):
    _verify_rest_auth(authorization)
    if sessions.delete_conversation(conversation_id):
        return {"deleted": conversation_id}
    raise HTTPException(status_code=404, detail="Conversation not found")


@app.get("/conversations/{conversation_id}/history")
async def get_conversation_history(conversation_id: str, authorization: str = Header(None)):
    _verify_rest_auth(authorization)
    conv = sessions.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"history": sessions.get_history(conversation_id)}


def _verify_rest_auth(authorization: str | None):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.removeprefix("Bearer ")
    if not verify_token(token):
        raise HTTPException(status_code=403, detail="Invalid token")


# ---------- WebSocket endpoint ----------

@app.websocket("/ws/chat")
async def ws_chat(websocket: WebSocket):
    await websocket.accept()
    authenticated = False

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            msg_type = msg.get("type")

            if msg_type == "auth":
                if verify_token(msg.get("token", "")):
                    authenticated = True
                    await _send(websocket, {"type": "auth_ok"})
                    logger.info("Client authenticated")
                else:
                    await _send(websocket, {"type": "error", "detail": "Invalid token"})
                    await websocket.close(code=4001, reason="Invalid token")
                    return
                continue

            if not authenticated:
                await _send(websocket, {"type": "error", "detail": "Not authenticated"})
                await websocket.close(code=4001, reason="Not authenticated")
                return

            if msg_type == "message":
                await _handle_message(websocket, msg)
            elif msg_type == "new_conversation":
                await _handle_new_conversation(websocket, msg)
            elif msg_type == "cancel":
                await _handle_cancel(websocket)
            else:
                await _send(websocket, {"type": "error", "detail": f"Unknown message type: {msg_type}"})

    except (WebSocketDisconnect, RuntimeError):
        logger.info("Client disconnected")
    except Exception as e:
        logger.exception(f"WebSocket error: {e}")


async def _handle_message(websocket: WebSocket, msg: dict):
    text = msg.get("text", "")
    conversation_id = msg.get("conversation_id", "")
    session_id = msg.get("session_id")

    if not text:
        await _send(websocket, {"type": "error", "detail": "Empty message"})
        return

    if claude_lock.locked():
        logger.info("Lock held — cancelling previous process for new message")
        await _cancel_active_process()
        # Wait briefly for the lock to release
        try:
            await asyncio.wait_for(claude_lock.acquire(), timeout=5.0)
            claude_lock.release()
        except asyncio.TimeoutError:
            await _send(websocket, {"type": "busy", "detail": "Another query is still finishing"})
            return

    # Look up session_id from conversation if not provided
    if not session_id and conversation_id:
        conv = sessions.get_conversation(conversation_id)
        if conv:
            session_id = conv.claude_session_id
        else:
            # Auto-create conversation if it doesn't exist
            sessions.create_conversation(conversation_id, text[:50])

    # Log user message to history
    sessions.append_history(conversation_id, {
        "role": "user",
        "text": text,
    })

    async with claude_lock:
        await _run_claude(websocket, text, conversation_id, session_id)


async def _handle_new_conversation(websocket: WebSocket, msg: dict):
    """Create a new conversation — tracked in session manager."""
    name = msg.get("name", "New conversation")
    conversation_id = msg.get("conversation_id", f"conv_{int(time.time())}")

    conv = sessions.create_conversation(conversation_id, name)
    logger.info(f"Created conversation: {conv.id} ({conv.name})")

    await _send(websocket, {
        "type": "conversation_created",
        "conversation_id": conv.id,
        "name": conv.name,
    })


async def _cancel_active_process():
    """Terminate the active claude subprocess if one is running."""
    global active_process
    if active_process and active_process.returncode is None:
        logger.info("Terminating active claude process")
        active_process.terminate()
        try:
            await asyncio.wait_for(active_process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            active_process.kill()
        return True
    return False


async def _handle_cancel(websocket: WebSocket):
    if await _cancel_active_process():
        await _send(websocket, {"type": "cancelled"})
    else:
        await _send(websocket, {"type": "error", "detail": "No active process to cancel"})


async def _run_claude(websocket: WebSocket, text: str, conversation_id: str, session_id: str | None):
    """Spawn claude -p subprocess and stream events back via WebSocket."""
    global active_process

    cmd = [
        "claude", "-p", text,
        "--output-format", "stream-json",
        "--allowedTools", "Read,Write,Edit,Bash,Glob,Grep",
        "--max-turns", "50",
        "--verbose",
        "--append-system-prompt",
        "The user is communicating with you remotely via ClaudeRemote, "
        "an Android app that connects to this machine over the local network. "
        "They cannot see your full terminal output or interact with files directly. "
        "Keep responses concise and focused on actionable results.",
    ]

    if session_id:
        cmd.extend(["--resume", session_id])

    logger.info(f"Running: {' '.join(cmd[:6])}...")

    accumulated_text = ""

    # Clear CLAUDECODE env var so claude doesn't think it's nested
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    try:
        active_process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=get_working_dir(),
        )

        new_session_id = session_id

        client_gone = False

        async for line in active_process.stdout:
            if not client_gone and websocket.client_state != WebSocketState.CONNECTED:
                logger.info("Client disconnected during streaming — continuing to capture response")
                client_gone = True

            line = line.decode().strip()
            if not line:
                continue

            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Always capture text and session ID, even after client disconnect
            if not client_gone:
                forwarded = await _forward_event(websocket, event, conversation_id)
                if forwarded and forwarded.get("type") == "text_delta":
                    accumulated_text += forwarded.get("text", "")
            else:
                # Client gone — still accumulate text from events
                event_type = event.get("type")
                if event_type == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        accumulated_text += delta.get("text", "")
                elif event_type == "assistant" and "message" in event:
                    for block in event["message"].get("content", []):
                        if block.get("type") == "text":
                            accumulated_text += block["text"]

            # Capture session ID from result events
            if event.get("type") == "result":
                new_session_id = event.get("session_id", new_session_id)

        await active_process.wait()

        # Log stderr for debugging
        if active_process.stderr:
            stderr_data = await active_process.stderr.read()
            if stderr_data:
                logger.warning(f"claude stderr: {stderr_data.decode().strip()}")

        logger.info(f"claude process exited with code {active_process.returncode}")

        # Update session tracking
        if new_session_id and conversation_id:
            sessions.update_session_id(conversation_id, new_session_id)

        # Log assistant response to history
        if accumulated_text:
            sessions.append_history(conversation_id, {
                "role": "assistant",
                "text": accumulated_text,
            })

        if websocket.client_state == WebSocketState.CONNECTED:
            await _send(websocket, {
                "type": "message_complete",
                "conversation_id": conversation_id,
                "session_id": new_session_id,
            })

    except Exception as e:
        logger.exception(f"claude subprocess error: {e}")
        if websocket.client_state == WebSocketState.CONNECTED:
            await _send(websocket, {"type": "error", "detail": str(e)})
    finally:
        active_process = None


async def _forward_event(websocket: WebSocket, event: dict, conversation_id: str) -> dict | None:
    """Map claude stream-json events to our WebSocket protocol. Returns the forwarded message or None."""
    event_type = event.get("type")

    if event_type == "assistant" and "message" in event:
        message = event["message"]
        for block in message.get("content", []):
            if block.get("type") == "text":
                out = {
                    "type": "text_delta",
                    "text": block["text"],
                    "conversation_id": conversation_id,
                }
                await _send(websocket, out)
                return out
            elif block.get("type") == "tool_use":
                out = {
                    "type": "tool_start",
                    "tool": block.get("name", ""),
                    "input_summary": _summarize_tool_input(block.get("name"), block.get("input", {})),
                    "conversation_id": conversation_id,
                }
                await _send(websocket, out)
                return out

    elif event_type == "content_block_delta":
        delta = event.get("delta", {})
        if delta.get("type") == "text_delta":
            out = {
                "type": "text_delta",
                "text": delta.get("text", ""),
                "conversation_id": conversation_id,
            }
            await _send(websocket, out)
            return out

    elif event_type == "content_block_start":
        block = event.get("content_block", {})
        if block.get("type") == "tool_use":
            out = {
                "type": "tool_start",
                "tool": block.get("name", ""),
                "input_summary": "",
                "conversation_id": conversation_id,
            }
            await _send(websocket, out)
            return out

    elif event_type == "content_block_stop":
        out = {"type": "tool_done", "conversation_id": conversation_id}
        await _send(websocket, out)
        return out

    return None


def _summarize_tool_input(tool_name: str | None, input_data: dict) -> str:
    """Create a human-readable summary of tool input."""
    if not tool_name:
        return ""

    if tool_name in ("Read", "Glob", "Grep"):
        return input_data.get("file_path") or input_data.get("pattern") or input_data.get("path", "")
    elif tool_name == "Edit":
        return input_data.get("file_path", "")
    elif tool_name == "Write":
        return input_data.get("file_path", "")
    elif tool_name == "Bash":
        cmd = input_data.get("command", "")
        return cmd[:80] + ("..." if len(cmd) > 80 else "")
    return str(input_data)[:80]


async def _send(websocket: WebSocket, data: dict):
    """Send JSON to WebSocket if still connected."""
    if websocket.client_state != WebSocketState.CONNECTED:
        return
    try:
        await websocket.send_json(data)
    except (WebSocketDisconnect, RuntimeError):
        pass


if __name__ == "__main__":
    import uvicorn
    config = load_config()
    uvicorn.run(app, host=config["host"], port=config["port"])
