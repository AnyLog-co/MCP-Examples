# AnyLog MCP Dashboard Generator Prompt

## Parameters — set these before running the prompt

```
DATA_TYPE      = "Power Plant"               # human label used in the UI (e.g. "Oil Rig", "Wind Turbine", "Water Plant")
QUERY_NODE     = "24.5.219.50:32349"         # full URL of the AnyLog query node (e.g. http://24.5.219.50:32349)
DBMS           = "cos"                       # AnyLog database name
TABLE          = "pp_pm"                     # primary table (leave blank to auto-discover)
UNS_NAMESPACE  = "Smart_City"               # UNS root namespace — use the top-level namespace, not a sub-path
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
3. Use `executeQuery` (`mode=post`) to run `SELECT distinct(device_id) FROM {TABLE}` (or equivalent ID column) to discover all device/monitor IDs — the dashboard drill-down will be pre-populated with these.
4. Use `checkStatus` (`mode=post`) to confirm the node is reachable and get the exact response shape for the ping command.
5. If `UNS_NAMESPACE` is set, query `blockchain get uns where namespace = {UNS_NAMESPACE}` (no `bring` clause) — this returns a full JSON object with name, namespace, uns_level, loc, id, date, and ledger fields. Note: use the top-level namespace only (e.g. `Smart_City`, not `Smart_City/Power_Plant`).

Use what you learn to decide which fields to visualise, what KPIs make sense, and what the column names actually are.

---

### Step 2 — Build the dashboard

Generate a **single self-contained HTML file** with the following features. Every REST call must use the exact formats derived from the MCP tools in Step 1.

---

#### Connection layer — two POST body shapes

AnyLog exposes a single REST endpoint (the query node URL). All calls use `POST` with a JSON body. There are two body shapes depending on the command type:

**SQL queries** — must include `destination: "network"` to fan out to operator nodes:
```js
// command format: sql {dbms} format=json:list and stat=false  {sql}
// NOTE: use "format=json:list and stat=false" — this returns a plain JSON array
// and suppresses the row-count metadata object that "format=json" appends.
fetch(CONN.node, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    'User-Agent':  'AnyLog/1.23',
    'command':     `sql ${dbms} format=json:list and stat=false  ${sql}`,
    'destination': 'network'
  })
})
```

**Blockchain / node commands** — no `destination` key (processed locally by the query node):
```js
fetch(CONN.node, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    'User-Agent': 'AnyLog/1.23',
    'command':    'blockchain get uns where namespace = Smart_City'
    //            'get status where format=json'
    //            'get queries where format=json'
    //            'test node'
    //            'test network'
  })
})
```

**nginx mode** — POST to `${nginxUrl}/api/query` using the same body shapes above. nginx handles all TLS/mTLS server-side; no cert headers are needed in the browser:
```js
fetch(`${nginxUrl}/api/query`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ 'User-Agent': 'AnyLog/1.23', 'command': '...' })
})
```

**Flask proxy mode** — POST to `${proxyUrl}/api/query`. The proxy holds the mTLS certs server-side and forwards to the AnyLog node over HTTPS:
```js
fetch(`${proxyUrl}/api/query`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ url: nodeUrl, 'User-Agent': 'AnyLog/1.23', 'command': '...' })
})
```

---

#### Config bar — always-visible connection settings

Place a **config bar** directly below the sticky header (not hidden in a drawer). It contains:

| Field | Default | Notes |
|---|---|---|
| Node URL | `http://{QUERY_NODE}` | Full URL including `http://` or `https://` |
| Database | `{DBMS}` | |
| Table | `{TABLE}` | |
| UNS Namespace | `{UNS_NAMESPACE}` | |
| **Apply & Connect** button | — | Re-initialises all timers and re-fetches everything |

This makes the endpoint immediately reconfigurable without opening any drawer. The `CONN` object is updated on Apply.

For **proxy / nginx** modes, an optional **⚙ settings drawer** (gear icon in the header) provides the additional fields (proxy URL, AnyLog node URL, cert paths for Flask proxy). The drawer is secondary — direct POST is the default and most common mode.

---

#### Connection modes — three modes, direct POST is the default

| Mode | How it works | Config location |
|---|---|---|
| **Direct POST** *(default)* | Browser POSTs directly to the AnyLog query node URL. Works when the node has CORS enabled (`Access-Control-Allow-Origin: *`). | Config bar |
| **Flask Proxy** (`anylog_proxy.py`) | Browser POSTs to `{proxyUrl}/api/query`. The proxy holds mTLS certs server-side. Start with: `python anylog_proxy.py --cert client.crt --key client.key --cacert ca.crt` | Settings drawer |
| **nginx** | Browser POSTs to `{nginxUrl}/api/query`. nginx handles TLS termination and mTLS server-side — no cert details needed in the browser. | Settings drawer |

The active mode (`DIRECT` / `PROXY` / `NGINX`), node address, and connection status must be visible in the header at all times via a mode chip and status dot.

If a direct POST fails with a network or CORS error, display a yellow banner explaining the issue and suggesting the proxy alternative — but do not hide or disable the config bar.

---

#### Query throttle & log — `QUERIES` button in header

