#!/usr/bin/env python3
"""
VSI Production Line Visualizer 2 - Complete pipeline overview with embedded station visualizers
"""

import sys
import os
import re
import time
import threading
import collections
import math
import traceback
import subprocess
import importlib.util
from datetime import datetime
from typing import *
from dataclasses import dataclass, field
from enum import Enum

# Force-add user site-packages to sys.path
import site
user_site = site.getusersitepackages()
if user_site not in sys.path:
    sys.path.insert(0, user_site)

# Import PySide6
try:
    from PySide6.QtWidgets import *
    from PySide6.QtCore import *
    from PySide6.QtGui import *
    QT_AVAILABLE = True
except Exception as e:
    print(f"ERROR importing PySide6: {e}")
    sys.exit(1)

# ===== CONFIGURATION =====
SEARCH_ROOT = "."
MAX_TOKENS_DRAWN = 10
PULSE_DURATION_MS = 300
TIMER_FPS = 30

# ===== DATA MODEL =====
@dataclass
class StationState:
    station: str  # "ST1", "ST2", etc
    ready: bool = False
    busy: bool = False
    done: bool = False
    fault: bool = False
    cycle_time_ms: Optional[float] = None
    extra: Dict[str, Any] = field(default_factory=dict)
    
    def get_color(self) -> str:
        if self.fault:
            return "#ff4444"  # red
        elif self.busy:
            return "#ff9900"  # orange
        elif self.ready:
            return "#44ff44"  # green
        else:
            return "#aaaaaa"  # gray

@dataclass
class PlcState:
    state: str = "UNKNOWN"
    buffers: Dict[str, int] = field(default_factory=dict)  # Q1..Q5
    commands: Dict[str, Dict[str, bool]] = field(default_factory=dict)  # station -> {start, reset, stop}
    inputs: Dict[str, Dict[str, bool]] = field(default_factory=dict)  # station -> {ready, busy, done, fault}
    outputs: Dict[str, Dict[str, bool]] = field(default_factory=dict)  # station -> {cmd_start, cmd_reset, cmd_stop}
    extra: Dict[str, Any] = field(default_factory=dict)

@dataclass 
class PulseEvent:
    type: str  # "cmd_start", "cmd_reset", "cmd_stop", "done"
    station: str
    timestamp: float  # time.time()
    direction: str  # "plc_to_station" or "station_to_plc"

# ===== STATION LOG PARSER (from visualizer.py) =====
class StationLogParser:
    """Parser for station logs (ST1..ST6) based on visualizer.py StreamingLogParser"""
    
    def __init__(self):
        self.last_vsi_time_ns = 0
        self.synthetic_time_ns = 0
        
        # VSI time pattern
        self.vsi_time_pattern = re.compile(r"VSI\s+time\s*:\s*(\d+)\s*ns", re.IGNORECASE)
        
        # State patterns with values
        self.state_patterns = {
            "ready": re.compile(r"\bready\b\s*[:=]\s*(\d+)", re.IGNORECASE),
            "busy": re.compile(r"\bbusy\b\s*[:=]\s*(\d+)", re.IGNORECASE),
            "done": re.compile(r"\bdone\b\s*[:=]\s*(\d+)", re.IGNORECASE),
            "fault": re.compile(r"\bfault\b\s*[:=]\s*(\d+)", re.IGNORECASE),
            "ready_bool": re.compile(r"\bready\b", re.IGNORECASE),
            "busy_bool": re.compile(r"\bbusy\b", re.IGNORECASE),
            "done_bool": re.compile(r"\bdone\b", re.IGNORECASE),
            "fault_bool": re.compile(r"\bfault\b", re.IGNORECASE),
        }
        
        # Cycle time
        self.cycle_time_pattern = re.compile(r"cycle[_\s]*time[_\s]*[:=]?\s*([\d.]+)\s*ms", re.IGNORECASE)
        
        # Key-value pairs for extra data
        self.key_value_pattern = re.compile(r"(\w+)[_\s]*[:=]\s*([\w.-]+)")
    
    def parse_line(self, line: str) -> Optional[StationState]:
        """Parse a station log line and return StationState if state info found"""
        line = line.strip()
        if not line:
            return None
        
        # Check for VSI time
        vsi_match = self.vsi_time_pattern.search(line)
        if vsi_match:
            self.last_vsi_time_ns = int(vsi_match.group(1))
            return None
        
        # Use timestamp
        timestamp = self.last_vsi_time_ns if self.last_vsi_time_ns > 0 else self.synthetic_time_ns
        
        # Check for state values
        ready_val = self._get_state_value(line, "ready")
        busy_val = self._get_state_value(line, "busy")
        done_val = self._get_state_value(line, "done")
        fault_val = self._get_state_value(line, "fault")
        
        # Also check for boolean mentions
        if ready_val is None and self.state_patterns["ready_bool"].search(line):
            ready_val = 1
        if busy_val is None and self.state_patterns["busy_bool"].search(line):
            busy_val = 1
        if done_val is None and self.state_patterns["done_bool"].search(line):
            done_val = 1
        if fault_val is None and self.state_patterns["fault_bool"].search(line):
            fault_val = 1
        
        # Check for cycle time
        cycle_time = None
        cycle_match = self.cycle_time_pattern.search(line)
        if cycle_match:
            cycle_time = float(cycle_match.group(1))
        
        # Extract key-value pairs for extra data
        extra = {}
        for match in self.key_value_pattern.finditer(line):
            key, value = match.groups()
            if key.lower() in ['vsi', 'time', 'cycle', 'ready', 'busy', 'done', 'fault']:
                continue
            try:
                if '.' in value:
                    value = float(value)
                else:
                    value = int(value)
            except ValueError:
                pass
            extra[key] = value
        
        # Determine station from line context
        station = self._detect_station(line)
        if not station:
            # If no station detected but we have state info, assume ST1 for compatibility
            if any(v is not None for v in [ready_val, busy_val, done_val, fault_val]) or cycle_time:
                station = "ST1"
            else:
                return None
        
        # Create state object (only if we have some state info)
        if any(v is not None for v in [ready_val, busy_val, done_val, fault_val]) or cycle_time or extra:
            state = StationState(
                station=station,
                ready=bool(ready_val) if ready_val is not None else False,
                busy=bool(busy_val) if busy_val is not None else False,
                done=bool(done_val) if done_val is not None else False,
                fault=bool(fault_val) if fault_val is not None else False,
                cycle_time_ms=cycle_time,
                extra=extra
            )
            return state
        
        self.synthetic_time_ns += 10_000_000
        return None
    
    def _get_state_value(self, line: str, state_name: str) -> Optional[int]:
        """Get state value (0 or 1) from line"""
        pattern = self.state_patterns.get(state_name)
        if pattern:
            match = pattern.search(line)
            if match:
                return int(match.group(1))
        return None
    
    def _detect_station(self, line: str) -> Optional[str]:
        """Detect which station this line belongs to"""
        line_upper = line.upper()
        for station in ["ST1", "ST2", "ST3", "ST4", "ST5", "ST6"]:
            if station in line_upper:
                return station
        return None

