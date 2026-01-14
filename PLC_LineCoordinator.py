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
        self.S1_ready = 0
        self.S1_busy = 0
        self.S1_fault = 0
        self.S1_done = 0
        self.S1_cycle_time_ms = 0
        self.S1_inventory_ok = 0
        self.S1_any_arm_failed = 0
        self.S2_ready = 0
        self.S2_busy = 0
        self.S2_fault = 0
        self.S2_done = 0
        self.S2_cycle_time_ms = 0
        self.S2_completed = 0
        self.S2_scrapped = 0
        self.S2_reworks = 0
        self.S2_cycle_time_avg_s = 0
        self.S3_ready = 0
        self.S3_busy = 0
        self.S3_fault = 0
        self.S3_done = 0
        self.S3_cycle_time_ms = 0
        self.S3_strain_relief_ok = 0
        self.S3_continuity_ok = 0
        self.S4_ready = 0
        self.S4_busy = 0
        self.S4_fault = 0
        self.S4_done = 0
        self.S4_cycle_time_ms = 0
        self.S4_total = 0
        self.S4_completed = 0
        self.S5_ready = 0
        self.S5_busy = 0
        self.S5_fault = 0
        self.S5_done = 0
        self.S5_cycle_time_ms = 0
        self.S5_accept = 0
        self.S5_reject = 0
        self.S5_last_accept = 0
        self.S6_ready = 0
        self.S6_busy = 0
        self.S6_fault = 0
        self.S6_done = 0
        self.S6_cycle_time_ms = 0
        self.S6_packages_completed = 0
        self.S6_arm_cycles = 0
        self.S6_total_repairs = 0
        self.S6_operational_time_s = 0
        self.S6_downtime_s = 0
        self.S6_availability = 0

        # Outputs
        self.S1_cmd_start = 0
        self.S1_cmd_stop = 0
        self.S1_cmd_reset = 0
        self.S1_batch_id = 0
        self.S1_recipe_id = 0
        self.S2_cmd_start = 0
        self.S2_cmd_stop = 0
        self.S2_cmd_reset = 0
        self.S2_batch_id = 0
        self.S2_recipe_id = 0
        self.S3_cmd_start = 0
        self.S3_cmd_stop = 0
        self.S3_cmd_reset = 0
        self.S3_batch_id = 0
        self.S3_recipe_id = 0
        self.S4_cmd_start = 0
        self.S4_cmd_stop = 0
        self.S4_cmd_reset = 0
        self.S4_batch_id = 0
        self.S4_recipe_id = 0
        self.S5_cmd_start = 0
        self.S5_cmd_stop = 0
        self.S5_cmd_reset = 0
        self.S5_batch_id = 0
        self.S5_recipe_id = 0
        self.S6_cmd_start = 0
        self.S6_cmd_stop = 0
        self.S6_cmd_reset = 0
        self.S6_batch_id = 0
        self.S6_recipe_id = 0



srcMacAddress = [0x02, 0x00, 0x00, 0x00, 0x00, 0x01]
ST1_ComponentKittingMacAddress = [0x02, 0x00, 0x00, 0x00, 0x00, 0x11]
ST2_FrameCoreAssemblyMacAddress = [0x02, 0x00, 0x00, 0x00, 0x00, 0x12]
ST3_ElectronicsWiringMacAddress = [0x02, 0x00, 0x00, 0x00, 0x00, 0x13]
ST4_CalibrationTestingMacAddress = [0x02, 0x00, 0x00, 0x00, 0x00, 0x14]
ST5_QualityInspectionMacAddress = [0x02, 0x00, 0x00, 0x00, 0x00, 0x15]
ST6_PackagingDispatchMacAddress = [0x02, 0x00, 0x00, 0x00, 0x00, 0x16]
srcIpAddress = [10, 10, 0, 1]
ST1_ComponentKittingIpAddress = [10, 10, 0, 11]
ST2_FrameCoreAssemblyIpAddress = [10, 10, 0, 12]
ST3_ElectronicsWiringIpAddress = [10, 10, 0, 13]
ST4_CalibrationTestingIpAddress = [10, 10, 0, 14]
ST5_QualityInspectionIpAddress = [10, 10, 0, 15]
ST6_PackagingDispatchIpAddress = [10, 10, 0, 16]

PLC_LineCoordinatorSocketPortNumber0 = 6001
PLC_LineCoordinatorSocketPortNumber1 = 6002
PLC_LineCoordinatorSocketPortNumber2 = 6003
PLC_LineCoordinatorSocketPortNumber3 = 6004
PLC_LineCoordinatorSocketPortNumber4 = 6005
PLC_LineCoordinatorSocketPortNumber5 = 6006

