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
		self.completed = 0
		self.scrapped = 0
		self.reworks = 0
		self.cycle_time_avg_s = 0



srcMacAddress = [0x02, 0x00, 0x00, 0x00, 0x00, 0x12]
PLC_LineCoordinatorMacAddress = [0x02, 0x00, 0x00, 0x00, 0x00, 0x01]
srcIpAddress = [10, 10, 0, 12]
PLC_LineCoordinatorIpAddress = [10, 10, 0, 1]

PLC_LineCoordinatorSocketPortNumber0 = 6002

ST2_FrameCoreAssembly0 = 0


# Start of user custom code region. Please apply edits only within these regions:  Global Variables & Definitions

# --- Station 2 SimPy model (Frame + Core Assembly) ---
# This code simulates a realistic assembly cell:
# - 2 robots, fixtures, tools
# - parallel operations
# - test + rework loop + scrap
# The VSI main loop steps the SimPy env and copies results to mySignals.

import time
import random
import statistics

try:
	import simpy
except Exception as _e:
	simpy = None


class _Station2FrameCoreSim:
	"""Discrete-event model of Station 2: Frame & Core Assembly."""

	def __init__(
		self,
		seed=7,
		p_alignment_pass=0.90,
		p_torque_pass=0.92,
		max_rework=2,
		p_robot_fault=0.02,
	):
		if simpy is None:
			raise RuntimeError("simpy is required for Station 2 simulation")

		self.env = simpy.Environment()
		random.seed(seed)

		# Resources (realistic blocking)
		self.robotA = simpy.Resource(self.env, capacity=1)
		self.robotB = simpy.Resource(self.env, capacity=1)

		self.base_clamp = simpy.Resource(self.env, capacity=1)
		self.tower_clamp = simpy.Resource(self.env, capacity=1)

		self.laser_scanner = simpy.Resource(self.env, capacity=1)
		self.torque_driver = simpy.Resource(self.env, capacity=1)
		self.belt_tool = simpy.Resource(self.env, capacity=1)

		# External-trigger job queue
		self.in_q = simpy.Store(self.env)

		# Parameters
		self.p_alignment_pass = float(p_alignment_pass)
		self.p_torque_pass = float(p_torque_pass)
		self.max_rework = int(max_rework)
		self.p_robot_fault = float(p_robot_fault)

		# KPIs
		self.completed = 0
		self.scrapped = 0
		self.reworks = 0
		self.cycle_times = []  # seconds

		# Live state
		self.in_progress = False
		self.fault_active = False
		self.fault_reason = ""
		self.current_job = None

		self.last_cycle_time_s = 0.0
		self._done_pulse = False

		# Start worker
		self.env.process(self._worker())

	def now(self):
		return float(self.env.now)

	def _chance(self, p):
		return random.random() < p

	def _small(self, base, var=2):
		# time in seconds, with variation
		return max(0.5, float(base + random.randint(-var, var)))

	def _tool_change(self):
		return float(random.randint(1, 3))

	def _maybe_robot_fault(self, where="robot"):
		# inject an occasional robot / cell fault that causes downtime
		if self._chance(self.p_robot_fault):
			self.fault_active = True
			self.fault_reason = f"{where}_fault"
			repair_t = float(random.randint(6, 18))
			yield self.env.timeout(repair_t)
			self.fault_active = False
			self.fault_reason = ""

	def submit_job(self, batch_id, recipe_id, jid):
		job = {"jid": int(jid), "batch_id": int(batch_id), "recipe_id": int(recipe_id), "t_in": self.env.now}
		return self.in_q.put(job)

	def consume_done_pulse(self):
		was = self._done_pulse
		self._done_pulse = False
		return was

	def avg_cycle_time(self):
		if not self.cycle_times:
			return 0.0
			
		return float(statistics.mean(self.cycle_times))

	def step(self, dt_s):
		# Advance the SimPy environment by dt_s seconds
		if dt_s <= 0:
			return
		target = self.env.now + float(dt_s)
		# SimPy requires target > now
		if target <= self.env.now:
			return
		self.env.run(until=target)

	# -------------------------
	# Worker + sequence
	# -------------------------
	def _worker(self):
		while True:
			job = yield self.in_q.get()
			self.current_job = job
			self.in_progress = True
			self._done_pulse = False

			t_start = self.env.now
			ok = yield from self._run_station2(job)
			t_end = self.env.now

			ct = float(t_end - t_start)
			self.last_cycle_time_s = ct
			self.cycle_times.append(ct)

			if ok:
				self.completed += 1
			else:
				self.scrapped += 1

			# pulse "done" for PLC edge-detect
			self._done_pulse = True

			self.in_progress = False
			self.current_job = None

	def _run_station2(self, job):
		jid = job["jid"]
		recipe_id = job["recipe_id"]

		# Recipe affects durations slightly (heavier core / reinforcement)
		recipe_factor = 1.0 + (0.05 * (recipe_id % 5))

		# Step 1: Build base (RobotA + base fixture)
		with self.base_clamp.request() as clamp_req:
			yield clamp_req
			yield from self._maybe_robot_fault("base_fixture")
			with self.robotA.request() as rA_req:
				yield rA_req
				yield from self._maybe_robot_fault("robotA")
				yield self.env.timeout(self._small(8, 2) * recipe_factor)

		# Step 2: Install Z axis (tower fixture + parallel robots)
		with self.tower_clamp.request() as tclamp_req:
			yield tclamp_req
			yield from self._maybe_robot_fault("tower_fixture")

			p = []
			p.append(self.env.process(self._robotB_hold_support(recipe_factor)))
			p.append(self.env.process(self._robotA_install_z(recipe_factor)))
			yield simpy.events.AllOf(self.env, p)

		# Step 3: Install gantry + belts (parallel)
		p2 = []
		p2.append(self.env.process(self._robotA_place_gantry(recipe_factor)))
		p2.append(self.env.process(self._robotB_tension_belts(recipe_factor)))
		yield simpy.events.AllOf(self.env, p2)

		# Step 4: Test + rework loop
		for attempt in range(self.max_rework + 1):
			passed = yield from self._test_cycle(recipe_factor)
			if passed:
				return True

			if attempt < self.max_rework:
				self.reworks += 1
				yield from self._rework_actions(recipe_factor)
			else:
				return False

	# -------------------------
	# Subtasks
	# -------------------------
	def _robotB_hold_support(self, recipe_factor):
		with self.robotB.request() as rB_req:
			yield rB_req
			yield from self._maybe_robot_fault("robotB")
			yield self.env.timeout(self._tool_change())
			yield self.env.timeout(self._small(7, 2) * recipe_factor)

	def _robotA_install_z(self, recipe_factor):
		with self.robotA.request() as rA_req:
			yield rA_req
			yield from self._maybe_robot_fault("robotA")
			yield self.env.timeout(self._small(10, 3) * recipe_factor)

	def _robotA_place_gantry(self, recipe_factor):
		with self.robotA.request() as rA_req:
			yield rA_req
			yield from self._maybe_robot_fault("robotA")
			yield self.env.timeout(self._small(9, 2) * recipe_factor)

	def _robotB_tension_belts(self, recipe_factor):
		with self.robotB.request() as rB_req:
			yield rB_req
			yield from self._maybe_robot_fault("robotB")
			with self.belt_tool.request() as belt_req:
				yield belt_req
				yield self.env.timeout(self._tool_change())
				yield self.env.timeout(self._small(6, 2) * recipe_factor)

	def _test_cycle(self, recipe_factor):
		# Laser alignment
		with self.laser_scanner.request() as lreq:
			yield lreq
			yield from self._maybe_robot_fault("laser")
			yield self.env.timeout(self._small(3, 1) * recipe_factor)
		alignment_ok = self._chance(self.p_alignment_pass)

		# Torque verification
		with self.torque_driver.request() as treq:
			yield treq
			with self.robotB.request() as rB_req:
				yield rB_req
				yield from self._maybe_robot_fault("torque")
				yield self.env.timeout(self._tool_change())
				yield self.env.timeout(self._small(4, 1) * recipe_factor)
		torque_ok = self._chance(self.p_torque_pass)

		# Quick smooth motion check
		with self.robotA.request() as rA_req:
			yield rA_req
			yield self.env.timeout(self._small(2, 1) * recipe_factor)

		return bool(alignment_ok and torque_ok)

	def _rework_actions(self, recipe_factor):
		# loosen/realign + retorque
		with self.robotA.request() as rA_req:
			yield rA_req
			yield from self._maybe_robot_fault("robotA")
			yield self.env.timeout(self._small(4, 2) * recipe_factor)

		with self.torque_driver.request() as treq:
			yield treq
			with self.robotB.request() as rB_req:
				yield rB_req
				yield self.env.timeout(self._tool_change())
				yield self.env.timeout(self._small(3, 1) * recipe_factor)
