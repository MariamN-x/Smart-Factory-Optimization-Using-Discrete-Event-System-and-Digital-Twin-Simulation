"""
3D Visualization and Replay System for Kitting Station
Enhanced with advanced features, charts, and better UI
"""
from datetime import datetime
import sys
import json
import numpy as np
import math
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict, deque
import bisect
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QSlider, QLabel, QGroupBox, QFileDialog, QTextEdit,
    QSplitter, QComboBox, QProgressBar, QMessageBox, QTabWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QGridLayout,
    QCheckBox, QListWidget, QListWidgetItem, QSpinBox,
    QDoubleSpinBox, QScrollArea, QFrame
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject, QThread, QSize, QPoint
from PyQt5.QtGui import QFont, QPalette, QColor, QIcon, QPainter, QPen
from PyQt5.QtOpenGL import QGLWidget, QGLFormat

# Matplotlib for charts
import matplotlib
matplotlib.use('Qt5Agg')
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.pyplot as plt

# OpenGL imports
from OpenGL.GL import *
from OpenGL.GLU import *
from OpenGL.GLUT import *
from OpenGL.GLUT import (
    glutInit,
    glutBitmapCharacter,
    glutBitmapWidth,
    GLUT_BITMAP_HELVETICA_10,
    GLUT_BITMAP_HELVETICA_12,
    GLUT_BITMAP_HELVETICA_18
)
from station_info_panel import StationInfoPanel # type: ignore

# ============================================================================
# Data Structures
# ============================================================================

class ArmState(str, Enum):
    IDLE = "IDLE"
    MOVING = "MOVING"
    WORKING = "WORKING"
    FAILED = "FAILED"

class StationState(str, Enum):
    IDLE = "IDLE"
    PROCESSING = "PROCESSING"
    CHECKING_INVENTORY = "CHECKING_INVENTORY"
    ROBOTIC_PICKING = "ROBOTIC_PICKING"
    ROBOTIC_KITTING = "ROBOTIC_KITTING"
    ROBOTIC_MOUNTING = "ROBOTIC_MOUNTING"
    ROBOTIC_SOLDERING = "ROBOTIC_SOLDERING"
    HANDLING_OUTPUT = "HANDLING_OUTPUT"
    RESETTING = "RESETTING"
    ERROR = "ERROR"
    EMERGENCY_STOP = "EMERGENCY_STOP"

@dataclass
class ArmVisualState:
    """Enhanced visual state of a robotic arm"""
    name: str
    role: str
    state: ArmState = ArmState.IDLE
    position: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    target_position: Optional[Tuple[float, float, float]] = None
    color: Tuple[float, float, float] = (0.8, 0.8, 0.8)
    completed_operations: int = 0
    failure_count: int = 0
    current_failure: Optional[dict] = None
    
    # Animation
    animation_progress: float = 0.0
    animation_duration: float = 0.0
    start_position: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    
    # Working animation
    work_pulse: float = 0.0
    work_pulse_direction: int = 1
    
    # Movement history for trail
    position_history: List[Tuple[float, float, float]] = field(default_factory=list)
    max_history: int = 20

@dataclass
class OrderVisual:
    """Enhanced visual representation of an order"""
    order_id: int
    model_type: str
    status: str  # QUEUED, PROCESSING, COMPLETED, FAILED
    position: Tuple[float, float, float]
    color: Tuple[float, float, float]
    current_station: str = "inventory"
    progress: float = 0.0
    creation_time: float = 0.0
    completion_time: Optional[float] = None
    cycle_time: Optional[float] = None
    
    # Animation
    hover_height: float = 0.0
    rotation: float = 0.0

@dataclass
class SystemVisualState:
    """Complete visual state at a point in time"""
    sim_time: float
    station_state: StationState
    arms: Dict[str, ArmVisualState]
    orders: List[OrderVisual]
    inventory_state: str = "READY"
    output_state: str = "READY"
    queued_orders: int = 0
    wip_count: int = 0
    completed_count: int = 0
    total_failures: int = 0
    
    # For KPI tracking
    cycle_times: List[float] = field(default_factory=list)
    throughput_history: List[Tuple[float, int]] = field(default_factory=list)  # (time, completed)
    failure_history: List[Tuple[float, str, str]] = field(default_factory=list)  # (time, arm, operation)

class OrderFlowTracker:
    """Tracks order flow through stations"""
    def __init__(self):
        self.order_stations = {}  # order_id -> current station
        self.station_positions = {}
        self.setup_station_positions()
    
    def setup_station_positions(self):
        """Define positions for each station"""
        self.station_positions = {
            "inventory": (-3.0, 0.2, 2.0),
            "staging": (-1.0, 0.2, 2.0),
            "kitting": (0.0, 0.2, 2.0),
            "mounting": (1.0, 0.2, 2.0),
            "soldering": (2.0, 0.2, 2.0),
            "output": (3.0, 0.2, 2.0)
        }
    
    def get_station_position(self, station_name: str) -> Tuple[float, float, float]:
        """Get 3D position for a station"""
        return self.station_positions.get(station_name, (0.0, 0.2, 0.0))

# ============================================================================
# Log Processor with Enhanced Analysis
# ============================================================================

