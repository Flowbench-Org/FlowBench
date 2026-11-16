# -*- mode: python ; coding: utf-8 -*-
# Cross-platform one-file agent: no Qt/desktop dependency.
import os
import sys

# Bundle the native engine when it has been staged (build_engine.ps1 copies it
# to the repo root). Without it the agent falls back to the Python worker.
engine_binaries = []
_engine_name = "flowbench-engine.exe" if sys.platform.startswith("win") else "flowbench-engine"
for _cand in (_engine_name, os.path.join("engine", "target", "release", _engine_name)):
    if os.path.exists(_cand):
        engine_binaries = [(_cand, ".")]
        break

a = Analysis(
    ["server_agent.py"], pathex=["."], binaries=engine_binaries, datas=[],
    hiddenimports=["agent.worker", "agent.engine_bridge", "agent.transport", "agent.relay", "agent.sysmetrics", "psutil", "paho.mqtt.client"], hookspath=[], hooksconfig={},
    runtime_hooks=[], excludes=["PySide6", "qfluentwidgets", "torch", "torchvision", "torchaudio", "tensorflow", "scipy", "pandas", "matplotlib", "IPython", "jupyter", "notebook", "cv2", "PIL.ImageQt"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="FlowBench-Agent", console=True, icon="app.ico")
