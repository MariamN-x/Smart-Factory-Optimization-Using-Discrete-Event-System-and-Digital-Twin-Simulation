#!/usr/bin/env python3
"""
ST6 Packaging/Dispatch Visualizer - Professional visualization for ST6 packaging logs
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
from enum import Enum

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
SEARCH_ROOT = "."
MAX_EVENTS = 50000
TIMER_FPS = 30
PULSE_DURATION_MS = 300  # For done, package, repair pulses
IDLE_TIMEOUT = 5.0
LONG_IDLE_TIMEOUT = 15.0
USE_PYOPENGL = True
MAX_RECENT_EVENTS = 50  # Number of recent log events to show

# ===== QT IMPORTS =====
try:
    from PySide6.QtWidgets import *
    from PySide6.QtCore import *
    from PySide6.QtGui import QSurfaceFormat, QPainter, QColor, QFont, QPen, QFontMetrics, QBrush
    from PySide6.QtOpenGLWidgets import QOpenGLWidget
    QT_AVAILABLE = True
except Exception as e:
    print(f"ERROR importing PySide6: {e}")
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
else:
    PYOPENGL_AVAILABLE = False

# ===== DATA MODEL =====
@dataclass
class ST6Event:
    t_ns: int
    ready: bool = False
    busy: bool = False
    done: bool = False
    fault: bool = False
    cycle_time_ms: Optional[float] = None
    packages_completed: Optional[int] = None
    arm_cycles: Optional[int] = None
    total_repairs: Optional[int] = None
    operational_time_s: Optional[float] = None
    downtime_s: Optional[float] = None
    availability: Optional[float] = None
    extra: Dict[str, Any] = field(default_factory=dict)

class LogMessageType(Enum):
    INFO = "info"
    RX_META = "rx_meta"
    RX_PACKET = "rx_packet"
    PLC_DECODE = "plc"
    STEPPING = "step"
    DONE_PULSE = "done"
    COUNTER_UPDATE = "counter"
    REPAIR_UPDATE = "repair"
    BATCH_CHANGE = "batch"
    ERROR = "error"
    BLOCK_START = "block_start"

@dataclass
class LogMessage:
    t_ns: int
    msg_type: LogMessageType
    text: str
    details: Dict[str, Any] = field(default_factory=dict)

# ===== BLOCK-BASED PARSER =====
class ST6LogParser:
    """Block-based parser for ST6 Packaging/Dispatch logs"""
    
    def __init__(self):
        self.reset()
    
    def reset(self):
        """Reset parser state"""
        self.last_vsi_time_ns = None
        self.synthetic_time_ns = 0
        self.last_emitted_ts = None
        
        # Block state
        self.current_block = None
        self.block_lines = []
        self.block_vsi_time = None
        
        # Carry-forward state (only for real ST6 fields)
        self.carried_state = {
            'ready': None, 'busy': None, 'done': None, 'fault': None,
            'cmd_start': None, 'cmd_stop': None, 'cmd_reset': None,
            'cycle_time_ms': None,
            'packages_completed': None, 'arm_cycles': None, 'total_repairs': None,
            'operational_time_s': None, 'downtime_s': None, 'availability': None,
            'batch_id': None, 'recipe_id': None
        }
        
        # Track for one-shot DONE pulse
        self.pending_done_pulse = False
        
        # Track latest RX meta info
        self.last_rx_meta = None
        self.last_packet_len = None
        
        # Patterns
        self.block_start_pattern = re.compile(r"^\+=ST6_PackagingDispatch\+=$", re.IGNORECASE)
        self.vsi_time_pattern = re.compile(r"VSI\s+time\s*:\s*(\d+)\s*ns", re.IGNORECASE)
        
        # Section patterns
        self.outputs_section_pattern = re.compile(r"^\s*Outputs:", re.IGNORECASE)
        self.inputs_section_pattern = re.compile(r"^\s*Inputs:", re.IGNORECASE)
        
        # Boolean states in inputs section (handle bad spacing like "F alse")
        self.bool_input_patterns = {
            "cmd_start": re.compile(r"^\s*cmd_start\s*[:=]\s*([^,\s]+)", re.IGNORECASE),
            "cmd_stop": re.compile(r"^\s*cmd_stop\s*[:=]\s*([^,\s]+)", re.IGNORECASE),
            "cmd_reset": re.compile(r"^\s*cmd_reset\s*[:=]\s*([^,\s]+)", re.IGNORECASE),
        }
        
        # Numeric inputs
        self.numeric_input_patterns = {
            "batch_id": re.compile(r"^\s*batch_id\s*[:=]\s*([\d.]+)", re.IGNORECASE),
            "recipe_id": re.compile(r"^\s*recipe_id\s*[:=]\s*([\d.]+)", re.IGNORECASE),
        }
        
        # Boolean states in outputs section
        self.bool_output_patterns = {
            "ready": re.compile(r"^\s*ready\s*[:=]\s*(?:(\d+)|([TtFf].*))", re.IGNORECASE),
            "busy": re.compile(r"^\s*busy\s*[:=]\s*(?:(\d+)|([TtFf].*))", re.IGNORECASE),
            "done": re.compile(r"^\s*done\s*[:=]\s*(?:(\d+)|([TtFf].*))", re.IGNORECASE),
            "fault": re.compile(r"^\s*fault\s*[:=]\s*(?:(\d+)|([TtFf].*))", re.IGNORECASE),
        }
        
        # Cycle time
        self.cycle_time_pattern = re.compile(r"^\s*cycle_time_ms\s*[:=]\s*([\d.]+)", re.IGNORECASE)
        
        # Counters
        self.counters_patterns = {
            "packages_completed": re.compile(r"^\s*packages_completed\s*[:=]\s*(\d+)", re.IGNORECASE),
            "arm_cycles": re.compile(r"^\s*arm_cycles\s*[:=]\s*(\d+)", re.IGNORECASE),
            "total_repairs": re.compile(r"^\s*total_repairs\s*[:=]\s*(\d+)", re.IGNORECASE),
        }
        
        # Floats
        self.float_patterns = {
            "operational_time_s": re.compile(r"^\s*operational_time_s\s*[:=]\s*([\d.]+)", re.IGNORECASE),
            "downtime_s": re.compile(r"^\s*downtime_s\s*[:=]\s*([\d.]+)", re.IGNORECASE),
            "availability": re.compile(r"^\s*availability\s*[:=]\s*([\d.]+)", re.IGNORECASE),
        }
        
        # Log message patterns
        self.rx_meta_pattern = re.compile(r"ST6\s+RX\s+meta\s+dest/src/len:\s*(\d+),\s*(\d+),\s*(\d+)", re.IGNORECASE)
        self.rx_packet_pattern = re.compile(r"ST6:\s*Received\s+(\d+)-byte\s+packet\s+from\s+PLC", re.IGNORECASE)
        self.decoded_plc_pattern = re.compile(r"ST6:\s*Decoded\s+PLC\s+(.+)$", re.IGNORECASE)
        self.stepping_pattern = re.compile(r"ST6:\s*stepping\s+\(.*\)", re.IGNORECASE)
        self.done_pulse_pattern = re.compile(r"ST6:\s*Cycle\s+complete\s*->\s*emitting\s+DONE\s+pulse", re.IGNORECASE)
        
        # For parsing decoded PLC key-values
        self.kv_pattern = re.compile(r"(\w+)\s*=\s*([^,\s]+)")
    
    def seed_from_event(self, event: ST6Event):
        """Seed the parser with an existing event"""
        self.carried_state = {
            'ready': 1 if event.ready else 0,
            'busy': 1 if event.busy else 0,
            'done': 1 if event.done else 0,
            'fault': 1 if event.fault else 0,
            'cmd_start': None, 'cmd_stop': None, 'cmd_reset': None,
            'cycle_time_ms': event.cycle_time_ms,
            'packages_completed': event.packages_completed,
            'arm_cycles': event.arm_cycles,
            'total_repairs': event.total_repairs,
            'operational_time_s': event.operational_time_s,
            'downtime_s': event.downtime_s,
            'availability': event.availability,
            'batch_id': event.extra.get('batch_id'),
            'recipe_id': event.extra.get('recipe_id')
        }
        self.last_vsi_time_ns = event.t_ns
        self.synthetic_time_ns = event.t_ns
        self.last_emitted_ts = event.t_ns
    
    def parse_line(self, line: str) -> Tuple[Optional[ST6Event], Optional[LogMessage]]:
        """Parse a line, returns (event, log_message)"""
        line = line.strip()
        if not line:
            return None, None
        
        log_msg = None
        
        # Handle b'...' prefix
        if line.startswith("b'") and line.endswith("'"):
            line = line[2:-1]
        elif line.startswith('b"') and line.endswith('"'):
            line = line[2:-1]
        
        # ---- Check for new block or flush conditions ----
        should_flush = False
        flush_event = None
        
        # Check if this line starts a new ST6 block
        if self.block_start_pattern.match(line):
            should_flush = True
        
        # Check if we're in a block and this line starts with ST6 log markers
        elif self.current_block and (line.startswith("ST6 RX meta") or line.startswith("ST6:")):
            should_flush = True
        
        # Flush current block if needed
        if should_flush and self.current_block:
            flush_event = self._flush_block()
        
        # ---- Handle the current line ----
        
        # Start new block
        if self.block_start_pattern.match(line):
            self.current_block = {
                'inputs': {},
                'outputs': {},
                'vsi_time': None,
                'lines': []
            }
            self.block_vsi_time = None
            log_msg = LogMessage(
                t_ns=self.last_vsi_time_ns or self.synthetic_time_ns,
                msg_type=LogMessageType.BLOCK_START,
                text="ST6 Block Start"
            )
        
        # If we're in a block, collect the line
        elif self.current_block:
            self.current_block['lines'].append(line)
            
            # Check for VSI time in block
            vsi_match = self.vsi_time_pattern.search(line)
            if vsi_match:
                self.block_vsi_time = int(vsi_match.group(1))
                self.last_vsi_time_ns = self.block_vsi_time
            
            # Check for section markers
            if self.inputs_section_pattern.match(line):
                self.current_block['in_inputs'] = True
                self.current_block['in_outputs'] = False
            elif self.outputs_section_pattern.match(line):
                self.current_block['in_inputs'] = False
                self.current_block['in_outputs'] = True
            else:
                # Parse based on current section
                if self.current_block.get('in_inputs'):
                    self._parse_input_line(line)
                elif self.current_block.get('in_outputs'):
                    self._parse_output_line(line)
        
        # ---- Parse log messages (always) ----
        log_msg = self._parse_log_message(line) or log_msg
        
        # Return flush event if we had one, otherwise None
        return flush_event, log_msg
    
    def _parse_log_message(self, line: str) -> Optional[LogMessage]:
        """Parse log messages that aren't part of blocks"""
        # RX meta line
        rx_meta_match = self.rx_meta_pattern.search(line)
        if rx_meta_match:
            dest, src, length = rx_meta_match.groups()
            self.last_rx_meta = {
                'dest': int(dest),
                'src': int(src),
                'len': int(length)
            }
            return LogMessage(
                t_ns=self.last_vsi_time_ns or self.synthetic_time_ns,
                msg_type=LogMessageType.RX_META,
                text=f"RX meta dest/src/len: {dest}, {src}, {length}",
                details=self.last_rx_meta.copy()
            )
        
        # RX packet line
        rx_packet_match = self.rx_packet_pattern.search(line)
        if rx_packet_match:
            self.last_packet_len = int(rx_packet_match.group(1))
            return LogMessage(
                t_ns=self.last_vsi_time_ns or self.synthetic_time_ns,
                msg_type=LogMessageType.RX_PACKET,
                text=f"Received {self.last_packet_len}-byte packet from PLC",
                details={'packet_len': self.last_packet_len}
            )
        
        # Decoded PLC line
        decoded_match = self.decoded_plc_pattern.search(line)
        if decoded_match:
            return self._parse_decoded_plc(line, decoded_match.group(1))
        
        # Stepping line
        stepping_match = self.stepping_pattern.search(line)
        if stepping_match:
            return LogMessage(
                t_ns=self.last_vsi_time_ns or self.synthetic_time_ns,
                msg_type=LogMessageType.STEPPING,
                text="stepping (latched/busy)",
                details={'line': line[:100]}
            )
        
        # DONE pulse line
        done_pulse_match = self.done_pulse_pattern.search(line)
        if done_pulse_match:
            self.pending_done_pulse = True
            return LogMessage(
                t_ns=self.last_vsi_time_ns or self.synthetic_time_ns,
                msg_type=LogMessageType.DONE_PULSE,
                text="Cycle complete -> emitting DONE pulse",
                details={}
            )
        
        return None
    
    def _parse_decoded_plc(self, line: str, data_str: str) -> LogMessage:
        """Parse Decoded PLC line"""
        details = {}
        
        # Parse key=value pairs
        for match in self.kv_pattern.finditer(data_str):
            key, value = match.groups()
            key_lower = key.lower()
            
            # Handle bad spacing in boolean values (e.g., "F alse")
            value_clean = value.replace(' ', '')
            
            # Convert value
            if value_clean.lower() == 'true':
                v = True
            elif value_clean.lower() == 'false':
                v = False
            else:
                try:
                    v = int(value_clean)
                except ValueError:
                    try:
                        v = float(value_clean)
                    except ValueError:
                        v = value_clean
            
            # Store in carried state if it's a known field
            if key_lower == 'batch':
                self.carried_state['batch_id'] = v
            elif key_lower == 'recipe':
                self.carried_state['recipe_id'] = v
            elif key_lower in ['cmd_start', 'cmd_stop', 'cmd_reset']:
                self.carried_state[key_lower] = v
            
            details[key] = v
        
        # Check for batch/recipe changes
        msg_type = LogMessageType.PLC_DECODE
        if 'batch' in details or 'recipe' in details:
            msg_type = LogMessageType.BATCH_CHANGE
        
        return LogMessage(
            t_ns=self.last_vsi_time_ns or self.synthetic_time_ns,
            msg_type=msg_type,
            text=f"Decoded PLC: {data_str[:80]}{'...' if len(data_str) > 80 else ''}",
            details=details
        )
    
    def _parse_input_line(self, line: str):
        """Parse a line in the Inputs section"""
        if not self.current_block:
            return
        
        # Parse boolean inputs (handle bad spacing)
        for field, pattern in self.bool_input_patterns.items():
            match = pattern.search(line)
            if match:
                value_str = match.group(1).replace(' ', '').lower()
                if value_str in ['1', 'true']:
                    self.current_block['inputs'][field] = True
                elif value_str in ['0', 'false']:
                    self.current_block['inputs'][field] = False
        
        # Parse numeric inputs
        for field, pattern in self.numeric_input_patterns.items():
            match = pattern.search(line)
            if match:
                try:
                    self.current_block['inputs'][field] = int(match.group(1))
                except ValueError:
                    pass
    
    def _parse_output_line(self, line: str):
        """Parse a line in the Outputs section"""
        if not self.current_block:
            return
        
        # Parse boolean outputs
        for field, pattern in self.bool_output_patterns.items():
            match = pattern.search(line)
            if match:
                if match.group(1):  # numeric (0/1)
                    self.current_block['outputs'][field] = int(match.group(1)) == 1
                elif match.group(2):  # True/False (handle bad spacing)
                    value_str = match.group(2).replace(' ', '').lower()
                    self.current_block['outputs'][field] = value_str.startswith('t')
        
        # Parse cycle time
        cycle_match = self.cycle_time_pattern.search(line)
        if cycle_match:
            try:
                self.current_block['outputs']['cycle_time_ms'] = float(cycle_match.group(1))
            except ValueError:
                pass
        
        # Parse counters
        for field, pattern in self.counters_patterns.items():
            match = pattern.search(line)
            if match:
                try:
                    self.current_block['outputs'][field] = int(match.group(1))
                except ValueError:
                    pass
        
        # Parse floats
        for field, pattern in self.float_patterns.items():
            match = pattern.search(line)
            if match:
                try:
                    self.current_block['outputs'][field] = float(match.group(1))
                except ValueError:
                    pass
    
    def _flush_block(self) -> Optional[ST6Event]:
        """Flush the current block and create an event"""
        if not self.current_block or not self.block_vsi_time:
            self.current_block = None
            return None
        
        # Merge block data with carried state
        merged_state = self.carried_state.copy()
        
        # Update from block inputs
        for key, value in self.current_block['inputs'].items():
            if value is not None:
                merged_state[key] = value
        
        # Update from block outputs
        for key, value in self.current_block['outputs'].items():
            if value is not None:
                merged_state[key] = value
        
        # Update carried state
        self.carried_state = merged_state
        
        # Create event
        timestamp = self.block_vsi_time
        
        # Enforce strictly increasing timestamps
        if self.last_emitted_ts is not None and timestamp <= self.last_emitted_ts:
            timestamp = self.last_emitted_ts + 1
        self.last_emitted_ts = timestamp
        
        # Convert to event
        ready = bool(merged_state['ready']) if merged_state['ready'] is not None else False
        busy = bool(merged_state['busy']) if merged_state['busy'] is not None else False
        fault = bool(merged_state['fault']) if merged_state['fault'] is not None else False
        
        # Handle done: use block value, but also check for pending pulse
        done = bool(merged_state['done']) if merged_state['done'] is not None else False
        
        # Build extra dict (only meaningful fields)
        extra = {}
        if merged_state['batch_id'] is not None:
            extra['batch_id'] = merged_state['batch_id']
        if merged_state['recipe_id'] is not None:
            extra['recipe_id'] = merged_state['recipe_id']
        if merged_state['cmd_start'] is not None:
            extra['cmd_start'] = bool(merged_state['cmd_start'])
        if merged_state['cmd_stop'] is not None:
            extra['cmd_stop'] = bool(merged_state['cmd_stop'])
        if merged_state['cmd_reset'] is not None:
            extra['cmd_reset'] = bool(merged_state['cmd_reset'])
        
        # Add RX info if available
        if self.last_rx_meta:
            extra['rx_dest'] = self.last_rx_meta['dest']
            extra['rx_src'] = self.last_rx_meta['src']
            extra['rx_len'] = self.last_rx_meta['len']
        if self.last_packet_len:
            extra['last_packet_len'] = self.last_packet_len
        
        # Create event
        event = ST6Event(
            t_ns=timestamp,
            ready=ready,
            busy=busy,
            done=done,
            fault=fault,
            cycle_time_ms=merged_state['cycle_time_ms'],
            packages_completed=merged_state['packages_completed'],
            arm_cycles=merged_state['arm_cycles'],
            total_repairs=merged_state['total_repairs'],
            operational_time_s=merged_state['operational_time_s'],
            downtime_s=merged_state['downtime_s'],
            availability=merged_state['availability'],
            extra=extra
        )
        
        # Reset block
        self.current_block = None
        self.block_vsi_time = None
        
        return event
    
    def flush(self) -> Optional[ST6Event]:
        """Force flush any pending block"""
        return self._flush_block()

