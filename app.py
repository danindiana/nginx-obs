#!/usr/bin/env python3
"""nginx-obs — NGINX Observability Dashboard for rpi4"""

import re, time, json, subprocess, threading, os
from collections import defaultdict, deque
from urllib.request import urlopen
from flask import Flask, Response, stream_with_context

# ── Config ────────────────────────────────────────────────────────────────────
NGINX_STATUS = "http://127.0.0.1/nginx_status"
ACCESS_LOG   = "/var/log/nginx/access.log"
HISTORY_LEN  = 120
BIND_HOST    = "0.0.0.0"
BIND_PORT    = 8081

# ── Shared state ──────────────────────────────────────────────────────────────
_lock       = threading.Lock()
ts_conn     = deque(maxlen=HISTORY_LEN)   # (active, reading, writing, waiting)
ts_rps      = deque(maxlen=HISTORY_LEN)   # int
status_win  = deque(maxlen=20000)         # (timestamp, class)  class = 2|3|4|5
path_counts = defaultdict(int)
ip_counts   = defaultdict(int)
recent_log  = deque(maxlen=100)
prev_total  = None
nginx_meta  = {'version': '—', 'pid': '—', 'uptime': '—', 'workers': '—'}


def _nginx_version():
    try:
        r = subprocess.run(['/usr/sbin/nginx', '-v'],
                           capture_output=True, text=True, timeout=3)
        m = re.search(r'nginx/(\S+)', r.stderr + r.stdout)
        return m.group(1) if m else '—'
    except Exception:
        return '—'


def _nginx_pid():
    try:
        return open('/run/nginx.pid').read().strip()
    except Exception:
        return '—'


def _nginx_uptime(pid):
    try:
        stat  = open(f'/proc/{pid}/stat').read().split()
        ticks = int(stat[21])
        up    = float(open('/proc/uptime').read().split()[0])
        hz    = os.sysconf('SC_CLK_TCK')
        age   = int(up) - ticks // hz
        h, rem = divmod(age, 3600)
        m, s   = divmod(rem, 60)
        return f"{h}h {m:02d}m {s:02d}s"
    except Exception:
        return '—'


def _worker_count():
    try:
        r = subprocess.run(['pgrep', '-c', '-x', 'nginx'],
                           capture_output=True, text=True)
        return str(max(0, int(r.stdout.strip()) - 1))
    except Exception:
        return '—'


def _fetch_stub():
    try:
        body    = urlopen(NGINX_STATUS, timeout=2).read().decode()
        active  = int(re.search(r'Active connections:\s+(\d+)', body).group(1))
        nums    = re.search(r'(\d+)\s+(\d+)\s+(\d+)', body)
        total   = int(nums.group(3))
        reading = int(re.search(r'Reading:\s+(\d+)', body).group(1))
        writing = int(re.search(r'Writing:\s+(\d+)', body).group(1))
        waiting = int(re.search(r'Waiting:\s+(\d+)', body).group(1))
        return dict(ok=True, active=active, total=total,
                    reading=reading, writing=writing, waiting=waiting)
    except Exception as ex:
        return dict(ok=False, error=str(ex))


_LOG_RE = re.compile(
    r'(\S+) \S+ \S+ \[[^\]]+\] "(?:\S+ )?(\S+)[^"]*" (\d{3}) (\d+)'
)


def _parse_log(line):
    m = _LOG_RE.match(line)
    if not m:
        return None
    ip, path, status, size = m.groups()
    return dict(ip=ip, path=path, status=int(status), size=int(size))


