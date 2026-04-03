# AnyLog REST Proxy — Dashboard & Proxy Suite

Browser-based dashboards for [AnyLog](https://anylog.co) distributed edge networks.
Includes sample dashboards, two proxy options, and prompt templates for generating
custom dashboards with Claude + AnyLog MCP.

---

## Repository Layout

MCP-Examples/
├── README.md                        ← this file
├── [mcp-setup.md](./mcp-setup.md)                     ← how to connect Claude to AnyLog via MCP
├── [TROUBLESHOOTING.md](./TROUBLESHOOTING.md)               ← all error & debug guidance
│
├── [html/](./html)                            ← ready-to-use HTML dashboards
│   ├── dashboard-node-status.html   ← AnyLog node & network health inspector
│   ├── dashboard-power-plant.html   ← Smart City Power Plant monitor
│   ├── rig_data.html                ← Oil rig interval monitor (Timbergrove)
│   └── wind_turbine_mcp.html        ← Wind turbine live dashboard (MCP-backed ⚠ experimental)
│
├── [prompts/](./prompts)                         ← LLM prompt templates for generating dashboards
│   ├── node_status.md
│   ├── power_plant.md
│   ├── rig_data.md
│   └── wind_turbine_mcp.md          ← Example 3 generation prompt
│
├── [proxy-nginx/](./proxy-nginx)                     ← nginx Docker proxy (recommended for production)
│   ├── docker-compose.yaml
│   ├── nginx.conf
│   └── README.md
│
└── [proxy-generic/](./proxy-generic)                   ← Python Flask proxy (REST + MCP modes)
    ├── anylog_proxy.py
    ├── requirements.txt
    ├── Dockerfile
    ├── docker-compose.yaml
    └── README.md

---

## Connection Modes

Browsers cannot POST directly to AnyLog nodes in most environments due to CORS.

Modern browsers enforce a security mechanism called the **Same-Origin Policy**. This policy prevents a web page from sending
requests (such as POST requests) to a server on a different domain, port, or protocol unless the target server explicitly
allows it through **Cross-Origin Resource Sharing (CORS)** headers.

AnyLog nodes do **not expose CORS headers by default**. As a result, when a browser-based application attempts to send a
POST request directly to an AnyLog node, the browser blocks the request before it reaches the node.

To interact with an AnyLog node from a web application, requests should be routed through a backend service (such as a
proxy or API server). This backend runs outside the browser's security model and can communicate with the AnyLog node
directly without being restricted by CORS.

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
See [`proxy-nginx/README.md`](proxy-nginx/README.md) for full setup.

### Flask proxy

```bash
cd proxy-generic 

# Edit docker-compose.yaml - set ANYLOG_NODE_URL and MODE=mcp (if using MCP otherwise REST) 

docker compose up -d --build
```

Open a dashboard, set Mode → `proxy`, Proxy URL → `http://localhost:8080`.
See [`proxy-generic/README.md`](proxy-generic/README.md) for full setup.

---

## Using Claude + MCP

Claude can connect to AnyLog via the Model Context Protocol (MCP) to discover live
schema, query data conversationally, and generate dashboards. There are three ways
to use this — ordered from most to least recommended:

### Example 1 — Generate a dashboard (recommended)

Claude connects to MCP **once** to discover schema, sample data, and node topology,
then generates a single `.html` file wired to the correct fields and query patterns.
The generated dashboard runs entirely over plain REST — no MCP required at runtime.

**Use this first.** It's the fastest path to a production-ready dashboard and costs
nothing at runtime.

→ See [Generating Custom Dashboards](#generating-custom-dashboards) below.

### Example 2 — Conversational data queries

Keep the MCP client connected to ask natural-language questions about live data:

> *"What is the average wind speed across all turbines in the last 6 hours?"*
> *"Which turbine had the highest RPM yesterday afternoon?"*
> *"Are there any anomalies in power output for turbine 3?"*

Claude translates each question into AnyLog SQL via MCP, executes it, and returns
a plain-language answer. Works alongside a generated dashboard — the dashboard shows
the overview, MCP chat handles ad-hoc investigation.

→ See [`mcp-setup.md`](mcp-setup.md) for how to connect Claude Desktop to AnyLog.

### Example 3 — MCP-backed live dashboard ⚠ experimental

A dashboard that routes **every data fetch** through the MCP proxy at runtime —
Claude intermediates each query rather than the browser calling AnyLog directly.

```
Browser → POST /api/query → anylog_proxy.py (MCP mode) → MCP/SSE → AnyLog
```

**Try Examples 1 and 2 first.** Example 3 has real trade-offs:

| Concern | Detail |
|---|---|
| **Cost** | Every dashboard refresh triggers LLM-mediated MCP calls — billable API usage on every poll cycle |
| **Latency** | MCP calls are serialized; a dashboard polling every few minutes across multiple sensors will queue |
| **Proxy required** | Requires `anylog_proxy.py` in MCP mode — nginx alone cannot do this |

**When Example 3 makes sense:**
- Low-frequency dashboards (≥ 5 minute refresh)
- Prototyping or demos where cost and latency are not constraints
- Deployments where the MCP endpoint is the only available access path
- You want the prompt/query log to show exactly what SQL was issued each cycle

→ See [`html/wind_turbine_mcp.html`](html/wind_turbine_mcp.html) for a working example.
→ See [`prompts/wind_turbine_mcp.md`](prompts/wind_turbine_mcp.md) to generate your own.

---

## Dashboards

| File | Dashboard type | Connection at runtime |
|---|---|---|
| `power_plant.md` | Data dashboard with charts, UNS panel, drill-down | Direct / nginx / proxy |
| `node_status.md` | Node diagnostic tool, no SQL | Direct only |
| `rig_data.md` | Industrial multi-unit monitor, increments() queries | Direct / nginx / proxy |
| `wind_turbine_mcp.md` | MCP-backed live dashboard (Example 3) | Flask proxy in MCP mode |


### `dashboard-node-status.html`

AnyLog node and network health inspector. No SQL — fires three diagnostic commands:

| Command | Panel |
|---|---|
| `get status where format=json` | Node status key-value grid |
| `test node` | Node test pass/fail table |
| `test network` | Network node list with type pills |

### `dashboard-power-plant.html`

Smart City Power Plant live monitor (`cos.pp_pm`). Features: KPI cards (real power,
reactive power, power factor, active monitors), phase current breakdown per monitor,
bar chart by monitor, line chart (1H / 6H / 24H), full monitor table, UNS panel,
drill-down by monitor and time range, query log.

### `rig_data.html`

Timbergrove oil rig interval monitor (`timbergrove_rigs.rig_data`). Features: fleet
summary for 4 rigs, SVG rig diagrams with live sensor annotations, KPI cards (ROP,
WOB, RPM, torque, hookload, bit depth, flow rate, gas), bar charts and time-series
charts, API call log.

### `wind_turbine_mcp.html` ⚠ experimental

Wind turbine live dashboard (`wind_turbine` dbms, turbines 1 / 2 / 3 / 5). Runs in
**Example 3 MCP mode** — every fetch goes through `anylog_proxy.py`. Features:
fleet overview KPIs (last 1 hour), per-turbine metric cards with mini power trend
chart, 🔍 Query log, ⚠ Error log with `curl` reproduction commands, 📋 Prompt
evolution log, configurable refresh (5 min / 10 min / demo modes).

All three standard dashboards support Direct / nginx / proxy connection modes via
the in-page config bar. The wind turbine dashboard requires the Flask proxy in MCP mode.

---

## Generating Custom Dashboards

Use the prompt templates in [prompts/](./promts) with Claude (AnyLog MCP connected) to generate
dashboards tailored to your data.

### Quick start

1. Connect Claude Desktop to your AnyLog node — see [`mcp-setup.md`](mcp-setup.md)
2. Pick the closest prompt template from `prompts/`
3. Fill in the parameters at the top:
   ```
   DATA_TYPE      = "Wind Turbine"
   QUERY_NODE     = "10.0.0.1:32349"
   DBMS           = "wind_turbine"
   TABLE          = "power_output"
   UNS_NAMESPACE  = "wind"
   ```
4. Paste the prompt into Claude — it discovers the live schema via MCP, then generates
   a single `.html` file
5. Drop the file into `html/` and open it via either proxy

### Prompt templates

> ## Parameters
> set these before running the prompt
>  ```
>  DATA_TYPE      = "Wind Turbine"
>  QUERY_NODE     = "10.0.0.1:32349"
>  DBMS           = "wind_turbine"
>  TABLE          = "power_output"
>  UNS_NAMESPACE  = "wind"
>  ```
>   
> ## Prompt 
> You are connected to an AnyLog network via MCP. Using the parameters above, build a complete, production-quality
single-file HTML dashboard for **`{DATA_TYPE}`** data. 
> 
> ## Functions to utilize
> The following MCP functions are to be used to help prepare the backend (queries) for generating the dashboard.
> However, the dashboard itself should use standard AnyLog queries requests via REST POST to retrieve the data.   
> 
> * Use `listPolicies` function - dynamically discover UNS every as they get added (can be done once an hour)
> * Use `executeQuery` (`mode=post`, `target_node={QUERY_NODE}`) to sample the schema and recent rows from `{DBMS}.{TABLE}`. If `TABLE` is blank, use `listTables` first to discover available tables in `{DBMS}`
> * Use `getClusterNodeMapping` to find which physical nodes hold the data (primary + backup nodes, cluster IDs, node status).
> * Use `executeQuery` (`mode=post`) to run `SELECT distinct(monitor_id) FROM {TABLE}` (or equivalent ID column) to discover all device/monitor IDs — the dashboard drill-down will be pre-populated with these. 
> * Use `checkStatus` (`mode=post`) to confirm the node is reachable and get the exact response shape for the ping command. 
> * If `UNS_NAMESPACE` is set, query `blockchain get uns where namespace = {UNS_NAMESPACE}` (no `bring` clause) — this returns a full JSON object with name, namespace, uns_level, loc, id, date, and ledger fields. Note: use the top-level namespace only (e.g. `Smart_City`, not `Smart_City/Power_Plant`). 
>  
> Use what you learn to decide which fields to visualise, what KPIs make sense, and what the column names actually are.
> 
> ## Backend logic 
> Utilize a direct REST connection to communicate with AnyLog
> 
> ```js
> fetch(nodeBase(), {
> method: 'POST',
> headers: { 'Content-Type': 'application/json' },
> body: JSON.stringify({
>   'User-Agent':  'AnyLog/1.23',
>   'command':     `sql ${dbms} format=json:list and stat=false  ${sql}`,
>   'destination': 'network'
> })
> })
> ```
> 
> #### Frontend Design
> Match the existing dashboard exactly:
> - **Background**: `#0a0c10`, subtle teal grid overlay (`rgba(0,212,170,0.025)` lines, 40px spacing)
> - **Surface**: `#111318` cards, `#181c24` secondary
> - **Accent**: `#00d4aa` (teal) as primary, `#0088ff` (blue) secondary, `#a78bfa` (purple) tertiary
> - **Font**: system monospace stack (`Cascadia Code`, `Fira Code`, `Consolas`, `Menlo`) throughout — **no Google Fonts**
> - **Logo**: `AL` square with teal→blue gradient, `box-shadow: 0 0 24px rgba(0,212,170,0.25)`
> - **Cards**: `border-radius: 12px`, `border: 1px solid #1e2330`
> - **Buttons**: teal gradient primary (`#00d4aa → #00b894`, black text), `border-radius: 7px`
> - **Status dots**: animated blink for ok, solid for error, pulsing for loading
> - **Section icons**: small SVG icons in colour-tinted squares matching the accent for that panel


 
---

## Troubleshooting

See [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) for:
- CORS errors and browser workarounds
- nginx `502 Bad Gateway` on Windows hosts
- Flask proxy Docker issues
- MCP connection and timeout errors
- Empty SQL results despite a working status call