All REST calls go through a central dispatcher that:
- **Deduplicates by tag** — if a query with the same tag is already in-flight, the new call is dropped and logged as `skipped`
- **Enforces a max-concurrent limit** (default 3, adjustable in the log panel)
- **Uses one query per refresh cycle** — fetch the full time window in a single SQL call; derive the current snapshot client-side by keeping the newest row per device/monitor (data arrives DESC ordered)

Clicking `QUERIES` opens a slide-up panel with two tabs:
- **Dashboard Queries** — every call made: sequence #, timestamp, tag, status (`pending` / `ok` / `err:…` / `skipped`), duration (ms), rows returned, full command string
- **Active on Node** — calls `get queries where format=json` (no destination) and displays what AnyLog reports as currently running

---

#### Node ping — every 5 minutes

Call `get status where format=json` (no `destination`) every 5 minutes. Show result in the header as:
- 🟢 `RUNNING HH:MM` — node responded with status running
- 🔴 `UNREACHABLE` — call failed or timed out

---

#### UNS panel — Unified Namespace metadata

If `UNS_NAMESPACE` is set, show a UNS panel using:
```
blockchain get uns where namespace = {UNS_NAMESPACE}
```
This returns a JSON array like `[{"uns": {"name": "...", "namespace": "...", "uns_level": "...", "loc": "lat, lng", "id": "...", "date": "...", "ledger": "..."}}]`.

Parse and display all fields as labelled key-value cards (name, namespace, level, location, ledger, date, id). If `loc` is present, show a `📍 VIEW MAP ↗` link to OpenStreetMap using the coordinates. Show the exact command used in the panel toolbar.

> **Note:** This command works in all three modes including direct POST — it is a blockchain command (no `destination`), so it is processed locally by the query node and does not trigger CORS issues beyond the initial connection.

---

#### Drill-Down panel — filter by device/monitor ID and timestamp range

Include a dedicated **Drill-Down** section below the main table. It must:

1. **Auto-populate** a monitor/device dropdown on load using:
   ```sql
   SELECT distinct(monitor_id) FROM {TABLE}
   ```
   (replace `monitor_id` with the actual ID column discovered in Step 1)

2. Provide **FROM / TO datetime pickers** (HTML `datetime-local` inputs) plus quick-select buttons: `1H`, `6H`, `24H`, `7D`

3. Build the SQL `WHERE` clause using AnyLog timestamp syntax:
   ```sql
   SELECT timestamp, realpower, reactivepower, ...
   FROM {TABLE}
   WHERE monitor_id = '{selectedId}'
     AND timestamp >= '{from}'
     AND timestamp <= '{to}'
   ORDER BY timestamp ASC
   ```
   Timestamps must be formatted as `YYYY-MM-DD HH:MM:SS` (replace the `T` from `datetime-local` with a space and append `:00`).

4. Show a **SQL preview bar** — the exact command string that will be sent — updating live as the user changes the monitor or time range.

5. On query, display:
   - **Summary stats**: row count, avg/peak/min of the primary metric, avg power factor or equivalent
   - **Two drill charts** (Chart.js line): primary metric (real + reactive or equivalent) and sub-channel breakdown (phase currents or equivalent) over time
   - **Scrollable raw data table** — most recent 200 rows, newest first

6. Clicking any **device/monitor ID** in the main overview table should jump to the drill-down panel and auto-query for that ID with a 24H default window.

---

#### Dashboard content — derived from Step 1

Use the actual field names and data ranges discovered in Step 1 to build:

- **KPI cards** (4) — the most meaningful aggregate metrics for this data type
- **Per-device/monitor sub-measurement cards** — a 3-column card row for the key sub-measurements (e.g. phase currents A/B/C, blade pitch angles, sensor channels). Controlled by the monitor selector in the header.
- **Bar chart** — latest snapshot values per active device, sorted descending, top 12
- **Line chart** — trend over time for the header-selected device, 1H / 6H / 24H selector
- **Full data table** — all devices/monitors sorted by primary metric descending, with inline mini-bars and an ONLINE / STANDBY status badge. Clicking a row's ID jumps to drill-down.

All charts use Chart.js (from cdnjs). A monitor/device `<select>` in the header controls the line chart and sub-measurement cards simultaneously.

---

#### Design

- Dark industrial theme: `#0a0c0e` background, amber (`#f59e0b`) as the primary accent
- Fonts: `Share Tech Mono` (data/mono values) + `Barlow Condensed` (UI labels) from Google Fonts
- Sticky header with: logo/title, node badge with status dot, mode chip (`DIRECT` / `PROXY` / `NGINX`), ping badge, monitor selector, refresh button, `QUERIES` button, settings (⚙) button
- Config bar always visible below the header
- Auto-refresh every 30 seconds; timer restarts cleanly when config is applied
- If live data fails, fall back to realistic demo data populated from the Step 1 sample rows — never show a broken or empty dashboard. Mark demo data clearly with `(demo)` in the last-updated timestamp.

---

### Step 3 — Output

Deliver a **single `.html` file**. No external dependencies except Chart.js from cdnjs and Google Fonts. All configuration lives in a `const CONN = { ... }` object at the top of the script, pre-filled with the parameters above.

Include a comment block near the top of the `<script>` section documenting:
- The two POST body shapes (SQL vs blockchain/node commands)
- The `format=json:list and stat=false` flag and why it is used instead of plain `format=json`
- The `destination: "network"` requirement for SQL queries
- The three connection modes and how each routes the request