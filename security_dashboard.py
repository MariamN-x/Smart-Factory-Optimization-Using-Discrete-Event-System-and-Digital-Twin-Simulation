# #!/usr/bin/env python3
# """
# VSI Security Test Dashboard - Single File Version
# All-in-one dashboard with embedded HTML template.
# """

# import os
# import sys
# import json
# import time
# import threading
# import socket
# import hashlib
# import random
# import re
# import glob
# import shutil
# from datetime import datetime
# from typing import Dict, List, Optional, Callable, Any
# from dataclasses import dataclass, field

# from flask import Flask, render_template_string, request, jsonify
# from flask_socketio import SocketIO, emit


# # ==================== CONFIG ====================
# PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

# LOGS_ROOT = f"{PROJECT_ROOT}/logs"
# SECURITY_RUNS_DIR = f"{LOGS_ROOT}/security_runs"
# DASHBOARD_EVENTS_LOG = f"{LOGS_ROOT}/security_dashboard_events.jsonl"
# BACKUP_DIR = f"{PROJECT_ROOT}/backups"

# os.makedirs(SECURITY_RUNS_DIR, exist_ok=True)
# os.makedirs(BACKUP_DIR, exist_ok=True)

# # Flask app
# app = Flask(__name__)
# app.config["SECRET_KEY"] = "vsi-security-dashboard-secret-key"
# socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")


# def _now_iso():
#     return datetime.now().isoformat()


# def append_jsonl(path: str, obj: dict):
#     try:
#         os.makedirs(os.path.dirname(path), exist_ok=True)
#         with open(path, "a", encoding="utf-8") as f:
#             f.write(json.dumps(obj, ensure_ascii=False) + "\n")
#     except Exception:
#         pass


# # ==================== HTML TEMPLATE ====================
# HTML_TEMPLATE = r"""
# <!DOCTYPE html>
# <html lang="en">
# <head>
#   <meta charset="UTF-8" />
#   <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
#   <title>VSI Security Test Dashboard</title>
#   <script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
#   <style>
#     * { margin:0; padding:0; box-sizing:border-box; }
#     body {
#       font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
#       background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
#       color:#eaeaea;
#       min-height:100vh;
#     }
#     .header {
#       background: rgba(0,0,0,0.3);
#       padding: 20px;
#       border-bottom: 2px solid #e94560;
#       display:flex;
#       justify-content:space-between;
#       align-items:center;
#     }
#     .header h1 { color:#e94560; font-size:24px; display:flex; align-items:center; gap:10px; }

#     .main-container {
#       display:grid;
#       grid-template-columns: 300px 1fr 350px;
#       gap:20px;
#       padding:20px;
#       height: calc(100vh - 100px);
#     }
#     .panel {
#       background: rgba(255,255,255,0.05);
#       border-radius:12px;
#       padding:20px;
#       border: 1px solid rgba(255,255,255,0.1);
#       overflow-y:auto;
#     }
#     .panel h2 { color:#e94560; margin-bottom:15px; font-size:18px; text-transform:uppercase; }

#     /* Scenario List */
#     .scenario-list { display:flex; flex-direction:column; gap:10px; }
#     .scenario-card {
#       background: rgba(255,255,255,0.05);
#       border: 1px solid rgba(255,255,255,0.1);
#       border-radius:8px;
#       padding:15px;
#       cursor:pointer;
#       transition: all 0.3s;
#       position:relative;
#     }
#     .scenario-card:hover { background: rgba(233,69,96,0.1); border-color:#e94560; }
#     .scenario-card.active { background: rgba(233,69,96,0.2); border-color:#e94560; }
#     .scenario-card .name { font-weight:bold; margin-bottom:5px; color:#fff; }
#     .scenario-card .category { font-size:12px; color:#888; text-transform:uppercase; }
#     .risk { position:absolute; top:10px; right:10px; width:10px; height:10px; border-radius:50%; }
#     .risk.low { background:#2ecc71; } .risk.medium { background:#f39c12; } .risk.high { background:#e74c3c; }

#     /* Target Discovery */
#     .scan-section {
#       background: rgba(52,152,219,0.1);
#       border: 1px solid #3498db;
#       border-radius:8px;
#       padding:15px;
#       margin-bottom:20px;
#     }
#     .scan-section h3 { color:#3498db; margin-bottom:10px; }
#     .target-grid { display:grid; grid-template-columns: repeat(auto-fill, minmax(80px,1fr)); gap:10px; margin-top:10px; }
#     .target-item {
#       background: rgba(255,255,255,0.05);
#       border: 2px solid transparent;
#       border-radius:8px;
#       padding:10px;
#       text-align:center;
#       cursor:pointer;
#       transition: all 0.3s;
#     }
#     .target-item:hover { background: rgba(255,255,255,0.1); }
#     .target-item.selected { border-color:#e94560; background: rgba(233,69,96,0.2); }
#     .target-item.online { border-left: 4px solid #2ecc71; }
#     .target-item.offline { border-left: 4px solid #e74c3c; opacity:0.6; }
#     .target-item .target-id { font-weight:bold; font-size:16px; color:#fff; }
#     .target-item .target-status { font-size:10px; text-transform:uppercase; margin-top:5px; }
#     .target-item.online .target-status { color:#2ecc71; }
#     .target-item.offline .target-status { color:#e74c3c; }

#     .all-targets-btn {
#       grid-column: 1 / -1;
#       background: rgba(155,89,182,0.3);
#       border: 2px dashed #9b59b6;
#       color:#9b59b6;
#       padding:15px;
#       text-align:center;
#       cursor:pointer;
#       border-radius:8px;
#       margin-top:10px;
#     }
#     .all-targets-btn.selected { background: rgba(155,89,182,0.7); color:#fff; border-style:solid; }

#     .scan-btn {
#       background:#3498db;
#       color:white;
#       border:none;
#       padding:8px 16px;
#       border-radius:4px;
#       cursor:pointer;
#       font-size:12px;
#     }
#     .scan-btn:disabled { opacity:0.5; cursor:not-allowed; }
#     .scan-status { font-size:12px; color:#888; margin-top:5px; }

#     /* Controls */
#     .arm-section {
#       background: rgba(231,76,60,0.1);
#       border: 2px solid #e74c3c;
#       border-radius:8px;
#       padding:20px;
#       margin-bottom:20px;
#     }
#     .arm-checkbox { display:flex; align-items:center; gap:10px; }
#     .arm-checkbox input { width:20px; height:20px; }
#     .arm-checkbox label { font-weight:bold; color:#e74c3c; }
#     .confirmation-input {
#       margin-top:10px;
#       width:100%;
#       padding:10px;
#       background: rgba(0,0,0,0.3);
#       border: 1px solid #e74c3c;
#       border-radius:4px;
#       color:#fff;
#     }

