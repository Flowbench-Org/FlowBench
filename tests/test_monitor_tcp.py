"""Counting TCP connections for the Monitor page must stay cheap: a heavy
test leaves tens of thousands of connections in TIME_WAIT for minutes, and
listing them all every second made the whole app stutter."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import monitor as monitor_module
from app.services.monitor import TcpCounter, linux_tcp_count, windows_tcp_count


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class TcpCounterTests(unittest.TestCase):
    def test_uses_the_systems_total_without_listing(self):
        counter = TcpCounter(fast=lambda: 42)
        with patch.object(monitor_module.psutil, "net_connections",
                          side_effect=AssertionError("listed every connection")):
            self.assertEqual(counter.count(), 42)
            self.assertEqual(counter.count(), 42)

    def test_falls_back_to_listing(self):
        def broken():
            raise OSError("no statistics")
        for fast in (lambda: None, broken, False):
            with self.subTest(fast=fast), \
                    patch.object(monitor_module.psutil, "net_connections", return_value=[1, 2, 3]):
                self.assertEqual(TcpCounter(fast=fast).count(), 3)
        with patch.object(monitor_module.psutil, "net_connections", side_effect=PermissionError):
            self.assertEqual(TcpCounter(fast=False).count(), -1)

    def test_a_slow_count_is_taken_every_ten_seconds(self):
        clock = Clock()
        calls = []

        def slow():
            calls.append(clock.t)
            clock.t += 0.5            # took half a second
            return len(calls) * 1000
        counter = TcpCounter(fast=slow, clock=clock)
        self.assertEqual(counter.count(), 1000)
        for _ in range(8):            # one sample a second: the last count is reused
            clock.t += 1.0
            self.assertEqual(counter.count(), 1000)
        clock.t += 2.0
        self.assertEqual(counter.count(), 2000)
        self.assertEqual(len(calls), 2)

        quick = []
        counter = TcpCounter(fast=lambda: quick.append(1) or 5, clock=clock)
        for _ in range(3):
            clock.t += 1.0
            counter.count()
        self.assertEqual(len(quick), 3)          # fast: every second

    def test_windows_statistics(self):
        class Api:
            def __init__(self, counts):
                self.counts = counts

            def GetTcpStatisticsEx(self, ref, family):
                if family not in self.counts:
                    return 50                    # ERROR_NOT_SUPPORTED
                ref._obj.dwNumConns = self.counts[family]
                return 0
        self.assertEqual(windows_tcp_count(Api({2: 30000, 23: 12})), 30012)
        self.assertEqual(windows_tcp_count(Api({2: 30000})), 30000)    # no IPv6
        self.assertIsNone(windows_tcp_count(Api({23: 12})))

    def test_linux_sockstat(self):
        with tempfile.TemporaryDirectory() as root:
            net = Path(root) / "net"
            net.mkdir()
            (net / "sockstat").write_text(
                "sockets: used 300\nTCP: inuse 40 orphan 0 tw 25000 alloc 50 mem 4\nUDP: inuse 3 mem 1\n")
            (net / "sockstat6").write_text("TCP6: inuse 7\nUDP6: inuse 1\n")
            self.assertEqual(linux_tcp_count(root), 25047)
            (net / "sockstat").unlink()
            self.assertIsNone(linux_tcp_count(root))

    @unittest.skipUnless(sys.platform.startswith("linux"), "reads /proc")
    def test_linux_count_matches_psutil(self):
        import psutil
        ours = linux_tcp_count()
        theirs = len(psutil.net_connections(kind="tcp"))
        self.assertIsInstance(ours, int)
        self.assertLessEqual(abs(ours - theirs), max(20, theirs // 10))

    def test_monitor_service_uses_the_counter(self):
        service = monitor_module.MonitorService()
        self.assertIsInstance(service.tcp, TcpCounter)


if __name__ == "__main__":
    unittest.main()
