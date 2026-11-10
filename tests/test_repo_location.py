"""The project lives at Flowbench-Org/FlowBench (moved from Carlown/FlowBench)."""
import os
import re
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("APPDATA", os.path.join(tempfile.gettempdir(), "FlowBench-repo-tests"))

from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
NEW = "Flowbench-Org/FlowBench"
# Addresses of the old repository; the author's own profile (github.com/Carlown)
# and the publisher login "Carlown" are a person and stay.
OLD_ADDRESS = re.compile(r"(github\.com|githubusercontent\.com|repos|gh)/Carlown/FlowBench", re.I)


class RepoLocationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_every_address_uses_the_organization(self):
        from app.services import market, updater
        from app.ui import market_view
        self.assertEqual(updater.REPO, NEW)
        self.assertEqual(updater.LATEST_URL, f"https://github.com/{NEW}/releases/latest")
        self.assertEqual(market._REPO, NEW)
        self.assertEqual(f"{market_view._REPO_OWNER}/{market_view._REPO_NAME}", NEW)
        for url, _kind in market.INDEX_SOURCES:
            self.assertIn(NEW, url)
        for url in (market.CDN_PLUGIN_BASE, market.RAW_PLUGIN_BASE, market.STATS_API,
                    market.INDEX_EDIT_URL):
            self.assertIn(NEW, url)

    def test_no_old_repository_address_is_left(self):
        files = [ROOT / "main.py", ROOT / "README.md", ROOT / "marketplace" / "plugins-index.json"]
        for folder in ("app", "agent", "flowbench_installer", "marketplace"):
            files += sorted((ROOT / folder).rglob("*.py"))
        found = []
        for path in files:
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if OLD_ADDRESS.search(line):
                    found.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
        self.assertEqual(found, [])

    def test_official_plugins_keep_their_publisher(self):
        from app.ui.market_view import PluginMarketPage
        for homepage in (f"https://github.com/{NEW}", "https://github.com/Carlown/FlowBench"):
            with self.subTest(homepage=homepage):
                entry = PluginMarketPage._normalize_market_entry(
                    {"id": "zh_tw", "author": "FlowBench", "homepage": homepage})
                self.assertEqual(entry["publisher"], "Carlown")
        stranger = PluginMarketPage._normalize_market_entry(
            {"id": "x", "author": "FlowBench", "homepage": "https://github.com/someone/else"})
        self.assertNotIn("publisher", stranger)


if __name__ == "__main__":
    unittest.main()
