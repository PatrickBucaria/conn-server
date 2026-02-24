"""FastAPI bridge server — WebSocket endpoint wrapping claude -p subprocess."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import time
import typing
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Header, HTTPException, UploadFile, File, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.websockets import WebSocketState

from .auth import verify_token
from .config import load_config, get_working_dir, get_host, get_port, print_startup_banner, UPLOADS_DIR, LOG_DIR, WORKTREES_DIR, RELEASES_DIR
from .git_utils import get_current_branch, is_git_repo, create_worktree, remove_worktree
from .agent_manager import AgentManager
from .mcp_catalog import get_catalog
from .mcp_config import McpConfigManager
from .preview_manager import PreviewManager
from .project_config import get_project_config, get_custom_instructions, set_custom_instructions
from .session_manager import SessionManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Global state
active_processes: dict[str, asyncio.subprocess.Process] = {}
conversation_locks: dict[str, asyncio.Lock] = {}
start_time: float = 0
sessions = SessionManager()
previews = PreviewManager()
mcp_servers = McpConfigManager()
agents = AgentManager()

# Track connected WebSocket clients for broadcasting events
connected_clients: list[WebSocket] = []


def _get_conversation_lock(conversation_id: str) -> asyncio.Lock:
    """Get or create a per-conversation lock."""
    if conversation_id not in conversation_locks:
        conversation_locks[conversation_id] = asyncio.Lock()
    return conversation_locks[conversation_id]


@asynccontextmanager
async def lifespan(app: FastAPI):
    global start_time
    start_time = time.time()
    print_startup_banner()
    yield
    logger.info("Server shutting down — stopping preview servers")
    await previews.stop_all()


app = FastAPI(lifespan=lifespan)

# ---------- Dashboard static files ----------

DASHBOARD_DIR = Path(__file__).parent / "dashboard"


@app.get("/")
async def root():
    """Redirect root to dashboard."""
    return FileResponse(DASHBOARD_DIR / "index.html")


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
    convs = sessions.list_conversations()
    # Compute git branch per unique working_dir (cached within request)
    branch_cache: dict[str, str | None] = {}
    for conv in convs:
        wd = conv.get("working_dir")
        if wd:
            if wd not in branch_cache:
                branch_cache[wd] = get_current_branch(wd)
            conv["git_branch"] = branch_cache[wd]
        else:
            conv["git_branch"] = None
    return {"conversations": convs}


@app.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, authorization: str = Header(None)):
    _verify_rest_auth(authorization)
    # Clean up worktree before deleting conversation data
    conv = sessions.get_conversation(conversation_id)
    if conv and conv.git_worktree_path and conv.original_working_dir:
        remove_worktree(conv.original_working_dir, conversation_id)
        logger.info(f"Cleaned up worktree for conversation {conversation_id}")
    # Stop any preview server for this conversation
    await previews.stop(conversation_id)
    if sessions.delete_conversation(conversation_id):
        # Clean up uploaded images for this conversation
        conv_uploads = UPLOADS_DIR / conversation_id
        if conv_uploads.exists():
            shutil.rmtree(conv_uploads)
            logger.info(f"Cleaned up uploads for conversation {conversation_id}")
        return {"deleted": conversation_id}
    raise HTTPException(status_code=404, detail="Conversation not found")


@app.get("/conversations/{conversation_id}/history")
async def get_conversation_history(conversation_id: str, authorization: str = Header(None)):
    _verify_rest_auth(authorization)
    conv = sessions.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"history": sessions.get_history(conversation_id)}


ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif"}
MAX_UPLOAD_SIZE = 20 * 1024 * 1024  # 20MB


@app.post("/upload")
async def upload_image(
    conversation_id: str = Query(...),
    file: UploadFile = File(...),
    authorization: str = Header(None),
):
    _verify_rest_auth(authorization)

    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext}")

    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail="File too large (max 20MB)")

    conv_dir = UPLOADS_DIR / conversation_id
    conv_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{uuid.uuid4().hex[:12]}_{file.filename}"
    dest = conv_dir / filename
    dest.write_bytes(content)

    logger.info(f"Uploaded {len(content)} bytes to {dest}")
    return {"path": str(dest)}


# File extensions that are safe to serve back to the client
SERVABLE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp"}


def _get_servable_roots() -> list[Path]:
    """Return directories from which /files is allowed to serve."""
    roots = [
        UPLOADS_DIR.resolve(),
        Path(get_working_dir()).resolve(),
        WORKTREES_DIR.resolve(),
        Path("/tmp").resolve(),
    ]
    # Include per-conversation working dirs (e.g. project-specific paths)
    for conv_data in sessions.list_conversations():
        wd = conv_data.get("working_dir")
        if wd:
            roots.append(Path(wd).resolve())
    return roots


@app.get("/files")
async def serve_file(path: str = Query(...), token: str = Query(None), authorization: str = Header(None)):
    """Serve an image file from the server filesystem.

    Used to send screenshots and other images generated by tools (e.g. Playwright)
    back to the mobile client. Only serves files with known image extensions.
    Restricted to files under the uploads dir, working dir, or /tmp.
    Accepts auth via Authorization header OR ?token= query parameter (for image loaders).
    """
    if token:
        if not verify_token(token):
            raise HTTPException(status_code=403, detail="Invalid token")
    else:
        _verify_rest_auth(authorization)

    file_path = Path(path).resolve()

    # Security: block path traversal via .. components
    if ".." in Path(path).parts:
        raise HTTPException(status_code=400, detail="Invalid path")

    # Security: restrict to known safe directories
    allowed_roots = _get_servable_roots()
    if not any(file_path == root or root in file_path.parents for root in allowed_roots):
        raise HTTPException(status_code=403, detail="Path is outside allowed directories")

    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    ext = file_path.suffix.lower()
    if ext not in SERVABLE_EXTENSIONS:
        raise HTTPException(status_code=403, detail=f"File type not allowed: {ext}")

    return FileResponse(str(file_path))


@app.get("/conversations/active")
async def active_conversations(authorization: str = Header(None)):
    """List conversation IDs that currently have a running Claude process."""
    _verify_rest_auth(authorization)
    active_ids = [cid for cid, proc in active_processes.items() if proc.returncode is None]
    return {"active_conversation_ids": active_ids}


class SendImageRequest(BaseModel):
    path: str
    conversation_id: str | None = None


SEND_IMAGE_ALLOWED_ROOTS = [Path("/tmp/auto-mobile/screenshots")]


@app.post("/send-image")
async def send_image(req: SendImageRequest, authorization: str = Header(None)):
    """Inject an image into a conversation's WebSocket stream.

    Used by Claude Code to send screenshots (e.g. from AutoMobile) to the
    mobile client for visual review.  If conversation_id is omitted, defaults
    to the conversation with an active Claude process.
    """
    _verify_rest_auth(authorization)

    file_path = Path(req.path).resolve()
    if ".." in Path(req.path).parts:
        raise HTTPException(status_code=400, detail="Invalid path")

    if not any(file_path == root or root.resolve() in file_path.parents
               for root in SEND_IMAGE_ALLOWED_ROOTS):
        raise HTTPException(status_code=403, detail="Path outside allowed directories")

    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    ext = file_path.suffix.lower()
    if ext not in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        raise HTTPException(status_code=403, detail=f"File type not allowed: {ext}")

    # Resolve conversation_id — default to the active one
    conv_id = req.conversation_id
    if not conv_id:
        active_ids = [cid for cid, proc in active_processes.items() if proc.returncode is None]
        if len(active_ids) == 1:
            conv_id = active_ids[0]
        elif len(active_ids) == 0:
            raise HTTPException(status_code=404, detail="No active conversation")
        else:
            raise HTTPException(status_code=409, detail="Multiple active conversations — specify conversation_id")

    # Broadcast image + message_complete so the client finalizes the message
    image_event = {"type": "image", "path": req.path, "conversation_id": conv_id}
    complete_event = {"type": "message_complete", "conversation_id": conv_id}
    for ws in list(connected_clients):
        await _send(ws, image_event)
        await _send(ws, complete_event)

    return {"ok": True, "conversation_id": conv_id}


@app.get("/projects")
async def list_projects(authorization: str = Header(None)):
    """List subdirectories of the projects root as available project contexts."""
    _verify_rest_auth(authorization)
    projects_root = Path(get_working_dir())
    if not projects_root.is_dir():
        return {"projects": []}
    projects = [{"name": "All Projects", "path": str(projects_root), "git_branch": get_current_branch(str(projects_root))}]
    for entry in sorted(projects_root.iterdir()):
        if entry.is_dir() and not entry.name.startswith("."):
            projects.append({"name": entry.name, "path": str(entry), "git_branch": get_current_branch(str(entry))})
    return {"projects": projects}


class CreateProjectRequest(BaseModel):
    name: str


@app.post("/projects")
async def create_project(request: CreateProjectRequest, authorization: str = Header(None)):
    """Create a new project directory under the projects root."""
    _verify_rest_auth(authorization)

    name = request.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Project name is required")
    if "/" in name or "\\" in name or ".." in name or name.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid project name")

    projects_root = Path(get_working_dir())
    new_project = projects_root / name

    if new_project.exists():
        raise HTTPException(status_code=409, detail="Project already exists")

    new_project.mkdir(parents=True)
    logger.info(f"Created project directory: {new_project}")

    return {"name": name, "path": str(new_project)}


@app.get("/projects/config")
async def get_project_config_endpoint(path: str = Query(...), authorization: str = Header(None)):
    """Get configuration (custom instructions) for a project."""
    _verify_rest_auth(authorization)
    return get_project_config(path)


class UpdateProjectConfigRequest(BaseModel):
    path: str
    custom_instructions: str


@app.put("/projects/config")
async def update_project_config(request: UpdateProjectConfigRequest, authorization: str = Header(None)):
    """Update custom instructions for a project."""
    _verify_rest_auth(authorization)
    set_custom_instructions(request.path, request.custom_instructions)
    return get_project_config(request.path)


@app.post("/restart")
async def restart_server(authorization: str = Header(None)):
    """Gracefully restart the server. Cancels active Claude process, then exits.
    launchd (KeepAlive=true) will restart the process automatically."""
    _verify_rest_auth(authorization)

    logger.info("Restart requested — shutting down gracefully")

    # Stop all preview servers
    await previews.stop_all()

    # Cancel all active Claude subprocesses
    await _cancel_all_processes()

    # Schedule the actual exit slightly after returning the response
    async def _exit():
        await asyncio.sleep(0.5)
        logger.info("Exiting for restart")
        os.kill(os.getpid(), signal.SIGTERM)

    asyncio.create_task(_exit())

    return {"status": "restarting"}


# Global deploy state
deploy_process: asyncio.subprocess.Process | None = None


@app.post("/deploy")
async def deploy_build(authorization: str = Header(None)):
    """Trigger a build and deploy to Firebase App Distribution.
    Runs the build script as a background process and returns immediately."""
    global deploy_process
    _verify_rest_auth(authorization)

    if deploy_process and deploy_process.returncode is None:
        raise HTTPException(status_code=409, detail="Deploy already in progress")

    script = Path.home() / "Projects" / "Conn" / "scripts" / "build-and-distribute.sh"
    if not script.exists():
        raise HTTPException(status_code=500, detail="Build script not found")

    log_file = LOG_DIR / f"deploy-{int(time.time())}.log"

    logger.info(f"Deploy triggered — logging to {log_file}")

    with open(log_file, "w") as f:
        deploy_process = await asyncio.create_subprocess_exec(
            str(script), "conn", "Deployed from Conn",
            stdout=f,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(script.parent.parent),
        )

    asyncio.create_task(_wait_deploy(deploy_process, log_file))

    return {"status": "deploying", "log_file": str(log_file)}


@app.get("/deploy/status")
async def deploy_status(authorization: str = Header(None)):
    """Check the status of the current or last deploy."""
    _verify_rest_auth(authorization)

    if deploy_process is None:
        return {"status": "idle"}

    if deploy_process.returncode is None:
        return {"status": "in_progress"}

    return {
        "status": "success" if deploy_process.returncode == 0 else "failed",
        "exit_code": deploy_process.returncode,
    }


async def _wait_deploy(proc: asyncio.subprocess.Process, log_file: Path):
    """Wait for deploy to complete and log the result."""
    await proc.wait()
    if proc.returncode == 0:
        logger.info(f"Deploy succeeded (log: {log_file})")
    else:
        logger.error(f"Deploy failed with exit code {proc.returncode} (log: {log_file})")


# ---------- Self-hosted update endpoints ----------


@app.get("/update/check")
async def update_check(authorization: str = Header(None)):
    """Return the latest available APK version info."""
    _verify_rest_auth(authorization)
    version_file = RELEASES_DIR / "version.json"
    if not version_file.exists():
        raise HTTPException(status_code=404, detail="No release available")
    return json.loads(version_file.read_text())


@app.get("/update/download")
async def update_download(token: str = Query(None), authorization: str = Header(None)):
    """Download the latest APK. Accepts auth via header or ?token= query param."""
    if token:
        if not verify_token(token):
            raise HTTPException(status_code=403, detail="Invalid token")
    else:
        _verify_rest_auth(authorization)

    apk_file = RELEASES_DIR / "latest.apk"
    if not apk_file.exists():
        raise HTTPException(status_code=404, detail="No APK available")

    return FileResponse(
        str(apk_file),
        media_type="application/vnd.android.package-archive",
        filename="conn-update.apk",
    )


@app.get("/update/releases")
async def update_releases(authorization: str = Header(None)):
    """Return the list of all available builds."""
    _verify_rest_auth(authorization)
    manifest = RELEASES_DIR / "releases.json"
    if not manifest.exists():
        return {"releases": []}
    return {"releases": json.loads(manifest.read_text())}


@app.get("/update/download/{filename}")
async def update_download_file(filename: str, token: str = Query(None), authorization: str = Header(None)):
    """Download a specific APK by filename. Accepts auth via header or ?token= query param."""
    if token:
        if not verify_token(token):
            raise HTTPException(status_code=403, detail="Invalid token")
    else:
        _verify_rest_auth(authorization)

    # Prevent path traversal
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    apk_file = RELEASES_DIR / filename
    if not apk_file.exists():
        raise HTTPException(status_code=404, detail="APK not found")

    return FileResponse(
        str(apk_file),
        media_type="application/vnd.android.package-archive",
        filename=filename,
    )


class PreviewStartRequest(BaseModel):
    conversation_id: str


class PreviewStopRequest(BaseModel):
    conversation_id: str


@app.get("/preview/check/{conversation_id}")
async def check_preview(conversation_id: str, authorization: str = Header(None)):
    """Check if a conversation's project directory is previewable."""
    _verify_rest_auth(authorization)
    conv = sessions.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    working_dir = conv.working_dir or get_working_dir()
    return {"previewable": PreviewManager.can_preview(working_dir)}


