#!/usr/bin/env python3
"""
NEEED IMPROVEMENT IN THE GRAPGH AND SOME small issues
"""

from __future__ import annotations

import os
import re
import sys
import math
import traceback
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Any

# ----------------------------
# Qt imports
# ----------------------------
try:
    from PySide6.QtCore import Qt, QThread, Signal
    from PySide6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
        QListWidget, QListWidgetItem, QLabel, QPushButton, QFileDialog,
        QLineEdit, QSplitter, QFormLayout, QGroupBox, QTextEdit, QSizePolicy
    )
except Exception as e:
    print("ERROR: PySide6 is required.")
    print(e)
    sys.exit(1)

# ----------------------------
# Matplotlib embedding
# ----------------------------
import matplotlib
matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure


# ============================================================
# Data Model
# ============================================================

TimePoint = Tuple[float, Any]  # (time_seconds, value)


@dataclass
class EntitySeries:
    name: str
    fields: Dict[str, List[TimePoint]] = field(default_factory=dict)

    def add(self, t: float, key: str, value: Any):
        if t is None or key is None:
            return
        self.fields.setdefault(key, []).append((t, value))

    def latest_value(self, key: str) -> Optional[Any]:
        pts = self.fields.get(key)
        if not pts:
            return None
        return pts[-1][1]

    def latest_time(self) -> Optional[float]:
        best = None
        for pts in self.fields.values():
            if pts:
                best = pts[-1][0] if best is None else max(best, pts[-1][0])
        return best


@dataclass
class PlcData(EntitySeries):
    state_timeline: List[Tuple[float, str]] = field(default_factory=list)
    buffers: Dict[str, List[TimePoint]] = field(default_factory=dict)
    start_sent: Dict[str, List[TimePoint]] = field(default_factory=dict)
    done_latches: Dict[str, List[TimePoint]] = field(default_factory=dict)
    rx_packets: Dict[str, int] = field(default_factory=dict)  # station -> count

    def add_state(self, t: float, state: str):
        if t is None or not state:
            return
        # avoid repeating same state at same time
        if self.state_timeline and self.state_timeline[-1][1] == state:
            return
        self.state_timeline.append((t, state))

    def add_buffer(self, t: float, link: str, v: int):
        self.buffers.setdefault(link, []).append((t, v))

    def add_bool_map(self, dst: Dict[str, List[TimePoint]], t: float, key: str, v: bool):
        dst.setdefault(key, []).append((t, v))


@dataclass
class ParsedData:
    plc: PlcData = field(default_factory=lambda: PlcData(name="PLC"))
    stations: Dict[str, EntitySeries] = field(default_factory=dict)  # "ST1".."ST6"
    warnings: List[str] = field(default_factory=list)

    def station(self, st: str) -> EntitySeries:
        if st not in self.stations:
            self.stations[st] = EntitySeries(name=st)
        return self.stations[st]


# ============================================================
# Helpers
# ============================================================

def safe_float(s: str) -> Optional[float]:
    try:
        return float(s)
    except Exception:
        return None

def parse_value(raw: str) -> Any:
    r = raw.strip()
    if r in ("True", "False"):
        return r == "True"
    # common VSI logs use 0/1 for booleans sometimes
    if re.fullmatch(r"[+-]?\d+", r):
        # int
        try:
            return int(r)
        except Exception:
            return r
    if re.fullmatch(r"[+-]?\d+(\.\d+)?([eE][+-]?\d+)?", r):
        # float
        try:
            f = float(r)
            # if it's basically an int, keep float anyway (chart-friendly)
            return f
        except Exception:
            return r
    return r

def ns_to_s(ns: int) -> float:
    return ns / 1e9

