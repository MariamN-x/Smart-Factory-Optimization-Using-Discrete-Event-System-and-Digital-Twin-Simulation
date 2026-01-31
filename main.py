import simpy
import random
import math
import time
import sys
import json
import csv
import os
import threading
from datetime import datetime
from PyQt5.QtWidgets import (
    QApplication, QAction, QMessageBox, QToolBar, 
    QWidget, QVBoxLayout, QLabel, QHBoxLayout,
    QPushButton, QSlider, QDialog, QTextEdit,
    QGridLayout, QGroupBox, QProgressBar,
    QCheckBox, QListWidget, QListWidgetItem   # ← ADD THESE 3
)

from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtGui import QColor, QBrush, QFont, QPalette

# Import the new OOP modules
from station1_plc import PLCController
from station1_sensors import (StationStateSensor, OrderSensor, CompletionSensor, 
                              ArmStateSensor, InventoryStatusSensor, OutputStatusSensor,
                              KPISensor, FailureSensor)
from station1_actuators import (StationActuators, ArmActuator, ProcessActuators)

# =====================================================
# ----------- Existing Data Structures ----------------
# =====================================================

class RoboticArm2Axis:
    """2-axis robotic arm with simplified movement and utilization tracking"""
    def __init__(self, env, name, role, home_position=[0, 0], logger=None):
        self.env = env
        self.name = name
        self.role = role  # "PICKING", "KITTING", "MOUNTING", "SOLDERING"
        self.home_position = home_position
        self.current_position = home_position.copy()
        self.target_position = home_position.copy()
        self.state = "IDLE"
        self.gripper_state = "OPEN"
        self.completed_operations = 0
        self.failure_count = 0
        self.failure_log = []

        # Logger (station)
        self.logger = logger
        
        # Utilization tracking
        self.busy_time = 0.0
        self.idle_time = 0.0
        self.last_state_change_time = env.now
        self.state_history = []
        
        # Arm-specific parameters
        self.base_move_time = 0.3  # Base movement time in seconds
        self.speed_factor = 0.002  # Time per unit distance
        self.failure_probability = 0.02  # 2% chance of failure per operation
        
        # Role-specific operation times
        self.operation_times = {
            "PICKING": {"pick": 0.4, "place": 0.3},
            "KITTING": {"pick": 0.3, "place": 0.4},
            "MOUNTING": {"pick": 0.5, "place": 0.6, "mount": 1.2},
            "SOLDERING": {"pick": 0.4, "place": 0.3, "solder": 1.0}
        }
        
        print(f"🤖 {self.name} ({self.role}) initialized at {home_position}")

    def _update_utilization(self, new_state):
        """Update utilization metrics when state changes"""
        current_time = self.env.now
        time_in_previous_state = current_time - self.last_state_change_time
        prev_state = self.state
        
        # Record state transition
        self.state_history.append({
            "time": current_time,
            "previous_state": prev_state,
            "new_state": new_state,
            "duration": time_in_previous_state
        })
        
        # Update busy/idle time
        if self.state in ["MOVING", "WORKING"]:
            self.busy_time += time_in_previous_state
        elif self.state == "IDLE":
            self.idle_time += time_in_previous_state

        # Log state change
        if self.logger is not None:
            self.logger.log_event(
                category="ARM",
                event_type="STATE_CHANGE",
                data={
                    "arm_name": self.name,
                    "role": self.role,
                    "previous_state": prev_state,
                    "new_state": new_state,
                    "duration": time_in_previous_state,
                    "x": self.current_position[0],
                    "y": self.current_position[1],
                    "busy_time": self.busy_time,
                    "idle_time": self.idle_time,
                    "completed_operations": self.completed_operations,
                    "failure_count": self.failure_count,
                }
            )
        
        self.state = new_state
        self.last_state_change_time = current_time

    def get_utilization(self):
        """Calculate current utilization percentage"""
        total_time = self.busy_time + self.idle_time
        if total_time == 0:
            return 0.0
        return (self.busy_time / total_time) * 100

    def move_to(self, target_x, target_y):
        """Move to target 2D position"""
        return self.env.process(self._perform_move(target_x, target_y))
    
    def pick_component(self):
        """Pick component operation"""
        return self.env.process(self._perform_pick())
    
    def place_component(self):
        """Place component operation"""
        return self.env.process(self._perform_place())
    
    def mount_assembly(self):
        """Mount assembly operation (for mounting arm)"""
        return self.env.process(self._perform_mount())
    
    def solder_joint(self):
        """Solder joint operation (for soldering arm)"""
        return self.env.process(self._perform_solder())
    
    def _perform_move(self, target_x, target_y):
        """Simulate 2D movement to target coordinates"""
        if self.state == "FAILED":
            print(f"⚠️ {self.env.now:.1f}s: {self.name} cannot move - ARM FAILED")
            return
            
        self._update_utilization("MOVING")
        self.target_position = [target_x, target_y]
        
        # Calculate movement time based on Euclidean distance
        distance = math.sqrt(
            (target_x - self.current_position[0])**2 + 
            (target_y - self.current_position[1])**2
        )
        move_time = self.base_move_time + (distance * self.speed_factor)
        
        print(f"  📍 {self.name} moving to [{target_x}, {target_y}] (distance: {distance:.1f}, time: {move_time:.1f}s)")
        yield self.env.timeout(move_time)
        
        self.current_position = [target_x, target_y]
        self._update_utilization("IDLE")

    def _perform_pick(self):
        """Perform pick operation with failure checking"""
        if self.state == "FAILED":
            return
            
        if random.random() < self.failure_probability:
            yield self.env.process(self._handle_failure("PICK"))
            return
            
        self._update_utilization("WORKING")
        print(f"  ✋ {self.name} picking component")
        
        # Close gripper
        self.gripper_state = "CLOSING"
        yield self.env.timeout(0.1)
        self.gripper_state = "CLOSED"
        
        # Pick operation time
        pick_time = self.operation_times[self.role]["pick"]
        yield self.env.timeout(pick_time)
        
        self.completed_operations += 1
        self._update_utilization("IDLE")

    def _perform_place(self):
        """Perform place operation with failure checking"""
        if self.state == "FAILED":
            return
            
        if random.random() < self.failure_probability:
            yield self.env.process(self._handle_failure("PLACE"))
            return
            
        self._update_utilization("WORKING")
        print(f"  📍 {self.name} placing component")
        
        # Place operation time
        place_time = self.operation_times[self.role]["place"]
        yield self.env.timeout(place_time)
        
        # Open gripper
        self.gripper_state = "OPENING"
        yield self.env.timeout(0.1)
        self.gripper_state = "OPEN"
        
        self.completed_operations += 1
        self._update_utilization("IDLE")

    def _perform_mount(self):
        """Perform mount operation (mounting arm only)"""
        if self.state == "FAILED":
            return
            
        if random.random() < self.failure_probability:
            yield self.env.process(self._handle_failure("MOUNT"))
            return
            
        self._update_utilization("WORKING")
        print(f"  🔧 {self.name} mounting assembly")
        
        mount_time = self.operation_times[self.role]["mount"]
        yield self.env.timeout(mount_time)
        
        self.completed_operations += 1
        self._update_utilization("IDLE")

    def _perform_solder(self):
        """Perform solder operation (soldering arm only)"""
        if self.state == "FAILED":
            return
            
        if random.random() < self.failure_probability:
            yield self.env.process(self._handle_failure("SOLDER"))
            return
            
        self._update_utilization("WORKING")
        print(f"  🔌 {self.name} soldering joint")
        
        solder_time = self.operation_times[self.role]["solder"]
        yield self.env.timeout(solder_time)
        
        self.completed_operations += 1
        self._update_utilization("IDLE")

    def _handle_failure(self, operation_type):
        """Handle arm failure and initiate repair process"""
        self._update_utilization("FAILED")
        self.failure_count += 1
        failure_record = {
            "time": self.env.now,
            "arm": self.name,
            "operation": operation_type,
            "position": self.current_position.copy(),
            "failure_id": self.failure_count
        }
        self.failure_log.append(failure_record)

        if self.logger is not None:
            self.logger.log_event(
                category="ARM",
                event_type="FAILURE",
                data={
                    "arm_name": self.name,
                    "role": self.role,
                    "operation": operation_type,
                    "failure_id": self.failure_count,
                    "x": self.current_position[0],
                    "y": self.current_position[1],
                }
            )
        
        print(f"🚨 {self.env.now:.1f}s: {self.name} FAILED during {operation_type}!")
        print(f"    Failure #{self.failure_count} at position {self.current_position}")
        
        # Start repair process
        yield self.env.process(self._perform_repair())

    def _perform_repair(self):
        """Perform repair process"""
        repair_time = random.uniform(3.0, 8.0)  # 3-8 seconds repair time
        print(f"🔧 {self.env.now:.1f}s: {self.name} repair started (est: {repair_time:.1f}s)")
        
        yield self.env.timeout(repair_time)
        
        self._update_utilization("IDLE")
        print(f"✅ {self.env.now:.1f}s: {self.name} repair completed, resuming operations")

    def return_home(self):
        """Return arm to home position"""
        return self.env.process(self._perform_move(
            self.home_position[0], 
            self.home_position[1]
        ))