@app.post("/preview/start")
async def start_preview(request: PreviewStartRequest, authorization: str = Header(None)):
    """Start a dev server for the given conversation's project directory."""
    _verify_rest_auth(authorization)

    conv = sessions.get_conversation(request.conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    working_dir = conv.working_dir or get_working_dir()

    try:
        info = await previews.start(
            conversation_id=request.conversation_id,
            working_dir=working_dir,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Broadcast preview_available to all connected WebSocket clients
    preview_event = {
        "type": "preview_available",
        "conversation_id": request.conversation_id,
        "port": info.port,
    }
    for ws in connected_clients:
        await _send(ws, preview_event)

    return {"port": info.port}


@app.post("/preview/stop")
async def stop_preview(request: PreviewStopRequest, authorization: str = Header(None)):
    """Stop the preview server for a conversation."""
    _verify_rest_auth(authorization)
    stopped = await previews.stop(request.conversation_id)
    if not stopped:
        raise HTTPException(status_code=404, detail="No preview running for this conversation")

    # Broadcast preview_stopped to all connected WebSocket clients
    stop_event = {
        "type": "preview_stopped",
        "conversation_id": request.conversation_id,
    }
    for ws in connected_clients:
        await _send(ws, stop_event)

    return {"stopped": True}


@app.get("/preview/status")
async def preview_status(authorization: str = Header(None)):
    """List all active preview servers."""
    _verify_rest_auth(authorization)
    return {"previews": previews.list_previews()}


# ---------- MCP server management endpoints ----------


class McpServerRequest(BaseModel):
    name: str
    display_name: str = ""
    transport: str = "stdio"
    command: typing.Optional[str] = None
    args: typing.Optional[typing.List[str]] = None
    url: typing.Optional[str] = None
    headers: typing.Optional[typing.Dict[str, str]] = None
    env: typing.Optional[typing.Dict[str, str]] = None
    enabled: bool = True


class McpServerToggleRequest(BaseModel):
    enabled: bool


@app.get("/mcp/servers")
async def list_mcp_servers(authorization: str = Header(None)):
    """List all configured MCP servers (env values masked)."""
    _verify_rest_auth(authorization)
    return {"servers": mcp_servers.list_servers()}


@app.post("/mcp/servers")
async def add_mcp_server(request: McpServerRequest, authorization: str = Header(None)):
    """Add a new MCP server."""
    _verify_rest_auth(authorization)
    from .mcp_config import McpServer
    server = McpServer(
        name=request.name,
        display_name=request.display_name or request.name,
        transport=request.transport,
        command=request.command,
        args=request.args,
        url=request.url,
        headers=request.headers,
        env=request.env,
        enabled=request.enabled,
    )
    try:
        mcp_servers.add_server(server)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"Added MCP server: {server.name} ({server.transport})")
    return {"server": request.name}


@app.put("/mcp/servers/{name}")
async def update_mcp_server(name: str, request: McpServerRequest, authorization: str = Header(None)):
    """Update an existing MCP server."""
    _verify_rest_auth(authorization)
    updates = request.model_dump(exclude_none=True)
    updates.pop("name", None)
    try:
        server = mcp_servers.update_server(name, updates)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not server:
        raise HTTPException(status_code=404, detail="MCP server not found")
    logger.info(f"Updated MCP server: {name}")
    return {"server": name}


@app.delete("/mcp/servers/{name}")
async def delete_mcp_server(name: str, authorization: str = Header(None)):
    """Remove an MCP server."""
    _verify_rest_auth(authorization)
    if mcp_servers.remove_server(name):
        logger.info(f"Removed MCP server: {name}")
        return {"deleted": name}
    raise HTTPException(status_code=404, detail="MCP server not found")


@app.post("/mcp/servers/{name}/toggle")
async def toggle_mcp_server(name: str, request: McpServerToggleRequest, authorization: str = Header(None)):
    """Enable or disable an MCP server globally."""
    _verify_rest_auth(authorization)
    if mcp_servers.toggle_server(name, request.enabled):
        logger.info(f"Toggled MCP server {name}: enabled={request.enabled}")
        return {"name": name, "enabled": request.enabled}
    raise HTTPException(status_code=404, detail="MCP server not found")


@app.get("/mcp/catalog")
async def list_mcp_catalog(authorization: str = Header(None)):
    """Return the catalog of pre-configured MCP server templates."""
    _verify_rest_auth(authorization)
    installed = set(mcp_servers.get_server_names())
    return {"catalog": get_catalog(installed)}


# ---------- Agent management endpoints ----------


class AgentRequest(BaseModel):
    name: str
    description: str
    prompt: str = ""
    model: typing.Optional[str] = None
    tools: typing.Optional[typing.List[str]] = None
    disallowed_tools: typing.Optional[typing.List[str]] = None
    permission_mode: typing.Optional[str] = None
    mcp_servers: typing.Optional[typing.List[str]] = None
    max_turns: typing.Optional[int] = None


@app.get("/agents")
async def list_agents(authorization: str = Header(None)):
    """List all available agents."""
    _verify_rest_auth(authorization)
    return {"agents": agents.list_agents()}


@app.get("/agents/{name}")
async def get_agent(name: str, authorization: str = Header(None)):
    """Get full agent details including prompt."""
    _verify_rest_auth(authorization)
    agent = agents.get_agent(name)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {
        "name": agent.name,
        "description": agent.description,
        "prompt": agent.prompt,
        "model": agent.model,
        "tools": agent.tools,
        "disallowed_tools": agent.disallowed_tools,
        "permission_mode": agent.permission_mode,
        "mcp_servers": agent.mcp_servers,
        "max_turns": agent.max_turns,
    }


@app.post("/agents")
async def create_agent(request: AgentRequest, authorization: str = Header(None)):
    """Create a new agent."""
    _verify_rest_auth(authorization)
    from .agent_manager import AgentInfo
    agent = AgentInfo(
        name=request.name,
        description=request.description,
        prompt=request.prompt,
        model=request.model,
        tools=request.tools,
        disallowed_tools=request.disallowed_tools,
        permission_mode=request.permission_mode,
        mcp_servers=request.mcp_servers,
        max_turns=request.max_turns,
    )
    try:
        agents.create_agent(agent)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info(f"Created agent: {agent.name}")
    return {"agent": agent.name}


@app.put("/agents/{name}")
async def update_agent(name: str, request: AgentRequest, authorization: str = Header(None)):
    """Update an existing agent."""
    _verify_rest_auth(authorization)
    from .agent_manager import AgentInfo
    agent = AgentInfo(
        name=request.name,
        description=request.description,
        prompt=request.prompt,
        model=request.model,
        tools=request.tools,
        disallowed_tools=request.disallowed_tools,
        permission_mode=request.permission_mode,
        mcp_servers=request.mcp_servers,
        max_turns=request.max_turns,
    )
    try:
        result = agents.update_agent(name, agent)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not result:
        raise HTTPException(status_code=404, detail="Agent not found")
    logger.info(f"Updated agent: {name}")
    return {"agent": agent.name}


@app.delete("/agents/{name}")
async def delete_agent(name: str, authorization: str = Header(None)):
    """Delete an agent."""
    _verify_rest_auth(authorization)
    if agents.delete_agent(name):
        logger.info(f"Deleted agent: {name}")
        return {"deleted": name}
    raise HTTPException(status_code=404, detail="Agent not found")


def _verify_rest_auth(authorization: str | None):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.removeprefix("Bearer ")
    if not verify_token(token):
        raise HTTPException(status_code=403, detail="Invalid token")


# ---------- WebSocket endpoint ----------


async def _safe_handle(websocket: WebSocket, coro):
    """Run a handler coroutine as a background task, logging any errors."""
    try:
        await coro
    except Exception as e:
        logger.exception(f"Background handler error: {e}")
        try:
            await _send(websocket, {"type": "error", "detail": str(e)})
        except Exception:
            pass  # Client may have disconnected


@app.websocket("/ws/chat")
async def ws_chat(websocket: WebSocket):
    await websocket.accept()
    authenticated = False
    ping_task: asyncio.Task | None = None

    async def _ping_loop():
        """Send periodic pings to keep the connection alive and detect dead clients."""
        try:
            while True:
                await asyncio.sleep(15)
                await _send(websocket, {"type": "ping"})
        except (WebSocketDisconnect, RuntimeError, asyncio.CancelledError):
            pass

    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            msg_type = msg.get("type")

            if msg_type == "auth":
                if verify_token(msg.get("token", "")):
                    authenticated = True
                    connected_clients.append(websocket)
                    await _send(websocket, {"type": "auth_ok"})
                    ping_task = asyncio.create_task(_ping_loop())
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

            # Client pong responses — just ignore them
            if msg_type == "pong":
                continue

            if msg_type == "message":
                # Dispatch as background task so the receive loop stays free
                # for other conversations' messages and cancel requests.
                asyncio.create_task(_safe_handle(websocket, _handle_message(websocket, msg)))
            elif msg_type == "new_conversation":
                await _handle_new_conversation(websocket, msg)
            elif msg_type == "update_permissions":
                await _handle_update_permissions(websocket, msg)
            elif msg_type == "update_mcp_servers":
                await _handle_update_mcp_servers(websocket, msg)
            elif msg_type == "cancel":
                await _handle_cancel(websocket, msg)
            else:
                await _send(websocket, {"type": "error", "detail": f"Unknown message type: {msg_type}"})

    except (WebSocketDisconnect, RuntimeError):
        logger.info("Client disconnected")
    except Exception as e:
        logger.exception(f"WebSocket error: {e}")
    finally:
        if ping_task:
            ping_task.cancel()
        if websocket in connected_clients:
            connected_clients.remove(websocket)


def _build_prompt(text: str, image_paths: list[str]) -> str:
    """Build the prompt text, prepending image references if present."""
    if not image_paths:
        return text

    image_lines = []
    for path in image_paths:
        image_lines.append(f"[The user attached an image. View it by reading this file: {path}]")
    image_block = "\n".join(image_lines)

    if text:
        return f"{image_block}\n\n{text}"
    return image_block.replace(
        "attached an image. View it by reading",
        "sent you an image. View and describe it by reading",
    )


async def _handle_message(websocket: WebSocket, msg: dict):
    text = msg.get("text", "")
    image_paths = msg.get("image_paths", [])
    conversation_id = msg.get("conversation_id", "")
    session_id = msg.get("session_id")

    if not text and not image_paths:
        await _send(websocket, {"type": "error", "detail": "Empty message"})
        return

    # Validate conversation_id format before using it in file paths
    from .session_manager import CONVERSATION_ID_PATTERN
    if conversation_id and not CONVERSATION_ID_PATTERN.match(conversation_id):
        await _send(websocket, {"type": "error", "detail": "Invalid conversation ID format"})
        return

    prompt = _build_prompt(text, image_paths)

    conv_lock = _get_conversation_lock(conversation_id)

    if conv_lock.locked():
        logger.info(f"Lock held for {conversation_id} — cancelling previous process")
        await _cancel_conversation_process(conversation_id)
        # Wait briefly for the lock to release
        try:
            await asyncio.wait_for(conv_lock.acquire(), timeout=5.0)
            conv_lock.release()
        except asyncio.TimeoutError:
            await _send(websocket, {"type": "busy", "detail": "Conversation is still finishing", "conversation_id": conversation_id})
            return

    # Look up session_id and working_dir from conversation if not provided
    is_first_turn = False
    conv_working_dir = None
    if not session_id and conversation_id:
        conv = sessions.get_conversation(conversation_id)
        if conv:
            session_id = conv.claude_session_id
            conv_working_dir = conv.working_dir
            is_first_turn = not session_id  # First turn if no stored session yet
        else:
            # Auto-create conversation if it doesn't exist
            sessions.create_conversation(conversation_id, text[:50])
            is_first_turn = True
    elif session_id:
        # Client provided a session_id — check if the conversation actually has one stored
        conv = sessions.get_conversation(conversation_id) if conversation_id else None
        if conv:
            conv_working_dir = conv.working_dir
            if not conv.claude_session_id:
                is_first_turn = True

    # Log user message to history (original text, not the expanded prompt)
    sessions.append_history(conversation_id, {
        "role": "user",
        "text": text or "[image]",
    })

    # Generate AI title immediately from first message (don't wait for response)
    if is_first_turn and conversation_id:
        asyncio.create_task(_generate_summary(conversation_id, text or "[image]"))

    # Use worktree path if this conversation is isolated, otherwise working_dir
    conv_obj = sessions.get_conversation(conversation_id)
    if conv_obj and conv_obj.git_worktree_path:
        cwd = conv_obj.git_worktree_path
    else:
        cwd = conv_working_dir or get_working_dir()

    async with conv_lock:
        await _run_claude(websocket, prompt, conversation_id, session_id, is_first_turn, cwd=cwd)


async def _handle_new_conversation(websocket: WebSocket, msg: dict):
    """Create a new conversation — tracked in session manager.

    If another conversation already has an active Claude process in the same
    working_dir (and it's a git repo), automatically creates a git worktree
    so both agents run in isolated directories.
    """
    name = msg.get("name", "New conversation")
    conversation_id = msg.get("conversation_id", f"conv_{int(time.time())}")
    working_dir = msg.get("working_dir")
    allowed_tools = msg.get("allowed_tools")
    mcp_server_names = msg.get("mcp_servers")
    model = msg.get("model")
    agent = msg.get("agent")

    try:
        conv = sessions.create_conversation(conversation_id, name, working_dir=working_dir, allowed_tools=allowed_tools, mcp_servers=mcp_server_names, model=model, agent=agent)
    except ValueError as e:
        await _send(websocket, {"type": "error", "detail": str(e)})
        return

    # Check if worktree isolation is needed
    if working_dir and is_git_repo(working_dir):
        active_in_project = [
            cid for cid, proc in active_processes.items()
            if proc.returncode is None and _working_dir_matches(cid, working_dir)
        ]
        if active_in_project:
            logger.info(f"Active conversations in {working_dir}: {active_in_project} — creating worktree")
            wt_path = create_worktree(working_dir, conversation_id)
            if wt_path:
                sessions.update_worktree(conversation_id, wt_path, working_dir)
                logger.info(f"Created conversation: {conv.id} ({conv.name}) [worktree: {wt_path}]")
            else:
                logger.warning(f"Worktree creation failed for {conversation_id} — running in shared directory")
                logger.info(f"Created conversation: {conv.id} ({conv.name})")
        else:
            logger.info(f"Created conversation: {conv.id} ({conv.name})")
    else:
        logger.info(f"Created conversation: {conv.id} ({conv.name})")

    await _send(websocket, {
        "type": "conversation_created",
        "conversation_id": conv.id,
        "name": conv.name,
    })


def _working_dir_matches(conversation_id: str, working_dir: str) -> bool:
    """Check if a conversation targets the given working directory."""
    conv = sessions.get_conversation(conversation_id)
    if not conv:
        return False
    return conv.working_dir == working_dir or conv.original_working_dir == working_dir


VALID_TOOL_NAMES = {"Read", "Write", "Edit", "Bash", "Glob", "Grep", "WebSearch", "WebFetch"}


def _validate_tool_spec(spec: str) -> bool:
    """Validate a tool spec like 'Bash' or 'Bash(git:*)'."""
    # Extract base tool name (everything before optional parenthesized pattern)
    base = spec.split("(", 1)[0]
    return base in VALID_TOOL_NAMES


async def _handle_update_permissions(websocket: WebSocket, msg: dict):
    """Update allowed tools for an existing conversation."""
    conversation_id = msg.get("conversation_id", "")
    allowed_tools = msg.get("allowed_tools", [])

    if not conversation_id:
        await _send(websocket, {"type": "error", "detail": "Missing conversation_id"})
        return

    invalid = [t for t in allowed_tools if not _validate_tool_spec(t)]
    if invalid:
        await _send(websocket, {"type": "error", "detail": f"Invalid tools: {invalid}"})
        return

    if sessions.update_allowed_tools(conversation_id, allowed_tools):
        await _send(websocket, {
            "type": "permissions_updated",
            "conversation_id": conversation_id,
            "allowed_tools": allowed_tools,
        })
    else:
        await _send(websocket, {"type": "error", "detail": "Conversation not found"})


async def _handle_update_mcp_servers(websocket: WebSocket, msg: dict):
    """Update MCP servers for an existing conversation."""
    conversation_id = msg.get("conversation_id", "")
    mcp_server_names = msg.get("mcp_servers", [])

    if not conversation_id:
        await _send(websocket, {"type": "error", "detail": "Missing conversation_id"})
        return

    # Validate that all requested servers actually exist
    known = set(mcp_servers.get_server_names())
    unknown = set(mcp_server_names) - known
    if unknown:
        await _send(websocket, {"type": "error", "detail": f"Unknown MCP servers: {unknown}"})
        return

    if sessions.update_mcp_servers(conversation_id, mcp_server_names):
        await _send(websocket, {
            "type": "mcp_servers_updated",
            "conversation_id": conversation_id,
            "mcp_servers": mcp_server_names,
        })
    else:
        await _send(websocket, {"type": "error", "detail": "Conversation not found"})


async def _cancel_conversation_process(conversation_id: str) -> bool:
    """Terminate the active claude subprocess for a specific conversation."""
    proc = active_processes.get(conversation_id)
    if proc and proc.returncode is None:
        logger.info(f"Terminating claude process for {conversation_id}")
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            proc.kill()
        return True
    return False


async def _cancel_all_processes():
    """Terminate all active claude subprocesses."""
    for cid in list(active_processes.keys()):
        await _cancel_conversation_process(cid)


async def _handle_cancel(websocket: WebSocket, msg: dict):
    conversation_id = msg.get("conversation_id")
    if conversation_id:
        if await _cancel_conversation_process(conversation_id):
            await _send(websocket, {"type": "cancelled", "conversation_id": conversation_id})
        else:
            await _send(websocket, {"type": "error", "detail": "No active process for this conversation", "conversation_id": conversation_id})
    else:
        # Backward compatibility: cancel without conversation_id cancels all
        cancelled_any = False
        for cid in list(active_processes.keys()):
            if await _cancel_conversation_process(cid):
                await _send(websocket, {"type": "cancelled", "conversation_id": cid})
                cancelled_any = True
        if not cancelled_any:
            await _send(websocket, {"type": "error", "detail": "No active process to cancel"})


async def _run_claude(websocket: WebSocket, text: str, conversation_id: str, session_id: str | None, is_first_turn: bool = False, cwd: str | None = None):
    """Spawn claude -p subprocess and stream events back via WebSocket."""

    conv = sessions.get_conversation(conversation_id)
    use_agent = conv and conv.agent

    # Conn-specific platform rules (appended regardless of agent)
    conn_system_prompt = (
        "The user is communicating with you remotely via Conn, "
        "an Android app that connects to this machine over the local network. "
        "They cannot see your full terminal output or interact with files directly. "
        "Keep responses concise and focused on actionable results.\n\n"
        "WEB APP PREVIEW — CRITICAL RULES:\n"
        "1. NEVER start long-running dev servers via the Bash tool. "
        "Running 'npm run dev', 'python -m http.server', 'flask run', 'npx vite', "
        "or ANY process that doesn't exit will hang your Bash tool forever and freeze the conversation.\n"
        "2. You CAN use Bash for short-lived build commands: npm install, npm run build, pip install, etc.\n"
        "3. When you finish building or modifying a web app, tell the user: "
        "\"The app is ready! Tap the menu (three dots) in the top right and select 'Start Preview' to view it in your browser.\"\n"
        "4. The Conn server will auto-detect the project type (Vite, npm, Django, Flask, static HTML) "
        "and start the right dev server on a free port. You do not need to configure anything.\n"
        "5. If the user asks you to 'run it', 'start the server', 'show me the app', or 'deploy it', "
        "remind them to use the Start Preview button instead of trying to run a server yourself.\n\n"
        "QUESTIONS — CRITICAL RULE:\n"
        "NEVER use the AskUserQuestion tool — it is not supported in this environment and will fail silently. "
        "Instead, when you need to ask the user a question or present choices, write them directly in your "
        "response text as numbered options. For example:\n"
        "\"Which approach do you prefer?\n"
        "1. Option A — description\n"
        "2. Option B — description\n"
        "3. Option C — description\"\n"
        "The user will reply with their choice number or a custom answer.\n\n"
        "DOCUMENTATION — IMPORTANT:\n"
        "After making any code changes, investigate whether related documentation "
        "(README files, docs/ folder, inline doc comments, CLAUDE.md, etc.) needs to be "
        "updated to stay consistent with the changes you made. If you find stale or "
        "missing documentation, update it as part of the same task."
    )

    # Append per-project custom instructions if configured
    project_dir = cwd or get_working_dir()
    custom = get_custom_instructions(project_dir)
    if custom:
        conn_system_prompt += (
            "\n\nPROJECT CUSTOM INSTRUCTIONS (set by the user for this project):\n"
            + custom
        )

    if use_agent:
        # Agent mode: let the agent definition handle tools, model, permissions.
        # Only append Conn platform rules.
        cmd = [
            "claude", "-p", text,
            "--output-format", "stream-json",
            "--max-turns", "200",
            "--verbose",
            "--agent", conv.agent,
            "--append-system-prompt", conn_system_prompt,
        ]
    else:
        # Manual mode: use per-conversation allowed tools
        tools = ",".join(conv.allowed_tools) if conv and conv.allowed_tools else "Read,Write,Edit,Bash,Glob,Grep,WebSearch,WebFetch"
        cmd = [
            "claude", "-p", text,
            "--output-format", "stream-json",
            "--tools", tools,
            "--allowedTools", tools,
            "--max-turns", "200",
            "--verbose",
            "--append-system-prompt", conn_system_prompt,
        ]

    # Add --model flag if conversation specifies a model (manual mode only;
    # agent mode gets model from the agent definition)
    if not use_agent and conv and conv.model:
        cmd.extend(["--model", conv.model])

    # Generate --mcp-config file if conversation has MCP servers enabled
    # (manual mode only; agent mode gets MCP from the agent definition)
    mcp_config_path = None
    if not use_agent and conv and conv.mcp_servers:
        mcp_config_path = mcp_servers.write_mcp_config_file(conv.mcp_servers)
        if mcp_config_path:
            cmd.extend(["--mcp-config", mcp_config_path])
            tools = cmd[cmd.index("--allowedTools") + 1] if "--allowedTools" in cmd else ""
            mcp_tool_patterns = [f"mcp__{name}__*" for name in conv.mcp_servers
                                 if mcp_servers.get_server(name) and mcp_servers.get_server(name).enabled]
            if mcp_tool_patterns and "--allowedTools" in cmd:
                tools_with_mcp = tools + "," + ",".join(mcp_tool_patterns)
                idx = cmd.index("--allowedTools")
                cmd[idx + 1] = tools_with_mcp
            logger.info(f"MCP config: {conv.mcp_servers} → {mcp_config_path}")

    if session_id:
        cmd.extend(["--resume", session_id])

    logger.info(f"Running: {' '.join(cmd[:6])}...")

    accumulated_text = ""
    in_tool_use = False  # Track when we're inside a tool use block
    result_is_error = False
    saw_streaming_deltas = False  # Track if we got content_block_delta events
    forwarder = EventForwarder(cwd=cwd or get_working_dir())

    # Clear CLAUDECODE env var so claude doesn't think it's nested
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    try:
        # Use a large stdout buffer limit (32MB) because Claude's stream-json
        # can emit very large single lines (e.g. base64-encoded image data from
        # Read tool results). The default asyncio limit is 64KB, which causes
        # "Separator is not found, and chunk exceed the limit" errors.
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=32 * 1024 * 1024,  # 32MB readline limit
            env=env,
            cwd=cwd or get_working_dir(),
        )
        active_processes[conversation_id] = process

        new_session_id = session_id

        async for raw_line in process.stdout:
            line = raw_line.decode().strip()
            if not line:
                continue

            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Debug: log all event types to understand stream-json format
            evt_type = event.get("type", "unknown")
            if evt_type != "content_block_delta":  # Skip noisy deltas
                extra = ""
                if evt_type == "content_block_start":
                    extra = f" block_type={event.get('content_block', {}).get('type')}"
                elif evt_type == "assistant":
                    blocks = [b.get("type") for b in event.get("message", {}).get("content", [])]
                    extra = f" blocks={blocks}"
                logger.info(f"stream-json event: {evt_type}{extra}")

            # Forward events to the latest connected client (survives reconnects)
            await forwarder.forward_to_client(event, conversation_id)

            # Accumulate ALL text into a single string for history — one response = one entry.
            # IMPORTANT: Only use ONE source of text — content_block_delta (streaming) OR
            # assistant (summary). Using both causes double-counting since the assistant
            # event repeats the same text that was already streamed via deltas.
            if event.get("type") == "content_block_delta":
                delta = event.get("delta", {})
                if delta.get("type") == "text_delta":
                    saw_streaming_deltas = True
                    # Add separator when text resumes after a tool use
                    if in_tool_use and accumulated_text:
                        accumulated_text += "\n\n"
                    in_tool_use = False
                    accumulated_text += delta.get("text", "")
            elif event.get("type") == "content_block_start":
                block = event.get("content_block", {})
                if block.get("type") == "tool_use":
                    in_tool_use = True
            elif event.get("type") == "assistant" and "message" in event:
                # Fallback: only use assistant events if we never got streaming deltas
                if not saw_streaming_deltas:
                    for block in event["message"].get("content", []):
                        if block.get("type") == "text":
                            accumulated_text += block["text"]

            # Capture session ID from result events
            if event.get("type") == "result":
                result_is_error = event.get("is_error", False)
                if result_is_error:
                    errors = event.get("errors", [])
                    logger.warning(f"claude result error: {errors}")
                    # Don't store session IDs from failed results — they may be
                    # invalid and would poison future --resume attempts.
                else:
                    new_session_id = event.get("session_id", new_session_id)
                # Fall back: if no assistant events produced text, use result text
                if not accumulated_text and event.get("result"):
                    accumulated_text = event["result"]
                    await _send_to_client({
                        "type": "text_delta",
                        "text": accumulated_text,
                        "conversation_id": conversation_id,
                    })

        await process.wait()

        # Log stderr for debugging
        if process.stderr:
            stderr_data = await process.stderr.read()
            if stderr_data:
                logger.warning(f"claude stderr: {stderr_data.decode().strip()}")

        logger.info(f"claude process exited with code {process.returncode}")

        # If --resume failed with an invalid session, clear it and retry without resume
        if result_is_error and session_id and not accumulated_text:
            logger.info(f"Resume failed for {conversation_id} — clearing session and retrying")
            sessions.update_session_id(conversation_id, None)
            await _send_to_client({"type": "error", "detail": "Session expired, retrying..."})
            # Retry without --resume (recursive call with session_id=None)
            await _run_claude(websocket, text, conversation_id, session_id=None, is_first_turn=True, cwd=cwd)
            return

        # Update session tracking
        if new_session_id and conversation_id:
            sessions.update_session_id(conversation_id, new_session_id)

        # Save the complete assistant response as a single history entry
        history_entry = {"role": "assistant", "text": accumulated_text}
        if forwarder.image_paths:
            history_entry["image_paths"] = forwarder.image_paths
        if accumulated_text.strip() or forwarder.image_paths:
            sessions.append_history(conversation_id, history_entry)

        complete_msg = {
            "type": "message_complete",
            "conversation_id": conversation_id,
            "session_id": new_session_id,
        }
        # Include current git branch so the client can update mid-session
        conv_info = sessions.get_conversation(conversation_id)
        if conv_info and conv_info.git_worktree_path:
            complete_msg["git_branch"] = f"conn/{conversation_id}"
        else:
            effective_cwd = cwd or get_working_dir()
            branch = get_current_branch(effective_cwd)
            if branch:
                complete_msg["git_branch"] = branch
        await _send_to_client(complete_msg)

        # Summary generation is now triggered earlier in _handle_message
        # before _run_claude is called, so the title arrives while streaming.

    except Exception as e:
        logger.exception(f"claude subprocess error: {e}")
        await _send_to_client({"type": "error", "detail": str(e)})
    finally:
        active_processes.pop(conversation_id, None)
        # Clean up temp MCP config file
        if mcp_config_path:
            try:
                os.unlink(mcp_config_path)
            except OSError:
                pass
        # Clean up lock if no longer held
        lock = conversation_locks.get(conversation_id)
        if lock and not lock.locked():
            conversation_locks.pop(conversation_id, None)


