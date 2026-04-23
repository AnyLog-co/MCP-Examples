# AnyLog REST Proxy — Dashboard & Proxy Suite

Browser-based dashboards for [AnyLog](https://anylog.co) distributed edge networks.
Includes sample dashboards, two proxy options, and prompt templates for generating
custom dashboards with Claude + AnyLog MCP.

---

## Table of Contents

- [Connection Modes](#connection-modes)
  - [Direct POST](#direct-post-no-proxy)
  - [nginx Proxy](#nginx-proxy-recommended-for-production)
    - [Docker Deployment](proxy-nginx)
  - [Flask Proxy](#flask-proxy)
    - [Docker Deployment](proxy-generic)
  - [Configuring MCP Client](mcp-setup.md)
- [Using Claude + MCP](#using-claude--mcp)
  - [Example 1 — Dashboard Generation](#example-1--generate-a-dashboard-recommended)
  - [Example 2 — Conversational Queries](#example-2--conversational-data-queries)
  - [Example 3 — MCP-backed Live Dashboard ⚠](#example-3--mcp-backed-live-dashboard--experimental)
- [Dashboards](#dashboards)
- [Generating Custom Dashboards](#generating-custom-dashboards)
- [AnyLog REST API Reference](#anylog-rest-api-reference)
- [Troubleshooting](./TROUBLESHOOTING.md)

---

## Repository Layout

```
MCP-Examples/
├── README.md                  ← this file
├── mcp-setup.md               ← how to connect Claude to AnyLog via MCP
├── TROUBLESHOOTING.md         ← all error & debug guidance
│
├── html/                      ← ready-to-use HTML dashboards
│   ├── dashboard-node-status.html
│   ├── dashboard-power-plant.html
│   ├── rig_data.html
│   └── wind_turbine_mcp.html  ← MCP-backed ⚠ experimental
│
├── prompts/                   ← LLM prompt templates for generating dashboards
│   ├── base44.md
│   ├── node_status.md
│   ├── power_plant.md
│   ├── rig_data.md
│   └── wind_turbine_mcp.md    ← Example 3 generation prompt
│
├── proxy-nginx/               ← nginx Docker proxy (recommended for production)
│   ├── docker-compose.yaml
│   ├── nginx.conf
│   └── README.md
│
└── proxy-generic/             ← Python Flask proxy (REST + MCP modes)
    ├── anylog_proxy.py
    ├── requirements.txt
    ├── Dockerfile
    ├── docker-compose.yaml
    └── README.md
```

---

## Connection Modes

Browsers cannot POST directly to AnyLog nodes in most environments due to CORS.

Modern browsers enforce the **Same-Origin Policy**, which prevents a web page from
sending requests to a different domain, port, or protocol unless the target server
explicitly allows it via **Cross-Origin Resource Sharing (CORS)** headers. AnyLog
nodes do not expose CORS headers by default, so direct browser-to-node calls are
blocked.

#### Why `AnyLog-Agent` instead of `User-Agent`

AnyLog identifies requests using a custom `AnyLog-Agent` request header rather than
the standard `User-Agent` header. This is intentional and directly related to CORS:

- **`User-Agent` is browser-controlled.** Browsers treat it as a forbidden header —
  any attempt to set it manually via `fetch()` is silently ignored, and its presence
  in a request can trigger a CORS preflight (`OPTIONS`) that AnyLog nodes are not
  configured to answer.
- **`AnyLog-Agent` is a custom header you own.** Because it is not on the browser's
  reserved list, you can set it freely. The AnyLog node can then explicitly whitelist
  it:
  ```
  Access-Control-Allow-Headers: AnyLog-Agent, Content-Type
  ```
- **Switching to POST + `AnyLog-Agent` makes the CORS contract explicit and
  controllable** on both ends, rather than fighting browser restrictions on reserved
  headers.

When running behind a proxy (nginx or Flask), CORS is handled at the proxy layer and
`AnyLog-Agent` is forwarded to the node transparently — the browser never sees the
cross-origin hop at all.

The solution is to route requests through a backend service (nginx or the Flask proxy)
that runs outside the browser's security model.

| Mode | How it works | Best for |
|---|---|---|
| **Direct POST** | Browser → AnyLog node | CORS enabled on node, or local dev/testing |
| **nginx proxy** | Browser → nginx (Docker) → AnyLog node | Production, shared access, no Python needed |
| **Flask proxy** | Browser → `anylog_proxy.py` → AnyLog node | REST pass-through or MCP bridge |

**nginx** and the **Flask proxy** are independent alternatives — use one, not both.

### Direct POST (no proxy)

Works when the AnyLog node responds with `Access-Control-Allow-Origin: *`, or when
the browser is launched with `--disable-web-security`.

Open any dashboard HTML file in a browser, set Mode → `direct`, enter the node URL.

### nginx proxy (recommended for production)

```bash
cd proxy-nginx
# Edit docker-compose.yaml — set ANYLOG_NODE_URL
docker compose up -d
```

Open a dashboard, set Mode → `nginx`, nginx URL → `http://localhost`.
See [proxy-nginx/README.md](./proxy-nginx/README.md) for full setup.

### Flask proxy

```bash
cd proxy-generic
# Edit docker-compose.yaml — set ANYLOG_NODE_URL and MODE (rest or mcp)
docker compose up -d --build
```

Open a dashboard, set Mode → `proxy`, Proxy URL → `http://localhost:8080`.
See [proxy-generic/README.md](./proxy-generic/README.md) for full setup.

---

## Using Claude + MCP

Claude can connect to AnyLog via the Model Context Protocol (MCP) to discover live
schema, query data conversationally, and generate dashboards. There are three ways
to use this — ordered from most to least recommended.

### Example 1 — Generate a dashboard (recommended)

Claude connects to MCP **once** to discover schema, sample data, and node topology,
then generates a single `.html` file wired to the correct field names, KPIs, and
query patterns. The generated dashboard runs entirely over plain REST — no MCP
required at runtime.

```
 Generation time (once)              Runtime (ongoing)
 ┌───────────────┐                   ┌──────────────────────┐
 │  Claude +     │  MCP discover     │  Browser opens       │
 │  AnyLog MCP   │ ───────────────►  │  dashboard.html      │
 │               │  generates HTML   │                      │
 └───────────────┘                   │  POST /api/query     │
                                     │  → AnyLog node       │
                                     └──────────────────────┘
```

**Use this first.** Zero runtime cost, full dashboard features, works with nginx
or the Flask proxy in REST mode.

→ See [Generating Custom Dashboards](#generating-custom-dashboards) below.

### Example 2 — Conversational data queries

Keep the MCP client connected to ask natural-language questions about live data:

> *"What is the average wind speed across all turbines in the last 6 hours?"*
> *"Which turbine had the highest RPM yesterday afternoon?"*
> *"Are there any anomalies in power output for turbine 3?"*

Claude translates each question into AnyLog SQL via MCP, executes it, and returns
a plain-language answer. Works alongside a generated dashboard — the dashboard shows
the overview, MCP chat handles ad-hoc investigation.

→ See [mcp-setup.md](./mcp-setup.md) for how to connect Claude to AnyLog.

### Example 3 — MCP-backed live dashboard ⚠ experimental

A dashboard that routes **every data fetch** through the MCP proxy at runtime.

```
Browser → POST /api/query → anylog_proxy.py (MCP mode) → MCP/SSE → AnyLog
```

**Try Examples 1 and 2 first.** Example 3 has real costs and constraints:

| Concern | Detail |
|---|---|
| **Cost** | Every dashboard refresh triggers LLM-mediated MCP calls — billable on every poll cycle |
| **Latency** | MCP calls are serialized; polling every few minutes across multiple sensors will queue |
| **Proxy required** | Requires `anylog_proxy.py` in MCP mode — nginx alone cannot do this |
| **Query discipline** | All SQL must be bounded (`LIMIT`, narrow time windows) — never `SELECT *` |

**When Example 3 makes sense:**
- Prototyping or demos where cost and latency are not a concern
- Low-frequency dashboards (refresh ≥ 5 minutes)
- Deployments where the MCP endpoint is the only available access path
- You want a built-in 🔍 Query log, ⚠ Error log (with `curl` reproduction), and 📋 Prompt evolution log

→ See [html/wind_turbine_mcp.html](./html/wind_turbine_mcp.html) for a working example.
→ See [prompts/wind_turbine_mcp.md](./prompts/wind_turbine_mcp.md) to generate your own.

---

## Dashboards

| File | Description | Connection at runtime |
|---|---|---|
| [dashboard-node-status.html](./html/dashboard-node-status.html) | Node & network health inspector — no SQL | Direct |
| [dashboard-power-plant.html](./html/dashboard-power-plant.html) | Smart City Power Plant monitor | Direct / nginx / proxy |
| [rig_data.html](./html/rig_data.html) | Timbergrove oil rig interval monitor | Direct / nginx / proxy |
| [wind_turbine_mcp.html](./html/wind_turbine_mcp.html) ⚠ | Wind turbine live dashboard (Example 3) | Flask proxy in MCP mode |

### `dashboard-node-status.html`

AnyLog node and network health inspector. No SQL — fires three diagnostic commands:

| Command | Panel |
|---|---|
| `get status where format=json` | Node status key-value grid |
| `test node` | Node test pass/fail table |
| `test network` | Network node list with type pills |

### `dashboard-power-plant.html`

Smart City Power Plant live monitor (`cos.pp_pm`). KPI cards (real power, reactive
power, power factor, active monitors), phase current breakdown per monitor, bar chart
by monitor, line chart (1H / 6H / 24H), full monitor table, UNS panel, drill-down
by monitor and time range, query log.

### `rig_data.html`

Timbergrove oil rig interval monitor (`timbergrove_rigs.rig_data`). Fleet summary
for 4 rigs, SVG rig diagrams with live sensor annotations, KPI cards (ROP, WOB,
RPM, torque, hookload, bit depth, flow rate, gas), bar and time-series charts, API
call log.

### `wind_turbine_mcp.html` ⚠ experimental

Wind turbine live dashboard (`wind_turbine` dbms, turbines 1/2/3/5). Runs in
**Example 3 MCP mode** — every fetch goes through `anylog_proxy.py`. Fleet overview
KPIs (last 1 hour), per-turbine metric cards with mini power trend chart, 🔍 Query
log, ⚠ Error log with `curl` reproduction, 📋 Prompt evolution log, configurable
refresh (5 min / 10 min / demo modes).

---

## Generating Custom Dashboards

Use the prompt templates in [prompts/](./prompts) with Claude (AnyLog MCP connected)
to generate dashboards tailored to your data.

### Quick start

1. Connect Claude to your AnyLog node — see [mcp-setup.md](./mcp-setup.md)
2. Pick the closest prompt template from [prompts/](./prompts)
3. Fill in the parameters at the top:
   ```
   DATA_TYPE      = "Wind Turbine"
   QUERY_NODE     = "10.0.0.1:32349"
   DBMS           = "wind_turbine"
   TABLE          = "power_output"
   UNS_NAMESPACE  = "wind"
   ```
4. Paste the prompt into Claude — it discovers the live schema via MCP, then
   generates a single `.html` file
5. Drop the file into [html/](./html) and open it via either proxy

### Prompt templates

| File | Generates | Runtime connection |
|---|---|---|
| [power_plant.md](./prompts/power_plant.md) | Data dashboard — charts, UNS panel, drill-down | Direct / nginx / proxy |
| [node_status.md](./prompts/node_status.md) | Node diagnostic tool — no SQL | Direct |
| [rig_data.md](./prompts/rig_data.md) | Industrial multi-unit monitor — increments() queries | Direct / nginx / proxy |
| [wind_turbine_mcp.md](./prompts/wind_turbine_mcp.md) | MCP-backed live dashboard (Example 3) | Flask proxy in MCP mode |
| [base44.md](./prompts/base44.md) | Hosted Base44 app (backend + frontend prompts) | Base44 backend → AnyLog REST |

---

## AnyLog REST API Reference

All calls use `POST` with a JSON body and two required headers.

### Headers

| Header | Value | Purpose |
|---|---|---|
| `Content-Type` | `application/json` | Required for all POST requests |
| `AnyLog-Agent` | `AnyLog/1.23` | Identifies the request to AnyLog; replaces `User-Agent` (see [Connection Modes](#connection-modes) for why) |

> **Why not `User-Agent`?** Browsers treat `User-Agent` as a reserved header —
> setting it via `fetch()` is silently ignored and can trigger CORS preflight.
> `AnyLog-Agent` is a custom header that both sides control explicitly.

### Two body shapes

#### SQL queries — requires `destination: "network"`

```bash
curl -X POST http://HOST:PORT \
  -H "Content-Type: application/json" \
  -H "AnyLog-Agent: AnyLog/1.23" \
  -d '{
    "command":     "sql mydb format=json:list and stat=false SELECT * FROM mytable LIMIT 10",
    "destination": "network"
  }'
```

`destination: "network"` fans the query out to all operator nodes holding the data.
`format=json:list and stat=false` returns a plain JSON array without a trailing
row-count object.

#### Blockchain / node commands — no `destination`

```bash
curl -X POST http://HOST:PORT \
  -H "Content-Type: application/json" \
  -H "AnyLog-Agent: AnyLog/1.23" \
  -d '{
    "command": "get status where format=json"
  }'
```

Examples: `get status where format=json` · `test node` · `test network` ·
`blockchain get uns where namespace = Smart_City`

### Via the Flask proxy (simple format)

The proxy handles headers and `destination` automatically — no `AnyLog-Agent` needed
from the browser:

```bash
curl -X POST http://localhost:8080/api/query \
  -H "Content-Type: application/json" \
  -d '{"dbms": "mydb", "sql": "SELECT * FROM mytable LIMIT 10"}'
```

Response: `{"results": [...], "row_count": N, "dbms": "mydb"}`