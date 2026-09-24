#!/usr/bin/env python3
"""
ST4 Visualizer - Standalone Qt + OpenGL visualization for ST4 Calibration & Testing logs
"""

# ===== ENVIRONMENT SETUP =====
import sys
import os
import re
import time
import math
import traceback
import collections
import copy
from typing import *
from dataclasses import dataclass, field
from datetime import datetime

# Add user site-packages to sys.path for PySide6
import site
user_site = site.getusersitepackages()
if user_site not in sys.path:
    sys.path.insert(0, user_site)

# ===== CONFIGURATION =====
def find_project_root(start_dir: str) -> str:
    start_dir = os.path.abspath(start_dir)
    markers = ["pythonGateways", "logs", "data", ".git"]
    cur = start_dir
    for _ in range(8):
        if any(os.path.exists(os.path.join(cur, m)) for m in markers):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return os.path.abspath(os.path.join(start_dir, ".."))

SCRIPT_DIR = os.path.dirname(__file__)
PROJECT_ROOT = find_project_root(SCRIPT_DIR)
SEARCH_ROOTS = []
# always include project root and current working dir
for r in [PROJECT_ROOT, os.getcwd()]:
    rr = os.path.abspath(r)
    if rr not in SEARCH_ROOTS:
        SEARCH_ROOTS.append(rr)

MAX_EVENTS = 50000
TIMER_FPS = 30
DONE_PULSE_MS = 300
OPENGL_MAJOR_VERSION = 2
OPENGL_MINOR_VERSION = 1
USE_PYOPENGL = True
SNAPSHOT_LINES = 3000
IDLE_TIMEOUT = 5.0  # seconds
LONG_IDLE_TIMEOUT = 15.0  # seconds for "STOPPED?" message
AUTO_SWITCH_ON_IDLE = False  # Whether to auto-switch to replay when idle

# Priority keys for display (shown first)
PRIORITY_EXTRA_KEYS = ['completed', 'total', 'cycle_time_ms', 'pass_count', 'last_result']

# ===== QT IMPORTS =====
try:
    from PySide6.QtWidgets import *
    from PySide6.QtCore import *
    from PySide6.QtGui import QSurfaceFormat, QPainter, QColor, QFont, QPen, QFontMetrics, QBrush
    from PySide6.QtOpenGLWidgets import QOpenGLWidget
    QT_AVAILABLE = True
except Exception as e:
    print(f"ERROR importing PySide6: {e}")
    print("Python executable:", sys.executable)
    print("Python path (first 5 entries):")
    for i, path in enumerate(sys.path[:5]):
        print(f"  {i}: {path}")
    print("\nFull traceback:")
    traceback.print_exc()
    print("\nERROR: PySide6 not installed. Install with: pip install PySide6")
    sys.exit(1)

# ===== OPENGL IMPORTS =====
if USE_PYOPENGL:
    try:
        from OpenGL.GL import *
        from OpenGL.GLU import *
        PYOPENGL_AVAILABLE = True
    except ImportError:
        PYOPENGL_AVAILABLE = False
        print("WARNING: PyOpenGL not installed. Using basic rendering.")
        print("For better graphics: pip install PyOpenGL")
else:
    PYOPENGL_AVAILABLE = False

# ===== DATA MODEL =====
@dataclass
class ST4Event:
    t_ns: int
    ready: bool = False
    busy: bool = False
    done: bool = False
    fault: bool = False
    cycle_time_ms: Optional[float] = None
    extra: Dict[str, Any] = field(default_factory=dict)

# ===== CARRY-FORWARD PARSER (ST4 SPECIFIC) =====
class ST4LogParser:
    """Carry-forward parser for ST4 Calibration & Testing logs"""
    
    def __init__(self):
        self.reset()
    
    def reset(self):
        """Reset parser state"""
        self.last_vsi_time_ns = None
        self.synthetic_time_ns = 0
        self.last_emitted_ts = None
    
        # Track SimPy time to advance timestamps by delta
        self.last_simpy_s = None
        self.seen_vsi_time = False  # have we ever seen a real VSI time line?
    
        # Carry-forward state
        self.carried_state = {'ready': None, 'busy': None, 'done': None, 'fault': None}
        self.carried_cycle_time = None
        self.carried_extra = {}
        self.has_seen_any_state = False
        self.in_outputs_section = False
        self.in_inputs_section = False
    
        # ST4-specific patterns
        # VSI time (same as ST3)
        self.vsi_time_pattern = re.compile(r"VSI\s+time\s*:\s*(\d+)\s*ns", re.IGNORECASE)
        
        # SimPy env.now - multiple formats for ST4
        # Format 1: "env.now=24.000s" (from SimState or step lines)
        # Format 2: "SimPy env.now = 24.000s" (old format)
        self.simpy_now_pattern = re.compile(
            r"(?:env\.now\s*=|SimPy\s+env\.now\s*=)\s*([\d.]+)\s*s", 
            re.IGNORECASE
        )
        
        # Time advanced with delta (ST4 specific)
        self.time_advanced_pattern = re.compile(
            r"env\.now\s*=\s*([\d.]+)s,\s*dt_s\s*=\s*[\d.]+s", 
            re.IGNORECASE
        )
        
        # SimState line (ST4 specific)
        self.simstate_pattern = re.compile(
            r"SimState:.*env\.now\s*=\s*([\d.]+)s", 
            re.IGNORECASE
        )
    
        # Boolean state updates (numeric or True/False)
        self.bool_state_inline = {
            "busy": re.compile(r"\bbusy\s*=\s*(True|False|\d+)\b", re.IGNORECASE),
            "ready": re.compile(r"\bready\s*=\s*(True|False|\d+)\b", re.IGNORECASE),
            "done": re.compile(r"\bdone\s*=\s*(True|False|\d+)\b", re.IGNORECASE),
            "fault": re.compile(r"\bfault\s*=\s*(True|False|\d+)\b", re.IGNORECASE),
        }
    
        # ST4 cycle time patterns
        self.cycle_time_patterns = [
            re.compile(r"cycle[_\s]*time[_\s]*[:=]?\s*([\d.]+)\s*ms", re.IGNORECASE),
            re.compile(r"cycle_time_ms[_\s]*[:=]?\s*([\d.]+)", re.IGNORECASE),
        ]
    
        # Key-value pattern for extras (ST4 specific)
        self.key_value_pattern = re.compile(r"(\w+)\s*=\s*([\w.-]+)")
    
        # State patterns (numeric only)
        self.state_patterns = {
            "ready": re.compile(r"\bready\b\s*=\s*(\d+)", re.IGNORECASE),
            "busy": re.compile(r"\bbusy\b\s*=\s*(\d+)", re.IGNORECASE),
            "done": re.compile(r"\bdone\b\s*=\s*(\d+)", re.IGNORECASE),
            "fault": re.compile(r"\bfault\b\s*=\s*(\d+)", re.IGNORECASE),
        }
    
        # ST4 block patterns
        self.block_start_pattern = re.compile(r"^\+=.*ST4_CalibrationTesting.*=\+$", re.IGNORECASE)
        self.block_end_pattern = re.compile(r"^=\+=$")
        self.outputs_section_pattern = re.compile(r"^\s*Outputs:", re.IGNORECASE)
        self.inputs_section_pattern = re.compile(r"^\s*Inputs:", re.IGNORECASE)
    
    def seed_from_event(self, event: ST4Event):
        """Seed the parser with an existing event to establish carried state"""
        self.carried_state = {
            'ready': 1 if event.ready else 0,
            'busy': 1 if event.busy else 0,
            'done': 1 if event.done else 0,
            'fault': 1 if event.fault else 0
        }
        self.carried_cycle_time = event.cycle_time_ms
        self.carried_extra = copy.deepcopy(event.extra)
        self.has_seen_any_state = True
        self.last_vsi_time_ns = event.t_ns
        self.synthetic_time_ns = event.t_ns
        self.last_emitted_ts = event.t_ns
    
    def _is_valid_extra_key(self, key: str) -> bool:
        """Filter extra keys to avoid garbage"""
        # Reject keys that are only digits
        if key.isdigit():
            return False
        
        # Reject keys shorter than 2 characters
        if len(key) < 2:
            return False
        
        # Reject keys that look like garbage (containing strange characters)
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', key):
            return False
        
        # Default: accept if looks reasonable
        return len(key) <= 30  # Max reasonable key length
    
    def parse_line(self, line: str) -> Optional[ST4Event]:
        line = line.strip()
        if not line:
            return None
    
        had_signal = False
    
        # ST4 section tracking
        if self.block_start_pattern.match(line):
            self.in_outputs_section = False
            self.in_inputs_section = False
        elif self.block_end_pattern.match(line):
            self.in_outputs_section = False
            self.in_inputs_section = False
        elif self.outputs_section_pattern.match(line):
            self.in_outputs_section = True
            self.in_inputs_section = False
        elif self.inputs_section_pattern.match(line):
            self.in_inputs_section = True
            self.in_outputs_section = False
    
        # ---- helper: advance timestamp using SimPy time safely ----
        def apply_simpy_time(simpy_s: float):
            nonlocal had_signal
            self.carried_extra['simpy_now_s'] = simpy_s
            had_signal = True
    
            if self.last_simpy_s is None:
                self.last_simpy_s = simpy_s
                if self.last_vsi_time_ns is None:
                    self.last_vsi_time_ns = int(simpy_s * 1e9)
                return
    
            delta_s = simpy_s - self.last_simpy_s
            self.last_simpy_s = simpy_s
    
            if delta_s < 0:
                delta_s = 0.0
    
            delta_ns = int(delta_s * 1e9)
            if delta_ns <= 0:
                delta_ns = 1
    
            if self.last_vsi_time_ns is None:
                self.last_vsi_time_ns = int(simpy_s * 1e9)
            elif self.seen_vsi_time:
                # IMPORTANT: if VSI time was epoch-like, don't compare with simpy_ns. just advance by delta.
                self.last_vsi_time_ns += delta_ns
            else:
                # if we never saw VSI time, we can treat it like simpy_ns
                self.last_vsi_time_ns = max(self.last_vsi_time_ns, int(simpy_s * 1e9))
    
        # ---- VSI time ----
        vsi_match = self.vsi_time_pattern.search(line)
        if vsi_match:
            self.last_vsi_time_ns = int(vsi_match.group(1))
            self.seen_vsi_time = True
            if self.has_seen_any_state:
                had_signal = True
    
        # ---- SimPy env.now (multiple formats) ----
        simpy_match = self.simpy_now_pattern.search(line)
        if simpy_match:
            try:
                apply_simpy_time(float(simpy_match.group(1)))
            except ValueError:
                pass
    
        # ---- Time advanced (ST4 specific format) ----
        time_advanced_match = self.time_advanced_pattern.search(line)
        if time_advanced_match:
            try:
                apply_simpy_time(float(time_advanced_match.group(1)))
            except ValueError:
                pass
        
        # ---- SimState line (ST4 specific) ----
        simstate_match = self.simstate_pattern.search(line)
        if simstate_match:
            try:
                apply_simpy_time(float(simstate_match.group(1)))
            except ValueError:
                pass
    
        # ---- numeric state updates ----
        for state_name, pattern in self.state_patterns.items():
            match = pattern.search(line)
            if match:
                try:
                    value = int(match.group(1))
                    self.carried_state[state_name] = value
                    self.has_seen_any_state = True
                    had_signal = True
                except ValueError:
                    pass
    
        # ---- inline bool/numeric state updates ----
        for state_name, pattern in self.bool_state_inline.items():
            match = pattern.search(line)
            if match:
                val_str = match.group(1).lower()
                if val_str in ['true', 'false']:
                    value = 1 if val_str == 'true' else 0
                else:
                    try:
                        value = int(val_str)
                    except ValueError:
                        continue
                
                self.carried_state[state_name] = value
                self.has_seen_any_state = True
                had_signal = True
    
        # ---- cycle time ----
        for pattern in self.cycle_time_patterns:
            match = pattern.search(line)
            if match:
                try:
                    self.carried_cycle_time = float(match.group(1))
                    had_signal = True
                    break
                except ValueError:
                    pass
    
        # ---- Inputs section: batch_id, recipe_id ----
        if self.in_inputs_section:
            for match in self.key_value_pattern.finditer(line):
                key, value = match.groups()
                k = key.lower()
                
                if k in ['batch_id', 'recipe_id', 'total']:
                    try:
                        v = float(value) if '.' in value else int(value)
                    except ValueError:
                        v = value
                    
                    self.carried_extra[key] = v
                    had_signal = True
    
        # ---- Outputs section: extras including ST4-specific signals ----
        if self.in_outputs_section:
            current_line_extras = {}
            for match in self.key_value_pattern.finditer(line):
                key, value = match.groups()
                k = key.lower()
        
                # convert value first
                try:
                    v = float(value) if '.' in value else int(value)
                except ValueError:
                    v = value
        
                # If the log gives SimPy time as a key-value, advance timestamp from it
                if k in ("simpy_now_s", "simpy_now", "simpy_s"):
                    try:
                        apply_simpy_time(float(v))
                    except Exception:
                        pass
                    current_line_extras[key] = v
                    continue
        
                # Filter out system keys
                if k in ['vsi', 'time', 'cycle', 'ready', 'busy', 'done', 'fault', 'simpy', 'env']:
                    continue
                if 'cycle_time' in k:
                    continue
                if not self._is_valid_extra_key(key):
                    continue
        
                # ST4-specific extras: total, completed
                # These are required signals for ST4
                current_line_extras[key] = v
        
            if current_line_extras:
                self.carried_extra.update(current_line_extras)
                had_signal = True
        
        # ---- choose timestamp ----
        if self.last_vsi_time_ns is not None:
            timestamp = self.last_vsi_time_ns
        else:
            self.synthetic_time_ns += 10_000_000
            timestamp = self.synthetic_time_ns
    
        # ---- emit heartbeat/state event ----
        if not had_signal:
            return None
    
        # enforce strictly increasing timestamps to avoid "flatline" replay
        if self.last_emitted_ts is not None and timestamp <= self.last_emitted_ts:
            timestamp = self.last_emitted_ts + 1
    
        self.last_emitted_ts = timestamp
    
        ready = bool(self.carried_state['ready']) if self.carried_state['ready'] is not None else False
        busy  = bool(self.carried_state['busy'])  if self.carried_state['busy']  is not None else False
        done  = bool(self.carried_state['done'])  if self.carried_state['done']  is not None else False
        fault = bool(self.carried_state['fault']) if self.carried_state['fault'] is not None else False
    
        return ST4Event(
            t_ns=timestamp,
            ready=ready,
            busy=busy,
            done=done,
            fault=fault,
            cycle_time_ms=self.carried_cycle_time,
            extra=copy.deepcopy(self.carried_extra)
        )
    
    def get_current_state(self) -> Optional[ST4Event]:
        """Get current state without parsing a line"""
        if not self.has_seen_any_state and self.carried_cycle_time is None and not self.carried_extra:
            return None
        
        # Determine timestamp
        if self.last_vsi_time_ns is not None:
            timestamp = self.last_vsi_time_ns
        else:
            timestamp = self.synthetic_time_ns
        
        # Convert carried state to booleans
        ready = bool(self.carried_state['ready']) if self.carried_state['ready'] is not None else False
        busy = bool(self.carried_state['busy']) if self.carried_state['busy'] is not None else False
        done = bool(self.carried_state['done']) if self.carried_state['done'] is not None else False
        fault = bool(self.carried_state['fault']) if self.carried_state['fault'] is not None else False
        
        return ST4Event(
            t_ns=timestamp,
            ready=ready,
            busy=busy,
            done=done,
            fault=fault,
            cycle_time_ms=self.carried_cycle_time,
            extra=copy.deepcopy(self.carried_extra)
        )

