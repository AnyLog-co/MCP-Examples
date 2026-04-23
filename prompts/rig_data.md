# Timbergrove Interval Monitor — Dashboard Fix Prompt

## Parameters — set these before running the prompt

```
DATA_TYPE      = "Rig Data"
QUERY_NODE     = "66.175.217.145:32349"
DBMS           = "timbergrove_rigs"
TABLE          = "rig_data"
UNS_NAMESPACE  = "wits/record01"
```

---

## Prompt

You are connected to an AnyLog network via MCP. Using the parameters above, build a complete, production-quality 
single-file HTML dashboard for **oil rig interval-based data**.

### Step 1 — Schema discovery (run these MCP tool calls first)

Before writing any code, use the MCP tools to discover the live schema:

1. `listTables(dbms="timbergrove_rigs")` — confirm `rig_data` is present
2. `listColumns(dbms="timbergrove_rigs", table="rig_data")` — get exact column names
3. Sample recent rows to confirm field names and value ranges:
```sql
   SELECT * FROM rig_data ORDER BY timestamp DESC LIMIT 5
```
4. Discover active rig IDs:
```sql
   SELECT DISTINCT rig_id FROM rig_data LIMIT 20
```
5. `getClusterNodeMapping(dbms="timbergrove_rigs")` — identify which operator nodes
   hold the data; use the returned IP:Port values to populate `data_nodes` in
   time-series queries
6. If `UNS_NAMESPACE` is set, query:
```anylog
blockchain get uns where namespace = wits/record01
```
Returns name, namespace, uns_level, loc, id, date, and ledger fields.

Use what you discover to confirm the real column names for: rate of penetration,
weight on bit, RPM, torque, hookload, bit depth, flow rate, total gas, standpipe
pressure, choke pressure, and depth. Do not assume column names — use only what
`listColumns` returns.


### Step 2 — Visual design

Dark industrial theme. Single file, no build tools, no frameworks beyond Chart.js
and Google Fonts.

**Fonts** (load from Google Fonts):
- `Rajdhani` — headers, brand, KPI values
- `Share Tech Mono` — monospace labels, timestamps, log panel
- `Barlow Condensed` — body text

**Color tokens** (define as CSS variables on `:root`):
```css
--bg:#03070e;  --bg2:#060c17;  --bg3:#09111f;
--surf:#0d1829;  --surf2:#111f34;
--bdr:#1b2e4a;  --bdr2:#223661;
--txt:#ccdff5;  --dim:#6b8db5;  --muted:#2d4a6a;
--amber:#f59e0b;  --cyan:#22d3ee;  --green:#22c55e;
--red:#f43f5e;  --orange:#fb923c;  --purple:#c084fc;
--sky:#38bdf8;  --gold:#fbbf24;  --lime:#a3e635;
--ff-title:'Rajdhani',sans-serif;
--ff-mono:'Share Tech Mono',monospace;
--ff-body:'Barlow Condensed',sans-serif;
```

**Extras:** grain overlay via inline SVG noise filter on `body::after` at opacity 0.4;
custom scrollbars (4px, matching `--bdr2`); sticky header with `backdrop-filter: blur(8px)`.

**Chart.js:** `https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js`


### Step 3 — Layout

#### Header
- 🔥 flame icon + **TIMBERGROVE** brand + `Interval Monitor` sub-label
- Live status dot (idle / live / error) with pulsing animation when live
- UTC clock updating every second — amber, monospace
- Rig filter badges — one per discovered rig ID, toggleable
- Mode chip showing active connection mode (see Step 4)

#### Control bar
One row using `.ctrl` / `.cl` / `.ci` / `.btn` classes:

| Control | Type | Default |
|---|---|---|
| Node URL | `<input>` | `http://66.175.217.145:32349` |
| Mode | `<select>` | Direct |
| Interval | `<input number>` | `5` |
| Window | `<select>` | `1H` |
| Bucket | `<select>` | `15 min` |
| ▶ Start | button | — |
| ⏹ Stop | button | shown while polling |
| ↻ Refresh | button | always shown |

**Proxy URL sub-row** — appears below the control bar only when Mode ≠ Direct.
Shows the effective endpoint (e.g. `http://localhost/api/query`). Hidden in Direct mode.