#     .button-row { display:flex; gap:10px; margin-bottom:20px; flex-wrap:wrap; }
#     button {
#       padding:12px 24px;
#       border:none;
#       border-radius:6px;
#       font-weight:bold;
#       cursor:pointer;
#       transition: all 0.3s;
#       text-transform:uppercase;
#       font-size:12px;
#     }
#     button:disabled { opacity:0.5; cursor:not-allowed; }
#     .btn-run { background:#e94560; color:white; }
#     .btn-run:hover:not(:disabled) { background:#ff6b6b; }
#     .btn-stop { background:#f39c12; color:white; }
#     .btn-undo { background:#3498db; color:white; }
#     .btn-mitigate { background:#2ecc71; color:black; }
#     .btn-mitigate:hover:not(:disabled) { background:#27ae60; }
#     .btn-panic { background:#e74c3c; color:white; animation:pulse 2s infinite; }
#     @keyframes pulse {
#       0%,100% { box-shadow: 0 0 0 0 rgba(231,76,60,0.7); }
#       50% { box-shadow: 0 0 0 10px rgba(231,76,60,0); }
#     }

#     /* Ransomware specific buttons */
#     .ransomware-controls {
#       background: rgba(241, 196, 15, 0.1);
#       border: 1px solid #f1c40f;
#       border-radius: 8px;
#       padding: 15px;
#       margin-bottom: 20px;
#       display: none;
#     }
#     .ransomware-controls.active { display: block; }
#     .ransomware-controls h4 { color: #f1c40f; margin-bottom: 10px; }
#     .btn-backup { background: #f1c40f; color: black; }
#     .btn-backup:hover:not(:disabled) { background: #f39c12; }
#     .btn-restore { background: #9b59b6; color: white; }
#     .btn-restore:hover:not(:disabled) { background: #8e44ad; }

#     .param-editor { background: rgba(0,0,0,0.2); border-radius:8px; padding:15px; }
#     .param-field { margin-bottom:15px; }
#     .param-field label { display:block; margin-bottom:5px; color:#aaa; font-size:12px; text-transform:uppercase; }
#     .param-field input, .param-field select {
#       width:100%;
#       padding:10px;
#       background: rgba(255,255,255,0.1);
#       border: 1px solid rgba(255,255,255,0.2);
#       border-radius:4px;
#       color:#fff;
#     }

#     .description-box {
#       background: rgba(255,255,255,0.05);
#       border-radius:8px;
#       padding:15px;
#       margin-bottom:20px;
#       font-size:14px;
#       line-height:1.6;
#       color:#bbb;
#       max-height:200px;
#       overflow-y:auto;
#     }

#     /* Execution Log - Enhanced */
#     .execution-log-panel {
#       background: rgba(0,0,0,0.3);
#       border-radius:8px;
#       padding:15px;
#       font-family: 'Courier New', monospace;
#       font-size:12px;
#       max-height:300px;
#       overflow-y:auto;
#       color:#2ecc71;
#       border: 1px solid rgba(46, 204, 113, 0.3);
#     }
#     .execution-log-panel .timestamp { color: #888; }
#     .execution-log-panel .error { color: #e74c3c; }
#     .execution-log-panel .warning { color: #f39c12; }
#     .execution-log-panel .success { color: #2ecc71; }
#     .execution-log-panel .info { color: #3498db; }
#     .execution-log-panel .mitigation { color: #9b59b6; }

#     /* Timeline */
#     .timeline { display:flex; flex-direction:column; gap:10px; max-height:400px; overflow-y:auto; }
#     .timeline-item {
#       background: rgba(255,255,255,0.05);
#       border-left: 3px solid #e94560;
#       padding:10px;
#       border-radius: 0 4px 4px 0;
#       font-size:13px;
#     }
#     .timeline-item .timestamp { color:#888; font-size:11px; }
#     .timeline-item .type { color:#e94560; font-weight:bold; font-size:10px; text-transform:uppercase; }
#     .timeline-item .message { color:#ddd; margin-top:5px; }

#     .state-badge {
#       display:inline-block;
#       padding:5px 15px;
#       border-radius:20px;
#       font-size:12px;
#       font-weight:bold;
#       text-transform:uppercase;
#     }
#     .state-idle { background: rgba(149,165,166,0.3); color:#95a5a6; }
#     .state-running { background: rgba(46,204,113,0.3); color:#2ecc71; animation:blink 1s infinite; }
#     .state-stopping { background: rgba(243,156,18,0.3); color:#f39c12; }
#     .state-mitigating { background: rgba(46, 204, 113, 0.3); color:#2ecc71; animation:blink 1s infinite; }
#     .state-undoing { background: rgba(52,152,219,0.3); color:#3498db; }
#     .state-error { background: rgba(231,76,60,0.3); color:#e74c3c; }
#     .state-done { background: rgba(155,89,182,0.3); color:#9b59b6; }
#     @keyframes blink { 0%,100% { opacity:1; } 50% { opacity:0.5; } }

#     .bottom-section {
#       grid-column: 1 / -1;
#       display:grid;
#       grid-template-columns: 1fr 1fr;
#       gap:20px;
#     }
#     .results-panel {
#       background: rgba(255,255,255,0.05);
#       border-radius:12px;
#       padding:20px;
#       border: 1px solid rgba(255,255,255,0.1);
#     }
#     .log-output {
#       background: rgba(0,0,0,0.3);
#       border-radius:8px;
#       padding:15px;
#       font-family: 'Courier New', monospace;
#       font-size:12px;
#       height:150px;
#       overflow-y:auto;
#       color:#2ecc71;
#     }
#     .hidden { display:none; }
#   </style>
# </head>
# <body>
#   <div class="header">
#     <h1><span>🛡️</span> VSI Security Test Dashboard</h1>
#     <div class="status-indicator" id="connection-status">
#       <span style="color:#e74c3c">●</span> <span id="connection-text">Disconnected</span>
#     </div>
#   </div>

#   <div class="main-container">
#     <!-- Left: Scenarios -->
#     <div class="panel">
#       <h2>Scenarios</h2>
#       <div class="scenario-list" id="scenario-list"><div>Loading...</div></div>
#     </div>

