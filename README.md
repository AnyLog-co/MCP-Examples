# AnyLog Dashboard Generation Guide

## Overview

This guide explains how to use an LLM (Claude) connected to the AnyLog MCP server to generate a production-quality HTML dashboard that queries live data from an AnyLog network. Three connection modes are supported:

| Mode | Description | Use When |
|---|---|---|
| **Direct POST** | AnyLog command delivered as JSON in the POST body, called directly from the browser | Node is accessible over HTTP and has CORS enabled, or browser launched with `--disable-web-security` |
| **Flask Proxy** | Browser POSTs to `anylog_proxy.py` which holds the user's mTLS certs and forwards to AnyLog server-side | Node requires HTTPS / mTLS, or browser CORS is not allowed — developer or single-user setup |
| **nginx** | Browser POSTs to nginx which proxies directly to the AnyLog node — TLS and mTLS configured server-side by the admin at deploy time | Production deployments, shared/multi-user access, or domain-based serving |

> **Important on CORS:** Browsers block cross-origin requests that use custom headers unless the server explicitly permits them via CORS. Direct mode only works when the AnyLog node has CORS configured, or the browser is launched with `--disable-web-security`. For all other cases, use the Flask proxy or nginx — they make requests server-side where CORS does not apply.

> **Flask proxy vs nginx:** These are independent alternatives — use one or the other, not both. The Flask proxy is a Python process each user runs locally. nginx is a system-level service the admin deploys once for all users.