class LogProcessor(QObject):
    """Enhanced log processor with metrics calculation"""
    timeline_loaded = pyqtSignal(list, dict)  # timeline, metadata
    progress_updated = pyqtSignal(int)
    
    def __init__(self):
        super().__init__()
        self.timeline = []
        self.max_time = 0
        self.events = []
        self.metrics = {}
        self.station_layout = self._create_station_layout()
        self.order_tracker = OrderFlowTracker()
        
        # Order tracking
        self.orders = {}  # order_id -> dict with creation/completion times
        self.order_events = defaultdict(list)  # order_id -> list of events
    
    def _create_station_layout(self) -> Dict[str, Tuple[float, float, float]]:
        """Define 3D positions for all station components"""
        layout = {
            # Arm home positions
            "PickingArm_home": (-3.0, 0.3, -2.0),
            "KittingArm_home": (-1.0, 0.3, -2.0),
            "MountingArm_home": (1.0, 0.3, -2.0),
            "SolderingArm_home": (3.0, 0.3, -2.0),
            
            # Work stations
            "inventory_area": (-3.0, 0.1, 2.0),
            "staging_area": (-1.0, 0.1, 2.0),
            "kitting_tray": (0.0, 0.1, 2.0),
            "mounting_area": (1.0, 0.1, 2.0),
            "soldering_area": (2.0, 0.1, 2.0),
            "output_buffer": (3.0, 0.1, 2.0),
            
            # Conveyor path
            "conveyor_start": (-3.5, 0.15, 0.0),
            "conveyor_end": (3.5, 0.15, 0.0),
            
            # Arm work positions
            "PickingArm_work": (-3.0, 0.3, 2.0),
            "KittingArm_work": (-1.0, 0.3, 2.0),
            "MountingArm_work": (1.0, 0.3, 2.0),
            "SolderingArm_work": (2.0, 0.3, 2.0),
        }
        return layout
    
    def load_log_file(self, filepath: str):
        """Load and parse JSONL log file with enhanced tracking"""
        print(f"Loading log file: {filepath}")
        self.events = []
        self.orders = {}
        self.order_events = defaultdict(list)
        
        try:
            with open(filepath, 'r') as f:
                for line_num, line in enumerate(f):
                    line = line.strip()
                    if line:
                        event = json.loads(line)
                        self.events.append(event)
                        
                        # Track order events
                        if event.get('category') == 'ORDER':
                            order_id = event.get('order_id')
                            if order_id:
                                self.order_events[order_id].append(event)
            
            # Sort by simulation time
            self.events.sort(key=lambda x: float(x.get('sim_time', 0)))
            
            # Build timeline
            self.timeline = self._build_timeline()
            self.max_time = self.timeline[-1].sim_time if self.timeline else 0
            
            # Calculate metrics
            self._calculate_metrics()
            
            print(f"Timeline built: {len(self.timeline)} states, max time: {self.max_time:.1f}s")
            self.timeline_loaded.emit(self.timeline, self.metrics)
            
        except Exception as e:
            print(f"Error loading log file: {e}")
            raise
    
    def _build_timeline(self) -> List[SystemVisualState]:
        """Convert log events to visual timeline"""
        timeline = []
        current_state = self._create_initial_state()
        
        # Process events in order
        for event in self.events:
            sim_time = float(event.get('sim_time', 0))
            category = event.get('category', '')
            event_type = event.get('event_type', '')
            
            # Create new state at this time
            new_state = self._copy_state(current_state)
            new_state.sim_time = sim_time
            
            # Update based on event
            self._apply_event(new_state, event, sim_time)
            
            timeline.append(new_state)
            current_state = new_state
        
        return timeline
    
    def _create_initial_state(self) -> SystemVisualState:
        """Create initial system state"""
        arms = {
            "PickingArm": ArmVisualState(
                name="PickingArm",
                role="PICKING",
                state=ArmState.IDLE,
                position=self.station_layout["PickingArm_home"],
                color=(0.8, 0.8, 0.8)
            ),
            "KittingArm": ArmVisualState(
                name="KittingArm",
                role="KITTING",
                state=ArmState.IDLE,
                position=self.station_layout["KittingArm_home"],
                color=(0.8, 0.8, 0.8)
            ),
            "MountingArm": ArmVisualState(
                name="MountingArm",
                role="MOUNTING",
                state=ArmState.IDLE,
                position=self.station_layout["MountingArm_home"],
                color=(0.8, 0.8, 0.8)
            ),
            "SolderingArm": ArmVisualState(
                name="SolderingArm",
                role="SOLDERING",
                state=ArmState.IDLE,
                position=self.station_layout["SolderingArm_home"],
                color=(0.8, 0.8, 0.8)
            )
        }
        
        return SystemVisualState(
            sim_time=0.0,
            station_state=StationState.IDLE,
            arms=arms,
            orders=[],
            inventory_state="READY",
            output_state="READY",
            queued_orders=0,
            wip_count=0,
            completed_count=0,
            total_failures=0
        )
    
    def _copy_state(self, state: SystemVisualState) -> SystemVisualState:
        """Create a deep copy of state"""
        arms_copy = {}
        for arm_name, arm in state.arms.items():
            arms_copy[arm_name] = ArmVisualState(
                name=arm.name,
                role=arm.role,
                state=arm.state,
                position=arm.position,
                target_position=arm.target_position,
                color=arm.color,
                completed_operations=arm.completed_operations,
                failure_count=arm.failure_count,
                current_failure=arm.current_failure,
                animation_progress=arm.animation_progress,
                animation_duration=arm.animation_duration,
                start_position=arm.start_position,
                work_pulse=arm.work_pulse,
                work_pulse_direction=arm.work_pulse_direction,
                position_history=arm.position_history.copy() if arm.position_history else []
            )
        
        orders_copy = []
        for order in state.orders:
            orders_copy.append(OrderVisual(
                order_id=order.order_id,
                model_type=order.model_type,
                status=order.status,
                position=order.position,
                color=order.color,
                current_station=order.current_station,
                progress=order.progress,
                creation_time=order.creation_time,
                completion_time=order.completion_time,
                cycle_time=order.cycle_time,
                hover_height=order.hover_height,
                rotation=order.rotation
            ))
        
        return SystemVisualState(
            sim_time=state.sim_time,
            station_state=state.station_state,
            arms=arms_copy,
            orders=orders_copy,
            inventory_state=state.inventory_state,
            output_state=state.output_state,
            queued_orders=state.queued_orders,
            wip_count=state.wip_count,
            completed_count=state.completed_count,
            total_failures=state.total_failures,
            cycle_times=state.cycle_times.copy(),
            throughput_history=state.throughput_history.copy(),
            failure_history=state.failure_history.copy()
        )
    
    def _apply_event(self, state: SystemVisualState, event: dict, sim_time: float):
        """Apply a log event to the visual state"""
        category = event.get('category', '')
        event_type = event.get('event_type', '')
        
        if category == "ARM":
            self._process_arm_event(state, event, sim_time)
        elif category == "PLC":
            self._process_plc_event(state, event, sim_time)
        elif category == "INVENTORY":
            self._process_inventory_event(state, event)
        elif category == "OUTPUT":
            self._process_output_event(state, event)
        elif category == "ORDER":
            self._process_order_event(state, event, sim_time)
    
    def _process_arm_event(self, state: SystemVisualState, event: dict, sim_time: float):
        """Process arm-related events"""
        arm_name = event.get('arm_name', '')
        if arm_name not in state.arms:
            return
        
        arm = state.arms[arm_name]
        event_type = event.get('event_type', '')
        
        if event_type == "STATE_CHANGE":
            new_state = event.get('new_state', arm.state)
            arm.state = ArmState(new_state) if new_state in ArmState.__members__ else ArmState.IDLE
            
            # Update position from log data
            if 'x' in event and 'y' in event:
                x = float(event['x']) / 100.0
                y = 0.3
                z = float(event['y']) / 100.0
                
                # Record position history
                arm.position_history.append(arm.position)
                if len(arm.position_history) > arm.max_history:
                    arm.position_history.pop(0)
                
                # If moving, set up animation
                if new_state == "MOVING":
                    arm.start_position = arm.position
                    arm.target_position = (x, y, z)
                    arm.animation_progress = 0.0
                    arm.animation_duration = 1.0  # Fixed animation time for smoothness
                else:
                    arm.position = (x, y, z)
                    arm.target_position = None
            
            # Update color based on state
            if new_state == "WORKING":
                arm.color = (0.0, 1.0, 0.0)  # Green
                arm.work_pulse = 0.0
                arm.work_pulse_direction = 1
            elif new_state == "MOVING":
                arm.color = (1.0, 1.0, 0.0)  # Yellow
            elif new_state == "FAILED":
                arm.color = (1.0, 0.0, 0.0)  # Red
                arm.current_failure = {
                    'operation': event.get('operation', 'unknown'),
                    'failure_id': event.get('failure_id', 0),
                    'time': sim_time
                }
                state.total_failures += 1
                state.failure_history.append((sim_time, arm_name, event.get('operation', 'unknown')))
            elif new_state == "IDLE":
                arm.color = (0.8, 0.8, 0.8)  # Grey
            
            # Update statistics
            arm.completed_operations = event.get('completed_operations', arm.completed_operations)
            arm.failure_count = event.get('failure_count', arm.failure_count)
        
        elif event_type == "FAILURE":
            arm.state = ArmState.FAILED
            arm.color = (1.0, 0.0, 0.0)
            arm.failure_count += 1
            arm.current_failure = {
                'operation': event.get('operation', 'unknown'),
                'failure_id': event.get('failure_id', 0),
                'time': sim_time
            }
            state.total_failures += 1
            state.failure_history.append((sim_time, arm_name, event.get('operation', 'unknown')))
    
    def _process_plc_event(self, state: SystemVisualState, event: dict, sim_time: float):
        """Process PLC events"""
        event_type = event.get('event_type', '')
        
        if event_type == "SNAPSHOT":
            station_state = event.get('station_state', 'IDLE')
            if station_state in StationState.__members__:
                state.station_state = StationState(station_state)
            
            state.queued_orders = event.get('queued_orders', state.queued_orders)
            state.wip_count = event.get('wip_count', state.wip_count)
            state.completed_count = event.get('completed_count', state.completed_count)
            
            # Record throughput
            state.throughput_history.append((sim_time, state.completed_count))
    
    def _process_inventory_event(self, state: SystemVisualState, event: dict):
        """Process inventory events"""
        event_type = event.get('event_type', '')
        if event_type == "STATE_CHANGE":
            state.inventory_state = event.get('state', state.inventory_state)
    
    def _process_output_event(self, state: SystemVisualState, event: dict):
        """Process output events"""
        event_type = event.get('event_type', '')
        if event_type == "STATE_CHANGE":
            state.output_state = event.get('state', state.output_state)
            state.completed_count = event.get('completed_count', state.completed_count)
    
    def _process_order_event(self, state: SystemVisualState, event: dict, sim_time: float):
        """Process order events with enhanced flow tracking"""
        event_type = event.get('event_type', '')
        
        if event_type == "CREATED":
            # Create new order visual
            order_id = event.get('order_id', len(state.orders) + 1)
            model_type = event.get('model_type', 'Unknown')
            
            # Different colors for different model types
            color_map = {
                "Pro X1": (0.2, 0.6, 1.0),    # Blue
                "Standard S2": (0.0, 0.8, 0.4), # Green
                "Mini M1": (1.0, 0.5, 0.0)     # Orange
            }
            
            order = OrderVisual(
                order_id=order_id,
                model_type=model_type,
                status="QUEUED",
                position=self.order_tracker.get_station_position("inventory"),
                color=color_map.get(model_type, (0.7, 0.7, 0.7)),
                creation_time=sim_time
            )
            state.orders.append(order)
            state.queued_orders += 1
            
            # Track in orders dict
            self.orders[order_id] = {
                'creation_time': sim_time,
                'completion_time': None,
                'model_type': model_type
            }
        
        elif event_type == "ORDER_START":
            # Move oldest queued order to processing
            for order in state.orders:
                if order.status == "QUEUED":
                    order.status = "PROCESSING"
                    order.current_station = "staging"
                    order.position = self.order_tracker.get_station_position("staging")
                    state.queued_orders -= 1
                    state.wip_count += 1
                    break
        
        elif event_type == "ORDER_COMPLETE":
            # Mark order as completed
            order_id = event.get('order_id')
            for order in state.orders:
                if order.order_id == order_id and order.status == "PROCESSING":
                    order.status = "COMPLETED"
                    order.current_station = "output"
                    order.position = self.order_tracker.get_station_position("output")
                    order.completion_time = sim_time
                    order.cycle_time = sim_time - order.creation_time
                    
                    state.wip_count -= 1
                    state.completed_count += 1
                    
                    # Record cycle time
                    if order.cycle_time is not None:
                        state.cycle_times.append(order.cycle_time)
                    
                    # Update orders dict
                    if order_id in self.orders:
                        self.orders[order_id]['completion_time'] = sim_time
                        self.orders[order_id]['cycle_time'] = order.cycle_time
                    break
    
    def _calculate_metrics(self):
        """Calculate comprehensive metrics from events"""
        self.metrics = {
            'total_orders': 0,
            'completed_orders': 0,
            'avg_cycle_time': 0.0,
            'min_cycle_time': 0.0,
            'max_cycle_time': 0.0,
            'total_failures': 0,
            'failures_by_arm': {},
            'throughput_over_time': [],
            'utilization_by_arm': {},
            'max_queue_length': 0,
            'station_state_changes': []
        }
        
        # Process events for metrics
        current_queued = 0
        arm_states = defaultdict(list)  # arm_name -> list of (start_time, state)
        
        for event in self.events:
            # Track max queue length
            if event.get('category') == 'PLC' and event.get('event_type') == 'SNAPSHOT':
                current_queued = event.get('queued_orders', 0)
                self.metrics['max_queue_length'] = max(self.metrics['max_queue_length'], current_queued)
            
            # Track arm states for utilization
            if event.get('category') == 'ARM' and event.get('event_type') == 'STATE_CHANGE':
                arm_name = event.get('arm_name')
                state = event.get('new_state')
                time = float(event.get('sim_time', 0))
                
                if arm_name and state:
                    arm_states[arm_name].append((time, state))
            
            # Track failures
            if event.get('category') == 'ARM' and event.get('event_type') == 'FAILURE':
                arm_name = event.get('arm_name')
                if arm_name:
                    self.metrics['failures_by_arm'][arm_name] = self.metrics['failures_by_arm'].get(arm_name, 0) + 1
                    self.metrics['total_failures'] += 1
        
        # Calculate arm utilization (approximate)
        for arm_name, states in arm_states.items():
            if len(states) >= 2:
                busy_time = 0
                total_time = self.max_time
                
                for i in range(1, len(states)):
                    prev_time, prev_state = states[i-1]
                    curr_time, curr_state = states[i]
                    
                    if prev_state in ['MOVING', 'WORKING']:
                        busy_time += (curr_time - prev_time)
                
                utilization = (busy_time / total_time * 100) if total_time > 0 else 0
                self.metrics['utilization_by_arm'][arm_name] = round(utilization, 1)
        
        # Calculate cycle times from orders
        cycle_times = []
        for order_id, order_data in self.orders.items():
            if order_data.get('completion_time') and order_data.get('creation_time'):
                cycle_time = order_data['completion_time'] - order_data['creation_time']
                cycle_times.append(cycle_time)
        
        if cycle_times:
            self.metrics['avg_cycle_time'] = sum(cycle_times) / len(cycle_times)
            self.metrics['min_cycle_time'] = min(cycle_times)
            self.metrics['max_cycle_time'] = max(cycle_times)
            self.metrics['completed_orders'] = len(cycle_times)
        
        self.metrics['total_orders'] = len(self.orders)

# ============================================================================
# Enhanced 3D Visualization Widget
# ============================================================================

