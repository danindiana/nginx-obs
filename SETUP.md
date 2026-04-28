# NGINX Observatory — Setup Guide

This guide covers manual installation on any Linux host running nginx.
For an automated install, use `setup.sh` instead.

## Prerequisites

Verify nginx has stub_status compiled in:

```bash
nginx -V 2>&1 | grep -o with-http_stub_status_module
```

Should print: `with-http_stub_status_module`

All standard Debian/Ubuntu/RHEL nginx packages include it.
If your build lacks it, install `nginx-extras` (Debian) or rebuild from source.

Verify Python 3 and venv:

```bash
python3 --version      # 3.9+
python3 -m venv --help
```

## Step 1 — Copy the app

```bash
sudo mkdir -p /opt/nginx-obs
sudo cp app.py /opt/nginx-obs/
sudo chown -R www-data:www-data /opt/nginx-obs
```

## Step 2 — Create the Python venv

```bash
python3 -m venv /opt/nginx-obs/venv
/opt/nginx-obs/venv/bin/pip install flask
```

## Step 3 — Add nginx locations

Edit your server block (usually `/etc/nginx/sites-enabled/default` or
`/etc/nginx/conf.d/default.conf`) and add inside `server { ... }`:

```nginx
location /nginx_status {
    stub_status on;
    allow 127.0.0.1;
    allow ::1;
    allow 192.168.1.0/24;   # replace with your LAN CIDR
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

Test and reload:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

Verify stub_status is reachable:

```bash
curl http://127.0.0.1/nginx_status
# Active connections: 3
# server accepts handled requests
#  10 10 42
# Reading: 0 Writing: 1 Waiting: 2
```

## Step 4 — Install the systemd service

```bash
sudo cp nginx-obs.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nginx-obs
sudo systemctl status nginx-obs
```

## Step 5 — Firewall (ufw)

Open port 8081 for LAN-only direct access (the nginx proxy on :80 is the
primary path, but this allows curl/debugging from the LAN):

```bash
sudo ufw allow from 192.168.1.0/24 to any port 8081 proto tcp comment 'nginx-obs LAN'
```

## Step 6 — Verify

```bash
# Direct Flask endpoint
curl -s http://127.0.0.1:8081/ | head -5

# Through nginx proxy
curl -sI http://localhost/nginx-obs/

# SSE stream (should emit JSON every 2s, Ctrl-C to stop)
curl -sN http://127.0.0.1:8081/api/metrics
```

Open **http://\<server-ip\>/nginx-obs/** in a browser.

## Changing the port

Edit the top of `app.py`:

```python
BIND_PORT = 8082   # or whatever
```

Update the nginx `proxy_pass` line to match, then:

```bash
sudo systemctl restart nginx-obs
sudo systemctl reload nginx
```

## Changing the log file path

If nginx logs to a non-standard location:

```python
ACCESS_LOG = "/var/log/nginx/my-site.access.log"
```

Restart the service after any `app.py` change:

```bash
sudo systemctl restart nginx-obs
```

## Permissions troubleshooting

The service runs as `www-data`. That user must be able to read the access log:

```bash
ls -lh /var/log/nginx/access.log
# -rw-r----- 1 www-data adm 42K ...
```

If it's owned by root, either change the service `User=` to root (not ideal)
or add `www-data` to the owning group:

```bash
sudo usermod -aG adm www-data
sudo systemctl restart nginx-obs
```

## Uninstall

```bash
sudo systemctl disable --now nginx-obs
sudo rm /etc/systemd/system/nginx-obs.service
sudo systemctl daemon-reload
sudo rm -rf /opt/nginx-obs
# Remove the nginx location blocks you added in Step 3
sudo systemctl reload nginx
```