# ===== LOG DISCOVERY =====
class LogDiscoverer:
    @staticmethod
    def find_st6_log() -> Optional[str]:
        candidates = []
        
        def consider_file(full_path: str, file: str):
            fl = file.lower()
            
            # Must be .log file with st6 in name
            if not fl.endswith(".log"):
                return
            if "st6" not in fl:
                return
            
            try:
                mtime = os.path.getmtime(full_path)
                size = os.path.getsize(full_path)
            except OSError:
                return
            
            # Score based on relevance
            score = 0
            if "packagingdispatch" in fl:
                score += 10
            if "packaging" in fl:
                score += 5
            if "dispatch" in fl:
                score += 3
            
            candidates.append((score, size, mtime, full_path))
        
        # Search recursively from SEARCH_ROOT
        for root, dirs, files in os.walk(SEARCH_ROOT):
            for file in files:
                consider_file(os.path.join(root, file), file)
        
        if not candidates:
            return None
        
        # Sort by score, size, and mtime (newest first)
        candidates.sort(key=lambda x: (-x[0], -x[1], -x[2]))
        
        selected = candidates[0][3]
        print(f"Selected ST6 log: {selected}")
        return selected

# ===== TAIL WORKER =====
class LogTailWorker(QThread):
    new_event = Signal(ST6Event)
    new_log_message = Signal(LogMessage)
    activity_detected = Signal()
    file_reopened = Signal()
    
    def __init__(self, log_path: str, seed_event: Optional[ST6Event] = None):
        super().__init__()
        self.log_path = log_path
        self.seed_event = seed_event
        self.running = True
        self.file_handle = None
        self.file_position = 0
        self.file_inode = None
        self.parser = ST6LogParser()
        self.last_activity_time = time.time()
    
    def run(self):
        """Tail the log file and emit new events"""
        self._open_file(seed=True)
        
        while self.running:
            try:
                if not self.file_handle:
                    time.sleep(0.1)
                    continue
                
                # Check for file rotation/truncation
                try:
                    current_size = os.path.getsize(self.log_path)
                    current_inode = os.stat(self.log_path).st_ino
                    
                    if current_inode != self.file_inode or current_size < self.file_position:
                        print("File rotated/truncated, reopening...")
                        self._open_file(seed=True)
                        self.file_reopened.emit()
                        continue
                except (OSError, IOError):
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
                        event, log_msg = self.parser.parse_line(line)
                        if event:
                            self.new_event.emit(event)
                        if log_msg:
                            self.new_log_message.emit(log_msg)
                
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
            
            self.file_inode = os.stat(self.log_path).st_ino
            
            # Reset parser
            self.parser = ST6LogParser()
            
            # Seed if provided
            if seed and self.seed_event:
                self.parser.seed_from_event(self.seed_event)
                
        except Exception as e:
            print(f"Error opening {self.log_path}: {e}")
            self.file_handle = None
    
    def stop(self):
        """Stop the worker thread"""
        self.running = False
        if self.isRunning():
            self.wait()

