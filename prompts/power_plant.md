# AnyLog Dashboard Generator Prompt

## Parameters — set these before running the prompt

```
DATA_TYPE      = "Power Plant"               # human label used in the UI
QUERY_NODE     = "24.5.219.50:32349"         # AnyLog query node  host:port (no http://)
DBMS           = "cos"                       # AnyLog database name
TABLE          = "pp_pm"                     # primary table (leave blank to auto-discover)
UNS_NAMESPACE  = "Smart_City"               # UNS root namespace — top-level only, not a sub-path
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
3. Use `executeQuery` (`mode=post`) to run `SELECT distinct(monitor_id) FROM {TABLE}` (or equivalent ID column) to discover all device/monitor IDs — the dashboard drill-down will be pre-populated with these.
4. Use `checkStatus` (`mode=post`) to confirm the node is reachable and get the exact response shape for the ping command.
5. If `UNS_NAMESPACE` is set, query `blockchain get uns where namespace = {UNS_NAMESPACE}` (no `bring` clause) — this returns a full JSON object with name, namespace, uns_level, loc, id, date, and ledger fields. Note: use the top-level namespace only (e.g. `Smart_City`, not `Smart_City/Power_Plant`).

Use what you learn to decide which fields to visualise, what KPIs make sense, and what the column names actually are.

---

### Step 2 — Build the dashboard

Generate a **single self-contained HTML file** with the following features. Every REST call must use the exact formats derived from the MCP tools in Step 1.

---

#### Connection layer — `anylogFetch()` dispatcher

All REST calls go through a single central dispatcher that routes based on the active mode.
Place this at the top of the `<script>` section:

```js
// ═══════════════════════════════════════════════════════════════
//  ANYLOG CONNECTION
// ═══════════════════════════════════════════════════════════════
//
//  Three modes — selected in the config bar:
//
//  DIRECT  Browser POSTs directly to the AnyLog query node.
//          Works when the node has CORS enabled (Access-Control-Allow-Origin: *).
//          Body: AnyLog REST format (see below).
//
//  NGINX   Browser POSTs to {nginxUrl}/api/query.
//          nginx handles TLS termination and proxies to the AnyLog node.
//          No cert details needed in the browser.
//          Body: same AnyLog REST format — nginx passes it through unchanged.
//
//  PROXY   Browser POSTs to {proxyUrl}/api/query.
//          anylog_proxy.py (REST mode) or anylog_proxy.py (MCP mode) handles routing.
//          Accepts two body shapes — auto-detected by the proxy:
//            {command: "sql {dbms} format=json:list and stat=false {sql}", ...}  ← AnyLog REST format
//            {dbms: "...", sql: "SELECT ..."}                                     ← simple format
//          Both return: {"results": [...], "row_count": N, "dbms": "..."}
//
//  POST body shapes (AnyLog REST format — used by DIRECT and NGINX modes)
//  -----------------------------------------------------------------------
//  SQL queries — must include destination:"network" to fan out to operator nodes:
//    {
//      "User-Agent":  "AnyLog/1.23",
//      "command":     "sql {dbms} format=json:list and stat=false  {sql}",
//      "destination": "network"
//    }
//    NOTE: "format=json:list and stat=false" returns a plain JSON array and
//    suppresses the trailing row-count metadata object that plain "format=json" appends.
//
//  Blockchain / node commands — no destination (processed locally by the query node):
//    {
//      "User-Agent": "AnyLog/1.23",
//      "command":    "blockchain get uns where namespace = Smart_City"
//      //            "get status where format=json"
//      //            "get queries where format=json"
//    }
//
//  Response normalisation
//  ----------------------
//  DIRECT / NGINX: AnyLog returns a plain JSON array for SQL queries.
//  PROXY:          Returns {"results": [...], "row_count": N}.
//  Always normalise with:
//    const rows = Array.isArray(data) ? data : (data.results || data.Query || []);
// ═══════════════════════════════════════════════════════════════

const CONN = {
  node:      'http://{QUERY_NODE}',   // used in DIRECT mode
  dbms:      '{DBMS}',
  table:     '{TABLE}',
  namespace: '{UNS_NAMESPACE}',
  mode:      'direct',                // 'direct' | 'nginx' | 'proxy'
  nginxUrl:  'http://localhost',      // used in NGINX mode  (no trailing slash)
  proxyUrl:  'http://localhost:8080', // used in PROXY mode  (no trailing slash)
};

function getEndpoint() {
  if (CONN.mode === 'nginx') return `${CONN.nginxUrl}/api/query`;
  if (CONN.mode === 'proxy') return `${CONN.proxyUrl}/api/query`;
  return CONN.node;
}

function buildSqlBody(sql) {
  const cmd = `sql ${CONN.dbms} format=json:list and stat=false  ${sql}`;
  if (CONN.mode === 'proxy') {
    // Proxy accepts both formats; use simple {dbms, sql} for clarity
    return { dbms: CONN.dbms, sql };
  }
  // DIRECT and NGINX: AnyLog REST format
  return { 'User-Agent': 'AnyLog/1.23', command: cmd, destination: 'network' };
}

