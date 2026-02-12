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
        self.accept = 0
        self.reject = 0
        self.last_accept = 0


srcMacAddress = [0x02, 0x00, 0x00, 0x00, 0x00, 0x15]
PLC_LineCoordinatorMacAddress = [0x02, 0x00, 0x00, 0x00, 0x00, 0x01]
srcIpAddress = [10, 10, 0, 15]
PLC_LineCoordinatorIpAddress = [10, 10, 0, 1]

PLC_LineCoordinatorSocketPortNumber0 = 6005

ST5_QualityInspection0 = 0


# Start of user custom code region. Please apply edits only within these regions:  Global Variables & Definitions
# --- Station 5: Quality Inspection & Diverter ENHANCED PARAMETERIZED model ---
import simpy

class FixedQualityInspectionStation:
    """
    Enhanced parameterized quality inspection station with FULL integration of:
    - Vision System Parameters (resolution, lighting, algorithms)
    - Human Inspector Integration (efficiency, fatigue, breaks)
    - Diverter/Pusher Mechanism (reliability, speed, calibration)
    - Maintenance Strategies (reactive/preventive/predictive)
    - Quality Metrics (accept/reject rates, rework, false positives/negatives)
    - Energy Management (consumption, cost, CO2)
    - Shift Scheduling (availability windows)
    - Recipe-based acceptance rates
    - Rework/reinspection logic
    
    All parameters loaded ONCE at init/reset - NO runtime changes (VSI constraint).
    """
    def __init__(self, env: simpy.Environment, config: dict, human_resources: dict,
                 maintenance: dict, shift_schedule: dict, quality: dict, energy_mgmt: dict,
                 vision_system: dict, diverter_mechanism: dict):
        self.env = env
        self.config = config
        self.human_resources = human_resources
        self.maintenance = maintenance
        self.shift_schedule = shift_schedule
        self.quality = quality
        self.energy_mgmt = energy_mgmt
        self.vision_system = vision_system
        self.diverter_mechanism = diverter_mechanism
        
        # === BASE PARAMETERS (from stations.S5) ===
        self._nominal_cycle_time_s = config.get("cycle_time_s", 6.4)
        self._base_accept_rate = config.get("base_accept_rate", 0.88)
        self._base_failure_rate = config.get("failure_rate", 0.01)
        self._base_mttr_s = config.get("mttr_s", 15.0)
        self._power_rating_w = config.get("power_rating_w", 800)
        
        # === VISION SYSTEM PARAMETERS ===
        self._camera_resolution_mp = vision_system.get("camera_resolution_mp", 5)
        self._illumination_type = vision_system.get("illumination_type", "LED")
        self._algorithm_accuracy_pct = vision_system.get("algorithm_accuracy_pct", 98.5) / 100.0
        self._false_positive_rate_pct = vision_system.get("false_positive_rate_pct", 1.2) / 100.0
        self._false_negative_rate_pct = vision_system.get("false_negative_rate_pct", 0.8) / 100.0
        self._calibration_interval_h = vision_system.get("calibration_interval_h", 24)
        self._calibration_duration_min = vision_system.get("calibration_duration_min", 15)
        self._last_calibration_time = 0
        
        # === DIVERTER/PUSHER MECHANISM PARAMETERS ===
        self._diverter_type = diverter_mechanism.get("diverter_type", "pneumatic")
        self._actuation_time_ms = diverter_mechanism.get("actuation_time_ms", 150) / 1000.0  # convert to seconds
        self._positioning_accuracy_mm = diverter_mechanism.get("positioning_accuracy_mm", 0.5)
        self._diverter_reliability_pct = diverter_mechanism.get("diverter_reliability_pct", 99.5) / 100.0
        self._reject_confirmation = diverter_mechanism.get("reject_confirmation", True)
        self._sensor_failure_rate = diverter_mechanism.get("sensor_failure_rate", 0.002)
        
        # === HUMAN RESOURCES EFFECTIVE PARAMETERS ===
        self._inspector_efficiency = human_resources.get("inspector_efficiency_factor", 95) / 100.0
        self._inspector_fatigue_rate = human_resources.get("inspector_fatigue_rate", 0.02)
        self._advanced_skill_pct = human_resources.get("advanced_skill_pct", 30) / 100.0
        self._cross_training_pct = human_resources.get("cross_training_pct", 20) / 100.0
        self._break_time_min_per_hour = human_resources.get("break_time_min_per_hour", 5)
        self._shift_changeover_min = human_resources.get("shift_changeover_min", 10)
        
        # Calculate effective cycle time (operator efficiency + vision system)
        self._effective_cycle_time_s = self._nominal_cycle_time_s / self._inspector_efficiency
        
        # Calculate skill-based MTTR reduction factor
        self._skill_mttr_reduction = (self._advanced_skill_pct * 0.25) + (self._cross_training_pct * 0.15)
        self._effective_mttr_s = self._base_mttr_s * (1.0 - self._skill_mttr_reduction)
        
        # === MAINTENANCE STRATEGY EFFECTIVE PARAMETERS ===
        self._maintenance_strategy = maintenance.get("strategy", "predictive")
        self._preventive_interval_h = maintenance.get("preventive_interval_h", 160)
        self._preventive_duration_min = maintenance.get("preventive_duration_min", 30)
        self._predictive_enabled = maintenance.get("predictive_enabled", True)
        self._predictive_mttr_reduction_pct = maintenance.get("predictive_mttr_reduction_pct", 25) / 100.0
        self._predictive_failure_reduction_pct = maintenance.get("predictive_failure_reduction_pct", 30) / 100.0
        self._condition_monitoring = maintenance.get("condition_monitoring", True)
        
        # Apply predictive maintenance benefits if enabled
        if self._predictive_enabled and self._condition_monitoring:
            self._effective_failure_rate = self._base_failure_rate * (1.0 - self._predictive_failure_reduction_pct)
            self._effective_mttr_s *= (1.0 - self._predictive_mttr_reduction_pct)
            # Vision system calibration also benefits from predictive
            self._calibration_interval_h *= 1.2  # 20% longer between calibrations
        else:
            self._effective_failure_rate = self._base_failure_rate
        
        # === QUALITY PARAMETERS ===
        self._defect_rate_pct = quality.get("defect_rate_pct", 0.5) / 100.0
        self._rework_enabled = quality.get("rework_enabled", True)
        self._rework_time_s = quality.get("rework_time_s", 60)
        self._rework_success_rate_pct = quality.get("rework_success_rate_pct", 70) / 100.0
        self._inspection_enabled = quality.get("inspection_enabled", True)
        self._first_pass_yield_target = quality.get("first_pass_yield_target", 98.5) / 100.0
        self._dual_inspection = quality.get("dual_inspection", False)  # Two inspectors for critical parts
        
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
        
        # === RECIPE-BASED ACCEPTANCE RATES ===
        self._recipe_base_rates = {
            0: self._base_accept_rate,
            1: self._base_accept_rate * 0.95,  # Recipe 1: 5% lower
            2: self._base_accept_rate * 0.90,  # Recipe 2: 10% lower
            3: self._base_accept_rate * 0.85,  # Recipe 3: 15% lower
            4: self._base_accept_rate * 0.80,  # Recipe 4: 20% lower
            5: self._base_accept_rate * 0.97,  # Recipe 5: premium quality
        }
        self._rework_accept_boost = config.get("rework_accept_boost", 0.12)
        
        # === STATE VARIABLES ===
        self.state = "IDLE"
        self._cycle_proc = None
        self._busy = False
        self._fault = False
        self._done_pulse = False
        self._in_break = False
        self._in_maintenance = False
        self._in_shift_change = False
        self._in_calibration = False
        self._vision_calibration_due = False
        
        # Cycle timing
        self._cycle_start_s = 0
        self._cycle_end_s = 0
        self._actual_cycle_time_ms = 0
        self._cycle_count = 0
        self._cycle_time_sum_ms = 0
        
        # Quality tracking
        self.accept_count = 0
        self.reject_count = 0
        self.last_accept = 0
        self.rework_count = 0
        self.false_positive_count = 0
        self.false_negative_count = 0
        self.diverter_miss_count = 0
        self.calibration_count = 0
        
        # KPI tracking
        self.completed_cycles = 0
        self.total_downtime_s = 0.0
        self.total_busy_time_s = 0.0
        self.last_busy_start_s = 0.0
        self.failure_count = 0
        self.preventive_maintenance_count = 0
        self.scheduled_downtime_s = 0.0  # Breaks + PM + shift changes + calibration
        self.unscheduled_downtime_s = 0.0  # Failures
        
        # ENERGY TRACKING
        self.energy_kwh = 0.0
        self.energy_cost_usd = 0.0
        
        # Schedule recurring events
        self._schedule_breaks()
        self._schedule_preventive_maintenance()
        self._schedule_shift_boundaries()
        self._schedule_vision_calibration()
        
        print(f"  FixedQualityInspectionStation INITIALIZED with FULL CONFIGURATION:")
        print(f"    Cycle Time: {self._nominal_cycle_time_s:.3f}s (effective: {self._effective_cycle_time_s:.3f}s with {self._inspector_efficiency*100:.1f}% efficiency)")
        print(f"    Vision System: {self._camera_resolution_mp}MP, Accuracy: {self._algorithm_accuracy_pct*100:.1f}%, FP: {self._false_positive_rate_pct*100:.2f}%, FN: {self._false_negative_rate_pct*100:.2f}%")
        print(f"    Diverter: {self._diverter_type}, Actuation: {self._actuation_time_ms*1000:.0f}ms, Reliability: {self._diverter_reliability_pct*100:.1f}%")
        print(f"    Base Accept Rate: {self._base_accept_rate*100:.1f}% (recipe-dependent)")
        print(f"    Failure Rate: {self._base_failure_rate:.3f} → {self._effective_failure_rate:.3f} (predictive: {'ON' if self._predictive_enabled else 'OFF'})")
        print(f"    MTTR: {self._base_mttr_s:.1f}s → {self._effective_mttr_s:.1f}s (skills: {self._skill_mttr_reduction*100:.1f}% reduction)")
        print(f"    Power: {self._power_rating_w}W | Rework: {self._rework_time_s}s | Success: {self._rework_success_rate_pct*100:.1f}%")
        print(f"    Shifts: {self._shifts_per_day}x{self._shift_duration_h}h ({self._effective_shift_duration_s/3600:.1f}h productive)")
        print(f"    Energy: ${self._peak_tariff:.2f}/kWh peak | ${self._off_peak_tariff:.2f}/kWh off-peak | CO2: {self._co2_factor}kg/kWh")

    def _schedule_breaks(self):
        """Schedule hourly breaks and shift breaks"""
        # Hourly micro-breaks (5 min per hour)
        def hourly_break():
            while True:
                yield self.env.timeout(3600)  # Every hour
                if not self._in_break and not self._in_maintenance and not self._fault and not self._in_calibration:
                    print(f"  ☕ BREAK scheduled at {self.env.now:.1f}s (hourly {self._break_time_min_per_hour} min break)")
                    self._in_break = True
                    break_duration_s = self._break_time_min_per_hour * 60
                    self.scheduled_downtime_s += break_duration_s
                    yield self.env.timeout(break_duration_s)
                    self._in_break = False
        
        # Shift breaks (lunch + short breaks)
        def shift_breaks():
            # First break after 2 hours
            yield self.env.timeout(2 * 3600)
            if not self._in_break and not self._in_maintenance and not self._fault and not self._in_calibration:
                print(f"  🥪 LUNCH BREAK scheduled at {self.env.now:.1f}s ({self._lunch_break_min} min)")
                self._in_break = True
                self.scheduled_downtime_s += self._lunch_break_min * 60
                yield self.env.timeout(self._lunch_break_min * 60)
                self._in_break = False
            
            # Short breaks after lunch
            for i in range(self._breaks_per_shift):
                yield self.env.timeout(2 * 3600)  # Every 2 hours after lunch
                if not self._in_break and not self._in_maintenance and not self._fault and not self._in_calibration:
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
                # Convert hours to seconds
                interval_s = self._preventive_interval_h * 3600
                yield self.env.timeout(interval_s)
                
                # Only perform PM if station is idle and not in fault
                if not self._busy and not self._fault and not self._in_maintenance and not self._in_calibration:
                    print(f"  🔧 PREVENTIVE MAINTENANCE scheduled at {self.env.now:.1f}s (duration: {self._preventive_duration_min} min)")
                    self._in_maintenance = True
                    pm_duration_s = self._preventive_duration_min * 60
                    self.scheduled_downtime_s += pm_duration_s
                    self.preventive_maintenance_count += 1
                    yield self.env.timeout(pm_duration_s)
                    self._in_maintenance = False
                    print(f"  ✅ PM completed at {self.env.now:.1f}s")
        
        # Only schedule PM if strategy is preventive or predictive
        if self._maintenance_strategy in ["preventive", "predictive"]:
            self.env.process(pm_cycle())
        else:
            print("  ⚠️  Preventive maintenance DISABLED (reactive strategy)")

    def _schedule_vision_calibration(self):
        """Schedule vision system calibration at configured intervals"""
        def calibration_cycle():
            while True:
                interval_s = self._calibration_interval_h * 3600
                yield self.env.timeout(interval_s)
                
                if not self._busy and not self._fault and not self._in_maintenance and not self._in_calibration:
                    print(f"  📷 VISION SYSTEM CALIBRATION at {self.env.now:.1f}s (duration: {self._calibration_duration_min} min)")
                    self._in_calibration = True
                    self._vision_calibration_due = False
                    cal_duration_s = self._calibration_duration_min * 60
                    self.scheduled_downtime_s += cal_duration_s
                    self.calibration_count += 1
                    yield self.env.timeout(cal_duration_s)
                    self._in_calibration = False
                    self._last_calibration_time = self.env.now
                    print(f"  ✅ Calibration completed at {self.env.now:.1f}s")
        
        self.env.process(calibration_cycle())

    def _schedule_shift_boundaries(self):
        """Schedule shift start/end and changeovers"""
        def shift_cycle():
            shift_duration_s = self._shift_duration_h * 3600
            day_duration_s = 24 * 3600
            
            while True:
                # Process each shift in the day
                for shift_num in range(self._shifts_per_day):
                    # Shift start - wait until shift begins
                    time_into_day = (self.env.now % day_duration_s)
                    next_shift_start = (shift_num * shift_duration_s)
                    
                    if time_into_day > next_shift_start:
                        # Next shift is tomorrow
                        wait_time = day_duration_s - time_into_day + next_shift_start
                    else:
                        wait_time = next_shift_start - time_into_day
                    
                    if wait_time > 0:
                        yield self.env.timeout(wait_time)
                    
                    print(f"  🌅 SHIFT {shift_num+1} START at {self.env.now:.1f}s (duration: {self._shift_duration_h}h)")
                    
                    # Shift duration (excluding breaks which are scheduled separately)
                    yield self.env.timeout(shift_duration_s)
                    
                    # Shift changeover downtime
                    if shift_num < self._shifts_per_day - 1:  # Not last shift of day
                        print(f"  🔄 SHIFT CHANGEOVER at {self.env.now:.1f}s ({self._shift_changeover_min} min downtime)")
                        self._in_shift_change = True
                        self.scheduled_downtime_s += self._shift_changeover_min * 60
                        yield self.env.timeout(self._shift_changeover_min * 60)
                        self._in_shift_change = False
                
                # End of day - wait until next day
                time_into_day = self.env.now % day_duration_s
                wait_until_next_day = day_duration_s - time_into_day
                if wait_until_next_day > 0:
                    print(f"  🌙 END OF DAY at {self.env.now:.1f}s - waiting {wait_until_next_day/3600:.1f}h for next day")
                    yield self.env.timeout(wait_until_next_day)
        
        self.env.process(shift_cycle())

    def _get_accept_rate(self, recipe_id: int, is_rework: bool = False) -> float:
        """Get acceptance rate based on recipe and rework status"""
        recipe_id = int(recipe_id)
        
        # Get base rate for recipe (default to recipe 0 if not found)
        if recipe_id in self._recipe_base_rates:
            rate = self._recipe_base_rates[recipe_id]
        else:
            rate = self._base_accept_rate
        
        # Apply vision system accuracy factors
        rate *= self._algorithm_accuracy_pct
        
        # Apply inspector fatigue (declines throughout shift)
        time_into_shift = self.env.now % (self._shift_duration_h * 3600)
        if time_into_shift > 4 * 3600:  # After 4 hours
            rate *= (1.0 - self._inspector_fatigue_rate * 0.5)
        elif time_into_shift > 6 * 3600:  # After 6 hours
            rate *= (1.0 - self._inspector_fatigue_rate)
        
        # Boost for rework attempts
        if is_rework and self._rework_enabled:
            rate = min(0.95, rate + self._rework_accept_boost)
        
        # Dual inspection increases accuracy
        if self._dual_inspection:
            rate = min(0.99, rate * 1.02)
        
        return rate

    def _is_operational(self):
        """Check if station can operate (not in break/maintenance/calibration/shift change/fault)"""
        return (not self._in_break and 
                not self._in_maintenance and 
                not self._in_calibration and
                not self._in_shift_change and 
                not self._fault and
                self._is_within_shift_hours())

    def _is_within_shift_hours(self):
        """Check if current simulation time is within operational shift hours"""
        time_into_day = self.env.now % (24 * 3600)
        shift_end_s = self._shifts_per_day * self._shift_duration_h * 3600
        
        # Account for breaks within shift duration
        operational_window_s = shift_end_s - (self._shift_breaks_total_min * 60)
        
        return time_into_day < operational_window_s

    def start_cycle(self, batch_id: int, recipe_id: int, start_time_s: float):
        """Start a new inspection cycle - ONLY called on cmd_start rising edge"""
        if self._cycle_proc is not None or self._busy:
            print(f"  ⚠️  WARNING: start_cycle called but already busy (state={self.state})")
            return False
        
        if not self._is_operational():
            reason = ("BREAK" if self._in_break else 
                     "MAINTENANCE" if self._in_maintenance else 
                     "CALIBRATION" if self._in_calibration else
                     "SHIFT CHANGE" if self._in_shift_change else 
                     "FAULT" if self._fault else 
                     "OFF-SHIFT")
            print(f"  ⚠️  CANNOT START: Station not operational ({reason}) at env.now={self.env.now:.3f}s")
            return False
        
        print(f"  🚀 STARTING inspection cycle at env.now={self.env.now:.3f}s (recipe={recipe_id}, effective cycle time={self._effective_cycle_time_s:.3f}s)")
        self.state = "RUNNING"
        self._busy = True
        self._done_pulse = False
        self._cycle_start_s = start_time_s
        self._actual_cycle_time_ms = 0
        self.last_busy_start_s = self.env.now
        self._current_batch = batch_id
        self._current_recipe = recipe_id
        self._cycle_proc = self.env.process(self._inspection_cycle(batch_id, recipe_id))
        return True

    def _inspection_cycle(self, batch_id: int, recipe_id: int):
        """Run a single inspection cycle with full integration of vision, diverter, quality, and energy"""
        try:
            # STEP 1: Check for station failure (inspection cell/camera/fixture)
            if random.random() < self._effective_failure_rate:
                # Failure occurs during setup
                failure_start = self.env.now
                print(f"  ⚠️  INSPECTION CELL FAILURE at {failure_start:.3f}s")
                self.failure_count += 1
                self._fault = True
                self._busy = False
                self.total_busy_time_s += (failure_start - self.last_busy_start_s)
                self.unscheduled_downtime_s += self._effective_mttr_s
                
                # NO energy consumed during repair
                yield self.env.timeout(self._effective_mttr_s)
                
                repair_end = self.env.now
                downtime = repair_end - failure_start
                self.total_downtime_s += downtime
                print(f"  ✅ REPAIR complete at {repair_end:.3f}s (downtime={downtime:.2f}s, MTTR={self._effective_mttr_s:.1f}s)")
                self._fault = False
                self.state = "IDLE"
                return  # Part lost - next start begins new cycle

            # STEP 2: Stage 1 - Positioning + Camera Capture
            positioning_time = 0.4  # Fixed positioning time
            yield self.env.timeout(positioning_time)
            
            # Accumulate energy for positioning
            energy_ws = self._power_rating_w * positioning_time
            self.energy_kwh += energy_ws / 3.6e6
            
            # STEP 3: Stage 2 - Vision/Measurement Compute
            vision_time = 0.8  # Fixed vision processing time
            yield self.env.timeout(vision_time)
            
            # Energy for vision processing
            energy_ws = self._power_rating_w * vision_time
            self.energy_kwh += energy_ws / 3.6e6

            # STEP 4: Stage 3 - Rules/Spec Compare
            compare_time = 0.3  # Fixed comparison time
            yield self.env.timeout(compare_time)
            
            # Energy for comparison
            energy_ws = self._power_rating_w * compare_time
            self.energy_kwh += energy_ws / 3.6e6

            # Accumulate energy cost based on time-of-day tariff
            tariff = self._off_peak_tariff if self._off_peak_enabled and (self.env.now % 3600) > 2520 else self._peak_tariff
            self.energy_cost_usd += (energy_ws / 3.6e6) * tariff

            # STEP 5: Decision Making with False Positives/Negatives
            p_accept = self._get_accept_rate(recipe_id, is_rework=False)
            
            # Apply vision system errors
            if random.random() < self._false_positive_rate_pct:
                # False positive: good part incorrectly rejected
                decision_accept = False
                self.false_positive_count += 1
                print(f"  ⚠️  FALSE POSITIVE: Good part rejected (FP rate={self._false_positive_rate_pct*100:.2f}%)")
            elif random.random() < self._false_negative_rate_pct:
                # False negative: bad part incorrectly accepted
                decision_accept = True
                self.false_negative_count += 1
                print(f"  ⚠️  FALSE NEGATIVE: Defective part accepted (FN rate={self._false_negative_rate_pct*100:.2f}%)")
            else:
                # Normal decision based on accept rate
                decision_accept = (random.random() < p_accept)

            # STEP 6: Optional Re-inspection (Rework Loop)
            if not decision_accept and self._rework_enabled:
                # Quick manual wipe / reposition
                yield self.env.timeout(0.6)
                
                # Re-run compute faster
                yield self.env.timeout(0.5)
                
                # Energy for rework
                rework_energy_ws = self._power_rating_w * (0.6 + 0.5)
                self.energy_kwh += rework_energy_ws / 3.6e6
                self.energy_cost_usd += (rework_energy_ws / 3.6e6) * tariff
                
                # Check for successful rework
                if random.random() < self._rework_success_rate_pct:
                    decision_accept = (random.random() < self._get_accept_rate(recipe_id, is_rework=True))
                    if decision_accept:
                        self.rework_count += 1
                        print(f"  🔧 REWORK successful - part accepted")
                    else:
                        print(f"  ❌ REWORK failed - part rejected")
                else:
                    print(f"  ❌ REWORK attempt failed")
                    decision_accept = False

            # STEP 7: Diverter Actuation
            # Check diverter reliability
            if random.random() > self._diverter_reliability_pct:
                # Diverter missed the part
                self.diverter_miss_count += 1
                print(f"  ⚠️  DIVERTER MISS: Part not properly diverted")
                # Missed part goes to wrong bin
                if decision_accept:
                    decision_accept = False  # Good part ended up in reject bin
                else:
                    decision_accept = True   # Bad part ended up in accept bin
            
            # Actuation time
            yield self.env.timeout(self._actuation_time_ms)
            
            # Energy for diverter actuation
            diverter_energy_ws = self._power_rating_w * 0.2  # Additional power for actuation
            self.energy_kwh += diverter_energy_ws / 3.6e6
            self.energy_cost_usd += (diverter_energy_ws / 3.6e6) * tariff

            # STEP 8: Update KPIs
            self._cycle_end_s = self.env.now
            actual_time_s = self._cycle_end_s - self._cycle_start_s
            self._actual_cycle_time_ms = int(actual_time_s * 1000)
            self._cycle_count += 1
            self._cycle_time_sum_ms += self._actual_cycle_time_ms
            self.total_busy_time_s += (self.env.now - self.last_busy_start_s)
            
            # Update quality counters
            self.last_accept = 1 if decision_accept else 0
            if decision_accept:
                self.accept_count += 1
            else:
                self.reject_count += 1
            
            self.completed_cycles += 1
            
            print(f"  ✅ CYCLE COMPLETED at env.now={self.env.now:.3f}s | "
                  f"Decision: {'ACCEPT' if decision_accept else 'REJECT'} | "
                  f"Cycle time: {self._actual_cycle_time_ms}ms | "
                  f"Total accepts: {self.accept_count}, rejects: {self.reject_count}")
            
            self._busy = False
            self._done_pulse = True
            self.state = "COMPLETE"
            
        except simpy.Interrupt:
            # Cycle was stopped by PLC command
            print("  ⏹️  Cycle interrupted by STOP command")
            self._busy = False
            self._done_pulse = False
            self.state = "IDLE"
            # Accumulate partial busy time + energy
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
        self._in_calibration = False
        self._vision_calibration_due = False
        
        self._actual_cycle_time_ms = int(self._effective_cycle_time_s * 1000)
        self._cycle_count = 0
        self._cycle_time_sum_ms = 0
        
        # Quality tracking reset
        self.accept_count = 0
        self.reject_count = 0
        self.last_accept = 0
        self.rework_count = 0
        self.false_positive_count = 0
        self.false_negative_count = 0
        self.diverter_miss_count = 0
        self.calibration_count = 0
        
        # KPI tracking reset
        self.completed_cycles = 0
        self.total_downtime_s = 0.0
        self.total_busy_time_s = 0.0
        self.last_busy_start_s = 0.0
        self.failure_count = 0
        self.preventive_maintenance_count = 0
        self.scheduled_downtime_s = 0.0
        self.unscheduled_downtime_s = 0.0
        self.energy_kwh = 0.0
        self.energy_cost_usd = 0.0
        
        # Reschedule recurring events
        self._schedule_breaks()
        self._schedule_preventive_maintenance()
        self._schedule_shift_boundaries()
        self._schedule_vision_calibration()
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
        """Utilization = busy time / total time (including breaks/downtime)"""
        if total_sim_time_s <= 0:
            return 0.0
        return (self.total_busy_time_s / total_sim_time_s) * 100.0
    
    def get_availability(self, total_sim_time_s: float) -> float:
        """Availability = (total time - downtime) / total time"""
        if total_sim_time_s <= 0:
            return 0.0
        uptime = total_sim_time_s - self.total_downtime_s
        return (uptime / total_sim_time_s) * 100.0
    
    def get_oee(self, total_sim_time_s: float) -> float:
        """OEE = Availability × Performance × Quality"""
        if total_sim_time_s <= 0 or self.completed_cycles == 0:
            return 0.0
        
        # Availability: (Total Time - Downtime) / Total Time
        availability = self.get_availability(total_sim_time_s) / 100.0
        
        # Performance: (Ideal Cycle Time × Total Count) / Operating Time
        ideal_cycle_time_s = self._nominal_cycle_time_s
        operating_time_s = total_sim_time_s - self.total_downtime_s - self.scheduled_downtime_s
        if operating_time_s <= 0:
            performance = 0.0
        else:
            performance = (ideal_cycle_time_s * self.completed_cycles) / operating_time_s
        
        # Quality: Good Count / Total Count
        # For inspection station, good = accepted parts
        quality = self.accept_count / max(self.completed_cycles, 1)
        
        return availability * performance * quality * 100.0
    
    def get_accept_rate_pct(self) -> float:
        """Overall accept rate percentage"""
        if self.completed_cycles == 0:
            return 0.0
        return (self.accept_count / self.completed_cycles) * 100.0
    
    def get_reject_rate_pct(self) -> float:
        """Overall reject rate percentage"""
        if self.completed_cycles == 0:
            return 0.0
        return (self.reject_count / self.completed_cycles) * 100.0
    
    def get_false_positive_rate_pct(self) -> float:
        """False positive rate (good parts incorrectly rejected)"""
        if self.completed_cycles == 0:
            return 0.0
        return (self.false_positive_count / self.completed_cycles) * 100.0
    
    def get_false_negative_rate_pct(self) -> float:
        """False negative rate (defective parts incorrectly accepted)"""
        if self.completed_cycles == 0:
            return 0.0
        return (self.false_negative_count / self.completed_cycles) * 100.0
    
    def get_total_downtime_s(self) -> float:
        return self.total_downtime_s + self.scheduled_downtime_s
    
    def get_failure_count(self) -> int:
        return self.failure_count

    # === ENERGY GETTERS ===
    def get_energy_kwh(self) -> float:
        return self.energy_kwh
    
    def get_energy_per_unit_kwh(self) -> float:
        if self.completed_cycles > 0:
            return self.energy_kwh / self.completed_cycles
        return 0.0
    
    def get_energy_cost_usd(self) -> float:
        return self.energy_cost_usd
    
    def get_co2_emissions_kg(self) -> float:
        return self.energy_kwh * self._co2_factor

