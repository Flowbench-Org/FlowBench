import ast
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("APPDATA", os.path.join(tempfile.gettempdir(), "FlowBench-gui-tests"))

from PySide6.QtCore import QLockFile
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import main
from app.ui import splash
from app.services.settings import settings


class _WindowProbe:
    def __init__(self):
        self.show_requests = 0

    def _show_from_tray(self):
        self.show_requests += 1


class GuiStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_brand_assets_are_readable(self):
        self.assertFalse(QIcon(main.resource_path("app.ico")).isNull())
        for filename in ("app_logo.png", "app_logo_dark.png"):
            with self.subTest(filename=filename):
                self.assertFalse(QPixmap(splash._resource_path(filename)).isNull())

    def test_splash_subtitle_follows_language_in_both_themes(self):
        for language, expected in (
            ("zh-CN", "网络压力测试与性能监控"),
            ("en-US", "Network Stress Testing & Performance Monitoring"),
        ):
            for dark in (False, True):
                with (self.subTest(language=language, dark=dark),
                      patch.dict(settings._data, {"language": language}),
                      patch("app.ui.splash.QPainter.drawText") as draw_text):
                    self.assertFalse(splash._render_content(dark=dark).isNull())
                    self.assertIn(expected, [call.args[-1] for call in draw_text.call_args_list])

    def test_splash_uses_logo_for_saved_theme(self):
        with tempfile.TemporaryDirectory() as directory:
            settings_path = Path(directory) / "FlowBench" / "settings.json"
            settings_path.parent.mkdir()
            for theme, expected_dark, filename in (
                ("light", False, "app_logo.png"),
                ("dark", True, "app_logo_dark.png"),
                ("unknown", False, "app_logo.png"),
            ):
                with self.subTest(theme=theme), patch.dict(os.environ, {"APPDATA": directory}):
                    settings_path.write_text(json.dumps({"theme": theme}), encoding="utf-8")
                    dark = splash._read_saved_theme()
                    self.assertEqual(dark, expected_dark)
                    with patch("app.ui.splash._resource_path", wraps=splash._resource_path) as resource:
                        self.assertFalse(splash._render_content(dark=dark).isNull())
                    resource.assert_called_once_with(filename)

    def test_splash_defaults_to_light_without_valid_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            settings_path = Path(directory) / "FlowBench" / "settings.json"
            with patch.dict(os.environ, {"APPDATA": directory}):
                self.assertFalse(splash._read_saved_theme())
                settings_path.parent.mkdir()
                settings_path.write_text("not json", encoding="utf-8")
                self.assertFalse(splash._read_saved_theme())

    def test_splash_loads_bundled_logos(self):
        root = Path(main.__file__).resolve().parent
        with patch.object(splash.sys, "_MEIPASS", str(root), create=True):
            for dark, filename in ((False, "app_logo.png"), (True, "app_logo_dark.png")):
                with self.subTest(dark=dark):
                    self.assertEqual(splash._resource_path(filename), str(root / filename))
                    self.assertFalse(splash._render_content(dark=dark).isNull())

    def test_build_specs_bundle_brand_assets(self):
        root = Path(main.__file__).resolve().parent
        tree = ast.parse((root / "FlowBench.spec").read_text(encoding="utf-8"))
        analysis = next(node for node in ast.walk(tree)
                        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == "Analysis")
        data = next(keyword.value for keyword in analysis.keywords if keyword.arg == "datas")
        bundled = {entry.elts[0].value for entry in data.elts}
        self.assertTrue({"app.ico", "app_logo.png", "app_logo_dark.png"}.issubset(bundled))
        for filename in ("FlowBench.spec", "Agent.spec", "Hub.spec", "AgentCtl.spec"):
            with self.subTest(filename=filename):
                tree = ast.parse((root / filename).read_text(encoding="utf-8"))
                executable = next(node for node in ast.walk(tree)
                                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                                  and node.func.id == "EXE")
                icon = next(keyword.value for keyword in executable.keywords if keyword.arg == "icon")
                self.assertEqual(ast.literal_eval(icon), "app.ico")

    def test_qlockfile_excludes_a_second_process(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "flowbench.lock")
            first = QLockFile(path)
            second = QLockFile(path)
            self.assertTrue(first.tryLock(0))
            self.assertFalse(second.tryLock(0))
            first.unlock()
            self.assertTrue(second.tryLock(0))
            second.unlock()

    def test_launch_requests_wait_until_the_window_is_ready(self):
        name = f"FlowBench-startup-test-{uuid.uuid4().hex}"
        old_name = main.SINGLE_INSTANCE_KEY
        main.SINGLE_INSTANCE_KEY = name
        server = None
        clients = []
        try:
            QLocalServer.removeServer(name)
            server = main.create_single_instance_server()
            self.assertIsNotNone(server)
            self.assertTrue(server.isListening())

            first = QLocalSocket()
            clients.append(first)
            first.connectToServer(name)
            self.assertTrue(first.waitForConnected(500))
            first.write(b"show")
            first.flush()
            self.app.processEvents()

            probe = _WindowProbe()
            server.set_main_window(probe)
            self.app.processEvents()
            self.assertEqual(probe.show_requests, 0)

            server.set_ready()
            self.app.processEvents()
            self.assertEqual(probe.show_requests, 1)

            second = QLocalSocket()
            clients.append(second)
            second.connectToServer(name)
            self.assertTrue(second.waitForConnected(500))
            second.write(b"show")
            second.flush()
            self.app.processEvents()
            QTest.qWait(30)
            self.app.processEvents()
            self.assertEqual(probe.show_requests, 2)
        finally:
            for client in clients:
                client.abort()
            if server is not None:
                server.close()
                server.deleteLater()
            QLocalServer.removeServer(name)
            main.SINGLE_INSTANCE_KEY = old_name


    # -- launching again while FlowBench runs ---------------------------------

    def _running_instance(self, directory):
        """A held lock and a ready launch endpoint, like a running FlowBench."""
        name = f"FlowBench-startup-test-{uuid.uuid4().hex}"
        lock_path = os.path.join(directory, "FlowBench.instance.lock")
        patches = [patch.object(main, "SINGLE_INSTANCE_KEY", name),
                   patch.object(main, "instance_lock_path", return_value=lock_path)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        quit_on_close = self.app.quitOnLastWindowClosed()
        self.addCleanup(self.app.setQuitOnLastWindowClosed, quit_on_close)
        return name, lock_path

    def test_second_launch_brings_back_the_window_without_a_splash(self):
        with tempfile.TemporaryDirectory() as directory:
            name, lock_path = self._running_instance(directory)
            held = QLockFile(lock_path)
            self.assertTrue(held.tryLock(0))
            server = main.create_single_instance_server()
            probe = _WindowProbe()
            server.set_main_window(probe)
            server.set_ready()
            try:
                with patch.object(main, "create_splash",
                                  side_effect=AssertionError("splash shown")) as create:
                    self.assertEqual(main.main(), 0)
                create.assert_not_called()
                for _ in range(20):
                    if probe.show_requests:
                        break
                    QTest.qWait(20)
                self.assertEqual(probe.show_requests, 1)
            finally:
                server.close()
                server.deleteLater()
                QLocalServer.removeServer(name)
                held.unlock()

    def test_first_launch_claims_the_instance_before_the_splash(self):
        class Stop(Exception):
            pass

        seen = {}
        with tempfile.TemporaryDirectory() as directory:
            name, lock_path = self._running_instance(directory)

            def splash_after_claim():
                other = QLockFile(lock_path)
                seen["locked"] = not other.tryLock(0)
                seen["listening"] = self.app._single_instance_server.isListening()
                raise Stop()

            try:
                with patch.object(main, "create_splash", side_effect=splash_after_claim):
                    with self.assertRaises(Stop):
                        main.main()
            finally:
                server = getattr(self.app, "_single_instance_server", None)
                if server is not None:
                    server.close()
                    server.deleteLater()
                    del self.app._single_instance_server
                lock = getattr(self.app, "_single_instance_lock", None)
                if lock is not None:
                    lock.unlock()
                    del self.app._single_instance_lock
                QLocalServer.removeServer(name)
        self.assertEqual(seen, {"locked": True, "listening": True})

    def test_handoff_lets_the_running_copy_come_to_the_front(self):
        name = f"FlowBench-startup-test-{uuid.uuid4().hex}"
        server = QLocalServer()
        self.assertTrue(server.listen(name))
        user32 = SimpleNamespace(AllowSetForegroundWindow=Mock(return_value=1))
        try:
            with patch.object(main, "SINGLE_INSTANCE_KEY", name), \
                    patch.object(main.sys, "platform", "win32"), \
                    patch("ctypes.windll", SimpleNamespace(user32=user32), create=True):
                self.assertTrue(main.is_already_running(attempts=3))
        finally:
            server.close()
            QLocalServer.removeServer(name)
        user32.AllowSetForegroundWindow.assert_called_once_with(-1)

    def test_bringing_back_keeps_a_maximized_window_maximized(self):
        from PySide6.QtWidgets import QWidget
        from app.ui.main_window import MainWindow
        window = QWidget()
        self.addCleanup(window.deleteLater)
        window.showMaximized()
        QTest.qWait(30)
        window.showMinimized()
        QTest.qWait(30)
        self.assertTrue(window.isMinimized())
        MainWindow._show_from_tray(window)
        QTest.qWait(30)
        self.assertFalse(window.isMinimized())
        self.assertTrue(window.isMaximized())
        window.hide()                         # minimized to the tray
        MainWindow._show_from_tray(window)
        QTest.qWait(30)
        self.assertTrue(window.isVisible())
        self.assertTrue(window.isMaximized())
        window.close()


if __name__ == "__main__":
    unittest.main()
