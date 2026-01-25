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

import simpy
import random
from dataclasses import dataclass
from typing import Optional, Dict, Any

# --- Station 4 (Calibration + Test Chamber) SimPy model ---
# Durations in seconds. SimPy time advances by dt_s = simulationStep(ns)/1e9 each VSI loop when not paused.
RANDOM_SEED_ST4 = 7

# Stages (seconds)
T_MOTION_S = 2.0
T_THERMAL_S = 18.0
T_CALIBRATION_S = 6.0
T_TESTPRINT_S = 15.0

# Pass probability (after calibration/test)
P_PASS = 0.93
P_PASS_AFTER_RETRY = 0.97
T_RETRY_S = 5.0  # extra time for recalibration/retry if first pass fails

@dataclass
class ST4Job:
    batch_id: int
    recipe_id: int
    enqueue_t: float

class Station4Sim:
    def __init__(self, chamber_capacity: int = 1, seed: int = RANDOM_SEED_ST4):
        random.seed(seed)
        self.env = simpy.Environment()
        self.queue = simpy.Store(self.env)
        self.chamber = simpy.Resource(self.env, capacity=chamber_capacity)

        self.total = 0
        self.completed = 0

        self.current_job = None  # type: Optional[ST4Job]
        self.last_result = {  # type: Dict[str, Any]
            "busy": 0,
            "fault": 0,
            "done_pulse": 0,
            "cycle_time_s": 0.0,
            "batch_id": 0,
            "recipe_id": 0,
        }

        self._job_start_t = None
        self.env.process(self._worker())

    def reset(self, chamber_capacity: int = 1, seed: int = RANDOM_SEED_ST4):
        self.__init__(chamber_capacity=chamber_capacity, seed=seed)

    def submit(self, batch_id: int, recipe_id: int):
        job = ST4Job(batch_id=batch_id, recipe_id=recipe_id, enqueue_t=self.env.now)
        self.queue.put(job)

    def _worker(self):
        while True:
            job = yield self.queue.get()
            self.current_job = job
            self.total += 1
            self._job_start_t = self.env.now
            self.last_result.update({
                "busy": 1,
                "fault": 0,
                "done_pulse": 0,
                "cycle_time_s": 0.0,
                "batch_id": job.batch_id,
                "recipe_id": job.recipe_id,
            })

            with self.chamber.request() as req:
                yield req
                # Core sequence inside chamber
                yield self.env.timeout(T_MOTION_S)
                yield self.env.timeout(T_THERMAL_S)
                yield self.env.timeout(T_CALIBRATION_S)
                yield self.env.timeout(T_TESTPRINT_S)

                # Pass/fail decision. If fail, retry once (recalibration + short rerun)
                passed = (random.random() <= P_PASS)
                if not passed:
                    yield self.env.timeout(T_RETRY_S)
                    passed = (random.random() <= P_PASS_AFTER_RETRY)

            end_t = self.env.now
            start_t = (self._job_start_t if self._job_start_t is not None else end_t)
            cycle_s = float(end_t - start_t)

            fault = 0 if passed else 1
            self.last_result.update({
                "busy": 0,
                "fault": int(fault),
                "done_pulse": 1 if passed else 0,
                "cycle_time_s": cycle_s,
            })

            if passed:
                self.completed += 1

            self.current_job = None
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

		self._st4 = Station4Sim(chamber_capacity=1)
		self._st4_last_batch_seen = -1
		self._st4_prev_reset = 0
		self._st4_fault_latched = 0
		# End of user custom code region. Please don't edit beyond this point.



	def mainThread(self):
		dSession = vsiCommonPythonApi.connectToServer(self.localHost, self.domain, self.portNum, self.componentId)
		vsiEthernetPythonGateway.initialize(dSession, self.componentId, bytes(srcMacAddress), bytes(srcIpAddress))
		try:
			vsiCommonPythonApi.waitForReset()

			# Start of user custom code region. Please apply edits only within these regions:  After Reset

			self.mySignals.ready = 1
			self.mySignals.busy = 0
			self.mySignals.fault = 0
			self.mySignals.done = 0
			self.mySignals.cycle_time_ms = 0
			self.mySignals.total = 0
			self.mySignals.completed = 0
			# End of user custom code region. Please don't edit beyond this point.
			self.updateInternalVariables()

			if(vsiCommonPythonApi.isStopRequested()):
				raise Exception("stopRequested")
			self.establishTcpUdpConnection()
			nextExpectedTime = vsiCommonPythonApi.getSimulationTimeInNs()
			while(vsiCommonPythonApi.getSimulationTimeInNs() < self.totalSimulationTime):

				# Start of user custom code region. Please apply edits only within these regions:  Inside the while loop

				self.mySignals.done = 0
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

				reset_edge = (self.mySignals.cmd_reset == 1 and self._st4_prev_reset == 0)
				self._st4_prev_reset = self.mySignals.cmd_reset

				if reset_edge:
					self._st4.reset(chamber_capacity=1)
					self.mySignals.ready = 1
					self.mySignals.busy = 0
					self.mySignals.fault = 0
					self.mySignals.done = 0
					self.mySignals.cycle_time_ms = 0
					self.mySignals.total = 0
					self.mySignals.completed = 0
					self._st4_fault_latched = 0

				if self._st4_fault_latched:
					self.mySignals.ready = 0
					self.mySignals.busy = 0
					self.mySignals.fault = 1
				else:
					pending = (len(self._st4.queue.items) > 0)
					if (self.mySignals.cmd_start == 1 and self.mySignals.cmd_stop == 0):
						if (not pending) and (self._st4.current_job is None) and (self._st4.last_result.get('busy', 0) == 0):
							self._st4.submit(int(self.mySignals.batch_id), int(self.mySignals.recipe_id))

					if self.mySignals.cmd_stop == 0:
						dt_s = float(self.simulationStep) / 1e9 if self.simulationStep else 0.0
						if dt_s > 0:
							self._st4.env.run(until=self._st4.env.now + dt_s)

					res = self._st4.last_result
					busy = int(res.get('busy', 0))
					fault = int(res.get('fault', 0))
					done_pulse = int(res.get('done_pulse', 0))

					if fault == 1:
						self._st4_fault_latched = 1

					self.mySignals.busy = 1 if busy else 0
					self.mySignals.ready = 1 if (not self._st4_fault_latched and busy == 0 and not pending) else 0
					self.mySignals.fault = 1 if self._st4_fault_latched else 0

					if done_pulse == 1:
						self.mySignals.done = 1
						self._st4.last_result['done_pulse'] = 0

					self.mySignals.cycle_time_ms = int(float(res.get('cycle_time_s', 0.0)) * 1000.0)
					self.mySignals.total = int(self._st4.total)
					self.mySignals.completed = int(self._st4.completed)
				# End of user custom code region. Please don't edit beyond this point.

				#Send ethernet packet to PLC_LineCoordinator
				self.sendEthernetPacketToPLC_LineCoordinator()

				# Start of user custom code region. Please apply edits only within these regions:  After sending the packet
				# End of user custom code region. Please don't edit beyond this point.

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
		if(self.clientPortNum[ST4_CalibrationTesting0] == 0):
			self.clientPortNum[ST4_CalibrationTesting0] = vsiEthernetPythonGateway.tcpConnect(bytes(PLC_LineCoordinatorIpAddress), PLC_LineCoordinatorSocketPortNumber0)

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

		bytesToSend += self.packBytes('L', self.mySignals.total)

		bytesToSend += self.packBytes('L', self.mySignals.completed)

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
                      
	sT4_CalibrationTesting = ST4_CalibrationTesting(args)
	sT4_CalibrationTesting.mainThread()



if __name__ == '__main__':
    main()