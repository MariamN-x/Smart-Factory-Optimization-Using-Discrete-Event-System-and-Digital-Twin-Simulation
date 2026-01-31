# Kitting Station 1 — PLC + SCADA Simulator (Python)

A desktop simulator for a small kitting cell in a 3D-printer assembly line.

It runs a discrete-event simulation with a PLC-style state machine, shows live status in a SCADA dashboard, and can replay runs in a 3D viewer from log files.

## Features

- End-to-end kitting flow:
  - order intake → inventory check → picking → kitting → mounting → soldering → output → reset
- Four simulated 2-axis arms:
  - Picking, Kitting, Mounting, Soldering
- Live SCADA-style view:
  - station state, queues/WIP, throughput, cycle times, arm utilization
- Failure injection:
  - random arm failures + repair handling + failure logs
- Logging + exports:
  - streaming event log (`.jsonl`)
  - run summary (`.json`)
  - cycle times (`.csv`)
- 3D replay viewer:
  - load a `.jsonl` log and scrub through the timeline with charts and metrics

## Project structure

- `main.py` — main app (GUI + simulation + SCADA)
- `station1_plc.py` — PLC controller (state machine + sequences)
- `station1_sensors.py` — read-only sensors (status + KPIs)
- `station1_actuators.py` — actuators (commands/actions)
- `visual.py` — 3D visualization + replay
- `station_info_panel.py` — workflow info panel used by the viewer

## Requirements

- Python 3.9+ recommended
- Python packages:
  - `simpy`
  - `PyQt5`
  - `numpy`
  - `matplotlib`
  - `PyOpenGL` (optional: `PyOpenGL_accelerate`)

Install (recommended in a virtual environment):

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate

pip install simpy PyQt5 numpy matplotlib PyOpenGL PyOpenGL_accelerate
