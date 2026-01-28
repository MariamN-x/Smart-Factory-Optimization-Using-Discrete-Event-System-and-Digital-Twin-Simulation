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
import random
import simpy

# -----------------------------
# Station 5: Quality Inspection + Diverter (SimPy core)
# -----------------------------

def _st5_accept_rate(recipe_id: int) -> float:
    base = 0.88
    if int(recipe_id) == 0:
        return base
    return max(0.70, min(0.97, base - (int(recipe_id) % 5) * 0.02))

class _ST5SimModel:
    def __init__(self, random_seed: int = 5):
        random.seed(int(random_seed))
        self.env = simpy.Environment()

        # state
        self.busy = False
        self.fault_latched = False
        self._unit_done = False
        self._unit_decision = None  # True=accept, False=reject
        self._current_batch = 0
        self._current_recipe = 0
        self._active_proc = None

        # results / KPIs
        self.accept_total = 0
        self.reject_total = 0
        self.last_cycle_time_s = 0.0

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

    def start_unit(self, batch_id: int, recipe_id: int) -> bool:
        if self.fault_latched or self.busy:
            return False

        self.busy = True
        self._unit_done = False
        self._unit_decision = None
        self._current_batch = int(batch_id)
        self._current_recipe = int(recipe_id)
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

    # ---- SimPy process ----
    def _unit_process(self, batch_id: int, recipe_id: int):
        t0 = self.env.now

        # Small chance of inspection cell fault
        if random.random() < 0.005:
            yield self.env.timeout(0.2)
            self.fault_latched = True
            self.busy = False
            return

        # Stage 1: positioning + camera capture
        yield self.env.timeout(0.4)

        # Stage 2: vision/measurement compute
        yield self.env.timeout(0.8)

        # Stage 3: rules/spec compare
        yield self.env.timeout(0.3)

        # Decision
        p_accept = _st5_accept_rate(recipe_id)
        decision_accept = (random.random() < p_accept)

        # Optional re-inspection once (rework loop)
        if not decision_accept:
            yield self.env.timeout(0.6)
            yield self.env.timeout(0.5)
            decision_accept = (random.random() < min(0.95, p_accept + 0.12))

        # Diverter actuation
        yield self.env.timeout(0.2)

        # Update KPIs
        self._unit_decision = decision_accept
        if decision_accept:
            self.accept_total += 1
        else:
            self.reject_total += 1

        self.last_cycle_time_s = float(self.env.now - t0)
        # Process will end here, busy flag will be cleared in step() when process dies
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
        # SimPy station model
        self._st5 = _ST5SimModel(random_seed=5)
        
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
        # End of user custom code region. Please don't edit beyond this point.



    def mainThread(self):
        dSession = vsiCommonPythonApi.connectToServer(self.localHost, self.domain, self.portNum, self.componentId)
        vsiEthernetPythonGateway.initialize(dSession, self.componentId, bytes(srcMacAddress), bytes(srcIpAddress))
        try:
            vsiCommonPythonApi.waitForReset()

            # Start of user custom code region. Please apply edits only within these regions:  After Reset
            # Initialize state - station starts in reset state
            self._st5.reset()
            self._prev_cmd_start = 0
            self._prev_cmd_reset = 0
            self._prev_cmd_stop = 0
            self._start_latched = False
            self._done_pulse_remaining = 0
            self._initialized = False
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
                    print("ST5: RESET rising edge detected")
                    # Reset SimPy model
                    self._st5.reset()
                    # Clear all latches and outputs
                    self._start_latched = False
                    self._done_pulse_remaining = 0
                    self._initialized = True  # Station is now initialized
                    
                    self.mySignals.ready = 1  # Ready after reset
                    self.mySignals.busy = 0
                    self.mySignals.fault = 0
                    self.mySignals.done = 0
                    self.mySignals.cycle_time_ms = 0
                    self.mySignals.accept = 0
                    self.mySignals.reject = 0
                    self.mySignals.last_accept = 0
                
                # --- START handling (one-shot pulse) ---
                if start_edge and not cmd_stop and not cmd_reset and self._initialized:
                    # Check if station is ready to start
                    if (not self._st5.busy and not self._st5.fault_latched and 
                        not self._start_latched and self._done_pulse_remaining == 0):
                        print(f"ST5: START edge latched (batch={self.mySignals.batch_id}, recipe={self.mySignals.recipe_id})")
                        success = self._st5.start_unit(self.mySignals.batch_id, self.mySignals.recipe_id)
                        if success:
                            self._start_latched = True
                            self.mySignals.ready = 0
                            self.mySignals.busy = 1
                            self._current_batch_id = self.mySignals.batch_id
                            self._current_recipe_id = self.mySignals.recipe_id
                
                # --- STEP SimPy model (CRITICAL: step based on latched/busy state, NOT cmd_start) ---
                # Step if start_latched OR busy OR done_pulse_remaining>0
                should_step = (self._start_latched or self._st5.busy or self._done_pulse_remaining > 0) and self._initialized and not cmd_reset
                
                # Stop command pauses stepping
                if cmd_stop and not cmd_reset:
                    should_step = False
                    self.mySignals.ready = 0
                
                if should_step:
                    # Debug log when stepping without cmd_start
                    if cmd_start == 0 and (self._start_latched or self._st5.busy):
                        print("ST5: stepping (latched/busy) cmd_start=0")
                    self._st5.step(self._sim_dt_s)
                
                # --- CYCLE COMPLETION handling ---
                if self._st5.get_done_pulse():
                    print("ST5: Cycle complete -> emitting DONE pulse")
                    # Update KPI counters from model
                    self.mySignals.accept = int(self._st5.accept_total)
                    self.mySignals.reject = int(self._st5.reject_total)
                    self.mySignals.last_accept = 1 if self._st5.get_last_decision() else 0
                    
                    # Update cycle time
                    self.mySignals.cycle_time_ms = int(self._st5.last_cycle_time_s * 1000.0)
                    
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
                self.mySignals.busy = 1 if (self._st5.busy or self._start_latched) else 0
                self.mySignals.fault = 1 if self._st5.fault_latched else 0
                
                # READY logic: not busy, not start_latched, no done pulse active, not resetting
                # Note: ready already set appropriately above
                
                # Save previous states for edge detection
                self._prev_cmd_start = cmd_start
                self._prev_cmd_stop = cmd_stop
                self._prev_cmd_reset = cmd_reset
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
