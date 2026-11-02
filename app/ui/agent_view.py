"""Local GUI page for controlling the additive server-agent feature.

The Stress page remains the single source of truth for a job configuration.
This page intentionally contains only connection/node controls and start/stop
buttons; it does not duplicate target, protocol, or rate inputs.
"""
from __future__ import annotations

import ipaddress
import json
import re
import secrets
import socket
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (QBoxLayout, QFileDialog, QGridLayout, QHBoxLayout,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)
from qfluentwidgets import (BodyLabel, CaptionLabel, CheckBox, ComboBox, FluentIcon as FIF,
                            FluentStyleSheet, InfoBar,
                            MessageBoxBase,
                            InfoBarPosition, LineEdit, PasswordLineEdit,
                            PrimaryPushButton, PushButton, ScrollArea, SearchLineEdit,
                            SimpleCardWidget, StrongBodyLabel, SubtitleLabel,
                            getFont, isDarkTheme, themeColor, Theme)

from app.services.agent_control import ControllerClient, RelayControllerClient
from app.services.auth import add_authorized, build_http_url, is_authorized, normalize_host
from app.services.settings import settings
from app.ui.disclaimer import AuthDialog
from app.ui.i18n import L
from app.ui.stress_view import HIGH_RATE, MiniStat, fmt_bytes


DEFAULT_AGENT_RELAY = "mqtt://broker.hivemq.com:8000"


def agent_search_text(agent) -> str:
    """Return the lower-cased text a server agent can be searched by.

    Covers the display name, agent ID, online state (both UI languages),
    host metadata reported by the agent, and the last job state.
    """
    metadata = agent.get("metadata") or {}
    event = agent.get("last_event") or {}
    parts = [
        agent.get("name"),
        agent.get("agent_id"),
        "online 在线" if agent.get("online") else "offline 离线",
        metadata.get("hostname"),
        metadata.get("platform"),
        event.get("state"),
    ]
    return " ".join(str(part) for part in parts if part).casefold()


def agent_matches(agent, query) -> bool:
    """True when every whitespace-separated term of ``query`` matches the agent."""
    terms = str(query or "").casefold().split()
    if not terms:
        return True
    haystack = agent_search_text(agent)
    return all(term in haystack for term in terms)


def agent_os(agent) -> str:
    """Return "windows", "linux" or "" from the platform string the agent reports."""
    platform_name = str((agent.get("metadata") or {}).get("platform") or "").casefold()
    if "windows" in platform_name:
        return "windows"
    if "linux" in platform_name:
        return "linux"
    return ""


def _paint_os_badge(painter, rect, os_name):
    """Draw a rounded OS badge: Windows panes, a small Tux, or a generic server."""
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.translate(rect.topLeft())
    painter.scale(rect.width() / 34, rect.height() / 34)
    badge = QRectF(0, 0, 34, 34)
    if os_name == "windows":
        painter.setBrush(QColor("#0078D4"))
        painter.drawRoundedRect(badge, 8, 8)
        painter.setBrush(QColor("#FFFFFF"))
        for x, y in ((9, 9), (17.6, 9), (9, 17.6), (17.6, 17.6)):
            painter.drawRect(QRectF(x, y, 7.4, 7.4))
    elif os_name == "linux":
        painter.setBrush(QColor("#FFC83D"))
        painter.drawRoundedRect(badge, 8, 8)
        painter.setBrush(QColor("#1F1F1F"))
        painter.drawEllipse(QRectF(9.5, 4.5, 15, 24))           # body
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawEllipse(QRectF(12.2, 12.5, 9.6, 14.5))      # belly
        painter.drawEllipse(QRectF(13.2, 7.6, 3.4, 4.4))        # eyes
        painter.drawEllipse(QRectF(17.4, 7.6, 3.4, 4.4))
        painter.setBrush(QColor("#1F1F1F"))
        painter.drawEllipse(QRectF(14.6, 9.2, 1.6, 2.2))        # pupils
        painter.drawEllipse(QRectF(17.8, 9.2, 1.6, 2.2))
        painter.setBrush(QColor("#F7931E"))
        painter.drawEllipse(QRectF(14.3, 11.6, 5.4, 3.0))       # beak
        painter.drawEllipse(QRectF(9.0, 26.4, 7.6, 3.8))        # feet
        painter.drawEllipse(QRectF(17.4, 26.4, 7.6, 3.8))
    else:
        painter.setBrush(QColor("#5C6670"))
        painter.drawRoundedRect(badge, 8, 8)
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawRoundedRect(QRectF(9, 9.5, 16, 6.5), 1.6, 1.6)
        painter.drawRoundedRect(QRectF(9, 18, 16, 6.5), 1.6, 1.6)
        painter.setBrush(QColor("#5C6670"))
        painter.drawEllipse(QRectF(11.2, 11.9, 1.8, 1.8))
        painter.drawEllipse(QRectF(11.2, 20.4, 1.8, 1.8))
    painter.restore()


