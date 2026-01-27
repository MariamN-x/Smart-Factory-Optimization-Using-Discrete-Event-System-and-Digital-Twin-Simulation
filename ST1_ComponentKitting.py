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

# --- Station 1 (Component Kitting) FIXED handshake model ---
import simpy
import random

class FixedKittingStation:
    """
    Fixed station with proper handshake that keeps start_latched during entire cycle.
    """
    def __init__(self, env: simpy.Environment):
        self.env = env
        self.state = "IDLE"
        self._cycle_proc = None  # Active SimPy process handle
        self._nominal_cycle_time_s = 9.597  # Target/nominal
        
        # State variables
        self._busy = False
        self._fault = False
        self._done_pulse = False
        
        # Cycle timing
        self._cycle_start_s = 0
        self._cycle_end_s = 0
        self._actual_cycle_time_ms = 0
        self._cycle_count = 0
        self._cycle_time_sum_ms = 0
        
        # KPIs
        self.completed_cycles = 0
        
    def start_cycle(self, start_time_s: float):
        """Start a new kitting cycle - ONLY called on cmd_start rising edge"""
        if self._cycle_proc is not None or self._busy:
            print(f"  WARNING: FixedKittingStation.start_cycle called but already busy!")
            return False
            
        print(f"  FixedKittingStation: Starting job at env.now={self.env.now}")
        self.state = "RUNNING"
        self._busy = True
        self._done_pulse = False
        self._cycle_start_s = start_time_s
        self._actual_cycle_time_ms = 0  # Clear until completion
        self._cycle_proc = self.env.process(self._kit_cycle())
        return True
    
    def _kit_cycle(self):
        """Run a single kitting cycle"""
        try:
            yield self.env.timeout(self._nominal_cycle_time_s)
            
            # Cycle completed successfully
            self._cycle_end_s = self.env.now
            actual_time_s = self._cycle_end_s - self._cycle_start_s
            self._actual_cycle_time_ms = int(actual_time_s * 1000)
            
            # Update running average
            self._cycle_count += 1
            self._cycle_time_sum_ms += self._actual_cycle_time_ms
            self.completed_cycles += 1
            
            print(f"  FixedKittingStation: Job completed at env.now={self.env.now}, "
                  f"actual_time={self._actual_cycle_time_ms}ms")
            
            self._busy = False
            self._done_pulse = True  # Pulse for one iteration
            self.state = "COMPLETE"
            
        except simpy.Interrupt:
            # Cycle was stopped
            print("  FixedKittingStation: Cycle interrupted by stop command")
            self._busy = False
            self._done_pulse = False
            self.state = "IDLE"
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
        """Full reset"""
        self.stop_cycle()
        self.state = "IDLE"
        self._busy = False
        self._done_pulse = False
        self._cycle_proc = None
        self._actual_cycle_time_ms = 9597  # Reset to nominal
        self._cycle_count = 0
        self._cycle_time_sum_ms = 0
        self.completed_cycles = 0
        
    def is_busy(self):
        return self._busy
        
    def get_done_pulse(self):
        return self._done_pulse
        
    def clear_done_pulse(self):
        """Clear done pulse after it's been read"""
        was_set = self._done_pulse
        self._done_pulse = False
        return was_set
        
    def get_cycle_time_ms(self):
        return self._actual_cycle_time_ms if self._actual_cycle_time_ms > 0 else 9597
        
    def get_avg_cycle_time_ms(self):
        if self._cycle_count > 0:
            return int(self._cycle_time_sum_ms / self._cycle_count)
        return 9597
        
    def has_active_proc(self):
        """Check if there's an active SimPy process"""
        return self._cycle_proc is not None


