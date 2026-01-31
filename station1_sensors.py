"""
Sensor module for Kitting Station 1
Sensors only read state, never change state
"""

class StationStateSensor:
    """Reads current station state from PLC"""
    def __init__(self, plc_controller):
        self.plc = plc_controller
    
    def read(self):
        return self.plc.get_station_state()

class OrderSensor:
    """Reports order queue status"""
    def __init__(self, plc_controller):
        self.plc = plc_controller
    
    def read(self):
        return self.plc.has_pending_orders()
    
    def get_queue_size(self):
        return self.plc.get_queued_orders_count()

class CompletionSensor:
    """Reports completion metrics"""
    def __init__(self, plc_controller):
        self.plc = plc_controller
    
    def read(self):
        return self.plc.get_completed_count()
    
    def get_output_queue_size(self):
        return self.plc.get_kits_out_count()

class ArmStateSensor:
    """Reads state of robotic arms"""
    def __init__(self, plc_controller, arm_id):
        self.plc = plc_controller
        self.arm_id = arm_id
    
    def read(self):
        return self.plc.get_arm_state(self.arm_id)
    
    def get_utilization(self):
        return self.plc.get_arm_utilization(self.arm_id)
    
    def get_operations_count(self):
        return self.plc.get_arm_operations_count(self.arm_id)
    
    def get_failure_count(self):
        return self.plc.get_arm_failure_count(self.arm_id)

class InventoryStatusSensor:
    """Reads InventoryCheck state"""
    def __init__(self, plc_controller):
        self.plc = plc_controller
    
    def read(self):
        return self.plc.get_inventory_status()

class OutputStatusSensor:
    """Reads OutputHandler state"""
    def __init__(self, plc_controller):
        self.plc = plc_controller
    
    def read(self):
        return self.plc.get_output_status()
    
    def get_completed_count(self):
        return self.plc.get_output_completed_count()

class KPISensor:
    """Reads KPI metrics"""
    def __init__(self, plc_controller):
        self.plc = plc_controller
    
    def get_cycle_times(self):
        return self.plc.get_cycle_times()
    
    def get_avg_cycle_time(self):
        return self.plc.get_avg_cycle_time()
    
    def get_total_orders(self):
        return self.plc.get_total_orders()
    
    def get_throughput(self):
        return self.plc.get_throughput()

class FailureSensor:
    """Reads failure data for predictive maintenance"""
    def __init__(self, plc_controller):
        self.plc = plc_controller
    
    def get_failure_logs(self):
        return self.plc.get_failure_logs()
    
    def get_total_failures(self):
        return self.plc.get_total_failures()
