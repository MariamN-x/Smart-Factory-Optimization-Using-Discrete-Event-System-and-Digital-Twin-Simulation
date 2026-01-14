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

# --- Station 1 (Component Kitting) SimPy model ---
# Goal: simulate a realistic kitting station for a 3D printer line.
# - Receives start/stop/reset + batch_id/recipe_id from PLC over VSI.
# - Runs the discrete-event process in SimPy (outside the VSI while-loop logic).
# - VSI mainThread only *steps* SimPy and copies results into outgoing signals.

import simpy
import random
import math
from collections import deque


# =====================
# ROBOTIC ARM (simple 2-axis pick/place)
# =====================
class RoboticArm2Axis:
    def __init__(self, env: simpy.Environment, name: str, home_xy):
        self.env = env
        self.name = name
        self.home = list(home_xy)
        self.pos = list(home_xy)

        self.state = "IDLE"   # IDLE / MOVING / WORKING / FAILED / REPAIRING
        self.failure_probability = 0.015  # per pick/place action

        # timing (seconds)
        self.move_base_s = 0.20
        self.move_s_per_unit = 0.002
        self.pick_s = 0.35
        self.place_s = 0.28

    def _dist(self, a, b):
        try:
            return math.dist(a, b)
        except Exception:
            return ((a[0]-b[0])**2 + (a[1]-b[1])**2) ** 0.5

    def move_to(self, x, y):
        return self.env.process(self._move(x, y))

    def _move(self, x, y):
        if self.state in ("FAILED", "REPAIRING"):
            return
        self.state = "MOVING"
        d = self._dist(self.pos, [x, y])
        yield self.env.timeout(self.move_base_s + d * self.move_s_per_unit)
        self.pos = [x, y]
        self.state = "IDLE"

    def pick(self):
        return self.env.process(self._op(self.pick_s))

    def place(self):
        return self.env.process(self._op(self.place_s))

    def _op(self, t_s: float):
        if self.state in ("FAILED", "REPAIRING"):
            return
        # failure chance per operation
        if random.random() < self.failure_probability:
            self.state = "FAILED"
            return
        self.state = "WORKING"
        yield self.env.timeout(t_s)
        self.state = "IDLE"

    def repair(self):
        return self.env.process(self._repair())

    def _repair(self):
        if self.state != "FAILED":
            return
        self.state = "REPAIRING"
        # realistic repair window
        yield self.env.timeout(random.uniform(4.0, 12.0))
        self.state = "IDLE"

    def home_return(self):
        return self.move_to(self.home[0], self.home[1])


# =====================
# INVENTORY (bins for kitting components)
# =====================
class Inventory:
    def __init__(self, env: simpy.Environment):
        self.env = env
        self.state = "READY"  # READY / CHECKING / PARTS_MISSING / RESTOCKING

        # start stock (units)
        self.levels = {
            "fasteners": 400,
            "brackets": 120,
            "sensors": 80,
            "wiring_kit": 120,
            "label": 300,
            "foam": 200,
        }

        # reorder points and replenishment quantities
        self.reorder_point = {
            "fasteners": 120,
            "brackets": 30,
            "sensors": 20,
            "wiring_kit": 30,
            "label": 80,
            "foam": 50,
        }
        self.restock_qty = {
            "fasteners": 400,
            "brackets": 120,
            "sensors": 80,
            "wiring_kit": 120,
            "label": 300,
            "foam": 200,
        }

        self._restock_in_progress = False

    def can_fulfill(self, bom: dict) -> bool:
        for k, q in bom.items():
            if self.levels.get(k, 0) < q:
                return False
        return True

    def consume(self, bom: dict):
        for k, q in bom.items():
            self.levels[k] = max(0, int(self.levels.get(k, 0)) - int(q))

    def check(self, bom: dict):
        # short check delay
        self.state = "CHECKING"
        yield self.env.timeout(0.6)

        if self.can_fulfill(bom):
            self.state = "READY"
            return True

        self.state = "PARTS_MISSING"
        # trigger restock in background (once)
        if not self._restock_in_progress:
            self._restock_in_progress = True
            self.env.process(self._restock())
        return False

    def _restock(self):
        self.state = "RESTOCKING"
        # lead time
        yield self.env.timeout(random.uniform(6.0, 18.0))

        # restock anything below reorder point
        for k, rp in self.reorder_point.items():
            if self.levels.get(k, 0) <= rp:
                self.levels[k] = int(self.levels.get(k, 0)) + int(self.restock_qty.get(k, 0))

        self._restock_in_progress = False
        self.state = "READY"