ST1_ComponentKitting0 = 0
ST2_FrameCoreAssembly1 = 1
ST3_ElectronicsWiring2 = 2
ST4_CalibrationTesting3 = 3
ST5_QualityInspection4 = 4
ST6_PackagingDispatch5 = 5


# Start of user custom code region. Please apply edits only within these regions:  Global Variables & Definitions
# PLC coordination state machine (Option A)
# - Run stations strictly sequentially: S1 -> S2 -> S3 -> S4 -> S5 -> S6
# - Only ONE station is allowed to run at any time; all others are held stopped.
# - PLC advances to the next station ONLY when it receives a rising edge on Sx_done.
# - If any station faults, PLC stops and issues a reset pulse to ALL stations, then restarts at S1.

RESET_PULSE_TICKS = 3

def _any_fault(ms):
    return bool(ms.S1_fault or ms.S2_fault or ms.S3_fault or ms.S4_fault or ms.S5_fault or ms.S6_fault)

def _stop_all(ms):
    # stop all stations (no reset)
    ms.S1_cmd_start = 0; ms.S1_cmd_stop = 1; ms.S1_cmd_reset = 0
    ms.S2_cmd_start = 0; ms.S2_cmd_stop = 1; ms.S2_cmd_reset = 0
    ms.S3_cmd_start = 0; ms.S3_cmd_stop = 1; ms.S3_cmd_reset = 0
    ms.S4_cmd_start = 0; ms.S4_cmd_stop = 1; ms.S4_cmd_reset = 0
    ms.S5_cmd_start = 0; ms.S5_cmd_stop = 1; ms.S5_cmd_reset = 0
    ms.S6_cmd_start = 0; ms.S6_cmd_stop = 1; ms.S6_cmd_reset = 0

def _reset_all_outputs(ms):
    # stop + reset all stations
    ms.S1_cmd_start = 0; ms.S1_cmd_stop = 1; ms.S1_cmd_reset = 1
    ms.S2_cmd_start = 0; ms.S2_cmd_stop = 1; ms.S2_cmd_reset = 1
    ms.S3_cmd_start = 0; ms.S3_cmd_stop = 1; ms.S3_cmd_reset = 1
    ms.S4_cmd_start = 0; ms.S4_cmd_stop = 1; ms.S4_cmd_reset = 1
    ms.S5_cmd_start = 0; ms.S5_cmd_stop = 1; ms.S5_cmd_reset = 1
    ms.S6_cmd_start = 0; ms.S6_cmd_stop = 1; ms.S6_cmd_reset = 1

def _propagate_batch_recipe(ms, batch_id, recipe_id):
    ms.S1_batch_id = batch_id; ms.S1_recipe_id = recipe_id
    ms.S2_batch_id = batch_id; ms.S2_recipe_id = recipe_id
    ms.S3_batch_id = batch_id; ms.S3_recipe_id = recipe_id
    ms.S4_batch_id = batch_id; ms.S4_recipe_id = recipe_id
    ms.S5_batch_id = batch_id; ms.S5_recipe_id = recipe_id
    ms.S6_batch_id = batch_id; ms.S6_recipe_id = recipe_id

# End of user custom code region. Please don't edit beyond this point.
class PLC_LineCoordinator:

    def __init__(self, args):
        self.componentId = 0
        self.localHost = args.server_url
        self.domain = args.domain
        self.portNum = 50101

        self.simulationStep = 0
        self.stopRequested = False
        self.totalSimulationTime = 0

        self.receivedNumberOfBytes = 0
        self.receivedPayload = []

        self.numberOfPorts = 6
        self.clientPortNum = [0] * self.numberOfPorts
        self.receivedDestPortNumber = 0
        self.receivedSrcPortNumber = 0
        self.expectedNumberOfBytes = 0
        self.mySignals = MySignals()

        # Start of user custom code region. Please apply edits only within these regions:  Constructor
        # coordination vars
        self._batch_id = 1
        self._recipe_id = 1

        # state
        self._state = "RESETTING"
        self._reset_ticks = 0

        # active station index (1..6)
        self._active_idx = 1

        # done edge tracking (1..6)
        self._prev_done = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0}

        # latch to avoid advancing twice if a station holds done high
        self._done_seen = {1: False, 2: False, 3: False, 4: False, 5: False, 6: False}
