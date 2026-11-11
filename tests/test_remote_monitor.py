"""The Monitor page shows a server agent's CPU, memory and network like this PC's."""
import copy
import csv
import os
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("APPDATA", os.path.join(tempfile.gettempdir(), "FlowBench-remote-monitor-tests"))

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.services import remote_monitor as remote_module
from app.services.remote_monitor import RemoteMonitor, normalize_sample, remote_monitor
from app.services.settings import settings

BASE = 1_800_000_000.0


def raw(ts, cpu=20.0, **extra):
    sample = {"ts": BASE + ts, "cpu": cpu, "mem_percent": 50.0, "mem_used_gb": 2.0,
              "mem_total_gb": 4.0, "down_kbs": 100.0 + ts, "up_kbs": 10.0, "tcp_conns": 30,
              "proc_count": 120}
    sample.update(extra)
    return sample


def agent(agent_id="a1", name="东京-1", online=True, samples=None, event=None, hostname="tokyo-1"):
    if event is None:
        event = {"state": "idle"}
        if samples is not None:
            event["system"] = {"v": 1, "available": True, "source": "proc",
                               "interval": 1.0, "samples": samples}
    return {"agent_id": agent_id, "name": name, "online": online,
            "metadata": {"hostname": hostname}, "last_event": event}


