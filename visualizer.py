#!/usr/bin/env python3
"""
VSI Log Visualizer - Single-file Qt + OpenGL visualization for VSI logs
"""

# ===== CONFIGURATION =====
SEARCH_ROOT = "."
MAX_EVENTS_PER_STATION = 20000
TIMER_FPS = 30
DONE_PULSE_MS = 300
OPENGL_MAJOR_VERSION = 2
OPENGL_MINOR_VERSION = 1
USE_PYOPENGL = True  # Set to False if PyOpenGL causes issues

import sys
import os
import re
import glob
import time
import threading
import collections
import math
import traceback
from datetime import datetime
from typing import *
from dataclasses import dataclass, field
from enum import Enum

# Force-add user site-packages to sys.path to ensure PySide6 can be found
import site
user_site = site.getusersitepackages()
if user_site not in sys.path:
    sys.path.insert(0, user_site)

# Try to import PySide6 with better error reporting
try:
    from PySide6.QtWidgets import *
    from PySide6.QtCore import *
    from PySide6.QtGui import *
    from PySide6.QtOpenGLWidgets import QOpenGLWidget
    # QSurfaceFormat is in QtGui, not QtOpenGL
    QT_AVAILABLE = True
except Exception as e:
    # Print detailed error information for debugging
    print(f"ERROR importing PySide6: {e}")
    print(f"Python executable: {sys.executable}")
    print(f"Python path (first 5 entries):")
    for i, path in enumerate(sys.path[:5]):
        print(f"  {i}: {path}")
    print("\nFull traceback:")
    traceback.print_exc()
    
    # Only exit if it's actually a ModuleNotFoundError for PySide6
    if isinstance(e, ModuleNotFoundError) and "PySide6" in str(e):
        print("\nERROR: PySide6 not installed. Install with: pip install PySide6")
        sys.exit(1)
    else:
        # Some other import error - try to continue but warn
        print("\nWARNING: PySide6 import failed for unknown reason. Trying to continue...")
        QT_AVAILABLE = False
        # We still exit because the app won't work without Qt
        sys.exit(1)

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
class StationStateEvent:
    station: str  # "ST1", "ST2", etc
    t_ns: int
    ready: bool = False
    busy: bool = False
    done: bool = False
    fault: bool = False
    cycle_time_ms: Optional[float] = None
    extra: Dict[str, Any] = field(default_factory=dict)

@dataclass
class PlcEvent:
    t_ns: int
    state: Optional[str] = None
    batch_id: Optional[int] = None
    recipe_id: Optional[int] = None
    buffers: Dict[str, int] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)

class Station(Enum):
    PLC = "PLC"
    ST1 = "ST1"
    ST2 = "ST2"
    ST3 = "ST3"
    ST4 = "ST4"
    ST5 = "ST5"
    ST6 = "ST6"

    @classmethod
    def all_stations(cls):
        return [cls.ST1, cls.ST2, cls.ST3, cls.ST4, cls.ST5, cls.ST6]

