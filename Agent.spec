# -*- mode: python ; coding: utf-8 -*-
# Cross-platform one-file agent: no Qt/desktop dependency.
a = Analysis(
    ["server_agent.py"], pathex=["."], binaries=[], datas=[],
    hiddenimports=["agent.worker", "agent.transport", "agent.relay", "agent.sysmetrics", "psutil", "paho.mqtt.client"], hookspath=[], hooksconfig={},
    runtime_hooks=[], excludes=["PySide6", "qfluentwidgets", "torch", "torchvision", "torchaudio", "tensorflow", "scipy", "pandas", "matplotlib", "IPython", "jupyter", "notebook", "cv2", "PIL.ImageQt"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="FlowBench-Agent", console=True, icon="app.ico")
