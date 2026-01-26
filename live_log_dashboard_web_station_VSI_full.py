#!/usr/bin/env python3
"""
Project KPI Dashboard (WebSockets) - FULL SINGLE FILE
- Auto search recursively for any file that has "logs" in its path + endswith .txt/.log
- Real-time tail (does not lock file; writer can append)
- Left column keeps "ALL" and shows stations as: Station 1 .. Station 6 (from filename first)
- Click station -> right side shows ONLY that station dashboard
- Font size textbox controls code/log font (CSS var) and persists in localStorage
- Charts: pie + bar (overview) and line chart (per-station)

Install:
  pip install aiohttp

Run:
  python kpi_dashboard_web.py --from-start

Open:
  http://127.0.0.1:8787
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import queue
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, Tuple, List

from aiohttp import web


# -------------------------
# Config
# -------------------------
DEFAULT_CODE_FONT_PX = 12
RAW_LINES_PER_STATION = 220
RAW_LINES_PER_FILE_FALLBACK = 260


# -------------------------
# Station name normalization (IMPORTANT FIX)
# Priority:
#   1) filename: ST1_logs.txt -> Station 1, ST2_logs.txt -> Station 2
#   2) header: +=ST4_Test+= -> Station 4
# -------------------------
FIRST_NUM_RE = re.compile(r"(\d+)")
FILE_STATION_RE = re.compile(
    r"(?:^|[^a-z0-9])(st|station)\s*0*(\d+)", re.IGNORECASE)


def _station_from_filename(path: str) -> Optional[int]:
    base = os.path.basename(path)
    m = FILE_STATION_RE.search(base)
    if m:
        return int(m.group(2))
    # fallback: any number in filename
    m2 = FIRST_NUM_RE.search(base)
    if m2:
        return int(m2.group(1))
    return None


def _station_from_header(raw_station: str) -> Optional[int]:
    m = FIRST_NUM_RE.search(raw_station or "")
    if m:
        return int(m.group(1))
    return None


def normalize_station_name(raw_station: str, source_file: str) -> Tuple[str, Optional[int], str]:
    raw = (raw_station or "").strip()
    n_file = _station_from_filename(source_file)
    n_hdr = _station_from_header(raw)

    n = n_file if n_file is not None else n_hdr
    if n is None:
        return "Station ?", None, raw or "UNKNOWN"
    return f"Station {n}", n, raw or f"Station {n}"


# -------------------------
# Parsing
# -------------------------
BLOCK_HEADER_RE = re.compile(r"^\+=\s*(?P<station>[^=]+)\s*\+=\s*$")
VSI_TIME_RE = re.compile(r"^VSI time:\s*(?P<ns>\d+)\s*ns\s*$")
KV_RE = re.compile(r"^\s*(?P<k>[A-Za-z0-9_]+)\s*=\s*(?P<v>.+?)\s*$")
RX_PACKET_RE = re.compile(r"Received packet from\s+(?P<src>\S+)")


def _parse_value(raw: str) -> Any:
    raw = raw.strip()
    if raw.lower() in ("true", "false"):
        return raw.lower() == "true"
    try:
        if raw.startswith(("0x", "0X")):
            return int(raw, 16)
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


def _to_int01(v: Any) -> int:
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, (int, float)):
        return 1 if int(v) != 0 else 0
    return 0


@dataclass
class Snapshot:
    station: str
    station_raw: str
    station_num: Optional[int]
    vsi_time_ns: int
    inputs: Dict[str, Any] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)
    source_file: str = ""


class BlockParser:
    def __init__(self, source_file: str):
        self.source_file = source_file
        self.reset()

    def reset(self):
        self._in_block = False
        self._station_display: Optional[str] = None
        self._station_raw: Optional[str] = None
        self._station_num: Optional[int] = None
        self._vsi_time: Optional[int] = None
        self._mode: Optional[str] = None
        self._inputs: Dict[str, Any] = {}
        self._outputs: Dict[str, Any] = {}

    @property
    def current_station(self) -> Optional[str]:
        return self._station_display if self._in_block else None

    @property
    def current_station_key(self) -> Tuple[Optional[str], Optional[str], Optional[int]]:
        return self._station_display, self._station_raw, self._station_num

    def flush(self) -> Optional[Snapshot]:
        if self._in_block and self._station_display and self._vsi_time is not None:
            snap = Snapshot(
                station=self._station_display,
                station_raw=self._station_raw or self._station_display,
                station_num=self._station_num,
                vsi_time_ns=int(self._vsi_time),
                inputs=dict(self._inputs),
                outputs=dict(self._outputs),
                source_file=self.source_file,
            )
            self.reset()
            return snap
        return None

    def feed_line(self, line: str) -> Tuple[Optional[Snapshot], Optional[Dict[str, Any]]]:
        line = line.rstrip("\n")

        m = RX_PACKET_RE.search(line)
        if m:
            return None, {"type": "rx_packet", "src": m.group("src"), "file": self.source_file}

        mh = BLOCK_HEADER_RE.match(line.strip())
        if mh:
            snap = self.flush()
            raw = mh.group("station").strip()

            disp, num, raw_keep = normalize_station_name(raw, self.source_file)
            self._in_block = True
            self._station_raw = raw_keep
            self._station_display = disp
            self._station_num = num
            self._mode = None
            return snap, None

        if not self._in_block:
            return None, None

        mt = VSI_TIME_RE.match(line.strip())
        if mt:
            self._vsi_time = int(mt.group("ns"))
            return None, None

        if line.strip() == "Inputs:":
            self._mode = "inputs"
            return None, None

        if line.strip() == "Outputs:":
            self._mode = "outputs"
            return None, None

        mkv = KV_RE.match(line)
        if mkv and self._mode in ("inputs", "outputs"):
            k = mkv.group("k")
            v = _parse_value(mkv.group("v"))
            if self._mode == "inputs":
                self._inputs[k] = v
            else:
                self._outputs[k] = v
            return None, None

        if line.strip() == "":
            snap = self.flush()
            return snap, None

        return None, None


# -------------------------
# Tail thread (no file locking)
# -------------------------
class TailThread(threading.Thread):
    def __init__(self, filepath: str, out_q: "queue.Queue[tuple]", from_start: bool = False):
        super().__init__(daemon=True)
        self.filepath = filepath
        self.out_q = out_q
        self.from_start = from_start
        self._stop = threading.Event()
        self.parser = BlockParser(source_file=filepath)
        self._pos = 0
        self._did_initial_read = False

    def stop(self):
        self._stop.set()

    def _emit_snap(self, snap: Optional[Snapshot]):
        if snap:
            self.out_q.put(("snapshot", snap))

    def run(self):
        while not os.path.exists(self.filepath) and not self._stop.is_set():
            time.sleep(0.15)

        try:
            with open(self.filepath, "r", encoding="utf-8", errors="replace") as f:
                if self.from_start and not self._did_initial_read:
                    f.seek(0, os.SEEK_SET)
                else:
                    f.seek(0, os.SEEK_END)
                self._pos = f.tell()

                if self.from_start and not self._did_initial_read:
                    while not self._stop.is_set():
                        line = f.readline()
                        if not line:
                            break
                        self._process_line(line)
                    self._emit_snap(self.parser.flush())
                    self._did_initial_read = True
                    self._pos = f.tell()

                while not self._stop.is_set():
                    try:
                        cur_size = os.path.getsize(self.filepath)
                    except OSError:
                        cur_size = None

                    # file rotated/truncated
                    if cur_size is not None and cur_size < self._pos:
                        f.seek(0, os.SEEK_SET)
                        self._pos = f.tell()
                        self.parser.reset()

                    line = f.readline()
                    if not line:
                        time.sleep(0.08)
                        continue

                    self._pos = f.tell()
                    self._process_line(line)

        except Exception as e:
            self.out_q.put(("error", {"file": self.filepath, "error": str(e)}))

    def _process_line(self, line: str):
        snap, ev = self.parser.feed_line(line)

        st = self.parser.current_station
        st_disp, st_raw, st_num = self.parser.current_station_key
        clean = line.rstrip("\n")

        if st:
            self.out_q.put(("raw_station", {
                "file": self.filepath,
                "station": st_disp,
                "station_raw": st_raw,
                "station_num": st_num,
                "line": clean
            }))
        else:
            self.out_q.put(
                ("raw_file", {"file": self.filepath, "line": clean}))

        if ev:
            self.out_q.put(("event", ev))

        self._emit_snap(snap)


# -------------------------
# Stats
# -------------------------
@dataclass
class StationStats:
    station: str
    station_raw: str
    station_num: Optional[int]
    file: str

    first_wall_ts: float = 0.0
    last_wall_ts: float = 0.0
    last_vsi_ns: Optional[int] = None

    inputs: Dict[str, Any] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)

    rx_packets: int = 0
    cycles_done: int = 0
    faults_count: int = 0
    start_pulses: int = 0
    reset_pulses: int = 0
    stop_asserts: int = 0

    busy_total_ns: int = 0
    cycle_time_ms_hist: deque = field(
        default_factory=lambda: deque(maxlen=200))

    _prev_done: int = 0
    _prev_fault: int = 0
    _prev_cmd_start: int = 0
    _prev_cmd_reset: int = 0


class StatsStore:
    def __init__(self):
        # (file, station_display)
        self.stats: Dict[Tuple[str, str], StationStats] = {}
        self.station_lines: Dict[Tuple[str, str], deque] = {}
        self.file_lines: Dict[str, deque] = {}

    def _stq(self, file: str, station: str) -> deque:
        key = (file, station)
        if key not in self.station_lines:
            self.station_lines[key] = deque(maxlen=RAW_LINES_PER_STATION)
        return self.station_lines[key]

    def _fq(self, file: str) -> deque:
        if file not in self.file_lines:
            self.file_lines[file] = deque(maxlen=RAW_LINES_PER_FILE_FALLBACK)
        return self.file_lines[file]

    def handle_raw_station(self, file: str, station: str, station_raw: str, station_num: Optional[int], line: str):
        self._stq(file, station).append(line)
        self.get(file, station, station_raw, station_num)

    def handle_raw_file(self, file: str, line: str):
        self._fq(file).append(line)

    def get(self, file: str, station: str, station_raw: str, station_num: Optional[int]) -> StationStats:
        key = (file, station)
        if key not in self.stats:
            self.stats[key] = StationStats(
                station=station,
                station_raw=station_raw,
                station_num=station_num,
                file=file
            )
        else:
            st = self.stats[key]
            st.station_raw = st.station_raw or station_raw
            st.station_num = st.station_num if st.station_num is not None else station_num
        return self.stats[key]

    def handle_event(self, ev: Dict[str, Any]):
        if ev.get("type") == "rx_packet":
            f = ev.get("file", "")
            for (ff, _st), st in self.stats.items():
                if ff == f:
                    st.rx_packets += 1

    def handle_snapshot(self, snap: Snapshot):
        st = self.get(snap.source_file, snap.station,
                      snap.station_raw, snap.station_num)

        now = time.time()
        if st.first_wall_ts == 0:
            st.first_wall_ts = now
        st.last_wall_ts = now

        if st.last_vsi_ns is not None:
            delta = max(0, snap.vsi_time_ns - st.last_vsi_ns)
            prev_busy = _to_int01(st.outputs.get("busy", 0))
            if prev_busy == 1:
                st.busy_total_ns += delta

        st.last_vsi_ns = snap.vsi_time_ns
        st.inputs = snap.inputs
        st.outputs = snap.outputs

        done = _to_int01(st.outputs.get("done", 0))
        fault = _to_int01(st.outputs.get("fault", 0))
        cmd_start = _to_int01(st.inputs.get("cmd_start", 0))
        cmd_stop = _to_int01(st.inputs.get("cmd_stop", 0))
        cmd_reset = _to_int01(st.inputs.get("cmd_reset", 0))

        if st._prev_done == 0 and done == 1:
            st.cycles_done += 1
        if st._prev_fault == 0 and fault == 1:
            st.faults_count += 1
        if st._prev_cmd_start == 0 and cmd_start == 1:
            st.start_pulses += 1
        if st._prev_cmd_reset == 0 and cmd_reset == 1:
            st.reset_pulses += 1
        if cmd_stop == 1:
            st.stop_asserts += 1

        st._prev_done = done
        st._prev_fault = fault
        st._prev_cmd_start = cmd_start
        st._prev_cmd_reset = cmd_reset

        ctm = snap.outputs.get("cycle_time_ms", None)
        if isinstance(ctm, (int, float)):
            st.cycle_time_ms_hist.append(float(ctm))

    def _state(self, st: StationStats) -> str:
        fault = _to_int01(st.outputs.get("fault", 0))
        busy = _to_int01(st.outputs.get("busy", 0))
        ready = _to_int01(st.outputs.get("ready", 0))
        cmd_stop = _to_int01(st.inputs.get("cmd_stop", 0))

        if fault == 1:
            return "FAULT"
        if busy == 1:
            return "RUNNING"
        if cmd_stop == 1:
            return "STOPPED"
        if ready == 1:
            return "READY"
        return "UNKNOWN"

    def export_payload(self) -> Dict[str, Any]:
        items = []
        state_counts = {"READY": 0, "RUNNING": 0,
                        "FAULT": 0, "STOPPED": 0, "UNKNOWN": 0}

        def sort_key(kv):
            (file, station), st = kv
            num = st.station_num if st.station_num is not None else 999999
            return (num, os.path.basename(file), station)

        for (file, station), st in sorted(self.stats.items(), key=sort_key):
            state = self._state(st)
            state_counts[state] = state_counts.get(state, 0) + 1

            elapsed = (st.last_wall_ts - st.first_wall_ts) if (
                st.first_wall_ts and st.last_wall_ts) else 0.0
            busy_s = st.busy_total_ns / 1e9
            util = (busy_s / elapsed) if elapsed > 0 else 0.0

            lines = list(self.station_lines.get((file, station), []))
            if not lines:
                lines = list(self.file_lines.get(file, []))

            key = f"{os.path.basename(file)}::{station}"

            items.append({
                "id": key,
                "file": os.path.basename(file),
                "station": station,
                "station_raw": st.station_raw,
                "station_num": st.station_num,
                "state": state,
                "updated_wall_ts": st.last_wall_ts,
                "elapsed_s": round(elapsed, 2),
                "utilization": round(util, 3),
                "inputs": st.inputs,
                "outputs": st.outputs,
                "logs": lines,
                "kpis": {
                    "rx_packets": st.rx_packets,
                    "cycles_done": st.cycles_done,
                    "faults_count": st.faults_count,
                    "start_pulses": st.start_pulses,
                    "reset_pulses": st.reset_pulses,
                    "stop_asserts": st.stop_asserts,
                    "busy_total_s": round(busy_s, 3),
                    "last_cycle_ms": (st.cycle_time_ms_hist[-1] if st.cycle_time_ms_hist else None),
                },
                "cycle_time_ms_hist": list(st.cycle_time_ms_hist),
            })

        cycles = {x["id"]: x["kpis"]["cycles_done"] for x in items}
        faults = {x["id"]: x["kpis"]["faults_count"] for x in items}
        util = {x["id"]: x["utilization"] for x in items}

        return {
            "items": items,
            "summary": {
                "stations": len(items),
                "state_counts": state_counts,
                "cycles_by_station": cycles,
                "faults_by_station": faults,
                "util_by_station": util,
            }
        }


class Engine:
    def __init__(self, files: List[str], from_start: bool):
        self.q_in: "queue.Queue[tuple]" = queue.Queue()
        self.tailers = [TailThread(
            f, self.q_in, from_start=from_start) for f in files]
        self.store = StatsStore()
        self.clients = set()

    def start(self):
        for t in self.tailers:
            t.start()

    def stop(self):
        for t in self.tailers:
            t.stop()

    def pump_once(self) -> bool:
        updated = False
        while True:
            try:
                kind, payload = self.q_in.get_nowait()
            except queue.Empty:
                break

            if kind == "raw_station":
                self.store.handle_raw_station(
                    payload["file"], payload["station"], payload.get(
                        "station_raw", ""), payload.get("station_num"), payload["line"]
                )
                updated = True
            elif kind == "raw_file":
                self.store.handle_raw_file(payload["file"], payload["line"])
                updated = True
            elif kind == "snapshot":
                self.store.handle_snapshot(payload)
                updated = True
            elif kind == "event":
                self.store.handle_event(payload)
                updated = True
            elif kind == "error":
                updated = True

        return updated


# -------------------------
# HTML UI
# -------------------------
HTML_PAGE_TEMPLATE = r"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Project KPI Dashboard</title>
  <style>
    :root{
      --siemens-teal: #00A0A0;
      --siemens-teal-2: #00B2B2;
      --panel: rgba(255,255,255,0.06);
      --border: rgba(255,255,255,0.10);
      --text: rgba(255,255,255,0.92);
      --muted: rgba(255,255,255,0.65);
      --shadow: 0 14px 35px rgba(0,0,0,0.35);
      --radius: 16px;
      --code-font-size: {{CODE_FONT_PX}}px;
    }

    *{ box-sizing: border-box; }
    body{
      margin:0;
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, "Helvetica Neue", sans-serif;
      background:
        radial-gradient(900px 600px at 15% 15%, rgba(0,160,160,0.35), transparent 55%),
        radial-gradient(700px 500px at 90% 10%, rgba(0,178,178,0.22), transparent 55%),
        linear-gradient(180deg, #06101F, #050A12 60%, #04070C);
      color: var(--text);
    }

    header{
      padding: 18px 18px 12px;
      border-bottom: 1px solid rgba(255,255,255,0.08);
      backdrop-filter: blur(10px);
      position: sticky;
      top: 0;
      z-index: 5;
      background: rgba(4,7,12,0.6);
    }

    .titlebar{ display:flex; align-items:center; justify-content:space-between; gap: 12px; }
    .brand{ display:flex; align-items:center; gap: 12px; }
    .dot{
      width: 12px; height: 12px; border-radius: 50%;
      background: var(--siemens-teal);
      box-shadow: 0 0 0 6px rgba(0,160,160,0.18);
    }
    h1{ margin:0; font-size: 18px; }
    .sub{ margin-top: 6px; color: var(--muted); font-size: 12px; }

    .pill{
      padding: 8px 12px;
      border: 1px solid rgba(255,255,255,0.10);
      border-radius: 999px;
      background: rgba(255,255,255,0.06);
      font-size: 12px;
      color: var(--muted);
      display:flex;
      align-items:center;
      gap: 8px;
    }

    .fontBox{
      width: 64px;
      border: 1px solid rgba(255,255,255,0.14);
      background: rgba(255,255,255,0.06);
      color: rgba(255,255,255,0.90);
      border-radius: 10px;
      padding: 6px 8px;
      outline: none;
      font-size: 12px;
    }
    .btn{
      appearance:none;
      border: 1px solid rgba(255,255,255,0.14);
      background: rgba(255,255,255,0.06);
      color: rgba(255,255,255,0.88);
      border-radius: 10px;
      padding: 6px 10px;
      cursor: pointer;
      font-size: 12px;
    }
    .btn:hover{
      border-color: rgba(0,178,178,0.45);
      background: rgba(0,178,178,0.10);
    }

    main{ padding: 16px; max-width: 1600px; margin: 0 auto; }
    .grid{ display:grid; grid-template-columns: 330px 1fr; gap: 14px; }

    .card{
      border: 1px solid var(--border);
      border-radius: var(--radius);
      background: var(--panel);
      box-shadow: var(--shadow);
      overflow: hidden;
    }

    .card .hd{
      padding: 12px 14px;
      display:flex;
      align-items:center;
      justify-content:space-between;
      border-bottom: 1px solid rgba(255,255,255,0.08);
      background: rgba(255,255,255,0.05);
      gap: 10px;
    }
    .card .hd h2{ margin:0; font-size: 13px; color: var(--muted); font-weight: 700; }
    .card .bd{ padding: 12px 14px; }

    .badge{
      padding: 6px 10px;
      border-radius: 999px;
      border: 1px solid rgba(255,255,255,0.12);
      background: rgba(0,0,0,0.12);
      font-size: 11px;
      color: var(--muted);
      white-space: nowrap;
    }

    .selList{
      display:flex;
      flex-direction:column;
      gap: 10px;
      max-height: calc(100vh - 110px);
      overflow:auto;
      padding-right: 6px;
    }

    .sel{
      border: 1px solid rgba(255,255,255,0.10);
      background: rgba(255,255,255,0.05);
      border-radius: 14px;
      padding: 10px 12px;
      cursor: pointer;
      transition: transform 80ms ease, background 120ms ease, border-color 120ms ease;
      display:flex;
      align-items:center;
      justify-content:space-between;
      gap: 12px;
    }
    .sel:hover{
      transform: translateY(-1px);
      border-color: rgba(0,178,178,0.35);
      background: rgba(0,178,178,0.08);
    }
    .sel.active{
      border-color: rgba(0,178,178,0.65);
      background: rgba(0,178,178,0.12);
    }
    .sel .left{
      min-width: 0;
      display:flex;
      flex-direction:column;
      gap: 4px;
    }
    .sel .name{
      font-size: 13px;
      font-weight: 900;
      white-space:nowrap;
      overflow:hidden;
      text-overflow:ellipsis;
    }
    .sel .meta{
      font-size: 11px;
      color: var(--muted);
      white-space:nowrap;
      overflow:hidden;
      text-overflow:ellipsis;
    }

    .statePill{
      padding: 6px 10px;
      border-radius: 999px;
      border: 1px solid rgba(255,255,255,0.12);
      font-size: 11px;
      white-space: nowrap;
    }
    .ready{ border-color: rgba(46,229,157,0.40); color: rgba(46,229,157,0.92); background: rgba(46,229,157,0.09); }
    .run{ border-color: rgba(0,178,178,0.55); color: rgba(0,178,178,0.95); background: rgba(0,178,178,0.10); }
    .fault{ border-color: rgba(255,77,109,0.55); color: rgba(255,77,109,0.95); background: rgba(255,77,109,0.10); }
    .stop{ border-color: rgba(255,200,87,0.55); color: rgba(255,200,87,0.95); background: rgba(255,200,87,0.10); }
    .unk{ border-color: rgba(255,255,255,0.18); color: rgba(255,255,255,0.70); background: rgba(255,255,255,0.06); }

    .kpiGrid{
      display:grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
    }
    .kpi{
      border: 1px solid rgba(255,255,255,0.10);
      background: rgba(255,255,255,0.04);
      border-radius: 14px;
      padding: 10px 10px;
    }
    .kpi .lab{ font-size: 11px; color: var(--muted); }
    .kpi .val{ font-size: 20px; margin-top: 4px; font-weight: 900; }

    pre{
      margin:0;
      border: 1px solid rgba(255,255,255,0.10);
      background: rgba(0,0,0,0.15);
      padding: 10px;
      border-radius: 14px;
      max-height: 300px;
      overflow: auto;
      color: rgba(255,255,255,0.85);
      font-size: var(--code-font-size);
      line-height: 1.35;
      white-space: pre-wrap;
    }

    .twoCol{
      display:grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }

    .charts{ display:grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    canvas{ width: 100%; height: 220px; border-radius: 12px; background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); }

    @media (max-width: 1200px){
      .grid{ grid-template-columns: 1fr; }
      .twoCol{ grid-template-columns: 1fr; }
      .charts{ grid-template-columns: 1fr; }
      canvas{ height: 200px; }
      .selList{ max-height: none; }
    }
  </style>
</head>
<body>
<header>
  <div class="titlebar">
    <div class="brand">
      <div class="dot"></div>
      <div>
        <h1>Project KPI Dashboard</h1>
        <div class="sub">click a station on the left to focus its dashboard</div>
      </div>
    </div>

    <div style="display:flex; gap:10px; align-items:center;">
      <div class="pill" style="gap:8px;">
        <span>font(px)</span>
        <input id="fontInput" class="fontBox" type="number" min="8" max="40" step="1" />
        <button id="fontApply" class="btn">Apply</button>
        <span class="badge" id="fontLabel">code: {{CODE_FONT_PX}}px</span>
      </div>

      <div class="pill">
        <span id="connDot">●</span>
        <span id="status">connecting...</span>
      </div>
    </div>
  </div>
</header>

<main>
  <div class="grid">
    <div class="card">
      <div class="hd">
        <h2>Stations</h2>
        <div class="badge" id="stationCount">0</div>
      </div>
      <div class="bd">
        <div class="selList" id="selList"></div>
      </div>
    </div>

    <div style="display:flex; flex-direction:column; gap: 14px;">
      <div class="card">
        <div class="hd">
          <h2 id="rightTitle">Overview</h2>
          <div class="badge" id="lastUpdate">-</div>
        </div>
        <div class="bd" id="rightBody"></div>
      </div>
    </div>

  </div>
</main>

<script>
/* ===== font control ===== */
const CODE_FONT_MIN = 8;
const CODE_FONT_MAX = 40;
const CODE_FONT_DEFAULT = {{CODE_FONT_PX}};

function clamp(n, a, b){ return Math.max(a, Math.min(b, n)); }
function setCodeFont(px){
  px = clamp(px, CODE_FONT_MIN, CODE_FONT_MAX);
  document.documentElement.style.setProperty("--code-font-size", px + "px");
  localStorage.setItem("codeFontPx", String(px));
  const lbl = document.getElementById("fontLabel");
  if(lbl) lbl.textContent = "code: " + px + "px";
  const inp = document.getElementById("fontInput");
  if(inp) inp.value = String(px);
}
function getSavedCodeFont(){
  const v = parseInt(localStorage.getItem("codeFontPx") || String(CODE_FONT_DEFAULT), 10);
  return isNaN(v) ? CODE_FONT_DEFAULT : v;
}
window.addEventListener("DOMContentLoaded", ()=>{
  setCodeFont(getSavedCodeFont());
  const btn = document.getElementById("fontApply");
  const inp = document.getElementById("fontInput");
  function applyFromBox(){
    const v = parseInt(inp.value || "", 10);
    if(isNaN(v)) return;
    setCodeFont(v);
  }
  btn.onclick = applyFromBox;
  inp.addEventListener("keydown", (e)=>{ if(e.key === "Enter") applyFromBox(); });
});

/* ===== canvas helpers ===== */
function clearCanvas(ctx, c){ ctx.clearRect(0,0,c.width,c.height); }

function drawPie(canvas, labels, values, colors){
  const ctx = canvas.getContext("2d");
  clearCanvas(ctx, canvas);
  const total = values.reduce((a,b)=>a+b,0);
  if(!total){
    ctx.fillStyle = "rgba(255,255,255,0.7)";
    ctx.fillText("No data", 10, 20);
    return;
  }
  const cx = canvas.width/2, cy = canvas.height/2;
  const r = Math.min(canvas.width, canvas.height) * 0.30;
  let start = -Math.PI/2;
  const inner = r * 0.62;

  for(let i=0;i<values.length;i++){
    const frac = values[i]/total;
    const end = start + frac * 2*Math.PI;
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.arc(cx, cy, r, start, end);
    ctx.closePath();
    ctx.fillStyle = colors[i] || "rgba(255,255,255,0.4)";
    ctx.fill();
    start = end;
  }

  ctx.globalCompositeOperation = "destination-out";
  ctx.beginPath(); ctx.arc(cx, cy, inner, 0, 2*Math.PI); ctx.fill();
  ctx.globalCompositeOperation = "source-over";

  ctx.font = "12px Arial";
  let lx = 14, ly = 14;
  for(let i=0;i<labels.length;i++){
    const pct = Math.round(values[i] * 100 / total);
    ctx.fillStyle = colors[i] || "rgba(255,255,255,0.4)";
    ctx.fillRect(lx, ly + i*20, 10, 10);
    ctx.fillStyle = "rgba(255,255,255,0.82)";
    ctx.fillText(`${labels[i]}: ${values[i]} (${pct}%)`, lx+16, ly+10 + i*20);
  }
}

function drawBar(canvas, labels, values){
  const ctx = canvas.getContext("2d");
  clearCanvas(ctx, canvas);
  const max = Math.max(1, ...values);
  const pad = 36;
  const w = canvas.width, h = canvas.height;
  const bw = (w - pad*2) / Math.max(1, labels.length);
  const barW = Math.max(6, bw * 0.62);

  ctx.strokeStyle = "rgba(255,255,255,0.12)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(pad, pad);
  ctx.lineTo(pad, h-pad);
  ctx.lineTo(w-pad, h-pad);
  ctx.stroke();

  for(let i=0;i<labels.length;i++){
    const v = values[i];
    const bh = (h - pad*2) * (v / max);
    const x = pad + i*bw + (bw - barW)/2;
    const y = (h - pad) - bh;

    const grad = ctx.createLinearGradient(0, y, 0, y+bh);
    grad.addColorStop(0, "rgba(0,178,178,0.95)");
    grad.addColorStop(1, "rgba(0,160,160,0.35)");
    ctx.fillStyle = grad;
    ctx.fillRect(x, y, barW, bh);
  }
}

function drawLine(canvas, values){
  const ctx = canvas.getContext("2d");
  clearCanvas(ctx, canvas);
  if(!values || values.length < 2){
    ctx.fillStyle = "rgba(255,255,255,0.7)";
    ctx.fillText("No data", 10, 20);
    return;
  }
  const pad = 36;
  const w = canvas.width, h = canvas.height;
  const minV = Math.min(...values);
  const maxV = Math.max(...values);
  const span = Math.max(1e-9, maxV - minV);

  ctx.strokeStyle = "rgba(255,255,255,0.12)";
  ctx.beginPath();
  ctx.moveTo(pad, pad);
  ctx.lineTo(pad, h-pad);
  ctx.lineTo(w-pad, h-pad);
  ctx.stroke();

  ctx.strokeStyle = "rgba(0,178,178,0.95)";
  ctx.lineWidth = 2;
  ctx.beginPath();
  values.forEach((v,i)=>{
    const x = pad + (i/(values.length-1))*(w-pad*2);
    const y = (h-pad) - ((v-minV)/span)*(h-pad*2);
    if(i===0) ctx.moveTo(x,y); else ctx.lineTo(x,y);
  });
  ctx.stroke();
}

/* ===== app state ===== */
let payload = {items:[], summary:{}};
let selectedId = "ALL";

function safeText(s){ return (s===undefined || s===null) ? "" : String(s); }
function fmt(obj){ try { return JSON.stringify(obj, null, 2); } catch(e){ return String(obj); } }

function stateClass(state){
  if(state==="READY") return "statePill ready";
  if(state==="RUNNING") return "statePill run";
  if(state==="FAULT") return "statePill fault";
  if(state==="STOPPED") return "statePill stop";
  return "statePill unk";
}

function setSelected(id){ selectedId = id; renderAll(); }

/* left list */
function renderLeft(items){
  const list = document.getElementById("selList");
  list.innerHTML = "";

  const all = document.createElement("div");
  all.className = "sel" + (selectedId==="ALL" ? " active" : "");
  all.onclick = ()=> setSelected("ALL");
  all.innerHTML = `
    <div class="left">
      <div class="name">ALL</div>
      <div class="meta">overview charts</div>
    </div>
    <div class="statePill run">OVERVIEW</div>
  `;
  list.appendChild(all);

  items.forEach(it=>{
    const el = document.createElement("div");
    el.className = "sel" + (selectedId===it.id ? " active" : "");
    el.onclick = ()=> setSelected(it.id);
    const utilPct = Math.round((it.utilization||0)*100);
    el.innerHTML = `
      <div class="left">
        <div class="name">${safeText(it.station)}</div>
        <div class="meta">${safeText(it.file)} • util ${utilPct}% • cycles ${it.kpis?.cycles_done ?? 0}</div>
      </div>
      <div class="${stateClass(it.state)}">${safeText(it.state)}</div>
    `;
    list.appendChild(el);
  });

  document.getElementById("stationCount").textContent = items.length;
}

/* right overview */
function renderOverviewRight(){
  const s = payload.summary || {};
  const sc = (s.state_counts || {});
  const body = document.getElementById("rightBody");
  document.getElementById("rightTitle").textContent = "Overview";

  body.innerHTML = `
    <div class="kpiGrid" style="margin-bottom:12px;">
      <div class="kpi"><div class="lab">Stations</div><div class="val" id="k_stations">-</div></div>
      <div class="kpi"><div class="lab">Running</div><div class="val" id="k_running">-</div></div>
      <div class="kpi"><div class="lab">Fault</div><div class="val" id="k_fault">-</div></div>
      <div class="kpi"><div class="lab">Stopped</div><div class="val" id="k_stopped">-</div></div>
    </div>

    <div class="charts">
      <div>
        <div class="sub" style="margin:0 0 8px;">State distribution</div>
        <canvas id="pieStates" width="900" height="360"></canvas>
      </div>
      <div>
        <div class="sub" style="margin:0 0 8px;">Cycles done (per station)</div>
        <canvas id="barCycles" width="900" height="360"></canvas>
      </div>
      <div>
        <div class="sub" style="margin:0 0 8px;">Faults (per station)</div>
        <canvas id="barFaults" width="900" height="360"></canvas>
      </div>
      <div>
        <div class="sub" style="margin:0 0 8px;">Utilization (busy/elapsed)</div>
        <canvas id="barUtil" width="900" height="360"></canvas>
      </div>
    </div>
  `;

  document.getElementById("k_stations").textContent = String(s.stations ?? "-");
  document.getElementById("k_running").textContent = String(sc.RUNNING ?? 0);
  document.getElementById("k_fault").textContent = String(sc.FAULT ?? 0);
  document.getElementById("k_stopped").textContent = String(sc.STOPPED ?? 0);

  const labels = ["READY","RUNNING","FAULT","STOPPED","UNKNOWN"];
  const values = labels.map(x => sc[x] || 0);
  const colors = [
    "rgba(46,229,157,0.85)",
    "rgba(0,178,178,0.90)",
    "rgba(255,77,109,0.90)",
    "rgba(255,200,87,0.90)",
    "rgba(255,255,255,0.25)"
  ];
  drawPie(document.getElementById("pieStates"), labels, values, colors);

  const cyclesMap = s.cycles_by_station || {};
  const faultsMap = s.faults_by_station || {};
  const utilMap = s.util_by_station || {};

  const keys = Object.keys(cyclesMap);
  const labels2 = keys.map(k => k.split("::").slice(-1)[0]); // station name
  const cyclesVals = keys.map(k => cyclesMap[k] || 0);
  const faultsVals = keys.map(k => faultsMap[k] || 0);
  const utilVals = keys.map(k => Math.round((utilMap[k] || 0) * 100));

  drawBar(document.getElementById("barCycles"), labels2, cyclesVals);
  drawBar(document.getElementById("barFaults"), labels2, faultsVals);
  drawBar(document.getElementById("barUtil"), labels2, utilVals);
}

/* right station */
function renderStationRight(it){
  const body = document.getElementById("rightBody");
  document.getElementById("rightTitle").textContent = `${it.station} • ${it.file}`;

  const k = it.kpis || {};
  const inputs = it.inputs || {};
  const outputs = it.outputs || {};
  const logs = (it.logs || []).join("\n");
  const utilPct = Math.round((it.utilization||0)*100);

  body.innerHTML = `
    <div class="kpiGrid" style="margin-bottom:12px;">
      <div class="kpi"><div class="lab">state</div><div class="val">${safeText(it.state)}</div></div>
      <div class="kpi"><div class="lab">utilization</div><div class="val">${utilPct}%</div></div>
      <div class="kpi"><div class="lab">cycles</div><div class="val">${k.cycles_done ?? 0}</div></div>
      <div class="kpi"><div class="lab">faults</div><div class="val">${k.faults_count ?? 0}</div></div>
    </div>

    <div class="kpiGrid" style="margin-bottom:12px;">
      <div class="kpi"><div class="lab">busy(s)</div><div class="val">${k.busy_total_s ?? 0}</div></div>
      <div class="kpi"><div class="lab">rx packets</div><div class="val">${k.rx_packets ?? 0}</div></div>
      <div class="kpi"><div class="lab">start pulses</div><div class="val">${k.start_pulses ?? 0}</div></div>
      <div class="kpi"><div class="lab">last cycle ms</div><div class="val">${k.last_cycle_ms ?? "-"}</div></div>
    </div>

    <div class="charts" style="grid-template-columns: 1fr;">
      <div>
        <div class="sub" style="margin:0 0 8px;">Cycle time trend (ms)</div>
        <canvas id="lineCycle" width="1200" height="360"></canvas>
      </div>
    </div>

    <div class="twoCol" style="margin-top:12px;">
      <pre id="preInputs"></pre>
      <pre id="preOutputs"></pre>
    </div>

    <div class="sub" style="margin:10px 0 6px;">Live logs</div>
    <pre id="preLogs" style="max-height: 360px;"></pre>
  `;

  document.getElementById("preInputs").textContent = "Inputs:\n" + fmt(inputs);
  document.getElementById("preOutputs").textContent = "Outputs:\n" + fmt(outputs);

  const preLogs = document.getElementById("preLogs");
  preLogs.textContent = logs || "(no lines yet)";
  setTimeout(()=>{ preLogs.scrollTop = preLogs.scrollHeight; }, 0);

  const hist = it.cycle_time_ms_hist || [];
  drawLine(document.getElementById("lineCycle"), hist.slice(-120));
}

function renderRight(items){
  if(selectedId === "ALL"){
    renderOverviewRight();
    return;
  }
  const it = items.find(x => x.id === selectedId);
  if(!it){
    selectedId = "ALL";
    renderOverviewRight();
    return;
  }
  renderStationRight(it);
}

function renderAll(){
  const items = payload.items || [];
  renderLeft(items);
  renderRight(items);

  let last = 0;
  items.forEach(it => { last = Math.max(last, it.updated_wall_ts || 0); });
  document.getElementById("lastUpdate").textContent =
    last ? ("last update: " + new Date(last*1000).toLocaleTimeString()) : "-";
}

/* WS */
const statusEl = document.getElementById("status");
const dotEl = document.getElementById("connDot");
function setConn(ok, text){
  statusEl.textContent = text;
  dotEl.style.color = ok ? "rgba(46,229,157,0.95)" : "rgba(255,77,109,0.95)";
}

const ws = new WebSocket(`ws://${location.host}/ws`);
ws.onopen = ()=> setConn(true, "connected");
ws.onclose = ()=> setConn(false, "disconnected");
ws.onerror = ()=> setConn(false, "error");
ws.onmessage = (msg)=>{
  try {
    payload = JSON.parse(msg.data);
    renderAll();
  } catch(e) {}
};
</script>
</body>
</html>
"""