# ===== PLC LOG PARSER (UPDATED FOR PLC LOGS) =====
class PlcLogParser:
    """Parser for PLC log format with buffers, TX commands, and I/O signals"""
    
    def __init__(self):
        self.last_vsi_time_ns = 0
        self.synthetic_time_ns = 0
        
        # Patterns for the new PLC log format
        self.vsi_time_pattern = re.compile(r"VSI\s+time\s*:\s*(\d+)\s*ns", re.IGNORECASE)
        
        # Buffers pattern: "Buffers: S1->S2=0, S2->S3=0, S3->S4=0, S4->S5=0, S5->S6=0"
        self.buffers_pattern = re.compile(
            r"Buffers:\s*" +
            r"S1->S2\s*=\s*(\d+)\s*,\s*" +
            r"S2->S3\s*=\s*(\d+)\s*,\s*" +
            r"S3->S4\s*=\s*(\d+)\s*,\s*" +
            r"S4->S5\s*=\s*(\d+)\s*,\s*" +
            r"S5->S6\s*=\s*(\d+)",
            re.IGNORECASE
        )
        
        # Alternative buffers pattern
        self.buffers_alt_pattern = re.compile(r"Q(\d+)\s*[:=]\s*(\d+)", re.IGNORECASE)
        self.buffers_alt2_pattern = re.compile(r"Buffer(\d+)\s*[:=]\s*(\d+)", re.IGNORECASE)
        
        # TX commands pattern: "TX S1 start=1 reset=0 stop=0"
        self.tx_pattern = re.compile(r"TX\s+(\w+)\s+start\s*=\s*(\d+)\s+reset\s*=\s*(\d+)\s+stop\s*=\s*(\d+)", re.IGNORECASE)
        
        # PLC state patterns
        self.plc_state_pattern1 = re.compile(r"PLC\s+state\s*[:=]\s*(\w+)", re.IGNORECASE)
        self.plc_state_pattern2 = re.compile(r"PLC\s+State:\s*(\w+)", re.IGNORECASE)
        
        # Station I/O patterns
        self.input_pattern = re.compile(r"(\w+)_(ready|busy|done|fault)\s*=\s*(True|False|1|0)", re.IGNORECASE)
        self.output_pattern = re.compile(r"(\w+)_cmd_(start|reset|stop)\s*=\s*(\d+)", re.IGNORECASE)
        
        # Key-value patterns for extra data
        self.key_value_pattern = re.compile(r"(\w+)\s*[:=]\s*([\w.-]+)")
    
    def parse_line(self, line: str) -> Tuple[Optional[Union[PlcState, Dict]], str]:
        """Parse a line and return event if any, along with event type"""
        line = line.strip()
        if not line:
            return None, ""
        
        # Check for VSI time
        vsi_match = self.vsi_time_pattern.search(line)
        if vsi_match:
            self.last_vsi_time_ns = int(vsi_match.group(1))
            return None, "time"
        
        # Determine timestamp to use
        timestamp = self.last_vsi_time_ns if self.last_vsi_time_ns > 0 else self.synthetic_time_ns
        
        # Check for buffers information
        buffers = self._parse_buffers(line)
        if buffers:
            plc_state = PlcState(state="UNKNOWN", buffers=buffers)
            return plc_state, "plc"
        
        # Check for TX commands
        tx_data = self._parse_tx_commands(line)
        if tx_data:
            return tx_data, "plc_commands"
        
        # Check for PLC state
        state_match = self.plc_state_pattern1.search(line) or self.plc_state_pattern2.search(line)
        if state_match:
            plc_state = PlcState(state=state_match.group(1).upper())
            return plc_state, "plc"
        
        # Check for station inputs (ready, busy, done, fault)
        inputs = self._parse_inputs(line)
        if inputs:
            return {"type": "inputs", "data": inputs}, "plc_inputs"
        
        # Check for station outputs (command signals)
        outputs = self._parse_outputs(line)
        if outputs:
            return {"type": "outputs", "data": outputs}, "plc_outputs"
        
        # Check for other key-value pairs
        extra = {}
        for match in self.key_value_pattern.finditer(line):
            key, value = match.groups()
            # Skip already parsed patterns
            if any(x in key.lower() for x in ['vsi', 'time', 'buffer', 'tx', 'state']):
                continue
            try:
                if '.' in value:
                    value = float(value)
                else:
                    value = int(value)
            except ValueError:
                pass
            extra[key] = value
        
        if extra:
            plc_state = PlcState(extra=extra)
            return plc_state, "plc"
        
        self.synthetic_time_ns += 10_000_000
        return None, ""
    
    def _parse_buffers(self, line: str) -> Dict[str, int]:
        """Parse buffer counts from line"""
        buffers = {}
        
        # Primary pattern: "Buffers: S1->S2=0, S2->S3=0, ..."
        buffers_match = self.buffers_pattern.search(line)
        if buffers_match:
            buffers = {
                "Q1": int(buffers_match.group(1)),
                "Q2": int(buffers_match.group(2)),
                "Q3": int(buffers_match.group(3)),
                "Q4": int(buffers_match.group(4)),
                "Q5": int(buffers_match.group(5))
            }
            return buffers
        
        # Alternative patterns
        for match in self.buffers_alt_pattern.finditer(line):
            buffer_num = int(match.group(1))
            if 1 <= buffer_num <= 5:
                buffers[f"Q{buffer_num}"] = int(match.group(2))
        
        for match in self.buffers_alt2_pattern.finditer(line):
            buffer_num = int(match.group(1))
            if 1 <= buffer_num <= 5:
                buffers[f"Q{buffer_num}"] = int(match.group(2))
        
        return buffers if buffers else {}
    
    def _parse_tx_commands(self, line: str) -> Optional[Dict]:
        """Parse TX command lines"""
        tx_match = self.tx_pattern.search(line)
        if tx_match:
            station_raw = tx_match.group(1).upper()
            # Convert S1->ST1, S2->ST2, etc.
            if station_raw.startswith("S") and len(station_raw) == 2:
                station_num = station_raw[1]
                if station_num.isdigit():
                    station = f"ST{station_num}"
                else:
                    station = station_raw
            else:
                station = station_raw
            
            commands = {
                "start": int(tx_match.group(2)) == 1,
                "reset": int(tx_match.group(3)) == 1,
                "stop": int(tx_match.group(4)) == 1
            }
            
            return {"type": "commands", "station": station, "commands": commands}
        
        return None
    
    def _parse_inputs(self, line: str) -> Dict[str, Dict[str, bool]]:
        """Parse station input signals"""
        inputs = {}
        for match in self.input_pattern.finditer(line):
            station_raw = match.group(1).upper()
            # Convert S1->ST1, S2->ST2, etc.
            if station_raw.startswith("S") and len(station_raw) == 2:
                station_num = station_raw[1]
                if station_num.isdigit():
                    station = f"ST{station_num}"
                else:
                    station = station_raw
            else:
                station = station_raw
            
            signal = match.group(2).lower()
            value_str = match.group(3).lower()
            value = value_str in ('true', '1')
            
            if station not in inputs:
                inputs[station] = {}
            inputs[station][signal] = value
        
        return inputs
    
    def _parse_outputs(self, line: str) -> Dict[str, Dict[str, bool]]:
        """Parse station output (command) signals"""
        outputs = {}
        for match in self.output_pattern.finditer(line):
            station_raw = match.group(1).upper()
            # Convert S1->ST1, S2->ST2, etc.
            if station_raw.startswith("S") and len(station_raw) == 2:
                station_num = station_raw[1]
                if station_num.isdigit():
                    station = f"ST{station_num}"
                else:
                    station = station_raw
            else:
                station = station_raw
            
            cmd = match.group(2).lower()
            value = int(match.group(3)) == 1
            
            if station not in outputs:
                outputs[station] = {}
            outputs[station][f"cmd_{cmd}"] = value
        
        return outputs

