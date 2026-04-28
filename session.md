# nginx-obs session — 2026-04-28_131144

## Goal
Build a web-based NGINX observability / telemetry dashboard for the rpi4 nginx instance,
inspired by F5 NGINX One Console.

## System state at start
- rpi4: Debian Trixie, IP 192.168.1.165
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
- SSE generator (`/api/metrics`): reads shared state under lock, emits JSON every 2 s

**Metrics emitted:**
- Connection time-series (active, reading, writing, waiting) — 120-sample window
- RPS delta time-series — 120-sample window
- Status code distribution (2xx/3xx/4xx/5xx) — 5-minute rolling window
- Top 10 paths and top 10 client IPs (cumulative since startup)
- Last 25 access log lines (live)
- nginx metadata: version, master PID, uptime, worker count

### Frontend (embedded in app.py)
Single-page dashboard served from `/`. Uses Chart.js 4.4.3 (CDN) and SSE.

**Sections:**
1. Sticky header — NGINX Observatory branding, live pulse dot, version badge
2. Instance info strip — version, PID, uptime, workers, stub_status status, log status
3. Four stat cards — Active Connections, Requests/sec, Error Rate (5 min), Waiting
4. Two line charts — Active Connections (2 min), Requests/sec (2 min)
5. Status code donut — 2xx/3xx/4xx/5xx with counts
6. Top Paths + Top IPs tables with relative bar indicators
7. Live access log tail with IP/status colorization

### nginx config change: `/etc/nginx/sites-enabled/default`
Added stub_status location (localhost-only):
```nginx
location /nginx_status {
    stub_status on;
    allow 127.0.0.1;
    allow ::1;
    deny all;
}
```

### Systemd service: `/etc/systemd/system/nginx-obs.service`
- Runs as `www-data` (has access to nginx access log)
- ExecStart: `/opt/nginx-obs/venv/bin/python /opt/nginx-obs/app.py`
- Enabled + started at: 2026-04-28 13:19:34 CDT

### venv
`/opt/nginx-obs/venv` — Python 3.13, flask only dependency.

### UFW rule
Port 8081 open for 192.168.1.0/24 (LAN only).

## Access
- Dashboard: http://192.168.1.165:8081/
- SSE endpoint: http://192.168.1.165:8081/api/metrics

## Files
```
/opt/nginx-obs/
  app.py              # Flask app + HTML dashboard (self-contained)
  venv/               # Python venv (flask)
/etc/systemd/system/nginx-obs.service
/etc/nginx/sites-enabled/default  (modified — added stub_status)
```

Local copy: `~/Documents/claude_creations/2026-04-28_131144_nginx-obs/`
