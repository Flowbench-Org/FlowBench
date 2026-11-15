# -*- mode: python ; coding: utf-8 -*-
# Cross-platform one-file agent: no Qt/desktop dependency.
import os

# Bundle the native engine when it has been staged (build_engine.ps1 copies it
# to the repo root). Without it the agent falls back to the Python worker.
engine_binaries = []
for _name in ("flowbench-engine.exe", "flowbench-engine"):
    if os.path.exists(_name):
        engine_binaries.append((_name, "."))
        break

a = Analysis(
    ["server_agent.py"], pathex=["."], binaries=engine_binaries, datas=[],
    hiddenimports=["agent.worker", "agent.engine_bridge", "agent.transport", "agent.relay", "agent.sysmetrics", "psutil", "paho.mqtt.client"], hookspath=[], hooksconfig={},
    runtime_hooks=[], excludes=["PySide6", "qfluentwidgets", "torch", "torchvision", "torchaudio", "tensorflow", "scipy", "pandas", "matplotlib", "IPython", "jupyter", "notebook", "cv2", "PIL.ImageQt"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="FlowBench-Agent", console=True, icon="app.ico")
