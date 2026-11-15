"""The native-engine bridge, exercised with a fake engine (no Rust, no network).

A tiny Python script stands in for the ``flowbench-engine`` binary and speaks
the stdin/stdout JSON protocol from ENGINE.md. ``subprocess.Popen`` is patched
to launch it with the current interpreter, so these tests run anywhere.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from agent import engine_bridge
from agent.engine_bridge import RustStressJob, find_engine_binary, make_stress_job

GOOD_ENGINE = r'''
import json, sys
def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n"); sys.stdout.flush()
emit({"type": "hello", "engine": "fake", "version": "9.9.9", "protocol": 1})
cfg = json.loads(sys.stdin.readline())["config"]
emit({"type": "snapshot", "running": True, "total": 1, "success": 1, "fail": 0,
      "tx": 10, "qps": 1.0, "avg": 2.0, "active": 1, "progress": 0.5, "last_error": ""})
emit({"type": "report", "target": cfg.get("target", ""), "protocol": cfg.get("protocol", ""),
      "duration": 1.0, "total": 2, "success": 2, "fail": 0, "avg": 2.0, "p50": 2.0,
      "p90": 2.0, "p99": 2.0, "traffic_mb": 0.0, "bytes_tx": 10,
      "rate_limit": cfg.get("rate", 0), "errors": {}})
'''

BAD_PROTOCOL_ENGINE = r'''
import json, sys
sys.stdout.write(json.dumps({"type": "hello", "engine": "fake", "version": "9", "protocol": 999}) + "\n")
sys.stdout.flush()
sys.stdin.readline()
'''

CRASH_ENGINE = r'''
import json, sys
sys.stdout.write(json.dumps({"type": "hello", "engine": "fake", "version": "9", "protocol": 1}) + "\n")
sys.stdout.flush()
sys.stdin.readline()   # read the start command, then exit without a report
'''

_real_popen = subprocess.Popen


def _py_popen(args, **kwargs):
    """Launch the fake engine script with the current Python interpreter."""
    return _real_popen([sys.executable] + list(args), **kwargs)


class _DummyWorker:
    def __init__(self, *args):
        self.running = False

    def start(self):
        self.running = True
        return True

    def stop(self):
        self.running = False


class RustStressJobTests(unittest.TestCase):
    def _script(self, text):
        handle = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8")
        handle.write(text)
        handle.close()
        self.addCleanup(lambda: os.path.exists(handle.name) and os.unlink(handle.name))
        return handle.name

    def _run(self, text, config=None):
        snaps, reports = [], []
        done = threading.Event()

        def on_report(report):
            reports.append(report)
            done.set()

        job = RustStressJob(
            config or {"target": "example.test", "protocol": "HTTP", "rate": 5},
            snaps.append, on_report, self._script(text),
        )
        with mock.patch.object(engine_bridge.subprocess, "Popen", _py_popen):
            started = job.start()
        return job, started, snaps, reports, done

    def test_known_protocol_streams_snapshots_and_report(self):
        job, started, snaps, reports, done = self._run(GOOD_ENGINE)
        self.assertTrue(started)
        self.assertTrue(done.wait(timeout=10), "no report arrived")
        self.assertEqual(reports[0]["target"], "example.test")
        self.assertEqual(reports[0]["errors"], {})
        self.assertGreaterEqual(len(snaps), 1)
        self.assertEqual(job.engine_version, "9.9.9")
        self.assertFalse(job.running)

    def test_unknown_protocol_is_refused_without_reporting(self):
        job, started, snaps, reports, done = self._run(BAD_PROTOCOL_ENGINE)
        self.assertFalse(started)
        self.assertFalse(done.wait(timeout=2))   # a refused engine never reports
        self.assertEqual(reports, [])
        self.assertFalse(job.running)

    def test_engine_exit_without_report_is_synthesized(self):
        job, started, snaps, reports, done = self._run(CRASH_ENGINE)
        self.assertTrue(started)
        self.assertTrue(done.wait(timeout=10), "no synthesized report")
        self.assertEqual(reports[0]["errors"], {"engine": 1})
        self.assertIn("engine_error", reports[0])
        self.assertFalse(job.running)


class EngineDiscoveryTests(unittest.TestCase):
    def test_env_override_wins(self):
        path = tempfile.NamedTemporaryFile(suffix=".bin", delete=False)
        path.close()
        self.addCleanup(lambda: os.unlink(path.name))
        with mock.patch.dict(os.environ, {"FLOWBENCH_ENGINE": path.name}):
            self.assertEqual(find_engine_binary({}), path.name)

    def test_missing_binary_returns_none(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(find_engine_binary({"engine_path": "/no/such/engine"}))


class EngineSelectionTests(unittest.TestCase):
    def _script(self, text):
        handle = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8")
        handle.write(text)
        handle.close()
        self.addCleanup(lambda: os.path.exists(handle.name) and os.unlink(handle.name))
        return handle.name

    def test_python_mode_never_touches_the_native_engine(self):
        with mock.patch.object(engine_bridge, "HeadlessStressJob", _DummyWorker), \
             mock.patch.object(engine_bridge, "find_engine_binary",
                               side_effect=AssertionError("python mode must not look for a binary")):
            job = make_stress_job({"target": "x"}, lambda d: None, lambda d: None, {"engine": "python"})
            self.assertTrue(job.start())
            self.assertTrue(job.running)
            self.assertIsInstance(job._impl, _DummyWorker)

    def test_rust_mode_without_binary_fails_and_does_not_fall_back(self):
        with mock.patch.object(engine_bridge, "find_engine_binary", return_value=None), \
             mock.patch.object(engine_bridge, "HeadlessStressJob",
                               side_effect=AssertionError("rust mode must not fall back")):
            job = make_stress_job({"target": "x"}, lambda d: None, lambda d: None, {"engine": "rust"})
            self.assertFalse(job.start())
            self.assertFalse(job.running)

    def test_auto_mode_prefers_the_native_engine(self):
        good = self._script(GOOD_ENGINE)
        with mock.patch.object(engine_bridge.subprocess, "Popen", _py_popen), \
             mock.patch.object(engine_bridge, "find_engine_binary", return_value=good), \
             mock.patch.object(engine_bridge, "HeadlessStressJob", _DummyWorker):
            job = make_stress_job({"target": "example.test", "protocol": "HTTP", "rate": 5},
                                  lambda d: None, lambda d: None, {"engine": "auto"})
            self.assertTrue(job.start())
            self.assertIsInstance(job._impl, RustStressJob)

    def test_auto_mode_falls_back_when_the_native_handshake_fails(self):
        bad = self._script(BAD_PROTOCOL_ENGINE)
        with mock.patch.object(engine_bridge.subprocess, "Popen", _py_popen), \
             mock.patch.object(engine_bridge, "find_engine_binary", return_value=bad), \
             mock.patch.object(engine_bridge, "HeadlessStressJob", _DummyWorker):
            job = make_stress_job({"target": "x", "protocol": "HTTP", "rate": 5},
                                  lambda d: None, lambda d: None, {"engine": "auto"})
            self.assertTrue(job.start())
            self.assertIsInstance(job._impl, _DummyWorker)


if __name__ == "__main__":
    unittest.main()