def decimate(points: List[TimePoint], max_points: int = 4000) -> List[TimePoint]:
    """Simple stride decimation for plotting."""
    n = len(points)
    if n <= max_points:
        return points
    step = max(1, n // max_points)
    return points[::step]

def station_label_to_key(label: str) -> str:
    # label: "Station 1" -> "ST1"
    m = re.search(r"(\d+)", label)
    if not m:
        return label
    return f"ST{int(m.group(1))}"


# ============================================================
# Log Parser (streaming, state-machine)
# ============================================================

class VsiLogParser:
    RE_BLOCK_START = re.compile(r"^\+=([A-Za-z0-9_]+)\+=\s*$")
    RE_VSI_TIME = re.compile(r"^\s*VSI time:\s*([0-9]+)\s*ns\s*$")
    RE_SIM_TIME = re.compile(r"^\s*Sim time:\s*([0-9.]+)s\s*$")
    RE_SIMPY_NOW = re.compile(r"^\s*SimPy env\.now\s*=\s*([0-9.]+)s\s*$")

    RE_KV = re.compile(r"^\s*([A-Za-z0-9_]+)\s*=\s*(.+?)\s*$")

    RE_PLC_STATE_LINE = re.compile(r"^\s*PLC State:\s*(.+)\s*$")
    RE_PLC_SCAN_STATE = re.compile(r"^\s*PLC state=([A-Za-z0-9_]+)\b.*$")

    RE_DONE_LATCHES = re.compile(r"^\s*Done latches:\s*(.+)\s*$")
    RE_START_SENT = re.compile(r"^\s*Start sent:\s*(.+)\s*$")
    RE_BUFFERS = re.compile(r"^\s*Buffers:\s*(.+)\s*$")
    RE_FINISHED = re.compile(r"^\s*Finished products:\s*([0-9]+)\s*$")

    RE_RX_FROM = re.compile(r"^\s*Received packet from\s+(ST[0-9]+)_(.+)\s*$")
    RE_RX_META = re.compile(r"^\s*PLC RX meta dest/src/len:\s*([0-9]+),\s*([0-9]+),\s*([0-9]+)\s*$")

    def __init__(self, max_warnings: int = 300):
        self.max_warnings = max_warnings

    def warn(self, data: ParsedData, msg: str):
        if len(data.warnings) < self.max_warnings:
            data.warnings.append(msg)

    def detect_logs(self, root: str) -> Dict[str, str]:
        """
        Returns mapping:
          "PLC" -> path
          "ST1".."ST6" -> path
        """
        found: Dict[str, str] = {}
        if os.path.isfile(root) and root.endswith(".log"):
            # single file
            base = os.path.basename(root)
            # check.PLC_LineCoordinator.log / check.ST1_....log
            if "check.PLC_" in base or "PLC_LineCoordinator" in base:
                found["PLC"] = root
            m = re.search(r"check\.(ST[1-6])_", base)
            if m:
                found[m.group(1)] = root
            return found

        # folder: search recursively
        for dirpath, _, filenames in os.walk(root):
            for fn in filenames:
                if not fn.startswith("check.") or not fn.endswith(".log"):
                    continue
                full = os.path.join(dirpath, fn)
                if "PLC_LineCoordinator" in fn:
                    found["PLC"] = full
                    continue
                m = re.search(r"check\.(ST[1-6])_", fn)
                if m:
                    found[m.group(1)] = full
        return found

    def parse_files(self, root_or_files: str, explicit: Optional[Dict[str, str]] = None) -> ParsedData:
        data = ParsedData()
        files = explicit if explicit else self.detect_logs(root_or_files)

        # ensure station keys exist
        for i in range(1, 7):
            data.station(f"ST{i}")

        # parse PLC first (line-level KPIs)
        plc_path = files.get("PLC")
        if plc_path and os.path.isfile(plc_path):
            self._parse_one(plc_path, data, entity_hint="PLC")
        else:
            self.warn(data, f"PLC log not found. expected check.PLC_LineCoordinator.log under: {root_or_files}")

        # parse stations
        for i in range(1, 7):
            st = f"ST{i}"
            p = files.get(st)
            if p and os.path.isfile(p):
                self._parse_one(p, data, entity_hint=st)
            else:
                self.warn(data, f"{st} log not found under: {root_or_files}")

        return data

    def _parse_one(self, path: str, data: ParsedData, entity_hint: str):
        """
        Streaming parse.
        entity_hint: "PLC" or "ST1"... given by file name detection.
        """
        current_block: Optional[str] = None  # "PLC_LineCoordinator" or "ST1_ComponentKitting"
        mode: Optional[str] = None  # "Inputs" | "Outputs" | None

        last_vsi_s: Optional[float] = None
        last_sim_s: Optional[float] = None  # from PLC scan or station SimPy env.now
        last_time: Optional[float] = None   # chosen timestamp

        # for PLC scan: we can see "PLC state=..." + "Sim time:..."
        pending_scan_state: Optional[str] = None

        def choose_time() -> Optional[float]:
            # prefer sim time if present
            return last_sim_s if last_sim_s is not None else last_vsi_s

        with open(path, "r", errors="replace") as f:
            for line_no, line in enumerate(f, 1):
                line = line.rstrip("\n")

                # block start
                m = self.RE_BLOCK_START.match(line.strip())
                if m:
                    current_block = m.group(1)  # PLC_LineCoordinator / ST1_ComponentKitting
                    mode = None
                    last_vsi_s = None
                    # do not reset last_sim_s globally (scan sections exist outside blocks)
                    last_time = choose_time()
                    continue

                # VSI time inside block
                m = self.RE_VSI_TIME.match(line)
                if m:
                    ns = int(m.group(1))
                    last_vsi_s = ns_to_s(ns)
                    last_time = choose_time()
                    continue

                # PLC scan state line
                m = self.RE_PLC_SCAN_STATE.match(line)
                if m:
                    pending_scan_state = m.group(1)
                    continue

                # Sim time line (PLC scan)
                m = self.RE_SIM_TIME.match(line)
                if m:
                    last_sim_s = float(m.group(1))
                    last_time = choose_time()
                    # commit pending scan state at this sim time
                    if pending_scan_state:
                        data.plc.add_state(last_time, pending_scan_state)
                        pending_scan_state = None
                    continue

                # station SimPy env.now
                m = self.RE_SIMPY_NOW.match(line)
                if m:
                    last_sim_s = float(m.group(1))
                    last_time = choose_time()
                    continue

                # mode switches
                if line.strip() == "Inputs:":
                    mode = "Inputs"
                    continue
                if line.strip() == "Outputs:":
                    mode = "Outputs"
                    continue

                # RX counts
                m = self.RE_RX_FROM.match(line)
                if m:
                    st_key = m.group(1)  # "ST1"
                    data.plc.rx_packets[st_key] = data.plc.rx_packets.get(st_key, 0) + 1
                    continue
                # ignore RX meta details for now (available if needed)

                # PLC single-line fields (often inside PLC block, but can appear in scan area too)
                m = self.RE_PLC_STATE_LINE.match(line)
                if m:
                    t = choose_time()
                    state = m.group(1).strip()
                    if t is not None:
                        data.plc.add_state(t, state)
                    continue

                m = self.RE_FINISHED.match(line)
                if m:
                    t = choose_time()
                    if t is not None:
                        data.plc.add(t, "finished_products", int(m.group(1)))
                    continue

                m = self.RE_BUFFERS.match(line)
                if m:
                    t = choose_time()
                    if t is None:
                        continue
                    blob = m.group(1)
                    # "S1->S2=0, S2->S3=1, ..."
                    for part in blob.split(","):
                        part = part.strip()
                        if not part:
                            continue
                        if "=" not in part:
                            continue
                        k, v = part.split("=", 1)
                        k = k.strip()
                        v = v.strip()
                        try:
                            iv = int(float(v))
                        except Exception:
                            continue
                        data.plc.add_buffer(t, k, iv)
                    continue

                m = self.RE_START_SENT.match(line)
                if m:
                    t = choose_time()
                    if t is None:
                        continue
                    blob = m.group(1)
                    for part in blob.split(","):
                        part = part.strip()
                        if not part or "=" not in part:
                            continue
                        k, v = part.split("=", 1)
                        k = k.strip()  # "S1"
                        vv = v.strip()
                        if vv in ("True", "False"):
                            data.plc.add_bool_map(data.plc.start_sent, t, k, vv == "True")
                    continue

                m = self.RE_DONE_LATCHES.match(line)
                if m:
                    t = choose_time()
                    if t is None:
                        continue
                    blob = m.group(1)
                    for part in blob.split(","):
                        part = part.strip()
                        if not part or "=" not in part:
                            continue
                        k, v = part.split("=", 1)
                        k = k.strip()  # "S1"
                        vv = v.strip()
                        if vv in ("True", "False"):
                            data.plc.add_bool_map(data.plc.done_latches, t, k, vv == "True")
                    continue

                # key=value inside Inputs/Outputs of a block
                if current_block and mode in ("Inputs", "Outputs"):
                    m = self.RE_KV.match(line)
                    if m:
                        key = m.group(1).strip()
                        val = parse_value(m.group(2))
                        t = choose_time()
                        if t is None:
                            continue

                        # route:
                        if entity_hint == "PLC":
                            # PLC inputs contain S1_ready, S2_cycle_time_ms, etc.
                            data.plc.add(t, key, val)
                        else:
                            # Station block outputs/inputs (ready/busy/done + station-specific)
                            data.station(entity_hint).add(t, key, val)
                        continue

                # Station internal counters like: "Internal: total_completed=1"
                if entity_hint.startswith("ST") and "Internal:" in line:
                    t = choose_time()
                    if t is None:
                        continue
                    # parse any a=b occurrences in this line
                    for m2 in re.finditer(r"([A-Za-z0-9_]+)\s*=\s*([A-Za-z0-9_.+-]+)", line):
                        k = m2.group(1).strip()
                        v = parse_value(m2.group(2))
                        data.station(entity_hint).add(t, k, v)

        # done file
        return


# ============================================================
# KPI Computation
# ============================================================

@dataclass
class KpiSummary:
    last_time: Optional[float] = None

    finished_products_final: Optional[int] = None
    throughput_per_min: Optional[float] = None

    plc_state_durations: Dict[str, float] = field(default_factory=dict)

    # quality / output summary
    st2_scrapped: Optional[int] = None
    st2_reworks: Optional[int] = None
    st5_accept: Optional[int] = None
    st5_reject: Optional[int] = None
    st6_repairs: Optional[int] = None
    st6_availability: Optional[float] = None


def compute_kpis(data: ParsedData) -> KpiSummary:
    k = KpiSummary()
    k.last_time = data.plc.latest_time()
    if k.last_time is None:
        # fallback to max station time
        for s in data.stations.values():
            t = s.latest_time()
            if t is not None:
                k.last_time = t if k.last_time is None else max(k.last_time, t)

    # finished products
    fp = data.plc.fields.get("finished_products", [])
    if fp:
        k.finished_products_final = int(fp[-1][1])
        t0, v0 = fp[0]
        t1, v1 = fp[-1]
        dt = max(1e-9, (t1 - t0))
        dv = float(v1) - float(v0)
        k.throughput_per_min = (dv / dt) * 60.0

    # PLC state durations from plc.state_timeline
    tl = data.plc.state_timeline
    if len(tl) >= 2:
        for i in range(len(tl) - 1):
            t_curr, st = tl[i]
            t_next, _ = tl[i + 1]
            dur = max(0.0, t_next - t_curr)
            k.plc_state_durations[st] = k.plc_state_durations.get(st, 0.0) + dur

    # station summaries (latest counters)
    st2 = data.stations.get("ST2")
    if st2:
        k.st2_scrapped = st2.latest_value("scrapped")
        k.st2_reworks = st2.latest_value("reworks")

    st5 = data.stations.get("ST5")
    if st5:
        k.st5_accept = st5.latest_value("accept")
        k.st5_reject = st5.latest_value("reject")

    st6 = data.stations.get("ST6")
    if st6:
        k.st6_repairs = st6.latest_value("total_repairs")
        k.st6_availability = st6.latest_value("availability")

    return k


# ============================================================
# Plot Widget
# ============================================================

class PlotCanvas(FigureCanvas):
    def __init__(self):
        self.fig = Figure()
        super().__init__(self.fig)
        self.ax = self.fig.add_subplot(111)
        self.fig.tight_layout()

    def clear(self):
        self.fig.clf()
        self.ax = self.fig.add_subplot(111)
        self.fig.tight_layout()

    def plot_series(self, series: List[TimePoint], title: str, xlabel: str = "time (s)", ylabel: str = ""):
        self.clear()
        if not series:
            self.ax.set_title(title + " (no data)")
            self.draw()
            return
        pts = decimate(series, 5000)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        self.ax.plot(xs, ys)
        self.ax.set_title(title)
        self.ax.set_xlabel(xlabel)
        if ylabel:
            self.ax.set_ylabel(ylabel)
        self.ax.grid(True, alpha=0.2)
        self.draw()

    def plot_multi(self, lines: Dict[str, List[TimePoint]], title: str, xlabel: str = "time (s)", ylabel: str = ""):
        self.clear()
        plotted = 0
        for name, series in lines.items():
            if not series:
                continue
            pts = decimate(series, 4000)
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            self.ax.plot(xs, ys, label=name)
            plotted += 1
        if plotted == 0:
            self.ax.set_title(title + " (no data)")
        else:
            self.ax.set_title(title)
            self.ax.legend(loc="best", fontsize=8)
        self.ax.set_xlabel(xlabel)
        if ylabel:
            self.ax.set_ylabel(ylabel)
        self.ax.grid(True, alpha=0.2)
        self.draw()


# ============================================================
# Worker Thread (keeps UI responsive)
# ============================================================

class ParseWorker(QThread):
    finished = Signal(object)  # ParsedData
    errored = Signal(str)

    def __init__(self, root: str):
        super().__init__()
        self.root = root

    def run(self):
        try:
            parser = VsiLogParser()
            data = parser.parse_files(self.root)
            self.finished.emit(data)
        except Exception:
            self.errored.emit(traceback.format_exc())


# ============================================================
# Dashboard UI
# ============================================================

class DashboardWindow(QMainWindow):
    def __init__(self, default_root: str):
        super().__init__()
        self.setWindowTitle("VSI KPI Dashboard (PLC + 6 Stations)")

        self.root = default_root
        self.data: Optional[ParsedData] = None
        self.kpis: Optional[KpiSummary] = None
        self.worker: Optional[ParseWorker] = None

        # top controls
        self.path_edit = QLineEdit(self.root)
        self.btn_browse = QPushButton("Browse")
        self.btn_refresh = QPushButton("Refresh")

        top = QWidget()
        top_l = QHBoxLayout(top)
        top_l.addWidget(QLabel("Logs folder:"))
        top_l.addWidget(self.path_edit, 1)
        top_l.addWidget(self.btn_browse)
        top_l.addWidget(self.btn_refresh)

        # left selector
        self.selector = QListWidget()
        self.selector.setMaximumWidth(200)
        for label in ["ALL", "PLC", "Station 1", "Station 2", "Station 3", "Station 4", "Station 5", "Station 6"]:
            self.selector.addItem(QListWidgetItem(label))
        self.selector.setCurrentRow(0)

        # KPI cards (form layout)
        self.kpi_group = QGroupBox("KPIs")
        self.kpi_form = QFormLayout(self.kpi_group)
        self.kpi_labels: Dict[str, QLabel] = {}

        def add_kpi_row(key: str, title: str):
            lbl = QLabel("-")
            lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.kpi_labels[key] = lbl
            self.kpi_form.addRow(QLabel(title), lbl)

        add_kpi_row("last_time", "Last timestamp (s)")
        add_kpi_row("finished", "Finished products")
        add_kpi_row("throughput", "Throughput (products/min)")
        add_kpi_row("st2_scrapped", "ST2 scrapped")
        add_kpi_row("st2_reworks", "ST2 reworks")
        add_kpi_row("st5_accept", "ST5 accept")
        add_kpi_row("st5_reject", "ST5 reject")
        add_kpi_row("st6_repairs", "ST6 repairs")
        add_kpi_row("st6_availability", "ST6 availability")

        # plots
        self.plot1 = PlotCanvas()
        self.plot2 = PlotCanvas()
        self.plot3 = PlotCanvas()
        for p in (self.plot1, self.plot2, self.plot3):
            p.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        plots_box = QWidget()
        plots_l = QVBoxLayout(plots_box)
        plots_l.addWidget(self.plot1, 1)
        plots_l.addWidget(self.plot2, 1)
        plots_l.addWidget(self.plot3, 1)

        # warnings panel
        self.warn_panel = QTextEdit()
        self.warn_panel.setReadOnly(True)
        self.warn_panel.setPlaceholderText("warnings / parse notes will appear here...")

        right = QWidget()
        right_l = QVBoxLayout(right)
        right_l.addWidget(self.kpi_group)
        right_l.addWidget(plots_box, 1)
        right_l.addWidget(QLabel("Warnings / Notes"))
        right_l.addWidget(self.warn_panel, 0)

        splitter = QSplitter()
        splitter.addWidget(self.selector)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        central = QWidget()
        c_l = QVBoxLayout(central)
        c_l.addWidget(top)
        c_l.addWidget(splitter, 1)
        self.setCentralWidget(central)

        # signals
        self.btn_browse.clicked.connect(self.on_browse)
        self.btn_refresh.clicked.connect(self.on_refresh)
        self.selector.currentRowChanged.connect(self.on_view_changed)

        # first load
        self.on_refresh()

    def on_browse(self):
        d = QFileDialog.getExistingDirectory(self, "Select logs folder", self.root)
        if d:
            self.path_edit.setText(d)
            self.on_refresh()

    def on_refresh(self):
        root = self.path_edit.text().strip()
        if not root:
            return
        self.root = root
        self.warn_panel.setPlainText("parsing logs...\n")
        self.btn_refresh.setEnabled(False)

        if self.worker and self.worker.isRunning():
            self.worker.terminate()

        self.worker = ParseWorker(self.root)
        self.worker.finished.connect(self.on_parsed)
        self.worker.errored.connect(self.on_error)
        self.worker.start()

    def on_error(self, err: str):
        self.btn_refresh.setEnabled(True)
        self.warn_panel.setPlainText(err)

    def on_parsed(self, data: ParsedData):
        self.btn_refresh.setEnabled(True)
        self.data = data
        self.kpis = compute_kpis(data)
        self.update_kpi_cards()
        self.update_warnings()
        self.update_plots_for_current_view()

    def on_view_changed(self, _idx: int):
        self.update_plots_for_current_view()

    def update_warnings(self):
        if not self.data:
            return
        txt = "\n".join(self.data.warnings) if self.data.warnings else "(no warnings)"
        self.warn_panel.setPlainText(txt)

    def update_kpi_cards(self):
        k = self.kpis
        if not k:
            return

        def setv(key: str, val: Any, fmt: str = "{}"):
            if val is None:
                self.kpi_labels[key].setText("-")
            else:
                try:
                    self.kpi_labels[key].setText(fmt.format(val))
                except Exception:
                    self.kpi_labels[key].setText(str(val))

        setv("last_time", k.last_time, "{:.3f}")
        setv("finished", k.finished_products_final, "{}")
        setv("throughput", k.throughput_per_min, "{:.3f}")
        setv("st2_scrapped", k.st2_scrapped, "{}")
        setv("st2_reworks", k.st2_reworks, "{}")
        setv("st5_accept", k.st5_accept, "{}")
        setv("st5_reject", k.st5_reject, "{}")
        setv("st6_repairs", k.st6_repairs, "{}")
        setv("st6_availability", k.st6_availability, "{:.2f}")

    def update_plots_for_current_view(self):
        if not self.data:
            # empty plots
            self.plot1.plot_series([], "No data")
            self.plot2.plot_series([], "No data")
            self.plot3.plot_series([], "No data")
            return

        label = self.selector.currentItem().text()

        if label == "ALL":
            self.render_all_view()
        elif label == "PLC":
            self.render_plc_view()
        else:
            st = station_label_to_key(label)
            self.render_station_view(st)

    # -------------------------
    # View renderers
    # -------------------------

    def render_all_view(self):
        d = self.data
        assert d

        # Plot1: finished products over time (prefer PLC sim time)
        fp = d.plc.fields.get("finished_products", [])
        self.plot1.plot_series(fp, "Finished products (line throughput)", ylabel="products")

        # Plot2: buffers (S1->S2 ... S5->S6)
        buf_lines = {}
        for link, pts in d.plc.buffers.items():
            buf_lines[link] = pts
        self.plot2.plot_multi(buf_lines, "Buffer levels", ylabel="count")

        # Plot3: cycle_time_ms for ST1..ST6 (from PLC inputs if present, else station outputs)
        ct_lines: Dict[str, List[TimePoint]] = {}

        # prefer PLC fields like S1_cycle_time_ms
        for i in range(1, 7):
            key = f"S{i}_cycle_time_ms"
            pts = d.plc.fields.get(key)
            if pts:
                ct_lines[f"S{i}"] = pts
            else:
                # fallback to station logs: cycle_time_ms
                st = d.stations.get(f"ST{i}")
                if st and st.fields.get("cycle_time_ms"):
                    ct_lines[f"S{i}"] = st.fields["cycle_time_ms"]

        self.plot3.plot_multi(ct_lines, "Cycle time trend (ms)", ylabel="ms")

    def render_plc_view(self):
        d = self.data
        assert d

        # Plot1: PLC state timeline as "state index" plot (simple)
        if d.plc.state_timeline:
            # map state to an integer
            states = []
            for _, st in d.plc.state_timeline:
                if st not in states:
                    states.append(st)
            series = [(t, states.index(st)) for (t, st) in d.plc.state_timeline]
            self.plot1.plot_series(series, "PLC state timeline (indexed)")
            self.plot1.ax.set_yticks(list(range(len(states))))
            self.plot1.ax.set_yticklabels(states, fontsize=7)
            self.plot1.draw()
        else:
            self.plot1.plot_series([], "PLC state timeline (no data)")

        # Plot2: finished products
        self.plot2.plot_series(d.plc.fields.get("finished_products", []), "Finished products", ylabel="products")

        # Plot3: start_sent signals for S1..S6 (if present)
        ss_lines = {}
        for i in range(1, 7):
            k = f"S{i}"
            pts = d.plc.start_sent.get(k)
            if pts:
                ss_lines[k] = [(t, 1 if v else 0) for (t, v) in pts]
        self.plot3.plot_multi(ss_lines, "Start sent (S1..S6)", ylabel="0/1")

    def render_station_view(self, st: str):
        d = self.data
        assert d
        s = d.stations.get(st)
        if not s:
            self.plot1.plot_series([], f"{st} (no data)")
            self.plot2.plot_series([], f"{st} (no data)")
            self.plot3.plot_series([], f"{st} (no data)")
            return

        # Plot1: cycle_time_ms
        self.plot1.plot_series(s.fields.get("cycle_time_ms", []), f"{st} cycle_time_ms", ylabel="ms")

        # Plot2: ready/busy/done/fault
        status_lines = {}
        for key in ("ready", "busy", "done", "fault"):
            pts = s.fields.get(key)
            if pts:
                # ensure numeric for plot
                status_lines[key] = [(t, 1 if bool(v) else 0) for (t, v) in pts]
        self.plot2.plot_multi(status_lines, f"{st} status signals", ylabel="0/1")

        # Plot3: station-specific counters (auto-pick numeric series other than status/cycle)
        blacklist = {"cmd_start", "cmd_stop", "cmd_reset", "batch_id", "recipe_id", "ready", "busy", "done", "fault", "cycle_time_ms"}
        candidates: Dict[str, List[TimePoint]] = {}
        for key, pts in s.fields.items():
            if key in blacklist:
                continue
            if not pts:
                continue
            # keep only numeric-ish
            v = pts[-1][1]
            if isinstance(v, (int, float, bool)):
                # normalize bool to 0/1
                if isinstance(v, bool):
                    candidates[key] = [(t, 1 if vv else 0) for (t, vv) in pts]
                else:
                    candidates[key] = pts

        # choose up to 4 to keep readable
        picked = dict(list(candidates.items())[:4])
        self.plot3.plot_multi(picked, f"{st} extra KPIs (auto)", ylabel="")


# ============================================================
# Entrypoint
# ============================================================

def main():
    # default folder from your VSI path (you can change at runtime)
    default_root = "/data/tools/pave/innexis_home/vsi_2025.2/all_stations/3d_printer_line/3DPrinterLine_6Stations"
    if len(sys.argv) > 1:
        default_root = sys.argv[1]

    app = QApplication(sys.argv)
    w = DashboardWindow(default_root=default_root)
    w.resize(1300, 900)
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
