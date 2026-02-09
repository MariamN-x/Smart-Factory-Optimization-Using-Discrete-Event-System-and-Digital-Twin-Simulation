
from __future__ import annotations

import os
import re
import sys
import json
import math
import time
import csv
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple

# -------------------------
# Qt imports
# -------------------------
try:
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QListWidget, QListWidgetItem, QLabel, QPushButton, QFileDialog,
        QSplitter, QGroupBox, QGridLayout, QPlainTextEdit, QComboBox,
        QCheckBox, QMessageBox, QLineEdit
    )
except Exception as e:
    print("ERROR: PySide6 import failed:", e)
    sys.exit(1)

# -------------------------
# Matplotlib (Qt embedding)
# -------------------------
try:
    import matplotlib
    matplotlib.use("QtAgg")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
except Exception as e:
    print("ERROR: matplotlib import failed:", e)
    sys.exit(1)


# ============================================================
# Utilities
# ============================================================

DEFAULT_LOG_FOLDER = "/data/tools/pave/innexis_home/vsi_2025.2/all_stations/3d_printer_line/3DPrinterLine_6Stations"

PLC_FILENAME_HINT = "check.PLC_LineCoordinator.log"
STATION_FILE_RE = re.compile(r"check\.(ST(?P<n>\d+)_.*)\.log$", re.IGNORECASE)

# From PLC block inputs: keys are like S1_ready, S2_cycle_time_ms, ...
PLC_SIGNAL_RE = re.compile(r"^\s*(?P<key>[A-Za-z0-9_>\-]+)\s*=\s*(?P<val>.+?)\s*$")
VSI_TIME_RE = re.compile(r"^\s*VSI time:\s*(?P<ns>\d+)\s*ns\s*$")

PLC_BLOCK_START_RE = re.compile(r"^\+=PLC_LineCoordinator\+=\s*$")
ST_BLOCK_START_RE = re.compile(r"^\+=ST(?P<n>\d+)_.*\+=\s*$")

PLC_STATE_RE = re.compile(r"^\s*PLC State:\s*(?P<state>.+?)\s*$")
DONE_LATCHES_RE = re.compile(r"^\s*Done latches:\s*(?P<rest>.+?)\s*$")
START_SENT_RE = re.compile(r"^\s*Start sent:\s*(?P<rest>.+?)\s*$")
BUFFERS_RE = re.compile(r"^\s*Buffers:\s*(?P<rest>.+?)\s*$")
FINISHED_RE = re.compile(r"^\s*Finished products:\s*(?P<count>\d+)\s*$")

PLC_SCAN_RE = re.compile(r"^===\s*PLC SCAN\s*(?P<scan>\d+)\s*===\s*$")
SIM_TIME_RE = re.compile(r"^\s*Sim time:\s*(?P<s>\d+(?:\.\d+)?)s\s*$")

ST_INTERNAL_RE = re.compile(r"^\s*Internal:\s*(?P<rest>.+?)\s*$")
ST_SIMSTATE_RE = re.compile(r"^\s*SimState:\s*(?P<rest>.+?)\s*$")
ST_SIMPY_RE = re.compile(r"^\s*SimPy env\.now\s*=\s*(?P<s>\d+(?:\.\d+)?)s\s*$")

KV_INLINE_LIST_RE = re.compile(r"\s*(?P<k>[A-Za-z0-9_>\-]+)\s*=\s*(?P<v>[^,]+)\s*")


def safe_float(x: str) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return None


def parse_bool_int_float(raw: str) -> Any:
    v = raw.strip()
    # bool words
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    # numeric bools often appear as 0/1 for stations
    # keep int if it's int-like
    if re.fullmatch(r"-?\d+", v):
        try:
            return int(v)
        except Exception:
            return v
    # float
    if re.fullmatch(r"-?\d+\.\d+(?:[eE]-?\d+)?", v) or re.fullmatch(r"-?\d+(?:[eE]-?\d+)", v):
        f = safe_float(v)
        return f if f is not None else v
    # sometimes values are like "80.07968127490041"
    f = safe_float(v)
    if f is not None:
        return f
    return v


def parse_inline_kv_list(rest: str) -> Dict[str, Any]:
    """
    Parses: "S1=True, S2=False, S3=True"
    or buffers: "S1->S2=0, S2->S3=1"
    """
    out: Dict[str, Any] = {}
    for m in KV_INLINE_LIST_RE.finditer(rest):
        k = m.group("k").strip()
        v = parse_bool_int_float(m.group("v").strip())
        out[k] = v
    return out


# ============================================================
# Data model with downsampling
# ============================================================

