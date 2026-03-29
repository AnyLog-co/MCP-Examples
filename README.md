# MCP Web Bridge — v5.2

A lightweight Python/Flask proxy that bridges browser-based dashboards to an
[AnyLog](https://anylog.co) distributed edge network via the MCP-over-SSE
protocol.  Dashboards make ordinary HTTP REST calls; the bridge translates them
into MCP JSON-RPC requests and returns the results — **synchronously, one call
at a time**.

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Call Serialization](#call-serialization)
4. [Cache](#cache)
5. [Installation](#installation)
6. [Command-Line Options](#command-line-options)
7. [API Endpoints](#api-endpoints)
8. [TLS / HTTPS](#tls--https)
9. [Logging](#logging)
10. [Debug Panel](#debug-panel)
11. [MCP-over-SSE Protocol Notes](#mcp-over-sse-protocol-notes)
12. [Changelog](#changelog)

---

## Overview

Browser dashboards cannot connect directly to an AnyLog MCP SSE server due to
CORS restrictions and the complexity of the MCP-over-SSE protocol.  The bridge
solves both problems:

```
Dashboard (browser)
    │  HTTP POST/GET (JSON)
    ▼
mcp_web_bridge.py   ←── this proxy
    │  MCP JSON-RPC over SSE
    ▼
AnyLog MCP SSE server  (e.g. https://172.79.89.206:32049/mcp/sse)
    │  AnyLog distributed query
    ▼
Edge nodes
```

Key design decisions:

- **One MCP call at a time** — the single worker thread guarantees the AnyLog
  SSE server never receives concurrent requests from this bridge.
- **Blocking API calls** — every `/api/*` endpoint blocks until its MCP call
  completes (or the job timeout expires).  There is no fire-and-forget path.
- **TTL cache** — repeated identical requests are served from cache without
  touching the MCP server.
- **No mcp-proxy subprocess** — the bridge uses a pure-`requests` SSE client
  (`McpSseClient`), eliminating asyncio flush-delay issues.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  Flask  (threaded=True, one thread per HTTP client) │
│                                                     │
│  /api/query  /api/status  /api/tables  …            │
│       │                                             │
│       │  _run_job(tool, params)                     │
│       │  ├─ cache hit? → return immediately         │
│       │  └─ enqueue Job, block on job.done.wait()   │
│                     │                               │
│            ┌────────▼──────────────┐                │
│            │   _job_queue (Queue)  │  ← FIFO        │
│            └────────┬──────────────┘                │
│                     │  one job at a time            │
│            ┌────────▼──────────────┐                │
│            │   _worker thread      │  daemon        │
│            │   _call_mcp(tool, p)  │                │
│            │   cache_set(key, r)   │                │
│            │   job.done.set()      │                │
│            │   sleep(CALL_DELAY_S) │                │
│            └────────┬──────────────┘                │
│                     │                               │
│            ┌────────▼──────────────┐                │
│            │   McpSseClient        │                │
│            │   GET /mcp/sse        │                │
│            │   POST <endpoint>     │                │
│            │   read SSE response   │                │
│            └───────────────────────┘                │
└─────────────────────────────────────────────────────┘
```

### Components

**Flask HTTP layer** — multi-threaded; each inbound request runs in its own
thread.  Threads do not call MCP directly — they submit a `Job` and wait.

**Job queue** (`_job_queue`) — a standard `queue.Queue`.  Jobs are enqueued
by Flask threads and drained one at a time by the worker.

**Worker thread** (`_worker`) — a single daemon thread.  It pops one job,
executes the MCP call to completion, writes the result, signals the job's
`done` event, then sleeps `CALL_DELAY_S` before taking the next job.

**McpSseClient** — a stateless, thread-safe MCP-over-SSE client built on
`requests`.  Opens a fresh SSE connection per RPC call.  Supports both
single-stream (mark-demo style) and two-stream (Timbergrove style) AnyLog
server variants.

**TTL Cache** — an in-memory dict protected by a `threading.Lock`.  Metadata
entries live for `CACHE_TTL_S` (default 300 s); query data entries live for
`DATA_TTL_S` (default 30 s).

---

## Call Serialization

**Rule: only one MCP call is ever in-flight at any moment.**

This is enforced by the single worker thread.  No MCP call can start while
another is running.  The `CALL_DELAY_S` pause after each call gives the AnyLog
SSE server time to reset before the next request arrives.

### What happens when multiple dashboards call the bridge simultaneously

1. Each request arrives on its own Flask thread.
2. Each thread calls `_run_job()`, which:
   - Returns immediately if the result is cached.
   - Otherwise enqueues a `Job` and calls `job.done.wait(JOB_TIMEOUT_S)`.
3. The worker drains the queue one job at a time.
4. When the worker finishes a job it sets `job.done`, unblocking the waiting
   Flask thread.
5. The Flask thread returns the HTTP response.

Duplicate concurrent requests for the same `(tool, params)` key are
**deduplicated**: the second caller joins the already-queued job rather than
submitting another one.

### UNS database discovery serialization

`/api/uns/databases` triggers a multi-step discovery that requires two or
three MCP calls internally (`listPolicyTypes`, `listPolicies`, optionally
`listNetworkDatabases`).  In v5.0 this entire discovery is submitted as a
single opaque job.  The worker executes all sub-calls sequentially with
`CALL_DELAY_S` between them, so the discovery never races with other jobs.

---

## Cache

| Type | Key | TTL |
|------|-----|-----|
| Metadata | `tool:params_json` | `CACHE_TTL_S` (default 300 s) |
| Query data | `tool:params_json` | `DATA_TTL_S` (default 30 s) |
| UNS databases | `uns_databases:<mcp_url>` | `CACHE_TTL_S` |

Cache is **in-memory only** — it is lost on restart.  Clear it at runtime with
`POST /api/cache/clear`.

---

## Installation

```bash
pip install flask flask-cors requests
```

Optional (for TLS certificate auto-generation without `openssl` CLI):

```bash
pip install cryptography
```

---

## Command-Line Options

```
python mcp_web_bridge.py [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--mcp-url URL` | `https://172.79.89.206:32049/mcp/sse` | MCP SSE server to connect to |
| `--port PORT` / `-p` | `8080` | HTTP/HTTPS port to listen on |
| `--host HOST` | `0.0.0.0` | Interface to bind to |
| `--call-delay SECS` | `1.5` | Pause between MCP calls (protects the SSE server) |
| `--job-timeout SECS` | `300` | Max seconds an HTTP request waits for the MCP worker |
| `--mcp-timeout SECS` | disabled | Hard kill timeout per individual MCP call. If the MCP server does not respond within this many seconds the SSE socket is closed and the call fails immediately. Should be less than `--job-timeout`. Omit to wait indefinitely. |
| `--debug [LEVEL]` | `0` | Debug level. `--debug` or `--debug 1`: Python DEBUG logging + full MCP payload output. `--debug 2`: level 1 plus step-through mode (pauses before query calls, see below). |
| `--quiet` / `-q` | off | Suppress INFO-level log chatter; show only warnings/errors |
| `--log-file PATH` | none | Append log output to file in addition to stderr |
| `--ssl` | off | Enable HTTPS on the Flask frontend |
| `--ssl-cert CERT.pem` | auto-generated | Path to PEM certificate (implies `--ssl`) |
| `--ssl-key KEY.pem` | auto-generated | Path to PEM private key (implies `--ssl`) |

### Common invocations

```bash
# Basic — connect to a specific AnyLog node
python mcp_web_bridge.py --mcp-url https://129.212.178.167:32349/mcp/sse

# Quieter logs, different port
python mcp_web_bridge.py --mcp-url https://129.212.178.167:32349/mcp/sse \
    --port 9090 --quiet --log-file bridge.log

# Slower call rate (2 s between calls) and longer timeout for heavy queries
python mcp_web_bridge.py --call-delay 2.0 --job-timeout 600

# Serve over HTTPS with auto-generated self-signed cert
python mcp_web_bridge.py --ssl

# Serve over HTTPS with your own certificate
python mcp_web_bridge.py --ssl-cert server.crt --ssl-key server.key

# Debug level 1 — verbose logging
python mcp_web_bridge.py --debug

# Debug level 2 — step-through on every executeQuery / queryWithIncrement
python mcp_web_bridge.py --debug 2
```

---

## API Endpoints

All endpoints return JSON.  Errors return `{"error": "<message>"}` with an
appropriate HTTP status code.

### `GET /api/status`

Calls `checkStatus` on the MCP server.  Use this to verify the AnyLog node is
reachable.

```json
{"status": "ok", "mcp_url": "https://...", "result": {...}}
```

An empty `"error"` in the result typically means the node is down, not an MCP
protocol issue.

---

### `GET /api/databases`

Lists all databases known to the network via `listNetworkDatabases`.

```json
{"databases": ["wind_turbine", "manufacturing_historian", ...]}
```

---

### `GET /api/uns/databases`

Discovers all databases referenced in UNS policies for the active MCP
connector.  Falls back to `listNetworkDatabases` if no UNS policies are found.
Results are cached for `CACHE_TTL_S`.

```json
{"databases": ["drilling_data", ...], "source": "uns", "mcp_url": "https://..."}
```

`source` is `"cache"` on subsequent calls within the TTL window.

---

### `GET /api/uns/discover`

Returns all UNS policies (`listPolicies` with `policyType=uns`).

```json
{"policies": [...], "count": 42, "mcp_url": "https://..."}
```

---

### `GET /api/uns/policies?type=<type>[&where=<condition>]`

Returns policies of any type.  `type` defaults to `uns`.

```json
{"policies": [...], "count": 12}
```

---

### `GET /api/tables?dbms=<name>`

Lists all tables in a database.

```json
{"dbms": "drilling_data", "tables": ["rig_sensor", "mud_weight", ...]}
```

---

### `GET /api/columns?dbms=<name>&table=<name>`

Lists columns for a table.

```json
{"dbms": "drilling_data", "table": "rig_sensor", "columns": [...]}
```

---

### `POST /api/query`  (alias: `POST /api/mcp/query`)

Execute a raw SQL query against any database.  AnyLog routes to all nodes
hosting the table automatically (no `nodes` parameter needed).

Request body:
```json
{"dbms": "drilling_data", "sql": "SELECT * FROM rig_sensor LIMIT 100"}
```

Response:
```json
{"results": [...], "row_count": 100, "dbms": "drilling_data"}
```

**AnyLog SQL constraints to be aware of:**

- Use `NOW() - N hours` for relative time ranges.
- `ORDER BY` and `GROUP BY` with aggregates are not supported on all node
  types — aggregate client-side when in doubt.
- `SELECT *` on tag-per-table schemas (e.g. `manufacturing_historian`) returns
  only metadata columns; use `col:'value'` explicitly.

---

### `POST /api/query/increment`

Execute a time-bucketed aggregation query using AnyLog's `increments()`
function.  Tries `queryWithIncrement` first; falls back to
`executeQuery` with an `increments()` SQL projection; falls back further to
a raw `SELECT` if both fail.

Request body:
```json
{
  "dbms":           "drilling_data",
  "table":          "rig_sensor",
  "timeColumn":     "timestamp",
  "startTime":      "2025-01-01 00:00:00",
  "endTime":        "2025-01-02 00:00:00",
  "intervalLength": 15,
  "timeUnit":       "minute",
  "projections":    ["avg(wob)", "avg(rop)", "max(hook_load)"],
  "where":          "rig_id = 'RIG-01'"
}
```

Response:
```json
{"results": [...], "row_count": 96}
```

---

### `GET /api/nodes`

Returns the list of nodes in the network (`getNodesList`).

---

### `GET /api/nodes/monitor?type=<type>[&nodes=<list>]`

Monitor node status via `monitorNodes`.  `type` defaults to `status`.

---

### `POST /api/cache/clear`

Flush the entire in-memory cache.

```json
{"status": "cleared"}
```

---

### `GET /api/worker/status`

Inspect the worker queue without making an MCP call.

```json
{
  "queue_depth":  2,
  "in_flight":    ["executeQuery:{...}"],
  "call_delay_s": 1.5,
  "mcp_url":      "https://..."
}
```

---

### `GET /api/log/snapshot`

Return the last 200 API call events as JSON (no streaming).

---

### `GET /api/log/stream`

Server-Sent Events stream of API call events in real time.  The debug panel
(`/debug`) consumes this stream.

---

### `GET /debug`

Self-contained API call log panel with live SSE feed, filtering, and
pause/resume controls.  Useful for watching what the bridge is doing while
a dashboard runs.

---

### `GET /`

Serves `timbergrove_dashboard.html`, `enterprise_c_spc_mcp_dashboard.html`,
or `dashboard.html` (first one found in the script directory), or a plain
HTML index listing available endpoints.

---

## TLS / HTTPS

The bridge can serve its own HTTP frontend over HTTPS independently of whether
the upstream MCP URL uses HTTP or HTTPS.

| Scenario | Flags |
|----------|-------|
| Auto-generate self-signed cert | `--ssl` |
| Existing cert + key | `--ssl-cert cert.pem --ssl-key key.pem` |
| Explicit cert paths (implies `--ssl`) | `--ssl-cert ... --ssl-key ...` |

The auto-generated certificate is written to `mcp_bridge_cert.pem` /
`mcp_bridge_key.pem` in the working directory and reused on subsequent starts.

**Warning:** if `--mcp-url` uses `http://` on an AnyLog TLS port (one
containing `:320`), the bridge logs a `WARNING` at startup because the
connection will silently hang for the full timeout.  Use `https://` for all
AnyLog nodes that require TLS.

The bridge itself connects to the MCP server with SSL verification **disabled**
by default (`verify_ssl=False`) because AnyLog nodes typically use self-signed
certificates.

---

## Logging

Three log levels are available:

| Mode | Flag | What you see |
|------|------|--------------|
| Normal | (default) | INFO — every HTTP request, MCP call, cache hit/miss |
| Quiet | `--quiet` | WARNING and ERROR only |
| Debug 1 | `--debug` | DEBUG — full payloads, SSE line-by-line trace |
| Debug 2 | `--debug 2` | All of debug 1, plus step-through on query calls |

### Debug level-2 step-through

When `--debug 2` is active, the worker pauses before each `executeQuery` or
`queryWithIncrement` call and prints the full parameters to stderr:

```
─────────────────────────────────────────────────────────────────
[DEBUG-2] STEP  tool=executeQuery
[DEBUG-2] queue depth remaining: 2
[DEBUG-2]   dbms = drilling_data
[DEBUG-2]   sql  = SELECT avg(wob) FROM rig_sensor WHERE timestamp >= ...
─────────────────────────────────────────────────────────────────
[DEBUG-2] Press Enter to send  |  's' skip wait  |  'q' quit stepping
```

All queued HTTP requests (and therefore all dashboard polls) remain blocked
while you inspect the prompt.  Keystrokes:

| Key | Action |
|-----|--------|
| Enter | Send the call immediately |
| `s` | Skip this wait (send without pausing) |
| `q` | Disable level-2 stepping for the rest of the session (downgrade to level 1) |

Log format: `YYYY-MM-DD HH:MM:SS [LEVEL] logger: message`

Add `--log-file path/to/bridge.log` to mirror all log output to a file.

### Reading the log

Key prefixes:

| Prefix | Meaning |
|--------|---------|
| `HTTP ▶` | Inbound HTTP request received |
| `HTTP ◀` | HTTP response sent (with status + ms) |
| `MCP >` | MCP call dispatched to the SSE server |
| `MCP <` | MCP response received (rows / chars / ms) |
| `MCP x` | MCP error response |
| `CACHE hit` | Request served from cache — no MCP call made |
| `DEDUP` | Duplicate request joined an in-flight job |
| `WORKER dequeue` | Worker picked up a job (shows queue depth) |
| `WORKER error` | Worker caught an exception during an MCP call |
| `McpSseClient stream-N` | SSE connection lifecycle (endpoint, POST, message) |

---

## Debug Panel

Browse to `http://localhost:8080/debug` while dashboards are running.

Features:
- **Live SSE feed** — events appear as they happen (no polling).
- **Pause / Resume** — freeze the display without disconnecting.
- **Filter** — filter by text or event kind (`http_req`, `http_resp`,
  `mcp_req`, `mcp_resp`).
- **History** — the panel loads the last 200 events on connect, so you can
  open it mid-session and see what happened.

---

## MCP-over-SSE Protocol Notes

AnyLog implements a per-request SSE model rather than a persistent MCP
connection:

1. Client opens `GET /mcp/sse` — a streaming HTTP response.
2. Server sends an `endpoint` event containing a session-specific POST URL.
3. Client POSTs the JSON-RPC request to that URL (while the SSE stream stays
   open).
4. Server sends the response as a `message` event on the SSE stream.

**Two-stream variant (Timbergrove):** the server closes stream-1 before the
response arrives.  `McpSseClient` detects this, opens stream-2, and receives
the `message` event there.

**Single-stream variant (mark-demo):** the response arrives on stream-1 before
it closes.  No second connection is needed.

The `McpSseClient` reads the SSE stream one byte at a time directly from the
urllib3 socket to avoid Python-level read buffering, which caused multi-second
delivery delays when using `iter_content()`.

---

## Changelog

### v5.2
- **`--debug [LEVEL]`** replaces the old boolean `--debug` flag. `--debug` / `--debug 1` retains the previous behaviour (Python DEBUG logging, verbose payloads). `--debug 2` adds step-through mode: the worker prints full call parameters to stderr and blocks on `input()` before each `executeQuery` or `queryWithIncrement`, pausing the entire proxy. The operator can press Enter to proceed, `s` to skip the current pause, or `q` to downgrade back to level 1 for the rest of the session.

### v5.1
- **`--mcp-timeout SECS`** — optional per-call hard kill timer. When set, a `threading.Timer` fires after the specified number of seconds and closes the open SSE socket(s). Closing the socket causes `resp.raw.read(1)` to return `b""` (EOF), breaking the read loop cleanly without leaving threads blocked. The call then raises `TimeoutError` back to the worker, which sets `job.error` and unblocks the waiting HTTP thread. The timer is always cancelled if a response arrives before the deadline. Disabled by default.

### v5.0
Two fixes that together guarantee only one MCP call is ever in-flight:

**Fix 1 — `McpSseClient._call` lock scope (the primary parallelism bug):**
`self._lock` previously only wrapped the `req_id` increment and was released
before any network I/O.  On the Timbergrove two-stream path, stream-1 closes
before the response arrives, then stream-2 opens to receive it.  In the gap
between stream-1 closing and stream-2 opening, a second call could open its
own stream-1 — and the AnyLog server would deliver the first call's response
to the second call's open stream, corrupting both results.  `self._lock` now
wraps the entire SSE session (both streams) so no other call can open a
connection between stream-1 close and stream-2 open.

**Fix 2 — UNS database discovery queue bypass:**
`_discover_databases_from_uns` previously spawned a side thread that called
`_call_mcp()` directly, bypassing the job queue and racing with the worker.
The entire discovery is now submitted as a single opaque job and runs inside
the worker thread with `CALL_DELAY_S` pacing between sub-calls.

**Also:** removed dead `_parse_sse_stream` method (leftover from an earlier
prototype that used `iter_content` buffering).

### v4.4
- Replaced `mcp-proxy` subprocess with `McpSseClient` (pure `requests`).
- One-byte-at-a-time SSE reads to eliminate buffering delays.
- Two-stream protocol for Timbergrove-style servers.

### v4.3
- Removed `nodes` parameter from all query calls (AnyLog auto-routes).
- `where` clause forwarding in `/api/query/increment`.

### v4.0 – v4.2
- `--mcp-url`, `--port`, `--host` CLI flags.
- HTTPS / TLS support with auto-generated self-signed certificates.
- `--quiet` / `--debug` / `--log-file` logging controls.
- SSE debug panel (`/debug`, `/api/log/stream`).

### v3.0
- Serialized job queue (single worker thread).
- TTL cache.
- Job deduplication.
