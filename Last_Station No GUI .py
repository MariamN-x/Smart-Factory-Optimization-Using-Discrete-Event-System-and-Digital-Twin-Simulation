"""
3D Printer Packaging Station (NO SCADA)
======================================
SimPy simulation of a packaging line with 4 components:
1) Sensors
2) PLC (control logic + box pipeline)
3) Actuators
4) Human Resource (repairs + refills)

No Tkinter / no GUI / no threading.
Logs go to console.
"""

import simpy
import random
from enum import Enum

# ============================================
# ENUMS AND CONSTANTS
# ============================================

class RepairType(Enum):
    ROBOT = 1
    FLAP = 2
    TAPE_SEALER = 3
    LABEL_UNIT = 4
    CONVEYOR = 5


class RefillType(Enum):
    CARTON = 1
    TAPE = 2
    LABEL = 3


class RobotDirection(Enum):
    IDLE = 0
    TO_PRINTER = 1
    TO_CARTON = 2


LOW_THRESHOLD_TAPE = 5
LOW_THRESHOLD_LABEL = 5
MAX_CARTON_STOCK = 50
MAX_TAPE_STOCK = 50
MAX_LABEL_STOCK = 50
CONVEYOR_CAPACITY = 1000  # you set it to 1000

INITIAL_CARTON_STOCK = 5
INITIAL_TAPE_STOCK = 5
INITIAL_LABEL_STOCK = 5


# ============================================
# SENSORS COMPONENT
# ============================================

class SensorsComponent:
    def __init__(self, env):
        self.env = env

        # Printer/Infeed sensors
        self.printer_present = False
        self.printer_counter = 0

        # Carton handling sensors
        self.loader_pocket_carton_present = False
        self.carton_blank_empty = False
        self.carton_stock = float(INITIAL_CARTON_STOCK)

        # Robotic arm sensors
        self.robot_fault = False
        self.product_placed_ok = False

        # Flap folding station sensors
        self.box_at_flap = False
        self.top_flaps_closed_ok = False
        self.flap_fault = False

        # Tape sealer station sensors
        self.box_at_tape = False
        self.tape_applied_ok = False
        self.tape_sealer_fault = False
        self.tape_empty = False
        self.tape_low = False
        self.tape_stock = float(INITIAL_TAPE_STOCK)

        # Labeling station sensors
        self.box_at_label = False
        self.label_applied_ok = False
        self.labeler_fault = False
        self.label_empty = False
        self.label_low = False
        self.label_stock = float(INITIAL_LABEL_STOCK)

        # Conveyor system sensors
        self.conveyor_full = False
        self.conveyor_fault = False
        self.conveyor_count = 0

        # Internal tracking
        self.current_box_position = None  # 'pocket', 'flap', 'tape', 'label'

        # Background processes
        self.env.process(self._simulate_printer())
        self.env.process(self._update_stock_flags())
        self.env.process(self._simulate_conveyor())

    # FIX 1: proper pulse + counter
    def _simulate_printer(self):
        while True:
            # idle gap
            self.printer_present = False
            yield self.env.timeout(random.uniform(6.0, 12.0))

            # pulse (one product)
            self.printer_present = True
            self.printer_counter += 1
            yield self.env.timeout(1.5)

            # end pulse
            self.printer_present = False
            yield self.env.timeout(1.0)

    def _update_stock_flags(self):
        while True:
            self.carton_blank_empty = self.carton_stock <= 0
            self.tape_empty = self.tape_stock <= 0
            self.tape_low = 0 < self.tape_stock <= LOW_THRESHOLD_TAPE
            self.label_empty = self.label_stock <= 0
            self.label_low = 0 < self.label_stock <= LOW_THRESHOLD_LABEL
            yield self.env.timeout(0.5)

    def _simulate_conveyor(self):
        while True:
            self.conveyor_full = self.conveyor_count >= CONVEYOR_CAPACITY
            yield self.env.timeout(0.2)

    def use_carton(self):
        if self.carton_stock > 0:
            self.carton_stock -= 1.0
            return True
        return False

    def use_tape(self):
        if self.tape_stock > 0:
            self.tape_stock -= 1.0
            return True
        return False

    def use_label(self):
        if self.label_stock > 0:
            self.label_stock -= 1.0
            return True
        return False

    def simulate_machine_fault(self, machine_type, probability_percent):
        if random.random() * 100 < probability_percent:
            fault_mapping = {
                "robot": lambda: setattr(self, "robot_fault", True),
                "flap": lambda: setattr(self, "flap_fault", True),
                "tape": lambda: setattr(self, "tape_sealer_fault", True),
                "label": lambda: setattr(self, "labeler_fault", True),
                "conveyor": lambda: setattr(self, "conveyor_fault", True),
            }
            fault_mapping[machine_type]()
            return True
        return False

    def reset_fault(self, machine_type):
        fault_mapping = {
            "robot": "robot_fault",
            "flap": "flap_fault",
            "tape": "tape_sealer_fault",
            "label": "labeler_fault",
            "conveyor": "conveyor_fault",
        }
        if machine_type in fault_mapping:
            setattr(self, fault_mapping[machine_type], False)

    def move_box_to_next_station(self):
        self.loader_pocket_carton_present = False
        self.box_at_flap = False
        self.box_at_tape = False
        self.box_at_label = False

        if self.current_box_position == "pocket":
            self.current_box_position = "flap"
            self.box_at_flap = True
            self.product_placed_ok = False

        elif self.current_box_position == "flap":
            self.current_box_position = "tape"
            self.box_at_tape = True

        elif self.current_box_position == "tape":
            self.current_box_position = "label"
            self.box_at_label = True

        elif self.current_box_position == "label":
            self.current_box_position = None
            self.conveyor_count += 1


