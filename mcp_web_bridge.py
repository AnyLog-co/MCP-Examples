#!/usr/bin/env python3
"""
MCP Web Bridge  v4.0
====================
Bridges HTTP REST requests from browser dashboards → MCP protocol → AnyLog network.

NEW IN v4.0
-----------
* --mcp-url  CLI argument  : choose which MCP SSE server to connect to at launch
* --mcp-proxy CLI argument : override the mcp-proxy binary path
* --port / --host          : bind address control
* UNS-aware database discovery: /api/uns/databases discovers the full set of
  databases in use across all UNS policies, then caches them so dashboards
  always query the right database for whatever MCP connector is active.

ARCHITECTURE (unchanged from v3)
---------------------------------
ONE subprocess (mcp-proxy), ONE worker thread, ONE MCP connection.
HTTP endpoints NEVER call MCP directly — they post a Job to the worker queue
and block on a threading.Event until the worker completes it.
The worker executes jobs one at a time with CALL_DELAY_S between them.

CACHE
-----
Results stored in a TTL cache keyed by (tool, canonical-params).
Metadata cached for CACHE_TTL_S; sensor/query data for DATA_TTL_S.
"""

import argparse
import json
import logging
import os
import queue
import ssl
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, jsonify, request
from flask_cors import CORS

# ---------------------------------------------------------------------------
# Defaults (all overridable via CLI)
# ---------------------------------------------------------------------------
DEFAULT_MCP_PROXY_PATH = "/Users/mdavidson58/Documents/AnyLog/Prove-IT/venv/bin/mcp-proxy"
DEFAULT_MCP_SERVER_URL = "https://172.79.89.206:32049/mcp/sse"
DEFAULT_PORT           = 8080
DEFAULT_HOST           = "0.0.0.0"

DEFAULT_CALL_DELAY_S = 1.5
CALL_DELAY_S  = DEFAULT_CALL_DELAY_S  # pause between MCP calls (be gentle on the SSE server)
JOB_TIMEOUT_S = 60    # max seconds an HTTP request waits for the worker
CACHE_TTL_S   = 300   # 5 min — metadata (tables, UNS, status)
DATA_TTL_S    = 30    # 30 s  — query results

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

# Root logger configured later in main() once CLI args are parsed.
# A minimal stderr handler is set here so any import-time messages are visible.
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, stream=sys.stderr)
log = logging.getLogger("mcp_bridge")


def _configure_logging(quiet: bool, log_file: Optional[str], debug: bool) -> None:
    """
    Reconfigure the root logger after CLI args are parsed.

    quiet=True  → WARNING level  (errors/warnings only, no per-request chatter)
    debug=True  → DEBUG level    (overrides quiet)
    log_file    → also write to the given file path (appends, UTF-8)
    """
    level = logging.DEBUG if debug else (logging.WARNING if quiet else logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    formatter = logging.Formatter(LOG_FORMAT)

    # Replace any existing handlers on the root logger
    root.handlers.clear()

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)
    root.addHandler(stderr_handler)

    if log_file:
        try:
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
            log.info("Logging to file: %s", log_file)
        except OSError as exc:
            log.error("Cannot open log file %r: %s — file logging disabled", log_file, exc)


def _make_self_signed_cert(cert_path: str, key_path: str) -> None:
    """
    Generate a self-signed TLS certificate + private key.
    Uses the openssl CLI if available, otherwise falls back to the
    cryptography package (pip install cryptography).
    Called automatically when --ssl is given without --ssl-cert/--ssl-key.
    """
    import shutil
    if shutil.which("openssl"):
        cmd = [
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", key_path,
            "-out",    cert_path,
            "-days",   "365",
            "-nodes",
            "-subj",   "/CN=mcp-web-bridge",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            log.info("Self-signed cert generated via openssl: %s / %s", cert_path, key_path)
            return
        log.warning("openssl failed (%s); trying cryptography package", result.stderr.strip())

    # Fallback: cryptography package
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        import datetime

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, u"mcp-web-bridge"),
        ])
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime.utcnow())
            .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=365))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(key, hashes.SHA256())
        )
        with open(key_path, "wb") as f:
            f.write(key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            ))
        with open(cert_path, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))
        log.info("Self-signed cert generated via cryptography: %s / %s", cert_path, key_path)
    except ImportError:
        raise RuntimeError(
            "Cannot generate a self-signed certificate: neither 'openssl' CLI nor "
            "the 'cryptography' package is available.\n"
            "Install with:  pip install cryptography\n"
            "or supply --ssl-cert / --ssl-key paths to an existing certificate."
        )


