"""系统监控服务：后台线程每秒采样 CPU/内存/网络，Qt 信号分发到 UI。"""
import sys
import threading
import time

import psutil
from PySide6.QtCore import QObject, Signal


def windows_tcp_count(api=None):
    """TCP connections in every state but LISTEN, from Windows' TCP
    statistics (IPv4 + IPv6): one call however many connections there are.
    None when the statistics cannot be read."""
    import ctypes
    from ctypes import wintypes

    class MIB_TCPSTATS(ctypes.Structure):
        _fields_ = [(name, wintypes.DWORD) for name in (
            "dwRtoAlgorithm", "dwRtoMin", "dwRtoMax", "dwMaxConn", "dwActiveOpens",
            "dwPassiveOpens", "dwAttemptFails", "dwEstabResets", "dwCurrEstab",
            "dwInSegs", "dwOutSegs", "dwRetransSegs", "dwInErrs", "dwOutRsts", "dwNumConns")]

    api = api if api is not None else ctypes.windll.iphlpapi
    total = 0
    for family in (2, 23):                     # AF_INET, AF_INET6
        stats = MIB_TCPSTATS()
        if api.GetTcpStatisticsEx(ctypes.byref(stats), family) != 0:
            if family == 2:
                return None
            continue                           # no IPv6 stack
        total += stats.dwNumConns
    return total


def linux_tcp_count(root="/proc"):
    """TCP sockets of every state from /proc/net/sockstat, without listing them."""
    counts = {}
    for name in ("net/sockstat", "net/sockstat6"):
        try:
            with open(f"{root}/{name}", encoding="ascii", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        for line in text.splitlines():
            label, _sep, rest = line.partition(":")
            words = rest.split()
            counts[label.strip()] = dict(zip(words[::2], words[1::2]))
    if "TCP" not in counts:
        return None
    tcp = counts["TCP"]
    return (int(tcp.get("inuse", 0)) + int(tcp.get("tw", 0))
            + int(counts.get("TCP6", {}).get("inuse", 0)))


def fast_tcp_counter():
    if sys.platform == "win32":
        return windows_tcp_count
    if sys.platform.startswith("linux"):
        return linux_tcp_count
    return None


class TcpCounter:
    """Counts TCP connections without stalling the app.

    psutil.net_connections() builds an object for every connection while
    holding the GIL.  A heavy test leaves tens of thousands of connections
    in TIME_WAIT for minutes, so counting that way every second made the
    whole app stutter long after the test.  The system's own totals are read
    instead; when only the slow way works and it is slow, it is used less
    often.
    """

    SLOW = 0.15            # seconds; a count this slow is taken less often
    SLOW_INTERVAL = 10.0   # seconds between slow counts

    def __init__(self, fast=None, clock=time.monotonic):
        self._fast = fast_tcp_counter() if fast is None else fast
        self._clock = clock
        self._value = -1
        self._next = 0.0

    def count(self) -> int:
        start = self._clock()
        if start < self._next:
            return self._value
        value = None
        if self._fast:
            try:
                value = self._fast()
            except Exception:
                value = None
        if value is None:
            try:
                value = len(psutil.net_connections(kind="tcp"))
            except Exception:
                value = -1
        took = self._clock() - start
        self._next = start + self.SLOW_INTERVAL if took > self.SLOW else 0.0
        self._value = int(value)
        return self._value


class MonitorService(QObject):
    updated = Signal(dict)  # cpu, mem_percent, mem_used_gb, mem_total_gb, down_kbs, up_kbs, tcp_conns

    def __init__(self):
        super().__init__()
        self._stop = threading.Event()
        self._thread = None
        self.tcp = TcpCounter()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        psutil.cpu_percent(None)  # 首次调用初始化
        last_net = psutil.net_io_counters()
        last_t = time.time()
        while not self._stop.is_set():
            try:
                cpu = psutil.cpu_percent(1.0)
                vm = psutil.virtual_memory()
                net = psutil.net_io_counters()
                now = time.time()
                dt = max(now - last_t, 1e-6)
                down = (net.bytes_recv - last_net.bytes_recv) / dt / 1024
                up = (net.bytes_sent - last_net.bytes_sent) / dt / 1024
                tcp = self.tcp.count()
                self.updated.emit({
                    "cpu": cpu,
                    "mem_percent": vm.percent,
                    "mem_used_gb": vm.used / 1024 ** 3,
                    "mem_total_gb": vm.total / 1024 ** 3,
                    "down_kbs": max(0.0, down),
                    "up_kbs": max(0.0, up),
                    "tcp_conns": tcp,
                })
                last_net, last_t = net, now
            except Exception:
                self._stop.wait(1.0)


monitor = MonitorService()
