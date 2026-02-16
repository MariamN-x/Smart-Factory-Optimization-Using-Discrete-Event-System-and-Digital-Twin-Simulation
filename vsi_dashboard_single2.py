#!/usr/bin/env python3
"""
VSI Security Test Dashboard - Single File Version
All-in-one dashboard with embedded attack scenarios.
"""

import os
import sys
import json
import time
import threading
import random
import shutil
import base64
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

# Try to import optional dependencies
try:
    from flask import Flask, render_template_string, request
    from flask_socketio import SocketIO, emit
    HAS_FLASK = True
except ImportError:
    HAS_FLASK = False
    print("Warning: Flask/Flask-SocketIO not installed. Install with: pip install flask flask-socketio")

# ==================== CONFIG ====================
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
LOGS_ROOT = f"{PROJECT_ROOT}/logs"
SECURITY_RUNS_DIR = f"{LOGS_ROOT}/security_runs"
BACKUP_DIR = f"{PROJECT_ROOT}/backups"
CONFIG_FILE = f"{PROJECT_ROOT}/vsi_config.json"

os.makedirs(SECURITY_RUNS_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

def _now_iso():
    return datetime.now().isoformat()

def append_jsonl(path: str, obj: dict):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
    except Exception:
        pass

# ==================== CONFIG-BASED TARGET DISCOVERY ====================
class ConfigTargetDiscovery:
    DEFAULT_CONFIG = {
        "plc": {"id": "PLC", "name": "Main PLC Controller", "host": "127.0.0.1", "port": 502, "type": "plc", "protocol": "modbus"},
        "stations": [
            {"id": "S1", "name": "Station 1 - Assembly", "host": "127.0.0.1", "port": 6001, "type": "station"},
            {"id": "S2", "name": "Station 2 - Testing", "host": "127.0.0.1", "port": 6002, "type": "station"},
            {"id": "S3", "name": "Station 3 - Packaging", "host": "127.0.0.1", "port": 6003, "type": "station"},
            {"id": "S4", "name": "Station 4 - Quality Control", "host": "127.0.0.1", "port": 6004, "type": "station"},
            {"id": "S5", "name": "Station 5 - Storage", "host": "127.0.0.1", "port": 6005, "type": "station"},
            {"id": "S6", "name": "Station 6 - Shipping", "host": "127.0.0.1", "port": 6006, "type": "station"}
        ],
        "network": {"gateway": "192.168.1.1", "subnet": "192.168.1.0/24", "mitm_proxy_port": 8080}
    }
    
    def __init__(self):
        self.config = self._load_config()
        self.targets = {}
        self._discover_targets()
    
    def _load_config(self) -> dict:
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Error loading config: {e}, using defaults")
        return self.DEFAULT_CONFIG
    
    def _discover_targets(self):
        plc_config = self.config.get("plc", self.DEFAULT_CONFIG["plc"])
        self.targets["PLC"] = {
            "id": "PLC", "name": plc_config.get("name", "Main PLC"),
            "host": plc_config.get("host", "127.0.0.1"),
            "port": plc_config.get("port", 502),
            "type": "plc", "protocol": plc_config.get("protocol", "modbus"),
            "status": "online"
        }
        stations = self.config.get("stations", self.DEFAULT_CONFIG["stations"])
        for station in stations:
            self.targets[station["id"]] = {
                "id": station["id"], "name": station.get("name", f"Station {station['id']}"),
                "host": station.get("host", "127.0.0.1"),
                "port": station.get("port", 6000 + int(station["id"][1:])),
                "type": "station", "status": "online"
            }
    
    def get_targets(self) -> Dict[str, dict]:
        return self.targets
    
    def get_target(self, target_id: str) -> Optional[dict]:
        return self.targets.get(target_id)
    
    def get_network_config(self) -> dict:
        return self.config.get("network", self.DEFAULT_CONFIG["network"])

DISCOVERY = ConfigTargetDiscovery()

# ==================== ATTACK SCENARIOS ====================

class MITMActiveAttack:
    id = "mitm_active"
    name = "Active MITM Attack"
    description = "Intercepts and actively modifies Modbus/TCP packets between PLC and stations."
    category = "Network"
    risk_level = "high"
    supports_undo = True
    mitigation_info = "Enable TLS/SSL encryption, implement HMAC, use VPN tunnels"
    targets = ["PLC", "S1", "S2", "S3", "S4", "S5", "S6", "ALL"]
    default_params = {"target": "ALL", "modification_rate": 0.3, "duration_seconds": 60}
    
    def __init__(self):
        self.modified_count = 0
        self.original_values = {}
    
    def validate_params(self, params: dict) -> tuple:
        return True, None
    
    def run(self, ctx, params: dict, reporter, stop_event):
        target = params.get("target", "ALL")
        mod_rate = params.get("modification_rate", 0.3)
        duration = params.get("duration_seconds", 60)
        
        reporter.report_event("ATTACK_START", f"Starting Active MITM on {target}", {"mod_rate": mod_rate})
        
        targets = [target] if target != "ALL" else list(DISCOVERY.get_targets().keys())
        start_time = time.time()
        packet_count = 0
        
        while (time.time() - start_time) < duration and not stop_event.is_set():
            for tgt in targets:
                packet_count += 1
                if random.random() < mod_rate:
                    self.modified_count += 1
                    key = f"{tgt}_{packet_count}"
                    original = random.randint(0, 65535)
                    modified = random.randint(0, 65535)
                    self.original_values[key] = original
                    reporter.report_event("PACKET_MODIFIED", f"Modified packet to {tgt}", {"orig": original, "mod": modified})
                else:
                    reporter.report_event("PACKET_FORWARDED", f"Forwarded packet to {tgt}", {})
            
            reporter.report_metric("packets", packet_count)
            reporter.report_metric("modified", self.modified_count)
            time.sleep(0.1)
        
        reporter.report_event("ATTACK_COMPLETE", f"MITM done. Modified {self.modified_count}/{packet_count} packets", {})
        ctx.create_artifact("mitm_log.json", {"modified": self.modified_count, "packets": packet_count})
    
    def mitigate(self, ctx, params: dict, reporter, stop_event):
        reporter.report_event("MITIGATION_START", "Applying MITM mitigation", {})
        for step in ["Enabling TLS...", "Implementing HMAC...", "Configuring VPN..."]:
            if stop_event.is_set(): break
            reporter.report_event("MITIGATION_STEP", step, {})
            time.sleep(0.3)
        reporter.report_event("MITIGATION_COMPLETE", "MITM mitigation applied", {"tls": "enabled", "hmac": "active"})
    
    def undo(self, ctx, params: dict, reporter, stop_event):
        reporter.report_event("UNDO_START", "Restoring original values", {})
        for key, val in list(self.original_values.items())[:100]:
            if stop_event.is_set(): break
            reporter.report_event("VALUE_RESTORED", f"Restored {key}", {"value": val})
        self.original_values.clear()
        reporter.report_event("UNDO_COMPLETE", "Values restored", {})


class MITMPassiveAttack:
    id = "mitm_passive"
    name = "Passive MITM (Sniffing)"
    description = "Passively monitors and logs Modbus/TCP communication without modification."
    category = "Network"
    risk_level = "medium"
    supports_undo = False
    mitigation_info = "Network segmentation, encrypted protocols, port security"
    targets = ["PLC", "S1", "S2", "S3", "S4", "S5", "S6", "ALL"]
    default_params = {"target": "ALL", "duration_seconds": 120, "max_packets": 10000}
    
    def __init__(self):
        self.captured_packets = []
    
    def validate_params(self, params: dict) -> tuple:
        return True, None
    
    def run(self, ctx, params: dict, reporter, stop_event):
        target = params.get("target", "ALL")
        duration = params.get("duration_seconds", 120)
        max_packets = params.get("max_packets", 10000)
        
        reporter.report_event("ATTACK_START", f"Starting passive sniffing on {target}", {})
        
        targets = [target] if target != "ALL" else list(DISCOVERY.get_targets().keys())
        start_time = time.time()
        packet_count = 0
        
        while (time.time() - start_time) < duration and packet_count < max_packets and not stop_event.is_set():
            for tgt in targets:
                packet_count += 1
                pkt = {"target": tgt, "type": random.choice(["READ", "WRITE", "HEARTBEAT"]), "len": random.randint(20, 256)}
                self.captured_packets.append(pkt)
                
                if packet_count % 100 == 0:
                    reporter.report_event("PACKET_CAPTURED", f"Captured {packet_count} packets", {})
            
            reporter.report_metric("captured", packet_count)
            time.sleep(0.01)
        
        reporter.report_event("ATTACK_COMPLETE", f"Sniffing done. Captured {packet_count} packets", {})
        ctx.create_artifact("capture.json", {"packets": packet_count, "samples": self.captured_packets[:100]})
    
    def mitigate(self, ctx, params: dict, reporter, stop_event):
        reporter.report_event("MITIGATION_START", "Applying sniffing mitigation", {})
        for step in ["Enabling VLAN isolation...", "Configuring port security...", "Enabling encryption..."]:
            if stop_event.is_set(): break
            reporter.report_event("MITIGATION_STEP", step, {})
            time.sleep(0.3)
        reporter.report_event("MITIGATION_COMPLETE", "Sniffing mitigation applied", {})
    
    def undo(self, ctx, params: dict, reporter, stop_event):
        reporter.report_event("UNDO_START", "Clearing captured data", {})
        self.captured_packets = []
        reporter.report_event("UNDO_COMPLETE", "Data cleared", {})


class LogTamperingAttack:
    id = "log_tampering"
    name = "Log Tampering"
    description = "Modifies, deletes, or injects false entries into system logs."
    category = "Integrity"
    risk_level = "high"
    supports_undo = True
    mitigation_info = "Write-once storage, hash chains, centralized SIEM"
    targets = ["PLC", "S1", "S2", "S3", "S4", "S5", "S6", "ALL"]
    default_params = {"target": "ALL", "tampering_type": "modify", "entries_to_tamper": 50}
    
    def __init__(self):
        self.tampered_entries = []
        self.backup_entries = {}
    
    def validate_params(self, params: dict) -> tuple:
        return True, None
    
    def run(self, ctx, params: dict, reporter, stop_event):
        target = params.get("target", "ALL")
        tamper_type = params.get("tampering_type", "modify")
        entries = params.get("entries_to_tamper", 50)
        
        reporter.report_event("ATTACK_START", f"Starting log tampering ({tamper_type}) on {target}", {})
        
        targets = [target] if target != "ALL" else list(DISCOVERY.get_targets().keys())
        
        for tgt in targets:
            if stop_event.is_set(): break
            for i in range(entries // len(targets)):
                entry_id = f"{tgt}_{i}"
                if tamper_type == "modify":
                    self.backup_entries[entry_id] = {"original": f"log_{i}"}
                    self.tampered_entries.append({"id": entry_id, "type": "modify"})
                elif tamper_type == "delete":
                    self.backup_entries[entry_id] = {"deleted": f"log_{i}"}
                    self.tampered_entries.append({"id": entry_id, "type": "delete"})
                else:
                    self.tampered_entries.append({"id": entry_id, "type": "inject", "fake": True})
                
                if i % 10 == 0:
                    reporter.report_event("TAMPER_PROGRESS", f"Processed {i} entries", {})
        
        reporter.report_event("ATTACK_COMPLETE", f"Tampered {len(self.tampered_entries)} entries", {})
        ctx.create_artifact("tamper_log.json", {"tampered": len(self.tampered_entries)})
    
    def mitigate(self, ctx, params: dict, reporter, stop_event):
        reporter.report_event("MITIGATION_START", "Applying log tampering mitigation", {})
        for step in ["Configuring WORM storage...", "Implementing hash chains...", "Setting up SIEM..."]:
            if stop_event.is_set(): break
            reporter.report_event("MITIGATION_STEP", step, {})
            time.sleep(0.3)
        reporter.report_event("MITIGATION_COMPLETE", "Log tampering mitigation applied", {})
    
    def undo(self, ctx, params: dict, reporter, stop_event):
        reporter.report_event("UNDO_START", "Restoring log entries", {})
        self.tampered_entries = []
        self.backup_entries = {}
        reporter.report_event("UNDO_COMPLETE", "Logs restored", {})


class DoSAttack:
    id = "dos_attack"
    name = "Denial of Service (DoS)"
    description = "Floods targets with excessive requests to cause service degradation."
    category = "Availability"
    risk_level = "high"
    supports_undo = True
    mitigation_info = "Rate limiting, connection timeouts, SYN cookies, traffic filtering"
    targets = ["PLC", "S1", "S2", "S3", "S4", "S5", "S6", "ALL"]
    default_params = {"target": "ALL", "attack_type": "flood", "requests_per_second": 1000, "duration_seconds": 60}
    
    def __init__(self):
        self.request_count = 0
        self.failed_requests = 0
    
    def validate_params(self, params: dict) -> tuple:
        return True, None
    
    def run(self, ctx, params: dict, reporter, stop_event):
        target = params.get("target", "ALL")
        rps = params.get("requests_per_second", 1000)
        duration = params.get("duration_seconds", 60)
        
        reporter.report_event("ATTACK_START", f"Starting DoS flood on {target}", {"rps": rps})
        
        targets = [target] if target != "ALL" else list(DISCOVERY.get_targets().keys())
        start_time = time.time()
        
        while (time.time() - start_time) < duration and not stop_event.is_set():
            for _ in range(rps // 10):
                for tgt in targets:
                    self.request_count += 1
                    if random.random() < 0.3:
                        self.failed_requests += 1
            
            reporter.report_metric("requests", self.request_count)
            reporter.report_metric("failed", self.failed_requests)
            time.sleep(0.1)
        
        reporter.report_event("ATTACK_COMPLETE", f"DoS done. {self.request_count} requests sent", {})
        ctx.create_artifact("dos_log.json", {"requests": self.request_count, "failed": self.failed_requests})
    
    def mitigate(self, ctx, params: dict, reporter, stop_event):
        reporter.report_event("MITIGATION_START", "Applying DoS mitigation", {})
        for step in ["Enabling rate limiting...", "Configuring SYN cookies...", "Setting up traffic filtering..."]:
            if stop_event.is_set(): break
            reporter.report_event("MITIGATION_STEP", step, {})
            time.sleep(0.3)
        reporter.report_event("MITIGATION_COMPLETE", "DoS mitigation applied", {})
    
    def undo(self, ctx, params: dict, reporter, stop_event):
        reporter.report_event("UNDO_START", "Releasing connections", {})
        self.request_count = 0
        self.failed_requests = 0
        reporter.report_event("UNDO_COMPLETE", "Connections released", {})


class FuzzingInputAttack:
    id = "fuzzing_input"
    name = "Fuzzing Input Attack"
    description = "Sends malformed/random data to test input validation."
    category = "Input Validation"
    risk_level = "medium"
    supports_undo = True
    mitigation_info = "Strict input validation, ASLR, stack canaries, parameterized queries"
    targets = ["PLC", "S1", "S2", "S3", "S4", "S5", "S6", "ALL"]
    default_params = {"target": "ALL", "fuzzing_type": "random", "iterations": 1000}
    
    def __init__(self):
        self.fuzzed_inputs = []
        self.crash_count = 0
    
    def validate_params(self, params: dict) -> tuple:
        return True, None
    
    def run(self, ctx, params: dict, reporter, stop_event):
        target = params.get("target", "ALL")
        fuzz_type = params.get("fuzzing_type", "random")
        iterations = params.get("iterations", 1000)
        
        reporter.report_event("ATTACK_START", f"Starting fuzzing ({fuzz_type}) on {target}", {})
        
        targets = [target] if target != "ALL" else list(DISCOVERY.get_targets().keys())
        
        for i in range(iterations):
            if stop_event.is_set(): break
            
            for tgt in targets:
                fuzzed = f"fuzz_{random.randint(0, 65535)}"
                self.fuzzed_inputs.append({"target": tgt, "input": fuzzed})
                
                if random.random() < 0.01:
                    self.crash_count += 1
                    reporter.report_event("CRASH_DETECTED", f"Crash on {tgt}", {})
            
            if i % 100 == 0:
                reporter.report_metric("iterations", i)
                reporter.report_metric("crashes", self.crash_count)
            
            time.sleep(0.01)
        
        reporter.report_event("ATTACK_COMPLETE", f"Fuzzing done. {len(self.fuzzed_inputs)} inputs sent", {})
        ctx.create_artifact("fuzz_log.json", {"inputs": len(self.fuzzed_inputs), "crashes": self.crash_count})
    
    def mitigate(self, ctx, params: dict, reporter, stop_event):
        reporter.report_event("MITIGATION_START", "Applying fuzzing mitigation", {})
        for step in ["Implementing input validation...", "Enabling ASLR...", "Configuring stack canaries..."]:
            if stop_event.is_set(): break
            reporter.report_event("MITIGATION_STEP", step, {})
            time.sleep(0.3)
        reporter.report_event("MITIGATION_COMPLETE", "Fuzzing mitigation applied", {})
    
    def undo(self, ctx, params: dict, reporter, stop_event):
        reporter.report_event("UNDO_START", "Recovering crashed services", {})
        self.fuzzed_inputs = []
        self.crash_count = 0
        reporter.report_event("UNDO_COMPLETE", "Services recovered", {})


class PLCSpamAttack:
    id = "plc_spam"
    name = "PLC Command Spam"
    description = "Floods PLC with excessive Modbus commands."
    category = "Protocol"
    risk_level = "high"
    supports_undo = True
    mitigation_info = "Command rate limiting, authentication, connection limits"
    targets = ["PLC"]
    default_params = {"target": "PLC", "commands_per_second": 500, "duration_seconds": 60}
    
    def __init__(self):
        self.command_count = 0
        self.failed_commands = 0
    
    def validate_params(self, params: dict) -> tuple:
        return True, None
    
    def run(self, ctx, params: dict, reporter, stop_event):
        target = params.get("target", "PLC")
        cps = params.get("commands_per_second", 500)
        duration = params.get("duration_seconds", 60)
        
        reporter.report_event("ATTACK_START", f"Starting PLC spam on {target}", {"cps": cps})
        
        start_time = time.time()
        
        while (time.time() - start_time) < duration and not stop_event.is_set():
            for _ in range(cps // 10):
                self.command_count += 1
                if random.random() < 0.3:
                    self.failed_commands += 1
            
            reporter.report_metric("commands", self.command_count)
            reporter.report_metric("failed", self.failed_commands)
            time.sleep(0.1)
        
        reporter.report_event("ATTACK_COMPLETE", f"PLC spam done. {self.command_count} commands sent", {})
        ctx.create_artifact("spam_log.json", {"commands": self.command_count, "failed": self.failed_commands})
    
    def mitigate(self, ctx, params: dict, reporter, stop_event):
        reporter.report_event("MITIGATION_START", "Applying PLC spam mitigation", {})
        for step in ["Enabling command rate limiting...", "Implementing authentication...", "Configuring connection limits..."]:
            if stop_event.is_set(): break
            reporter.report_event("MITIGATION_STEP", step, {})
            time.sleep(0.3)
        reporter.report_event("MITIGATION_COMPLETE", "PLC spam mitigation applied", {})
    
    def undo(self, ctx, params: dict, reporter, stop_event):
        reporter.report_event("UNDO_START", "Clearing command queue", {})
        self.command_count = 0
        self.failed_commands = 0
        reporter.report_event("UNDO_COMPLETE", "Queue cleared", {})


class RansomwareAttack:
    id = "ransomware_exercise"
    name = "Ransomware Simulation"
    description = "Simulates ransomware by encrypting config files. Reversible for testing."
    category = "Malware"
    risk_level = "critical"
    supports_undo = True
    mitigation_info = "Offline backups, network segmentation, application whitelisting"
    targets = ["PLC", "S1", "S2", "S3", "S4", "S5", "S6", "ALL"]
    default_params = {"target": "ALL", "encryption_speed": "medium"}
    
    def __init__(self):
        self.encrypted_files = []
        self.key = None
    
    def validate_params(self, params: dict) -> tuple:
        return True, None
    
    def run(self, ctx, params: dict, reporter, stop_event):
        target = params.get("target", "ALL")
        speed = params.get("encryption_speed", "medium")
        
        reporter.report_event("ATTACK_START", f"Starting ransomware sim on {target}", {"speed": speed})
        
        self.key = base64.b64encode(os.urandom(32)).decode()
        
        targets = [target] if target != "ALL" else list(DISCOVERY.get_targets().keys())
        
        for tgt in targets:
            if stop_event.is_set(): break
            files = random.randint(10, 30)
            reporter.report_event("ENCRYPTING", f"Encrypting {files} files on {tgt}", {})
            
            for i in range(files):
                if stop_event.is_set(): break
                self.encrypted_files.append({"target": tgt, "file": f"config_{i}.cfg"})
                if i % 5 == 0:
                    reporter.report_event("FILE_ENCRYPTED", f"Encrypted {i}/{files} on {tgt}", {})
                time.sleep(0.1 if speed == "fast" else 0.3 if speed == "medium" else 0.5)
        
        reporter.report_event("ATTACK_COMPLETE", f"Ransomware done. {len(self.encrypted_files)} files encrypted", {})
        reporter.report_metric("encrypted", len(self.encrypted_files))
        ctx.create_artifact("ransom_log.json", {"encrypted": len(self.encrypted_files), "key_id": self.key[:16]})
    
    def mitigate(self, ctx, params: dict, reporter, stop_event):
        reporter.report_event("MITIGATION_START", "Applying ransomware mitigation", {})
        for step in ["Isolating systems...", "Activating backups...", "Enabling segmentation..."]:
            if stop_event.is_set(): break
            reporter.report_event("MITIGATION_STEP", step, {})
            time.sleep(0.3)
        reporter.report_event("MITIGATION_COMPLETE", "Ransomware mitigation applied", {})
    
    def undo(self, ctx, params: dict, reporter, stop_event):
        reporter.report_event("UNDO_START", "Decrypting files", {})
        self.encrypted_files = []
        reporter.report_event("UNDO_COMPLETE", "Files decrypted", {})


class ReplayAttack:
    id = "replay_attack"
    name = "Replay Attack"
    description = "Captures and replays valid Modbus commands."
    category = "Protocol"
    risk_level = "high"
    supports_undo = True
    mitigation_info = "Sequence numbers, timestamps, challenge-response auth"
    targets = ["PLC", "S1", "S2", "S3", "S4", "S5", "S6", "ALL"]
    default_params = {"target": "PLC", "capture_duration": 30, "replay_count": 100}
    
    def __init__(self):
        self.captured = []
        self.replayed = 0
    
    def validate_params(self, params: dict) -> tuple:
        return True, None
    
    def run(self, ctx, params: dict, reporter, stop_event):
        target = params.get("target", "PLC")
        capture_dur = params.get("capture_duration", 30)
        replay_count = params.get("replay_count", 100)
        
        reporter.report_event("ATTACK_START", f"Starting replay attack on {target}", {})
        
        # Capture phase
        reporter.report_event("CAPTURE_START", f"Capturing for {capture_dur}s", {})
        start = time.time()
        while (time.time() - start) < capture_dur and not stop_event.is_set():
            self.captured.append({"cmd": f"cmd_{len(self.captured)}", "target": target})
            time.sleep(0.1)
        reporter.report_event("CAPTURE_COMPLETE", f"Captured {len(self.captured)} commands", {})
        
        # Replay phase
        reporter.report_event("REPLAY_START", f"Replaying {replay_count} times", {})
        for i in range(replay_count):
            if stop_event.is_set(): break
            self.replayed += 1
            if i % 10 == 0:
                reporter.report_metric("replayed", self.replayed)
            time.sleep(0.05)
        
        reporter.report_event("ATTACK_COMPLETE", f"Replay done. {self.replayed} commands replayed", {})
        ctx.create_artifact("replay_log.json", {"captured": len(self.captured), "replayed": self.replayed})
    
    def mitigate(self, ctx, params: dict, reporter, stop_event):
        reporter.report_event("MITIGATION_START", "Applying replay mitigation", {})
        for step in ["Implementing sequence numbers...", "Enabling timestamps...", "Configuring challenge-response..."]:
            if stop_event.is_set(): break
            reporter.report_event("MITIGATION_STEP", step, {})
            time.sleep(0.3)
        reporter.report_event("MITIGATION_COMPLETE", "Replay mitigation applied", {})
    
    def undo(self, ctx, params: dict, reporter, stop_event):
        reporter.report_event("UNDO_START", "Revoking replayed commands", {})
        self.replayed = 0
        reporter.report_event("UNDO_COMPLETE", "Commands revoked", {})


# ==================== SCENARIO REGISTRY ====================
SCENARIOS = {
    "mitm_active": MITMActiveAttack(),
    "mitm_passive": MITMPassiveAttack(),
    "log_tampering": LogTamperingAttack(),
    "dos_attack": DoSAttack(),
    "fuzzing_input": FuzzingInputAttack(),
    "plc_spam": PLCSpamAttack(),
    "ransomware_exercise": RansomwareAttack(),
    "replay_attack": ReplayAttack()
}

# ==================== BACKUP MANAGER ====================
class BackupManager:
    def __init__(self, backup_dir: str):
        self.backup_dir = backup_dir
        
    def create_backup(self, source_dirs: List[str]) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"{self.backup_dir}/backup_{timestamp}"
        os.makedirs(backup_path, exist_ok=True)
        
        for source in source_dirs:
            if os.path.exists(source):
                dest = f"{backup_path}/{os.path.basename(source)}"
                shutil.copytree(source, dest, dirs_exist_ok=True)
        
        return backup_path
    
    def get_latest_backup(self) -> Optional[str]:
        if not os.path.exists(self.backup_dir):
            return None
        backups = [d for d in os.listdir(self.backup_dir) if d.startswith("backup_")]
        if not backups:
            return None
        backups.sort(reverse=True)
        return f"{self.backup_dir}/{backups[0]}"
    
    def restore_backup(self, backup_path: str, target_dir: str) -> bool:
        if not os.path.exists(backup_path):
            return False
        for item in os.listdir(backup_path):
            if item == "manifest.json":
                continue
            source = f"{backup_path}/{item}"
            dest = f"{target_dir}/{item}"
            if os.path.isdir(source):
                if os.path.exists(dest):
                    shutil.rmtree(dest)
                shutil.copytree(source, dest)
            else:
                shutil.copy2(source, dest)
        return True

BACKUP_MANAGER = BackupManager(BACKUP_DIR)

# ==================== CONTEXT & REPORTER ====================
class Context:
    def __init__(self, artifacts_folder: str):
        self.project_root = PROJECT_ROOT
        self.artifacts_folder = artifacts_folder
        self.logs_root = LOGS_ROOT
    
    def create_artifact(self, filename: str, content: dict):
        os.makedirs(self.artifacts_folder, exist_ok=True)
        path = f"{self.artifacts_folder}/{filename}"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(content, f, indent=2)
        return path

class Reporter:
    def __init__(self, socketio, sid: str, scenario_id: str):
        self.socketio = socketio
        self.sid = sid
        self.scenario_id = scenario_id
        self.events = []
        self.metrics = {}
    
    def report_event(self, event_type: str, message: str, data: dict = None):
        event = {"timestamp": _now_iso(), "type": event_type, "message": message, "data": data or {}}
        self.events.append(event)
        self.socketio.emit("scenario_event", {"scenario_id": self.scenario_id, "event": event}, room=self.sid)
    
    def report_metric(self, name: str, value):
        self.metrics[name] = value
        self.socketio.emit("scenario_metric", {"scenario_id": self.scenario_id, "metric_name": name, "value": value}, room=self.sid)

# ==================== HTML TEMPLATE ====================
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>VSI Security Test Dashboard</title>
  <script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
  <style>
    * { margin:0; padding:0; box-sizing:border-box; }
    body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); color:#eaeaea; min-height:100vh; }
    .header { background: rgba(0,0,0,0.3); padding: 20px; border-bottom: 2px solid #e94560; display:flex; justify-content:space-between; align-items:center; }
    .header h1 { color:#e94560; font-size:24px; display:flex; align-items:center; gap:10px; }
    .main-container { display:grid; grid-template-columns: 300px 1fr 350px; gap:20px; padding:20px; height: calc(100vh - 100px); }
    .panel { background: rgba(255,255,255,0.05); border-radius:12px; padding:20px; border: 1px solid rgba(255,255,255,0.1); overflow-y:auto; }
    .panel h2 { color:#e94560; margin-bottom:15px; font-size:18px; text-transform:uppercase; }
    .scenario-list { display:flex; flex-direction:column; gap:10px; }
    .scenario-card { background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); border-radius:8px; padding:15px; cursor:pointer; transition: all 0.3s; position:relative; }
    .scenario-card:hover { background: rgba(233,69,96,0.1); border-color:#e94560; }
    .scenario-card.active { background: rgba(233,69,96,0.2); border-color:#e94560; }
    .scenario-card .name { font-weight:bold; margin-bottom:5px; color:#fff; }
    .scenario-card .category { font-size:12px; color:#888; text-transform:uppercase; }
    .risk { position:absolute; top:10px; right:10px; width:10px; height:10px; border-radius:50%; }
    .risk.low { background:#2ecc71; } .risk.medium { background:#f39c12; } .risk.high { background:#e74c3c; } .risk.critical { background:#9b59b6; }
    .target-section { background: rgba(52,152,219,0.1); border: 1px solid #3498db; border-radius:8px; padding:15px; margin-bottom:20px; }
    .target-section h3 { color:#3498db; margin-bottom:10px; }
    .target-grid { display:grid; grid-template-columns: repeat(auto-fill, minmax(80px,1fr)); gap:10px; margin-top:10px; }
    .target-item { background: rgba(255,255,255,0.05); border: 2px solid transparent; border-radius:8px; padding:10px; text-align:center; cursor:pointer; transition: all 0.3s; }
    .target-item:hover { background: rgba(255,255,255,0.1); }
    .target-item.selected { border-color:#e94560; background: rgba(233,69,96,0.2); }
    .target-item.online { border-left: 4px solid #2ecc71; }
    .target-item .target-id { font-weight:bold; font-size:16px; color:#fff; }
    .target-item .target-status { font-size:10px; text-transform:uppercase; margin-top:5px; color:#2ecc71; }
    .all-targets-btn { grid-column: 1 / -1; background: rgba(155,89,182,0.3); border: 2px dashed #9b59b6; color:#9b59b6; padding:15px; text-align:center; cursor:pointer; border-radius:8px; margin-top:10px; }
    .all-targets-btn.selected { background: rgba(155,89,182,0.7); color:#fff; border-style:solid; }
    .config-info { font-size:12px; color:#888; margin-top:5px; font-style:italic; }
    .arm-section { background: rgba(231,76,60,0.1); border: 2px solid #e74c3c; border-radius:8px; padding:20px; margin-bottom:20px; }
    .arm-checkbox { display:flex; align-items:center; gap:10px; }
    .arm-checkbox input { width:20px; height:20px; }
    .arm-checkbox label { font-weight:bold; color:#e74c3c; }
    .confirmation-input { margin-top:10px; width:100%; padding:10px; background: rgba(0,0,0,0.3); border: 1px solid #e74c3c; border-radius:4px; color:#fff; }
    .button-row { display:flex; gap:10px; margin-bottom:20px; flex-wrap:wrap; }
    button { padding:12px 24px; border:none; border-radius:6px; font-weight:bold; cursor:pointer; transition: all 0.3s; text-transform:uppercase; font-size:12px; }
    button:disabled { opacity:0.5; cursor:not-allowed; }
    .btn-run { background:#e94560; color:white; }
    .btn-run:hover:not(:disabled) { background:#ff6b6b; }
    .btn-stop { background:#f39c12; color:white; }
    .btn-mitigate { background:#2ecc71; color:black; }
    .btn-mitigate:hover:not(:disabled) { background:#27ae60; }
    .btn-undo { background:#3498db; color:white; }
    .btn-panic { background:#e74c3c; color:white; animation:pulse 2s infinite; }
    @keyframes pulse { 0%,100% { box-shadow: 0 0 0 0 rgba(231,76,60,0.7); } 50% { box-shadow: 0 0 0 10px rgba(231,76,60,0); } }
    .attack-controls { background: rgba(241, 196, 15, 0.1); border: 1px solid #f1c40f; border-radius: 8px; padding: 15px; margin-bottom: 20px; display: none; }
    .attack-controls.active { display: block; }
    .attack-controls h4 { color: #f1c40f; margin-bottom: 10px; }
    .btn-backup { background: #f1c40f; color: black; }
    .btn-restore { background: #9b59b6; color: white; }
    .param-editor { background: rgba(0,0,0,0.2); border-radius:8px; padding:15px; }
    .param-field { margin-bottom:15px; }
    .param-field label { display:block; margin-bottom:5px; color:#aaa; font-size:12px; text-transform:uppercase; }
    .param-field input, .param-field select { width:100%; padding:10px; background: rgba(255,255,255,0.1); border: 1px solid rgba(255,255,255,0.2); border-radius:4px; color:#fff; }
    .description-box { background: rgba(255,255,255,0.05); border-radius:8px; padding:15px; margin-bottom:20px; font-size:14px; line-height:1.6; color:#bbb; max-height:200px; overflow-y:auto; }
    .execution-log-panel { background: rgba(0,0,0,0.3); border-radius:8px; padding:15px; font-family: 'Courier New', monospace; font-size:12px; max-height:300px; overflow-y:auto; color:#2ecc71; border: 1px solid rgba(46, 204, 113, 0.3); }
    .execution-log-panel .timestamp { color: #888; }
    .execution-log-panel .error { color: #e74c3c; }
    .execution-log-panel .warning { color: #f39c12; }
    .execution-log-panel .success { color: #2ecc71; }
    .execution-log-panel .info { color: #3498db; }
    .execution-log-panel .mitigation { color: #9b59b6; }
    .execution-log-panel .attack { color: #e94560; }
    .timeline { display:flex; flex-direction:column; gap:10px; max-height:400px; overflow-y:auto; }
    .timeline-item { background: rgba(255,255,255,0.05); border-left: 3px solid #e94560; padding:10px; border-radius: 0 4px 4px 0; font-size:13px; }
    .timeline-item .timestamp { color:#888; font-size:11px; }
    .timeline-item .type { color:#e94560; font-weight:bold; font-size:10px; text-transform:uppercase; }
    .timeline-item .message { color:#ddd; margin-top:5px; }
    .state-badge { display:inline-block; padding:5px 15px; border-radius:20px; font-size:12px; font-weight:bold; text-transform:uppercase; }
    .state-idle { background: rgba(149,165,166,0.3); color:#95a5a6; }
    .state-running { background: rgba(46,204,113,0.3); color:#2ecc71; animation:blink 1s infinite; }
    .state-mitigating { background: rgba(46, 204, 113, 0.3); color:#2ecc71; animation:blink 1s infinite; }
    .state-undoing { background: rgba(52,152,219,0.3); color:#3498db; }
    .state-error { background: rgba(231,76,60,0.3); color:#e74c3c; }
    .state-done { background: rgba(155,89,182,0.3); color:#9b59b6; }
    @keyframes blink { 0%,100% { opacity:1; } 50% { opacity:0.5; } }
    .bottom-section { grid-column: 1 / -1; display:grid; grid-template-columns: 1fr 1fr; gap:20px; }
    .results-panel { background: rgba(255,255,255,0.05); border-radius:12px; padding:20px; border: 1px solid rgba(255,255,255,0.1); }
    .log-output { background: rgba(0,0,0,0.3); border-radius:8px; padding:15px; font-family: 'Courier New', monospace; font-size:12px; height:150px; overflow-y:auto; color:#2ecc71; }
    .hidden { display:none; }
  </style>
</head>
<body>
  <div class="header">
    <h1><span>🛡️</span> VSI Security Test Dashboard</h1>
    <div class="status-indicator" id="connection-status">
      <span style="color:#e74c3c">●</span> <span id="connection-text">Disconnected</span>
    </div>
  </div>

  <div class="main-container">
    <div class="panel">
      <h2>Attack Scenarios</h2>
      <div class="scenario-list" id="scenario-list"><div>Loading...</div></div>
    </div>

    <div class="panel">
      <h2>Control Center</h2>

      <div class="target-section">
        <h3>🔍 Discovered Targets (Config-Based)</h3>
        <div class="config-info" id="config-info">Loading from vsi_config.json...</div>
        <div class="target-grid" id="target-grid"></div>
        <div class="all-targets-btn" id="all-targets-btn" onclick="selectAllTargets()">ALL TARGETS</div>
      </div>

      <div class="arm-section">
        <div class="arm-checkbox">
          <input type="checkbox" id="arm-checkbox" onchange="toggleArm()">
          <label for="arm-checkbox">ARM SYSTEM</label>
        </div>
        <input type="text" class="confirmation-input" id="confirm-text"
               placeholder="Type 'RUN EXERCISE' to confirm" disabled oninput="updateUIState()">
      </div>

      <div class="button-row">
        <button class="btn-run" id="btn-run" disabled onclick="runScenario()">▶ Run Attack</button>
        <button class="btn-stop" id="btn-stop" disabled onclick="stopScenario()">⏹ Stop</button>
        <button class="btn-mitigate" id="btn-mitigate" disabled onclick="applyMitigation()">🛡️ Mitigation</button>
        <button class="btn-undo" id="btn-undo" disabled onclick="undoScenario()">↩ Undo</button>
        <button class="btn-panic" onclick="panicStop()">🚨 PANIC</button>
      </div>

      <div class="attack-controls" id="ransomware-controls">
        <h4>🗄️ Ransomware Controls</h4>
        <div class="button-row">
          <button class="btn-backup" onclick="backupSystem()">💾 Backup</button>
          <button class="btn-restore" onclick="restoreBackup()">📥 Restore</button>
        </div>
      </div>

      <div class="description-box hidden" id="scenario-description">Select a scenario...</div>

      <div class="param-editor" id="param-editor"><p style="color:#666;">Select scenario and target...</p></div>

      <div class="results-panel" style="margin-top: 20px;">
        <h2>⚡ Execution Log</h2>
        <div class="execution-log-panel" id="execution-log"><div class="info">Waiting...</div></div>
      </div>
    </div>

    <div class="panel">
      <h2>Live Timeline</h2>
      <div class="timeline" id="timeline">
        <div class="timeline-item"><div class="timestamp">--:--:--</div><div class="type">INFO</div><div class="message">Dashboard ready</div></div>
      </div>
      <div style="margin-top:20px;"><span class="state-badge state-idle" id="state-badge">IDLE</span></div>
    </div>

    <div class="bottom-section">
      <div class="results-panel">
        <h2>System Log</h2>
        <div class="log-output" id="log-output">> Ready...</div>
      </div>
      <div class="results-panel">
        <h2>Metrics</h2>
        <div id="metrics-list" style="color:#888;">No active scenario</div>
      </div>
    </div>
  </div>

<script>
  let socket = io();
  let scenarios = {};
  let selectedScenario = null;
  let selectedTarget = null;
  let isArmed = false;
  let currentState = 'idle';
  let metricsData = {};

  socket.on('connect', () => {
    document.getElementById('connection-status').innerHTML = '<span style="color:#2ecc71">●</span> Connected';
    addTimeline('SYSTEM', 'Connected');
    addExecutionLog('Connected to VSI Security Dashboard', 'info');
  });

  socket.on('scenarios_list', (data) => {
    scenarios = data.scenarios || {};
    renderScenarios();
  });

  socket.on('targets_update', (data) => {
    updateTargets(data.targets || {});
    document.getElementById('config-info').textContent = 'Config: ' + (data.config_source || 'default');
  });

  socket.on('scenario_started', (data) => {
    currentState = 'running';
    updateUIState();
    addTimeline('START', data.scenario_id + ' started');
    addExecutionLog('=== ATTACK STARTED: ' + data.scenario_id + ' ===', 'attack');
  });

  socket.on('scenario_event', (data) => {
    addTimeline(data.event.type, data.event.message);
    let logClass = 'info';
    const et = data.event.type.toLowerCase();
    if (et.includes('attack') || et.includes('error') || et.includes('encrypt') || et.includes('dos')) logClass = 'attack';
    else if (et.includes('warn')) logClass = 'warning';
    else if (et.includes('complete') || et.includes('success')) logClass = 'success';
    else if (et.includes('mitigation')) logClass = 'mitigation';
    addExecutionLog('[' + data.event.type + '] ' + data.event.message, logClass);
  });

  socket.on('scenario_metric', (data) => {
    metricsData[data.metric_name] = data.value;
    updateMetrics();
  });

  socket.on('scenario_complete', (data) => {
    currentState = 'done';
    updateUIState();
    addTimeline('COMPLETE', 'Scenario finished');
    addExecutionLog('=== ATTACK COMPLETE ===', 'success');
  });

  socket.on('scenario_error', (data) => {
    currentState = 'error';
    updateUIState();
    addTimeline('ERROR', data.error);
    addExecutionLog('ERROR: ' + data.error, 'error');
  });

  socket.on('mitigation_started', (data) => {
    currentState = 'mitigating';
    updateUIState();
    addTimeline('MITIGATION', data.scenario_id + ' mitigation started');
    addExecutionLog('=== MITIGATION STARTED ===', 'mitigation');
  });

  socket.on('mitigation_complete', (data) => {
    currentState = 'done';
    updateUIState();
    addTimeline('MITIGATION', 'Mitigation complete');
    addExecutionLog('=== MITIGATION COMPLETE ===', 'success');
  });

  socket.on('undo_started', (data) => {
    currentState = 'undoing';
    updateUIState();
    addTimeline('UNDO', data.scenario_id + ' undo started');
    addExecutionLog('=== UNDO STARTED ===', 'mitigation');
  });

  socket.on('undo_complete', (data) => {
    currentState = 'done';
    updateUIState();
    addTimeline('UNDO', data.scenario_id + ' undo complete');
    addExecutionLog('=== UNDO COMPLETE ===', 'success');
  });

  socket.on('backup_complete', (data) => {
    addTimeline('BACKUP', 'Backup complete');
    addExecutionLog('Backup saved: ' + data.backup_path, 'success');
  });

  socket.on('restore_complete', (data) => {
    addTimeline('RESTORE', 'Restore complete');
    addExecutionLog('Restored from: ' + data.backup_path, 'success');
  });

  socket.on('panic_ack', (data) => {
    addTimeline('PANIC', 'Emergency stop executed');
    addExecutionLog('🚨 PANIC STOP EXECUTED 🚨', 'error');
  });

  function renderScenarios() {
    const container = document.getElementById('scenario-list');
    container.innerHTML = '';
    Object.entries(scenarios).forEach(([id, s]) => {
      const card = document.createElement('div');
      card.className = 'scenario-card';
      card.innerHTML = '<div class="risk ' + s.risk_level + '"></div><div class="name">' + s.name + '</div><div class="category">' + s.category + '</div>';
      card.onclick = () => selectScenario(id, card);
      container.appendChild(card);
    });
  }

  function selectScenario(id, cardElement) {
    document.querySelectorAll('.scenario-card').forEach(c => c.classList.remove('active'));
    cardElement.classList.add('active');
    selectedScenario = id;
    const s = scenarios[id];
    document.getElementById('scenario-description').classList.remove('hidden');
    document.getElementById('scenario-description').innerHTML = '<strong>' + s.name + '</strong><br>' + s.description + '<br><br><em>Mitigation:</em> ' + (s.mitigation_info || '');
    document.querySelectorAll('.attack-controls').forEach(el => el.classList.remove('active'));
    if (id === 'ransomware_exercise') document.getElementById('ransomware-controls').classList.add('active');
    renderParams(s);
    updateUIState();
    addTimeline('SELECT', 'Scenario: ' + s.name);
    addExecutionLog('Selected: ' + s.name, 'info');
  }

  function renderParams(scenario) {
    const container = document.getElementById('param-editor');
    container.innerHTML = '';
    const params = scenario.default_params || {};
    Object.keys(params).forEach((key) => {
      if (key === 'target') return;
      const val = params[key];
      const div = document.createElement('div');
      div.className = 'param-field';
      div.innerHTML = '<label>' + key + '</label><input type="' + (typeof val === 'number' ? 'number' : 'text') + '" id="param-' + key + '" value="' + val + '" data-param="' + key + '">';
      container.appendChild(div);
    });
  }

  function updateTargets(targets) {
    const grid = document.getElementById('target-grid');
    grid.innerHTML = '';
    Object.keys(targets).forEach(id => {
      const div = document.createElement('div');
      div.className = 'target-item online';
      div.dataset.target = id;
      div.innerHTML = '<div class="target-id">' + id + '</div><div class="target-status">online</div>';
      div.onclick = () => selectTarget(id, div);
      grid.appendChild(div);
    });
  }

  function selectTarget(id, el) {
    document.querySelectorAll('.target-item').forEach(e => e.classList.remove('selected'));
    document.getElementById('all-targets-btn').classList.remove('selected');
    el.classList.add('selected');
    selectedTarget = id;
    addTimeline('TARGET', 'Selected: ' + id);
    addExecutionLog('Target: ' + id, 'info');
    updateUIState();
  }

  function selectAllTargets() {
    document.querySelectorAll('.target-item').forEach(e => e.classList.remove('selected'));
    document.getElementById('all-targets-btn').classList.add('selected');
    selectedTarget = 'ALL';
    addTimeline('TARGET', 'Selected: ALL');
    addExecutionLog('Target: ALL', 'info');
    updateUIState();
  }

  function toggleArm() {
    isArmed = document.getElementById('arm-checkbox').checked;
    document.getElementById('confirm-text').disabled = !isArmed;
    updateUIState();
  }

  function updateUIState() {
    const confirmed = document.getElementById('confirm-text').value === 'RUN EXERCISE';
    const canRun = isArmed && confirmed && selectedScenario && selectedTarget && ['idle','done','error'].includes(currentState);
    document.getElementById('btn-run').disabled = !canRun;
    document.getElementById('btn-stop').disabled = (currentState !== 'running' && currentState !== 'mitigating');
    document.getElementById('btn-mitigate').disabled = !(selectedScenario && (currentState === 'done' || currentState === 'error'));
    document.getElementById('btn-undo').disabled = !(currentState === 'done' || currentState === 'error') || !scenarios[selectedScenario]?.supports_undo;
    document.getElementById('state-badge').className = 'state-badge state-' + currentState;
    document.getElementById('state-badge').textContent = currentState;
  }

  function getParams() {
    const params = { target: selectedTarget || 'ALL' };
    document.querySelectorAll('[data-param]').forEach(input => {
      let val = input.value;
      if (!isNaN(val) && val !== '') val = Number(val);
      params[input.dataset.param] = val;
    });
    return params;
  }

  function runScenario() {
    if (!selectedScenario || !selectedTarget) return;
    socket.emit('run_scenario', { scenario_id: selectedScenario, params: getParams() });
  }

  function stopScenario() {
    socket.emit('stop_scenario', { scenario_id: selectedScenario });
    addExecutionLog('Stop requested', 'warning');
  }

  function applyMitigation() {
    if (!selectedScenario) return;
    socket.emit('apply_mitigation', { scenario_id: selectedScenario, params: getParams() });
  }

  function undoScenario() {
    socket.emit('undo_scenario', { scenario_id: selectedScenario, params: getParams() });
  }

  function backupSystem() {
    socket.emit('backup_system', {});
    addExecutionLog('Backup requested...', 'info');
  }

  function restoreBackup() {
    socket.emit('restore_system', {});
    addExecutionLog('Restore requested...', 'info');
  }

  function panicStop() {
    socket.emit('panic_stop');
    addExecutionLog('🚨 PANIC STOP EXECUTED 🚨', 'error');
  }

  function addExecutionLog(message, type) {
    const log = document.getElementById('execution-log');
    const time = new Date().toLocaleTimeString();
    const div = document.createElement('div');
    div.className = type;
    div.innerHTML = '<span class="timestamp">[' + time + ']</span> ' + message.replace(/</g, '&lt;');
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
    while (log.children.length > 100) log.removeChild(log.firstChild);
  }

  function addTimeline(type, msg) {
    const div = document.createElement('div');
    div.className = 'timeline-item';
    const time = new Date().toLocaleTimeString();
    div.innerHTML = '<div class="timestamp">' + time + '</div><div class="type">' + type + '</div><div class="message">' + msg + '</div>';
    const tl = document.getElementById('timeline');
    tl.insertBefore(div, tl.firstChild);
  }

  function updateMetrics() {
    const div = document.getElementById('metrics-list');
    div.innerHTML = Object.entries(metricsData).map(([k, v]) => '<div style="margin:5px 0;"><span style="color:#888">' + k + ':</span> <span style="color:#e94560">' + JSON.stringify(v) + '</span></div>').join('');
  }
</script>
</body>
</html>
'''

# ==================== FLASK APP ====================
if HAS_FLASK:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "vsi-security-dashboard-secret-key"
    socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")
    
    active_runs = {}
    runs_lock = threading.Lock()
    
    @app.route("/")
    def index():
        return render_template_string(HTML_TEMPLATE)
    
    @socketio.on("connect")
    def handle_connect():
        metadata = {}
        for sid, scenario in SCENARIOS.items():
            metadata[sid] = {
                "id": getattr(scenario, "id", sid),
                "name": getattr(scenario, "name", sid),
                "description": getattr(scenario, "description", ""),
                "category": getattr(scenario, "category", ""),
                "supports_undo": bool(getattr(scenario, "supports_undo", False)),
                "risk_level": getattr(scenario, "risk_level", "low"),
                "default_params": getattr(scenario, "default_params", {}),
                "mitigation_info": getattr(scenario, "mitigation_info", ""),
            }
        emit("scenarios_list", {"scenarios": metadata})
        
        targets = DISCOVERY.get_targets()
        config_source = "file" if os.path.exists(CONFIG_FILE) else "default"
        emit("targets_update", {"targets": targets, "config_source": config_source})
    
    @socketio.on("run_scenario")
    def handle_run(data):
        sid = request.sid
        scenario_id = (data or {}).get("scenario_id")
        params = (data or {}).get("params", {})
        
        if not scenario_id or scenario_id not in SCENARIOS:
            emit("scenario_error", {"error": "Unknown scenario"}, room=sid)
            return
        
        scenario = SCENARIOS[scenario_id]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        artifacts = f"{SECURITY_RUNS_DIR}/{timestamp}_{scenario_id}"
        os.makedirs(artifacts, exist_ok=True)
        
        ctx = Context(artifacts)
        reporter = Reporter(socketio, sid, scenario_id)
        stop_event = threading.Event()
        
        run_id = f"{sid}_{scenario_id}"
        with runs_lock:
            active_runs[run_id] = {"stop_event": stop_event}
        
        def worker():
            try:
                emit("scenario_started", {"scenario_id": scenario_id}, room=sid)
                scenario.run(ctx, params, reporter, stop_event)
                emit("scenario_complete", {"scenario_id": scenario_id}, room=sid)
            except Exception as e:
                emit("scenario_error", {"error": str(e)}, room=sid)
        
        threading.Thread(target=worker, daemon=True).start()
    
    @socketio.on("stop_scenario")
    def handle_stop(data):
        sid = request.sid
        scenario_id = (data or {}).get("scenario_id")
        run_id = f"{sid}_{scenario_id}"
        with runs_lock:
            if run_id in active_runs:
                active_runs[run_id]["stop_event"].set()
    
    @socketio.on("apply_mitigation")
    def handle_mitigation(data):
        sid = request.sid
        scenario_id = (data or {}).get("scenario_id")
        params = (data or {}).get("params", {})
        
        if not scenario_id or scenario_id not in SCENARIOS:
            emit("mitigation_error", {"error": "Unknown scenario"}, room=sid)
            return
        
        scenario = SCENARIOS[scenario_id]
        if not hasattr(scenario, 'mitigate'):
            emit("mitigation_error", {"error": "No mitigation available"}, room=sid)
            return
        
        emit("mitigation_started", {"scenario_id": scenario_id}, room=sid)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        artifacts = f"{SECURITY_RUNS_DIR}/{timestamp}_{scenario_id}_mitigation"
        os.makedirs(artifacts, exist_ok=True)
        
        ctx = Context(artifacts)
        reporter = Reporter(socketio, sid, scenario_id)
        stop_event = threading.Event()
        
        def mitigation_worker():
            try:
                scenario.mitigate(ctx, params, reporter, stop_event)
                emit("mitigation_complete", {"scenario_id": scenario_id}, room=sid)
            except Exception as e:
                emit("mitigation_error", {"error": str(e)}, room=sid)
        
        threading.Thread(target=mitigation_worker, daemon=True).start()
    
    @socketio.on("undo_scenario")
    def handle_undo(data):
        sid = request.sid
        scenario_id = (data or {}).get("scenario_id")
        params = (data or {}).get("params", {})
        
        if not scenario_id or scenario_id not in SCENARIOS:
            emit("undo_error", {"error": "Unknown scenario"}, room=sid)
            return
        
        scenario = SCENARIOS[scenario_id]
        if not getattr(scenario, "supports_undo", False):
            emit("undo_error", {"error": "Undo not supported"}, room=sid)
            return
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        artifacts = f"{SECURITY_RUNS_DIR}/{timestamp}_{scenario_id}_undo"
        os.makedirs(artifacts, exist_ok=True)
        
        ctx = Context(artifacts)
        reporter = Reporter(socketio, sid, scenario_id)
        stop_event = threading.Event()
        
        def worker():
            try:
                emit("undo_started", {"scenario_id": scenario_id}, room=sid)
                scenario.undo(ctx, params, reporter, stop_event)
                emit("undo_complete", {"scenario_id": scenario_id}, room=sid)
            except Exception as e:
                emit("undo_error", {"error": str(e)}, room=sid)
        
        threading.Thread(target=worker, daemon=True).start()
    
    @socketio.on("backup_system")
    def handle_backup():
        sid = request.sid
        emit("backup_started", {}, room=sid)
        
        def backup_worker():
            try:
                sources = [f"{PROJECT_ROOT}/logs", f"{PROJECT_ROOT}/security_scenarios"]
                backup_path = BACKUP_MANAGER.create_backup(sources)
                emit("backup_complete", {"backup_path": backup_path}, room=sid)
            except Exception as e:
                emit("backup_error", {"error": str(e)}, room=sid)
        
        threading.Thread(target=backup_worker, daemon=True).start()
    
    @socketio.on("restore_system")
    def handle_restore():
        sid = request.sid
        latest = BACKUP_MANAGER.get_latest_backup()
        if not latest:
            emit("restore_error", {"error": "No backup found"}, room=sid)
            return
        
        emit("restore_started", {"backup_path": latest}, room=sid)
        
        def restore_worker():
            try:
                BACKUP_MANAGER.restore_backup(latest, PROJECT_ROOT)
                emit("restore_complete", {"backup_path": latest}, room=sid)
            except Exception as e:
                emit("restore_error", {"error": str(e)}, room=sid)
        
        threading.Thread(target=restore_worker, daemon=True).start()
    
    @socketio.on("panic_stop")
    def handle_panic():
        sid = request.sid
        with runs_lock:
            for run_id, info in active_runs.items():
                if run_id.startswith(sid + "_"):
                    info["stop_event"].set()
        emit("panic_ack", {"stopped": []}, room=sid)

# ==================== MAIN ====================
if __name__ == "__main__":
    print("=" * 60)
    print("VSI Security Test Dashboard")
    print("=" * 60)
    print(f"\nLoaded {len(SCENARIOS)} attack scenarios:")
    for sid, s in SCENARIOS.items():
        print(f"  - {sid}: {s.name} [{s.risk_level}]")
    
    print(f"\nDiscovered targets:")
    for tid, t in DISCOVERY.get_targets().items():
        print(f"  - {tid}: {t['name']} @ {t['host']}:{t['port']}")
    
    if HAS_FLASK:
        print("\n" + "=" * 60)
        print("Dashboard starting on http://localhost:5000")
        print("Press Ctrl+C to stop")
        print("=" * 60)
        socketio.run(app, host="0.0.0.0", port=5000, debug=False)
    else:
        print("\nError: Flask not installed. Run: pip install flask flask-socketio")
