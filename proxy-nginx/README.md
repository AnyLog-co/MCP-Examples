# AnyLog nginx Proxy — Quick Start

Runs an nginx reverse proxy in Docker so the dashboard can reach an AnyLog node
without CORS issues. The browser talks to nginx on localhost; nginx forwards to
the AnyLog query node.

```
Browser  →  http://localhost/api/query  →  nginx (Docker)  →  AnyLog node
```

---

## Files

```
nginx/
├── docker-compose.yaml        ← Docker service definition
├── nginx.conf                 ← nginx configuration
└── html/
    └── rig_data.html          ← dashboard (or any other HTML file)
```

---

## Setup

**1. Edit `nginx.conf`** — set your AnyLog node address:
```nginx
proxy_pass http://<your-node-ip>:<port>/;
```

**2. Drop your dashboard** into the `html/` folder.

**3. Start:**
```powershell
docker compose up -d
```

**4. Open the dashboard** at `http://localhost`

**5. In the dashboard control bar:**
- Mode → `nginx`
- nginx URL → `http://localhost`
- Click ▶ Start

---

## Useful commands

```powershell
# View logs
docker compose logs -f nginx

# Reload config after editing nginx.conf (no restart needed)
docker compose exec nginx nginx -s reload

# Stop
docker compose down
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `[emerg] "worker_processes" directive is not allowed here` | `worker_processes` inside `events {}` | Move it to top of file, before `events {}` |
| `502 Bad Gateway` | nginx can't reach the AnyLog node | Check IP/port in `proxy_pass`; verify firewall |
| AnyLog node is on the same Windows host | `localhost` inside Docker ≠ Windows host | Use `host.docker.internal` in `proxy_pass` and add `extra_hosts` in `docker-compose.yaml` (see below) |
| Port 80 already in use | IIS or another service | Change `"80:80"` to `"8080:80"` and use `http://localhost:8080` |

**If AnyLog runs on the same Windows machine:**
```yaml
# docker-compose.yaml
services:
  nginx:
    extra_hosts:
      - "host.docker.internal:host-gateway"
```
```nginx
# nginx.conf
proxy_pass http://host.docker.internal:<port>/;
```