@dataclass
class EntitySeries:
    name: str
    t: List[float] = field(default_factory=list)
    signals: Dict[str, List[Any]] = field(default_factory=dict)
    events: Dict[str, List[Tuple[float, Any]]] = field(default_factory=dict)
    last_t: float = 0.0
    max_points: int = 20000

    def _ensure_key(self, key: str):
        if key not in self.signals:
            self.signals[key] = []

    def append_snapshot(self, t_s: float, snapshot: Dict[str, Any], ensure_all_keys: bool = True):
        """
        Append one timepoint. For keys not in snapshot, fill None.
        """
        if self.t and t_s < self.t[-1]:
            # out-of-order timestamp, ignore to keep charts stable
            return

        self.t.append(t_s)
        self.last_t = t_s

        # ensure existing keys get a value this timestamp
        if ensure_all_keys:
            for k in self.signals.keys():
                self.signals[k].append(None)

        # write snapshot keys
        for k, v in snapshot.items():
            self._ensure_key(k)
            # if we just appended None for existing keys, ensure this key list is aligned
            if len(self.signals[k]) < len(self.t):
                # fill missing points up to current index
                missing = len(self.t) - len(self.signals[k])
                self.signals[k].extend([None] * missing)
            self.signals[k][-1] = v

        self._downsample_if_needed()

    def add_event(self, event_name: str, t_s: float, value: Any):
        self.events.setdefault(event_name, []).append((t_s, value))

    def _downsample_if_needed(self):
        if len(self.t) <= self.max_points:
            return
        # simple decimation by factor 2
        idx = list(range(0, len(self.t), 2))
        self.t = [self.t[i] for i in idx]
        for k, arr in self.signals.items():
            self.signals[k] = [arr[i] if i < len(arr) else None for i in idx]
        for ev, arr in self.events.items():
            # keep events that still fall in range; simplest: keep all (events small typically)
            self.events[ev] = arr


@dataclass
class TimeSeriesDB:
    plc: EntitySeries = field(default_factory=lambda: EntitySeries("PLC"))
    stations: Dict[str, EntitySeries] = field(default_factory=dict)  # S1..S6
    warnings: List[str] = field(default_factory=list)
    files: Dict[str, str] = field(default_factory=dict)  # entity -> path

    def get_station(self, sn: str) -> EntitySeries:
        if sn not in self.stations:
            self.stations[sn] = EntitySeries(sn)
        return self.stations[sn]

    def warn(self, msg: str):
        if len(self.warnings) < 2000:
            self.warnings.append(msg)


# ============================================================
# Log discovery
# ============================================================

def find_logs(folder: str, recursive: bool = True) -> Dict[str, str]:
    """
    Returns mapping:
      "PLC" -> path
      "S1".."S6" -> path
    """
    found: Dict[str, str] = {}
    if not os.path.isdir(folder):
        return found

    def consider(path: str):
        base = os.path.basename(path)
        if base == PLC_FILENAME_HINT:
            found["PLC"] = path
            return
        m = STATION_FILE_RE.match(base)
        if m:
            n = int(m.group("n"))
            if 1 <= n <= 6:
                found[f"S{n}"] = path

    if recursive:
        for root, _, files in os.walk(folder):
            for f in files:
                if f.startswith("check.") and f.endswith(".log"):
                    consider(os.path.join(root, f))
    else:
        for f in os.listdir(folder):
            if f.startswith("check.") and f.endswith(".log"):
                consider(os.path.join(folder, f))

    return found


# ============================================================
# Parsers
# ============================================================

