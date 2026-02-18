#!/usr/bin/env python3
"""

Install:
  pip install aiohttp

Run:
  python kpi_dashboard_web.py --from-start

Open:
  http://127.0.0.1:8787
"""

from __future__ import annotations

import argparse
import ast
import glob
import html as html_lib
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
DEFAULT_LOG_FILES = [
    "/data/tools/pave/innexis_home/vsi_2025.2/MIUv2/3d_printer_line/3DPrinterLine_6Stations/check.PLC_LineCoordinator.log",
    "/data/tools/pave/innexis_home/vsi_2025.2/MIUv2/3d_printer_line/3DPrinterLine_6Stations/check.ST1_ComponentKitting.log",
    "/data/tools/pave/innexis_home/vsi_2025.2/MIUv2/3d_printer_line/3DPrinterLine_6Stations/check.ST2_FrameCoreAssembly.log",
    "/data/tools/pave/innexis_home/vsi_2025.2/MIUv2/3d_printer_line/3DPrinterLine_6Stations/check.ST3_ElectronicsWiring.log",
    "/data/tools/pave/innexis_home/vsi_2025.2/MIUv2/3d_printer_line/3DPrinterLine_6Stations/check.ST4_CalibrationTesting.log",
    "/data/tools/pave/innexis_home/vsi_2025.2/MIUv2/3d_printer_line/3DPrinterLine_6Stations/check.ST5_QualityInspection.log",
    "/data/tools/pave/innexis_home/vsi_2025.2/MIUv2/3d_printer_line/3DPrinterLine_6Stations/check.ST6_PackagingDispatch.log",
]


# -------------------------
# Station name normalization (IMPORTANT FIX)
# Priority:
#   1) filename: ST1_log.txt -> Station 1, ST2_log.txt -> Station 2
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


def _display_from_text(raw_station: str, source_file: str) -> Tuple[str, str]:
    raw = (raw_station or "").strip()
    candidate = raw if raw else os.path.splitext(os.path.basename(source_file))[0].strip()
    if not candidate:
        candidate = "UNKNOWN"

    low = candidate.lower()
    if "plc" in low:
        return "PLC", (raw or "PLC")

    # Keep readable names for non-numbered blocks/files.
    disp = re.sub(r"^check\.", "", candidate, flags=re.IGNORECASE)
    disp = disp.replace("_", " ").strip()
    if not disp:
        disp = "UNKNOWN"
    return disp, (raw or disp)


def normalize_station_name(raw_station: str, source_file: str) -> Tuple[str, Optional[int], str]:
    raw = (raw_station or "").strip()
    # Force PLC identity before any numeric inference to avoid PLC being mislabeled as "Station 7".
    plc_hint = f"{raw} {os.path.basename(source_file)}".lower()
    if "plc" in plc_hint:
        return "PLC", None, (raw or "PLC")

    n_file = _station_from_filename(source_file)
    n_hdr = _station_from_header(raw)

    n = n_file if n_file is not None else n_hdr
    if n is None:
        disp, raw_keep = _display_from_text(raw, source_file)
        return disp, None, raw_keep
    return f"Station {n}", n, raw or f"Station {n}"


def infer_station_from_file(source_file: str) -> Tuple[str, Optional[int], str]:
    return normalize_station_name("", source_file)


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


LOG_KPI_KEYS = {
    "inventory_ok",
    "any_arm_failed",
    "batch_id",
    "recipe_id",
    "strain_relief_ok",
    "continuity_ok",
    "completed",
    "scrapped",
    "reworks",
    "total",
    "accept",
    "reject",
    "last_accept",
    "packages_completed",
    "arm_cycles",
    "total_repairs",
    "operational_time_s",
    "downtime_s",
    "availability",
    "cycle_time_avg_s",
}
PREFIXED_KPI_RE = re.compile(r"^s\d+_(?P<name>[a-z0-9_]+)$", re.IGNORECASE)


def _to_kpi_scalar(v: Any) -> Any:
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        parsed = _parse_value(v)
        if isinstance(parsed, bool):
            return int(parsed)
        if isinstance(parsed, (int, float)):
            return parsed
        return v
    return str(v)


