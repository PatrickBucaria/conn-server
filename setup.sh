#!/bin/bash
set -euo pipefail

# Conn Server Setup
# Sets up the Python environment, configures the server, and optionally
# installs it as a system service (launchd on macOS, systemd on Linux).

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$PROJECT_ROOT/venv"
CONFIG_DIR="$HOME/.conn"
CONFIG_FILE="$CONFIG_DIR/config.json"
PLIST_NAME="com.conn.server"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_NAME.plist"

DEFAULT_PROJECTS_DIR="$HOME/Projects"
DEFAULT_PORT=8443
DEFAULT_HOST="0.0.0.0"
TLS_DIR="$CONFIG_DIR/tls"

OS="$(uname)"

# ---------- Colors ----------

RED=$'\033[0;31m'
GREEN=$'\033[0;32m'
YELLOW=$'\033[0;33m'
BLUE=$'\033[0;34m'
BOLD=$'\033[1m'
NC=$'\033[0m' # No color

# ---------- Helpers ----------

info()    { printf "  ${BLUE}→${NC} %s\n" "$1"; }
success() { printf "  ${GREEN}✓${NC} %s\n" "$1"; }
warn()    { printf "  ${YELLOW}!${NC} %s\n" "$1"; }
fail()    { printf "  ${RED}✗${NC} %s\n" "$1"; }

prompt() {
  local prompt_text="$1"
  local default="$2"
  local var_name="$3"
  printf "  %s [%s]: " "$prompt_text" "$default"
  read -r input
  eval "$var_name=\"${input:-$default}\""
}

prompt_yn() {
  local prompt_text="$1"
  local default="$2"
  printf "  %s [%s]: " "$prompt_text" "$default"
  read -r input
  input="${input:-$default}"
  case "$input" in
    [Yy]*) return 0 ;;
    *) return 1 ;;
  esac
}

check_command() {
  command -v "$1" &>/dev/null
}

# ---------- Banner ----------

echo ""
echo "  ${BOLD}   ██████╗ ██████╗ ███╗   ██╗███╗   ██╗${NC}"
echo "  ${BOLD}  ██╔════╝██╔═══██╗████╗  ██║████╗  ██║${NC}"
echo "  ${BOLD}  ██║     ██║   ██║██╔██╗ ██║██╔██╗ ██║${NC}"
echo "  ${BOLD}  ██║     ██║   ██║██║╚██╗██║██║╚██╗██║${NC}"
echo "  ${BOLD}  ╚██████╗╚██████╔╝██║ ╚████║██║ ╚████║${NC}"
echo "  ${BOLD}   ╚═════╝ ╚═════╝ ╚═╝  ╚═══╝╚═╝  ╚═══╝${NC}"
echo ""
echo "  ${BLUE}Server Setup${NC}"
echo ""

# ==========================================================
# Homebrew (macOS)
# ==========================================================

if [ "$OS" = "Darwin" ]; then
  if check_command brew; then
    success "Homebrew is installed"
  else
    warn "Homebrew is not installed"
    if prompt_yn "Install Homebrew? (recommended for managing dependencies)" "Y"; then
      echo ""
      info "Installing Homebrew..."
      /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
      eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null)" || true
      success "Homebrew installed"
    else
      warn "Skipping Homebrew — you'll need to install dependencies manually"
    fi
  fi
  echo ""
fi

# ==========================================================
# Python
# ==========================================================

echo "  ${BOLD}Python${NC}"
echo "  ──────────────────────────────────────────────────"
echo ""

# Find the best Python to use.
# On macOS, prefer Homebrew Python because it links against OpenSSL.
# The system Python uses LibreSSL, which has TLS compatibility issues
# with modern clients (Android/BoringSSL handshake failures).
PYTHON_BIN=""

