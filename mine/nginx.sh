#!/usr/bin/env bash
# =============================================================================
# AnyLog Dashboard — nginx setup script
# Ubuntu 20.04 / 22.04 / 24.04
#
# Installs and configures nginx as a standalone proxy for the AnyLog dashboard.
# nginx proxies /api/ directly to the AnyLog node — no Python or Flask required.
#
# NOTE: anylog_proxy.py is a separate, independent alternative to this script.
#       Use one OR the other — not both.
#
#   Flask proxy (anylog_proxy.py)     — simpler, single-machine, no system deps
#   nginx (this script)               — production, multi-user, TLS termination
#
# What this script does:
#   1. Installs nginx and openssl
#   2. Copies the dashboard HTML to the nginx webroot
#   3. Copies mTLS certs to /etc/nginx/ssl/anylog/ (if provided)
#   4. Configures nginx to:
#        - Serve the dashboard HTML as a static file
#        - Reverse-proxy  /api/  →  AnyLog node directly (no intermediate process)
#        - Present client mTLS certificates to the AnyLog node (if --cert/--key)
#        - Add CORS headers so the browser never hits CORS errors
#        - Handle OPTIONS preflight requests (204 No Content)
#        - Optionally terminate TLS on the browser-facing side (--tls flag)
#
# Architecture:
#
#   Browser  ──POST /api/──►  nginx (:80 or :443)
#                                  │  proxy_pass  (+ mTLS client cert if configured)
#                                  ▼
#                             AnyLog node  (HTTP or HTTPS)
#
# Usage:
#   # Basic — HTTP, no mTLS:
#   sudo bash setup_nginx.sh --node 24.5.219.50:32349
#
#   # With mTLS to the AnyLog node:
#   sudo bash setup_nginx.sh \
#       --node   24.5.219.50:7849 \
#       --cert   /path/to/client.crt \
#       --key    /path/to/client.key \
#       --cacert /path/to/ca.crt
#
#   # With self-signed TLS on the browser-facing side:
#   sudo bash setup_nginx.sh --node 24.5.219.50:32349 --tls
#
#   # Full options:
#   sudo bash setup_nginx.sh \
#       --node       24.5.219.50:7849 \
#       --dashboard  /path/to/dashboard-power-plant.html \
#       --cert       /path/to/client.crt \
#       --key        /path/to/client.key \
#       --cacert     /path/to/ca.crt \
#       --tls \
#       --domain     dashboard.example.com
#
# After running:
#   - Dashboard:  http(s)://<domain>/
#   - nginx logs: /var/log/nginx/anylog_{access,error}.log
#   - nginx conf: /etc/nginx/sites-available/anylog-dashboard
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults — override with flags
# ---------------------------------------------------------------------------
ANYLOG_NODE="24.5.219.50:32349"
DASHBOARD_FILE=""        # auto-detected if blank
CERT_FILE=""             # client cert for mTLS to AnyLog node (.crt)
KEY_FILE=""              # client key  for mTLS to AnyLog node (.key)
CA_FILE=""               # CA cert to verify the AnyLog node's certificate (.crt)
USE_TLS=false
DOMAIN="localhost"
NGINX_PORT_HTTP=80
NGINX_PORT_HTTPS=443
WEBROOT="/var/www/anylog"
NGINX_SSL_DIR="/etc/nginx/ssl/anylog"

# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
ok()      { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; exit 1; }
section() { echo -e "\n${BOLD}━━━  $*  ━━━${RESET}"; }

# ---------------------------------------------------------------------------
# Parse flags
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --node)       ANYLOG_NODE="$2";    shift 2 ;;
        --dashboard)  DASHBOARD_FILE="$2"; shift 2 ;;
        --cert)       CERT_FILE="$2";      shift 2 ;;
        --key)        KEY_FILE="$2";       shift 2 ;;
        --cacert)     CA_FILE="$2";        shift 2 ;;
        --tls)        USE_TLS=true;        shift   ;;
        --domain)     DOMAIN="$2";         shift 2 ;;
        --webroot)    WEBROOT="$2";        shift 2 ;;
        -h|--help)
            sed -n '/^# Usage:/,/^# ===/p' "$0" | grep -v "^# ===" | sed 's/^# //'
            exit 0 ;;
        *) error "Unknown flag: $1" ;;
    esac
done

# ---------------------------------------------------------------------------
# Must run as root
# ---------------------------------------------------------------------------
[[ $EUID -eq 0 ]] || error "Run as root: sudo bash $0 $*"

# ---------------------------------------------------------------------------
# Auto-detect dashboard file
# ---------------------------------------------------------------------------
if [[ -z "$DASHBOARD_FILE" ]]; then
    for candidate in \
        "./power-plant-dashboard.html" \
        "./anylog_dashboard.html"
    do
        if [[ -f "$candidate" ]]; then
            DASHBOARD_FILE="$(realpath "$candidate")"
            break
        fi
    done
fi
[[ -z "$DASHBOARD_FILE" ]] && warn "No dashboard HTML found — add an HTML file to $WEBROOT manually."