#     <!-- Middle: Controls -->
#     <div class="panel">
#       <h2>Control Center</h2>

#       <div class="scan-section">
#         <h3>🔍 Target Discovery (TCP Port Scan)</h3>
#         <div class="scan-status" id="scan-status">Click scan to probe configured host/ports</div>
#         <button class="scan-btn" id="btn-scan" onclick="scanTargets()">Scan Ports</button>
#         <div class="target-grid" id="target-grid"></div>
#         <div class="all-targets-btn" id="all-targets-btn" onclick="selectAllTargets()">ALL TARGETS</div>
#       </div>

#       <div class="arm-section">
#         <div class="arm-checkbox">
#           <input type="checkbox" id="arm-checkbox" onchange="toggleArm()">
#           <label for="arm-checkbox">ARM SYSTEM</label>
#         </div>
#         <input type="text" class="confirmation-input" id="confirm-text"
#                placeholder="Type 'RUN EXERCISE' to confirm" disabled oninput="updateUIState()">
#       </div>

#       <div class="button-row">
#         <button class="btn-run" id="btn-run" disabled onclick="runScenario()">▶ Run Attack</button>
#         <button class="btn-stop" id="btn-stop" disabled onclick="stopScenario()">⏹ Stop</button>
#         <button class="btn-mitigate" id="btn-mitigate" disabled onclick="applyMitigation()">🛡️ Apply Mitigation</button>
#         <button class="btn-undo" id="btn-undo" disabled onclick="undoScenario()">↩ Undo</button>
#         <button class="btn-panic" onclick="panicStop()">🚨 PANIC</button>
#       </div>

#       <!-- Ransomware-specific controls -->
#       <div class="ransomware-controls" id="ransomware-controls">
#         <h4>🗄️ Ransomware Backup Controls</h4>
#         <div class="button-row">
#           <button class="btn-backup" id="btn-backup" onclick="backupSystem()">💾 Backup System</button>
#           <button class="btn-restore" id="btn-restore" onclick="restoreBackup()">📥 Restore Backup</button>
#         </div>
#         <div id="backup-status" style="font-size: 12px; color: #888; margin-top: 5px;"></div>
#       </div>

#       <div class="description-box hidden" id="scenario-description">Select a scenario...</div>

#       <div class="param-editor" id="param-editor">
#         <p style="color:#666;">Select scenario and target...</p>
#       </div>

#       <!-- Execution Log Window -->
#       <div class="results-panel" style="margin-top: 20px;">
#         <h2>⚡ Execution Log</h2>
#         <div class="execution-log-panel" id="execution-log">
#           <div class="info">Waiting for scenario execution...</div>
#         </div>
#       </div>
#     </div>

#     <!-- Right: Timeline -->
#     <div class="panel">
#       <h2>Live Timeline</h2>
#       <div class="timeline" id="timeline">
#         <div class="timeline-item">
#           <div class="timestamp">--:--:--</div>
#           <div class="type">INFO</div>
#           <div class="message">Dashboard ready</div>
#         </div>
#       </div>
#       <div style="margin-top:20px;">
#         <span class="state-badge state-idle" id="state-badge">IDLE</span>
#       </div>
#     </div>

#     <!-- Bottom -->
#     <div class="bottom-section">
#       <div class="results-panel">
#         <h2>System Log</h2>
#         <div class="log-output" id="log-output">> Ready...</div>
#       </div>
#       <div class="results-panel">
#         <h2>Metrics</h2>
#         <div id="metrics-list" style="color:#888;">No active scenario</div>
#       </div>
#     </div>
#   </div>

# <script>
#   let socket = io();
#   let scenarios = {};
#   let selectedScenario = null;
#   let selectedTarget = null;
#   let isArmed = false;
#   let currentState = 'idle';
#   let metricsData = {};
#   let executionLogBuffer = [];

#   socket.on('connect', () => {
#     document.getElementById('connection-status').innerHTML =
#       '<span style="color:#2ecc71">●</span> Connected';
#     addTimeline('SYSTEM', 'Connected');
#     addExecutionLog('Connected to VSI Security Dashboard', 'info');
#   });

#   socket.on('scenarios_list', (data) => {
#     scenarios = data.scenarios || {};
#     renderScenarios();
#   });

#   socket.on('targets_update', (data) => {
#     updateTargets(data.targets || {});
#   });

#   socket.on('scan_complete', (data) => {
#     document.getElementById('scan-status').textContent =
#       `Host ${data.host || ''}: Found ${data.online_count}/${data.total_count} online`;
#     document.getElementById('btn-scan').disabled = false;
#     updateTargets(data.targets || {});
#     addTimeline('SCAN', `Found ${data.online_count} online`);
#     addExecutionLog(`Port scan complete: ${data.online_count}/${data.total_count} targets online`, 'success');
#   });

#   socket.on('scenario_started', (data) => {
#     currentState = 'running';
#     updateUIState();
#     addTimeline('START', `${data.scenario_id} started`);
#     addExecutionLog(`=== ATTACK STARTED: ${data.scenario_id} ===`, 'error');
#     addExecutionLog(`Target: ${selectedTarget}`, 'warning');
#     clearExecutionLog();
#   });

#   socket.on('scenario_event', (data) => {
#     addTimeline(data.event.type, data.event.message);
#     // Add to execution log with appropriate styling
#     const eventType = data.event.type.toLowerCase();
#     let logClass = 'info';
#     if (eventType.includes('error') || eventType.includes('tamper') || eventType.includes('encrypt')) {
#       logClass = 'error';
#     } else if (eventType.includes('warn')) {
#       logClass = 'warning';
#     } else if (eventType.includes('complete') || eventType.includes('success')) {
#       logClass = 'success';
#     }
#     addExecutionLog(`[${data.event.type}] ${data.event.message}`, logClass);
#     if (data.event.data) {
#       addExecutionLog(`  Data: ${JSON.stringify(data.event.data, null, 2)}`, 'info');
#     }
#   });

#   socket.on('scenario_metric', (data) => {
#     metricsData[data.metric_name] = data.value;
#     updateMetrics();
#     addExecutionLog(`[METRIC] ${data.metric_name} = ${JSON.stringify(data.value)}`, 'info');
#   });

#   socket.on('scenario_complete', (data) => {
#     currentState = 'done';
#     updateUIState();
#     addTimeline('COMPLETE', 'Scenario finished');
#     addExecutionLog(`=== ATTACK COMPLETE ===`, 'success');
#     addExecutionLog(`Artifacts: ${data.artifacts}`, 'info');
#   });

