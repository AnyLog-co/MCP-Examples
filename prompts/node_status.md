# AnyLog Node Inspector — Dashboard Generator Prompt

## Parameters — set these before running the prompt

```
QUERY_NODE = "24.5.219.50:32349"    # AnyLog query node  host:port (no http://)
```

> Unlike data dashboards, this dashboard has no DBMS, TABLE, or UNS parameters.
> It sends only node and network diagnostic commands — no SQL queries.

---

## Prompt

Build a complete, production-quality single-file HTML dashboard for inspecting the health and
connectivity of an AnyLog node. The dashboard issues three diagnostic commands in parallel and
renders each result in its own panel.

---

### Step 1 — Understand the commands and response shapes

There are no data queries in this dashboard. All three commands are **node/network commands**
(no `destination` key — processed locally by the query node):

---

#### Command 1 — `get status where format=json`

Returns a JSON object describing the node's current state. Example shape:
```json
{
  "assigned_name": "anylog-query@24.5.219.50:32348",
  "status":        "running",
  "profiling":     false
}
```
May contain nested objects — flatten recursively into key-value pairs for display.
Classify values for colour-coding:
- `status = "running"` / `true` / `"ok"` → green
- `status = "stopped"` / `false` / `"error"` → red
- Name, IP, port, version fields → accent colour (highlight)

---

#### Command 2 — `test node`

Returns a **pipe-delimited plain text table** (not JSON). Example shape:
```
Test                 |Status |
---------------------|-------|
TCP connection       |Pass   |
REST connection      |Pass   |
Operator processes   |running|
```

Parse with `parsePipeTable()` (see connection layer below).
Render as a styled HTML table with pass/fail/running badges.
Determine overall section status (green dot / red dot) from whether all rows pass.

---

#### Command 3 — `test network`

Also returns a **pipe-delimited plain text table**. Example shape:
```
Node                   |Type     |Status|
-----------------------|---------|------|
24.5.219.50:32348      |operator |+     |
24.5.219.50:32349      |query    |+     |
```

Parse with the same `parsePipeTable()` helper.
Render with:
- **Node type pills**: `master` (amber), `operator` (blue), `query` (purple)
- **Status badges**: `+` → online (green), `fail` → red
- **Summary strip** above the table: Total nodes, Online count, count per node type

---

### Step 2 — Build the dashboard

---

#### Connection layer

All calls use a single `queryNode()` function. No `anylogFetch()` dispatcher is needed —
this dashboard is direct POST only. There are no SQL queries and no `destination` field.

```js
// ═══════════════════════════════════════════════════════════════
//  ANYLOG NODE INSPECTOR — Connection
// ═══════════════════════════════════════════════════════════════
//
//  All commands POST to the AnyLog query node URL directly.
//  Body shape — same for all three commands (no destination key):
//    {
//      "User-Agent": "AnyLog/1.23",
//      "command":    "<command string>"
//    }
//
//  Response types:
//    get status where format=json  →  JSON object   (parse with JSON.parse)
//    test node                     →  plain text     (parse with parsePipeTable)
//    test network                  →  plain text     (parse with parsePipeTable)
//
//  CORS note: direct browser POST requires the node to respond with
//  Access-Control-Allow-Origin: *. If blocked, the CORS warning is shown
//  and the user can use anylog_proxy.py in REST mode:
//    python3 anylog_proxy.py --anylog-url http://{QUERY_NODE}
//  Then point the Node URL field at http://localhost:8080 instead.
// ═══════════════════════════════════════════════════════════════

const USER_AGENT = "AnyLog/1.23";

async function queryNode(nodeUrl, command) {
  const resp = await fetch(nodeUrl, {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ "User-Agent": USER_AGENT, "command": command }),
    signal:  AbortSignal.timeout(15000),
  });
  return await resp.text();   // always read as text; callers parse as needed
}
```

---

#### Pipe-delimited text table parser

AnyLog returns `test node` and `test network` as fixed-width pipe-delimited text.
Include this parser — it is essential:

```js
/**
 * Parse AnyLog pipe-delimited table text into { headers, rows }.
 * Table format:
 *   Col1              |Col2   |Col3  |
 *   ------------------|-------|------|
 *   val1              |val2   |val3  |
 *
 * Returns null if text cannot be parsed.
 */
function parsePipeTable(text) {
  const lines  = text.split("\n").filter(l => l.trim().length > 0);
  if (lines.length < 2) return null;

  const sepIdx = lines.findIndex(l => /^[-|]+$/.test(l.trim()));
  if (sepIdx < 1) return null;

  const headerLine = lines[sepIdx - 1];
  const dataLines  = lines.slice(sepIdx + 1);
  const sep        = lines[sepIdx];

  // Build column split positions from pipe characters in separator
  const pipes = [];
  for (let i = 0; i < sep.length; i++) {
    if (sep[i] === "|") pipes.push(i);
  }
  if (pipes.length === 0) return null;

  function sliceCols(line) {
    const cells = [];
    let prev = 0;
    for (const p of pipes) {
      cells.push(line.substring(prev, p).trim());
      prev = p + 1;
    }
    const rest = line.substring(prev).trim();
    if (rest) cells.push(rest);
    return cells;
  }

  const headers = sliceCols(headerLine);
  const rows    = dataLines
    .filter(l => l.trim() && !/^[-|]+$/.test(l.trim()))
    .map(l => sliceCols(l));

  return { headers, rows };
}
```

