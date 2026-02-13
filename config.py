"""Load and manage server configuration from ~/.claude-remote/config.json."""

import json
import secrets
from pathlib import Path

CONFIG_DIR = Path.home() / ".claude-remote"
CONFIG_FILE = CONFIG_DIR / "config.json"
SESSIONS_FILE = CONFIG_DIR / "sessions.json"
HISTORY_DIR = CONFIG_DIR / "history"
LOG_DIR = CONFIG_DIR / "logs"

DEFAULT_PORT = 8080
DEFAULT_HOST = "0.0.0.0"
WORKING_DIR = "/Users/patrickbucaria/Projects"


def _ensure_dirs():
    CONFIG_DIR.mkdir(exist_ok=True)
    HISTORY_DIR.mkdir(exist_ok=True)
    LOG_DIR.mkdir(exist_ok=True)


def load_config() -> dict:
    """Load config, generating auth token on first run."""
    _ensure_dirs()

    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return json.load(f)

    config = {
        "auth_token": secrets.token_hex(32),
        "host": DEFAULT_HOST,
        "port": DEFAULT_PORT,
        "working_dir": WORKING_DIR,
    }
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

    print(f"Generated new config at {CONFIG_FILE}")
    print(f"Auth token: {config['auth_token']}")
    return config


def get_auth_token() -> str:
    return load_config()["auth_token"]


def get_host() -> str:
    return load_config().get("host", DEFAULT_HOST)


def get_port() -> int:
    return load_config().get("port", DEFAULT_PORT)


def get_working_dir() -> str:
    return load_config().get("working_dir", WORKING_DIR)
