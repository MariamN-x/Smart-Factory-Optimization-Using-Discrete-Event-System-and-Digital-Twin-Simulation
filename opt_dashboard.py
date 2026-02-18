# pythonGateways/opt_dashboard.py
import os
import time
import uuid
import json
import csv
import threading
import sqlite3
import secrets
import hmac
import hashlib
import base64
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs
from html import escape as html_escape

# ----------------------------
# Config
# ----------------------------
OPT_HOST = "0.0.0.0"
OPT_PORT = 8055

STATIONS_DEFAULT = ["S1", "S2", "S3", "S4", "S5", "S6"]

# Auth / Security config
SESSION_TTL_S = 8 * 60 * 60  # 8 hours
CSRF_HEADER = "X-CSRF-Token"
COOKIE_NAME = "opt_sid"
LOGIN_RATE_LIMIT_WINDOW_S = 60
LOGIN_RATE_LIMIT_MAX = 8

# If you run behind TLS reverse proxy, set OPT_COOKIE_SECURE=1
COOKIE_SECURE = os.environ.get("OPT_COOKIE_SECURE", "0") == "1"

# ----------------------------
# Paths (write next to this file)
# ----------------------------
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
KPI_JSON_PATH = os.path.join(_BASE_DIR, "kpi_latest.json")
KPI_CSV_PATH = os.path.join(_BASE_DIR, "kpi_history.csv")

AUTH_DB_PATH = os.path.join(_BASE_DIR, "opt_auth.sqlite3")
SECRET_PATH = os.path.join(_BASE_DIR, ".opt_cookie_secret")

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

# in-memory login rate limit (per IP)
_rl_lock = threading.Lock()
_login_attempts = {}  # ip -> [timestamps]


# ============================================================
# Security helpers
# ============================================================
def _now() -> float:
    return time.time()


def _load_or_create_secret() -> bytes:
    """Keep cookie signing stable across restarts."""
    try:
        if os.path.exists(SECRET_PATH):
            with open(SECRET_PATH, "rb") as f:
                s = f.read().strip()
                if len(s) >= 32:
                    return s
        s = secrets.token_bytes(32)
        with open(SECRET_PATH, "wb") as f:
            f.write(s)
        try:
            os.chmod(SECRET_PATH, 0o600)
        except Exception:
            pass
        return s
    except Exception:
        # fallback (sessions will break after restart)
        return secrets.token_bytes(32)


_COOKIE_SIGNING_KEY = _load_or_create_secret()


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _b64u_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode((s + pad).encode("ascii"))


def _sign_cookie(sid: str) -> str:
    mac = hmac.new(_COOKIE_SIGNING_KEY, sid.encode("utf-8"), hashlib.sha256).digest()
    return _b64u(mac)


def _make_cookie_value(sid: str) -> str:
    return f"{sid}.{_sign_cookie(sid)}"


def _verify_cookie_value(val: str):
    """Returns sid if valid, else None."""
    if not val or "." not in val:
        return None
    sid, sig = val.split(".", 1)
    good = _sign_cookie(sid)
    if hmac.compare_digest(sig, good):
        return sid
    return None


def _pbkdf2_hash(password: str, salt: bytes | None = None) -> tuple[str, str]:
    if salt is None:
        salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return _b64u(dk), _b64u(salt)


def _pbkdf2_verify(password: str, stored_hash: str, stored_salt: str) -> bool:
    try:
        salt = _b64u_decode(stored_salt)
        want, _ = _pbkdf2_hash(password, salt=salt)
        return hmac.compare_digest(want, stored_hash)
    except Exception:
        return False


def _gen_csrf() -> str:
    return _b64u(secrets.token_bytes(24))


def _is_email(s: str) -> bool:
    if not s or len(s) > 200:
        return False
    return re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", s) is not None


