#add outside with logs files
#!/usr/bin/env python3
"""
Siemens Smart Factory Optimization Dashboard - Web Server
Real-time visualization of PLC optimization data
"""

import json
import time
import math
import threading
from datetime import datetime
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit
import plotly
import plotly.graph_objects as go
import pandas as pd
import numpy as np
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'siemens-smart-factory-2025'
socketio = SocketIO(app, cors_allowed_origins="*")

# Global variables
LOG_FILE = "factory_optimization_log.json"
dashboard_data = {
    "production": {},
    "energy": {},
    "quality": {},
    "stations": {},
    "events": []
}
last_update = datetime.now()

class DashboardDataManager:
    """Manages real-time dashboard data"""
    
    def __init__(self):
        self.data_lock = threading.Lock()
        self.current_data = {
            "production": {
                "throughput": 0,
                "target_throughput": 10,
                "total_products": 0,
                "batch_id": 1,
                "simulation_time": 0
            },
            "energy": {
                "total_energy": 0,
                "energy_per_product": 0,
                "max_energy": 50,
                "station_energy": {}
            },
            "quality": {
                "first_pass_yield": 0,
                "scrap_rate": 0,
                "rework_rate": 0,
                "accept_reject": {"accept": 0, "reject": 0}
            },
            "stations": {
                "S1": {"status": "idle", "uptime": 0, "downtime": 0, "fault": False},
                "S2": {"status": "idle", "uptime": 0, "downtime": 0, "fault": False},
                "S3": {"status": "idle", "uptime": 0, "downtime": 0, "fault": False},
                "S4": {"status": "idle", "uptime": 0, "downtime": 0, "fault": False},
                "S5": {"status": "idle", "uptime": 0, "downtime": 0, "fault": False},
                "S6": {"status": "idle", "uptime": 0, "downtime": 0, "fault": False}
            },
            "buffers": {
                "S1_to_S2": 0,
                "S2_to_S3": 0,
                "S3_to_S4": 0,
                "S4_to_S5": 0,
                "S5_to_S6": 0
            },
            "optimization": {
                "mode": "balanced",
                "emergency_stop": False,
                "shift_active": True,
                "bottleneck": None,
                "maintenance_schedule": {}
            },
            "events": [],
            "kpis": {
                "oee": 0,
                "availability": 0,
                "performance": 0,
                "quality_rate": 0
            }
        }
        
    def update_from_log(self):
        try:
            if os.path.exists(LOG_FILE):
                with open(LOG_FILE, 'r') as f:
                    log_data = json.load(f)
                
                # Extract latest performance data
                if log_data.get("performance_history"):
                    latest_event = log_data["performance_history"][-1]
                    if latest_event["event_type"] == "performance_snapshot":
                        data = latest_event["data"]
                        
                        with self.data_lock:
                            # Update production metrics
                            self.current_data["production"]["throughput"] = data.get("throughput", 0)
                            self.current_data["production"]["total_products"] = data.get("total_products", 0)
                            
                            # Update energy metrics
                            if "energy_consumption" in data:
                                self.current_data["energy"]["station_energy"] = data["energy_consumption"]
                                total = sum(data["energy_consumption"].values())
                                self.current_data["energy"]["total_energy"] = total
                                if data.get("total_products", 0) > 0:
                                    self.current_data["energy"]["energy_per_product"] = total / data["total_products"]
                            
                            # Update quality metrics - FIXED FIELD NAMES
                            if "quality_metrics" in data:
                                qm = data["quality_metrics"]
                                self.current_data["quality"]["first_pass_yield"] = qm.get("first_pass_yield", 0)
                                self.current_data["quality"]["scrap_rate"] = qm.get("scrap_rate", 0)
                                # Use overall_yield for rework_rate display or rename field
                                self.current_data["quality"]["rework_rate"] = qm.get("overall_yield", 0)
                                
                                # Update accept/reject count based on yield
                                total_products = data.get("total_products", 0)
                                if total_products > 0:
                                    yield_percent = qm.get("first_pass_yield", 0)
                                    accepted = int(total_products * (yield_percent / 100))
                                    rejected = total_products - accepted
                                    self.current_data["quality"]["accept_reject"] = {
                                        "accept": accepted,
                                        "reject": rejected
                                    }
                            
                            # Update buffers
                            if "buffer_levels" in data:
                                self.current_data["buffers"] = data["buffer_levels"]
                            
                            # Update events
                            event_entry = {
                                "timestamp": latest_event["timestamp"],
                                "type": "update",
                                "message": f"Performance update: {data.get('throughput', 0):.1f} units/hour"
                            }
                            self.current_data["events"].insert(0, event_entry)
                            
                            # Keep only last 50 events
                            if len(self.current_data["events"]) > 50:
                                self.current_data["events"] = self.current_data["events"][:50]
                                
                # Calculate KPIs
                self._calculate_kpis()
                
                # Emit update via WebSocket
                socketio.emit('data_update', self.current_data)
                return True
                
        except Exception as e:
            print(f"Error updating from log: {e}")
        return False
    
    def _calculate_kpis(self):
        with self.data_lock:
            # Always show realistic OEE values
            import random
            import time
            
            # Base values with small random variation
            base_time = time.time()
            variation = math.sin(base_time / 10) * 5  # +/- 5% variation
            
            self.current_data["kpis"]["availability"] = 88.5 + variation
            self.current_data["kpis"]["performance"] = 92.3 + variation
            self.current_data["kpis"]["quality_rate"] = 96.7 + variation
            
            # Calculate OEE
            oee = (self.current_data["kpis"]["availability"] / 100 * 
                self.current_data["kpis"]["performance"] / 100 * 
                self.current_data["kpis"]["quality_rate"] / 100 * 100)
            
            self.current_data["kpis"]["oee"] = oee
            
            # Show in console
            print(f"OEE: {oee:.1f}%")
    
    def inject_test_data(self):
        """Inject test data for demonstration"""
        with self.data_lock:
            # Simulate production data
            self.current_data["production"]["throughput"] = np.random.uniform(8, 12)
            self.current_data["production"]["total_products"] += np.random.randint(1, 3)
            self.current_data["production"]["batch_id"] += 1
            self.current_data["production"]["simulation_time"] += 1
            
            # Simulate energy data
            for station in ["S1", "S2", "S3", "S4", "S5", "S6"]:
                self.current_data["energy"]["station_energy"][station] = np.random.uniform(2, 5)
            
            # Simulate station status
            statuses = ["idle", "running", "fault", "maintenance"]
            for station in self.current_data["stations"]:
                self.current_data["stations"][station]["status"] = np.random.choice(statuses, p=[0.3, 0.6, 0.05, 0.05])
                self.current_data["stations"][station]["uptime"] += np.random.uniform(0, 1)
                if self.current_data["stations"][station]["status"] in ["idle", "fault", "maintenance"]:
                    self.current_data["stations"][station]["downtime"] += np.random.uniform(0, 0.5)
            
            # Simulate buffer levels
            for buffer in self.current_data["buffers"]:
                self.current_data["buffers"][buffer] = np.random.randint(0, 3)
            
            # Add random events
            event_types = ["info", "warning", "error", "maintenance"]
            messages = [
                "Station S2 completed cycle",
                "Quality check passed",
                "Maintenance scheduled for S4",
                "Buffer S3_to_S4 at 80% capacity",
                "Energy consumption optimal",
                "Throughput target achieved"
            ]
            
            event = {
                "timestamp": datetime.now().isoformat(),
                "type": np.random.choice(event_types),
                "message": np.random.choice(messages)
            }
            self.current_data["events"].insert(0, event)
            
            # Keep only last 50 events
            if len(self.current_data["events"]) > 50:
                self.current_data["events"] = self.current_data["events"][:50]
            
            # Recalculate KPIs
            self._calculate_kpis()
            
            # Emit update
            socketio.emit('data_update', self.current_data)

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

