#!/usr/bin/env python3
"""
anylog_rest_proxy.py
====================
REST proxy for the Enterprise B rest Dashboard.

Per README_REST.md the AnyLog node is reached with:

    GET http://<ip>:<port>
    User-Agent:   AnyLog/1.23
    Content-Type: application/json
    command:      <anylog_command>
    destination:  network          <-- SQL queries only

The command is always in the header; the body is empty.
SQL command syntax:  sql <dbms> <sql>

Endpoints exposed to the dashboard:

    POST /api/query              { dbms, sql }
    POST /api/query/increment    { dbms, table, timeColumn,
                                   startTime, endTime,
                                   timeUnit, intervalLength, projections }
    POST /api/command            { command [, timeout] }
    GET  /api/databases
    GET  /api/databases/<dbms>/tables
    GET  /api/databases/<dbms>/tables/<table>/columns
    GET  /api/nodes
    GET  /api/nodes/status[?node=<ip:port>]
    POST /api/nodes/status       { node }
    GET  /api/data/location[?dbms=x&table=y]
    GET  /api/connection/status
    POST /api/connection/test
    GET  /health
    GET  /stats

Usage
-----
    pip install flask flask-cors requests
    python anylog_rest_proxy.py                                      # :8080
    python anylog_rest_proxy.py --port 5050
    python anylog_rest_proxy.py --anylog-ip 172.79.89.206 --anylog-port 32049
    python anylog_rest_proxy.py --debug

Set the Dashboard "Proxy URL" to:
    http://localhost:8080    (replace 8080 with your --port value)
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

import re

import requests
import urllib3
from flask import Flask, jsonify, request
from flask_cors import CORS

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("anylog_proxy.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("anylog-proxy")

# ---------------------------------------------------------------------------
# Flask + CORS
# ---------------------------------------------------------------------------
app = Flask(__name__)

# IMPORTANT: use simple CORS(app), NOT CORS(app, resources={...}).
# The resources={} form only fires after Flask routing resolves the endpoint.
# That means an OPTIONS preflight reaches the router first, finds no handler,
# and returns 404 — before CORS can respond.
# The simple form registers an after_request hook at Werkzeug level and
# therefore intercepts OPTIONS before routing.
CORS(
    app,
    origins="*",
    allow_headers=["Content-Type", "Authorization", "Accept"],
    methods=["GET", "POST", "OPTIONS"],
    max_age=86400,
)

# ---------------------------------------------------------------------------
# Runtime config  (populated by argparse / env-vars in main())
# ---------------------------------------------------------------------------
CFG = {
    "anylog_ip":   "172.79.89.206",
    "anylog_port": 32049,
    "timeout":     60.0,
}

# ---------------------------------------------------------------------------
# Stats counters
# ---------------------------------------------------------------------------
_stats = {
    "proxy_calls":   0,
    "anylog_calls":  0,
    "total_rows":    0,
    "errors":        0,
    "start_time":    time.time(),
    "_latencies":    [],   # rolling last-50 for average
}

_SKIP_PATHS = {"/favicon.ico", "/health", "/stats"}


# ---------------------------------------------------------------------------
# Core AnyLog HTTP helper
# ---------------------------------------------------------------------------
class AnyLogError(Exception):
    """Wraps an AnyLog-level error returned inside a broken chunked response."""
    def __init__(self, err_code: int, err_text: str, raw: str):
        self.err_code = err_code
        self.err_text = err_text
        self.raw      = raw
        super().__init__(f"AnyLog err {err_code}: {err_text}")



def _extract_anylog_error(exc_str: str) -> dict | None:
    """
    AnyLog sometimes embeds a JSON error payload inside the exception string
    raised by urllib3/requests when chunked transfer decoding fails.

    We locate and parse the *first valid JSON object* found anywhere in the
    exception text using JSONDecoder.raw_decode (more robust than regex when
    the payload contains nested braces inside strings).
    """
    if not exc_str:
        return None
    dec = json.JSONDecoder()
    # Try every '{' as a potential start of a JSON object
    for i, ch in enumerate(exc_str):
        if ch != "{":
            continue
        try:
            obj, _ = dec.raw_decode(exc_str[i:])
            if isinstance(obj, dict) and ("err_code" in obj or "err_text" in obj):
                return obj
        except Exception:
            continue
    return None



def anylog_get(command: str, sql_query: bool = False) -> str:
    """
    One AnyLog REST call, exactly as README_REST.md specifies:

        GET http://<ip>:<port>
        User-Agent:   AnyLog/1.23
        Content-Type: application/json
        command:      <command>
        destination:  network   (only when sql_query=True)
        Body: empty

    Important implementation notes
    --------------------------------
    * stream=False  — Forces requests to read the entire response body before
      returning, bypassing chunked-transfer-encoding parsing.  AnyLog sometimes
      returns its error payload (JSON) where the chunk-length byte should be,
      which corrupts HTTP framing and causes urllib3 to raise
      InvalidChunkLength.  With stream=False the body is buffered internally
      and this path is avoided for normal responses.

    * InvalidChunkLength / ProtocolError catch  — When AnyLog *does* send a
      malformed chunked error (timeout, SQL failure, etc.) the JSON error
      payload is embedded in the exception message itself.  We extract it,
      parse it, and raise a clean AnyLogError so callers can handle it
      properly instead of crashing with an unhandled ProtocolError.
    """
    url = f"http://{CFG['anylog_ip']}:{CFG['anylog_port']}"
    headers = {
        "User-Agent":   "AnyLog/1.23",
        "Content-Type": "application/json",
        "command":      command,
    }
    if sql_query:
        headers["destination"] = "network"

    log.info("[AL]  command: %s", command[:160])
    _stats["anylog_calls"] += 1

    t0 = time.perf_counter()
    try:
        resp = requests.get(
            url,
            headers=headers,
            timeout=CFG["timeout"],
            stream=False,          # ← read full body; avoids chunked-parse crash
        )
        ms = round((time.perf_counter() - t0) * 1000)
        _stats["_latencies"].append(ms)
        if len(_stats["_latencies"]) > 50:
            _stats["_latencies"].pop(0)

        log.info("[AL]  %d ms | HTTP %d | %d bytes", ms, resp.status_code, len(resp.text))
        resp.raise_for_status()
        return resp.text

    except Exception as exc:
        # AnyLog sends its JSON error payload where the HTTP chunk-length byte
        # should be.  This corrupts HTTP framing and surfaces as one of several
        # exception types depending on the requests/urllib3 version and whether
        # stream=False managed to buffer before the error hit.  Catch all and
        # check whether it looks like a chunked-encoding / AnyLog protocol error.
        ms  = round((time.perf_counter() - t0) * 1000)
        raw = str(exc)

        is_chunked = (
            isinstance(exc, (
                requests.exceptions.ChunkedEncodingError,
                urllib3.exceptions.ProtocolError,
                urllib3.exceptions.InvalidChunkLength,
            ))
            # fallback: match by string for wrapped/nested exception types
            or "InvalidChunkLength" in raw
            or "ProtocolError" in raw
            or "ChunkedEncodingError" in raw
            or "err_code" in raw        # AnyLog JSON payload present
        )

        if is_chunked:
            log.warning("[AL]  %d ms | chunked-encoding / AnyLog protocol error", ms)
            log.debug("[AL]  raw exception: %s", raw[:400])
            info = _extract_anylog_error(raw)
            if info:
                code = info.get("err_code", -1)
                text = info.get("err_text", "Unknown")
                log.warning("[AL]  AnyLog error %d: %s", code, text)
                raise AnyLogError(code, text, raw) from exc
            # Chunked error but no JSON payload — raise generic AnyLogError
            raise AnyLogError(-1, "Malformed chunked response", raw) from exc

        # Not a chunked error — re-raise as-is (ConnectionError, Timeout, etc.)
        raise


def _parse_rows(raw: str) -> list:
    """Coerce any AnyLog response shape into a list of row-dicts."""
    raw = (raw or "").strip()
    if not raw or raw in ("[]", "null", "{}"):
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.warning("JSON parse error: %s  raw=%s", exc, raw[:200])
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("Query", "result", "rows", "data"):
            if key in data and isinstance(data[key], list):
                return data[key]
        return [data]   # single-row dict
    return []


def run_sql(dbms: str, sql: str) -> list:
    """Distributed SQL query → list of row dicts."""
    raw  = anylog_get(f"sql {dbms} {sql}", sql_query=True)
    rows = _parse_rows(raw)
    _stats["total_rows"] += len(rows)
    return rows


def run_command(command: str) -> str:
    """Non-SQL AnyLog command → raw response text."""
    return anylog_get(command, sql_query=False)


# ---------------------------------------------------------------------------
# Error helper
# ---------------------------------------------------------------------------
def _err(msg: str, details: str = "", status: int = 500):
    _stats["errors"] += 1
    body = {"error": msg}
    if details:
        body["details"] = details
    return jsonify(body), status


# ---------------------------------------------------------------------------
# before_request  – count real proxy calls only
# ---------------------------------------------------------------------------
@app.before_request
def _count():
    if request.method != "OPTIONS" and request.path not in _SKIP_PATHS:
        _stats["proxy_calls"] += 1


# ===========================================================================
# Health / Stats
# ===========================================================================
@app.route("/health", methods=["GET"])
def health():
    conn, node_raw = "ok", ""
    try:
        node_raw = run_command("get status").strip()[:200]
    except Exception as exc:
        conn     = "error"
        node_raw = str(exc)[:200]

    lats   = _stats["_latencies"]
    avg_ms = round(sum(lats) / len(lats)) if lats else None

    return jsonify({
        "status":          "healthy",
        "service":         "anylog-rest-proxy",
        "anylog_node":     f"{CFG['anylog_ip']}:{CFG['anylog_port']}",
        "anylog_protocol": "http",
        "user_agent":      "AnyLog/1.23",
        "connection":      conn,
        "node_status":     node_raw,
        "uptime_sec":      round(time.time() - _stats["start_time"]),
        "avg_latency_ms":  avg_ms,
        "stats": {
            "proxy_calls":  _stats["proxy_calls"],
            "anylog_calls": _stats["anylog_calls"],
            "total_rows":   _stats["total_rows"],
            "errors":       _stats["errors"],
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@app.route("/stats", methods=["GET"])
def stats_ep():
    lats = _stats["_latencies"]
    return jsonify({
        "proxy_calls":    _stats["proxy_calls"],
        "anylog_calls":   _stats["anylog_calls"],
        "total_rows":     _stats["total_rows"],
        "errors":         _stats["errors"],
        "avg_latency_ms": round(sum(lats) / len(lats)) if lats else None,
        "uptime_sec":     round(time.time() - _stats["start_time"]),
    })


# ===========================================================================
# Connection
# ===========================================================================
@app.route("/api/connection/status", methods=["GET"])
def connection_status():
    try:
        raw = run_command("get status")
        return jsonify({
            "status":   "established",
            "node":     f"{CFG['anylog_ip']}:{CFG['anylog_port']}",
            "response": raw.strip()[:200],
        })
    except Exception as exc:
        return _err("Connection failed", str(exc), 503)


@app.route("/api/connection/test", methods=["POST", "OPTIONS"])
def connection_test():
    if request.method == "OPTIONS":
        return "", 204
    try:
        raw = run_command("get status")
        return jsonify({"success": True, "response": raw.strip()[:200]})
    except Exception as exc:
        return _err("Connection test failed", str(exc), 503)


# ===========================================================================
# Metadata discovery
# ===========================================================================
@app.route("/api/databases", methods=["GET"])
def list_databases():
    try:
        raw = run_command("get databases")
        return jsonify({"databases": _parse_rows(raw), "raw": raw})
    except Exception as exc:
        return _err("Failed to list databases", str(exc))


@app.route("/api/databases/<dbms>/tables", methods=["GET"])
def list_tables(dbms):
    try:
        raw = run_command(f"get tables where dbms = {dbms}")
        return jsonify({"dbms": dbms, "tables": _parse_rows(raw), "raw": raw})
    except Exception as exc:
        return _err(f"Failed to list tables for {dbms}", str(exc))


@app.route("/api/databases/<dbms>/tables/<table>/columns", methods=["GET"])
def list_columns(dbms, table):
    try:
        raw = run_command(f"get columns where dbms = {dbms} and table = {table} and format = json")
        return jsonify({"dbms": dbms, "table": table, "columns": _parse_rows(raw)})
    except Exception as exc:
        return _err(f"Failed to list columns for {dbms}.{table}", str(exc))


# ===========================================================================
# Node management
# ===========================================================================
@app.route("/api/nodes", methods=["GET"])
def list_nodes():
    try:
        raw = run_command("get cluster info")
        return jsonify({"nodes": _parse_rows(raw), "raw": raw})
    except Exception as exc:
        return _err("Failed to get cluster info", str(exc))


@app.route("/api/nodes/status", methods=["GET", "POST", "OPTIONS"])
def node_status():
    if request.method == "OPTIONS":
        return "", 204
    node = None
    if request.method == "POST":
        body = request.get_json(force=True, silent=True) or {}
        node = body.get("node")
    else:
        node = request.args.get("node")
    cmd = f"get status where node = {node}" if node else "get status"
    try:
        raw = run_command(cmd)
        return jsonify({"node": node or "local", "status": raw.strip()})
    except Exception as exc:
        return _err("Failed to get node status", str(exc))


@app.route("/api/data/location", methods=["GET"])
def data_location():
    dbms  = request.args.get("dbms",  "")
    table = request.args.get("table", "")
    try:
        raw  = run_command("get data nodes")
        rows = _parse_rows(raw)
        if dbms:
            rows = [r for r in rows if str(r.get("dbms", "")).lower() == dbms.lower()]
        if table:
            rows = [r for r in rows if str(r.get("table", "")).lower() == table.lower()]
        return jsonify({"dbms": dbms, "table": table, "locations": rows})
    except Exception as exc:
        return _err("Failed to get data location", str(exc))


# ===========================================================================
# SQL query  (main dashboard endpoint)
# ===========================================================================
@app.route("/api/query", methods=["POST", "OPTIONS"])
def api_query():
    if request.method == "OPTIONS":
        return "", 204

    body = request.get_json(force=True, silent=True) or {}
    dbms = (body.get("dbms") or "").strip()
    sql  = (body.get("sql")  or "").strip()

    if not dbms or not sql:
        return _err("Request body must include 'dbms' and 'sql'", status=400)

    log.info("[PROXY] /api/query  dbms=%s | sql=%s", dbms, sql[:150])

    try:
        rows = run_sql(dbms, sql)
        return jsonify(rows)
    except AnyLogError as exc:
        # AnyLog returned a structured error (timeout, SQL failure, etc.)
        # delivered via malformed chunked response — already extracted cleanly
        log.warning("[PROXY] AnyLog error %d: %s", exc.err_code, exc.err_text)
        return _err(f"AnyLog error {exc.err_code}: {exc.err_text}", exc.raw[:300], 502)
    except requests.exceptions.ConnectionError as exc:
        return _err(
            f"Cannot connect to AnyLog node {CFG['anylog_ip']}:{CFG['anylog_port']}",
            str(exc), 502,
        )
    except requests.exceptions.Timeout:
        return _err(f"AnyLog node timed out after {CFG['timeout']}s", status=504)
    except requests.exceptions.HTTPError as exc:
        return _err(
            f"AnyLog returned HTTP {exc.response.status_code}",
            exc.response.text[:300], 502,
        )
    except Exception as exc:
        log.error("[PROXY] Unexpected: %s", exc, exc_info=True)
        return _err(str(exc))


# ===========================================================================
# Time-series increment query
# ===========================================================================
@app.route("/api/query/increment", methods=["POST", "OPTIONS"])
def api_query_increment():
    if request.method == "OPTIONS":
        return "", 204

    body        = request.get_json(force=True, silent=True) or {}
    dbms        = (body.get("dbms")          or "").strip()
    table       = (body.get("table")         or "").strip()
    time_col    = (body.get("timeColumn")    or "insert_timestamp").strip()
    start       = (body.get("startTime")     or "NOW() - 1 day").strip()
    end         = (body.get("endTime")       or "NOW()").strip()
    unit        = (body.get("timeUnit")      or "hour").strip()
    interval    = int(body.get("intervalLength", 1))
    projections = body.get("projections", ["avg(rest)"])

    if not dbms or not table:
        return _err("Missing 'dbms' or 'table'", status=400)

    proj_str = ", ".join(projections)
    sql = (
        f"SELECT increments({unit}, {interval}, {time_col}), "
        f"{proj_str} "
        f"FROM {table} "
        f"WHERE {time_col} >= '{start}' AND {time_col} <= '{end}' "
        f"ORDER BY {time_col}"
    )

    log.info("[PROXY] /api/query/increment  dbms=%s | sql=%s", dbms, sql[:160])

    try:
        rows = run_sql(dbms, sql)
        return jsonify(rows)
    except AnyLogError as exc:
        log.warning("[PROXY] AnyLog error %d: %s", exc.err_code, exc.err_text)
        return _err(f"AnyLog error {exc.err_code}: {exc.err_text}", exc.raw[:300], 502)
    except requests.exceptions.ConnectionError as exc:
        return _err(f"Cannot connect to {CFG['anylog_ip']}:{CFG['anylog_port']}", str(exc), 502)
    except requests.exceptions.Timeout:
        return _err(f"Timeout after {CFG['timeout']}s", status=504)
    except requests.exceptions.HTTPError as exc:
        return _err(f"HTTP {exc.response.status_code}", exc.response.text[:300], 502)
    except Exception as exc:
        log.error("[PROXY] Unexpected: %s", exc, exc_info=True)
        return _err(str(exc))


# ===========================================================================
# Arbitrary AnyLog command
# ===========================================================================
@app.route("/api/command", methods=["POST", "OPTIONS"])
def api_command():
    if request.method == "OPTIONS":
        return "", 204

    body    = request.get_json(force=True, silent=True) or {}
    command = (body.get("command") or "").strip()
    if not command:
        return _err("Request body must include 'command'", status=400)

    timeout_override = body.get("timeout")
    orig_timeout = CFG["timeout"]
    if timeout_override:
        CFG["timeout"] = float(timeout_override)

    try:
        raw  = run_command(command)
        rows = _parse_rows(raw)
        return jsonify({"command": command, "raw": raw, "rows": rows})
    except AnyLogError as exc:
        return _err(f"AnyLog error {exc.err_code}: {exc.err_text}", exc.raw[:300], 502)
    except Exception as exc:
        return _err(str(exc))
    finally:
        CFG["timeout"] = orig_timeout



# ===========================================================================
# UNS policies (blockchain root policies)
# ===========================================================================
@app.route("/api/uns", methods=["GET"])
def uns_root_policies():
    """Return UNS root policies via AnyLog blockchain."""
    cmd = "blockchain get uns"
    try:
        raw = run_command(cmd)
        policies = _parse_rows(raw)
        return jsonify({"command": cmd, "policies": policies, "raw": raw})
    except AnyLogError as exc:
        log.warning("[PROXY] AnyLog error %d: %s", exc.err_code, exc.err_text)
        return _err(f"AnyLog error {exc.err_code}: {exc.err_text}", exc.raw[:300], 502)
    except Exception as exc:
        return _err("Failed to get UNS root policies", str(exc))


# ===========================================================================
# Entry point
# ===========================================================================
def main():
    parser = argparse.ArgumentParser(description="AnyLog rest REST Proxy")
    parser.add_argument("--host",        default="0.0.0.0",       help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port",        type=int, default=8080,  help="Proxy port (default: 8080)")
    parser.add_argument("--anylog-ip",   default="172.79.89.206", help="AnyLog node IP")
    parser.add_argument("--anylog-port", type=int, default=32049, help="AnyLog node REST port")
    parser.add_argument("--timeout",     type=float, default=60.0,help="Request timeout in seconds (default: 60)")
    parser.add_argument("--debug",       action="store_true",     help="Enable Flask debug mode")
    args = parser.parse_args()

    # Env-var overrides (as documented in README_REST.md)
    CFG["anylog_ip"]   = os.environ.get("ANYLOG_IP",   args.anylog_ip)
    CFG["anylog_port"] = int(os.environ.get("ANYLOG_PORT", args.anylog_port))
    CFG["timeout"]     = float(os.environ.get("TIMEOUT",   args.timeout))

    proxy_host = os.environ.get("PROXY_HOST", args.host)
    proxy_port = int(os.environ.get("PROXY_PORT", args.port))

    log.info("=" * 62)
    log.info("  AnyLog rest Proxy")
    log.info("  Listening  :  http://%s:%d", proxy_host, proxy_port)
    log.info("  AnyLog node:  http://%s:%d  (HTTP, not HTTPS)", CFG["anylog_ip"], CFG["anylog_port"])
    log.info("  User-Agent :  AnyLog/1.23")
    log.info("  Timeout    :  %.0f s", CFG["timeout"])
    log.info("")
    log.info("  Set Dashboard 'Proxy URL' to:")
    log.info("    http://localhost:%d", proxy_port)
    log.info("")
    log.info("  Quick test:")
    log.info("    curl http://localhost:%d/health", proxy_port)
    log.info("=" * 62)

    if not args.debug:
        logging.getLogger("werkzeug").setLevel(logging.WARNING)

    app.run(host=proxy_host, port=proxy_port, debug=args.debug, threaded=True)


if __name__ == "__main__":
    main()
