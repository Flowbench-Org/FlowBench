"""Server agents report their limits; the desktop checks a job against them
before sending it, offers to run within them, and explains a refusal."""
import json
import os
import re
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("APPDATA", os.path.join(tempfile.gettempdir(), "FlowBench-agent-limits-tests"))

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import server_agent
from agent.hub import HubState
from app.services import agent_limits
from app.services.settings import settings

ROOT = Path(__file__).resolve().parents[1]
CJK = re.compile(r"[　-〿一-鿿＀-￯]")


def app():
    return QApplication.instance() or QApplication([])


def wait_until(condition, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        QTest.qWait(20)
    return bool(condition())


def agent(agent_id, name=None, rate=100, duration=300, threads=32, protocols=("HTTP", "HTTPS", "TCP"),
          limits=True):
    metadata = {"hostname": name or agent_id}
    if limits:
        metadata["limits"] = {"max_rate": rate, "max_duration": duration, "max_threads": threads,
                              "max_packet_size": 65536, "protocols": list(protocols)}
    return {"agent_id": agent_id, "name": name or agent_id, "online": True, "metadata": metadata}


JOB = {"target": "example.test", "protocol": "HTTP", "rate": 500, "duration": 600, "threads": 50,
       "packet_size": 64, "port": 80, "url": "http://example.test", "timeout": 5000, "headers": {}}


class AgentSideTests(unittest.TestCase):
    def config(self, **values):
        data = {"hub_url": "https://hub.example", "agent_id": "a1", "token": "t" * 20}
        data.update(values)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "agent.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            return server_agent.load_config(path)

    def test_metadata_reports_the_limits(self):
        config = self.config(max_rate=250, max_duration=120, max_threads=8,
                             allowed_protocols=["http", "TCP", "UDP"])
        limits = server_agent.metadata(config)["limits"]
        self.assertEqual(limits, {"max_rate": 250.0, "max_duration": 120, "max_threads": 8,
                                  "max_packet_size": 65536, "protocols": ["HTTP", "TCP"]})
        self.assertNotIn("limits", server_agent.metadata())      # no config: nothing to report
        # Defaults when agent.json leaves them out.
        self.assertEqual(server_agent.metadata(self.config())["limits"]["max_rate"], 100.0)

    def test_limits_travel_through_the_hub_and_parse_on_the_desktop(self):
        state = HubState({"controller_token": "c" * 20,
                          "agents": [{"agent_id": "a1", "name": "node", "token": "t" * 20}]})
        state.register("a1", "t" * 20, server_agent.metadata(self.config(max_rate=40)))
        listed = state.public_agent("a1")
        self.assertEqual(agent_limits.reported_limits(listed)["max_rate"], 40.0)

    def test_relay_status_carries_the_limits(self):
        meta = server_agent.metadata(self.config(max_threads=3))
        message = server_agent.relay_status_message("s" * 20, "a1", "node", meta, True, {})
        self.assertEqual(message["metadata"]["limits"]["max_threads"], 3)

    def test_bad_limits_stop_the_agent_at_start(self):
        for value in (0, -5, "abc", True, None, "NaN"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "max_rate must be a positive"):
                self.config(max_rate=value)
        with self.assertRaisesRegex(ValueError, "max_threads must be a positive"):
            self.config(max_threads=0)
        self.assertEqual(self.config(max_duration="600")["max_duration"], 600)

    def test_refusals_say_by_how_much(self):
        config = {"allowed_targets": ["example.test"], "allowed_protocols": ["HTTP"],
                  "max_rate": 100, "max_duration": 300, "max_threads": 32, "max_packet_size": 65536}
        cases = ((dict(rate=500), "rate exceeds agent limit: 500 > 100"),
                 (dict(duration=600), "duration exceeds agent limit: 600 > 300"),
                 (dict(threads=50), "threads exceed agent limit: 50 > 32"),
                 (dict(packet_size=70000), "packet_size exceeds agent limit: 70000 > 65536"),
                 (dict(rate=0), "rate must be positive"))
        for values, message in cases:
            with self.subTest(values=values), self.assertRaises(ValueError) as caught:
                server_agent.validate_job(dict({"target": "example.test", "protocol": "HTTP"}, **values),
                                          config)
            self.assertEqual(str(caught.exception), message)


class LimitLogicTests(unittest.TestCase):
    def test_reported_limits(self):
        self.assertEqual(agent_limits.reported_limits(agent("a", rate=50))["max_rate"], 50.0)
        self.assertIsNone(agent_limits.reported_limits(agent("a", limits=False)))     # older agent
        self.assertIsNone(agent_limits.reported_limits({"metadata": None}))
        for bad in (0, -1, "x", True, float("inf")):
            with self.subTest(bad=bad):
                self.assertIsNone(agent_limits.reported_limits(agent("a", rate=bad)))

    def test_problems_and_fit(self):
        limits = agent_limits.reported_limits(agent("a"))
        found = agent_limits.problems(JOB, limits)
        self.assertEqual(found, [("rate", 500, 100.0), ("duration", 600, 300), ("threads", 50, 32)])
        fitted = agent_limits.fit(JOB, limits)
        self.assertEqual((fitted["rate"], fitted["duration"], fitted["threads"]), (100.0, 300, 32))
        self.assertEqual(fitted["target"], JOB["target"])
        self.assertEqual(JOB["rate"], 500)                         # the original is not changed
        self.assertEqual(agent_limits.problems(fitted, limits), [])
        udp = dict(JOB, protocol="UDP")
        self.assertEqual(agent_limits.problems(udp, limits)[0], ("protocol", "UDP", ["HTTP", "HTTPS", "TCP"]))
        self.assertIsNone(agent_limits.fit(udp, limits))           # lowering cannot help
        self.assertEqual(agent_limits.problems(JOB, None), [])     # older agent: nothing known

    def test_refusal_parsing_old_and_new_messages(self):
        self.assertEqual(agent_limits.refusal("rate exceeds agent limit"), ("rate", None, None))
        self.assertEqual(agent_limits.refusal("rate exceeds agent limit: 500 > 100"), ("rate", "500", "100"))
        self.assertEqual(agent_limits.refusal("threads exceed agent limit: 50 > 32"), ("threads", "50", "32"))
        self.assertEqual(agent_limits.refusal("protocol is not allowed: UDP"), ("protocol", "UDP", None))
        self.assertEqual(agent_limits.refusal("an authorized job is already running"), ("busy", None, None))
        self.assertIsNone(agent_limits.refusal("connection reset"))


class FakeClient:
    def __init__(self):
        self.sent = []
        self.lock = threading.Lock()

    def send(self, agent_id, command):
        with self.lock:
            self.sent.append((agent_id, command))


class AgentPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app()

    def setUp(self):
        from app.ui.agent_view import AgentView
        self.saved_language = settings._data.get("language")
        self.addCleanup(lambda: settings._data.__setitem__("language", self.saved_language))
        settings._data["language"] = "en-US"
        self.view = AgentView()
        self.view._poll_timer.stop()
        self.addCleanup(self.view.deleteLater)
        self.asked = []
        self.answer = True
        self.view.ask_fit = lambda title, text: self.asked.append((title, text)) or self.answer

    def run_job(self, agents, selected, job=JOB, high_rate_ok=True):
        client = FakeClient()
        self.view._agents = agents
        with patch.object(self.view, "_selected_agents_ids", return_value=list(selected)), \
                patch.object(self.view, "_client", return_value=client), \
                patch.object(self.view, "_stress_config", return_value=dict(job)), \
                patch.object(self.view, "_confirm_high_rate", return_value=high_rate_ok) as confirm:
            self.view.run_job()
            wait_until(lambda: len(client.sent) >= 2 * len(selected), 3.0)
            QTest.qWait(50)
        self.confirm = confirm
        return {agent_id: command["config"] for agent_id, command in client.sent if command["type"] == "start"}

    def test_job_over_a_limit_is_lowered_per_agent_after_asking(self):
        agents = [agent("small", rate=100), agent("big", rate=1000, duration=3600, threads=64),
                  agent("old", limits=False)]
        sent = self.run_job(agents, ["small", "big", "old"])
        self.assertEqual(len(self.asked), 1)
        title, text = self.asked[0]
        self.assertEqual(title, "Over the agents' limits")
        self.assertIn("small: rate 500 QPS (limit 100), duration 600 s (limit 300), threads 50 (limit 32)", text)
        self.assertNotIn("big:", text)
        self.assertEqual((sent["small"]["rate"], sent["small"]["duration"], sent["small"]["threads"]),
                         (100.0, 300, 32))
        self.assertEqual((sent["big"]["rate"], sent["big"]["duration"]), (500, 600))
        self.assertEqual(sent["old"]["rate"], 500)                 # reports no limits: as it is
        self.assertTrue(any("small runs within its limits: 100 QPS, 300 s, 32 threads" in line
                            for line in self.view._log_lines))

    def test_cancelling_sends_nothing(self):
        self.answer = False
        sent = self.run_job([agent("small")], ["small"])
        self.assertEqual(sent, {})
        self.assertTrue(any("Cancelled: the job is over the agents' limits." in line
                            for line in self.view._log_lines))

    def test_within_limits_does_not_ask(self):
        job = dict(JOB, rate=50, duration=60, threads=4)
        sent = self.run_job([agent("a"), agent("b")], ["a", "b"], job=job)
        self.assertEqual(self.asked, [])
        self.assertEqual(set(sent), {"a", "b"})
        self.assertEqual(len({command["rate"] for command in sent.values()}), 1)

    def test_agent_without_the_protocol_is_skipped(self):
        job = dict(JOB, protocol="UDP", rate=10, duration=10, threads=1)
        agents = [agent("http-only"), agent("old", limits=False)]
        sent = self.run_job(agents, ["http-only", "old"], job=job)
        self.assertIn("http-only: does not support UDP; it will be skipped", self.asked[0][1])
        self.assertEqual(set(sent), {"old"})

    def test_no_agent_can_run_it(self):
        job = dict(JOB, protocol="UDP")
        sent = self.run_job([agent("a"), agent("b")], ["a", "b"], job=job)
        self.assertEqual(sent, {})
        self.assertEqual(self.asked, [])
        self.assertTrue(any("None of the selected agents can run this job" in line
                            for line in self.view._log_lines))

    def test_high_rate_is_confirmed_with_the_rate_actually_sent(self):
        job = dict(JOB, rate=2000, duration=10, threads=1)
        self.run_job([agent("a", rate=1000)], ["a"], job=job)
        self.confirm.assert_called_once_with("example.test", 1000.0)
        sent = self.run_job([agent("a", rate=300)], ["a"], job=job)
        self.confirm.assert_not_called()                           # lowered to 300: not high
        self.assertEqual(sent["a"]["rate"], 300.0)
        sent = self.run_job([agent("a", rate=5000)], ["a"], job=job, high_rate_ok=False)
        self.assertEqual(sent, {})

    def test_refusal_is_explained_once(self):
        refusing = dict(agent("a", name="hk-01"),
                        last_event={"state": "error", "error": "rate exceeds agent limit: 500 > 100", "ts": 1})
        self.view._agents = [refusing]
        self.view._update_agent_status_widgets()
        self.view._update_agent_status_widgets()
        self.assertIn("hk-01 [error]: the rate is over the agent's limit (500 > 100)",
                      self.view.nodeListLabel.text())
        logged = [line for line in self.view._log_lines if "refused the job" in line]
        self.assertEqual(len(logged), 1)
        old = dict(refusing, last_event={"state": "error", "error": "rate exceeds agent limit", "ts": 2})
        self.view._agents = [old]
        self.view._update_agent_status_widgets()
        self.assertIn("the rate is over the agent's limit", self.view.nodeListLabel.text())
        other = dict(refusing, last_event={"state": "error", "error": "disk full", "ts": 3})
        self.view._agents = [other]
        self.view._update_agent_status_widgets()
        self.assertIn("hk-01 [error]: disk full", self.view.nodeListLabel.text())

    def test_chinese_texts(self):
        from app.ui import agent_view
        settings._data["language"] = "zh-CN"
        self.assertEqual(agent_view.refusal_text("rate exceeds agent limit: 500 > 100"),
                         "速率超过节点上限（500 > 100）")
        self.assertEqual(agent_view.problem_text("threads", 50, 32), "线程 50（上限 32）")
        self.assertEqual(agent_view.limits_text(agent_limits.reported_limits(agent("a"))),
                         "上限 100 QPS · 300 秒 · 32 线程")
        settings._data["language"] = "en-US"
        for text in (agent_view.refusal_text("protocol is not allowed: UDP"),
                     agent_view.problem_text("rate", 500, 100.0),
                     agent_view.limits_text(agent_limits.reported_limits(agent("a")))):
            self.assertIsNone(CJK.search(text), text)

    def test_agent_row_tooltip_shows_the_limits(self):
        from qfluentwidgets import CheckBox
        from app.ui.agent_view import AgentRow
        row = AgentRow(agent("a", rate=250), CheckBox())
        self.addCleanup(row.deleteLater)
        self.assertIn("Limits 250 QPS · 300 s · 32 threads", row.toolTip())
        old = AgentRow(agent("b", limits=False), CheckBox())
        self.addCleanup(old.deleteLater)
        self.assertIn("Limits not reported (older agent)", old.toolTip())


class GenerateNodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app()

    def test_dialog_limits(self):
        from PySide6.QtWidgets import QWidget
        from app.ui.agent_view import GenerateNodeDialog
        host = QWidget()
        host.resize(900, 700)
        self.addCleanup(host.deleteLater)
        dialog = GenerateNodeDialog(host)
        self.assertEqual(dialog.limits(), agent_limits.DEFAULT_LIMITS)
        dialog.rateLimitSpin.setValue(800)
        dialog.durationLimitSpin.setValue(7200)
        dialog.threadLimitSpin.setValue(128)
        self.assertEqual(dialog.limits(), {"max_rate": 800, "max_duration": 7200, "max_threads": 128,
                                           "max_packet_size": 65536})
        self.assertEqual(dialog.threadLimitSpin.maximum(), 1024)       # like the Stress page

    def test_bundles_write_the_chosen_limits(self):
        from types import SimpleNamespace
        from app.ui.agent_view import AgentView
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        folder = Path(temp.name)
        exe, hub = folder / "FlowBench-Agent.exe", folder / "FlowBench-Hub.exe"
        exe.write_bytes(b"MZ agent")
        hub.write_bytes(b"MZ hub")
        with patch.object(AgentView, "_node_payload_paths", return_value=(exe, ROOT)), \
                patch.object(AgentView, "_hub_exe_path", return_value=hub):
            view = AgentView()
            view._poll_timer.stop()
            self.addCleanup(view.deleteLater)
            limits = {"max_rate": 800, "max_duration": 7200, "max_threads": 128, "max_packet_size": 65536}
            view._generate_relay_package("node", "mqtt://broker.hivemq.com:8000/" + "r" * 20,
                                         "controller-token-1234567890", folder / "relay.zip", limits)
            view._generate_all_in_one_package("node", "203.0.113.5", 8787, folder / "all.zip", limits)
            view.hubEdit.setText("https://hub.example.com")
            client = SimpleNamespace(provision_agent=lambda name: {
                "agent_id": "server-1", "token": "agent-token-1234567890"})
            view._provision_and_package(client, "node", folder / "hub.zip", limits)
            view._generate_relay_package("default", "mqtt://broker.hivemq.com:8000/" + "r" * 20,
                                         "controller-token-1234567890", folder / "default.zip")
        for name, prefix in (("relay.zip", "linux/"), ("all.zip", "linux/agent/"), ("hub.zip", "linux/")):
            with self.subTest(bundle=name), zipfile.ZipFile(folder / name) as bundle:
                config = json.loads(bundle.read(prefix + "agent.json"))
                self.assertEqual({key: config[key] for key in limits}, limits)
                readme = bundle.read("README.txt").decode("utf-8")
                self.assertIn("max_rate", readme)
                self.assertTrue("800" in readme and "7200" in readme and "128" in readme, readme)
        with zipfile.ZipFile(folder / "default.zip") as bundle:
            config = json.loads(bundle.read("linux/agent.json"))
        self.assertEqual(config["max_rate"], 100)


if __name__ == "__main__":
    unittest.main()
