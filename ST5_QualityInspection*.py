#!/usr/bin/env python3
from __future__ import print_function
import struct
import sys
import argparse
import math
import json
import os
import random

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

# Part A.1: Port assignment
ST5_PORT = 6005
PLC_LineCoordinatorSocketPortNumber0 = ST5_PORT
ST5_QualityInspection0 = 0


# Start of user custom code region. Please apply edits only within these regions:  Global Variables & Definitions
import simpy

# -----------------------------
# Station 5: Quality Inspection + Diverter (PARAMETERIZED SimPy core)
# -----------------------------
# Real-world idea:
# - A unit arrives from Station 4.
# - Camera/measurement + checks (visual + basic functional).
# - Optional re-inspection (one rework loop).
# - Diverter sends to PASS lane or REJECT/REWORK lane.
#
# VSI integration:
# - PLC controls via cmd_start/cmd_stop/cmd_reset.
# - We step SimPy in the mainThread loop and copy results to mySignals.
# - CONFIGURATION: Loaded from line_config.json (stations.S5)

class FixedQualityInspectionStation:
    """
    Parameterized station for quality inspection with config-driven cycle time,
    pass rates, rework, and KPI tracking. Includes energy consumption tracking.
    """
    def __init__(self, env: simpy.Environment, config: dict):
        self.env = env
        self.config = config  # Store config for entire simulation run
        
        # Load parameters ONCE at init (VSI constraint: no runtime changes)
        self._nominal_cycle_time_s = config.get("cycle_time_s", 2.5)
        self._base_pass_rate = config.get("base_pass_rate", 0.88)
        self._rework_pass_rate_boost = config.get("rework_pass_rate_boost", 0.12)
        self._rework_time_factor = config.get("rework_time_factor", 0.6)  # Rework takes 60% of main cycle
        self._fault_rate = config.get("fault_rate", 0.005)
        self._buffer_capacity = config.get("buffer_capacity", 2)
        self._power_rating_w = config.get("power_rating_w", 1500)  # Moderate power for inspection equipment
        
        # State variables
        self.state = "IDLE"
        self._cycle_proc = None
        self._busy = False
        self._fault = False
        self._done_pulse = False
        
        # Cycle timing
        self._cycle_start_s = 0
        self._cycle_end_s = 0
        self._actual_cycle_time_ms = 0
        self._cycle_count = 0
        self._cycle_time_sum_ms = 0
        
        # Counters
        self.accept = 0
        self.reject = 0
        self.last_accept = 0
        
        # KPI tracking
        self.completed_cycles = 0
        self.total_downtime_s = 0.0
        self.total_busy_time_s = 0.0
        self.last_busy_start_s = 0.0
        self.failure_count = 0
        
        # Pass/fail statistics
        self.passed_first_try = 0
        self.passed_after_rework = 0
        self.failed_final = 0
        self.rework_attempts = 0
        
        # ENERGY TRACKING (Siemens requirement)
        self.energy_kwh = 0.0
        
        print(f"  FixedQualityInspectionStation INITIALIZED with config:")
        print(f"    cycle_time_s={self._nominal_cycle_time_s}, base_pass_rate={self._base_pass_rate}, "
              f"fault_rate={self._fault_rate}, power_rating_w={self._power_rating_w}W")

    def _get_pass_rate(self, recipe_id: int, is_rework: bool = False) -> float:
        """Calculate pass rate based on recipe and whether it's a rework attempt"""
        # Tune per recipe: different printer variants have different pass rates
        base = self._base_pass_rate
        if int(recipe_id) == 0:
            rate = base
        else:
            # small variation but clamped
            rate = max(0.70, min(0.97, base - (int(recipe_id) % 5) * 0.02))
        
        # Boost for rework attempts
        if is_rework:
            rate = min(0.95, rate + self._rework_pass_rate_boost)
        
        return rate

    def start_cycle(self, batch_id: int, recipe_id: int, start_time_s: float):
        """Start a new inspection cycle - ONLY called on cmd_start rising edge"""
        if self._cycle_proc is not None or self._busy:
            print(f"  WARNING: FixedQualityInspectionStation.start_cycle called but already busy!")
            return False
        
        if self._fault:
            print(f"  ERROR: FixedQualityInspectionStation.start_cycle called but in fault state!")
            return False
        
        print(f"  FixedQualityInspectionStation: Starting job at env.now={self.env.now:.3f}s, "
              f"batch_id={batch_id}, recipe_id={recipe_id}")
        self.state = "RUNNING"
        self._busy = True
        self._done_pulse = False
        self._cycle_start_s = start_time_s
        self._actual_cycle_time_ms = 0
        self._current_batch = int(batch_id)
        self._current_recipe = int(recipe_id)
        
        # Track busy time start for utilization + energy KPIs
        self.last_busy_start_s = self.env.now
        self._cycle_proc = self.env.process(self._inspection_cycle())
        return True

    def _inspection_cycle(self):
        """Run a single inspection cycle with probabilistic outcomes + ENERGY TRACKING"""
        try:
            # Small chance of inspection cell fault (camera/fixture/jig)
            if random.random() < self._fault_rate:
                # fault happens during setup
                fault_time = 0.2
                yield self.env.timeout(fault_time)
                
                # Energy consumption during fault setup
                energy_ws = self._power_rating_w * fault_time
                self.energy_kwh += energy_ws / 3.6e6
                
                self.failure_count += 1
                failure_start = self.env.now
                print(f"  ⚠️  INSPECTION FAULT at {failure_start:.3f}s")
                
                # Enter fault state
                self._fault = True
                self._busy = False
                
                # Accumulate busy time BEFORE failure
                self.total_busy_time_s += (failure_start - self.last_busy_start_s)
                
                # Simulate repair time (MTTR) - NO ENERGY CONSUMED during repair
                mttr_s = self.config.get("mttr_s", 30.0)
                yield self.env.timeout(mttr_s)
                
                # Repair complete
                repair_end = self.env.now
                downtime = repair_end - failure_start
                self.total_downtime_s += downtime
                print(f"  ✅ INSPECTION REPAIR complete at {repair_end:.3f}s (downtime={downtime:.2f}s)")
                
                # Exit fault state
                self._fault = False
                self.state = "IDLE"
                return

            # Stage 1: positioning + camera capture (scaled time)
            stage1_s = self._nominal_cycle_time_s * (0.4 / 2.5)
            yield self.env.timeout(stage1_s)
            
            # Stage 2: vision/measurement compute (scaled time)
            stage2_s = self._nominal_cycle_time_s * (0.8 / 2.5)
            yield self.env.timeout(stage2_s)
            
            # Stage 3: rules/spec compare (scaled time)
            stage3_s = self._nominal_cycle_time_s * (0.3 / 2.5)
            yield self.env.timeout(stage3_s)
            
            # Accumulate energy for processing time
            processing_time = stage1_s + stage2_s + stage3_s
            energy_ws = self._power_rating_w * processing_time
            self.energy_kwh += energy_ws / 3.6e6

            # Decision
            p_accept = self._get_pass_rate(self._current_recipe, is_rework=False)
            decision_accept = (random.random() < p_accept)
            print(f"  FixedQualityInspectionStation: First test {'PASSED' if decision_accept else 'FAILED'} "
                  f"(p_accept={p_accept:.3f})")

            # Optional re-inspection once (rework loop)
            if not decision_accept:
                self.rework_attempts += 1
                
                # quick manual wipe / reposition (scaled time)
                rework_s = self._nominal_cycle_time_s * (0.6 / 2.5)
                yield self.env.timeout(rework_s)
                
                # re-run compute faster (scaled time)
                rework_compute_s = self._nominal_cycle_time_s * (0.5 / 2.5)
                yield self.env.timeout(rework_compute_s)
                
                # Additional energy for rework
                rework_energy_ws = self._power_rating_w * (rework_s + rework_compute_s)
                self.energy_kwh += rework_energy_ws / 3.6e6
                
                # partial recovery chance
                p_rework_accept = self._get_pass_rate(self._current_recipe, is_rework=True)
                decision_accept = (random.random() < p_rework_accept)
                print(f"  FixedQualityInspectionStation: Rework test {'PASSED' if decision_accept else 'FAILED'} "
                      f"(p_accept={p_rework_accept:.3f})")

            # Diverter actuation (scaled time)
            diverter_s = self._nominal_cycle_time_s * (0.2 / 2.5)
            yield self.env.timeout(diverter_s)
            
            # Energy for diverter
            energy_ws = self._power_rating_w * diverter_s
            self.energy_kwh += energy_ws / 3.6e6

            # Complete cycle
            self._cycle_end_s = self.env.now
            actual_time_s = self._cycle_end_s - self._cycle_start_s
            self._actual_cycle_time_ms = int(actual_time_s * 1000)
            
            # Update counters
            self._cycle_count += 1
            self._cycle_time_sum_ms += self._actual_cycle_time_ms
            self.completed_cycles += 1
            
            # Update KPIs
            self.last_accept = 1 if decision_accept else 0
            if decision_accept:
                self.accept += 1
                if self._actual_cycle_time_ms < self._nominal_cycle_time_s * 1000 * 0.9:  # Rough heuristic
                    self.passed_first_try += 1
                else:
                    self.passed_after_rework += 1
                self.state = "COMPLETE"
                self._done_pulse = True
                print(f"  FixedQualityInspectionStation: Cycle PASSED after {self._actual_cycle_time_ms}ms")
            else:
                self.reject += 1
                self.failed_final += 1
                self._fault = True  # Signal failure to PLC
                print(f"  FixedQualityInspectionStation: Cycle FAILED after {self._actual_cycle_time_ms}ms")
            
            # Accumulate busy time
            self.total_busy_time_s += (self.env.now - self.last_busy_start_s)
            
            print(f"  FixedQualityInspectionStation: Job completed at env.now={self.env.now:.3f}s, "
                  f"actual_time={self._actual_cycle_time_ms}ms, accept={self.accept}, reject={self.reject}")
            
            self._busy = False
            
        except simpy.Interrupt:
            print("  FixedQualityInspectionStation: Cycle interrupted by stop command")
            self._busy = False
            self._done_pulse = False
            self.state = "IDLE"
            # Accumulate partial busy time + energy
            if self.last_busy_start_s > 0:
                partial_time = self.env.now - self.last_busy_start_s
                self.total_busy_time_s += partial_time
                # Partial energy consumption
                energy_ws = self._power_rating_w * partial_time
                self.energy_kwh += energy_ws / 3.6e6
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
        # Accumulate partial busy time + energy on stop
        if self.last_busy_start_s > 0:
            partial_time = self.env.now - self.last_busy_start_s
            self.total_busy_time_s += partial_time
            # Partial energy consumption
            energy_ws = self._power_rating_w * partial_time
            self.energy_kwh += energy_ws / 3.6e6
            self.last_busy_start_s = 0.0

    def reset(self):
        """Full reset - reloads config parameters (for new simulation run)"""
        print("  FixedQualityInspectionStation: FULL RESET")
        self.stop_cycle()
        self.state = "IDLE"
        self._busy = False
        self._fault = False
        self._done_pulse = False
        self._cycle_proc = None
        self._actual_cycle_time_ms = int(self._nominal_cycle_time_s * 1000)
        self._cycle_count = 0
        self._cycle_time_sum_ms = 0
        self.accept = 0
        self.reject = 0
        self.last_accept = 0
        self.completed_cycles = 0
        self.total_downtime_s = 0.0
        self.total_busy_time_s = 0.0
        self.last_busy_start_s = 0.0
        self.failure_count = 0
        self.passed_first_try = 0
        self.passed_after_rework = 0
        self.failed_final = 0
        self.rework_attempts = 0
        self.energy_kwh = 0.0

    def is_busy(self):
        return self._busy

    def is_fault(self):
        return self._fault

    def get_done_pulse(self):
        return self._done_pulse

    def clear_done_pulse(self):
        """Clear done pulse after it's been read"""
        was_set = self._done_pulse
        self._done_pulse = False
        return was_set

    def get_cycle_time_ms(self):
        return self._actual_cycle_time_ms if self._actual_cycle_time_ms > 0 else int(self._nominal_cycle_time_s * 1000)

    def get_avg_cycle_time_ms(self):
        if self._cycle_count > 0:
            return int(self._cycle_time_sum_ms / self._cycle_count)
        return int(self._nominal_cycle_time_s * 1000)

    def has_active_proc(self):
        return self._cycle_proc is not None

    def get_accept(self):
        return self.accept

    def get_reject(self):
        return self.reject

    # KPI getters
    def get_utilization(self, total_sim_time_s: float) -> float:
        if total_sim_time_s <= 0:
            return 0.0
        return (self.total_busy_time_s / total_sim_time_s) * 100.0

    def get_availability(self, total_sim_time_s: float) -> float:
        if total_sim_time_s <= 0:
            return 0.0
        uptime = total_sim_time_s - self.total_downtime_s
        return (uptime / total_sim_time_s) * 100.0

    def get_total_downtime_s(self) -> float:
        return self.total_downtime_s

    def get_failure_count(self) -> int:
        return self.failure_count

    def get_rework_rate(self) -> float:
        if self.completed_cycles > 0:
            return (self.rework_attempts / self.completed_cycles) * 100.0
        return 0.0

    def get_acceptance_rate(self) -> float:
        total = self.accept + self.reject
        if total > 0:
            return (self.accept / total) * 100.0
        return 0.0

    def get_first_pass_yield(self) -> float:
        if self.completed_cycles > 0:
            return (self.passed_first_try / self.completed_cycles) * 100.0
        return 0.0

    # ENERGY getters (Siemens requirement)
    def get_energy_kwh(self) -> float:
        """Get total energy consumed in kWh"""
        return self.energy_kwh

    def get_energy_per_unit_kwh(self) -> float:
        """Get energy consumed per completed unit (kWh/unit)"""
        if self.accept > 0:
            return self.energy_kwh / self.accept
        return 0.0