if [ "$OS" = "Darwin" ]; then
  # Check for Homebrew Python (preferred on macOS for OpenSSL support)
  # Homebrew puts the unversioned "python3" in libexec/bin/, while
  # bin/ only has the versioned name (e.g., python3.12).
  for brew_py in python@3.13 python@3.12 python@3.11 python@3.10; do
    brew_prefix="$(brew --prefix "$brew_py" 2>/dev/null)" 2>/dev/null || continue
    for candidate in "$brew_prefix/libexec/bin/python3" "$brew_prefix/bin/python3"; do
      if [ -x "$candidate" ] 2>/dev/null; then
        PYTHON_BIN="$candidate"
        break 2
      fi
    done
  done
fi

# Fall back to system python3
if [ -z "$PYTHON_BIN" ] && check_command python3; then
  PYTHON_BIN="$(command -v python3)"
fi

if [ -n "$PYTHON_BIN" ]; then
  py_ver=$("$PYTHON_BIN" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
  py_major=$(echo "$py_ver" | cut -d. -f1)
  py_minor=$(echo "$py_ver" | cut -d. -f2)
  ssl_ver=$("$PYTHON_BIN" -c "import ssl; print(ssl.OPENSSL_VERSION)")

  if [ "$py_major" -ge 3 ] && [ "$py_minor" -ge 10 ]; then
    success "Python $py_ver found ($PYTHON_BIN)"
    success "SSL: $ssl_ver"
  else
    warn "Python $py_ver found but 3.10+ is required"
  fi
else
  fail "Python 3 not found"
fi

NEEDS_PYTHON=false
if [ -z "$PYTHON_BIN" ] || { [ "$py_major" -le 3 ] && [ "$py_minor" -lt 10 ]; }; then
  NEEDS_PYTHON=true
elif [ "$OS" = "Darwin" ] && echo "$ssl_ver" | grep -qi "libressl"; then
  warn "LibreSSL detected — causes TLS handshake failures with Android/iOS clients"
  NEEDS_PYTHON=true
fi

if [ "$NEEDS_PYTHON" = true ]; then
  if [ "$OS" = "Darwin" ] && check_command brew; then
    if prompt_yn "Install Python 3.12 via Homebrew? (uses OpenSSL for TLS compatibility)" "Y"; then
      brew install python@3.12
      brew_prefix="$(brew --prefix python@3.12)"
      if [ -x "$brew_prefix/libexec/bin/python3" ]; then
        PYTHON_BIN="$brew_prefix/libexec/bin/python3"
      else
        PYTHON_BIN="$brew_prefix/bin/python3"
      fi
      py_ver=$("$PYTHON_BIN" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
      ssl_ver=$("$PYTHON_BIN" -c "import ssl; print(ssl.OPENSSL_VERSION)")
      success "Python $py_ver installed"
      success "SSL: $ssl_ver"
    else
      if echo "$ssl_ver" | grep -qi "libressl"; then
        warn "Continuing with LibreSSL — Android/iOS connections will fail"
      fi
    fi
  else
    echo ""
    echo "  Install Python 3.10+:"
    if [ "$OS" = "Darwin" ]; then
      echo "    brew install python@3.12"
    else
      echo "    Ubuntu/Debian:  sudo apt install python3 python3-venv python3-pip"
      echo "    Fedora:         sudo dnf install python3"
    fi
  fi
fi

# ==========================================================
# Claude CLI
# ==========================================================

echo ""
echo "  ${BOLD}Claude CLI${NC}"
echo "  ──────────────────────────────────────────────────"
echo ""

if check_command claude; then
  claude_ver=$(claude --version 2>/dev/null || echo "unknown")
  success "Claude CLI found ($claude_ver)"
else
  warn "Claude CLI not found — the server requires it to function"
  echo ""

  if check_command npm; then
    if prompt_yn "Install Claude CLI via npm?" "Y"; then
      echo ""
      info "Installing @anthropic-ai/claude-code..."
      npm install -g @anthropic-ai/claude-code
      if check_command claude; then
        success "Claude CLI installed"
        echo ""
        warn "Run 'claude' once to authenticate before starting the server"
      else
        warn "Install completed but 'claude' not found on PATH"
        warn "You may need to restart your terminal"
      fi
    fi
  else
    info "npm not found — install Node.js first, then:"
    echo ""
    echo "  Install Claude CLI:"
    echo "    npm install -g @anthropic-ai/claude-code"
    echo ""
    echo "  Then run 'claude' once to authenticate."
  fi
fi

# ==========================================================
# Server Setup
# ==========================================================

echo ""
echo "  ${BOLD}Server Setup${NC}"
echo "  ══════════════════════════════════════════════════"
echo ""

# --- Python venv ---
info "Setting up Python environment..."

# Recreate venv if it was built with a different Python
if [ -d "$VENV_DIR" ]; then
  VENV_PYTHON="$("$VENV_DIR/bin/python3" -c "import sys; print(sys.base_prefix)" 2>/dev/null || echo "")"
  TARGET_PREFIX="$("$PYTHON_BIN" -c "import sys; print(sys.prefix)" 2>/dev/null || echo "")"
  if [ "$VENV_PYTHON" != "$TARGET_PREFIX" ]; then
    info "Recreating venv (switching to $("$PYTHON_BIN" -c "import ssl; print(ssl.OPENSSL_VERSION)"))"
    rm -rf "$VENV_DIR"
  fi
fi

if [ ! -d "$VENV_DIR" ]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install -q --disable-pip-version-check -r "$PROJECT_ROOT/requirements.txt"
success "Python environment ready"
echo ""

# --- Config ---
EXISTING_PROJECTS_DIR="$DEFAULT_PROJECTS_DIR"
EXISTING_PORT="$DEFAULT_PORT"
EXISTING_HOST="$DEFAULT_HOST"
EXISTING_TOKEN=""

if [ -f "$CONFIG_FILE" ]; then
  info "Existing config found at $CONFIG_FILE"
  EXISTING_PROJECTS_DIR=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE')).get('working_dir', '$DEFAULT_PROJECTS_DIR'))" 2>/dev/null || echo "$DEFAULT_PROJECTS_DIR")
  EXISTING_PORT=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE')).get('port', $DEFAULT_PORT))" 2>/dev/null || echo "$DEFAULT_PORT")
  EXISTING_HOST=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE')).get('host', '$DEFAULT_HOST'))" 2>/dev/null || echo "$DEFAULT_HOST")
  EXISTING_TOKEN=$(python3 -c "import json; print(json.load(open('$CONFIG_FILE')).get('auth_token', ''))" 2>/dev/null || echo "")
fi

echo ""
prompt "Projects directory" "$EXISTING_PROJECTS_DIR" PROJECTS_DIR
prompt "Port" "$EXISTING_PORT" PORT
prompt "Host" "$EXISTING_HOST" HOST

# Expand ~ in projects dir
PROJECTS_DIR="${PROJECTS_DIR/#\~/$HOME}"

# Create projects dir if needed
if [ ! -d "$PROJECTS_DIR" ]; then
  if prompt_yn "Directory $PROJECTS_DIR does not exist. Create it?" "Y"; then
    mkdir -p "$PROJECTS_DIR"
    success "Created $PROJECTS_DIR"
  fi
fi

echo ""

# Preserve existing token or generate a new one
mkdir -p "$CONFIG_DIR"
if [ -n "$EXISTING_TOKEN" ]; then
  AUTH_TOKEN="$EXISTING_TOKEN"
else
  AUTH_TOKEN=$("$VENV_DIR/bin/python3" -c "import secrets; print(secrets.token_hex(32))")
fi

# Write config
"$VENV_DIR/bin/python3" -c "
import json, os
config = {
    'auth_token': '$AUTH_TOKEN',
    'host': '$HOST',
    'port': $PORT,
    'working_dir': '$PROJECTS_DIR',
}
fd = os.open('$CONFIG_FILE', os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
try:
    os.write(fd, json.dumps(config, indent=2).encode())
finally:
    os.close(fd)
"
success "Config saved to $CONFIG_FILE"

# --- TLS certificates ---
echo ""
info "Generating TLS certificate..."
"$VENV_DIR/bin/python3" -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT')
from tls import ensure_certs, get_cert_fingerprint
ensure_certs()
print(get_cert_fingerprint())
" > /tmp/conn_fingerprint 2>&1
CERT_FINGERPRINT=$(tail -1 /tmp/conn_fingerprint)
rm -f /tmp/conn_fingerprint
success "TLS certificate ready"
echo "  Fingerprint: $CERT_FINGERPRINT"

# --- Service ---
echo ""
if [ "$OS" = "Darwin" ]; then
  if prompt_yn "Install as launchd service (auto-start on boot)?" "Y"; then
    echo ""

    # Unload existing service if present
    if launchctl list "$PLIST_NAME" &>/dev/null; then
      launchctl unload "$PLIST_PATH" 2>/dev/null || true
    fi

    mkdir -p "$CONFIG_DIR/logs"

    cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$PLIST_NAME</string>
    <key>ProgramArguments</key>
    <array>
        <string>$VENV_DIR/bin/uvicorn</string>
        <string>server:app</string>
        <string>--host</string>
        <string>$HOST</string>
        <string>--port</string>
        <string>$PORT</string>
        <string>--ssl-keyfile</string>
        <string>$TLS_DIR/server.key</string>
        <string>--ssl-certfile</string>
        <string>$TLS_DIR/server.crt</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$PROJECT_ROOT</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$CONFIG_DIR/logs/server.log</string>
    <key>StandardErrorPath</key>
    <string>$CONFIG_DIR/logs/server.err</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
PLIST

    launchctl load "$PLIST_PATH"
    success "Service installed and running"

    # Health check
    echo ""
    info "Checking server health..."
    sleep 2
    if curl -skf "https://localhost:$PORT/health" >/dev/null 2>&1; then
      success "Server is responding"
    else
      warn "Server not responding yet — it may still be starting"
      echo "    Check logs: tail -f $CONFIG_DIR/logs/server.err"
      echo ""
      if ! check_command claude; then
        warn "Claude CLI is not installed — the server needs it to handle messages"
      fi
    fi
  else
    echo ""
    echo "  To start the server manually:"
    echo "    cd $PROJECT_ROOT && ./venv/bin/uvicorn server:app --host $HOST --port $PORT --ssl-keyfile $TLS_DIR/server.key --ssl-certfile $TLS_DIR/server.crt"
  fi
else
  # Linux — offer systemd
  if check_command systemctl; then
    if prompt_yn "Install as systemd service (auto-start on boot)?" "Y"; then
      echo ""
      service_file="/etc/systemd/system/conn.service"
      sudo bash -c "cat > $service_file" <<SYSTEMD
[Unit]
Description=Conn Server
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$PROJECT_ROOT
ExecStart=$VENV_DIR/bin/uvicorn server:app --host $HOST --port $PORT --ssl-keyfile $TLS_DIR/server.key --ssl-certfile $TLS_DIR/server.crt
Restart=always
RestartSec=5
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=multi-user.target
SYSTEMD

      sudo systemctl daemon-reload
      sudo systemctl enable conn
      sudo systemctl start conn
      success "Service installed and running"

      echo ""
      info "Checking server health..."
      sleep 2
      if curl -skf "https://localhost:$PORT/health" >/dev/null 2>&1; then
        success "Server is responding"
      else
        warn "Server not responding yet — it may still be starting"
        echo "    Check logs: journalctl -u conn -f"
      fi
    fi
  fi

  if [ ! -f "/etc/systemd/system/conn.service" ] 2>/dev/null; then
    echo ""
    echo "  To start the server manually:"
    echo "    cd $PROJECT_ROOT && ./venv/bin/uvicorn server:app --host $HOST --port $PORT --ssl-keyfile $TLS_DIR/server.key --ssl-certfile $TLS_DIR/server.crt"
  fi
fi

# ==========================================================
# Summary
# ==========================================================

# Get local IP
LOCAL_IP=$(python3 -c "
import socket
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(('8.8.8.8', 80))
    print(s.getsockname()[0])
    s.close()
except Exception:
    print('127.0.0.1')
")

# Detect Tailscale
CONN_IP="$LOCAL_IP"
TAILSCALE_CMD=""
if check_command tailscale; then
  TAILSCALE_CMD="tailscale"
elif [ -x "/Applications/Tailscale.app/Contents/MacOS/Tailscale" ]; then
  TAILSCALE_CMD="/Applications/Tailscale.app/Contents/MacOS/Tailscale"
fi

if [ -n "$TAILSCALE_CMD" ]; then
  TAILSCALE_IP=$("$TAILSCALE_CMD" ip -4 2>/dev/null || true)
  if [ -n "$TAILSCALE_IP" ]; then
    echo ""
    success "Tailscale detected ($TAILSCALE_IP)"
    if prompt_yn "Use Tailscale IP for the connection QR code? (allows access from anywhere)" "Y"; then
      CONN_IP="$TAILSCALE_IP"
    fi
  fi
fi

echo ""
echo "  =================================================="
echo "  ${BOLD}Setup Complete${NC}"
echo "  =================================================="
echo ""
echo "  ${BOLD}Server${NC}"
echo "  URL:        https://$CONN_IP:$PORT"
if [ "$CONN_IP" != "$LOCAL_IP" ]; then
  echo "  LAN URL:    https://$LOCAL_IP:$PORT"
fi
echo "  Auth token: $AUTH_TOKEN"
echo "  TLS:        $CERT_FINGERPRINT"
echo "  Projects:   $PROJECTS_DIR"

# QR code (includes cert for zero-trust-on-first-use setup)
# Generates both a terminal QR and an SVG file for small displays.
"$VENV_DIR/bin/python3" -c "
import json, io, sys, os
sys.path.insert(0, '$PROJECT_ROOT')
try:
    import qrcode
    from qrcode.image.svg import SvgPathImage
    from tls import get_cert_der_b64
    cert = get_cert_der_b64()
    data = json.dumps({'host': '$CONN_IP', 'port': $PORT, 'token': '$AUTH_TOKEN', 'cert': cert}, separators=(',', ':'))
    qr = qrcode.QRCode(box_size=1, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    # Terminal QR
    buf = io.StringIO()
    qr.print_ascii(out=buf, invert=True)
    print()
    for line in buf.getvalue().splitlines():
        print(f'  {line}')
    print()
    # SVG file for small terminals
    svg_path = os.path.expanduser('~/.conn/qr-code.svg')
    qr_svg = qrcode.QRCode(box_size=10, border=4)
    qr_svg.add_data(data)
    qr_svg.make(fit=True)
    img = qr_svg.make_image(image_factory=SvgPathImage)
    img.save(svg_path)
    print('  Scan the QR code above, or open the image:')
    print(f'  {svg_path}')
except ImportError:
    pass
" 2>/dev/null || true

QR_SVG="$CONFIG_DIR/qr-code.svg"
if [ -f "$QR_SVG" ]; then
  echo ""
  if prompt_yn "Open QR code image?" "Y"; then
    if [ "$OS" = "Darwin" ]; then
      open "$QR_SVG"
    elif check_command xdg-open; then
      xdg-open "$QR_SVG"
    else
      info "Open $QR_SVG in a browser to scan"
    fi
  fi
fi

echo ""
echo "  =================================================="
echo ""
