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
		self.strain_relief_ok = 0
		self.continuity_ok = 0



srcMacAddress = [0x02, 0x00, 0x00, 0x00, 0x00, 0x13]
PLC_LineCoordinatorMacAddress = [0x02, 0x00, 0x00, 0x00, 0x00, 0x01]
srcIpAddress = [10, 10, 0, 13]
PLC_LineCoordinatorIpAddress = [10, 10, 0, 1]

PLC_LineCoordinatorSocketPortNumber0 = 6003

ST3_ElectronicsWiring0 = 0


# Start of user custom code region. Please apply edits only within these regions:  Global Variables & Definitions

import simpy
import random
from dataclasses import dataclass
from typing import Optional, Dict, Any

# --- Station 3 (Electronics + Wiring) SimPy model ---
# Time base:
#   We advance SimPy by dt_s = simulationStep(ns) / 1e9 every VSI loop when NOT paused.
#   All durations below are in seconds.
RANDOM_SEED_ST3 = 42

# Base process times (seconds)
T_MOUNT_PSU_S = 4.0
T_MOUNT_BOARD_S = 3.0
T_MOUNT_SCREEN_S = 2.0
T_ROUTE_CABLES_S = 5.0
T_STRAIN_RELIEF_S = 2.0
T_CONTINUITY_TEST_S = 2.0
T_REWORK_S = 4.0

# Quality probabilities
P_STRAIN_OK = 0.95
P_CONTINUITY_OK = 0.92

@dataclass
class ST3Job:
    batch_id: int
    recipe_id: int
    enqueue_t: float

class Station3Sim:
    """SimPy model for Station 3: install electronics + route wiring + test."""
    def __init__(self, seed: int = RANDOM_SEED_ST3):
        random.seed(seed)
        self.env = simpy.Environment()
        self.queue = simpy.Store(self.env)

        # Resources (one workcell, one tester)
        self.workcell = simpy.Resource(self.env, capacity=1)
        self.tester = simpy.Resource(self.env, capacity=1)

        # KPIs / state
        self.total = 0
        self.completed = 0
        self.reworks = 0

        self.current_job = None  # type: Optional[ST3Job]
        self.last_result = {  # type: Dict[str, Any]
            "busy": 0,
            "fault": 0,
            "done_pulse": 0,
            "cycle_time_s": 0.0,
            "strain_relief_ok": 0,
            "continuity_ok": 0,
            "batch_id": 0,
            "recipe_id": 0,
        }

        self._job_start_t = None
        self.env.process(self._worker())

    def reset(self, seed: int = RANDOM_SEED_ST3):
        self.__init__(seed=seed)

    def submit(self, batch_id: int, recipe_id: int):
        job = ST3Job(batch_id=batch_id, recipe_id=recipe_id, enqueue_t=self.env.now)
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
                "strain_relief_ok": 0,
                "continuity_ok": 0,
                "batch_id": job.batch_id,
                "recipe_id": job.recipe_id,
            })

            # One unit goes through: mounting + wiring + QA tests.
            with self.workcell.request() as wc_req:
                yield wc_req
                # Mount PSU / Board / Screen
                yield self.env.timeout(T_MOUNT_PSU_S)
                yield self.env.timeout(T_MOUNT_BOARD_S)
                yield self.env.timeout(T_MOUNT_SCREEN_S)

                # Route cables + strain relief
                yield self.env.timeout(T_ROUTE_CABLES_S)
                yield self.env.timeout(T_STRAIN_RELIEF_S)
                strain_ok = (random.random() <= P_STRAIN_OK)

            # Continuity test uses tester resource
            with self.tester.request() as t_req:
                yield t_req
                yield self.env.timeout(T_CONTINUITY_TEST_S)
                cont_ok = (random.random() <= P_CONTINUITY_OK)

            # If failed, do one rework loop then retest
            fault = 0
            if (not strain_ok) or (not cont_ok):
                self.reworks += 1
                # Rework (manual fix / re-route / re-crimp)
                yield self.env.timeout(T_REWORK_S)

                # Retest after rework (higher success chances)
                strain_ok = (random.random() <= min(0.98, P_STRAIN_OK + 0.03))
                with self.tester.request() as t_req2:
                    yield t_req2
                    yield self.env.timeout(T_CONTINUITY_TEST_S)
                    cont_ok = (random.random() <= min(0.97, P_CONTINUITY_OK + 0.05))

                if (not strain_ok) or (not cont_ok):
                    fault = 1  # latch a station fault (requires PLC reset)

            end_t = self.env.now
            start_t = (self._job_start_t if self._job_start_t is not None else end_t)
            cycle_s = float(end_t - start_t)
            self.last_result.update({
                "busy": 0,
                "fault": int(fault),
                "done_pulse": 1 if fault == 0 else 0,
                "cycle_time_s": cycle_s,
                "strain_relief_ok": 1 if strain_ok else 0,
                "continuity_ok": 1 if cont_ok else 0,
            })

            if fault == 0:
                self.completed += 1

            self.current_job = None
