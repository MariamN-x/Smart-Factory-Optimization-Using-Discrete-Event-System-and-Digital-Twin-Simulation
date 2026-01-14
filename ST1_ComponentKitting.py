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
        self.inventory_ok = 0
        self.any_arm_failed = 0



srcMacAddress = [0x02, 0x00, 0x00, 0x00, 0x00, 0x11]
PLC_LineCoordinatorMacAddress = [0x02, 0x00, 0x00, 0x00, 0x00, 0x01]
srcIpAddress = [10, 10, 0, 11]
PLC_LineCoordinatorIpAddress = [10, 10, 0, 1]

PLC_LineCoordinatorSocketPortNumber0 = 6001

ST1_ComponentKitting0 = 0


# Start of user custom code region. Please apply edits only within these regions:  Global Variables & Definitions

# --- Station 1 (Component Kitting) SimPy model ---
# Simplified to run one cycle per PLC start command
# Uses a fixed cycle time observed from logs: 9597ms = 9.597s

import simpy
import random
import math
from collections import deque


# =====================
# SIMPLIFIED STATION 1 MODEL
# =====================
class SimpleKittingStation:
    """
    Simplified station that runs one kitting cycle per start command.
    - Starts on rising edge of cmd_start
    - Runs for fixed cycle time (9.597s)
    - Pulses done for 1 tick when complete
    - Returns to idle state
    """
    def __init__(self, env: simpy.Environment):
        self.env = env
        self.state = "IDLE"  # IDLE / RUNNING / COMPLETE
        self._busy = False
        self._done_pulse = False
        self._job_proc = None
        self._cycle_time_s = 9.597  # Observed from logs: 9597ms = 9.597s
        
    def start_job(self):
        """Start a new kitting cycle if not already running"""
        if self._job_proc is None and not self._busy:
            print(f"  SimpleKittingStation: Starting job at env.now={self.env.now}")
            self.state = "RUNNING"
            self._busy = True
            self._done_pulse = False
            self._job_proc = self.env.process(self._kit_cycle())
            return True
        return False
    
    def _kit_cycle(self):
        """Run a single kitting cycle"""
        yield self.env.timeout(self._cycle_time_s)
        print(f"  SimpleKittingStation: Job completed at env.now={self.env.now}")
        self._busy = False
        self._done_pulse = True
        self.state = "COMPLETE"
        self._job_proc = None
        
    def stop_job(self):
        """Stop any running job"""
        if self._job_proc is not None:
            self._job_proc.interrupt()
            self._job_proc = None
        self._busy = False
        self._done_pulse = False
        self.state = "IDLE"
        
    def reset(self):
        """Full reset"""
        self.stop_job()
        self.state = "IDLE"
        self._busy = False
        self._done_pulse = False
        self._job_proc = None
        
    def is_busy(self):
        return self._busy
        
    def get_done_pulse(self):
        return self._done_pulse
        
    def clear_done_pulse(self):
        """Clear done pulse after it's been read"""
        was_set = self._done_pulse
        self._done_pulse = False
        return was_set


# =====================
# VSI <-> SimPy Wrapper (SIMPLIFIED)
# =====================
class ST1_SimRuntime:
    def __init__(self):
        self.env = simpy.Environment()
        self.station = SimpleKittingStation(self.env)
        
        self.enabled = False
        self._run_latched = False
        self._prev_enabled = False
        
        # Track done pulse for exactly 1 tick
        self._done_pulse_for_output = False
        self._done_was_set_previous_tick = False
        
        # Context from PLC
        self.batch_id = 0
        self.recipe_id = 0
        
        # Cycle time tracking
        self._cycle_time_ms = 9597  # Fixed from logs
        
    def reset(self):
        self.env = simpy.Environment()
        self.station = SimpleKittingStation(self.env)
        self.enabled = False
        self._run_latched = False
        self._prev_enabled = False
        self._done_pulse_for_output = False
        self._done_was_set_previous_tick = False
        print("  ST1_SimRuntime: Full reset")
        
    def set_enabled(self, enabled: bool):
        self.enabled = bool(enabled)
        
    def set_context(self, batch_id: int, recipe_id: int):
        self.batch_id = int(batch_id)
        self.recipe_id = int(recipe_id)
        
    def step(self, dt_s: float):
        if dt_s is None or dt_s <= 0:
            return
            
        # DEBUG: Print current time and state
        print(f"  ST1_SimRuntime step: env.now={self.env.now:.3f}s, dt_s={dt_s:.6f}s, enabled={self.enabled}, busy={self.station.is_busy()}")
        
        # Handle edge detection for starting jobs
        # Start job on rising edge of enabled (when run_latched becomes true)
        if self.enabled and not self.station.is_busy():
            # Only start if we're enabled and not already running
            self.station.start_job()
        
        # Stop job if disabled
        if not self.enabled and self.station.is_busy():
            self.station.stop_job()
            
        # Advance simulation time
        target_time = self.env.now + float(dt_s)
        self.env.run(until=target_time)
        
        # Handle done pulse timing
        # If station just set done pulse, we need to output it for 1 tick
        station_done = self.station.get_done_pulse()
        
        # Set output done pulse if station says it's done AND we haven't output it yet
        if station_done and not self._done_was_set_previous_tick:
            self._done_pulse_for_output = True
            self._done_was_set_previous_tick = True
            print(f"  ST1_SimRuntime: Setting done pulse for output at env.now={self.env.now:.3f}s")
        elif not station_done:
            # Clear tracking if station is no longer in done state
            self._done_was_set_previous_tick = False
            
        # DEBUG: Print state after step
        if self.station.is_busy():
            print(f"  ST1_SimRuntime: Still running, progress: {self.env.now:.3f}s")
        
    def outputs(self):
        # Map internal state to VSI signals
        busy = 1 if self.station.is_busy() else 0
        fault = 0  # No faults in simplified model
        inventory_ok = 1  # Always OK in simplified model
        any_arm_failed = 0  # No failures in simplified model
        
        # Ready = not busy AND not fault AND enabled (PLC wants to see ready=1 when idle)
        ready = 1 if (not busy and not fault and self.enabled) else 0
        
        # Done pulse for exactly 1 tick
        done = 1 if self._done_pulse_for_output else 0
        
        # Clear done pulse after reading (for next tick)
        self._done_pulse_for_output = False
        
        # Fixed cycle time from logs
        cycle_time_ms = self._cycle_time_ms
        
        return ready, busy, fault, done, cycle_time_ms, inventory_ok, any_arm_failed
        
    def result_snapshot(self):
        return {
            "completed_count": 0,
            "last_cycle_time_ms": self._cycle_time_ms,
            "inventory_state": "READY",
            "arm_state": "IDLE" if not self.station.is_busy() else "RUNNING",
            "batch_id": self.batch_id,
            "recipe_id": self.recipe_id,
        }

