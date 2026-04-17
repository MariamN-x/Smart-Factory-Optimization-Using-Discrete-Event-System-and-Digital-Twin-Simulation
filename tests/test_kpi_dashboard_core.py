import asyncio
import json
import os
import queue
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
DEPS = ROOT / "_deps"
if DEPS.exists():
    sys.path.insert(0, str(DEPS))
sys.path.insert(0, str(ROOT))

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import live_log_dashboard_web_station_VSI_full as dash  # noqa: E402


class TestKpiDashboardCoreUtils(unittest.TestCase):
    def test_extract_log_kpis_with_prefixed_and_embedded(self):
        inputs = {
            "inventory_ok": "true",
            "S1_accept": "2",
            "ignored_key": "x",
        }
        outputs = {
            "reject": "3",
            "kpis": '{"accepted_total": 8, "rejected_total": 4, "availability": 0.97}',
        }

        out = dash._extract_log_kpis(inputs, outputs)
        self.assertEqual(out["inventory_ok"], 1)
        self.assertEqual(out["s1_accept"], 2)
        self.assertEqual(out["reject"], 3)
        self.assertEqual(out["kpis_accepted_total"], 8)
        self.assertEqual(out["kpis_rejected_total"], 4)
        self.assertEqual(out["kpis_availability"], 0.97)

    def test_parse_embedded_kpis_parses_python_dict_literal(self):
        raw = "{'accepted_total': 9, 'rejected_total': 1}"
        out = dash._parse_embedded_kpis(raw)
        self.assertEqual(out["accepted_total"], 9)
        self.assertEqual(out["rejected_total"], 1)

    def test_parse_embedded_kpis_edge_cases(self):
        self.assertEqual(dash._parse_embedded_kpis({"x": 1}), {"x": 1})
        self.assertEqual(dash._parse_embedded_kpis(123), {})
        self.assertEqual(dash._parse_embedded_kpis("not-a-dict"), {})
        self.assertEqual(dash._parse_embedded_kpis("{not valid json"), {})

    def test_display_and_scalar_conversion_helpers(self):
        disp, raw = dash._display_from_text("", r"D:\KPIs\check.ST9_CustomStation.log")
        self.assertEqual(disp, "ST9 CustomStation")
        self.assertEqual(raw, "ST9 CustomStation")

        disp2, raw2 = dash._display_from_text("PLC_Master", r"D:\KPIs\any.log")
        self.assertEqual(disp2, "PLC")
        self.assertEqual(raw2, "PLC_Master")

        self.assertEqual(dash._to_kpi_scalar(True), 1)
        self.assertEqual(dash._to_kpi_scalar("17"), 17)
        obj = object()
        self.assertEqual(dash._to_kpi_scalar(obj), str(obj))

    def test_payload_js_literal_escapes_html_breakouts(self):
        payload = {"x": "<script>alert(1)</script>&"}
        lit = dash._payload_to_js_literal(payload)
        self.assertIn("\\u003cscript\\u003e", lit)
        self.assertIn("\\u0026", lit)

    def test_server_fallback_text_limits_item_list(self):
        items = [
            {"station": f"Station {i}", "state": "READY", "file": f"f{i}.log"}
            for i in range(1, 36)
        ]
        text = dash._server_fallback_text({"items": items})
        self.assertIn("Server payload: 35 station(s)", text)
        self.assertIn("... and 5 more", text)

    def test_auto_find_log_recursive_filters_extensions_and_log_hint(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            logs_dir = root / "logs"
            other_dir = root / "data"
            logs_dir.mkdir()
            other_dir.mkdir()
            (logs_dir / "keep.log").write_text("a", encoding="utf-8")
            (logs_dir / "keep.txt").write_text("b", encoding="utf-8")
            (logs_dir / "skip.md").write_text("c", encoding="utf-8")
            (other_dir / "nope.md").write_text("d", encoding="utf-8")

            cwd = os.getcwd()
            os.chdir(root)
            try:
                found = set(map(os.path.abspath, dash._auto_find_log_recursive()))
            finally:
                os.chdir(cwd)

            self.assertIn(str((logs_dir / "keep.log").resolve()), found)
            self.assertIn(str((logs_dir / "keep.txt").resolve()), found)
            self.assertNotIn(str((logs_dir / "skip.md").resolve()), found)
            self.assertNotIn(str((other_dir / "nope.md").resolve()), found)

    def test_build_file_list_uses_default_and_auto_paths(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            default_file = root / "default.log"
            auto_file = root / "auto.log"
            default_file.write_text("x", encoding="utf-8")
            auto_file.write_text("y", encoding="utf-8")

            args = SimpleNamespace(log=None, glob=None)
            with mock.patch.object(dash, "DEFAULT_LOG_FILES", [str(default_file)]):
                got = dash.build_file_list(args)
            self.assertEqual(got, [str(default_file.resolve())])

            with (
                mock.patch.object(dash, "DEFAULT_LOG_FILES", []),
                mock.patch.object(dash, "_auto_find_log_recursive", return_value=[str(auto_file)]),
            ):
                got2 = dash.build_file_list(args)
            self.assertEqual(got2, [str(auto_file.resolve())])

    def test_build_file_list_uses_cwd_glob_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            txt = root / "fallback.txt"
            txt.write_text("z", encoding="utf-8")
            args = SimpleNamespace(log=None, glob=None)

            cwd = os.getcwd()
            os.chdir(root)
            try:
                with (
                    mock.patch.object(dash, "DEFAULT_LOG_FILES", []),
                    mock.patch.object(dash, "_auto_find_log_recursive", return_value=[]),
                ):
                    got = dash.build_file_list(args)
            finally:
                os.chdir(cwd)

            self.assertEqual(got, [str(txt.resolve())])


class TestKpiDashboardCoreParsingAndAggregation(unittest.TestCase):
    def test_parse_value_converts_common_types(self):
        self.assertTrue(dash._parse_value("true"))
        self.assertFalse(dash._parse_value("FALSE"))
        self.assertEqual(dash._parse_value("0x10"), 16)
        self.assertEqual(dash._parse_value("42"), 42)
        self.assertEqual(dash._parse_value("3.5"), 3.5)
        self.assertEqual(dash._parse_value("abc"), "abc")

    def test_normalize_station_name_handles_station_and_plc(self):
        station, num, raw = dash.normalize_station_name(
            "ST5_QualityInspection", r"D:\KPIs\check.ST5_QualityInspection.log"
        )
        self.assertEqual(station, "Station 5")
        self.assertEqual(num, 5)
        self.assertEqual(raw, "ST5_QualityInspection")

        station, num, raw = dash.normalize_station_name(
            "", r"D:\KPIs\check.PLC_LineCoordinator.log"
        )
        self.assertEqual(station, "PLC")
        self.assertIsNone(num)
        self.assertEqual(raw, "PLC")

    def test_block_parser_parses_snapshot_and_internal_values(self):
        parser = dash.BlockParser(source_file=r"D:\KPIs\check.ST3_ElectronicsWiring.log")
        lines = [
            "+= ST3_ElectronicsWiring +=",
            "VSI time: 123 ns",
            "Inputs:",
            "cmd_start = 1",
            "Outputs:",
            "busy = 1",
            "accept = 0",
            "Internal: total_completed = 9",
            "",
        ]

        snap = None
        for line in lines:
            maybe_snap, _ = parser.feed_line(line)
            if maybe_snap is not None:
                snap = maybe_snap

        self.assertIsNotNone(snap)
        self.assertEqual(snap.station, "Station 3")
        self.assertEqual(snap.station_num, 3)
        self.assertEqual(snap.vsi_time_ns, 123)
        self.assertEqual(snap.inputs["cmd_start"], 1)
        self.assertEqual(snap.outputs["busy"], 1)
        self.assertEqual(snap.outputs["total_completed"], 9)

    def test_block_parser_emits_rx_packet_event(self):
        parser = dash.BlockParser(source_file=r"D:\KPIs\check.PLC_LineCoordinator.log")
        snap, ev = parser.feed_line("INFO Received packet from ST2")
        self.assertIsNone(snap)
        self.assertIsNotNone(ev)
        self.assertEqual(ev["type"], "rx_packet")
        self.assertEqual(ev["src"], "ST2")

    def test_stats_store_counts_edges_and_exports_fault_state(self):
        store = dash.StatsStore()
        source_file = r"D:\KPIs\check.ST1_ComponentKitting.log"

        snapshots = [
            dash.Snapshot(
                station="Station 1",
                station_raw="ST1_ComponentKitting",
                station_num=1,
                vsi_time_ns=1_000_000_000,
                inputs={"cmd_start": 1},
                outputs={"busy": 1, "done": 0, "fault": 0, "accept": 0, "reject": 0},
                source_file=source_file,
            ),
            dash.Snapshot(
                station="Station 1",
                station_raw="ST1_ComponentKitting",
                station_num=1,
                vsi_time_ns=2_000_000_000,
                inputs={"cmd_start": 0},
                outputs={"busy": 1, "done": 1, "fault": 0, "accept": 1, "reject": 0, "cycle_time_ms": 52},
                source_file=source_file,
            ),
            dash.Snapshot(
                station="Station 1",
                station_raw="ST1_ComponentKitting",
                station_num=1,
                vsi_time_ns=3_000_000_000,
                inputs={},
                outputs={"busy": 0, "done": 0, "fault": 1, "accept": 0, "reject": 1},
                source_file=source_file,
            ),
        ]

        for snap in snapshots:
            store.handle_snapshot(snap)

        payload = store.export_payload()
        self.assertEqual(payload["summary"]["stations"], 1)
        item = payload["items"][0]
        kpis = item["kpis"]

        self.assertEqual(item["state"], "FAULT")
        self.assertEqual(item["utilization"], 1.0)
        self.assertEqual(kpis["cycles_done"], 1)
        self.assertEqual(kpis["faults_count"], 1)
        self.assertEqual(kpis["start_pulses"], 1)
        self.assertEqual(kpis["cumulative_accepts"], 1)
        self.assertEqual(kpis["cumulative_rejects"], 1)
        self.assertEqual(kpis["accept"], 1)
        self.assertEqual(kpis["reject"], 1)
        self.assertEqual(kpis["completed"], 2)
        self.assertEqual(kpis["last_cycle_ms"], 52)

    def test_export_payload_prefers_log_totals(self):
        store = dash.StatsStore()
        source_file = r"D:\KPIs\check.ST2_FrameCoreAssembly.log"

        store.handle_snapshot(
            dash.Snapshot(
                station="Station 2",
                station_raw="ST2_FrameCoreAssembly",
                station_num=2,
                vsi_time_ns=1_000_000_000,
                inputs={},
                outputs={
                    "ready": 1,
                    "accept": 5,
                    "reject": 2,
                    "total_completed": 8,
                },
                source_file=source_file,
            )
        )

        payload = store.export_payload()
        item = payload["items"][0]
        kpis = item["kpis"]

        self.assertEqual(item["state"], "READY")
        self.assertEqual(kpis["accept"], 5)
        self.assertEqual(kpis["reject"], 2)
        self.assertEqual(kpis["completed"], 8)
        self.assertEqual(kpis["accepted_total"], 5)
        self.assertEqual(kpis["rejected_total"], 2)
        self.assertEqual(kpis["completed_total"], 8)

    def test_build_file_list_deduplicates_and_ignores_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            a = root / "a.log"
            b = root / "b.log"
            missing = root / "missing.log"
            a.write_text("x", encoding="utf-8")
            b.write_text("y", encoding="utf-8")

            args = SimpleNamespace(
                log=[str(a), str(a)],
                glob=str(root / "*.log"),
            )
            files = dash.build_file_list(args)

            self.assertNotIn(str(missing.resolve()), files)
            self.assertEqual(set(files), {str(a.resolve()), str(b.resolve())})
            self.assertEqual(len(files), 2)


class TestKpiDashboardCoreTailAndEngine(unittest.TestCase):
    def _wait_for(self, predicate, timeout=3.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.05)
        return False

    def test_tailthread_signatures_and_process_line_paths(self):
        q = queue.Queue()
        tail = dash.TailThread(r"D:\KPIs\missing.log", q, from_start=False)

        self.assertIsNone(tail._path_sig())

        class BadFD:
            def fileno(self):
                raise OSError("bad fd")

        self.assertIsNone(tail._fd_sig(BadFD()))

        tail._process_line("random line\n")
        kind1, payload1 = q.get(timeout=1)
        self.assertEqual(kind1, "raw_file")
        self.assertIn("line", payload1)

        tail._process_line("+= ST4_CalibrationTesting +=\n")
        kind2, payload2 = q.get(timeout=1)
        self.assertEqual(kind2, "raw_station")
        self.assertEqual(payload2["station"], "Station 4")

    def test_block_parser_unknown_line_path(self):
        parser = dash.BlockParser(source_file=r"D:\KPIs\check.ST1_ComponentKitting.log")
        parser.feed_line("+= ST1_ComponentKitting +=\n")
        parser.feed_line("VSI time: 99 ns\n")
        parser.feed_line("Inputs:\n")
        snap, ev = parser.feed_line("THIS_LINE_DOES_NOT_MATCH\n")
        self.assertIsNone(snap)
        self.assertIsNone(ev)

    def test_tailthread_from_start_reads_existing_content(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "check.ST3_ElectronicsWiring.log"
            p.write_text(
                (
                    "+= ST3_ElectronicsWiring +=\n"
                    "VSI time: 10 ns\n"
                    "Inputs:\n"
                    "cmd_start = 1\n"
                    "Outputs:\n"
                    "busy = 1\n"
                    "\n"
                ),
                encoding="utf-8",
            )
            q = queue.Queue()
            tail = dash.TailThread(str(p), q, from_start=True)
            tail.start()
            try:
                ok = self._wait_for(lambda: any(k == "snapshot" for k, _ in list(q.queue)), timeout=2.5)
                self.assertTrue(ok, "Expected snapshot from initial file content")
            finally:
                tail.stop()
                tail.join(timeout=2)

    def test_tailthread_from_end_reads_appended_content(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "check.ST5_QualityInspection.log"
            p.write_text(
                (
                    "+= ST5_QualityInspection +=\n"
                    "VSI time: 10 ns\n"
                    "Inputs:\n"
                    "cmd_start = 1\n"
                    "Outputs:\n"
                    "busy = 1\n"
                    "\n"
                ),
                encoding="utf-8",
            )
            q = queue.Queue()
            tail = dash.TailThread(str(p), q, from_start=False)
            tail.start()
            try:
                time.sleep(0.2)
                self.assertFalse(any(k == "snapshot" for k, _ in list(q.queue)))

                with p.open("a", encoding="utf-8") as f:
                    f.write(
                        (
                            "\n+= ST5_QualityInspection +=\n"
                            "VSI time: 20 ns\n"
                            "Inputs:\n"
                            "cmd_start = 0\n"
                            "Outputs:\n"
                            "busy = 0\n"
                            "\n"
                        )
                    )
                    f.flush()

                ok = self._wait_for(lambda: any(k == "snapshot" for k, _ in list(q.queue)), timeout=2.5)
                self.assertTrue(ok, "Expected snapshot after appending new block")
            finally:
                tail.stop()
                tail.join(timeout=2)

    def test_engine_pump_once_all_message_kinds_and_start_stop(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "check.ST2_FrameCoreAssembly.log"
            p.write_text("", encoding="utf-8")
            engine = dash.Engine([str(p)], from_start=False)

            engine.start()
            try:
                snap = dash.Snapshot(
                    station="Station 2",
                    station_raw="ST2_FrameCoreAssembly",
                    station_num=2,
                    vsi_time_ns=100,
                    inputs={"cmd_start": 1},
                    outputs={"busy": 1, "done": 0},
                    source_file=str(p),
                )
                engine.q_in.put(
                    (
                        "raw_station",
                        {
                            "file": str(p),
                            "station": "Station 2",
                            "station_raw": "ST2_FrameCoreAssembly",
                            "station_num": 2,
                            "line": "raw station line",
                        },
                    )
                )
                engine.q_in.put(("raw_file", {"file": str(p), "line": "Script done on ..."}))
                engine.q_in.put(("snapshot", snap))
                engine.q_in.put(("event", {"type": "rx_packet", "src": "ST2", "file": str(p)}))
                engine.q_in.put(("error", {"file": str(p), "error": "x"}))

                updated = engine.pump_once()
                self.assertTrue(updated)
                self.assertFalse(engine.pump_once())

                payload = engine.store.export_payload()
                self.assertGreaterEqual(payload["summary"]["stations"], 1)
            finally:
                engine.stop()
                for t in engine.tailers:
                    t.join(timeout=2)

    def test_stats_store_plc_state_branches(self):
        store = dash.StatsStore()
        file = "check.PLC_LineCoordinator.log"

        st = store.get(file, "PLC", "PLC", None)
        st.inputs = {"S1_fault": 1}
        st.outputs = {}
        self.assertEqual(store._state(st), "FAULT")

        st.inputs = {"S1_fault": 0}
        store.file_stopped[file] = True
        self.assertEqual(store._state(st), "STOPPED")

        store.file_stopped[file] = False
        st.outputs = {"S1_cmd_stop": 1, "S2_cmd_stop": 1}
        self.assertEqual(store._state(st), "STOPPED")

        st.outputs = {"S1_cmd_stop": 0}
        st.inputs = {"S1_busy": 1}
        self.assertEqual(store._state(st), "RUNNING")

        st.inputs = {"S1_busy": 0, "S1_ready": 1}
        self.assertEqual(store._state(st), "READY")

        st.inputs = {"S1_busy": 0, "S1_ready": 0}
        self.assertEqual(store._state(st), "STOPPED")

    def test_stats_store_non_plc_state_branches(self):
        store = dash.StatsStore()
        file = "check.ST6_PackagingDispatch.log"
        st = store.get(file, "Station 6", "ST6_PackagingDispatch", 6)
        st.outputs = {"fault": 0, "busy": 0, "ready": 0}
        st.inputs = {"cmd_stop": 1}
        self.assertEqual(store._state(st), "STOPPED")

        st.inputs = {"cmd_stop": 0}
        store.file_stopped[file] = True
        self.assertEqual(store._state(st), "STOPPED")

        store.file_stopped[file] = False
        self.assertEqual(store._state(st), "UNKNOWN")

    def test_plc_kpi_fanout_copies_prefixed_values(self):
        store = dash.StatsStore()
        plc_file = "check.PLC_LineCoordinator.log"
        st1_file = "check.ST1_ComponentKitting.log"

        store.handle_snapshot(
            dash.Snapshot(
                station="Station 1",
                station_raw="ST1_ComponentKitting",
                station_num=1,
                vsi_time_ns=100,
                inputs={},
                outputs={"ready": 1},
                source_file=st1_file,
            )
        )
        store.handle_snapshot(
            dash.Snapshot(
                station="PLC",
                station_raw="PLC",
                station_num=None,
                vsi_time_ns=100,
                inputs={},
                outputs={"s1_availability": 0.93},
                source_file=plc_file,
            )
        )

        with mock.patch.object(dash, "ENABLE_PLC_KPI_FANOUT", True):
            payload = store.export_payload()

        st1_item = next(x for x in payload["items"] if x["station"] == "Station 1")
        self.assertEqual(st1_item["kpis"].get("availability"), 0.93)


class TestKpiDashboardCoreWebHandlers(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        class DummyStore:
            def __init__(self):
                self.payload = {
                    "items": [
                        {"station": "Station 1", "state": "READY", "file": "x.log"},
                        {"station": "PLC", "state": "RUNNING", "file": "plc.log"},
                    ],
                    "summary": {"stations": 2, "state_counts": {}},
                }

            def export_payload(self):
                return self.payload

        class DummyEngine:
            def __init__(self):
                self.store = DummyStore()
                self.clients = set()

            def pump_once(self):
                return False

        self.engine = DummyEngine()
        self.app = web.Application()
        self.app["engine"] = self.engine
        self.app["html_page"] = "__INITIAL_PAYLOAD__\n{{SERVER_FALLBACK_TEXT}}"
        self.app.router.add_get("/", dash.index)
        self.app.router.add_get("/api/payload", dash.api_payload)
        self.app.router.add_get("/ws", dash.ws_handler)

        self.server = TestServer(self.app)
        self.client = TestClient(self.server)
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        await self.server.close()

    async def test_index_api_and_websocket_handlers(self):
        r1 = await self.client.get("/")
        self.assertEqual(r1.status, 200)
        html = await r1.text()
        self.assertIn("Server payload: 2 station(s)", html)
        self.assertIn("\\u003c", dash._payload_to_js_literal({"x": "<"}))

        r2 = await self.client.get("/api/payload")
        self.assertEqual(r2.status, 200)
        payload = await r2.json()
        self.assertEqual(payload["summary"]["stations"], 2)

        ws = await self.client.ws_connect("/ws")
        msg = await ws.receive(timeout=5)
        self.assertEqual(msg.type.name, "TEXT")
        ws_payload = json.loads(msg.data)
        self.assertEqual(ws_payload["summary"]["stations"], 2)
        await ws.close()

    async def test_broadcaster_pushes_to_clients_and_prunes_dead(self):
        class GoodWS:
            def __init__(self):
                self.messages = []

            async def send_str(self, msg):
                self.messages.append(msg)

        class BadWS:
            async def send_str(self, msg):
                _ = msg
                raise RuntimeError("dead")

        good = GoodWS()
        bad = BadWS()
        calls = {"n": 0}

        class DummyStore:
            def export_payload(self):
                return {"items": [{"station": "S1"}], "summary": {"stations": 1}}

        class EngineWithUpdates:
            def __init__(self):
                self.store = DummyStore()
                self.clients = {good, bad}

            def pump_once(self):
                calls["n"] += 1
                return calls["n"] == 1

        app = {"engine": EngineWithUpdates()}
        sleep_mock = mock.AsyncMock(side_effect=asyncio.CancelledError())
        with mock.patch("asyncio.sleep", sleep_mock):
            with self.assertRaises(asyncio.CancelledError):
                await dash.broadcaster(app)

        self.assertEqual(len(good.messages), 1)
        self.assertNotIn(bad, app["engine"].clients)


class TestKpiDashboardCoreMainEntrypoint(unittest.TestCase):
    def test_main_no_files_prints_and_returns(self):
        args = SimpleNamespace(
            log=None,
            glob=None,
            host="127.0.0.1",
            port=8787,
            from_start=False,
            code_font=12,
        )
        with (
            mock.patch.object(dash.argparse.ArgumentParser, "parse_args", return_value=args),
            mock.patch.object(dash, "build_file_list", return_value=[]),
            mock.patch("builtins.print") as print_mock,
        ):
            dash.main()

        printed = "\n".join(" ".join(str(x) for x in call.args) for call in print_mock.call_args_list)
        self.assertIn("No log files found.", printed)

    def test_main_builds_app_and_invokes_run_app(self):
        with tempfile.TemporaryDirectory() as td:
            log_file = Path(td) / "check.ST1_ComponentKitting.log"
            log_file.write_text("", encoding="utf-8")

            args = SimpleNamespace(
                log=[str(log_file)],
                glob=None,
                host="127.0.0.1",
                port=8899,
                from_start=True,
                code_font=15,
            )

            class FakeRouter:
                def __init__(self):
                    self.routes = []

                def add_get(self, path, handler):
                    self.routes.append((path, handler))

            class FakeApp(dict):
                def __init__(self):
                    super().__init__()
                    self.router = FakeRouter()
                    self.on_startup = []
                    self.on_cleanup = []

            class FakeEngine:
                last = None

                def __init__(self, files, from_start):
                    self.files = files
                    self.from_start = from_start
                    self.started = False
                    self.stopped = False
                    self.clients = set()
                    self.store = SimpleNamespace(
                        export_payload=lambda: {"items": [], "summary": {"stations": 0}}
                    )
                    FakeEngine.last = self

                def start(self):
                    self.started = True

                def stop(self):
                    self.stopped = True

                def pump_once(self):
                    return False

            fake_app = FakeApp()
            run_call = {}

            def fake_run_app(app, host, port):
                run_call["app"] = app
                run_call["host"] = host
                run_call["port"] = port

                async def cycle():
                    for cb in app.on_startup:
                        await cb(app)
                    for cb in app.on_cleanup:
                        await cb(app)

                asyncio.run(cycle())

            with (
                mock.patch.object(dash.argparse.ArgumentParser, "parse_args", return_value=args),
                mock.patch.object(dash, "build_file_list", return_value=[str(log_file)]),
                mock.patch.object(dash, "Engine", FakeEngine),
                mock.patch.object(dash.web, "Application", return_value=fake_app),
                mock.patch.object(dash.web, "run_app", side_effect=fake_run_app),
                mock.patch("builtins.print"),
            ):
                dash.main()

            self.assertEqual(run_call["host"], "127.0.0.1")
            self.assertEqual(run_call["port"], 8899)
            self.assertIs(run_call["app"], fake_app)
            self.assertIn(("/", dash.index), fake_app.router.routes)
            self.assertIn(("/api/payload", dash.api_payload), fake_app.router.routes)
            self.assertIn(("/ws", dash.ws_handler), fake_app.router.routes)
            self.assertIn("__INITIAL_PAYLOAD__", fake_app["html_page"])
            self.assertIn("15", fake_app["html_page"])
            self.assertTrue(FakeEngine.last.started)
            self.assertTrue(FakeEngine.last.stopped)
            self.assertEqual(FakeEngine.last.files, [str(log_file)])
            self.assertTrue(FakeEngine.last.from_start)


if __name__ == "__main__":
    unittest.main(verbosity=2)
