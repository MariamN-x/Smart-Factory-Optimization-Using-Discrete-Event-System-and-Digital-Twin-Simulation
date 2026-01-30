#!/usr/bin/env python3
"""
Siemens Smart Factory Optimization Dashboard - Web Server
Real-time visualization with realistic fault injection and buffer management
"""

import json
import time
import threading
import random
import math
from datetime import datetime
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit
import plotly
import plotly.graph_objects as go
import numpy as np
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'siemens-smart-factory-2025'
socketio = SocketIO(app, cors_allowed_origins="*")

# Global variables
LOG_FILE = "factory_optimization_log.json"

class DashboardDataManager:
    """Manages real-time dashboard data with realistic fault simulation"""
    
    def __init__(self):
        self.data_lock = threading.Lock()
        self.current_data = {
            "production": {
                "throughput": 180.0,
                "target_throughput": 10,
                "total_products": 42,
                "batch_id": 43,
                "simulation_time": 0,
                "baseline_throughput": 180.0,
            },
            "energy": {
                "total_energy": 18.5,
                "energy_per_product": 0.44,
                "max_energy": 50,
                "station_energy": {
                    "S1": 2.5, "S2": 2.8, "S3": 3.1, "S4": 3.4, "S5": 3.7, "S6": 4.0
                }
            },
            "quality": {
                "first_pass_yield": 96.5,
                "scrap_rate": 2.1,
                "rework_rate": 1.4,
                "accept_reject": {"accept": 402, "reject": 18}
            },
            "stations": {
                "S1": {"status": "running", "uptime": 95, "downtime": 5, "fault": False, 
                       "fault_type": None, "fault_duration": 0, "cycle_time": 30},
                "S2": {"status": "running", "uptime": 92, "downtime": 8, "fault": False,
                       "fault_type": None, "fault_duration": 0, "cycle_time": 45},
                "S3": {"status": "running", "uptime": 98, "downtime": 2, "fault": False,
                       "fault_type": None, "fault_duration": 0, "cycle_time": 35},
                "S4": {"status": "running", "uptime": 90, "downtime": 10, "fault": False,
                       "fault_type": None, "fault_duration": 0, "cycle_time": 50},
                "S5": {"status": "running", "uptime": 99, "downtime": 1, "fault": False,
                       "fault_type": None, "fault_duration": 0, "cycle_time": 40},
                "S6": {"status": "running", "uptime": 97, "downtime": 3, "fault": False,
                       "fault_type": None, "fault_duration": 0, "cycle_time": 55}
            },
            "buffers": {
                "S1_to_S2": 1.0,
                "S2_to_S3": 1.0,
                "S3_to_S4": 1.0,
                "S4_to_S5": 1.0,
                "S5_to_S6": 1.0
            },
            "optimization": {
                "mode": "balanced",
                "emergency_stop": False,
                "shift_active": True,
                "bottleneck": None,
                "maintenance_schedule": {},
                "active_faults": []
            },
            "events": [
                {
                    "timestamp": datetime.now().isoformat(),
                    "type": "info",
                    "message": "System initialized with realistic simulation data"
                },
                {
                    "timestamp": datetime.now().isoformat(),
                    "type": "info", 
                    "message": "Production line running at optimal capacity"
                }
            ],
            "kpis": {
                "oee": 82.5,
                "availability": 90.0,
                "performance": 92.0,
                "quality_rate": 96.5
            },
            "simulation": {
                "fault_impact_active": False,
                "fault_start_time": 0,
                "fault_duration": 0,
                "fault_station": None,
                "fault_type": None
            }
        }
        
        # Fault simulation parameters
        self.fault_types = ["mechanical", "electrical", "sensor", "communication", "software"]
        self.fault_impacts = {
            "mechanical": {"throughput_impact": 0.3, "energy_impact": 1.8, "quality_impact": 0.7},
            "electrical": {"throughput_impact": 0.2, "energy_impact": 2.0, "quality_impact": 0.8},
            "sensor": {"throughput_impact": 0.4, "energy_impact": 1.2, "quality_impact": 0.5},
            "communication": {"throughput_impact": 0.6, "energy_impact": 1.1, "quality_impact": 0.9},
            "software": {"throughput_impact": 0.7, "energy_impact": 1.0, "quality_impact": 0.6}
        }
        
        # Station-specific fault characteristics
        self.station_fault_characteristics = {
            "S1": {"severity": "high", "recovery_time": 45, "propagation": 0.8},
            "S2": {"severity": "medium", "recovery_time": 30, "propagation": 0.6},
            "S3": {"severity": "medium", "recovery_time": 25, "propagation": 0.4},
            "S4": {"severity": "low", "recovery_time": 20, "propagation": 0.3},
            "S5": {"severity": "low", "recovery_time": 15, "propagation": 0.2},
            "S6": {"severity": "medium", "recovery_time": 25, "propagation": 0.5}
        }
        
        # Buffer management system
        self.buffer_capacities = {
            "S1_to_S2": 3,
            "S2_to_S3": 3, 
            "S3_to_S4": 3,
            "S4_to_S5": 3,
            "S5_to_S6": 3
        }
        
        self.buffer_fill_rates = {
            "S1_to_S2": 0.05,
            "S2_to_S3": 0.04,
            "S3_to_S4": 0.04,
            "S4_to_S5": 0.03,
            "S5_to_S6": 0.03
        }
        
        # Station dependencies
        self.station_outputs = {
            "S1": ["S1_to_S2"],
            "S2": ["S2_to_S3"],
            "S3": ["S3_to_S4"],
            "S4": ["S4_to_S5"],
            "S5": ["S5_to_S6"],
            "S6": []
        }
        
        self.station_inputs = {
            "S1": [],
            "S2": ["S1_to_S2"],
            "S3": ["S2_to_S3"],
            "S4": ["S3_to_S4"],
            "S5": ["S4_to_S5"],
            "S6": ["S5_to_S6"]
        }
        
        # Cascading effects tracking
        self.cascading_effects = {}
        self.buffer_monitoring_active = False
        
        # Start dynamic data updates
        self.start_dynamic_updates()
    
    def start_dynamic_updates(self):
        """Start background thread for dynamic data updates"""
        update_thread = threading.Thread(target=self._dynamic_update_loop, daemon=True)
        update_thread.start()
    
    def _dynamic_update_loop(self):
        """Background thread to update dynamic data"""
        while True:
            time.sleep(2)  # Update every 2 seconds
            
            # Update from log if available
            self.update_from_log()
            
            # Update dynamic simulation data
            with self.data_lock:
                self._update_dynamic_data()
                
                # Emit update via WebSocket
                socketio.emit('data_update', self.current_data)
    
    def _update_dynamic_data(self):
        """Update dynamic simulation data"""
        # Update simulation time
        self.current_data["production"]["simulation_time"] += 2
        
        # Check if line is stopped
        line_stopped = (
            self.current_data["optimization"]["emergency_stop"] or
            any(station["status"] == "stopped" 
                for station in self.current_data["stations"].values())
        )
        
        if line_stopped:
            # Line is stopped
            self.current_data["production"]["throughput"] = 0
            
            # Ensure all stations show as stopped (except faulting ones)
            for station in self.current_data["stations"]:
                if not self.current_data["stations"][station]["fault"]:
                    self.current_data["stations"][station]["status"] = "stopped"
        
        elif self.current_data["simulation"]["fault_impact_active"]:
            # Fault is active - handle buffer dynamics
            self._update_buffer_during_fault()
            
            # Update throughput based on fault impact
            fault_station = self.current_data["simulation"]["fault_station"]
            if fault_station:
                fault_type = self.current_data["simulation"]["fault_type"]
                fault_impact = self.fault_impacts.get(fault_type, self.fault_impacts["mechanical"])
                baseline = self.current_data["production"]["baseline_throughput"]
                self.current_data["production"]["throughput"] = baseline * fault_impact["throughput_impact"]
        else:
            # Normal operation - small fluctuations
            base_throughput = self.current_data["production"]["baseline_throughput"]
            variation = random.uniform(-5, 5)
            self.current_data["production"]["throughput"] = max(0, base_throughput + variation)
            
            # Update batch ID occasionally
            if random.random() < 0.05:
                self.current_data["production"]["batch_id"] += 1
                self.current_data["production"]["total_products"] += random.randint(1, 3)
        
        # Update OEE
        self._calculate_realistic_oee()
    
    def _update_buffer_during_fault(self):
        """Update buffer levels during fault"""
        if not self.current_data["simulation"]["fault_impact_active"]:
            return
        
        fault_station = self.current_data["simulation"]["fault_station"]
        if not fault_station:
            return
        
        # Get input buffer for faulting station
        input_buffers = self.station_inputs.get(fault_station, [])
        
        for buffer_name in input_buffers:
            if buffer_name not in self.cascading_effects.get("buffer_fill_started", {}):
                continue
            
            # Calculate time since fault started
            fault_start = self.current_data["simulation"]["fault_start_time"]
            current_time = time.time()
            time_filling = current_time - fault_start
            
            # Calculate fill amount
            fill_amount = time_filling * self.buffer_fill_rates.get(buffer_name, 0.03)
            
            # Update buffer level
            current_level = self.current_data["buffers"].get(buffer_name, 0)
            new_level = min(
                current_level + fill_amount,
                self.buffer_capacities[buffer_name]
            )
            
            self.current_data["buffers"][buffer_name] = round(new_level, 1)
            
            # Check buffer status
            buffer_capacity = self.buffer_capacities[buffer_name]
            fill_percentage = (new_level / buffer_capacity) * 100
            
            # Handle buffer warnings and stops
            if fill_percentage >= 80 and fill_percentage < 90:
                if not self.cascading_effects.get("warning_sent", False):
                    self._create_buffer_warning(buffer_name, fill_percentage)
                    self.cascading_effects["warning_sent"] = True
            
            elif fill_percentage >= 90:
                if not self.cascading_effects.get("upstream_stopped", False):
                    self._stop_upstream_stations(fault_station, buffer_name)
    
    def _create_buffer_warning(self, buffer_name, fill_percentage):
        """Create buffer warning event"""
        warning_event = {
            "timestamp": datetime.now().isoformat(),
            "type": "warning",
            "message": f"WARNING: Buffer {buffer_name} is {fill_percentage:.0f}% full. "
                      f"Upstream stations may need to stop soon."
        }
        self.current_data["events"].insert(0, warning_event)
        
        socketio.emit('buffer_warning', {
            "buffer": buffer_name,
            "percentage": fill_percentage
        })
        
        # Keep only last 50 events
        if len(self.current_data["events"]) > 50:
            self.current_data["events"] = self.current_data["events"][:50]
    
    def _stop_upstream_stations(self, fault_station, full_buffer):
        """Stop upstream stations to prevent buffer overflow"""
        # Find which station feeds this buffer
        upstream_station = None
        for station, outputs in self.station_outputs.items():
            if full_buffer in outputs:
                upstream_station = station
                break
        
        if upstream_station:
            # Stop the upstream station
            self.current_data["stations"][upstream_station]["status"] = "stopped"
            
            # Mark in cascading effects
            if "stations_stopped" not in self.cascading_effects:
                self.cascading_effects["stations_stopped"] = []
            self.cascading_effects["stations_stopped"].append(upstream_station)
            self.cascading_effects["upstream_stopped"] = True
            
            # Create event
            stop_event = {
                "timestamp": datetime.now().isoformat(),
                "type": "warning",
                "message": f"UPSTREAM STOP: Station {upstream_station} stopped to prevent "
                          f"buffer {full_buffer} overflow (feeding {fault_station})"
            }
            self.current_data["events"].insert(0, stop_event)
            
            socketio.emit('upstream_stopped', {
                "fault_station": fault_station,
                "upstream_station": upstream_station,
                "buffer": full_buffer
            })
            
            # Keep only last 50 events
            if len(self.current_data["events"]) > 50:
                self.current_data["events"] = self.current_data["events"][:50]
    
    def simulate_fault_injection(self, station, fault_type=None):
        """Simulate a fault injection with realistic effects"""
        with self.data_lock:
            # Choose random fault type if not specified
            if fault_type is None:
                fault_type = random.choice(self.fault_types)
            
            # Get fault characteristics
            station_char = self.station_fault_characteristics.get(station, {})
            fault_impact = self.fault_impacts.get(fault_type, self.fault_impacts["mechanical"])
            
            # Store baseline throughput before fault
            if not self.current_data["simulation"]["fault_impact_active"]:
                self.current_data["production"]["baseline_throughput"] = (
                    self.current_data["production"]["throughput"]
                )
            
            # Activate fault simulation
            self.current_data["simulation"]["fault_impact_active"] = True
            self.current_data["simulation"]["fault_start_time"] = time.time()
            self.current_data["simulation"]["fault_duration"] = station_char.get("recovery_time", 30)
            self.current_data["simulation"]["fault_station"] = station
            self.current_data["simulation"]["fault_type"] = fault_type
            
            # Update station status
            self.current_data["stations"][station]["status"] = "fault"
            self.current_data["stations"][station]["fault"] = True
            self.current_data["stations"][station]["fault_type"] = fault_type
            self.current_data["stations"][station]["fault_duration"] = (
                self.current_data["simulation"]["fault_duration"]
            )
            
            # Initialize cascading effects
            self.cascading_effects = {
                "fault_station": station,
                "fault_type": fault_type,
                "upstream_stopped": False,
                "warning_sent": False,
                "buffer_fill_started": {},
                "stations_stopped": []
            }
            
            # Mark which buffers will fill
            input_buffers = self.station_inputs.get(station, [])
            for buffer in input_buffers:
                self.cascading_effects["buffer_fill_started"][buffer] = time.time()
            
            # Apply throughput impact
            baseline = self.current_data["production"]["baseline_throughput"]
            throughput_impact = fault_impact["throughput_impact"]
            self.current_data["production"]["throughput"] = baseline * throughput_impact
            
            # Apply energy impact
            for st in ["S1", "S2", "S3", "S4", "S5", "S6"]:
                if st == station:
                    # Faulting station uses more energy
                    self.current_data["energy"]["station_energy"][st] *= fault_impact["energy_impact"]
            
            # Apply quality impact
            quality_impact = fault_impact["quality_impact"]
            self.current_data["quality"]["first_pass_yield"] *= quality_impact
            self.current_data["quality"]["scrap_rate"] *= (1 + (1 - quality_impact))
            
            # Add to active faults list
            if station not in self.current_data["optimization"]["active_faults"]:
                self.current_data["optimization"]["active_faults"].append(station)
            
            # Create fault event
            fault_event = {
                "timestamp": datetime.now().isoformat(),
                "type": "error",
                "message": f"FAULT INJECTED: {station} - {fault_type} fault. "
                          f"Throughput reduced to {throughput_impact*100:.0f}%. "
                          f"Buffer will fill in {station_char.get('recovery_time', 30)}s."
            }
            self.current_data["events"].insert(0, fault_event)
            
            # Keep only last 50 events
            if len(self.current_data["events"]) > 50:
                self.current_data["events"] = self.current_data["events"][:50]
            
            # Schedule automatic recovery
            recovery_time = self.current_data["simulation"]["fault_duration"]
            threading.Timer(recovery_time, self.recover_from_fault, args=[station]).start()
            
            # Schedule line stop if fault persists
            line_stop_time = min(30, recovery_time + 10)  # Stop line if fault persists
            threading.Timer(line_stop_time, self._auto_stop_line, args=[station]).start()
            
            # Emit update
            socketio.emit('fault_injected', {
                "station": station,
                "fault_type": fault_type,
                "duration": recovery_time,
                "line_stop_time": line_stop_time
            })
            
            print(f"Simulated {fault_type} fault injected to {station}")
            print(f"  Throughput impact: {throughput_impact*100:.0f}%")
            print(f"  Buffer will fill in {recovery_time}s")
            print(f"  Line will auto-stop in {line_stop_time}s if fault persists")
            
            return True
    
    def _auto_stop_line(self, station):
        """Automatically stop line if fault persists"""
        with self.data_lock:
            if (self.current_data["simulation"]["fault_impact_active"] and 
                self.current_data["simulation"]["fault_station"] == station):
                
                print(f"  Auto-stopping line due to persistent fault at {station}")
                
                # Stop all stations
                for st in self.current_data["stations"]:
                    self.current_data["stations"][st]["status"] = "stopped"
                
                # Set throughput to 0
                self.current_data["production"]["throughput"] = 0
                
                # Create auto-stop event
                stop_event = {
                    "timestamp": datetime.now().isoformat(),
                    "type": "warning",
                    "message": f"AUTO LINE STOP: Fault at {station} persisted. "
                              f"Line stopped to prevent damage."
                }
                self.current_data["events"].insert(0, stop_event)
                
                socketio.emit('auto_line_stop', {"station": station})
                
                # Keep only last 50 events
                if len(self.current_data["events"]) > 50:
                    self.current_data["events"] = self.current_data["events"][:50]
    
    def recover_from_fault(self, station):
        """Recover from simulated fault"""
        with self.data_lock:
            # Reset station status
            self.current_data["stations"][station]["status"] = "running"
            self.current_data["stations"][station]["fault"] = False
            self.current_data["stations"][station]["fault_type"] = None
            self.current_data["stations"][station]["fault_duration"] = 0
            
            # Remove from active faults
            if station in self.current_data["optimization"]["active_faults"]:
                self.current_data["optimization"]["active_faults"].remove(station)
            
            # Check if all faults are cleared
            if not self.current_data["optimization"]["active_faults"]:
                self.current_data["simulation"]["fault_impact_active"] = False
                
                # Restart any stopped upstream stations
                for st in self.current_data["stations"]:
                    if self.current_data["stations"][st]["status"] == "stopped":
                        self.current_data["stations"][st]["status"] = "running"
                
                # Restore throughput to baseline
                baseline = self.current_data["production"]["baseline_throughput"]
                self.current_data["production"]["throughput"] = baseline
                
                # Gradually drain buffers
                self._gradually_drain_buffers()
            
            # Create recovery event
            recovery_event = {
                "timestamp": datetime.now().isoformat(),
                "type": "info",
                "message": f"FAULT RECOVERED: {station} back online. Production returning to normal."
            }
            self.current_data["events"].insert(0, recovery_event)
            
            # Keep only last 50 events
            if len(self.current_data["events"]) > 50:
                self.current_data["events"] = self.current_data["events"][:50]
            
            # Recalculate OEE after recovery
            self._calculate_realistic_oee()
            
            # Emit update
            socketio.emit('fault_recovered', {"station": station})
            
            print(f"Recovered from {station} fault")
    
    def _gradually_drain_buffers(self):
        """Gradually drain buffers that filled up during fault"""
        # Start a thread to gradually drain buffers
        threading.Thread(target=self._drain_buffers_thread, daemon=True).start()
    
    def _drain_buffers_thread(self):
        """Thread to gradually drain buffers"""
        drain_duration = 15  # Drain over 15 seconds
        start_time = time.time()
        
        while time.time() - start_time < drain_duration:
            time.sleep(1)
            
            with self.data_lock:
                # Calculate drain progress
                elapsed = time.time() - start_time
                drain_factor = elapsed / drain_duration
                
                # Drain each buffer
                for buffer_name in self.current_data["buffers"]:
                    current_level = self.current_data["buffers"][buffer_name]
                    target_level = 1.0
                    
                    # Calculate new level
                    new_level = current_level - ((current_level - target_level) * drain_factor)
                    self.current_data["buffers"][buffer_name] = max(0, round(new_level, 1))
        
        # Final buffer state
        with self.data_lock:
            for buffer_name in self.current_data["buffers"]:
                self.current_data["buffers"][buffer_name] = 1.0
    
    def _calculate_realistic_oee(self):
        """Calculate realistic OEE values"""
        base_oee = 82.5
        
        # Adjust based on current conditions
        if self.current_data["simulation"]["fault_impact_active"]:
            # Reduce OEE during faults
            fault_factor = 0.6
            base_oee *= fault_factor
        
        # Add small random variation
        variation = random.uniform(-3, 3)
        oee = base_oee + variation
        
        # Ensure within bounds
        oee = max(10, min(100, oee))
        
        # Set OEE and components
        self.current_data["kpis"]["oee"] = oee
        
        # Calculate components
        self.current_data["kpis"]["availability"] = random.uniform(88, 94)
        self.current_data["kpis"]["performance"] = random.uniform(92, 97)
        self.current_data["kpis"]["quality_rate"] = random.uniform(96, 99)
        
        # Show in console
        print(f"OEE: {oee:.1f}%")
    
    def _calculate_kpis(self):
        with self.data_lock:
            try:
                # Get production data
                throughput = self.current_data["production"]["throughput"]
                target_throughput = self.current_data["production"].get("target_throughput", 10)
                total_products = self.current_data["production"]["total_products"]
                
                # Get quality data
                yield_rate = self.current_data["quality"]["first_pass_yield"]
                
                # Calculate performance (throughput vs target)
                if target_throughput > 0:
                    performance = min((throughput / target_throughput) * 100, 100)
                else:
                    performance = 0
                
                # Calculate availability based on station status
                # Simulate based on throughput - if throughput > 0, stations are running
                if throughput > 0:
                    # High throughput = high availability
                    availability = 85 + min((throughput / target_throughput) * 15, 15)
                else:
                    availability = 0
                
                # Quality rate from first pass yield
                quality_rate = yield_rate
                
                # Calculate OEE
                if availability > 0 and performance > 0 and quality_rate > 0:
                    oee = (availability * performance * quality_rate) / 10000
                else:
                    oee = 0
                
                # Update KPI values
                self.current_data["kpis"]["availability"] = round(availability, 1)
                self.current_data["kpis"]["performance"] = round(performance, 1)
                self.current_data["kpis"]["quality_rate"] = round(quality_rate, 1)
                self.current_data["kpis"]["oee"] = round(oee, 1)
                
                print(f"KPIs: OEE={oee:.1f}%, Availability={availability:.1f}%, "
                    f"Performance={performance:.1f}%, Quality={quality_rate:.1f}%")
                
            except Exception as e:
                print(f"Error calculating KPIs: {e}")
                # Fallback to reasonable values
                self.current_data["kpis"]["availability"] = 88.5
                self.current_data["kpis"]["performance"] = 92.3
                self.current_data["kpis"]["quality_rate"] = 96.7
                self.current_data["kpis"]["oee"] = 78.5
        
    def update_from_log(self):
        try:
            if not os.path.exists(LOG_FILE):
                print(f"Log file not found: {LOG_FILE}")
                return False
            
            # DEBUG: Show file modification time
            mod_time = os.path.getmtime(LOG_FILE)
            print(f"Reading log file (modified: {datetime.fromtimestamp(mod_time).strftime('%H:%M:%S.%f')})")
            
            # READ FRESH EVERY TIME - don't cache
            with open(LOG_FILE, 'r') as f:
                log_data = json.load(f)
            
            with self.data_lock:
                has_updates = False
                latest_snapshot = None
                
                # Find the MOST RECENT performance_snapshot
                if "performance_history" in log_data and log_data["performance_history"]:
                    # Find the latest performance_snapshot
                    for event in reversed(log_data["performance_history"]):
                        if event.get("event_type") == "performance_snapshot":
                            latest_snapshot = event
                            print(f"Found snapshot at sim_time: {event.get('sim_time')}")
                            break
                    
                    if latest_snapshot:
                        data = latest_snapshot.get("data", {})
                        
                        # DEBUG: Show what we found
                        print(f"Processing snapshot - Throughput: {data.get('throughput')}, Products: {data.get('total_products')}")
                        
                        # Update production metrics
                        throughput = data.get("throughput", 0)
                        if throughput != self.current_data["production"]["throughput"]:
                            self.current_data["production"]["throughput"] = float(throughput)
                            has_updates = True
                            print(f"Updated throughput: {throughput}")
                        
                        total_products = data.get("total_products", 0)
                        if total_products != self.current_data["production"]["total_products"]:
                            self.current_data["production"]["total_products"] = int(total_products)
                            has_updates = True
                            print(f"Updated total products: {total_products}")
                        
                        # Update energy metrics
                        if "energy_consumption" in data and isinstance(data["energy_consumption"], dict):
                            new_energy = {k: float(v) for k, v in data["energy_consumption"].items()}
                            if new_energy != self.current_data["energy"]["station_energy"]:
                                self.current_data["energy"]["station_energy"] = new_energy
                                total_energy = sum(new_energy.values())
                                self.current_data["energy"]["total_energy"] = total_energy
                                
                                if total_products > 0:
                                    self.current_data["energy"]["energy_per_product"] = total_energy / total_products
                                else:
                                    self.current_data["energy"]["energy_per_product"] = 0
                                has_updates = True
                                print(f"Updated energy data")
                        
                        # Update quality metrics
                        if "quality_metrics" in data and isinstance(data["quality_metrics"], dict):
                            qm = data["quality_metrics"]
                            fpy = qm.get("first_pass_yield", 0)
                            if fpy != self.current_data["quality"]["first_pass_yield"]:
                                self.current_data["quality"]["first_pass_yield"] = float(fpy)
                                has_updates = True
                                print(f"Updated yield: {fpy}%")
                            
                            if "scrap_rate" in qm:
                                self.current_data["quality"]["scrap_rate"] = float(qm["scrap_rate"])
                            
                            if "rework_rate" in qm:
                                self.current_data["quality"]["rework_rate"] = float(qm["rework_rate"])
                        
                        # Update buffers
                        if "buffer_levels" in data and isinstance(data["buffer_levels"], dict):
                            new_buffers = {}
                            for key, value in data["buffer_levels"].items():
                                new_buffers[key] = int(value)
                            
                            if new_buffers != self.current_data["buffers"]:
                                self.current_data["buffers"] = new_buffers
                                has_updates = True
                                print(f"Updated buffers: {new_buffers}")
                        
                        # Update simulation time
                        sim_time = latest_snapshot.get("sim_time", 0)
                        if sim_time != self.current_data["production"]["simulation_time"]:
                            self.current_data["production"]["simulation_time"] = float(sim_time)
                            has_updates = True
                        
                        # Update optimization mode from file
                        if "optimization_parameters" in log_data:
                            opt_params = log_data["optimization_parameters"]
                            mode = opt_params.get("mode", "balanced")
                            if mode != self.current_data["optimization"]["mode"]:
                                self.current_data["optimization"]["mode"] = mode
                                has_updates = True
                            
                            target = opt_params.get("target_throughput", 10)
                            if target != self.current_data["production"]["target_throughput"]:
                                self.current_data["production"]["target_throughput"] = target
                                has_updates = True
                        
                        # Check for bottlenecks in history
                        bottleneck = None
                        for event in reversed(log_data["performance_history"]):
                            if event.get("event_type") == "bottleneck":
                                bottleneck = event.get("data", {}).get("station")
                                if bottleneck:
                                    break
                        
                        if bottleneck != self.current_data["optimization"]["bottleneck"]:
                            self.current_data["optimization"]["bottleneck"] = bottleneck
                            has_updates = True
                            if bottleneck:
                                print(f"Bottleneck detected at {bottleneck}")
                    
                    # Process other events for the event log
                    new_events = []
                    for event in reversed(log_data["performance_history"][-20:]):  # Last 20 events
                        event_type = event.get("event_type")
                        timestamp = event.get("timestamp", "")
                        
                        if event_type == "bottleneck":
                            station = event.get("data", {}).get("station", "")
                            wait_time = event.get("data", {}).get("wait_time", 0)
                            new_events.append({
                                "timestamp": timestamp,
                                "type": "warning",
                                "message": f"Bottleneck at {station} (wait: {wait_time}s)"
                            })
                        elif event_type == "performance_snapshot":
                            throughput = event.get("data", {}).get("throughput", 0)
                            if throughput > 0:
                                new_events.append({
                                    "timestamp": timestamp,
                                    "type": "info",
                                    "message": f"Throughput: {throughput:.1f} units/hour"
                                })
                        elif event_type == "shift_change":
                            hour = event.get("data", {}).get("hour", 0)
                            new_events.append({
                                "timestamp": timestamp,
                                "type": "info",
                                "message": f"Shift hour: {hour}"
                            })
                    
                    # Add new events if they're not already in the log
                    for new_event in new_events:
                        if not any(e["message"] == new_event["message"] for e in self.current_data["events"]):
                            self.current_data["events"].insert(0, new_event)
                            has_updates = True
                    
                    # Keep only last 50 events
                    if len(self.current_data["events"]) > 50:
                        self.current_data["events"] = self.current_data["events"][:50]
                    
                    if has_updates:
                        # Update batch ID based on simulation time
                        self.current_data["production"]["batch_id"] = int(self.current_data["production"]["simulation_time"] / 3600) + 1
                        
                        # Calculate KPIs
                        self._calculate_kpis()
                        
                        # Emit update via WebSocket
                        socketio.emit('data_update', self.current_data)
                        print(f"✓ Dashboard updated at {datetime.now().strftime('%H:%M:%S.%f')[:-3]}")
                        return True
                    else:
                        print(f"No changes in data")
                        return False
                else:
                    print("No performance_history in log")
                    return False
                    
        except json.JSONDecodeError as e:
            print(f"JSON decode error: {e}")
            return False
        except Exception as e:
            print(f"Error updating from log: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _fill_missing_data(self):
        """Fill missing data with realistic values"""
        with self.data_lock:
            # Generate station energy consumption
            for station in self.current_data["stations"]:
                self.current_data["energy"]["station_energy"][station] = random.uniform(2, 5)
            
            # Generate buffer levels
            for buffer in ["S1_to_S2", "S2_to_S3", "S3_to_S4", "S4_to_S5", "S5_to_S6"]:
                self.current_data["buffers"][buffer] = random.randint(0, 3)
            
            # Generate station status (mostly running, some idle)
            for station in self.current_data["stations"]:
                if random.random() > 0.2:
                    self.current_data["stations"][station]["status"] = "running"
                    self.current_data["stations"][station]["uptime"] += 1
                else:
                    self.current_data["stations"][station]["status"] = "idle"
                    self.current_data["stations"][station]["downtime"] += 1
    
    def simulate_random_fault(self):
        """Simulate a random fault for demonstration"""
        stations = ["S1", "S2", "S3", "S4", "S5", "S6"]
        station = random.choice(stations)
        fault_type = random.choice(self.fault_types)
        
        # Only inject if not already in fault
        if not self.current_data["stations"][station]["fault"]:
            self.simulate_fault_injection(station, fault_type)
            return True
        return False
    def inject_test_data(self):
        with self.data_lock:
            # Check if we already have real data
            if self.current_data["production"]["throughput"] > 0:
                # We have real data, don't override
                return
            
            print("No real data, injecting test data")
            
            # Only inject test data if throughput is zero
            self.current_data["production"]["throughput"] = np.random.uniform(8, 12)
            self.current_data["production"]["total_products"] += np.random.randint(1, 3)
            self.current_data["production"]["batch_id"] += 1
            self.current_data["production"]["simulation_time"] += 2
            
            # Simulate energy data
            for station in ["S1", "S2", "S3", "S4", "S5", "S6"]:
                self.current_data["energy"]["station_energy"][station] = np.random.uniform(2, 5)
            
            # Add test event
            event = {
                "timestamp": datetime.now().isoformat(),
                "type": "info",
                "message": "Test data injected - waiting for simulation data"
            }
            self.current_data["events"].insert(0, event)
            
            # Keep only last 50 events
            if len(self.current_data["events"]) > 50:
                self.current_data["events"] = self.current_data["events"][:50]
            
            # Recalculate KPIs
            self._calculate_kpis()
        
    def data_updater():
        last_file_size = 0
        
        while True:
            try:
                # Check if file has changed
                if os.path.exists(LOG_FILE):
                    current_size = os.path.getsize(LOG_FILE)
                    if current_size != last_file_size:
                        print(f"File changed: {current_size} bytes")
                        last_file_size = current_size
                
                # Always try to update from real log file
                if data_manager.update_from_log():
                    # Successfully read from log
                    pass
                else:
                    # Log file exists but has no data yet
                    print("Log file empty or no snapshot data")
                    
                    # Only inject test data if we have absolutely no data
                    if data_manager.current_data["production"]["throughput"] == 0:
                        data_manager.inject_test_data()
                        socketio.emit('data_update', data_manager.current_data)
                
            except Exception as e:
                print(f"Error in data updater: {e}")
        
            time.sleep(1)  # Check every 1 second (more frequent)
# Initialize data manager
data_manager = DashboardDataManager()

@app.route('/')
def index():
    """Main dashboard page"""
    return render_template('dashboard.html')

@app.route('/api/data')
def get_data():
    """API endpoint to get current dashboard data"""
    return jsonify(data_manager.current_data)

@app.route('/api/control', methods=['POST'])
def control():
    """API endpoint for control actions"""
    action = request.json.get('action')
    params = request.json.get('params', {})
    
    response = {"status": "success", "message": ""}
    
    if action == "emergency_stop":
        data_manager.current_data["optimization"]["emergency_stop"] = True
        
        # Stop all stations
        for station in data_manager.current_data["stations"]:
            data_manager.current_data["stations"][station]["status"] = "stopped"
        
        # Set throughput to 0
        data_manager.current_data["production"]["throughput"] = 0
        
        response["message"] = "Emergency stop activated"
        
    elif action == "resume":
        data_manager.current_data["optimization"]["emergency_stop"] = False
        
        # Resume stations (except those in fault)
        for station in data_manager.current_data["stations"]:
            if not data_manager.current_data["stations"][station]["fault"]:
                data_manager.current_data["stations"][station]["status"] = "running"
        
        # Restore throughput
        data_manager.current_data["production"]["throughput"] = (
            data_manager.current_data["production"]["baseline_throughput"]
        )
        
        response["message"] = "Production resumed"
        
    elif action == "change_mode":
        mode = params.get("mode")
        if mode in ["balanced", "throughput", "energy_saving", "quality"]:
            data_manager.current_data["optimization"]["mode"] = mode
            response["message"] = f"Optimization mode changed to {mode}"
            
    elif action == "inject_fault":
        station = params.get("station", "S1")
        fault_type = params.get("fault_type")
        
        if station in data_manager.current_data["stations"]:
            success = data_manager.simulate_fault_injection(station, fault_type)
            if success:
                response["message"] = f"{fault_type or 'Random'} fault injected to {station}"
            else:
                response["status"] = "error"
                response["message"] = f"{station} already in fault state"
        else:
            response["status"] = "error"
            response["message"] = f"Invalid station: {station}"
            
    elif action == "clear_fault":
        station = params.get("station", "S1")
        
        if station in data_manager.current_data["stations"]:
            data_manager.recover_from_fault(station)
            response["message"] = f"Fault cleared from {station}"
        else:
            response["status"] = "error"
            response["message"] = f"Invalid station: {station}"
    
    elif action == "inject_random_fault":
        success = data_manager.simulate_random_fault()
        if success:
            station = data_manager.current_data["simulation"]["fault_station"]
            fault_type = data_manager.current_data["simulation"]["fault_type"]
            response["message"] = f"Random {fault_type} fault injected to {station}"
        else:
            response["status"] = "warning"
            response["message"] = "All stations already have faults"
    
    elif action == "clear_all_faults":
        # Clear all active faults
        active_faults = list(data_manager.current_data["optimization"]["active_faults"])
        
        for station in active_faults:
            data_manager.recover_from_fault(station)
        
        response["message"] = f"Cleared {len(active_faults)} faults and restarted line"
    
    elif action == "restart_line":
        # Clear emergency stop and restart line
        data_manager.current_data["optimization"]["emergency_stop"] = False
        
        # Restart all stations
        for station in data_manager.current_data["stations"]:
            data_manager.current_data["stations"][station]["status"] = "running"
        
        # Restore throughput
        data_manager.current_data["production"]["throughput"] = (
            data_manager.current_data["production"]["baseline_throughput"]
        )
        
        response["message"] = "Production line restarted"
    
    # Emit update
    socketio.emit('control_update', response)
    socketio.emit('data_update', data_manager.current_data)
    
    return jsonify(response)

@app.route('/api/simulation/reset')
def reset_simulation():
    """Reset simulation to initial state"""
    # Reinitialize data manager
    global data_manager
    data_manager = DashboardDataManager()
    
    response = {"status": "success", "message": "Simulation reset to initial state"}
    socketio.emit('simulation_reset', response)
    socketio.emit('data_update', data_manager.current_data)
    
    return jsonify(response)

@socketio.on('connect')
def handle_connect():
    """Handle WebSocket connection"""
    print(f"Client connected: {request.sid}")
    emit('connected', {'message': 'Connected to Siemens Smart Factory Dashboard'})
    emit('data_update', data_manager.current_data)

# SocketIO event handlers
@socketio.on('fault_injected')
def handle_fault_injected(data):
    """Broadcast fault injection to all clients"""
    emit('fault_injected', data, broadcast=True, include_self=False)

@socketio.on('fault_recovered')
def handle_fault_recovered(data):
    """Broadcast fault recovery to all clients"""
    emit('fault_recovered', data, broadcast=True, include_self=False)

@socketio.on('buffer_warning')
def handle_buffer_warning(data):
    """Broadcast buffer warning to all clients"""
    emit('buffer_warning', data, broadcast=True, include_self=False)

@socketio.on('upstream_stopped')
def handle_upstream_stopped(data):
    """Broadcast upstream stop to all clients"""
    emit('upstream_stopped', data, broadcast=True, include_self=False)

@socketio.on('auto_line_stop')
def handle_auto_line_stop(data):
    """Broadcast auto line stop to all clients"""
    emit('auto_line_stop', data, broadcast=True, include_self=False)

if __name__ == '__main__':
    print("=" * 80)
    print("Siemens Smart Factory Optimization Dashboard")
    print("=" * 80)
    print("Dashboard URL: http://localhost:5000")
    print("\nFeatures:")
    print("  • Realistic fault injection simulation")
    print("  • Buffer management with overflow prevention")
    print("  • Cascading effects (upstream station stops)")
    print("  • Automatic line stoppage for persistent faults")
    print("\nAPI Endpoints:")
    print("  POST /api/control")
    print("    Actions: inject_fault, clear_fault, inject_random_fault")
    print("    clear_all_faults, restart_line, emergency_stop, resume")
    print("=" * 80)
    
    socketio.run(app, debug=True, port=5000)