def _build_ssl_context(cert: str, key: str) -> ssl.SSLContext:
    """Return an SSLContext suitable for Flask/Werkzeug."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=cert, keyfile=key)
    return ctx


# ---------------------------------------------------------------------------
# Runtime config (populated in main() from parsed args)
# ---------------------------------------------------------------------------
CFG: Dict[str, Any] = {}

# ---------------------------------------------------------------------------
# Simple TTL Cache
# ---------------------------------------------------------------------------
_cache: Dict[str, Any]        = {}
_cache_ts: Dict[str, float]   = {}
_cache_lock = threading.Lock()


def cache_get(key: str, ttl: float) -> Optional[Any]:
    with _cache_lock:
        if key in _cache and (time.time() - _cache_ts[key]) < ttl:
            return _cache[key]
    return None


def cache_set(key: str, value: Any) -> None:
    with _cache_lock:
        _cache[key]    = value
        _cache_ts[key] = time.time()


def cache_clear() -> None:
    with _cache_lock:
        _cache.clear()
        _cache_ts.clear()

# ---------------------------------------------------------------------------
# Job / Worker
# ---------------------------------------------------------------------------
@dataclass
class Job:
    tool:       str
    params:     Dict[str, Any]
    cache_key:  str
    cache_ttl:  float
    done:       threading.Event = field(default_factory=threading.Event)
    result:     Any             = None
    error:      Optional[str]   = None


_job_queue: queue.Queue = queue.Queue()
_pending_jobs: Dict[str, Job] = {}   # cache_key → in-flight job (dedup)
_pending_lock = threading.Lock()


def _enqueue(tool: str, params: Dict[str, Any],
             cache_ttl: float = CACHE_TTL_S) -> Job:
    """Post a job and return it. Deduplicates by cache_key."""
    cache_key = f"{tool}:{json.dumps(params, sort_keys=True)}"

    # Cache hit — return immediately with a pre-done synthetic job
    cached = cache_get(cache_key, cache_ttl)
    if cached is not None:
        row_count = len(cached) if isinstance(cached, list) else "cached"
        log.info("CACHE hit       tool=%-28s  rows=%s", tool, row_count)
        j = Job(tool=tool, params=params, cache_key=cache_key, cache_ttl=cache_ttl)
        j.result = cached
        j.done.set()
        return j

    with _pending_lock:
        if cache_key in _pending_jobs:
            log.info("DEDUP           tool=%-28s  (joining in-flight job)", tool)
            return _pending_jobs[cache_key]
        j = Job(tool=tool, params=params, cache_key=cache_key, cache_ttl=cache_ttl)
        _pending_jobs[cache_key] = j

    _job_queue.put(j)
    return j


def _worker() -> None:
    """Single worker: pop jobs, call MCP, set events."""
    log.info("Worker thread started")
    while True:
        try:
            job: Job = _job_queue.get(timeout=5)
        except queue.Empty:
            continue

        qd = _job_queue.qsize()
        log.info("WORKER dequeue  tool=%-28s  queue_remaining=%d", job.tool, qd)
        try:
            result = _call_mcp(job.tool, job.params)
            job.result = result
            cache_set(job.cache_key, result)
        except Exception as exc:
            log.error("WORKER error    tool=%-28s  err=%s", job.tool, exc)
            job.error = str(exc)
        finally:
            with _pending_lock:
                _pending_jobs.pop(job.cache_key, None)
            job.done.set()
            _job_queue.task_done()
            time.sleep(CALL_DELAY_S)


def start_worker() -> None:
    t = threading.Thread(target=_worker, daemon=True, name="mcp-worker")
    t.start()


# ---------------------------------------------------------------------------
# MCPClient — one subprocess, serialized via the worker thread
# ---------------------------------------------------------------------------
_mcp_proc: Optional[subprocess.Popen] = None
_mcp_lock = threading.Lock()
_req_id   = 0


def _get_mcp_proc() -> subprocess.Popen:
    global _mcp_proc
    with _mcp_lock:
        if _mcp_proc is None or _mcp_proc.poll() is not None:
            log.info("Spawning mcp-proxy → %s", CFG["mcp_url"])
            _mcp_proc = subprocess.Popen(
                [CFG["mcp_proxy"], CFG["mcp_url"]],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            # MCP initialize handshake
            _send_rpc(_mcp_proc, "initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "mcp-web-bridge", "version": "4.0"},
            })
            _send_rpc(_mcp_proc, "notifications/initialized", {})
        return _mcp_proc


def _send_rpc(proc: subprocess.Popen, method: str,
              params: Dict[str, Any]) -> Optional[Dict]:
    global _req_id
    _req_id += 1
    req = {"jsonrpc": "2.0", "id": _req_id, "method": method, "params": params}
    line = json.dumps(req) + "\n"
    proc.stdin.write(line)
    proc.stdin.flush()

    if method == "notifications/initialized":
        return None

    # Read until we get the response matching our id
    deadline = time.time() + 45
    while time.time() < deadline:
        raw = proc.stdout.readline()
        if not raw:
            time.sleep(0.05)
            continue
        try:
            msg = json.loads(raw)
            if msg.get("id") == _req_id:
                return msg
        except json.JSONDecodeError:
            continue
    raise TimeoutError(f"No response for id={_req_id} method={method}")


def _call_mcp(tool: str, params: Dict[str, Any]) -> Any:
    """Call an MCP tool and return parsed result. Called ONLY from worker thread."""
    t0 = time.time()
    param_summary = json.dumps(params)[:200]
    log.info("MCP ▶  tool=%-28s  params=%s", tool, param_summary)
    proc = _get_mcp_proc()
    resp = _send_rpc(proc, "tools/call", {"name": tool, "arguments": params})

    if resp is None:
        log.warning("MCP ◀  tool=%-28s  → None response", tool)
        return None

    if "error" in resp:
        err_msg = resp["error"].get("message", str(resp["error"]))
        log.error("MCP ✗  tool=%-28s  → error: %s", tool, err_msg)
        raise RuntimeError(err_msg)

    result = resp.get("result", {})

    # MCP returns {"content": [{"type": "text", "text": "..."}], "isError": bool}
    if isinstance(result, dict) and "content" in result:
        if result.get("isError"):
            err_text = ""
            for c in result["content"]:
                if c.get("type") == "text":
                    err_text = c["text"]
                    break
            log.error("MCP ✗  tool=%-28s  → isError: %s", tool, err_text[:200])
            raise RuntimeError(f"MCP tool error: {err_text}")
        texts = [c["text"] for c in result["content"] if c.get("type") == "text"]
        combined = "\n".join(texts)
        try:
            parsed = json.loads(combined)
            row_count = len(parsed) if isinstance(parsed, list) else "dict"
            ms = int((time.time() - t0) * 1000)
            log.info("MCP ◀  tool=%-28s  → %s rows  (%dms)", tool, row_count, ms)
            return parsed
        except json.JSONDecodeError:
            ms = int((time.time() - t0) * 1000)
            log.info("MCP ◀  tool=%-28s  → text (%d chars)  (%dms)", tool, len(combined), ms)
            return combined

    ms = int((time.time() - t0) * 1000)
    log.info("MCP ◀  tool=%-28s  → raw result  (%dms)", tool, ms)
    return result


# ---------------------------------------------------------------------------
# UNS database discovery
# ---------------------------------------------------------------------------
def _discover_databases_from_uns() -> List[str]:
    """
    Query all UNS policies for this MCP connector and collect the unique set
    of database names referenced. Falls back to listNetworkDatabases if UNS
    has no 'dbms' fields.
    """
    dbs = set()

    # 1. Try listPolicyTypes to confirm 'uns' exists
    try:
        policy_types_raw = _call_mcp("listPolicyTypes", {})
        has_uns = False
        if isinstance(policy_types_raw, list):
            has_uns = any(
                (p.get("policy") if isinstance(p, dict) else p) == "uns"
                for p in policy_types_raw
            )
        elif isinstance(policy_types_raw, dict) and "policies" in policy_types_raw:
            has_uns = any(p.get("policy") == "uns"
                         for p in policy_types_raw["policies"])

        if has_uns:
            uns_raw = _call_mcp("listPolicies", {"policyType": "uns"})
            policies = []
            if isinstance(uns_raw, list):
                policies = uns_raw
            elif isinstance(uns_raw, dict):
                policies = uns_raw.get("policies", uns_raw.get("result", []))

            for p in policies:
                pol = p.get("uns", p) if isinstance(p, dict) else {}
                dbms = pol.get("dbms")
                if dbms:
                    dbs.add(dbms)

            if dbs:
                log.info("UNS discovery found databases: %s", sorted(dbs))
                return sorted(dbs)

    except Exception as exc:
        log.warning("UNS policy scan failed (%s), falling back to listNetworkDatabases", exc)

    # 2. Fallback: listNetworkDatabases
    try:
        raw = _call_mcp("listNetworkDatabases", {})
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    dbms = item.get("dbms") or item.get("name") or item.get("database")
                    if dbms:
                        dbs.add(dbms)
                elif isinstance(item, str):
                    dbs.add(item)
        elif isinstance(raw, dict):
            for k in ("databases", "dbms", "result"):
                if k in raw:
                    for item in (raw[k] if isinstance(raw[k], list) else [raw[k]]):
                        dbms = item.get("dbms") or item.get("name") if isinstance(item, dict) else item
                        if dbms:
                            dbs.add(str(dbms))
                    break
        log.info("listNetworkDatabases found: %s", sorted(dbs))
    except Exception as exc:
        log.error("listNetworkDatabases also failed: %s", exc)

    return sorted(dbs)


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__)
CORS(app)

import time as _time

@app.before_request
def _before():
    from flask import g
    g._t0 = _time.time()
    if request.method == "POST":
        body = request.get_data(as_text=True)
        log.info("HTTP ▶  %s %s  body=%s", request.method, request.path,
                 body[:300] if body else "(empty)")
    else:
        log.info("HTTP ▶  %s %s  args=%s", request.method, request.path,
                 dict(request.args))

@app.after_request
def _after(response):
    from flask import g
    ms = int((_time.time() - getattr(g, "_t0", _time.time())) * 1000)
    log.info("HTTP ◀  %s %s  status=%d  (%dms)",
             request.method, request.path, response.status_code, ms)
    return response


def _run_job(tool: str, params: Dict, ttl: float = CACHE_TTL_S):
    """Enqueue a job, wait for it, return (result, error_str)."""
    job = _enqueue(tool, params, cache_ttl=ttl)
    if not job.done.wait(timeout=JOB_TIMEOUT_S):
        return None, "timeout waiting for MCP worker"
    if job.error:
        return None, job.error
    return job.result, None


# ── Status ──────────────────────────────────────────────────────────────────
@app.route("/api/status")
def api_status():
    result, err = _run_job("checkStatus", {}, ttl=30)
    if err:
        return jsonify({"status": "error", "error": err,
                        "mcp_url": CFG.get("mcp_url")}), 503
    return jsonify({"status": "ok", "mcp_url": CFG.get("mcp_url"),
                    "result": result})


# ── UNS: database discovery ─────────────────────────────────────────────────
@app.route("/api/uns/databases")
def api_uns_databases():
    """
    Discover all databases referenced in UNS policies for the active MCP
    connector. Results cached for CACHE_TTL_S.
    """
    cache_key = f"uns_databases:{CFG.get('mcp_url')}"
    cached = cache_get(cache_key, CACHE_TTL_S)
    if cached is not None:
        return jsonify({"databases": cached, "source": "cache",
                        "mcp_url": CFG.get("mcp_url")})

    # Run discovery in worker context by using a custom job
    job = Job(
        tool="__uns_discover_databases__",
        params={},
        cache_key=cache_key,
        cache_ttl=CACHE_TTL_S,
    )

    def _run():
        try:
            dbs = _discover_databases_from_uns()
            job.result = dbs
            cache_set(cache_key, dbs)
        except Exception as exc:
            job.error = str(exc)
        finally:
            job.done.set()

    # Run directly in a short-lived thread so it uses the same _call_mcp path
    # but still respects CALL_DELAY_S via the worker queue for each sub-call.
    # We enqueue the individual sub-calls inside _discover_databases_from_uns
    # so they all go through the single-worker queue.
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    if not job.done.wait(timeout=JOB_TIMEOUT_S * 2):
        return jsonify({"error": "timeout during UNS database discovery"}), 504
    if job.error:
        return jsonify({"error": job.error}), 500

    return jsonify({"databases": job.result, "source": "uns",
                    "mcp_url": CFG.get("mcp_url")})


# ── UNS: full policy list ────────────────────────────────────────────────────
@app.route("/api/uns/discover")
def api_uns_discover():
    result, err = _run_job("listPolicies", {"policyType": "uns"})
    if err:
        return jsonify({"error": err}), 500
    policies = result if isinstance(result, list) else result.get("policies", []) if isinstance(result, dict) else []
    return jsonify({"policies": policies, "count": len(policies),
                    "mcp_url": CFG.get("mcp_url")})


@app.route("/api/uns/policies")
def api_uns_policies():
    policy_type = request.args.get("type", "uns")
    where       = request.args.get("where", "")
    params = {"policyType": policy_type}
    if where:
        params["whereCond"] = where
    result, err = _run_job("listPolicies", params)
    if err:
        return jsonify({"error": err}), 500
    policies = result if isinstance(result, list) else result.get("policies", []) if isinstance(result, dict) else []
    return jsonify({"policies": policies, "count": len(policies)})


# ── Schema ───────────────────────────────────────────────────────────────────
@app.route("/api/tables")
def api_tables():
    dbms = request.args.get("dbms", "")
    if not dbms:
        return jsonify({"error": "?dbms= required"}), 400
    result, err = _run_job("listTables", {"dbms": dbms})
    if err:
        return jsonify({"error": err}), 500
    tables = result if isinstance(result, list) else result.get("tables", []) if isinstance(result, dict) else []
    return jsonify({"dbms": dbms, "tables": tables})


@app.route("/api/columns")
def api_columns():
    dbms  = request.args.get("dbms", "")
    table = request.args.get("table", "")
    if not dbms or not table:
        return jsonify({"error": "?dbms= and ?table= required"}), 400
    result, err = _run_job("listColumns", {"dbms": dbms, "table": table})
    if err:
        return jsonify({"error": err}), 500
    cols = result if isinstance(result, list) else result.get("columns", []) if isinstance(result, dict) else []
    return jsonify({"dbms": dbms, "table": table, "columns": cols})


@app.route("/api/databases")
def api_databases():
    result, err = _run_job("listNetworkDatabases", {})
    if err:
        return jsonify({"error": err}), 500
    dbs = result if isinstance(result, list) else result.get("databases", []) if isinstance(result, dict) else []
    return jsonify({"databases": dbs})


# ── Query ────────────────────────────────────────────────────────────────────
@app.route("/api/query", methods=["POST"])
@app.route("/api/mcp/query", methods=["POST"])   # legacy alias
def api_query():
    body  = request.get_json(force=True) or {}
    dbms  = body.get("dbms", "")
    sql   = body.get("sql", "")
    nodes = body.get("nodes", "")
    if not dbms or not sql:
        return jsonify({"error": "body must contain {dbms, sql}"}), 400
    params = {"dbms": dbms, "sql": sql}
    if nodes:
        params["nodes"] = nodes
    result, err = _run_job("executeQuery", params, ttl=DATA_TTL_S)
    if err:
        return jsonify({"error": err}), 500
    rows = result if isinstance(result, list) else result.get("results", result.get("rows", [])) if isinstance(result, dict) else []
    return jsonify({"results": rows, "row_count": len(rows), "dbms": dbms})


# ── Incremental query ─────────────────────────────────────────────────────────
@app.route("/api/query/increment", methods=["POST"])
def api_query_increment():
    body = request.get_json(force=True) or {}
    required = ["dbms", "table", "timeColumn", "startTime", "endTime",
                "intervalLength", "timeUnit", "projections"]
    missing = [k for k in required if k not in body]
    if missing:
        return jsonify({"error": f"missing fields: {missing}"}), 400

    params = {
        "dbms":           body["dbms"],
        "table":          body["table"],
        "timeColumn":     body["timeColumn"],
        "startTime":      body["startTime"],
        "endTime":        body["endTime"],
        "intervalLength": int(body["intervalLength"]),
        "timeUnit":       body["timeUnit"],
        "projections":    body["projections"],
    }
    if "nodes" in body:
        params["nodes"] = body["nodes"]

    result, err = _run_job("queryWithIncrement", params, ttl=DATA_TTL_S)
    if err:
        return jsonify({"error": err}), 500
    rows = result if isinstance(result, list) else result.get("results", []) if isinstance(result, dict) else []
    return jsonify({"results": rows, "row_count": len(rows)})


# ── Nodes ────────────────────────────────────────────────────────────────────
@app.route("/api/nodes")
def api_nodes():
    result, err = _run_job("getNodesList", {})
    if err:
        return jsonify({"error": err}), 500
    return jsonify({"nodes": result})


@app.route("/api/nodes/monitor")
def api_nodes_monitor():
    status_type = request.args.get("type", "status")
    nodes       = request.args.get("nodes", "")
    params = {"status_type": status_type}
    if nodes:
        params["nodes"] = nodes
    result, err = _run_job("monitorNodes", params)
    if err:
        return jsonify({"error": err}), 500
    return jsonify({"result": result})


# ── Cache control ─────────────────────────────────────────────────────────────
@app.route("/api/cache/clear", methods=["POST"])
def api_cache_clear():
    cache_clear()
    return jsonify({"status": "cleared"})


# ── Worker status ─────────────────────────────────────────────────────────────
@app.route("/api/worker/status")
def api_worker_status():
    with _pending_lock:
        in_flight = list(_pending_jobs.keys())
    return jsonify({
        "queue_depth": _job_queue.qsize(),
        "in_flight":   in_flight,
        "call_delay_s": CALL_DELAY_S,
        "mcp_url": CFG.get("mcp_url"),
    })


# ── Dashboard serve ───────────────────────────────────────────────────────────
@app.route("/")
def index():
    import os
    for name in ["timbergrove_dashboard.html",
                 "enterprise_c_spc_mcp_dashboard.html",
                 "dashboard.html"]:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
        if os.path.exists(path):
            with open(path) as f:
                return f.read()
    return (
        "<h1>MCP Web Bridge v4.0</h1>"
        "<p>MCP: <code>" + CFG.get("mcp_url", "?") + "</code></p>"
        "<p>Endpoints: /api/status  /api/uns/databases  /api/uns/discover  "
        "/api/uns/policies  /api/tables  /api/columns  /api/databases  "
        "/api/query(POST)  /api/query/increment(POST)  /api/nodes  "
        "/api/nodes/monitor  /api/cache/clear(POST)  /api/worker/status</p>"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    # global must be declared before CALL_DELAY_S is assigned in this scope
    global CALL_DELAY_S

    parser = argparse.ArgumentParser(
        description="MCP Web Bridge v4.0 — HTTP ↔ AnyLog MCP SSE proxy",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--mcp-url",
        default=DEFAULT_MCP_SERVER_URL,
        help="MCP SSE server URL to connect to (e.g. https://172.79.89.206:32049/mcp/sse)",
    )
    parser.add_argument(
        "--mcp-proxy",
        default=DEFAULT_MCP_PROXY_PATH,
        help="Path to the mcp-proxy binary",
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=DEFAULT_PORT,
        help="HTTP port to listen on",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help="Interface to bind to",
    )
    parser.add_argument(
        "--call-delay",
        type=float,
        default=DEFAULT_CALL_DELAY_S,
        help="Seconds to pause between MCP calls (prevents SSE server overload)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable Flask debug mode and verbose logging (overrides --quiet)",
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress INFO-level log chatter; show only warnings and errors",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        metavar="PATH",
        help="Append log output to this file in addition to stderr (e.g. bridge.log)",
    )

    # ── TLS / HTTPS ──────────────────────────────────────────────────────────
    ssl_group = parser.add_argument_group(
        "TLS / HTTPS",
        "Serve the bridge over HTTPS instead of plain HTTP.  Use --ssl to "
        "enable TLS.  Supply --ssl-cert and --ssl-key if you have an existing "
        "certificate; omit them to auto-generate a self-signed certificate "
        "(requires openssl CLI or: pip install cryptography).",
    )
    ssl_group.add_argument(
        "--ssl",
        action="store_true",
        help="Enable HTTPS on the Flask frontend",
    )
    ssl_group.add_argument(
        "--ssl-cert",
        default=None,
        metavar="CERT.pem",
        help="Path to PEM certificate file (implies --ssl)",
    )
    ssl_group.add_argument(
        "--ssl-key",
        default=None,
        metavar="KEY.pem",
        help="Path to PEM private-key file (implies --ssl)",
    )

    args = parser.parse_args()

    # --ssl-cert / --ssl-key imply --ssl
    use_ssl = args.ssl or bool(args.ssl_cert or args.ssl_key)

    # Populate runtime config
    CFG["mcp_url"]    = args.mcp_url
    CFG["mcp_proxy"]  = args.mcp_proxy
    CFG["port"]       = args.port
    CFG["host"]       = args.host

    CALL_DELAY_S = args.call_delay

    # Apply logging configuration (quiet / debug / log-file)
    _configure_logging(quiet=args.quiet, log_file=args.log_file, debug=args.debug)

    # ── Resolve TLS certificate ───────────────────────────────────────────────
    ssl_context = None
    if use_ssl:
        cert_path = args.ssl_cert or "mcp_bridge_cert.pem"
        key_path  = args.ssl_key  or "mcp_bridge_key.pem"

        if not (os.path.exists(cert_path) and os.path.exists(key_path)):
            if args.ssl_cert or args.ssl_key:
                # User supplied explicit paths that don't exist — hard error
                missing = [p for p in (cert_path, key_path) if not os.path.exists(p)]
                log.error("SSL file(s) not found: %s", missing)
                sys.exit(1)
            # Auto-generate self-signed cert
            log.info("Generating self-signed certificate ...")
            try:
                _make_self_signed_cert(cert_path, key_path)
            except RuntimeError as exc:
                log.error("%s", exc)
                sys.exit(1)

        ssl_context = _build_ssl_context(cert_path, key_path)
        log.info("TLS enabled: cert=%s  key=%s", cert_path, key_path)

    scheme = "https" if use_ssl else "http"
    log_level_label = "DEBUG" if args.debug else ("WARNING (quiet)" if args.quiet else "INFO")

    print("=" * 65, file=sys.stderr)
    print("MCP Web Bridge  v4.0  (single-worker, all MCP calls serialised)",
          file=sys.stderr)
    print("=" * 65, file=sys.stderr)
    print(f"  MCP URL   : {CFG['mcp_url']}",  file=sys.stderr)
    print(f"  MCP Proxy : {CFG['mcp_proxy']}", file=sys.stderr)
    print(f"  Listen    : {scheme}://{CFG['host']}:{CFG['port']}", file=sys.stderr)
    if use_ssl:
        print(f"  TLS cert  : {cert_path}", file=sys.stderr)
        print(f"  TLS key   : {key_path}",  file=sys.stderr)
    print(f"  Call delay: {CALL_DELAY_S}s between MCP calls", file=sys.stderr)
    print(f"  Job timeout: {JOB_TIMEOUT_S}s per HTTP request", file=sys.stderr)
    print(f"  Log level : {log_level_label}", file=sys.stderr)
    if args.log_file:
        print(f"  Log file  : {args.log_file}", file=sys.stderr)
    print("=" * 65, file=sys.stderr)
    print(file=sys.stderr)

    start_worker()
    app.run(
        host=CFG["host"],
        port=CFG["port"],
        debug=args.debug,
        threaded=True,
        ssl_context=ssl_context,   # None → plain HTTP; SSLContext → HTTPS
    )


if __name__ == "__main__":
    main()
