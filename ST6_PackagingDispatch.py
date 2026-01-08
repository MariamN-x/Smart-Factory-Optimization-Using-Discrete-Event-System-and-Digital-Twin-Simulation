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
# Station 6: Packaging + Dispatch (SimPy core)
# -----------------------------
# Real-world idea:
# - Carton erect, robot place product, fold flaps, tape seal, apply label, outfeed.
# - Stocks (carton/tape/label) can run out -> downtime + refill delay.
# - Machine faults can happen -> downtime + repair delay.
#
# VSI integration:
# - PLC controls via cmd_start/cmd_stop/cmd_reset.
# - We step SimPy in the mainThread loop and copy results to mySignals.

class _ST6SimModel:
    def __init__(self, random_seed: int = 6):
        random.seed(int(random_seed))
        self.env = simpy.Environment()

        # state
        self.busy = False
        self.fault_latched = False

        # stocks
        self.carton_stock = 12
        self.tape_stock = 12
        self.label_stock = 12

        # KPIs
        self.packages_completed = 0
        self.arm_cycles = 0
        self.total_repairs = 0
        self.operational_time_s = 0.0
        self.downtime_s = 0.0
        self.availability = 0.0

        # last cycle
        self.last_cycle_time_s = 0.0
        self._done_pulse = False

        self._active_proc = None

    def reset(self, random_seed: int = 6):
        self.__init__(random_seed=random_seed)

    def step(self, dt_s: float):
        if dt_s <= 0:
            return
        target = self.env.now + float(dt_s)
        self.env.run(until=target)

    def pop_done_pulse(self) -> bool:
        if self._done_pulse:
            self._done_pulse = False
            return True
        return False

    def start_unit(self, batch_id: int, recipe_id: int) -> bool:
        # batch_id/recipe_id not used deeply here, but kept for realism/extensions
        if self.fault_latched or self.busy:
            return False
        self.busy = True
        self._active_proc = self.env.process(self._pack_one_unit(batch_id, recipe_id))
        return True

    # -------- helpers --------
    def _update_availability(self):
        total = self.operational_time_s + self.downtime_s
        self.availability = (self.operational_time_s / total * 100.0) if total > 0 else 0.0

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
        # simple refill delay (operator refills)
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
        self.total_repairs += 1
        yield self._downtime(seconds)

    # -------- main process --------
    def _pack_one_unit(self, batch_id: int, recipe_id: int):
        t0 = self.env.now

        # Check / refill materials
        if self.carton_stock <= 0:
            yield from self._refill("carton")
        if self.tape_stock <= 0:
            yield from self._refill("tape")
        if self.label_stock <= 0:
            yield from self._refill("label")

        # Step 1: carton erect
        if self._maybe_fault(0.010):
            yield from self._repair(5.0)
        yield self._operate(1.0)
        self.carton_stock -= 1

        # Step 2: robot pick+place
        if self._maybe_fault(0.015):
            yield from self._repair(6.0)
        yield self._operate(1.2)
        self.arm_cycles += 1

        # Step 3: flap fold
        if self._maybe_fault(0.008):
            yield from self._repair(4.5)
        yield self._operate(1.5)

        # Step 4: tape seal
        if self.tape_stock <= 0:
            yield from self._refill("tape")
        if self._maybe_fault(0.010):
            yield from self._repair(5.5)
        yield self._operate(1.2)
        self.tape_stock -= 1

        # Step 5: label apply
        if self.label_stock <= 0:
            yield from self._refill("label")
        if self._maybe_fault(0.010):
            yield from self._repair(5.0)
        yield self._operate(1.0)
        self.label_stock -= 1

        # Step 6: outfeed
        if self._maybe_fault(0.005):
            yield from self._repair(4.0)
        yield self._operate(0.8)

        # Complete
        self.packages_completed += 1
        self.last_cycle_time_s = float(self.env.now - t0)
        self._done_pulse = True
        self.busy = False