# ===== STREAMING LOG PARSER =====
class StreamingLogParser:
    """Robust parser that handles VSI time on separate lines"""
    
    def __init__(self):
        self.last_vsi_time_ns = {}
        self.synthetic_time_ns = {}
        self.station_patterns = {
            Station.ST1: re.compile(r".*ST1.*", re.IGNORECASE),
            Station.ST2: re.compile(r".*ST2.*", re.IGNORECASE),
            Station.ST3: re.compile(r".*ST3.*", re.IGNORECASE),
            Station.ST4: re.compile(r".*ST4.*", re.IGNORECASE),
            Station.ST5: re.compile(r".*ST5.*", re.IGNORECASE),
            Station.ST6: re.compile(r".*ST6.*", re.IGNORECASE),
            Station.PLC: re.compile(r".*PLC.*", re.IGNORECASE),
        }
        
        # VSI time pattern (can be on its own line)
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
        
        # Other patterns
        self.cycle_time_pattern = re.compile(r"cycle[_\s]*time[_\s]*[:=]?\s*([\d.]+)\s*ms", re.IGNORECASE)
        self.batch_id_pattern = re.compile(r"batch[_\s]*id[_\s]*[:=]?\s*(\d+)", re.IGNORECASE)
        self.recipe_id_pattern = re.compile(r"recipe[_\s]*id[_\s]*[:=]?\s*(\d+)", re.IGNORECASE)
        self.buffer_pattern = re.compile(r"buffer(\d+)[_\s]*[:=]\s*(\d+)", re.IGNORECASE)
        self.key_value_pattern = re.compile(r"(\w+)[_\s]*[:=]\s*([\w.-]+)")
        self.state_string_pattern = re.compile(r"state\s*[:=]\s*(\w+)", re.IGNORECASE)
        
        # Initialize time trackers
        for station in Station:
            self.last_vsi_time_ns[station] = None
            self.synthetic_time_ns[station] = 0
    
    def parse_line(self, line: str) -> Tuple[Optional[Union[StationStateEvent, PlcEvent]], Station]:
        """Parse a line and return event if any, along with detected station"""
        line = line.strip()
        if not line:
            return None, None
        
        # Detect which station this line belongs to
        station = None
        for st, pattern in self.station_patterns.items():
            if pattern.match(line):
                station = st
                break
        
        if not station:
            return None, None
        
        # Check for VSI time (can be on its own line)
        vsi_match = self.vsi_time_pattern.search(line)
        if vsi_match:
            time_ns = int(vsi_match.group(1))
            self.last_vsi_time_ns[station] = time_ns
            # Don't emit event yet, just update time
        
        # Determine timestamp to use
        if self.last_vsi_time_ns[station] is not None:
            timestamp = self.last_vsi_time_ns[station]
        else:
            self.synthetic_time_ns[station] += 10_000_000  # 10ms increment
            timestamp = self.synthetic_time_ns[station]
        
        # Check for state information
        event = None
        if station == Station.PLC:
            event = self._parse_plc_line(line, timestamp)
        else:
            event = self._parse_station_line(line, timestamp, station)
        
        return event, station
    
    def _parse_station_line(self, line: str, timestamp: int, station: Station) -> Optional[StationStateEvent]:
        """Parse a station line for state information"""
        event = None
        state_found = False
        
        # Check for explicit state values (ready: 1, busy: 0, etc)
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
        
        # If any state value found, create event
        if any(v is not None for v in [ready_val, busy_val, done_val, fault_val]):
            state_found = True
        
        # Check for cycle time
        cycle_time = None
        cycle_match = self.cycle_time_pattern.search(line)
        if cycle_match:
            cycle_time = float(cycle_match.group(1))
            state_found = True
        
        # Extract key-value pairs
        extra = {}
        for match in self.key_value_pattern.finditer(line):
            key, value = match.groups()
            # Skip already parsed keys
            if key.lower() in ['vsi', 'time', 'cycle', 'ready', 'busy', 'done', 'fault', 'state', 'batch', 'recipe']:
                continue
            # Try to convert to number if possible
            try:
                if '.' in value:
                    value = float(value)
                else:
                    value = int(value)
            except ValueError:
                pass
            extra[key] = value
            state_found = True
        
        # Also check for buffer patterns
        for match in self.buffer_pattern.finditer(line):
            buf_num, count = match.groups()
            extra[f"buffer{buf_num}"] = int(count)
            state_found = True
        
        if state_found:
            event = StationStateEvent(
                station=station.value,
                t_ns=timestamp,
                ready=bool(ready_val) if ready_val is not None else False,
                busy=bool(busy_val) if busy_val is not None else False,
                done=bool(done_val) if done_val is not None else False,
                fault=bool(fault_val) if fault_val is not None else False,
                cycle_time_ms=cycle_time,
                extra=extra
            )
        
        return event
    
    def _parse_plc_line(self, line: str, timestamp: int) -> Optional[PlcEvent]:
        """Parse a PLC line"""
        event = None
        state_found = False
        
        # Extract state string
        state = None
        state_match = self.state_string_pattern.search(line)
        if state_match:
            state = state_match.group(1)
            state_found = True
        
        # Extract batch and recipe IDs
        batch_id = None
        recipe_id = None
        batch_match = self.batch_id_pattern.search(line)
        if batch_match:
            batch_id = int(batch_match.group(1))
            state_found = True
        
        recipe_match = self.recipe_id_pattern.search(line)
        if recipe_match:
            recipe_id = int(recipe_match.group(1))
            state_found = True
        
        # Extract buffers
        buffers = {}
        for match in self.buffer_pattern.finditer(line):
            buf_num, count = match.groups()
            buffers[f"buffer{buf_num}"] = int(count)
            state_found = True
        
        # Extract other key-value pairs
        extra = {}
        for match in self.key_value_pattern.finditer(line):
            key, value = match.groups()
            if key.lower() in ['vsi', 'time', 'state', 'batch', 'recipe']:
                continue
            try:
                if '.' in value:
                    value = float(value)
                else:
                    value = int(value)
            except ValueError:
                pass
            extra[key] = value
            state_found = True
        
        if state_found:
            event = PlcEvent(
                t_ns=timestamp,
                state=state,
                batch_id=batch_id,
                recipe_id=recipe_id,
                buffers=buffers,
                extra=extra
            )
        
        return event
    
    def _get_state_value(self, line: str, state_name: str) -> Optional[int]:
        """Get state value (0 or 1) from line"""
        pattern = self.state_patterns.get(state_name)
        if pattern:
            match = pattern.search(line)
            if match:
                return int(match.group(1))
        return None

# ===== LOG DISCOVERY =====
class LogDiscoverer:
    @staticmethod
    def find_newest_log(pattern: str) -> Optional[str]:
        """Find newest log file matching pattern (case-insensitive)"""
        matches = []
        
        # Search in current directory and subdirectories
        for root, dirs, files in os.walk(SEARCH_ROOT):
            for file in files:
                if file.lower().endswith('.log'):
                    if re.match(pattern.replace("*", ".*"), file, re.IGNORECASE):
                        full_path = os.path.join(root, file)
                        matches.append((full_path, os.path.getmtime(full_path)))
        
        if not matches:
            return None
        
        # Return newest
        matches.sort(key=lambda x: x[1], reverse=True)
        return matches[0][0]
    
    @classmethod
    def discover_all_logs(cls) -> Dict[Station, str]:
        """Discover all log files"""
        logs = {}
        
        patterns = {
            Station.PLC: [r".*PLC.*Coordinator.*"],
            Station.ST1: [r".*ST1.*ComponentKitting.*"],
            Station.ST2: [r".*ST2.*FrameCoreAssembly.*"],
            Station.ST3: [r".*ST3.*ElectronicsWiring.*"],
            Station.ST4: [r".*ST4.*CalibrationTesting.*"],
            Station.ST5: [r".*ST5.*QualityInspection.*"],
            Station.ST6: [r".*ST6.*PackagingDispatch.*"],
        }
        
        for station, pattern_list in patterns.items():
            log_path = None
            for pattern in pattern_list:
                log_path = cls.find_newest_log(pattern)
                if log_path:
                    break
            if log_path:
                logs[station] = log_path
                print(f"Found {station.value} log: {log_path}")
            else:
                print(f"WARNING: No log found for {station.value}")
        
        return logs

