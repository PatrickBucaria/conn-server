"""conn-server CLI — manage your Conn server.

Usage:
  conn-server start           Start the server
  conn-server stop            Stop the system service
  conn-server restart         Restart the system service
  conn-server status          Show server status
  conn-server setup           Interactive setup / reconfigure
  conn-server upgrade         Upgrade to latest version and restart
  conn-server qr              Show the connection QR code
  conn-server config          Show current configuration
  conn-server logs [-f]       Show/follow server logs
  conn-server version         Show version
"""
from __future__ import annotations

import argparse
import io
import json
import os
import platform
import secrets
import shutil
import signal
import socket
import subprocess
import sys
from pathlib import Path

from . import __version__
from .config import (
    CONFIG_DIR, CONFIG_FILE, LOG_DIR,
    load_config, get_host, get_port, get_working_dir,
    DEFAULT_HOST, DEFAULT_PORT, WORKING_DIR,
    _write_private_file, _get_local_ip,
)
from .tls import ensure_certs, get_cert_fingerprint, get_cert_der_b64, TLS_DIR

# ---------- Colors ----------

RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[0;33m"
BLUE = "\033[0;34m"
BOLD = "\033[1m"
DIM = "\033[2m"
NC = "\033[0m"

PLIST_NAME = "com.conn.server"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{PLIST_NAME}.plist"


def _check_prerequisites() -> bool:
    """Check that required tools are available. Returns True if all OK."""
    ok = True

    if not shutil.which("claude"):
        _fail("Claude CLI not found")
        print()
        print("  The Conn server requires the Claude CLI to be installed and authenticated.")
        print()
        print(f"  1. Install Node.js from {BOLD}https://nodejs.org{NC} (if not installed)")
        print(f"  2. Run: {BOLD}npm install -g @anthropic-ai/claude-code{NC}")
        print(f"  3. Run: {BOLD}claude{NC}  (to authenticate)")
        print()
        ok = False

    return ok


def _info(msg: str):
    print(f"  {BLUE}->{NC} {msg}")


def _success(msg: str):
    print(f"  {GREEN}\u2713{NC} {msg}")


def _warn(msg: str):
    print(f"  {YELLOW}!{NC} {msg}")


def _fail(msg: str):
    print(f"  {RED}\u2717{NC} {msg}")


# ---------- Service helpers ----------

def _is_macos() -> bool:
    return platform.system() == "Darwin"


def _is_service_running() -> bool:
    if _is_macos():
        result = subprocess.run(
            ["launchctl", "list", PLIST_NAME],
            capture_output=True, text=True,
        )
        return result.returncode == 0
    else:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", "conn"],
            capture_output=True,
        )
        return result.returncode == 0


def _health_check() -> bool:
    port = get_port()
    result = subprocess.run(
        ["curl", "-skf", f"https://localhost:{port}/health"],
        capture_output=True,
    )
    return result.returncode == 0


def _install_launchd_service():
    """Install a launchd plist for the server."""
    host = get_host()
    port = get_port()
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Unload existing service
    if _is_service_running():
        subprocess.run(
            ["launchctl", "unload", str(PLIST_PATH)],
            capture_output=True,
        )

    # Find the conn-server executable
    conn_server_bin = shutil.which("conn-server")
    if not conn_server_bin:
        _fail("conn-server not found on PATH — can't install service")
        return False

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{PLIST_NAME}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{conn_server_bin}</string>
        <string>serve</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{LOG_DIR}/server.log</string>
    <key>StandardErrorPath</key>
    <string>{LOG_DIR}/server.err</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>{os.environ.get("HOME", "")}/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>"""

    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_text(plist_content)

    subprocess.run(["launchctl", "load", str(PLIST_PATH)])
    return True


def _install_systemd_service():
    """Install a systemd unit for the server."""
    conn_server_bin = shutil.which("conn-server")
    if not conn_server_bin:
        _fail("conn-server not found on PATH — can't install service")
        return False

    service_content = f"""[Unit]
Description=Conn Server
After=network.target

