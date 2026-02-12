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
        self.completed = 0
        self.scrapped = 0
        self.reworks = 0
        self.cycle_time_avg_s = 0.0

srcMacAddress = [0x02, 0x00, 0x00, 0x00, 0x00, 0x12]
PLC_LineCoordinatorMacAddress = [0x02, 0x00, 0x00, 0x00, 0x00, 0x01]
srcIpAddress = [10, 10, 0, 12]
PLC_LineCoordinatorIpAddress = [10, 10, 0, 1]
PLC_LineCoordinatorSocketPortNumber1 = 6002
ST2_FrameCoreAssembly1 = 0

# Start of user custom code region. Please apply edits only within these regions:  Global Variables & Definitions
import simpy

class FixedFrameCoreHandler:
    """
    Enhanced parameterized station with FULL integration of:
    - Human Resources (efficiency, skills, breaks, shifts)
    - Maintenance Strategies (reactive/preventive/predictive)
    - Quality Metrics (defects, rework, yield)
    - Energy Management (consumption, cost, CO2)
    - Shift Scheduling (availability windows)
    
    All parameters loaded ONCE at init/reset - NO runtime changes (VSI constraint).
    """
    def __init__(self, env: simpy.Environment, config: dict, human_resources: dict, 
                 maintenance: dict, shift_schedule: dict, quality: dict, energy_mgmt: dict):
        self.env = env
        self.config = config
        self.human_resources = human_resources
        self.maintenance = maintenance
        self.shift_schedule = shift_schedule
        self.quality = quality
        self.energy_mgmt = energy_mgmt
        
        # === BASE PARAMETERS (from stations.S2) ===
        self._nominal_cycle_time_s = config.get("cycle_time_s", 12.3)
        self._base_failure_rate = config.get("failure_rate", 0.05)
        self._base_mttr_s = config.get("mttr_s", 45.0)
        self._power_rating_w = config.get("power_rating_w", 2200)
        self._buffer_capacity = config.get("buffer_capacity", 2)
        self._cycle_time_jitter = 0.15  # Preserve existing jitter behavior
        
        # === HUMAN RESOURCES EFFECTIVE PARAMETERS ===
        self._operator_efficiency = human_resources.get("operator_efficiency_factor", 95) / 100.0
        self._advanced_skill_pct = human_resources.get("advanced_skill_pct", 30) / 100.0
        self._cross_training_pct = human_resources.get("cross_training_pct", 20) / 100.0
        self._break_time_min_per_hour = human_resources.get("break_time_min_per_hour", 5)
        self._shift_changeover_min = human_resources.get("shift_changeover_min", 10)
        
        # Calculate effective cycle time (operator efficiency)
        self._effective_cycle_time_s = self._nominal_cycle_time_s / self._operator_efficiency
        
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
        else:
            self._effective_failure_rate = self._base_failure_rate
        
        # === QUALITY PARAMETERS ===
        self._defect_rate_pct = quality.get("defect_rate_pct", 0.5) / 100.0
        self._rework_time_s = quality.get("rework_time_s", 180)
        self._inspection_enabled = quality.get("inspection_enabled", True)
        self._first_pass_yield_target = quality.get("first_pass_yield_target", 98.5) / 100.0
        
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
        
        # === STATE VARIABLES ===
        self.state = "IDLE"
        self._cycle_proc = None
        self._busy = False
        self._fault = False
        self._done_latched = False
        self._ready = True
        
        # Cycle timing
        self._cycle_start_s = 0
        self._last_cycle_time_ms = int(self._effective_cycle_time_s * 1000)
        self._current_cycle_time_s = self._effective_cycle_time_s
        
        # Production counters
        self._completed = 0
        self._scrapped = 0
        self._reworks = 0
        self._total_cycles = 0
        self._cycle_time_sum_s = 0.0
        self._cycle_time_avg_s = 0.0
        
        # KPI tracking
        self.total_downtime_s = 0.0
        self.total_busy_time_s = 0.0
        self.last_busy_start_s = 0.0
        self.failure_count = 0
        self.preventive_maintenance_count = 0
        self.scheduled_downtime_s = 0.0  # Breaks + PM + shift changes
        self.unscheduled_downtime_s = 0.0  # Failures
        
        # ENERGY TRACKING
        self.energy_kwh = 0.0
        self.energy_cost_usd = 0.0
        
        # Operational state flags
        self._in_break = False
        self._in_maintenance = False
        self._in_shift_change = False
        
        # Schedule recurring events
        self._schedule_breaks()
        self._schedule_preventive_maintenance()
        self._schedule_shift_boundaries()
        
        print(f"  FixedFrameCoreHandler INITIALIZED with FULL CONFIGURATION:")
        print(f"    Cycle Time: {self._nominal_cycle_time_s:.3f}s (effective: {self._effective_cycle_time_s:.3f}s with {self._operator_efficiency*100:.1f}% efficiency)")
        print(f"    Failure Rate: {self._base_failure_rate:.3f} → {self._effective_failure_rate:.3f} (predictive: {'ON' if self._predictive_enabled else 'OFF'})")
        print(f"    MTTR: {self._base_mttr_s:.1f}s → {self._effective_mttr_s:.1f}s (skills: {self._skill_mttr_reduction*100:.1f}% reduction)")
        print(f"    Power: {self._power_rating_w}W | Defect Rate: {self._defect_rate_pct*100:.2f}% | Rework Time: {self._rework_time_s}s")
        print(f"    Shifts: {self._shifts_per_day}x{self._shift_duration_h}h ({self._effective_shift_duration_s/3600:.1f}h productive)")
        print(f"    Energy: ${self._peak_tariff:.2f}/kWh peak | ${self._off_peak_tariff:.2f}/kWh off-peak | CO2: {self._co2_factor}kg/kWh")

    def _schedule_breaks(self):
        """Schedule hourly breaks and shift breaks"""
        # Hourly micro-breaks (5 min per hour)
        def hourly_break():
            while True:
                yield self.env.timeout(3600)  # Every hour
                if not self._in_break and not self._in_maintenance and not self._fault:
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
            if not self._in_break and not self._in_maintenance and not self._fault:
                print(f"  🥪 LUNCH BREAK scheduled at {self.env.now:.1f}s ({self._lunch_break_min} min)")
                self._in_break = True
                self.scheduled_downtime_s += self._lunch_break_min * 60
                yield self.env.timeout(self._lunch_break_min * 60)
                self._in_break = False
            
            # Short breaks after lunch
            for i in range(self._breaks_per_shift):
                yield self.env.timeout(2 * 3600)  # Every 2 hours after lunch
                if not self._in_break and not self._in_maintenance and not self._fault:
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
                if not self._busy and not self._fault and not self._in_maintenance:
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

    def _is_operational(self):
        """Check if station can operate (not in break/maintenance/shift change/fault)"""
        return (not self._in_break and 
                not self._in_maintenance and 
                not self._in_shift_change and 
                not self._fault and
                self._is_within_shift_hours())

    def _is_within_shift_hours(self):
        """Check if current simulation time is within operational shift hours"""
        time_into_day = self.env.now % (24 * 3600)
        shift_end_s = self._shifts_per_day * self._shift_duration_h * 3600
        operational_window_s = shift_end_s - (self._shift_breaks_total_min * 60)
        return time_into_day < operational_window_s

    def reset(self):
        if self._cycle_proc is not None:
            self._cycle_proc.interrupt()
        self.state = "IDLE"
        self._busy = False
        self._done_latched = False
        self._ready = True
        self._fault = False
        self._completed = 0
        self._scrapped = 0
        self._reworks = 0
        self._total_cycles = 0
        self._cycle_time_sum_s = 0.0
        self._cycle_time_avg_s = 0.0
        self.total_downtime_s = 0.0
        self.total_busy_time_s = 0.0
        self.last_busy_start_s = 0.0
        self.failure_count = 0
        self.preventive_maintenance_count = 0
        self.scheduled_downtime_s = 0.0
        self.unscheduled_downtime_s = 0.0
        self.energy_kwh = 0.0
        self.energy_cost_usd = 0.0
        self._in_break = False
        self._in_maintenance = False
        self._in_shift_change = False
        
        # Reschedule recurring events
        self._schedule_breaks()
        self._schedule_preventive_maintenance()
        self._schedule_shift_boundaries()

    def start_cycle(self, recipe_id: int):
        """Starts a cycle; returns False if already busy or not operational."""
        if self._busy:
            return False
        
        if not self._is_operational():
            reason = ("BREAK" if self._in_break else 
                     "MAINTENANCE" if self._in_maintenance else 
                     "SHIFT CHANGE" if self._in_shift_change else 
                     "FAULT" if self._fault else 
                     "OFF-SHIFT")
            print(f"  ⚠️  CANNOT START: Station not operational ({reason}) at env.now={self.env.now:.3f}s")
            return False
        
        # Recipe-based timing (preserve existing behavior) with efficiency adjustment
        base_time = 14.0 if recipe_id == 1 else self._effective_cycle_time_s
        jitter = 1.0 + random.uniform(-self._cycle_time_jitter, self._cycle_time_jitter)
        self._current_cycle_time_s = max(2.0, base_time * jitter)
        
        self.state = "RUNNING"
        self._busy = True
        self._done_latched = False
        self._ready = False
        self._fault = False
        self._cycle_start_s = self.env.now
        self.last_busy_start_s = self.env.now
        self._cycle_proc = self.env.process(self._run_cycle())
        print(f"  FixedFrameCoreHandler: Starting cycle at env.now={self.env.now:.3f}s, expected={self._current_cycle_time_s:.3f}s")
        return True

    def _run_cycle(self):
        try:
            # STEP 1: Simulate normal processing time with energy consumption
            processing_time = self._current_cycle_time_s
            yield self.env.timeout(processing_time)
            
            # Accumulate energy for processing time (W × seconds → kWh)
            energy_ws = self._power_rating_w * processing_time
            self.energy_kwh += energy_ws / 3.6e6
            
            # Accumulate energy cost based on time-of-day tariff
            tariff = self._off_peak_tariff if self._off_peak_enabled and (self.env.now % 3600) > 2520 else self._peak_tariff
            self.energy_cost_usd += (energy_ws / 3.6e6) * tariff
            
            # STEP 2: Calculate actual cycle time & update averages
            actual_time_s = self.env.now - self._cycle_start_s
            self._last_cycle_time_ms = int(actual_time_s * 1000)
            self._total_cycles += 1
            self._cycle_time_sum_s += actual_time_s
            self._cycle_time_avg_s = self._cycle_time_sum_s / self._total_cycles
            
            # Accumulate busy time BEFORE outcome determination
            if self.last_busy_start_s > 0:
                self.total_busy_time_s += (self.env.now - self.last_busy_start_s)
                self.last_busy_start_s = 0.0
            
            # STEP 3: Check for failure AFTER processing completes
            if random.random() < self._effective_failure_rate:
                # Failure occurred
                self.failure_count += 1
                failure_start = self.env.now
                print(f"  ⚠️  FAILURE at {failure_start:.3f}s (cycle #{self._total_cycles})")
                self._fault = True
                self._busy = False
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
            
            # STEP 4: Determine outcome with quality metrics
            r = random.random()
            is_defective = False
            
            # Apply quality inspection (if enabled)
            if self._inspection_enabled and random.random() < self._defect_rate_pct:
                is_defective = True
            
            if is_defective and r < 0.4:  # 40% of defects become scrap
                self.state = "SCRAPPED"
                self._scrapped += 1
                print(f"  ❌ SCRAPPED defective part (defect rate={self._defect_rate_pct*100:.2f}%)")
            elif is_defective:  # 60% of defects go to rework
                self.state = "REWORK"
                self._reworks += 1
                print(f"  🔧 REWORKING defective part (time: {self._rework_time_s}s)")
                # Simulate rework time
                yield self.env.timeout(self._rework_time_s)
                
                # Energy for rework
                rework_energy_ws = self._power_rating_w * self._rework_time_s
                self.energy_kwh += rework_energy_ws / 3.6e6
                self.energy_cost_usd += (rework_energy_ws / 3.6e6) * tariff
                
                # After rework, part is accepted
                self.state = "COMPLETE"
                self._completed += 1
                print(f"  ✅ REWORK successful - part accepted")
            else:
                self.state = "COMPLETE"
                self._completed += 1
            
            print(f"  FixedFrameCoreHandler: Cycle completed at env.now={self.env.now:.3f}s, "
                  f"actual={self._last_cycle_time_ms}ms, state={self.state}, "
                  f"completed={self._completed}, scrapped={self._scrapped}, reworks={self._reworks}")
            self._done_latched = True
            self._busy = False
            self._cycle_proc = None
            
        except simpy.Interrupt:
            # Cycle was stopped by PLC command
            print("  FixedFrameCoreHandler: Cycle interrupted by stop command")
            self._busy = False
            self._done_latched = False
            self._fault = False
            # Accumulate partial busy time + energy on interrupt
            if self.last_busy_start_s > 0:
                partial_time = self.env.now - self.last_busy_start_s
                self.total_busy_time_s += partial_time
                energy_ws = self._power_rating_w * partial_time
                self.energy_kwh += energy_ws / 3.6e6
                tariff = self._off_peak_tariff if self._off_peak_enabled and (self.env.now % 3600) > 2520 else self._peak_tariff
                self.energy_cost_usd += (energy_ws / 3.6e6) * tariff
                self.last_busy_start_s = 0.0
        finally:
            self._cycle_proc = None

    def stop_cycle(self):
        if self._cycle_proc:
            self._cycle_proc.interrupt()
        self.state = "IDLE"
        self._busy = False
        self._done_latched = False
        self._fault = False
        # Accumulate partial busy time + energy on stop
        if self.last_busy_start_s > 0:
            partial_time = self.env.now - self.last_busy_start_s
            self.total_busy_time_s += partial_time
            energy_ws = self._power_rating_w * partial_time
            self.energy_kwh += energy_ws / 3.6e6
            tariff = self._off_peak_tariff if self._off_peak_enabled and (self.env.now % 3600) > 2520 else self._peak_tariff
            self.energy_cost_usd += (energy_ws / 3.6e6) * tariff
            self.last_busy_start_s = 0.0

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
        if total_sim_time_s <= 0 or self._completed == 0:
            return 0.0
        
        # Availability: (Total Time - Downtime) / Total Time
        availability = self.get_availability(total_sim_time_s) / 100.0
        
        # Performance: (Ideal Cycle Time × Total Count) / Operating Time
        ideal_cycle_time_s = self._nominal_cycle_time_s
        operating_time_s = total_sim_time_s - self.total_downtime_s - self.scheduled_downtime_s
        if operating_time_s <= 0:
            performance = 0.0
        else:
            performance = (ideal_cycle_time_s * self._completed) / operating_time_s
        
        # Quality: Good Count / Total Count (after rework)
        quality = 1.0  # All completed parts are good after rework
        
        return availability * performance * quality * 100.0

    def get_first_pass_yield(self) -> float:
        if self._total_cycles == 0:
            return 0.0
        good_without_rework = self._completed - self._reworks
        return (good_without_rework / self._total_cycles) * 100.0

    def get_total_downtime_s(self) -> float:
        return self.total_downtime_s + self.scheduled_downtime_s

    def get_failure_count(self) -> int:
        return self.failure_count

    # === ENERGY GETTERS ===
    def get_energy_kwh(self) -> float:
        return self.energy_kwh

    def get_energy_per_unit_kwh(self) -> float:
        if self._completed > 0:
            return self.energy_kwh / self._completed
        return 0.0

    def get_energy_cost_usd(self) -> float:
        return self.energy_cost_usd

    def get_co2_emissions_kg(self) -> float:
        return self.energy_kwh * self._co2_factor

