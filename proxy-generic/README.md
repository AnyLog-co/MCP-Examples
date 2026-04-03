# proxy-generic — AnyLog Flask Proxy

A unified Python/Flask proxy that works in two modes, auto-detected from the
`--anylog-url` flag:

| Mode | URL pattern | What it does |
|---|---|---|
| **REST** | `http://HOST:PORT` | Transparent pass-through to AnyLog REST API — like nginx but Python |
| **MCP** | `http://HOST:PORT/mcp/sse` | Bridges HTTP ↔ MCP/SSE protocol with strict single-call serialization |

```
Browser  →  http://localhost:8080/api/query  →  anylog_proxy.py
                                                  ├── REST mode  →  AnyLog REST API
                                                  └── MCP  mode  →  AnyLog MCP/SSE
Browser  →  http://localhost:8080/<file>.html →  serves ../html/
```

---

## Files

```
proxy-generic/
├── anylog_proxy.py       ← unified proxy (REST + MCP)
├── requirements.txt      ← flask, flask-cors, requests
├── Dockerfile
├── docker-compose.yaml
└── README.md             ← this file

../html/                  ← dashboards served by the proxy (shared with proxy-nginx)
    ├── dashboard-node-status.html
    ├── dashboard-power-plant.html
    └── rig_data.html
```

---

## Quick Start

### Without Docker

```bash
pip install -r requirements.txt

# REST mode
python3 anylog_proxy.py --anylog-url http://HOST:PORT --html-dir ../html

# MCP mode  (/mcp/sse suffix auto-selects MCP mode)
python3 anylog_proxy.py --anylog-url http://HOST:PORT/mcp/sse --html-dir ../html
```

Open `http://localhost:8080/<dashboard>.html`, set Mode → `proxy`,
Proxy URL → `http://localhost:8080`.

### With Docker

**1. Set your AnyLog node URL** in `docker-compose.yaml`:
```yaml
environment:
  - ANYLOG_NODE_URL=http://HOST:PORT    # REST mode
  - MODE=rest                           # or: mcp
  - PROXY_PORT=8080
```

For MCP mode:
```yaml
environment:
  - ANYLOG_NODE_URL=http://HOST:PORT/mcp/sse
  - MODE=mcp
  - PROXY_PORT=8080
```

**2. Start:**
```bash
docker compose up -d --build
docker logs -f rest-proxy
```

**3. Check it's up:**
```bash
curl http://localhost:8080/api/status
# {"status": "ok", "mode": "rest", ...}
```

---

## REST vs MCP mode

### REST mode (default)

Behaves like nginx — translates browser requests into AnyLog REST API calls
and returns the response. No MCP/SSE involved.

Best when:
- The AnyLog node is accessible over plain HTTP
- You want a lightweight, stateless proxy
- You don't need UNS discovery or `queryWithIncrement`

### MCP mode

Bridges HTTP to the MCP/SSE protocol. Runs a single worker thread that
serializes all MCP calls — only one call is ever in-flight at a time.
Includes TTL cache, job deduplication, and fallback query paths.

Best when:
- The AnyLog deployment exposes an MCP endpoint (`/mcp/sse`)
- You need UNS-aware database discovery (`/api/uns/databases`)
- You want `queryWithIncrement` with automatic fallback to `increments()` SQL

---

## Body Formats

Both modes accept two request body shapes at `/api/query`:

**AnyLog REST format** (sent by dashboards in nginx/direct mode):
```json
{
  "User-Agent":  "AnyLog/1.23",
  "command":     "sql mydb format=json:list and stat=false  SELECT ...",
  "destination": "network"
}
```

**Simple format** (cleaner, also accepted):
```json
{"dbms": "mydb", "sql": "SELECT * FROM mytable LIMIT 100"}
```

Both return: `{"results": [...], "row_count": N, "dbms": "mydb"}`

---

## API Endpoints

### Both modes

| Endpoint | Method | Description |
|---|---|---|
| `/api/query` | POST | SQL query — both body formats accepted |
| `/api/query/increment` | POST | Time-bucketed aggregation |
| `/api/status` | GET | Node health check |
| `/api/cache/clear` | POST | Flush result cache |
| `/debug` | GET | Live API call log panel |
| `/<file>.html` | GET | Serve from `--html-dir` |

### MCP mode only

| Endpoint | Method | Description |
|---|---|---|
| `/api/uns/databases` | GET | UNS-aware database discovery |
| `/api/uns/discover` | GET | All UNS policies |
| `/api/uns/policies` | GET | Policies by type (`?type=uns`) |
| `/api/tables` | GET | Tables in a database (`?dbms=`) |
| `/api/columns` | GET | Columns in a table (`?dbms=&table=`) |
| `/api/databases` | GET | All network databases |
| `/api/nodes` | GET | Node list |
| `/api/nodes/monitor` | GET | Node monitor status |
| `/api/worker/status` | GET | MCP worker queue depth |
| `/api/log/stream` | GET | SSE stream of API call events |
| `/api/log/snapshot` | GET | Last 200 events as JSON |