**CORS warning banner** — shown on fetch failure in Direct mode. Instructs the user
to switch Mode to nginx or proxy rather than describing manual proxy setup.

#### Fleet summary panel
One card per discovered rig ID. Each card shows:
- Rig ID and location (from UNS if available)
- KPI snapshot: ROP, WOB, RPM, torque
- Activity badge: DRILLING / TRIPPING / IDLE (derived from ROP threshold)
- Clicking a card scrolls to that rig's detail section

#### Per-rig sections (one per discovered rig ID)
Each section contains three columns:

**Left — SVG rig diagram** (`buildRigSVG(rigId, data)`)
An animated schematic of a drilling rig with live sensor values overlaid as SVG
`<text>` elements that update in place: bit depth marker on the derrick, flow rate
indicator, hookload gauge. Colour the derrick bar with the rig's accent colour.
Include CSS `@keyframes` animations for the kelly drive rotation and fluid flow pulses.

**Centre — vertical bar charts**
Three stacked `vc-card` panels (Chart.js or custom SVG bars):
- Standpipe pressure
- Choke pressure
- Depth progress

**Right — metrics + time-series**
- KPI grid (2×4): ROP, WOB, RPM, torque, hookload, bit depth, flow rate, total gas —
  each showing current value, unit, and a colour-coded delta arrow vs. previous poll
- Time-series charts (Chart.js line):
  - ROP & WOB on dual Y-axis
  - RPM & torque on dual Y-axis
  - Flow rate (single)
  - Total gas (single)

#### API call log panel
Collapsible panel at the bottom. Each entry shows timestamp, `[DIRECT]` / `[NGINX]` /
`[PROXY]` prefix, URL, HTTP status, and latency in ms. Colour-coded by status (green
ok, red error). Capped at 100 entries, newest first.


### Step 4 — Connection modes

Support three modes via the Mode dropdown. All fetch logic routes through a single
`anylogFetch(bodyObj, timeoutMs)` dispatcher:

**Direct** — browser POSTs straight to the AnyLog query node:
```js
fetch(nodeUrl, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    'AnyLog-Agent':  'AnyLog/1.23',
    'command':     `sql ${dbms} format=json:list and stat=false  ${sql}`,
    'destination': 'network'
  })
})
```

**nginx** — browser POSTs to `{nginxUrl}/api/query` with the same body shape.

**Flask proxy** — browser POSTs to `{proxyUrl}/api/query` using the simple format:
```js
{ dbms: 'timbergrove_rigs', sql: '...' }
```

Mode chip in the header reflects the active mode:
- `DIRECT` — cyan tint (`--cyan`)
- `NGINX` — green tint (`--green`)
- `PROXY` — amber tint (`--amber`)

SQL queries must always include `"destination": "network"`.
Node and blockchain commands omit `destination` and run locally on the query node.


### Step 5 — Queries

Use `increments()` for all time-series to avoid pulling raw rows. Pattern:
```sql
SELECT increments(minute, 15, timestamp),
       min(timestamp)  as ts,
       avg(rop)        as rop_avg,
       max(wob)        as wob_max,
       avg(rpm)        as rpm_avg
FROM rig_data
WHERE timestamp >= NOW() - 1 hour
  AND rig_id = 'RIG-TX-001'
ORDER BY timestamp
```

Substitute the actual column names returned by `listColumns` for the placeholders above.

Polling interval: configurable via the Interval control (default 5 minutes).
Time window: configurable via the Window control (1H / 6H / 24H).
Bucket size: configurable via the Bucket control (5 min / 15 min / 1H).

### What NOT to include

- No React, Vue, or build tooling — vanilla HTML/CSS/JS only
- No external CSS frameworks
- No hard-coded rig IDs — discover them from `DISTINCT rig_id`
- No hard-coded column names — use only what `listColumns` returns
- No unbounded `SELECT *` at runtime


## Connection parameters to pre-fill
```js
nodeUrl = "http://66.175.217.145:32349"
dbms    = "timbergrove_rigs"
table   = "rig_data"
```

## Deliver

A single `.html` file. No external dependencies beyond Google Fonts and Chart.js from
cdnjs. Open directly in a browser — set Mode and Node URL in the control bar, then
click ▶ Start.