async def _generate_summary(conversation_id: str, user_text: str):
    """Generate a short AI title from the user's first message."""
    try:
        if not user_text or user_text == "[image]":
            return

        prompt = (
            "Generate a very short title (under 50 characters) for this conversation. "
            "Be specific and concise, like a commit message or task title. "
            "Examples: 'Fix WebSocket buffer overflow', 'Add dark mode toggle', 'Debug login crash'. "
            "Just output the title, nothing else.\n\n"
            f"User: {user_text[:500]}"
        )

        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p", prompt,
            "--output-format", "text",
            "--max-turns", "0",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd="/tmp",
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        summary = stdout.decode().strip()

        logger.info(f"Summary raw output for {conversation_id}: {summary!r}")

        if summary and len(summary) < 80 and not summary.lower().startswith("error"):
            sessions.rename_conversation(conversation_id, summary)
            logger.info(f"Renamed conversation {conversation_id}: {summary}")

            await _send_to_client({
                "type": "conversation_renamed",
                "conversation_id": conversation_id,
                "name": summary,
            })
        elif summary:
            logger.warning(f"Summary rejected for {conversation_id}: {summary!r}")
    except asyncio.TimeoutError:
        logger.warning(f"Summary generation timed out for {conversation_id}")
    except Exception as e:
        logger.warning(f"Summary generation failed for {conversation_id}: {e}")


