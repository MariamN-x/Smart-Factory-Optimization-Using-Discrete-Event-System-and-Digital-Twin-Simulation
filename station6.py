#!/usr/bin/env python3
from __future__ import print_function
import struct
import sys
import argparse
import math
import json
import os
import random
import time
from datetime import datetime, timedelta

PythonGateways = 'pythonGateways/'
sys.path.append(PythonGateways)

import VsiCommonPythonApi as vsiCommonPythonApi
import VsiTcpUdpPythonGateway as vsiEthernetPythonGateway


class MySignals:
    def __init__(self):
        # Inputs
        self.cmd_start = 0
        self.cmd_stop = 0
        self.cmd_reset = 0
        self.batch_id = 0
        self.recipe_id = 0

        # Outputs
        self.ready = 0
        self.busy = 0
        self.fault = 0
        self.done = 0
        self.cycle_time_ms = 0
        self.packages_completed = 0
        self.arm_cycles = 0
        self.total_repairs = 0
        self.operational_time_s = 0
        self.downtime_s = 0
        self.availability = 0


srcMacAddress = [0x02, 0x00, 0x00, 0x00, 0x00, 0x16]
PLC_LineCoordinatorMacAddress = [0x02, 0x00, 0x00, 0x00, 0x00, 0x01]
srcIpAddress = [10, 10, 0, 16]
PLC_LineCoordinatorIpAddress = [10, 10, 0, 1]

PLC_LineCoordinatorSocketPortNumber0 = 6006

ST6_PackagingDispatch0 = 0


# Start of user custom code region. Please apply edits only within these regions:  Global Variables & Definitions
# --- Station 6: Packaging & Dispatch ENHANCED PARAMETERIZED model ---
import simpy

