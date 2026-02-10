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
PLC_LineCoordinatorSocketPortNumber0 = 6005
ST5_QualityInspection0 = 0

# Start of user custom code region. Please apply edits only within these regions:  Global Variables & Definitions
import simpy

# -----------------------------
# Station 5: Quality Inspection + Diverter (Parameterized SimPy core)
# -----------------------------
def _st5_accept_rate(recipe_id: int) -> float:
    base = 0.88
    if int(recipe_id) == 0:
        return base
    return max(0.70, min(0.97, base - (int(recipe_id) % 5) * 0.02))

class _ST5SimModel:
    def __init__(self, random_seed: int = 5, config: dict = None):
        random.seed(int(random_seed))
        self.env = simpy.Environment()
        
        # Load config parameters ONCE at init (VSI constraint: no runtime changes)
        self.config = config or {
            "cycle_time_s": 2.5,   # Base cycle time (sum of all steps)
            "failure_rate": 0.0,   # Probability of catastrophic failure per cycle
            "mttr_s": 0.0,         # Mean Time To Repair
            "buffer_capacity": 2
        }
        
        # State
        self.busy = False
        self.fault_latched = False
        self._unit_done = False
        self._unit_decision = None  # True=accept, False=reject
        self._current_batch = 0
        self._current_recipe = 0
        self._active_proc = None
        
        # Results / KPIs (preserve existing counters)
        self.accept_total = 0
        self.reject_total = 0
        self.last_cycle_time_s = 0.0
        
        # NEW: KPI tracking for Week 2
        self.total_downtime_s = 0.0
        self.total_busy_time_s = 0.0
        self.last_busy_start_s = 0.0
        self.failure_count = 0
        self.completed_cycles = 0
        
        # Base timing constants (seconds) - used for proportional scaling
        self._T_POSITIONING_S = 0.4
        self._T_VISION_S = 0.8
        self._T_RULES_S = 0.3
        self._T_REINSPECT_S = 0.6
        self._T_REMEASURE_S = 0.5
        self._T_DIVERTER_S = 0.2
        
        # Scale factors based on config cycle time (base = 2.5s)
        base_cycle = 2.5
        self._scale = max(0.1, self.config["cycle_time_s"] / base_cycle)
        
        print(f"  _ST5SimModel INITIALIZED with config:")
        print(f"    cycle_time_s={self.config['cycle_time_s']} (scale={self._scale:.2f}), "
              f"failure_rate={self.config['failure_rate']}, mttr_s={self.config['mttr_s']}")

    def reset(self):
        self.env = simpy.Environment()
        self.busy = False
        self.fault_latched = False
        self._unit_done = False
        self._unit_decision = None
        self._current_batch = 0
        self._current_recipe = 0
        self._active_proc = None
        self.accept_total = 0
        self.reject_total = 0
        self.last_cycle_time_s = 0.0
        self.total_downtime_s = 0.0
        self.total_busy_time_s = 0.0
        self.last_busy_start_s = 0.0
        self.failure_count = 0
        self.completed_cycles = 0

    def start_unit(self, batch_id: int, recipe_id: int) -> bool:
        if self.fault_latched or self.busy:
            return False
        self.busy = True
        self._unit_done = False
        self._unit_decision = None
        self._current_batch = int(batch_id)
        self._current_recipe = int(recipe_id)
        self.last_busy_start_s = self.env.now  # Start tracking busy time
        self._active_proc = self.env.process(self._unit_process(batch_id, recipe_id))
        return True

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

    def get_last_decision(self) -> bool:
        """Return the acceptance decision of the last completed unit"""
        return self._unit_decision if self._unit_decision is not None else False
    
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

    # ---- SimPy process ----
    def _unit_process(self, batch_id: int, recipe_id: int):
        t0 = self.env.now
        
        # Stage 1: positioning + camera capture (scaled)
        yield self.env.timeout(self._T_POSITIONING_S * self._scale)
        
        # Stage 2: vision/measurement compute (scaled)
        yield self.env.timeout(self._T_VISION_S * self._scale)
        
        # Stage 3: rules/spec compare (scaled)
        yield self.env.timeout(self._T_RULES_S * self._scale)
        
        # STEP 4: Check for catastrophic failure AFTER positioning/vision but BEFORE decision
        # Represents sensor failure, jam, or critical fault that loses the part
        if random.random() < self.config["failure_rate"]:
            # Failure occurred - simulate downtime
            self.failure_count += 1
            failure_start = self.env.now
            
            # Accumulate busy time BEFORE failure
            if self.last_busy_start_s > 0:
                self.total_busy_time_s += (failure_start - self.last_busy_start_s)
                self.last_busy_start_s = 0.0
            
            # Enter fault state
            self.fault_latched = True
            self.busy = False
            
            # Simulate repair time (MTTR)
            yield self.env.timeout(self.config["mttr_s"])
            
            # Repair complete
            repair_end = self.env.now
            downtime = repair_end - failure_start
            self.total_downtime_s += downtime
            print(f"  ⚠️  ST5 FAILURE at {failure_start:.3f}s - catastrophic event, part lost (downtime={downtime:.2f}s)")
            
            # Exit fault state (reset happens externally via PLC)
            self.fault_latched = False
            # DO NOT proceed to decision logic - part is lost during failure
            return
        
        # Decision logic (preserve existing behavior)
        p_accept = _st5_accept_rate(recipe_id)
        decision_accept = (random.random() < p_accept)
        
        # Optional re-inspection once (rework loop)
        if not decision_accept:
            yield self.env.timeout(self._T_REINSPECT_S * self._scale)
            yield self.env.timeout(self._T_REMEASURE_S * self._scale)
            decision_accept = (random.random() < min(0.95, p_accept + 0.12))
        
        # Diverter actuation (scaled)
        yield self.env.timeout(self._T_DIVERTER_S * self._scale)
        
        # Update KPIs
        self._unit_decision = decision_accept
        if decision_accept:
            self.accept_total += 1
        else:
            self.reject_total += 1
        self.last_cycle_time_s = float(self.env.now - t0)
        self.completed_cycles += 1
        
        # Accumulate busy time for successful cycles
        if self.last_busy_start_s > 0:
            self.total_busy_time_s += (self.env.now - self.last_busy_start_s)
            self.last_busy_start_s = 0.0
        
        # Process will end here, busy flag will be cleared in step() when process dies

