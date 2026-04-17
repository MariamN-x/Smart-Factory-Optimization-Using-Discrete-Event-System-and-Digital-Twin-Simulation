#!/usr/bin/env python3
"""
Tkinter KPI Dashboard (desktop)

Run:
  python kpi_dashboard_tkinter.py

This app reuses the same parsing/tailing engine as the web dashboard.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

from live_log_dashboard_web_station_VSI_full import Engine, build_file_list


def station_number_of(item: Dict[str, Any]) -> int:
    direct = item.get("station_num")
    if isinstance(direct, (int, float)):
        return int(direct)
    txt = str(item.get("station", ""))
    digits = "".join(ch for ch in txt if ch.isdigit())
    return int(digits) if digits else 10**9


def is_production_station(item: Dict[str, Any]) -> bool:
    n = station_number_of(item)
    return 1 <= n <= 6


def normalize_state_value(state: Any) -> str:
    s = str(state or "UNKNOWN").upper()
    if s == "STOP":
        return "STOPPED"
    return s


KPI_LABELS: Dict[str, str] = {
    "inventory_ok": "Inventory OK",
    "any_arm_failed": "Any Arm Failed",
    "scrapped": "Scrapped",
    "reworks": "Reworks",
    "cycle_time_avg_s": "Cycle Time Avg (s)",
    "continuity_ok": "Continuity OK",
    "strain_relief_ok": "Strain Relief OK",
    "total": "Total",
    "accepted_total": "Accepted",
    "rejected_total": "Rejected",
    "completed_total": "Completed",
    "total_completed": "Total Completed",
    "last_accept": "Last Accept",
    "packages_completed": "Packages Completed",
    "arm_cycles": "Arm Cycles",
    "total_repairs": "Total Repairs",
    "availability": "Availability",
    "operational_time_s": "Operational Time (s)",
    "downtime_s": "Downtime (s)",
}

STATION_KPI_PROFILES: Dict[int, List[str]] = {
    1: ["total_completed", "inventory_ok", "any_arm_failed"],
    2: ["completed_total", "scrapped", "reworks", "cycle_time_avg_s"],
    3: ["total_completed", "continuity_ok", "strain_relief_ok"],
    4: ["total_completed", "completed_total", "total"],
    5: ["accepted_total", "rejected_total", "completed_total", "last_accept"],
    6: ["total_completed", "packages_completed", "arm_cycles", "total_repairs", "availability", "operational_time_s", "downtime_s"],
}

BASE_KPI_KEYS = {
    "cycles_done",
    "faults_count",
    "start_pulses",
    "reset_pulses",
    "stop_asserts",
    "busy_total_s",
    "last_cycle_ms",
}

EXTRA_KPI_HIDE_KEYS = {
    "batch_id",
    "recipe_id",
    "cumulative_accepts",
    "cumulative_rejects",
    "accept_now",
    "reject_now",
    "completed_now",
    "completed",
    "total_completed",
}


def _has_kpi_value(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return bool(v.strip())
    return True


def _as_float(v: Any) -> float | None:
    if isinstance(v, bool):
        return float(int(v))
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except Exception:
            return None
    return None


def _format_kpi_value(key: str, value: Any) -> str:
    if value is None:
        return "-"
    if key.endswith("_ok") or key == "last_accept":
        n = _as_float(value)
        iv = int(n) if n is not None else 0
        return f"{iv} ({'Yes' if iv == 1 else 'No'})"
    if key == "availability":
        n = _as_float(value)
        if n is not None:
            pct = n * 100.0 if n <= 1.0 else n
            return f"{round(pct, 1)}%"
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(round(value, 2))
    return str(value)


def _resolve_profile_value(k: Dict[str, Any], key: str) -> Any:
    if key == "completed_total":
        for c in ("completed_total", "total_completed"):
            if c in k and _has_kpi_value(k[c]):
                return k[c]
        if _as_float(k.get("completed")) and _as_float(k.get("completed")) > 1:
            return k.get("completed")
        if "cycles_done" in k and _has_kpi_value(k.get("cycles_done")):
            return k.get("cycles_done")
        return None

    if key == "total_completed":
        for c in ("total_completed", "completed_total"):
            if c in k and _has_kpi_value(k[c]):
                return k[c]
        return None

    if key == "accepted_total":
        for c in ("accepted_total", "accept"):
            if c in k and _has_kpi_value(k[c]):
                return k[c]
        return None

    if key == "rejected_total":
        for c in ("rejected_total", "reject"):
            if c in k and _has_kpi_value(k[c]):
                return k[c]
        return None

    return k.get(key)


def station_specific_kpis(item: Dict[str, Any]) -> List[Tuple[str, str]]:
    n = station_number_of(item)
    k = item.get("kpis") or {}

    selected: List[Tuple[str, str]] = []
    seen = set()

    for key in STATION_KPI_PROFILES.get(n, []):
        v = _resolve_profile_value(k, key)
        if _has_kpi_value(v) and key not in seen:
            selected.append((KPI_LABELS.get(key, key.replace("_", " ")), _format_kpi_value(key, v)))
            seen.add(key)

    if selected:
        return selected

    for kk, vv in k.items():
        key = str(kk)
        if key in BASE_KPI_KEYS:
            continue
        if key in EXTRA_KPI_HIDE_KEYS:
            continue
        if re.match(r"^s\d+_", key, re.IGNORECASE):
            continue
        if not _has_kpi_value(vv):
            continue
        selected.append((KPI_LABELS.get(key, key.replace("_", " ")), _format_kpi_value(key, vv)))
        if len(selected) >= 4:
            break
    return selected


def _dedupe_existing(paths: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for p in paths:
        pp = os.path.abspath(p)
        if pp not in seen and os.path.isfile(pp):
            out.append(pp)
            seen.add(pp)
    return out


def build_file_list_tk(args) -> List[str]:
    # Primary discovery (shared with web app).
    files = build_file_list(args)
    if files:
        return files

    # Fallback: find logs next to this script regardless of current working dir.
    script_dir = Path(__file__).resolve().parent
    extra: List[str] = []

    # Prefer canonical simulator log names first.
    extra.extend(glob.glob(str(script_dir / "check.*.log")))
    if not extra:
        extra.extend(glob.glob(str(script_dir / "*.log")))
    if not extra:
        extra.extend(glob.glob(str(script_dir / "*.txt")))

    # Last fallback: recursive under script directory.
    if not extra:
        extra.extend(glob.glob(str(script_dir / "**" / "check.*.log"), recursive=True))
    if not extra:
        extra.extend(glob.glob(str(script_dir / "**" / "*.log"), recursive=True))

    return _dedupe_existing(extra)


class TkKPIDashboard:
    def __init__(self, engine: Engine, refresh_ms: int = 350):
        self.engine = engine
        self.refresh_ms = refresh_ms

        self.root = tk.Tk()
        self.root.title("Project KPI Dashboard (Tkinter)")
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        win_w = max(980, min(1480, sw - 40))
        win_h = max(680, min(930, sh - 80))
        self.root.geometry(f"{win_w}x{win_h}+10+10")
        self.root.minsize(960, 640)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.last_payload: Dict[str, Any] = {"items": [], "summary": {}}
        self.item_by_id: Dict[str, Dict[str, Any]] = {}
        self.selected_id: str = ""

        self._setup_style()
        self._build_ui()
        self._tick()

    def _setup_style(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"))
        style.configure("Subtitle.TLabel", font=("Segoe UI", 10), foreground="#5B677A")
        style.configure("KpiLabel.TLabel", font=("Segoe UI", 10), foreground="#5B677A")
        style.configure("KpiValue.TLabel", font=("Segoe UI", 22, "bold"), foreground="#162236")

    def _build_ui(self):
        scroll_host = ttk.Frame(self.root)
        scroll_host.pack(fill=tk.BOTH, expand=True)

        self.v_scroll = ttk.Scrollbar(scroll_host, orient=tk.VERTICAL)
        self.v_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.main_canvas = tk.Canvas(
            scroll_host,
            highlightthickness=0,
            yscrollcommand=self.v_scroll.set,
            bg="#F3F5F8",
        )
        self.main_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.v_scroll.configure(command=self.main_canvas.yview)

        outer = ttk.Frame(self.main_canvas, padding=12)
        self._outer_window = self.main_canvas.create_window((0, 0), window=outer, anchor="nw")
        outer.bind("<Configure>", self._on_outer_configure)
        self.main_canvas.bind("<Configure>", self._on_main_canvas_configure)
        self.main_canvas.bind("<Enter>", lambda _e: self._bind_mousewheel())
        self.main_canvas.bind("<Leave>", lambda _e: self._unbind_mousewheel())

        hdr = ttk.Frame(outer)
        hdr.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(hdr, text="Project KPI Dashboard", style="Title.TLabel").pack(side=tk.LEFT)
        self.header_time_var = tk.StringVar(value="--")
        ttk.Label(hdr, textvariable=self.header_time_var, style="Subtitle.TLabel").pack(side=tk.RIGHT)

        ttk.Label(
            outer,
            text="Professional live view of line performance with overview and station trends",
            style="Subtitle.TLabel",
        ).pack(fill=tk.X, pady=(0, 8))

        summary = ttk.Frame(outer)
        summary.pack(fill=tk.X, pady=(0, 8))

        self.kpi_vars = {
            "stations": tk.StringVar(value="0"),
            "running": tk.StringVar(value="0"),
            "faults": tk.StringVar(value="0"),
            "stopped": tk.StringVar(value="0"),
        }

        self._kpi_card(summary, 0, "Stations", self.kpi_vars["stations"])
        self._kpi_card(summary, 1, "Running", self.kpi_vars["running"])
        self._kpi_card(summary, 2, "Faults", self.kpi_vars["faults"])
        self._kpi_card(summary, 3, "Stopped", self.kpi_vars["stopped"])
        summary.columnconfigure((0, 1, 2, 3), weight=1)

        middle = ttk.Frame(outer)
        middle.pack(fill=tk.BOTH, expand=True)
        middle.columnconfigure(0, weight=3)
        middle.columnconfigure(1, weight=4)
        middle.rowconfigure(0, weight=1)

        charts_panel = ttk.LabelFrame(middle, text="Overview Charts", padding=8)
        charts_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self.charts_notebook = ttk.Notebook(charts_panel)
        self.charts_notebook.pack(fill=tk.BOTH, expand=True)

        self.pie_canvas = self._make_chart_tab(self.charts_notebook, "State Distribution")
        self.cycles_canvas = self._make_chart_tab(self.charts_notebook, "Cycles per Station")
        self.faults_canvas = self._make_chart_tab(self.charts_notebook, "Faults per Station")
        self.util_canvas = self._make_chart_tab(self.charts_notebook, "Utilization per Station")

        table_box = ttk.LabelFrame(middle, text="Stations", padding=8)
        table_box.grid(row=0, column=1, sticky="nsew")
        table_box.columnconfigure(0, weight=1)
        table_box.rowconfigure(0, weight=1)

        cols = ("station", "file", "state", "cycles", "faults", "util")
        self.tree = ttk.Treeview(table_box, columns=cols, show="headings", height=16)
        for c, title, w in [
            ("station", "Station", 120),
            ("file", "File", 240),
            ("state", "State", 95),
            ("cycles", "Cycles", 80),
            ("faults", "Faults", 80),
            ("util", "Utilization", 95),
        ]:
            self.tree.heading(c, text=title)
            self.tree.column(c, width=w, anchor=tk.CENTER if c != "file" else tk.W)
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        yscroll = ttk.Scrollbar(table_box, orient=tk.VERTICAL, command=self.tree.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=yscroll.set)

        details = ttk.LabelFrame(outer, text="Selected Station", padding=8)
        details.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        details.columnconfigure(0, weight=1)
        details.rowconfigure(1, weight=1)

        trend_row = ttk.Frame(details)
        trend_row.grid(row=0, column=0, sticky="nsew")
        trend_row.columnconfigure(0, weight=1)

        self.sel_util_canvas = tk.Canvas(
            trend_row, width=1380, height=220, bg="white", highlightthickness=1, highlightbackground="#D5DEEA"
        )
        self.sel_util_canvas.grid(row=0, column=0, sticky="nsew", pady=(0, 8))

        self.detail_text = ScrolledText(details, height=12, wrap=tk.WORD)
        self.detail_text.grid(row=1, column=0, sticky="nsew")
        self.detail_text.insert("1.0", "Select a station row to view details.")
        self.detail_text.configure(state=tk.DISABLED)

        self.status_var = tk.StringVar(value="Starting...")
        ttk.Label(outer, textvariable=self.status_var, style="Subtitle.TLabel").pack(anchor=tk.W, pady=(8, 0))

    def _kpi_card(self, parent, col: int, label: str, var: tk.StringVar):
        card = ttk.LabelFrame(parent, text=label, padding=10)
        card.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 8, 0))
        ttk.Label(card, textvariable=var, style="KpiValue.TLabel").pack(anchor=tk.W)
        ttk.Label(card, text="Live", style="KpiLabel.TLabel").pack(anchor=tk.W)

    def _on_outer_configure(self, _evt=None):
        self.main_canvas.configure(scrollregion=self.main_canvas.bbox("all"))

    def _on_main_canvas_configure(self, evt):
        # Keep content width equal to viewport width so layout behaves like a normal page.
        self.main_canvas.itemconfigure(self._outer_window, width=evt.width)

    def _bind_mousewheel(self):
        self.main_canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.main_canvas.bind_all("<Button-4>", self._on_mousewheel_linux)
        self.main_canvas.bind_all("<Button-5>", self._on_mousewheel_linux)

    def _unbind_mousewheel(self):
        self.main_canvas.unbind_all("<MouseWheel>")
        self.main_canvas.unbind_all("<Button-4>")
        self.main_canvas.unbind_all("<Button-5>")

    def _on_mousewheel(self, evt):
        cls = str(evt.widget.winfo_class())
        if cls in ("Text", "Treeview", "TCombobox", "Combobox"):
            return
        if evt.delta:
            self.main_canvas.yview_scroll(int(-1 * (evt.delta / 120)), "units")

    def _on_mousewheel_linux(self, evt):
        cls = str(evt.widget.winfo_class())
        if cls in ("Text", "Treeview", "TCombobox", "Combobox"):
            return
        if getattr(evt, "num", None) == 4:
            self.main_canvas.yview_scroll(-1, "units")
        elif getattr(evt, "num", None) == 5:
            self.main_canvas.yview_scroll(1, "units")

    def _make_chart_tab(self, notebook: ttk.Notebook, tab_name: str) -> tk.Canvas:
        frame = ttk.Frame(notebook, padding=6)
        notebook.add(frame, text=tab_name)
        canvas = tk.Canvas(
            frame, width=620, height=300, bg="white", highlightthickness=1, highlightbackground="#D5DEEA"
        )
        canvas.pack(fill=tk.BOTH, expand=True)
        return canvas

    def _canvas_size(self, canvas: tk.Canvas) -> tuple[int, int]:
        w = max(canvas.winfo_width(), int(canvas.cget("width")))
        h = max(canvas.winfo_height(), int(canvas.cget("height")))
        return w, h

    def _tick(self):
        try:
            for _ in range(32):
                if not self.engine.pump_once():
                    break

            payload = self.engine.store.export_payload()
            self.last_payload = payload
            self._render(payload)
        except Exception as exc:
            self.status_var.set(f"Update error: {exc}")
        finally:
            self.root.after(self.refresh_ms, self._tick)

    def _short_station_label(self, station_name: str) -> str:
        txt = str(station_name or "")
        m = re.search(r"(\d+)", txt)
        if m:
            return f"S{m.group(1)}"
        if txt.upper() == "PLC":
            return "PLC"
        return txt[:6] if txt else "-"

    def _render(self, payload: Dict[str, Any]):
        items = payload.get("items", [])
        prod_items = [it for it in items if is_production_station(it)]
        prod_items.sort(key=lambda it: (station_number_of(it), str(it.get("station", ""))))

        counts = {"READY": 0, "RUNNING": 0, "FAULT": 0, "STOPPED": 0, "UNKNOWN": 0}
        labels: List[str] = []
        cycles_vals: List[int] = []
        faults_vals: List[int] = []
        util_vals: List[int] = []

        for it in prod_items:
            s = normalize_state_value(it.get("state"))
            counts[s] = counts.get(s, 0) + 1
            k = it.get("kpis") or {}
            labels.append(self._short_station_label(str(it.get("station", ""))))
            cycles_vals.append(int(k.get("cycles_done") or 0))
            faults_vals.append(int(k.get("faults_count") or 0))
            util_vals.append(int(round(float(it.get("utilization") or 0.0) * 100)))

        total_faults = sum(faults_vals)

        self.kpi_vars["stations"].set(str(len(prod_items)))
        self.kpi_vars["running"].set(str(counts.get("RUNNING", 0)))
        self.kpi_vars["faults"].set(str(total_faults))
        self.kpi_vars["stopped"].set(str(counts.get("STOPPED", 0)))

        self._draw_state_pie(
            labels=["READY", "RUNNING", "STOPPED"],
            values=[counts.get("READY", 0), counts.get("RUNNING", 0), counts.get("STOPPED", 0)],
            colors=["#2EE59D", "#00B2B2", "#FFC857"],
        )
        self._draw_bar_chart(self.cycles_canvas, labels, cycles_vals, "Cycles Done", "#12B886")
        self._draw_bar_chart(self.faults_canvas, labels, faults_vals, "Faults", "#FF6B6B")
        self._draw_bar_chart(self.util_canvas, labels, util_vals, "Utilization (%)", "#339AF0")

        self._refresh_tree(prod_items)
        self._render_selected_details()

        last_ts = 0.0
        for it in prod_items:
            try:
                last_ts = max(last_ts, float(it.get("updated_wall_ts") or 0.0))
            except Exception:
                pass
        last_txt = datetime.fromtimestamp(last_ts).strftime("%Y-%m-%d %H:%M:%S") if last_ts else "--"
        self.header_time_var.set(last_txt)
        self.status_var.set(f"Connected | {len(prod_items)} production stations")

    def _refresh_tree(self, items: List[Dict[str, Any]]):
        self.item_by_id = {str(it.get("id")): it for it in items}
        wanted = set(self.item_by_id.keys())
        have = set(self.tree.get_children(""))

        for iid in sorted(have - wanted):
            self.tree.delete(iid)

        for it in items:
            iid = str(it.get("id"))
            k = it.get("kpis") or {}
            util_pct = int(round(float(it.get("utilization") or 0.0) * 100))
            row = (
                str(it.get("station", "")),
                str(it.get("file", "")),
                normalize_state_value(it.get("state")),
                int(k.get("cycles_done") or 0),
                int(k.get("faults_count") or 0),
                f"{util_pct}%",
            )
            if iid in have:
                self.tree.item(iid, values=row)
            else:
                self.tree.insert("", tk.END, iid=iid, values=row)

        if (not self.selected_id or self.selected_id not in self.item_by_id) and items:
            self.selected_id = str(items[0].get("id"))
            self.tree.selection_set(self.selected_id)

    def _draw_state_pie(self, labels: List[str], values: List[int], colors: List[str]):
        c = self.pie_canvas
        c.delete("all")
        w, h = self._canvas_size(c)
        total = sum(values)

        c.create_text(12, 10, text="State Distribution", anchor="nw", font=("Segoe UI", 12, "bold"), fill="#1F2A44")

        if total <= 0:
            c.create_text(w // 2, h // 2, text="No data", fill="#666666", font=("Segoe UI", 14, "bold"))
            return

        cx = int(w * 0.33)
        cy = int(h * 0.55)
        r = int(min(w, h) * 0.30)

        start = 90.0
        for v, color in zip(values, colors):
            if v <= 0:
                continue
            extent = -360.0 * (v / total)
            c.create_arc(cx - r, cy - r, cx + r, cy + r, start=start, extent=extent, fill=color, outline="white")
            start += extent

        inner = int(r * 0.58)
        c.create_oval(cx - inner, cy - inner, cx + inner, cy + inner, fill="white", outline="white")
        c.create_text(cx, cy - 10, text=str(total), font=("Segoe UI", 20, "bold"), fill="#1F2A44")
        c.create_text(cx, cy + 15, text="stations", font=("Segoe UI", 10), fill="#6B7280")

        lx = int(w * 0.60)
        ly = int(h * 0.28)
        for i, (lab, val, col) in enumerate(zip(labels, values, colors)):
            pct = int(round((100.0 * val / total))) if total else 0
            y = ly + i * 35
            c.create_rectangle(lx, y, lx + 16, y + 16, fill=col, outline=col)
            c.create_text(
                lx + 24, y + 8, anchor="w", text=f"{lab}: {val} ({pct}%)", font=("Segoe UI", 11), fill="#1F2A44"
            )

    def _draw_bar_chart(self, canvas: tk.Canvas, labels: List[str], values: List[int], title: str, color: str):
        c = canvas
        c.delete("all")
        w, h = self._canvas_size(c)
        c.create_text(12, 10, text=title, anchor="nw", font=("Segoe UI", 12, "bold"), fill="#1F2A44")

        if not labels:
            c.create_text(w // 2, h // 2, text="No data", fill="#666666", font=("Segoe UI", 14, "bold"))
            return

        left, right, top, bottom = 48, 16, 36, 52
        x0, y0 = left, top
        x1, y1 = w - right, h - bottom

        c.create_line(x0, y1, x1, y1, fill="#C8D2E0")
        c.create_line(x0, y0, x0, y1, fill="#C8D2E0")

        max_val = max(1, max(values))
        n = len(labels)
        slot = (x1 - x0) / max(1, n)
        bar_w = max(12.0, slot * 0.58)

        for i, (lab, val) in enumerate(zip(labels, values)):
            bx0 = x0 + i * slot + (slot - bar_w) / 2
            bh = (y1 - y0) * (val / max_val)
            by0 = y1 - bh
            c.create_rectangle(bx0, by0, bx0 + bar_w, y1, fill=color, outline="")
            c.create_text(bx0 + bar_w / 2, by0 - 10, text=str(val), font=("Segoe UI", 9), fill="#1F2A44")
            c.create_text(bx0 + bar_w / 2, y1 + 14, text=lab, font=("Segoe UI", 9), fill="#475569")

    def _draw_line_chart(self, canvas: tk.Canvas, values: List[float], title: str, color: str, unit: str):
        c = canvas
        c.delete("all")
        w, h = self._canvas_size(c)
        c.create_text(12, 10, text=title, anchor="nw", font=("Segoe UI", 12, "bold"), fill="#1F2A44")

        if len(values) < 2:
            c.create_text(w // 2, h // 2, text="No trend data", fill="#666666", font=("Segoe UI", 13, "bold"))
            return

        left, right, top, bottom = 48, 18, 36, 38
        x0, y0 = left, top
        x1, y1 = w - right, h - bottom
        c.create_line(x0, y1, x1, y1, fill="#C8D2E0")
        c.create_line(x0, y0, x0, y1, fill="#C8D2E0")

        min_v = min(values)
        max_v = max(values)
        if abs(max_v - min_v) < 1e-9:
            max_v += 1.0
            min_v -= 1.0
        span = max_v - min_v

        points: List[float] = []
        n = len(values)
        for i, v in enumerate(values):
            x = x0 + (i / (n - 1)) * (x1 - x0)
            y = y1 - ((v - min_v) / span) * (y1 - y0)
            points.extend([x, y])
        c.create_line(*points, fill=color, width=2)

        lx, ly = points[-2], points[-1]
        c.create_oval(lx - 4, ly - 4, lx + 4, ly + 4, fill=color, outline=color)
        c.create_text(
            lx + 8,
            ly - 8,
            text=f"{values[-1]:.2f}{unit}",
            anchor="w",
            font=("Segoe UI", 9),
            fill="#1F2A44",
        )

    def _on_select(self, _evt=None):
        sel = self.tree.selection()
        self.selected_id = sel[0] if sel else ""
        self._render_selected_details()

    def _render_selected_details(self):
        self.detail_text.configure(state=tk.NORMAL)
        self.detail_text.delete("1.0", tk.END)

        it = self.item_by_id.get(self.selected_id)
        if not it:
            self.detail_text.insert("1.0", "Select a station row to view details.")
            self.detail_text.configure(state=tk.DISABLED)
            self._draw_line_chart(self.sel_util_canvas, [], "Utilization Trend (%)", "#339AF0", "%")
            return

        k = it.get("kpis") or {}
        util_hist = [float(x) for x in (it.get("utilization_hist_pct") or []) if isinstance(x, (int, float))]
        self._draw_line_chart(self.sel_util_canvas, util_hist[-140:], "Utilization Trend (%)", "#339AF0", "%")

        head = [
            f"Station: {it.get('station', '-')}",
            f"File: {it.get('file', '-')}",
            f"State: {normalize_state_value(it.get('state'))}",
            f"Utilization: {int(round(float(it.get('utilization') or 0.0) * 100))}%",
            f"Cycles: {k.get('cycles_done', 0)}",
            f"Faults: {k.get('faults_count', 0)}",
            "",
        ]
        self.detail_text.insert(tk.END, "\n".join(head))

        station_kpis = station_specific_kpis(it)
        if station_kpis:
            self.detail_text.insert(tk.END, "Station-Specific KPIs:\n")
            for label, value in station_kpis:
                self.detail_text.insert(tk.END, f"  {label}: {value}\n")
            self.detail_text.insert(tk.END, "\n")

        self.detail_text.insert(tk.END, "Inputs:\n")
        self.detail_text.insert(tk.END, json.dumps(it.get("inputs", {}), indent=2))
        self.detail_text.insert(tk.END, "\n\nOutputs:\n")
        self.detail_text.insert(tk.END, json.dumps(it.get("outputs", {}), indent=2))

        self.detail_text.configure(state=tk.DISABLED)

    def _on_close(self):
        try:
            self.engine.stop()
        except Exception:
            pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", nargs="*", help="Explicit log file paths (optional).")
    ap.add_argument("--glob", default=None, help='Glob pattern, e.g. "log/*.txt" (optional).')
    ap.add_argument(
        "--from-start",
        dest="from_start",
        action="store_true",
        default=True,
        help="Read existing content at startup (default).",
    )
    ap.add_argument(
        "--tail-only",
        dest="from_start",
        action="store_false",
        help="Tail from end of file only (skip existing content).",
    )
    ap.add_argument("--refresh-ms", type=int, default=350, help="UI refresh period in milliseconds.")
    args = ap.parse_args()

    files = build_file_list_tk(args)
    if not files:
        print("No log files found.")
        print(f"Searched current dir: {os.getcwd()}")
        print(f"Searched script dir: {Path(__file__).resolve().parent}")
        print("Tip: pass --log with explicit paths.")
        return

    engine = Engine(files, from_start=args.from_start)
    engine.start()

    app = TkKPIDashboard(engine, refresh_ms=max(100, int(args.refresh_ms)))
    app.run()


if __name__ == "__main__":
    main()
