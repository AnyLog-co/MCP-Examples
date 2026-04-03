#!/usr/bin/env python3
"""
anylog_proxy.py  —  Unified AnyLog Proxy  v1.0
===============================================
A single Flask proxy-generic that works in two modes, auto-detected from --anylog-url:

  REST  mode  http://host:port            → transparent pass-through to AnyLog REST API
  MCP   mode  http://host:port/mcp/sse   → bridges HTTP ↔ MCP/SSE protocol

BODY FORMATS ACCEPTED AT /api/query  (both modes)
--------------------------------------------------
  AnyLog REST  {"command": "sql {dbms} format=json:list and stat=false {sql}",
                "User-Agent": "AnyLog/1.23", "destination": "network"}

  Simple       {"dbms": "my_db", "sql": "SELECT ..."}

USAGE
-----
  # REST mode
  python3 anylog_proxy.py --anylog-url http://66.175.217.145:32349

  # MCP mode  (auto-detected from /mcp/sse suffix)
  python3 anylog_proxy.py --anylog-url https://host:32049/mcp/sse

  # With HTML dashboards
  python3 anylog_proxy.py --anylog-url http://host:32349 --html-dir ../html

ENDPOINTS  (both modes)
-----------------------
  POST /api/query              dual-format query
  POST /api/query/increment    time-bucketed aggregation
  GET  /api/status             node health check
  POST /api/cache/clear        flush result cache
  GET  /debug                  live API call log panel
  GET  /                       serves --html-dir index if set
  GET  /<file>                 serves --html-dir files if set

EXTRA ENDPOINTS  (MCP mode only)
---------------------------------
  GET  /api/uns/databases
  GET  /api/uns/discover
  GET  /api/uns/policies
  GET  /api/tables?dbms=
  GET  /api/columns?dbms=&table=
  GET  /api/databases
  GET  /api/nodes
  GET  /api/nodes/monitor
  GET  /api/worker/status
  GET  /api/log/snapshot
  GET  /api/log/stream          (SSE — used by /debug panel)
"""

import argparse
import collections
import json
import logging
import os
import queue
import re
import sys
import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import requests
from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context
from flask_cors import CORS

# ─────────────────────────────────────────────────────────────────────────────
#  Defaults  (all overridable via CLI)
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_ANYLOG_URL  = "http://127.0.0.1:32349"
DEFAULT_PORT        = 8080
DEFAULT_HOST        = "0.0.0.0"
DEFAULT_CALL_DELAY  = 1.5       # seconds between MCP calls
JOB_TIMEOUT_S       = 300       # max seconds an HTTP request waits for the worker
MCP_CALL_TIMEOUT_S: Optional[float] = None   # per-call hard kill (None = disabled)
CACHE_TTL_S         = 300       # metadata cache TTL
DATA_TTL_S          = 30        # query-result cache TTL

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, stream=sys.stderr)
log = logging.getLogger("anylog_proxy")

# Runtime config — populated in main()
CFG: Dict[str, Any] = {}

CALL_DELAY_S = DEFAULT_CALL_DELAY   # mutable global updated in main()


# ─────────────────────────────────────────────────────────────────────────────
#  Mode detection
# ─────────────────────────────────────────────────────────────────────────────
def _is_mcp_url(url: str) -> bool:
    """Return True when the URL looks like an MCP SSE endpoint."""
    return "/mcp" in url.lower()


# ─────────────────────────────────────────────────────────────────────────────
#  Body-format helpers
#
#  Two formats are accepted everywhere:
#
#    AnyLog REST  {"command": "sql {dbms} format=json:list and stat=false {sql}",
#                  "User-Agent": "AnyLog/1.23", "destination": "network"}
#
#    Simple       {"dbms": "...", "sql": "SELECT ..."}
# ─────────────────────────────────────────────────────────────────────────────
_CMD_RE = re.compile(
    r'^sql\s+(\S+)\s+'                         # "sql {dbms}"
    r'(?:[\w=:]+\s+and\s+[\w=:]+\s+)?'        # optional "format=... and stat=..."
    r'([\s\S]+)',                               # remainder = SQL
    re.IGNORECASE,
)


def parse_command(command: str) -> Tuple[Optional[str], Optional[str]]:
    """Parse 'sql {dbms} [format=... and stat=...] {sql}' → (dbms, sql)."""
    m = _CMD_RE.match(command.strip())
    return (m.group(1), m.group(2).strip()) if m else (None, None)


def build_command(dbms: str, sql: str) -> str:
    """Build the AnyLog REST command string from dbms + sql."""
    return f"sql {dbms} format=json:list and stat=false  {sql}"


def extract_dbms_sql(body: dict) -> Tuple[Optional[str], Optional[str], str]:
    """
    Extract (dbms, sql, error_msg) from a request body.
    Accepts both the AnyLog REST format {"command": ...} and {"dbms", "sql"}.
    """
    if "command" in body and "dbms" not in body:
        dbms, sql = parse_command(body["command"])
        if not dbms:
            return None, None, (
                "Cannot parse 'command' — expected: sql {dbms} [format=...] {sql}"
            )
        return dbms, sql, ""

    dbms = body.get("dbms", "")
    sql  = body.get("sql",  "")
    if not dbms or not sql:
        return None, None, "body must contain {dbms, sql} or AnyLog {command}"
    return dbms, sql, ""


# ─────────────────────────────────────────────────────────────────────────────
#  TTL cache
# ─────────────────────────────────────────────────────────────────────────────
_cache:    Dict[str, Any]   = {}
_cache_ts: Dict[str, float] = {}
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


# ─────────────────────────────────────────────────────────────────────────────
#  Event log  (circular buffer + SSE fan-out → /debug panel)
# ─────────────────────────────────────────────────────────────────────────────
_EVENT_MAX = 200
_event_log: collections.deque          = collections.deque(maxlen=_EVENT_MAX)
_event_log_lock                        = threading.Lock()
_event_listeners: List[queue.Queue]    = []
_event_listeners_lock                  = threading.Lock()


