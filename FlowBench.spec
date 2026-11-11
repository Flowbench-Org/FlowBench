# -*- mode: python ; coding: utf-8 -*-
# FlowBench 打包配置（onedir 模式，供 Inno Setup 打安装包）：
import os

# 排除环境中无关的可编辑安装包（phantom_backend 等），
# 避免把 torch/scipy 等巨型依赖拖进 exe。

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('app.ico', '.'),
        ('app_logo.png', '.'),
        ('app_logo_dark.png', '.'),
        # Server-agent payload used by the Generate server node button.
        ('dist/FlowBench-Agent.exe', 'agent_template/windows'),
        ('dist/FlowBench-Hub.exe', 'agent_template/hub'),
        ('server_agent.py', 'agent_template/linux'),
        ('agent/hub.py', 'agent_template/linux/agent'),
        ('agent/worker.py', 'agent_template/linux/agent'),
        ('agent/transport.py', 'agent_template/linux/agent'),
        ('agent/relay.py', 'agent_template/linux/agent'),
        ('agent/sysmetrics.py', 'agent_template/linux/agent'),
        ('agent/__init__.py', 'agent_template/linux/agent'),
    ],
    hiddenimports=['shiboken6.Shiboken'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'torch', 'torchvision', 'torchaudio', 'tensorflow', 'numpy.testing',
        'scipy', 'pandas', 'matplotlib', 'phantom_backend', 'IPython',
        'jupyter', 'notebook', 'cv2', 'PIL.ImageQt',
    ],
    noarchive=False,
    optimize=0,
)


# PyInstaller can collect an incompatible ICU runtime from another dependency.
# Qt uses the ICU runtime shipped with Windows; bundling ICU 78 hides required
# Qt entry points and breaks QtCore loading.
a.binaries = [
    item for item in a.binaries
    if os.path.basename(item[0]).lower() not in {'icuuc.dll', 'icudt78.dll'}
]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='FlowBench',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='app.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='FlowBench',
)