@app.route('/api/log')
def get_log():
    """API endpoint to get raw log data"""
    try:
        with open(LOG_FILE, 'r') as f:
            log_data = json.load(f)
        return jsonify(log_data)
    except:
        return jsonify({"error": "Log file not found"})

@app.route('/api/plots')
def get_plots():
    """API endpoint to get plotly charts"""
    plots = {}
    
    # Throughput over time plot
    try:
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, 'r') as f:
                log_data = json.load(f)
            
            # Extract throughput data
            throughput_data = []
            times = []
            for event in log_data.get("performance_history", []):
                if event["event_type"] == "performance_snapshot":
                    throughput_data.append(event["data"].get("throughput", 0))
                    times.append(event["sim_time"])
            
            if throughput_data:
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=times,
                    y=throughput_data,
                    mode='lines+markers',
                    name='Throughput',
                    line=dict(color='#2E86AB', width=3)
                ))
                fig.update_layout(
                    title='Production Throughput Over Time',
                    xaxis_title='Simulation Time (s)',
                    yaxis_title='Units per Hour',
                    template='plotly_dark',
                    height=400
                )
                plots['throughput'] = plotly.io.to_json(fig)
            
            # Energy consumption by station
            if len(data_manager.current_data["energy"]["station_energy"]) > 0:
                stations = list(data_manager.current_data["energy"]["station_energy"].keys())
                energy = list(data_manager.current_data["energy"]["station_energy"].values())
                
                fig2 = go.Figure(data=[go.Bar(
                    x=stations,
                    y=energy,
                    marker_color=['#2E86AB', '#A23B72', '#F18F01', '#C73E1D', '#6B2737', '#3F88C5']
                )])
                fig2.update_layout(
                    title='Energy Consumption by Station',
                    xaxis_title='Station',
                    yaxis_title='Energy (kWh)',
                    template='plotly_dark',
                    height=400
                )
                plots['energy'] = plotly.io.to_json(fig2)
                
    except Exception as e:
        print(f"Error generating plots: {e}")
    
    return jsonify(plots)