def _log_event(entry: Dict[str, Any]) -> None:
    entry.setdefault("ts", time.time())
    with _event_log_lock:
        _event_log.append(entry)
    with _event_listeners_lock:
        dead = []
        for q in _event_listeners:
            try:
                q.put_nowait(entry)
            except Exception:
                dead.append(q)
        for q in dead:
            _event_listeners.remove(q)


def _snapshot_log() -> List[Dict]:
    with _event_log_lock:
        return list(_event_log)


# ─────────────────────────────────────────────────────────────────────────────
#  REST mode  —  direct AnyLog HTTP calls
# ─────────────────────────────────────────────────────────────────────────────
def _rest_post(body: dict, timeout: int = 50) -> Tuple[Any, Optional[str]]:
    """POST a body dict directly to the configured AnyLog REST URL."""
    url = CFG["anylog_url"]
    t0  = time.time()
    try:
        resp = requests.post(
            url, json=body, timeout=timeout,
            verify=CFG.get("verify_ssl", True),
        )
        ms = int((time.time() - t0) * 1000)
        resp.raise_for_status()
        data = resp.json()
        # AnyLog returns a plain JSON array for SQL; normalise to list
        rows = data if isinstance(data, list) else data.get("Query", data.get("results", data))
        log.info("REST POST → %s  ms=%d  rows=%s", url, ms,
                 len(rows) if isinstance(rows, list) else type(rows).__name__)
        _log_event({"kind": "rest_resp", "ms": ms, "status": resp.status_code,
                    "row_count": len(rows) if isinstance(rows, list) else None})
        return rows, None
    except Exception as exc:
        ms = int((time.time() - t0) * 1000)
        log.error("REST POST failed  ms=%d  err=%s", ms, exc)
        _log_event({"kind": "rest_resp", "ms": ms, "status": "error", "error": str(exc)})
        return None, str(exc)


def rest_query(dbms: str, sql: str) -> Tuple[Any, Optional[str]]:
    """Execute a SQL query via the AnyLog REST API."""
    body = {
        "command":     build_command(dbms, sql),
        "User-Agent":  "AnyLog/1.23",
        "destination": "network",
    }
    log.info("rest_query  dbms=%s  sql=%.120s", dbms, sql)
    _log_event({"kind": "rest_req", "dbms": dbms, "sql": sql})
    return _rest_post(body)


def rest_status() -> Tuple[Any, Optional[str]]:
    """Check AnyLog node status via REST."""
    body = {"command": "get status where format=json", "User-Agent": "AnyLog/1.23"}
    return _rest_post(body, timeout=15)


def rest_increment(
    dbms: str, table: str, time_col: str,
    start_time: str, end_time: str,
    interval_len: int, time_unit: str,
    projections: List[str], where: str = "",
) -> Tuple[Any, Optional[str]]:
    """Execute a time-bucketed increments() query via REST."""
    agg_cols    = ", ".join(projections)
    where_parts = [f"{time_col} >= '{start_time}'", f"{time_col} <= '{end_time}'"]
    if where:
        where_parts.append(where)
    sql = (
        f"SELECT increments({time_unit}, {interval_len}, {time_col}), "
        f"min({time_col}) as timestamp, {agg_cols} "
        f"FROM {table} WHERE {' AND '.join(where_parts)} ORDER BY {time_col}"
    )
    log.info("rest_increment  sql=%s", sql)
    return rest_query(dbms, sql)


# ─────────────────────────────────────────────────────────────────────────────
#  MCP SSE client
#  Per-call connections, strict single-call serialization via self._lock.
#  Supports the two-stream protocol used by Timbergrove-style MCP servers.
# ─────────────────────────────────────────────────────────────────────────────
class McpSseClient:
    def __init__(self, base_url: str, verify_ssl: bool = True, rpc_timeout: int = 290):
        parsed          = urllib.parse.urlparse(base_url)
        self._base      = f"{parsed.scheme}://{parsed.netloc}"   # for relative post_url
        self._sse_url   = base_url                                # full SSE endpoint
        self._verify    = verify_ssl
        self._session   = requests.Session()
        self._session.verify = verify_ssl
        self._lock      = threading.Lock()
        self._req_id    = 0

    def _call(self, method: str, params: dict) -> Any:
        sse_url = self._sse_url

        with self._lock:
            self._req_id += 1
            req_id  = self._req_id
            payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
            log.info("MCP call  id=%d  tool=%s", req_id, params.get("name", method))
            log.debug("MCP payload: %s", json.dumps(payload))

            # Per-call hard timeout
            _timed_out  = [False]
            _open_resps = []

            def _kill():
                _timed_out[0] = True
                log.warning("MCP TIMEOUT  id=%d  after %.1fs", req_id, MCP_CALL_TIMEOUT_S)
                for r in list(_open_resps):
                    try: r.close()
                    except Exception: pass

            timer = None
            if MCP_CALL_TIMEOUT_S is not None:
                timer = threading.Timer(MCP_CALL_TIMEOUT_S, _kill)
                timer.daemon = True
                timer.start()

            try:
                def _sse_loop(resp, already_posted, stream_num=1):
                    """
                    Read one SSE stream byte-by-byte (bypasses all Python read buffering).
                    Returns (post_url, result, did_post).
                    """
                    _open_resps.append(resp)
                    event_type = "message"
                    data_buf   = None
                    post_url   = None
                    result     = None
                    tail       = ""
                    posted     = already_posted

                    try:
                        while True:
                            line = None
                            while line is None:
                                b = resp.raw.read(1)
                                if not b:                       # EOF (server close or timer kill)
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

                            if not line:                        # blank line = event boundary
                                if data_buf is not None:
                                    if event_type == "endpoint":
                                        ep       = data_buf.strip()
                                        post_url = ep if ep.startswith("http") else f"{self._base}{ep}"
                                        log.info("MCP stream-%d endpoint  id=%d  url=%s",
                                                 stream_num, req_id, post_url)
                                        if not posted:
                                            pr = self._session.post(
                                                post_url, json=payload,
                                                timeout=10, verify=self._verify,
                                            )
                                            pr.raise_for_status()
                                            posted = True
                                            log.info("MCP POST  id=%d  tool=%s  status=%d",
                                                     req_id, params.get("name", method),
                                                     pr.status_code)
                                    elif event_type == "message":
                                        try:
                                            msg = json.loads(data_buf)
                                            if msg.get("id") == req_id:
                                                result = msg
                                                break
                                            log.debug("MCP stream-%d skip msg id=%s",
                                                      stream_num, msg.get("id"))
                                        except json.JSONDecodeError:
                                            pass
                                event_type = "message"
                                data_buf   = None
                            elif line.startswith("event:"):
                                event_type = line[6:].strip()
                            elif line.startswith("data:"):
                                data_buf = line[5:].strip()
                    finally:
                        resp.close()
                        try: _open_resps.remove(resp)
                        except ValueError: pass

                    return post_url, result, posted

                # ── Stream 1
                try:
                    resp1 = self._session.get(
                        sse_url, stream=True, timeout=(10, None), verify=self._verify
                    )
                    resp1.raise_for_status()
                    resp1.raw.decode_content = True
                except Exception as exc:
                    raise RuntimeError(f"SSE connect failed: {exc}") from exc

                post_url, result, did_post = _sse_loop(resp1, False, 1)

                if _timed_out[0]:
                    raise TimeoutError(f"MCP call id={req_id} timed out")
                if post_url is None:
                    raise TimeoutError(f"No SSE endpoint from {sse_url}")
                if result is not None:
                    return result                               # single-stream path

                if not did_post:
                    raise RuntimeError("SSE stream closed before endpoint received")

                # ── Stream 2  (Timbergrove two-stream protocol)
                log.info("MCP stream-1 closed without response — opening stream-2  id=%d", req_id)
                try:
                    resp2 = self._session.get(
                        sse_url, stream=True, timeout=(10, None), verify=self._verify
                    )
                    resp2.raise_for_status()
                    resp2.raw.decode_content = True
                except Exception as exc:
                    raise RuntimeError(f"SSE reconnect failed: {exc}") from exc

                _, result, _ = _sse_loop(resp2, True, 2)

                if _timed_out[0]:
                    raise TimeoutError(f"MCP call id={req_id} timed out")
                if result is None:
                    raise TimeoutError(f"No response for id={req_id} method={method}")
                return result

            finally:
                if timer:
                    timer.cancel()

    def call_tool(self, tool: str, params: dict) -> Any:
        return self._call("tools/call", {"name": tool, "arguments": params})


