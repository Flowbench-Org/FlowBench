"""InfoBars wrap by pixel width and stay inside their window."""
import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("APPDATA", os.path.join(tempfile.gettempdir(), "FlowBench-infobar-tests"))

from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from qfluentwidgets import HyperlinkButton, InfoBar, InfoBarIcon

from app.ui import info_bar_fit

URL = "https://api.github.com/repos/Carlown/FlowBench/releases/latest"
LONG = ("无法连接 GitHub：403 Client Error: rate limit exceeded for url: " + URL
        + "，请检查网络连接、代理设置或者稍后再试。")


class WrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.metrics = QFontMetrics(QFont("Segoe UI", 10))

    def test_lines_fit_and_keep_every_character(self):
        for width in (60, 140, 300, 900):
            with self.subTest(width=width):
                lines = info_bar_fit.wrap_to_width(LONG, self.metrics, width)
                for line in lines:
                    self.assertLessEqual(self.metrics.horizontalAdvance(line), width, line)
                self.assertEqual("".join(lines).replace(" ", ""), LONG.replace(" ", ""))

    def test_urls_break_after_a_slash(self):
        width = self.metrics.horizontalAdvance("https://api.github.com/repos/")
        lines = info_bar_fit.wrap_to_width(URL, self.metrics, width)
        self.assertGreater(len(lines), 1)
        for line in lines[:-1]:
            self.assertTrue(line.endswith("/"), line)

    def test_line_breaks_in_the_message_are_kept(self):
        self.assertEqual(info_bar_fit.wrap_to_width("第一行\n第二行", self.metrics, 500),
                         ["第一行", "第二行"])


class FittedInfoBarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        info_bar_fit.install()

    def parent(self, width):
        parent = QWidget()
        parent.resize(width, 500)
        parent.show()
        self.addCleanup(parent.deleteLater)
        return parent

    def assertInside(self, parent, bar):
        QTest.qWait(500)                                     # slide-in finished
        self.assertTrue(parent.rect().contains(bar.geometry()), (bar.geometry(), parent.width()))
        close = bar.closeButton.geometry().translated(bar.pos())
        self.assertTrue(parent.rect().contains(close))

    def test_long_messages_stay_inside_narrow_and_wide_windows(self):
        for width in (520, 620, 900, 1400):
            with self.subTest(width=width):
                parent = self.parent(width)
                bar = InfoBar.error("测试失败", LONG, parent=parent, duration=-1)
                self.assertInside(parent, bar)

    def test_a_link_added_before_showing_is_made_room_for(self):
        parent = self.parent(620)
        bar = InfoBar(InfoBarIcon.ERROR, "检查更新失败", LONG, parent=parent, duration=-1)
        bar.addWidget(HyperlinkButton(URL, "打开发布页", bar))
        bar.show()
        self.assertInside(parent, bar)

    def test_window_resize_rewraps(self):
        parent = self.parent(1400)
        bar = InfoBar.error("测试失败", LONG, parent=parent, duration=-1)
        QTest.qWait(300)
        wide_lines = bar.contentLabel.text().count("\n")
        parent.resize(620, 500)
        QTest.qWait(300)
        self.assertGreater(bar.contentLabel.text().count("\n"), wide_lines)
        self.assertInside(parent, bar)


if __name__ == "__main__":
    unittest.main()
