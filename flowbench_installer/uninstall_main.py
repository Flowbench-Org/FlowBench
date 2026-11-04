"""Entry point of ``FlowBench-Uninstall.exe``.

The uninstaller lives inside the folder it deletes, so it first copies itself
to %TEMP% and lets that copy do the work.  The copy shows the same Fluent
window as the setup wizard, in FlowBench's own language and theme; ``/S``
(and the other quiet aliases) uninstalls without any window.  Settings and
logs in %APPDATA%\\FlowBench are kept unless the user ticks the box to
delete them.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from flowbench_installer import core, winplat
from flowbench_installer.session import UninstallSession

MB_OK, MB_YESNO, MB_RETRYCANCEL = 0x0, 0x4, 0x5
MB_ICONERROR, MB_ICONQUESTION, MB_ICONWARNING, MB_ICONINFO = 0x10, 0x20, 0x30, 0x40
IDYES, IDRETRY = 6, 4
QUIET_ALIASES = {"/s", "/silent", "/verysilent", "/quiet", "-s"}
DEFAULT_ACCENT = "#0078D4"      # FlowBench's default theme colour


def message(text: str, title: str, flags: int) -> int:
    if winplat.IS_WINDOWS:
        import ctypes
        return ctypes.windll.user32.MessageBoxW(None, text, title, flags | 0x10000)
    print(f"{title}: {text}")
    return IDYES


def parse_args(argv):
    argv = ["--quiet" if a.lower() in QUIET_ALIASES else a for a in argv]
    parser = argparse.ArgumentParser(prog="FlowBench-Uninstall", add_help=False)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--stage2", action="store_true")
    parser.add_argument("--dir", default="")
    parser.add_argument("--parent-pid", type=int, default=0)
    args, _unknown = parser.parse_known_args(argv)
    return args


def relaunch_from_temp(args, install_dir: Path) -> int:
    copy = Path(tempfile.gettempdir()) / f"FlowBench-Uninstall-{os.getpid()}.exe"
    shutil.copy2(sys.executable, copy)
    extra = ["--stage2", "--dir", str(install_dir), "--parent-pid", str(os.getpid())]
    if args.quiet:
        extra.append("--quiet")
    winplat.launch_detached(copy, extra, tempfile.gettempdir())
    return 0


def prefers_chinese(prefs: dict) -> bool:
    """FlowBench's own language setting first, then the Windows language."""
    if prefs.get("lang"):
        return prefs["lang"] == "zh"
    return winplat.system_prefers_chinese()


def uninstall(install_dir: Path, quiet: bool, parent_pid: int = 0, ui=message,
              platform=winplat) -> int:
    """Window-less uninstall: ``/S``, or a fallback when Qt cannot start."""
    chinese = prefers_chinese(core.read_user_prefs(core.user_data_dir()))

    def t(zh, en):
        return zh if chinese else en

    title = t("卸载 FlowBench", "Uninstall FlowBench")
    session = UninstallSession(install_dir, parent_pid=parent_pid, platform=platform)
    if parent_pid:
        platform.wait_for_exit(parent_pid, 30)
        session.parent_pid = 0
    if not quiet and ui(t("确定要卸载 FlowBench 吗？\n\n你的设置和日志会保留在 %APPDATA%\\FlowBench。",
                          "Uninstall FlowBench?\n\nYour settings and logs stay in %APPDATA%\\FlowBench."),
                        title, MB_YESNO | MB_ICONQUESTION) != IDYES:
        return 1
    while True:
        try:
            session.run()
            break
        except core.AppInUseError as exc:
            if quiet or ui(exc.text(chinese), title, MB_RETRYCANCEL | MB_ICONWARNING) != IDRETRY:
                return 2
        except core.InstallError as exc:
            if not quiet:
                ui(exc.text(chinese), title, MB_OK | MB_ICONERROR)
            return 3
    if not quiet:
        ui(t("FlowBench 已卸载。", "FlowBench has been uninstalled."), title, MB_OK | MB_ICONINFO)
    return 0


def run_window(install_dir: Path, parent_pid: int = 0) -> int:
    """Show the Fluent uninstall window; returns 0 once FlowBench is removed."""
    try:
        from PySide6.QtGui import QColor
        from PySide6.QtWidgets import QApplication
        from qfluentwidgets import Theme, setTheme, setThemeColor
        from flowbench_installer.uninstall_gui import UninstallWindow
    except ImportError:
        return uninstall(install_dir, False, parent_pid)

    prefs = core.read_user_prefs(core.user_data_dir())
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("FlowBench Uninstall")
    setTheme({"dark": Theme.DARK, "light": Theme.LIGHT}.get(prefs["theme"], Theme.AUTO))
    setThemeColor(QColor(prefs["accent"] or DEFAULT_ACCENT))
    window = UninstallWindow(install_dir, parent_pid=parent_pid,
                             chinese=prefers_chinese(prefs), resources=bundle_dir())
    window.show()
    app.exec()
    return 0 if window.succeeded else 1


def bundle_dir() -> Path:
    """Folder holding the bundled logos (PyInstaller's temp dir when frozen)."""
    frozen = getattr(sys, "_MEIPASS", None)
    return Path(frozen) if frozen else Path(__file__).resolve().parent.parent


def delete_self_later() -> None:
    """Remove this temporary copy once the process has exited."""
    if not (winplat.IS_WINDOWS and getattr(sys, "frozen", False)):
        return
    exe = Path(sys.executable)
    if exe.parent.resolve() != Path(tempfile.gettempdir()).resolve():
        return
    subprocess.Popen(f'cmd /c ping 127.0.0.1 -n 3 > nul & del /f /q "{exe}"',
                     creationflags=0x08000000, close_fds=True)   # CREATE_NO_WINDOW


def main(argv=None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    frozen = getattr(sys, "frozen", False)
    install_dir = Path(args.dir) if args.dir else (
        Path(sys.executable).resolve().parent if frozen else Path.cwd())
    if frozen and not args.stage2:
        return relaunch_from_temp(args, install_dir)
    try:
        if args.quiet:
            return uninstall(install_dir, True, args.parent_pid)
        return run_window(install_dir, args.parent_pid)
    finally:
        delete_self_later()


if __name__ == "__main__":
    sys.exit(main())
