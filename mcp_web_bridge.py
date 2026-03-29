#!/usr/bin/env python3
"""
MCP Web Bridge  v5.2
====================
Bridges HTTP REST requests from browser dashboards → MCP protocol → AnyLog network.

NEW IN v5.2
-----------
* --debug now accepts an optional integer level (default 1 when flag present):
    --debug   / --debug 1 : Python DEBUG logging + full MCP payload output (was
                            the previous --debug behaviour, unchanged).
    --debug 2             : all of level 1 PLUS step-through mode.  Before each
                            executeQuery or queryWithIncrement the worker prints
                            the full call parameters to stderr and blocks on
                            input(), pausing the entire proxy.  All queued HTTP
                            requests remain blocked while the operator reads the
                            prompt.  Press Enter to send the call, 's' to skip
                            the wait, or 'q' to disable stepping for the session.

NEW IN v5.2
-----------
* --mcp-timeout SECS : optional per-call hard kill timer.  When set, a
  threading.Timer fires after SECS seconds and closes the open SSE socket(s),
  causing the read loop to exit with EOF and raising TimeoutError back to the
  worker.  The timer is always cancelled on a successful response.  Disabled
  by default (None = wait indefinitely).  Should be set below --job-timeout.

NEW IN v5.0
-----------
* Strict single-call serialization — two fixes:
  1. McpSseClient._call now holds self._lock for the ENTIRE SSE session
     (both stream-1 and stream-2), not just for req_id assignment.  Previously
     the lock was released before any network I/O, so on the Timbergrove
     two-stream path a second call could open its stream-1 in the gap between
     this call's stream-1 closing and stream-2 opening, causing the server to
     deliver this call's response to the wrong stream.
  2. _discover_databases_from_uns sub-calls previously bypassed the worker
     queue via a side thread; now the entire discovery runs as a single queued
     job inside the worker thread.
* Removed dead _parse_sse_stream method (leftover from an earlier prototype).
* Banner and version strings updated.

NEW IN v4.4
-----------
* Replaced mcp-proxy subprocess with a direct McpSseClient class.
  Eliminates the asyncio flush-delay bug that caused requests to appear unsent.
* Per-call SSE connections: each RPC opens a fresh GET /mcp/sse, gets the
  session endpoint, POSTs the request inline, then reads the response — all
  without a persistent background connection.
* Two-stream protocol: if the server closes stream-1 before delivering the
  response (Timbergrove style), a second SSE connection receives the result.

NEW IN v4.3
-----------
* /api/query and /api/query/increment omit the "nodes" parameter entirely so
  AnyLog routes each query to all nodes hosting the table automatically.
* /api/query/increment now forwards the optional "where" clause.
* Query endpoints log dbms/table/sql at INFO level for easy verification.

NEW IN v4.0 / v4.1
-------------------
* --mcp-url  CLI argument  : choose which MCP SSE server to connect to at launch
* --port / --host          : bind address control
* UNS-aware database discovery via /api/uns/databases.

ARCHITECTURE
------------
ONE McpSseClient, ONE worker thread, ONE MCP call at a time.
HTTP endpoints NEVER call MCP directly — they post a Job to the worker queue
and BLOCK on job.done.wait() until the worker signals completion.
The worker executes jobs strictly one at a time with CALL_DELAY_S between them.
No MCP call can start while another is running.

CACHE
-----
Results stored in a TTL cache keyed by (tool, canonical-params).
Metadata cached for CACHE_TTL_S; sensor/query data for DATA_TTL_S.
Duplicate concurrent requests for the same key share one in-flight job.
"""

import argparse
import json
import logging
import os
import queue
import ssl
import sys
import requests
import subprocess
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
DEFAULT_MCP_PROXY_PATH = os.path.join(
    os.path.dirname(__file__),
    "venv",
    "Scripts" if sys.platform == "win32" else "bin",
    "mcp-proxy.exe"
)

# DEFAULT_MCP_PROXY_PATH = "/Users/mdavidson58/Documents/AnyLog/Prove-IT/venv/bin/mcp-proxy"
DEFAULT_PORT           = 8080
DEFAULT_HOST           = "0.0.0.0"

DEFAULT_CALL_DELAY_S = 1.5
CALL_DELAY_S         = DEFAULT_CALL_DELAY_S  # pause between MCP calls
JOB_TIMEOUT_S        = 300   # max seconds an HTTP request waits for the worker
MCP_CALL_TIMEOUT_S   = None  # type: Optional[float]  per-call hard kill timer (None = disabled)
DEBUG_LEVEL          = 0     # 0=INFO  1=DEBUG (--debug)  2=DEBUG+step (--debug 2)
CACHE_TTL_S          = 300   # 5 min — metadata (tables, UNS, status)
DATA_TTL_S           = 30    # 30 s  — query results

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

