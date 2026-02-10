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

# --- Station 4 (Calibration + Test Chamber) PARAMETERIZED SimPy model ---
class ST4_SimRuntime:
    def __init__(self):
        # Load config ONCE at simulation start (VSI constraint: no runtime changes)
        self.config = self._load_config()
        print(f"ST4_SimRuntime: Loaded config from line_config.json -> cycle_time_base={self.config['cycle_time_s']}s, "
              f"failure_rate={self.config['failure_rate']}, mttr={self.config['mttr_s']}s")
        
        self.env = simpy.Environment()
        self._process = None
        
        # Handshake state - EXACTLY like ST3
        self._start_latched = False
        self._prev_cmd_start = 0
        self._prev_cmd_stop = 0
        self._prev_cmd_reset = 0
        self._fault_latched = False
        self._done_pulse = False
        
        # Results from last cycle
        self._cycle_time_ms = 0
        
        # Counters (preserve existing functionality)
        self.total = 0
        self.completed = 0
        
        # Context
        self.batch_id = 0
        self.recipe_id = 0
        
        # KPI tracking (NEW for Week 2)
        self.completed_cycles = 0
        self.total_downtime_s = 0.0      # Accumulated downtime from failures
        self.total_busy_time_s = 0.0     # Accumulated productive time
        self.last_busy_start_s = 0.0     # For tracking current busy period
        self.failure_count = 0           # Total failures occurred
        
        # Pass/fail statistics (preserve existing logic)
        self.passed_first_try = 0
        self.passed_after_retry = 0
        self.failed_final = 0

    def _load_config(self) -> dict:
        """Load station parameters from external JSON config (Week 2 deliverable)"""
        config_path = "line_config.json"
        default_config = {
            "cycle_time_s": 41.0,  # Base cycle time (sum of all steps without retry)
            "failure_rate": 0.0,
            "mttr_s": 0.0,
            "buffer_capacity": 2
        }
        
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    full_config = json.load(f)
                    # Extract S4-specific config
                    if "stations" in full_config and "S4" in full_config["stations"]:
                        return full_config["stations"]["S4"]
                    else:
                        print(f"  ⚠️  WARNING: line_config.json missing 'stations.S4' section - using defaults")
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
        print("  ST4_SimRuntime: FULL RESET (reloading config)")
        self.config = self._load_config()  # Reload config for new run
        if self._process is not None:
            self._process.interrupt()
        self.env = simpy.Environment()
        self._process = None
        self._start_latched = False
        self._prev_cmd_start = 0
        self._prev_cmd_stop = 0
        self._prev_cmd_reset = 0
        self._fault_latched = False
        self._done_pulse = False
        self._cycle_time_ms = 0
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
        print("  ST4_SimRuntime: Reset complete - ready for new simulation")

    def set_context(self, batch_id: int, recipe_id: int):
        self.batch_id = int(batch_id)
        self.recipe_id = int(recipe_id)

    def update_handshake(self, cmd_start: int, cmd_stop: int, cmd_reset: int):
        """Process PLC commands - EXACTLY like ST3"""
        # Reset has highest priority
        if cmd_reset and not self._prev_cmd_reset:
            print("ST4: RESET command received (rising edge)")
            self.reset()
            self._prev_cmd_reset = 1
            return
        self._prev_cmd_reset = int(cmd_reset)
        
        # Rising edge detection for start - ONLY if not already busy
        start_edge = (cmd_start == 1 and self._prev_cmd_start == 0)
        
        # Stop command (rising edge) - immediate stop
        if cmd_stop and not self._prev_cmd_stop:
            print("ST4: STOP command received (rising edge)")
            self._start_latched = False
            if self._process is not None:
                self._process.interrupt()
                self._process = None
            self._prev_cmd_stop = int(cmd_stop)
        
        # Start logic: ONLY on rising edge AND station idle AND no fault
        if start_edge:
            if not self._busy() and not self._fault_latched:
                print("ST4: START rising edge detected -> starting cycle")
                self._start_latched = True
                self._done_pulse = False
                self.total += 1
                # Start tracking busy time
                self.last_busy_start_s = self.env.now
                self._process = self.env.process(self._cycle())
            else:
                fault_status = "FAULT" if self._fault_latched else "BUSY"
                print(f"ST4: START rising edge but station {fault_status} - ignoring")
        
        # Keep start_latched during entire cycle execution
        self._prev_cmd_start = int(cmd_start)
        
        # Safety check: if process is alive but start_latched is False, fix it
        if self._busy() and not self._start_latched:
            print("  ERROR: ST4_SimRuntime: process alive but start_latched=False! Fixing...")
            self._start_latched = True

    def _busy(self):
        """Check if there's an active SimPy process running"""
        return self._process is not None and self._process.is_alive

    def _cycle(self):
        """SimPy process for ST4 cycle - parameterized with failure simulation"""
        try:
            start_time = self.env.now
            print(f"ST4: Cycle starting at {start_time:.3f}s")
            
            # STEP 1: Motion (scaled to 2.0s nominal)
            motion_s = self.config["cycle_time_s"] * (2.0 / 41.0)
            yield self.env.timeout(motion_s)
            print(f"ST4: Motion completed at {self.env.now:.3f}s")
            
            # STEP 2: Thermal stabilization (scaled to 18.0s nominal)
            thermal_s = self.config["cycle_time_s"] * (18.0 / 41.0)
            yield self.env.timeout(thermal_s)
            print(f"ST4: Thermal stabilization completed at {self.env.now:.3f}s")
            
            # STEP 3: Calibration (scaled to 6.0s nominal)
            calibration_s = self.config["cycle_time_s"] * (6.0 / 41.0)
            yield self.env.timeout(calibration_s)
            print(f"ST4: Calibration completed at {self.env.now:.3f}s")
            
            # STEP 4: Test printing (scaled to 15.0s nominal)
            testprint_s = self.config["cycle_time_s"] * (15.0 / 41.0)
            yield self.env.timeout(testprint_s)
            print(f"ST4: Test printing completed at {self.env.now:.3f}s")
            
            # STEP 5: Check for catastrophic failure AFTER processing completes (realistic model)
            if random.random() < self.config["failure_rate"]:
                # Failure occurred - simulate downtime
                self.failure_count += 1
                failure_start = self.env.now
                
                # Accumulate busy time BEFORE failure
                if self.last_busy_start_s > 0:
                    self.total_busy_time_s += (failure_start - self.last_busy_start_s)
                    self.last_busy_start_s = 0.0
                
                # Enter fault state
                self._fault_latched = True
                print(f"  ⚠️  FAILURE at {failure_start:.3f}s - catastrophic event, part lost")
                
                # Simulate repair time (MTTR)
                yield self.env.timeout(self.config["mttr_s"])
                
                # Repair complete
                repair_end = self.env.now
                downtime = repair_end - failure_start
                self.total_downtime_s += downtime
                print(f"  ✅ REPAIR complete at {repair_end:.3f}s (downtime={downtime:.2f}s)")
                
                # Exit fault state (reset happens externally via PLC)
                # DO NOT proceed to pass/fail checks - part is lost during failure
                self._done_pulse = False
                return
            
            # STEP 6: Pass/fail decision (preserve existing logic)
            passed = (random.random() <= 0.93)
            print(f"ST4: First test {'PASSED' if passed else 'FAILED'}")
            
            # If fail, retry once
            if not passed:
                # Retry time (scaled to 5.0s nominal)
                retry_s = self.config["cycle_time_s"] * (5.0 / 41.0)
                yield self.env.timeout(retry_s)
                print(f"ST4: Retry completed at {self.env.now:.3f}s")
                passed = (random.random() <= 0.97)
                print(f"ST4: Retry test {'PASSED' if passed else 'FAILED'}")
            
            end_time = self.env.now
            self._cycle_time_ms = int((end_time - start_time) * 1000)
            
            # Determine final status
            if not passed:
                self._fault_latched = True
                self._done_pulse = False
                self.failed_final += 1
                print(f"ST4: Cycle FAILED after {self._cycle_time_ms}ms")
            else:
                self.completed += 1
                self.completed_cycles += 1
                self._done_pulse = True
                if random.random() <= 0.93:
                    self.passed_first_try += 1
                else:
                    self.passed_after_retry += 1
                print(f"ST4: Cycle completed successfully in {self._cycle_time_ms}ms, completed={self.completed}")
            
            # Accumulate busy time for successful/quality-failed cycles
            if self.last_busy_start_s > 0:
                self.total_busy_time_s += (end_time - self.last_busy_start_s)
                self.last_busy_start_s = 0.0
                
        except simpy.Interrupt:
            print("ST4: Cycle interrupted")
            self._done_pulse = False
            # Accumulate partial busy time on interrupt
            if self.last_busy_start_s > 0:
                self.total_busy_time_s += (self.env.now - self.last_busy_start_s)
                self.last_busy_start_s = 0.0
        finally:
            self._process = None

    def step(self, dt_s: float):
        """Advance simulation ONLY when necessary"""
        should_step = self._busy() or self._start_latched
        if self._prev_cmd_stop and not self._busy():
            return
        if should_step and dt_s > 0:
            target_time = self.env.now + float(dt_s)
            self.env.run(until=target_time)
        # Check for cycle completion and clear start_latched
        if self._done_pulse and not self._busy():
            self._start_latched = False

    def outputs(self):
        """Get output signals - EXACTLY like ST3 logic"""
        busy = 1 if self._busy() else 0
        fault = 1 if self._fault_latched else 0
        # Ready logic: NOT busy, NOT fault, and NOT in the middle of a cycle
        ready = 1 if (not busy and not fault and not self._start_latched and not self._done_pulse) else 0
        done = 1 if self._done_pulse else 0
        # Clear done pulse after reading (one-shot)
        if self._done_pulse and not self._busy():
            self._done_pulse = False
            self._start_latched = False
        return ready, busy, fault, done, self._cycle_time_ms, self.total, self.completed

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

    def export_kpis(self, total_sim_time_s: float) -> dict:
        """Export structured KPIs for optimizer (Week 2 deliverable)"""
        utilization = self.get_utilization(total_sim_time_s)
        availability = self.get_availability(total_sim_time_s)
        
        return {
            "station": "S4",
            "total": self.total,
            "completed": self.completed,
            "completed_cycles": self.completed_cycles,
            "passed_first_try": self.passed_first_try,
            "passed_after_retry": self.passed_after_retry,
            "failed_final": self.failed_final,
            "total_downtime_s": self.total_downtime_s,
            "failure_count": self.failure_count,
            "utilization_pct": utilization,
            "availability_pct": availability,
            "first_pass_yield": (self.passed_first_try / max(1, self.completed_cycles)) * 100.0,
            "overall_yield": (self.completed / max(1, self.total)) * 100.0,
            "config": {
                "cycle_time_s": self.config["cycle_time_s"],
                "failure_rate": self.config["failure_rate"],
                "mttr_s": self.config["mttr_s"],
                "buffer_capacity": self.config["buffer_capacity"]
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
        self._sim_start_time_ns = 0  # For KPI calculation at end
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
                    
                    # Get outputs from SimPy
                    (ready, busy, fault, done, cycle_time_ms,
                     total, completed) = self._sim.outputs()
                    
                    # Copy SimPy outputs into VSI signals
                    self.mySignals.ready = int(ready)
                    self.mySignals.busy = int(busy)
                    self.mySignals.fault = int(fault)
                    self.mySignals.done = int(done)
                    self.mySignals.cycle_time_ms = int(cycle_time_ms)
                    self.mySignals.total = int(total)
                    self.mySignals.completed = int(completed)
                    
                    # Log KPIs every 5 cycles for visibility
                    if self._sim.completed_cycles > 0 and self._sim.completed_cycles % 5 == 0:
                        total_sim_time_s = (vsiCommonPythonApi.getSimulationTimeInNs() - self._sim_start_time_ns) / 1e9
                        utilization = self._sim.get_utilization(total_sim_time_s)
                        availability = self._sim.get_availability(total_sim_time_s)
                        print(f"  📊 ST4 KPIs (cycle #{self._sim.completed_cycles}): "
                              f"utilization={utilization:.1f}%, availability={availability:.1f}%, "
                              f"failures={self._sim.failure_count}, downtime={self._sim.total_downtime_s:.1f}s")
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
                kpi_file = f"ST4_kpis_{int(vsiCommonPythonApi.getSimulationTimeInNs()/1e9)}.json"
                with open(kpi_file, 'w') as f:
                    json.dump(kpis, f, indent=2)
                print(f"\n✅ ST4 KPIs exported to {kpi_file}")
                print(f"   Throughput: {(kpis['completed'] / total_sim_time_s) * 3600:.1f} units/hour")
                print(f"   Utilization: {kpis['utilization_pct']:.1f}%")
                print(f"   Availability: {kpis['availability_pct']:.1f}%")
                print(f"   Failures: {kpis['failure_count']}")
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
