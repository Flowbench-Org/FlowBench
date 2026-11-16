"""Removed servers stay hidden after discovery and a settings reload."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.services.agent_registry import AgentRegistry
from app.services.settings import AppSettings


URL = "mqtt://broker.example.com:8000/relay-one"
TOKEN = "test-controller-token-1234567890"
AGENTS = [
    {"agent_id": "server-a", "name": "Hong Kong", "online": True},
    {"agent_id": "server-b", "name": "Tokyo", "online": False},
]


class AgentRegistryTests(unittest.TestCase):
    def setUp(self):
        self.profile = tempfile.TemporaryDirectory(prefix="flowbench-registry-")
        self.addCleanup(self.profile.cleanup)
        environment = patch.dict(os.environ, {"APPDATA": self.profile.name})
        environment.start()
        self.addCleanup(environment.stop)
        self.settings = AppSettings()
        self.registry = AgentRegistry(self.settings)

    def test_removal_survives_fresh_discovery_and_a_disk_reload(self):
        self.registry.remove(URL, TOKEN, AGENTS[:1])
        discovered = copy.deepcopy(AGENTS)
        self.assertEqual(self.registry.filter(URL, TOKEN, discovered), discovered[1:])
        fresh = AgentRegistry(AppSettings())
        self.assertEqual(fresh.filter(URL, TOKEN, discovered), discovered[1:])
        self.assertEqual(fresh.removed(URL, TOKEN), [
            {"agent_id": "server-a", "name": "Hong Kong"}])
        disk = Path(self.settings.path).read_text(encoding="utf-8")
        self.assertNotIn(TOKEN, disk)
        self.assertNotIn(URL, disk)

    def test_scope_isolated_by_url_and_token_with_unambiguous_digest(self):
        self.registry.remove(URL, TOKEN, AGENTS[:1])
        self.assertEqual(self.registry.filter(URL + "-other", TOKEN, AGENTS), AGENTS)
        self.assertEqual(self.registry.filter(URL, TOKEN + "-other", AGENTS), AGENTS)
        self.assertEqual(self.registry.removed("  " + URL + "/ ", " " + TOKEN + " "),
                         self.registry.removed(URL, TOKEN))
        self.assertNotEqual(self.registry.scope_key("ab", "c"),
                            self.registry.scope_key("a", "bc"))

    def test_removal_merges_ids_and_is_idempotent_without_mutating_inputs(self):
        self.registry.remove(URL, TOKEN, AGENTS[:1])
        supplied = copy.deepcopy(AGENTS)
        self.registry.remove(URL, TOKEN, supplied)
        self.assertEqual(supplied, AGENTS)
        with patch.object(self.settings, "set") as save:
            self.registry.remove(URL, TOKEN, supplied)
        save.assert_not_called()
        returned = self.registry.removed(URL, TOKEN)
        returned[0]["name"] = "changed outside registry"
        self.assertEqual(self.registry.removed(URL, TOKEN)[0]["name"], "Hong Kong")
        self.assertEqual(len(self.registry.removed(URL, TOKEN)), 2)

    def test_restore_selected_or_all_leaves_other_connections_untouched(self):
        self.registry.remove(URL, TOKEN, AGENTS)
        self.registry.remove(URL + "-other", TOKEN, AGENTS)
        other_scope = self.registry.scope_key(URL + "-other", TOKEN)
        other = copy.deepcopy(self.settings.agent_removed_servers[other_scope])
        self.registry.restore(URL, TOKEN, ["server-a"])
        self.assertEqual(self.registry.filter(URL, TOKEN, AGENTS), AGENTS[:1])
        self.registry.restore(URL, TOKEN)
        self.assertEqual(self.registry.filter(URL, TOKEN, AGENTS), AGENTS)
        self.assertEqual(self.settings.agent_removed_servers[other_scope], other)
        reloaded = AgentRegistry(AppSettings())
        self.assertEqual(reloaded.removed(URL, TOKEN), [])
        self.assertEqual(len(reloaded.removed(URL + "-other", TOKEN)), 2)

    def test_damaged_records_do_not_break_discovery_or_other_scopes(self):
        scope = self.registry.scope_key(URL, TOKEN)
        corrupted = {scope: [None, [], {}, {"agent_id": 25},
                             {"agent_id": ""}, {"agent_id": "server-a", "name": None}],
                     "future-connection": {"preserve": "as written"}}
        self.settings.set("agent_removed_servers", corrupted)
        self.assertEqual(self.registry.removed(URL, TOKEN), [
            {"agent_id": "server-a", "name": "server-a"}])
        self.assertEqual(self.registry.filter(URL, TOKEN, AGENTS), AGENTS[1:])
        self.registry.remove(URL, TOKEN, AGENTS[1:])
        self.assertEqual(self.settings.agent_removed_servers["future-connection"],
                         corrupted["future-connection"])
        self.settings.set("agent_removed_servers", "broken")
        self.assertEqual(self.registry.removed(URL, TOKEN), [])
        self.registry.remove(URL, TOKEN, AGENTS[:1])
        self.assertEqual(len(self.registry.removed(URL, TOKEN)), 1)

    def test_failed_remove_preserves_effective_state_and_file(self):
        self.registry.remove(URL, TOKEN, AGENTS[:1])
        original = Path(self.settings.path).read_bytes()
        with patch.object(self.settings, "_atomic_write_json", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.registry.remove(URL, TOKEN, AGENTS[1:])
        self.assertEqual(self.registry.filter(URL, TOKEN, AGENTS), AGENTS[1:])
        self.assertEqual(Path(self.settings.path).read_bytes(), original)

    def test_failed_restore_preserves_effective_state_and_file(self):
        self.registry.remove(URL, TOKEN, AGENTS)
        original = Path(self.settings.path).read_bytes()
        with patch.object(self.settings, "_atomic_write_json", side_effect=OSError("access denied")):
            with self.assertRaisesRegex(OSError, "access denied"):
                self.registry.restore(URL, TOKEN)
        self.assertEqual(self.registry.filter(URL, TOKEN, AGENTS), [])
        self.assertEqual(Path(self.settings.path).read_bytes(), original)

    def test_records_are_local_state_excluded_from_shareable_backups(self):
        self.registry.remove(URL, TOKEN, AGENTS)
        self.assertNotIn("agent_removed_servers", self.settings.safe_snapshot())
        payload = json.loads(Path(self.settings.path).read_text(encoding="utf-8"))
        self.assertEqual(len(payload["agent_removed_servers"]), 1)


if __name__ == "__main__":
    unittest.main()
