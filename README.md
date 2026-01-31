
# 3D Printer Packaging Station – SCADA Simulation

**Developed by:** Mina Atef (only)

---

## What is this project?

This is a **software simulation** of a **3D printer packaging station**.  
It models how a real packaging line works using **PLC logic**, **sensors**, **actuators**, and a **SCADA interface**.  
No real hardware is used.

---

## Main Components

The system is built from **four parts**:

- **Sensors** – detect box position, stock levels, and machine faults  
- **PLC** – controls the full packaging sequence and decision logic  
- **Actuators** – robot, motors, flap folder, tape sealer, label unit, conveyors  
- **Human Resource (HR)** – simulates operator repairs and material refills  

---

## Station 6 – Sensors & Actuators Blueprint

![Station 6 – Sensors and Actuators Blueprint](https://github.com/user-attachments/assets/101c0a76-65f4-401d-b78b-3d62002b710e)

**Station 6.png** represents the **blueprint of the packaging station**.

It shows:
- All **sensors** used in the station  
- All **actuators** controlled by the PLC  
- The logical structure of the station  

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
7. Box exits to the final conveyor  

Only one product is processed per printer signal.

---

## Faults and Downtime

The system stops if:
- A machine fault occurs  
- Cartons, tape, or labels are empty  
- The final conveyor is full  

Tower lights behavior:
- **Green** → normal operation  
- **Yellow** → low stock warning  
- **Red** → fault or downtime  

HR automatically handles repairs and refills.

---

## SCADA Interface

![SCADA Interface](https://github.com/user-attachments/assets/f9c011ca-cf99-4871-9712-ab8e9a44df14)

The SCADA GUI shows:
- System state (RUNNING / DOWNTIME)
- Live sensor values
- Live actuator states
- Tower lights
- KPIs (packages, availability, downtime)
- Event log

---

## How to Run

Install dependency:
```bash
pip install simpy
````

Run the simulation:

```bash
python packaging_scada.py
```
- **Simulation library:** SimPy  
- **GUI library:** Tkinter (built-in with Python)
---

## Notes

* Software-only simulation
* No real PLC or industrial hardware
* Intended for learning, testing, and demonstration

---

**Author:** Mina Atef

```
