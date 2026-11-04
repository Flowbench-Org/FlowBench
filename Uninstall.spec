# -*- mode: python ; coding: utf-8 -*-
# FlowBench-Uninstall.exe: the same Fluent window as the setup wizard.
# Only the Qt pieces a plain widgets window needs are kept, to stay small.
import os

a = Analysis(
    ["flowbench_installer/uninstall_main.py"], pathex=["."], binaries=[],
    datas=[
        ("app.ico", "."),
        ("app_logo.png", "."),
        ("app_logo_dark.png", "."),
    ],
    hiddenimports=["shiboken6.Shiboken", "flowbench_installer.uninstall_gui"],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["requests", "urllib3", "cryptography", "paho", "icmplib", "tkinter",
              "numpy", "torch", "scipy", "pandas", "matplotlib", "IPython",
              "PySide6.QtCharts", "PySide6.QtNetwork", "PySide6.QtMultimedia",
              "PySide6.QtWebEngineCore", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtPdf",
              "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.Qt3DCore"],
    noarchive=False,
)

# Same ICU workaround as FlowBench.spec.
_DROP_BINARIES = {"icuuc.dll", "icudt78.dll",
                  # Software OpenGL / Direct3D compiler: unused by a widgets window.
                  "opengl32sw.dll", "d3dcompiler_47.dll",
                  "qt6pdf.dll", "qpdf.dll", "qt6quick.dll", "qt6qml.dll",
                  "qt6qmlmodels.dll", "qt6network.dll"}
a.binaries = [item for item in a.binaries
              if os.path.basename(item[0]).lower() not in _DROP_BINARIES]
# Qt's own translations: the window carries its Chinese and English text.
a.datas = [item for item in a.datas
           if not item[0].replace("\\", "/").lower().startswith("pyside6/translations/")]

pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="FlowBench-Uninstall",
          console=False, upx=False, icon="app.ico")