class Order:
    """Represents a manufacturing order with tracking"""
    def __init__(self, order_id, model_type, creation_time):
        self.order_id = order_id
        self.model_type = model_type
        self.creation_time = creation_time
        self.start_time = None
        self.completion_time = None
        self.cycle_time = None
        self.status = "CREATED"
        
    def start_processing(self, start_time):
        self.start_time = start_time
        self.status = "PROCESSING"
        
    def complete(self, completion_time):
        self.completion_time = completion_time
        self.cycle_time = completion_time - self.creation_time
        self.status = "COMPLETED"
        
    def to_dict(self):
        return {
            "order_id": self.order_id,
            "model_type": self.model_type,
            "creation_time": self.creation_time,
            "start_time": self.start_time,
            "completion_time": self.completion_time,
            "cycle_time": self.cycle_time,
            "status": self.status
        }


class OrderGenerator:
    """Generates new orders for 3D printers with DES queue integration"""
    def __init__(self, env, name, orders_store, logger=None):
        self.env = env
        self.name = name
        self.orders_store = orders_store
        self.order_counter = 0
        self.models = ["Pro X1", "Standard S2", "Mini M1"]
        self.logger = logger

    def generate_orders(self):
        while True:
            # Simulate order arrival every 5-15 minutes
            if random.random() < 0.05:
                self.order_counter += 1
                model = random.choice(self.models)
                order = Order(self.order_counter, model, self.env.now)
                
                # Put order into the DES queue
                yield self.orders_store.put(order)
                print(f"📋 {self.env.now:.1f}s: Order #{self.order_counter} for {model} added to queue")

                if self.logger is not None:
                    self.logger.log_event(
                        category="ORDER",
                        event_type="CREATED",
                        data={
                            "order_id": order.order_id,
                            "model_type": order.model_type,
                            "creation_time": order.creation_time,
                        }
                    )
            
            yield self.env.timeout(1.0)


class InventoryCheck:
    """Checks inventory for required parts"""
    def __init__(self, env, name, logger=None):
        self.env = env
        self.name = name
        self.state = "READY"
        self.display_state = "READY"
        self.inventory = {
            "linear_rail": 50,
            "stepper_motor": 40, 
            "mainboard": 35,
            "chassis_plate": 45,
            "quick_connect": 60
        }
        self.input_signal = None
        self.logger = logger

    def execute_command(self, command):
        return self.env.process(self._process_command(command))

    def _process_command(self, command):
        if command == "CHECK_INVENTORY":
            print(f"📦 {self.env.now:.1f}s: {self.name} checking inventory...")
            self.state = "CHECKING"
            self.display_state = "CHECKING"
            if self.logger is not None:
                self.logger.log_event(
                    category="INVENTORY",
                    event_type="STATE_CHANGE",
                    data={"state": self.state}
                )
            yield self.env.timeout(1.0)  # Checking process
            
            # Simulate inventory check
            if random.random() > 0.1:  # 90% success rate
                self.state = "PARTS_AVAILABLE"
                self.display_state = "PARTS_AVAILABLE"
                print(f"✅ {self.env.now:.1f}s: {self.name} parts available")
            else:
                self.state = "PARTS_MISSING"
                self.display_state = "PARTS_MISSING"
                print(f"❌ {self.env.now:.1f}s: {self.name} some parts missing")

            if self.logger is not None:
                self.logger.log_event(
                    category="INVENTORY",
                    event_type="STATE_CHANGE",
                    data={"state": self.state}
                )
                
        elif command == "RESET":
            self.state = "READY"
            self.display_state = "READY"
            if self.logger is not None:
                self.logger.log_event(
                    category="INVENTORY",
                    event_type="STATE_CHANGE",
                    data={"state": self.state}
                )
            print(f"🔄 {self.env.now:.1f}s: {self.name} reset to ready")


