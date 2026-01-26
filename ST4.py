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
        self.total = 0
        self.completed = 0


srcMacAddress = [0x02, 0x00, 0x00, 0x00, 0x00, 0x14]
PLC_LineCoordinatorMacAddress = [0x02, 0x00, 0x00, 0x00, 0x00, 0x01]
srcIpAddress = [10, 10, 0, 14]
PLC_LineCoordinatorIpAddress = [10, 10, 0, 1]

PLC_LineCoordinatorSocketPortNumber0 = 6004
ST4_CalibrationTesting0 = 0


# Start of user custom code region. Please apply edits only within these regions:  Global Variables & Definitions

import random
from dataclasses import dataclass
from typing import Optional, Dict, Any

RANDOM_SEED_ST4 = 7

# Stages (seconds)
T_MOTION_S = 2.0
T_THERMAL_S = 18.0
T_CALIBRATION_S = 6.0
T_TESTPRINT_S = 15.0

# Pass probability (after calibration/test)
P_PASS = 0.93
P_PASS_AFTER_RETRY = 0.97
T_RETRY_S = 5.0


@dataclass
class ST4Job:
    batch_id: int
    recipe_id: int


def st4_cycle_generator(rng: random.Random):
    """
    Generator-based station cycle:
    yields stage durations so runtime can consume time in small dt steps.
    returns (passed: bool, cycle_time_s: float).
    """
    cycle_time_s = 0.0

    # stage: motion
    dt = T_MOTION_S
    cycle_time_s += dt
    yield ("MOTION", dt)

    # stage: thermal
    dt = T_THERMAL_S
    cycle_time_s += dt
    yield ("THERMAL", dt)

    # stage: calibration
    dt = T_CALIBRATION_S
    cycle_time_s += dt
    yield ("CALIBRATION", dt)

    # stage: test print
    dt = T_TESTPRINT_S
    cycle_time_s += dt
    yield ("TESTPRINT", dt)

    # pass/fail
    passed = (rng.random() <= P_PASS)
    if not passed:
        # retry stage
        dt = T_RETRY_S
        cycle_time_s += dt
        yield ("RETRY", dt)
        passed = (rng.random() <= P_PASS_AFTER_RETRY)

    # signal completion via StopIteration.value
    return (passed, cycle_time_s)


