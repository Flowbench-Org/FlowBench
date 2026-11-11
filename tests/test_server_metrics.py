"""Server agents report CPU, memory, network, TCP connections and processes."""
import ast
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("APPDATA", os.path.join(tempfile.gettempdir(), "FlowBench-metrics-tests"))

import server_agent
from agent import sysmetrics
from agent.hub import HubHandler, HubState
from agent.relay import verify_message
from agent.sysmetrics import ProcReader, PsutilReader, SystemSampler
from app.services.agent_control import ControllerClient, RelayControllerClient
from app.services.remote_monitor import RemoteMonitor

ROOT = Path(__file__).resolve().parents[1]
GB = 1024 ** 3


def _net_dev(nics):
    lines = ["Inter-|   Receive                            |  Transmit",
             " face |bytes    packets errs drop fifo frame compressed multicast|bytes"]
    for name, (rx, tx) in nics.items():
        lines.append(f"{name:>6}: {rx} 10 0 0 0 0 0 0 {tx} 10 0 0 0 0 0 0")
    return "\n".join(lines) + "\n"


class FakeProc:
    """A /proc tree in a temporary folder."""

    def __init__(self, test):
        self.dir = tempfile.TemporaryDirectory()
        test.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        (self.root / "net").mkdir()
        for pid in ("1", "42", "1337"):
            (self.root / pid).mkdir()
        (self.root / "self").mkdir()
        self.set(cpu=(100, 0, 100, 800, 0, 0, 0, 0, 50, 0),
                 meminfo={"MemTotal": 8 * 1024 * 1024, "MemFree": 1024 * 1024,
                          "MemAvailable": 2 * 1024 * 1024},
                 nics={"lo": (5000, 5000), "eth0": (1_000_000, 200_000)},
                 sockstat=(40, 7, 3))

    def set(self, cpu=None, meminfo=None, nics=None, sockstat=None):
        if cpu is not None:
            (self.root / "stat").write_text(
                "cpu  " + " ".join(str(x) for x in cpu) + "\ncpu0 1 2 3 4\nintr 5\n")
        if meminfo is not None:
            (self.root / "meminfo").write_text(
                "".join(f"{key}: {value} kB\n" for key, value in meminfo.items()))
        if nics is not None:
            (self.root / "net" / "dev").write_text(_net_dev(nics))
        if sockstat is not None:
            inuse, tw, inuse6 = sockstat
            (self.root / "net" / "sockstat").write_text(
                f"sockets: used 300\nTCP: inuse {inuse} orphan 0 tw {tw} alloc 60 mem 4\n"
                "UDP: inuse 5 mem 2\n")
            (self.root / "net" / "sockstat6").write_text(f"TCP6: inuse {inuse6}\nUDP6: inuse 1\n")


class _Clock:
    def __init__(self, start=100.0):
        self.now = start

    def __call__(self):
        return self.now


def _sampler(reader, **kwargs):
    mono = _Clock()
    sampler = SystemSampler(reader, clock=lambda: 1_800_000_000.0 + mono.now,
                            monotonic=mono, **kwargs)
    return sampler, mono


