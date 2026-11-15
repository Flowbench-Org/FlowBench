"""Settings and plugins from before FlowBench was renamed (NetPulse) carry over."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("APPDATA", os.path.join(tempfile.gettempdir(), "FlowBench-migration-tests"))

from app.services.settings import AppSettings

OLD_PLUGIN = "class OldTool(NetPulsePlugin):\n    name = ('旧工具', 'Old tool')\n"


class OldProfileTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        appdata = patch.dict(os.environ, {"APPDATA": str(self.root)})
        appdata.start()
        self.addCleanup(appdata.stop)

    def old_profile(self, name, settings, plugins=None):
        folder = self.root / name
        (folder / "plugins").mkdir(parents=True)
        (folder / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
        for file_name, text in (plugins or {}).items():
            (folder / "plugins" / file_name).write_text(text, encoding="utf-8")
        return folder

    def test_settings_and_plugins_come_over_from_netpulse(self):
        old = self.old_profile("NetPulse", {
            "theme": "dark", "language": "en-US", "_lang_migrated": True,
            "authorized": [{"host": "example.test", "note": "lab", "ts": 1}]},
            {"old_tool.py": OLD_PLUGIN})
        settings = AppSettings()
        self.assertEqual(settings.theme, "dark")
        self.assertEqual(settings.language, "en-US")
        self.assertEqual([item["host"] for item in settings.authorized], ["example.test"])
        self.assertTrue((self.root / "FlowBench" / "plugins" / "old_tool.py").is_file())
        self.assertTrue((old / "settings.json").is_file())          # the old folder is left alone

        from app.services.plugins import PluginManager
        manager = PluginManager()
        manager.discover()
        record = manager.record("old_tool")
        self.assertTrue(manager.load(record), record.error)         # NetPulsePlugin still works
        manager.unload("old_tool")

    def test_the_newest_old_name_wins_and_only_once(self):
        self.old_profile("NetPulsePy", {"theme": "light"})
        self.old_profile("NetPulse", {"theme": "dark"})
        self.assertEqual(AppSettings().theme, "dark")
        (self.root / "NetPulse" / "settings.json").write_text(json.dumps({"theme": "light"}))
        self.assertEqual(AppSettings().theme, "dark")               # FlowBench's own file now

    def test_existing_plugins_are_not_mixed_with_old_ones(self):
        self.old_profile("NetPulse", {"theme": "dark"}, {"old_tool.py": OLD_PLUGIN})
        mine = self.root / "FlowBench" / "plugins"
        mine.mkdir(parents=True)
        (mine / "new_tool.py").write_text("# installed in FlowBench", encoding="utf-8")
        AppSettings()
        self.assertEqual(sorted(p.name for p in mine.iterdir()), ["new_tool.py"])

    def test_a_broken_old_file_is_skipped(self):
        (self.root / "NetPulse").mkdir()
        (self.root / "NetPulse" / "settings.json").write_text("{", encoding="utf-8")
        self.old_profile("NetPulsePy", {"theme": "dark"})
        self.assertEqual(AppSettings().theme, "dark")


if __name__ == "__main__":
    unittest.main()
