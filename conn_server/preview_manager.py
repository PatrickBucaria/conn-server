"""Manage background dev server processes for web app previews."""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Port range for preview servers
PREVIEW_PORT_MIN = 8100
PREVIEW_PORT_MAX = 8199


@dataclass
class PreviewInfo:
    port: int
    pid: int
    conversation_id: str
    working_dir: str
    command: str


class PreviewManager:
    def __init__(self):
        self._previews: dict[str, PreviewInfo] = {}
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    @staticmethod
    def can_preview(working_dir: str) -> bool:
        """Check if a directory contains a previewable web project."""
        wd = Path(working_dir)

        # Node.js with dev or start script
        package_json = wd / "package.json"
        if package_json.exists():
            import json
            try:
                pkg = json.loads(package_json.read_text())
                scripts = pkg.get("scripts", {})
                if "dev" in scripts or "start" in scripts:
                    return True
            except (json.JSONDecodeError, KeyError):
                pass

        # Django
        if (wd / "manage.py").exists():
            return True

        # Flask
        if (wd / "app.py").exists():
            return True

        # Static HTML
        if (wd / "dist" / "index.html").exists() or (wd / "index.html").exists():
            return True

        return False

    def _find_free_port(self) -> int:
        """Find an available port in the preview range."""
        for port in range(PREVIEW_PORT_MIN, PREVIEW_PORT_MAX + 1):
            if any(p.port == port for p in self._previews.values()):
                continue
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(("", port))
                    return port
            except OSError:
                continue
        raise RuntimeError("No free ports available in preview range")

    def _detect_command(self, working_dir: str, port: int) -> list[str]:
        """Auto-detect the right dev server command for the project."""
        wd = Path(working_dir)

        # Node.js projects
        package_json = wd / "package.json"
        if package_json.exists():
            import json
            try:
                pkg = json.loads(package_json.read_text())
                scripts = pkg.get("scripts", {})
                if "dev" in scripts:
                    return ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", str(port)]
                if "start" in scripts:
                    return ["npm", "start"]
            except (json.JSONDecodeError, KeyError):
                pass

        python = sys.executable

        # Python projects
        manage_py = wd / "manage.py"
        if manage_py.exists():
            return [python, "manage.py", "runserver", f"0.0.0.0:{port}"]

        # Flask
        app_py = wd / "app.py"
        if app_py.exists():
            return [python, "-m", "flask", "run", "--host", "0.0.0.0", "--port", str(port)]

        # Static files
        index_html = wd / "index.html"
        dist_index = wd / "dist" / "index.html"
        if dist_index.exists():
            return [python, "-m", "http.server", str(port), "--directory", str(wd / "dist"), "--bind", "0.0.0.0"]
        if index_html.exists():
            return [python, "-m", "http.server", str(port), "--directory", str(wd), "--bind", "0.0.0.0"]

        raise RuntimeError(f"Could not detect project type in {working_dir}")

    async def _wait_for_port(self, port: int, timeout: float = 15.0) -> bool:
        """Poll until the port is accepting connections."""
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", port),
                    timeout=1.0,
                )
                writer.close()
                await writer.wait_closed()
                return True
            except (OSError, asyncio.TimeoutError):
                await asyncio.sleep(0.5)
        return False

    async def start(
        self,
        conversation_id: str,
        working_dir: str,
        command: list[str] | None = None,
    ) -> PreviewInfo:
        """Start a dev server for a conversation.

        Args:
            conversation_id: The conversation requesting the preview.
            working_dir: The project directory to serve.
            command: Optional explicit command. Auto-detected if not given.
        """
        # Stop any existing preview for this conversation
        await self.stop(conversation_id)

        port = self._find_free_port()

        if command is None:
            cmd = self._detect_command(working_dir, port)
        else:
            cmd = command

        cmd_str = " ".join(cmd)
        logger.info(f"Starting preview for {conversation_id}: {cmd_str} (port {port})")

        # Start in its own process group so it survives Claude process termination
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=working_dir,
            start_new_session=True,
        )

        info = PreviewInfo(
            port=port,
            pid=process.pid,
            conversation_id=conversation_id,
            working_dir=working_dir,
            command=cmd_str,
        )
        self._previews[conversation_id] = info
        self._processes[conversation_id] = process

        # Wait for the server to become ready
        ready = await self._wait_for_port(port)
        if not ready:
            # Check if the process died
            if process.returncode is not None:
                self._previews.pop(conversation_id, None)
                self._processes.pop(conversation_id, None)
                raise RuntimeError(f"Preview server exited immediately (code {process.returncode})")
            logger.warning(f"Preview on port {port} not yet responding, but process is running")

        logger.info(f"Preview ready on port {port} (pid {process.pid})")
        return info

    async def stop(self, conversation_id: str) -> bool:
        """Stop the preview server for a conversation."""
        process = self._processes.pop(conversation_id, None)
        info = self._previews.pop(conversation_id, None)

        if process is None:
            return False

        if process.returncode is None:
            logger.info(f"Stopping preview for {conversation_id} (pid {process.pid})")
            try:
                # Kill the entire process group
                os.killpg(os.getpgid(process.pid), 9)
            except (ProcessLookupError, PermissionError):
                # Process already gone
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                process.kill()

        return True

    async def stop_all(self):
        """Stop all preview servers."""
        for cid in list(self._previews.keys()):
            await self.stop(cid)

    def get_preview(self, conversation_id: str) -> PreviewInfo | None:
        """Get active preview info for a conversation."""
        info = self._previews.get(conversation_id)
        if info is None:
            return None
        # Check if process is still alive
        process = self._processes.get(conversation_id)
        if process and process.returncode is not None:
            # Process died — clean up
            self._previews.pop(conversation_id, None)
            self._processes.pop(conversation_id, None)
            return None
        return info

    def list_previews(self) -> list[dict]:
        """List all active previews."""
        result = []
        for cid in list(self._previews.keys()):
            info = self.get_preview(cid)
            if info:
                result.append({
                    "conversation_id": info.conversation_id,
                    "port": info.port,
                    "pid": info.pid,
                    "working_dir": info.working_dir,
                    "command": info.command,
                })
        return result