# Root logger configured later in main() once CLI args are parsed.
# A minimal stderr handler is set here so any import-time messages are visible.
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, stream=sys.stderr)
log = logging.getLogger("mcp_bridge")

def _get_status(conn:str):
    try:
        response = requests.post(url=conn,
                                 headers={"Content-Type": "application/json"},
                                 data=json.dumps({
                                     "command": "get status where format=json",
                                     "User-Agent": "AnyLog/1.23"
                                 }))
        response.raise_for_status()
        print(not response.json())
    except Exception as error:
        raise Exception(f"Failed to connect to AnygLog conn: {conn} (Error: {error}")


def _configure_logging(quiet: bool, log_file: Optional[str], debug_level: int) -> None:
    """
    Reconfigure the root logger after CLI args are parsed.

    debug_level=0 → INFO  (default)
    debug_level=1 → DEBUG (--debug or --debug 1)
    debug_level=2 → DEBUG + step-through on query calls (--debug 2)
    quiet=True    → WARNING level (errors/warnings only); overridden by debug_level >= 1
    log_file      → also write to the given file path (appends, UTF-8)
    """
    level = logging.DEBUG if debug_level >= 1 else (logging.WARNING if quiet else logging.INFO)

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


_QUERY_TOOLS = {"executeQuery", "queryWithIncrement"}


def _debug2_step(tool: str, params: Dict[str, Any]) -> None:
    """
    Debug level-2 step-through gate.

    Called from the worker thread before dispatching executeQuery or
    queryWithIncrement.  Prints the full call details to stderr and blocks
    on input(), pausing the entire proxy until the operator presses Enter
    (or types 's' to skip this call's wait, or 'q' to disable level-2
    stepping for the rest of the session).

    Because this runs inside the single worker thread, ALL queued HTTP
    requests remain blocked while the operator reads the prompt.
    """
    sep = "─" * 65
    print(f"\n{sep}", file=sys.stderr)
    print(f"[DEBUG-2] STEP  tool={tool}", file=sys.stderr)
    print(f"[DEBUG-2] queue depth remaining: {_job_queue.qsize()}", file=sys.stderr)
    for k, v in params.items():
        v_str = str(v)
        if len(v_str) > 200:
            v_str = v_str[:200] + "…"
        print(f"[DEBUG-2]   {k} = {v_str}", file=sys.stderr)
    print(f"{sep}", file=sys.stderr)
    print("[DEBUG-2] Press Enter to send  |  's' skip wait  |  'q' quit stepping",
          file=sys.stderr, end=" ", flush=True)
    try:
        ans = input().strip().lower()
    except EOFError:
        # stdin not a tty (e.g. piped); skip silently
        ans = "s"
    if ans == "q":
        global DEBUG_LEVEL
        DEBUG_LEVEL = 1
        print("[DEBUG-2] Stepping disabled for remainder of session.", file=sys.stderr)
    print(file=sys.stderr)


def _worker() -> None:
    """Single worker: pop jobs, call MCP, set events.

    Only one MCP call is ever in-flight at any moment.  HTTP request threads
    block on job.done.wait() and do not proceed until this worker signals them.

    At DEBUG_LEVEL >= 2 the worker pauses before each executeQuery /
    queryWithIncrement and waits for operator input on stderr/stdin.
    """
    log.info("Worker thread started")
    while True:
        try:
            job: Job = _job_queue.get(timeout=5)
        except queue.Empty:
            continue

        qd = _job_queue.qsize()
        log.info("WORKER dequeue  tool=%-28s  queue_remaining=%d", job.tool, qd)

        # ── Debug level-2 step-through ────────────────────────────────────────
        if DEBUG_LEVEL >= 2 and job.tool in _QUERY_TOOLS:
            _debug2_step(job.tool, job.params)

        try:
            if job.tool == "__uns_discover_databases__":
                result = _discover_databases_from_uns()
                cache_set(job.cache_key, result)
            else:
                result = _call_mcp(job.tool, job.params)
                cache_set(job.cache_key, result)
            job.result = result
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

