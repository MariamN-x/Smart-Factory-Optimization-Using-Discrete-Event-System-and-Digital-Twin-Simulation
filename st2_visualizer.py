#!/usr/bin/env python3
"""
ST2 Visualizer - Standalone Qt + OpenGL visualization for ST2 Frame/Core Assembly logs
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
PRIORITY_EXTRA_KEYS = ['completed', 'scrapped', 'reworks', 'cycle_time_avg_s', 'batch_id', 'recipe_id']

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
class ST2Event:
    t_ns: int
    ready: bool = False
    busy: bool = False
    done: bool = False
    fault: bool = False
    cycle_time_ms: Optional[float] = None
    extra: Dict[str, Any] = field(default_factory=dict)

# ===== CARRY-FORWARD PARSER =====
class ST2LogParser:
    """Carry-forward parser for ST2 Frame/Core Assembly logs"""
    
    def __init__(self):
        self.reset()
    
    def reset(self):
        """Reset parser state"""
        self.last_vsi_time_ns = None
        self.synthetic_time_ns = 0
        self.last_emitted_ts = None
    
        # NEW: track SimPy time to advance timestamps by delta
        self.last_simpy_s = None
        self.seen_vsi_time = False  # have we ever seen a real VSI time line?
    
        # Carry-forward state
        self.carried_state = {'ready': None, 'busy': None, 'done': None, 'fault': None}
        self.carried_cycle_time = None
        self.carried_extra = {}
        self.has_seen_any_state = False
        self.in_outputs_section = False
    
        # Patterns
        self.vsi_time_pattern = re.compile(r"VSI\s+time\s*:\s*(\d+)\s*ns", re.IGNORECASE)
        self.simpy_now_pattern = re.compile(r"SimPy\s+env\.now\s*=\s*([\d.]+)\s*s", re.IGNORECASE)
    
        self.time_advanced_pattern = re.compile(
            r"Time\s+advanced\s+[\d.]+s\s*->\s*([\d.]+)s", re.IGNORECASE
        )
    
        self.bool_state_inline = {
            "busy": re.compile(r"\bbusy\s*=\s*(True|False)\b", re.IGNORECASE),
            "ready": re.compile(r"\bready\s*=\s*(True|False)\b", re.IGNORECASE),
            "done": re.compile(r"\bdone\s*=\s*(True|False)\b", re.IGNORECASE),
            "fault": re.compile(r"\bfault\s*=\s*(True|False)\b", re.IGNORECASE),
        }
    
        self.cycle_time_patterns = [
            re.compile(r"cycle[_\s]*time[_\s]*[:=]?\s*([\d.]+)\s*ms", re.IGNORECASE),
            re.compile(r"cycle_time_ms[_\s]*[:=]?\s*([\d.]+)", re.IGNORECASE),
        ]
    
        self.key_value_pattern = re.compile(r"(\w+)[_\s]*[:=]\s*([\w.-]+)")
    
        self.state_patterns = {
            "ready": re.compile(r"\bready\b\s*[:=]\s*(\d+)", re.IGNORECASE),
            "busy": re.compile(r"\bbusy\b\s*[:=]\s*(\d+)", re.IGNORECASE),
            "done": re.compile(r"\bdone\b\s*[:=]\s*(\d+)", re.IGNORECASE),
            "fault": re.compile(r"\bfault\b\s*[:=]\s*(\d+)", re.IGNORECASE),
        }
    
        self.block_start_pattern = re.compile(r"^\+=.*ST2_FrameCoreAssembly.*=\+$", re.IGNORECASE)
        self.block_end_pattern = re.compile(r"^=\+=$")
        self.outputs_section_pattern = re.compile(r"^\s*Outputs:", re.IGNORECASE)
        self.inputs_section_pattern = re.compile(r"^\s*Inputs:", re.IGNORECASE)
        self.decode_section_pattern = re.compile(r"^\s*decode:", re.IGNORECASE)
    
    
    def seed_from_event(self, event: ST2Event):
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
    
    def parse_line(self, line: str) -> Optional[ST2Event]:
        line = line.strip()
        if not line:
            return None
    
        had_signal = False
    
        # section tracking
        if self.block_start_pattern.match(line):
            self.in_outputs_section = False
        elif self.block_end_pattern.match(line):
            self.in_outputs_section = False
        elif self.outputs_section_pattern.match(line):
            self.in_outputs_section = True
        elif self.inputs_section_pattern.match(line) or self.decode_section_pattern.match(line):
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
    
        # ---- SimPy env.now ----
        simpy_match = self.simpy_now_pattern.search(line)
        if simpy_match:
            try:
                apply_simpy_time(float(simpy_match.group(1)))
            except ValueError:
                pass
    
        # ---- Time advanced ----
        time_advanced_match = self.time_advanced_pattern.search(line)
        if time_advanced_match:
            try:
                apply_simpy_time(float(time_advanced_match.group(1)))
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
    
        # ---- inline bool state updates (REMOVE baseline gate) ----
        for state_name, pattern in self.bool_state_inline.items():
            match = pattern.search(line)
            if match:
                bool_str = match.group(1).lower()
                value = 1 if bool_str == 'true' else 0
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
    
        # ---- extras only in Outputs section ----
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
        
                # ✅ NEW: if the log gives SimPy time as a key-value, advance timestamp from it
                if k in ("simpy_now_s", "simpy_now", "simpy_s"):
                    try:
                        apply_simpy_time(float(v))
                    except Exception:
                        pass
                    current_line_extras[key] = v
                    continue
        
                # existing filters
                if k in ['vsi', 'time', 'cycle', 'ready', 'busy', 'done', 'fault', 'simpy']:
                    continue
                if 'cycle_time' in k:
                    continue
                if not self._is_valid_extra_key(key):
                    continue
        
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
    
        return ST2Event(
            t_ns=timestamp,
            ready=ready,
            busy=busy,
            done=done,
            fault=fault,
            cycle_time_ms=self.carried_cycle_time,
            extra=copy.deepcopy(self.carried_extra)
        )
    
    
    def get_current_state(self) -> Optional[ST2Event]:
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
        
        return ST2Event(
            t_ns=timestamp,
            ready=ready,
            busy=busy,
            done=done,
            fault=fault,
            cycle_time_ms=self.carried_cycle_time,
            extra=copy.deepcopy(self.carried_extra)
        )

# ===== LOG DISCOVERY =====
class LogDiscoverer:
    @staticmethod
    def find_st2_log() -> Optional[str]:
        candidates = []

        def consider_file(full_path: str, file: str):
            fl = file.lower()

            # accept .log only (keep strict)
            if not fl.endswith(".log"):
                return

            # must be ST2-ish AND assembly-ish
            st2_ok = ("st2" in fl) or ("station2" in fl) or ("station_2" in fl) or ("station 2" in fl)
            asm_ok = ("framecoreassembly" in fl) or ("frame" in fl) or ("core" in fl) or ("assembly" in fl)
            if not (st2_ok and asm_ok):
                return

            try:
                mtime = os.path.getmtime(full_path)
                size = os.path.getsize(full_path)
            except OSError:
                return

            score = 0
            if "framecoreassembly" in fl:
                score += 10
            if "assembly" in fl:
                score += 6
            if "frame" in fl:
                score += 3
            if "core" in fl:
                score += 3

            base = os.path.basename(full_path).lower()
            is_check = base.startswith("check") or base.startswith("check_") or base.startswith("check.")

            candidates.append((is_check, score, size, mtime, full_path))

        # scan all roots
        for root0 in SEARCH_ROOTS:
            for root, dirs, files in os.walk(root0):
                for file in files:
                    consider_file(os.path.join(root, file), file)

        # DEBUG: print what we found
        print(f"[LogDiscoverer] candidates total = {len(candidates)}")
        non_check = [c for c in candidates if not c[0]]
        print(f"[LogDiscoverer] non-check candidates = {len(non_check)}")

        # show top 15 by (score,size,mtime) for visibility
        preview = sorted(candidates, key=lambda x: (-x[1], -x[2], -x[3]))[:15]
        for i, (is_check, score, size, mtime, path) in enumerate(preview, 1):
            tag = "CHECK" if is_check else "REAL"
            print(f"  {i:02d}) {tag} score={score} size={size} mtime={datetime.fromtimestamp(mtime)} path={path}")

        if not candidates:
            return None

        pool = non_check if non_check else candidates
        pool.sort(key=lambda x: (-x[1], -x[2], -x[3]))
        selected = pool[0][4]
        print("Selected ST2 log:", selected)
        return selected

# ===== TAIL WORKER =====
class LogTailWorker(QThread):
    new_event = Signal(ST2Event)  # Emitted for every signal line
    activity_detected = Signal()  # Emitted when any line is read
    file_reopened = Signal()  # Emitted when file is reopened (rotation/truncation)
    
    def __init__(self, log_path: str, seed_event: Optional[ST2Event] = None):
        super().__init__()
        self.log_path = log_path
        self.seed_event = seed_event
        self.running = True
        self.file_handle = None
        self.file_position = 0
        self.file_inode = None
        self.parser = ST2LogParser()
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
            self.parser = ST2LogParser()
            
            # Seed the parser if we have a seed event
            if seed and self.seed_event:
                self.parser.seed_from_event(self.seed_event)
                
        except Exception as e:
            print(f"Error opening {self.log_path}: {e}")
            self.file_handle = None
    
    def reseed_parser(self, seed_event: ST2Event):
        """Reseed the parser with a new event (e.g., after file rotation)"""
        self.seed_event = seed_event
        self.parser.seed_from_event(seed_event)
    
    def stop(self):
        """Stop the worker thread"""
        self.running = False
        if self.isRunning():
            self.wait()

# ===== OPENGL WIDGET =====
class ST2OpenGLWidget(QOpenGLWidget):
    """OpenGL visualization widget for ST2 Frame/Core Assembly"""
    
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
        self.animation_time = 0
        self.done_pulse_time = 0
        self.shake_offset = 0.0
        self.last_completed_count = 0
        self.last_scrapped_count = 0
        self.last_reworks_count = 0
        self.scrap_increased = False
        self.rework_increased = False
        self.scrap_animation_start = 0
        self.rework_animation_start = 0
        
        # Stage animation parameters
        self.base_stage_s = 2.5  # Default seconds per stage
        self.stage_s = self.base_stage_s  # Current stage duration
        self.proc_active = False  # Is processing active?
        self.proc_start = 0.0  # When processing started
        self.proc_end = 0.0  # When processing ends (4 stages)
        self.active_stage_index = -1  # -1 means no stage active
        self.stage_progress = 0.0  # Progress within current stage (0.0 to 1.0)
        self.stage_names = ["Frame Load", "Core Insert", "Fasten", "Verify"]
        self.mode = "LIVE"  # Will be updated by MainWindow
        
        # Animation states
        self.clamp_open = True
        self.core_tool_height = 3.0
        self.screwdriver_pos = [-1.2, 2.5, 0.8]
        self.screwdriver_active = False
        self.screwdriver_angle = 0
        self.current_screw_index = 0
        self.scanner_pos = -1.5
        self.part_pos = [0, 0.5, 0]  # Normal position
        self.scrap_bin_pos = [-4.0, 0.5, 0]  # Left side
        self.rework_loop_pos = [4.0, 1.0, 0]  # Right side
        
        # Colors
        self.colors = {
            "conveyor": (0.2, 0.2, 0.25, 1.0),
            "fixture_base": (0.3, 0.3, 0.35, 1.0),
            "fixture_nest": (0.25, 0.25, 0.3, 1.0),
            "fixture_clamp": (0.4, 0.4, 0.45, 1.0),
            "frame": (0.6, 0.6, 0.7, 1.0),
            "core": (0.8, 0.5, 0.3, 1.0),
            "fastener": (0.7, 0.7, 0.2, 1.0),
            "screw_head": (0.9, 0.9, 0.3, 1.0),
            "core_tool": (0.5, 0.5, 0.6, 1.0),
            "screwdriver": (0.1, 0.1, 0.1, 1.0),
            "scanner": (0.0, 0.8, 1.0, 1.0),
            "pass_green": (0.0, 0.8, 0.0, 1.0),
            "fail_red": (0.8, 0.0, 0.0, 1.0),
            "scrap_bin": (0.8, 0.0, 0.0, 0.8),
            "rework_loop": (0.8, 0.8, 0.0, 0.8),
            "fault": (1.0, 0.0, 0.0, 1.0),
            "done_pulse": (0.0, 1.0, 1.0, 1.0),
            "grid": (0.3, 0.3, 0.3, 1.0),
        }
        
        # Camera
        self.camera_distance = 15.0
        self.camera_angle_x = 25.0
        self.camera_angle_y = 35.0
        self.last_mouse_pos = None
        
        self.setMouseTracking(True)
    
    def compute_active_stage(self, now=None):
        """Compute which stage is active and its progress"""
        now = now or time.time()
        if not self.proc_active:
            self.active_stage_index = -1
            self.stage_progress = 0.0
            return -1
        
        # If busy but timer ended, stay in stage 4 (Verify) with looping animation
        if now >= self.proc_end:
            if self.current_state['busy']:
                # Stay in stage 4 with looping scanner
                self.active_stage_index = 3
                # Loop scanner every 2 seconds
                self.stage_progress = (math.sin(now * math.pi) * 0.5 + 0.5) * 0.7  # 0.0 to 0.7 range
                self.scanner_pos = -1.5 + 3.0 * ((now % 2.0) / 2.0)
                return 3
            else:
                self.proc_active = False
                self.active_stage_index = -1
                self.stage_progress = 0.0
                return -1
        
        elapsed = now - self.proc_start
        self.active_stage_index = min(3, int(elapsed / self.stage_s))
        
        # Calculate progress within current stage
        stage_start_time = self.proc_start + self.active_stage_index * self.stage_s
        self.stage_progress = min(1.0, max(0.0, (now - stage_start_time) / self.stage_s))
        
        # Update animations based on stage
        self.update_animations(now)
        
        return self.active_stage_index
    
    def update_animations(self, now):
        """Update animation states based on current stage and progress"""
        # Stage 0: Frame Load
        if self.active_stage_index == 0:
            # Open clamps during frame load
            self.clamp_open = True
            # Frame slides in from left conveyor
            t = min(1.0, self.stage_progress * 1.5)
            self.part_pos[0] = -4.0 + 4.0 * t
            
            # Close clamps at end of stage
            if self.stage_progress > 0.8:
                self.clamp_open = False
                
        # Stage 1: Core Insert
        elif self.active_stage_index == 1:
            self.clamp_open = False
            # Core tool lowers
            if self.stage_progress < 0.7:
                self.core_tool_height = 3.0 - 2.5 * (self.stage_progress / 0.7)
            else:
                # Retract after insertion
                self.core_tool_height = 0.5 + 2.5 * ((self.stage_progress - 0.7) / 0.3)
                
        # Stage 2: Fasten
        elif self.active_stage_index == 2:
            self.screwdriver_active = True
            # Move screwdriver to 4 positions sequentially
            screw_positions = [
                (-1.2, 1.0, 0.8),
                (1.2, 1.0, 0.8),
                (-1.2, 1.0, -0.8),
                (1.2, 1.0, -0.8)
            ]
            
            # Determine current screw position
            self.current_screw_index = min(3, int(self.stage_progress * 4))
            target_pos = screw_positions[self.current_screw_index]
            
            # Animate movement
            move_progress = (self.stage_progress * 4) - self.current_screw_index
            if move_progress < 0.5:
                # Move to position
                t = move_progress * 2
                self.screwdriver_pos[0] = -1.2 + (target_pos[0] + 1.2) * t
                self.screwdriver_pos[2] = 0.8 + (target_pos[2] - 0.8) * t
                self.screwdriver_angle = 0
            else:
                # Spin at position
                self.screwdriver_pos = list(target_pos)
                self.screwdriver_angle = (move_progress - 0.5) * 720 * 2  # 2 full rotations
                
        # Stage 3: Verify
        elif self.active_stage_index == 3:
            self.screwdriver_active = False
            self.screwdriver_pos = [-1.2, 2.5, 0.8]  # Return to home
            # Scanner sweeps across
            self.scanner_pos = -1.5 + 3.0 * self.stage_progress
            
        # Handle scrap/rework animations
        current_time = time.time()
        if self.scrap_increased and current_time - self.scrap_animation_start < 2.0:
            # Animate part moving to scrap bin
            anim_progress = min(1.0, (current_time - self.scrap_animation_start) / 2.0)
            self.part_pos[0] = -4.0 * anim_progress
            self.part_pos[1] = 0.5 - 0.3 * math.sin(anim_progress * math.pi)
            
        elif self.rework_increased and current_time - self.rework_animation_start < 2.0:
            # Pulse part with yellow color
            anim_progress = (current_time - self.rework_animation_start) / 2.0
            pulse = 0.5 + 0.5 * math.sin(anim_progress * 4 * math.pi)
            # Part position wobbles slightly
            self.part_pos[0] = 0.1 * math.sin(anim_progress * 2 * math.pi)
            
    def initializeGL(self):
        if PYOPENGL_AVAILABLE:
            glEnable(GL_DEPTH_TEST)
            glEnable(GL_LIGHTING)
            glEnable(GL_LIGHT0)
            glEnable(GL_COLOR_MATERIAL)
            glColorMaterial(GL_FRONT_AND_BACK, GL_AMBIENT_AND_DIFFUSE)
            
            # Enable blending for transparency
            glEnable(GL_BLEND)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            
            # Setup light
            glLightfv(GL_LIGHT0, GL_POSITION, [5.0, 10.0, 5.0, 1.0])
            glLightfv(GL_LIGHT0, GL_AMBIENT, [0.2, 0.2, 0.2, 1.0])
            glLightfv(GL_LIGHT0, GL_DIFFUSE, [0.8, 0.8, 0.8, 1.0])
    
    def resizeGL(self, w, h):
        if PYOPENGL_AVAILABLE:
            glViewport(0, 0, w, h)
            glMatrixMode(GL_PROJECTION)
            glLoadIdentity()
            aspect = w / h if h > 0 else 1.0
            glOrtho(-8 * aspect, 8 * aspect, -8, 8, -50, 50)
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
        
        # Compute active stage
        current_time = time.time()
        self.compute_active_stage(current_time)
        
        # Draw assembly cell
        self.draw_conveyors_basic(painter, center_x, center_y)
        self.draw_fixture_basic(painter, center_x, center_y)
        self.draw_clamps_basic(painter, center_x, center_y)
        self.draw_core_tool_basic(painter, center_x, center_y)
        self.draw_screwdriver_basic(painter, center_x, center_y)
        self.draw_scanner_basic(painter, center_x, center_y)
        self.draw_frame_basic(painter, center_x, center_y)
        self.draw_core_basic(painter, center_x, center_y)
        self.draw_fasteners_basic(painter, center_x, center_y)
        self.draw_scrap_bin_basic(painter, center_x, center_y)
        self.draw_rework_loop_basic(painter, center_x, center_y)
        
        # Draw fault overlay if needed
        if self.current_state['fault']:
            self.draw_fault_overlay_basic(painter, center_x, center_y)
        
        # Draw done pulse if active
        if current_time - self.done_pulse_time < DONE_PULSE_MS / 1000.0:
            self.draw_done_pulse_basic(painter, center_x, center_y)
        
        # Draw overlay
        self.draw_overlay(painter)
    
    def draw_conveyors_basic(self, painter, center_x, center_y):
        """Draw IN and OUT conveyors"""
        # IN conveyor (left)
        painter.setBrush(QColor(51, 51, 64))
        painter.setPen(QPen(QColor(100, 100, 120), 2))
        painter.drawRect(center_x - 300, center_y + 50, 200, 30)
        
        # OUT conveyor (right)
        painter.drawRect(center_x + 100, center_y + 50, 200, 30)
        
        # Conveyor belts (moving texture effect)
        current_time = time.time()
        offset = int(current_time * 20) % 20
        
        # Draw moving lines on conveyors
        painter.setPen(QPen(QColor(150, 150, 180), 1))
        for i in range(-10, 11):
            # IN conveyor lines
            x = center_x - 300 + (i * 20 + offset) % 200
            painter.drawLine(x, center_y + 50, x, center_y + 80)
            
            # OUT conveyor lines
            x = center_x + 100 + (i * 20 + offset) % 200
            painter.drawLine(x, center_y + 50, x, center_y + 80)
    
    def draw_fixture_basic(self, painter, center_x, center_y):
        """Draw assembly fixture with nest"""
        # Base plate
        painter.setBrush(QColor(77, 77, 89))
        painter.setPen(QPen(QColor(100, 100, 120), 2))
        painter.drawRect(center_x - 100, center_y - 80, 200, 160)
        
        # Nest (indentation for frame)
        painter.setBrush(QColor(64, 64, 77))
        painter.drawRect(center_x - 80, center_y - 60, 160, 120)
    
    def draw_clamps_basic(self, painter, center_x, center_y):
        """Draw moving clamps"""
        clamp_size = 20
        clamp_offset = 0 if self.clamp_open else 15
        
        painter.setBrush(QColor(102, 102, 115))
        painter.setPen(QPen(QColor(120, 120, 140), 2))
        
        # Left clamp
        painter.drawRect(center_x - 120 + clamp_offset, center_y - 70, clamp_size, clamp_size)
        painter.drawRect(center_x - 120 + clamp_offset, center_y + 50, clamp_size, clamp_size)
        
        # Right clamp
        painter.drawRect(center_x + 100 - clamp_offset, center_y - 70, clamp_size, clamp_size)
        painter.drawRect(center_x + 100 - clamp_offset, center_y + 50, clamp_size, clamp_size)
    
    def draw_core_tool_basic(self, painter, center_x, center_y):
        """Draw core insertion tool"""
        if self.active_stage_index >= 1:
            # Calculate tool height
            tool_height = 150 - int(self.core_tool_height * 40)
            painter.setBrush(QColor(128, 128, 153))
            painter.setPen(QPen(QColor(150, 150, 180), 2))
            painter.drawRect(center_x - 15, center_y - 200 + tool_height, 30, 40)
            
            # Tool head
            painter.setBrush(QColor(100, 100, 120))
            painter.drawEllipse(center_x - 20, center_y - 200 + tool_height + 35, 40, 10)
    
    def draw_screwdriver_basic(self, painter, center_x, center_y):
        """Draw screwdriver tool"""
        if self.active_stage_index >= 2 and self.screwdriver_active:
            # Calculate position based on animation
            pos_x = center_x + int(self.screwdriver_pos[0] * 50)
            pos_y = center_y - 150 + int(self.screwdriver_pos[1] * 30)
            
            painter.save()
            painter.translate(pos_x, pos_y)
            
            # Rotate screwdriver
            painter.rotate(self.screwdriver_angle)
            
            # Screwdriver body
            painter.setBrush(QColor(25, 25, 30))
            painter.setPen(QPen(QColor(50, 50, 60), 2))
            painter.drawRect(-8, -20, 16, 40)
            
            # Screwdriver tip
            painter.setBrush(QColor(200, 200, 100))
            painter.drawRect(-5, -25, 10, 10)
            
            painter.restore()
    
    def draw_scanner_basic(self, painter, center_x, center_y):
        """Draw scanner gantry"""
        if self.active_stage_index >= 3:
            # Scanner gantry
            scanner_x = center_x + int(self.scanner_pos * 50)
            
            # Gantry beams
            painter.setBrush(QColor(0, 100, 150))
            painter.setPen(QPen(QColor(0, 150, 200), 2))
            painter.drawRect(scanner_x - 5, center_y - 150, 10, 300)  # Vertical beam
            painter.drawRect(center_x - 150, center_y - 150, 300, 10)  # Top horizontal
            
            # Scanner head
            painter.setBrush(QColor(0, 200, 255))
            painter.drawEllipse(scanner_x - 15, center_y - 15, 30, 30)
            
            # Laser beam
            painter.setPen(QPen(QColor(0, 255, 255, 150), 3))
            painter.drawLine(scanner_x, center_y - 100, scanner_x, center_y + 100)
            
            # Draw verification result at the end of stage 4
            if self.active_stage_index == 3 and self.stage_progress > 0.95:
                if self.scrap_increased:
                    # FAIL - red overlay
                    painter.setBrush(QColor(255, 0, 0, 100))
                    painter.setPen(QPen(QColor(255, 0, 0), 2))
                    painter.drawRect(center_x - 60, center_y - 40, 120, 80)
                    painter.setPen(QPen(QColor(255, 255, 255), 2, Qt.DashLine))
                    painter.drawText(center_x - 20, center_y, "FAIL")
                else:
                    # PASS - green overlay
                    painter.setBrush(QColor(0, 255, 0, 100))
                    painter.setPen(QPen(QColor(0, 255, 0), 2))
                    painter.drawRect(center_x - 60, center_y - 40, 120, 80)
                    painter.setPen(QPen(QColor(255, 255, 255), 2, Qt.DashLine))
                    painter.drawText(center_x - 20, center_y, "PASS")
    
    def draw_frame_basic(self, painter, center_x, center_y):
        """Draw frame with animation"""
        if self.active_stage_index < 0 and not (self.scrap_increased or self.rework_increased):
            return
        
        frame_width = 100
        frame_height = 80
        frame_thickness = 8
        
        # Calculate position based on animation
        if self.scrap_increased:
            # Moving to scrap bin
            current_time = time.time()
            anim_progress = min(1.0, (current_time - self.scrap_animation_start) / 2.0)
            pos_x = center_x - 200 * anim_progress
            pos_y = center_y - 20 * math.sin(anim_progress * math.pi)
            alpha = int(255 * (1.0 - anim_progress))
        elif self.rework_increased:
            # Wobble in rework loop
            current_time = time.time()
            anim_progress = (current_time - self.rework_animation_start) / 2.0
            pos_x = center_x + 10 * math.sin(anim_progress * 2 * math.pi)
            pos_y = center_y
            alpha = 255
        else:
            # Normal position
            pos_x = center_x
            pos_y = center_y
            alpha = 255
        
        # Draw frame (hollow rectangle)
        painter.setPen(QPen(QColor(153, 153, 179, alpha), frame_thickness))
        painter.setBrush(Qt.NoBrush)
        frame_rect = QRect(pos_x - frame_width//2, 
                          pos_y - frame_height//2, 
                          frame_width, frame_height)
        painter.drawRect(frame_rect)
        
        # Draw frame label
        painter.setPen(QPen(QColor(200, 200, 200, alpha), 1))
        painter.drawText(frame_rect.center().x() - 20, frame_rect.center().y(), "FRAME")
    
    def draw_core_basic(self, painter, center_x, center_y):
        """Draw core insert"""
        if self.active_stage_index < 1:
            return
        
        core_width = 60
        core_height = 50
        
        # Calculate position (same as frame but offset for scrap/rework)
        if self.scrap_increased:
            current_time = time.time()
            anim_progress = min(1.0, (current_time - self.scrap_animation_start) / 2.0)
            pos_x = center_x - 200 * anim_progress
            pos_y = center_y - 20 * math.sin(anim_progress * math.pi)
            alpha = int(255 * (1.0 - anim_progress))
        elif self.rework_increased:
            current_time = time.time()
            anim_progress = (current_time - self.rework_animation_start) / 2.0
            pos_x = center_x + 10 * math.sin(anim_progress * 2 * math.pi)
            pos_y = center_y
            alpha = 255
        else:
            pos_x = center_x
            pos_y = center_y
            alpha = 255
        
        # Draw core (solid block)
        painter.setBrush(QColor(204, 128, 77, alpha))
        painter.setPen(QPen(QColor(230, 150, 100, alpha), 2))
        core_rect = QRect(pos_x - core_width//2, 
                         pos_y - core_height//2, 
                         core_width, core_height)
        painter.drawRect(core_rect)
        
        # Draw core label
        painter.setPen(QPen(QColor(255, 255, 255, alpha), 1))
        painter.drawText(core_rect.center().x() - 15, core_rect.center().y(), "CORE")
    
    def draw_fasteners_basic(self, painter, center_x, center_y):
        """Draw fasteners"""
        if self.active_stage_index < 2:
            return
        
        fastener_radius = 6
        screw_positions = [
            (center_x - 45, center_y - 35),  # Top-left
            (center_x + 40, center_y - 35),  # Top-right
            (center_x - 45, center_y + 30),  # Bottom-left
            (center_x + 40, center_y + 30)   # Bottom-right
        ]
        
        # Draw screws that have been installed
        for i in range(4):
            if self.active_stage_index > 2 or (self.active_stage_index == 2 and self.stage_progress > (i + 0.5) / 4):
                pos = screw_positions[i]
                
                # Adjust position for scrap/rework animation
                if self.scrap_increased:
                    current_time = time.time()
                    anim_progress = min(1.0, (current_time - self.scrap_animation_start) / 2.0)
                    pos_x = pos[0] - 200 * anim_progress
                    pos_y = pos[1] - 20 * math.sin(anim_progress * math.pi)
                    alpha = int(255 * (1.0 - anim_progress))
                elif self.rework_increased:
                    current_time = time.time()
                    anim_progress = (current_time - self.rework_animation_start) / 2.0
                    pos_x = pos[0] + 10 * math.sin(anim_progress * 2 * math.pi)
                    pos_y = pos[1]
                    alpha = 255
                else:
                    pos_x = pos[0]
                    pos_y = pos[1]
                    alpha = 255
                
                # Draw screw head
                painter.setBrush(QColor(179, 179, 51, alpha))
                painter.setPen(QPen(QColor(200, 200, 60, alpha), 2))
                painter.drawEllipse(pos_x - fastener_radius, 
                                  pos_y - fastener_radius,
                                  fastener_radius * 2, fastener_radius * 2)
    
    def draw_scrap_bin_basic(self, painter, center_x, center_y):
        """Draw scrap bin"""
        if self.scrap_increased:
            current_time = time.time()
            anim_progress = min(1.0, (current_time - self.scrap_animation_start) / 2.0)
            alpha = int(200 * anim_progress)
            
            painter.setBrush(QColor(200, 0, 0, alpha))
            painter.setPen(QPen(QColor(255, 100, 100, alpha), 2))
            painter.drawRect(center_x - 300, center_y - 40, 60, 80)
            painter.setPen(QPen(QColor(255, 255, 255, alpha), 1))
            painter.drawText(center_x - 290, center_y, "SCRAP")
    
    def draw_rework_loop_basic(self, painter, center_x, center_y):
        """Draw rework loop"""
        if self.rework_increased:
            current_time = time.time()
            anim_progress = (current_time - self.rework_animation_start) / 2.0
            alpha = int(200 * (0.5 + 0.5 * math.sin(anim_progress * 4 * math.pi)))
            
            painter.setBrush(QColor(200, 200, 0, alpha))
            painter.setPen(QPen(QColor(255, 255, 100, alpha), 2))
            
            # Draw circular arrow
            arrow_rect = QRect(center_x + 250, center_y - 50, 100, 100)
            painter.drawArc(arrow_rect, 45 * 16, 270 * 16)
            
            # Arrow head
            painter.drawLine(center_x + 300, center_y + 40, center_x + 320, center_y + 20)
            painter.drawLine(center_x + 300, center_y + 40, center_x + 280, center_y + 20)
            
            painter.setPen(QPen(QColor(255, 255, 255, alpha), 1))
            painter.drawText(center_x + 260, center_y + 80, "REWORK")
    
    def draw_fault_overlay_basic(self, painter, center_x, center_y):
        """Draw fault overlay"""
        # Red border
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(255, 0, 0), 4))
        painter.drawRect(center_x - 100, center_y - 80, 200, 160)
        
        # Fault text
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        painter.setBrush(QColor(255, 0, 0, 200))
        painter.drawRect(center_x - 40, center_y - 15, 80, 30)
        painter.drawText(center_x - 30, center_y + 5, "FAULT")
    
    def draw_done_pulse_basic(self, painter, center_x, center_y):
        """Draw done pulse effect"""
        current_time = time.time()
        pulse_progress = (current_time - self.done_pulse_time) / (DONE_PULSE_MS / 1000.0)
        pulse_alpha = int(255 * (1.0 - pulse_progress))
        pulse_scale = 1.0 + pulse_progress * 0.5
        
        painter.setBrush(QColor(0, 255, 255, pulse_alpha // 3))
        painter.setPen(QPen(QColor(0, 255, 255, pulse_alpha), 2))
        
        # Pulse around assembled part
        pulse_width = int(120 * pulse_scale)
        pulse_height = int(100 * pulse_scale)
        painter.drawRect(center_x - pulse_width//2, center_y - pulse_height//2,
                        pulse_width, pulse_height)
    
    def draw_overlay(self, painter):
        """Draw overlay text with system information"""
        # Setup font
        font = QFont("Monospace", 10)
        painter.setFont(font)
        painter.setPen(QPen(QColor(255, 255, 255), 1))
        
        # Mode and state
        mode_text = f"Mode: {self.mode}"
        state_text = f"State: R={int(self.current_state['ready'])} B={int(self.current_state['busy'])} D={int(self.current_state['done'])} F={int(self.current_state['fault'])}"
        
        # Batch/Recipe info
        batch_id = self.current_state['extra'].get('batch_id', 'N/A')
        recipe_id = self.current_state['extra'].get('recipe_id', 'N/A')
        
        # Use SimPy time if available, otherwise use current time
        simpy_now = self.current_state['extra'].get('simpy_now_s')
        if simpy_now is not None:
            now_text = f"SimPy: {simpy_now:.1f}s"
        else:
            now_text = f"Now: {datetime.now().strftime('%H:%M:%S')}"
            
        info_text = f"Batch: {batch_id} | Recipe: {recipe_id} | {now_text}"
        
        # Active stage
        active_stage = "Idle"
        stage_color = QColor(200, 200, 200)
        if self.active_stage_index >= 0 and self.active_stage_index < 4:
            active_stage = self.stage_names[self.active_stage_index]
            stage_color = QColor(255, 215, 0)  # Gold for active
            
            # Add verification result
            if self.active_stage_index == 3 and self.stage_progress > 0.95:
                if self.scrap_increased:
                    active_stage += " (FAIL)"
                    stage_color = QColor(255, 0, 0)
                else:
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
            
            # Draw progress
            progress_width = int(progress_bar_width * self.stage_progress)
            painter.fillRect(progress_bar_x, progress_bar_y, 
                           progress_width, progress_bar_height, 
                           stage_color)
            
            # Draw border
            painter.setPen(QPen(QColor(100, 100, 100), 1))
            painter.drawRect(progress_bar_x, progress_bar_y, 
                           progress_bar_width, progress_bar_height)
            
            # Draw percentage text
            painter.setPen(QPen(QColor(255, 255, 255), 1))
            percent_text = f"{self.stage_progress*100:.0f}%"
            painter.drawText(progress_bar_x + progress_bar_width + 10, 
                           progress_bar_y + progress_bar_height - 2, 
                           percent_text)
        
        # Counters
        completed = self.current_state['extra'].get('completed', 'N/A')
        scrapped = self.current_state['extra'].get('scrapped', 'N/A')
        reworks = self.current_state['extra'].get('reworks', 'N/A')
        counters_text = f"Completed: {completed} | Scrapped: {scrapped} | Reworks: {reworks}"
        
        # Cycle time and stage duration
        cycle_text = f"Cycle: {self.current_state['cycle_time_ms'] or 'N/A'} ms"
        stage_dur_text = f"Stage Dur: {self.stage_s:.1f}s"
        
        # Draw text with background for readability
        y_offset = 20
        line_height = 20
        
        texts = [mode_text, state_text, info_text, stage_text, counters_text, cycle_text, stage_dur_text]
        
        for i, text in enumerate(texts):
            # Draw background rectangle
            metrics = QFontMetrics(font)
            text_width = metrics.horizontalAdvance(text)
            painter.fillRect(10, y_offset + i * line_height - 15, text_width + 10, line_height, QColor(0, 0, 0, 180))
            # Draw text
            painter.drawText(15, y_offset + i * line_height, text)
    
    def draw_scene(self):
        """Draw the 3D scene with assembly cell"""
        # Draw floor grid
        self.draw_grid()
        
        # Apply shake effect if fault
        if self.current_state['fault']:
            self.shake_offset = math.sin(time.time() * 10) * 0.1
            glTranslatef(self.shake_offset, 0, 0)
        
        # Compute active stage
        current_time = time.time()
        self.compute_active_stage(current_time)
        
        # Draw assembly cell
        self.draw_conveyors()
        self.draw_fixture()
        self.draw_clamps()
        self.draw_core_tool()
        self.draw_screwdriver()
        self.draw_scanner()
        self.draw_frame()
        self.draw_core()
        self.draw_fasteners()
        self.draw_scrap_bin()
        self.draw_rework_loop()
        
        # Draw fault overlay if needed
        if self.current_state['fault']:
            self.draw_fault_overlay()
        
        # Draw done pulse if active
        if current_time - self.done_pulse_time < DONE_PULSE_MS / 1000.0:
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
    
    def draw_conveyors(self):
        """Draw IN and OUT conveyors"""
        if not PYOPENGL_AVAILABLE:
            return
            
        glColor4f(*self.colors["conveyor"])
        
        # IN conveyor (left)
        glPushMatrix()
        glTranslatef(-5.0, 0.1, 0)
        glScalef(4.0, 0.15, 0.5)
        self.draw_cube()
        glPopMatrix()
        
        # OUT conveyor (right)
        glPushMatrix()
        glTranslatef(5.0, 0.1, 0)
        glScalef(4.0, 0.15, 0.5)
        self.draw_cube()
        glPopMatrix()
    
    def draw_fixture(self):
        """Draw assembly fixture with nest"""
        if not PYOPENGL_AVAILABLE:
            return
            
        # Base plate
        glColor4f(*self.colors["fixture_base"])
        glPushMatrix()
        glTranslatef(0, 0.1, 0)
        glScalef(3.0, 0.2, 2.0)
        self.draw_cube()
        glPopMatrix()
        
        # Nest (indentation for frame)
        glColor4f(*self.colors["fixture_nest"])
        glPushMatrix()
        glTranslatef(0, 0.25, 0)
        glScalef(2.4, 0.3, 1.6)
        self.draw_cube()
        glPopMatrix()
    
    def draw_clamps(self):
        """Draw moving clamps"""
        if not PYOPENGL_AVAILABLE:
            return
            
        glColor4f(*self.colors["fixture_clamp"])
        
        clamp_offset = 0.0 if self.clamp_open else 0.3
        
        # Left clamps
        glPushMatrix()
        glTranslatef(-1.2 + clamp_offset, 0.3, -0.8)
        glScalef(0.2, 0.4, 0.2)
        self.draw_cube()
        glPopMatrix()
        
        glPushMatrix()
        glTranslatef(-1.2 + clamp_offset, 0.3, 0.8)
        glScalef(0.2, 0.4, 0.2)
        self.draw_cube()
        glPopMatrix()
        
        # Right clamps
        glPushMatrix()
        glTranslatef(1.2 - clamp_offset, 0.3, -0.8)
        glScalef(0.2, 0.4, 0.2)
        self.draw_cube()
        glPopMatrix()
        
        glPushMatrix()
        glTranslatef(1.2 - clamp_offset, 0.3, 0.8)
        glScalef(0.2, 0.4, 0.2)
        self.draw_cube()
        glPopMatrix()
    
    def draw_core_tool(self):
        """Draw core insertion tool"""
        if not PYOPENGL_AVAILABLE or self.active_stage_index < 1:
            return
            
        glColor4f(*self.colors["core_tool"])
        
        # Tool column
        glPushMatrix()
        glTranslatef(0, self.core_tool_height, 0)
        glScalef(0.3, 2.0, 0.3)
        self.draw_cylinder()
        glPopMatrix()
        
        # Tool head
        glPushMatrix()
        glTranslatef(0, self.core_tool_height - 1.0, 0)
        glScalef(0.8, 0.2, 0.8)
        self.draw_cube()
        glPopMatrix()
    
    def draw_screwdriver(self):
        """Draw screwdriver tool"""
        if not PYOPENGL_AVAILABLE or not self.screwdriver_active:
            return
            
        glColor4f(*self.colors["screwdriver"])
        
        glPushMatrix()
        glTranslatef(*self.screwdriver_pos)
        glRotatef(self.screwdriver_angle, 0, 1, 0)
        
        # Screwdriver body
        glPushMatrix()
        glScalef(0.1, 0.8, 0.1)
        self.draw_cylinder()
        glPopMatrix()
        
        # Screwdriver tip
        glPushMatrix()
        glTranslatef(0, -0.5, 0)
        glScalef(0.15, 0.3, 0.15)
        self.draw_cone()
        glPopMatrix()
        
        glPopMatrix()
    
    def draw_scanner(self):
        """Draw scanner gantry"""
        if not PYOPENGL_AVAILABLE or self.active_stage_index < 3:
            return
        
        glColor4f(*self.colors["scanner"])
        
        # Gantry beams
        glPushMatrix()
        glTranslatef(0, 2.0, 0)
        glScalef(3.0, 0.1, 0.1)
        self.draw_cube()
        glPopMatrix()
        
        glPushMatrix()
        glTranslatef(self.scanner_pos, 1.5, 0)
        glScalef(0.1, 2.0, 0.1)
        self.draw_cube()
        glPopMatrix()
        
        # Scanner head
        glPushMatrix()
        glTranslatef(self.scanner_pos, 1.5, 0)
        glScalef(0.3, 0.3, 0.3)
        self.draw_cube()
        glPopMatrix()
        
        # Laser beam
        glColor4f(0.0, 1.0, 1.0, 0.3)
        glPushMatrix()
        glTranslatef(self.scanner_pos, 0.8, 0)
        glScalef(0.05, 1.2, 0.05)
        self.draw_cube()
        glPopMatrix()
    
    def draw_frame(self):
        """Draw frame"""
        if not PYOPENGL_AVAILABLE:
            return
        
        glColor4f(*self.colors["frame"])
        
        glPushMatrix()
        glTranslatef(*self.part_pos)
        
        # Frame (hollow - draw as thin walls)
        # Front wall
        glPushMatrix()
        glTranslatef(0, 0.3, 0.5)
        glScalef(1.0, 0.6, 0.05)
        self.draw_cube()
        glPopMatrix()
        
        # Back wall
        glPushMatrix()
        glTranslatef(0, 0.3, -0.5)
        glScalef(1.0, 0.6, 0.05)
        self.draw_cube()
        glPopMatrix()
        
        # Left wall
        glPushMatrix()
        glTranslatef(-0.5, 0.3, 0)
        glScalef(0.05, 0.6, 1.0)
        self.draw_cube()
        glPopMatrix()
        
        # Right wall
        glPushMatrix()
        glTranslatef(0.5, 0.3, 0)
        glScalef(0.05, 0.6, 1.0)
        self.draw_cube()
        glPopMatrix()
        
        glPopMatrix()
    
    def draw_core(self):
        """Draw core insert"""
        if not PYOPENGL_AVAILABLE or self.active_stage_index < 1:
            return
        
        glColor4f(*self.colors["core"])
        
        glPushMatrix()
        glTranslatef(*self.part_pos)
        glTranslatef(0, 0.3, 0)
        glScalef(0.8, 0.4, 0.8)
        self.draw_cube()
        glPopMatrix()
    
    def draw_fasteners(self):
        """Draw fasteners"""
        if not PYOPENGL_AVAILABLE or self.active_stage_index < 2:
            return
        
        screw_positions = [
            (-0.4, 0.7, 0.4),  # Top-left
            (0.4, 0.7, 0.4),   # Top-right
            (-0.4, 0.7, -0.4), # Bottom-left
            (0.4, 0.7, -0.4)   # Bottom-right
        ]
        
        # Draw screws that have been installed
        for i in range(4):
            if self.active_stage_index > 2 or (self.active_stage_index == 2 and self.stage_progress > (i + 0.5) / 4):
                pos = screw_positions[i]
                glColor4f(*self.colors["screw_head"])
                glPushMatrix()
                glTranslatef(self.part_pos[0] + pos[0], 
                           self.part_pos[1] + pos[1] - 0.3, 
                           self.part_pos[2] + pos[2])
                glScalef(0.15, 0.1, 0.15)
                self.draw_cylinder()
                glPopMatrix()
    
    def draw_scrap_bin(self):
        """Draw scrap bin"""
        if not PYOPENGL_AVAILABLE or not self.scrap_increased:
            return
        
        current_time = time.time()
        anim_progress = min(1.0, (current_time - self.scrap_animation_start) / 2.0)
        
        glColor4f(self.colors["scrap_bin"][0],
                 self.colors["scrap_bin"][1],
                 self.colors["scrap_bin"][2],
                 self.colors["scrap_bin"][3] * anim_progress)
        
        glPushMatrix()
        glTranslatef(*self.scrap_bin_pos)
        glScalef(1.0, 1.0, 1.0)
        self.draw_cube()
        glPopMatrix()
    
    def draw_rework_loop(self):
        """Draw rework loop"""
        if not PYOPENGL_AVAILABLE or not self.rework_increased:
            return
        
        current_time = time.time()
        anim_progress = (current_time - self.rework_animation_start) / 2.0
        alpha = 0.5 + 0.5 * math.sin(anim_progress * 4 * math.pi)
        
        glColor4f(self.colors["rework_loop"][0],
                 self.colors["rework_loop"][1],
                 self.colors["rework_loop"][2],
                 self.colors["rework_loop"][3] * alpha)
        
        # Draw circular arrow
        glPushMatrix()
        glTranslatef(*self.rework_loop_pos)
        
        # Draw torus (donut shape)
        glPushMatrix()
        glRotatef(90, 1, 0, 0)
        self.draw_torus(0.5, 0.1, 16, 32)
        glPopMatrix()
        
        glPopMatrix()
    
    def draw_fault_overlay(self):
        """Draw fault overlay in 3D"""
        if not PYOPENGL_AVAILABLE or not self.current_state['fault']:
            return
        
        glColor4f(*self.colors["fault"])
        
        # Red border around fixture
        glPushMatrix()
        glTranslatef(0, 0.5, 0)
        glScalef(3.2, 0.8, 2.2)
        self.draw_wireframe_cube()
        glPopMatrix()
        
        # "FAULT" text indicator
        glPushMatrix()
        glTranslatef(0, 1.2, 0)
        glScalef(0.8, 0.3, 0.1)
        glColor4f(1.0, 0.0, 0.0, 1.0)
        self.draw_cube()
        glPopMatrix()
    
    def draw_done_pulse(self):
        """Draw done pulse effect in 3D"""
        if not PYOPENGL_AVAILABLE:
            return
        
        current_time = time.time()
        pulse_progress = (current_time - self.done_pulse_time) / (DONE_PULSE_MS / 1000.0)
        pulse_alpha = 1.0 - pulse_progress
        pulse_scale = 1.0 + pulse_progress * 0.5
        
        glColor4f(self.colors["done_pulse"][0],
                 self.colors["done_pulse"][1],
                 self.colors["done_pulse"][2],
                 pulse_alpha)
        
        # Pulse around assembled part
        glPushMatrix()
        glTranslatef(*self.part_pos)
        glTranslatef(0, 0.5, 0)
        glScalef(pulse_scale * 1.2, pulse_scale * 0.7, pulse_scale * 1.0)
        self.draw_wireframe_cube()
        glPopMatrix()
    
    def draw_cube(self):
        """Draw a simple cube"""
        vertices = [
            (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
            (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)
        ]
        
        faces = [
            (0,1,2,3), (1,5,6,2), (5,4,7,6),
            (4,0,3,7), (3,2,6,7), (1,0,4,5)
        ]
        
        normals = [
            (0,0,-1), (1,0,0), (0,0,1),
            (-1,0,0), (0,1,0), (0,-1,0)
        ]
        
        glBegin(GL_QUADS)
        for i, face in enumerate(faces):
            glNormal3f(*normals[i])
            for vertex in face:
                glVertex3f(*vertices[vertex])
        glEnd()
    
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
    
    def draw_cylinder(self, sides=8):
        """Draw a simple cylinder"""
        if not PYOPENGL_AVAILABLE:
            return
        
        glPushMatrix()
        
        # Draw sides
        glBegin(GL_QUAD_STRIP)
        for i in range(sides + 1):
            angle = 2 * math.pi * i / sides
            x = math.cos(angle)
            z = math.sin(angle)
            
            glNormal3f(x, 0, z)
            glVertex3f(x, -1, z)
            glVertex3f(x, 1, z)
        glEnd()
        
        # Draw top
        glBegin(GL_POLYGON)
        glNormal3f(0, 1, 0)
        for i in range(sides):
            angle = 2 * math.pi * i / sides
            x = math.cos(angle)
            z = math.sin(angle)
            glVertex3f(x, 1, z)
        glEnd()
        
        # Draw bottom
        glBegin(GL_POLYGON)
        glNormal3f(0, -1, 0)
        for i in range(sides):
            angle = 2 * math.pi * i / sides
            x = math.cos(angle)
            z = math.sin(angle)
            glVertex3f(x, -1, z)
        glEnd()
        
        glPopMatrix()
    
    def draw_cone(self, sides=8):
        """Draw a cone"""
        if not PYOPENGL_AVAILABLE:
            return
        
        glPushMatrix()
        
        # Draw sides
        glBegin(GL_TRIANGLE_FAN)
        glNormal3f(0, 1, 0)
        glVertex3f(0, 1, 0)  # Tip
        for i in range(sides + 1):
            angle = 2 * math.pi * i / sides
            x = math.cos(angle)
            z = math.sin(angle)
            
            # Calculate normal for sloped surface
            length = math.sqrt(x*x + z*z + 1)
            nx = x / length
            ny = 1 / length
            nz = z / length
            glNormal3f(nx, ny, nz)
            
            glVertex3f(x, -1, z)
        glEnd()
        
        # Draw base
        glBegin(GL_POLYGON)
        glNormal3f(0, -1, 0)
        for i in range(sides):
            angle = 2 * math.pi * i / sides
            x = math.cos(angle)
            z = math.sin(angle)
            glVertex3f(x, -1, z)
        glEnd()
        
        glPopMatrix()
    
    def draw_torus(self, major_radius, minor_radius, major_segments, minor_segments):
        """Draw a torus (donut)"""
        if not PYOPENGL_AVAILABLE:
            return
        
        for i in range(major_segments):
            glBegin(GL_QUAD_STRIP)
            for j in range(minor_segments + 1):
                for k in [0, 1]:
                    s = (i + k) % major_segments
                    t = j % minor_segments
                    
                    x = (major_radius + minor_radius * math.cos(t * 2 * math.pi / minor_segments)) * \
                        math.cos(s * 2 * math.pi / major_segments)
                    y = (major_radius + minor_radius * math.cos(t * 2 * math.pi / minor_segments)) * \
                        math.sin(s * 2 * math.pi / major_segments)
                    z = minor_radius * math.sin(t * 2 * math.pi / minor_segments)
                    
                    nx = math.cos(s * 2 * math.pi / major_segments) * \
                         math.cos(t * 2 * math.pi / minor_segments)
                    ny = math.sin(s * 2 * math.pi / major_segments) * \
                         math.cos(t * 2 * math.pi / minor_segments)
                    nz = math.sin(t * 2 * math.pi / minor_segments)
                    
                    glNormal3f(nx, ny, nz)
                    glVertex3f(x, y, z)
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
    
    def update_state(self, event: ST2Event, mode: str = "LIVE"):
        """Update the current state from an event"""
        old_busy = self.current_state.get('busy', False)
        old_done = self.current_state.get('_last_done', False)
        
        # Track old scrapped before updating
        old_scrapped = self.last_scrapped_count
        
        self.current_state = {
            'ready': event.ready,
            'busy': event.busy,
            'done': event.done,
            'fault': event.fault,
            'cycle_time_ms': event.cycle_time_ms,
            'extra': event.extra
        }
        
        self.mode = mode
        
        # Check for busy rising edge OR if we're not active but should be (mid-cycle attach)
        now = time.time()
        if event.busy and not old_busy:
            # Normal busy rising edge
            self.proc_active = True
            self.proc_start = now
            
            # Reset animations
            self.clamp_open = True
            self.core_tool_height = 3.0
            self.screwdriver_active = False
            self.scanner_pos = -1.5
            self.part_pos = [0, 0.5, 0]
            self.scrap_increased = False
            self.rework_increased = False
            
            # Calculate stage duration from cycle_time_ms if available
            if event.cycle_time_ms:
                total_cycle_s = event.cycle_time_ms / 1000.0
                # Divide by 4 stages, clamp to reasonable range (0.8-8.0 seconds per stage)
                calculated_stage_s = total_cycle_s / 4.0
                self.stage_s = max(0.8, min(8.0, calculated_stage_s))
            else:
                self.stage_s = self.base_stage_s
            
            self.proc_end = now + 4 * self.stage_s  # 4 stages
        
        # Handle case where visualizer attaches mid-cycle (busy already True but not active)
        elif event.busy and not self.proc_active and self.active_stage_index == -1:
            # We missed the busy rising edge, start animation anyway
            self.proc_active = True
            
            # Use cycle time to determine stage duration
            if event.cycle_time_ms:
                total_cycle_s = event.cycle_time_ms / 1000.0
                calculated_stage_s = total_cycle_s / 4.0
                self.stage_s = max(0.8, min(8.0, calculated_stage_s))
            else:
                self.stage_s = self.base_stage_s
            
            # Estimate we're in stage 3 or 4 (fasten or verify) since we're already busy
            # Set proc_start in the past so animation shows active stage
            self.proc_start = now - 3 * self.stage_s  # Assume we're near the end
            self.proc_end = now + 1 * self.stage_s  # Extend a bit into the future
            
            # Set reasonable animation states for mid-cycle
            self.clamp_open = False  # Clamps closed during processing
            self.core_tool_height = 0.5  # Core tool retracted
            self.screwdriver_active = False
            self.scanner_pos = -1.5
            self.part_pos = [0, 0.5, 0]
            
            print(f"Attached mid-cycle: starting animation at estimated stage 3/4")
        
        # On fault: stop processing
        if event.fault:
            self.proc_active = False
            self.active_stage_index = -1
            self.stage_progress = 0.0
            self.scrap_increased = False
            self.rework_increased = False
        
        # On done rising edge: stop processing and trigger pulse
        if event.done and not old_done:
            self.done_pulse_time = time.time()
            self.proc_active = False
            self.active_stage_index = -1
            self.stage_progress = 0.0
        
        # Check for done pulse from completed counter
        completed = event.extra.get('completed')
        if completed is not None:
            try:
                completed = int(completed)
                if completed > self.last_completed_count:
                    self.done_pulse_time = time.time()
                    self.last_completed_count = completed
                    # Stop processing when completion is detected
                    self.proc_active = False
                    self.active_stage_index = -1
                    self.stage_progress = 0.0
                    self.scrap_increased = False
                    self.rework_increased = False
            except (ValueError, TypeError):
                pass
        
        # Update scrapped and reworks counters for visualization
        scrapped = event.extra.get('scrapped')
        if scrapped is not None:
            try:
                scrapped = int(scrapped)
                # Check if scrapped increased
                if scrapped > old_scrapped:
                    self.scrap_increased = True
                    self.scrap_animation_start = time.time()
                    # Reset part position for animation
                    self.part_pos = [0, 0.5, 0]
                self.last_scrapped_count = scrapped
            except (ValueError, TypeError):
                pass
        
        reworks = event.extra.get('reworks')
        if reworks is not None:
            try:
                reworks = int(reworks)
                if reworks > self.last_reworks_count:
                    self.rework_increased = True
                    self.rework_animation_start = time.time()
                self.last_reworks_count = reworks
            except (ValueError, TypeError):
                pass
        
        # Store last done state
        self.current_state['_last_done'] = event.done
        
        self.update()

# ===== MAIN WINDOW =====
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ST2 Frame/Core Assembly Visualizer")
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
    
    def init_ui(self):
        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Main layout
        main_layout = QVBoxLayout(central_widget)
        
        # Top info bar
        info_layout = QHBoxLayout()
        self.log_info_label = QLabel("Searching for ST2 log...")
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
        self.gl_widget = ST2OpenGLWidget()
        content_layout.addWidget(self.gl_widget, 3)
        
        # Status panel (right)
        status_panel = QVBoxLayout()
        
        # Batch/Recipe info
        info_group = QGroupBox("Batch / Recipe Info")
        info_layout = QVBoxLayout()
        
        self.batch_label = QLabel("Batch ID: N/A")
        self.recipe_label = QLabel("Recipe ID: N/A")
        self.now_label = QLabel("Now: --:--:--")
        
        for label in [self.batch_label, self.recipe_label, self.now_label]:
            label.setStyleSheet("font-family: monospace; padding: 2px;")
            info_layout.addWidget(label)
        
        info_group.setLayout(info_layout)
        status_panel.addWidget(info_group)
        
        # State labels
        state_group = QGroupBox("ST2 State")
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
        stage_group = QGroupBox("Assembly Status")
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
    
    def load_live_snapshot(self) -> Tuple[Optional[ST2Event], int]:
        """Load snapshot of last N lines from log file, return latest event and raw count"""
        if not self.log_path or not os.path.exists(self.log_path):
            return None, 0
        
        try:
            with open(self.log_path, 'r', encoding='utf-8', errors='ignore') as f:
                # Read last N lines efficiently
                lines = collections.deque(f, maxlen=SNAPSHOT_LINES)
            
            parser = ST2LogParser()
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
        """Discover and load ST2 log file"""
        # Stop current worker
        self._stop_tail_worker()
        
        # Find log
        self.log_path = LogDiscoverer.find_st2_log()
        
        if self.log_path:
            base_name = os.path.basename(self.log_path)
            self.log_info_label.setText(f"Log: {base_name}")
            
            # Reset counters
            self.raw_event_count = 0
            self.accepted_event_count = 0
            self.update_event_count_label()
            
            if self.is_live_mode:
                self.switch_to_live()
            else:
                self.switch_to_replay()
        else:
            self.log_info_label.setText("No ST2 log found (Demo Mode)")
            self.log_path = None
            self.replay_events = []
            self.set_demo_mode()
    
    def set_demo_mode(self):
        """Setup demo mode with synthetic events"""
        self.replay_events = []
        synthetic_time = 0
        
        # Create some demo events
        for i in range(10):
            event = ST2Event(
                t_ns=synthetic_time,
                ready=(i % 4 == 0),
                busy=(i % 4 == 1),
                done=(i % 4 == 2),
                fault=(i % 4 == 3),
                cycle_time_ms=15921.0 + i * 100,  # Typical ST2 cycle time ~16 seconds
                extra={"completed": i, "scrapped": i // 5, "reworks": i // 3, "batch_id": "BATCH001", "recipe_id": "RECIPE_A"}
            )
            self.replay_events.append(event)
            synthetic_time += 100_000_000  # 100ms
        
        self.raw_event_count = len(self.replay_events)
        self.accepted_event_count = 0
        self.update_event_count_label()
        
        if not self.is_live_mode:
            self.update_states_from_replay()
    
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
            # Force immediate display update
            self.update_states_from_replay()
    
    def load_replay_data(self):
        """Load all events from log file for replay"""
        try:
            with open(self.log_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
            
            events = []
            parser = ST2LogParser()
            
            for line in lines:
                event = parser.parse_line(line)
                if event:
                    events.append(event)
            
            # Sort by time and limit
            events.sort(key=lambda x: x.t_ns)
            self.replay_events = events[-MAX_EVENTS:]
            self.raw_event_count = len(self.replay_events)
            self.accepted_event_count = 0
            self.update_event_count_label()
            
            print(f"Loaded {len(self.replay_events)} events for replay")
            if self.replay_events:
                print(f"Time range: {self.replay_events[0].t_ns/1e9:.3f}s to {self.replay_events[-1].t_ns/1e9:.3f}s")
            
        except Exception as e:
            print(f"Error loading {self.log_path}: {e}")
            traceback.print_exc()
            self.replay_events = []
    
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
            # For replay mode, update display without debouncing
            self.update_display(current_event, from_snapshot=False)
    
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
    
    def process_new_event(self, event: ST2Event):
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
    
    def update_display(self, event: ST2Event, from_snapshot: bool = False):
        """Update all displays from event"""
        # Update OpenGL with mode
        self.gl_widget.update_state(event, self.current_mode)
        
        # Update status labels
        self.ready_label.setText(f"Ready: {event.ready}")
        self.busy_label.setText(f"Busy: {event.busy}")
        self.done_label.setText(f"Done: {event.done}")
        self.fault_label.setText(f"Fault: {event.fault}")
        
        if event.cycle_time_ms:
            self.cycle_label.setText(f"Cycle Time: {event.cycle_time_ms:.1f} ms")
        else:
            self.cycle_label.setText("Cycle Time: N/A")
        
        # Update batch/recipe info - prefer SimPy time if available
        batch_id = event.extra.get('batch_id', 'N/A')
        recipe_id = event.extra.get('recipe_id', 'N/A')
        
        # Use SimPy time if available, otherwise use current time
        simpy_now = event.extra.get('simpy_now_s')
        if simpy_now is not None:
            now_text = f"SimPy: {simpy_now:.1f}s"
        else:
            now_text = f"Now: {datetime.now().strftime('%H:%M:%S')}"
        
        self.batch_label.setText(f"Batch ID: {batch_id}")
        self.recipe_label.setText(f"Recipe ID: {recipe_id}")
        self.now_label.setText(now_text)
        
        # Update stage status
        active_index = self.gl_widget.active_stage_index
        if active_index >= 0 and active_index < 4:
            stage_name = self.gl_widget.stage_names[active_index]
            self.stage_status_label.setText(f"Active Stage: {stage_name}")
            self.stage_status_label.setStyleSheet("font-family: monospace; padding: 2px; color: #FFD700;")
            
            # Update progress bar
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
        # Update animation time
        self.gl_widget.animation_time = time.time()
        
        # Update stage status based on current processing state
        self.gl_widget.compute_active_stage(time.time())
        active_index = self.gl_widget.active_stage_index
        
        if active_index >= 0 and active_index < 4:
            stage_name = self.gl_widget.stage_names[active_index]
            # Add progress percentage if available
            if self.gl_widget.stage_progress > 0:
                stage_name += f" ({self.gl_widget.stage_progress*100:.0f}%)"
                # Update progress bar
                self.progress_bar.setValue(int(self.gl_widget.stage_progress * 100))
            self.stage_status_label.setText(f"Active Stage: {stage_name}")
            self.stage_status_label.setStyleSheet("font-family: monospace; padding: 2px; color: #FFD700;")
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
    print("Running visualizer from:", os.path.abspath(__file__))
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