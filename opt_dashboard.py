# pythonGateways/opt_dashboard.py
import os
import time
import uuid
import json
import csv
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ----------------------------
# Config
# ----------------------------
OPT_HOST = "0.0.0.0"
OPT_PORT = 8055

STATIONS_DEFAULT = ["S1", "S2", "S3", "S4", "S5", "S6"]

# ----------------------------
# Paths (write next to this file)
# ----------------------------
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
KPI_JSON_PATH = os.path.join(_BASE_DIR, "kpi_latest.json")
KPI_CSV_PATH = os.path.join(_BASE_DIR, "kpi_history.csv")

KPI_WRITE_EVERY_TICKS = 2
_kpi_tick_counter = 0
_kpi_csv_header_written = False

# ----------------------------
# Thread-safe state
# ----------------------------
_kpi_lock = threading.Lock()
_kpi_snapshot = {}

_params_lock = threading.Lock()
_opt_params = {
    "run_enable": True,
    "buf_max": 2,
    "reset_pulse_ticks": 3,
    "file_logging": False,
    "operators_total": 2,
    "operators_required": {st: 1 for st in STATIONS_DEFAULT},
    "fault_reset_all": True,
}

# active overrides (maintenance / fault blocks)
_over_lock = threading.Lock()
_blocked_until = {st: 0.0 for st in STATIONS_DEFAULT}
_blocked_reason = {st: "" for st in STATIONS_DEFAULT}

# triggers (one-shot events PLC should consume)
_trig_lock = threading.Lock()
_triggers = []  # {"type":"fault_request"/"maintenance_request", ...}

# web commands (start/stop/reset)
_web_cmd_lock = threading.Lock()
_web_start_req = False
_web_stop_req = False
_web_reset_req = False

# run history (optional)
_runs_lock = threading.Lock()
_runs = []
_current_run = None

# server guard
_srv_thread = None
_srv_started = False


# ----------------------------
# Public API for PLC
# ----------------------------
def start_in_thread(host=OPT_HOST, port=OPT_PORT):
    global _srv_thread, _srv_started, OPT_HOST, OPT_PORT
    OPT_HOST = host
    OPT_PORT = int(port)
    if _srv_started:
        return
    _srv_started = True
    _srv_thread = threading.Thread(target=start_server, args=(OPT_HOST, OPT_PORT), daemon=True)
    _srv_thread.start()


def request_web_start():
    global _web_start_req
    with _web_cmd_lock:
        _web_start_req = True


def request_web_stop():
    global _web_stop_req
    with _web_cmd_lock:
        _web_stop_req = True


def request_web_reset():
    global _web_reset_req
    with _web_cmd_lock:
        _web_reset_req = True


def consume_web_cmds():
    global _web_start_req, _web_stop_req, _web_reset_req
    with _web_cmd_lock:
        s, p, r = _web_start_req, _web_stop_req, _web_reset_req
        _web_start_req = False
        _web_stop_req = False
        _web_reset_req = False
    return s, p, r


def get_params():
    with _params_lock:
        p = dict(_opt_params)
        p["operators_required"] = dict(_opt_params.get("operators_required", {}))
        return p


def set_params(patch: dict):
    if not isinstance(patch, dict):
        return
    with _params_lock:
        for k, v in patch.items():
            if k not in _opt_params:
                continue
            if k == "operators_required" and isinstance(v, dict):
                _opt_params[k] = dict(v)
            else:
                _opt_params[k] = v


def set_kpi_snapshot(snap: dict):
    if not isinstance(snap, dict):
        return
    with _kpi_lock:
        _kpi_snapshot.clear()
        _kpi_snapshot.update(snap)


def get_kpi_snapshot():
    with _kpi_lock:
        return dict(_kpi_snapshot)


def get_overrides(sim_time_s: float):
    now = float(sim_time_s)
    with _over_lock:
        active = {}
        for st, until in _blocked_until.items():
            if until > now:
                active[st] = {"until": float(until), "reason": _blocked_reason.get(st, "")}
    p = get_params()
    return {
        "active_blocks": active,
        "operators_total": int(p.get("operators_total", 0)),
        "operators_required": dict(p.get("operators_required", {})),
    }


def consume_triggers():
    with _trig_lock:
        out = list(_triggers)
        _triggers.clear()
    return out


def inject_fault(station: str, duration_s: float, sim_time_s: float, reason="fault"):
    st = str(station)
    dur = max(0.0, float(duration_s))
    now = float(sim_time_s)
    until = now + dur
    with _over_lock:
        if st not in _blocked_until:
            _blocked_until[st] = 0.0
            _blocked_reason[st] = ""
        _blocked_until[st] = max(_blocked_until[st], until)
        _blocked_reason[st] = str(reason)


def schedule_maintenance(station: str, duration_s: float, sim_time_s: float):
    st = str(station)
    dur = max(0.0, float(duration_s))
    now = float(sim_time_s)
    until = now + dur
    with _over_lock:
        if st not in _blocked_until:
            _blocked_until[st] = 0.0
            _blocked_reason[st] = ""
        _blocked_until[st] = max(_blocked_until[st], until)
        _blocked_reason[st] = "maintenance"


