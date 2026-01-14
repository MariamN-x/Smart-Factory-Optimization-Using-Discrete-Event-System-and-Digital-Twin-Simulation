#!/usr/bin/env python3
from __future__ import print_function
import struct
import sys
import argparse
import math

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
import random
import math


# =====================
# STATION 2 HANDLING WITH CLEAN HANDSHAKE
# =====================
class ST2_CycleHandler:
    """
    Handles frame core assembly cycles with clean handshake (similar to ST1).
    - Starts on rising edge of cmd_start ONLY
    - Runs for variable cycle time (with jitter)
    - Pulses done for exactly 1 tick when complete
    - Auto-clears run_latched after completion
    - Tracks completed, scrapped, reworks
    """
    def __init__(self, env: simpy.Environment):
        self.env = env
        self.state = "IDLE"  # IDLE, RUNNING, COMPLETE, SCRAPPED, REWORK
        self._cycle_proc = None
        self._current_cycle_time_s = 12.0  # Base cycle time for frame assembly
        self._cycle_time_jitter = 0.15  # ±15%
        
        # Output tracking
        self._busy = False
        self._done_pulse = False
        self._ready = True  # Initially ready
        self._fault = False
        
        # Cycle timing
        self._start_time_s = 0
        self._last_cycle_time_s = 12.0
        self._last_cycle_time_ms = 12000
        
        # Counters
        self._completed = 0
        self._scrapped = 0
        self._reworks = 0
        self._total_cycles = 0
        self._cycle_time_avg_s = 0.0
        
        # Probabilities
        self._scrap_prob = 0.02  # 2% scrap chance
        self._rework_prob = 0.05  # 5% rework chance
        
    def reset(self):
        """Reset to initial state"""
        if self._cycle_proc is not None:
            self._cycle_proc.interrupt()
        self.state = "IDLE"
        self._busy = False
        self._done_pulse = False
        self._ready = True
        self._fault = False
        self._cycle_proc = None
        self._completed = 0
        self._scrapped = 0
        self._reworks = 0
        self._total_cycles = 0
        self._cycle_time_avg_s = 0.0
        print("  ST2_CycleHandler: Reset complete")
        
    def start_cycle(self, recipe_id: int):
        """Start a new assembly cycle if idle"""
        if self.state != "IDLE" or self._busy:
            print(f"  ST2_CycleHandler: Cannot start, state={self.state}, busy={self._busy}")
            return False
            
        print(f"  ST2_CycleHandler: Starting new assembly cycle at env.now={self.env.now:.3f}s")
        
        # Calculate cycle time with jitter based on recipe
        base_time = self._get_base_cycle_time(recipe_id)
        jitter_factor = 1.0 + random.uniform(-self._cycle_time_jitter, self._cycle_time_jitter)
        self._current_cycle_time_s = base_time * jitter_factor
        self._current_cycle_time_s = max(2.0, self._current_cycle_time_s)  # Minimum 2 seconds
        
        print(f"  ST2_CycleHandler: Cycle time = {self._current_cycle_time_s:.3f}s (base={base_time:.3f}s, jitter={jitter_factor:.3f})")
        
        # Update state
        self.state = "RUNNING"
        self._busy = True
        self._ready = False
        self._done_pulse = False
        self._start_time_s = self.env.now
        
        # Start the cycle process
        self._cycle_proc = self.env.process(self._run_cycle())
        return True
        
    def _get_base_cycle_time(self, recipe_id: int) -> float:
        """Get base cycle time based on recipe"""
        if recipe_id == 1:
            return 14.0  # Longer for complex recipe
        else:
            return 12.0  # Standard frame assembly
            
    def _run_cycle(self):
        """Run a single assembly cycle - MUST BE A GENERATOR"""
        try:
            # Simulate cycle duration with yield
            yield self.env.timeout(self._current_cycle_time_s)
            
            # Determine outcome
            r = random.random()
            self._total_cycles += 1
            
            if r < self._scrap_prob:
                # Scrapped
                self.state = "SCRAPPED"
                self._scrapped += 1
                print(f"  ST2_CycleHandler: Cycle SCRAPPED at env.now={self.env.now:.3f}s")
            elif r < self._scrap_prob + self._rework_prob:
                # Rework needed
                self.state = "REWORK"
                self._reworks += 1
                print(f"  ST2_CycleHandler: Cycle requires REWORK at env.now={self.env.now:.3f}s")
            else:
                # Successfully completed
                self.state = "COMPLETE"
                self._completed += 1
                print(f"  ST2_CycleHandler: Cycle COMPLETED at env.now={self.env.now:.3f}s")
            
            # Calculate cycle time
            self._last_cycle_time_s = self.env.now - self._start_time_s
            self._last_cycle_time_ms = int(self._last_cycle_time_s * 1000)
            
            # Update running average
            if self._total_cycles == 1:
                self._cycle_time_avg_s = self._last_cycle_time_s
            else:
                self._cycle_time_avg_s = (self._cycle_time_avg_s * (self._total_cycles - 1) + self._last_cycle_time_s) / self._total_cycles
            
            print(f"  ST2_CycleHandler: Cycle time = {self._last_cycle_time_s:.3f}s, Avg = {self._cycle_time_avg_s:.3f}s")
            
            # Set done pulse (regardless of outcome)
            self._done_pulse = True
            self._busy = False
            self._ready = True
            
            # Clear the process reference
            self._cycle_proc = None
            
        except simpy.Interrupt:
            print("  ST2_CycleHandler: Cycle interrupted")
            self.state = "IDLE"
            self._busy = False
            self._ready = True
            self._done_pulse = False
            self._cycle_proc = None
            
    def stop_cycle(self):
        """Stop any running cycle"""
        if self._cycle_proc is not None:
            self._cycle_proc.interrupt()
        self.state = "IDLE"
        self._busy = False
        self._ready = True
        self._done_pulse = False
        self._cycle_proc = None
        
    def step(self, dt_s: float):
        """Advance simulation time"""
        if dt_s <= 0:
            return
            
        # DEBUG: Print progress if running
        if self.state == "RUNNING":
            progress = (self.env.now - self._start_time_s) / self._current_cycle_time_s * 100
            print(f"  ST2_CycleHandler: Running, env.now={self.env.now:.3f}s, progress={progress:.1f}%")
        
        # Advance simulation - MUST USE env.run(until=...)
        target_time = self.env.now + dt_s
        self.env.run(until=target_time)
        
    def get_outputs(self):
        """Get current outputs for PLC"""
        # Note: done_pulse is cleared after being read
        done_output = self._done_pulse
        if self._done_pulse:
            print(f"  ST2_CycleHandler: Outputting done pulse at env.now={self.env.now:.3f}s")
            self._done_pulse = False  # Clear after outputting
            # Auto-clear state after outputting done
            if self.state in ["COMPLETE", "SCRAPPED", "REWORK"]:
                self.state = "IDLE"
                
        return {
            "ready": 1 if self._ready and not self._fault else 0,
            "busy": 1 if self._busy else 0,
            "fault": 1 if self._fault else 0,
            "done": 1 if done_output else 0,
            "cycle_time_ms": self._last_cycle_time_ms,
            "completed": self._completed,
            "scrapped": self._scrapped,
            "reworks": self._reworks,
            "cycle_time_avg_s": self._cycle_time_avg_s,
        }