# ===== LOG DISCOVERY =====
class LogDiscoverer:
    @staticmethod
    def find_newest_log(pattern: str) -> Optional[str]:
        """Find newest log file matching pattern (case-insensitive)"""
        matches = []
        
        for root, dirs, files in os.walk(SEARCH_ROOT):
            for file in files:
                if file.lower().endswith('.log'):
                    if re.match(pattern.replace("*", ".*"), file, re.IGNORECASE):
                        full_path = os.path.join(root, file)
                        matches.append((full_path, os.path.getmtime(full_path)))
        
        if not matches:
            return None
        
        matches.sort(key=lambda x: x[1], reverse=True)
        return matches[0][0]
    
    @classmethod
    def discover_all_logs(cls) -> Dict[str, str]:
        """Discover all log files including PLC"""
        logs = {}
        
        # PLC log patterns
        plc_patterns = [
            r".*PLC.*",
            r".*Coordinator.*",
            r".*Controller.*"
        ]
        
        # Station log patterns
        station_patterns = {
            "ST1": [r".*ST1.*ComponentKitting.*"],
            "ST2": [r".*ST2.*FrameCoreAssembly.*"],
            "ST3": [r".*ST3.*ElectronicsWiring.*"],
            "ST4": [r".*ST4.*CalibrationTesting.*"],
            "ST5": [r".*ST5.*QualityInspection.*"],
            "ST6": [r".*ST6.*PackagingDispatch.*"],
        }
        
        # Find PLC log
        for pattern in plc_patterns:
            log_path = cls.find_newest_log(pattern)
            if log_path:
                logs["PLC"] = log_path
                print(f"Found PLC log: {log_path}")
                break
        
        if "PLC" not in logs:
            print("WARNING: No PLC log found")
        
        # Find station logs
        for station, patterns in station_patterns.items():
            log_path = None
            for pattern in patterns:
                log_path = cls.find_newest_log(pattern)
                if log_path:
                    break
            if log_path:
                logs[station] = log_path
                print(f"Found {station} log: {log_path}")
            else:
                print(f"WARNING: No log found for {station}")
        
        return logs

