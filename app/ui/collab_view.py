"""协同测试页：主控邀请 / 节点加入（直连 + 中继模式）。"""
import json
import time
import threading

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QBoxLayout, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import (BodyLabel, CaptionLabel, ComboBox, InfoBar,
                            InfoBarPosition, LineEdit, PrimaryPushButton,
                            PushButton, ScrollArea, SimpleCardWidget, SpinBox,
                            StrongBodyLabel, SubtitleLabel, SwitchButton)

from app.services.auth import (add_authorized, build_http_url, is_authorized,
                               normalize_host)
from app.services.collab import collab_client, collab_server, PORT
from app.services.logger import log
from app.services.settings import settings
from app.services.stress import engine
from app.ui.disclaimer import AuthDialog
from app.ui.i18n import L
from app.ui.stress_view import HIGH_RATE, MiniStat, fmt_bytes


class CollabView(ScrollArea):
    _net_info_ready = Signal(dict)
    _join_result_ready = Signal(bool, str, str)  # ok, msg, code

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("collabView")
        self.view = QWidget(self)
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.enableTransparentBackground()

        # 节点本地统计快照（用于上报给主控）
        self._local_stats = {
            "total": 0, "success": 0, "fail": 0,
            "qps": 0.0, "tx": 0, "state": "idle",
        }
        self._log_lines = []

        root = QVBoxLayout(self.view)
        root.setContentsMargins(28, 22, 28, 28)
        root.setSpacing(14)

        root.addWidget(SubtitleLabel(L("协同测试", "Collaborative Testing"), self.view))

        # 连接方式选择
        mode_row = QHBoxLayout()
        mode_row.addWidget(BodyLabel(L("连接方式", "Connection"), self.view))
        self.connCombo = ComboBox(self.view)
        self.connCombo.addItems([L("中继（外网推荐）", "Relay (WAN)"), L("直连（局域网）", "Direct (LAN)")])
        self.connCombo.setCurrentIndex(0)  # 默认中继模式
        self.connCombo.currentIndexChanged.connect(self._switch_conn_mode)
        mode_row.addWidget(self.connCombo)
        mode_row.addStretch(1)
        root.addLayout(mode_row)

        cols = QHBoxLayout()
        cols.setSpacing(14)
        self._columns_layout = cols

        # ========== 主控卡片 ==========
        self.hostCard = SimpleCardWidget(self.view)
        hl = QVBoxLayout(self.hostCard)
        hl.setContentsMargins(20, 16, 20, 16)
        hl.setSpacing(10)
        hl.addWidget(StrongBodyLabel(L("发起协同", "Host a Session"), self.hostCard))
        hl.addWidget(BodyLabel(L("最大节点数", "Max Nodes"), self.hostCard))
        self.maxNodesSpin = SpinBox(self.hostCard)
        self.maxNodesSpin.setRange(1, 64)
        self.maxNodesSpin.setValue(8)
        hl.addWidget(self.maxNodesSpin)
        self.genBtn = PrimaryPushButton(L("生成邀请码", "Generate Invite"), self.hostCard)
        self.genBtn.clicked.connect(self._gen_invite)
        hl.addWidget(self.genBtn)

        # 邀请码（点击即可复制）—— 初始隐藏，生成邀请码后再显示
        self.inviteBtn = PushButton("-", self.hostCard)
        self.inviteBtn.clicked.connect(self._copy_invite)
        # 用 setCustomStyleSheet 叠加字号字重，不用 setStyleSheet 覆盖：
        # 后者会清掉按钮的主题文字色规则，导致深色模式下文字变黑
        from qfluentwidgets import setCustomStyleSheet
        setCustomStyleSheet(
            self.inviteBtn,
            "PushButton{font-size:22px; font-weight:700; padding:6px 24px;}",
            "PushButton{font-size:22px; font-weight:700; padding:6px 24px;}")
        self.inviteBtn.hide()  # 初始隐藏
        hl.addWidget(self.inviteBtn)

        # 邀请码有效期提示 —— 初始隐藏
        self.inviteValidHint = CaptionLabel("", self.hostCard)
        self.inviteValidHint.setWordWrap(True)
        self.inviteValidHint.hide()  # 初始隐藏
        hl.addWidget(self.inviteValidHint)

        # 中继模式提示
        self.relayHint = CaptionLabel("", self.hostCard)
        self.relayHint.setWordWrap(True)
        hl.addWidget(self.relayHint)

        # 直连模式：地址信息（仅直连模式且有内容时显示）—— 初始隐藏
        self.addrLabel = BodyLabel("", self.hostCard)
        self.addrLabel.setWordWrap(True)
        self.addrLabel.hide()  # 初始隐藏
        hl.addWidget(self.addrLabel)
        addr_row = QHBoxLayout()
        self.copyLanBtn = PushButton(L("复制局域网地址", "Copy LAN Address"), self.hostCard)
        self.copyLanBtn.clicked.connect(self._copy_lan)
        self.adminBtn = PushButton(L("以管理员重启并放行防火墙", "Restart as admin to open firewall"), self.hostCard)
        self.adminBtn.clicked.connect(self._restart_as_admin)
        self.adminBtn.hide()
        addr_row.addWidget(self.copyLanBtn)
        addr_row.addWidget(self.adminBtn)
        addr_row.addStretch(1)
        hl.addLayout(addr_row)

        self.pushStartBtn = PushButton(L("广播开始（使用压测页配置）", "Broadcast Start (uses Stress config)"), self.hostCard)
        self.pushStartBtn.clicked.connect(self._push_start)
        self.pushStopBtn = PushButton(L("广播停止", "Broadcast Stop"), self.hostCard)
        self.pushStopBtn.clicked.connect(lambda: (collab_server.broadcast({"type": "stop"}), self._server_log(L("已广播停止", "Stop broadcast"))))
        self.pushStartBtn.setEnabled(False)
        self.pushStopBtn.setEnabled(False)
        hl.addWidget(self.pushStartBtn)
        hl.addWidget(self.pushStopBtn)
        hl.addStretch(1)
        # 两张卡片内容高度会随连接方式变化；顶部对齐可避免较短的卡片
        # 被另一张卡片强制拉高，从而在底部留下大片卡片内空白。
        cols.addWidget(self.hostCard, 1, Qt.AlignmentFlag.AlignTop)

        # ========== 节点卡片 ==========
        self.nodeCard = SimpleCardWidget(self.view)
        nl = QVBoxLayout(self.nodeCard)
        nl.setContentsMargins(20, 16, 20, 16)
        nl.setSpacing(10)
        nl.addWidget(StrongBodyLabel(L("加入协同", "Join a Session"), self.nodeCard))

        # 中继模式提示
        self.nodeRelayHint = CaptionLabel("", self.nodeCard)
        self.nodeRelayHint.setWordWrap(True)
        nl.addWidget(self.nodeRelayHint)

        # 直连模式地址输入
        self.hostLabel = BodyLabel(L("主控地址（局域网 IP，可带端口）", "Host address (LAN IP, port optional)"), self.nodeCard)
        nl.addWidget(self.hostLabel)
        self.hostEdit = LineEdit(self.nodeCard)
        self.hostEdit.setPlaceholderText(L("192.168.1.100:50505",
                                            "192.168.1.100:50505"))
        nl.addWidget(self.hostEdit)

        nl.addWidget(BodyLabel(L("邀请码", "Invite Code"), self.nodeCard))
        self.codeEdit = LineEdit(self.nodeCard)
        self.codeEdit.setPlaceholderText("ABC123")
        nl.addWidget(self.codeEdit)
        nl.addWidget(CaptionLabel(L("💡 邀请码生成后 5 分钟内有效，请在有效期内加入",
                                    "💡 Invite code is valid for 5 minutes after generation; join within that window"), self.nodeCard))
        nl.addWidget(BodyLabel(L("节点名称", "Node Name"), self.nodeCard))
        self.nameEdit = LineEdit(self.nodeCard)
        import socket as _s
        try:
            self.nameEdit.setText(_s.gethostname())
        except OSError:
            pass
        nl.addWidget(self.nameEdit)
        self.joinBtn = PrimaryPushButton(L("加入", "Join"), self.nodeCard)
        self.joinBtn.clicked.connect(self._join)
        self.leaveBtn = PushButton(L("退出", "Leave"), self.nodeCard)
        self.leaveBtn.clicked.connect(self._leave)
        self.leaveBtn.setEnabled(False)
        nl.addWidget(self.joinBtn)
        nl.addWidget(self.leaveBtn)
        nl.addStretch(1)
        cols.addWidget(self.nodeCard, 1, Qt.AlignmentFlag.AlignTop)
        self.hostCard.setMinimumWidth(420)
        self.nodeCard.setMinimumWidth(420)

        root.addLayout(cols)

        # 节点统计（主控端聚合显示）
        nodes_card = SimpleCardWidget(self.view)
        ncl = QVBoxLayout(nodes_card)
        ncl.setContentsMargins(20, 16, 20, 14)
        ncl.addWidget(StrongBodyLabel(L("节点状态", "Node Status"), nodes_card))
        self.mTotal = MiniStat("#0078D4", nodes_card)
        self.mSuccess = MiniStat("#107C10", nodes_card)
        self.mQps = MiniStat("#8764B8", nodes_card)
        self.mTx = MiniStat("#00B7C3", nodes_card)
        grid = QGridLayout()
        grid.addWidget(self._mini(L("累计请求", "Total Requests"), self.mTotal, nodes_card), 0, 0)
        grid.addWidget(self._mini(L("累计成功", "Total Success"), self.mSuccess, nodes_card), 0, 1)
        grid.addWidget(self._mini(L("实时 QPS", "Live QPS"), self.mQps, nodes_card), 0, 2)
        grid.addWidget(self._mini(L("总发送流量", "Total Sent Traffic"), self.mTx, nodes_card), 0, 3)
        ncl.addLayout(grid)
        self.nodeListLabel = CaptionLabel(L("（暂无节点连接）", "(no nodes connected)"), nodes_card)
        self.nodeListLabel.setWordWrap(True)
        ncl.addWidget(self.nodeListLabel)
        root.addWidget(nodes_card)

        # 日志
        log_card = SimpleCardWidget(self.view)
        lcl = QVBoxLayout(log_card)
        lcl.setContentsMargins(20, 16, 20, 14)
        log_head = QHBoxLayout()
        log_head.addWidget(StrongBodyLabel(L("协同日志", "Collab Log"), log_card))
        log_head.addStretch(1)
        self.copyLogBtn = PushButton(L("复制日志", "Copy Log"), log_card)
        self.copyLogBtn.clicked.connect(self._copy_log)
        self.clearLogBtn = PushButton(L("清空", "Clear"), log_card)
        self.clearLogBtn.clicked.connect(self._clear_log)
        log_head.addWidget(self.copyLogBtn)
        log_head.addWidget(self.clearLogBtn)
        lcl.addLayout(log_head)
        self.logLabel = CaptionLabel(L("（暂无）", "(empty)"), log_card)
        self.logLabel.setWordWrap(True)
        lcl.addWidget(self.logLabel)
        root.addWidget(log_card)
        root.addStretch(1)

        # 信号
        self._net_info_ready.connect(self._on_net_info)
        self._join_result_ready.connect(self._on_join_result)
        collab_server.log_msg.connect(self._server_log)
        collab_server.nodes_changed.connect(self._update_node_stats)
        collab_server.relay_status_changed.connect(self._on_relay_status)
        collab_client.status_msg.connect(lambda m: self._client_log(m))
        collab_client.start_requested.connect(self._on_remote_start)
        collab_client.stop_requested.connect(self._on_remote_stop)
        engine.snapshot.connect(self._on_local_snapshot)
        engine.report_ready.connect(self._on_remote_report)
        engine.started.connect(self._on_remote_engine_started)
        self._waiting_remote = False
        self._startup_busy = False   # 远程启动时的等待遮罩标记
        self._remote_start_config = None  # 延迟启动时暂存配置

        # 定时器：主控端定时聚合节点统计；节点端定时上报本地统计
        self._stat_timer = QTimer(self)
        self._stat_timer.timeout.connect(self._tick_stats)
        self._stat_timer.start(1000)
        self._node_states = {}

        self._switch_conn_mode(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        narrow = self.width() < 960
        direction = (QBoxLayout.Direction.TopToBottom if narrow
                     else QBoxLayout.Direction.LeftToRight)
        if self._columns_layout.direction() != direction:
            self._columns_layout.setDirection(direction)
        self.hostCard.setMinimumWidth(0 if narrow else 420)
        self.nodeCard.setMinimumWidth(0 if narrow else 420)

    def _is_relay_mode(self):
        return self.connCombo.currentIndex() == 0

    def _mini(self, title, value_label, parent):
        w = QWidget(parent)
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        v.addWidget(CaptionLabel(title, w))
        v.addWidget(value_label)
        return w

    def _switch_conn_mode(self, idx):
        """切换中继/直连模式时更新 UI 显示。"""
        is_relay = (idx == 0)
        relay_desc = L("公共 MQTT 中继 (broker.hivemq.com)",
                       "public MQTT relay (broker.hivemq.com)")

        # 主控侧
        if is_relay:
            self.relayHint.setText(L(
                f"中继模式：通过 {relay_desc} 中转，支持外网节点加入，无需部署服务器、无需公网 IP。",
                f"Relay mode: routed via public MQTT broker, WAN nodes supported, no server setup needed."))
            self.addrLabel.hide()
            self.copyLanBtn.hide()
            self.adminBtn.hide()
        else:
            self.relayHint.setText(L(
                "直连模式：节点需与主控在同一局域网。",
                "Direct mode: nodes must be on the same LAN as the host."))
            # 只有当 addrLabel 有内容时才显示
            if self.addrLabel.text().strip():
                self.addrLabel.show()
            else:
                self.addrLabel.hide()
            self.copyLanBtn.hide()  # 生成邀请码后才显示复制按钮
            self.adminBtn.hide()  # 局域网模式默认不需要特殊防火墙配置（子网内通常放行）

        # 节点侧
        if is_relay:
            self.nodeRelayHint.setText(L(
                f"中继模式：自动通过 {relay_desc} 连接主控，只需输入邀请码，无需填写主控地址。",
                f"Relay mode: auto-connect via public MQTT broker, only the invite code is needed."))
            self.hostLabel.hide()
            self.hostEdit.hide()
        else:
            self.nodeRelayHint.setText(L(
                "直连模式：请填写主控的局域网 IP 地址。",
                "Direct mode: enter the host's LAN IP address."))
            self.hostLabel.show()
            self.hostEdit.show()

        # 已生成邀请码后切换连接方式时，按新模式自动重新生成
        if getattr(self, "_last_code", None):
            self._server_log(L(
                f"连接模式已切换为{'中继' if is_relay else '直连'}，正在按新模式自动重新生成邀请码…",
                f"Connection mode switched to {'relay' if is_relay else 'direct'}; regenerating invite automatically..."))
            self._gen_invite()

        # show()/hide() 会改变两张卡片的 sizeHint；立即刷新布局，避免 Qt
        # 暂时沿用切换前的高度，在较短卡片底部留下空白区域。
        self.hostCard.updateGeometry()
        self.nodeCard.updateGeometry()
        self._columns_layout.invalidate()
        self._columns_layout.activate()

    def _gen_invite(self):
        # 使旧的中继等待状态与超时定时器失效（快速切换模式时防止旧定时器误关新遮罩、旧遮罩残留）
        self._relay_busy_token = getattr(self, "_relay_busy_token", 0) + 1
        if getattr(self, "_relay_busy", False):
            self._relay_busy = False
            win = self.window()
            if hasattr(win, "hide_busy"):
                win.hide_busy()
        use_relay = self._is_relay_mode()
        if use_relay:
            win = self.window()
            if hasattr(win, "show_busy"):
                self._relay_busy = True  # 标记：仅隐藏自己显示的遮罩，避免误关其他遮罩（如压测启动遮罩）
                token = self._relay_busy_token
                win.show_busy(L("正在连接中继服务器...", "Connecting to relay server..."),
                              L("请稍候", "Please wait"))
                # 安全超时：MQTT无响应时防止遮罩永久卡住（token失效旧的定时器）
                QTimer.singleShot(8000, lambda: self._relay_busy_timeout(token))
        code = collab_server.generate_invite(self.maxNodesSpin.value(), use_relay=use_relay)
        if not code:
            self._last_code = ""
            self.inviteBtn.hide()
            self.inviteValidHint.hide()
            self.pushStartBtn.setEnabled(False)
            self.pushStopBtn.setEnabled(False)
            InfoBar.error(
                L("生成邀请码失败", "Invite generation failed"),
                L("无法监听协同端口，请检查端口是否被占用。",
                  "The collaboration port could not be opened; check whether it is already in use."),
                parent=self.window())
            return
        self._last_code = code
        self.inviteBtn.setText(code)
        self.inviteBtn.show()  # 显示邀请码按钮
        self.inviteValidHint.show()  # 显示有效期提示
        self.pushStartBtn.setEnabled(True)
        self.pushStopBtn.setEnabled(True)

        if use_relay:
            relay_addr = collab_server.relay_addr_display()
            self._server_log(L(f"已生成邀请码 {code}（中继模式，通过 {relay_addr} 中转）",
                               f"Invite generated: {code} (relay via {relay_addr})"))
            self.copyLanBtn.hide()
        else:
            # 直连局域网模式：只获取局域网IP，不探测公网
            self._server_log(L("已生成邀请码（局域网直连模式）", "Invite generated (LAN direct mode)"))
            lan = self._get_lan_ip_simple()
            self._lan_addr = f"{lan}:{PORT}"
            self._pub_addr = ""  # 局域网模式不使用公网地址
            self.addrLabel.setText(L(
                f"局域网地址：{self._lan_addr}  ← 内网节点连这个",
                f"LAN address: {self._lan_addr}  ← for LAN nodes"))
            self.addrLabel.show()  # 显示地址标签
            self.copyLanBtn.show()  # 显示局域网复制按钮
        log.info(L("生成协同邀请码", "Collab invite generated"))

    def _on_relay_status(self, connected: bool):
        """中继服务器连接状态变化时的回调：只隐藏自己显示的连接遮罩。"""
        if getattr(self, "_relay_busy", False):
            self._relay_busy = False
            win = self.window()
            if hasattr(win, "hide_busy"):
                win.hide_busy()

    def _relay_busy_timeout(self, token=None):
        """安全超时：MQTT长时间无响应时隐藏连接遮罩并提示。token 用于失效过期定时器。"""
        if token is not None and token != getattr(self, "_relay_busy_token", 0):
            return  # 过期的超时定时器（期间已重新生成邀请码/切换模式）
        if getattr(self, "_relay_busy", False):
            self._relay_busy = False
            win = self.window()
            if hasattr(win, "hide_busy"):
                win.hide_busy()
            self._server_log(L("连接中继服务器超时，请检查网络后重试",
                               "Relay connection timed out; check your network and retry"))

    def _get_lan_ip_simple(self):
        """简单获取局域网IP，不进行公网探测。"""
        from app.services.network import get_lan_ip
        return get_lan_ip()

    def _probe_net(self):
        """后台线程：探测公网 IP + UPnP 自动端口映射 + 防火墙放行。"""
        from app.services.network import (add_firewall_rule, get_lan_ip,
                                          get_public_ip, upnp_map)
        lan = get_lan_ip()
        pub = get_public_ip()
        fw_ok = add_firewall_rule()
        up_ok, up_info = upnp_map()
        self._net_info_ready.emit({
            "lan": lan, "pub": pub, "fw": fw_ok,
            "up": up_ok, "up_ip": up_info if up_ok else None,
        })

    def _on_net_info(self, d):
        pub, lan = d["pub"], d["lan"]
        lines = []
        is_v6 = bool(pub) and ":" in pub
        if d["up"]:
            ext = d["up_ip"] or pub
            self._pub_addr = f"[{ext}]:{PORT}" if ":" in ext else f"{ext}:{PORT}"
            lines.append(L(f"外网地址（UPnP 已自动映射）：{self._pub_addr}  ← 外网节点连这个",
                           f"WAN address (UPnP auto-mapped): {self._pub_addr}  ← for WAN nodes"))
        elif pub and is_v6:
            self._pub_addr = f"[{pub}]:{PORT}"
            lines.append(L(f"外网地址（IPv6，无需端口映射，直连可用）：{self._pub_addr}  ← 外网节点连这个",
                           f"WAN address (IPv6, no port mapping needed): {self._pub_addr}  ← for WAN nodes"))
        elif pub:
            self._pub_addr = f"{pub}:{PORT}"
            lines.append(L(f"公网 IP：{self._pub_addr}（IPv4，需在路由器转发 TCP {PORT} 到本机，或使用中继模式）",
                           f"Public IP: {self._pub_addr} (IPv4; forward TCP {PORT} on router, or use Relay mode)"))
        else:
            self._pub_addr = ""
            lines.append(L("无法探测公网 IP（建议切换到中继模式）",
                           "Cannot detect public IP (consider switching to Relay mode)"))
        self._lan_addr = f"{lan}:{PORT}"
        lines.append(L(f"局域网地址：{self._lan_addr}  ← 内网节点连这个",
                       f"LAN address: {self._lan_addr}  ← for LAN nodes"))
        if d["fw"]:
            self.adminBtn.hide()
            lines.append(L(f"防火墙：已放行 TCP {PORT}", f"Firewall: TCP {PORT} allowed"))
        else:
            self.adminBtn.show()
            lines.append(L(f"防火墙：尚未放行 TCP {PORT}，点击下方按钮以管理员身份重启后自动放行",
                           f"Firewall: TCP {PORT} not allowed yet; click the button below to elevate"))
        self.addrLabel.setText("\n".join(lines))
        self.addrLabel.show()  # 有内容了，显示地址标签
        self.copyLanBtn.show()

    def _restart_as_admin(self):
        """通过 UAC 以管理员身份重启本程序，以自动放行防火墙。"""
        import ctypes
        import os
        import sys
        try:
            params = f'"{os.path.abspath(sys.argv[0])}"'
            ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
            if ret > 32:
                log.info(L("请求管理员权限重启以放行防火墙。", "Requesting admin restart to open firewall."))
                QGuiApplication.quit()
            else:
                InfoBar.warning(L("未授权", "Not elevated"),
                                L("您拒绝了 UAC 授权，防火墙未能放行", "UAC denied; firewall not opened"),
                                parent=self.window())
        except Exception as e:
            InfoBar.error(L("失败", "Failed"), str(e), parent=self.window())

    def _copy_invite(self):
        code = getattr(self, "_last_code", None)
        if not code:
            return
        # 邀请码已过期或房间已关闭：不允许再复制（新节点已无法用该码加入）
        remaining = collab_server.invite_remaining_seconds()
        if remaining <= 0:
            InfoBar.warning(L("邀请码已失效", "Invite code expired"),
                            L("该邀请码已过期，新节点无法使用它加入。如需邀请新节点，请重新生成邀请码。",
                              "This invite code has expired; new nodes can no longer join with it. Generate a new invite to add nodes."),
                            parent=self.window())
            return
        QGuiApplication.clipboard().setText(code)
        InfoBar.success(L("已复制", "Copied"),
                        L(f"邀请码 {code} 已复制到剪贴板", f"Invite code {code} copied to clipboard"),
                        parent=self.window())

    def _copy_lan(self):
        addr = getattr(self, "_lan_addr", "")
        if not addr:
            return
        QGuiApplication.clipboard().setText(addr)
        InfoBar.success(L("已复制", "Copied"), addr, parent=self.window())

    def _push_start(self):
        nodes = collab_server.get_nodes()
        if not nodes:
            InfoBar.warning(
                L("暂无在线节点", "No Online Nodes"),
                L("请等待至少一个节点加入后再广播开始指令。",
                  "Wait for at least one node to join before broadcasting a start command."),
                parent=self.window())
            return
        stress = self.window().stress
        target_raw = stress.targetEdit.toPlainText().strip().splitlines()[0].strip()
        host = normalize_host(target_raw)
        if not host:
            InfoBar.warning(L("参数错误", "Invalid input"),
                            L("请先在压测页填写目标", "Set the target on the Stress page first"),
                            parent=self.window())
            return
        # 授权校验：主控端广播前，目标必须先通过本机授权确认
        if not is_authorized(host):
            dlg = AuthDialog(host, self.window())
            if not dlg.exec():
                InfoBar.warning(L("已取消", "Cancelled"),
                                L(f"目标 {host} 未授权，测试已阻止", f"Target {host} not authorized; test blocked"),
                                parent=self.window())
                return
            if not add_authorized(host, dlg.note()):
                InfoBar.error(
                    L("授权保存失败", "Authorization Save Failed"),
                    L(f"无法保存目标 {host} 的授权记录：{settings.last_error}",
                      f"Could not save authorization for {host}: {settings.last_error}"),
                    parent=self.window())
                return
            stress.refresh_auth_list()
        # 高速率二次确认：与本地压测一致（>500 QPS时提醒），并按在线节点数计算合计压力
        rate = stress.rateSpin.value()
        if rate > HIGH_RATE:
            from qfluentwidgets import MessageBox
            n = len(nodes)
            w = MessageBox(L("高请求速率二次确认", "High Rate Confirmation"),
                           L(f"协同测试将广播开始：每个节点速率上限 {rate} QPS，"
                             f"当前在线 {n} 个节点（合计约 {rate * n} QPS 同时压向同一目标）。\n"
                             f"请再次确认您拥有目标授权，且目标可承受该速率。",
                             f"Collab test will broadcast: rate cap {rate} QPS per node, "
                             f"{n} node(s) online (~{rate * n} QPS combined against the same target).\n"
                             f"Confirm again that the target is authorized and can handle this rate."),
                           self.window())
            if not w.exec():
                InfoBar.warning(L("已取消", "Cancelled"),
                                L("高速率未确认，广播已取消", "High rate not confirmed; broadcast cancelled"),
                                parent=self.window())
                return
        # 与本地压测一致：解析自定义请求头（非法 JSON 时阻止广播）
        try:
            headers_raw = stress.headersEdit.toPlainText().strip()
            headers = json.loads(headers_raw) if headers_raw else {}
        except json.JSONDecodeError:
            InfoBar.warning(L("请求头格式错误", "Invalid headers"),
                            L("请求头须为合法 JSON", "Headers must be valid JSON"),
                            parent=self.window())
            return
        if not isinstance(headers, dict):
            InfoBar.warning(
                L("请求头格式错误", "Invalid headers"),
                L("请求头必须是 JSON 对象，例如 {\"User-Agent\": \"FlowBench\"}",
                  "Headers must be a JSON object, for example {\"User-Agent\": \"FlowBench\"}"),
                parent=self.window())
            return
        config = {
            "target": host,
            "port": stress.portSpin.value(),
            "protocol": stress.protoCombo.currentText(),
            "url": "",
            "threads": stress.threadSpin.value(),
            "duration": stress.get_duration_seconds(),
            "rate": stress.rateSpin.value(),
            "packet_size": settings.default_packet_size,
            "timeout": settings.default_timeout_ms,
            "headers": headers,
        }
        if config["protocol"] in ("HTTP", "HTTPS"):
            config["url"] = build_http_url(
                target_raw, host, config["port"], config["protocol"])
        self._local_stats = {
            "state": "running", "total": 0, "success": 0, "fail": 0,
            "qps": 0.0, "tx": 0,
        }
        self._node_states = {}
        collab_server.broadcast({"type": "start", "config": config})
        self._server_log(L(f"已广播开始: {config['protocol']}://{host}:{config['port']}",
                           f"Start broadcast: {config['protocol']}://{host}:{config['port']}"))
        log.info(L("广播协同开始", "Collab start broadcast"))

    def _join(self):
        use_relay = self._is_relay_mode()
        code = self.codeEdit.text().strip()
        name = self.nameEdit.text().strip() or "node"
        host = self.hostEdit.text().strip()

        if not code:
            InfoBar.warning(L("参数错误", "Invalid input"),
                            L("请填写邀请码", "Invite code required"),
                            parent=self.window())
            return
        if not use_relay and not host:
            InfoBar.warning(L("参数错误", "Invalid input"),
                            L("请填写主控地址", "Host address required"),
                            parent=self.window())
            return

        self.joinBtn.setEnabled(False)
        self._client_log(L("正在连接...", "Connecting..."))

        # 显示加载遮罩
        win = self.window()
        if hasattr(win, "show_busy"):
            if use_relay:
                win.show_busy(L("正在连接中继服务器...", "Connecting to relay server..."),
                              L("请稍候", "Please wait"))
            else:
                win.show_busy(L("正在连接主控...", "Connecting to host..."),
                              L("请稍候", "Please wait"))

        def _do_join():
            ok, msg = collab_client.join(host, code, name, use_relay=use_relay)
            self._join_result_ready.emit(ok, msg, code)
        threading.Thread(target=_do_join, daemon=True).start()

    def _on_join_result(self, ok: bool, msg: str, code: str):
        """加入操作完成后在主线程回调"""
        # 隐藏加载遮罩
        win = self.window()
        if hasattr(win, "hide_busy"):
            win.hide_busy()

        if ok:
            self.leaveBtn.setEnabled(True)
            self.connCombo.setEnabled(False)
            self._client_log(L(f"已加入，邀请码 {code}", f"Joined with code {code}"))
            log.info(L(f"加入协同: code={code}", f"Joined collab: code={code}"))
        else:
            self.joinBtn.setEnabled(True)
            InfoBar.error(L("加入失败", "Join failed"), msg, parent=self.window())

    def _leave(self):
        collab_client.leave()
        self.joinBtn.setEnabled(True)
        self.leaveBtn.setEnabled(False)
        self.connCombo.setEnabled(True)
        self._client_log(L("已退出", "Left"))

    def _on_remote_start(self, config):
        """收到主控开始指令：主控已完成目标授权，节点端直接填充配置并启动。"""
        stress = self.window().stress
        try:
            config = self._validate_remote_config(config, stress)
        except (TypeError, ValueError) as e:
            reason = str(e) or L("配置格式错误", "Malformed configuration")
            self._client_log(L(f"已拒绝无效的主控配置：{reason}",
                               f"Rejected invalid host configuration: {reason}"))
            log.warning(L(f"拒绝无效协同启动配置：{reason}",
                          f"Rejected invalid collab start configuration: {reason}"))
            InfoBar.warning(
                L("启动指令无效", "Invalid Start Command"), reason,
                parent=self.window())
            return
        self._client_log(L("收到主控指令，开始压测", "Received host command; starting"))
        target = config.get("url") or config.get("target", "")
        if target:
            stress.targetEdit.setPlainText(target)
        # 先切协议再设端口：协议切换会重置默认端口，顺序反了会覆盖主控下发的端口
        proto = config.get("protocol", "HTTP")
        idx = stress.protoCombo.findText(proto)
        if idx >= 0:
            stress.protoCombo.setCurrentIndex(idx)
        port = config.get("port", 80)
        stress.portSpin.setValue(port)
        threads = config.get("threads", 8)
        stress.threadSpin.setValue(threads)
        stress.threadSlider.setValue(threads)
        rate = config.get("rate", 100)
        stress.rateSpin.setValue(rate)
        # 持续时间：把主控下发的秒数换算成最合适的单位，节点压测页显示与实际运行一致
        dur = int(config.get("duration", 30)) or 30
        if dur % 86400 == 0 and dur // 86400 >= 1:
            unit_idx, unit_val = 3, dur // 86400
        elif dur % 3600 == 0 and dur // 3600 >= 1:
            unit_idx, unit_val = 2, dur // 3600
        elif dur % 60 == 0 and dur // 60 >= 1:
            unit_idx, unit_val = 1, dur // 60
        else:
            unit_idx, unit_val = 0, dur
        stress.durUnitCombo.setCurrentIndex(unit_idx)
        stress.durSpin.setRange(1, stress.DUR_MAX[unit_idx])
        stress.durSpin.setValue(unit_val)
        # 请求头：仅 HTTP/HTTPS 时回显到节点压测页
        headers = config.get("headers")
        if isinstance(headers, dict) and headers and proto in ("HTTP", "HTTPS"):
            stress.headersEdit.setPlainText(json.dumps(headers, ensure_ascii=False, indent=2))

        # 立即显示等待遮罩，给用户明确反馈
        total_threads = threads
        win = self.window()
        if hasattr(win, "show_busy"):
            self._startup_busy = True
            win.show_busy(L("正在启动压测...", "Starting stress test..."),
                          L(f"正在创建 {total_threads} 个 worker 线程", f"Creating {total_threads} worker threads"))

        # 延迟80ms再启动引擎，确保遮罩先渲染
        self._remote_start_config = config
        QTimer.singleShot(80, self._do_remote_start)

    @staticmethod
    def _validate_remote_config(config, stress):
        """校验网络收到的启动配置，并返回只含安全范围参数的新字典。"""
        if not isinstance(config, dict):
            raise TypeError(L("主控配置必须是对象", "Host configuration must be an object"))

        def bounded_int(name, default, minimum, maximum):
            value = config.get(name, default)
            if isinstance(value, bool):
                raise ValueError(L(f"参数 {name} 不是有效整数",
                                   f"Parameter {name} is not a valid integer"))
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise ValueError(L(f"参数 {name} 不是有效整数",
                                   f"Parameter {name} is not a valid integer")) from None
            if not minimum <= value <= maximum:
                raise ValueError(L(f"参数 {name} 超出允许范围 {minimum}–{maximum}",
                                   f"Parameter {name} is outside the allowed range {minimum}–{maximum}"))
            return value

        proto = str(config.get("protocol", "HTTP")).strip().upper()
        if not proto or stress.protoCombo.findText(proto) < 0:
            raise ValueError(L(f"节点不支持协议：{proto or '-'}",
                               f"Protocol is not available on this node: {proto or '-'}"))

        raw_target = config.get("url") or config.get("target") or ""
        if not isinstance(raw_target, str) or not raw_target.strip():
            raise ValueError(L("缺少目标地址", "Target address is missing"))
        raw_target = raw_target.strip()
        if len(raw_target) > 2048:
            raise ValueError(L("目标地址过长", "Target address is too long"))
        host = normalize_host(raw_target)
        if not host:
            raise ValueError(L("目标地址无效", "Target address is invalid"))

        headers = config.get("headers", {})
        if headers is None:
            headers = {}
        if not isinstance(headers, dict):
            raise ValueError(L("请求头必须是 JSON 对象", "Headers must be a JSON object"))
        if len(headers) > 100 or len(json.dumps(headers, ensure_ascii=False)) > 65536:
            raise ValueError(L("请求头数量或大小超出限制", "Headers exceed the count or size limit"))

        port = bounded_int("port", 80, 1, 65535)
        safe = {
            "target": host,
            "port": port,
            "protocol": proto,
            "url": raw_target if proto in ("HTTP", "HTTPS") else "",
            "threads": bounded_int("threads", 8, 1, 1024),
            "duration": bounded_int("duration", 30, 1, 30 * 24 * 60 * 60),
            "rate": bounded_int("rate", 100, 1, 100000),
            "packet_size": bounded_int("packet_size", settings.default_packet_size,
                                       1, 1024 * 1024),
            "timeout": bounded_int("timeout", settings.default_timeout_ms, 500, 60000),
            "headers": dict(headers),
        }
        if proto in ("HTTP", "HTTPS") and not str(config.get("url") or "").strip():
            safe["url"] = build_http_url(raw_target, host, port, proto)
        return safe

    def _do_remote_start(self):
        """延迟执行远程引擎启动；若引擎忙（本地测试中）则先停止旧测试再启动。"""
        config = self._remote_start_config
        if config is None:
            return
        if engine.running:
            # 节点端正有测试在跑：先停止，稍后自动启动主控的新指令（主控指令优先）
            engine.stop()
            QTimer.singleShot(300, self._do_remote_start)
            return
        self._remote_start_config = None
        if not engine.start([config]):
            self._hide_startup_busy()
            reason = L("压测引擎拒绝了远程启动请求", "The stress engine rejected the remote start request")
            self._client_log(reason)
            InfoBar.warning(L("启动失败", "Start failed"), reason,
                            parent=self.window())
            return
        # 安全超时：5秒后强制隐藏
        QTimer.singleShot(5000, self._hide_startup_busy)

    def _hide_startup_busy(self):
        """隐藏远程启动等待遮罩（确保只隐藏一次）。"""
        if self._startup_busy:
            self._startup_busy = False
            win = self.window()
            if hasattr(win, "hide_busy"):
                win.hide_busy()

    def _on_remote_stop(self):
        """收到主控停止指令。"""
        self._client_log(L("收到主控指令，停止压测", "Received host command; stopping"))
        # A stop command must also cancel a start waiting in the 80 ms render
        # delay or in the retry loop that waits for a previous job to finish.
        self._remote_start_config = None
        self._hide_startup_busy()
        # 仅在确实有测试运行时才显示遮罩（否则遮罩会因无结束事件而永久卡死）
        if engine.running:
            win = self.window()
            if hasattr(win, "show_busy"):
                win.show_busy(L("正在停止...", "Stopping..."),
                              L("等待 worker 线程退出", "Waiting for worker threads to exit"))
        engine.stop()

    def _on_remote_report(self, r):
        """本地节点压测结束：上报最终结果并写入协同日志。"""
        self._waiting_remote = False
        self._startup_busy = False
        win = self.window()
        if hasattr(win, "hide_busy"):
            win.hide_busy()

        final_stats = {
            "state": "completed",
            "total": r.get("total", 0),
            "success": r.get("success", 0),
            "fail": r.get("fail", 0),
            "qps": 0.0,
            "tx": r.get("tx", r.get("bytes_tx", 0)),
        }
        if collab_client.connected:
            collab_client.send_stats(final_stats)

        self._local_stats = dict(final_stats)
        self._client_log(L(
            f"压测完成：总请求 {final_stats['total']}  成功 {final_stats['success']}  "
            f"失败 {final_stats['fail']}  发送 {fmt_bytes(final_stats['tx'])}",
            f"Test completed: total {final_stats['total']}  success {final_stats['success']}  "
            f"failed {final_stats['fail']}  sent {fmt_bytes(final_stats['tx'])}"))

    def _on_remote_engine_started(self):
        """远程启动的 worker 线程已创建完成。遮罩等真正跑起来再隐藏。"""
        pass

    def _on_local_snapshot(self, d):
        """本地压测快照：节点端保存用于周期上报。"""
        self._local_stats = {
            "state": "running",
            "total": d.get("total", 0),
            "success": d.get("success", 0),
            "fail": d.get("fail", 0),
            "qps": d.get("qps", 0.0),
            "tx": d.get("tx", 0),
        }
        # 收到第一份快照即说明引擎已在运行，隐藏启动遮罩（原生引擎的 active 是
        # 在途请求数，目标响应快时常为 0，不能用来判断是否已启动）。
        if self._startup_busy:
            self._hide_startup_busy()

    def _tick_stats(self):
        """每秒：节点上报本地统计，主控刷新聚合显示 + 更新邀请码倒计时。"""
        # 更新邀请码有效期倒计时
        remaining = collab_server.invite_remaining_seconds()
        if remaining >= 0:
            self.inviteValidHint.show()
            if remaining > 0:
                mins = remaining // 60
                secs = remaining % 60
                self.inviteValidHint.setText(L(
                    f"⏱ 邀请码有效期：{mins}分{secs:02d}秒（已加入节点不受影响，过期后新节点无法加入）",
                    f"⏱ Invite code expires in: {mins}m {secs:02d}s (joined nodes stay connected; new nodes cannot join after expiry)"))
            else:
                self.inviteValidHint.setText(L(
                    "⏱ 邀请码已过期（已加入节点不受影响，新节点无法加入）",
                    "⏱ Invite code expired (joined nodes stay connected; new nodes cannot join)"))
        else:
            self.inviteValidHint.setText("")
            self.inviteValidHint.hide()

        # 节点侧：如果已连接，上报当前统计
        if collab_client.connected:
            collab_client.send_stats(self._local_stats)

        # 主控侧：只要房间处于活跃状态就刷新（邀请码过期只阻止新节点加入，不影响已连接节点）
        if collab_server.active:
            self._update_node_stats()

    def _update_node_stats(self):
        """主控端：聚合所有节点统计并更新 UI。"""
        nodes = collab_server.get_nodes()
        total_req = 0
        total_ok = 0
        total_qps = 0.0
        total_tx = 0
        parts = []
        for name, stats in nodes:
            if stats:
                total_req += stats.get("total", 0)
                total_ok += stats.get("success", 0)
                total_qps += stats.get("qps", 0.0)
                tx = stats.get("tx", stats.get("bytes_tx", 0))
                total_tx += tx
                parts.append(
                    f"{name}: ✓{stats.get('success',0)} ✗{stats.get('fail',0)} "
                    f"({stats.get('qps',0):.0f} QPS) {fmt_bytes(tx)}")
                state = str(stats.get("state", "running"))
                if state == "completed" and self._node_states.get(name) != "completed":
                    self._server_log(L(
                        f"节点 {name} 压测完成：总请求 {stats.get('total',0)}  "
                        f"成功 {stats.get('success',0)}  失败 {stats.get('fail',0)}  "
                        f"发送 {fmt_bytes(tx)}",
                        f"Node {name} finished: total {stats.get('total',0)}  "
                        f"success {stats.get('success',0)}  failed {stats.get('fail',0)}  "
                        f"sent {fmt_bytes(tx)}"))
                self._node_states[name] = state
            else:
                parts.append(f"{name}: " + L("等待中...", "waiting..."))
        if parts:
            self.nodeListLabel.setText("  |  ".join(parts))
        else:
            self.nodeListLabel.setText(L("（暂无节点连接）", "(no nodes connected)"))
        self.mTotal.setText(str(total_req))
        self.mSuccess.setText(str(total_ok))
        self.mQps.setText(f"{total_qps:.1f}")
        self.mTx.setText(fmt_bytes(total_tx))

    def _server_log(self, msg):
        self._append_log(msg)

    def _client_log(self, msg):
        self._append_log(msg)

    def _append_log(self, msg):
        self._log_lines.append(f"[{time.strftime('%H:%M:%S')}] {msg}")
        self._log_lines = self._log_lines[-30:]
        self.logLabel.setText("\n".join(self._log_lines))

    def _copy_log(self):
        """复制当前可见的协同日志，方便排障和分享。"""
        if not self._log_lines:
            InfoBar.warning(L("暂无日志", "No log"),
                            L("当前没有可复制的协同日志", "There is no collaborative log to copy"),
                            parent=self.window())
            return
        QGuiApplication.clipboard().setText("\n".join(self._log_lines))
        InfoBar.success(L("已复制", "Copied"),
                        L(f"已复制 {len(self._log_lines)} 条协同日志",
                          f"Copied {len(self._log_lines)} collaborative log entries"),
                        parent=self.window())

    def _clear_log(self):
        """只清空页面中的临时日志，不影响审计日志文件。"""
        self._log_lines.clear()
        self.logLabel.setText(L("（暂无）", "(empty)"))
