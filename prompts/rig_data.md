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

I have an existing single-file HTML dashboard at:
**https://raw.githubusercontent.com/AnyLog-co/timbergrove/refs/heads/main/timbergrove_interval.html**

I love the visual design — the dark industrial theme, the animated SVG rig diagrams, the
fleet summary cards, the KPI grid, the vertical bar charts, the time-series charts, and
the API call log panel. **Keep all of that exactly as-is.**

The only things that need to change are:

### 1 — Fix the AnyLog connection layer

The dashboard currently calls AnyLog incorrectly. Replace every `fetch()` call with a
central dispatcher `anylogFetch(bodyObj, timeoutMs)` that supports three connection modes:

**Direct POST** (default) — browser POSTs straight to the AnyLog query node:
```js
fetch(nodeBase(), {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    'User-Agent':  'AnyLog/1.23',
    'command':     `sql ${dbms} format=json:list and stat=false  ${sql}`,
    'destination': 'network'
  })
})
```

**nginx mode** — browser POSTs to `{nginxUrl}/api/query` using the same body shape;
nginx handles routing to the AnyLog node server-side:
```js
fetch(`${nginxUrl}/api/query`, { method: 'POST', ... same body ... })
```

**Flask proxy mode** — browser POSTs to `{proxyUrl}/api/query` and includes a `url`
field in the body so the proxy knows where to forward:
```js
fetch(`${proxyUrl}/api/query`, {
  method: 'POST',
  body: JSON.stringify({ url: nodeUrl, 'User-Agent': 'AnyLog/1.23', 'command': '...' })
})
```

The two POST body shapes are:

- **SQL queries** — must include `destination: "network"` to fan out to operator nodes:
  `command: "sql {dbms} format=json:list and stat=false  {sql}"`
  Use `format=json:list and stat=false` — this returns a plain JSON array and suppresses
  the row-count metadata object that plain `format=json` appends.

- **Blockchain / node commands** — no `destination` key (run locally on the query node):
  `command: "get status where format=json"`

### 2 — Add a Mode selector + proxy URL row to the control bar

Add these controls to the existing control bar, keeping its exact style (`.ctrl`,
`.cl`, `.ci`, `.csep`, `.btn` classes):

| Control | Type | Default |
|---|---|---|
| Mode | `<select>` with options: Direct / nginx / Flask Proxy | Direct |
| Proxy URL | `<input>` (hidden when mode = Direct) | `http://localhost` |

The proxy URL row should slide into view below the main control bar only when mode ≠ Direct,
showing the effective endpoint: e.g. `http://localhost/api/query`.

### 3 — Add a mode chip to the header

Add a small pill badge next to the status dot showing the active mode:
- `DIRECT` — cyan tint  
- `NGINX` — green tint  
- `PROXY` — amber tint

Use the existing CSS variable palette (`--cyan`, `--green`, `--amber`, `--surf`, `--bdr2`).

### 4 — Update the CORS warning banner

The existing banner text should tell the user to switch the Mode selector rather than
run a proxy manually.

### 5 — Update the log panel

Each log entry should prefix the URL with `[DIRECT]`, `[NGINX]`, or `[PROXY]` so it is
clear which mode was active for each call.

---

## What NOT to change

- The visual design, colour tokens, fonts, layout, or any CSS
- The SVG rig diagrams (`buildRigSVG`)
- The fleet summary cards (`renderFleetSummary`)
- The KPI grid, vertical bar charts, time-series charts, or chart registry
- The UNS/sensor definitions (`UNS` object)
- The data store, polling logic, or formatters
- The API call log panel structure
- The existing Node URL, Interval, Window, and Bucket controls

---

## Connection parameters to pre-fill

```js
// Node URL input default
"http://66.175.217.145:32349"

// UNS / database
dbms:  "timbergrove_rigs"
table: "rig_data"
```

---

## Deliver

A single `.html` file — the original dashboard with only the five changes above applied.
No new dependencies. All existing external resources (Chart.js from cdnjs, Google Fonts)
remain unchanged.