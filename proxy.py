"""
AnyLog Dashboard Proxy
======================
A lightweight Flask server that:
  - Holds the mTLS certificates (cert, key, CA)
  - Proxies REST requests from the browser dashboard to AnyLog nodes
  - Handles both SQL queries and blockchain/status commands correctly
  - Returns responses to the browser preserving the original content type

Usage:
    pip install flask flask-cors requests

    python anylog_proxy.py \
        --cert   /path/to/server-acme-inc-public-key.crt \
        --key    /path/to/server-acme-inc-private-key.key \
        --cacert /path/to/ca-anylog-public-key.crt \
        --node   24.5.219.50:32349 \
        --port   5000

    # Without mTLS (plain HTTP to the node):
    python anylog_proxy.py --node 24.5.219.50:32349

Then point the dashboard at: http://localhost:5000
Or open the dashboard directly: http://localhost:5000/dashboard

Dashboard <-> Proxy protocol
-----------------------------
The browser always POSTs to /api/query with a JSON body. The proxy extracts
the AnyLog command from the body, forwards it to the node as a GET request
with the command in HTTP headers (the format AnyLog natively accepts), and
returns the response to the browser.

POST /api/query
Body (from dashboard):
  {
    "command":     "sql cos format=json SELECT ...",  # required
    "User-Agent":  "AnyLog/1.23",                     # optional override
    "destination": "network",                          # SQL queries only
    "url":         "https://10.0.0.78:7849"           # optional node override
  }

Keys forwarded as AnyLog headers:  command, User-Agent, destination
Keys consumed by the proxy:        url  (not forwarded to AnyLog)
"""

import os
import sys
import argparse

import requests
import urllib3
from flask import Flask, Response, jsonify, request, send_from_directory
from flask_cors import CORS

# ---------------------------------------------------------------------------
# Suppress InsecureRequestWarning globally when cert verification is off
# ---------------------------------------------------------------------------
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)
CORS(app)  # Allow requests from any origin (browser opened from file://, IDE, etc.)

# ---------------------------------------------------------------------------
# Runtime-configurable state (set by CLI args; hot-reloadable via /api/configure)
# ---------------------------------------------------------------------------
CERT_FILE     = None   # client public cert  (.crt)
KEY_FILE      = None   # client private key  (.key)
CA_FILE       = None   # CA cert for server verification — None = skip verification
TIMEOUT       = 30     # seconds
DEST_NODE_URL = None   # default AnyLog node URL (http:// or https://)
DASHBOARD_FILE = "dashboard-power-plant.html"  # file served at GET /dashboard

# Headers that are proxy-internal and must NOT be forwarded to AnyLog
_PROXY_KEYS = {"url", "content-type", "content_type"}


# ---------------------------------------------------------------------------
# Helper: build the AnyLog node URL from a raw host:port string
# ---------------------------------------------------------------------------
def _node_url(raw: str) -> str:
    raw = raw.strip().rstrip("/")
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    # Default to http:// — caller uses https:// explicitly when needed
    return f"http://{raw}"


# ---------------------------------------------------------------------------
# Helper: forward a command to AnyLog and return the raw requests.Response
# ---------------------------------------------------------------------------
def _forward(command: str, node_url: str, extra_headers: dict) -> requests.Response:
    headers = {
        "User-Agent":  "AnyLog/1.23",
        "Accept":      "text/plain",   # AnyLog returns plain text or JSON
        "command":     command,
    }
    # Merge any extra headers from the request body (e.g. destination: network)
    for k, v in extra_headers.items():
        if k.lower() not in _PROXY_KEYS:
            headers[k] = str(v)

    cert   = (CERT_FILE, KEY_FILE) if CERT_FILE and KEY_FILE else None
    verify = CA_FILE if CA_FILE else False

    return requests.get(
        node_url,
        headers=headers,
        cert=cert,
        verify=verify,
        timeout=TIMEOUT,
    )


# ---------------------------------------------------------------------------
# Serve the dashboard HTML
#   GET /           → redirect hint
#   GET /dashboard  → serve DASHBOARD_FILE from the current directory
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return (
        f'<meta http-equiv="refresh" content="0; url=/dashboard">'
        f'<p>Redirecting to <a href="/dashboard">/dashboard</a></p>'
    )


