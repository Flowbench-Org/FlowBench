"""Layout and lifetime checks for the UI polish pass (shortcuts, form alignment, leaks)."""
import copy
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("APPDATA", os.path.join(tempfile.gettempdir(), "FlowBench-ui-polish-tests"))

from PySide6.QtCore import QAbstractAnimation, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPalette
from PySide6.QtWidgets import QApplication, QWidget
from qfluentwidgets import Theme, setTheme

from app.services.settings import settings
from app.ui.charts import HoverChart
from app.ui.main_window import MainWindow


class UiPolishTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.saved_settings = copy.deepcopy(settings._data)
        settings._data.update({
            "language": "zh-CN", "theme": "light", "animations_enabled": False,
            "minimize_to_tray": False, "auto_check_updates": False,
            "stress_form": {}, "authorized": [],
        })
        setTheme(Theme.LIGHT)
        self.market_patch = patch("app.services.market.MarketClient.fetch_index")
        self.market_patch.start()
        self.window = None

    def tearDown(self):
        if self.window is not None:
            self.window.tray_icon.hide()
            self.window.close()
            self.window.deleteLater()
        self.settle()
        self.market_patch.stop()
        settings._data.clear()
        settings._data.update(self.saved_settings)

    def settle(self, cycles=8):
        for _ in range(cycles):
            self.app.processEvents()
            self.app.sendPostedEvents(None, QEvent.DeferredDelete)

    def wait(self, ms):
        from PySide6.QtTest import QTest
        QTest.qWait(ms)
        self.settle()

    def make_window(self, animations=False):
        settings._data["animations_enabled"] = animations
        window = MainWindow()
        window.tray_icon.hide()
        window.setAttribute(Qt.WA_DontShowOnScreen, True)
        window.resize(1360, 860)
        window.show()
        window.market.marketPage._on_index([], False)
        self.settle()
        self.window = window
        return window

    def pages(self, window):
        return [window.dashboard, window.stress, window.collab, window.agentView,
                window.monitor, window.market, window.settingsView]

    def test_page_reveal_animations_are_freed(self):
        window = self.make_window(animations=True)

        def animations():
            return len(window.findChildren(QAbstractAnimation))

        counts = []
        for _round in range(3):
            for page in self.pages(window):
                window.switchTo(page)
                self.wait(1400)
            counts.append(animations())
        # Before the fix every round added ~800 objects that were never deleted.
        self.assertLess(counts[2] - counts[0], 100, counts)
        self.wait(1500)     # let the last page's watchdog restore everything
        self.assertEqual(window._control_reveals, [])
        self.assertEqual([w for w in window.settingsView.findChildren(QWidget)
                          if w.graphicsEffect() is not None
                          and type(w.graphicsEffect()).__name__ == "QGraphicsOpacityEffect"], [])

    def test_page_shortcuts_follow_sidebar_order(self):
        window = self.make_window()
        expected = {
            "Ctrl+1": window.dashboard, "Ctrl+2": window.stress,
            "Ctrl+3": window.collab, "Ctrl+4": window.agentView,
            "Ctrl+5": window.monitor, "Ctrl+6": window.market,
            "Ctrl+,": window.settingsView,
        }
        shortcuts = {s.key().toString(): s for s in window._page_shortcuts}
        self.assertEqual(set(shortcuts), set(expected))
        for key, page in expected.items():
            with self.subTest(key=key):
                window.switchTo(window.dashboard if page is not window.dashboard else window.stress)
                self.settle()
                shortcuts[key].activated.emit()
                self.settle()
                self.assertIs(window.stackedWidget.currentWidget(), page)

    def test_stress_form_controls_share_one_height(self):
        stress = self.make_window().stress
        self.assertEqual(stress.protoCombo.height(), stress.portSpin.height())
        self.assertEqual(stress.durUnitCombo.height(), stress.durSpin.height())
        # Labels above the port and protocol boxes start on the same line.
        port_top = stress.portSpin.mapTo(stress, QPoint(0, 0)).y()
        proto_top = stress.protoCombo.mapTo(stress, QPoint(0, 0)).y()
        self.assertEqual(port_top, proto_top)

    def test_last_error_is_red_only_when_there_is_an_error(self):
        stress = self.make_window().stress

        def color():
            self.settle()
            stress.errLabel.ensurePolished()
            return stress.errLabel.palette().color(QPalette.WindowText)

        idle = color()
        self.assertLess(abs(idle.red() - idle.green()), 40, idle.name())
        stress._set_last_error("timeout")
        self.assertIn("timeout", stress.errLabel.text())
        failed = color()
        self.assertGreater(failed.red(), 150, failed.name())
        self.assertLess(failed.green(), 100, failed.name())
        stress._set_last_error("")
        self.assertLess(abs(color().red() - color().green()), 40)

    def test_settings_number_boxes_line_up(self):
        view = self.make_window().settingsView
        spins = (view.threadSpin, view.timeoutSpin, view.rateSpin,
                 view.durationSpin, view.packetSizeSpin)
        self.assertEqual(len({spin.width() for spin in spins}), 1)
        rights = {spin.mapTo(view, QPoint(spin.width(), 0)).x() for spin in spins}
        self.assertEqual(len(rights), 1)

    def test_agent_connection_fields_share_a_column(self):
        window = self.make_window()
        window.resize(1080, 700)
        window.switchTo(window.agentView)
        self.settle()
        agent = window.agentView
        left = {w.mapTo(agent, QPoint(0, 0)).x() for w in (agent.hubEdit, agent.tokenEdit, agent.agentCombo)}
        self.assertEqual(len(left), 1)
        self.assertEqual(agent.hubEdit.width(), agent.tokenEdit.width())
        # The URL box used to share its row with the token box (~140 px).
        self.assertGreater(agent.hubEdit.width(), 300)

    def test_search_drop_down_uses_a_flippable_chevron(self):
        from app.ui import market_view
        page = self.make_window().market.marketPage
        self.assertIs(page.searchDropDownBtn._icon, market_view._CHEVRON_DOWN)

        def draw(icon):
            image = QImage(16, 16, QImage.Format_ARGB32)
            image.fill(Qt.transparent)
            painter = QPainter(image)
            icon.render(painter, image.rect(), fill="#000000")
            painter.end()
            return image

        def spread(image, rows):
            """Horizontal extent of the ink within the given rows."""
            xs = [x for y in rows for x in range(image.width())
                  if image.pixelColor(x, y).alpha() > 60]
            return (max(xs) - min(xs)) if xs else 0

        down, up = draw(market_view._CHEVRON_DOWN), draw(market_view._CHEVRON_UP)
        top, bottom = range(0, 8), range(8, 16)
        # A "v" is wide at the top and narrow at the bottom; "^" the reverse.
        self.assertGreater(spread(down, top), spread(down, bottom))
        self.assertGreater(spread(up, bottom), spread(up, top))

    def test_finished_report_replaces_the_live_numbers(self):
        stress = self.make_window().stress
        stress._on_snapshot({"total": 306, "success": 50, "fail": 256, "tx": 1000,
                             "qps": 90.0, "avg": 120.0, "active": 12, "progress": 0.9,
                             "last_error": "refused",
                             "targets": [{"host": "127.0.0.1", "success": 50, "fail": 0, "qps": 17.0}]})
        report = {"target": "2", "protocol": "MIX", "duration": 4.0, "total": 322,
                  "success": 57, "fail": 265, "avg": 154.6, "p50": 98.5, "p90": 488.5,
                  "p99": 832.5, "traffic_mb": 0.0, "bytes_tx": 18084, "rate_limit": 150.0,
                  "errors": {"refused": 197, "HTTP 500": 68},
                  "targets": [
                      {"target": "127.0.0.1", "protocol": "HTTP", "duration": 4.0, "total": 57,
                       "success": 57, "fail": 0, "avg": 311.8, "p50": 1, "p90": 1, "p99": 1,
                       "bytes_tx": 8151, "rate_limit": 50.0, "errors": {}},
                      {"target": "127.0.0.3", "protocol": "HTTP", "duration": 4.0, "total": 265,
                       "success": 0, "fail": 265, "avg": 46.5, "p50": 1, "p90": 1, "p99": 1,
                       "bytes_tx": 9933, "rate_limit": 50.0, "errors": {"refused": 265}}]}
        stress._on_report(report)
        self.assertEqual((stress.mTotal.text(), stress.mSuccess.text(), stress.mFail.text()),
                         ("322", "57", "265"))
        self.assertEqual(stress.mActive.text(), "0")
        self.assertEqual(stress.mQps.text(), "0.0")
        self.assertIn("127.0.0.1: ✓57 ✗0", stress.targetsLabel.text())
        text = stress.reportLabel.text()
        self.assertIn("http://127.0.0.1", text)
        self.assertNotIn("HTTP://", text)
        self.assertIn("速率上限 150 QPS", text)

    def test_stale_update_check_does_not_unlock_a_running_one(self):
        view = self.make_window().settingsView
        callbacks = []
        with patch("app.services.updater.check_for_updates",
                   lambda parent=None, manual=False, on_finished=None: callbacks.append(on_finished)):
            view._check_update()
            callbacks[0]()
            self.assertTrue(view.updateBtn.isEnabled())
            view._check_update()
            self.assertFalse(view.updateBtn.isEnabled())
            callbacks[0]()          # e.g. the first check's 10 s fallback
            self.assertFalse(view.updateBtn.isEnabled())
            callbacks[1]()
            self.assertTrue(view.updateBtn.isEnabled())

    def test_chart_plot_area_uses_the_card_height(self):
        chart = HoverChart(window_points=60, y_min=0.0)
        chart.add_series("CPU %", "#0078D4")
        chart.resize(800, 200)
        chart.show()
        self.settle()
        plot = chart.chart().plotArea()
        self.assertGreater(plot.height(), 115, plot)
        chart.deleteLater()


class ChartZoomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        import math
        self.chart = HoverChart(window_points=60, y_min=0.0)
        self.chart.add_series("CPU %", "#0078D4", " %")
        self.chart.resize(900, 260)
        self.chart.show()
        self.data = [30 + 20 * math.sin(i / 5) for i in range(60)]
        self.chart.set_data({"CPU %": self.data})
        self.wait(50)

    def tearDown(self):
        self.chart.close()
        self.chart.deleteLater()
        self.wait(10)

    def wait(self, ms):
        from PySide6.QtTest import QTest
        QTest.qWait(ms)

    def wheel(self, x_frac, notches):
        from PySide6.QtGui import QWheelEvent
        plot = self.chart.chart().plotArea()
        pos = QPointF(plot.left() + plot.width() * x_frac, plot.center().y())
        event = QWheelEvent(pos, self.chart.mapToGlobal(pos), QPoint(), QPoint(0, int(120 * notches)),
                            Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
        QApplication.sendEvent(self.chart.viewport(), event)
        return event.isAccepted()

    def drag(self, x_from, x_to):
        from PySide6.QtTest import QTest
        viewport = self.chart.viewport()
        y = int(self.chart.chart().plotArea().center().y())
        QTest.mousePress(viewport, Qt.LeftButton, Qt.NoModifier, QPoint(x_from, y))
        QTest.mouseMove(viewport, QPoint(x_to, y))
        QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier, QPoint(x_to, y))

    def test_box_zoom_is_gone(self):
        from PySide6.QtCharts import QChartView
        self.assertEqual(self.chart.rubberBand(), QChartView.RubberBand.NoRubberBand)
        self.drag(200, 600)      # not zoomed: a drag changes nothing
        self.assertEqual(self.chart.visible_range(), (0.0, 60.0))
        self.assertFalse(self.chart.is_zoomed())

    def test_wheel_zooms_one_step_around_the_cursor_with_animation(self):
        from app.ui.charts import ZOOM_MS
        self.assertTrue(self.wheel(0.75, 1))
        # Animated, not a jump. Checked without a fixed sleep, which a busy
        # machine can overrun before the first frame is drawn.
        ani = self.chart._zoom_ani
        self.assertEqual(ani.state(), QAbstractAnimation.State.Running)
        self.assertEqual(ani.duration(), ZOOM_MS)
        x0, x1 = self.chart.visible_range()
        self.assertGreater(x1 - x0, 48.5, (x0, x1))           # not at the target yet
        for _ in range(250):                                  # up to 5 s
            if ani.state() == QAbstractAnimation.State.Stopped:
                break
            self.wait(20)
        self.assertEqual(ani.state(), QAbstractAnimation.State.Stopped)
        x0, x1 = self.chart.visible_range()
        self.assertAlmostEqual(x1 - x0, 48.0, places=3)      # one notch = 80 %
        self.assertAlmostEqual(x0, 9.0, delta=0.05)          # 45 stays under the cursor
        self.assertTrue(self.chart.is_zoomed())

    def test_zoomed_chart_pans_back_and_keeps_its_range_on_new_data(self):
        self.wheel(0.5, 3)
        self.wait(400)
        x0, x1 = self.chart.visible_range()
        span = x1 - x0
        self.drag(300, 600)                                  # drag right = go back in time
        self.assertLess(self.chart.visible_range()[0], x0)
        self.drag(300, 3000)                                 # far past the start
        self.assertAlmostEqual(self.chart.visible_range()[0], 0.0, places=6)
        self.assertAlmostEqual(self.chart.visible_range()[1] - 0.0, span, places=6)
        self.drag(600, 300)
        moved = self.chart.visible_range()
        self.assertGreater(moved[0], 0.0)
        self.chart.set_data({"CPU %": self.data[1:] + [90.0]})
        self.assertEqual(self.chart.visible_range(), moved)

    def test_double_click_and_zooming_out_resume_live_view(self):
        from PySide6.QtTest import QTest
        self.wheel(0.5, 2)
        self.wait(400)
        QTest.mouseDClick(self.chart.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(400, 120))
        self.wait(400)
        self.assertEqual(self.chart.visible_range(), (0.0, 60.0))
        self.assertFalse(self.chart.is_zoomed())
        self.wheel(0.5, 1)
        self.wait(400)
        self.wheel(0.5, -6)                                  # zooming out past the full view
        self.wait(400)
        self.assertEqual(self.chart.visible_range(), (0.0, 60.0))
        self.assertFalse(self.chart.is_zoomed())
        # Already showing everything: the wheel scrolls the page instead.
        self.assertFalse(self.wheel(0.5, -1))


class ReportDurationTests(unittest.TestCase):
    def test_duration_excludes_waiting_for_in_flight_requests(self):
        """A request still blocked at the planned end must not stretch the test."""
        import socket
        import threading
        from app.services.stress import StressEngine

        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(8)
        accepted = []
        threading.Thread(target=lambda: accepted.append(server.accept()), daemon=True).start()
        engine = StressEngine()
        reports = []
        engine.report_ready.connect(reports.append, Qt.DirectConnection)
        try:
            self.assertTrue(engine.start({
                "target": "127.0.0.1", "port": server.getsockname()[1], "protocol": "TCP",
                "threads": 1, "rate": 100, "duration": 1, "packet_size": 8,
                "timeout": 5000}))   # the server never answers: recv() blocks 5 s
            import time
            deadline = time.monotonic() + 10
            while not reports and time.monotonic() < deadline:
                time.sleep(0.05)
        finally:
            engine.stop()
            server.close()
        self.assertTrue(reports)
        self.assertGreaterEqual(reports[0]["duration"], 0.9)
        self.assertLess(reports[0]["duration"], 1.4, reports[0])


if __name__ == "__main__":
    unittest.main()