#   socket.on('scenario_error', (data) => {
#     currentState = 'error';
#     updateUIState();
#     addTimeline('ERROR', data.error || 'Unknown error');
#     addExecutionLog(`ERROR: ${data.error}`, 'error');
#   });

#   // Mitigation events
#   socket.on('mitigation_started', (data) => {
#     currentState = 'mitigating';
#     updateUIState();
#     addTimeline('MITIGATION', `${data.scenario_id} mitigation started`);
#     addExecutionLog(`=== MITIGATION STARTED: ${data.scenario_id} ===`, 'mitigation');
#   });

#   socket.on('mitigation_complete', (data) => {
#     currentState = 'done';
#     updateUIState();
#     addTimeline('MITIGATION', 'Mitigation complete');
#     addExecutionLog(`=== MITIGATION COMPLETE ===`, 'success');
#     addExecutionLog(`Result: ${data.result}`, 'info');
#   });

#   socket.on('mitigation_error', (data) => {
#     currentState = 'error';
#     updateUIState();
#     addTimeline('MITIGATION-ERROR', data.error);
#     addExecutionLog(`MITIGATION ERROR: ${data.error}`, 'error');
#   });

#   // Undo events
#   socket.on('undo_started', (data) => {
#     currentState = 'undoing';
#     updateUIState();
#     addTimeline('UNDO', `${data.scenario_id} undo started`);
#     addExecutionLog(`=== UNDO STARTED ===`, 'mitigation');
#   });

#   socket.on('undo_complete', (data) => {
#     currentState = 'done';
#     updateUIState();
#     addTimeline('UNDO', `${data.scenario_id} undo complete`);
#     addExecutionLog(`=== UNDO COMPLETE ===`, 'success');
#   });

#   socket.on('undo_error', (data) => {
#     currentState = 'error';
#     updateUIState();
#     addTimeline('UNDO-ERROR', data.error);
#     addExecutionLog(`UNDO ERROR: ${data.error}`, 'error');
#   });

#   // Backup/Restore events
#   socket.on('backup_started', (data) => {
#     addTimeline('BACKUP', 'System backup started');
#     addExecutionLog(`=== BACKUP STARTED ===`, 'mitigation');
#     document.getElementById('btn-backup').disabled = true;
#   });

#   socket.on('backup_complete', (data) => {
#     addTimeline('BACKUP', `Backup complete: ${data.backup_path}`);
#     addExecutionLog(`=== BACKUP COMPLETE ===`, 'success');
#     addExecutionLog(`Backup location: ${data.backup_path}`, 'info');
#     document.getElementById('btn-backup').disabled = false;
#     document.getElementById('backup-status').textContent = `Last backup: ${new Date().toLocaleString()}`;
#   });

#   socket.on('backup_error', (data) => {
#     addTimeline('BACKUP-ERROR', data.error);
#     addExecutionLog(`BACKUP ERROR: ${data.error}`, 'error');
#     document.getElementById('btn-backup').disabled = false;
#   });

#   socket.on('restore_started', (data) => {
#     addTimeline('RESTORE', 'System restore started');
#     addExecutionLog(`=== RESTORE STARTED ===`, 'mitigation');
#     document.getElementById('btn-restore').disabled = true;
#   });

#   socket.on('restore_complete', (data) => {
#     addTimeline('RESTORE', 'System restore complete');
#     addExecutionLog(`=== RESTORE COMPLETE ===`, 'success');
#     addExecutionLog(`Restored from: ${data.backup_path}`, 'info');
#     document.getElementById('btn-restore').disabled = false;
#   });

#   socket.on('restore_error', (data) => {
#     addTimeline('RESTORE-ERROR', data.error);
#     addExecutionLog(`RESTORE ERROR: ${data.error}`, 'error');
#     document.getElementById('btn-restore').disabled = false;
#   });

#   socket.on('panic_ack', (data) => {
#     addTimeline('PANIC', `Stopped: ${(data.stopped || []).join(', ')}`);
#     addExecutionLog('🚨 PANIC STOP EXECUTED 🚨', 'error');
#   });

#   function renderScenarios() {
#     const container = document.getElementById('scenario-list');
#     container.innerHTML = '';
#     const entries = Object.entries(scenarios);
#     if (entries.length === 0) {
#       container.innerHTML = '<div style="color:#888;">No scenarios loaded</div>';
#       return;
#     }
#     entries.forEach(([id, s]) => {
#       const card = document.createElement('div');
#       card.className = 'scenario-card';
#       card.innerHTML = `
#         <div class="risk ${s.risk_level || 'low'}"></div>
#         <div class="name">${s.name}</div>
#         <div class="category">${s.category}</div>
#       `;
#       card.onclick = () => selectScenario(id, card);
#       container.appendChild(card);
#     });
#   }

#   function selectScenario(id, cardElement) {
#     document.querySelectorAll('.scenario-card').forEach(c => c.classList.remove('active'));
#     cardElement.classList.add('active');
#     selectedScenario = id;
#     const s = scenarios[id];

#     document.getElementById('scenario-description').classList.remove('hidden');
#     document.getElementById('scenario-description').innerHTML =
#       `<strong>${s.name}</strong><br>${s.description}<br><br><em>Mitigation:</em> ${s.mitigation_info || ''}`;

#     // Show ransomware controls if applicable
#     const ransomControls = document.getElementById('ransomware-controls');
#     if (id === 'ransomware_exercise') {
#       ransomControls.classList.add('active');
#     } else {
#       ransomControls.classList.remove('active');
#     }

#     renderParams(s);
#     updateUIState();
#     addTimeline('SELECT', `Scenario: ${s.name}`);
#     addExecutionLog(`Selected scenario: ${s.name}`, 'info');
#   }

#   function renderParams(scenario) {
#     const container = document.getElementById('param-editor');
#     container.innerHTML = '';
#     const params = scenario.default_params || {};
#     const keys = Object.keys(params);
#     if (keys.length === 0) {
#       container.innerHTML = '<p style="color:#666;">No parameters</p>';
#       return;
#     }
#     keys.forEach((key) => {
#       if (key === 'target') return;
#       const val = params[key];
#       const div = document.createElement('div');
#       div.className = 'param-field';
#       div.innerHTML =
#         `<label>${key}</label>
#          <input type="${typeof val === 'number' ? 'number' : 'text'}"
#                 id="param-${key}" value="${val}" data-param="${key}">`;
#       container.appendChild(div);
#     });
#   }