@app.route("/dashboard")
def dashboard():
    directory = os.path.dirname(os.path.abspath(__file__))
    return send_from_directory(directory, DASHBOARD_FILE)


# ---------------------------------------------------------------------------
# Main proxy endpoint
#   POST /api/query
#   Body: { "command": "...", "url": "...", ...extra headers... }
# ---------------------------------------------------------------------------
@app.route("/api/query", methods=["POST"])
def proxy_query():
    body = request.get_json(silent=True) or {}

    # Resolve target node: body["url"] > global default
    raw_url = body.get("url", "").strip()
    node_url = _node_url(raw_url) if raw_url else DEST_NODE_URL

    if not node_url:
        return jsonify({"error": "No AnyLog node configured. Provide 'url' in the request body or start the proxy with --node."}), 400

    command = body.get("command", "").strip()
    if not command:
        return jsonify({"error": "Missing 'command' in request body."}), 400

    # Extra headers to forward (everything except proxy-internal keys and command/url)
    extra = {k: v for k, v in body.items() if k.lower() not in {"command", "url"}}

    try:
        resp = _forward(command, node_url, extra)
        resp.raise_for_status()
    except requests.exceptions.SSLError as e:
        return jsonify({"error": f"SSL/TLS error: {e}"}), 502
    except requests.exceptions.ConnectionError as e:
        return jsonify({"error": f"Connection error: {e}"}), 502
    except requests.exceptions.Timeout:
        return jsonify({"error": f"Request timed out after {TIMEOUT}s"}), 504
    except requests.exceptions.HTTPError as e:
        return jsonify({"error": f"HTTP {resp.status_code}: {resp.text[:200]}"}), resp.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    # -----------------------------------------------------------------------
    # Return the response preserving its content type.
    #
    # AnyLog can return:
    #   - JSON array/object  → Content-Type: application/json
    #   - Plain text string  → e.g. "39.9049, -95.8034" for bring [*][loc]
    #
    # The dashboard's parseAnyLogResponse() handles both: it tries JSON.parse()
    # first, then falls back to treating the response as raw text. So we must
    # NOT wrap plain-text responses in {"raw": ...} — return them as-is.
    # -----------------------------------------------------------------------
    content_type = resp.headers.get("Content-Type", "text/plain")

    # Attempt JSON parse to normalise content-type for the browser
    try:
        data = resp.json()
        return jsonify(data)
    except ValueError:
        # Plain text (e.g. bring [*][loc] coordinate string) — return as-is
        return Response(resp.text, status=200, mimetype="text/plain")


# ---------------------------------------------------------------------------
# Hot-reload cert configuration
#   POST /api/configure
#   Body: { "cert": "/path/...", "key": "/path/...", "cacert": "/path/..." }
#   Called by the dashboard settings drawer when cert paths are updated.
# ---------------------------------------------------------------------------
@app.route("/api/configure", methods=["POST"])
def configure():
    global CERT_FILE, KEY_FILE, CA_FILE

    body = request.get_json(silent=True) or {}
    errors = []

    cert   = body.get("cert",   "").strip() or None
    key    = body.get("key",    "").strip() or None
    cacert = body.get("cacert", "").strip() or None

    for label, path in [("cert", cert), ("key", key), ("cacert", cacert)]:
        if path and not os.path.isfile(path):
            errors.append(f"{label}: file not found: {path}")

    if errors:
        return jsonify({"error": "Invalid paths", "details": errors}), 400

    if cert:   CERT_FILE = cert
    if key:    KEY_FILE  = key
    if cacert: CA_FILE   = cacert

    print(f"[configure] Certs updated: cert={CERT_FILE} key={KEY_FILE} ca={CA_FILE}")
    return jsonify({
        "status":  "ok",
        "cert":    CERT_FILE  or "not configured",
        "key":     KEY_FILE   or "not configured",
        "cacert":  CA_FILE    or "not configured (server cert verification disabled)",
    })


