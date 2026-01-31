# 3D Printer Production Line Digital Twin (PLC + ST1–ST6)

This repository contains a **station-level digital twin** of a 3D-printer manufacturing line.
It is controlled using **PLC-style handshakes** and a **pipeline execution model**.

The simulation uses two key technologies:

- **Innexis Virtual System Interconnect (VSI)** (closed source by Siemens)  
  Used to run multiple Python components as separate “devices” and exchange packets (PLC ↔ stations).

- **SimPy** (open-source Python library)  
  Used inside each station to model cycle timing, delays, faults, and maintenance as discrete-event processes.

---

## What’s Inside

- **PLC_LineCoordinator (Controller / Server)**
  - Runs the main scan loop (PLC behavior)
  - Sends commands to all stations (start/stop/reset + batch/recipe)
  - Receives station feedback packets
  - Maintains **virtual buffers** (WIP tokens) to enforce correct pipeline ordering
  - Uses **done-latch** logic to safely detect short `done` pulses

- **ST1–ST6 Stations (Clients)**
  - Each station runs a small state machine (ready/busy/done/fault)
  - Starts **only** when PLC sends a start command
  - Internally modeled using SimPy generator processes

---

## Stations Overview

| Station | Role | PLC Port | Example Station Outputs |
|---|---|---:|---|
| ST1 | Component Kitting | 6001 | ready/busy/done/fault + cycle time |
| ST2 | Frame Core Assembly | 6002 | counters (scrap/rework) + cycle time |
| ST3 | Electronics Wiring | 6003 | wiring checks + cycle time |
| ST4 | Calibration + Testing | 6004 | test pass/fail counters |
| ST5 | Quality Inspection | 6005 | accept/reject decision + counters |
| ST6 | Packaging + Dispatch | 6006 | packing/dispatch counters + downtime |

---

## How the Pipeline is Integrated

There are multiple ways to integrate a multi-station pipeline. This project follows the PLC approach and supports typical pipeline behavior (overlap between stations).

### A) Sequential (simple, lowest throughput)
- PLC runs ST1, waits done, then ST2, then ST3… until ST6.
- Easy, but it wastes time because stations do not overlap.

### B) True pipeline (overlap + ordering control)
- Multiple stations can run at the same time.
- PLC still enforces correct order using **virtual buffers** (WIP tokens).
- Example: ST2 can start only if `buffer_S1_to_S2 > 0`.

### C) Event-driven start (generator-based readiness)
- Stations expose readiness/busy/done signals.
- PLC reacts each scan based on signals and buffers.
- This is how you get deterministic behavior without race conditions.

**This repository uses (B) + (C).**  
Stations overlap, but only when the PLC allows it.

---

## Core Reliability Mechanisms

### 1) Generator-based station cycles (SimPy)
Each station’s “cycle” is modeled as a **generator** like:

- start is received → station becomes busy
- `yield env.timeout(cycle_time)` → time passes in simulation
- station sets done, updates counters, returns to ready

This makes timing deterministic and easy to test.

### 2) One-shot start + internal start latch
- PLC sends `cmd_start=1` as a short pulse (often one scan).
- Station latches it internally so it can finish the cycle even if start goes low next scan.

Why it matters: it prevents “missed starts” and repeated cycles.

### 3) Done pulse + PLC done-latch (edge-safe)
- Stations typically raise `done=1` briefly.
- PLC uses a **done-latch** per station to reliably detect completion even if the pulse is short.

Why it matters: it prevents “PLC never saw done” bugs.

### 4) Virtual buffers (WIP tokens)
PLC keeps counters that represent parts moving through the line:

- When ST1 completes: `buffer_S1_to_S2 += 1`
- Before starting ST2: require `buffer_S1_to_S2 > 0`
- When ST2 starts: `buffer_S1_to_S2 -= 1` and later `buffer_S2_to_S3 += 1`

Why it matters:
- prevents ST2 starting without input
- prevents duplicated production
- keeps the pipeline valid during parallel execution

---

## Communication (VSI Role)

VSI is used to run the PLC and each station as separate components and exchange packets:

- **PLC → Station:** command packet (start/stop/reset + batch_id + recipe_id)
- **Station → PLC:** feedback packet (ready/busy/done/fault + KPIs)

So the separation is realistic:
- distributed components
- network delay/scan timing effects
- packet decoding/encoding (pack/unpack)

---

## Requirements

### Python dependency
```bash
pip install simpy


## Authors

* Mina Adel
* Mina Atef
* George Sameh
* Sama Salem
* Mariam Nasr

### Supervised by

* Dr. Mohamed Abdelsalam
* Dr. Mohamed Elithy
* Dr. Mohamed El Hosseini

---