# End of user custom code region. Please don't edit beyond this point.
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
        # SimPy station model (created before mainThread)
        self._st6 = _ST6SimModel(random_seed=6)
        self._prev_cmd_reset = 0
        self._sim_dt_s = 0.1
        # End of user custom code region. Please don't edit beyond this point.



    def mainThread(self):
        dSession = vsiCommonPythonApi.connectToServer(self.localHost, self.domain, self.portNum, self.componentId)
        vsiEthernetPythonGateway.initialize(dSession, self.componentId, bytes(srcMacAddress), bytes(srcIpAddress))
        try:
            vsiCommonPythonApi.waitForReset()

            # Start of user custom code region. Please apply edits only within these regions:  After Reset
            # initialize outputs
            self.mySignals.ready = 1
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

                # --- detect reset edge ---
                cmd_reset = 1 if self.mySignals.cmd_reset else 0
                if cmd_reset == 1 and self._prev_cmd_reset == 0:
                    self._st6.reset(random_seed=6)
                    # clear outputs
                    self.mySignals.done = 0
                    self.mySignals.fault = 0
                    self.mySignals.busy = 0
                    self.mySignals.ready = 1
                    self.mySignals.cycle_time_ms = 0
                    self.mySignals.packages_completed = 0
                    self.mySignals.arm_cycles = 0
                    self.mySignals.total_repairs = 0
                    self.mySignals.operational_time_s = 0
                    self.mySignals.downtime_s = 0
                    self.mySignals.availability = 0

                self._prev_cmd_reset = cmd_reset

                start_en = bool(self.mySignals.cmd_start)
                stop_en = bool(self.mySignals.cmd_stop)

                # If fault latched (not used heavily here), expose it
                if self._st6.fault_latched:
                    self.mySignals.fault = 1
                    self.mySignals.ready = 0
                    self.mySignals.busy = 0
                    self.mySignals.done = 0
                else:
                    self.mySignals.fault = 0

                    if stop_en:
                        # paused by PLC -> no sim stepping
                        self.mySignals.ready = 0
                        self.mySignals.busy = 1 if self._st6.busy else 0
                        self.mySignals.done = 0
                    else:
                        # start a new package cycle if enabled and idle
                        if start_en and (not self._st6.busy):
                            self._st6.start_unit(self.mySignals.batch_id, self.mySignals.recipe_id)

                        # advance time only when started
                        if start_en:
                            self._st6.step(self._sim_dt_s)

                        # snapshot outputs
                        self.mySignals.busy = 1 if self._st6.busy else 0
                        self.mySignals.ready = 1 if (not self._st6.busy) else 0
                        self.mySignals.done = 1 if self._st6.pop_done_pulse() else 0
                        self.mySignals.cycle_time_ms = int(self._st6.last_cycle_time_s * 1000.0)

                        self.mySignals.packages_completed = int(self._st6.packages_completed)
                        self.mySignals.arm_cycles = int(self._st6.arm_cycles)
                        self.mySignals.total_repairs = int(self._st6.total_repairs)
                        self.mySignals.operational_time_s = float(self._st6.operational_time_s)
                        self.mySignals.downtime_s = float(self._st6.downtime_s)
                        self.mySignals.availability = float(self._st6.availability)
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

        if(self.receivedSrcPortNumber == PLC_LineCoordinatorSocketPortNumber0):
            print("Received packet from PLC_LineCoordinator")
            receivedPayload = bytes(self.receivedPayload)
            self.mySignals.cmd_start, receivedPayload = self.unpackBytes('?', receivedPayload)

            self.mySignals.cmd_stop, receivedPayload = self.unpackBytes('?', receivedPayload)

            self.mySignals.cmd_reset, receivedPayload = self.unpackBytes('?', receivedPayload)

            self.mySignals.batch_id, receivedPayload = self.unpackBytes('L', receivedPayload)

            self.mySignals.recipe_id, receivedPayload = self.unpackBytes('H', receivedPayload)


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
                      
    sT6_PackagingDispatch = ST6_PackagingDispatch(args)
    sT6_PackagingDispatch.mainThread()



if __name__ == '__main__':
    main()