def _parse_embedded_kpis(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {}
    text = raw.strip()
    if not text.startswith("{"):
        return {}

    for parser in (json.loads, ast.literal_eval):
        try:
            obj = parser(text)
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue
    return {}


def _extract_log_kpis(inputs: Dict[str, Any], outputs: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}

    for src in (inputs, outputs):
        for k, v in src.items():
            key = str(k).strip().lower()
            if key in LOG_KPI_KEYS:
                out[key] = _to_kpi_scalar(v)
                continue

            m = PREFIXED_KPI_RE.match(key)
            if m and m.group("name") in LOG_KPI_KEYS:
                out[key] = _to_kpi_scalar(v)

    # Some stations may dump a packed KPI dict in a single "kpis =" line.
    packed = _parse_embedded_kpis(outputs.get("kpis", inputs.get("kpis")))
    for k, v in packed.items():
        key = f"kpis_{str(k).strip().lower()}"
        out[key] = _to_kpi_scalar(v)

    return out


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

    def _path_sig(self) -> Optional[Tuple[int, int]]:
        try:
            st = os.stat(self.filepath)
            return (int(st.st_dev), int(st.st_ino))
        except OSError:
            return None

    def _fd_sig(self, f) -> Optional[Tuple[int, int]]:
        try:
            st = os.fstat(f.fileno())
            return (int(st.st_dev), int(st.st_ino))
        except OSError:
            return None

    def run(self):
        reopen_from_start = False

        while not self._stop.is_set():
            while not os.path.exists(self.filepath) and not self._stop.is_set():
                time.sleep(0.15)
            if self._stop.is_set():
                return

            try:
                with open(self.filepath, "r", encoding="utf-8", errors="replace") as f:
                    if reopen_from_start or (self.from_start and not self._did_initial_read):
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

                    need_reopen = False
                    while not self._stop.is_set():
                        # If producer rotates/recreates file, reopen from start of the new file.
                        path_sig = self._path_sig()
                        fd_sig = self._fd_sig(f)
                        if path_sig is None or fd_sig is None or path_sig != fd_sig:
                            need_reopen = True
                            reopen_from_start = True
                            # Preserve any complete in-memory block before switching file handle.
                            self._emit_snap(self.parser.flush())
                            self.parser.reset()
                            break

                        try:
                            cur_size = os.path.getsize(self.filepath)
                        except OSError:
                            cur_size = None

                        # file truncated
                        if cur_size is not None and cur_size < self._pos:
                            # Flush current parsed block before reset on truncate.
                            self._emit_snap(self.parser.flush())
                            f.seek(0, os.SEEK_SET)
                            self._pos = f.tell()
                            self.parser.reset()

                        line = f.readline()
                        if not line:
                            time.sleep(0.08)
                            continue

                        self._pos = f.tell()
                        self._process_line(line)

                    if need_reopen:
                        time.sleep(0.06)
                        continue
                    return

            except Exception as e:
                self.out_q.put(("error", {"file": self.filepath, "error": str(e)}))
                time.sleep(0.15)

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
    first_vsi_ns: Optional[int] = None
    last_vsi_ns: Optional[int] = None

    inputs: Dict[str, Any] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)

    cycles_done: int = 0
    faults_count: int = 0
    start_pulses: int = 0
    reset_pulses: int = 0
    stop_asserts: int = 0

    busy_total_ns: int = 0
    cycle_time_ms_hist: deque = field(
        default_factory=lambda: deque(maxlen=200))
    utilization_hist_pct: deque = field(
        default_factory=lambda: deque(maxlen=200))
    batch_id_hist: deque = field(
        default_factory=lambda: deque(maxlen=200))
    log_kpis: Dict[str, Any] = field(default_factory=dict)

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

    def ensure_station_for_file(self, file: str):
        station, station_num, station_raw = infer_station_from_file(file)
        self.get(file, station, station_raw, station_num)

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
        _ = ev

    def handle_snapshot(self, snap: Snapshot):
        st = self.get(snap.source_file, snap.station,
                      snap.station_raw, snap.station_num)

        now = time.time()
        if st.first_wall_ts == 0:
            st.first_wall_ts = now
        st.last_wall_ts = now
        if st.first_vsi_ns is None:
            st.first_vsi_ns = snap.vsi_time_ns

        if st.last_vsi_ns is not None:
            delta = max(0, snap.vsi_time_ns - st.last_vsi_ns)
            prev_busy = _to_int01(st.outputs.get("busy", 0))
            if prev_busy == 1:
                st.busy_total_ns += delta

        st.last_vsi_ns = snap.vsi_time_ns
        st.inputs = snap.inputs
        st.outputs = snap.outputs
        st.log_kpis.update(_extract_log_kpis(st.inputs, st.outputs))

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

        # Keep utilization trend history in percent for station charting.
        vsi_elapsed_ns = 0
        if st.first_vsi_ns is not None:
            vsi_elapsed_ns = max(0, snap.vsi_time_ns - st.first_vsi_ns)
        util_now = (st.busy_total_ns / vsi_elapsed_ns) if vsi_elapsed_ns > 0 else 0.0
        util_now = max(0.0, min(1.0, util_now))
        st.utilization_hist_pct.append(round(util_now * 100.0, 3))

        # Keep batch-id history for station charting.
        def _as_int(v):
            if isinstance(v, bool):
                return int(v)
            if isinstance(v, (int, float)):
                return int(v)
            if isinstance(v, str):
                p = _parse_value(v)
                if isinstance(p, bool):
                    return int(p)
                if isinstance(p, (int, float)):
                    return int(p)
            return None

        batch_val = _as_int(st.inputs.get("batch_id"))
        if batch_val is None:
            batch_val = _as_int(st.outputs.get("batch_id"))
        if batch_val is None and st.station_num is not None:
            pref = f"S{st.station_num}_batch_id"
            batch_val = _as_int(st.inputs.get(pref))
            if batch_val is None:
                batch_val = _as_int(st.outputs.get(pref))
        if batch_val is not None:
            st.batch_id_hist.append(batch_val)

        ctm = snap.outputs.get("cycle_time_ms", None)
        if isinstance(ctm, (int, float)):
            st.cycle_time_ms_hist.append(float(ctm))

    def _state(self, st: StationStats) -> str:
        # PLC snapshots use aggregated Sx_* signals instead of generic ready/busy/fault.
        if str(st.station).upper() == "PLC":
            fault_vals = [
                _to_int01(v) for k, v in st.inputs.items()
                if re.match(r"^S\d+_fault$", str(k), re.IGNORECASE)
            ]
            busy_vals = [
                _to_int01(v) for k, v in st.inputs.items()
                if re.match(r"^S\d+_busy$", str(k), re.IGNORECASE)
            ]
            ready_vals = [
                _to_int01(v) for k, v in st.inputs.items()
                if re.match(r"^S\d+_ready$", str(k), re.IGNORECASE)
            ]
            stop_vals = [
                _to_int01(v) for k, v in st.outputs.items()
                if re.match(r"^S\d+_cmd_stop$", str(k), re.IGNORECASE)
            ]

            if fault_vals and any(x == 1 for x in fault_vals):
                return "FAULT"
            if stop_vals and all(x == 1 for x in stop_vals):
                return "STOPPED"
            if busy_vals and any(x == 1 for x in busy_vals):
                return "RUNNING"
            if ready_vals and any(x == 1 for x in ready_vals):
                return "READY"
            return "STOPPED"

        fault = _to_int01(st.outputs.get("fault", 0))
        busy = _to_int01(st.outputs.get("busy", 0))
        ready = _to_int01(st.outputs.get("ready", 0))
        cmd_stop = _to_int01(st.inputs.get("cmd_stop", 0))

        if fault == 1:
            return "FAULT"
        if cmd_stop == 1:
            return "STOPPED"
        if busy == 1:
            return "RUNNING"
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

            wall_elapsed = (st.last_wall_ts - st.first_wall_ts) if (
                st.first_wall_ts and st.last_wall_ts) else 0.0
            vsi_elapsed_ns = 0
            if st.first_vsi_ns is not None and st.last_vsi_ns is not None:
                vsi_elapsed_ns = max(0, st.last_vsi_ns - st.first_vsi_ns)

            busy_s = st.busy_total_ns / 1e9
            util = (st.busy_total_ns / vsi_elapsed_ns) if vsi_elapsed_ns > 0 else 0.0
            util = max(0.0, min(1.0, util))
            elapsed_s = (vsi_elapsed_ns / 1e9) if vsi_elapsed_ns > 0 else wall_elapsed

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
                "elapsed_s": round(elapsed_s, 2),
                "utilization": round(util, 3),
                "inputs": st.inputs,
                "outputs": st.outputs,
                "log": lines,
                "kpis": {
                    "cycles_done": st.cycles_done,
                    "faults_count": st.faults_count,
                    "start_pulses": st.start_pulses,
                    "reset_pulses": st.reset_pulses,
                    "stop_asserts": st.stop_asserts,
                    "busy_total_s": round(busy_s, 3),
                    "last_cycle_ms": (st.cycle_time_ms_hist[-1] if st.cycle_time_ms_hist else None),
                    **st.log_kpis,
                },
                "cycle_time_ms_hist": list(st.cycle_time_ms_hist),
                "utilization_hist_pct": list(st.utilization_hist_pct),
                "batch_id_hist": list(st.batch_id_hist),
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
        for f in files:
            self.store.ensure_station_for_file(f)
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
        <button id="exportKpiBtn" class="btn">Export KPI Report</button>
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
        <div class="bd" id="rightBody">
          <pre id="serverFallback">{{SERVER_FALLBACK_TEXT}}</pre>
        </div>
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
  const exportBtn = document.getElementById("exportKpiBtn");
  function applyFromBox(){
    const v = parseInt(inp.value || "", 10);
    if(isNaN(v)) return;
    setCodeFont(v);
  }
  btn.onclick = applyFromBox;
  inp.addEventListener("keydown", (e)=>{ if(e.key === "Enter") applyFromBox(); });
  if(exportBtn) exportBtn.onclick = exportKpiReport;
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

