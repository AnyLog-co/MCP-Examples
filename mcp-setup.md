# Connecting to AnyLog via MCP

AnyLog exposes a Model Context Protocol (MCP) server that LLM clients can connect
to directly. This unlocks two complementary use cases:

---

## Three ways to use the MCP

### 1. Dashboard generation

Connect an LLM to the MCP **once** to generate a dashboard. The LLM uses MCP tools
to discover the live schema, sample data, node topology, and UNS metadata — then
generates a single-file HTML dashboard pre-wired with the correct field names,
KPIs, and query patterns.

Once the dashboard is generated, **it communicates with AnyLog over plain REST
(HTTP POST)** — no MCP client required at runtime. Users open the HTML file in a
browser and query live data directly, without Claude or any LLM involved.

```
 Generation time (once)          Runtime (ongoing)
 ┌─────────────┐                 ┌──────────────────┐
 │  Claude +   │  MCP discover   │  Browser opens   │
 │  AnyLog MCP │ ─────────────►  │  dashboard.html  │
 │             │  generates HTML │                  │
 └─────────────┘                 │  POST /api/query │
                                 │  → AnyLog node   │
                                 └──────────────────┘
```

See [`prompts/`](prompts/) for the prompt templates and
[`html/`](html/) for the generated dashboard examples.

### 2. Conversational data queries

Keep the MCP client connected to ask natural-language questions about live data:

> *"What is the current state of generator 3?"*
> *"Can you identify any anomalies in the power readings for region 3 around 2pm yesterday?"*
> *"Which monitors had the highest reactive power in the last 6 hours?"*

The LLM translates the question into AnyLog SQL or blockchain queries via MCP,
executes them against the live network, and returns a plain-language answer.
This works alongside the dashboard — the dashboard shows the overview, the MCP
chat handles ad-hoc investigation.

### 3. MCP-backed live dashboard ⚠ experimental

A dashboard that routes every data fetch through the MCP proxy at runtime —
the LLM intermediates each query rather than the browser calling AnyLog directly.

```
Browser  →  POST /api/query  →  anylog_proxy.py (MCP mode)  →  MCP/SSE  →  AnyLog
```

This is technically possible with `anylog_proxy.py` in MCP mode
(`--anylog-url http://HOST:PORT/mcp/sse`) but comes with significant trade-offs
that make it unsuitable for most production use:

| Concern | Detail |
|---|---|
| **Cost** | Every dashboard refresh triggers one or more LLM-mediated MCP calls — billable API usage on every poll cycle |
| **Latency** | MCP calls are serialized (one at a time) and each involves SSE round-trips; a dashboard polling every 30 s across multiple sensors will queue up and fall behind |
| **Proxy dependency** | Requires `anylog_proxy.py` in MCP mode — nginx alone cannot do this |
| **Query discipline** | The dashboard prompt must specify exact, bounded SQL (e.g. `LIMIT`, narrow time windows, `increments()` buckets) otherwise a single poll may pull thousands of rows and hang the worker |

**When it makes sense:**
- Low-frequency dashboards (refresh interval ≥ 5 minutes)
- Small result sets per query (< 500 rows per call)
- Deployments where the MCP endpoint is the only available access path
- Prototyping or demos where cost and latency are not constraints

**If you use this mode**, the dashboard prompt must be explicit:

> *All SQL queries must use `LIMIT`, bounded time windows (`timestamp >= NOW() - N hours`),
> or `increments()` bucketing. Never issue unbounded `SELECT *`. Each poll cycle must
> complete within 30 seconds or the worker will queue and the dashboard will stall.*

For most cases, **use REST mode** (direct or via nginx / `anylog_proxy.py` in REST mode)
for the dashboard runtime, and reserve MCP for generation and conversational queries.

---

## MCP endpoint

Every AnyLog query node exposes an MCP SSE endpoint at:

```
http://HOST:PORT/mcp/sse
```

All MCP clients connect to this URL. The same endpoint is used by `anylog_proxy.py`
in MCP mode (see [`proxy-generic/README.md`](../proxy-generic/README.md)).

---

## Claude Desktop

### 1. Install Claude Desktop