# ============================================
# ACTUATORS COMPONENT
# ============================================

class ActuatorsComponent:
    def __init__(self, env):
        self.env = env

        # Robotic arm actuators
        self.axis_x_move = False
        self.axis_x_dir = 0
        self.axis_z_move = False
        self.axis_z_dir = 0
        self.gripper_cmd = 0

        # Station actuators
        self.flap_folder_enable = False
        self.tape_sealer_enable = False
        self.label_unit_enable = False
        self.final_conveyor_motor = False

        # Carton handling actuators
        self.carton_erector_enable = False
        self.carton_conveyor_motor = False
        self.carton_conveyor_stopper = False

        # Tower light indicators
        self.tower_light_green = False
        self.tower_light_yellow = False
        self.tower_light_red = False


# ============================================
# HUMAN RESOURCE COMPONENT
# ============================================

class HumanResourceComponent:
    def __init__(self, env):
        self.env = env

        self.repairRequest = False
        self.refillRequest = False
        self.repairType = 0
        self.refillType = 0
        self.repairDone = False
        self.refillDone = False

        self.active_repair = None
        self.active_refill = None

        self.tower_light_green = False
        self.tower_light_yellow = False
        self.tower_light_red = False

    def set_tower_lights(self, green, yellow, red):
        self.tower_light_green = green
        self.tower_light_yellow = yellow
        self.tower_light_red = red

    def process_requests(self, repair_request, refill_request, repair_type, refill_type):
        if repair_request and not self.active_repair:
            self.repairRequest = True
            self.repairType = repair_type
            self.env.process(self._handle_repair(repair_type))

        if refill_request and not self.active_refill:
            self.refillRequest = True
            self.refillType = refill_type
            self.env.process(self._handle_refill(refill_type))

    def _handle_repair(self, repair_type):
        self.active_repair = repair_type
        yield self.env.timeout(5)
        self.repairRequest = False
        self.active_repair = None
        self.repairDone = True
        yield self.env.timeout(0.1)
        self.repairDone = False

    def _handle_refill(self, refill_type):
        self.active_refill = refill_type
        yield self.env.timeout(4)
        self.refillRequest = False
        self.active_refill = None
        self.refillDone = True
        yield self.env.timeout(0.1)
        self.refillDone = False


# ============================================
# PLC COMPONENT
# ============================================

