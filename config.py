"""Load and manage server configuration from ~/.conn/config.json.

Configuration priority (highest to lowest):
  1. Environment variables: CONN_PORT, CONN_HOST, CONN_WORKING_DIR
  2. Config file: ~/.conn/config.json
  3. Defaults
"""
from __future__ import annotations

import io
import json
import os
import secrets
import socket
from pathlib import Path

CONFIG_DIR = Path.home() / ".conn"
CONFIG_FILE = CONFIG_DIR / "config.json"
SESSIONS_FILE = CONFIG_DIR / "sessions.json"
HISTORY_DIR = CONFIG_DIR / "history"
LOG_DIR = CONFIG_DIR / "logs"
UPLOADS_DIR = CONFIG_DIR / "uploads"
WORKTREES_DIR = CONFIG_DIR / "worktrees"
RELEASES_DIR = CONFIG_DIR / "releases"
PROJECTS_CONFIG_DIR = CONFIG_DIR / "projects"

DEFAULT_PORT = 8443
DEFAULT_HOST = "0.0.0.0"
WORKING_DIR = str(Path.home() / "Projects")


def _ensure_dirs():
    CONFIG_DIR.mkdir(mode=0o700, exist_ok=True)
    HISTORY_DIR.mkdir(mode=0o700, exist_ok=True)
    LOG_DIR.mkdir(mode=0o700, exist_ok=True)
    UPLOADS_DIR.mkdir(mode=0o700, exist_ok=True)
    RELEASES_DIR.mkdir(mode=0o700, exist_ok=True)
    PROJECTS_CONFIG_DIR.mkdir(mode=0o700, exist_ok=True)


def _write_private_file(path: Path, content: str):
    """Write a file with owner-only permissions (0600)."""
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, content.encode())
    finally:
        os.close(fd)


def _get_local_ip() -> str:
    """Get the machine's local network IP address."""
    try:
        # Connect to an external address to determine the local IP
        # (doesn't actually send traffic)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def load_config() -> dict:
    """Load config, generating auth token on first run."""
    _ensure_dirs()

    is_first_run = not CONFIG_FILE.exists()

    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            config = json.load(f)
    else:
        config = {
            "auth_token": secrets.token_hex(32),
            "host": DEFAULT_HOST,
            "port": DEFAULT_PORT,
            "working_dir": WORKING_DIR,
        }
        _write_private_file(CONFIG_FILE, json.dumps(config, indent=2))

    config["_is_first_run"] = is_first_run
    return config


def get_auth_token() -> str:
    return load_config()["auth_token"]


def get_host() -> str:
    return os.environ.get("CONN_HOST") or load_config().get("host", DEFAULT_HOST)


def get_port() -> int:
    env_port = os.environ.get("CONN_PORT")
    if env_port:
        return int(env_port)
    return load_config().get("port", DEFAULT_PORT)


def get_working_dir() -> str:
    return os.environ.get("CONN_WORKING_DIR") or load_config().get("working_dir", WORKING_DIR)


def _print_qr_code(host: str, port: int, token: str, cert_der_b64: str | None = None):
    """Print a QR code to the terminal containing connection details."""
    try:
        import qrcode
    except ImportError:
        print("  (Install 'qrcode' package to display a scannable QR code)")
        return

    payload = {"host": host, "port": port, "token": token}
    if cert_der_b64:
        payload["cert"] = cert_der_b64
    data = json.dumps(payload, separators=(",", ":"))  # Compact JSON
    qr = qrcode.QRCode(box_size=1, border=2)
    qr.add_data(data)
    qr.make(fit=True)

    # Capture ASCII output and indent it
    buf = io.StringIO()
    qr.print_ascii(out=buf, invert=True)
    for line in buf.getvalue().splitlines():
        print(f"  {line}")

    print()
    print("  Scan this QR code with the Conn app to connect.")


def print_startup_banner():
    """Print server connection info on startup."""
    from tls import ensure_certs, get_cert_fingerprint, get_cert_der_b64

    config = load_config()
    is_first_run = config.get("_is_first_run", False)
    host = get_host()
    port = get_port()
    token = config["auth_token"]
    working_dir = get_working_dir()
    local_ip = _get_local_ip()

    # Ensure TLS certs exist
    ensure_certs()
    fingerprint = get_cert_fingerprint()
    cert_der_b64 = get_cert_der_b64()

    url = f"https://{local_ip}:{port}"

    print()
    print("=" * 50)
    print("  Conn Server")
    print()
    print(f"  URL:        {url}")
    print(f"  Auth token: {token}")
    print(f"  TLS:        {fingerprint}")
    print(f"  Projects:   {working_dir}")
    print("=" * 50)

    _print_qr_code(local_ip, port, token, cert_der_b64)

    if is_first_run:
        print()
        print(f"  Config generated at {CONFIG_FILE}")
        print("  Scan the QR code above with the Conn app, or enter the URL and token manually.")

    working_dir_path = Path(working_dir)
    if not working_dir_path.is_dir():
        print()
        print(f"  Warning: Projects directory does not exist: {working_dir}")
        print("  Set CONN_WORKING_DIR or edit ~/.conn/config.json")

    print()
