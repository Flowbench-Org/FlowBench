"""Popular searches ranked by real installs (jsDelivr download statistics)."""
import copy
import hashlib
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("APPDATA", os.path.join(tempfile.gettempdir(), "FlowBench-market-tests"))

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.services import market
from app.services.settings import settings


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class _FakeStats:
    """jsDelivr's stats API: a versions list, then files per version."""

    def __init__(self, versions, files):
        self.versions = versions
        self.files = files
        self.urls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.urls.append(url)
        if url == f"{market.STATS_API}/versions":
            return _Response(self.versions)
        version = url[len(market.STATS_API) + 1:-len("/files")]
        return _Response(self.files[version])


def _version(name, total, kind="commit"):
    return {"type": kind, "version": name, "hits": {"total": total}}


def _file(name, total):
    return {"name": name, "hits": {"total": total}}


class InstallCountTests(unittest.TestCase):
    def test_counts_are_summed_over_commits_and_tags(self):
        stats = _FakeStats(
            [_version("a" * 40, 30), _version("1.2.11", 5, "version"), _version("b" * 40, 0),
             _version("../evil", 99)],
            {"a" * 40: [_file("/marketplace/japanese.py", 20), _file("/marketplace/zh_tw.py", 10),
                        _file("/marketplace/plugins-index.json", 50), _file("/app_logo.png", 7)],
             "1.2.11": [_file("/marketplace/japanese.py", 4), _file("/marketplace/quick_notes.py", 0)]})
        counts = market.fetch_install_counts(stats)
        self.assertEqual(counts, {"japanese.py": 24, "zh_tw.py": 10})
        # Versions without downloads and unsafe version names are never fetched.
        self.assertEqual(stats.urls, [f"{market.STATS_API}/versions",
                                      f"{market.STATS_API}@{'a' * 40}/files",
                                      f"{market.STATS_API}@1.2.11/files"])

    def test_no_downloads_yet_is_an_empty_result(self):
        self.assertEqual(market.fetch_install_counts(_FakeStats([], {})), {})

    def test_unexpected_stats_format_is_an_error(self):
        with self.assertRaises(ValueError):
            market.fetch_install_counts(_FakeStats({"message": "rate limited"}, {}))

    def test_saved_counts_are_used_while_fresh_and_when_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "market_stats.json")
            with patch.object(market, "_stats_file", return_value=path):
                client = market.MarketClient()
                seen = []
                client.install_counts_ready.connect(seen.append)

                market.save_install_counts({"japanese.py": 3})
                with patch.object(market, "fetch_install_counts",
                                  side_effect=AssertionError("network")):
                    client.fetch_install_counts()
                self.assertEqual(seen, [{"japanese.py": 3}])

                # Stale and offline: the older real numbers beat none.
                market.save_install_counts({"japanese.py": 3},
                                           fetched=time.time() - market.STATS_MAX_AGE - 1)
                with patch.object(market, "fetch_install_counts", side_effect=OSError("offline")):
                    client._counts_work(market.load_install_counts()[0])
                self.assertEqual(seen[-1], {"japanese.py": 3})

                with patch.object(market, "fetch_install_counts", return_value={"zh_tw.py": 8}):
                    client._counts_work({"japanese.py": 3})
                self.assertEqual(seen[-1], {"zh_tw.py": 8})
                self.assertEqual(market.load_install_counts()[0], {"zh_tw.py": 8})

    def test_corrupt_saved_counts_are_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "market_stats.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('{"fetched": 1, "counts": {"a.py": "many", "b.py": 2, "c.py": -1}}')
            with patch.object(market, "_stats_file", return_value=path):
                self.assertEqual(market.load_install_counts()[0], {"b.py": 2})


class _FileHandler(BaseHTTPRequestHandler):
    body = b""
    status = 200

    def do_GET(self):
        self.server.requests.append(self.path)
        self.send_response(self.status)
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args):
        pass