class PLCComponent:
    def __init__(self, env, sensors, actuators, hr):
        self.env = env
        self.sensors = sensors
        self.actuators = actuators
        self.hr = hr

        # HR mirrors
        self.hr_repair_request = False
        self.hr_refill_request = False
        self.hr_repair_type = 0
        self.hr_refill_type = 0
        self.hr_repair_done = False
        self.hr_refill_done = False
        self.hr_tower_light_green = False
        self.hr_tower_light_yellow = False
        self.hr_tower_light_red = False

        # Refill tracking
        self._refill_in_progress = False
        self._refill_type_in_progress = 0

        # KPIs
        self.packages_completed = 0
        self.arm_cycles = 0
        self.total_repairs = 0
        self.total_refills = 0
        self.carton_refills = 0
        self.tape_refills = 0
        self.label_refills = 0
        self.downtime_seconds = 0.0
        self.operational_time_seconds = 0.0

        # State
        self.state = "IDLE"
        self.robot_state = "IDLE"
        self.flap_attempts = 0
        self.tape_attempts = 0
        self.label_attempts = 0
        self.max_attempts = 3

        # Queue flow
        self.generator_flow_enabled = True
        self.box_queue = None

        # Start processes
        self.env.process(self._main_control_loop())
        self.env.process(self._kpi_tracking_loop())
        self.env.process(self._printer_infeed_loop())
        self.env.process(self._box_pipeline_loop())

    # -------- main loops --------

    def _main_control_loop(self):
        while True:
            self._update_tower_lights()
            self._process_hr_requests()

            if self._is_downtime():
                self._handle_downtime()
            else:
                self._handle_normal_operation()

            yield self.env.timeout(0.1)

    def _kpi_tracking_loop(self):
        while True:
            yield self.env.timeout(0.1)
            dt = 0.1
            if self._is_downtime():
                self.downtime_seconds += dt
            else:
                self.operational_time_seconds += dt

    # -------- tower lights --------

    def _update_tower_lights(self):
        if self._is_downtime():
            self.actuators.tower_light_green = False
            self.actuators.tower_light_yellow = False
            self.actuators.tower_light_red = True

        elif (self.sensors.tape_low or self.sensors.label_low or self.sensors.carton_blank_empty):
            self.actuators.tower_light_green = False
            self.actuators.tower_light_yellow = True
            self.actuators.tower_light_red = False

        else:
            self.actuators.tower_light_green = True
            self.actuators.tower_light_yellow = False
            self.actuators.tower_light_red = False

        self.hr.set_tower_lights(
            self.actuators.tower_light_green,
            self.actuators.tower_light_yellow,
            self.actuators.tower_light_red,
        )

        self.hr_tower_light_green = self.hr.tower_light_green
        self.hr_tower_light_yellow = self.hr.tower_light_yellow
        self.hr_tower_light_red = self.hr.tower_light_red

    # -------- HR interaction --------

    def _process_hr_requests(self):
        self._trigger_hr_requests()
        self._process_refill_completion()
        self._process_repair_completion()
        self._mirror_hr_signals()

    def _trigger_hr_requests(self):
        if (self.hr.tower_light_red and not (self.hr.repairRequest or self.hr.refillRequest)):
            if self._refill_in_progress:
                return

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

        self._trigger_failsafe_repairs()

    def _trigger_failsafe_repairs(self):
        if not self.hr.repairRequest and not self.hr.refillRequest:
            fault_mapping = [
                (self.sensors.robot_fault, RepairType.ROBOT.value),
                (self.sensors.flap_fault, RepairType.FLAP.value),
                (self.sensors.tape_sealer_fault, RepairType.TAPE_SEALER.value),
                (self.sensors.labeler_fault, RepairType.LABEL_UNIT.value),
                (self.sensors.conveyor_fault, RepairType.CONVEYOR.value),
            ]
            for cond, rtype in fault_mapping:
                if cond:
                    self.hr.process_requests(True, False, rtype, 0)
                    break

    def _process_refill_completion(self):
        if self.hr.refillDone:
            if self._refill_in_progress:
                self.total_refills += 1

                if self._refill_type_in_progress == RefillType.CARTON.value:
                    self.sensors.carton_stock = float(min(35, MAX_CARTON_STOCK))
                    self.sensors.carton_blank_empty = False
                    self.carton_refills += 1

                elif self._refill_type_in_progress == RefillType.TAPE.value:
                    self.sensors.tape_stock = float(min(35, MAX_TAPE_STOCK))
                    self.sensors.tape_empty = False
                    self.tape_refills += 1

                elif self._refill_type_in_progress == RefillType.LABEL.value:
                    self.sensors.label_stock = float(min(35, MAX_LABEL_STOCK))
                    self.sensors.label_empty = False
                    self.label_refills += 1

            self._refill_in_progress = False
            self._refill_type_in_progress = 0
            self.hr.refillDone = False

    # FIX 3: increment total_repairs
    def _process_repair_completion(self):
        if self.hr.repairDone:
            self.total_repairs += 1

            repair_actions = {
                RepairType.ROBOT.value: ("robot", lambda: None),
                RepairType.FLAP.value: ("flap", lambda: setattr(self, "flap_attempts", 0)),
                RepairType.TAPE_SEALER.value: ("tape", lambda: setattr(self, "tape_attempts", 0)),
                RepairType.LABEL_UNIT.value: ("label", lambda: setattr(self, "label_attempts", 0)),
                RepairType.CONVEYOR.value: ("conveyor", lambda: None),
            }

            if self.hr.repairType in repair_actions:
                machine_type, reset_func = repair_actions[self.hr.repairType]
                self.sensors.reset_fault(machine_type)
                reset_func()

            self.hr.repairDone = False

    def _mirror_hr_signals(self):
        self.hr_repair_request = self.hr.repairRequest
        self.hr_refill_request = self.hr.refillRequest
        self.hr_repair_type = self.hr.repairType
        self.hr_refill_type = self.hr.refillType
        self.hr_repair_done = self.hr.repairDone
        self.hr_refill_done = self.hr.refillDone

    # -------- downtime / normal --------

    def _is_downtime(self):
        return (
            self.hr_repair_request
            or self.sensors.conveyor_full
            or self.sensors.robot_fault
            or self.sensors.flap_fault
            or self.sensors.tape_sealer_fault
            or self.sensors.labeler_fault
            or self.sensors.conveyor_fault
            or self.sensors.carton_blank_empty
            or self.sensors.tape_empty
            or self.sensors.label_empty
        )

    def _handle_downtime(self):
        self.state = "DOWNTIME"

        self.actuators.axis_x_move = False
        self.actuators.axis_z_move = False
        self.actuators.gripper_cmd = 0
        self.actuators.flap_folder_enable = False
        self.actuators.tape_sealer_enable = False
        self.actuators.label_unit_enable = False
        self.actuators.carton_erector_enable = False
        self.actuators.carton_conveyor_motor = False
        self.actuators.final_conveyor_motor = False

        self.actuators.carton_conveyor_stopper = True
        self._reset_robot()

    def _handle_normal_operation(self):
        self.state = "NORMAL"
        if self.generator_flow_enabled:
            return

    # -------- printer -> queue --------

    def _printer_infeed_loop(self):
        box_id = 0
        prev_present = self.sensors.printer_present

        while True:
            current_present = self.sensors.printer_present

            if current_present and not prev_present:
                if self.box_queue is None:
                    self.box_queue = simpy.Store(self.env)

                yield self.env.timeout(1.5)

                box = {"id": box_id, "created_at": self.env.now}
                box_id += 1

                yield self.env.timeout(1.0)
                yield self.box_queue.put(box)
                yield self.env.timeout(1.0)

            prev_present = current_present
            wait_time = 0.8 if current_present else 1.2
            yield self.env.timeout(wait_time)

    def _box_pipeline_loop(self):
        while True:
            if self.box_queue is None:
                yield self.env.timeout(0.5)
                continue

            box = yield self.box_queue.get()
            yield self.env.timeout(0.6)
            yield from self._process_single_box(box)

    def _process_single_box(self, box):
        yield from self._wait_until_operational()

        while self.sensors.carton_stock <= 0:
            self.sensors.carton_blank_empty = True
            yield self.env.timeout(1.0)
        self.sensors.carton_blank_empty = False
        self.sensors.use_carton()

        self.sensors.current_box_position = "pocket"
        self._set_position_flags("pocket")
        self.sensors.product_placed_ok = False

        yield self.env.timeout(1.2)

        while not self.sensors.product_placed_ok:
            yield from self._robot_place_cycle()

        yield from self._move_to_station("flap", travel_time=1.5)
        while not self.sensors.top_flaps_closed_ok:
            yield from self._fold_flaps()

        yield from self._move_to_station("tape", travel_time=1.5)
        while not self.sensors.tape_applied_ok:
            yield from self._apply_tape()

        yield from self._move_to_station("label", travel_time=1.5)
        while not self.sensors.label_applied_ok:
            yield from self._apply_label()

        yield from self._move_to_outfeed()

    def _wait_until_operational(self):
        while self._is_downtime():
            yield self.env.timeout(0.5)

    # -------- station operations --------

    def _robot_place_cycle(self):
        yield from self._wait_until_operational()

        self.robot_state = "MOVE_TO_PRINTER"
        self.actuators.axis_x_move = True
        self.actuators.axis_x_dir = RobotDirection.TO_PRINTER.value
        yield self.env.timeout(1.5)

        self.robot_state = "GRAB_PRODUCT"
        self.actuators.axis_z_move = True
        self.actuators.axis_z_dir = 1
        yield self.env.timeout(1.0)

        if self.sensors.simulate_machine_fault("robot", 2):
            return

        self.robot_state = "MOVE_TO_CARTON"
        self.actuators.axis_x_dir = RobotDirection.TO_CARTON.value
        yield self.env.timeout(1.2)

        self.robot_state = "PLACE_PRODUCT"
        self.actuators.gripper_cmd = 2
        yield self.env.timeout(1.0)
        self.sensors.product_placed_ok = True

        self.robot_state = "RETURN_HOME"
        yield self.env.timeout(0.8)

        self.robot_state = "IDLE"
        self.arm_cycles += 1
        self._reset_robot()

    def _move_to_station(self, target, travel_time):
        yield from self._wait_until_operational()

        self._clear_position_flags()

        self.actuators.carton_conveyor_motor = True
        self.actuators.carton_conveyor_stopper = False
        yield self.env.timeout(travel_time)

        self.actuators.carton_conveyor_motor = False
        self.actuators.carton_conveyor_stopper = True

        self.sensors.move_box_to_next_station()

        if target == "flap":
            self.sensors.top_flaps_closed_ok = False
        elif target == "tape":
            self.sensors.tape_applied_ok = False
        elif target == "label":
            self.sensors.label_applied_ok = False

        self._set_position_flags(self.sensors.current_box_position)
        yield self.env.timeout(0.5)

    def _fold_flaps(self):
        yield from self._wait_until_operational()

        self.actuators.flap_folder_enable = True
        self.sensors.top_flaps_closed_ok = False

        yield self.env.timeout(3.0)

        if not self.sensors.simulate_machine_fault("flap", 1):
            self.sensors.top_flaps_closed_ok = True
            self.sensors.flap_fault = False

        self.actuators.flap_folder_enable = False
        yield self.env.timeout(0.5)

    def _apply_tape(self):
        yield from self._wait_until_operational()

        while self.sensors.tape_stock <= 0:
            self.sensors.tape_empty = True
            yield self.env.timeout(1.0)

        self.sensors.tape_empty = False
        self.sensors.tape_applied_ok = False

        self.actuators.tape_sealer_enable = True
        yield self.env.timeout(2.5)

        self.sensors.use_tape()

        if not self.sensors.simulate_machine_fault("tape", 1):
            self.sensors.tape_applied_ok = True
            self.sensors.tape_sealer_fault = False

        self.actuators.tape_sealer_enable = False
        yield self.env.timeout(0.7)

    def _apply_label(self):
        yield from self._wait_until_operational()

        while self.sensors.label_stock <= 0:
            self.sensors.label_empty = True
            yield self.env.timeout(1.0)

        self.sensors.label_empty = False
        self.sensors.label_applied_ok = False

        self.actuators.label_unit_enable = True
        self.label_attempts += 1
        yield self.env.timeout(3.0)

        self.sensors.use_label()

        if not self.sensors.simulate_machine_fault("label", 1):
            self.sensors.label_applied_ok = True
            self.sensors.labeler_fault = False
        else:
            self.sensors.label_applied_ok = False
            self.sensors.labeler_fault = self.label_attempts >= self.max_attempts

        self.actuators.label_unit_enable = False
        yield self.env.timeout(0.8)

    # FIX 2: respect conveyor_full before pushing outfeed
    def _move_to_outfeed(self):
        yield from self._wait_until_operational()

        while self.sensors.conveyor_full:
            yield self.env.timeout(0.5)

        self.actuators.final_conveyor_motor = True
        yield self.env.timeout(1.5)

        self.sensors.move_box_to_next_station()
        self._clear_position_flags()
        self.actuators.final_conveyor_motor = False

        self.packages_completed += 1
        self.label_attempts = 0

        self.sensors.label_applied_ok = False
        self.sensors.top_flaps_closed_ok = False
        self.sensors.tape_applied_ok = False

        yield self.env.timeout(0.8)

    # -------- helpers --------

    def _set_position_flags(self, position):
        self._clear_position_flags()

        position_actions = {
            "pocket": lambda: setattr(self.sensors, "loader_pocket_carton_present", True),
            "flap": lambda: setattr(self.sensors, "box_at_flap", True),
            "tape": lambda: setattr(self.sensors, "box_at_tape", True),
            "label": lambda: setattr(self.sensors, "box_at_label", True),
        }

        if position in position_actions:
            self.sensors.current_box_position = position
            position_actions[position]()
        else:
            self.sensors.current_box_position = None

    def _clear_position_flags(self):
        self.sensors.loader_pocket_carton_present = False
        self.sensors.box_at_flap = False
        self.sensors.box_at_tape = False
        self.sensors.box_at_label = False

    def _reset_robot(self):
        self.actuators.axis_x_move = False
        self.actuators.axis_x_dir = RobotDirection.IDLE.value
        self.actuators.axis_z_move = False
        self.actuators.axis_z_dir = 0
        self.actuators.gripper_cmd = 0