def wait_until(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        QTest.qWait(20)
    return condition()


class RemoteMonitorTests(unittest.TestCase):
    def test_overlapping_heartbeats_become_one_sample_per_second(self):
        monitor = RemoteMonitor()
        monitor.ingest([agent(samples=[raw(t) for t in range(1, 16)])])
        monitor.ingest([agent(samples=[raw(t) for t in range(6, 21)])])
        monitor.ingest([agent(samples=[raw(t) for t in range(6, 21)])])     # the same heartbeat again
        samples = monitor.samples("a1")
        self.assertEqual([s["download_kbs"] for s in samples], [100.0 + t for t in range(1, 21)])
        self.assertEqual(set(samples[0]), {
            "timestamp", "cpu_percent", "memory_percent", "memory_used_gb", "memory_total_gb",
            "download_kbs", "upload_kbs", "tcp_connections", "process_count"})

    def test_bad_values_are_dropped_or_clamped(self):
        self.assertIsNone(normalize_sample({"ts": BASE}))
        self.assertIsNone(normalize_sample(raw(1, cpu="high")))
        self.assertIsNone(normalize_sample(raw(1, cpu=float("nan"))))
        self.assertIsNone(normalize_sample(raw(1, cpu=True)))
        self.assertIsNone(normalize_sample(dict(raw(1), ts=1e300)))
        self.assertIsNone(normalize_sample("sample"))
        _ts, sample = normalize_sample(raw(1, cpu=150, down_kbs=-5, tcp_conns=-1))
        self.assertEqual((sample["cpu_percent"], sample["download_kbs"], sample["tcp_connections"]),
                         (100.0, 0.0, -1))
        monitor = RemoteMonitor()
        monitor.ingest([agent(samples=[raw(1), "junk", raw(2, mem_percent=None), raw(3)])])
        self.assertEqual(len(monitor.samples("a1")), 2)

    def test_status_of_each_kind_of_agent(self):
        monitor = RemoteMonitor()
        monitor.ingest([
            agent("ok", samples=[raw(1)]),
            agent("new", event={}),                                   # no heartbeat yet
            agent("old", event={"state": "idle", "cursor": 3}),      # older agent version
            agent("bare", event={"state": "idle", "system": {"v": 1, "available": False,
                                                             "error": "psutil is not installed"}}),
        ])
        status = {item["agent_id"]: (item["status"], item["error"]) for item in monitor.agents()}
        self.assertEqual(status, {"ok": ("ok", ""), "new": ("waiting", ""), "old": ("unsupported", ""),
                                  "bare": ("unavailable", "psutil is not installed")})

    def test_history_is_capped_cleared_and_follows_the_list(self):
        monitor = RemoteMonitor()
        with patch.object(remote_module, "MAX_POINTS", 10):
            monitor.ingest([agent(samples=[raw(t) for t in range(1, 16)])])
        self.assertEqual(len(monitor.samples("a1")), 10)
        monitor.clear("a1")
        monitor.ingest([agent(samples=[raw(t) for t in range(1, 17)])])
        self.assertEqual([s["download_kbs"] for s in monitor.samples("a1")], [116.0])
        monitor.ingest([agent("b2", name="B")])
        self.assertEqual([item["agent_id"] for item in monitor.agents()], ["b2"])
        self.assertEqual(monitor.samples("a1"), [])

    def test_connection_changes_are_announced_once(self):
        monitor = RemoteMonitor()
        seen = []
        monitor.changed.connect(lambda: seen.append(monitor.connection))
        monitor.set_connection("error", "timed out")
        monitor.set_connection("error", "timed out")
        monitor.set_connection("ok")
        self.assertEqual(seen, ["error", "ok"])


class MonitorViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from app.ui.monitor_view import MonitorView
        self.saved = copy.deepcopy(settings._data)
        settings._data["language"] = "zh-CN"
        remote_monitor.reset()
        remote_monitor.watching = ""
        remote_monitor.page_visible = False
        remote_monitor.connection, remote_monitor.connection_error = "unknown", ""
        self.view = MonitorView()
        self.view.resize(1100, 900)
        self.view.show()
        QTest.qWait(50)

    def tearDown(self):
        self.view.close()
        self.view.deleteLater()
        QTest.qWait(10)
        remote_monitor.reset()
        remote_monitor.watching = ""
        remote_monitor.page_visible = False
        settings._data.clear()
        settings._data.update(self.saved)

    def local(self, cpu=12.5):
        self.view._on_update({"cpu": cpu, "mem_percent": 48.0, "mem_used_gb": 7.7,
                              "mem_total_gb": 16.0, "down_kbs": 30.0, "up_kbs": 5.0,
                              "tcp_conns": 90})

    def items(self):
        combo = self.view.sourceCombo
        return [(combo.itemData(i), combo.itemText(i)) for i in range(combo.count())]

    def choose(self, agent_id):
        ids = [item[0] for item in self.items()]
        self.view.sourceCombo.setCurrentIndex(ids.index(agent_id))

    def points(self, plot, index=0):
        series = list(plot._series.values())[index][0]
        return [point.y() for point in series.points()]

    def test_servers_are_listed_next_to_this_pc(self):
        self.assertEqual(self.items(), [("", "本机")])
        remote_monitor.ingest([agent("a1", "东京-1"), agent("a2", "web", online=False),
                               agent("a3", "web")])
        self.assertEqual(self.items(), [("", "本机"), ("a1", "东京-1"), ("a2", "web (a2)（离线）"),
                                        ("a3", "web (a3)")])
        self.assertEqual(self.view.sourceCombo.currentIndex(), 0)

    def test_a_server_is_shown_like_this_pc(self):
        self.local(cpu=12.5)
        remote_monitor.ingest([agent(samples=[raw(t, cpu=40.0 + t) for t in range(1, 16)])])
        self.choose("a1")
        self.assertEqual(remote_monitor.watching, "a1")
        self.assertEqual(self.view.tCpu.valueLabel.text(), "55.0 %")
        self.assertEqual(self.view.tMem.valueLabel.text(), "50.0 %")
        self.assertIn("2.0 / 4.0 GB", self.view.tMem.detailLabel.text())
        self.assertEqual(self.view.tTcp.value.text(), "30")
        self.assertEqual(self.view.tProc.value.text(), "120")
        self.assertEqual(self.points(self.view.cpuPlot), [40.0 + t for t in range(1, 16)])
        self.assertEqual(self.points(self.view.netPlot), [100.0 + t for t in range(1, 16)])
        self.assertIn("在线：tokyo-1", self.view.sourceStatus.text())

        # This PC keeps recording but does not overwrite the server's numbers.
        self.local(cpu=99.0)
        self.assertEqual(self.view.tCpu.valueLabel.text(), "55.0 %")
        self.assertEqual(len(self.view._samples), 2)

        # New heartbeats extend the server's charts.
        remote_monitor.ingest([agent(samples=[raw(t, cpu=40.0 + t) for t in range(6, 21)])])
        self.assertEqual(len(self.points(self.view.cpuPlot)), 20)
        self.assertEqual(self.view.tCpu.valueLabel.text(), "60.0 %")

        self.choose("")
        self.assertEqual(remote_monitor.watching, "")
        self.assertEqual(self.view.tCpu.valueLabel.text(), "99.0 %")
        self.assertEqual(self.points(self.view.cpuPlot), [12.5, 99.0])

    def test_status_line_explains_what_is_shown(self):
        hint = self.view.sourceStatus
        self.assertTrue(hint.isVisible())
        self.assertIn("服务器节点", hint.text())          # how to add a server
        remote_monitor.ingest([agent("old", "old", event={"state": "idle", "cursor": 1}),
                               agent("new", "new", event={}),
                               agent("gone", "gone", online=False, samples=[raw(1)])])
        self.assertFalse(hint.isVisible())               # this PC, servers available
        self.choose("old")
        self.assertIn("版本较旧", hint.text())
        self.assertEqual(self.view.tCpu.valueLabel.text(), "-- %")
        self.assertEqual(self.view.tTcp.value.text(), "--")
        self.choose("new")
        self.assertIn("等待", hint.text())
        self.choose("gone")
        self.assertIn("离线", hint.text())
        self.assertEqual(self.view.tCpu.valueLabel.text(), "20.0 %")   # the last data received
        remote_monitor.set_connection("error", "relay connection timed out")
        self.assertIn("relay connection timed out", hint.text())

    def test_server_removed_from_the_list_falls_back_to_this_pc(self):
        self.local()
        remote_monitor.ingest([agent(samples=[raw(1)])])
        self.choose("a1")
        remote_monitor.ingest([])
        self.assertEqual(self.items(), [("", "本机")])
        self.assertEqual(self.view._source, "")
        self.assertEqual(remote_monitor.watching, "")
        self.assertEqual(self.view.tCpu.valueLabel.text(), "12.5 %")

    def test_pause_clear_and_export_work_on_the_server(self):
        self.local()
        remote_monitor.ingest([agent(samples=[raw(t) for t in range(1, 6)])])
        self.choose("a1")
        self.view._toggle_plot_pause()
        remote_monitor.ingest([agent(samples=[raw(t) for t in range(1, 9)])])
        self.assertEqual(len(self.points(self.view.cpuPlot)), 5)        # paused
        self.view._toggle_plot_pause()
        self.assertEqual(len(self.points(self.view.cpuPlot)), 8)

        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, "server")
            with patch("app.ui.monitor_view.QFileDialog.getSaveFileName",
                       return_value=(target, "")) as dialog, \
                    patch("app.ui.monitor_view.InfoBar"):
                self.view._export_csv()
            self.assertTrue(dialog.call_args[0][2].startswith("flowbench-monitor-东京-1-"))
            with open(target + ".csv", encoding="utf-8-sig", newline="") as fh:
                rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 8)
        self.assertEqual(rows[-1]["download_kbs"], "108.0")

        self.view._clear_history()
        self.assertEqual(remote_monitor.samples("a1"), [])
        self.assertEqual(len(self.view._samples), 1)                     # this PC is untouched
        remote_monitor.ingest([agent(samples=[raw(t) for t in range(1, 10)])])
        self.assertEqual(len(remote_monitor.samples("a1")), 1)           # only the new second

    def test_page_visibility_drives_polling(self):
        asked = []
        remote_monitor.refresh_requested.connect(lambda: asked.append(1))
        self.addCleanup(remote_monitor.refresh_requested.disconnect)
        self.view.hide()
        self.assertFalse(remote_monitor.page_visible)
        self.assertFalse(remote_monitor.wants_updates())
        self.view.show()
        self.assertTrue(remote_monitor.page_visible)
        self.assertTrue(asked)

    def test_english(self):
        self.view.close()
        settings._data["language"] = "en-US"
        from app.ui.monitor_view import MonitorView
        self.view = MonitorView()
        self.view.show()
        remote_monitor.ingest([agent(samples=[raw(1)]), agent("a2", "web", online=False)])
        self.assertEqual(self.items()[0][1], "This PC")
        self.assertEqual(self.items()[2][1], "web (offline)")
        self.choose("a1")
        self.assertIn("Online: tokyo-1", self.view.sourceStatus.text())


