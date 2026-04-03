# proxy-nginx — AnyLog nginx Proxy

Runs an nginx reverse proxy in Docker. The browser talks to nginx on localhost;
nginx substitutes the AnyLog node URL from an environment variable at startup
and forwards `/api/query` requests to the node.

```
Browser  →  http://localhost/api/query  →  nginx (Docker)  →  AnyLog node
Browser  →  http://localhost/<file>.html  →  nginx (Docker) serves ../html/
```

---

## Files

```
proxy-nginx/
├── docker-compose.yaml   ← service definition + ANYLOG_NODE_URL env var
├── nginx.conf            ← nginx config template (uses ${ANYLOG_NODE_URL})
└── README.md             ← this file

../html/                  ← dashboards served by nginx (shared with proxy-generic)
    ├── dashboard-node-status.html
    ├── dashboard-power-plant.html
    └── rig_data.html
```

The `html/` directory lives one level up and is shared across both proxies —
add new dashboards there once and they are immediately available from both.

---

## Setup

**1. Set your AnyLog node URL** in `docker-compose.yaml`:
```yaml
environment:
  - ANYLOG_NODE_URL=http://HOST:PORT/
```

**2. Start:**
```bash
docker compose up -d --build
docker logs -f proxy-nginx-nginx-1
```

You should see:
```
Configuration complete; ready for start up
```

**3. Open a dashboard** at `http://localhost/<filename>.html`

**4. In the dashboard config bar:**
- Mode → `nginx`
- nginx URL → `http://localhost`
- Click ↻ Refresh or ▶ Start

---

## How the env var substitution works

`nginx.conf` contains `${ANYLOG_NODE_URL}` in the `proxy_pass` directive.
The Docker `command` runs `envsubst` at container startup to substitute the
variable and write the final `nginx.conf` before nginx starts:

```yaml
command: >
  /bin/sh -c "envsubst '$$ANYLOG_NODE_URL'
  < /etc/nginx/nginx.conf.src
  > /etc/nginx/nginx.conf
  && nginx -g 'daemon off;'"
```

The double `$$` is intentional — docker-compose consumes one `$`, leaving
`$ANYLOG_NODE_URL` for the shell to pass to `envsubst`.

`envsubst` is given the explicit variable list `'$$ANYLOG_NODE_URL'` so it
only substitutes that one variable and leaves nginx's own `$variables`
(`$proxy_host`, `$request_method`, `$uri`, etc.) untouched.

---

## Useful commands

```bash
# View logs
docker compose logs -f

# Reload config after editing nginx.conf (no restart needed)
docker compose exec nginx nginx -s reload

# Stop
docker compose down
```

---

## Adding dashboards

Drop any `.html` file into `../html/`. No nginx restart is needed — the
directory is bind-mounted read-only and nginx serves files from it directly.

Access at: `http://localhost/<filename>.html`

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Is a directory` on startup | Docker auto-created `nginx.conf` as a directory | `docker compose down && rm -rf nginx.conf` then recreate the file |
| `unknown variable` nginx error | `envsubst` not substituting the variable | Check `docker-compose.yaml` has the correct `environment:` key and `command:` uses `$$ANYLOG_NODE_URL` |
| `502 Bad Gateway` | nginx can't reach the AnyLog node | Check `ANYLOG_NODE_URL`; verify port is open |
| AnyLog on same Windows host | `localhost` inside Docker ≠ Windows localhost | Use `host.docker.internal` (see below) |
| Port 80 already in use | Another service on port 80 | Change `"80:80"` → `"8080:80"` in `docker-compose.yaml` |

**If AnyLog runs on the same Windows machine:**

```yaml
# docker-compose.yaml — add under the nginx service:
extra_hosts:
  - "host.docker.internal:host-gateway"
environment:
  - ANYLOG_NODE_URL=http://host.docker.internal:PORT/
```