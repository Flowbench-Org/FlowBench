"""Bridge between the Python agent and the native (Rust) load engine.

The native engine (`engine/`, binary ``flowbench-engine``) runs the stress hot
path. The agent talks to it over stdin/stdout with newline-delimited JSON; the
protocol is documented in ``ENGINE.md``.

Only a job that already passed ``validate_job`` (authorization allow-list,
protocol allow-list and per-agent limits) is ever handed to the engine, so the
native path adds no new way to bypass those gates. :func:`make_stress_job`
chooses the engine and, in ``auto`` mode, falls back to the built-in Python
:class:`~agent.worker.HeadlessStressJob` when the native binary is missing or
fails to hand-shake.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import threading

from agent.worker import HeadlessStressJob

# Wire-protocol versions this bridge understands (the ``protocol`` field of the
# engine's ``hello`` line). Refuse an engine that speaks anything else.
KNOWN_PROTOCOLS = {1}

HANDSHAKE_TIMEOUT = 5.0
EXIT_TIMEOUT = 5.0


def _engine_names():
    if os.name == "nt":
        return ("flowbench-engine.exe", "flowbench-engine")
    return ("flowbench-engine",)


def find_engine_binary(config=None):
    """Locate the native engine binary, or return ``None``.

    Order: ``FLOWBENCH_ENGINE`` env var, the ``engine_path`` config key, next to
    the (frozen) executable, the PyInstaller bundle dir, then the
    ``engine/target/release`` build output or a copy at the repo root.
    """
    config = config or {}

    # Return absolute paths: a bare relative name would make Popen do a
    # PATH search on POSIX, silently missing a file next to the cwd.
    override = str(os.environ.get("FLOWBENCH_ENGINE") or "").strip()
    if override and os.path.isfile(override):
        return os.path.abspath(override)

    cfg_path = str(config.get("engine_path") or "").strip()
    if cfg_path and os.path.isfile(cfg_path):
        return os.path.abspath(cfg_path)

    dirs = []
    if getattr(sys, "frozen", False):
        dirs.append(os.path.dirname(os.path.abspath(sys.executable)))
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            dirs.append(meipass)
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(here)
    dirs.append(os.path.join(repo, "engine", "target", "release"))
    dirs.append(repo)

    seen = set()
    for directory in dirs:
        if not directory or directory in seen:
            continue
        seen.add(directory)
        for name in _engine_names():
            candidate = os.path.join(directory, name)
            if os.path.isfile(candidate):
                return candidate
    return None


def _spawn_kwargs():
    # Keep a child console window from flashing up on Windows.
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


def _engine_job(config):
    """A clean job object the engine's strict JSON parser accepts: known keys
    only, headers coerced to str->str, url never null, numbers coerced and
    finite. Keeps a job that passed validate_job (which preserves extra or
    oddly typed fields) from being rejected by the engine's serde parser.
    """
    config = config or {}

    def _int(value, default):
        if isinstance(value, bool):
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _float(value, default):
        if isinstance(value, bool):
            return default
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        return number if math.isfinite(number) else default

    headers = config.get("headers")
    clean_headers = ({str(k): str(v) for k, v in headers.items()}
                     if isinstance(headers, dict) else None)
    return {
        "target": str(config.get("target", "")),
        "protocol": str(config.get("protocol", "")).upper(),
        "url": str(config.get("url") or ""),
        "port": _int(config.get("port"), 0),
        "rate": _float(config.get("rate"), 0.0),
        "duration": _float(config.get("duration"), 0.0),
        "threads": _int(config.get("threads"), 1),
        "packet_size": _int(config.get("packet_size"), 1),
        "timeout": _int(config.get("timeout"), 5000),
        "headers": clean_headers,
    }


class RustStressJob:
    """Drive the native engine with the same interface as ``HeadlessStressJob``.

    ``start()`` returns ``False`` (without ever calling the report callback) if
    the engine cannot be spawned or does not hand-shake, so the caller can fall
    back. Once ``start()`` returns ``True`` the job behaves like the Python one:
    ``on_snapshot`` is called repeatedly and ``on_report`` exactly once.
    """

    def __init__(self, config, on_snapshot=None, on_report=None, binary=None):
        self.config = config
        self.on_snapshot = on_snapshot or (lambda data: None)
        self.on_report = on_report or (lambda data: None)
        self.binary = binary
        self.proc = None
        self.reader = None
        self.running = False
        self.engine_version = None
        self._reported = False
        self._job_sent = False
        self._lock = threading.Lock()
        self._hello = threading.Event()
        self._hello_ok = False
        self._last_snapshot = None

    def start(self):
        if not self.binary:
            return False
        try:
            self.proc = subprocess.Popen(
                [self.binary],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=1,
                text=True,
                encoding="utf-8",
                **_spawn_kwargs(),
            )
        except OSError:
            self.proc = None
            return False

        self.reader = threading.Thread(
            target=self._read_loop, daemon=True, name="flowbench-engine-reader"
        )
        self.reader.start()

        if not self._hello.wait(timeout=HANDSHAKE_TIMEOUT) or not self._hello_ok:
            self._terminate()
            return False

        try:
            command = json.dumps(
                {"type": "start", "config": _engine_job(self.config)},
                ensure_ascii=False, allow_nan=False,
            )
        except (ValueError, TypeError):
            self._terminate()
            return False

        # Commit the job before writing the start line, so a crash the
        # instant the engine reads it still yields a report from _finish
        # (which reports only when _job_sent is set). Roll back on failure.
        with self._lock:
            self._job_sent = True
        self.running = True
        try:
            self.proc.stdin.write(command + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError):
            with self._lock:
                already_reported = self._reported
                if not already_reported:
                    self._job_sent = False
                    self._reported = True  # suppress a synthesized report
            if already_reported:
                # The engine died the instant it got the job, and the reader
                # has already reported it as failed. The job ran and its one
                # report is out: falling back now would run it a second time,
                # with nothing left that can stop it.
                return True
            self.running = False
            self._terminate()
            return False
        return True

    def stop(self):
        proc = self.proc
        if not proc:
            return
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.write('{"type":"stop"}\n')
                proc.stdin.flush()
        except (OSError, ValueError):
            pass

    # -- internals ---------------------------------------------------------

    def _read_loop(self):
        proc = self.proc
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(message, dict):
                    continue
                kind = message.get("type")
                if kind == "hello":
                    self._hello_ok = message.get("protocol") in KNOWN_PROTOCOLS
                    self.engine_version = message.get("version")
                    self._hello.set()
                elif kind == "snapshot":
                    if self._reported:
                        continue  # ignore a snapshot that trails the report
                    message.pop("type", None)
                    self._last_snapshot = dict(message)
                    self.on_snapshot(message)
                elif kind == "report":
                    message.pop("type", None)
                    self._emit_report(message)
                elif kind == "error":
                    self._emit_report(
                        self._error_report(message.get("message", "engine error"))
                    )
                    break  # the engine will not run this job; stop reading
        except (OSError, ValueError):
            pass
        finally:
            # Never let start() block on the handshake if the engine died early.
            self._hello.set()
            self._finish()

    def _finish(self):
        proc = self.proc
        if proc is not None:
            # Close stdin so an engine still waiting on it (EOF == stop)
            # exits, and so the pipe fd is not leaked.
            try:
                if proc.stdin and not proc.stdin.closed:
                    proc.stdin.close()
            except OSError:
                pass
            try:
                proc.wait(timeout=EXIT_TIMEOUT)
            except Exception:
                self._terminate()
        # A crash with no report would otherwise hang the agent on a job that
        # never completes; synthesize a failed report instead. Only once the
        # job was actually sent, so a refused handshake never reports.
        if self._job_sent and not self._reported:
            self._emit_report(self._error_report("engine exited without a report"))

    def _emit_report(self, report):
        with self._lock:
            if self._reported:
                return
            self._reported = True
        self.running = False
        self.on_report(report)

    def _error_report(self, message):
        """A failed report for a job the engine did not finish. It keeps what
        the last snapshot had counted, so an engine killed part-way (OOM
        killer, antivirus) does not report a run that sent nothing."""
        cfg = self.config or {}
        snap = self._last_snapshot or {}

        def _count(key):
            value = snap.get(key)
            if isinstance(value, bool) or not isinstance(value, int):
                return 0
            return max(0, value)

        def _real(value):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return 0.0
            value = float(value)
            return value if math.isfinite(value) and value >= 0 else 0.0

        tx = _count("tx")
        duration = min(1.0, _real(snap.get("progress"))) * _real(cfg.get("duration"))
        return {
            "target": cfg.get("target", ""),
            "protocol": cfg.get("protocol", ""),
            "duration": duration,
            "total": _count("total"),
            "success": _count("success"),
            "fail": _count("fail"),
            "avg": _real(snap.get("avg")),
            "p50": 0.0,
            "p90": 0.0,
            "p99": 0.0,
            "traffic_mb": tx / 1024 / 1024,
            "bytes_tx": tx,
            "rate_limit": cfg.get("rate", 0),
            "errors": {"engine": 1},
            "engine_error": str(message),
        }

    def _terminate(self):
        proc = self.proc
        if not proc:
            return
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


class _StressJob:
    """Facade that picks the native engine or the Python worker at ``start()``.

    ``engine`` mode: ``auto`` (native if available, else Python), ``rust``
    (native only, fail if missing), ``python`` (always the built-in worker).
    """

    def __init__(self, job, on_snapshot, on_report, agent_config, mode):
        self._job = job
        self._on_snapshot = on_snapshot
        self._on_report = on_report
        self._agent_config = agent_config
        self._mode = mode
        self._impl = None

    @property
    def running(self):
        return bool(self._impl and getattr(self._impl, "running", False))

    def start(self):
        if self._mode in ("auto", "rust"):
            binary = find_engine_binary(self._agent_config)
            if binary:
                native = RustStressJob(self._job, self._on_snapshot, self._on_report, binary)
                if native.start():
                    self._impl = native
                    return True
            if self._mode == "rust":
                return False  # required but unavailable; do not fall back

        python = HeadlessStressJob(self._job, self._on_snapshot, self._on_report)
        if python.start():
            self._impl = python
            return True
        return False

    def stop(self):
        if self._impl:
            self._impl.stop()


def make_stress_job(job, on_snapshot, on_report, agent_config=None):
    agent_config = agent_config or {}
    mode = str(agent_config.get("engine") or "auto").strip().lower()
    if mode not in ("auto", "rust", "python"):
        mode = "auto"
    return _StressJob(job, on_snapshot, on_report, agent_config, mode)