# ---------------------------------------------------------------------------
# McpSseClient -- direct SSE client, no mcp-proxy subprocess
# ---------------------------------------------------------------------------
# MCP-over-SSE protocol:
#   1. GET <base>/mcp/sse   -- persistent SSE stream; first event type
#                              'endpoint' gives the POST URL for this session.
#   2. POST <endpoint>      -- send JSON-RPC request (immediate HTTP POST).
#   3. SSE stream delivers responses as 'message' events matched by request id.
#
# This replaces the mcp-proxy subprocess approach. The subprocess approach
# failed because mcp-proxy is an async Python (anyio/httpx) process that
# buffers its outgoing HTTP calls internally and only flushes when the asyncio
# event loop gets CPU or the process exits -- making requests appear unsent
# until the bridge was killed.

class McpSseClient:
    """Direct MCP-over-SSE client using requests. Thread-safe.

    The AnyLog MCP server uses a per-request SSE model:
      - Each call opens a fresh GET /mcp/sse connection
      - The server immediately sends an 'endpoint' event with a session POST URL
      - The server then closes (or keeps open) the SSE stream to deliver the response
      - The client POSTs the JSON-RPC message to the session endpoint
      - The response arrives as a 'message' SSE event on the same stream

    We therefore open a new SSE connection per RPC call rather than trying to
    maintain a persistent connection across calls.
    """

    def __init__(self, base_url, verify_ssl=False, rpc_timeout=120.0):
        # Normalise base_url to host root (strip trailing /mcp/sse if present)
        _stripped = base_url.rstrip("/")
        if _stripped.endswith("/mcp/sse"):
            _stripped = _stripped[: -len("/mcp/sse")]
        self._base        = _stripped
        self._verify      = verify_ssl
        self._rpc_timeout = rpc_timeout
        self._req_id      = 0
        self._lock        = threading.Lock()
        self._session     = requests.Session()
        self._session.verify = verify_ssl

        if base_url.startswith("http://") and ":320" in base_url:
            log.warning(
                "McpSseClient: URL uses http:// on AnyLog TLS port (%s). "
                "Connection will likely hang -- use https://", base_url
            )

        # No handshake needed here -- the AnyLog MCP server does not require
        # an initialize round-trip before tool calls.  Each _call() opens its
        # own SSE connection so there is no persistent state to set up.
        log.info("McpSseClient configured -> %s", self._base)

    # -- Per-call SSE session -------------------------------------------------

    def _call(self, method, params):
        """Open SSE stream, get session endpoint, POST the RPC, read response.

        AnyLog MCP SSE protocol:
          1. GET /mcp/sse  -> server sends endpoint event with session POST URL
          2. POST <endpoint> with JSON-RPC payload
          3. Server sends response as a message event on the SAME SSE stream

        The stream must stay open through steps 1-3. We read lines one at a
        time; when we see the endpoint event we immediately POST (still inside
        the streaming read loop), then continue reading until the message event
        arrives with our req_id.

        If the server closes the stream before the response (Timbergrove style),
        we open a second SSE connection to receive the result.

        IMPORTANT — self._lock is held for the ENTIRE call (connect through
        final response).  This prevents concurrent callers from interleaving
        their stream-1/stream-2 connections: the AnyLog server delivers a
        response to the next SSE connection that opens, so two overlapping
        calls would steal each other's responses.  In practice only the single
        worker thread calls this method, but the lock makes the guarantee
        explicit and robust against future changes.
        """
        sse_url = "{}/mcp/sse".format(self._base)

        # Hold self._lock across THE ENTIRE call — both SSE streams.
        # The AnyLog server delivers a response to the next SSE connection that
        # opens after the POST.  If two calls overlapped, stream-2 of call A
        # could connect after stream-1 of call B, causing the server to send
        # call A's response to call B's stream and vice-versa.  Holding the
        # lock end-to-end prevents any such interleaving.
        with self._lock:
            self._req_id += 1
            req_id = self._req_id

            log.info("McpSseClient _call  id=%d  tool=%s  url=%s", req_id, params.get("name", method), sse_url)

            payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
            log.debug("McpSseClient _call  id=%d  payload=%s", req_id, json.dumps(payload))

            # ── Per-call hard timeout ─────────────────────────────────────────
            # When MCP_CALL_TIMEOUT_S is set, a Timer fires after that many
            # seconds and closes any open response socket.  Closing the socket
            # causes resp.raw.read(1) to return b"" (EOF), which breaks the
            # _sse_loop read loop.  We detect the kill via _timed_out[0].
            _timed_out  = [False]
            _open_resps = []   # responses currently open; timer closes them all

            def _kill_call():
                _timed_out[0] = True
                log.warning("McpSseClient TIMEOUT  id=%d  after %.1fs  -- closing socket(s)",
                             req_id, MCP_CALL_TIMEOUT_S)
                for r in list(_open_resps):
                    try:
                        r.close()
                    except Exception:
                        pass

            timer = None
            if MCP_CALL_TIMEOUT_S is not None:
                timer = threading.Timer(MCP_CALL_TIMEOUT_S, _kill_call)
                timer.daemon = True
                timer.start()
                log.debug("McpSseClient call-timer  id=%d  deadline=%.1fs", req_id, MCP_CALL_TIMEOUT_S)

            try:
                def _sse_loop(resp, already_posted, stream_num=1):
                    """Iterate one SSE stream. Returns (post_url, result, did_post).

                    Reads one byte at a time directly from the urllib3 socket to
                    bypass all Python-level read buffering.  requests.iter_content()
                    and http.client.BufferedReader accumulate data internally before
                    releasing chunks, causing multi-second delivery delays on small
                    SSE events.
                    """
                    _open_resps.append(resp)
                    event_type = "message"
                    data_buf = None
                    post_url = None
                    result = None
                    tail = ""
                    posted = already_posted
                    t_start = time.time()
                    lines_read = 0
                    events_seen = []
                    log.debug("McpSseClient stream-%d open  id=%d", stream_num, req_id)
                    try:
                        while True:
                            line = None
                            while line is None:
                                b = resp.raw.read(1)
                                if not b:
                                    # EOF — either server closed or timer killed the socket
                                    if tail:
                                        line = tail.rstrip("\r")
                                        tail = ""
                                    break
                                ch = b.decode("utf-8", errors="replace")
                                if ch == "\n":
                                    line = tail.rstrip("\r")
                                    tail = ""
                                else:
                                    tail += ch
                            if line is None:
                                break
                            lines_read += 1

                            if not line:
                                if data_buf is not None:
                                    if event_type == "endpoint":
                                        ep = data_buf.strip()
                                        post_url = (ep if ep.startswith("http")
                                                    else "{}{}".format(self._base, ep))
                                        t_ep = time.time() - t_start
                                        log.info("McpSseClient stream-%d endpoint  id=%d  url=%s  (%.1fs)",
                                                 stream_num, req_id, post_url, t_ep)
                                        events_seen.append("endpoint@{:.1f}s".format(t_ep))
                                        if not posted:
                                            t_post = time.time()
                                            pr = self._session.post(
                                                post_url, json=payload,
                                                timeout=10, verify=self._verify)
                                            pr.raise_for_status()
                                            log.info("McpSseClient stream-%d POST    id=%d  tool=%s  status=%d  (%.0fms)",
                                                     stream_num, req_id,
                                                     params.get("name", method),
                                                     pr.status_code,
                                                     (time.time()-t_post)*1000)
                                            posted = True
                                    elif event_type == "message":
                                        t_msg = time.time() - t_start
                                        try:
                                            msg = json.loads(data_buf)
                                            msg_id = msg.get("id")
                                            if msg_id == req_id:
                                                resp_size = len(data_buf)
                                                log.info("McpSseClient stream-%d message id=%d matched  resp_chars=%d  elapsed=%.1fs",
                                                         stream_num, req_id, resp_size, t_msg)
                                                log.debug("McpSseClient stream-%d message id=%d  raw=%.400s",
                                                          stream_num, req_id, data_buf)
                                                result = msg
                                                break
                                            else:
                                                method_name = msg.get("method", "?")
                                                log.debug("McpSseClient stream-%d message id=%s method=%s  (%.1fs) -- skipped",
                                                          stream_num, msg_id, method_name, t_msg)
                                                events_seen.append("msg:{}@{:.1f}s".format(method_name, t_msg))
                                        except json.JSONDecodeError:
                                            log.debug("McpSseClient stream-%d non-JSON data ignored: %.80s",
                                                      stream_num, data_buf)
                                    else:
                                        log.debug("McpSseClient stream-%d event type=%s data=%.120s",
                                                  stream_num, event_type, data_buf)
                                        events_seen.append("{}@{:.1f}s".format(event_type, time.time()-t_start))
                                event_type = "message"
                                data_buf = None
                            elif line.startswith("event:"):
                                event_type = line[6:].strip()
                            elif line.startswith("data:"):
                                data_buf = line[5:].strip()
                    finally:
                        elapsed = time.time() - t_start
                        log.info("McpSseClient stream-%d closed  id=%d  lines=%d  events=%s  elapsed=%.1fs  result=%s",
                                 stream_num, req_id, lines_read,
                                 ",".join(events_seen) if events_seen else "none",
                                 elapsed,
                                 "ok" if result is not None else ("posted" if posted else "no-post"))
                        resp.close()
                        try:
                            _open_resps.remove(resp)
                        except ValueError:
                            pass
                    return post_url, result, posted

                # --- First SSE connection ---
                try:
                    resp1 = self._session.get(sse_url, stream=True,
                                              timeout=(10, None), verify=self._verify)
                    resp1.raise_for_status()
                    resp1.raw.decode_content = True
                except Exception as exc:
                    raise RuntimeError("McpSseClient SSE connect failed: {}".format(exc)) from exc

                post_url, result, did_post = _sse_loop(resp1, already_posted=False, stream_num=1)

                # Check timeout before acting on the result
                if _timed_out[0]:
                    raise TimeoutError(
                        "McpSseClient: call id={} timed out after {}s".format(
                            req_id, MCP_CALL_TIMEOUT_S))

                if post_url is None:
                    raise TimeoutError("McpSseClient: no SSE endpoint from {}".format(sse_url))

                if result is not None:
                    return result  # single-stream path (mark-demo style)

                if not did_post:
                    raise RuntimeError(
                        "McpSseClient: SSE stream closed before endpoint was received")

                # --- Second SSE connection (Timbergrove style) ---
                # Stream-1 closed without the response.  Open stream-2 immediately —
                # still inside self._lock so no other call can sneak a connection in
                # between and steal this call's response from the server.
                log.info("McpSseClient stream-1 closed without response -- opening stream-2  id=%d", req_id)
                try:
                    resp2 = self._session.get(sse_url, stream=True,
                                              timeout=(10, None), verify=self._verify)
                    resp2.raise_for_status()
                    resp2.raw.decode_content = True
                except Exception as exc:
                    raise RuntimeError(
                        "McpSseClient SSE reconnect failed: {}".format(exc)) from exc

                _, result, _ = _sse_loop(resp2, already_posted=True, stream_num=2)

                if _timed_out[0]:
                    raise TimeoutError(
                        "McpSseClient: call id={} timed out after {}s".format(
                            req_id, MCP_CALL_TIMEOUT_S))

                if result is None:
                    raise TimeoutError(
                        "McpSseClient: no response for id={} method={}".format(req_id, method))
                return result

            finally:
                if timer is not None:
                    timer.cancel()

    def call_tool(self, tool, params):
        return self._call("tools/call", {"name": tool, "arguments": params})

    def is_alive(self):
        return True  # stateless -- each call opens its own connection