# ===== TAIL WORKER =====
class LogTailWorker(QThread):
    new_events = Signal(dict)  # {station: [events]}
    
    def __init__(self, log_files: Dict[Station, str]):
        super().__init__()
        self.log_files = log_files
        self.running = True
        self.file_handles = {}
        self.file_positions = {}
        self.file_inodes = {}
        self.parsers = {station: StreamingLogParser() for station in log_files.keys()}
        
    def run(self):
        """Tail all log files and emit new events"""
        # Open files
        for station, path in self.log_files.items():
            if path and os.path.exists(path):
                self._open_file(station, path)
        
        last_emit_time = time.time()
        buffer = collections.defaultdict(list)
        
        while self.running:
            try:
                events_found = False
                
                for station, fh in list(self.file_handles.items()):
                    if not fh:
                        continue
                    
                    # Check if file was rotated/truncated
                    current_size = os.path.getsize(self.log_files[station])
                    if current_size < self.file_positions[station]:
                        print(f"File truncated, reopening: {self.log_files[station]}")
                        self._open_file(station, self.log_files[station])
                        continue
                    
                    # Read new lines
                    try:
                        fh.seek(self.file_positions[station])
                        new_lines = fh.readlines()
                        self.file_positions[station] = fh.tell()
                    except (OSError, IOError) as e:
                        print(f"Error reading {station.value}: {e}")
                        self._open_file(station, self.log_files[station])
                        continue
                    
                    # Parse lines
                    parser = self.parsers[station]
                    for line in new_lines:
                        event, detected_station = parser.parse_line(line)
                        if event and detected_station == station:
                            buffer[station].append(event)
                            events_found = True
                
                # Emit events periodically
                current_time = time.time()
                if events_found and (current_time - last_emit_time > 0.1 or len(buffer) > 10):
                    if buffer:
                        self.new_events.emit(dict(buffer))
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
    
    def _open_file(self, station: Station, path: str):
        """Open or reopen a log file"""
        try:
            if station in self.file_handles and self.file_handles[station]:
                self.file_handles[station].close()
            
            self.file_handles[station] = open(path, 'r', encoding='utf-8', errors='ignore')
            self.file_handles[station].seek(0, os.SEEK_END)
            self.file_positions[station] = self.file_handles[station].tell()
            self.parsers[station] = StreamingLogParser()  # Reset parser
            
            # Store inode to detect rotation
            self.file_inodes[station] = os.stat(path).st_ino
        except Exception as e:
            print(f"Error opening {path}: {e}")
            self.file_handles[station] = None
    
    def stop(self):
        self.running = False
        self.wait()

