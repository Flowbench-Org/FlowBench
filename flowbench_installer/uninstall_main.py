"""Entry point of ``FlowBench-Uninstall.exe`` (standard library only).

The uninstaller lives inside the folder it deletes, so it first copies itself
to %TEMP% and lets that copy do the work.  Settings and logs in
%APPDATA%\\FlowBench are kept, as with the old Inno Setup uninstaller.
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

MB_OK, MB_YESNO, MB_RETRYCANCEL = 0x0, 0x4, 0x5
MB_ICONERROR, MB_ICONQUESTION, MB_ICONWARNING, MB_ICONINFO = 0x10, 0x20, 0x30, 0x40
IDYES, IDRETRY = 6, 4
QUIET_ALIASES = {"/s", "/silent", "/verysilent", "/quiet", "-s"}


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


def remove_shortcuts(marker: dict) -> None:
    listed = marker.get("shortcuts")
    if not isinstance(listed, list):
        listed = [str(p) for p in winplat.shortcut_paths().values()]
    # Only ever delete .lnk files, whatever the marker says.
    winplat.remove_files([p for p in listed
                          if isinstance(p, str) and p.lower().endswith(".lnk")])


def uninstall(install_dir: Path, quiet: bool, parent_pid: int = 0, ui=message) -> int:
    chinese = winplat.system_prefers_chinese()

    def t(zh, en):
        return zh if chinese else en

    title = t("卸载 FlowBench", "Uninstall FlowBench")
    if parent_pid:
        winplat.wait_for_exit(parent_pid, 30)
    if not quiet and ui(t("确定要卸载 FlowBench 吗？\n\n你的设置和日志会保留在 %APPDATA%\\FlowBench。",
                          "Uninstall FlowBench?\n\nYour settings and logs stay in %APPDATA%\\FlowBench."),
                        title, MB_YESNO | MB_ICONQUESTION) != IDYES:
        return 1
    marker = core.read_marker(install_dir)
    while True:
        try:
            core.uninstall_files(install_dir)
            break
        except core.AppInUseError as exc:
            if quiet or ui(exc.text(chinese), title, MB_RETRYCANCEL | MB_ICONWARNING) != IDRETRY:
                return 2
        except core.InstallError as exc:
            if not quiet:
                ui(exc.text(chinese), title, MB_OK | MB_ICONERROR)
            return 3
    remove_shortcuts(marker)
    registered = winplat.registered_install_dir()
    if registered is None or os.path.normcase(str(registered)) == os.path.normcase(str(install_dir)):
        winplat.delete_uninstall_entry()
    winplat.notify_shell()
    if not quiet:
        ui(t("FlowBench 已卸载。", "FlowBench has been uninstalled."), title, MB_OK | MB_ICONINFO)
    return 0


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
        return uninstall(install_dir, args.quiet, args.parent_pid)
    finally:
        delete_self_later()


if __name__ == "__main__":
    sys.exit(main())