# ---------------------------------------------------------------------------
# Step 1 — Install nginx
# ---------------------------------------------------------------------------
section "Installing nginx"
apt-get update -qq
apt-get install -y -qq nginx openssl curl
ok "nginx $(nginx -v 2>&1 | grep -oP '[\d.]+')"

# ---------------------------------------------------------------------------
# Step 2 — Webroot and dashboard
# ---------------------------------------------------------------------------
section "Setting up webroot: $WEBROOT"
mkdir -p "$WEBROOT"

if [[ -n "$DASHBOARD_FILE" && -f "$DASHBOARD_FILE" ]]; then
    cp "$DASHBOARD_FILE" "$WEBROOT/"
    DASHBOARD_BASENAME="$(basename "$DASHBOARD_FILE")"
    ok "Dashboard → $WEBROOT/$DASHBOARD_BASENAME"
else
    DASHBOARD_BASENAME="index.html"
fi
chown -R www-data:www-data "$WEBROOT"

# ---------------------------------------------------------------------------
# Step 3 — mTLS certificates for nginx → AnyLog upstream
# ---------------------------------------------------------------------------
# nginx presents these when connecting to the AnyLog node via proxy_pass.
# They are the same client certs the Flask proxy would use via requests.get().
PROXY_SSL_BLOCK=""