class AgentRow(QWidget):
    """One selectable server: checkbox, OS badge, name, details and online pill."""

    HEIGHT = 60

    def __init__(self, agent, check, parent=None):
        super().__init__(parent)
        self.agent = agent
        self.check = check
        self.os_name = agent_os(agent)
        self._hover = False
        self.setFixedHeight(self.HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 14, 0)
        check.setParent(self)
        # Fluent's CheckBox stylesheet enforces min-width 28px + 1px margin; the
        # OS badge is painted at x=48, so keep the box to exactly that footprint.
        check.setFixedWidth(29)
        layout.addWidget(check)
        layout.addStretch(1)
        check.stateChanged.connect(self.update)

        agent_id = str(agent.get("agent_id") or "")
        metadata = agent.get("metadata") or {}
        hostname = str(metadata.get("hostname") or "").strip()
        os_label = {"windows": "Windows", "linux": "Linux"}.get(self.os_name, L("未知系统", "Unknown OS"))
        self.title = str(agent.get("name") or agent_id)
        self.details = " · ".join(part for part in (os_label, hostname, agent_id) if part)
        self.online = bool(agent.get("online"))
        self.status = L("在线", "online") if self.online else L("离线", "offline")
        platform_name = str(metadata.get("platform") or "").strip()
        self.setToolTip("\n".join(part for part in (self.title, self.details, platform_name) if part))
        self.setAccessibleName(f"{self.title} — {self.details} — {self.status}")

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.check.toggle()
        super().mouseReleaseEvent(event)

    def _pill_colors(self, dark):
        if self.online:
            return ((QColor(108, 203, 95, 40), QColor("#6CCB5F"), QColor("#6CCB5F")) if dark
                    else (QColor(16, 124, 16, 26), QColor("#0F7B0F"), QColor("#13A10E")))
        return ((QColor(255, 255, 255, 20), QColor("#C8C8C8"), QColor("#9A9A9A")) if dark
                else (QColor(0, 0, 0, 14), QColor("#5F5F5F"), QColor("#A0A0A0")))

    def paintEvent(self, event):
        dark = isDarkTheme()
        painter = QPainter(self)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        card = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        accent = QColor(themeColor())
        if self.check.isChecked():
            fill = QColor(accent)
            fill.setAlpha(46 if dark else 22)
            border = QColor(accent)
            border.setAlpha(170 if dark else 150)
        else:
            fill = (QColor(255, 255, 255, 18 if self._hover else 10) if dark
                    else QColor(0, 0, 0, 10 if self._hover else 4))
            border = QColor(255, 255, 255, 22) if dark else QColor(0, 0, 0, 20)
        painter.setPen(QPen(border, 1))
        painter.setBrush(fill)
        painter.drawRoundedRect(card, 8, 8)

        _paint_os_badge(painter, QRectF(48, (self.HEIGHT - 34) / 2, 34, 34), self.os_name)

        pill_font = getFont(12)
        pill_metrics = QFontMetrics(pill_font)
        pill_width = 26 + pill_metrics.horizontalAdvance(self.status)
        pill = QRectF(self.width() - 14 - pill_width, (self.HEIGHT - 22) / 2, pill_width, 22)
        pill_fill, pill_text, dot = self._pill_colors(dark)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(pill_fill)
        painter.drawRoundedRect(pill, 11, 11)
        painter.setBrush(dot)
        painter.drawEllipse(QRectF(pill.left() + 9, pill.center().y() - 3, 6, 6))
        painter.setFont(pill_font)
        painter.setPen(pill_text)
        painter.drawText(pill.adjusted(19, 0, -7, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         self.status)

        text_left = 48 + 34 + 12
        text_width = max(0, int(pill.left() - 12 - text_left))
        title_font = getFont(14, QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(QColor("#FFFFFF") if dark else QColor("#1B1B1B"))
        painter.drawText(QRectF(text_left, 10, text_width, 21),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         QFontMetrics(title_font).elidedText(self.title, Qt.TextElideMode.ElideRight, text_width))
        detail_font = getFont(12)
        painter.setFont(detail_font)
        painter.setPen(QColor(255, 255, 255, 160) if dark else QColor(0, 0, 0, 150))
        painter.drawText(QRectF(text_left, 31, text_width, 18),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         QFontMetrics(detail_font).elidedText(self.details, Qt.TextElideMode.ElideMiddle, text_width))


class AgentSelectionDialog(MessageBoxBase):
    """Dialog for searching and selecting server agents for one operation."""

    def __init__(self, agents, selected_ids=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(L("选择服务器节点", "Select Server Agents"))
        self.widget.setMinimumWidth(600)
        FluentStyleSheet.DIALOG.apply(self, Theme.DARK if isDarkTheme() else Theme.LIGHT)
        self.setMaskColor(QColor(0, 0, 0, 120) if isDarkTheme() else QColor(0, 0, 0, 76))
        self.yesButton.setText(L("应用选择", "Apply selection"))
        self.cancelButton.setText(L("取消", "Cancel"))

        self.viewLayout.addWidget(StrongBodyLabel(
            L("选择要执行任务的服务器", "Choose servers for this task"), self.widget))
        self.viewLayout.addWidget(CaptionLabel(
            L("可以选择一个、多个或全部服务器。离线节点也可以保留在队列中，待上线后执行。",
              "Select one, several, or all servers. Offline nodes can stay queued and run when they come online."),
            self.widget))

        self.searchEdit = SearchLineEdit(self.widget)
        self.searchEdit.setPlaceholderText(L("搜索名称、ID、主机名或状态（如 hk 在线）",
                                             "Search name, ID, hostname or status (e.g. hk online)"))
        self.searchEdit.textChanged.connect(self._apply_filter)
        self.viewLayout.addWidget(self.searchEdit)

        actions = QHBoxLayout()
        self.selectAllBtn = PushButton(L("全选", "Select all"), self.widget)
        self.clearBtn = PushButton(L("清空", "Clear"), self.widget)
        self.selectAllBtn.setToolTip(L("选中当前搜索结果中的全部服务器",
                                       "Select every server in the current search results"))
        self.clearBtn.setToolTip(L("取消当前搜索结果中的选择",
                                   "Unselect the servers in the current search results"))
        self.selectedCount = CaptionLabel("", self.widget)
        self.selectAllBtn.clicked.connect(lambda: self._set_all(True))
        self.clearBtn.clicked.connect(lambda: self._set_all(False))
        actions.addWidget(self.selectAllBtn)
        actions.addWidget(self.clearBtn)
        actions.addStretch(1)
        actions.addWidget(self.selectedCount)
        self.viewLayout.addLayout(actions)

        self._checks = {}
        self._rows = {}
        selected = {str(value) for value in (selected_ids or [])}
        content = QWidget()
        content.setObjectName("agentSelectionList")
        content.setStyleSheet("#agentSelectionList { background: transparent; }")
        content_layout = QVBoxLayout(content)
        # Right margin leaves room for Fluent's overlay scrollbar beside the cards.
        content_layout.setContentsMargins(0, 2, 12, 2)
        content_layout.setSpacing(6)
        for agent in agents or []:
            agent_id = str(agent.get("agent_id") or "").strip()
            if not agent_id:
                continue
            check = CheckBox("", content)
            check.setChecked(agent_id in selected)
            check.stateChanged.connect(self._update_count)
            row = AgentRow(agent, check, content)
            self._checks[agent_id] = check
            self._rows[agent_id] = row
            content_layout.addWidget(row)
        if not self._checks:
            empty = CaptionLabel(L("请先刷新服务器节点列表", "Refresh the server list first"), content)
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setMinimumHeight(AgentRow.HEIGHT)
            content_layout.addWidget(empty)
            self.searchEdit.setEnabled(False)
        self.noMatchLabel = CaptionLabel(L("没有匹配的服务器", "No matching servers"), content)
        self.noMatchLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.noMatchLabel.setMinimumHeight(AgentRow.HEIGHT)
        self.noMatchLabel.hide()
        content_layout.addWidget(self.noMatchLabel)
        content_layout.addStretch(1)
        scroll = ScrollArea(self.widget)
        scroll.setWidgetResizable(True)
        scroll.enableTransparentBackground()
        visible_rows = max(2, min(len(self._rows), 5))
        scroll.setFixedHeight(visible_rows * (AgentRow.HEIGHT + 6) + 4)
        scroll.setWidget(content)
        self.viewLayout.addWidget(scroll)
        self._update_count()
        QTimer.singleShot(0, self.searchEdit.setFocus)

    def _apply_filter(self, *_):
        query = self.searchEdit.text()
        visible = 0
        for row in self._rows.values():
            match = agent_matches(row.agent, query)
            row.setHidden(not match)
            visible += match
        self.noMatchLabel.setHidden(not self._rows or visible > 0)
        self._update_count()

    def visible_ids(self):
        """IDs of the servers shown by the current search, in display order."""
        return [agent_id for agent_id, row in self._rows.items() if not row.isHidden()]

    def _set_all(self, checked):
        # Act on the search results only, so "search hk → Select all" picks
        # just the matching servers and keeps choices made under other searches.
        for agent_id in self.visible_ids():
            self._checks[agent_id].setChecked(checked)
        self._update_count()

    def _update_count(self, *_):
        count = len(self.selected_ids())
        text = L(f"已选择 {count} 个", f"{count} selected")
        if self.searchEdit.text().strip() and self._checks:
            shown, total = len(self.visible_ids()), len(self._checks)
            text += L(f" · 显示 {shown}/{total}", f" · showing {shown}/{total}")
        self.selectedCount.setText(text)

    def selected_ids(self):
        return [agent_id for agent_id, check in self._checks.items() if check.isChecked()]


class GenerateNodeDialog(MessageBoxBase):
    """Fluent-style one-shot dialog for creating a server-node package."""

    def __init__(self, parent=None, has_hub=False, hub_url="", use_relay=False):
        super().__init__(parent)
        self.setWindowTitle(L("生成服务器节点", "Generate Server Node"))
        self.widget.setMinimumWidth(560)
        # MessageBoxBase applies styles during import-time construction; force
        # the current theme so dark mode never falls back to a light dialog.
        FluentStyleSheet.DIALOG.apply(self, Theme.DARK if isDarkTheme() else Theme.LIGHT)
        self.setMaskColor(QColor(0, 0, 0, 120) if isDarkTheme() else QColor(0, 0, 0, 76))
        self.yesButton.setText(L("生成", "Generate"))
        self.cancelButton.setText(L("取消", "Cancel"))

        self.viewLayout.addWidget(StrongBodyLabel(
            L("生成服务器节点", "Generate Server Node"), self.widget))

        description = BodyLabel(L(
            "输入节点信息后点击生成。安装包会自动保存到下面的位置。",
            "Enter the node information and generate it. The bundle will be saved to the location below."),
            self.widget)
        description.setWordWrap(True)
        self.viewLayout.addWidget(description)

        mode_row = QHBoxLayout()
        mode_row.addWidget(BodyLabel(L("连接方式", "Connection"), self.widget))
        self.connectionCombo = ComboBox(self.widget)
        self.connectionCombo.addItems([
            L("Hub 直连（自建或现有 Hub）", "Direct Hub (self-hosted or existing)"),
            L("公共 MQTT 中继（免开放控制端口）", "Public MQTT relay (no inbound control port)"),
        ])
        self.connectionCombo.setCurrentIndex(1 if use_relay else 0)
        self.connectionCombo.currentIndexChanged.connect(self._update_connection_mode)
        mode_row.addWidget(self.connectionCombo, 1)
        self.viewLayout.addLayout(mode_row)

        name_row = QHBoxLayout()
        name_row.addWidget(BodyLabel(L("节点名称", "Node name"), self.widget))
        self.nameEdit = LineEdit(self.widget)
        self.nameEdit.setPlaceholderText(L("例如 server-hk-01", "For example, server-hk-01"))
        self.nameEdit.setText(socket.gethostname() or "server-node")
        self.nameEdit.textChanged.connect(self._update_output)
        name_row.addWidget(self.nameEdit, 1)
        self.viewLayout.addLayout(name_row)

        self._has_direct_hub = bool(has_hub)
        self.directHint = CaptionLabel(
            L(
                f"将向现有 Hub 追加节点：{hub_url}",
                f"A new node will be added to the existing Hub: {hub_url}")
            if has_hub else L(
                "未配置 Hub 时会自动生成 HTTPS 控制端和节点，服务器需放行 TCP 8787。",
                "Without a configured Hub, an HTTPS control Hub and node are generated automatically; open TCP 8787 on the server."),
            self.widget,
        )
        self.directHint.setWordWrap(True)
        self.viewLayout.addWidget(self.directHint)

        self.addressRow = QWidget(self.widget)
        address_row = QHBoxLayout(self.addressRow)
        address_row.setContentsMargins(0, 0, 0, 0)
        address_row.addWidget(BodyLabel(L("服务器地址", "Server address"), self.addressRow))
        self.addressEdit = LineEdit(self.addressRow)
        self.addressEdit.setPlaceholderText(L("公网 IP 或域名，可带端口", "Public IP or domain; port optional"))
        address_row.addWidget(self.addressEdit, 1)
        self.viewLayout.addWidget(self.addressRow)

        self.relayHint = CaptionLabel(L(
            "通过公共 MQTT 中继连接，服务器只需能够访问外网，无需开放 8787 控制端口。",
            "Connect through the public MQTT relay. The server only needs outbound internet access; port 8787 stays closed."),
            self.widget)
        self.relayHint.setWordWrap(True)
        self.viewLayout.addWidget(self.relayHint)
        self._update_connection_mode()

        output_row = QHBoxLayout()
        output_row.addWidget(BodyLabel(L("保存位置", "Save location"), self.widget))
        self.outputEdit = LineEdit(self.widget)
        self.outputEdit.textEdited.connect(lambda: setattr(self, "_custom_output", True))
        self._output_dir = self._default_output_dir()
        self._custom_output = False
        self._update_output()
        output_row.addWidget(self.outputEdit, 1)
        browse_button = PushButton(L("浏览…", "Browse…"), self.widget)
        browse_button.clicked.connect(self._pick_output)
        output_row.addWidget(browse_button)
        self.viewLayout.addLayout(output_row)

    @staticmethod
    def _default_output_dir():
        desktop = Path.home() / "Desktop"
        return desktop if desktop.is_dir() else Path.home()

    @staticmethod
    def _safe_name(value):
        clean = re.sub(r'[\\/:*?"<>|\s]+', "-", value.strip()).strip("-.")
        return clean[:48] or "server-node"

    def _update_output(self):
        if not self._custom_output:
            path = self._output_dir / f"FlowBench-{self._safe_name(self.nameEdit.text())}.zip"
            self.outputEdit.setText(str(path))

    def _pick_output(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            L("选择保存位置", "Choose Save Location"),
            self.outputEdit.text().strip(),
            L("ZIP 安装包 (*.zip)", "ZIP Bundle (*.zip)"),
        )
        if not path:
            return
        if not path.lower().endswith(".zip"):
            path += ".zip"
        self._custom_output = True
        self.outputEdit.setText(path)

    def node_name(self):
        return self.nameEdit.text().strip()

    def server_address(self):
        return self.addressEdit.text().strip()

    def connection_mode(self):
        return "relay" if self.connectionCombo.currentIndex() == 1 else "direct"

    def _update_connection_mode(self, *_):
        use_relay = self.connectionCombo.currentIndex() == 1
        self.directHint.setVisible(not use_relay)
        self.addressRow.setVisible(not use_relay and not self._has_direct_hub)
        self.relayHint.setVisible(use_relay)
        self.widget.updateGeometry()

    def output_path(self):
        return self.outputEdit.text().strip()


class AgentView(ScrollArea):
    _result = Signal(str, object, object)
    remote_event = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("agentView")
        self.view = QWidget(self)
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.enableTransparentBackground()
        self._agents = []
        # The combo remains the legacy single-agent path until the selection
        # dialog is applied; afterwards this set is the source for batch
        # start/stop operations, including an intentional empty selection.
        self._selected_agent_ids = set()
        self._multi_selection_active = False
        self._updating_combo = False
        self._ca_file = str(settings.agent_hub_ca or "")
        self._result.connect(self._on_result)
        self._poll_in_flight = False
        self._last_poll_error = ""
        self._last_remote_key = ""
        self._completion_logged = {}
        self._relay_client = None
        self._relay_client_key = None
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(2000)
        self._poll_timer.timeout.connect(self._poll_remote_status)
        self._poll_timer.start()

        root = QVBoxLayout(self.view)
        root.setContentsMargins(28, 22, 28, 28)
        root.setSpacing(14)
        root.addWidget(SubtitleLabel(L("服务器节点", "Server Agents"), self.view))
        root.addWidget(CaptionLabel(
            L("服务器节点使用压力测试页当前配置；生成节点时可选择自建 Hub 或公共 MQTT 中继。",
              "Server nodes use the current Stress Test page configuration; choose a self-hosted Hub or the public MQTT relay when generating a node."),
            self.view))

        control = SimpleCardWidget(self.view)
        form = QVBoxLayout(control)
        form.setContentsMargins(20, 16, 20, 16)
        form.setSpacing(10)

        # 标题行：左侧标题 + 右侧操作按钮（与“复制日志/清空”同一排布逻辑），
        # 按钮统一 32px 高并配图标，避免裸按钮悬在表单中间的廉价观感。
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_row.addWidget(StrongBodyLabel(L("节点控制", "Agent Control"), control))
        title_row.addStretch(1)
        self.refreshBtn = PushButton(FIF.SYNC, L("刷新节点", "Refresh agents"), control)
        self.refreshBtn.setFixedHeight(32)
        self.refreshBtn.setMinimumWidth(112)
        self.refreshBtn.setToolTip(L("从当前控制通道刷新在线服务器节点列表。",
                                     "Refresh online server agents through the current control channel."))
        self.refreshBtn.clicked.connect(self.refresh_agents)
        title_row.addWidget(self.refreshBtn)
        self.generateBtn = PushButton(FIF.ADD, L("生成服务器节点", "Generate server node"), control)
        self.generateBtn.setFixedHeight(32)
        self.generateBtn.setMinimumWidth(148)
        self.generateBtn.setToolTip(L(
            "可选择现有 Hub、一体化 Hub 或公共 MQTT 中继。",
            "Choose an existing Hub, an all-in-one Hub, or the public MQTT relay."))
        self.generateBtn.clicked.connect(self.generate_node)
        title_row.addWidget(self.generateBtn)
        form.addLayout(title_row)

        connection = QVBoxLayout()
        connection.setSpacing(8)
        connection_fields = QHBoxLayout()
        connection_fields.addWidget(BodyLabel(L("控制地址", "Control URL"), control))
        self.hubEdit = LineEdit(control)
        self.hubEdit.setPlaceholderText("https://control.example.com  /  mqtt://broker…")
        connection_fields.addWidget(self.hubEdit, 1)
        connection_fields.addWidget(BodyLabel(L("控制令牌", "Controller token"), control))
        self.tokenEdit = PasswordLineEdit(control)
        connection_fields.addWidget(self.tokenEdit, 1)
        connection.addLayout(connection_fields)
        form.addLayout(connection)

        selection = QHBoxLayout()
        selection.addWidget(BodyLabel(L("服务器节点", "Server agent"), control))
        self.agentCombo = ComboBox(control)
        self.agentCombo.setPlaceholderText(L("先刷新在线节点", "Refresh to load agents"))
        self.agentCombo.currentIndexChanged.connect(self._on_combo_selection_changed)
        selection.addWidget(self.agentCombo, 1)
        self.selectAgentsBtn = PushButton(L("多选服务器…", "Select servers…"), control)
        self.selectAgentsBtn.setToolTip(L(
            "打开面板搜索并选择一个或多个服务器执行同一个任务。",
            "Open a panel to search and select one or more servers for the same task."))
        self.selectAgentsBtn.clicked.connect(self.select_agents)
        selection.addWidget(self.selectAgentsBtn)
        self.nodeStatus = CaptionLabel(L("未连接", "Not connected"), control)
        selection.addWidget(self.nodeStatus)
        form.addLayout(selection)

        # Node status card, migrated from the Collaborative page.
        status_card = SimpleCardWidget(self.view)
        status_layout = QVBoxLayout(status_card)
        status_layout.setContentsMargins(20, 16, 20, 14)
        status_layout.setSpacing(8)
        status_layout.addWidget(StrongBodyLabel(L("节点状态", "Node Status"), status_card))
        self.mTotal = MiniStat("#0078D4", status_card)
        self.mSuccess = MiniStat("#107C10", status_card)
        self.mQps = MiniStat("#8764B8", status_card)
        self.mTx = MiniStat("#00B7C3", status_card)
        status_grid = QGridLayout()
        self._status_grid = status_grid
        self._status_widgets = [
            self._mini(L("累计请求", "Total Requests"), self.mTotal, status_card),
            self._mini(L("累计成功", "Total Success"), self.mSuccess, status_card),
            self._mini(L("实时 QPS", "Live QPS"), self.mQps, status_card),
            self._mini(L("总发送流量", "Total Sent Traffic"), self.mTx, status_card),
        ]
        for index, widget in enumerate(self._status_widgets):
            status_grid.addWidget(widget, index // 2, index % 2)
        status_layout.addLayout(status_grid)
        self.nodeListLabel = CaptionLabel(L("（暂无节点连接）", "(no nodes connected)"), status_card)
        self.nodeListLabel.setWordWrap(True)
        status_layout.addWidget(self.nodeListLabel)
        status_layout.addStretch(1)
        buttons = QHBoxLayout()
        self.runBtn = PrimaryPushButton(L("在服务器启动", "Start on server"), control)
        self.runBtn.clicked.connect(self.run_job)
        self.stopBtn = PushButton(L("停止服务器任务", "Stop server job"), control)
        self.stopBtn.clicked.connect(self.stop_job)
        buttons.addWidget(self.runBtn)
        buttons.addWidget(self.stopBtn)
        buttons.addStretch(1)
        form.addLayout(buttons)
        form.addStretch(1)

        # 左右双栏布局（与协同测试页一致）：左侧节点控制，右侧节点状态；
        # 窗口较窄时自动堆叠为上下两栏。
        self._control_card = control
        self._status_card = status_card
        self._columns_layout = QHBoxLayout()
        self._columns_layout.setSpacing(14)
        self._columns_layout.addWidget(control, 1)
        self._columns_layout.addWidget(status_card, 1)
        root.addLayout(self._columns_layout)

        # 日志区域与协同测试页保持一致：纯文本标签 + 复制/清空按钮，
        # 不再使用大边框的 TextEdit，避免深色模式下风格不统一。
        self._log_lines = []
        log_card = SimpleCardWidget(self.view)
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(20, 16, 20, 14)
        # 用一个横向可扩展的标题容器承载操作区。直接把子布局塞进
        # QVBoxLayout 时，侧栏宽度变化后 Qt 偶尔会保留旧的 sizeHint，
        # 导致按钮停在页面中部；显式的 Expanding 容器会始终吃满卡片宽度。
        log_header = QWidget(log_card)
        log_header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        log_head = QHBoxLayout(log_header)
        log_head.setContentsMargins(0, 0, 0, 0)
        log_head.setSpacing(8)
        log_head.addWidget(StrongBodyLabel(L("节点状态和操作结果", "Agent status and operation results"), log_header))
        log_head.addStretch(1)
        self.copyLogBtn = PushButton(L("复制日志", "Copy Log"), log_header)
        self.copyLogBtn.clicked.connect(self._copy_log)
        self.clearLogBtn = PushButton(L("清空", "Clear"), log_header)
        self.clearLogBtn.clicked.connect(self._clear_log)
        log_head.addWidget(self.copyLogBtn)
        log_head.addWidget(self.clearLogBtn)
        log_layout.addWidget(log_header)
        self.logLabel = CaptionLabel(L("（暂无）", "(empty)"), log_card)
        self.logLabel.setWordWrap(True)
        log_layout.addWidget(self.logLabel)
        log_layout.addStretch(1)
        self.hubEdit.setText(str(settings.agent_hub_url or ""))
        self.tokenEdit.setText(str(settings.agent_hub_token or ""))
        # 日志卡片占满剩余空间，让日志区域延伸到底部而不是悬空一大段
        root.addWidget(log_card, 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        narrow = self.width() < 960
        direction = (QBoxLayout.Direction.TopToBottom if narrow
                     else QBoxLayout.Direction.LeftToRight)
        if self._columns_layout.direction() != direction:
            self._columns_layout.setDirection(direction)
        self._control_card.setMinimumWidth(0 if narrow else 440)
        self._status_card.setMinimumWidth(0 if narrow else 440)
        self._set_status_grid_columns(4 if narrow else 2)

    def _set_status_grid_columns(self, columns):
        grid = getattr(self, "_status_grid", None)
        widgets = getattr(self, "_status_widgets", None)
        if grid is None or widgets is None:
            return
        for widget in widgets:
            grid.removeWidget(widget)
        for index, widget in enumerate(widgets):
            grid.addWidget(widget, index // columns, index % columns)

    def _mini(self, title, value_label, parent):
        w = QWidget(parent)
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        v.addWidget(CaptionLabel(title, w))
        v.addWidget(value_label)
        return w

    def _client(self):
        url = self.hubEdit.text().strip()
        token = self.tokenEdit.text().strip()
        if not url or not token:
            raise ValueError(L("请填写控制地址和控制令牌", "Enter the control URL and controller token"))
        if RelayControllerClient.supports(url):
            key = (url, token)
            if self._relay_client is None or self._relay_client_key != key:
                if self._relay_client is not None:
                    self._relay_client.close()
                self._relay_client = RelayControllerClient(url, token)
                self._relay_client_key = key
            return self._relay_client
        if self._relay_client is not None:
            self._relay_client.close()
            self._relay_client = None
            self._relay_client_key = None
        return ControllerClient(url, token, ca_file=self._ca_file)

    def _async(self, action, fn):
        def run():
            try:
                self._result.emit(action, fn(), None)
            except Exception as exc:
                self._result.emit(action, None, exc)
        threading.Thread(target=run, daemon=True).start()

    @staticmethod
    def _remote_status_key(event):
        if not isinstance(event, dict):
            return ""
        return "|".join((
            str(event.get("state", "")),
            str(event.get("job_id", "")),
            str(event.get("total", "")),
            str(event.get("tx", "")),
        ))

    def _poll_remote_status(self):
        """Keep the selected job visible on the Stress Test page."""
        if self._poll_in_flight or not self._selected_agents_ids():
            return
        try:
            client = self._client()
        except Exception:
            return

        self._poll_in_flight = True

        def query():
            try:
                return client.list_agents()
            finally:
                self._poll_in_flight = False

        self._async("poll_status", query)

    def refresh_agents(self):
        try:
            client = self._client()
        except Exception as exc:
            self._show_error(str(exc))
            return
        self.refreshBtn.setEnabled(False)
        self._async("list", client.list_agents)

    def generate_node(self):
        """Show one Fluent dialog and generate either an appended or all-in-one node."""
        url = self.hubEdit.text().strip()
        token = self.tokenEdit.text().strip()
        use_existing_relay = bool(url and token and RelayControllerClient.supports(url))
        scheme = urlsplit(url).scheme.lower() if url else ""
        has_hub = bool(url and token and scheme in {"http", "https"})
        dialog = GenerateNodeDialog(
            self.window(), has_hub=has_hub, hub_url=url, use_relay=use_existing_relay
        )
        if not dialog.exec():
            return

        name = dialog.node_name()
        if not name:
            self._show_error(L("请填写节点名称", "Enter a node name"))
            return

        output = dialog.output_path()
        if not output:
            self._show_error(L("请填写保存位置", "Enter a save location"))
            return
        if not output.lower().endswith(".zip"):
            output += ".zip"
        try:
            output_path = Path(output).expanduser().resolve()
            output_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            self._show_error(str(exc))
            return

        self.generateBtn.setEnabled(False)
        if dialog.connection_mode() == "relay":
            relay_url = url if use_existing_relay else (
                f"{DEFAULT_AGENT_RELAY}/{secrets.token_urlsafe(24)}"
            )
            controller_token = token if use_existing_relay else secrets.token_urlsafe(32)
            self._async("generate_relay", lambda: self._generate_relay_package(
                name, relay_url, controller_token, str(output_path)))
            return
        if has_hub:
            try:
                client = ControllerClient(url, token, ca_file=self._ca_file)
            except Exception as exc:
                self.generateBtn.setEnabled(True)
                self._show_error(str(exc))
                return
            self._async("generate", lambda: self._provision_and_package(client, name, str(output_path)))
            return

        public_address = dialog.server_address()
        try:
            host, port = self._normalize_public_hub(public_address)
        except Exception as exc:
            self.generateBtn.setEnabled(True)
            self._show_error(str(exc))
            return
        self._async("generate_offline", lambda: self._generate_all_in_one_package(
            name, host, port, str(output_path)))

    @staticmethod
    def _normalize_public_hub(value):
        raw = str(value or "").strip()
        if not raw:
            raise ValueError(L("请填写服务器公网 IP 或域名", "Enter the server public IP or domain"))
        if "://" not in raw:
            raw = f"https://{raw}"
        parsed = urlsplit(raw)
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            raise ValueError(L("服务器地址必须是 IP 或域名", "Server address must be an IP or domain"))
        host = parsed.hostname.rstrip(".")
        port = parsed.port or 8787
        if not 1 <= port <= 65535:
            raise ValueError(L("端口必须在 1–65535", "Port must be 1-65535"))
        return host, port

    @staticmethod
    def _bracket_host(host):
        return f"[{host}]" if ":" in host else host

    @staticmethod
    def _generate_certificate(directory, host):
        import datetime

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
        names = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
        try:
            names.append(x509.IPAddress(ipaddress.ip_address(host)))
        except ValueError:
            names.append(x509.DNSName(host))
        now = datetime.datetime.now(datetime.timezone.utc)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=825))
            .add_extension(x509.SubjectAlternativeName(names), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .sign(key, hashes.SHA256())
        )
        cert_path = Path(directory) / "cert.pem"
        key_path = Path(directory) / "key.pem"
        cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))
        return cert_path, key_path

    def _generate_all_in_one_package(self, name, public_host, public_port, output):
        """Create a Hub plus node package without requiring an already-running Hub."""
        output = Path(output).resolve()
        agent_exe, linux_root = self._node_payload_paths()
        hub_exe = self._hub_exe_path()
        hub_source = linux_root / "agent" / "hub.py"
        transport_source = linux_root / "agent" / "transport.py"
        relay_source = linux_root / "agent" / "relay.py"
        missing = [path for path in (agent_exe, hub_exe, hub_source, transport_source, relay_source,
                                     linux_root / "server_agent.py") if not path.is_file()]
        if missing:
            raise FileNotFoundError(L("安装包缺少服务端文件，请重新构建主程序。",
                                      "The installed app is missing server payload files; rebuild the main app."))

        agent_id = "server-" + secrets.token_hex(6)
        agent_token = secrets.token_urlsafe(32)
        controller_token = secrets.token_urlsafe(32)
        public_url = f"https://{self._bracket_host(public_host)}:{public_port}"
        local_hub_url = f"https://127.0.0.1:{public_port}"
        agent_config = {
            "hub_url": local_hub_url,
            "agent_id": agent_id,
            "token": agent_token,
            "ca_file": "ca.pem",
            "allowed_targets": sorted({str(item.get("host")) for item in settings.authorized if item.get("host")}),
            "allowed_protocols": ["HTTP", "HTTPS", "TCP"],
            "max_rate": 100,
            "max_duration": 300,
            "max_threads": 32,
            "max_packet_size": 65536,
            "poll_interval": 2,
            "heartbeat_interval": 10,
            "job_heartbeat_interval": 2,
        }
        agent_config["insecure_control_tls"] = True
        hub_config = {
            "controller_token": controller_token,
            "agents": [{"agent_id": agent_id, "name": name, "token": agent_token}],
        }
        config_json = json.dumps(agent_config, ensure_ascii=False, indent=2)
        hub_json = json.dumps(hub_config, ensure_ascii=False, indent=2)

        with tempfile.TemporaryDirectory(prefix="flowbench-cert-") as temp:
            cert_path, key_path = self._generate_certificate(temp, public_host)
            cert_pem = cert_path.read_bytes()
            key_pem = key_path.read_bytes()

            windows_hub = """@echo off
cd /d "%~dp0"
FlowBench-Hub.exe --config hub.json --host 0.0.0.0 --port __FLOWBENCH_PORT__ --certfile cert.pem --keyfile key.pem
""".replace("__FLOWBENCH_PORT__", str(public_port)).replace("\n", "\r\n")
            windows_agent = """@echo off
cd /d "%~dp0"
FlowBench-Agent.exe --config agent.json
""".replace("\n", "\r\n")
            windows_all = r"""@echo off
cd /d "%~dp0"
start "FlowBench Hub" cmd /c hub\start-hub.cmd
timeout /t 2 /nobreak >nul
cd agent
FlowBench-Agent.exe --config agent.json
""".replace("\n", "\r\n")
            linux_hub = """#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
exec python3 hub.py --config hub.json --host 0.0.0.0 --port __FLOWBENCH_PORT__ --certfile cert.pem --keyfile key.pem
""".replace("__FLOWBENCH_PORT__", str(public_port))
            linux_agent = """#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
exec python3 server_agent.py --config agent.json
"""
            linux_all = """#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
if [ "${SKIP_PIP:-0}" != "1" ]; then
    python3 -m pip install --user -r agent/requirements.txt
fi
export PYTHONUNBUFFERED=1
python3 hub/hub.py --config hub/hub.json --host 0.0.0.0 --port __FLOWBENCH_PORT__ --certfile hub/cert.pem --keyfile hub/key.pem --wait-ready 1 &
hub_pid=$!
trap 'kill "$hub_pid" 2>/dev/null || true' EXIT INT TERM
for i in $(seq 1 30); do
    if curl -ksSf "https://127.0.0.1:__FLOWBENCH_PORT__/health" >/dev/null 2>&1; then
        break
    fi
    if ! kill -0 "$hub_pid" 2>/dev/null; then
        echo "FlowBench Hub exited during startup" >&2
        exit 1
    fi
    sleep 0.5
done
if ! curl -ksSf "https://127.0.0.1:__FLOWBENCH_PORT__/health" >/dev/null 2>&1; then
    echo "FlowBench Hub did not become ready" >&2
    kill "$hub_pid" 2>/dev/null || true
    exit 1
fi
echo "FlowBench Hub ready"
exec python3 agent/server_agent.py --config agent/agent.json
""".replace("__FLOWBENCH_PORT__", str(public_port))

            def executable_info(content):
                info = zipfile.ZipInfo(content[0])
                info.create_system = 3
                info.external_attr = 0o755 << 16
                return info, content[1]

            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as bundle:
                # Windows all-in-one deployment
                bundle.write(hub_exe, "windows/hub/FlowBench-Hub.exe")
                bundle.writestr("windows/hub/hub.json", hub_json)
                bundle.writestr("windows/hub/cert.pem", cert_pem)
                bundle.writestr("windows/hub/key.pem", key_pem)
                bundle.writestr("windows/hub/start-hub.cmd", windows_hub)
                bundle.write(agent_exe, "windows/agent/FlowBench-Agent.exe")
                bundle.writestr("windows/agent/agent.json", config_json)
                bundle.writestr("windows/agent/ca.pem", cert_pem)
                bundle.writestr("windows/agent/start-agent.cmd", windows_agent)
                bundle.writestr("windows/start-all.cmd", windows_all)

                # Linux all-in-one deployment
                bundle.write(hub_source, "linux/hub/hub.py")
                bundle.write(transport_source, "linux/hub/transport.py")
                bundle.writestr("linux/hub/hub.json", hub_json)
                bundle.writestr("linux/hub/cert.pem", cert_pem)
                bundle.writestr("linux/hub/key.pem", key_pem)
                bundle.write(linux_root / "server_agent.py", "linux/agent/server_agent.py")
                bundle.write(linux_root / "agent" / "worker.py", "linux/agent/agent/worker.py")
                bundle.write(transport_source, "linux/agent/agent/transport.py")
                bundle.write(relay_source, "linux/agent/agent/relay.py")
                bundle.write(linux_root / "agent" / "__init__.py", "linux/agent/agent/__init__.py")
                bundle.writestr("linux/agent/agent.json", config_json)
                bundle.writestr("linux/agent/ca.pem", cert_pem)
                bundle.writestr(
                    "linux/agent/requirements.txt",
                    "requests>=2.31\npaho-mqtt>=2.0\n",
                )
                for script in (("linux/hub/start-hub.sh", linux_hub),
                               ("linux/agent/start-agent.sh", linux_agent),
                               ("linux/start-all.sh", linux_all)):
                    bundle.writestr(*executable_info(script))

                instructions = L(
                    f"FlowBench 一体化服务器节点\n\n"
                    f"控制地址：{public_url}\n\n"
                    "Windows：解压后进入 windows，右键以管理员运行 start-all.cmd，并允许防火墙端口 8787。\n"
                    "Linux 脚本会等待 Hub 健康检查通过后再启动 Agent。\n"
                    "Linux：解压后进入 linux，执行 chmod +x start-all.sh && ./start-all.sh，并放行 TCP 8787。\n"
                    "启动完成后，在本地 GUI 服务器节点页点击刷新节点。\n"
                    "本地会同时生成 .ca.pem 证书文件；不要删除它，GUI 需要它连接控制端。\n"
                    "这个包使用随机自签名证书，仅用于你自己的服务器控制面。\n"
                    "只对你拥有或取得书面授权的目标执行测试。",
                    f"FlowBench all-in-one server node\n\n"
                    f"Control URL: {public_url}\n\n"
                    "Windows: extract, open windows, run start-all.cmd as administrator, and allow TCP 8787.\n"
                    "Linux: extract, open linux, run chmod +x start-all.sh && ./start-all.sh, and open TCP 8787. The script waits for the Hub health check before starting the Agent.\n"
                    "After it starts, click Refresh agents in the local GUI.\n"
                    "A .ca.pem certificate is generated beside the ZIP; keep it because the GUI needs it to connect.\n"
                    "This package uses a random self-signed certificate for your own control plane only.\n"
                    "Use only targets you own or are authorized to test.")
                bundle.writestr("README.txt", instructions)

        ca_path = output.with_suffix(".ca.pem")
        ca_path.write_bytes(cert_pem)
        return {
            "path": str(output),
            "agent_id": agent_id,
            "name": name,
            "hub_url": public_url,
            "controller_token": controller_token,
            "ca_file": str(ca_path),
        }

    def _generate_relay_package(self, name, relay_url, controller_token, output):
        """Create a standalone node package that uses the public MQTT relay."""
        output = Path(output).resolve()
        exe, linux_root = self._node_payload_paths()
        source_files = [
            linux_root / "server_agent.py",
            linux_root / "agent" / "worker.py",
            linux_root / "agent" / "transport.py",
            linux_root / "agent" / "relay.py",
            linux_root / "agent" / "__init__.py",
        ]
        missing = ([exe] if not exe.is_file() else []) + [
            path for path in source_files if not path.is_file()
        ]
        if missing:
            raise FileNotFoundError(L(
                "安装包缺少 Agent 文件，请重新构建主程序。",
                "The installed app is missing agent payload files; rebuild the main app.",
            ))

        agent_id = "server-" + secrets.token_hex(6)
        allowed_targets = sorted({
            str(item.get("host")) for item in settings.authorized if item.get("host")
        })
        agent_config = {
            "transport": "mqtt_relay",
            "relay_url": relay_url,
            "controller_token": controller_token,
            "agent_id": agent_id,
            "name": name,
            "allowed_targets": allowed_targets,
            "allowed_protocols": ["HTTP", "HTTPS", "TCP"],
            "max_rate": 100,
            "max_duration": 300,
            "max_threads": 32,
            "max_packet_size": 65536,
            "heartbeat_interval": 10,
            "job_heartbeat_interval": 2,
        }
        config_json = json.dumps(agent_config, ensure_ascii=False, indent=2)
        start_cmd = '@echo off\ncd /d "%~dp0"\nFlowBench-Agent.exe --config agent.json\n'
        start_sh = '#!/usr/bin/env bash\nset -e\ncd "$(dirname "$0")"\nexec python3 server_agent.py --config agent.json\n'
        instructions = L(
            "FlowBench 中继服务器节点\n\n"
            "此节点通过公共 MQTT 中继连接，不需要开放 8787 控制端口。\n"
            "Windows：进入 windows 文件夹，双击 start-agent.cmd。\n"
            "Linux：进入 linux 文件夹，安装 requirements.txt 后运行 ./start-agent.sh。\n"
            "节点和本地 FlowBench 都必须能访问 broker.hivemq.com。\n"
            "只对你拥有或取得书面授权的目标执行测试。",
            "FlowBench relay server node\n\n"
            "This node connects through the public MQTT relay; port 8787 stays closed.\n"
            "Windows: open the windows folder and run start-agent.cmd.\n"
            "Linux: open the linux folder, install requirements.txt, then run ./start-agent.sh.\n"
            "Both the node and local FlowBench must be able to reach broker.hivemq.com.\n"
            "Use only targets you own or are authorized to test.",
        )
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.write(exe, "windows/FlowBench-Agent.exe")
            bundle.writestr("windows/agent.json", config_json)
            bundle.writestr("windows/start-agent.cmd", start_cmd)
            archive_names = [
                "linux/server_agent.py",
                "linux/agent/worker.py",
                "linux/agent/transport.py",
                "linux/agent/relay.py",
                "linux/agent/__init__.py",
            ]
            for path, archive_name in zip(source_files, archive_names):
                bundle.write(path, archive_name)
            bundle.writestr("linux/agent.json", config_json)
            bundle.writestr("linux/requirements.txt", "requests>=2.31\npaho-mqtt>=2.0\n")
            info = zipfile.ZipInfo("linux/start-agent.sh")
            info.create_system = 3
            info.external_attr = 0o755 << 16
            bundle.writestr(info, start_sh, compress_type=zipfile.ZIP_DEFLATED)
            bundle.writestr("README.txt", instructions)
        return {
            "path": str(output),
            "agent_id": agent_id,
            "name": name,
            "hub_url": relay_url,
            "controller_token": controller_token,
            "ca_file": "",
        }

    def _provision_and_package(self, client, name, output):
        record = client.provision_agent(name)
        exe, linux_root = self._node_payload_paths()
        source_files = [
            linux_root / "server_agent.py",
            linux_root / "agent" / "worker.py",
            linux_root / "agent" / "transport.py",
            linux_root / "agent" / "relay.py",
            linux_root / "agent" / "__init__.py",
        ]
        missing = ([exe] if not exe.is_file() else []) + [path for path in source_files if not path.is_file()]
        if missing:
            raise FileNotFoundError(L("安装包缺少 Agent 文件，请重新构建主程序。",
                                      "The installed app is missing agent payload files; rebuild the main app."))
        allowed_targets = sorted({str(item.get("host")) for item in settings.authorized if item.get("host")})
        agent_config = {
            "hub_url": self.hubEdit.text().strip().rstrip("/"),
            "agent_id": record["agent_id"],
            "token": record["token"],
            "allowed_targets": allowed_targets,
            "allowed_protocols": ["HTTP", "HTTPS", "TCP"],
            "max_rate": 100,
            "max_duration": 300,
            "max_threads": 32,
            "max_packet_size": 65536,
            "poll_interval": 2,
            "heartbeat_interval": 10,
            "job_heartbeat_interval": 2,
        }
        instructions = L(
            "FlowBench 服务器节点\n\n"
            "1. Windows：进入 windows 文件夹，把 agent.json 与 FlowBench-Agent.exe 放在同一目录，双击 start-agent.cmd。\n"
            "2. Linux：进入 linux 文件夹，安装 requests 后运行 ./start-agent.sh。\n"
            "3. 节点上线后，在本地压力测试页填写配置，再回到服务器节点页点击启动。\n"
            "4. 只对你拥有或取得书面授权的目标执行测试。",
            "FlowBench server node\n\n"
            "1. Windows: open the windows folder, keep agent.json beside FlowBench-Agent.exe, then run start-agent.cmd.\n"
            "2. Linux: open the linux folder, install requests, then run ./start-agent.sh.\n"
            "3. After the node is online, configure the Stress Test page locally and click Start on server.\n"
            "4. Use only targets you own or are authorized to test.")
        start_cmd = '@echo off\ncd /d "%~dp0"\nFlowBench-Agent.exe --config agent.json\n'
        start_sh = '#!/usr/bin/env bash\nset -e\ncd "$(dirname "$0")"\nexec python3 server_agent.py --config agent.json\n'
        config_json = json.dumps(agent_config, ensure_ascii=False, indent=2)
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as bundle:
            if exe.is_file():
                bundle.write(exe, "windows/FlowBench-Agent.exe")
                bundle.writestr("windows/agent.json", config_json)
                bundle.writestr("windows/start-agent.cmd", start_cmd)
            linux_payloads = [
                (source_files[0], "linux/server_agent.py"),
                (source_files[1], "linux/agent/worker.py"),
                (source_files[2], "linux/agent/transport.py"),
                (source_files[3], "linux/agent/relay.py"),
                (source_files[4], "linux/agent/__init__.py"),
            ]
            for path, archive_name in linux_payloads:
                if path.is_file():
                    bundle.write(path, archive_name)
            bundle.writestr("linux/agent.json", config_json)
            bundle.writestr("linux/requirements.txt", "requests>=2.31\npaho-mqtt>=2.0\n")
            # Preserve the executable bit for Linux ZIP archives.
            info = zipfile.ZipInfo("linux/start-agent.sh")
            info.create_system = 3
            info.external_attr = 0o755 << 16
            bundle.writestr(info, start_sh, compress_type=zipfile.ZIP_DEFLATED)
            bundle.writestr("README.txt", instructions)
        return {"path": output, "agent_id": record["agent_id"], "name": name}

    @classmethod
    def _hub_exe_path(cls):
        resource = cls._resource_root()
        project = Path(__file__).resolve().parents[2]
        candidates = [
            resource / "agent_template" / "hub" / "FlowBench-Hub.exe",
            project / "dist" / "FlowBench-Hub.exe",
            project / "FlowBench-Hub.exe",
        ]
        return next((path for path in candidates if path.is_file()), candidates[0])

    @staticmethod
    def _resource_root():
        if getattr(sys, "frozen", False):
            return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        return Path(__file__).resolve().parents[2]

    @classmethod
    def _node_payload_paths(cls):
        """Locate the Windows binary and Linux source bundled with the app."""
        resource = cls._resource_root()
        project = Path(__file__).resolve().parents[2]
        exe_candidates = [
            resource / "agent_template" / "windows" / "FlowBench-Agent.exe",
            project / "dist" / "FlowBench-Agent.exe",
            project / "FlowBench-Agent.exe",
        ]
        linux_candidates = [
            resource / "agent_template" / "linux",
            project,
        ]
        exe = next((path for path in exe_candidates if path.is_file()), exe_candidates[0])
        linux_root = next((path for path in linux_candidates if (path / "server_agent.py").is_file()), linux_candidates[0])
        return exe, linux_root

    def _selected_agent(self):
        ids = self._selected_agents_ids()
        return ids[0] if ids else ""

    def _selected_agents_ids(self):
        """Return the current operation selection in stable display order."""
        available = [str(agent.get("agent_id") or "") for agent in self._agents]
        if self._multi_selection_active:
            selected = self._selected_agent_ids
            return [agent_id for agent_id in available if agent_id in selected]
        idx = self.agentCombo.currentIndex()
        if 0 <= idx < len(self._agents):
            agent_id = str(self._agents[idx].get("agent_id") or "")
            return [agent_id] if agent_id else []
        return []

    def _on_combo_selection_changed(self, *_):
        # Choosing an item in the legacy combo intentionally switches back to
        # single-agent mode, so the old workflow remains predictable.
        if getattr(self, "_updating_combo", False):
            return
        if self._multi_selection_active:
            self._selected_agent_ids.clear()
            self._multi_selection_active = False
            self._update_selection_summary()

    def _update_selection_summary(self):
        if self._multi_selection_active:
            count = len(self._selected_agents_ids())
            self.selectAgentsBtn.setText(L(f"已选 {count} 个服务器…", f"{count} servers selected…"))
        else:
            self.selectAgentsBtn.setText(L("多选服务器…", "Select servers…"))

    def select_agents(self):
        if not self._agents:
            self._show_error(L("请先刷新服务器节点列表", "Refresh the server list first"))
            return
        selected = (self._selected_agent_ids if self._multi_selection_active
                    else set(self._selected_agents_ids()))
        dialog = AgentSelectionDialog(self._agents, selected, self.window())
        if not dialog.exec():
            return
        self._selected_agent_ids = set(dialog.selected_ids())
        self._multi_selection_active = True
        self._update_selection_summary()
        count = len(self._selected_agent_ids)
        if count:
            self.nodeStatus.setText(L(f"已选择 {count} 个节点", f"{count} agent(s) selected"))
            self._log(L(f"已选择 {count} 个服务器节点。", f"Selected {count} server agent(s)."))
        else:
            self.nodeStatus.setText(L(f"已加载 {len(self._agents)} 个节点",
                                      f"Loaded {len(self._agents)} agent(s)"))
            self._log(L("已清空服务器节点选择。", "Cleared server-agent selection."))

    def _stress_config(self):
        """Build the same single-target config used by the existing Collab page."""
        stress = self.window().stress
        targets = stress._parse_targets()
        if targets is None:
            raise ValueError(L("请先修正压力测试页的目标地址", "Fix the target address on the Stress Test page first"))
        if not targets:
            raise ValueError(L("请先在压力测试页填写目标", "Set the target on the Stress Test page first"))

        # A server agent runs one job at a time. Keep the existing Stress page
        # as the source of truth and use its first target, like Collab Start.
        target_raw, host = targets[0]
        if not is_authorized(host):
            dlg = AuthDialog(host, self.window())
            if not dlg.exec():
                raise ValueError(L(f"目标 {host} 未授权，服务器任务已阻止",
                                   f"Target {host} is not authorized; server job blocked"))
            if not add_authorized(host, dlg.note()):
                raise ValueError(L(f"无法保存目标 {host} 的授权记录：{settings.last_error}",
                                   f"Could not save authorization for {host}: {settings.last_error}"))
            stress.refresh_auth_list()

        protocol = stress.protoCombo.currentText().upper()
        rate = stress.rateSpin.value()
        if rate > HIGH_RATE:
            from qfluentwidgets import MessageBox
            confirm = MessageBox(
                L("高请求速率二次确认", "High Rate Confirmation"),
                L(f"服务器节点将使用压力测试页配置向 {host} 发送最高 {rate} QPS。\n"
                  "请再次确认目标已获授权且能够承受该速率。",
                  f"The server agent will use the Stress page configuration against {host}, up to {rate} QPS.\n"
                  "Confirm that the target is authorized and can handle this rate."),
                self.window())
            if not confirm.exec():
                raise ValueError(L("高速率未确认，服务器任务已取消",
                                   "High rate not confirmed; server job cancelled"))

        headers_raw = stress.headersEdit.toPlainText().strip()
        try:
            headers = json.loads(headers_raw) if headers_raw else {}
        except json.JSONDecodeError as exc:
            raise ValueError(L("请求头须为合法 JSON", "Headers must be valid JSON")) from exc
        if not isinstance(headers, dict):
            raise ValueError(L("请求头必须是 JSON 对象", "Headers must be a JSON object"))

        port = stress.portSpin.value()
        config = {
            "target": host,
            "port": port,
            "protocol": protocol,
            "url": "",
            "threads": stress.threadSpin.value(),
            "duration": stress.get_duration_seconds(),
            "rate": rate,
            "packet_size": settings.default_packet_size,
            "timeout": settings.default_timeout_ms,
            "headers": headers,
        }
        if protocol in ("HTTP", "HTTPS"):
            config["url"] = build_http_url(target_raw, host, port, protocol)
        return config

    def run_job(self):
        agent_ids = self._selected_agents_ids()
        if not agent_ids:
            self._show_error(L("请先刷新并选择服务器节点", "Refresh and select at least one server agent first"))
            return
        try:
            client = self._client()
            job = self._stress_config()
        except Exception as exc:
            self._show_error(str(exc))
            return
        command = {"type": "start", "job_id": uuid.uuid4().hex, "config": job}
        # Keep the node allow-list aligned with the authorized target currently
        # selected on the Stress page, then queue the start command on every
        # selected agent. Each send is isolated so one offline node does not
        # prevent the remaining servers from receiving the task.
        def dispatch():
            results = []
            for agent_id in agent_ids:
                try:
                    client.send(agent_id, {"type": "sync_targets", "targets": [job["target"]]})
                    client.send(agent_id, command)
                    results.append({"agent_id": agent_id, "ok": True})
                except Exception as exc:
                    results.append({"agent_id": agent_id, "ok": False, "error": str(exc)})
            return results
        self._async("run", dispatch)

    def stop_job(self):
        agent_ids = self._selected_agents_ids()
        if not agent_ids:
            self._show_error(L("请先选择服务器节点", "Select at least one server agent first"))
            return
        try:
            client = self._client()
        except Exception as exc:
            self._show_error(str(exc))
            return
        def dispatch():
            results = []
            for agent_id in agent_ids:
                try:
                    client.send(agent_id, {"type": "stop"})
                    results.append({"agent_id": agent_id, "ok": True})
                except Exception as exc:
                    results.append({"agent_id": agent_id, "ok": False, "error": str(exc)})
            return results
        self._async("stop", dispatch)

    def _on_result(self, action, value, error):
        self.refreshBtn.setEnabled(True)
        self.generateBtn.setEnabled(True)
        if error:
            if action == "poll_status":
                if str(error) != self._last_poll_error:
                    self._last_poll_error = str(error)
                    self._log(f"[poll] {error}")
                return
            self._show_error(str(error))
            return
        if action == "list":
            self._agents = list(value or [])
            self._updating_combo = True
            try:
                self.agentCombo.clear()
                for agent in self._agents:
                    state = L("在线", "online") if agent.get("online") else L("离线", "offline")
                    self.agentCombo.addItem(f"{agent.get('name', agent['agent_id'])} [{state}] — {agent['agent_id']}")
            finally:
                self._updating_combo = False
            # Remove agents that no longer exist while preserving a valid
            # multi-selection after a refresh.
            available_ids = {str(agent.get("agent_id") or "") for agent in self._agents}
            self._selected_agent_ids.intersection_update(available_ids)
            if self._multi_selection_active:
                self.nodeStatus.setText(L(f"已选择 {len(self._selected_agent_ids)} 个节点",
                                          f"{len(self._selected_agent_ids)} agent(s) selected"))
            else:
                self.nodeStatus.setText(L(f"已加载 {len(self._agents)} 个节点",
                                          f"Loaded {len(self._agents)} agent(s)"))
            self._update_selection_summary()
            self._log(L(f"已刷新节点列表，共 {len(self._agents)} 个节点。",
                                  f"Agent list refreshed: {len(self._agents)} agent(s)."))
            for agent in self._agents:
                event = agent.get("last_event") or {}
                if event:
                    state = event.get("state", "unknown")
                    self._log(f"{agent.get('agent_id')}: {state}")
            self._update_agent_status_widgets()
            self._emit_selected_remote_event(force=True)
        elif action == "poll_status":
            self._agents = list(value or [])
            available_ids = {str(agent.get("agent_id") or "") for agent in self._agents}
            self._selected_agent_ids.intersection_update(available_ids)
            if self._multi_selection_active:
                self.nodeStatus.setText(L(f"已选择 {len(self._selected_agent_ids)} 个节点",
                                          f"{len(self._selected_agent_ids)} agent(s) selected"))
                self._update_selection_summary()
            self._update_agent_status_widgets()
            self._emit_selected_remote_event()
            if self._last_poll_error:
                self._log(L("服务器节点状态恢复刷新。", "Server-agent status polling recovered."))
            self._last_poll_error = ""
        elif action in {"run", "stop"}:
            self._show_batch_result(action, value or [])
        else:
            if action in {"generate", "generate_offline", "generate_relay"}:
                self._log(L(f"节点安装包已生成：{value['path']}",
                                      f"Node bundle generated: {value['path']}"))
                if action in {"generate_offline", "generate_relay"}:
                    self.hubEdit.setText(value["hub_url"])
                    self.tokenEdit.setText(value["controller_token"])
                    self._ca_file = value["ca_file"]
                    settings.set("agent_hub_url", value["hub_url"])
                    settings.set("agent_hub_token", value["controller_token"])
                    settings.set("agent_hub_ca", value["ca_file"])
                    if action == "generate_relay":
                        self._log(L(
                            f"中继节点包已生成。服务器无需开放控制端口；中继地址：{value['hub_url']}",
                            f"Relay node bundle generated. No inbound control port is needed; relay: {value['hub_url']}"))
                    else:
                        self._log(L(
                            f"一体化包已生成。复制到服务器运行 start-all 后，本地控制地址：{value['hub_url']}",
                            f"All-in-one bundle generated. Run start-all on the server; local control URL: {value['hub_url']}"))
                InfoBar.success(L("节点已生成", "Node generated"),
                                L("解压后把对应目录复制到服务器即可。", "Extract it and copy the matching folder to the server."),
                                parent=self, position=InfoBarPosition.TOP)
            else:
                InfoBar.success(L("已发送", "Sent"), L("命令已发送到控制通道", "Command sent to the control channel"),
                                parent=self, position=InfoBarPosition.TOP)

    def _show_batch_result(self, action, results):
        """Log and display per-node outcomes for a batch operation."""
        results = list(results or [])
        succeeded = [item for item in results if item.get("ok")]
        failed = [item for item in results if not item.get("ok")]
        verb = L("启动", "start") if action == "run" else L("停止", "stop")
        for item in failed:
            self._log(L(
                f"节点 {item.get('agent_id', '-')} {verb}失败：{item.get('error', '未知错误')}",
                f"Agent {item.get('agent_id', '-')} failed to {verb}: {item.get('error', 'unknown error')}"))
        self._log(L(
            f"已向 {len(succeeded)} 个节点发送{verb}命令，失败 {len(failed)} 个。",
            f"Sent {verb} command to {len(succeeded)} agent(s); {len(failed)} failed."))
        if failed and succeeded:
            InfoBar.warning(L("批量操作部分完成", "Batch operation partially completed"),
                            L(f"成功 {len(succeeded)} 个，失败 {len(failed)} 个。",
                              f"{len(succeeded)} succeeded, {len(failed)} failed."),
                            parent=self, position=InfoBarPosition.TOP)
        elif failed:
            InfoBar.error(L("批量操作失败", "Batch operation failed"),
                          L(f"没有节点成功执行{verb}命令。", f"No agent accepted the {verb} command."),
                          parent=self, position=InfoBarPosition.TOP)
        else:
            InfoBar.success(L("批量操作已发送", "Batch operation sent"),
                            L(f"已向 {len(succeeded)} 个节点发送{verb}命令。",
                              f"Sent the {verb} command to {len(succeeded)} agent(s)."),
                            parent=self, position=InfoBarPosition.TOP)

    def _update_agent_status_widgets(self):
        total_req = total_ok = total_tx = 0
        total_qps = 0.0
        parts = []
        for agent in self._agents:
            stats = agent.get("last_event") or {}
            name = agent.get("name") or agent.get("agent_id", "-")
            if stats:
                total_req += stats.get("total", 0)
                total_ok += stats.get("success", 0)
                total_qps += stats.get("qps", 0.0)
                tx = stats.get("tx", stats.get("bytes_tx", 0))
                total_tx += tx
                state = stats.get("state", "running")
                state_text = {
                    "idle": L("空闲", "idle"), "ready": L("就绪", "ready"),
                    "starting": L("启动中", "starting"), "running": L("运行中", "running"),
                    "stopping": L("停止中", "stopping"), "completed": L("完成", "completed"),
                    "error": L("错误", "error"),
                }.get(state, state)
                parts.append(
                    f"{name} [{state_text}]: ✓{stats.get('success',0)} "
                    f"✗{stats.get('fail',0)} ({stats.get('qps',0):.0f} QPS) {fmt_bytes(tx)}")
                if state == "completed":
                    job_key = str(stats.get("job_id") or stats.get("ts") or "final")
                    if self._completion_logged.get(name) != job_key:
                        self._completion_logged[name] = job_key
                        self._log(L(
                            f"服务器节点 {name} 压测完成：总请求 {stats.get('total',0)}  "
                            f"成功 {stats.get('success',0)}  失败 {stats.get('fail',0)}  "
                            f"发送 {fmt_bytes(tx)}",
                            f"Server node {name} finished: total {stats.get('total',0)}  "
                            f"success {stats.get('success',0)}  failed {stats.get('fail',0)}  "
                            f"sent {fmt_bytes(tx)}"))
            else:
                parts.append(f"{name}: " + L("等待数据", "waiting for data"))
        self.mTotal.setText(str(total_req))
        self.mSuccess.setText(str(total_ok))
        self.mQps.setText(f"{total_qps:.1f}")
        self.mTx.setText(fmt_bytes(total_tx))
        self.nodeListLabel.setText("  |  ".join(parts) if parts else L("（暂无节点连接）", "(no nodes connected)"))

    def _emit_selected_remote_event(self, force=False):
        selected_id = self._selected_agent()
        if not selected_id:
            return
        agent = next((item for item in self._agents
                      if str(item.get("agent_id") or "") == selected_id), None)
        if agent is None:
            return
        event = dict(agent.get("last_event") or {})
        if not event:
            return
        key = self._remote_status_key(event)
        if force or key != self._last_remote_key:
            self._last_remote_key = key
            self.remote_event.emit(event)

    def _log(self, text):
        """追加一条带时间戳的日志，最多保留最近 200 条。"""
        self._log_lines.append(f"[{time.strftime('%H:%M:%S')}] {text}")
        self._log_lines = self._log_lines[-200:]
        self.logLabel.setText("\n".join(self._log_lines))

    def _copy_log(self):
        """复制当前可见的日志，方便排障和分享。"""
        if not self._log_lines:
            InfoBar.warning(L("暂无日志", "No log"),
                            L("当前没有可复制的日志", "There is no log to copy"),
                            parent=self.window())
            return
        QGuiApplication.clipboard().setText("\n".join(self._log_lines))
        InfoBar.success(L("已复制", "Copied"),
                        L(f"已复制 {len(self._log_lines)} 条日志",
                          f"Copied {len(self._log_lines)} log entries"),
                        parent=self.window())

    def _clear_log(self):
        """只清空页面中的临时日志，不影响审计日志文件。"""
        self._log_lines.clear()
        self.logLabel.setText(L("（暂无）", "(empty)"))

    def _show_error(self, text):
        self._log(f"[error] {text}")
        InfoBar.error(L("服务器节点操作失败", "Server-agent operation failed"), text,
                      parent=self, position=InfoBarPosition.TOP)