#   function updateTargets(targets) {
#     const grid = document.getElementById('target-grid');
#     grid.innerHTML = '';
#     ['PLC','S1','S2','S3','S4','S5','S6'].forEach(id => {
#       const t = targets[id] || { status: 'offline', type: id === 'PLC' ? 'plc' : 'station' };
#       const div = document.createElement('div');
#       div.className = `target-item ${t.status}`;
#       div.dataset.target = id;
#       div.innerHTML = `<div class="target-id">${id}</div><div class="target-status">${t.status}</div>`;
#       div.onclick = () => selectTarget(id, div);
#       grid.appendChild(div);
#     });
#   }

#   function selectTarget(id, el) {
#     document.querySelectorAll('.target-item').forEach(e => e.classList.remove('selected'));
#     document.getElementById('all-targets-btn').classList.remove('selected');
#     el.classList.add('selected');
#     selectedTarget = id;
#     addTimeline('TARGET', `Selected: ${id}`);
#     addExecutionLog(`Selected target: ${id}`, 'info');
#     updateUIState();
#   }

#   function selectAllTargets() {
#     document.querySelectorAll('.target-item').forEach(e => e.classList.remove('selected'));
#     document.getElementById('all-targets-btn').classList.add('selected');
#     selectedTarget = 'ALL';
#     addTimeline('TARGET', 'Selected: ALL');
#     addExecutionLog('Selected target: ALL', 'info');
#     updateUIState();
#   }

#   function scanTargets() {
#     document.getElementById('btn-scan').disabled = true;
#     document.getElementById('scan-status').textContent = 'Scanning...';
#     addExecutionLog('Starting port scan...', 'info');
#     socket.emit('scan_targets');
#   }

#   function toggleArm() {
#     isArmed = document.getElementById('arm-checkbox').checked;
#     document.getElementById('confirm-text').disabled = !isArmed;
#     updateUIState();
#   }

#   function updateUIState() {
#     const confirmed = document.getElementById('confirm-text').value === 'RUN EXERCISE';
#     const canRun = isArmed && confirmed && selectedScenario && selectedTarget &&
#       ['idle','done','error'].includes(currentState);

#     document.getElementById('btn-run').disabled = !canRun;
#     document.getElementById('btn-stop').disabled = (currentState !== 'running' && currentState !== 'mitigating');

#     // Enable mitigation button after attack is done or for ransomware
#     const canMitigate = selectedScenario &&
#       (currentState === 'done' || currentState === 'error' || selectedScenario === 'ransomware_exercise');
#     document.getElementById('btn-mitigate').disabled = !canMitigate;

#     document.getElementById('btn-undo').disabled =
#       !(currentState === 'done' || currentState === 'error') ||
#       !scenarios[selectedScenario]?.supports_undo;

#     const badge = document.getElementById('state-badge');
#     badge.className = `state-badge state-${currentState}`;
#     badge.textContent = currentState;
#   }

#   function getParams() {
#     const params = { target: selectedTarget || 'ALL' };
#     document.querySelectorAll('[data-param]').forEach(input => {
#       let val = input.value;
#       if (!isNaN(val) && val !== '') val = Number(val);
#       params[input.dataset.param] = val;
#     });
#     return params;
#   }

#   function runScenario() {
#     if (!selectedScenario || !selectedTarget) return;
#     socket.emit('run_scenario', { scenario_id: selectedScenario, params: getParams() });
#     addLog(`Running ${selectedScenario} on ${selectedTarget}`);
#   }

#   function stopScenario() {
#     socket.emit('stop_scenario', { scenario_id: selectedScenario });
#     addExecutionLog('Stop requested', 'warning');
#   }

#   function applyMitigation() {
#     if (!selectedScenario) return;
#     socket.emit('apply_mitigation', {
#       scenario_id: selectedScenario,
#       params: getParams()
#     });
#     addLog(`Applying mitigation for ${selectedScenario}`);
#   }

#   function undoScenario() {
#     socket.emit('undo_scenario', { scenario_id: selectedScenario, params: getParams() });
#   }

#   function backupSystem() {
#     socket.emit('backup_system', {});
#     addExecutionLog('Backup requested...', 'info');
#   }

#   function restoreBackup() {
#     socket.emit('restore_system', {});
#     addExecutionLog('Restore requested...', 'info');
#   }

#   function panicStop() {
#     socket.emit('panic_stop');
#     addLog('🚨 PANIC STOP 🚨');
#     addExecutionLog('🚨 PANIC STOP EXECUTED 🚨', 'error');
#   }

#   // Execution Log functions
#   function addExecutionLog(message, type = 'info') {
#     const log = document.getElementById('execution-log');
#     const time = new Date().toLocaleTimeString();
#     const div = document.createElement('div');
#     div.className = type;
#     div.innerHTML = `<span class="timestamp">[${time}]</span> ${escapeHtml(message)}`;
#     log.appendChild(div);
#     log.scrollTop = log.scrollHeight;

#     // Keep only last 100 lines
#     while (log.children.length > 100) {
#       log.removeChild(log.firstChild);
#     }
#   }

#   function clearExecutionLog() {
#     const log = document.getElementById('execution-log');
#     log.innerHTML = '';
#   }

#   function escapeHtml(text) {
#     const div = document.createElement('div');
#     div.textContent = text;
#     return div.innerHTML;
#   }

#   function addTimeline(type, msg) {
#     const div = document.createElement('div');
#     div.className = 'timeline-item';
#     const time = new Date().toLocaleTimeString();
#     div.innerHTML = `<div class="timestamp">${time}</div><div class="type">${type}</div><div class="message">${msg}</div>`;
#     const tl = document.getElementById('timeline');
#     tl.insertBefore(div, tl.firstChild);
#   }

#   function addLog(msg) {
#     const log = document.getElementById('log-output');
#     const time = new Date().toLocaleTimeString();
#     log.innerHTML += `<br>[${time}] ${msg}`;
#     log.scrollTop = log.scrollHeight;
#   }

#   function updateMetrics() {
#     const div = document.getElementById('metrics-list');
#     div.innerHTML = Object.entries(metricsData).map(([k, v]) =>
#       `<div style="margin:5px 0;"><span style="color:#888">${k}:</span> <span style="color:#e94560">${JSON.stringify(v)}</span></div>`
#     ).join('');
#   }
# </script>
# </body>
# </html>
# """