class FixedPackagingStation:
    """
    Enhanced parameterized packaging station with FULL integration of:
    - Material Handling (carton, tape, label inventory with replenishment)
    - Robotic Arm Operations (cycles, speed, reliability, maintenance)
    - Maintenance Strategies (reactive/preventive/predictive)
    - Quality Metrics (defects, rework, seal integrity)
    - Energy Management (consumption, cost, CO2)
    - Shift Scheduling (availability windows, breaks)
    - Catastrophic Failure Modeling with MTTR
    - Material Consumption Tracking
    
    All parameters loaded ONCE at init/reset - NO runtime changes (VSI constraint).
    """
    def __init__(self, env: simpy.Environment, config: dict, human_resources: dict,
                 maintenance: dict, shift_schedule: dict, quality: dict, energy_mgmt: dict,
                 material_handling: dict, robotic_arm: dict):
        self.env = env
        self.config = config
        self.human_resources = human_resources
        self.maintenance = maintenance
        self.shift_schedule = shift_schedule
        self.quality = quality
        self.energy_mgmt = energy_mgmt
        self.material_handling = material_handling
        self.robotic_arm = robotic_arm
        
        # === BASE PARAMETERS (from stations.S6) ===
        self._nominal_cycle_time_s = config.get("cycle_time_s", 6.7)
        self._base_failure_rate = config.get("failure_rate", 0.02)
        self._base_mttr_s = config.get("mttr_s", 35.0)
        self._buffer_capacity = config.get("buffer_capacity", 2)
        self._power_rating_w = config.get("power_rating_w", 2000)
        
        # === MATERIAL HANDLING PARAMETERS ===
        self._carton_capacity = material_handling.get("carton_capacity", 50)
        self._tape_capacity = material_handling.get("tape_capacity", 50)
        self._label_capacity = material_handling.get("label_capacity", 50)
        self._carton_reorder_point = material_handling.get("carton_reorder_point", 15)
        self._tape_reorder_point = material_handling.get("tape_reorder_point", 15)
        self._label_reorder_point = material_handling.get("label_reorder_point", 15)
        self._carton_refill_time_s = material_handling.get("carton_refill_time_s", 120)
        self._tape_refill_time_s = material_handling.get("tape_refill_time_s", 90)
        self._label_refill_time_s = material_handling.get("label_refill_time_s", 105)
        self._auto_replenishment = material_handling.get("auto_replenishment", True)
        self._just_in_time = material_handling.get("just_in_time", False)
        
        # === ROBOTIC ARM PARAMETERS ===
        self._arm_speed_pct = robotic_arm.get("arm_speed_pct", 100) / 100.0
        self._arm_reliability_pct = robotic_arm.get("arm_reliability_pct", 98.5) / 100.0
        self._pick_place_time_s = robotic_arm.get("pick_place_time_s", 1.2)
        self._arm_calibration_interval_h = robotic_arm.get("calibration_interval_h", 168)
        self._arm_calibration_duration_min = robotic_arm.get("calibration_duration_min", 20)
        self._arm_end_effector_lifetime_cycles = robotic_arm.get("end_effector_lifetime_cycles", 10000)
        self._arm_end_effector_replace_time_s = robotic_arm.get("end_effector_replace_time_s", 300)
        self._arm_cycles_since_maintenance = 0
        
        # === HUMAN RESOURCES EFFECTIVE PARAMETERS ===
        self._operator_efficiency = human_resources.get("operator_efficiency_factor", 95) / 100.0
        self._advanced_skill_pct = human_resources.get("advanced_skill_pct", 30) / 100.0
        self._cross_training_pct = human_resources.get("cross_training_pct", 20) / 100.0
        self._break_time_min_per_hour = human_resources.get("break_time_min_per_hour", 5)
        self._shift_changeover_min = human_resources.get("shift_changeover_min", 10)
        
        # Calculate effective cycle time (operator efficiency + arm speed)
        self._effective_cycle_time_s = self._nominal_cycle_time_s / (self._operator_efficiency * self._arm_speed_pct)
        
        # Calculate skill-based MTTR reduction factor
        self._skill_mttr_reduction = (self._advanced_skill_pct * 0.25) + (self._cross_training_pct * 0.15)
        self._effective_mttr_s = self._base_mttr_s * (1.0 - self._skill_mttr_reduction)
        
        # === MAINTENANCE STRATEGY EFFECTIVE PARAMETERS ===
        self._maintenance_strategy = maintenance.get("strategy", "predictive")
        self._preventive_interval_h = maintenance.get("preventive_interval_h", 160)
        self._preventive_duration_min = maintenance.get("preventive_duration_min", 45)
        self._predictive_enabled = maintenance.get("predictive_enabled", True)
        self._predictive_mttr_reduction_pct = maintenance.get("predictive_mttr_reduction_pct", 25) / 100.0
        self._predictive_failure_reduction_pct = maintenance.get("predictive_failure_reduction_pct", 30) / 100.0
        self._condition_monitoring = maintenance.get("condition_monitoring", True)
        
        # Apply predictive maintenance benefits if enabled
        if self._predictive_enabled and self._condition_monitoring:
            self._effective_failure_rate = self._base_failure_rate * (1.0 - self._predictive_failure_reduction_pct)
            self._effective_mttr_s *= (1.0 - self._predictive_mttr_reduction_pct)
            # Arm reliability improves with predictive maintenance
            self._arm_reliability_pct = min(0.995, self._arm_reliability_pct * 1.02)
        else:
            self._effective_failure_rate = self._base_failure_rate
        
        # === QUALITY PARAMETERS ===
        self._defect_rate_pct = quality.get("defect_rate_pct", 0.3) / 100.0
        self._seal_integrity_pct = quality.get("seal_integrity_pct", 99.5) / 100.0
        self._label_accuracy_pct = quality.get("label_accuracy_pct", 99.8) / 100.0
        self._rework_enabled = quality.get("rework_enabled", True)
        self._rework_time_s = quality.get("rework_time_s", 45)
        self._rework_success_rate_pct = quality.get("rework_success_rate_pct", 85) / 100.0
        self._inspection_enabled = quality.get("inspection_enabled", True)
        self._first_pass_yield_target = quality.get("first_pass_yield_target", 99.0) / 100.0
        
        # === SHIFT SCHEDULE PARAMETERS ===
        self._shifts_per_day = shift_schedule.get("shifts_per_day", 1)
        self._shift_duration_h = shift_schedule.get("shift_duration_h", 8)
        self._working_days_per_week = shift_schedule.get("working_days_per_week", 5)
        self._overtime_enabled = shift_schedule.get("overtime_enabled", False)
        self._breaks_per_shift = shift_schedule.get("breaks_per_shift", 2)
        self._break_duration_min = shift_schedule.get("break_duration_min", 15)
        self._lunch_break_min = shift_schedule.get("lunch_break_min", 30)
        
        # Calculate total shift time in seconds (excluding breaks)
        self._shift_breaks_total_min = (self._breaks_per_shift * self._break_duration_min) + self._lunch_break_min
        self._effective_shift_duration_s = (self._shift_duration_h * 3600) - (self._shift_breaks_total_min * 60)
        
        # === ENERGY MANAGEMENT ===
        self._off_peak_enabled = energy_mgmt.get("off_peak_enabled", False)
        self._peak_tariff = energy_mgmt.get("peak_tariff", 0.18)
        self._off_peak_tariff = energy_mgmt.get("off_peak_tariff", 0.08)
        self._co2_factor = energy_mgmt.get("co2_factor_kg_per_kwh", 0.4)
        self._energy_saving_mode = energy_mgmt.get("energy_saving_mode", False)
        
        # === BASE TIMING CONSTANTS (seconds) ===
        self._T_CARTON_ERECT_S = material_handling.get("carton_erect_time_s", 1.0)
        self._T_ROBOT_PICKPLACE_S = self._pick_place_time_s
        self._T_FLAP_FOLD_S = material_handling.get("flap_fold_time_s", 1.5)
        self._T_TAPE_SEAL_S = material_handling.get("tape_seal_time_s", 1.2)
        self._T_LABEL_APPLY_S = material_handling.get("label_apply_time_s", 1.0)
        self._T_OUTFEED_S = material_handling.get("outfeed_time_s", 0.8)
        
        # Scale factor based on config cycle time
        base_cycle = 6.7
        self._scale = max(0.1, self._nominal_cycle_time_s / base_cycle)
        
        # === MATERIAL INVENTORY ===
        self.carton_stock = self._carton_capacity
        self.tape_stock = self._tape_capacity
        self.label_stock = self._label_capacity
        self.carton_replenishments = 0
        self.tape_replenishments = 0
        self.label_replenishments = 0
        self.material_stockouts = 0
        
        # === STATE VARIABLES ===
        self.state = "IDLE"
        self._cycle_proc = None
        self._busy = False
        self._fault = False
        self._done_pulse = False
        self._in_break = False
        self._in_maintenance = False
        self._in_shift_change = False
        self._in_arm_calibration = False
        self._in_end_effector_replace = False
        
        # Cycle timing
        self._cycle_start_s = 0
        self._cycle_end_s = 0
        self._actual_cycle_time_ms = 0
        self._cycle_count = 0
        self._cycle_time_sum_ms = 0
        
        # KPI tracking
        self.packages_completed = 0
        self.arm_cycles = 0
        self.total_repairs = 0
        self.operational_time_s = 0.0
        self.downtime_s = 0.0
        self.availability = 0.0
        self.total_downtime_s = 0.0
        self.total_busy_time_s = 0.0
        self.last_busy_start_s = 0.0
        self.failure_count = 0
        self.completed_cycles = 0
        self.preventive_maintenance_count = 0
        self.scheduled_downtime_s = 0.0
        self.unscheduled_downtime_s = 0.0
        
        # Quality tracking
        self.seal_defects = 0
        self.label_defects = 0
        self.arm_position_errors = 0
        self.rework_count = 0
        self.scrap_count = 0
        
        # ENERGY TRACKING
        self.energy_kwh = 0.0
        self.energy_cost_usd = 0.0
        self.energy_savings_kwh = 0.0
        
        # Schedule recurring events
        self._schedule_breaks()
        self._schedule_preventive_maintenance()
        self._schedule_shift_boundaries()
        self._schedule_arm_calibration()
        self._schedule_material_replenishment()
        
        print(f"  FixedPackagingStation INITIALIZED with FULL CONFIGURATION:")
        print(f"    Cycle Time: {self._nominal_cycle_time_s:.3f}s (effective: {self._effective_cycle_time_s:.3f}s, scale: {self._scale:.2f})")
        print(f"    Material: Carton:{self._carton_capacity}, Tape:{self._tape_capacity}, Label:{self._label_capacity}")
        print(f"    Robotic Arm: Speed: {self._arm_speed_pct*100:.0f}%, Reliability: {self._arm_reliability_pct*100:.1f}%")
        print(f"    Failure Rate: {self._base_failure_rate:.3f} → {self._effective_failure_rate:.3f} (predictive: {'ON' if self._predictive_enabled else 'OFF'})")
        print(f"    MTTR: {self._base_mttr_s:.1f}s → {self._effective_mttr_s:.1f}s (skills: {self._skill_mttr_reduction*100:.1f}% reduction)")
        print(f"    Power: {self._power_rating_w}W | Defect Rate: {self._defect_rate_pct*100:.2f}%")
        print(f"    Shifts: {self._shifts_per_day}x{self._shift_duration_h}h ({self._effective_shift_duration_s/3600:.1f}h productive)")
        print(f"    Energy: ${self._peak_tariff:.2f}/kWh peak | ${self._off_peak_tariff:.2f}/kWh off-peak | CO2: {self._co2_factor}kg/kWh")

    def _schedule_breaks(self):
        """Schedule hourly breaks and shift breaks"""
        def hourly_break():
            while True:
                yield self.env.timeout(3600)  # Every hour
                if not self._in_break and not self._in_maintenance and not self._fault and not self._in_arm_calibration:
                    print(f"  ☕ BREAK scheduled at {self.env.now:.1f}s (hourly {self._break_time_min_per_hour} min break)")
                    self._in_break = True
                    break_duration_s = self._break_time_min_per_hour * 60
                    self.scheduled_downtime_s += break_duration_s
                    yield self.env.timeout(break_duration_s)
                    self._in_break = False
        
        def shift_breaks():
            # First break after 2 hours
            yield self.env.timeout(2 * 3600)
            if not self._in_break and not self._in_maintenance and not self._fault and not self._in_arm_calibration:
                print(f"  🥪 LUNCH BREAK scheduled at {self.env.now:.1f}s ({self._lunch_break_min} min)")
                self._in_break = True
                self.scheduled_downtime_s += self._lunch_break_min * 60
                yield self.env.timeout(self._lunch_break_min * 60)
                self._in_break = False
            
            # Short breaks after lunch
            for i in range(self._breaks_per_shift):
                yield self.env.timeout(2 * 3600)  # Every 2 hours after lunch
                if not self._in_break and not self._in_maintenance and not self._fault and not self._in_arm_calibration:
                    print(f"  ⏸️  SHORT BREAK #{i+1} at {self.env.now:.1f}s ({self._break_duration_min} min)")
                    self._in_break = True
                    self.scheduled_downtime_s += self._break_duration_min * 60
                    yield self.env.timeout(self._break_duration_min * 60)
                    self._in_break = False
        
        self.env.process(hourly_break())
        self.env.process(shift_breaks())

    def _schedule_preventive_maintenance(self):
        """Schedule preventive maintenance at configured intervals"""
        def pm_cycle():
            while True:
                interval_s = self._preventive_interval_h * 3600
                yield self.env.timeout(interval_s)
                
                if not self._busy and not self._fault and not self._in_maintenance and not self._in_arm_calibration:
                    print(f"  🔧 PREVENTIVE MAINTENANCE scheduled at {self.env.now:.1f}s (duration: {self._preventive_duration_min} min)")
                    self._in_maintenance = True
                    pm_duration_s = self._preventive_duration_min * 60
                    self.scheduled_downtime_s += pm_duration_s
                    self.preventive_maintenance_count += 1
                    yield self.env.timeout(pm_duration_s)
                    self._in_maintenance = False
                    print(f"  ✅ PM completed at {self.env.now:.1f}s")
        
        if self._maintenance_strategy in ["preventive", "predictive"]:
            self.env.process(pm_cycle())

    def _schedule_arm_calibration(self):
        """Schedule robotic arm calibration"""
        def calibration_cycle():
            while True:
                interval_s = self._arm_calibration_interval_h * 3600
                yield self.env.timeout(interval_s)
                
                if not self._busy and not self._fault and not self._in_maintenance and not self._in_arm_calibration:
                    print(f"  🤖 ARM CALIBRATION at {self.env.now:.1f}s (duration: {self._arm_calibration_duration_min} min)")
                    self._in_arm_calibration = True
                    cal_duration_s = self._arm_calibration_duration_min * 60
                    self.scheduled_downtime_s += cal_duration_s
                    yield self.env.timeout(cal_duration_s)
                    self._in_arm_calibration = False
                    print(f"  ✅ Arm calibration completed at {self.env.now:.1f}s")
        
        self.env.process(calibration_cycle())

    def _schedule_material_replenishment(self):
        """Auto-replenishment of materials at fixed intervals"""
        def replenishment_cycle():
            while True:
                yield self.env.timeout(3600)  # Check every hour
                if self._auto_replenishment:
                    # Replenish cartons if below reorder point
                    if self.carton_stock < self._carton_reorder_point:
                        print(f"  📦 CARTON replenishment triggered at {self.env.now:.1f}s (stock: {self.carton_stock})")
                        self.carton_stock = self._carton_capacity
                        self.carton_replenishments += 1
                        # No downtime - replenishment happens in background
                    
                    # Replenish tape if below reorder point
                    if self.tape_stock < self._tape_reorder_point:
                        print(f"  📦 TAPE replenishment triggered at {self.env.now:.1f}s (stock: {self.tape_stock})")
                        self.tape_stock = self._tape_capacity
                        self.tape_replenishments += 1
                    
                    # Replenish labels if below reorder point
                    if self.label_stock < self._label_reorder_point:
                        print(f"  📦 LABEL replenishment triggered at {self.env.now:.1f}s (stock: {self.label_stock})")
                        self.label_stock = self._label_capacity
                        self.label_replenishments += 1
        
        if self._auto_replenishment:
            self.env.process(replenishment_cycle())

    def _schedule_shift_boundaries(self):
        """Schedule shift start/end and changeovers"""
        def shift_cycle():
            shift_duration_s = self._shift_duration_h * 3600
            day_duration_s = 24 * 3600
            
            while True:
                for shift_num in range(self._shifts_per_day):
                    time_into_day = (self.env.now % day_duration_s)
                    next_shift_start = (shift_num * shift_duration_s)
                    
                    if time_into_day > next_shift_start:
                        wait_time = day_duration_s - time_into_day + next_shift_start
                    else:
                        wait_time = next_shift_start - time_into_day
                    
                    if wait_time > 0:
                        yield self.env.timeout(wait_time)
                    
                    print(f"  🌅 SHIFT {shift_num+1} START at {self.env.now:.1f}s (duration: {self._shift_duration_h}h)")
                    
                    yield self.env.timeout(shift_duration_s)
                    
                    if shift_num < self._shifts_per_day - 1:
                        print(f"  🔄 SHIFT CHANGEOVER at {self.env.now:.1f}s ({self._shift_changeover_min} min downtime)")
                        self._in_shift_change = True
                        self.scheduled_downtime_s += self._shift_changeover_min * 60
                        yield self.env.timeout(self._shift_changeover_min * 60)
                        self._in_shift_change = False
                
                time_into_day = self.env.now % day_duration_s
                wait_until_next_day = day_duration_s - time_into_day
                if wait_until_next_day > 0:
                    print(f"  🌙 END OF DAY at {self.env.now:.1f}s - waiting {wait_until_next_day/3600:.1f}h for next day")
                    yield self.env.timeout(wait_until_next_day)
        
        self.env.process(shift_cycle())

    def _check_end_effector_maintenance(self):
        """Check if end effector needs replacement based on cycle count"""
        if self.arm_cycles >= self._arm_end_effector_lifetime_cycles:
            print(f"  ⚠️  ARM END EFFECTOR replacement needed at {self.env.now:.1f}s ({self.arm_cycles} cycles)")
            self._in_end_effector_replace = True
            self.scheduled_downtime_s += self._arm_end_effector_replace_time_s
            yield self.env.timeout(self._arm_end_effector_replace_time_s)
            self._in_end_effector_replace = False
            self.arm_cycles = 0
            print(f"  ✅ End effector replacement completed at {self.env.now:.1f}s")

    def _check_material_stock(self):
        """Check and replenish materials with downtime if JIT or stockout"""
        total_refill_time = 0.0
        
        if self.carton_stock <= 0:
            print(f"  ⚠️  CARTON STOCKOUT at {self.env.now:.1f}s - manual replenishment required")
            self.material_stockouts += 1
            total_refill_time += self._carton_refill_time_s
            yield self.env.timeout(self._carton_refill_time_s)
            self.carton_stock = self._carton_capacity
            self.carton_replenishments += 1
            self.downtime_s += self._carton_refill_time_s
        
        if self.tape_stock <= 0:
            print(f"  ⚠️  TAPE STOCKOUT at {self.env.now:.1f}s - manual replenishment required")
            self.material_stockouts += 1
            total_refill_time += self._tape_refill_time_s
            yield self.env.timeout(self._tape_refill_time_s)
            self.tape_stock = self._tape_capacity
            self.tape_replenishments += 1
            self.downtime_s += self._tape_refill_time_s
        
        if self.label_stock <= 0:
            print(f"  ⚠️  LABEL STOCKOUT at {self.env.now:.1f}s - manual replenishment required")
            self.material_stockouts += 1
            total_refill_time += self._label_refill_time_s
            yield self.env.timeout(self._label_refill_time_s)
            self.label_stock = self._label_capacity
            self.label_replenishments += 1
            self.downtime_s += self._label_refill_time_s
        
        return total_refill_time

    def _is_operational(self):
        """Check if station can operate (not in break/maintenance/calibration/shift change/fault)"""
        return (not self._in_break and 
                not self._in_maintenance and 
                not self._in_arm_calibration and
                not self._in_end_effector_replace and
                not self._in_shift_change and 
                not self._fault and
                self._is_within_shift_hours())

    def _is_within_shift_hours(self):
        """Check if current simulation time is within operational shift hours"""
        time_into_day = self.env.now % (24 * 3600)
        shift_end_s = self._shifts_per_day * self._shift_duration_h * 3600
        operational_window_s = shift_end_s - (self._shift_breaks_total_min * 60)
        return time_into_day < operational_window_s

    def start_cycle(self, batch_id: int, recipe_id: int, start_time_s: float):
        """Start a new packaging cycle - ONLY called on cmd_start rising edge"""
        if self._cycle_proc is not None or self._busy:
            print(f"  ⚠️  WARNING: start_cycle called but already busy (state={self.state})")
            return False
        
        if not self._is_operational():
            reason = ("BREAK" if self._in_break else 
                     "MAINTENANCE" if self._in_maintenance else 
                     "ARM CALIBRATION" if self._in_arm_calibration else
                     "END EFFECTOR REPLACE" if self._in_end_effector_replace else
                     "SHIFT CHANGE" if self._in_shift_change else 
                     "FAULT" if self._fault else 
                     "OFF-SHIFT")
            print(f"  ⚠️  CANNOT START: Station not operational ({reason}) at env.now={self.env.now:.3f}s")
            return False
        
        print(f"  🚀 STARTING packaging cycle at env.now={self.env.now:.3f}s (effective cycle time={self._effective_cycle_time_s:.3f}s)")
        self.state = "RUNNING"
        self._busy = True
        self._done_pulse = False
        self._cycle_start_s = start_time_s
        self._actual_cycle_time_ms = 0
        self.last_busy_start_s = self.env.now
        self._current_batch = batch_id
        self._current_recipe = recipe_id
        self._cycle_proc = self.env.process(self._packaging_cycle())
        return True

    def _packaging_cycle(self):
        """Run a single packaging cycle with full integration of materials, robotics, quality, and energy"""
        try:
            t0 = self.env.now
            total_energy_time = 0.0
            
            # STEP 1: Check end effector maintenance
            if self.arm_cycles >= self._arm_end_effector_lifetime_cycles:
                yield from self._check_end_effector_maintenance()
            
            # STEP 2: Check for catastrophic failure
            if random.random() < self._effective_failure_rate:
                failure_start = self.env.now
                print(f"  ⚠️  CATASTROPHIC FAILURE at {failure_start:.3f}s (cycle #{self._cycle_count+1})")
                self.failure_count += 1
                self.total_repairs += 1
                self._fault = True
                self._busy = False
                self.total_busy_time_s += (failure_start - self.last_busy_start_s)
                self.unscheduled_downtime_s += self._effective_mttr_s
                
                yield self.env.timeout(self._effective_mttr_s)
                
                repair_end = self.env.now
                downtime = repair_end - failure_start
                self.total_downtime_s += downtime
                self.downtime_s += downtime
                print(f"  ✅ REPAIR complete at {repair_end:.3f}s (downtime={downtime:.2f}s)")
                self._fault = False
                self.state = "IDLE"
                return

            # STEP 3: Check material stock and replenish if needed
            refill_time = yield from self._check_material_stock()
            total_energy_time += refill_time

            # STEP 4: Check arm reliability
            arm_failure = False
            if random.random() > self._arm_reliability_pct:
                arm_failure = True
                self.arm_position_errors += 1
                print(f"  ⚠️  ARM POSITION ERROR at {self.env.now:.3f}s")
                yield self.env.timeout(10.0)  # Recovery time
                self.unscheduled_downtime_s += 10.0

            # STEP 5: Carton erect (scaled)
            step_time = self._T_CARTON_ERECT_S * self._scale
            yield self.env.timeout(step_time)
            self.carton_stock -= 1
            self.operational_time_s += step_time
            total_energy_time += step_time

            # STEP 6: Robot pick+place (scaled)
            step_time = self._T_ROBOT_PICKPLACE_S * self._scale
            yield self.env.timeout(step_time)
            self.arm_cycles += 1
            self.operational_time_s += step_time
            total_energy_time += step_time

            # STEP 7: Flap fold (scaled)
            step_time = self._T_FLAP_FOLD_S * self._scale
            yield self.env.timeout(step_time)
            self.operational_time_s += step_time
            total_energy_time += step_time

            # STEP 8: Tape seal (scaled) with quality check
            step_time = self._T_TAPE_SEAL_S * self._scale
            yield self.env.timeout(step_time)
            self.tape_stock -= 1
            self.operational_time_s += step_time
            total_energy_time += step_time
            
            # Check seal integrity
            seal_defect = False
            if random.random() > self._seal_integrity_pct:
                seal_defect = True
                self.seal_defects += 1
                print(f"  ⚠️  SEAL DEFECT detected at {self.env.now:.3f}s")

            # STEP 9: Label apply (scaled) with quality check
            step_time = self._T_LABEL_APPLY_S * self._scale
            yield self.env.timeout(step_time)
            self.label_stock -= 1
            self.operational_time_s += step_time
            total_energy_time += step_time
            
            # Check label accuracy
            label_defect = False
            if random.random() > self._label_accuracy_pct:
                label_defect = True
                self.label_defects += 1
                print(f"  ⚠️  LABEL DEFECT detected at {self.env.now:.3f}s")

            # STEP 10: Outfeed (scaled)
            step_time = self._T_OUTFEED_S * self._scale
            yield self.env.timeout(step_time)
            self.operational_time_s += step_time
            total_energy_time += step_time

            # STEP 11: Accumulate energy
            # Apply energy saving mode if enabled
            if self._energy_saving_mode and not self._is_operational():
                # Reduced power consumption during non-operational periods
                effective_power = self._power_rating_w * 0.3
                energy_ws = effective_power * total_energy_time
                self.energy_savings_kwh += (self._power_rating_w * total_energy_time - energy_ws) / 3.6e6
            else:
                energy_ws = self._power_rating_w * total_energy_time
            
            self.energy_kwh += energy_ws / 3.6e6
            
            # Energy cost based on time-of-day tariff
            tariff = self._off_peak_tariff if self._off_peak_enabled and (self.env.now % 3600) > 2520 else self._peak_tariff
            self.energy_cost_usd += (energy_ws / 3.6e6) * tariff

            # STEP 12: Quality rework for defects
            is_defective = seal_defect or label_defect or arm_failure
            if is_defective and self._rework_enabled:
                if random.random() < self._rework_success_rate_pct:
                    print(f"  🔧 REWORKING defective package (time: {self._rework_time_s}s)")
                    self.rework_count += 1
                    yield self.env.timeout(self._rework_time_s)
                    
                    # Energy for rework
                    rework_energy_ws = self._power_rating_w * self._rework_time_s
                    self.energy_kwh += rework_energy_ws / 3.6e6
                    self.energy_cost_usd += (rework_energy_ws / 3.6e6) * tariff
                    
                    print(f"  ✅ REWORK successful - package accepted")
                else:
                    print(f"  🗑️  PACKAGE SCRAPPED - not counted in throughput")
                    self.scrap_count += 1
                    self._busy = False
                    self.state = "IDLE"
                    return

            # STEP 13: SUCCESS - Package completed
            self._cycle_end_s = self.env.now
            actual_time_s = self._cycle_end_s - self._cycle_start_s
            self._actual_cycle_time_ms = int(actual_time_s * 1000)
            
            self._cycle_count += 1
            self._cycle_time_sum_ms += self._actual_cycle_time_ms
            self.packages_completed += 1
            self.completed_cycles += 1
            self.total_busy_time_s += (self.env.now - self.last_busy_start_s)
            
            # Update availability
            self._update_availability()
            
            print(f"  ✅ PACKAGE COMPLETED at env.now={self.env.now:.3f}s | "
                  f"Time: {self._actual_cycle_time_ms}ms | "
                  f"Total packages: {self.packages_completed} | "
                  f"Arm cycles: {self.arm_cycles}")
            
            self._busy = False
            self._done_pulse = True
            self.state = "COMPLETE"
            
        except simpy.Interrupt:
            print("  ⏹️  Cycle interrupted by STOP command")
            self._busy = False
            self._done_pulse = False
            self.state = "IDLE"
            if self.last_busy_start_s > 0:
                partial_time = self.env.now - self.last_busy_start_s
                self.total_busy_time_s += partial_time
                energy_ws = self._power_rating_w * partial_time
                self.energy_kwh += energy_ws / 3.6e6
                self.energy_cost_usd += (energy_ws / 3.6e6) * tariff
            self.last_busy_start_s = 0.0
        finally:
            self._cycle_proc = None

    def stop_cycle(self):
        """Stop any running job"""
        if self._cycle_proc is not None:
            self._cycle_proc.interrupt()
        self._busy = False
        self._done_pulse = False
        self.state = "IDLE"
        if self.last_busy_start_s > 0:
            partial_time = self.env.now - self.last_busy_start_s
            self.total_busy_time_s += partial_time
            energy_ws = self._power_rating_w * partial_time
            self.energy_kwh += energy_ws / 3.6e6
            tariff = self._off_peak_tariff if self._off_peak_enabled and (self.env.now % 3600) > 2520 else self._peak_tariff
            self.energy_cost_usd += (energy_ws / 3.6e6) * tariff
            self.last_busy_start_s = 0.0

    def reset(self):
        """Full reset - reloads config parameters (for new simulation run)"""
        print("  ♻️  FULL RESET - reloading all configuration parameters")
        self.stop_cycle()
        self.state = "IDLE"
        self._busy = False
        self._fault = False
        self._done_pulse = False
        self._cycle_proc = None
        self._in_break = False
        self._in_maintenance = False
        self._in_shift_change = False
        self._in_arm_calibration = False
        self._in_end_effector_replace = False
        
        self._actual_cycle_time_ms = int(self._effective_cycle_time_s * 1000)
        self._cycle_count = 0
        self._cycle_time_sum_ms = 0
        
        # Material inventory
        self.carton_stock = self._carton_capacity
        self.tape_stock = self._tape_capacity
        self.label_stock = self._label_capacity
        self.carton_replenishments = 0
        self.tape_replenishments = 0
        self.label_replenishments = 0
        self.material_stockouts = 0
        
        # KPI tracking
        self.packages_completed = 0
        self.arm_cycles = 0
        self.total_repairs = 0
        self.operational_time_s = 0.0
        self.downtime_s = 0.0
        self.availability = 0.0
        self.total_downtime_s = 0.0
        self.total_busy_time_s = 0.0
        self.last_busy_start_s = 0.0
        self.failure_count = 0
        self.completed_cycles = 0
        self.preventive_maintenance_count = 0
        self.scheduled_downtime_s = 0.0
        self.unscheduled_downtime_s = 0.0
        
        # Quality tracking
        self.seal_defects = 0
        self.label_defects = 0
        self.arm_position_errors = 0
        self.rework_count = 0
        self.scrap_count = 0
        
        # ENERGY TRACKING
        self.energy_kwh = 0.0
        self.energy_cost_usd = 0.0
        self.energy_savings_kwh = 0.0
        
        # Reschedule recurring events
        self._schedule_breaks()
        self._schedule_preventive_maintenance()
        self._schedule_shift_boundaries()
        self._schedule_arm_calibration()
        self._schedule_material_replenishment()
        print("  ✅ Reset complete - station ready for new simulation")

    # === STATE GETTERS ===
    def is_busy(self):
        return self._busy
    
    def is_fault(self):
        return self._fault
    
    def get_done_pulse(self):
        return self._done_pulse
    
    def clear_done_pulse(self):
        was_set = self._done_pulse
        self._done_pulse = False
        return was_set
    
    def get_cycle_time_ms(self):
        return self._actual_cycle_time_ms if self._actual_cycle_time_ms > 0 else int(self._effective_cycle_time_s * 1000)
    
    def get_avg_cycle_time_ms(self):
        if self._cycle_count > 0:
            return int(self._cycle_time_sum_ms / self._cycle_count)
        return int(self._effective_cycle_time_s * 1000)
    
    def has_active_proc(self):
        return self._cycle_proc is not None

    # === KPI GETTERS ===
    def get_utilization(self, total_sim_time_s: float) -> float:
        if total_sim_time_s <= 0:
            return 0.0
        return (self.total_busy_time_s / total_sim_time_s) * 100.0
    
    def get_availability(self, total_sim_time_s: float) -> float:
        if total_sim_time_s <= 0:
            return 0.0
        uptime = total_sim_time_s - self.total_downtime_s
        return (uptime / total_sim_time_s) * 100.0
    
    def get_oee(self, total_sim_time_s: float) -> float:
        if total_sim_time_s <= 0 or self.completed_cycles == 0:
            return 0.0
        
        availability = self.get_availability(total_sim_time_s) / 100.0
        
        ideal_cycle_time_s = self._nominal_cycle_time_s
        operating_time_s = total_sim_time_s - self.total_downtime_s - self.scheduled_downtime_s
        if operating_time_s <= 0:
            performance = 0.0
        else:
            performance = (ideal_cycle_time_s * self.completed_cycles) / operating_time_s
        
        quality = 1.0 - (self.scrap_count / max(self.completed_cycles, 1))
        
        return availability * performance * quality * 100.0
    
    def get_quality_rate_pct(self) -> float:
        if self.completed_cycles == 0:
            return 0.0
        good_units = self.completed_cycles - self.scrap_count
        return (good_units / self.completed_cycles) * 100.0
    
    def get_total_downtime_s(self) -> float:
        return self.total_downtime_s + self.scheduled_downtime_s
    
    def get_failure_count(self) -> int:
        return self.failure_count
    
    def _update_availability(self):
        total = self.operational_time_s + self.downtime_s
        if total > 0:
            self.availability = (self.operational_time_s / total) * 100.0
        else:
            self.availability = 0.0

    # === ENERGY GETTERS ===
    def get_energy_kwh(self) -> float:
        return self.energy_kwh
    
    def get_energy_per_unit_kwh(self) -> float:
        if self.packages_completed > 0:
            return self.energy_kwh / self.packages_completed
        return 0.0
    
    def get_energy_cost_usd(self) -> float:
        return self.energy_cost_usd
    
    def get_co2_emissions_kg(self) -> float:
        return self.energy_kwh * self._co2_factor