# ===== TAIL WORKER =====
class LogTailWorker(QThread):
    new_events = Signal(dict)  # {"type": event_type, "event": event}
    
    def __init__(self, log_files: Dict[str, str]):
        super().__init__()
        self.log_files = log_files
        self.running = True
        self.file_handles = {}
        self.file_positions = {}
        self.parsers = {}
        
    def run(self):
        """Tail all log files and emit new events"""
        # Open files
        for name, path in self.log_files.items():
            if path and os.path.exists(path):
                self._open_file(name, path)
        
        last_emit_time = time.time()
        buffer = []
        
        while self.running:
            try:
                events_found = False
                
                for name, fh in list(self.file_handles.items()):
                    if not fh:
                        continue
                    
                    # Check if file was truncated
                    current_size = os.path.getsize(self.log_files[name])
                    if current_size < self.file_positions[name]:
                        print(f"File truncated, reopening: {self.log_files[name]}")
                        self._open_file(name, self.log_files[name])
                        continue
                    
                    # Read new lines
                    try:
                        fh.seek(self.file_positions[name])
                        new_lines = fh.readlines()
                        self.file_positions[name] = fh.tell()
                    except (OSError, IOError) as e:
                        print(f"Error reading {name}: {e}")
                        self._open_file(name, self.log_files[name])
                        continue
                    
                    # Parse lines with appropriate parser
                    parser = self.parsers.get(name)
                    if not parser:
                        parser = self._create_parser_for_file(name)
                        self.parsers[name] = parser
                    
                    for line in new_lines:
                        if name == "PLC":
                            event, event_type = parser.parse_line(line)
                            if event and event_type:
                                buffer.append({
                                    "type": event_type,
                                    "event": event,
                                    "source": name
                                })
                                events_found = True
                        else:
                            # Station log
                            event = parser.parse_line(line)
                            if event:
                                buffer.append({
                                    "type": "station",
                                    "event": event,
                                    "source": name
                                })
                                events_found = True
                
                # Emit events periodically
                current_time = time.time()
                if events_found and (current_time - last_emit_time > 0.1 or len(buffer) > 10):
                    if buffer:
                        # Group by type for efficiency
                        grouped = {}
                        for item in buffer:
                            etype = item["type"]
                            if etype not in grouped:
                                grouped[etype] = []
                            grouped[etype].append({"event": item["event"], "source": item["source"]})
                        
                        for etype, events in grouped.items():
                            self.new_events.emit({"type": etype, "events": events})
                        
                        buffer.clear()
                    last_emit_time = current_time
                
                time.sleep(0.05)  # 50ms sleep
                
            except Exception as e:
                print(f"Error in tail worker: {e}")
                time.sleep(1)
        
        # Cleanup
        for fh in self.file_handles.values():
            if fh:
                fh.close()
    
    def _open_file(self, name: str, path: str):
        """Open or reopen a log file"""
        try:
            if name in self.file_handles and self.file_handles[name]:
                self.file_handles[name].close()
            
            self.file_handles[name] = open(path, 'r', encoding='utf-8', errors='ignore')
            self.file_handles[name].seek(0, os.SEEK_END)
            self.file_positions[name] = self.file_handles[name].tell()
            
            # Create appropriate parser
            self.parsers[name] = self._create_parser_for_file(name)
                
        except Exception as e:
            print(f"Error opening {path}: {e}")
            self.file_handles[name] = None
    
    def _create_parser_for_file(self, name: str):
        """Create appropriate parser for file type"""
        if name == "PLC":
            return PlcLogParser()
        else:
            return StationLogParser()
    
    def stop(self):
        self.running = False
        self.wait()

