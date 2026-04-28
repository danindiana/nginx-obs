#!/usr/bin/env bash
# nginx-obs setup wizard
# Installs and configures nginx-obs on any Linux host running nginx.
# Must be run as root (or with sudo).

set -euo pipefail

# ── Colours ───────────────────────────────────────────────────────────────────
G='\033[0;32m'; C='\033[0;36m'; Y='\033[0;33m'; R='\033[0;31m'; B='\033[1;34m'
M='\033[0;35m'; W='\033[1;37m'; DIM='\033[2m'; NC='\033[0m'

hdr()  { echo -e "\n${B}══ $* ══${NC}"; }
ok()   { echo -e "  ${G}✓${NC}  $*"; }
info() { echo -e "  ${C}→${NC}  $*"; }
warn() { echo -e "  ${Y}!${NC}  $*"; }
err()  { echo -e "  ${R}✗${NC}  $*"; }
ask()  { echo -en "  ${M}?${NC}  $* "; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Root check ────────────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    echo -e "${R}Error:${NC} run with sudo: sudo $0"
    exit 1
fi

# ── Banner ────────────────────────────────────────────────────────────────────
clear
echo -e "${G}"
cat << 'BANNER'
  _   _  _____ _____ _   ___  __    ___  _
 | \ | |/ ____|_   _| \ | \ \/ /   / _ \| |
 |  \| | |  __ | | |  \| |\  /   | | | | |__  ___
 | . ` | | |_ || | | . ` |/  \   | | | | '_ \/ __|
 | |\  | |__| || |_| |\  / /\ \  | |_| | |_) \__ \
 |_| \_|\_____|_____|_| \_/_/  \_\ \___/|_.__/|___/

              Observatory  Setup Wizard
BANNER
echo -e "${NC}"
echo -e "  ${DIM}Real-time nginx telemetry dashboard — github.com/danindiana/nginx-obs${NC}"
echo

# ── Detect system ─────────────────────────────────────────────────────────────
hdr "System Detection"

OS_ID=$(grep -oP '(?<=^ID=).+' /etc/os-release 2>/dev/null | tr -d '"' || echo unknown)
info "OS: $OS_ID"

# nginx binary
NGINX_BIN=$(command -v nginx || find /usr/sbin /usr/local/sbin -name nginx 2>/dev/null | head -1 || true)
if [[ -z "$NGINX_BIN" ]]; then
    err "nginx not found in PATH or /usr/sbin"
    echo -e "  Install nginx first:  ${DIM}apt install nginx${NC}  or  ${DIM}yum install nginx${NC}"
    exit 1
fi
NGINX_VER=$("$NGINX_BIN" -v 2>&1 | grep -oP '(?<=nginx/)[\d.]+' || echo "?")
ok "nginx $NGINX_VER at $NGINX_BIN"

# stub_status
if "$NGINX_BIN" -V 2>&1 | grep -q with-http_stub_status_module; then
    ok "stub_status module present"
else
    err "nginx was not compiled with --with-http_stub_status_module"
    echo -e "  On Debian/Ubuntu:  ${DIM}sudo apt install nginx-extras${NC}"
    exit 1
fi

# Python3
PYTHON=$(command -v python3 || true)
if [[ -z "$PYTHON" ]]; then
    err "python3 not found"
    echo -e "  Install:  ${DIM}apt install python3 python3-venv${NC}"
    exit 1
fi
PY_VER=$("$PYTHON" --version 2>&1 | grep -oP '[\d.]+')
ok "python3 $PY_VER at $PYTHON"

if ! "$PYTHON" -m venv --help &>/dev/null; then
    err "python3-venv not available"
    echo -e "  Install:  ${DIM}apt install python3-venv${NC}"
    exit 1
fi
ok "python3-venv available"

# systemd
if command -v systemctl &>/dev/null && systemctl is-system-running --quiet 2>/dev/null; then
    HAS_SYSTEMD=1; ok "systemd detected"
else
    HAS_SYSTEMD=0; warn "systemd not detected — service install will be skipped"
fi

# ufw
if command -v ufw &>/dev/null; then
    HAS_UFW=1; ok "ufw detected"
else
    HAS_UFW=0; warn "ufw not found — firewall rule will be skipped"
fi

# ── Interactive config ─────────────────────────────────────────────────────────
hdr "Configuration"

# Install dir
ask "Install directory [/opt/nginx-obs]:"
read -r INSTALL_DIR; INSTALL_DIR="${INSTALL_DIR:-/opt/nginx-obs}"
info "Install dir: $INSTALL_DIR"

