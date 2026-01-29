// add inside /static/js/
/**
 * Siemens Smart Factory Dashboard - JavaScript Logic
 * Real-time data visualization and control
 */

let currentData = {};
let throughputChart = null;
let energyChart = null;

// Update the entire dashboard with new data
function updateDashboard(data) {
    currentData = data;
    
    // Update KPIs
    updateKPIs(data);
    
    // Update station status
    updateStations(data.stations);
    
    // Update buffers
    updateBuffers(data.buffers);
    
    // Update system status
    updateSystemStatus(data);
    
    // Update event log
    updateEventLog(data.events);
    
    // Update last update timestamp
    document.getElementById('last-update').textContent = new Date().toLocaleTimeString();
}

// Update Key Performance Indicators
function updateKPIs(data) {
    // OEE
    const oee = data.kpis.oee || 0;
    document.getElementById('kpi-oee').textContent = oee.toFixed(1) + '%';
    document.getElementById('oee-progress').style.width = oee + '%';
    
    // Throughput
    const throughput = data.production.throughput || 0;
    document.getElementById('kpi-throughput').textContent = throughput.toFixed(1);
    document.getElementById('target-throughput').textContent = data.production.target_throughput || 10;
    
    // Energy
    const energyPerProduct = data.energy.energy_per_product || 0;
    document.getElementById('kpi-energy').textContent = energyPerProduct.toFixed(2);
    
    // Yield
    const yieldValue = data.quality.first_pass_yield || 0;
    document.getElementById('kpi-yield').textContent = yieldValue.toFixed(1) + '%';
    document.getElementById('yield-progress').style.width = yieldValue + '%';
    
    // Total products
    document.getElementById('total-products').textContent = 
        `Total: ${data.production.total_products || 0} products`;
}

// Update station status display
function updateStations(stations) {
    const container = document.getElementById('stations-container');
    if (!container) return;
    
    let html = '';
    
    for (const [stationId, station] of Object.entries(stations)) {
        const statusClass = `status-${station.status || 'idle'}`;
        const statusText = station.status ? station.status.charAt(0).toUpperCase() + station.status.slice(1) : 'Idle';
        const uptime = (station.uptime || 0).toFixed(1);
        const downtime = (station.downtime || 0).toFixed(1);
        const availability = station.uptime + station.downtime > 0 
            ? ((station.uptime / (station.uptime + station.downtime)) * 100).toFixed(1) 
            : '0.0';
        
        // Energy for this station
        const energy = currentData.energy.station_energy?.[stationId] || 0;
        
        html += `
            <div class="col-md-4 col-sm-6 mb-3">
                <div class="card station-card" onclick="stationDetail('${stationId}')">
                    <div class="card-body p-3">
                        <div class="d-flex justify-content-between align-items-center mb-2">
                            <h5 class="card-title mb-0">${stationId}</h5>
                            <span class="status-indicator ${statusClass}"></span>
                        </div>
                        <div class="mb-2">
                            <span class="badge bg-dark">${statusText}</span>
                            ${station.fault ? '<span class="badge bg-danger ms-1">Fault</span>' : ''}
                        </div>
                        <div class="row small">
                            <div class="col-6">
                                <div class="text-muted">Uptime</div>
                                <div>${uptime}s</div>
                            </div>
                            <div class="col-6">
                                <div class="text-muted">Availability</div>
                                <div>${availability}%</div>
                            </div>
                        </div>
                        <div class="mt-2">
                            <div class="text-muted">Energy</div>
                            <div>${energy.toFixed(2)} kWh</div>
                        </div>
                    </div>
                </div>
            </div>
        `;
    }
    
    container.innerHTML = html;
}

// Update buffer status display
function updateBuffers(buffers) {
    const container = document.getElementById('buffers-container');
    if (!container) return;
    
    let html = '';
    
    for (const [bufferId, level] of Object.entries(buffers)) {
        const percentage = Math.min((level / 3) * 100, 100); // Assuming max buffer of 3
        
        html += `
            <div class="mb-3">
                <div class="d-flex justify-content-between mb-1">
                    <span>${bufferId.replace('_', ' → ')}</span>
                    <span>${level}/3 (${percentage.toFixed(0)}%)</span>
                </div>
                <div class="buffer-bar">
                    <div class="buffer-fill" style="width: ${percentage}%"></div>
                </div>
            </div>
        `;
    }
    
    container.innerHTML = html;
}

