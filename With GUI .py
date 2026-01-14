"""
3D Printer Packaging Station SCADA Simulation
=============================================
A comprehensive simulation of a packaging line with 4 components:
1. Sensors - Monitor physical inputs and stock levels
2. PLC - Control logic and state machine
3. Actuators - Physical outputs and motors
4. Human Resource - Manual intervention for repairs and refills

The simulation includes fault injection, queue-based box flow, and real-time SCADA GUI.
"""

import simpy
import random
import tkinter as tk
from tkinter import ttk, scrolledtext
import threading
import time
from enum import Enum

# ============================================
# ENUMS AND CONSTANTS
# ============================================

class RepairType(Enum):
    """Types of repairs that Human Resource can perform"""
    ROBOT = 1
    FLAP = 2
    TAPE_SEALER = 3
    LABEL_UNIT = 4
    CONVEYOR = 5


class RefillType(Enum):
    """Types of material refills that Human Resource can perform"""
    CARTON = 1
    TAPE = 2
    LABEL = 3


class RobotDirection(Enum):
    """Robot movement directions for the robotic arm"""
    IDLE = 0
    TO_PRINTER = 1
    TO_CARTON = 2


# System configuration constants
LOW_THRESHOLD_TAPE = 5        # Minimum tape level before warning
LOW_THRESHOLD_LABEL = 5       # Minimum label level before warning
MAX_CARTON_STOCK = 50         # Maximum carton storage capacity
MAX_TAPE_STOCK = 50           # Maximum tape roll capacity
MAX_LABEL_STOCK = 50          # Maximum label roll capacity
CONVEYOR_CAPACITY = 100       # Maximum boxes on final conveyor
INITIAL_CARTON_STOCK = 5      # Starting carton count
INITIAL_TAPE_STOCK = 5        # Starting tape amount
INITIAL_LABEL_STOCK = 5       # Starting label amount


# ============================================
# SENSORS COMPONENT
# ============================================

class SensorsComponent:
    """
    Represents all physical sensors in the packaging station.
    Monitors printer presence, stock levels, machine faults, and box positions.
    """
    
    def __init__(self, env):
        """Initialize all sensor values and start background processes"""
        self.env = env
        
        # VSI sensor outputs (aligned with system specification)
        # Printer/Infeed sensors
        self.printer_present = False            # 3D printer has product ready
        self.printer_counter = 0                # Count of printer arrivals
        
        # Carton handling sensors
        self.loader_pocket_carton_present = False  # Carton in loader pocket
        self.carton_blank_empty = False          # No cartons in stack
        self.carton_stock = INITIAL_CARTON_STOCK # Available carton count
        
        # Robotic arm sensors
        self.robot_fault = False                # Robotic arm failure
        self.product_placed_ok = False          # Product placed in carton
        
        # Flap folding station sensors
        self.box_at_flap = False                # Carton at flap station
        self.top_flaps_closed_ok = False        # Flaps correctly folded
        self.flap_fault = False                 # Flap folder failure
        
        # Tape sealer station sensors
        self.box_at_tape = False                # Carton at tape station
        self.tape_applied_ok = False            # Tape correctly applied
        self.tape_sealer_fault = False          # Tape sealer failure
        self.tape_empty = False                 # No tape available
        self.tape_low = False                   # Low tape warning
        self.tape_stock = INITIAL_TAPE_STOCK    # Available tape amount
        
        # Labeling station sensors
        self.box_at_label = False               # Carton at label station
        self.label_applied_ok = False           # Label correctly applied
        self.labeler_fault = False              # Labeler failure
        self.label_empty = False                # No labels available
        self.label_low = False                  # Low label warning
        self.label_stock = INITIAL_LABEL_STOCK  # Available label amount
        
        # Conveyor system sensors
        self.conveyor_full = False              # Final conveyor at capacity
        self.conveyor_fault = False             # Conveyor failure
        self.conveyor_count = 0                 # Boxes on final conveyor
        
        # Internal tracking
        self.current_box_position = None        # 'pocket', 'flap', 'tape', 'label'
        
        # Start background simulation processes
        self.env.process(self._simulate_printer())
        self.env.process(self._update_stock_flags())
        self.env.process(self._simulate_conveyor())
    
    
    def _simulate_printer(self):
        """
        Simulates the upstream 3D printer sending products.
        Uses pulse-based logic: false→true→false represents one product arrival.
        Edge detection ensures each pulse is counted only once.
        """
        prev_present = self.printer_present
        
        while True:
            # Random idle gap between printer pulses
            self.printer_present = False
            prev_present = self.printer_present
            yield self.env.timeout(random.uniform(6.0, 12.0))
            
            # Rising edge: printer sends a product (pulse starts)
            self.printer_present = True
            if self.printer_present and not prev_present:
                self.printer_counter += 1  # Count only on rising edge
            prev_present = self.printer_present
            yield self.env.timeout(1.5)  # Pulse duration
            
            # Falling edge: pulse ends
            self.printer_present = False
            prev_present = self.printer_present
            yield self.env.timeout(1.0)  # Low period before next pulse
    
    
    def _update_stock_flags(self):
        """Continuously updates stock warning and empty flags"""
        while True:
            self.carton_blank_empty = self.carton_stock <= 0
            self.tape_empty = self.tape_stock <= 0
            self.tape_low = 0 < self.tape_stock <= LOW_THRESHOLD_TAPE
            self.label_empty = self.label_stock <= 0
            self.label_low = 0 < self.label_stock <= LOW_THRESHOLD_LABEL
            yield self.env.timeout(0.5)
    
    
    def _simulate_conveyor(self):
        """Monitors conveyor capacity and updates full status"""
        while True:
            self.conveyor_full = self.conveyor_count >= CONVEYOR_CAPACITY
            yield self.env.timeout(0.2)
    
    
    def use_carton(self):
        """Consumes one carton from stock. Returns True if successful."""
        if self.carton_stock > 0:
            self.carton_stock -= 1.0
            return True
        return False
    
    
    def use_tape(self):
        """Consumes tape from stock. Returns True if successful."""
        if self.tape_stock > 0:
            self.tape_stock -= 1.0
            return True
        return False
    
    
    def use_label(self):
        """Consumes label from stock. Returns True if successful."""
        if self.label_stock > 0:
            self.label_stock -= 1.0
            return True
        return False
    
    
    def simulate_machine_fault(self, machine_type, probability_percent):
        """
        Randomly simulates machine faults based on probability.
        
        Args:
            machine_type: String identifying machine ('robot', 'flap', 'tape', 'label', 'conveyor')
            probability_percent: Chance of fault occurring (0-100)
        
        Returns:
            True if fault occurred, False otherwise
        """
        if random.random() * 100 < probability_percent:
            fault_mapping = {
                "robot": lambda: setattr(self, 'robot_fault', True),
                "flap": lambda: setattr(self, 'flap_fault', True),
                "tape": lambda: setattr(self, 'tape_sealer_fault', True),
                "label": lambda: setattr(self, 'labeler_fault', True),
                "conveyor": lambda: setattr(self, 'conveyor_fault', True)
            }
            fault_mapping[machine_type]()
            return True
        return False
    
    
    def reset_fault(self, machine_type):
        """Resets fault flag for specified machine"""
        fault_mapping = {
            "robot": 'robot_fault',
            "flap": 'flap_fault',
            "tape": 'tape_sealer_fault',
            "label": 'labeler_fault',
            "conveyor": 'conveyor_fault'
        }
        if machine_type in fault_mapping:
            setattr(self, fault_mapping[machine_type], False)
    
    
    def move_box_to_next_station(self):
        """
        Moves box to next station in the production line.
        Updates position flags and conveyor count.
        """
        # Clear all position flags first
        self.loader_pocket_carton_present = False
        self.box_at_flap = False
        self.box_at_tape = False
        self.box_at_label = False
        
        # Update position based on current location
        if self.current_box_position == 'pocket':
            self.current_box_position = 'flap'
            self.box_at_flap = True
            self.product_placed_ok = False
            
        elif self.current_box_position == 'flap':
            self.current_box_position = 'tape'
            self.box_at_tape = True
            
        elif self.current_box_position == 'tape':
            self.current_box_position = 'label'
            self.box_at_label = True
            
        elif self.current_box_position == 'label':
            self.current_box_position = None
            self.conveyor_count += 1  # Box exits system


# ============================================
# ACTUATORS COMPONENT
# ============================================

class ActuatorsComponent:
    """
    Represents all physical actuators in the packaging station.
    Controls motors, grippers, and tower lights.
    """
    
    def __init__(self, env):
        """Initialize all actuator outputs to default (off) state"""
        self.env = env
        
        # Robotic arm actuators
        self.axis_x_move = False      # Horizontal movement enabled
        self.axis_x_dir = 0           # Horizontal direction (0=idle, 1=to printer, 2=to carton)
        self.axis_z_move = False      # Vertical movement enabled
        self.axis_z_dir = 0           # Vertical direction (0=idle, 1=down, 2=up)
        self.gripper_cmd = 0          # Gripper command (0=idle, 1=close, 2=open)
        
        # Station actuators
        self.flap_folder_enable = False      # Flap folder motor
        self.tape_sealer_enable = False      # Tape sealer motor
        self.label_unit_enable = False       # Label applicator motor
        self.final_conveyor_motor = False    # Final conveyor motor
        
        # Carton handling actuators
        self.carton_erector_enable = False   # Carton erector mechanism
        self.carton_conveyor_motor = False   # Internal conveyor motor
        self.carton_conveyor_stopper = False # Internal conveyor stopper
        
        # Tower light indicators
        self.tower_light_green = False   # Normal operation
        self.tower_light_yellow = False  # Low stock warning
        self.tower_light_red = False     # Fault or empty


# ============================================
# HUMAN RESOURCE COMPONENT
# ============================================