# ============================================================
# DB
# ============================================================
def _db():
    con = sqlite3.connect(AUTH_DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def _db_init():
    os.makedirs(_BASE_DIR, exist_ok=True)
    con = _db()
    cur = con.cursor()

    # USERS (email + password only). no OTP/TOTP.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      email TEXT UNIQUE NOT NULL,
      pw_hash TEXT NOT NULL,
      pw_salt TEXT NOT NULL,
      role TEXT NOT NULL DEFAULT 'operator',
      created_at REAL NOT NULL,
      last_login REAL,
      locked_until REAL NOT NULL DEFAULT 0,
      failed_attempts INTEGER NOT NULL DEFAULT 0
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
      sid TEXT PRIMARY KEY,
      user_id INTEGER NOT NULL,
      csrf TEXT NOT NULL,
      created_at REAL NOT NULL,
      expires_at REAL NOT NULL,
      ip TEXT NOT NULL,
      ua TEXT NOT NULL,
      FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      ts REAL NOT NULL,
      user_id INTEGER,
      ip TEXT NOT NULL,
      action TEXT NOT NULL,
      meta TEXT NOT NULL
    )
    """)

    con.commit()

    # create demo user if empty
    cur.execute("SELECT COUNT(*) AS c FROM users")
    c = int(cur.fetchone()["c"])
    if c == 0:
        demo_email = "sama2206518@miuegypt.edu.eg"
        demo_pw = "53408177" + secrets.token_hex(3)
        h, s = _pbkdf2_hash(demo_pw)
        cur.execute(
            "INSERT INTO users(email,pw_hash,pw_salt,role,created_at) VALUES(?,?,?,?,?)",
            (demo_email.lower(), h, s, "operator", _now()),
        )
        con.commit()
        print("====================================================")
        print("Created DEMO user (first run):")
        print(f"  email:    {demo_email}")
        print(f"  password: {demo_pw}")
        print("Change it later by editing the DB.")
        print("====================================================")

    con.close()


def _audit(user_id: int | None, ip: str, action: str, meta: dict):
    try:
        con = _db()
        con.execute(
            "INSERT INTO audit(ts,user_id,ip,action,meta) VALUES(?,?,?,?,?)",
            (_now(), user_id, ip, action, json.dumps(meta, ensure_ascii=False)),
        )
        con.commit()
        con.close()
    except Exception:
        pass


def _session_create(user_id: int, ip: str, ua: str) -> tuple[str, str]:
    sid = _b64u(secrets.token_bytes(24))
    csrf = _gen_csrf()
    now = _now()
    exp = now + SESSION_TTL_S
    con = _db()
    con.execute(
        "INSERT INTO sessions(sid,user_id,csrf,created_at,expires_at,ip,ua) VALUES(?,?,?,?,?,?,?)",
        (sid, user_id, csrf, now, exp, ip, ua[:300]),
    )
    con.commit()
    con.close()
    return sid, csrf


def _session_get(sid: str):
    con = _db()
    row = con.execute("SELECT * FROM sessions WHERE sid=?", (sid,)).fetchone()
    con.close()
    return row


def _session_delete(sid: str):
    con = _db()
    con.execute("DELETE FROM sessions WHERE sid=?", (sid,))
    con.commit()
    con.close()


def _session_touch(sid: str):
    # sliding expiration (simple): extend by 1 hour each request up to SESSION_TTL_S.
    try:
        con = _db()
        row = con.execute("SELECT expires_at, created_at FROM sessions WHERE sid=?", (sid,)).fetchone()
        if not row:
            con.close()
            return
        now = _now()
        created = float(row["created_at"])
        max_exp = created + SESSION_TTL_S
        new_exp = min(max_exp, now + 60 * 60)
        con.execute("UPDATE sessions SET expires_at=? WHERE sid=?", (new_exp, sid))
        con.commit()
        con.close()
    except Exception:
        pass


def _user_get_by_email(email: str):
    con = _db()
    row = con.execute("SELECT * FROM users WHERE email=?", (email.lower(),)).fetchone()
    con.close()
    return row


def _user_get(user_id: int):
    con = _db()
    row = con.execute("SELECT * FROM users WHERE id=?", (int(user_id),)).fetchone()
    con.close()
    return row


def _user_set_login_success(user_id: int):
    con = _db()
    con.execute(
        "UPDATE users SET last_login=?, failed_attempts=0, locked_until=0 WHERE id=?",
        (_now(), int(user_id)),
    )
    con.commit()
    con.close()


def _user_fail_attempt(user_id: int | None, lock_s: int = 0):
    if not user_id:
        return
    con = _db()
    row = con.execute("SELECT failed_attempts FROM users WHERE id=?", (int(user_id),)).fetchone()
    n = int(row["failed_attempts"]) if row else 0
    n += 1
    locked_until = 0
    if lock_s > 0:
        locked_until = _now() + lock_s
    con.execute(
        "UPDATE users SET failed_attempts=?, locked_until=? WHERE id=?",
        (n, locked_until, int(user_id)),
    )
    con.commit()
    con.close()


# ============================================================
# Public API for PLC (unchanged)
# ============================================================
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
        "stations": stations_map,
        "active_blocks": ov.get("active_blocks", {}),
        "operators_total": ov.get("operators_total", 0),
        "operators_required": ov.get("operators_required", {}),
    }


# ============================================================
# HTML (login + dashboard)
# ============================================================
LOGIN_CSS = r"""
*{margin:0;padding:0;box-sizing:border-box;font-family:'Segoe UI',Tahoma,Geneva,Verdana,sans-serif}
body{background:linear-gradient(135deg,#0a192f 0%,#112240 100%);min-height:100vh;display:flex;justify-content:center;align-items:center;padding:20px}
.container{display:flex;width:100%;max-width:1000px;background:rgba(255,255,255,.92);border-radius:20px;box-shadow:0 15px 35px rgba(0,0,0,.35);overflow:hidden}
.factory-section{flex:1;background:linear-gradient(120deg,#009fe3 0%,#0066b3 100%);display:flex;flex-direction:column;justify-content:center;align-items:center;padding:40px;position:relative;overflow:hidden}
.login-section{flex:1;padding:50px 40px;display:flex;flex-direction:column;justify-content:center}
.siemens-logo{position:absolute;top:25px;left:25px;font-weight:700;font-size:28px;color:#fff;display:flex;align-items:center}
.siemens-logo::after{content:"";display:block;width:12px;height:12px;background:#fff;border-radius:50%;margin-left:8px}
.factory-scene{width:280px;height:320px;position:relative;margin:20px 0;perspective:1000px}
.conveyor-belt{width:100%;height:40px;background:linear-gradient(90deg,#2d3748 0%,#4a5568 25%,#2d3748 50%,#4a5568 75%,#2d3748 100%);position:absolute;bottom:0;left:0;border-radius:20px;animation:conveyor-move 2s linear infinite}
.conveyor-belt::before{content:"";position:absolute;top:8px;left:0;right:0;height:24px;background:linear-gradient(90deg,transparent 20%,rgba(0,180,230,.3) 50%,transparent 80%);animation:conveyor-glow 2s linear infinite}
.printer-base{width:180px;height:30px;background:linear-gradient(135deg,#4a5568 0%,#2d3748 100%);border-radius:8px 8px 4px 4px;position:absolute;bottom:80px;left:50%;transform:translateX(-50%);box-shadow:0 5px 15px rgba(0,0,0,.4);border:2px solid #1a202c}
.printer-frame{position:absolute;bottom:120px;width:160px;height:150px;left:50%;transform:translateX(-50%)}
.frame-vertical{width:10px;height:100%;background:linear-gradient(135deg,#718096 0%,#4a5568 100%);position:absolute}
.frame-vertical.left{left:0;border-radius:5px 0 0 5px}
.frame-vertical.right{right:0;border-radius:0 5px 5px 0}
.frame-horizontal{width:100%;height:10px;background:linear-gradient(135deg,#718096 0%,#4a5568 100%);position:absolute;top:0;border-radius:5px 5px 0 0}
.frame-bottom{width:100%;height:10px;background:linear-gradient(135deg,#718096 0%,#4a5568 100%);position:absolute;bottom:0;border-radius:0 0 5px 5px}
.print-head{width:35px;height:25px;background:linear-gradient(135deg,#3b82f6 0%,#2563eb 100%);border-radius:6px;position:absolute;bottom:180px;left:90px;box-shadow:0 0 20px rgba(59,130,246,.6);animation:print-head-move 4s ease-in-out infinite;display:flex;justify-content:center;align-items:center;border:2px solid #1e40af;z-index:10}
.print-head::before{content:"";width:8px;height:8px;background:#fbbf24;border-radius:50%;position:absolute;bottom:-5px;box-shadow:0 0 15px #fbbf24}
.print-nozzle{width:6px;height:15px;background:#94a3b8;position:absolute;bottom:-15px;left:50%;transform:translateX(-50%);border-radius:3px;border:1px solid #718096}
.nozzle-tip{width:4px;height:8px;background:#fbbf24;position:absolute;bottom:-23px;left:50%;transform:translateX(-50%);border-radius:2px;box-shadow:0 0 10px #fbbf24;animation:nozzle-glow 1s infinite alternate}
.print-bed{width:140px;height:15px;background:linear-gradient(135deg,#1e40af 0%,#1e3a8a 100%);border-radius:8px;position:absolute;bottom:120px;left:50%;transform:translateX(-50%);box-shadow:0 4px 12px rgba(30,64,175,.5);border:2px solid #1e3a8a;overflow:hidden}
.print-progress{width:0%;height:100%;background:linear-gradient(90deg,#10b981,#059669);position:absolute;bottom:0;left:0;animation:print-progress 8s ease-in-out infinite}
.robot-inspector{width:50px;height:70px;position:absolute;bottom:40px;right:30px;animation:robot-walk 8s ease-in-out infinite;z-index:20}
.robot-head-inspector{width:32px;height:32px;background:#10b981;border-radius:50%;position:absolute;top:0;left:50%;transform:translateX(-50%);border:3px solid #065f46;display:flex;justify-content:center;align-items:center;box-shadow:0 0 15px rgba(16,185,129,.5)}
.robot-eye-inspector{width:9px;height:9px;background:#0f172a;border-radius:50%;margin:0 5px;position:relative;overflow:hidden}
.robot-eye-inspector::after{content:"";position:absolute;width:5px;height:5px;background:#fefefe;border-radius:50%;top:2px;left:2px}
.robot-body-inspector{width:40px;height:28px;background:#10b981;border-radius:10px;position:absolute;bottom:0;left:50%;transform:translateX(-50%);border:3px solid #065f46;box-shadow:0 4px 10px rgba(6,95,70,.5)}
.welcome-text{color:#fff;text-align:center;font-size:26px;font-weight:600;margin-top:20px;text-shadow:0 2px 10px rgba(0,0,0,.3);line-height:1.4}
.welcome-text span{display:block;font-size:17px;font-weight:300;margin-top:8px;opacity:.9}
h1{font-size:34px;color:#0a192f;margin-bottom:10px;font-weight:700}
.subtitle{color:#4a6580;font-size:16px;margin-bottom:22px;line-height:1.5}
.input-group{margin-bottom:16px}
.input-group label{display:block;margin-bottom:6px;font-weight:500;color:#0a192f;font-size:14px}
.input-group input{width:100%;padding:14px 16px;border:2px solid #d1d9e6;border-radius:12px;font-size:15px;transition:all .2s;background:#f8fafc}
.input-group input:focus{border-color:#009fe3;box-shadow:0 0 0 3px rgba(0,159,227,.2);outline:none}
.login-btn{background:linear-gradient(120deg,#009fe3 0%,#0066b3 100%);color:#fff;border:none;width:100%;padding:16px;border-radius:12px;font-size:16px;font-weight:600;cursor:pointer;transition:all .2s;box-shadow:0 6px 20px rgba(0,102,179,.4)}
.login-btn:hover{transform:translateY(-1px);box-shadow:0 8px 25px rgba(0,102,179,.55)}
.msg{margin-top:12px;padding:10px 12px;border-radius:12px;border:1px solid #d1d9e6;background:#fff;display:none}
.msg.err{border-color:#fb7185;color:#9f1239;background:#fff1f2}
.msg.ok{border-color:#34d399;color:#065f46;background:#ecfdf5}
.small{margin-top:14px;color:#64748b;font-size:13px}
@keyframes conveyor-move{0%{background-position:0 0}100%{background-position:40px 0}}
@keyframes conveyor-glow{0%{opacity:.3}50%{opacity:.8}100%{opacity:.3}}
@keyframes print-head-move{0%,100%{left:70px;transform:translateX(-50%) rotate(0)}25%{left:130px;transform:translateX(-50%) rotate(5deg)}50%{left:100px;transform:translateX(-50%) rotate(0)}75%{left:80px;transform:translateX(-50%) rotate(-5deg)}}
@keyframes nozzle-glow{from{box-shadow:0 0 5px #fbbf24}to{box-shadow:0 0 15px #fbbf24,0 0 25px #fbbf24}}
@keyframes print-progress{0%{width:0%}30%{width:40%}60%{width:80%}80%{width:100%}100%{width:0%}}
@keyframes robot-walk{0%,100%{transform:translateX(0) translateY(0)}20%{transform:translateX(-30px) translateY(-5px)}40%{transform:translateX(0) translateY(0)}60%{transform:translateX(30px) translateY(-5px)}80%{transform:translateX(0) translateY(0)}}
@media(max-width:768px){.container{flex-direction:column}.factory-section{padding:30px}.login-section{padding:34px 26px}}
"""

LOGIN_JS = r"""
(function(){
  const form = document.getElementById('loginForm');
  const msg = document.getElementById('msg');

  function show(text, ok){
    msg.textContent = text;
    msg.className = 'msg ' + (ok ? 'ok' : 'err');
    msg.style.display = 'block';
  }

  form.addEventListener('submit', async function(e){
    e.preventDefault();
    const email = document.getElementById('email').value.trim();
    const password = document.getElementById('password').value;
    const csrf = document.getElementById('csrf').value;

    try{
      const res = await fetch('/api/login', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({email, password, csrf})
      });

      const data = await res.json();
      if(!res.ok || !data.ok){
        show(data.error || 'login failed', false);
        return;
      }

      show('Access granted. Redirecting...', true);
      setTimeout(()=>{ window.location.href = data.next || '/'; }, 400);
    }catch(err){
      show('server error', false);
    }
  });
})();
"""

LOGIN_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Siemens 3D Printing Production - Login</title>
  <link rel="stylesheet" href="/static/login.css">
</head>
<body>
  <div class="container">
    <div class="factory-section">
      <div class="siemens-logo">SIEMENS</div>
      <div class="factory-scene">
        <div class="conveyor-belt"></div>

        <div class="printer-base"></div>
        <div class="printer-frame">
          <div class="frame-vertical left"></div>
          <div class="frame-vertical right"></div>
          <div class="frame-horizontal"></div>
          <div class="frame-bottom"></div>
          <div class="print-bed"><div class="print-progress"></div></div>
          <div class="print-head">
            <div class="print-nozzle"></div>
            <div class="nozzle-tip"></div>
          </div>
        </div>

        <div class="robot-inspector">
          <div class="robot-head-inspector">
            <div class="robot-eye-inspector"></div>
            <div class="robot-eye-inspector"></div>
          </div>
          <div class="robot-body-inspector"></div>
        </div>
      </div>

      <h2 class="welcome-text">Siemens 3D Printing Production
        <span>Automated Manufacturing Excellence</span>
      </h2>
    </div>

    <div class="login-section">
      <h1>Production Control Access</h1>
      <p class="subtitle">Sign in to open the optimization dashboard</p>

      <form id="loginForm">
        <input type="hidden" id="csrf" value="{csrf}">
        <div class="input-group">
          <label for="email">Operator Email</label>
          <input type="email" id="email" placeholder="operator@siemens.com" required>
        </div>

        <div class="input-group">
          <label for="password">Access Code</label>
          <input type="password" id="password" placeholder="••••••••" required>
        </div>

        <button type="submit" class="login-btn">Access Production System</button>
        <div id="msg" class="msg"></div>
      </form>

      <p class="small">© 2026 Siemens AG. All rights reserved.</p>
    </div>
  </div>

  <script src="/static/login.js"></script>
</body>
</html>
"""

# IMPORTANT FIX:
# DO NOT use .format() on this big HTML (it contains many { } in CSS/JS).
# We use tokens __CSRF__ and __CSRF_HEADER__ then .replace().
HTML_PAGE = ("""<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <meta name="csrf" content="__CSRF__"/>
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
    .title{font-size:22px; font-weight:750; letter-spacing:.2px}
    .subtitle{color:var(--muted); font-size:13px; margin-top:4px}

    .rightActions{display:flex; gap:10px; flex-wrap:wrap; justify-content:flex-end}
    .linkbtn{display:inline-flex;align-items:center;gap:8px;text-decoration:none}

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

    details summary{cursor:pointer;color: rgba(234,240,255,.9);user-select:none}
  </style>
</head>

<body>
  <div class="wrap">
    <div class="topbar">
      <div>
        <div class="title">Optimization Dashboard</div>
        <div class="subtitle">real-time KPI monitoring + scenario injection (fault / maintenance)</div>
      </div>

      <div class="rightActions">
        <button onclick="logout()">Logout</button>
      </div>
    </div>

    <div class="topbar" style="margin-top:-6px">
      <div></div>
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
function csrf(){
  const m = document.querySelector('meta[name="csrf"]');
  return m ? m.getAttribute('content') : '';
}
async function get(path){ return (await fetch(path,{cache:"no-store"})).json(); }
async function post(path,obj){
  return (await fetch(path,{
    method:"POST",
    headers:{
      "Content-Type":"application/json",
      "__CSRF_HEADER__": csrf()
    },
    body: JSON.stringify(obj||{})
  })).json();
}
async function logout(){
  try{
    await fetch('/api/logout', {method:'POST', headers:{"__CSRF_HEADER__": csrf()}});
  }catch(e){}
  window.location.href = '/login';
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

function animateNumber(el, next, opts={}){
  const dur = opts.dur ?? 450;
  const decimals = opts.decimals ?? 0;
  const suffix = opts.suffix ?? "";
  const prev = parseFloat(el.dataset.v ?? "0");
  const start = performance.now();

  function step(t){
    const p = Math.min(1, (t - start)/dur);
    const e = 1 - Math.pow(1-p, 3);
    const cur = prev + (next - prev)*e;
    el.textContent = cur.toFixed(decimals) + suffix;
    if(p < 1) requestAnimationFrame(step);
    else el.dataset.v = String(next);
  }
  requestAnimationFrame(step);
}

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
    out += `${st} • ${reason} • ${rem.toFixed(1)}s remaining\n`;
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
""").replace("__CSRF_HEADER__", CSRF_HEADER)


# ============================================================
# HTTP server (auth-aware)
# ============================================================
class _OptHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    # ---------- low-level helpers ----------
    def _security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        self.send_header("X-Frame-Options", "DENY")  # clickjacking
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; "
            "img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; form-action 'self'"
        )

    def _send(self, code: int, body: bytes, ctype="application/json; charset=utf-8", extra_headers: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self._security_headers()
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict):
        self._send(code, json.dumps(obj).encode("utf-8"))

    def _read_body(self) -> bytes:
        n = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(n) if n > 0 else b""

    def _read_json(self) -> dict:
        raw = self._read_body().decode("utf-8", errors="replace").strip()
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def _read_form(self) -> dict:
        raw = self._read_body().decode("utf-8", errors="replace")
        q = parse_qs(raw, keep_blank_values=True)
        return {k: (v[0] if isinstance(v, list) and v else "") for k, v in q.items()}

    def _cookies(self) -> dict:
        c = {}
        raw = self.headers.get("Cookie", "")
        for part in raw.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                c[k.strip()] = v.strip()
        return c

    def _set_cookie(self, name: str, value: str, max_age: int, http_only=True, same_site="Strict"):
        parts = [f"{name}={value}", f"Max-Age={max_age}", "Path=/", f"SameSite={same_site}"]
        if http_only:
            parts.append("HttpOnly")
        if COOKIE_SECURE:
            parts.append("Secure")
        self.send_header("Set-Cookie", "; ".join(parts))

    def _clear_cookie(self, name: str):
        self.send_header("Set-Cookie", f"{name}=; Max-Age=0; Path=/; SameSite=Strict")

    def _ip(self) -> str:
        return self.client_address[0] if self.client_address else "0.0.0.0"

    def _ua(self) -> str:
        return (self.headers.get("User-Agent", "") or "")[:300]

    # ---------- auth helpers ----------
    def _current_session(self):
        ck = self._cookies().get(COOKIE_NAME, "")
        sid = _verify_cookie_value(ck)
        if not sid:
            return None
        row = _session_get(sid)
        if not row:
            return None
        if float(row["expires_at"]) < _now():
            _session_delete(sid)
            return None
        # bind session to ip + UA (basic anti-hijack)
        if row["ip"] != self._ip() or row["ua"] != self._ua():
            return None
        _session_touch(sid)
        return row

    def _require_auth(self, wants_html=False):
        s = self._current_session()
        if s:
            return s
        if wants_html:
            self.send_response(302)
            self.send_header("Location", "/login")
            self._security_headers()
            self.end_headers()
            return None
        self._json(401, {"ok": False, "error": "unauthorized"})
        return None

    def _require_csrf(self, session_row):
        sent = self.headers.get(CSRF_HEADER, "")
        if not sent or not session_row:
            return False
        return hmac.compare_digest(str(session_row["csrf"]), sent)

    # ---------- rate limit ----------
    def _rate_limit_ok(self) -> bool:
        ip = self._ip()
        now = _now()
        with _rl_lock:
            arr = _login_attempts.get(ip, [])
            arr = [t for t in arr if now - t < LOGIN_RATE_LIMIT_WINDOW_S]
            if len(arr) >= LOGIN_RATE_LIMIT_MAX:
                _login_attempts[ip] = arr
                return False
            arr.append(now)
            _login_attempts[ip] = arr
            return True

    # ============================================================
    # Routes
    # ============================================================
    def do_GET(self):
        # static assets
        if self.path == "/static/login.css":
            return self._send(200, LOGIN_CSS.encode("utf-8"), ctype="text/css; charset=utf-8")
        if self.path == "/static/login.js":
            return self._send(200, LOGIN_JS.encode("utf-8"), ctype="application/javascript; charset=utf-8")

        # login page (no auth)
        if self.path == "/login":
            csrf0 = _gen_csrf()
            body = LOGIN_PAGE.format(csrf=csrf0).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self._security_headers()
            self._set_cookie("csrf0", csrf0, max_age=10 * 60, http_only=True, same_site="Strict")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # protected HTML pages
        if self.path == "/" or self.path.startswith("/index") or self.path.startswith("/dashboard"):
            s = self._require_auth(wants_html=True)
            if not s:
                return
            page = HTML_PAGE.replace("__CSRF__", str(s["csrf"])).encode("utf-8")
            return self._send(200, page, ctype="text/html; charset=utf-8")

        # protected APIs
        if self.path.startswith("/kpi"):
            s = self._require_auth()
            if not s:
                return
            return self._json(200, get_kpi_snapshot())

        if self.path.startswith("/params"):
            s = self._require_auth()
            if not s:
                return
            return self._json(200, get_params())

        if self.path.startswith("/runs"):
            s = self._require_auth()
            if not s:
                return
            with _runs_lock:
                data = list(_runs)
                cur = _current_run
            return self._json(200, {"current_run": cur, "runs": data})

        return self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        # ---- login (no session yet) ----
        if self.path.startswith("/api/login"):
            if not self._rate_limit_ok():
                return self._json(429, {"ok": False, "error": "too many attempts. wait 60s."})

            data = self._read_json()
            email = (data.get("email") or "").strip().lower()
            password = (data.get("password") or "")
            csrf = (data.get("csrf") or "").strip()

            # login CSRF: compare hidden token with cookie csrf0
            csrf0 = self._cookies().get("csrf0", "")
            if not csrf0 or not csrf or not hmac.compare_digest(csrf0, csrf):
                return self._json(403, {"ok": False, "error": "csrf failed. refresh page."})

            if not _is_email(email) or not password or len(password) > 300:
                return self._json(400, {"ok": False, "error": "invalid credentials"})

            user = _user_get_by_email(email)
            if not user:
                _audit(None, self._ip(), "login_fail", {"email": email, "why": "no_user"})
                return self._json(401, {"ok": False, "error": "invalid credentials"})

            if float(user["locked_until"] or 0) > _now():
                _audit(int(user["id"]), self._ip(), "login_fail", {"email": email, "why": "locked"})
                return self._json(403, {"ok": False, "error": "account locked. try later."})

            if not _pbkdf2_verify(password, str(user["pw_hash"]), str(user["pw_salt"])):
                lock_s = 60 if int(user["failed_attempts"] or 0) >= 6 else 0
                _user_fail_attempt(int(user["id"]), lock_s=lock_s)
                _audit(int(user["id"]), self._ip(), "login_fail", {"email": email, "why": "bad_pw"})
                return self._json(401, {"ok": False, "error": "invalid credentials"})

            _user_set_login_success(int(user["id"]))
            sid, csrf_sess = _session_create(int(user["id"]), self._ip(), self._ua())
            _audit(int(user["id"]), self._ip(), "login_ok", {})

            cookie_val = _make_cookie_value(sid)
            body = json.dumps({"ok": True, "next": "/"}).encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self._security_headers()
            self._set_cookie(COOKIE_NAME, cookie_val, max_age=SESSION_TTL_S, http_only=True, same_site="Strict")
            self._clear_cookie("csrf0")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # ---- logout ----
        if self.path.startswith("/api/logout"):
            s = self._current_session()
            if s:
                if not self._require_csrf(s):
                    return self._json(403, {"ok": False, "error": "csrf"})
                _audit(int(s["user_id"]), self._ip(), "logout", {})
                _session_delete(str(s["sid"]))
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self._security_headers()
            self._clear_cookie(COOKIE_NAME)
            body = b'{"ok":true}'
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # ---- protected POST endpoints below ----
        s = self._require_auth()
        if not s:
            return
        if not self._require_csrf(s):
            return self._json(403, {"ok": False, "error": "csrf"})

        if self.path.startswith("/params"):
            patch = self._read_json()
            set_params(patch)
            _audit(int(s["user_id"]), self._ip(), "set_params", {"keys": list(patch.keys())})
            return self._json(200, {"ok": True, "params": get_params()})

        if self.path.startswith("/run/start"):
            request_web_start()
            _audit(int(s["user_id"]), self._ip(), "run_start_req", {})
            return self._json(200, {"ok": True})

        if self.path.startswith("/run/stop"):
            request_web_stop()
            _audit(int(s["user_id"]), self._ip(), "run_stop_req", {})
            return self._json(200, {"ok": True})

        if self.path.startswith("/run/reset"):
            request_web_reset()
            _audit(int(s["user_id"]), self._ip(), "run_reset_req", {})
            return self._json(200, {"ok": True})

        if self.path.startswith("/scenario/fault"):
            data = self._read_json()
            st = str(data.get("station", "S3"))
            dur = float(data.get("duration_s", 8.0))
            with _trig_lock:
                _triggers.append({"type": "fault_request", "station": st, "duration_s": dur})
            _audit(int(s["user_id"]), self._ip(), "inject_fault_req", {"station": st, "duration_s": dur})
            return self._json(200, {"ok": True})

        if self.path.startswith("/scenario/maintenance"):
            data = self._read_json()
            st = str(data.get("station", "S4"))
            dur = float(data.get("duration_s", 15.0))
            with _trig_lock:
                _triggers.append({"type": "maintenance_request", "station": st, "duration_s": dur})
            _audit(int(s["user_id"]), self._ip(), "maintenance_req", {"station": st, "duration_s": dur})
            return self._json(200, {"ok": True})

        if self.path.startswith("/blocks/clear"):
            clear_blocks()
            _audit(int(s["user_id"]), self._ip(), "clear_blocks", {})
            return self._json(200, {"ok": True})

        return self._json(404, {"ok": False, "error": "not found"})


def start_server(host, port):
    _db_init()
    srv = ThreadingHTTPServer((host, int(port)), _OptHandler)
    print(f"Dashboard listening on {host}:{port}")
    print(f"Open: http://127.0.0.1:{port}  (or http://localhost:{port})")
    srv.serve_forever()


if __name__ == "__main__":
    start_server(OPT_HOST, OPT_PORT)