// Update system status
function updateSystemStatus(data) {
    // System status indicator
    const systemStatus = data.optimization.emergency_stop ? 'fault' : 'running';
    document.getElementById('system-status').className = `status-indicator status-${systemStatus}`;
    
    // Batch ID
    document.getElementById('batch-id').textContent = `Batch: ${data.production.batch_id || 0}`;
    
    // Simulation time
    const simTime = data.production.simulation_time || 0;
    const hours = Math.floor(simTime / 3600);
    const minutes = Math.floor((simTime % 3600) / 60);
    const seconds = Math.floor(simTime % 60);
    document.getElementById('sim-time').textContent = 
        `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
    
    // Bottleneck
    document.getElementById('bottleneck').textContent = data.optimization.bottleneck || 'None';
    if (data.optimization.bottleneck) {
        document.getElementById('bottleneck').className = 'text-warning';
    } else {
        document.getElementById('bottleneck').className = '';
    }
    
    // Emergency status
    document.getElementById('emergency-status').textContent = 
        data.optimization.emergency_stop ? 'Active' : 'Inactive';
    document.getElementById('emergency-status').className = 
        data.optimization.emergency_stop ? 'text-danger' : 'text-success';
    
    // Current mode
    document.getElementById('current-mode').textContent = data.optimization.mode || 'balanced';
    document.getElementById('optimization-mode').value = data.optimization.mode || 'balanced';
    
    // Maintenance schedule
    updateMaintenanceSchedule(data.optimization.maintenance_schedule || {});
}

// Update maintenance schedule display
function updateMaintenanceSchedule(schedule) {
    const container = document.getElementById('maintenance-list');
    if (!container) return;
    
    if (Object.keys(schedule).length === 0) {
        container.innerHTML = '<div class="text-center text-muted small">No maintenance scheduled</div>';
        return;
    }
    
    let html = '';
    for (const [station, time] of Object.entries(schedule)) {
        const timeRemaining = time - (currentData.production.simulation_time || 0);
        if (timeRemaining > 0) {
            const minutes = Math.ceil(timeRemaining / 60);
            html += `
                <div class="d-flex justify-content-between small mb-1">
                    <span>${station}</span>
                    <span class="text-warning">in ${minutes} min</span>
                </div>
            `;
        }
    }
    
    container.innerHTML = html || '<div class="text-center text-muted small">No active maintenance</div>';
}

// Update event log
function updateEventLog(events) {
    const container = document.getElementById('event-log');
    if (!container) return;
    
    if (!events || events.length === 0) {
        container.innerHTML = '<div class="text-center text-muted py-4">No events yet</div>';
        return;
    }
    
    let html = '';
    events.forEach(event => {
        const time = new Date(event.timestamp).toLocaleTimeString();
        const eventClass = `event-${event.type || 'info'}`;
        
        html += `
            <div class="event-item ${eventClass}">
                <div class="small text-muted">${time}</div>
                <div>${event.message || 'No message'}</div>
            </div>
        `;
    });
    
    container.innerHTML = html;
}

// Load charts from server
async function loadCharts() {
    try {
        const response = await fetch('/api/plots');
        const plots = await response.json();
        
        // Throughput chart
        if (plots.throughput) {
            const throughputData = JSON.parse(plots.throughput);
            Plotly.newPlot('throughput-chart', throughputData.data, throughputData.layout);
        }
        
        // Energy chart
        if (plots.energy) {
            const energyData = JSON.parse(plots.energy);
            Plotly.newPlot('energy-chart', energyData.data, energyData.layout);
        }
        
    } catch (error) {
        console.error('Error loading charts:', error);
    }
}

// Control functions
function changeMode(mode) {
    fetch('/api/control', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            action: 'change_mode',
            params: { mode: mode }
        })
    });
}

function emergencyStop() {
    if (confirm('Are you sure you want to activate emergency stop? This will halt all production.')) {
        fetch('/api/control', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'emergency_stop' })
        });
    }
}

function resumeProduction() {
    fetch('/api/control', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'resume' })
    });
}

function injectFault() {
    const station = document.getElementById('fault-station').value;
    fetch('/api/control', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            action: 'inject_fault',
            params: { station: station }
        })
    });
}

function clearFault() {
    const station = document.getElementById('fault-station').value;
    fetch('/api/control', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            action: 'clear_fault',
            params: { station: station }
        })
    });
}

function stationDetail(stationId) {
    alert(`Station ${stationId} Details:\n\n` +
          `Status: ${currentData.stations[stationId]?.status || 'Unknown'}\n` +
          `Uptime: ${currentData.stations[stationId]?.uptime?.toFixed(1) || 0}s\n` +
          `Downtime: ${currentData.stations[stationId]?.downtime?.toFixed(1) || 0}s\n` +
          `Energy: ${currentData.energy.station_energy?.[stationId]?.toFixed(2) || 0} kWh`);
}

function clearEvents() {
    if (confirm('Clear all events from the log?')) {
        const container = document.getElementById('event-log');
        container.innerHTML = '<div class="text-center text-muted py-4">Event log cleared</div>';
    }
}

// Auto-refresh data every 2 seconds
setInterval(() => {
    if (socket.connected) {
        // Data is automatically pushed via WebSocket
    } else {
        // Fallback to polling if WebSocket disconnected
        fetch('/api/data')
            .then(response => response.json())
            .then(data => updateDashboard(data))
            .catch(error => console.error('Error polling data:', error));
    }
}, 2000);