# # ==================== SCENARIO LOADER ====================
# def load_scenarios():
#     """Dynamically load all scenario modules from security_scenarios folder"""
#     scenarios = {}
#     scenarios_dir = f"{PROJECT_ROOT}/security_scenarios"

#     if not os.path.exists(scenarios_dir):
#         os.makedirs(scenarios_dir, exist_ok=True)
#         return scenarios

#     for filename in os.listdir(scenarios_dir):
#         if filename.endswith(".py") and not filename.startswith("_"):
#             module_name = filename[:-3]
#             try:
#                 import importlib.util

#                 spec = importlib.util.spec_from_file_location(
#                     module_name, f"{scenarios_dir}/{filename}"
#                 )
#                 module = importlib.util.module_from_spec(spec)
#                 sys.modules[module_name] = module
#                 spec.loader.exec_module(module)

#                 # Find first class that looks like a scenario
#                 for attr_name in dir(module):
#                     attr = getattr(module, attr_name)
#                     if (
#                         isinstance(attr, type)
#                         and hasattr(attr, "run")
#                         and hasattr(attr, "id")
#                         and getattr(attr, "id")
#                     ):
#                         scenarios[attr.id] = attr()
#                         print(f"Loaded scenario: {attr.id}")
#                         break

#             except Exception as e:
#                 print(f"Failed to load {filename}: {e}")

#     return scenarios


# # Global scenarios dict
# SCENARIOS: Dict[str, Any] = {}


# # ==================== TARGET SCANNER (TCP PORT SCAN) ====================
# class TargetScanner:
#     """
#     TCP port scan for known lab targets only.

#     Env vars:
#       - VSI_HOST: host/IP to scan (default 127.0.0.1)
#       - VSI_SCAN_TIMEOUT: seconds (default 1.0)
#     """

#     def __init__(self):
#         self.host = os.getenv("VSI_HOST", "127.0.0.1").strip()
#         self.timeout = float(os.getenv("VSI_SCAN_TIMEOUT", "1.0"))

#         # Target -> port mapping
#         self.target_ports = {
#             "PLC": 6001,
#             "S1": 6001,
#             "S2": 6002,
#             "S3": 6003,
#             "S4": 6004,
#             "S5": 6005,
#             "S6": 6006,
#         }

#         self.targets: Dict[str, dict] = {}

#     def _tcp_check(self, host: str, port: int, timeout: float) -> bool:
#         try:
#             sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
#             sock.settimeout(timeout)
#             rc = sock.connect_ex((host, port))
#             sock.close()
#             return rc == 0
#         except Exception:
#             return False

#     def scan(self, timeout: Optional[float] = None) -> Dict[str, dict]:
#         import concurrent.futures

#         timeout = float(timeout if timeout is not None else self.timeout)

#         results: Dict[str, dict] = {}
#         with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
#             future_map = {}
#             for tid, port in self.target_ports.items():
#                 future_map[ex.submit(self._tcp_check, self.host, port, timeout)] = (tid, port)

#             for fut in concurrent.futures.as_completed(future_map):
#                 tid, port = future_map[fut]
#                 is_open = False
#                 try:
#                     is_open = fut.result()
#                 except Exception:
#                     is_open = False

#                 results[tid] = {
#                     "id": tid,
#                     "name": "PLC" if tid == "PLC" else f"Station {tid[1]}",
#                     "type": "plc" if tid == "PLC" else "station",
#                     "host": self.host,
#                     "port": port,
#                     "status": "online" if is_open else "offline",
#                 }

#         self.targets = results
#         return self.targets


# SCANNER = TargetScanner()


# # ==================== CONTEXT & REPORTER ====================
# class Context:
#     def __init__(self, artifacts_folder: str):
#         self.project_root = PROJECT_ROOT
#         self.artifacts_folder = artifacts_folder
#         self.logs_root = LOGS_ROOT

#     def tail_log(self, component: str, lines: int = 100) -> List[str]:
#         try:
#             path = f"{self.logs_root}/{component.lower()}_vsi.log"
#             if not os.path.exists(path):
#                 return []
#             with open(path, "r", encoding="utf-8", errors="replace") as f:
#                 all_lines = f.readlines()
#                 return all_lines[-lines:] if len(all_lines) > lines else all_lines
#         except Exception as e:
#             return [f"Error: {e}"]

#     def create_artifact(self, filename: str, content: dict):
#         os.makedirs(self.artifacts_folder, exist_ok=True)
#         path = f"{self.artifacts_folder}/{filename}"
#         with open(path, "w", encoding="utf-8") as f:
#             json.dump(content, f, indent=2)
#         return path


# class Reporter:
#     def __init__(self, socketio, sid: str, scenario_id: str):
#         self.socketio = socketio
#         self.sid = sid
#         self.scenario_id = scenario_id
#         self.events = []
#         self.metrics = {}
#         self.start_time = None
#         self.end_time = None

#     def report_event(self, event_type: str, message: str, data: dict = None):
#         event = {
#             "timestamp": _now_iso(),
#             "type": event_type,
#             "message": message,
#             "data": data or {},
#         }
#         self.events.append(event)
#         self.socketio.emit(
#             "scenario_event",
#             {"scenario_id": self.scenario_id, "event": event},
#             room=self.sid,
#         )
#         append_jsonl(DASHBOARD_EVENTS_LOG, {"kind": "event", "scenario_id": self.scenario_id, **event})

#     def report_metric(self, name: str, value):
#         self.metrics[name] = value
#         self.socketio.emit(
#             "scenario_metric",
#             {"scenario_id": self.scenario_id, "metric_name": name, "value": value},
#             room=self.sid,
#         )
#         append_jsonl(DASHBOARD_EVENTS_LOG, {"kind": "metric", "scenario_id": self.scenario_id, "name": name, "value": value, "ts": _now_iso()})


# # ==================== BACKUP MANAGER ====================
# class BackupManager:
#     """Manages system backups for ransomware recovery"""

#     def __init__(self, backup_dir: str):
#         self.backup_dir = backup_dir

#     def create_backup(self, source_dirs: List[str]) -> str:
#         """Create timestamped backup of source directories"""
#         timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
#         backup_path = f"{self.backup_dir}/system_backup_{timestamp}"
#         os.makedirs(backup_path, exist_ok=True)

#         # Create manifest
#         manifest = {
#             "created": timestamp,
#             "sources": source_dirs,
#             "files": []
#         }

