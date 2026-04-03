# Wind Turbine · MCP Live Dashboard — Generation Prompt

## Parameters — fill these in before running

```
DATA_TYPE      = "Wind Turbine"
PROXY_URL      = "http://localhost:8080"          ← anylog_proxy.py in MCP mode
DBMS           = "wind_turbine"
UNS_NAMESPACE  = "wind"
TURBINES       = [1, 2, 3, 5]
REFRESH_OPTS   = [30s (demo), 1min (demo), 5min, 10min]
DEFAULT_REFRESH = 5min
```

---

## Prompt

You have a live MCP connection to an AnyLog network via `anylog-api-mcp-proxy`.

Use the MCP tools to discover the live schema, sample recent data, and confirm
which turbine IDs are active. Then generate a **single self-contained `.html`
dashboard** that routes every data fetch through `anylog_proxy.py` running in
**MCP mode** (Example 3 — experimental).

---

### Step 1 — Schema discovery (run these MCP tool calls first)

Before writing any code, call the MCP tools in this order:

1. `listNetworkDatabases` — confirm `wind_turbine` is present
2. `listTables(dbms="wind_turbine")` — enumerate all tables
3. For each key table below, call `listColumns`:

| Table | Key columns expected |
|---|---|
| `power_output` | `turbine_id`, `power_avg`, `power_max`, `power_min`, `timestamp` |
| `wind` | `turbine_id`, `wind_avg`, `wind_max`, `wind_min`, `timestamp` |
| `rpm` | `turbine_id`, `rpm_avg`, `rpm_max`, `rpm_min`, `timestamp` |
| `blade_pitch` | `turbine_id`, `pitch_avg`, `timestamp` |
| `energy` | `turbine_id`, `energy_kwh`, `timestamp` |
| `operations` | `turbine_id`, `operating_hours`, `nacelle_position`, `timestamp` |
| `atmosphere` | discover columns live |
| `ice_detection` | discover columns live |

4. Run a bounded sample query to confirm active turbine IDs and data recency:
   ```sql
   SELECT DISTINCT turbine_id FROM power_output
   ORDER BY turbine_id LIMIT 20
   ```
5. Run one recent-data spot-check per key table (LIMIT 5) to verify field names
   and value ranges before hard-coding anything.

Use the actual column names returned — do not assume they match the table above.

---

### Step 2 — What to build

A dark industrial single-file HTML dashboard with the following structure:

#### Visual style
- Fonts: `Rajdhani` (headers/values), `Share Tech Mono` (labels/mono), `Barlow Condensed` (body)
- Color tokens via CSS variables — dark navy background, amber/sky-blue accents for wind energy,
  cyan for RPM, purple for blade pitch, green for healthy status
- Subtle grain overlay (SVG noise filter at low opacity)
- Chart.js 4.x from cdnjs for all charts (no other charting dependencies)

#### Header
- Title: **Wind Turbine · MCP Live Dashboard**
- Live status dot (idle / fetching / live / error states)
- UTC clock (updates every second)
- Mode chip: `⚠ MCP mode` — purple tint, always visible as a reminder this is Example 3

#### Control bar (one row)
All controls use the same `.ctrl` / `.cl` / `.ci` / `.btn` CSS class pattern:

| Control | Type | Default |
|---|---|---|
| Proxy URL | `<input>` text | `http://localhost:8080` |
| Refresh interval | `<select>` | 5 min |
| ▶ Start | button | — |
| ⏹ Stop | button | shown only while polling |
| ↻ Now | button | always shown |
| 🔍 Query | log button with count badge | — |
| ⚠ Error | log button with count badge, red when errors exist | — |
| 📋 Prompt | log button with count badge | — |
| Countdown | `Next refresh in M:SS` + progress bar | right-aligned |

#### Three log modals (opened by the toolbar buttons above)

**🔍 Query Log**
Every SQL query executed, shown newest-first. Each entry shows:
- Timestamp, table name, row count returned, latency in ms
- The exact SQL that was sent (in a monospace code block)

**⚠ Error Log**
Any failed fetch, shown newest-first. Each entry shows:
- Timestamp, table name, error message
- The equivalent `curl` command so the user can reproduce it manually:
  ```
  curl -X POST http://localhost:8080/api/query \
    -H "Content-Type: application/json" \
    -d '{"dbms":"wind_turbine","sql":"SELECT ..."}'
  ```
- The Error button badge turns red and stays red when any errors exist in the log

**📋 Prompt Evolution**
One entry per refresh cycle, shown newest-first. Each entry shows:
- Cycle number, timestamp
- Each query in the cycle as a numbered step: label, SQL, rows returned, ms (or error)
- This documents how the dashboard's "conversation" with the MCP evolves over time

#### Fleet overview panel
Spanning the full width, showing aggregate metrics across **all active turbines** for
the last 1 hour. KPI tiles:

