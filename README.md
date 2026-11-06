<div align="center">
<img src="./LargeLogo-light-preview.png" alt="FlowBench Logo" width="577" height="184">
</div>

# FlowBench

**Legally-Authorized Network Stress Testing & Performance Monitoring Tool**

**合法授权网络压力测试与性能监控工具**

[![Platform](https://img.shields.io/badge/Platform-Windows-blue)](https://github.com) [![Python](https://img.shields.io/badge/Python-3.10+-green)](https://www.python.org) [![License](https://img.shields.io/badge/License-MIT-orange)](LICENSE)

---

## 🇬🇧 English

### Introduction

FlowBench is a Windows desktop application for network stress testing and performance monitoring, built with **Python + PySide6 + QFluentWidgets** following the **Windows 11 Fluent Design** language. A **legal-compliance framework** is built in: every test target must pass an authorization confirmation first, rate and concurrency are capped by a token bucket, and all operations are written to an audit log.

> ⚠️ **Disclaimer**: This tool is for learning, research, and performance testing **with written authorization only**. Stress-testing unauthorized targets is illegal; the user bears full legal responsibility.

### Features

| Module | Description |
|--------|-------------|
| 📊 **Dashboard** | Real-time CPU / memory / network cards and trend charts |
| 🔥 **Stress Test** | HTTP / HTTPS / TCP / UDP / ICMP protocols; live QPS, latency percentiles (P50/P90/P99), **total sent traffic** (auto-scaled B/KB/MB/GB/TB), classified failure reasons |
| 🤝 **Collaborative Test** | Host / node modes, one-click invite code copy, IPv4+IPv6 dual-stack listening, UPnP automatic port mapping (Internet collaboration supported) |
| 🧩 **Plugin System** | Full plugin platform: local plugin management (enable/disable/reload), **built-in plugin marketplace**, one-click install & update, custom icons, and **one-click publish/unpublish to GitHub** — no manual token required |
| 📈 **Monitoring** | Real-time CPU / memory / network speed curves, smooth animated wheel zoom on the time axis, drag to pan when zoomed, double-click to resume live scrolling |
| ⚙️ **Settings** | Dark / light theme, Chinese-English language selection (restart to fully apply), default parameters, audit log export |

### Security & Compliance

- **First-launch disclaimer**: must read and accept before use
- **Authorized target list**: each target requires separate authorization (with note & timestamp)
- **High-rate double confirmation**: re-confirmation dialog above 500 QPS
- **Token-bucket rate limiting**: shared across threads, never exceeds the configured rate
- **Audit log**: start / stop / authorization events persisted to disk

### Plugin System (New in v1.1.0)

FlowBench v1.1.0 ships with a full plugin platform — extend the app with your own features:

- **Plugin Marketplace**: browse, search, install and update community plugins with one click (integrity-verified via SHA-256)
- **Local Plugin Management**: enable / disable / reload / remove plugins instantly; metadata and icons stay visible when disabled
- **One-click Publish**: publish your own plugins to the marketplace directly from the app via GitHub OAuth device flow — no manual token generation. External submissions create pull requests for manual maintainer review
- **One-click Unpublish**: plugin authors can remove their own listings at any time
- **Custom Icons**: plugin authors can upload a PNG/JPG icon (also supports built-in Fluent icons); auto-generated colored initial badge as fallback
- **Rich Plugin API**: register custom protocols, exporters, target providers and metrics subscriptions; respond to test lifecycle events
- **Guarded lifecycle**: plugin load/page errors are logged and isolated where possible; plugins still run in-process and must be trusted

Check the built-in `example_hello.py` / `example_dns.py` plugins in the plugin folder for API usage examples. **Want to write your own?** See the [**Plugin Development Guide**](PLUGINS.md) — a minimal plugin is about 10 lines of Python.

### Install (End Users)

1. Go to the [**Releases**](https://github.com/Carlown/FlowBench/releases/latest) page
2. Download the latest `FlowBench-Setup-x.x.x.exe`
3. Run the installer and follow the wizard (desktop shortcut optional). It installs for the
   current user in `%LOCALAPPDATA%\Programs\FlowBench` without administrator rights, and removes
   an older Inno Setup install (1.2.7 and earlier) automatically
4. Launch from the Start menu or desktop

Later updates install from inside the app: **Settings → Check for Updates → Update now** downloads
the new installer with a progress bar, closes FlowBench, updates it and reopens it. If the download
fails, **Open in browser** opens the release page so you can download the installer yourself and run
it. Uninstall from
Windows **Settings → Apps**: the uninstaller uses the same window as setup, in FlowBench's language
and theme. Settings, plugins and logs in `%APPDATA%\FlowBench` are kept unless you tick
**Also delete my settings, plugins and logs**.

### Run from Source (Developers)

```bash
git clone https://github.com/Carlown/FlowBench.git
cd FlowBenchPy
pip install -r requirements.txt
python main.py
```

### Build from Source

One step (stops at the first failure and prints the installer path):

```bash
powershell -ExecutionPolicy Bypass -File build_installer.ps1
# Output: installer/FlowBench-Setup-1.2.10.exe
```

Only Python and PyInstaller are needed; the installer is FlowBench's own (`flowbench_installer/`),
so Inno Setup is no longer required. Or step by step:

```bash
# 1. Build the server utilities and GUI with all icon/logo resources
powershell -File build_all.ps1

# 2. Build the uninstaller, pack the payload, build the installer
python -m PyInstaller --clean --noconfirm Uninstall.spec
python tools/make_payload.py
python -m PyInstaller --clean --noconfirm Setup.spec
# Output: dist/FlowBench-Setup-1.2.10.exe
```

To publish, upload `FlowBench-Setup-<version>.exe` to the GitHub release under exactly that
name: the in-app updater looks for it.

`app.ico` is the fixed icon for executables, shortcuts, the installer and the system tray.
The splash uses `app_logo.png` for light mode and `app_logo_dark.png` for dark mode,
according to the saved theme (light by default). Both PNGs are bundled by `FlowBench.spec`.

### Tech Stack

- **UI**: PySide6 + QFluentWidgets (Fluent Design)
- **Charts**: PySide6 QtCharts (native real-time curves)
- **System monitoring**: psutil
- **Networking**: requests / socket / icmplib
- **Rate limiting**: custom token-bucket algorithm
- **Storage**: JSON (`%APPDATA%\FlowBench`)
- **Packaging**: PyInstaller + FlowBench's own installer (`flowbench_installer/`)

---

## 🇨🇳 中文说明

### 简介

FlowBench 是一款 Windows 桌面端的网络压力测试与性能监控工具，基于 **Python + PySide6 + QFluentWidgets** 构建，采用 **Windows 11 Fluent Design** 设计语言。工具内置**法律合规框架**：所有测试目标必须先通过授权确认，速率与并发受令牌桶限速保护，全部操作写入审计日志。

> ⚠️ **免责声明**：本工具仅供学习研究与获得书面授权的性能测试使用。对未授权目标发起压力测试属于违法行为，使用者需自行承担全部法律责任。

### 功能特性

| 模块 | 说明 |
|------|------|
| 📊 **仪表盘** | CPU / 内存 / 网络实时监控卡片与趋势图 |
| 🔥 **压力测试** | HTTP / HTTPS / TCP / UDP / ICMP 五种协议，实时 QPS、延迟分位数（P50/P90/P99）、**总发送流量统计**（自动换算 B/KB/MB/GB/TB）、失败原因分类统计 |
| 🤝 **协同测试** | 主控邀请 / 节点加入模式，邀请码一键复制，IPv4+IPv6 双栈监听，UPnP 自动端口映射（支持外网协同） |
| 🧩 **插件系统** | 完整插件平台：本地插件管理（启用/禁用/重载）、**内置插件市场**、一键安装与更新、自定义图标、**一键发布/下架到 GitHub** —— 无需手动生成令牌 |
| 📈 **性能监控** | CPU / 内存 / 网速实时曲线，鼠标滚轮带动画平滑缩放时间段，放大后可左右拖动，双击恢复实时滚动 |
| ⚙️ **设置** | 深色 / 浅色主题，中英双语选择（重启后完整生效），默认参数配置，审计日志导出 |

### 安全与合规设计

- **首次启动免责声明**：必须阅读并同意方可使用
- **目标授权清单**：每个测试目标需单独确认授权（含备注与时间戳）
- **高速率二次确认**：速率超过 500 QPS 时弹出再次确认
- **令牌桶限速**：多线程共享令牌桶，严格不超设定速率
- **审计日志**：开始 / 停止 / 授权等关键操作全部落盘

### 插件系统（v1.1.0 新增）

FlowBench v1.1.0 内置完整插件平台，可以自由扩展功能：

- **插件市场**：浏览、搜索、一键安装和更新社区插件（SHA-256 完整性校验）
- **本地插件管理**：即时启用 / 禁用 / 重载 / 删除插件；禁用后图标和元数据保持显示
- **一键发布**：通过 GitHub OAuth 设备授权，直接在软件内把你的插件发布到市场 —— 无需手动生成令牌；外部提交会创建 PR，由维护者人工审核
- **一键下架**：插件作者可随时下架自己发布的插件
- **自定义图标**：发布时可上传 PNG/JPG 图标（也支持内置 Fluent 图标）；未上传时自动生成彩色首字徽章
- **丰富的插件 API**：注册自定义协议、导出器、目标源和指标订阅；响应测试生命周期事件
- **受保护生命周期**：插件加载/页面错误会尽量隔离并记录；插件仍在进程内运行，仅应安装可信插件

插件目录内置 `example_hello.py` / `example_dns.py` 示例插件，可参考其 API 用法。**想自己写一个？** 看 [**插件开发指南**](PLUGINS.md) —— 最小插件只要 10 行 Python。

### 安装（普通用户）

1. 进入 [**Releases**](https://github.com/Carlown/FlowBench/releases/latest) 发布页
2. 下载最新的 `FlowBench-Setup-x.x.x.exe`
3. 双击运行，按向导完成安装（可勾选桌面快捷方式）。默认为当前用户安装到
   `%LOCALAPPDATA%\Programs\FlowBench`，不需要管理员权限；检测到旧版（1.2.7 及以前的
   Inno Setup 安装）会自动卸载
4. 安装后从开始菜单或桌面启动

之后的更新在软件内完成：**设置 → 检查更新 → 立即更新**，带进度条下载新版安装包，
自动关闭 FlowBench、完成更新并重新打开。下载失败时可以点 **用浏览器下载**，
到发布页自己下载安装包后运行。卸载在 Windows **设置 → 应用** 中进行，
卸载程序和安装向导是同一套界面，语言和深浅色跟随 FlowBench 的设置。`%APPDATA%\FlowBench`
里的设置、插件和日志默认保留，勾选 **同时删除我的设置、插件和日志** 才会删除。

### 从源码运行（开发者）

```bash
git clone https://github.com/Carlown/FlowBench.git
cd FlowBenchPy
pip install -r requirements.txt
python main.py
```

### 从源码打包

一键打包（任一步失败立即停止，成功后打印安装包路径）：

```bash
powershell -ExecutionPolicy Bypass -File build_installer.ps1
# 产物位于 installer/FlowBench-Setup-1.2.10.exe
```

只需要 Python 和 PyInstaller。安装程序是 FlowBench 自己的（`flowbench_installer/`），
不再需要 Inno Setup。也可以分步执行：

```bash
# 1. 生成服务器工具与主程序，包含全部图标和启动图资源
powershell -File build_all.ps1

# 2. 生成卸载程序、打包安装数据、生成安装程序
python -m PyInstaller --clean --noconfirm Uninstall.spec
python tools/make_payload.py
python -m PyInstaller --clean --noconfirm Setup.spec
# 产物位于 dist/FlowBench-Setup-1.2.10.exe
```

发布时把 `FlowBench-Setup-<版本号>.exe` 原名上传到 GitHub Release，软件内更新按这个文件名查找。

`app.ico` 固定用于 EXE、快捷方式、安装程序和系统托盘，不随主题切换。
启动画面根据已保存的主题选择 `app_logo.png`（浅色）或 `app_logo_dark.png`（深色），
默认使用浅色；两张 PNG 均由 `FlowBench.spec` 打包。

### 技术栈

- **界面**：PySide6 + QFluentWidgets（Fluent Design）
- **图表**：PySide6 QtCharts（原生实时曲线）
- **系统监控**：psutil
- **网络**：requests / socket / icmplib
- **限速**：自研令牌桶算法
- **存储**：JSON（`%APPDATA%\FlowBench`）
- **打包**：PyInstaller + FlowBench 自带安装程序（`flowbench_installer/`）

---

## License / 许可证

MIT License
## Server Agent (additive)

如果需要让服务器作为独立节点上线，而不是把桌面 GUI 搬到服务器，请看 [`SERVER_AGENT.md`](SERVER_AGENT.md)。它新增了跨平台 headless Agent、Hub、控制 CLI 和打包脚本；现有 GUI 与原功能保持不变。