# =====================
# RECIPE (BOM) - driven by recipe_id
# =====================
def recipe_bom(recipe_id: int) -> dict:
    # recipe 0 = standard kit
    # recipe 1 = "pro" kit with more sensors/wiring
    if int(recipe_id) == 1:
        return {
            "fasteners": 10,
            "brackets": 4,
            "sensors": 2,
            "wiring_kit": 2,
            "label": 1,
            "foam": 1,
        }
    return {
        "fasteners": 8,
        "brackets": 4,
        "sensors": 1,
        "wiring_kit": 1,
        "label": 1,
        "foam": 1,
    }


# =====================
# ORDER
# =====================
class Order:
    def __init__(self, oid: int, batch_id: int, recipe_id: int):
        self.id = int(oid)
        self.batch_id = int(batch_id)
        self.recipe_id = int(recipe_id)


# =====================
# STATION 1 PROCESS (kitting)
# =====================
class KittingStation:
    """
    Station 1: Component Kitting
    - verifies stock
    - picks parts from bins into a kit tray
    - labels + prepares handoff to next station
    """
    def __init__(self, env: simpy.Environment):
        self.env = env
        self.orders_in = simpy.Store(env)

        self.state = "IDLE"  # IDLE / PROCESSING / WAITING_PARTS / FAULT
        self.inventory = Inventory(env)
        self.arm = RoboticArm2Axis(env, "ST1_ARM", home_xy=(0, 0))

        # coordinates (x,y) for bins / tray
        self.loc = {
            "fasteners_bin": (10, 40),
            "brackets_bin": (20, 50),
            "sensors_bin": (30, 60),
            "wiring_bin": (40, 55),
            "label_bin": (50, 45),
            "foam_bin": (60, 35),
            "kit_tray": (120, 60),
            "handoff": (160, 70),
        }

        # counters / last-cycle info (read by VSI)
        self.completed = 0
        self.last_cycle_time_s = 0.0
        self._cycle_started_at = None
        self._done_events = deque()  # timestamps for completions inside last step window

        env.process(self.run())

    def _push_done(self):
        self._done_events.append(self.env.now)

    def pop_done_since_last_step(self) -> int:
        # VSI wrapper will clear this each step.
        if len(self._done_events) == 0:
            return 0
        self._done_events.clear()
        return 1

    def is_fault(self) -> bool:
        return self.arm.state in ("FAILED", "REPAIRING") or self.state == "FAULT"

    def inventory_ok(self) -> bool:
        return self.inventory.state not in ("PARTS_MISSING", "RESTOCKING")

    def run(self):
        while True:
            order = yield self.orders_in.get()

            # if arm is failed, repair first then retry
            if self.arm.state == "FAILED":
                self.state = "FAULT"
                yield self.env.process(self.arm.repair())
                self.state = "IDLE"
                yield self.orders_in.put(order)
                continue

            self.state = "PROCESSING"
            self._cycle_started_at = self.env.now

            bom = recipe_bom(order.recipe_id)

            ok = yield self.env.process(self.inventory.check(bom))
            if not ok:
                # can't kit now; wait and retry
                self.state = "WAITING_PARTS"
                yield self.env.timeout(1.0)
                self.state = "IDLE"
                yield self.orders_in.put(order)
                continue

            # reserve/consume parts
            self.inventory.consume(bom)

            # Pick sequence (simple but realistic)
            for bin_key in ["fasteners_bin", "brackets_bin", "sensors_bin", "wiring_bin", "foam_bin"]:
                yield self.env.process(self.arm.move_to(*self.loc[bin_key]))
                yield self.env.process(self.arm.pick())
                if self.arm.state == "FAILED":
                    self.state = "FAULT"
                    yield self.env.process(self.arm.repair())
                    self.state = "IDLE"
                    yield self.orders_in.put(order)
                    break
                yield self.env.process(self.arm.move_to(*self.loc["kit_tray"]))
                yield self.env.process(self.arm.place())
            else:
                # label + scan verify
                yield self.env.process(self.arm.move_to(*self.loc["label_bin"]))
                yield self.env.process(self.arm.pick())
                if self.arm.state == "FAILED":
                    self.state = "FAULT"
                    yield self.env.process(self.arm.repair())
                    self.state = "IDLE"
                    yield self.orders_in.put(order)
                    continue
                yield self.env.timeout(0.35)
                yield self.env.process(self.arm.move_to(*self.loc["kit_tray"]))
                yield self.env.process(self.arm.place())

                # handoff
                yield self.env.process(self.arm.move_to(*self.loc["handoff"]))
                yield self.env.timeout(0.25)

                # cycle done
                self.completed += 1
                self.last_cycle_time_s = max(0.0, self.env.now - float(self._cycle_started_at or self.env.now))
                self._cycle_started_at = None
                self._push_done()

                # return home
                yield self.env.process(self.arm.home_return())
                self.state = "IDLE"