class KittingStation3DWidget(QGLWidget):
    """Enhanced OpenGL widget for 3D visualization"""
    
    def __init__(self, parent=None):
        format = QGLFormat()
        format.setSampleBuffers(True)
        super().__init__(format, parent)
        
        # Camera settings
        self.camera_distance = 12.0
        self.camera_angle_x = 30.0
        self.camera_angle_y = -45.0
        self.camera_target = [0.0, 0.0, 0.0]
        
        # Current state
        self.current_state: Optional[SystemVisualState] = None
        self.animation_timer = QTimer()
        self.animation_timer.timeout.connect(self.update_animation)
        self.animation_timer.start(16)  # ~60 FPS
        
        # Visualization settings
        self.show_axes = True
        self.show_grid = True
        self.show_orders = True
        self.show_arm_trails = False
        
        # Camera focus
        self.focus_arm = None
        self.focus_smoothness = 0.1
        
        # Time tracking
        self.time_multiplier = 1.0
        
        # Initialize
        self.setMinimumSize(800, 600)
        self.setMouseTracking(True)
        
        # Working animation timer
        self.work_animation_timer = QTimer()
        self.work_animation_timer.timeout.connect(self.update_work_animation)
        self.work_animation_timer.start(50)  # 20 FPS for work animations
    
    def initializeGL(self):
        """Initialize OpenGL settings"""
        glClearColor(0.08, 0.08, 0.1, 1.0)  # Dark blue-grey background
        glEnable(GL_DEPTH_TEST)
        glEnable(GL_LIGHTING)
        glEnable(GL_LIGHT0)
        glEnable(GL_COLOR_MATERIAL)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        
        # Enhanced lighting
        glLightfv(GL_LIGHT0, GL_POSITION, [5.0, 15.0, 5.0, 1.0])
        glLightfv(GL_LIGHT0, GL_AMBIENT, [0.3, 0.3, 0.3, 1.0])
        glLightfv(GL_LIGHT0, GL_DIFFUSE, [0.9, 0.9, 0.9, 1.0])
        glLightfv(GL_LIGHT0, GL_SPECULAR, [1.0, 1.0, 1.0, 1.0])
        glLightf(GL_LIGHT0, GL_CONSTANT_ATTENUATION, 1.0)
        glLightf(GL_LIGHT0, GL_LINEAR_ATTENUATION, 0.05)
        glLightf(GL_LIGHT0, GL_QUADRATIC_ATTENUATION, 0.001)
        
        glColorMaterial(GL_FRONT_AND_BACK, GL_AMBIENT_AND_DIFFUSE)
        glShadeModel(GL_SMOOTH)
    
    def resizeGL(self, w, h):
        """Handle window resize"""
        glViewport(0, 0, w, h)
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        gluPerspective(45.0, w / h, 0.1, 100.0)
        glMatrixMode(GL_MODELVIEW)
    
    def paintGL(self):
        """Render the enhanced 3D scene"""
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        glLoadIdentity()
        
        # Update camera target based on focus
        target = self.camera_target.copy()
        if self.focus_arm and self.current_state:
            arm = self.current_state.arms.get(self.focus_arm)
            if arm:
                # Smoothly move camera target to arm position
                target[0] = target[0] * (1 - self.focus_smoothness) + arm.position[0] * self.focus_smoothness
                target[1] = target[1] * (1 - self.focus_smoothness) + arm.position[1] * self.focus_smoothness
                target[2] = target[2] * (1 - self.focus_smoothness) + arm.position[2] * self.focus_smoothness
        
        # Set camera position
        camera_x = target[0] + self.camera_distance * np.sin(np.radians(self.camera_angle_y)) * np.cos(np.radians(self.camera_angle_x))
        camera_y = target[1] + self.camera_distance * np.sin(np.radians(self.camera_angle_x))
        camera_z = target[2] + self.camera_distance * np.cos(np.radians(self.camera_angle_y)) * np.cos(np.radians(self.camera_angle_x))
        
        gluLookAt(camera_x, camera_y, camera_z,
                  target[0], target[1], target[2],
                  0.0, 1.0, 0.0)
        
        # Draw floor and axes
        if self.show_grid:
            self._draw_enhanced_floor()
        
        if self.show_axes:
            self._draw_enhanced_axes()
        
        # Draw station layout
        self._draw_enhanced_station_layout()
        
        # Draw current state if available
        if self.current_state:
            self._draw_current_state()
            
            # Draw station status banner
            self._draw_station_status_banner()
    
    def _draw_enhanced_floor(self):
        """Draw enhanced factory floor with grid"""
        # Main floor
        glColor3f(0.25, 0.25, 0.3)
        glBegin(GL_QUADS)
        glVertex3f(-6.0, 0.0, -6.0)
        glVertex3f(-6.0, 0.0, 6.0)
        glVertex3f(6.0, 0.0, 6.0)
        glVertex3f(6.0, 0.0, -6.0)
        glEnd()
        
        # Grid lines
        glColor3f(0.35, 0.35, 0.4)
        glLineWidth(1.0)
        glBegin(GL_LINES)
        for i in range(-6, 7):
            glVertex3f(i, 0.01, -6.0)
            glVertex3f(i, 0.01, 6.0)
            glVertex3f(-6.0, 0.01, i)
            glVertex3f(6.0, 0.01, i)
        glEnd()
        
        # Highlight work area
        glColor4f(0.3, 0.3, 0.5, 0.3)
        glBegin(GL_QUADS)
        glVertex3f(-4.0, 0.02, 1.5)
        glVertex3f(-4.0, 0.02, 2.5)
        glVertex3f(4.0, 0.02, 2.5)
        glVertex3f(4.0, 0.02, 1.5)
        glEnd()
    
    def _draw_enhanced_axes(self):
        """Draw enhanced XYZ axes"""
        glLineWidth(2.0)
        glBegin(GL_LINES)
        # X axis (red)
        glColor3f(1.0, 0.0, 0.0)
        glVertex3f(0.0, 0.0, 0.0)
        glVertex3f(2.0, 0.0, 0.0)
        # Y axis (green)
        glColor3f(0.0, 1.0, 0.0)
        glVertex3f(0.0, 0.0, 0.0)
        glVertex3f(0.0, 2.0, 0.0)
        # Z axis (blue)
        glColor3f(0.0, 0.0, 1.0)
        glVertex3f(0.0, 0.0, 0.0)
        glVertex3f(0.0, 0.0, 2.0)
        glEnd()
        glLineWidth(1.0)
    
    def _draw_enhanced_station_layout(self):
        """Draw enhanced station elements"""
        # Work tables with labels
        stations = [
            (-3.0, 0.05, 2.0, "INVENTORY", (0.4, 0.6, 0.8)),
            (-1.0, 0.05, 2.0, "STAGING", (0.6, 0.8, 0.4)),
            (0.0, 0.05, 2.0, "KITTING", (0.8, 0.8, 0.4)),
            (1.0, 0.05, 2.0, "MOUNTING", (0.8, 0.6, 0.4)),
            (2.0, 0.05, 2.0, "SOLDERING", (0.8, 0.4, 0.6)),
            (3.0, 0.05, 2.0, "OUTPUT", (0.6, 0.4, 0.8)),
        ]
        
        for x, y, z, label, color in stations:
            # Draw table
            glPushMatrix()
            glTranslatef(x, y, z)
            glColor3f(*color)
            self._draw_table(0.8, 0.1, 0.8)
            
            # Draw label
            glColor3f(1.0, 1.0, 1.0)
            glRasterPos3f(-0.3, 0.3, 0.0)
            for char in label:
                glutBitmapCharacter(GLUT_BITMAP_HELVETICA_12, ord(char))
            glPopMatrix()
        
        # Draw conveyor path
        self._draw_conveyor_path()
    
    def _draw_table(self, width, height, depth):
        """Draw a work table"""
        w, h, d = width/2, height/2, depth/2
        
        # Table top
        glPushMatrix()
        glTranslatef(0, h, 0)
        glBegin(GL_QUADS)
        glNormal3f(0, 1, 0)
        glVertex3f(-w, 0, -d)
        glVertex3f(-w, 0, d)
        glVertex3f(w, 0, d)
        glVertex3f(w, 0, -d)
        glEnd()
        
        # Table legs
        leg_positions = [(-w+0.1, -h, -d+0.1), (w-0.1, -h, -d+0.1),
                        (-w+0.1, -h, d-0.1), (w-0.1, -h, d-0.1)]
        
        for leg_x, leg_y, leg_z in leg_positions:
            glPushMatrix()
            glTranslatef(leg_x, leg_y, leg_z)
            glScalef(0.1, 1.0, 0.1)
            self._draw_cube(1.0, 1.0, 1.0)
            glPopMatrix()
        glPopMatrix()
    
    def _draw_conveyor_path(self):
        """Draw conveyor belt path"""
        glColor3f(0.4, 0.4, 0.4)
        glLineWidth(3.0)
        glBegin(GL_LINE_STRIP)
        glVertex3f(-3.5, 0.15, 0.0)
        glVertex3f(3.5, 0.15, 0.0)
        glEnd()
        glLineWidth(1.0)
        
        # Draw conveyor rollers
        for i in range(-7, 8):
            x = i * 0.5
            glPushMatrix()
            glTranslatef(x, 0.15, 0.0)
            glRotatef(90, 0, 0, 1)
            glColor3f(0.5, 0.5, 0.5)
            self._draw_cylinder(0.05, 0.2, 20)
            glPopMatrix()
    
    def _draw_current_state(self):
        """Draw the current system state with enhanced visuals"""
        # Draw order flow first (so arms appear on top)
        if self.show_orders:
            for order in self.current_state.orders:
                self._draw_enhanced_order(order)
        
        # Draw robotic arms
        for arm_name, arm in self.current_state.arms.items():
            self._draw_enhanced_robotic_arm(arm)
            
            # Draw arm trail if enabled
            if self.show_arm_trails and len(arm.position_history) > 1:
                self._draw_arm_trail(arm)
    
    def _draw_enhanced_robotic_arm(self, arm: ArmVisualState):
        """Draw an enhanced robotic arm with animations"""
        x, y, z = arm.position
        
        glPushMatrix()
        glTranslatef(x, y, z)
        
        # Arm base (with pulsing for working state)
        if arm.state == ArmState.WORKING:
            pulse = 0.1 * math.sin(arm.work_pulse * math.pi)
            glColor3f(arm.color[0] + pulse, arm.color[1] + pulse, arm.color[2] + pulse)
        elif arm.state == ArmState.FAILED:
            # Pulsing red for failures
            pulse = 0.2 * math.sin(arm.work_pulse * 2 * math.pi)
            glColor3f(1.0, pulse, pulse)
        else:
            glColor3f(*arm.color)
        
        # Draw base
        quadric = gluNewQuadric()
        gluCylinder(quadric, 0.25, 0.25, 0.6, 20, 20)
        
        # Draw arm segments
        glTranslatef(0.0, 0.6, 0.0)
        self._draw_cube(0.35, 0.35, 0.35)
        
        # Draw second segment (elbow)
        glTranslatef(0.0, 0.35, 0.0)
        glRotatef(45, 0, 0, 1)  # Simulate arm bending
        self._draw_cube(0.25, 0.4, 0.25)
        
        # Draw end effector
        glTranslatef(0.0, 0.4, 0.0)
        if arm.state == ArmState.WORKING:
            # Animated gripper for working state
            grip_angle = 30 * math.sin(arm.work_pulse * 2 * math.pi)
            glPushMatrix()
            glRotatef(grip_angle, 0, 0, 1)
            self._draw_gripper()
            glPopMatrix()
        else:
            self._draw_gripper()
        
        glPopMatrix()
        
        # Draw arm label with status
        self._draw_arm_label(arm)
        
        # Draw failure indicator if failed
        if arm.state == ArmState.FAILED and arm.current_failure:
            self._draw_failure_indicator(arm)
    
    def _draw_gripper(self):
        """Draw robot gripper"""
        glColor3f(0.3, 0.3, 0.3)
        
        # Gripper base
        self._draw_cube(0.15, 0.15, 0.15)
        
        # Gripper fingers
        glPushMatrix()
        glTranslatef(0.1, 0.0, 0.0)
        glScalef(0.5, 1.0, 0.3)
        self._draw_cube(1.0, 1.0, 1.0)
        glPopMatrix()
        
        glPushMatrix()
        glTranslatef(-0.1, 0.0, 0.0)
        glScalef(0.5, 1.0, 0.3)
        self._draw_cube(1.0, 1.0, 1.0)
        glPopMatrix()
    
    def _draw_arm_label(self, arm: ArmVisualState):
        """Draw arm label with status"""
        x, y, z = arm.position
        
        glColor3f(1.0, 1.0, 1.0)
        glRasterPos3f(x - 0.5, y + 1.2, z)
        
        status_symbol = {
            ArmState.IDLE: "◼",
            ArmState.MOVING: "↗",
            ArmState.WORKING: "⚙",
            ArmState.FAILED: "⚡"
        }.get(arm.state, "?")
        
        label = f"{arm.name}: {status_symbol} {arm.state.value}"
        for char in label:
            glutBitmapCharacter(GLUT_BITMAP_HELVETICA_12, ord(char))
        
        # Draw operations count
        glRasterPos3f(x - 0.5, y + 1.0, z)
        ops_text = f"Ops: {arm.completed_operations}"
        for char in ops_text:
            glutBitmapCharacter(GLUT_BITMAP_HELVETICA_10, ord(char))
    
    def _draw_failure_indicator(self, arm: ArmVisualState):
        """Draw visual indicator for arm failure"""
        x, y, z = arm.position
        
        # Draw warning symbol
        glPushMatrix()
        glTranslatef(x, y + 1.8, z)
        glColor3f(1.0, 0.0, 0.0)
        
        # Draw warning triangle
        glBegin(GL_TRIANGLES)
        glVertex3f(0.0, 0.3, 0.0)
        glVertex3f(-0.2, -0.1, 0.0)
        glVertex3f(0.2, -0.1, 0.0)
        glEnd()
        
        # Draw exclamation mark
        glColor3f(1.0, 1.0, 1.0)
        glLineWidth(3.0)
        glBegin(GL_LINES)
        glVertex3f(0.0, 0.1, 0.0)
        glVertex3f(0.0, -0.05, 0.0)
        glVertex3f(0.0, -0.1, 0.0)
        glVertex3f(0.0, -0.15, 0.0)
        glEnd()
        glLineWidth(1.0)
        
        glPopMatrix()
        
        # Draw failure text
        if arm.current_failure:
            glColor3f(1.0, 0.5, 0.5)
            glRasterPos3f(x - 0.8, y + 2.2, z)
            fail_text = f"FAIL #{arm.current_failure.get('failure_id', 0)}"
            for char in fail_text:
                glutBitmapCharacter(GLUT_BITMAP_HELVETICA_12, ord(char))
    
    def _draw_arm_trail(self, arm: ArmVisualState):
        """Draw movement trail for arm"""
        if len(arm.position_history) < 2:
            return
        
        glColor4f(0.5, 0.5, 1.0, 0.3)
        glLineWidth(2.0)
        glBegin(GL_LINE_STRIP)
        
        for i, pos in enumerate(arm.position_history):
            alpha = i / len(arm.position_history)
            glColor4f(0.5, 0.5, 1.0, alpha * 0.3)
            glVertex3f(*pos)
        
        glEnd()
        glLineWidth(1.0)
    
    def _draw_enhanced_order(self, order: OrderVisual):
        """Draw enhanced order visualization"""
        x, y, z = order.position
        
        # Add hover animation
        hover = math.sin(order.hover_height) * 0.1
        
        glPushMatrix()
        glTranslatef(x, y + hover, z)
        glRotatef(order.rotation, 0, 1, 0)
        
        # Different appearance based on status
        if order.status == "COMPLETED":
            glColor3f(*order.color)
            # Add completion glow
            glColor3f(order.color[0] * 1.5, order.color[1] * 1.5, order.color[2] * 1.5)
        elif order.status == "FAILED":
            glColor3f(1.0, 0.3, 0.3)  # Red for failed
        else:
            glColor3f(*order.color)
        
        # Different size based on model type
        size_map = {
            "Pro X1": 0.35,
            "Standard S2": 0.3,
            "Mini M1": 0.25
        }
        size = size_map.get(order.model_type, 0.3)
        
        self._draw_cube(size, size, size)
        
        # Draw order ID
        glColor3f(1.0, 1.0, 1.0)
        glRasterPos3f(-0.05, size + 0.15, 0.0)
        id_text = f"#{order.order_id}"
        for char in id_text:
            glutBitmapCharacter(GLUT_BITMAP_HELVETICA_10, ord(char))
        
        glPopMatrix()
    
    def _draw_station_status_banner(self):
        """Draw enhanced station status banner"""
        if not self.current_state:
            return
        
        state = self.current_state.station_state
        
        # Define colors and descriptions for each state
        state_info = {
            StationState.IDLE: ("#4CAF50", "🏭 Station Idle - Waiting for orders"),
            StationState.PROCESSING: ("#FFC107", "⚙ Processing Order"),
            StationState.CHECKING_INVENTORY: ("#2196F3", "📦 Checking Inventory"),
            StationState.ROBOTIC_PICKING: ("#FF9800", "🤖 Robotic Picking"),
            StationState.ROBOTIC_KITTING: ("#FF9800", "🤖 Robotic Kitting"),
            StationState.ROBOTIC_MOUNTING: ("#FF9800", "🤖 Robotic Mounting"),
            StationState.ROBOTIC_SOLDERING: ("#FF9800", "🤖 Robotic Soldering"),
            StationState.HANDLING_OUTPUT: ("#9C27B0", "📤 Handling Output"),
            StationState.RESETTING: ("#00BCD4", "🔄 Resetting Station"),
            StationState.ERROR: ("#F44336", "❌ Station Error"),
            StationState.EMERGENCY_STOP: ("#D32F2F", "🛑 EMERGENCY STOP")
        }
        
        color_hex, description = state_info.get(state, ("#607D8B", "Unknown State"))
        
        # Convert hex to RGB
        color = QColor(color_hex)
        r, g, b = color.red()/255.0, color.green()/255.0, color.blue()/255.0
        
        # Draw status bar
        glColor3f(r, g, b)
        glBegin(GL_QUADS)
        glVertex3f(-5.0, 4.8, -4.0)
        glVertex3f(-5.0, 4.8, 4.0)
        glVertex3f(5.0, 4.8, 4.0)
        glVertex3f(5.0, 4.8, -4.0)
        glEnd()
        
        # Draw status text
        glColor3f(1.0, 1.0, 1.0)
        glRasterPos3f(-4.5, 5.0, 0.0)
        status_text = f"{description}"
        for char in status_text:
            glutBitmapCharacter(GLUT_BITMAP_HELVETICA_18, ord(char))
        
        # Draw simulation time
        time_text = f"Time: {self.current_state.sim_time:.1f}s"
        glRasterPos3f(3.0, 5.0, 0.0)
        for char in time_text:
            glutBitmapCharacter(GLUT_BITMAP_HELVETICA_12, ord(char))
    
    def _draw_cylinder(self, radius, height, slices):
        """Draw a cylinder"""
        quadric = gluNewQuadric()
        gluCylinder(quadric, radius, radius, height, slices, 1)
        gluDeleteQuadric(quadric)
    
    def _draw_cube(self, width: float, height: float, depth: float):
        """Draw a cube with given dimensions"""
        w, h, d = width/2, height/2, depth/2
        
        glBegin(GL_QUADS)
        
        # Front
        glNormal3f(0, 0, 1)
        glVertex3f(-w, -h, d)
        glVertex3f(w, -h, d)
        glVertex3f(w, h, d)
        glVertex3f(-w, h, d)
        
        # Back
        glNormal3f(0, 0, -1)
        glVertex3f(-w, -h, -d)
        glVertex3f(-w, h, -d)
        glVertex3f(w, h, -d)
        glVertex3f(w, -h, -d)
        
        # Top
        glNormal3f(0, 1, 0)
        glVertex3f(-w, h, -d)
        glVertex3f(-w, h, d)
        glVertex3f(w, h, d)
        glVertex3f(w, h, -d)
        
        # Bottom
        glNormal3f(0, -1, 0)
        glVertex3f(-w, -h, -d)
        glVertex3f(w, -h, -d)
        glVertex3f(w, -h, d)
        glVertex3f(-w, -h, d)
        
        # Right
        glNormal3f(1, 0, 0)
        glVertex3f(w, -h, -d)
        glVertex3f(w, h, -d)
        glVertex3f(w, h, d)
        glVertex3f(w, -h, d)
        
        # Left
        glNormal3f(-1, 0, 0)
        glVertex3f(-w, -h, -d)
        glVertex3f(-w, -h, d)
        glVertex3f(-w, h, d)
        glVertex3f(-w, h, -d)
        
        glEnd()
    
    def wheelEvent(self, event):
        """Handle mouse wheel for zoom"""
        delta = event.angleDelta().y() / 120.0
        self.camera_distance = max(3.0, min(30.0, self.camera_distance - delta))
        self.update()
    
    def mousePressEvent(self, event):
        """Handle mouse press for camera rotation"""
        self.last_mouse_pos = event.pos()
    
    def mouseMoveEvent(self, event):
        """Handle mouse drag for camera rotation"""
        if hasattr(self, 'last_mouse_pos'):
            dx = event.x() - self.last_mouse_pos.x()
            dy = event.y() - self.last_mouse_pos.y()
            
            self.camera_angle_y += dx * 0.5
            self.camera_angle_x = max(-85.0, min(85.0, self.camera_angle_x + dy * 0.5))
            
            self.last_mouse_pos = event.pos()
            self.update()
    
    def set_current_state(self, state: SystemVisualState):
        """Update the current visualization state"""
        self.current_state = state
        self.update()
    
    def update_animation(self):
        """Update animation progress"""
        if not self.current_state:
            return
        
        # Update arm animations
        for arm in self.current_state.arms.values():
            if arm.target_position and arm.animation_duration > 0:
                arm.animation_progress += 0.016 / arm.animation_duration * self.time_multiplier
                if arm.animation_progress >= 1.0:
                    arm.position = arm.target_position
                    arm.target_position = None
                    arm.animation_progress = 0.0
                else:
                    # Smooth interpolation
                    t = arm.animation_progress
                    smooth_t = t * t * (3 - 2 * t)  # Smoothstep
                    
                    arm.position = (
                        arm.start_position[0] + (arm.target_position[0] - arm.start_position[0]) * smooth_t,
                        arm.start_position[1] + (arm.target_position[1] - arm.start_position[1]) * smooth_t,
                        arm.start_position[2] + (arm.target_position[2] - arm.start_position[2]) * smooth_t
                    )
    
    def update_work_animation(self):
        """Update work animations for arms and orders"""
        if not self.current_state:
            return
        
        # Update arm work animations
        for arm in self.current_state.arms.values():
            if arm.state == ArmState.WORKING or arm.state == ArmState.FAILED:
                arm.work_pulse += 0.1 * self.time_multiplier
                if arm.work_pulse >= 2.0:
                    arm.work_pulse = 0.0
        
        # Update order animations
        for order in self.current_state.orders:
            order.hover_height += 0.05 * self.time_multiplier
            order.rotation += 1.0 * self.time_multiplier
        
        self.update()
    
    def reset_camera(self):
        """Reset camera to default position"""
        self.camera_distance = 12.0
        self.camera_angle_x = 30.0
        self.camera_angle_y = -45.0
        self.camera_target = [0.0, 0.0, 0.0]
        self.focus_arm = None
        self.update()
    
    def focus_on_arm(self, arm_name: str):
        """Focus camera on specific arm"""
        self.focus_arm = arm_name
        self.update()
    
    def toggle_axes(self, show: bool):
        """Toggle axes visibility"""
        self.show_axes = show
        self.update()
    
    def toggle_grid(self, show: bool):
        """Toggle grid visibility"""
        self.show_grid = show
        self.update()
    
    def toggle_orders(self, show: bool):
        """Toggle orders visibility"""
        self.show_orders = show
        self.update()
    
    def toggle_arm_trails(self, show: bool):
        """Toggle arm trails visibility"""
        self.show_arm_trails = show
        self.update()

