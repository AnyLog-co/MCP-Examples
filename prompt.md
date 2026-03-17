# AnyLog MCP Dashboard Generator Prompt

## Parameters — set these before running the prompt

```
DATA_TYPE      = "Power Plant"               # human label used in the UI (e.g. "Oil Rig", "Wind Turbine", "Water Plant")
QUERY_NODE     = "24.5.219.50:32349"         # IP:Port of the AnyLog query node
DBMS           = "cos"                       # AnyLog database name
TABLE          = "pp_pm"                     # primary table (leave blank to auto-discover)
UNS_NAMESPACE  = "Smart_City/Power_Plant"    # UNS root namespace (leave blank if none)
```

---

## Prompt

You are connected to an AnyLog network via MCP. Using the parameters above, build a complete, production-quality 
single-file HTML dashboard for **`{DATA_TYPE}`** data.

---

### Step 1 — Discover the data

Before writing any code, use the MCP tools to understand what is actually in the network:

1. Use `executeQuery` (`mode=post`, `target_node={QUERY_NODE}`) to sample the schema and recent rows from `{DBMS}.{TABLE}`. If `TABLE` is blank, use `listTables` first to discover available tables in `{DBMS}`.
2. Use `getClusterNodeMapping` to find which physical nodes hold the data (primary + backup nodes, cluster IDs, node status).
3. Use `executeQuery` (`mode=post`) to get the exact POST body shape for SQL queries — the dashboard must replicate this exactly.
4. Use `checkStatus` (`mode=post`) to get the POST body shape for the node ping command.
5. If `UNS_NAMESPACE` is set, note that the data location panel will use:
   `blockchain get uns where namespace={UNS_NAMESPACE} bring [*][loc]`

Use what you learn to decide which fields to visualise, what KPIs make sense, and what the column names actually are.

---

### Step 2 — Build the dashboard

Generate a **single self-contained HTML file** with the following features. Every REST call must use the exact formats derived from the MCP tools in Step 1.

#### Connection layer — two POST transports

AnyLog uses two distinct REST patterns. Both use `POST` with a JSON body (GET with custom headers is blocked by browser CORS):

**SQL queries** — include `destination`:
```js
fetch(`http://${queryNode}`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    'User-Agent':  'AnyLog/1.23',
    'command':     `sql ${dbms} format=json ${sql}`,
    'destination': 'network'
  })
})
```

**Blockchain / status commands** — no `destination`:
```js
fetch(`http://${queryNode}`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    'User-Agent': 'AnyLog/1.23',
    'command':    'blockchain get uns where namespace=...'
  })
})
```

**nginx mode** — POST to `${nginxUrl}/api/query`, same JSON body shapes as above. No cert headers — nginx handles all TLS and mTLS server-side:
```js
fetch(`${nginxUrl}/api/query`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ 'User-Agent': 'AnyLog/1.23', 'command': '...' })
})
```

#### Connection modes — configurable via a settings drawer (⚙ button)

Support three modes, selectable at runtime without reloading:

| Mode | How it works | Settings drawer fields |
|---|---|---|
| **Direct POST** | Browser POSTs directly to the AnyLog query node. Works only if the node has CORS enabled or the browser runs with `--disable-web-security`. | Query node URL, database, table |
| **Flask Proxy** (`anylog_proxy.py`) | Browser POSTs to the Flask proxy's `/api/query`. The proxy holds the user's mTLS certs server-side and forwards to the AnyLog node. | Proxy URL, AnyLog node URL, database, table, cert/key/cacert file paths |
| **nginx** | Browser POSTs to the nginx URL. nginx handles TLS termination and mTLS to the AnyLog node server-side — certificates are configured by the admin at deploy time via `setup_nginx.sh`. No cert details are needed in the browser. | nginx URL, database, table |

The active mode, node address, and connection status must be visible in the header at all times.

#### Query throttle & log — `QUERIES` button in header

All REST calls go through a central dispatcher that:
- **Deduplicates by tag** — if a query with the same tag is already in-flight, the new call is dropped and logged as `skipped`
- **Enforces a max-concurrent limit** (default 3, adjustable in the log panel)
- **Uses one query per refresh cycle** — fetch the full time window in a single call; derive the current snapshot client-side by deduplicating on the latest row per device/monitor

Clicking `QUERIES` opens a slide-up panel with two tabs:
- **Dashboard Queries** — every call made: sequence #, timestamp, tag, status (`pending` / `ok` / `err:…` / `skipped`), duration (ms), rows returned, full command string
- **Active on Node** — calls `get queries where format=json` and displays what AnyLog reports as currently running

#### Node ping — every 5 minutes

Call `get status where format=json` (no `destination`) every 5 minutes. Show result in the header as:
- 🟢 `RUNNING HH:MM` — node is responding
- 🔴 `UNREACHABLE` — call failed

#### Data Location panel — UNS-driven

If `UNS_NAMESPACE` is provided, show a Data Location panel using:
```
blockchain get uns where namespace={UNS_NAMESPACE} bring [*][loc]
```
This returns a plain-text coordinate string (e.g. `"39.9049, -95.8034"`), not JSON. Handle it as raw text. Display the coordinates and a `VIEW MAP ↗` link to OpenStreetMap. Show the exact command used in the panel toolbar.

In **direct mode**, this panel cannot execute due to CORS. Display a clear explanation with the equivalent working `curl` command instead of a generic error.

#### Dashboard content — derived from Step 1

Use the actual field names and data ranges discovered in Step 1 to build:

- **KPI cards** (4) — the most meaningful aggregate metrics for this data type
- **Per-device/monitor phase or channel breakdown** — a 3-column card row for the key sub-measurements (e.g. phase currents, blade angles, sensor channels)
- **Bar chart** — latest snapshot values per active device, sorted descending
- **Line chart** — trend over time for a user-selected device, with a 1H / 6H / 24H time range selector
- **Full data table** — all devices/monitors, sortable, with inline mini-bars for the primary metric and an ONLINE / STANDBY status badge

All charts use Chart.js (from cdnjs). A monitor/device selector in the header controls the line chart and breakdown cards simultaneously.

#### Design

- Dark industrial theme: `#0a0c0e` background, amber (`#f59e0b`) as the primary accent
- Fonts: `Share Tech Mono` (data/mono values) + `Barlow Condensed` (UI labels) from Google Fonts
- Sticky header with: logo/title, node badge, mode chip (`DIRECT` / `PROXY` / `NGINX`), ping badge, monitor selector, refresh button, `QUERIES` button, settings button
- Auto-refresh every 30 seconds; timer restarts cleanly when settings change
- If live data fails, fall back to demo data populated from the Step 1 sample — never show a broken/empty dashboard

---

### Step 3 — Output

Deliver a **single `.html` file**. No external dependencies except Chart.js from cdnjs and Google Fonts. All configuration lives in a `const CONN = { ... }` object at the top of the script, pre-filled with the parameters above.

Include a comment block near the top of the script clearly documenting the two POST body shapes used, so the file is self-explanatory as a reference implementation.