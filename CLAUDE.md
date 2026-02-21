# CLAUDE.md - AI Assistant Guidelines for conn-server

## Project Overview

Conn server is a Python FastAPI backend that bridges WebSocket/REST clients to Claude Code (the Claude CLI). It runs on a home server (e.g., Mac Mini) and lets you interact with Claude from any device over the network.

## Quick Reference

### Build & Run

```bash
# First-time setup
./setup.sh

# Manual start
pip install -r requirements.txt
python server.py
# Or: uvicorn server:app --host 0.0.0.0 --port 8080
```

### Testing

```bash
pytest -v                                    # All tests
pytest tests/test_session_manager.py         # Single file
pytest -k "test_create"                      # Pattern match
```

**Test files** (in `tests/`):
- `conftest.py` — Shared fixtures (`tmp_config_dir` patches config paths to temp dirs for isolation)
- `test_session_manager.py` — Conversation CRUD, persistence, JSONL history
- `test_rest_endpoints.py` — REST endpoints (health, conversations, projects, upload, updates)
- `test_event_forwarder.py` — EventForwarder stream-json mapping, tool input accumulation
- `test_auth_config.py` — Token verification, config loading/generation
- `test_concurrency.py` — Per-conversation locks, cancel targeting, concurrent process management
- `test_agent_manager.py` — Agent CRUD, frontmatter parsing
- `test_git_utils.py` — Git worktree operations
- `test_mcp_catalog.py` — MCP server catalog
- `test_mcp_config.py` — MCP server configuration
- `test_preview_manager.py` — Dev server lifecycle management
- `test_project_config.py` — Per-project custom instructions

**After modifying code**, run `pytest -v` and update/add tests for any changed behavior. Tests use `tmp_config_dir` to isolate from real `~/.conn/` data.

## Project Structure

```
├── server.py              # Main app, WebSocket + REST endpoints
├── session_manager.py     # Conversation tracking, JSONL history
├── config.py              # Config management (~/.conn/config.json)
├── auth.py                # Bearer token verification
├── agent_manager.py       # Agent CRUD and frontmatter parsing
├── mcp_config.py          # MCP server configuration
├── mcp_catalog.py         # MCP server catalog (available integrations)
├── preview_manager.py     # Background dev server management
├── project_config.py      # Per-project custom instructions
├── git_utils.py           # Git utilities (worktrees, branch detection)
├── requirements.txt       # Python dependencies
├── pyproject.toml         # Project metadata + tool config
├── setup.sh               # Interactive setup (venv, config, service)
├── tests/                 # Pytest test suite
│   ├── conftest.py
│   └── test_*.py
└── docs/
    ├── api.md             # REST & WebSocket protocol reference
    └── architecture.md    # Server architecture details
```

## Code Conventions

- Use `snake_case` for functions and files
- Use `PascalCase` for classes
- FastAPI with async endpoints
- Bearer token authentication on all endpoints except `/health`
- WebSocket JSON protocol for real-time streaming

## Key Files

| File | Purpose |
|------|---------|
| `server.py` | Main FastAPI app, WebSocket + REST endpoints |
| `config.py` | Reads `~/.conn/config.json`, env var overrides |
| `auth.py` | Bearer token verification |
| `session_manager.py` | Conversation tracking, JSONL history |
| `agent_manager.py` | Agent CRUD, markdown frontmatter parsing |
| `mcp_config.py` | MCP server config (`~/.conn/mcp_servers.json`) |
| `mcp_catalog.py` | Catalog of available MCP integrations |
| `preview_manager.py` | Dev server lifecycle (ports 8100-8199) |
| `project_config.py` | Per-project custom instructions |
| `git_utils.py` | Git worktree management |

## Common Pitfalls

1. **Server requires Claude CLI**: The `claude` command must be available on PATH
2. **Concurrent messages**: Server uses per-conversation locks. If a lock doesn't release within 5s, a `busy` event is returned
3. **WebSocket auth**: First message must be `{"type": "auth", "token": "..."}` or connection is rejected
4. **Stream-json event format varies**: Claude CLI with `-p` emits complete `assistant` events, NOT streaming `content_block_start`/`content_block_delta`/`content_block_stop` events. The `EventForwarder` handles both paths
5. **MCP tool auto-allow**: MCP tools must be included in `--allowedTools` or the CLI will prompt (hanging the subprocess). The server auto-appends `mcp__<name>__*` wildcard patterns
6. **Image Read tool results**: Claude's stream-json can emit multi-MB lines. Stdout buffer is set to 32MB
7. **Never run dev servers via Bash tool**: Long-lived processes hang the conversation lock. Use `PreviewManager` instead
8. **Python venv has hardcoded paths**: If the project is moved, recreate the venv