class ST2_FixedSimRuntime:
    def __init__(self):
        # Load FULL configuration from line_config.json
        self.full_config = self._load_full_config()
        
        # Extract sections
        self.station_config = self.full_config.get("stations", {}).get("S2", {})
        self.human_resources = self.full_config.get("human_resources", {})
        self.maintenance = self.full_config.get("maintenance", {})
        self.shift_schedule = self.full_config.get("shift_schedule", {})
        self.quality = self.full_config.get("quality", {})
        self.energy_mgmt = self.full_config.get("energy_management", {})
        
        print(f"ST2_FixedSimRuntime: Loaded FULL configuration from line_config.json")
        print(f"  Station S2: cycle={self.station_config.get('cycle_time_s', 12.3)}s, failure={self.station_config.get('failure_rate', 0.05)}")
        print(f"  HR: efficiency={self.human_resources.get('operator_efficiency_factor', 95)}%, advanced={self.human_resources.get('advanced_skill_pct', 30)}%")
        print(f"  Maintenance: strategy={self.maintenance.get('strategy', 'predictive')}, predictive={'ENABLED' if self.maintenance.get('predictive_enabled', True) else 'DISABLED'}")
        print(f"  Quality: defect_rate={self.quality.get('defect_rate_pct', 0.5)}%, rework={self.quality.get('rework_time_s', 180)}s")
        print(f"  Shifts: {self.shift_schedule.get('shifts_per_day', 1)} shifts × {self.shift_schedule.get('shift_duration_h', 8)}h")
        print(f"  Energy: peak=${self.energy_mgmt.get('peak_tariff', 0.18)}/kWh, CO2={self.energy_mgmt.get('co2_factor_kg_per_kwh', 0.4)}kg/kWh")
        
        self.env = simpy.Environment()
        self.handler = FixedFrameCoreHandler(
            self.env, 
            self.station_config,
            self.human_resources,
            self.maintenance,
            self.shift_schedule,
            self.quality,
            self.energy_mgmt
        )
        
        # Handshake state
        self._run_latched = False
        self._prev_cmd_start = 0
        self._prev_cmd_stop = 0
        self._prev_cmd_reset = 0
        
        # Context
        self.batch_id = 0
        self.recipe_id = 0
        self._sim_start_time_ns = 0

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
                "S5": {"cycle_time_s": 6.4, "failure_rate": 0.01, "mttr_s": 15, "buffer_capacity": 2, "power_rating_w": 800},
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
                "energy_monitoring_enabled": True
            },
            "quality": {
                "defect_rate_pct": 0.5,
                "rework_time_s": 180,
                "inspection_enabled": True,
                "first_pass_yield_target": 98.5
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
                       "setup_time_s": 120, "requires_operator": True, "operators_required": 1, "criticality": "medium",
                       "equipment": "Collaborative Robot Arms (Cobots)", "quantity": "3-5 units"},
                "S2": {"cycle_time_s": 12.3, "failure_rate": 0.05, "mttr_s": 45, "mtbf_h": 20, "power_rating_w": 2200,
                       "setup_time_s": 180, "requires_operator": True, "operators_required": 1, "criticality": "critical",
                       "equipment": "Automated Bearing Press / Linear Rail Alignment Tool", "quantity": "1 unit"},
                "S3": {"cycle_time_s": 8.7, "failure_rate": 0.03, "mttr_s": 25, "mtbf_h": 33.3, "power_rating_w": 1800,
                       "setup_time_s": 90, "requires_operator": True, "operators_required": 1, "criticality": "high",
                       "equipment": "Smart Torque Drivers / Nutrunners", "quantity": "6-10 units"},
                "S4": {"cycle_time_s": 15.2, "failure_rate": 0.08, "mttr_s": 60, "mtbf_h": 12.5, "power_rating_w": 3500,
                       "setup_time_s": 240, "requires_operator": False, "operators_required": 0, "criticality": "bottleneck_candidate",
                       "equipment": "Gantry Run-in and Measurement Fixture", "quantity": "2 units", "energy_profile": "high"},
                "S5": {"cycle_time_s": 6.4, "failure_rate": 0.01, "mttr_s": 15, "mtbf_h": 100, "power_rating_w": 800,
                       "setup_time_s": 300, "requires_operator": True, "operators_required": 1, "criticality": "high",
                       "equipment": "Machine Vision System (Camera + Software)", "quantity": "1 unit"},
                "S6": {"cycle_time_s": 10.1, "failure_rate": 0.04, "mttr_s": 35, "mtbf_h": 25, "power_rating_w": 2000,
                       "setup_time_s": 150, "requires_operator": True, "operators_required": 2, "criticality": "medium",
                       "equipment": "Automated Box Sealer / Taping Machine", "quantity": "1 unit"}
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
                "defect_rate_pct": 0.5,
                "rework_time_s": 180,
                "inspection_enabled": True,
                "first_pass_yield_target": 98.5
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
        print("  ♻️  ST2_FixedSimRuntime: FULL RESET (reloading ALL configuration sections)")
        self.full_config = self._load_full_config()
        self.station_config = self.full_config.get("stations", {}).get("S2", {})
        self.human_resources = self.full_config.get("human_resources", {})
        self.maintenance = self.full_config.get("maintenance", {})
        self.shift_schedule = self.full_config.get("shift_schedule", {})
        self.quality = self.full_config.get("quality", {})
        self.energy_mgmt = self.full_config.get("energy_management", {})
        
        self.env = simpy.Environment()
        self.handler = FixedFrameCoreHandler(
            self.env,
            self.station_config,
            self.human_resources,
            self.maintenance,
            self.shift_schedule,
            self.quality,
            self.energy_mgmt
        )
        self._run_latched = False
        self._prev_cmd_start = 0
        self._prev_cmd_stop = 0
        self._prev_cmd_reset = 0
        print("  ✅ ST2_FixedSimRuntime: Reset complete - ready for new simulation")

    def update_handshake(self, cmd_start: int, cmd_stop: int, cmd_reset: int):
        """Process PLC commands and update internal state"""
        cmd_start_int = 1 if cmd_start else 0
        
        # Handle reset
        if cmd_reset and not self._prev_cmd_reset:
            print("  ST2_FixedSimRuntime: RESET command (rising edge)")
            self.reset()
            self._prev_cmd_reset = 1
            return
        self._prev_cmd_reset = int(cmd_reset)
        
        # Rising edge of cmd_start AND handler is idle AND no fault AND operational
        if cmd_start_int == 1 and self._prev_cmd_start == 0:
            if not self.handler._busy and not self.handler._fault and self.handler._is_operational():
                # Clear any previous done flag
                self.handler._done_latched = False
                self._run_latched = True
                print(f"  ST2_FixedSimRuntime: Rising edge cmd_start, starting cycle")
            else:
                reasons = []
                if self.handler._busy: reasons.append("BUSY")
                if self.handler._fault: reasons.append("FAULT")
                if not self.handler._is_operational():
                    if self.handler._in_break: reasons.append("BREAK")
                    if self.handler._in_maintenance: reasons.append("MAINTENANCE")
                    if self.handler._in_shift_change: reasons.append("SHIFT CHANGE")
                    if not self.handler._is_within_shift_hours(): reasons.append("OFF-SHIFT")
                print(f"  ⚠️  ST2_FixedSimRuntime: START ignored - not operational ({'/'.join(reasons)})")
        
        # Falling edge of cmd_start - clear done latch (preserve existing behavior)
        if cmd_start_int == 0 and self._prev_cmd_start == 1:
            self._run_latched = False
            self.handler._done_latched = False
            print(f"  ST2_FixedSimRuntime: cmd_start dropped, clearing done latch")
        
        # Stop command
        if cmd_stop and not self._prev_cmd_stop:
            print("  ST2_FixedSimRuntime: STOP command (rising edge)")
            self._run_latched = False
            self.handler.stop_cycle()
            self._prev_cmd_stop = int(cmd_stop)
        
        self._prev_cmd_start = cmd_start_int
        
        # Start cycle if latched and not busy and no fault and operational
        if self._run_latched and not self.handler._busy and not self.handler._fault and self.handler._is_operational():
            self.handler.start_cycle(self.recipe_id)

    def step(self, dt_s: float):
        if dt_s is None or dt_s <= 0:
            return
        
        # Step SimPy ONLY if busy OR in fault state (repair in progress) OR run_latched OR in maintenance/break
        should_step = (self.handler._busy or 
                      self.handler._fault or 
                      self._run_latched or
                      self.handler._in_maintenance or
                      self.handler._in_break)
        
        if should_step:
            old_time = self.env.now
            target_time = self.env.now + float(dt_s)
            self.env.run(until=target_time)
            if self.handler._busy or self.handler._fault:
                print(f"  ST2_FixedSimRuntime: Time advanced {old_time:.3f}s -> {self.env.now:.3f}s, "
                      f"busy={self.handler._busy}, fault={self.handler._fault}, done_latched={self.handler._done_latched}, "
                      f"operational={self.handler._is_operational()}")
            else:
                print(f"  ST2_FixedSimRuntime: Stepping during downtime (maintenance/break) to {self.env.now:.3f}s")
        else:
            print(f"  ST2_FixedSimRuntime: NOT stepping - idle state (busy={self.handler._busy}, fault={self.handler._fault}, operational={self.handler._is_operational()})")

    def get_outputs(self):
        # Ready = not busy AND not fault AND run_latched is False AND operational
        ready_signal = 1 if (not self.handler._busy and 
                            not self.handler._fault and 
                            not self._run_latched and
                            self.handler._is_operational()) else 0
        
        # Done is latched until PLC clears cmd_start
        done_signal = 1 if self.handler._done_latched else 0
        
        out = (
            ready_signal,
            1 if self.handler._busy else 0,
            1 if self.handler._fault else 0,
            done_signal,
            self.handler._last_cycle_time_ms,
            self.handler._completed,
            self.handler._scrapped,
            self.handler._reworks,
            self.handler._cycle_time_avg_s
        )
        return out

    def export_kpis(self, total_sim_time_s: float) -> dict:
        """Export structured KPIs for optimizer with FULL integration"""
        utilization = self.handler.get_utilization(total_sim_time_s)
        availability = self.handler.get_availability(total_sim_time_s)
        oee = self.handler.get_oee(total_sim_time_s)
        fpy = self.handler.get_first_pass_yield()
        energy_per_unit = self.handler.get_energy_per_unit_kwh()
        
        # Calculate throughput
        throughput_units_per_hour = (self.handler._completed / total_sim_time_s) * 3600 if total_sim_time_s > 0 else 0
        
        # MTBF calculation
        mtbf_h = (total_sim_time_s / 3600) / max(self.handler.failure_count, 1) if self.handler.failure_count > 0 else 0
        
        # First-pass yield calculation
        first_pass_yield = ((self.handler._completed - self.handler._reworks) / max(self.handler._total_cycles, 1)) * 100
        
        return {
            "station": "S2",
            "station_name": "🏗️ Frame and Core Assembly",
            "simulation_duration_s": total_sim_time_s,
            "completed": self.handler._completed,
            "scrapped": self.handler._scrapped,
            "reworks": self.handler._reworks,
            "total_cycles": self.handler._total_cycles,
            "throughput_units_per_hour": round(throughput_units_per_hour, 2),
            
            # Downtime metrics
            "total_downtime_s": round(self.handler.get_total_downtime_s(), 2),
            "scheduled_downtime_s": round(self.handler.scheduled_downtime_s, 2),
            "unscheduled_downtime_s": round(self.handler.unscheduled_downtime_s, 2),
            "failure_count": self.handler.failure_count,
            "preventive_maintenance_count": self.handler.preventive_maintenance_count,
            
            # Quality metrics
            "defect_count": self.handler._reworks + self.handler._scrapped,
            "rework_count": self.handler._reworks,
            "first_pass_yield_pct": round(first_pass_yield, 2),
            "scrap_rate_pct": round((self.handler._scrapped / max(self.handler._total_cycles, 1)) * 100, 2),
            
            # Performance metrics
            "utilization_pct": round(utilization, 2),
            "availability_pct": round(availability, 2),
            "oee_pct": round(oee, 2),
            "mtbf_h": round(mtbf_h, 2),
            "mttr_s": round(self.handler._effective_mttr_s, 2),
            "avg_cycle_time_s": round(self.handler._cycle_time_avg_s, 3),
            
            # Energy metrics (Siemens ISO 50001 compliant)
            "energy_kwh": round(self.handler.get_energy_kwh(), 4),
            "energy_per_unit_kwh": round(energy_per_unit, 4),
            "energy_cost_usd": round(self.handler.get_energy_cost_usd(), 2),
            "co2_emissions_kg": round(self.handler.get_co2_emissions_kg(), 2),
            "power_rating_w": self.station_config.get("power_rating_w", 2200),
            
            # Configuration snapshot (for traceability)
            "config_snapshot": {
                "cycle_time_s": self.station_config.get("cycle_time_s", 12.3),
                "failure_rate": self.station_config.get("failure_rate", 0.05),
                "mttr_s": self.station_config.get("mttr_s", 45),
                "power_rating_w": self.station_config.get("power_rating_w", 2200),
                "operator_efficiency_factor": self.human_resources.get("operator_efficiency_factor", 95),
                "advanced_skill_pct": self.human_resources.get("advanced_skill_pct", 30),
                "cross_training_pct": self.human_resources.get("cross_training_pct", 20),
                "maintenance_strategy": self.maintenance.get("strategy", "predictive"),
                "predictive_enabled": self.maintenance.get("predictive_enabled", True),
                "defect_rate_pct": self.quality.get("defect_rate_pct", 0.5),
                "shifts_per_day": self.shift_schedule.get("shifts_per_day", 1),
                "shift_duration_h": self.shift_schedule.get("shift_duration_h", 8)
            }
        }