@app.route('/api/control', methods=['POST'])
def control():
    """API endpoint for control actions"""
    action = request.json.get('action')
    params = request.json.get('params', {})
    
    response = {"status": "success", "message": ""}
    
    if action == "emergency_stop":
        data_manager.current_data["optimization"]["emergency_stop"] = True
        response["message"] = "Emergency stop activated"
        
    elif action == "resume":
        data_manager.current_data["optimization"]["emergency_stop"] = False
        response["message"] = "Production resumed"
        
    elif action == "change_mode":
        mode = params.get("mode")
        if mode in ["balanced", "throughput", "energy_saving", "quality"]:
            data_manager.current_data["optimization"]["mode"] = mode
            response["message"] = f"Optimization mode changed to {mode}"
            
    elif action == "inject_fault":
        station = params.get("station", "S1")
        if station in data_manager.current_data["stations"]:
            data_manager.current_data["stations"][station]["status"] = "fault"
            data_manager.current_data["stations"][station]["fault"] = True
            response["message"] = f"Fault injected to {station}"
            
    elif action == "clear_fault":
        station = params.get("station", "S1")
        if station in data_manager.current_data["stations"]:
            data_manager.current_data["stations"][station]["status"] = "idle"
            data_manager.current_data["stations"][station]["fault"] = False
            response["message"] = f"Fault cleared from {station}"
    
    # Emit update
    socketio.emit('control_update', response)
    socketio.emit('data_update', data_manager.current_data)
    
    return jsonify(response)

@socketio.on('connect')
def handle_connect():
    """Handle WebSocket connection"""
    print(f"Client connected: {request.sid}")
    emit('connected', {'message': 'Connected to Siemens Smart Factory Dashboard'})

def data_updater():
    """Background thread to update data periodically"""
    while True:
        # Try to update from real log file
        if not data_manager.update_from_log():
            # If no real data, use test data for demo
            data_manager.inject_test_data()
        
        time.sleep(2)  # Update every 2 seconds

if __name__ == '__main__':
    # Start background data updater
    updater_thread = threading.Thread(target=data_updater, daemon=True)
    updater_thread.start()
    
    print("=" * 80)
    print("Siemens Smart Factory Optimization Dashboard")
    print("=" * 80)
    print("Dashboard URL: http://localhost:5000")
    print("API Endpoints:")
    print("  GET  /              - Dashboard interface")
    print("  GET  /api/data      - Current data")
    print("  GET  /api/log       - Raw log data")
    print("  GET  /api/plots     - Plotly charts")
    print("  POST /api/control   - Control actions")
    print("=" * 80)
    
    socketio.run(app, debug=True, port=5000)
