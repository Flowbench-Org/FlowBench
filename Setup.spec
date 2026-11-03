# -*- mode: python ; coding: utf-8 -*-
# FlowBench-Setup-<version>.exe: our own installer (replaces Inno Setup).
# Build order: build_all.ps1 -> Uninstall.spec -> tools/make_payload.py -> this.
import json
import os

with open(os.path.join("build", "payload", "payload.json"), encoding="utf-8") as fh:
    VERSION = json.load(fh)["version"]

a = Analysis(
    ["flowbench_installer/setup_main.py"], pathex=["."], binaries=[],
    datas=[
        ("build/payload/FlowBench-payload.tar.xz", "payload"),
        ("build/payload/payload.json", "payload"),
        ("app.ico", "."),
        ("app_logo.png", "."),
        ("app_logo_dark.png", "."),
    ],
    hiddenimports=["shiboken6.Shiboken"], hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["requests", "urllib3", "cryptography", "paho", "icmplib", "tkinter",
              "numpy", "torch", "scipy", "pandas", "matplotlib", "IPython",
              "PySide6.QtCharts", "PySide6.QtNetwork", "PySide6.QtMultimedia",
              "PySide6.QtWebEngineCore", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtPdf"],
    noarchive=False,
)

# Same ICU workaround as FlowBench.spec.
a.binaries = [
    item for item in a.binaries
    if os.path.basename(item[0]).lower() not in {"icuuc.dll", "icudt78.dll"}
]

pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name=f"FlowBench-Setup-{VERSION}",
          console=False, upx=False, icon="app.ico")
