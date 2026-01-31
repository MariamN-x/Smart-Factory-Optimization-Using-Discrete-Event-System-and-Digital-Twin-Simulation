"""
PLC Controller for Kitting Station 1
Central brain coordinating all station operations
"""

import simpy
import random
import math
from datetime import datetime


class PLCController:
    """Main PLC controller for Kitting Station 1"""
    
    def __init__(self, env):
        self.env = env
        
        # State management
        self.state = "IDLE"
        self.order_count = 0
        self.queued_orders = 0
        self.wip_count = 0
        self.completed_count = 0
        self.cycle_times = []
        
        # DES Queues
        self.orders_in = simpy.Store(env)
        self.kits_out = simpy.Store(env)
        
        # Order tracking
        self.active_orders = {}
        self.completed_orders = []
        self.current_order = None
        
        # Initialize components (these will be injected)
        self.picking_arm = None
        self.kitting_arm = None
        self.mounting_arm = None
        self.soldering_arm = None
        self.inventory_check = None
        self.output_handler = None
        self.order_generator = None

        # Logger (back-reference to KittingStation)
        self.logger = None
        
        # Work area coordinates
        self.work_areas = {
            "part_bins": [[10, 50], [30, 50], [50, 50]],
            "staging_area": [[80, 60]],
            "kitting_tray": [[120, 70]],
            "mounting_area": [[220, 80]],
            "soldering_area": [[320, 90]],
            "output_buffer": [[400, 100]]
        }
        
        # Start main controller process
        self.env.process(self._main_controller())
        self.env.process(self._queue_monitor())

    def _main_controller(self):
        """Main control loop for processing orders"""
        while True:
            try:
                # Wait for order from input queue
                order = yield self.orders_in.get()
                print(f"🚀 {self.env.now:.1f}s: PLC starting processing for {order.model_type}")
                
                order.start_processing(self.env.now)
                self.current_order = order
                self.order_count += 1
                self.state = "PROCESSING"

                if self.logger is not None:
                    self.logger.log_event(
                        category="PLC",
                        event_type="ORDER_START",
                        data={
                            "order_id": order.order_id,
                            "model_type": order.model_type,
                            "queue_size_after_get": len(self.orders_in.items),
                        }
                    )
                
                # Execute complete kitting sequence
                yield self.env.process(self._execute_full_kitting_sequence(order))
                
                # Complete order
                order.complete(self.env.now)
                self.cycle_times.append(order.cycle_time)
                self.completed_orders.append(order)
                self.completed_count += 1

                if self.logger is not None:
                    self.logger.log_event(
                        category="PLC",
                        event_type="ORDER_COMPLETE",
                        data={
                            "order_id": order.order_id,
                            "model_type": order.model_type,
                            "cycle_time": order.cycle_time,
                            "total_completed": self.completed_count,
                        }
                    )
                
                # Put completed kit into output store
                yield self.kits_out.put(order)
                print(f"📦 {self.env.now:.1f}s: Order #{order.order_id} completed, cycle time: {order.cycle_time:.1f}s")
                
                self.current_order = None
                self.state = "IDLE"
                
            except simpy.Interrupt:
                print(f"🚨 {self.env.now:.1f}s: PLC controller interrupted!")
                self.state = "ERROR"
                if self.logger is not None:
                    self.logger.log_event(
                        category="PLC",
                        event_type="ERROR",
                        data={"msg": "PLC main controller interrupted"}
                    )
            
            # periodic PLC snapshot
            if self.logger is not None:
                self.logger.log_plc_snapshot()

            yield self.env.timeout(0.1)

    def _queue_monitor(self):
        """Monitor queue and WIP counts"""
        while True:
            self.queued_orders = len(self.orders_in.items)
            
            if self.state in ["PROCESSING", "CHECKING_INVENTORY", "ROBOTIC_PICKING", 
                              "ROBOTIC_KITTING", "ROBOTIC_MOUNTING", "ROBOTIC_SOLDERING", 
                              "HANDLING_OUTPUT"]:
                self.wip_count = 1
            else:
                self.wip_count = 0

            if self.logger is not None:
                self.logger.log_event(
                    category="PLC",
                    event_type="QUEUE_SNAPSHOT",
                    data={
                        "queued_orders": self.queued_orders,
                        "wip_count": self.wip_count,
                        "completed_count": self.completed_count,
                    }
                )
            
            yield self.env.timeout(0.5)

    def _execute_full_kitting_sequence(self, order):
        """Execute complete kitting sequence"""
        try:
            # Step 1: Check inventory
            self.state = "CHECKING_INVENTORY"
            if self.logger is not None:
                self.logger.log_event(
                    category="PLC",
                    event_type="STEP",
                    data={"step": "CHECKING_INVENTORY", "order_id": order.order_id}
                )
            yield self.run_inventory_check(order)
            
            if self.inventory_check.state == "PARTS_MISSING":
                print("❌ PLC: Cannot proceed - parts missing")
                if self.logger is not None:
                    self.logger.log_event(
                        category="PLC",
                        event_type="STEP",
                        data={
                            "step": "PARTS_MISSING",
                            "order_id": order.order_id
                        }
                    )
                # Return order to queue for retry
                yield self.orders_in.put(order)
                yield self.env.timeout(2.0)
                return
            
            # Step 2: Robotic picking sequence
            self.state = "ROBOTIC_PICKING"
            if self.logger is not None:
                self.logger.log_event(
                    category="PLC",
                    event_type="STEP",
                    data={"step": "ROBOTIC_PICKING", "order_id": order.order_id}
                )
            part_bin = random.choice(self.work_areas["part_bins"])
            staging = self.work_areas["staging_area"][0]
            yield self.env.process(self.run_arm_pick_sequence("picking", order, part_bin, staging))
            
            # Step 3: Robotic kitting sequence  
            self.state = "ROBOTIC_KITTING"
            if self.logger is not None:
                self.logger.log_event(
                    category="PLC",
                    event_type="STEP",
                    data={"step": "ROBOTIC_KITTING", "order_id": order.order_id}
                )
            kitting_tray = self.work_areas["kitting_tray"][0]
            yield self.env.process(self.run_arm_pick_sequence("kitting", order, staging, kitting_tray))
            
            # Step 4: Robotic mounting sequence
            self.state = "ROBOTIC_MOUNTING"
            if self.logger is not None:
                self.logger.log_event(
                    category="PLC",
                    event_type="STEP",
                    data={"step": "ROBOTIC_MOUNTING", "order_id": order.order_id}
                )
            mounting_area = self.work_areas["mounting_area"][0]
            yield self.env.process(self.run_arm_pick_sequence("mounting", order, kitting_tray, mounting_area))
            yield self.run_arm_special_operation("mounting", "mount")
            
            # Step 5: Robotic soldering sequence
            self.state = "ROBOTIC_SOLDERING"
            if self.logger is not None:
                self.logger.log_event(
                    category="PLC",
                    event_type="STEP",
                    data={"step": "ROBOTIC_SOLDERING", "order_id": order.order_id}
                )
            soldering_area = self.work_areas["soldering_area"][0]
            yield self.run_arm_special_operation("soldering", "solder")
            
            # Step 6: Output handling
            self.state = "HANDLING_OUTPUT"
            if self.logger is not None:
                self.logger.log_event(
                    category="PLC",
                    event_type="STEP",
                    data={"step": "HANDLING_OUTPUT", "order_id": order.order_id}
                )
            yield self.run_output_handling(order)
            
            # Step 7: Reset components
            self.state = "RESETTING"
            if self.logger is not None:
                self.logger.log_event(
                    category="PLC",
                    event_type="STEP",
                    data={"step": "RESETTING", "order_id": order.order_id}
                )
            yield self.inventory_check.execute_command("RESET")
            yield self.output_handler.execute_command("RESET")
            
            print(f"🎉 PLC: Order #{order.order_id} ({order.model_type}) sequence completed!")
            
        except simpy.Interrupt:
            print(f"🚨 {self.env.now:.1f}s: Kitting sequence interrupted!")
            self.state = "ERROR"
            if self.logger is not None:
                self.logger.log_event(
                    category="PLC",
                    event_type="ERROR",
                    data={"order_id": getattr(order, "order_id", None)}
                )

    # ========== SENSOR READ METHODS ==========
    
    def get_station_state(self):
        return self.state
    
    def has_pending_orders(self):
        return len(self.orders_in.items) > 0
    
    def get_queued_orders_count(self):
        return len(self.orders_in.items)
    
    def get_completed_count(self):
        return self.completed_count
    
    def get_kits_out_count(self):
        return len(self.kits_out.items)
    
    def get_arm_state(self, arm_id):
        arm = self._get_arm_by_id(arm_id)
        return arm.state if arm else "UNKNOWN"
    
    def get_arm_utilization(self, arm_id):
        arm = self._get_arm_by_id(arm_id)
        return arm.get_utilization() if arm else 0.0
    
    def get_arm_operations_count(self, arm_id):
        arm = self._get_arm_by_id(arm_id)
        return arm.completed_operations if arm else 0
    
    def get_arm_failure_count(self, arm_id):
        arm = self._get_arm_by_id(arm_id)
        return arm.failure_count if arm else 0
    
    def get_inventory_status(self):
        return self.inventory_check.state if self.inventory_check else "UNKNOWN"
    
    def get_output_status(self):
        return self.output_handler.state if self.output_handler else "UNKNOWN"
    
    def get_output_completed_count(self):
        return self.output_handler.completed_count if self.output_handler else 0
    
    def get_cycle_times(self):
        return self.cycle_times
    
    def get_avg_cycle_time(self):
        return sum(self.cycle_times) / len(self.cycle_times) if self.cycle_times else 0.0
    
    def get_total_orders(self):
        return self.order_count
    
    def get_throughput(self):
        if self.env.now > 0:
            return (self.completed_count / self.env.now) * 3600
        return 0.0
    
    def get_failure_logs(self):
        """Get all failure logs from all arms"""
        all_failures = []
        arms = [self.picking_arm, self.kitting_arm, self.mounting_arm, self.soldering_arm]
        for arm in arms:
            if arm:
                all_failures.extend(arm.failure_log)
        
        all_failures.sort(key=lambda x: x["time"])
        return all_failures
    
    def get_total_failures(self):
        total = 0
        arms = [self.picking_arm, self.kitting_arm, self.mounting_arm, self.soldering_arm]
        for arm in arms:
            if arm:
                total += arm.failure_count
        return total

    # ========== ACTUATOR COMMAND METHODS ==========
    
    def start_next_order(self):
        """Start processing next order (if any)"""
        if self.state == "IDLE" and len(self.orders_in.items) > 0:
            # This will be handled by the main controller loop
            return True
        return False
    
    def reset_station(self):
        """Reset station state"""
        self.state = "IDLE"
        if self.inventory_check:
            self.inventory_check.execute_command("RESET")
        if self.output_handler:
            self.output_handler.execute_command("RESET")
        return True
    
    def emergency_stop(self):
        """Emergency stop - interrupt current processes"""
        self.state = "EMERGENCY_STOP"
        # In a real implementation, we would interrupt all running processes
        if self.logger is not None:
            self.logger.log_event(
                category="PLC",
                event_type="EMERGENCY_STOP",
                data={}
            )
        return True
    
    def run_inventory_check(self, order):
        """Execute inventory check"""
        if self.inventory_check:
            return self.inventory_check.execute_command("CHECK_INVENTORY")
        return self.env.timeout(0)  # No-op if no inventory check
    
    def run_output_handling(self, order):
        """Execute output handling"""
        if self.output_handler:
            return self.output_handler.execute_command("HANDLE_OUTPUT")
        return self.env.timeout(0)  # No-op if no output handler
    
    def run_arm_pick_sequence(self, arm_id, order, from_pos, to_pos):
        """Execute complete pick and place sequence for arm"""
        arm = self._get_arm_by_id(arm_id)
        if not arm:
            return self.env.timeout(0)
        
        # Move to source, pick, move to destination, place
        yield arm.move_to(from_pos[0], from_pos[1])
        yield arm.pick_component()
        yield arm.move_to(to_pos[0], to_pos[1])
        yield arm.place_component()
        yield arm.return_home()
    
    def run_arm_move(self, arm_id, x, y):
        """Move arm to specific position"""
        arm = self._get_arm_by_id(arm_id)
        if arm:
            return arm.move_to(x, y)
        return self.env.timeout(0)
    
    def run_arm_return_home(self, arm_id):
        """Return arm to home position"""
        arm = self._get_arm_by_id(arm_id)
        if arm:
            return arm.return_home()
        return self.env.timeout(0)
    
    def run_arm_special_operation(self, arm_id, operation_type):
        """Perform role-specific operation"""
        arm = self._get_arm_by_id(arm_id)
        if not arm:
            return self.env.timeout(0)
        
        if operation_type == "mount" and arm.role == "MOUNTING":
            return arm.mount_assembly()
        elif operation_type == "solder" and arm.role == "SOLDERING":
            return arm.solder_joint()
        
        return self.env.timeout(0)
    
    def reset_components(self):
        """Reset all components"""
        if self.inventory_check:
            yield self.inventory_check.execute_command("RESET")
        if self.output_handler:
            yield self.output_handler.execute_command("RESET")

    # ========== UTILITY METHODS ==========
    
    def _get_arm_by_id(self, arm_id):
        """Get arm instance by ID"""
        arm_map = {
            "picking": self.picking_arm,
            "kitting": self.kitting_arm, 
            "mounting": self.mounting_arm,
            "soldering": self.soldering_arm
        }
        return arm_map.get(arm_id)
    
    def get_utilization_data(self):
        """Get utilization data for all arms"""
        utilizations = {}
        arms = [
            ("PickingArm", self.picking_arm),
            ("KittingArm", self.kitting_arm),
            ("MountingArm", self.mounting_arm),
            ("SolderingArm", self.soldering_arm)
        ]
        
        for name, arm in arms:
            if arm:
                utilizations[name] = {
                    "utilization_percent": arm.get_utilization(),
                    "busy_time": arm.busy_time,
                    "idle_time": arm.idle_time,
                    "completed_operations": arm.completed_operations,
                    "failure_count": arm.failure_count
                }
        
        return utilizations