_mcp_client:      Optional[McpSseClient] = None
_mcp_client_lock = threading.Lock()


def _get_mcp_client() -> McpSseClient:
    global _mcp_client
    with _mcp_client_lock:
        if _mcp_client is None:
            log.info("Creating McpSseClient → %s", CFG["anylog_url"])
            _mcp_client = McpSseClient(
                base_url   = CFG["anylog_url"],
                verify_ssl = CFG.get("verify_ssl", True),
                rpc_timeout= JOB_TIMEOUT_S - 10,
            )
    return _mcp_client


# ─────────────────────────────────────────────────────────────────────────────
#  MCP call wrapper  —  parses the MCP JSON-RPC response into a plain Python value
# ─────────────────────────────────────────────────────────────────────────────
def _call_mcp(tool: str, params: dict) -> Any:
    """Invoke an MCP tool. MUST be called only from the worker thread."""
    t0 = time.time()
    log.info("MCP > tool=%-28s  params=%s", tool, json.dumps(params))
    _log_event({"kind": "mcp_req", "tool": tool, "params": params})

    resp = _get_mcp_client().call_tool(tool, params)
    ms   = int((time.time() - t0) * 1000)

    if resp is None:
        log.warning("MCP < tool=%s → None  (%dms)", tool, ms)
        _log_event({"kind": "mcp_resp", "tool": tool, "ms": ms, "status": "none"})
        return None

    if "error" in resp:
        err = resp["error"].get("message", str(resp["error"]))
        log.error("MCP x tool=%s → error: %s  (%dms)", tool, err, ms)
        _log_event({"kind": "mcp_resp", "tool": tool, "ms": ms,
                    "status": "error", "error": err})
        raise RuntimeError(err)

    result = resp.get("result", {})
    if isinstance(result, dict) and "content" in result:
        if result.get("isError"):
            err_text = next(
                (c["text"] for c in result["content"] if c.get("type") == "text"),
                "(isError=true with no message)",
            )
            log.error("MCP x tool=%s → isError: %s  (%dms)", tool, err_text, ms)
            _log_event({"kind": "mcp_resp", "tool": tool, "ms": ms,
                        "status": "isError", "error": err_text})
            raise RuntimeError(f"MCP tool error: {err_text}")

        combined = "\n".join(
            c["text"] for c in result["content"] if c.get("type") == "text"
        )
        try:
            parsed = json.loads(combined)
            rc     = len(parsed) if isinstance(parsed, list) else "dict"
            log.info("MCP < tool=%-28s  → %s rows  ms=%d", tool, rc, ms)
            _log_event({"kind": "mcp_resp", "tool": tool, "ms": ms,
                        "status": "ok", "row_count": rc, "result": parsed})
            return parsed
        except json.JSONDecodeError:
            log.info("MCP < tool=%-28s  → text  ms=%d", tool, ms)
            _log_event({"kind": "mcp_resp", "tool": tool, "ms": ms,
                        "status": "ok", "result": combined})
            return combined

    log.info("MCP < tool=%-28s  → raw  ms=%d", tool, ms)
    _log_event({"kind": "mcp_resp", "tool": tool, "ms": ms,
                "status": "ok", "result": result})
    return result


# ─────────────────────────────────────────────────────────────────────────────
#  Job / Worker  (MCP mode — strict single-call serialization)
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Job:
    tool:      str
    params:    Dict[str, Any]
    cache_key: str
    cache_ttl: float
    done:      threading.Event = field(default_factory=threading.Event)
    result:    Any             = None
    error:     Optional[str]   = None


_job_queue: queue.Queue    = queue.Queue()
_pending:   Dict[str, Job] = {}
_pending_lock = threading.Lock()