# McpSseClient is stateless (per-call connections) -- create once at first use
_mcp_client      = None   # type: Optional[McpSseClient]
_mcp_client_lock = threading.Lock()


def _get_mcp_client():
    # type: () -> McpSseClient
    global _mcp_client
    with _mcp_client_lock:
        if _mcp_client is None:
            log.info("Creating McpSseClient -> %s", CFG["mcp_url"])
            _mcp_client = McpSseClient(
                base_url=CFG["mcp_url"],
                verify_ssl=CFG.get("verify_ssl", False),
                rpc_timeout=JOB_TIMEOUT_S - 10,  # slightly under HTTP timeout
            )
        return _mcp_client


def _call_mcp(tool: str, params: Dict[str, Any]) -> Any:
    """Call an MCP tool via McpSseClient. Called ONLY from the worker thread."""
    t0 = time.time()

    # Build the full JSON-RPC payload that will be sent to the MCP server
    mcp_payload = {"jsonrpc": "2.0", "method": "tools/call",
                   "params": {"name": tool, "arguments": params}}
    payload_json = json.dumps(mcp_payload)
    payload_chars = len(payload_json)

    log.info("MCP >  tool=%-28s  params=%s", tool, json.dumps(params))
    log.debug("MCP >  tool=%-28s  payload_chars=%d  full_json=%s",
              tool, payload_chars, payload_json)
    _log_event({"kind": "mcp_req", "tool": tool, "params": params})

    try:
        client = _get_mcp_client()
    except Exception as exc:
        raise RuntimeError("Cannot connect to MCP server: {}".format(exc)) from exc

    resp = client.call_tool(tool, params)
    ms_total = int((time.time() - t0) * 1000)

    # Log the raw JSON response at DEBUG level
    log.debug("MCP <  tool=%-28s  total_ms=%d  raw_resp=%s",
              tool, ms_total, json.dumps(resp) if resp is not None else "None")

    if resp is None:
        log.warning("MCP <  tool=%-28s  -> None response  (%dms)", tool, ms_total)
        _log_event({"kind": "mcp_resp", "tool": tool,
                    "ms": ms_total, "status": "none", "result": None})
        return None

    if "error" in resp:
        err_msg = resp["error"].get("message", str(resp["error"]))
        log.error("MCP x  tool=%-28s  -> error: %s  (%dms)", tool, err_msg, ms_total)
        _log_event({"kind": "mcp_resp", "tool": tool,
                    "ms": ms_total, "status": "error", "error": err_msg})
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
            if not err_text:
                err_text = "(AnyLog returned isError=true with no message -- check SQL/table name)"
            log.error("MCP x  tool=%-28s  -> isError: %s  (%dms)", tool, err_text, ms_total)
            _log_event({"kind": "mcp_resp", "tool": tool,
                        "ms": ms_total, "status": "isError", "error": err_text})
            raise RuntimeError("MCP tool error: {}".format(err_text))

        texts = [c["text"] for c in result["content"] if c.get("type") == "text"]
        combined = "\n".join(texts)
        resp_chars = len(combined)
        try:
            parsed = json.loads(combined)
            row_count = len(parsed) if isinstance(parsed, list) else "dict"
            # Show first row at DEBUG for data inspection
            if isinstance(parsed, list) and parsed:
                log.debug("MCP <  tool=%-28s  first_row=%s",
                          tool, json.dumps(parsed[0]))
            elif isinstance(parsed, dict):
                log.debug("MCP <  tool=%-28s  result_keys=%s",
                          tool, list(parsed.keys()))
            log.info("MCP <  tool=%-28s  -> %s rows  resp_chars=%d  total_ms=%d",
                     tool, row_count, resp_chars, ms_total)
            _log_event({"kind": "mcp_resp", "tool": tool, "ms": ms_total,
                        "status": "ok", "row_count": row_count, "result": parsed})
            return parsed
        except json.JSONDecodeError:
            log.info("MCP <  tool=%-28s  -> text  resp_chars=%d  total_ms=%d",
                     tool, resp_chars, ms_total)
            log.debug("MCP <  tool=%-28s  text_preview=%.200s", tool, combined)
            _log_event({"kind": "mcp_resp", "tool": tool, "ms": ms_total,
                        "status": "ok", "result": combined})
            return combined

    resp_chars = len(json.dumps(result))
    log.info("MCP <  tool=%-28s  -> raw result  resp_chars=%d  total_ms=%d",
             tool, resp_chars, ms_total)
    log.debug("MCP <  tool=%-28s  raw=%s", tool, json.dumps(result)[:500])
    _log_event({"kind": "mcp_resp", "tool": tool, "ms": ms_total,
                "status": "ok", "result": result})
    return result


