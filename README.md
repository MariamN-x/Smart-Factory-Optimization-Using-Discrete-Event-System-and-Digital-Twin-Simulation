# 3D Printer Packaging Station – SCADA Simulation

**Developed by:** Mina Atef (only)

---

## What is this project?

This is a **software simulation** of a **3D printer packaging station**.  
It models how a real packaging line works using PLC logic, sensors, actuators,
and a SCADA interface.  
No real hardware is used.

---

## Main Components

The system is built from **four parts**:

- **Sensors** – detect box position, stock levels, and faults  
- **PLC** – controls the full packaging sequence  
- **Actuators** – motors, robot, flap folder, tape sealer, label unit, conveyors  
- **Human Resource (HR)** – simulates operator repairs and refills  

---

## Station 6 – Sensors & Actuators Blueprint


::contentReference[oaicite:0]{index=0}


**Station 6.png** represents the **blueprint of the packaging station**.

It shows:
- All **sensors** used in the station  
- All **actuators** controlled by the PLC  
- How the station is structured logically  

The SCADA screen displays these same signals live during the simulation.

---

## Packaging Flow

Each product follows this order:

1. Product arrives from the 3D printer  
2. Carton is created  
3. Robot places product in carton  
4. Flaps are closed  
5. Tape is applied  
6. Label is applied  
7. Box exits to final conveyor  

Only one product is processed per printer signal.

---

## Faults and Downtime

The system stops if:
- A machine fault occurs  
- Cartons, tape, or labels are empty  
- The final conveyor is full  

- **Red light** → system stopped  
- **Yellow light** → low stock warning  
- **Green light** → normal operation  

HR automatically handles repairs and refills.

---

## SCADA Interface

The SCADA GUI shows:
- System state (RUNNING / DOWNTIME)
- Sensors and actuators live values
- Tower lights
- KPIs (packages, availability, downtime)
- Event log

---

## How to Run

Install dependency:

pip install simpy

##Run the simulation:

python packaging_scada.py
Notes
Software-only simulation

No real PLC or hardware

Intended for learning and demonstration
<img width="1920" height=<img width="1918" height="1010" alt="SCADA" src="https://github.com/user-attachments/assets/f9c011ca-cf99-4871-9712-ab8e9a44df14" />
"1080" alt="Station 6" src="https://github.com/user-attachments/assets/101c0a76-65f4-401d-b78b-3d62002b710e" />

##Author: Mina Atef