| KPI | Source | Color |
|---|---|---|
| Active Turbines | count of distinct turbine_ids with data in last 1h | sky blue |
| Fleet Avg Power | mean of all power_avg rows | amber |
| Fleet Avg Wind | mean of all wind_avg rows | sky blue |
| Fleet Avg RPM | mean of all rpm_avg rows | cyan |
| Total Power Samples | row count from power_output | green |

#### Per-turbine cards (2-column responsive grid)
One card per active turbine (discovered dynamically — do not hard-code IDs).
Each card shows:

- Header: **TURBINE {id}** · status chip (Active / No data / Waiting)
- Metric grid (2×2):
  - Avg Power (kW) — amber — with sub-line: max / min
  - Avg Wind Speed (m/s) — sky blue — with sub-line: max / min
  - Avg RPM — cyan
  - Avg Blade Pitch (°) — purple
- Mini Chart.js line chart: `power_avg` over the last hour (time on x, value on y,
  no axes shown, filled area under the line)
- Footer: last timestamp seen · sample count

All metrics are **aggregated client-side** from the raw rows returned — do not
use GROUP BY in SQL (AnyLog distributed SQL does not guarantee GROUP BY support
across nodes).

---

### Step 3 — Data fetching rules (MCP proxy constraints)

This dashboard runs in **Example 3 MCP mode** — every `fetch()` goes to the proxy
which serializes calls through MCP. Follow these rules strictly:

**All SQL must be bounded:**
```js
// ✅ CORRECT — bounded time window + LIMIT
`SELECT turbine_id, power_avg, power_max, power_min, timestamp
 FROM power_output
 WHERE timestamp >= NOW() - 1 hour
 ORDER BY timestamp LIMIT 500`

// ❌ WRONG — unbounded
`SELECT * FROM power_output`
```

**POST format to the proxy** (simple body shape):
```js
fetch(proxyUrl + '/api/query', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    dbms: 'wind_turbine',
    sql:  'SELECT ...'
  })
})
// Response: { results: [...], row_count: N, dbms: "wind_turbine" }
```

**Queries per refresh cycle** — run these 4 queries sequentially (not in parallel,
to avoid overwhelming the MCP worker queue):
1. `power_output` — last 1 hour, LIMIT 500
2. `wind` — last 1 hour, LIMIT 500
3. `rpm` — last 1 hour, LIMIT 500
4. `blade_pitch` — last 1 hour, LIMIT 500

Add a short delay (≥ 500ms) between each fetch to respect the proxy's call-delay
setting (default 1.5s between MCP calls).

**Refresh interval:** 5 minutes default (also offer 10 min, 1 min, 30s for demos).

**Error handling:** On any fetch failure, log the error to the Error log, show the
curl equivalent, mark the live-dot red, and continue — do not stop polling.

---

### Step 4 — Countdown and progress

Show a footer bar with:
- `Next refresh in M:SS` — counts down, resets on each cycle
- A thin progress bar (fills left-to-right as time passes since last refresh)
- `⚠ MCP proxy mode` reminder label right-aligned

---

### Step 5 — Polish

- All three modal overlays close on Escape key or clicking the backdrop
- Live dot: grey (idle) → no class (fetching, shows text "fetching…") →
  pulsing green (live) → solid red (error)
- Charts: `animation: false` (no animate on data update), `responsive: true`,
  `maintainAspectRatio: false`, no visible axes, no legend, no tooltip
- Cards appear/disappear dynamically based on which turbine IDs have data —
  no hard-coded turbine list
- Grain overlay (`body::after`) using inline SVG noise filter at opacity 0.4
- All fonts loaded from Google Fonts (Rajdhani, Share Tech Mono, Barlow Condensed)
- Chart.js from `https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js`

---

### What NOT to include

- No React, Vue, or build tooling — vanilla HTML/CSS/JS only
- No external CSS frameworks
- No direct AnyLog REST calls — all data goes through the proxy
- No `SELECT *` or unbounded queries
- No parallel fetches in the same cycle
- No hard-coded turbine IDs (discover them from query results)

---

## Connection parameters to pre-fill in the generated dashboard

```js
// Proxy URL input default
proxyUrl = "http://localhost:8080"

// Database
dbms = "wind_turbine"

// Tables to query each cycle (in order)
tables = ["power_output", "wind", "rpm", "blade_pitch"]

// Time window
window = "1 hour"

// Row cap per query
limit = 500

// Default refresh interval (seconds)
defaultRefreshSecs = 300   // 5 minutes
```

---

## Deliver

A single `.html` file — no external dependencies beyond Google Fonts and Chart.js from cdnjs.
Open it in any browser, point the Proxy URL at `anylog_proxy.py` running in MCP mode
(`--anylog-url http://HOST:PORT/mcp/sse`), and click ▶ Start.