[Service]
Type=simple
User={os.environ.get("USER", "root")}
ExecStart={conn_server_bin} serve
Restart=always
RestartSec=5
Environment=PATH={os.environ.get("HOME", "")}/.local/bin:/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=multi-user.target
"""

    service_path = "/etc/systemd/system/conn.service"
    result = subprocess.run(
        ["sudo", "tee", service_path],
        input=service_content, capture_output=True, text=True,
    )
    if result.returncode != 0:
        _fail("Failed to write systemd service file")
        return False

    subprocess.run(["sudo", "systemctl", "daemon-reload"])
    subprocess.run(["sudo", "systemctl", "enable", "conn"])
    subprocess.run(["sudo", "systemctl", "start", "conn"])
    return True


# ---------- Commands ----------

def _prompt_yn(msg: str, default: str = "Y") -> bool:
    """Prompt the user for Y/N input."""
    try:
        answer = input(f"  {msg} [{default}]: ").strip() or default
        return answer.lower().startswith("y")
    except (EOFError, KeyboardInterrupt):
        print()
        return default.lower().startswith("y")


def _print_connection_info(show_qr: bool = False):
    """Print connection details to the terminal.

    Args:
        show_qr: If True, display QR code and offer to open SVG.
                 If False, show compact summary only.
    """
    config = load_config()
    port = get_port()
    token = config["auth_token"]
    working_dir = get_working_dir()
    local_ip = _get_local_ip()
    fingerprint = get_cert_fingerprint()

    print()
    print(f"  {BOLD}Conn Server v{__version__}{NC}")
    print(f"  {'─' * 50}")
    print()
    print(f"  {DIM}URL:{NC}        https://{local_ip}:{port}")
    print(f"  {DIM}Auth token:{NC} {token[:8]}...{token[-8:]}")
    print(f"  {DIM}TLS:{NC}        {fingerprint}")
    print(f"  {DIM}Projects:{NC}   {working_dir}")

    if not show_qr:
        svg_path = CONFIG_DIR / "qr-code.svg"
        print()
        if svg_path.exists():
            print(f"  {DIM}QR code:{NC}    {svg_path}")
        print(f"  {DIM}Show QR:{NC}    conn-server qr")
        print()
        return

    # QR code
    try:
        import qrcode
        from qrcode.image.svg import SvgPathImage

        cert_der_b64 = get_cert_der_b64()
        payload = json.dumps(
            {"host": local_ip, "port": port, "token": token, "cert": cert_der_b64},
            separators=(",", ":"),
        )

        # Terminal QR
        qr = qrcode.QRCode(box_size=1, border=2)
        qr.add_data(payload)
        qr.make(fit=True)
        buf = io.StringIO()
        qr.print_ascii(out=buf, invert=True)
        print()
        for line in buf.getvalue().splitlines():
            print(f"  {line}")
        print()

        # SVG file for small terminals
        svg_path = CONFIG_DIR / "qr-code.svg"
        qr_svg = qrcode.QRCode(box_size=10, border=4)
        qr_svg.add_data(payload)
        qr_svg.make(fit=True)
        img = qr_svg.make_image(image_factory=SvgPathImage)
        img.save(str(svg_path))

        print("  Scan the QR code above, or open the image:")
        print(f"  {svg_path}")
        print()

        if _prompt_yn("Open QR code image?"):
            if _is_macos():
                subprocess.run(["open", str(svg_path)], capture_output=True)
            elif shutil.which("xdg-open"):
                subprocess.run(["xdg-open", str(svg_path)], capture_output=True)
            else:
                _info(f"Open {svg_path} in a browser to scan")
    except ImportError:
        pass

    print()


def cmd_setup(args):
    """Interactive setup — configure port, project directory, and auth token."""
    print()
    print(f"  {BOLD}Conn Server Setup{NC}")
    print(f"  {'─' * 50}")
    print()

    if not _check_prerequisites():
        return

    # Load existing config or defaults
    existing = {}
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            existing = json.load(f)

    current_port = existing.get("port", DEFAULT_PORT)
    current_dir = existing.get("working_dir", WORKING_DIR)
    current_token = existing.get("auth_token", "")

    # Port
    try:
        port_input = input(f"  Port [{current_port}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    port = int(port_input) if port_input else current_port

    # Projects directory
    try:
        dir_input = input(f"  Projects directory [{current_dir}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    working_dir = dir_input if dir_input else current_dir

    # Expand ~ in path
    working_dir = str(Path(working_dir).expanduser())

    # Create projects dir if it doesn't exist
    wd_path = Path(working_dir)
    if not wd_path.exists():
        if _prompt_yn(f"  Directory {working_dir} doesn't exist. Create it?"):
            wd_path.mkdir(parents=True, exist_ok=True)
            _success(f"Created {working_dir}")
        else:
            _warn(f"Projects directory doesn't exist — you can create it later")

    # Auth token — generate if first run, keep existing otherwise
    if not current_token:
        token = secrets.token_hex(32)
        _info("Generated new auth token")
    else:
        if _prompt_yn("Regenerate auth token? (existing app connections will need to re-scan QR)", default="N"):
            token = secrets.token_hex(32)
            _info("Generated new auth token")
        else:
            token = current_token

    # Save config
    config = {
        "auth_token": token,
        "host": existing.get("host", DEFAULT_HOST),
        "port": port,
        "working_dir": working_dir,
    }

    from .config import _ensure_dirs
    _ensure_dirs()
    _write_private_file(CONFIG_FILE, json.dumps(config, indent=2))
    _success(f"Configuration saved to {CONFIG_FILE}")

    # Ensure TLS certs
    ensure_certs()
    _success("TLS certificates ready")

    print()
    _info(f"Run {BOLD}conn-server start{NC} to start the server")
    print()


def _run_first_time_setup():
    """Run interactive setup for first-time users. Returns True if completed."""
    print()
    print(f"  {BOLD}Conn Server Setup{NC}")
    print(f"  {'─' * 50}")
    print()

    if not _check_prerequisites():
        return False

    # Port
    try:
        port_input = input(f"  Port [{DEFAULT_PORT}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    port = int(port_input) if port_input else DEFAULT_PORT

    # Projects directory
    default_dir = WORKING_DIR
    try:
        dir_input = input(f"  Projects directory [{default_dir}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    working_dir = str(Path(dir_input).expanduser()) if dir_input else default_dir

    # Create projects dir if needed
    wd_path = Path(working_dir)
    if not wd_path.exists():
        if _prompt_yn(f"  Directory {working_dir} doesn't exist. Create it?"):
            wd_path.mkdir(parents=True, exist_ok=True)
            _success(f"Created {working_dir}")

    # Generate auth token
    token = secrets.token_hex(32)

    # Save config
    config = {
        "auth_token": token,
        "host": DEFAULT_HOST,
        "port": port,
        "working_dir": working_dir,
    }

    from .config import _ensure_dirs
    _ensure_dirs()
    _write_private_file(CONFIG_FILE, json.dumps(config, indent=2))
    _success(f"Configuration saved to {CONFIG_FILE}")

    # Ensure TLS certs
    ensure_certs()
    _success("TLS certificates ready")
    print()

    return True


def cmd_start(args):
    """Start the server."""
    # Check prerequisites
    if not _check_prerequisites():
        return

    # First-run: trigger interactive setup
    if not CONFIG_FILE.exists():
        _info("First run detected — let's get you set up")
        if not _run_first_time_setup():
            return  # Setup was cancelled

    config = load_config()
    host = get_host()
    port = get_port()

    ensure_certs()

    # If no service is installed yet, ask whether to install one
    service_running = _is_service_running()
    service_exists = PLIST_PATH.exists() if _is_macos() else Path("/etc/systemd/system/conn.service").exists()

    if service_exists and service_running:
        # Service already running — just inform and exit
        if _health_check():
            _success("Server is already running")
            _print_connection_info()
        else:
            _warn("Service is loaded but not responding — check: conn-server logs")
        return

    if service_exists and not service_running:
        # Service exists but stopped — restart it
        _info("Starting service...")
        if _is_macos():
            subprocess.run(["launchctl", "load", str(PLIST_PATH)], capture_output=True)
        else:
            subprocess.run(["sudo", "systemctl", "start", "conn"])
        import time
        time.sleep(2)
        if _health_check():
            _success("Server started")
            _print_connection_info()
        else:
            _warn("Server started but not responding yet — check: conn-server logs")
        return

    # No service installed — ask the user
    print()
    if _is_macos():
        install_service = _prompt_yn("Install as a background service (auto-start on boot)?")
    else:
        install_service = _prompt_yn("Install as a systemd service (auto-start on boot)?")

    if install_service:
        _info("Installing service...")
        if _is_macos():
            ok = _install_launchd_service()
        else:
            ok = _install_systemd_service()

        if ok:
            import time
            time.sleep(2)
            if _health_check():
                _success("Service installed and running")
                _print_connection_info(show_qr=True)
            else:
                _warn("Service installed but not responding yet")
                print("    Check logs: conn-server logs")
        return

    # Run in foreground
    _run_server()


def _run_server():
    """Start the uvicorn server (blocking). Used by both foreground and service modes."""
    host = get_host()
    port = get_port()
    ensure_certs()

    import uvicorn
    uvicorn.run(
        "conn_server.server:app",
        host=host,
        port=port,
        ssl_keyfile=str(TLS_DIR / "server.key"),
        ssl_certfile=str(TLS_DIR / "server.crt"),
    )


def cmd_serve(args):
    """Internal command: run the server directly (no interactive prompts).

    Used by launchd/systemd service plists.
    """
    if not CONFIG_FILE.exists():
        _fail("Server not configured — run: conn-server start")
        sys.exit(1)
    _run_server()


def cmd_stop(args):
    """Stop the system service."""
    if not _is_service_running():
        _warn("Server is not running")
        return

    _info("Stopping server...")
    if _is_macos():
        subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True)
    else:
        subprocess.run(["sudo", "systemctl", "stop", "conn"])
    _success("Server stopped")


def cmd_restart(args):
    """Restart the system service."""
    _info("Restarting server...")
    if _is_macos():
        if not PLIST_PATH.exists():
            _fail("Service not installed — run: conn-server start")
            return
        subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True)
        import time
        time.sleep(1)
        subprocess.run(["launchctl", "load", str(PLIST_PATH)])
    else:
        subprocess.run(["sudo", "systemctl", "restart", "conn"])

    import time
    time.sleep(2)
    if _health_check():
        _success("Server restarted")
    else:
        _warn("Server restarted but not responding yet — check: conn-server logs")


def cmd_status(args):
    """Show server status."""
    config = load_config()
    port = get_port()
    host = get_host()
    working_dir = get_working_dir()

    print()
    print(f"  {BOLD}Conn Server Status{NC} {DIM}v{__version__}{NC}")
    print(f"  {'─' * 50}")
    print()

    if _is_service_running():
        if _health_check():
            _success("Server is running and healthy")
        else:
            _warn("Service is loaded but server is not responding")
    else:
        _fail("Server is not running")

    local_ip = _get_local_ip()
    print()
    print(f"  {DIM}URL:{NC}        https://localhost:{port}")
    print(f"  {DIM}Host:{NC}       {host}")
    print(f"  {DIM}Projects:{NC}   {working_dir}")
    print(f"  {DIM}LAN:{NC}        https://{local_ip}:{port}")

    log_file = LOG_DIR / "server.log"
    if log_file.exists():
        size = log_file.stat().st_size
        if size > 1_000_000:
            print(f"  {DIM}Log size:{NC}   {size / 1_000_000:.1f}M")
        elif size > 1_000:
            print(f"  {DIM}Log size:{NC}   {size / 1_000:.1f}K")

    print()


def cmd_qr(args):
    """Show the connection QR code."""
    try:
        import qrcode
    except ImportError:
        _fail("qrcode package not installed")
        print("  Run: pip install qrcode")
        return

    config = load_config()
    port = get_port()
    token = config["auth_token"]

    ensure_certs()
    cert_der_b64 = get_cert_der_b64()
    local_ip = _get_local_ip()

    payload = json.dumps(
        {"host": local_ip, "port": port, "token": token, "cert": cert_der_b64},
        separators=(",", ":"),
    )

    qr = qrcode.QRCode(box_size=1, border=2)
    qr.add_data(payload)
    qr.make(fit=True)

    buf = io.StringIO()
    qr.print_ascii(out=buf, invert=True)
    print()
    for line in buf.getvalue().splitlines():
        print(f"  {line}")
    print()
    print("  Scan this QR code with the Conn app to connect.")
    print()


def cmd_config(args):
    """Show current configuration."""
    config = load_config()
    port = get_port()
    host = get_host()
    working_dir = get_working_dir()
    token = config["auth_token"]

    print()
    print(f"  {BOLD}Conn Configuration{NC}")
    print(f"  {'─' * 50}")
    print()
    print(f"  {DIM}Config file:{NC}  {CONFIG_FILE}")
    print(f"  {DIM}Host:{NC}         {host}")
    print(f"  {DIM}Port:{NC}         {port}")
    print(f"  {DIM}Projects:{NC}     {working_dir}")
    print(f"  {DIM}Auth token:{NC}   {token[:8]}...{token[-8:]}")
    print(f"  {DIM}TLS cert:{NC}     {TLS_DIR / 'server.crt'}")
    print(f"  {DIM}Log dir:{NC}      {LOG_DIR}")
    print()


def cmd_logs(args):
    """Show server logs."""
    log_file = LOG_DIR / "server.err"
    if not log_file.exists():
        log_file = LOG_DIR / "server.log"

    if not log_file.exists():
        if not _is_macos():
            _info("Showing systemd journal...")
            os.execvp("journalctl", ["journalctl", "-u", "conn", "-f", "--no-pager", "-n", "50"])
            return
        _fail(f"No log files found at {LOG_DIR}")
        return

    if args.follow:
        _info(f"Following {log_file} (Ctrl+C to stop)")
        os.execvp("tail", ["tail", "-f", str(log_file)])
    else:
        _info(f"Last 50 lines from {log_file}")
        print()
        os.execvp("tail", ["tail", "-n", "50", str(log_file)])


def cmd_upgrade(args):
    """Upgrade conn-server to the latest version and restart."""
    print()
    print(f"  {BOLD}Upgrading conn-server{NC}")
    print(f"  {'─' * 50}")
    print()

    _info(f"Current version: {__version__}")

    # Detect install method and upgrade
    pipx_bin = shutil.which("pipx")
    if pipx_bin:
        _info("Upgrading via pipx...")
        result = subprocess.run(
            [pipx_bin, "upgrade", "conn-server"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            if "is not installed" in result.stderr.lower() or "is not installed" in result.stdout.lower():
                # Installed via pip, not pipx
                _info("Not a pipx install — upgrading via pip...")
                subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "conn-server"])
            else:
                print(result.stdout)
                if result.stderr:
                    print(result.stderr)
                _fail("Upgrade failed")
                return
        else:
            print(result.stdout.strip())
    else:
        _info("Upgrading via pip...")
        subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "conn-server"])

    # Show new version
    result = subprocess.run(
        [sys.executable, "-c", "from conn_server import __version__; print(__version__)"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        new_version = result.stdout.strip()
        if new_version == __version__:
            _success(f"Already at latest version ({__version__})")
        else:
            _success(f"Upgraded: {__version__} → {new_version}")
    print()

    # Restart service if running
    if _is_service_running():
        _info("Restarting service...")
        if _is_macos():
            subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True)
            import time
            time.sleep(1)
            subprocess.run(["launchctl", "load", str(PLIST_PATH)])
        else:
            subprocess.run(["sudo", "systemctl", "restart", "conn"])

        import time
        time.sleep(2)
        if _health_check():
            _success("Server restarted with new version")
        else:
            _warn("Server restarted but not responding yet — check: conn-server logs")
    else:
        _info("Server is not running — start it with: conn-server start")

    print()


def cmd_version(args):
    """Show version."""
    print(f"conn-server {__version__}")


# ---------- Main ----------

def main():
    parser = argparse.ArgumentParser(
        prog="conn-server",
        description="Conn Server — remote control server for Claude CLI",
    )
    subparsers = parser.add_subparsers(dest="command")

    # start
    p_start = subparsers.add_parser("start", help="Start the server")
    p_start.set_defaults(func=cmd_start)

    # serve (internal — used by launchd/systemd)
    p_serve = subparsers.add_parser("serve")
    p_serve.set_defaults(func=cmd_serve)

    # stop
    p_stop = subparsers.add_parser("stop", help="Stop the system service")
    p_stop.set_defaults(func=cmd_stop)

    # restart
    p_restart = subparsers.add_parser("restart", help="Restart the system service")
    p_restart.set_defaults(func=cmd_restart)

    # status
    p_status = subparsers.add_parser("status", help="Show server status")
    p_status.set_defaults(func=cmd_status)

    # setup
    p_setup = subparsers.add_parser("setup", help="Interactive setup / reconfigure")
    p_setup.set_defaults(func=cmd_setup)

    # qr
    p_qr = subparsers.add_parser("qr", help="Show the connection QR code")
    p_qr.set_defaults(func=cmd_qr)

    # config
    p_config = subparsers.add_parser("config", help="Show current configuration")
    p_config.set_defaults(func=cmd_config)

    # logs
    p_logs = subparsers.add_parser("logs", help="Show server logs")
    p_logs.add_argument("-f", "--follow", action="store_true", help="Follow logs in real time")
    p_logs.set_defaults(func=cmd_logs)

    # upgrade
    p_upgrade = subparsers.add_parser("upgrade", help="Upgrade to latest version and restart")
    p_upgrade.set_defaults(func=cmd_upgrade)

    # version
    p_version = subparsers.add_parser("version", help="Show version")
    p_version.set_defaults(func=cmd_version)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    args.func(args)


if __name__ == "__main__":
    main()