#         for source in source_dirs:
#             if os.path.exists(source):
#                 # Copy directory tree
#                 dest = f"{backup_path}/{os.path.basename(source)}"
#                 shutil.copytree(source, dest, dirs_exist_ok=True)
#                 manifest["files"].append({
#                     "source": source,
#                     "dest": dest
#                 })

#         # Save manifest
#         with open(f"{backup_path}/manifest.json", "w") as f:
#             json.dump(manifest, f, indent=2)

#         return backup_path

#     def get_latest_backup(self) -> Optional[str]:
#         """Get path to most recent backup"""
#         if not os.path.exists(self.backup_dir):
#             return None

#         backups = [d for d in os.listdir(self.backup_dir) if d.startswith("system_backup_")]
#         if not backups:
#             return None

#         # Sort by timestamp (newest first)
#         backups.sort(reverse=True)
#         return f"{self.backup_dir}/{backups[0]}"

#     def restore_backup(self, backup_path: str, target_dir: str) -> bool:
#         """Restore from backup"""
#         if not os.path.exists(backup_path):
#             return False

#         # Read manifest
#         manifest_path = f"{backup_path}/manifest.json"
#         if os.path.exists(manifest_path):
#             with open(manifest_path, "r") as f:
#                 manifest = json.load(f)

#         # Restore files
#         for item in os.listdir(backup_path):
#             if item == "manifest.json":
#                 continue

#             source = f"{backup_path}/{item}"
#             dest = f"{target_dir}/{item}"

#             if os.path.isdir(source):
#                 if os.path.exists(dest):
#                     shutil.rmtree(dest)
#                 shutil.copytree(source, dest)
#             else:
#                 shutil.copy2(source, dest)

#         return True


# BACKUP_MANAGER = BackupManager(BACKUP_DIR)


# # ==================== FLASK ROUTES ====================
# @app.route("/")
# def index():
#     return render_template_string(HTML_TEMPLATE)


# # ==================== WEBSOCKET EVENTS ====================
# active_runs: Dict[str, dict] = {}
# runs_lock = threading.Lock()


# @socketio.on("connect")
# def handle_connect():
#     # Scenarios metadata
#     metadata = {}
#     for sid, scenario in SCENARIOS.items():
#         metadata[sid] = {
#             "id": getattr(scenario, "id", sid),
#             "name": getattr(scenario, "name", sid),
#             "description": getattr(scenario, "description", ""),
#             "category": getattr(scenario, "category", ""),
#             "targets": getattr(scenario, "targets", ["PLC", "S1", "S2", "S3", "S4", "S5", "S6", "ALL"]),
#             "supports_undo": bool(getattr(scenario, "supports_undo", False)),
#             "risk_level": getattr(scenario, "risk_level", "low"),
#             "default_params": getattr(scenario, "default_params", {}),
#             "mitigation_info": getattr(scenario, "mitigation_info", ""),
#         }
#     emit("scenarios_list", {"scenarios": metadata})

#     # Auto-scan targets
#     targets = SCANNER.scan(timeout=SCANNER.timeout)
#     emit("targets_update", {"targets": targets})


# @socketio.on("scan_targets")
# def handle_scan():
#     targets = SCANNER.scan(timeout=SCANNER.timeout)
#     online = sum(1 for t in targets.values() if t.get("status") == "online")
#     emit(
#         "scan_complete",
#         {
#             "targets": targets,
#             "online_count": online,
#             "total_count": len(targets),
#             "host": SCANNER.host,
#         },
#     )


# @socketio.on("run_scenario")
# def handle_run(data):
#     sid = request.sid
#     scenario_id = (data or {}).get("scenario_id")
#     params = (data or {}).get("params", {})

#     if not scenario_id or scenario_id not in SCENARIOS:
#         emit("scenario_error", {"scenario_id": scenario_id or "?", "error": "Unknown scenario"}, room=sid)
#         return

#     scenario = SCENARIOS[scenario_id]
#     try:
#         valid, error = scenario.validate_params(params)
#     except Exception as e:
#         valid, error = False, f"validate_params crashed: {e}"

#     if not valid:
#         emit("scenario_error", {"scenario_id": scenario_id, "error": f"Invalid params: {error}"}, room=sid)
#         return

#     timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
#     artifacts = f"{SECURITY_RUNS_DIR}/{timestamp}_{scenario_id}"
#     os.makedirs(artifacts, exist_ok=True)

#     ctx = Context(artifacts)
#     reporter = Reporter(socketio, sid, scenario_id)
#     stop_event = threading.Event()

#     run_id = f"{sid}_{scenario_id}"
#     with runs_lock:
#         active_runs[run_id] = {"stop_event": stop_event, "state": "running"}

#     def worker():
#         try:
#             reporter.start_time = time.time()
#             socketio.emit("scenario_started", {"scenario_id": scenario_id}, room=sid)

#             scenario.run(ctx, params, reporter, stop_event)

#             result = {
#                 "scenario_id": scenario_id,
#                 "params": params,
#                 "events": reporter.events,
#                 "metrics": reporter.metrics,
#                 "success": True,
#                 "artifacts": artifacts,
#                 "start_iso": _now_iso(),
#             }
#             ctx.create_artifact("result.json", result)

#             socketio.emit("scenario_complete", {"scenario_id": scenario_id, "artifacts": artifacts}, room=sid)

#         except Exception as e:
#             socketio.emit("scenario_error", {"scenario_id": scenario_id, "error": str(e)}, room=sid)
#         finally:
#             with runs_lock:
#                 if run_id in active_runs:
#                     active_runs[run_id]["state"] = "done"

#     threading.Thread(target=worker, daemon=True).start()


# @socketio.on("stop_scenario")
# def handle_stop(data):
#     sid = request.sid
#     scenario_id = (data or {}).get("scenario_id")
#     run_id = f"{sid}_{scenario_id}"

#     with runs_lock:
#         if run_id in active_runs:
#             active_runs[run_id]["stop_event"].set()


# @socketio.on("apply_mitigation")
# def handle_mitigation(data):
#     """Apply mitigation for a scenario"""
#     sid = request.sid
#     scenario_id = (data or {}).get("scenario_id")
#     params = (data or {}).get("params", {})

#     if not scenario_id or scenario_id not in SCENARIOS:
#         emit("mitigation_error", {"error": "Unknown scenario"}, room=sid)
#         return

#     scenario = SCENARIOS[scenario_id]

