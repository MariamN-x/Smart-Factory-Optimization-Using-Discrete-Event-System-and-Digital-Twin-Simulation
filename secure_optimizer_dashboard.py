#!/usr/bin/env python3
"""
secure_login_gateway.py

Front-door login + security gateway for an existing Flask dashboard.

Fixes added (important):
- WAF now normalizes/decodes URLs before matching patterns (handles %xx and + spaces)
- WAF checks BOTH raw + decoded path (catches bypass attempts)
- Small extra encoded traversal separators included
"""

import os
import time
import json
import hmac
import base64
import hashlib
import secrets
import sqlite3
import threading
import re
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote_plus
from typing import Optional, Dict, Any, Tuple, List


# =============================================================================
# Config
# =============================================================================
GATEWAY_HOST = os.environ.get("GATEWAY_HOST", "0.0.0.0")
GATEWAY_PORT = int(os.environ.get("GATEWAY_PORT", "8055"))

BACKEND_HOST = os.environ.get("BACKEND_HOST", "127.0.0.1")
BACKEND_PORT = int(os.environ.get("BACKEND_PORT", "8050"))

# Cookie + sessions
SESSION_TTL_S = int(os.environ.get("SESSION_TTL_S", str(8 * 60 * 60)))
COOKIE_NAME = "optgw_sid"
COOKIE_SECURE = os.environ.get("OPT_COOKIE_SECURE", "0") == "1"  # set to 1 if behind HTTPS proxy
SAMESITE = "Lax"  # "Strict" is stronger, "Lax" is less annoying

# Login rate limit
LOGIN_RATE_LIMIT_WINDOW_S = 60
LOGIN_RATE_LIMIT_MAX = 8

# Simple WAF/IDS
MAX_BODY_BYTES = int(os.environ.get("OPT_MAX_BODY_BYTES", str(256 * 1024)))  # 256KB
MAX_PATH_LEN = 2048
MAX_HEADER_VALUE_LEN = 4096

IDS_WINDOW_S = 60
IDS_MAX_HITS = 25
IDS_BLOCK_S = 10 * 60

# RBAC
ROLES = ("admin", "engineer", "operator", "viewer")

# Role policy for HTTP methods and specific paths
ROLE_ALLOW_POST_PREFIX = {
    "viewer": [],
    "operator": [
        "/api/save-full-config",
        "/api/save-resources-config",
        "/api/save-maintenance-config",
        "/api/save-energy-config",
    ],
    "engineer": [
        "/api/save-full-config",
        "/api/save-resources-config",
        "/api/save-maintenance-config",
        "/api/save-energy-config",
        "/api/reset-config",
    ],
    "admin": ["*"],
}

# =============================================================================
# Paths (next to this file)
# =============================================================================
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AUTH_DB_PATH = os.path.join(_BASE_DIR, "optgw_auth.sqlite3")
SECRET_PATH = os.path.join(_BASE_DIR, ".optgw_cookie_secret")


# =============================================================================
# Login UI
# =============================================================================
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
      setTimeout(()=>{ window.location.href = data.next || '/'; }, 350);
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