function buildNodeBody(command) {
  // Blockchain / node commands — same body shape in all modes
  // In PROXY mode the proxy forwards these as-is via its /api/query endpoint
  return { 'User-Agent': 'AnyLog/1.23', command };
}

async function anylogFetch(body, timeoutMs = 50000) {
  const url = getEndpoint();
  const resp = await fetch(url, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify(body),
    signal:  AbortSignal.timeout(timeoutMs),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const data = await resp.json();
  // Normalise: plain array (DIRECT/NGINX) or {results:[]} (PROXY)
  return Array.isArray(data) ? data : (data.results || data.Query || data);
}
```

---

#### Config bar — always-visible, directly below the sticky header

Place a **config bar** directly below the sticky header. It must be always visible (not hidden in a drawer). It contains:

| Field | Default | Notes |
|---|---|---|
| Node URL | `http://{QUERY_NODE}` | Used in DIRECT mode |
| Database | `{DBMS}` | |
| Table | `{TABLE}` | |
| UNS Namespace | `{UNS_NAMESPACE}` | |
| Mode | `direct` | Dropdown: `direct` / `nginx` / `proxy` |
| Proxy / nginx URL | `http://localhost:8080` | Shown only when mode ≠ direct |
| **Apply & Connect** button | — | Updates `CONN`, restarts all timers, re-fetches everything |

Switching mode updates the mode chip in the header instantly (before Apply).
The proxy URL field appears/disappears based on the selected mode.

```js
function applyConfig() {
  CONN.node      = document.getElementById('cfg-node').value.trim().replace(/\/$/, '');
  CONN.dbms      = document.getElementById('cfg-dbms').value.trim();
  CONN.table     = document.getElementById('cfg-table').value.trim();
  CONN.namespace = document.getElementById('cfg-uns').value.trim();
  CONN.mode      = document.getElementById('cfg-mode').value;
  const proxyVal = document.getElementById('cfg-proxy').value.trim().replace(/\/$/, '');
  if (CONN.mode === 'nginx') CONN.nginxUrl = proxyVal;
  if (CONN.mode === 'proxy') CONN.proxyUrl = proxyVal;
  updateModeChip();
  restartPolling();
  fetchData();
}

function onModeChange() {
  const mode  = document.getElementById('cfg-mode').value;
  const row   = document.getElementById('proxy-url-row');
  const label = document.getElementById('proxy-url-label');
  row.style.display = mode === 'direct' ? 'none' : 'flex';
  label.textContent = mode === 'nginx' ? 'nginx URL' : 'Proxy URL';
  updateModeChip();
}
```

---

#### Header — sticky, always visible

The sticky header must show:

- **Logo / title** — icon + data type name
- **Node badge** — `● host:port` with a green/red status dot (updates on each fetch)
- **Mode chip** — `DIRECT` / `NGINX` / `PROXY` label, colour-coded:
  - DIRECT → cyan border
  - NGINX  → green border
  - PROXY  → amber border
- **Ping badge** — `🟢 RUNNING HH:MM` or `🔴 UNREACHABLE` (updated every 5 min)
- **Monitor selector** — `<select>` for per-device drill-down; auto-populated on load
- **↻ REFRESH** button
- **QUERIES** button — opens the query log panel; badge shows active call count

```js
function updateModeChip() {
  const chip  = document.getElementById('modeChip');
  const mode  = CONN.mode.toUpperCase();
  chip.textContent = mode;
  chip.className   = 'mode-chip mode-' + CONN.mode;  // CSS handles colour
}
```

---

#### Connection modes — CORS error handling

If a direct POST fails with a network or CORS error, display a yellow banner:

> ⚠ **Network / CORS Error** — The browser could not reach the AnyLog node directly.
> Switch **Mode → nginx** and set the nginx URL, or **Mode → proxy** and start `anylog_proxy.py`.

Do **not** hide the config bar. Keep it visible so the user can switch modes without reloading.

**Starting the proxy** (for reference, shown in dashboard tooltip or `README`):
```bash
# REST mode (direct pass-through, like nginx but Python)
python3 anylog_proxy.py --anylog-url http://{QUERY_NODE}

# MCP mode (auto-detected from /mcp/sse suffix)
python3 anylog_proxy.py --anylog-url http://{QUERY_NODE}/mcp/sse

# With HTML dashboards served from the proxy itself
python3 anylog_proxy.py --anylog-url http://{QUERY_NODE} --html-dir ./html
```

---

#### Query throttle & log — `QUERIES` button in header

All REST calls go through `anylogFetch()` wrapped by a central dispatcher that:
- **Deduplicates by tag** — if a query with the same tag is already in-flight, the new call is dropped and logged as `skipped`
- **Enforces a max-concurrent limit** (default 3)
- **Uses one query per refresh cycle** — fetch the full time window in a single SQL call; derive the current snapshot client-side by keeping the newest row per device/monitor

Clicking `QUERIES` opens a slide-up panel with two tabs:
- **Dashboard Queries** — every call: sequence #, timestamp, tag, status (`pending` / `ok` / `err:…` / `skipped`), duration (ms), rows returned, full command string
- **Active on Node** — calls `get queries where format=json` (no destination) and displays what AnyLog reports as currently running

---

#### Node ping — every 5 minutes

Call `get status where format=json` (no `destination`) every 5 minutes via `anylogFetch(buildNodeBody('get status where format=json'))`.
Show result in the header ping badge as:
- 🟢 `RUNNING HH:MM` — node responded
- 🔴 `UNREACHABLE` — call failed or timed out

---

#### UNS panel — Unified Namespace metadata

If `UNS_NAMESPACE` is set, show a UNS panel:

```js
const rows = await anylogFetch(
  buildNodeBody(`blockchain get uns where namespace = ${CONN.namespace}`)
);
```

This returns a JSON array like `[{"uns": {"name":"...","namespace":"...","uns_level":"...","loc":"lat, lng","id":"...","date":"...","ledger":"..."}}]`.

Display all fields as labelled key-value cards (name, namespace, level, location, ledger, date, id).
If `loc` is present, show a `📍 VIEW MAP ↗` link to OpenStreetMap using the coordinates.
Show the exact command used in the panel toolbar.

> **Note:** Blockchain commands work in all three modes — they are processed locally by the query node and do not require `destination: "network"`.

---

#### Drill-Down panel — filter by device/monitor ID and timestamp range

Include a dedicated **Drill-Down** section below the main table. It must:

1. **Auto-populate** a monitor/device dropdown on load:
   ```js
   const rows = await anylogFetch(
     buildSqlBody(`SELECT distinct(monitor_id) FROM ${CONN.table}`)
   );
   ```
   Replace `monitor_id` with the actual ID column discovered in Step 1.

2. Provide **FROM / TO datetime pickers** (`datetime-local` inputs) plus quick-select buttons: `1H`, `6H`, `24H`, `7D`

3. Build the SQL `WHERE` clause using AnyLog timestamp syntax:
   ```sql
   SELECT timestamp, realpower, reactivepower, ...
   FROM {TABLE}
   WHERE monitor_id = '{selectedId}'
     AND timestamp >= '{from}'
     AND timestamp <= '{to}'
   ORDER BY timestamp ASC
   ```
   Format timestamps as `YYYY-MM-DD HH:MM:SS` (replace `T` from `datetime-local` with a space).

4. Show a **SQL preview bar** — the exact command string that will be sent — updating live as the user changes the monitor or time range.

5. On query, display:
   - **Summary stats**: row count, avg/peak/min of the primary metric, avg power factor or equivalent
   - **Two drill charts** (Chart.js line): primary metric (real + reactive or equivalent) and phase breakdown over time
   - **Scrollable raw data table** — most recent 200 rows, newest first

6. Clicking any **device/monitor ID** in the main overview table jumps to the drill-down panel and auto-queries for that ID with a 24H default window.

---

#### Dashboard content — derived from Step 1

Use the actual field names and data ranges discovered in Step 1 to build:

- **KPI cards** (4) — the most meaningful aggregate metrics:
  - Total Real Power (kW)
  - Total Reactive Power (kVAR)
  - Avg Power Factor (%)
  - Active Monitors / Total

- **Phase current cards** (3-column row) — Phase A / B / C current in Amperes for the selected monitor

- **Bar chart** — latest real power per active monitor, sorted descending, top 12

- **Line chart** — real power + reactive power trend over time for the selected monitor; 1H / 6H / 24H selector

- **Full data table** — all monitors sorted by real power descending, with:
  - Inline mini-bar (real power relative to max)
  - `ONLINE` / `STANDBY` status badge
  - Clickable monitor ID → jumps to drill-down

All charts use Chart.js (from cdnjs).

---

#### Design

Match the existing dashboard exactly:
- Dark industrial theme: `#0a0c0e` background, `#111418` surface, `#f59e0b` amber accent
- Fonts: `Share Tech Mono` (data/mono values) + `Barlow Condensed` (UI labels) from Google Fonts
- Sticky header with logo, node badge, **mode chip**, ping badge, monitor selector, refresh, QUERIES button
- Config bar always visible below the header — includes mode selector and conditional proxy URL field
- Auto-refresh every 30 seconds; timer restarts cleanly on Apply
- If live data fails, fall back to realistic demo data populated from the Step 1 sample rows — never show a broken or empty dashboard. Mark demo data with `(demo)` in the last-updated timestamp.

---

### Step 3 — Output

Deliver a **single `.html` file**. No external dependencies except Chart.js from cdnjs and Google Fonts.
All configuration lives in the `const CONN = { ... }` object at the top of the `<script>` section,
pre-filled with the parameters above.

Include a comment block near the top of the `<script>` section documenting:
- The `anylogFetch()` dispatcher and the three connection modes
- The two POST body shapes (SQL via `buildSqlBody()` vs blockchain/node via `buildNodeBody()`)
- The `format=json:list and stat=false` flag and why it is used
- The `destination: "network"` requirement for SQL queries
- How to start `anylog_proxy.py` for each mode
- Response normalisation: `Array.isArray(data) ? data : (data.results || data.Query || [])`