# ===== LOG DISCOVERY (ST4 SPECIFIC) =====
class LogDiscoverer:
    """Finds ST4 Calibration & Testing logs with priority for check.ST4_CalibrationTesting.log"""
    
    @staticmethod
    def find_st4_log() -> Optional[str]:
        candidates = []

        def consider_file(full_path: str, file: str):
            fl = file.lower()

            # Must be a .log file
            if not fl.endswith(".log"):
                return

            # ST4 station markers
            st4_ok = ("st4" in fl) or ("station4" in fl) or ("station_4" in fl) or ("station 4" in fl)
            
            # ST4 process markers: calibration, testing
            process_ok = ("calibrationtesting" in fl) or ("calibration" in fl) or ("testing" in fl)
            
            if not (st4_ok and process_ok):
                return

            try:
                mtime = os.path.getmtime(full_path)
                size = os.path.getsize(full_path)
            except OSError:
                return

            # Score based on filename relevance for ST4
            score = 0
            # Highest priority: exact match check.ST4_CalibrationTesting.log
            if "check.st4_calibrationtesting.log" == fl:
                score += 100
            # High priority: contains ST4 and CalibrationTesting
            if "st4_calibrationtesting" in fl:
                score += 50
            # Medium priority: check.*.log files (real ST4 logs)
            if fl.startswith("check") and st4_ok:
                score += 30
            # ST4 keyword bonuses
            if "calibration" in fl:
                score += 10
            if "testing" in fl:
                score += 10

            # Check file status
            is_check = fl.startswith("check") or fl.startswith("check_") or fl.startswith("check.")

            candidates.append((is_check, score, size, mtime, full_path))

        # Scan all roots
        for root0 in SEARCH_ROOTS:
            for root, dirs, files in os.walk(root0):
                for file in files:
                    consider_file(os.path.join(root, file), file)

        # DEBUG: print what we found
        print(f"[LogDiscoverer] ST4 candidates total = {len(candidates)}")
        
        # Show top 10 by (score,size,mtime) for visibility
        preview = sorted(candidates, key=lambda x: (-x[1], -x[2], -x[3]))[:10]
        for i, (is_check, score, size, mtime, path) in enumerate(preview, 1):
            tag = "CHECK" if is_check else "OTHER"
            print(f"  {i:02d}) {tag} score={score} size={size} mtime={datetime.fromtimestamp(mtime)} path={path}")

        if not candidates:
            return None

        # Sort by score, then size, then mtime (newest first)
        candidates.sort(key=lambda x: (-x[1], -x[2], -x[3]))
        selected = candidates[0][4]
        print(f"Selected ST4 log: {selected}")
        return selected