# VSI <-> SimPy Wrapper with CONFIG LOADING
class ST5_SimRuntime:
    def __init__(self):
        # Load config ONCE at simulation start
        self.config = self._load_config()
        print(f"ST5_SimRuntime: Loaded config from line_config.json -> cycle_time={self.config['cycle_time_s']}s, "
              f"base_pass_rate={self.config['base_pass_rate']}, fault_rate={self.config['fault_rate']}, "
              f"power={self.config['power_rating_w']}W")
        
        self.env = simpy.Environment()
        self.station = FixedQualityInspectionStation(self.env, self.config)
        
        # Handshake state
        self._start_latched = False
        self._prev_cmd_start = 0
        self._prev_cmd_stop = 0
        self._prev_cmd_reset = 0
        
        # Context from PLC
        self.batch_id = 0
        self.recipe_id = 0
        
        # Debug tracking
        self._last_start_edge = False
        self._last_step_dt = 0.0

    def _load_config(self) -> dict:
        """Load station parameters from external JSON config"""
        config_path = "line_config.json"
        default_config = {
            "cycle_time_s": 2.5,
            "base_pass_rate": 0.88,
            "rework_pass_rate_boost": 0.12,
            "rework_time_factor": 0.6,
            "fault_rate": 0.005,
            "mttr_s": 30.0,
            "buffer_capacity": 2,
            "power_rating_w": 1500
        }
        
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    full_config = json.load(f)
                    # Extract S5-specific config
                    if "stations" in full_config and "S5" in full_config["stations"]:
                        cfg = full_config["stations"]["S5"]
                        # Ensure power_rating_w exists (backwards compatibility)
                        if "power_rating_w" not in cfg:
                            cfg["power_rating_w"] = default_config["power_rating_w"]
                            print(f"  ⚠️  ST5: power_rating_w not in config - using default {default_config['power_rating_w']}W")
                        return cfg
                    else:
                        print(f"  ⚠️  WARNING: line_config.json missing 'stations.S5' section - using defaults")
                        return default_config
            except Exception as e:
                print(f"  ⚠️  WARNING: Error loading {config_path}: {e} - using defaults")
                return default_config
        else:
            print(f"  ⚠️  WARNING: {config_path} not found - using default parameters")
            return default_config

    def reset(self):
        """Full reset - reloads config for new simulation run"""
        print("  ST5_SimRuntime: FULL RESET (reloading config)")
        self.config = self._load_config()  # Reload config for new run
        self.env = simpy.Environment()
        self.station = FixedQualityInspectionStation(self.env, self.config)
        self._start_latched = False
        self._prev_cmd_start = 0
        self._prev_cmd_stop = 0
        self._prev_cmd_reset = 0
        print("  ST5_SimRuntime: Reset complete - ready for new simulation")

    def set_context(self, batch_id: int, recipe_id: int):
        self.batch_id = int(batch_id)
        self.recipe_id = int(recipe_id)

    def update_handshake(self, cmd_start: int, cmd_stop: int, cmd_reset: int):
        """Process PLC commands and update internal state"""
        # Reset has highest priority
        if cmd_reset and not self._prev_cmd_reset:
            print("ST5: RESET command received (rising edge)")
            self.reset()
            self._prev_cmd_reset = 1
            return
        self._prev_cmd_reset = int(cmd_reset)
        
        # Rising edge detection for start
        start_edge = (cmd_start == 1 and self._prev_cmd_start == 0)
        self._last_start_edge = start_edge
        
        # Stop command (rising edge) - immediate stop
        if cmd_stop and not self._prev_cmd_stop:
            print("ST5: STOP command received (rising edge)")
            self._start_latched = False
            self.station.stop_cycle()
            self._prev_cmd_stop = int(cmd_stop)
        
        # Start logic: ONLY on rising edge AND station idle AND no fault
        if start_edge:
            if not self.station.is_busy() and not self.station.is_fault():
                print("ST5: START rising edge detected -> starting cycle")
                self._start_latched = True
                self.station.start_cycle(self.batch_id, self.recipe_id, self.env.now)
            else:
                fault_status = "FAULT" if self.station.is_fault() else "BUSY"
                print(f"ST5: START rising edge but station {fault_status} - ignoring")
        
        # Keep start_latched during entire cycle
        self._prev_cmd_start = int(cmd_start)
        
        # Safety check: if station is busy but start_latched is False, fix it
        if self.station.is_busy() and not self._start_latched:
            print("  ⚠️  ERROR: ST5_SimRuntime: station busy but start_latched=False! Fixing...")
            self._start_latched = True
        
        # Safety check: if station has active process but busy flag is False, fix it
        if self.station.has_active_proc() and not self.station.is_busy():
            print("  ⚠️  ERROR: ST5_SimRuntime: active process but busy=False! Fixing...")
            self.station._busy = True

    def step(self, dt_s: float):
        """Advance simulation ONLY when necessary"""
        self._last_step_dt = dt_s
        
        # Step SimPy ONLY if busy OR in fault state OR start_latched
        should_step = self.station.is_busy() or self.station.is_fault() or self._start_latched
        print(f"  ST5_SimRuntime step: env.now={self.env.now:.3f}s, dt_s={dt_s:.6f}s, "
              f"start_latched={self._start_latched}, busy={self.station.is_busy()}, "
              f"fault={self.station.is_fault()}, should_step={should_step}")
        
        # DO NOT step if stop command active and not in cycle/repair
        if self._prev_cmd_stop and not (self.station.is_busy() or self.station.is_fault()):
            print(f"  ST5_SimRuntime: NOT stepping - stop command active and idle")
            return
        
        # Only step if we should step
        if should_step and dt_s > 0:
            target_time = self.env.now + float(dt_s)
            self.env.run(until=target_time)
            print(f"  ST5_SimRuntime: Stepped to env.now={self.env.now:.3f}s")
        
        # Check for cycle completion and clear start_latched
        if self.station.get_done_pulse():
            print("  ST5_SimRuntime: Cycle completed, clearing start_latched")
            self._start_latched = False

    def outputs(self, total_sim_time_s: float):
        """Get station outputs INCLUDING KPIs"""
        busy = 1 if self.station.is_busy() else 0
        fault = 1 if self.station.is_fault() else 0
        accept = self.station.get_accept()
        reject = self.station.get_reject()
        
        # Ready = not busy AND not fault (independent of start latch)
        ready = 1 if (not busy and not fault) else 0
        
        # Done pulse for exactly ONE iteration after completion
        done = 1 if self.station.get_done_pulse() else 0
        
        # Real cycle time
        cycle_time_ms = self.station.get_cycle_time_ms()
        last_accept = self.station.last_accept
        
        # Calculate utilization/availability for logging
        utilization = self.station.get_utilization(total_sim_time_s)
        availability = self.station.get_availability(total_sim_time_s)
        acceptance_rate = self.station.get_acceptance_rate()
        
        # Log KPIs every 5 cycles for visibility
        if self.station.completed_cycles > 0 and self.station.completed_cycles % 5 == 0:
            energy_per_unit = self.station.get_energy_per_unit_kwh()
            print(f"  📊 ST5 KPIs (cycle #{self.station.completed_cycles}): "
                  f"utilization={utilization:.1f}%, availability={availability:.1f}%, "
                  f"energy={self.station.get_energy_kwh():.4f}kWh, energy/unit={energy_per_unit:.4f}kWh/unit, "
                  f"acceptance_rate={acceptance_rate:.1f}%, first_pass_yield={self.station.get_first_pass_yield():.1f}%, "
                  f"rework_rate={self.station.get_rework_rate():.1f}%, failures={self.station.get_failure_count()}, "
                  f"downtime={self.station.get_total_downtime_s():.1f}s")
        
        return ready, busy, fault, done, cycle_time_ms, accept, reject, last_accept

    def export_kpis(self, total_sim_time_s: float) -> dict:
        """Export structured KPIs for optimizer (with ENERGY metrics)"""
        energy_per_unit = self.station.get_energy_per_unit_kwh()
        
        return {
            "station": "S5",
            "accept": self.station.get_accept(),
            "reject": self.station.get_reject(),
            "completed_cycles": self.station.completed_cycles,
            "passed_first_try": self.station.passed_first_try,
            "passed_after_rework": self.station.passed_after_rework,
            "failed_final": self.station.failed_final,
            "rework_attempts": self.station.rework_attempts,
            "total_downtime_s": self.station.get_total_downtime_s(),
            "failure_count": self.station.get_failure_count(),
            "utilization_pct": self.station.get_utilization(total_sim_time_s),
            "availability_pct": self.station.get_availability(total_sim_time_s),
            "avg_cycle_time_ms": self.station.get_avg_cycle_time_ms(),
            "acceptance_rate_pct": self.station.get_acceptance_rate(),
            "first_pass_yield_pct": self.station.get_first_pass_yield(),
            "rework_rate_pct": self.station.get_rework_rate(),
            # ENERGY METRICS (Siemens requirement)
            "energy_kwh": self.station.get_energy_kwh(),
            "energy_per_unit_kwh": energy_per_unit,
            "power_rating_w": self.config["power_rating_w"],
            "config": {
                "cycle_time_s": self.config["cycle_time_s"],
                "base_pass_rate": self.config["base_pass_rate"],
                "rework_pass_rate_boost": self.config.get("rework_pass_rate_boost", 0.12),
                "fault_rate": self.config["fault_rate"],
                "mttr_s": self.config.get("mttr_s", 30.0),
                "power_rating_w": self.config["power_rating_w"]
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
        print("ST5: Initializing...")
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

                receivedData = vsiEthernetPythonGateway.recvEthernetPacket(ST5_PORT)
                if(receivedData[3] != 0):
                    self.decapsulateReceivedData(receivedData)

                # Start of user custom code region. Please apply edits only within these regions:  Before sending the packet
                # Process handshake and simulation stepping
                if self._sim is not None:
                    # Update context
                    self._sim.set_context(self.mySignals.batch_id, self.mySignals.recipe_id)
                    
                    # Process PLC commands and update handshake state
                    self._sim.update_handshake(
                        self.mySignals.cmd_start,
                        self.mySignals.cmd_stop,
                        self.mySignals.cmd_reset
                    )
                    
                    # Advance simulation time
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
                        print(f"ST5: Cycle completed! cycle_time={cycle_time_ms}ms, accept={accept}, reject={reject}, total_completed={self.total_completed}")
                    
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

            # SIMULATION COMPLETE - Export KPIs to file
            if self._sim is not None:
                total_sim_time_s = (vsiCommonPythonApi.getSimulationTimeInNs() - self._sim_start_time_ns) / 1e9
                kpis = self._sim.export_kpis(total_sim_time_s)
                kpis["simulation_duration_s"] = total_sim_time_s
                
                # Write to station-specific KPI file
                kpi_file = f"ST5_kpis_{int(vsiCommonPythonApi.getSimulationTimeInNs()/1e9)}.json"
                with open(kpi_file, 'w') as f:
                    json.dump(kpis, f, indent=2)
                print(f"\n✅ ST5 KPIs exported to {kpi_file}")
                print(f"   Throughput: {kpis['accept'] / total_sim_time_s * 3600:.1f} units/hour")
                print(f"   Energy: {kpis['energy_kwh']:.4f} kWh total")
                print(f"   Energy per unit: {kpis['energy_per_unit_kwh']:.4f} kWh/unit")
                print(f"   Utilization: {kpis['utilization_pct']:.1f}%")
                print(f"   Availability: {kpis['availability_pct']:.1f}%")
                print(f"   Acceptance rate: {kpis['acceptance_rate_pct']:.1f}%")
                print(f"   First-pass yield: {kpis['first_pass_yield_pct']:.1f}%")
                print(f"   Rework rate: {kpis['rework_rate_pct']:.1f}%")

            if(vsiCommonPythonApi.getSimulationTimeInNs() < self.totalSimulationTime):
                vsiEthernetPythonGateway.terminate()
        except Exception as e:
            if str(e) == "stopRequested":
                print("Terminate signal has been received from one of the VSI clients")
                # Advance time with a step that is equal to "simulationStep + 1" so that all other clients
                # receive the terminate packet before terminating this client
                vsiCommonPythonApi.advanceSimulation(self.simulationStep + 1)
            else:
                print(f"An error occurred: {str(e)}")
                import traceback
                traceback.print_exc()
        except:
            # Advance time with a step that is equal to "simulationStep + 1" so that all other clients
            # receive the terminate packet before terminating this client
            vsiCommonPythonApi.advanceSimulation(self.simulationStep + 1)
            import traceback
            traceback.print_exc()

    def establishTcpUdpConnection(self):
        if(self.clientPortNum[ST5_QualityInspection0] == 0):
            self.clientPortNum[ST5_QualityInspection0] = vsiEthernetPythonGateway.tcpConnect(
                bytes(PLC_LineCoordinatorIpAddress), PLC_LineCoordinatorSocketPortNumber0)

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

        # CRITICAL FIX: Decode PLC command packets EXACTLY like ST1/ST2/ST3/ST4
        if self.receivedNumberOfBytes == 9:
            print(f"ST5: Received 9-byte command packet from PLC (len={self.receivedNumberOfBytes})")
            receivedPayload = bytes(self.receivedPayload)
            # EXACTLY like ST1/ST2/ST3/ST4: ?, ?, ?, L, H
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

        #Send ethernet packet to PLC_LineCoordinator on PORT 6005
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
