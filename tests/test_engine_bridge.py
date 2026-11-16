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
import time
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

LATE_SNAPSHOT_ENGINE = r'''
import json, sys
def emit(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
emit({"type": "hello", "engine": "fake", "version": "1", "protocol": 1})
cfg = json.loads(sys.stdin.readline())["config"]
emit({"type": "report", "target": cfg.get("target", ""), "protocol": cfg.get("protocol", ""),
      "duration": 1.0, "total": 1, "success": 1, "fail": 0, "avg": 1.0, "p50": 1.0,
      "p90": 1.0, "p99": 1.0, "traffic_mb": 0.0, "bytes_tx": 0, "rate_limit": 0, "errors": {}})
emit({"type": "snapshot", "running": True, "total": 99, "success": 99, "fail": 0,
      "tx": 0, "qps": 0.0, "avg": 0.0, "active": 0, "progress": 1.0, "last_error": ""})
'''

ERROR_THEN_WAIT_ENGINE = r'''
import json, sys
def emit(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
emit({"type": "hello", "engine": "fake", "version": "1", "protocol": 1})
sys.stdin.readline()
emit({"type": "error", "message": "bad command: boom"})
# Mimic the real engine: keep reading stdin, exit only on EOF (stdin closed).
while sys.stdin.readline() != "":
    pass
'''

SNAPSHOT_THEN_CRASH_ENGINE = r'''
import json, sys
def emit(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
emit({"type": "hello", "engine": "fake", "version": "1", "protocol": 1})
sys.stdin.readline()
emit({"type": "snapshot", "running": True, "total": 40, "success": 38, "fail": 2,
      "tx": 4000, "qps": 20.0, "avg": 3.5, "active": 2, "progress": 0.5, "last_error": "timeout"})
# ...then die without a report, as when the OOM killer or antivirus kills it.
'''