function compressDiscreteChanges(values){
  if(!values || !values.length) return [];
  const out = [values[0]];
  for(let i=1;i<values.length;i++){
    if(values[i] !== values[i-1]) out.push(values[i]);
  }
  if(values.length > 1 && out[out.length-1] !== values[values.length-1]){
    out.push(values[values.length-1]);
  }
  return out;
}

function drawStepLine(canvas, values){
  const ctx = canvas.getContext("2d");
  clearCanvas(ctx, canvas);
  if(!values || values.length < 1){
    ctx.fillStyle = "rgba(255,255,255,0.7)";
    ctx.fillText("No data", 10, 20);
    return;
  }
  // Allow single-point series by rendering it as a flat two-point step.
  const pts = (values.length === 1) ? [values[0], values[0]] : values;

  const pad = 36;
  const w = canvas.width, h = canvas.height;
  const minRaw = Math.min(...pts);
  const maxRaw = Math.max(...pts);
  let minV = Math.floor(minRaw);
  let maxV = Math.ceil(maxRaw);
  if(minV === maxV){
    minV -= 1;
    maxV += 1;
  }
  const span = Math.max(1, maxV - minV);

  ctx.strokeStyle = "rgba(255,255,255,0.12)";
  ctx.beginPath();
  ctx.moveTo(pad, pad);
  ctx.lineTo(pad, h-pad);
  ctx.lineTo(w-pad, h-pad);
  ctx.stroke();

  const toY = (v) => (h-pad) - ((v - minV) / span) * (h - pad * 2);
  const toX = (i) => pad + (i / (pts.length - 1)) * (w - pad * 2);

  ctx.strokeStyle = "rgba(255,200,87,0.95)";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(toX(0), toY(pts[0]));
  for(let i=1;i<pts.length;i++){
    const xPrev = toX(i-1);
    const x = toX(i);
    const yPrev = toY(pts[i-1]);
    const y = toY(pts[i]);
    ctx.lineTo(x, yPrev); // horizontal hold
    if(y !== yPrev){
      ctx.lineTo(x, y); // vertical jump
    }
  }
  ctx.stroke();
}