_QUERY_TOOLS = {"executeQuery", "queryWithIncrement"}


def _enqueue(tool: str, params: dict, ttl: float = CACHE_TTL_S) -> Job:
    key = f"{tool}:{json.dumps(params, sort_keys=True)}"

    cached = cache_get(key, ttl)
    if cached is not None:
        log.info("CACHE hit  tool=%s", tool)
        j = Job(tool=tool, params=params, cache_key=key, cache_ttl=ttl)
        j.result = cached
        j.done.set()
        return j

    with _pending_lock:
        if key in _pending:
            log.info("DEDUP  tool=%s", tool)
            return _pending[key]
        j = Job(tool=tool, params=params, cache_key=key, cache_ttl=ttl)
        _pending[key] = j

    _job_queue.put(j)
    return j


def _run_job(tool: str, params: dict, ttl: float = CACHE_TTL_S) -> Tuple[Any, Optional[str]]:
    job = _enqueue(tool, params, ttl)
    if not job.done.wait(timeout=JOB_TIMEOUT_S):
        return None, "timeout waiting for MCP worker"
    if job.error:
        return None, job.error
    return job.result, None


def _worker_loop() -> None:
    log.info("MCP worker started")
    while True:
        job = _job_queue.get()
        try:
            if job.tool == "__uns_discover__":
                job.result = _discover_databases()
            else:
                if job.tool in _QUERY_TOOLS:
                    time.sleep(CALL_DELAY_S)
                job.result = _call_mcp(job.tool, job.params)
            cache_set(job.cache_key, job.result)
        except Exception as exc:
            job.error = str(exc)
            log.error("Worker: %s failed: %s", job.tool, exc)
        finally:
            with _pending_lock:
                _pending.pop(job.cache_key, None)
            job.done.set()


def start_worker() -> None:
    t = threading.Thread(target=_worker_loop, daemon=True, name="mcp-worker")
    t.start()


def _discover_databases() -> List[str]:
    """
    Walk UNS policies → collect dbms names.
    Falls back to listNetworkDatabases if UNS has no dbms fields.
    Runs inside the worker thread — may call _call_mcp() directly.
    """
    dbs: set = set()
    try:
        types    = _call_mcp("listPolicyTypes", {})
        has_uns  = False
        if isinstance(types, list):
            has_uns = any(
                (p.get("policy") if isinstance(p, dict) else p) == "uns"
                for p in types
            )
        elif isinstance(types, dict):
            has_uns = any(p.get("policy") == "uns"
                         for p in types.get("policies", []))

        if has_uns:
            time.sleep(CALL_DELAY_S)
            uns_raw  = _call_mcp("listPolicies", {"policyType": "uns"})
            policies = (uns_raw if isinstance(uns_raw, list)
                        else uns_raw.get("policies", []) if isinstance(uns_raw, dict)
                        else [])
            for p in policies:
                inner = p.get("uns", p) if isinstance(p, dict) else {}
                dbms  = inner.get("dbms") or inner.get("database")
                if dbms:
                    dbs.add(dbms)
    except Exception as exc:
        log.warning("UNS policy discovery failed: %s", exc)

    if not dbs:
        try:
            raw = _call_mcp("listNetworkDatabases", {})
            if isinstance(raw, list):
                for item in raw:
                    d = (item.get("dbms") or item.get("name")) if isinstance(item, dict) else item
                    if d:
                        dbs.add(str(d))
            elif isinstance(raw, dict):
                for item in raw.get("databases", []):
                    d = (item.get("dbms") or item.get("name")) if isinstance(item, dict) else item
                    if d:
                        dbs.add(str(d))
        except Exception as exc:
            log.error("listNetworkDatabases failed: %s", exc)

    return sorted(dbs)


# ─────────────────────────────────────────────────────────────────────────────
#  Flask app
# ─────────────────────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)


# ── Request / response logging middleware ─────────────────────────────────────
@app.before_request
def _before():
    from flask import g
    g._t0     = time.time()
    g._req_id = str(uuid.uuid4())[:8]
    if request.path.startswith("/api/log") or request.path == "/debug":
        return
    if request.method == "OPTIONS":
        return
    if request.method == "POST":
        body = request.get_data(as_text=True)
        log.info("HTTP ▶  POST %s  body=%s", request.path, body[:200] if body else "(empty)")
        _log_event({"kind": "http_req", "req_id": g._req_id,
                    "method": "POST", "path": request.path, "body": body or None})
    else:
        log.info("HTTP ▶  %s %s  args=%s", request.method, request.path, dict(request.args))
        _log_event({"kind": "http_req", "req_id": g._req_id,
                    "method": request.method, "path": request.path,
                    "args": dict(request.args)})


@app.after_request
def _after(response):
    from flask import g
    ms = int((time.time() - getattr(g, "_t0", time.time())) * 1000)
    if not (request.path.startswith("/api/log") or request.path == "/debug"
            or request.method == "OPTIONS"):
        resp_body = None
        if "json" in (response.content_type or ""):
            try: resp_body = response.get_data(as_text=True)
            except Exception: pass
        _log_event({"kind": "http_resp", "req_id": getattr(g, "_req_id", "?"),
                    "method": request.method, "path": request.path,
                    "status": response.status_code, "ms": ms, "body": resp_body})
    log.info("HTTP ◀  %s %s  %d  (%dms)",
             request.method, request.path, response.status_code, ms)
    return response


# ── Shared result extractor ───────────────────────────────────────────────────
def _to_rows(result: Any) -> List:
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for k in ("results", "rows", "Query"):
            if k in result:
                v = result[k]
                return v if isinstance(v, list) else []
    return []


# ── /api/status ───────────────────────────────────────────────────────────────
@app.route("/api/status")
def api_status():
    mode = CFG.get("mode", "rest")
    if mode == "rest":
        result, err = rest_status()
    else:
        result, err = _run_job("checkStatus", {}, ttl=30)
    if err:
        return jsonify({"status": "error", "error": err,
                        "mode": mode, "anylog_url": CFG.get("anylog_url")}), 503
    return jsonify({"status": "ok", "mode": mode,
                    "anylog_url": CFG.get("anylog_url"), "result": result})


