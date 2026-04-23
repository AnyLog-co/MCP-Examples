# AnyLog × Base44 App Generator Prompt

## Parameters — set these before running

```
DATA_TYPE      = "Power Plant"          # human label used in the UI
QUERY_NODE     = "24.5.219.50:32349"    # AnyLog query node  host:port (no http://)
DBMS           = "cos"                  # AnyLog database name
TABLE          = "pp_pm"                # primary table (leave blank to auto-discover)
UNS_NAMESPACE  = "Smart_City"          # UNS root namespace (blank if not used)
APP_NAME       = "Power Plant Monitor" # Base44 app display name
```

## Architecture notes (fill in before running)

Describe your desired frontend layout here. Be as specific or as vague as suits
your needs — Claude will fill in the gaps from the MCP discovery:

```
Example:
- Overview page: KPI cards (total power, reactive power, power factor, active monitors)
- Per-monitor drilldown: phase currents, power trend chart, raw data table
- Node health page: status, test node, test network results
- Navigation: sidebar with page links
```

---

## Prompt

You are connected to an AnyLog network via MCP. Using the parameters and
architecture notes above, produce **two Base44 prompts** — one for the backend
service and one for the frontend. Do not build the app yourself. Your output is
prompt text that a developer will paste into Base44.

---

### Step 1 — Discover the data (run before writing any prompt)

Use the MCP tools to understand what is actually in the network:

1. Use `executeQuery` to sample the schema and recent rows from `{DBMS}.{TABLE}`.
   If `TABLE` is blank, use `listTables` to find available tables first.
2. Use `executeQuery` to run `SELECT distinct(monitor_id) FROM {TABLE}` (or the
   equivalent ID column) to discover all device/monitor IDs.
3. Use `getClusterNodeMapping` to find which operator nodes hold the data and their
   IP addresses.
4. Use `checkStatus` to confirm the node is reachable.
5. If `UNS_NAMESPACE` is set, query
   `blockchain get uns where namespace = {UNS_NAMESPACE}` to retrieve the UNS
   metadata (name, namespace, uns_level, loc, id, date, ledger fields).

Use the results to determine the exact column names, data types, ID field name,
timestamp format, and meaningful aggregation columns before writing either prompt.

---

### Step 2 — Output: Backend Prompt

Write a prompt that a developer will paste into Base44 to generate the backend
service. The prompt must be complete and self-contained — it will be run without
any MCP connection. Include all concrete values discovered in Step 1.

The backend prompt must instruct Base44 to create the following backend functions.
Each function makes a `POST` call to the AnyLog query node. **Base44 backend
runs server-side so CORS is not an issue — no proxy is needed.**

#### AnyLog POST body shapes

**SQL queries** — always include `destination: "network"`:
```
POST http://{QUERY_NODE}
Content-Type: application/json
{
  "AnyLog-Agent":  "AnyLog/1.23",
  "command":     "sql {dbms} format=json:list and stat=false  {SQL}",
  "destination": "network"
}
```
- `format=json:list and stat=false` returns a plain flat JSON array with no
  trailing metadata object
- `destination: "network"` fans the query out to all operator nodes — required
  for SQL or the query runs only on the query node (which holds no operator data)

**Blockchain / node commands** — no `destination` key:
```
POST http://{QUERY_NODE}
Content-Type: application/json
{
  "AnyLog-Agent": "AnyLog/1.23",
  "command":    "blockchain get uns where namespace = {namespace}"
}
```

**Response:** always a flat JSON array. Parse with:
```
rows = Array.isArray(response) ? response : []
```

#### Required backend functions

The backend prompt must produce these functions, with exact SQL and field names
filled in from the Step 1 discovery:

1. **`getNodeStatus()`**
   Command: `get status where format=json`
   Returns: parsed JSON status object

2. **`getLatestSnapshot(limit)`**
   SQL: `SELECT * FROM {TABLE} WHERE timestamp >= NOW() - 5 minutes ORDER BY timestamp DESC LIMIT {limit}`
   Returns: flat array of rows, newest first

3. **`getMonitorIds()`**
   SQL: `SELECT distinct({id_column}) FROM {TABLE}`
   Returns: flat array of unique ID values