# ===== TAIL WORKER (SAME AS ST3) =====
class LogTailWorker(QThread):
    new_event = Signal(ST4Event)  # Emitted for every signal line
    activity_detected = Signal()  # Emitted when any line is read
    file_reopened = Signal()  # Emitted when file is reopened (rotation/truncation)
    
    def __init__(self, log_path: str, seed_event: Optional[ST4Event] = None):
        super().__init__()
        self.log_path = log_path
        self.seed_event = seed_event
        self.running = True
        self.file_handle = None
        self.file_position = 0
        self.file_inode = None
        self.parser = ST4LogParser()
        self.last_activity_time = time.time()
        
    def run(self):
        """Tail the log file and emit new events"""
        self._open_file(seed=True)
        
        while self.running:
            try:
                if not self.file_handle:
                    time.sleep(0.1)
                    continue
                
                # Check if file was rotated/truncated
                try:
                    current_size = os.path.getsize(self.log_path)
                    current_inode = os.stat(self.log_path).st_ino
                    
                    # Check for rotation (inode changed) or truncation
                    if current_inode != self.file_inode or current_size < self.file_position:
                        print("File rotated/truncated, reopening...")
                        self._open_file(seed=True)
                        self.file_reopened.emit()
                        continue
                except (OSError, IOError):
                    # File might have been deleted
                    time.sleep(1)
                    continue
                
                # Read new lines
                try:
                    self.file_handle.seek(self.file_position)
                    new_lines = self.file_handle.readlines()
                    self.file_position = self.file_handle.tell()
                except (OSError, IOError) as e:
                    print(f"Error reading file: {e}")
                    self._open_file(seed=True)
                    self.file_reopened.emit()
                    continue
                
                # Parse lines
                if new_lines:
                    self.last_activity_time = time.time()
                    self.activity_detected.emit()
                    
                    for line in new_lines:
                        event = self.parser.parse_line(line)
                        if event:
                            self.new_event.emit(event)
                
                time.sleep(0.05)  # 50ms sleep
                
            except Exception as e:
                print(f"Error in tail worker: {e}")
                time.sleep(1)
        
        # Cleanup
        if self.file_handle:
            self.file_handle.close()
    
    def _open_file(self, seed=False):
        """Open or reopen the log file"""
        try:
            if self.file_handle:
                self.file_handle.close()
            
            self.file_handle = open(self.log_path, 'r', encoding='utf-8', errors='ignore')
            self.file_handle.seek(0, os.SEEK_END)
            self.file_position = self.file_handle.tell()
            
            # Get file inode to detect rotation
            self.file_inode = os.stat(self.log_path).st_ino
            
            # Reset parser when file is reopened
            self.parser = ST4LogParser()
            
            # Seed the parser if we have a seed event
            if seed and self.seed_event:
                self.parser.seed_from_event(self.seed_event)
                
        except Exception as e:
            print(f"Error opening {self.log_path}: {e}")
            self.file_handle = None
    
    def reseed_parser(self, seed_event: ST4Event):
        """Reseed the parser with a new event (e.g., after file rotation)"""
        self.seed_event = seed_event
        self.parser.seed_from_event(seed_event)
    
    def stop(self):
        """Stop the worker thread"""
        self.running = False
        if self.isRunning():
            self.wait()