# =====================
# VSI <-> SimPy Wrapper
# =====================
class ST1_SimRuntime:
    def __init__(self):
        self.env = simpy.Environment()
        self.station = KittingStation(self.env)

        self.enabled = False
        self._next_order_id = 0

        # context from PLC inputs
        self.batch_id = 0
        self.recipe_id = 0

        # done pulse for last step
        self._done_pulse = 0

    def reset(self):
        # full reset: rebuild env + station
        self.__init__()

    def set_enabled(self, enabled: bool):
        self.enabled = bool(enabled)

    def set_context(self, batch_id: int, recipe_id: int):
        self.batch_id = int(batch_id)
        self.recipe_id = int(recipe_id)

    def _inject_orders_if_needed(self):
        # If enabled and no backlog, probabilistically inject a new order.
        if not self.enabled:
            return

        try:
            qlen = len(self.station.orders_in.items)
        except Exception:
            qlen = 0
        if qlen > 0:
            return

        # Arrival probability (tuned for VSI step sizes)
        if random.random() < 0.07:
            self._next_order_id += 1
            self.station.orders_in.put(Order(self._next_order_id, self.batch_id, self.recipe_id))

    def step(self, dt_s: float):
        if dt_s is None or dt_s <= 0:
            self._done_pulse = 0
            return

        self._done_pulse = 0
        self._inject_orders_if_needed()
        self.env.run(until=self.env.now + float(dt_s))
        self._done_pulse = int(self.station.pop_done_since_last_step())

    def result_snapshot(self):
        return {
            "completed_count": int(self.station.completed),
            "last_cycle_time_ms": int(float(self.station.last_cycle_time_s) * 1000.0),
            "inventory_state": str(self.station.inventory.state),
            "arm_state": str(self.station.arm.state),
        }

    def outputs(self):
        busy = 1 if self.station.state == "PROCESSING" else 0
        any_arm_failed = 1 if self.station.arm.state in ("FAILED", "REPAIRING") else 0
        inventory_ok = 1 if self.station.inventory_ok() else 0
        fault = 1 if self.station.is_fault() else 0
        ready = 1 if (self.enabled and busy == 0 and fault == 0 and inventory_ok == 1) else 0
        cycle_time_ms = int(float(self.station.last_cycle_time_s) * 1000.0)
        done = int(self._done_pulse)
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
        self._run_latched = False
        self._prev_cmd_start = 0
        self._prev_cmd_stop = 0
        self._prev_cmd_reset = 0

        # Latest SimPy snapshot copied into VSI mainThread (debug / KPIs)
        self.last_result = {
            "completed_count": 0,
            "last_cycle_time_ms": 0,
            "inventory_state": "",
            "arm_state": "",
            "batch_id": 0,
            "recipe_id": 0,
        }

        # Aggregated KPIs in VSI mainThread
        self.total_completed = 0
        self.total_cycle_time_ms = 0