def clear_blocks():
    with _over_lock:
        for st in list(_blocked_until.keys()):
            _blocked_until[st] = 0.0
            _blocked_reason[st] = ""


def run_start(meta=None):
    global _current_run
    r = {
        "run_id": str(uuid.uuid4())[:8],
        "started_at": time.time(),
        "ended_at": None,
        "params": get_params(),
        "meta": meta or {},
        "summary": {},
    }
    with _runs_lock:
        _current_run = r
    return r


def run_stop(final_snap: dict):
    global _current_run
    with _runs_lock:
        if not _current_run:
            return None
        _current_run["ended_at"] = time.time()
        _current_run["summary"] = {
            "packages_completed": int(final_snap.get("packages_completed", 0)),
            "throughput_per_min": float(final_snap.get("throughput_per_min", 0.0)),
            "accept": int(final_snap.get("accept", 0)),
            "reject": int(final_snap.get("reject", 0)),
            "yield_pct": float(final_snap.get("yield_pct", 0.0)),
            "availability": float(final_snap.get("availability", 0.0)),
            "downtime_s": float(final_snap.get("downtime_s", 0.0)),
            "state": str(final_snap.get("plc_state", "")),
        }
        _runs.append(_current_run)
        finished = _current_run
        _current_run = None
        return finished