class ST4_SimRuntime:
    """
    ST1-style wrapper but with a generator job (yield-based).
    - enabled is driven by run_latched in VSI mainThread
    - on enabled & idle -> start one cycle
    - done is 1 tick pulse
    - fault is latched until reset
    """
    def __init__(self):
        self.rng = random.Random(RANDOM_SEED_ST4)

        self.enabled = False
        self.batch_id = 0
        self.recipe_id = 0

        self.total = 0
        self.completed = 0

        self._busy = False
        self._fault_latched = False

        self._job: Optional[ST4Job] = None
        self._job_gen = None
        self._stage_name = "IDLE"
        self._stage_remaining_s = 0.0
        self._job_cycle_time_ms = 0

        self._done_pulse_for_output = False
        self._done_seen_prev_tick = False

        # for debug / snapshot
        self._env_now_s = 0.0

    def reset(self):
        self.__init__()
        print("  ST4_SimRuntime: Full reset")

    def set_enabled(self, enabled: bool):
        self.enabled = bool(enabled)

    def set_context(self, batch_id: int, recipe_id: int):
        self.batch_id = int(batch_id)
        self.recipe_id = int(recipe_id)

    def _start_job_if_needed(self):
        if self._fault_latched:
            return
        if not self.enabled:
            return
        if self._busy:
            return

        # start one job per enable when idle
        self._job = ST4Job(batch_id=self.batch_id, recipe_id=self.recipe_id)
        self.total += 1
        self._busy = True
        self._done_pulse_for_output = False
        self._done_seen_prev_tick = False

        self._job_gen = st4_cycle_generator(self.rng)

        # prime generator to first yielded stage
        try:
            self._stage_name, self._stage_remaining_s = next(self._job_gen)
        except StopIteration as e:
            # extremely unlikely (no stages)
            passed, cycle_s = (True, 0.0)
            self._finish_job(passed, cycle_s)

        print(f"  ST4: Job started batch={self._job.batch_id} recipe={self._job.recipe_id}")

    def _finish_job(self, passed: bool, cycle_s: float):
        self._busy = False
        self._job_cycle_time_ms = int(float(cycle_s) * 1000.0)

        if passed:
            self.completed += 1
            # 1 tick done pulse
            if not self._done_seen_prev_tick:
                self._done_pulse_for_output = True
                self._done_seen_prev_tick = True
        else:
            self._fault_latched = True

        # clear job
        self._job = None
        self._job_gen = None
        self._stage_name = "IDLE"
        self._stage_remaining_s = 0.0

        print(f"  ST4: Job finished passed={passed} cycle_ms={self._job_cycle_time_ms}")

    def step(self, dt_s: float):
        if dt_s is None or dt_s <= 0:
            return

        # advance internal time
        self._env_now_s += float(dt_s)

        if self._fault_latched:
            return

        # if disabled while running -> stop (like ST1 stop behavior)
        if (not self.enabled) and self._busy:
            print("  ST4: Disabled while busy -> stopping job")
            self._busy = False
            self._job = None
            self._job_gen = None
            self._stage_name = "IDLE"
            self._stage_remaining_s = 0.0
            self._done_pulse_for_output = False
            self._done_seen_prev_tick = False
            return

        # start job if needed
        self._start_job_if_needed()

        # run generator stages by consuming dt_s
        remaining = float(dt_s)

        while self._busy and remaining > 0:
            if self._stage_remaining_s > remaining:
                self._stage_remaining_s -= remaining
                remaining = 0.0
                break

            # finish this stage
            remaining -= self._stage_remaining_s
            self._stage_remaining_s = 0.0

            # move to next stage or finish
            try:
                self._stage_name, self._stage_remaining_s = next(self._job_gen)
            except StopIteration as e:
                # generator returned (passed, cycle_s)
                try:
                    passed, cycle_s = e.value
                except Exception:
                    passed, cycle_s = (False, 0.0)
                self._finish_job(bool(passed), float(cycle_s))
                break

        # done pulse bookkeeping:
        # if we already emitted done once, clear seen flag when pulse is not re-set
        if not self._done_pulse_for_output:
            self._done_seen_prev_tick = False

    def outputs(self):
        if self._fault_latched:
            # fault latched => not ready, not busy, fault=1
            return 0, 0, 1, 0, self._job_cycle_time_ms, self.total, self.completed

        busy = 1 if self._busy else 0
        # ST1-like: ready is 1 only when enabled and idle
        ready = 1 if (self.enabled and not self._busy) else 0

        done = 1 if self._done_pulse_for_output else 0
        self._done_pulse_for_output = False

        fault = 0
        return ready, busy, fault, done, self._job_cycle_time_ms, self.total, self.completed

    def result_snapshot(self):
        return {
            "batch_id": int(self.batch_id),
            "recipe_id": int(self.recipe_id),
            "total": int(self.total),
            "completed": int(self.completed),
            "busy": int(self._busy),
            "fault_latched": int(self._fault_latched),
            "stage": str(self._stage_name),
            "stage_remaining_s": float(self._stage_remaining_s),
            "last_cycle_time_ms": int(self._job_cycle_time_ms),
            "env_now_s": float(self._env_now_s),
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
        self._run_latched = False
        self._prev_cmd_start = 0
        self._prev_cmd_stop = 0
        self._prev_cmd_reset = 0

        self.last_result = {}

        # End of user custom code region. Please don't edit beyond this point.

    def mainThread(self):
        dSession = vsiCommonPythonApi.connectToServer(self.localHost, self.domain, self.portNum, self.componentId)
        vsiEthernetPythonGateway.initialize(dSession, self.componentId, bytes(srcMacAddress), bytes(srcIpAddress))
        try:
            vsiCommonPythonApi.waitForReset()

            # Start of user custom code region. Please apply edits only within these regions:  After Reset

            self._sim = ST4_SimRuntime()
            self._run_latched = False
            self._prev_cmd_start = 0
            self._prev_cmd_stop = 0
            self._prev_cmd_reset = 0
            self.last_result = {}
            print("ST4: Generator runtime initialized")

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

                # Receive on configured port (6004)
                receivedData = vsiEthernetPythonGateway.recvEthernetPacket(PLC_LineCoordinatorSocketPortNumber0)
                if(receivedData[3] != 0):
                    self.decapsulateReceivedData(receivedData)

                # Start of user custom code region. Please apply edits only within these regions:  Before sending the packet

                # rising edge reset
                if self.mySignals.cmd_reset and not self._prev_cmd_reset:
                    print("ST4: RESET rising edge")
                    self._run_latched = False
                    if self._sim is not None:
                        self._sim.reset()

                # rising edge start
                if self.mySignals.cmd_start and not self._prev_cmd_start:
                    print("ST4: START rising edge")
                    self._run_latched = True

                # rising edge stop
                if self.mySignals.cmd_stop and not self._prev_cmd_stop:
                    print("ST4: STOP rising edge")
                    self._run_latched = False

                self._prev_cmd_start = int(self.mySignals.cmd_start)
                self._prev_cmd_stop = int(self.mySignals.cmd_stop)
                self._prev_cmd_reset = int(self.mySignals.cmd_reset)

                dt_s = float(self.simulationStep) / 1e9 if self.simulationStep else 0.0

                if self._sim is not None:
                    self._sim.set_enabled(self._run_latched)
                    self._sim.set_context(self.mySignals.batch_id, self.mySignals.recipe_id)

                    # advance generator station
                    self._sim.step(dt_s)

                    ready, busy, fault, done, cycle_time_ms, total, completed = self._sim.outputs()

                    self.mySignals.ready = int(ready)
                    self.mySignals.busy = int(busy)
                    self.mySignals.fault = int(fault)
                    self.mySignals.done = int(done)
                    self.mySignals.cycle_time_ms = int(cycle_time_ms)
                    self.mySignals.total = int(total)
                    self.mySignals.completed = int(completed)

                    self.last_result = self._sim.result_snapshot()

                # End of user custom code region. Please don't edit beyond this point.

                #Send ethernet packet to PLC_LineCoordinator
                self.sendEthernetPacketToPLC_LineCoordinator()

                # debug print
                print("\n+=ST4_CalibrationTesting+=")
                print("  VSI time:", vsiCommonPythonApi.getSimulationTimeInNs(), "ns")
                print("  Inputs:")
                print("\tcmd_start =", self.mySignals.cmd_start)
                print("\tcmd_stop  =", self.mySignals.cmd_stop)
                print("\tcmd_reset =", self.mySignals.cmd_reset)
                print("\tbatch_id  =", self.mySignals.batch_id)
                print("\trecipe_id =", self.mySignals.recipe_id)
                print("  Outputs:")
                print("\tready =", self.mySignals.ready)
                print("\tbusy  =", self.mySignals.busy)
                print("\tfault =", self.mySignals.fault)
                print("\tdone  =", self.mySignals.done)
                print("\tcycle_time_ms =", self.mySignals.cycle_time_ms)
                print("\ttotal =", self.mySignals.total)
                print("\tcompleted =", self.mySignals.completed)
                print("  Internal:")
                print("\trun_latched =", self._run_latched)
                print("\tlast_result =", self.last_result)
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
        if(self.clientPortNum[ST4_CalibrationTesting0] == 0):
            self.clientPortNum[ST4_CalibrationTesting0] = vsiEthernetPythonGateway.tcpConnect(
                bytes(PLC_LineCoordinatorIpAddress),
                PLC_LineCoordinatorSocketPortNumber0
            )

        if(self.clientPortNum[ST4_CalibrationTesting0] == 0):
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

        # command packet: 9 bytes = ? ? ? L H
        if self.receivedNumberOfBytes == 9:
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
        bytesToSend += self.packBytes('L', self.mySignals.total)
        bytesToSend += self.packBytes('L', self.mySignals.completed)

        vsiEthernetPythonGateway.sendEthernetPacket(PLC_LineCoordinatorSocketPortNumber0, bytes(bytesToSend))

        # Start of user custom code region. Please apply edits only within these regions:  Protocol's callback function
        # End of user custom code region. Please don't edit beyond this point.

    def packBytes(self, signalType, signal):
        if isinstance(signal, list):
            if signalType == 's':
                packedData = b''
                for s in signal:
                    s += '\0'
                    s = s.encode('utf-8')
                    packedData += struct.pack(f'={len(s)}s', s)
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

    def unpackBytes(self, signalType, packedBytes, signal=""):
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
    inputArgs.add_argument('--domain', metavar='D', default='AF_UNIX',
                           help='Socket domain for connection with the VSI TLM fabric server')
    inputArgs.add_argument('--server-url', metavar='CO', default='localhost',
                           help='server URL of the VSI TLM Fabric Server')

    # Start of user custom code region. Please apply edits only within these regions:  Main method
    # End of user custom code region. Please don't edit beyond this point.

    args = inputArgs.parse_args()

    sT4_CalibrationTesting = ST4_CalibrationTesting(args)
    sT4_CalibrationTesting.mainThread()


if __name__ == '__main__':
    main()