ADMIN_PAGE = """<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Admin - Users</title>
  <style>
    body{font-family:Segoe UI,Arial;background:#0b1220;color:#eaf0ff;margin:0;padding:20px}
    .card{max-width:1100px;margin:0 auto;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);border-radius:16px;padding:16px}
    h1{margin:0 0 10px 0}
    input,select,button{padding:10px 12px;border-radius:10px;border:1px solid rgba(255,255,255,.14);background:rgba(0,0,0,.25);color:#eaf0ff}
    button{cursor:pointer}
    table{width:100%;border-collapse:collapse;margin-top:12px}
    th,td{padding:10px;border-bottom:1px solid rgba(255,255,255,.12);font-size:13px}
    .row{display:flex;gap:10px;flex-wrap:wrap}
    .muted{opacity:.75}
  </style>
</head>
<body>
  <div class="card">
    <div class="row" style="justify-content:space-between;align-items:center">
      <div>
        <h1>Admin: Users</h1>
        <div class="muted">create users, set roles, disable, reset passwords</div>
      </div>
      <div class="row">
        <button onclick="goDash()">Dashboard</button>
        <button onclick="logout()">Logout</button>
      </div>
    </div>

    <hr style="border:none;border-top:1px solid rgba(255,255,255,.12);margin:14px 0"/>

    <div class="row">
      <input id="email" placeholder="user@example.com" style="flex:1;min-width:240px"/>
      <select id="role">
        <option>viewer</option><option>operator</option><option>engineer</option><option>admin</option>
      </select>
      <input id="pw" type="password" placeholder="Strong password" style="flex:1;min-width:220px"/>
      <button onclick="createUser()">Create</button>
      <button onclick="loadUsers()">Reload</button>
    </div>
    <div id="msg" class="muted" style="margin-top:10px"></div>

    <div style="overflow:auto">
      <table>
        <thead>
          <tr><th>id</th><th>email</th><th>role</th><th>active</th><th>actions</th></tr>
        </thead>
        <tbody id="tbody"></tbody>
      </table>
    </div>
  </div>

<script>
async function jget(p){ return (await fetch(p,{cache:"no-store"})).json(); }
async function jpost(p,obj){
  return (await fetch(p,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(obj||{})})).json();
}
function setMsg(t){ document.getElementById("msg").textContent = t; }
function goDash(){ window.location.href="/"; }

async function logout(){
  await jpost("/api/logout",{});
  window.location.href="/login";
}

async function loadUsers(){
  setMsg("loading...");
  const r = await jget("/api/admin/users");
  if(!r.ok){ setMsg(r.error||"failed"); return; }
  const tb = document.getElementById("tbody");
  tb.innerHTML = "";
  (r.users||[]).forEach(u=>{
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${u.id}</td>
      <td>${u.email}</td>
      <td>
        <select id="role_${u.id}">
          <option ${u.role==="viewer"?"selected":""}>viewer</option>
          <option ${u.role==="operator"?"selected":""}>operator</option>
          <option ${u.role==="engineer"?"selected":""}>engineer</option>
          <option ${u.role==="admin"?"selected":""}>admin</option>
        </select>
      </td>
      <td>${u.is_active ? "yes":"no"}</td>
      <td>
        <button onclick="setRole(${u.id})">set role</button>
        <button onclick="toggle(${u.id},${u.is_active?0:1})">${u.is_active?"disable":"enable"}</button>
        <button onclick="resetPw(${u.id})">reset pw</button>
      </td>
    `;
    tb.appendChild(tr);
  });
  setMsg("ok");
}

async function createUser(){
  const email = document.getElementById("email").value.trim();
  const role = document.getElementById("role").value;
  const pw = document.getElementById("pw").value;
  const r = await jpost("/api/admin/users",{email,role,password:pw});
  setMsg(r.ok ? "created" : (r.error||"failed"));
  if(r.ok){
    document.getElementById("email").value="";
    document.getElementById("pw").value="";
    await loadUsers();
  }
}

async function setRole(id){
  const role = document.getElementById("role_"+id).value;
  const r = await jpost("/api/admin/users/role",{user_id:id,role});
  if(!r.ok) alert(r.error||"failed");
  await loadUsers();
}

async function toggle(id, active){
  const r = await jpost("/api/admin/users/disable",{user_id:id,is_active:active});
  if(!r.ok) alert(r.error||"failed");
  await loadUsers();
}

async function resetPw(id){
  const pw = prompt("new strong password:");
  if(!pw) return;
  const r = await jpost("/api/admin/users/reset-pw",{user_id:id,new_password:pw});
  if(!r.ok) alert(r.error||"failed");
  else alert("password reset done");
  await loadUsers();
}

loadUsers();
</script>
</body>
</html>
"""


# =============================================================================
# Utilities / crypto
# =============================================================================
def _now() -> float:
    return time.time()


def _load_or_create_secret() -> bytes:
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


def _verify_cookie_value(val: str) -> Optional[str]:
    if not val or "." not in val:
        return None
    sid, sig = val.split(".", 1)
    good = _sign_cookie(sid)
    if hmac.compare_digest(sig, good):
        return sid
    return None


def _pbkdf2_hash(password: str, salt: Optional[bytes] = None) -> Tuple[str, str]:
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