class EventForwarder:
    """Stateful mapper from claude stream-json events to our WebSocket protocol.

    The Claude CLI stream-json format emits:
    - content_block_start / content_block_delta / content_block_stop for streaming
    - assistant events with complete content blocks (text + tool_use) after each turn

    We use content_block_start/stop for real-time tool notifications and
    content_block_delta for streaming text. The assistant event is used as a
    fallback for tool info only when content_block events didn't fire.
    """

    # Tool names that produce screenshot image files
    SCREENSHOT_TOOLS = {
        "mcp__playwright__browser_take_screenshot",
    }

    def __init__(self, cwd: str | None = None):
        self._saw_streaming_events = False  # Track if we got content_block events
        self._active_tool_name: str | None = None
        self._tool_input_json: str = ""  # Accumulated input_json_delta fragments
        self._tool_start_sent: bool = False  # Whether we sent the initial tool_start
        self.image_paths: list[str] = []  # Image file paths emitted during this response
        self._cwd = cwd  # Working directory of the Claude subprocess

    async def forward(self, websocket: WebSocket, event: dict, conversation_id: str) -> dict | None:
        """Forward event to a specific WebSocket (used by send-image and tests)."""
        async def sender(data: dict):
            await _send(websocket, data)
        return await self._forward_impl(sender, event, conversation_id)

    async def forward_to_client(self, event: dict, conversation_id: str) -> dict | None:
        """Forward event to the latest connected client (survives reconnects)."""
        return await self._forward_impl(_send_to_client, event, conversation_id)

    async def _forward_impl(self, sender, event: dict, conversation_id: str) -> dict | None:
        event_type = event.get("type")

        if event_type == "content_block_start":
            self._saw_streaming_events = True
            block = event.get("content_block", {})
            if block.get("type") == "tool_use":
                self._active_tool_name = block.get("name", "")
                self._tool_input_json = ""
                self._tool_start_sent = False
                tool_input = block.get("input", {})
                summary = _summarize_tool_input(self._active_tool_name, tool_input)
                if summary:
                    # Input was available immediately — send tool_start now
                    self._tool_start_sent = True
                    out = {
                        "type": "tool_start",
                        "tool": self._active_tool_name,
                        "input_summary": summary,
                        "conversation_id": conversation_id,
                    }
                    await sender(out)
                    return out
                # Otherwise wait for input_json_delta to build the summary
            return None

        elif event_type == "content_block_stop":
            if self._active_tool_name is not None:
                # If we haven't sent tool_start yet, send it now with accumulated input
                if not self._tool_start_sent:
                    summary = ""
                    if self._tool_input_json:
                        try:
                            input_data = json.loads(self._tool_input_json)
                            summary = _summarize_tool_input(self._active_tool_name, input_data)
                        except json.JSONDecodeError:
                            summary = self._tool_input_json[:80]
                    start_out = {
                        "type": "tool_start",
                        "tool": self._active_tool_name,
                        "input_summary": summary,
                        "conversation_id": conversation_id,
                    }
                    await sender(start_out)

                # Detect screenshot tools and emit image event
                if self._active_tool_name in self.SCREENSHOT_TOOLS:
                    image_path = _extract_screenshot_path(self._tool_input_json)
                    if image_path:
                        # Resolve relative paths against the Claude subprocess cwd
                        resolved = Path(image_path)
                        if not resolved.is_absolute() and self._cwd:
                            resolved = Path(self._cwd) / image_path
                        abs_path = str(resolved.resolve())
                        self.image_paths.append(abs_path)
                        await sender({
                            "type": "image",
                            "path": abs_path,
                            "conversation_id": conversation_id,
                        })

                self._active_tool_name = None
                self._tool_input_json = ""
                self._tool_start_sent = False
                out = {"type": "tool_done", "conversation_id": conversation_id}
                await sender(out)
                return out
            return None

        elif event_type == "content_block_delta":
            self._saw_streaming_events = True
            delta = event.get("delta", {})
            if delta.get("type") == "text_delta":
                out = {
                    "type": "text_delta",
                    "text": delta.get("text", ""),
                    "conversation_id": conversation_id,
                }
                await sender(out)
                return out
            elif delta.get("type") == "input_json_delta" and self._active_tool_name:
                # Accumulate tool input fragments
                self._tool_input_json += delta.get("partial_json", "")
                # Once we have enough to parse, send tool_start with summary
                if not self._tool_start_sent and len(self._tool_input_json) > 5:
                    try:
                        input_data = json.loads(self._tool_input_json)
                        summary = _summarize_tool_input(self._active_tool_name, input_data)
                        if summary:
                            self._tool_start_sent = True
                            out = {
                                "type": "tool_start",
                                "tool": self._active_tool_name,
                                "input_summary": summary,
                                "conversation_id": conversation_id,
                            }
                            await sender(out)
                            return out
                    except json.JSONDecodeError:
                        pass  # Not valid JSON yet — keep accumulating
            return None

        elif event_type == "assistant" and "message" in event:
            # Fallback: only use assistant events if we didn't get streaming events
            if self._saw_streaming_events:
                return None
            last_out = None
            message = event["message"]
            for block in message.get("content", []):
                if block.get("type") == "text":
                    out = {
                        "type": "text_delta",
                        "text": block["text"],
                        "conversation_id": conversation_id,
                    }
                    await sender(out)
                    last_out = out
                elif block.get("type") == "tool_use":
                    tool_name = block.get("name", "")
                    tool_input = block.get("input", {})
                    start_out = {
                        "type": "tool_start",
                        "tool": tool_name,
                        "input_summary": _summarize_tool_input(tool_name, tool_input),
                        "conversation_id": conversation_id,
                    }
                    await sender(start_out)

                    # Detect screenshot tools in fallback path
                    if tool_name in self.SCREENSHOT_TOOLS:
                        filename = tool_input.get("filename")
                        if filename and isinstance(filename, str):
                            resolved = Path(filename)
                            if not resolved.is_absolute() and self._cwd:
                                resolved = Path(self._cwd) / filename
                            abs_path = str(resolved.resolve())
                            self.image_paths.append(abs_path)
                            await sender({
                                "type": "image",
                                "path": abs_path,
                                "conversation_id": conversation_id,
                            })

                    done_out = {"type": "tool_done", "conversation_id": conversation_id}
                    await sender(done_out)
                    last_out = start_out
            return last_out

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
    elif tool_name == "Task":
        return input_data.get("description") or input_data.get("prompt", "")[:80]
    elif tool_name == "TodoWrite":
        todos = input_data.get("todos", [])
        in_progress = [t.get("content", "") for t in todos if t.get("status") == "in_progress"]
        if in_progress:
            return in_progress[0]
        return f"{len(todos)} items"
    elif tool_name == "WebSearch":
        return input_data.get("query", "")
    elif tool_name == "WebFetch":
        return input_data.get("url", "")
    elif tool_name == "NotebookEdit":
        return input_data.get("notebook_path", "")

    # Fallback: pick the first string value instead of dumping raw dict
    for val in input_data.values():
        if isinstance(val, str) and val:
            return val[:80] + ("..." if len(val) > 80 else "")
    return ""


