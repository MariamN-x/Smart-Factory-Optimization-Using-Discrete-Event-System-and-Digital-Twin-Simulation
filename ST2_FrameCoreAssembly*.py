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
    Parameterized station with config-driven cycle time, failures, and KPI tracking.
    All parameters loaded ONCE at init/reset - NO runtime changes (VSI constraint).
    Preserves existing recipe-based timing, scrap/rework logic.
    """
    def __init__(self, env: simpy.Environment, config: dict):
        self.env = env
        self.config = config  # Store config for entire simulation run
        
        # Load parameters ONCE at init (VSI constraint: no runtime changes)
        self._nominal_cycle_time_s = config.get("cycle_time_s", 12.0)
        self._failure_rate = config.get("failure_rate", 0.0)      # Probability of failure per cycle
        self._mttr_s = config.get("mttr_s", 0.0)                   # Mean Time To Repair (seconds)
        self._buffer_capacity = config.get("buffer_capacity", 2)   # For consistency (handled by PLC)
        self._cycle_time_jitter = 0.15  # Keep existing jitter behavior
        
        # State variables
        self.state = "IDLE"
        self._cycle_proc = None
        self._busy = False
        self._fault = False
        self._done_latched = False
        self._ready = True
        
        # Cycle timing
        self._cycle_start_s = 0
        self._last_cycle_time_ms = int(self._nominal_cycle_time_s * 1000)
        
        # Production counters (preserve existing functionality)
        self._completed = 0
        self._scrapped = 0
        self._reworks = 0
        self._total_cycles = 0
        self._cycle_time_sum_s = 0.0
        self._cycle_time_avg_s = 0.0
        
        # KPI tracking (NEW for Week 2)
        self.total_downtime_s = 0.0      # Accumulated downtime from failures
        self.total_busy_time_s = 0.0     # Accumulated productive time
        self.last_busy_start_s = 0.0     # For tracking current busy period
        self.failure_count = 0           # Total failures occurred
        
        print(f"  FixedFrameCoreHandler INITIALIZED with config:")
        print(f"    nominal_cycle_time_s={self._nominal_cycle_time_s}, failure_rate={self._failure_rate}, mttr_s={self._mttr_s}")

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
        self._last_cycle_time_ms = int(self._nominal_cycle_time_s * 1000)
        self.total_downtime_s = 0.0
        self.total_busy_time_s = 0.0
        self.last_busy_start_s = 0.0
        self.failure_count = 0

    def start_cycle(self, recipe_id: int):
        """Starts a cycle; returns False if already busy."""
        if self._busy:
            return False
        
        # Recipe-based timing (preserve existing behavior)
        base_time = 14.0 if recipe_id == 1 else self._nominal_cycle_time_s
        jitter = 1.0 + random.uniform(-self._cycle_time_jitter, self._cycle_time_jitter)
        self._current_cycle_time_s = max(2.0, base_time * jitter)
        
        self.state = "RUNNING"
        self._busy = True
        self._done_latched = False
        self._ready = False
        self._fault = False
        self._cycle_start_s = self.env.now
        self.last_busy_start_s = self.env.now  # Start tracking busy time
        
        self._cycle_proc = self.env.process(self._run_cycle())
        print(f"  FixedFrameCoreHandler: Starting cycle at env.now={self.env.now:.3f}s, expected={self._current_cycle_time_s:.3f}s")
        return True

    def _run_cycle(self):
        try:
            # STEP 1: Simulate normal processing time
            yield self.env.timeout(self._current_cycle_time_s)
            
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
            
            # STEP 3: Check for failure AFTER processing completes (realistic failure model)
            if random.random() < self._failure_rate:
                # Failure occurred - simulate downtime
                self.failure_count += 1
                failure_start = self.env.now
                print(f"  ⚠️  FAILURE at {failure_start:.3f}s (cycle #{self._total_cycles})")
                
                # Enter fault state
                self._fault = True
                self._busy = False
                
                # Simulate repair time (MTTR)
                yield self.env.timeout(self._mttr_s)
                
                # Repair complete
                repair_end = self.env.now
                downtime = repair_end - failure_start
                self.total_downtime_s += downtime
                print(f"  ✅ REPAIR complete at {repair_end:.3f}s (downtime={downtime:.2f}s)")
                
                # Exit fault state
                self._fault = False
                self.state = "IDLE"
                
                # DO NOT update production counters - part is lost during failure
                return
            
            # STEP 4: Determine outcome (preserve existing scrap/rework logic)
            r = random.random()
            if r < 0.02:  # 2% Scrap
                self.state = "SCRAPPED"
                self._scrapped += 1
            elif r < 0.07:  # 5% Rework (cumulative 7%)
                self.state = "REWORK"
                self._reworks += 1
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
            
            # Accumulate partial busy time on interrupt
            if self.last_busy_start_s > 0:
                self.total_busy_time_s += (self.env.now - self.last_busy_start_s)
                self.last_busy_start_s = 0.0
            
            self._cycle_proc = None
            self.state = "IDLE"

    def stop_cycle(self):
        if self._cycle_proc:
            self._cycle_proc.interrupt()
        self.state = "IDLE"
        self._busy = False
        self._done_latched = False
        self._fault = False
        
        # Accumulate partial busy time on stop
        if self.last_busy_start_s > 0:
            self.total_busy_time_s += (self.env.now - self.last_busy_start_s)
            self.last_busy_start_s = 0.0

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

    def get_total_downtime_s(self) -> float:
        return self.total_downtime_s

    def get_failure_count(self) -> int:
        return self.failure_count


class ST2_FixedSimRuntime:
    def __init__(self):
        # Load config ONCE at simulation start (VSI constraint: no runtime changes)
        self.config = self._load_config()
        print(f"ST2_FixedSimRuntime: Loaded config from line_config.json -> cycle_time={self.config['cycle_time_s']}s, "
              f"failure_rate={self.config['failure_rate']}, mttr={self.config['mttr_s']}s")
        
        self.env = simpy.Environment()
        self.handler = FixedFrameCoreHandler(self.env, self.config)
        
        # Handshake state
        self._run_latched = False
        self._prev_cmd_start = 0
        
        # Context
        self.batch_id = 0
        self.recipe_id = 0

    def _load_config(self) -> dict:
        """Load station parameters from external JSON config (Week 2 deliverable)"""
        config_path = "line_config.json"
        default_config = {
            "cycle_time_s": 12.0,
            "failure_rate": 0.0,
            "mttr_s": 0.0,
            "buffer_capacity": 2
        }
        
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    full_config = json.load(f)
                    # Extract S2-specific config
                    if "stations" in full_config and "S2" in full_config["stations"]:
                        return full_config["stations"]["S2"]
                    else:
                        print(f"  ⚠️  WARNING: line_config.json missing 'stations.S2' section - using defaults")
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
        print("  ST2_FixedSimRuntime: FULL RESET (reloading config)")
        self.config = self._load_config()  # Reload config for new run
        self.env = simpy.Environment()
        self.handler = FixedFrameCoreHandler(self.env, self.config)
        self._run_latched = False
        self._prev_cmd_start = 0
        print("  ST2_FixedSimRuntime: Reset complete - ready for new simulation")

    def update_handshake(self, cmd_start: int, cmd_stop: int, cmd_reset: int):
        """Process PLC commands and update internal state"""
        cmd_start_int = 1 if cmd_start else 0
        
        # Handle reset
        if cmd_reset:
            self.reset()
            return
        
        # Rising edge of cmd_start AND handler is idle AND no fault
        if cmd_start_int == 1 and self._prev_cmd_start == 0 and not self.handler._busy and not self.handler._fault:
            # Clear any previous done flag
            self.handler._done_latched = False
            self._run_latched = True
            print(f"  ST2_FixedSimRuntime: Rising edge cmd_start, starting cycle")
        
        # Falling edge of cmd_start - clear done latch (preserve existing behavior)
        if cmd_start_int == 0 and self._prev_cmd_start == 1:
            self._run_latched = False
            self.handler._done_latched = False
            print(f"  ST2_FixedSimRuntime: cmd_start dropped, clearing done latch")
        
        # Stop command
        if cmd_stop:
            self._run_latched = False
            self.handler.stop_cycle()
        
        self._prev_cmd_start = cmd_start_int
        
        # Start cycle if latched and not busy and no fault
        if self._run_latched and not self.handler._busy and not self.handler._fault:
            self.handler.start_cycle(self.recipe_id)

    def step(self, dt_s: float):
        if dt_s is None or dt_s <= 0:
            return
        
        # Step SimPy ONLY if busy OR in fault state (repair in progress) OR run_latched
        should_step = self.handler._busy or self.handler._fault or self._run_latched
        if should_step:
            old_time = self.env.now
            target_time = self.env.now + float(dt_s)
            self.env.run(until=target_time)
            if self.handler._busy or self.handler._fault:
                print(f"  ST2_FixedSimRuntime: Time advanced {old_time:.3f}s -> {self.env.now:.3f}s, "
                      f"busy={self.handler._busy}, fault={self.handler._fault}, done_latched={self.handler._done_latched}")
        else:
            print(f"  ST2_FixedSimRuntime: NOT stepping - idle state (busy={self.handler._busy}, fault={self.handler._fault})")

    def get_outputs(self):
        # Ready = not busy AND not fault AND run_latched is False (idle)
        ready_signal = 1 if (not self.handler._busy and not self.handler._fault and not self._run_latched) else 0
        
        # Done is latched until PLC clears cmd_start
        done_signal = 1 if self.handler._done_latched else 0
        
        out = (
            ready_signal,
            1 if self.handler._busy else 0,
            1 if self.handler._fault else 0,  # Now reflects actual fault state
            done_signal,
            self.handler._last_cycle_time_ms,
            self.handler._completed,
            self.handler._scrapped,
            self.handler._reworks,
            self.handler._cycle_time_avg_s
        )
        return out

    def export_kpis(self, total_sim_time_s: float) -> dict:
        """Export structured KPIs for optimizer (Week 2 deliverable)"""
        utilization = self.handler.get_utilization(total_sim_time_s)
        availability = self.handler.get_availability(total_sim_time_s)
        
        return {
            "station": "S2",
            "completed": self.handler._completed,
            "scrapped": self.handler._scrapped,
            "reworks": self.handler._reworks,
            "total_cycles": self.handler._total_cycles,
            "total_downtime_s": self.handler.get_total_downtime_s(),
            "failure_count": self.handler.get_failure_count(),
            "utilization_pct": utilization,
            "availability_pct": availability,
            "avg_cycle_time_s": self.handler._cycle_time_avg_s,
            "config": {
                "cycle_time_s": self.config["cycle_time_s"],
                "failure_rate": self.config["failure_rate"],
                "mttr_s": self.config["mttr_s"],
                "buffer_capacity": self.config["buffer_capacity"]
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
        self._sim_start_time_ns = 0  # For KPI calculation at end
        # End of user custom code region. Please don't edit beyond this point.

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
            print("ST2: Parameterized SimPy runtime initialized with config from line_config.json")
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
                # Receive on configured port (6002)
                print(f"ST2 attempting to receive on PORT: {PLC_LineCoordinatorSocketPortNumber1}")
                receivedData = vsiEthernetPythonGateway.recvEthernetPacket(PLC_LineCoordinatorSocketPortNumber1)
                if(receivedData[3] != 0):
                    self.decapsulateReceivedData(receivedData)
                # Start of user custom code region. Please apply edits only within these regions:  Before sending the packet
                # Process handshake and simulation stepping AFTER receiving the packet
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
                    (self.mySignals.ready, self.mySignals.busy, self.mySignals.fault,
                     self.mySignals.done, self.mySignals.cycle_time_ms, self.mySignals.completed,
                     self.mySignals.scrapped, self.mySignals.reworks,
                     self.mySignals.cycle_time_avg_s) = self._sim.get_outputs()
                    
                    # Update previous states
                    self._prev_cmd_start = int(self.mySignals.cmd_start)
                    self._prev_cmd_stop = int(self.mySignals.cmd_stop)
                    self._prev_cmd_reset = int(self.mySignals.cmd_reset)
                    
                    # Log KPIs every 10 cycles for visibility
                    if self._sim.handler._total_cycles > 0 and self._sim.handler._total_cycles % 10 == 0:
                        total_sim_time_s = (vsiCommonPythonApi.getSimulationTimeInNs() - self._sim_start_time_ns) / 1e9
                        utilization = self._sim.handler.get_utilization(total_sim_time_s)
                        availability = self._sim.handler.get_availability(total_sim_time_s)
                        print(f"  📊 ST2 KPIs (cycle #{self._sim.handler._total_cycles}): "
                              f"utilization={utilization:.1f}%, availability={availability:.1f}%, "
                              f"failures={self._sim.handler.get_failure_count()}, "
                              f"downtime={self._sim.handler.get_total_downtime_s():.1f}s")
                # End of user custom code region. Please don't edit beyond this point.
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
                # Show SimPy time if available
                if self._sim is not None:
                    print(f"  SimPy env.now = {self._sim.env.now:.3f}s")
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
                kpi_file = f"ST2_kpis_{int(vsiCommonPythonApi.getSimulationTimeInNs()/1e9)}.json"
                with open(kpi_file, 'w') as f:
                    json.dump(kpis, f, indent=2)
                print(f"\n✅ ST2 KPIs exported to {kpi_file}")
                print(f"   Throughput: {(kpis['completed'] / total_sim_time_s) * 3600:.1f} units/hour")
                print(f"   Utilization: {kpis['utilization_pct']:.1f}%")
                print(f"   Availability: {kpis['availability_pct']:.1f}%")
                print(f"   Failures: {kpis['failure_count']}")
                print(f"   Scrap rate: {(kpis['scrapped'] / max(1, kpis['total_cycles'])) * 100:.1f}%")
            
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