# ===== OPENGL WIDGET (ST4 SPECIFIC VISUALIZATION) =====
class ST4OpenGLWidget(QOpenGLWidget):
    """OpenGL visualization widget for ST4 Calibration & Testing"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_state = {
            'ready': False,
            'busy': False,
            'done': False,
            'fault': False,
            'cycle_time_ms': None,
            'extra': {}
        }
        self.animation_time = 0  # Will be set by MainWindow with visual_time_s
        self.done_pulse_time = 0
        self.shake_offset = 0.0
        self.last_completed_count = 0
        self.mode = "LIVE"  # Will be updated by MainWindow
        
        # ST4 stage animation parameters
        self.base_stage_s = 2.5  # Default seconds per stage
        self.stage_s = self.base_stage_s  # Current stage duration
        self.proc_active = False  # Is processing active?
        self.proc_start = 0.0  # When processing started (in visual_time_s)
        self.proc_end = 0.0  # When processing ends (4 stages)
        self.active_stage_index = -1  # -1 means no stage active
        self.stage_progress = 0.0  # Progress within current stage (0.0 to 1.0)
        
        # ST4-specific stage names
        self.stage_names = ["Move to Position", "Thermal Stabilization", "Calibration", "Test Print + Result"]
        
        # Colors for ST4
        self.colors = {
            "conveyor": (0.2, 0.2, 0.25, 1.0),
            "fixture": (0.3, 0.3, 0.35, 1.0),
            "test_platform": (0.1, 0.1, 0.15, 1.0),
            "printer_head": (0.8, 0.3, 0.1, 1.0),
            "test_sample": (0.0, 0.6, 0.8, 1.0),
            "heater": (0.8, 0.2, 0.1, 1.0),
            "calibration_probe": (0.0, 0.8, 0.4, 1.0),
            "pass_green": (0.0, 0.8, 0.0, 1.0),
            "fail_red": (0.8, 0.0, 0.0, 1.0),
            "warning_yellow": (0.8, 0.8, 0.0, 1.0),
            "fault": (1.0, 0.0, 0.0, 1.0),
            "done_pulse": (0.0, 1.0, 1.0, 1.0),
            "grid": (0.3, 0.3, 0.3, 1.0),
            "base_table": (0.15, 0.15, 0.2, 1.0),
            "stage_indicator": (0.9, 0.6, 0.1, 1.0),
            "thermal_glow": (1.0, 0.5, 0.2, 1.0),
            "neutral_gray": (0.5, 0.5, 0.5, 1.0),
        }
        
        # Camera
        self.camera_distance = 15.0
        self.camera_angle_x = 25.0
        self.camera_angle_y = 35.0
        self.last_mouse_pos = None
        
        self.setMouseTracking(True)
    
    def compute_active_stage(self, visual_time_s=None):
        """Compute which ST4 stage is active and its progress based on visual_time_s"""
        if visual_time_s is None:
            visual_time_s = self.animation_time
            
        if not self.proc_active:
            self.active_stage_index = -1
            self.stage_progress = 0.0
            return -1
        
        # If busy but timer ended, stay in stage 4 (Test Print + Result) with looping animation
        if visual_time_s >= self.proc_end:
            if self.current_state['busy']:
                # Stay in stage 4 with looping
                self.active_stage_index = 3
                # Loop every 2 seconds (using visual_time_s for determinism)
                self.stage_progress = (math.sin(visual_time_s * math.pi) * 0.5 + 0.5) * 0.7  # 0.0 to 0.7 range
                return 3
            else:
                self.proc_active = False
                self.active_stage_index = -1
                self.stage_progress = 0.0
                return -1
        
        elapsed = visual_time_s - self.proc_start
        self.active_stage_index = min(3, int(elapsed / self.stage_s))
        
        # Calculate progress within current stage
        stage_start_time = self.proc_start + self.active_stage_index * self.stage_s
        self.stage_progress = min(1.0, max(0.0, (visual_time_s - stage_start_time) / self.stage_s))
        
        return self.active_stage_index
    
    def initializeGL(self):
        if PYOPENGL_AVAILABLE:
            glEnable(GL_DEPTH_TEST)
            # Disable lighting - we're using simple colored geometry
            glDisable(GL_LIGHTING)
            glDisable(GL_LIGHT0)
            glDisable(GL_COLOR_MATERIAL)
            
            # Enable blending for transparency
            glEnable(GL_BLEND)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    
    def resizeGL(self, w, h):
        if PYOPENGL_AVAILABLE:
            glViewport(0, 0, w, h)
            glMatrixMode(GL_PROJECTION)
            glLoadIdentity()
            aspect = w / h if h > 0 else 1.0
            gluPerspective(45, aspect, 0.1, 200)
            glMatrixMode(GL_MODELVIEW)
    
    def paintGL(self):
        if not PYOPENGL_AVAILABLE:
            self.paintBasic()
            return
            
        try:
            glClearColor(0.1, 0.1, 0.15, 1.0)
            glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
            glLoadIdentity()
            
            # Camera positioning
            cam_x = self.camera_distance * math.cos(math.radians(self.camera_angle_y)) * math.cos(math.radians(self.camera_angle_x))
            cam_y = self.camera_distance * math.sin(math.radians(self.camera_angle_x))
            cam_z = self.camera_distance * math.sin(math.radians(self.camera_angle_y)) * math.cos(math.radians(self.camera_angle_x))
            
            gluLookAt(
                cam_x, cam_y, cam_z,
                0, 0, 0,
                0, 1, 0
            )
            
            self.draw_scene()
            
            # Draw overlay using QPainter
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            self.draw_overlay(painter)
            painter.end()
                
        except Exception as e:
            print(f"OpenGL error: {e}")
            traceback.print_exc()
    
    def paintBasic(self):
        """Basic painting when OpenGL is not available"""
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(30, 30, 40))
        
        center_x = self.width() // 2
        center_y = self.height() // 2
        
        # Draw overlay
        self.draw_overlay(painter)
        painter.end()
    
    # ===== 3D DRAWING HELPER FUNCTIONS =====
    def draw_box(self, cx, cy, cz, sx, sy, sz, rgba):
        """Draw a box centered at (cx, cy, cz) with size (sx, sy, sz)"""
        if not PYOPENGL_AVAILABLE:
            return
            
        glColor4f(*rgba)
        glBegin(GL_QUADS)
        
        # Front face
        glVertex3f(cx - sx/2, cy - sy/2, cz + sz/2)
        glVertex3f(cx + sx/2, cy - sy/2, cz + sz/2)
        glVertex3f(cx + sx/2, cy + sy/2, cz + sz/2)
        glVertex3f(cx - sx/2, cy + sy/2, cz + sz/2)
        
        # Back face
        glVertex3f(cx - sx/2, cy - sy/2, cz - sz/2)
        glVertex3f(cx - sx/2, cy + sy/2, cz - sz/2)
        glVertex3f(cx + sx/2, cy + sy/2, cz - sz/2)
        glVertex3f(cx + sx/2, cy - sy/2, cz - sz/2)
        
        # Left face
        glVertex3f(cx - sx/2, cy - sy/2, cz - sz/2)
        glVertex3f(cx - sx/2, cy - sy/2, cz + sz/2)
        glVertex3f(cx - sx/2, cy + sy/2, cz + sz/2)
        glVertex3f(cx - sx/2, cy + sy/2, cz - sz/2)
        
        # Right face
        glVertex3f(cx + sx/2, cy - sy/2, cz - sz/2)
        glVertex3f(cx + sx/2, cy + sy/2, cz - sz/2)
        glVertex3f(cx + sx/2, cy + sy/2, cz + sz/2)
        glVertex3f(cx + sx/2, cy - sy/2, cz + sz/2)
        
        # Top face
        glVertex3f(cx - sx/2, cy + sy/2, cz - sz/2)
        glVertex3f(cx - sx/2, cy + sy/2, cz + sz/2)
        glVertex3f(cx + sx/2, cy + sy/2, cz + sz/2)
        glVertex3f(cx + sx/2, cy + sy/2, cz - sz/2)
        
        # Bottom face
        glVertex3f(cx - sx/2, cy - sy/2, cz - sz/2)
        glVertex3f(cx + sx/2, cy - sy/2, cz - sz/2)
        glVertex3f(cx + sx/2, cy - sy/2, cz + sz/2)
        glVertex3f(cx - sx/2, cy - sy/2, cz + sz/2)
        
        glEnd()
    
    def draw_plate(self, cx, cy, cz, sx, sz, thickness, rgba):
        """Draw a flat plate (thin in Y direction)"""
        self.draw_box(cx, cy, cz, sx, thickness, sz, rgba)
    
    def draw_wire_polyline(self, points, progress_0_1, rgba):
        """Draw a wire polyline with partial length based on progress"""
        if not PYOPENGL_AVAILABLE or len(points) < 2:
            return
            
        glColor4f(*rgba)
        glLineWidth(2.0)
        glBegin(GL_LINE_STRIP)
        
        # Calculate total length of polyline
        total_length = 0.0
        segment_lengths = []
        for i in range(len(points) - 1):
            dx = points[i+1][0] - points[i][0]
            dy = points[i+1][1] - points[i][1]
            dz = points[i+1][2] - points[i][2]
            length = math.sqrt(dx*dx + dy*dy + dz*dz)
            segment_lengths.append(length)
            total_length += length
        
        if total_length == 0:
            return
            
        # Draw up to the progress point
        target_length = total_length * progress_0_1
        current_length = 0.0
        
        # Always draw first point
        glVertex3f(*points[0])
        
        for i in range(len(points) - 1):
            segment_len = segment_lengths[i]
            if current_length + segment_len >= target_length:
                # This is the segment where we stop
                remaining = target_length - current_length
                if remaining > 0 and segment_len > 0:
                    t = remaining / segment_len
                    # Interpolate point
                    x = points[i][0] + t * (points[i+1][0] - points[i][0])
                    y = points[i][1] + t * (points[i+1][1] - points[i][1])
                    z = points[i][2] + t * (points[i+1][2] - points[i][2])
                    glVertex3f(x, y, z)
                break
            else:
                # Draw whole segment
                glVertex3f(*points[i+1])
                current_length += segment_len
        
        glEnd()
    
    def draw_cylinder(self, cx, cy, cz, radius, height, rgba, segments=12):
        """Draw a simple cylinder approximation"""
        if not PYOPENGL_AVAILABLE:
            return
            
        glColor4f(*rgba)
        
        # Draw side faces
        glBegin(GL_QUAD_STRIP)
        for i in range(segments + 1):
            angle = 2.0 * math.pi * i / segments
            x = radius * math.cos(angle)
            z = radius * math.sin(angle)
            
            glVertex3f(cx + x, cy - height/2, cz + z)
            glVertex3f(cx + x, cy + height/2, cz + z)
        glEnd()
        
        # Draw top cap
        glBegin(GL_POLYGON)
        for i in range(segments):
            angle = 2.0 * math.pi * i / segments
            x = radius * math.cos(angle)
            z = radius * math.sin(angle)
            glVertex3f(cx + x, cy + height/2, cz + z)
        glEnd()
        
        # Draw bottom cap
        glBegin(GL_POLYGON)
        for i in range(segments):
            angle = 2.0 * math.pi * i / segments
            x = radius * math.cos(angle)
            z = radius * math.sin(angle)
            glVertex3f(cx + x, cy - height/2, cz + z)
        glEnd()
    
    def draw_overlay(self, painter):
        """Draw overlay text with ST4 system information"""
        # Setup font
        font = QFont("Monospace", 10)
        painter.setFont(font)
        painter.setPen(QPen(QColor(255, 255, 255), 1))
        
        # Mode and state
        mode_text = f"Mode: {self.mode}"
        state_text = f"State: R={int(self.current_state['ready'])} B={int(self.current_state['busy'])} D={int(self.current_state['done'])} F={int(self.current_state['fault'])}"
        
        # Get current completed count for display
        completed = self.current_state['extra'].get('completed', 0)
        total = self.current_state['extra'].get('total', 0)
        
        # Use animation time for display
        now_text = f"Visual Time: {self.animation_time:.1f}s"
            
        info_text = f"Completed: {completed}/{total} | {now_text}"
        
        # Active stage
        active_stage = "Idle"
        stage_color = QColor(200, 200, 200)
        if self.active_stage_index >= 0 and self.active_stage_index < 4:
            active_stage = self.stage_names[self.active_stage_index]
            stage_color = QColor(255, 215, 0)  # Gold for active
            
            # Add PASS/FAIL result based on stage completion
            if self.active_stage_index == 3 and self.stage_progress > 0.95:
                # Determine result
                if self.current_state['fault']:
                    active_stage += " (FAIL)"
                    stage_color = QColor(255, 0, 0)
                elif self.current_state['done']:
                    active_stage += " (PASS)"
                    stage_color = QColor(0, 255, 0)
        
        stage_text = f"Active Stage: {active_stage}"
        
        # Stage progress bar
        progress_bar_width = 200
        progress_bar_height = 15
        progress_bar_x = 15
        progress_bar_y = 140
        
        if self.active_stage_index >= 0:
            # Draw progress bar background
            painter.fillRect(progress_bar_x, progress_bar_y, 
                           progress_bar_width, progress_bar_height, 
                           QColor(50, 50, 50))
            
            # Draw progress - use stage_progress as single source
            progress_width = int(progress_bar_width * self.stage_progress)
            painter.fillRect(progress_bar_x, progress_bar_y, 
                           progress_width, progress_bar_height, 
                           stage_color)
            
            # Draw border
            painter.setPen(QPen(QColor(100, 100, 100), 1))
            painter.drawRect(progress_bar_x, progress_bar_y, 
                           progress_bar_width, progress_bar_height)
            
            # Draw percentage text - use stage_progress as single source
            painter.setPen(QPen(QColor(255, 255, 255), 1))
            percent_text = f"{self.stage_progress*100:.0f}%"
            painter.drawText(progress_bar_x + progress_bar_width + 10, 
                           progress_bar_y + progress_bar_height - 2, 
                           percent_text)
        
        # Determine last result
        last_result = "RUNNING"
        result_color = QColor(200, 200, 200)
        
        if self.current_state['fault']:
            last_result = "FAIL"
            result_color = QColor(255, 0, 0)
        elif self.current_state['done']:
            last_result = "PASS"
            result_color = QColor(0, 255, 0)
        elif self.current_state['busy']:
            last_result = "RUNNING"
            result_color = QColor(255, 215, 0)
        
        result_text = f"Last Result: {last_result}"
        
        # Cycle time
        cycle_text = f"Cycle: {self.current_state['cycle_time_ms'] or 'N/A'} ms"
        stage_dur_text = f"Stage Dur: {self.stage_s:.1f}s"
        
        # Draw text with background for readability
        y_offset = 20
        line_height = 20
        
        texts = [mode_text, state_text, info_text, stage_text, 
                result_text, cycle_text, stage_dur_text]
        
        colors = [None, None, None, None, 
                 result_color, None, None]
        
        for i, (text, color) in enumerate(zip(texts, colors)):
            # Draw background rectangle
            metrics = QFontMetrics(font)
            text_width = metrics.horizontalAdvance(text)
            painter.fillRect(10, y_offset + i * line_height - 15, 
                           text_width + 10, line_height, 
                           QColor(0, 0, 0, 180))
            
            # Draw text with optional color
            if color:
                painter.setPen(QPen(color, 1))
            else:
                painter.setPen(QPen(QColor(255, 255, 255), 1))
            painter.drawText(15, y_offset + i * line_height, text)
    
    def draw_scene(self):
        """Draw the 3D scene for ST4 Calibration & Testing"""
        # Draw floor grid
        self.draw_grid()
        
        # Apply shake effect if fault
        if self.current_state['fault']:
            self.shake_offset = math.sin(self.animation_time * 10) * 0.1
            glTranslatef(self.shake_offset, 0, 0)
        
        # ===== ALWAYS DRAW STATION GEOMETRY =====
        # 1. Base table
        self.draw_plate(0, -0.25, 0, 10, 8, 0.5, self.colors["base_table"])
        
        # 2. Test platform
        self.draw_box(0, 0.5, 0, 4, 0.5, 3, self.colors["test_platform"])
        
        # 3. Printer head gantry (static)
        self.draw_box(0, 3.0, 0, 6, 0.3, 0.2, self.colors["printer_head"])
        
        # 4. Test sample (always present)
        self.draw_box(0, 1.2, 0, 1.0, 0.8, 0.8, self.colors["test_sample"])
        
        # ===== STAGE-BASED ANIMATIONS =====
        self.compute_active_stage(self.animation_time)
        
        if self.active_stage_index == 0:  # Move to Position
            # Printer head moves to position
            head_x = -2.0 + self.stage_progress * 4.0  # Move from left to right
            head_y = 3.0
            
            # Draw moving printer head
            self.draw_box(head_x, head_y, 0, 0.5, 0.5, 0.5, self.colors["printer_head"])
            
            # Stage indicator
            indicator_y = 2.0 + 0.2 * math.sin(self.animation_time * 3)
            self.draw_box(0, indicator_y, 2.0, 0.3, 0.3, 0.3, self.colors["stage_indicator"])
            
        elif self.active_stage_index == 1:  # Thermal Stabilization
            # Heater glows with temperature
            heat_intensity = 0.5 + 0.5 * math.sin(self.animation_time * 2)
            heat_color = (
                self.colors["thermal_glow"][0] * heat_intensity,
                self.colors["thermal_glow"][1] * heat_intensity,
                self.colors["thermal_glow"][2] * heat_intensity,
                self.colors["thermal_glow"][3]
            )
            
            # Draw heater elements
            self.draw_box(-1.5, 1.0, -1.0, 0.3, 0.2, 0.3, heat_color)
            self.draw_box(1.5, 1.0, -1.0, 0.3, 0.2, 0.3, heat_color)
            
            # Temperature display
            temp_height = 0.5 + self.stage_progress * 1.0
            self.draw_box(0, 0.5 + temp_height/2, 2.5, 0.5, temp_height, 0.1, 
                         (heat_intensity, 0.2, 0.1, 1.0))
            
        elif self.active_stage_index == 2:  # Calibration
            # Calibration probe moves and takes measurements
            probe_y = 2.0 + 0.3 * math.sin(self.animation_time * 4)
            probe_z = -1.0 + self.stage_progress * 2.0
            
            # Draw calibration probe
            self.draw_cylinder(0, probe_y, probe_z, 0.1, 0.8, self.colors["calibration_probe"])
            
            # Measurement points
            for i in range(3):
                point_progress = min(1.0, (self.stage_progress * 3) - i)
                if point_progress > 0:
                    point_x = -1.0 + i * 1.0
                    point_size = 0.1 + 0.1 * math.sin(self.animation_time * 5)
                    self.draw_box(point_x, 1.3, 0, point_size, point_size, point_size,
                                 self.colors["calibration_probe"])
            
        elif self.active_stage_index == 3:  # Test Print + Result
            # Printer head moves and "prints" test pattern
            head_x = math.sin(self.animation_time * 2) * 2.0
            head_z = math.cos(self.animation_time * 2) * 1.0
            
            # Draw moving printer head
            self.draw_box(head_x, 3.0, head_z, 0.5, 0.5, 0.5, self.colors["printer_head"])
            
            # Result panel
            if self.stage_progress > 0.7:
                # Determine panel color
                if self.current_state['fault']:
                    panel_color = self.colors["fail_red"]
                elif self.current_state['done']:
                    panel_color = self.colors["pass_green"]
                else:
                    panel_color = self.colors["neutral_gray"]
                
                # Panel position with slight hover animation
                panel_y = 2.5 + 0.1 * math.sin(self.animation_time * 2)
                self.draw_plate(0, panel_y, -2.5, 1.5, 1.0, 0.05, panel_color)
        
        # Draw done pulse if active
        if self.animation_time - self.done_pulse_time < DONE_PULSE_MS / 1000.0:
            self.draw_done_pulse()
    
    def draw_grid(self):
        """Draw floor grid"""
        if not PYOPENGL_AVAILABLE:
            return
            
        glColor4f(*self.colors["grid"])
        glBegin(GL_LINES)
        
        size = 15.0
        steps = 30
        
        for i in range(-steps, steps + 1):
            x = i * (size / steps)
            glVertex3f(x, 0, -size/2)
            glVertex3f(x, 0, size/2)
            glVertex3f(-size/2, 0, x)
            glVertex3f(size/2, 0, x)
        
        glEnd()
    
    def draw_done_pulse(self):
        """Draw done pulse effect in 3D"""
        if not PYOPENGL_AVAILABLE:
            return
        
        pulse_progress = (self.animation_time - self.done_pulse_time) / (DONE_PULSE_MS / 1000.0)
        pulse_alpha = 1.0 - pulse_progress
        pulse_scale = 1.0 + pulse_progress * 0.5
        
        # Create pulsed color with alpha
        pulsed_color = (
            self.colors["done_pulse"][0],
            self.colors["done_pulse"][1],
            self.colors["done_pulse"][2],
            pulse_alpha
        )
        
        # Pulse in center
        glPushMatrix()
        glTranslatef(0, 0.5, 0)
        glScalef(pulse_scale * 1.2, pulse_scale * 0.7, pulse_scale * 1.0)
        self.draw_wireframe_cube()
        glPopMatrix()
    
    def draw_wireframe_cube(self):
        """Draw wireframe cube"""
        vertices = [
            (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
            (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)
        ]
        
        edges = [
            (0,1), (1,2), (2,3), (3,0),
            (4,5), (5,6), (6,7), (7,4),
            (0,4), (1,5), (2,6), (3,7)
        ]
        
        glBegin(GL_LINES)
        for edge in edges:
            for vertex in edge:
                glVertex3f(*vertices[vertex])
        glEnd()
    
    def mousePressEvent(self, event):
        self.last_mouse_pos = event.position()
    
    def mouseMoveEvent(self, event):
        if self.last_mouse_pos and event.buttons() & Qt.LeftButton:
            dx = event.position().x() - self.last_mouse_pos.x()
            dy = event.position().y() - self.last_mouse_pos.y()
            
            self.camera_angle_y += dx * 0.5
            self.camera_angle_x = max(-90, min(90, self.camera_angle_x - dy * 0.5))
            
            self.last_mouse_pos = event.position()
            self.update()
    
    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        self.camera_distance = max(5.0, min(30.0, self.camera_distance - delta * 0.01))
        self.update()
    
    def update_state(self, event: ST4Event, mode: str = "LIVE", event_time_s: float = None):
        """Update the current state from an ST4 event"""
        old_busy = self.current_state.get('busy', False)
        old_done = self.current_state.get('_last_done', False)
        old_completed = self.current_state.get('extra', {}).get('completed', 0)
        
        self.current_state = {
            'ready': event.ready,
            'busy': event.busy,
            'done': event.done,
            'fault': event.fault,
            'cycle_time_ms': event.cycle_time_ms,
            'extra': event.extra
        }
        
        self.mode = mode
        
        # Use provided event_time_s or fallback to current time for LIVE mode
        if event_time_s is None:
            event_time_s = time.time()
        
        # Check for busy rising edge OR if we're not active but should be (mid-cycle attach)
        current_completed = event.extra.get('completed', 0)
        if event.busy and not old_busy:
            # Normal busy rising edge
            self.proc_active = True
            self.proc_start = event_time_s
            
            # Reset animation state
            self.active_stage_index = -1
            self.stage_progress = 0.0
            
            # Calculate stage duration from cycle_time_ms if available
            if event.cycle_time_ms:
                total_cycle_s = event.cycle_time_ms / 1000.0
                # Divide by 4 stages, clamp to reasonable range (0.5-6.0 seconds per stage)
                calculated_stage_s = total_cycle_s / 4.0
                self.stage_s = max(0.5, min(6.0, calculated_stage_s))
            else:
                self.stage_s = self.base_stage_s
            
            self.proc_end = event_time_s + 4 * self.stage_s  # 4 stages
        
        # Handle case where visualizer attaches mid-cycle (busy already True but not active)
        elif event.busy and not self.proc_active and self.active_stage_index == -1:
            # We missed the busy rising edge, start animation anyway
            self.proc_active = True
            
            # Use cycle time to determine stage duration
            if event.cycle_time_ms:
                total_cycle_s = event.cycle_time_ms / 1000.0
                calculated_stage_s = total_cycle_s / 4.0
                self.stage_s = max(0.5, min(6.0, calculated_stage_s))
            else:
                self.stage_s = self.base_stage_s
            
            # Estimate we're in stage 3 or 4 (Calibration or Test Print) since we're already busy
            # Set proc_start in the past so animation shows active stage
            self.proc_start = event_time_s - 3 * self.stage_s  # Assume we're near the end
            self.proc_end = event_time_s + 1 * self.stage_s  # Extend a bit into the future
            
            print(f"ST4 attached mid-cycle: starting animation at estimated stage 3/4")
        
        # Stop processing on done rising edge OR completed counter increase
        if (event.done and not old_done) or (current_completed > old_completed):
            if event.done and not old_done:
                self.done_pulse_time = event_time_s
            self.proc_active = False
            self.active_stage_index = -1
            self.stage_progress = 0.0
        
        # On fault: stop processing and show FAIL
        if event.fault:
            self.proc_active = False
            self.active_stage_index = -1
            self.stage_progress = 0.0
        
        # Store last done state
        self.current_state['_last_done'] = event.done
        
        self.update()

# ===== MAIN WINDOW =====
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ST4 Calibration & Testing Visualizer")
        self.setGeometry(100, 100, 1400, 900)
        
        # Data
        self.log_path = None
        self.replay_events = []
        self.replay_min_time = 0
        self.replay_max_time = 0
        self.current_time_ns = 0
        self.is_live_mode = True
        self.is_playing = True
        self.playback_speed = 1.0
        self.raw_event_count = 0  # All parsed events
        self.accepted_event_count = 0  # Events that actually changed state
        self.last_state_snapshot = None  # For debouncing
        self.last_event_time_ns = 0  # For debug display
        self.last_event_ignored = False  # Track if last event was ignored
        self.last_activity_time = time.time()
        self.is_idle = False
        self.idle_start_time = 0.0
        self.current_mode = "LIVE"  # "LIVE" or "REPLAY"
        self.current_event = None  # Store current event for time display
        
        # Threads
        self.tail_worker = None
        
        # UI
        self.init_ui()
        
        # Timer for animation and replay
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_animation)
        self.timer.start(1000 // TIMER_FPS)
        
        # Initial log discovery
        self.discover_log()
    
    def get_visual_time_s(self):
        """Get the current visual time in seconds for deterministic animations"""
        if self.is_live_mode:
            # In LIVE mode, use SimPy time if available, otherwise current time
            if self.current_event:
                # Use SimPy time if available in the current event
                simpy_now = self.current_event.extra.get('simpy_now_s')
                if simpy_now is not None:
                    return simpy_now
            # Fallback to wall time for LIVE mode
            return time.time()
        else:
            # In REPLAY mode, find the current event based on current_time_ns
            if not self.replay_events:
                return (self.current_time_ns - self.replay_min_time) / 1e9
            
            current_event = None
            for event in self.replay_events:
                if event.t_ns <= self.current_time_ns:
                    current_event = event
                else:
                    break
            
            if current_event:
                # Use SimPy time if available in the replay event
                simpy_now = current_event.extra.get('simpy_now_s')
                if simpy_now is not None:
                    return simpy_now
            
            # Fallback to VSI time scaled to seconds
            return (self.current_time_ns - self.replay_min_time) / 1e9
    
    def init_ui(self):
        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Main layout
        main_layout = QVBoxLayout(central_widget)
        
        # Top info bar
        info_layout = QHBoxLayout()
        self.log_info_label = QLabel("Searching for ST4 Calibration & Testing log...")
        self.log_info_label.setStyleSheet("font-weight: bold; padding: 5px;")
        info_layout.addWidget(self.log_info_label)
        
        self.event_count_label = QLabel("Raw: 0 | Accepted: 0")
        info_layout.addWidget(self.event_count_label)
        
        self.debug_label = QLabel("Mode: LIVE | Last Event: NONE (no parsable state lines yet)")
        self.debug_label.setStyleSheet("font-family: monospace; padding: 5px; color: #cccccc;")
        info_layout.addWidget(self.debug_label)
        
        info_layout.addStretch()
        main_layout.addLayout(info_layout)
        
        # Center and right panel
        content_layout = QHBoxLayout()
        
        # OpenGL widget (center)
        self.gl_widget = ST4OpenGLWidget()
        content_layout.addWidget(self.gl_widget, 3)
        
        # Status panel (right)
        status_panel = QVBoxLayout()
        
        # Batch/Recipe info
        info_group = QGroupBox("Batch / Recipe Info")
        info_layout = QVBoxLayout()
        
        self.total_label = QLabel("Total: N/A")
        self.completed_label = QLabel("Completed: N/A")
        self.now_label = QLabel("Now: --:--:--")
        
        for label in [self.total_label, self.completed_label, self.now_label]:
            label.setStyleSheet("font-family: monospace; padding: 2px;")
            info_layout.addWidget(label)
        
        info_group.setLayout(info_layout)
        status_panel.addWidget(info_group)
        
        # State labels
        state_group = QGroupBox("ST4 State")
        state_layout = QVBoxLayout()
        
        self.ready_label = QLabel("Ready: False")
        self.busy_label = QLabel("Busy: False")
        self.done_label = QLabel("Done: False")
        self.fault_label = QLabel("Fault: False")
        self.cycle_label = QLabel("Cycle Time: N/A")
        
        for label in [self.ready_label, self.busy_label, self.done_label, self.fault_label, self.cycle_label]:
            label.setStyleSheet("font-family: monospace; padding: 2px;")
            state_layout.addWidget(label)
        
        state_group.setLayout(state_layout)
        status_panel.addWidget(state_group)
        
        # Stage info
        stage_group = QGroupBox("Calibration & Testing Status")
        stage_layout = QVBoxLayout()
        self.stage_status_label = QLabel("Active Stage: Idle")
        self.stage_status_label.setStyleSheet("font-family: monospace; padding: 2px; color: #CCCCCC;")
        stage_layout.addWidget(self.stage_status_label)
        
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        stage_layout.addWidget(self.progress_bar)
        
        stage_group.setLayout(stage_layout)
        status_panel.addWidget(stage_group)
        
        # Result display
        result_group = QGroupBox("Test Result")
        result_layout = QVBoxLayout()
        
        self.result_label = QLabel("Result: N/A")
        self.pass_count_label = QLabel("Pass Count: N/A")
        
        for label in [self.result_label, self.pass_count_label]:
            label.setStyleSheet("font-family: monospace; padding: 2px;")
            result_layout.addWidget(label)
        
        result_group.setLayout(result_layout)
        status_panel.addWidget(result_group)
        
        # Extra KPIs
        self.extra_group = QGroupBox("Extra Signals")
        self.extra_layout = QVBoxLayout()
        self.extra_group.setLayout(self.extra_layout)
        status_panel.addWidget(self.extra_group)
        
        status_panel.addStretch()
        content_layout.addLayout(status_panel, 1)
        
        main_layout.addLayout(content_layout, 1)
        
        # Bottom controls
        controls_layout = QHBoxLayout()
        
        # Live/Replay toggle
        self.live_toggle = QCheckBox("LIVE Mode")
        self.live_toggle.setChecked(True)
        self.live_toggle.stateChanged.connect(self.on_live_toggled)
        controls_layout.addWidget(self.live_toggle)
        
        # Play/Pause
        self.play_button = QPushButton("⏸")
        self.play_button.clicked.connect(self.toggle_play)
        self.play_button.setEnabled(False)
        controls_layout.addWidget(self.play_button)
        
        # Speed control
        controls_layout.addWidget(QLabel("Speed:"))
        self.speed_combo = QComboBox()
        self.speed_combo.addItems(["0.5x", "1x", "2x", "4x"])
        self.speed_combo.setCurrentIndex(1)
        self.speed_combo.currentIndexChanged.connect(self.on_speed_changed)
        self.speed_combo.setEnabled(False)
        controls_layout.addWidget(self.speed_combo)
        
        # Timeline slider
        controls_layout.addWidget(QLabel("Timeline:"))
        self.timeline_slider = QSlider(Qt.Horizontal)
        self.timeline_slider.setEnabled(False)
        self.timeline_slider.setMinimum(0)
        self.timeline_slider.setMaximum(1000)
        self.timeline_slider.valueChanged.connect(self.on_timeline_changed)
        controls_layout.addWidget(self.timeline_slider, 2)
        
        # Time display
        self.time_label = QLabel("00:00.000")
        self.time_label.setStyleSheet("font-family: monospace; padding: 2px;")
        controls_layout.addWidget(self.time_label)
        
        # Reload button
        reload_button = QPushButton("Reload Log")
        reload_button.clicked.connect(self.discover_log)
        controls_layout.addWidget(reload_button)
        
        main_layout.addLayout(controls_layout)
    
    def _stop_tail_worker(self):
        """Safely stop the tail worker if it exists"""
        if self.tail_worker:
            self.tail_worker.stop()
            self.tail_worker = None
    
    def load_live_snapshot(self) -> Tuple[Optional[ST4Event], int]:
        """Load snapshot of last N lines from log file, return latest event and raw count"""
        if not self.log_path or not os.path.exists(self.log_path):
            return None, 0
        
        try:
            with open(self.log_path, 'r', encoding='utf-8', errors='ignore') as f:
                # Read last N lines efficiently
                lines = collections.deque(f, maxlen=SNAPSHOT_LINES)
            
            parser = ST4LogParser()
            raw_count = 0
            latest_event = None
            
            for line in lines:
                event = parser.parse_line(line)
                if event:
                    raw_count += 1
                    latest_event = event
            
            # If we have a parser state but no event from the last line,
            # get the current state from the parser
            if latest_event is None:
                latest_event = parser.get_current_state()
            
            return latest_event, raw_count
                
        except Exception as e:
            print(f"Error loading snapshot: {e}")
            traceback.print_exc()
            return None, 0
    
    def discover_log(self):
        """Discover and load ST4 log file"""
        # Stop current worker
        self._stop_tail_worker()
        
        # Find log
        self.log_path = LogDiscoverer.find_st4_log()
        
        if self.log_path:
            base_name = os.path.basename(self.log_path)
            self.log_info_label.setText(f"ST4 Log: {base_name}")
            
            # Reset counters
            self.raw_event_count = 0
            self.accepted_event_count = 0
            self.update_event_count_label()
            
            if self.is_live_mode:
                self.switch_to_live()
            else:
                self.switch_to_replay()
        else:
            self.log_info_label.setText("No ST4 log found")
            self.log_path = None
            self.replay_events = []
    
    def switch_to_live(self):
        """Switch to live mode (tail log)"""
        if not self.log_path:
            return
        
        # Stop any existing worker
        self._stop_tail_worker()
        
        # Clear replay data
        self.replay_events = []
        self.current_time_ns = 0
        
        # Update UI for LIVE mode
        self.current_mode = "LIVE"
        self.timeline_slider.setEnabled(False)
        self.speed_combo.setEnabled(False)
        self.play_button.setEnabled(False)
        self.is_playing = False
        self.play_button.setText("▶")
        
        # Load snapshot of existing content
        latest_event, snapshot_raw_count = self.load_live_snapshot()
        self.raw_event_count = snapshot_raw_count
        self.accepted_event_count = 0
        self.update_event_count_label()
        
        if latest_event:
            self.last_state_snapshot = {
                'ready': latest_event.ready,
                'busy': latest_event.busy,
                'done': latest_event.done,
                'fault': latest_event.fault,
                'cycle_time_ms': latest_event.cycle_time_ms,
                'extra': latest_event.extra.copy() if latest_event.extra else {}
            }
            
            # Get event time for update_state
            event_time_s = latest_event.extra.get('simpy_now_s', time.time())
            self.gl_widget.update_state(latest_event, self.current_mode, event_time_s)
            self.update_display(latest_event, from_snapshot=True)
            
            # Update debug label with snapshot info
            simpy_now = latest_event.extra.get('simpy_now_s')
            if simpy_now is not None:
                time_str = f"{simpy_now:.2f}s"
            else:
                time_str = f"{latest_event.t_ns/1e9:.2f}s"
            self.debug_label.setText(f"Mode: LIVE | Snapshot: {time_str} | R={latest_event.ready} B={latest_event.busy} D={latest_event.done} F={latest_event.fault}")
            
            # Start tail worker with seeded parser
            self.tail_worker = LogTailWorker(self.log_path, seed_event=latest_event)
        else:
            self.debug_label.setText("Mode: LIVE | Last Event: NONE (no parsable state lines yet)")
            self.last_state_snapshot = None
            # Start tail worker without seed
            self.tail_worker = LogTailWorker(self.log_path)
        
        # Connect signals
        self.tail_worker.new_event.connect(self.process_new_event)
        self.tail_worker.activity_detected.connect(self.on_activity_detected)
        self.tail_worker.file_reopened.connect(self.on_file_reopened)
        self.tail_worker.start()
        
        self.last_activity_time = time.time()
        self.is_idle = False
        self.idle_start_time = 0.0
    
    def switch_to_replay(self):
        """Switch to replay mode (load full file)"""
        # Stop any existing worker
        self._stop_tail_worker()
        
        if not self.log_path:
            return
        
        # Update UI for REPLAY mode
        self.current_mode = "REPLAY"
        self.timeline_slider.setEnabled(True)
        self.speed_combo.setEnabled(True)
        self.play_button.setEnabled(True)
        self.is_playing = True
        self.play_button.setText("⏸")
        self.is_idle = False
        self.idle_start_time = 0.0
        
        # Load all events
        self.load_replay_data()
        
        # Setup timeline slider and timebase
        if self.replay_events:
            self.replay_min_time = self.replay_events[0].t_ns
            self.replay_max_time = self.replay_events[-1].t_ns
            self.current_time_ns = self.replay_min_time
            self.timeline_slider.setValue(0)
            self.update_time_label()
            
            # Get initial visual time
            visual_time_s = self.get_visual_time_s()
            self.gl_widget.animation_time = visual_time_s
            self.gl_widget.compute_active_stage(visual_time_s)
            
            # Force immediate display update with initial state
            self.update_states_from_replay()
            
            # Update debug label for REPLAY mode
            current_event = None
            for event in self.replay_events:
                if event.t_ns <= self.current_time_ns:
                    current_event = event
                else:
                    break
            
            if current_event:
                simpy_now = current_event.extra.get('simpy_now_s')
                if simpy_now is not None:
                    time_str = f"{simpy_now:.2f}s"
                else:
                    time_str = f"{current_event.t_ns/1e9:.2f}s"
                self.debug_label.setText(f"Mode: REPLAY | Event: {time_str} | R={current_event.ready} B={current_event.busy} D={current_event.done} F={current_event.fault}")
            else:
                self.debug_label.setText("Mode: REPLAY | No events loaded")
        else:
            self.debug_label.setText("Mode: REPLAY | No events loaded")
    
    def load_replay_data(self):
        """Load all events from log file for replay and compute accepted_event_count"""
        try:
            with open(self.log_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
            
            events = []
            parser = ST4LogParser()
            
            for line in lines:
                event = parser.parse_line(line)
                if event:
                    events.append(event)
            
            # Sort by time and limit
            events.sort(key=lambda x: x.t_ns)
            self.replay_events = events[-MAX_EVENTS:]
            self.raw_event_count = len(self.replay_events)
            
            # Compute accepted_event_count using the same logic as in live mode
            self.accepted_event_count = 0
            last_state_snapshot = None
            
            for event in self.replay_events:
                new_state = {
                    'ready': event.ready,
                    'busy': event.busy,
                    'done': event.done,
                    'fault': event.fault,
                    'cycle_time_ms': event.cycle_time_ms,
                    'extra': event.extra.copy() if event.extra else {}
                }
                
                if last_state_snapshot is None or self._state_changed(last_state_snapshot, new_state):
                    last_state_snapshot = new_state
                    self.accepted_event_count += 1
            
            self.update_event_count_label()
            
            print(f"Loaded {len(self.replay_events)} ST4 events for replay")
            print(f"Accepted events (state changes): {self.accepted_event_count}")
            if self.replay_events:
                print(f"Time range: {self.replay_events[0].t_ns/1e9:.3f}s to {self.replay_events[-1].t_ns/1e9:.3f}s")
            
        except Exception as e:
            print(f"Error loading {self.log_path}: {e}")
            traceback.print_exc()
            self.replay_events = []
            self.raw_event_count = 0
            self.accepted_event_count = 0
            self.update_event_count_label()
    
    def get_max_replay_time(self):
        """Get maximum timestamp across replay events"""
        if not self.replay_events:
            return 0
        return self.replay_events[-1].t_ns
    
    def get_min_replay_time(self):
        """Get minimum timestamp across replay events"""
        if not self.replay_events:
            return 0
        return self.replay_events[0].t_ns
    
    def update_states_from_replay(self):
        """Update states based on current replay time"""
        if not self.replay_events:
            return
        
        current_time = self.current_time_ns
        current_event = None
        
        # Find last event <= current time
        for event in self.replay_events:
            if event.t_ns <= current_time:
                current_event = event
            else:
                break
        
        if current_event:
            # Get event time for update_state
            event_time_s = current_event.extra.get('simpy_now_s', (current_time - self.replay_min_time) / 1e9)
            self.gl_widget.update_state(current_event, self.current_mode, event_time_s)
            
            # For replay mode, update display without debouncing
            self.update_display(current_event, from_snapshot=False)
            
            # Update debug label for REPLAY mode
            simpy_now = current_event.extra.get('simpy_now_s')
            if simpy_now is not None:
                time_str = f"{simpy_now:.2f}s"
            else:
                time_str = f"{current_event.t_ns/1e9:.2f}s"
            self.debug_label.setText(f"Mode: REPLAY | Event: {time_str} | R={current_event.ready} B={current_event.busy} D={current_event.done} F={current_event.fault}")
    
    def update_event_count_label(self):
        """Update the event count label"""
        self.event_count_label.setText(f"Raw: {self.raw_event_count} | Accepted: {self.accepted_event_count}")
    
    def update_time_label(self):
        """Update the time display label"""
        if self.is_live_mode:
            if self.is_idle:
                idle_time = time.time() - self.idle_start_time
                if idle_time > LONG_IDLE_TIMEOUT:
                    self.time_label.setText("LIVE (STOPPED?)")
                else:
                    self.time_label.setText("LIVE (IDLE)")
            else:
                # In live mode, show SimPy time if available, otherwise "LIVE"
                if self.current_event:
                    simpy_now = self.current_event.extra.get('simpy_now_s')
                    if simpy_now is not None:
                        self.time_label.setText(f"LIVE (SimPy: {simpy_now:.1f}s)")
                    else:
                        self.time_label.setText("LIVE")
                else:
                    self.time_label.setText("LIVE")
        else:
            # In replay mode, show SimPy time if available, otherwise VSI time
            if self.replay_events:
                # Find current event based on current_time_ns
                current_event = None
                for event in self.replay_events:
                    if event.t_ns <= self.current_time_ns:
                        current_event = event
                    else:
                        break
                
                if current_event:
                    simpy_now = current_event.extra.get('simpy_now_s')
                    if simpy_now is not None:
                        # Show SimPy time with total range
                        total_simpy_start = self.replay_events[0].extra.get('simpy_now_s', 0)
                        total_simpy_end = self.replay_events[-1].extra.get('simpy_now_s', simpy_now + 1)
                        self.time_label.setText(f"{simpy_now:07.3f}s / {total_simpy_end:07.3f}s")
                    else:
                        # Fall back to VSI time
                        time_s = (self.current_time_ns - self.replay_min_time) / 1e9
                        total_s = (self.replay_max_time - self.replay_min_time) / 1e9
                        self.time_label.setText(f"{time_s:07.3f}s / {total_s:07.3f}s")
                else:
                    self.time_label.setText("00:00.000")
            else:
                self.time_label.setText("00:00.000")
    
    def process_new_event(self, event: ST4Event):
        """Process new event from tail worker"""
        # Increment raw count
        self.raw_event_count += 1
        self.update_event_count_label()
        
        # Store current event
        self.current_event = event
        
        # Check if state has actually changed
        new_state = {
            'ready': event.ready,
            'busy': event.busy,
            'done': event.done,
            'fault': event.fault,
            'cycle_time_ms': event.cycle_time_ms,
            'extra': event.extra.copy() if event.extra else {}
        }
        
        # Always update last activity time
        self.last_activity_time = time.time()
        if self.is_idle:
            self.is_idle = False
            self.idle_start_time = 0.0
        
        # Check if state changed
        if self.last_state_snapshot is None or self._state_changed(self.last_state_snapshot, new_state):
            self.last_state_snapshot = new_state
            self.accepted_event_count += 1
            self.update_event_count_label()
            
            # Get event time for update_state
            event_time_s = event.extra.get('simpy_now_s', time.time())
            self.gl_widget.update_state(event, self.current_mode, event_time_s)
            self.update_display(event, from_snapshot=False)
            
            # Use SimPy time in debug label if available
            simpy_now = event.extra.get('simpy_now_s')
            if simpy_now is not None:
                time_str = f"{simpy_now:.2f}s"
            else:
                time_str = f"{event.t_ns/1e9:.2f}s"
                
            self.debug_label.setText(f"Mode: LIVE | Accepted: {time_str} | R={event.ready} B={event.busy} D={event.done} F={event.fault}")
            self.last_event_ignored = False
        else:
            # State didn't change
            simpy_now = event.extra.get('simpy_now_s')
            if simpy_now is not None:
                time_str = f"{simpy_now:.2f}s"
            else:
                time_str = f"{event.t_ns/1e9:.2f}s"
                
            self.debug_label.setText(f"Mode: LIVE | No change: {time_str} | R={event.ready} B={event.busy} D={event.done} F={event.fault}")
            self.last_event_ignored = True
    
    def on_activity_detected(self):
        """Handle activity detection from tail worker"""
        self.last_activity_time = time.time()
        if self.is_idle:
            self.is_idle = False
            self.idle_start_time = 0.0
            self.update_time_label()
    
    def on_idle_detected(self):
        """Handle idle detection"""
        if not self.is_idle and self.is_live_mode:
            self.is_idle = True
            self.idle_start_time = time.time()
            self.update_time_label()
            
            # Auto-switch to replay if enabled and idle
            if AUTO_SWITCH_ON_IDLE and self.is_live_mode:
                self.live_toggle.setChecked(False)
    
    def on_file_reopened(self):
        """Handle file reopening (rotation/truncation)"""
        if not self.log_path or not self.is_live_mode:
            return
        
        # Reload snapshot to get latest state
        latest_event, _ = self.load_live_snapshot()
        if latest_event and self.tail_worker:
            # Reseed the parser with the latest state
            self.tail_worker.reseed_parser(latest_event)
            # Update UI with the latest state
            event_time_s = latest_event.extra.get('simpy_now_s', time.time())
            self.gl_widget.update_state(latest_event, self.current_mode, event_time_s)
            self.update_display(latest_event, from_snapshot=False)
    
    def _state_changed(self, old_state: dict, new_state: dict) -> bool:
        """Check if state has meaningfully changed"""
        # Check basic states
        for key in ['ready', 'busy', 'done', 'fault']:
            if old_state.get(key) != new_state.get(key):
                return True
        
        # Check cycle time (with tolerance for floating point)
        old_cycle = old_state.get('cycle_time_ms')
        new_cycle = new_state.get('cycle_time_ms')
        if old_cycle is not None and new_cycle is not None:
            if abs(old_cycle - new_cycle) > 0.01:
                return True
        elif old_cycle != new_cycle:  # One is None, other is not
            return True
        
        # Check extra signals
        old_extras = old_state.get('extra', {})
        new_extras = new_state.get('extra', {})
        
        # Check if any tracked extra signal changed
        tracked_keys = set(old_extras.keys()) | set(new_extras.keys())
        for key in tracked_keys:
            if old_extras.get(key) != new_extras.get(key):
                return True
        
        return False
    
    def update_display(self, event: ST4Event, from_snapshot: bool = False):
        """Update all displays from ST4 event"""
        # Note: gl_widget state is already updated in update_state() call
        
        # Update status labels
        self.ready_label.setText(f"Ready: {event.ready}")
        self.busy_label.setText(f"Busy: {event.busy}")
        self.done_label.setText(f"Done: {event.done}")
        self.fault_label.setText(f"Fault: {event.fault}")
        
        if event.cycle_time_ms:
            self.cycle_label.setText(f"Cycle Time: {event.cycle_time_ms:.1f} ms")
        else:
            self.cycle_label.setText("Cycle Time: N/A")
        
        # Update total and completed
        total = event.extra.get('total', 'N/A')
        completed = event.extra.get('completed', 0)
        
        # Use visual time for display
        now_text = f"Visual Time: {self.gl_widget.animation_time:.1f}s"
        
        self.total_label.setText(f"Total: {total}")
        self.completed_label.setText(f"Completed: {completed}")
        self.now_label.setText(now_text)
        
        # Determine result
        result = "RUNNING"
        result_color = "#CCCCCC"
        
        if event.fault:
            result = "FAIL"
            result_color = "#FF0000"
        elif event.done:
            result = "PASS"
            result_color = "#00FF00"
        elif event.busy:
            result = "RUNNING"
            result_color = "#FFD700"
        
        self.result_label.setText(f"Result: {result}")
        self.result_label.setStyleSheet(f"font-family: monospace; padding: 2px; color: {result_color};")
        
        # Pass count (completed count)
        self.pass_count_label.setText(f"Pass Count: {completed}")
        
        # Update stage status - use gl_widget.stage_progress as single source
        active_index = self.gl_widget.active_stage_index
        if active_index >= 0 and active_index < 4:
            stage_name = self.gl_widget.stage_names[active_index]
            self.stage_status_label.setText(f"Active Stage: {stage_name}")
            self.stage_status_label.setStyleSheet("font-family: monospace; padding: 2px; color: #FFD700;")
            
            # Update progress bar - use gl_widget.stage_progress as single source
            progress = int(self.gl_widget.stage_progress * 100)
            self.progress_bar.setValue(progress)
        else:
            self.stage_status_label.setText(f"Active Stage: Idle")
            self.stage_status_label.setStyleSheet("font-family: monospace; padding: 2px; color: #CCCCCC;")
            self.progress_bar.setValue(0)
        
        # Update extra KPIs with priority ordering
        # Clear existing widgets
        for i in reversed(range(self.extra_layout.count())):
            widget = self.extra_layout.itemAt(i).widget()
            if widget:
                widget.deleteLater()
        
        # Gather extra items with priority keys first
        priority_items = []
        other_items = []
        
        for key, value in event.extra.items():
            if key in PRIORITY_EXTRA_KEYS:
                priority_items.append((key, value))
            else:
                other_items.append((key, value))
        
        # Sort priority items by our defined order
        priority_items.sort(key=lambda x: PRIORITY_EXTRA_KEYS.index(x[0]) if x[0] in PRIORITY_EXTRA_KEYS else len(PRIORITY_EXTRA_KEYS))
        
        # Take up to 6 items total, prioritizing the priority items
        all_items = priority_items + other_items
        extra_items = all_items[:6]
        
        for key, value in extra_items:
            label = QLabel(f"{key}: {value}")
            label.setStyleSheet("font-family: monospace; padding: 2px;")
            self.extra_layout.addWidget(label)
        
        # Hide extra group if no extra signals
        self.extra_group.setVisible(len(extra_items) > 0)
    
    def update_animation(self):
        """Update animation based on timer"""
        # Get current visual time
        visual_time_s = self.get_visual_time_s()
        
        # Update animation time in gl_widget
        self.gl_widget.animation_time = visual_time_s
        
        # Compute active stage using visual time
        self.gl_widget.compute_active_stage(visual_time_s)
        active_index = self.gl_widget.active_stage_index
        
        # Update stage status and progress bar - use gl_widget.stage_progress as single source
        if active_index >= 0 and active_index < 4:
            stage_name = self.gl_widget.stage_names[active_index]
            self.stage_status_label.setText(f"Active Stage: {stage_name}")
            self.stage_status_label.setStyleSheet("font-family: monospace; padding: 2px; color: #FFD700;")
            
            # Update progress bar - use gl_widget.stage_progress as single source
            progress = int(self.gl_widget.stage_progress * 100)
            self.progress_bar.setValue(progress)
        else:
            self.stage_status_label.setText("Active Stage: Idle")
            self.stage_status_label.setStyleSheet("font-family: monospace; padding: 2px; color: #CCCCCC;")
            self.progress_bar.setValue(0)
        
        if self.is_playing and not self.is_live_mode and self.replay_events:
            # Advance replay time
            time_delta_ns = int(33_333_333 * self.playback_speed)  # ~30 FPS
            self.current_time_ns += time_delta_ns
            
            # Check bounds
            if self.current_time_ns > self.replay_max_time:
                self.current_time_ns = self.replay_max_time
                self.is_playing = False
                self.play_button.setText("▶")
            
            # Update slider
            time_range = self.replay_max_time - self.replay_min_time
            if time_range > 0:
                slider_value = int((self.current_time_ns - self.replay_min_time) * 1000 / time_range)
                self.timeline_slider.blockSignals(True)
                self.timeline_slider.setValue(slider_value)
                self.timeline_slider.blockSignals(False)
            
            self.update_states_from_replay()
            self.update_time_label()
        
        # Check for idle in LIVE mode
        if self.is_live_mode and not self.is_idle:
            idle_time = time.time() - self.last_activity_time
            if idle_time > IDLE_TIMEOUT:
                self.on_idle_detected()
        elif self.is_live_mode and self.is_idle:
            # Update time label with idle status
            self.update_time_label()
        
        # Trigger OpenGL update
        self.gl_widget.update()
    
    def on_live_toggled(self, state):
        self.is_live_mode = state == Qt.Checked
        
        if self.is_live_mode:
            self.switch_to_live()
        else:
            self.switch_to_replay()
    
    def toggle_play(self):
        if not self.is_live_mode:
            self.is_playing = not self.is_playing
            self.play_button.setText("▶" if not self.is_playing else "⏸")
    
    def on_speed_changed(self, index):
        speeds = [0.5, 1.0, 2.0, 4.0]
        self.playback_speed = speeds[index]
    
    def on_timeline_changed(self, value):
        if not self.replay_events or self.is_live_mode:
            return
        
        # Calculate time from slider
        time_range = self.replay_max_time - self.replay_min_time
        if time_range > 0:
            self.current_time_ns = self.replay_min_time + int(time_range * value / 1000)
            self.update_states_from_replay()
            self.update_time_label()
    
    def closeEvent(self, event):
        """Cleanup on close"""
        self._stop_tail_worker()
        event.accept()

# ===== MAIN APPLICATION =====
def main():
    # Print debug info at startup
    print("Running ST4 Calibration & Testing visualizer from:", os.path.abspath(__file__))
    print("CWD:", os.path.abspath(os.getcwd()))
    print("PROJECT_ROOT:", PROJECT_ROOT)
    print("SEARCH_ROOTS:", SEARCH_ROOTS)
    
    # Set OpenGL compatibility profile
    fmt = QSurfaceFormat()
    fmt.setVersion(OPENGL_MAJOR_VERSION, OPENGL_MINOR_VERSION)
    fmt.setProfile(QSurfaceFormat.CompatibilityProfile)
    fmt.setDepthBufferSize(24)
    fmt.setSamples(4)  # 4x MSAA
    QSurfaceFormat.setDefaultFormat(fmt)
    
    app = QApplication(sys.argv)
    
    # Check for OpenGL
    if USE_PYOPENGL and not PYOPENGL_AVAILABLE:
        reply = QMessageBox.warning(
            None,
            "OpenGL Warning",
            "PyOpenGL not installed. Visualization will be basic.\n"
            "Install with: pip install PyOpenGL\n\n"
            "Continue anyway?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.No:
            sys.exit(1)
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()