# VSI <-> SimPy Wrapper with FULL CONFIG LOADING
class ST6_SimRuntime:
    def __init__(self):
        # Load FULL configuration from line_config.json
        self.full_config = self._load_full_config()
        
        # Extract sections
        self.station_config = self.full_config.get("stations", {}).get("S6", {})
        self.human_resources = self.full_config.get("human_resources", {})
        self.maintenance = self.full_config.get("maintenance", {})
        self.shift_schedule = self.full_config.get("shift_schedule", {})
        self.quality = self.full_config.get("quality", {})
        self.energy_mgmt = self.full_config.get("energy_management", {})
        self.material_handling = self.full_config.get("material_handling", {})
        self.robotic_arm = self.full_config.get("robotic_arm", {})
        
        # Apply station-specific overrides
        if "material_handling" in self.station_config:
            self.material_handling.update(self.station_config["material_handling"])
        if "robotic_arm" in self.station_config:
            self.robotic_arm.update(self.station_config["robotic_arm"])
        
        print(f"ST6_SimRuntime: Loaded FULL configuration from line_config.json")
        print(f"  Station S6: cycle={self.station_config.get('cycle_time_s', 6.7)}s, failure={self.station_config.get('failure_rate', 0.02)}")
        print(f"  Material: Carton cap={self.material_handling.get('carton_capacity', 50)}")
        print(f"  Robotic Arm: Speed={self.robotic_arm.get('arm_speed_pct', 100)}%, Reliability={self.robotic_arm.get('arm_reliability_pct', 98.5)}%")
        print(f"  Shifts: {self.shift_schedule.get('shifts_per_day', 1)} shifts × {self.shift_schedule.get('shift_duration_h', 8)}h")
        
        self.env = simpy.Environment()
        self.station = FixedPackagingStation(
            self.env,
            self.station_config,
            self.human_resources,
            self.maintenance,
            self.shift_schedule,
            self.quality,
            self.energy_mgmt,
            self.material_handling,
            self.robotic_arm
        )
        
        # Handshake state
        self._start_latched = False
        self._prev_cmd_start = 0
        self._prev_cmd_stop = 0
        self._prev_cmd_reset = 0
        self.batch_id = 0
        self.recipe_id = 0
        self._last_start_edge = False
        self._last_step_dt = 0.0

    def _load_full_config(self) -> dict:
        """Load COMPLETE configuration from line_config.json with all sections"""
        config_path = "line_config.json"
        default_full_config = {
            "simulation_time_s": 3600,
            "stations": {
                "S1": {"cycle_time_s": 9.597, "failure_rate": 0.02, "mttr_s": 30, "buffer_capacity": 2, "power_rating_w": 1500},
                "S2": {"cycle_time_s": 12.3, "failure_rate": 0.05, "mttr_s": 45, "buffer_capacity": 2, "power_rating_w": 2200},
                "S3": {"cycle_time_s": 8.7, "failure_rate": 0.03, "mttr_s": 25, "buffer_capacity": 2, "power_rating_w": 1800},
                "S4": {"cycle_time_s": 15.2, "failure_rate": 0.08, "mttr_s": 60, "buffer_capacity": 2, "power_rating_w": 3500},
                "S5": {"cycle_time_s": 6.4, "base_accept_rate": 0.88, "failure_rate": 0.01, "mttr_s": 15, "buffer_capacity": 2, "power_rating_w": 800},
                "S6": {
                    "cycle_time_s": 6.7,
                    "failure_rate": 0.02,
                    "mttr_s": 35,
                    "buffer_capacity": 2,
                    "power_rating_w": 2000,
                    "material_handling": {
                        "carton_capacity": 50,
                        "tape_capacity": 50,
                        "label_capacity": 50,
                        "carton_reorder_point": 15,
                        "tape_reorder_point": 15,
                        "label_reorder_point": 15,
                        "carton_refill_time_s": 120,
                        "tape_refill_time_s": 90,
                        "label_refill_time_s": 105,
                        "auto_replenishment": True,
                        "just_in_time": False,
                        "carton_erect_time_s": 1.0,
                        "flap_fold_time_s": 1.5,
                        "tape_seal_time_s": 1.2,
                        "label_apply_time_s": 1.0,
                        "outfeed_time_s": 0.8
                    },
                    "robotic_arm": {
                        "arm_speed_pct": 100,
                        "arm_reliability_pct": 98.5,
                        "pick_place_time_s": 1.2,
                        "calibration_interval_h": 168,
                        "calibration_duration_min": 20,
                        "end_effector_lifetime_cycles": 10000,
                        "end_effector_replace_time_s": 300
                    }
                }
            },
            "buffers": {
                "S1_to_S2": 2,
                "S2_to_S3": 2,
                "S3_to_S4": 2,
                "S4_to_S5": 2,
                "S5_to_S6": 2
            },
            "human_resources": {
                "operators_per_shift": 4,
                "maintenance_technicians": 2,
                "operator_efficiency_factor": 95,
                "advanced_skill_pct": 30,
                "cross_training_pct": 20,
                "break_time_min_per_hour": 5,
                "shift_changeover_min": 10
            },
            "shift_schedule": {
                "shifts_per_day": 1,
                "shift_duration_h": 8,
                "working_days_per_week": 5,
                "overtime_enabled": False,
                "breaks_per_shift": 2,
                "break_duration_min": 15,
                "lunch_break_min": 30
            },
            "maintenance": {
                "strategy": "predictive",
                "preventive_interval_h": 160,
                "preventive_duration_min": 45,
                "predictive_enabled": True,
                "predictive_mttr_reduction_pct": 25,
                "predictive_failure_reduction_pct": 30,
                "condition_monitoring": True,
                "iot_sensors": True,
                "maintenance_log_enabled": True,
                "maintenance_cost_per_hour": 120,
                "oee_target_pct": 85,
                "mttr_target_min": 30,
                "mtbf_target_h": 200
            },
            "energy_management": {
                "off_peak_enabled": False,
                "peak_tariff": 0.18,
                "off_peak_tariff": 0.08,
                "co2_factor_kg_per_kwh": 0.4,
                "energy_monitoring_enabled": True,
                "iso50001_compliant": True,
                "energy_saving_mode": False
            },
            "quality": {
                "defect_rate_pct": 0.3,
                "seal_integrity_pct": 99.5,
                "label_accuracy_pct": 99.8,
                "rework_enabled": True,
                "rework_time_s": 45,
                "rework_success_rate_pct": 85,
                "inspection_enabled": True,
                "first_pass_yield_target": 99.0
            },
            "material_handling": {
                "carton_capacity": 50,
                "tape_capacity": 50,
                "label_capacity": 50,
                "carton_reorder_point": 15,
                "tape_reorder_point": 15,
                "label_reorder_point": 15,
                "carton_refill_time_s": 120,
                "tape_refill_time_s": 90,
                "label_refill_time_s": 105,
                "auto_replenishment": True,
                "just_in_time": False,
                "carton_erect_time_s": 1.0,
                "flap_fold_time_s": 1.5,
                "tape_seal_time_s": 1.2,
                "label_apply_time_s": 1.0,
                "outfeed_time_s": 0.8
            },
            "robotic_arm": {
                "arm_speed_pct": 100,
                "arm_reliability_pct": 98.5,
                "pick_place_time_s": 1.2,
                "calibration_interval_h": 168,
                "calibration_duration_min": 20,
                "end_effector_lifetime_cycles": 10000,
                "end_effector_replace_time_s": 300,
                "vendor": "Fanuc",
                "model": "LR Mate 200iD"
            }
        }
        
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    return json.load(f)
            except Exception as e:
                print(f"  ⚠️  WARNING: Error loading {config_path}: {e} - using defaults")
                return default_full_config
        else:
            print(f"  ⚠️  WARNING: {config_path} not found - using default parameters")
            self._create_default_config()
            return default_full_config

    def _create_default_config(self):
        """Create default line_config.json with ALL sections if missing"""
        default_full_config = {
            "simulation_metadata": {
                "name": "3D Printer Manufacturing Line",
                "version": "3.0",
                "last_modified": datetime.now().isoformat(),
                "stations": 6,
                "simulation_time_h": 8,
                "simulation_time_s": 28800
            },
            "stations": {
                "S1": {"cycle_time_s": 9.597, "failure_rate": 0.02, "mttr_s": 30, "mtbf_h": 50, "power_rating_w": 1500},
                "S2": {"cycle_time_s": 12.3, "failure_rate": 0.05, "mttr_s": 45, "mtbf_h": 20, "power_rating_w": 2200},
                "S3": {"cycle_time_s": 8.7, "failure_rate": 0.03, "mttr_s": 25, "mtbf_h": 33.3, "power_rating_w": 1800},
                "S4": {"cycle_time_s": 15.2, "failure_rate": 0.08, "mttr_s": 60, "mtbf_h": 12.5, "power_rating_w": 3500},
                "S5": {"cycle_time_s": 6.4, "base_accept_rate": 0.88, "failure_rate": 0.01, "mttr_s": 15, "mtbf_h": 100, "power_rating_w": 800},
                "S6": {
                    "cycle_time_s": 6.7,
                    "failure_rate": 0.02,
                    "mttr_s": 35,
                    "mtbf_h": 50,
                    "buffer_capacity": 2,
                    "power_rating_w": 2000,
                    "equipment": "Automated Box Sealer / Taping Machine",
                    "quantity": "1 unit",
                    "material_handling": {
                        "carton_capacity": 50,
                        "tape_capacity": 50,
                        "label_capacity": 50,
                        "carton_reorder_point": 15,
                        "tape_reorder_point": 15,
                        "label_reorder_point": 15,
                        "carton_refill_time_s": 120,
                        "tape_refill_time_s": 90,
                        "label_refill_time_s": 105,
                        "auto_replenishment": True,
                        "just_in_time": False
                    },
                    "robotic_arm": {
                        "arm_speed_pct": 100,
                        "arm_reliability_pct": 98.5,
                        "pick_place_time_s": 1.2,
                        "calibration_interval_h": 168,
                        "calibration_duration_min": 20,
                        "end_effector_lifetime_cycles": 10000,
                        "end_effector_replace_time_s": 300
                    }
                }
            },
            "buffers": {
                "S1_to_S2": 5,
                "S2_to_S3": 5,
                "S3_to_S4": 5,
                "S4_to_S5": 5,
                "S5_to_S6": 5
            },
            "human_resources": {
                "operators_per_shift": 4,
                "technicians_on_call": 2,
                "maintenance_technicians": 2,
                "skill_level_pct": {"basic": 60, "advanced": 30, "expert": 10},
                "advanced_skill_pct": 30,
                "operator_efficiency_factor": 95,
                "training_level": "intermediate",
                "cross_training_pct": 20,
                "break_time_min_per_hour": 5,
                "shift_changeover_min": 10
            },
            "shift_schedule": {
                "shifts_per_day": 1,
                "shift_duration_h": 8,
                "breaks_per_shift": 2,
                "break_duration_min": 15,
                "lunch_break_min": 30,
                "working_days_per_week": 5,
                "overtime_enabled": False,
                "overtime_hours": 2
            },
            "maintenance": {
                "strategy": "predictive",
                "preventive_interval_h": 160,
                "preventive_duration_min": 45,
                "predictive_enabled": True,
                "predictive_mttr_reduction_pct": 25,
                "predictive_failure_reduction_pct": 30,
                "condition_monitoring": True,
                "iot_sensors": True,
                "maintenance_log_enabled": True,
                "maintenance_cost_per_hour": 120,
                "oee_target_pct": 85,
                "mttr_target_min": 30,
                "mtbf_target_h": 200
            },
            "energy_management": {
                "off_peak_enabled": False,
                "off_peak_tariff": 0.08,
                "peak_tariff": 0.18,
                "peak_hours": ["08:00-12:00", "17:00-20:00"],
                "energy_saving_mode": False,
                "iso50001_compliant": True,
                "co2_factor_kg_per_kwh": 0.4,
                "energy_monitoring_enabled": True
            },
            "quality": {
                "defect_rate_pct": 0.3,
                "seal_integrity_pct": 99.5,
                "label_accuracy_pct": 99.8,
                "rework_enabled": True,
                "rework_time_s": 45,
                "rework_success_rate_pct": 85,
                "inspection_enabled": True,
                "first_pass_yield_target": 99.0
            },
            "material_handling": {
                "carton_capacity": 50,
                "tape_capacity": 50,
                "label_capacity": 50,
                "carton_reorder_point": 15,
                "tape_reorder_point": 15,
                "label_reorder_point": 15,
                "carton_refill_time_s": 120,
                "tape_refill_time_s": 90,
                "label_refill_time_s": 105,
                "auto_replenishment": True,
                "just_in_time": False
            },
            "robotic_arm": {
                "arm_speed_pct": 100,
                "arm_reliability_pct": 98.5,
                "pick_place_time_s": 1.2,
                "calibration_interval_h": 168,
                "calibration_duration_min": 20,
                "end_effector_lifetime_cycles": 10000,
                "end_effector_replace_time_s": 300,
                "vendor": "Fanuc",
                "model": "LR Mate 200iD"
            }
        }
        try:
            with open("line_config.json", 'w') as f:
                json.dump(default_full_config, f, indent=2)
            print("  ✅ Created comprehensive default line_config.json with ALL parameters")
        except Exception as e:
            print(f"  ⚠️  Could not create default config: {e}")

    def reset(self):
        """Full reset - reloads ALL configuration sections"""
        print("  ♻️  ST6_SimRuntime: FULL RESET (reloading ALL configuration sections)")
        self.full_config = self._load_full_config()
        self.station_config = self.full_config.get("stations", {}).get("S6", {})
        self.human_resources = self.full_config.get("human_resources", {})
        self.maintenance = self.full_config.get("maintenance", {})
        self.shift_schedule = self.full_config.get("shift_schedule", {})
        self.quality = self.full_config.get("quality", {})
        self.energy_mgmt = self.full_config.get("energy_management", {})
        self.material_handling = self.full_config.get("material_handling", {})
        self.robotic_arm = self.full_config.get("robotic_arm", {})
        
        # Apply station-specific overrides
        if "material_handling" in self.station_config:
            self.material_handling.update(self.station_config["material_handling"])
        if "robotic_arm" in self.station_config:
            self.robotic_arm.update(self.station_config["robotic_arm"])
        
        self.env = simpy.Environment()
        self.station = FixedPackagingStation(
            self.env,
            self.station_config,
            self.human_resources,
            self.maintenance,
            self.shift_schedule,
            self.quality,
            self.energy_mgmt,
            self.material_handling,
            self.robotic_arm
        )
        self._start_latched = False
        self._prev_cmd_start = 0
        self._prev_cmd_stop = 0
        self._prev_cmd_reset = 0
        print("  ✅ ST6_SimRuntime: Reset complete - ready for new simulation")

    def set_context(self, batch_id: int, recipe_id: int):
        self.batch_id = int(batch_id)
        self.recipe_id = int(recipe_id)

    def update_handshake(self, cmd_start: int, cmd_stop: int, cmd_reset: int):
        """Process PLC commands with full operational state awareness"""
        # Reset has highest priority
        if cmd_reset and not self._prev_cmd_reset:
            print("  🔄 ST6_SimRuntime: RESET command (rising edge)")
            self.reset()
            self._prev_cmd_reset = 1
            return
        self._prev_cmd_reset = int(cmd_reset)

        # Rising edge detection for start
        start_edge = (cmd_start == 1 and self._prev_cmd_start == 0)
        self._last_start_edge = start_edge

        # Stop command (rising edge) - immediate stop
        if cmd_stop and not self._prev_cmd_stop:
            print("  ⏹️  ST6_SimRuntime: STOP command (rising edge)")
            self._start_latched = False
            self.station.stop_cycle()
            self._prev_cmd_stop = int(cmd_stop)

        # Start logic: ONLY on rising edge AND station operational
        if start_edge:
            if self.station._is_operational():
                print(f"  ▶️  ST6_SimRuntime: START rising edge - starting cycle")
                self._start_latched = True
                self.station.start_cycle(self.batch_id, self.recipe_id, self.env.now)
            else:
                reasons = []
                if self.station._in_break: reasons.append("BREAK")
                if self.station._in_maintenance: reasons.append("MAINTENANCE")
                if self.station._in_arm_calibration: reasons.append("ARM CALIBRATION")
                if self.station._in_end_effector_replace: reasons.append("END EFFECTOR REPLACE")
                if self.station._in_shift_change: reasons.append("SHIFT CHANGE")
                if self.station._fault: reasons.append("FAULT")
                if not self.station._is_within_shift_hours(): reasons.append("OFF-SHIFT")
                print(f"  ⚠️  ST6_SimRuntime: START ignored - not operational ({'/'.join(reasons)})")

        self._prev_cmd_start = int(cmd_start)

        # Safety checks
        if self.station.is_busy() and not self._start_latched:
            print("  ⚠️  FIXING: station busy but start_latched=False")
            self._start_latched = True
        if self.station.has_active_proc() and not self.station.is_busy():
            print("  ⚠️  FIXING: active process but busy=False")
            self.station._busy = True

    def step(self, dt_s: float):
        """Advance simulation with full operational constraints"""
        self._last_step_dt = dt_s
        
        should_step = (self.station.is_busy() or 
                      self.station.is_fault() or 
                      self.station._in_maintenance or 
                      self.station._in_arm_calibration or
                      self.station._in_end_effector_replace or
                      self.station._in_break or
                      self.station._in_shift_change or
                      self._start_latched)
        
        if self._prev_cmd_stop and not (self.station.is_busy() or self.station.is_fault()):
            return
        
        if should_step and dt_s > 0:
            target_time = self.env.now + float(dt_s)
            self.env.run(until=target_time)
            
            if self.station.get_done_pulse():
                self._start_latched = False

    def outputs(self, total_sim_time_s: float):
        """Get station outputs with comprehensive KPI awareness"""
        busy = 1 if self.station.is_busy() else 0
        fault = 1 if self.station.is_fault() else 0
        
        ready = 1 if (not busy and not fault and self.station._is_operational()) else 0
        
        done = 1 if self.station.get_done_pulse() else 0
        
        cycle_time_ms = self.station.get_cycle_time_ms()
        
        packages_completed = self.station.packages_completed
        arm_cycles = self.station.arm_cycles
        total_repairs = self.station.total_repairs
        operational_time_s = self.station.operational_time_s
        downtime_s = self.station.downtime_s
        availability = self.station.availability
        
        if self.station.completed_cycles > 0 and self.station.completed_cycles % 10 == 0:
            utilization = self.station.get_utilization(total_sim_time_s)
            availability_kpi = self.station.get_availability(total_sim_time_s)
            oee = self.station.get_oee(total_sim_time_s)
            quality_rate = self.station.get_quality_rate_pct()
            energy_per_unit = self.station.get_energy_per_unit_kwh()
            
            print(f"  📊 ST6 COMPREHENSIVE KPIs (cycle #{self.station.completed_cycles}):")
            print(f"     Utilization: {utilization:.1f}% | Availability: {availability_kpi:.1f}% | OEE: {oee:.1f}%")
            print(f"     Quality Rate: {quality_rate:.1f}% | Scrap: {self.station.scrap_count} | Reworks: {self.station.rework_count}")
            print(f"     Seal Defects: {self.station.seal_defects} | Label Defects: {self.station.label_defects}")
            print(f"     Arm Errors: {self.station.arm_position_errors} | Arm Cycles: {self.station.arm_cycles}")
            print(f"     Energy: {self.station.get_energy_kwh():.4f}kWh total | {energy_per_unit:.4f}kWh/unit | Cost: ${self.station.get_energy_cost_usd():.2f}")
            print(f"     CO2: {self.station.get_co2_emissions_kg():.2f}kg | Failures: {self.station.failure_count}")
            print(f"     Material: Carton:{self.station.carton_stock}, Tape:{self.station.tape_stock}, Label:{self.station.label_stock}")
        
        return (ready, busy, fault, done, cycle_time_ms, packages_completed, 
                arm_cycles, total_repairs, operational_time_s, downtime_s, availability)

    def export_kpis(self, total_sim_time_s: float) -> dict:
        """Export FULL KPI set for optimizer dashboard integration"""
        utilization = self.station.get_utilization(total_sim_time_s)
        availability = self.station.get_availability(total_sim_time_s)
        oee = self.station.get_oee(total_sim_time_s)
        quality_rate = self.station.get_quality_rate_pct()
        energy_per_unit = self.station.get_energy_per_unit_kwh()
        
        throughput_units_per_hour = (self.station.packages_completed / total_sim_time_s) * 3600 if total_sim_time_s > 0 else 0
        mtbf_h = (total_sim_time_s / 3600) / max(self.station.failure_count, 1) if self.station.failure_count > 0 else 0
        
        return {
            "station": "S6",
            "station_name": "Packaging & Dispatch",
            "simulation_duration_s": total_sim_time_s,
            "packages_completed": self.station.packages_completed,
            "completed_cycles": self.station.completed_cycles,
            "throughput_units_per_hour": round(throughput_units_per_hour, 2),
            
            # Quality metrics
            "seal_defects": self.station.seal_defects,
            "label_defects": self.station.label_defects,
            "arm_position_errors": self.station.arm_position_errors,
            "rework_count": self.station.rework_count,
            "scrap_count": self.station.scrap_count,
            "quality_rate_pct": round(quality_rate, 2),
            "first_pass_yield_pct": round((self.station.completed_cycles - self.station.rework_count) / max(self.station.completed_cycles, 1) * 100, 2),
            
            # Material metrics
            "carton_consumption": self._carton_capacity - self.station.carton_stock,
            "tape_consumption": self._tape_capacity - self.station.tape_stock,
            "label_consumption": self._label_capacity - self.station.label_stock,
            "carton_replenishments": self.station.carton_replenishments,
            "tape_replenishments": self.station.tape_replenishments,
            "label_replenishments": self.station.label_replenishments,
            "material_stockouts": self.station.material_stockouts,
            
            # Robotic arm metrics
            "arm_cycles": self.station.arm_cycles,
            "arm_reliability_pct": self.robotic_arm.get("arm_reliability_pct", 98.5),
            "arm_calibrations": self.station.calibration_count if hasattr(self.station, 'calibration_count') else 0,
            
            # Downtime metrics
            "total_downtime_s": round(self.station.get_total_downtime_s(), 2),
            "scheduled_downtime_s": round(self.station.scheduled_downtime_s, 2),
            "unscheduled_downtime_s": round(self.station.unscheduled_downtime_s, 2),
            "failure_count": self.station.failure_count,
            "preventive_maintenance_count": self.station.preventive_maintenance_count,
            "total_repairs": self.station.total_repairs,
            
            # Performance metrics
            "utilization_pct": round(utilization, 2),
            "availability_pct": round(availability, 2),
            "oee_pct": round(oee, 2),
            "mtbf_h": round(mtbf_h, 2),
            "mttr_s": round(self.station._effective_mttr_s, 2),
            "avg_cycle_time_ms": self.station.get_avg_cycle_time_ms(),
            "operational_time_s": round(self.station.operational_time_s, 2),
            "downtime_s": round(self.station.downtime_s, 2),
            "station_availability_pct": round(self.station.availability, 2),
            
            # Energy metrics
            "energy_kwh": round(self.station.get_energy_kwh(), 4),
            "energy_per_unit_kwh": round(energy_per_unit, 4),
            "energy_cost_usd": round(self.station.get_energy_cost_usd(), 2),
            "energy_savings_kwh": round(self.station.energy_savings_kwh, 4),
            "co2_emissions_kg": round(self.station.get_co2_emissions_kg(), 2),
            "power_rating_w": self.station_config.get("power_rating_w", 2000),
            
            # Configuration snapshot
            "config_snapshot": {
                "cycle_time_s": self.station_config.get("cycle_time_s", 6.7),
                "failure_rate": self.station_config.get("failure_rate", 0.02),
                "mttr_s": self.station_config.get("mttr_s", 35),
                "power_rating_w": self.station_config.get("power_rating_w", 2000),
                "operator_efficiency_factor": self.human_resources.get("operator_efficiency_factor", 95),
                "arm_speed_pct": self.robotic_arm.get("arm_speed_pct", 100),
                "arm_reliability_pct": self.robotic_arm.get("arm_reliability_pct", 98.5),
                "maintenance_strategy": self.maintenance.get("strategy", "predictive"),
                "seal_integrity_pct": self.quality.get("seal_integrity_pct", 99.5),
                "label_accuracy_pct": self.quality.get("label_accuracy_pct", 99.8),
                "rework_enabled": self.quality.get("rework_enabled", True),
                "shifts_per_day": self.shift_schedule.get("shifts_per_day", 1),
                "auto_replenishment": self.material_handling.get("auto_replenishment", True)
            }
        }
