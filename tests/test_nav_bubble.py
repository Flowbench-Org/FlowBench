"""Sidebar name bubbles while the sidebar is collapsed to icons."""
import copy
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("APPDATA", os.path.join(tempfile.gettempdir(), "FlowBench-nav-bubble-tests"))

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QEnterEvent, QHelpEvent, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from qfluentwidgets import FluentIcon, NavigationDisplayMode, NavigationItemPosition, Theme, setTheme
from qfluentwidgets.components.widgets.tool_tip import ToolTipFilter

from app.services.settings import settings
from app.ui import nav_bubble
from app.ui.main_window import MainWindow


class NavBubbleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.saved = copy.deepcopy(settings._data)
        settings._data.update({"language": "zh-CN", "theme": "light", "animations_enabled": False,
                               "minimize_to_tray": False, "auto_check_updates": False})
        setTheme(Theme.LIGHT)
        market = patch("app.services.market.MarketClient.fetch_index")
        market.start()
        self.addCleanup(market.stop)
        self.window = MainWindow()
        self.window.tray_icon.hide()
        self.window.resize(1100, 700)
        self.window.show()
        QTest.qWait(50)
        self.panel = self.window.navigationInterface.panel
        self.bubbles = self.window._nav_bubbles
        self.bubble = self.bubbles.bubble

    def tearDown(self):
        self.bubbles.hide()
        self.window.close()
        self.window.deleteLater()
        QTest.qWait(10)
        settings._data.clear()
        settings._data.update(self.saved)

    def item(self, page):
        return self.panel.items[page.objectName()].widget

    def enter(self, widget):
        point = QPointF(5, 5)
        QApplication.sendEvent(widget, QEnterEvent(point, point, point))

    def leave(self, widget):
        QApplication.sendEvent(widget, QEvent(QEvent.Leave))

    def wait_until(self, condition, timeout_ms=3000):
        """Process events until ``condition()``; a busy machine delays timers."""
        for _ in range(timeout_ms // 20):
            if condition():
                return True
            QTest.qWait(20)
        return condition()

    def test_hover_shows_the_name_at_once_pointing_at_the_icon(self):
        self.assertEqual(self.panel.displayMode, NavigationDisplayMode.COMPACT)
        target = self.item(self.window.stress)
        self.enter(target)
        self.assertTrue(self.bubble.isVisible())                  # no delay
        self.assertEqual(self.bubble.text(), "压力测试")
        tip = self.bubble.mapToGlobal(self.bubble.tip_offset())
        edge = target.mapToGlobal(QPoint(target.width(), target.height() // 2))
        self.assertEqual(tip.y(), edge.y())
        self.assertEqual(tip.x() - edge.x(), nav_bubble.GAP)
        # The pointer really is drawn: its tip is inside the bubble's outline.
        self.assertTrue(self.bubble.bubble_path().contains(QPointF(self.bubble.tip_offset()) + QPointF(1.5, 0)))

    def test_moving_down_the_sidebar_keeps_one_bubble(self):
        home, stress = self.item(self.window.dashboard), self.item(self.window.stress)
        self.enter(home)
        self.leave(home)
        self.assertTrue(self.bubble.isVisible())                  # held for the next item
        self.enter(stress)
        self.assertEqual(self.bubble.text(), "压力测试")
        self.assertIs(self.bubble.target, stress)
        QTest.qWait(nav_bubble.SWITCH_MS + 80)
        self.assertTrue(self.bubble.isVisible())

    def test_leaving_or_clicking_hides_it(self):
        home = self.item(self.window.dashboard)
        self.enter(home)
        self.leave(home)
        self.assertTrue(self.wait_until(lambda: not self.bubble.isVisible()))
        self.enter(home)
        self.assertTrue(self.bubble.isVisible())
        press = QMouseEvent(QEvent.MouseButtonPress, QPointF(5, 5), QPointF(5, 5),
                            Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(home, press)
        self.assertFalse(self.bubble.isVisible())

    def test_replaces_the_delayed_tooltip(self):
        for widget in [self.panel.menuButton] + [i.widget for i in self.panel.items.values()]:
            with self.subTest(widget=self.bubbles._text_for(widget)):
                self.assertEqual(widget.findChildren(ToolTipFilter, options=Qt.FindDirectChildrenOnly), [])
                event = QHelpEvent(QEvent.ToolTip, QPoint(5, 5), widget.mapToGlobal(QPoint(5, 5)))
                self.assertTrue(QApplication.sendEvent(widget, event))   # swallowed

    def test_expanded_sidebar_shows_no_bubble(self):
        home = self.item(self.window.dashboard)
        self.enter(home)
        self.assertTrue(self.bubble.isVisible())
        self.panel.expand(useAni=False)
        QTest.qWait(30)
        self.assertNotEqual(self.panel.displayMode, NavigationDisplayMode.COMPACT)
        self.assertFalse(self.bubble.isVisible())                 # expanding hides it
        self.enter(home)
        self.assertFalse(self.bubble.isVisible())

    def test_menu_button_and_pages_added_later(self):
        self.enter(self.panel.menuButton)
        self.assertEqual(self.bubble.text(), self.panel.menuButton.toolTip())
        page = QWidget()
        page.setObjectName("plugin_demo")
        self.window.addSubInterface(page, FluentIcon.APPLICATION, "演示插件",
                                    NavigationItemPosition.SCROLL)
        self.enter(self.item(page))
        self.assertEqual(self.bubble.text(), "演示插件")

    def test_dark_and_light_paint(self):
        self.enter(self.item(self.window.dashboard))
        light = self.bubble.grab().toImage()
        setTheme(Theme.DARK)
        self.bubble.update()
        dark = self.bubble.grab().toImage()
        center = QPoint(self.bubble.width() // 2 + nav_bubble.TAIL_W, 9)
        self.assertGreater(light.pixelColor(center).lightness(), 200)
        self.assertLess(dark.pixelColor(center).lightness(), 90)


if __name__ == "__main__":
    unittest.main()
