# Troubleshooting

Common errors and fixes for AnyLog dashboards, proxies, and MCP connections.

---

## Browser / Dashboard

### `Failed to fetch` or CORS error (Direct mode)

The browser is blocking the request before it reaches the AnyLog node. This happens
for one of two reasons:

**a) The node is not returning CORS headers**

The AnyLog node is not responding with `Access-Control-Allow-Origin: *`.

**Fix:** Switch Mode → `nginx` or `proxy` in the dashboard config bar, or launch
Chrome with web security disabled for local dev:

```bash
# macOS
open -n -a "Google Chrome" --args --disable-web-security --user-data-dir=/tmp/dev

# Linux
google-chrome --disable-web-security --user-data-dir=/tmp/dev

# Windows
chrome.exe --disable-web-security --user-data-dir=C:\tmp\dev
```

**b) The browser is sending a CORS preflight (`OPTIONS`) the node doesn't answer**

Browsers send a preflight before any request that uses a non-simple header or
method. If you see a message like:

```
Response to preflight request doesn't pass access control check:
No 'Access-Control-Allow-Origin' header is present on the requested resource.
```

This is why AnyLog uses `AnyLog-Agent` instead of `User-Agent`:

- `User-Agent` is a **browser-reserved header** — `fetch()` cannot set it, and
  its presence in a request triggers a preflight that AnyLog nodes are not
  configured to answer.
- `AnyLog-Agent` is a **custom header** that both sides control. The node can
  whitelist it explicitly:
  ```
  Access-Control-Allow-Headers: AnyLog-Agent, Content-Type
  ```

If you are calling the AnyLog REST API directly from a browser (Direct mode), ensure
the node is configured to respond with the required CORS headers, or route through
the nginx or Flask proxy instead.

### CORS banner persists after switching to nginx / proxy mode

The banner clears on the next successful fetch — click ↻ Refresh. If it persists,
the proxy itself cannot reach the AnyLog node. Check proxy logs:

```bash
# Flask proxy
docker logs rest-proxy

# nginx
docker logs rest-proxy-nginx
```

### SQL queries return empty, but node status works

Missing `"destination": "network"` in the SQL body. Without it the query runs only
on the query node, which holds no operator data.

```json
// ❌ Wrong — query node only
{"command": "sql mydb format=json:list and stat=false SELECT ..."}

// ✅ Correct — distributed to operator nodes
{"command": "sql mydb format=json:list and stat=false SELECT ...", "destination": "network"}
```

> **Note:** `AnyLog-Agent` belongs in the HTTP **header**, not the JSON body.
> See the [REST API Reference](./README.md#anylog-rest-api-reference) for correct curl usage.

The Flask proxy in REST mode adds `destination: network` automatically.

### Dashboard shows stale data after a schema change

Flush the proxy result cache:

```bash
curl -X POST http://localhost:8080/api/cache/clear
```

---

## nginx Proxy

### `502 Bad Gateway`

nginx cannot reach the AnyLog node. Most common on Windows when AnyLog runs on the
Docker host itself.

```yaml
# docker-compose.yaml — add under the nginx service
extra_hosts:
  - "host.docker.internal:host-gateway"
```

```nginx
# nginx.conf — use the host alias instead of an IP
proxy_pass http://host.docker.internal:PORT/;
```

### `connection refused` from nginx container

The AnyLog node is not listening on the configured port. Verify with:

```bash
curl -X POST http://HOST:PORT \
  -H "Content-Type: application/json" \
  -H "AnyLog-Agent: AnyLog/1.23" \
  -d '{"command": "get status where format=json"}'
```

---

## Flask Proxy

### `Is a directory` error on startup

Docker auto-created a config file as a directory before it existed. Happens when
a volume mount target doesn't exist yet.

```bash
docker compose down
# Remove the incorrectly created directory
rm -rf ./path/to/offending-dir
# Recreate as a file if needed, then:
docker compose up -d --build
```

### `pip install requirements.txt` fails — "package literally named requirements.txt"

Missing the `-r` flag. The Dockerfile line should be:

```dockerfile
RUN pip install -r requirements.txt
# not: pip install requirements.txt
# not: pip install --upgrade requirements.txt
```

### Proxy starts but returns `404` for `/api/uns/databases`

The proxy is running in REST mode, not MCP mode. MCP-only endpoints return 404 in
REST mode. Check the URL in `ANYLOG_NODE_URL` — it must end in `/mcp/sse`:

```bash
# ❌ REST mode — no MCP endpoints
ANYLOG_NODE_URL=http://66.175.217.145:32349

# ✅ MCP mode — /api/uns/* and other MCP-only endpoints available
ANYLOG_NODE_URL=http://66.175.217.145:32349/mcp/sse
```

### Requests queue up / dashboard stalls in MCP mode

The MCP worker serializes all calls — one at a time. If the dashboard polls too
frequently across many sensors the queue grows faster than it drains.

Check queue depth:

```bash
curl http://localhost:8080/api/worker/status
```

**Fixes:**
- Increase refresh interval to ≥ 5 minutes
- Increase call delay: `--call-delay 2.0`
- Add LIMIT and bounded time windows to all SQL (never `SELECT *`)

---

## MCP Connection

### MCP tools not showing in Claude Desktop

1. Restart Claude Desktop after editing the config file
2. Verify the config is valid JSON (no trailing commas)
3. Confirm the `command` path is correct and the binary is executable:

```bash
which mcp-proxy      # macOS / Linux
mcp-proxy --version  # should print a version
```

### `connection refused` when Claude uses an MCP tool

The AnyLog query node is unreachable. Test directly:

```bash
curl -X POST http://HOST:PORT \
  -H "Content-Type: application/json" \
  -H "AnyLog-Agent: AnyLog/1.23" \
  -d '{"command": "get status where format=json"}'
```

### `timeout` errors on large MCP queries

Increase the timeout in the Claude Desktop config:

```json
{
  "mcpServers": {
    "anylog": {
      "command": "/path/to/mcp-proxy",
      "args":    ["http://HOST:PORT/mcp/sse"],
      "timeout": 60000
    }
  }
}
```

Default is 30 000 ms (30 s). Try `60000` or `120000` for complex distributed queries.

### MCP tools appear but return errors

Confirm the MCP endpoint URL ends in `/mcp/sse`:

```
http://HOST:PORT/mcp/sse    ✅ correct
http://HOST:PORT            ❌ wrong — connects but all tool calls fail
```

---

## Example 3 (MCP-backed live dashboard)

### Dashboard shows spinner indefinitely

The proxy worker queue is full or the MCP call timed out. Open a new tab and check:

```bash
# Queue depth
curl http://localhost:8080/api/worker/status

# Live call log
open http://localhost:8080/debug
```

If `queue_depth` is growing, the refresh interval is too short for the MCP
serialization latency. Set refresh to 5 or 10 minutes.

### Error log shows `curl` commands — how do I use them?

The dashboard generates a ready-to-run `curl` for every failed query. Paste it
directly into a terminal to reproduce the request outside the browser:

```bash
curl -X POST http://localhost:8080/api/query \
  -H "Content-Type: application/json" \
  -d '{"dbms":"wind_turbine","sql":"SELECT turbine_id, power_avg FROM power_output WHERE timestamp >= NOW() - 1 hour LIMIT 500"}'
```

If this works in the terminal but fails in the dashboard, the issue is likely a
browser CORS policy or a mismatched proxy URL in the config bar.