# ============================================================================
# Matplotlib Charts for Metrics
# ============================================================================

class CycleTimeChart(FigureCanvas):
    """Cycle time distribution chart"""
    def __init__(self, parent=None):
        self.fig = Figure(figsize=(8, 6), dpi=80)
        super().__init__(self.fig)
        self.setParent(parent)
        self.ax = self.fig.add_subplot(111)
        self.setup_style()
    
    def setup_style(self):
        """Setup chart style"""
        self.ax.set_facecolor('#2b2b2b')
        self.fig.patch.set_facecolor('#2b2b2b')
        self.ax.tick_params(colors='white')
        self.ax.xaxis.label.set_color('white')
        self.ax.yaxis.label.set_color('white')
        self.ax.title.set_color('white')
    
    def plot_cycle_times(self, cycle_times: List[float]):
        """Plot cycle time histogram"""
        self.ax.clear()
        
        if not cycle_times:
            self.ax.text(0.5, 0.5, 'No cycle time data available', 
                        ha='center', va='center', color='white', fontsize=12)
            self.ax.set_xlim(0, 1)
            self.ax.set_ylim(0, 1)
            self.draw()
            return
        
        # Create histogram
        n_bins = min(20, len(cycle_times))
        colors = plt.cm.viridis(np.linspace(0.3, 0.9, n_bins))
        
        n, bins, patches = self.ax.hist(cycle_times, bins=n_bins, edgecolor='black', alpha=0.7)
        
        # Color bars
        for patch, color in zip(patches, colors):
            patch.set_facecolor(color)
        
        # Add statistics
        avg_time = sum(cycle_times) / len(cycle_times)
        min_time = min(cycle_times)
        max_time = max(cycle_times)
        
        self.ax.axvline(avg_time, color='red', linestyle='--', linewidth=2, label=f'Average: {avg_time:.1f}s')
        self.ax.axvline(min_time, color='green', linestyle=':', linewidth=2, label=f'Min: {min_time:.1f}s')
        self.ax.axvline(max_time, color='orange', linestyle=':', linewidth=2, label=f'Max: {max_time:.1f}s')
        
        self.ax.set_xlabel('Cycle Time (seconds)', fontsize=12)
        self.ax.set_ylabel('Frequency', fontsize=12)
        self.ax.set_title(f'Cycle Time Distribution (n={len(cycle_times)})', fontsize=14, fontweight='bold')
        self.ax.legend(facecolor='#3b3b3b', edgecolor='white', labelcolor='white')
        self.ax.grid(True, alpha=0.3)
        
        self.fig.tight_layout()
        self.draw()

