"""
SCADA Dashboard for Kitting Station 1
Real-time monitoring of Sensors → Controller → Actuators workflow
"""

import math
from typing import Dict, Any, Optional
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QGridLayout, QGroupBox, QPushButton, QApplication, QMainWindow,
    QSizePolicy, QSpacerItem, QComboBox
)
from PyQt5.QtCore import Qt, QTimer, QSize
from PyQt5.QtGui import QFont, QColor, QPalette, QPainter, QPen, QLinearGradient


class LampIndicator(QWidget):
    """Custom lamp widget with animation for sensor/actuator states"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(14, 14)
        self.setMaximumSize(18, 18)
        
        # State
        self._active = False
        self._severity = "normal"  # normal, warning, error
        self._size = 12
        self._pulse_value = 0.0
        
        # Animation
        self.pulse_timer = QTimer(self)
        self.pulse_timer.timeout.connect(self.update_pulse)
        self.pulse_timer.setInterval(100)
        
        # Colors
        self.colors = {
            "normal": {"on": QColor(0, 200, 0), "off": QColor(70, 70, 70)},
            "warning": {"on": QColor(255, 200, 0), "off": QColor(100, 80, 0)},
            "error": {"on": QColor(255, 50, 50), "off": QColor(100, 30, 30)}
        }
    
    def set_state(self, active: bool, severity: str = "normal"):
        """Set lamp state with optional severity"""
        was_active = self._active
        self._active = active
        self._severity = severity
        
        if active and not was_active:
            # Start pulsing animation when turning on
            self._size = 14
            self.pulse_timer.start()
        elif not active:
            self._size = 12
            self.pulse_timer.stop()
        
        self.update()
    
    def update_pulse(self):
        """Update pulse animation for active lamps"""
        self._pulse_value = (self._pulse_value + 0.2) % (2 * math.pi)
        self.update()
    
    def paintEvent(self, event):
        """Draw the lamp"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # Lamp body with gradient
        center = self.rect().center()
        radius = self._size // 2
        
        if self._active:
            # Pulsing effect
            pulse = 1 + 0.15 * math.sin(self._pulse_value)
            radius = int(radius * pulse)
            
            # Gradient for active lamp
            gradient = QLinearGradient(
                center.x() - radius, center.y() - radius,
                center.x() + radius, center.y() + radius
            )
            
            color = self.colors[self._severity]["on"]
            gradient.setColorAt(0, color.lighter(150))
            gradient.setColorAt(0.5, color)
            gradient.setColorAt(1, color.darker(150))
            
            painter.setBrush(gradient)
            
            # Glow effect
            painter.setPen(Qt.NoPen)
            painter.setOpacity(0.6)
            glow_radius = int(radius * 1.3)
            painter.drawEllipse(center, glow_radius, glow_radius)
            
            painter.setOpacity(1.0)
            painter.setPen(QPen(color.lighter(150), 1))
        else:
            # Inactive lamp
            color = self.colors[self._severity]["off"]
            painter.setBrush(color)
            painter.setPen(QPen(color.lighter(100), 1))
        
        painter.drawEllipse(center, radius, radius)
        
        # Draw inner highlight for active lamps
        if self._active:
            painter.setBrush(QColor(255, 255, 255, 120))
            painter.setPen(Qt.NoPen)
            highlight_radius = max(2, radius // 3)
            painter.drawEllipse(
                center.x() - highlight_radius,
                center.y() - highlight_radius,
                highlight_radius * 2,
                highlight_radius * 2
            )
    
    def sizeHint(self):
        return QSize(16, 16)


class PhaseLamp(QWidget):
    """Phase indicator for PLC workflow steps"""
    
    def __init__(self, label, parent=None):
        super().__init__(parent)
        self.label = label
        self._active = False
        self._current = False
        self.setFixedSize(60, 40)
    
    def set_state(self, active: bool, current: bool = False):
        self._active = active
        self._current = current
        self.update()
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # Draw lamp
        if self._active:
            if self._current:
                # Current phase - pulsing green
                pulse = 1 + 0.1 * math.sin(self._pulse_value() if hasattr(self, '_pulse_value') else 0)
                radius = int(8 * pulse)
                painter.setBrush(QColor(0, 220, 0))
                painter.setPen(QPen(QColor(100, 255, 100), 1, Qt.SolidLine))
            else:
                # Completed phase
                radius = 7
                painter.setBrush(QColor(0, 150, 0))
                painter.setPen(QPen(QColor(0, 200, 0), 1, Qt.SolidLine))
        else:
            # Inactive phase
            radius = 6
            painter.setBrush(QColor(80, 80, 80))
            painter.setPen(QPen(QColor(120, 120, 120), 1, Qt.SolidLine))
        
        painter.drawEllipse(30 - radius, 10, radius * 2, radius * 2)
        
        # Draw label with smaller font
        painter.setPen(QColor(200, 200, 200))
        painter.setFont(QFont("Arial", 7, QFont.Normal))
        text_rect = painter.fontMetrics().boundingRect(self.label)
        painter.drawText(
            30 - text_rect.width() // 2,
            32,
            text_rect.width(),
            20,
            Qt.AlignCenter,
            self.label
        )
    
    def sizeHint(self):
        return QSize(60, 40)


class ArrowLabel(QLabel):
    """Simple arrow label for phase timeline"""
    
    def __init__(self, parent=None):
        super().__init__("→", parent)
        self.setAlignment(Qt.AlignCenter)
        self.setFont(QFont("Arial", 12))
        self.setStyleSheet("color: #888;")
        self.setFixedWidth(20)


class ScadaDashboardWidget(QWidget):
    """
    SCADA Dashboard for real-time monitoring of Sensors → Controller → Actuators
    """
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # Reference to station object
        self.station = None
        self.sim_manager = None
        
        # UI Elements
        self.phase_lamps = {}
        self.sensor_indicators = {}
        self.actuator_indicators = {}
        
        # Setup UI
        self.setup_ui()
        self.setup_styling()
        
        # Update timer for real-time updates
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self.update_from_live_station)
        self.update_interval = 300  # ms
        
        # Start with disconnected state
        self.status_label.setText("Status: Not connected")
    
    def setup_ui(self):
        """Setup the SCADA dashboard layout"""
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(12, 12, 12, 12)
        
        # Title
        title_label = QLabel("🏭 KITTING STATION 1 - PLC SCADA DASHBOARD")
        title_label.setFont(QFont("Arial", 14, QFont.Bold))
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("""
            QLabel {
                color: #4CAF50;
                background-color: #2a2a35;
                padding: 12px;
                border-radius: 6px;
                border: 2px solid #4CAF50;
                margin-bottom: 10px;
            }
        """)
        main_layout.addWidget(title_label)
        
        # === MAIN SCADA PANEL ===
        scada_panel = QWidget()
        scada_layout = QHBoxLayout(scada_panel)
        scada_layout.setSpacing(15)
        scada_layout.setContentsMargins(5, 5, 5, 5)
        
        # === SENSORS PANEL (Left) ===
        sensors_group = QGroupBox("📡 SENSORS")
        sensors_group.setMinimumWidth(340)
        sensors_group.setMaximumWidth(400)
        sensors_layout = QGridLayout(sensors_group)
        sensors_layout.setSpacing(6)
        sensors_layout.setContentsMargins(12, 15, 12, 12)
        
        # Station sensors
        sensors_layout.addWidget(QLabel("<b>Station Sensors</b>"), 0, 0, 1, 3)
        
        self.create_sensor_indicator("station_state", "Station State", sensors_layout, 1)
        self.create_sensor_indicator("queued_orders", "Queued Orders", sensors_layout, 2)
        self.create_sensor_indicator("wip_count", "WIP Count", sensors_layout, 3)
        self.create_sensor_indicator("completed_count", "Completed", sensors_layout, 4)
        self.create_sensor_indicator("total_failures", "Total Failures", sensors_layout, 5)
        
        sensors_layout.addItem(QSpacerItem(10, 10), 6, 0)
        
        # Arm sensors
        sensors_layout.addWidget(QLabel("<b>Arm Sensors</b>"), 7, 0, 1, 3)
        
        self.create_sensor_indicator("picking_arm", "Picking Arm", sensors_layout, 8)
        self.create_sensor_indicator("kitting_arm", "Kitting Arm", sensors_layout, 9)
        self.create_sensor_indicator("mounting_arm", "Mounting Arm", sensors_layout, 10)
        self.create_sensor_indicator("soldering_arm", "Soldering Arm", sensors_layout, 11)
        
        sensors_layout.addItem(QSpacerItem(10, 10), 12, 0)
        
        # Component sensors
        sensors_layout.addWidget(QLabel("<b>Component Sensors</b>"), 13, 0, 1, 3)
        
        self.create_sensor_indicator("inventory", "Inventory", sensors_layout, 14)
        self.create_sensor_indicator("output", "Output", sensors_layout, 15)
        self.create_sensor_indicator("failure", "Failure Det.", sensors_layout, 16)
        
        sensors_layout.setRowStretch(17, 1)
        scada_layout.addWidget(sensors_group)
        
        # === CONTROLLER SECTION (Center) ===
        controller_group = QGroupBox("🎛️ PLC CONTROLLER")
        controller_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        controller_layout = QVBoxLayout(controller_group)
        controller_layout.setSpacing(12)
        controller_layout.setContentsMargins(15, 15, 15, 15)
        
        # PLC State display
        state_frame = QFrame()
        state_frame.setFrameStyle(QFrame.StyledPanel | QFrame.Raised)
        state_frame.setStyleSheet("background-color: #2a2a35; border-radius: 8px;")
        state_layout = QVBoxLayout(state_frame)
        state_layout.setContentsMargins(10, 10, 10, 10)
        
        self.plc_state_label = QLabel("PLC State: IDLE")
        self.plc_state_label.setFont(QFont("Arial", 13, QFont.Bold))
        self.plc_state_label.setAlignment(Qt.AlignCenter)
        self.plc_state_label.setMinimumHeight(50)
        state_layout.addWidget(self.plc_state_label)
        
        controller_layout.addWidget(state_frame, 0)
        
        # Phase timeline
        phase_group = QGroupBox("Workflow Timeline")
        phase_group.setFont(QFont("Arial", 9, QFont.Bold))
        phase_layout = QVBoxLayout(phase_group)
        phase_layout.setContentsMargins(10, 15, 10, 10)
        
        # Phase lamps in a horizontal layout
        phases_frame = QFrame()
        phases_layout = QHBoxLayout(phases_frame)
        phases_layout.setSpacing(2)
        phases_layout.setContentsMargins(5, 5, 5, 5)
        
        phases = ["Order", "Inventory", "Picking", "Kitting", "Mounting", "Soldering", "Output"]
        for i, phase in enumerate(phases):
            lamp = PhaseLamp(phase)
            self.phase_lamps[phase] = lamp
            phases_layout.addWidget(lamp, 0, Qt.AlignCenter)
            
            # Add arrow between phases (except after the last one)
            if i < len(phases) - 1:
                arrow = ArrowLabel()
                phases_layout.addWidget(arrow, 0, Qt.AlignCenter)
        
        phase_layout.addWidget(phases_frame)
        controller_layout.addWidget(phase_group, 0)
        
        # Current order info
        order_frame = QFrame()
        order_frame.setFrameStyle(QFrame.StyledPanel | QFrame.Sunken)
        order_frame.setStyleSheet("background-color: #2a2a3a; border-radius: 6px;")
        order_layout = QVBoxLayout(order_frame)
        order_layout.setSpacing(8)
        order_layout.setContentsMargins(12, 12, 12, 12)
        
        self.current_order_label = QLabel("Current Order: None")
        self.current_order_label.setFont(QFont("Arial", 10, QFont.Medium))
        self.current_order_label.setAlignment(Qt.AlignCenter)
        order_layout.addWidget(self.current_order_label)
        
        self.cycle_time_label = QLabel("Cycle Time: 0.0s")
        self.cycle_time_label.setFont(QFont("Arial", 9))
        self.cycle_time_label.setAlignment(Qt.AlignCenter)
        order_layout.addWidget(self.cycle_time_label)
        
        controller_layout.addWidget(order_frame, 0)
        
        # KPI metrics
        kpi_frame = QFrame()
        kpi_frame.setFrameStyle(QFrame.StyledPanel | QFrame.Sunken)
        kpi_frame.setStyleSheet("background-color: #2a2a3a; border-radius: 6px;")
        kpi_layout = QGridLayout(kpi_frame)
        kpi_layout.setSpacing(10)
        kpi_layout.setContentsMargins(12, 12, 12, 12)
        
        self.throughput_label = QLabel("Throughput: 0.0/hr")
        self.throughput_label.setFont(QFont("Arial", 10, QFont.Medium))
        self.throughput_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        kpi_layout.addWidget(self.throughput_label, 0, 0)
        
        self.avg_cycle_label = QLabel("Avg Cycle: 0.0s")
        self.avg_cycle_label.setFont(QFont("Arial", 10, QFont.Medium))
        self.avg_cycle_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        kpi_layout.addWidget(self.avg_cycle_label, 0, 1)
        
        self.utilization_label = QLabel("Utilization: 0%")
        self.utilization_label.setFont(QFont("Arial", 10, QFont.Medium))
        self.utilization_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        kpi_layout.addWidget(self.utilization_label, 1, 0)
        
        self.failure_rate_label = QLabel("Failures: 0")
        self.failure_rate_label.setFont(QFont("Arial", 10, QFont.Medium))
        self.failure_rate_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        kpi_layout.addWidget(self.failure_rate_label, 1, 1)
        
        controller_layout.addWidget(kpi_frame, 0)
        
        # Add stretch to push everything up
        controller_layout.addStretch(1)
        
        scada_layout.addWidget(controller_group, 1)  # Give controller more space
        
        # === ACTUATORS PANEL (Right) ===
        actuators_group = QGroupBox("⚙️ ACTUATORS")
        actuators_group.setMinimumWidth(340)
        actuators_group.setMaximumWidth(400)
        actuators_layout = QGridLayout(actuators_group)
        actuators_layout.setSpacing(6)
        actuators_layout.setContentsMargins(12, 15, 12, 12)
        
        # Station actuators
        actuators_layout.addWidget(QLabel("<b>Station Control</b>"), 0, 0, 1, 3)
        
        self.create_actuator_indicator("start_order", "Start Order", actuators_layout, 1)
        self.create_actuator_indicator("reset_station", "Reset Station", actuators_layout, 2)
        
        actuators_layout.addItem(QSpacerItem(10, 10), 3, 0)
        
        # Arm actuators
        actuators_layout.addWidget(QLabel("<b>Arm Actuators</b>"), 4, 0, 1, 3)
        
        self.create_actuator_indicator("picking_actuator", "Picking Arm", actuators_layout, 5)
        self.create_actuator_indicator("kitting_actuator", "Kitting Arm", actuators_layout, 6)
        self.create_actuator_indicator("mounting_actuator", "Mounting Arm", actuators_layout, 7)
        self.create_actuator_indicator("soldering_actuator", "Soldering Arm", actuators_layout, 8)
        
        actuators_layout.addItem(QSpacerItem(10, 10), 9, 0)
        
        # Process actuators
        actuators_layout.addWidget(QLabel("<b>Process Actuators</b>"), 10, 0, 1, 3)
        
        self.create_actuator_indicator("inventory_check", "Inv. Check", actuators_layout, 11)
        self.create_actuator_indicator("output_handling", "Output Handler", actuators_layout, 12)
        self.create_actuator_indicator("reset_components", "Reset Comps.", actuators_layout, 13)
        
        actuators_layout.addItem(QSpacerItem(10, 10), 14, 0)
        
        # Special operations
        actuators_layout.addWidget(QLabel("<b>Special Operations</b>"), 15, 0, 1, 3)
        
        self.create_actuator_indicator("mount_operation", "Mount Assembly", actuators_layout, 16)
        self.create_actuator_indicator("solder_operation", "Solder Joint", actuators_layout, 17)
        
        actuators_layout.setRowStretch(18, 1)
        scada_layout.addWidget(actuators_group)
        
        main_layout.addWidget(scada_panel, 1)
        
        # === STATUS BAR ===
        status_bar = QFrame()
        status_bar.setFrameStyle(QFrame.StyledPanel | QFrame.Sunken)
        status_bar.setStyleSheet("background-color: #2a2a3a; border-radius: 4px;")
        status_layout = QHBoxLayout(status_bar)
        status_layout.setContentsMargins(10, 6, 10, 6)
        
        self.status_label = QLabel("Status: Ready")
        self.status_label.setFont(QFont("Arial", 9))
        self.status_label.setStyleSheet("color: #aaa; font-style: italic;")
        status_layout.addWidget(self.status_label, 0, Qt.AlignLeft)
        
        self.sim_time_label = QLabel("Sim Time: 0.0s")
        self.sim_time_label.setFont(QFont("Arial", 9))
        status_layout.addWidget(self.sim_time_label, 0, Qt.AlignLeft)
        
        status_layout.addStretch(1)
        
        self.update_btn = QPushButton("🔄 Update")
        self.update_btn.setFont(QFont("Arial", 9))
        self.update_btn.clicked.connect(self.update_from_live_station)
        self.update_btn.setFixedWidth(100)
        status_layout.addWidget(self.update_btn, 0, Qt.AlignRight)
        
        main_layout.addWidget(status_bar, 0)
    
    def create_sensor_indicator(self, name, label, layout, row):
        """Create a sensor indicator row"""
        # Label
        label_widget = QLabel(label)
        label_widget.setFont(QFont("Arial", 9))
        label_widget.setMinimumWidth(120)
        layout.addWidget(label_widget, row, 0)
        
        # Value display
        value_label = QLabel("--")
        value_label.setMinimumWidth(100)
        value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        value_label.setFont(QFont("Courier", 9, QFont.Medium))
        layout.addWidget(value_label, row, 1)
        
        # Lamp
        lamp = LampIndicator()
        lamp.set_state(False)
        layout.addWidget(lamp, row, 2, Qt.AlignCenter)
        
        # Store reference
        self.sensor_indicators[name] = {
            "label": label_widget,
            "value": value_label,
            "lamp": lamp
        }
    
    def create_actuator_indicator(self, name, label, layout, row):
        """Create an actuator indicator row"""
        # Label
        label_widget = QLabel(label)
        label_widget.setFont(QFont("Arial", 9))
        label_widget.setMinimumWidth(120)
        layout.addWidget(label_widget, row, 0)
        
        # Status display
        status_label = QLabel("OFF")
        status_label.setMinimumWidth(100)
        status_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        status_label.setFont(QFont("Courier", 9, QFont.Medium))
        layout.addWidget(status_label, row, 1)
        
        # Lamp
        lamp = LampIndicator()
        lamp.set_state(False)
        layout.addWidget(lamp, row, 2, Qt.AlignCenter)
        
        # Store reference
        self.actuator_indicators[name] = {
            "label": label_widget,
            "status": status_label,
            "lamp": lamp
        }
    
    def setup_styling(self):
        """Setup industrial SCADA styling"""
        # Set background
        palette = self.palette()
        palette.setColor(QPalette.Window, QColor(40, 40, 48))
        palette.setColor(QPalette.WindowText, QColor(220, 220, 220))
        palette.setColor(QPalette.Base, QColor(30, 30, 38))
        palette.setColor(QPalette.AlternateBase, QColor(50, 50, 58))
        self.setPalette(palette)
        
        # Style update button
        self.update_btn.setStyleSheet("""
            QPushButton {
                background-color: #3a3a4a;
                color: white;
                border: 1px solid #555;
                border-radius: 4px;
                padding: 5px 10px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #4a4a5a;
                border: 1px solid #666;
            }
            QPushButton:pressed {
                background-color: #2a2a3a;
            }
        """)
        
        # Style group boxes
        group_style = """
            QGroupBox {
                font-weight: bold;
                border: 2px solid #555;
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 12px;
                background-color: #353545;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 8px 0 8px;
                color: #ffffff;
                font-size: 11px;
            }
        """
        self.setStyleSheet(group_style)
    
    def connect_to_station(self, station):
        """Connect SCADA to a station instance"""
        self.station = station
        
        # Start update timer
        self.update_timer.start(self.update_interval)
        
        # Update once to show initial state
        self.update_from_live_station()
        
        self.status_label.setText(f"Status: Connected to station")
    
    def set_simulation_manager(self, sim_manager):
        """Set reference to simulation manager"""
        self.sim_manager = sim_manager
    
    def update_from_live_station(self):
        """Update SCADA from live simulation data"""
        if not self.station:
            return
        
        try:
            # Update from sensors
            self.update_sensors_panel()
            
            # Update controller section
            self.update_controller_panel()
            
            # Update actuators panel
            self.update_actuators_panel()
            
            # Update status
            if hasattr(self.station, 'env'):
                self.sim_time_label.setText(f"Sim Time: {self.station.env.now:.1f}s")
            
            if hasattr(self.station, 'plc_controller'):
                state = self.station.plc_controller.state
                self.status_label.setText(f"Status: {state}")
            
        except Exception as e:
            self.status_label.setText(f"Error: {str(e)[:50]}")
    
    def update_sensors_panel(self):
        """Update sensors panel from station sensors"""
        if not hasattr(self.station, 'sensors'):
            return
        
        sensors = self.station.sensors
        
        # Station sensors
        station_state = sensors.get('station_state', None)
        if station_state and hasattr(station_state, 'read'):
            state = station_state.read()
            self.update_sensor_indicator('station_state', str(state), state != "IDLE")
        
        # Order queue sensor
        order_sensor = sensors.get('order', None)
        if order_sensor and hasattr(order_sensor, 'get_queue_size'):
            queued = order_sensor.get_queue_size()
            self.update_sensor_indicator('queued_orders', str(queued), queued > 0)
        
        # WIP count
        wip = getattr(self.station, 'wip_count', 0)
        self.update_sensor_indicator('wip_count', str(wip), wip > 0)
        
        # Completion sensor
        completion_sensor = sensors.get('completion', None)
        if completion_sensor and hasattr(completion_sensor, 'read'):
            completed = completion_sensor.read()
            self.update_sensor_indicator('completed_count', str(completed), completed > 0)
        
        # Arm sensors
        for arm_name in ['picking_arm', 'kitting_arm', 'mounting_arm', 'soldering_arm']:
            if arm_name in sensors:
                arm_sensor = sensors[arm_name]
                if hasattr(arm_sensor, 'read'):
                    state = arm_sensor.read()
                    operations = 0
                    if hasattr(arm_sensor, 'get_operations_count'):
                        operations = arm_sensor.get_operations_count()
                    self.update_sensor_indicator(
                        arm_name, 
                        f"{state} ({operations})", 
                        state != "IDLE" and state != "UNKNOWN"
                    )
        
        # Component sensors
        inventory_sensor = sensors.get('inventory', None)
        if inventory_sensor and hasattr(inventory_sensor, 'read'):
            inventory_state = inventory_sensor.read()
            self.update_sensor_indicator(
                'inventory', 
                inventory_state, 
                inventory_state not in ["READY", "UNKNOWN"]
            )
        
        output_sensor = sensors.get('output', None)
        if output_sensor and hasattr(output_sensor, 'read'):
            output_state = output_sensor.read()
            self.update_sensor_indicator(
                'output', 
                output_state, 
                output_state not in ["READY", "UNKNOWN"]
            )
        
        # Failure sensor
        failure_sensor = sensors.get('failure', None)
        if failure_sensor and hasattr(failure_sensor, 'get_total_failures'):
            failures = failure_sensor.get_total_failures()
            self.update_sensor_indicator('failure', f"{failures}", failures > 0, "error")
        
        # Total failures
        total_failures = getattr(self.station, 'total_failures', 0)
        self.update_sensor_indicator('total_failures', str(total_failures), 
                                   total_failures > 0, "error")
    
    def update_sensor_indicator(self, name, value, active, severity="normal"):
        """Update individual sensor indicator"""
        if name in self.sensor_indicators:
            indicator = self.sensor_indicators[name]
            indicator['value'].setText(str(value))
            indicator['lamp'].set_state(active, severity)
            
            # Highlight label if active
            color = {
                "normal": "#4CAF50",
                "warning": "#FFC107",
                "error": "#F44336"
            }.get(severity, "#4CAF50")
            
            if active:
                indicator['label'].setStyleSheet(f"color: {color}; font-weight: bold;")
                indicator['value'].setStyleSheet(f"color: {color}; font-weight: bold;")
            else:
                indicator['label'].setStyleSheet("color: #cccccc;")
                indicator['value'].setStyleSheet("color: #aaaaaa;")
    
    def update_controller_panel(self):
        """Update controller panel from PLC state"""
        if not hasattr(self.station, 'plc_controller'):
            return
        
        plc = self.station.plc_controller
        state = plc.state
        
        # Update PLC state label with color coding
        color_map = {
            "IDLE": "#4CAF50",           # Green
            "PROCESSING": "#FFC107",     # Yellow
            "CHECKING_INVENTORY": "#2196F3",  # Blue
            "ROBOTIC_PICKING": "#FF9800",     # Orange
            "ROBOTIC_KITTING": "#FF9800",
            "ROBOTIC_MOUNTING": "#FF9800",
            "ROBOTIC_SOLDERING": "#FF9800",
            "HANDLING_OUTPUT": "#9C27B0",     # Purple
            "RESETTING": "#00BCD4",      # Cyan
            "ERROR": "#F44336",          # Red
            "EMERGENCY_STOP": "#D32F2F"  # Dark Red
        }
        
        color = color_map.get(state, "#607D8B")
        self.plc_state_label.setText(f"PLC State: {state}")
        self.plc_state_label.setStyleSheet(f"""
            QLabel {{
                color: {color};
                background-color: #2a2a35;
                border: 2px solid {color};
                border-radius: 6px;
                padding: 8px;
            }}
        """)
        
        # Update phase lamps based on PLC state
        self.update_phase_lamps(state)
        
        # Update current order info
        if hasattr(plc, 'current_order') and plc.current_order:
            order = plc.current_order
            self.current_order_label.setText(f"Order #{order.order_id} ({order.model_type})")
            if hasattr(order, 'cycle_time') and order.cycle_time:
                self.cycle_time_label.setText(f"Cycle: {order.cycle_time:.1f}s")
        else:
            self.current_order_label.setText("Current Order: None")
            self.cycle_time_label.setText("Cycle Time: 0.0s")
        
        # Update KPI metrics
        if hasattr(self.station.sensors, 'kpi'):
            kpi = self.station.sensors['kpi']
            throughput = kpi.get_throughput() if hasattr(kpi, 'get_throughput') else 0.0
            avg_cycle = kpi.get_avg_cycle_time() if hasattr(kpi, 'get_avg_cycle_time') else 0.0
            
            self.throughput_label.setText(f"Throughput: {throughput:.1f}/hr")
            self.avg_cycle_label.setText(f"Avg Cycle: {avg_cycle:.1f}s")
            
            # Calculate utilization
            total_util = 0
            count = 0
            for arm_name in ['picking_arm', 'kitting_arm', 'mounting_arm', 'soldering_arm']:
                if arm_name in self.station.sensors:
                    arm_sensor = self.station.sensors[arm_name]
                    if hasattr(arm_sensor, 'get_utilization'):
                        util = arm_sensor.get_utilization()
                        total_util += util
                        count += 1
            
            avg_util = total_util / count if count > 0 else 0
            self.utilization_label.setText(f"Utilization: {avg_util:.0f}%")
            
            failures = getattr(self.station, 'total_failures', 0)
            self.failure_rate_label.setText(f"Failures: {failures}")
    
    def update_phase_lamps(self, plc_state):
        """Update phase lamps based on PLC state"""
        # Define phase sequence
        phase_sequence = ["Order", "Inventory", "Picking", "Kitting", "Mounting", "Soldering", "Output"]
        
        # Reset all lamps to inactive
        for phase in phase_sequence:
            if phase in self.phase_lamps:
                self.phase_lamps[phase].set_state(False, False)
        
        # Map PLC state to current phase
        state_to_phase = {
            "CHECKING_INVENTORY": "Inventory",
            "ROBOTIC_PICKING": "Picking",
            "ROBOTIC_KITTING": "Kitting",
            "ROBOTIC_MOUNTING": "Mounting",
            "ROBOTIC_SOLDERING": "Soldering",
            "HANDLING_OUTPUT": "Output"
        }
        
        current_phase = state_to_phase.get(plc_state)
        
        # If we're not IDLE, Order phase is always completed
        if plc_state != "IDLE" and "Order" in self.phase_lamps:
            self.phase_lamps["Order"].set_state(True, False)
        
        # Activate phases up to and including current phase
        if current_phase:
            for i, phase in enumerate(phase_sequence):
                if phase in self.phase_lamps:
                    is_current = (phase == current_phase)
                    phase_index = phase_sequence.index(phase)
                    current_index = phase_sequence.index(current_phase)
                    is_active = (phase_index <= current_index)
                    
                    if is_active:
                        self.phase_lamps[phase].set_state(True, is_current)
    
    def update_actuators_panel(self):
        """Update actuators panel based on current state"""
        if not hasattr(self.station, 'plc_controller'):
            return
        
        plc = self.station.plc_controller
        state = plc.state
        
        # Determine which actuators are active based on PLC state
        actuators_active = {
            "start_order": state == "IDLE" and hasattr(plc, 'orders_in') and len(getattr(plc.orders_in, 'items', [])) > 0,
            "reset_station": state in ["RESETTING", "ERROR", "EMERGENCY_STOP"],
            "picking_actuator": state == "ROBOTIC_PICKING",
            "kitting_actuator": state == "ROBOTIC_KITTING",
            "mounting_actuator": state == "ROBOTIC_MOUNTING",
            "soldering_actuator": state == "ROBOTIC_SOLDERING",
            "inventory_check": state == "CHECKING_INVENTORY",
            "output_handling": state == "HANDLING_OUTPUT",
            "reset_components": state == "RESETTING",
            "mount_operation": state == "ROBOTIC_MOUNTING" and hasattr(self.station, 'mounting_arm') and self.station.mounting_arm.state == "WORKING",
            "solder_operation": state == "ROBOTIC_SOLDERING" and hasattr(self.station, 'soldering_arm') and self.station.soldering_arm.state == "WORKING"
        }
        
        # Update actuator indicators
        for actuator_name, active in actuators_active.items():
            if actuator_name in self.actuator_indicators:
                indicator = self.actuator_indicators[actuator_name]
                indicator['status'].setText("ON" if active else "OFF")
                indicator['lamp'].set_state(active, "normal" if active else "normal")
                
                # Highlight label if active
                if active:
                    indicator['label'].setStyleSheet("color: #4CAF50; font-weight: bold;")
                    indicator['status'].setStyleSheet("color: #4CAF50; font-weight: bold;")
                else:
                    indicator['label'].setStyleSheet("color: #cccccc;")
                    indicator['status'].setStyleSheet("color: #aaaaaa;")
    
    def disconnect(self):
        """Disconnect from station"""
        self.update_timer.stop()
        self.station = None
        
        # Reset all indicators
        for indicator in self.sensor_indicators.values():
            indicator['value'].setText("--")
            indicator['lamp'].set_state(False)
            indicator['label'].setStyleSheet("color: #cccccc;")
            indicator['value'].setStyleSheet("color: #aaaaaa;")
        
        for indicator in self.actuator_indicators.values():
            indicator['status'].setText("OFF")
            indicator['lamp'].set_state(False)
            indicator['label'].setStyleSheet("color: #cccccc;")
            indicator['status'].setStyleSheet("color: #aaaaaa;")
        
        # Reset phase lamps
        for phase in self.phase_lamps.values():
            phase.set_state(False, False)
        
        self.plc_state_label.setText("PLC State: DISCONNECTED")
        self.plc_state_label.setStyleSheet("""
            QLabel {
                color: #607D8B;
                background-color: #2a2a35;
                border: 2px solid #607D8B;
                border-radius: 6px;
                padding: 8px;
            }
        """)
        self.current_order_label.setText("Current Order: None")
        self.cycle_time_label.setText("Cycle Time: 0.0s")
        self.throughput_label.setText("Throughput: 0.0/hr")
        self.avg_cycle_label.setText("Avg Cycle: 0.0s")
        self.utilization_label.setText("Utilization: 0%")
        self.failure_rate_label.setText("Failures: 0")
        self.status_label.setText("Status: Disconnected")
        self.sim_time_label.setText("Sim Time: 0.0s")