Download from [claude.ai/download](https://claude.ai/download).

### 2. Install mcp-proxy

`mcp-proxy` bridges Claude Desktop (which speaks stdio MCP) to the AnyLog SSE
endpoint.

```bash
pip install --upgrade mcp-proxy

# Get the full path to the installed binary:
# Linux / macOS
which mcp-proxy

# Windows (PowerShell)
(Get-Command mcp-proxy).Source
```

Note the full path — you will need it in the next step.

### 3. Configure Claude Desktop

Open the Claude Desktop configuration file:

| Platform | Path |
|---|---|
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` |

Add an entry under `mcpServers`:

```json
{
  "mcpServers": {
    "anylog": {
      "command": "/path/to/mcp-proxy",
      "args":    ["http://HOST:PORT/mcp/sse"],
      "env":     {},
      "timeout": 30000
    }
  }
}
```

Replace `/path/to/mcp-proxy` with the path from step 2, and `HOST:PORT` with
your AnyLog query node address.

**Windows example:**
```json
{
  "mcpServers": {
    "anylog": {
      "command": "C:\\Users\\you\\AppData\\Local\\Programs\\Python\\Python311\\Scripts\\mcp-proxy.exe",
      "args":    ["http://24.5.219.50:32349/mcp/sse"],
      "env":     {},
      "timeout": 30000
    }
  }
}
```

**macOS / Linux example:**
```json
{
  "mcpServers": {
    "anylog": {
      "command": "/usr/local/bin/mcp-proxy",
      "args":    ["http://24.5.219.50:32349/mcp/sse"],
      "env":     {},
      "timeout": 30000
    }
  }
}
```

### 4. Restart Claude Desktop

Quit and reopen Claude Desktop. You should see the AnyLog MCP tools available
in the tool selector (hammer icon). If the connection fails, check that the
query node is reachable from your machine:

```bash
curl -X POST http://HOST:PORT \
  -H "Content-Type: application/json" \
  -d '{"User-Agent": "AnyLog/1.23", "command": "get status where format=json"}'
```

### 5. Connect multiple nodes

Add one entry per node under `mcpServers` — each gets its own key:

```json
{
  "mcpServers": {
    "anylog-smart-city": {
      "command": "/path/to/mcp-proxy",
      "args":    ["http://24.5.219.50:32349/mcp/sse"],
      "timeout": 30000
    },
    "anylog-timbergrove": {
      "command": "/path/to/mcp-proxy",
      "args":    ["http://66.175.217.145:32349/mcp/sse"],
      "timeout": 30000
    }
  }
}
```

---

## Other MCP clients

The same `mcp-proxy` pattern works with any MCP-compatible client. Configuration
format varies by client — update this section as new clients are validated.

| Client | Status | Notes |
|---|---|---|
| Claude Desktop | ✅ Supported | See above |
| Cursor | 🔜 Planned | |
| Continue.dev | 🔜 Planned | |
| Other | — | Any client that supports stdio MCP + `mcp-proxy` should work |

---

## Generating a dashboard via MCP (quick start)

Once Claude Desktop is connected:

1. Open a new conversation
2. Paste a prompt from [`prompts/`](../prompts/) with your parameters filled in:
   ```
   DATA_TYPE      = "Power Plant"
   QUERY_NODE     = "24.5.219.50:32349"
   DBMS           = "cos"
   TABLE          = "pp_pm"
   UNS_NAMESPACE  = "Smart_City"
   ```
3. Claude discovers the live schema and generates a single `.html` file
4. Save the file into [`html/`](../html/) and open it via either proxy

---

## Troubleshooting

**MCP tools not showing in Claude Desktop**
Restart Claude Desktop after editing the config file. Check the config file is
valid JSON (no trailing commas). Verify the `command` path has no typos and the
binary is executable.

**`connection refused` when Claude tries to use a tool**
The query node is unreachable from your machine. Check the IP, port, and any
firewall rules. Test with the `curl` command above.

**`timeout` errors on large queries**
Increase `"timeout": 30000` (milliseconds) in the config. For complex queries
30 seconds may not be enough — try `60000` or `120000`.

**Tools appear but return errors**
Check that the MCP endpoint URL ends in `/mcp/sse`. Using the bare node URL
(`http://HOST:PORT`) without `/mcp/sse` will connect but all tool calls will fail.