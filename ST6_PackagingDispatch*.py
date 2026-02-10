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
import random
import simpy

# -----------------------------
# Station 6: Packaging + Dispatch (Parameterized SimPy core)
# -----------------------------
class _ST6SimModel:
    def __init__(self, random_seed: int = 6, config: dict = None):
        random.seed(int(random_seed))
        self.env = simpy.Environment()
        
        # Load config parameters ONCE at init (VSI constraint: no runtime changes)
        self.config = config or {
            "cycle_time_s": 6.7,   # Base cycle time (sum of all steps without refills/repairs)
            "failure_rate": 0.0,   # Probability of catastrophic failure per cycle
            "mttr_s": 0.0,         # Mean Time To Repair for catastrophic failures
            "buffer_capacity": 2
        }
        
        # State
        self.busy = False
        self.fault_latched = False
        self._unit_done = False
        self._unit_decision = None  # True=success, False=catastrophic failure
        
        # Stocks (preserve existing material handling)
        self.carton_stock = 12
        self.tape_stock = 12
        self.label_stock = 12
        
        # KPIs (preserve existing counters)
        self.packages_completed_total = 0
        self.arm_cycles_total = 0
        self.total_repairs_count = 0
        self.operational_time_s = 0.0
        self.downtime_s = 0.0
        self.availability = 0.0
        
        # NEW: KPI tracking for Week 2
        self.total_downtime_s = 0.0          # Accumulated downtime from catastrophic failures
        self.total_busy_time_s = 0.0         # Accumulated productive time (excluding MTTR)
        self.last_busy_start_s = 0.0         # For tracking current busy period
        self.failure_count = 0               # Total catastrophic failures occurred
        self.completed_cycles = 0            # Successful cycles (excluding catastrophic failures)
        
        # Last cycle timing
        self.last_cycle_time_s = 0.0
        self._active_proc = None
        
        # Base timing constants (seconds) - used for proportional scaling
        self._T_CARTON_ERECT_S = 1.0
        self._T_ROBOT_PICKPLACE_S = 1.2
        self._T_FLAP_FOLD_S = 1.5
        self._T_TAPE_SEAL_S = 1.2
        self._T_LABEL_APPLY_S = 1.0
        self._T_OUTFEED_S = 0.8
        
        # Scale factor based on config cycle time (base = 6.7s)
        base_cycle = 6.7
        self._scale = max(0.1, self.config["cycle_time_s"] / base_cycle)
        
        print(f"  _ST6SimModel INITIALIZED with config:")
        print(f"    cycle_time_s={self.config['cycle_time_s']} (scale={self._scale:.2f}), "
              f"failure_rate={self.config['failure_rate']}, mttr_s={self.config['mttr_s']}")

    def reset(self):
        self.env = simpy.Environment()
        self.busy = False
        self.fault_latched = False
        self._unit_done = False
        self._unit_decision = None
        self.carton_stock = 12
        self.tape_stock = 12
        self.label_stock = 12
        self.packages_completed_total = 0
        self.arm_cycles_total = 0
        self.total_repairs_count = 0
        self.operational_time_s = 0.0
        self.downtime_s = 0.0
        self.availability = 0.0
        self.total_downtime_s = 0.0
        self.total_busy_time_s = 0.0
        self.last_busy_start_s = 0.0
        self.failure_count = 0
        self.completed_cycles = 0
        self.last_cycle_time_s = 0.0
        self._active_proc = None

    def step(self, dt_s: float):
        if dt_s <= 0:
            return
        target = self.env.now + float(dt_s)
        self.env.run(until=target)
        # Check if process completed during this step
        if self._active_proc and not self._active_proc.is_alive and self.busy:
            self.busy = False
            self._unit_done = True

    def get_done_pulse(self) -> bool:
        """Return True if a unit just completed, then clear the flag"""
        if self._unit_done:
            self._unit_done = False
            return True
        return False

    def start_unit(self, batch_id: int, recipe_id: int) -> bool:
        if self.fault_latched or self.busy:
            return False
        self.busy = True
        self._unit_done = False
        self._unit_decision = None
        self.last_busy_start_s = self.env.now  # Start tracking busy time
        self._active_proc = self.env.process(self._pack_one_unit(batch_id, recipe_id))
        return True
    
    # NEW: KPI getters for Week 2
    def get_utilization(self, total_sim_time_s: float) -> float:
        """Calculate station utilization (busy time / total time)"""
        if total_sim_time_s <= 0:
            return 0.0
        return (self.total_busy_time_s / total_sim_time_s) * 100.0

    def get_availability(self, total_sim_time_s: float) -> float:
        """Calculate station availability (uptime / total time)"""
        if total_sim_time_s <= 0:
            return 0.0
        uptime = total_sim_time_s - self.total_downtime_s
        return (uptime / total_sim_time_s) * 100.0

    # -------- helpers (preserve existing logic) --------
    def _update_availability(self):
        total = self.operational_time_s + self.downtime_s
        if total > 0:
            self.availability = (self.operational_time_s / total * 100.0)
        else:
            self.availability = 0.0

    def _downtime(self, seconds: float):
        seconds = max(0.0, float(seconds))
        self.downtime_s += seconds
        self._update_availability()
        return self.env.timeout(seconds)

    def _operate(self, seconds: float):
        seconds = max(0.0, float(seconds))
        self.operational_time_s += seconds
        self._update_availability()
        return self.env.timeout(seconds)

    def _maybe_fault(self, p: float) -> bool:
        if random.random() < float(p):
            return True
        return False

    def _refill(self, kind: str):
        # simple refill delay (operator refills) - preserve existing logic
        if kind == "carton":
            yield self._downtime(4.0)
            self.carton_stock = 25
        elif kind == "tape":
            yield self._downtime(3.0)
            self.tape_stock = 25
        elif kind == "label":
            yield self._downtime(3.5)
            self.label_stock = 25

    def _repair(self, seconds: float = 5.0):
        self.total_repairs_count += 1
        yield self._downtime(seconds)

    # -------- main process (enhanced with catastrophic failure) --------
    def _pack_one_unit(self, batch_id: int, recipe_id: int):
        t0 = self.env.now
        
        # Check / refill materials (preserve existing logic)
        if self.carton_stock <= 0:
            yield from self._refill("carton")
        if self.tape_stock <= 0:
            yield from self._refill("tape")
        if self.label_stock <= 0:
            yield from self._refill("label")
        
        # Step 1: carton erect (scaled)
        if self._maybe_fault(0.010):
            yield from self._repair(5.0)
        yield self._operate(self._T_CARTON_ERECT_S * self._scale)
        self.carton_stock -= 1
        
        # Step 2: robot pick+place (scaled)
        if self._maybe_fault(0.015):
            yield from self._repair(6.0)
        yield self._operate(self._T_ROBOT_PICKPLACE_S * self._scale)
        self.arm_cycles_total += 1
        
        # Step 3: flap fold (scaled)
        if self._maybe_fault(0.008):
            yield from self._repair(4.5)
        yield self._operate(self._T_FLAP_FOLD_S * self._scale)
        
        # Step 4: tape seal (scaled)
        if self.tape_stock <= 0:
            yield from self._refill("tape")
        if self._maybe_fault(0.010):
            yield from self._repair(5.5)
        yield self._operate(self._T_TAPE_SEAL_S * self._scale)
        self.tape_stock -= 1
        
        # Step 5: label apply (scaled)
        if self.label_stock <= 0:
            yield from self._refill("label")
        if self._maybe_fault(0.010):
            yield from self._repair(5.0)
        yield self._operate(self._T_LABEL_APPLY_S * self._scale)
        self.label_stock -= 1
        
        # Step 6: outfeed (scaled)
        if self._maybe_fault(0.005):
            yield from self._repair(4.0)
        yield self._operate(self._T_OUTFEED_S * self._scale)
        
        # STEP 7: Check for CATASTROPHIC failure AFTER all steps complete (Week 2 deliverable)
        # Represents jam, system crash, or critical fault that loses the entire package
        if random.random() < self.config["failure_rate"]:
            # Catastrophic failure occurred - simulate downtime
            self.failure_count += 1
            failure_start = self.env.now
            
            # Accumulate busy time BEFORE failure (all steps completed successfully)
            if self.last_busy_start_s > 0:
                self.total_busy_time_s += (failure_start - self.last_busy_start_s)
                self.last_busy_start_s = 0.0
            
            # Enter fault state
            self.fault_latched = True
            self.busy = False
            
            # Simulate repair time (MTTR)
            yield self._downtime(self.config["mttr_s"])
            
            # Repair complete - DO NOT increment package count (part lost)
            repair_end = self.env.now
            downtime = repair_end - failure_start
            self.total_downtime_s += downtime  # Track catastrophic downtime separately
            print(f"  ⚠️  ST6 CATASTROPHIC FAILURE at {failure_start:.3f}s - package lost (downtime={downtime:.2f}s)")
            
            # Exit fault state (reset happens externally via PLC)
            self.fault_latched = False
            self._unit_decision = False
            self.last_cycle_time_s = float(self.env.now - t0)
            return
        
        # SUCCESS: Package completed successfully
        self.packages_completed_total += 1
        self.completed_cycles += 1
        self.last_cycle_time_s = float(self.env.now - t0)
        
        # Accumulate busy time for successful cycle
        if self.last_busy_start_s > 0:
            self.total_busy_time_s += (self.env.now - self.last_busy_start_s)
            self.last_busy_start_s = 0.0
        
        self._unit_decision = True
        # Process will end here, busy flag will be cleared in step() when process dies

