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

STATIONS = ["S1", "S2", "S3", "S4", "S5", "S6"]
RESET_PULSE_TICKS = 3

# Buffer constants
BUF_MAX = 2  # Increased buffer size for better pipeline flow


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

        # State machine
        self._state = "RESET_ALL"
        self._reset_ticks = 0
        self._run_enable = True  # Master enable for line

        # Previous done states for edge detection
        self._prev_done = {
            "S1": False, "S2": False, "S3": False, 
            "S4": False, "S5": False, "S6": False
        }
        
        # DONE LATCHES (Critical fix)
        self._done_latched = {
            "S1": False, "S2": False, "S3": False,
            "S4": False, "S5": False, "S6": False
        }
        
        # Start pulse sent flags (ensure one-shot)
        self._start_sent = {
            "S1": False, "S2": False, "S3": False,
            "S4": False, "S5": False, "S6": False
        }

        # pipeline buffers
        self._buffers = {
            "S1_to_S2": 0,
            "S2_to_S3": 0,
            "S3_to_S4": 0,
            "S4_to_S5": 0,
            "S5_to_S6": 0,
        }
        
        self.finished = 0  # Completed packages from S6

        # KPI totals for ST5
        self._s5_accept_total = 0
        self._s5_reject_total = 0

        # Store actual connection handles for sending commands
        self.station_handles = {
            "S1": 0,
            "S2": 0,
            "S3": 0,
            "S4": 0,
            "S5": 0,
            "S6": 0
        }
        
        # Debug counters and timers
        self._scan_count = 0
        self._sim_time_s = 0.0
        self._debug_override_active = False
        self._debug_override_done = False
        
        # Timeout counters for stuck stations
        self._s1_wait_counter = 0
        self._s2_wait_counter = 0
        self._s3_wait_counter = 0
        self._s4_wait_counter = 0
        self._s5_wait_counter = 0
        self._s6_wait_counter = 0
        
        # Previous busy states for edge detection
        self._prev_S1_busy = 0
        self._prev_S2_busy = 0
        self._prev_S3_busy = 0
        self._prev_S4_busy = 0
        self._prev_S5_busy = 0
        self._prev_S6_busy = 0
        # End of user custom code region.


    def mainThread(self):
        dSession = vsiCommonPythonApi.connectToServer(self.localHost, self.domain, self.portNum, self.componentId)
        vsiEthernetPythonGateway.initialize(dSession, self.componentId, bytes(srcMacAddress), bytes(srcIpAddress))
        try:
            vsiCommonPythonApi.waitForReset()

            # Start of user custom code region. Please apply edits only within these regions:  After Reset
            # Initialize pipeline controller state
            self._batch_id = 1
            self._recipe_id = 1

            # State machine
            self._state = "RESET_ALL"
            self._reset_ticks = 0
            self._run_enable = True

            # Virtual buffers (tokens) between stations
            self._buffers = {
                "S1_to_S2": 0,
                "S2_to_S3": 0,
                "S3_to_S4": 0,
                "S4_to_S5": 0,
                "S5_to_S6": 0,
            }
            self.finished = 0

            # Edge trackers
            self._prev_done = {st: False for st in STATIONS}
            self._done_latched = {st: False for st in STATIONS}
            self._start_sent = {st: False for st in STATIONS}
            self._scan_count = 0
            self._sim_time_s = 0.0
            self._debug_override_active = False
            self._debug_override_done = False

            # KPI counters
            self._s5_accept_total = 0
            self._s5_reject_total = 0
            
            # Timeout counters
            self._s1_wait_counter = 0
            self._s2_wait_counter = 0
            self._s3_wait_counter = 0
            self._s4_wait_counter = 0
            self._s5_wait_counter = 0
            self._s6_wait_counter = 0
            
            # Previous busy states
            self._prev_S1_busy = 0
            self._prev_S2_busy = 0
            self._prev_S3_busy = 0
            self._prev_S4_busy = 0
            self._prev_S5_busy = 0
            self._prev_S6_busy = 0

            # Pulse reset on all stations at sim start
            _reset_all(self.mySignals)

            # Initial context
            for st in STATIONS:
                _set_context(self.mySignals, st, self._batch_id, self._recipe_id)

            # Reset station handles (learned from RX packets)
            for st in STATIONS:
                self.station_handles[st] = 0
                
            print("PLC: Initialized with full 6-station state machine")
            print("PLC: Pipeline flow: S1 -> S2 -> S3 -> S4 -> S5 -> S6 -> FINISH")
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
                self._scan_count += 1
                self._sim_time_s = vsiCommonPythonApi.getSimulationTimeInNs() / 1e9

                # 1) PRINT PLC STATE EVERY SCAN
                print(f"\n=== PLC SCAN {self._scan_count} ===")
                print(f"PLC state={self._state} step={self.simulationStep}ns run_enable={self._run_enable}")
                print(f"Sim time: {self._sim_time_s:.3f}s")
                print(f"Buffers: S1->S2={self._buffers['S1_to_S2']}, S2->S3={self._buffers['S2_to_S3']}, "
                      f"S3->S4={self._buffers['S3_to_S4']}, S4->S5={self._buffers['S4_to_S5']}, S5->S6={self._buffers['S5_to_S6']}")
                print(f"Start sent: S1={self._start_sent['S1']}, S2={self._start_sent['S2']}, S3={self._start_sent['S3']}, "
                      f"S4={self._start_sent['S4']}, S5={self._start_sent['S5']}, S6={self._start_sent['S6']}")

                # Keep station context updated
                for st in STATIONS:
                    _set_context(ms, st, self._batch_id, self._recipe_id)

                # ---- FAULT handling ----
                if _any_fault(ms) and self._state != "FAULT_RESET":
                    print("PLC: Fault detected, entering FAULT_RESET state")
                    self._state = "FAULT_RESET"
                    self._reset_ticks = 0

                # ---- RESET_ALL / FAULT_RESET ----
                if self._state in ("RESET_ALL", "FAULT_RESET"):
                    _reset_all(ms)
                    self._reset_ticks += 1
                    print(f"PLC: In {self._state} state, tick {self._reset_ticks}/{RESET_PULSE_TICKS}")

                    # Clear pipeline state while resetting
                    for k in self._buffers:
                        self._buffers[k] = 0
                    self.finished = 0
                    self._done_latched = {st: False for st in STATIONS}
                    self._prev_done = {st: False for st in STATIONS}
                    self._start_sent = {st: False for st in STATIONS}
                    
                    # Clear busy edge tracking
                    self._prev_S1_busy = 0
                    self._prev_S2_busy = 0
                    self._prev_S3_busy = 0
                    self._prev_S4_busy = 0
                    self._prev_S5_busy = 0
                    self._prev_S6_busy = 0
                    
                    # Reset timeout counters
                    self._s1_wait_counter = 0
                    self._s2_wait_counter = 0
                    self._s3_wait_counter = 0
                    self._s4_wait_counter = 0
                    self._s5_wait_counter = 0
                    self._s6_wait_counter = 0

                    if self._reset_ticks >= RESET_PULSE_TICKS:
                        # Deassert reset/stop when entering RUN
                        for st in STATIONS:
                            _set_cmd(ms, st, start=0, stop=0, reset=0)
                        self._state = "WAIT_ALL_READY"
                        print("PLC: Entering WAIT_ALL_READY state")

                # ---- WAIT_ALL_READY: Wait for all stations to be ready ----
                elif self._state == "WAIT_ALL_READY":
                    # Check if all stations are ready (not busy, no fault)
                    all_ready = True
                    for st in STATIONS:  # Check ALL 6 stations
                        ready = _get(ms, st, "ready")
                        busy = _get(ms, st, "busy")
                        fault = _get(ms, st, "fault")
                        if not ready or busy or fault:
                            all_ready = False
                            print(f"  {st}: ready={ready}, busy={busy}, fault={fault} (NOT READY)")
                    
                    if all_ready:
                        print("PLC: All 6 stations ready, moving to START_S1")
                        self._state = "START_S1"
                        
                        # Initialize previous busy states
                        self._prev_S1_busy = _get(ms, "S1", "busy")
                        self._prev_S2_busy = _get(ms, "S2", "busy")
                        self._prev_S3_busy = _get(ms, "S3", "busy")
                        self._prev_S4_busy = _get(ms, "S4", "busy")
                        self._prev_S5_busy = _get(ms, "S5", "busy")
                        self._prev_S6_busy = _get(ms, "S6", "busy")
                    else:
                        print("PLC: Waiting for stations to be ready...")

                # ---- START_S1: Send start pulse to S1 ----
                elif self._state == "START_S1":
                    # Clear all start commands first
                    for st in STATIONS:
                        _set_cmd(ms, st, start=0, stop=0, reset=0)
                    
                    # Check current S1 state
                    s1_ready = _get(ms, "S1", "ready")
                    s1_busy = _get(ms, "S1", "busy")
                    s1_fault = _get(ms, "S1", "fault")
                    
                    print(f"  S1 start check: ready={s1_ready}, busy={s1_busy}, fault={s1_fault}")
                    
                    # FIX: Check if S1 is already busy (station started autonomously)
                    if s1_busy and not self._start_sent["S1"]:
                        print("PLC: S1 already busy in START_S1 -> forcing WAIT")
                        self._start_sent["S1"] = True
                        self._state = "WAIT_S1_DONE"
                    elif (s1_ready and not s1_busy and not s1_fault):
                        if not self._start_sent["S1"]:
                            print("PLC: START pulse -> S1")
                            _set_cmd(ms, "S1", start=1, stop=0, reset=0)
                            self._start_sent["S1"] = True
                            self._state = "WAIT_S1_DONE"
                        else:
                            print("PLC: S1 start already sent, waiting...")
                    else:
                        print(f"PLC: S1 not ready to start")

                # ---- WAIT_S1_DONE: Wait for S1 to complete ----
                elif self._state == "WAIT_S1_DONE":
                    # CRITICAL FIX: Clear S1 start command - it should be a ONE-SHOT pulse
                    _set_cmd(ms, "S1", start=0, stop=0, reset=0)
                    
                    # Latch S1 done
                    s1_done = _get(ms, "S1", "done")
                    s1_busy = _get(ms, "S1", "busy")
                    s1_ready = _get(ms, "S1", "ready")
                    s1_fault = _get(ms, "S1", "fault")
                    
                    # Detect busy edges
                    s1_busy_rise = (not self._prev_S1_busy) and s1_busy
                    s1_busy_fall = self._prev_S1_busy and (not s1_busy)
                    
                    if s1_done and not self._done_latched["S1"]:
                        print("PLC: S1 DONE detected (raw) -> latching")
                        self._done_latched["S1"] = True
                    
                    # BACKUP COMPLETION: If start was sent, busy falls, station is ready and no fault
                    backup_complete = (self._start_sent["S1"] and s1_busy_fall and 
                                      s1_ready and not s1_fault)
                    
                    # Timeout counter
                    self._s1_wait_counter += 1
                    
                    completion_condition = (self._done_latched["S1"] or backup_complete)
                    
                    if completion_condition and s1_ready and not s1_busy and not s1_fault:
                        if backup_complete:
                            print("PLC: S1 BUSY_FALL completion detected")
                        else:
                            print("PLC: S1 done latched -> advancing to START_S2")
                        
                        self._done_latched["S1"] = False
                        self._start_sent["S1"] = False
                        self._state = "START_S2"
                        self._s1_wait_counter = 0
                        
                        # Buffer S1->S2
                        self._buffers["S1_to_S2"] = min(self._buffers["S1_to_S2"] + 1, BUF_MAX)
                        print(f"PLC: Incremented S1_to_S2 buffer to {self._buffers['S1_to_S2']}")
                    elif self._s1_wait_counter > 15:  #~10 seconds for S1
                        print(f"PLC: TIMEOUT - S1 stuck for {self._s1_wait_counter} scans, forcing advance")
                        self._done_latched["S1"] = False
                        self._start_sent["S1"] = False
                        self._state = "START_S2"
                        self._s1_wait_counter = 0
                        # Assume work was done
                        self._buffers["S1_to_S2"] = min(self._buffers["S1_to_S2"] + 1, BUF_MAX)
                        print(f"PLC: Forced S1_to_S2 buffer to {self._buffers['S1_to_S2']}")
                    else:
                        print(f"  WAIT_S1_DONE: done_latched={self._done_latched['S1']}, busy={s1_busy}, ready={s1_ready}, "
                              f"busy_fall={s1_busy_fall}, start_sent={self._start_sent['S1']}, timeout={self._s1_wait_counter}/15")
                    
                    # Update previous busy state
                    self._prev_S1_busy = s1_busy

                # ---- START_S2: Send start pulse to S2 ----
                elif self._state == "START_S2":
                    # Clear all start commands
                    for st in STATIONS:
                        _set_cmd(ms, st, start=0, stop=0, reset=0)
                    
                    # Check if S2 is ready and has work from S1
                    s2_ready = _get(ms, "S2", "ready")
                    s2_busy = _get(ms, "S2", "busy")
                    s2_fault = _get(ms, "S2", "fault")
                    
                    print(f"  S2 start check: ready={s2_ready}, busy={s2_busy}, fault={s2_fault}, buffer={self._buffers['S1_to_S2']}")
                    
                    # FIX: Check if S2 is already busy (station started autonomously)
                    if s2_busy and not self._start_sent["S2"]:
                        print("PLC: S2 already busy in START_S2 -> forcing WAIT")
                        self._start_sent["S2"] = True
                        self._state = "WAIT_S2_DONE"
                    elif (s2_ready and not s2_busy and not s2_fault and self._buffers["S1_to_S2"] > 0):
                        if not self._start_sent["S2"]:
                            print("PLC: START pulse -> S2")
                            _set_cmd(ms, "S2", start=1, stop=0, reset=0)
                            self._start_sent["S2"] = True
                            self._state = "WAIT_S2_DONE"
                            
                            # Consume buffer
                            self._buffers["S1_to_S2"] = max(0, self._buffers["S1_to_S2"] - 1)
                            print(f"PLC: Consumed S1_to_S2 buffer, now {self._buffers['S1_to_S2']}")
                        else:
                            print("PLC: S2 start already sent, waiting...")
                    else:
                        print(f"PLC: S2 not ready to start")

                # ---- WAIT_S2_DONE: Wait for S2 to complete ----
                elif self._state == "WAIT_S2_DONE":
                    # CRITICAL FIX: Clear S2 start command - it should be a ONE-SHOT pulse
                    _set_cmd(ms, "S2", start=0, stop=0, reset=0)
                    
                    # Latch S2 done
                    s2_done = _get(ms, "S2", "done")
                    s2_busy = _get(ms, "S2", "busy")
                    s2_ready = _get(ms, "S2", "ready")
                    s2_fault = _get(ms, "S2", "fault")
                    
                    # Detect busy edges
                    s2_busy_rise = (not self._prev_S2_busy) and s2_busy
                    s2_busy_fall = self._prev_S2_busy and (not s2_busy)
                    
                    if s2_done and not self._done_latched["S2"]:
                        print("PLC: S2 DONE detected (raw) -> latching")
                        self._done_latched["S2"] = True
                    
                    # BACKUP COMPLETION: If start was sent, busy falls, station is ready and no fault
                    backup_complete = (self._start_sent["S2"] and s2_busy_fall and 
                                      s2_ready and not s2_fault)
                    
                    # Timeout counter
                    self._s2_wait_counter += 1
                    
                    completion_condition = (self._done_latched["S2"] or backup_complete)
                    
                    if completion_condition and s2_ready and not s2_busy and not s2_fault:
                        if backup_complete:
                            print("PLC: S2 BUSY_FALL completion detected")
                        else:
                            print("PLC: S2 done latched -> advancing to START_S3")
                        
                        self._done_latched["S2"] = False
                        self._start_sent["S2"] = False
                        self._state = "START_S3"
                        self._s2_wait_counter = 0
                        
                        # Buffer S2->S3
                        self._buffers["S2_to_S3"] = min(self._buffers["S2_to_S3"] + 1, BUF_MAX)
                        print(f"PLC: Incremented S2_to_S3 buffer to {self._buffers['S2_to_S3']}")
                    elif self._s2_wait_counter > 15:  # 13 sec
                        print(f"PLC: TIMEOUT - S2 stuck for {self._s2_wait_counter} scans, forcing advance")
                        self._done_latched["S2"] = False
                        self._start_sent["S2"] = False
                        self._state = "START_S3"
                        self._s2_wait_counter = 0
                        self._buffers["S2_to_S3"] = min(self._buffers["S2_to_S3"] + 1, BUF_MAX)
                        print(f"PLC: Forced S2_to_S3 buffer to {self._buffers['S2_to_S3']}")
                    else:
                        print(f"  WAIT_S2_DONE: done_latched={self._done_latched['S2']}, busy={s2_busy}, ready={s2_ready}, "
                              f"busy_fall={s2_busy_fall}, start_sent={self._start_sent['S2']}, timeout={self._s2_wait_counter}/15")
                    
                    # Update previous busy state
                    self._prev_S2_busy = s2_busy

                # ---- START_S3: Send start pulse to S3 ----
                elif self._state == "START_S3":
                    # Clear all start commands
                    for st in STATIONS:
                        _set_cmd(ms, st, start=0, stop=0, reset=0)
                    
                    # Check if S3 is ready and has work from S2
                    s3_ready = _get(ms, "S3", "ready")
                    s3_busy = _get(ms, "S3", "busy")
                    s3_fault = _get(ms, "S3", "fault")
                    
                    print(f"  S3 start check: ready={s3_ready}, busy={s3_busy}, fault={s3_fault}, buffer={self._buffers['S2_to_S3']}")
                    
                    # FIX: Check if S3 is already busy (station started autonomously)
                    if s3_busy and not self._start_sent["S3"]:
                        print("PLC: S3 already busy in START_S3 -> forcing WAIT")
                        self._start_sent["S3"] = True
                        self._state = "WAIT_S3_DONE"
                    elif (s3_ready and not s3_busy and not s3_fault and self._buffers["S2_to_S3"] > 0):
                        if not self._start_sent["S3"]:
                            print("PLC: START pulse -> S3")
                            _set_cmd(ms, "S3", start=1, stop=0, reset=0)
                            self._start_sent["S3"] = True
                            self._state = "WAIT_S3_DONE"
                            
                            # Consume buffer
                            self._buffers["S2_to_S3"] = max(0, self._buffers["S2_to_S3"] - 1)
                            print(f"PLC: Consumed S2_to_S3 buffer, now {self._buffers['S2_to_S3']}")
                        else:
                            print("PLC: S3 start already sent, waiting...")
                    else:
                        print(f"PLC: S3 not ready to start")

                # ---- WAIT_S3_DONE: Wait for S3 to complete ----
                elif self._state == "WAIT_S3_DONE":
                    # CRITICAL FIX: Clear S3 start command - it should be a ONE-SHOT pulse
                    _set_cmd(ms, "S3", start=0, stop=0, reset=0)
                    
                    # Latch S3 done
                    s3_done = _get(ms, "S3", "done")
                    s3_busy = _get(ms, "S3", "busy")
                    s3_ready = _get(ms, "S3", "ready")
                    s3_fault = _get(ms, "S3", "fault")
                    
                    # Detect busy edges
                    s3_busy_rise = (not self._prev_S3_busy) and s3_busy
                    s3_busy_fall = self._prev_S3_busy and (not s3_busy)
                    
                    if s3_done and not self._done_latched["S3"]:
                        print("PLC: S3 DONE detected (raw) -> latching")
                        self._done_latched["S3"] = True
                    
                    # BACKUP COMPLETION: If start was sent, busy falls, station is ready and no fault
                    backup_complete = (self._start_sent["S3"] and s3_busy_fall and 
                                      s3_ready and not s3_fault)
                    
                    # Timeout counter
                    self._s3_wait_counter += 1
                    
                    completion_condition = (self._done_latched["S3"] or backup_complete)
                    
                    if completion_condition and s3_ready and not s3_busy and not s3_fault:
                        if backup_complete:
                            print("PLC: S3 BUSY_FALL completion detected")
                        else:
                            print("PLC: S3 done latched -> advancing to START_S4")
                        
                        self._done_latched["S3"] = False
                        self._start_sent["S3"] = False
                        self._state = "START_S4"
                        self._s3_wait_counter = 0
                        
                        # Buffer S3->S4
                        self._buffers["S3_to_S4"] = min(self._buffers["S3_to_S4"] + 1, BUF_MAX)
                        print(f"PLC: Incremented S3_to_S4 buffer to {self._buffers['S3_to_S4']}")
                    elif self._s3_wait_counter > 15:  # Timeout
                        print(f"PLC: TIMEOUT - S3 stuck for {self._s3_wait_counter} scans, forcing advance")
                        self._done_latched["S3"] = False
                        self._start_sent["S3"] = False
                        self._state = "START_S4"
                        self._s3_wait_counter = 0
                        self._buffers["S3_to_S4"] = min(self._buffers["S3_to_S4"] + 1, BUF_MAX)
                        print(f"PLC: Forced S3_to_S4 buffer to {self._buffers['S3_to_S4']}")
                    else:
                        print(f"  WAIT_S3_DONE: done_latched={self._done_latched['S3']}, busy={s3_busy}, ready={s3_ready}, "
                              f"busy_fall={s3_busy_fall}, start_sent={self._start_sent['S3']}, timeout={self._s3_wait_counter}/15")
                    
                    # Update previous busy state
                    self._prev_S3_busy = s3_busy

                # ---- START_S4: Send start pulse to S4 ----
                elif self._state == "START_S4":
                    # Clear all start commands
                    for st in STATIONS:
                        _set_cmd(ms, st, start=0, stop=0, reset=0)
                    
                    # Check if S4 is ready and has work from S3
                    s4_ready = _get(ms, "S4", "ready")
                    s4_busy = _get(ms, "S4", "busy")
                    s4_fault = _get(ms, "S4", "fault")
                    
                    print(f"  S4 start check: ready={s4_ready}, busy={s4_busy}, fault={s4_fault}, buffer={self._buffers['S3_to_S4']}")
                    
                    # FIX: Check if S4 is already busy (station started autonomously)
                    if s4_busy and not self._start_sent["S4"]:
                        print("PLC: S4 already busy in START_S4 -> forcing WAIT")
                        self._start_sent["S4"] = True
                        self._state = "WAIT_S4_DONE"
                    elif (s4_ready and not s4_busy and not s4_fault and self._buffers["S3_to_S4"] > 0):
                        if not self._start_sent["S4"]:
                            print("PLC: START pulse -> S4")
                            _set_cmd(ms, "S4", start=1, stop=0, reset=0)
                            self._start_sent["S4"] = True
                            self._state = "WAIT_S4_DONE"
                            
                            # Consume buffer
                            self._buffers["S3_to_S4"] = max(0, self._buffers["S3_to_S4"] - 1)
                            print(f"PLC: Consumed S3_to_S4 buffer, now {self._buffers['S3_to_S4']}")
                        else:
                            print("PLC: S4 start already sent, waiting...")
                    else:
                        print(f"PLC: S4 not ready to start")

                # ---- WAIT_S4_DONE: Wait for S4 to complete ----
                elif self._state == "WAIT_S4_DONE":
                    # CRITICAL FIX: Clear S4 start command - it should be a ONE-SHOT pulse
                    _set_cmd(ms, "S4", start=0, stop=0, reset=0)
                    
                    # Latch S4 done
                    s4_done = _get(ms, "S4", "done")
                    s4_busy = _get(ms, "S4", "busy")
                    s4_ready = _get(ms, "S4", "ready")
                    s4_fault = _get(ms, "S4", "fault")
                    
                    # Detect busy edges
                    s4_busy_rise = (not self._prev_S4_busy) and s4_busy
                    s4_busy_fall = self._prev_S4_busy and (not s4_busy)
                    
                    if s4_done and not self._done_latched["S4"]:
                        print("PLC: S4 DONE detected (raw) -> latching")
                        self._done_latched["S4"] = True
                    
                    # BACKUP COMPLETION: If start was sent, busy falls, station is ready and no fault
                    backup_complete = (self._start_sent["S4"] and s4_busy_fall and 
                                      s4_ready and not s4_fault)
                    
                    # Timeout counter
                    self._s4_wait_counter += 1
                    
                    completion_condition = (self._done_latched["S4"] or backup_complete)
                    
                    if completion_condition and s4_ready and not s4_busy and not s4_fault:
                        if backup_complete:
                            print("PLC: S4 BUSY_FALL completion detected")
                        else:
                            print("PLC: S4 done latched -> advancing to START_S5")
                        
                        self._done_latched["S4"] = False
                        self._start_sent["S4"] = False
                        self._state = "START_S5"
                        self._s4_wait_counter = 0
                        
                        # Buffer S4->S5
                        self._buffers["S4_to_S5"] = min(self._buffers["S4_to_S5"] + 1, BUF_MAX)
                        print(f"PLC: Incremented S4_to_S5 buffer to {self._buffers['S4_to_S5']}")
                    elif self._s4_wait_counter > 30:  # Timeout
                        print(f"PLC: TIMEOUT - S4 stuck for {self._s4_wait_counter} scans, forcing advance")
                        self._done_latched["S4"] = False
                        self._start_sent["S4"] = False
                        self._state = "START_S5"
                        self._s4_wait_counter = 0
                        # FIX: Increment buffer on timeout to prevent deadlock
                        self._buffers["S4_to_S5"] = min(self._buffers["S4_to_S5"] + 1, BUF_MAX)
                        print(f"PLC: Forced S4_to_S5 buffer increment, now at {self._buffers['S4_to_S5']}")
                    else:
                        print(f"  WAIT_S4_DONE: done_latched={self._done_latched['S4']}, busy={s4_busy}, ready={s4_ready}, "
                              f"busy_fall={s4_busy_fall}, start_sent={self._start_sent['S4']}, timeout={self._s4_wait_counter}/30")
                    
                    # Update previous busy state
                    self._prev_S4_busy = s4_busy

                # ---- START_S5: Send start pulse to S5 ----
                elif self._state == "START_S5":
                    # Clear all start commands
                    for st in STATIONS:
                        _set_cmd(ms, st, start=0, stop=0, reset=0)
                    
                    # Check if S5 is ready and has work from S4
                    s5_ready = _get(ms, "S5", "ready")
                    s5_busy = _get(ms, "S5", "busy")
                    s5_fault = _get(ms, "S5", "fault")
                    
                    print(f"  S5 start check: ready={s5_ready}, busy={s5_busy}, fault={s5_fault}, buffer={self._buffers['S4_to_S5']}")
                    
                    # FIX: Check if S5 is already busy (station started autonomously)
                    if s5_busy and not self._start_sent["S5"]:
                        print("PLC: S5 already busy in START_S5 -> forcing WAIT")
                        self._start_sent["S5"] = True
                        self._state = "WAIT_S5_DONE"
                    elif self._buffers["S4_to_S5"] == 0:
                        print(f"PLC: No work: S4_to_S5 buffer empty")
                    elif (s5_ready and not s5_busy and not s5_fault and self._buffers["S4_to_S5"] > 0):
                        if not self._start_sent["S5"]:
                            print("PLC: START pulse -> S5")
                            _set_cmd(ms, "S5", start=1, stop=0, reset=0)
                            self._start_sent["S5"] = True
                            self._state = "WAIT_S5_DONE"
                            
                            # Consume buffer
                            self._buffers["S4_to_S5"] = max(0, self._buffers["S4_to_S5"] - 1)
                            print(f"PLC: Consumed S4_to_S5 buffer, now {self._buffers['S4_to_S5']}")
                        else:
                            print("PLC: S5 start already sent, waiting...")
                    else:
                        print(f"PLC: S5 not ready to start (buffer={self._buffers['S4_to_S5']}, ready={s5_ready}, busy={s5_busy}, fault={s5_fault})")

                # ---- WAIT_S5_DONE: Wait for S5 to complete ----
                elif self._state == "WAIT_S5_DONE":
                    # CRITICAL FIX: Clear S5 start command - it should be a ONE-SHOT pulse
                    _set_cmd(ms, "S5", start=0, stop=0, reset=0)
                    
                    # Latch S5 done
                    s5_done = _get(ms, "S5", "done")
                    s5_busy = _get(ms, "S5", "busy")
                    s5_ready = _get(ms, "S5", "ready")
                    s5_fault = _get(ms, "S5", "fault")
                    
                    # Detect busy edges
                    s5_busy_rise = (not self._prev_S5_busy) and s5_busy
                    s5_busy_fall = self._prev_S5_busy and (not s5_busy)
                    
                    if s5_done and not self._done_latched["S5"]:
                        print("PLC: S5 DONE detected (raw) -> latching")
                        self._done_latched["S5"] = True
                    
                    # BACKUP COMPLETION: If start was sent, busy falls, station is ready and no fault
                    backup_complete = (self._start_sent["S5"] and s5_busy_fall and 
                                      s5_ready and not s5_fault)
                    
                    # Timeout counter
                    self._s5_wait_counter += 1
                    
                    completion_condition = (self._done_latched["S5"] or backup_complete)
                    
                    if completion_condition and s5_ready and not s5_busy and not s5_fault:
                        if backup_complete:
                            print("PLC: S5 BUSY_FALL completion detected")
                        else:
                            print("PLC: S5 done latched -> advancing to START_S6")
                        
                        self._done_latched["S5"] = False
                        self._start_sent["S5"] = False
                        self._state = "START_S6"
                        self._s5_wait_counter = 0
                        
                        # Buffer S5->S6
                        self._buffers["S5_to_S6"] = min(self._buffers["S5_to_S6"] + 1, BUF_MAX)
                        print(f"PLC: Incremented S5_to_S6 buffer to {self._buffers['S5_to_S6']}")
                    elif self._s5_wait_counter > 200:  # Increased timeout for ST5 (200 scans ≈ 20-40 seconds)
                        print(f"PLC: TIMEOUT - S5 stuck for {self._s5_wait_counter} scans, forcing advance")
                        self._done_latched["S5"] = False
                        self._start_sent["S5"] = False
                        self._state = "START_S6"
                        self._s5_wait_counter = 0
                        self._buffers["S5_to_S6"] = min(self._buffers["S5_to_S6"] + 1, BUF_MAX)
                        print(f"PLC: Forced S5_to_S6 buffer to {self._buffers['S5_to_S6']}")
                    else:
                        print(f"  WAIT_S5_DONE: done_latched={self._done_latched['S5']}, busy={s5_busy}, ready={s5_ready}, "
                              f"busy_fall={s5_busy_fall}, start_sent={self._start_sent['S5']}, timeout={self._s5_wait_counter}/200")
                    
                    # Update previous busy state
                    self._prev_S5_busy = s5_busy

                # ---- START_S6: Send start pulse to S6 ----
                elif self._state == "START_S6":
                    # Clear all start commands
                    for st in STATIONS:
                        _set_cmd(ms, st, start=0, stop=0, reset=0)
                    
                    # Check if S6 is ready and has work from S5
                    s6_ready = _get(ms, "S6", "ready")
                    s6_busy = _get(ms, "S6", "busy")
                    s6_fault = _get(ms, "S6", "fault")
                    
                    print(f"  S6 start check: ready={s6_ready}, busy={s6_busy}, fault={s6_fault}, buffer={self._buffers['S5_to_S6']}")
                    
                    # FIX: Check if S6 is already busy (station started autonomously)
                    if s6_busy and not self._start_sent["S6"]:
                        print("PLC: S6 already busy in START_S6 -> forcing WAIT")
                        self._start_sent["S6"] = True
                        self._state = "WAIT_S6_DONE"
                    elif (s6_ready and not s6_busy and not s6_fault and self._buffers["S5_to_S6"] > 0):
                        if not self._start_sent["S6"]:
                            print("PLC: START pulse -> S6")
                            _set_cmd(ms, "S6", start=1, stop=0, reset=0)
                            self._start_sent["S6"] = True
                            self._state = "WAIT_S6_DONE"
                            
                            # Consume buffer
                            self._buffers["S5_to_S6"] = max(0, self._buffers["S5_to_S6"] - 1)
                            print(f"PLC: Consumed S5_to_S6 buffer, now {self._buffers['S5_to_S6']}")
                        else:
                            print("PLC: S6 start already sent, waiting...")
                    else:
                        print(f"PLC: S6 not ready to start")

                # ---- WAIT_S6_DONE: Wait for S6 to complete ----
                elif self._state == "WAIT_S6_DONE":
                    # CRITICAL FIX: Clear S6 start command - it should be a ONE-SHOT pulse
                    _set_cmd(ms, "S6", start=0, stop=0, reset=0)
                    
                    # Latch S6 done
                    s6_done = _get(ms, "S6", "done")
                    s6_busy = _get(ms, "S6", "busy")
                    s6_ready = _get(ms, "S6", "ready")
                    s6_fault = _get(ms, "S6", "fault")
                    
                    # Detect busy edges
                    s6_busy_rise = (not self._prev_S6_busy) and s6_busy
                    s6_busy_fall = self._prev_S6_busy and (not s6_busy)
                    
                    if s6_done and not self._done_latched["S6"]:
                        print("PLC: S6 DONE detected (raw) -> latching")
                        self._done_latched["S6"] = True
                    
                    # BACKUP COMPLETION: If start was sent, busy falls, station is ready and no fault
                    backup_complete = (self._start_sent["S6"] and s6_busy_fall and 
                                      s6_ready and not s6_fault)
                    
                    # Timeout counter
                    self._s6_wait_counter += 1
                    
                    completion_condition = (self._done_latched["S6"] or backup_complete)
                    
                    if completion_condition and s6_ready and not s6_busy and not s6_fault:
                        if backup_complete:
                            print("PLC: S6 BUSY_FALL completion detected")
                        else:
                            print("PLC: S6 done latched -> FULL CYCLE COMPLETE")
                        
                        self._done_latched["S6"] = False
                        self._start_sent["S6"] = False
                        self._s6_wait_counter = 0
                        
                        # Increment batch and finished count
                        self._batch_id += 1
                        self.finished += 1
                        
                        # Update KPI totals from S5
                        self._s5_accept_total += _get(ms, "S5", "accept")
                        self._s5_reject_total += _get(ms, "S5", "reject")
                        
                        print(f"PLC: Batch {self._batch_id-1} complete, finished products: {self.finished}")
                        print(f"PLC: S5 Accept/Reject totals: {self._s5_accept_total}/{self._s5_reject_total}")
                        
                        # Go back to START_S1 for next unit
                        self._state = "START_S1"
                        print("PLC: Restarting pipeline with next unit")
                    elif self._s6_wait_counter > 300:  # Increased timeout for ST6 (300 scans ≈ 30-60 seconds)
                        print(f"PLC: TIMEOUT - S6 stuck for {self._s6_wait_counter} scans, forcing cycle complete")
                        self._done_latched["S6"] = False
                        self._start_sent["S6"] = False
                        self._s6_wait_counter = 0
                        self._batch_id += 1
                        self.finished += 1
                        self._state = "START_S1"
                        print(f"PLC: Forced batch {self._batch_id-1} complete, restarting")
                    else:
                        print(f"  WAIT_S6_DONE: done_latched={self._done_latched['S6']}, busy={s6_busy}, ready={s6_ready}, "
                              f"busy_fall={s6_busy_fall}, start_sent={self._start_sent['S6']}, timeout={self._s6_wait_counter}/300")
                    
                    # Update previous busy state
                    self._prev_S6_busy = s6_busy
                
                # Update DONE LATCHES (safety catch)
                for st in STATIONS:
                    if _get(ms, st, "done"):
                        if not self._done_latched[st]:
                            print(f"PLC: Safety latch for {st} done")
                            self._done_latched[st] = True
                
                # Update previous done states for edge detection
                for st in STATIONS:
                    self._prev_done[st] = (_get(ms, st, "done") == 1)

                # 2) PRINT WHAT PLC IS TRANSMITTING
                print("TX commands:")
                for st in STATIONS:
                    start = getattr(self.mySignals, f"{st}_cmd_start")
                    reset = getattr(self.mySignals, f"{st}_cmd_reset")
                    stop = getattr(self.mySignals, f"{st}_cmd_stop")
                    print(f"  TX {st} start={start} reset={reset} stop={stop}")
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
                print(f"  PLC State: {self._state}")
                print(f"  Done latches: S1={self._done_latched['S1']}, S2={self._done_latched['S2']}, S3={self._done_latched['S3']}, "
                      f"S4={self._done_latched['S4']}, S5={self._done_latched['S5']}, S6={self._done_latched['S6']}")
                print(f"  Start sent: S1={self._start_sent['S1']}, S2={self._start_sent['S2']}, S3={self._start_sent['S3']}, "
                      f"S4={self._start_sent['S4']}, S5={self._start_sent['S5']}, S6={self._start_sent['S6']}")
                print(f"  Buffers: S1->S2={self._buffers['S1_to_S2']}, S2->S3={self._buffers['S2_to_S3']}, "
                      f"S3->S4={self._buffers['S3_to_S4']}, S4->S5={self._buffers['S4_to_S5']}, S5->S6={self._buffers['S5_to_S6']}")
                print(f"  Finished products: {self.finished}")
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
        vsiEthernetPythonGateway.sendEthernetPacket(handle, bytes(bytesToSend))



    def packBytes(self, signalType, signal):
        if isinstance(signal, list):
            if signalType == 's':
                packedData = b''
                for str in signal:
                    str += '\0'
                    str = str.encode('utf-8')
                    packedData += struct.pack(f'={len(str)}s', *str)
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

    pLC_LineCoordinator = PLC_LineCoordinator(args)
    pLC_LineCoordinator.mainThread()



if __name__ == '__main__':
    main()
