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
        self.total = 0
        self.completed = 0

srcMacAddress = [0x02, 0x00, 0x00, 0x00, 0x00, 0x14]
PLC_LineCoordinatorMacAddress = [0x02, 0x00, 0x00, 0x00, 0x00, 0x01]
srcIpAddress = [10, 10, 0, 14]
PLC_LineCoordinatorIpAddress = [10, 10, 0, 1]
# Part A.1: Port assignment
ST4_PORT = 6004
PLC_LineCoordinatorSocketPortNumber0 = ST4_PORT
ST4_CalibrationTesting0 = 0

# Start of user custom code region. Please apply edits only within these regions:  Global Variables & Definitions
import simpy

# --- Station 4 (Calibration + Test Chamber) PARAMETERIZED model ---
class FixedCalibrationStation:
    """
    Parameterized station for calibration/testing with config-driven cycle time, failures, and KPI tracking.
    Includes energy consumption tracking like ST1 and ST3.
    """
    def __init__(self, env: simpy.Environment, config: dict):
        self.env = env
        self.config = config  # Store config for entire simulation run
        
        # Load parameters ONCE at init (VSI constraint: no runtime changes)
        self._nominal_cycle_time_s = config.get("cycle_time_s", 41.0)
        self._failure_rate = config.get("failure_rate", 0.0)
        self._mttr_s = config.get("mttr_s", 0.0)
        self._buffer_capacity = config.get("buffer_capacity", 2)
        self._power_rating_w = config.get("power_rating_w", 3500)  # High power for thermal chamber
        
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
        self.total = 0
        self.completed = 0
        
        # KPI tracking
        self.completed_cycles = 0
        self.total_downtime_s = 0.0
        self.total_busy_time_s = 0.0
        self.last_busy_start_s = 0.0
        self.failure_count = 0
        
        # Pass/fail statistics
        self.passed_first_try = 0
        self.passed_after_retry = 0
        self.failed_final = 0
        
        # ENERGY TRACKING (Siemens requirement)
        self.energy_kwh = 0.0
        
        print(f"  FixedCalibrationStation INITIALIZED with config:")
        print(f"    cycle_time_s={self._nominal_cycle_time_s}, failure_rate={self._failure_rate}, "
              f"mttr_s={self._mttr_s}, power_rating_w={self._power_rating_w}W")

    def start_cycle(self, start_time_s: float):
        """Start a new calibration cycle - ONLY called on cmd_start rising edge"""
        if self._cycle_proc is not None or self._busy:
            print(f"  WARNING: FixedCalibrationStation.start_cycle called but already busy!")
            return False
        print(f"  FixedCalibrationStation: Starting job at env.now={self.env.now:.3f}s")
        self.state = "RUNNING"
        self._busy = True
        self._done_pulse = False
        self._cycle_start_s = start_time_s
        self._actual_cycle_time_ms = 0
        self.total += 1
        # Track busy time start for utilization + energy KPIs
        self.last_busy_start_s = self.env.now
        self._cycle_proc = self.env.process(self._calibration_cycle())
        return True

    def _calibration_cycle(self):
        """Run a single calibration cycle with probabilistic failure simulation + ENERGY TRACKING"""
        try:
            # STEP 1: Motion (scaled time)
            motion_s = self._nominal_cycle_time_s * (2.0 / 41.0)
            yield self.env.timeout(motion_s)
            
            # STEP 2: Thermal stabilization (scaled time) - HIGH ENERGY CONSUMPTION
            thermal_s = self._nominal_cycle_time_s * (18.0 / 41.0)
            yield self.env.timeout(thermal_s)
            
            # STEP 3: Calibration (scaled time)
            calibration_s = self._nominal_cycle_time_s * (6.0 / 41.0)
            yield self.env.timeout(calibration_s)
            
            # STEP 4: Test printing (scaled time)
            testprint_s = self._nominal_cycle_time_s * (15.0 / 41.0)
            yield self.env.timeout(testprint_s)
            
            # Accumulate energy for processing time (W × seconds → kWh)
            processing_time = motion_s + thermal_s + calibration_s + testprint_s
            energy_ws = self._power_rating_w * processing_time
            self.energy_kwh += energy_ws / 3.6e6  # Convert W·s to kWh
            
            # STEP 5: Check for catastrophic failure
            if random.random() < self._failure_rate:
                # Failure occurred
                self.failure_count += 1
                failure_start = self.env.now
                print(f"  ⚠️  FAILURE at {failure_start:.3f}s (cycle #{self._cycle_count+1})")
                
                # Enter fault state
                self._fault = True
                self._busy = False
                
                # Accumulate busy time BEFORE failure
                self.total_busy_time_s += (failure_start - self.last_busy_start_s)
                
                # Simulate repair time (MTTR) - NO ENERGY CONSUMED during repair
                yield self.env.timeout(self._mttr_s)
                
                # Repair complete
                repair_end = self.env.now
                downtime = repair_end - failure_start
                self.total_downtime_s += downtime
                print(f"  ✅ REPAIR complete at {repair_end:.3f}s (downtime={downtime:.2f}s)")
                
                # Exit fault state
                self._fault = False
                self.state = "IDLE"
                return
            
            # STEP 6: Pass/fail decision
            passed = (random.random() <= 0.93)
            print(f"  FixedCalibrationStation: First test {'PASSED' if passed else 'FAILED'}")
            
            # If fail, retry once (additional energy consumption)
            if not passed:
                # Retry time (scaled time)
                retry_s = self._nominal_cycle_time_s * (5.0 / 41.0)
                yield self.env.timeout(retry_s)
                
                # Additional energy for retry
                energy_ws = self._power_rating_w * retry_s
                self.energy_kwh += energy_ws / 3.6e6
                
                print(f"  FixedCalibrationStation: Retry completed at {self.env.now:.3f}s")
                passed = (random.random() <= 0.97)
                print(f"  FixedCalibrationStation: Retry test {'PASSED' if passed else 'FAILED'}")
            
            # STEP 7: Complete cycle
            self._cycle_end_s = self.env.now
            actual_time_s = self._cycle_end_s - self._cycle_start_s
            self._actual_cycle_time_ms = int(actual_time_s * 1000)
            
            # Update counters
            self._cycle_count += 1
            self._cycle_time_sum_ms += self._actual_cycle_time_ms
            
            if not passed:
                self._fault = True
                self.failed_final += 1
                print(f"  FixedCalibrationStation: Cycle FAILED after {self._actual_cycle_time_ms}ms")
            else:
                self.completed += 1
                self.completed_cycles += 1
                if random.random() <= 0.93:
                    self.passed_first_try += 1
                else:
                    self.passed_after_retry += 1
                self.state = "COMPLETE"
                self._done_pulse = True
            
            # Accumulate busy time
            self.total_busy_time_s += (self.env.now - self.last_busy_start_s)
            
            print(f"  FixedCalibrationStation: Job completed at env.now={self.env.now:.3f}s, "
                  f"actual_time={self._actual_cycle_time_ms}ms, total_completed={self.completed}")
            
            self._busy = False
            
        except simpy.Interrupt:
            print("  FixedCalibrationStation: Cycle interrupted by stop command")
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
        print("  FixedCalibrationStation: FULL RESET")
        self.stop_cycle()
        self.state = "IDLE"
        self._busy = False
        self._fault = False
        self._done_pulse = False
        self._cycle_proc = None
        self._actual_cycle_time_ms = int(self._nominal_cycle_time_s * 1000)
        self._cycle_count = 0
        self._cycle_time_sum_ms = 0
        self.total = 0
        self.completed = 0
        self.completed_cycles = 0
        self.total_downtime_s = 0.0
        self.total_busy_time_s = 0.0
        self.last_busy_start_s = 0.0
        self.failure_count = 0
        self.passed_first_try = 0
        self.passed_after_retry = 0
        self.failed_final = 0
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

    def get_total(self):
        return self.total

    def get_completed(self):
        return self.completed

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

    def get_first_pass_yield(self) -> float:
        if self.completed_cycles > 0:
            return (self.passed_first_try / self.completed_cycles) * 100.0
        return 0.0

    def get_overall_yield(self) -> float:
        if self.total > 0:
            return (self.completed / self.total) * 100.0
        return 0.0

    # ENERGY getters (Siemens requirement)
    def get_energy_kwh(self) -> float:
        """Get total energy consumed in kWh"""
        return self.energy_kwh

    def get_energy_per_unit_kwh(self) -> float:
        """Get energy consumed per completed unit (kWh/unit)"""
        if self.completed > 0:
            return self.energy_kwh / self.completed
        return 0.0