class PLCLogParser:
    """
    Streaming parser for PLC log:
    - PLC blocks with Inputs/Outputs and PLC State/Buffers/Finished products etc.
    - PLC scan sections for Sim time
    """

    def __init__(self, db: TimeSeriesDB):
        self.db = db

        self._last_sim_time_s: Optional[float] = None
        self._pending_scan_sim_time_s: Optional[float] = None

        self._in_plc_block = False
        self._section: Optional[str] = None  # "inputs" | "outputs" | None

        self._current_vsi_ns: Optional[int] = None
        self._current_snapshot: Dict[str, Any] = {}

        self._current_state: Optional[str] = None
        self._current_done_latches: Dict[str, Any] = {}
        self._current_start_sent: Dict[str, Any] = {}
        self._current_buffers: Dict[str, Any] = {}
        self._current_finished: Optional[int] = None

    def parse_file(self, path: str):
        try:
            with open(path, "r", errors="replace") as f:
                for line_no, line in enumerate(f, start=1):
                    self._parse_line(line.rstrip("\n"), line_no, path)
            # close any open block
            self._flush_block(final=True)
        except Exception as e:
            self.db.warn(f"PLC parse failed for {path}: {e}")

    def _parse_line(self, line: str, line_no: int, path: str):
        # PLC SCAN
        if PLC_SCAN_RE.match(line):
            self._pending_scan_sim_time_s = None
            return

        m = SIM_TIME_RE.match(line)
        if m:
            self._last_sim_time_s = float(m.group("s"))
            self._pending_scan_sim_time_s = self._last_sim_time_s
            return

        # PLC block start
        if PLC_BLOCK_START_RE.match(line):
            self._flush_block(final=False)
            self._in_plc_block = True
            self._section = None
            self._current_vsi_ns = None
            self._current_snapshot = {}
            self._current_state = None
            self._current_done_latches = {}
            self._current_start_sent = {}
            self._current_buffers = {}
            self._current_finished = None
            return

        if not self._in_plc_block:
            return

        # VSI time
        m = VSI_TIME_RE.match(line)
        if m:
            self._current_vsi_ns = int(m.group("ns"))
            return

        # sections
        if line.strip() == "Inputs:":
            self._section = "inputs"
            return
        if line.strip() == "Outputs:":
            self._section = "outputs"
            return

        # PLC State
        m = PLC_STATE_RE.match(line)
        if m:
            self._current_state = m.group("state").strip()
            return

        # Done latches / Start sent / Buffers
        m = DONE_LATCHES_RE.match(line)
        if m:
            self._current_done_latches = parse_inline_kv_list(m.group("rest"))
            return

        m = START_SENT_RE.match(line)
        if m:
            self._current_start_sent = parse_inline_kv_list(m.group("rest"))
            return

        m = BUFFERS_RE.match(line)
        if m:
            self._current_buffers = parse_inline_kv_list(m.group("rest"))
            return

        m = FINISHED_RE.match(line)
        if m:
            self._current_finished = int(m.group("count"))
            return

        # key=val lines in inputs/outputs
        if self._section in ("inputs", "outputs"):
            m = PLC_SIGNAL_RE.match(line)
            if m:
                k = m.group("key").strip()
                v = parse_bool_int_float(m.group("val"))
                # store with prefix so we can separate inputs/outputs if needed
                prefix = "in_" if self._section == "inputs" else "out_"
                self._current_snapshot[prefix + k] = v
            return

        # block ends implicitly when a new block starts; we flush there
        # otherwise ignore

    def _flush_block(self, final: bool):
        if not self._in_plc_block:
            return

        # choose time: Sim time if close/available, else VSI time
        t_s: Optional[float] = None
        if self._pending_scan_sim_time_s is not None:
            # if we recently saw a scan sim time, use it
            t_s = self._pending_scan_sim_time_s
        elif self._last_sim_time_s is not None:
            t_s = self._last_sim_time_s
        elif self._current_vsi_ns is not None:
            t_s = self._current_vsi_ns / 1e9

        if t_s is None:
            self.db.warn("PLC block without timestamp (ignored).")
            self._in_plc_block = False
            return

        snap = dict(self._current_snapshot)

        # normalize PLC extras into signals
        if self._current_finished is not None:
            snap["finished_products"] = self._current_finished

        if self._current_buffers:
            for k, v in self._current_buffers.items():
                snap[f"buffer_{k}"] = v

        if self._current_done_latches:
            for k, v in self._current_done_latches.items():
                snap[f"done_latched_{k}"] = v

        if self._current_start_sent:
            for k, v in self._current_start_sent.items():
                snap[f"start_sent_{k}"] = v

        if self._current_state is not None:
            snap["plc_state"] = self._current_state
            self.db.plc.add_event("plc_state", t_s, self._current_state)

        self.db.plc.append_snapshot(t_s, snap)

        # reset scan anchor after consuming
        self._pending_scan_sim_time_s = None

        # end current block
        self._in_plc_block = False
        self._section = None


