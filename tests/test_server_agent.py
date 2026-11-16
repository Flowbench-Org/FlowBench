import json
import shutil
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from agent.hub import HubHandler, HubState
from agent.relay import sign_message
from server_agent import (AgentRuntime, CommandCursor, HubClient, HubLoop, load_config,
                          relay_command, run_relay_command, validate_job)


class AgentValidationTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "allowed_targets": ["example.test"],
            "allowed_protocols": ["HTTP", "HTTPS", "TCP"],
            "max_rate": 20,
            "max_duration": 60,
            "max_threads": 4,
            "max_packet_size": 4096,
        }

    def test_valid_job_is_normalized(self):
        job = validate_job({
            "target": "https://example.test/health", "protocol": "HTTPS",
            "rate": 5, "duration": 10, "threads": 2,
            "packet_size": 512, "port": 443,
        }, self.config)
        self.assertEqual(job["target"], "example.test")
        self.assertEqual(job["protocol"], "HTTPS")

    def test_url_that_parsers_read_differently_is_refused(self):
        # urlsplit takes example.test as the host of the first URL, but the
        # native engine and urllib3 would send it to 127.0.0.2.
        for url in ("http://127.0.0.2:8080\\@example.test/", "http://exam\tple.test/",
                    "http://example.test/\x00"):
            with self.assertRaises(ValueError, msg=repr(url)):
                validate_job({"target": "example.test", "protocol": "HTTP", "url": url,
                              "rate": 5, "duration": 10, "threads": 2}, self.config)

    def test_target_and_limits_are_enforced(self):
        with self.assertRaises(ValueError):
            validate_job({"target": "not-allowed.test", "protocol": "HTTP"}, self.config)
        with self.assertRaises(ValueError):
            validate_job({"target": "example.test", "protocol": "UDP", "rate": 1}, self.config)
        with self.assertRaises(ValueError):
            validate_job({"target": "example.test", "protocol": "HTTP", "rate": 21}, self.config)

    def test_provisioned_agent_allows_empty_target_list(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agent.json"
            path.write_text(json.dumps({
                "hub_url": "http://127.0.0.1:8787",
                "agent_id": "a1", "token": "agent-token-1234567890",
                "allowed_targets": [],
            }), encoding="utf-8")
            self.assertEqual(load_config(path)["allowed_targets"], [])

    def test_intervals_are_checked_at_start(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agent.json"

            def load(**values):
                path.write_text(json.dumps(dict({
                    "hub_url": "http://127.0.0.1:8787", "agent_id": "a1",
                    "token": "agent-token-1234567890"}, **values)), encoding="utf-8")
                return load_config(path)
            self.assertEqual(load()["poll_interval"], 2.0)
            self.assertEqual(load(request_timeout="15")["request_timeout"], 15.0)
            for key in ("poll_interval", "heartbeat_interval", "job_heartbeat_interval", "request_timeout"):
                for bad in ("8s", True, None, [2]):
                    with self.subTest(key=key, bad=bad), \
                            self.assertRaisesRegex(ValueError, f"{key} must be a number of seconds"):
                        load(**{key: bad})
                for bad in (0, -1, float("inf"), 7200):
                    with self.subTest(key=key, bad=bad), \
                            self.assertRaisesRegex(ValueError, f"{key} must be more than 0"):
                        load(**{key: bad})

    def test_target_sync_updates_allow_list(self):
        runtime = AgentRuntime(dict(self.config))
        runtime.handle({"type": "sync_targets", "targets": ["https://new.example/path"]})
        self.assertEqual(runtime.config["allowed_targets"], ["new.example"])


class HubStateTests(unittest.TestCase):
    def test_agent_auth_and_command_queue(self):
        state = HubState({
            "controller_token": "controller-token-1234567890",
            "agents": [{"agent_id": "a1", "token": "agent-token-1234567890"}],
        })
        state.register("a1", "agent-token-1234567890", {"hostname": "test"})
        item = state.enqueue("a1", {"type": "stop"})
        self.assertEqual(state.poll("a1", "agent-token-1234567890", 0), item)
        with self.assertRaises(PermissionError):
            state.poll("a1", "wrong-token", 0)

    def test_provision_persists_agent_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hub.json"
            path.write_text(json.dumps({
                "controller_token": "controller-token-1234567890", "agents": []
            }), encoding="utf-8")
            state = HubState(json.loads(path.read_text()), str(path))
            created = state.provision("Linux server")
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["agents"][0]["agent_id"], created["agent_id"])
            self.assertEqual(saved["agents"][0]["token"], created["token"])


class HubRestartTests(unittest.TestCase):
    """The hub keeps commands in memory and numbers them from 1 every time it
    starts; an agent must still take the commands sent after a restart."""

    TOKEN = "agent-token-1234567890"
    HUB = {"controller_token": "controller-token-1234567890",
           "agents": [{"agent_id": "a1", "name": "n1", "token": TOKEN}]}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        quiet = patch("builtins.print")
        self.printed = quiet.start()
        self.addCleanup(quiet.stop)

    def start_hub(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), HubHandler)
        server.state = HubState(self.HUB)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def agent(self, server, state_path=None):
        path = self.tmp / "agent.json"
        path.write_text(json.dumps({
            "hub_url": f"http://127.0.0.1:{server.server_address[1]}", "agent_id": "a1",
            "token": self.TOKEN, "allowed_targets": [], "metrics": False}), encoding="utf-8")
        config = load_config(path)
        return HubLoop(config, HubClient(config, path), AgentRuntime(config),
                       CommandCursor(state_path or self.tmp / "agent.json.state.json"))

    @staticmethod
    def targets(loop):
        return loop.runtime.config["allowed_targets"]

    @staticmethod
    def sync(server, name):
        return server.state.enqueue("a1", {"type": "sync_targets", "targets": [name]})

    def test_commands_sent_after_a_hub_restart_are_taken(self):
        server = self.start_hub()
        loop = self.agent(server)
        for name in ("t1.example", "t2.example", "t3.example"):
            self.sync(server, name)
        for _ in range(4):
            loop.step()
        self.assertEqual(self.targets(loop), ["t3.example"])
        self.assertEqual(loop.cursor.cursor, 3)

        server.state = HubState(self.HUB)           # restarted: its numbers start from 1 again
        self.assertEqual(self.sync(server, "after.example")["seq"], 1)
        for _ in range(3):
            loop.step()
        self.assertEqual(self.targets(loop), ["after.example"])
        # It registered again, so the restarted hub knows its limits.
        self.assertIn("limits", server.state.public_agent("a1")["metadata"])
        # An agent restarted afterwards does not take that command again.
        again = self.agent(server)
        again.step()
        self.assertEqual(self.targets(again), [])

    def test_an_agent_that_does_not_know_the_epoch_still_gets_new_commands(self):
        state = HubState(self.HUB)                  # restarted: numbers from 1 again
        self.assertEqual(state.enqueue("a1", {"type": "stop"})["seq"], 1)
        # An older agent asks for what comes after its number from before.
        self.assertEqual(state.poll("a1", self.TOKEN, 7), {"seq": 8, "command": {"type": "stop"}})
        self.assertEqual(state.enqueue("a1", {"type": "stop"})["seq"], 9)
        self.assertEqual(state.poll("a1", self.TOKEN, 8)["seq"], 9)
        self.assertIsNone(state.poll("a1", self.TOKEN, 9))
        self.assertNotEqual(HubState(self.HUB).epoch, state.epoch)

    def test_a_state_file_that_cannot_be_written_does_not_cost_the_command(self):
        blocker = self.tmp / "not-a-folder"
        blocker.write_text("a file where the state folder should be")
        server = self.start_hub()
        loop = self.agent(server, state_path=blocker / "agent.json.state.json")
        for name in ("t1.example", "t2.example"):
            self.sync(server, name)
            loop.step()
            self.assertEqual(self.targets(loop), [name])
        self.assertTrue(any("cannot save" in str(call) for call in self.printed.call_args_list))

    def test_cursor_is_kept_across_agent_restarts(self):
        path = self.tmp / "state.json"
        cursor = CommandCursor(path)
        self.assertFalse(cursor.hub_restarted("e1"))
        self.assertTrue(cursor.advance(5))
        self.assertFalse(cursor.advance(5))
        again = CommandCursor(path)
        self.assertEqual((again.cursor, again.epoch), (5, "e1"))
        self.assertFalse(again.hub_restarted("e1"))
        self.assertFalse(again.hub_restarted(""))   # a hub without epochs
        self.assertTrue(again.hub_restarted("e2"))
        self.assertEqual(CommandCursor(path).cursor, 0)
        path.write_text('{"cursor": 3}')            # written by an older agent
        old = CommandCursor(path)
        self.assertFalse(old.hub_restarted("e1"))
        self.assertEqual(old.cursor, 3)
        for junk in ("[1, 2]", "{", '{"cursor": "x"}'):
            path.write_text(junk)
            self.assertEqual(CommandCursor(path).cursor, 0)


