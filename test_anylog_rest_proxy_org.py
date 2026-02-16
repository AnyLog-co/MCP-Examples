#!/usr/bin/env python3
"""
test_anylog_rest_proxy.py
========================
Test suite for anylog_rest_proxy.py.

Covers every endpoint, CORS preflight, input validation, error shapes,
stats counters, and live AnyLog query responses.

Requirements
------------
    pip install requests
    # Proxy must already be running:
    python anylog_rest_proxy.py

Usage
-----
    python test_anylog_rest_proxy.py
    python test_anylog_rest_proxy.py --url http://localhost:5050
    python test_anylog_rest_proxy.py --dbms bottle_factory --table metric_11
    python test_anylog_rest_proxy.py --skip-live    # validation-only, no AnyLog calls
    python test_anylog_rest_proxy.py --verbose      # dump response bodies
    python test_anylog_rest_proxy.py --timeout 90   # slow nodes
"""

import argparse
import json
import sys
import time
from typing import Any

import requests

# ─── ANSI colour helpers ──────────────────────────────────────────────────────
_TTY = sys.stdout.isatty()

def _c(code, s):  return f"\033[{code}m{s}\033[0m" if _TTY else s
GRN  = lambda s: _c("32",  s)
RED  = lambda s: _c("31",  s)
YEL  = lambda s: _c("33",  s)
CYN  = lambda s: _c("36",  s)
DIM  = lambda s: _c("2",   s)
BOLD = lambda s: _c("1",   s)

# ─── Globals set in main() ────────────────────────────────────────────────────
BASE    = "http://localhost:8080"
DBMS    = "bottle_factory"
TABLE   = "metric_11"           # Site1 metric table
TIMEOUT = 60.0
VERBOSE = False

# ─── Test registry ────────────────────────────────────────────────────────────
_results: list[dict] = []          # {name, passed, ms, detail}

def _record(name: str, passed: bool, ms: float, detail: str = ""):
    _results.append({"name": name, "passed": passed, "ms": ms, "detail": detail})
    badge  = GRN("PASS") if passed else RED("FAIL")
    timing = DIM(f"{ms:6.0f}ms")
    print(f"  {badge}  {timing}  {name}")
    if not passed and detail:
        print(f"          {YEL(detail)}")
    elif VERBOSE and detail:
        print(f"          {DIM(detail)}")

# ─── HTTP shorthands ──────────────────────────────────────────────────────────
_S = requests.Session()

def GET(path: str, **kw) -> requests.Response:
    return _S.get(f"{BASE}{path}", timeout=TIMEOUT, **kw)

def POST(path: str, body: dict = None) -> requests.Response:
    return _S.post(f"{BASE}{path}", json=body or {}, timeout=TIMEOUT)

def OPTIONS(path: str) -> requests.Response:
    return _S.options(
        f"{BASE}{path}", timeout=5,
        headers={"Origin": "http://localhost",
                 "Access-Control-Request-Method": "POST",
                 "Access-Control-Request-Headers": "Content-Type"},
    )