# VSI <-> SimPy Wrapper with CONFIG LOADING
class ST4_SimRuntime:
    def __init__(self):
        # Load config ONCE at simulation start
        self.config = self._load_config()
        print(f"ST4_SimRuntime: Loaded config from line_config.json -> cycle_time={self.config['cycle_time_s']}s, "
              f"failure_rate={self.config['failure_rate']}, mttr={self.config['mttr_s']}s, "
              f"power={self.config['power_rating_w']}W")
        
        self.env = simpy.Environment()
        self.station = FixedCalibrationStation(self.env, self.config)
        
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
            "cycle_time_s": 41.0,
            "failure_rate": 0.0,
            "mttr_s": 0.0,
            "buffer_capacity": 2,
            "power_rating_w": 3500  # High power for thermal chamber
        }
        
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    full_config = json.load(f)
                    # Extract S4-specific config
                    if "stations" in full_config and "S4" in full_config["stations"]:
                        cfg = full_config["stations"]["S4"]
                        # Ensure power_rating_w exists (backwards compatibility)
                        if "power_rating_w" not in cfg:
                            cfg["power_rating_w"] = default_config["power_rating_w"]
                            print(f"  ⚠️  ST4: power_rating_w not in config - using default {default_config['power_rating_w']}W")
                        return cfg
                    else:
                        print(f"  ⚠️  WARNING: line_config.json missing 'stations.S4' section - using defaults")
                        return default_config
            except Exception as e:
                print(f"  ⚠️  WARNING: Error loading {config_path}: {e} - using defaults")
                return default_config
        else:
            print(f"  ⚠️  WARNING: {config_path} not found - using default parameters")
            return default_config

    def reset(self):
        """Full reset - reloads config for new simulation run"""
        print("  ST4_SimRuntime: FULL RESET (reloading config)")
        self.config = self._load_config()  # Reload config for new run
        self.env = simpy.Environment()
        self.station = FixedCalibrationStation(self.env, self.config)
        self._start_latched = False
        self._prev_cmd_start = 0
        self._prev_cmd_stop = 0
        self._prev_cmd_reset = 0
        print("  ST4_SimRuntime: Reset complete - ready for new simulation")

    def set_context(self, batch_id: int, recipe_id: int):
        self.batch_id = int(batch_id)
        self.recipe_id = int(recipe_id)

    def update_handshake(self, cmd_start: int, cmd_stop: int, cmd_reset: int):
        """Process PLC commands and update internal state"""
        # Reset has highest priority
        if cmd_reset and not self._prev_cmd_reset:
            print("ST4: RESET command received (rising edge)")
            self.reset()
            self._prev_cmd_reset = 1
            return
        self._prev_cmd_reset = int(cmd_reset)
        
        # Rising edge detection for start
        start_edge = (cmd_start == 1 and self._prev_cmd_start == 0)
        self._last_start_edge = start_edge
        
        # Stop command (rising edge) - immediate stop
        if cmd_stop and not self._prev_cmd_stop:
            print("ST4: STOP command received (rising edge)")
            self._start_latched = False
            self.station.stop_cycle()
            self._prev_cmd_stop = int(cmd_stop)
        
        # Start logic: ONLY on rising edge AND station idle AND no fault
        if start_edge:
            if not self.station.is_busy() and not self.station.is_fault():
                print("ST4: START rising edge detected -> starting cycle")
                self._start_latched = True
                self.station.start_cycle(self.env.now)
            else:
                fault_status = "FAULT" if self.station.is_fault() else "BUSY"
                print(f"ST4: START rising edge but station {fault_status} - ignoring")
        
        # Keep start_latched during entire cycle
        self._prev_cmd_start = int(cmd_start)
        
        # Safety check: if station is busy but start_latched is False, fix it
        if self.station.is_busy() and not self._start_latched:
            print("  ⚠️  ERROR: ST4_SimRuntime: station busy but start_latched=False! Fixing...")
            self._start_latched = True
        
        # Safety check: if station has active process but busy flag is False, fix it
        if self.station.has_active_proc() and not self.station.is_busy():
            print("  ⚠️  ERROR: ST4_SimRuntime: active process but busy=False! Fixing...")
            self.station._busy = True

    def step(self, dt_s: float):
        """Advance simulation ONLY when necessary"""
        self._last_step_dt = dt_s
        
        # Step SimPy ONLY if busy OR in fault state OR start_latched
        should_step = self.station.is_busy() or self.station.is_fault() or self._start_latched
        print(f"  ST4_SimRuntime step: env.now={self.env.now:.3f}s, dt_s={dt_s:.6f}s, "
              f"start_latched={self._start_latched}, busy={self.station.is_busy()}, "
              f"fault={self.station.is_fault()}, should_step={should_step}")
        
        # DO NOT step if stop command active and not in cycle/repair
        if self._prev_cmd_stop and not (self.station.is_busy() or self.station.is_fault()):
            print(f"  ST4_SimRuntime: NOT stepping - stop command active and idle")
            return
        
        # Only step if we should step
        if should_step and dt_s > 0:
            target_time = self.env.now + float(dt_s)
            self.env.run(until=target_time)
            print(f"  ST4_SimRuntime: Stepped to env.now={self.env.now:.3f}s")
        
        # Check for cycle completion and clear start_latched
        if self.station.get_done_pulse():
            print("  ST4_SimRuntime: Cycle completed, clearing start_latched")
            self._start_latched = False

    def outputs(self, total_sim_time_s: float):
        """Get station outputs INCLUDING KPIs"""
        busy = 1 if self.station.is_busy() else 0
        fault = 1 if self.station.is_fault() else 0
        total = self.station.get_total()
        completed = self.station.get_completed()
        
        # Ready = not busy AND not fault (independent of start latch)
        ready = 1 if (not busy and not fault) else 0
        
        # Done pulse for exactly ONE iteration after completion
        done = 1 if self.station.get_done_pulse() else 0
        
        # Real cycle time
        cycle_time_ms = self.station.get_cycle_time_ms()
        
        # Calculate utilization/availability for logging
        utilization = self.station.get_utilization(total_sim_time_s)
        availability = self.station.get_availability(total_sim_time_s)
        
        # Log KPIs every 5 cycles for visibility
        if self.station.completed_cycles > 0 and self.station.completed_cycles % 5 == 0:
            energy_per_unit = self.station.get_energy_per_unit_kwh()
            print(f"  📊 ST4 KPIs (cycle #{self.station.completed_cycles}): "
                  f"utilization={utilization:.1f}%, availability={availability:.1f}%, "
                  f"energy={self.station.get_energy_kwh():.4f}kWh, energy/unit={energy_per_unit:.4f}kWh/unit, "
                  f"failures={self.station.get_failure_count()}, downtime={self.station.get_total_downtime_s():.1f}s, "
                  f"first_pass_yield={self.station.get_first_pass_yield():.1f}%, "
                  f"overall_yield={self.station.get_overall_yield():.1f}%")
        
        return ready, busy, fault, done, cycle_time_ms, total, completed

    def export_kpis(self, total_sim_time_s: float) -> dict:
        """Export structured KPIs for optimizer (with ENERGY metrics)"""
        energy_per_unit = self.station.get_energy_per_unit_kwh()
        
        return {
            "station": "S4",
            "total": self.station.get_total(),
            "completed": self.station.get_completed(),
            "completed_cycles": self.station.completed_cycles,
            "passed_first_try": self.station.passed_first_try,
            "passed_after_retry": self.station.passed_after_retry,
            "failed_final": self.station.failed_final,
            "total_downtime_s": self.station.get_total_downtime_s(),
            "failure_count": self.station.get_failure_count(),
            "utilization_pct": self.station.get_utilization(total_sim_time_s),
            "availability_pct": self.station.get_availability(total_sim_time_s),
            "avg_cycle_time_ms": self.station.get_avg_cycle_time_ms(),
            "first_pass_yield": self.station.get_first_pass_yield(),
            "overall_yield": self.station.get_overall_yield(),
            # ENERGY METRICS (Siemens requirement)
            "energy_kwh": self.station.get_energy_kwh(),
            "energy_per_unit_kwh": energy_per_unit,
            "power_rating_w": self.config["power_rating_w"],
            "config": {
                "cycle_time_s": self.config["cycle_time_s"],
                "failure_rate": self.config["failure_rate"],
                "mttr_s": self.config["mttr_s"],
                "power_rating_w": self.config["power_rating_w"]
            }
        }
