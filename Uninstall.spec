# -*- mode: python ; coding: utf-8 -*-
# FlowBench-Uninstall.exe: standard library only, so it stays small.
a = Analysis(
    ["flowbench_installer/uninstall_main.py"], pathex=["."], binaries=[], datas=[],
    hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["PySide6", "shiboken6", "qfluentwidgets", "qframelesswindow", "psutil",
              "requests", "urllib3", "cryptography", "paho", "icmplib", "tkinter",
              "numpy", "torch", "scipy", "pandas", "matplotlib", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="FlowBench-Uninstall",
          console=False, upx=False, icon="app.ico")
