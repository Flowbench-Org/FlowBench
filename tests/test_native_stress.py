"""Desktop local stress runs on the native engine (with Python fallback).

Uses a fake engine script in place of the Rust binary (no Rust, no network),
and drives a real Qt event loop so the queued snapshot/report signals arrive.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication

from agent import engine_bridge
from app.services import stress

GOOD_ENGINE = r'''
import json, sys
def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n"); sys.stdout.flush()
emit({"type": "hello", "engine": "fake", "version": "9.9.9", "protocol": 1})
cfg = json.loads(sys.stdin.readline())["config"]
emit({"type": "snapshot", "running": True, "total": 3, "success": 3, "fail": 0,
      "tx": 30, "qps": 3.0, "avg": 1.5, "active": 1, "progress": 0.5, "last_error": ""})
emit({"type": "report", "target": cfg.get("target", ""), "protocol": cfg.get("protocol", ""),
      "duration": 1.0, "total": 5, "success": 5, "fail": 0, "avg": 1.5, "p50": 1.5,
      "p90": 2.0, "p99": 2.0, "traffic_mb": 0.0, "bytes_tx": 30,
      "rate_limit": cfg.get("rate", 0), "errors": {}})
'''

_real_popen = subprocess.Popen


def _py_popen(args, **kwargs):
    return _real_popen([sys.executable] + list(args), **kwargs)


class _DummyStressEngine(QObject):
    snapshot = Signal(dict)
    report_ready = Signal(dict)
    ready = Signal()

    def __init__(self):
        super().__init__()
        self.started_with = None
        self.stopped = False

    def start(self, config):
        self.started_with = config
        QTimer.singleShot(0, self.ready.emit)
        QTimer.singleShot(0, lambda: self.report_ready.emit({"target": "x", "fallback": True}))
        return True

    def stop(self):
        self.stopped = True


class _QtTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def pump_until(self, predicate, timeout=10.0):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        return predicate()

    def script(self, text):
        handle = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8")
        handle.write(text)
        handle.close()
        self.addCleanup(lambda: os.path.exists(handle.name) and os.unlink(handle.name))
        return handle.name


class FactorySelectionTests(_QtTestCase):
    def test_non_native_protocols_use_the_python_engine(self):
        for proto in ("UDP", "ICMP", "MY_PLUGIN_PROTO"):
            engine = stress.make_child_engine({"protocol": proto})
            self.assertIsInstance(engine, stress.StressEngine)

    def test_native_protocols_use_the_adaptive_engine_in_auto(self):
        with mock.patch.object(stress, "_engine_mode", return_value="auto"):
            for proto in ("HTTP", "HTTPS", "TCP", "tcp"):
                engine = stress.make_child_engine({"protocol": proto})
                self.assertIsInstance(engine, stress._AdaptiveStressEngine)

    def test_python_mode_forces_the_python_engine(self):
        with mock.patch.object(stress, "_engine_mode", return_value="python"):
            engine = stress.make_child_engine({"protocol": "HTTP"})
            self.assertIsInstance(engine, stress.StressEngine)


class NativeConfigTests(unittest.TestCase):
    BASE = {"target": "h.test", "protocol": "HTTP", "url": "http://h.test/", "port": 80,
            "threads": 2, "rate": 5, "duration": 3, "packet_size": 64, "timeout": 2000}

    def test_valid_config_passes_the_gate(self):
        self.assertTrue(stress._valid_native_config(self.BASE))
        self.assertTrue(stress._valid_native_config({**self.BASE, "protocol": "TCP", "port": 9000}))

    def test_bad_config_is_rejected(self):
        self.assertFalse(stress._valid_native_config({**self.BASE, "rate": 0}))
        self.assertFalse(stress._valid_native_config({**self.BASE, "threads": True}))
        self.assertFalse(stress._valid_native_config({**self.BASE, "protocol": "HTTP", "url": ""}))
        self.assertFalse(stress._valid_native_config({**self.BASE, "protocol": "TCP", "port": 0}))
        self.assertFalse(stress._valid_native_config({k: v for k, v in self.BASE.items() if k != "timeout"}))

    def test_native_config_shape(self):
        cfg = stress._native_config({**self.BASE, "protocol": "TCP", "port": "9000", "timeout": 2500.0})
        self.assertEqual(cfg["port"], 9000)
        self.assertEqual(cfg["timeout"], 2500)
        self.assertIsNone(cfg["headers"])
        self.assertEqual(set(cfg), {"target", "protocol", "url", "port", "rate",
                                    "duration", "threads", "packet_size", "timeout", "headers"})


class AdaptiveEngineTests(_QtTestCase):
    CONFIG = {"target": "h.test", "protocol": "HTTP", "url": "http://h.test/", "port": 80,
              "threads": 2, "rate": 5, "duration": 3, "packet_size": 64, "timeout": 2000}

    def test_native_path_streams_snapshots_and_a_report(self):
        good = self.script(GOOD_ENGINE)
        snaps, reports = [], []
        engine = stress._AdaptiveStressEngine()
        engine.snapshot.connect(snaps.append)
        engine.report_ready.connect(reports.append)
        with mock.patch.object(engine_bridge.subprocess, "Popen", _py_popen), \
             mock.patch.object(engine_bridge, "find_engine_binary", return_value=good):
            self.assertTrue(engine.start(self.CONFIG))
            self.assertTrue(self.pump_until(lambda: bool(reports)), "no report arrived")
        self.assertEqual(reports[0]["target"], "h.test")
        self.assertEqual(reports[0]["errors"], {})
        for key in ("p50", "p90", "p99", "bytes_tx", "duration", "rate_limit"):
            self.assertIn(key, reports[0])
        self.assertGreaterEqual(len(snaps), 1)
        self.assertFalse(engine.running)

    def test_falls_back_to_python_when_no_binary(self):
        got_ready, reports = [], []
        engine = stress._AdaptiveStressEngine()
        engine.ready.connect(lambda: got_ready.append(True))
        engine.report_ready.connect(reports.append)
        with mock.patch.object(engine_bridge, "find_engine_binary", return_value=None), \
             mock.patch.object(stress, "StressEngine", _DummyStressEngine):
            self.assertTrue(engine.start(self.CONFIG))
            self.assertIsInstance(engine._impl, _DummyStressEngine)
            self.assertTrue(self.pump_until(lambda: bool(reports)))
        self.assertTrue(reports[0].get("fallback"))
        self.assertTrue(got_ready)
        engine.stop()
        self.assertTrue(engine._impl.stopped)

    def test_require_native_fails_without_a_binary(self):
        engine = stress._AdaptiveStressEngine(require_native=True)
        with mock.patch.object(engine_bridge, "find_engine_binary", return_value=None), \
             mock.patch.object(stress, "StressEngine",
                               side_effect=AssertionError("must not fall back when require_native")):
            self.assertFalse(engine.start(self.CONFIG))
            self.assertFalse(engine.running)


if __name__ == "__main__":
    unittest.main()