# ============================================
# CONSOLE MONITOR (NO GUI)
# ============================================

def console_monitor(env, plc, interval=5.0):
    while True:
        yield env.timeout(interval)
        qlen = (len(plc.box_queue.items) if plc.box_queue else 0)
        print(
            f"[t={env.now:7.1f}s] "
            f"Pkgs={plc.packages_completed:4d} "
            f"Queue={qlen:3d} "
            f"State={plc.state:8s} "
            f"Carton={plc.sensors.carton_stock:5.0f} "
            f"Tape={plc.sensors.tape_stock:5.0f} "
            f"Label={plc.sensors.label_stock:5.0f} "
            f"Conv={plc.sensors.conveyor_count:5d}/{CONVEYOR_CAPACITY} "
            f"Lights(G/Y/R)={int(plc.actuators.tower_light_green)}/"
            f"{int(plc.actuators.tower_light_yellow)}/"
            f"{int(plc.actuators.tower_light_red)}"
        )


# ============================================
# MAIN
# ============================================

def main():
    env = simpy.Environment()

    sensors = SensorsComponent(env)
    actuators = ActuatorsComponent(env)
    hr = HumanResourceComponent(env)
    plc = PLCComponent(env, sensors, actuators, hr)

    env.process(console_monitor(env, plc, interval=5.0))

    simulation_time = 9000  # seconds
    env.run(until=simulation_time)

    total_time = plc.operational_time_seconds + plc.downtime_seconds
    availability = (plc.operational_time_seconds / total_time * 100) if total_time > 0 else 0

    print("\n" + "=" * 60)
    print("FINAL RESULTS")
    print("=" * 60)
    print(f"Simulation Time: {simulation_time}s")
    print(f"Printer Pulses Counted: {sensors.printer_counter}")
    print(f"Packages Completed: {plc.packages_completed}")
    print(f"Robot Cycles: {plc.arm_cycles}")
    print(f"Total Repairs: {plc.total_repairs}")
    print(f"Total Refills: {plc.total_refills} (C/T/L = {plc.carton_refills}/{plc.tape_refills}/{plc.label_refills})")
    print(f"Operational Time: {plc.operational_time_seconds:.1f}s")
    print(f"Downtime: {plc.downtime_seconds:.1f}s")
    print(f"Availability: {availability:.1f}%")
    print(f"Final Stock - Carton={sensors.carton_stock:.0f}, Tape={sensors.tape_stock:.0f}, Label={sensors.label_stock:.0f}")
    print(f"Conveyor Count: {sensors.conveyor_count}/{CONVEYOR_CAPACITY}")
    print("=" * 60)


if __name__ == "__main__":
    main()
