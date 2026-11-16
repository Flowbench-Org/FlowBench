# -*- mode: python ; coding: utf-8 -*-
# FlowBench 打包配置（onedir 模式，再由 tools/make_payload.py 和 Setup.spec 打成自带的安装程序）：
import os
import sys

# Embed a Linux native engine so generated node bundles ship it to Linux
# servers. First found wins: FLOWBENCH_LINUX_ENGINE, a fresh build from
# build_engine.sh (engine/target/release/flowbench-engine), then the committed
# glibc-2.17 build in engine/prebuilt (see its README). A node that cannot run
# it falls back to the Python worker.
_linux_engine = next((path for path in (
    os.environ.get('FLOWBENCH_LINUX_ENGINE') or '',
    'engine/target/release/flowbench-engine',
    'engine/prebuilt/linux-x86_64/flowbench-engine',
) if path and os.path.isfile(path)), '')
_engine_datas = [(_linux_engine, 'agent_template/linux')] if _linux_engine else []

# Bundle the native engine beside the desktop app for its own local stress
# runs (HTTP/HTTPS/TCP). Staged by build_engine.ps1 (repo root) or build_engine.sh
# (engine/target/release). Absent, local runs use the Python engine.
_desktop_engine = []
_engine_name = 'flowbench-engine.exe' if sys.platform.startswith('win') else 'flowbench-engine'
for _cand in (_engine_name, os.path.join('engine', 'target', 'release', _engine_name)):
    if os.path.isfile(_cand):
        _desktop_engine = [(_cand, '.')]
        break

# 排除环境中无关的可编辑安装包（phantom_backend 等），
# 避免把 torch/scipy 等巨型依赖拖进 exe。

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=_desktop_engine,
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
        ('agent/engine_bridge.py', 'agent_template/linux/agent'),
        ('agent/transport.py', 'agent_template/linux/agent'),
        ('agent/relay.py', 'agent_template/linux/agent'),
        ('agent/sysmetrics.py', 'agent_template/linux/agent'),
        ('agent/__init__.py', 'agent_template/linux/agent'),
        *_engine_datas,
    ],
    hiddenimports=['shiboken6.Shiboken', 'agent.engine_bridge', 'agent.worker'],
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