# Port
ask "Flask listen port [8081]:"
read -r BIND_PORT; BIND_PORT="${BIND_PORT:-8081}"
info "Port: $BIND_PORT"

# Access log
DEFAULT_LOG="/var/log/nginx/access.log"
ask "nginx access log path [$DEFAULT_LOG]:"
read -r ACCESS_LOG; ACCESS_LOG="${ACCESS_LOG:-$DEFAULT_LOG}"
if [[ ! -f "$ACCESS_LOG" ]]; then
    warn "Log file not found at $ACCESS_LOG — service will start but log tail may be empty until nginx writes to it"
fi
info "Access log: $ACCESS_LOG"

# nginx config file
NGINX_CONF_DIR="/etc/nginx"
SITES_ENABLED="$NGINX_CONF_DIR/sites-enabled/default"
CONF_D_DEFAULT="$NGINX_CONF_DIR/conf.d/default.conf"
if [[ -f "$SITES_ENABLED" ]]; then
    DEFAULT_NGINX_CONF="$SITES_ENABLED"
elif [[ -f "$CONF_D_DEFAULT" ]]; then
    DEFAULT_NGINX_CONF="$CONF_D_DEFAULT"
else
    DEFAULT_NGINX_CONF="$NGINX_CONF_DIR/sites-enabled/default"
fi
ask "nginx server block config file [$DEFAULT_NGINX_CONF]:"
read -r NGINX_SITE_CONF; NGINX_SITE_CONF="${NGINX_SITE_CONF:-$DEFAULT_NGINX_CONF}"
info "nginx config: $NGINX_SITE_CONF"

# LAN CIDR for stub_status
ask "LAN CIDR to allow /nginx_status access [192.168.1.0/24]:"
read -r LAN_CIDR; LAN_CIDR="${LAN_CIDR:-192.168.1.0/24}"
info "LAN CIDR: $LAN_CIDR"

# Run user
ask "Service user [www-data]:"
read -r SVC_USER; SVC_USER="${SVC_USER:-www-data}"
info "Service user: $SVC_USER"

# Confirm
echo
echo -e "  ${W}Summary:${NC}"
echo -e "  ${DIM}Install dir ${NC}: $INSTALL_DIR"
echo -e "  ${DIM}Port        ${NC}: $BIND_PORT"
echo -e "  ${DIM}Access log  ${NC}: $ACCESS_LOG"
echo -e "  ${DIM}nginx conf  ${NC}: $NGINX_SITE_CONF"
echo -e "  ${DIM}LAN CIDR    ${NC}: $LAN_CIDR"
echo -e "  ${DIM}Service user${NC}: $SVC_USER"
echo
ask "Proceed? [Y/n]:"
read -r CONFIRM
if [[ "${CONFIRM,,}" == "n" ]]; then
    info "Aborted."; exit 0
fi

# ── Install app ───────────────────────────────────────────────────────────────
hdr "Installing App"

mkdir -p "$INSTALL_DIR"

# Patch app.py with chosen config values, then copy
APP_SRC="$SCRIPT_DIR/app.py"
if [[ ! -f "$APP_SRC" ]]; then
    err "app.py not found at $SCRIPT_DIR — run setup.sh from the repo root"
    exit 1
fi

APP_DEST="$INSTALL_DIR/app.py"
sed \
    -e "s|^ACCESS_LOG.*|ACCESS_LOG   = \"$ACCESS_LOG\"|" \
    -e "s|^BIND_PORT.*|BIND_PORT    = $BIND_PORT|" \
    "$APP_SRC" > "$APP_DEST"
ok "app.py installed to $INSTALL_DIR"

# venv
info "Creating Python venv…"
"$PYTHON" -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --quiet flask
ok "Flask installed in $INSTALL_DIR/venv"

# permissions
chown -R "$SVC_USER":"$SVC_USER" "$INSTALL_DIR" 2>/dev/null || \
    chown -R "$SVC_USER" "$INSTALL_DIR"
chmod 755 "$INSTALL_DIR"
ok "Permissions set (owner: $SVC_USER)"

# ── nginx config ──────────────────────────────────────────────────────────────
hdr "Configuring nginx"

# Check if locations already present
if grep -q "nginx_status" "$NGINX_SITE_CONF" 2>/dev/null; then
    warn "/nginx_status location already in $NGINX_SITE_CONF — skipping (edit manually if needed)"
    SKIP_NGINX_PATCH=1
else
    SKIP_NGINX_PATCH=0
fi

