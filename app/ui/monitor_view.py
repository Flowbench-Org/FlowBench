"""监控面板：资源卡片、可暂停实时曲线与采样历史导出。

“监控对象”可以在本机和服务器节点之间切换：服务器节点每秒采样一次 CPU、
内存、网络、TCP 连接数和进程数，随心跳发回，显示方式与本机相同。
"""
import csv
import re
from collections import deque
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFileDialog, QGridLayout, QHBoxLayout, QLabel,
                               QProgressBar, QVBoxLayout, QWidget)
from qfluentwidgets import (CaptionLabel, ComboBox, InfoBar, PushButton,
                            ScrollArea, SimpleCardWidget, StrongBodyLabel,
                            SubtitleLabel, isDarkTheme, qconfig,
                            setCustomStyleSheet)

from app.services.monitor import monitor
from app.services.remote_monitor import remote_monitor
from app.ui.charts import ACCENT, GREEN, PURPLE, HoverChart
from app.ui.i18n import L

WINDOW_POINTS = 60  # 最近 60 个采样点（约 1 分钟，每秒一次）
MAX_WINDOW_POINTS = 900
WINDOW_OPTIONS = (60, 300, 900)
CSV_FIELDS = (
    "timestamp", "cpu_percent", "memory_percent", "memory_used_gb",
    "memory_total_gb", "download_kbs", "upload_kbs", "tcp_connections",
    "process_count",
)


def _usage_color(pct: float) -> str:
    if pct < 60:
        return "#107C10"
    elif pct < 85:
        return "#F2B010"
    else:
        return "#D13438"


def _bar_bg_color() -> str:
    if isDarkTheme():
        return "rgba(255,255,255,0.08)"
    else:
        return "rgba(0,0,0,0.06)"


def _subtle_text_color() -> str:
    if isDarkTheme():
        return "#999"
    else:
        return "#8A8A8A"