# VSI <-> SimPy Wrapper with FULL CONFIG LOADING
class ST5_SimRuntime:
    def __init__(self):
        # Load FULL configuration from line_config.json
        self.full_config = self._load_full_config()
        
        # Extract sections
        self.station_config = self.full_config.get("stations", {}).get("S5", {})
        self.human_resources = self.full_config.get("human_resources", {})
        self.maintenance = self.full_config.get("maintenance", {})
        self.shift_schedule = self.full_config.get("shift_schedule", {})
        self.quality = self.full_config.get("quality", {})
        self.energy_mgmt = self.full_config.get("energy_management", {})
        self.vision_system = self.full_config.get("vision_system", {})
        self.diverter_mechanism = self.full_config.get("diverter_mechanism", {})
        
        # Apply station-specific overrides for vision and diverter if they exist
        if "vision_system" in self.station_config:
            self.vision_system.update(self.station_config["vision_system"])
        if "diverter_mechanism" in self.station_config:
            self.diverter_mechanism.update(self.station_config["diverter_mechanism"])
        
        print(f"ST5_SimRuntime: Loaded FULL configuration from line_config.json")
        print(f"  Station S5: cycle={self.station_config.get('cycle_time_s', 6.4)}s, accept_rate={self.station_config.get('base_accept_rate', 0.88)}")
        print(f"  Vision: {self.vision_system.get('camera_resolution_mp', 5)}MP, accuracy={self.vision_system.get('algorithm_accuracy_pct', 98.5)}%")
        print(f"  Diverter: {self.diverter_mechanism.get('diverter_type', 'pneumatic')}, reliability={self.diverter_mechanism.get('diverter_reliability_pct', 99.5)}%")
        print(f"  HR: efficiency={self.human_resources.get('inspector_efficiency_factor', 95)}%, advanced={self.human_resources.get('advanced_skill_pct', 30)}%")
        print(f"  Maintenance: strategy={self.maintenance.get('strategy', 'predictive')}")
        print(f"  Shifts: {self.shift_schedule.get('shifts_per_day', 1)} shifts × {self.shift_schedule.get('shift_duration_h', 8)}h")
        
        self.env = simpy.Environment()
        self.station = FixedQualityInspectionStation(
            self.env,
            self.station_config,
            self.human_resources,
            self.maintenance,
            self.shift_schedule,
            self.quality,
            self.energy_mgmt,
            self.vision_system,
            self.diverter_mechanism
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
                "S5": {
                    "cycle_time_s": 6.4,
                    "base_accept_rate": 0.88,
                    "failure_rate": 0.01,
                    "mttr_s": 15,
                    "buffer_capacity": 2,
                    "power_rating_w": 800,
                    "rework_accept_boost": 0.12,
                    "vision_system": {
                        "camera_resolution_mp": 5,
                        "illumination_type": "LED",
                        "algorithm_accuracy_pct": 98.5,
                        "false_positive_rate_pct": 1.2,
                        "false_negative_rate_pct": 0.8,
                        "calibration_interval_h": 24,
                        "calibration_duration_min": 15
                    },
                    "diverter_mechanism": {
                        "diverter_type": "pneumatic",
                        "actuation_time_ms": 150,
                        "positioning_accuracy_mm": 0.5,
                        "diverter_reliability_pct": 99.5,
                        "reject_confirmation": True,
                        "sensor_failure_rate": 0.002
                    }
                },
                "S6": {"cycle_time_s": 10.1, "failure_rate": 0.04, "mttr_s": 35, "buffer_capacity": 2, "power_rating_w": 2000}
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
                "inspectors_per_shift": 2,
                "maintenance_technicians": 2,
                "operator_efficiency_factor": 95,
                "inspector_efficiency_factor": 95,
                "inspector_fatigue_rate": 0.02,
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
                "preventive_duration_min": 30,
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
                "iso50001_compliant": True
            },
            "quality": {
                "defect_rate_pct": 0.5,
                "rework_enabled": True,
                "rework_time_s": 60,
                "rework_success_rate_pct": 70,
                "inspection_enabled": True,
                "first_pass_yield_target": 98.5,
                "dual_inspection": False
            },
            "vision_system": {
                "camera_resolution_mp": 5,
                "illumination_type": "LED",
                "algorithm_accuracy_pct": 98.5,
                "false_positive_rate_pct": 1.2,
                "false_negative_rate_pct": 0.8,
                "calibration_interval_h": 24,
                "calibration_duration_min": 15,
                "vendor": "Cognex",
                "model": "In-Sight 7000"
            },
            "diverter_mechanism": {
                "diverter_type": "pneumatic",
                "actuation_time_ms": 150,
                "positioning_accuracy_mm": 0.5,
                "diverter_reliability_pct": 99.5,
                "reject_confirmation": True,
                "sensor_failure_rate": 0.002,
                "vendor": "SMC",
                "model": "Cylinder CDQ2B"
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
                "S1": {"cycle_time_s": 9.597, "failure_rate": 0.02, "mttr_s": 30, "mtbf_h": 50, "power_rating_w": 1500, 
                       "setup_time_s": 120, "requires_operator": True, "operators_required": 1, "criticality": "medium"},
                "S2": {"cycle_time_s": 12.3, "failure_rate": 0.05, "mttr_s": 45, "mtbf_h": 20, "power_rating_w": 2200,
                       "setup_time_s": 180, "requires_operator": True, "operators_required": 1, "criticality": "critical"},
                "S3": {"cycle_time_s": 8.7, "failure_rate": 0.03, "mttr_s": 25, "mtbf_h": 33.3, "power_rating_w": 1800,
                       "setup_time_s": 90, "requires_operator": True, "operators_required": 1, "criticality": "high"},
                "S4": {"cycle_time_s": 15.2, "failure_rate": 0.08, "mttr_s": 60, "mtbf_h": 12.5, "power_rating_w": 3500,
                       "setup_time_s": 240, "requires_operator": False, "operators_required": 0, "criticality": "bottleneck_candidate"},
                "S5": {
                    "cycle_time_s": 6.4,
                    "base_accept_rate": 0.88,
                    "failure_rate": 0.01,
                    "mttr_s": 15,
                    "mtbf_h": 100,
                    "buffer_capacity": 2,
                    "power_rating_w": 800,
                    "setup_time_s": 300,
                    "requires_operator": True,
                    "operators_required": 1,
                    "criticality": "high",
                    "equipment": "Machine Vision System (Camera + Software)",
                    "quantity": "1 unit",
                    "rework_accept_boost": 0.12,
                    "vision_system": {
                        "camera_resolution_mp": 5,
                        "illumination_type": "LED",
                        "algorithm_accuracy_pct": 98.5,
                        "false_positive_rate_pct": 1.2,
                        "false_negative_rate_pct": 0.8,
                        "calibration_interval_h": 24,
                        "calibration_duration_min": 15
                    },
                    "diverter_mechanism": {
                        "diverter_type": "pneumatic",
                        "actuation_time_ms": 150,
                        "positioning_accuracy_mm": 0.5,
                        "diverter_reliability_pct": 99.5,
                        "reject_confirmation": True,
                        "sensor_failure_rate": 0.002
                    }
                },
                "S6": {"cycle_time_s": 10.1, "failure_rate": 0.04, "mttr_s": 35, "mtbf_h": 25, "power_rating_w": 2000,
                       "setup_time_s": 150, "requires_operator": True, "operators_required": 2, "criticality": "medium"}
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
                "inspectors_per_shift": 2,
                "technicians_on_call": 2,
                "maintenance_technicians": 2,
                "skill_level_pct": {"basic": 60, "advanced": 30, "expert": 10},
                "advanced_skill_pct": 30,
                "operator_efficiency_factor": 95,
                "inspector_efficiency_factor": 95,
                "inspector_fatigue_rate": 0.02,
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
                "preventive_duration_min": 30,
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
                "defect_rate_pct": 0.5,
                "rework_enabled": True,
                "rework_time_s": 60,
                "rework_success_rate_pct": 70,
                "inspection_enabled": True,
                "first_pass_yield_target": 98.5,
                "dual_inspection": False
            },
            "vision_system": {
                "camera_resolution_mp": 5,
                "illumination_type": "LED",
                "algorithm_accuracy_pct": 98.5,
                "false_positive_rate_pct": 1.2,
                "false_negative_rate_pct": 0.8,
                "calibration_interval_h": 24,
                "calibration_duration_min": 15,
                "vendor": "Cognex",
                "model": "In-Sight 7000"
            },
            "diverter_mechanism": {
                "diverter_type": "pneumatic",
                "actuation_time_ms": 150,
                "positioning_accuracy_mm": 0.5,
                "diverter_reliability_pct": 99.5,
                "reject_confirmation": True,
                "sensor_failure_rate": 0.002,
                "vendor": "SMC",
                "model": "Cylinder CDQ2B"
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
        print("  ♻️  ST5_SimRuntime: FULL RESET (reloading ALL configuration sections)")
        self.full_config = self._load_full_config()
        self.station_config = self.full_config.get("stations", {}).get("S5", {})
        self.human_resources = self.full_config.get("human_resources", {})
        self.maintenance = self.full_config.get("maintenance", {})
        self.shift_schedule = self.full_config.get("shift_schedule", {})
        self.quality = self.full_config.get("quality", {})
        self.energy_mgmt = self.full_config.get("energy_management", {})
        self.vision_system = self.full_config.get("vision_system", {})
        self.diverter_mechanism = self.full_config.get("diverter_mechanism", {})
        
        # Apply station-specific overrides
        if "vision_system" in self.station_config:
            self.vision_system.update(self.station_config["vision_system"])
        if "diverter_mechanism" in self.station_config:
            self.diverter_mechanism.update(self.station_config["diverter_mechanism"])
        
        self.env = simpy.Environment()
        self.station = FixedQualityInspectionStation(
            self.env,
            self.station_config,
            self.human_resources,
            self.maintenance,
            self.shift_schedule,
            self.quality,
            self.energy_mgmt,
            self.vision_system,
            self.diverter_mechanism
        )
        self._start_latched = False
        self._prev_cmd_start = 0
        self._prev_cmd_stop = 0
        self._prev_cmd_reset = 0
        print("  ✅ ST5_SimRuntime: Reset complete - ready for new simulation")

    def set_context(self, batch_id: int, recipe_id: int):
        self.batch_id = int(batch_id)
        self.recipe_id = int(recipe_id)

    def update_handshake(self, cmd_start: int, cmd_stop: int, cmd_reset: int):
        """Process PLC commands with full operational state awareness"""
        # Reset has highest priority
        if cmd_reset and not self._prev_cmd_reset:
            print("  🔄 ST5_SimRuntime: RESET command (rising edge)")
            self.reset()
            self._prev_cmd_reset = 1
            return
        self._prev_cmd_reset = int(cmd_reset)

        # Rising edge detection for start
        start_edge = (cmd_start == 1 and self._prev_cmd_start == 0)
        self._last_start_edge = start_edge

        # Stop command (rising edge) - immediate stop
        if cmd_stop and not self._prev_cmd_stop:
            print("  ⏹️  ST5_SimRuntime: STOP command (rising edge)")
            self._start_latched = False
            self.station.stop_cycle()
            self._prev_cmd_stop = int(cmd_stop)

        # Start logic: ONLY on rising edge AND station operational
        if start_edge:
            if self.station._is_operational():
                print(f"  ▶️  ST5_SimRuntime: START rising edge - starting cycle")
                self._start_latched = True
                self.station.start_cycle(self.batch_id, self.recipe_id, self.env.now)
            else:
                reasons = []
                if self.station._in_break: reasons.append("BREAK")
                if self.station._in_maintenance: reasons.append("MAINTENANCE")
                if self.station._in_calibration: reasons.append("CALIBRATION")
                if self.station._in_shift_change: reasons.append("SHIFT CHANGE")
                if self.station._fault: reasons.append("FAULT")
                if not self.station._is_within_shift_hours(): reasons.append("OFF-SHIFT")
                print(f"  ⚠️  ST5_SimRuntime: START ignored - not operational ({'/'.join(reasons)})")

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
        
        # Only step if station is active (busy, in fault/repair, in maintenance, in calibration, or start latched)
        should_step = (self.station.is_busy() or 
                      self.station.is_fault() or 
                      self.station._in_maintenance or 
                      self.station._in_calibration or
                      self.station._in_break or
                      self.station._in_shift_change or
                      self._start_latched)
        
        # Skip stepping during stop command when idle
        if self._prev_cmd_stop and not (self.station.is_busy() or self.station.is_fault()):
            return
        
        if should_step and dt_s > 0:
            target_time = self.env.now + float(dt_s)
            self.env.run(until=target_time)
            
            # Check for cycle completion
            if self.station.get_done_pulse():
                self._start_latched = False

    def outputs(self, total_sim_time_s: float):
        """Get station outputs with comprehensive KPI awareness"""
        busy = 1 if self.station.is_busy() else 0
        fault = 1 if self.station.is_fault() else 0
        
        # Ready = not busy AND not fault AND operational (not in break/maintenance/calibration/shift change)
        ready = 1 if (not busy and not fault and self.station._is_operational()) else 0
        
        # Done pulse for exactly ONE iteration after completion
        done = 1 if self.station.get_done_pulse() else 0
        
        # Real cycle time
        cycle_time_ms = self.station.get_cycle_time_ms()
        
        # Accept/Reject counters
        accept = self.station.accept_count
        reject = self.station.reject_count
        last_accept = self.station.last_accept
        
        # Log comprehensive KPIs every 10 cycles
        if self.station.completed_cycles > 0 and self.station.completed_cycles % 10 == 0:
            utilization = self.station.get_utilization(total_sim_time_s)
            availability = self.station.get_availability(total_sim_time_s)
            oee = self.station.get_oee(total_sim_time_s)
            accept_rate = self.station.get_accept_rate_pct()
            fp_rate = self.station.get_false_positive_rate_pct()
            fn_rate = self.station.get_false_negative_rate_pct()
            energy_per_unit = self.station.get_energy_per_unit_kwh()
            
            print(f"  📊 ST5 COMPREHENSIVE KPIs (cycle #{self.station.completed_cycles}):")
            print(f"     Utilization: {utilization:.1f}% | Availability: {availability:.1f}% | OEE: {oee:.1f}%")
            print(f"     Accept Rate: {accept_rate:.1f}% | Reject Rate: {100-accept_rate:.1f}%")
            print(f"     False Positives: {fp_rate:.2f}% | False Negatives: {fn_rate:.2f}%")
            print(f"     Reworks: {self.station.rework_count} | Diverter Misses: {self.station.diverter_miss_count}")
            print(f"     Energy: {self.station.get_energy_kwh():.4f}kWh total | {energy_per_unit:.4f}kWh/unit | Cost: ${self.station.get_energy_cost_usd():.2f}")
            print(f"     CO2: {self.station.get_co2_emissions_kg():.2f}kg | Failures: {self.station.failure_count} | Calibrations: {self.station.calibration_count}")
        
        return ready, busy, fault, done, cycle_time_ms, accept, reject, last_accept

    def export_kpis(self, total_sim_time_s: float) -> dict:
        """Export FULL KPI set for optimizer dashboard integration"""
        utilization = self.station.get_utilization(total_sim_time_s)
        availability = self.station.get_availability(total_sim_time_s)
        oee = self.station.get_oee(total_sim_time_s)
        accept_rate = self.station.get_accept_rate_pct()
        fp_rate = self.station.get_false_positive_rate_pct()
        fn_rate = self.station.get_false_negative_rate_pct()
        energy_per_unit = self.station.get_energy_per_unit_kwh()
        
        # Calculate throughput
        throughput_units_per_hour = (self.station.completed_cycles / total_sim_time_s) * 3600 if total_sim_time_s > 0 else 0
        
        # MTBF calculation
        mtbf_h = (total_sim_time_s / 3600) / max(self.station.failure_count, 1) if self.station.failure_count > 0 else 0
        
        return {
            "station": "S5",
            "station_name": "Quality Inspection & Diverter",
            "simulation_duration_s": total_sim_time_s,
            "completed_cycles": self.station.completed_cycles,
            "throughput_units_per_hour": round(throughput_units_per_hour, 2),
            
            # Quality metrics
            "accept_count": self.station.accept_count,
            "reject_count": self.station.reject_count,
            "accept_rate_pct": round(accept_rate, 2),
            "reject_rate_pct": round(100 - accept_rate, 2),
            "rework_count": self.station.rework_count,
            "false_positive_count": self.station.false_positive_count,
            "false_negative_count": self.station.false_negative_count,
            "false_positive_rate_pct": round(fp_rate, 2),
            "false_negative_rate_pct": round(fn_rate, 2),
            "diverter_miss_count": self.station.diverter_miss_count,
            "calibration_count": self.station.calibration_count,
            
            # Downtime metrics
            "total_downtime_s": round(self.station.get_total_downtime_s(), 2),
            "scheduled_downtime_s": round(self.station.scheduled_downtime_s, 2),
            "unscheduled_downtime_s": round(self.station.unscheduled_downtime_s, 2),
            "failure_count": self.station.failure_count,
            "preventive_maintenance_count": self.station.preventive_maintenance_count,
            
            # Performance metrics
            "utilization_pct": round(utilization, 2),
            "availability_pct": round(availability, 2),
            "oee_pct": round(oee, 2),
            "mtbf_h": round(mtbf_h, 2),
            "mttr_s": round(self.station._effective_mttr_s, 2),
            "avg_cycle_time_ms": self.station.get_avg_cycle_time_ms(),
            
            # Energy metrics
            "energy_kwh": round(self.station.get_energy_kwh(), 4),
            "energy_per_unit_kwh": round(energy_per_unit, 4),
            "energy_cost_usd": round(self.station.get_energy_cost_usd(), 2),
            "co2_emissions_kg": round(self.station.get_co2_emissions_kg(), 2),
            "power_rating_w": self.station_config.get("power_rating_w", 800),
            
            # Configuration snapshot
            "config_snapshot": {
                "cycle_time_s": self.station_config.get("cycle_time_s", 6.4),
                "base_accept_rate": self.station_config.get("base_accept_rate", 0.88),
                "failure_rate": self.station_config.get("failure_rate", 0.01),
                "mttr_s": self.station_config.get("mttr_s", 15),
                "power_rating_w": self.station_config.get("power_rating_w", 800),
                "inspector_efficiency_factor": self.human_resources.get("inspector_efficiency_factor", 95),
                "algorithm_accuracy_pct": self.vision_system.get("algorithm_accuracy_pct", 98.5),
                "diverter_reliability_pct": self.diverter_mechanism.get("diverter_reliability_pct", 99.5),
                "maintenance_strategy": self.maintenance.get("strategy", "predictive"),
                "rework_enabled": self.quality.get("rework_enabled", True),
                "shifts_per_day": self.shift_schedule.get("shifts_per_day", 1)
            }
        }
# End of user custom code region. Please don't edit beyond this point.

class ST5_QualityInspection:

    def __init__(self, args):
        self.componentId = 5
        self.localHost = args.server_url
        self.domain = args.domain
        self.portNum = 50106
        
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
        # End of user custom code region. Please don't edit beyond this point.

    def mainThread(self):
        dSession = vsiCommonPythonApi.connectToServer(self.localHost, self.domain, self.portNum, self.componentId)
        vsiEthernetPythonGateway.initialize(dSession, self.componentId, bytes(srcMacAddress), bytes(srcIpAddress))
        try:
            vsiCommonPythonApi.waitForReset()

            # Start of user custom code region. Please apply edits only within these regions:  After Reset
            self._sim_start_time_ns = vsiCommonPythonApi.getSimulationTimeInNs()
            self._sim = ST5_SimRuntime()
            self._prev_done = 0
            self.total_completed = 0
            print("ST5: Parameterized SimPy runtime initialized with config from line_config.json")
            # End of user custom code region. Please don't edit beyond this point.
            self.updateInternalVariables()

            if(vsiCommonPythonApi.isStopRequested()):
                raise Exception("stopRequested")
            self.establishTcpUdpConnection()
            nextExpectedTime = vsiCommonPythonApi.getSimulationTimeInNs()
            while(vsiCommonPythonApi.getSimulationTimeInNs() < self.totalSimulationTime):

                # Start of user custom code region. Please apply edits only within these regions:  Inside the while loop
                # End of user custom code region. Please don't edit beyond this point.

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
                    ready, busy, fault, done, cycle_time_ms, accept, reject, last_accept = self._sim.outputs(total_sim_time_s)
                    
                    # Copy SimPy outputs into VSI signals
                    self.mySignals.ready = int(ready)
                    self.mySignals.busy = int(busy)
                    self.mySignals.fault = int(fault)
                    self.mySignals.done = int(done)
                    self.mySignals.cycle_time_ms = int(cycle_time_ms)
                    self.mySignals.accept = int(accept)
                    self.mySignals.reject = int(reject)
                    self.mySignals.last_accept = int(last_accept)
                    
                    # Track completions
                    if done and not self._prev_done:
                        self.total_completed += 1
                        print(f"ST5: Cycle completed! cycle_time={cycle_time_ms}ms, accept={last_accept}, total={self.total_completed}")
                    
                    # Update previous done state
                    self._prev_done = int(self.mySignals.done)
                # End of user custom code region. Please don't edit beyond this point.

                #Send ethernet packet to PLC_LineCoordinator
                self.sendEthernetPacketToPLC_LineCoordinator()

                # Start of user custom code region. Please apply edits only within these regions:  After sending the packet
                # End of user custom code region. Please don't edit beyond this point.

                print("\n+=ST5_QualityInspection+=")
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
                print("\taccept =", end = " ")
                print(self.mySignals.accept)
                print("\treject =", end = " ")
                print(self.mySignals.reject)
                print("\tlast_accept =", end = " ")
                print(self.mySignals.last_accept)
                print(f"  Internal: total_completed={self.total_completed}")
                if self._sim is not None:
                    print(f"  SimState: start_latched={self._sim._start_latched}, fault={self.mySignals.fault}")
                print("\n\n")

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
                
                kpi_file = f"ST5_kpis_{int(vsiCommonPythonApi.getSimulationTimeInNs()/1e9)}.json"
                with open(kpi_file, 'w') as f:
                    json.dump(kpis, f, indent=2)
                print(f"\n✅ ST5 KPIs exported to {kpi_file}")
                print(f"   Throughput: {kpis['completed_cycles'] / total_sim_time_s * 3600:.1f} units/hour")
                print(f"   Accept Rate: {kpis['accept_rate_pct']:.1f}% | Reject Rate: {kpis['reject_rate_pct']:.1f}%")
                print(f"   Energy: {kpis['energy_kwh']:.4f} kWh total | {kpis['energy_per_unit_kwh']:.4f} kWh/unit")
                print(f"   OEE: {kpis['oee_pct']:.1f}% | Utilization: {kpis['utilization_pct']:.1f}%")
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
        if(self.clientPortNum[ST5_QualityInspection0] == 0):
            self.clientPortNum[ST5_QualityInspection0] = vsiEthernetPythonGateway.tcpConnect(bytes(PLC_LineCoordinatorIpAddress), PLC_LineCoordinatorSocketPortNumber0)

        if(self.clientPortNum[ST5_QualityInspection0] == 0):
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

        if(self.receivedSrcPortNumber == PLC_LineCoordinatorSocketPortNumber0):
            print(f"ST5 decapsulate: destPort={self.receivedDestPortNumber}, srcPort={self.receivedSrcPortNumber}, len={self.receivedNumberOfBytes}")
            if self.receivedNumberOfBytes == 9:
                receivedPayload = bytes(self.receivedPayload)
                self.mySignals.cmd_start, receivedPayload = self.unpackBytes('?', receivedPayload)
                self.mySignals.cmd_stop, receivedPayload = self.unpackBytes('?', receivedPayload)
                self.mySignals.cmd_reset, receivedPayload = self.unpackBytes('?', receivedPayload)
                self.mySignals.batch_id, receivedPayload = self.unpackBytes('L', receivedPayload)
                self.mySignals.recipe_id, receivedPayload = self.unpackBytes('H', receivedPayload)
                print(f"ST5 decoded PLC command: cmd_start={self.mySignals.cmd_start}, cmd_stop={self.mySignals.cmd_stop}, "
                      f"cmd_reset={self.mySignals.cmd_reset}, batch_id={self.mySignals.batch_id}, "
                      f"recipe_id={self.mySignals.recipe_id}")
            elif self.receivedNumberOfBytes > 0:
                print(f"ST5 ignoring packet: wrong size ({self.receivedNumberOfBytes} bytes, expected 9)")
            else:
                print("ST5 received empty packet (len=0)")

    def sendEthernetPacketToPLC_LineCoordinator(self):
        bytesToSend = bytes()

        bytesToSend += self.packBytes('?', self.mySignals.ready)
        bytesToSend += self.packBytes('?', self.mySignals.busy)
        bytesToSend += self.packBytes('?', self.mySignals.fault)
        bytesToSend += self.packBytes('?', self.mySignals.done)
        bytesToSend += self.packBytes('L', self.mySignals.cycle_time_ms)
        bytesToSend += self.packBytes('L', self.mySignals.accept)
        bytesToSend += self.packBytes('L', self.mySignals.reject)
        bytesToSend += self.packBytes('?', self.mySignals.last_accept)

        print(f"ST5 sending to PLC on port: {PLC_LineCoordinatorSocketPortNumber0}")
        vsiEthernetPythonGateway.sendEthernetPacket(PLC_LineCoordinatorSocketPortNumber0, bytes(bytesToSend))

        # Start of user custom code region. Please apply edits only within these regions:  Protocol's callback function
        # End of user custom code region. Please don't edit beyond this point.

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
                      
    sT5_QualityInspection = ST5_QualityInspection(args)
    sT5_QualityInspection.mainThread()

if __name__ == '__main__':
    main()