class ThroughputChart(FigureCanvas):
    """Throughput over time chart"""
    def __init__(self, parent=None):
        self.fig = Figure(figsize=(8, 6), dpi=80)
        super().__init__(self.fig)
        self.setParent(parent)
        self.ax = self.fig.add_subplot(111)
        self.setup_style()
    
    def setup_style(self):
        """Setup chart style"""
        self.ax.set_facecolor('#2b2b2b')
        self.fig.patch.set_facecolor('#2b2b2b')
        self.ax.tick_params(colors='white')
        self.ax.xaxis.label.set_color('white')
        self.ax.yaxis.label.set_color('white')
        self.ax.title.set_color('white')
    
    def plot_throughput(self, throughput_history: List[Tuple[float, int]]):
        """Plot throughput over time"""
        self.ax.clear()
        
        if not throughput_history:
            self.ax.text(0.5, 0.5, 'No throughput data available', 
                        ha='center', va='center', color='white', fontsize=12)
            self.ax.set_xlim(0, 1)
            self.ax.set_ylim(0, 1)
            self.draw()
            return
        
        times, counts = zip(*throughput_history)
        
        # Create step plot
        self.ax.step(times, counts, where='post', linewidth=2, color='#4CAF50')
        
        # Fill under the curve
        self.ax.fill_between(times, 0, counts, alpha=0.3, color='#4CAF50')
        
        # Calculate throughput rate (orders per minute)
        if len(times) > 1:
            total_time = times[-1] - times[0]
            if total_time > 0:
                throughput_rate = (counts[-1] - counts[0]) / total_time * 60  # orders per minute
                self.ax.text(0.02, 0.98, f'Throughput: {throughput_rate:.1f} orders/min', 
                           transform=self.ax.transAxes, color='white', 
                           fontsize=11, verticalalignment='top',
                           bbox=dict(boxstyle='round', facecolor='#4CAF50', alpha=0.7))
        
        self.ax.set_xlabel('Simulation Time (seconds)', fontsize=12)
        self.ax.set_ylabel('Completed Orders', fontsize=12)
        self.ax.set_title('Throughput Over Time', fontsize=14, fontweight='bold')
        self.ax.grid(True, alpha=0.3)
        
        self.fig.tight_layout()
        self.draw()

class FailuresChart(FigureCanvas):
    """Failures over time chart"""
    def __init__(self, parent=None):
        self.fig = Figure(figsize=(8, 6), dpi=80)
        super().__init__(self.fig)
        self.setParent(parent)
        self.ax = self.fig.add_subplot(111)
        self.setup_style()
    
    def setup_style(self):
        """Setup chart style"""
        self.ax.set_facecolor('#2b2b2b')
        self.fig.patch.set_facecolor('#2b2b2b')
        self.ax.tick_params(colors='white')
        self.ax.xaxis.label.set_color('white')
        self.ax.yaxis.label.set_color('white')
        self.ax.title.set_color('white')
    
    def plot_failures(self, failure_history: List[Tuple[float, str, str]]):
        """Plot failures over time"""
        self.ax.clear()
        
        if not failure_history:
            self.ax.text(0.5, 0.5, 'No failure data available', 
                        ha='center', va='center', color='white', fontsize=12)
            self.ax.set_xlim(0, 1)
            self.ax.set_ylim(0, 1)
            self.draw()
            return
        
        # Group failures by arm
        failures_by_arm = {}
        for time, arm, operation in failure_history:
            if arm not in failures_by_arm:
                failures_by_arm[arm] = []
            failures_by_arm[arm].append((time, operation))
        
        # Plot each arm's failures
        colors = plt.cm.Set1(np.linspace(0, 1, len(failures_by_arm)))
        arm_names = list(failures_by_arm.keys())
        
        for idx, (arm, failures) in enumerate(failures_by_arm.items()):
            times, operations = zip(*failures)
            self.ax.scatter(times, [idx] * len(times), 
                          color=colors[idx], s=100, label=arm, alpha=0.8)
            
            # Add operation labels
            for time, operation in failures:
                self.ax.annotate(operation, xy=(time, idx), 
                               xytext=(0, 10), textcoords='offset points',
                               ha='center', color='white', fontsize=9)
        
        self.ax.set_yticks(range(len(arm_names)))
        self.ax.set_yticklabels(arm_names)
        self.ax.set_xlabel('Simulation Time (seconds)', fontsize=12)
        self.ax.set_ylabel('Robotic Arm', fontsize=12)
        self.ax.set_title(f'Failures Over Time (Total: {len(failure_history)})', 
                         fontsize=14, fontweight='bold')
        self.ax.legend(facecolor='#3b3b3b', edgecolor='white', labelcolor='white')
        self.ax.grid(True, alpha=0.3, axis='x')
        
        self.fig.tight_layout()
        self.draw()

# ============================================================================
# Enhanced Main Visualization Window with Tabs
# ============================================================================

