# Connecting Claude to AnyLog via MCP
 
AnyLog exposes a Model Context Protocol (MCP) server that Claude can connect to
directly. This gives Claude live access to your network's schema, data, and node
topology — without you having to write any SQL.
 
---
 
## Table of Contents
 
- [Three Ways to Use MCP](#three-ways-to-use-mcp)
- [MCP Endpoint](#mcp-endpoint)
- [Supported MCP Connectors](#supported-mcp-connectors)
- [Setup](#setup)
  - [Claude Desktop](#claude-desktop)
  - [Flask Proxy in MCP Mode](#flask-proxy-in-mcp-mode-example-3)
  - [Connecting Multiple Nodes](#connecting-multiple-nodes)
  - [Base44](#base44)

--- 
## Three Ways to Use MCP

Claude can connect to AnyLog via the Model Context Protocol (MCP) to discover live
schema, query data conversationally, and generate dashboards. There are three ways
users can communicate between an MCP client (e.g., Claude Desktop) and AnyLog, and
all three follow the same steps to connect an MCP client, either directly or via
proxy, regardless of which data-gathering option is chosen.

The key difference lies in the prompt content — it determines which connection logic
applies when establishing a backend connection to AnyLog.

* [Generating Dashboards](./README.md#example-1--generate-a-dashboard-recommended) —
  Claude connects to MCP **once** to discover schema, sample data, and node topology,
  then generates a single `.html` file wired to the correct fields and query patterns.
* [Conversational Data Queries](./README.md#example-2--conversational-data-queries) —
  Keep the MCP client connected to ask natural-language questions about live data.
* [MCP-backed Live Dashboards](./README.md#example-3--mcp-backed-live-dashboard--experimental) —
  A dashboard that routes **every data fetch** through the MCP proxy at runtime.

A deep dive into each example can be found in the [Connection Modes](README.md#connection-modes)
and [Using Claude + MCP](README.md#using-claude--mcp) sections of the _README_.

> **Note:** Conversational querying (Example 2) requires no additional setup beyond
> Example 1 — once the MCP client is connected, users can query AnyLog directly from
> the chat interface (e.g., Claude Desktop) without any further configuration.


### MCP endpoint

Every AnyLog query node exposes an MCP SSE endpoint at:

```
http://HOST:PORT/mcp/sse
```

This is the URL used by `mcp-proxy` (for Claude Desktop) and by [`anylog_proxy.py`](./proxy-generic/anylog_proxy.py)
in MCP mode.

### Supported MCP Connectors 

|              Client               |    Status    |
|:---------------------------------:|:------------:|
| [Claude Desktop](#Claude-Desktop) | ✅ Supported  |
|        [Based44](#Base44)         | ✅ Supported  |
|              Cursor               |  🔜 Planned  |
|           Continue.dev            |  🔜 Planned  |
|          Claude.ai (web)          | ✅ Supported  |

---

## MCP Connectors Setup

### General Requirements

1. `mcp-proxy` bridges Claude Desktop (stdio MCP) to the AnyLog SSE endpoint:

```bash
pip install --upgrade mcp-proxy
```

2. Locate `mcp-proxy` location path
```shell
# Mac OSX and Liinux 
which mcp-proxy

# Windows (Powershell)
(Get-Command mcp-proxy).Source
```

| Operating System |           Path           | 
| :---: |:------------------------:| 
| Mac OSX| /usr/local/bin/mcp-proxy | 
| Linux | /usr/local/bin/mcp-proxy | 
| Windows | C:\Users\USERNAME\AppData\Local\Programs\Python\Python311\Scripts\mcp-proxy.exe |


### Claude Desktop 

1. Install Claude Desktop - Download from [claude.ai/download](https://claude.ai/download).
2. [Install mcp-proxy](#general-requirements)

#### Configure Claude Desktop

1. Locate & Open the config file


| Platform | Path |
|---|---|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` |

2. Add an entry under `mcpServers`:

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

 
> **Complete File Example**:
> * macOS / Linux example
> ```json
> {
>  "mcpServers": {
>    "anylog": {
>      "command": "/usr/local/bin/mcp-proxy",
>      "args":    ["http://66.175.217.145:32349/mcp/sse"],
>      "env":     {},
>      "timeout": 30000
>    }
>  }
> }
> ```
>
> * Windows example
> ```json
> {
>  "mcpServers": {
>    "anylog": {
>      "command": "C:\\Users\\you\\AppData\\Local\\Programs\\Python\\Python311\\Scripts\\mcp-proxy.exe",
>      "args":    ["http://66.175.217.145:32349/mcp/sse"],
>      "env":     {},
>      "timeout": 30000
>    }
>  }
> }
> ```

⚠️ **Note the `/mcp/sse` suffix.** Using the bare node URL (`http://HOST:PORT`) will connect but all tool calls will fail.

4. Restart Claude Desktop --  Quit and reopen. You should see the AnyLog MCP tools in the tool selector (🔨 icon).

5. Test the connection

In a new Claude conversation, try:

```
"What databases are available on this AnyLog network?"
```

Claude should call `listNetworkDatabases` and return a live list.



#### Connecting multiple nodes

Add one entry per node — each gets its own key in `mcpServers` and may share mcp-proxy path:

```json
{
  "mcpServers": {
    "anylog-wind": {
      "command": "/path/to/mcp-proxy",
      "args":    ["http://66.175.217.145:32349/mcp/sse"],
      "timeout": 30000
    },
    "anylog-power-plant": {
      "command": "/path/to/mcp-proxy",
      "args":    ["http://24.5.219.50:32349/mcp/sse"],
      "timeout": 30000
    }
  }
}
```

---

### Base44

[Base44](https://base44.com) is an AI-native app builder. Rather than generating a static HTML file, Base44 produces a 
full hosted application with a separate backend service layer and frontend components. The AnyLog integration uses the
MCP once at generation time, then the running app talks to AnyLog directly over REST — same principle as use case 1 
above, but the output is a Base44 app instead of a standalone HTML file.

**How it works**

The workflow has two phases, each producing a prompt that gets run inside Base44:

```
 Phase 1 — Backend                    Phase 2 — Frontend
 ┌──────────────────────┐             ┌──────────────────────┐
 │ Claude + AnyLog MCP  │             │ Claude (no MCP)      │
 │                      │             │                      │
 │ Discover schema,     │             │ Given: architecture  │
 │ UNS, topology        │             │ + backend API from   │
 │                      │             │ phase 1              │
 │ Generate:            │             │                      │
 │ Backend prompt →     │             │ Generate:            │
 │ run on Base44        │             │ Frontend prompt →    │
 └──────────────────────┘             │ run on Base44        │
          ↓                           └──────────────────────┘
   Base44 backend                              ↓
   (AnyLog REST calls)               Base44 frontend
                                     (calls backend)
```

**Phase 1 — Claude + MCP generates the backend prompt:** Connect Claude Desktop to AnyLog MCP. Claude discovers the 
live schema, UNS metadata, and cluster topology, then generates a prompt you paste into Base44 to create the backend 
service. The backend functions POST to AnyLog using the standard REST format (see below).

**Phase 2 — Claude generates the frontend prompt:** Without MCP, describe your desired UI architecture to Claude along 
with the backend API created in phase 1. Claude generates a second prompt you paste into Base44 to build the frontend 
components that call the backend.

### AnyLog connection from Base44 backend

The Base44 backend functions POST to the AnyLog query node directly — no proxy,
no nginx. Base44 runs server-side so CORS is not an issue.

**SQL queries** — require `destination: "network"`:
```json
POST http://{node_ip}:{port}
Headers: { "User-Agent": "AnyLog/1.23", "Content-Type": "application/json" }
Body: {
  "command":     "sql {dbms} format=json:list and stat=false  {SQL}",
  "destination": "network"
}
```

**Blockchain / node commands** — no `destination`:
```json
POST http://{node_ip}:{port}
Headers: { "User-Agent": "AnyLog/1.23", "Content-Type": "application/json" }
Body: {
  "command": "blockchain get uns where namespace = {namespace}"
}
```

Response is always a **flat JSON array** — no nested result object. Parse with:
```js
const rows = Array.isArray(response) ? response : [];
```

**Generating a Base44 app via MCP**

Use the prompt template at [`prompts/base44.md`](prompts/base44.md).

1. Connect Claude Desktop to AnyLog MCP (see [Claude Desktop](#claude-desktop) above)
2. Fill in the parameters at the top of `base44.md`
3. Paste into Claude — it will produce **two outputs**:
   - **Backend prompt** → paste into Base44 to create the backend service
   - **Frontend prompt** → paste into Base44 to create the frontend
4. Run them in order (backend first, then frontend)

See [`html/base44_sample_backend.html`](html/base44_sample_backend.html) for an
example of what the Base44 backend layer discovers and exposes.
