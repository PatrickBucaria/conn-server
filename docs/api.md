# API Protocol Reference

> Full REST and WebSocket protocol for the Conn server.

## REST Endpoints

All endpoints require `Authorization: Bearer {token}` unless noted.

### Conversations

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/conversations` | List all conversations (includes `git_branch` per conversation) |
| `GET` | `/conversations/active` | List conversation IDs with running Claude processes |
| `DELETE` | `/conversations/{id}` | Delete conversation (cleans up worktrees, previews, uploads) |
| `GET` | `/conversations/{id}/history` | Get message history (JSONL → JSON) |

### Files & Uploads

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/upload?conversation_id={id}` | Upload file (multipart, max 20MB). Supports images (jpg/png/gif/webp/heic/heif) and documents (pdf/txt/md/csv/json/code files/office docs) |
| `GET` | `/files?path={path}` | Serve image file (png/jpg/gif/webp/svg/bmp). Auth via header or `?token=` query param |
| `POST` | `/send-image` | Inject an image into a conversation's WebSocket stream (`{"path": "...", "conversation_id": "..."}`) |

### Projects

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/projects` | List subdirectories of projects root as available project contexts |
| `POST` | `/projects` | Create a new project directory (`{"name": "..."}`) |
| `GET` | `/projects/files?path={path}` | Browse files in a project directory (returns entries with name, path, is_dir, size) |
| `GET` | `/projects/files/download?path={path}` | Download a file from a project. Auth via header or `?token=` query param |
| `GET` | `/projects/config?path={path}` | Get project configuration (custom instructions) |
| `PUT` | `/projects/config` | Update project custom instructions (`{"path": "...", "custom_instructions": "..."}`) |

### Web Preview

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/preview/check/{conversation_id}` | Check if a conversation's project is previewable |
| `GET` | `/preview/check-project?path={path}` | Check if a project directory is previewable |
| `POST` | `/preview/start` | Start a dev server for a conversation (`{"conversation_id": "..."}`) |
| `POST` | `/preview/start-project` | Start a dev server for a project directory (`{"working_dir": "..."}`) |
| `POST` | `/preview/restart` | Restart a conversation's preview server (`{"conversation_id": "..."}`) |
| `POST` | `/preview/stop` | Stop a conversation's preview server (`{"conversation_id": "..."}`) |
| `POST` | `/preview/stop-project` | Stop a project's preview server (`{"working_dir": "..."}`) |
| `GET` | `/preview/status` | List all active preview servers |

### MCP Servers

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/mcp/servers` | List all configured MCP servers (env values masked) |
| `POST` | `/mcp/servers` | Add a new MCP server |
| `PUT` | `/mcp/servers/{name}` | Update an existing MCP server |
| `DELETE` | `/mcp/servers/{name}` | Remove an MCP server |
| `POST` | `/mcp/servers/{name}/toggle` | Enable or disable an MCP server (`{"enabled": true/false}`) |
| `GET` | `/mcp/catalog` | List available MCP server templates (with installed status) |

### Agents

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/agents` | List all available agents |
| `GET` | `/agents/{name}` | Get full agent details (including prompt) |
| `POST` | `/agents` | Create a new agent |
| `PUT` | `/agents/{name}` | Update an existing agent |
| `DELETE` | `/agents/{name}` | Delete an agent |

### App Updates

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/update/check` | Check for app update (returns `{versionCode, versionName, buildDate, notes}` or 404) |
| `GET` | `/update/releases` | List all available builds (returns `{releases: [...]}`) |
| `GET` | `/update/download` | Download latest APK. Auth via header or `?token=` query param |
| `GET` | `/update/download/{filename}` | Download specific APK by filename. Auth via header or `?token=` query param |

### Deploy

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/deploy` | Trigger a build and deploy to Firebase App Distribution (runs in background) |
| `GET` | `/deploy/status` | Check status of current or last deploy (`idle`/`in_progress`/`success`/`failed`) |

### Server Management

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check (public, no auth required) |
| `POST` | `/restart` | Gracefully restart server (kills all active Claude processes and preview servers, launchd restarts) |

## WebSocket Protocol

Endpoint: `wss://{host}:{port}/ws/chat` (TLS) or `ws://{host}:{port}/ws/chat` (plain)

### Client → Server

```json
{"type": "auth", "token": "..."}
{"type": "message", "text": "...", "conversation_id": "...", "session_id": "...", "image_paths": ["..."]}
{"type": "new_conversation", "conversation_id": "...", "name": "...", "working_dir": "...", "allowed_tools": ["..."], "mcp_servers": ["..."], "model": "...", "agent": "...", "effort": "low|medium|high"}
{"type": "update_permissions", "conversation_id": "...", "allowed_tools": ["..."]}
{"type": "update_mcp_servers", "conversation_id": "...", "mcp_servers": ["..."]}
{"type": "cancel", "conversation_id": "..."}
{"type": "pong"}
```

### Server → Client

```json
{"type": "auth_ok"}
{"type": "ping"}
{"type": "text_delta", "text": "...", "conversation_id": "..."}
{"type": "tool_start", "tool": "...", "input_summary": "...", "conversation_id": "..."}
{"type": "tool_done", "conversation_id": "..."}
{"type": "image", "path": "...", "conversation_id": "..."}
{"type": "message_complete", "conversation_id": "...", "session_id": "..."}
{"type": "conversation_created", "conversation_id": "...", "name": "..."}
{"type": "conversation_renamed", "conversation_id": "...", "name": "..."}
{"type": "permissions_updated", "conversation_id": "...", "allowed_tools": ["..."]}
{"type": "mcp_servers_updated", "conversation_id": "...", "mcp_servers": ["..."]}
{"type": "preview_available", "conversation_id": "...", "working_dir": "...", "port": 8100}
{"type": "preview_stopped", "conversation_id": "...", "working_dir": "..."}
{"type": "busy", "detail": "...", "conversation_id": "..."}
{"type": "error", "detail": "...", "conversation_id": "..."}
{"type": "cancelled", "conversation_id": "..."}
```