class OutputHandler:
    """Handles completed sub-assemblies"""
    def __init__(self, env, name, logger=None):
        self.env = env
        self.name = name
        self.state = "READY"
        self.display_state = "READY"
        self.completed_count = 0
        self.input_signal = None
        self.logger = logger

    def execute_command(self, command):
        return self.env.process(self._process_command(command))

    def _process_command(self, command):
        if command == "HANDLE_OUTPUT":
            print(f"📤 {self.env.now:.1f}s: {self.name} handling output...")
            self.state = "PROCESSING"
            self.display_state = "PROCESSING"
            if self.logger is not None:
                self.logger.log_event(
                    category="OUTPUT",
                    event_type="STATE_CHANGE",
                    data={"state": self.state, "completed_count": self.completed_count}
                )
            yield self.env.timeout(0.5)  # Output handling
            self.completed_count += 1
            self.state = "OUTPUT_COMPLETE"
            self.display_state = f"OUTPUT_COMPLETE ({self.completed_count})"
            if self.logger is not None:
                self.logger.log_event(
                    category="OUTPUT",
                    event_type="STATE_CHANGE",
                    data={"state": self.state, "completed_count": self.completed_count}
                )
            print(f"✅ {self.env.now:.1f}s: {self.name} output #{self.completed_count} complete")
        elif command == "RESET":
            self.state = "READY"
            self.display_state = "READY"
            if self.logger is not None:
                self.logger.log_event(
                    category="OUTPUT",
                    event_type="STATE_CHANGE",
                    data={"state": self.state, "completed_count": self.completed_count}
                )
            print(f"🔄 {self.env.now:.1f}s: {self.name} reset to ready")


# =====================================================
# ----------- Updated Kitting Station -----------------
# =====================================================