class DownloadSourceTests(unittest.TestCase):
    def serve(self, body, status=200):
        handler = type("H", (_FileHandler,), {"body": body, "status": status})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server.requests = []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server, f"http://127.0.0.1:{server.server_address[1]}/"

    def install(self, cdn_body, raw_body, cdn_status=200, expect=b"print('new')\n"):
        cdn, cdn_base = self.serve(cdn_body, cdn_status)
        raw, raw_base = self.serve(raw_body)
        entry = {"id": "demo", "file": "demo.py", "sha256": hashlib.sha256(expect).hexdigest()}
        client = market.MarketClient()
        ready, failed, purged = [], [], []
        client.download_ready.connect(lambda e, path: ready.append(open(path, "rb").read()))
        client.download_failed.connect(lambda pid, msg: failed.append(msg))
        with patch.object(market, "CDN_PLUGIN_BASE", cdn_base), \
                patch.object(market, "RAW_PLUGIN_BASE", raw_base), \
                patch.object(market, "_purge_cdn_copy", side_effect=purged.append):
            client._install_work(entry)
        return ready, failed, purged, cdn.requests, raw.requests

    def test_repository_plugins_come_through_the_cdn_first(self):
        self.assertEqual(market.plugin_download_urls("japanese.py"),
                         [market.CDN_PLUGIN_BASE + "japanese.py",
                          market.RAW_PLUGIN_BASE + "japanese.py"])
        self.assertTrue(market.CDN_PLUGIN_BASE.startswith("https://cdn.jsdelivr.net/gh/Flowbench-Org/FlowBench@"))
        self.assertEqual(market.plugin_download_urls("https://example.com/p.py"),
                         ["https://example.com/p.py"])

        ready, failed, purged, cdn, raw = self.install(b"print('new')\n", b"unused")
        self.assertEqual((ready, failed, purged), ([b"print('new')\n"], [], []))
        self.assertEqual((cdn, raw), (["/demo.py"], []))

    def test_outdated_cdn_copy_falls_back_to_github_and_is_purged(self):
        ready, failed, purged, cdn, raw = self.install(b"print('old')\n", b"print('new')\n")
        self.assertEqual((ready, failed), ([b"print('new')\n"], []))
        self.assertEqual(purged, ["demo.py"])
        self.assertEqual((cdn, raw), (["/demo.py"], ["/demo.py"]))

    def test_unreachable_cdn_falls_back_to_github(self):
        ready, failed, purged, _cdn, raw = self.install(b"", b"print('new')\n", cdn_status=503)
        self.assertEqual((ready, failed, purged), ([b"print('new')\n"], [], []))
        self.assertEqual(raw, ["/demo.py"])

    def test_bad_file_everywhere_is_refused(self):
        ready, failed, _purged, _cdn, _raw = self.install(b"print('old')\n", b"print('evil')\n")
        self.assertEqual(ready, [])
        self.assertEqual(len(failed), 1)
        self.assertIn("sha256 mismatch", failed[0])


class PopularSearchPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from app.ui import market_view
        self.saved = copy.deepcopy(settings._data)
        settings._data["language"] = "zh-CN"
        settings._data["plugin_market_search_history"] = []
        patches = [patch.object(market.MarketClient, "fetch_index"),
                   patch.object(market.MarketClient, "fetch_install_counts")]
        self.fetch_counts = patches[1].start()
        patches[0].start()
        for item in patches:
            self.addCleanup(item.stop)
        self.page = market_view.PluginMarketPage()
        self.page.resize(1000, 700)
        self.page.show()
        self.entries = [
            {"id": "old", "name": ["旧插件", "Old"], "file": "old.py", "date": "2026-01-01",
             "sha256": "0" * 64},
            {"id": "new", "name": ["新插件", "New"], "file": "new.py", "date": "2026-11-01",
             "sha256": "0" * 64},
            {"id": "mid", "name": ["中间插件", "Mid"], "file": "mid.py", "date": "2026-06-01",
             "sha256": "0" * 64},
            {"id": "future", "name": ["未来插件", "Future"], "file": "future.py",
             "date": "2026-12-01", "min_app": "99.0", "sha256": "0" * 64},
            {"id": "ext", "name": ["外部插件", "External"], "file": "https://example.com/ext.py",
             "date": "2026-02-01", "sha256": "0" * 64},
        ]

    def tearDown(self):
        self.page.close()
        self.page.deleteLater()
        QTest.qWait(10)
        settings._data.clear()
        settings._data.update(self.saved)

    def hot_rows(self):
        rows = []
        for btn, _rank, term, count in self.page._hot_buttons:
            if not btn.isHidden():
                rows.append((term.text(), count.text()))
        return rows

    def test_without_stats_newest_first_and_says_so(self):
        self.page._on_index(self.entries, False)
        self.fetch_counts.assert_called_once()
        self.assertEqual(self.hot_rows(), [("新插件", ""), ("中间插件", ""), ("外部插件", ""),
                                           ("旧插件", "")])   # the incompatible one is left out
        self.assertEqual(self.page.hotNote.text(), "安装统计约有两天延迟，暂按上架时间排列")
        self.assertIn("两天", self.page.hotNote.toolTip())

    def test_real_installs_rank_first_and_are_shown(self):
        self.page._on_index(self.entries, False)
        self.page._on_install_counts({"old.py": 1520, "mid.py": 1, "future.py": 999,
                                      "ext.py": 50})
        self.assertEqual(self.hot_rows(), [("旧插件", "1,520 次安装"), ("中间插件", "1 次安装"),
                                           ("新插件", ""), ("外部插件", "")])
        self.assertEqual(self.page.hotNote.text(), "按近 30 天安装次数")
        self.assertIn("jsDelivr", self.page.hotNote.toolTip())

    def test_popular_searches_stay_visible_next_to_the_history(self):
        self.page._on_index(self.entries, False)
        self.page._commit_search("dns")
        self.page._show_search_suggestions()
        panel = self.page.searchSuggestPanel
        opened = lambda: panel.height() >= panel.sizeHint().height()
        for _ in range(150):              # the slide-down animation, up to 3 s
            if opened():
                break
            QTest.qWait(20)
        self.assertTrue(self.page.hotHost.isVisible())
        self.assertTrue(self.page.historyHost.isVisible())
        self.assertTrue(opened(), (panel.height(), panel.sizeHint().height()))

    def test_six_searches_are_kept_in_two_full_rows(self):
        self.assertEqual(settings.SEARCH_HISTORY_LIMIT, 6)
        for term in ("a1", "b2", "c3", "d4", "e5", "f6", "g7"):
            self.page._commit_search(term)
        newest = ["g7", "f6", "e5", "d4", "c3", "b2"]          # "a1" was pushed out
        self.assertEqual(self.page._search_history, newest)
        self.assertEqual(settings.plugin_market_search_history, newest)
        self.page._show_search_suggestions()
        shown = [(term_btn.property("searchTerm"), self.page.historyLay.getItemPosition(
                     self.page.historyLay.indexOf(row))[:2])
                 for row, term_btn, _delete in self.page._history_rows if not row.isHidden()]
        self.assertEqual(shown, [(term, (1 + i // 3, i % 3)) for i, term in enumerate(newest)])

    def test_saved_settings_keep_six_searches(self):
        raw = {"plugin_market_search_history": ["a", "b", "c", "d", "e", "f", "g", "a"]}
        clean = type(settings)._sanitize_loaded_data(raw)
        self.assertEqual(clean["plugin_market_search_history"], ["a", "b", "c", "d", "e", "f"])

    def test_clicking_a_popular_search_finds_that_plugin(self):
        self.page._on_index(self.entries, False)
        self.page._on_install_counts({"mid.py": 7})
        self.page._show_search_suggestions()
        QTest.qWait(400)
        self.page._hot_buttons[0][0].click()
        self.assertEqual(self.page.searchEdit.text(), "中间插件")
        visible = [card.entry["id"] for card in self.page._all_cards if not card.isHidden()]
        self.assertEqual(visible, ["mid"])

    def test_empty_market_does_not_ask_for_stats(self):
        self.page._on_index([], False)
        self.fetch_counts.assert_not_called()


if __name__ == "__main__":
    unittest.main()