def _extract_screenshot_path(tool_input_json: str) -> str | None:
    """Extract the screenshot file path from a Playwright screenshot tool's input.

    The browser_take_screenshot tool accepts a 'filename' parameter.
    If not provided, Playwright MCP uses a default like 'page-{timestamp}.png'.
    Returns the filename if found, or None.
    """
    if not tool_input_json:
        return None
    try:
        input_data = json.loads(tool_input_json)
    except json.JSONDecodeError:
        return None
    filename = input_data.get("filename")
    if filename and isinstance(filename, str):
        return filename
    return None


MAX_WS_MESSAGE_SIZE = 1 * 1024 * 1024  # 1MB — safety cap for WebSocket messages


async def _send(websocket: WebSocket, data: dict):
    """Send JSON to WebSocket if still connected."""
    if websocket.client_state != WebSocketState.CONNECTED:
        return
    try:
        payload = json.dumps(data)
        if len(payload) > MAX_WS_MESSAGE_SIZE:
            logger.warning(f"Dropping oversized WebSocket message ({len(payload)} bytes, type={data.get('type')})")
            return
        await websocket.send_text(payload)
    except (WebSocketDisconnect, RuntimeError):
        pass


async def _send_to_client(data: dict):
    """Send JSON to the latest connected client (survives reconnects)."""
    if not connected_clients:
        return
    ws = connected_clients[-1]
    await _send(ws, data)


# Mount dashboard static files (after all routes to avoid path conflicts)
app.mount("/dashboard", StaticFiles(directory=str(DASHBOARD_DIR), html=True), name="dashboard")


if __name__ == "__main__":
    import uvicorn
    from .tls import ensure_certs
    cert_path, key_path = ensure_certs()
    uvicorn.run(
        app,
        host=get_host(),
        port=get_port(),
        ssl_keyfile=str(key_path),
        ssl_certfile=str(cert_path),
    )
