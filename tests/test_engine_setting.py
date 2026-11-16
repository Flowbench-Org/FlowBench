"""The engine setting: the settings page's selector, its status line, backups."""
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.services.settings import AppSettings, settings
from app.ui.settings_view import ENGINE_MODES, SettingsView


class EngineSettingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.addCleanup(settings.set, "engine", settings.engine)

    def _view(self):
        view = SettingsView()
        self.addCleanup(view.deleteLater)
        return view

    def test_selector_saves_each_mode(self):
        view = self._view()
        self.assertEqual(view.engineCombo.count(), len(ENGINE_MODES))
        for index in (2, 1, 0):
            view.engineCombo.setCurrentIndex(index)
            self.assertEqual(settings.engine, ENGINE_MODES[index])

    def test_status_says_whether_the_native_engine_is_there(self):
        view = self._view()
        with mock.patch("agent.engine_bridge.find_engine_binary",
                        return_value="/opt/flowbench/flowbench-engine"):
            view._refresh_engine_status()
            self.assertIn("/opt/flowbench/flowbench-engine", view.engineStatus.text())
        with mock.patch("agent.engine_bridge.find_engine_binary", return_value=None):
            view.engineCombo.setCurrentIndex(ENGINE_MODES.index("rust"))
            rust_text = view.engineStatus.text()   # warns HTTP tests cannot start
            view.engineCombo.setCurrentIndex(ENGINE_MODES.index("auto"))
            auto_text = view.engineStatus.text()
        self.assertIn("Rust", rust_text)
        self.assertIn("Python", auto_text)
        self.assertNotEqual(rust_text, auto_text)

    def test_engine_is_a_safe_backup_preference(self):
        self.assertIn("engine", AppSettings.SAFE_BACKUP_KEYS)
        for mode in ENGINE_MODES:
            self.assertEqual(AppSettings._validate_safe_value("engine", mode), mode)
        for bad in ("RUST", "", None, 1):
            with self.assertRaises(ValueError):
                AppSettings._validate_safe_value("engine", bad)


if __name__ == "__main__":
    unittest.main()