def write_kpis_to_files(snapshot: dict):
    global _kpi_tick_counter, _kpi_csv_header_written
    if not bool(get_params().get("file_logging", False)):
        return

    _kpi_tick_counter += 1
    if (_kpi_tick_counter % int(KPI_WRITE_EVERY_TICKS)) != 0:
        return

    os.makedirs(_BASE_DIR, exist_ok=True)

    tmp = KPI_JSON_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2)
    os.replace(tmp, KPI_JSON_PATH)

    row = {
        "sim_time_s": snapshot.get("sim_time_s", 0.0),
        "plc_state": snapshot.get("plc_state", ""),
        "packages_completed": snapshot.get("packages_completed", 0),
        "accept": snapshot.get("accept", 0),
        "reject": snapshot.get("reject", 0),
        "yield_pct": snapshot.get("yield_pct", 0.0),
        "throughput_per_min": snapshot.get("throughput_per_min", 0.0),
        "availability": snapshot.get("availability", 0.0),
        "downtime_s": snapshot.get("downtime_s", 0.0),
        "fault_any": snapshot.get("fault_any", 0),
    }

    write_header = (not os.path.exists(KPI_CSV_PATH)) or (not _kpi_csv_header_written)
    with open(KPI_CSV_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if write_header:
            w.writeheader()
            _kpi_csv_header_written = True
        w.writerow(row)


def build_kpi_snapshot(plc, ms, stations):
    t_s = float(getattr(plc, "_sim_time_s", 0.0))
    t_s = max(0.001, t_s)

    p_ms = int(getattr(ms, "S6_packages_completed", 0) or 0)
    p_plc = int(getattr(plc, "finished", 0) or 0)
    packages = max(p_ms, p_plc)

    accept = int(getattr(ms, "S5_accept", 0) or 0)
    reject = int(getattr(ms, "S5_reject", 0) or 0)

    total_q = accept + reject
    yield_pct = (accept / total_q * 100.0) if total_q else 0.0
    tpm = packages / (t_s / 60.0)

    fault_any = 1 if any(int(getattr(ms, f"{st}_fault", 0) or 0) for st in stations) else 0
    busy_map = {st: int(getattr(ms, f"{st}_busy", 0) or 0) for st in stations}

    # station status map for UI tiles
    stations_map = {}
    for st in stations:
        stations_map[st] = {
            "ready": int(getattr(ms, f"{st}_ready", 0) or 0),
            "busy": int(getattr(ms, f"{st}_busy", 0) or 0),
            "fault": int(getattr(ms, f"{st}_fault", 0) or 0),
            "done": int(getattr(ms, f"{st}_done", 0) or 0),
            "cycle_time_ms": int(getattr(ms, f"{st}_cycle_time_ms", 0) or 0),
        }

    bneck = "-"
    if any(busy_map.values()):
        bneck = [k for k, v in busy_map.items() if v][0]

    busy_frac = (sum(busy_map.values()) / max(1, len(stations)))
    idle_pct = (1.0 - busy_frac) * 100.0

    downtime_s = float(getattr(ms, "S6_downtime_s", 0.0) or 0.0)
    availability = float(getattr(ms, "S6_availability", 0.0) or 0.0)

    ov = get_overrides(t_s)

    return {
        "sim_time_s": t_s,
        "plc_state": str(getattr(plc, "_state", "")),
        "batch_id": int(getattr(plc, "_batch_id", 0) or 0),
        "recipe_id": int(getattr(plc, "_recipe_id", 0) or 0),

        "packages_completed": packages,
        "throughput_per_min": float(tpm),

        "accept": accept,
        "reject": reject,
        "yield_pct": float(yield_pct),

        "buffers": dict(getattr(plc, "_buffers", {}) or {}),
        "bottleneck_station": bneck,
        "bottleneck_utilization": float(busy_frac),
        "line_idle_pct": float(idle_pct),

        "downtime_s": float(downtime_s),
        "availability": float(availability),

        "fault_any": int(fault_any),

        "stations": stations_map,  # <-- for the animated station tiles

        "active_blocks": ov.get("active_blocks", {}),
        "operators_total": ov.get("operators_total", 0),
        "operators_required": ov.get("operators_required", {}),
    }


# ----------------------------
# HTTP server (UI)
# ----------------------------
HTML_PAGE = """<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Optimization Dashboard</title>
  <style>
    :root{
      --bg0:#070A12;
      --bg1:#0B1020;
      --card:rgba(255,255,255,.06);
      --card2:rgba(255,255,255,.08);
      --stroke:rgba(255,255,255,.12);
      --text:#EAF0FF;
      --muted:rgba(234,240,255,.65);
      --accent:#6EE7FF;
      --accent2:#A78BFA;
      --good:#34D399;
      --warn:#FBBF24;
      --bad:#FB7185;
      --shadow: 0 20px 60px rgba(0,0,0,.55);
      --r:18px;
    }

    *{box-sizing:border-box}
    .mono{font-family:ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace}

    body{
      margin:0;
      color:var(--text);
      font-family: ui-sans-serif,system-ui,Segoe UI,Arial;
      background:
        radial-gradient(1200px 700px at 15% 10%, rgba(110,231,255,.20), transparent 60%),
        radial-gradient(900px 600px at 85% 20%, rgba(167,139,250,.22), transparent 55%),
        radial-gradient(800px 600px at 50% 110%, rgba(52,211,153,.10), transparent 55%),
        linear-gradient(180deg,var(--bg0),var(--bg1));
      overflow-x:hidden;
    }

    /* subtle animated aurora */
    body:before{
      content:"";
      position:fixed; inset:-20%;
      background:
        radial-gradient(600px 380px at 20% 30%, rgba(110,231,255,.12), transparent 55%),
        radial-gradient(520px 340px at 75% 25%, rgba(167,139,250,.12), transparent 55%),
        radial-gradient(520px 360px at 55% 75%, rgba(251,113,133,.08), transparent 55%);
      filter: blur(18px);
      animation: floaty 14s ease-in-out infinite;
      pointer-events:none;
      opacity:.9;
    }
    @keyframes floaty{
      0%{transform:translate3d(0,0,0) scale(1)}
      50%{transform:translate3d(-2%,1.5%,0) scale(1.02)}
      100%{transform:translate3d(0,0,0) scale(1)}
    }

    .wrap{max-width:1280px;margin:20px auto;padding:0 16px; position:relative}
    .topbar{
      display:flex; align-items:flex-end; justify-content:space-between; gap:12px;
      margin-bottom:14px;
    }
    .title{
      font-size:22px; font-weight:750; letter-spacing:.2px;
    }
    .subtitle{color:var(--muted); font-size:13px; margin-top:4px}

    .badges{display:flex; gap:10px; flex-wrap:wrap; align-items:center}
    .badge{
      display:inline-flex; gap:8px; align-items:center;
      padding:8px 10px;
      border:1px solid var(--stroke);
      background:rgba(255,255,255,.05);
      border-radius:999px;
      box-shadow: 0 10px 30px rgba(0,0,0,.25);
      backdrop-filter: blur(10px);
      font-size:12px;
    }
    .dot{width:8px;height:8px;border-radius:999px;background:var(--muted)}
    .dot.good{background:var(--good)}
    .dot.warn{background:var(--warn)}
    .dot.bad{background:var(--bad)}
    .pulse.bad{animation:pulseBad 1s ease-in-out infinite}
    @keyframes pulseBad{
      0%,100%{box-shadow:0 0 0 0 rgba(251,113,133,.0)}
      50%{box-shadow:0 0 0 10px rgba(251,113,133,.12)}
    }

    .row{display:grid; grid-template-columns: repeat(12, 1fr); gap:12px}
    .span4{grid-column: span 4}
    .span6{grid-column: span 6}
    .span12{grid-column: span 12}
    @media (max-width: 980px){
      .span4,.span6{grid-column: span 12}
    }

    .card{
      background: linear-gradient(180deg, var(--card2), var(--card));
      border:1px solid var(--stroke);
      border-radius:var(--r);
      padding:14px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(12px);
      transform: translateY(0);
      transition: transform .18s ease, border-color .18s ease;
    }
    .card:hover{transform: translateY(-2px); border-color: rgba(110,231,255,.26)}

    .h{display:flex; align-items:center; justify-content:space-between; gap:10px}
    .h b{font-size:13px; letter-spacing:.3px}
    .muted{color:var(--muted); font-size:12px}
    .line{height:1px;background:rgba(255,255,255,.08); margin:12px 0}

    button{
      border:1px solid rgba(255,255,255,.14);
      color:var(--text);
      padding:10px 12px;
      border-radius:12px;
      background: rgba(255,255,255,.06);
      cursor:pointer;
      transition: transform .12s ease, background .12s ease, border-color .12s ease;
      user-select:none;
    }
    button:hover{transform: translateY(-1px); background: rgba(255,255,255,.10); border-color: rgba(110,231,255,.25)}
    button.primary{
      background: linear-gradient(135deg, rgba(110,231,255,.22), rgba(167,139,250,.18));
      border-color: rgba(110,231,255,.28);
    }
    button.danger{
      background: linear-gradient(135deg, rgba(251,113,133,.22), rgba(167,139,250,.12));
      border-color: rgba(251,113,133,.30);
    }

    input, select{
      width:100%;
      color:var(--text);
      background: rgba(0,0,0,.25);
      border:1px solid rgba(255,255,255,.14);
      border-radius:12px;
      padding:10px 12px;
      outline:none;
    }
    input:focus, select:focus{border-color: rgba(110,231,255,.35); box-shadow:0 0 0 4px rgba(110,231,255,.10)}
    input[type="checkbox"]{width:auto; transform: translateY(1px); accent-color: #6EE7FF}
    label{display:flex; gap:8px; align-items:center}

    .kpis{
      display:grid;
      grid-template-columns: repeat(12, 1fr);
      gap:12px;
      margin:14px 0;
    }
    .kpi{
      grid-column: span 3;
      padding:12px 14px;
      border-radius:var(--r);
      border:1px solid rgba(255,255,255,.12);
      background: linear-gradient(180deg, rgba(255,255,255,.08), rgba(255,255,255,.05));
      box-shadow: 0 18px 50px rgba(0,0,0,.45);
      position:relative;
      overflow:hidden;
    }
    @media (max-width: 980px){ .kpi{grid-column: span 6} }
    @media (max-width: 560px){ .kpi{grid-column: span 12} }

    .kpi:before{
      content:"";
      position:absolute; inset:-40%;
      background: radial-gradient(circle at 30% 30%, rgba(110,231,255,.16), transparent 55%);
      transform: rotate(10deg);
      opacity:.7;
      pointer-events:none;
    }
    .kpiLabel{color:var(--muted); font-size:12px}
    .kpiValue{font-size:26px; font-weight:800; margin-top:4px}
    .kpiSub{color:var(--muted); font-size:12px; margin-top:4px}
    .kpiGlow{position:absolute; right:12px; top:12px; font-size:12px; color:rgba(234,240,255,.8)}

    .sparkWrap{
      grid-column: span 6;
      border-radius:var(--r);
      border:1px solid rgba(255,255,255,.12);
      background: linear-gradient(180deg, rgba(255,255,255,.08), rgba(255,255,255,.05));
      box-shadow: 0 18px 50px rgba(0,0,0,.45);
      padding:12px 14px;
    }
    @media (max-width: 980px){ .sparkWrap{grid-column: span 12} }

    .sparkTop{display:flex; justify-content:space-between; align-items:center; gap:10px}
    canvas{width:100%; height:60px; display:block; margin-top:10px}

    .stations{
      display:grid;
      grid-template-columns: repeat(6, 1fr);
      gap:10px;
      margin-top:10px;
    }
    @media (max-width: 980px){ .stations{grid-template-columns: repeat(3, 1fr)} }
    @media (max-width: 560px){ .stations{grid-template-columns: repeat(2, 1fr)} }

    .st{
      border-radius:16px;
      border:1px solid rgba(255,255,255,.12);
      padding:10px;
      background: rgba(255,255,255,.05);
      transition: transform .15s ease, border-color .15s ease;
      position:relative;
      overflow:hidden;
    }
    .st:hover{transform: translateY(-2px); border-color: rgba(110,231,255,.25)}
    .stName{font-weight:800}
    .stMeta{margin-top:6px; font-size:12px; color:var(--muted); display:flex; justify-content:space-between; gap:8px}
    .stTag{
      display:inline-flex; gap:6px; align-items:center;
      margin-top:8px;
      padding:6px 8px;
      border-radius:999px;
      border:1px solid rgba(255,255,255,.12);
      width:fit-content;
      font-size:12px;
      background: rgba(0,0,0,.18);
    }
    .stTag .dot{width:7px;height:7px}
    .st.good .stTag{border-color: rgba(52,211,153,.28)}
    .st.warn .stTag{border-color: rgba(251,191,36,.28)}
    .st.bad  .stTag{border-color: rgba(251,113,133,.30)}
    .st.bad:after{
      content:"";
      position:absolute; inset:-40%;
      background: radial-gradient(circle at 30% 30%, rgba(251,113,133,.18), transparent 55%);
      animation: floaty 10s ease-in-out infinite;
      pointer-events:none;
    }

    .cols2{display:grid; grid-template-columns: 1fr 1fr; gap:10px}
    @media (max-width: 560px){ .cols2{grid-template-columns: 1fr} }

    .hrrow{display:grid; grid-template-columns:52px 1fr 40px; gap:10px; align-items:center; margin:8px 0}
    input[type="range"]{width:100%; accent-color:#6EE7FF}

    pre{
      background: rgba(0,0,0,.35);
      border:1px solid rgba(255,255,255,.10);
      border-radius:16px;
      padding:12px;
      overflow:auto;
      max-height:360px;
      color: rgba(234,240,255,.9);
    }

    details summary{
      cursor:pointer;
      color: rgba(234,240,255,.9);
      user-select:none;
    }
  </style>
</head>

<body>
  <div class="wrap">
    <div class="topbar">
      <div>
        <div class="title">Optimization Dashboard</div>
        <div class="subtitle">real-time KPI monitoring + scenario injection (fault / maintenance)</div>
      </div>
      <div class="badges">
        <div class="badge" id="badge_state"><span class="dot"></span><span class="mono" id="pill_state">state: -</span></div>
        <div class="badge" id="badge_time"><span class="dot good"></span><span class="mono" id="pill_time">t: -</span></div>
        <div class="badge" id="badge_pkg"><span class="dot"></span><span class="mono" id="pill_pkg">pkg: -</span></div>
        <div class="badge" id="badge_fault"><span class="dot good" id="fault_dot"></span><span class="mono" id="fault_txt">fault: no</span></div>
      </div>
    </div>

    <div class="kpis">
      <div class="kpi">
        <div class="kpiLabel">Packages completed</div>
        <div class="kpiValue" id="kpi_packages">0</div>
        <div class="kpiSub">batch <span id="kpi_batch">-</span> • recipe <span id="kpi_recipe">-</span></div>
        <div class="kpiGlow" id="kpi_bneck">bneck: -</div>
      </div>

      <div class="kpi">
        <div class="kpiLabel">Throughput</div>
        <div class="kpiValue" id="kpi_tpm">0.0</div>
        <div class="kpiSub">units / minute</div>
        <div class="kpiGlow" id="kpi_idle">idle: -%</div>
      </div>

      <div class="kpi">
        <div class="kpiLabel">Yield</div>
        <div class="kpiValue" id="kpi_yield">0%</div>
        <div class="kpiSub">accept <span id="kpi_accept">0</span> • reject <span id="kpi_reject">0</span></div>
        <div class="kpiGlow" id="kpi_q">quality</div>
      </div>

      <div class="kpi">
        <div class="kpiLabel">Availability</div>
        <div class="kpiValue" id="kpi_avail">0%</div>
        <div class="kpiSub">downtime <span id="kpi_dt">0</span>s</div>
        <div class="kpiGlow" id="kpi_live">live</div>
      </div>

      <div class="sparkWrap">
        <div class="sparkTop">
          <div>
            <div style="font-weight:800">Throughput trend</div>
            <div class="muted">last ~60 updates</div>
          </div>
          <div class="muted mono">auto-refresh 500ms</div>
        </div>
        <canvas id="spark" width="900" height="160"></canvas>
      </div>

      <div class="card span12">
        <div class="h">
          <b>Stations</b>
          <div class="muted">hover a tile • colors react to busy/fault/blocked</div>
        </div>
        <div class="stations" id="stations"></div>
      </div>
    </div>

    <div class="row">
      <div class="card span4">
        <div class="h"><b>Run</b><span class="muted">requests only • PLC consumes next scan</span></div>
        <div class="line"></div>
        <div style="display:flex; gap:10px; flex-wrap:wrap">
          <button class="primary" onclick="post('/run/start',{})">Start</button>
          <button class="danger" onclick="post('/run/stop',{})">Stop</button>
          <button onclick="post('/run/reset',{})">Reset</button>
        </div>
      </div>

      <div class="card span4">
        <div class="h"><b>Line tuning</b><span class="muted">PLC reads each scan</span></div>
        <div class="line"></div>
        <div class="cols2">
          <div>
            <div class="muted">buf_max</div>
            <input id="buf_max" type="number" min="0" max="20" value="2"/>
          </div>
          <div>
            <div class="muted">reset_pulse_ticks</div>
            <input id="reset_ticks" type="number" min="1" max="20" value="3"/>
          </div>
          <label><input id="file_logging" type="checkbox"/> file_logging</label>
          <label><input id="fault_reset_all" type="checkbox" checked/> fault_reset_all</label>
        </div>
        <div class="line"></div>
        <button class="primary" onclick="applyLine()">Apply line params</button>
      </div>

      <div class="card span4">
        <div class="h"><b>Human resources</b><span class="muted">station gating</span></div>
        <div class="line"></div>
        <div class="muted">operators_total</div>
        <input id="ops_total" type="number" min="0" max="20" value="2" style="max-width:180px"/>
        <div class="line"></div>

        <div class="muted">operators_required per station</div>

        <div class="hrrow"><div class="mono">S1</div><input id="req_S1" type="range" min="0" max="5" value="1" oninput="syncVal('S1')"/><div class="mono" id="val_S1">1</div></div>
        <div class="hrrow"><div class="mono">S2</div><input id="req_S2" type="range" min="0" max="5" value="1" oninput="syncVal('S2')"/><div class="mono" id="val_S2">1</div></div>
        <div class="hrrow"><div class="mono">S3</div><input id="req_S3" type="range" min="0" max="5" value="1" oninput="syncVal('S3')"/><div class="mono" id="val_S3">1</div></div>
        <div class="hrrow"><div class="mono">S4</div><input id="req_S4" type="range" min="0" max="5" value="1" oninput="syncVal('S4')"/><div class="mono" id="val_S4">1</div></div>
        <div class="hrrow"><div class="mono">S5</div><input id="req_S5" type="range" min="0" max="5" value="1" oninput="syncVal('S5')"/><div class="mono" id="val_S5">1</div></div>
        <div class="hrrow"><div class="mono">S6</div><input id="req_S6" type="range" min="0" max="5" value="1" oninput="syncVal('S6')"/><div class="mono" id="val_S6">1</div></div>

        <div class="line"></div>
        <div style="display:flex; gap:10px; flex-wrap:wrap">
          <button onclick="setAllReq(1)">Set all = 1</button>
          <button onclick="setAllReq(0)">Set all = 0</button>
          <button class="primary" onclick="applyHR()">Apply HR params</button>
        </div>
      </div>

      <div class="card span6">
        <div class="h"><b>Scenario</b><span class="muted">fault / maintenance injection</span></div>
        <div class="line"></div>

        <div class="cols2">
          <div>
            <div style="font-weight:800; margin-bottom:6px">Fault</div>
            <div class="muted">station</div>
            <select id="f_st">
              <option>S1</option><option>S2</option><option selected>S3</option>
              <option>S4</option><option>S5</option><option>S6</option>
            </select>
            <div class="muted" style="margin-top:10px">duration_s</div>
            <input id="f_dur" type="number" value="8" style="max-width:160px"/>
            <div style="margin-top:10px"><button class="danger" onclick="fault()">Inject fault</button></div>
          </div>

          <div>
            <div style="font-weight:800; margin-bottom:6px">Maintenance</div>
            <div class="muted">station</div>
            <select id="m_st">
              <option>S1</option><option>S2</option><option>S3</option>
              <option selected>S4</option><option>S5</option><option>S6</option>
            </select>
            <div class="muted" style="margin-top:10px">duration_s</div>
            <input id="m_dur" type="number" value="15" style="max-width:160px"/>
            <div style="margin-top:10px; display:flex; gap:10px; flex-wrap:wrap">
              <button class="primary" onclick="maint()">Start maintenance</button>
              <button onclick="post('/blocks/clear',{})">Clear blocks</button>
            </div>
          </div>
        </div>

        <div class="line"></div>
        <div class="h"><b>Active blocks</b><span class="muted">countdown uses sim_time_s</span></div>
        <div id="blocks" style="margin-top:10px" class="muted">none</div>
      </div>

      <div class="card span6">
        <div class="h"><b>Debug</b><span class="muted">raw JSON (optional)</span></div>
        <div class="line"></div>

        <details open>
          <summary>KPIs JSON</summary>
          <pre id="kpi">waiting for PLC…</pre>
        </details>

        <details>
          <summary>Runs JSON</summary>
          <pre id="runs">loading…</pre>
        </details>
      </div>
    </div>
  </div>

<script>
async function get(path){ return (await fetch(path,{cache:"no-store"})).json(); }
async function post(path,obj){
  return (await fetch(path,{
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body: JSON.stringify(obj||{})
  })).json();
}
function i(id){ return document.getElementById(id); }

function syncVal(st){
  const v = parseInt(i("req_"+st).value||"0");
  i("val_"+st).textContent = String(v);
}
function setAllReq(v){
  ["S1","S2","S3","S4","S5","S6"].forEach(st=>{
    i("req_"+st).value = String(v);
    syncVal(st);
  });
}

async function loadParams(){
  const p = await get("/params");
  i("buf_max").value = p.buf_max ?? 2;
  i("reset_ticks").value = p.reset_pulse_ticks ?? 3;
  i("file_logging").checked = !!p.file_logging;
  i("fault_reset_all").checked = !!p.fault_reset_all;

  i("ops_total").value = p.operators_total ?? 2;

  const req = p.operators_required || {};
  ["S1","S2","S3","S4","S5","S6"].forEach(st=>{
    const el = i("req_"+st);
    if(el){
      el.value = String(req[st] ?? 1);
      syncVal(st);
    }
  });
}

async function applyLine(){
  await post("/params",{
    buf_max: parseInt(i("buf_max").value||"2"),
    reset_pulse_ticks: parseInt(i("reset_ticks").value||"3"),
    file_logging: !!i("file_logging").checked,
    fault_reset_all: !!i("fault_reset_all").checked
  });
  await loadParams();
}
async function applyHR(){
  const req = {};
  ["S1","S2","S3","S4","S5","S6"].forEach(st=>{
    req[st] = parseInt(i("req_"+st).value||"1");
  });
  await post("/params",{
    operators_total: parseInt(i("ops_total").value||"2"),
    operators_required: req
  });
  await loadParams();
}

async function fault(){
  await post("/scenario/fault",{
    station: i("f_st").value,
    duration_s: parseFloat(i("f_dur").value||"8")
  });
}
async function maint(){
  await post("/scenario/maintenance",{
    station: i("m_st").value,
    duration_s: parseFloat(i("m_dur").value||"15")
  });
}

/* smooth number animation */
function animateNumber(el, next, opts={}){
  const dur = opts.dur ?? 450;
  const decimals = opts.decimals ?? 0;
  const suffix = opts.suffix ?? "";
  const prev = parseFloat(el.dataset.v ?? "0");
  const start = performance.now();

  function step(t){
    const p = Math.min(1, (t - start)/dur);
    const e = 1 - Math.pow(1-p, 3); // easeOutCubic
    const cur = prev + (next - prev)*e;
    el.textContent = cur.toFixed(decimals) + suffix;
    if(p < 1) requestAnimationFrame(step);
    else el.dataset.v = String(next);
  }
  requestAnimationFrame(step);
}

/* sparkline */
const spark = { ys:[] };
function pushSpark(y){
  spark.ys.push(y);
  if(spark.ys.length > 60) spark.ys.shift();
}
function drawSpark(){
  const c = i("spark");
  const ctx = c.getContext("2d");
  const w = c.width, h = c.height;
  ctx.clearRect(0,0,w,h);

  // background grid
  ctx.globalAlpha = 0.18;
  ctx.strokeStyle = "#FFFFFF";
  ctx.lineWidth = 1;
  for(let k=1;k<6;k++){
    const y = (h/6)*k;
    ctx.beginPath(); ctx.moveTo(0,y); ctx.lineTo(w,y); ctx.stroke();
  }
  ctx.globalAlpha = 1;

  const arr = spark.ys;
  if(arr.length < 2) return;
  const min = Math.min(...arr);
  const max = Math.max(...arr);
  const span = (max-min) || 1;

  // line
  ctx.strokeStyle = "#FFFFFF";
  ctx.globalAlpha = 0.85;
  ctx.lineWidth = 2;

  ctx.beginPath();
  for(let idx=0; idx<arr.length; idx++){
    const x = (w*(idx/(arr.length-1)));
    const y = h - ((arr[idx]-min)/span)*(h-14) - 7;
    if(idx===0) ctx.moveTo(x,y);
    else ctx.lineTo(x,y);
  }
  ctx.stroke();

  // end dot
  const last = arr[arr.length-1];
  const ly = h - ((last-min)/span)*(h-14) - 7;
  ctx.globalAlpha = 1;
  ctx.beginPath();
  ctx.arc(w, ly, 4, 0, Math.PI*2);
  ctx.fillStyle = "#FFFFFF";
  ctx.fill();
}

function renderStations(kpi){
  const box = i("stations");
  const s = kpi.stations || {};
  const blocks = kpi.active_blocks || {};
  const now = Number(kpi.sim_time_s || 0);

  const stations = ["S1","S2","S3","S4","S5","S6"];
  let html = "";

  stations.forEach(st=>{
    const stx = s[st] || {};
    const ready = !!stx.ready;
    const busy  = !!stx.busy;
    const fault = !!stx.fault;

    let cls = "st";
    let tag = "idle";
    let dot = "warn";

    if(fault){ cls += " bad"; tag="fault"; dot="bad"; }
    else if(blocks[st]){ cls += " warn"; tag="blocked"; dot="warn"; }
    else if(busy){ cls += " good"; tag="busy"; dot="good"; }
    else if(ready){ cls += " warn"; tag="ready"; dot="warn"; }

    let extra = "";
    if(blocks[st]){
      const until = Number(blocks[st].until || 0);
      const rem = Math.max(0, until - now).toFixed(1);
      extra = " • " + rem + "s";
    }

    html += `
      <div class="${cls}">
        <div class="stName">${st}</div>
        <div class="stMeta">
          <span>ct: ${(stx.cycle_time_ms||0)} ms</span>
          <span>done: ${(stx.done||0)}</span>
        </div>
        <div class="stTag"><span class="dot ${dot}"></span>${tag}${extra}</div>
      </div>
    `;
  });

  box.innerHTML = html;
}

function renderBlocks(kpi){
  const box = i("blocks");
  const blocks = (kpi && kpi.active_blocks) ? kpi.active_blocks : {};
  const now = Number(kpi.sim_time_s || 0);
  const keys = Object.keys(blocks||{});

  if(!keys.length){
    box.textContent = "none";
    box.className = "muted";
    return;
  }

  keys.sort((a,b)=>{
    const ra = (blocks[a].until||0) - now;
    const rb = (blocks[b].until||0) - now;
    return rb - ra;
  });

  let out = "";
  keys.forEach(st=>{
    const info = blocks[st] || {};
    const until = Number(info.until || 0);
    const rem = Math.max(0, until - now);
    const reason = String(info.reason || "-");
    out += `${st} • ${reason} • ${rem.toFixed(1)}s remaining\\n`;
  });

  box.textContent = out.trim();
  box.className = "mono";
}

function updateUI(k){
  const state = k.plc_state ?? "-";
  const t = Number(k.sim_time_s ?? 0);
  const pkg = Number(k.packages_completed ?? 0);

  i("pill_state").textContent = "state: " + state;
  i("pill_time").textContent  = "t: " + t.toFixed(2) + "s";
  i("pill_pkg").textContent   = "pkg: " + pkg;

  const faultAny = Number(k.fault_any ?? 0) === 1;
  i("fault_dot").className = "dot " + (faultAny ? "bad" : "good");
  i("fault_txt").textContent = "fault: " + (faultAny ? "YES" : "no");
  i("badge_fault").className = "badge " + (faultAny ? "pulse bad" : "");

  animateNumber(i("kpi_packages"), pkg, {dur:450, decimals:0});
  animateNumber(i("kpi_tpm"), Number(k.throughput_per_min ?? 0), {dur:450, decimals:2});
  animateNumber(i("kpi_yield"), Number(k.yield_pct ?? 0), {dur:450, decimals:1, suffix:"%"});
  animateNumber(i("kpi_avail"), Number(k.availability ?? 0), {dur:450, decimals:1, suffix:"%"});

  i("kpi_accept").textContent = String(k.accept ?? 0);
  i("kpi_reject").textContent = String(k.reject ?? 0);
  i("kpi_dt").textContent = String(Number(k.downtime_s ?? 0).toFixed(1));

  i("kpi_batch").textContent = String(k.batch_id ?? "-");
  i("kpi_recipe").textContent = String(k.recipe_id ?? "-");

  i("kpi_bneck").textContent = "bneck: " + (k.bottleneck_station ?? "-");
  i("kpi_idle").textContent = "idle: " + Number(k.line_idle_pct ?? 0).toFixed(1) + "%";

  pushSpark(Number(k.throughput_per_min ?? 0));
  drawSpark();

  renderBlocks(k);
  renderStations(k);
}

async function tick(){
  const k = await get("/kpi");
  i("kpi").textContent = JSON.stringify(k,null,2);
  updateUI(k);

  const r = await get("/runs");
  i("runs").textContent = JSON.stringify(r,null,2);
}

loadParams();
tick();
setInterval(tick, 500);
</script>
</body>
</html>
"""


class _OptHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return  # silence HTTP logs

    def _send(self, code: int, body: bytes, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj):
        self._send(code, json.dumps(obj).encode("utf-8"))

    def _read_json(self):
        n = int(self.headers.get("Content-Length", "0"))
        if n <= 0:
            return {}
        raw = self.rfile.read(n).decode("utf-8")
        return json.loads(raw) if raw else {}

    def do_GET(self):
        if self.path.startswith("/kpi"):
            return self._json(200, get_kpi_snapshot())
        if self.path.startswith("/params"):
            return self._json(200, get_params())
        if self.path.startswith("/runs"):
            with _runs_lock:
                data = list(_runs)
                cur = _current_run
            return self._json(200, {"current_run": cur, "runs": data})
        if self.path == "/" or self.path.startswith("/index"):
            return self._send(200, HTML_PAGE.encode("utf-8"), ctype="text/html; charset=utf-8")
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path.startswith("/params"):
            patch = self._read_json()
            set_params(patch)
            return self._json(200, {"ok": True, "params": get_params()})

        if self.path.startswith("/run/start"):
            request_web_start()
            return self._json(200, {"ok": True})

        if self.path.startswith("/run/stop"):
            request_web_stop()
            return self._json(200, {"ok": True})

        if self.path.startswith("/run/reset"):
            request_web_reset()
            return self._json(200, {"ok": True})

        if self.path.startswith("/scenario/fault"):
            data = self._read_json()
            st = data.get("station", "S3")
            dur = float(data.get("duration_s", 8.0))
            with _trig_lock:
                _triggers.append({"type": "fault_request", "station": st, "duration_s": dur})
            return self._json(200, {"ok": True})

        if self.path.startswith("/scenario/maintenance"):
            data = self._read_json()
            st = data.get("station", "S4")
            dur = float(data.get("duration_s", 15.0))
            with _trig_lock:
                _triggers.append({"type": "maintenance_request", "station": st, "duration_s": dur})
            return self._json(200, {"ok": True})

        if self.path.startswith("/blocks/clear"):
            clear_blocks()
            return self._json(200, {"ok": True})

        return self._json(404, {"error": "not found"})


def start_server(host, port):
    srv = ThreadingHTTPServer((host, int(port)), _OptHandler)
    print(f"Dashboard listening on {host}:{port}")
    print(f"Open: http://127.0.0.1:{port}  (or http://localhost:{port})")
    srv.serve_forever()


if __name__ == "__main__":
    start_server(OPT_HOST, OPT_PORT)
