import io
import json
import os
import tarfile
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("APPDATA", os.path.join(tempfile.gettempdir(), "FlowBench-installer-tests"))

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from flowbench_installer import core, session as session_mod, uninstall_main, winplat
from flowbench_installer.gui import SetupWindow
from flowbench_installer.session import SetupSession
from tools import make_payload
from app.services import updater


def make_dist(root: Path, version_text: str = "new", extra=None) -> Path:
    dist = root / "dist"
    app = dist / "FlowBench"
    (app / "_internal" / "PySide6").mkdir(parents=True)
    (app / "FlowBench.exe").write_bytes(b"MZ" + version_text.encode())
    (app / "_internal" / "PySide6" / "Qt6Core.dll").write_bytes(os.urandom(300_000))
    (app / "_internal" / "base_library.zip").write_bytes(b"zip" * 1000)
    for rel, data in (extra or {}).items():
        (app / rel).parent.mkdir(parents=True, exist_ok=True)
        (app / rel).write_bytes(data)
    (dist / "FlowBench-Uninstall.exe").write_bytes(b"MZuninstall")
    return dist


def build_payload(root: Path, version_text: str = "new", extra=None):
    dist = make_dist(root / f"src-{version_text}", version_text, extra)
    out = root / f"payload-{version_text}"
    info = make_payload.build(dist, out, preset=1)
    return out / core.PAYLOAD_NAME, info


class FakePlatform:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.calls = []
        self.running = []
        self.legacy = []
        self.legacy_code = 0
        self.registry = None
        self.exit_ok = True

    def shortcut_paths(self):
        return {"start_menu": self.root / "start" / "FlowBench.lnk",
                "desktop": self.root / "desktop" / "FlowBench.lnk"}

    def create_shortcut(self, link, target, workdir, icon, description="", app_id=""):
        Path(link).parent.mkdir(parents=True, exist_ok=True)
        Path(link).write_text(str(target))
        self.calls.append(("shortcut", str(link)))

    def remove_files(self, paths):
        winplat.remove_files(paths)

    def write_uninstall_entry(self, target, version, size):
        self.registry = {"target": str(target), "version": version, "size": size}

    def delete_uninstall_entry(self):
        self.registry = None

    def registered_install_dir(self):
        return Path(self.registry["target"]) if self.registry else None

    def notify_shell(self):
        pass

    def find_legacy_installs(self):
        return list(self.legacy)

    def run_legacy_uninstall(self, entry):
        self.calls.append(("legacy", entry["location"]))
        return self.legacy_code

    def running_instances(self, exes):
        self.calls.append(("running?", [str(e) for e in exes]))
        return list(self.running)

    def wait_for_exit(self, pid, timeout):
        self.calls.append(("wait", pid))
        return self.exit_ok

    def launch_detached(self, exe, args=(), cwd=None):
        self.calls.append(("launch", str(exe), list(args)))

    def system_prefers_chinese(self):
        return True

    def default_install_dir(self):
        return self.root / "Programs" / "FlowBench"

    def set_dark_title_bar(self, hwnd, dark):
        pass


class PayloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_payload_contains_app_and_uninstaller_with_version(self):
        payload, info = build_payload(self.root)
        self.assertEqual(info["version"], updater.APP_VERSION)
        self.assertEqual(info["app"], "FlowBench")
        with tarfile.open(payload) as archive:
            names = set(archive.getnames())
        self.assertIn("FlowBench.exe", names)
        self.assertIn("FlowBench-Uninstall.exe", names)
        self.assertIn("_internal/PySide6/Qt6Core.dll", names)
        self.assertEqual(info["files"], len(names))
        self.assertEqual(core.read_payload_info(payload.parent / core.PAYLOAD_INFO_NAME), info)

    def test_unsafe_member_names_are_rejected(self):
        for name in ("../evil.txt", "/abs.txt", "C:/win.txt", "a/../../x", "..\\x"):
            with self.subTest(name=name), self.assertRaises(core.InstallError):
                core.safe_member_path(name)
        self.assertEqual(str(core.safe_member_path("./a/b.dll")), "a/b.dll")

    def _tar(self, members):
        path = self.root / "bad.tar.xz"
        with tarfile.open(path, "w:xz") as archive:
            for info, data in members:
                archive.addfile(info, io.BytesIO(data) if data is not None else None)
        return path

    def test_traversal_and_links_never_escape_the_staging_folder(self):
        ok = tarfile.TarInfo("FlowBench.exe")
        ok.size = 2
        evil = tarfile.TarInfo("../escaped.txt")
        evil.size = 3
        link = tarfile.TarInfo("link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        for members in ([(ok, b"MZ"), (evil, b"bad")], [(ok, b"MZ"), (link, None)]):
            payload = self._tar(members)
            target = self.root / "Programs" / "FlowBench"
            job = core.InstallJob(payload, {"version": "9"}, target)
            with self.subTest(members=[m.name for m, _ in members]):
                with self.assertRaises(core.InstallError):
                    job.stage()
                self.assertFalse((self.root / "Programs" / "escaped.txt").exists())
                self.assertFalse(target.exists())
                self.assertEqual(list((self.root / "Programs").iterdir()), [])


class InstallJobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.payload, self.info = build_payload(self.root)
        self.target = self.root / "Programs" / "FlowBench"

    def tearDown(self):
        self.tmp.cleanup()

    def siblings(self):
        return sorted(p.name for p in self.target.parent.iterdir())

    def install(self, payload=None, info=None):
        job = core.InstallJob(payload or self.payload, info or self.info, self.target,
                              rename_attempts=2, rename_delay=0)
        job.stage()
        job.activate({"version": (info or self.info)["version"]})
        return job

    def test_fresh_install_writes_files_and_marker_only(self):
        self.install()
        self.assertEqual((self.target / "FlowBench.exe").read_bytes(), b"MZnew")
        self.assertTrue((self.target / "FlowBench-Uninstall.exe").is_file())
        self.assertEqual(core.read_marker(self.target)["version"], self.info["version"])
        self.assertEqual(self.siblings(), ["FlowBench"])

    def test_upgrade_replaces_old_files_and_removes_backup(self):
        old_payload, old_info = build_payload(self.root, "old", {"_internal/obsolete.dll": b"x"})
        self.install(old_payload, old_info)
        self.assertTrue((self.target / "_internal" / "obsolete.dll").exists())
        self.install()
        self.assertEqual((self.target / "FlowBench.exe").read_bytes(), b"MZnew")
        self.assertFalse((self.target / "_internal" / "obsolete.dll").exists())
        self.assertEqual(self.siblings(), ["FlowBench"])

    def test_refuses_folders_that_are_not_safe_to_replace(self):
        foreign = self.root / "Documents"
        foreign.mkdir()
        (foreign / "thesis.docx").write_text("precious")
        cases = [Path("relative/FlowBench"), Path(self.root.anchor), foreign]
        afile = self.root / "file.txt"
        afile.write_text("x")
        cases.append(afile)
        for target in cases:
            with self.subTest(target=str(target)), self.assertRaises(core.InstallError):
                core.InstallJob(self.payload, self.info, target).stage()
        self.assertEqual((foreign / "thesis.docx").read_text(), "precious")
        empty = self.root / "Empty"
        empty.mkdir()
        core.check_target(empty)

    def test_cancel_during_extraction_leaves_nothing_behind(self):
        job = core.InstallJob(self.payload, self.info, self.target)
        calls = []
        with self.assertRaises(core.InstallCancelled):
            job.stage(cancelled=lambda: calls.append(1) or len(calls) > 2)
        self.assertEqual(self.siblings(), [])

    def test_locked_target_keeps_old_version_and_can_retry_without_extracting(self):
        old_payload, old_info = build_payload(self.root, "old")
        self.install(old_payload, old_info)
        job = core.InstallJob(self.payload, self.info, self.target, rename_attempts=2, rename_delay=0)
        job.stage()
        real_rename = os.rename

        def locked(src, dst):
            if Path(src) == self.target:
                raise PermissionError("in use")
            return real_rename(src, dst)

        with mock.patch("os.rename", side_effect=locked):
            with self.assertRaises(core.AppInUseError):
                job.activate({"version": "x"})
        self.assertEqual((self.target / "FlowBench.exe").read_bytes(), b"MZold")
        self.assertIsNotNone(job.staging)
        with mock.patch.object(core, "extract_payload", side_effect=AssertionError("re-extracted")):
            job.activate({"version": "x"})
        self.assertEqual((self.target / "FlowBench.exe").read_bytes(), b"MZnew")
        self.assertEqual(self.siblings(), ["FlowBench"])

    def test_failed_swap_restores_the_previous_version(self):
        old_payload, old_info = build_payload(self.root, "old")
        self.install(old_payload, old_info)
        job = core.InstallJob(self.payload, self.info, self.target, rename_attempts=1, rename_delay=0)
        job.stage()
        real_rename = os.rename

        def fail_new(src, dst):
            if Path(src) == job.staging:
                raise OSError("disk full")
            return real_rename(src, dst)

        with mock.patch("os.rename", side_effect=fail_new):
            with self.assertRaises(core.InstallError):
                job.activate({})
        self.assertEqual((self.target / "FlowBench.exe").read_bytes(), b"MZold")
        job.discard()
        self.assertEqual(self.siblings(), ["FlowBench"])

    def test_stale_staging_folders_are_cleaned_but_others_kept(self):
        parent = self.target.parent
        parent.mkdir(parents=True)
        for name in ("FlowBench.new-0123abcd", "FlowBench.old-89abcdef", "FlowBench.new-keep", "Other"):
            (parent / name).mkdir()
        self.install()
        self.assertEqual(self.siblings(), ["FlowBench", "FlowBench.new-keep", "Other"])

    def test_uninstall_removes_only_flowbench_folders(self):
        self.install()
        core.uninstall_files(self.target)
        self.assertFalse(self.target.exists())
        foreign = self.root / "Other"
        foreign.mkdir()
        (foreign / "a.txt").write_text("keep")
        with self.assertRaises(core.InstallError):
            core.uninstall_files(foreign)
        self.assertTrue((foreign / "a.txt").exists())


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.payload, self.info = build_payload(self.root)
        self.target = self.root / "Programs" / "FlowBench"
        self.platform = FakePlatform(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def run_session(self, **kwargs):
        s = SetupSession(self.payload, self.info, self.target, platform=self.platform,
                         rename_attempts=1, rename_delay=0, **kwargs)
        s.stage()
        s.activate()
        return s, s.finalize()

    def test_install_registers_shortcuts_and_uninstall_entry(self):
        _s, warnings = self.run_session(desktop_shortcut=True)
        self.assertEqual(warnings, [])
        links = self.platform.shortcut_paths()
        self.assertTrue(links["start_menu"].exists())
        self.assertTrue(links["desktop"].exists())
        self.assertEqual(self.platform.registry["version"], self.info["version"])
        marker = core.read_marker(self.target)
        self.assertEqual(sorted(marker["shortcuts"]), sorted(str(p) for p in links.values()))

    def test_unchecked_desktop_shortcut_is_removed_on_reinstall(self):
        self.run_session(desktop_shortcut=True)
        self.run_session(desktop_shortcut=False)
        self.assertFalse(self.platform.shortcut_paths()["desktop"].exists())

    def test_update_keeps_the_desktop_shortcut_choice(self):
        self.run_session(desktop_shortcut=False)
        self.run_session(desktop_shortcut=None, update=True)
        self.assertFalse(self.platform.shortcut_paths()["desktop"].exists())
        self.run_session(desktop_shortcut=True)
        self.run_session(desktop_shortcut=None, update=True)
        self.assertTrue(self.platform.shortcut_paths()["desktop"].exists())

    def test_legacy_install_is_removed_after_install_and_failures_are_warnings(self):
        legacy_dir = self.root / "Program Files" / "FlowBench"
        self.platform.legacy = [{"location": str(legacy_dir), "admin": True, "uninstall": "u"}]
        s = SetupSession(self.payload, self.info, self.target, platform=self.platform)
        self.assertEqual(s.blocking_pids(), [])
        checked = self.platform.calls[-1]
        self.assertEqual(checked[0], "running?")
        self.assertIn(str(legacy_dir / "FlowBench.exe"), checked[1])
        self.platform.legacy_code = 1223
        _s, warnings = self.run_session()
        self.assertEqual(warnings, [("legacy", str(legacy_dir), "1223")])
        order = [c[0] for c in self.platform.calls]
        self.assertLess(order.index("shortcut"), order.index("legacy"))

    def test_update_mode_never_touches_legacy_installs(self):
        self.platform.legacy = [{"location": "C:/old", "admin": True, "uninstall": "u"}]
        self.run_session(desktop_shortcut=None, update=True)
        self.assertNotIn("legacy", [c[0] for c in self.platform.calls])


class UninstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        payload, info = build_payload(self.root)
        self.platform = FakePlatform(self.root)
        self.target = self.root / "Programs" / "FlowBench"
        s = SetupSession(payload, info, self.target, platform=self.platform)
        s.stage()
        s.activate()
        s.finalize()
        self.precious = self.root / "precious.txt"
        self.precious.write_text("keep")

    def tearDown(self):
        self.tmp.cleanup()

    def patched(self):
        p = self.platform
        return mock.patch.multiple(
            winplat, shortcut_paths=p.shortcut_paths, registered_install_dir=p.registered_install_dir,
            delete_uninstall_entry=p.delete_uninstall_entry, notify_shell=p.notify_shell,
            system_prefers_chinese=lambda: False, wait_for_exit=lambda pid, t: True)

    def no_user_prefs(self):
        return mock.patch.object(core, "user_data_dir", lambda environ=None: self.root / "AppData" / "FlowBench")

    def test_uninstall_removes_app_shortcuts_and_entry(self):
        marker = core.read_marker(self.target)
        marker["shortcuts"].append(str(self.precious))      # never deleted: not a .lnk
        core.write_marker(self.target, marker)
        prompts = []
        with self.patched(), self.no_user_prefs():
            code = uninstall_main.uninstall(self.target, quiet=False,
                                            ui=lambda text, title, flags: prompts.append(text) or 6)
        self.assertEqual(code, 0)
        self.assertFalse(self.target.exists())
        self.assertFalse(self.platform.shortcut_paths()["start_menu"].exists())
        self.assertIsNone(self.platform.registry)
        self.assertTrue(self.precious.exists())
        self.assertIn("settings", prompts[0])

    def test_declining_keeps_everything(self):
        with self.patched(), self.no_user_prefs():
            code = uninstall_main.uninstall(self.target, quiet=False, ui=lambda *a: 7)
        self.assertEqual(code, 1)
        self.assertTrue((self.target / "FlowBench.exe").exists())
        self.assertIsNotNone(self.platform.registry)

    def test_quiet_aliases(self):
        for flag in ("/S", "/VERYSILENT", "--quiet"):
            self.assertTrue(uninstall_main.parse_args([flag]).quiet)

    def test_quiet_uninstall_never_prompts_and_keeps_user_data(self):
        data = self.root / "AppData" / "FlowBench"
        (data / "logs").mkdir(parents=True)
        (data / "settings.json").write_text('{"language": "zh-CN"}', encoding="utf-8")
        with self.patched(), self.no_user_prefs():
            code = uninstall_main.uninstall(self.target, quiet=True,
                                            ui=lambda *a: self.fail("quiet mode prompted"))
        self.assertEqual(code, 0)
        self.assertFalse(self.target.exists())
        self.assertTrue((data / "settings.json").exists())

    def test_session_reports_file_progress_and_can_remove_user_data(self):
        data = self.root / "AppData" / "FlowBench"
        (data / "plugins").mkdir(parents=True)
        (data / "settings.json").write_text("{}", encoding="utf-8")
        steps = []
        session = session_mod.UninstallSession(
            self.target, remove_user_data=True, data_dir=data, platform=self.platform)
        self.assertEqual(session.version, core.read_marker(self.target)["version"])
        warnings = session.run(lambda pct, name: steps.append((pct, name)))
        self.assertEqual(warnings, [])
        self.assertFalse(self.target.exists())
        self.assertFalse(data.exists())
        self.assertIsNone(self.platform.registry)
        names = [name for pct, name in steps if pct < session.FILES_END]
        self.assertIn("FlowBench.exe", names)
        self.assertEqual([pct for pct, _n in steps], sorted(pct for pct, _n in steps))
        self.assertEqual(steps[-1][0], 100)

    def test_user_data_removal_refuses_other_folders(self):
        other = self.root / "AppData" / "Important"
        other.mkdir(parents=True)
        self.assertFalse(core.remove_user_data(other))
        self.assertTrue(other.exists())
        self.assertTrue(core.remove_user_data(self.root / "AppData" / "FlowBench"))   # missing: fine

    def test_user_prefs_follow_flowbench_settings(self):
        data = self.root / "AppData" / "FlowBench"
        data.mkdir(parents=True)
        self.assertEqual(core.read_user_prefs(data), {"lang": None, "theme": None, "accent": None})
        (data / "settings.json").write_text(
            '{"language": "en-US", "theme": "dark", "theme_color": "#5B5FC7"}', encoding="utf-8")
        self.assertEqual(core.read_user_prefs(data), {"lang": "en", "theme": "dark", "accent": "#5B5FC7"})
        (data / "settings.json").write_text(
            '{"language": "auto", "theme": "blue", "theme_color": "red"}', encoding="utf-8")
        self.assertEqual(core.read_user_prefs(data), {"lang": None, "theme": None, "accent": None})
        (data / "settings.json").write_text("not json", encoding="utf-8")
        self.assertEqual(core.read_user_prefs(data)["lang"], None)
        self.assertEqual(core.user_data_dir({"APPDATA": str(self.root)}), self.root / "FlowBench")
        with mock.patch.object(winplat, "system_prefers_chinese", lambda: True):
            self.assertTrue(uninstall_main.prefers_chinese({"lang": None}))
            self.assertFalse(uninstall_main.prefers_chinese({"lang": "en"}))


def app():
    return QApplication.instance() or QApplication([])


def wait_for(condition, timeout_ms=10000):
    """Process events until ``condition()`` is true (PySide6 lacks qWaitFor)."""
    import time
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if condition():
            return True
        QTest.qWait(20)
    return bool(condition())


class UninstallWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        payload, info = build_payload(self.root)
        self.version = info["version"]
        self.platform = FakePlatform(self.root)
        self.target = self.root / "Programs" / "FlowBench"
        s = SetupSession(payload, info, self.target, platform=self.platform)
        s.stage()
        s.activate()
        s.finalize()
        self.data = self.root / "AppData" / "FlowBench"
        (self.data / "plugins").mkdir(parents=True)
        (self.data / "settings.json").write_text("{}", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def window(self, chinese=False):
        from flowbench_installer.uninstall_gui import UninstallWindow
        win = UninstallWindow(self.target, data_dir=self.data, chinese=chinese,
                              platform=self.platform)
        win.show()
        return win

    def test_uninstalls_and_keeps_user_data_by_default(self):
        win = self.window()
        self.assertEqual(win.titleLabel.text(), f"FlowBench {self.version}")
        self.assertEqual(win.uninstallButton.text(), "Uninstall")
        self.assertFalse(win.removeDataCheck.isChecked())
        self.assertIn("are kept", win.dataNote.text())
        self.assertIn(str(self.data), win.dataNote.text())
        win.uninstallButton.click()
        self.assertTrue(wait_for(lambda: win.pages.currentWidget() is win.donePage, 10000))
        self.assertFalse(self.target.exists())
        self.assertIsNone(self.platform.registry)
        self.assertFalse(self.platform.shortcut_paths()["start_menu"].exists())
        self.assertTrue((self.data / "settings.json").exists())
        self.assertIn("kept", win.doneDetail.text())
        self.assertEqual(win.progressBar.value(), 100)
        finished = []
        win.finished.connect(finished.append)
        win.finishButton.click()
        self.assertEqual(finished, [True])
        self.assertTrue(win.succeeded)
        self.assertFalse(win.isVisible())

    def test_ticking_the_box_deletes_settings_plugins_and_logs(self):
        win = self.window()
        win.removeDataCheck.setChecked(True)
        self.assertIn("cannot be undone", win.dataNote.text())
        win.uninstallButton.click()
        self.assertTrue(wait_for(lambda: win.pages.currentWidget() is win.donePage, 10000))
        self.assertFalse(self.data.exists())
        self.assertIn("deleted too", win.doneDetail.text())
        win.close()

    def test_running_app_must_be_closed_first(self):
        self.platform.running = [1234]
        win = self.window()
        asked = []
        win.ask_retry = lambda title, text: asked.append(title) or False
        win.uninstallButton.click()
        self.assertEqual(asked, ["FlowBench is running"])
        self.assertIs(win.pages.currentWidget(), win.confirmPage)
        self.assertTrue((self.target / "FlowBench.exe").exists())
        win.close()

    def test_locked_folder_shows_retry_and_close_is_blocked_while_busy(self):
        win = self.window()
        win.ask_retry = lambda title, text: False
        with mock.patch.object(core, "uninstall_files",
                               side_effect=core.AppInUseError("locked", "被占用")):
            win.uninstallButton.click()
            self.assertTrue(wait_for(lambda: win.retryButton.isVisible()))
        self.assertEqual(win.progressTitle.text(), "Uninstall did not finish")
        self.assertIn("locked", win.progressError.text())
        self.assertTrue((self.target / "FlowBench.exe").exists())
        win.retryButton.click()                       # second attempt succeeds
        self.assertTrue(win.busy or win.succeeded)
        win.close()
        self.assertTrue(win.isVisible() or win.succeeded)
        self.assertTrue(wait_for(lambda: win.pages.currentWidget() is win.donePage, 10000))
        self.assertFalse(self.target.exists())
        win.close()

    def test_chinese_text(self):
        win = self.window(chinese=True)
        self.assertEqual(win.windowTitle(), "卸载 FlowBench")
        self.assertEqual(win.uninstallButton.text(), "卸载")
        self.assertIn("同时删除我的设置", win.removeDataCheck.text())
        self.assertIn("卸载向导", win.subtitleLabel.text())
        win.close()


class SetupWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.payload, self.info = build_payload(self.root)
        self.platform = FakePlatform(self.root)
        self.target = self.root / "Programs" / "FlowBench"

    def tearDown(self):
        self.tmp.cleanup()

    def wizard(self):
        win = SetupWindow(self.payload, self.info, target=self.target,
                          chinese=False, platform=self.platform)
        win.show()
        return win

    def test_wizard_installs_and_launches(self):
        win = self.wizard()
        self.assertEqual(win.installButton.text(), "Install")
        win.installButton.click()
        self.assertTrue(wait_for(lambda: win.pages.currentWidget() is win.donePage, 10000))
        self.assertTrue((self.target / "FlowBench.exe").exists())
        self.assertFalse(win.doneWarnings.isVisible())
        win.finishButton.click()
        self.assertIn(("launch", str(self.target / "FlowBench.exe"), []), self.platform.calls)
        self.assertFalse(win.isVisible())

    def test_wizard_offers_update_for_existing_install(self):
        first = self.wizard()
        first.installButton.click()
        self.assertTrue(wait_for(lambda: first.pages.currentWidget() is first.donePage))
        first.close()
        win = self.wizard()
        self.assertEqual(win.installButton.text(), "Reinstall")
        self.assertIn(self.info["version"], win.noteLabel.text())
        win.close()

    def test_running_app_must_be_closed_first(self):
        self.platform.running = [1234]
        win = self.wizard()
        asked = []
        win.ask_retry = lambda title, text: asked.append(title) or False
        win.installButton.click()
        self.assertEqual(asked, ["FlowBench is running"])
        self.assertIs(win.pages.currentWidget(), win.welcomePage)
        self.assertFalse(self.target.exists())
        win.close()

    def test_locked_folder_asks_before_each_retry_instead_of_looping(self):
        win = self.wizard()
        asked = []
        win.ask_retry = lambda title, text: asked.append(title) or len(asked) < 2
        with mock.patch.object(core.InstallJob, "activate",
                               side_effect=core.AppInUseError("locked", "被占用")):
            win.installButton.click()
            self.assertTrue(wait_for(lambda: win.retryButton.isVisible()))
        self.assertEqual(asked, ["The install folder is in use"] * 2)
        self.assertFalse(self.target.exists())
        win.retryButton.click()
        self.assertTrue(wait_for(lambda: win.pages.currentWidget() is win.donePage))
        self.assertTrue((self.target / "FlowBench.exe").exists())
        self.assertEqual([p.name for p in self.target.parent.iterdir()], ["FlowBench"])
        win.close()

    def test_unsafe_folder_is_refused_before_installing(self):
        foreign = self.root / "Docs"
        foreign.mkdir()
        (foreign / "a.txt").write_text("x")
        win = self.wizard()
        warned = []
        win._warn = lambda title, text: warned.append(text)
        win.pathEdit.setText(str(foreign))
        win.installButton.click()
        self.assertEqual(len(warned), 1)
        self.assertIs(win.pages.currentWidget(), win.welcomePage)
        win.close()

    def test_update_mode_waits_installs_relaunches_and_closes(self):
        old_payload, old_info = build_payload(self.root, "old")
        job = core.InstallJob(old_payload, old_info, self.target)
        job.stage()
        job.activate({"version": "old"})
        win = SetupWindow(self.payload, self.info, update=True, target=self.target,
                          wait_pid=4321, chinese=True, platform=self.platform)
        win.show()
        self.assertTrue(wait_for(lambda: not win.isVisible(), 10000))
        self.assertEqual(self.platform.calls[0], ("wait", 4321))
        self.assertEqual((self.target / "FlowBench.exe").read_bytes(), b"MZnew")
        self.assertIn(("launch", str(self.target / "FlowBench.exe"), []), self.platform.calls)

    def test_failed_update_offers_retry_and_relaunches_old_version_on_close(self):
        old_payload, old_info = build_payload(self.root, "old")
        job = core.InstallJob(old_payload, old_info, self.target)
        job.stage()
        job.activate({"version": "old"})
        self.platform.exit_ok = False
        win = SetupWindow(self.payload, self.info, update=True, target=self.target,
                          wait_pid=99, chinese=False, platform=self.platform)
        win.show()
        self.assertTrue(wait_for(lambda: win.updateRetry.isVisible(), 10000))
        self.assertIn("did not finish", win.updateTitle.text())
        self.platform.exit_ok = True
        win.close()
        self.assertFalse(win.isVisible())
        self.assertIn(("launch", str(self.target / "FlowBench.exe"), []), self.platform.calls)
        self.assertEqual((self.target / "FlowBench.exe").read_bytes(), b"MZold")


class _Handler(BaseHTTPRequestHandler):
    body = b""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args):
        pass


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def release(self, **asset):
        base = {"name": "FlowBench-Setup-1.2.8.exe", "size": 10,
                "browser_download_url": "https://github.com/Carlown/FlowBench/releases/download/v1.2.8/FlowBench-Setup-1.2.8.exe"}
        base.update(asset)
        return {"tag_name": "v1.2.8", "assets": [{"name": "notes.txt"}, base]}

    def test_pick_setup_asset(self):
        asset = updater.pick_setup_asset(self.release(digest="sha256:ABC"))
        self.assertEqual(asset["name"], "FlowBench-Setup-1.2.8.exe")
        self.assertEqual(asset["sha256"], "abc")
        self.assertEqual(asset["size"], 10)
        for bad in ({"browser_download_url": "http://github.com/x/FlowBench-Setup-1.2.8.exe"},
                    {"browser_download_url": "https://evil.example/FlowBench-Setup-1.2.8.exe"},
                    {"name": "FlowBench-Setup-1.2.8.zip"},
                    {"name": "FlowBench-Setup-../x.exe"}):
            with self.subTest(bad=bad):
                self.assertIsNone(updater.pick_setup_asset(self.release(**bad)))
        self.assertIsNone(updater.pick_setup_asset({"tag_name": "v1", "assets": None}))

    def test_self_update_needs_packaged_windows_app(self):
        info = {"asset": {"url": "u"}}
        self.assertFalse(updater.can_self_update({"asset": None}))
        with mock.patch.object(updater.sys, "platform", "win32"), \
                mock.patch.object(updater.sys, "frozen", True, create=True):
            self.assertTrue(updater.can_self_update(info))
        self.assertFalse(updater.can_self_update(info))     # source run on Linux

    def test_setup_command(self):
        setup = Path("C:/Temp/FlowBench-Setup-1.2.8.exe")
        self.assertEqual(updater.setup_command(setup, None, "zh", "dark"), [str(setup)])
        cmd = updater.setup_command(setup, Path("C:/Apps/FlowBench"), "en", "light", pid=42)
        self.assertEqual(cmd[1:], ["--update", "--pid", "42", "--dir", str(Path("C:/Apps/FlowBench")),
                                   "--lang", "en", "--theme", "light"])
        cmd = updater.setup_command(setup, Path("C:/Apps/FlowBench"), "zh", "dark", pid=1, accent="#FF0000")
        self.assertEqual(cmd[-2:], ["--accent", "#FF0000"])

    def test_installer_accepts_what_the_app_sends_and_ignores_unknown_flags(self):
        from flowbench_installer import setup_main
        cmd = updater.setup_command(Path("s.exe"), Path("/apps/FlowBench"), "zh", "dark", pid=7, accent="#112233")
        args = setup_main.parse_args(cmd[1:] + ["--from-the-future", "x"])
        self.assertTrue(args.update)
        self.assertEqual((args.pid, args.dir, args.lang, args.theme, args.accent),
                         (7, str(Path("/apps/FlowBench")), "zh", "dark", "#112233"))

    def test_cleanup_keeps_only_newer_complete_downloads(self):
        names = ["FlowBench-Setup-1.2.6.exe", "FlowBench-Setup-1.2.7.exe",
                 "FlowBench-Setup-1.2.8.exe", "FlowBench-Setup-1.2.9.exe.part",
                 "FlowBench-Setup-1.2.9.exe.0a1b2c3d.part", "other.exe"]
        for name in names:
            (self.root / name).write_text("x")
        updater.cleanup_downloads("1.2.7", self.root)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()),
                         ["FlowBench-Setup-1.2.8.exe", "other.exe"])

    def serve(self, body):
        handler = type("H", (_Handler,), {"body": body})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}/setup.exe"

    def test_download_reports_progress_and_verifies(self):
        import hashlib
        body = os.urandom(700_000)
        url = self.serve(body)
        dest = self.root / "dl" / "FlowBench-Setup-1.2.8.exe"
        seen = []
        updater.download_file(url, dest, len(body), hashlib.sha256(body).hexdigest(),
                              progress=lambda d, t: seen.append((d, t)))
        self.assertEqual(dest.read_bytes(), body)
        self.assertEqual(seen[-1], (len(body), len(body)))
        self.assertGreater(len(seen), 1)
        self.assertTrue(updater.verified_download(dest, len(body), hashlib.sha256(body).hexdigest()))
        self.assertFalse(updater.verified_download(dest, len(body) + 1, ""))

    def test_bad_or_cancelled_downloads_leave_no_files(self):
        body = os.urandom(600_000)
        url = self.serve(body)
        dest = self.root / "FlowBench-Setup-1.2.8.exe"
        for kwargs, error in (({"size": len(body) + 5}, updater.DownloadError),
                              ({"sha256": "0" * 64}, updater.DownloadError),
                              ({"cancelled": lambda: True}, updater.DownloadCancelled)):
            with self.subTest(kwargs=list(kwargs)), self.assertRaises(error):
                updater.download_file(url, dest, **kwargs)
            self.assertEqual(list(self.root.iterdir()), [])

    def test_update_now_downloads_launches_installer_and_quits(self):
        app()
        from PySide6.QtWidgets import QWidget
        parent = QWidget()
        parent.resize(900, 700)
        parent.show()
        body = os.urandom(300_000)
        url = self.serve(body)
        asset = {"name": "FlowBench-Setup-1.2.8.exe", "url": url, "size": len(body), "sha256": ""}
        launched, quits = [], []

        def fake_exec(box):
            box.show()
            wait_for(lambda: box.setup_path is not None, 10000)
            return bool(box.setup_path)

        with mock.patch.object(updater, "downloads_dir", return_value=self.root), \
                mock.patch("flowbench_installer.winplat.launch_detached",
                           side_effect=lambda exe, args, cwd: launched.append((str(exe), list(args)))), \
                mock.patch.object(updater, "_quit_for_update", side_effect=lambda p: quits.append(p)), \
                mock.patch("qfluentwidgets.MessageBoxBase.exec", fake_exec):
            ok = updater.start_self_update(parent, "v1.2.8", asset)
        self.assertTrue(ok)
        self.assertEqual(launched, [(str(self.root / "FlowBench-Setup-1.2.8.exe"), [])])
        self.assertEqual((self.root / "FlowBench-Setup-1.2.8.exe").read_bytes(), body)
        self.assertEqual(quits, [parent])

    # -- download failures fall back to the browser ---------------------------

    PAGE = "https://github.com/Carlown/FlowBench/releases/tag/v1.2.11"

    def dead_url(self):
        import socket
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()                 # nothing listens here: connection refused
        return f"http://127.0.0.1:{port}/setup.exe"

    def failed_dialog(self):
        from PySide6.QtWidgets import QWidget
        from app.ui.i18n import L
        app()
        parent = QWidget()
        parent.resize(900, 700)
        parent.show()
        self.addCleanup(parent.deleteLater)
        asset = {"name": "FlowBench-Setup-1.2.11.exe", "url": self.dead_url(), "size": 0, "sha256": ""}
        with mock.patch.object(updater, "downloads_dir", return_value=self.root):
            box = updater._make_download_dialog(parent, "v1.2.11", asset, L, self.PAGE)
            box.show()
            self.assertTrue(wait_for(lambda: box.browserButton.isVisible()))
        return box, L

    def test_download_error_kinds(self):
        import requests
        for exc, kind in ((updater.DownloadError("SHA-256 mismatch"), "corrupt"),
                          (requests.HTTPError("404"), "http"),
                          (requests.ConnectionError("refused"), "network"),
                          (requests.Timeout("slow"), "network"),
                          (requests.exceptions.ChunkedEncodingError("cut"), "network"),
                          (PermissionError("denied"), "disk"),
                          (ValueError("?"), "other")):
            with self.subTest(exc=type(exc).__name__):
                self.assertEqual(updater.download_error_kind(exc), kind)

    def test_failed_download_explains_and_offers_the_browser(self):
        box, L = self.failed_dialog()
        self.assertEqual(box.titleLabel.text(), L("下载失败", "Download failed"))
        self.assertTrue(box.yesButton.isVisible())
        self.assertEqual(box.cancelButton.text(), L("关闭", "Close"))
        self.assertIn(L("无法连接 GitHub，或者下载中途断开了。",
                        "Could not reach GitHub, or the download was interrupted."), box.detail.text())
        self.assertIn("FlowBench-Setup-1.2.11.exe", box.detail.text())
        self.assertTrue(box.detail.toolTip())          # raw error kept for reports
        # Same width as the other two buttons, between Retry and Close.
        layout = box.buttonLayout
        self.assertEqual([layout.itemAt(i).widget() for i in range(3)],
                         [box.yesButton, box.browserButton, box.cancelButton])
        self.assertEqual(box.browserButton.width(), box.cancelButton.width())

        # Retry hides the fallback again until the next failure.
        with mock.patch.object(updater, "downloads_dir", return_value=self.root):
            box.yesButton.click()
            self.assertFalse(box.browserButton.isVisible())
            self.assertEqual(box.cancelButton.text(), L("取消", "Cancel"))
            self.assertTrue(wait_for(lambda: box.browserButton.isVisible()))

        opened = []
        with mock.patch.object(updater.QDesktopServices, "openUrl",
                               side_effect=lambda u: opened.append(u.toString()) or True):
            box.browserButton.click()
        self.assertEqual(opened, [self.PAGE])
        self.assertTrue(box.opened_browser)
        self.assertEqual(box.result(), 0)
        self.assertEqual(list(self.root.iterdir()), [])     # no stray partial files

    def test_browser_that_will_not_open_shows_the_address(self):
        box, _L = self.failed_dialog()
        with mock.patch.object(updater.QDesktopServices, "openUrl", return_value=False):
            box.browserButton.click()
        self.assertFalse(box.opened_browser)
        self.assertTrue(box.isVisible())
        self.assertIn(self.PAGE, box.detail.text())
        box.reject()

    def test_update_now_falls_back_to_the_release_page(self):
        from PySide6.QtWidgets import QWidget
        app()
        parent = QWidget()
        parent.resize(900, 700)
        parent.show()
        self.addCleanup(parent.deleteLater)
        asset = {"name": "FlowBench-Setup-1.2.11.exe", "url": self.dead_url(), "size": 0, "sha256": ""}
        opened, notes = [], []

        def fake_exec(box):
            box.show()
            wait_for(lambda: box.browserButton.isVisible())
            box.browserButton.click()
            return box.result()

        with mock.patch.object(updater, "downloads_dir", return_value=self.root), \
                mock.patch.object(updater.QDesktopServices, "openUrl",
                                  side_effect=lambda u: opened.append(u.toString()) or True), \
                mock.patch("qfluentwidgets.InfoBar.success",
                           side_effect=lambda title, content, **kw: notes.append(content)), \
                mock.patch("qfluentwidgets.MessageBoxBase.exec", fake_exec):
            ok = updater.start_self_update(parent, "v1.2.11", asset, self.PAGE)
        self.assertFalse(ok)
        self.assertEqual(opened, [self.PAGE])
        self.assertEqual(len(notes), 1)
        self.assertIn("FlowBench-Setup-1.2.11.exe", notes[0])

    def test_failed_manual_check_links_to_the_release_page(self):
        import requests
        from PySide6.QtWidgets import QWidget
        from qfluentwidgets import HyperlinkButton, InfoBar
        from app.ui.i18n import L
        app()
        parent = QWidget()
        parent.resize(620, 500)          # as narrow as the report's screenshot
        parent.show()
        self.addCleanup(parent.deleteLater)
        limited = requests.Response()
        limited.status_code = 403
        limited.headers["X-RateLimit-Remaining"] = "0"
        error = requests.HTTPError("403 Client Error: rate limit exceeded for url: "
                                   + updater._API_URL, response=limited)
        with mock.patch.object(updater, "cleanup_downloads"), \
                mock.patch.object(updater.requests, "get", side_effect=error), \
                mock.patch.object(updater.requests, "head",
                                  side_effect=requests.ConnectionError("offline")):
            updater.check_for_updates(parent, manual=True)
            self.assertTrue(wait_for(lambda: parent.findChildren(HyperlinkButton)))
        QTest.qWait(600)                 # let the bar slide into place
        link = parent.findChildren(HyperlinkButton)[0]
        self.assertEqual(link.getUrl().toString(), updater.LATEST_URL)
        bar = next(b for b in parent.findChildren(InfoBar) if link in b.findChildren(HyperlinkButton))
        self.assertEqual(bar.content, L("GitHub 暂时限制了查询次数，请过一会儿再试，或者打开发布页查看。",
                                        "GitHub is limiting requests for now. Try again later, "
                                        "or open the release page."))
        self.assertIn("rate limit exceeded", bar.toolTip())   # raw error kept
        # The whole bar, its link and its close button stay inside the window.
        self.assertTrue(parent.rect().contains(bar.geometry()), bar.geometry())
        close = bar.closeButton.geometry().translated(bar.pos())
        self.assertTrue(bar.closeButton.isVisible())
        self.assertTrue(parent.rect().contains(close), close)

    # -- when the API is out of requests --------------------------------------

    class _Http:
        def __init__(self, api=None, page_url=None, page_error=None):
            self.api, self.page_url, self.page_error = api, page_url, page_error
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append(("get", url))
            if isinstance(self.api, Exception):
                raise self.api
            response = mock.Mock()
            response.raise_for_status.return_value = None
            response.json.return_value = self.api
            return response

        def head(self, url, **kwargs):
            self.calls.append(("head", url, kwargs.get("allow_redirects")))
            if self.page_error:
                raise self.page_error
            response = mock.Mock()
            response.raise_for_status.return_value = None
            response.url = self.page_url
            return response

    def test_api_answer_is_used_when_it_works(self):
        release = self.release()
        http = self._Http(api=dict(release, html_url="https://github.com/x/y/releases/tag/v1.2.8"))
        result = updater.fetch_latest_release(http)
        self.assertEqual((result["tag"], result["asset"]["name"]), ("v1.2.8", "FlowBench-Setup-1.2.8.exe"))
        self.assertEqual([c[0] for c in http.calls], ["get"])

    def test_release_page_answers_when_the_api_is_limited(self):
        import requests
        http = self._Http(api=requests.HTTPError("403 rate limit"),
                          page_url="https://github.com/Flowbench-Org/FlowBench/releases/tag/v99.0.1")
        result = updater.fetch_latest_release(http)
        self.assertEqual(result["tag"], "v99.0.1")
        self.assertTrue(result["newer"])
        self.assertIsNone(result["asset"])              # no installer details: browser update
        self.assertFalse(updater.can_self_update(result))
        self.assertEqual(http.calls[-1], ("head", updater.LATEST_URL, True))

    def test_release_page_must_be_a_github_release_tag(self):
        import requests
        for url in ("https://github.com/Carlown/FlowBench/releases",        # no release yet
                    "https://evil.example/Carlown/FlowBench/releases/tag/v9.9.9",
                    "http://github.com/Carlown/FlowBench/releases/tag/v9.9.9"):
            with self.subTest(url=url):
                error = requests.HTTPError("403 rate limit")
                with self.assertRaises(requests.HTTPError):      # the API's own error
                    updater.fetch_latest_release(self._Http(api=error, page_url=url))

    def test_check_errors_are_sorted_for_the_message(self):
        import requests

        def http_error(status, remaining="", text="error"):
            response = requests.Response()
            response.status_code = status
            if remaining:
                response.headers["X-RateLimit-Remaining"] = remaining
            return requests.HTTPError(text, response=response)

        for exc, kind in ((http_error(403, "0"), "rate_limit"),
                          (http_error(429, text="429 rate limit exceeded"), "rate_limit"),
                          (http_error(403, "12"), "other"),
                          (http_error(500), "other"),
                          (requests.ConnectionError("offline"), "network"),
                          (requests.Timeout("slow"), "network"),
                          (ValueError("bad json"), "other")):
            with self.subTest(exc=str(exc)):
                self.assertEqual(updater.check_error_kind(exc), kind)

if __name__ == "__main__":
    unittest.main()
