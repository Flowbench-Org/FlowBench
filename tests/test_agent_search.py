import copy
import os
import re
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("APPDATA", os.path.join(tempfile.gettempdir(), "FlowBench-agent-search-tests"))

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QWidget

from PySide6.QtCore import QPoint
from PySide6.QtTest import QTest
from qfluentwidgets import Theme, isDarkTheme, setTheme

from app.services.settings import settings
from app.ui.agent_view import AgentSelectionDialog, agent_matches, agent_os


AGENTS = [
    {"agent_id": "a-hk-01", "name": "server-hk-01", "online": True,
     "metadata": {"hostname": "vps-hongkong", "platform": "Linux-5.15-x86_64"},
     "last_event": {"state": "running"}},
    {"agent_id": "a-hk-02", "name": "server-hk-02", "online": False,
     "metadata": {"hostname": "vps-hongkong-2"}, "last_event": {}},
    {"agent_id": "a-us-01", "name": "server-us-01", "online": True,
     "metadata": {"hostname": "la-box", "platform": "Windows-10"},
     "last_event": {"state": "idle"}},
]


class AgentMatchTests(unittest.TestCase):
    def test_empty_query_matches_everything(self):
        for query in ("", "   ", None):
            self.assertTrue(all(agent_matches(agent, query) for agent in AGENTS))

    def test_matches_name_id_hostname_platform_and_job_state(self):
        hk, _, us = AGENTS
        self.assertTrue(agent_matches(hk, "HK-01"))  # case-insensitive name
        self.assertTrue(agent_matches(us, "a-us"))  # agent ID
        self.assertTrue(agent_matches(us, "la-box"))  # hostname
        self.assertTrue(agent_matches(us, "windows"))  # platform
        self.assertTrue(agent_matches(hk, "running"))  # last job state
        self.assertFalse(agent_matches(us, "hongkong"))

    def test_online_state_in_both_languages(self):
        hk1, hk2, _ = AGENTS
        for word in ("online", "在线"):
            self.assertTrue(agent_matches(hk1, word))
            self.assertFalse(agent_matches(hk2, word))
        for word in ("offline", "离线"):
            self.assertTrue(agent_matches(hk2, word))
            self.assertFalse(agent_matches(hk1, word))

    def test_all_terms_must_match(self):
        names = [agent["agent_id"] for agent in AGENTS if agent_matches(agent, "hk online")]
        self.assertEqual(names, ["a-hk-01"])

    def test_missing_fields_do_not_crash(self):
        self.assertTrue(agent_matches({"agent_id": "bare"}, "bare"))
        self.assertTrue(agent_matches({"agent_id": "bare", "metadata": None, "last_event": None}, "offline"))


class AgentOsTests(unittest.TestCase):
    def test_detects_os_from_reported_platform(self):
        cases = {
            "Linux-5.15.0-91-generic-x86_64-with-glibc2.35": "linux",
            "Windows-10-10.0.20348-SP0": "windows",
            "Windows-11-10.0.22631-SP0": "windows",
            "macOS-14.4-arm64-arm-64bit": "",
            "": "",
        }
        for platform_name, expected in cases.items():
            with self.subTest(platform=platform_name):
                self.assertEqual(agent_os({"metadata": {"platform": platform_name}}), expected)
        self.assertEqual(agent_os({}), "")
        self.assertEqual(agent_os({"metadata": None}), "")


class AgentSelectionDialogSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.saved_settings = copy.deepcopy(settings._data)
        settings._data["language"] = "en-US"

    def tearDown(self):
        settings._data.clear()
        settings._data.update(self.saved_settings)

    def make_dialog(self, agents=AGENTS, selected=()):
        parent = QWidget()
        parent.resize(900, 700)
        self.addCleanup(parent.deleteLater)
        dialog = AgentSelectionDialog(agents, selected, parent)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.close)
        return dialog

    def test_search_filters_visible_servers(self):
        dialog = self.make_dialog()
        self.assertEqual(dialog.visible_ids(), ["a-hk-01", "a-hk-02", "a-us-01"])
        dialog.searchEdit.setText("hk")
        self.assertEqual(dialog.visible_ids(), ["a-hk-01", "a-hk-02"])
        self.assertIn("showing 2/3", dialog.selectedCount.text())
        dialog.searchEdit.setText("hk offline")
        self.assertEqual(dialog.visible_ids(), ["a-hk-02"])
        dialog.searchEdit.clear()
        self.assertEqual(len(dialog.visible_ids()), 3)
        self.assertNotIn("showing", dialog.selectedCount.text())

    def test_no_match_label(self):
        dialog = self.make_dialog()
        self.assertTrue(dialog.noMatchLabel.isHidden())
        dialog.searchEdit.setText("does-not-exist")
        self.assertEqual(dialog.visible_ids(), [])
        self.assertFalse(dialog.noMatchLabel.isHidden())
        dialog.searchEdit.setText("us")
        self.assertTrue(dialog.noMatchLabel.isHidden())

    def test_select_all_and_clear_act_on_search_results_only(self):
        dialog = self.make_dialog(selected=["a-us-01"])
        dialog.searchEdit.setText("hk")
        dialog.selectAllBtn.click()
        self.assertEqual(dialog.selected_ids(), ["a-hk-01", "a-hk-02", "a-us-01"])
        dialog.clearBtn.click()
        # The hidden US server keeps its earlier selection.
        self.assertEqual(dialog.selected_ids(), ["a-us-01"])

    def test_selection_survives_changing_the_search(self):
        dialog = self.make_dialog()
        dialog.searchEdit.setText("us")
        dialog._checks["a-us-01"].setChecked(True)
        dialog.searchEdit.setText("hk")
        self.assertEqual(dialog.selected_ids(), ["a-us-01"])
        self.assertIn("1 selected", dialog.selectedCount.text())

    def test_enter_in_search_box_does_not_apply_selection(self):
        dialog = self.make_dialog()
        accepted = []
        dialog.accepted.connect(lambda: accepted.append(True))
        dialog.searchEdit.setText("hk")
        for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            event = QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(dialog.searchEdit, event)
        self.app.processEvents()
        self.assertEqual(accepted, [])
        self.assertEqual(dialog.visible_ids(), ["a-hk-01", "a-hk-02"])

    def test_empty_agent_list_disables_search(self):
        dialog = self.make_dialog(agents=[])
        self.assertFalse(dialog.searchEdit.isEnabled())
        self.assertTrue(dialog.noMatchLabel.isHidden())

    def test_rows_show_os_details_and_status(self):
        dialog = self.make_dialog()
        hk1, hk2, us = (dialog._rows[agent_id] for agent_id in ("a-hk-01", "a-hk-02", "a-us-01"))
        self.assertEqual((hk1.os_name, hk2.os_name, us.os_name), ("linux", "", "windows"))
        self.assertEqual(hk1.title, "server-hk-01")
        self.assertEqual(hk1.details, "Linux · vps-hongkong · a-hk-01")
        self.assertEqual(hk2.details, "Unknown OS · vps-hongkong-2 · a-hk-02")
        self.assertEqual((hk1.status, hk2.status), ("online", "offline"))

    def test_english_rows_have_no_chinese_text(self):
        dialog = self.make_dialog()
        han = re.compile(r"[㐀-鿿]")
        for row in dialog._rows.values():
            for text in (row.title, row.details, row.status, row.toolTip()):
                self.assertIsNone(han.search(text), text)

    def test_clicking_a_row_toggles_its_checkbox(self):
        dialog = self.make_dialog()
        dialog.parent().show()
        dialog.show()
        self.app.processEvents()
        row = dialog._rows["a-us-01"]
        self.assertGreater(row.width(), 200)
        self.assertFalse(row.check.isChecked())
        # Click on the server name, well away from the checkbox itself.
        point = QPoint(row.width() // 2, row.height() // 2)
        QTest.mouseClick(row, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
        self.assertTrue(row.check.isChecked())
        self.assertEqual(dialog.selected_ids(), ["a-us-01"])
        QTest.mouseClick(row, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
        self.assertEqual(dialog.selected_ids(), [])

    def test_rows_render_in_light_and_dark_themes(self):
        was_dark = isDarkTheme()
        self.addCleanup(setTheme, Theme.DARK if was_dark else Theme.LIGHT)
        for theme in (Theme.LIGHT, Theme.DARK):
            setTheme(theme)
            dialog = self.make_dialog(selected=["a-hk-01"])
            for row in dialog._rows.values():
                row.resize(560, row.HEIGHT)
                with self.subTest(theme=theme, agent=row.agent["agent_id"]):
                    self.assertFalse(row.grab().isNull())


if __name__ == "__main__":
    unittest.main()
