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
# PLC coordination state machine for the 6-station line (S1..S6).
# States:
#   RESET_ALL    -> pulse reset on all stations for RESET_PULSE_TICKS
#   STARTUP      -> start stations sequentially S1 -> ... -> S6 (wait for done edge per station)
#   RUN          -> keep all stations running; increment batch_id when S6 completes
#   FAULT_RESET  -> if any station faults, stop/reset all then restart STARTUP

STATIONS = ["S1", "S2", "S3", "S4", "S5", "S6"]
RESET_PULSE_TICKS = 3


def _set_context(ms, st, batch_id, recipe_id):
    setattr(ms, f"{st}_batch_id", int(batch_id))
    setattr(ms, f"{st}_recipe_id", int(recipe_id))


def _get(ms, st, field):
    return getattr(ms, f"{st}_{field}")


def _set_cmd(ms, st, start=None, stop=None, reset=None):
    if start is not None:
        setattr(ms, f"{st}_cmd_start", 1 if start else 0)
    if stop is not None:
        setattr(ms, f"{st}_cmd_stop", 1 if stop else 0)
    if reset is not None:
        setattr(ms, f"{st}_cmd_reset", 1 if reset else 0)


def _stop_station(ms, st):
    _set_cmd(ms, st, start=0, stop=1, reset=0)


def _start_station(ms, st):
    _set_cmd(ms, st, start=1, stop=0, reset=0)


def _reset_station(ms, st):
    _set_cmd(ms, st, start=0, stop=1, reset=1)


def _stop_all(ms):
    for st in STATIONS:
        _stop_station(ms, st)


def _start_all(ms):
    for st in STATIONS:
        _start_station(ms, st)


def _reset_all(ms):
    for st in STATIONS:
        _reset_station(ms, st)


def _any_fault(ms):
    return any(_get(ms, st, "fault") for st in STATIONS)


def _done_edge(ms, st, prev_done):
    cur = 1 if _get(ms, st, "done") else 0
    edge = 1 if (cur == 1 and prev_done.get(st, 0) == 0) else 0
    prev_done[st] = cur
    return edge
