"""设置页：主题/语言/默认参数/日志管理/检查更新/作者。"""
from PySide6.QtCore import Qt, QUrl, Signal, QTimer
from PySide6.QtGui import QColor, QDesktopServices, QCursor, QFont
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout,
                               QVBoxLayout, QWidget, QPushButton)
from qfluentwidgets import (BodyLabel, CaptionLabel, ColorDialog, ComboBox, InfoBar,
                            MessageBox, PushButton, ScrollArea, SimpleCardWidget,
                            SpinBox, StrongBodyLabel, SubtitleLabel, SwitchButton,
                            setTheme, setThemeColor, Theme, IconWidget,
                            FluentIcon, isDarkTheme, qconfig)

from app.services.logger import log
from app.services.settings import settings
from app.services.updater import APP_VERSION
from app.ui.disclaimer import DisclaimerDialog
from app.ui.i18n import L

AUTHOR_NAME = "Carlown"
AUTHOR_URL = "https://github.com/Carlown"


class SettingRow(QWidget):
    def __init__(self, title, desc, control, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 4)
        col = QVBoxLayout()
        t = BodyLabel(title, self)
        d = CaptionLabel(desc, self)
        d.setWordWrap(True)
        col.addWidget(t)
        col.addWidget(d)
        lay.addLayout(col, 1)
        lay.addWidget(control)


class ClickableCard(SimpleCardWidget):
    """可点击的卡片，整个区域可点击，带悬停效果和右侧图标。"""

    clicked = Signal()

    def __init__(self, parent=None):
        self._hover = False  # 必须在 super().__init__ 之前初始化，因为父类会调用 _normalBackgroundColor()
        super().__init__(parent)
        self.setCursor(QCursor(Qt.PointingHandCursor))

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def _normalBackgroundColor(self):
        from qfluentwidgets import isDarkTheme
        if isDarkTheme():
            return QColor(255, 255, 255, 13) if not self._hover else QColor(255, 255, 255, 20)
        else:
            return QColor(255, 255, 255) if not self._hover else QColor(245, 245, 250)