# ---------------------------------------------------------------------------
# Health check — also shows active cert config and default node
# ---------------------------------------------------------------------------
@app.route("/api/health")
def health():
    return jsonify({
        "status":       "ok",
        "node":         DEST_NODE_URL or "not configured (pass url in each request)",
        "cert":         CERT_FILE     or "not configured",
        "key":          KEY_FILE      or "not configured",
        "cacert":       CA_FILE       or "not configured (server cert verification disabled)",
        "dashboard":    DASHBOARD_FILE,
        "timeout":      TIMEOUT,
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    global CERT_FILE, KEY_FILE, CA_FILE, TIMEOUT, DEST_NODE_URL, DASHBOARD_FILE

    parser = argparse.ArgumentParser(
        description="AnyLog Dashboard Proxy",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # No mTLS, plain HTTP node:
  python anylog_proxy.py --node 24.5.219.50:32349

  # mTLS, HTTPS node:
  python anylog_proxy.py \\
      --node   24.5.219.50:7849 \\
      --cert   /path/to/client.crt \\
      --key    /path/to/client.key \\
      --cacert /path/to/ca.crt

  # Listen on all interfaces (e.g. for a VM):
  python anylog_proxy.py --node 24.5.219.50:32349 --host 0.0.0.0
""")

    parser.add_argument("--node",
                        default=os.environ.get("ANYLOG_NODE"),
                        type=str,                          # was type=int — fixed
                        help="Default AnyLog node (host:port or full URL). "
                             "Can be overridden per-request via the 'url' body key.")
    parser.add_argument("--cert",
                        default=os.environ.get("ANYLOG_CERT"),
                        help="Path to client certificate (.crt) for mTLS")
    parser.add_argument("--key",
                        default=os.environ.get("ANYLOG_KEY"),
                        help="Path to client private key (.key) for mTLS")
    parser.add_argument("--cacert",
                        default=os.environ.get("ANYLOG_CACERT"),
                        help="Path to CA certificate (.crt) — omit to skip server verification")
    parser.add_argument("--port",
                        type=int,
                        default=int(os.environ.get("PROXY_PORT", 5000)),
                        help="Port for this proxy server (default: 5000)")
    parser.add_argument("--host",
                        default=os.environ.get("PROXY_HOST", "127.0.0.1"),
                        help="Interface to bind to (default: 127.0.0.1; use 0.0.0.0 for all interfaces)")
    parser.add_argument("--timeout",
                        type=int,
                        default=30,
                        help="Request timeout in seconds (default: 30)")
    parser.add_argument("--dashboard",
                        default=os.environ.get("ANYLOG_DASHBOARD", "dashboard-power-plant.html"),
                        help="HTML file to serve at GET /dashboard (default: dashboard-power-plant.html)")

    args = parser.parse_args()

    # Apply args to globals
    if args.node:    DEST_NODE_URL  = _node_url(args.node)
    if args.cert:    CERT_FILE      = args.cert
    if args.key:     KEY_FILE       = args.key
    if args.cacert:  CA_FILE        = args.cacert
    if args.timeout: TIMEOUT        = args.timeout
    DASHBOARD_FILE = args.dashboard

    # Validate cert files
    for label, path in [("--cert", CERT_FILE), ("--key", KEY_FILE), ("--cacert", CA_FILE)]:
        if path and not os.path.isfile(path):
            print(f"ERROR: {label} file not found: {path}", file=sys.stderr)
            sys.exit(1)

    # Validate dashboard file
    if not os.path.isfile(DASHBOARD_FILE):
        print(f"WARNING: dashboard file not found: {DASHBOARD_FILE} "
              f"(GET /dashboard will return 404 until the file is placed here)")

    # Startup summary
    print("\nAnyLog Dashboard Proxy")
    print("=" * 40)
    print(f"  Proxy URL   : http://{args.host}:{args.port}")
    print(f"  Dashboard   : http://{args.host}:{args.port}/dashboard  →  {DASHBOARD_FILE}")
    print(f"  Default node: {DEST_NODE_URL or '(none — provide url in each request)'}")
    if CERT_FILE and KEY_FILE:
        print(f"  Client cert : {CERT_FILE}")
        print(f"  Client key  : {KEY_FILE}")
        print(f"  CA cert     : {CA_FILE or '(skipping server verification)'}")
    else:
        print("  mTLS        : not configured (connecting without client certificate)")
    print()

    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()