class StationLogParser:
    """
    Streaming parser for station log blocks:
      +=STx_...+=
        VSI time: ...
        Inputs: key=val
        Outputs: key=val (dynamic KPIs)
        Internal: total_completed=1
        SimState: start_latched=False
        SimPy env.now = 27.500s  (optional better time)
    """

    def __init__(self, db: TimeSeriesDB, station_id: str):
        self.db = db
        self.station_id = station_id  # "S1".."S6"

        self._in_block = False
        self._section: Optional[str] = None  # "inputs"|"outputs"|None

        self._current_vsi_ns: Optional[int] = None
        self._current_sim_s: Optional[float] = None
        self._snapshot: Dict[str, Any] = {}

    def parse_file(self, path: str):
        try:
            with open(path, "r", errors="replace") as f:
                for line_no, line in enumerate(f, start=1):
                    self._parse_line(line.rstrip("\n"), line_no, path)
            self._flush_block(final=True)
        except Exception as e:
            self.db.warn(f"{self.station_id} parse failed for {path}: {e}")

    def _parse_line(self, line: str, line_no: int, path: str):
        # block start
        m = ST_BLOCK_START_RE.match(line)
        if m:
            # if this block is for THIS station number, start; else ignore
            n = int(m.group("n"))
            if self.station_id == f"S{n}":
                self._flush_block(final=False)
                self._in_block = True
                self._section = None
                self._current_vsi_ns = None
                self._current_sim_s = None
                self._snapshot = {}
            else:
                # entering another station block; close ours if open
                self._flush_block(final=False)
                self._in_block = False
            return

        if not self._in_block:
            # but we can still pick SimPy env.now lines even outside block (rare); ignore
            return

        # VSI time
        m = VSI_TIME_RE.match(line)
        if m:
            self._current_vsi_ns = int(m.group("ns"))
            return

        # section switches
        if line.strip() == "Inputs:":
            self._section = "inputs"
            return
        if line.strip() == "Outputs:":
            self._section = "outputs"
            return

        # SimPy env.now
        m = ST_SIMPY_RE.match(line)
        if m:
            self._current_sim_s = float(m.group("s"))
            return

        # internal info
        m = ST_INTERNAL_RE.match(line)
        if m:
            # may contain multiple kv separated by commas
            # example: "total_completed=1"
            parts = m.group("rest").split(",")
            for p in parts:
                if "=" in p:
                    k, v = p.split("=", 1)
                    self._snapshot[f"internal_{k.strip()}"] = parse_bool_int_float(v.strip())
            return

        # simstate info
        m = ST_SIMSTATE_RE.match(line)
        if m:
            # example: "start_latched=False"
            parts = m.group("rest").split(",")
            for p in parts:
                if "=" in p:
                    k, v = p.split("=", 1)
                    self._snapshot[f"simstate_{k.strip()}"] = parse_bool_int_float(v.strip())
            return

        # key=val lines inside Inputs/Outputs
        if self._section in ("inputs", "outputs"):
            m = PLC_SIGNAL_RE.match(line)
            if m:
                k = m.group("key").strip()
                v = parse_bool_int_float(m.group("val"))
                prefix = "in_" if self._section == "inputs" else "out_"
                self._snapshot[prefix + k] = v
            return

        # ignore others

    def _flush_block(self, final: bool):
        if not self._in_block:
            return

        # choose time: station SimPy env.now is best, else VSI time
        t_s: Optional[float] = None
        if self._current_sim_s is not None:
            t_s = self._current_sim_s
        elif self._current_vsi_ns is not None:
            t_s = self._current_vsi_ns / 1e9

        if t_s is None:
            self.db.warn(f"{self.station_id} block without timestamp (ignored).")
            self._in_block = False
            return

        self.db.get_station(self.station_id).append_snapshot(t_s, dict(self._snapshot))

        self._in_block = False
        self._section = None


# ============================================================
# KPI computation helpers
# ============================================================

def compute_throughput_products_per_min(t: List[float], finished: List[Any]) -> float:
    """
    Simple throughput = delta finished / delta time (min) over the whole run.
    """
    if not t or not finished:
        return 0.0
    # find first/last valid
    first_i = None
    last_i = None
    for i in range(len(t)):
        if finished[i] is not None:
            first_i = i
            break
    for i in range(len(t) - 1, -1, -1):
        if finished[i] is not None:
            last_i = i
            break
    if first_i is None or last_i is None or last_i <= first_i:
        return 0.0

    dt = t[last_i] - t[first_i]
    if dt <= 0:
        return 0.0
    df = float(finished[last_i]) - float(finished[first_i])
    return (df / dt) * 60.0


def compute_state_durations(events: List[Tuple[float, Any]]) -> Dict[str, float]:
    """
    events: [(t, state), ...] sorted by time
    returns total duration per state (seconds), using next-event time.
    """
    if len(events) < 2:
        return {}
    out: Dict[str, float] = {}
    for (t0, s0), (t1, _) in zip(events[:-1], events[1:]):
        if t1 > t0:
            out[str(s0)] = out.get(str(s0), 0.0) + (t1 - t0)
    return out


# ============================================================
# Plot widgets
# ============================================================

class MplChart(QWidget):
    def __init__(self, title: str = ""):
        super().__init__()
        self.fig = Figure(figsize=(5, 3), dpi=100)
        self.canvas = FigureCanvas(self.fig)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_title(title)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.canvas)

    def clear(self, title: Optional[str] = None):
        self.ax.clear()
        if title is not None:
            self.ax.set_title(title)

    def plot_line(self, x: List[float], y: List[Any], label: str):
        # filter None
        xs, ys = [], []
        for a, b in zip(x, y):
            if b is None:
                continue
            xs.append(a)
            ys.append(b)
        if xs:
            self.ax.plot(xs, ys, label=label)

    def finalize(self, xlabel: str = "time (s)", ylabel: str = "", legend: bool = True):
        self.ax.set_xlabel(xlabel)
        if ylabel:
            self.ax.set_ylabel(ylabel)
        if legend:
            self.ax.legend(loc="best")
        self.ax.grid(True, alpha=0.3)
        self.canvas.draw_idle()