class ThemeColorPicker(QWidget):
    """主题颜色选择器：预设色块 + 自定义颜色对话框。选择即生效并持久化到 settings。"""

    PRESETS = ["#0078D4", "#5B5FC7", "#7B61FF", "#E3008C",
               "#E81123", "#D83B01", "#107C10", "#00B7C3"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._swatches = []
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        for c in self.PRESETS:
            btn = QPushButton(self)
            btn.setFixedSize(22, 22)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setProperty("swatch_color", c)
            btn.setToolTip(c)
            # 用默认参数绑定当前色值，避免闭包陷阱
            btn.clicked.connect(lambda _checked=False, col=c: self._apply(col))
            self._swatches.append(btn)
            lay.addWidget(btn)
        self.customBtn = PushButton(L("自定义…", "Custom…"), self)
        self.customBtn.clicked.connect(self._pick_custom)
        lay.addWidget(self.customBtn)
        self._refresh()
        # 主题切换时刷新色块描边颜色（高亮色随主题变化）
        qconfig.themeChanged.connect(self._refresh)

    def _apply(self, color_str):
        if not settings.set("theme_color", color_str):
            InfoBar.error(
                L("保存失败", "Save Failed"),
                L(f"无法保存主题颜色：{settings.last_error}",
                  f"Could not save the theme color: {settings.last_error}"),
                parent=self.window(), duration=5000)
            return
        setThemeColor(QColor(color_str))
        self._refresh()

    def _refresh(self, *_):
        """刷新色块选中态：当前色加高亮描边。"""
        cur = str(settings.theme_color).upper()
        border_hi = "#FFFFFF" if isDarkTheme() else "#1A1A1A"
        idle_border = "#555555" if isDarkTheme() else "rgba(128,128,128,0.45)"
        idle_hover = "#757575" if isDarkTheme() else "rgba(128,128,128,0.85)"
        for btn in self._swatches:
            c = btn.property("swatch_color")
            if c.upper() == cur:
                btn.setStyleSheet(
                    f"QPushButton {{ background-color: {c}; border-radius: 11px;"
                    f" border: 2px solid {border_hi}; }}")
            else:
                btn.setStyleSheet(
                    f"QPushButton {{ background-color: {c}; border-radius: 11px;"
                    f" border: 1px solid {idle_border}; }}"
                    f"QPushButton:hover {{ border: 2px solid {idle_hover}; }}")

    def _pick_custom(self):
        old = QColor(str(settings.theme_color))
        dlg = ColorDialog(old, L("选择主题颜色", "Choose Theme Color"), self.window())
        # 简化界面：隐藏"原色"对比卡（下半截），新色卡拉伸为整块实时预览
        dlg.oldColorCard.hide()
        dlg.newColorCard.setFixedHeight(256)
        # 显式设置关键按钮和标签，确保对话框始终跟随 FlowBench 选择的语言。
        dlg.yesButton.setText(L("确定", "OK"))
        dlg.cancelButton.setText(L("取消", "Cancel"))
        dlg.editLabel.setText(L("编辑颜色", "Edit Color"))
        dlg.redLabel.setText(L("红", "Red"))
        dlg.greenLabel.setText(L("绿", "Green"))
        dlg.blueLabel.setText(L("蓝", "Blue"))
        # 不做全局实时预览（setThemeColor 会全局重建样式表，拖动时太卡）：
        # 对话框内的大色卡已能实时显示所选颜色，点"确定"才真正应用主题色
        if dlg.exec():
            self._apply(dlg.color.name())


class SettingsView(ScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsView")
        self.view = QWidget(self)
        self.setWidget(self.view)
        self.setWidgetResizable(True)
        self.enableTransparentBackground()

        root = QVBoxLayout(self.view)
        root.setContentsMargins(28, 22, 28, 28)
        root.setSpacing(14)
        root.addWidget(SubtitleLabel(L("设置", "Settings"), self.view))

        # 外观
        appear = SimpleCardWidget(self.view)
        al = QVBoxLayout(appear)
        al.setContentsMargins(20, 16, 20, 16)
        al.setSpacing(4)
        al.addWidget(StrongBodyLabel(L("外观", "Appearance"), appear))
        self.darkSwitch = SwitchButton()
        self.darkSwitch.setChecked(settings.theme == "dark")
        self.darkSwitch.checkedChanged.connect(self._theme_changed)
        al.addWidget(SettingRow(L("深色模式", "Dark mode"),
                                L("Fluent 深色主题", "Fluent dark theme"), self.darkSwitch, appear))
        self.colorPicker = ThemeColorPicker(appear)
        al.addWidget(SettingRow(L("主题颜色", "Theme color"),
                                L("按钮、进度环等强调色，选择后立即生效",
                                  "Accent color for buttons and highlights; applies instantly"),
                                self.colorPicker, appear))
        self.animationSwitch = SwitchButton()
        self.animationSwitch.setChecked(bool(settings.animations_enabled))
        self.animationSwitch.checkedChanged.connect(self._animation_changed)
        al.addWidget(SettingRow(
            L("页面动画", "Page animations"),
            L("启用页面切换和控件依次浮现效果",
              "Enable page transitions and staggered control reveals"),
            self.animationSwitch, appear))
        self.traySwitch = SwitchButton()
        self.traySwitch.setChecked(settings.minimize_to_tray)
        self.traySwitch.checkedChanged.connect(
            lambda v: self._save_bool_setting(
                self.traySwitch, "minimize_to_tray", v))
        al.addWidget(SettingRow(L("关闭时最小化到托盘", "Minimize to tray on close"),
                                L("关闭窗口时程序将驻留系统托盘", "Keep app running in system tray when closing window"), self.traySwitch, appear))
        self.autoUpdateSwitch = SwitchButton()
        self.autoUpdateSwitch.setChecked(settings.auto_check_updates)
        self.autoUpdateSwitch.checkedChanged.connect(
            lambda v: self._save_bool_setting(
                self.autoUpdateSwitch, "auto_check_updates", v))
        al.addWidget(SettingRow(L("启动时自动检查更新", "Auto-check for updates on launch"),
                                L("发现新版本时弹窗提示", "Show notification when new version is available"), self.autoUpdateSwitch, appear))
        self.langCombo = ComboBox()
        self.langCombo.addItems([
            L("跟随系统", "Auto (system)"),
            L("简体中文", "Simplified Chinese"),
            "English",
        ])
        self.langCombo.setCurrentIndex(
            {"auto": 0, "zh-CN": 1, "en-US": 2}.get(settings.language, 0))
        self.langCombo.currentIndexChanged.connect(self._lang_changed)
        al.addWidget(SettingRow(L("界面语言", "Language"),
                                L("跟随系统语言自动选择（默认）", "Auto-follow system language (default)"), self.langCombo, appear))
        root.addWidget(appear)

        # 默认参数
        defaults = SimpleCardWidget(self.view)
        dl = QVBoxLayout(defaults)
        dl.setContentsMargins(20, 16, 20, 16)
        dl.setSpacing(4)
        dl.addWidget(StrongBodyLabel(L("默认参数", "Defaults"), defaults))
        self.threadSpin = SpinBox()
        self.threadSpin.setRange(1, 1024)
        self.threadSpin.setValue(settings.default_threads)
        self.threadSpin.valueChanged.connect(
            lambda v: self._save_int_setting(self.threadSpin, "default_threads", v))
        dl.addWidget(SettingRow(L("默认并发线程", "Default concurrency"),
                                L("新会话的初始线程数", "Initial thread count"), self.threadSpin, defaults))
        self.timeoutSpin = SpinBox()
        self.timeoutSpin.setRange(500, 60000)
        self.timeoutSpin.setValue(settings.default_timeout_ms)
        self.timeoutSpin.setSingleStep(500)
        self.timeoutSpin.valueChanged.connect(
            lambda v: self._save_int_setting(
                self.timeoutSpin, "default_timeout_ms", v))
        dl.addWidget(SettingRow(L("超时(ms)", "Timeout (ms)"),
                                L("单请求超时时间", "Per-request timeout"), self.timeoutSpin, defaults))
        self.rateSpin = SpinBox()
        self.rateSpin.setRange(1, 100000)
        self.rateSpin.setValue(settings.default_rate)
        self.rateSpin.valueChanged.connect(
            lambda v: self._save_int_setting(self.rateSpin, "default_rate", v))
        dl.addWidget(SettingRow(L("默认速率上限(QPS)", "Default rate cap"),
                                L("令牌桶填充速率", "Token bucket fill rate"), self.rateSpin, defaults))
        self.durationSpin = SpinBox()
        self.durationSpin.setRange(1, 3600)
        self.durationSpin.setValue(settings.default_duration)
        self.durationSpin.valueChanged.connect(
            lambda v: self._save_int_setting(
                self.durationSpin, "default_duration", v))
        dl.addWidget(SettingRow(L("默认持续时间(秒)", "Default duration (s)"),
                                L("新会话的初始测试时长", "Initial test duration for new sessions"),
                                self.durationSpin, defaults))
        self.packetSizeSpin = SpinBox()
        self.packetSizeSpin.setRange(1, 1024 * 1024)
        self.packetSizeSpin.setSingleStep(64)
        self.packetSizeSpin.setValue(settings.default_packet_size)
        self.packetSizeSpin.valueChanged.connect(
            lambda v: self._save_int_setting(
                self.packetSizeSpin, "default_packet_size", v))
        dl.addWidget(SettingRow(L("默认报文大小(字节)", "Default packet size (bytes)"),
                                L("TCP、UDP 与插件协议的发送载荷大小",
                                  "Payload size for TCP, UDP and plugin protocols"),
                                self.packetSizeSpin, defaults))
        # Same width for every number box so the right-hand column lines up
        # (each SpinBox otherwise sizes itself to its own range).
        default_spins = (self.threadSpin, self.timeoutSpin, self.rateSpin,
                         self.durationSpin, self.packetSizeSpin)
        spin_width = max(spin.sizeHint().width() for spin in default_spins)
        for spin in default_spins:
            spin.setFixedWidth(spin_width)
        root.addWidget(defaults)

        # 设置备份与恢复（仅处理安全偏好，不导出授权目标、Token 或插件私有数据）
        dataCard = SimpleCardWidget(self.view)
        dataLay = QVBoxLayout(dataCard)
        dataLay.setContentsMargins(20, 16, 20, 16)
        dataLay.setSpacing(6)
        dataLay.addWidget(StrongBodyLabel(L("备份与诊断", "Backup & Diagnostics"), dataCard))
        safeHint = CaptionLabel(L(
            "备份仅包含外观、语言、默认测试参数、托盘和更新偏好；"
            "不会包含授权目标、上次测试、访问令牌、插件私有数据或搜索历史。",
            "Backups contain only appearance, language, test defaults, tray and update preferences; "
            "authorized targets, previous tests, access tokens, private plugin data and search history are excluded."),
            dataCard)
        safeHint.setWordWrap(True)
        dataLay.addWidget(safeHint)
        dataButtons = QHBoxLayout()
        self.backupSettingsBtn = PushButton(L("导出设置备份…", "Export Settings…"), dataCard)
        self.backupSettingsBtn.clicked.connect(self._export_settings_backup)
        self.restoreSettingsBtn = PushButton(L("恢复设置备份…", "Restore Settings…"), dataCard)
        self.restoreSettingsBtn.clicked.connect(self._import_settings_backup)
        self.resetPreferencesBtn = PushButton(L("恢复偏好默认", "Reset Preferences"), dataCard)
        self.resetPreferencesBtn.clicked.connect(self._reset_preferences)
        self.copyDiagnosticsBtn = PushButton(L("复制诊断摘要", "Copy Diagnostics"), dataCard)
        self.copyDiagnosticsBtn.clicked.connect(self._copy_diagnostic_summary)
        dataButtons.addWidget(self.backupSettingsBtn)
        dataButtons.addWidget(self.restoreSettingsBtn)
        dataButtons.addWidget(self.resetPreferencesBtn)
        dataButtons.addWidget(self.copyDiagnosticsBtn)
        dataButtons.addStretch(1)
        dataLay.addLayout(dataButtons)
        root.addWidget(dataCard)

        # 日志
        logcard = SimpleCardWidget(self.view)
        ll = QVBoxLayout(logcard)
        ll.setContentsMargins(20, 16, 20, 16)
        ll.setSpacing(4)
        ll.addWidget(StrongBodyLabel(L("审计日志", "Audit Log"), logcard))
        self.logPathLabel = CaptionLabel(log.file_path, logcard)
        self.logPathLabel.setWordWrap(True)
        ll.addWidget(self.logPathLabel)
        brow = QHBoxLayout()
        self.exportBtn = PushButton(L("导出日志", "Export Log"), logcard)
        self.exportBtn.clicked.connect(self._export)
        self.openBtn = PushButton(L("打开日志目录", "Open Log Folder"), logcard)
        self.openBtn.clicked.connect(self._open_dir)
        brow.addWidget(self.exportBtn)
        brow.addWidget(self.openBtn)
        brow.addStretch(1)
        ll.addLayout(brow)
        root.addWidget(logcard)

        # 关于
        about = SimpleCardWidget(self.view)
        bl = QVBoxLayout(about)
        bl.setContentsMargins(20, 16, 20, 16)
        bl.addWidget(StrongBodyLabel(L("关于", "About"), about))
        bl.addWidget(BodyLabel(f"FlowBench v{APP_VERSION}", about))
        bl.addWidget(CaptionLabel(L("仅用于合法授权的性能测试。", "For legally authorized testing only."), about))
        brow = QHBoxLayout()
        self.disclaimerBtn = PushButton(L("查看免责声明", "View Disclaimer"), about)
        self.disclaimerBtn.clicked.connect(lambda: DisclaimerDialog(self.window()).exec())
        self.updateBtn = PushButton(L("检查更新", "Check for Updates"), about)
        self.updateBtn.clicked.connect(self._check_update)
        brow.addWidget(self.disclaimerBtn)
        brow.addWidget(self.updateBtn)
        brow.addStretch(1)
        bl.addLayout(brow)
        root.addWidget(about)

        # 作者（可点击卡片，整个区域跳转）
        author = ClickableCard(self.view)
        author.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(AUTHOR_URL)))
        aul = QHBoxLayout(author)
        aul.setContentsMargins(20, 16, 20, 16)
        aul.setSpacing(12)
        # 左侧：作者信息
        info_col = QVBoxLayout()
        info_col.setSpacing(2)
        info_col.addWidget(StrongBodyLabel(L("作者", "Author"), author))
        author_name = BodyLabel(AUTHOR_NAME, author)
        author_name_font = QFont()
        author_name_font.setPointSize(12)
        author_name.setFont(author_name_font)
        info_col.addWidget(author_name)
        gh_label = CaptionLabel(L(f"GitHub 主页：{AUTHOR_URL}", f"GitHub: {AUTHOR_URL}"), author)
        gh_label.setWordWrap(True)
        info_col.addWidget(gh_label)
        aul.addLayout(info_col, 1)
        # 右侧：链接图标（自动适配主题色）
        self.linkIcon = IconWidget(FluentIcon.LINK, author)
        self.linkIcon.setFixedSize(24, 24)
        aul.addWidget(self.linkIcon, 0, Qt.AlignVCenter)
        root.addWidget(author)

        root.addStretch(1)

    def _check_update(self):
        self.updateBtn.setEnabled(False)
        self.updateBtn.setText(L("检查中…", "Checking…"))
        from app.services.updater import check_for_updates as run_check
        # The 10 s fallback of an earlier check must not re-enable the button
        # while a later check is still running.
        self._update_check_serial = getattr(self, "_update_check_serial", 0) + 1
        serial = self._update_check_serial

        def _done():
            if serial != self._update_check_serial:
                return
            self.updateBtn.setEnabled(True)
            self.updateBtn.setText(L("检查更新", "Check for Updates"))

        # 使用回调在检查完成后恢复按钮状态，同时设置10秒安全超时
        run_check(parent=self.window(), manual=True, on_finished=_done)
        QTimer.singleShot(10000, _done)

    def _show_preference_save_error(self, setting_name):
        InfoBar.error(
            L("保存失败", "Save Failed"),
            L(f"无法保存设置：{setting_name}：{settings.last_error}",
              f"Could not save setting {setting_name}: {settings.last_error}"),
            parent=self.window(), duration=5000)

    def _save_bool_setting(self, control, name, value):
        if settings.set(name, bool(value)):
            return
        old_value = bool(getattr(settings, name))
        was_blocked = control.blockSignals(True)
        control.setChecked(old_value)
        control.blockSignals(was_blocked)
        self._show_preference_save_error(name)

    def _save_int_setting(self, control, name, value):
        if settings.set(name, int(value)):
            return
        old_value = int(getattr(settings, name))
        was_blocked = control.blockSignals(True)
        control.setValue(old_value)
        control.blockSignals(was_blocked)
        self._show_preference_save_error(name)

    def _theme_changed(self, checked):
        theme = "dark" if checked else "light"
        if not settings.set("theme", theme):
            was_blocked = self.darkSwitch.blockSignals(True)
            self.darkSwitch.setChecked(settings.theme == "dark")
            self.darkSwitch.blockSignals(was_blocked)
            InfoBar.error(
                L("保存失败", "Save Failed"),
                L(f"无法保存主题设置：{settings.last_error}",
                  f"Could not save the theme setting: {settings.last_error}"),
                parent=self.window(), duration=5000)
            return
        setTheme(Theme.DARK if checked else Theme.LIGHT)
        # setTheme() rebuilds the global Fluent stylesheet and some versions
        # reset the accent color while doing so. Reapply the persisted accent
        # after every light/dark switch so appearance settings stay intact.
        setThemeColor(QColor(str(settings.theme_color)))

    def _animation_changed(self, checked):
        """保存动画偏好并立即应用到主窗口。"""
        if not settings.set("animations_enabled", bool(checked)):
            was_blocked = self.animationSwitch.blockSignals(True)
            self.animationSwitch.setChecked(not checked)
            self.animationSwitch.blockSignals(was_blocked)
            InfoBar.error(
                L("保存失败", "Save Failed"),
                L(f"无法保存动画设置：{settings.last_error}",
                  f"Could not save the animation setting: {settings.last_error}"),
                parent=self.window(), duration=5000)
            return
        apply_setting = getattr(
            self.window(), "set_page_animations_enabled", None)
        if callable(apply_setting):
            apply_setting(bool(checked))

    def _lang_changed(self, idx):
        language = {0: "auto", 1: "zh-CN", 2: "en-US"}.get(idx, "auto")
        if not settings.set("language", language):
            was_blocked = self.langCombo.blockSignals(True)
            self.langCombo.setCurrentIndex(
                {"auto": 0, "zh-CN": 1, "en-US": 2}.get(settings.language, 0))
            self.langCombo.blockSignals(was_blocked)
            InfoBar.error(
                L("保存失败", "Save Failed"),
                L(f"无法保存语言设置：{settings.last_error}",
                  f"Could not save the language setting: {settings.last_error}"),
                parent=self.window(), duration=5000)
            return
        InfoBar.success(L("已保存", "Saved"),
                        L("界面语言将在重启后完全生效", "Language fully applies after restart"),
                        parent=self.window())

    def _sync_preference_controls(self):
        """从 settings 同步本页控件，不触发写回信号。"""
        controls = (
            (self.darkSwitch, "setChecked", settings.theme == "dark"),
            (self.animationSwitch, "setChecked",
             bool(settings.animations_enabled)),
            (self.traySwitch, "setChecked", bool(settings.minimize_to_tray)),
            (self.autoUpdateSwitch, "setChecked", bool(settings.auto_check_updates)),
            (self.langCombo, "setCurrentIndex",
             {"auto": 0, "zh-CN": 1, "en-US": 2}.get(settings.language, 0)),
            (self.threadSpin, "setValue", int(settings.default_threads)),
            (self.timeoutSpin, "setValue", int(settings.default_timeout_ms)),
            (self.rateSpin, "setValue", int(settings.default_rate)),
            (self.durationSpin, "setValue", int(settings.default_duration)),
            (self.packetSizeSpin, "setValue", int(settings.default_packet_size)),
        )
        for control, setter, value in controls:
            was_blocked = control.blockSignals(True)
            getattr(control, setter)(value)
            control.blockSignals(was_blocked)
        self.colorPicker._refresh()

    def _apply_preference_theme(self):
        """恢复/重置后立即应用外观偏好。"""
        setTheme(Theme.DARK if settings.theme == "dark" else Theme.LIGHT)
        setThemeColor(QColor(str(settings.theme_color)))
        self.colorPicker._refresh()
        apply_setting = getattr(
            self.window(), "set_page_animations_enabled", None)
        if callable(apply_setting):
            apply_setting(bool(settings.animations_enabled))

    def _show_language_restart_notice(self):
        InfoBar.info(
            L("需要重启", "Restart Required"),
            L("已恢复语言偏好，界面语言将在重启后完全生效。",
              "The language preference was restored and will fully apply after restart."),
            parent=self.window(), duration=5000)

    def _export_settings_backup(self):
        import os
        import time

        default_name = f"flowbench-settings-{time.strftime('%Y%m%d')}.json"
        path, _ = QFileDialog.getSaveFileName(
            self, L("导出设置备份", "Export settings backup"), default_name,
            L("JSON 设置备份 (*.json)", "JSON settings backup (*.json)"))
        if not path:
            return
        if not os.path.splitext(path)[1]:
            path += ".json"
        try:
            count = settings.export_backup(path, APP_VERSION)
        except Exception as e:
            InfoBar.error(
                L("导出失败", "Export Failed"),
                L(f"无法写入设置备份：{e}", f"Could not write the settings backup: {e}"),
                parent=self.window(), duration=6000)
            return
        InfoBar.success(
            L("备份已导出", "Backup Exported"),
            L(f"已写入 {count} 项安全偏好，敏感数据未包含。",
              f"Saved {count} safe preferences; sensitive data was excluded."),
            parent=self.window(), duration=4000)

    def _import_settings_backup(self):
        import os

        path, _ = QFileDialog.getOpenFileName(
            self, L("恢复设置备份", "Restore settings backup"), "",
            L("JSON 设置备份 (*.json);;所有文件 (*.*)",
              "JSON settings backup (*.json);;All files (*.*)"))
        if not path:
            return
        box = MessageBox(
            L("恢复设置备份", "Restore Settings Backup"),
            L("将使用备份中的安全偏好覆盖当前偏好。恢复前会自动创建 settings.json.bak；"
              "授权目标、访问令牌、插件私有数据和测试历史不会改变。是否继续？",
              "Safe preferences in the backup will replace the current preferences. "
              "A settings.json.bak file will be created first; authorized targets, access tokens, "
              "private plugin data and test history will not change. Continue?"),
            self.window())
        if not box.exec():
            return

        old_language = settings.language
        try:
            result = settings.import_backup(path)
        except Exception as e:
            InfoBar.error(
                L("恢复失败", "Restore Failed"),
                L(f"备份无效、版本不兼容或无法写入：{e}",
                  f"The backup is invalid, incompatible, or could not be written: {e}"),
                parent=self.window(), duration=7000)
            return

        self._sync_preference_controls()
        self._apply_preference_theme()
        applied = len(result["applied"])
        ignored = len(result["ignored"])
        backup_name = os.path.basename(result["backup_path"])
        detail = L(
            f"已恢复 {applied} 项偏好；原设置已保存为 {backup_name}。",
            f"Restored {applied} preferences; previous settings were saved as {backup_name}.")
        if ignored:
            detail += L(f" 已安全忽略 {ignored} 个非白名单字段。",
                        f" Safely ignored {ignored} non-allowlisted fields.")
        InfoBar.success(L("设置已恢复", "Settings Restored"), detail,
                        parent=self.window(), duration=5000)
        if old_language != settings.language:
            self._show_language_restart_notice()

    def _reset_preferences(self):
        box = MessageBox(
            L("恢复偏好默认", "Reset Preferences"),
            L("将重置外观、语言、默认测试参数、托盘和更新偏好。"
              "授权状态、授权目标、访问令牌、插件及其私有数据不会被清除。是否继续？",
              "This resets appearance, language, test defaults, tray and update preferences. "
              "Consent, authorized targets, access tokens, plugins and private plugin data will not be cleared. Continue?"),
            self.window())
        if not box.exec():
            return

        old_language = settings.language
        try:
            changed = settings.reset_preferences()
        except Exception as e:
            InfoBar.error(
                L("重置失败", "Reset Failed"),
                L(f"无法保存默认偏好：{e}", f"Could not save default preferences: {e}"),
                parent=self.window(), duration=6000)
            return

        self._sync_preference_controls()
        self._apply_preference_theme()
        InfoBar.success(
            L("偏好已重置", "Preferences Reset"),
            L(f"已恢复 {len(changed)} 项默认偏好，敏感状态保持不变。",
              f"Restored {len(changed)} default preferences; sensitive state was preserved."),
            parent=self.window(), duration=4000)
        if old_language != settings.language:
            self._show_language_restart_notice()

    def _diagnostic_summary(self):
        """生成适合粘贴给维护者的脱敏纯文本摘要。"""
        import os
        import platform
        import sys
        from collections import Counter
        from datetime import datetime

        from PySide6 import __version__ as pyside_version
        from PySide6.QtCore import qVersion

        try:
            from importlib.metadata import version
            fluent_version = version("PySide6-Fluent-Widgets")
        except Exception:
            fluent_version = L("未知", "Unknown")

        try:
            from app.services.plugins import plugin_manager
            records = plugin_manager.records()
            plugin_states = Counter(rec.state for rec in records)
            plugin_total = len(records)
        except Exception:
            plugin_states = Counter()
            plugin_total = 0

        safe = settings.safe_snapshot()
        log_exists = os.path.isfile(log.file_path)
        log_size = os.path.getsize(log.file_path) if log_exists else 0
        crash_path = os.path.join(os.path.dirname(log.file_path), "crash.log")
        crash_exists = os.path.isfile(crash_path)
        crash_size = os.path.getsize(crash_path) if crash_exists else 0
        settings_dir = os.path.dirname(settings.path)
        settings_writable = os.access(settings_dir, os.W_OK)

        yes_no = lambda value: L("是", "Yes") if value else L("否", "No")
        theme_name = L("深色", "Dark") if safe["theme"] == "dark" else L("浅色", "Light")
        language_name = {
            "auto": L("跟随系统", "System"),
            "zh-CN": L("简体中文", "Simplified Chinese"),
            "en-US": "English",
        }.get(safe["language"], safe["language"])
        mode_name = L("打包版本", "Packaged") if getattr(sys, "frozen", False) else L("源码运行", "Source")

        lines = [
            "FlowBench — " + L("脱敏诊断摘要", "Redacted Diagnostic Summary"),
            L("生成时间：", "Generated: ") + datetime.now().astimezone().isoformat(timespec="seconds"),
            L("应用版本：", "App version: ") + f"{APP_VERSION} ({mode_name})",
            L("操作系统：", "OS: ") + " ".join(filter(None, (
                platform.system(), platform.release(), platform.machine()))),
            L("Python：", "Python: ") + platform.python_version(),
            L("PySide6 / Qt：", "PySide6 / Qt: ") + f"{pyside_version} / {qVersion()}",
            L("Fluent Widgets：", "Fluent Widgets: ") + fluent_version,
            L("主题 / 强调色：", "Theme / accent: ") + f"{theme_name} / {safe['theme_color']}",
            L("语言：", "Language: ") + language_name,
            L("页面动画：", "Page animations: ")
            + yes_no(safe["animations_enabled"]),
            L("默认线程 / 超时 / QPS：", "Defaults threads / timeout / QPS: ")
            + f"{safe['default_threads']} / {safe['default_timeout_ms']} ms / {safe['default_rate']}",
            L("默认时长 / 数据包：", "Default duration / packet: ")
            + f"{safe['default_duration']} s / {safe['default_packet_size']} B",
            L("最小化到托盘 / 自动更新：", "Tray minimize / auto update: ")
            + f"{yes_no(safe['minimize_to_tray'])} / {yes_no(safe['auto_check_updates'])}",
            L("设置目录可写：", "Settings directory writable: ") + yes_no(settings_writable),
            L("审计日志：", "Audit log: ")
            + (L(f"可用（{log_size} 字节）", f"Available ({log_size} bytes)")
               if log_exists else L("不存在", "Not found")),
            L("崩溃日志：", "Crash log: ")
            + (L(f"可用（{crash_size} 字节）", f"Available ({crash_size} bytes)")
               if crash_exists else L("不存在", "Not found")),
            L("插件（总数/运行/禁用/错误/未加载）：",
              "Plugins (total/running/disabled/error/unloaded): ")
            + f"{plugin_total}/{plugin_states['loaded']}/{plugin_states['disabled']}/"
              f"{plugin_states['error']}/{plugin_states['unloaded']}",
            L("隐私：已排除令牌、授权目标、测试目标、插件私有数据、搜索历史和本机绝对路径。",
              "Privacy: tokens, authorized/test targets, private plugin data, search history and absolute local paths are excluded."),
        ]
        return "\n".join(lines)

    def _copy_diagnostic_summary(self):
        try:
            summary = self._diagnostic_summary()
            QApplication.clipboard().setText(summary)
        except Exception as e:
            InfoBar.error(
                L("复制失败", "Copy Failed"),
                L(f"无法生成诊断摘要：{e}", f"Could not create the diagnostic summary: {e}"),
                parent=self.window(), duration=6000)
            return
        InfoBar.success(
            L("已复制", "Copied"),
            L("脱敏诊断摘要已复制到剪贴板。", "The redacted diagnostic summary was copied to the clipboard."),
            parent=self.window(), duration=3000)

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, L("导出日志", "Export log"),
                                              "flowbench-audit.log",
                                              L("日志文件 (*.log *.txt)",
                                                "Log files (*.log *.txt)"))
        if not path:
            return
        import os
        if not os.path.splitext(path)[1]:
            path += ".log"
        try:
            n = log.export_text(path)
        except Exception as e:
            InfoBar.error(L("导出失败", "Export Failed"), str(e),
                          parent=self.window(), duration=6000)
            return
        InfoBar.success(L("导出成功", "Exported"),
                        L(f"{n} 条记录", f"{n} entries"), parent=self.window())

    def _open_dir(self):
        import os
        os.startfile(os.path.dirname(log.file_path))