class ProcReaderTests(unittest.TestCase):
    def test_counters_become_one_sample_per_interval(self):
        proc = FakeProc(self)
        sampler, mono = _sampler(ProcReader(str(proc.root)))
        self.assertIsNone(sampler.sample_once())          # the first reading is the baseline
        # 100 more jiffies: 30 busy (incl. guest 5, already part of user), 70 idle.
        proc.set(cpu=(125, 0, 105, 870, 0, 0, 0, 0, 55, 0),
                 nics={"lo": (999_999, 999_999), "eth0": (1_000_000 + 2 * 1024 * 300,
                                                          200_000 + 2 * 1024 * 50)})
        mono.now += 2.0
        sample = sampler.sample_once()
        self.assertEqual(sample["cpu"], 30.0)
        self.assertEqual(sample["mem_percent"], 75.0)     # 6 of 8 GB not available
        self.assertEqual(sample["mem_used_gb"], 6.0)
        self.assertEqual(sample["mem_total_gb"], 8.0)
        self.assertEqual(sample["down_kbs"], 300.0)        # loopback is left out
        self.assertEqual(sample["up_kbs"], 50.0)
        self.assertEqual(sample["tcp_conns"], 50)          # inuse 40 + TIME_WAIT 7 + IPv6 3
        self.assertEqual(sample["proc_count"], 3)
        self.assertEqual(sample["ts"], 1_800_000_102.0)

    def test_fallbacks_for_older_kernels(self):
        proc = FakeProc(self)
        proc.set(meminfo={"MemTotal": 4 * 1024 * 1024, "MemFree": 1024 * 1024,
                          "Buffers": 512 * 1024, "Cached": 512 * 1024})
        for name in ("sockstat", "sockstat6"):
            (proc.root / "net" / name).unlink()
        header = "  sl  local_address rem_address   st\n"
        (proc.root / "net" / "tcp").write_text(header + "0: a b 0A\n1: c d 01\n2: e f 06\n")
        (proc.root / "net" / "tcp6").write_text(header + "0: a b 0A\n")
        reader = ProcReader(str(proc.root))
        self.assertEqual(reader.memory(), (2 * GB, 4 * GB))
        self.assertEqual(reader.tcp_connections(), 4)
        (proc.root / "net" / "tcp").unlink()
        (proc.root / "net" / "tcp6").unlink()
        self.assertEqual(reader.tcp_connections(), -1)

    def test_counters_that_go_backwards_are_not_negative(self):
        proc = FakeProc(self)
        sampler, mono = _sampler(ProcReader(str(proc.root)))
        sampler.sample_once()
        proc.set(cpu=(110, 0, 110, 880, 0, 0, 0, 0, 50, 0), nics={"eth0": (10, 10)})
        mono.now += 1.0
        sample = sampler.sample_once()
        self.assertEqual((sample["down_kbs"], sample["up_kbs"]), (0.0, 0.0))

    def test_this_machine(self):
        if not sys.platform.startswith("linux"):
            self.skipTest("reads /proc")
        import psutil
        sampler = SystemSampler.create()
        self.assertEqual(sampler.reader.source, "proc")
        sampler.sample_once()
        time.sleep(0.3)
        sample = sampler.sample_once()
        self.assertTrue(0 <= sample["cpu"] <= 100)
        vm = psutil.virtual_memory()
        self.assertAlmostEqual(sample["mem_total_gb"], vm.total / GB, places=2)
        self.assertAlmostEqual(sample["mem_percent"],
                               100 * (vm.total - vm.available) / vm.total, delta=5)
        self.assertGreater(sample["proc_count"], 0)
        self.assertGreaterEqual(sample["tcp_conns"], 0)
        self.assertGreaterEqual(sample["down_kbs"], 0)


class _Counters:
    def __init__(self, rx, tx):
        self.bytes_recv, self.bytes_sent = rx, tx


class PsutilReaderTests(unittest.TestCase):
    def fake_psutil(self):
        def connections(kind):
            raise PermissionError("AccessDenied")
        return types.SimpleNamespace(
            cpu_percent=lambda interval=None: 12.5,
            virtual_memory=lambda: types.SimpleNamespace(total=16 * GB, available=12 * GB),
            net_io_counters=lambda pernic=False: {
                "Ethernet": _Counters(1000, 300), "Wi-Fi": _Counters(24, 12),
                "Loopback Pseudo-Interface 1": _Counters(10 ** 9, 10 ** 9), "lo0": _Counters(7, 7)},
            net_connections=connections,
            pids=lambda: [4, 8, 15, 16, 23, 42])

    def test_windows_counters(self):
        reader = PsutilReader(self.fake_psutil())
        self.assertEqual(reader.cpu_percent(), 12.5)
        self.assertEqual(reader.memory(), (4 * GB, 16 * GB))
        self.assertEqual(reader.net_bytes(), (1024, 312))
        self.assertEqual(reader.tcp_connections(), -1)     # shown as "N/A"
        self.assertEqual(reader.process_count(), 6)

    def test_real_psutil(self):
        import psutil
        sampler = SystemSampler(PsutilReader(psutil))
        sampler.sample_once()
        time.sleep(0.2)
        sample = sampler.sample_once()
        self.assertTrue(0 <= sample["cpu"] <= 100)
        self.assertGreater(sample["mem_total_gb"], 0)


class _FakeReader:
    source = "fake"

    def __init__(self):
        self.calls = 0
        self.fail = False

    def cpu_percent(self):
        if self.fail:
            raise OSError("proc is gone")
        self.calls += 1
        return float(self.calls % 100)

    def memory(self):
        return 3 * GB, 4 * GB

    def net_bytes(self):
        return self.calls * 2048, self.calls * 1024

    def tcp_connections(self):
        return 9

    def process_count(self):
        return 99


