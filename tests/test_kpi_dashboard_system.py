import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPS = ROOT / "_deps"
if DEPS.exists():
    sys.path.insert(0, str(DEPS))
sys.path.insert(0, str(ROOT))

import aiohttp  # noqa: E402


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s: #stream TCP while datagram UDP
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class TestKpiDashboardSystemRuntime(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.port = _free_port()

        self.st1 = self.base / "check.ST1_ComponentKitting.log"
        self.st2 = self.base / "check.ST2_FrameCoreAssembly.log"
        self.plc = self.base / "check.PLC_LineCoordinator.log"

        self.st1.write_text(
            (
                "+= ST1_ComponentKitting +=\n"
                "VSI time: 100 ns\n"
                "Inputs:\n"
                "cmd_start = 1\n"
                "Outputs:\n"
                "busy = 1\n"
                "accept = 0\n"
                "\n"
            ),
            encoding="utf-8",
        )
        self.st2.write_text(
            (
                "+= ST2_FrameCoreAssembly +=\n"
                "VSI time: 200 ns\n"
                "Inputs:\n"
                "cmd_start = 0\n"
                "Outputs:\n"
                "ready = 1\n"
                "accept = 1\n"
                "\n"
            ),
            encoding="utf-8",
        )
        self.plc.write_text(
            (
                "+= PLC_LineCoordinator +=\n"
                "VSI time: 150 ns\n"
                "Inputs:\n"
                "S1_ready = 1\n"
                "S2_busy = 1\n"
                "Outputs:\n"
                "S1_cmd_stop = 0\n"
                "S2_cmd_stop = 0\n"
                "\n"
            ),
            encoding="utf-8",
        )

        script = ROOT / "live_log_dashboard_web_station_VSI_full.py"
        env = os.environ.copy()
        if DEPS.exists():
            existing = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = f"{DEPS}{os.pathsep}{existing}" if existing else str(DEPS)

        cmd = [
            sys.executable,
            str(script),
            "--from-start",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            "--log",
            str(self.plc),
            str(self.st1),
            str(self.st2),
        ]
        self.proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )

        self._wait_until_ready()

    def tearDown(self):
        if getattr(self, "proc", None) is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=8)
            except Exception:
                self.proc.kill()
        if getattr(self, "temp_dir", None) is not None:
            self.temp_dir.cleanup()

    def _wait_until_ready(self):
        deadline = time.time() + 20
        last_exc = None
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"Dashboard exited early with code {self.proc.returncode}")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/payload", timeout=2):
                    return
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                time.sleep(0.2)
        raise RuntimeError(f"Dashboard did not start in time: {last_exc}")

    def _get_json(self, path: str):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=3) as resp:
            return json.load(resp)

    def test_index_and_payload_endpoints(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/", timeout=3) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
        self.assertIn("Project KPI Dashboard", body)

        payload = self._get_json("/api/payload")
        self.assertIn("items", payload)
        self.assertIn("summary", payload)
        self.assertGreaterEqual(len(payload["items"]), 3)

    def test_websocket_sends_initial_payload(self):
        async def ws_read():
            ws_timeout = aiohttp.ClientWSTimeout(ws_receive=5, ws_close=5)
            async with aiohttp.ClientSession() as sess:
                async with sess.ws_connect(
                    f"http://127.0.0.1:{self.port}/ws", timeout=ws_timeout
                ) as ws:
                    msg = await ws.receive(timeout=5)
                    self.assertEqual(msg.type.name, "TEXT")
                    data = json.loads(msg.data)
                    self.assertIn("items", data)
                    self.assertGreaterEqual(len(data["items"]), 3)

        import asyncio

        asyncio.run(ws_read())

    def test_tail_update_visible_via_api(self):
        marker = f"SYSTEM_TEST_MARKER_{int(time.time())}"
        with self.st1.open("a", encoding="utf-8") as f:
            f.write(
                (
                    "\n+= ST1_ComponentKitting +=\n"
                    "VSI time: 300 ns\n"
                    "Inputs:\n"
                    "cmd_start = 0\n"
                    "Outputs:\n"
                    "busy = 0\n"
                    f"Internal: note = {marker}\n"
                    "\n"
                )
            )
            f.flush()

        deadline = time.time() + 10
        while time.time() < deadline:
            payload = self._get_json("/api/payload")
            blob = json.dumps(payload)
            if marker in blob:
                return
            time.sleep(0.2)
        self.fail("Tail update marker was not observed in /api/payload")

    def test_missing_route_returns_not_found(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(f"http://127.0.0.1:{self.port}/does-not-exist", timeout=3)
        self.assertEqual(ctx.exception.code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