/* ===== app state ===== */
let payload = __INITIAL_PAYLOAD__;
let selectedId = "ALL";

function safeText(s){ return (s===undefined || s===null) ? "" : String(s); }
function fmt(obj){ try { return JSON.stringify(obj, null, 2); } catch(e){ return String(obj); } }
function kpiLabel(name){ return safeText(name).replace(/_/g, " "); }

const BASE_KPI_KEYS = new Set([
  "cycles_done",
  "faults_count",
  "start_pulses",
  "reset_pulses",
  "stop_asserts",
  "busy_total_s",
  "last_cycle_ms"
]);

function renderExtraLogKpis(k){
  const entries = Object.entries(k || {}).filter(([kk]) => !BASE_KPI_KEYS.has(kk));
  if(!entries.length) return "";
  const cards = entries.map(([kk, vv]) => {
    return `<div class="kpi"><div class="lab">${kpiLabel(kk)}</div><div class="val">${safeText(vv)}</div></div>`;
  }).join("");
  return `
    <div class="sub" style="margin:4px 0 8px;">KPIs parsed from log</div>
    <div class="kpiGrid" style="margin-bottom:12px;">${cards}</div>
  `;
}

function stateClass(state){
  if(state==="READY") return "statePill ready";
  if(state==="RUNNING") return "statePill run";
  if(state==="FAULT") return "statePill fault";
  if(state==="STOPPED") return "statePill stop";
  return "statePill unk";
}