def _log_tailer():
    for log_file in [ACCESS_LOG + '.1', ACCESS_LOG]:
        try:
            with open(log_file) as f:
                for line in f:
                    p = _parse_log(line.strip())
                    if p:
                        path_counts[p['path']] += 1
                        ip_counts[p['ip']] += 1
                        status_win.append((time.time(), p['status'] // 100))
        except Exception:
            pass
    try:
        proc = subprocess.Popen(
            ['tail', '-n0', '-F', ACCESS_LOG],
            stdout=subprocess.PIPE, text=True, bufsize=1
        )
        for line in proc.stdout:
            line = line.strip()
            p = _parse_log(line)
            if p:
                with _lock:
                    path_counts[p['path']] += 1
                    ip_counts[p['ip']] += 1
                    status_win.append((time.time(), p['status'] // 100))
                    recent_log.appendleft(line)
    except Exception:
        pass


def _collector():
    global prev_total
    nginx_meta['version'] = _nginx_version()
    while True:
        s   = _fetch_stub()
        pid = _nginx_pid()
        nginx_meta.update(
            pid=pid,
            uptime=_nginx_uptime(pid) if pid != '—' else '—',
            workers=_worker_count(),
        )
        if s['ok']:
            rps = 0
            if prev_total is not None and s['total'] >= prev_total:
                rps = s['total'] - prev_total
            prev_total = s['total']
            with _lock:
                ts_conn.append((s['active'], s['reading'], s['writing'], s['waiting']))
                ts_rps.append(rps)
        time.sleep(1)


threading.Thread(target=_collector,  daemon=True).start()
threading.Thread(target=_log_tailer, daemon=True).start()


# ── Flask ─────────────────────────────────────────────────────────────────────
app = Flask(__name__)


@app.route('/')
def index():
    return DASHBOARD_HTML


@app.route('/api/metrics')
def metrics_sse():
    def _gen():
        while True:
            with _lock:
                conn_s    = list(ts_conn)
                rps_s     = list(ts_rps)
                cutoff    = time.time() - 300
                sc        = defaultdict(int)
                for ts, cls in status_win:
                    if ts >= cutoff:
                        sc[cls] += 1
                top_paths = sorted(path_counts.items(), key=lambda x: -x[1])[:10]
                top_ips   = sorted(ip_counts.items(),   key=lambda x: -x[1])[:10]
                logs      = list(recent_log)[:25]

            latest   = conn_s[-1] if conn_s else (0, 0, 0, 0)
            total_5m = sum(sc.values())
            errs_5m  = sc.get(4, 0) + sc.get(5, 0)

            payload = dict(
                nginx        = nginx_meta.copy(),
                conn_active  = [c[0] for c in conn_s],
                conn_reading = [c[1] for c in conn_s],
                conn_writing = [c[2] for c in conn_s],
                conn_waiting = [c[3] for c in conn_s],
                rps          = rps_s,
                active       = latest[0],
                reading      = latest[1],
                writing      = latest[2],
                waiting      = latest[3],
                rps_now      = rps_s[-1] if rps_s else 0,
                status       = {str(k): v for k, v in sc.items()},
                top_paths    = top_paths,
                top_ips      = top_ips,
                logs         = logs,
                error_rate   = round(errs_5m / total_5m * 100, 1) if total_5m > 0 else 0.0,
            )
            yield f"data: {json.dumps(payload)}\n\n"
            time.sleep(2)

    return Response(
        stream_with_context(_gen()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


# ── Dashboard HTML ─────────────────────────────────────────────────────────────
DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NGINX Observatory — rpi4</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
<style>
:root{
  --bg:#0d1117;--surface:#161b22;--surface2:#1c2333;--border:#21262d;
  --text:#e6edf3;--muted:#7d8590;
  --green:#3fb950;--cyan:#38bdf8;--red:#f85149;
  --yellow:#d29922;--orange:#db6d28;--purple:#a371f7;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font:13px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;min-height:100vh}

.hdr{background:var(--surface);border-bottom:1px solid var(--border);padding:0 24px;height:52px;display:flex;align-items:center;gap:12px;position:sticky;top:0;z-index:100}
.logo{display:flex;align-items:center;gap:10px;font-weight:600;font-size:15px}
.logo-icon{width:28px;height:28px;background:linear-gradient(135deg,#007f2b,#00c04b);border-radius:6px;display:flex;align-items:center;justify-content:center;color:#fff;font:bold 15px monospace}
.hdr-right{margin-left:auto;display:flex;align-items:center;gap:12px;font-size:12px;color:var(--muted)}
.badge{background:var(--surface2);border:1px solid var(--border);border-radius:4px;padding:2px 8px;font:11px monospace;color:var(--muted)}
.dot{width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 6px var(--green);animation:blink 2s infinite;flex-shrink:0}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}

.main{max-width:1280px;margin:0 auto;padding:20px 24px}

.info-strip{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:10px 20px;display:flex;gap:28px;flex-wrap:wrap;margin-bottom:20px}
.ii{display:flex;flex-direction:column;gap:2px}
.ik{color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.07em}
.iv{font:12px monospace;color:var(--text)}

.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:20px}
@media(max-width:800px){.cards{grid-template-columns:repeat(2,1fr)}}
.card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:16px 18px;position:relative;overflow:hidden}
.card::before{content:'';position:absolute;top:0;left:0;right:0;height:3px}
.card.g::before{background:var(--green)}.card.c::before{background:var(--cyan)}
.card.r::before{background:var(--red)}.card.p::before{background:var(--purple)}
.clabel{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px}
.cval{font-size:30px;font-weight:600;line-height:1;margin-bottom:4px;font-variant-numeric:tabular-nums}
.card.g .cval{color:var(--green)}.card.c .cval{color:var(--cyan)}
.card.r .cval{color:var(--red)}.card.p .cval{color:var(--purple)}
.csub{font-size:11px;color:var(--muted)}

.charts{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:20px}
@media(max-width:800px){.charts{grid-template-columns:1fr}}
.cc{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:16px 18px}
.cc h3{font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;margin-bottom:12px}
.cw{height:150px;position:relative}

.status-row{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:16px 18px;margin-bottom:20px;display:flex;gap:20px;align-items:center;flex-wrap:wrap}
.status-row h3{font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;width:100%;margin-bottom:4px}
.donut-wrap{width:110px;height:110px;flex-shrink:0}
.sc-legend{display:flex;flex-direction:column;gap:7px}
.sc-item{display:flex;align-items:center;gap:8px;font-size:12px}
.sc-dot{width:10px;height:10px;border-radius:2px;flex-shrink:0}
.sc-cnt{margin-left:auto;font-variant-numeric:tabular-nums;font-size:12px;min-width:52px;text-align:right;color:var(--muted)}

.tables{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:20px}
@media(max-width:800px){.tables{grid-template-columns:1fr}}
.tc{background:var(--surface);border:1px solid var(--border);border-radius:8px;overflow:hidden}
.tc-hdr{padding:10px 16px;border-bottom:1px solid var(--border);font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
table{width:100%;border-collapse:collapse;font-size:12px}
th{padding:7px 14px;text-align:left;color:var(--muted);font-weight:normal;border-bottom:1px solid var(--border);font-size:11px}
th.r,td.r{text-align:right}
td{padding:6px 14px;border-bottom:1px solid var(--border);font-family:monospace}
tr:last-child td{border-bottom:none}
tr:hover td{background:var(--surface2)}
.btrack{flex:1;height:4px;background:var(--border);border-radius:2px}
.bfill{height:4px;background:var(--cyan);border-radius:2px;transition:width .3s}
.bcell{display:flex;align-items:center;gap:8px}

.log-card{background:var(--surface);border:1px solid var(--border);border-radius:8px;overflow:hidden;margin-bottom:20px}
.log-hdr{padding:10px 16px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px;font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
.log-body{padding:10px 16px;font:11px/1.8 'Courier New',monospace;max-height:220px;overflow-y:auto;color:var(--muted)}
.l-ip{color:var(--cyan)}.l-2{color:var(--green)}.l-3{color:var(--yellow)}.l-4{color:var(--orange)}.l-5{color:var(--red)}

.footer{color:var(--border);font-size:11px;text-align:center;padding:14px;border-top:1px solid var(--border)}
</style>
</head>
<body>
<div class="hdr">
  <div class="logo">
    <div class="logo-icon">N</div>
    NGINX Observatory
  </div>
  <span class="badge">rpi4</span>
  <div class="hdr-right">
    <div class="dot"></div>
    <span id="ts">connecting…</span>
    <span class="badge" id="ver">nginx/—</span>
  </div>
</div>

<div class="main">
  <div class="info-strip">
    <div class="ii"><span class="ik">Host</span><span class="iv">rpi4</span></div>
    <div class="ii"><span class="ik">Version</span><span class="iv" id="ib-ver">—</span></div>
    <div class="ii"><span class="ik">PID</span><span class="iv" id="ib-pid">—</span></div>
    <div class="ii"><span class="ik">Uptime</span><span class="iv" id="ib-up">—</span></div>
    <div class="ii"><span class="ik">Workers</span><span class="iv" id="ib-wk">—</span></div>
    <div class="ii"><span class="ik">stub_status</span><span class="iv" style="color:var(--green)">active ✓</span></div>
    <div class="ii"><span class="ik">Access log</span><span class="iv" style="color:var(--green)">tailing ✓</span></div>
  </div>

  <div class="cards">
    <div class="card g">
      <div class="clabel">Active Connections</div>
      <div class="cval" id="c-active">—</div>
      <div class="csub">currently open</div>
    </div>
    <div class="card c">
      <div class="clabel">Requests / sec</div>
      <div class="cval" id="c-rps">—</div>
      <div class="csub">last 1 s delta</div>
    </div>
    <div class="card r">
      <div class="clabel">Error Rate (5 min)</div>
      <div class="cval" id="c-err">—</div>
      <div class="csub">4xx + 5xx of total</div>
    </div>
    <div class="card p">
      <div class="clabel">Waiting (keepalive)</div>
      <div class="cval" id="c-wait">—</div>
      <div class="csub">R:<span id="c-read">—</span> W:<span id="c-wrt">—</span></div>
    </div>
  </div>

  <div class="charts">
    <div class="cc">
      <h3>Active Connections — 2 min window</h3>
      <div class="cw"><canvas id="ch-conn"></canvas></div>
    </div>
    <div class="cc">
      <h3>Requests / sec — 2 min window</h3>
      <div class="cw"><canvas id="ch-rps"></canvas></div>
    </div>
  </div>

  <div class="status-row">
    <h3>Status Code Distribution — last 5 min</h3>
    <div class="donut-wrap"><canvas id="ch-status"></canvas></div>
    <div class="sc-legend">
      <div class="sc-item"><div class="sc-dot" style="background:#3fb950"></div>2xx OK<span class="sc-cnt" id="sc-2">0</span></div>
      <div class="sc-item"><div class="sc-dot" style="background:#38bdf8"></div>3xx Redirect<span class="sc-cnt" id="sc-3">0</span></div>
      <div class="sc-item"><div class="sc-dot" style="background:#d29922"></div>4xx Client error<span class="sc-cnt" id="sc-4">0</span></div>
      <div class="sc-item"><div class="sc-dot" style="background:#f85149"></div>5xx Server error<span class="sc-cnt" id="sc-5">0</span></div>
    </div>
  </div>

  <div class="tables">
    <div class="tc">
      <div class="tc-hdr">Top Paths</div>
      <table><thead><tr><th>Path</th><th class="r">Hits</th><th style="width:90px"></th></tr></thead>
      <tbody id="t-paths"></tbody></table>
    </div>
    <div class="tc">
      <div class="tc-hdr">Top Client IPs</div>
      <table><thead><tr><th>IP</th><th class="r">Hits</th><th style="width:90px"></th></tr></thead>
      <tbody id="t-ips"></tbody></table>
    </div>
  </div>

  <div class="log-card">
    <div class="log-hdr"><div class="dot"></div>Live Access Log</div>
    <div class="log-body" id="log-body"><span>waiting for log entries…</span></div>
  </div>
</div>

<div class="footer">nginx-obs v1.0 · rpi4 · <span id="ft"></span></div>

<script>
Chart.defaults.color='#7d8590';
Chart.defaults.borderColor='#21262d';
Chart.defaults.font.family="-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif";
Chart.defaults.font.size=11;

const HISTORY=120;

function makeLine(id,label,color){
  const ctx=document.getElementById(id).getContext('2d');
  const g=ctx.createLinearGradient(0,0,0,150);
  g.addColorStop(0,color+'44');g.addColorStop(1,color+'00');
  return new Chart(ctx,{
    type:'line',
    data:{labels:Array(HISTORY).fill(''),datasets:[{label,data:Array(HISTORY).fill(null),
      borderColor:color,backgroundColor:g,borderWidth:1.5,fill:true,tension:.3,pointRadius:0}]},
    options:{
      responsive:true,maintainAspectRatio:false,animation:{duration:0},
      plugins:{legend:{display:false},tooltip:{mode:'index',intersect:false}},
      scales:{
        x:{display:false},
        y:{grid:{color:'#21262d'},ticks:{color:'#7d8590',maxTicksLimit:4},min:0}
      }
    }
  });
}

const chConn=makeLine('ch-conn','Connections','#3fb950');
const chRps =makeLine('ch-rps','RPS','#38bdf8');

const chStatus=new Chart(document.getElementById('ch-status').getContext('2d'),{
  type:'doughnut',
  data:{labels:['2xx','3xx','4xx','5xx'],datasets:[{
    data:[1,0,0,0],
    backgroundColor:['#3fb950','#38bdf8','#d29922','#f85149'],
    borderWidth:0,hoverOffset:4
  }]},
  options:{
    responsive:true,maintainAspectRatio:false,cutout:'65%',
    plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>` ${c.label}: ${c.raw}`}}},
    animation:{duration:500}
  }
});

function tableRows(tbodyId,rows){
  const max=rows.length>0?rows[0][1]:1;
  document.getElementById(tbodyId).innerHTML=rows.map(([lbl,cnt])=>`
    <tr>
      <td style="max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${lbl}">${lbl}</td>
      <td class="r">${cnt.toLocaleString()}</td>
      <td><div class="bcell"><div class="btrack"><div class="bfill" style="width:${Math.round(cnt/max*100)}%"></div></div></div></td>
    </tr>`).join('');
}

function colorLog(line){
  const m=line.match(/^(\S+).*?"(?:\S+ )?(\S+)[^"]*"\s+(\d{3})/);
  if(!m)return line;
  const[,ip,,status]=m;
  const cls='l-'+status[0];
  return line
    .replace(ip,`<span class="l-ip">${ip}</span>`)
    .replace(` ${status} `,` <span class="${cls}">${status}</span> `);
}

function setChart(chart,arr){
  const data=arr.slice(-HISTORY);
  while(data.length<HISTORY)data.unshift(null);
  chart.data.datasets[0].data=data;
  chart.update('none');
}

const es=new EventSource('api/metrics');
es.onmessage=function(e){
  const d=JSON.parse(e.data);
  const now=new Date();
  document.getElementById('ts').textContent='Updated '+now.toLocaleTimeString();
  document.getElementById('ft').textContent=now.toLocaleString();

  const n=d.nginx||{};
  if(n.version&&n.version!=='—'){
    document.getElementById('ver').textContent='nginx/'+n.version;
    document.getElementById('ib-ver').textContent='nginx/'+n.version;
  }
  if(n.pid)    document.getElementById('ib-pid').textContent=n.pid;
  if(n.uptime) document.getElementById('ib-up').textContent=n.uptime;
  if(n.workers)document.getElementById('ib-wk').textContent=n.workers;

  document.getElementById('c-active').textContent=d.active??'—';
  document.getElementById('c-rps').textContent=d.rps_now??'—';
  document.getElementById('c-wait').textContent=d.waiting??'—';
  document.getElementById('c-read').textContent=d.reading??'—';
  document.getElementById('c-wrt').textContent=d.writing??'—';
  document.getElementById('c-err').textContent=(d.error_rate??0)+'%';

  setChart(chConn,d.conn_active||[]);
  setChart(chRps, d.rps||[]);

  const sc=d.status||{};
  chStatus.data.datasets[0].data=[sc['2']||0,sc['3']||0,sc['4']||0,sc['5']||0];
  chStatus.update();
  document.getElementById('sc-2').textContent=(sc['2']||0).toLocaleString();
  document.getElementById('sc-3').textContent=(sc['3']||0).toLocaleString();
  document.getElementById('sc-4').textContent=(sc['4']||0).toLocaleString();
  document.getElementById('sc-5').textContent=(sc['5']||0).toLocaleString();

  if(d.top_paths)tableRows('t-paths',d.top_paths);
  if(d.top_ips)  tableRows('t-ips',  d.top_ips);

  if(d.logs&&d.logs.length){
    document.getElementById('log-body').innerHTML=
      d.logs.map(l=>`<div>${colorLog(l)}</div>`).join('');
  }
};
es.onerror=function(){
  document.getElementById('ts').textContent='disconnected — retrying…';
};
setInterval(()=>document.getElementById('ft').textContent=new Date().toLocaleString(),1000);
</script>
</body>
</html>"""

if __name__ == '__main__':
    app.run(host=BIND_HOST, port=BIND_PORT, threaded=True)
