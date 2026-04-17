# 3D Printer Production Line Digital Twin (PLC + ST1–ST6)

This project implements a **digital twin of a 3D printer production line** using a PLC-controlled pipeline.  
The system simulates real industrial behavior including station coordination, timing, faults, and performance monitoring.

---

## Overview

The system is built using:

- **Innexis VSI (Virtual System Interconnect)**  
  Used to run PLC and stations as separate components and handle communication.

- **SimPy**  
  Used to simulate station behavior such as cycle time, delays, failures, and maintenance.

---

## System Architecture
Server-Client Based 

### PLC Line Coordinator SERVER
- Controls the whole production line
- Sends commands (start / stop / reset)
- Receives station feedback
- Manages execution order using **virtual buffers**
- Uses **done-latch logic** to detect completion safely

### Stations (ST1–ST6) CLIENTS 
- Each station represents a production step
- Runs a simple state machine (ready / busy / done / fault)
- Starts only when commanded by the PLC
- Internally simulated using SimPy

---

## Stations Overview

| Station | Function | Port |
|--------|---------|------|
| ST1 | Component Kitting | 6001 |
| ST2 | Frame Assembly | 6002 |
| ST3 | Electronics Wiring | 6003 |
| ST4 | Calibration & Testing | 6004 |
| ST5 | Quality Inspection | 6005 |
| ST6 | Packaging & Dispatch | 6006 |

---

## Pipeline Execution

The system uses a **controlled pipeline with parallel execution**:

- Stations can run simultaneously  
- PLC ensures correct order using buffers  
- Each station starts only when input is available  

This provides:
- realistic production flow  
- no missing or duplicated parts  
- stable and deterministic execution  

---

## Key Concepts

### 1. SimPy-Based Simulation
Each station simulates:
- cycle time  
- delays  
- failures  
- maintenance  

---

### 2. Start Signal (Latch)
- PLC sends a short start signal  
- Station stores it internally  
- Prevents missed or repeated starts  

---

### 3. Done Signal (Latch)
- Stations send a short done pulse  
- PLC captures it using a latch  

Prevents missing completion signals.

---

### 4. Virtual Buffers (WIP)
Buffers represent parts moving between stations:

- ST1 → ST2 → ST3 → ... → ST6  
- Each station requires input from the previous one  

Ensures correct pipeline behavior.

---

## Communication

Handled using VSI packets:

- **PLC → Station:** commands (start / stop / reset)  
- **Station → PLC:** status + KPIs  

This simulates a real distributed industrial system.

---

## Requirements

Install dependency:

```bash
pip install simpy
```

---

## KPIs and Optimization

This project includes two dashboards.

---

### KPI Dashboard

Used to monitor the production line during runtime from generated logs.

<img width="1600" height="604" alt="kpi1" src="https://github.com/user-attachments/assets/6486928a-67ab-4c8d-aa8f-05e602c22f9d" />
station 3 
<img width="1580" height="639" alt="kpi2" src="https://github.com/user-attachments/assets/1dfe43ab-e532-4030-82b3-c105099c854f" />

---

### Optimization Dashboard and AI

Used to read and analyze production logs.  
It calculates KPIs such as cycle time, defects, and performance, then compares results.  
This helps find better system settings and improve the production process.
#### Login 
<img width="898" height="491" alt="login " src="https://github.com/user-attachments/assets/676e05bb-ed40-457a-b643-617a30d4514b" />



#### Main View
![Optimization 1](https://github.com/user-attachments/assets/ead4909b-aa05-4be9-adda-4f773db05efc)
![Optimization 2](https://github.com/user-attachments/assets/91909f0a-b152-4f3a-92fa-bd96caafa593)

#### Human Resources
![HR](https://github.com/user-attachments/assets/cefff9e6-5305-45b0-8e9d-ab09436b5e83)

#### Maintenance
![Maintenance](https://github.com/user-attachments/assets/52a0e3fd-3c03-482b-a445-cd8d5335574b)

#### Energy & Optimization
![Energy](https://github.com/user-attachments/assets/c411c8a3-bd00-47bb-8151-f5926b7d6c8f)

#### Analysis
![Analysis 1](https://github.com/user-attachments/assets/e16a633b-83a9-469f-b122-a3e6a6a00b10)
![Analysis 2](https://github.com/user-attachments/assets/1f050d51-ae40-4935-b5f4-885e3949548f)
![Analysis 3](https://github.com/user-attachments/assets/0677a670-901c-46be-b963-b0899d798a5a)

---

#### AI 
<img width="1311" height="565" alt="AI" src="https://github.com/user-attachments/assets/6d1d8492-4c94-48ed-bf0e-d7fa277751b0" />

## vizuilization 
<img width="1288" height="596" alt="vis" src="https://github.com/user-attachments/assets/016f1ca8-709b-4169-863d-3b9758909eb3" />
station 1
<img width="1103" height="662" alt="st1" src="https://github.com/user-attachments/assets/3319523f-06cb-4e65-a1fb-1642f0519d73" />

there are also visualizations for the rest of the stations
## Screenshots

![CLI](https://github.com/user-attachments/assets/feeb2d34-75fa-4c12-856e-a4de3688988e)

---

## Authors

- Mina Adel  
- Mina Atef  
- George Sameh  
- Sama Salem  
- Mariam Nasr  

---

## Supervised by

- Dr. Mohamed Abdelsalam  (Siemins)
- Dr. Mohamed Elithy      (Siemins)
- Dr. Mohamed El Hosseini  
- Dr. Nada (TA)