class StatTile(SimpleCardWidget):
    """普通统计卡片（用于 TCP 连接数、进程数等）。"""

    def __init__(self, title, color=ACCENT, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.setSpacing(6)
        # 标题 - 使用 qfluentwidgets 的 CaptionLabel 自动适配主题
        self.titleLabel = CaptionLabel(title, self)
        lay.addWidget(self.titleLabel)
        # 大数值
        self.value = QLabel("--")
        self.value.setStyleSheet(f"font-size:32px; font-weight:700; color:{color}; background:transparent;")
        lay.addWidget(self.value)
        lay.addStretch(1)
        self.setMinimumHeight(120)

    def set(self, text):
        self.value.setText(text)


class PercentTile(SimpleCardWidget):
    """带进度条的百分比卡片（CPU / 内存专用）。"""

    def __init__(self, title, color=ACCENT, parent=None):
        super().__init__(parent)
        self._base_color = color
        self._cur_color = color
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.setSpacing(8)

        self.titleLabel = CaptionLabel(title, self)
        lay.addWidget(self.titleLabel)

        self.valueLabel = QLabel("-- %", self)
        self.valueLabel.setStyleSheet(
            f"font-size:36px; font-weight:700; color:{color}; background:transparent;")
        lay.addWidget(self.valueLabel)

        self.bar = QProgressBar(self)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self._apply_bar_style(color)
        lay.addWidget(self.bar)

        self.detailLabel = QLabel("", self)
        self.detailLabel.setStyleSheet(
            f"font-size:12px; color:{_subtle_text_color()}; background:transparent;")
        lay.addWidget(self.detailLabel)

        lay.addStretch(1)
        self.setMinimumHeight(150)
        # 主题切换时刷新次要文字颜色与进度条轨道（初始化时写死会残留旧主题的颜色）
        qconfig.themeChanged.connect(self._refresh_theme_colors)

    def _refresh_theme_colors(self, *_):
        self.detailLabel.setStyleSheet(
            f"font-size:12px; color:{_subtle_text_color()}; background:transparent;")
        self._apply_bar_style(self._cur_color)

    def _apply_bar_style(self, color: str):
        bg = _bar_bg_color()
        self.bar.setStyleSheet(f"""
            QProgressBar {{
                background-color: {bg};
                border: none;
                border-radius: 3px;
            }}
            QProgressBar::chunk {{
                border-radius: 3px;
                background-color: {color};
            }}
        """)

    def reset(self):
        """没有数据时显示占位符。"""
        self._cur_color = self._base_color
        self.valueLabel.setText("-- %")
        self.valueLabel.setStyleSheet(
            f"font-size:36px; font-weight:700; color:{self._base_color}; background:transparent;")
        self.bar.setValue(0)
        self._apply_bar_style(self._base_color)
        self.detailLabel.setText("")
        self.detailLabel.setVisible(False)

    def set_percent(self, pct: float, detail: str = ""):
        pct = max(0.0, min(100.0, pct))
        c = _usage_color(pct)
        self._cur_color = c
        self.valueLabel.setText(f"{pct:.1f} %")
        self.valueLabel.setStyleSheet(
            f"font-size:36px; font-weight:700; color:{c}; background:transparent;")
        self.bar.setValue(int(round(pct)))
        self._apply_bar_style(c)
        self.detailLabel.setText(detail)
        self.detailLabel.setVisible(bool(detail))


class MonitorView(ScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("monitorView")
        self._samples = deque(maxlen=MAX_WINDOW_POINTS)   # 本机采样，切到服务器时也继续记录
        self._window_points = WINDOW_POINTS
        self._plot_paused = False
        self._source = ""            # 当前监控对象："" 为本机，否则为服务器节点 ID
        self._source_items = []
        self._updating_sources = False
        self.view = QWidget(self)
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.enableTransparentBackground()

        root = QVBoxLayout(self.view)
        root.setContentsMargins(28, 22, 28, 28)
        root.setSpacing(14)

        # 标题与实时曲线工具栏
        header = QVBoxLayout()
        header.setSpacing(8)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_row.addWidget(SubtitleLabel(L("监控面板", "Monitor"), self.view))
        title_row.addStretch(1)
        title_row.addWidget(CaptionLabel(L("监控对象", "Source"), self.view))
        self.sourceCombo = ComboBox(self.view)
        self.sourceCombo.setMinimumWidth(220)
        self.sourceCombo.setToolTip(L(
            "选择监控本机还是某台服务器节点。服务器节点列表来自“服务器节点”页的控制通道。",
            "Monitor this PC or a server agent. Servers come from the control channel "
            "on the Server Agents page."))
        self.sourceCombo.currentIndexChanged.connect(self._on_source_changed)
        title_row.addWidget(self.sourceCombo)
        header.addLayout(title_row)
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        action_row.addWidget(CaptionLabel(L("时间窗", "Window"), self.view))
        self.windowCombo = ComboBox(self.view)
        self.windowCombo.addItems([
            L("1 分钟", "1 min"), L("5 分钟", "5 min"), L("15 分钟", "15 min")
        ])
        self.windowCombo.setCurrentIndex(0)
        self.windowCombo.setMinimumWidth(92)
        self.windowCombo.currentIndexChanged.connect(self._on_window_changed)
        action_row.addWidget(self.windowCombo)
        self.pauseBtn = PushButton(L("暂停绘图", "Pause Charts"), self.view)
        self.pauseBtn.clicked.connect(self._toggle_plot_pause)
        action_row.addWidget(self.pauseBtn)
        self.clearBtn = PushButton(L("清空历史", "Clear History"), self.view)
        self.clearBtn.clicked.connect(self._clear_history)
        action_row.addWidget(self.clearBtn)
        self.exportBtn = PushButton(L("导出 CSV", "Export CSV"), self.view)
        self.exportBtn.clicked.connect(self._export_csv)
        action_row.addWidget(self.exportBtn)
        action_row.addStretch(1)
        header.addLayout(action_row)
        # 服务器的在线状态、等待数据、旧版节点等提示；本机且无提示时隐藏
        self.sourceStatus = CaptionLabel("", self.view)
        self.sourceStatus.setWordWrap(True)
        self.sourceStatus.hide()
        header.addWidget(self.sourceStatus)
        root.addLayout(header)

        grid = QGridLayout()
        self._metrics_grid = grid
        grid.setSpacing(14)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self.tCpu = PercentTile(L("CPU 使用率", "CPU Usage"), ACCENT)
        self.tMem = PercentTile(L("内存", "Memory"), PURPLE)
        self.tTcp = StatTile(L("TCP 连接数", "TCP Connections"), GREEN)
        self.tProc = StatTile(L("进程数", "Processes"), ACCENT)
        self._metric_tiles = (self.tCpu, self.tMem, self.tTcp, self.tProc)
        self._tiles_single_column = False
        for tile in self._metric_tiles:
            tile.setMinimumWidth(250)
        grid.addWidget(self.tCpu, 0, 0)
        grid.addWidget(self.tMem, 0, 1)
        grid.addWidget(self.tTcp, 1, 0)
        grid.addWidget(self.tProc, 1, 1)
        root.addLayout(grid)

        # CPU / 内存：百分比 0-100，Y 轴下限固定为 0
        cpu_card = SimpleCardWidget(self.view)
        cl = QVBoxLayout(cpu_card)
        cl.setContentsMargins(20, 16, 20, 12)
        cl.addWidget(StrongBodyLabel(L("CPU / 内存趋势", "CPU / Memory Trend"), cpu_card))
        self.cpuPlot = HoverChart(window_points=WINDOW_POINTS, y_min=0.0)
        self.cpuPlot.addLegend(offset=(8, 8))
        self._cpu_name = L("CPU %", "CPU %")
        self._mem_name = L("内存 %", "Memory %")
        self.cpuPlot.add_series(self._cpu_name, ACCENT, " %")
        self.cpuPlot.add_series(self._mem_name, PURPLE, " %")
        cl.addWidget(self.cpuPlot, 1)
        root.addWidget(cpu_card, 1)

        # 网络速率：非负值，Y 轴自动适配
        net_card = SimpleCardWidget(self.view)
        nl = QVBoxLayout(net_card)
        nl.setContentsMargins(20, 16, 20, 12)
        nl.addWidget(StrongBodyLabel(L("网络速率", "Network Throughput"), net_card))
        self.netPlot = HoverChart(window_points=WINDOW_POINTS, y_min=0.0)
        self.netPlot.addLegend(offset=(8, 8))
        self._down_name = L("下行 KB/s", "Download KB/s")
        self._up_name = L("上行 KB/s", "Upload KB/s")
        self.netPlot.add_series(self._down_name, ACCENT, " KB/s")
        self.netPlot.add_series(self._up_name, GREEN, " KB/s")
        nl.addWidget(self.netPlot, 1)
        root.addWidget(net_card, 1)

        monitor.updated.connect(self._on_update)
        remote_monitor.changed.connect(self._on_remote_changed)
        self._sync_sources()
        self._update_source_status()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        single = self.width() < 980
        if single == self._tiles_single_column:
            return
        self._tiles_single_column = single
        grid = self._metrics_grid
        for tile in self._metric_tiles:
            grid.removeWidget(tile)
        for index, tile in enumerate(self._metric_tiles):
            row, column = (index, 0) if single else divmod(index, 2)
            grid.addWidget(tile, row, column)

    def _on_update(self, d):
        try:
            import psutil
            process_count = len(psutil.pids())
        except Exception:
            process_count = -1
        # 无论曲线是否暂停、当前看的是不是本机，本机采样都持续写入固定长度缓存。
        sample = {
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "cpu_percent": float(d["cpu"]),
            "memory_percent": float(d["mem_percent"]),
            "memory_used_gb": float(d["mem_used_gb"]),
            "memory_total_gb": float(d["mem_total_gb"]),
            "download_kbs": float(d["down_kbs"]),
            "upload_kbs": float(d["up_kbs"]),
            "tcp_connections": int(d["tcp_conns"]),
            "process_count": process_count,
        }
        self._samples.append(sample)
        if self._source:
            return
        self._show_sample(sample)
        if not self._plot_paused:
            self._refresh_plots()

    def _show_sample(self, sample):
        """把一条采样（本机或服务器）显示到四张卡片上。"""
        cpu = float(sample["cpu_percent"])
        mem_pct = float(sample["memory_percent"])
        mem_used = float(sample["memory_used_gb"])
        mem_total = float(sample["memory_total_gb"])
        mem_free = mem_total - mem_used

        self.tCpu.set_percent(cpu, L(
            f"剩余 {100 - cpu:.1f}% 可用",
            f"{100 - cpu:.1f}% available"
        ))
        self.tMem.set_percent(mem_pct, L(
            f"{mem_used:.1f} / {mem_total:.1f} GB  剩余 {mem_free:.1f} GB",
            f"{mem_used:.1f} / {mem_total:.1f} GB  {mem_free:.1f} GB free"
        ))
        tcp_conns = int(sample["tcp_connections"])
        self.tTcp.set(str(tcp_conns) if tcp_conns >= 0 else L("无权限", "N/A"))
        process_count = int(sample["process_count"])
        self.tProc.set(str(process_count) if process_count >= 0 else "--")

    def _show_empty(self):
        self.tCpu.reset()
        self.tMem.reset()
        self.tTcp.set("--")
        self.tProc.set("--")

    def _current_samples(self):
        if self._source:
            return remote_monitor.samples(self._source)
        return list(self._samples)

    def _show_current(self):
        samples = self._current_samples()
        if samples:
            self._show_sample(samples[-1])
        else:
            self._show_empty()

    # ---------- 监控对象：本机 / 服务器节点 ----------

    def _sync_sources(self):
        """按最新的服务器节点列表重建“监控对象”下拉框，保留当前选择。"""
        agents = remote_monitor.agents()
        names = [agent["name"] for agent in agents]
        items = [("", L("本机", "This PC"))]
        for agent in agents:
            label = agent["name"]
            if names.count(label) > 1:
                label = f"{label} ({agent['agent_id']})"
            if not agent["online"]:
                label = L(f"{label}（离线）", f"{label} (offline)")
            items.append((agent["agent_id"], label))
        if items == self._source_items:
            return
        self._source_items = items
        ids = [agent_id for agent_id, _label in items]
        self._updating_sources = True
        try:
            self.sourceCombo.clear()
            for agent_id, label in items:
                self.sourceCombo.addItem(label, userData=agent_id)
            self.sourceCombo.setCurrentIndex(ids.index(self._source) if self._source in ids else 0)
        finally:
            self._updating_sources = False
        if self._source not in ids:          # 这台服务器已从节点列表中移除
            self._set_source("")

    def _on_source_changed(self, index: int):
        if self._updating_sources:
            return
        self._set_source(str(self.sourceCombo.itemData(index) or ""))

    def _set_source(self, agent_id: str):
        self._source = agent_id
        remote_monitor.watching = agent_id
        if agent_id:
            remote_monitor.request_refresh()
        # 换了对象，退出手动缩放，并立即显示它的数据（暂停时也一样）。
        self.cpuPlot.set_window_points(self._window_points)
        self.netPlot.set_window_points(self._window_points)
        self._show_current()
        self._refresh_plots()
        self._update_source_status()

    def _on_remote_changed(self):
        self._sync_sources()
        self._update_source_status()
        if self._source:
            self._show_current()
            if not self._plot_paused:
                self._refresh_plots()

    def _source_status(self):
        """(提示文字, 是否需要提醒) —— 显示在工具栏下方。"""
        if not self._source:
            if not remote_monitor.agents() and remote_monitor.connection in {"unknown", "unconfigured"}:
                return L(
                    "在“服务器节点”页连接 Hub 或中继后，可以在右上角“监控对象”里切换到服务器，"
                    "查看它的 CPU、内存和网络。",
                    "Connect a Hub or relay on the Server Agents page, then pick a server under "
                    "Source to see its CPU, memory and network."), False
            return "", False
        info = remote_monitor.agent(self._source)
        if info is None:
            return "", False
        if remote_monitor.connection == "error":
            return L(f"无法连接控制通道：{remote_monitor.connection_error}",
                     f"Cannot reach the control channel: {remote_monitor.connection_error}"), True
        if info["status"] == "unsupported":
            return L(
                "这台服务器上的节点版本较旧，不会上报 CPU、内存和网络数据。请在“服务器节点”页重新生成"
                "节点包，替换服务器上的节点文件并重启节点。",
                "The agent on this server is an older version that does not report CPU, memory or "
                "network data. Generate the node again on the Server Agents page, replace the agent "
                "files on the server and restart the agent."), True
        if info["status"] == "unavailable":
            return L(f"这台服务器读不到系统数据：{info['error']}",
                     f"This server cannot read its system data: {info['error']}"), True
        if not info["samples"]:
            return L("正在等待服务器节点上报数据…", "Waiting for the server agent to report…"), False
        if not info["online"]:
            return L("服务器节点已离线，下面是最后收到的数据。",
                     "The server agent is offline; showing the last data received."), True
        host = info["hostname"] or info["name"]
        return L(f"在线：{host}。每秒采样一次，约每 2 秒刷新。",
                 f"Online: {host}. Sampled every second, refreshed about every 2 seconds."), False

    def _update_source_status(self):
        text, warn = self._source_status()
        self.sourceStatus.setText(text)
        self.sourceStatus.setVisible(bool(text))
        if warn:
            setCustomStyleSheet(self.sourceStatus,
                                "FluentLabelBase{color:#9d5d00;}", "FluentLabelBase{color:#fce100;}")
        else:
            setCustomStyleSheet(self.sourceStatus, "", "")

    def showEvent(self, event):
        super().showEvent(event)
        remote_monitor.page_visible = True
        remote_monitor.request_refresh()

    def hideEvent(self, event):
        super().hideEvent(event)
        remote_monitor.page_visible = False

    def _refresh_plots(self):
        samples = self._current_samples()[-self._window_points:]
        self.cpuPlot.set_data({
            self._cpu_name: [s["cpu_percent"] for s in samples],
            self._mem_name: [s["memory_percent"] for s in samples],
        })
        self.netPlot.set_data({
            self._down_name: [s["download_kbs"] for s in samples],
            self._up_name: [s["upload_kbs"] for s in samples],
        })

    def _toggle_plot_pause(self):
        self._plot_paused = not self._plot_paused
        self.pauseBtn.setText(
            L("继续绘图", "Resume Charts") if self._plot_paused
            else L("暂停绘图", "Pause Charts")
        )
        if not self._plot_paused:
            self._refresh_plots()

    def _clear_history(self):
        if self._source:
            remote_monitor.clear(self._source)
        else:
            self._samples.clear()
        # 清空同时退出手动缩放，确保下一条采样能立即重新出现在视野中。
        self.cpuPlot.set_window_points(self._window_points)
        self.netPlot.set_window_points(self._window_points)
        self.cpuPlot.set_data({self._cpu_name: [], self._mem_name: []})
        self.netPlot.set_data({self._down_name: [], self._up_name: []})

    def _on_window_changed(self, index: int):
        if not 0 <= index < len(WINDOW_OPTIONS):
            return
        self._window_points = WINDOW_OPTIONS[index]
        self.cpuPlot.set_window_points(self._window_points)
        self.netPlot.set_window_points(self._window_points)
        # 直接操作时间窗应立即生效；暂停只阻止后台采样触发重绘。
        self._refresh_plots()

    def _export_csv(self):
        samples = self._current_samples()
        if not samples:
            InfoBar.warning(
                L("暂无数据", "No data"),
                L("当前没有可导出的监控历史", "There is no monitoring history to export"),
                parent=self.window(),
            )
            return
        source = ""
        if self._source:
            info = remote_monitor.agent(self._source) or {}
            source = re.sub(r"[^\w.-]+", "_", str(info.get("name") or self._source)).strip("_") + "-"
        default_name = ("flowbench-monitor-" + source
                        + datetime.now().strftime("%Y%m%d-%H%M%S") + ".csv")
        path, _ = QFileDialog.getSaveFileName(
            self, L("导出监控数据", "Export monitoring data"), default_name,
            L("CSV 文件 (*.csv)", "CSV files (*.csv)"),
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
                writer.writeheader()
                writer.writerows(samples)
            InfoBar.success(
                L("导出成功", "Exported"),
                L(f"已导出 {len(samples)} 条监控记录", f"Exported {len(samples)} monitoring samples"),
                parent=self.window(),
            )
        except Exception as e:
            InfoBar.error(
                L("导出失败", "Export failed"), str(e), parent=self.window()
            )