MCP-only endpoints return `404 + tip` in REST mode.

### `/api/query/increment` request body

```json
{
  "dbms":           "mydb",
  "table":          "mytable",
  "timeColumn":     "timestamp",
  "startTime":      "2025-01-01 00:00:00",
  "endTime":        "2025-01-02 00:00:00",
  "intervalLength": 15,
  "timeUnit":       "minute",
  "projections":    ["avg(value)", "min(value)", "max(value)"],
  "where":          "device_id = 'DEV-01'"
}
```

In MCP mode: tries `queryWithIncrement` first, falls back to `increments()` SQL.
In REST mode: builds `increments()` SQL and POSTs directly to AnyLog.

---

## CLI Options

```
python3 anylog_proxy.py [OPTIONS]
```

| Option | Default | Description |
|---|---|---|
| `--anylog-url URL` | `http://127.0.0.1:32349` | AnyLog REST URL or MCP SSE URL — mode auto-detected |
| `--mode rest\|mcp` | auto | Force mode (default: detect from URL) |
| `--html-dir PATH` | none | Directory of HTML dashboards to serve |
| `--port PORT` / `-p` | `8080` | HTTP port |
| `--host HOST` | `0.0.0.0` | Bind interface |
| `--call-delay SECS` | `1.5` | (MCP) Pause between MCP calls |
| `--job-timeout SECS` | `300` | (MCP) Max seconds an HTTP request waits for the worker |
| `--mcp-timeout SECS` | disabled | (MCP) Hard kill timeout per MCP call |
| `--no-verify-ssl` | off | Disable SSL certificate verification |
| `--debug` | off | Enable DEBUG logging |
| `--log-file PATH` | none | Append logs to file |

---

## Debug Panel

Browse to `http://localhost:8080/debug` while dashboards are running.

Shows a live SSE-fed event log of every HTTP request, proxy response, MCP call
(MCP mode), and REST call (REST mode). Supports filtering by event kind and
free-text search.

---

## MCP Architecture (MCP mode only)

```
┌────────────────────────────────────────────────┐
│  Flask  (threaded — one thread per HTTP client)│
│  /api/query  /api/status  /api/uns/databases …  │
│        │                                        │
│        │  _run_job(tool, params)                │
│        │  ├─ cache hit? → return immediately    │
│        │  └─ enqueue Job, wait on job.done      │
│                    │                            │
│           ┌────────▼──────────────┐             │
│           │  _job_queue  (FIFO)   │             │
│           └────────┬──────────────┘             │
│                    │  one job at a time         │
│           ┌────────▼──────────────┐             │
│           │  worker thread        │  daemon     │
│           │  _call_mcp(tool, p)   │             │
│           │  cache_set(key, r)    │             │
│           │  job.done.set()       │             │
│           └────────┬──────────────┘             │
│                    │                            │
│           ┌────────▼──────────────┐             │
│           │  McpSseClient         │             │
│           │  GET /mcp/sse         │             │
│           │  POST <endpoint>      │             │
│           │  read SSE response    │             │
│           └───────────────────────┘             │
└────────────────────────────────────────────────┘
```

Key design decisions:
- **One MCP call at a time** — the single worker thread guarantees the AnyLog
  SSE server never receives concurrent requests
- **Job deduplication** — duplicate concurrent requests for the same
  `(tool, params)` share one in-flight job
- **TTL cache** — metadata cached 300 s, query data 30 s
- **Two-stream SSE** — supports both single-stream and two-stream MCP server
  variants (Timbergrove style: stream-1 closes before response, stream-2
  delivers it)

---

## Troubleshooting

**`Is a directory` error on startup**
Docker auto-created a mounted file as a directory before it existed.
```bash
docker compose down
# Delete and recreate the offending file, then:
docker compose up -d --build
```

**MCP mode: requests time out**
The MCP server is slow or unreachable. Try `--mcp-timeout 60` to fail fast
and `--call-delay 2.0` to give the server more recovery time between calls.
Check `/api/worker/status` to see queue depth.

**REST mode: empty results**
AnyLog may have returned results in a different format. Check the `/debug`
panel to see the raw response. The proxy normalises:
`plain array → {results:[]}` but unusual response shapes may need adjustment.

**Adding dashboards**
Drop `.html` files into `../html/`. No restart needed — the directory is
bind-mounted and served directly.