# ===== OPENGL VISUALIZATION =====
class OpenGLWidget(QOpenGLWidget):
    """Main OpenGL visualization widget"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_view = "ALL"
        self.station_states = {}
        self.plc_state = {}
        self.current_time_ns = 0
        self.animation_time = 0
        self.part_index = 0  # 0=ST1, 1=ST2, ..., 5=ST6
        self.part_moving = False
        self.part_move_start_time = 0
        self.part_move_duration = 1.0  # seconds
        self.station_animations = {}  # Animation states per station
        self.done_pulse_times = {}  # When done pulses started
        
        # Colors
        self.colors = {
            "ready": (0.0, 0.8, 0.0, 1.0),
            "busy": (1.0, 0.6, 0.0, 1.0),
            "fault": (0.8, 0.0, 0.0, 1.0),
            "idle": (0.5, 0.5, 0.5, 1.0),
            "done_pulse": (0.0, 1.0, 1.0, 1.0),
            "grid": (0.3, 0.3, 0.3, 1.0),
            "conveyor": (0.4, 0.4, 0.4, 1.0),
            "text": (1.0, 1.0, 1.0, 1.0),
            "part": (0.2, 0.6, 1.0, 1.0),
            "plc": (0.6, 0.2, 0.8, 1.0),
        }
        
        # Station positions (x, y, z)
        self.station_positions = {
            "ST1": (-5.0, 0.0, 0.0),
            "ST2": (-3.0, 0.0, 0.0),
            "ST3": (-1.0, 0.0, 0.0),
            "ST4": (1.0, 0.0, 0.0),
            "ST5": (3.0, 0.0, 0.0),
            "ST6": (5.0, 0.0, 0.0),
            "PLC": (0.0, 3.0, 0.0),
        }
        
        # Camera
        self.camera_distance = 15.0
        self.camera_angle_x = 30.0
        self.camera_angle_y = 45.0
        self.last_mouse_pos = None
        
        self.setMouseTracking(True)
        
    def initializeGL(self):
        if PYOPENGL_AVAILABLE:
            glEnable(GL_DEPTH_TEST)
            glEnable(GL_LIGHTING)
            glEnable(GL_LIGHT0)
            glEnable(GL_COLOR_MATERIAL)
            glColorMaterial(GL_FRONT_AND_BACK, GL_AMBIENT_AND_DIFFUSE)
            
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
            glOrtho(-10 * aspect, 10 * aspect, -10, 10, -50, 50)
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
            
            target = self.get_view_target()
            gluLookAt(
                cam_x + target[0], cam_y + target[1], cam_z + target[2],
                target[0], target[1], target[2],
                0, 1, 0
            )
            
            # Draw scene based on view
            if self.current_view == "ALL":
                self.draw_all_view()
            else:
                self.draw_station_view(self.current_view)
                
        except Exception as e:
            print(f"OpenGL error: {e}")
            traceback.print_exc()
    
    def paintBasic(self):
        """Basic painting when OpenGL is not available"""
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(30, 30, 40))
        
        # Draw station boxes
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        painter.setFont(QFont("Arial", 12))
        
        for i, station in enumerate(["ST1", "ST2", "ST3", "ST4", "ST5", "ST6"]):
            x = 100 + i * 120
            y = 200
            
            # Get station color
            state = self.station_states.get(station, {})
            color = QColor(128, 128, 128)  # idle
            
            if state.get('fault', False):
                color = QColor(255, 0, 0)
            elif state.get('busy', False):
                color = QColor(255, 165, 0)
            elif state.get('ready', False):
                color = QColor(0, 255, 0)
            
            painter.fillRect(x, y, 100, 80, color)
            painter.drawRect(x, y, 100, 80)
            painter.drawText(x + 10, y + 40, station)
        
        # Draw part
        part_x = 150 + int(self.part_index) * 120
        if self.part_moving:
            elapsed = time.time() - self.part_move_start_time
            progress = min(elapsed / self.part_move_duration, 1.0)
            part_x += int(progress * 120)
        
        painter.fillRect(part_x, 180, 20, 20, QColor(50, 150, 255))
        
        # Draw PLC box
        painter.fillRect(600, 50, 100, 80, QColor(150, 50, 200))
        painter.drawRect(600, 50, 100, 80)
        painter.drawText(610, 90, "PLC")
        
        # Draw status text
        painter.setPen(QPen(QColor(255, 255, 255), 1))
        painter.drawText(10, 30, f"View: {self.current_view}")
        painter.drawText(10, 50, f"Time: {self.current_time_ns // 1000000000}s")
        painter.drawText(10, 70, f"Part at ST{self.part_index + 1}")
    
    def get_view_target(self):
        """Get camera target based on current view"""
        if self.current_view == "ALL":
            return (0.0, 0.0, 0.0)
        elif self.current_view in self.station_positions:
            return self.station_positions[self.current_view]
        elif self.current_view == "PLC":
            return self.station_positions["PLC"]
        return (0.0, 0.0, 0.0)
    
    def draw_all_view(self):
        """Draw the entire production line"""
        # Draw floor grid
        self.draw_grid()
        
        # Draw conveyor
        self.draw_conveyor()
        
        # Draw stations
        for station in ["ST1", "ST2", "ST3", "ST4", "ST5", "ST6"]:
            self.draw_station_box(station)
        
        # Draw PLC
        self.draw_plc_box()
        
        # Draw moving part
        self.draw_moving_part()
    
    def draw_station_view(self, station):
        """Draw detailed view of a specific station"""
        # Draw simplified background
        self.draw_grid(scale=0.5)
        
        # Draw the station
        self.draw_station_box(station, detailed=True)
        
        # Draw station-specific animation
        self.draw_station_animation(station)
    
    def draw_grid(self, scale=2.0):
        """Draw floor grid"""
        if not PYOPENGL_AVAILABLE:
            return
            
        glColor4f(*self.colors["grid"])
        glBegin(GL_LINES)
        
        size = 10.0 * scale
        steps = 20
        
        for i in range(-steps, steps + 1):
            x = i * (size / steps)
            glVertex3f(x, 0, -size/2)
            glVertex3f(x, 0, size/2)
            glVertex3f(-size/2, 0, x)
            glVertex3f(size/2, 0, x)
        
        glEnd()
    
    def draw_conveyor(self):
        """Draw conveyor belt"""
        if not PYOPENGL_AVAILABLE:
            return
            
        glColor4f(*self.colors["conveyor"])
        
        # Conveyor base
        glPushMatrix()
        glTranslatef(0, -0.1, 0)
        glScalef(12.0, 0.1, 0.5)
        self.draw_cube()
        glPopMatrix()
    
    def draw_station_box(self, station, detailed=False):
        """Draw a station box with appropriate color"""
        if station not in self.station_positions:
            return
            
        pos = self.station_positions[station]
        
        # Get station state and color
        state = self.station_states.get(station, {})
        base_color = self.colors["idle"]
        
        if state.get('fault', False):
            base_color = self.colors["fault"]
        elif state.get('busy', False):
            base_color = self.colors["busy"]
        elif state.get('ready', False):
            base_color = self.colors["ready"]
        
        # Check for done pulse
        pulse_time = self.done_pulse_times.get(station, 0)
        current_time = time.time()
        if current_time - pulse_time < DONE_PULSE_MS / 1000.0:
            # Pulsing effect
            pulse_factor = math.sin((current_time - pulse_time) * 20) * 0.5 + 0.5
            base_color = (
                base_color[0] * (1 - pulse_factor) + self.colors["done_pulse"][0] * pulse_factor,
                base_color[1] * (1 - pulse_factor) + self.colors["done_pulse"][1] * pulse_factor,
                base_color[2] * (1 - pulse_factor) + self.colors["done_pulse"][2] * pulse_factor,
                1.0
            )
        
        if not PYOPENGL_AVAILABLE:
            return
            
        glPushMatrix()
        glTranslatef(*pos)
        
        if detailed:
            glScalef(1.5, 1.5, 1.5)
        else:
            glScalef(0.8, 0.8, 0.8)
        
        glColor4f(*base_color)
        self.draw_cube()
        
        glPopMatrix()
    
    def draw_plc_box(self):
        """Draw PLC box"""
        pos = self.station_positions["PLC"]
        
        if not PYOPENGL_AVAILABLE:
            return
            
        glPushMatrix()
        glTranslatef(*pos)
        glScalef(0.6, 0.6, 0.6)
        glColor4f(*self.colors["plc"])
        self.draw_cube()
        glPopMatrix()
    
    def draw_moving_part(self):
        """Draw the moving part on the conveyor"""
        if not PYOPENGL_AVAILABLE:
            return
            
        # Calculate part position
        if self.part_moving:
            elapsed = time.time() - self.part_move_start_time
            progress = min(elapsed / self.part_move_duration, 1.0)
            if progress >= 1.0:
                self.part_moving = False
        else:
            progress = 0
        
        # Map part_index (0-5) to station positions
        source_idx = self.part_index
        target_idx = min(source_idx + 1, 5)  # Don't go beyond ST6
        
        station_names = ["ST1", "ST2", "ST3", "ST4", "ST5", "ST6"]
        source_pos = self.station_positions[station_names[source_idx]]
        target_pos = self.station_positions[station_names[target_idx]]
        
        current_x = source_pos[0] + (target_pos[0] - source_pos[0]) * progress
        current_y = 0.5 + 0.2 * math.sin(progress * math.pi)  # Bounce effect
        current_z = source_pos[2]
        
        # Draw part
        glPushMatrix()
        glTranslatef(current_x, current_y, current_z)
        glScalef(0.3, 0.3, 0.3)
        glColor4f(*self.colors["part"])
        self.draw_cube()
        glPopMatrix()
    
    def draw_station_animation(self, station):
        """Draw station-specific animation"""
        if not PYOPENGL_AVAILABLE:
            return
            
        state = self.station_states.get(station, {})
        busy = state.get('busy', False)
        
        if not busy:
            return
            
        glPushMatrix()
        glTranslatef(*self.station_positions[station])
        
        current_time = time.time()
        
        if station == "ST1":
            # Rotating arms
            glPushMatrix()
            glRotatef(current_time * 180, 0, 1, 0)
            glColor4f(1.0, 0.8, 0.0, 1.0)
            glBegin(GL_LINES)
            for i in range(2):
                angle = i * math.pi
                glVertex3f(0, 0, 0)
                glVertex3f(math.cos(angle + current_time) * 0.8, 
                          math.sin(current_time * 2) * 0.2 + 0.5,
                          math.sin(angle + current_time) * 0.8)
            glEnd()
            glPopMatrix()
            
        elif station == "ST2":
            # Press plate moving up/down
            press_height = 0.3 + 0.2 * math.sin(current_time * 3)
            glColor4f(0.8, 0.8, 0.2, 1.0)
            glPushMatrix()
            glTranslatef(0, press_height, 0)
            glScalef(0.6, 0.1, 0.6)
            self.draw_cube()
            glPopMatrix()
            
        elif station == "ST3":
            # Wire drawing effect
            wire_length = 0.5 + 0.3 * abs(math.sin(current_time * 2))
            glColor4f(0.2, 0.8, 0.2, 1.0)
            glBegin(GL_LINE_STRIP)
            for i in range(10):
                t = i / 9.0 * wire_length
                glVertex3f(t - wire_length/2, 
                          math.sin(t * 4 + current_time) * 0.1,
                          math.cos(t * 3) * 0.1)
            glEnd()
            
        elif station == "ST4":
            # Chamber door and progress bar
            # Door
            door_open = 0.3 * abs(math.sin(current_time))
            glColor4f(0.6, 0.6, 0.9, 1.0)
            glPushMatrix()
            glTranslatef(-0.4 - door_open, 0.5, 0)
            glScalef(0.1, 0.6, 0.4)
            self.draw_cube()
            glPopMatrix()
            
            glPushMatrix()
            glTranslatef(0.4 + door_open, 0.5, 0)
            glScalef(0.1, 0.6, 0.4)
            self.draw_cube()
            glPopMatrix()
            
            # Progress bar
            progress = abs(math.sin(current_time * 0.5))
            glColor4f(0.0, 0.7, 1.0, 1.0)
            glBegin(GL_QUADS)
            glVertex3f(-0.3, 1.0, 0.2)
            glVertex3f(-0.3 + 0.6 * progress, 1.0, 0.2)
            glVertex3f(-0.3 + 0.6 * progress, 1.1, 0.2)
            glVertex3f(-0.3, 1.1, 0.2)
            glEnd()
            
        elif station == "ST5":
            # Camera flash and diverter
            # Flash effect
            flash = abs(math.sin(current_time * 5))
            glColor4f(1.0, 1.0, 1.0, flash)
            self.draw_sphere(0.2, 0, 1.0, 0)
            
            # Diverter arrow
            extra = state.get('extra', {})
            direction = extra.get('last_accept', 1)  # Default to accept
            arrow_angle = 45 if direction == 1 else -45
            
            glColor4f(0.8, 0.2, 0.2, 1.0)
            glPushMatrix()
            glRotatef(arrow_angle, 0, 1, 0)
            glBegin(GL_TRIANGLES)
            glVertex3f(0, 0.3, 0)
            glVertex3f(-0.2, 0.1, 0)
            glVertex3f(0.2, 0.1, 0)
            glEnd()
            glPopMatrix()
            
        elif station == "ST6":
            # Pick/place arm
            arm_angle = math.sin(current_time * 2) * 60
            glColor4f(0.8, 0.5, 0.2, 1.0)
            
            # Arm base
            glPushMatrix()
            glRotatef(arm_angle, 0, 1, 0)
            glBegin(GL_LINES)
            glVertex3f(0, 0.5, 0)
            glVertex3f(0.8, 0.5, 0)
            glEnd()
            
            # End effector
            glTranslatef(0.8, 0.5, 0)
            self.draw_sphere(0.1, 0, 0, 0)
            glPopMatrix()
        
        glPopMatrix()
    
    def draw_sphere(self, radius, x, y, z, slices=10, stacks=10):
        """Draw a sphere approximation (replacement for glutSolidSphere)"""
        if not PYOPENGL_AVAILABLE:
            return
            
        glPushMatrix()
        glTranslatef(x, y, z)
        
        for i in range(slices):
            lat0 = math.pi * (-0.5 + float(i) / slices)
            z0 = math.sin(lat0) * radius
            zr0 = math.cos(lat0) * radius
            
            lat1 = math.pi * (-0.5 + float(i + 1) / slices)
            z1 = math.sin(lat1) * radius
            zr1 = math.cos(lat1) * radius
            
            glBegin(GL_QUAD_STRIP)
            for j in range(stacks + 1):
                lng = 2 * math.pi * float(j) / stacks
                x = math.cos(lng)
                y = math.sin(lng)
                
                glNormal3f(x * zr0, y * zr0, z0)
                glVertex3f(x * zr0, y * zr0, z0)
                glNormal3f(x * zr1, y * zr1, z1)
                glVertex3f(x * zr1, y * zr1, z1)
            glEnd()
        
        glPopMatrix()
    
    def draw_cube(self):
        """Draw a simple cube"""
        if not PYOPENGL_AVAILABLE:
            return
            
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
        self.camera_distance = max(5.0, min(50.0, self.camera_distance - delta * 0.01))
        self.update()
    
    def update_states(self, station_states, plc_state):
        """Update station states from outside"""
        old_station_states = self.station_states.copy()
        self.station_states = station_states
        self.plc_state = plc_state
        
        # Check for done events to trigger part movement
        for station, state in station_states.items():
            if state.get('done', False):
                # Only trigger if this station wasn't done before
                old_state = old_station_states.get(station, {})
                if not old_state.get('done', False):
                    self.done_pulse_times[station] = time.time()
                    
                    # Move part if this station matches current part position
                    if station in ["ST1", "ST2", "ST3", "ST4", "ST5"]:
                        station_num = int(station[2:]) - 1  # Convert "ST1" -> 0
                        if station_num == self.part_index:
                            self.part_moving = True
                            self.part_move_start_time = time.time()
                            # Don't update part_index yet - let animation complete
        
        # Update part_index when animation completes
        if not self.part_moving and self.part_index < 5:
            # Check if we should be at next station
            for station_num in range(self.part_index, 6):
                station_name = f"ST{station_num + 1}"
                state = station_states.get(station_name, {})
                if state.get('done', False):
                    self.part_index = station_num
                    break
        
        self.update()
    
    def set_view(self, view_name):
        """Change the current view"""
        self.current_view = view_name
        
        # Adjust camera for station view
        if view_name != "ALL":
            self.camera_distance = 8.0
            self.camera_angle_x = 30.0
            self.camera_angle_y = 45.0
        
        self.update()

# ===== MAIN WINDOW =====
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VSI Log Visualizer")
        self.setGeometry(100, 100, 1400, 900)
        
        # Data storage
        self.replay_events = {s.value: [] for s in Station}
        self.live_states = {s.value: {} for s in Station}
        self.live_states["PLC"] = {}
        self.current_time_ns = 0
        self.is_live_mode = True
        self.is_playing = True
        self.playback_speed = 1.0
        
        # Log files
        self.log_files = {}
        
        # Threads
        self.tail_worker = None
        
        # UI
        self.init_ui()
        
        # Timer for animation
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_animation)
        self.timer.start(1000 // TIMER_FPS)
        
        # Initial log discovery
        self.discover_logs()
    
    def init_ui(self):
        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Main layout - VERTICAL
        main_layout = QVBoxLayout(central_widget)
        
        # Top panel (horizontal)
        top_panel = QHBoxLayout()
        main_layout.addLayout(top_panel, 1)  # Takes most space
        
        # Left panel - View selector
        left_panel = QVBoxLayout()
        
        view_label = QLabel("Views")
        view_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        left_panel.addWidget(view_label)
        
        self.view_list = QListWidget()
        self.view_list.addItems(["ALL", "PLC", "ST1", "ST2", "ST3", "ST4", "ST5", "ST6"])
        self.view_list.currentItemChanged.connect(self.on_view_changed)
        self.view_list.setFixedWidth(150)
        self.view_list.setCurrentRow(0)
        left_panel.addWidget(self.view_list)
        
        left_panel.addStretch()
        
        # Log status
        self.log_status_label = QLabel("No logs loaded")
        self.log_status_label.setWordWrap(True)
        self.log_status_label.setMaximumWidth(150)
        left_panel.addWidget(self.log_status_label)
        
        top_panel.addLayout(left_panel)
        
        # Center panel - OpenGL view
        center_panel = QVBoxLayout()
        
        self.gl_widget = OpenGLWidget()
        center_panel.addWidget(self.gl_widget, 1)
        
        # Time display
        time_layout = QHBoxLayout()
        time_layout.addWidget(QLabel("Current Time:"))
        self.time_label = QLabel("0 ns")
        time_layout.addWidget(self.time_label)
        time_layout.addStretch()
        
        self.live_indicator = QLabel("● LIVE")
        self.live_indicator.setStyleSheet("color: #00ff00; font-weight: bold;")
        time_layout.addWidget(self.live_indicator)
        
        center_panel.addLayout(time_layout)
        top_panel.addLayout(center_panel)
        
        # Right panel - KPI display
        right_panel = QVBoxLayout()
        
        kpi_label = QLabel("Station Status")
        kpi_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        right_panel.addWidget(kpi_label)
        
        self.kpi_text = QTextEdit()
        self.kpi_text.setReadOnly(True)
        self.kpi_text.setMaximumWidth(300)
        right_panel.addWidget(self.kpi_text)
        
        right_panel.addStretch()
        top_panel.addLayout(right_panel)
        
        # Bottom panel - Controls
        bottom_panel = QHBoxLayout()
        main_layout.addLayout(bottom_panel, 0)  # Fixed height
        
        # Play/Pause
        self.play_button = QPushButton("⏸")
        self.play_button.clicked.connect(self.toggle_play)
        bottom_panel.addWidget(self.play_button)
        
        # Speed control
        bottom_panel.addWidget(QLabel("Speed:"))
        self.speed_combo = QComboBox()
        self.speed_combo.addItems(["0.5x", "1x", "2x", "4x"])
        self.speed_combo.setCurrentIndex(1)
        self.speed_combo.currentIndexChanged.connect(self.on_speed_changed)
        bottom_panel.addWidget(self.speed_combo)
        
        # Live/Replay toggle
        self.live_toggle = QCheckBox("LIVE Mode")
        self.live_toggle.setChecked(True)
        self.live_toggle.stateChanged.connect(self.on_live_toggled)
        bottom_panel.addWidget(self.live_toggle)
        
        # Timeline slider (disabled in live mode)
        bottom_panel.addWidget(QLabel("Timeline:"))
        self.timeline_slider = QSlider(Qt.Horizontal)
        self.timeline_slider.setEnabled(False)
        self.timeline_slider.setMinimum(0)
        self.timeline_slider.setMaximum(1000)
        self.timeline_slider.valueChanged.connect(self.on_timeline_changed)
        bottom_panel.addWidget(self.timeline_slider, 1)  # Takes more space
        
        # Reload button
        reload_button = QPushButton("Reload Logs")
        reload_button.clicked.connect(self.discover_logs)
        bottom_panel.addWidget(reload_button)
    
    def on_view_changed(self, current, previous):
        if current:
            view_name = current.text()
            self.gl_widget.set_view(view_name)
            self.update_kpi_display(view_name)
    
    def toggle_play(self):
        self.is_playing = not self.is_playing
        self.play_button.setText("▶" if not self.is_playing else "⏸")
    
    def on_speed_changed(self, index):
        speeds = [0.5, 1.0, 2.0, 4.0]
        self.playback_speed = speeds[index]
    
    def on_live_toggled(self, state):
        self.is_live_mode = state == Qt.Checked
        self.live_indicator.setVisible(self.is_live_mode)
        self.timeline_slider.setEnabled(not self.is_live_mode)
        
        if self.is_live_mode:
            self.switch_to_live()
        else:
            self.switch_to_replay()
    
    def on_timeline_changed(self, value):
        if not self.replay_events or self.is_live_mode:
            return
        
        # Calculate time from slider
        max_time = self.get_max_replay_time()
        if max_time > 0:
            self.current_time_ns = int(max_time * value / 1000)
            self.update_states_from_replay()
    
    def discover_logs(self):
        """Discover and load log files"""
        self.log_files = LogDiscoverer.discover_all_logs()
        
        status_text = "Logs found:\n"
        for station, path in self.log_files.items():
            if path:
                status_text += f"{station.value}: {os.path.basename(path)}\n"
            else:
                status_text += f"{station.value}: NOT FOUND\n"
        
        self.log_status_label.setText(status_text)
        
        if self.is_live_mode:
            self.switch_to_live()
        else:
            self.switch_to_replay()
    
    def switch_to_live(self):
        """Switch to live mode (tail logs)"""
        if self.tail_worker:
            self.tail_worker.stop()
            self.tail_worker = None
        
        # Clear replay data
        for key in self.replay_events:
            self.replay_events[key] = []
        
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
        
        # Load all log files
        self.load_replay_data()
        
        # Setup timeline slider
        max_time = self.get_max_replay_time()
        if max_time > 0:
            self.timeline_slider.setValue(0)
        
        # Set to start
        self.current_time_ns = 0
        self.update_states_from_replay()
    
    def load_replay_data(self):
        """Load all events from log files for replay using streaming parser"""
        for station, path in self.log_files.items():
            if not path or not os.path.exists(path):
                continue
            
            try:
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
                
                events = []
                parser = StreamingLogParser()
                
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    
                    event, detected_station = parser.parse_line(line)
                    if event and detected_station == station:
                        events.append(event)
                
                # Sort by time and limit
                events.sort(key=lambda x: x.t_ns)
                self.replay_events[station.value] = events[-MAX_EVENTS_PER_STATION:]
                
                print(f"Loaded {len(events)} events for {station.value}")
                
            except Exception as e:
                print(f"Error loading {path}: {e}")
                traceback.print_exc()
    
    def get_max_replay_time(self):
        """Get maximum timestamp across all replay events"""
        max_time = 0
        for events in self.replay_events.values():
            if events:
                max_time = max(max_time, events[-1].t_ns)
        return max_time
    
    def update_states_from_replay(self):
        """Update states based on current replay time"""
        current_time = self.current_time_ns
        
        # Update station states
        for station in Station.all_stations():
            events = self.replay_events.get(station.value, [])
            state = {}
            
            # Find last event <= current time
            for event in reversed(events):
                if event.t_ns <= current_time:
                    state = {
                        'ready': event.ready,
                        'busy': event.busy,
                        'done': event.done,
                        'fault': event.fault,
                        'cycle_time_ms': event.cycle_time_ms,
                        'extra': event.extra
                    }
                    break
            
            self.live_states[station.value] = state
        
        # Update PLC state
        plc_events = self.replay_events.get("PLC", [])
        plc_state = {}
        for event in reversed(plc_events):
            if event.t_ns <= current_time:
                plc_state = {
                    'state': event.state,
                    'batch_id': event.batch_id,
                    'recipe_id': event.recipe_id,
                    'buffers': event.buffers,
                    'extra': event.extra
                }
                break
        
        self.live_states["PLC"] = plc_state
        
        # Update display
        self.update_display()
    
    def process_new_events(self, new_events_dict):
        """Process new events from tail worker"""
        for station, events in new_events_dict.items():
            for event in events:
                # Update current state
                if station == Station.PLC:
                    self.live_states["PLC"] = {
                        'state': event.state,
                        'batch_id': event.batch_id,
                        'recipe_id': event.recipe_id,
                        'buffers': event.buffers,
                        'extra': event.extra
                    }
                    self.current_time_ns = max(self.current_time_ns, event.t_ns)
                else:
                    self.live_states[station.value] = {
                        'ready': event.ready,
                        'busy': event.busy,
                        'done': event.done,
                        'fault': event.fault,
                        'cycle_time_ms': event.cycle_time_ms,
                        'extra': event.extra
                    }
                    self.current_time_ns = max(self.current_time_ns, event.t_ns)
        
        self.update_display()
    
    def update_animation(self):
        """Update animation based on timer"""
        if self.is_playing and not self.is_live_mode:
            # Advance replay time
            time_delta_ns = int(33_333_333 * self.playback_speed)  # ~30 FPS
            self.current_time_ns += time_delta_ns
            
            max_time = self.get_max_replay_time()
            if max_time > 0:
                if self.current_time_ns > max_time:
                    self.current_time_ns = 0
                
                # Update slider
                slider_value = int(self.current_time_ns * 1000 / max_time)
                self.timeline_slider.blockSignals(True)
                self.timeline_slider.setValue(slider_value)
                self.timeline_slider.blockSignals(False)
            
            self.update_states_from_replay()
        
        # Update time display
        time_str = f"{self.current_time_ns} ns"
        if self.current_time_ns > 1_000_000_000:
            time_str = f"{self.current_time_ns / 1_000_000_000:.2f} s"
        self.time_label.setText(time_str)
        
        # Trigger OpenGL update
        self.gl_widget.current_time_ns = self.current_time_ns
        self.gl_widget.animation_time = time.time()
        self.gl_widget.update()
    
    def update_display(self):
        """Update all displays"""
        # Update OpenGL
        self.gl_widget.update_states(self.live_states, self.live_states.get("PLC", {}))
        
        # Update KPI for current view
        current_item = self.view_list.currentItem()
        if current_item:
            self.update_kpi_display(current_item.text())
    
    def update_kpi_display(self, view_name):
        """Update KPI display for selected view"""
        if view_name == "ALL":
            text = "All Stations Overview\n\n"
            for station in ["ST1", "ST2", "ST3", "ST4", "ST5", "ST6"]:
                state = self.live_states.get(station, {})
                status = "IDLE"
                if state.get('fault', False):
                    status = "FAULT"
                elif state.get('busy', False):
                    status = "BUSY"
                elif state.get('ready', False):
                    status = "READY"
                
                cycle_time = state.get('cycle_time_ms')
                cycle_str = f"{cycle_time:.1f} ms" if cycle_time else "N/A"
                
                text += f"{station}: {status} (Cycle: {cycle_str})\n"
            
            # PLC info
            plc_state = self.live_states.get("PLC", {})
            if plc_state:
                text += f"\nPLC: {plc_state.get('state', 'N/A')}\n"
                if plc_state.get('batch_id'):
                    text += f"Batch: {plc_state['batch_id']}\n"
                if plc_state.get('recipe_id'):
                    text += f"Recipe: {plc_state['recipe_id']}\n"
        
        elif view_name == "PLC":
            state = self.live_states.get("PLC", {})
            text = "PLC Status\n\n"
            text += f"State: {state.get('state', 'N/A')}\n"
            text += f"Batch ID: {state.get('batch_id', 'N/A')}\n"
            text += f"Recipe ID: {state.get('recipe_id', 'N/A')}\n"
            
            if state.get('buffers'):
                text += "\nBuffers:\n"
                for buf, count in state['buffers'].items():
                    text += f"  {buf}: {count}\n"
            
            if state.get('extra'):
                text += "\nExtra Signals:\n"
                for i, (key, value) in enumerate(list(state['extra'].items())[:6]):
                    text += f"  {key}: {value}\n"
        
        else:  # Station view
            state = self.live_states.get(view_name, {})
            text = f"{view_name} Status\n\n"
            text += f"Ready: {state.get('ready', False)}\n"
            text += f"Busy: {state.get('busy', False)}\n"
            text += f"Done: {state.get('done', False)}\n"
            text += f"Fault: {state.get('fault', False)}\n"
            
            cycle_time = state.get('cycle_time_ms')
            if cycle_time:
                text += f"Cycle Time: {cycle_time:.1f} ms\n"
            
            extra = state.get('extra', {})
            if extra:
                text += "\nExtra Signals:\n"
                for i, (key, value) in enumerate(list(extra.items())[:6]):
                    text += f"  {key}: {value}\n"
        
        self.kpi_text.setText(text)
    
    def closeEvent(self, event):
        """Cleanup on close"""
        if self.tail_worker:
            self.tail_worker.stop()
        event.accept()

# ===== MAIN APPLICATION =====
def main():
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