# UNS database discovery
# ---------------------------------------------------------------------------
def _discover_databases_from_uns() -> List[str]:
    """
    Query all UNS policies for this MCP connector and collect the unique set
    of database names referenced. Falls back to listNetworkDatabases if UNS
    has no 'dbms' fields.

    IMPORTANT: this function is called from a Job that runs inside the worker
    thread.  It must NOT call _call_mcp() directly (that would be a re-entrant
    call on the worker) nor submit jobs to the queue (deadlock: worker waiting
    on itself).  Instead it calls _call_mcp() directly — which is safe here
    because we ARE the worker.  All serialization is enforced by the fact that
    only one Job runs at a time.
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
            time.sleep(CALL_DELAY_S)   # respect inter-call pacing
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
        time.sleep(CALL_DELAY_S)       # respect inter-call pacing
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
                 body if body else "(empty)")
        _log_event({
            "kind": "http_req",
            "req_id": g._req_id,
            "method": request.method,
            "path": request.path,
            "body": body if body else None,
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
        # Capture full response body for the log panel
        resp_body = None
        ct = response.content_type or ""
        if "json" in ct:
            try:
                resp_body = response.get_data(as_text=True)
            except Exception:
                pass
        _log_event({
            "kind": "http_resp",
            "req_id": getattr(g, "_req_id", "?"),
            "method": request.method,
            "path": request.path,
            "status": response.status_code,
            "ms": ms,
            "body": resp_body,
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

    # Submit discovery as a single opaque job so ALL MCP sub-calls inside
    # _discover_databases_from_uns run inside the worker thread and are
    # therefore serialized with every other job.  The side-thread approach
    # used in v4.x allowed _call_mcp() to run concurrently with the worker.
    job = Job(
        tool="__uns_discover_databases__",
        params={},
        cache_key=cache_key,
        cache_ttl=CACHE_TTL_S,
    )
    with _pending_lock:
        _pending_jobs[cache_key] = job
    _job_queue.put(job)
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
    if not dbms or not sql:
        return jsonify({"error": "body must contain {dbms, sql}"}), 400

    # No "nodes" param — AnyLog automatically routes to all nodes hosting the table.
    log.info("api_query  dbms=%s  sql=%.120s", dbms, sql)
    params = {"dbms": dbms, "sql": sql}
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

    # No "nodes" param — AnyLog automatically routes to all nodes hosting the table.
    log.info("api_query_increment  dbms=%s  table=%s  where=%.80s",
             body["dbms"], body["table"], body.get("where", ""))

    dbms         = body["dbms"]
    table        = body["table"]
    time_col     = body["timeColumn"]
    start_time   = body["startTime"]
    end_time     = body["endTime"]
    interval_len = int(body["intervalLength"])
    time_unit    = body["timeUnit"]
    projections  = body["projections"]
    where        = body.get("where", "")

    # Try queryWithIncrement first; fall back to executeQuery with increments()
    # AnyLog SQL syntax for time-bucketed aggregation:
    #   SELECT increments(<unit>, <len>, <timecol>), <agg_cols>
    #   FROM <table> WHERE <timecol> >= <start> AND <timecol> <= <end> [AND <where>]
    params_inc = {
        "dbms": dbms, "table": table, "timeColumn": time_col,
        "startTime": start_time, "endTime": end_time,
        "intervalLength": interval_len, "timeUnit": time_unit,
        "projections": projections,
    }
    if where:
        params_inc["where"] = where

    # Track per-URL whether queryWithIncrement is supported.
    # Avoids a wasted round-trip on every call when the tool is unavailable.
    _qwi_supported = CFG.get("queryWithIncrement_supported", True)

    result, err = None, None
    if _qwi_supported:
        result, err = _run_job("queryWithIncrement", params_inc, ttl=DATA_TTL_S)
        if err and ("Unable to process" in err or "not found" in err.lower()
                    or "unknown" in err.lower()):
            log.warning("queryWithIncrement not supported on %s -- disabling for this session",
                        CFG.get("mcp_url", "?"))
            CFG["queryWithIncrement_supported"] = False
            err = None  # trigger fallback below
    else:
        log.debug("queryWithIncrement disabled for this session -- using increments() SQL directly")

    if result is None and err is None:
        # Build an increments() SQL query via executeQuery
        agg_cols = ", ".join(projections)
        # AnyLog increments() WHERE: only lower bound needed; upper bound is implicit
        where_clause = "WHERE {tc} >= {st}".format(tc=time_col, st=start_time)
        if where:
            where_clause += " AND {}".format(where)
        sql = ("SELECT increments({unit}, {length}, {tc}), {cols} "
               "FROM {table} {where}").format(
            unit=time_unit, length=interval_len, tc=time_col,
            cols=agg_cols, table=table, where=where_clause)
        log.info("increment SQL: %s", sql)
        result, err = _run_job("executeQuery", {"dbms": dbms, "sql": sql},
                               ttl=DATA_TTL_S)

    if result is None and err:
        # increments() also failed -- fall back to raw data query and
        # return it; the dashboard will handle aggregation client-side
        log.warning("increments() SQL failed (%s) -- falling back to raw SELECT", err)
        agg_cols_raw = ", ".join(
            c for c in projections
            if not any(c.startswith(fn) for fn in ("avg(", "min(", "max(", "sum(", "count("))
        ) or time_col
        raw_sql = ("SELECT {cols} FROM {table} WHERE {tc} >= {st}{where_extra} "
                   "ORDER BY {tc} DESC LIMIT 500").format(
            cols=agg_cols_raw, table=table, tc=time_col, st=start_time,
            where_extra=" AND {}".format(where) if where else "")
        log.info("raw fallback SQL: %s", raw_sql)
        result, err = _run_job("executeQuery", {"dbms": dbms, "sql": raw_sql},
                               ttl=DATA_TTL_S)

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
  // Try to pretty-print body / result as JSON
  if (obj.body) { try { obj.body = JSON.parse(obj.body); } catch(_){} }
  if (obj.result) { try { obj.result = (typeof obj.result === "string") ? JSON.parse(obj.result) : obj.result; } catch(_){} }
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
        "<h1>MCP Web Bridge v5.2</h1>"
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
    global JOB_TIMEOUT_S
    global MCP_CALL_TIMEOUT_S
    global DEBUG_LEVEL

    parser = argparse.ArgumentParser(
        description="MCP Web Bridge v5.2 -- HTTP <-> AnyLog MCP SSE proxy (strict single-call serialization)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--mcp-url",
        default=DEFAULT_MCP_SERVER_URL,
        help="MCP SSE server URL to connect to (e.g. https://172.79.89.206:32049/mcp/sse)",
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
        "--job-timeout",
        type=int,
        default=JOB_TIMEOUT_S,
        help="Seconds an HTTP request will wait for the MCP worker (default: %(default)s)",
    )
    parser.add_argument(
        "--mcp-timeout",
        type=float,
        default=None,
        metavar="SECS",
        help=(
            "Hard kill timeout (seconds) for each individual MCP call.  "
            "If the MCP server does not respond within this many seconds the "
            "SSE socket is closed and the call fails with a TimeoutError.  "
            "Omit (default) to wait indefinitely for a response.  "
            "Should be less than --job-timeout."
        ),
    )
    parser.add_argument(
        "--debug",
        type=int,
        nargs="?",
        const=1,
        default=0,
        metavar="LEVEL",
        help=(
            "Debug level.  --debug or --debug 1: enable Python DEBUG logging "
            "and verbose MCP payload output.  --debug 2: all of level 1 plus "
            "step-through mode — the worker pauses before each executeQuery / "
            "queryWithIncrement and waits for Enter on stdin before sending the "
            "call (press 's' to skip the wait, 'q' to disable stepping).  "
            "Default: 0 (INFO logging)."
        ),
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
    CFG["port"]       = args.port
    CFG["host"]       = args.host

    CALL_DELAY_S       = args.call_delay
    JOB_TIMEOUT_S      = args.job_timeout
    MCP_CALL_TIMEOUT_S = args.mcp_timeout
    DEBUG_LEVEL        = args.debug if args.debug is not None else 0

    # Apply logging configuration (quiet / debug / log-file)
    _configure_logging(quiet=args.quiet, log_file=args.log_file, debug_level=DEBUG_LEVEL)

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
    _debug_labels = {0: "INFO", 1: "DEBUG (verbose)", 2: "DEBUG-2 (step-through on query calls)"}
    log_level_label = _debug_labels.get(DEBUG_LEVEL, f"DEBUG-{DEBUG_LEVEL}")
    if args.quiet and DEBUG_LEVEL == 0:
        log_level_label = "WARNING (quiet)"

    print("=" * 65, file=sys.stderr)
    print("MCP Web Bridge  v5.2  (strict single-call serialization, direct SSE client)",
          file=sys.stderr)
    print("=" * 65, file=sys.stderr)
    print(f"  MCP URL   : {CFG['mcp_url']}",  file=sys.stderr)
    print(f"  Listen    : {scheme}://{CFG['host']}:{CFG['port']}", file=sys.stderr)
    if use_ssl:
        print(f"  TLS cert  : {cert_path}", file=sys.stderr)
        print(f"  TLS key   : {key_path}",  file=sys.stderr)
    print(f"  Call delay: {CALL_DELAY_S}s between MCP calls", file=sys.stderr)
    print(f"  Job timeout: {JOB_TIMEOUT_S}s per HTTP request  (SSE endpoint + query latency)", file=sys.stderr)
    mcp_to_label = f"{MCP_CALL_TIMEOUT_S}s" if MCP_CALL_TIMEOUT_S is not None else "disabled"
    print(f"  MCP timeout: {mcp_to_label} per individual MCP call (--mcp-timeout)", file=sys.stderr)
    print(f"  Log level : {log_level_label}", file=sys.stderr)
    if args.log_file:
        print(f"  Log file  : {args.log_file}", file=sys.stderr)
    print("=" * 65, file=sys.stderr)
    print(file=sys.stderr)

    _get_status(conn=CFG['mcp_url'].split("/mcp")[0])
    start_worker()
    app.run(
        host=CFG["host"],
        port=CFG["port"],
        debug=(DEBUG_LEVEL >= 1),
        threaded=True,
        use_reloader=False,
        ssl_context=ssl_context,   # None → plain HTTP; SSLContext → HTTPS
    )


if __name__ == "__main__":
    main()
