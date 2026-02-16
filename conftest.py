"""Shared test fixtures for the ClaudeRemote server test suite."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def tmp_config_dir(tmp_path):
    """Create a temporary config directory and patch config module paths."""
    sessions_file = tmp_path / "sessions.json"
    history_dir = tmp_path / "history"
    uploads_dir = tmp_path / "uploads"
    log_dir = tmp_path / "logs"
    worktrees_dir = tmp_path / "worktrees"
    config_file = tmp_path / "config.json"

    history_dir.mkdir()
    uploads_dir.mkdir()
    log_dir.mkdir()
    worktrees_dir.mkdir()

    token = "test-token-abc123"
    config_data = {
        "auth_token": token,
        "host": "0.0.0.0",
        "port": 8080,
        "working_dir": str(tmp_path / "projects"),
    }
    config_file.write_text(json.dumps(config_data))

    # Create the projects directory
    (tmp_path / "projects").mkdir()

    # Patch in both config and session_manager modules, since session_manager
    # imports SESSIONS_FILE and HISTORY_DIR at the top level.
    with patch("config.CONFIG_DIR", tmp_path), \
         patch("config.CONFIG_FILE", config_file), \
         patch("config.SESSIONS_FILE", sessions_file), \
         patch("config.HISTORY_DIR", history_dir), \
         patch("config.UPLOADS_DIR", uploads_dir), \
         patch("config.LOG_DIR", log_dir), \
         patch("config.WORKTREES_DIR", worktrees_dir), \
         patch("config.WORKING_DIR", str(tmp_path / "projects")), \
         patch("session_manager.SESSIONS_FILE", sessions_file), \
         patch("session_manager.HISTORY_DIR", history_dir), \
         patch("server.UPLOADS_DIR", uploads_dir), \
         patch("server.LOG_DIR", log_dir), \
         patch("git_utils.WORKTREES_DIR", worktrees_dir):
        yield {
            "dir": tmp_path,
            "token": token,
            "sessions_file": sessions_file,
            "history_dir": history_dir,
            "uploads_dir": uploads_dir,
            "worktrees_dir": worktrees_dir,
            "projects_dir": tmp_path / "projects",
            "config_file": config_file,
        }


@pytest.fixture
def auth_header(tmp_config_dir):
    """Return a valid Authorization header."""
    return f"Bearer {tmp_config_dir['token']}"