---

#### Config — minimal, just Node URL

No DBMS, TABLE, or UNS fields. The config card contains only:

| Field | Default | Notes |
|---|---|---|
| Node URL | `http://{QUERY_NODE}` | Full URL including `http://` |
| **Fetch Data** button | — | Fires all three commands in parallel |
| **Clear** button | — | Resets all panels to placeholder state |

Show an **info strip** below the inputs explaining the POST body shape — this is a
developer/ops tool and the transparency is valuable:
```
▸ Mode: Browser → AnyLog node directly via POST
  Header: Content-Type: application/json
  Body:   {"User-Agent":"AnyLog/1.23","command":"..."}
```

Show a **CORS warning** (hidden by default, shown on network errors) explaining that
`Access-Control-Allow-Origin: *` is required for direct browser POST, and that
`anylog_proxy.py --anylog-url http://{QUERY_NODE}` is the alternative.

---

#### Main layout — three panels

Fire all three commands in parallel with `Promise.allSettled()`. Each panel transitions
through three states independently: **placeholder → loading spinner → result / error**.

```
┌─────────────────────────────────────────────────┐
│  Node Status          (full width)              │
│  get status where format=json  →  KV grid       │
└─────────────────────────────────────────────────┘
┌──────────────────────┐  ┌──────────────────────┐
│  Node Test           │  │  Network Test        │
│  test node           │  │  test network        │
│  → pass/fail table   │  │  → node list table   │
└──────────────────────┘  └──────────────────────┘
```

Each panel has:
- **Section header**: icon + title + status dot (loading / ok / error) + command label in monospace
- **Section body**: result content or placeholder
- **Raw toggle** (`<details>`) at the bottom — shows the unmodified response text, collapsed by default

---

#### Panel 1 — Node Status (full width)

Render the parsed JSON as a key-value grid:
- Flatten nested objects recursively with dot-notation keys (`network.ip`, `processes.operator`, etc.)
- Each row: key (dim) on the left, value (colour-coded) on the right
- Hover highlight on each row
- Colour classes:
  - `.running` → green (status=running, true, ok)
  - `.stopped` → red (status=stopped, false, error)
  - `.highlight` → accent colour (name, ip, port, version fields)
  - `.bool-false` → dim (boolean false values)
- Update the **connection badge** in the header on success: show `assigned_name` or node address

---

#### Panel 2 — Node Test (half width, left)

Render the parsed pipe table as a styled HTML table:
- Last column and any column named "Status" → pass/fail/running badge
- `Pass` / `running` → green badge
- `Fail` → red badge
- `+` → green plus badge
- Determine section dot: green if all rows pass, red otherwise

---

#### Panel 3 — Network Test (half width, right)

Render the parsed pipe table with enriched cells:
- **Node type column** → colour-coded pill: `master` (amber), `operator` (blue), `query` (purple), unknown (grey)
- **Status column** (last) → `+` green badge, `fail` red badge
- **Summary strip** above the table:
  - Total node count
  - Online count (`+` status)
  - Count per node type (master N / operator N / query N)

---

#### Connection badge — always visible in header

Show the connection state in the top-right of the header at all times:
- **Not connected** (default) → dim border, grey dot, "Not connected"
- **Connected** → teal border + glow, animated blink dot, `assigned_name` from status response
- **Error** → red border, red dot, "Offline"

---

#### Footer

- **Auto-refresh toggle** — pill switch, 30-second interval when on, label shows "Auto-refresh (30s)"
- **Last fetch** timestamp — updates after every fetch cycle
- Version label: `AnyLog Node Inspector · Direct POST · v1.23`

---

#### Design

Match the existing dashboard exactly:

- **Background**: `#0a0c10`, subtle teal grid overlay (`rgba(0,212,170,0.025)` lines, 40px spacing)
- **Surface**: `#111318` cards, `#181c24` secondary
- **Accent**: `#00d4aa` (teal) as primary, `#0088ff` (blue) secondary, `#a78bfa` (purple) tertiary
- **Font**: system monospace stack (`Cascadia Code`, `Fira Code`, `Consolas`, `Menlo`) throughout — **no Google Fonts**
- **Logo**: `AL` square with teal→blue gradient, `box-shadow: 0 0 24px rgba(0,212,170,0.25)`
- **Cards**: `border-radius: 12px`, `border: 1px solid #1e2330`
- **Buttons**: teal gradient primary (`#00d4aa → #00b894`, black text), `border-radius: 7px`
- **Status dots**: animated blink for ok, solid for error, pulsing for loading
- **Section icons**: small SVG icons in colour-tinted squares matching the accent for that panel

---

### Step 3 — Output

Deliver a **single `.html` file** with no external dependencies — no CDN, no Google Fonts,
no Chart.js (this dashboard has no charts).

All configuration lives in a single `const` at the top of `<script>`:
```js
const CONFIG = {
  nodeUrl: "http://{QUERY_NODE}",
};
```

Include a comment block near the top of `<script>` documenting:
- The three commands and their response types (JSON vs pipe-delimited text)
- The POST body shape (no `destination` key — these are node-local commands)
- The `parsePipeTable()` contract and expected input format
- The CORS constraint and `anylog_proxy.py` as the workaround
- Why `resp.text()` is used instead of `resp.json()` (mixed response types)