def _password_strength(password: str) -> Tuple[bool, str]:
    if not isinstance(password, str):
        return False, "password invalid"
    if len(password) < 12:
        return False, "min length is 12"
    if len(password) > 300:
        return False, "password too long"
    if not re.search(r"[a-z]", password):
        return False, "must include lowercase"
    if not re.search(r"[A-Z]", password):
        return False, "must include uppercase"
    if not re.search(r"[0-9]", password):
        return False, "must include number"
    if not re.search(r"[^A-Za-z0-9]", password):
        return False, "must include special char"
    common = {"password", "password123", "123456789", "qwerty123", "admin123", "letmein123", "welcome123"}
    if password.lower() in common:
        return False, "too common"
    return True, "ok"


# =============================================================================
# DB
# =============================================================================
def _db() -> sqlite3.Connection:
    con = sqlite3.connect(AUTH_DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def _db_init() -> None:
    os.makedirs(_BASE_DIR, exist_ok=True)
    con = _db()
    cur = con.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      email TEXT UNIQUE NOT NULL,
      pw_hash TEXT NOT NULL,
      pw_salt TEXT NOT NULL,
      role TEXT NOT NULL DEFAULT 'viewer',
      is_active INTEGER NOT NULL DEFAULT 1,
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

    c = int(cur.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"])
    if c == 0:
        env_email = (os.environ.get("OPT_ADMIN_EMAIL", "") or "").strip().lower()
        env_pw = os.environ.get("OPT_ADMIN_PASSWORD", "") or ""

        if not _is_email(env_email):
            env_email = "admin@siemens.local"

        if env_pw:
            ok, why = _password_strength(env_pw)
            if not ok:
                print("OPT_ADMIN_PASSWORD is weak:", why)
                env_pw = ""

        if not env_pw:
            env_pw = "A!" + secrets.token_urlsafe(14) + "9z#"

        h, s = _pbkdf2_hash(env_pw)
        cur.execute(
            "INSERT INTO users(email,pw_hash,pw_salt,role,is_active,created_at) VALUES(?,?,?,?,?,?)",
            (env_email, h, s, "admin", 1, _now()),
        )
        con.commit()

        print("====================================================")
        print("Created ADMIN user (first run):")
        print("  email:   ", env_email)
        print("  password:", env_pw)
        print("Change it later using /admin or DB.")
        print("====================================================")

    con.close()


def _audit(user_id: Optional[int], ip: str, action: str, meta: Dict[str, Any]) -> None:
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


def _user_set_login_success(user_id: int) -> None:
    con = _db()
    con.execute(
        "UPDATE users SET last_login=?, failed_attempts=0, locked_until=0 WHERE id=?",
        (_now(), int(user_id)),
    )
    con.commit()
    con.close()


def _user_fail_attempt(user_id: Optional[int], lock_s: int = 0) -> None:
    if not user_id:
        return
    con = _db()
    row = con.execute("SELECT failed_attempts FROM users WHERE id=?", (int(user_id),)).fetchone()
    n = int(row["failed_attempts"]) if row else 0
    n += 1
    locked_until = _now() + lock_s if lock_s > 0 else 0
    con.execute(
        "UPDATE users SET failed_attempts=?, locked_until=? WHERE id=?",
        (n, locked_until, int(user_id)),
    )
    con.commit()
    con.close()


def _session_create(user_id: int, ip: str, ua: str) -> Tuple[str, str]:
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


def _session_delete(sid: str) -> None:
    con = _db()
    con.execute("DELETE FROM sessions WHERE sid=?", (sid,))
    con.commit()
    con.close()


def _session_touch(sid: str) -> None:
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


def _admin_list_users() -> List[Dict[str, Any]]:
    con = _db()
    rows = con.execute(
        "SELECT id,email,role,is_active,created_at,last_login,failed_attempts,locked_until FROM users ORDER BY id ASC"
    ).fetchall()
    con.close()
    out = []
    for r in rows:
        out.append({
            "id": int(r["id"]),
            "email": str(r["email"]),
            "role": str(r["role"]),
            "is_active": int(r["is_active"]),
            "created_at": float(r["created_at"] or 0),
            "last_login": float(r["last_login"] or 0) if r["last_login"] else None,
            "failed_attempts": int(r["failed_attempts"] or 0),
            "locked_until": float(r["locked_until"] or 0),
        })
    return out


def _admin_create_user(email: str, role: str, password: str) -> Tuple[bool, str]:
    if not _is_email(email):
        return False, "invalid email"
    role = role.lower().strip()
    if role not in ROLES:
        return False, "invalid role"
    ok, why = _password_strength(password)
    if not ok:
        return False, "weak password: " + why

    h, s = _pbkdf2_hash(password)
    con = _db()
    try:
        con.execute(
            "INSERT INTO users(email,pw_hash,pw_salt,role,is_active,created_at) VALUES(?,?,?,?,?,?)",
            (email.lower(), h, s, role, 1, _now()),
        )
        con.commit()
        return True, "created"
    except sqlite3.IntegrityError:
        return False, "email already exists"
    finally:
        con.close()


def _admin_set_role(user_id: int, role: str) -> Tuple[bool, str]:
    role = role.lower().strip()
    if role not in ROLES:
        return False, "invalid role"
    con = _db()
    con.execute("UPDATE users SET role=? WHERE id=?", (role, int(user_id)))
    con.commit()
    con.close()
    return True, "ok"


def _admin_set_active(user_id: int, is_active: int) -> Tuple[bool, str]:
    con = _db()
    con.execute("UPDATE users SET is_active=? WHERE id=?", (1 if int(is_active) else 0, int(user_id)))
    con.commit()
    con.close()
    return True, "ok"


def _admin_reset_password(user_id: int, new_password: str) -> Tuple[bool, str]:
    ok, why = _password_strength(new_password)
    if not ok:
        return False, "weak password: " + why
    h, s = _pbkdf2_hash(new_password)
    con = _db()
    con.execute(
        "UPDATE users SET pw_hash=?, pw_salt=?, failed_attempts=0, locked_until=0 WHERE id=?",
        (h, s, int(user_id)),
    )
    con.commit()
    con.close()
    return True, "ok"


# =============================================================================
# In-memory rate limit + IDS
# =============================================================================
_rl_lock = threading.Lock()
_login_attempts: Dict[str, List[float]] = {}

_ids_lock = threading.Lock()
_ids_hits: Dict[str, List[float]] = {}
_ids_blocked: Dict[str, float] = {}


def _rate_limit_ok(ip: str) -> bool:
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


def _ids_hit(ip: str, reason: str, meta: Dict[str, Any]) -> None:
    now = _now()
    with _ids_lock:
        arr = _ids_hits.get(ip, [])
        arr = [t for t in arr if now - t < IDS_WINDOW_S]
        arr.append(now)
        _ids_hits[ip] = arr
        if len(arr) >= IDS_MAX_HITS:
            _ids_blocked[ip] = now + IDS_BLOCK_S

    _audit(None, ip, "ids_hit", {"reason": reason, "meta": meta})


def _ids_is_blocked(ip: str) -> bool:
    now = _now()
    with _ids_lock:
        until = float(_ids_blocked.get(ip, 0.0))
    return until > now


# =============================================================================
# HTTP Handler (gateway + proxy)
# =============================================================================
HOP_BY_HOP_REQ = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade"
}
HOP_BY_HOP_RESP = set(HOP_BY_HOP_REQ)


class GatewayHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def _ip(self) -> str:
        return self.client_address[0] if self.client_address else "0.0.0.0"

    def _ua(self) -> str:
        return (self.headers.get("User-Agent", "") or "")[:300]

    def _cookies(self) -> Dict[str, str]:
        out = {}
        raw = self.headers.get("Cookie", "") or ""
        for part in raw.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                out[k.strip()] = v.strip()
        return out

    def _set_cookie(self, name: str, value: str, max_age: int, http_only: bool = True):
        parts = [f"{name}={value}", f"Max-Age={int(max_age)}", "Path=/", f"SameSite={SAMESITE}"]
        if http_only:
            parts.append("HttpOnly")
        if COOKIE_SECURE:
            parts.append("Secure")
        self.send_header("Set-Cookie", "; ".join(parts))

    def _clear_cookie(self, name: str):
        self.send_header("Set-Cookie", f"{name}=; Max-Age=0; Path=/; SameSite={SAMESITE}")

    def _security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; "
            "img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline' https://cdn.plot.ly; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; form-action 'self'"
        )

    def _send(self, code: int, body: bytes, ctype: str, extra_headers: Optional[Dict[str, str]] = None):
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

    def _json(self, code: int, obj: Dict[str, Any]):
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8")

    def _read_body(self) -> bytes:
        n = int(self.headers.get("Content-Length", "0") or "0")
        if n > MAX_BODY_BYTES:
            _ids_hit(self._ip(), "body_too_large", {"len": n, "path": self.path})
            return b""
        return self.rfile.read(n) if n > 0 else b""

    def _read_json(self) -> Dict[str, Any]:
        raw = self._read_body().decode("utf-8", errors="replace").strip()
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except Exception:
            return {}

    # =========================
    # FIXED WAF (decode + match)
    # =========================
    def _waf_check(self) -> Tuple[bool, str]:
        if not self.path or len(self.path) > MAX_PATH_LEN:
            _ids_hit(self._ip(), "path_len", {"len": len(self.path or "")})
            return False, "bad request"

        for k, v in self.headers.items():
            if len(str(v)) > MAX_HEADER_VALUE_LEN:
                _ids_hit(self._ip(), "header_too_long", {"header": k})
                return False, "bad request"

        raw = self.path or ""
        raw_l = raw.lower()

        # decode percent-encoding and also convert "+" to space (querystring)
        try:
            dec = unquote_plus(raw)
        except Exception:
            dec = raw
        dec_l = dec.lower()

        # check both raw + decoded to avoid bypass
        haystacks = (raw_l, dec_l)

        bad_patterns = [
            # traversal / null byte
            "../", "..\\",
            "%2e%2e", "%2f", "%5c",  # encoded .. and separators (raw form)
            "\x00", "%00",

            # xss-ish
            "<script", "javascript:", "onerror=", "onload=",

            # sqli-ish
            "union select", "sleep(", "benchmark(",
        ]

        for bp in bad_patterns:
            for h in haystacks:
                if bp in h:
                    _ids_hit(self._ip(), "pattern", {"pattern": bp, "path": raw, "decoded": dec})
                    return False, "blocked"

        return True, "ok"

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
        if row["ip"] != self._ip() or row["ua"] != self._ua():
            _ids_hit(self._ip(), "session_bind_fail", {"sid": sid})
            return None
        _session_touch(sid)
        return row

    def _current_user(self, sess_row):
        if not sess_row:
            return None
        return _user_get(int(sess_row["user_id"]))

    def _require_auth(self) -> Tuple[Optional[Any], Optional[Any]]:
        s = self._current_session()
        if not s:
            return None, None
        u = self._current_user(s)
        if not u:
            return None, None
        if int(u["is_active"] or 0) != 1:
            return None, None
        return s, u

    def _role_can_post(self, role: str, path: str) -> bool:
        role = (role or "viewer").lower()
        allow = ROLE_ALLOW_POST_PREFIX.get(role, [])
        if "*" in allow:
            return True
        for pref in allow:
            if path.startswith(pref):
                return True
        return False

    def _proxy_to_backend(self):
        conn = http.client.HTTPConnection(BACKEND_HOST, BACKEND_PORT, timeout=20)

        out_headers = {}
        for k, v in self.headers.items():
            lk = k.lower()
            if lk in HOP_BY_HOP_REQ:
                continue
            if lk == "host":
                continue
            out_headers[k] = v

        out_headers["Host"] = f"{BACKEND_HOST}:{BACKEND_PORT}"
        out_headers["X-Forwarded-For"] = self._ip()
        out_headers["X-Forwarded-Proto"] = "http"
        out_headers["X-From-Gateway"] = "1"
        out_headers["X-Gateway-Secret"] = os.environ.get("OPTGW_SHARED_SECRET", "change-me-123")

        body = b""
        if self.command in ("POST", "PUT", "PATCH"):
            body = self._read_body()

        conn.request(self.command, self.path, body=body, headers=out_headers)
        resp = conn.getresponse()
        data = resp.read()

        self.send_response(resp.status)

        for k, v in resp.getheaders():
            lk = k.lower()
            if lk in HOP_BY_HOP_RESP:
                continue
            if lk in ("content-length", "cache-control"):
                continue
            self.send_header(k, v)

        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self._security_headers()
        self.end_headers()
        self.wfile.write(data)
    

    def do_GET(self):
        try:
            ip = self._ip()
            if _ids_is_blocked(ip):
                return self._json(429, {"ok": False, "error": "temporarily blocked"})

            ok, why = self._waf_check()
            if not ok:
                return self._json(403, {"ok": False, "error": why})

            if self.path == "/static/login.css":
                return self._send(200, LOGIN_CSS.encode("utf-8"), "text/css; charset=utf-8")
            if self.path == "/static/login.js":
                return self._send(200, LOGIN_JS.encode("utf-8"), "application/javascript; charset=utf-8")

            if self.path.startswith("/login"):
                csrf0 = _gen_csrf()
                body = LOGIN_PAGE.format(csrf=csrf0).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self._security_headers()
                self._set_cookie("csrf0", csrf0, max_age=10 * 60, http_only=True)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if self.path.startswith("/admin"):
                s, u = self._require_auth()
                if not s:
                    self.send_response(302)
                    self.send_header("Location", "/login")
                    self._security_headers()
                    self.end_headers()
                    return
                if str(u["role"]) != "admin":
                    return self._json(403, {"ok": False, "error": "forbidden"})
                return self._send(200, ADMIN_PAGE.encode("utf-8"), "text/html; charset=utf-8")

            if self.path.startswith("/api/admin/users") and self.path == "/api/admin/users":
                s, u = self._require_auth()
                if not s:
                    return self._json(401, {"ok": False, "error": "unauthorized"})
                if str(u["role"]) != "admin":
                    return self._json(403, {"ok": False, "error": "forbidden"})
                return self._json(200, {"ok": True, "users": _admin_list_users()})

            s, u = self._require_auth()
            if not s:
                self.send_response(302)
                self.send_header("Location", "/login")
                self._security_headers()
                self.end_headers()
                return

            return self._proxy_to_backend()

        except Exception as e:
            _audit(None, self._ip(), "gateway_exception_get", {"path": self.path, "err": str(e)})
            return self._json(500, {"ok": False, "error": "server error", "detail": str(e), "path": self.path})

    def do_POST(self):
        try:
            ip = self._ip()
            if _ids_is_blocked(ip):
                return self._json(429, {"ok": False, "error": "temporarily blocked"})

            ok, why = self._waf_check()
            if not ok:
                return self._json(403, {"ok": False, "error": why})

            if self.path.startswith("/api/login"):
                if not _rate_limit_ok(ip):
                    return self._json(429, {"ok": False, "error": "too many attempts. wait 60s."})

                data = self._read_json()
                email = (data.get("email") or "").strip().lower()
                password = (data.get("password") or "")
                csrf = (data.get("csrf") or "").strip()

                csrf0 = self._cookies().get("csrf0", "")
                if not csrf0 or not csrf or not hmac.compare_digest(csrf0, csrf):
                    _ids_hit(ip, "login_csrf_fail", {"email": email})
                    return self._json(403, {"ok": False, "error": "csrf failed. refresh page."})

                if not _is_email(email) or not password or len(password) > 300:
                    return self._json(400, {"ok": False, "error": "invalid credentials"})

                user = _user_get_by_email(email)
                if not user:
                    _audit(None, ip, "login_fail", {"email": email, "why": "no_user"})
                    return self._json(401, {"ok": False, "error": "invalid credentials"})

                if int(user["is_active"] or 0) != 1:
                    _audit(int(user["id"]), ip, "login_fail", {"email": email, "why": "disabled"})
                    return self._json(403, {"ok": False, "error": "account disabled"})

                if float(user["locked_until"] or 0) > _now():
                    _audit(int(user["id"]), ip, "login_fail", {"email": email, "why": "locked"})
                    return self._json(403, {"ok": False, "error": "account locked. try later."})

                if not _pbkdf2_verify(password, str(user["pw_hash"]), str(user["pw_salt"])):
                    lock_s = 60 if int(user["failed_attempts"] or 0) >= 6 else 0
                    _user_fail_attempt(int(user["id"]), lock_s=lock_s)
                    _audit(int(user["id"]), ip, "login_fail", {"email": email, "why": "bad_pw"})
                    return self._json(401, {"ok": False, "error": "invalid credentials"})

                _user_set_login_success(int(user["id"]))
                sid, _csrf_sess = _session_create(int(user["id"]), ip, self._ua())
                _audit(int(user["id"]), ip, "login_ok", {"role": str(user["role"])})

                cookie_val = _make_cookie_value(sid)
                body = json.dumps({"ok": True, "next": "/"}).encode("utf-8")

                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self._security_headers()
                self._set_cookie(COOKIE_NAME, cookie_val, max_age=SESSION_TTL_S, http_only=True)
                self._clear_cookie("csrf0")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if self.path.startswith("/api/logout"):
                s = self._current_session()
                if s:
                    _audit(int(s["user_id"]), ip, "logout", {})
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

            s, u = self._require_auth()
            if not s:
                return self._json(401, {"ok": False, "error": "unauthorized"})

            role = str(u["role"] or "viewer").lower()

            if self.path == "/api/admin/users":
                if role != "admin":
                    return self._json(403, {"ok": False, "error": "forbidden"})
                data = self._read_json()
                ok2, msg = _admin_create_user(
                    (data.get("email") or "").strip().lower(),
                    (data.get("role") or "").strip().lower(),
                    data.get("password") or "",
                )
                _audit(int(u["id"]), ip, "admin_create_user",
                       {"ok": ok2, "email": data.get("email"), "role": data.get("role")})
                return self._json(200 if ok2 else 400, {"ok": ok2, "error": (None if ok2 else msg)})

            if self.path == "/api/admin/users/role":
                if role != "admin":
                    return self._json(403, {"ok": False, "error": "forbidden"})
                data = self._read_json()
                ok2, msg = _admin_set_role(int(data.get("user_id") or 0), str(data.get("role") or "viewer"))
                _audit(int(u["id"]), ip, "admin_set_role",
                       {"ok": ok2, "user_id": data.get("user_id"), "role": data.get("role")})
                return self._json(200 if ok2 else 400, {"ok": ok2, "error": (None if ok2 else msg)})

            if self.path == "/api/admin/users/disable":
                if role != "admin":
                    return self._json(403, {"ok": False, "error": "forbidden"})
                data = self._read_json()
                ok2, msg = _admin_set_active(int(data.get("user_id") or 0), int(data.get("is_active") or 0))
                _audit(int(u["id"]), ip, "admin_set_active",
                       {"ok": ok2, "user_id": data.get("user_id"), "is_active": data.get("is_active")})
                return self._json(200 if ok2 else 400, {"ok": ok2, "error": (None if ok2 else msg)})

            if self.path == "/api/admin/users/reset-pw":
                if role != "admin":
                    return self._json(403, {"ok": False, "error": "forbidden"})
                data = self._read_json()
                ok2, msg = _admin_reset_password(int(data.get("user_id") or 0), data.get("new_password") or "")
                _audit(int(u["id"]), ip, "admin_reset_pw", {"ok": ok2, "user_id": data.get("user_id")})
                return self._json(200 if ok2 else 400, {"ok": ok2, "error": (None if ok2 else msg)})

            if not self._role_can_post(role, self.path):
                return self._json(403, {"ok": False, "error": "forbidden (role)"})

            return self._proxy_to_backend()

        except Exception as e:
             _audit(None, self._ip(), "gateway_exception_post", {"path": self.path, "err": str(e)})
             return self._json(500, {"ok": False, "error": "server error", "detail": str(e), "path": self.path})

def start_gateway(host: str, port: int):
    _db_init()
    srv = ThreadingHTTPServer((host, int(port)), GatewayHandler)
    print("====================================================")
    print("Secure Login Gateway is running")
    print(f"  Gateway: http://127.0.0.1:{port}/login")
    print(f"  Proxies to backend: http://{BACKEND_HOST}:{BACKEND_PORT}/")
    print("Tip: run backend on 127.0.0.1 only, so users can't bypass login.")
    print("====================================================")
    srv.serve_forever()


if __name__ == "__main__":
    start_gateway(GATEWAY_HOST, GATEWAY_PORT)