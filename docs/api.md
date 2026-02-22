# API Protocol Reference

> Full REST and WebSocket protocol for the Conn server.

## REST Endpoints

All endpoints require `Authorization: Bearer {token}` unless noted.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check (public, no auth) |
| `GET` | `/conversations` | List all conversations |
| `GET` | `/conversations/active` | List conversation IDs with running Claude processes |
| `DELETE` | `/conversations/{id}` | Delete conversation |
| `GET` | `/conversations/{id}/history` | Get message history (JSONL → JSON) |
| `POST` | `/upload?conversation_id={id}` | Upload image (multipart, max 20MB, jpg/png/gif/webp/heic) |
| `GET` | `/files?path={path}&token={token}` | Serve image file (png/jpg/gif/webp/svg/bmp). Auth via header or `?token=` |
| `GET` | `/projects` | List subdirectories of projects root as available project contexts |
| `POST` | `/preview/start` | Start a dev server (`{"conversation_id": "...", "command": [...]}`) |
| `POST` | `/preview/stop` | Stop a conversation's preview server (`{"conversation_id": "..."}`) |
| `GET` | `/preview/status` | List all active preview servers |
| `GET` | `/update/check` | Check for app update (returns `{versionCode, versionName, buildDate, notes}` or 404) |
| `GET` | `/update/releases` | List all available builds (returns `{releases: [...]}`) |
| `GET` | `/update/download` | Download latest APK. Auth via header or `?token=` query param |
| `GET` | `/update/download/{filename}` | Download specific APK by filename. Auth via header or `?token=` query param |
| `POST` | `/restart` | Gracefully restart server (kills all active Claude processes and preview servers, launchd restarts) |

## WebSocket Protocol

Endpoint: `wss://{host}:{port}/ws/chat` (TLS) or `ws://{host}:{port}/ws/chat` (plain)

### Client → Server

```json
{"type": "auth", "token": "..."}
{"type": "message", "text": "...", "conversation_id": "...", "session_id": "...", "image_paths": ["..."]}
{"type": "new_conversation", "conversation_id": "...", "name": "...", "working_dir": "..."}
{"type": "update_permissions", "conversation_id": "...", "allowed_tools": ["..."]}
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
{"type": "preview_available", "conversation_id": "...", "url": "..."}
{"type": "preview_stopped", "conversation_id": "..."}
{"type": "busy", "detail": "...", "conversation_id": "..."}
{"type": "error", "detail": "...", "conversation_id": "..."}
{"type": "cancelled", "conversation_id": "..."}
```