# End of user custom code region.


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
        # coordinator internal state
        self._batch_id = 1
        self._recipe_id = 1

        # RESET_ALL -> RUN (or FAULT_RESET)
        self._state = "RESET_ALL"
        self._reset_ticks = 0

        # rising-edge tracking for done pulses from stations
        self._prev_done = {st: 0 for st in STATIONS}
        self._prev_busy = {st: 0 for st in STATIONS}
        self._pending_start = {st: False for st in STATIONS}

        # pipeline buffers
        self._buffers = {
            "S1_to_S2": 0,
            "S2_to_S3": 0,
            "S3_to_S4": 0,
            "S4_to_S5": 0,
            "S5_to_S6": 0,
        }

        # KPI totals for ST5
        self._s5_accept_total = 0
        self._s5_reject_total = 0

        # Store actual connection handles for sending commands
        self.station_handles = {
            "S1": 0,  # Will be updated when we receive first packet from ST1
            "S2": 0,
            "S3": 0,
            "S4": 0,
            "S5": 0,
            "S6": 0
        }
        # End of user custom code region.


    def mainThread(self):
        dSession = vsiCommonPythonApi.connectToServer(self.localHost, self.domain, self.portNum, self.componentId)
        vsiEthernetPythonGateway.initialize(dSession, self.componentId, bytes(srcMacAddress), bytes(srcIpAddress))
        try:
            vsiCommonPythonApi.waitForReset()

            # Start of user custom code region. Please apply edits only within these regions:  After Reset
            # Initialize pipeline controller state (Parallel DES)
            self._batch_id = 1
            self._recipe_id = 1

            # State machine
            self._state = "RESET_ALL"
            self._reset_ticks = 0

            # Virtual buffers (tokens) between stations
            self._buffers = {
                "S1_to_S2": 0,
                "S2_to_S3": 0,
                "S3_to_S4": 0,
                "S4_to_S5": 0,
                "S5_to_S6": 0,
            }

            # Edge trackers / latches
            self._prev_done = {st: 0 for st in STATIONS}
            self._prev_busy = {st: 0 for st in STATIONS}
            self._pending_start = {st: False for st in STATIONS}

            # KPI counters
            self._s5_accept_total = 0
            self._s5_reject_total = 0

            # Pulse reset on all stations at sim start
            _reset_all(self.mySignals)

            # Initial context
            for st in STATIONS:
                _set_context(self.mySignals, st, self._batch_id, self._recipe_id)

            # Reset station handles (learned from RX packets)
            for st in STATIONS:
                self.station_handles[st] = 0
            # End of user custom code region.
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
                ms = self.mySignals

                # Keep station context updated
                for st in STATIONS:
                    _set_context(ms, st, self._batch_id, self._recipe_id)

                # Done edges (one-shot)
                done_edges = {st: _done_edge(ms, st, self._prev_done) for st in STATIONS}

                # Helper: rising edge on busy
                def _busy_edge(st: str) -> bool:
                    cur = 1 if _get(ms, st, "busy") else 0
                    prev = self._prev_busy.get(st, 0)
                    return (cur == 1) and (prev == 0)

                # ---- FAULT handling ----
                if _any_fault(ms) and self._state != "FAULT_RESET":
                    self._state = "FAULT_RESET"
                    self._reset_ticks = 0

                # ---- RESET_ALL / FAULT_RESET ----
                if self._state in ("RESET_ALL", "FAULT_RESET"):
                    _reset_all(ms)
                    self._reset_ticks += 1

                    # Clear pipeline state while resetting
                    for k in self._buffers:
                        self._buffers[k] = 0
                    self._pending_start = {st: False for st in STATIONS}
                    self._prev_busy = {st: 0 for st in STATIONS}

                    if self._reset_ticks >= RESET_PULSE_TICKS:
                        # Deassert reset/stop when entering RUN
                        for st in STATIONS:
                            _set_cmd(ms, st, start=0, stop=0, reset=0)
                        self._state = "RUN"

                # ---- RUN: evaluate all stations every scan (parallel) ----
                if self._state == "RUN":
                    # Default outputs each scan
                    for st in STATIONS:
                        _set_cmd(ms, st, start=0, stop=0, reset=0)

                    # Snapshot inputs
                    cur_ready = {st: 1 if _get(ms, st, "ready") else 0 for st in STATIONS}
                    cur_busy  = {st: 1 if _get(ms, st, "busy")  else 0 for st in STATIONS}
                    cur_fault = {st: 1 if _get(ms, st, "fault") else 0 for st in STATIONS}

                    # Producer: done -> increment downstream buffer
                    if done_edges["S1"]:
                        self._buffers["S1_to_S2"] += 1
                    if done_edges["S2"]:
                        self._buffers["S2_to_S3"] += 1
                    if done_edges["S3"]:
                        self._buffers["S3_to_S4"] += 1
                    if done_edges["S4"]:
                        self._buffers["S4_to_S5"] += 1

                    # S5 -> S6 only if accepted (when flag exists)
                    if done_edges["S5"]:
                        if hasattr(ms, "S5_last_accept"):
                            if 1 if ms.S5_last_accept else 0:
                                self._buffers["S5_to_S6"] += 1
                        else:
                            self._buffers["S5_to_S6"] += 1

                    # Consumer start eligibility (important: don't hard-block on ready)
                    def _idle_ok(st: str) -> bool:
                        return (cur_busy[st] == 0) and (cur_fault[st] == 0)

                    def _can_start(st: str) -> bool:
                        # Some station models keep ready=0 until cmd_start latches.
                        # Allow start when idle, or when ready is already high.
                        return (cur_ready[st] == 1) or _idle_ok(st)

                    start_req = {st: False for st in STATIONS}

                    # S1 (source): start whenever idle
                    start_req["S1"] = _idle_ok("S1") and (not self._pending_start["S1"]) and _can_start("S1")

                    # Downstream stations: require buffer token + idle
                    start_req["S2"] = _idle_ok("S2") and (self._buffers["S1_to_S2"] > 0) and (not self._pending_start["S2"]) and _can_start("S2")
                    start_req["S3"] = _idle_ok("S3") and (self._buffers["S2_to_S3"] > 0) and (not self._pending_start["S3"]) and _can_start("S3")
                    start_req["S4"] = _idle_ok("S4") and (self._buffers["S3_to_S4"] > 0) and (not self._pending_start["S4"]) and _can_start("S4")

                    # Station 5 MUST wait for S4_to_S5 buffer
                    start_req["S5"] = _idle_ok("S5") and (self._buffers["S4_to_S5"] > 0) and (not self._pending_start["S5"]) and _can_start("S5")

                    start_req["S6"] = _idle_ok("S6") and (self._buffers["S5_to_S6"] > 0) and (not self._pending_start["S6"]) and _can_start("S6")

                    # Issue one-scan start pulses
                    for st in STATIONS:
                        if start_req[st]:
                            _set_cmd(ms, st, start=1, stop=0, reset=0)
                            self._pending_start[st] = True

                    # Token consumption: decrement only when station actually starts (busy rising edge)
                    if _busy_edge("S1") and self._pending_start["S1"]:
                        self._pending_start["S1"] = False

                    if _busy_edge("S2") and self._pending_start["S2"]:
                        self._buffers["S1_to_S2"] = max(0, self._buffers["S1_to_S2"] - 1)
                        self._pending_start["S2"] = False

                    if _busy_edge("S3") and self._pending_start["S3"]:
                        self._buffers["S2_to_S3"] = max(0, self._buffers["S2_to_S3"] - 1)
                        self._pending_start["S3"] = False

                    if _busy_edge("S4") and self._pending_start["S4"]:
                        self._buffers["S3_to_S4"] = max(0, self._buffers["S3_to_S4"] - 1)
                        self._pending_start["S4"] = False

                    if _busy_edge("S5") and self._pending_start["S5"]:
                        self._buffers["S4_to_S5"] = max(0, self._buffers["S4_to_S5"] - 1)
                        self._pending_start["S5"] = False

                    if _busy_edge("S6") and self._pending_start["S6"]:
                        self._buffers["S5_to_S6"] = max(0, self._buffers["S5_to_S6"] - 1)
                        self._pending_start["S6"] = False

                    # ST5 accept/reject counters
                    if done_edges["S5"]:
                        if hasattr(ms, "S5_last_accept") and (1 if ms.S5_last_accept else 0):
                            self._s5_accept_total += 1
                        else:
                            self._s5_reject_total += 1

                    # Batch increments when packaging completes
                    if done_edges["S6"]:
                        self._batch_id += 1

                    # Update busy edge trackers
                    for st in STATIONS:
                        self._prev_busy[st] = cur_busy[st]
                # End of user custom code region.
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

        # Print all listen handles for debugging
        print(f"PLC handles: ST1={self.clientPortNum[ST1_ComponentKitting0]}, ST2={self.clientPortNum[ST2_FrameCoreAssembly1]}, "
              f"ST3={self.clientPortNum[ST3_ElectronicsWiring2]}, ST4={self.clientPortNum[ST4_CalibrationTesting3]}, "
              f"ST5={self.clientPortNum[ST5_QualityInspection4]}, ST6={self.clientPortNum[ST6_PackagingDispatch5]}")

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

        # DEBUG: Print packet metadata
        print(f"PLC RX meta dest/src/len: {self.receivedDestPortNumber}, {self.receivedSrcPortNumber}, {self.receivedNumberOfBytes}")

        # Store the ST1 client handle when we receive a packet from ST1
        if(self.receivedDestPortNumber == PLC_LineCoordinatorSocketPortNumber0):
            print("Received packet from ST1_ComponentKitting")
            
            st1_handle = self.receivedSrcPortNumber
            if st1_handle != 0 and st1_handle != self.station_handles["S1"]:
                print(f"  Storing ST1 handle: {st1_handle} (was {self.station_handles['S1']})")
                self.station_handles["S1"] = st1_handle
            
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
            st2_handle = self.receivedSrcPortNumber
            if st2_handle != 0 and st2_handle != self.station_handles["S2"]:
                print(f"  Storing ST2 handle: {st2_handle}")
                self.station_handles["S2"] = st2_handle
                
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
            st3_handle = self.receivedSrcPortNumber
            if st3_handle != 0 and st3_handle != self.station_handles["S3"]:
                print(f"  Storing ST3 handle: {st3_handle}")
                self.station_handles["S3"] = st3_handle
                
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
            st4_handle = self.receivedSrcPortNumber
            if st4_handle != 0 and st4_handle != self.station_handles["S4"]:
                print(f"  Storing ST4 handle: {st4_handle}")
                self.station_handles["S4"] = st4_handle
                
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
            st5_handle = self.receivedSrcPortNumber
            if st5_handle != 0 and st5_handle != self.station_handles["S5"]:
                print(f"  Storing ST5 handle: {st5_handle}")
                self.station_handles["S5"] = st5_handle
                
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
            st6_handle = self.receivedSrcPortNumber
            if st6_handle != 0 and st6_handle != self.station_handles["S6"]:
                print(f"  Storing ST6 handle: {st6_handle}")
                self.station_handles["S6"] = st6_handle
                
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
        
        handle = self.station_handles["S1"]
        packet_len = len(bytesToSend)
        
        if handle == 0:
            print(f"PLC TX ST1: SKIPPING - no handle yet (need to receive from ST1 first)")
            return
            
        print(f"PLC TX ST1 via HANDLE: {handle}, size: {packet_len}")
        vsiEthernetPythonGateway.sendEthernetPacket(handle, bytes(bytesToSend))

    def sendEthernetPacketToST2_FrameCoreAssembly(self):
        bytesToSend = bytes()
        bytesToSend += self.packBytes('?', self.mySignals.S2_cmd_start)
        bytesToSend += self.packBytes('?', self.mySignals.S2_cmd_stop)
        bytesToSend += self.packBytes('?', self.mySignals.S2_cmd_reset)
        bytesToSend += self.packBytes('L', self.mySignals.S2_batch_id)
        bytesToSend += self.packBytes('H', self.mySignals.S2_recipe_id)
        handle = self.station_handles["S2"]
        if handle == 0:
            handle = self.clientPortNum[ST2_FrameCoreAssembly1]
        packet_len = len(bytesToSend)
        print(f"PLC TX ST2 via HANDLE: {handle}, size: {packet_len}")
        vsiEthernetPythonGateway.sendEthernetPacket(handle, bytes(bytesToSend))

    def sendEthernetPacketToST3_ElectronicsWiring(self):
        bytesToSend = bytes()
        bytesToSend += self.packBytes('?', self.mySignals.S3_cmd_start)
        bytesToSend += self.packBytes('?', self.mySignals.S3_cmd_stop)
        bytesToSend += self.packBytes('?', self.mySignals.S3_cmd_reset)
        bytesToSend += self.packBytes('L', self.mySignals.S3_batch_id)
        bytesToSend += self.packBytes('H', self.mySignals.S3_recipe_id)
        handle = self.station_handles["S3"]
        if handle == 0:
            handle = self.clientPortNum[ST3_ElectronicsWiring2]
        packet_len = len(bytesToSend)
        print(f"PLC TX ST3 via HANDLE: {handle}, size: {packet_len}")
        vsiEthernetPythonGateway.sendEthernetPacket(handle, bytes(bytesToSend))

    def sendEthernetPacketToST4_CalibrationTesting(self):
        bytesToSend = bytes()
        bytesToSend += self.packBytes('?', self.mySignals.S4_cmd_start)
        bytesToSend += self.packBytes('?', self.mySignals.S4_cmd_stop)
        bytesToSend += self.packBytes('?', self.mySignals.S4_cmd_reset)
        bytesToSend += self.packBytes('L', self.mySignals.S4_batch_id)
        bytesToSend += self.packBytes('H', self.mySignals.S4_recipe_id)
        handle = self.station_handles["S4"]
        if handle == 0:
            handle = self.clientPortNum[ST4_CalibrationTesting3]
        packet_len = len(bytesToSend)
        print(f"PLC TX ST4 via HANDLE: {handle}, size: {packet_len}")
        vsiEthernetPythonGateway.sendEthernetPacket(handle, bytes(bytesToSend))

    def sendEthernetPacketToST5_QualityInspection(self):
        bytesToSend = bytes()
        bytesToSend += self.packBytes('?', self.mySignals.S5_cmd_start)
        bytesToSend += self.packBytes('?', self.mySignals.S5_cmd_stop)
        bytesToSend += self.packBytes('?', self.mySignals.S5_cmd_reset)
        bytesToSend += self.packBytes('L', self.mySignals.S5_batch_id)
        bytesToSend += self.packBytes('H', self.mySignals.S5_recipe_id)
        handle = self.station_handles["S5"]
        if handle == 0:
            handle = self.clientPortNum[ST5_QualityInspection4]
        packet_len = len(bytesToSend)
        print(f"PLC TX ST5 via HANDLE: {handle}, size: {packet_len}")
        vsiEthernetPythonGateway.sendEthernetPacket(handle, bytes(bytesToSend))

    def sendEthernetPacketToST6_PackagingDispatch(self):
        bytesToSend = bytes()
        bytesToSend += self.packBytes('?', self.mySignals.S6_cmd_start)
        bytesToSend += self.packBytes('?', self.mySignals.S6_cmd_stop)
        bytesToSend += self.packBytes('?', self.mySignals.S6_cmd_reset)
        bytesToSend += self.packBytes('L', self.mySignals.S6_batch_id)
        bytesToSend += self.packBytes('H', self.mySignals.S6_recipe_id)
        handle = self.station_handles["S6"]
        if handle == 0:
            handle = self.clientPortNum[ST6_PackagingDispatch5]
        packet_len = len(bytesToSend)
        print(f"PLC TX ST6 via HANDLE: {handle}, size: {packet_len}")
        vsiEthernetPythonGateway.sendEthernetPacket(handle, bytes(bytesToSend))

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