# -------------------------
# Web handlers
# -------------------------
async def index(req: web.Request):
    return web.Response(text=req.app["html_page"], content_type="text/html")


async def ws_handler(req: web.Request):
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(req)
    engine: Engine = req.app["engine"]
    engine.clients.add(ws)
    try:
        async for _ in ws:
            pass
    finally:
        engine.clients.discard(ws)
    return ws


async def broadcaster(app: web.Application):
    engine: Engine = app["engine"]
    import asyncio
    while True:
        any_update = False
        for _ in range(8):
            if engine.pump_once():
                any_update = True
            else:
                break

        if any_update and engine.clients:
            msg = json.dumps(engine.store.export_payload())
            dead = []
            for ws in list(engine.clients):
                try:
                    await ws.send_str(msg)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                engine.clients.discard(ws)

        await asyncio.sleep(0.12)


# -------------------------
# Auto file discovery
# -------------------------
def _auto_find_logs_recursive() -> List[str]:
    root = os.getcwd()
    found: List[str] = []
    for dp, _dirs, fnames in os.walk(root):
        for fn in fnames:
            full = os.path.join(dp, fn)
            low = full.lower()
            if "logs" not in low:
                continue
            if not (fn.lower().endswith(".log") or fn.lower().endswith(".txt")):
                continue
            found.append(full)
    return found