if [[ $SKIP_NGINX_PATCH -eq 0 ]]; then
    # Insert before the closing brace of the first server block
    LOCATIONS=$(cat << EOF

    location /nginx_status {
        stub_status on;
        allow 127.0.0.1;
        allow ::1;
        allow $LAN_CIDR;
        deny all;
    }

    location /nginx-obs/ {
        proxy_pass http://127.0.0.1:$BIND_PORT/;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
    }
EOF
)
    # Backup original
    cp "$NGINX_SITE_CONF" "${NGINX_SITE_CONF}.nginx-obs-bak"
    info "Backed up to ${NGINX_SITE_CONF}.nginx-obs-bak"

    # Append locations before last closing brace
    python3 - "$NGINX_SITE_CONF" "$LOCATIONS" << 'PYEOF'
import sys
conf_path, locations = sys.argv[1], sys.argv[2]
with open(conf_path) as f:
    content = f.read()
# Insert before the last closing brace
idx = content.rfind('}')
if idx == -1:
    print("ERROR: no closing brace found in nginx config", file=sys.stderr)
    sys.exit(1)
new_content = content[:idx] + locations + '\n' + content[idx:]
with open(conf_path, 'w') as f:
    f.write(new_content)
PYEOF
    ok "nginx locations added to $NGINX_SITE_CONF"
fi

# Test and reload nginx
info "Testing nginx config…"
if "$NGINX_BIN" -t 2>&1; then
    if command -v systemctl &>/dev/null; then
        systemctl reload nginx
        ok "nginx reloaded"
    else
        "$NGINX_BIN" -s reload && ok "nginx reloaded"
    fi
else
    err "nginx config test failed — restoring backup"
    [[ -f "${NGINX_SITE_CONF}.nginx-obs-bak" ]] && cp "${NGINX_SITE_CONF}.nginx-obs-bak" "$NGINX_SITE_CONF"
    exit 1
fi

# ── Verify stub_status ────────────────────────────────────────────────────────
info "Verifying stub_status…"
STUB=$(curl -sf "http://127.0.0.1/nginx_status" 2>/dev/null || true)
if echo "$STUB" | grep -q "Active connections"; then
    ok "stub_status responding"
else
    warn "stub_status not responding on localhost — check nginx config"
fi

# ── Systemd service ───────────────────────────────────────────────────────────
hdr "Systemd Service"

if [[ $HAS_SYSTEMD -eq 1 ]]; then
    SERVICE_FILE="/etc/systemd/system/nginx-obs.service"
    cat > "$SERVICE_FILE" << EOF
[Unit]
Description=NGINX Observability Dashboard
After=network.target nginx.service

[Service]
Type=simple
User=$SVC_USER
Group=$SVC_USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable --now nginx-obs
    sleep 1
    if systemctl is-active --quiet nginx-obs; then
        ok "nginx-obs.service enabled and running"
    else
        err "Service failed to start — check: journalctl -u nginx-obs -n 30"
    fi
else
    warn "systemd not available — start manually:"
    echo -e "  ${DIM}sudo -u $SVC_USER $INSTALL_DIR/venv/bin/python $INSTALL_DIR/app.py &${NC}"
fi

# ── UFW ───────────────────────────────────────────────────────────────────────
hdr "Firewall"

if [[ $HAS_UFW -eq 1 ]]; then
    if ufw status | grep -q "$BIND_PORT"; then
        warn "UFW rule for port $BIND_PORT already exists — skipping"
    else
        ufw allow from "$LAN_CIDR" to any port "$BIND_PORT" proto tcp comment 'nginx-obs LAN'
        ok "UFW rule added: port $BIND_PORT from $LAN_CIDR"
    fi
else
    warn "UFW not available — ensure port $BIND_PORT is accessible on your LAN"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
hdr "Done"

SERVER_IP=$(hostname -I | awk '{print $1}')
echo
echo -e "  ${G}nginx-obs is running!${NC}"
echo
echo -e "  ${W}Dashboard (via nginx proxy):${NC}  ${C}http://$SERVER_IP/nginx-obs/${NC}"
echo -e "  ${W}Dashboard (direct port):${NC}      ${C}http://$SERVER_IP:$BIND_PORT/${NC}"
echo -e "  ${W}Raw stub_status:${NC}              ${C}http://$SERVER_IP/nginx_status${NC}"
echo
echo -e "  ${DIM}Service:  sudo systemctl status nginx-obs${NC}"
echo -e "  ${DIM}Logs:     sudo journalctl -u nginx-obs -f${NC}"
echo -e "  ${DIM}Restart:  sudo systemctl restart nginx-obs${NC}"
echo