# ── /api/query ────────────────────────────────────────────────────────────────
@app.route("/api/query",     methods=["POST"])
@app.route("/api/mcp/query", methods=["POST"])   # legacy alias
def api_query():
    body = request.get_json(force=True) or {}
    dbms, sql, err = extract_dbms_sql(body)
    if err:
        return jsonify({"error": err}), 400

    log.info("api_query  dbms=%s  sql=%.120s", dbms, sql)
    mode = CFG.get("mode", "rest")

    if mode == "rest":
        rows, err = rest_query(dbms, sql)
    else:
        result, err = _run_job("executeQuery", {"dbms": dbms, "sql": sql}, ttl=DATA_TTL_S)
        rows = _to_rows(result)

    if err:
        return jsonify({"error": err}), 500
    rows = rows if isinstance(rows, list) else []
    return jsonify({"results": rows, "row_count": len(rows), "dbms": dbms})


# ── /api/query/increment ──────────────────────────────────────────────────────
@app.route("/api/query/increment", methods=["POST"])
def api_query_increment():
    body    = request.get_json(force=True) or {}
    required = ["dbms", "table", "timeColumn", "startTime", "endTime",
                "intervalLength", "timeUnit", "projections"]
    missing = [k for k in required if k not in body]
    if missing:
        return jsonify({"error": f"missing fields: {missing}"}), 400

    dbms         = body["dbms"]
    table        = body["table"]
    time_col     = body["timeColumn"]
    start_time   = body["startTime"]
    end_time     = body["endTime"]
    interval_len = int(body["intervalLength"])
    time_unit    = body["timeUnit"]
    projections  = body["projections"]
    where        = body.get("where", "")

    log.info("api_query_increment  dbms=%s  table=%s  where=%.80s", dbms, table, where)
    mode = CFG.get("mode", "rest")

    # ── REST mode: build increments() SQL and POST directly
    if mode == "rest":
        rows, err = rest_increment(
            dbms, table, time_col, start_time, end_time,
            interval_len, time_unit, projections, where,
        )

    # ── MCP mode: try queryWithIncrement → fall back to increments() SQL
    else:
        params_qwi = {
            "dbms": dbms, "table": table, "timeColumn": time_col,
            "startTime": start_time, "endTime": end_time,
            "intervalLength": interval_len, "timeUnit": time_unit,
            "projections": projections,
        }
        if where:
            params_qwi["where"] = where

        rows, err = None, None
        if CFG.get("qwi_supported", True):
            rows_raw, err = _run_job("queryWithIncrement", params_qwi, ttl=DATA_TTL_S)
            rows          = _to_rows(rows_raw)
            if err and any(x in err.lower() for x in
                           ("unable to process", "not found", "unknown", "not supported")):
                log.warning("queryWithIncrement not supported — falling back to SQL")
                CFG["qwi_supported"] = False
                rows, err = None, None

        if rows is None and err is None:
            agg_cols     = ", ".join(projections)
            where_clause = f"WHERE {time_col} >= '{start_time}'"
            if where:
                where_clause += f" AND {where}"
            sql = (
                f"SELECT increments({time_unit}, {interval_len}, {time_col}), "
                f"min({time_col}) as timestamp, {agg_cols} "
                f"FROM {table} {where_clause}"
            )
            log.info("increment fallback SQL: %s", sql)
            rows_raw, err = _run_job("executeQuery", {"dbms": dbms, "sql": sql}, ttl=DATA_TTL_S)
            rows          = _to_rows(rows_raw)

    if err:
        return jsonify({"error": err}), 500
    rows = rows if isinstance(rows, list) else []
    return jsonify({"results": rows, "row_count": len(rows)})


# ── MCP-only helper ───────────────────────────────────────────────────────────
def _mcp_only():
    return jsonify({
        "error": "this endpoint is only available in MCP mode",
        "mode":  CFG.get("mode"),
        "tip":   "use --anylog-url http://host:port/mcp/sse to enable MCP mode",
    }), 404


# ── /api/uns/* ────────────────────────────────────────────────────────────────
@app.route("/api/uns/databases")
def api_uns_databases():
    if CFG.get("mode") != "mcp":
        return _mcp_only()
    key    = f"uns_db:{CFG['anylog_url']}"
    cached = cache_get(key, CACHE_TTL_S)
    if cached:
        return jsonify({"databases": cached, "source": "cache"})
    job = Job(tool="__uns_discover__", params={}, cache_key=key, cache_ttl=CACHE_TTL_S)
    with _pending_lock:
        _pending[key] = job
    _job_queue.put(job)
    if not job.done.wait(timeout=JOB_TIMEOUT_S * 2):
        return jsonify({"error": "timeout during UNS discovery"}), 504
    if job.error:
        return jsonify({"error": job.error}), 500
    return jsonify({"databases": job.result, "source": "uns"})


@app.route("/api/uns/discover")
def api_uns_discover():
    if CFG.get("mode") != "mcp": return _mcp_only()
    result, err = _run_job("listPolicies", {"policyType": "uns"})
    if err: return jsonify({"error": err}), 500
    policies = (result if isinstance(result, list)
                else result.get("policies", []) if isinstance(result, dict) else [])
    return jsonify({"policies": policies, "count": len(policies)})


@app.route("/api/uns/policies")
def api_uns_policies():
    if CFG.get("mode") != "mcp": return _mcp_only()
    params = {"policyType": request.args.get("type", "uns")}
    where  = request.args.get("where", "")
    if where: params["whereCond"] = where
    result, err = _run_job("listPolicies", params)
    if err: return jsonify({"error": err}), 500
    policies = (result if isinstance(result, list)
                else result.get("policies", []) if isinstance(result, dict) else [])
    return jsonify({"policies": policies, "count": len(policies)})