function setSelected(id){ selectedId = id; renderAll(); }

function stationNumberOf(it){
  const direct = Number(it?.station_num);
  if(Number.isFinite(direct)) return direct;
  const m = String(it?.station || "").match(/(\d+)/);
  return m ? Number(m[1]) : NaN;
}

function isProductionStation(it){
  const n = stationNumberOf(it);
  return Number.isFinite(n) && n >= 1 && n <= 6;
}

function normalizeStateValue(state){
  const s = String(state || "UNKNOWN").toUpperCase();
  if(s === "STOP") return "STOPPED";
  return s;
}

function escHtml(s){
  return safeText(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function exportKpiReport(){
  const items = payload.items || [];
  const prodItems = items
    .filter(isProductionStation)
    .sort((a, b) => {
      const an = stationNumberOf(a);
      const bn = stationNumberOf(b);
      if(an !== bn) return an - bn;
      return String(a.station || "").localeCompare(String(b.station || ""));
    });

  const stations = prodItems.slice(0, 6);
  const sc = { READY: 0, RUNNING: 0, FAULT: 0, STOPPED: 0, UNKNOWN: 0 };
  prodItems.forEach(it => {
    const st = normalizeStateValue(it.state);
    sc[st] = (sc[st] || 0) + 1;
  });

  const s = payload.summary || {};
  const faultsMap = s.faults_by_station || {};
  const totalFaults = Object.values(faultsMap).reduce((sum, v) => sum + (Number(v) || 0), 0);

  const rows = stations.map((it, idx) => {
    const k = it.kpis || {};
    const utilPct = Math.round((Number(it.utilization) || 0) * 100);
    return `
      <tr>
        <td>${idx + 1}</td>
        <td>${escHtml(it.station)}</td>
        <td>${escHtml(it.file)}</td>
        <td>${escHtml(it.state || "UNKNOWN")}</td>
        <td>${k.cycles_done ?? 0}</td>
        <td>${k.faults_count ?? 0}</td>
        <td>${utilPct}%</td>
        <td>${k.busy_total_s ?? 0}</td>
        <td>${k.last_cycle_ms ?? "-"}</td>
      </tr>
    `;
  }).join("");

  const generatedAt = new Date().toLocaleString();
  const html = `<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>KPI Export</title>
  <style>
    body{ font-family: Segoe UI, Arial, sans-serif; margin: 24px; color:#111; }
    h1{ margin:0 0 8px; font-size: 24px; }
    .meta{ color:#444; margin-bottom: 18px; font-size: 13px; }
    .cards{ display:grid; grid-template-columns: repeat(4, minmax(120px, 1fr)); gap:10px; margin: 0 0 16px; }
    .card{ border:1px solid #d9d9d9; border-radius:10px; padding:10px; }
    .lab{ color:#666; font-size:12px; margin-bottom:4px; }
    .val{ font-size:20px; font-weight:700; }
    h2{ margin:18px 0 8px; font-size:18px; }
    table{ width:100%; border-collapse: collapse; font-size: 13px; }
    th, td{ border:1px solid #d9d9d9; padding:8px; text-align:left; }
    th{ background:#f3f6f8; }
    @media print{
      body{ margin:12mm; }
      .no-print{ display:none; }
    }
  </style>
</head>
<body>
  <h1>Project KPI Report</h1>
  <div class="meta">Generated: ${escHtml(generatedAt)}</div>

  <h2>Overview</h2>
  <div class="cards">
    <div class="card"><div class="lab">Stations</div><div class="val">${prodItems.length}</div></div>
    <div class="card"><div class="lab">Running</div><div class="val">${sc.RUNNING ?? 0}</div></div>
    <div class="card"><div class="lab">Faults</div><div class="val">${totalFaults}</div></div>
    <div class="card"><div class="lab">Stopped</div><div class="val">${sc.STOPPED ?? 0}</div></div>
  </div>

  <h2>Top 6 Stations KPI</h2>
  <table>
    <thead>
      <tr>
        <th>#</th>
        <th>Station</th>
        <th>File</th>
        <th>State</th>
        <th>Cycles</th>
        <th>Faults</th>
        <th>Utilization</th>
        <th>Busy(s)</th>
        <th>Last Cycle ms</th>
      </tr>
    </thead>
    <tbody>
      ${rows || '<tr><td colspan="9">No station data</td></tr>'}
    </tbody>
  </table>
</body>
</html>`;

  const w = window.open("", "_blank");
  if(!w){
    alert("Popup blocked. Please allow popups and click Export KPI Report again.");
    return;
  }
  w.document.open();
  w.document.write(html);
  w.document.close();
  w.focus();
  setTimeout(() => { w.print(); }, 120);
}

/* left list */
function renderLeft(items){
  const list = document.getElementById("selList");
  list.innerHTML = "";
  const listItems = (items || []).filter(isProductionStation);

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

  listItems.forEach(it=>{
    const el = document.createElement("div");
    el.className = "sel" + (selectedId===it.id ? " active" : "");
    el.onclick = ()=> setSelected(it.id);
    const utilPct = Math.round((it.utilization||0)*100);
    const isPlc = String(it.station || "").toUpperCase() === "PLC";
    const metaText = isPlc
      ? `${safeText(it.file)} • line coordinator`
      : `${safeText(it.file)} • util ${utilPct}% • cycles ${it.kpis?.cycles_done ?? 0}`;
    el.innerHTML = `
      <div class="left">
        <div class="name">${safeText(it.station)}</div>
        <div class="meta">${metaText}</div>
      </div>
      <div class="${stateClass(it.state)}">${safeText(it.state)}</div>
    `;
    list.appendChild(el);
  });

  document.getElementById("stationCount").textContent = listItems.length;
}

/* right overview */
function renderOverviewRight(){
  const s = payload.summary || {};
  const items = payload.items || [];
  const prodItems = items.filter(isProductionStation);
  const sc = { READY: 0, RUNNING: 0, FAULT: 0, STOPPED: 0, UNKNOWN: 0 };
  prodItems.forEach(it => {
    const st = normalizeStateValue(it.state);
    sc[st] = (sc[st] || 0) + 1;
  });
  const body = document.getElementById("rightBody");
  document.getElementById("rightTitle").textContent = "Overview";

  body.innerHTML = `
    <div class="kpiGrid" style="margin-bottom:12px;">
      <div class="kpi"><div class="lab">Stations</div><div class="val" id="k_stations">-</div></div>
      <div class="kpi"><div class="lab">Running</div><div class="val" id="k_running">-</div></div>
      <div class="kpi"><div class="lab">Faults</div><div class="val" id="k_fault">-</div></div>
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

  document.getElementById("k_stations").textContent = String(prodItems.length);
  document.getElementById("k_running").textContent = String(sc.RUNNING ?? 0);
  const faultsMap = s.faults_by_station || {};
  const totalFaults = Object.values(faultsMap).reduce((sum, v) => sum + (Number(v) || 0), 0);
  document.getElementById("k_fault").textContent = String(totalFaults);
  document.getElementById("k_stopped").textContent = String(sc.STOPPED ?? 0);

  const stateOrder = ["READY","RUNNING","STOPPED"];
  const stateColors = {
    READY: "rgba(46,229,157,0.85)",
    RUNNING: "rgba(0,178,178,0.90)",
    STOPPED: "rgba(255,200,87,0.90)"
  };
  const labels = stateOrder;
  const values = labels.map(x => sc[x] || 0);
  const colors = labels.map(x => stateColors[x]);
  drawPie(document.getElementById("pieStates"), labels, values, colors);

  const cyclesMap = s.cycles_by_station || {};
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
  const isPlc = String(it.station || "").toUpperCase() === "PLC";
  document.getElementById("rightTitle").textContent = isPlc ? "PLC" : `${it.station} • ${it.file}`;

  const k = it.kpis || {};
  const inputs = it.inputs || {};
  const outputs = it.outputs || {};
  const log = (it.log || []).join("\n");
  const utilPct = Math.round((it.utilization||0)*100);
  const stateCard = isPlc ? "" : `<div class="kpi"><div class="lab">state</div><div class="val">${safeText(it.state)}</div></div>`;

  body.innerHTML = `
    <div class="kpiGrid" style="margin-bottom:12px;">
      ${stateCard}
      <div class="kpi"><div class="lab">utilization</div><div class="val">${utilPct}%</div></div>
      <div class="kpi"><div class="lab">cycles</div><div class="val">${k.cycles_done ?? 0}</div></div>
      <div class="kpi"><div class="lab">faults</div><div class="val">${k.faults_count ?? 0}</div></div>
    </div>

    <div class="kpiGrid" style="margin-bottom:12px;">
      <div class="kpi"><div class="lab">busy(s)</div><div class="val">${k.busy_total_s ?? 0}</div></div>
      <div class="kpi"><div class="lab">start pulses</div><div class="val">${k.start_pulses ?? 0}</div></div>
      <div class="kpi"><div class="lab">last cycle ms</div><div class="val">${k.last_cycle_ms ?? "-"}</div></div>
    </div>

    ${renderExtraLogKpis(k)}

    <div class="charts" style="grid-template-columns: 1fr;">
      <div>
        <div class="sub" style="margin:0 0 8px;">Utilization trend (%)</div>
        <canvas id="lineUtil" width="1200" height="360"></canvas>
      </div>
    </div>

    <div class="twoCol" style="margin-top:12px;">
      <pre id="preInputs"></pre>
      <pre id="preOutputs"></pre>
    </div>

    <div class="sub" style="margin:10px 0 6px;">Live log</div>
    <pre id="prelog" style="max-height: 360px;"></pre>
  `;

  document.getElementById("preInputs").textContent = "Inputs:\n" + fmt(inputs);
  document.getElementById("preOutputs").textContent = "Outputs:\n" + fmt(outputs);

  const prelog = document.getElementById("prelog");
  prelog.textContent = log || "(no lines yet)";
  setTimeout(()=>{ prelog.scrollTop = prelog.scrollHeight; }, 0);

  const hist = it.utilization_hist_pct || [];
  drawLine(document.getElementById("lineUtil"), hist.slice(-120));
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
  const selected = selectedId === "ALL" ? null : items.find(x => x.id === selectedId);
  const hideLastForPlc = selected && String(selected.station || "").toUpperCase() === "PLC";
  document.getElementById("lastUpdate").textContent = hideLastForPlc
    ? ""
    : (last ? ("last update: " + new Date(last*1000).toLocaleTimeString()) : "-");
}

/* WS */
const statusEl = document.getElementById("status");
const dotEl = document.getElementById("connDot");
function setConn(ok, text){
  statusEl.textContent = text;
  dotEl.style.color = ok ? "rgba(46,229,157,0.95)" : "rgba(255,77,109,0.95)";
}

const ws = new WebSocket(`ws://${location.host}/ws`);
ws.onopen = ()=> { setConn(true, "connected"); renderAll(); };
ws.onclose = ()=> setConn(false, "disconnected");
ws.onerror = ()=> setConn(false, "error");
ws.onmessage = (msg)=>{
  try {
    payload = JSON.parse(msg.data);
    renderAll();
  } catch(e) {}
};

async function bootstrapPayload(){
  try{
    const r = await fetch("/api/payload", { cache: "no-store" });
    if(r.ok){
      payload = await r.json();
    }
  }catch(e){}
  renderAll();
}
bootstrapPayload();
</script>
</body>
</html>
"""


# -------------------------
# Web handlers
# -------------------------
def _payload_to_js_literal(payload: Dict[str, Any]) -> str:
    # Prevent `</script>` and HTML entity edge cases when embedding JSON in script.
    return (
        json.dumps(payload, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def _server_fallback_text(payload: Dict[str, Any]) -> str:
    items = payload.get("items", [])
    lines = [f"Server payload: {len(items)} station(s)"]
    for it in items[:30]:
        lines.append(
            f"- {it.get('station', '?')} [{it.get('state', 'UNKNOWN')}] from {it.get('file', '?')}"
        )
    if len(items) > 30:
        lines.append(f"... and {len(items) - 30} more")
    return "\n".join(lines)


async def index(req: web.Request):
    engine: Engine = req.app["engine"]
    payload = engine.store.export_payload()

    page = req.app["html_page"]
    page = page.replace("__INITIAL_PAYLOAD__", _payload_to_js_literal(payload))
    page = page.replace("{{SERVER_FALLBACK_TEXT}}", html_lib.escape(_server_fallback_text(payload)))
    return web.Response(text=page, content_type="text/html")


async def api_payload(req: web.Request):
    engine: Engine = req.app["engine"]
    return web.json_response(engine.store.export_payload())


async def ws_handler(req: web.Request):
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(req)
    engine: Engine = req.app["engine"]
    engine.clients.add(ws)
    try:
        await ws.send_str(json.dumps(engine.store.export_payload()))
    except Exception:
        pass
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
def _auto_find_log_recursive() -> List[str]:
    root = os.getcwd()
    found: List[str] = []
    for dp, _dirs, fnames in os.walk(root):
        for fn in fnames:
            full = os.path.join(dp, fn)
            low = full.lower()
            if "log" not in low:
                continue
            if not (fn.lower().endswith(".log") or fn.lower().endswith(".txt")):
                continue
            found.append(full)
    return found


def build_file_list(args) -> List[str]:
    files: List[str] = []

    if args.log:
        files.extend(args.log)
    if args.glob:
        files.extend(glob.glob(args.glob))

    if not files:
        files.extend([p for p in DEFAULT_LOG_FILES if os.path.isfile(p)])

    if not files:
        files.extend(_auto_find_log_recursive())

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
    ap.add_argument("--log", nargs="*",
                    help="Explicit log file paths (optional).")
    ap.add_argument("--glob", default=None,
                    help='Glob pattern, e.g. "log/*.txt" (optional).')
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
        print('Tip: put your files under a folder that includes "log" in its name/path, ex: ./log/ST1_log.txt')
        return

    engine = Engine(files, from_start=args.from_start)
    engine.start()

    html = HTML_PAGE_TEMPLATE.replace("{{CODE_FONT_PX}}", str(args.code_font))

    app = web.Application()
    app["engine"] = engine
    app["html_page"] = html

    app.router.add_get("/", index)
    app.router.add_get("/api/payload", api_payload)
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