# ===== PRODUCTION LINE OVERVIEW WIDGET =====
class ProductionLineOverviewWidget(QWidget):
    """Widget that shows the complete production line overview"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # Data
        self.station_states = {}  # station -> StationState
        self.plc_state = PlcState()
        self.pulse_events = []  # list of PulseEvent
        self.buffer_counts = {"Q1": 0, "Q2": 0, "Q3": 0, "Q4": 0, "Q5": 0}
        
        # Geometry
        self.plc_rect = QRect(300, 20, 200, 80)
        self.station_rects = {}
        self.queue_rects = {}
        self.arrow_paths = {}
        
        # Colors
        self.colors = {
            "plc": QColor(150, 50, 200),
            "station_default": QColor(170, 170, 170),
            "station_ready": QColor(100, 220, 100),
            "station_busy": QColor(255, 165, 0),
            "station_fault": QColor(255, 100, 100),
            "queue": QColor(80, 80, 120),
            "token": QColor(100, 150, 255),
            "arrow": QColor(200, 200, 200),
            "arrow_pulse": QColor(255, 255, 100),
            "text": QColor(255, 255, 255),
            "background": QColor(30, 30, 40)
        }
        
        self.setMinimumSize(900, 400)
        self.setMouseTracking(True)
        
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # Fill background
        painter.fillRect(self.rect(), self.colors["background"])
        
        # Calculate geometry if not done
        if not self.station_rects:
            self.calculate_geometry()
        
        # Draw queues (between stations)
        self.draw_queues(painter)
        
        # Draw stations
        self.draw_stations(painter)
        
        # Draw PLC
        self.draw_plc(painter)
        
        # Draw arrows and pulses
        self.draw_arrows(painter)
        
        # Draw tokens in queues
        self.draw_queue_tokens(painter)
        
        # Draw labels
        self.draw_labels(painter)
        
    def calculate_geometry(self):
        """Calculate positions for all elements"""
        width = self.width()
        height = self.height()
        
        # PLC at top center
        self.plc_rect = QRect(width//2 - 100, 20, 200, 80)
        
        # Stations in a row
        station_width = 80
        station_height = 100
        station_spacing = 120
        start_x = (width - (6 * station_width + 5 * station_spacing)) // 2
        start_y = height - 150
        
        for i in range(6):
            station = f"ST{i+1}"
            x = start_x + i * (station_width + station_spacing)
            self.station_rects[station] = QRect(x, start_y, station_width, station_height)
        
        # Queues between stations
        queue_width = 50
        queue_height = 70
        for i in range(5):
            queue = f"Q{i+1}"
            # Position between station i and i+1
            left_station_rect = self.station_rects[f"ST{i+1}"]
            right_station_rect = self.station_rects[f"ST{i+2}"]
            center_x = (left_station_rect.right() + right_station_rect.left()) // 2
            x = center_x - queue_width // 2
            y = start_y + (station_height - queue_height) // 2
            self.queue_rects[queue] = QRect(x, y, queue_width, queue_height)
        
        # Arrow paths
        # PLC to each station
        plc_center = self.plc_rect.center()
        for i in range(6):
            station = f"ST{i+1}"
            station_rect = self.station_rects[station]
            station_top_center = QPoint(station_rect.center().x(), station_rect.top())
            
            path = QPainterPath()
            path.moveTo(plc_center)
            control1 = QPoint(plc_center.x(), plc_center.y() + 50)
            control2 = QPoint(station_top_center.x(), station_top_center.y() - 30)
            path.cubicTo(control1, control2, station_top_center)
            
            self.arrow_paths[f"PLC->{station}"] = path
        
        # Station to PLC (for done signals)
        for i in range(6):
            station = f"ST{i+1}"
            station_rect = self.station_rects[station]
            station_top_center = QPoint(station_rect.center().x(), station_rect.top())
            
            path = QPainterPath()
            path.moveTo(station_top_center)
            control1 = QPoint(station_top_center.x(), station_top_center.y() - 30)
            control2 = QPoint(plc_center.x(), plc_center.y() + 50)
            path.cubicTo(control1, control2, plc_center)
            
            self.arrow_paths[f"{station}->PLC"] = path
    
    def draw_plc(self, painter):
        """Draw the PLC box"""
        # Draw box
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        painter.setBrush(self.colors["plc"])
        painter.drawRoundedRect(self.plc_rect, 10, 10)
        
        # Draw text
        painter.setPen(self.colors["text"])
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        
        # PLC label
        painter.drawText(self.plc_rect, Qt.AlignCenter, "PLC")
        
        # PLC state
        font.setPointSize(8)
        font.setBold(False)
        painter.setFont(font)
        state_text = self.plc_state.state if self.plc_state.state else "UNKNOWN"
        painter.drawText(self.plc_rect.x(), self.plc_rect.y() - 5, 
                        self.plc_rect.width(), 20,
                        Qt.AlignCenter, state_text)
    
    def draw_stations(self, painter):
        """Draw all station boxes"""
        for station, rect in self.station_rects.items():
            # Get station state
            state = self.station_states.get(station, StationState(station=station))
            
            # Determine color
            if state.fault:
                color = self.colors["station_fault"]
            elif state.busy:
                color = self.colors["station_busy"]
            elif state.ready:
                color = self.colors["station_ready"]
            else:
                color = self.colors["station_default"]
            
            # Draw station box
            painter.setPen(QPen(QColor(255, 255, 255), 2))
            painter.setBrush(color)
            painter.drawRoundedRect(rect, 8, 8)
            
            # Draw station label
            painter.setPen(self.colors["text"])
            font = painter.font()
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignCenter, station)
            
            # Draw status indicators
            font.setPointSize(8)
            font.setBold(False)
            painter.setFont(font)
            
            status = []
            if state.ready: status.append("R")
            if state.busy: status.append("B")
            if state.done: status.append("D")
            if state.fault: status.append("F")
            
            if status:
                status_text = " ".join(status)
                painter.drawText(rect.x(), rect.y() + rect.height() + 15,
                                rect.width(), 20,
                                Qt.AlignCenter, status_text)
    
    def draw_queues(self, painter):
        """Draw all queue boxes"""
        for queue, rect in self.queue_rects.items():
            painter.setPen(QPen(QColor(255, 255, 255), 1))
            painter.setBrush(self.colors["queue"])
            painter.drawRoundedRect(rect, 5, 5)
    
    def draw_queue_tokens(self, painter):
        """Draw tokens inside queue boxes to represent buffer count"""
        for queue, rect in self.queue_rects.items():
            count = self.buffer_counts.get(queue, 0)
            if count <= 0:
                continue
            
            # Draw up to MAX_TOKENS_DRAWN tokens
            tokens_to_draw = min(count, MAX_TOKENS_DRAWN)
            
            # Calculate token positions
            token_size = 10
            padding = 5
            max_tokens_per_row = 3
            
            for i in range(tokens_to_draw):
                row = i // max_tokens_per_row
                col = i % max_tokens_per_row
                
                x = rect.x() + padding + col * (token_size + 2)
                y = rect.y() + padding + row * (token_size + 2)
                
                painter.setPen(Qt.NoPen)
                painter.setBrush(self.colors["token"])
                painter.drawEllipse(x, y, token_size, token_size)
            
            # Draw count text
            if count > MAX_TOKENS_DRAWN:
                painter.setPen(self.colors["text"])
                font = painter.font()
                font.setPointSize(7)
                painter.setFont(font)
                painter.drawText(rect.bottomLeft().x(), rect.bottomLeft().y() - 20,
                                rect.width(), 20, Qt.AlignCenter, f"+{count-MAX_TOKENS_DRAWN}")
    
    def draw_arrows(self, painter):
        """Draw arrows and pulses between PLC and stations"""
        current_time = time.time()
        
        # Draw all arrow paths
        painter.setPen(QPen(self.colors["arrow"], 1, Qt.DashLine))
        painter.setBrush(Qt.NoBrush)
        
        for path in self.arrow_paths.values():
            painter.drawPath(path)
        
        # Draw active pulses
        for pulse in self.pulse_events[:]:  # Iterate over copy
            elapsed = (current_time - pulse.timestamp) * 1000  # ms
            
            if elapsed > PULSE_DURATION_MS:
                self.pulse_events.remove(pulse)
                continue
            
            # Calculate pulse position along path
            if pulse.direction == "plc_to_station":
                path_key = f"PLC->{pulse.station}"
            else:
                path_key = f"{pulse.station}->PLC"
            
            if path_key in self.arrow_paths:
                path = self.arrow_paths[path_key]
                
                # Calculate progress (0 to 1)
                progress = elapsed / PULSE_DURATION_MS
                
                # Get point along path
                point = path.pointAtPercent(progress)
                
                # Draw pulse circle
                radius = 10 * (1 - abs(progress - 0.5) * 2)  # Pulse in and out
                painter.setPen(Qt.NoPen)
                painter.setBrush(self.colors["arrow_pulse"])
                painter.drawEllipse(point, radius, radius)
    
    def draw_labels(self, painter):
        """Draw queue labels"""
        painter.setPen(self.colors["text"])
        font = painter.font()
        font.setPointSize(9)
        painter.setFont(font)
        
        for queue, rect in self.queue_rects.items():
            count = self.buffer_counts.get(queue, 0)
            label = f"{queue}\n({count})"
            painter.drawText(rect, Qt.AlignCenter, label)
    
    def update_states(self, station_states: Dict[str, StationState], plc_state: PlcState):
        """Update the display with new states"""
        self.station_states = station_states
        self.plc_state = plc_state
        
        # Update buffer counts from PLC state
        if plc_state.buffers:
            for queue, count in plc_state.buffers.items():
                if queue in self.buffer_counts:
                    self.buffer_counts[queue] = count
        
        self.update()
    
    def add_pulse(self, pulse: PulseEvent):
        """Add a pulse event for visualization"""
        self.pulse_events.append(pulse)
        self.update()

# ===== STATION VISUALIZER EMBEDDING =====
def embed_station_visualizer(station_name: str) -> QWidget:
    """
    Try to embed a station visualizer from existing module.
    Returns a QWidget containing the visualizer or a placeholder.
    """
    module_name = f"{station_name.lower()}_visualizer"
    filename = f"{station_name.lower()}_visualizer.py"
    
    # Check if file exists
    if not os.path.exists(filename):
        return create_placeholder(station_name)
    
    try:
        # Try to import the module
        spec = importlib.util.spec_from_file_location(module_name, filename)
        if spec is None:
            return create_placeholder(station_name)
        
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        
        try:
            spec.loader.exec_module(module)
        except Exception as e:
            print(f"Error loading module {filename}: {e}")
            return create_placeholder(station_name)
        
        # Create a container widget to hold the embedded visualizer
        container = QWidget()
        container.layout = QVBoxLayout(container)
        container.layout.setContentsMargins(0, 0, 0, 0)
        
        # Store strong reference to prevent garbage collection
        container._embedded_instance = None
        
        # Pattern A: Look for create_widget function
        if hasattr(module, 'create_widget'):
            try:
                widget = module.create_widget(container)
                if isinstance(widget, QWidget):
                    container.layout.addWidget(widget)
                    container._embedded_instance = widget
                    return container
            except Exception as e:
                print(f"Error calling create_widget for {station_name}: {e}")
        
        # Pattern B: Look for MainWindow/Window/App class
        for class_name in ['MainWindow', 'Window', 'App']:
            if hasattr(module, class_name):
                try:
                    cls = getattr(module, class_name)
                    instance = cls()
                    container._embedded_instance = instance
                    
                    if isinstance(instance, QMainWindow):
                        central_widget = instance.centralWidget()
                        if central_widget:
                            container.layout.addWidget(central_widget)
                            # Store reference to main window too
                            container._main_window = instance
                        else:
                            # Create a placeholder if no central widget
                            return create_placeholder(station_name)
                        return container
                    elif isinstance(instance, QWidget):
                        container.layout.addWidget(instance)
                        return container
                except Exception as e:
                    print(f"Error instantiating {class_name} for {station_name}: {e}")
                    continue
        
        # Pattern C: Look for main function that returns QWidget
        if hasattr(module, 'main'):
            try:
                result = module.main()
                if isinstance(result, QWidget):
                    container.layout.addWidget(result)
                    container._embedded_instance = result
                    return container
            except Exception as e:
                print(f"Error calling main for {station_name}: {e}")
        
        # If nothing worked, create placeholder
        return create_placeholder(station_name)
        
    except Exception as e:
        print(f"Unexpected error embedding {station_name}: {e}")
        traceback.print_exc()
        return create_placeholder(station_name)

def create_placeholder(station_name: str) -> QWidget:
    """Create a placeholder widget when embedding fails"""
    widget = QWidget()
    layout = QVBoxLayout(widget)
    
    label = QLabel(f"Could not embed {station_name} visualizer")
    label.setAlignment(Qt.AlignCenter)
    layout.addWidget(label)
    
    button = QPushButton("Open External")
    button.clicked.connect(lambda: open_external_visualizer(station_name))
    layout.addWidget(button)
    
    layout.addStretch()
    return widget

def open_external_visualizer(station_name: str):
    """Open the station visualizer as a separate process"""
    filename = f"{station_name.lower()}_visualizer.py"
    if os.path.exists(filename):
        try:
            subprocess.Popen([sys.executable, filename])
        except Exception as e:
            print(f"Error opening external visualizer: {e}")
            QMessageBox.warning(None, "Error", f"Failed to open {filename}")

# ===== MAIN WINDOW =====
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VSI Production Line Visualizer 2")
        self.setGeometry(100, 100, 1600, 900)
        
        # Data storage
        self.station_states = {f"ST{i}": StationState(station=f"ST{i}") for i in range(1, 7)}
        self.plc_state = PlcState()
        self.log_files = {}
        self.is_live_mode = True
        self.is_playing = True
        self.playback_speed = 1.0
        
        # Edge detection tracking
        self.last_commands = {f"ST{i}": {"start": False, "reset": False, "stop": False} for i in range(1, 7)}
        self.last_done_states = {f"ST{i}": False for i in range(1, 7)}
        
        # Threads
        self.tail_worker = None
        
        # UI
        self.init_ui()
        
        # Animation timer
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_animation)
        self.timer.start(1000 // TIMER_FPS)
        
        # Initial log discovery
        QTimer.singleShot(100, self.discover_logs)
    
    def init_ui(self):
        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Main layout - horizontal splitter
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        
        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter)
        
        # Left side: Production line overview
        self.overview_widget = ProductionLineOverviewWidget()
        splitter.addWidget(self.overview_widget)
        
        # Right side: Tab widget with station visualizers
        self.tab_widget = QTabWidget()
        
        # Add tabs for each station
        for i in range(1, 7):
            station_name = f"ST{i}"
            tab = embed_station_visualizer(station_name)
            self.tab_widget.addTab(tab, station_name)
        
        splitter.addWidget(self.tab_widget)
        
        # Set initial splitter sizes
        splitter.setSizes([700, 900])
        
        # Bottom control bar
        control_widget = QWidget()
        control_layout = QHBoxLayout(control_widget)
        
        # Play/Pause button
        self.play_button = QPushButton("⏸")
        self.play_button.clicked.connect(self.toggle_play)
        self.play_button.setFixedWidth(40)
        control_layout.addWidget(self.play_button)
        
        # Live toggle
        self.live_checkbox = QCheckBox("Live Mode")
        self.live_checkbox.setChecked(True)
        self.live_checkbox.stateChanged.connect(self.on_live_toggled)
        control_layout.addWidget(self.live_checkbox)
        
        # Speed control
        control_layout.addWidget(QLabel("Speed:"))
        self.speed_combo = QComboBox()
        self.speed_combo.addItems(["0.5x", "1x", "2x", "4x"])
        self.speed_combo.setCurrentIndex(1)
        self.speed_combo.currentIndexChanged.connect(self.on_speed_changed)
        control_layout.addWidget(self.speed_combo)
        
        # Reload logs button
        reload_button = QPushButton("Reload Logs")
        reload_button.clicked.connect(self.discover_logs)
        control_layout.addWidget(reload_button)
        
        # Status label
        self.status_label = QLabel("No logs loaded")
        control_layout.addWidget(self.status_label)
        
        control_layout.addStretch()
        
        # Add control bar to bottom of window
        dock_widget = QWidget()
        dock_layout = QVBoxLayout(dock_widget)
        dock_layout.addWidget(control_widget)
        
        self.setStatusBar(QStatusBar())
        self.statusBar().addPermanentWidget(dock_widget)
    
    def toggle_play(self):
        self.is_playing = not self.is_playing
        self.play_button.setText("▶" if not self.is_playing else "⏸")
    
    def on_live_toggled(self, state):
        self.is_live_mode = state == Qt.Checked
        if self.is_live_mode:
            self.switch_to_live()
        else:
            self.switch_to_replay()
    
    def on_speed_changed(self, index):
        speeds = [0.5, 1.0, 2.0, 4.0]
        self.playback_speed = speeds[index]
    
    def discover_logs(self):
        """Discover and load log files"""
        self.log_files = LogDiscoverer.discover_all_logs()
        
        status_text = "Logs: "
        found_logs = []
        for name, path in self.log_files.items():
            if path:
                found_logs.append(name)
        
        if found_logs:
            status_text += ", ".join(found_logs)
        else:
            status_text += "None found"
        
        self.status_label.setText(status_text)
        
        if self.is_live_mode:
            self.switch_to_live()
        else:
            self.switch_to_replay()
    
    def switch_to_live(self):
        """Switch to live mode (tail logs)"""
        if self.tail_worker:
            self.tail_worker.stop()
            self.tail_worker = None
        
        # Start tail worker
        if self.log_files:
            self.tail_worker = LogTailWorker(self.log_files)
            self.tail_worker.new_events.connect(self.process_new_events)
            self.tail_worker.start()
    
    def switch_to_replay(self):
        """Switch to replay mode (load all logs)"""
        if self.tail_worker:
            self.tail_worker.stop()
            self.tail_worker = None
        
        # TODO: Implement replay mode
        # For now, just show message
        self.status_label.setText("Replay mode not fully implemented yet")
    
    def process_new_events(self, data):
        """Process new events from tail worker"""
        event_type = data.get("type", "")
        events_list = data.get("events", [])
        
        for event_data in events_list:
            event = event_data["event"]
            source = event_data.get("source", "")
            
            if event_type == "station":
                # Station state update from station log
                if isinstance(event, StationState):
                    self._update_station_state(event, source)
                    
            elif event_type == "plc":
                # PLC state update
                if isinstance(event, PlcState):
                    self._update_plc_state(event)
                    
            elif event_type == "plc_commands":
                # TX command update
                if isinstance(event, dict) and event.get("type") == "commands":
                    self._handle_plc_commands(event)
                    
            elif event_type == "plc_inputs":
                # Station inputs from PLC
                if isinstance(event, dict) and event.get("type") == "inputs":
                    self._handle_plc_inputs(event)
                    
            elif event_type == "plc_outputs":
                # Station outputs (commands) from PLC
                if isinstance(event, dict) and event.get("type") == "outputs":
                    self._handle_plc_outputs(event)
        
        # Update overview display
        self.overview_widget.update_states(self.station_states, self.plc_state)
    
    def _update_station_state(self, new_state: StationState, source: str):
        """Update station state with edge detection for done signals"""
        station = new_state.station
        
        # Get current state before updating
        prev_state = self.station_states.get(station, StationState(station=station))
        
        # Merge new state with existing (don't overwrite missing fields)
        if station not in self.station_states:
            self.station_states[station] = new_state
        else:
            # Update only the fields present in new_state
            current = self.station_states[station]
            if new_state.ready is not None:
                current.ready = new_state.ready
            if new_state.busy is not None:
                current.busy = new_state.busy
            if new_state.done is not None:
                current.done = new_state.done
            if new_state.fault is not None:
                current.fault = new_state.fault
            if new_state.cycle_time_ms is not None:
                current.cycle_time_ms = new_state.cycle_time_ms
            if new_state.extra:
                current.extra.update(new_state.extra)
        
        # Check for done edge detection
        current_state = self.station_states[station]
        if current_state.done and not self.last_done_states[station]:
            # Rising edge of done signal
            pulse = PulseEvent(
                type="done",
                station=station,
                timestamp=time.time(),
                direction="station_to_plc"
            )
            self.overview_widget.add_pulse(pulse)
        
        # Update last done state
        self.last_done_states[station] = current_state.done
    
    def _update_plc_state(self, new_state: PlcState):
        """Update PLC state"""
        # Merge with existing state
        if new_state.state and new_state.state != "UNKNOWN":
            self.plc_state.state = new_state.state
        
        if new_state.buffers:
            self.plc_state.buffers.update(new_state.buffers)
        
        if new_state.extra:
            self.plc_state.extra.update(new_state.extra)
    
    def _handle_plc_commands(self, command_data: dict):
        """Handle TX command updates with edge detection"""
        station = command_data.get("station")
        commands = command_data.get("commands", {})
        
        if not station or station not in self.last_commands:
            return
        
        # Get previous command states
        prev_cmds = self.last_commands[station]
        
        # Update commands in PLC state
        if station not in self.plc_state.commands:
            self.plc_state.commands[station] = {}
        
        # Check for rising edges
        for cmd_type, cmd_value in commands.items():
            prev_value = prev_cmds.get(cmd_type, False)
            
            # Update in PLC state
            self.plc_state.commands[station][cmd_type] = cmd_value
            
            # Check for rising edge (0->1)
            if cmd_value and not prev_value:
                pulse = PulseEvent(
                    type=f"cmd_{cmd_type}",
                    station=station,
                    timestamp=time.time(),
                    direction="plc_to_station"
                )
                self.overview_widget.add_pulse(pulse)
            
            # Update last command state
            prev_cmds[cmd_type] = cmd_value
    
    def _handle_plc_inputs(self, input_data: dict):
        """Handle station inputs from PLC"""
        inputs = input_data.get("data", {})
        
        for station, signals in inputs.items():
            # Update station state from PLC inputs
            if station not in self.station_states:
                self.station_states[station] = StationState(station=station)
            
            state = self.station_states[station]
            
            # Get previous done state before updating
            prev_done = state.done
            
            # Update signals
            for signal, value in signals.items():
                if signal == "ready":
                    state.ready = value
                elif signal == "busy":
                    state.busy = value
                elif signal == "done":
                    state.done = value
                elif signal == "fault":
                    state.fault = value
            
            # Check for done edge detection
            if state.done and not prev_done:
                pulse = PulseEvent(
                    type="done",
                    station=station,
                    timestamp=time.time(),
                    direction="station_to_plc"
                )
                self.overview_widget.add_pulse(pulse)
            
            # Update last done state
            self.last_done_states[station] = state.done
    
    def _handle_plc_outputs(self, output_data: dict):
        """Handle station outputs (commands) from PLC"""
        outputs = output_data.get("data", {})
        
        for station, signals in outputs.items():
            # Get previous command states
            prev_cmds = self.last_commands.get(station, {})
            
            # Update commands in PLC state
            if station not in self.plc_state.commands:
                self.plc_state.commands[station] = {}
            
            # Check for rising edges
            for signal, value in signals.items():
                # Extract command type (cmd_start -> start)
                cmd_type = signal.replace("cmd_", "")
                prev_value = prev_cmds.get(cmd_type, False)
                
                # Update in PLC state
                self.plc_state.commands[station][cmd_type] = value
                
                # Check for rising edge (0->1)
                if value and not prev_value:
                    pulse = PulseEvent(
                        type=f"cmd_{cmd_type}",
                        station=station,
                        timestamp=time.time(),
                        direction="plc_to_station"
                    )
                    self.overview_widget.add_pulse(pulse)
                
                # Update last command state
                if station not in self.last_commands:
                    self.last_commands[station] = {}
                self.last_commands[station][cmd_type] = value
    
    def update_animation(self):
        """Update animation (pulses, etc.)"""
        # Currently handled by ProductionLineOverviewWidget
        pass
    
    def closeEvent(self, event):
        """Cleanup on close"""
        if self.tail_worker:
            self.tail_worker.stop()
        event.accept()

# ===== MAIN APPLICATION =====
def main():
    app = QApplication(sys.argv)
    
    # Set application style
    app.setStyle("Fusion")
    
    # Create and show main window
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()