# End of user custom code region. Please don't edit beyond this point.
class ST3_ElectronicsWiring:

	def __init__(self, args):
		self.componentId = 3
		self.localHost = args.server_url
		self.domain = args.domain
		self.portNum = 50104
        
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

		self._st3 = Station3Sim()
		self._st3_last_batch_seen = -1
		self._st3_prev_reset = 0
		self._st3_fault_latched = 0
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
			self.mySignals.strain_relief_ok = 0
			self.mySignals.continuity_ok = 0
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

				reset_edge = (self.mySignals.cmd_reset == 1 and self._st3_prev_reset == 0)
				self._st3_prev_reset = self.mySignals.cmd_reset

				if reset_edge:
					self._st3.reset()
					self.mySignals.ready = 1
					self.mySignals.busy = 0
					self.mySignals.fault = 0
					self.mySignals.done = 0
					self.mySignals.cycle_time_ms = 0
					self.mySignals.strain_relief_ok = 0
					self.mySignals.continuity_ok = 0
					self._st3_fault_latched = 0

				if self._st3_fault_latched:
					self.mySignals.ready = 0
					self.mySignals.busy = 0
					self.mySignals.fault = 1
				else:
					# PLC-controlled: run when cmd_start=1, pause when cmd_stop=1.
					# We submit ONE job when the station is idle and there is no queued job.
					pending = (len(self._st3.queue.items) > 0)
					if (self.mySignals.cmd_start == 1 and self.mySignals.cmd_stop == 0):
						if (not pending) and (self._st3.current_job is None) and (self._st3.last_result.get('busy', 0) == 0):
							self._st3.submit(int(self.mySignals.batch_id), int(self.mySignals.recipe_id))

					# Advance SimPy time if not paused
					if self.mySignals.cmd_stop == 0:
						dt_s = float(self.simulationStep) / 1e9 if self.simulationStep else 0.0
						if dt_s > 0:
							self._st3.env.run(until=self._st3.env.now + dt_s)

					res = self._st3.last_result
					busy = int(res.get('busy', 0))
					fault = int(res.get('fault', 0))
					done_pulse = int(res.get('done_pulse', 0))

					if fault == 1:
						self._st3_fault_latched = 1

					self.mySignals.busy = 1 if busy else 0
					self.mySignals.ready = 1 if (not self._st3_fault_latched and busy == 0 and not pending) else 0
					self.mySignals.fault = 1 if self._st3_fault_latched else 0

					if done_pulse == 1:
						self.mySignals.done = 1
						self._st3.last_result['done_pulse'] = 0

					self.mySignals.cycle_time_ms = int(float(res.get('cycle_time_s', 0.0)) * 1000.0)
					self.mySignals.strain_relief_ok = 1 if int(res.get('strain_relief_ok', 0)) else 0
					self.mySignals.continuity_ok = 1 if int(res.get('continuity_ok', 0)) else 0
				# End of user custom code region. Please don't edit beyond this point.

				#Send ethernet packet to PLC_LineCoordinator
				self.sendEthernetPacketToPLC_LineCoordinator()

				# Start of user custom code region. Please apply edits only within these regions:  After sending the packet
				# End of user custom code region. Please don't edit beyond this point.

				print("\n+=ST3_ElectronicsWiring+=")
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
				print("\tstrain_relief_ok =", end = " ")
				print(self.mySignals.strain_relief_ok)
				print("\tcontinuity_ok =", end = " ")
				print(self.mySignals.continuity_ok)
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
		if(self.clientPortNum[ST3_ElectronicsWiring0] == 0):
			self.clientPortNum[ST3_ElectronicsWiring0] = vsiEthernetPythonGateway.tcpConnect(bytes(PLC_LineCoordinatorIpAddress), PLC_LineCoordinatorSocketPortNumber0)

		if(self.clientPortNum[ST3_ElectronicsWiring0] == 0):
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

		bytesToSend += self.packBytes('?', self.mySignals.strain_relief_ok)

		bytesToSend += self.packBytes('?', self.mySignals.continuity_ok)

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
                      
	sT3_ElectronicsWiring = ST3_ElectronicsWiring(args)
	sT3_ElectronicsWiring.mainThread()



if __name__ == '__main__':
    main()