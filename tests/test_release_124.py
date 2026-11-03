import ast
import copy
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("APPDATA", os.path.join(tempfile.gettempdir(), "FlowBench-release-124-tests"))

from PySide6.QtCore import QLocale
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QAbstractButton, QApplication, QComboBox,
                               QLabel, QLineEdit, QWidget)
from qfluentwidgets import (FluentTranslator, Theme, isDarkTheme, qconfig,
                            setTheme, setThemeColor)

from app.services.settings import settings
from app.services.updater import APP_VERSION


ROOT = Path(__file__).resolve().parents[1]
HAN_RE = re.compile(r"[\u3400-\u9fff]")


class Release124Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._settings = copy.deepcopy(settings._data)

    def tearDown(self):
        settings._data.clear()
        settings._data.update(self._settings)

    def test_release_version_is_consistent(self):
        self.assertEqual(APP_VERSION, "1.2.8")
        setup_spec = (ROOT / "Setup.spec").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        features = (ROOT / "PRODUCT_FEATURES.txt").read_text(encoding="utf-8")
        # The installer takes its version from payload.json, which
        # tools/make_payload.py reads from APP_VERSION.
        self.assertIn('name=f"FlowBench-Setup-{VERSION}"', setup_spec)
        self.assertFalse((ROOT / "installer.iss").exists())
        self.assertIn("FlowBench-Setup-1.2.8.exe", readme)
        self.assertIn("(v1.2.8)", features.splitlines()[0])

    def test_all_translation_calls_have_chinese_and_english_text(self):
        failures = []
        sources = list((ROOT / "app").rglob("*.py")) + [ROOT / "main.py"]
        for path in sources:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if isinstance(node.func, ast.Name) and node.func.id == "L":
                    if len(node.args) < 2:
                        failures.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual(failures, [], "L() calls missing a translation: " + ", ".join(failures))

    def test_english_main_window_has_no_visible_chinese_text(self):
        from app.ui.main_window import MainWindow

        settings._data["language"] = "en-US"
        settings._data["minimize_to_tray"] = False
        translator = FluentTranslator(QLocale("en_US"), self.app)
        self.app.installTranslator(translator)
        window = MainWindow()
        try:
            texts = []
            widgets = [window] + window.findChildren(QWidget)
            for widget in widgets:
                if isinstance(widget, (QLabel, QAbstractButton)):
                    texts.append((type(widget).__name__, widget.text()))
                if isinstance(widget, QLineEdit):
                    texts.append((type(widget).__name__ + " placeholder", widget.placeholderText()))
                if isinstance(widget, QComboBox):
                    for index in range(widget.count()):
                        texts.append((type(widget).__name__ + " item", widget.itemText(index)))
                if widget.toolTip():
                    texts.append((type(widget).__name__ + " tooltip", widget.toolTip()))
            for action in window.findChildren(QAction):
                texts.append(("QAction", action.text()))
                texts.append(("QAction tooltip", action.toolTip()))
            leaked = [(kind, text) for kind, text in texts if text and HAN_RE.search(text)]
            self.assertEqual(leaked, [], f"Chinese text leaked into English UI: {leaked[:20]}")
        finally:
            window.tray_icon.hide()
            window.close()
            window.deleteLater()
            self.app.removeTranslator(translator)
            self.app.processEvents()

    def test_theme_switch_reapplies_saved_accent(self):
        from app.ui.settings_view import SettingsView

        settings._data["theme"] = "light"
        settings._data["theme_color"] = "#5B5FC7"
        view = SettingsView()
        try:
            with (patch("app.ui.settings_view.settings.set", return_value=True),
                  patch("app.ui.settings_view.setTheme") as set_theme,
                  patch("app.ui.settings_view.setThemeColor") as set_color):
                view._theme_changed(True)
            set_theme.assert_called_once()
            set_color.assert_called_once()
            self.assertEqual(set_color.call_args.args[0].name().upper(), "#5B5FC7")
        finally:
            view.deleteLater()

    def test_all_primary_pages_render_in_light_and_dark_modes(self):
        from app.ui.main_window import MainWindow

        settings._data["language"] = "en-US"
        settings._data["animations_enabled"] = False
        settings._data["minimize_to_tray"] = False
        old_dark = isDarkTheme()
        old_color = qconfig.themeColor.value
        window = MainWindow()
        window.resize(1360, 860)
        window.show()
        try:
            pages = (
                window.dashboard, window.stress, window.collab,
                window.agentView, window.monitor, window.market,
                window.settingsView,
            )
            for theme, expected_dark in ((Theme.LIGHT, False), (Theme.DARK, True)):
                setTheme(theme)
                setThemeColor("#5B5FC7")
                self.app.processEvents()
                self.assertEqual(isDarkTheme(), expected_dark)
                for page in pages:
                    window.switchTo(page)
                    self.app.processEvents()
                    image = page.grab().toImage()
                    self.assertFalse(image.isNull(), page.objectName())
                    self.assertGreater(image.width(), 0, page.objectName())
                    self.assertGreater(image.height(), 0, page.objectName())
        finally:
            window.tray_icon.hide()
            window.close()
            window.deleteLater()
            setTheme(Theme.DARK if old_dark else Theme.LIGHT)
            setThemeColor(old_color)
            self.app.processEvents()

    def test_local_stop_cancels_queued_start(self):
        from app.ui.stress_view import StressView

        settings._data["language"] = "en-US"

        class FakeEngine:
            running = False

            def __init__(self):
                self.started = 0
                self.stopped = 0

            def start(self, _configs):
                self.started += 1
                return True

            def stop(self):
                self.stopped += 1

        view = StressView()
        fake = FakeEngine()
        try:
            view._startup_configs = [{"target": "example.test"}]
            view._previous_report = None
            view._previous_report_text = ""
            with patch("app.ui.stress_view.engine", fake):
                view._stop()
                view._do_start_engine()
            self.assertEqual(fake.started, 0)
            self.assertEqual(fake.stopped, 0)
            self.assertTrue(view.startBtn.isEnabled())
            self.assertFalse(view.stopBtn.isEnabled())
            self.assertEqual(view.reportLabel.text(), "No test executed yet.")
        finally:
            view.deleteLater()

    def test_remote_stop_cancels_queued_start(self):
        from app.ui.collab_view import CollabView

        class FakeEngine:
            running = False

            def __init__(self):
                self.started = 0
                self.stopped = 0

            def start(self, _configs):
                self.started += 1
                return True

            def stop(self):
                self.stopped += 1

        view = CollabView()
        fake = FakeEngine()
        try:
            view._remote_start_config = {"target": "example.test"}
            with patch("app.ui.collab_view.engine", fake):
                view._on_remote_stop()
                view._do_remote_start()
            self.assertEqual(fake.started, 0)
            self.assertEqual(fake.stopped, 1)
            self.assertIsNone(view._remote_start_config)
        finally:
            view.deleteLater()

    def test_importing_installed_folder_plugin_keeps_source(self):
        from app.services.plugins import PluginManager

        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"APPDATA": directory}):
                folder = Path(directory) / "FlowBench" / "plugins" / "demo"
                folder.mkdir(parents=True)
                source = folder / "main.py"
                source.write_text(
                    "class DemoPlugin(FlowBenchPlugin):\n"
                    "    name = ('\u6f14\u793a', 'Demo')\n",
                    encoding="utf-8",
                )
                manager = PluginManager()
                ok, message = manager.import_from(str(folder))
                self.assertTrue(ok, message)
                self.assertTrue(source.is_file())
                self.assertIsNotNone(manager.record("demo"))
                manager.unload("demo")


if __name__ == "__main__":
    unittest.main()
