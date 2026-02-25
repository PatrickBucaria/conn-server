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
    working_dir: str
    command: str
    conversation_id: str | None = None


class PreviewManager:
    def __init__(self):
        # Primary key: working_dir (one preview per project directory)
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

    def _find_free_port(self, working_dir: str | None = None) -> int:
        """Find an available port in the preview range.

        If working_dir is given, derives a stable preferred port from the path
        hash so the same project always lands on the same port (avoids browser
        cache crossover when different projects reuse ports).
        """
        port_range = PREVIEW_PORT_MAX - PREVIEW_PORT_MIN + 1
        used = {p.port for p in self._previews.values()}

        if working_dir:
            preferred = PREVIEW_PORT_MIN + (hash(working_dir) % port_range)
            # Try the preferred port first, then scan from there
            for offset in range(port_range):
                port = PREVIEW_PORT_MIN + (preferred - PREVIEW_PORT_MIN + offset) % port_range
                if port in used:
                    continue
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                        s.bind(("", port))
                        return port
                except OSError:
                    continue
        else:
            for port in range(PREVIEW_PORT_MIN, PREVIEW_PORT_MAX + 1):
                if port in used:
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
        working_dir: str,
        conversation_id: str | None = None,
        command: list[str] | None = None,
    ) -> PreviewInfo:
        """Start a dev server for a project directory.

        If a preview is already running for this working_dir, returns it.

        Args:
            working_dir: The project directory to serve.
            conversation_id: Optional conversation that triggered the preview.
            command: Optional explicit command. Auto-detected if not given.
        """
        # If already running for this directory, return existing
        existing = self.get_preview(working_dir)
        if existing:
            return existing

        port = self._find_free_port(working_dir)

        if command is None:
            cmd = self._detect_command(working_dir, port)
        else:
            cmd = command

        cmd_str = " ".join(cmd)
        logger.info(f"Starting preview for {working_dir}: {cmd_str} (port {port})")

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
            working_dir=working_dir,
            command=cmd_str,
            conversation_id=conversation_id,
        )
        self._previews[working_dir] = info
        self._processes[working_dir] = process

        # Wait for the server to become ready
        ready = await self._wait_for_port(port)
        if not ready:
            # Check if the process died
            if process.returncode is not None:
                self._previews.pop(working_dir, None)
                self._processes.pop(working_dir, None)
                raise RuntimeError(f"Preview server exited immediately (code {process.returncode})")
            logger.warning(f"Preview on port {port} not yet responding, but process is running")

        logger.info(f"Preview ready on port {port} (pid {process.pid})")
        return info

    async def stop(self, working_dir: str) -> bool:
        """Stop the preview server for a project directory."""
        process = self._processes.pop(working_dir, None)
        self._previews.pop(working_dir, None)

        if process is None:
            return False

        if process.returncode is None:
            logger.info(f"Stopping preview for {working_dir} (pid {process.pid})")
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

    async def stop_for_conversation(self, conversation_id: str) -> str | None:
        """Stop the preview associated with a conversation. Returns working_dir if stopped."""
        for wd, info in list(self._previews.items()):
            if info.conversation_id == conversation_id:
                await self.stop(wd)
                return wd
        return None

    async def stop_all(self):
        """Stop all preview servers."""
        for wd in list(self._previews.keys()):
            await self.stop(wd)

    def get_preview(self, working_dir: str) -> PreviewInfo | None:
        """Get active preview info for a project directory."""
        info = self._previews.get(working_dir)
        if info is None:
            return None
        # Check if process is still alive
        process = self._processes.get(working_dir)
        if process and process.returncode is not None:
            # Process died — clean up
            self._previews.pop(working_dir, None)
            self._processes.pop(working_dir, None)
            return None
        return info

    def get_preview_for_conversation(self, conversation_id: str) -> PreviewInfo | None:
        """Get active preview for the directory associated with a conversation."""
        for wd, info in self._previews.items():
            if info.conversation_id == conversation_id:
                return self.get_preview(wd)
        return None

    def list_previews(self) -> list[dict]:
        """List all active previews."""
        result = []
        for wd in list(self._previews.keys()):
            info = self.get_preview(wd)
            if info:
                result.append({
                    "port": info.port,
                    "pid": info.pid,
                    "working_dir": info.working_dir,
                    "command": info.command,
                    "conversation_id": info.conversation_id,
                })
        return result