# ============================================================================
# Mock Classes for Testing (Fixed lambdas)
# ============================================================================

class MockSensor:
    def __init__(self, name, initial_value="READY"):
        self.name = name
        self._value = initial_value
        self._operations = 0
    
    def read(self):
        return self._value
    
    def get_operations_count(self):
        return self._operations
    
    def get_utilization(self):
        return 75.5  # Mock utilization
    
    def set_value(self, value):
        self._value = value
    
    def increment_operations(self):
        self._operations += 1


class MockOrderSensor:
    def get_queue_size(self):
        return 5
    
    def read(self):
        return True


class MockCompletionSensor:
    def read(self):
        return 20


class MockKPISensor:
    def get_throughput(self):
        return 12.5
    
    def get_avg_cycle_time(self):
        return 45.2
    
    def get_total_orders(self):
        return 25
    
    def get_completed_count(self):
        return 20


class MockFailureSensor:
    def get_total_failures(self):
        return 2
    
    def get_failure_logs(self):
        return []


class MockPLC:
    def __init__(self):
        self.state = "IDLE"
        self.current_order = None
        self.orders_in = type('obj', (object,), {'items': [1, 2, 3]})()
        self.mounting_arm = type('obj', (object,), {'state': 'IDLE'})()
        self.soldering_arm = type('obj', (object,), {'state': 'IDLE'})()