# ── /api/schema ───────────────────────────────────────────────────────────────
@app.route("/api/tables")
def api_tables():
    if CFG.get("mode") != "mcp": return _mcp_only()
    dbms = request.args.get("dbms", "")
    if not dbms: return jsonify({"error": "?dbms= required"}), 400
    result, err = _run_job("listTables", {"dbms": dbms})
    if err: return jsonify({"error": err}), 500
    tables = (result if isinstance(result, list)
              else result.get("tables", []) if isinstance(result, dict) else [])
    return jsonify({"dbms": dbms, "tables": tables})


@app.route("/api/columns")
def api_columns():
    if CFG.get("mode") != "mcp": return _mcp_only()
    dbms  = request.args.get("dbms",  "")
    table = request.args.get("table", "")
    if not dbms or not table:
        return jsonify({"error": "?dbms= and ?table= required"}), 400
    result, err = _run_job("listColumns", {"dbms": dbms, "table": table})
    if err: return jsonify({"error": err}), 500
    cols = (result if isinstance(result, list)
            else result.get("columns", []) if isinstance(result, dict) else [])
    return jsonify({"dbms": dbms, "table": table, "columns": cols})


@app.route("/api/databases")
def api_databases():
    if CFG.get("mode") != "mcp": return _mcp_only()
    result, err = _run_job("listNetworkDatabases", {})
    if err: return jsonify({"error": err}), 500
    dbs = (result if isinstance(result, list)
           else result.get("databases", []) if isinstance(result, dict) else [])
    return jsonify({"databases": dbs})


# ── /api/nodes ────────────────────────────────────────────────────────────────
@app.route("/api/nodes")
def api_nodes():
    if CFG.get("mode") != "mcp": return _mcp_only()
    result, err = _run_job("getNodesList", {})
    if err: return jsonify({"error": err}), 500
    return jsonify({"nodes": result})


@app.route("/api/nodes/monitor")
def api_nodes_monitor():
    if CFG.get("mode") != "mcp": return _mcp_only()
    params = {"status_type": request.args.get("type", "status")}
    nodes  = request.args.get("nodes", "")
    if nodes: params["nodes"] = nodes
    result, err = _run_job("monitorNodes", params)
    if err: return jsonify({"error": err}), 500
    return jsonify({"result": result})


# ── /api/worker/status ────────────────────────────────────────────────────────
@app.route("/api/worker/status")
def api_worker_status():
    if CFG.get("mode") != "mcp":
        return jsonify({"mode": "rest", "note": "no worker queue in REST mode"})
    with _pending_lock:
        in_flight = list(_pending.keys())
    return jsonify({
        "queue_depth":  _job_queue.qsize(),
        "in_flight":    in_flight,
        "call_delay_s": CALL_DELAY_S,
        "anylog_url":   CFG.get("anylog_url"),
    })


# ── /api/cache/clear ──────────────────────────────────────────────────────────
@app.route("/api/cache/clear", methods=["POST"])
def api_cache_clear():
    cache_clear()
    return jsonify({"status": "cleared"})


# ── /api/log/snapshot + /api/log/stream (SSE) ────────────────────────────────
@app.route("/api/log/snapshot")
def api_log_snapshot():
    return jsonify({"events": _snapshot_log(), "max": _EVENT_MAX})