def build_file_list(args) -> List[str]:
    files: List[str] = []

    if args.logs:
        files.extend(args.logs)
    if args.glob:
        files.extend(glob.glob(args.glob))

    if not files:
        files.extend(_auto_find_logs_recursive())

    if not files:
        pwd = os.getcwd()
        for pat in ("*.txt", "*.log"):
            files.extend(glob.glob(os.path.join(pwd, pat)))

    seen = set()
    out: List[str] = []
    for f in files:
        ff = os.path.abspath(f)
        if ff not in seen and os.path.isfile(ff):
            out.append(ff)
            seen.add(ff)
    return out


# -------------------------
# Main
# -------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", nargs="*",
                    help="Explicit log file paths (optional).")
    ap.add_argument("--glob", default=None,
                    help='Glob pattern, e.g. "logs/*.txt" (optional).')
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--from-start", action="store_true",
                    help="Read existing content at startup (then tail live).")
    ap.add_argument("--code-font", type=int, default=DEFAULT_CODE_FONT_PX,
                    help="Default code/log font size in px (UI textbox can change it).")
    args = ap.parse_args()

    files = build_file_list(args)
    if not files:
        print("No log files found.")
        print('Tip: put your files under a folder that includes "logs" in its name/path, ex: ./logs/ST1_logs.txt')
        return

    engine = Engine(files, from_start=args.from_start)
    engine.start()

    html = HTML_PAGE_TEMPLATE.replace("{{CODE_FONT_PX}}", str(args.code_font))

    app = web.Application()
    app["engine"] = engine
    app["html_page"] = html

    app.router.add_get("/", index)
    app.router.add_get("/ws", ws_handler)

    async def on_startup(app):
        import asyncio
        app["bcast_task"] = asyncio.create_task(broadcaster(app))

    async def on_cleanup(app):
        engine.stop()
        task = app.get("bcast_task")
        if task:
            task.cancel()

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    print("Tailing files:")
    for f in files:
        print(" -", f)
    print(f"\nOpen: http://{args.host}:{args.port}\n")

    web.run_app(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