# VSI <-> SimPy Wrapper with CONFIG LOADING (Week 2)
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
        # SimPy station model (will be initialized in reset)
        self._st6 = None
        # Handshake state variables (like ST1 pattern)
        self._prev_cmd_start = 0
        self._prev_cmd_reset = 0
        self._prev_cmd_stop = 0
        # Runtime state
        self._start_latched = False
        self._done_pulse_remaining = 0
        # Timing
        self._sim_dt_s = 0.1
        # Station initialization
        self._initialized = False
        # Current batch/recipe
        self._current_batch_id = 0
        self._current_recipe_id = 0
        self._sim_start_time_ns = 0  # For KPI calculation at end
        # End of user custom code region. Please don't edit beyond this point.

    def _load_config(self) -> dict:
        """Load station parameters from external JSON config (Week 2 deliverable)"""
        config_path = "line_config.json"
        default_config = {
            "cycle_time_s": 6.7,
            "failure_rate": 0.0,
            "mttr_s": 0.0,
            "buffer_capacity": 2
        }
        
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    full_config = json.load(f)
                    # Extract S6-specific config
                    if "stations" in full_config and "S6" in full_config["stations"]:
                        return full_config["stations"]["S6"]
                    else:
                        print(f"  ⚠️  WARNING: line_config.json missing 'stations.S6' section - using defaults")
                        return default_config
            except Exception as e:
                print(f"  ⚠️  WARNING: Error loading {config_path}: {e} - using defaults")
                return default_config
        else:
            print(f"  ⚠️  WARNING: {config_path} not found - using default parameters")
            # Create default config file for user convenience (only done by ST1)
            return default_config

    def mainThread(self):
        dSession = vsiCommonPythonApi.connectToServer(self.localHost, self.domain, self.portNum, self.componentId)
        vsiEthernetPythonGateway.initialize(dSession, self.componentId, bytes(srcMacAddress), bytes(srcIpAddress))
        try:
            vsiCommonPythonApi.waitForReset()
            # Start of user custom code region. Please apply edits only within these regions:  After Reset
            self._sim_start_time_ns = vsiCommonPythonApi.getSimulationTimeInNs()
            config = self._load_config()
            self._st6 = _ST6SimModel(random_seed=6, config=config)
            self._prev_cmd_start = 0
            self._prev_cmd_reset = 0
            self._prev_cmd_stop = 0
            self._start_latched = False
            self._done_pulse_remaining = 0
            self._initialized = True
            self._current_batch_id = 0
            self._current_recipe_id = 0
            # Initialize outputs
            self.mySignals.ready = 0
            self.mySignals.busy = 0
            self.mySignals.fault = 0
            self.mySignals.done = 0
            self.mySignals.cycle_time_ms = 0
            self.mySignals.packages_completed = 0
            self.mySignals.arm_cycles = 0
            self.mySignals.total_repairs = 0
            self.mySignals.operational_time_s = 0
            self.mySignals.downtime_s = 0
            self.mySignals.availability = 0
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
                # --- update dt from VSI ---
                try:
                    self._sim_dt_s = max(0.001, float(self.simulationStep) / 1e9)
                except Exception:
                    self._sim_dt_s = 0.1
                
                # --- Get current command states ---
                cmd_start = 1 if self.mySignals.cmd_start else 0
                cmd_stop = 1 if self.mySignals.cmd_stop else 0
                cmd_reset = 1 if self.mySignals.cmd_reset else 0
                
                # --- Detect edges ---
                start_edge = (cmd_start == 1 and self._prev_cmd_start == 0)
                reset_edge = (cmd_reset == 1 and self._prev_cmd_reset == 0)
                
                # --- RESET handling (rising edge only) ---
                if reset_edge:
                    print("ST6: RESET rising edge detected")
                    # Reload config for new simulation run
                    config = self._load_config()
                    self._st6 = _ST6SimModel(random_seed=6, config=config)
                    # Clear all latches and outputs
                    self._start_latched = False
                    self._done_pulse_remaining = 0
                    self._initialized = True  # Station is now initialized
                    self.mySignals.ready = 1  # Ready after reset
                    self.mySignals.busy = 0
                    self.mySignals.fault = 0
                    self.mySignals.done = 0
                    self.mySignals.cycle_time_ms = 0
                    self.mySignals.packages_completed = 0
                    self.mySignals.arm_cycles = 0
                    self.mySignals.total_repairs = 0
                    self.mySignals.operational_time_s = 0
                    self.mySignals.downtime_s = 0
                    self.mySignals.availability = 0
                
                # --- START handling (one-shot pulse) ---
                if start_edge and not cmd_stop and not cmd_reset and self._initialized:
                    # Check if station is ready to start
                    if (not self._st6.busy and not self._st6.fault_latched and
                        not self._start_latched and self._done_pulse_remaining == 0):
                        print(f"ST6: START edge latched (batch={self.mySignals.batch_id}, recipe={self.mySignals.recipe_id})")
                        success = self._st6.start_unit(self.mySignals.batch_id, self.mySignals.recipe_id)
                        if success:
                            self._start_latched = True
                            self.mySignals.ready = 0
                            self.mySignals.busy = 1
                            self._current_batch_id = self.mySignals.batch_id
                            self._current_recipe_id = self.mySignals.recipe_id
                
                # --- STEP SimPy model (CRITICAL: step based on latched/busy state, NOT cmd_start) ---
                # Step if start_latched OR busy OR done_pulse_remaining>0
                should_step = (self._start_latched or self._st6.busy or self._done_pulse_remaining > 0) and self._initialized and not cmd_reset
                # Stop command pauses stepping
                if cmd_stop and not cmd_reset:
                    should_step = False
                    self.mySignals.ready = 0
                
                if should_step:
                    # Debug log when stepping without cmd_start
                    if cmd_start == 0 and (self._start_latched or self._st6.busy):
                        print("ST6: stepping (latched/busy) cmd_start=0")
                    self._st6.step(self._sim_dt_s)
                
                # --- CYCLE COMPLETION handling ---
                if self._st6.get_done_pulse():
                    print("ST6: Cycle complete -> emitting DONE pulse")
                    # Update KPI counters from model (preserve existing counters)
                    self.mySignals.packages_completed = int(self._st6.packages_completed_total)
                    self.mySignals.arm_cycles = int(self._st6.arm_cycles_total)
                    self.mySignals.total_repairs = int(self._st6.total_repairs_count)
                    self.mySignals.operational_time_s = float(self._st6.operational_time_s)
                    self.mySignals.downtime_s = float(self._st6.downtime_s)
                    # Calculate availability (prevent NaN)
                    total = self._st6.operational_time_s + self._st6.downtime_s
                    if total > 0:
                        self.mySignals.availability = float(self._st6.operational_time_s / total * 100.0)
                    else:
                        self.mySignals.availability = 0.0
                    # Update cycle time
                    self.mySignals.cycle_time_ms = int(self._st6.last_cycle_time_s * 1000.0)
                    # Set done pulse
                    self._done_pulse_remaining = 1
                    self._start_latched = False
                    self.mySignals.busy = 0
                    self.mySignals.ready = 1
                
                # --- DONE pulse output (one-shot) ---
                self.mySignals.done = 1 if self._done_pulse_remaining > 0 else 0
                if self._done_pulse_remaining > 0:
                    self._done_pulse_remaining -= 1
                
                # --- Update status outputs ---
                # Busy and fault directly from model (but override if done pulse is active)
                self.mySignals.busy = 1 if (self._st6.busy or self._start_latched) else 0
                self.mySignals.fault = 1 if self._st6.fault_latched else 0
                # READY logic: not busy, not start_latched, no done pulse active, not resetting
                # Note: ready already set appropriately above
                
                # Save previous states for edge detection
                self._prev_cmd_start = cmd_start
                self._prev_cmd_stop = cmd_stop
                self._prev_cmd_reset = cmd_reset
                
                # Log KPIs every 10 cycles for visibility
                if self._st6.completed_cycles > 0 and self._st6.completed_cycles % 10 == 0:
                    total_sim_time_s = (vsiCommonPythonApi.getSimulationTimeInNs() - self._sim_start_time_ns) / 1e9
                    utilization = self._st6.get_utilization(total_sim_time_s)
                    availability = self._st6.get_availability(total_sim_time_s)
                    print(f"  📊 ST6 KPIs (cycle #{self._st6.completed_cycles}): "
                          f"utilization={utilization:.1f}%, availability={availability:.1f}%, "
                          f"catastrophic_failures={self._st6.failure_count}, downtime={self._st6.total_downtime_s:.1f}s")
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
            
            # SIMULATION COMPLETE - Export KPIs to file (Week 2 deliverable)
            if self._st6 is not None:
                total_sim_time_s = (vsiCommonPythonApi.getSimulationTimeInNs() - self._sim_start_time_ns) / 1e9
                kpis = {
                    "station": "S6",
                    "packages_completed": self._st6.packages_completed_total,
                    "arm_cycles": self._st6.arm_cycles_total,
                    "total_repairs": self._st6.total_repairs_count,
                    "completed_cycles": self._st6.completed_cycles,
                    "catastrophic_failures": self._st6.failure_count,
                    "total_downtime_s": self._st6.total_downtime_s,
                    "operational_time_s": self._st6.operational_time_s,
                    "downtime_s": self._st6.downtime_s,
                    "utilization_pct": self._st6.get_utilization(total_sim_time_s),
                    "availability_pct": self._st6.get_availability(total_sim_time_s),
                    "config": {
                        "cycle_time_s": self._st6.config["cycle_time_s"],
                        "failure_rate": self._st6.config["failure_rate"],
                        "mttr_s": self._st6.config["mttr_s"],
                        "buffer_capacity": self._st6.config["buffer_capacity"]
                    }
                }
                kpis["simulation_duration_s"] = total_sim_time_s
                
                # Write to station-specific KPI file
                kpi_file = f"ST6_kpis_{int(vsiCommonPythonApi.getSimulationTimeInNs()/1e9)}.json"
                with open(kpi_file, 'w') as f:
                    json.dump(kpis, f, indent=2)
                print(f"\n✅ ST6 KPIs exported to {kpi_file}")
                print(f"   Throughput: {(kpis['packages_completed'] / total_sim_time_s) * 3600:.1f} units/hour")
                print(f"   Utilization: {kpis['utilization_pct']:.1f}%")
                print(f"   Availability: {kpis['availability_pct']:.1f}%")
                print(f"   Catastrophic failures: {kpis['catastrophic_failures']}")
                print(f"   Total downtime: {kpis['total_downtime_s']:.1f}s")
            
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
        # DEBUG: Print packet metadata
        print(f"ST6 RX meta dest/src/len: {self.receivedDestPortNumber}, {self.receivedSrcPortNumber}, {self.receivedNumberOfBytes}")
        # Decode by length==9 (command packet)
        if self.receivedNumberOfBytes == 9:
            print("ST6: Received 9-byte packet from PLC -> decoding command...")
            receivedPayload = bytes(self.receivedPayload)
            self.mySignals.cmd_start, receivedPayload = self.unpackBytes('?', receivedPayload)
            self.mySignals.cmd_stop, receivedPayload = self.unpackBytes('?', receivedPayload)
            self.mySignals.cmd_reset, receivedPayload = self.unpackBytes('?', receivedPayload)
            self.mySignals.batch_id, receivedPayload = self.unpackBytes('L', receivedPayload)
            self.mySignals.recipe_id, receivedPayload = self.unpackBytes('H', receivedPayload)
            print(f"ST6: Decoded PLC cmd_start={self.mySignals.cmd_start}, cmd_stop={self.mySignals.cmd_stop}, cmd_reset={self.mySignals.cmd_reset}, "
                  f"batch={self.mySignals.batch_id}, recipe={self.mySignals.recipe_id}")
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
        #Send ethernet packet to PLC_LineCoordinator
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
    sT6_PackagingDispatch = ST6_PackagingDispatch(args)
    sT6_PackagingDispatch.mainThread()

if __name__ == '__main__':
    main()