# End of user custom code region. Please don't edit beyond this point.
class ST2_FrameCoreAssembly:

	def __init__(self, args):
		self.componentId = 2
		self.localHost = args.server_url
		self.domain = args.domain
		self.portNum = 50103
        
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

		# --- Station 2 custom state ---
		self._sim_enabled = True
		self._sim = None
		self._job_seq = 0
		self._last_batch_id = 0
		self._last_recipe_id = 0
		self._last_cmd_reset = 0

		# Create SimPy model BEFORE mainThread starts (per requirement)
		try:
			self._sim = _Station2FrameCoreSim(seed=7)
		except Exception as e:
			print("ST2 SimPy init failed:", str(e))
			self._sim_enabled = False
		# End of user custom code region. Please don't edit beyond this point.



	def mainThread(self):
		dSession = vsiCommonPythonApi.connectToServer(self.localHost, self.domain, self.portNum, self.componentId)
		vsiEthernetPythonGateway.initialize(dSession, self.componentId, bytes(srcMacAddress), bytes(srcIpAddress))
		try:
			vsiCommonPythonApi.waitForReset()

			# Start of user custom code region. Please apply edits only within these regions:  After Reset

			# Reset station state
			self.mySignals.ready = 1
			self.mySignals.busy = 0
			self.mySignals.fault = 0
			self.mySignals.done = 0
			self.mySignals.cycle_time_ms = 0
			self.mySignals.completed = 0
			self.mySignals.scrapped = 0
			self.mySignals.reworks = 0
			self.mySignals.cycle_time_avg_s = 0

			self._job_seq = 0
			self._last_batch_id = 0
			self._last_recipe_id = 0
			self._last_cmd_reset = 0

			# Re-create SimPy env/model on reset so faults/queues clear
			if self._sim_enabled:
				try:
					self._sim = _Station2FrameCoreSim(seed=7)
				except Exception as e:
					print("ST2 SimPy re-init failed:", str(e))
					self._sim_enabled = False
			# End of user custom code region. Please don't edit beyond this point.
			self.updateInternalVariables()

			if(vsiCommonPythonApi.isStopRequested()):
				raise Exception("stopRequested")
			self.establishTcpUdpConnection()
			nextExpectedTime = vsiCommonPythonApi.getSimulationTimeInNs()
			while(vsiCommonPythonApi.getSimulationTimeInNs() < self.totalSimulationTime):

				# Start of user custom code region. Please apply edits only within these regions:  Inside the while loop

				# --- Station 2 control (PLC-driven) ---
				# Behavior:
				# - cmd_start=1 keeps the cell producing (auto-launch next job when ready)
				# - cmd_stop=1 freezes the SimPy time (acts like a pause/stop)
				# - cmd_reset rising edge re-initializes the SimPy model and clears KPIs
				cmd_start = int(self.mySignals.cmd_start)
				cmd_stop = int(self.mySignals.cmd_stop)
				cmd_reset = int(self.mySignals.cmd_reset)

				# Rising edge reset
				if cmd_reset == 1 and self._last_cmd_reset == 0:
					if self._sim_enabled:
						try:
							self._sim = _Station2FrameCoreSim(seed=7)
						except Exception as e:
							print("ST2 reset failed:", str(e))
							self._sim_enabled = False
					# Clear outputs immediately
					self.mySignals.ready = 1
					self.mySignals.busy = 0
					self.mySignals.fault = 0
					self.mySignals.done = 0
					self.mySignals.cycle_time_ms = 0
					self.mySignals.completed = 0
					self.mySignals.scrapped = 0
					self.mySignals.reworks = 0
					self.mySignals.cycle_time_avg_s = 0
					self._job_seq = 0

				self._last_cmd_reset = cmd_reset

				# Decide if we advance the SimPy time this tick
				advance_sim = (cmd_stop == 0) and (cmd_start == 1)

				# Auto-submit a job when running and cell is idle
				if self._sim_enabled and cmd_start == 1 and cmd_stop == 0:
					if (not self._sim.in_progress) and (not self._sim.fault_active):
						# submit next frame/core assembly job
						self._job_seq += 1
						self._last_batch_id = int(self.mySignals.batch_id)
						self._last_recipe_id = int(self.mySignals.recipe_id)
						try:
							self._sim.submit_job(self._last_batch_id, self._last_recipe_id, self._job_seq)
						except Exception as e:
							print("ST2 submit_job failed:", str(e))
							self._sim_enabled = False

				# Advance SimPy
				if self._sim_enabled and advance_sim:
					# Convert VSI step (ns) to seconds
					dt_s = float(self.simulationStep) / 1e9 if self.simulationStep else 0.001
					# clamp very small dt to avoid "until == now"
					if dt_s < 1e-4:
						dt_s = 1e-4
					try:
						self._sim.step(dt_s)
					except Exception as e:
						print("ST2 SimPy step failed:", str(e))
						self._sim_enabled = False

				# Copy SimPy state -> VSI outputs
				if self._sim_enabled:
					# ready/busy/fault reflect live model
					self.mySignals.fault = 1 if self._sim.fault_active else 0

					if cmd_stop == 1:
						# stopped: hold outputs in a safe "not ready" state
						self.mySignals.ready = 0
						self.mySignals.busy = 0
					else:
						self.mySignals.busy = 1 if self._sim.in_progress else 0
						self.mySignals.ready = 1 if (not self._sim.in_progress and not self._sim.fault_active) else 0

					# done pulse
					if self._sim.consume_done_pulse():
						self.mySignals.done = 1
						self.mySignals.cycle_time_ms = int(self._sim.last_cycle_time_s * 1000.0)
					else:
						self.mySignals.done = 0

					# KPIs
					self.mySignals.completed = int(self._sim.completed)
					self.mySignals.scrapped = int(self._sim.scrapped)
					self.mySignals.reworks = int(self._sim.reworks)
					self.mySignals.cycle_time_avg_s = float(self._sim.avg_cycle_time())
				else:
					# SimPy disabled: keep safe outputs
					self.mySignals.ready = 1 if cmd_stop == 0 else 0
					self.mySignals.busy = 0
					self.mySignals.fault = 1
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

				# Nothing needed here: outputs already updated from SimPy in the while-loop custom logic.
				# End of user custom code region. Please don't edit beyond this point.

				#Send ethernet packet to PLC_LineCoordinator
				self.sendEthernetPacketToPLC_LineCoordinator()

				# Start of user custom code region. Please apply edits only within these regions:  After sending the packet

				# Clear the done pulse after sending is handled by consume_done_pulse() above (done is already a 1-tick pulse).
				# End of user custom code region. Please don't edit beyond this point.

				print("\n+=ST2_FrameCoreAssembly+=")
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
				print("\tcompleted =", end = " ")
				print(self.mySignals.completed)
				print("\tscrapped =", end = " ")
				print(self.mySignals.scrapped)
				print("\treworks =", end = " ")
				print(self.mySignals.reworks)
				print("\tcycle_time_avg_s =", end = " ")
				print(self.mySignals.cycle_time_avg_s)
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
		if(self.clientPortNum[ST2_FrameCoreAssembly0] == 0):
			self.clientPortNum[ST2_FrameCoreAssembly0] = vsiEthernetPythonGateway.tcpConnect(bytes(PLC_LineCoordinatorIpAddress), PLC_LineCoordinatorSocketPortNumber0)

		if(self.clientPortNum[ST2_FrameCoreAssembly0] == 0):
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

		bytesToSend += self.packBytes('L', self.mySignals.completed)

		bytesToSend += self.packBytes('L', self.mySignals.scrapped)

		bytesToSend += self.packBytes('L', self.mySignals.reworks)

		bytesToSend += self.packBytes('d', self.mySignals.cycle_time_avg_s)

		#Send ethernet packet to PLC_LineCoordinator
		vsiEthernetPythonGateway.sendEthernetPacket(PLC_LineCoordinatorSocketPortNumber0, bytes(bytesToSend))

		# Start of user custom code region. Please apply edits only within these regions:  Protocol's callback function

		# Protocol callback not used: we directly send our packed signals each tick.
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

	# (no changes needed here)
	# End of user custom code region. Please don't edit beyond this point.

	args = inputArgs.parse_args()
                      
	sT2_FrameCoreAssembly = ST2_FrameCoreAssembly(args)
	sT2_FrameCoreAssembly.mainThread()



if __name__ == '__main__':
    main()