Details on using the MCP to generate dashboards and applications can be found in the [documentation](https://github.com/AnyLog-co/documentation/blob/master/dashboard%20generation.md).

---

## File Structure

```
rest-proxy/
├── prompt.md                    ← Sample prompt with configurable parameters
├── anylog_proxy.py              ← Flask-based reverse proxy (handles mTLS) — alternative to nginx
├── anylog-proxy.service         ← Sample service file for anylog_proxy.py 
├── setup_nginx.sh               ← Script to install and configure nginx — alternative to Flask proxy
├── dashboard-node-status.html   ← Sample dashboard for getting node / network status
└── dashboard-power-plant.html   ← Sample dashboard (Smart City Power Plant)
```

---

## Python Requirements (Flask proxy only)

The Flask proxy (`anylog_proxy.py`) requires Python 3 and the following packages. Not needed if using nginx.

```
flask
flask-cors
requests
urllib3
```

Install with:

```bash
pip install flask flask-cors requests urllib3
```

---

## Certificate Setup

AnyLog uses mutual TLS (mTLS) to authenticate both the server node and the connecting client. The node admin must generate **two sets of certificates**: one for the node itself, and one per user or application that will connect to the network.

### What the node admin creates

Using the AnyLog certificate utilities, the node admin generates:

**1. Node certificates** — identify the AnyLog node to connecting clients:
- `server-<node-name>-public-key.crt` — node's public certificate
- `server-<node-name>-private-key.key` — node's private key
- `ca-anylog-public-key.crt` — the Certificate Authority cert that signed the node cert

**2. User / client certificates** — per user or application that will build or run dashboards:
- `client-<username>-public-key.crt` — client's public certificate
- `client-<username>-private-key.key` — client's private key

These files are stored inside the AnyLog node's Docker volume:

```
anylog-[node-type]-anylog/   ← Docker named volume
└── data/
    └── pem/
        ├── server-<node-name>-public-key.crt
        ├── server-<node-name>-private-key.key
        ├── ca-anylog-public-key.crt
        ├── client-<username>-public-key.crt
        └── client-<username>-private-key.key
```

### Who receives the certificates

Certificates are only needed when connecting to an AnyLog node over HTTPS/mTLS. How they are used depends on which proxy option you choose:

**Flask proxy users** — each user runs their own proxy and needs the three files below. The node admin distributes them securely:

| File | Purpose |
|---|---|
| `client-<username>-public-key.crt` | Proves the client's identity to the AnyLog node |
| `client-<username>-private-key.key` | Signs the client's TLS handshake (keep private) |
| `ca-anylog-public-key.crt` | Verifies the AnyLog node's certificate is legitimate |

The user passes them when starting the proxy:

```bash
python anylog_proxy.py \
    --cert   /path/to/client-<username>-public-key.crt \
    --key    /path/to/client-<username>-private-key.key \
    --cacert /path/to/ca-anylog-public-key.crt \
    --node   24.5.219.50:7849
```

Or via the dashboard's **⚙ Settings** drawer when using Proxy mode — cert file paths are sent to the proxy's `/api/configure` endpoint and applied without restarting.

**nginx admin** — the admin configures the same three files once at deploy time via `setup_nginx.sh`. Individual dashboard users never handle certificates — they only need the nginx URL.

---

## Components

### 1. Deploy an AnyLog Node

Follow the standard deployment guide using Docker Compose:
[https://github.com/AnyLog-co/docker-compose](https://github.com/AnyLog-co/docker-compose)

After deployment, note:
- The **REST port** of your query node (e.g. `24.5.219.50:32349` for HTTP, `24.5.219.50:7849` for HTTPS)
- The **database name** and **table name** you want to query
- The **UNS namespace** if your deployment uses the Unified Namespace

---

### 2. Deploy the Proxy

Choose **one** option — Flask proxy and nginx are independent alternatives. Do not use both.

#### Option A — Flask proxy (local or single-user)

Suitable for local development or a per-user setup. Each user runs their own proxy with their own client certificates.

```bash
# Without mTLS (plain HTTP node):
python anylog_proxy.py --node 24.5.219.50:32349

# With mTLS:
python anylog_proxy.py \
    --node   24.5.219.50:7849 \
    --cert   /path/to/client.crt \
    --key    /path/to/client.key \
    --cacert /path/to/ca.crt
```

The proxy starts at `http://localhost:5000`. Open the dashboard, click ⚙ Settings, select **Flask Proxy** mode, and set the Proxy URL to `http://localhost:5000`.

**All CLI options:**

| Flag | Default | Description |
|---|---|---|
| `--node` | *(required)* | AnyLog query node — `host:port` or full URL |
| `--cert` | `$ANYLOG_CERT` | Path to client certificate (.crt) |
| `--key` | `$ANYLOG_KEY` | Path to client private key (.key) |
| `--cacert` | `$ANYLOG_CACERT` | Path to CA certificate — omit to skip server verification |
| `--port` | `5000` | Port for the proxy server |
| `--host` | `127.0.0.1` | Interface to bind — use `0.0.0.0` for all interfaces |
| `--timeout` | `30` | Request timeout in seconds |
| `--dashboard` | `power-plant-dashboard.html` | HTML file served at `GET /dashboard` |

Environment variable equivalents: `ANYLOG_NODE`, `ANYLOG_CERT`, `ANYLOG_KEY`, `ANYLOG_CACERT`, `PROXY_PORT`, `PROXY_HOST`.

#### Option B — nginx (production / shared access)

Suitable for shared or production deployments. nginx serves the dashboard as a static file and proxies `/api/` **directly to the AnyLog node** — no Flask or Python required. TLS termination (browser → nginx) and mTLS (nginx → AnyLog node) are both configured by the admin at deploy time.

```bash
# Basic — HTTP, no mTLS:
sudo bash setup_nginx.sh \
    --node      24.5.219.50:32349 \
    --dashboard /path/to/my-dashboard.html

# With mTLS to the AnyLog node:
sudo bash setup_nginx.sh \
    --node      24.5.219.50:7849 \
    --dashboard /path/to/my-dashboard.html \
    --cert      /etc/anylog/client.crt \
    --key       /etc/anylog/client.key \
    --cacert    /etc/anylog/ca.crt

# With self-signed TLS (browser → nginx):
sudo bash setup_nginx.sh \
    --node      24.5.219.50:7849 \
    --dashboard /path/to/my-dashboard.html \
    --cert      /etc/anylog/client.crt \
    --key       /etc/anylog/client.key \
    --cacert    /etc/anylog/ca.crt \
    --tls \
    --domain    dashboard.example.com
```

**What the script does:**
1. Installs nginx and openssl
2. Copies the dashboard HTML to `/var/www/anylog/`
3. Copies mTLS client certificates to `/etc/nginx/ssl/anylog/` (if provided)
4. Writes an nginx site config that serves the dashboard at `/` and proxies `/api/` directly to the AnyLog node using `proxy_ssl_certificate` directives
5. Adds CORS headers and handles `OPTIONS` preflight requests
6. Optionally generates a self-signed TLS certificate for the browser-facing side

**After setup:**

```
Dashboard:   http(s)://<domain>/
nginx logs:  tail -f /var/log/nginx/anylog_error.log

Service control:
  systemctl reload nginx
  systemctl status nginx

Files installed:
  /var/www/anylog/<dashboard>.html
  /etc/nginx/ssl/anylog/          ← mTLS certs (if provided)
  /etc/nginx/sites-available/anylog-dashboard
```

> **Certificate note for nginx:** The admin provides certificates at deploy time via `--cert/--key/--cacert`. Dashboard users only need to enter the nginx URL — they never handle certificate files directly.

---

### 3. Configure the Dashboard

The dashboard has a **⚙ Settings** drawer (top-right of the header) where all connection settings are configured at runtime without editing the HTML file.

#### Connection mode: Direct POST

Use when the AnyLog node is accessible over HTTP and CORS is enabled on the node (or browser launched with `--disable-web-security`).

| Field | Example |
|---|---|
| Query Node | `24.5.219.50:32349` |
| Database | `cos` |
| Table | `pp_pm` |

> Note: Blockchain commands (`blockchain get uns ...`) cannot be executed in Direct mode from a browser due to CORS. The Data Location panel will display a `curl` equivalent instead.

#### Connection mode: Flask Proxy

Use when the node requires mTLS or HTTPS.

| Field | Example |
|---|---|
| Proxy URL | `http://localhost:5000` |
| AnyLog Node URL | `https://24.5.219.50:7849` |
| Database | `cos` |
| Table | `pp_pm` |
| Client Cert path | `/path/to/client.crt` *(sent to proxy's `/api/configure`)* |
| Private Key path | `/path/to/client.key` |
| CA Cert path | `/path/to/ca.crt` |

#### Connection mode: nginx

Use when nginx is deployed via `setup_nginx.sh`. Certificates and routing are handled server-side by the admin — the browser user only needs the nginx URL.

| Field | Example |
|---|---|
| nginx URL | `http://localhost` or `https://dashboard.example.com` |
| Database | `cos` |
| Table | `pp_pm` |

---

## Running the Sample Dashboard

The sample dashboard (`power-plant-dashboard.html`) demonstrates a Smart City Power Plant monitoring interface. It works against the `cos.pp_pm` table on the `Smart City` AnyLog deployment.

**To run it directly (no proxy):**

```bash
# Open in browser — will use demo data if the node is unreachable
open dashboard-power-plant.html
```

**To run it via the Flask proxy:**

```bash
python anylog_proxy.py --node 24.5.219.50:32349
# then open http://localhost:5000/dashboard
```

**To run it via nginx (after setup_nginx.sh):**

```bash
# Dashboard is already served at:
open http://localhost/
```

The sample dashboard features:
- Live KPI cards (total real power, reactive power, power factor, frequency)
- Phase current breakdown per monitor (A / B / C)
- Real power bar chart (active monitors, sorted descending)
- Power trend line chart with 1H / 6H / 24H time range selector
- Full monitor table with inline power bars and ONLINE / STANDBY status
- Query log panel (all REST calls, duration, rows, status) with max-concurrent throttling
- Node ping every 5 minutes (`get status where format=json`)
- Data Location panel driven by UNS (`blockchain get uns where namespace=Smart_City/Power_Plant bring [*][loc]`)

---

## Generating a Custom Dashboard

Use `prompt.md` as the starting point. Set the five parameters at the top of the file:

```
DATA_TYPE      = "Oil Rig"                  # e.g. "Wind Turbine", "Water Plant", "Oil Rig"
QUERY_NODE     = "10.0.0.1:32349"           # your AnyLog query node IP:Port
DBMS           = "timbergrove_rigs"         # your database name
TABLE          = "rig_data"                 # your primary table (blank = auto-discover)
UNS_NAMESPACE  = "Timbergrove/RIG-TX-001"  # UNS namespace (blank if not using UNS)
```

Then paste the full prompt into Claude (with the AnyLog MCP connected). Claude will:

1. **Discover** — query the live network using MCP tools to find actual column names, data ranges, node topology, and POST body shapes
2. **Build** — generate a single-file HTML dashboard with the correct field names, meaningful KPIs, and all connection modes pre-wired
3. **Deliver** — output a file ready to drop into this directory and serve via either proxy option

The generated dashboard will match the same structure as the sample — same connection modes, query log, ping, and data location panel — but with content derived from your actual data.

---

## REST API Reference

Both the dashboard and any other HTTP client communicate with AnyLog using two POST patterns:

### SQL Queries

```bash
curl -X POST http://<query-node> \
  -H "Content-Type: application/json" \
  -d '{
    "User-Agent":  "AnyLog/1.23",
    "command":     "sql <dbms> format=json SELECT * FROM <table> WHERE timestamp >= NOW() - 1 hours",
    "destination": "network"
  }'
```

`destination: network` causes the query node to fan out the SQL across all operator nodes holding the data and merge the results.

### Blockchain / Status Commands

```bash
curl -X POST http://<query-node> \
  -H "Content-Type: application/json" \
  -d '{
    "User-Agent": "AnyLog/1.23",
    "command":    "blockchain get uns where namespace=Smart_City/Power_Plant bring [*][loc]"
  }'
```

No `destination` key — these commands run locally on the query node against the blockchain ledger.

### Via the Flask Proxy

```bash
curl -X POST http://localhost:5000/api/query \
  -H "Content-Type: application/json" \
  -d '{
    "url":         "https://<node>:7849",
    "User-Agent":  "AnyLog/1.23",
    "command":     "sql cos format=json SELECT ...",
    "destination": "network"
  }'
```

The proxy extracts `url` (not forwarded to AnyLog) and forwards all other keys as HTTP headers to the node, applying mTLS certificates server-side.

### Hot-reload proxy certificates

```bash
curl -X POST http://localhost:5000/api/configure \
  -H "Content-Type: application/json" \
  -d '{
    "cert":   "/path/to/client.crt",
    "key":    "/path/to/client.key",
    "cacert": "/path/to/ca.crt"
  }'
```

### Proxy health check

```bash
curl http://localhost:5000/api/health
```

---

## Troubleshooting

**`Failed to fetch` in the browser (Direct mode)**

The browser is blocking the request due to CORS. The AnyLog node does not respond to `OPTIONS` preflight requests. Switch to **Proxy** or **nginx** mode, or launch Chrome with `--disable-web-security --user-data-dir=/tmp/dev`.

**Data Location panel shows CORS warning (Direct mode)**

Expected — `blockchain get uns ...` cannot run from a browser in Direct mode. The panel shows the equivalent `curl` command. Switch to Proxy or nginx mode to enable it.

**Proxy shows `SSL/TLS error`**

The client certificate is incorrect or the CA cert does not match the node's certificate. Verify the three cert files were issued by the same CA as the node. Check with:
```bash
openssl verify -CAfile ca-anylog-public-key.crt client-<username>-public-key.crt
```

**`Connection error` from proxy**

The proxy cannot reach the AnyLog node. Check the `--node` value, firewall rules, and that the node's REST port is open. Test directly:
```bash
curl -X GET http://<node>:<rest-port> -H "command: get status"
```

**Dashboard shows demo data**

The dashboard fell back to demo data because the live query returned no results or failed. Check the **QUERIES** log panel (click `QUERIES` in the header) to see the exact error on each call.

**nginx `502 Bad Gateway`**

nginx cannot reach the AnyLog node. Check the node URL configured in `setup_nginx.sh` (`--node`), firewall rules, and that the node's REST port is open. Check the nginx error log:
```bash
tail -f /var/log/nginx/anylog_error.log
nginx -t   # validate config
```