# VSI <-> SimPy Wrapper (FIXED - keeps start_latched during cycle)
class ST1_SimRuntime:
    def __init__(self):
        self.env = simpy.Environment()
        self.station = FixedKittingStation(self.env)
        
        # Handshake state - CLEAR on init
        self._start_latched = False  # Set on cmd_start rising edge, cleared on cycle completion
        self._prev_cmd_start = 0
        self._prev_cmd_stop = 0
        self._prev_cmd_reset = 0
        
        # Context from PLC
        self.batch_id = 0
        self.recipe_id = 0
        
        # Debug tracking
        self._last_start_edge = False
        self._last_step_dt = 0.0
        
    def reset(self):
        """Full reset - NO auto-start processes"""
        self.env = simpy.Environment()
        self.station = FixedKittingStation(self.env)
        self._start_latched = False
        self._prev_cmd_start = 0
        self._prev_cmd_stop = 0
        self._prev_cmd_reset = 0
        print("  ST1_SimRuntime: Full reset - NO auto-start")
        
    def set_context(self, batch_id: int, recipe_id: int):
        self.batch_id = int(batch_id)
        self.recipe_id = int(recipe_id)
        
    def update_handshake(self, cmd_start: int, cmd_stop: int, cmd_reset: int):
        """Process PLC commands and update internal state"""
        # Reset has highest priority
        if cmd_reset and not self._prev_cmd_reset:
            print("  ST1_SimRuntime: RESET command (rising edge)")
            self.reset()
            self._prev_cmd_reset = 1
            return
            
        self._prev_cmd_reset = int(cmd_reset)
        
        # Rising edge detection for start
        start_edge = (cmd_start == 1 and self._prev_cmd_start == 0)
        self._last_start_edge = start_edge
        
        # Stop command (rising edge) - immediate stop
        if cmd_stop and not self._prev_cmd_stop:
            print("  ST1_SimRuntime: STOP command (rising edge)")
            self._start_latched = False  # Clear start latch on stop
            self.station.stop_cycle()
            
        self._prev_cmd_stop = int(cmd_stop)
        
        # Start logic: ONLY on rising edge AND station idle AND no fault
        if start_edge:
            if not self.station.is_busy() and not self.station._fault:
                print("  ST1_SimRuntime: START rising edge, station idle - starting cycle")
                self._start_latched = True  # Set and KEEP until cycle completes
                self.station.start_cycle(self.env.now)
            else:
                print(f"  ST1_SimRuntime: START rising edge but station busy={self.station.is_busy()}, fault={self.station._fault} - ignoring")
                
        # *** CRITICAL FIX: DO NOT clear start_latched when cmd_start drops ***
        # PLC pulses start, but we keep start_latched=True for entire cycle
        # Commenting out the problematic code:
        # if cmd_start == 0 and self._prev_cmd_start == 1:
        #     print("  ST1_SimRuntime: cmd_start dropped to 0, clearing start_latch")
        #     self._start_latched = False
            
        self._prev_cmd_start = int(cmd_start)
        
        # Safety check: if station is busy but start_latched is False, fix it
        if self.station.is_busy() and not self._start_latched:
            print("  ERROR: ST1_SimRuntime: station busy but start_latched=False! Fixing...")
            self._start_latched = True
            
        # Safety check: if station has active process but busy flag is False, fix it
        if self.station.has_active_proc() and not self.station.is_busy():
            print("  ERROR: ST1_SimRuntime: active process but busy=False! Fixing...")
            self.station._busy = True
        
    def step(self, dt_s: float):
        """Advance simulation ONLY when necessary"""
        self._last_step_dt = dt_s
        
        # Step SimPy ONLY if busy==True OR start_latched==True
        should_step = self.station.is_busy() or self._start_latched
        
        print(f"  ST1_SimRuntime step: env.now={self.env.now:.3f}s, dt_s={dt_s:.6f}s, "
              f"start_latched={self._start_latched}, busy={self.station.is_busy()}, "
              f"has_proc={self.station.has_active_proc()}, should_step={should_step}")
        
        # DO NOT step if stop command is active and we're not in a cycle
        if self._prev_cmd_stop and not self.station.is_busy():
            print(f"  ST1_SimRuntime: NOT stepping - stop command active and not busy")
            return
            
        # Only step if we should step
        if should_step and dt_s > 0:
            target_time = self.env.now + float(dt_s)
            self.env.run(until=target_time)
            print(f"  ST1_SimRuntime: Stepped to env.now={self.env.now:.3f}s")
            
            # Check for cycle completion and clear start_latched
            if self.station.get_done_pulse():
                print("  ST1_SimRuntime: Cycle completed, clearing start_latched")
                self._start_latched = False
        
    def outputs(self):
        # Get station state
        busy = 1 if self.station.is_busy() else 0
        fault = 0  # No faults in this model
        inventory_ok = 1
        any_arm_failed = 0
        
        # Ready = not busy AND not fault (independent of start latch)
        ready = 1 if (not busy and not fault) else 0
        
        # Done pulse for exactly ONE iteration after completion
        done = 1 if self.station.get_done_pulse() else 0
        
        # Real cycle time (0 if cycle hasn't completed yet)
        cycle_time_ms = self.station.get_cycle_time_ms()
        
        return ready, busy, fault, done, cycle_time_ms, inventory_ok, any_arm_failed

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
        self._prev_done = 0  # For tracking done transitions
        self.total_completed = 0