#     # Check if scenario has mitigation method
#     if not hasattr(scenario, 'mitigate'):
#         # Fall back to undo if no specific mitigation
#         if hasattr(scenario, 'undo') and getattr(scenario, 'supports_undo', False):
#             socketio.emit("mitigation_started", {"scenario_id": scenario_id}, room=sid)
#             # Call undo as mitigation
#             timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
#             artifacts = f"{SECURITY_RUNS_DIR}/{timestamp}_{scenario_id}_mitigation"
#             os.makedirs(artifacts, exist_ok=True)

#             ctx = Context(artifacts)
#             reporter = Reporter(socketio, sid, scenario_id)
#             stop_event = threading.Event()

#             def mitigation_worker():
#                 try:
#                     scenario.undo(ctx, params, reporter, stop_event)
#                     socketio.emit("mitigation_complete", {
#                         "scenario_id": scenario_id,
#                         "result": "Mitigation applied via undo method",
#                         "artifacts": artifacts
#                     }, room=sid)
#                 except Exception as e:
#                     socketio.emit("mitigation_error", {"error": str(e)}, room=sid)

#             threading.Thread(target=mitigation_worker, daemon=True).start()
#             return
#         else:
#             emit("mitigation_error", {"error": "Scenario has no mitigation or undo method"}, room=sid)
#             return

#     # Run scenario-specific mitigation
#     socketio.emit("mitigation_started", {"scenario_id": scenario_id}, room=sid)

#     timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
#     artifacts = f"{SECURITY_RUNS_DIR}/{timestamp}_{scenario_id}_mitigation"
#     os.makedirs(artifacts, exist_ok=True)

#     ctx = Context(artifacts)
#     reporter = Reporter(socketio, sid, scenario_id)
#     stop_event = threading.Event()

#     def mitigation_worker():
#         try:
#             scenario.mitigate(ctx, params, reporter, stop_event)
#             socketio.emit("mitigation_complete", {
#                 "scenario_id": scenario_id,
#                 "result": "Mitigation applied successfully",
#                 "artifacts": artifacts
#             }, room=sid)
#         except Exception as e:
#             socketio.emit("mitigation_error", {"error": str(e)}, room=sid)

#     threading.Thread(target=mitigation_worker, daemon=True).start()


# @socketio.on("undo_scenario")
# def handle_undo(data):
#     sid = request.sid
#     scenario_id = (data or {}).get("scenario_id")
#     params = (data or {}).get("params", {})

#     if not scenario_id or scenario_id not in SCENARIOS:
#         socketio.emit("undo_error", {"scenario_id": scenario_id or "?", "error": "Unknown scenario"}, room=sid)
#         return

#     scenario = SCENARIOS[scenario_id]
#     if not bool(getattr(scenario, "supports_undo", False)):
#         socketio.emit("undo_error", {"scenario_id": scenario_id, "error": "Undo not supported"}, room=sid)
#         return

#     timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
#     artifacts = f"{SECURITY_RUNS_DIR}/{timestamp}_{scenario_id}_undo"
#     os.makedirs(artifacts, exist_ok=True)

#     ctx = Context(artifacts)
#     reporter = Reporter(socketio, sid, scenario_id)
#     stop_event = threading.Event()

#     def worker():
#         try:
#             socketio.emit("undo_started", {"scenario_id": scenario_id}, room=sid)
#             scenario.undo(ctx, params, reporter, stop_event)
#             ctx.create_artifact("undo_result.json", {"scenario_id": scenario_id, "params": params, "events": reporter.events, "metrics": reporter.metrics, "success": True})
#             socketio.emit("undo_complete", {"scenario_id": scenario_id, "artifacts": artifacts}, room=sid)
#         except Exception as e:
#             socketio.emit("undo_error", {"scenario_id": scenario_id, "error": str(e)}, room=sid)

#     threading.Thread(target=worker, daemon=True).start()


# @socketio.on("backup_system")
# def handle_backup():
#     """Create system backup"""
#     sid = request.sid
#     socketio.emit("backup_started", {}, room=sid)

#     def backup_worker():
#         try:
#             # Backup key directories
#             sources = [
#                 f"{PROJECT_ROOT}/logs",
#                 f"{PROJECT_ROOT}/security_scenarios",
#             ]
#             # Add any additional VSI-related directories here

#             backup_path = BACKUP_MANAGER.create_backup(sources)
#             socketio.emit("backup_complete", {
#                 "backup_path": backup_path,
#                 "timestamp": datetime.now().isoformat()
#             }, room=sid)
#         except Exception as e:
#             socketio.emit("backup_error", {"error": str(e)}, room=sid)

#     threading.Thread(target=backup_worker, daemon=True).start()


# @socketio.on("restore_system")
# def handle_restore():
#     """Restore from latest backup"""
#     sid = request.sid

#     latest = BACKUP_MANAGER.get_latest_backup()
#     if not latest:
#         socketio.emit("restore_error", {"error": "No backup found"}, room=sid)
#         return

#     socketio.emit("restore_started", {"backup_path": latest}, room=sid)

#     def restore_worker():
#         try:
#             success = BACKUP_MANAGER.restore_backup(latest, PROJECT_ROOT)
#             if success:
#                 socketio.emit("restore_complete", {
#                     "backup_path": latest,
#                     "timestamp": datetime.now().isoformat()
#                 }, room=sid)
#             else:
#                 socketio.emit("restore_error", {"error": "Restore failed"}, room=sid)
#         except Exception as e:
#             socketio.emit("restore_error", {"error": str(e)}, room=sid)

#     threading.Thread(target=restore_worker, daemon=True).start()


# @socketio.on("panic_stop")
# def handle_panic():
#     sid = request.sid
#     with runs_lock:
#         stopped = []
#         for run_id, info in active_runs.items():
#             if run_id.startswith(sid + "_"):
#                 info["stop_event"].set()
#                 stopped.append(run_id.split("_", 1)[1])

#     emit("panic_ack", {"stopped": stopped})


# # ==================== MAIN ====================
# if __name__ == "__main__":
#     print("Loading scenarios...")
#     SCENARIOS.update(load_scenarios())
#     print(f"Loaded {len(SCENARIOS)} scenarios")

#     print("Port-scan config:")
#     print(f"  VSI_HOST={SCANNER.host}")
#     print(f"  VSI_SCAN_TIMEOUT={SCANNER.timeout}")
#     print("Dashboard starting on http://localhost:5000")

#     socketio.run(app, host="0.0.0.0", port=5000, debug=True)