# ===== OPENGL WIDGET =====
class ST6OpenGLWidget(QOpenGLWidget):
    """OpenGL visualization widget for ST6 Packaging/Dispatch"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # Current state
        self.current_state = {
            'ready': False, 'busy': False, 'done': False, 'fault': False,
            'cycle_time_ms': None,
            'packages_completed': 0, 'arm_cycles': 0, 'total_repairs': 0,
            'operational_time_s': 0.0, 'downtime_s': 0.0, 'availability': 0.0,
            'extra': {}
        }
        
        # Previous state for edge detection
        self.prev_state = self.current_state.copy()
        
        # Pulse animations
        self.done_pulse_time = 0
        self.package_pulse_time = 0
        self.repair_pulse_time = 0
        self.arm_tick_time = 0
        
        # Progress tracking
        self.cycle_start_time = 0
        self.cycle_duration = 0
        self.cycle_progress = 0.0
        
        # Animation states
        self.conveyor_offset = 0.0
        self.arm_angle = 0.0
        self.arm_phase = 0  # 0: home, 1: pickup, 2: place, 3: retract
        self.package_pos = [-3.0, 0.3, 0.0]  # Start on conveyor
        self.package_on_conveyor = True
        self.package_on_table = False
        self.package_in_chute = False
        
        # Fault animation
        self.fault_flash = 0.0
        self.fault_shake = 0.0
        
        # State-based highlighting
        self.ready_glow = 0.0
        self.busy_glow = 0.0
        
        # Mode
        self.mode = "LIVE"
        
        # Theme accent color (cyan, green, orange)
        self.accent_color = "cyan"
        self.accent_colors = {
            "cyan": (0.0, 0.8, 0.8, 1.0),
            "green": (0.3, 0.8, 0.3, 1.0),
            "orange": (0.95, 0.6, 0.2, 1.0)
        }
        
        # Modern color palette (dark theme)
        self.colors = {
            # Background and grid
            "bg_dark": (0.05, 0.06, 0.08, 1.0),
            "bg_grid": (0.12, 0.13, 0.15, 1.0),
            "grid_lines": (0.20, 0.21, 0.23, 1.0),
            
            # Equipment
            "conveyor_base": (0.25, 0.27, 0.30, 1.0),
            "conveyor_belt": (0.35, 0.37, 0.40, 1.0),
            "conveyor_stripe": (0.50, 0.52, 0.55, 1.0),
            "table_top": (0.30, 0.32, 0.35, 1.0),
            "table_leg": (0.40, 0.42, 0.45, 1.0),
            "arm_base": (0.35, 0.37, 0.40, 1.0),
            "arm_link1": (0.45, 0.47, 0.50, 1.0),
            "arm_link2": (0.55, 0.57, 0.60, 1.0),
            "gripper": (0.65, 0.67, 0.70, 1.0),
            "chute_base": (0.30, 0.32, 0.35, 1.0),
            "chute_ramp": (0.40, 0.42, 0.45, 1.0),
            
            # Package
            "package_base": (0.20, 0.60, 0.30, 1.0),  # Soft green
            "package_label": (0.95, 0.95, 0.95, 0.9),
            
            # State colors
            "ready": (0.30, 0.70, 0.40, 1.0),  # Soft green
            "busy": (0.95, 0.75, 0.20, 1.0),   # Amber
            "done_pulse": (0.00, 0.80, 0.80, 1.0),  # Cyan
            "package_pulse": (0.30, 0.80, 0.30, 1.0),  # Green
            "repair_pulse": (0.90, 0.30, 0.30, 1.0),  # Red
            "arm_tick": (0.95, 0.95, 0.30, 1.0),  # Yellow
            "fault": (0.90, 0.20, 0.20, 1.0),   # Red
            "fault_flash": (0.90, 0.20, 0.20, 0.5),  # Red with alpha
            
            # Text/labels
            "label": (0.80, 0.82, 0.85, 1.0),
        }
        
        # Camera
        self.camera_distance = 14.0
        self.camera_angle_x = 30.0
        self.camera_angle_y = 45.0
        self.last_mouse_pos = None
        
        # Lighting
        self.light_pos = [5.0, 10.0, 5.0, 1.0]
        self.light_ambient = [0.15, 0.16, 0.18, 1.0]
        self.light_diffuse = [0.65, 0.66, 0.68, 1.0]
        self.light_specular = [0.15, 0.16, 0.18, 1.0]  # Reduced specular
        
        self.setMouseTracking(True)
    
    def set_accent_color(self, color_name: str):
        """Set the accent color for highlights"""
        if color_name in self.accent_colors:
            self.accent_color = color_name
            # Update the done pulse color
            self.colors["done_pulse"] = self.accent_colors[color_name]
            self.update()
    
    def update_animations(self, now):
        """Update all animations based on current time"""
        
        # Update state-based glows
        self.ready_glow = 0.3 if self.current_state['ready'] else 0.0
        self.busy_glow = 0.4 if self.current_state['busy'] else 0.0
        
        # Update fault animation
        if self.current_state['fault']:
            self.fault_flash = 0.5 + 0.5 * math.sin(now * 5.0)
            self.fault_shake = 0.1 * math.sin(now * 10.0)
        else:
            self.fault_flash = 0.0
            self.fault_shake = 0.0
        
        # Conveyor movement when busy and not in fault
        if self.current_state['busy'] and not self.current_state['fault']:
            self.conveyor_offset += 0.08
            if self.conveyor_offset > 1.0:
                self.conveyor_offset = 0.0
        
        # Package movement (only when busy and not in fault)
        if self.current_state['busy'] and not self.current_state['fault']:
            # Calculate progress through cycle
            if self.cycle_duration > 0:
                elapsed = now - self.cycle_start_time
                self.cycle_progress = min(1.0, elapsed / (self.cycle_duration / 1000.0))
                
                # Move package through stations
                if self.cycle_progress < 0.25:
                    # On conveyor moving to table
                    t = self.cycle_progress / 0.25
                    self.package_pos[0] = -3.0 + 3.0 * t
                    self.package_on_conveyor = True
                    self.package_on_table = False
                    self.package_in_chute = False
                elif self.cycle_progress < 0.5:
                    # On table being packaged
                    self.package_pos[0] = 0.0
                    self.package_pos[1] = 0.8
                    self.package_on_conveyor = False
                    self.package_on_table = True
                    self.package_in_chute = False
                elif self.cycle_progress < 0.75:
                    # Arm moving package to chute
                    t = (self.cycle_progress - 0.5) / 0.25
                    self.package_pos[0] = 3.0 * t
                    self.package_pos[1] = 0.8 - 0.5 * t
                    self.package_on_table = False
                else:
                    # In chute
                    self.package_pos[0] = 3.0
                    self.package_pos[1] = 0.3
                    self.package_in_chute = True
        
        # Arm animation (only when busy and not in fault)
        if self.current_state['busy'] and not self.current_state['fault']:
            # Arm follows package
            if self.cycle_progress < 0.25:
                self.arm_phase = 0  # Home position
                self.arm_angle = 0.0
            elif self.cycle_progress < 0.5:
                self.arm_phase = 1  # Pickup from conveyor
                self.arm_angle = -45.0 * (self.cycle_progress - 0.25) / 0.25
            elif self.cycle_progress < 0.75:
                self.arm_phase = 2  # Place on table
                self.arm_angle = -45.0 + 90.0 * (self.cycle_progress - 0.5) / 0.25
            else:
                self.arm_phase = 3  # Retract
                self.arm_angle = 45.0 - 45.0 * (self.cycle_progress - 0.75) / 0.25
    
    def update_state(self, event: ST6Event, mode: str = "LIVE"):
        """Update current state from event"""
        now = time.time()
        self.mode = mode
        
        # Detect rising edges with explicit None checks
        if event.done and not self.prev_state['done']:
            self.done_pulse_time = now
        
        if event.packages_completed is not None:
            if self.prev_state['packages_completed'] is not None:
                if event.packages_completed > self.prev_state['packages_completed']:
                    self.package_pulse_time = now
            elif event.packages_completed > 0:
                self.package_pulse_time = now
        
        if event.total_repairs is not None:
            if self.prev_state['total_repairs'] is not None:
                if event.total_repairs > self.prev_state['total_repairs']:
                    self.repair_pulse_time = now
            elif event.total_repairs > 0:
                self.repair_pulse_time = now
        
        if event.arm_cycles is not None:
            if self.prev_state['arm_cycles'] is not None:
                if event.arm_cycles > self.prev_state['arm_cycles']:
                    self.arm_tick_time = now
            elif event.arm_cycles > 0:
                self.arm_tick_time = now
        
        # Update current state with explicit None checks
        new_state = {
            'ready': event.ready,
            'busy': event.busy,
            'done': event.done,
            'fault': event.fault,
            'cycle_time_ms': event.cycle_time_ms if event.cycle_time_ms is not None else self.current_state['cycle_time_ms'],
            'packages_completed': event.packages_completed if event.packages_completed is not None else self.current_state['packages_completed'],
            'arm_cycles': event.arm_cycles if event.arm_cycles is not None else self.current_state['arm_cycles'],
            'total_repairs': event.total_repairs if event.total_repairs is not None else self.current_state['total_repairs'],
            'operational_time_s': event.operational_time_s if event.operational_time_s is not None else self.current_state['operational_time_s'],
            'downtime_s': event.downtime_s if event.downtime_s is not None else self.current_state['downtime_s'],
            'availability': event.availability if event.availability is not None else self.current_state['availability'],
            'extra': event.extra.copy() if event.extra else self.current_state['extra'].copy()
        }
        
        # Handle busy rising edge
        if event.busy and not self.prev_state['busy']:
            self.cycle_start_time = now
            self.cycle_duration = event.cycle_time_ms or 5000.0  # Default 5s
        
        # Reset package on done or fault
        if (event.done and not self.prev_state['done']) or (event.fault and not self.prev_state['fault']):
            self.package_pos = [-3.0, 0.3, 0.0]
            self.package_on_conveyor = True
            self.package_on_table = False
            self.package_in_chute = False
            self.cycle_progress = 0.0
        
        # Store previous state
        self.prev_state = self.current_state.copy()
        self.current_state = new_state
        
        self.update()
    
    def trigger_done_pulse(self):
        """Trigger done pulse animation (for log messages)"""
        self.done_pulse_time = time.time()
        self.update()
    
    def initializeGL(self):
        if not PYOPENGL_AVAILABLE:
            return
        
        glClearColor(*self.colors["bg_dark"])
        glEnable(GL_DEPTH_TEST)
        glEnable(GL_LIGHTING)
        glEnable(GL_LIGHT0)
        glEnable(GL_COLOR_MATERIAL)
        glColorMaterial(GL_FRONT_AND_BACK, GL_AMBIENT_AND_DIFFUSE)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        
        # Enable normalization for better lighting
        glEnable(GL_NORMALIZE)
        
        # Setup lighting
        glLightfv(GL_LIGHT0, GL_POSITION, self.light_pos)
        glLightfv(GL_LIGHT0, GL_AMBIENT, self.light_ambient)
        glLightfv(GL_LIGHT0, GL_DIFFUSE, self.light_diffuse)
        glLightfv(GL_LIGHT0, GL_SPECULAR, self.light_specular)
        
        # Material properties (reduced shininess)
        glMaterialfv(GL_FRONT, GL_SPECULAR, [0.1, 0.1, 0.1, 1.0])
        glMaterialf(GL_FRONT, GL_SHININESS, 10.0)
        
        # Smooth shading
        glShadeModel(GL_SMOOTH)
        
        # Enable face culling for better performance
        glEnable(GL_CULL_FACE)
        glCullFace(GL_BACK)
    
    def resizeGL(self, w, h):
        if not PYOPENGL_AVAILABLE:
            return
        
        glViewport(0, 0, w, h)
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        aspect = w / h if h > 0 else 1.0
        gluPerspective(45, aspect, 0.1, 100.0)
        glMatrixMode(GL_MODELVIEW)
    
    def paintGL(self):
        if not PYOPENGL_AVAILABLE:
            self.paintBasic()
            return
        
        try:
            glClearColor(*self.colors["bg_dark"])
            glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
            glLoadIdentity()
            
            # Apply fault shake
            shake_x = self.fault_shake if self.current_state['fault'] else 0.0
            
            # Camera positioning
            cam_x = self.camera_distance * math.cos(math.radians(self.camera_angle_y)) * math.cos(math.radians(self.camera_angle_x))
            cam_y = self.camera_distance * math.sin(math.radians(self.camera_angle_x))
            cam_z = self.camera_distance * math.sin(math.radians(self.camera_angle_y)) * math.cos(math.radians(self.camera_angle_x))
            
            gluLookAt(cam_x + shake_x, cam_y, cam_z, shake_x, 0, 0, 0, 1, 0)
            
            # Update animations
            self.update_animations(time.time())
            
            # Draw scene
            self.draw_scene()
            
            # Draw overlay
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            self.draw_overlay(painter)
            painter.end()
                
        except Exception as e:
            print(f"OpenGL error: {e}")
    
    def draw_scene(self):
        """Draw the packaging cell"""
        # Draw grid
        self.draw_grid()
        
        # Draw packaging cell components with state-based effects
        self.draw_conveyor()
        self.draw_packaging_table()
        self.draw_robotic_arm()
        self.draw_package()
        self.draw_dispatch_chute()
        
        # Draw pulses
        now = time.time()
        if now - self.done_pulse_time < PULSE_DURATION_MS / 1000.0:
            self.draw_done_pulse()
        if now - self.package_pulse_time < PULSE_DURATION_MS / 1000.0:
            self.draw_package_pulse()
        if now - self.repair_pulse_time < PULSE_DURATION_MS / 1000.0:
            self.draw_repair_pulse()
        if now - self.arm_tick_time < PULSE_DURATION_MS / 1000.0:
            self.draw_arm_tick()
        
        # Draw fault overlay
        if self.current_state['fault']:
            self.draw_fault_overlay()
    
    def draw_grid(self):
        """Draw floor grid with subtle coloring"""
        if not PYOPENGL_AVAILABLE:
            return
        
        glColor4f(*self.colors["grid_lines"])
        glLineWidth(1.0)
        glBegin(GL_LINES)
        
        size = 12.0
        steps = 24
        
        for i in range(-steps, steps + 1):
            x = i * (size / steps)
            glVertex3f(x, 0, -size/2)
            glVertex3f(x, 0, size/2)
            glVertex3f(-size/2, 0, x)
            glVertex3f(size/2, 0, x)
        
        glEnd()
    
    def draw_conveyor(self):
        """Draw input conveyor with moving stripes"""
        if not PYOPENGL_AVAILABLE:
            return
        
        # Conveyor base
        base_color = list(self.colors["conveyor_base"])
        if self.busy_glow > 0:
            base_color[0] += self.busy_glow * 0.2
            base_color[1] += self.busy_glow * 0.2
            base_color[2] += self.busy_glow * 0.2
        glColor4f(*base_color)
        
        glPushMatrix()
        glTranslatef(-1.5, 0.1, 0)
        glScalef(3.0, 0.15, 0.9)
        self.draw_cube()
        glPopMatrix()
        
        # Conveyor belt
        belt_color = list(self.colors["conveyor_belt"])
        if self.busy_glow > 0:
            belt_color[0] += self.busy_glow * 0.3
            belt_color[1] += self.busy_glow * 0.3
            belt_color[2] += self.busy_glow * 0.3
        glColor4f(*belt_color)
        
        glPushMatrix()
        glTranslatef(-1.5, 0.2, 0)
        glScalef(3.0, 0.05, 0.7)
        self.draw_cube()
        glPopMatrix()
        
        # Moving stripes (when busy)
        if self.current_state['busy'] and not self.current_state['fault']:
            stripe_color = list(self.colors["conveyor_stripe"])
            if self.busy_glow > 0:
                stripe_color[0] += self.busy_glow * 0.4
                stripe_color[1] += self.busy_glow * 0.4
                stripe_color[2] += self.busy_glow * 0.4
            glColor4f(*stripe_color)
            
            for i in range(-6, 7):
                pos = (i + self.conveyor_offset) * 0.5
                if -1.5 <= pos <= 1.5:
                    glPushMatrix()
                    glTranslatef(pos, 0.23, 0)
                    glScalef(0.08, 0.03, 0.6)
                    self.draw_cube()
                    glPopMatrix()
    
    def draw_packaging_table(self):
        """Draw packaging table with realistic details"""
        if not PYOPENGL_AVAILABLE:
            return
        
        # Table top with ready glow
        table_color = list(self.colors["table_top"])
        if self.ready_glow > 0:
            table_color[0] += self.ready_glow * 0.3
            table_color[1] += self.ready_glow * 0.3
        glColor4f(*table_color)
        
        glPushMatrix()
        glTranslatef(0, 0.8, 0)
        glScalef(1.2, 0.1, 0.9)
        self.draw_cube()
        glPopMatrix()
        
        # Table legs
        glColor4f(*self.colors["table_leg"])
        leg_positions = [(-0.5, 0.4, -0.35), (0.5, 0.4, -0.35), (-0.5, 0.4, 0.35), (0.5, 0.4, 0.35)]
        for x, y, z in leg_positions:
            glPushMatrix()
            glTranslatef(x, y, z)
            glScalef(0.08, 0.8, 0.08)
            self.draw_cylinder(16)
            glPopMatrix()
        
        # Table border (subtle)
        if self.ready_glow > 0:
            glColor4f(0.4, 0.7, 0.4, 0.5 + self.ready_glow * 0.3)
            glLineWidth(2.0 + self.ready_glow * 2.0)
            glPushMatrix()
            glTranslatef(0, 0.85, 0)
            glScalef(1.21, 0.11, 0.91)
            self.draw_wireframe_cube()
            glPopMatrix()
    
    def draw_robotic_arm(self):
        """Draw robotic arm with smooth shading"""
        if not PYOPENGL_AVAILABLE:
            return
        
        # Arm base
        glColor4f(*self.colors["arm_base"])
        glPushMatrix()
        glTranslatef(0, 0.25, 0)
        glScalef(0.35, 0.5, 0.35)
        self.draw_cylinder(16)
        glPopMatrix()
        
        # First link (vertical)
        glColor4f(*self.colors["arm_link1"])
        glPushMatrix()
        glTranslatef(0, 0.7, 0)
        glRotatef(self.arm_angle, 0, 1, 0)
        glScalef(0.12, 0.9, 0.12)
        self.draw_cylinder(12)
        glPopMatrix()
        
        # Second link (horizontal)
        glColor4f(*self.colors["arm_link2"])
        glPushMatrix()
        glTranslatef(0, 1.15, 0)
        glRotatef(self.arm_angle, 0, 1, 0)
        glTranslatef(0.6, 0, 0)
        glRotatef(90, 0, 0, 1)
        glScalef(0.1, 0.6, 0.1)
        self.draw_cylinder(12)
        glPopMatrix()
        
        # Gripper
        glColor4f(*self.colors["gripper"])
        gripper_x = 0.6 * math.cos(math.radians(self.arm_angle))
        gripper_y = 1.15
        gripper_z = 0.6 * math.sin(math.radians(self.arm_angle))
        
        glPushMatrix()
        glTranslatef(gripper_x, gripper_y, gripper_z)
        
        # Gripper base
        glPushMatrix()
        glScalef(0.2, 0.15, 0.2)
        self.draw_cube()
        glPopMatrix()
        
        # Gripper fingers
        glPushMatrix()
        glTranslatef(-0.15, -0.05, 0)
        glScalef(0.06, 0.2, 0.1)
        self.draw_cube()
        glPopMatrix()
        
        glPushMatrix()
        glTranslatef(0.15, -0.05, 0)
        glScalef(0.06, 0.2, 0.1)
        self.draw_cube()
        glPopMatrix()
        
        glPopMatrix()
    
    def draw_package(self):
        """Draw package with label"""
        if not PYOPENGL_AVAILABLE:
            return
        
        # Apply fault shake to package
        shake_x = self.fault_shake if self.current_state['fault'] else 0.0
        
        glColor4f(*self.colors["package_base"])
        
        glPushMatrix()
        glTranslatef(self.package_pos[0] + shake_x, self.package_pos[1], self.package_pos[2])
        
        # Package body
        glPushMatrix()
        glScalef(0.35, 0.25, 0.25)
        self.draw_cube()
        glPopMatrix()
        
        # Package label
        glColor4f(*self.colors["package_label"])
        glPushMatrix()
        glTranslatef(0, 0.15, 0.135)
        glScalef(0.3, 0.2, 0.01)
        self.draw_cube()
        glPopMatrix()
        
        # Package outline (subtle)
        glColor4f(0.9, 0.9, 0.9, 0.3)
        glLineWidth(1.5)
        glPushMatrix()
        glScalef(0.36, 0.26, 0.26)
        self.draw_wireframe_cube()
        glPopMatrix()
        
        glPopMatrix()
    
    def draw_dispatch_chute(self):
        """Draw dispatch chute"""
        if not PYOPENGL_AVAILABLE:
            return
        
        # Chute base
        glColor4f(*self.colors["chute_base"])
        glPushMatrix()
        glTranslatef(3.0, 0.55, 0)
        glScalef(0.9, 0.5, 0.7)
        self.draw_cube()
        glPopMatrix()
        
        # Chute ramp
        glColor4f(*self.colors["chute_ramp"])
        glPushMatrix()
        glTranslatef(3.0, 0.3, 0)
        glRotatef(-25, 0, 0, 1)
        glTranslatef(0.5, -0.25, 0)
        glScalef(1.2, 0.08, 0.6)
        self.draw_cube()
        glPopMatrix()
        
        # Chute opening
        glColor4f(0.2, 0.22, 0.25, 1.0)
        glPushMatrix()
        glTranslatef(3.45, 0.15, 0)
        glScalef(0.1, 0.3, 0.5)
        self.draw_cube()
        glPopMatrix()
    
    def draw_done_pulse(self):
        """Draw done pulse effect"""
        if not PYOPENGL_AVAILABLE:
            return
        
        now = time.time()
        pulse_progress = (now - self.done_pulse_time) / (PULSE_DURATION_MS / 1000.0)
        alpha = 1.0 - pulse_progress
        scale = 1.0 + pulse_progress * 1.5
        
        glColor4f(self.colors["done_pulse"][0],
                 self.colors["done_pulse"][1],
                 self.colors["done_pulse"][2],
                 alpha * 0.7)
        
        # Pulse ring around table
        glPushMatrix()
        glTranslatef(0, 0.85, 0)
        glScalef(scale * 1.3, scale * 0.15, scale * 1.0)
        self.draw_wireframe_cube()
        glPopMatrix()
    
    def draw_package_pulse(self):
        """Draw package completed pulse"""
        if not PYOPENGL_AVAILABLE:
            return
        
        now = time.time()
        pulse_progress = (now - self.package_pulse_time) / (PULSE_DURATION_MS / 1000.0)
        alpha = 1.0 - pulse_progress
        scale = 1.0 + pulse_progress * 0.5
        
        glColor4f(self.colors["package_pulse"][0],
                 self.colors["package_pulse"][1],
                 self.colors["package_pulse"][2],
                 alpha * 0.8)
        
        # Green flash at chute
        glPushMatrix()
        glTranslatef(3.0, 0.8, 0)
        glScalef(scale * 0.6, scale * 0.6, scale * 0.6)
        self.draw_cube()
        glPopMatrix()
    
    def draw_repair_pulse(self):
        """Draw repair pulse with subtle shake"""
        if not PYOPENGL_AVAILABLE:
            return
        
        now = time.time()
        pulse_progress = (now - self.repair_pulse_time) / (PULSE_DURATION_MS / 1000.0)
        alpha = 1.0 - pulse_progress
        shake = math.sin(pulse_progress * 15 * math.pi) * 0.05 * alpha
        
        glColor4f(self.colors["repair_pulse"][0],
                 self.colors["repair_pulse"][1],
                 self.colors["repair_pulse"][2],
                 alpha * 0.7)
        
        # Red flash with shake
        glPushMatrix()
        glTranslatef(shake, 0, 0)
        glTranslatef(0, 1.2, 0)
        glScalef(0.7, 0.7, 0.7)
        self.draw_cube()
        glPopMatrix()
    
    def draw_arm_tick(self):
        """Draw arm cycle tick effect"""
        if not PYOPENGL_AVAILABLE:
            return
        
        now = time.time()
        pulse_progress = (now - self.arm_tick_time) / (PULSE_DURATION_MS / 1000.0)
        alpha = 1.0 - pulse_progress
        
        glColor4f(self.colors["arm_tick"][0],
                 self.colors["arm_tick"][1],
                 self.colors["arm_tick"][2],
                 alpha * 0.6)
        
        # Yellow highlight on arm joint
        glPushMatrix()
        glTranslatef(0, 1.15, 0)
        glScalef(0.25, 0.25, 0.25)
        self.draw_cube()
        glPopMatrix()
    
    def draw_fault_overlay(self):
        """Draw fault overlay with flashing border"""
        if not PYOPENGL_AVAILABLE:
            return
        
        # Red flashing border
        alpha = self.fault_flash * 0.7
        glColor4f(self.colors["fault_flash"][0],
                 self.colors["fault_flash"][1],
                 self.colors["fault_flash"][2],
                 alpha)
        
        glLineWidth(3.0)
        glPushMatrix()
        glTranslatef(0, 0.5, 0)
        glScalef(4.5, 1.2, 1.5)
        self.draw_wireframe_cube()
        glPopMatrix()
        
        # Red overlay on equipment
        glColor4f(0.9, 0.2, 0.2, alpha * 0.3)
        glPushMatrix()
        glTranslatef(0, 0.5, 0)
        glScalef(4.0, 1.0, 1.0)
        self.draw_cube()
        glPopMatrix()
    
    def draw_cube(self):
        """Draw a cube with smooth normals"""
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
    
    def draw_cylinder(self, sides=16):
        """Draw a cylinder"""
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
    
    def draw_overlay(self, painter):
        """Draw debug overlay with correct mode"""
        font = QFont("Monospace", 10)
        painter.setFont(font)
        
        # Mode and state
        state_text = f"Mode: {self.mode} | R={int(self.current_state['ready'])} B={int(self.current_state['busy'])} D={int(self.current_state['done'])} F={int(self.current_state['fault'])}"
        
        # Cycle time
        cycle_text = f"Cycle: {self.current_state['cycle_time_ms'] or 'N/A'} ms | Progress: {self.cycle_progress*100:.0f}%"
        
        # Counters
        counters_text = f"Packages: {self.current_state['packages_completed']} | Arm Cycles: {self.current_state['arm_cycles']} | Repairs: {self.current_state['total_repairs']}"
        
        # Times and availability
        op_time = self.current_state['operational_time_s'] or 0.0
        down_time = self.current_state['downtime_s'] or 0.0
        avail = self.current_state['availability'] or 0.0
        time_text = f"Op: {op_time:.1f}s | Down: {down_time:.1f}s | Avail: {avail:.1f}%"
        
        # Pulse flags
        now = time.time()
        pulses = []
        if now - self.done_pulse_time < PULSE_DURATION_MS / 1000.0:
            pulses.append("DONE")
        if now - self.package_pulse_time < PULSE_DURATION_MS / 1000.0:
            pulses.append("PKG")
        if now - self.repair_pulse_time < PULSE_DURATION_MS / 1000.0:
            pulses.append("REPAIR")
        if now - self.arm_tick_time < PULSE_DURATION_MS / 1000.0:
            pulses.append("ARM")
        pulse_text = f"Pulses: {', '.join(pulses) if pulses else 'None'}"
        
        # Station labels
        station_font = QFont("Arial", 9, QFont.Bold)
        painter.setFont(station_font)
        painter.setPen(QPen(QColor(200, 200, 220), 1))
        
        # Draw station labels
        labels = [
            ("CONVEYOR", -220, 50),
            ("PACKAGING TABLE", -50, 50),
            ("DISPATCH CHUTE", 150, 50)
        ]
        
        for text, x, y in labels:
            painter.drawText(self.width() // 2 + x, self.height() - y, text)
        
        # Draw debug info
        painter.setFont(font)
        painter.setPen(QPen(QColor(255, 255, 255), 1))
        
        y_offset = 20
        line_height = 20
        
        texts = [state_text, cycle_text, counters_text, time_text, pulse_text]
        
        for i, text in enumerate(texts):
            metrics = QFontMetrics(font)
            text_width = metrics.horizontalAdvance(text)
            painter.fillRect(10, y_offset + i * line_height - 15, text_width + 10, line_height, QColor(0, 0, 0, 180))
            painter.drawText(15, y_offset + i * line_height, text)
    
    def paintBasic(self):
        """Basic painting when OpenGL is not available"""
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(13, 15, 20))  # Dark background
        
        center_x = self.width() // 2
        center_y = self.height() // 2
        
        # Draw simple representation
        painter.setPen(QPen(QColor(200, 200, 220), 2))
        painter.setFont(QFont("Arial", 14, QFont.Bold))
        painter.drawText(center_x - 100, center_y - 100, "ST6 Packaging Cell")
        
        # Draw status
        painter.setFont(QFont("Monospace", 10))
        painter.drawText(center_x - 100, center_y - 70, f"Packages: {self.current_state['packages_completed']}")
        painter.drawText(center_x - 100, center_y - 50, f"Busy: {self.current_state['busy']}")
        painter.drawText(center_x - 100, center_y - 30, f"Fault: {self.current_state['fault']}")
        
        self.draw_overlay(painter)
    
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
        self.camera_distance = max(8.0, min(25.0, self.camera_distance - delta * 0.01))
        self.update()

# ===== MAIN WINDOW =====
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ST6 Packaging/Dispatch Visualizer")
        self.setGeometry(100, 100, 1600, 900)
        
        # Data
        self.log_path = None
        self.replay_events = []
        self.replay_log_messages = []
        self.replay_times_ns = []
        self.replay_min_time = 0
        self.replay_max_time = 0
        self.current_time_ns = 0
        self.is_live_mode = True
        self.is_playing = True
        self.playback_speed = 1.0
        self.raw_event_count = 0
        self.accepted_event_count = 0
        self.last_state_snapshot = None
        self.last_activity_time = time.time()
        self.is_idle = False
        self.idle_start_time = 0.0
        
        # Recent log events
        self.recent_log_messages = []
        
        # Latest RX info
        self.latest_rx_info = None
        self.latest_plc_decode = None
        
        # Threads
        self.tail_worker = None
        
        # UI
        self.init_ui()
        
        # Timer
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_animation)
        self.timer.start(1000 // TIMER_FPS)
        
        # Initial log discovery
        self.discover_log()
    
    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(5)
        
        # Top info bar
        info_layout = QHBoxLayout()
        
        self.log_info_label = QLabel("Searching for ST6 log...")
        self.log_info_label.setStyleSheet("font-weight: bold; padding: 3px;")
        info_layout.addWidget(self.log_info_label)
        
        self.event_count_label = QLabel("Parsed: 0 | State Updates: 0")
        info_layout.addWidget(self.event_count_label)
        
        self.debug_label = QLabel("Mode: LIVE | Waiting for data...")
        self.debug_label.setStyleSheet("font-family: monospace; padding: 3px; color: #cccccc;")
        info_layout.addWidget(self.debug_label)
        
        info_layout.addStretch()
        main_layout.addLayout(info_layout)
        
        # Center and right panel
        content_layout = QHBoxLayout()
        content_layout.setSpacing(10)
        
        # OpenGL widget
        self.gl_widget = ST6OpenGLWidget()
        content_layout.addWidget(self.gl_widget, 3)
        
        # Right panel (scrollable)
        right_panel = QScrollArea()
        right_panel.setWidgetResizable(True)
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setSpacing(10)
        right_layout.setContentsMargins(10, 10, 10, 10)
        
        # Theme accent selector
        accent_group = QGroupBox("Theme Accent")
        accent_layout = QHBoxLayout()
        self.accent_combo = QComboBox()
        self.accent_combo.addItems(["Cyan", "Green", "Orange"])
        self.accent_combo.currentTextChanged.connect(self.on_accent_changed)
        accent_layout.addWidget(QLabel("Accent:"))
        accent_layout.addWidget(self.accent_combo)
        accent_layout.addStretch()
        accent_group.setLayout(accent_layout)
        right_layout.addWidget(accent_group)
        
        # Batch/Recipe info
        info_group = QGroupBox("Batch / Recipe Info")
        info_layout = QVBoxLayout()
        
        self.batch_label = QLabel("Batch ID: N/A")
        self.recipe_label = QLabel("Recipe ID: N/A")
        self.cmd_start_label = QLabel("cmd_start: N/A")
        self.cmd_stop_label = QLabel("cmd_stop: N/A")
        self.cmd_reset_label = QLabel("cmd_reset: N/A")
        
        for label in [self.batch_label, self.recipe_label, self.cmd_start_label, self.cmd_stop_label, self.cmd_reset_label]:
            label.setStyleSheet("font-family: monospace; padding: 2px;")
            info_layout.addWidget(label)
        
        info_group.setLayout(info_layout)
        right_layout.addWidget(info_group)
        
        # RX Info
        rx_group = QGroupBox("RX Info")
        rx_layout = QVBoxLayout()
        
        self.rx_dest_label = QLabel("Dest: N/A")
        self.rx_src_label = QLabel("Src: N/A")
        self.rx_len_label = QLabel("Len: N/A")
        self.packet_len_label = QLabel("Last Packet: N/A")
        
        for label in [self.rx_dest_label, self.rx_src_label, self.rx_len_label, self.packet_len_label]:
            label.setStyleSheet("font-family: monospace; padding: 2px;")
            rx_layout.addWidget(label)
        
        rx_group.setLayout(rx_layout)
        right_layout.addWidget(rx_group)
        
        # ST6 State
        state_group = QGroupBox("ST6 State")
        state_layout = QVBoxLayout()
        
        self.ready_label = QLabel("🔴 Ready: False")
        self.busy_label = QLabel("⚪ Busy: False")
        self.done_label = QLabel("⚪ Done: False")
        self.fault_label = QLabel("⚪ Fault: False")
        self.cycle_label = QLabel("⏱️ Cycle Time: N/A")
        
        for label in [self.ready_label, self.busy_label, self.done_label, self.fault_label, self.cycle_label]:
            label.setStyleSheet("font-family: monospace; padding: 3px;")
            state_layout.addWidget(label)
        
        state_group.setLayout(state_layout)
        right_layout.addWidget(state_group)
        
        # Packaging KPIs
        kpi_group = QGroupBox("Packaging KPIs")
        kpi_layout = QVBoxLayout()
        
        self.packages_label = QLabel("📦 Packages Completed: 0")
        self.arm_cycles_label = QLabel("🤖 Arm Cycles: 0")
        self.repairs_label = QLabel("🔧 Total Repairs: 0")
        
        for label in [self.packages_label, self.arm_cycles_label, self.repairs_label]:
            label.setStyleSheet("font-family: monospace; padding: 3px;")
            kpi_layout.addWidget(label)
        
        kpi_group.setLayout(kpi_layout)
        right_layout.addWidget(kpi_group)
        
        # Time + Availability
        time_group = QGroupBox("Time & Availability")
        time_layout = QVBoxLayout()
        
        self.op_time_label = QLabel("🕒 Operational Time: 0.0 s")
        self.downtime_label = QLabel("⏸️ Downtime: 0.0 s")
        self.availability_label = QLabel("📊 Availability: 0.0 %")
        
        for label in [self.op_time_label, self.downtime_label, self.availability_label]:
            label.setStyleSheet("font-family: monospace; padding: 3px;")
            time_layout.addWidget(label)
        
        time_group.setLayout(time_layout)
        right_layout.addWidget(time_group)
        
        # Progress
        progress_group = QGroupBox("Cycle Progress")
        progress_layout = QVBoxLayout()
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setTextVisible(True)
        progress_layout.addWidget(self.progress_bar)
        
        progress_group.setLayout(progress_layout)
        right_layout.addWidget(progress_group)
        
        # Last PLC Decode
        decode_group = QGroupBox("Last PLC Decode")
        decode_layout = QVBoxLayout()
        
        self.decode_label = QLabel("N/A")
        self.decode_label.setWordWrap(True)
        self.decode_label.setMaximumHeight(40)
        decode_layout.addWidget(self.decode_label)
        
        decode_group.setLayout(decode_layout)
        right_layout.addWidget(decode_group)
        
        # Recent Log Events
        log_group = QGroupBox("Recent Log Events")
        log_layout = QVBoxLayout()
        
        self.log_list = QListWidget()
        self.log_list.setMaximumHeight(250)
        log_layout.addWidget(self.log_list)
        
        log_group.setLayout(log_layout)
        right_layout.addWidget(log_group)
        
        right_layout.addStretch()
        right_panel.setWidget(right_widget)
        content_layout.addWidget(right_panel, 1)
        
        main_layout.addLayout(content_layout, 1)
        
        # Bottom controls
        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(10)
        
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
        self.speed_combo.addItems(["0.25x", "0.5x", "1x", "2x", "4x"])
        self.speed_combo.setCurrentIndex(2)
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
        controls_layout.addWidget(self.time_label)
        
        # Reload button
        reload_button = QPushButton("🔄 Reload Log")
        reload_button.clicked.connect(self.discover_log)
        controls_layout.addWidget(reload_button)
        
        main_layout.addLayout(controls_layout)
    
    def apply_stylesheet(self, accent_color="cyan"):
        """Apply QSS stylesheet with specified accent color"""
        accent_colors = {
            "cyan": "#00CCCC",
            "green": "#33CC33",
            "orange": "#FF9933"
        }
        
        accent = accent_colors.get(accent_color.lower(), "#00CCCC")
        
        stylesheet = f"""
        QMainWindow {{
            background-color: #1a1a1a;
        }}
        
        QWidget {{
            background-color: #2a2a2a;
            color: #e0e0e0;
            font-family: 'Segoe UI', Arial, sans-serif;
        }}
        
        QGroupBox {{
            font-weight: bold;
            border: 1px solid #444;
            border-radius: 5px;
            margin-top: 10px;
            padding-top: 10px;
            background-color: #333;
        }}
        
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 5px 0 5px;
            color: {accent};
        }}
        
        QLabel {{
            color: #e0e0e0;
        }}
        
        QPushButton {{
            background-color: #444;
            border: 1px solid #555;
            border-radius: 3px;
            padding: 5px 10px;
            color: #e0e0e0;
        }}
        
        QPushButton:hover {{
            background-color: #555;
            border-color: {accent};
        }}
        
        QPushButton:pressed {{
            background-color: #3a3a3a;
        }}
        
        QCheckBox {{
            color: #e0e0e0;
        }}
        
        QCheckBox::indicator {{
            width: 16px;
            height: 16px;
        }}
        
        QComboBox {{
            background-color: #3a3a3a;
            border: 1px solid #555;
            border-radius: 3px;
            padding: 3px;
            color: #e0e0e0;
        }}
        
        QComboBox:hover {{
            border-color: {accent};
        }}
        
        QComboBox::drop-down {{
            border: none;
        }}
        
        QComboBox QAbstractItemView {{
            background-color: #3a3a3a;
            color: #e0e0e0;
            selection-background-color: {accent};
        }}
        
        QSlider::groove:horizontal {{
            border: 1px solid #444;
            height: 8px;
            background: #3a3a3a;
            margin: 2px 0;
            border-radius: 4px;
        }}
        
        QSlider::handle:horizontal {{
            background: {accent};
            border: 1px solid #666;
            width: 18px;
            margin: -5px 0;
            border-radius: 9px;
        }}
        
        QProgressBar {{
            border: 1px solid #444;
            border-radius: 3px;
            text-align: center;
            background-color: #3a3a3a;
        }}
        
        QProgressBar::chunk {{
            background-color: {accent};
            border-radius: 2px;
        }}
        
        QListWidget {{
            font-family: 'Consolas', 'Monaco', monospace;
            font-size: 9pt;
            background-color: #2a2a2a;
            border: 1px solid #444;
            border-radius: 3px;
        }}
        
        QListWidget::item {{
            padding: 3px;
            border-bottom: 1px solid #333;
        }}
        
        QListWidget::item:selected {{
            background-color: #3a3a3a;
        }}
        
        QListWidget::item:nth-child(even) {{
            background-color: #2d2d2d;
        }}
        
        QListWidget::item:nth-child(odd) {{
            background-color: #2a2a2a;
        }}
        
        QScrollArea {{
            border: none;
            background-color: #2a2a2a;
        }}
        
        QScrollBar:vertical {{
            background: #3a3a3a;
            width: 12px;
            border-radius: 6px;
        }}
        
        QScrollBar::handle:vertical {{
            background: #555;
            border-radius: 6px;
            min-height: 20px;
        }}
        
        QScrollBar::handle:vertical:hover {{
            background: {accent};
        }}
        """
        
        self.setStyleSheet(stylesheet)
    
    def on_accent_changed(self, color_name):
        """Handle accent color change"""
        self.apply_stylesheet(color_name.lower())
        self.gl_widget.set_accent_color(color_name.lower())
        
        # Update progress bar color via stylesheet
        self.progress_bar.setStyleSheet(f"""
            QProgressBar {{
                border: 1px solid #444;
                border-radius: 3px;
                text-align: center;
                background-color: #3a3a3a;
            }}
            QProgressBar::chunk {{
                background-color: {self.get_accent_color(color_name)};
                border-radius: 2px;
            }}
        """)
    
    def get_accent_color(self, color_name):
        """Get hex color for accent name"""
        colors = {
            "cyan": "#00CCCC",
            "green": "#33CC33",
            "orange": "#FF9933"
        }
        return colors.get(color_name.lower(), "#00CCCC")
    
    def add_log_message(self, log_msg: LogMessage):
        """Add a log message to the recent events list"""
        if not log_msg:
            return
        
        # Update UI from log message details
            if log_msg.msg_type in (LogMessageType.PLC_DECODE, LogMessageType.BATCH_CHANGE):
                    self.latest_plc_decode = log_msg.text
            self.decode_label.setText(log_msg.text[:80] + ("..." if len(log_msg.text) > 80 else ""))
            
            # Update batch/recipe from decoded PLC if available
            if 'batch' in log_msg.details:
                self.batch_label.setText(f"Batch ID: {log_msg.details['batch']}")
            if 'recipe' in log_msg.details:
                self.recipe_label.setText(f"Recipe ID: {log_msg.details['recipe']}")
            if 'cmd_start' in log_msg.details:
                self.cmd_start_label.setText(f"cmd_start: {log_msg.details['cmd_start']}")
            if 'cmd_stop' in log_msg.details:
                self.cmd_stop_label.setText(f"cmd_stop: {log_msg.details['cmd_stop']}")
            if 'cmd_reset' in log_msg.details:
                self.cmd_reset_label.setText(f"cmd_reset: {log_msg.details['cmd_reset']}")
        
        elif log_msg.msg_type == LogMessageType.RX_META:
            self.latest_rx_info = log_msg.details
            self.rx_dest_label.setText(f"Dest: {log_msg.details.get('dest', 'N/A')}")
            self.rx_src_label.setText(f"Src: {log_msg.details.get('src', 'N/A')}")
            self.rx_len_label.setText(f"Len: {log_msg.details.get('len', 'N/A')}")
        
        elif log_msg.msg_type == LogMessageType.RX_PACKET:
            self.packet_len_label.setText(f"Last Packet: {log_msg.details.get('packet_len', 'N/A')} bytes")
        
        elif log_msg.msg_type == LogMessageType.DONE_PULSE:
            # Trigger done pulse in OpenGL
            self.gl_widget.trigger_done_pulse()
        
        # Format time
        time_str = f"{log_msg.t_ns/1e9:.3f}s" if log_msg.t_ns > 0 else "N/A"
        
        # Format message with icon based on type
        icons = {
            LogMessageType.INFO: "📄",
            LogMessageType.RX_META: "📡",
            LogMessageType.RX_PACKET: "📨",
            LogMessageType.PLC_DECODE: "🔌",
            LogMessageType.STEPPING: "⚙️",
            LogMessageType.DONE_PULSE: "✅",
            LogMessageType.COUNTER_UPDATE: "📦",
            LogMessageType.REPAIR_UPDATE: "🔧",
            LogMessageType.BATCH_CHANGE: "🔄",
            LogMessageType.ERROR: "❌",
            LogMessageType.BLOCK_START: "🚀"
        }
        
        icon = icons.get(log_msg.msg_type, "📄")
        text = f"{icon} [{time_str}] {log_msg.text}"
        
        # Color coding based on message type
        color_map = {
            LogMessageType.DONE_PULSE: "#00CCCC",
            LogMessageType.COUNTER_UPDATE: "#33CC33",
            LogMessageType.REPAIR_UPDATE: "#FF6666",
            LogMessageType.BATCH_CHANGE: "#FF9933",
            LogMessageType.ERROR: "#FF3333",
            LogMessageType.BLOCK_START: "#9966CC"
        }
        
        color = color_map.get(log_msg.msg_type, "#CCCCCC")
        
        # Add to list
        item = QListWidgetItem(text)
        item.setForeground(QColor(color))
        self.log_list.insertItem(0, item)
        
        # Limit list size
        if self.log_list.count() > MAX_RECENT_EVENTS:
            self.log_list.takeItem(self.log_list.count() - 1)
        
        # Store in recent messages
        self.recent_log_messages.insert(0, log_msg)
        if len(self.recent_log_messages) > MAX_RECENT_EVENTS:
            self.recent_log_messages.pop()
    
    def discover_log(self):
        """Discover and load ST6 log file"""
        # Stop current worker
        if self.tail_worker:
            self.tail_worker.stop()
            self.tail_worker = None
        
        # Find log
        self.log_path = LogDiscoverer.find_st6_log()
        
        if self.log_path:
            base_name = os.path.basename(self.log_path)
            self.log_info_label.setText(f"📁 Log: {base_name}")
            
            # Reset counters
            self.raw_event_count = 0
            self.accepted_event_count = 0
            self.update_event_count_label()
            self.log_list.clear()
            self.recent_log_messages = []
            
            if self.is_live_mode:
                self.switch_to_live()
            else:
                self.switch_to_replay()
        else:
            self.log_info_label.setText("❌ No ST6 log found")
            self.log_path = None
    
    def switch_to_live(self):
        """Switch to live mode"""
        if not self.log_path:
            return
        
        # Stop any existing worker
        if self.tail_worker:
            self.tail_worker.stop()
        
        # Clear replay data
        self.replay_events = []
        self.replay_log_messages = []
        self.replay_times_ns = []
        self.current_time_ns = 0
        
        # Update UI
        self.timeline_slider.setEnabled(False)
        self.speed_combo.setEnabled(False)
        self.play_button.setEnabled(False)
        self.is_playing = False
        self.play_button.setText("▶")
        
        # Add initial log message
        self.add_log_message(LogMessage(
            t_ns=int(time.time() * 1e9),
            msg_type=LogMessageType.INFO,
            text="Starting LIVE mode..."
        ))
        
        # Start tail worker
        self.tail_worker = LogTailWorker(self.log_path)
        self.tail_worker.new_event.connect(self.process_new_event)
        self.tail_worker.new_log_message.connect(self.add_log_message)
        self.tail_worker.activity_detected.connect(self.on_activity_detected)
        self.tail_worker.start()
        
        self.last_activity_time = time.time()
        self.is_idle = False
        self.idle_start_time = 0.0
    
    def switch_to_replay(self):
        """Switch to replay mode"""
        if not self.log_path:
            return
        
        # Stop tail worker
        if self.tail_worker:
            self.tail_worker.stop()
            self.tail_worker = None
        
        # Load all events
        self.load_replay_data()
        
        # Update UI
        self.timeline_slider.setEnabled(True)
        self.speed_combo.setEnabled(True)
        self.play_button.setEnabled(True)
        self.is_playing = True
        self.play_button.setText("⏸")
        
        # Add initial log message
        self.add_log_message(LogMessage(
            t_ns=int(time.time() * 1e9),
            msg_type=LogMessageType.INFO,
            text="Starting REPLAY mode..."
        ))
        
        # Setup timeline
        if self.replay_events:
            self.replay_times_ns = [e.t_ns for e in self.replay_events]
            self.replay_min_time = self.replay_times_ns[0]
            self.replay_max_time = self.replay_times_ns[-1]
            self.current_time_ns = self.replay_min_time
            self.timeline_slider.setValue(0)
            self.update_time_label()
            self.update_states_from_replay()
            
            # Update log messages for replay time
            self.update_log_messages_for_replay()
    
    def load_replay_data(self):
        """Load all events for replay"""
        try:
            with open(self.log_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
            
            events = []
            log_messages = []
            parser = ST6LogParser()
            
            for line in lines:
                event, log_msg = parser.parse_line(line)
                if event:
                    events.append(event)
                if log_msg:
                    log_messages.append(log_msg)
            
            # Sort by time
            events.sort(key=lambda x: x.t_ns)
            log_messages.sort(key=lambda x: x.t_ns)
            
            self.replay_events = events[-MAX_EVENTS:] if len(events) > MAX_EVENTS else events
            self.replay_log_messages = log_messages[-MAX_EVENTS:] if len(log_messages) > MAX_EVENTS else log_messages
            self.raw_event_count = len(self.replay_events)
            self.accepted_event_count = 0
            self.update_event_count_label()
            
            print(f"Loaded {len(self.replay_events)} events for replay")
            
        except Exception as e:
            print(f"Error loading {self.log_path}: {e}")
            self.replay_events = []
            self.replay_log_messages = []
    
    def update_log_messages_for_replay(self):
        """Update log messages list for current replay time"""
        if not self.replay_log_messages:
            return
        
        self.log_list.clear()
        
        # Find messages up to current time
        cutoff_idx = bisect.bisect_right([m.t_ns for m in self.replay_log_messages], self.current_time_ns)
        recent_messages = self.replay_log_messages[max(0, cutoff_idx - 30):cutoff_idx]
        
        for log_msg in reversed(recent_messages):
            self.add_log_message(log_msg)
    
    def process_new_event(self, event: ST6Event):
        """Process new event from tail worker"""
        self.raw_event_count += 1
        
        # Check if state has actually changed
        new_state = {
            'ready': event.ready,
            'busy': event.busy,
            'done': event.done,
            'fault': event.fault,
            'cycle_time_ms': event.cycle_time_ms,
            'packages_completed': event.packages_completed,
            'arm_cycles': event.arm_cycles,
            'total_repairs': event.total_repairs,
            'operational_time_s': event.operational_time_s,
            'downtime_s': event.downtime_s,
            'availability': event.availability,
            'extra': event.extra.copy() if event.extra else {}
        }
        
        # Update activity time
        self.last_activity_time = time.time()
        if self.is_idle:
            self.is_idle = False
            self.idle_start_time = 0.0
        
        # Check if state changed
        if self.last_state_snapshot is None or self._state_changed(self.last_state_snapshot, new_state):
            self.last_state_snapshot = new_state
            self.accepted_event_count += 1
            self.update_event_count_label()
            self.update_display(event)
            
            # Update debug label
            time_str = f"{event.t_ns/1e9:.3f}s" if event.t_ns > 0 else "N/A"
            self.debug_label.setText(f"Mode: LIVE | Time: {time_str} | R={event.ready} B={event.busy} D={event.done} F={event.fault}")
    
    def _state_changed(self, old_state: dict, new_state: dict) -> bool:
        """Check if state has meaningfully changed"""
        # Check basic states
        for key in ['ready', 'busy', 'done', 'fault']:
            if old_state.get(key) != new_state.get(key):
                return True
        
        # Check cycle time with tolerance
        old_cycle = old_state.get('cycle_time_ms')
        new_cycle = new_state.get('cycle_time_ms')
        if old_cycle is not None and new_cycle is not None:
            if abs(old_cycle - new_cycle) > 0.01:
                return True
        elif old_cycle != new_cycle:
            return True
        
        # Check counters
        for key in ['packages_completed', 'arm_cycles', 'total_repairs']:
            if old_state.get(key) != new_state.get(key):
                return True
        
        # Check floats with tolerance
        for key in ['operational_time_s', 'downtime_s', 'availability']:
            old_val = old_state.get(key)
            new_val = new_state.get(key)
            if old_val is not None and new_val is not None:
                if abs(old_val - new_val) > 0.001:
                    return True
            elif old_val != new_val:
                return True
        
        # Check extra fields (only meaningful ones)
        old_extras = old_state.get('extra', {})
        new_extras = new_state.get('extra', {})
        
        for key in ['batch_id', 'recipe_id', 'cmd_start', 'cmd_stop', 'cmd_reset']:
            if old_extras.get(key) != new_extras.get(key):
                return True
        
        return False
    
    def update_display(self, event: ST6Event):
        """Update all displays from event"""
        # Update OpenGL with correct mode
        mode = "LIVE" if self.is_live_mode else "REPLAY"
        self.gl_widget.update_state(event, mode)
        
        # Update state labels with colors
        ready_color = self.get_accent_color("green") if event.ready else "#FF6B6B"
        busy_color = self.get_accent_color("orange") if event.busy else "#B0BEC5"
        done_color = self.get_accent_color(self.accent_combo.currentText().lower()) if event.done else "#B0BEC5"
        fault_color = "#F44336" if event.fault else "#B0BEC5"
        
        self.ready_label.setText(f'<font color="{ready_color}">🔴 Ready: {event.ready}</font>')
        self.busy_label.setText(f'<font color="{busy_color}">⚪ Busy: {event.busy}</font>')
        self.done_label.setText(f'<font color="{done_color}">⚪ Done: {event.done}</font>')
        self.fault_label.setText(f'<font color="{fault_color}">⚪ Fault: {event.fault}</font>')
        
        if event.cycle_time_ms:
            self.cycle_label.setText(f'⏱️ Cycle Time: {event.cycle_time_ms:.1f} ms')
        else:
            self.cycle_label.setText("⏱️ Cycle Time: N/A")
        
        # Update KPI labels
        self.packages_label.setText(f"📦 Packages Completed: {event.packages_completed or 0}")
        self.arm_cycles_label.setText(f"🤖 Arm Cycles: {event.arm_cycles or 0}")
        self.repairs_label.setText(f"🔧 Total Repairs: {event.total_repairs or 0}")
        
        # Update time labels
        self.op_time_label.setText(f"🕒 Operational Time: {event.operational_time_s or 0.0:.1f} s")
        self.downtime_label.setText(f"⏸️ Downtime: {event.downtime_s or 0.0:.1f} s")
        self.availability_label.setText(f"📊 Availability: {event.availability or 0.0:.1f} %")
        
        # Update progress bar
        progress = int(self.gl_widget.cycle_progress * 100)
        self.progress_bar.setValue(progress)
        
        # Update batch/recipe and cmd states from event
        batch_id = event.extra.get('batch_id', 'N/A')
        recipe_id = event.extra.get('recipe_id', 'N/A')
        cmd_start = event.extra.get('cmd_start', 'N/A')
        cmd_stop = event.extra.get('cmd_stop', 'N/A')
        cmd_reset = event.extra.get('cmd_reset', 'N/A')
        
        self.batch_label.setText(f"Batch ID: {batch_id}")
        self.recipe_label.setText(f"Recipe ID: {recipe_id}")
        self.cmd_start_label.setText(f"cmd_start: {cmd_start}")
        self.cmd_stop_label.setText(f"cmd_stop: {cmd_stop}")
        self.cmd_reset_label.setText(f"cmd_reset: {cmd_reset}")
        
        # Update RX info from event
        rx_dest = event.extra.get('rx_dest', 'N/A')
        rx_src = event.extra.get('rx_src', 'N/A')
        rx_len = event.extra.get('rx_len', 'N/A')
        packet_len = event.extra.get('last_packet_len', 'N/A')
        
        self.rx_dest_label.setText(f"Dest: {rx_dest}")
        self.rx_src_label.setText(f"Src: {rx_src}")
        self.rx_len_label.setText(f"Len: {rx_len}")
        self.packet_len_label.setText(f"Last Packet: {packet_len} bytes")
    
    def update_states_from_replay(self):
        """Update states based on current replay time (deterministic)"""
        if not self.replay_events:
            return
        
        # Find event at or before current time
        idx = bisect.bisect_right(self.replay_times_ns, self.current_time_ns) - 1
        if idx >= 0:
            event = self.replay_events[idx]
            self.update_display(event)
            
            # Update log messages for current time
            self.update_log_messages_for_replay()
    
    def update_animation(self):
        """Update animation and replay playback"""
        # Update time label
        self.update_time_label()
        
        if self.is_playing and not self.is_live_mode and self.replay_events:
            # Advance replay time (deterministic VSI time only)
            time_delta_ns = int(33_333_333 * self.playback_speed)  # ~30 FPS
            
            # Use VSI time increment
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
        
        # Check for idle
        if self.is_live_mode and not self.is_idle:
            idle_time = time.time() - self.last_activity_time
            if idle_time > IDLE_TIMEOUT:
                self.on_idle_detected()
        
        # Trigger OpenGL update
        self.gl_widget.update()
    
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
            # Show VSI time in replay
            if self.replay_events:
                time_s = (self.current_time_ns - self.replay_min_time) / 1e9
                total_s = (self.replay_max_time - self.replay_min_time) / 1e9
                self.time_label.setText(f"{time_s:07.3f}s / {total_s:07.3f}s")
            else:
                self.time_label.setText("00:00.000")
    
    def update_event_count_label(self):
        """Update the event count label"""
        self.event_count_label.setText(f"Parsed: {self.raw_event_count} | State Updates: {self.accepted_event_count}")
    
    def on_activity_detected(self):
        """Handle activity detection"""
        self.last_activity_time = time.time()
        if self.is_idle:
            self.is_idle = False
            self.idle_start_time = 0.0
    
    def on_idle_detected(self):
        """Handle idle detection"""
        if not self.is_idle:
            self.is_idle = True
            self.idle_start_time = time.time()
    
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
        speeds = [0.25, 0.5, 1.0, 2.0, 4.0]
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
        if self.tail_worker:
            self.tail_worker.stop()
        event.accept()

# ===== MAIN APPLICATION =====
def main():
    # Set OpenGL format
    fmt = QSurfaceFormat()
    fmt.setVersion(2, 1)
    fmt.setProfile(QSurfaceFormat.CompatibilityProfile)
    fmt.setDepthBufferSize(24)
    fmt.setSamples(4)
    QSurfaceFormat.setDefaultFormat(fmt)
    
    app = QApplication(sys.argv)
    
    # Apply dark theme using QSS (NO QPalette)
    window = MainWindow()
    window.apply_stylesheet("cyan")  # Default accent
    
    if USE_PYOPENGL and not PYOPENGL_AVAILABLE:
        reply = QMessageBox.warning(
            None,
            "OpenGL Warning",
            "PyOpenGL not installed. Visualization will be basic.\n"
            "Continue anyway?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.No:
            sys.exit(1)
    
    window.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()