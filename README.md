# Conn Server

A self-hosted server that lets you interact with [Claude Code](https://docs.anthropic.com/en/docs/claude-code) from any device over your network. Run Claude on a home server, connect from your phone or any WebSocket client.

## Features

- **WebSocket streaming** — real-time text deltas, tool use events, and image sharing
- **Multiple concurrent conversations** — each with its own Claude process and working directory
- **Session resume** — conversations persist across reconnects
- **Project context** — point each conversation at a different project directory
- **MCP integration** — configure Model Context Protocol servers for extended tool access
- **Web preview** — auto-detect and serve dev servers for web projects
- **Built-in TLS** — auto-generated EC P-256 self-signed certificate with cert pinning (no CA needed)
- **Self-hosted updates** — optionally serve app releases from the server

## Requirements

- Python 3.9+
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (the Claude CLI) — `npm install -g @anthropic-ai/claude-code`

## Quick Start

### Interactive Setup

```bash
./setup.sh
```

This will:
1. Check Python and Claude CLI are installed
2. Create a virtual environment and install dependencies
3. Walk you through server configuration (projects directory, port, auth token)
4. Optionally install as a system service (launchd on macOS, systemd on Linux)
5. Print connection info and a QR code for the mobile app

### Manual Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python server.py
```

On first run, the server generates a config file at `~/.conn/config.json` with a random auth token and TLS certificates at `~/.conn/tls/`.

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

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check (no auth) |
| `GET` | `/conversations` | List all conversations |
| `GET` | `/conversations/active` | List running conversation IDs |
| `DELETE` | `/conversations/{id}` | Delete conversation |
| `GET` | `/conversations/{id}/history` | Get message history |
| `POST` | `/upload?conversation_id={id}` | Upload image |
| `GET` | `/projects` | List available project directories |
| `POST` | `/restart` | Gracefully restart server |

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

## Testing

```bash
pytest -v                                    # All tests
pytest tests/test_session_manager.py         # Single file
pytest -k "test_create"                      # Pattern match
```

## License

[MIT](LICENSE)