class Kitting3DVisualizationWindow(QMainWindow):
    """Enhanced main window with tabs for 3D visualization, charts, and metrics"""
    
    def __init__(self, parent=None, auto_load_path: str = None):
        super().__init__(parent)
        self.setWindowTitle("🎬 Kitting Station 3D Visualization & Analytics")
        self.setGeometry(100, 100, 1600, 900)
        
        # Initialize components
        self.log_processor = LogProcessor()
        self.timeline = []
        self.current_time_index = 0
        self.is_playing = False
        self.playback_speed = 1.0
        self.metrics = {}
        self.event_log_buffer = []
        
        # Store parent reference for auto-load
        self.parent_window = parent
        self.auto_load_path = auto_load_path
        
        # Setup UI
        self.setup_ui()
        
        # Setup playback timer
        self.playback_timer = QTimer()
        self.playback_timer.timeout.connect(self.update_playback)
        
        # Connect signals
        self.log_processor.timeline_loaded.connect(self.on_timeline_loaded)
        
        # Auto-load if path provided
        if auto_load_path:
            QTimer.singleShot(100, lambda: self.load_log_file(auto_load_path))
    
    def setup_ui(self):
        """Setup enhanced user interface with tabs"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        main_layout = QVBoxLayout(central_widget)
        
        # Toolbar
        self.setup_toolbar(main_layout)
        
        # Main content with tabs
        self.tab_widget = QTabWidget()
        self.tab_widget.setTabPosition(QTabWidget.North)
        
        # Create tabs
        self.overview_tab = self.create_overview_tab()
        self.charts_tab = self.create_charts_tab()
        self.metrics_tab = self.create_metrics_tab()
        
        self.tab_widget.addTab(self.overview_tab, "🏭 Overview")
        self.tab_widget.addTab(self.charts_tab, "📊 Charts")
        self.tab_widget.addTab(self.metrics_tab, "📈 Metrics")
        
        main_layout.addWidget(self.tab_widget)
        
        # Status bar
        self.status_bar = self.statusBar()
        self.status_bar.showMessage("Ready to load log file")
        
        # Apply dark theme
        self.apply_dark_theme()
    
    def setup_toolbar(self, main_layout):
        """Setup enhanced toolbar"""
        toolbar_layout = QHBoxLayout()
        toolbar_layout.setSpacing(10)
        
        # Load log button
        self.load_btn = QPushButton("📁 Load Log File")
        self.load_btn.clicked.connect(self.load_log_dialog)
        self.load_btn.setToolTip("Load a JSONL log file for visualization")
        toolbar_layout.addWidget(self.load_btn)
        
        # Load last run button
        self.load_last_btn = QPushButton("🔄 Load Last Run")
        self.load_last_btn.clicked.connect(self.load_last_run)
        self.load_last_btn.setToolTip("Load the most recent simulation log")
        toolbar_layout.addWidget(self.load_last_btn)
        
        toolbar_layout.addSpacing(20)
        
        # Playback controls
        self.play_btn = QPushButton("▶ Play")
        self.play_btn.clicked.connect(self.toggle_playback)
        self.play_btn.setEnabled(False)
        toolbar_layout.addWidget(self.play_btn)
        
        self.pause_btn = QPushButton("⏸ Pause")
        self.pause_btn.clicked.connect(self.pause_playback)
        self.pause_btn.setEnabled(False)
        toolbar_layout.addWidget(self.pause_btn)
        
        self.stop_btn = QPushButton("⏹ Stop")
        self.stop_btn.clicked.connect(self.stop_playback)
        self.stop_btn.setEnabled(False)
        toolbar_layout.addWidget(self.stop_btn)
        
        self.step_back_btn = QPushButton("⏪ Step Back")
        self.step_back_btn.clicked.connect(self.step_back)
        self.step_back_btn.setEnabled(False)
        toolbar_layout.addWidget(self.step_back_btn)
        
        self.step_forward_btn = QPushButton("⏩ Step Forward")
        self.step_forward_btn.clicked.connect(self.step_forward)
        self.step_forward_btn.setEnabled(False)
        toolbar_layout.addWidget(self.step_forward_btn)
        
        toolbar_layout.addSpacing(20)
        
        # Speed control
        toolbar_layout.addWidget(QLabel("Speed:"))
        self.speed_combo = QComboBox()
        self.speed_combo.addItems(["0.1x", "0.25x", "0.5x", "1x", "2x", "5x", "10x"])
        self.speed_combo.setCurrentText("1x")
        self.speed_combo.currentTextChanged.connect(self.change_speed)
        self.speed_combo.setEnabled(False)
        toolbar_layout.addWidget(self.speed_combo)
        
        toolbar_layout.addStretch()
        
        # Export button
        self.export_btn = QPushButton("💾 Export Report")
        self.export_btn.clicked.connect(self.export_report)
        self.export_btn.setEnabled(False)
        toolbar_layout.addWidget(self.export_btn)
        
        main_layout.addLayout(toolbar_layout)
    
    def create_overview_tab(self):
        """Create overview tab with 3D view and controls"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        # Splitter for 3D view and controls
        splitter = QSplitter(Qt.Horizontal)
        
        # 3D Visualization widget
        self.gl_widget = KittingStation3DWidget()
        splitter.addWidget(self.gl_widget)
        
        # Right panel with controls and event log
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        
        # Control panel
        control_group = QGroupBox("🎮 Visualization Controls")
        control_layout = QVBoxLayout(control_group)
        
        # Camera controls
        camera_group = QGroupBox("📷 Camera")
        camera_layout = QVBoxLayout(camera_group)
        
        self.reset_camera_btn = QPushButton("🔄 Reset Camera")
        self.reset_camera_btn.clicked.connect(lambda: self.gl_widget.reset_camera())
        camera_layout.addWidget(self.reset_camera_btn)
        
        self.focus_combo = QComboBox()
        self.focus_combo.addItems(["None", "PickingArm", "KittingArm", "MountingArm", "SolderingArm"])
        self.focus_combo.currentTextChanged.connect(self.change_camera_focus)
        camera_layout.addWidget(QLabel("Focus on Arm:"))
        camera_layout.addWidget(self.focus_combo)
        
        control_layout.addWidget(camera_group)
        
        # Visualization toggles
        viz_group = QGroupBox("👁️ Visualization Toggles")
        viz_layout = QGridLayout(viz_group)
        
        self.toggle_axes_check = QCheckBox("Show Axes")
        self.toggle_axes_check.setChecked(True)
        self.toggle_axes_check.stateChanged.connect(
            lambda state: self.gl_widget.toggle_axes(state == Qt.Checked)
        )
        viz_layout.addWidget(self.toggle_axes_check, 0, 0)
        
        self.toggle_grid_check = QCheckBox("Show Grid")
        self.toggle_grid_check.setChecked(True)
        self.toggle_grid_check.stateChanged.connect(
            lambda state: self.gl_widget.toggle_grid(state == Qt.Checked)
        )
        viz_layout.addWidget(self.toggle_grid_check, 0, 1)
        
        self.toggle_orders_check = QCheckBox("Show Orders")
        self.toggle_orders_check.setChecked(True)
        self.toggle_orders_check.stateChanged.connect(
            lambda state: self.gl_widget.toggle_orders(state == Qt.Checked)
        )
        viz_layout.addWidget(self.toggle_orders_check, 1, 0)
        
        self.toggle_trails_check = QCheckBox("Show Arm Trails")
        self.toggle_trails_check.setChecked(False)
        self.toggle_trails_check.stateChanged.connect(
            lambda state: self.gl_widget.toggle_arm_trails(state == Qt.Checked)
        )
        viz_layout.addWidget(self.toggle_trails_check, 1, 1)
        
        control_layout.addWidget(viz_group)
        
        # Time control
        time_group = QGroupBox("⏱️ Time Control")
        time_layout = QVBoxLayout(time_group)
        
        self.time_slider = QSlider(Qt.Horizontal)
        self.time_slider.setMinimum(0)
        self.time_slider.setMaximum(100)
        self.time_slider.valueChanged.connect(self.on_time_slider_changed)
        time_layout.addWidget(self.time_slider)
        
        time_info_layout = QHBoxLayout()
        self.time_label = QLabel("Time: 0.0 s")
        self.time_index_label = QLabel("Frame: 0/0")
        time_info_layout.addWidget(self.time_label)
        time_info_layout.addStretch()
        time_info_layout.addWidget(self.time_index_label)
        time_layout.addLayout(time_info_layout)
        
        control_layout.addWidget(time_group)
        
        # KPI Panel (ONLY KPIs here)
        kpi_group = QGroupBox("📊 Live KPIs")
        kpi_layout = QGridLayout(kpi_group)
        
        self.queued_label = QLabel("Queued Orders: 0")
        kpi_layout.addWidget(self.queued_label, 0, 0)
        
        self.wip_label = QLabel("Work In Progress: 0")
        kpi_layout.addWidget(self.wip_label, 0, 1)
        
        self.completed_label = QLabel("Completed Orders: 0")
        kpi_layout.addWidget(self.completed_label, 1, 0)
        
        self.failures_label = QLabel("Total Failures: 0")
        kpi_layout.addWidget(self.failures_label, 1, 1)
        
        self.station_state_label = QLabel("Station State: IDLE")
        kpi_layout.addWidget(self.station_state_label, 2, 0, 1, 2)
        
        control_layout.addWidget(kpi_group)
        
        # Arm status
        arm_group = QGroupBox("🤖 Arm Status")
        arm_layout = QGridLayout(arm_group)
        
        self.picking_arm_label = QLabel("Picking Arm: IDLE")
        arm_layout.addWidget(self.picking_arm_label, 0, 0)
        
        self.kitting_arm_label = QLabel("Kitting Arm: IDLE")
        arm_layout.addWidget(self.kitting_arm_label, 0, 1)
        
        self.mounting_arm_label = QLabel("Mounting Arm: IDLE")
        arm_layout.addWidget(self.mounting_arm_label, 1, 0)
        
        self.soldering_arm_label = QLabel("Soldering Arm: IDLE")
        arm_layout.addWidget(self.soldering_arm_label, 1, 1)
        
        control_layout.addWidget(arm_group)
        
        control_layout.addStretch()
        right_layout.addWidget(control_group)
        
        # Event log display
        log_group = QGroupBox("📋 Event Log")
        log_layout = QVBoxLayout(log_group)
        
        self.event_log_text = QTextEdit()
        self.event_log_text.setReadOnly(True)
        self.event_log_text.setMaximumHeight(200)
        self.event_log_text.setFont(QFont("Courier", 9))
        log_layout.addWidget(self.event_log_text)
        
        right_layout.addWidget(log_group)
        
        splitter.addWidget(right_panel)
        splitter.setSizes([1000, 400])
        
        layout.addWidget(splitter)
        return tab
    
    def create_charts_tab(self):
        """Create charts tab with matplotlib plots"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        # Create charts
        self.cycle_time_chart = CycleTimeChart()
        self.throughput_chart = ThroughputChart()
        self.failures_chart = FailuresChart()
        
        # Add charts to layout
        layout.addWidget(QLabel("📈 Cycle Time Distribution", self))
        layout.addWidget(self.cycle_time_chart)
        
        layout.addWidget(QLabel("📈 Throughput Over Time", self))
        layout.addWidget(self.throughput_chart)
        
        layout.addWidget(QLabel("📈 Failures Over Time", self))
        layout.addWidget(self.failures_chart)
        
        # Add refresh button
        refresh_btn = QPushButton("🔄 Refresh Charts")
        refresh_btn.clicked.connect(self.refresh_charts)
        layout.addWidget(refresh_btn)
        
        return tab
    
    def create_metrics_tab(self):
        """Create metrics tab with detailed KPIs + Station 1 explanation"""
        tab = QWidget()
        outer_layout = QVBoxLayout(tab)

        splitter = QSplitter(Qt.Horizontal)

        # ---------- LEFT: metrics ----------
        metrics_container = QWidget()
        metrics_layout = QVBoxLayout(metrics_container)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        
        # Order metrics
        order_group = QGroupBox("📦 Order Metrics")
        order_layout = QGridLayout(order_group)
        
        self.total_orders_label = QLabel("Total Orders: 0")
        order_layout.addWidget(self.total_orders_label, 0, 0)
        
        self.completed_orders_label = QLabel("Completed Orders: 0")
        order_layout.addWidget(self.completed_orders_label, 0, 1)
        
        self.avg_cycle_time_label = QLabel("Avg Cycle Time: 0.0s")
        order_layout.addWidget(self.avg_cycle_time_label, 1, 0)
        
        self.min_cycle_time_label = QLabel("Min Cycle Time: 0.0s")
        order_layout.addWidget(self.min_cycle_time_label, 1, 1)
        
        self.max_cycle_time_label = QLabel("Max Cycle Time: 0.0s")
        order_layout.addWidget(self.max_cycle_time_label, 2, 0)
        
        self.max_queue_label = QLabel("Max Queue Length: 0")
        order_layout.addWidget(self.max_queue_label, 2, 1)
        
        content_layout.addWidget(order_group)
        
        # Failure metrics
        failure_group = QGroupBox("🚨 Failure Metrics")
        failure_layout = QGridLayout(failure_group)
        
        self.total_failures_label = QLabel("Total Failures: 0")
        failure_layout.addWidget(self.total_failures_label, 0, 0)
        
        self.failures_table = QTableWidget()
        self.failures_table.setColumnCount(3)
        self.failures_table.setHorizontalHeaderLabels(["Arm", "Failures", "% of Total"])
        self.failures_table.horizontalHeader().setStretchLastSection(True)
        failure_layout.addWidget(self.failures_table, 1, 0, 1, 2)
        
        content_layout.addWidget(failure_group)
        
        # Arm utilization
        util_group = QGroupBox("⚙️ Arm Utilization")
        util_layout = QGridLayout(util_group)
        
        self.util_table = QTableWidget()
        self.util_table.setColumnCount(2)
        self.util_table.setHorizontalHeaderLabels(["Arm", "Utilization %"])
        self.util_table.horizontalHeader().setStretchLastSection(True)
        util_layout.addWidget(self.util_table, 0, 0, 1, 2)
        
        content_layout.addWidget(util_group)
        
        # Performance summary
        perf_group = QGroupBox("🏆 Performance Summary")
        perf_layout = QVBoxLayout(perf_group)
        
        self.performance_text = QTextEdit()
        self.performance_text.setReadOnly(True)
        self.performance_text.setMaximumHeight(150)
        perf_layout.addWidget(self.performance_text)
        
        content_layout.addWidget(perf_group)
        
        content_layout.addStretch()
        scroll.setWidget(content)
        metrics_layout.addWidget(scroll)

        splitter.addWidget(metrics_container)

        # ---------- RIGHT: Station info panel ----------
        self.station_info_panel = StationInfoPanel()
        splitter.addWidget(self.station_info_panel)

        splitter.setSizes([900, 500])

        outer_layout.addWidget(splitter)
        return tab
    
    def apply_dark_theme(self):
        """Apply dark theme to the window"""
        palette = QPalette()
        palette.setColor(QPalette.Window, QColor(53, 53, 53))
        palette.setColor(QPalette.WindowText, Qt.white)
        palette.setColor(QPalette.Base, QColor(25, 25, 25))
        palette.setColor(QPalette.AlternateBase, QColor(53, 53, 53))
        palette.setColor(QPalette.ToolTipBase, Qt.white)
        palette.setColor(QPalette.ToolTipText, Qt.white)
        palette.setColor(QPalette.Text, Qt.white)
        palette.setColor(QPalette.Button, QColor(53, 53, 53))
        palette.setColor(QPalette.ButtonText, Qt.white)
        palette.setColor(QPalette.BrightText, Qt.red)
        palette.setColor(QPalette.Link, QColor(42, 130, 218))
        palette.setColor(QPalette.Highlight, QColor(42, 130, 218))
        palette.setColor(QPalette.HighlightedText, Qt.black)
        self.setPalette(palette)
    
    # -------------- rest of your methods stay the same --------------
    # load_log_dialog, load_last_run, load_log_file, on_timeline_loaded,
    # process_events_for_log, update_event_log_display, toggle_playback, 
    # pause_playback, stop_playback, step_back, step_forward, change_speed,
    # change_camera_focus, update_playback, on_time_slider_changed,
    # update_display, refresh_charts, update_metrics_tab, export_report,
    # closeEvent

    def apply_dark_theme(self):
        """Apply dark theme to the window"""
        palette = QPalette()
        palette.setColor(QPalette.Window, QColor(53, 53, 53))
        palette.setColor(QPalette.WindowText, Qt.white)
        palette.setColor(QPalette.Base, QColor(25, 25, 25))
        palette.setColor(QPalette.AlternateBase, QColor(53, 53, 53))
        palette.setColor(QPalette.ToolTipBase, Qt.white)
        palette.setColor(QPalette.ToolTipText, Qt.white)
        palette.setColor(QPalette.Text, Qt.white)
        palette.setColor(QPalette.Button, QColor(53, 53, 53))
        palette.setColor(QPalette.ButtonText, Qt.white)
        palette.setColor(QPalette.BrightText, Qt.red)
        palette.setColor(QPalette.Link, QColor(42, 130, 218))
        palette.setColor(QPalette.Highlight, QColor(42, 130, 218))
        palette.setColor(QPalette.HighlightedText, Qt.black)
        self.setPalette(palette)
    
    def load_log_dialog(self):
        """Open file dialog to load log file"""
        filepath, _ = QFileDialog.getOpenFileName(
            self, "Select Log File", "", 
            "Log Files (*.jsonl *.json);;All Files (*.*)"
        )
        
        if filepath:
            self.load_log_file(filepath)
    
    def load_last_run(self):
        """Load the most recent simulation log"""
        try:
            import os
            import glob
            from datetime import datetime
            
            # Find all log files
            log_files = glob.glob("kitting_station_1_plc_events_*.jsonl")
            
            if not log_files:
                QMessageBox.information(self, "No Logs", "No simulation logs found.")
                return
            
            # Sort by timestamp (newest first)
            log_files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
            latest_log = log_files[0]
            
            self.load_log_file(latest_log)
            
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Could not load last run: {str(e)}")
    
    def load_log_file(self, filepath: str):
        """Load a log file"""
        self.load_btn.setEnabled(False)
        self.load_btn.setText("Processing...")
        self.status_bar.showMessage(f"Loading {filepath}...")
        
        # Process in background thread
        from threading import Thread
        thread = Thread(target=self.log_processor.load_log_file, args=(filepath,))
        thread.daemon = True
        thread.start()
    
    def on_timeline_loaded(self, timeline, metrics):
        """Handle timeline loaded signal"""
        self.timeline = timeline
        self.metrics = metrics
        
        if timeline:
            self.time_slider.setMaximum(len(timeline) - 1)
            self.update_display(0)
            
            # Enable controls
            self.play_btn.setEnabled(True)
            self.pause_btn.setEnabled(True)
            self.stop_btn.setEnabled(True)
            self.step_back_btn.setEnabled(True)
            self.step_forward_btn.setEnabled(True)
            self.speed_combo.setEnabled(True)
            self.export_btn.setEnabled(True)
            
            self.load_btn.setEnabled(True)
            self.load_btn.setText("📁 Load Log File")
            
            # Update charts and metrics
            self.refresh_charts()
            self.update_metrics_tab()
            
            # Process events for log display
            self.process_events_for_log()
            
            self.status_bar.showMessage(f"Loaded {len(timeline)} timeline states, max time: {timeline[-1].sim_time:.1f}s")
            
            QMessageBox.information(
                self, "Log Loaded", 
                f"Successfully loaded log file\n"
                f"• Timeline: {len(timeline)} states\n"
                f"• Max time: {timeline[-1].sim_time:.1f}s\n"
                f"• Orders: {metrics.get('total_orders', 0)}\n"
                f"• Failures: {metrics.get('total_failures', 0)}"
            )
    
    def process_events_for_log(self):
        """Process events for the event log display"""
        self.event_log_buffer = []
        
        for event in self.log_processor.events:
            sim_time = event.get('sim_time', 0)
            category = event.get('category', '')
            event_type = event.get('event_type', '')
            
            # Create formatted log entry
            if category == "ARM":
                if event_type == "STATE_CHANGE":
                    arm = event.get('arm_name', 'Unknown')
                    state = event.get('new_state', '')
                    x = event.get('x', 0)
                    y = event.get('y', 0)
                    entry = f"[{sim_time:6.1f}s] 🤖 {arm}: {state} at ({x}, {y})"
                elif event_type == "FAILURE":
                    arm = event.get('arm_name', 'Unknown')
                    op = event.get('operation', 'unknown')
                    fid = event.get('failure_id', 0)
                    entry = f"[{sim_time:6.1f}s] ⚠️  {arm} FAILURE #{fid} during {op}"
                else:
                    entry = f"[{sim_time:6.1f}s] ARM {event_type}"
            
            elif category == "PLC":
                if event_type == "SNAPSHOT":
                    state = event.get('station_state', '')
                    queued = event.get('queued_orders', 0)
                    wip = event.get('wip_count', 0)
                    completed = event.get('completed_count', 0)
                    entry = f"[{sim_time:6.1f}s] 🏭 PLC: state={state}, queued={queued}, wip={wip}, completed={completed}"
                else:
                    entry = f"[{sim_time:6.1f}s] PLC {event_type}"
            
            elif category == "ORDER":
                if event_type == "CREATED":
                    order_id = event.get('order_id', 0)
                    model = event.get('model_type', 'Unknown')
                    entry = f"[{sim_time:6.1f}s] 📋 ORDER #{order_id} created ({model})"
                elif event_type == "ORDER_START":
                    entry = f"[{sim_time:6.1f}s] ▶ Order processing started"
                elif event_type == "ORDER_COMPLETE":
                    order_id = event.get('order_id', 0)
                    entry = f"[{sim_time:6.1f}s] ✅ ORDER #{order_id} completed"
                else:
                    entry = f"[{sim_time:6.1f}s] ORDER {event_type}"
            
            elif category == "INVENTORY":
                state = event.get('state', '')
                entry = f"[{sim_time:6.1f}s] 📦 Inventory: {state}"
            
            elif category == "OUTPUT":
                state = event.get('state', '')
                count = event.get('completed_count', 0)
                entry = f"[{sim_time:6.1f}s] 📤 Output: {state} (total: {count})"
            
            else:
                entry = f"[{sim_time:6.1f}s] {category} {event_type}"
            
            self.event_log_buffer.append(entry)
        
        # Update event log display
        self.update_event_log_display()
    
    def update_event_log_display(self):
        """Update event log display with current position"""
        if not self.event_log_buffer:
            return
        
        # Find events up to current time
        current_time = self.timeline[self.current_time_index].sim_time
        events_to_show = []
        
        for i, event in enumerate(self.log_processor.events):
            if float(event.get('sim_time', 0)) <= current_time:
                events_to_show.append(self.event_log_buffer[i])
        
        # Display events (show last 20)
        display_text = "\n".join(events_to_show[-20:])
        self.event_log_text.setText(display_text)
        
        # Scroll to bottom
        scrollbar = self.event_log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
    
    def toggle_playback(self):
        """Toggle playback"""
        if not self.timeline:
            QMessageBox.warning(self, "No Data", "Please load a log file first")
            return
        
        self.is_playing = not self.is_playing
        
        if self.is_playing:
            self.play_btn.setText("⏸ Pause")
            interval = int(1000 / (30 * self.playback_speed))  # 30 FPS adjusted by speed
            self.playback_timer.start(interval)
        else:
            self.play_btn.setText("▶ Play")
            self.playback_timer.stop()
    
    def pause_playback(self):
        """Pause playback"""
        self.is_playing = False
        self.play_btn.setText("▶ Play")
        self.playback_timer.stop()
    
    def stop_playback(self):
        """Stop playback and reset to start"""
        self.is_playing = False
        self.play_btn.setText("▶ Play")
        self.playback_timer.stop()
        self.current_time_index = 0
        self.update_display(0)
    
    def step_back(self):
        """Step one frame back"""
        if self.current_time_index > 0:
            self.current_time_index -= 1
            self.time_slider.setValue(self.current_time_index)
            self.update_display(self.current_time_index)
    
    def step_forward(self):
        """Step one frame forward"""
        if self.current_time_index < len(self.timeline) - 1:
            self.current_time_index += 1
            self.time_slider.setValue(self.current_time_index)
            self.update_display(self.current_time_index)
    
    def change_speed(self, speed_text):
        """Change playback speed"""
        speed_map = {
            "0.1x": 0.1,
            "0.25x": 0.25,
            "0.5x": 0.5,
            "1x": 1.0,
            "2x": 2.0,
            "5x": 5.0,
            "10x": 10.0
        }
        self.playback_speed = speed_map.get(speed_text, 1.0)
        self.gl_widget.time_multiplier = self.playback_speed
        
        if self.is_playing:
            self.playback_timer.stop()
            interval = int(1000 / (30 * self.playback_speed))
            self.playback_timer.start(interval)
    
    def change_camera_focus(self, arm_name: str):
        """Change camera focus to specific arm"""
        if arm_name == "None":
            self.gl_widget.focus_arm = None
        else:
            self.gl_widget.focus_on_arm(arm_name)
    
    def update_playback(self):
        """Update playback to next frame"""
        if not self.timeline or self.current_time_index >= len(self.timeline) - 1:
            self.pause_playback()
            return
        
        self.current_time_index += 1
        self.time_slider.setValue(self.current_time_index)
        self.update_display(self.current_time_index)
    
    def on_time_slider_changed(self, value):
        """Handle time slider change"""
        if not self.timeline:
            return
        
        self.current_time_index = min(value, len(self.timeline) - 1)
        self.update_display(self.current_time_index)
    
    def update_display(self, index):
        """Update all displays for given timeline index"""
        if not self.timeline or index >= len(self.timeline):
            return
        
        state = self.timeline[index]
        
        # Update 3D visualization
        self.gl_widget.set_current_state(state)
        
        # Update time labels
        self.time_label.setText(f"Time: {state.sim_time:.1f} s")
        self.time_index_label.setText(f"Frame: {index + 1}/{len(self.timeline)}")
        
        # Update KPIs
        self.queued_label.setText(f"Queued Orders: {state.queued_orders}")
        self.wip_label.setText(f"Work In Progress: {state.wip_count}")
        self.completed_label.setText(f"Completed Orders: {state.completed_count}")
        self.failures_label.setText(f"Total Failures: {state.total_failures}")
        self.station_state_label.setText(f"Station State: {state.station_state.value}")
        
        # Update arm status
        if state.arms:
            self.picking_arm_label.setText(
                f"Picking Arm: {state.arms.get('PickingArm', ArmVisualState('PickingArm', 'PICKING')).state.value}"
            )
            self.kitting_arm_label.setText(
                f"Kitting Arm: {state.arms.get('KittingArm', ArmVisualState('KittingArm', 'KITTING')).state.value}"
            )
            self.mounting_arm_label.setText(
                f"Mounting Arm: {state.arms.get('MountingArm', ArmVisualState('MountingArm', 'MOUNTING')).state.value}"
            )
            self.soldering_arm_label.setText(
                f"Soldering Arm: {state.arms.get('SolderingArm', ArmVisualState('SolderingArm', 'SOLDERING')).state.value}"
            )
        
        # Update event log display
        self.update_event_log_display()
    
    def refresh_charts(self):
        """Refresh all charts with current data"""
        if not self.timeline:
            return
        
        # Get data from last state
        last_state = self.timeline[-1]
        
        # Update charts
        self.cycle_time_chart.plot_cycle_times(last_state.cycle_times)
        self.throughput_chart.plot_throughput(last_state.throughput_history)
        self.failures_chart.plot_failures(last_state.failure_history)
    
    def update_metrics_tab(self):
        """Update metrics tab with calculated metrics"""
        # Order metrics
        self.total_orders_label.setText(f"Total Orders: {self.metrics.get('total_orders', 0)}")
        self.completed_orders_label.setText(f"Completed Orders: {self.metrics.get('completed_orders', 0)}")
        self.avg_cycle_time_label.setText(f"Avg Cycle Time: {self.metrics.get('avg_cycle_time', 0.0):.1f}s")
        self.min_cycle_time_label.setText(f"Min Cycle Time: {self.metrics.get('min_cycle_time', 0.0):.1f}s")
        self.max_cycle_time_label.setText(f"Max Cycle Time: {self.metrics.get('max_cycle_time', 0.0):.1f}s")
        self.max_queue_label.setText(f"Max Queue Length: {self.metrics.get('max_queue_length', 0)}")
        
        # Failure metrics
        self.total_failures_label.setText(f"Total Failures: {self.metrics.get('total_failures', 0)}")
        
        failures_by_arm = self.metrics.get('failures_by_arm', {})
        self.failures_table.setRowCount(len(failures_by_arm))
        
        total_failures = self.metrics.get('total_failures', 1)  # Avoid division by zero
        
        for row, (arm, count) in enumerate(failures_by_arm.items()):
            self.failures_table.setItem(row, 0, QTableWidgetItem(arm))
            self.failures_table.setItem(row, 1, QTableWidgetItem(str(count)))
            percentage = (count / total_failures * 100) if total_failures > 0 else 0
            self.failures_table.setItem(row, 2, QTableWidgetItem(f"{percentage:.1f}%"))
        
        # Arm utilization
        utilization_by_arm = self.metrics.get('utilization_by_arm', {})
        self.util_table.setRowCount(len(utilization_by_arm))
        
        for row, (arm, utilization) in enumerate(utilization_by_arm.items()):
            self.util_table.setItem(row, 0, QTableWidgetItem(arm))
            self.util_table.setItem(row, 1, QTableWidgetItem(f"{utilization:.1f}%"))
        
        # Performance summary
        summary = []
        summary.append("🏆 PERFORMANCE SUMMARY")
        summary.append("=" * 40)
        summary.append(f"Total Orders Processed: {self.metrics.get('completed_orders', 0)}")
        summary.append(f"Success Rate: {(self.metrics.get('completed_orders', 0) / max(1, self.metrics.get('total_orders', 1)) * 100):.1f}%")
        summary.append(f"Average Cycle Time: {self.metrics.get('avg_cycle_time', 0.0):.1f}s")
        summary.append(f"Total Failures: {self.metrics.get('total_failures', 0)}")
        summary.append(f"Max Queue Length: {self.metrics.get('max_queue_length', 0)}")
        summary.append("\nArm Performance:")
        
        for arm, util in utilization_by_arm.items():
            failures = failures_by_arm.get(arm, 0)
            summary.append(f"  • {arm}: {util:.1f}% utilization, {failures} failures")
        
        self.performance_text.setText("\n".join(summary))
    
    def export_report(self):
        """Export performance report"""
        filepath, _ = QFileDialog.getSaveFileName(
            self, "Export Report", "kitting_station_report.txt",
            "Text Files (*.txt);;All Files (*.*)"
        )
        
        if filepath:
            try:
                with open(filepath, 'w') as f:
                    f.write("KITTING STATION PERFORMANCE REPORT\n")
                    f.write("=" * 50 + "\n\n")
                    
                    # Write metrics
                    f.write("ORDER METRICS:\n")
                    f.write(f"  Total Orders: {self.metrics.get('total_orders', 0)}\n")
                    f.write(f"  Completed Orders: {self.metrics.get('completed_orders', 0)}\n")
                    f.write(f"  Success Rate: {(self.metrics.get('completed_orders', 0) / max(1, self.metrics.get('total_orders', 1)) * 100):.1f}%\n")
                    f.write(f"  Average Cycle Time: {self.metrics.get('avg_cycle_time', 0.0):.1f}s\n")
                    f.write(f"  Min Cycle Time: {self.metrics.get('min_cycle_time', 0.0):.1f}s\n")
                    f.write(f"  Max Cycle Time: {self.metrics.get('max_cycle_time', 0.0):.1f}s\n")
                    f.write(f"  Max Queue Length: {self.metrics.get('max_queue_length', 0)}\n\n")
                    
                    f.write("FAILURE METRICS:\n")
                    f.write(f"  Total Failures: {self.metrics.get('total_failures', 0)}\n")
                    for arm, count in self.metrics.get('failures_by_arm', {}).items():
                        f.write(f"    {arm}: {count} failures\n")
                    
                    f.write("\nARM UTILIZATION:\n")
                    for arm, util in self.metrics.get('utilization_by_arm', {}).items():
                        f.write(f"    {arm}: {util:.1f}%\n")
                    
                    f.write(f"\nReport generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                
                QMessageBox.information(self, "Export Successful", f"Report exported to {filepath}")
                
            except Exception as e:
                QMessageBox.warning(self, "Export Failed", f"Could not export report: {str(e)}")
    
    def closeEvent(self, event):
        """Clean up on close"""
        self.playback_timer.stop()
        event.accept()

# ============================================================================
# Integration Helper
# ============================================================================

def add_3d_viz_to_main_window():
    """
    Integration helper for main.py
    Returns a function that opens the 3D visualization window
    
    Usage in main.py:
        from visual import add_3d_viz_to_main_window
        ...
        viz_btn = QPushButton("🎬 3D Visualization")
        viz_btn.clicked.connect(add_3d_viz_to_main_window())
    """
    def open_3d_visualization():
        """Open 3D visualization window"""
        # Initialize GLUT if not already initialized
        try:
            glutInit([])
        except:
            pass
        
        viz_window = Kitting3DVisualizationWindow()
        viz_window.show()
        
        # Try to auto-load last run
        try:
            import os
            import glob
            
            log_files = glob.glob("kitting_station_1_plc_events_*.jsonl")
            if log_files:
                log_files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
                viz_window.status_bar.showMessage(f"Found recent log: {log_files[0]}")
        except:
            pass
        
        return viz_window
    
    return open_3d_visualization

def open_visualization_with_log(log_path: str):
    """
    Open visualization window with specific log file
    
    Usage: After simulation completes, call this with the generated log path
    """
    def open_viz():
        try:
            glutInit([])
        except:
            pass
        
        viz_window = Kitting3DVisualizationWindow(auto_load_path=log_path)
        viz_window.show()
        return viz_window
    
    return open_viz

# ============================================================================
# Main Entry Point
# ============================================================================

def main():
    """Main entry point for standalone 3D visualization"""
    import sys
    
    # Initialize GLUT for text rendering
    try:
        glutInit([])
    except:
        pass
    
    # Create and show window
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    
    window = Kitting3DVisualizationWindow()
    window.show()
    
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()