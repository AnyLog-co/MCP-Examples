#!/usr/bin/env python3
"""
MCP Web Bridge  v4.2
====================
Bridges HTTP REST requests from browser dashboards → MCP protocol → AnyLog network.

NEW IN v4.2
-----------
* _send_rpc deadline reduced from 45 s → 30 s (well under JOB_TIMEOUT_S=60 s)
  so the worker thread never freezes behind a hanging readline.
* _send_rpc uses select() with 0.5 s polling instead of blocking readline(),
  allowing early detection of a dead mcp-proxy process (OSError on poll()!=None).
* _spawn_mcp_proc now drains mcp-proxy stderr non-blocking on failure so the
  real error (TLS mismatch, connection refused, bad URL) appears in the log
  instead of a bare TimeoutError.
* HTTP → HTTPS warning: logs a WARNING if --mcp-url uses http:// on an
  AnyLog-style TLS port (contains ":320"), which is the most common cause
  of silent 60-second timeouts.
* clientInfo version bumped to 4.1 in MCP handshake.

NEW IN v4.0 / v4.1
-------------------
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
# API Call Event Log  (in-memory circular buffer, streamed via SSE)
# ---------------------------------------------------------------------------
import collections

_EVENT_LOG_MAX = 200          # keep last N entries
_event_log: collections.deque = collections.deque(maxlen=_EVENT_LOG_MAX)
_event_log_lock = threading.Lock()
_event_log_listeners: List[queue.Queue] = []
_event_log_listeners_lock = threading.Lock()


def _log_event(entry: Dict[str, Any]) -> None:
    """Append an event to the circular buffer and fan-out to SSE listeners."""
    entry.setdefault("ts", time.time())
    with _event_log_lock:
        _event_log.append(entry)
    # Fan out to any open /api/log SSE streams
    with _event_log_listeners_lock:
        dead = []
        for q in _event_log_listeners:
            try:
                q.put_nowait(entry)
            except Exception:
                dead.append(q)
        for q in dead:
            _event_log_listeners.remove(q)


def _snapshot_log() -> List[Dict]:
    with _event_log_lock:
        return list(_event_log)


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

MCP_SPAWN_RETRIES = 2   # how many times to retry spawning on handshake failure


def _spawn_mcp_proc() -> subprocess.Popen:
    """Spawn a fresh mcp-proxy and complete the MCP initialize handshake.
    Raises on failure so the caller can decide whether to retry."""
    import select as _select
    mcp_url = CFG["mcp_url"]
    log.info("Spawning mcp-proxy → %s", mcp_url)

    # Warn loudly if the URL looks like it should be HTTPS but uses http://.
    # This is the most common cause of silent 60-second timeouts against
    # AnyLog nodes (which default to TLS on port 32x49).
    if mcp_url.startswith("http://") and ":320" in mcp_url:
        log.warning(
            "MCP URL uses http:// on what looks like an AnyLog TLS port (%s). "
            "If the node requires HTTPS this will silently hang. "
            "Restart with --mcp-url https://... if you see timeouts.", mcp_url
        )

    proc = subprocess.Popen(
        [CFG["mcp_proxy"], mcp_url],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        # MCP initialize handshake — done OUTSIDE _mcp_lock so we don't
        # hold the lock for up to 30 s during a blocking readline.
        _send_rpc(proc, "initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "mcp-web-bridge", "version": "4.1"},
        })
        _send_rpc(proc, "notifications/initialized", {})
    except Exception:
        # Drain stderr (non-blocking) before killing so the real error
        # (TLS mismatch, connection refused, bad URL, etc.) is visible.
        try:
            ready, _, _ = _select.select([proc.stderr], [], [], 1.0)
            if ready:
                stderr_out = proc.stderr.read(800)
                if stderr_out:
                    log.warning("mcp-proxy stderr during failed spawn:\n%s", stderr_out)
        except Exception:
            pass
        proc.kill()
        raise
    return proc


def _get_mcp_proc() -> subprocess.Popen:
    global _mcp_proc
    with _mcp_lock:
        alive = _mcp_proc is not None and _mcp_proc.poll() is None
        if alive:
            return _mcp_proc

    # Proc is dead or missing — spawn outside the lock so we don't block
    # other threads for the duration of the handshake.
    last_exc: Optional[Exception] = None
    for attempt in range(1, MCP_SPAWN_RETRIES + 1):
        try:
            new_proc = _spawn_mcp_proc()
        except Exception as exc:
            last_exc = exc
            log.warning("mcp-proxy spawn attempt %d/%d failed: %s",
                        attempt, MCP_SPAWN_RETRIES, exc)
            time.sleep(1.0)
            continue

        with _mcp_lock:
            # Another thread might have raced us — accept whichever proc won.
            if _mcp_proc is None or _mcp_proc.poll() is not None:
                _mcp_proc = new_proc
            else:
                # We lost the race; kill our surplus proc.
                new_proc.kill()
        return _mcp_proc

    raise RuntimeError(
        f"Failed to spawn mcp-proxy after {MCP_SPAWN_RETRIES} attempts: {last_exc}"
    )


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

    # Read until we get the response matching our id.
    # Use select() with a short poll interval so we can:
    #   1. honour the deadline without blocking the worker thread indefinitely
    #   2. detect a dead proc early (poll() != None) and raise OSError
    # Deadline is 30 s — well under JOB_TIMEOUT_S (60 s) so the HTTP layer
    # always gets a clean error rather than a silent timeout.
    import select as _select
    deadline = time.time() + 30
    while time.time() < deadline:
        if proc.poll() is not None:
            raise OSError(f"mcp-proxy exited (rc={proc.poll()}) while waiting for id={_req_id}")
        ready, _, _ = _select.select([proc.stdout], [], [], 0.5)
        if not ready:
            continue
        raw = proc.stdout.readline()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
            if msg.get("id") == _req_id:
                return msg
        except json.JSONDecodeError:
            continue
    raise TimeoutError(f"No response for id={_req_id} method={method} (30s deadline)")


def _call_mcp(tool: str, params: Dict[str, Any]) -> Any:
    """Call an MCP tool and return parsed result. Called ONLY from worker thread."""
    t0 = time.time()
    param_summary = json.dumps(params)[:200]
    log.info("MCP ▶  tool=%-28s  params=%s", tool, param_summary)
    _log_event({
        "kind": "mcp_req",
        "tool": tool,
        "params": params,
    })

    # Retry once if the proc is dead or the connection closes unexpectedly.
    for attempt in range(2):
        try:
            proc = _get_mcp_proc()
        except RuntimeError as exc:
            raise RuntimeError(f"Cannot get mcp-proxy: {exc}") from exc

        try:
            resp = _send_rpc(proc, "tools/call", {"name": tool, "arguments": params})
            break   # success — fall through to response parsing
        except (TimeoutError, OSError, BrokenPipeError) as exc:
            log.warning("MCP connection error on attempt %d: %s — killing proc and retrying",
                        attempt + 1, exc)
            # Force-kill the dead proc so _get_mcp_proc will respawn.
            global _mcp_proc
            with _mcp_lock:
                if _mcp_proc is proc:
                    _mcp_proc = None
            proc.kill()
            if attempt == 1:
                raise RuntimeError(f"MCP connection failed after retry: {exc}") from exc
            time.sleep(0.5)
    else:
        raise RuntimeError("MCP call loop exhausted without a response")

    if resp is None:
        log.warning("MCP ◀  tool=%-28s  → None response", tool)
        _log_event({"kind": "mcp_resp", "tool": tool, "ms": int((time.time()-t0)*1000),
                    "status": "none", "result": None})
        return None

    if "error" in resp:
        err_msg = resp["error"].get("message", str(resp["error"]))
        log.error("MCP ✗  tool=%-28s  → error: %s", tool, err_msg)
        _log_event({"kind": "mcp_resp", "tool": tool, "ms": int((time.time()-t0)*1000),
                    "status": "error", "error": err_msg})
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
            # isError with no message text often means the AnyLog node rejected
            # the query (bad SQL, unknown table, etc).  Make that explicit.
            if not err_text:
                err_text = "(AnyLog returned isError=true with no message — check SQL/table name)"
            log.error("MCP ✗  tool=%-28s  → isError: %s", tool, err_text[:300])
            _log_event({"kind": "mcp_resp", "tool": tool, "ms": int((time.time()-t0)*1000),
                        "status": "isError", "error": err_text[:300]})
            raise RuntimeError(f"MCP tool error: {err_text}")
        texts = [c["text"] for c in result["content"] if c.get("type") == "text"]
        combined = "\n".join(texts)
        try:
            parsed = json.loads(combined)
            row_count = len(parsed) if isinstance(parsed, list) else "dict"
            ms = int((time.time() - t0) * 1000)
            log.info("MCP ◀  tool=%-28s  → %s rows  (%dms)", tool, row_count, ms)
            _log_event({"kind": "mcp_resp", "tool": tool, "ms": ms,
                        "status": "ok", "row_count": row_count,
                        "result_preview": json.dumps(parsed)[:600] if isinstance(parsed, list) and len(parsed) > 0 else str(parsed)[:600]})
            return parsed
        except json.JSONDecodeError:
            ms = int((time.time() - t0) * 1000)
            log.info("MCP ◀  tool=%-28s  → text (%d chars)  (%dms)", tool, len(combined), ms)
            _log_event({"kind": "mcp_resp", "tool": tool, "ms": ms,
                        "status": "ok", "result_preview": combined[:600]})
            return combined

    ms = int((time.time() - t0) * 1000)
    log.info("MCP ◀  tool=%-28s  → raw result  (%dms)", tool, ms)
    _log_event({"kind": "mcp_resp", "tool": tool, "ms": ms,
                "status": "ok", "result_preview": str(result)[:600]})
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
    import uuid as _uuid
    g._t0 = _time.time()
    g._req_id = str(_uuid.uuid4())[:8]
    # Skip logging for the debug/log endpoints themselves to avoid noise
    if request.path.startswith("/api/log") or request.path == "/debug":
        return
    if request.method in ("OPTIONS",):
        return
    if request.method == "POST":
        body = request.get_data(as_text=True)
        log.info("HTTP ▶  %s %s  body=%s", request.method, request.path,
                 body[:300] if body else "(empty)")
        _log_event({
            "kind": "http_req",
            "req_id": g._req_id,
            "method": request.method,
            "path": request.path,
            "body": body[:600] if body else None,
        })
    else:
        log.info("HTTP ▶  %s %s  args=%s", request.method, request.path,
                 dict(request.args))
        _log_event({
            "kind": "http_req",
            "req_id": g._req_id,
            "method": request.method,
            "path": request.path,
            "args": dict(request.args),
        })

@app.after_request
def _after(response):
    from flask import g
    ms = int((_time.time() - getattr(g, "_t0", _time.time())) * 1000)
    log.info("HTTP ◀  %s %s  status=%d  (%dms)",
             request.method, request.path, response.status_code, ms)
    if not (request.path.startswith("/api/log") or request.path == "/debug"
            or request.method == "OPTIONS"):
        # Capture a snippet of the response body for the log panel
        body_preview = None
        ct = response.content_type or ""
        if "json" in ct:
            try:
                body_preview = response.get_data(as_text=True)[:800]
            except Exception:
                pass
        _log_event({
            "kind": "http_resp",
            "req_id": getattr(g, "_req_id", "?"),
            "method": request.method,
            "path": request.path,
            "status": response.status_code,
            "ms": ms,
            "body": body_preview,
        })
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


# ── API Call Log — SSE stream & snapshot ──────────────────────────────────────
@app.route("/api/log/snapshot")
def api_log_snapshot():
    """Return the current circular buffer as JSON (no streaming)."""
    return jsonify({"events": _snapshot_log(), "max": _EVENT_LOG_MAX})


@app.route("/api/log/stream")
def api_log_stream():
    """
    Server-Sent Events stream of API call events.
    Each event is a JSON line:  data: {...}\\n\\n
    """
    from flask import Response, stream_with_context

    q: queue.Queue = queue.Queue(maxsize=500)
    with _event_log_listeners_lock:
        _event_log_listeners.append(q)

    # Send existing buffer first so the client sees history on connect
    snapshot = _snapshot_log()

    def generate():
        try:
            # Flush history
            for ev in snapshot:
                yield f"data: {json.dumps(ev)}\n\n"
            # Stream live events
            while True:
                try:
                    ev = q.get(timeout=20)
                    yield f"data: {json.dumps(ev)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        except GeneratorExit:
            pass
        finally:
            with _event_log_listeners_lock:
                try:
                    _event_log_listeners.remove(q)
                except ValueError:
                    pass

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── Debug / Log Panel ─────────────────────────────────────────────────────────
_DEBUG_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>MCP Bridge · API Log</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{background:#0d1117;color:#c9d1d9;font-family:'Courier New',monospace;font-size:12px;height:100vh;display:flex;flex-direction:column}
#toolbar{display:flex;align-items:center;gap:10px;padding:8px 12px;background:#161b22;border-bottom:1px solid #30363d;flex-shrink:0;flex-wrap:wrap}
#toolbar h1{font-size:13px;font-weight:700;color:#58a6ff;letter-spacing:.06em;white-space:nowrap}
.tb-sep{width:1px;height:20px;background:#30363d}
button{padding:4px 10px;border-radius:4px;border:1px solid #30363d;background:#21262d;color:#c9d1d9;cursor:pointer;font-size:11px;font-family:inherit}
button:hover{border-color:#58a6ff;color:#58a6ff}
button.active{border-color:#3fb950;color:#3fb950;background:#0d2119}
#status-dot{width:8px;height:8px;border-radius:50%;background:#f85149;flex-shrink:0}
#status-dot.live{background:#3fb950;box-shadow:0 0 6px #3fb950}
#count{font-size:11px;color:#8b949e;margin-left:auto}
#filter-bar{display:flex;gap:6px;align-items:center}
#filter-bar label{color:#8b949e;font-size:11px}
#filter-input{background:#0d1117;border:1px solid #30363d;color:#c9d1d9;padding:3px 7px;border-radius:4px;font-size:11px;font-family:inherit;width:180px}
#filter-input:focus{outline:none;border-color:#58a6ff}
select{background:#0d1117;border:1px solid #30363d;color:#c9d1d9;padding:3px 7px;border-radius:4px;font-size:11px;font-family:inherit}
select:focus{outline:none;border-color:#58a6ff}
#log{flex:1;overflow-y:auto;padding:6px 0}
.ev{border-bottom:1px solid #161b22;cursor:pointer;user-select:none}
.ev:hover{background:#161b22}
.ev-hdr{display:flex;align-items:center;gap:8px;padding:5px 12px;min-height:28px}
.badge{display:inline-block;padding:1px 6px;border-radius:3px;font-size:10px;font-weight:700;letter-spacing:.05em;white-space:nowrap}
.b-http-req {background:#0d2a4a;color:#58a6ff;border:1px solid #1f6feb}
.b-http-resp{background:#0d2a4a;color:#79c0ff;border:1px solid #1f6feb}
.b-mcp-req  {background:#1a2a0d;color:#7ee787;border:1px solid #2ea043}
.b-mcp-resp {background:#1a2a0d;color:#56d364;border:1px solid #2ea043}
.b-err      {background:#2d0f0f;color:#f85149;border:1px solid #6e1a1a}
.method{color:#d29922;font-weight:700;font-size:11px}
.path{color:#c9d1d9}
.tool{color:#7ee787;font-weight:700}
.ms{color:#6e7681;font-size:10px;margin-left:auto;white-space:nowrap}
.status-ok  {color:#3fb950}
.status-err {color:#f85149}
.ts-label{color:#484f58;font-size:10px;white-space:nowrap}
.ev-body{display:none;padding:6px 12px 10px 30px;border-top:1px solid #21262d;background:#080c10}
.ev-body.open{display:block}
.ev-body pre{white-space:pre-wrap;word-break:break-all;color:#8b949e;font-size:11px;line-height:1.5;max-height:320px;overflow-y:auto}
.json-key{color:#79c0ff}
.json-str{color:#a5d6ff}
.json-num{color:#f2cc60}
.json-bool{color:#ff7b72}
.json-null{color:#8b949e}
#empty{display:none;text-align:center;padding:60px 20px;color:#484f58}
#empty.show{display:block}
::-webkit-scrollbar{width:5px;height:5px}
::-webkit-scrollbar-track{background:#0d1117}
::-webkit-scrollbar-thumb{background:#30363d;border-radius:3px}
</style>
</head>
<body>
<div id="toolbar">
  <h1>🔌 MCP Bridge · API Log</h1>
  <div class="tb-sep"></div>
  <div id="status-dot"></div>
  <span id="conn-label" style="font-size:11px;color:#8b949e">disconnected</span>
  <div class="tb-sep"></div>
  <div id="filter-bar">
    <label>Filter:</label>
    <input id="filter-input" type="text" placeholder="path, tool, status…">
    <select id="kind-filter">
      <option value="">All kinds</option>
      <option value="http_req">HTTP req</option>
      <option value="http_resp">HTTP resp</option>
      <option value="mcp_req">MCP req</option>
      <option value="mcp_resp">MCP resp</option>
    </select>
  </div>
  <div class="tb-sep"></div>
  <button id="btn-pause">⏸ Pause</button>
  <button id="btn-clear">🗑 Clear</button>
  <button id="btn-top">⬆ Top</button>
  <button id="btn-bottom">⬇ Bottom</button>
  <span id="count" style="font-size:11px;color:#484f58">0 events</span>
</div>
<div id="log"><div id="empty" class="show">No events yet — waiting for API calls…</div></div>

<script>
const log = document.getElementById('log');
const empty = document.getElementById('empty');
const dot = document.getElementById('status-dot');
const connLabel = document.getElementById('conn-label');
const countEl = document.getElementById('count');
const filterInput = document.getElementById('filter-input');
const kindFilter = document.getElementById('kind-filter');
const btnPause = document.getElementById('btn-pause');

let events = [];
let paused = false;
let es = null;

function fmtTime(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString('en-US', {hour12: false, hour:'2-digit', minute:'2-digit', second:'2-digit'}) +
    '.' + String(d.getMilliseconds()).padStart(3,'0');
}

function syntaxHL(obj) {
  const s = JSON.stringify(obj, null, 2);
  return s.replace(/("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g, m => {
    if (/^"/.test(m)) {
      if (/:$/.test(m)) return `<span class="json-key">${m}</span>`;
      return `<span class="json-str">${m}</span>`;
    }
    if (/true|false/.test(m)) return `<span class="json-bool">${m}</span>`;
    if (/null/.test(m)) return `<span class="json-null">${m}</span>`;
    return `<span class="json-num">${m}</span>`;
  });
}

function kindBadge(ev) {
  if (ev.kind === 'http_req')  return `<span class="badge b-http-req">HTTP ▶</span>`;
  if (ev.kind === 'http_resp') return `<span class="badge b-http-resp">HTTP ◀</span>`;
  if (ev.kind === 'mcp_req')   return `<span class="badge b-mcp-req">MCP ▶</span>`;
  if (ev.kind === 'mcp_resp') {
    const cls = (ev.status && ev.status !== 'ok') ? 'b-err' : 'b-mcp-resp';
    return `<span class="badge ${cls}">MCP ◀</span>`;
  }
  return `<span class="badge">${ev.kind}</span>`;
}

function mainLabel(ev) {
  if (ev.kind === 'http_req')  return `<span class="method">${ev.method}</span> <span class="path">${ev.path}</span>`;
  if (ev.kind === 'http_resp') {
    const sc = ev.status >= 400 ? 'status-err' : 'status-ok';
    return `<span class="method">${ev.method}</span> <span class="path">${ev.path}</span> <span class="${sc}">${ev.status}</span>`;
  }
  if (ev.kind === 'mcp_req')  return `<span class="tool">${ev.tool}</span>`;
  if (ev.kind === 'mcp_resp') {
    const sc = (ev.status && ev.status !== 'ok') ? 'status-err' : 'status-ok';
    const rows = ev.row_count !== undefined ? ` <span style="color:#484f58">(${ev.row_count} rows)</span>` : '';
    return `<span class="tool">${ev.tool}</span> <span class="${sc}">${ev.status||''}</span>${rows}`;
  }
  return '';
}

function bodyHTML(ev) {
  const parts = [];
  const skip = new Set(['kind','ts','req_id']);
  const obj = {};
  for (const [k,v] of Object.entries(ev)) {
    if (!skip.has(k) && v !== null && v !== undefined) obj[k] = v;
  }
  // Try to pretty-print body / result_preview as JSON
  if (obj.body) { try { obj.body = JSON.parse(obj.body); } catch(_){} }
  if (obj.result_preview) { try { obj.result_preview = JSON.parse(obj.result_preview); } catch(_){} }
  return `<pre>${syntaxHL(obj)}</pre>`;
}

function matchesFilter(ev) {
  const kf = kindFilter.value;
  if (kf && ev.kind !== kf) return false;
  const txt = filterInput.value.trim().toLowerCase();
  if (!txt) return true;
  return JSON.stringify(ev).toLowerCase().includes(txt);
}

function renderAll() {
  const filtered = events.filter(matchesFilter);
  empty.className = filtered.length === 0 ? 'show' : '';
  countEl.textContent = `${filtered.length} / ${events.length} events`;
  // Remove all existing rows
  Array.from(log.querySelectorAll('.ev')).forEach(n => n.remove());
  // Re-insert in order
  for (const ev of filtered) {
    log.appendChild(buildRow(ev));
  }
}

function buildRow(ev) {
  const div = document.createElement('div');
  div.className = 'ev';
  div.dataset.id = ev._id;
  const ms = ev.ms ? `<span class="ms">${ev.ms}ms</span>` : '';
  div.innerHTML = `
    <div class="ev-hdr">
      <span class="ts-label">${fmtTime(ev.ts)}</span>
      ${kindBadge(ev)}
      ${mainLabel(ev)}
      ${ms}
    </div>
    <div class="ev-body">${bodyHTML(ev)}</div>`;
  div.querySelector('.ev-hdr').addEventListener('click', () => {
    const body = div.querySelector('.ev-body');
    body.classList.toggle('open');
  });
  return div;
}

function appendEvent(ev) {
  ev._id = events.length;
  events.push(ev);
  if (!matchesFilter(ev)) {
    countEl.textContent = `${events.filter(matchesFilter).length} / ${events.length} events`;
    return;
  }
  empty.className = '';
  const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 60;
  const row = buildRow(ev);
  log.appendChild(row);
  countEl.textContent = `${events.filter(matchesFilter).length} / ${events.length} events`;
  if (atBottom) log.scrollTop = log.scrollHeight;
}

function connect() {
  if (es) es.close();
  dot.className = '';
  connLabel.textContent = 'connecting…';
  es = new EventSource('/api/log/stream');
  es.onopen = () => {
    dot.className = 'live';
    connLabel.textContent = 'live';
  };
  es.onmessage = e => {
    if (paused) return;
    try { appendEvent(JSON.parse(e.data)); } catch(_) {}
  };
  es.onerror = () => {
    dot.className = '';
    connLabel.textContent = 'reconnecting…';
    setTimeout(connect, 3000);
  };
}

btnPause.addEventListener('click', () => {
  paused = !paused;
  btnPause.textContent = paused ? '▶ Resume' : '⏸ Pause';
  btnPause.className = paused ? 'active' : '';
});
document.getElementById('btn-clear').addEventListener('click', () => {
  events = [];
  Array.from(log.querySelectorAll('.ev')).forEach(n => n.remove());
  empty.className = 'show';
  countEl.textContent = '0 events';
});
document.getElementById('btn-top').addEventListener('click', () => { log.scrollTop = 0; });
document.getElementById('btn-bottom').addEventListener('click', () => { log.scrollTop = log.scrollHeight; });
filterInput.addEventListener('input', renderAll);
kindFilter.addEventListener('change', renderAll);

connect();
</script>
</body>
</html>"""


@app.route("/debug")
def debug_panel():
    """Self-contained API call log panel — collapsible rows, live SSE feed."""
    return _DEBUG_PAGE


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
        "<h1>MCP Web Bridge v4.2</h1>"
        "<p>MCP: <code>" + CFG.get("mcp_url", "?") + "</code></p>"
        "<p>Endpoints: /api/status  /api/uns/databases  /api/uns/discover  "
        "/api/uns/policies  /api/tables  /api/columns  /api/databases  "
        "/api/query(POST)  /api/query/increment(POST)  /api/nodes  "
        "/api/nodes/monitor  /api/cache/clear(POST)  /api/worker/status</p>"
        "<p><a href='/debug' style='color:#58a6ff'>🔌 API Call Log Panel</a></p>"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    # global must be declared before CALL_DELAY_S is assigned in this scope
    global CALL_DELAY_S

    parser = argparse.ArgumentParser(
        description="MCP Web Bridge v4.2 — HTTP ↔ AnyLog MCP SSE proxy",
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
    print("MCP Web Bridge  v4.2  (single-worker, all MCP calls serialised)",
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
