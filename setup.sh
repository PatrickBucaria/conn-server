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
DEFAULT_PORT=8080
DEFAULT_HOST="0.0.0.0"

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
echo "  =================================================="
echo "  ${BOLD}Conn Server Setup${NC}"
echo "  =================================================="
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

if check_command python3; then
  py_ver=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
  py_major=$(echo "$py_ver" | cut -d. -f1)
  py_minor=$(echo "$py_ver" | cut -d. -f2)

  if [ "$py_major" -ge 3 ] && [ "$py_minor" -ge 9 ]; then
    success "Python $py_ver found"
  else
    warn "Python $py_ver found but 3.9+ is required"
  fi
else
  fail "Python 3 not found"
fi

if ! check_command python3 || { [ "$py_major" -le 3 ] && [ "$py_minor" -lt 9 ]; }; then
  if [ "$OS" = "Darwin" ] && check_command brew; then
    if prompt_yn "Install Python 3.12 via Homebrew?" "Y"; then
      brew install python@3.12
      success "Python installed"
    fi
  else
    echo ""
    echo "  Install Python 3.9+:"
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
if [ ! -d "$VENV_DIR" ]; then
  python3 -m venv "$VENV_DIR"
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
    if curl -sf "http://localhost:$PORT/health" >/dev/null 2>&1; then
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
    echo "    cd $PROJECT_ROOT && ./venv/bin/uvicorn server:app --host $HOST --port $PORT"
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
ExecStart=$VENV_DIR/bin/uvicorn server:app --host $HOST --port $PORT
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
      if curl -sf "http://localhost:$PORT/health" >/dev/null 2>&1; then
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
    echo "    cd $PROJECT_ROOT && ./venv/bin/uvicorn server:app --host $HOST --port $PORT"
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
echo "  URL:        http://$CONN_IP:$PORT"
if [ "$CONN_IP" != "$LOCAL_IP" ]; then
  echo "  LAN URL:    http://$LOCAL_IP:$PORT"
fi
echo "  Auth token: $AUTH_TOKEN"
echo "  Projects:   $PROJECTS_DIR"

# QR code
"$VENV_DIR/bin/python3" -c "
import json, io
try:
    import qrcode
    data = json.dumps({'host': '$CONN_IP', 'port': $PORT, 'token': '$AUTH_TOKEN'})
    qr = qrcode.QRCode(box_size=1, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    buf = io.StringIO()
    qr.print_ascii(out=buf, invert=True)
    print()
    for line in buf.getvalue().splitlines():
        print(f'  {line}')
    print()
    print('  Scan this QR code with the Conn app to connect.')
except ImportError:
    pass
" 2>/dev/null || true

echo ""
echo "  =================================================="
echo ""