class HumanResourceComponent:
    """
    Represents human operator who performs repairs and refills.
    Reacts to tower lights and machine faults.
    """
    
    def __init__(self, env):
        """Initialize HR component with no active requests"""
        self.env = env
        
        # Request flags to PLC
        self.repairRequest = False    # Repair needed
        self.refillRequest = False    # Refill needed
        self.repairType = 0           # Type of repair (RepairType enum)
        self.refillType = 0           # Type of refill (RefillType enum)
        self.repairDone = False       # Repair completed
        self.refillDone = False       # Refill completed
        
        # Active process tracking
        self.active_repair = None     # Currently repairing machine type
        self.active_refill = None     # Currently refilling material type
        
        # Sensed tower lights (what operator sees)
        self.tower_light_green = False
        self.tower_light_yellow = False
        self.tower_light_red = False
    
    
    def set_tower_lights(self, green, yellow, red):
        """Update tower light status as seen by operator"""
        self.tower_light_green = green
        self.tower_light_yellow = yellow
        self.tower_light_red = red
    
    
    def process_requests(self, repair_request, refill_request, repair_type, refill_type):
        """
        Process repair or refill requests from PLC.
        Starts simulation process for the requested action.
        """
        if repair_request and not self.active_repair:
            self.repairRequest = True
            self.repairType = repair_type
            self.env.process(self._handle_repair(repair_type))
        
        if refill_request and not self.active_refill:
            self.refillRequest = True
            self.refillType = refill_type
            self.env.process(self._handle_refill(refill_type))
    
    
    def _handle_repair(self, repair_type):
        """Simulates repair process (takes 5 seconds)"""
        self.active_repair = repair_type
        yield self.env.timeout(5)  # Repair time
        
        self.repairRequest = False
        self.active_repair = None
        self.repairDone = True
        
        yield self.env.timeout(0.1)  # Brief pulse
        self.repairDone = False
    
    
    def _handle_refill(self, refill_type):
        """Simulates refill process (takes 4 seconds)"""
        self.active_refill = refill_type
        yield self.env.timeout(4)  # Refill time
        
        self.refillRequest = False
        self.active_refill = None
        self.refillDone = True
        
        yield self.env.timeout(0.1)  # Brief pulse
        self.refillDone = False


# ============================================
# PLC COMPONENT (MAIN CONTROL LOGIC)
# ============================================