# ============================================================
# Dashboard UI
# ============================================================

class KPIValueCard(QGroupBox):
    def __init__(self, title: str, value: str = "-"):
        super().__init__(title)
        self.label = QLabel(value)
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setStyleSheet("font-size: 20px; font-weight: 600;")
        lay = QVBoxLayout(self)
        lay.addWidget(self.label)

    def set_value(self, v: str):
        self.label.setText(v)


class DashboardWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VSI KPI Dashboard (PLC + 6 Stations)")
        self.resize(1400, 820)

        self.db = TimeSeriesDB()

        # root layout
        root = QWidget()
        self.setCentralWidget(root)
        root_lay = QVBoxLayout(root)

        # top controls
        top = QHBoxLayout()
        root_lay.addLayout(top)

        self.folder_edit = QLineEdit(DEFAULT_LOG_FOLDER)
        self.folder_edit.setPlaceholderText("log folder path...")
        top.addWidget(QLabel("Folder:"))
        top.addWidget(self.folder_edit, 1)

        self.recursive_chk = QCheckBox("Recursive")
        self.recursive_chk.setChecked(True)
        top.addWidget(self.recursive_chk)

        self.btn_browse = QPushButton("Browse")
        self.btn_refresh = QPushButton("Refresh/Parse")
        self.btn_export = QPushButton("Export Data")
        top.addWidget(self.btn_browse)
        top.addWidget(self.btn_refresh)
        top.addWidget(self.btn_export)

        top.addWidget(QLabel("Export:"))
        self.export_format = QComboBox()
        self.export_format.addItems(["CSV (per series)", "JSON (full db)"])
        top.addWidget(self.export_format)

        # splitter
        splitter = QSplitter(Qt.Horizontal)
        root_lay.addWidget(splitter, 1)

        # left list
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(8, 8, 8, 8)

        self.entity_list = QListWidget()
        left_lay.addWidget(QLabel("Views"))
        left_lay.addWidget(self.entity_list, 1)

        self.last_parsed_label = QLabel("Last parsed: -")
        left_lay.addWidget(self.last_parsed_label)

        splitter.addWidget(left)

        # right main
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(8, 8, 8, 8)

        # KPI cards grid
        self.cards_box = QGroupBox("Key KPIs")
        cards_lay = QGridLayout(self.cards_box)

        self.card_finished = KPIValueCard("Finished Products", "-")
        self.card_throughput = KPIValueCard("Throughput (prod/min)", "-")
        self.card_availability = KPIValueCard("S6 Availability (%)", "-")
        self.card_quality = KPIValueCard("Quality (S5 accept/reject)", "-")

        cards_lay.addWidget(self.card_finished, 0, 0)
        cards_lay.addWidget(self.card_throughput, 0, 1)
        cards_lay.addWidget(self.card_availability, 0, 2)
        cards_lay.addWidget(self.card_quality, 0, 3)

        right_lay.addWidget(self.cards_box)

        # charts
        self.chart1 = MplChart("Throughput / Finished products")
        self.chart2 = MplChart("Buffers")
        self.chart3 = MplChart("Cycle times")
        self.chart4 = MplChart("Quality / Station specific")

        right_lay.addWidget(self.chart1, 1)
        right_lay.addWidget(self.chart2, 1)
        right_lay.addWidget(self.chart3, 1)
        right_lay.addWidget(self.chart4, 1)

        # bottom warning panel
        self.warn_panel = QPlainTextEdit()
        self.warn_panel.setReadOnly(True)
        self.warn_panel.setMaximumBlockCount(2000)
        self.warn_panel.setPlaceholderText("warnings / parse notes ...")
        right_lay.addWidget(QLabel("Warnings / Notes"))
        right_lay.addWidget(self.warn_panel, 0)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([240, 1160])

        # signals
        self.btn_browse.clicked.connect(self.on_browse)
        self.btn_refresh.clicked.connect(self.on_refresh)
        self.btn_export.clicked.connect(self.on_export)
        self.entity_list.currentItemChanged.connect(self.on_entity_changed)

        # init list
        self._rebuild_entity_list()

        # optional auto refresh timer (off by default)
        self.timer = QTimer(self)
        self.timer.setInterval(0)
        # self.timer.timeout.connect(self.on_refresh)  # enable if you want

    # -------------------------
    # UI handlers
    # -------------------------

    def on_browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Select log folder", self.folder_edit.text())
        if folder:
            self.folder_edit.setText(folder)

    def on_refresh(self):
        folder = self.folder_edit.text().strip()
        recursive = self.recursive_chk.isChecked()

        self.db = TimeSeriesDB()
        self.warn_panel.clear()

        logs = find_logs(folder, recursive=recursive)
        if not logs:
            QMessageBox.warning(self, "No logs", f"No check.*.log files found in:\n{folder}")
            return

        # store file mapping
        for k, p in logs.items():
            self.db.files[k] = p

        # parse PLC first (for line-level)
        if "PLC" in logs:
            plc_parser = PLCLogParser(self.db)
            plc_parser.parse_file(logs["PLC"])
        else:
            self.db.warn("PLC log not found (line-level KPIs will be limited).")

        # parse stations
        for i in range(1, 7):
            sid = f"S{i}"
            if sid in logs:
                sp = StationLogParser(self.db, sid)
                sp.parse_file(logs[sid])
            else:
                self.db.warn(f"{sid} log not found.")

        self._rebuild_entity_list()
        self._update_warnings()
        self._render_current_view()

    def on_entity_changed(self, cur: QListWidgetItem, prev: QListWidgetItem):
        self._render_current_view()

    def on_export(self):
        """
        Export function requested:
          - CSV: exports current entity series into one CSV (time + all signals)
          - JSON: exports full DB (plc + stations) as json (big file possible)
        """
        entity = self._current_entity_key()
        if not entity:
            return

        fmt = self.export_format.currentText()
        if "CSV" in fmt:
            path, _ = QFileDialog.getSaveFileName(self, "Save CSV", f"{entity}.csv", "CSV Files (*.csv)")
            if not path:
                return
            ok, msg = export_entity_to_csv(self.db, entity, path)
            if ok:
                QMessageBox.information(self, "Export", f"CSV exported:\n{path}")
            else:
                QMessageBox.warning(self, "Export failed", msg)
        else:
            path, _ = QFileDialog.getSaveFileName(self, "Save JSON", "kpi_db.json", "JSON Files (*.json)")
            if not path:
                return
            ok, msg = export_db_to_json(self.db, path)
            if ok:
                QMessageBox.information(self, "Export", f"JSON exported:\n{path}")
            else:
                QMessageBox.warning(self, "Export failed", msg)

    # -------------------------
    # render
    # -------------------------

    def _rebuild_entity_list(self):
        self.entity_list.blockSignals(True)
        self.entity_list.clear()

        def add(name: str, key: str):
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, key)
            self.entity_list.addItem(item)

        add("ALL", "ALL")
        add("PLC", "PLC")
        for i in range(1, 7):
            add(f"Station {i}", f"S{i}")

        # keep selection
        self.entity_list.setCurrentRow(0)
        self.entity_list.blockSignals(False)

    def _current_entity_key(self) -> Optional[str]:
        it = self.entity_list.currentItem()
        if not it:
            return None
        return it.data(Qt.UserRole)

    def _update_warnings(self):
        if self.db.warnings:
            self.warn_panel.setPlainText("\n".join(self.db.warnings))
        else:
            self.warn_panel.setPlainText("no warnings.")

        # last parsed timestamp
        last_ts = self.db.plc.last_t
        # include stations max too
        for s in self.db.stations.values():
            last_ts = max(last_ts, s.last_t)
        self.last_parsed_label.setText(f"Last parsed: {last_ts:.3f}s")

    def _render_current_view(self):
        key = self._current_entity_key()
        if not key:
            return

        if key == "ALL":
            self._render_all()
        elif key == "PLC":
            self._render_plc()
        else:
            self._render_station(key)

    def _render_all(self):
        self._render_kpi_cards_all()

        # chart1: finished products + throughput context
        self.chart1.clear("Finished products (PLC)")
        t = self.db.plc.t
        finished = self.db.plc.signals.get("finished_products", [])
        self.chart1.plot_line(t, finished, "finished_products")
        self.chart1.finalize(ylabel="count", legend=True)

        # chart2: buffers (PLC)
        self.chart2.clear("Buffers (PLC)")
        # common buffers keys:
        for b in ["buffer_S1->S2", "buffer_S2->S3", "buffer_S3->S4", "buffer_S4->S5", "buffer_S5->S6"]:
            if b in self.db.plc.signals:
                self.chart2.plot_line(self.db.plc.t, self.db.plc.signals[b], b.replace("buffer_", ""))
        self.chart2.finalize(ylabel="items", legend=True)

        # chart3: cycle times per station (prefer PLC inputs if present, else station outputs)
        self.chart3.clear("Cycle time (ms) per station")
        for i in range(1, 7):
            # try PLC input first: in_S1_cycle_time_ms
            k_plc = f"in_S{i}_cycle_time_ms"
            if k_plc in self.db.plc.signals:
                self.chart3.plot_line(self.db.plc.t, self.db.plc.signals[k_plc], f"S{i} (plc)")
            else:
                st = self.db.stations.get(f"S{i}")
                if st and "out_cycle_time_ms" in st.signals:
                    self.chart3.plot_line(st.t, st.signals["out_cycle_time_ms"], f"S{i}")
        self.chart3.finalize(ylabel="ms", legend=True)

        # chart4: quality summary (S5 accept/reject, S2 scrap/rework, S6 repairs/availability)
        self.chart4.clear("Quality summary")
        # S5 accept/reject from PLC inputs if present
        if "in_S5_accept" in self.db.plc.signals:
            self.chart4.plot_line(self.db.plc.t, self.db.plc.signals["in_S5_accept"], "S5 accept (plc)")
        if "in_S5_reject" in self.db.plc.signals:
            self.chart4.plot_line(self.db.plc.t, self.db.plc.signals["in_S5_reject"], "S5 reject (plc)")

        if "in_S2_scrapped" in self.db.plc.signals:
            self.chart4.plot_line(self.db.plc.t, self.db.plc.signals["in_S2_scrapped"], "S2 scrapped (plc)")
        if "in_S2_reworks" in self.db.plc.signals:
            self.chart4.plot_line(self.db.plc.t, self.db.plc.signals["in_S2_reworks"], "S2 reworks (plc)")

        if "in_S6_total_repairs" in self.db.plc.signals:
            self.chart4.plot_line(self.db.plc.t, self.db.plc.signals["in_S6_total_repairs"], "S6 repairs (plc)")
        if "in_S6_availability" in self.db.plc.signals:
            self.chart4.plot_line(self.db.plc.t, self.db.plc.signals["in_S6_availability"], "S6 availability (plc)")

        self.chart4.finalize(ylabel="value", legend=True)

    def _render_plc(self):
        self._render_kpi_cards_all()  # still relevant

        # chart1: finished + state events count
        self.chart1.clear("PLC: finished products")
        t = self.db.plc.t
        finished = self.db.plc.signals.get("finished_products", [])
        self.chart1.plot_line(t, finished, "finished_products")
        self.chart1.finalize(ylabel="count", legend=True)

        # chart2: buffers
        self.chart2.clear("PLC: buffers")
        for k in sorted(self.db.plc.signals.keys()):
            if k.startswith("buffer_"):
                self.chart2.plot_line(self.db.plc.t, self.db.plc.signals[k], k.replace("buffer_", ""))
        self.chart2.finalize(ylabel="items", legend=True)

        # chart3: start_sent signals
        self.chart3.clear("PLC: start_sent flags")
        for i in range(1, 7):
            k = f"start_sent_S{i}"
            if k in self.db.plc.signals:
                self.chart3.plot_line(self.db.plc.t, self.db.plc.signals[k], k.replace("start_sent_", ""))
        self.chart3.finalize(ylabel="flag", legend=True)

        # chart4: done_latched signals
        self.chart4.clear("PLC: done_latched flags")
        for i in range(1, 7):
            k = f"done_latched_S{i}"
            if k in self.db.plc.signals:
                self.chart4.plot_line(self.db.plc.t, self.db.plc.signals[k], k.replace("done_latched_", ""))
        self.chart4.finalize(ylabel="flag", legend=True)

    def _render_station(self, sid: str):
        st = self.db.stations.get(sid)
        if not st:
            self.db.warn(f"No parsed data for {sid}.")
            self._update_warnings()
            return

        self._render_kpi_cards_station(sid, st)

        # chart1: status flags (ready/busy/done/fault)
        self.chart1.clear(f"{sid}: status flags")
        for key in ["out_ready", "out_busy", "out_done", "out_fault"]:
            if key in st.signals:
                self.chart1.plot_line(st.t, st.signals[key], key.replace("out_", ""))
        self.chart1.finalize(ylabel="flag", legend=True)

        # chart2: cycle time
        self.chart2.clear(f"{sid}: cycle time (ms)")
        if "out_cycle_time_ms" in st.signals:
            self.chart2.plot_line(st.t, st.signals["out_cycle_time_ms"], "cycle_time_ms")
        self.chart2.finalize(ylabel="ms", legend=True)

        # chart3: station-specific KPIs (dynamic - pick common known ones if present)
        self.chart3.clear(f"{sid}: station KPIs")
        preferred = []
        if sid == "S1":
            preferred = ["out_inventory_ok", "out_any_arm_failed", "internal_total_completed"]
        elif sid == "S2":
            preferred = ["out_completed", "out_scrapped", "out_reworks", "out_cycle_time_avg_s"]
        elif sid == "S3":
            preferred = ["out_strain_relief_ok", "out_continuity_ok"]
        elif sid == "S4":
            preferred = ["out_total", "out_completed"]
        elif sid == "S5":
            preferred = ["out_accept", "out_reject", "out_last_accept"]
        elif sid == "S6":
            preferred = ["out_packages_completed", "out_arm_cycles", "out_total_repairs",
                         "out_operational_time_s", "out_downtime_s", "out_availability"]

        for k in preferred:
            if k in st.signals:
                self.chart3.plot_line(st.t, st.signals[k], k.replace("out_", "").replace("internal_", ""))
        self.chart3.finalize(ylabel="value", legend=True)

        # chart4: show any other numeric output signals (best-effort)
        self.chart4.clear(f"{sid}: other outputs (best-effort)")
        extras = []
        for k in st.signals.keys():
            if k.startswith("out_") and k not in preferred and k not in ("out_ready", "out_busy", "out_done", "out_fault", "out_cycle_time_ms"):
                extras.append(k)
        # limit to avoid unreadable plot
        for k in extras[:6]:
            self.chart4.plot_line(st.t, st.signals[k], k.replace("out_", ""))
        self.chart4.finalize(ylabel="value", legend=True)

    def _render_kpi_cards_all(self):
        # finished products (PLC)
        finished_series = self.db.plc.signals.get("finished_products", [])
        finished_final = "-"
        if finished_series:
            for v in reversed(finished_series):
                if v is not None:
                    finished_final = str(v)
                    break
        self.card_finished.set_value(finished_final)

        # throughput
        tp = compute_throughput_products_per_min(self.db.plc.t, finished_series)
        self.card_throughput.set_value(f"{tp:.2f}")

        # availability (S6)
        av = "-"
        s6_av = self.db.plc.signals.get("in_S6_availability", [])
        if s6_av:
            for v in reversed(s6_av):
                if v is not None:
                    av = f"{float(v):.2f}"
                    break
        else:
            st6 = self.db.stations.get("S6")
            if st6 and "out_availability" in st6.signals:
                for v in reversed(st6.signals["out_availability"]):
                    if v is not None:
                        av = f"{float(v):.2f}"
                        break
        self.card_availability.set_value(av)

        # quality (S5)
        acc = "-"
        rej = "-"
        s5_acc = self.db.plc.signals.get("in_S5_accept", [])
        s5_rej = self.db.plc.signals.get("in_S5_reject", [])
        if s5_acc:
            for v in reversed(s5_acc):
                if v is not None:
                    acc = str(v)
                    break
        if s5_rej:
            for v in reversed(s5_rej):
                if v is not None:
                    rej = str(v)
                    break
        self.card_quality.set_value(f"{acc}/{rej}")

    def _render_kpi_cards_station(self, sid: str, st: EntitySeries):
        # For station view, keep line-level cards but make them relevant if possible.
        self._render_kpi_cards_all()