# End of user custom code region. Please don't edit beyond this point.



    def mainThread(self):
        dSession = vsiCommonPythonApi.connectToServer(self.localHost, self.domain, self.portNum, self.componentId)
        vsiEthernetPythonGateway.initialize(dSession, self.componentId, bytes(srcMacAddress), bytes(srcIpAddress))
        try:
            vsiCommonPythonApi.waitForReset()

            # Start of user custom code region. Please apply edits only within these regions:  After Reset
            # initialize outputs: reset everyone for a few ticks
            _reset_all_outputs(self.mySignals)

            # propagate initial context
            _propagate_batch_recipe(self.mySignals, self._batch_id, self._recipe_id)

            # init sequencing
            self._state = "RESETTING"
            self._reset_ticks = 0
            self._active_idx = 1
            self._prev_done = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0}
            self._done_seen = {1: False, 2: False, 3: False, 4: False, 5: False, 6: False}
# End of user custom code region. Please don't edit beyond this point.
            self.updateInternalVariables()

            if(vsiCommonPythonApi.isStopRequested()):
                raise Exception("stopRequested")
            self.establishTcpUdpConnection()
            nextExpectedTime = vsiCommonPythonApi.getSimulationTimeInNs()
            while(vsiCommonPythonApi.getSimulationTimeInNs() < self.totalSimulationTime):

                # Start of user custom code region. Please apply edits only within these regions:  Inside the while loop