# End of user custom code region. Please don't edit beyond this point.

class ST6_PackagingDispatch:

    def __init__(self, args):
        self.componentId = 6
        self.localHost = args.server_url
        self.domain = args.domain
        self.portNum = 50107
        
        self.simulationStep = 0
        self.stopRequested = False
        self.totalSimulationTime = 0
        
        self.receivedNumberOfBytes = 0
        self.receivedPayload = []

        self.numberOfPorts = 1
        self.clientPortNum = [0] * self.numberOfPorts
        self.receivedDestPortNumber = 0
        self.receivedSrcPortNumber = 0
        self.expectedNumberOfBytes = 0
        self.mySignals = MySignals()

        # Start of user custom code region. Please apply edits only within these regions:  Constructor
        self._sim = None
        self._prev_done = 0
        self.total_completed = 0
        self._sim_start_time_ns = 0
        print("ST6: Initializing...")
        # End of user custom code region. Please don't edit beyond this point.

    def mainThread(self):
        dSession = vsiCommonPythonApi.connectToServer(self.localHost, self.domain, self.portNum, self.componentId)
        vsiEthernetPythonGateway.initialize(dSession, self.componentId, bytes(srcMacAddress), bytes(srcIpAddress))
        try:
            vsiCommonPythonApi.waitForReset()

            # Start of user custom code region. Please apply edits only within these regions:  After Reset
            self._sim_start_time_ns = vsiCommonPythonApi.getSimulationTimeInNs()
            self._sim = ST6_SimRuntime()
            self._prev_done = 0
            self.total_completed = 0
            print("ST6: Parameterized SimPy runtime initialized with config from line_config.json")
            # End of user custom code region. Please don't edit beyond this point.

            self.updateInternalVariables()

            if(vsiCommonPythonApi.isStopRequested()):
                raise Exception("stopRequested")
            self.establishTcpUdpConnection()
            nextExpectedTime = vsiCommonPythonApi.getSimulationTimeInNs()
            while(vsiCommonPythonApi.getSimulationTimeInNs() < self.totalSimulationTime):

                self.updateInternalVariables()

                if(vsiCommonPythonApi.isStopRequested()):
                    raise Exception("stopRequested")

                if(vsiEthernetPythonGateway.isTerminationOnGoing()):
                    print("Termination is on going")
                    break

                if(vsiEthernetPythonGateway.isTerminated()):
                    print("Application terminated")
                    break

                receivedData = vsiEthernetPythonGateway.recvEthernetPacket(PLC_LineCoordinatorSocketPortNumber0)
                if(receivedData[3] != 0):
                    self.decapsulateReceivedData(receivedData)

                # Start of user custom code region. Please apply edits only within these regions:  Before sending the packet
                # Process handshake and simulation stepping AFTER receiving the packet
                if self._sim is not None:
                    # Update context
                    self._sim.set_context(self.mySignals.batch_id, self.mySignals.recipe_id)
                    
                    # Process PLC commands and update handshake state
                    self._sim.update_handshake(
                        self.mySignals.cmd_start,
                        self.mySignals.cmd_stop,
                        self.mySignals.cmd_reset
                    )
                    
                    # Advance simulation time ONLY when appropriate
                    dt_s = float(self.simulationStep) / 1e9 if self.simulationStep else 0.0
                    self._sim.step(dt_s)
                    
                    # Get outputs from SimPy (pass total sim time for KPI calculation)
                    total_sim_time_s = (vsiCommonPythonApi.getSimulationTimeInNs() - self._sim_start_time_ns) / 1e9
                    (ready, busy, fault, done, cycle_time_ms, packages_completed, 
                     arm_cycles, total_repairs, operational_time_s, downtime_s, 
                     availability) = self._sim.outputs(total_sim_time_s)
                    
                    # Copy SimPy outputs into VSI signals
                    self.mySignals.ready = int(ready)
                    self.mySignals.busy = int(busy)
                    self.mySignals.fault = int(fault)
                    self.mySignals.done = int(done)
                    self.mySignals.cycle_time_ms = int(cycle_time_ms)
                    self.mySignals.packages_completed = int(packages_completed)
                    self.mySignals.arm_cycles = int(arm_cycles)
                    self.mySignals.total_repairs = int(total_repairs)
                    self.mySignals.operational_time_s = float(operational_time_s)
                    self.mySignals.downtime_s = float(downtime_s)
                    self.mySignals.availability = float(availability)
                    
                    # Track completions
                    if done and not self._prev_done:
                        self.total_completed += 1
                        print(f"ST6: Package completed! cycle_time={cycle_time_ms}ms, total={self.total_completed}")
                    
                    # Update previous done state
                    self._prev_done = int(self.mySignals.done)
                # End of user custom code region. Please don't edit beyond this point.

                #Send ethernet packet to PLC_LineCoordinator
                self.sendEthernetPacketToPLC_LineCoordinator()

                # Start of user custom code region. Please apply edits only within these regions:  After sending the packet
                # End of user custom code region. Please don't edit beyond this point.

                print("\n+=ST6_PackagingDispatch+=")
                print("  VSI time:", end = " ")
                print(vsiCommonPythonApi.getSimulationTimeInNs(), end = " ")
                print("ns")
                print("  Inputs:")
                print("\tcmd_start =", end = " ")
                print(self.mySignals.cmd_start)
                print("\tcmd_stop =", end = " ")
                print(self.mySignals.cmd_stop)
                print("\tcmd_reset =", end = " ")
                print(self.mySignals.cmd_reset)
                print("\tbatch_id =", end = " ")
                print(self.mySignals.batch_id)
                print("\trecipe_id =", end = " ")
                print(self.mySignals.recipe_id)
                print("  Outputs:")
                print("\tready =", end = " ")
                print(self.mySignals.ready)
                print("\tbusy =", end = " ")
                print(self.mySignals.busy)
                print("\tfault =", end = " ")
                print(self.mySignals.fault)
                print("\tdone =", end = " ")
                print(self.mySignals.done)
                print("\tcycle_time_ms =", end = " ")
                print(self.mySignals.cycle_time_ms)
                print("\tpackages_completed =", end = " ")
                print(self.mySignals.packages_completed)
                print("\tarm_cycles =", end = " ")
                print(self.mySignals.arm_cycles)
                print("\ttotal_repairs =", end = " ")
                print(self.mySignals.total_repairs)
                print("\toperational_time_s =", end = " ")
                print(self.mySignals.operational_time_s)
                print("\tdowntime_s =", end = " ")
                print(self.mySignals.downtime_s)
                print("\tavailability =", end = " ")
                print(self.mySignals.availability)
                print(f"  Internal: total_completed={self.total_completed}")
                if self._sim is not None:
                    print(f"  SimState: start_latched={self._sim._start_latched}, fault={self.mySignals.fault}")
                print("\n")

                self.updateInternalVariables()

                if(vsiCommonPythonApi.isStopRequested()):
                    raise Exception("stopRequested")
                nextExpectedTime += self.simulationStep

                if(vsiCommonPythonApi.getSimulationTimeInNs() >= nextExpectedTime):
                    continue

                if(nextExpectedTime > self.totalSimulationTime):
                    remainingTime = self.totalSimulationTime - vsiCommonPythonApi.getSimulationTimeInNs()
                    vsiCommonPythonApi.advanceSimulation(remainingTime)
                    break

                vsiCommonPythonApi.advanceSimulation(nextExpectedTime - vsiCommonPythonApi.getSimulationTimeInNs())

            # Start of user custom code region. Please apply edits only within these regions:  Simulation Complete
            # Export KPIs at end of simulation
            if self._sim is not None:
                total_sim_time_s = (vsiCommonPythonApi.getSimulationTimeInNs() - self._sim_start_time_ns) / 1e9
                kpis = self._sim.export_kpis(total_sim_time_s)
                kpis["simulation_duration_s"] = total_sim_time_s
                
                kpi_file = f"ST6_kpis_{int(vsiCommonPythonApi.getSimulationTimeInNs()/1e9)}.json"
                with open(kpi_file, 'w') as f:
                    json.dump(kpis, f, indent=2)
                print(f"\n✅ ST6 KPIs exported to {kpi_file}")
                print(f"   Throughput: {kpis['packages_completed'] / total_sim_time_s * 3600:.1f} units/hour")
                print(f"   Energy: {kpis['energy_kwh']:.4f} kWh total")
                print(f"   Energy per unit: {kpis['energy_per_unit_kwh']:.4f} kWh/unit")
                print(f"   Utilization: {kpis['utilization_pct']:.1f}%")
                print(f"   Availability: {kpis['availability_pct']:.1f}%")
                print(f"   OEE: {kpis['oee_pct']:.1f}%")
                print(f"   Quality Rate: {kpis['quality_rate_pct']:.1f}%")
                print(f"   Catastrophic failures: {kpis['failure_count']}")
                print(f"   Total downtime: {kpis['total_downtime_s']:.1f}s")
            # End of user custom code region. Please don't edit beyond this point.

            if(vsiCommonPythonApi.getSimulationTimeInNs() < self.totalSimulationTime):
                vsiEthernetPythonGateway.terminate()
        except Exception as e:
            if str(e) == "stopRequested":
                print("Terminate signal has been received from one of the VSI clients")
                vsiCommonPythonApi.advanceSimulation(self.simulationStep + 1)
            else:
                print(f"An error occurred: {str(e)}")
                import traceback
                traceback.print_exc()
        except:
            vsiCommonPythonApi.advanceSimulation(self.simulationStep + 1)
            import traceback
            traceback.print_exc()

    def establishTcpUdpConnection(self):
        if(self.clientPortNum[ST6_PackagingDispatch0] == 0):
            self.clientPortNum[ST6_PackagingDispatch0] = vsiEthernetPythonGateway.tcpConnect(bytes(PLC_LineCoordinatorIpAddress), PLC_LineCoordinatorSocketPortNumber0)

        if(self.clientPortNum[ST6_PackagingDispatch0] == 0):
            print("Error: Failed to connect to port: PLC_LineCoordinator on TCP port: ") 
            print(PLC_LineCoordinatorSocketPortNumber0)
            exit()

    def decapsulateReceivedData(self, receivedData):
        self.receivedDestPortNumber = receivedData[0]
        self.receivedSrcPortNumber = receivedData[1]
        self.receivedNumberOfBytes = receivedData[3]
        self.receivedPayload = [0] * (self.receivedNumberOfBytes)

        for i in range(self.receivedNumberOfBytes):
            self.receivedPayload[i] = receivedData[2][i]

        print(f"ST6 RX meta dest/src/len: {self.receivedDestPortNumber}, {self.receivedSrcPortNumber}, {self.receivedNumberOfBytes}")
        
        if self.receivedNumberOfBytes == 9:
            print("ST6: Received 9-byte packet from PLC -> decoding command...")
            receivedPayload = bytes(self.receivedPayload)
            self.mySignals.cmd_start, receivedPayload = self.unpackBytes('?', receivedPayload)
            self.mySignals.cmd_stop, receivedPayload = self.unpackBytes('?', receivedPayload)
            self.mySignals.cmd_reset, receivedPayload = self.unpackBytes('?', receivedPayload)
            self.mySignals.batch_id, receivedPayload = self.unpackBytes('L', receivedPayload)
            self.mySignals.recipe_id, receivedPayload = self.unpackBytes('H', receivedPayload)
            print(f"ST6: Decoded PLC cmd_start={self.mySignals.cmd_start}, cmd_stop={self.mySignals.cmd_stop}, "
                  f"cmd_reset={self.mySignals.cmd_reset}, batch={self.mySignals.batch_id}, "
                  f"recipe={self.mySignals.recipe_id}")
        else:
            print(f"ST6: Ignoring non-command packet (len={self.receivedNumberOfBytes})")

    def sendEthernetPacketToPLC_LineCoordinator(self):
        bytesToSend = bytes()

        bytesToSend += self.packBytes('?', self.mySignals.ready)
        bytesToSend += self.packBytes('?', self.mySignals.busy)
        bytesToSend += self.packBytes('?', self.mySignals.fault)
        bytesToSend += self.packBytes('?', self.mySignals.done)
        bytesToSend += self.packBytes('L', self.mySignals.cycle_time_ms)
        bytesToSend += self.packBytes('L', self.mySignals.packages_completed)
        bytesToSend += self.packBytes('L', self.mySignals.arm_cycles)
        bytesToSend += self.packBytes('L', self.mySignals.total_repairs)
        bytesToSend += self.packBytes('d', self.mySignals.operational_time_s)
        bytesToSend += self.packBytes('d', self.mySignals.downtime_s)
        bytesToSend += self.packBytes('d', self.mySignals.availability)

        vsiEthernetPythonGateway.sendEthernetPacket(PLC_LineCoordinatorSocketPortNumber0, bytes(bytesToSend))

    def packBytes(self, signalType, signal):
        if isinstance(signal, list):
            if signalType == 's':
                packedData = b''
                for str in signal:
                    str += '\0'
                    str = str.encode('utf-8')
                    packedData += struct.pack(f'={len(str)}s', str)
                return packedData
            else:
                return struct.pack(f'={len(signal)}{signalType}', *signal)
        else:
            if signalType == 's':
                signal += '\0'
                signal = signal.encode('utf-8')
                return struct.pack(f'={len(signal)}s', signal)
            else:
                return struct.pack(f'={signalType}', signal)

    def unpackBytes(self, signalType, packedBytes, signal = ""):
        if isinstance(signal, list):
            if signalType == 's':
                unpackedStrings = [''] * len(signal)
                for i in range(len(signal)):
                    nullCharacterIndex = packedBytes.find(b'\0')
                    if nullCharacterIndex == -1:
                        break
                    unpackedString = struct.unpack(f'={nullCharacterIndex}s', packedBytes[:nullCharacterIndex])[0].decode('utf-8')
                    unpackedStrings[i] = unpackedString
                    packedBytes = packedBytes[nullCharacterIndex + 1:]
                return unpackedStrings, packedBytes
            else:
                unpackedVariable = struct.unpack(f'={len(signal)}{signalType}', packedBytes[:len(signal)*struct.calcsize(f'={signalType}')])
                packedBytes = packedBytes[len(unpackedVariable)*struct.calcsize(f'={signalType}'):]
                return list(unpackedVariable), packedBytes
        elif signalType == 's':
            nullCharacterIndex = packedBytes.find(b'\0')
            unpackedVariable = struct.unpack(f'={nullCharacterIndex}s', packedBytes[:nullCharacterIndex])[0].decode('utf-8')
            packedBytes = packedBytes[nullCharacterIndex + 1:]
            return unpackedVariable, packedBytes
        else:
            numBytes = 0
            if signalType in ['?', 'b', 'B']:
                numBytes = 1
            elif signalType in ['h', 'H']:
                numBytes = 2
            elif signalType in ['f', 'i', 'I', 'L', 'l']:
                numBytes = 4
            elif signalType in ['q', 'Q', 'd']:
                numBytes = 8
            else:
                raise Exception('received an invalid signal type in unpackBytes()')
            unpackedVariable = struct.unpack(f'={signalType}', packedBytes[0:numBytes])[0]
            packedBytes = packedBytes[numBytes:]
            return unpackedVariable, packedBytes

    def updateInternalVariables(self):
        self.totalSimulationTime = vsiCommonPythonApi.getTotalSimulationTime()
        self.stopRequested = vsiCommonPythonApi.isStopRequested()
        self.simulationStep = vsiCommonPythonApi.getSimulationStep()

def main():
    inputArgs = argparse.ArgumentParser(" ")
    inputArgs.add_argument('--domain', metavar='D', default='AF_UNIX', help='Socket domain for connection with the VSI TLM fabric server')
    inputArgs.add_argument('--server-url', metavar='CO', default='localhost', help='server URL of the VSI TLM Fabric Server')

    # Start of user custom code region. Please apply edits only within these regions:  Main method
    # End of user custom code region. Please don't edit beyond this point.

    args = inputArgs.parse_args()
                      
    sT6_PackagingDispatch = ST6_PackagingDispatch(args)
    sT6_PackagingDispatch.mainThread()

if __name__ == '__main__':
    main()