# End of user custom code region. Please don't edit beyond this point.



    def mainThread(self):
        dSession = vsiCommonPythonApi.connectToServer(self.localHost, self.domain, self.portNum, self.componentId)
        vsiEthernetPythonGateway.initialize(dSession, self.componentId, bytes(srcMacAddress), bytes(srcIpAddress))
        try:
            vsiCommonPythonApi.waitForReset()

            # Start of user custom code region. Please apply edits only within these regions:  After Reset
            self._sim = ST1_SimRuntime()
            self._run_latched = False
            self._prev_cmd_start = 0
            self._prev_cmd_stop = 0
            self._prev_cmd_reset = 0

# End of user custom code region. Please don't edit beyond this point.
            self.updateInternalVariables()

            if(vsiCommonPythonApi.isStopRequested()):
                raise Exception("stopRequested")
            self.establishTcpUdpConnection()
            nextExpectedTime = vsiCommonPythonApi.getSimulationTimeInNs()
            while(vsiCommonPythonApi.getSimulationTimeInNs() < self.totalSimulationTime):

                # Start of user custom code region. Please apply edits only within these regions:  Inside the while loop

                # REMOVED: Moved to "Before sending the packet" region to ensure proper execution order
                # The edge detection and SimPy stepping must happen AFTER receiving the Ethernet packet
                # This ensures fresh PLC inputs are processed immediately

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

                # CRITICAL FIX: Receive on configured port (6001) NOT on the connection handle
                print(f"ST1 attempting to receive on PORT: {PLC_LineCoordinatorSocketPortNumber0}")
                receivedData = vsiEthernetPythonGateway.recvEthernetPacket(PLC_LineCoordinatorSocketPortNumber0)
                
                # DEBUG: Instrument receive path
                print(f"ST1 RX meta dest/src/len: {receivedData[0]}, {receivedData[1]}, {receivedData[3]}")
                if receivedData[3] > 0:
                    # Print first 16 bytes of payload as hex
                    payload_bytes = receivedData[2]
                    hex_bytes = ' '.join(f'{b:02x}' for b in payload_bytes[:min(16, len(payload_bytes))])
                    print(f"ST1 RX first 16 bytes: {hex_bytes}")
                
                if(receivedData[3] != 0):
                    self.decapsulateReceivedData(receivedData)

                # Start of user custom code region. Please apply edits only within these regions:  Before sending the packet

                # Process edge detection and SimPy stepping AFTER receiving the packet
                # This ensures we use FRESH inputs from PLC, not stale data from previous cycle
                
                # Edge detect start/stop/reset (latch run state) using FRESH inputs
                if self.mySignals.cmd_reset and not self._prev_cmd_reset:
                    self._run_latched = False
                    if self._sim is not None:
                        self._sim.reset()

                if self.mySignals.cmd_start and not self._prev_cmd_start:
                    self._run_latched = True

                if self.mySignals.cmd_stop and not self._prev_cmd_stop:
                    self._run_latched = False

                self._prev_cmd_start = int(self.mySignals.cmd_start)
                self._prev_cmd_stop = int(self.mySignals.cmd_stop)
                self._prev_cmd_reset = int(self.mySignals.cmd_reset)

                # Step SimPy using VSI simulationStep (ns -> s)
                dt_s = float(self.simulationStep) / 1e9 if self.simulationStep else 0.0

                if self._sim is not None:
                    self._sim.set_enabled(self._run_latched)
                    # recipe/batch context comes from PLC (FRESH data)
                    self._sim.set_context(self.mySignals.batch_id, self.mySignals.recipe_id)

                    self._sim.step(dt_s)
                    ready, busy, fault, done, cycle_time_ms, inventory_ok, any_arm_failed = self._sim.outputs()

                    # Copy SimPy outputs into VSI signals (sent to PLC in this SAME cycle)
                    self.mySignals.ready = int(ready)
                    self.mySignals.busy = int(busy)
                    self.mySignals.fault = int(fault)
                    self.mySignals.done = int(done)
                    self.mySignals.cycle_time_ms = int(cycle_time_ms)
                    self.mySignals.inventory_ok = int(inventory_ok)
                    self.mySignals.any_arm_failed = int(any_arm_failed)

                    # Always capture latest SimPy snapshot into mainThread variables
                    snap = self._sim.result_snapshot()
                    self.last_result["completed_count"] = int(snap.get("completed_count", 0))
                    self.last_result["last_cycle_time_ms"] = int(snap.get("last_cycle_time_ms", 0))
                    self.last_result["inventory_state"] = str(snap.get("inventory_state", ""))
                    self.last_result["arm_state"] = str(snap.get("arm_state", ""))
                    self.last_result["batch_id"] = int(self.mySignals.batch_id)
                    self.last_result["recipe_id"] = int(self.mySignals.recipe_id)

                    # Aggregate KPI only when a kit is completed (done pulse)
                    if self.mySignals.done == 1:
                        self.total_completed += 1
                        self.total_cycle_time_ms += int(self.mySignals.cycle_time_ms)

                # End of user custom code region. Please don't edit beyond this point.

                #Send ethernet packet to PLC_LineCoordinator
                self.sendEthernetPacketToPLC_LineCoordinator()

                # Start of user custom code region. Please apply edits only within these regions:  After sending the packet

                # End of user custom code region. Please don't edit beyond this point.

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
                if self.mySignals.done == 1:
                    print("  >>> SimPy RESULT (captured in mainThread):")
                    print("\tcompleted_count =", self.last_result.get("completed_count", 0))
                    print("\tlast_cycle_time_ms =", self.last_result.get("last_cycle_time_ms", 0))
                    print("\tbatch_id =", self.last_result.get("batch_id", 0))
                    print("\trecipe_id =", self.last_result.get("recipe_id", 0))

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

            # Print summary captured in mainThread
            avg_ms = (self.total_cycle_time_ms / float(self.total_completed)) if self.total_completed > 0 else 0.0
            print("=== ST1 SUMMARY (mainThread) ===")
            print("total_completed =", self.total_completed)
            print("avg_cycle_time_ms =", avg_ms)

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
        if(self.clientPortNum[ST1_ComponentKitting0] == 0):
            self.clientPortNum[ST1_ComponentKitting0] = vsiEthernetPythonGateway.tcpConnect(bytes(PLC_LineCoordinatorIpAddress), PLC_LineCoordinatorSocketPortNumber0)
            print(f"ST1 tcpConnect handle: {self.clientPortNum[ST1_ComponentKitting0]}")  # Keep for debugging

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

        # DEBUG: Print what we received
        print(f"ST1 decapsulate: destPort={self.receivedDestPortNumber}, srcPort={self.receivedSrcPortNumber}, len={self.receivedNumberOfBytes}")
        
        # FIX: Decode PLC command packets when we receive 9 bytes (regardless of source port)
        # PLC sends: cmd_start (?), cmd_stop (?), cmd_reset (?), batch_id (L), recipe_id (H) = 9 bytes
        if self.receivedNumberOfBytes == 9:
            print("Received 9-byte packet from PLC (command packet)")
            receivedPayload = bytes(self.receivedPayload)
            
            # Decode the 9-byte command packet
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

        #Send ethernet packet to PLC_LineCoordinator
        # Keep using port number 6001 for sending to match PLC's listening port
        print(f"ST1 sending to PLC on port: {PLC_LineCoordinatorSocketPortNumber0}")
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

    sT1_ComponentKitting = ST1_ComponentKitting(args)
    sT1_ComponentKitting.mainThread()



if __name__ == '__main__':
    main()