class PLCComponent:
    """
    Main Programmable Logic Controller.
    Coordinates sensors, actuators, and HR to run the packaging line.
    Implements state machine and production logic.
    """
    
    def __init__(self, env, sensors, actuators, hr, gui_callback=None):
        """Initialize PLC with all components and start control loops"""
        self.env = env
        self.sensors = sensors
        self.actuators = actuators
        self.hr = hr
        self.gui_callback = gui_callback
        
        # Mirror HR signals for internal use
        self.hr_repair_request = False
        self.hr_refill_request = False
        self.hr_repair_type = 0
        self.hr_refill_type = 0
        self.hr_repair_done = False
        self.hr_refill_done = False
        self.hr_tower_light_green = False
        self.hr_tower_light_yellow = False
        self.hr_tower_light_red = False
        
        # Refill state tracking (prevents double counting)
        self._refill_in_progress = False
        self._refill_type_in_progress = 0
        
        # KPI tracking
        self.packages_completed = 0
        self.arm_cycles = 0
        self.total_repairs = 0
        self.total_refills = 0
        self.carton_refills = 0
        self.tape_refills = 0
        self.label_refills = 0
        self.downtime_seconds = 0.0
        self.operational_time_seconds = 0.0
        
        # State variables
        self.state = "IDLE"
        self.robot_state = "IDLE"
        self.flap_attempts = 0
        self.tape_attempts = 0
        self.label_attempts = 0
        self.max_attempts = 3
        
        # Process timing flags
        self.flap_in_progress = False
        self.flap_busy_until = 0.0
        self.tape_in_progress = False
        self.tape_busy_until = 0.0
        self.label_in_progress = False
        self.label_busy_until = 0.0
        self.erect_in_progress = False
        self.erect_busy_until = 0.0
        
        # Queue-based flow control
        self.generator_flow_enabled = True
        self.box_queue = None  # simpy.Store for printer queue
        
        # Start all control processes
        self.env.process(self._main_control_loop())
        self.env.process(self._kpi_tracking_loop())
        self.env.process(self._printer_infeed_loop())
        self.env.process(self._box_pipeline_loop())
    
    
    # ============================================
    # MAIN CONTROL LOOPS
    # ============================================
    
    def _main_control_loop(self):
        """
        Main control loop running at 10Hz (0.1 second intervals).
        Updates tower lights, processes HR requests, and manages station state.
        """
        while True:
            # Core control functions
            self._update_tower_lights()
            self._process_hr_requests()
            
            # State-based handling
            if self._is_downtime():
                self._handle_downtime()
            else:
                self._handle_normal_operation()
            
            yield self.env.timeout(0.1)
    
    
    def _kpi_tracking_loop(self):
        """
        Tracks KPIs and updates GUI at regular intervals.
        Calculates availability and operational metrics.
        """
        gui_interval = 0.5  # Update GUI twice per second
        accumulator = 0.0
        
        while True:
            yield self.env.timeout(0.1)
            dt = 0.1  # Time delta for this iteration
            
            # Track operational vs downtime
            if self._is_downtime():
                self.downtime_seconds += dt
            else:
                self.operational_time_seconds += dt
            
            accumulator += dt
            
            # Send KPI data to GUI at regular intervals
            if self.gui_callback and accumulator >= gui_interval:
                accumulator = 0.0
                self.gui_callback("update_kpi", self.get_kpi_data())
    
    
    # ============================================
    # TOWER LIGHT MANAGEMENT
    # ============================================
    
    def _update_tower_lights(self):
        """
        Updates tower lights based on system status:
        - Green: Normal operation
        - Yellow: Low stock warning (but not empty)
        - Red: Fault, empty stock, or conveyor full (downtime)
        """
        if self._is_downtime():
            # Red light for downtime conditions
            self.actuators.tower_light_green = False
            self.actuators.tower_light_yellow = False
            self.actuators.tower_light_red = True
            
        elif (self.sensors.tape_low or 
              self.sensors.label_low or 
              self.sensors.carton_blank_empty):
            # Yellow light for low stock warnings
            self.actuators.tower_light_green = False
            self.actuators.tower_light_yellow = True
            self.actuators.tower_light_red = False
            
        else:
            # Green light for normal operation
            self.actuators.tower_light_green = True
            self.actuators.tower_light_yellow = False
            self.actuators.tower_light_red = False
        
        # Mirror lights to HR component
        self.hr.set_tower_lights(
            self.actuators.tower_light_green,
            self.actuators.tower_light_yellow,
            self.actuators.tower_light_red,
        )
        
        # Update internal HR light variables
        self.hr_tower_light_green = self.hr.tower_light_green
        self.hr_tower_light_yellow = self.hr.tower_light_yellow
        self.hr_tower_light_red = self.hr.tower_light_red
    
    
    # ============================================
    # HUMAN RESOURCE INTERACTION
    # ============================================
    
    def _process_hr_requests(self):
        """
        Manages HR requests for repairs and refills.
        Triggers requests based on tower lights and processes completions.
        Prevents double counting of refills.
        """
        # Request new repairs/refills based on tower lights
        self._trigger_hr_requests()
        
        # Process completed refills (FIXED: prevents double counting)
        self._process_refill_completion()
        
        # Process completed repairs
        self._process_repair_completion()
        
        # Mirror HR signals for internal use
        self._mirror_hr_signals()
    
    
    def _trigger_hr_requests(self):
        """
        Triggers HR requests based on system state.
        Red tower light triggers repairs or refills.
        Yellow light is only a warning (no automatic action).
        """
        # Check if HR should act based on red tower light
        if (self.hr.tower_light_red and 
            not (self.hr.repairRequest or self.hr.refillRequest)):
            
            # Don't trigger new refill while one is in progress
            if self._refill_in_progress:
                return
            
            # Prioritize repairs, then handle empty stocks
            if self.sensors.robot_fault:
                self.hr.process_requests(True, False, RepairType.ROBOT.value, 0)
                
            elif self.sensors.flap_fault:
                self.hr.process_requests(True, False, RepairType.FLAP.value, 0)
                
            elif self.sensors.tape_sealer_fault:
                self.hr.process_requests(True, False, RepairType.TAPE_SEALER.value, 0)
                
            elif self.sensors.labeler_fault:
                self.hr.process_requests(True, False, RepairType.LABEL_UNIT.value, 0)
                
            elif self.sensors.conveyor_fault:
                self.hr.process_requests(True, False, RepairType.CONVEYOR.value, 0)
                
            elif self.sensors.carton_blank_empty:
                # Refill only when completely empty (not on low warning)
                self.hr.process_requests(False, True, 0, RefillType.CARTON.value)
                self._refill_in_progress = True
                self._refill_type_in_progress = RefillType.CARTON.value
                
            elif self.sensors.tape_empty:
                self.hr.process_requests(False, True, 0, RefillType.TAPE.value)
                self._refill_in_progress = True
                self._refill_type_in_progress = RefillType.TAPE.value
                
            elif self.sensors.label_empty:
                self.hr.process_requests(False, True, 0, RefillType.LABEL.value)
                self._refill_in_progress = True
                self._refill_type_in_progress = RefillType.LABEL.value
        
        # Failsafe: trigger repairs even if tower lights are out of sync
        self._trigger_failsafe_repairs()
    
    
    def _trigger_failsafe_repairs(self):
        """Triggers repairs for any active faults, regardless of tower lights"""
        if not self.hr.repairRequest and not self.hr.refillRequest:
            fault_mapping = [
                (self.sensors.robot_fault, RepairType.ROBOT.value),
                (self.sensors.flap_fault, RepairType.FLAP.value),
                (self.sensors.tape_sealer_fault, RepairType.TAPE_SEALER.value),
                (self.sensors.labeler_fault, RepairType.LABEL_UNIT.value),
                (self.sensors.conveyor_fault, RepairType.CONVEYOR.value)
            ]
            
            for fault_condition, repair_type in fault_mapping:
                if fault_condition:
                    self.hr.process_requests(True, False, repair_type, 0)
                    break
    
    
    def _process_refill_completion(self):
        """
        Processes completed refills and updates stock levels.
        FIXED: Ensures refills are counted only once by clearing flags
        before resetting the done flag.
        """
        if self.hr.refillDone:
            # Only process if we have a refill in progress
            if self._refill_in_progress:
                self.total_refills += 1  # Count total refills
                
                # Update stock based on refill type
                if self._refill_type_in_progress == RefillType.CARTON.value:
                    self.sensors.carton_stock = min(35, MAX_CARTON_STOCK)
                    self.sensors.carton_blank_empty = False
                    self.carton_refills += 1  # Count carton-specific refills
                    
                elif self._refill_type_in_progress == RefillType.TAPE.value:
                    self.sensors.tape_stock = min(35, MAX_TAPE_STOCK)
                    self.sensors.tape_empty = False
                    self.tape_refills += 1
                    
                elif self._refill_type_in_progress == RefillType.LABEL.value:
                    self.sensors.label_stock = min(35, MAX_LABEL_STOCK)
                    self.sensors.label_empty = False
                    self.label_refills += 1
            
            # CRITICAL FIX: Reset flags BEFORE clearing done flag
            # This prevents race conditions and double counting
            self._refill_in_progress = False
            self._refill_type_in_progress = 0
            
            # Now clear the done flag
            self.hr.refillDone = False
    
    
    def _process_repair_completion(self):
        """Processes completed repairs and resets fault flags"""
        if self.hr.repairDone:
            self.total_repairs += 1
            
            # Reset fault based on repair type
            repair_actions = {
                RepairType.ROBOT.value: ("robot", lambda: setattr(self, 'flap_attempts', 0)),
                RepairType.FLAP.value: ("flap", lambda: setattr(self, 'flap_attempts', 0)),
                RepairType.TAPE_SEALER.value: ("tape", lambda: setattr(self, 'tape_attempts', 0)),
                RepairType.LABEL_UNIT.value: ("label", lambda: setattr(self, 'label_attempts', 0)),
                RepairType.CONVEYOR.value: ("conveyor", lambda: None)
            }
            
            if self.hr.repairType in repair_actions:
                machine_type, reset_func = repair_actions[self.hr.repairType]
                self.sensors.reset_fault(machine_type)
                reset_func()
            
            self.hr.repairDone = False
    
    
    def _mirror_hr_signals(self):
        """Copies HR signals to internal variables for GUI display"""
        self.hr_repair_request = self.hr.repairRequest
        self.hr_refill_request = self.hr.refillRequest
        self.hr_repair_type = self.hr.repairType
        self.hr_refill_type = self.hr.refillType
        self.hr_repair_done = self.hr.repairDone
        self.hr_refill_done = self.hr.refillDone
    
    
    # ============================================
    # SYSTEM STATE MANAGEMENT
    # ============================================
    
    def _is_downtime(self):
        """
        Determines if system is in downtime.
        Downtime conditions: faults, empty stocks, or full conveyor.
        Low stock warnings (yellow light) do NOT cause downtime.
        """
        return (
            self.hr_repair_request or
            self.sensors.conveyor_full or
            self.sensors.robot_fault or
            self.sensors.flap_fault or
            self.sensors.tape_sealer_fault or
            self.sensors.labeler_fault or
            self.sensors.conveyor_fault or
            self.sensors.carton_blank_empty or
            self.sensors.tape_empty or
            self.sensors.label_empty
        )
    
    
    def _handle_downtime(self):
        """Stops all motion during downtime conditions"""
        self.state = "DOWNTIME"
        
        # Stop all actuators
        self.actuators.axis_x_move = False
        self.actuators.axis_z_move = False
        self.actuators.gripper_cmd = 0
        self.actuators.flap_folder_enable = False
        self.actuators.tape_sealer_enable = False
        self.actuators.label_unit_enable = False
        self.actuators.carton_erector_enable = False
        self.actuators.carton_conveyor_motor = False
        self.actuators.final_conveyor_motor = False
        
        # Engage stopper to prevent movement
        self.actuators.carton_conveyor_stopper = True
        
        # Reset robot to idle
        self._reset_robot()
    
    
    def _handle_normal_operation(self):
        """Runs normal production cycle when system is operational"""
        self.state = "NORMAL"
        
        # If using generator-based flow, return early
        if self.generator_flow_enabled:
            return
        
        now = self.env.now
        
        # Process existing box through stations
        self._advance_box_positions(now)
        
        # Run station cycles
        self._run_flap_cycle(now)
        self._run_tape_cycle(now)
        self._run_label_cycle(now)
        
        # Create new carton if conditions are met
        if (self._printer_ready() and
            not self.sensors.loader_pocket_carton_present and
            not self.sensors.carton_blank_empty and
            not self.sensors.conveyor_full):
            self._erect_and_move_carton(now)
        
        self._advance_box_positions(now)
    
    
    # ============================================
    # PRINTER AND BOX FLOW MANAGEMENT
    # ============================================
    
    def _printer_infeed_loop(self):
        """
        Watches printer sensor and feeds boxes into a queue.
        Uses edge detection to add one box per printer pulse.
        """
        box_id = 0
        prev_present = self.sensors.printer_present
        
        while True:
            current_present = self.sensors.printer_present
            
            # Rising edge detection: printer just became present
            if current_present and not prev_present:
                # Create queue if it doesn't exist
                if self.box_queue is None:
                    self.box_queue = simpy.Store(self.env)
                
                # Simulate printer finishing job
                yield self.env.timeout(1.5)
                
                # Create box object
                box = {"id": box_id, "created_at": self.env.now}
                box_id += 1
                
                # Simulate box travel to queue
                yield self.env.timeout(1.0)
                
                # Add to queue (exactly once per pulse)
                yield self.box_queue.put(box)
                
                yield self.env.timeout(1.0)  # Processing delay
            
            # Update previous state for next edge detection
            prev_present = current_present
            
            # Wait for next state change
            wait_time = 0.8 if current_present else 1.2
            yield self.env.timeout(wait_time)
    
    
    def _box_pipeline_loop(self):
        """
        Pulls boxes from printer queue and processes them sequentially.
        Each box goes through the complete packaging pipeline.
        """
        while True:
            # Wait for queue to be created
            if self.box_queue is None:
                yield self.env.timeout(0.5)
                continue
            
            # Get next box from queue (waits if queue empty)
            box = yield self.box_queue.get()
            
            # Brief delay before starting processing
            yield self.env.timeout(0.6)
            
            # Process the box through all stations
            yield from self._process_single_box(box)
    
    
    def _process_single_box(self, box):
        """
        Processes a single box through all stations:
        1. Wait for carton availability
        2. Robot places product in carton
        3. Fold flaps
        4. Apply tape
        5. Apply label
        6. Send to outfeed conveyor
        """
        # Wait for operational conditions
        yield from self._wait_until_operational()
        
        # Ensure carton is available
        while self.sensors.carton_stock <= 0:
            self.sensors.carton_blank_empty = True
            yield self.env.timeout(1.0)  # Wait for refill
        self.sensors.carton_blank_empty = False
        self.sensors.use_carton()
        
        # Position box at pocket station
        self.sensors.current_box_position = 'pocket'
        self._set_position_flags('pocket')
        self.sensors.product_placed_ok = False
        
        yield self.env.timeout(1.2)  # Travel to pocket
        
        # Robot places product in carton
        while not self.sensors.product_placed_ok:
            yield from self._robot_place_cycle()
        
        # Move through stations
        yield from self._move_to_station('flap', travel_time=1.5)
        while not self.sensors.top_flaps_closed_ok:
            yield from self._fold_flaps()
        
        yield from self._move_to_station('tape', travel_time=1.5)
        while not self.sensors.tape_applied_ok:
            yield from self._apply_tape()
        
        yield from self._move_to_station('label', travel_time=1.5)
        while not self.sensors.label_applied_ok:
            yield from self._apply_label()
        
        # Send to outfeed
        yield from self._move_to_outfeed()
    
    
    def _wait_until_operational(self):
        """Pauses box movement while system is in downtime"""
        while self._is_downtime():
            yield self.env.timeout(0.5)  # Check every 0.5 seconds
    
    
    # ============================================
    # STATION OPERATIONS
    # ============================================
    
    def _robot_place_cycle(self):
        """
        Robot picks product from printer and places in carton.
        Sequence: Move to printer → Grab product → Move to carton → Place → Return home.
        """
        yield from self._wait_until_operational()
        
        # Move to printer
        self.robot_state = "MOVE_TO_PRINTER"
        self.actuators.axis_x_move = True
        self.actuators.axis_x_dir = RobotDirection.TO_PRINTER.value
        yield self.env.timeout(1.5)
        
        # Grab product
        self.robot_state = "GRAB_PRODUCT"
        self.actuators.axis_z_move = True
        self.actuators.axis_z_dir = 1  # Down
        yield self.env.timeout(1.0)
        
        # 2% chance of robot fault
        if self.sensors.simulate_machine_fault("robot", 2):
            return
        
        # Move to carton
        self.robot_state = "MOVE_TO_CARTON"
        self.actuators.axis_x_dir = RobotDirection.TO_CARTON.value
        yield self.env.timeout(1.2)
        
        # Place product
        self.robot_state = "PLACE_PRODUCT"
        self.actuators.gripper_cmd = 2  # Open gripper
        yield self.env.timeout(1.0)
        self.sensors.product_placed_ok = True
        
        # Return to home position
        self.robot_state = "RETURN_HOME"
        yield self.env.timeout(0.8)
        
        self.robot_state = "IDLE"
        self.arm_cycles += 1
        self._reset_robot()
    
    
    def _move_to_station(self, target, travel_time):
        """
        Moves box along conveyor to specified station.
        Updates position flags and resets station-specific completion flags.
        """
        yield from self._wait_until_operational()
        
        # Clear current position flags
        self._clear_position_flags()
        
        # Start conveyor movement
        self.actuators.carton_conveyor_motor = True
        self.actuators.carton_conveyor_stopper = False
        
        yield self.env.timeout(travel_time)  # Travel time
        
        # Stop conveyor
        self.actuators.carton_conveyor_motor = False
        self.actuators.carton_conveyor_stopper = True
        
        # Update box position
        self.sensors.move_box_to_next_station()
        
        # Reset station completion flags
        if target == 'flap':
            self.sensors.top_flaps_closed_ok = False
        elif target == 'tape':
            self.sensors.tape_applied_ok = False
        elif target == 'label':
            self.sensors.label_applied_ok = False
        
        # Update position flags
        self._set_position_flags(self.sensors.current_box_position)
        
        # Settling time
        yield self.env.timeout(0.5)
    
    
    def _fold_flaps(self):
        """Runs flap folding operation with fault simulation"""
        yield from self._wait_until_operational()
        
        self.actuators.flap_folder_enable = True
        self.sensors.top_flaps_closed_ok = False
        
        yield self.env.timeout(3.0)  # Flap folding time
        
        # 1% chance of flap fault
        if not self.sensors.simulate_machine_fault("flap", 1):
            self.sensors.top_flaps_closed_ok = True
            self.sensors.flap_fault = False
        
        self.actuators.flap_folder_enable = False
        yield self.env.timeout(0.5)  # Inspection delay
    
    
    def _apply_tape(self):
        """Applies tape to box with stock checking and fault simulation"""
        yield from self._wait_until_operational()
        
        # Wait for tape if empty
        while self.sensors.tape_stock <= 0:
            self.sensors.tape_empty = True
            yield self.env.timeout(1.0)
        
        self.sensors.tape_empty = False
        self.sensors.tape_applied_ok = False
        
        # Run tape sealer
        self.actuators.tape_sealer_enable = True
        yield self.env.timeout(2.5)
        
        # Consume tape
        self.sensors.use_tape()
        
        # 1% chance of tape fault
        if not self.sensors.simulate_machine_fault("tape", 1):
            self.sensors.tape_applied_ok = True
            self.sensors.tape_sealer_fault = False
        
        self.actuators.tape_sealer_enable = False
        yield self.env.timeout(0.7)  # Tape setting time
    
    
    def _apply_label(self):
        """Applies label to box with stock checking and fault simulation"""
        yield from self._wait_until_operational()
        
        # Wait for labels if empty
        while self.sensors.label_stock <= 0:
            self.sensors.label_empty = True
            yield self.env.timeout(1.0)
        
        self.sensors.label_empty = False
        self.sensors.label_applied_ok = False
        
        # Run label applicator
        self.actuators.label_unit_enable = True
        self.label_attempts += 1
        
        yield self.env.timeout(3.0)  # Label application time
        
        # Consume label
        self.sensors.use_label()
        
        # 1% chance of label fault
        if not self.sensors.simulate_machine_fault("label", 1):
            self.sensors.label_applied_ok = True
            self.sensors.labeler_fault = False
        else:
            # Only fault if max attempts reached
            self.sensors.label_applied_ok = False
            self.sensors.labeler_fault = self.label_attempts >= self.max_attempts
        
        self.actuators.label_unit_enable = False
        yield self.env.timeout(0.8)  # Adhesive setting time
    
    
    def _move_to_outfeed(self):
        """Moves completed box to outfeed conveyor and updates KPIs"""
        yield from self._wait_until_operational()
        
        # Start final conveyor
        self.actuators.final_conveyor_motor = True
        yield self.env.timeout(1.5)  # Travel to outfeed
        
        # Update box position and KPIs
        self.sensors.move_box_to_next_station()
        self._clear_position_flags()
        self.actuators.final_conveyor_motor = False
        
        # Update production metrics
        self.packages_completed += 1
        self.label_attempts = 0  # Reset attempt counter
        
        # Reset station completion flags
        self.sensors.label_applied_ok = False
        self.sensors.top_flaps_closed_ok = False
        self.sensors.tape_applied_ok = False
        
        yield self.env.timeout(0.8)  # Spacing between boxes
    
    
    # ============================================
    # HELPER METHODS
    # ============================================
    
    def _printer_ready(self):
        """Checks if printer has product ready"""
        return self.sensors.printer_present
    
    
    def _set_position_flags(self, position):
        """Sets sensor flags for current box position"""
        self._clear_position_flags()
        
        position_actions = {
            'pocket': lambda: setattr(self.sensors, 'loader_pocket_carton_present', True),
            'flap': lambda: setattr(self.sensors, 'box_at_flap', True),
            'tape': lambda: setattr(self.sensors, 'box_at_tape', True),
            'label': lambda: setattr(self.sensors, 'box_at_label', True)
        }
        
        if position in position_actions:
            self.sensors.current_box_position = position
            position_actions[position]()
        else:
            self.sensors.current_box_position = None
    
    
    def _clear_position_flags(self):
        """Clears all box position flags"""
        self.sensors.loader_pocket_carton_present = False
        self.sensors.box_at_flap = False
        self.sensors.box_at_tape = False
        self.sensors.box_at_label = False
    
    
    def _advance_box_positions(self, now):
        """Updates position flags based on current box position"""
        position = self.sensors.current_box_position
        
        if position == 'pocket':
            self.sensors.loader_pocket_carton_present = True
            self.sensors.box_at_flap = False
            self.sensors.box_at_tape = False
            self.sensors.box_at_label = False
            
        elif position == 'flap':
            self.sensors.loader_pocket_carton_present = False
            self.sensors.box_at_flap = True
            self.sensors.box_at_tape = False
            self.sensors.box_at_label = False
            
        elif position == 'tape':
            self.sensors.loader_pocket_carton_present = False
            self.sensors.box_at_flap = False
            self.sensors.box_at_tape = True
            self.sensors.box_at_label = False
            
        elif position == 'label':
            self.sensors.loader_pocket_carton_present = False
            self.sensors.box_at_flap = False
            self.sensors.box_at_tape = False
            self.sensors.box_at_label = True
            
        else:
            self.sensors.loader_pocket_carton_present = False
            self.sensors.box_at_flap = False
            self.sensors.box_at_tape = False
            self.sensors.box_at_label = False
    
    
    def _reset_robot(self):
        """Resets all robot actuators to idle state"""
        self.actuators.axis_x_move = False
        self.actuators.axis_x_dir = RobotDirection.IDLE.value
        self.actuators.axis_z_move = False
        self.actuators.axis_z_dir = 0
        self.actuators.gripper_cmd = 0
        self.actuators.flap_folder_enable = False
        self.actuators.tape_sealer_enable = False
        self.actuators.label_unit_enable = False
        self.actuators.carton_erector_enable = False
    
    
    # ============================================
    # STATION CYCLE METHODS (for non-generator flow)
    # ============================================
    
    def _erect_and_move_carton(self, now):
        """Erects new carton and moves to pocket (non-generator flow)"""
        if self.erect_in_progress:
            if now >= self.erect_busy_until:
                self.actuators.carton_erector_enable = False
                self.erect_in_progress = False
                self.sensors.loader_pocket_carton_present = True
                self.sensors.current_box_position = 'pocket'
                self.actuators.carton_conveyor_motor = False
                self.actuators.carton_conveyor_stopper = True
            return
        
        if self.sensors.use_carton():
            self.actuators.carton_erector_enable = True
            self.actuators.carton_conveyor_motor = True
            self.actuators.carton_conveyor_stopper = False
            self.erect_in_progress = True
            self.erect_busy_until = now + 1.0
    
    
    def _run_flap_cycle(self, now):
        """Runs flap folding cycle (non-generator flow)"""
        if self.flap_in_progress:
            if now >= self.flap_busy_until:
                self.flap_in_progress = False
                self.actuators.flap_folder_enable = False
                
                # 99% success rate
                if random.random() < 0.99:
                    self.sensors.top_flaps_closed_ok = True
                    self.sensors.flap_fault = False
                else:
                    self.sensors.top_flaps_closed_ok = False
                    self.sensors.flap_fault = self.flap_attempts >= self.max_attempts
            return
        
        # Start new flap cycle if conditions met
        if (self.sensors.box_at_flap and
            not self.sensors.top_flaps_closed_ok and
            (not self.sensors.flap_fault or self.flap_attempts < self.max_attempts)):
            
            if self.sensors.flap_fault and self.flap_attempts < self.max_attempts:
                self.sensors.flap_fault = False  # Clear for retry
            
            self.flap_in_progress = True
            self.flap_busy_until = now + 1.0
            self.flap_attempts += 1
            self.actuators.flap_folder_enable = True
            self.sensors.top_flaps_closed_ok = False
    
    
    def _run_tape_cycle(self, now):
        """Runs tape sealing cycle (non-generator flow)"""
        if self.tape_in_progress:
            if now >= self.tape_busy_until:
                self.tape_in_progress = False
                self.actuators.tape_sealer_enable = False
                
                if self.sensors.use_tape():
                    # 99% success rate
                    if random.random() < 0.99:
                        self.sensors.tape_applied_ok = True
                        self.sensors.tape_sealer_fault = False
                    else:
                        self.sensors.tape_applied_ok = False
                        self.sensors.tape_sealer_fault = self.tape_attempts >= self.max_attempts
                else:
                    self.sensors.tape_applied_ok = False
                    self.sensors.tape_empty = True
            return
        
        # Start new tape cycle if conditions met
        if (self.sensors.box_at_tape and
            not self.sensors.tape_applied_ok and
            not self.sensors.tape_empty and
            (not self.sensors.tape_sealer_fault or self.tape_attempts < self.max_attempts)):
            
            if self.sensors.tape_sealer_fault and self.tape_attempts < self.max_attempts:
                self.sensors.tape_sealer_fault = False  # Clear for retry
            
            self.tape_in_progress = True
            self.tape_busy_until = now + 1.0
            self.tape_attempts += 1
            self.actuators.tape_sealer_enable = True
            self.sensors.tape_applied_ok = False
    
    
    def _run_label_cycle(self, now):
        """Runs labeling cycle (non-generator flow)"""
        if self.label_in_progress:
            if now >= self.label_busy_until:
                self.label_in_progress = False
                self.actuators.label_unit_enable = False
                
                if self.sensors.use_label():
                    # 99% success rate
                    if random.random() < 0.99:
                        self.sensors.label_applied_ok = True
                        self.sensors.labeler_fault = False
                    else:
                        self.sensors.label_applied_ok = False
                        self.sensors.labeler_fault = self.label_attempts >= self.max_attempts
                else:
                    self.sensors.label_applied_ok = False
                    self.sensors.label_empty = True
            return
        
        # Start new label cycle if conditions met
        if (self.sensors.box_at_label and
            not self.sensors.label_applied_ok and
            not self.sensors.label_empty and
            (not self.sensors.labeler_fault or self.label_attempts < self.max_attempts)):
            
            if self.sensors.labeler_fault and self.label_attempts < self.max_attempts:
                self.sensors.labeler_fault = False  # Clear for retry
            
            self.label_in_progress = True
            self.label_busy_until = now + 1.0
            self.label_attempts += 1
            self.actuators.label_unit_enable = True
            self.sensors.label_applied_ok = False
    
    
    # ============================================
    # KPI DATA COLLECTION
    # ============================================
    
    def get_kpi_data(self):
        """
        Collects all Key Performance Indicators for GUI display.
        
        Returns:
            Dictionary containing all system metrics and statuses
        """
        # Calculate availability percentage
        total_time = self.operational_time_seconds + self.downtime_seconds
        availability = (self.operational_time_seconds / total_time * 100) if total_time > 0 else 0
        
        return {
            # Production metrics
            'packages_completed': self.packages_completed,
            'arm_cycles': self.arm_cycles,
            'downtime': round(self.downtime_seconds, 1),
            'operational_time': round(self.operational_time_seconds, 1),
            'availability': round(availability, 1),
            
            # Maintenance metrics
            'total_repairs': self.total_repairs,
            'total_refills': self.total_refills,
            'carton_refills': self.carton_refills,
            'tape_refills': self.tape_refills,
            'label_refills': self.label_refills,
            
            # System status
            'station_status': self.state,
            'robot_state': self.robot_state,
            'queue_len': len(self.box_queue.items) if self.box_queue else 0,
            
            # Stock levels
            'carton_stock': self.sensors.carton_stock,
            'tape_stock': self.sensors.tape_stock,
            'label_stock': self.sensors.label_stock,
            'conveyor_count': self.sensors.conveyor_count,
            
            # Tower lights
            'tower_light_green': self.actuators.tower_light_green,
            'tower_light_yellow': self.actuators.tower_light_yellow,
            'tower_light_red': self.actuators.tower_light_red,
            
            # Sensor values (for GUI display)
            'printer_present': self.sensors.printer_present,
            'carton_in_pocket': self.sensors.loader_pocket_carton_present,
            'carton_blank_empty': self.sensors.carton_blank_empty,
            'box_at_flap': self.sensors.box_at_flap,
            'box_at_tape': self.sensors.box_at_tape,
            'box_at_label': self.sensors.box_at_label,
            'robot_fault': self.sensors.robot_fault,
            'flap_fault': self.sensors.flap_fault,
            'tape_fault': self.sensors.tape_sealer_fault,
            'label_fault': self.sensors.labeler_fault,
            'conveyor_fault': self.sensors.conveyor_fault,
            'conveyor_full': self.sensors.conveyor_full,
            'product_placed_ok': self.sensors.product_placed_ok,
            'top_flaps_closed_ok': self.sensors.top_flaps_closed_ok,
            'tape_applied_ok': self.sensors.tape_applied_ok,
            'label_applied_ok': self.sensors.label_applied_ok,
            'tape_empty': self.sensors.tape_empty,
            'tape_low': self.sensors.tape_low,
            'label_empty': self.sensors.label_empty,
            'label_low': self.sensors.label_low,
            
            # Actuator values (for GUI display)
            'axis_x_move': self.actuators.axis_x_move,
            'axis_x_dir': self.actuators.axis_x_dir,
            'axis_z_move': self.actuators.axis_z_move,
            'axis_z_dir': self.actuators.axis_z_dir,
            'gripper_cmd': self.actuators.gripper_cmd,
            'flap_folder_enable': self.actuators.flap_folder_enable,
            'tape_sealer_enable': self.actuators.tape_sealer_enable,
            'label_unit_enable': self.actuators.label_unit_enable,
            'carton_erector_enable': self.actuators.carton_erector_enable,
            'carton_conveyor_motor': self.actuators.carton_conveyor_motor,
            'carton_conveyor_stopper': self.actuators.carton_conveyor_stopper,
            'final_conveyor_motor': self.actuators.final_conveyor_motor,
            
            # HR values (for GUI display)
            'hr_repair_request': self.hr_repair_request,
            'hr_refill_request': self.hr_refill_request,
            'hr_repair_type': self.hr_repair_type,
            'hr_refill_type': self.hr_refill_type,
            'hr_repair_done': self.hr_repair_done,
            'hr_refill_done': self.hr_refill_done,
            'hr_tower_light_green': self.hr_tower_light_green,
            'hr_tower_light_yellow': self.hr_tower_light_yellow,
            'hr_tower_light_red': self.hr_tower_light_red,
        }