# (no PLC logic here; we act after receiving station packets, before sending commands)

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

                receivedData = vsiEthernetPythonGateway.recvEthernetPacket(self.clientPortNum[ST1_ComponentKitting0])
                if(receivedData[3] != 0):
                    self.decapsulateReceivedData(receivedData)

                receivedData = vsiEthernetPythonGateway.recvEthernetPacket(self.clientPortNum[ST2_FrameCoreAssembly1])
                if(receivedData[3] != 0):
                    self.decapsulateReceivedData(receivedData)

                receivedData = vsiEthernetPythonGateway.recvEthernetPacket(self.clientPortNum[ST3_ElectronicsWiring2])
                if(receivedData[3] != 0):
                    self.decapsulateReceivedData(receivedData)

                receivedData = vsiEthernetPythonGateway.recvEthernetPacket(self.clientPortNum[ST4_CalibrationTesting3])
                if(receivedData[3] != 0):
                    self.decapsulateReceivedData(receivedData)

                receivedData = vsiEthernetPythonGateway.recvEthernetPacket(self.clientPortNum[ST5_QualityInspection4])
                if(receivedData[3] != 0):
                    self.decapsulateReceivedData(receivedData)

                receivedData = vsiEthernetPythonGateway.recvEthernetPacket(self.clientPortNum[ST6_PackagingDispatch5])
                if(receivedData[3] != 0):
                    self.decapsulateReceivedData(receivedData)

                # Start of user custom code region. Please apply edits only within these regions:  Before sending the packet
                # --- PLC Option A sequencing (uses fresh received station inputs) ---
                ms = self.mySignals

                # Always propagate batch/recipe to all stations
                _propagate_batch_recipe(ms, self._batch_id, self._recipe_id)

                # If any fault occurs, reset the entire line and restart from S1
                if _any_fault(ms):
                    self._state = "RESETTING"
                    self._reset_ticks = 0
                    self._active_idx = 1
                    self._prev_done = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0}
                    self._done_seen = {1: False, 2: False, 3: False, 4: False, 5: False, 6: False}

                if self._state == "RESETTING":
                    _reset_all_outputs(ms)
                    self._reset_ticks += 1

                    if self._reset_ticks >= RESET_PULSE_TICKS:
                        _stop_all(ms)  # clears reset=0 and holds stopped
                        self._state = "RUNNING"
                        self._active_idx = 1
                        self._prev_done = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0}
                        self._done_seen = {1: False, 2: False, 3: False, 4: False, 5: False, 6: False}

                else:
                    # RUNNING: allow only the active station to run, hold others stopped.
                    _stop_all(ms)

                    k = int(self._active_idx)
                    if k < 1:
                        k = 1
                    if k > 6:
                        k = 6

                    # allow station k to run
                    setattr(ms, f"S{k}_cmd_stop", 0)
                    setattr(ms, f"S{k}_cmd_reset", 0)

                    # HOLD cmd_start until station actually goes busy (prevents missing a 1-tick pulse)
                    busy_now = int(getattr(ms, f"S{k}_busy"))
                    done_now = int(getattr(ms, f"S{k}_done"))

                    if done_now == 0 and busy_now == 0:
                        setattr(ms, f"S{k}_cmd_start", 1)
                    else:
                        setattr(ms, f"S{k}_cmd_start", 0)

                    # done detect (pulse or level)
                    prev_done = int(self._prev_done.get(k, 0))
                    self._prev_done[k] = done_now
                    done_rise = (done_now == 1 and prev_done == 0)

                    if done_now == 1 and not self._done_seen.get(k, False):
                        self._done_seen[k] = True
                        done_rise = True
                    if done_now == 0:
                        self._done_seen[k] = False

                    if done_rise:
                        if k < 6:
                            self._active_idx = k + 1
                        else:
                            self._batch_id += 1
                            self._active_idx = 1
                # End of user custom code region. Please don't edit beyond this point.

                #Send ethernet packet to ST1_ComponentKitting
                self.sendEthernetPacketToST1_ComponentKitting()

                #Send ethernet packet to ST2_FrameCoreAssembly
                self.sendEthernetPacketToST2_FrameCoreAssembly()

                #Send ethernet packet to ST3_ElectronicsWiring
                self.sendEthernetPacketToST3_ElectronicsWiring()

                #Send ethernet packet to ST4_CalibrationTesting
                self.sendEthernetPacketToST4_CalibrationTesting()

                #Send ethernet packet to ST5_QualityInspection
                self.sendEthernetPacketToST5_QualityInspection()

                #Send ethernet packet to ST6_PackagingDispatch
                self.sendEthernetPacketToST6_PackagingDispatch()

                # Start of user custom code region. Please apply edits only within these regions:  After sending the packet

                # End of user custom code region. Please don't edit beyond this point.

                print("\n+=PLC_LineCoordinator+=")
                print("  VSI time:", end = " ")
                print(vsiCommonPythonApi.getSimulationTimeInNs(), end = " ")
                print("ns")
                print("  Inputs:")
                print("\tS1_ready =", end = " ")
                print(self.mySignals.S1_ready)
                print("\tS1_busy =", end = " ")
                print(self.mySignals.S1_busy)
                print("\tS1_fault =", end = " ")
                print(self.mySignals.S1_fault)
                print("\tS1_done =", end = " ")
                print(self.mySignals.S1_done)
                print("\tS1_cycle_time_ms =", end = " ")
                print(self.mySignals.S1_cycle_time_ms)
                print("\tS1_inventory_ok =", end = " ")
                print(self.mySignals.S1_inventory_ok)
                print("\tS1_any_arm_failed =", end = " ")
                print(self.mySignals.S1_any_arm_failed)
                print("\tS2_ready =", end = " ")
                print(self.mySignals.S2_ready)
                print("\tS2_busy =", end = " ")
                print(self.mySignals.S2_busy)
                print("\tS2_fault =", end = " ")
                print(self.mySignals.S2_fault)
                print("\tS2_done =", end = " ")
                print(self.mySignals.S2_done)
                print("\tS2_cycle_time_ms =", end = " ")
                print(self.mySignals.S2_cycle_time_ms)
                print("\tS2_completed =", end = " ")
                print(self.mySignals.S2_completed)
                print("\tS2_scrapped =", end = " ")
                print(self.mySignals.S2_scrapped)
                print("\tS2_reworks =", end = " ")
                print(self.mySignals.S2_reworks)
                print("\tS2_cycle_time_avg_s =", end = " ")
                print(self.mySignals.S2_cycle_time_avg_s)
                print("\tS3_ready =", end = " ")
                print(self.mySignals.S3_ready)
                print("\tS3_busy =", end = " ")
                print(self.mySignals.S3_busy)
                print("\tS3_fault =", end = " ")
                print(self.mySignals.S3_fault)
                print("\tS3_done =", end = " ")
                print(self.mySignals.S3_done)
                print("\tS3_cycle_time_ms =", end = " ")
                print(self.mySignals.S3_cycle_time_ms)
                print("\tS3_strain_relief_ok =", end = " ")
                print(self.mySignals.S3_strain_relief_ok)
                print("\tS3_continuity_ok =", end = " ")
                print(self.mySignals.S3_continuity_ok)
                print("\tS4_ready =", end = " ")
                print(self.mySignals.S4_ready)
                print("\tS4_busy =", end = " ")
                print(self.mySignals.S4_busy)
                print("\tS4_fault =", end = " ")
                print(self.mySignals.S4_fault)
                print("\tS4_done =", end = " ")
                print(self.mySignals.S4_done)
                print("\tS4_cycle_time_ms =", end = " ")
                print(self.mySignals.S4_cycle_time_ms)
                print("\tS4_total =", end = " ")
                print(self.mySignals.S4_total)
                print("\tS4_completed =", end = " ")
                print(self.mySignals.S4_completed)
                print("\tS5_ready =", end = " ")
                print(self.mySignals.S5_ready)
                print("\tS5_busy =", end = " ")
                print(self.mySignals.S5_busy)
                print("\tS5_fault =", end = " ")
                print(self.mySignals.S5_fault)
                print("\tS5_done =", end = " ")
                print(self.mySignals.S5_done)
                print("\tS5_cycle_time_ms =", end = " ")
                print(self.mySignals.S5_cycle_time_ms)
                print("\tS5_accept =", end = " ")
                print(self.mySignals.S5_accept)
                print("\tS5_reject =", end = " ")
                print(self.mySignals.S5_reject)
                print("\tS5_last_accept =", end = " ")
                print(self.mySignals.S5_last_accept)
                print("\tS6_ready =", end = " ")
                print(self.mySignals.S6_ready)
                print("\tS6_busy =", end = " ")
                print(self.mySignals.S6_busy)
                print("\tS6_fault =", end = " ")
                print(self.mySignals.S6_fault)
                print("\tS6_done =", end = " ")
                print(self.mySignals.S6_done)
                print("\tS6_cycle_time_ms =", end = " ")
                print(self.mySignals.S6_cycle_time_ms)
                print("\tS6_packages_completed =", end = " ")
                print(self.mySignals.S6_packages_completed)
                print("\tS6_arm_cycles =", end = " ")
                print(self.mySignals.S6_arm_cycles)
                print("\tS6_total_repairs =", end = " ")
                print(self.mySignals.S6_total_repairs)
                print("\tS6_operational_time_s =", end = " ")
                print(self.mySignals.S6_operational_time_s)
                print("\tS6_downtime_s =", end = " ")
                print(self.mySignals.S6_downtime_s)
                print("\tS6_availability =", end = " ")
                print(self.mySignals.S6_availability)
                print("  Outputs:")
                print("\tS1_cmd_start =", end = " ")
                print(self.mySignals.S1_cmd_start)
                print("\tS1_cmd_stop =", end = " ")
                print(self.mySignals.S1_cmd_stop)
                print("\tS1_cmd_reset =", end = " ")
                print(self.mySignals.S1_cmd_reset)
                print("\tS1_batch_id =", end = " ")
                print(self.mySignals.S1_batch_id)
                print("\tS1_recipe_id =", end = " ")
                print(self.mySignals.S1_recipe_id)
                print("\tS2_cmd_start =", end = " ")
                print(self.mySignals.S2_cmd_start)
                print("\tS2_cmd_stop =", end = " ")
                print(self.mySignals.S2_cmd_stop)
                print("\tS2_cmd_reset =", end = " ")
                print(self.mySignals.S2_cmd_reset)
                print("\tS2_batch_id =", end = " ")
                print(self.mySignals.S2_batch_id)
                print("\tS2_recipe_id =", end = " ")
                print(self.mySignals.S2_recipe_id)
                print("\tS3_cmd_start =", end = " ")
                print(self.mySignals.S3_cmd_start)
                print("\tS3_cmd_stop =", end = " ")
                print(self.mySignals.S3_cmd_stop)
                print("\tS3_cmd_reset =", end = " ")
                print(self.mySignals.S3_cmd_reset)
                print("\tS3_batch_id =", end = " ")
                print(self.mySignals.S3_batch_id)
                print("\tS3_recipe_id =", end = " ")
                print(self.mySignals.S3_recipe_id)
                print("\tS4_cmd_start =", end = " ")
                print(self.mySignals.S4_cmd_start)
                print("\tS4_cmd_stop =", end = " ")
                print(self.mySignals.S4_cmd_stop)
                print("\tS4_cmd_reset =", end = " ")
                print(self.mySignals.S4_cmd_reset)
                print("\tS4_batch_id =", end = " ")
                print(self.mySignals.S4_batch_id)
                print("\tS4_recipe_id =", end = " ")
                print(self.mySignals.S4_recipe_id)
                print("\tS5_cmd_start =", end = " ")
                print(self.mySignals.S5_cmd_start)
                print("\tS5_cmd_stop =", end = " ")
                print(self.mySignals.S5_cmd_stop)
                print("\tS5_cmd_reset =", end = " ")
                print(self.mySignals.S5_cmd_reset)
                print("\tS5_batch_id =", end = " ")
                print(self.mySignals.S5_batch_id)
                print("\tS5_recipe_id =", end = " ")
                print(self.mySignals.S5_recipe_id)
                print("\tS6_cmd_start =", end = " ")
                print(self.mySignals.S6_cmd_start)
                print("\tS6_cmd_stop =", end = " ")
                print(self.mySignals.S6_cmd_stop)
                print("\tS6_cmd_reset =", end = " ")
                print(self.mySignals.S6_cmd_reset)
                print("\tS6_batch_id =", end = " ")
                print(self.mySignals.S6_batch_id)
                print("\tS6_recipe_id =", end = " ")
                print(self.mySignals.S6_recipe_id)
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
            self.clientPortNum[ST1_ComponentKitting0] = vsiEthernetPythonGateway.tcpListen(PLC_LineCoordinatorSocketPortNumber0)

        if(self.clientPortNum[ST2_FrameCoreAssembly1] == 0):
            self.clientPortNum[ST2_FrameCoreAssembly1] = vsiEthernetPythonGateway.tcpListen(PLC_LineCoordinatorSocketPortNumber1)

        if(self.clientPortNum[ST3_ElectronicsWiring2] == 0):
            self.clientPortNum[ST3_ElectronicsWiring2] = vsiEthernetPythonGateway.tcpListen(PLC_LineCoordinatorSocketPortNumber2)

        if(self.clientPortNum[ST4_CalibrationTesting3] == 0):
            self.clientPortNum[ST4_CalibrationTesting3] = vsiEthernetPythonGateway.tcpListen(PLC_LineCoordinatorSocketPortNumber3)

        if(self.clientPortNum[ST5_QualityInspection4] == 0):
            self.clientPortNum[ST5_QualityInspection4] = vsiEthernetPythonGateway.tcpListen(PLC_LineCoordinatorSocketPortNumber4)

        if(self.clientPortNum[ST6_PackagingDispatch5] == 0):
            self.clientPortNum[ST6_PackagingDispatch5] = vsiEthernetPythonGateway.tcpListen(PLC_LineCoordinatorSocketPortNumber5)

        if(self.clientPortNum[ST1_ComponentKitting0] == 0):
            print("Error: Failed to listen on TCP port:")
            print(PLC_LineCoordinatorSocketPortNumber0)
            exit()

        if(self.clientPortNum[ST2_FrameCoreAssembly1] == 0):
            print("Error: Failed to listen on TCP port:")
            print(PLC_LineCoordinatorSocketPortNumber1)
            exit()

        if(self.clientPortNum[ST3_ElectronicsWiring2] == 0):
            print("Error: Failed to listen on TCP port:")
            print(PLC_LineCoordinatorSocketPortNumber2)
            exit()

        if(self.clientPortNum[ST4_CalibrationTesting3] == 0):
            print("Error: Failed to listen on TCP port:")
            print(PLC_LineCoordinatorSocketPortNumber3)
            exit()

        if(self.clientPortNum[ST5_QualityInspection4] == 0):
            print("Error: Failed to listen on TCP port:")
            print(PLC_LineCoordinatorSocketPortNumber4)
            exit()

        if(self.clientPortNum[ST6_PackagingDispatch5] == 0):
            print("Error: Failed to listen on TCP port:")
            print(PLC_LineCoordinatorSocketPortNumber5)
            exit()


    def decapsulateReceivedData(self, receivedData):
        self.receivedDestPortNumber = receivedData[0]
        self.receivedSrcPortNumber = receivedData[1]
        self.receivedNumberOfBytes = receivedData[3]
        self.receivedPayload = [0] * (self.receivedNumberOfBytes)

        for i in range(self.receivedNumberOfBytes):
            self.receivedPayload[i] = receivedData[2][i]

        if(self.receivedDestPortNumber == PLC_LineCoordinatorSocketPortNumber0):
            print("Received packet from ST1_ComponentKitting")
            receivedPayload = bytes(self.receivedPayload)
            self.mySignals.S1_ready, receivedPayload = self.unpackBytes('?', receivedPayload)

            self.mySignals.S1_busy, receivedPayload = self.unpackBytes('?', receivedPayload)

            self.mySignals.S1_fault, receivedPayload = self.unpackBytes('?', receivedPayload)

            self.mySignals.S1_done, receivedPayload = self.unpackBytes('?', receivedPayload)

            self.mySignals.S1_cycle_time_ms, receivedPayload = self.unpackBytes('L', receivedPayload)

            self.mySignals.S1_inventory_ok, receivedPayload = self.unpackBytes('?', receivedPayload)

            self.mySignals.S1_any_arm_failed, receivedPayload = self.unpackBytes('?', receivedPayload)

        if(self.receivedDestPortNumber == PLC_LineCoordinatorSocketPortNumber1):
            print("Received packet from ST2_FrameCoreAssembly")
            receivedPayload = bytes(self.receivedPayload)
            self.mySignals.S2_ready, receivedPayload = self.unpackBytes('?', receivedPayload)

            self.mySignals.S2_busy, receivedPayload = self.unpackBytes('?', receivedPayload)

            self.mySignals.S2_fault, receivedPayload = self.unpackBytes('?', receivedPayload)

            self.mySignals.S2_done, receivedPayload = self.unpackBytes('?', receivedPayload)

            self.mySignals.S2_cycle_time_ms, receivedPayload = self.unpackBytes('L', receivedPayload)

            self.mySignals.S2_completed, receivedPayload = self.unpackBytes('L', receivedPayload)

            self.mySignals.S2_scrapped, receivedPayload = self.unpackBytes('L', receivedPayload)

            self.mySignals.S2_reworks, receivedPayload = self.unpackBytes('L', receivedPayload)

            self.mySignals.S2_cycle_time_avg_s, receivedPayload = self.unpackBytes('d', receivedPayload)

        if(self.receivedDestPortNumber == PLC_LineCoordinatorSocketPortNumber2):
            print("Received packet from ST3_ElectronicsWiring")
            receivedPayload = bytes(self.receivedPayload)
            self.mySignals.S3_ready, receivedPayload = self.unpackBytes('?', receivedPayload)

            self.mySignals.S3_busy, receivedPayload = self.unpackBytes('?', receivedPayload)

            self.mySignals.S3_fault, receivedPayload = self.unpackBytes('?', receivedPayload)

            self.mySignals.S3_done, receivedPayload = self.unpackBytes('?', receivedPayload)

            self.mySignals.S3_cycle_time_ms, receivedPayload = self.unpackBytes('L', receivedPayload)

            self.mySignals.S3_strain_relief_ok, receivedPayload = self.unpackBytes('?', receivedPayload)

            self.mySignals.S3_continuity_ok, receivedPayload = self.unpackBytes('?', receivedPayload)

        if(self.receivedDestPortNumber == PLC_LineCoordinatorSocketPortNumber3):
            print("Received packet from ST4_CalibrationTesting")
            receivedPayload = bytes(self.receivedPayload)
            self.mySignals.S4_ready, receivedPayload = self.unpackBytes('?', receivedPayload)

            self.mySignals.S4_busy, receivedPayload = self.unpackBytes('?', receivedPayload)

            self.mySignals.S4_fault, receivedPayload = self.unpackBytes('?', receivedPayload)

            self.mySignals.S4_done, receivedPayload = self.unpackBytes('?', receivedPayload)

            self.mySignals.S4_cycle_time_ms, receivedPayload = self.unpackBytes('L', receivedPayload)

            self.mySignals.S4_total, receivedPayload = self.unpackBytes('L', receivedPayload)

            self.mySignals.S4_completed, receivedPayload = self.unpackBytes('L', receivedPayload)

        if(self.receivedDestPortNumber == PLC_LineCoordinatorSocketPortNumber4):
            print("Received packet from ST5_QualityInspection")
            receivedPayload = bytes(self.receivedPayload)
            self.mySignals.S5_ready, receivedPayload = self.unpackBytes('?', receivedPayload)

            self.mySignals.S5_busy, receivedPayload = self.unpackBytes('?', receivedPayload)

            self.mySignals.S5_fault, receivedPayload = self.unpackBytes('?', receivedPayload)

            self.mySignals.S5_done, receivedPayload = self.unpackBytes('?', receivedPayload)

            self.mySignals.S5_cycle_time_ms, receivedPayload = self.unpackBytes('L', receivedPayload)

            self.mySignals.S5_accept, receivedPayload = self.unpackBytes('L', receivedPayload)

            self.mySignals.S5_reject, receivedPayload = self.unpackBytes('L', receivedPayload)

            self.mySignals.S5_last_accept, receivedPayload = self.unpackBytes('?', receivedPayload)

        if(self.receivedDestPortNumber == PLC_LineCoordinatorSocketPortNumber5):
            print("Received packet from ST6_PackagingDispatch")
            receivedPayload = bytes(self.receivedPayload)
            self.mySignals.S6_ready, receivedPayload = self.unpackBytes('?', receivedPayload)

            self.mySignals.S6_busy, receivedPayload = self.unpackBytes('?', receivedPayload)

            self.mySignals.S6_fault, receivedPayload = self.unpackBytes('?', receivedPayload)

            self.mySignals.S6_done, receivedPayload = self.unpackBytes('?', receivedPayload)

            self.mySignals.S6_cycle_time_ms, receivedPayload = self.unpackBytes('L', receivedPayload)

            self.mySignals.S6_packages_completed, receivedPayload = self.unpackBytes('L', receivedPayload)

            self.mySignals.S6_arm_cycles, receivedPayload = self.unpackBytes('L', receivedPayload)

            self.mySignals.S6_total_repairs, receivedPayload = self.unpackBytes('L', receivedPayload)

            self.mySignals.S6_operational_time_s, receivedPayload = self.unpackBytes('d', receivedPayload)

            self.mySignals.S6_downtime_s, receivedPayload = self.unpackBytes('d', receivedPayload)

            self.mySignals.S6_availability, receivedPayload = self.unpackBytes('d', receivedPayload)


    def sendEthernetPacketToST1_ComponentKitting(self):
        bytesToSend = bytes()

        bytesToSend += self.packBytes('?', self.mySignals.S1_cmd_start)
        bytesToSend += self.packBytes('?', self.mySignals.S1_cmd_stop)
        bytesToSend += self.packBytes('?', self.mySignals.S1_cmd_reset)
        bytesToSend += self.packBytes('L', self.mySignals.S1_batch_id)
        bytesToSend += self.packBytes('H', self.mySignals.S1_recipe_id)
        vsiEthernetPythonGateway.sendEthernetPacket(self.clientPortNum[ST1_ComponentKitting0], bytes(bytesToSend))

    def sendEthernetPacketToST2_FrameCoreAssembly(self):
        bytesToSend = bytes()
        bytesToSend += self.packBytes('?', self.mySignals.S2_cmd_start)
        bytesToSend += self.packBytes('?', self.mySignals.S2_cmd_stop)
        bytesToSend += self.packBytes('?', self.mySignals.S2_cmd_reset)
        bytesToSend += self.packBytes('L', self.mySignals.S2_batch_id)
        bytesToSend += self.packBytes('H', self.mySignals.S2_recipe_id)
        vsiEthernetPythonGateway.sendEthernetPacket(self.clientPortNum[ST2_FrameCoreAssembly1], bytes(bytesToSend))

    def sendEthernetPacketToST3_ElectronicsWiring(self):
        bytesToSend = bytes()
        bytesToSend += self.packBytes('?', self.mySignals.S3_cmd_start)
        bytesToSend += self.packBytes('?', self.mySignals.S3_cmd_stop)
        bytesToSend += self.packBytes('?', self.mySignals.S3_cmd_reset)
        bytesToSend += self.packBytes('L', self.mySignals.S3_batch_id)
        bytesToSend += self.packBytes('H', self.mySignals.S3_recipe_id)
        vsiEthernetPythonGateway.sendEthernetPacket(self.clientPortNum[ST3_ElectronicsWiring2], bytes(bytesToSend))

    def sendEthernetPacketToST4_CalibrationTesting(self):
        bytesToSend = bytes()
        bytesToSend += self.packBytes('?', self.mySignals.S4_cmd_start)
        bytesToSend += self.packBytes('?', self.mySignals.S4_cmd_stop)
        bytesToSend += self.packBytes('?', self.mySignals.S4_cmd_reset)
        bytesToSend += self.packBytes('L', self.mySignals.S4_batch_id)
        bytesToSend += self.packBytes('H', self.mySignals.S4_recipe_id)
        vsiEthernetPythonGateway.sendEthernetPacket(self.clientPortNum[ST4_CalibrationTesting3], bytes(bytesToSend))

    def sendEthernetPacketToST5_QualityInspection(self):
        bytesToSend = bytes()
        bytesToSend += self.packBytes('?', self.mySignals.S5_cmd_start)
        bytesToSend += self.packBytes('?', self.mySignals.S5_cmd_stop)
        bytesToSend += self.packBytes('?', self.mySignals.S5_cmd_reset)
        bytesToSend += self.packBytes('L', self.mySignals.S5_batch_id)
        bytesToSend += self.packBytes('H', self.mySignals.S5_recipe_id)
        vsiEthernetPythonGateway.sendEthernetPacket(self.clientPortNum[ST5_QualityInspection4], bytes(bytesToSend))

    def sendEthernetPacketToST6_PackagingDispatch(self):
        bytesToSend = bytes()
        bytesToSend += self.packBytes('?', self.mySignals.S6_cmd_start)
        bytesToSend += self.packBytes('?', self.mySignals.S6_cmd_stop)
        bytesToSend += self.packBytes('?', self.mySignals.S6_cmd_reset)
        bytesToSend += self.packBytes('L', self.mySignals.S6_batch_id)
        bytesToSend += self.packBytes('H', self.mySignals.S6_recipe_id)
        vsiEthernetPythonGateway.sendEthernetPacket(self.clientPortNum[ST6_PackagingDispatch5], bytes(bytesToSend))

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

    pLC_LineCoordinator = PLC_LineCoordinator(args)
    pLC_LineCoordinator.mainThread()



if __name__ == '__main__':
    main()