class SamplerTests(unittest.TestCase):
    def test_keeps_the_last_samples_for_the_heartbeat(self):
        sampler, mono = _sampler(_FakeReader(), history=5)
        for _ in range(9):
            sampler.sample_once()
            mono.now += 1.0
        snapshot = sampler.snapshot()
        self.assertEqual((snapshot["v"], snapshot["available"], snapshot["source"]), (1, True, "fake"))
        self.assertEqual(len(snapshot["samples"]), 5)
        stamps = [item["ts"] for item in snapshot["samples"]]
        self.assertEqual(stamps, sorted(stamps))
        self.assertLess(len(json.dumps(snapshot)), 1500)

    def test_errors_are_reported_and_do_not_stop_sampling(self):
        reader = _FakeReader()
        sampler, mono = _sampler(reader)
        sampler.sample_once()
        reader.fail = True
        mono.now += 1.0
        self.assertIsNone(sampler.sample_once())
        self.assertIn("proc is gone", sampler.snapshot()["error"])
        reader.fail = False
        mono.now += 1.0
        self.assertIsNotNone(sampler.sample_once())
        self.assertNotIn("error", sampler.snapshot())

    def test_no_source_says_so(self):
        snapshot = SystemSampler(None).snapshot()
        self.assertFalse(snapshot["available"])
        self.assertIn("psutil", snapshot["error"])
        with patch.object(sysmetrics, "default_reader", side_effect=RuntimeError("boom")):
            self.assertIsNone(SystemSampler.create().reader)

    def test_background_thread_samples_every_interval(self):
        sampler = SystemSampler(_FakeReader(), interval=0.05).start()
        self.addCleanup(sampler.stop)
        deadline = time.monotonic() + 5
        while len(sampler.snapshot()["samples"]) < 3 and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertGreaterEqual(len(sampler.snapshot()["samples"]), 3)
        sampler.stop()
        sampler._thread.join(2)
        self.assertFalse(sampler._thread.is_alive())


class AgentConfigTests(unittest.TestCase):
    def config(self, **extra):
        data = {"hub_url": "https://hub.example.com", "agent_id": "a1",
                "token": "agent-token-1234567890", **extra}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agent.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            return server_agent.load_config(path)

    def test_metrics_are_on_by_default_and_validated(self):
        config = self.config()
        self.assertTrue(config["metrics"])
        self.assertEqual(config["metrics_interval"], 2.0)
        self.assertFalse(self.config(metrics=False)["metrics"])
        for bad in ("soon", 0.5, 61, float("nan")):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.config(metrics_interval=bad)

    def test_heartbeats_come_every_two_seconds_while_sending_metrics(self):
        config = self.config()
        idle, running = {"state": "idle"}, {"state": "running"}
        self.assertEqual(server_agent.heartbeat_interval(config, idle, True), 2.0)
        self.assertEqual(server_agent.heartbeat_interval(config, idle, False), 10.0)
        self.assertEqual(server_agent.heartbeat_interval(config, running, False), 2.0)
        config["metrics_interval"] = 5.0
        self.assertEqual(server_agent.heartbeat_interval(config, idle, True), 5.0)
        self.assertIsNone(server_agent.start_sampler({"metrics": False}))

    def test_heartbeat_carries_the_samples(self):
        sampler, mono = _sampler(_FakeReader())
        for _ in range(3):
            sampler.sample_once()
            mono.now += 1.0
        event = {"state": "idle", "cursor": 4}
        payload = server_agent.with_system(event, sampler)
        self.assertEqual(payload["state"], "idle")
        self.assertEqual(len(payload["system"]["samples"]), 2)
        self.assertNotIn("system", event)
        self.assertEqual(server_agent.with_system(event, None), event)


def _sampler_with(count):
    sampler, mono = _sampler(_FakeReader())
    for _ in range(count + 1):
        sampler.sample_once()
        mono.now += 1.0
    return sampler


class TransportTests(unittest.TestCase):
    """Samples reach the desktop through a real Hub and through the relay format."""

    def test_through_a_hub(self):
        state = HubState({"controller_token": "controller-token-1234567890",
                          "agents": [{"agent_id": "a1", "name": "东京-1",
                                      "token": "agent-token-1234567890"}]})
        server = ThreadingHTTPServer(("127.0.0.1", 0), HubHandler)
        server.state = state
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_address[1]}"
        with patch("builtins.print"):
            agent = server_agent.HubClient({"hub_url": url, "agent_id": "a1",
                                            "token": "agent-token-1234567890",
                                            "request_timeout": 5})
            agent.register(server_agent.metadata())
            agent.heartbeat(server_agent.with_system({"cursor": 0, "state": "idle"},
                                                     _sampler_with(4)))
            agents = ControllerClient(url, "controller-token-1234567890").list_agents()
        monitor = RemoteMonitor()
        monitor.ingest(agents)
        info = monitor.agent("a1")
        self.assertEqual((info["name"], info["status"], info["online"]), ("东京-1", "ok", True))
        self.assertEqual(info["hostname"], socket.gethostname())
        samples = monitor.samples("a1")
        self.assertEqual(len(samples), 4)
        self.assertEqual(samples[-1]["memory_percent"], 75.0)
        self.assertEqual(samples[-1]["process_count"], 99)

    def test_through_the_relay(self):
        secret = "controller-token-1234567890"
        message = server_agent.relay_status_message(
            secret, "a1", "东京-1", {"hostname": "tokyo-1"}, True,
            server_agent.with_system({"state": "idle"}, _sampler_with(3)))
        self.assertTrue(verify_message(secret, message))
        controller = RelayControllerClient("mqtt://broker.example.com:8000/" + "r" * 20, secret)
        client = controller._build_client()
        client.on_message(client, None, types.SimpleNamespace(
            payload=json.dumps(message, ensure_ascii=False).encode("utf-8")))
        monitor = RemoteMonitor()
        monitor.ingest(list(controller._agents.values()))
        self.assertEqual(monitor.agent("a1")["hostname"], "tokyo-1")
        self.assertEqual(len(monitor.samples("a1")), 3)
        # A forged status (wrong token) is still ignored.
        controller._agents.clear()
        forged = dict(message, name="evil")
        client.on_message(client, None, types.SimpleNamespace(payload=json.dumps(forged).encode()))
        self.assertEqual(controller._agents, {})