# End of user custom code region. Please don't edit beyond this point.

class ST4_CalibrationTesting:
    def __init__(self, args):
        self.componentId = 4
        self.localHost = args.server_url
        self.domain = args.domain
        self.portNum = 50105
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
        print("ST4: Initializing...")
        # End of user custom code region. Please don't edit beyond this point.

    def mainThread(self):
        dSession = vsiCommonPythonApi.connectToServer(self.localHost, self.domain, self.portNum, self.componentId)
        vsiEthernetPythonGateway.initialize(dSession, self.componentId, bytes(srcMacAddress), bytes(srcIpAddress))
        try:
            vsiCommonPythonApi.waitForReset()
            # Start of user custom code region. Please apply edits only within these regions:  After Reset
            self._sim_start_time_ns = vsiCommonPythonApi.getSimulationTimeInNs()
            self._sim = ST4_SimRuntime()
            self._prev_done = 0
            self.total_completed = 0
            print("ST4: Parameterized SimPy runtime initialized with config from line_config.json")
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
                # Receive on PORT 6004
                receivedData = vsiEthernetPythonGateway.recvEthernetPacket(ST4_PORT)
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
                    ready, busy, fault, done, cycle_time_ms, total, completed = self._sim.outputs(total_sim_time_s)
                    
                    # Copy SimPy outputs into VSI signals
                    self.mySignals.ready = int(ready)
                    self.mySignals.busy = int(busy)
                    self.mySignals.fault = int(fault)
                    self.mySignals.done = int(done)
                    self.mySignals.cycle_time_ms = int(cycle_time_ms)
                    self.mySignals.total = int(total)
                    self.mySignals.completed = int(completed)
                    
                    # Track completions
                    if done and not self._prev_done:
                        self.total_completed += 1
                        print(f"ST4: Cycle completed! cycle_time={cycle_time_ms}ms, total={self.total_completed}")
                    
                    # Update previous done state
                    self._prev_done = int(self.mySignals.done)
                # End of user custom code region. Please don't edit beyond this point.
                #Send ethernet packet to PLC_LineCoordinator
                self.sendEthernetPacketToPLC_LineCoordinator()
                print("\n+=ST4_CalibrationTesting+=")
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
                print("\ttotal =", end = " ")
                print(self.mySignals.total)
                print("\tcompleted =", end = " ")
                print(self.mySignals.completed)
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
            
            # SIMULATION COMPLETE - Export KPIs to file
            if self._sim is not None:
                total_sim_time_s = (vsiCommonPythonApi.getSimulationTimeInNs() - self._sim_start_time_ns) / 1e9
                kpis = self._sim.export_kpis(total_sim_time_s)
                kpis["simulation_duration_s"] = total_sim_time_s
                
                # Write to station-specific KPI file
                kpi_file = f"ST4_kpis_{int(vsiCommonPythonApi.getSimulationTimeInNs()/1e9)}.json"
                with open(kpi_file, 'w') as f:
                    json.dump(kpis, f, indent=2)
                print(f"\n✅ ST4 KPIs exported to {kpi_file}")
                print(f"   Throughput: {kpis['completed'] / total_sim_time_s * 3600:.1f} units/hour")
                print(f"   Energy: {kpis['energy_kwh']:.4f} kWh total")
                print(f"   Energy per unit: {kpis['energy_per_unit_kwh']:.4f} kWh/unit")
                print(f"   Utilization: {kpis['utilization_pct']:.1f}%")
                print(f"   Availability: {kpis['availability_pct']:.1f}%")
                print(f"   First-pass yield: {kpis['first_pass_yield']:.1f}%")
                print(f"   Overall yield: {kpis['overall_yield']:.1f}%")
            
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
        if(self.clientPortNum[ST4_CalibrationTesting0] == 0):
            self.clientPortNum[ST4_CalibrationTesting0] = vsiEthernetPythonGateway.tcpConnect(
                bytes(PLC_LineCoordinatorIpAddress), PLC_LineCoordinatorSocketPortNumber0)
        if(self.clientPortNum[ST4_CalibrationTesting0] == 0):
            print(f"Error: Failed to connect to port: PLC_LineCoordinator on TCP port: {ST4_PORT}")
            exit()

    def decapsulateReceivedData(self, receivedData):
        self.receivedDestPortNumber = receivedData[0]
        self.receivedSrcPortNumber = receivedData[1]
        self.receivedNumberOfBytes = receivedData[3]
        self.receivedPayload = [0] * (self.receivedNumberOfBytes)
        for i in range(self.receivedNumberOfBytes):
            self.receivedPayload[i] = receivedData[2][i]
        # CRITICAL FIX: Decode PLC command packets EXACTLY like ST1/ST2/ST3
        if self.receivedNumberOfBytes == 9:
            print(f"ST4: Received 9-byte command packet from PLC (len={self.receivedNumberOfBytes})")
            receivedPayload = bytes(self.receivedPayload)
            # EXACTLY like ST1/ST2/ST3: ?, ?, ?, L, H
            self.mySignals.cmd_start, receivedPayload = self.unpackBytes('?', receivedPayload)
            self.mySignals.cmd_stop, receivedPayload = self.unpackBytes('?', receivedPayload)
            self.mySignals.cmd_reset, receivedPayload = self.unpackBytes('?', receivedPayload)
            self.mySignals.batch_id, receivedPayload = self.unpackBytes('L', receivedPayload)
            self.mySignals.recipe_id, receivedPayload = self.unpackBytes('H', receivedPayload)
            print(f"ST4 decoded PLC command: cmd_start={self.mySignals.cmd_start}, cmd_stop={self.mySignals.cmd_stop}, "
                  f"cmd_reset={self.mySignals.cmd_reset}, batch_id={self.mySignals.batch_id}, "
                  f"recipe_id={self.mySignals.recipe_id}")
        elif self.receivedNumberOfBytes > 0:
            print(f"ST4 ignoring packet: wrong size ({self.receivedNumberOfBytes} bytes, expected 9)")

    def sendEthernetPacketToPLC_LineCoordinator(self):
        bytesToSend = bytes()
        bytesToSend += self.packBytes('?', self.mySignals.ready)
        bytesToSend += self.packBytes('?', self.mySignals.busy)
        bytesToSend += self.packBytes('?', self.mySignals.fault)
        bytesToSend += self.packBytes('?', self.mySignals.done)
        bytesToSend += self.packBytes('L', self.mySignals.cycle_time_ms)
        bytesToSend += self.packBytes('L', self.mySignals.total)
        bytesToSend += self.packBytes('L', self.mySignals.completed)
        # Send to PLC PORT 6004
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
    args = inputArgs.parse_args()
    sT4_CalibrationTesting = ST4_CalibrationTesting(args)
    sT4_CalibrationTesting.mainThread()

if __name__ == '__main__':
    main()