class MockStation:
    def __init__(self):
        self.sensors = {
            'station_state': MockSensor('station_state', 'IDLE'),
            'order': MockOrderSensor(),
            'completion': MockCompletionSensor(),
            'picking_arm': MockSensor('picking_arm', 'IDLE'),
            'kitting_arm': MockSensor('kitting_arm', 'IDLE'),
            'mounting_arm': MockSensor('mounting_arm', 'IDLE'),
            'soldering_arm': MockSensor('soldering_arm', 'IDLE'),
            'inventory': MockSensor('inventory', 'READY'),
            'output': MockSensor('output', 'READY'),
            'failure': MockFailureSensor(),
            'kpi': MockKPISensor()
        }
        
        self.plc_controller = MockPLC()
        self.wip_count = 3
        self.total_failures = 2
        self.env = type('obj', (object,), {'now': 125.5})()
        self.mounting_arm = type('obj', (object,), {'state': 'IDLE'})()
        self.soldering_arm = type('obj', (object,), {'state': 'IDLE'})()


# ============================================================================
# Demo Application
# ============================================================================

class DemoWindow(QMainWindow):
    """Demo window to test SCADA dashboard standalone"""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SCADA Dashboard Demo - Kitting Station 1")
        self.setGeometry(100, 100, 1400, 800)
        
        # Create mock station
        self.mock_station = MockStation()
        
        # Create SCADA widget
        self.scada_widget = ScadaDashboardWidget()
        self.scada_widget.connect_to_station(self.mock_station)
        
        # Control panel for testing
        control_widget = QWidget()
        control_widget.setMaximumWidth(300)
        control_layout = QVBoxLayout(control_widget)
        control_layout.setContentsMargins(15, 15, 15, 15)
        control_layout.setSpacing(15)
        
        # Title
        title = QLabel("📊 SCADA Test Controls")
        title.setFont(QFont("Arial", 12, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        control_layout.addWidget(title)
        
        # PLC State selector
        state_label = QLabel("Change PLC State:")
        state_label.setFont(QFont("Arial", 10))
        control_layout.addWidget(state_label)
        
        self.state_combo = QComboBox()
        self.state_combo.addItems([
            "IDLE", "PROCESSING", "CHECKING_INVENTORY", 
            "ROBOTIC_PICKING", "ROBOTIC_KITTING", "ROBOTIC_MOUNTING",
            "ROBOTIC_SOLDERING", "HANDLING_OUTPUT", "RESETTING", "ERROR"
        ])
        self.state_combo.currentTextChanged.connect(self.change_plc_state)
        control_layout.addWidget(self.state_combo)
        
        # Arm state controls
        arm_label = QLabel("Change Arm States:")
        arm_label.setFont(QFont("Arial", 10))
        control_layout.addWidget(arm_label)
        
        self.arm_state_combo = QComboBox()
        self.arm_state_combo.addItems(["IDLE", "MOVING", "WORKING", "FAILED"])
        self.arm_state_combo.setCurrentText("IDLE")
        self.arm_state_combo.currentTextChanged.connect(self.change_arm_states)
        control_layout.addWidget(self.arm_state_combo)
        
        # Sim time control
        time_label = QLabel("Simulation Time:")
        time_label.setFont(QFont("Arial", 10))
        control_layout.addWidget(time_label)
        
        self.time_slider_label = QLabel("125.5s")
        self.time_slider_label.setAlignment(Qt.AlignCenter)
        control_layout.addWidget(self.time_slider_label)
        
        # Add some spacing
        control_layout.addSpacing(20)
        
        # Update button
        self.update_btn = QPushButton("🔄 Update SCADA")
        self.update_btn.clicked.connect(self.scada_widget.update_from_live_station)
        control_layout.addWidget(self.update_btn)
        
        # Disconnect button
        disconnect_btn = QPushButton("🔌 Disconnect")
        disconnect_btn.clicked.connect(self.scada_widget.disconnect)
        control_layout.addWidget(disconnect_btn)
        
        # Reconnect button
        reconnect_btn = QPushButton("🔗 Reconnect")
        reconnect_btn.clicked.connect(self.reconnect_station)
        control_layout.addWidget(reconnect_btn)
        
        # Status info
        control_layout.addSpacing(30)
        info_label = QLabel("Demo Features:")
        info_label.setFont(QFont("Arial", 10, QFont.Bold))
        control_layout.addWidget(info_label)
        
        info_text = QLabel(
            "• Live sensor/actuator status\n"
            "• Phase timeline visualization\n"
            "• Real-time KPI updates\n"
            "• Lamp indicators with animation\n"
            "• Industrial SCADA theme"
        )
        info_text.setFont(QFont("Arial", 9))
        info_text.setWordWrap(True)
        control_layout.addWidget(info_text)
        
        control_layout.addStretch()
        
        # Main layout
        main_widget = QWidget()
        main_layout = QHBoxLayout(main_widget)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.addWidget(self.scada_widget, 1)
        main_layout.addWidget(control_widget, 0)
        
        self.setCentralWidget(main_widget)
        
        # Apply dark theme
        self.apply_dark_theme()
        
        # Initial update
        QTimer.singleShot(100, lambda: self.change_plc_state("ROBOTIC_PICKING"))
    
    def change_plc_state(self, state):
        """Change PLC state for testing"""
        self.mock_station.plc_controller.state = state
        self.scada_widget.update_from_live_station()
    
    def change_arm_states(self, state):
        """Change all arm states for testing"""
        for arm in ['picking_arm', 'kitting_arm', 'mounting_arm', 'soldering_arm']:
            self.mock_station.sensors[arm].set_value(state)
            if state == "WORKING":
                self.mock_station.sensors[arm].increment_operations()
        
        # Update specific arms in station
        self.mock_station.mounting_arm.state = state
        self.mock_station.soldering_arm.state = state
        
        self.scada_widget.update_from_live_station()
    
    def reconnect_station(self):
        """Reconnect to mock station"""
        self.scada_widget.connect_to_station(self.mock_station)
    
    def apply_dark_theme(self):
        """Apply dark theme to the window"""
        palette = self.palette()
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


# ============================================================================
# Main Entry Point for Demo
# ============================================================================

if __name__ == "__main__":
    import sys
    
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    
    demo_window = DemoWindow()
    demo_window.show()
    
    sys.exit(app.exec_())