# ============================================
# SCADA GUI CLASS
# ============================================

class PackagingSCADA:
    """
    SCADA (Supervisory Control and Data Acquisition) GUI.
    Provides real-time visualization of the packaging station.
    Displays sensors, actuators, KPIs, and system status.
    """
    
    def __init__(self):
        """Initialize SCADA window and GUI components"""
        self.root = tk.Tk()
        self.root.title("3D Printer Packaging Station SCADA")
        self.root.geometry("1400x800")
        
        # Simulation control variables
        self.simulation = None
        self.simulation_thread = None
        self.running = False
        self.speed_factor = 1.0
        
        # Setup GUI layout
        self.setup_gui()
    
    
    def setup_gui(self):
        """Creates and arranges all GUI widgets"""
        # Main container
        main_container = ttk.Frame(self.root, padding="10")
        main_container.pack(fill=tk.BOTH, expand=True)
        
        # Top control bar
        self._create_top_bar(main_container)
        
        # Content area (left and right columns)
        content_frame = ttk.Frame(main_container)
        content_frame.pack(fill=tk.BOTH, expand=True)
        
        # Left column with scrollable content
        left_column = self._create_left_column(content_frame)
        
        # Right column with scrollable sensors/actuators
        right_column = self._create_right_column(content_frame)
        
        # Fill left column sections
        self._create_status_section(left_column)
        self._create_production_metrics_section(left_column)
        self._create_stock_levels_section(left_column)
        self._create_refills_counter_section(left_column)
        self._create_hr_visualization_section(left_column)
        
        # Fill right column sections
        self._create_sensors_section(right_column)
        self._create_actuators_section(right_column)
        
        # Log section at bottom
        self._create_log_section(main_container)
    
    
    def _create_top_bar(self, parent):
        """Creates top control bar with title and buttons"""
        top_frame = ttk.Frame(parent)
        top_frame.pack(fill=tk.X, pady=(0, 10))
        
        # Title
        title_label = ttk.Label(
            top_frame,
            text="3D Printer Packaging Station - SCADA System",
            font=("Arial", 18, "bold"),
        )
        title_label.pack(side=tk.LEFT, padx=10)
        
        # Control buttons frame
        control_frame = ttk.Frame(top_frame)
        control_frame.pack(side=tk.RIGHT, padx=10)
        
        # Start button
        self.start_button = ttk.Button(
            control_frame,
            text="▶ Start Simulation",
            command=self.start_simulation,
            width=15,
        )
        self.start_button.pack(side=tk.LEFT, padx=5)
        
        # Stop button
        self.stop_button = ttk.Button(
            control_frame,
            text="⏹ Stop Simulation",
            command=self.stop_simulation,
            state="disabled",
            width=15,
        )
        self.stop_button.pack(side=tk.LEFT, padx=5)
        
        # Speed control
        ttk.Label(control_frame, text="Speed:").pack(side=tk.LEFT, padx=5)
        self.speed_var = tk.StringVar(value="1x Normal")
        self.speed_combo = ttk.Combobox(
            control_frame,
            state="readonly",
            values=["0.5x Slow", "1x Normal", "2x Fast", "5x Turbo"],
            width=10,
            textvariable=self.speed_var,
        )
        self.speed_combo.pack(side=tk.LEFT, padx=5)
        self.speed_combo.bind("<<ComboboxSelected>>", self.on_speed_change)
    
    
    def _create_left_column(self, parent):
        """Creates scrollable left column for status and metrics"""
        left_container = ttk.Frame(parent)
        left_container.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))
        
        # Canvas with scrollbar for left column
        left_canvas = tk.Canvas(left_container)
        left_scrollbar = ttk.Scrollbar(left_container, orient="vertical", command=left_canvas.yview)
        left_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        left_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Frame inside canvas
        left_column = ttk.Frame(left_canvas)
        left_canvas.create_window((0, 0), window=left_column, anchor="nw")
        left_canvas.configure(yscrollcommand=left_scrollbar.set)
        
        # Configure scroll region
        left_column.bind(
            "<Configure>",
            lambda e: left_canvas.configure(scrollregion=left_canvas.bbox("all")),
        )
        
        # Mouse wheel scroll support
        left_canvas.bind_all(
            "<MouseWheel>", 
            lambda e: left_canvas.yview_scroll(int(-1*(e.delta/120)), "units")
        )
        
        return left_column
    
    
    def _create_right_column(self, parent):
        """Creates right column for sensors and actuators"""
        right_column = ttk.Frame(parent)
        right_column.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(10, 0))
        return right_column
    
    
    def _create_status_section(self, parent):
        """Creates system status section"""
        status_frame = ttk.LabelFrame(parent, text="System Status", padding="10")
        status_frame.pack(fill=tk.X, pady=(0, 10))
        
        status_grid = ttk.Frame(status_frame)
        status_grid.pack()
        
        # Station status
        ttk.Label(status_grid, text="Station Status:", 
                 font=("Arial", 11, "bold")).grid(row=0, column=0, sticky=tk.W, pady=5)
        self.status_var = tk.StringVar(value="IDLE")
        self.status_label = ttk.Label(
            status_grid, textvariable=self.status_var, font=("Arial", 11, "bold")
        )
        self.status_label.grid(row=0, column=1, sticky=tk.W, padx=10, pady=5)
        
        # Robot state
        ttk.Label(status_grid, text="Robot State:", 
                 font=("Arial", 11)).grid(row=1, column=0, sticky=tk.W, pady=5)
        self.robot_state_var = tk.StringVar(value="IDLE")
        ttk.Label(status_grid, textvariable=self.robot_state_var).grid(
            row=1, column=1, sticky=tk.W, padx=10, pady=5
        )
        
        # Tower lights
        ttk.Label(status_grid, text="Tower Lights:", 
                 font=("Arial", 11)).grid(row=2, column=0, sticky=tk.W, pady=5)
        lights_frame = ttk.Frame(status_grid)
        lights_frame.grid(row=2, column=1, sticky=tk.W, padx=10, pady=5)
        
        # Green light (normal)
        self.light_green = tk.Label(lights_frame, text="●", font=("Arial", 24), fg="gray")
        self.light_green.pack(side=tk.LEFT, padx=5)
        ttk.Label(lights_frame, text="Normal").pack(side=tk.LEFT, padx=2)
        
        # Yellow light (low stock)
        self.light_yellow = tk.Label(lights_frame, text="●", font=("Arial", 24), fg="gray")
        self.light_yellow.pack(side=tk.LEFT, padx=20)
        ttk.Label(lights_frame, text="Low Stock").pack(side=tk.LEFT, padx=2)
        
        # Red light (fault)
        self.light_red = tk.Label(lights_frame, text="●", font=("Arial", 24), fg="gray")
        self.light_red.pack(side=tk.LEFT, padx=20)
        ttk.Label(lights_frame, text="Fault").pack(side=tk.LEFT, padx=2)
        
        # Printer queue length
        ttk.Label(status_grid, text="Printer Queue:", 
                 font=("Arial", 11)).grid(row=3, column=0, sticky=tk.W, pady=5)
        self.queue_len_var = tk.StringVar(value="0")
        ttk.Label(status_grid, textvariable=self.queue_len_var).grid(
            row=3, column=1, sticky=tk.W, padx=10, pady=5
        )
    
    
    def _create_production_metrics_section(self, parent):
        """Creates production metrics section"""
        production_frame = ttk.LabelFrame(
            parent, text="Production Metrics", padding="10"
        )
        production_frame.pack(fill=tk.X, pady=(0, 10))
        
        prod_grid = ttk.Frame(production_frame)
        prod_grid.pack()
        
        # Define metrics to display
        metrics = [
            ("Packages Completed:", "packages_var"),
            ("Robot Cycles:", "arm_cycles_var"),
            ("Availability:", "availability_var"),
            ("Operational Time:", "operational_time_var"),
            ("Downtime:", "downtime_var"),
            ("Total Repairs:", "total_repairs_var"),
            ("Total Refills:", "total_refills_var"),
        ]
        
        # Create labels for each metric
        for i, (label, var_name) in enumerate(metrics):
            ttk.Label(prod_grid, text=label, font=("Arial", 10)).grid(
                row=i, column=0, sticky=tk.W, padx=10, pady=3
            )
            setattr(self, var_name, tk.StringVar(value="0"))
            ttk.Label(
                prod_grid,
                textvariable=getattr(self, var_name),
                font=("Arial", 10, "bold"),
            ).grid(row=i, column=1, sticky=tk.W, padx=10, pady=3)
    
    
    def _create_stock_levels_section(self, parent):
        """Creates stock levels section with progress bars"""
        stock_frame = ttk.LabelFrame(parent, text="Stock Levels", padding="10")
        stock_frame.pack(fill=tk.X, pady=(0, 10))
        
        # Cartons
        ttk.Label(stock_frame, text="Cartons:").pack(anchor=tk.W)
        self.carton_progress = ttk.Progressbar(
            stock_frame, length=300, mode="determinate"
        )
        self.carton_progress.pack(fill=tk.X, pady=(0, 10))
        self.carton_progress["value"] = (INITIAL_CARTON_STOCK / MAX_CARTON_STOCK) * 100
        self.carton_label = ttk.Label(
            stock_frame, text=f"{INITIAL_CARTON_STOCK:.0f}/{MAX_CARTON_STOCK}"
        )
        self.carton_label.pack(anchor=tk.W)
        
        # Tape
        ttk.Label(stock_frame, text="Tape:").pack(anchor=tk.W)
        self.tape_progress = ttk.Progressbar(
            stock_frame, length=300, mode="determinate"
        )
        self.tape_progress.pack(fill=tk.X, pady=(0, 10))
        self.tape_progress["value"] = (INITIAL_TAPE_STOCK / MAX_TAPE_STOCK) * 100
        self.tape_label = ttk.Label(
            stock_frame, text=f"{INITIAL_TAPE_STOCK:.0f}/{MAX_TAPE_STOCK}"
        )
        self.tape_label.pack(anchor=tk.W)
        
        # Labels
        ttk.Label(stock_frame, text="Labels:").pack(anchor=tk.W)
        self.label_progress = ttk.Progressbar(
            stock_frame, length=300, mode="determinate"
        )
        self.label_progress.pack(fill=tk.X, pady=(0, 10))
        self.label_progress["value"] = (INITIAL_LABEL_STOCK / MAX_LABEL_STOCK) * 100
        self.label_label = ttk.Label(
            stock_frame, text=f"{INITIAL_LABEL_STOCK:.0f}/{MAX_LABEL_STOCK}"
        )
        self.label_label.pack(anchor=tk.W)
    
    
    def _create_refills_counter_section(self, parent):
        """Creates refills counter section"""
        refill_frame = ttk.LabelFrame(parent, text="Refills Counter", padding="10")
        refill_frame.pack(fill=tk.X, pady=(0, 10))
        
        refill_grid = ttk.Frame(refill_frame)
        refill_grid.pack()
        
        refills = [
            ("Carton Refills:", "carton_refills_var"),
            ("Tape Refills:", "tape_refills_var"),
            ("Label Refills:", "label_refills_var"),
        ]
        
        for i, (label, var_name) in enumerate(refills):
            ttk.Label(refill_grid, text=label).grid(
                row=i, column=0, sticky=tk.W, padx=10, pady=3
            )
            setattr(self, var_name, tk.StringVar(value="0"))
            ttk.Label(refill_grid, textvariable=getattr(self, var_name)).grid(
                row=i, column=1, sticky=tk.W, padx=10, pady=3
            )
    
    
    def _create_hr_visualization_section(self, parent):
        """Creates Human Resource visualization section"""
        hr_frame = ttk.LabelFrame(parent, text="Human Resource", padding="10")
        hr_frame.pack(fill=tk.X, pady=(10, 0))
        
        hr_grid = ttk.Frame(hr_frame)
        hr_grid.pack()
        
        hr_rows = [
            ("Repair Request", "hr_repair_req_var"),
            ("Repair Type", "hr_repair_type_var"),
            ("Repair Done", "hr_repair_done_var"),
            ("Refill Request", "hr_refill_req_var"),
            ("Refill Type", "hr_refill_type_var"),
            ("Refill Done", "hr_refill_done_var"),
            ("Tower Green", "hr_tower_green_var"),
            ("Tower Yellow", "hr_tower_yellow_var"),
            ("Tower Red", "hr_tower_red_var"),
        ]
        
        for i, (label, var_name) in enumerate(hr_rows):
            ttk.Label(hr_grid, text=label).grid(
                row=i, column=0, sticky=tk.W, padx=8, pady=2
            )
            setattr(self, var_name, tk.StringVar(value="False/0"))
            ttk.Label(hr_grid, textvariable=getattr(self, var_name), width=12).grid(
                row=i, column=1, sticky=tk.W, padx=4, pady=2
            )
            
            # Status indicator dot
            indicator = tk.Label(hr_grid, text="●", font=("Arial", 16))
            indicator.grid(row=i, column=2, padx=6, pady=2)
            setattr(self, var_name + "_indicator", indicator)
    
    
    def _create_sensors_section(self, parent):
        """Creates scrollable sensors status section"""
        sensors_frame = ttk.LabelFrame(parent, text="Sensors Status", padding="10")
        sensors_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10), padx=(6, 0))
        
        # Create scrollable canvas
        sensors_canvas = tk.Canvas(sensors_frame)
        sensors_scrollbar = ttk.Scrollbar(
            sensors_frame, orient="vertical", command=sensors_canvas.yview
        )
        sensors_scrollable_frame = ttk.Frame(sensors_canvas)
        
        # Configure scroll region
        sensors_scrollable_frame.bind(
            "<Configure>",
            lambda e: sensors_canvas.configure(
                scrollregion=sensors_canvas.bbox("all")
            ),
        )
        
        # Place frame in canvas
        sensors_canvas.create_window(
            (0, 0), window=sensors_scrollable_frame, anchor="nw"
        )
        sensors_canvas.configure(yscrollcommand=sensors_scrollbar.set)
        
        # Define important sensors with descriptions
        important_sensors = [
            # 3D-Printer Loader
            ("printer_present", "printer_present_var", "3D-Printer Loader (Mariam Nasr station)"),
            
            # Carton Erector + Loader Conveyor
            ("carton_stock (cartons available)", "carton_stock_var", "Carton Erector + Loader Conveyor"),
            ("carton_blank_empty (no cartons in stack)", "carton_blank_empty_var", "Carton Erector + Loader Conveyor"),
            
            # Loader Pocket
            ("loader_pocket_carton_present (carton in pocket)", "carton_in_pocket_var", "Loader Pocket"),
            ("product_placed_ok (product placed inside carton)", "product_placed_var", "Loader Pocket"),
            
            # Robotic Arm
            ("robot_fault (robot failure)", "robot_fault_var", "Robotic Arm"),
            
            # Flap Folding Unit
            ("box_at_flap (carton arrived at flap station)", "box_at_flap_var", "Flap Folding Unit"),
            ("top_flaps_closed_ok (flaps correctly folded)", "flaps_closed_var", "Flap Folding Unit"),
            ("flap_fault (flap folder failure)", "flap_fault_var", "Flap Folding Unit"),
            
            # Tape Sealer
            ("box_at_tape (carton at tape station)", "box_at_tape_var", "Tape Sealer"),
            ("tape_stock (tape amount)", "tape_stock_var", "Tape Sealer"),
            ("tape_empty", "tape_empty_var", "Tape Sealer"),
            ("tape_low", "tape_low_var", "Tape Sealer"),
            ("tape_applied_ok (tape correctly applied)", "tape_applied_var", "Tape Sealer"),
            ("tape_sealer_fault (tape unit failure)", "tape_fault_var", "Tape Sealer"),
            
            # Labeling Unit
            ("box_at_label (carton at label station)", "box_at_label_var", "Labeling Unit"),
            ("label_stock (labels amount)", "label_stock_var", "Labeling Unit"),
            ("label_empty", "label_empty_var", "Labeling Unit"),
            ("label_low", "label_low_var", "Labeling Unit"),
            ("label_applied_ok (label correctly applied)", "label_applied_var", "Labeling Unit"),
            ("labeler_fault (label unit failure)", "label_fault_var", "Labeling Unit"),
            
            # Final Conveyor
            ("conveyor_full (100 boxes capacity reached)", "conveyor_full_var", "Final Conveyor"),
            ("conveyor_fault (conveyor failure)", "conveyor_fault_var", "Final Conveyor"),
        ]
        
        # Create sensor display rows
        for _, (label, var_name, part) in enumerate(important_sensors):
            frame = ttk.Frame(sensors_scrollable_frame)
            frame.pack(fill=tk.X, padx=5, pady=2)
            
            # Sensor label
            ttk.Label(frame, text=f"{label}", width=40, anchor=tk.W).grid(
                row=0, column=0, padx=(0, 8), sticky=tk.W
            )
            
            # Value display
            setattr(self, var_name, tk.StringVar(value="False"))
            value_label = ttk.Label(
                frame, textvariable=getattr(self, var_name), width=10
            )
            value_label.grid(row=0, column=1, padx=(12, 12), sticky=tk.W)
            
            # Status indicator
            indicator = tk.Label(frame, text="●", font=("Arial", 16))
            indicator.grid(row=0, column=2, padx=(8, 12), sticky=tk.W)
            setattr(self, var_name + "_indicator", indicator)
            
            # Part description
            ttk.Label(
                frame, text=f"[{part}]", width=52, 
                foreground="gray", anchor=tk.W, wraplength=520
            ).grid(row=0, column=3, padx=(12, 0), sticky=tk.W)
        
        # Pack canvas and scrollbar
        sensors_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sensors_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    
    
    def _create_actuators_section(self, parent):
        """Creates scrollable actuators status section"""
        actuators_frame = ttk.LabelFrame(parent, text="Actuators Status", padding="10")
        actuators_frame.pack(fill=tk.BOTH, expand=True, padx=(6, 0))
        
        # Create scrollable canvas
        actuators_canvas = tk.Canvas(actuators_frame)
        actuators_scrollbar = ttk.Scrollbar(
            actuators_frame, orient="vertical", command=actuators_canvas.yview
        )
        actuators_scrollable_frame = ttk.Frame(actuators_canvas)
        
        # Configure scroll region
        actuators_scrollable_frame.bind(
            "<Configure>",
            lambda e: actuators_canvas.configure(
                scrollregion=actuators_canvas.bbox("all")
            ),
        )
        
        # Place frame in canvas
        actuators_canvas.create_window(
            (0, 0), window=actuators_scrollable_frame, anchor="nw"
        )
        actuators_canvas.configure(yscrollcommand=actuators_scrollbar.set)
        
        # Define important actuators with descriptions
        important_actuators = [
            # Carton Erector + Loader Conveyor
            ("carton_erector_enable (open one carton)", "carton_erector_var", "Carton Erector + Loader Conveyor"),
            ("carton_conveyor_motor (move carton)", "carton_conveyor_motor_var", "Carton Erector + Loader Conveyor"),
            ("carton_conveyor_stopper (stop at pocket)", "carton_conveyor_stopper_var", "Carton Erector + Loader Conveyor"),
            
            # Robotic Arm
            ("axis_x_move (horizontal move)", "axis_x_move_var", "Robotic Arm"),
            ("axis_x_dir (pick / place / home)", "axis_x_dir_var", "Robotic Arm"),
            ("axis_z_move (vertical move)", "axis_z_move_var", "Robotic Arm"),
            ("axis_z_dir (up / down)", "axis_z_dir_var", "Robotic Arm"),
            ("gripper_cmd (open / close)", "gripper_cmd_var", "Robotic Arm"),
            
            # Flap Folding Unit
            ("flap_folder_enable", "flap_folder_var", "Flap Folding Unit"),
            
            # Tape Sealer
            ("tape_sealer_enable", "tape_sealer_var", "Tape Sealer"),
            
            # Labeling Unit
            ("label_unit_enable", "label_unit_var", "Labeling Unit"),
            
            # Final Conveyor
            ("final_conveyor_motor", "final_conveyor_var", "Final Conveyor"),
            
            # HR (for completeness)
            ("HR repair_request", "hr_repair_var", "HR"),
            ("HR refill_request", "hr_refill_var", "HR"),
        ]
        
        # Create actuator display rows
        for _, (label, var_name, part) in enumerate(important_actuators):
            frame = ttk.Frame(actuators_scrollable_frame)
            frame.pack(fill=tk.X, padx=5, pady=2)
            
            # Actuator label
            ttk.Label(frame, text=label, width=40, anchor=tk.W).grid(
                row=0, column=0, padx=(0, 8), sticky=tk.W
            )
            
            # Value display
            setattr(self, var_name, tk.StringVar(value="False/0"))
            ttk.Label(
                frame, textvariable=getattr(self, var_name), width=12
            ).grid(row=0, column=1, padx=(12, 12), sticky=tk.W)
            
            # Status indicator (square for actuators)
            indicator = tk.Label(frame, text="◼", font=("Arial", 16))
            indicator.grid(row=0, column=2, padx=(8, 12), sticky=tk.W)
            setattr(self, var_name + "_indicator", indicator)
            
            # Part description
            ttk.Label(
                frame, text=f"[{part}]", width=52, 
                foreground="gray", anchor=tk.W, wraplength=520
            ).grid(row=0, column=3, padx=(12, 0), sticky=tk.W)
        
        # Pack canvas and scrollbar
        actuators_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        actuators_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    
    
    def _create_log_section(self, parent):
        """Creates log text area at bottom"""
        log_frame = ttk.LabelFrame(parent, text="Simulation Log", padding="10")
        log_frame.pack(fill=tk.X, pady=(10, 0))
        
        self.log_text = scrolledtext.ScrolledText(log_frame, height=8, width=150)
        self.log_text.pack(fill=tk.BOTH, expand=True)
    
    
    # ============================================
    # GUI CONTROL METHODS
    # ============================================
    
    def log_message(self, message):
        """Adds timestamped message to log window"""
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")
        self.log_text.see(tk.END)  # Auto-scroll to bottom
    
    
    def on_speed_change(self, event=None):
        """Handles speed selection change"""
        self._apply_speed_selection()
    
    
    def _apply_speed_selection(self):
        """Applies selected simulation speed factor"""
        speed_mapping = {
            "0.5x Slow": 0.5,
            "1x Normal": 1.0,
            "2x Fast": 2.0,
            "5x Turbo": 5.0,
        }
        self.speed_factor = speed_mapping.get(self.speed_var.get(), 1.0)
    
    
    def gui_callback(self, action, data=None):
        """
        Callback function for PLC to update GUI.
        Runs in main thread to avoid threading issues.
        """
        if action == "update_kpi":
            self.root.after(0, lambda: self.update_display(data))
        elif action == "log":
            self.root.after(0, lambda: self.log_message(data))
    
    
    def update_display(self, data):
        """
        Updates all GUI elements with current simulation data.
        
        Args:
            data: Dictionary containing all KPI and status data from PLC
        """
        try:
            # System status
            self.status_var.set(data["station_status"])
            self.robot_state_var.set(data["robot_state"])
            self.queue_len_var.set(str(data["queue_len"]))
            
            # Status color coding
            status_color = "green"
            if data["station_status"] == "DOWNTIME":
                status_color = "red"
            elif data["station_status"] == "NORMAL":
                status_color = "blue"
            self.status_label.configure(foreground=status_color)
            
            # Tower lights
            self.light_green.config(fg="green" if data["tower_light_green"] else "gray")
            self.light_yellow.config(fg="yellow" if data["tower_light_yellow"] else "gray")
            self.light_red.config(fg="red" if data["tower_light_red"] else "gray")
            
            # Production metrics
            self.packages_var.set(str(data["packages_completed"]))
            self.arm_cycles_var.set(str(data["arm_cycles"]))
            self.availability_var.set(f"{data['availability']}%")
            self.operational_time_var.set(f"{data['operational_time']}s")
            self.downtime_var.set(f"{data['downtime']}s")
            self.total_repairs_var.set(str(data["total_repairs"]))
            self.total_refills_var.set(str(data["total_refills"]))
            
            # Stock levels and progress bars
            carton_percent = (data["carton_stock"] / MAX_CARTON_STOCK) * 100
            tape_percent = (data["tape_stock"] / MAX_TAPE_STOCK) * 100
            label_percent = (data["label_stock"] / MAX_LABEL_STOCK) * 100
            
            self.carton_progress["value"] = carton_percent
            self.tape_progress["value"] = tape_percent
            self.label_progress["value"] = label_percent
            
            self.carton_label.config(text=f"{data['carton_stock']:.0f}/{MAX_CARTON_STOCK}")
            self.tape_label.config(text=f"{data['tape_stock']:.0f}/{MAX_TAPE_STOCK}")
            self.label_label.config(text=f"{data['label_stock']:.0f}/{MAX_LABEL_STOCK}")
            
            # Refill counters
            self.carton_refills_var.set(str(data["carton_refills"]))
            self.tape_refills_var.set(str(data["tape_refills"]))
            self.label_refills_var.set(str(data["label_refills"]))
            
            # Update sensors display
            self._update_sensors_display(data)
            
            # Update actuators display
            self._update_actuators_display(data)
            
            # Update HR display
            self._update_hr_display(data)
            
        except Exception as e:
            print(f"GUI update error: {e}")
    
    
    def _update_sensors_display(self, data):
        """Updates all sensor displays in GUI"""
        sensor_mapping = {
            "printer_present_var": data["printer_present"],
            "carton_stock_var": data["carton_stock"],
            "carton_blank_empty_var": data["carton_blank_empty"],
            "carton_in_pocket_var": data["carton_in_pocket"],
            "product_placed_var": data["product_placed_ok"],
            "robot_fault_var": data["robot_fault"],
            "box_at_flap_var": data["box_at_flap"],
            "flaps_closed_var": data["top_flaps_closed_ok"],
            "flap_fault_var": data["flap_fault"],
            "box_at_tape_var": data["box_at_tape"],
            "tape_stock_var": data["tape_stock"],
            "tape_empty_var": data["tape_empty"],
            "tape_low_var": data["tape_low"],
            "tape_applied_var": data["tape_applied_ok"],
            "tape_fault_var": data["tape_fault"],
            "box_at_label_var": data["box_at_label"],
            "label_stock_var": data["label_stock"],
            "label_empty_var": data["label_empty"],
            "label_low_var": data["label_low"],
            "label_applied_var": data["label_applied_ok"],
            "label_fault_var": data["label_fault"],
            "conveyor_full_var": data["conveyor_full"],
            "conveyor_fault_var": data["conveyor_fault"],
        }
        
        for var_name, value in sensor_mapping.items():
            var = getattr(self, var_name)
            var.set(str(value))
            
            # Update indicator color
            indicator = getattr(self, var_name + "_indicator", None)
            if indicator:
                if isinstance(value, bool):
                    indicator.config(fg="green" if value else "red")
                else:
                    # For numeric values, show green if not zero/false
                    indicator.config(fg="green" if str(value) != "False" and str(value) != "0" else "red")
    
    
    def _update_actuators_display(self, data):
        """Updates all actuator displays in GUI"""
        # Set actuator values
        self.axis_x_move_var.set("On" if data["axis_x_move"] else "Off")
        self.axis_x_dir_var.set(str(data["axis_x_dir"]))
        self.axis_z_move_var.set("On" if data["axis_z_move"] else "Off")
        self.axis_z_dir_var.set(str(data["axis_z_dir"]))
        self.gripper_cmd_var.set(str(data["gripper_cmd"]))
        self.flap_folder_var.set("On" if data["flap_folder_enable"] else "Off")
        self.tape_sealer_var.set("On" if data["tape_sealer_enable"] else "Off")
        self.label_unit_var.set("On" if data["label_unit_enable"] else "Off")
        self.carton_erector_var.set("On" if data["carton_erector_enable"] else "Off")
        self.final_conveyor_var.set("On" if data["final_conveyor_motor"] else "Off")
        self.hr_repair_var.set(str(data["hr_repair_request"]))
        self.hr_refill_var.set(str(data["hr_refill_request"]))
        
        # Update actuator indicators
        actuator_bool_map = {
            "axis_x_move_var": data["axis_x_move"],
            "axis_z_move_var": data["axis_z_move"],
            "flap_folder_var": data["flap_folder_enable"],
            "tape_sealer_var": data["tape_sealer_enable"],
            "label_unit_var": data["label_unit_enable"],
            "carton_erector_var": data["carton_erector_enable"],
            "carton_conveyor_motor_var": data["carton_conveyor_motor"],
            "carton_conveyor_stopper_var": data["carton_conveyor_stopper"],
            "final_conveyor_var": data["final_conveyor_motor"],
        }
        
        for var_name, value in actuator_bool_map.items():
            indicator = getattr(self, var_name + "_indicator", None)
            if indicator:
                indicator.config(fg="green" if value else "gray")
    
    
    def _update_hr_display(self, data):
        """Updates Human Resource display in GUI"""
        # Set HR values
        self.hr_repair_req_var.set(str(data["hr_repair_request"]))
        self.hr_repair_type_var.set(str(data["hr_repair_type"]))
        self.hr_repair_done_var.set(str(data["hr_repair_done"]))
        self.hr_refill_req_var.set(str(data["hr_refill_request"]))
        self.hr_refill_type_var.set(str(data["hr_refill_type"]))
        self.hr_refill_done_var.set(str(data["hr_refill_done"]))
        self.hr_tower_green_var.set(str(data["hr_tower_light_green"]))
        self.hr_tower_yellow_var.set(str(data["hr_tower_light_yellow"]))
        self.hr_tower_red_var.set(str(data["hr_tower_light_red"]))
        
        # Update HR indicators
        hr_indicator_map = {
            "hr_repair_req_var": data["hr_repair_request"],
            "hr_repair_done_var": data["hr_repair_done"],
            "hr_refill_req_var": data["hr_refill_request"],
            "hr_refill_done_var": data["hr_refill_done"],
            "hr_tower_green_var": data["hr_tower_light_green"],
            "hr_tower_yellow_var": data["hr_tower_light_yellow"],
            "hr_tower_red_var": data["hr_tower_light_red"],
        }
        
        for var_name, value in hr_indicator_map.items():
            indicator = getattr(self, var_name + "_indicator", None)
            if indicator:
                indicator.config(fg="green" if value else "red")
    
    
    # ============================================
    # SIMULATION CONTROL METHODS
    # ============================================
    
    def start_simulation(self):
        """Starts the simulation in a separate thread"""
        if not self.running:
            self.running = True
            self.start_button.config(state="disabled")
            self.stop_button.config(state="normal")
            self._apply_speed_selection()
            
            # Create simulation environment and components
            env = simpy.Environment()
            sensors = SensorsComponent(env)
            actuators = ActuatorsComponent(env)
            hr = HumanResourceComponent(env)
            
            # Create PLC with GUI callback
            self.simulation = PLCComponent(env, sensors, actuators, hr, self.gui_callback)
            
            # Start simulation in background thread
            self.simulation_thread = threading.Thread(
                target=self.run_simulation, args=(env,)
            )
            self.simulation_thread.daemon = True
            self.simulation_thread.start()
            
            # Log startup message
            self.log_message("🏭 Simulation started - 3D Printer Packaging Station")
            self.log_message("🤖 4 Components: Sensors / PLC / Actuators / HR")
            self.log_message("⚙️ Signal names aligned with VSI definition")
            self.log_message("=" * 70)
    
    
    def stop_simulation(self):
        """Stops the running simulation"""
        if self.running:
            self.running = False
            self.start_button.config(state="normal")
            self.stop_button.config(state="disabled")
            self.log_message("🛑 Simulation stopped by user.")
    
    
    def run_simulation(self, env):
        """
        Runs the simulation for specified duration.
        This runs in a separate thread to keep GUI responsive.
        """
        try:
            simulation_time = 9000  # 9000 seconds (2.5 hours simulation time)
            last_log_time = 0
            
            while env.now < simulation_time and self.running:
                # Calculate time step based on speed factor
                step = 0.1 * max(self.speed_factor, 0.1)
                env.run(until=env.now + step)
                
                # Throttle GUI updates to prevent freezing
                time.sleep(max(0.01, 0.05 / max(self.speed_factor, 0.1)))
                
                # Log status every second
                if int(env.now) > last_log_time:
                    self.log_message(
                        f"⏱️ Time: {env.now:.1f}s | "
                        f"Packages: {self.simulation.packages_completed} | "
                        f"Status: {self.simulation.state}"
                    )
                    last_log_time = int(env.now)
            
            # Simulation completed successfully
            if self.running:
                self.log_message("✅ Simulation completed successfully!")
                kpi_data = self.simulation.get_kpi_data()
                
                # Display final results
                self.log_message("\n" + "=" * 60)
                self.log_message("FINAL SIMULATION RESULTS")
                self.log_message("=" * 60)
                self.log_message(f"Total Packages: {kpi_data['packages_completed']}")
                self.log_message(f"Total Robot Cycles: {kpi_data['arm_cycles']}")
                self.log_message(f"Availability: {kpi_data['availability']}%")
                self.log_message(f"Total Repairs: {kpi_data['total_repairs']}")
                self.log_message(f"Total Refills: {kpi_data['total_refills']}")
                self.log_message(f"Operational Time: {kpi_data['operational_time']}s")
                self.log_message(f"Downtime: {kpi_data['downtime']}s")
        
        except Exception as e:
            self.log_message(f"❌ Simulation error: {str(e)}")
        
        finally:
            # Ensure GUI buttons are reset
            self.running = False
            self.root.after(0, lambda: self.stop_button.config(state="disabled"))
            self.root.after(0, lambda: self.start_button.config(state="normal"))


# ============================================
# MAIN ENTRY POINT
# ============================================

def main():
    """
    Main function to start the SCADA application.
    Creates SCADA instance and starts Tkinter main loop.
    """
    scada = PackagingSCADA()
    scada.root.mainloop()


if __name__ == "__main__":
    main()