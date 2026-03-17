#!/usr/bin/env bash
# =============================================================================
# start_bridge.sh  —  MCP Web Bridge v4.0 launcher
# =============================================================================
#
# USAGE
# -----
#   ./start_bridge.sh [OPTIONS]
#
# All options are passed directly to mcp_web_bridge.py.
#
# KEY OPTIONS
# -----------
#   --mcp-url  <url>     MCP SSE server URL  (overrides BRIDGE_MCP_URL)
#   --mcp-proxy <path>   mcp-proxy binary    (overrides BRIDGE_MCP_PROXY)
#   --port     <n>       HTTP port           (default 8080)
#   --host     <addr>    bind interface      (default 0.0.0.0)
#   --call-delay <s>     pause between MCP calls (default 1.5)
#   --debug              enable Flask debug + verbose logging
#
# ENVIRONMENT OVERRIDES  (used when no CLI arg is supplied)
# ----------------------------------------------------------
#   BRIDGE_MCP_URL       MCP SSE server URL
#   BRIDGE_MCP_PROXY     path to mcp-proxy binary
#   BRIDGE_PORT          HTTP listen port
#   BRIDGE_HOST          bind interface
#   BRIDGE_CALL_DELAY    seconds between MCP calls
#   BRIDGE_VENV          path to Python venv (activates it automatically)
#
# EXAMPLES
# --------
#   # Timbergrove
#   ./start_bridge.sh --mcp-url https://172.79.89.206:32049/mcp/sse --port 8080
#
#   # AnyLog Prove-IT
#   ./start_bridge.sh --mcp-url https://50.116.13.109:32049/mcp/sse --port 8081
#
#   # Dynics Prove-IT
#   ./start_bridge.sh --mcp-url https://172.79.89.206:32049/mcp/sse
#
#   # Use env var instead of CLI
#   BRIDGE_MCP_URL=https://172.79.89.206:32049/mcp/sse ./start_bridge.sh --port 9000
#
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve script directory so relative paths work regardless of cwd
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRIDGE_SCRIPT="${SCRIPT_DIR}/mcp_web_bridge.py"

# ---------------------------------------------------------------------------
# Defaults (can be overridden by env vars)
# ---------------------------------------------------------------------------
: "${BRIDGE_MCP_URL:=http://50.116.13.109:32049/mcp/sse}"
: "${BRIDGE_MCP_PROXY:=./venv/bin/mcp-proxy}"
: "${BRIDGE_PORT:=8080}"
: "${BRIDGE_HOST:=0.0.0.0}"
: "${BRIDGE_CALL_DELAY:=1.5}"
: "${BRIDGE_VENV:=}"

# ---------------------------------------------------------------------------
# Activate virtualenv if BRIDGE_VENV is set or a local venv exists
# ---------------------------------------------------------------------------
if [[ -n "${BRIDGE_VENV}" && -f "${BRIDGE_VENV}/bin/activate" ]]; then
    echo "[bridge] Activating venv: ${BRIDGE_VENV}"
    # shellcheck disable=SC1090
    source "${BRIDGE_VENV}/bin/activate"
elif [[ -f "${SCRIPT_DIR}/venv/bin/activate" ]]; then
    echo "[bridge] Activating local venv: ${SCRIPT_DIR}/venv"
    # shellcheck disable=SC1090
    source "${SCRIPT_DIR}/venv/bin/activate"
fi

# ---------------------------------------------------------------------------
# Verify python and bridge script exist
# ---------------------------------------------------------------------------
if ! command -v python3 &>/dev/null; then
    echo "[bridge] ERROR: python3 not found in PATH" >&2
    exit 1
fi

if [[ ! -f "${BRIDGE_SCRIPT}" ]]; then
    echo "[bridge] ERROR: bridge script not found: ${BRIDGE_SCRIPT}" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Build default CLI args from env vars.
# Any explicit CLI args passed to this script OVERRIDE the env defaults.
# ---------------------------------------------------------------------------
DEFAULT_ARGS=(
    "--mcp-url"    "${BRIDGE_MCP_URL}"
    "--mcp-proxy"  "${BRIDGE_MCP_PROXY}"
    "--port"       "${BRIDGE_PORT}"
    "--host"       "${BRIDGE_HOST}"
    "--call-delay" "${BRIDGE_CALL_DELAY}"
)

# ---------------------------------------------------------------------------
# Parse CLI args to detect overrides so we don't double-pass
# Already-specified flags in $@ take precedence — we filter duplicates.
# ---------------------------------------------------------------------------
PASSTHROUGH=("${@:+$@}")

has_flag() {
    local flag="$1"
    for arg in "${PASSTHROUGH[@]:+${PASSTHROUGH[@]}}"; do
        [[ "$arg" == "$flag" ]] && return 0
    done
    return 1
}

FINAL_ARGS=()
# Add defaults only if the caller didn't already supply the flag
i=0
while [[ $i -lt ${#DEFAULT_ARGS[@]} ]]; do
    flag="${DEFAULT_ARGS[$i]}"
    val="${DEFAULT_ARGS[$((i+1))]}"
    if ! has_flag "${flag}"; then
        FINAL_ARGS+=("${flag}" "${val}")
    fi
    i=$((i+2))
done

# FIX 3: guard the append with a length check so set -u never sees an
# expansion of an empty array.
[[ ${#PASSTHROUGH[@]} -gt 0 ]] && FINAL_ARGS+=("${PASSTHROUGH[@]}")

# ---------------------------------------------------------------------------
# Print banner
# ---------------------------------------------------------------------------
echo "============================================================"
echo " MCP Web Bridge v4.0"
echo "============================================================"

# Show effective values
mcp_url="${BRIDGE_MCP_URL}"
port="${BRIDGE_PORT}"
for (( i=0; i<${#FINAL_ARGS[@]}-1; i++ )); do
    case "${FINAL_ARGS[$i]}" in
        --mcp-url) mcp_url="${FINAL_ARGS[$((i+1))]}" ;;
        --port)    port="${FINAL_ARGS[$((i+1))]}" ;;
    esac
done

echo " MCP URL : ${mcp_url}"
echo " Port    : ${port}"
echo " Script  : ${BRIDGE_SCRIPT}"
echo "============================================================"
echo ""

# Install Python deps
echo "[mcp_web_proxy] Installing Python dependencies..."
pip install -r requirements.txt 

# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------
exec python3 "${BRIDGE_SCRIPT}" "${FINAL_ARGS[@]}"