class _FakeClient:
    def __init__(self, agents=None, error=None):
        self.agents, self.error, self.calls = agents or [], error, 0

    def list_agents(self):
        self.calls += 1
        if self.error:
            raise RuntimeError(self.error)
        return self.agents


class AgentPollingTests(unittest.TestCase):
    """The Server Agents page fetches the list while the Monitor page needs it."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from app.ui.agent_view import AgentView
        remote_monitor.reset()
        remote_monitor.watching = ""
        remote_monitor.page_visible = False
        remote_monitor.connection = "unknown"
        self.view = AgentView()
        self.view._poll_timer.stop()
        self.addCleanup(self.view.deleteLater)
        self.addCleanup(remote_monitor.reset)

    def tearDown(self):
        remote_monitor.watching = ""
        remote_monitor.page_visible = False

    def test_no_poll_when_nothing_needs_it(self):
        client = _FakeClient([agent(samples=[raw(1)])])
        with patch.object(self.view, "_client", return_value=client):
            self.view._poll_remote_status()
            QTest.qWait(100)
        self.assertEqual(client.calls, 0)

    def test_watching_a_server_polls_and_feeds_the_monitor(self):
        client = _FakeClient([agent(samples=[raw(1), raw(2)])])
        remote_monitor.watching = "a1"
        with patch.object(self.view, "_client", return_value=client):
            self.view._poll_remote_status()
            self.assertTrue(wait_until(lambda: len(remote_monitor.samples("a1")) == 2))
        self.assertEqual(remote_monitor.connection, "ok")
        # The Server Agents page's own state is updated as before.
        self.assertEqual([item["agent_id"] for item in self.view._agents], ["a1"])

    def test_monitor_request_and_errors(self):
        remote_monitor.page_visible = True
        with patch.object(self.view, "_client", side_effect=ValueError("no url")):
            remote_monitor.request_refresh()
        self.assertEqual(remote_monitor.connection, "unconfigured")
        client = _FakeClient(error="relay connection timed out")
        with patch.object(self.view, "_client", return_value=client):
            remote_monitor.request_refresh()
            self.assertTrue(wait_until(lambda: remote_monitor.connection == "error"))
        self.assertEqual(remote_monitor.connection_error, "relay connection timed out")


if __name__ == "__main__":
    unittest.main()