# ============================================================
# Export functions (requested)
# ============================================================

def export_entity_to_csv(db: TimeSeriesDB, entity_key: str, out_path: str) -> Tuple[bool, str]:
    """
    Export one entity (PLC or S1..S6) into a single CSV:
      time_s, <signal1>, <signal2>, ...
    """
    try:
        if entity_key == "PLC":
            ent = db.plc
        elif entity_key.startswith("S"):
            ent = db.stations.get(entity_key)
            if ent is None:
                return False, f"No entity data for {entity_key}"
        elif entity_key == "ALL":
            return False, "CSV export supports PLC or single station only. Use JSON for ALL."
        else:
            return False, f"Unknown entity {entity_key}"

        keys = sorted(ent.signals.keys())
        with open(out_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["time_s"] + keys)
            n = len(ent.t)
            for i in range(n):
                row = [ent.t[i]]
                for k in keys:
                    arr = ent.signals.get(k, [])
                    row.append(arr[i] if i < len(arr) else None)
                w.writerow(row)
        return True, "ok"
    except Exception as e:
        return False, str(e)


def export_db_to_json(db: TimeSeriesDB, out_path: str) -> Tuple[bool, str]:
    """
    Export full DB to JSON (can be large).
    """
    try:
        def pack(ent: EntitySeries) -> Dict[str, Any]:
            return {
                "name": ent.name,
                "t": ent.t,
                "signals": ent.signals,
                "events": ent.events,
                "last_t": ent.last_t
            }

        data = {
            "files": db.files,
            "warnings": db.warnings,
            "plc": pack(db.plc),
            "stations": {k: pack(v) for k, v in db.stations.items()},
        }

        with open(out_path, "w") as f:
            json.dump(data, f, indent=2)
        return True, "ok"
    except Exception as e:
        return False, str(e)


# ============================================================
# Main
# ============================================================

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", default=DEFAULT_LOG_FOLDER, help="Folder containing check.*.log files")
    ap.add_argument("--no-recursive", action="store_true", help="Disable recursive search")
    args = ap.parse_args()

    app = QApplication(sys.argv)
    w = DashboardWindow()
    w.folder_edit.setText(args.folder)
    w.recursive_chk.setChecked(not args.no_recursive)
    w.show()

    # auto parse at startup
    w.on_refresh()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