# End of user custom code region. Please don't edit beyond this point.



    def mainThread(self):
        dSession = vsiCommonPythonApi.connectToServer(self.localHost, self.domain, self.portNum, self.componentId)
        vsiEthernetPythonGateway.initialize(dSession, self.componentId, bytes(srcMacAddress), bytes(srcIpAddress))
        try:
            vsiCommonPythonApi.waitForReset()

            # Start of user custom code region. Please apply edits only within these regions:  After Reset
            self._sim = ST1_SimRuntime()
            self._prev_done = 0
            self.total_completed = 0
            print("ST1: Fixed SimPy runtime initialized - NO auto-start")

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

                # Receive on configured port (6001)
                print(f"ST1 attempting to receive on PORT: {PLC_LineCoordinatorSocketPortNumber0}")
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
                    
                    # Get outputs from SimPy
                    ready, busy, fault, done, cycle_time_ms, inventory_ok, any_arm_failed = self._sim.outputs()

                    # Copy SimPy outputs into VSI signals
                    self.mySignals.ready = int(ready)
                    self.mySignals.busy = int(busy)
                    self.mySignals.fault = int(fault)
                    self.mySignals.done = int(done)
                    self.mySignals.cycle_time_ms = int(cycle_time_ms)
                    self.mySignals.inventory_ok = int(inventory_ok)
                    self.mySignals.any_arm_failed = int(any_arm_failed)
                    
                    # Track completions
                    if done and not self._prev_done:
                        self.total_completed += 1
                        print(f"ST1: Cycle completed! cycle_time={cycle_time_ms}ms, total={self.total_completed}")

                # Update previous done state
                self._prev_done = int(self.mySignals.done)

                # End of user custom code region. Please don't edit beyond this point.

                #Send ethernet packet to PLC_LineCoordinator
                self.sendEthernetPacketToPLC_LineCoordinator()

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
                print(f"  Internal: total_completed={self.total_completed}")
                if self._sim is not None:
                    print(f"  SimState: start_latched={self._sim._start_latched}")
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
                vsiCommonPythonApi.advanceSimulation(self.simulationStep + 1)
            else:
                print(f"An error occurred: {str(e)}")
        except:
            vsiCommonPythonApi.advanceSimulation(self.simulationStep + 1)



    def establishTcpUdpConnection(self):
        if(self.clientPortNum[ST1_ComponentKitting0] == 0):
            self.clientPortNum[ST1_ComponentKitting0] = vsiEthernetPythonGateway.tcpConnect(bytes(PLC_LineCoordinatorIpAddress), PLC_LineCoordinatorSocketPortNumber0)

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

        print(f"ST1 decapsulate: destPort={self.receivedDestPortNumber}, srcPort={self.receivedSrcPortNumber}, len={self.receivedNumberOfBytes}")
        
        if self.receivedNumberOfBytes == 9:
            print("Received 9-byte packet from PLC (command packet)")
            receivedPayload = bytes(self.receivedPayload)
            
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

        print(f"ST1 sending to PLC on port: {PLC_LineCoordinatorSocketPortNumber0}")
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

    sT1_ComponentKitting = ST1_ComponentKitting(args)
    sT1_ComponentKitting.mainThread()



if __name__ == '__main__':
    main()