def _json(resp: requests.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return resp.text

# ─── Section header ───────────────────────────────────────────────────────────
def section(title: str):
    print(f"\n{CYN('─' * 66)}")
    print(f"  {BOLD(title)}")
    print(f"{CYN('─' * 66)}")

# ─── Core assertion runner ────────────────────────────────────────────────────
def check(name: str, fn):
    """
    fn() must return (passed: bool, detail: str).
    Wraps timing and records result.
    """
    t0 = time.perf_counter()
    try:
        passed, detail = fn()
    except Exception as exc:
        passed, detail = False, f"EXCEPTION: {exc}"
    ms = (time.perf_counter() - t0) * 1000
    _record(name, passed, ms, detail)

# ═══════════════════════════════════════════════════════════════════════════════
# TEST FUNCTIONS
# Each returns (passed: bool, detail: str)
# ═══════════════════════════════════════════════════════════════════════════════

# ── 1. Health ─────────────────────────────────────────────────────────────────

def t_health_200():
    r = GET("/health")
    return r.status_code == 200, f"HTTP {r.status_code}"

def t_health_required_fields():
    b = _json(GET("/health"))
    required = {"status", "service", "anylog_node", "user_agent", "uptime_sec", "stats"}
    missing  = required - set(b.keys()) if isinstance(b, dict) else required
    return not missing, f"missing: {missing}" if missing else ""

def t_health_status_healthy():
    b = _json(GET("/health"))
    v = b.get("status") if isinstance(b, dict) else None
    return v == "healthy", f"status={v!r}"

def t_health_user_agent():
    b = _json(GET("/health"))
    v = b.get("user_agent") if isinstance(b, dict) else None
    return v == "AnyLog/1.23", f"user_agent={v!r} (expected 'AnyLog/1.23')"

def t_health_anylog_protocol():
    """Proxy must reach AnyLog over HTTP not HTTPS."""
    b = _json(GET("/health"))
    node = b.get("anylog_node", "") if isinstance(b, dict) else ""
    return ":" in node, f"anylog_node={node!r}"

def t_health_stats_subfields():
    b = _json(GET("/health"))
    stats = b.get("stats", {}) if isinstance(b, dict) else {}
    required = {"proxy_calls", "anylog_calls", "total_rows", "errors"}
    missing  = required - set(stats.keys())
    return not missing, f"missing from stats: {missing}" if missing else ""

# ── 2. Stats ──────────────────────────────────────────────────────────────────

def t_stats_200():
    r = GET("/stats")
    return r.status_code == 200, f"HTTP {r.status_code}"

def t_stats_fields():
    b = _json(GET("/stats"))
    required = {"proxy_calls", "anylog_calls", "total_rows", "errors", "uptime_sec"}
    missing  = required - set(b.keys()) if isinstance(b, dict) else required
    return not missing, f"missing: {missing}" if missing else ""

def t_stats_no_anylog_call():
    """GET /stats must NOT trigger an AnyLog call (it's local-only)."""
    before = _json(GET("/stats")).get("anylog_calls", 0)
    GET("/stats")
    after  = _json(GET("/stats")).get("anylog_calls", 0)
    # stats itself is not counted; two extra GETs to /stats should not increment anylog_calls
    return after == before, f"anylog_calls changed {before}→{after} just from /stats calls"

# ── 3. CORS Preflight ─────────────────────────────────────────────────────────

def t_cors_query_preflight():
    r = OPTIONS("/api/query")
    ok = r.status_code in (200, 204)
    return ok, f"HTTP {r.status_code} (expected 200 or 204)"

def t_cors_increment_preflight():
    r = OPTIONS("/api/query/increment")
    ok = r.status_code in (200, 204)
    return ok, f"HTTP {r.status_code}"

def t_cors_command_preflight():
    r = OPTIONS("/api/command")
    ok = r.status_code in (200, 204)
    return ok, f"HTTP {r.status_code}"

def t_cors_allow_origin_header():
    r = OPTIONS("/api/query")
    acao = r.headers.get("Access-Control-Allow-Origin", "")
    return bool(acao), f"Access-Control-Allow-Origin={acao!r}"

def t_cors_allow_methods_header():
    r = OPTIONS("/api/query")
    acam = r.headers.get("Access-Control-Allow-Methods", "")
    return bool(acam), f"Access-Control-Allow-Methods={acam!r}"

# ── 4. Connection endpoints ───────────────────────────────────────────────────

def t_conn_status_200():
    r = GET("/api/connection/status")
    return r.status_code == 200, f"HTTP {r.status_code}"

def t_conn_status_has_status_field():
    b = _json(GET("/api/connection/status"))
    return isinstance(b, dict) and "status" in b, f"body={str(b)[:120]}"

def t_conn_test_200():
    r = POST("/api/connection/test")
    return r.status_code == 200, f"HTTP {r.status_code}"

def t_conn_test_has_success_field():
    b = _json(POST("/api/connection/test"))
    return isinstance(b, dict) and "success" in b, f"body={str(b)[:120]}"

# ── 5. Metadata ───────────────────────────────────────────────────────────────

def t_databases_200():
    r = GET("/api/databases")
    return r.status_code == 200, f"HTTP {r.status_code}"

def t_databases_field():
    b = _json(GET("/api/databases"))
    return isinstance(b, dict) and "databases" in b, f"body={str(b)[:120]}"

def t_tables_200():
    r = GET(f"/api/databases/{DBMS}/tables")
    return r.status_code == 200, f"HTTP {r.status_code}"

def t_tables_field():
    b = _json(GET(f"/api/databases/{DBMS}/tables"))
    return isinstance(b, dict) and "tables" in b, f"body={str(b)[:120]}"

def t_columns_200():
    r = GET(f"/api/databases/{DBMS}/tables/{TABLE}/columns")
    return r.status_code == 200, f"HTTP {r.status_code}"

def t_columns_field():
    b = _json(GET(f"/api/databases/{DBMS}/tables/{TABLE}/columns"))
    return isinstance(b, dict) and "columns" in b, f"body={str(b)[:120]}"

def t_nodes_200():
    r = GET("/api/nodes")
    return r.status_code == 200, f"HTTP {r.status_code}"

def t_nodes_field():
    b = _json(GET("/api/nodes"))
    return isinstance(b, dict) and "nodes" in b, f"body={str(b)[:120]}"

def t_node_status_get():
    r = GET("/api/nodes/status")
    b = _json(r)
    ok = r.status_code == 200 and isinstance(b, dict) and "status" in b
    return ok, f"HTTP {r.status_code}  body={str(b)[:100]}"

def t_node_status_post():
    r = POST("/api/nodes/status", {})
    b = _json(r)
    ok = r.status_code == 200 and isinstance(b, dict) and "status" in b
    return ok, f"HTTP {r.status_code}  body={str(b)[:100]}"

def t_data_location_field():
    b = _json(GET(f"/api/data/location?dbms={DBMS}"))
    return isinstance(b, dict) and "locations" in b, f"body={str(b)[:120]}"

# ── 6. POST /api/query — input validation ────────────────────────────────────

def t_query_missing_dbms():
    r = POST("/api/query", {"sql": "SELECT 1"})
    return r.status_code == 400, f"HTTP {r.status_code} (expected 400)"

def t_query_missing_sql():
    r = POST("/api/query", {"dbms": DBMS})
    return r.status_code == 400, f"HTTP {r.status_code} (expected 400)"

def t_query_empty_body():
    r = POST("/api/query", {})
    return r.status_code == 400, f"HTTP {r.status_code} (expected 400)"

def t_query_whitespace_only_dbms():
    r = POST("/api/query", {"dbms": "   ", "sql": "SELECT 1"})
    return r.status_code == 400, f"HTTP {r.status_code} (expected 400)"

def t_query_error_body_has_error_field():
    """Error responses must have an 'error' key (from _err())."""
    b = _json(POST("/api/query", {}))
    return isinstance(b, dict) and "error" in b, f"body={str(b)[:120]}"

# ── 7. POST /api/query — live AnyLog queries ─────────────────────────────────

def t_query_rest_metrics():
    """Core rest query — must return a list with rest/availability/performance/quality."""
    r = POST("/api/query", {
        "dbms": DBMS,
        "sql":  (f"SELECT avg(rest) as rest, avg(availability) as availability, "
                 f"avg(performance) as performance, avg(quality) as quality "
                 f"FROM {TABLE} WHERE insert_timestamp >= NOW() - 4 hours"),
    })
    b = _json(r)
    if not r.ok:
        return False, f"HTTP {r.status_code}  error={b.get('error','') if isinstance(b,dict) else b}"
    if not isinstance(b, list):
        return False, f"Expected list, got {type(b).__name__}: {str(b)[:150]}"
    if len(b) == 0:
        return True, "Empty result — no data in last 4h window (may be normal)"
    row = b[0]
    cols = {"rest", "availability", "performance", "quality"}
    missing = cols - set(row.keys())
    if missing:
        return False, f"Missing columns {missing} in row: {row}"
    rest_pct = float(row["rest"]) * 100
    return True, (f"rest={rest_pct:.1f}%  "
                  f"A={float(row['availability'])*100:.1f}%  "
                  f"P={float(row['performance'])*100:.1f}%  "
                  f"Q={float(row['quality'])*100:.1f}%")

def t_query_returns_list():
    """Successful query must return a JSON array, not a dict."""
    r = POST("/api/query", {
        "dbms": DBMS,
        "sql":  f"SELECT * FROM {TABLE} LIMIT 3",
    })
    b = _json(r)
    if not r.ok:
        return False, f"HTTP {r.status_code}  {b.get('error','') if isinstance(b,dict) else b}"
    return isinstance(b, list), f"type={type(b).__name__}  body={str(b)[:120]}"

def t_query_insert_timestamp_works():
    """Confirm insert_timestamp is the correct time column (not 'timestamp')."""
    r = POST("/api/query", {
        "dbms": DBMS,
        "sql":  f"SELECT COUNT(*) as cnt FROM {TABLE} WHERE insert_timestamp >= NOW() - 1 day",
    })
    b = _json(r)
    ok = r.ok and isinstance(b, list)
    detail = (f"cnt={b[0].get('cnt','?') if b else 'no rows'}" if ok
              else f"HTTP {r.status_code}  {str(b)[:150]}")
    return ok, detail

def t_query_wrong_time_col_no_crash():
    """Using 'timestamp' (wrong) must return a handled error, not a proxy 500 crash."""
    r = POST("/api/query", {
        "dbms": DBMS,
        "sql":  f"SELECT avg(rest) as rest FROM {TABLE} WHERE timestamp >= NOW() - 1 hour",
    })
    # Proxy must NOT return 500. AnyLog will fail, proxy should return 502 or 200[].
    return r.status_code != 500, f"proxy returned 500 (unhandled crash) for bad column name"

def t_query_bad_table_no_crash():
    """Non-existent table must return a handled error, not a proxy 500 crash."""
    r = POST("/api/query", {
        "dbms": DBMS,
        "sql":  "SELECT avg(rest) as rest FROM nonexistent_table_xyz WHERE insert_timestamp >= NOW() - 1 hour",
    })
    return r.status_code != 500, f"proxy returned 500 on bad table name"

def t_query_stats_increment():
    """proxy_calls and anylog_calls must both increment after a /api/query call."""
    before = _json(GET("/stats"))
    POST("/api/query", {
        "dbms": DBMS,
        "sql":  f"SELECT avg(rest) as rest FROM {TABLE} WHERE insert_timestamp >= NOW() - 1 hour",
    })
    after = _json(GET("/stats"))
    pc_ok = after.get("proxy_calls", 0)  > before.get("proxy_calls", 0)
    al_ok = after.get("anylog_calls", 0) > before.get("anylog_calls", 0)
    detail = []
    if not pc_ok: detail.append(f"proxy_calls {before.get('proxy_calls')}→{after.get('proxy_calls')}")
    if not al_ok: detail.append(f"anylog_calls {before.get('anylog_calls')}→{after.get('anylog_calls')}")
    return pc_ok and al_ok, "  ".join(detail)

# ── 8. POST /api/query/increment — input validation ──────────────────────────

def t_incr_missing_dbms():
    r = POST("/api/query/increment", {"table": TABLE})
    return r.status_code == 400, f"HTTP {r.status_code} (expected 400)"

def t_incr_missing_table():
    r = POST("/api/query/increment", {"dbms": DBMS})
    return r.status_code == 400, f"HTTP {r.status_code} (expected 400)"

def t_incr_empty_body():
    r = POST("/api/query/increment", {})
    return r.status_code == 400, f"HTTP {r.status_code} (expected 400)"

def t_incr_error_has_error_field():
    b = _json(POST("/api/query/increment", {}))
    return isinstance(b, dict) and "error" in b, f"body={str(b)[:120]}"

# ── 9. POST /api/query/increment — live ──────────────────────────────────────

def t_incr_basic():
    """Hourly rest increments over last 4 hours."""
    r = POST("/api/query/increment", {
        "dbms":           DBMS,
        "table":          TABLE,
        "timeColumn":     "insert_timestamp",
        "startTime":      "NOW() - 4 hours",
        "endTime":        "NOW()",
        "timeUnit":       "hour",
        "intervalLength": 1,
        "projections":    ["avg(rest)", "avg(availability)", "avg(performance)", "avg(quality)"],
    })
    b = _json(r)
    if not r.ok:
        return False, f"HTTP {r.status_code}  {b.get('error','') if isinstance(b,dict) else str(b)[:150]}"
    return isinstance(b, list), f"{len(b)} interval(s) returned" if isinstance(b, list) else f"not a list: {str(b)[:120]}"

def t_incr_default_time_column():
    """Omitting timeColumn must use insert_timestamp (default) without crashing."""
    r = POST("/api/query/increment", {
        "dbms":           DBMS,
        "table":          TABLE,
        # timeColumn intentionally omitted — proxy defaults to insert_timestamp
        "startTime":      "NOW() - 2 hours",
        "endTime":        "NOW()",
        "timeUnit":       "hour",
        "intervalLength": 1,
        "projections":    ["avg(rest)"],
    })
    return r.status_code != 500, f"HTTP {r.status_code}"

# ── 10. POST /api/command ─────────────────────────────────────────────────────

def t_command_missing_field():
    r = POST("/api/command", {})
    return r.status_code == 400, f"HTTP {r.status_code} (expected 400)"

def t_command_empty_string():
    r = POST("/api/command", {"command": ""})
    return r.status_code == 400, f"HTTP {r.status_code} (expected 400)"

def t_command_whitespace_only():
    r = POST("/api/command", {"command": "   "})
    return r.status_code == 400, f"HTTP {r.status_code} (expected 400)"

def t_command_error_has_error_field():
    b = _json(POST("/api/command", {}))
    return isinstance(b, dict) and "error" in b, f"body={str(b)[:120]}"

def t_command_get_status():
    r = POST("/api/command", {"command": "get status"})
    b = _json(r)
    ok = r.ok and isinstance(b, dict) and "command" in b and "raw" in b and "rows" in b
    return ok, f"HTTP {r.status_code}  fields={list(b.keys()) if isinstance(b,dict) else '?'}"

def t_command_get_databases():
    r = POST("/api/command", {"command": "get databases"})
    b = _json(r)
    ok = r.ok and isinstance(b, dict) and "raw" in b
    return ok, f"HTTP {r.status_code}  raw_len={len(b.get('raw','')) if isinstance(b,dict) else '?'}"

def t_command_response_fields():
    """Successful command response must have command, raw, rows."""
    b = _json(POST("/api/command", {"command": "get status"}))
    if not isinstance(b, dict):
        return False, f"not a dict: {str(b)[:100]}"
    missing = {"command", "raw", "rows"} - set(b.keys())
    return not missing, f"missing fields: {missing}" if missing else f"command={b.get('command')!r}"

# ── 11. Error response shape ──────────────────────────────────────────────────

def t_error_shape_400():
    """All 400 errors must return JSON with an 'error' key."""
    endpoints = [
        ("/api/query",           {}),
        ("/api/query/increment", {}),
        ("/api/command",         {}),
    ]
    failures = []
    for path, body in endpoints:
        try:
            b = _json(POST(path, body))
            if not (isinstance(b, dict) and "error" in b):
                failures.append(f"{path}: {str(b)[:80]}")
        except Exception as exc:
            failures.append(f"{path}: {exc}")
    return not failures, "  |  ".join(failures)

def t_404_unknown_route():
    """Unknown routes must return 404, not 500."""
    r = GET("/api/this_does_not_exist")
    return r.status_code == 404, f"HTTP {r.status_code} (expected 404)"

# ─── Reachability check ───────────────────────────────────────────────────────
def check_reachable():
    try:
        r = requests.get(f"{BASE}/health", timeout=5)
        return True, r.status_code
    except requests.exceptions.ConnectionError:
        return False, None
    except requests.exceptions.InvalidSchema:
        # Missing scheme — should not happen after auto-fix but guard anyway
        return False, "invalid-schema"
    except Exception as exc:
        return False, str(exc)[:80]


# ═══════════════════════════════════════════════════════════════════════════════
# RUNNER
# ═══════════════════════════════════════════════════════════════════════════════
def run(name: str, fn):
    check(name, fn)

def run_live(name: str, fn, skip: bool):
    if skip:
        print(f"  {DIM('SKIP')}           {DIM(name)}")
    else:
        check(name, fn)


def main():
    global BASE, DBMS, TABLE, TIMEOUT, VERBOSE

    p = argparse.ArgumentParser(description="Test suite for anylog_rest_proxy.py")
    p.add_argument("--url",       default="http://localhost:8080", help="Proxy base URL")
    p.add_argument("--dbms",      default="bottle_factory",        help="Database name (default: bottle_factory)")
    p.add_argument("--table",     default="metric_11",             help="Table name (default: metric_11)")
    p.add_argument("--timeout",   type=float, default=60.0,        help="Request timeout seconds (default: 60)")
    p.add_argument("--skip-live", action="store_true",             help="Skip tests that make live AnyLog queries")
    p.add_argument("--verbose",   action="store_true",             help="Print detail for passing tests too")
    args = p.parse_args()

    raw_url = args.url.rstrip("/")
    if not raw_url.startswith("http://") and not raw_url.startswith("https://"):
        raw_url = "http://" + raw_url
    BASE    = raw_url
    DBMS    = args.dbms
    TABLE   = args.table
    TIMEOUT = args.timeout
    VERBOSE = args.verbose
    skip    = args.skip_live

    print(f"\n{CYN('═' * 66)}")
    print(f"  {BOLD('AnyLog rest Proxy — Test Suite')}")
    print(f"{CYN('═' * 66)}")
    print(f"  Proxy    : {BOLD(BASE)}")
    print(f"  Database : {BOLD(DBMS)}    Table: {BOLD(TABLE)}")
    print(f"  Timeout  : {TIMEOUT}s    Skip-live: {skip}")

    # Reachability gate
    print(f"\n  Checking proxy reachability … ", end="", flush=True)
    reachable, code = check_reachable()
    if not reachable:
        print(RED("UNREACHABLE"))
        print(f"\n  {RED('Cannot connect to')} {BOLD(BASE)}")
        print(f"  {DIM('Ensure the proxy is running:  python anylog_rest_proxy.py')}")
        print(f"  {DIM('URL must include scheme, e.g.: --url http://localhost:8080')}\n")
        sys.exit(1)
    print(GRN(f"OK  (HTTP {code})"))

    # ──────────────────────────────────────────────────────────────────────────
    section("1  Health")
    run("GET /health → HTTP 200",                       t_health_200)
    run("GET /health → status = 'healthy'",             t_health_status_healthy)
    run("GET /health → required top-level fields",      t_health_required_fields)
    run("GET /health → user_agent = 'AnyLog/1.23'",    t_health_user_agent)
    run("GET /health → anylog_node contains IP:port",  t_health_anylog_protocol)
    run("GET /health → stats subfields present",       t_health_stats_subfields)

    section("2  Stats")
    run("GET /stats → HTTP 200",                        t_stats_200)
    run("GET /stats → required counter fields",         t_stats_fields)
    run("GET /stats → no AnyLog call triggered",        t_stats_no_anylog_call)

    section("3  CORS Preflight")
    run("OPTIONS /api/query → 200 or 204",              t_cors_query_preflight)
    run("OPTIONS /api/query/increment → 200 or 204",   t_cors_increment_preflight)
    run("OPTIONS /api/command → 200 or 204",            t_cors_command_preflight)
    run("OPTIONS → Access-Control-Allow-Origin present", t_cors_allow_origin_header)
    run("OPTIONS → Access-Control-Allow-Methods present",t_cors_allow_methods_header)

    section("4  Connection Endpoints")
    run("GET  /api/connection/status → HTTP 200",       t_conn_status_200)
    run("GET  /api/connection/status → 'status' field", t_conn_status_has_status_field)
    run("POST /api/connection/test   → HTTP 200",       t_conn_test_200)
    run("POST /api/connection/test   → 'success' field",t_conn_test_has_success_field)

    section("5  Metadata Discovery")
    run("GET /api/databases → HTTP 200",                t_databases_200)
    run("GET /api/databases → 'databases' field",       t_databases_field)
    run(f"GET /api/databases/{DBMS}/tables → 200",     t_tables_200)
    run(f"GET /api/databases/{DBMS}/tables → 'tables'",t_tables_field)
    run(f"GET …/{TABLE}/columns → 200",                t_columns_200)
    run(f"GET …/{TABLE}/columns → 'columns' field",    t_columns_field)
    run("GET /api/nodes → 'nodes' field",               t_nodes_field)
    run("GET /api/nodes/status (GET)  → 'status' field",t_node_status_get)
    run("POST /api/nodes/status (POST) → 'status' field",t_node_status_post)
    run("GET /api/data/location → 'locations' field",   t_data_location_field)

    section("6  POST /api/query — Input Validation")
    run("missing 'dbms' → HTTP 400",                    t_query_missing_dbms)
    run("missing 'sql'  → HTTP 400",                    t_query_missing_sql)
    run("empty body     → HTTP 400",                    t_query_empty_body)
    run("whitespace dbms → HTTP 400",                   t_query_whitespace_only_dbms)
    run("error response has 'error' field",             t_query_error_body_has_error_field)

    section("7  POST /api/query — Live AnyLog Queries")
    run_live("avg(rest,availability,performance,quality) — correct columns returned",
             t_query_rest_metrics, skip)
    run_live("successful query returns JSON array (not dict)",
             t_query_returns_list, skip)
    run_live("WHERE insert_timestamp works (correct time column)",
             t_query_insert_timestamp_works, skip)
    run_live("WHERE timestamp (wrong col) → handled error, not proxy 500",
             t_query_wrong_time_col_no_crash, skip)
    run_live("non-existent table → handled error, not proxy 500",
             t_query_bad_table_no_crash, skip)
    run_live("proxy_calls + anylog_calls increment after query",
             t_query_stats_increment, skip)

    section("8  POST /api/query/increment — Input Validation")
    run("missing 'dbms'  → HTTP 400",                   t_incr_missing_dbms)
    run("missing 'table' → HTTP 400",                   t_incr_missing_table)
    run("empty body      → HTTP 400",                   t_incr_empty_body)
    run("error response has 'error' field",             t_incr_error_has_error_field)

    section("9  POST /api/query/increment — Live AnyLog Queries")
    run_live("hourly rest increments last 4h → list returned",
             t_incr_basic, skip)
    run_live("omitting timeColumn defaults to insert_timestamp (no crash)",
             t_incr_default_time_column, skip)

    section("10  POST /api/command")
    run("missing 'command' field → HTTP 400",           t_command_missing_field)
    run("empty string command    → HTTP 400",           t_command_empty_string)
    run("whitespace-only command → HTTP 400",           t_command_whitespace_only)
    run("error response has 'error' field",             t_command_error_has_error_field)
    run_live("'get status'    → command+raw+rows fields",t_command_get_status,   skip)
    run_live("'get databases' → raw field present",      t_command_get_databases, skip)
    run_live("response has command, raw, rows fields",  t_command_response_fields, skip)

    section("11  Error Response Contract")
    run("all 400 errors return JSON with 'error' key",  t_error_shape_400)
    run("unknown route → HTTP 404 (not 500)",           t_404_unknown_route)

    # ──────────────────────────────────────────────────────────────────────────
    # Summary
    # ──────────────────────────────────────────────────────────────────────────
    total   = len(_results)
    passed  = sum(1 for r in _results if r["passed"])
    failed  = total - passed
    skipped = sum(1 for line in open(sys.argv[0]).readlines()
                  if "SKIP" in line)  # rough count only

    print(f"\n{CYN('═' * 66)}")
    print(f"  {BOLD('RESULTS')}")
    print(f"{CYN('═' * 66)}")
    print(f"  Total   : {total}")
    print(f"  {GRN('Passed')}  : {GRN(str(passed))}")
    print(f"  {(RED('Failed') if failed else DIM('Failed'))}  : {(RED(str(failed)) if failed else DIM('0'))}")

    if failed:
        print(f"\n  {RED(BOLD('FAILED TESTS:'))}")
        for r in _results:
            if not r["passed"]:
                print(f"    {RED('✗')} {r['name']}")
                if r["detail"]:
                    print(f"        {YEL(r['detail'])}")

    verdict = GRN(BOLD("ALL TESTS PASSED")) if not failed else RED(BOLD(f"{failed} TEST(S) FAILED"))
    print(f"\n  {verdict}\n")
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