class KittingStation:
    """Main kitting station using the new PLC controller architecture"""
    def __init__(self, env):
        self.env = env

        # Central event log (in memory)
        self.event_log = []

        # create per-run streaming log file on disk
        self.log_filename = None
        self._init_log_file()
        
        # Create PLC Controller
        self.plc_controller = PLCController(env)
        
        # Create robotic arms (with logger)
        self.picking_arm = RoboticArm2Axis(env, "PickingArm", "PICKING", [0, 0], logger=self)
        self.kitting_arm = RoboticArm2Axis(env, "KittingArm", "KITTING", [100, 0], logger=self)
        self.mounting_arm = RoboticArm2Axis(env, "MountingArm", "MOUNTING", [200, 0], logger=self)
        self.soldering_arm = RoboticArm2Axis(env, "SolderingArm", "SOLDERING", [300, 0], logger=self)
        
        # Create other components (with logger)
        self.inventory_check = InventoryCheck(env, "InventoryCheck", logger=self)
        self.output_handler = OutputHandler(env, "OutputHandler", logger=self)
        self.order_generator = OrderGenerator(env, "OrderGenerator", self.plc_controller.orders_in, logger=self)
        
        # Connect components to PLC
        self.plc_controller.picking_arm = self.picking_arm
        self.plc_controller.kitting_arm = self.kitting_arm
        self.plc_controller.mounting_arm = self.mounting_arm
        self.plc_controller.soldering_arm = self.soldering_arm
        self.plc_controller.inventory_check = self.inventory_check
        self.plc_controller.output_handler = self.output_handler
        self.plc_controller.order_generator = self.order_generator

        # Give PLC access to logger (station)
        self.plc_controller.logger = self
        
        # Create sensor and actuator interfaces
        self.sensors = self._create_sensors()
        self.actuators = self._create_actuators()
        
        # Start processes
        self.env.process(self.order_generator.generate_orders())
        
        print("🏭 Kitting Station 1 with PLC Controller Architecture initialized")
        print("🤖 Robotic Arms:")
        print(f"   - {self.picking_arm.name} ({self.picking_arm.role})")
        print(f"   - {self.kitting_arm.name} ({self.kitting_arm.role})")
        print(f"   - {self.mounting_arm.name} ({self.mounting_arm.role})")
        print(f"   - {self.soldering_arm.name} ({self.soldering_arm.role})")
        print("🔧 PLC Controller with Sensor/Actuator architecture ready")

    # -------- logging setup --------

    def _init_log_file(self):
        """Create streaming .jsonl log file in same folder as main.py"""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_dir = os.path.dirname(os.path.abspath(__file__))
        self.log_filename = os.path.join(base_dir, f"kitting_station_1_plc_events_{ts}.jsonl")
        print(f"[LOG] Streaming events to: {self.log_filename}")

    def log_event(self, category, event_type, data=None):
        """Generic event logger for whole station (+ stream to file)"""
        if data is None:
            data = {}

        event = {
            "sim_time": float(self.env.now),
            "category": category,
            "event_type": event_type,
        }
        event.update(data)

        # 1) keep in memory (for SCADA / export_data)
        self.event_log.append(event)

        # 2) append to a .jsonl file on disk (line per event)
        if self.log_filename is not None:
            try:
                with open(self.log_filename, "a", encoding="utf-8") as f:
                    f.write(json.dumps(event) + "\n")
            except Exception as e:
                # don't crash simulation if logging fails
                print(f"[LOG ERROR] could not write event: {e}")

    def log_plc_snapshot(self):
        """Snapshot of PLC-level KPIs"""
        self.log_event(
            category="PLC",
            event_type="SNAPSHOT",
            data={
                "station_state": self.plc_controller.state,
                "queued_orders": self.plc_controller.queued_orders,
                "wip_count": self.plc_controller.wip_count,
                "completed_count": self.plc_controller.completed_count,
            }
        )

    # -------- sensors / actuators creation --------

    def _create_sensors(self):
        """Create all sensor objects"""
        return {
            "station_state": StationStateSensor(self.plc_controller),
            "order": OrderSensor(self.plc_controller),
            "completion": CompletionSensor(self.plc_controller),
            "picking_arm": ArmStateSensor(self.plc_controller, "picking"),
            "kitting_arm": ArmStateSensor(self.plc_controller, "kitting"),
            "mounting_arm": ArmStateSensor(self.plc_controller, "mounting"),
            "soldering_arm": ArmStateSensor(self.plc_controller, "soldering"),
            "inventory": InventoryStatusSensor(self.plc_controller),
            "output": OutputStatusSensor(self.plc_controller),
            "kpi": KPISensor(self.plc_controller),
            "failure": FailureSensor(self.plc_controller)
        }

    def _create_actuators(self):
        """Create all actuator objects"""
        station_actuators = StationActuators(self.plc_controller)
        arm_actuators = {
            "picking": ArmActuator(self.plc_controller, "picking"),
            "kitting": ArmActuator(self.plc_controller, "kitting"),
            "mounting": ArmActuator(self.plc_controller, "mounting"),
            "soldering": ArmActuator(self.plc_controller, "soldering")
        }
        process_actuators = ProcessActuators(self.plc_controller)
        
        return {
            "station": station_actuators,
            "arms": arm_actuators,
            "process": process_actuators
        }

    # -------- delegate properties --------

    @property
    def state(self):
        return self.plc_controller.state
    
    @property
    def order_count(self):
        return self.plc_controller.order_count
    
    @property
    def queued_orders(self):
        return self.plc_controller.queued_orders
    
    @property
    def wip_count(self):
        return self.plc_controller.wip_count
    
    @property
    def completed_count(self):
        return self.plc_controller.completed_count
    
    @property
    def cycle_times(self):
        return self.plc_controller.cycle_times
    
    def get_failure_logs(self):
        return self.plc_controller.get_failure_logs()
    
    def get_utilization_data(self):
        return self.plc_controller.get_utilization_data()
    
    def export_data(self, filename_prefix="kitting_station"):
        """Export all simulation data (delegated to PLC controller data)"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        export_data = {
            "simulation_info": {
                "timestamp": timestamp,
                "total_orders": self.plc_controller.order_count,
                "completed_orders": self.plc_controller.completed_count,
                "simulation_duration": self.env.now,
                "success_rate": (
                    self.plc_controller.completed_count / self.plc_controller.order_count * 100
                ) if self.plc_controller.order_count > 0 else 0
            },
            "cycle_times": self.plc_controller.cycle_times,
            "order_details": [order.to_dict() for order in self.plc_controller.completed_orders],
            "arm_utilizations": self.get_utilization_data(),
            "failures": self.get_failure_logs(),
            "summary_statistics": {
                "avg_cycle_time": self.plc_controller.get_avg_cycle_time(),
                "throughput_orders_per_hour": self.plc_controller.get_throughput(),
                "total_failures": self.plc_controller.get_total_failures(),
                "failures_per_arm": {
                    "PickingArm": self.picking_arm.failure_count,
                    "KittingArm": self.kitting_arm.failure_count,
                    "MountingArm": self.mounting_arm.failure_count,
                    "SolderingArm": self.soldering_arm.failure_count
                }
            },
            # full event log
            "event_log": self.event_log,
        }
        
        # Export to JSON
        json_filename = f"{filename_prefix}_{timestamp}.json"
        with open(json_filename, 'w') as f:
            json.dump(export_data, f, indent=2)
        print(f"📁 Data exported to {json_filename}")
        
        # Export cycle times to CSV
        csv_filename = f"{filename_prefix}_cycle_times_{timestamp}.csv"
        with open(csv_filename, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Order_Index', 'Cycle_Time_Seconds'])
            for i, cycle_time in enumerate(self.plc_controller.cycle_times):
                writer.writerow([i + 1, cycle_time])
        print(f"📁 Cycle times exported to {csv_filename}")
        
        return export_data


# =====================================================
# ----------- Real-time Simulation Engine -------------
# =====================================================

class RealtimeKittingSimulation:
    """Real-time kitting station simulation with PLC controller"""
    def __init__(self):
        self.env = simpy.Environment()
        self.station = KittingStation(self.env)
        
        # Simulation control
        self.is_running = False
        self.simulation_speed = 1.0
        self.max_simulation_time = 300.0
        self.current_speed = 1.0
        self.max_orders = 20

    def set_simulation_speed(self, speed):
        self.simulation_speed = max(0.1, min(10.0, speed))
        self.current_speed = speed

    def run_realtime(self, until=float('inf'), step=0.2):
        self.is_running = True
        
        if until == float('inf'):
            print("🏭 Kitting Station 1 with PLC Controller - SCADA System")
            print("🔧 Architecture: PLC Controller + Sensors + Actuators")
            print("🤖 Robotic Kitting Sequence:")
            print("  1. 📋 Order received (DES Queue)")
            print("  2. 📦 Check inventory (1.0s)")
            print("  3. 🤖 Picking arm: bin → staging")
            print("  4. 🤖 Kitting arm: staging → kitting tray") 
            print("  5. 🤖 Mounting arm: kitting tray → mounting + assembly")
            print("  6. 🤖 Soldering arm: soldering operation")
            print("  7. 📤 Handle output (0.5s)")
            print("  8. 📦 Add to output queue (DES Store)")
            print("  9. 🔄 Reset for next order")
            print(f"🎯 Target: {self.max_orders} orders")
            print("⚠️  Failure probability: 2% per operation")
            print("📊 Features: PLC Controller, DES Queues, Utilization Tracking, Data Export")
        
        start = time.time()
        sim_time = 0
        
        while sim_time < until and self.is_running:
            self.env.run(until=sim_time + step)
            sim_time += step
            
            # Check if we've reached the maximum orders
            if self.station.completed_count >= self.max_orders:
                print(f"🎉 Simulation completed! Reached {self.max_orders} orders!")
                self._print_final_report()
                
                # Export all data
                self.station.export_data("kitting_station_1_plc")
                
                self.is_running = False
                break
                
            elapsed = time.time() - start
            adjusted_elapsed = elapsed * self.simulation_speed
            
            if (delay := (sim_time - adjusted_elapsed) / self.simulation_speed) > 0:
                time.sleep(delay)
                
        if not self.is_running:
            print("🛑 Simulation stopped")

    def _print_final_report(self):
        """Print comprehensive simulation report"""
        print("\n" + "="*60)
        print("📊 FINAL SIMULATION REPORT - PLC ARCHITECTURE")
        print("="*60)
        
        station = self.station
        print(f"🏭 Kitting Station 1 Performance:")
        print(f"   Total Orders Processed: {station.completed_count}")
        if station.order_count > 0:
            print(f"   Success Rate: {(station.completed_count/station.order_count)*100:.1f}%")
        
        if station.cycle_times:
            avg_cycle = station.plc_controller.get_avg_cycle_time()
            min_cycle = min(station.cycle_times)
            max_cycle = max(station.cycle_times)
            print(f"   Cycle Time - Avg: {avg_cycle:.1f}s, Min: {min_cycle:.1f}s, Max: {max_cycle:.1f}s")
        
        print(f"\n🤖 Robotic Arm Performance:")
        arms = [station.picking_arm, station.kitting_arm, station.mounting_arm, station.soldering_arm]
        for arm in arms:
            utilization = arm.get_utilization()
            availability = "❌ FAILED" if arm.state == "FAILED" else "✅ OPERATIONAL"
            print(f"   {arm.name} ({arm.role}):")
            print(f"     Operations: {arm.completed_operations}")
            print(f"     Failures: {arm.failure_count}")
            print(f"     Utilization: {utilization:.1f}%")
            print(f"     Status: {availability}")
        
        # Failure analysis for predictive maintenance
        failures = station.get_failure_logs()
        if failures:
            print(f"\n🚨 Failure Analysis (Total: {len(failures)} failures):")
            for failure in failures[-5:]:  # Show last 5 failures
                print(f"   Time: {failure['time']:.1f}s, Arm: {failure['arm']}, Operation: {failure['operation']}")
        
        print("="*60)

    def stop_simulation(self):
        self.is_running = False

# =====================================================
# ----------- Updated SCADA System Dashboard ----------
# =====================================================

class SCADADashboard(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Kitting Station 1 SCADA Dashboard - PLC Controller")
        self.setFixedSize(1000, 800)
        
        # Store reference to parent for accessing simulation
        self.parent = parent
        
        # Main layout
        main_layout = QVBoxLayout()
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 15, 15, 15)
        
        # Title
        title = QLabel("🏭 3D Printer Kitting Station 1 - PLC Controller SCADA")
        title.setFont(QFont("Arial", 16, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("padding: 15px; background-color: #2c3e50; color: white; border-radius: 10px;")
        title.setMinimumHeight(60)
        main_layout.addWidget(title)
        
        # KPI Section
        kpi_group = QGroupBox("📊 REAL-TIME PRODUCTION KPIs")
        kpi_group.setFont(QFont("Arial", 12, QFont.Bold))
        kpi_layout = QGridLayout()
        kpi_layout.setSpacing(10)
        
        # KPI Labels
        self.queued_label = self.create_kpi_label("Queued Orders: 0")
        self.wip_label = self.create_kpi_label("Work In Progress: 0")
        self.completed_label = self.create_kpi_label("Completed Today: 0")
        self.station_state_label = self.create_kpi_label("Station State: IDLE")
        self.cycle_time_label = self.create_kpi_label("Avg Cycle Time: 0.0s")
        self.orders_label = self.create_kpi_label("Total Orders: 0")
        
        kpi_layout.addWidget(self.queued_label, 0, 0)
        kpi_layout.addWidget(self.wip_label, 0, 1)
        kpi_layout.addWidget(self.completed_label, 1, 0)
        kpi_layout.addWidget(self.station_state_label, 1, 1)
        kpi_layout.addWidget(self.cycle_time_label, 2, 0)
        kpi_layout.addWidget(self.orders_label, 2, 1)
        
        kpi_group.setLayout(kpi_layout)
        main_layout.addWidget(kpi_group)
        
        # Progress Bar
        progress_group = QGroupBox("📈 PRODUCTION PROGRESS")
        progress_group.setFont(QFont("Arial", 12, QFont.Bold))
        progress_layout = QVBoxLayout()
        
        max_orders = 20
        if hasattr(self.parent, 'sim_manager') and self.parent.sim_manager:
            max_orders = self.parent.sim_manager.sim.max_orders

        self.daily_target_label = QLabel(f"Production Target: {max_orders} orders")
        self.daily_target_label.setFont(QFont("Arial", 11))
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximum(max_orders)
        self.progress_bar.setValue(0)
        self.progress_bar.setMinimumHeight(30)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 2px solid #34495e;
                border-radius: 8px;
                text-align: center;
                font-weight: bold;
                color: #2c3e50;
            }
            QProgressBar::chunk {
                background-color: #27ae60;
                border-radius: 6px;
            }
        """)
        
        progress_layout.addWidget(self.daily_target_label)
        progress_layout.addWidget(self.progress_bar)
        progress_group.setLayout(progress_layout)
        main_layout.addWidget(progress_group)
        
        # Current Operation
        operation_group = QGroupBox("🔧 CURRENT OPERATION")
        operation_group.setFont(QFont("Arial", 12, QFont.Bold))
        operation_layout = QVBoxLayout()
        
        self.operation_label = QLabel("Status: Waiting for orders...")
        self.operation_label.setFont(QFont("Arial", 14, QFont.Bold))
        self.operation_label.setStyleSheet("padding: 15px; background-color: #e8f5e8; border: 2px solid #27ae60; border-radius: 8px; color: #2c3e50;")
        self.operation_label.setAlignment(Qt.AlignCenter)
        self.operation_label.setMinimumHeight(50)
        operation_layout.addWidget(self.operation_label)
        
        operation_group.setLayout(operation_layout)
        main_layout.addWidget(operation_group)
        
        # Robotic Arms Status with Utilization
        arms_group = QGroupBox("🤖 ROBOTIC ARMS STATUS & UTILIZATION")
        arms_group.setFont(QFont("Arial", 12, QFont.Bold))
        arms_layout = QGridLayout()
        arms_layout.setSpacing(8)
        
        self.picking_arm_status = self.create_arm_status_label("Picking Arm: IDLE\nUtilization: 0.0%", "PICKING")
        self.kitting_arm_status = self.create_arm_status_label("Kitting Arm: IDLE\nUtilization: 0.0%", "KITTING")
        self.mounting_arm_status = self.create_arm_status_label("Mounting Arm: IDLE\nUtilization: 0.0%", "MOUNTING")
        self.soldering_arm_status = self.create_arm_status_label("Soldering Arm: IDLE\nUtilization: 0.0%", "SOLDERING")
        
        arms_layout.addWidget(self.picking_arm_status, 0, 0)
        arms_layout.addWidget(self.kitting_arm_status, 0, 1)
        arms_layout.addWidget(self.mounting_arm_status, 1, 0)
        arms_layout.addWidget(self.soldering_arm_status, 1, 1)
        
        arms_group.setLayout(arms_layout)
        main_layout.addWidget(arms_group)
        
        # System Components Status
        components_group = QGroupBox("📋 SYSTEM COMPONENTS")
        components_group.setFont(QFont("Arial", 12, QFont.Bold))
        components_layout = QGridLayout()
        components_layout.setSpacing(8)
        
        self.inventory_status = self.create_status_label("Inventory Check: READY")
        self.output_status = self.create_status_label("Output Handler: READY")
        
        components_layout.addWidget(self.inventory_status, 0, 0)
        components_layout.addWidget(self.output_status, 0, 1)
        
        components_group.setLayout(components_layout)
        main_layout.addWidget(components_group)
        
        # Control buttons
        button_layout = QHBoxLayout()
        stop_btn = QPushButton("🛑 EMERGENCY STOP")
        stop_btn.setFont(QFont("Arial", 12, QFont.Bold))
        stop_btn.setStyleSheet("""
            QPushButton {
                background-color: #e74c3c;
                color: white;
                border: none;
                padding: 15px;
                border-radius: 8px;
                font-weight: bold;
                min-height: 40px;
            }
            QPushButton:hover {
                background-color: #c0392b;
            }
            QPushButton:pressed {
                background-color: #a93226;
            }
        """)
        stop_btn.clicked.connect(self.stop_simulation)
        button_layout.addWidget(stop_btn)
        
        main_layout.addLayout(button_layout)
        
        self.setLayout(main_layout)

    def create_kpi_label(self, text):
        """Create a standardized KPI label"""
        label = QLabel(text)
        label.setFont(QFont("Arial", 11, QFont.Bold))
        label.setStyleSheet("""
            padding: 12px; 
            background-color: #ecf0f1; 
            border: 1px solid #bdc3c7;
            border-radius: 6px;
            color: #2c3e50;
        """)
        label.setMinimumHeight(40)
        return label

    def create_arm_status_label(self, text, arm_type):
        """Create a robotic arm status label"""
        label = QLabel(text)
        label.setFont(QFont("Arial", 10, QFont.Medium))
        label.setStyleSheet("""
            padding: 10px; 
            background-color: #e3f2fd; 
            border: 1px solid #90caf9;
            border-radius: 5px;
            color: #1565c0;
            font-weight: bold;
        """)
        label.setMinimumHeight(50)
        return label

    def create_status_label(self, text):
        """Create a standardized status label"""
        label = QLabel(text)
        label.setFont(QFont("Arial", 10, QFont.Medium))
        label.setStyleSheet("""
            padding: 10px; 
            background-color: #f8f9fa; 
            border: 1px solid #dee2e6;
            border-radius: 5px;
            color: #495057;
        """)
        label.setMinimumHeight(35)
        return label

    def update_dashboard(self, station):
        """Update all SCADA dashboard elements using sensor interface"""
        if station is None or not hasattr(station, 'sensors'):
            print("SCADA: Station or sensors not available")
            return
            
        try:
            sensors = station.sensors
            
            # Update KPIs using sensors
            self.queued_label.setText(f"Queued Orders: {sensors['order'].get_queue_size()}")
            self.wip_label.setText(f"Work In Progress: {station.wip_count}")
            self.completed_label.setText(f"Completed Today: {sensors['completion'].read()}")
            self.station_state_label.setText(f"Station State: {sensors['station_state'].read()}")
            self.orders_label.setText(f"Total Orders: {sensors['kpi'].get_total_orders()}")
            
            # Update cycle time
            avg_cycle_time = sensors['kpi'].get_avg_cycle_time()
            self.cycle_time_label.setText(f"Avg Cycle Time: {avg_cycle_time:.1f}s")
            
            # Update progress bar
            self.progress_bar.setValue(station.completed_count)
            
            # Update current operation with color coding
            operation_text, color = self._get_operation_text(station.state)
            self.operation_label.setText(f"Status: {operation_text}")
            self.operation_label.setStyleSheet(f"""
                padding: 15px; 
                background-color: {color}; 
                border: 2px solid #34495e;
                border-radius: 8px; 
                color: #2c3e50;
                font-weight: bold;
            """)
            
            # Update robotic arms status with utilization using sensors
            self.update_arm_status(self.picking_arm_status, sensors['picking_arm'], "Picking Arm")
            self.update_arm_status(self.kitting_arm_status, sensors['kitting_arm'], "Kitting Arm")
            self.update_arm_status(self.mounting_arm_status, sensors['mounting_arm'], "Mounting Arm")
            self.update_arm_status(self.soldering_arm_status, sensors['soldering_arm'], "Soldering Arm")
            
            # Update component status using sensors
            self.update_component_status(self.inventory_status, sensors['inventory'].read(), "Inventory Check")
            self.update_component_status(self.output_status, sensors['output'].read(), "Output Handler")
            
            # Force UI update
            self.update()
            self.repaint()
            
        except Exception as e:
            print(f"SCADA update error: {e}")

    def update_arm_status(self, label, arm_sensor, arm_name):
        """Update robotic arm status with utilization using sensor"""
        state = arm_sensor.read()
        utilization = arm_sensor.get_utilization()
        operations_text = f" ({arm_sensor.get_operations_count()} ops)"
        label.setText(f"{arm_name}: {state}{operations_text}\nUtilization: {utilization:.1f}%")
        label.setStyleSheet(f"""
            padding: 10px; 
            background-color: {self._get_arm_state_color(state)}; 
            border: 2px solid #34495e;
            border-radius: 5px;
            color: #2c3e50;
            font-weight: bold;
        """)

    def update_component_status(self, label, state, component_name):
        """Update component status with color coding"""
        color = self._get_state_color(state)
        label.setText(f"{component_name}: {state}")
        label.setStyleSheet(f"""
            padding: 10px; 
            background-color: {color}; 
            border: 1px solid #dee2e6;
            border-radius: 5px;
            color: #495057;
            font-weight: medium;
        """)

    def _get_arm_state_color(self, state):
        """Get color based on robotic arm state"""
        if state == "FAILED":
            return "#ffebee"  # Light red for failure
        elif state in ["MOVING", "WORKING"]:
            return "#fff3cd"  # Light yellow for active
        elif state == "IDLE":
            return "#e8f5e8"  # Light green for idle
        else:
            return "#e3f2fd"  # Light blue for unknown

    def _get_state_color(self, state):
        """Get color based on component state"""
        if state in ["PARTS_AVAILABLE", "OUTPUT_COMPLETE"]:
            return "#d4edda"  # Light green for success
        elif state in ["READY", "PARTS_MISSING"]:
            return "#f8d7da"  # Light red for error/ready
        elif state in ["CHECKING", "PROCESSING"]:
            return "#fff3cd"  # Light yellow for in-progress
        else:
            return "#d1ecf1"  # Light blue for unknown

    def _get_operation_text(self, state):
        """Convert state to human-readable operation text with color"""
        operations = {
            "IDLE": ("🟢 Waiting for orders...", "#d4edda"),
            "PROCESSING": ("🟡 Starting kitting sequence", "#fff3cd"),
            "CHECKING_INVENTORY": ("📦 Checking parts inventory", "#d1ecf1"),
            "ROBOTIC_PICKING": ("🤖 Picking components", "#fff3cd"),
            "ROBOTIC_KITTING": ("🤖 Kitting components", "#fff3cd"),
            "ROBOTIC_MOUNTING": ("🤖 Mounting assembly", "#fff3cd"),
            "ROBOTIC_SOLDERING": ("🤖 Soldering joints", "#fff3cd"),
            "HANDLING_OUTPUT": ("📤 Handling completed assembly", "#fff3cd"),
            "RESETTING": ("🔄 Resetting station", "#d1ecf1"),
            "ERROR": ("🔴 Station error", "#ffebee"),
            "EMERGENCY_STOP": ("🛑 EMERGENCY STOP", "#ffebee")
        }
        return operations.get(state, ("❓ Unknown operation", "#f8f9fa"))

    def stop_simulation(self):
        """Stop the simulation"""
        if hasattr(self.parent, 'sim_manager') and self.parent.sim_manager:
            self.parent.sim_manager.sim.stop_simulation()
            QMessageBox.information(self, "Simulation Stopped", "Kitting simulation has been stopped.")
        self.close()