if [[ -n "$CERT_FILE" && -n "$KEY_FILE" ]]; then
    section "Installing mTLS certificates"

    [[ -f "$CERT_FILE" ]] || error "--cert file not found: $CERT_FILE"
    [[ -f "$KEY_FILE"  ]] || error "--key file not found: $KEY_FILE"

    mkdir -p "$NGINX_SSL_DIR"
    chmod 700 "$NGINX_SSL_DIR"
    cp "$CERT_FILE" "$NGINX_SSL_DIR/client.crt"
    cp "$KEY_FILE"  "$NGINX_SSL_DIR/client.key"
    chmod 600 "$NGINX_SSL_DIR/client.key"
    chown -R root:www-data "$NGINX_SSL_DIR"
    ok "Client cert → $NGINX_SSL_DIR/client.crt"
    ok "Client key  → $NGINX_SSL_DIR/client.key"

    PROXY_SSL_BLOCK="
        # mTLS: present client certificate to the AnyLog node
        proxy_ssl_certificate        ${NGINX_SSL_DIR}/client.crt;
        proxy_ssl_certificate_key    ${NGINX_SSL_DIR}/client.key;"

    if [[ -n "$CA_FILE" ]]; then
        [[ -f "$CA_FILE" ]] || error "--cacert file not found: $CA_FILE"
        cp "$CA_FILE" "$NGINX_SSL_DIR/ca.crt"
        ok "CA cert     → $NGINX_SSL_DIR/ca.crt"
        PROXY_SSL_BLOCK+="
        proxy_ssl_trusted_certificate ${NGINX_SSL_DIR}/ca.crt;
        proxy_ssl_verify              on;
        proxy_ssl_verify_depth        2;"
    else
        PROXY_SSL_BLOCK+="
        proxy_ssl_verify              off;"
    fi
    PROXY_SSL_BLOCK+="
        proxy_ssl_session_reuse       on;"

    # Node must be reached over HTTPS when using mTLS
    [[ "$ANYLOG_NODE" != https://* ]] && ANYLOG_NODE="https://${ANYLOG_NODE#http://}"
else
    # No mTLS — plain HTTP to the node
    [[ "$ANYLOG_NODE" != http://* && "$ANYLOG_NODE" != https://* ]] \
        && ANYLOG_NODE="http://${ANYLOG_NODE}"
fi

# ---------------------------------------------------------------------------
# Step 4 — Self-signed TLS for browser → nginx (if --tls)
# ---------------------------------------------------------------------------
TLS_CERT="/etc/nginx/ssl/anylog-selfsigned.crt"
TLS_KEY="/etc/nginx/ssl/anylog-selfsigned.key"

if $USE_TLS; then
    section "Generating self-signed TLS certificate"
    mkdir -p /etc/nginx/ssl
    if [[ ! -f "$TLS_CERT" ]]; then
        openssl req -x509 -nodes -days 730 \
            -newkey rsa:2048 \
            -keyout "$TLS_KEY" \
            -out "$TLS_CERT" \
            -subj "/CN=${DOMAIN}/O=AnyLog/C=US" \
            -addext "subjectAltName=DNS:${DOMAIN},IP:127.0.0.1" \
            2>/dev/null
        chmod 600 "$TLS_KEY"
        ok "Self-signed cert → $TLS_CERT"
    else
        ok "Self-signed cert already exists, skipping"
    fi
fi

# ---------------------------------------------------------------------------
# Step 5 — nginx configuration
# ---------------------------------------------------------------------------
section "Writing nginx configuration"

NGINX_CONF="/etc/nginx/sites-available/anylog-dashboard"
mkdir -p "$WEBROOT"

# Reusable location block — same content for HTTP and HTTPS server blocks
# /api/ is the path the dashboard POSTs to; nginx strips it and forwards
# the request body directly to the AnyLog node root (/).
API_LOCATION="
    # ── Proxy /api/ → AnyLog node ────────────────────────────────────
    # Browser POSTs JSON to /api/; nginx forwards directly to AnyLog.
    # No intermediate process — nginx handles mTLS client certs natively.
    location /api/ {
        rewrite ^/api(/.*)? /\$1 break;

        proxy_pass         ${ANYLOG_NODE};
        proxy_http_version 1.1;
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
        proxy_connect_timeout 10s;
${PROXY_SSL_BLOCK}

        # CORS — allow browser dashboard to call /api/ without restriction
        add_header Access-Control-Allow-Origin  \"*\" always;
        add_header Access-Control-Allow-Methods \"GET, POST, OPTIONS\" always;
        add_header Access-Control-Allow-Headers \"Content-Type, User-Agent, command, destination\" always;

        # Respond to browser OPTIONS preflight immediately — no upstream call
        if (\$request_method = OPTIONS) {
            add_header Access-Control-Allow-Origin  \"*\";
            add_header Access-Control-Allow-Methods \"GET, POST, OPTIONS\";
            add_header Access-Control-Allow-Headers \"Content-Type, User-Agent, command, destination\";
            add_header Content-Length 0;
            return 204;
        }
    }"

# ---------- HTTP block (always present) ----------
HTTP_SERVER_BLOCK="
server {
    listen ${NGINX_PORT_HTTP};
    server_name ${DOMAIN};
$(if $USE_TLS; then echo "
    # Redirect all HTTP → HTTPS
    return 301 https://\$host\$request_uri;
"; else echo "
    root ${WEBROOT};
    index ${DASHBOARD_BASENAME};

    # ── Static dashboard ─────────────────────────────────────────
    location / {
        try_files \$uri \$uri/ =404;
        add_header Cache-Control \"no-store\";
    }
${API_LOCATION}
"; fi)
}
"

# ---------- HTTPS block (only if --tls) ----------
HTTPS_SERVER_BLOCK=""
if $USE_TLS; then
    HTTPS_SERVER_BLOCK="
server {
    listen ${NGINX_PORT_HTTPS} ssl;
    server_name ${DOMAIN};

    ssl_certificate     ${TLS_CERT};
    ssl_certificate_key ${TLS_KEY};
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;
    ssl_session_cache   shared:SSL:10m;
    ssl_session_timeout 10m;

    root ${WEBROOT};
    index ${DASHBOARD_BASENAME};

    location / {
        try_files \$uri \$uri/ =404;
        add_header Cache-Control \"no-store\";
    }
${API_LOCATION}

    access_log /var/log/nginx/anylog_access.log;
    error_log  /var/log/nginx/anylog_error.log;
}
"
fi

# Write final config
echo "${HTTP_SERVER_BLOCK}${HTTPS_SERVER_BLOCK}" > "$NGINX_CONF"

# Enable site, disable default
ln -sf "$NGINX_CONF" /etc/nginx/sites-enabled/anylog-dashboard
rm -f /etc/nginx/sites-enabled/default

# Test and reload
nginx -t 2>/dev/null && ok "nginx config valid" || error "nginx config test failed — check $NGINX_CONF"
systemctl reload nginx
ok "nginx reloaded"

# ---------------------------------------------------------------------------
# Step 6 — Dashboard connection mode note
# ---------------------------------------------------------------------------
section "Dashboard configuration"
SCHEME="http"; $USE_TLS && SCHEME="https"

cat << NOTE

  Open the dashboard, click ⚙ Settings and configure:
    Mode:       nginx
    nginx URL:  ${SCHEME}://${DOMAIN}
    Node URL:   ${ANYLOG_NODE}

  Or set these as defaults in the HTML file (CONN object):
    mode:      'nginx'
    nginxUrl:  '${SCHEME}://${DOMAIN}'
    nodeUrl:   '${ANYLOG_NODE}'

NOTE

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
section "Setup complete"

echo -e "
  ${BOLD}Dashboard${RESET}      ${SCHEME}://${DOMAIN}/
  ${BOLD}API proxy${RESET}      ${SCHEME}://${DOMAIN}/api/  →  ${ANYLOG_NODE}
  ${BOLD}mTLS certs${RESET}     ${NGINX_SSL_DIR:-not configured}

  ${BOLD}Logs${RESET}
    nginx:  tail -f /var/log/nginx/anylog_error.log

  ${BOLD}Service control${RESET}
    systemctl reload nginx
    systemctl status nginx

  ${BOLD}Files${RESET}
    Dashboard:   ${WEBROOT}/
    nginx conf:  ${NGINX_CONF}
    TLS certs:   /etc/nginx/ssl/
"

if $USE_TLS; then
    warn "Using self-signed TLS — browser will show a security warning."
    warn "Accept the warning once, or replace ${TLS_CERT} with a real cert."
fi