# Conn Server

*"Mr. Spock, you have the Conn!"* 🖖

A self-hosted server that lets you interact with [Claude Code](https://docs.anthropic.com/en/docs/claude-code) from any device over your network. Run Claude on a home server, connect from your phone or any WebSocket client.

## Features

- **WebSocket streaming** — real-time text deltas, tool use events, and image sharing
- **Multiple concurrent conversations** — each with its own Claude process and working directory
- **Session resume** — conversations persist across reconnects
- **Project context** — point each conversation at a different project directory
- **File browser** — browse and download files from project directories
- **MCP integration** — configure Model Context Protocol servers from a built-in catalog
- **Custom agents** — define reusable agent profiles with custom prompts, models, and tool sets
- **Web preview** — auto-detect and serve dev servers for web projects
- **Built-in TLS** — auto-generated EC P-256 self-signed certificate with cert pinning (no CA needed)
- **Self-hosted updates** — serve app releases from the server, with deploy trigger via API
- **Git worktree isolation** — multiple conversations in the same repo get isolated worktrees

## Requirements

- Python 3.10+
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (the Claude CLI) — `npm install -g @anthropic-ai/claude-code`

## Quick Start

### Install from PyPI

```bash
pipx install conn-server
conn-server start
```

On first run, `conn-server start` will:
1. Check that the Claude CLI is installed (guides you through installation if not)
2. Walk you through configuration (port, projects directory)
3. Generate TLS certificates and an auth token
4. Ask if you want to install as a background service (launchd on macOS, systemd on Linux)
5. Display a QR code for the mobile app

**Prerequisites** (if you don't have them):
- Python 3.10+ — `brew install python` or download from [python.org](https://www.python.org/downloads/)
- pipx — `brew install pipx` (or `python3 -m pip install --user pipx && python3 -m pipx ensurepath`)
- Node.js — `brew install node` or download from [nodejs.org](https://nodejs.org/) (needed for Claude CLI)
- Claude CLI — `npm install -g @anthropic-ai/claude-code && claude` (to authenticate)

### CLI Commands

```bash
conn-server start     # Start the server (interactive setup on first run)
conn-server stop      # Stop the background service
conn-server restart   # Restart the background service
conn-server status    # Show server status and health
conn-server setup     # Reconfigure (port, projects directory, auth token)
conn-server upgrade   # Upgrade to latest version and restart
conn-server qr        # Show the connection QR code
conn-server config    # Show current configuration
conn-server logs      # Show recent server logs
conn-server logs -f   # Follow logs in real time
conn-server version   # Show version
```

### Developer Setup (from source)

If you're contributing or want to run from the repo:

```bash
./setup.sh
```

This creates a virtual environment, installs dependencies via `pip install -e .`, walks through configuration, and optionally installs the system service. The `setup.sh` script also handles Homebrew Python detection on macOS for OpenSSL compatibility.

## Configuration

The server reads from `~/.conn/config.json`:

```json
{
  "auth_token": "your-secret-token",
  "host": "0.0.0.0",
  "port": 8443,
  "working_dir": "~/Projects"
}
```

Environment variables override the config file:
- `CONN_HOST` — bind address
- `CONN_PORT` — listen port
- `CONN_WORKING_DIR` — root directory for project listing

## API

All endpoints require `Authorization: Bearer {token}` unless noted.

### Core

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check (no auth) |
| `GET` | `/conversations` | List all conversations |
| `GET` | `/conversations/active` | List running conversation IDs |
| `DELETE` | `/conversations/{id}` | Delete conversation |
| `GET` | `/conversations/{id}/history` | Get message history |
| `POST` | `/upload?conversation_id={id}` | Upload image (multipart, max 20MB) |
| `GET` | `/files?path={path}` | Serve image file |
| `POST` | `/send-image` | Inject an image into a conversation stream |
| `POST` | `/restart` | Gracefully restart server |

### Projects & Files

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/projects` | List available project directories |
| `POST` | `/projects` | Create a new project directory |
| `GET` | `/projects/files?path={path}` | List files in a project directory |
| `GET` | `/projects/files/download?path={path}` | Download a project file |
| `GET` | `/projects/config?path={path}` | Get per-project custom instructions |
| `PUT` | `/projects/config` | Update per-project custom instructions |

### Web Preview

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/preview/check/{conversation_id}` | Check if conversation dir is previewable |
| `GET` | `/preview/check-project?path={path}` | Check if a directory is previewable |
| `POST` | `/preview/start` | Start a dev server for a conversation |
| `POST` | `/preview/start-project` | Start a dev server by directory path |
| `POST` | `/preview/restart` | Restart a preview server |
| `POST` | `/preview/stop` | Stop a conversation's preview server |
| `POST` | `/preview/stop-project` | Stop a preview by directory |
| `GET` | `/preview/status` | List all active preview servers |

### MCP Servers

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/mcp/servers` | List configured MCP servers |
| `POST` | `/mcp/servers` | Add an MCP server |
| `PUT` | `/mcp/servers/{name}` | Update an MCP server |
| `DELETE` | `/mcp/servers/{name}` | Delete an MCP server |
| `POST` | `/mcp/servers/{name}/toggle` | Enable/disable an MCP server |
| `GET` | `/mcp/catalog` | Browse the built-in MCP catalog |

### Agents

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/agents` | List all agents |
| `GET` | `/agents/{name}` | Get agent details |
| `POST` | `/agents` | Create an agent |
| `PUT` | `/agents/{name}` | Update an agent |
| `DELETE` | `/agents/{name}` | Delete an agent |

### Updates & Deploy

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/update/check` | Check for app update |
| `GET` | `/update/releases` | List available builds |
| `GET` | `/update/download` | Download latest APK |
| `GET` | `/update/download/{filename}` | Download specific APK |
| `POST` | `/deploy` | Trigger build and deploy |
| `GET` | `/deploy/status` | Check deploy status |

See [docs/api.md](docs/api.md) for the full REST and WebSocket protocol reference.

## Building a Custom Client

The WebSocket protocol is straightforward. Connect to `wss://{host}:{port}/ws/chat` (or `ws://` if TLS is not configured) and:

1. **Authenticate** — send `{"type": "auth", "token": "..."}` as the first message. Server responds with `{"type": "auth_ok"}`.
2. **Create a conversation** — send `{"type": "new_conversation", "conversation_id": "uuid", "name": "My Chat", "working_dir": "/path/to/project"}`
3. **Send messages** — send `{"type": "message", "text": "hello", "conversation_id": "uuid"}`
4. **Receive streaming responses** — the server sends `text_delta`, `tool_start`, `tool_done`, and `message_complete` events with the `conversation_id`
5. **Cancel** — send `{"type": "cancel", "conversation_id": "uuid"}` to stop a running response

The server sends `{"type": "ping"}` every 15 seconds; respond with `{"type": "pong"}` to keep the connection alive.

See [docs/api.md](docs/api.md) for all message types and payloads.

## Data Directory

All server data lives in `~/.conn/`:

```
~/.conn/
├── config.json           # Server configuration
├── sessions.json         # Active conversation metadata
├── tls/                  # TLS certificates (auto-generated on first run)
│   ├── server.crt        # EC P-256 certificate (PEM)
│   └── server.key        # Private key (PEM, 0600 permissions)
├── history/              # Conversation history (JSONL per conversation)
├── uploads/              # Uploaded images
├── logs/                 # Server logs
├── releases/             # Optional: self-hosted app releases
├── worktrees/            # Git worktrees for conversations
└── mcp_servers.json      # MCP server configuration
```

## TLS

The server generates a self-signed EC P-256 certificate on first run and serves all traffic over HTTPS (port 8443). The certificate's DER bytes are included in the startup QR code so the mobile app can pin the exact certificate — no CA or reverse proxy needed.

To regenerate certificates:
```bash
rm -rf ~/.conn/tls/
# Restart the server — new certs are generated automatically
# Re-scan the QR code on all connected devices
```

**macOS note**: System Python links against LibreSSL, which has TLS handshake issues with Android's BoringSSL. Use Homebrew Python (`brew install python`) instead. The setup script handles this automatically.

## Project Structure

The server is packaged as `conn-server` on PyPI. Source code lives in the `conn_server/` package:

```
conn_server/
├── __init__.py          # Package version
├── cli.py               # CLI entry point (conn-server command)
├── server.py            # FastAPI app, WebSocket + REST endpoints
├── config.py            # Config management (~/.conn/config.json)
├── tls.py               # EC P-256 cert generation, fingerprint, DER export
├── auth.py              # Bearer token verification
├── session_manager.py   # Conversation tracking, JSONL history
├── agent_manager.py     # Claude subprocess management
├── mcp_config.py        # MCP server configuration
├── mcp_catalog.py       # MCP tool catalog
├── preview_manager.py   # Background dev server management
├── project_config.py    # Per-project settings
└── git_utils.py         # Git utilities
```

## Testing

```bash
pytest -v                                    # All tests
pytest tests/test_session_manager.py         # Single file
pytest -k "test_create"                      # Pattern match
```

## License

[MIT](LICENSE)
