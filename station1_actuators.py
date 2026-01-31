"""
Actuator module for Kitting Station 1
Actuators trigger actions and start processes
"""

class StationActuators:
    """High-level station control actuators"""
    def __init__(self, plc_controller):
        self.plc = plc_controller
    
    def start_next_order(self):
        """Start processing the next order in queue"""
        return self.plc.start_next_order()
    
    def reset_station(self):
        """Reset station after batch or fault"""
        return self.plc.reset_station()
    
    def emergency_stop(self):
        """Emergency stop all operations"""
        return self.plc.emergency_stop()

class ArmActuator:
    """Controls individual robotic arms"""
    def __init__(self, plc_controller, arm_id):
        self.plc = plc_controller
        self.arm_id = arm_id
    
    def execute_pick_sequence(self, order, from_pos, to_pos):
        """Execute complete pick and place sequence"""
        return self.plc.run_arm_pick_sequence(self.arm_id, order, from_pos, to_pos)
    
    def move_to_position(self, x, y):
        """Move arm to specific position"""
        return self.plc.run_arm_move(self.arm_id, x, y)
    
    def return_home(self):
        """Return arm to home position"""
        return self.plc.run_arm_return_home(self.arm_id)
    
    def perform_special_operation(self, operation_type):
        """Perform role-specific operation (mount, solder, etc.)"""
        return self.plc.run_arm_special_operation(self.arm_id, operation_type)

class ProcessActuators:
    """Controls station processes"""
    def __init__(self, plc_controller):
        self.plc = plc_controller
    
    def start_inventory_check(self, order):
        """Start inventory check cycle"""
        return self.plc.run_inventory_check(order)
    
    def start_output_handling(self, order):
        """Start output handling for finished order"""
        return self.plc.run_output_handling(order)
    
    def reset_components(self):
        """Reset inventory and output handlers"""
        return self.plc.reset_components()
