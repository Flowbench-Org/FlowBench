"""Local plugins are cards like the marketplace's, with tidy actions, and the list scrolls."""
import copy
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("APPDATA", os.path.join(tempfile.gettempdir(), "FlowBench-local-card-tests"))

from PySide6.QtCore import QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from qfluentwidgets import SimpleCardWidget, Theme, setTheme

from app.services import plugins
from app.services.settings import settings


class _Rec:
    def __init__(self, pid, name, state, version="1.0", author="a", desc="", error=None):
        self.pid, self.display_name, self.display_version = pid, name, version
        self.display_author, self.display_description = author, desc
        self.state, self.error = state, error
        self.plugin, self.display_icon = None, ""
        self.path = f"/nonexistent/{pid}/main.py"


def _records():
    return [
        _Rec("jp", ("日语语言包", "Japanese"), "loaded", "1.0.7", "FlowBench", ("日语界面", "Japanese UI")),
        _Rec("notes", ("快速笔记", "Notes"), "disabled", "v0.3"),
        _Rec("dns", ("DNS 探测", "DNS"), "error", "2.1",
             error="ModuleNotFoundError: No module named 'dnspython'\nTraceback (most recent call last)"),
        _Rec("bare", "bare", "unloaded", "", author=""),
    ]


class LocalPluginCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.saved = copy.deepcopy(settings._data)
        settings._data["language"] = "zh-CN"
        self.records = _records()
        discover = patch.object(plugins.plugin_manager, "discover", side_effect=lambda: self.records)
        discover.start()
        self.addCleanup(discover.stop)

    def tearDown(self):
        settings._data.clear()
        settings._data.update(self.saved)
        setTheme(Theme.LIGHT)

    def page(self, width=900, height=560):
        from app.ui.market_view import LocalPluginsPage
        page = LocalPluginsPage()
        page.resize(width, height)
        page.show()
        self.addCleanup(page.deleteLater)
        self.addCleanup(page.close)
        QTest.qWait(150)
        return page

    @staticmethod
    def cards(page):
        from app.ui.market_view import LocalPluginCard
        found = []
        for i in range(page.listLay.count()):
            widget = page.listLay.itemAt(i).widget()
            if isinstance(widget, LocalPluginCard):
                found.append(widget)
        return found

    def test_every_plugin_is_its_own_card(self):
        cards = self.cards(self.page())
        self.assertEqual([c._rec.pid for c in cards], ["jp", "notes", "dns", "bare"])
        for card in cards:
            self.assertIsInstance(card, SimpleCardWidget)
        self.assertEqual([(c._title_label.text(), c._version_label.text(), c._state_pill.text())
                          for c in cards],
                         [("日语语言包", "v1.0.7", "运行中"), ("快速笔记", "v0.3", "已禁用"),
                          ("DNS 探测", "v2.1", "加载失败"), ("bare", "", "未加载")])
        self.assertTrue(cards[3]._version_label.isHidden())
        self.assertTrue(cards[3]._desc_label.isHidden())
        # Cards are spaced apart instead of touching.
        self.assertGreaterEqual(cards[1].y() - cards[0].geometry().bottom(), 8)

    def test_actions_sit_in_one_centred_row_aligned_across_cards(self):
        page = self.page()
        cards = self.cards(page)
        lefts = set()
        for card in cards:
            widgets = (card._reload_btn, card._del_btn, card._switch)
            centres = [w.mapTo(card, QPoint(0, w.height() // 2)).y() for w in widgets]
            self.assertLessEqual(max(centres) - min(centres), 1, card._rec.pid)
            self.assertLessEqual(abs(centres[0] - card.height() // 2), 2, card._rec.pid)
            xs = [w.mapTo(card, QPoint(0, 0)).x() for w in widgets]
            self.assertEqual(xs, sorted(xs))                   # reload, remove, then the switch
            switch_right = card._switch.mapTo(card, QPoint(card._switch.width(), 0)).x()
            self.assertLessEqual(card.width() - switch_right, 24)    # flush with the right edge
            self.assertFalse(card._switch.label.isVisible())
            lefts.add(tuple(xs))
        self.assertEqual(len(lefts), 1, lefts)

    def test_failed_plugin_shows_its_error_under_the_name(self):
        card = self.cards(self.page())[2]
        self.assertTrue(card._error_label.isVisible())
        self.assertEqual(card._error_label.text(),
                         "ModuleNotFoundError: No module named 'dnspython'")
        self.assertIn("Traceback", card._error_label.toolTip())
        # The error line belongs to the text column, right of the icon and left of the actions.
        error_x = card._error_label.mapTo(card, QPoint(0, 0)).x()
        self.assertGreater(error_x, card._icon_label.geometry().right())
        self.assertLess(card._error_label.mapTo(card, QPoint(card._error_label.width(), 0)).x(),
                        card.actions.x())
        self.assertFalse(self.cards(self.page())[0]._error_label.isVisible())

    def test_switch_enables_and_disables_and_updates_quietly(self):
        page = self.page()
        with patch.object(plugins.plugin_manager, "set_enabled") as set_enabled:
            notes = self.cards(page)[1]
            self.assertFalse(notes._switch.isChecked())
            notes._switch.setChecked(True)
            set_enabled.assert_called_once_with("notes", True)
            set_enabled.reset_mock()
            self.records[0].state = "disabled"
            page.refresh()
            set_enabled.assert_not_called()
        jp = self.cards(page)[0]
        self.assertFalse(jp._switch.isChecked())
        self.assertEqual(jp._state_pill.text(), "已禁用")
        self.assertEqual(jp._switch.toolTip(), "启用插件：日语语言包")

    def test_refresh_keeps_cards_and_drops_removed_plugins(self):
        page = self.page()
        before = self.cards(page)
        self.records = [self.records[2], self.records[0]]
        page.refresh()
        QTest.qWait(20)
        after = self.cards(page)
        self.assertEqual([c._rec.pid for c in after], ["dns", "jp"])
        self.assertIs(after[0], before[2])
        self.assertIs(after[1], before[0])

    def test_many_plugins_scroll_while_the_buttons_stay_put(self):
        self.records = [_Rec(f"p{i}", f"插件 {i}", "loaded") for i in range(30)]
        page = self.page(900, 520)
        bar = page.listScroll.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        last = self.cards(page)[-1]
        viewport = page.listScroll.viewport()
        self.assertGreater(last.mapTo(viewport, QPoint(0, 0)).y(), viewport.height())
        page.listScroll.verticalScrollBar().setValue(bar.maximum())
        deadline = 150
        while deadline and last.mapTo(viewport, QPoint(0, last.height())).y() > viewport.height():
            QTest.qWait(20)
            deadline -= 1
        self.assertLessEqual(last.mapTo(viewport, QPoint(0, last.height())).y(), viewport.height())
        # The warning and the Import / Open folder / Rescan row are outside the scrolling list.
        self.assertLessEqual(page.listScroll.geometry().bottom(), page.height())
        for child in page.findChildren(QWidget):
            if child.parent() is page and child is not page.listScroll and child.isVisible():
                self.assertFalse(page.listScroll.geometry().intersects(child.geometry()),
                                 child)

    def test_state_colours_follow_the_theme(self):
        card = self.cards(self.page())[0]
        light = card._state_pill.color().name()
        setTheme(Theme.DARK)
        self.assertNotEqual(card._state_pill.color().name(), light)
        error = self.cards(self.page())[2]._state_pill.color().name()
        self.assertNotIn(error, (light, card._state_pill.color().name()))

    def test_english_labels(self):
        settings._data["language"] = "en-US"
        card = self.cards(self.page())[0]
        self.assertEqual(card._title_label.text(), "Japanese")
        self.assertEqual(card._state_pill.text(), "Running")
        self.assertEqual(card._reload_btn.text(), "Reload")
        self.assertEqual(card._del_btn.text(), "Remove")
        self.assertEqual(card._switch.toolTip(), "Disable plugin: Japanese")


if __name__ == "__main__":
    unittest.main()