# VSI <-> SimPy Wrapper with CONFIG LOADING (Week 2)
class ST5_SimRuntime:
    def __init__(self):
        # Load config ONCE at simulation start (VSI constraint: no runtime changes)
        self.config = self._load_config()
        print(f"ST5_SimRuntime: Loaded config from line_config.json -> cycle_time={self.config['cycle_time_s']}s, "
              f"failure_rate={self.config['failure_rate']}, mttr={self.config['mttr_s']}s")
        
        self.env = simpy.Environment()
        self.station = _ST5SimModel(random_seed=5, config=self.config)
        
        # Handshake state
        self._start_latched = False
        self._prev_cmd_start = 0
        self._prev_cmd_stop = 0
        self._prev_cmd_reset = 0
        
        # Context from PLC
        self.batch_id = 0
        self.recipe_id = 0
        
        # Timing
        self._sim_dt_s = 0.1

    def _load_config(self) -> dict:
        """Load station parameters from external JSON config (Week 2 deliverable)"""
        config_path = "line_config.json"
        default_config = {
            "cycle_time_s": 2.5,
            "failure_rate": 0.0,
            "mttr_s": 0.0,
            "buffer_capacity": 2
        }
        
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    full_config = json.load(f)
                    # Extract S5-specific config
                    if "stations" in full_config and "S5" in full_config["stations"]:
                        return full_config["stations"]["S5"]
                    else:
                        print(f"  ⚠️  WARNING: line_config.json missing 'stations.S5' section - using defaults")
                        return default_config
            except Exception as e:
                print(f"  ⚠️  WARNING: Error loading {config_path}: {e} - using defaults")
                return default_config
        else:
            print(f"  ⚠️  WARNING: {config_path} not found - using default parameters")
            # Create default config file for user convenience (only done by ST1)
            return default_config

    def reset(self):
        """Full reset - reloads config for new simulation run"""
        print("  ST5_SimRuntime: FULL RESET (reloading config)")
        self.config = self._load_config()  # Reload config for new run
        self.env = simpy.Environment()
        self.station = _ST5SimModel(random_seed=5, config=self.config)
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
            print("ST5: RESET rising edge detected")
            self.reset()
            self._prev_cmd_reset = 1
            return
        self._prev_cmd_reset = int(cmd_reset)
        
        # Rising edge detection for start
        start_edge = (cmd_start == 1 and self._prev_cmd_start == 0)
        
        # Stop command (rising edge) - immediate stop
        if cmd_stop and not self._prev_cmd_stop:
            print("ST5: STOP command received")
            self._start_latched = False
            self.station.busy = False
            self._prev_cmd_stop = int(cmd_stop)
        
        # Start logic: ONLY on rising edge AND station idle AND no fault
        if start_edge:
            if (not self.station.busy and not self.station.fault_latched and
                not self._start_latched):
                print(f"ST5: START edge latched (batch={self.batch_id}, recipe={self.recipe_id})")
                success = self.station.start_unit(self.batch_id, self.recipe_id)
                if success:
                    self._start_latched = True
        
        self._prev_cmd_start = int(cmd_start)
        
        # Safety check: if station is busy but start_latched is False, fix it
        if self.station.busy and not self._start_latched:
            print("  ERROR: ST5_SimRuntime: station busy but start_latched=False! Fixing...")
            self._start_latched = True

    def step(self, dt_s: float):
        """Advance simulation ONLY when necessary"""
        should_step = (self._start_latched or self.station.busy) and not self._prev_cmd_reset
        if self._prev_cmd_stop and not self.station.busy:
            should_step = False
        
        if should_step and dt_s > 0:
            target = self.env.now + float(dt_s)
            self.env.run(until=target)
        
        # Check for cycle completion and clear start_latched
        if self.station.get_done_pulse() and not self.station.busy:
            self._start_latched = False

    def outputs(self):
        # Update counters from model
        accept = int(self.station.accept_total)
        reject = int(self.station.reject_total)
        last_accept = 1 if self.station.get_last_decision() else 0
        cycle_time_ms = int(self.station.last_cycle_time_s * 1000.0)
        
        # Status outputs
        busy = 1 if (self.station.busy or self._start_latched) else 0
        fault = 1 if self.station.fault_latched else 0
        done = 1 if self.station.get_done_pulse() else 0
        ready = 1 if (not busy and not fault and not self._start_latched) else 0
        
        return ready, busy, fault, done, cycle_time_ms, accept, reject, last_accept

    def export_kpis(self, total_sim_time_s: float) -> dict:
        """Export structured KPIs for optimizer (Week 2 deliverable)"""
        utilization = self.station.get_utilization(total_sim_time_s)
        availability = self.station.get_availability(total_sim_time_s)
        
        total_inspected = self.station.accept_total + self.station.reject_total
        yield_rate = (self.station.accept_total / max(1, total_inspected)) * 100.0 if total_inspected > 0 else 0.0
        
        return {
            "station": "S5",
            "accept_total": self.station.accept_total,
            "reject_total": self.station.reject_total,
            "yield_rate_pct": yield_rate,
            "completed_cycles": self.station.completed_cycles,
            "total_downtime_s": self.station.total_downtime_s,
            "failure_count": self.station.failure_count,
            "utilization_pct": utilization,
            "availability_pct": availability,
            "config": {
                "cycle_time_s": self.config["cycle_time_s"],
                "failure_rate": self.config["failure_rate"],
                "mttr_s": self.config["mttr_s"],
                "buffer_capacity": self.config["buffer_capacity"]
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
        self._prev_cmd_start = 0
        self._prev_cmd_stop = 0
        self._prev_cmd_reset = 0
        self._start_latched = False
        self._done_pulse_remaining = 0
        self._sim_dt_s = 0.1
        self._initialized = False
        self._current_batch_id = 0
        self._current_recipe_id = 0
        self._sim_start_time_ns = 0  # For KPI calculation at end
        # End of user custom code region. Please don't edit beyond this point.

    def mainThread(self):
        dSession = vsiCommonPythonApi.connectToServer(self.localHost, self.domain, self.portNum, self.componentId)
        vsiEthernetPythonGateway.initialize(dSession, self.componentId, bytes(srcMacAddress), bytes(srcIpAddress))
        try:
            vsiCommonPythonApi.waitForReset()
            # Start of user custom code region. Please apply edits only within these regions:  After Reset
            self._sim_start_time_ns = vsiCommonPythonApi.getSimulationTimeInNs()
            self._sim = ST5_SimRuntime()
            self._prev_cmd_start = 0
            self._prev_cmd_stop = 0
            self._prev_cmd_reset = 0
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
            self.mySignals.accept = 0
            self.mySignals.reject = 0
            self.mySignals.last_accept = 0
            print("ST5: Parameterized SimPy runtime initialized with config from line_config.json")
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
                
                # --- Update context ---
                if self._sim is not None:
                    self._sim.set_context(self.mySignals.batch_id, self.mySignals.recipe_id)
                
                # --- Process handshake ---
                if self._sim is not None:
                    self._sim.update_handshake(cmd_start, cmd_stop, cmd_reset)
                
                # --- STEP SimPy model ---
                if self._sim is not None:
                    self._sim.step(self._sim_dt_s)
                
                # --- Get outputs ---
                if self._sim is not None:
                    (ready, busy, fault, done, cycle_time_ms,
                     accept, reject, last_accept) = self._sim.outputs()
                    
                    # Copy to VSI signals
                    self.mySignals.ready = int(ready)
                    self.mySignals.busy = int(busy)
                    self.mySignals.fault = int(fault)
                    self.mySignals.done = int(done)
                    self.mySignals.cycle_time_ms = int(cycle_time_ms)
                    self.mySignals.accept = int(accept)
                    self.mySignals.reject = int(reject)
                    self.mySignals.last_accept = int(last_accept)
                    
                    # Log KPIs every 20 cycles for visibility
                    if self._sim.station.completed_cycles > 0 and self._sim.station.completed_cycles % 20 == 0:
                        total_sim_time_s = (vsiCommonPythonApi.getSimulationTimeInNs() - self._sim_start_time_ns) / 1e9
                        utilization = self._sim.station.get_utilization(total_sim_time_s)
                        availability = self._sim.station.get_availability(total_sim_time_s)
                        print(f"  📊 ST5 KPIs (cycle #{self._sim.station.completed_cycles}): "
                              f"utilization={utilization:.1f}%, availability={availability:.1f}%, "
                              f"failures={self._sim.station.failure_count}, downtime={self._sim.station.total_downtime_s:.1f}s")
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
            if self._sim is not None:
                total_sim_time_s = (vsiCommonPythonApi.getSimulationTimeInNs() - self._sim_start_time_ns) / 1e9
                kpis = self._sim.export_kpis(total_sim_time_s)
                kpis["simulation_duration_s"] = total_sim_time_s
                
                # Write to station-specific KPI file
                kpi_file = f"ST5_kpis_{int(vsiCommonPythonApi.getSimulationTimeInNs()/1e9)}.json"
                with open(kpi_file, 'w') as f:
                    json.dump(kpis, f, indent=2)
                print(f"\n✅ ST5 KPIs exported to {kpi_file}")
                print(f"   Throughput: {(kpis['completed_cycles'] / total_sim_time_s) * 3600:.1f} units/hour")
                print(f"   Utilization: {kpis['utilization_pct']:.1f}%")
                print(f"   Availability: {kpis['availability_pct']:.1f}%")
                print(f"   Failures: {kpis['failure_count']}")
                print(f"   Yield rate: {kpis['yield_rate_pct']:.1f}%")
            
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
        # DEBUG: Print packet metadata
        print(f"ST5 RX meta dest/src/len: {self.receivedDestPortNumber}, {self.receivedSrcPortNumber}, {self.receivedNumberOfBytes}")
        # Decode by length==9 (command packet)
        if self.receivedNumberOfBytes == 9:
            print("ST5: Received 9-byte packet from PLC -> decoding command...")
            receivedPayload = bytes(self.receivedPayload)
            self.mySignals.cmd_start, receivedPayload = self.unpackBytes('?', receivedPayload)
            self.mySignals.cmd_stop, receivedPayload = self.unpackBytes('?', receivedPayload)
            self.mySignals.cmd_reset, receivedPayload = self.unpackBytes('?', receivedPayload)
            self.mySignals.batch_id, receivedPayload = self.unpackBytes('L', receivedPayload)
            self.mySignals.recipe_id, receivedPayload = self.unpackBytes('H', receivedPayload)
            print(f"ST5: Decoded PLC cmd_start={self.mySignals.cmd_start}, cmd_stop={self.mySignals.cmd_stop}, cmd_reset={self.mySignals.cmd_reset}, "
                  f"batch={self.mySignals.batch_id}, recipe={self.mySignals.recipe_id}")
        else:
            print(f"ST5: Ignoring non-command packet (len={self.receivedNumberOfBytes})")

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
    sT5_QualityInspection = ST5_QualityInspection(args)
    sT5_QualityInspection.mainThread()

if __name__ == '__main__':
    main()