DIES_RIGHT_AFTER_HELLO_ENGINE = r'''
import json, sys, time
sys.stdout.write(json.dumps({"type": "hello", "engine": "fake", "version": "1", "protocol": 1}) + "\n")
sys.stdout.flush()
time.sleep(0.5)   # die before reading the start line
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

    def _wait_exit(self, proc, timeout=4.0):
        deadline = time.time() + timeout
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.02)
        return proc.poll()

    def test_engine_is_reaped_and_stdin_closed_after_a_report(self):
        job, started, snaps, reports, done = self._run(GOOD_ENGINE)
        self.assertTrue(started)
        self.assertTrue(done.wait(timeout=10))
        self.assertIsNotNone(self._wait_exit(job.proc), "engine not reaped after report")
        self.assertTrue(job.proc.stdin.closed)

    def test_snapshot_after_report_is_dropped(self):
        job, started, snaps, reports, done = self._run(LATE_SNAPSHOT_ENGINE)
        self.assertTrue(started)
        self.assertTrue(done.wait(timeout=10))
        self._wait_exit(job.proc)
        time.sleep(0.2)   # let the reader process the trailing snapshot line
        self.assertEqual(snaps, [])

    def test_error_event_reports_and_reaps_the_engine(self):
        job, started, snaps, reports, done = self._run(ERROR_THEN_WAIT_ENGINE)
        self.assertTrue(started)
        self.assertTrue(done.wait(timeout=10))
        self.assertEqual(reports[0]["errors"], {"engine": 1})
        self.assertIsNotNone(self._wait_exit(job.proc), "engine not reaped after an error event")

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

    def test_crash_report_keeps_what_the_last_snapshot_counted(self):
        job, started, snaps, reports, done = self._run(
            SNAPSHOT_THEN_CRASH_ENGINE,
            {"target": "example.test", "protocol": "HTTP", "rate": 5, "duration": 10})
        self.assertTrue(started)
        self.assertTrue(done.wait(timeout=10), "no synthesized report")
        report = reports[0]
        self.assertEqual((report["total"], report["success"], report["fail"]), (40, 38, 2))
        self.assertEqual(report["bytes_tx"], 4000)
        self.assertAlmostEqual(report["duration"], 5.0)
        self.assertEqual(report["errors"], {"engine": 1})


class EngineDiscoveryTests(unittest.TestCase):
    def test_env_override_wins(self):
        path = tempfile.NamedTemporaryFile(suffix=".bin", delete=False)
        path.close()
        self.addCleanup(lambda: os.unlink(path.name))
        with mock.patch.dict(os.environ, {"FLOWBENCH_ENGINE": path.name}):
            self.assertEqual(find_engine_binary({}), path.name)

    def test_missing_binary_returns_none(self):
        # Search a checkout where nothing is built: an engine built in this
        # one (engine/target/release) must not answer for a missing binary.
        with tempfile.TemporaryDirectory() as empty, \
             mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch.object(engine_bridge, "__file__",
                               os.path.join(empty, "agent", "engine_bridge.py")):
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

    def test_engine_dying_as_it_takes_the_job_reports_once_and_never_falls_back(self):
        """The reader reports the dead engine's job while start() is still
        writing the start line. start() must not then fall back and run the
        job a second time, unowned and unstoppable."""
        reports, python_jobs = [], []

        class _FailingStdin:
            def __init__(self, real):
                self.real = real

            @property
            def closed(self):
                return self.real.closed

            def write(self, data):
                # Hold the write until the reader has seen the engine exit and
                # reported the job, then fail like the closed pipe does.
                deadline = time.time() + 10
                while not reports and time.time() < deadline:
                    time.sleep(0.02)
                raise ValueError("I/O operation on closed file")

            def flush(self):
                pass

            def close(self):
                self.real.close()

        def popen(args, **kwargs):
            proc = _py_popen(args, **kwargs)
            proc.stdin = _FailingStdin(proc.stdin)
            return proc

        class _RecordingWorker(_DummyWorker):
            def start(self):
                python_jobs.append(self)
                return super().start()

        dying = self._script(DIES_RIGHT_AFTER_HELLO_ENGINE)
        with mock.patch.object(engine_bridge.subprocess, "Popen", popen), \
             mock.patch.object(engine_bridge, "find_engine_binary", return_value=dying), \
             mock.patch.object(engine_bridge, "HeadlessStressJob", _RecordingWorker):
            job = make_stress_job({"target": "example.test", "protocol": "HTTP", "rate": 5},
                                  lambda d: None, reports.append, {"engine": "auto"})
            self.assertTrue(job.start())
        self.assertEqual(python_jobs, [], "fell back to Python after the job was reported")
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["errors"], {"engine": 1})
        self.assertIsInstance(job._impl, RustStressJob)
        self.assertFalse(job.running)


class EngineJobSanitizerTests(unittest.TestCase):
    def test_coerces_headers_drops_junk_and_stays_serializable(self):
        from agent.engine_bridge import _engine_job
        job = _engine_job({
            "target": "h", "protocol": "http", "url": None, "port": "80",
            "rate": 5, "duration": 3, "threads": 2, "packet_size": 64, "timeout": 2000.0,
            "headers": {"X-Retry": 3, "A": "b"}, "junk": object(), "extra": float("nan"),
        })
        self.assertEqual(set(job), {"target", "protocol", "url", "port", "rate",
                                    "duration", "threads", "packet_size", "timeout", "headers"})
        self.assertEqual(job["protocol"], "HTTP")
        self.assertEqual(job["url"], "")
        self.assertEqual(job["port"], 80)
        self.assertEqual(job["timeout"], 2000)
        self.assertEqual(job["headers"], {"X-Retry": "3", "A": "b"})
        json.dumps(job, allow_nan=False)   # must not raise

    def test_non_finite_and_bool_numbers_fall_back(self):
        from agent.engine_bridge import _engine_job
        job = _engine_job({"rate": float("nan"), "duration": float("inf"), "threads": True})
        self.assertEqual(job["rate"], 0.0)
        self.assertEqual(job["duration"], 0.0)
        self.assertEqual(job["threads"], 1)
        json.dumps(job, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