# End of user custom code region. Please don't edit beyond this point.
class ST1_ComponentKitting:

    def __init__(self, args):
        self.componentId = 1
        self.localHost = args.server_url
        self.domain = args.domain
        self.portNum = 50102

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
            "completed_count": 0,
            "last_cycle_time_ms": 0,
            "inventory_state": "",
            "arm_state": "",
            "batch_id": 0,
            "recipe_id": 0,
        }

        # Aggregated KPIs in VSI mainThread
        self.total_completed = 0
        self.total_cycle_time_ms = 0

# End of user custom code region. Please don't edit beyond this point.



    def mainThread(self):
        dSession = vsiCommonPythonApi.connectToServer(self.localHost, self.domain, self.portNum, self.componentId)
        vsiEthernetPythonGateway.initialize(dSession, self.componentId, bytes(srcMacAddress), bytes(srcIpAddress))
        try:
            vsiCommonPythonApi.waitForReset()

            # Start of user custom code region. Please apply edits only within these regions:  After Reset
            self._sim = ST1_SimRuntime()
            self._run_latched = False
            self._prev_cmd_start = 0
            self._prev_cmd_stop = 0
            self._prev_cmd_reset = 0
            print("ST1: SimPy runtime initialized")

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

                # Receive on configured port (6001)
                print(f"ST1 attempting to receive on PORT: {PLC_LineCoordinatorSocketPortNumber0}")
                receivedData = vsiEthernetPythonGateway.recvEthernetPacket(PLC_LineCoordinatorSocketPortNumber0)
                
                # DEBUG: Instrument receive path
                print(f"ST1 RX meta dest/src/len: {receivedData[0]}, {receivedData[1]}, {receivedData[3]}")
                
                if(receivedData[3] != 0):
                    self.decapsulateReceivedData(receivedData)

                # Start of user custom code region. Please apply edits only within these regions:  Before sending the packet

                # Process edge detection and SimPy stepping AFTER receiving the packet
                # This ensures we use FRESH inputs from PLC, not stale data from previous cycle
                
                # Edge detect start/stop/reset (latch run state) using FRESH inputs
                if self.mySignals.cmd_reset and not self._prev_cmd_reset:
                    print("ST1: RESET command detected (rising edge)")
                    self._run_latched = False
                    if self._sim is not None:
                        self._sim.reset()

                if self.mySignals.cmd_start and not self._prev_cmd_start:
                    print("ST1: START command detected (rising edge)")
                    self._run_latched = True
                    print(f"ST1: run_latched set to True")

                if self.mySignals.cmd_stop and not self._prev_cmd_stop:
                    print("ST1: STOP command detected (rising edge)")
                    self._run_latched = False
                    print(f"ST1: run_latched set to False")

                self._prev_cmd_start = int(self.mySignals.cmd_start)
                self._prev_cmd_stop = int(self.mySignals.cmd_stop)
                self._prev_cmd_reset = int(self.mySignals.cmd_reset)

                # Step SimPy using VSI simulationStep (ns -> s)
                dt_s = float(self.simulationStep) / 1e9 if self.simulationStep else 0.0

                if self._sim is not None:
                    # Set enabled state based on latched run state
                    self._sim.set_enabled(self._run_latched)
                    
                    # Set recipe/batch context from PLC (FRESH data)
                    self._sim.set_context(self.mySignals.batch_id, self.mySignals.recipe_id)

                    # Advance SimPy simulation by dt_s
                    self._sim.step(dt_s)
                    
                    # Get outputs from SimPy
                    ready, busy, fault, done, cycle_time_ms, inventory_ok, any_arm_failed = self._sim.outputs()

                    # Copy SimPy outputs into VSI signals (sent to PLC in this SAME cycle)
                    self.mySignals.ready = int(ready)
                    self.mySignals.busy = int(busy)
                    self.mySignals.fault = int(fault)
                    self.mySignals.done = int(done)
                    self.mySignals.cycle_time_ms = int(cycle_time_ms)
                    self.mySignals.inventory_ok = int(inventory_ok)
                    self.mySignals.any_arm_failed = int(any_arm_failed)

                    # Always capture latest SimPy snapshot into mainThread variables
                    snap = self._sim.result_snapshot()
                    self.last_result["completed_count"] = int(snap.get("completed_count", 0))
                    self.last_result["last_cycle_time_ms"] = int(snap.get("last_cycle_time_ms", 0))
                    self.last_result["inventory_state"] = str(snap.get("inventory_state", ""))
                    self.last_result["arm_state"] = str(snap.get("arm_state", ""))
                    self.last_result["batch_id"] = int(self.mySignals.batch_id)
                    self.last_result["recipe_id"] = int(self.mySignals.recipe_id)

                    # Count completions
                    if self.mySignals.done == 1:
                        self.total_completed += 1
                        self.total_cycle_time_ms += int(self.mySignals.cycle_time_ms)
                        print(f"ST1: Kitting cycle completed! Total completions: {self.total_completed}")

                # End of user custom code region. Please don't edit beyond this point.

                #Send ethernet packet to PLC_LineCoordinator
                self.sendEthernetPacketToPLC_LineCoordinator()

                # Start of user custom code region. Please apply edits only within these regions:  After sending the packet

                # End of user custom code region. Please don't edit beyond this point.

                print("\n+=ST1_ComponentKitting+=")
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
                print("\tinventory_ok =", end = " ")
                print(self.mySignals.inventory_ok)
                print("\tany_arm_failed =", end = " ")
                print(self.mySignals.any_arm_failed)
                
                # Debug output
                print("  Internal state:")
                print(f"\trun_latched = {self._run_latched}")
                print(f"\ttotal_completed = {self.total_completed}")

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

            # Print summary captured in mainThread
            avg_ms = (self.total_cycle_time_ms / float(self.total_completed)) if self.total_completed > 0 else 0.0
            print("=== ST1 SUMMARY (mainThread) ===")
            print("total_completed =", self.total_completed)
            print("avg_cycle_time_ms =", avg_ms)

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
        if(self.clientPortNum[ST1_ComponentKitting0] == 0):
            self.clientPortNum[ST1_ComponentKitting0] = vsiEthernetPythonGateway.tcpConnect(bytes(PLC_LineCoordinatorIpAddress), PLC_LineCoordinatorSocketPortNumber0)
            print(f"ST1 tcpConnect handle: {self.clientPortNum[ST1_ComponentKitting0]}")  # Keep for debugging

        if(self.clientPortNum[ST1_ComponentKitting0] == 0):
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

        # DEBUG: Print what we received
        print(f"ST1 decapsulate: destPort={self.receivedDestPortNumber}, srcPort={self.receivedSrcPortNumber}, len={self.receivedNumberOfBytes}")
        
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
            
            print(f"ST1 decoded PLC command: cmd_start={self.mySignals.cmd_start}, cmd_stop={self.mySignals.cmd_stop}, "
                  f"cmd_reset={self.mySignals.cmd_reset}, batch_id={self.mySignals.batch_id}, "
                  f"recipe_id={self.mySignals.recipe_id}")
        elif self.receivedNumberOfBytes > 0:
            print(f"ST1 ignoring packet: wrong size ({self.receivedNumberOfBytes} bytes, expected 9)")
        else:
            print("ST1 received empty packet (len=0)")


    def sendEthernetPacketToPLC_LineCoordinator(self):
        bytesToSend = bytes()

        bytesToSend += self.packBytes('?', self.mySignals.ready)

        bytesToSend += self.packBytes('?', self.mySignals.busy)

        bytesToSend += self.packBytes('?', self.mySignals.fault)

        bytesToSend += self.packBytes('?', self.mySignals.done)

        bytesToSend += self.packBytes('L', self.mySignals.cycle_time_ms)

        bytesToSend += self.packBytes('?', self.mySignals.inventory_ok)

        bytesToSend += self.packBytes('?', self.mySignals.any_arm_failed)

        #Send ethernet packet to PLC_LineCoordinator
        print(f"ST1 sending to PLC on port: {PLC_LineCoordinatorSocketPortNumber0}")
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

    sT1_ComponentKitting = ST1_ComponentKitting(args)
    sT1_ComponentKitting.mainThread()



if __name__ == '__main__':
    main()
