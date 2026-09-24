#!/usr/bin/env python3
"""
ST1 Visualizer - Professional industrial robot visualization for ST1 Component Kitting logs

CHANGES:
1. Fixed cylinder axis bug: Added glRotatef(-90, 1, 0, 0) in draw_cylinder() to align gluCylinder's +Z axis with +Y
2. Reused GLU quadrics: Created single quadric in initializeGL(), reused in draw_cylinder/draw_sphere, removed per-frame creation/deletion
3. Made REPLAY deterministic: Removed time.time() fallback in update_state(), using self.animation_time instead
4. Fixed visual_time_s consistency: MainWindow now passes visual_time_s properly in all call paths
5. Added local blending for Secure/Solder effect with alpha transparency
6. Cleaned up quadric in widget destructor
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
SEARCH_ROOT = "."
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
class ST1Event:
    t_ns: int
    ready: bool = False
    busy: bool = False
    done: bool = False
    fault: bool = False
    cycle_time_ms: Optional[float] = None
    extra: Dict[str, Any] = field(default_factory=dict)

# ===== CARRY-FORWARD PARSER =====
class ST1LogParser:
    """Carry-forward parser for ST1 Component Kitting logs"""
    
    def __init__(self):
        self.reset()
    
    def reset(self):
        """Reset parser state"""
        self.last_vsi_time_ns = None
        self.synthetic_time_ns = 0
        
        # Carry-forward state
        self.carried_state = {
            'ready': None,  # None means unknown/not seen yet
            'busy': None,
            'done': None,
            'fault': None
        }
        self.carried_cycle_time = None
        self.carried_extra = {}
        
        # Have we ever seen any state value?
        self.has_seen_any_state = False
        
        # Patterns
        self.vsi_time_pattern = re.compile(r"VSI\s+time\s*:\s*(\d+)\s*ns", re.IGNORECASE)
        self.cycle_time_pattern = re.compile(r"cycle[_\s]*time[_\s]*[:=]?\s*([\d.]+)\s*ms", re.IGNORECASE)
        self.key_value_pattern = re.compile(r"(\w+)[_\s]*[:=]\s*([\w.-]+)")
        
        # State patterns with values ONLY - no boolean fallback
        self.state_patterns = {
            "ready": re.compile(r"\bready\b\s*[:=]\s*(\d+)", re.IGNORECASE),
            "busy": re.compile(r"\bbusy\b\s*[:=]\s*(\d+)", re.IGNORECASE),
            "done": re.compile(r"\bdone\b\s*[:=]\s*(\d+)", re.IGNORECASE),
            "fault": re.compile(r"\bfault\b\s*[:=]\s*(\d+)", re.IGNORECASE),
        }
    
    def seed_from_event(self, event: ST1Event):
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
    
    def parse_line(self, line: str) -> Optional[ST1Event]:
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
        
        # Check for state updates
        for state_name, pattern in self.state_patterns.items():
            match = pattern.search(line)
            if match:
                try:
                    value = int(match.group(1))
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
        
        # Extract key-value pairs
        current_line_extras = {}
        for match in self.key_value_pattern.finditer(line):
            key, value = match.groups()
            # Skip already parsed keys
            if key.lower() in ['vsi', 'time', 'cycle', 'ready', 'busy', 'done', 'fault']:
                continue
            # Try to convert to number if possible
            try:
                if '.' in value:
                    value = float(value)
                else:
                    value = int(value)
            except ValueError:
                pass
            current_line_extras[key] = value
        
        # Update carried extras with current line extras
        if current_line_extras:
            for key, value in current_line_extras.items():
                self.carried_extra[key] = value
            had_signal = True
        
        # Emit an event if this line had any signal (even if values didn't change)
        if had_signal:
            # Convert carried state to booleans (None -> False)
            ready = bool(self.carried_state['ready']) if self.carried_state['ready'] is not None else False
            busy = bool(self.carried_state['busy']) if self.carried_state['busy'] is not None else False
            done = bool(self.carried_state['done']) if self.carried_state['done'] is not None else False
            fault = bool(self.carried_state['fault']) if self.carried_state['fault'] is not None else False
            
            event = ST1Event(
                t_ns=timestamp,
                ready=ready,
                busy=busy,
                done=done,
                fault=fault,
                cycle_time_ms=self.carried_cycle_time,
                extra=copy.deepcopy(self.carried_extra)
            )
            return event
        
        return None
    
    def get_current_state(self) -> Optional[ST1Event]:
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
        
        return ST1Event(
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
    def find_st1_log() -> Optional[str]:
        """Find newest ST1 log file matching pattern (more robust)"""
        st1_files = []
        
        # Search recursively from SEARCH_ROOT
        for root, dirs, files in os.walk(SEARCH_ROOT):
            for file in files:
                file_lower = file.lower()
                # Check if file contains "st1" and ends with ".log"
                if "st1" in file_lower and file_lower.endswith('.log'):
                    full_path = os.path.join(root, file)
                    mtime = os.path.getmtime(full_path)
                    
                    # Score: prefer files containing "componentkitting"
                    score = 0
                    if "componentkitting" in file_lower:
                        score = 1
                    
                    st1_files.append((score, mtime, full_path))
        
        if not st1_files:
            return None
        
        # Sort by score (higher first), then by mtime (newest first)
        st1_files.sort(key=lambda x: (-x[0], -x[1]))
        selected_file = st1_files[0][2]
        print(f"Selected ST1 log: {selected_file}")
        return selected_file

# ===== TAIL WORKER =====
class LogTailWorker(QThread):
    new_event = Signal(ST1Event)  # Emitted for every signal line
    activity_detected = Signal()  # Emitted when any line is read
    file_reopened = Signal()  # Emitted when file is reopened (rotation/truncation)
    
    def __init__(self, log_path: str, seed_event: Optional[ST1Event] = None):
        super().__init__()
        self.log_path = log_path
        self.seed_event = seed_event
        self.running = True
        self.file_handle = None
        self.file_position = 0
        self.file_inode = None
        self.parser = ST1LogParser()
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
            self.parser = ST1LogParser()
            
            # Seed the parser if we have a seed event
            if seed and self.seed_event:
                self.parser.seed_from_event(self.seed_event)  # FIXED: was seed_event
                
        except Exception as e:
            print(f"Error opening {self.log_path}: {e}")
            self.file_handle = None
    
    def reseed_parser(self, seed_event: ST1Event):
        """Reseed the parser with a new event (e.g., after file rotation)"""
        self.seed_event = seed_event
        self.parser.seed_from_event(seed_event)
    
    def stop(self):
        """Stop the worker thread"""
        self.running = False
        if self.isRunning():
            self.wait()

# ===== OPENGL WIDGET =====
class ST1OpenGLWidget(QOpenGLWidget):
    """Professional OpenGL visualization for ST1 with industrial robot cell"""
    
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
        
        # Stage animation parameters
        self.base_stage_s = 2.5  # Default seconds per stage
        self.stage_s = self.base_stage_s  # Current stage duration
        self.proc_active = False  # Is processing active?
        self.proc_start = 0.0  # When processing started (in visual_time_s)
        self.proc_end = 0.0  # When processing ends (4 stages)
        self.active_stage_index = -1  # -1 means no stage active
        self.stage_progress = 0.0  # Progress within current stage (0.0 to 1.0)
        
        # ST1 stage names
        self.stage_names = ["Pick Component", "Place/Mount", "Secure/Solder", "Kit Complete"]
        
        # Colors - restrained industrial palette
        self.colors = {
            "dark_gray": (0.15, 0.15, 0.17, 1.0),
            "medium_gray": (0.25, 0.25, 0.27, 1.0),
            "light_gray": (0.35, 0.35, 0.37, 1.0),
            "metal_gray": (0.4, 0.42, 0.45, 1.0),
            "dark_blue": (0.1, 0.15, 0.25, 1.0),
            "green_led": (0.0, 0.8, 0.0, 1.0),
            "amber_led": (1.0, 0.6, 0.0, 1.0),
            "red_led": (0.8, 0.0, 0.0, 1.0),
            "cyan_pulse": (0.0, 1.0, 1.0, 1.0),
            "grid": (0.25, 0.25, 0.28, 1.0),
            "conveyor": (0.2, 0.18, 0.16, 1.0),
            "safety_yellow": (0.8, 0.8, 0.1, 0.3),
            "bin_green": (0.1, 0.3, 0.1, 1.0),
            "bin_blue": (0.1, 0.2, 0.3, 1.0),
            "tray_gray": (0.5, 0.5, 0.5, 1.0),
            "component_gold": (0.8, 0.7, 0.2, 1.0),
            "robot_base": (0.3, 0.3, 0.32, 1.0),
            "robot_link": (0.4, 0.42, 0.44, 1.0),
            "robot_joint": (0.5, 0.52, 0.54, 1.0),
            "gripper": (0.6, 0.62, 0.64, 1.0),
            "active_glow": (0.9, 0.7, 0.2, 1.0),
        }
        
        # Camera
        self.camera_distance = 18.0
        self.camera_angle_x = 35.0
        self.camera_angle_y = 45.0
        self.last_mouse_pos = None
        
        # Robot joint angles (degrees)
        self.joint_angles = {
            'shoulder_yaw': 0.0,
            'shoulder_pitch': 0.0,
            'elbow_pitch': 0.0,
            'wrist_pitch': 0.0,
            'gripper_open': 0.0  # 0 = closed, 1 = open
        }
        
        # Keyframes for each stage (start and end angles)
        self.keyframes = [
            # Stage 0: Pick Component
            {
                'start': {'shoulder_yaw': -90, 'shoulder_pitch': 20, 'elbow_pitch': 60, 'wrist_pitch': 30, 'gripper_open': 1.0},
                'end': {'shoulder_yaw': -90, 'shoulder_pitch': 45, 'elbow_pitch': 45, 'wrist_pitch': 0, 'gripper_open': 0.0}
            },
            # Stage 1: Place/Mount
            {
                'start': {'shoulder_yaw': -45, 'shoulder_pitch': 30, 'elbow_pitch': 60, 'wrist_pitch': 15, 'gripper_open': 0.0},
                'end': {'shoulder_yaw': 45, 'shoulder_pitch': 45, 'elbow_pitch': 45, 'wrist_pitch': -15, 'gripper_open': 1.0}
            },
            # Stage 2: Secure/Solder
            {
                'start': {'shoulder_yaw': 30, 'shoulder_pitch': 45, 'elbow_pitch': 45, 'wrist_pitch': 0, 'gripper_open': 0.0},
                'end': {'shoulder_yaw': 30, 'shoulder_pitch': 45, 'elbow_pitch': 45, 'wrist_pitch': 0, 'gripper_open': 0.0}
            },
            # Stage 3: Kit Complete
            {
                'start': {'shoulder_yaw': 0, 'shoulder_pitch': 60, 'elbow_pitch': 30, 'wrist_pitch': 0, 'gripper_open': 0.0},
                'end': {'shoulder_yaw': 0, 'shoulder_pitch': 60, 'elbow_pitch': 30, 'wrist_pitch': 0, 'gripper_open': 0.0}
            }
        ]
        
        # Component tracking
        self.component_pos = [0, 0, 0]
        self.component_attached = False
        self.component_visible = False
        
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
    
    def compute_active_stage(self):
        """Compute which stage is active and its progress based on animation_time"""
        if not self.proc_active:
            self.active_stage_index = -1
            self.stage_progress = 0.0
            return -1
        
        # If busy but timer ended, stay in stage 3 (Kit Complete) with looping animation
        if self.animation_time >= self.proc_end:
            if self.current_state['busy']:
                # Stay in stage 3 with looping
                self.active_stage_index = 3
                # Loop every 2 seconds (using animation_time for determinism)
                self.stage_progress = (math.sin(self.animation_time * math.pi) * 0.5 + 0.5) * 0.7  # 0.0 to 0.7 range
                return 3
            else:
                self.proc_active = False
                self.active_stage_index = -1
                self.stage_progress = 0.0
                return -1
        
        elapsed = self.animation_time - self.proc_start
        self.active_stage_index = min(3, int(elapsed / self.stage_s))
        
        # Calculate progress within current stage
        stage_start_time = self.proc_start + self.active_stage_index * self.stage_s
        self.stage_progress = min(1.0, max(0.0, (self.animation_time - stage_start_time) / self.stage_s))
        
        return self.active_stage_index
    
    def update_joint_angles(self):
        """Update robot joint angles based on current stage and progress"""
        if self.active_stage_index < 0 or self.active_stage_index > 3:
            # Idle position
            idle_time = self.animation_time
            self.joint_angles['shoulder_yaw'] = 15 * math.sin(idle_time * 0.3)
            self.joint_angles['shoulder_pitch'] = 30 + 5 * math.sin(idle_time * 0.4)
            self.joint_angles['elbow_pitch'] = 45 + 5 * math.sin(idle_time * 0.5)
            self.joint_angles['wrist_pitch'] = 10 * math.sin(idle_time * 0.6)
            self.joint_angles['gripper_open'] = 0.0
            return
        
        # Get keyframes for current stage
        keyframe = self.keyframes[self.active_stage_index]
        start = keyframe['start']
        end = keyframe['end']
        
        # Use smoothstep for smooth interpolation
        t = self.smoothstep(self.stage_progress)
        
        # Interpolate between start and end angles
        self.joint_angles['shoulder_yaw'] = start['shoulder_yaw'] + (end['shoulder_yaw'] - start['shoulder_yaw']) * t
        self.joint_angles['shoulder_pitch'] = start['shoulder_pitch'] + (end['shoulder_pitch'] - start['shoulder_pitch']) * t
        self.joint_angles['elbow_pitch'] = start['elbow_pitch'] + (end['elbow_pitch'] - start['elbow_pitch']) * t
        self.joint_angles['wrist_pitch'] = start['wrist_pitch'] + (end['wrist_pitch'] - start['wrist_pitch']) * t
        self.joint_angles['gripper_open'] = start['gripper_open'] + (end['gripper_open'] - start['gripper_open']) * t
        
        # Special case for stage 2 (Secure/Solder) - add vibration
        if self.active_stage_index == 2:
            vibration = math.sin(self.animation_time * 20) * 5.0
            self.joint_angles['wrist_pitch'] += vibration
        
        # Special case for stage 3 (Kit Complete) - subtle idle motion
        elif self.active_stage_index == 3 and self.stage_progress > 0.5:
            idle_time = self.animation_time
            self.joint_angles['shoulder_yaw'] += 5 * math.sin(idle_time * 1.0)
            self.joint_angles['shoulder_pitch'] += 3 * math.sin(idle_time * 1.2)
    
    def forward_kinematics(self):
        """Calculate end effector position from joint angles"""
        # Simple forward kinematics for visualization
        # Base at (0, 0.5, 0)
        # Link lengths
        L1 = 1.0  # Shoulder to elbow
        L2 = 0.8  # Elbow to wrist
        L3 = 0.3  # Wrist to gripper tip
        
        # Convert angles to radians
        shoulder_yaw = math.radians(self.joint_angles['shoulder_yaw'])
        shoulder_pitch = math.radians(self.joint_angles['shoulder_pitch'])
        elbow_pitch = math.radians(self.joint_angles['elbow_pitch'])
        wrist_pitch = math.radians(self.joint_angles['wrist_pitch'])
        
        # Calculate positions
        x = L1 * math.cos(shoulder_pitch) * math.cos(shoulder_yaw) + L2 * math.cos(shoulder_pitch + elbow_pitch) * math.cos(shoulder_yaw)
        y = 0.5 + L1 * math.sin(shoulder_pitch) + L2 * math.sin(shoulder_pitch + elbow_pitch)
        z = L1 * math.cos(shoulder_pitch) * math.sin(shoulder_yaw) + L2 * math.cos(shoulder_pitch + elbow_pitch) * math.sin(shoulder_yaw)
        
        # Add wrist offset
        x += L3 * math.cos(shoulder_pitch + elbow_pitch + wrist_pitch) * math.cos(shoulder_yaw)
        y += L3 * math.sin(shoulder_pitch + elbow_pitch + wrist_pitch)
        z += L3 * math.cos(shoulder_pitch + elbow_pitch + wrist_pitch) * math.sin(shoulder_yaw)
        
        return [x, y, z]
    
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
    
    def draw_robot(self):
        """Draw the industrial robot arm"""
        if not PYOPENGL_AVAILABLE:
            return
        
        glPushMatrix()
        # Base position
        glTranslatef(0, 0.5, 0)
        
        # Determine robot color based on state
        if self.current_state['fault']:
            base_color = self.colors["red_led"]
            link_color = self.colors["robot_link"]
        elif self.proc_active:
            base_color = self.colors["active_glow"]
            link_color = self.colors["robot_link"]
        else:
            base_color = self.colors["robot_base"]
            link_color = self.colors["robot_link"]
        
        # 1. Base pedestal
        self.draw_cylinder(0, 0, 0, 0.4, 1.0, base_color)
        
        # 2. Shoulder yaw rotation
        glRotatef(self.joint_angles['shoulder_yaw'], 0, 1, 0)
        
        # 3. Shoulder joint (sphere)
        self.draw_sphere(0, 0.5, 0, 0.15, self.colors["robot_joint"])
        
        # 4. Shoulder pitch rotation
        glRotatef(self.joint_angles['shoulder_pitch'], 0, 0, 1)
        
        # 5. Upper arm (from shoulder to elbow)
        glPushMatrix()
        glTranslatef(0, 0.5, 0)
        self.draw_cylinder(0, 0, 0, 0.1, 1.0, link_color)
        
        # 6. Elbow joint (sphere)
        glTranslatef(0, 1.0, 0)
        self.draw_sphere(0, 0, 0, 0.12, self.colors["robot_joint"])
        
        # 7. Elbow pitch rotation
        glRotatef(self.joint_angles['elbow_pitch'], 0, 0, 1)
        
        # 8. Forearm (from elbow to wrist)
        glTranslatef(0, 0, 0)
        self.draw_cylinder(0, 0, 0, 0.08, 0.8, link_color)
        
        # 9. Wrist joint (sphere)
        glTranslatef(0, 0.8, 0)
        self.draw_sphere(0, 0, 0, 0.1, self.colors["robot_joint"])
        
        # 10. Wrist pitch rotation
        glRotatef(self.joint_angles['wrist_pitch'], 0, 0, 1)
        
        # 11. Wrist to gripper
        glTranslatef(0, 0, 0)
        self.draw_cylinder(0, 0, 0, 0.06, 0.3, link_color)
        
        # 12. Gripper
        glTranslatef(0, 0.3, 0)
        gripper_width = 0.15 + 0.1 * self.joint_angles['gripper_open']
        # Left gripper finger
        glPushMatrix()
        glTranslatef(-gripper_width/2, 0, 0)
        self.draw_box(0, 0.1, 0, 0.05, 0.2, 0.03, self.colors["gripper"])
        glPopMatrix()
        
        # Right gripper finger
        glPushMatrix()
        glTranslatef(gripper_width/2, 0, 0)
        self.draw_box(0, 0.1, 0, 0.05, 0.2, 0.03, self.colors["gripper"])
        glPopMatrix()
        
        glPopMatrix()  # Restore to robot base
        
        glPopMatrix()  # Restore to world
    
    def draw_component(self):
        """Draw the component being manipulated"""
        if not self.component_visible:
            return
        
        # Calculate component position
        if self.component_attached:
            # Component is attached to gripper
            gripper_pos = self.forward_kinematics()
            self.component_pos = [
                gripper_pos[0],
                gripper_pos[1] - 0.1,  # Slightly below gripper
                gripper_pos[2]
            ]
        elif self.active_stage_index == 1 and self.stage_progress > 0.5:
            # Component is being placed on tray
            t = self.smoothstep((self.stage_progress - 0.5) * 2)
            start_pos = [-2.5, 0.8, 0]  # Bin position
            end_pos = [2.5, 0.3, 0]    # Tray position
            self.component_pos = [
                start_pos[0] + (end_pos[0] - start_pos[0]) * t,
                start_pos[1] + (end_pos[1] - start_pos[1]) * t,
                start_pos[2] + (end_pos[2] - start_pos[2]) * t
            ]
        
        # Draw component as a small gold cube
        self.draw_box(
            self.component_pos[0],
            self.component_pos[1],
            self.component_pos[2],
            0.15, 0.15, 0.15,
            self.colors["component_gold"]
        )
    
    def draw_done_pulse(self):
        """Draw done pulse effect"""
        if not PYOPENGL_AVAILABLE:
            return
        
        pulse_progress = (self.animation_time - self.done_pulse_time) / (DONE_PULSE_MS / 1000.0)
        if pulse_progress >= 1.0:
            return
        
        pulse_alpha = 1.0 - pulse_progress
        pulse_radius = 0.5 + pulse_progress * 3.0
        
        # Draw pulsing ring on central table
        glPushMatrix()
        glTranslatef(0, 0.1, 0)
        
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
            
            # Setup lighting for industrial look
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
        """Draw the complete industrial robot cell"""
        # Apply shake effect if fault (subtle)
        if self.current_state['fault']:
            shake_intensity = 0.02
            shake_x = shake_intensity * math.sin(self.animation_time * 8)
            shake_y = shake_intensity * math.cos(self.animation_time * 7)
            glTranslatef(shake_x, shake_y, 0)
        
        # Draw floor grid
        self.draw_grid()
        
        # ===== FACTORY STATION STRUCTURE =====
        # 1. Main work table
        self.draw_box(0, 0.1, 0, 8, 0.2, 5, self.colors["dark_gray"])
        
        # 2. Conveyor belt at back
        self.draw_box(0, 0.3, 2.8, 7, 0.3, 0.5, self.colors["conveyor"])
        
        # 3. Component bins (left side)
        self.draw_box(-2.5, 0.6, -1.5, 1.2, 0.8, 1.2, self.colors["bin_green"])
        self.draw_box(-2.5, 0.6, 0, 1.2, 0.8, 1.2, self.colors["bin_blue"])
        self.draw_box(-2.5, 0.6, 1.5, 1.2, 0.8, 1.2, self.colors["bin_green"])
        
        # 4. Kit tray area (right side)
        self.draw_box(2.5, 0.3, 0, 1.5, 0.1, 2, self.colors["tray_gray"])
        
        # 5. Safety frame (transparent yellow)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        # Draw safety frame posts
        for x in [-3.5, 3.5]:
            for z in [-2.5, 2.5]:
                self.draw_box(x, 1.5, z, 0.1, 3.0, 0.1, self.colors["safety_yellow"])
        glDisable(GL_BLEND)
        
        # ===== STATUS INDICATORS =====
        # Ready/Busy/Fault LED panel
        led_y = 2.0
        if self.current_state['ready']:
            self.draw_box(-3.0, led_y, 2.2, 0.3, 0.3, 0.1, self.colors["green_led"])
        if self.current_state['busy']:
            self.draw_box(-3.0, led_y, 1.8, 0.3, 0.3, 0.1, self.colors["amber_led"])
        if self.current_state['fault']:
            # Pulsing red LED for fault
            blink = 0.5 + 0.5 * math.sin(self.animation_time * 3)
            fault_color = (
                self.colors["red_led"][0] * blink,
                self.colors["red_led"][1] * blink,
                self.colors["red_led"][2] * blink,
                1.0
            )
            self.draw_box(-3.0, led_y, 1.4, 0.3, 0.3, 0.1, fault_color)
        
        # ===== UPDATE ANIMATION STATE =====
        self.compute_active_stage()
        self.update_joint_angles()
        
        # Update component visibility and attachment
        if self.active_stage_index == 0:  # Pick Component
            self.component_visible = True
            self.component_attached = self.stage_progress > 0.5
        elif self.active_stage_index == 1:  # Place/Mount
            self.component_visible = True
            self.component_attached = self.stage_progress < 0.5
        elif self.active_stage_index == 2:  # Secure/Solder
            self.component_visible = False
            self.component_attached = False
        elif self.active_stage_index == 3:  # Kit Complete
            self.component_visible = False
            self.component_attached = False
        else:
            self.component_visible = False
            self.component_attached = False
        
        # ===== DRAW ROBOT =====
        self.draw_robot()
        
        # ===== DRAW COMPONENT =====
        self.draw_component()
        
        # ===== STAGE-SPECIFIC EFFECTS =====
        if self.active_stage_index == 2:  # Secure/Solder
            # Show soldering/secure action with small effect
            effect_y = 0.9 + 0.1 * math.sin(self.animation_time * 10)
            effect_color = (
                0.8 + 0.2 * math.sin(self.animation_time * 8),
                0.6 + 0.2 * math.sin(self.animation_time * 7),
                0.2,
                0.7
            )
            # Enable blending for alpha transparency
            glEnable(GL_BLEND)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            self.draw_box(2.5, effect_y, 0, 0.1, 0.1, 0.1, effect_color)
            glDisable(GL_BLEND)
        
        # ===== DONE PULSE EFFECT =====
        if self.animation_time - self.done_pulse_time < DONE_PULSE_MS / 1000.0:
            self.draw_done_pulse()
        
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
    
    def draw_overlay(self, painter):
        """Draw overlay text with system information"""
        # Setup font
        font = QFont("Monospace", 10)
        painter.setFont(font)
        painter.setPen(QPen(QColor(240, 240, 240), 1))
        
        # Mode and state - use current_mode from MainWindow
        mode_text = f"Mode: {self.mode}"
        state_text = f"State: R={int(self.current_state['ready'])} B={int(self.current_state['busy'])} D={int(self.current_state['done'])} F={int(self.current_state['fault'])}"
        
        # Get current completed count for display
        completed = self.current_state['extra'].get('total_completed', 0)
        total = self.current_state['extra'].get('total', 'N/A')
        
        # Use animation time for display
        now_text = f"Visual Time: {self.animation_time:.1f}s"
            
        info_text = f"Completed: {completed}/{total} | {now_text}"
        
        # Active stage
        active_stage = "Idle"
        stage_color = QColor(200, 200, 200)
        if self.active_stage_index >= 0 and self.active_stage_index < 4:
            active_stage = self.stage_names[self.active_stage_index]
            stage_color = QColor(255, 215, 0)  # Gold for active
            
            # Add progress percentage
            active_stage += f" ({self.stage_progress*100:.0f}%)"
        
        stage_text = f"Active Stage: {active_stage}"
        
        # Cycle time
        cycle_text = f"Cycle: {self.current_state['cycle_time_ms'] or 'N/A'} ms"
        stage_dur_text = f"Stage Dur: {self.stage_s:.1f}s"
        
        # Draw text with background for readability
        y_offset = 20
        line_height = 20
        
        texts = [mode_text, state_text, info_text, stage_text, cycle_text, stage_dur_text]
        
        colors = [None, None, None, stage_color, None, None]
        
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
    
    def update_state(self, event: ST1Event, mode: str = "LIVE", visual_time_s: float = None):
        """Update the current state from an event"""
        old_busy = self.current_state.get('busy', False)
        old_done = self.current_state.get('_last_done', False)
        old_completed = self.current_state.get('extra', {}).get('total_completed', 0)
        
        self.current_state = {
            'ready': event.ready,
            'busy': event.busy,
            'done': event.done,
            'fault': event.fault,
            'cycle_time_ms': event.cycle_time_ms,
            'extra': event.extra
        }
        
        self.mode = mode
        
        # Use provided visual_time_s or fallback to current animation time (deterministic)
        if visual_time_s is None:
            visual_time_s = self.animation_time
        
        # Check for busy rising edge OR if we're not active but should be (mid-cycle attach)
        current_completed = event.extra.get('total_completed', 0)
        if event.busy and not old_busy:
            # Normal busy rising edge
            self.proc_active = True
            self.proc_start = visual_time_s
            
            # Reset animation state
            self.active_stage_index = -1
            self.stage_progress = 0.0
            
            # Calculate stage duration from cycle_time_ms if available
            if event.cycle_time_ms:
                total_cycle_s = event.cycle_time_ms / 1000.0
                # Divide by 4 stages, clamp to reasonable range (0.4-5.0 seconds per stage)
                calculated_stage_s = total_cycle_s / 4.0
                self.stage_s = max(0.4, min(5.0, calculated_stage_s))
            else:
                self.stage_s = self.base_stage_s
            
            self.proc_end = visual_time_s + 4 * self.stage_s  # 4 stages
        
        # Handle case where visualizer attaches mid-cycle (busy already True but not active)
        elif event.busy and not self.proc_active and self.active_stage_index == -1:
            # We missed the busy rising edge, start animation anyway
            self.proc_active = True
            
            # Use cycle time to determine stage duration
            if event.cycle_time_ms:
                total_cycle_s = event.cycle_time_ms / 1000.0
                calculated_stage_s = total_cycle_s / 4.0
                self.stage_s = max(0.4, min(5.0, calculated_stage_s))
            else:
                self.stage_s = self.base_stage_s
            
            # Estimate we're in stage 2 or 3 (Secure/Solder or Kit Complete) since we're already busy
            # Set proc_start in the past so animation shows active stage
            self.proc_start = visual_time_s - 2 * self.stage_s  # Assume we're in later stages
            self.proc_end = visual_time_s + 2 * self.stage_s  # Extend a bit into the future
            
            print(f"ST1 attached mid-cycle: starting animation at estimated stage 2/3")
        
        # Stop processing on done rising edge OR completed counter increase
        if (event.done and not old_done) or (current_completed > old_completed):
            if event.done and not old_done:
                self.done_pulse_time = visual_time_s
            self.proc_active = False
            self.active_stage_index = -1
            self.stage_progress = 0.0
            self.component_attached = False
            self.component_visible = False
        
        # On fault: stop processing and show FAIL
        if event.fault:
            self.proc_active = False
            self.active_stage_index = -1
            self.stage_progress = 0.0
            self.component_attached = False
            self.component_visible = False
        
        # Store last done state
        self.current_state['_last_done'] = event.done
        
        # Update animation time
        self.animation_time = visual_time_s
        
        self.update()

# ===== MAIN WINDOW =====
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ST1 Component Kitting Visualizer - Industrial Robot Cell")
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
            # In LIVE mode, use wall time
            return time.time()
        else:
            # In REPLAY mode, use scaled VSI time
            if not self.replay_events:
                return (self.current_time_ns - self.replay_min_time) / 1e9
            
            # Find current event
            current_event = None
            for event in self.replay_events:
                if event.t_ns <= self.current_time_ns:
                    current_event = event
                else:
                    break
            
            if current_event:
                # Use VSI time scaled to seconds for replay
                return (current_event.t_ns - self.replay_min_time) / 1e9
            
            return (self.current_time_ns - self.replay_min_time) / 1e9
    
    def init_ui(self):
        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Main layout
        main_layout = QVBoxLayout(central_widget)
        
        # Top info bar
        info_layout = QHBoxLayout()
        self.log_info_label = QLabel("Searching for ST1 Component Kitting log...")
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
        self.gl_widget = ST1OpenGLWidget()
        content_layout.addWidget(self.gl_widget, 3)
        
        # Status panel (right)
        status_panel = QVBoxLayout()
        
        # Component info
        info_group = QGroupBox("Component / Kit Info")
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
        state_group = QGroupBox("ST1 State")
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
        stage_group = QGroupBox("Kitting Status")
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
        result_group = QGroupBox("Kit Result")
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
    
    def load_live_snapshot(self) -> Tuple[Optional[ST1Event], int]:
        """Load snapshot of last N lines from log file, return latest event and raw count"""
        if not self.log_path or not os.path.exists(self.log_path):
            return None, 0
        
        try:
            with open(self.log_path, 'r', encoding='utf-8', errors='ignore') as f:
                # Read last N lines efficiently
                lines = collections.deque(f, maxlen=SNAPSHOT_LINES)
            
            parser = ST1LogParser()
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
        """Discover and load ST1 log file"""
        # Stop current worker
        self._stop_tail_worker()
        
        # Find log
        self.log_path = LogDiscoverer.find_st1_log()
        
        if self.log_path:
            base_name = os.path.basename(self.log_path)
            self.log_info_label.setText(f"ST1 Log: {base_name}")
            
            # Reset counters
            self.raw_event_count = 0
            self.accepted_event_count = 0
            self.update_event_count_label()
            
            if self.is_live_mode:
                self.switch_to_live()
            else:
                self.switch_to_replay()
        else:
            self.log_info_label.setText("No ST1 log found")
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
            
            # Get initial visual time
            visual_time_s = self.get_visual_time_s()
            self.gl_widget.animation_time = visual_time_s
            self.gl_widget.compute_active_stage()
            
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
                self.debug_label.setText(f"Mode: REPLAY | Event: {current_event.t_ns/1e9:.2f}s | R={current_event.ready} B={current_event.busy} D={current_event.done} F={current_event.fault}")
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
            parser = ST1LogParser()
            
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
            
            print(f"Loaded {len(self.replay_events)} ST1 events for replay")
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
            # Get visual time for animation
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
    
    def process_new_event(self, event: ST1Event):
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
    
    def update_display(self, event: ST1Event, from_snapshot: bool = False):
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
        
        # Update total and completed
        total = event.extra.get('total', 'N/A')
        completed = event.extra.get('total_completed', 0)
        
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
    
    def update_animation(self):
        """Update animation based on timer"""
        # Get current visual time
        visual_time_s = self.get_visual_time_s()
        
        # Update animation time in gl_widget
        self.gl_widget.animation_time = visual_time_s
        
        # Compute active stage using animation time
        self.gl_widget.compute_active_stage()
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