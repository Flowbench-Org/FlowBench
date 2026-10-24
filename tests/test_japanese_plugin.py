import ast
import copy
import importlib.util
import os
import re
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("APPDATA", os.path.join(tempfile.gettempdir(), "FlowBench-japanese-tests"))

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPlainTextEdit, QTextEdit, QWidget
from qfluentwidgets import ComboBox, SwitchButton, Theme, isDarkTheme, setTheme

import app.services.plugins as plugins_module
import app.ui.disclaimer as disclaimer_module
import app.ui.i18n as i18n
import app.ui.main_window as main_window_module
from app.services.settings import settings
from app.ui.agent_view import GenerateNodeDialog
from app.ui.disclaimer import AuthDialog, DisclaimerDialog
from app.ui.main_window import MainWindow
from app.ui.market_view import PublishDialog


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_PATH = ROOT / "marketplace" / "japanese.py"


def load_module():
    name = "flowbench_plugin_japanese_test"
    spec = importlib.util.spec_from_file_location(name, PLUGIN_PATH)
    module = importlib.util.module_from_spec(spec)
    module.FlowBenchPlugin = plugins_module.FlowBenchPlugin
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def substitute(template, values):
    return re.sub(r"\{#(\d+)\}", lambda match: values[int(match.group(1))], template)


def settle(application):
    for cycle in range(8):
        application.processEvents()
        application.sendPostedEvents(None, QEvent.DeferredDelete)


class JapanesePluginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        if not QFontDatabase.families():
            font_directory = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
            for filename in ("segoeui.ttf", "segoeuib.ttf", "msyh.ttc", "msyhbd.ttc", "YuGothR.ttc"):
                if (font_directory / filename).is_file():
                    QFontDatabase.addApplicationFont(str(font_directory / filename))
            cls.application.setFont(QFont("Segoe UI", 9))

    def setUp(self):
        self.saved_settings = copy.deepcopy(settings._data)
        self.saved_dark = isDarkTheme()
        self.module = load_module()

    def tearDown(self):
        self.module.uninstall()
        settings._data.clear()
        settings._data.update(self.saved_settings)
        setTheme(Theme.DARK if self.saved_dark else Theme.LIGHT)
        settle(self.application)
        sys.modules.pop(self.module.__name__, None)

    def test_every_resource_translates_and_preserves_dynamic_values(self):
        values = [f"VALUE{index}-https://example.com/Home?key=Settings" for index in range(10)]
        for language in ("zh-CN", "en-US"):
            settings._data["language"] = language
            self.module.install()
            for index, (japanese, chinese, english) in enumerate(self.module._RESOURCES):
                with self.subTest(language=language, resource=index):
                    expected = substitute(japanese, values)
                    if self.module._SLOT.search(chinese) and not re.search(r"\w", self.module._SLOT.sub("", chinese + english)):
                        expected = substitute(chinese if language == "zh-CN" else english, values)
                    self.assertEqual(i18n.L(substitute(chinese, values), substitute(english, values)), expected)
            self.module.uninstall()

    def test_all_host_translation_calls_have_resources(self):
        known = {source for japanese, chinese, english in self.module._RESOURCES for source in (chinese, english)}
        missing = []
        for path in (ROOT / "app").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or getattr(node.func, "id", "") != "L" or len(node.args) != 2:
                    continue
                for argument in node.args:
                    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                        if argument.value not in known:
                            missing.append(f"{path.name}:{node.lineno}: {argument.value}")
        self.assertEqual(missing, [])

    def test_unknown_strings_urls_and_non_strings_are_unchanged(self):
        for value in (None, 123, "", "https://Home.example/Settings?name=Plugins", r"C:\Home\Settings\Plugins.py", "ユーザーのメモ"):
            with self.subTest(value=value):
                self.assertEqual(self.module.translate(value), value)
        self.assertEqual(self.module.translate("30 sec"), "30 秒")
        self.assertEqual(self.module.translate("Settings\nStress Test\n"), "設定\n負荷テスト\n")

    def test_existing_widgets_translate_without_changing_inputs_or_emitting_signals(self):
        root = QWidget()
        original = "Settings https://example.com/Home"
        edits = [QLineEdit(root), QTextEdit(root), QPlainTextEdit(root)]
        for edit in edits:
            if isinstance(edit, QLineEdit):
                edit.setText(original)
            else:
                edit.setPlainText(original)
            edit.setPlaceholderText("Target address is missing")
        combo = ComboBox(root)
        combo.addItems(["sec", "min", "hour", "day"])
        combo.setCurrentIndex(1)
        signals = []
        combo.currentTextChanged.connect(signals.append)
        label = QLabel("Settings", root)
        switch = SwitchButton(root)
        self.module.install()
        self.assertEqual(label.text(), "設定")
        self.assertEqual(combo.currentIndex(), 1)
        self.assertEqual([combo.itemText(index) for index in range(4)], ["秒", "分", "時間", "日"])
        self.assertEqual(signals, [])
        for edit in edits:
            value = edit.text() if isinstance(edit, QLineEdit) else edit.toPlainText()
            self.assertEqual(value, original)
            self.assertEqual(edit.placeholderText(), "対象アドレスがありません")
        self.assertEqual(switch.getOffText(), "オフ")
        switch.setChecked(True)
        self.assertEqual(switch.getText(), "オン")
        switch.setChecked(False)
        self.assertEqual(switch.getText(), "オフ")
        self.module.uninstall()
        self.assertEqual(label.text(), "Settings")
        self.assertEqual(combo.itemText(1), "min")
        root.deleteLater()
        settle(self.application)

    def test_install_uninstall_is_repeatable_and_restores_imported_references(self):
        original_l = i18n.L
        original_text = plugins_module._i18n_text
        original_disclaimer = disclaimer_module._disclaimer_text
        for cycle in range(3):
            self.module.install()
            self.module.install()
            self.assertEqual(main_window_module.L("设置", "Settings"), "設定")
            self.assertEqual(plugins_module.PluginContext.tr("压力测试", "Stress Test"), "負荷テスト")
            self.assertEqual(plugins_module._i18n_text(["日语语言包", "Japanese Language Pack"]), "日本語言語パック")
            self.assertEqual(disclaimer_module._disclaimer_text(), self.module._DISCLAIMER)
            self.module.uninstall()
            self.module.uninstall()
            self.assertIs(i18n.L, original_l)
            self.assertIs(main_window_module.L, original_l)
            self.assertIs(plugins_module._i18n_text, original_text)
            self.assertIs(disclaimer_module._disclaimer_text, original_disclaimer)

    def test_new_raw_widgets_are_translated_when_shown(self):
        self.module.install()
        widget = QLabel("Settings")
        widget.setAttribute(Qt.WA_DontShowOnScreen, True)
        widget.show()
        settle(self.application)
        self.assertEqual(widget.text(), "設定")
        widget.close()
        widget.deleteLater()
        settle(self.application)

    def test_other_language_packs_must_be_disabled_first(self):
        from types import SimpleNamespace

        record = SimpleNamespace(pid="russian", plugin=object())
        with patch.object(plugins_module.plugin_manager, "records", return_value=[record]):
            with self.assertRaisesRegex(RuntimeError, "Disable other language packs"):
                self.module.Plugin().on_load(SimpleNamespace())
        self.assertFalse(self.module._patched)

    @contextmanager
    def window_for(self, language, theme):
        settings._data.update({"language": language, "theme": "dark" if theme == Theme.DARK else "light",
                               "animations_enabled": False, "minimize_to_tray": False, "stress_form": {},
                               "authorized": [], "plugins_disabled": [], "github_token": "", "github_login": "",
                               "agent_hub_url": "", "agent_hub_token": "", "auto_check_updates": False})
        setTheme(theme)
        with tempfile.TemporaryDirectory() as directory, patch("app.services.plugins.plugins_dir", return_value=directory), \
                patch("app.services.market.MarketClient.fetch_index"):
            manager = plugins_module.PluginManager()
            manager_patch = patch("app.services.plugins.plugin_manager", manager)
            market_patch = patch("app.ui.market_view.plugin_manager", manager)
            manager_patch.start()
            market_patch.start()
            window = MainWindow()
            window.tray_icon.hide()
            window.setAttribute(Qt.WA_DontShowOnScreen, True)
            window.show()
            window.market.marketPage._on_index([], False)
            try:
                ok, message = manager.import_from(str(PLUGIN_PATH))
                self.assertTrue(ok, message)
                record = manager.record("japanese")
                self.assertIsNotNone(record.plugin, record.error)
                self.module = sys.modules["flowbench_plugin_japanese"]
                settle(self.application)
                yield window, manager
            finally:
                manager.unload("japanese")
                settle(self.application)
                window.tray_icon.hide()
                window.close()
                window.deleteLater()
                manager.deleteLater()
                settle(self.application)
                market_patch.stop()
                manager_patch.stop()

    def test_real_plugin_import_all_pages_dialogs_and_both_themes(self):
        preview_directory = ROOT / "build" / "japanese-plugin"
        preview_directory.mkdir(parents=True, exist_ok=True)
        for language in ("zh-CN", "en-US"):
            for theme in (Theme.LIGHT, Theme.DARK):
                with self.subTest(language=language, theme=theme), self.window_for(language, theme) as (window, manager):
                    expected = {"stress": "負荷テスト", "collab": "共同テスト", "agentView": "サーバーノード",
                                "monitor": "モニター", "market": "プラグイン", "settingsView": "設定"}
                    for name, title in expected.items():
                        page = getattr(window, name)
                        window.switchTo(page)
                        settle(self.application)
                        self.assertIn(title, [label.text() for label in page.findChildren(QLabel)])
                        self.assertFalse(page.grab().isNull())
                        for label in page.findChildren(QLabel):
                            self.assertEqual(self.module.translate(label.text()), label.text())
                    self.assertIn("japanese", window._plugin_pages)
                    self.assertEqual(window.stress.protoCombo.currentText(), "HTTP")
                    before_duration = window.stress.get_duration_seconds()
                    window.stress._refresh_plan()
                    self.assertEqual(window.stress.get_duration_seconds(), before_duration)
                    self.assertIn("秒", window.stress.planDurationValue.text())
                    self.assertEqual(isDarkTheme(), theme == Theme.DARK)
                    color = window.stress.planTargetsValue.palette().color(QPalette.WindowText)
                    self.assertGreater(color.lightness(), 180) if theme == Theme.DARK else self.assertLess(color.lightness(), 100)
                    for factory, title in ((lambda: DisclaimerDialog(window), "免責事項と利用規約"),
                                           (lambda: AuthDialog("127.0.0.1", window), "対象の許可確認"),
                                           (lambda: GenerateNodeDialog(window), "サーバーノードの生成"),
                                           (lambda: PublishDialog(window), "マーケットに公開")):
                        dialog = factory()
                        self.assertIn(title, [label.text() for label in dialog.findChildren(QLabel)])
                        if isinstance(dialog, (DisclaimerDialog, AuthDialog)):
                            self.assertFalse(dialog.yesButton.isEnabled())
                        if isinstance(dialog, DisclaimerDialog):
                            self.assertEqual(dialog.contentLabel.text(), self.module._DISCLAIMER)
                        dialog.close()
                        dialog.deleteLater()
                    if language == "zh-CN":
                        for page_name, page in (("stress", window.stress), ("plugin", window._plugin_pages["japanese"])):
                            window.switchTo(page)
                            settle(self.application)
                            self.assertTrue(window.grab().save(str(preview_directory / f"{page_name}-{theme.value.lower()}.png")))
                    self.assertTrue(manager.reload("japanese"))
                    self.module = sys.modules["flowbench_plugin_japanese"]
                    settle(self.application)
                    self.assertEqual(i18n.L("设置", "Settings"), "設定")


if __name__ == "__main__":
    unittest.main()
