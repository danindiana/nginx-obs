# nginx-obs session — 2026-04-28_131144

## Goal
Build a web-based NGINX observability / telemetry dashboard for the rpi4 nginx instance,
inspired by F5 NGINX One Console. Push to GitHub. Document fully.

## System state at start
- rpi4: Debian Trixie, IP 192.168.1.165, `ssh rpi4` from worlock
- nginx 1.26.3, compiled with `--with-http_stub_status_module`
- Serving NTP monitor at `/` (port 80), access log at `/var/log/nginx/access.log`
- nginx PID 707, 4 worker processes

## What was built

### Backend: `/opt/nginx-obs/app.py`
Python 3 / Flask application running on port 8081.

**Data sources:**
- `http://127.0.0.1/nginx_status` — nginx stub_status, polled every 1 s
- `/var/log/nginx/access.log` — seeded on startup from log + log.1, then `tail -F` live

**Threading model:**
- `_collector` thread: polls stub_status every 1 s, appends to `ts_conn` / `ts_rps` deques (maxlen=120)
- `_log_tailer` thread: `tail -F` loop appending to `status_win`, `path_counts`, `ip_counts`, `recent_log`
- SSE generator (`/api/metrics`): reads shared state under `threading.Lock`, emits JSON every 2 s

**Metrics emitted:**
- Connection time-series (active, reading, writing, waiting) — 120-sample window
- RPS delta time-series — 120-sample window
- Status code distribution (2xx/3xx/4xx/5xx) — 5-minute rolling window
- Top 10 paths and top 10 client IPs (cumulative since startup)
- Last 25 access log lines (live)
- nginx metadata: version, master PID, uptime, worker count

### Frontend (embedded in app.py)
Single-page dashboard served from `/`. Uses Chart.js 4.4.3 (CDN) and SSE.
EventSource URL is relative (`api/metrics`) so it works both at port 8081
and behind the nginx proxy at `/nginx-obs/`.

**Sections:**
1. Sticky header — NGINX Observatory branding, live pulse dot, version badge
2. Instance info strip — version, PID, uptime, workers, stub_status status, log status
3. Four stat cards — Active Connections, Requests/sec, Error Rate (5 min), Waiting
4. Two line charts — Active Connections (2 min), Requests/sec (2 min)
5. Status code donut — 2xx/3xx/4xx/5xx with counts
6. Top Paths + Top IPs tables with relative bar indicators
7. Live access log tail with IP/status colorization

### nginx config: `/etc/nginx/sites-enabled/default`
Two new locations added:

```nginx
location /nginx_status {
    stub_status on;
    allow 127.0.0.1;
    allow ::1;
    allow 192.168.1.0/24;  # added in second session turn
    deny all;
}

location /nginx-obs/ {
    proxy_pass http://127.0.0.1:8081/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 3600s;
}
```

Note: LAN CIDR for `/nginx_status` was added after initial deploy when user
hit 403 trying to browse to the raw stub_status URL from worlock.

### Systemd service: `/etc/systemd/system/nginx-obs.service`
- Runs as `www-data` (has access to nginx access log)
- ExecStart: `/opt/nginx-obs/venv/bin/python /opt/nginx-obs/app.py`
- Enabled + started at: 2026-04-28 13:19:34 CDT

### venv
`/opt/nginx-obs/venv` — Python 3.13, flask only dependency.

### UFW rule
Port 8081 open for 192.168.1.0/24 (LAN only).

## Access
- Dashboard (proxy): http://192.168.1.165/nginx-obs/
- Dashboard (direct): http://192.168.1.165:8081/
- Raw stub_status: http://192.168.1.165/nginx_status

## GitHub repo
https://github.com/danindiana/nginx-obs

## Repo files
```
app.py                          Flask app + HTML dashboard (self-contained)
setup.sh                        Interactive setup wizard
nginx-obs.service               Systemd unit template
README.md                       Project overview + feature list
SETUP.md                        Manual installation guide
HOWTO.md                        Dashboard reading guide + API reference
diagrams/
  01_system_arch.dot/.png       System architecture
  02_data_flow.dot/.png         Metrics data flow pipeline
  03_threading.dot/.png         Threading model + lock flow
  04_dashboard_ui.dot/.png      Dashboard UI component map
  05_deployment.dot/.png        Deployment topology (rpi4 + LAN)
session.md                      This file
```

## On-server files
```
/opt/nginx-obs/app.py
/opt/nginx-obs/venv/
/etc/systemd/system/nginx-obs.service
/etc/nginx/sites-enabled/default  (modified)
```

## Key decisions
- Python/Flask over Go: faster iteration for a UI-heavy app
- SSE over WebSocket: simpler, no special nginx config needed
- Relative EventSource URL: works transparently under nginx proxy or direct port
- `splines=spline` in Graphviz DOT: `ortho` drops edge labels silently

## Issues encountered
- **403 on /nginx_status from LAN**: initial nginx location only allowed `127.0.0.1`;
  added `allow 192.168.1.0/24` after user hit 403 from worlock browser
- **Background task push silently failed**: `git push` inside a background shell runner
  hit an SSL connection timeout (`fatal: unable to access ... SSL connection timeout`);
  the task output showed the commit summary but not the push error, making it appear
  successful. Manual `git push origin main` on the second attempt succeeded.
  Lesson: don't rely on background task output to confirm `git push` — verify with
  `git log --oneline origin/main` afterwards.

## Completed: 2026-04-28
