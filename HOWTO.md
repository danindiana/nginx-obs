# NGINX Observatory — How-To Guide

## Accessing the dashboard

| URL | What you get |
|-----|-------------|
| `http://<host>/nginx-obs/` | Full GUI dashboard (via nginx proxy, port 80) |
| `http://<host>:8081/` | Same app, direct port (LAN only if UFW is set) |
| `http://<host>/nginx_status` | Raw stub_status text (machine-readable) |

The dashboard auto-refreshes via SSE — no manual reload needed.

---

## Reading the stat cards

### Active Connections
Total open connections at this instant, from `stub_status`.
Includes idle keepalive connections. On a quiet server this is often 1–3.

### Requests / sec
The delta in the `requests` counter from stub_status between two 1-second polls.
Spikes indicate bursts of traffic. A sustained value > worker_processes × connections
may indicate queuing.

### Error Rate (5 min)
`(4xx + 5xx) / total` over the last 5 minutes, derived from the access log.
0% is normal for a healthy server. A spike here warrants checking the log panel.

### Waiting (keepalive idle)
Connections open but not actively reading or writing — clients holding keepalive
slots. `R:` = reading request body, `W:` = writing response.

---

## Reading the charts

Both line charts show a **2-minute sliding window** (120 samples at 1 s each).
The x-axis is time (oldest left, newest right). Hovering shows the value at
any sample point.

- **Green line** = active connections
- **Cyan line** = requests per second

---

## Reading the status code donut

Counts are from the **last 5 minutes** of access log entries, live-updated.
The donut redraws on each SSE push (every 2 s).

- **Green** = 2xx (success)
- **Cyan** = 3xx (redirect)
- **Yellow** = 4xx (client error — 404, 403, 401…)
- **Red** = 5xx (server error)

---

## Reading the top paths / IPs tables

These are **cumulative since the service last started**, seeded from `access.log.1`
and `access.log` on startup. The bar width is relative to the top entry.

To reset counts: `sudo systemctl restart nginx-obs`

To view only a specific vhost's traffic, point `ACCESS_LOG` at that vhost's log file.

---

## Reading the live log panel

Shows the last 25 access log lines, updated on each SSE push.
Colors:

| Color | Meaning |
|-------|---------|
| Cyan | Source IP |
| Green | 2xx status |
| Yellow | 3xx status |
| Orange | 4xx status |
| Red | 5xx status |

---

## Monitoring multiple nginx instances

Run one `nginx-obs` process per host. Use different ports (`BIND_PORT`) and
add a separate nginx location block per instance if you want them all under one
nginx reverse proxy.

---

## Using the raw stub_status endpoint

`http://<host>/nginx_status` returns:

```
Active connections: 3
server accepts handled requests
 191 191 1926
Reading: 0 Writing: 1 Waiting: 2
```

Fields:
- **accepts** — total accepted connections since nginx start
- **handled** — connections that completed (should equal accepts unless worker limit hit)
- **requests** — total HTTP requests served
- **Reading** — currently reading request headers
- **Writing** — currently writing response
- **Waiting** — keepalive idle

Use this endpoint for scripting, Prometheus scrapers, or any tool that needs
nginx metrics without a browser.

---

## Checking service health

```bash
# Is it running?
sudo systemctl status nginx-obs

# Recent logs
sudo journalctl -u nginx-obs -n 50

# Is the SSE stream alive?
curl -sN http://127.0.0.1:8081/api/metrics | head -2

# Quick reload after editing app.py
sudo systemctl restart nginx-obs
```

---

## Integrating with Prometheus / Grafana

The `/nginx_status` endpoint is compatible with the
[nginx_stub_status_exporter](https://github.com/nginxinc/nginx-prometheus-exporter).
nginx-obs and the Prometheus exporter can both read the same `/nginx_status`
endpoint simultaneously.

```bash
# Example: nginx-prometheus-exporter sidecar
nginx-prometheus-exporter -nginx.scrape-uri=http://127.0.0.1/nginx_status
```

---

## Extending the dashboard

The SSE payload (`/api/metrics`) is plain JSON — any client can consume it.
Example fields emitted:

```json
{
  "nginx":        { "version": "1.26.3", "pid": "707", "uptime": "65h 04m", "workers": "4" },
  "active":       1,
  "reading":      0,
  "writing":      1,
  "waiting":      0,
  "rps_now":      3,
  "error_rate":   0.0,
  "conn_active":  [1, 1, 2, ...],
  "rps":          [0, 1, 3, ...],
  "status":       { "2": 1764, "3": 0, "4": 2, "5": 0 },
  "top_paths":    [["/", 880], ["/favicon.ico", 873], ...],
  "top_ips":      [["192.168.1.135", 1746], ...],
  "logs":         ["192.168.1.135 - - [28/Apr/...] \"GET / HTTP/1.1\" 200 ..."]
}
```

Connect a second `EventSource` from any other page on your LAN to consume
the same stream without any changes to the server.