4. **`getTimeSeries(monitorId, hours)`**
   SQL: `SELECT timestamp, {key_metrics} FROM {TABLE} WHERE {id_column} = '{monitorId}' AND timestamp >= NOW() - {hours} hours ORDER BY timestamp ASC`
   Returns: flat array ordered by timestamp ascending

5. **`getIncrements(monitorId, hours, bucketMinutes)`**
   SQL: `SELECT increments(minute, {bucketMinutes}, timestamp), min(timestamp) as timestamp, {agg_projections} FROM {TABLE} WHERE {id_column} = '{monitorId}' AND timestamp >= NOW() - {hours} hours ORDER BY timestamp`
   Returns: flat array of time-bucketed rows

6. **`getUNS()`** *(only if UNS_NAMESPACE is set)*
   Command: `blockchain get uns where namespace = {UNS_NAMESPACE}`
   Returns: flat array of UNS policy objects

7. **`getClusterTopology()`** *(include known topology from Step 1 as fallback)*
   Command: `blockchain get cluster where dbms = {DBMS}`
   Returns: flat array of cluster objects; falls back to hardcoded topology
   from Step 1 discovery if the blockchain query returns empty

Include in the backend prompt:
- The exact `QUERY_NODE` URL as a named constant
- All field names and column names discovered in Step 1
- Error handling: each function should return `{error: string}` on failure rather
  than throwing, so the frontend can display a user-friendly message
- A note that all responses are flat arrays — no nested `{results: [...]}` wrapper

---

### Step 3 — Output: Frontend Prompt

Write a second prompt that a developer will paste into Base44 to generate the
frontend. This prompt assumes the backend functions from Step 2 already exist.
Include all concrete values (column names, monitor IDs, UNS metadata) from Step 1
so the frontend prompt is self-contained.

The frontend prompt must instruct Base44 to build the UI described in the
**Architecture notes** above, using the backend functions. It must specify:

#### Component structure

Map the architecture notes to concrete Base44 components. For each page/section:
- Which backend function(s) to call
- How to call them (on mount, on user interaction, on a timer)
- What to render from the response (which fields, what format)
- Refresh interval if applicable (e.g. every 30 seconds for live data)

#### Data binding rules

- `getLatestSnapshot()` → derive the current per-monitor snapshot client-side
  by keeping the most recent row per `{id_column}` (data arrives DESC ordered,
  so the first occurrence of each ID is the latest)
- `getTimeSeries()` / `getIncrements()` → used for chart components; always
  order by timestamp ascending for display
- `getMonitorIds()` → populate a dropdown/selector that controls which monitor
  the detail view shows
- `getNodeStatus()` → shown in a header status badge, polled every 5 minutes
- `getUNS()` → shown in a metadata panel as labelled key-value cards; if `loc`
  field is present, include an OpenStreetMap link

#### Config bar

The frontend must include a visible config bar (not hidden in a drawer) with:
- Node URL field (pre-filled: `http://{QUERY_NODE}`)
- Database field (pre-filled: `{DBMS}`)
- Table field (pre-filled: `{TABLE}`)
- Apply & Connect button — re-calls all backend functions with the new values

The config values are passed as parameters to the backend functions, not
hardcoded in the frontend.

#### Design

- Dark industrial theme: `#0a0c0e` background, `#f59e0b` amber accent
- Fonts: Share Tech Mono (data values) + Barlow Condensed (labels) from Google Fonts
- Status indicator in the header: green dot (node reachable) / red dot (unreachable)
- Loading states for each data section (spinner while fetching, error message on failure)
- If a backend function returns `{error: ...}`, display the error inline in the
  relevant section rather than crashing the whole page

---

### Output format

Deliver **two clearly labelled blocks**:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BACKEND PROMPT  (paste this into Base44 first)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[backend prompt text here]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FRONTEND PROMPT  (paste this into Base44 second)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[frontend prompt text here]
```

Each prompt must be standalone — a developer should be able to paste it into
Base44 without needing to refer back to this document or have an MCP connection.
All discovered values (node URL, column names, monitor IDs, UNS data, cluster
topology) must be embedded directly in the prompts.