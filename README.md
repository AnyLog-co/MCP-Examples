# MCP Web Bridge

**Version 4.1** | Python 3.9+ | Flask · flask-cors

Bridges HTTP REST requests from browser dashboards to the AnyLog MCP SSE
protocol.  A single long-lived `mcp-proxy` subprocess handles the MCP
connection; a single worker thread serialises every call to it.  Browser
dashboards speak plain JSON over HTTP — no MCP SDK required on the
client side.

---

## Contents

- [How it works](#how-it-works)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Quick start](#quick-start)
- [CLI reference](#cli-reference)
- [start_bridge.sh](#start_bridgesh)
- [API reference](#api-reference)
- [UNS database discovery](#uns-database-discovery)
- [Caching](#caching)
- [Calling from JavaScript](#calling-from-javascript)
- [Running multiple connectors](#running-multiple-connectors)
- [HTTPS / TLS](#https--tls)
- [Logging](#logging)
- [Troubleshooting](#troubleshooting)
- [Version history](#version-history)

---

## How it works

```
Browser dashboard
      │  HTTP POST /api/query  {dbms, sql}
      ▼
┌─────────────────────────────────────────────┐
│  Flask  (threaded, any number of requests)  │
│                                             │
│  HTTP handler ──► _enqueue(job) ──► wait   │
│                        │          (≤60 s)  │
│              ┌─────────▼──────────┐         │
│              │  Job Queue (FIFO)  │         │
│              └─────────┬──────────┘         │
│                        │  one at a time     │
│              ┌─────────▼──────────┐         │
│              │   Worker thread    │         │
│              │  _call_mcp(tool)   │         │
│              └─────────┬──────────┘         │
└────────────────────────┼────────────────────┘
                         │  JSON-RPC over stdio
                         ▼
                   mcp-proxy binary
                         │  HTTPS SSE
                         ▼
               AnyLog MCP SSE server
               (e.g. :32049/mcp/sse)
                         │
                         ▼
              AnyLog distributed network
```

Key design rules:
- **HTTP threads never call MCP directly.** They post a `Job` and block on a
  `threading.Event` until the worker signals completion.
- **One worker, one call at a time.** `CALL_DELAY_S` (default 1.5 s) is
  observed between every MCP call, preventing SSE server overload regardless
  of how many browser tabs are open.
- **Duplicate-request deduplication.** If 10 tabs ask for the same table list
  simultaneously, only one MCP call is made; all 10 waiters share the result.
- **TTL cache.** Metadata results (UNS, tables, status) are cached for 5 min;
  query results for 30 s.  Cache-hit responses are returned immediately without
  touching the queue.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.9+ | Tested on 3.10 / 3.11 |
| `mcp-proxy` binary | Installed in your AnyLog venv via `pip install mcp-proxy` |
| AnyLog MCP SSE server | Running and reachable, e.g. `https://172.79.89.206:32049/mcp/sse` |
| `flask` | `pip install flask` |
| `flask-cors` | `pip install flask-cors` |
| `cryptography` _(optional)_ | `pip install cryptography` — only needed for `--ssl` auto-cert generation when the `openssl` CLI is unavailable |

---

## Installation

```bash
# 1. Activate your AnyLog virtual environment (must contain mcp-proxy)
source /path/to/venv/bin/activate

# 2. Install Python dependencies
pip install flask flask-cors

# 3a. (Optional) Install cryptography for --ssl auto-cert generation
#     Skip if your system already has the openssl CLI (macOS / most Linux distros)
pip install cryptography

# 3b. Make start_bridge.sh executable
chmod +x start_bridge.sh
```

---

## Quick start

```bash
# Timbergrove connector
./start_bridge.sh --mcp-url https://172.79.89.206:32049/mcp/sse

# AnyLog Prove-IT connector on a different port
./start_bridge.sh --mcp-url https://50.116.13.109:32049/mcp/sse --port 8081

# Test it
curl http://localhost:8080/api/status
curl http://localhost:8080/api/uns/databases

# HTTPS with auto-generated self-signed certificate
./start_bridge.sh --mcp-url https://50.116.13.109:32049/mcp/sse --ssl
curl -k https://localhost:8080/api/status

# Quiet mode — warnings and errors only (no per-request log lines)
./start_bridge.sh --mcp-url https://172.79.89.206:32049/mcp/sse --quiet

# Log everything to a file as well as stderr
./start_bridge.sh --mcp-url https://172.79.89.206:32049/mcp/sse --log-file bridge.log
```

The bridge starts, spawns `mcp-proxy`, performs the MCP initialise handshake,
and then listens on `http://0.0.0.0:8080` (or your chosen port).  When `--ssl`
is active, the scheme changes to `https://`.

---

## CLI reference

```
python3 mcp_web_bridge.py [OPTIONS]
```

| Option | Default | Description |
|---|---|---|
| `--mcp-url URL` | `https://172.79.89.206:32049/mcp/sse` | MCP SSE server to connect to |
| `--mcp-proxy PATH` | `…/venv/bin/mcp-proxy` | Path to the `mcp-proxy` binary |
| `--port INT` | `8080` | HTTP/HTTPS port to listen on |
| `--host ADDR` | `0.0.0.0` | Interface to bind to |
| `--call-delay FLOAT` | `1.5` | Seconds between MCP calls |
| `--debug` | off | Enable Flask debug mode + verbose logging (overrides `--quiet`) |
| `--quiet`, `-q` | off | Suppress INFO log chatter; show warnings and errors only |
| `--log-file PATH` | _(none)_ | Append all log output to this file **in addition to** stderr |
| `--ssl` | off | Serve the Flask frontend over HTTPS |
| `--ssl-cert CERT.pem` | _(auto)_ | Path to an existing PEM certificate (implies `--ssl`) |
| `--ssl-key KEY.pem` | _(auto)_ | Path to an existing PEM private key (implies `--ssl`) |
| `-h, --help` | | Show help and exit |

**`--mcp-url`** is the key argument.  It selects which AnyLog network the
bridge talks to.  Every endpoint response includes the active `mcp_url` so
dashboards can confirm they are hitting the right connector.

**Logging options** (`--quiet`, `--debug`, `--log-file`) are independent and
composable.  `--debug` overrides `--quiet` when both are given.  `--log-file`
always appends (never truncates) and writes the same lines that go to stderr.

**TLS options** — see [HTTPS / TLS](#https--tls) for full details.

---

## start_bridge.sh

A convenience launcher that merges environment-variable defaults with CLI
arguments.  Explicit CLI flags always win over environment variables.

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `BRIDGE_MCP_URL` | `https://172.79.89.206:32049/mcp/sse` | MCP SSE server URL |
| `BRIDGE_MCP_PROXY` | `…/venv/bin/mcp-proxy` | Path to `mcp-proxy` |
| `BRIDGE_PORT` | `8080` | HTTP listen port |
| `BRIDGE_HOST` | `0.0.0.0` | Bind interface |
| `BRIDGE_CALL_DELAY` | `1.5` | Seconds between MCP calls |
| `BRIDGE_VENV` | _(empty)_ | Virtualenv path to auto-activate |

### Usage examples

```bash
# Basic — Timbergrove connector
./start_bridge.sh --mcp-url https://172.79.89.206:32049/mcp/sse

# Dynics Prove-IT connector, port 8081
./start_bridge.sh \
  --mcp-url https://172.79.89.206:32049/mcp/sse \
  --port 8081

# AnyLog Prove-IT connector via env var
BRIDGE_MCP_URL=https://50.116.13.109:32049/mcp/sse ./start_bridge.sh

# Auto-activate a specific venv
BRIDGE_VENV=/home/mark/anylog/venv ./start_bridge.sh \
  --mcp-url https://172.79.89.206:32049/mcp/sse

# Slow down calls (useful for busy servers)
./start_bridge.sh --mcp-url https://... --call-delay 3.0

# Debug mode
./start_bridge.sh --mcp-url https://... --debug

# Quiet logging — warnings and errors only
./start_bridge.sh --mcp-url https://... --quiet

# Log to file and suppress console chatter
./start_bridge.sh --mcp-url https://... --quiet --log-file /var/log/bridge.log

# HTTPS with auto-generated self-signed certificate
./start_bridge.sh --mcp-url https://... --ssl

# HTTPS with your own certificate
./start_bridge.sh --mcp-url https://... \
  --ssl-cert /etc/ssl/bridge.crt \
  --ssl-key  /etc/ssl/bridge.key
```

---

## API reference

All responses are JSON.  Errors return `{"error": "message"}` with an
appropriate HTTP status code.

---

### `GET /api/status`

Check MCP connectivity.

**Response**
```json
{
  "status": "ok",
  "mcp_url": "https://172.79.89.206:32049/mcp/sse",
  "result": { ... }
}
```

**Error** → HTTP 503 with `{"status": "error", "error": "...", "mcp_url": "..."}`

---

### `GET /api/uns/databases`

**UNS-aware database discovery.** Scans all UNS policies for the active
connector and returns every unique `dbms` value found.  Falls back to
`listNetworkDatabases` if UNS policies have no `dbms` fields.  Result cached
for 5 minutes.

Use this endpoint at dashboard startup to automatically determine which
databases to query, rather than hardcoding database names.

**Response**
```json
{
  "databases": ["timbergrove", "lsl_demo"],
  "source": "uns",
  "mcp_url": "https://172.79.89.206:32049/mcp/sse"
}
```

`source` is `"uns"` when databases came from UNS policies, `"network"` when
they came from the fallback `listNetworkDatabases` call, `"cache"` when the
cached result was returned.

---

### `GET /api/uns/discover`

Return all UNS policies for the active connector (raw list).

**Response**
```json
{
  "policies": [ { "uns": { ... } }, ... ],
  "count": 84,
  "mcp_url": "..."
}
```

---

### `GET /api/uns/policies?type=<type>[&where=<condition>]`

Return policies of any type with optional WHERE filter.

| Parameter | Required | Description |
|---|---|---|
| `type` | no (default `uns`) | Policy type: `uns`, `operator`, `table`, `rig`, etc. |
| `where` | no | Filter condition, e.g. `rig_id='RIG-TX-001'` |

**Response**
```json
{
  "policies": [ ... ],
  "count": 4
}
```

---

### `GET /api/tables?dbms=<database>`

List all tables in a database.

**Response**
```json
{
  "dbms": "timbergrove",
  "tables": ["rig_data"]
}
```

---

### `GET /api/columns?dbms=<database>&table=<table>`

List column definitions for a table.

**Response**
```json
{
  "dbms": "timbergrove",
  "table": "rig_data",
  "columns": [
    { "name": "timestamp", "type": "timestamp" },
    { "name": "rig_id",    "type": "varchar" },
    { "name": "rop",       "type": "float" },
    ...
  ]
}
```

---

### `GET /api/databases`

List all databases visible in the network via `listNetworkDatabases`.

**Response**
```json
{ "databases": ["timbergrove", "lsl_demo", "system"] }
```

---

### `POST /api/query`

Execute a SQL query distributed across the network.

**Request body**
```json
{
  "dbms":  "timbergrove",
  "sql":   "SELECT * FROM rig_data WHERE rig_id='RIG-TX-001' AND timestamp >= NOW() - 1 hour LIMIT 50",
  "nodes": "172.79.89.206:32049"
}
```

| Field | Required | Description |
|---|---|---|
| `dbms` | yes | Target database name |
| `sql` | yes | SQL query string. Use `NOW() - N hours/minutes/days` for time ranges. No nested queries or JOINs. |
| `nodes` | no | Comma-separated `IP:Port` list to target specific nodes; omit to query all nodes |

**Time filter syntax**
```sql
WHERE timestamp >= NOW() - 24 hours
WHERE timestamp >= NOW() - 30 minutes
WHERE timestamp >= NOW() - 7 days
```

**Response**
```json
{
  "results": [ { "timestamp": "...", "rig_id": "RIG-TX-001", "rop": 42.1, ... }, ... ],
  "row_count": 50,
  "dbms": "timbergrove"
}
```

---

### `POST /api/query/increment`

Execute a time-bucketed aggregation query (calls `queryWithIncrement` on the
MCP server).

**Request body**
```json
{
  "dbms":           "timbergrove",
  "table":          "rig_data",
  "timeColumn":     "timestamp",
  "startTime":      "NOW() - 24 hours",
  "endTime":        "NOW()",
  "intervalLength": 1,
  "timeUnit":       "hour",
  "projections":    ["avg(rop)", "max(wob)", "min(rpm)", "count(rop)"],
  "nodes":          "172.79.89.206:32049"
}
```

| Field | Required | Description |
|---|---|---|
| `dbms` | yes | Database name |
| `table` | yes | Table name |
| `timeColumn` | yes | Name of the timestamp column |
| `startTime` | yes | Start of range — ISO 8601 or `NOW() - N unit` |
| `endTime` | yes | End of range — ISO 8601 or `NOW()` |
| `intervalLength` | yes | Integer — number of time units per bucket |
| `timeUnit` | yes | `minute` · `hour` · `day` · `week` · `month` · `year` |
| `projections` | yes | Array of aggregate expressions, e.g. `["avg(rop)", "max(wob)"]` |
| `nodes` | no | Comma-separated `IP:Port` to target specific nodes |

**Response**
```json
{
  "results": [
    { "timestamp": "2026-02-23T00:00:00Z", "avg_rop": 38.4, "max_wob": 22.1, ... },
    ...
  ],
  "row_count": 24
}
```

---

### `GET /api/nodes`

List all nodes registered in the network.

**Response**
```json
{
  "nodes": [
    { "type": "operator", "name": "timbergrove-op1", "ip": "172.79.89.206", "port": 32048 },
    ...
  ]
}
```

---

### `GET /api/nodes/monitor?type=<status_type>[&nodes=<ip:port,...>]`

Get runtime status for one or more nodes.

| Parameter | Default | Options |
|---|---|---|
| `type` | `status` | `status` · `resources` · `version` · `cpu` |
| `nodes` | _(all)_ | Comma-separated `IP:Port` list |

**Response**
```json
{ "result": { ... } }
```

---

### `POST /api/cache/clear`

Flush the entire TTL cache, forcing fresh MCP calls on the next request.

**Response**
```json
{ "status": "cleared" }
```

---

### `GET /api/worker/status`

Inspect the internal job queue.

**Response**
```json
{
  "queue_depth":  2,
  "in_flight":    ["executeQuery:{...}", "listTables:{...}"],
  "call_delay_s": 1.5,
  "mcp_url":      "https://172.79.89.206:32049/mcp/sse"
}
```

---

### `GET /`

Serves a local HTML dashboard file if one is found alongside the script.
Checks for these filenames in order:

1. `timbergrove_dashboard.html`
2. `enterprise_c_spc_mcp_dashboard.html`
3. `dashboard.html`

If none is found, returns a plain-text endpoint listing.

---

## UNS database discovery

Dashboards should call `GET /api/uns/databases` at startup instead of
hardcoding database names.  The endpoint traverses UNS policies in this order:

1. Calls `listPolicyTypes` to check that a `uns` policy type exists.
2. Calls `listPolicies(policyType="uns")` and collects every `dbms` field from
   the returned policies.
3. If step 2 yields no databases (UNS policies have no `dbms` fields), falls
   back to `listNetworkDatabases`.
4. Returns a deduplicated, sorted list of database names.

This means the same dashboard HTML file can be used against different MCP
connectors (Timbergrove, Dynics, AnyLog Prove-IT) without code changes —
just point the bridge at a different `--mcp-url` and the dashboard discovers
its databases automatically.

**JavaScript example**
```javascript
const { databases } = await fetch('/api/uns/databases').then(r => r.json());
// databases = ["timbergrove"]   (for Timbergrove connector)
// databases = ["lsl_demo", "manufacturing_historian"]  (for Dynics / AnyLog)
```

---

## Caching

| Data type | TTL | Affected endpoints |
|---|---|---|
| Metadata | 300 s (5 min) | `/api/status`, `/api/tables`, `/api/columns`, `/api/databases`, `/api/uns/*`, `/api/nodes` |
| Query results | 30 s | `/api/query`, `/api/query/increment` |

Cache keys are `"<tool_name>:<sorted-json-params>"`.  Identical requests from
concurrent tabs share a single MCP call and a single cache entry.

To force fresh data: `POST /api/cache/clear`.

---

## Calling from JavaScript

```javascript
// ── Status check ───────────────────────────────────────────
const status = await fetch('/api/status').then(r => r.json());
console.log(status.mcp_url, status.status);

// ── Database discovery via UNS ──────────────────────────────
const { databases } = await fetch('/api/uns/databases').then(r => r.json());

// ── List tables ─────────────────────────────────────────────
const { tables } = await fetch('/api/tables?dbms=timbergrove').then(r => r.json());

// ── Execute a query ─────────────────────────────────────────
const { results, row_count } = await fetch('/api/query', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    dbms: 'timbergrove',
    sql: "SELECT timestamp, rig_id, rop, wob FROM rig_data WHERE rig_id='RIG-TX-001' AND timestamp >= NOW() - 1 hour LIMIT 100"
  })
}).then(r => r.json());

// ── Incremental (time-bucketed) query ───────────────────────
const { results: hourly } = await fetch('/api/query/increment', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    dbms: 'timbergrove',
    table: 'rig_data',
    timeColumn: 'timestamp',
    startTime: 'NOW() - 24 hours',
    endTime: 'NOW()',
    intervalLength: 1,
    timeUnit: 'hour',
    projections: ['avg(rop)', 'max(wob)', 'min(rpm)']
  })
}).then(r => r.json());

// ── Clear cache ─────────────────────────────────────────────
await fetch('/api/cache/clear', { method: 'POST' });
```

---

## Running multiple connectors

Each connector needs its own bridge instance on a different port.

```bash
# Terminal 1 — Timbergrove on port 8080
./start_bridge.sh \
  --mcp-url https://172.79.89.206:32049/mcp/sse \
  --port 8080

# Terminal 2 — AnyLog Prove-IT on port 8081
./start_bridge.sh \
  --mcp-url https://50.116.13.109:32049/mcp/sse \
  --port 8081

# Terminal 3 — Dynics Prove-IT on port 8082
./start_bridge.sh \
  --mcp-url https://172.79.89.206:32049/mcp/sse \
  --port 8082
```

Dashboards can let the user enter an `IP:port` and will hit whichever bridge
instance is running there.

---

## HTTPS / TLS

The bridge can serve its HTTP frontend over TLS so that dashboards hosted on
`https://` pages can call it without mixed-content browser errors.

> **Note:** TLS here applies only to the **browser ↔ bridge** leg.  The
> **bridge ↔ AnyLog MCP SSE** leg is always HTTPS via `mcp-proxy` and is
> unaffected by these options.

### Quickstart — self-signed certificate

```bash
# Auto-generates mcp_bridge_cert.pem + mcp_bridge_key.pem in the working directory
python3 mcp_web_bridge.py --mcp-url https://... --ssl

# Then test (skip cert verification for self-signed)
curl -k https://localhost:8080/api/status
```

The certificate is generated once and reused on subsequent starts.  It is
valid for 365 days.  Generation uses the `openssl` CLI if available, otherwise
falls back to the `cryptography` package.

### Using an existing certificate

Supply paths to your own PEM files (e.g. from Let's Encrypt or an internal CA):

```bash
python3 mcp_web_bridge.py --mcp-url https://... \
  --ssl-cert /etc/ssl/certs/bridge.crt \
  --ssl-key  /etc/ssl/private/bridge.key
```

Providing `--ssl-cert` or `--ssl-key` automatically enables TLS — you do not
need `--ssl` as well.

### Certificate trust

| Scenario | Action |
|---|---|
| Self-signed, development | Use `curl -k` or accept the browser security exception once |
| Self-signed, shared team | Import `mcp_bridge_cert.pem` into your OS / browser trust store |
| CA-signed (Let's Encrypt) | Browsers trust automatically; no exceptions needed |

### Dashboard `fetch` URL

When the bridge is running over HTTPS, update dashboard base URLs:

```javascript
const BRIDGE = 'https://localhost:8080';   // was http://
const status = await fetch(`${BRIDGE}/api/status`).then(r => r.json());
```

---

## Logging

| Flag | Log level | Use case |
|---|---|---|
| _(default)_ | `INFO` | Normal operation — one line per HTTP request and MCP call |
| `--quiet` / `-q` | `WARNING` | Production / low-noise — warnings and errors only |
| `--debug` | `DEBUG` | Diagnosis — full JSON-RPC traffic, overrides `--quiet` |

`--log-file PATH` appends all log output to the named file **in addition to**
stderr.  The file is opened in UTF-8 append mode, so restarts accumulate rather
than overwrite.

```bash
# Minimal console noise, full log preserved on disk
python3 mcp_web_bridge.py --mcp-url https://... --quiet --log-file bridge.log

# Debug everything, also save to file
python3 mcp_web_bridge.py --mcp-url https://... --debug --log-file debug.log
```

---

## Troubleshooting

**Bridge starts but `/api/status` returns 503**

The `mcp-proxy` binary cannot reach the SSE server.
- Confirm the AnyLog node is running: `curl -k https://<ip>:<port>/`
- Confirm `mcp-proxy` is on PATH or `--mcp-proxy` points to the right binary.
- Check firewall / VPN connectivity.

**Responses take > 30 seconds**

The MCP server is under load or the SSE connection is slow.
- Increase `--call-delay` (e.g. `--call-delay 3.0`) to reduce call rate.
- Increase `JOB_TIMEOUT_S` in the source if your queries are legitimately slow.

**Stale data returned**

The cache may be serving old results.
```bash
curl -X POST http://localhost:8080/api/cache/clear
```

**`/api/uns/databases` returns an empty list**

- The active connector has no UNS policies or its UNS policies have no `dbms`
  fields, and `listNetworkDatabases` returned nothing.
- Verify with: `curl http://localhost:8080/api/uns/discover | python3 -m json.tool`
- Check `GET /api/databases` as a raw fallback.

**`mcp-proxy` crashes and calls hang**

The worker detects a dead process (`poll() is not None`) and automatically
respawns it on the next call.  Check `stderr` output for error details.
Use `--debug` to see full JSON-RPC traffic.

**Browser shows "mixed content" error when calling the bridge**

Your dashboard is served over `https://` but the bridge is running on plain
`http://`.  Enable TLS on the bridge:
```bash
./start_bridge.sh --mcp-url https://... --ssl
```
Then update the dashboard base URL to `https://localhost:<port>`.

**`--ssl` fails: "Cannot generate a self-signed certificate"**

Neither the `openssl` CLI nor the `cryptography` package was found.
```bash
pip install cryptography
```
Or generate a cert manually and supply the paths:
```bash
openssl req -x509 -newkey rsa:2048 -keyout bridge.key -out bridge.crt -days 365 -nodes -subj "/CN=mcp-web-bridge"
./start_bridge.sh --ssl-cert bridge.crt --ssl-key bridge.key ...
```

**Log file is not being written**

Verify the path is writable by the user running the bridge.  The bridge logs
an error to stderr and continues without file logging if the file cannot be
opened.  Use an absolute path to avoid working-directory surprises:
```bash
./start_bridge.sh --log-file /var/log/mcp_bridge.log ...
```

---

## Version history

| Version | Date | Changes |
|---|---|---|
| **4.1** | 2026-03-05 | `--quiet` / `-q` flag (WARNING-level logging); `--log-file` (append log to file); `--ssl` / `--ssl-cert` / `--ssl-key` (HTTPS frontend with auto self-signed cert generation via `openssl` CLI or `cryptography` package) |
| **4.0** | 2026-02-23 | `--mcp-url` CLI arg; UNS-aware `/api/uns/databases` endpoint; `--mcp-proxy`, `--port`, `--host`, `--call-delay`, `--debug` args; `start_bridge.sh` env-var + CLI merge; runtime `CFG` dict replaces hardcoded constants |
| 3.0 | 2026-02-18 | Single-worker job queue; TTL cache; duplicate-request dedup; `/api/uns/policies`, `/api/cache/clear`, `/api/worker/status` endpoints; fixed concurrent-request MCP corruption |
| 2.0 | 2026-02-13 | Per-request `_pending` dict routing; `call_lock` serialisation; fixed MCP response parsing (`content[0].text`) |
| 1.0 | 2026-02-10 | Initial release |
