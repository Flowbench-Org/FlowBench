"""System metrics a server agent sends with its heartbeat.

The desktop's Monitor page shows them the same way as the local machine's:
CPU, memory, network throughput, TCP connections and processes.

Linux is read straight from /proc, so a Linux server needs no extra package
and no root. Elsewhere (the Windows agent) psutil is used when it is present.
Sampling runs in its own thread once a second; every heartbeat carries the
last few samples, so the desktop keeps one point per second even when a
heartbeat or a poll is missed.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from collections import deque

SCHEMA = 1
SAMPLE_INTERVAL = 1.0     # seconds between samples, like the local monitor
HISTORY = 15              # samples sent with every heartbeat (the last 15 s)


def _is_loopback(name) -> bool:
    lowered = str(name).strip().lower()
    return lowered in {"lo", "lo0"} or lowered.startswith("loopback")


class ProcReader:
    """Reads Linux counters from /proc without any third-party package."""

    source = "proc"

    def __init__(self, root: str = "/proc"):
        self.root = root
        self._cpu = None

    def _read(self, name: str) -> str:
        with open(os.path.join(self.root, name), encoding="ascii", errors="replace") as fh:
            return fh.read()

    def cpu_percent(self):
        """Busy share of all CPUs since the previous call; None on the first call."""
        values = [int(x) for x in self._read("stat").splitlines()[0].split()[1:]]
        # user nice system idle iowait irq softirq steal [guest guest_nice];
        # guest time is already part of user and nice.
        total = sum(values[:8])
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        previous, self._cpu = self._cpu, (total, idle)
        if previous is None or total <= previous[0]:
            return None
        spent = total - previous[0]
        busy = spent - max(0, idle - previous[1])
        return max(0.0, min(100.0, 100.0 * busy / spent))

    def memory(self):
        """(used, total) in bytes; used is everything not available to new programs."""
        info = {}
        for line in self._read("meminfo").splitlines():
            key, _sep, rest = line.partition(":")
            parts = rest.split()
            if parts and parts[0].isdigit():
                info[key.strip()] = int(parts[0]) * 1024
        total = info.get("MemTotal", 0)
        available = info.get("MemAvailable")
        if available is None:     # kernels before 3.14
            available = info.get("MemFree", 0) + info.get("Buffers", 0) + info.get("Cached", 0)
        return max(0, total - available), total

    def net_bytes(self):
        """Bytes received and sent by all interfaces except loopback."""
        rx = tx = 0
        for line in self._read("net/dev").splitlines()[2:]:
            name, sep, data = line.partition(":")
            fields = data.split()
            if not sep or _is_loopback(name) or len(fields) < 9:
                continue
            rx += int(fields[0])
            tx += int(fields[8])
        return rx, tx

    def tcp_connections(self) -> int:
        """TCP sockets of every state, like psutil.net_connections(kind="tcp")."""
        try:
            counts = {}
            for name in ("net/sockstat", "net/sockstat6"):
                for line in self._read(name).splitlines():
                    label, _sep, rest = line.partition(":")
                    words = rest.split()
                    counts[label.strip()] = dict(zip(words[::2], words[1::2]))
            tcp = counts["TCP"]
            return (int(tcp["inuse"]) + int(tcp.get("tw", 0))
                    + int(counts.get("TCP6", {}).get("inuse", 0)))
        except (OSError, KeyError, ValueError):
            pass
        # Without sockstat, count the socket tables (slower with many sockets).
        count, found = 0, False
        for name in ("net/tcp", "net/tcp6"):
            try:
                lines = self._read(name).splitlines()
            except OSError:
                continue
            found = True
            count += max(0, len(lines) - 1)
        return count if found else -1

    def process_count(self) -> int:
        return sum(1 for entry in os.listdir(self.root) if entry.isdigit())


class PsutilReader:
    """The same counters through psutil, for Windows and macOS agents."""

    source = "psutil"

    def __init__(self, psutil_module):
        self.psutil = psutil_module
        self.psutil.cpu_percent(None)     # the next call measures from here

    def cpu_percent(self):
        return float(self.psutil.cpu_percent(None))

    def memory(self):
        vm = self.psutil.virtual_memory()
        return max(0, vm.total - vm.available), vm.total

    def net_bytes(self):
        rx = tx = 0
        for name, counters in self.psutil.net_io_counters(pernic=True).items():
            if not _is_loopback(name):
                rx += counters.bytes_recv
                tx += counters.bytes_sent
        return rx, tx

    def tcp_connections(self) -> int:
        try:
            return len(self.psutil.net_connections(kind="tcp"))
        except Exception:      # AccessDenied on macOS without root
            return -1

    def process_count(self) -> int:
        try:
            return len(self.psutil.pids())
        except Exception:
            return -1


def default_reader():
    """The best source for this machine, or None when nothing can be read."""
    if sys.platform.startswith("linux") and os.path.isfile("/proc/stat"):
        return ProcReader()
    try:
        import psutil
    except ImportError:
        return None
    return PsutilReader(psutil)


class SystemSampler:
    """Samples the machine once a second and keeps the last HISTORY samples."""

    def __init__(self, reader=None, interval=SAMPLE_INTERVAL, history=HISTORY,
                 clock=time.time, monotonic=time.monotonic):
        self.reader = reader
        self.interval = float(interval)
        self._clock = clock
        self._monotonic = monotonic
        self._samples = deque(maxlen=max(1, int(history)))
        self._net = None          # (monotonic time, bytes received, bytes sent)
        self._error = ""
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    @classmethod
    def create(cls, **kwargs):
        """A sampler for this machine. Metrics must never stop the agent itself."""
        try:
            reader = default_reader()
        except Exception:
            reader = None
        return cls(reader, **kwargs)

    def sample_once(self):
        """Take one sample. The first call only sets the baseline and returns None."""
        if self.reader is None:
            return None
        try:
            cpu = self.reader.cpu_percent()
            used, total = self.reader.memory()
            rx, tx = self.reader.net_bytes()
            tcp = int(self.reader.tcp_connections())
            processes = int(self.reader.process_count())
        except Exception as exc:
            with self._lock:
                self._error = f"{type(exc).__name__}: {exc}"[:200]
            return None
        now = self._monotonic()
        previous, self._net = self._net, (now, rx, tx)
        if previous is None or cpu is None or now <= previous[0]:
            return None
        elapsed = now - previous[0]
        sample = {
            "ts": round(float(self._clock()), 3),
            "cpu": round(float(cpu), 1),
            "mem_percent": round(100.0 * used / total, 1) if total else 0.0,
            "mem_used_gb": round(used / 1024 ** 3, 3),
            "mem_total_gb": round(total / 1024 ** 3, 3),
            # A counter that went backwards (an interface went away) counts as no traffic.
            "down_kbs": round(max(0, rx - previous[1]) / elapsed / 1024, 2),
            "up_kbs": round(max(0, tx - previous[2]) / elapsed / 1024, 2),
            "tcp_conns": tcp,
            "proc_count": processes,
        }
        with self._lock:
            self._samples.append(sample)
            self._error = ""
        return sample

    def snapshot(self) -> dict:
        """What the heartbeat carries."""
        with self._lock:
            if self.reader is None:
                return {"v": SCHEMA, "available": False,
                        "error": "no system metrics source on this machine (psutil is not installed)"}
            data = {"v": SCHEMA, "available": True, "source": self.reader.source,
                    "interval": self.interval, "samples": list(self._samples)}
            if self._error:
                data["error"] = self._error
            return data

    def start(self):
        if self.reader is not None and not (self._thread and self._thread.is_alive()):
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="flowbench-metrics",
                                            daemon=True)
            self._thread.start()
        return self

    def stop(self):
        self._stop.set()

    def _run(self):
        due = time.monotonic()
        while not self._stop.is_set():
            self.sample_once()
            due += self.interval
            delay = due - time.monotonic()
            if delay < 0:          # fell behind (a suspended VM): do not catch up in a burst
                due = time.monotonic()
                delay = 0
            self._stop.wait(delay)