# =====================
# VSI <-> SimPy Wrapper with Clean Handshake
# =====================
class ST2_SimRuntime:
    def __init__(self):
        self.env = simpy.Environment()
        self.cycle_handler = ST2_CycleHandler(self.env)
        
        # Handshake state
        self._run_latched = False  # Latched from PLC start pulse
        self._prev_cmd_start = 0  # For edge detection
        
        # Context
        self.batch_id = 0
        self.recipe_id = 0
        
        # Debug counters
        self._cycles_started = 0
        
    def reset(self):
        """Full reset"""
        self.env = simpy.Environment()
        self.cycle_handler = ST2_CycleHandler(self.env)
        self._run_latched = False
        self._prev_cmd_start = 0
        self._cycles_started = 0
        print("  ST2_SimRuntime: Full reset")
        
    def process_plc_command(self, cmd_start: int, cmd_stop: int, cmd_reset: int, 
                           batch_id: int, recipe_id: int):
        """Process PLC command with edge detection"""
        # Update context
        self.batch_id = int(batch_id)
        self.recipe_id = int(recipe_id)
        
        # Handle reset
        if cmd_reset:
            print("  ST2_SimRuntime: Reset command received")
            self.reset()
            return
            
        # Handle stop
        if cmd_stop:
            print("  ST2_SimRuntime: Stop command received")
            self.cycle_handler.stop_cycle()
            self._run_latched = False
            self._prev_cmd_start = 0
            return
            
        # Detect rising edge of cmd_start (ONE-SCAN PULSE)
        if cmd_start and not self._prev_cmd_start:
            print(f"  ST2_SimRuntime: cmd_start rising edge detected (ONE-SCAN PULSE)")
            if not self._run_latched:  # Only latch if not already latched
                self._run_latched = True
                print(f"  ST2_SimRuntime: run_latched set to True")
            else:
                print(f"  ST2_SimRuntime: Already latched, ignoring additional start")
                
        self._prev_cmd_start = cmd_start
        
        # If start signal stays high (not a pulse), ignore it
        # We only respond to the rising edge
        
    def step(self, dt_s: float):
        """Advance simulation by dt_s seconds"""
        if dt_s <= 0:
            return
            
        # DEBUG: Print state
        print(f"  ST2_SimRuntime step: dt_s={dt_s:.6f}s, run_latched={self._run_latched}, env.now={self.env.now:.3f}s")
        
        # If we have a latched start and we're idle, start a cycle
        if self._run_latched and not self.cycle_handler._busy:
            print(f"  ST2_SimRuntime: Starting new assembly cycle (latched start)")
            if self.cycle_handler.start_cycle(self.recipe_id):
                self._cycles_started += 1
                print(f"  ST2_SimRuntime: Cycle {self._cycles_started} started")
                # Clear latch immediately after starting (station now busy)
                self._run_latched = False
                print(f"  ST2_SimRuntime: run_latched cleared (station now busy)")
            else:
                print(f"  ST2_SimRuntime: Failed to start cycle")
                
        # Advance simulation time
        self.cycle_handler.step(dt_s)
        
    def get_outputs(self):
        """Get outputs for PLC"""
        outputs = self.cycle_handler.get_outputs()
        
        # Add debug info
        if outputs["done"]:
            print(f"  ST2_SimRuntime: Sending done pulse to PLC")
            
        return (
            outputs["ready"],
            outputs["busy"],
            outputs["fault"],
            outputs["done"],
            outputs["cycle_time_ms"],
            outputs["completed"],
            outputs["scrapped"],
            outputs["reworks"],
            outputs["cycle_time_avg_s"]
        )
        
    def get_snapshot(self):
        """Get snapshot for debug"""
        return {
            "cycles_started": self._cycles_started,
            "env_now": self.env.now,
            "batch_id": self.batch_id,
            "recipe_id": self.recipe_id,
            "run_latched": self._run_latched,
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
        self._run_latched = False
        self._prev_cmd_start = 0
        self._prev_cmd_stop = 0
        self._prev_cmd_reset = 0

        # Latest SimPy snapshot copied into VSI mainThread (debug / KPIs)
        self.last_result = {
            "cycles_started": 0,
            "env_now": 0,
            "batch_id": 0,
            "recipe_id": 0,
        }

# End of user custom code region. Please don't edit beyond this point.



    def mainThread(self):
        dSession = vsiCommonPythonApi.connectToServer(self.localHost, self.domain, self.portNum, self.componentId)
        vsiEthernetPythonGateway.initialize(dSession, self.componentId, bytes(srcMacAddress), bytes(srcIpAddress))
        try:
            vsiCommonPythonApi.waitForReset()

            # Start of user custom code region. Please apply edits only within these regions:  After Reset
            self._sim = ST2_SimRuntime()
            self._run_latched = False
            self._prev_cmd_start = 0
            self._prev_cmd_stop = 0
            self._prev_cmd_reset = 0
            print("ST2: SimPy runtime initialized with clean handshake")

# End of user custom code region. Please don't edit beyond this point.
            self.updateInternalVariables()

            if(vsiCommonPythonApi.isStopRequested()):
                raise Exception("stopRequested")
            self.establishTcpUdpConnection()
            nextExpectedTime = vsiCommonPythonApi.getSimulationTimeInNs()
            while(vsiCommonPythonApi.getSimulationTimeInNs() < self.totalSimulationTime):

                # Start of user custom code region. Please apply edits only within these regions:  Inside the while loop

                # REMOVED: Moved to "Before sending the packet" region to ensure proper execution order
                # The edge detection and SimPy stepping must happen AFTER receiving the Ethernet packet
                # This ensures fresh PLC inputs are processed immediately

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

                # Receive on configured port (6002)
                print(f"ST2 attempting to receive on PORT: {PLC_LineCoordinatorSocketPortNumber1}")
                receivedData = vsiEthernetPythonGateway.recvEthernetPacket(PLC_LineCoordinatorSocketPortNumber1)
                
                # DEBUG: Instrument receive path
                print(f"ST2 RX meta dest/src/len: {receivedData[0]}, {receivedData[1]}, {receivedData[3]}")
                
                if(receivedData[3] != 0):
                    self.decapsulateReceivedData(receivedData)

                # Start of user custom code region. Please apply edits only within these regions:  Before sending the packet

                # Process PLC commands with SimPy handler
                if self._sim is not None:
                    # Pass fresh PLC inputs to SimPy handler
                    self._sim.process_plc_command(
                        self.mySignals.cmd_start,
                        self.mySignals.cmd_stop,
                        self.mySignals.cmd_reset,
                        self.mySignals.batch_id,
                        self.mySignals.recipe_id
                    )
                    
                    # Step SimPy using VSI simulationStep (ns -> s)
                    dt_s = float(self.simulationStep) / 1e9 if self.simulationStep else 0.0
                    
                    # Advance SimPy simulation by dt_s
                    self._sim.step(dt_s)
                    
                    # Get outputs from SimPy
                    ready, busy, fault, done, cycle_time_ms, completed, scrapped, reworks, cycle_time_avg_s = self._sim.get_outputs()

                    # Copy SimPy outputs into VSI signals (sent to PLC in this SAME cycle)
                    self.mySignals.ready = int(ready)
                    self.mySignals.busy = int(busy)
                    self.mySignals.fault = int(fault)
                    self.mySignals.done = int(done)
                    self.mySignals.cycle_time_ms = int(cycle_time_ms)
                    self.mySignals.completed = int(completed)
                    self.mySignals.scrapped = int(scrapped)
                    self.mySignals.reworks = int(reworks)
                    self.mySignals.cycle_time_avg_s = float(cycle_time_avg_s)

                    # Always capture latest SimPy snapshot into mainThread variables
                    snap = self._sim.get_snapshot()
                    self.last_result["cycles_started"] = int(snap.get("cycles_started", 0))
                    self.last_result["env_now"] = float(snap.get("env_now", 0))
                    self.last_result["batch_id"] = int(self.mySignals.batch_id)
                    self.last_result["recipe_id"] = int(self.mySignals.recipe_id)

                # End of user custom code region. Please don't edit beyond this point.

                #Send ethernet packet to PLC_LineCoordinator
                self.sendEthernetPacketToPLC_LineCoordinator()

                # Start of user custom code region. Please apply edits only within these regions:  After sending the packet

                # End of user custom code region. Please don't edit beyond this point.

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
                
                # Debug output
                print("  Internal state:")
                print(f"\tenv_now = {self.last_result.get('env_now', 0):.3f}s")
                print(f"\tcycles_started = {self.last_result.get('cycles_started', 0)}")

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
        except:
            # Advance time with a step that is equal to "simulationStep + 1" so that all other clients
            # receive the terminate packet before terminating this client
            vsiCommonPythonApi.advanceSimulation(self.simulationStep + 1)



    def establishTcpUdpConnection(self):
        if(self.clientPortNum[ST2_FrameCoreAssembly1] == 0):
            self.clientPortNum[ST2_FrameCoreAssembly1] = vsiEthernetPythonGateway.tcpConnect(bytes(PLC_LineCoordinatorIpAddress), PLC_LineCoordinatorSocketPortNumber1)
            print(f"ST2 tcpConnect handle: {self.clientPortNum[ST2_FrameCoreAssembly1]}")  # Keep for debugging

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

        # DEBUG: Print what we received
        print(f"ST2 decapsulate: destPort={self.receivedDestPortNumber}, srcPort={self.receivedSrcPortNumber}, len={self.receivedNumberOfBytes}")
        
        # Decode PLC command packets when we receive 9 bytes
        if self.receivedNumberOfBytes == 9:
            print("Received 9-byte packet from PLC (command packet)")
            receivedPayload = bytes(self.receivedPayload)
            
            # Decode the 9-byte command packet
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

        #Send ethernet packet to PLC_LineCoordinator
        print(f"ST2 sending to PLC on port: {PLC_LineCoordinatorSocketPortNumber1}")
        vsiEthernetPythonGateway.sendEthernetPacket(PLC_LineCoordinatorSocketPortNumber1, bytes(bytesToSend))

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

    sT2_FrameCoreAssembly = ST2_FrameCoreAssembly(args)
    sT2_FrameCoreAssembly.mainThread()



if __name__ == '__main__':
    main()