class RelayCommandTests(unittest.TestCase):
    SECRET = "controller-token-1234567890"

    def command(self, created_at, agent_id="a1", **extra):
        return sign_message(self.SECRET, dict({
            "type": "command", "id": "c1", "agent_id": agent_id, "created_at": created_at,
            "command": {"type": "sync_targets", "targets": ["new.example"]}}, **extra))

    def test_commands_on_time_run(self):
        found = relay_command(self.SECRET, "a1", self.command(1000.0), now=1010.0)
        self.assertEqual(found, ("c1", {"type": "sync_targets", "targets": ["new.example"]}, None))
        runtime = AgentRuntime({"allowed_targets": [], "allowed_protocols": ["HTTP"]})
        run_relay_command(runtime, found[1], found[2])
        self.assertEqual(runtime.config["allowed_targets"], ["new.example"])

    def test_a_clock_that_is_off_is_reported_not_ignored(self):
        for now, off_by in ((1000.0 + 612, 612), (1000.0 - 400, -400)):     # ahead / behind
            with self.subTest(now=now):
                found = relay_command(self.SECRET, "a1", self.command(1000.0), now=now)
                self.assertEqual(found[2], off_by)
                runtime = AgentRuntime({"allowed_targets": [], "allowed_protocols": ["HTTP"]})
                with patch("builtins.print"):
                    run_relay_command(runtime, found[1], found[2])
                self.assertEqual(runtime.config["allowed_targets"], [])          # not run
                event = runtime.current_event()
                self.assertEqual((event["state"], event["error"]),
                                 ("error", f"command time is {abs(off_by)} s off the agent's clock"))

    def test_other_messages_are_ignored(self):
        self.assertIsNone(relay_command(self.SECRET, "a2", self.command(1000.0), now=1000.0))
        self.assertIsNone(relay_command("another-token-1234567890", "a1", self.command(1000.0), now=1000.0))
        self.assertIsNone(relay_command(self.SECRET, "a1", self.command(0), now=1000.0))
        self.assertIsNone(relay_command(self.SECRET, "a1", self.command(1000.0, type="status"), now=1000.0))


if __name__ == "__main__":
    unittest.main()