# =====================================================
# ----------- Main Application ------------------------
# =====================================================

class SimulationManager:
    def __init__(self, main_window):
        self.wnd = main_window
        self.sim = RealtimeKittingSimulation()
        self.scada_dashboard = None
        self.current_speed = 1.0

    def start_simulation(self):
        thread = threading.Thread(target=self.sim.run_realtime, daemon=True)
        thread.start()

class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Kitting Station 1 - PLC Controller Architecture")
        self.setGeometry(100, 100, 600, 400)
        
        self.sim_manager = None
        self.scada_dashboard = None
        self.scada_timer = QTimer()
        self.scada_timer.timeout.connect(self.update_scada_dashboard)
        
        # Add visualization window reference and auto-launch timer
        self.viz_window = None
        self.completion_timer = QTimer()
        self.completion_timer.timeout.connect(self.check_simulation_completion)
        self.completion_timer.setInterval(1000)  # Check every second
        self.auto_launch_enabled = False
        
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()
        
        # Title
        title = QLabel("🏭 Kitting Station 1 - PLC Controller Architecture")
        title.setFont(QFont("Arial", 18, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        
        # Description
        desc = QLabel(
            "Smart Factory Kitting Cell with PLC Controller Architecture:\n"
            "• PLC Controller • Sensors Module • Actuators Module\n"
            "• Picking Arm • Kitting Arm • Mounting Arm • Soldering Arm\n"
            "• Real DES Queues • Utilization Metrics • Data Export"
        )
        desc.setFont(QFont("Arial", 11))
        desc.setAlignment(Qt.AlignCenter)
        layout.addWidget(desc)
        
        # Control buttons
        btn_layout = QHBoxLayout()
        
        start_btn = QPushButton("▶ Start Simulation")
        start_btn.setFont(QFont("Arial", 12))
        start_btn.clicked.connect(self.start_simulation)
        
        scada_btn = QPushButton("📊 Open SCADA Dashboard")
        scada_btn.setFont(QFont("Arial", 12))
        scada_btn.clicked.connect(self.show_scada_dashboard)
        
        stop_btn = QPushButton("⏹ Stop Simulation")
        stop_btn.setFont(QFont("Arial", 12))
        stop_btn.clicked.connect(self.stop_simulation)

        # 3D Visualization button
        viz_btn = QPushButton("🎬 3D Visualization")
        viz_btn.setFont(QFont("Arial", 12))
        viz_btn.clicked.connect(self.open_3d_viz)
        
        # Auto-launch checkbox
        auto_launch_checkbox = QCheckBox("Auto-launch 3D after simulation")
        auto_launch_checkbox.setChecked(True)
        auto_launch_checkbox.stateChanged.connect(
            lambda state: setattr(self, 'auto_launch_enabled', state == Qt.Checked)
        )
        
        btn_layout.addWidget(start_btn)
        btn_layout.addWidget(scada_btn)
        btn_layout.addWidget(stop_btn)
        btn_layout.addWidget(viz_btn)
        
        layout.addLayout(btn_layout)
        
        # Add auto-launch checkbox below buttons
        checkbox_layout = QHBoxLayout()
        checkbox_layout.addStretch()
        checkbox_layout.addWidget(auto_launch_checkbox)
        checkbox_layout.addStretch()
        layout.addLayout(checkbox_layout)
        
        # Status
        self.status_label = QLabel("Status: Ready to start simulation")
        self.status_label.setFont(QFont("Arial", 10))
        layout.addWidget(self.status_label)
        
        # Recent log files list
        recent_group = QGroupBox("📁 Recent Simulation Logs")
        recent_layout = QVBoxLayout(recent_group)
        
        self.recent_logs_list = QListWidget()
        self.recent_logs_list.setMaximumHeight(100)
        self.recent_logs_list.itemDoubleClicked.connect(self.open_log_in_3d)
        self.update_recent_logs_list()
        
        recent_layout.addWidget(QLabel("Double-click to open in 3D viewer:"))
        recent_layout.addWidget(self.recent_logs_list)
        
        refresh_btn = QPushButton("🔄 Refresh List")
        refresh_btn.clicked.connect(self.update_recent_logs_list)
        recent_layout.addWidget(refresh_btn)
        
        layout.addWidget(recent_group)
        
        self.setLayout(layout)

    def start_simulation(self):
        if self.sim_manager and hasattr(self.sim_manager, 'sim') and self.sim_manager.sim.is_running:
            QMessageBox.warning(self, "Simulation", "Simulation is already running.")
            return

        self.sim_manager = SimulationManager(self)
        self.sim_manager.start_simulation()
        self.scada_timer.start(500)  # Update SCADA every 500ms
        self.completion_timer.start()  # Start checking for completion
        self.status_label.setText("Status: Simulation running...")
        
        # Disable auto-launch during simulation
        if hasattr(self, 'auto_launch_enabled'):
            self.status_label.setText(f"Status: Simulation running... (Auto-launch: {'ON' if self.auto_launch_enabled else 'OFF'})")

    def stop_simulation(self):
        self.scada_timer.stop()
        self.completion_timer.stop()
        if self.sim_manager and hasattr(self.sim_manager, 'sim'):
            self.sim_manager.sim.stop_simulation()
            self.status_label.setText("Status: Simulation stopped")
        QMessageBox.information(self, "Simulation", "Kitting simulation stopped.")
        
        # Update recent logs list
        self.update_recent_logs_list()

    def show_scada_dashboard(self):
        """Show the SCADA dashboard"""
        if not self.scada_dashboard:
            self.scada_dashboard = SCADADashboard(self)
        
        self.scada_dashboard.show()
        self.scada_dashboard.raise_()
        self.scada_dashboard.activateWindow()

    def update_scada_dashboard(self):
        """Update SCADA dashboard with current simulation data"""
        if (self.scada_dashboard and self.scada_dashboard.isVisible() and 
            self.sim_manager and hasattr(self.sim_manager, 'sim')):
            
            sim = self.sim_manager.sim
            station = sim.station
            self.scada_dashboard.update_dashboard(station)

    def open_3d_viz(self, log_path: str = None):
        """Open 3D visualization window with optional log file"""
        try:
            # Import here to avoid circular imports
            from visual import open_visualization_with_log, add_3d_viz_to_main_window
            
            # If specific log path provided, use it
            if log_path and os.path.exists(log_path):
                open_func = open_visualization_with_log(log_path)
                self.viz_window = open_func()
            else:
                # Try to find the most recent log file
                import glob
                log_files = glob.glob("kitting_station_1_plc_events_*.jsonl")
                if log_files:
                    log_files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
                    latest_log = log_files[0]
                    
                    # Ask user if they want to open the latest log
                    reply = QMessageBox.question(
                        self, 'Open Latest Log',
                        f'Found recent log: {os.path.basename(latest_log)}\n\nOpen this log in 3D viewer?',
                        QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                        QMessageBox.Yes
                    )
                    
                    if reply == QMessageBox.Yes:
                        open_func = open_visualization_with_log(latest_log)
                        self.viz_window = open_func()
                    elif reply == QMessageBox.No:
                        # Open empty visualization
                        open_func = add_3d_viz_to_main_window()
                        self.viz_window = open_func()
                    # If Cancel, do nothing
                else:
                    # No log files found, open empty visualization
                    open_func = add_3d_viz_to_main_window()
                    self.viz_window = open_func()
            
            if self.viz_window:
                self.viz_window.show()
                
        except Exception as e:
            QMessageBox.critical(self, "3D Visualization Error", f"Failed to open 3D view:\n{str(e)}")

    def open_log_in_3d(self, item):
        """Open a specific log file from the recent logs list"""
        log_path = item.data(Qt.UserRole)
        if log_path and os.path.exists(log_path):
            self.open_3d_viz(log_path)
        else:
            QMessageBox.warning(self, "File Not Found", f"Log file not found:\n{log_path}")

    def update_recent_logs_list(self):
        """Update the list of recent log files"""
        try:
            import glob
            from datetime import datetime
            
            self.recent_logs_list.clear()
            log_files = glob.glob("kitting_station_1_plc_events_*.jsonl")
            
            if not log_files:
                self.recent_logs_list.addItem("No log files found")
                return
            
            # Sort by modification time (newest first)
            log_files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
            
            # Show only the 5 most recent files
            for i, log_file in enumerate(log_files[:5]):
                file_time = datetime.fromtimestamp(os.path.getmtime(log_file))
                file_size = os.path.getsize(log_file) / 1024  # Size in KB
                
                # Create display text
                display_text = f"{i+1}. {os.path.basename(log_file)}"
                display_text += f"\n   {file_time.strftime('%Y-%m-%d %H:%M:%S')} | {file_size:.1f} KB"
                
                # Count events in file
                try:
                    with open(log_file, 'r') as f:
                        event_count = sum(1 for _ in f)
                    display_text += f" | {event_count} events"
                except:
                    display_text += " | Error reading"
                
                item = QListWidgetItem(display_text)
                item.setData(Qt.UserRole, log_file)
                
                # Color code based on age
                days_old = (datetime.now() - file_time).days
                if days_old == 0:
                    item.setForeground(QColor(0, 128, 0))  # Green for today
                elif days_old <= 7:
                    item.setForeground(QColor(0, 0, 128))  # Blue for this week
                
                self.recent_logs_list.addItem(item)
            
            if len(log_files) > 5:
                self.recent_logs_list.addItem(f"... and {len(log_files) - 5} more log files")
                
        except Exception as e:
            print(f"Error updating recent logs list: {e}")
            self.recent_logs_list.addItem(f"Error: {str(e)}")

    def check_simulation_completion(self):
        """Check if simulation has completed and handle auto-launch"""
        if (self.sim_manager and hasattr(self.sim_manager, 'sim') and 
            hasattr(self.sim_manager.sim, 'is_running')):
            
            sim = self.sim_manager.sim
            
            # Check if simulation has stopped (completed or manually stopped)
            if not sim.is_running:
                self.completion_timer.stop()
                
                # Check if it completed successfully (reached target)
                if (hasattr(sim, 'station') and hasattr(sim.station, 'completed_count') and
                    hasattr(sim, 'max_orders') and sim.station.completed_count >= sim.max_orders):
                    
                    # Update status
                    self.status_label.setText(f"Status: Simulation completed ({sim.station.completed_count}/{sim.max_orders} orders)")
                    
                    # Auto-launch visualization if enabled
                    if self.auto_launch_enabled and not self.viz_window:
                        self.auto_launch_visualization()
                        
                    # Update recent logs list
                    QTimer.singleShot(1000, self.update_recent_logs_list)  # Wait a second for file to be written

    def auto_launch_visualization(self):
        """Auto-launch 3D visualization after simulation completes"""
        try:
            import glob
            
            # Get the latest log file
            log_files = glob.glob("kitting_station_1_plc_events_*.jsonl")
            if log_files:
                log_files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
                latest_log = log_files[0]
                
                # Ask user if they want to open visualization
                reply = QMessageBox.question(
                    self, 'Simulation Complete',
                    f'Simulation completed successfully!\n\n'
                    f'Orders processed: {self.sim_manager.sim.station.completed_count}\n'
                    f'Log file: {os.path.basename(latest_log)}\n\n'
                    f'Open 3D visualization?',
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
                )
                
                if reply == QMessageBox.Yes:
                    self.open_3d_viz(latest_log)
                    
        except Exception as e:
            print(f"Auto-launch failed: {e}")
            # Don't show error message for auto-launch failures

    def closeEvent(self, event):
        """Clean up on window close"""
        self.scada_timer.stop()
        self.completion_timer.stop()
        
        # Close visualization window if open
        if self.viz_window and hasattr(self.viz_window, 'close'):
            self.viz_window.close()
        
        # Close SCADA dashboard if open
        if self.scada_dashboard and hasattr(self.scada_dashboard, 'close'):
            self.scada_dashboard.close()
        
        event.accept()

# =====================================================
# ----------- Update SimulationManager for Parent Reference
# =====================================================

class SimulationManager:
    def __init__(self, main_window):
        self.wnd = main_window
        self.sim = RealtimeKittingSimulation()
        self.scada_dashboard = None
        self.current_speed = 1.0
        
        # Store parent reference in simulation for auto-launch
        self.sim.parent_window = main_window

    def start_simulation(self):
        thread = threading.Thread(target=self.sim.run_realtime, daemon=True)
        thread.start()

# =====================================================
# ----------- Entry Point -----------------------------
# =====================================================

if __name__ == "__main__":
    app = QApplication(sys.argv)
    font = QFont("Arial", 9)
    app.setFont(font)

    # ---- GLOBAL DARK THEME (so visual window is not white) ----
    app.setStyle("Fusion")
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
    app.setPalette(palette)
    # -----------------------------------------------------------

    # Initialize GLUT for 3D visualization (needed for text rendering)
    try:
        from OpenGL.GLUT import glutInit
        glutInit([])
    except Exception as e:
        print(f"Note: GLUT initialization failed (text rendering may be limited): {e}")

    wnd = MainWindow()
    wnd.show()

    try:
        sys.exit(app.exec_())
    except Exception as e:
        print(f"Application error: {e}")
        sys.exit(1)
