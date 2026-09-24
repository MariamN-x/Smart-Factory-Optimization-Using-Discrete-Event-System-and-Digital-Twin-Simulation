#!/usr/bin/env python3
"""
ST5 Quality Inspection Visualizer - Professional visualization for ST5 Quality Inspection logs

FEATURES / CHANGES from ST1 base:
1. New log discovery: Finds ST5 logs with "qualityinspection" preference
2. Updated parser: Handles ST5 signals (accept, reject, last_accept) as counters and pulse-based done
3. New OpenGL scene: Quality inspection cell with conveyor, inspection table, pass/reject bins
4. ST5-specific animations: Sensor head scanning, part movement, accept/reject effects
5. Enhanced UI: Inspection result panel, batch/recipe info
6. Deterministic timing: REPLAY uses VSI time only, no wall-clock drift
7. Fixed OpenGL: Reused quadrics, proper cylinder alignment, blending for transparency
8. CORRECTED: accept/reject are counters, decision latched on done edge, pulse from counter increase
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
import bisect
from typing import *
from dataclasses import dataclass, field
from datetime import datetime

# Add user site-packages to sys.path for PySide6
import site
user_site = site.getusersitepackages()
if user_site not in sys.path:
    sys.path.insert(0, user_site)

# ===== CONFIGURATION =====
SEARCH_ROOT = "."
MAX_EVENTS = 50000
TIMER_FPS = 30
DONE_PULSE_MS = 300
ACCEPT_REJECT_PULSE_MS = 500
OPENGL_MAJOR_VERSION = 2
OPENGL_MINOR_VERSION = 1
USE_PYOPENGL = True
SNAPSHOT_LINES = 3000
IDLE_TIMEOUT = 5.0  # seconds
LONG_IDLE_TIMEOUT = 15.0  # seconds for "STOPPED?" message
AUTO_SWITCH_ON_IDLE = False  # Whether to auto-switch to replay when idle

# ===== QT IMPORTS =====
try:
    from PySide6.QtWidgets import *
    from PySide6.QtCore import *
    from PySide6.QtGui import QSurfaceFormat, QPainter, QColor, QFont, QPen, QFontMetrics
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
class ST5Event:
    t_ns: int
    ready: bool = False
    busy: bool = False
    done: bool = False
    fault: bool = False
    cycle_time_ms: Optional[float] = None
    accept: int = 0  # Counter, not boolean
    reject: int = 0  # Counter, not boolean
    last_accept: int = 0  # 0 = reject, 1 = accept
    extra: Dict[str, Any] = field(default_factory=dict)

# ===== CARRY-FORWARD PARSER =====
class ST5LogParser:
    """Carry-forward parser for ST5 Quality Inspection logs"""
    
    def __init__(self):
        self.reset()
    
    def reset(self):
        """Reset parser state"""
        self.last_vsi_time_ns = None
        self.synthetic_time_ns = 0
        
        # Carry-forward state for ST5 signals
        self.carried_state = {
            'ready': None,
            'busy': None,
            'done': None,
            'fault': None,
            'accept': None,  # Counter
            'reject': None,  # Counter
            'last_accept': None  # 0/1
        }
        self.carried_cycle_time = None
        self.carried_extra = {}
        
        # Have we ever seen any state value?
        self.has_seen_any_state = False
        
        # Patterns
        self.vsi_time_pattern = re.compile(r"VSI\s+time\s*:\s*(\d+)\s*ns", re.IGNORECASE)
        
        # FIXED: Match both "cycle_time_ms = 2800" and "cycle_time = 2800 ms"
        self.cycle_time_pattern = re.compile(
            r"\bcycle(?:[_\s]*time)(?:[_\s]*ms)?\b\s*[:=]\s*([\d.]+)\s*(?:ms)?\b",
            re.IGNORECASE
        )
        
        # Generic key-value pattern for signals like "ready = 1", "ready: 1", "ready=1"
        # Supports both numeric and True/False values
        # Fixed: Exclude cycle_time_ms to prevent duplication
        self.key_value_pattern = re.compile(
            r"(?!cycle[_\s]*time)(\w+)[_\s]*[:=]\s*([\w.]+)", 
            re.IGNORECASE
        )
        
        # Specific patterns for ST5 signals (more precise)
        # FIXED: accept and reject are counters (any integer), not just 0/1
        self.st5_patterns = {
            "ready": re.compile(r"\bready\b[_\s]*[:=]\s*([01]|true|false)", re.IGNORECASE),
            "busy": re.compile(r"\bbusy\b[_\s]*[:=]\s*([01]|true|false)", re.IGNORECASE),
            "done": re.compile(r"\bdone\b[_\s]*[:=]\s*([01]|true|false)", re.IGNORECASE),
            "fault": re.compile(r"\bfault\b[_\s]*[:=]\s*([01]|true|false)", re.IGNORECASE),
            "accept": re.compile(r"\baccept\b[_\s]*[:=]\s*(\d+|true|false)", re.IGNORECASE),
            "reject": re.compile(r"\breject\b[_\s]*[:=]\s*(\d+|true|false)", re.IGNORECASE),
            "last_accept": re.compile(r"\blast_accept\b[_\s]*[:=]\s*([01]|true|false)", re.IGNORECASE),
        }
        
        # Pattern for "+=ST5_QualityInspection+=" block markers (optional)
        self.st5_block_pattern = re.compile(r"\+=ST5_QualityInspection\+=", re.IGNORECASE)
    
    def seed_from_event(self, event: ST5Event):
        """Seed the parser with an existing event to establish carried state"""
        self.carried_state = {
            'ready': 1 if event.ready else 0,
            'busy': 1 if event.busy else 0,
            'done': 1 if event.done else 0,
            'fault': 1 if event.fault else 0,
            'accept': event.accept,
            'reject': event.reject,
            'last_accept': event.last_accept
        }
        self.carried_cycle_time = event.cycle_time_ms
        self.carried_extra = copy.deepcopy(event.extra)
        self.has_seen_any_state = True
        self.last_vsi_time_ns = event.t_ns
        self.synthetic_time_ns = event.t_ns
    
    def parse_line(self, line: str) -> Optional[ST5Event]:
        """Parse a line and return an event if state was updated"""
        line = line.strip()
        if not line:
            return None
        
        # Track if this line had any signal at all
        had_signal = False
        
        # Check for VSI time (can be on its own line)
        vsi_match = self.vsi_time_pattern.search(line)
        if vsi_match:
            self.last_vsi_time_ns = int(vsi_match.group(1))
            # VSI time alone doesn't count as a signal
        
        # Determine timestamp to use
        if self.last_vsi_time_ns is not None:
            timestamp = self.last_vsi_time_ns
        else:
            self.synthetic_time_ns += 10_000_000  # 10ms increment
            timestamp = self.synthetic_time_ns
        
        # Check for ST5-specific state updates
        for state_name, pattern in self.st5_patterns.items():
            match = pattern.search(line)
            if match:
                try:
                    value_str = match.group(1).lower()
                    if value_str in ['true', 'false']:
                        value = 1 if value_str == 'true' else 0
                    else:
                        value = int(value_str)  # Accepts any integer for accept/reject
                    # Always update carried state when we see a signal
                    self.carried_state[state_name] = value
                    self.has_seen_any_state = True
                    had_signal = True
                except ValueError:
                    pass
        
        # Check for cycle time
        cycle_match = self.cycle_time_pattern.search(line)
        if cycle_match:
            try:
                cycle_time = float(cycle_match.group(1))
                self.carried_cycle_time = cycle_time
                had_signal = True
            except ValueError:
                pass
        
        # Extract key-value pairs (for extra signals like batch_id, recipe_id)
        current_line_extras = {}
        for match in self.key_value_pattern.finditer(line):
            key, value_str = match.groups()
            key_lower = key.lower()
            
            # Skip already parsed ST5 keys
            if key_lower in ['vsi', 'time', 'cycle', 'ready', 'busy', 'done', 
                            'fault', 'accept', 'reject', 'last_accept']:
                continue
            
            # Try to convert to appropriate type
            try:
                if '.' in value_str:
                    value = float(value_str)
                elif value_str.lower() in ['true', 'false']:
                    value = value_str.lower() == 'true'
                else:
                    value = int(value_str)
            except ValueError:
                value = value_str
            
            current_line_extras[key] = value
        
        # Update carried extras with current line extras
        if current_line_extras:
            for key, value in current_line_extras.items():
                self.carried_extra[key] = value
            had_signal = True
        
        # Also check for "DONE pulse" lines (they might indicate done signal)
        if "done" in line.lower() and "pulse" in line.lower():
            # This line indicates a DONE pulse
            self.carried_state['done'] = 1
            self.has_seen_any_state = True
            had_signal = True
        
        # Emit an event if this line had any signal (even if values didn't change)
        if had_signal:
            # Convert carried state to appropriate types
            ready = bool(self.carried_state['ready']) if self.carried_state['ready'] is not None else False
            busy = bool(self.carried_state['busy']) if self.carried_state['busy'] is not None else False
            done = bool(self.carried_state['done']) if self.carried_state['done'] is not None else False
            fault = bool(self.carried_state['fault']) if self.carried_state['fault'] is not None else False
            
            # accept/reject are integers (counters)
            accept = self.carried_state['accept'] if self.carried_state['accept'] is not None else 0
            reject = self.carried_state['reject'] if self.carried_state['reject'] is not None else 0
            last_accept = self.carried_state['last_accept'] if self.carried_state['last_accept'] is not None else 0
            
            event = ST5Event(
                t_ns=timestamp,
                ready=ready,
                busy=busy,
                done=done,
                fault=fault,
                cycle_time_ms=self.carried_cycle_time,
                accept=accept,
                reject=reject,
                last_accept=last_accept,
                extra=copy.deepcopy(self.carried_extra)
            )
            return event
        
        return None
    
    def get_current_state(self) -> Optional[ST5Event]:
        """Get current state without parsing a line"""
        if not self.has_seen_any_state and self.carried_cycle_time is None and not self.carried_extra:
            return None
        
        # Determine timestamp
        if self.last_vsi_time_ns is not None:
            timestamp = self.last_vsi_time_ns
        else:
            timestamp = self.synthetic_time_ns
        
        # Convert carried state to appropriate types
        ready = bool(self.carried_state['ready']) if self.carried_state['ready'] is not None else False
        busy = bool(self.carried_state['busy']) if self.carried_state['busy'] is not None else False
        done = bool(self.carried_state['done']) if self.carried_state['done'] is not None else False
        fault = bool(self.carried_state['fault']) if self.carried_state['fault'] is not None else False
        
        accept = self.carried_state['accept'] if self.carried_state['accept'] is not None else 0
        reject = self.carried_state['reject'] if self.carried_state['reject'] is not None else 0
        last_accept = self.carried_state['last_accept'] if self.carried_state['last_accept'] is not None else 0
        
        return ST5Event(
            t_ns=timestamp,
            ready=ready,
            busy=busy,
            done=done,
            fault=fault,
            cycle_time_ms=self.carried_cycle_time,
            accept=accept,
            reject=reject,
            last_accept=last_accept,
            extra=copy.deepcopy(self.carried_extra)
        )

# ===== LOG DISCOVERY =====
class LogDiscoverer:
    @staticmethod
    def find_st5_log() -> Optional[str]:
        """Find newest ST5 log file matching pattern (more robust)"""
        st5_files = []
        
        # Search recursively from SEARCH_ROOT
        for root, dirs, files in os.walk(SEARCH_ROOT):
            for file in files:
                file_lower = file.lower()
                # Check if file contains "st5" and ends with ".log"
                if "st5" in file_lower and file_lower.endswith('.log'):
                    full_path = os.path.join(root, file)
                    mtime = os.path.getmtime(full_path)
                    
                    # Score: prefer files containing "qualityinspection"
                    score = 0
                    if "qualityinspection" in file_lower:
                        score = 1
                    
                    st5_files.append((score, mtime, full_path))
        
        if not st5_files:
            return None
        
        # Sort by score (higher first), then by mtime (newest first)
        st5_files.sort(key=lambda x: (-x[0], -x[1]))
        selected_file = st5_files[0][2]
        print(f"Selected ST5 log: {selected_file}")
        return selected_file

# ===== TAIL WORKER =====
class LogTailWorker(QThread):
    new_event = Signal(ST5Event)  # Emitted for every signal line
    activity_detected = Signal()  # Emitted when any line is read
    file_reopened = Signal()  # Emitted when file is reopened (rotation/truncation)
    
    def __init__(self, log_path: str, seed_event: Optional[ST5Event] = None):
        super().__init__()
        self.log_path = log_path
        self.seed_event = seed_event
        self.running = True
        self.file_handle = None
        self.file_position = 0
        self.file_inode = None
        self.parser = ST5LogParser()
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
            self.parser = ST5LogParser()
            
            # Seed the parser if we have a seed event
            if seed and self.seed_event:
                self.parser.seed_from_event(self.seed_event)
                
        except Exception as e:
            print(f"Error opening {self.log_path}: {e}")
            self.file_handle = None
    
    def reseed_parser(self, seed_event: ST5Event):
        """Reseed the parser with a new event (e.g., after file rotation)"""
        self.seed_event = seed_event
        self.parser.seed_from_event(seed_event)
    
    def stop(self):
        """Stop the worker thread"""
        self.running = False
        if self.isRunning():
            self.wait()

# ===== OPENGL WIDGET =====
class ST5OpenGLWidget(QOpenGLWidget):
    """Professional OpenGL visualization for ST5 Quality Inspection Station"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_state = {
            'ready': False,
            'busy': False,
            'done': False,
            'fault': False,
            'cycle_time_ms': None,
            'accept': 0,  # Counter
            'reject': 0,  # Counter
            'last_accept': 0,
            'extra': {}
        }
        self.animation_time = 0
        self.done_pulse_time = 0
        self.accept_pulse_time = 0
        self.reject_pulse_time = 0
        self.shake_offset = 0.0
        
        # Animation parameters for ST5
        self.base_cycle_s = 2.8  # Default 2800ms
        self.cycle_s = self.base_cycle_s  # Current cycle duration
        self.inspection_active = False  # Is inspection active?
        self.inspection_start = 0.0  # When inspection started (in visual_time_s)
        self.inspection_end = 0.0  # When inspection ends
        self.inspection_progress = 0.0  # Progress within current inspection (0.0 to 1.0)
        
        # Part movement
        self.part_visible = False
        self.part_position = [-4.0, 0.5, 0]  # Start at conveyor infeed
        self.part_moving_to_table = False
        self.part_on_table = False
        self.part_moving_to_bin = False
        self.decision_latched = None  # "pass" or "reject" - latched on done edge
        
        # Previous counters for delta detection
        self.accept_count_prev = 0
        self.reject_count_prev = 0
        
        # Animation speeds (deterministic from animation_time)
        self.conveyor_speed = 0.5  # stripes per second
        self.sensor_scan_speed = 2.0  # oscillations per second
        
        # Mode for overlay
        self.mode = "LIVE"  # Fixed: Initialize mode attribute
        
        # Colors - professional inspection cell palette
        self.colors = {
            "dark_gray": (0.15, 0.15, 0.17, 1.0),
            "medium_gray": (0.25, 0.25, 0.27, 1.0),
            "light_gray": (0.35, 0.35, 0.37, 1.0),
            "metal_gray": (0.4, 0.42, 0.45, 1.0),
            "conveyor": (0.3, 0.3, 0.32, 1.0),
            "conveyor_stripe": (0.5, 0.5, 0.5, 1.0),
            "inspection_table": (0.2, 0.2, 0.25, 1.0),
            "pass_bin": (0.1, 0.4, 0.1, 1.0),
            "reject_bin": (0.4, 0.1, 0.1, 1.0),
            "sensor_head": (0.5, 0.5, 0.6, 1.0),
            "sensor_lens": (0.1, 0.1, 0.2, 1.0),
            "part": (0.8, 0.7, 0.2, 1.0),
            "green_led": (0.0, 0.8, 0.0, 1.0),
            "amber_led": (1.0, 0.6, 0.0, 1.0),
            "red_led": (0.8, 0.0, 0.0, 1.0),
            "cyan_pulse": (0.0, 1.0, 1.0, 1.0),
            "green_pulse": (0.0, 1.0, 0.0, 1.0),
            "red_pulse": (1.0, 0.0, 0.0, 1.0),
            "grid": (0.25, 0.25, 0.28, 1.0),
            "safety_yellow": (0.8, 0.8, 0.1, 0.3),
        }
        
        # Camera
        self.camera_distance = 16.0
        self.camera_angle_x = 30.0
        self.camera_angle_y = 45.0
        self.last_mouse_pos = None
        
        # GLU quadric for reuse
        self._quadric = None
        
        self.setMouseTracking(True)
    
    def __del__(self):
        """Clean up GLU quadric when widget is destroyed"""
        if hasattr(self, '_quadric') and self._quadric and PYOPENGL_AVAILABLE:
            gluDeleteQuadric(self._quadric)
    
    def smoothstep(self, t):
        """Smoothstep easing function for smooth animations"""
        return t * t * (3 - 2 * t)
    
    def compute_inspection_progress(self):
        """Compute inspection progress based on animation_time"""
        if not self.inspection_active:
            self.inspection_progress = 0.0
            return 0.0
        
        # If busy but timer ended, stay at 100% progress
        if self.animation_time >= self.inspection_end:
            if self.current_state['busy']:
                # Stay at 100% with subtle animation
                self.inspection_progress = 1.0
                return 1.0
            else:
                self.inspection_active = False
                self.inspection_progress = 0.0
                return 0.0
        
        elapsed = self.animation_time - self.inspection_start
        self.inspection_progress = min(1.0, max(0.0, elapsed / self.cycle_s))
        
        return self.inspection_progress
    
    def update_part_animation(self):
        """Update part position and movement based on state and progress"""
        progress = self.inspection_progress
        
        if not self.current_state['busy']:
            # Idle state - part at conveyor start
            self.part_visible = False
            self.part_moving_to_table = False
            self.part_on_table = False
            self.part_moving_to_bin = False
            self.part_position = [-4.0, 0.5, 0]
            return
        
        # Inspection cycle logic
        if progress < 0.3:  # Part moving to table
            self.part_visible = True
            self.part_moving_to_table = True
            self.part_on_table = False
            self.part_moving_to_bin = False
            
            # Move from conveyor start to table center
            t = self.smoothstep(progress / 0.3)
            self.part_position[0] = -4.0 + t * 4.0  # From -4 to 0
            self.part_position[1] = 0.5 + t * 0.3   # Slight lift
        
        elif progress < 0.8:  # Part on table being inspected
            self.part_visible = True
            self.part_moving_to_table = False
            self.part_on_table = True
            self.part_moving_to_bin = False
            
            # Part centered on table with slight vibration during inspection
            self.part_position[0] = 0
            self.part_position[1] = 0.8
            # Add subtle vibration
            self.part_position[2] = math.sin(self.animation_time * 10) * 0.02
        
        elif progress >= 0.8:  # Part moving to bin after inspection result
            self.part_visible = True
            self.part_moving_to_table = False
            self.part_on_table = False
            self.part_moving_to_bin = True
            
            # Use latched decision for bin selection
            if self.decision_latched == "pass":
                target_x = 3.5  # Pass bin position
            elif self.decision_latched == "reject":
                target_x = -3.5  # Reject bin position
            else:
                # No decision yet, stay on table
                self.part_moving_to_bin = False
                self.part_on_table = True
                return
            
            # Move to bin
            t = self.smoothstep((progress - 0.8) / 0.2)
            self.part_position[0] = t * target_x
            self.part_position[1] = 0.8 - t * 0.5  # Drop into bin
    
    def get_conveyor_offset(self):
        """Get conveyor offset based on animation_time (deterministic)"""
        if self.current_state['busy'] and not self.current_state['fault']:
            # Conveyor moves during busy state
            return (self.animation_time * self.conveyor_speed) % 1.0
        else:
            # Slow idle movement
            return (self.animation_time * 0.1) % 1.0
    
    def get_sensor_animation(self):
        """Get sensor head animation based on animation_time (deterministic)"""
        if self.current_state['busy'] and not self.current_state['fault']:
            # Active scanning during inspection
            offset = math.sin(self.animation_time * self.sensor_scan_speed) * 0.1
            angle = math.sin(self.animation_time * (self.sensor_scan_speed * 0.7)) * 5.0
            return offset, angle
        else:
            # Subtle idle movement
            offset = math.sin(self.animation_time * 0.5) * 0.02
            angle = math.sin(self.animation_time * 0.3) * 2.0
            return offset, angle
    
    # ===== OPENGL PRIMITIVES =====
    def draw_box(self, cx, cy, cz, sx, sy, sz, rgba):
        """Draw a solid box centered at (cx, cy, cz) with size (sx, sy, sz)"""
        if not PYOPENGL_AVAILABLE:
            return
            
        glColor4f(*rgba)
        glBegin(GL_QUADS)
        
        # Front face
        glNormal3f(0, 0, 1)
        glVertex3f(cx - sx/2, cy - sy/2, cz + sz/2)
        glVertex3f(cx + sx/2, cy - sy/2, cz + sz/2)
        glVertex3f(cx + sx/2, cy + sy/2, cz + sz/2)
        glVertex3f(cx - sx/2, cy + sy/2, cz + sz/2)
        
        # Back face
        glNormal3f(0, 0, -1)
        glVertex3f(cx - sx/2, cy - sy/2, cz - sz/2)
        glVertex3f(cx - sx/2, cy + sy/2, cz - sz/2)
        glVertex3f(cx + sx/2, cy + sy/2, cz - sz/2)
        glVertex3f(cx + sx/2, cy - sy/2, cz - sz/2)
        
        # Left face
        glNormal3f(-1, 0, 0)
        glVertex3f(cx - sx/2, cy - sy/2, cz - sz/2)
        glVertex3f(cx - sx/2, cy - sy/2, cz + sz/2)
        glVertex3f(cx - sx/2, cy + sy/2, cz + sz/2)
        glVertex3f(cx - sx/2, cy + sy/2, cz - sz/2)
        
        # Right face
        glNormal3f(1, 0, 0)
        glVertex3f(cx + sx/2, cy - sy/2, cz - sz/2)
        glVertex3f(cx + sx/2, cy + sy/2, cz - sz/2)
        glVertex3f(cx + sx/2, cy + sy/2, cz + sz/2)
        glVertex3f(cx + sx/2, cy - sy/2, cz + sz/2)
        
        # Top face
        glNormal3f(0, 1, 0)
        glVertex3f(cx - sx/2, cy + sy/2, cz - sz/2)
        glVertex3f(cx - sx/2, cy + sy/2, cz + sz/2)
        glVertex3f(cx + sx/2, cy + sy/2, cz + sz/2)
        glVertex3f(cx + sx/2, cy + sy/2, cz - sz/2)
        
        # Bottom face
        glNormal3f(0, -1, 0)
        glVertex3f(cx - sx/2, cy - sy/2, cz - sz/2)
        glVertex3f(cx + sx/2, cy - sy/2, cz - sz/2)
        glVertex3f(cx + sx/2, cy - sy/2, cz + sz/2)
        glVertex3f(cx - sx/2, cy - sy/2, cz + sz/2)
        
        glEnd()
    
    def draw_cylinder(self, cx, cy, cz, radius, height, rgba, segments=32):
        """Draw a cylinder aligned with Y axis"""
        if not PYOPENGL_AVAILABLE:
            return
        
        # Ensure quadric exists
        if not self._quadric:
            return
        
        glPushMatrix()
        glTranslatef(cx, cy, cz)
        glRotatef(-90, 1, 0, 0)   # Fix: align Z-axis cylinder to Y-axis
        glColor4f(*rgba)
        gluCylinder(self._quadric, radius, radius, height, segments, 1)
        
        # Draw bottom cap
        gluDisk(self._quadric, 0, radius, segments, 1)
        
        # Draw top cap
        glTranslatef(0, 0, height)
        gluDisk(self._quadric, 0, radius, segments, 1)
        
        glPopMatrix()
    
    def draw_sphere(self, cx, cy, cz, radius, rgba, slices=16, stacks=16):
        """Draw a sphere"""
        if not PYOPENGL_AVAILABLE:
            return
        
        # Ensure quadric exists
        if not self._quadric:
            return
        
        glPushMatrix()
        glTranslatef(cx, cy, cz)
        glColor4f(*rgba)
        gluSphere(self._quadric, radius, slices, stacks)
        glPopMatrix()
    
    def draw_done_pulse(self):
        """Draw done pulse effect on inspection table"""
        pulse_progress = (self.animation_time - self.done_pulse_time) / (DONE_PULSE_MS / 1000.0)
        if pulse_progress >= 1.0:
            return
        
        pulse_alpha = 1.0 - pulse_progress
        pulse_radius = 0.3 + pulse_progress * 2.0
        
        # Draw pulsing ring on table
        glPushMatrix()
        glTranslatef(0, 0.9, 0)
        
        glColor4f(
            self.colors["cyan_pulse"][0],
            self.colors["cyan_pulse"][1],
            self.colors["cyan_pulse"][2],
            pulse_alpha
        )
        
        glBegin(GL_LINE_LOOP)
        segments = 32
        for i in range(segments):
            angle = 2.0 * math.pi * i / segments
            x = pulse_radius * math.cos(angle)
            z = pulse_radius * math.sin(angle)
            glVertex3f(x, 0, z)
        glEnd()
        
        glPopMatrix()
    
    def draw_accept_pulse(self):
        """Draw accept pulse effect on pass bin"""
        pulse_progress = (self.animation_time - self.accept_pulse_time) / (ACCEPT_REJECT_PULSE_MS / 1000.0)
        if pulse_progress >= 1.0:
            return
        
        pulse_alpha = 1.0 - pulse_progress
        pulse_radius = 0.2 + pulse_progress * 1.5
        
        # Draw pulsing ring on pass bin
        glPushMatrix()
        glTranslatef(3.5, 0.5, 0)
        
        glColor4f(
            self.colors["green_pulse"][0],
            self.colors["green_pulse"][1],
            self.colors["green_pulse"][2],
            pulse_alpha
        )
        
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        
        glBegin(GL_LINE_LOOP)
        segments = 32
        for i in range(segments):
            angle = 2.0 * math.pi * i / segments
            x = pulse_radius * math.cos(angle)
            z = pulse_radius * math.sin(angle)
            glVertex3f(x, 0, z)
        glEnd()
        
        glDisable(GL_BLEND)
        glPopMatrix()
    
    def draw_reject_pulse(self):
        """Draw reject pulse effect on reject bin"""
        pulse_progress = (self.animation_time - self.reject_pulse_time) / (ACCEPT_REJECT_PULSE_MS / 1000.0)
        if pulse_progress >= 1.0:
            return
        
        pulse_alpha = 1.0 - pulse_progress
        pulse_radius = 0.2 + pulse_progress * 1.5
        
        # Draw pulsing ring on reject bin
        glPushMatrix()
        glTranslatef(-3.5, 0.5, 0)
        
        glColor4f(
            self.colors["red_pulse"][0],
            self.colors["red_pulse"][1],
            self.colors["red_pulse"][2],
            pulse_alpha
        )
        
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        
        glBegin(GL_LINE_LOOP)
        segments = 32
        for i in range(segments):
            angle = 2.0 * math.pi * i / segments
            x = pulse_radius * math.cos(angle)
            z = pulse_radius * math.sin(angle)
            glVertex3f(x, 0, z)
        glEnd()
        
        glDisable(GL_BLEND)
        glPopMatrix()
    
    # ===== MAIN OPENGL METHODS =====
    def initializeGL(self):
        if PYOPENGL_AVAILABLE:
            glEnable(GL_DEPTH_TEST)
            glEnable(GL_LIGHTING)
            glEnable(GL_LIGHT0)
            glEnable(GL_LIGHT1)
            glEnable(GL_COLOR_MATERIAL)
            glColorMaterial(GL_FRONT_AND_BACK, GL_AMBIENT_AND_DIFFUSE)
            glEnable(GL_NORMALIZE)
            glEnable(GL_MULTISAMPLE)
            
            # Setup lighting for professional look
            glLightfv(GL_LIGHT0, GL_POSITION, [10.0, 15.0, 10.0, 1.0])
            glLightfv(GL_LIGHT0, GL_AMBIENT, [0.15, 0.15, 0.15, 1.0])
            glLightfv(GL_LIGHT0, GL_DIFFUSE, [0.7, 0.7, 0.7, 1.0])
            glLightfv(GL_LIGHT0, GL_SPECULAR, [0.3, 0.3, 0.3, 1.0])
            
            # Second light for fill lighting
            glLightfv(GL_LIGHT1, GL_POSITION, [-10.0, 15.0, -10.0, 1.0])
            glLightfv(GL_LIGHT1, GL_DIFFUSE, [0.4, 0.4, 0.4, 1.0])
            glLightfv(GL_LIGHT1, GL_SPECULAR, [0.2, 0.2, 0.2, 1.0])
            
            # Material properties for metallic look
            glMaterialfv(GL_FRONT, GL_SPECULAR, [0.5, 0.5, 0.5, 1.0])
            glMaterialf(GL_FRONT, GL_SHININESS, 50.0)
            
            # Enable smooth shading
            glShadeModel(GL_SMOOTH)
            
            # Create reusable quadric
            self._quadric = gluNewQuadric()
            gluQuadricNormals(self._quadric, GLU_SMOOTH)
    
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
            glClearColor(0.08, 0.08, 0.1, 1.0)
            glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
            glLoadIdentity()
            
            # Camera positioning (orbit around center)
            cam_x = self.camera_distance * math.cos(math.radians(self.camera_angle_y)) * math.cos(math.radians(self.camera_angle_x))
            cam_y = self.camera_distance * math.sin(math.radians(self.camera_angle_x))
            cam_z = self.camera_distance * math.sin(math.radians(self.camera_angle_y)) * math.cos(math.radians(self.camera_angle_x))
            
            gluLookAt(
                cam_x, cam_y, cam_z,
                0, 1, 0,  # Look at center of scene
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
        painter.fillRect(self.rect(), QColor(20, 20, 25))
        
        # Draw overlay
        self.draw_overlay(painter)
        painter.end()
    
    def draw_scene(self):
        """Draw the complete quality inspection cell"""
        # Apply shake effect if fault (subtle)
        if self.current_state['fault']:
            shake_intensity = 0.02
            shake_x = shake_intensity * math.sin(self.animation_time * 8)
            shake_y = shake_intensity * math.cos(self.animation_time * 7)
            glTranslatef(shake_x, shake_y, 0)
        
        # Draw floor grid
        self.draw_grid()
        
        # ===== INSPECTION CELL STRUCTURE =====
        # 1. Main inspection table (center)
        self.draw_box(0, 0.5, 0, 3, 0.1, 2, self.colors["inspection_table"])
        
        # 2. Conveyor belt (left to center)
        glPushMatrix()
        glTranslatef(-2.0, 0.3, 0)
        
        # Conveyor base
        self.draw_box(0, 0, 0, 5, 0.2, 0.8, self.colors["conveyor"])
        
        # Conveyor stripes (moving when busy)
        if not self.current_state['fault']:
            conveyor_offset = self.get_conveyor_offset()
            for i in range(-2, 3):
                stripe_x = i + conveyor_offset
                self.draw_box(stripe_x, 0.15, 0, 0.3, 0.05, 0.7, self.colors["conveyor_stripe"])
        
        glPopMatrix()
        
        # 3. Pass bin (right - green)
        self.draw_box(3.5, 0.5, 0, 1.5, 1.0, 1.2, self.colors["pass_bin"])
        
        # 4. Reject bin (left - red)
        self.draw_box(-3.5, 0.5, 0, 1.5, 1.0, 1.2, self.colors["reject_bin"])
        
        # 5. Sensor head above table
        sensor_offset, sensor_angle = self.get_sensor_animation()
        glPushMatrix()
        glTranslatef(0, 2.5 + sensor_offset, 0)
        glRotatef(sensor_angle, 0, 1, 0)
        
        # Sensor head base
        self.draw_box(0, 0, 0, 0.8, 0.3, 0.8, self.colors["sensor_head"])
        
        # Sensor lens
        self.draw_box(0, -0.1, 0, 0.3, 0.1, 0.3, self.colors["sensor_lens"])
        
        # Sensor light (on when busy)
        if self.current_state['busy'] and not self.current_state['fault']:
            light_color = (
                self.colors["cyan_pulse"][0],
                self.colors["cyan_pulse"][1],
                self.colors["cyan_pulse"][2],
                0.7 + 0.3 * math.sin(self.animation_time * 5)
            )
            glEnable(GL_BLEND)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            self.draw_box(0, -0.2, 0, 0.1, 0.1, 0.1, light_color)
            glDisable(GL_BLEND)
        
        glPopMatrix()
        
        # 6. Safety frame (transparent yellow)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        # Draw safety frame posts
        for x in [-4.5, 4.5]:
            for z in [-2.5, 2.5]:
                self.draw_box(x, 1.5, z, 0.1, 3.0, 0.1, self.colors["safety_yellow"])
        glDisable(GL_BLEND)
        
        # ===== STATUS INDICATORS =====
        # Ready/Busy/Fault LED panel
        led_y = 3.0
        if self.current_state['ready']:
            self.draw_box(-4.0, led_y, 2.2, 0.3, 0.3, 0.1, self.colors["green_led"])
        if self.current_state['busy']:
            self.draw_box(-4.0, led_y, 1.8, 0.3, 0.3, 0.1, self.colors["amber_led"])
        if self.current_state['fault']:
            # Pulsing red LED for fault
            blink = 0.5 + 0.5 * math.sin(self.animation_time * 3)
            fault_color = (
                self.colors["red_led"][0] * blink,
                self.colors["red_led"][1] * blink,
                self.colors["red_led"][2] * blink,
                1.0
            )
            self.draw_box(-4.0, led_y, 1.4, 0.3, 0.3, 0.1, fault_color)
        
        # ===== UPDATE ANIMATIONS =====
        self.compute_inspection_progress()
        self.update_part_animation()
        
        # ===== DRAW PART =====
        if self.part_visible:
            # Draw part as a small box
            self.draw_box(
                self.part_position[0],
                self.part_position[1],
                self.part_position[2],
                0.3, 0.3, 0.3,
                self.colors["part"]
            )
            
            # Add highlight if being inspected
            if self.part_on_table and self.current_state['busy']:
                highlight_alpha = 0.3 + 0.3 * math.sin(self.animation_time * 3)
                highlight_color = (
                    self.colors["cyan_pulse"][0],
                    self.colors["cyan_pulse"][1],
                    self.colors["cyan_pulse"][2],
                    highlight_alpha
                )
                glEnable(GL_BLEND)
                glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
                self.draw_box(
                    self.part_position[0],
                    self.part_position[1] + 0.2,
                    self.part_position[2],
                    0.35, 0.1, 0.35,
                    highlight_color
                )
                glDisable(GL_BLEND)
        
        # ===== PULSE EFFECTS =====
        # Done pulse
        done_pulse_active = self.animation_time - self.done_pulse_time < DONE_PULSE_MS / 1000.0
        if done_pulse_active:
            self.draw_done_pulse()
        
        # Accept pulse
        accept_pulse_active = self.animation_time - self.accept_pulse_time < ACCEPT_REJECT_PULSE_MS / 1000.0
        if accept_pulse_active:
            self.draw_accept_pulse()
        
        # Reject pulse
        reject_pulse_active = self.animation_time - self.reject_pulse_time < ACCEPT_REJECT_PULSE_MS / 1000.0
        if reject_pulse_active:
            self.draw_reject_pulse()
    
    def draw_grid(self):
        """Draw industrial floor grid"""
        if not PYOPENGL_AVAILABLE:
            return
            
        glColor4f(*self.colors["grid"])
        glLineWidth(1.0)
        glBegin(GL_LINES)
        
        size = 20.0
        steps = 40
        
        for i in range(-steps, steps + 1):
            x = i * (size / steps)
            glVertex3f(x, 0, -size/2)
            glVertex3f(x, 0, size/2)
            glVertex3f(-size/2, 0, x)
            glVertex3f(size/2, 0, x)
        
        glEnd()
        
        # Draw thicker center lines
        glLineWidth(2.0)
        glBegin(GL_LINES)
        glVertex3f(-size/2, 0.01, 0)
        glVertex3f(size/2, 0.01, 0)
        glVertex3f(0, 0.01, -size/2)
        glVertex3f(0, 0.01, size/2)
        glEnd()
    
    def get_result_display_info(self):
        """Get current result text and color based on state and pulses"""
        # Check for done pulse first (highest priority for display)
        done_pulse_active = self.animation_time - self.done_pulse_time < DONE_PULSE_MS / 1000.0
        
        if self.current_state['fault']:
            return "FAULT ⚠", QColor(255, 0, 0)
        elif self.current_state['busy']:
            return "INSPECTING...", QColor(255, 215, 0)
        elif done_pulse_active:
            if self.decision_latched == "pass":
                return "COMPLETE: ACCEPT ✓", QColor(0, 255, 0)
            elif self.decision_latched == "reject":
                return "COMPLETE: REJECT ✗", QColor(255, 0, 0)
            else:
                return "COMPLETE", QColor(200, 200, 200)
        else:
            # Idle state - show last result
            if self.current_state['last_accept'] == 1:
                return "LAST RESULT: ACCEPT", QColor(150, 255, 150)
            elif self.current_state['last_accept'] == 0:
                return "LAST RESULT: REJECT", QColor(255, 150, 150)
            else:
                return "WAITING", QColor(200, 200, 200)
    
    def draw_overlay(self, painter):
        """Draw overlay text with system information"""
        # Setup font
        font = QFont("Monospace", 10)
        painter.setFont(font)
        painter.setPen(QPen(QColor(240, 240, 240), 1))
        
        # Mode and state - use current_mode from MainWindow
        mode_text = f"Mode: {self.mode}"
        state_text = f"State: R={int(self.current_state['ready'])} B={int(self.current_state['busy'])} D={int(self.current_state['done'])} F={int(self.current_state['fault'])}"
        
        # Get result text and color
        result_text, result_color = self.get_result_display_info()
        
        # Progress
        progress_text = f"Inspection: {self.inspection_progress*100:.0f}%"
        
        # Cycle time
        cycle_text = f"Cycle: {self.current_state['cycle_time_ms'] or 'N/A'} ms"
        
        # Accept/reject counters
        counter_text = f"Accept: {self.current_state['accept']} | Reject: {self.current_state['reject']}"
        
        # Pulse status
        done_pulse_active = self.animation_time - self.done_pulse_time < DONE_PULSE_MS / 1000.0
        accept_pulse_active = self.animation_time - self.accept_pulse_time < ACCEPT_REJECT_PULSE_MS / 1000.0
        reject_pulse_active = self.animation_time - self.reject_pulse_time < ACCEPT_REJECT_PULSE_MS / 1000.0
        
        pulse_text = f"Pulses: D={'Y' if done_pulse_active else 'N'} A={'Y' if accept_pulse_active else 'N'} R={'Y' if reject_pulse_active else 'N'}"
        
        # Draw text with background for readability
        y_offset = 20
        line_height = 20
        
        texts = [mode_text, state_text, result_text, progress_text, cycle_text, counter_text, pulse_text]
        
        colors = [None, None, result_color, None, None, None, None]
        
        for i, (text, color) in enumerate(zip(texts, colors)):
            # Draw background rectangle
            metrics = QFontMetrics(font)
            text_width = metrics.horizontalAdvance(text)
            painter.fillRect(10, y_offset + i * line_height - 15, 
                           text_width + 10, line_height, 
                           QColor(0, 0, 0, 200))
            
            # Draw text with optional color
            if color:
                painter.setPen(QPen(color, 1))
            else:
                painter.setPen(QPen(QColor(240, 240, 240), 1))
            painter.drawText(15, y_offset + i * line_height, text)
    
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
        self.camera_distance = max(8.0, min(30.0, self.camera_distance - delta * 0.01))
        self.update()
    
    def update_state(self, event: ST5Event, mode: str = "LIVE", visual_time_s: float = None):
        """Update the current state from an event"""
        old_busy = self.current_state.get('busy', False)
        old_done = self.current_state.get('_last_done', False)
        old_accept = self.current_state.get('accept', 0)
        old_reject = self.current_state.get('reject', 0)
        
        self.current_state = {
            'ready': event.ready,
            'busy': event.busy,
            'done': event.done,
            'fault': event.fault,
            'cycle_time_ms': event.cycle_time_ms,
            'accept': event.accept,
            'reject': event.reject,
            'last_accept': event.last_accept,
            'extra': event.extra
        }
        
        self.mode = mode
        
        # Use provided visual_time_s or fallback to current animation time (deterministic)
        if visual_time_s is None:
            visual_time_s = self.animation_time
        
        # Check for busy rising edge
        if event.busy and not old_busy:
            # Inspection cycle starting
            self.inspection_active = True
            self.inspection_start = visual_time_s
            
            # Reset progress
            self.inspection_progress = 0.0
            
            # Calculate cycle duration from cycle_time_ms if available
            if event.cycle_time_ms:
                self.cycle_s = event.cycle_time_ms / 1000.0
                # Clamp to reasonable range (0.5-5.0 seconds)
                self.cycle_s = max(0.5, min(5.0, self.cycle_s))
            else:
                self.cycle_s = self.base_cycle_s
            
            self.inspection_end = visual_time_s + self.cycle_s
        
        # Handle case where visualizer attaches mid-cycle (busy already True but not active)
        elif event.busy and not self.inspection_active:
            # We missed the busy rising edge, start inspection anyway
            self.inspection_active = True
            
            # Use cycle time to determine duration
            if event.cycle_time_ms:
                self.cycle_s = event.cycle_time_ms / 1000.0
                self.cycle_s = max(0.5, min(5.0, self.cycle_s))
            else:
                self.cycle_s = self.base_cycle_s
            
            # Estimate we're partway through inspection
            self.inspection_start = visual_time_s - 0.5 * self.cycle_s  # Assume halfway
            self.inspection_end = visual_time_s + 0.5 * self.cycle_s
        
        # Pulse triggers from counter increases
        if event.accept > old_accept:
            self.accept_pulse_time = visual_time_s
        if event.reject > old_reject:
            self.reject_pulse_time = visual_time_s
        
        # Done pulse and decision latching
        if event.done and not old_done:
            self.done_pulse_time = visual_time_s
            # Latch decision on done rising edge
            if event.last_accept == 1:
                self.decision_latched = "pass"
            else:
                self.decision_latched = "reject"
        
        # On fault: stop everything
        if event.fault:
            self.inspection_active = False
            self.inspection_progress = 0.0
            self.part_visible = False
        
        # Store previous counters for next delta calculation
        self.accept_count_prev = event.accept
        self.reject_count_prev = event.reject
        
        # Store last done state
        self.current_state['_last_done'] = event.done
        
        # Update animation time
        self.animation_time = visual_time_s
        
        self.update()

# ===== MAIN WINDOW =====
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ST5 – Quality Inspection")  # Shorter title
        self.setGeometry(100, 100, 1400, 900)
        
        # Data
        self.log_path = None
        self.replay_events = []
        self.replay_timestamps = []  # Fast lookup array
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
            # In LIVE mode, use wall time
            return time.time()
        else:
            # In REPLAY mode, use scaled VSI time only (no wall-clock)
            if not self.replay_events:
                return (self.current_time_ns - self.replay_min_time) / 1e9
            
            # Fast lookup using bisect
            idx = bisect.bisect_right(self.replay_timestamps, self.current_time_ns) - 1
            if idx >= 0:
                # Use VSI time scaled to seconds for replay
                return (self.replay_events[idx].t_ns - self.replay_min_time) / 1e9
            
            return (self.current_time_ns - self.replay_min_time) / 1e9
    
    def init_ui(self):
        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Main layout
        main_layout = QVBoxLayout(central_widget)
        
        # Top info bar
        info_layout = QHBoxLayout()
        self.log_info_label = QLabel("Searching for ST5 Quality Inspection log...")
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
        self.gl_widget = ST5OpenGLWidget()
        content_layout.addWidget(self.gl_widget, 3)
        
        # Status panel (right)
        status_panel = QVBoxLayout()
        
        # Batch/Recipe info
        batch_group = QGroupBox("Batch / Recipe Info")
        batch_layout = QVBoxLayout()
        
        self.batch_id_label = QLabel("Batch ID: N/A")
        self.recipe_id_label = QLabel("Recipe ID: N/A")
        self.part_count_label = QLabel("Part Count: N/A")
        
        for label in [self.batch_id_label, self.recipe_id_label, self.part_count_label]:
            label.setStyleSheet("font-family: monospace; padding: 2px;")
            batch_layout.addWidget(label)
        
        batch_group.setLayout(batch_layout)
        status_panel.addWidget(batch_group)
        
        # ST5 State
        state_group = QGroupBox("ST5 State")
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
        
        # Inspection Result
        result_group = QGroupBox("Inspection Result")
        result_layout = QVBoxLayout()
        
        self.result_label = QLabel("Result: WAITING")
        self.result_label.setStyleSheet("font-family: monospace; padding: 2px; color: #CCCCCC;")
        
        self.accept_label = QLabel("Accept Count: 0")
        self.reject_label = QLabel("Reject Count: 0")
        self.last_accept_label = QLabel("Last Decision: N/A")
        self.done_pulse_label = QLabel("Done Pulse: No")
        
        for label in [self.result_label, self.accept_label, self.reject_label, 
                     self.last_accept_label, self.done_pulse_label]:
            label.setStyleSheet("font-family: monospace; padding: 2px;")
            result_layout.addWidget(label)
        
        result_group.setLayout(result_layout)
        status_panel.addWidget(result_group)
        
        # Progress bar for inspection
        progress_group = QGroupBox("Inspection Progress")
        progress_layout = QVBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        progress_layout.addWidget(self.progress_bar)
        progress_group.setLayout(progress_layout)
        status_panel.addWidget(progress_group)
        
        # Extra Signals
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
    
    def load_live_snapshot(self) -> Tuple[Optional[ST5Event], int]:
        """Load snapshot of last N lines from log file, return latest event and raw count"""
        if not self.log_path or not os.path.exists(self.log_path):
            return None, 0
        
        try:
            with open(self.log_path, 'r', encoding='utf-8', errors='ignore') as f:
                # Read last N lines efficiently
                lines = collections.deque(f, maxlen=SNAPSHOT_LINES)
            
            parser = ST5LogParser()
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
        """Discover and load ST5 log file"""
        # Stop current worker
        self._stop_tail_worker()
        
        # Find log
        self.log_path = LogDiscoverer.find_st5_log()
        
        if self.log_path:
            base_name = os.path.basename(self.log_path)
            self.log_info_label.setText(f"ST5 Log: {base_name}")
            
            # Reset counters
            self.raw_event_count = 0
            self.accepted_event_count = 0
            self.update_event_count_label()
            
            if self.is_live_mode:
                self.switch_to_live()
            else:
                self.switch_to_replay()
        else:
            self.log_info_label.setText("No ST5 log found")
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
        self.replay_timestamps = []
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
                'accept': latest_event.accept,
                'reject': latest_event.reject,
                'last_accept': latest_event.last_accept,
                'extra': latest_event.extra.copy() if latest_event.extra else {}
            }
            
            # Get current visual time for animation
            visual_time_s = time.time()
            self.gl_widget.animation_time = visual_time_s
            
            # Update widget state with visual time
            self.gl_widget.update_state(latest_event, self.current_mode, visual_time_s)
            self.update_display(latest_event, from_snapshot=True)
            
            # Update debug label with snapshot info
            self.debug_label.setText(f"Mode: LIVE | Snapshot: {latest_event.t_ns/1e9:.2f}s | R={latest_event.ready} B={latest_event.busy} D={latest_event.done} F={latest_event.fault}")
            
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
            
            # Get initial visual time (using VSI time only)
            visual_time_s = self.get_visual_time_s()
            self.gl_widget.animation_time = visual_time_s
            
            # Force immediate display update with initial state
            self.update_states_from_replay()
            
            # Update debug label for REPLAY mode
            if self.replay_events:
                self.debug_label.setText(f"Mode: REPLAY | Event: {self.replay_events[0].t_ns/1e9:.2f}s | R={self.replay_events[0].ready} B={self.replay_events[0].busy} D={self.replay_events[0].done} F={self.replay_events[0].fault}")
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
            parser = ST5LogParser()
            
            for line in lines:
                event = parser.parse_line(line)
                if event:
                    events.append(event)
            
            # Sort by time and limit
            events.sort(key=lambda x: x.t_ns)
            self.replay_events = events[-MAX_EVENTS:]
            self.replay_timestamps = [e.t_ns for e in self.replay_events]  # Fast lookup array
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
                    'accept': event.accept,
                    'reject': event.reject,
                    'last_accept': event.last_accept,
                    'extra': event.extra.copy() if event.extra else {}
                }
                
                if last_state_snapshot is None or self._state_changed(last_state_snapshot, new_state):
                    last_state_snapshot = new_state
                    self.accepted_event_count += 1
            
            self.update_event_count_label()
            
            print(f"Loaded {len(self.replay_events)} ST5 events for replay")
            print(f"Accepted events (state changes): {self.accepted_event_count}")
            if self.replay_events:
                print(f"Time range: {self.replay_events[0].t_ns/1e9:.3f}s to {self.replay_events[-1].t_ns/1e9:.3f}s")
            
        except Exception as e:
            print(f"Error loading {self.log_path}: {e}")
            traceback.print_exc()
            self.replay_events = []
            self.replay_timestamps = []
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
        """Update states based on current replay time (fast O(log n) lookup)"""
        if not self.replay_events:
            return
        
        # Fast lookup using bisect
        idx = bisect.bisect_right(self.replay_timestamps, self.current_time_ns) - 1
        if idx >= 0:
            current_event = self.replay_events[idx]
            
            # Get visual time for animation (using VSI time only)
            visual_time_s = self.get_visual_time_s()
            
            # Update widget state with visual time
            self.gl_widget.update_state(current_event, self.current_mode, visual_time_s)
            
            # Update display
            self.update_display(current_event, from_snapshot=False)
            
            # Update debug label for REPLAY mode
            self.debug_label.setText(f"Mode: REPLAY | Event: {current_event.t_ns/1e9:.2f}s | R={current_event.ready} B={current_event.busy} D={current_event.done} F={current_event.fault}")
    
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
                self.time_label.setText("LIVE")
        else:
            # Show replay time in seconds
            time_s = (self.current_time_ns - self.replay_min_time) / 1e9
            total_s = (self.replay_max_time - self.replay_min_time) / 1e9
            self.time_label.setText(f"{time_s:07.3f}s / {total_s:07.3f}s")
    
    def process_new_event(self, event: ST5Event):
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
            'accept': event.accept,
            'reject': event.reject,
            'last_accept': event.last_accept,
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
            
            # Get visual time for animation
            visual_time_s = time.time()
            
            # Update widget state with visual time
            self.gl_widget.update_state(event, self.current_mode, visual_time_s)
            self.update_display(event, from_snapshot=False)
            
            self.debug_label.setText(f"Mode: LIVE | Accepted: {event.t_ns/1e9:.2f}s | R={event.ready} B={event.busy} D={event.done} F={event.fault}")
            self.last_event_ignored = False
        else:
            # State didn't change
            self.debug_label.setText(f"Mode: LIVE | No change: {event.t_ns/1e9:.2f}s | R={event.ready} B={event.busy} D={event.done} F={event.fault}")
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
            visual_time_s = time.time()
            self.gl_widget.update_state(latest_event, self.current_mode, visual_time_s)
            self.update_display(latest_event, from_snapshot=False)
    
    def _state_changed(self, old_state: dict, new_state: dict) -> bool:
        """Check if state has meaningfully changed"""
        # Check basic ST5 states
        for key in ['ready', 'busy', 'done', 'fault', 'accept', 'reject', 'last_accept']:
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
    
    def update_display(self, event: ST5Event, from_snapshot: bool = False):
        """Update all displays from event - THIS IS THE SINGLE SOURCE OF TRUTH"""
        # Update status labels
        self.ready_label.setText(f"Ready: {event.ready}")
        self.busy_label.setText(f"Busy: {event.busy}")
        self.done_label.setText(f"Done: {event.done}")
        self.fault_label.setText(f"Fault: {event.fault}")
        
        if event.cycle_time_ms:
            self.cycle_label.setText(f"Cycle Time: {event.cycle_time_ms:.1f} ms")
        else:
            self.cycle_label.setText("Cycle Time: N/A")
        
        # Update accept/reject counters
        self.accept_label.setText(f"Accept Count: {event.accept}")
        self.reject_label.setText(f"Reject Count: {event.reject}")
        
        # Update last decision
        if event.last_accept == 1:
            self.last_accept_label.setText("Last Decision: ACCEPT")
        elif event.last_accept == 0:
            self.last_accept_label.setText("Last Decision: REJECT")
        else:
            self.last_accept_label.setText("Last Decision: N/A")
        
        # Update batch/recipe info from extras
        batch_id = event.extra.get('batch_id', 'N/A')
        recipe_id = event.extra.get('recipe_id', 'N/A')
        part_count = event.extra.get('part_count', 'N/A')
        
        self.batch_id_label.setText(f"Batch ID: {batch_id}")
        self.recipe_id_label.setText(f"Recipe ID: {recipe_id}")
        self.part_count_label.setText(f"Part Count: {part_count}")
        
        # Update progress bar
        progress = int(self.gl_widget.inspection_progress * 100)
        self.progress_bar.setValue(progress)
        
        # Update extra KPIs
        # Clear existing widgets
        for i in reversed(range(self.extra_layout.count())):
            widget = self.extra_layout.itemAt(i).widget()
            if widget:
                widget.deleteLater()
        
        # Add new KPI labels (max 6)
        extra_items = list(event.extra.items())[:6]
        for key, value in extra_items:
            label = QLabel(f"{key}: {value}")
            label.setStyleSheet("font-family: monospace; padding: 2px;")
            self.extra_layout.addWidget(label)
        
        # Hide extra group if no extra signals
        self.extra_group.setVisible(len(extra_items) > 0)
    
    def refresh_pulse_displays(self):
        """Refresh pulse-related displays based on current animation time"""
        # Update done pulse label
        done_pulse_active = self.gl_widget.animation_time - self.gl_widget.done_pulse_time < DONE_PULSE_MS / 1000.0
        self.done_pulse_label.setText(f"Done Pulse: {'Yes' if done_pulse_active else 'No'}")
        
        # Update result label based on current state and pulses
        result_text, result_color = self.gl_widget.get_result_display_info()
        self.result_label.setText(result_text)
        self.result_label.setStyleSheet(f"font-family: monospace; padding: 2px; color: {result_color.name()};")
    
    def update_animation(self):
        """Update animation based on timer"""
        # Get current visual time (deterministic)
        visual_time_s = self.get_visual_time_s()
        
        # Update animation time in gl_widget
        self.gl_widget.animation_time = visual_time_s
        
        # Compute inspection progress using animation time
        self.gl_widget.compute_inspection_progress()
        
        # Update progress bar
        progress = int(self.gl_widget.inspection_progress * 100)
        self.progress_bar.setValue(progress)
        
        # Refresh pulse displays (for LIVE mode visual updates)
        self.refresh_pulse_displays()
        
        if self.is_playing and not self.is_live_mode and self.replay_events:
            # Advance replay time (deterministic - no wall clock)
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
    # Set OpenGL compatibility profile
    fmt = QSurfaceFormat()
    fmt.setVersion(OPENGL_MAJOR_VERSION, OPENGL_MINOR_VERSION)
    fmt.setProfile(QSurfaceFormat.CompatibilityProfile)
    fmt.setDepthBufferSize(24)
    fmt.setSamples(8)  # 8x MSAA for better quality
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