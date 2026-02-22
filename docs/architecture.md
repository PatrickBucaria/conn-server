# Server Architecture

## Overview

Conn server is a Python FastAPI application that bridges WebSocket clients to [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (the Claude CLI). It manages multiple concurrent conversations, each running a separate Claude subprocess.

## Core Design

- **Claude Integration**: Runs `claude -p "{text}" --output-format stream-json` as subprocess
- **Session Resume**: Uses `--resume {session_id}` to continue conversations. If resume fails (stale/invalid session), automatically clears the session and retries fresh
- **Per-Conversation Working Dir**: Each conversation stores an optional `working_dir` (set via project picker). The Claude subprocess runs in that directory, giving it the right project context (CLAUDE.md, git repo, etc.)
- **Multi-Agent Concurrency**: Per-conversation asyncio locks allow multiple Claude processes to run simultaneously across different conversations. Each conversation has its own lock and process entry in `active_processes` dict. Sending a new message in a conversation only cancels that conversation's previous process, not others. The WebSocket handler dispatches message handlers as background tasks via `asyncio.create_task()` so the receive loop stays free for other conversations' messages and cancel requests
- **WebSocket Keep-Alive**: Server sends `{"type": "ping"}` every 15 seconds after authentication. Client responds with `{"type": "pong"}`
- **Allowed Tools**: Read, Write, Edit, Bash, Glob, Grep (max 50 turns)
- **History Storage**: JSONL files per conversation, saved immediately for user messages, segmented by tool use for assistant messages

## Image Handling

- **User → Claude**: Images uploaded via `/upload` are saved to `~/.conn/uploads/{conversation_id}/`, prepended to prompt as `[The user attached an image. View it by reading this file: {path}]`
- **Claude → User**: When Claude uses MCP screenshot tools, the `EventForwarder` detects the tool name and extracts the `filename` from the tool input. It emits an `{"type": "image", "path": "..."}` WebSocket event. The `/files` endpoint serves the image with auth via header or `?token=` query param

## Buffering and Limits

- **Stdout Buffer**: Uses 32MB readline limit for `asyncio.create_subprocess_exec` because Claude's stream-json can emit multi-MB lines for image Read tool results
- **WebSocket Message Cap**: Messages over 1MB are silently dropped to prevent oversized frame errors on clients

## Conversation Summaries

After the first Claude response in a new conversation, an async background task spawns `claude -p` (outside the conversation lock) to generate a short AI title (~50 chars). Updates the name via `sessions.rename_conversation()` and sends a `conversation_renamed` WebSocket event. Best-effort — falls back to raw first-message name on failure.

## Web Preview

`PreviewManager` runs dev servers as detached background processes on ports 8100-8199. Auto-detects project type (Vite/npm, Django, Flask, static HTML). Cleaned up on conversation delete, server restart, or server shutdown.

## Self-Hosted App Updates

The server hosts client app releases at `~/.conn/releases/`. This is optional — the endpoints return 404 if no releases exist.

**Directory format:**
```
~/.conn/releases/
├── version.json          # Latest build metadata {versionCode, versionName, buildDate, notes}
├── latest.apk            # Latest APK
├── releases.json         # Manifest of last 10 builds [{versionCode, versionName, ...}]
├── conn-v0.0.1.apk       # Versioned APKs
├── conn-v0.0.2.apk
└── build_number          # Auto-incrementing counter
```

Any tool that writes APK files in this format can publish updates. The server just reads and serves what's there.

## Configuration

Server reads from `~/.conn/config.json` (run `./setup.sh` for interactive setup):

- Auto-generates auth token on first run
- Default port: 8443 (HTTPS)
- Working directory: `~/Projects` (configurable via `working_dir` in config)
- Environment variable overrides: `CONN_WORKING_DIR`, `CONN_PORT`, `CONN_HOST` (take precedence over config file)
- Conversation history: `~/.conn/history/{conversation_id}.jsonl`
- Session tracking: `~/.conn/sessions.json`
- Image uploads: `~/.conn/uploads/{conversation_id}/`
- App releases: `~/.conn/releases/`
- launchd service: `~/Library/LaunchAgents/com.conn.server.plist` (macOS, installed by `setup.sh`)
- systemd service: `/etc/systemd/system/conn.service` (Linux, installed by `setup.sh`)

## Known Pitfalls

1. **Claude CLI required**: The `claude` command must be available on PATH for the server to work
2. **Concurrent messages**: Server uses per-conversation locks. If a conversation's lock doesn't release within 5s, a `busy` event is returned with the `conversation_id`
3. **WebSocket auth**: First message must be `{"type": "auth", "token": "..."}` or connection is rejected
4. **Image Read tool results**: Claude's stream-json output can include very large lines (multi-MB). The subprocess stdout buffer must be large enough (32MB)
5. **Client disconnect during streaming**: Server continues capturing the full response even after the WebSocket disconnects, saving it to history so it's available on reconnect
6. **Stale session IDs**: If the Claude CLI's internal session storage is cleared (e.g. CLI update), stored `--resume` session IDs become invalid. The server detects the error, clears the stored session, and auto-retries
7. **Never run dev servers via Bash**: Long-lived server processes hang the conversation lock. Use `PreviewManager` instead
8. **Python venv has hardcoded paths**: If the project directory is moved, the venv breaks. Fix: recreate it
9. **launchd/systemd service**: Re-run `./setup.sh` to regenerate with new settings. Logs at `~/.conn/logs/` (macOS) or `journalctl -u conn` (Linux)