# End of user custom code region. Please don't edit beyond this point.

class ST2_FrameCoreAssembly:
    def __init__(self, args):
        self.componentId = 2
        self.localHost = args.server_url
        self.domain = args.domain
        self.portNum = 50103
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
        self._prev_cmd_start = 0
        self._prev_cmd_stop = 0
        self._prev_cmd_reset = 0
        self._sim_start_time_ns = 0
        # End of user custom code region.

    def mainThread(self):
        dSession = vsiCommonPythonApi.connectToServer(self.localHost, self.domain, self.portNum, self.componentId)
        vsiEthernetPythonGateway.initialize(dSession, self.componentId, bytes(srcMacAddress), bytes(srcIpAddress))
        try:
            vsiCommonPythonApi.waitForReset()
            # Start of user custom code region. Please apply edits only within these regions:  After Reset
            self._sim_start_time_ns = vsiCommonPythonApi.getSimulationTimeInNs()
            self._sim = ST2_FixedSimRuntime()
            self._prev_cmd_start = 0
            self._prev_cmd_stop = 0
            self._prev_cmd_reset = 0
            print("ST2: Enhanced SimPy runtime initialized with FULL configuration from line_config.json")
            # End of user custom code region.
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
                # Receive on configured port (6002)
                print(f"ST2 attempting to receive on PORT: {PLC_LineCoordinatorSocketPortNumber1}")
                receivedData = vsiEthernetPythonGateway.recvEthernetPacket(PLC_LineCoordinatorSocketPortNumber1)
                if(receivedData[3] != 0):
                    self.decapsulateReceivedData(receivedData)
                # Start of user custom code region. Please apply edits only within these regions:  Before sending the packet
                if self._sim is not None:
                    # Update context
                    self._sim.batch_id = self.mySignals.batch_id
                    self._sim.recipe_id = self.mySignals.recipe_id
                    
                    # Process PLC commands and update handshake state
                    self._sim.update_handshake(
                        self.mySignals.cmd_start,
                        self.mySignals.cmd_stop,
                        self.mySignals.cmd_reset
                    )
                    
                    # Advance simulation time
                    dt_s = float(self.simulationStep) / 1e9 if self.simulationStep else 0.0
                    self._sim.step(dt_s)
                    
                    # Get outputs from SimPy
                    (ready, busy, fault, done, cycle_time_ms,
                     completed, scrapped, reworks,
                     cycle_time_avg_s) = self._sim.get_outputs()
                    
                    # Copy SimPy outputs into VSI signals
                    self.mySignals.ready = int(ready)
                    self.mySignals.busy = int(busy)
                    self.mySignals.fault = int(fault)
                    self.mySignals.done = int(done)
                    self.mySignals.cycle_time_ms = int(cycle_time_ms)
                    self.mySignals.completed = int(completed)
                    self.mySignals.scrapped = int(scrapped)
                    self.mySignals.reworks = int(reworks)
                    self.mySignals.cycle_time_avg_s = cycle_time_avg_s
                    
                    # Update previous states
                    self._prev_cmd_start = int(self.mySignals.cmd_start)
                    self._prev_cmd_stop = int(self.mySignals.cmd_stop)
                    self._prev_cmd_reset = int(self.mySignals.cmd_reset)
                    
                    # Log comprehensive KPIs every 10 cycles
                    if self._sim.handler._total_cycles > 0 and self._sim.handler._total_cycles % 10 == 0:
                        total_sim_time_s = (vsiCommonPythonApi.getSimulationTimeInNs() - self._sim_start_time_ns) / 1e9
                        utilization = self._sim.handler.get_utilization(total_sim_time_s)
                        availability = self._sim.handler.get_availability(total_sim_time_s)
                        oee = self._sim.handler.get_oee(total_sim_time_s)
                        fpy = self._sim.handler.get_first_pass_yield()
                        energy_per_unit = self._sim.handler.get_energy_per_unit_kwh()
                        
                        print(f"  📊 ST2 COMPREHENSIVE KPIs (cycle #{self._sim.handler._total_cycles}):")
                        print(f"     Utilization: {utilization:.1f}% | Availability: {availability:.1f}% | OEE: {oee:.1f}%")
                        print(f"     First-Pass Yield: {fpy:.1f}% | Scrap: {self._sim.handler._scrapped} | Reworks: {self._sim.handler._reworks}")
                        print(f"     Energy: {self._sim.handler.get_energy_kwh():.4f}kWh total | {energy_per_unit:.4f}kWh/unit | Cost: ${self._sim.handler.get_energy_cost_usd():.2f}")
                        print(f"     CO2: {self._sim.handler.get_co2_emissions_kg():.2f}kg | Failures: {self._sim.handler.failure_count} | PM Events: {self._sim.handler.preventive_maintenance_count}")
                # End of user custom code region.
                #Send ethernet packet to PLC_LineCoordinator
                self.sendEthernetPacketToPLC_LineCoordinator()
                print("\n+=ST2_FrameCoreAssembly+=")
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
                print("\tcompleted =", end = " ")
                print(self.mySignals.completed)
                print("\tscrapped =", end = " ")
                print(self.mySignals.scrapped)
                print("\treworks =", end = " ")
                print(self.mySignals.reworks)
                print("\tcycle_time_avg_s =", end = " ")
                print(self.mySignals.cycle_time_avg_s)
                if self._sim is not None:
                    print(f"  SimPy env.now = {self._sim.env.now:.3f}s")
                    print(f"  Operational: {self._sim.handler._is_operational()} (break={self._sim.handler._in_break}, maint={self._sim.handler._in_maintenance}, shift={self._sim.handler._in_shift_change})")
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
            # SIMULATION COMPLETE - Export KPIs to file
            if self._sim is not None:
                total_sim_time_s = (vsiCommonPythonApi.getSimulationTimeInNs() - self._sim_start_time_ns) / 1e9
                kpis = self._sim.export_kpis(total_sim_time_s)
                kpis["simulation_duration_s"] = total_sim_time_s
                
                # Write to station-specific KPI file
                kpi_file = f"ST2_kpis_{int(vsiCommonPythonApi.getSimulationTimeInNs()/1e9)}.json"
                with open(kpi_file, 'w') as f:
                    json.dump(kpis, f, indent=2)
                print(f"\n✅ ST2 KPIs exported to {kpi_file}")
                print(f"   Throughput: {kpis['throughput_units_per_hour']:.1f} units/hour")
                print(f"   Energy: {kpis['energy_kwh']:.4f} kWh total | {kpis['energy_per_unit_kwh']:.4f} kWh/unit")
                print(f"   Utilization: {kpis['utilization_pct']:.1f}% | Availability: {kpis['availability_pct']:.1f}% | OEE: {kpis['oee_pct']:.1f}%")
                print(f"   First-Pass Yield: {kpis['first_pass_yield_pct']:.1f}% | Scrap Rate: {kpis['scrap_rate_pct']:.1f}%")
                print(f"   CO2 Emissions: {kpis['co2_emissions_kg']:.2f} kg | Failures: {kpis['failure_count']}")
                print(f"   MTBF: {kpis['mtbf_h']:.1f}h | MTTR: {kpis['mttr_s']:.1f}s")
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
        if(self.clientPortNum[ST2_FrameCoreAssembly1] == 0):
            self.clientPortNum[ST2_FrameCoreAssembly1] = vsiEthernetPythonGateway.tcpConnect(bytes(PLC_LineCoordinatorIpAddress), PLC_LineCoordinatorSocketPortNumber1)
        if(self.clientPortNum[ST2_FrameCoreAssembly1] == 0):
            print("Error: Failed to connect to port: PLC_LineCoordinator on TCP port: ")
            print(PLC_LineCoordinatorSocketPortNumber1)
            exit()

    def decapsulateReceivedData(self, receivedData):
        self.receivedDestPortNumber = receivedData[0]
        self.receivedSrcPortNumber = receivedData[1]
        self.receivedNumberOfBytes = receivedData[3]
        self.receivedPayload = [0] * (self.receivedNumberOfBytes)
        for i in range(self.receivedNumberOfBytes):
            self.receivedPayload[i] = receivedData[2][i]
        print(f"ST2 decapsulate: destPort={self.receivedDestPortNumber}, srcPort={self.receivedSrcPortNumber}, len={self.receivedNumberOfBytes}")
        if self.receivedNumberOfBytes == 9:
            print("Received 9-byte packet from PLC (command packet)")
            receivedPayload = bytes(self.receivedPayload)
            self.mySignals.cmd_start, receivedPayload = self.unpackBytes('?', receivedPayload)
            self.mySignals.cmd_stop, receivedPayload = self.unpackBytes('?', receivedPayload)
            self.mySignals.cmd_reset, receivedPayload = self.unpackBytes('?', receivedPayload)
            self.mySignals.batch_id, receivedPayload = self.unpackBytes('L', receivedPayload)
            self.mySignals.recipe_id, receivedPayload = self.unpackBytes('H', receivedPayload)
            print(f"ST2 decoded PLC command: cmd_start={self.mySignals.cmd_start}, cmd_stop={self.mySignals.cmd_stop}, "
                  f"cmd_reset={self.mySignals.cmd_reset}, batch_id={self.mySignals.batch_id}, "
                  f"recipe_id={self.mySignals.recipe_id}")
        elif self.receivedNumberOfBytes > 0:
            print(f"ST2 ignoring packet: wrong size ({self.receivedNumberOfBytes} bytes, expected 9)")
        else:
            print("ST2 received empty packet (len=0)")

    def sendEthernetPacketToPLC_LineCoordinator(self):
        bytesToSend = bytes()
        bytesToSend += self.packBytes('?', self.mySignals.ready)
        bytesToSend += self.packBytes('?', self.mySignals.busy)
        bytesToSend += self.packBytes('?', self.mySignals.fault)
        bytesToSend += self.packBytes('?', self.mySignals.done)
        bytesToSend += self.packBytes('L', self.mySignals.cycle_time_ms)
        bytesToSend += self.packBytes('L', self.mySignals.completed)
        bytesToSend += self.packBytes('L', self.mySignals.scrapped)
        bytesToSend += self.packBytes('L', self.mySignals.reworks)
        bytesToSend += self.packBytes('d', self.mySignals.cycle_time_avg_s)
        print(f"ST2 sending to PLC on port: {PLC_LineCoordinatorSocketPortNumber1}")
        vsiEthernetPythonGateway.sendEthernetPacket(PLC_LineCoordinatorSocketPortNumber1, bytes(bytesToSend))

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
    args = inputArgs.parse_args()
    sT2_FrameCoreAssembly = ST2_FrameCoreAssembly(args)
    sT2_FrameCoreAssembly.mainThread()

if __name__ == '__main__':
    main()
