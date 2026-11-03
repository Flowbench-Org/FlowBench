"""Entry point of ``FlowBench-Setup-<version>.exe``.

Interactive install:  FlowBench-Setup.exe
In-app update:        FlowBench-Setup.exe --update --pid <app pid> --dir <install dir>
                                          [--lang zh|en] [--theme light|dark] [--accent #RRGGBB]

Unknown arguments are ignored so older apps can launch newer installers.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from flowbench_installer import core

DEFAULT_ACCENT = "#0078D4"      # FlowBench's default theme colour


def bundle_dir() -> Path:
    """Folder holding bundled data (PyInstaller's temp dir when frozen)."""
    frozen = getattr(sys, "_MEIPASS", None)
    return Path(frozen) if frozen else Path(__file__).resolve().parent.parent


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="FlowBench-Setup", add_help=False)
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--pid", type=int, default=0)
    parser.add_argument("--dir", default="")
    parser.add_argument("--lang", choices=("zh", "en"), default=None)
    parser.add_argument("--theme", choices=("light", "dark"), default=None)
    parser.add_argument("--accent", default="")
    parser.add_argument("--payload-dir", default="")
    args, _unknown = parser.parse_known_args(argv)
    return args


def main(argv=None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    base = bundle_dir()
    payload_dir = Path(args.payload_dir) if args.payload_dir else base / "payload"

    from PySide6.QtWidgets import QApplication, QMessageBox
    from PySide6.QtGui import QColor
    from qfluentwidgets import Theme, setTheme, setThemeColor

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("FlowBench Setup")
    if args.theme:
        setTheme(Theme.DARK if args.theme == "dark" else Theme.LIGHT)
    else:
        setTheme(Theme.AUTO)
    accent = QColor(args.accent) if args.accent else QColor()
    setThemeColor(accent if accent.isValid() else QColor(DEFAULT_ACCENT))

    try:
        info = core.read_payload_info(payload_dir / core.PAYLOAD_INFO_NAME)
        if not (payload_dir / core.PAYLOAD_NAME).is_file():
            raise core.InstallError("The installer is missing its payload.",
                                    "安装程序缺少安装数据。")
    except (OSError, ValueError, core.InstallError) as exc:
        QMessageBox.critical(None, "FlowBench Setup", str(exc))
        return 1

    from flowbench_installer.gui import SetupWindow

    chinese = None if args.lang is None else args.lang == "zh"
    window = SetupWindow(payload_dir / core.PAYLOAD_NAME, info,
                         update=args.update,
                         target=Path(args.dir) if args.dir else None,
                         wait_pid=args.pid, chinese=chinese, resources=base)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