@app.route("/api/log/stream")
def api_log_stream():
    q: queue.Queue = queue.Queue(maxsize=500)
    with _event_listeners_lock:
        _event_listeners.append(q)
    snapshot = _snapshot_log()

    def generate():
        try:
            for ev in snapshot:
                yield f"data: {json.dumps(ev)}\n\n"
            while True:
                try:
                    ev = q.get(timeout=20)
                    yield f"data: {json.dumps(ev)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            with _event_listeners_lock:
                try: _event_listeners.remove(q)
                except ValueError: pass

    return Response(
        stream_with_context(generate()),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── /debug  (live API call log panel) ────────────────────────────────────────
_DEBUG_PAGE = """\
<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><title>AnyLog Proxy — Call Log</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d1117;color:#c9d1d9;font-family:'Consolas','Courier New',monospace;font-size:12px}
.bar{display:flex;align-items:center;gap:10px;padding:8px 14px;
  background:#161b22;border-bottom:1px solid #30363d;flex-wrap:wrap}
.bar h1{color:#58a6ff;font-size:13px;font-weight:700;letter-spacing:.06em;flex:1}
.dot{width:8px;height:8px;border-radius:50%;background:#3d444d}
.dot.live{background:#3fb950;box-shadow:0 0 6px #3fb950}
#conn{font-size:10px;color:#8b949e}
.badge{font-size:10px;font-weight:700;padding:2px 7px;border-radius:10px;cursor:default}
.b-http-req{background:#1f3a5f;color:#58a6ff}.b-http-resp{background:#1a3a1a;color:#3fb950}
.b-mcp-req{background:#3a2a1a;color:#e3b341}.b-mcp-resp{background:#1a2a3a;color:#79c0ff}
.b-err{background:#3a1a1a;color:#f85149}.b-rest{background:#2a1a3a;color:#d2a8ff}
.toolbar{display:flex;gap:8px;padding:6px 14px;background:#161b22;
  border-bottom:1px solid #21262d;flex-wrap:wrap;align-items:center}
.toolbar input{flex:1;padding:4px 8px;border-radius:4px;border:1px solid #30363d;
  background:#0d1117;color:#e6edf3;font-size:11px;font-family:inherit}
.fb{padding:3px 9px;border:none;border-radius:4px;font-size:10px;font-weight:700;
  cursor:pointer;background:#21262d;color:#8b949e;transition:.15s}
.fb.on{background:#1f6feb;color:#fff}.fb:hover:not(.on){background:#30363d}
.clr{padding:3px 9px;border:none;border-radius:4px;font-size:10px;cursor:pointer;
  background:#3a1a1a;color:#f85149}
#log{overflow-y:auto;height:calc(100vh - 82px)}
.ev{border-bottom:1px solid #161b22;border-left:3px solid transparent}
.ev:hover{background:#161b22}
.ev-hdr{display:flex;align-items:center;gap:7px;padding:5px 12px;cursor:pointer;flex-wrap:wrap}
.ts{color:#8b949e;font-size:10px;white-space:nowrap}
.path{color:#79c0ff}.tool{color:#e3b341}.method{color:#58a6ff;font-weight:700}
.s-ok{color:#3fb950;font-weight:700}.s-err{color:#f85149;font-weight:700}
.ms{color:#8b949e;font-size:10px;margin-left:auto}
.ev-body{display:none;padding:4px 12px 8px;background:#010409}
.ev-body.open{display:block}
pre{font-size:10px;white-space:pre-wrap;word-break:break-all}
.json-key{color:#79c0ff}.json-str{color:#a5d6a7}.json-num{color:#e3b341}
.json-bool{color:#f85149}.json-null{color:#8b949e}
#empty{padding:30px;text-align:center;color:#3d444d}
</style></head><body>
<div class="bar">
  <h1>⚡ AnyLog Proxy — API Call Log</h1>
  <div class="dot" id="dot"></div><span id="conn">connecting…</span>
  <span id="cnt" style="font-size:10px;color:#8b949e">0 events</span>
</div>
<div class="toolbar">
  <input id="fi" type="text" placeholder="filter…" oninput="render()">
  <button class="fb on"  onclick="fkind('',this)">All</button>
  <button class="fb"     onclick="fkind('http_req',this)">HTTP▶</button>
  <button class="fb"     onclick="fkind('http_resp',this)">HTTP◀</button>
  <button class="fb"     onclick="fkind('mcp_req',this)">MCP▶</button>
  <button class="fb"     onclick="fkind('mcp_resp',this)">MCP◀</button>
  <button class="fb"     onclick="fkind('rest_req',this)">REST▶</button>
  <button class="fb"     onclick="fkind('rest_resp',this)">REST◀</button>
  <button class="clr"    onclick="evts=[];render()">Clear</button>
</div>
<div id="log"><div id="empty">No events yet…</div></div>
<script>
let evts=[],es=null,kf='';
function fkind(k,btn){kf=k;document.querySelectorAll('.fb').forEach(b=>b.classList.remove('on'));btn.classList.add('on');render();}
function match(e){if(kf&&e.kind!==kf)return false;const t=document.getElementById('fi').value.trim().toLowerCase();return !t||JSON.stringify(e).toLowerCase().includes(t);}
function hl(o){return JSON.stringify(o,null,2).replace(/("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\\s*:)?|\\b(true|false|null)\\b|-?\\d+(?:\\.\\d*)?(?:[eE][+\\-]?\\d+)?)/g,m=>{if(/^"/.test(m)){if(/:$/.test(m))return`<span class="json-key">${m}</span>`;return`<span class="json-str">${m}</span>`;}if(/true|false/.test(m))return`<span class="json-bool">${m}</span>`;if(/null/.test(m))return`<span class="json-null">${m}</span>`;return`<span class="json-num">${m}</span>`;});}
function badge(e){const m={'http_req':'b-http-req HTTP▶','http_resp':'b-http-resp HTTP◀','mcp_req':'b-mcp-req MCP▶','mcp_resp':'b-mcp-resp MCP◀','rest_req':'b-rest REST▶','rest_resp':'b-rest REST◀'};const s=(m[e.kind]||'b-http-req '+e.kind).split(' ');return`<span class="badge ${s[0]}">${s[1]||e.kind}</span>`;}
function label(e){if(e.kind==='http_req')return`<span class="method">${e.method||''}</span> <span class="path">${e.path||''}</span>`;if(e.kind==='http_resp'){const c=e.status>=400?'s-err':'s-ok';return`<span class="method">${e.method||''}</span> <span class="path">${e.path||''}</span> <span class="${c}">${e.status}</span>`;}if(e.kind==='mcp_req')return`<span class="tool">${e.tool||''}</span>`;if(e.kind==='mcp_resp'){const c=(e.status&&e.status!=='ok')?'s-err':'s-ok';return`<span class="tool">${e.tool||''}</span> <span class="${c}">${e.status||''}</span>`+(e.row_count!==undefined?` <span style="color:#8b949e">(${e.row_count}r)</span>`:'');}if(e.kind.startsWith('rest'))return`<span class="method">REST</span> dbms=${e.dbms||''} ${e.status||''}${e.row_count!==undefined?' ('+e.row_count+'r)':''}`;return '';}
function ts(t){const d=new Date(t*1000);return d.toLocaleTimeString('en',{hour12:false})+'.'+(d.getMilliseconds()+'').padStart(3,'0');}
function row(e,i){const ms=e.ms?`<span class="ms">${e.ms}ms</span>`:'';const obj={};for(const[k,v]of Object.entries(e))if(!['kind','ts'].includes(k)&&v!=null)obj[k]=v;if(obj.body){try{obj.body=JSON.parse(obj.body);}catch(_){}}if(obj.result){try{obj.result=typeof obj.result==='string'?JSON.parse(obj.result):obj.result;}catch(_){}}const d=document.createElement('div');d.className='ev';d.innerHTML=`<div class="ev-hdr"><span class="ts">${ts(e.ts)}</span>${badge(e)}${label(e)}${ms}</div><div class="ev-body"><pre>${hl(obj)}</pre></div>`;d.querySelector('.ev-hdr').onclick=()=>d.querySelector('.ev-body').classList.toggle('open');return d;}
function render(){const log=document.getElementById('log');const empty=document.getElementById('empty');const filtered=evts.filter(match);empty.style.display=filtered.length?'none':'block';Array.from(log.querySelectorAll('.ev')).forEach(n=>n.remove());const bot=log.scrollHeight-log.scrollTop-log.clientHeight<60;filtered.forEach((e,i)=>log.appendChild(row(e,i)));document.getElementById('cnt').textContent=filtered.length+'/'+evts.length+' events';if(bot)log.scrollTop=log.scrollHeight;}
function append(e){evts.push(e);const log=document.getElementById('log');if(!match(e)){document.getElementById('cnt').textContent=evts.filter(match).length+'/'+evts.length+' events';return;}document.getElementById('empty').style.display='none';const bot=log.scrollHeight-log.scrollTop-log.clientHeight<60;log.appendChild(row(e,evts.length-1));document.getElementById('cnt').textContent=evts.filter(match).length+'/'+evts.length+' events';if(bot)log.scrollTop=log.scrollHeight;}
function connect(){if(es)es.close();const dot=document.getElementById('dot'),conn=document.getElementById('conn');dot.className='dot';conn.textContent='connecting…';es=new EventSource('/api/log/stream');es.onopen=()=>{dot.className='dot live';conn.textContent='live';};es.onmessage=e=>{try{append(JSON.parse(e.data));}catch(_){}};es.onerror=()=>{dot.className='dot';conn.textContent='reconnecting…';setTimeout(connect,3000);};}
connect();
</script></body></html>"""


@app.route("/debug")
def debug_panel():
    return _DEBUG_PAGE, 200, {"Content-Type": "text/html"}


# ── Static HTML serving ───────────────────────────────────────────────────────
@app.route("/")
def index():
    html_dir = CFG.get("html_dir")
    if html_dir:
        for name in ("index.html", "dashboard.html"):
            p = os.path.join(html_dir, name)
            if os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    return f.read(), 200, {"Content-Type": "text/html"}

    mode = CFG.get("mode", "rest")
    url  = CFG.get("anylog_url", "?")
    return (
        f"<h1 style='font-family:monospace'>AnyLog Proxy  <small style='color:#666'>{mode}</small></h1>"
        f"<p style='font-family:monospace'>{url}</p>"
        f"<p style='font-family:monospace'>"
        f"POST /api/query &nbsp; POST /api/query/increment &nbsp; GET /api/status"
        f"{'&nbsp; GET /api/uns/databases &nbsp; GET /api/tables' if mode == 'mcp' else ''}"
        f"</p>"
        f"<p style='font-family:monospace'><a href='/debug'>🔌 API Call Log</a></p>"
    ), 200, {"Content-Type": "text/html"}


@app.route("/<path:filename>")
def serve_file(filename):
    html_dir = CFG.get("html_dir")
    if html_dir:
        try:
            return send_from_directory(html_dir, filename)
        except Exception:
            pass
    return jsonify({"error": f"not found: {filename}"}), 404


# ─────────────────────────────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    global CALL_DELAY_S, JOB_TIMEOUT_S, MCP_CALL_TIMEOUT_S

    parser = argparse.ArgumentParser(
        description="AnyLog Proxy — unified REST / MCP proxy-generic",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--anylog-url", default=DEFAULT_ANYLOG_URL,
        help=(
            "AnyLog target URL.  "
            "REST mode: http://host:port  "
            "MCP  mode: http://host:port/mcp/sse  (auto-detected)"
        ),
    )
    parser.add_argument(
        "--mode", choices=["rest", "mcp"], default=None,
        help="Force mode (default: auto-detect from --anylog-url)",
    )
    parser.add_argument(
        "--html-dir", default=None, metavar="PATH",
        help="Directory of HTML dashboards to serve (e.g. ../html)",
    )
    parser.add_argument("--port", "-p", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host",        default=DEFAULT_HOST)
    parser.add_argument(
        "--call-delay", type=float, default=DEFAULT_CALL_DELAY, metavar="SECS",
        help="(MCP) Pause between MCP calls",
    )
    parser.add_argument(
        "--job-timeout", type=int, default=JOB_TIMEOUT_S, metavar="SECS",
        help="Max seconds an HTTP request waits for the MCP worker",
    )
    parser.add_argument(
        "--mcp-timeout", type=float, default=None, metavar="SECS",
        help="(MCP) Hard kill timeout per MCP call (default: disabled)",
    )
    parser.add_argument(
        "--no-verify-ssl", action="store_true",
        help="Disable SSL certificate verification",
    )
    parser.add_argument("--debug",    action="store_true", help="Enable DEBUG logging")
    parser.add_argument("--log-file", default=None, metavar="PATH",
                        help="Also write logs to this file")

    args = parser.parse_args()

    # Auto-detect mode
    mode = args.mode or ("mcp" if _is_mcp_url(args.anylog_url) else "rest")

    # Populate runtime config
    CFG.update({
        "anylog_url":     args.anylog_url,
        "mode":           mode,
        "html_dir":       args.html_dir,
        "host":           args.host,
        "port":           args.port,
        "verify_ssl":     not args.no_verify_ssl,
        "qwi_supported":  True,
    })

    CALL_DELAY_S       = args.call_delay
    JOB_TIMEOUT_S      = args.job_timeout
    MCP_CALL_TIMEOUT_S = args.mcp_timeout

    # Configure logging
    level = logging.DEBUG if args.debug else logging.INFO
    logging.getLogger().setLevel(level)
    if args.log_file:
        fh = logging.FileHandler(args.log_file, encoding="utf-8")
        fh.setFormatter(logging.Formatter(LOG_FORMAT))
        logging.getLogger().addHandler(fh)

    # Banner
    W = 62
    print("=" * W, file=sys.stderr)
    print(f"  AnyLog Proxy  (mode={mode})", file=sys.stderr)
    print(f"  AnyLog URL : {CFG['anylog_url']}", file=sys.stderr)
    print(f"  Listen     : http://{args.host}:{args.port}", file=sys.stderr)
    if args.html_dir:
        print(f"  HTML dir   : {os.path.abspath(args.html_dir)}", file=sys.stderr)
    if mode == "mcp":
        print(f"  Call delay : {CALL_DELAY_S}s  |  Job timeout: {JOB_TIMEOUT_S}s", file=sys.stderr)
        to_label = f"{MCP_CALL_TIMEOUT_S}s" if MCP_CALL_TIMEOUT_S else "disabled"
        print(f"  MCP timeout: {to_label}", file=sys.stderr)
    print(f"  SSL verify : {not args.no_verify_ssl}", file=sys.stderr)
    print(f"  Debug panel: http://localhost:{args.port}/debug", file=sys.stderr)
    print("=" * W, file=sys.stderr)

    if mode == "mcp":
        start_worker()

    app.run(
        host=args.host,
        port=args.port,
        debug=args.debug,
        threaded=True,
        use_reloader=False,
    )


if __name__ == "__main__":
    main()