def _agent_imports(path):
    """Modules of the agent package that a file imports."""
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("agent."):
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names if alias.name.startswith("agent."))
    return found


class NodeBundleTests(unittest.TestCase):
    """Every node bundle ships every module the agent imports."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def needed(self):
        modules, todo = set(), [ROOT / "server_agent.py"]
        while todo:
            for module in _agent_imports(todo.pop()) - modules:
                modules.add(module)
                todo.append(ROOT / (module.replace(".", "/") + ".py"))
        return {module.replace(".", "/") + ".py" for module in modules}

    def test_file_list_and_specs_cover_the_imports(self):
        from app.ui.agent_view import LINUX_AGENT_FILES
        needed = self.needed()
        self.assertIn("agent/sysmetrics.py", needed)
        self.assertLessEqual(needed | {"server_agent.py", "agent/__init__.py"}, set(LINUX_AGENT_FILES))
        spec = (ROOT / "FlowBench.spec").read_text(encoding="utf-8")
        agent_spec = (ROOT / "Agent.spec").read_text(encoding="utf-8")
        for name in LINUX_AGENT_FILES:
            folder = os.path.dirname(f"agent_template/linux/{name}")
            self.assertIn(f"('{name}', '{folder}')", spec)
        for name in needed:
            self.assertIn(f'"{name[:-3].replace("/", ".")}"', agent_spec)
        self.assertIn('"psutil"', agent_spec)

    def make_view(self):
        from app.ui.agent_view import AgentView
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        folder = Path(temp.name)
        exe = folder / "FlowBench-Agent.exe"
        hub = folder / "FlowBench-Hub.exe"
        exe.write_bytes(b"MZ agent")
        hub.write_bytes(b"MZ hub")
        for item in (patch.object(AgentView, "_node_payload_paths", return_value=(exe, ROOT)),
                     patch.object(AgentView, "_hub_exe_path", return_value=hub)):
            item.start()
            self.addCleanup(item.stop)
        view = AgentView()
        view._poll_timer.stop()
        self.addCleanup(view.deleteLater)
        return view, folder

    def assert_runs(self, folder):
        """The extracted Linux folder imports on its own (no files from the app)."""
        script = ("import sys; sys.path.insert(0, sys.argv[1]); import server_agent; "
                  "from agent.sysmetrics import SystemSampler; "
                  "print(SystemSampler.create().snapshot()['available'])")
        result = subprocess.run([sys.executable, "-I", "-c", script, str(folder)],
                                cwd=str(folder), capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "True")

    def check_bundle(self, path, prefix):
        from app.ui.agent_view import LINUX_AGENT_FILES
        with zipfile.ZipFile(path) as bundle:
            names = set(bundle.namelist())
            for name in LINUX_AGENT_FILES:
                self.assertIn(prefix + name, names)
            target = Path(tempfile.mkdtemp(dir=path.parent))
            for name in LINUX_AGENT_FILES:
                bundle.extract(prefix + name, target)
        self.assert_runs(target / prefix)

    def test_relay_bundle(self):
        view, folder = self.make_view()
        out = folder / "relay.zip"
        view._generate_relay_package("node", "mqtt://broker.hivemq.com:8000/" + "r" * 20,
                                     "controller-token-1234567890", out)
        self.check_bundle(out, "linux/")

    def test_all_in_one_bundle(self):
        view, folder = self.make_view()
        out = folder / "all.zip"
        view._generate_all_in_one_package("node", "203.0.113.5", 8787, out)
        self.check_bundle(out, "linux/agent/")

    def test_hub_bundle(self):
        view, folder = self.make_view()
        view.hubEdit.setText("https://hub.example.com")
        client = types.SimpleNamespace(provision_agent=lambda name: {
            "agent_id": "server-1", "token": "agent-token-1234567890"})
        out = folder / "hub.zip"
        view._provision_and_package(client, "node", out)
        self.check_bundle(out, "linux/")


if __name__ == "__main__":
    unittest.main()
