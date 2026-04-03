# AnyLog REST Proxy — Dashboard & Proxy Suite

Browser-based dashboards for [AnyLog](https://anylog.co) distributed edge networks.
Includes sample dashboards, two proxy options, and prompt templates for generating
custom dashboards with Claude + AnyLog MCP.

---

## Repository Layout

```
rest-proxy/
├── README.md                        ← this file
│
├── html/                            ← ready-to-use HTML dashboards
│   ├── dashboard-node-status.html   ← AnyLog node & network health inspector
│   ├── dashboard-power-plant.html   ← Smart City Power Plant monitor
│   └── rig_data.html                ← Oil rig interval monitor (Timbergrove)
│
├── prompts/                         ← LLM prompt templates for generating dashboards
│   ├── node_status.md
│   ├── power_plant.md
│   └── rig_data.md
│
├── proxy-nginx/                     ← nginx Docker proxy (recommended for production)
│   ├── docker-compose.yaml
│   ├── nginx.conf
│   └── README.md
│
└── proxy-generic/                   ← Python Flask proxy (REST + MCP modes)
    ├── anylog_proxy.py
    ├── requirements.txt
    ├── Dockerfile
    ├── docker-compose.yaml
    └── README.md
```

---

## Connection Modes

Browsers cannot POST directly to AnyLog nodes in most environments due to CORS.
Choose one of three connection modes:

| Mode | How it works | Best for |
|---|---|---|
| **Direct POST** | Browser → AnyLog node | Node has CORS enabled, or dev/testing |
| **nginx proxy** | Browser → nginx (Docker) → AnyLog node | Production, shared access, no Python needed |
| **Flask proxy** | Browser → `anylog_proxy.py` → AnyLog node | REST or MCP mode, Docker or local |

**nginx** and the **Flask proxy** are independent alternatives — use one, not both.

### Direct POST (no proxy)

Works when the AnyLog node responds with `Access-Control-Allow-Origin: *`, or when
the browser is launched with `--disable-web-security`.

Open any dashboard HTML file directly in a browser, set Mode → `direct` in the
config bar, and enter the node URL.

### nginx proxy

```bash
cd proxy-nginx
# Set ANYLOG_NODE_URL in docker-compose.yaml, then:
docker compose up -d
```

Open a dashboard, set Mode → `nginx`, nginx URL → `http://localhost`.
See [`proxy-nginx/README.md`](proxy-nginx/README.md) for full setup.

### Flask proxy

```bash
cd proxy-generic

# REST mode (direct pass-through, like nginx but Python)
python3 anylog_proxy.py --anylog-url http://HOST:PORT --html-dir ../html

# MCP mode (auto-detected from /mcp/sse suffix)
python3 anylog_proxy.py --anylog-url http://HOST:PORT/mcp/sse --html-dir ../html

# Or via Docker
docker compose up -d
```

Open a dashboard, set Mode → `proxy`, Proxy URL → `http://localhost:8080`.
See [`proxy-generic/README.md`](proxy-generic/README.md) for full setup.

---

## Dashboards

### `dashboard-node-status.html`

AnyLog node and network health inspector. No database or SQL queries — fires three
diagnostic commands in parallel:

| Command | Response | Panel |
|---|---|---|
| `get status where format=json` | JSON object | Node Status (key-value grid) |
| `test node` | Pipe-delimited text | Node Test (pass/fail table) |
| `test network` | Pipe-delimited text | Network Test (node list with type pills) |

Direct POST only. CORS note shown if blocked.

### `dashboard-power-plant.html`

Smart City Power Plant live monitor (`cos.pp_pm` table). Features:
- KPI cards: real power, reactive power, power factor, active monitors
- Phase current breakdown per monitor (A / B / C)
- Bar chart: real power by monitor
- Line chart: power trend (1H / 6H / 24H)
- Full monitor table with inline bars and ONLINE / STANDBY status
- UNS panel: `blockchain get uns where namespace = Smart_City`
- Drill-down: filter by monitor ID and time range
- Query log panel with call deduplication and max-concurrent throttling
- Node ping every 5 minutes

### `rig_data.html`

Timbergrove oil rig interval monitor (`timbergrove_rigs.rig_data` table). Features:
- Fleet summary for 4 rigs (RIG-TX-001, RIG-TX-007, RIG-ND-012, RIG-GOM-023)
- SVG rig diagram per unit with live sensor annotations
- KPI cards: ROP, WOB, RPM, torque, hookload, bit depth, flow rate, total gas
- Vertical bar charts: standpipe pressure, choke pressure, depth
- Time-series charts: ROP/WOB, RPM/torque, flow rate, gas
- API call log panel

All dashboards support all three connection modes via the in-page config bar.

---

## Generating Custom Dashboards

Use the prompt templates in `prompts/` with Claude (AnyLog MCP connected).

### Quick start

1. Pick the closest prompt template (or use `power_plant.md` as a base)
2. Set the parameters at the top:
   ```
   DATA_TYPE      = "Wind Turbine"
   QUERY_NODE     = "10.0.0.1:32349"
   DBMS           = "my_database"
   TABLE          = "my_table"
   UNS_NAMESPACE  = "MyOrg"
   ```
3. Paste into Claude with the AnyLog MCP server connected
4. Claude discovers the schema via MCP, then generates a single `.html` file
5. Drop the file into `html/` — it works immediately via either proxy

### Prompt templates

| File | Dashboard type | Key features |
|---|---|---|
| `power_plant.md` | Data dashboard | SQL queries, UNS panel, charts, drill-down, 3 connection modes |
| `node_status.md` | Diagnostic tool | Node commands only, pipe-table parser, no SQL |
| `rig_data.md` | Industrial monitor | Multi-rig, SVG diagram, increments() queries |

---

## AnyLog REST API Reference

All calls use `POST` with a JSON body. Two body shapes:

### SQL queries — requires `destination: "network"`

```bash
curl -X POST http://HOST:PORT \
  -H "Content-Type: application/json" \
  -d '{
    "User-Agent":  "AnyLog/1.23",
    "command":     "sql mydb format=json:list and stat=false  SELECT * FROM mytable LIMIT 10",
    "destination": "network"
  }'
```

- `destination: "network"` fans the query out to all operator nodes holding the data
- `format=json:list and stat=false` returns a plain JSON array (suppresses the
  trailing row-count object that plain `format=json` appends)

### Blockchain / node commands — no `destination`

```bash
curl -X POST http://HOST:PORT \
  -H "Content-Type: application/json" \
  -d '{
    "User-Agent": "AnyLog/1.23",
    "command":    "get status where format=json"
  }'
```

Processed locally by the query node. Examples:
- `get status where format=json`
- `test node` / `test network`
- `get queries where format=json`
- `blockchain get uns where namespace = Smart_City`

### Via the Flask proxy

The proxy accepts both the AnyLog REST `{command}` format and a simpler `{dbms, sql}` shape:

```bash
curl -X POST http://localhost:8080/api/query \
  -H "Content-Type: application/json" \
  -d '{"dbms": "mydb", "sql": "SELECT * FROM mytable LIMIT 10"}'
```

Response: `{"results": [...], "row_count": N, "dbms": "mydb"}`

---

## Troubleshooting

**`Failed to fetch` / CORS error (Direct mode)**
Switch Mode → `nginx` or `proxy`, or launch Chrome with
`--disable-web-security --user-data-dir=/tmp/dev`.

**CORS banner persists after switching to nginx/proxy mode**
Click ↻ Refresh — the banner clears on the next successful fetch. If it
persists, the proxy itself can't reach the AnyLog node (check proxy logs).

**`Is a directory` error on proxy startup**
Docker auto-created a config file as a directory before it existed.
Delete it and recreate as a file — see the relevant proxy README.

**`502 Bad Gateway` from nginx**
nginx can't reach the AnyLog node. If AnyLog runs on the same Windows host:
```yaml
# docker-compose.yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```
```nginx
proxy_pass http://host.docker.internal:PORT/;
```

**SQL queries return empty, status works**
Missing `"destination": "network"` in the SQL body. Without it the query runs
only on the query node, which holds no operator data.