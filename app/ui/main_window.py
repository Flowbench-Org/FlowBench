"""主窗口：FluentWindow 侧边导航 + Mica 效果。"""
import os
import sys

from PySide6.QtCore import (QAbstractAnimation, QEasingCurve,
                            QParallelAnimationGroup, QPoint,
                            QPropertyAnimation, QSize, Qt, QTimer)
from PySide6.QtGui import QCursor, QGuiApplication, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractButton, QAbstractItemView,
                               QAbstractScrollArea, QAbstractSpinBox, QComboBox,
                               QGraphicsOpacityEffect, QLabel, QLineEdit,
                               QPlainTextEdit, QProgressBar, QSlider,
                               QStackedWidget, QSystemTrayIcon, QTextEdit)
from qfluentwidgets import FluentIcon as FIF
from qfluentwidgets import (Action, FluentWindow, NavigationItemPosition,
                            RoundMenu, SwitchButton)

from app.services.settings import settings
from app.ui.agent_view import AgentView
from app.ui.busy_overlay import BusyOverlay
from app.ui.collab_view import CollabView
from app.ui.dashboard import DashboardView
from app.ui.i18n import L
from app.ui.market_view import MarketView
from app.ui.monitor_view import MonitorView
from app.ui.settings_view import SettingsView
from app.ui.stress_view import StressView
from app.ui import info_bar_fit

# Every InfoBar wraps by pixel width, so long messages stay inside the window.
info_bar_fit.install()

MON_ICON = getattr(FIF, "DIAGNOSTICS", getattr(FIF, "HEART", FIF.DEVELOPER_TOOLS))
PLUGIN_ICON = getattr(FIF, "APPLICATION", FIF.DEVELOPER_TOOLS)
PAGE_EXIT_MS = 150
PAGE_TRANSITION_MS = 520
CONTROL_REVEAL_MS = 560
MAX_STAGGER_SPAN_MS = 700
REVEAL_WATCHDOG_MS = 1200


def _get_icon_path():
    """获取应用图标路径。"""
    if getattr(sys, "_MEIPASS", None):
        return os.path.join(sys._MEIPASS, "app.ico")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "app.ico")


class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FlowBench")
        # Give the dense monitoring and test pages enough horizontal room by
        # default, while keeping a reasonable minimum for smaller laptops.
        self._default_size = QSize(1360, 860)
        self._minimum_size = QSize(1080, 700)
        self.resize(self._default_size)
        self.setMinimumSize(self._minimum_size)
        self._first_show = True
        self._control_reveals = []
        self._nav_reveal = None
        self._pending_reveal_interface = None
        self._reveal_serial = 0
        self._page_animations_enabled = bool(settings.animations_enabled)

        self.dashboard = DashboardView(self)
        self.stress = StressView(self)
        self.collab = CollabView(self)
        self.agentView = AgentView(self)
        self.monitor = MonitorView(self)
        self.market = MarketView(self)
        self.settingsView = SettingsView(self)

        self.init_navigation()
        self.stackedWidget.setAnimationEnabled(
            self._page_animations_enabled)
        self.stackedWidget.view.aniFinished.connect(
            self._on_page_transition_finished)
        self.init_window()
        self._init_tray()
        self._init_shortcuts()
        self._init_plugins()

        # BusyOverlay 创建为子控件，但初始隐藏
        # 使用 QTimer.singleShot 确保在窗口完全构建后再创建
        self._busy_overlay = None

    def addSubInterface(self, interface, icon, text,
                        position=NavigationItemPosition.TOP, *args, **kwargs):
        item = super().addSubInterface(interface, icon, text, position, *args, **kwargs)
        bubbles = getattr(self, "_nav_bubbles", None)
        if bubbles is not None:
            bubbles.attach(item)
        return item

    def init_navigation(self):
        panel = getattr(self.navigationInterface, "panel", None)
        # 保留侧边栏展开/收起按钮（默认已连接 menuButton.clicked -> panel.toggle）。
        # 之前的文字遮挡/错位来自动画中触发的重排，由 expandAni.finished 与
        # resizeEvent 中的防抖逻辑处理，无需再禁用折叠。
        if panel is not None:
            panel.setCollapsible(True)
            panel.setMenuButtonVisible(True)
        # 侧边栏收起成图标时，鼠标移到选项上立刻弹出带尖角的名字气泡。
        # 每个页面在 addSubInterface 里挂上，插件后来加的页面也一样。
        self._nav_bubbles = None
        if panel is not None and hasattr(panel, "displayModeChanged"):
            from app.ui.nav_bubble import NavBubbles
            self._nav_bubbles = NavBubbles(panel)
            menu = getattr(panel, "menuButton", None)
            if menu is not None:
                self._nav_bubbles.attach(menu, menu.toolTip)
        # 展开/收起动画结束后重排一次，保证文字绘制宽度与面板最终宽度一致
        ani = getattr(panel, "expandAni", None) if panel is not None else None
        if ani is not None:
            ani.finished.connect(self._on_nav_expand_animation_finished)
        self.addSubInterface(self.dashboard, FIF.HOME, L("主页", "Home"))
        self.addSubInterface(self.stress, FIF.SPEED_HIGH, L("压力测试", "Stress Test"))
        self.addSubInterface(self.collab, FIF.CONNECT, L("协同测试", "Collaborative"))
        self.addSubInterface(self.agentView, getattr(FIF, "SERVER", FIF.DEVELOPER_TOOLS), L("服务器节点", "Server Agents"))
        self.addSubInterface(self.monitor, MON_ICON, L("监控面板", "Monitor"))
        self.addSubInterface(self.market, PLUGIN_ICON, L("插件", "Plugins"))
        self.addSubInterface(self.settingsView, FIF.SETTING, L("设置", "Settings"),
                             NavigationItemPosition.BOTTOM)

    def init_window(self):
        # Mica is implemented by qframelesswindow through native Windows
        # window events. It can access the HWND while the splash/main window
        # is still being created and has caused process-level access violations
        # on some Windows/PySide6 combinations. Use the stable Fluent fallback
        # background instead; this does not affect the page layout or theme.
        try:
            self.setMicaEffectEnabled(False)
        except Exception:
            pass

    def _ensure_overlay(self):
        """确保 overlay 已创建（首次调用时在窗口显示后创建）。"""
        if self._busy_overlay is None:
            self._busy_overlay = BusyOverlay(self)
            self._busy_overlay.setGeometry(0, 0, self.width(), self.height())
            self._busy_overlay.hide()
        return self._busy_overlay

    def _init_plugins(self):
        """加载插件并监听后续启停（设置页可动态启停/导入）。"""
        from app.services.plugins import plugin_manager
        self._plugin_pages = {}  # pid -> 页面控件
        plugin_manager.loaded.connect(self._on_plugin_loaded)
        plugin_manager.unloaded.connect(self._on_plugin_unloaded)
        plugin_manager.load_all()

    def _on_plugin_loaded(self, plugin):
        """插件加载成功：创建其页面并加入导航。"""
        try:
            widget = plugin.create_widget(self)
        except Exception as e:
            from app.services.logger import log
            log.error(L(f"插件页面创建失败：{plugin.id} — {e}",
                        f"Plugin page creation failed: {plugin.id} — {e}"))
            return
        if widget is None:
            return
        pid = plugin.id
        widget.setObjectName(f"plugin_{pid}")
        self._plugin_pages[pid] = widget
        # 解析插件图标：市场图标 > 插件自定义 > 默认
        from app.services.plugins import plugin_manager, resolve_plugin_icon
        rec = plugin_manager.record(pid)
        rec_path = rec.path if rec else ""
        icon = resolve_plugin_icon(plugin, pid, rec_path) or PLUGIN_ICON
        try:
            self.addSubInterface(widget, icon, plugin.page_title(),
                                 NavigationItemPosition.SCROLL)
        except Exception as e:
            from app.services.logger import log
            log.error(L(f"插件页面注册失败：{pid} — {e}",
                        f"Plugin page registration failed: {pid} — {e}"))
            self._plugin_pages.pop(pid, None)

    def _on_plugin_unloaded(self, pid: str):
        """插件卸载：从导航与堆栈窗口移除其页面。"""
        widget = self._plugin_pages.pop(pid, None)
        if widget is None:
            return
        route_key = widget.objectName()
        try:
            self.navigationInterface.removeWidget(route_key)
        except Exception:
            pass
        try:
            self.stackedWidget.removeWidget(widget)
        except Exception:
            pass
        widget.deleteLater()

    def _init_tray(self):
        """初始化系统托盘。"""
        icon_path = _get_icon_path()
        self.tray_icon = QSystemTrayIcon(self)
        if os.path.exists(icon_path):
            self.tray_icon.setIcon(QIcon(icon_path))
        else:
            self.tray_icon.setIcon(self.windowIcon())
        self.tray_icon.setToolTip("FlowBench")

        # 使用 RoundMenu 自动适配深色/浅色主题。
        # 不走 setContextMenu：Qt 原生定位在托盘靠近任务栏/屏幕边缘时会把
        # 菜单底部挤出屏幕，只露出最下面的几项；改为右键时手动向上弹出。
        self._tray_menu = RoundMenu(parent=self)
        tray_menu = self._tray_menu

        show_action = Action(FIF.VIEW, L("显示主窗口", "Show Window"), self)
        show_action.triggered.connect(self._show_from_tray)
        tray_menu.addAction(show_action)

        tray_menu.addSeparator()

        stress_action = Action(FIF.SPEED_HIGH, L("压力测试", "Stress Test"), self)
        stress_action.triggered.connect(self.go_stress)
        tray_menu.addAction(stress_action)

        monitor_action = Action(MON_ICON, L("监控面板", "Monitor"), self)
        monitor_action.triggered.connect(self.go_monitor)
        tray_menu.addAction(monitor_action)

        market_action = Action(PLUGIN_ICON, L("插件", "Plugins"), self)
        market_action.triggered.connect(self.go_market)
        tray_menu.addAction(market_action)

        settings_action = Action(FIF.SETTING, L("设置", "Settings"), self)
        settings_action.triggered.connect(self.go_settings)
        tray_menu.addAction(settings_action)

        tray_menu.addSeparator()

        quit_action = Action(FIF.CLOSE, L("退出", "Quit"), self)
        quit_action.triggered.connect(self._quit_app)
        tray_menu.addAction(quit_action)

        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _init_shortcuts(self):
        """注册全局页面快捷键，并保存引用避免被 Qt 回收。"""
        # Ctrl+1..6 follow the order of the sidebar.
        bindings = (
            ("Ctrl+1", self.go_dashboard),
            ("Ctrl+2", self.go_stress),
            ("Ctrl+3", self.go_collab),
            ("Ctrl+4", self.go_agents),
            ("Ctrl+5", self.go_monitor),
            ("Ctrl+6", self.go_market),
            ("Ctrl+,", self.go_settings),
        )
        self._page_shortcuts = []
        for sequence, callback in bindings:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ApplicationShortcut)
            shortcut.activated.connect(callback)
            self._page_shortcuts.append(shortcut)

    def _on_tray_activated(self, reason):
        """托盘图标双击显示窗口，右键弹出完整菜单。"""
        if reason == QSystemTrayIcon.DoubleClick:
            self._show_from_tray()
        elif reason == QSystemTrayIcon.Context:
            self._popup_tray_menu()

    def _popup_tray_menu(self):
        """在托盘图标上方弹出完整菜单，并钳位到屏幕可用区域。

        默认整体在光标上方展开（尽量靠上，不被任务栏遮挡）；只有上方
        放不下时才向下弹。水平/垂直都做钳位，保证菜单完整可见。
        """
        menu = self._tray_menu
        # RoundMenu 的 sizeHint() 在未显示前是过期值，先 adjustSize()
        # 迫使内部布局重算，再用 size() 拿到真实尺寸
        menu.adjustSize()
        pos = QCursor.pos()
        screen = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        size = menu.size()
        x = pos.x() - size.width() // 2
        y = pos.y() - size.height()
        if y < avail.top():
            y = pos.y()
        x = max(avail.left(), min(x, avail.right() - size.width() + 1))
        y = max(avail.top(), min(y, avail.bottom() - size.height() + 1))
        menu.popup(QPoint(x, y))

    def _show_from_tray(self):
        """从托盘或最小化恢复窗口并放到最前面：页面不变，最大化的仍是最大化。"""
        # Clearing only the minimized flag keeps a maximized window maximized;
        # showNormal() used to shrink it back to its normal size.
        state = (self.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive
        if not self.isVisible():
            self.show()
        self.setWindowState(state)
        self.raise_()
        self.activateWindow()

    def _quit_app(self):
        """从托盘退出应用。"""
        self.tray_icon.hide()
        from PySide6.QtWidgets import QApplication
        QApplication.quit()

    def closeEvent(self, event):
        """重写关闭事件，根据配置决定最小化到托盘还是退出。"""
        if settings.minimize_to_tray and QSystemTrayIcon.isSystemTrayAvailable():
            event.ignore()
            self.hide()
            QTimer.singleShot(0, self._minimize_to_tray)
        else:
            event.accept()

    def _minimize_to_tray(self):
        """确保托盘图标可用，并在首次最小化时给出提示。"""
        first_icon = not self.tray_icon.isVisible()
        if first_icon:
            self.tray_icon.show()
        if settings.tray_notified or getattr(self, "_tray_hint_scheduled", False):
            return
        self._tray_hint_scheduled = True
        # 等托盘图标真正注册到通知区域：系统溢出提示先弹，我们自己的气泡作兜底。
        QTimer.singleShot(800 if first_icon else 200, self._show_first_tray_hint)

    def _show_first_tray_hint(self):
        """托盘图标注册完成后，永久只弹一次气泡提示。"""
        if settings.tray_notified:
            return
        settings.set("tray_notified", True)
        if not self.tray_icon.isVisible() or not QSystemTrayIcon.supportsMessages():
            return
        self.tray_icon.showMessage(
            "FlowBench",
            L("程序已最小化到托盘，右键托盘图标可退出",
              "Minimized to tray, right-click tray icon to quit"),
            QSystemTrayIcon.Information,
            5000
        )

    def _go_to(self, widget):
        """从普通窗口或托盘状态直达指定页面。"""
        # 页面内按钮/快捷键在窗口已显示时只切页，避免重复切换窗口状态；
        # 真正处于托盘隐藏或最小化状态时才执行恢复流程。
        if not self.isVisible() or self.isMinimized():
            self._show_from_tray()
        self.switchTo(widget)

    def switchTo(self, interface):
        """Switch pages with a visible upward entrance and content reveal."""
        current = self.stackedWidget.currentWidget()
        pending = self._pending_reveal_interface
        if pending is interface or (pending is None and current is interface):
            return

        # Clicking the page that is currently exiting should reverse the
        # transition instead of letting the other page flash into view.
        if pending is not None and current is interface:
            self._stop_page_transition()

        self._clear_control_reveals()
        self._clear_reveal("_nav_reveal")
        self._pending_reveal_interface = interface
        self._reveal_serial += 1
        reveal_serial = self._reveal_serial

        if isinstance(interface, QAbstractScrollArea):
            interface.verticalScrollBar().setValue(0)

        if self._page_animations_enabled:
            # Hide the destination controls before the stacked widget can
            # paint them. Final positions are captured after page layout.
            self._prepare_control_reveals(interface, reveal_serial)
        if self.stackedWidget.isAnimationEnabled():
            self.stackedWidget.view.setCurrentWidget(
                interface, duration=PAGE_TRANSITION_MS)
            delay = PAGE_EXIT_MS - 10
        else:
            self.stackedWidget.view.setCurrentWidget(interface, duration=0)
            self._pending_reveal_interface = None
            delay = 0

        if self._page_animations_enabled:
            QTimer.singleShot(
                delay,
                lambda: self._start_control_reveals(reveal_serial))
            self._animate_navigation_reveal(interface.objectName())

    def set_page_animations_enabled(self, enabled: bool):
        """Immediately enable or disable all main page transition effects."""
        enabled = bool(enabled)
        if not enabled:
            self._stop_page_transition()
            self._reveal_serial += 1
            self._pending_reveal_interface = None
            self._clear_control_reveals()
            self._clear_reveal("_nav_reveal")
        self._page_animations_enabled = enabled
        self.stackedWidget.setAnimationEnabled(enabled)

    def _stop_page_transition(self):
        """Stop the transition across supported Fluent Widgets versions."""
        view = self.stackedWidget.view
        stop_animation = getattr(view, "_stopAnimation", None)
        if callable(stop_animation):
            stop_animation()
            return

        animation = getattr(view, "_ani", None)
        if (animation is None
                or animation.state() != QAbstractAnimation.State.Running):
            return
        next_index = getattr(view, "_nextIndex", None)
        animation.stop()
        try:
            animation.finished.disconnect()
        except (RuntimeError, TypeError):
            pass
        if isinstance(next_index, int) and 0 <= next_index < view.count():
            QStackedWidget.setCurrentIndex(view, next_index)
        view.aniFinished.emit()

    def _on_page_transition_finished(self):
        if self.stackedWidget.currentWidget() is self._pending_reveal_interface:
            self._pending_reveal_interface = None

    def _prepare_control_reveals(self, interface, reveal_serial: int):
        """Synchronously hide destination controls before the page is painted."""
        if reveal_serial != self._reveal_serial:
            return
        targets = self._collect_reveal_targets(interface)
        if not targets:
            return

        for widget in targets:
            if widget.graphicsEffect() is not None:
                continue
            effect = QGraphicsOpacityEffect(widget)
            effect.setOpacity(0.0)
            widget.setGraphicsEffect(effect)

            group = QParallelAnimationGroup(self)
            opacity = QPropertyAnimation(effect, b"opacity", group)
            opacity.setStartValue(0.0)
            opacity.setEndValue(1.0)
            opacity.setDuration(CONTROL_REVEAL_MS)
            opacity.setEasingCurve(QEasingCurve.Type.OutCubic)
            movement = QPropertyAnimation(widget, b"pos", group)
            movement.setDuration(CONTROL_REVEAL_MS)
            movement.setEasingCurve(QEasingCurve.Type.OutCubic)
            group.addAnimation(opacity)
            group.addAnimation(movement)

            state = [widget, effect, group, movement, None, reveal_serial]
            self._control_reveals.append(state)
            group.finished.connect(
                lambda state=state: self._finish_control_reveal(state))

    def _start_control_reveals(self, reveal_serial: int):
        """Animate prepared controls from their freshly laid-out positions."""
        if reveal_serial != self._reveal_serial:
            return
        states = [state for state in self._control_reveals
                  if state[5] == reveal_serial]
        if not states:
            return

        # Capture every final position before moving any control so sibling
        # layouts cannot influence positions captured later in this pass.
        for state in states:
            widget, _effect, _group, movement, _end_pos, _serial = state
            try:
                end_pos = widget.pos()
                start_pos = end_pos + QPoint(0, 12)
                state[4] = end_pos
                movement.setStartValue(start_pos)
                movement.setEndValue(end_pos)
            except RuntimeError:
                self._finish_control_reveal(state)

        states = [state for state in states if state in self._control_reveals]
        stagger = max(28, min(
            85, MAX_STAGGER_SPAN_MS // max(1, len(states) - 1)))
        max_delay = (len(states) - 1) * stagger
        for index, state in enumerate(states):
            try:
                state[0].move(state[3].startValue())
            except RuntimeError:
                self._finish_control_reveal(state)
                continue
            QTimer.singleShot(
                index * stagger,
                lambda state=state: self._start_control_reveal(state))

        # Animation callbacks can be lost when a Qt object is recreated during
        # theme changes or rapid page switches. Always install a watchdog so
        # every control is restored even if a finished() signal is missed.
        QTimer.singleShot(
            max(REVEAL_WATCHDOG_MS, max_delay + CONTROL_REVEAL_MS),
            lambda: self._finish_control_reveals(reveal_serial))

    def _finish_control_reveals(self, reveal_serial: int):
        states = [state for state in self._control_reveals
                  if state[5] == reveal_serial]
        for state in states:
            self._finish_control_reveal(state)

    def _collect_reveal_targets(self, interface):
        """Collect user-visible controls without descending into internals."""
        atomic_types = (
            QAbstractButton, QAbstractItemView, QAbstractSpinBox, QComboBox,
            QLabel, QLineEdit, QPlainTextEdit, QProgressBar, QSlider,
            SwitchButton, QTextEdit,
        )
        targets = []
        seen = set()

        def add(widget):
            if (widget in seen or not widget.isVisibleTo(interface)
                    or widget.objectName().startswith("qt_")):
                return
            seen.add(widget)
            targets.append(widget)

        def walk_layout(layout):
            for index in range(layout.count()):
                item = layout.itemAt(index)
                if item.layout() is not None:
                    walk_layout(item.layout())
                elif item.widget() is not None:
                    walk_widget(item.widget())

        def walk_widget(widget, is_root=False):
            # QStackedWidget marks an unselected page itself as hidden. Walk
            # that root once, while still filtering hidden child tab pages.
            if (widget is None
                    or (not is_root and not widget.isVisibleTo(interface))):
                return
            if isinstance(widget, atomic_types):
                add(widget)
                return
            if isinstance(widget, QStackedWidget):
                walk_widget(widget.currentWidget())
                return
            if isinstance(widget, QAbstractScrollArea):
                content = widget.widget() if hasattr(widget, "widget") else None
                if content is not None and content.layout() is not None:
                    walk_layout(content.layout())
                else:
                    add(widget)
                return
            if widget.layout() is not None:
                walk_layout(widget.layout())
            else:
                add(widget)

        root = interface
        if isinstance(interface, QAbstractScrollArea) and hasattr(interface, "widget"):
            root = interface.widget() or interface
        walk_widget(root, True)
        return targets

    def _start_control_reveal(self, state):
        if (state not in self._control_reveals
                or state[5] != self._reveal_serial):
            return
        try:
            state[2].start()
        except RuntimeError:
            self._finish_control_reveal(state)

    def _finish_control_reveal(self, state):
        if state not in self._control_reveals:
            return
        self._control_reveals.remove(state)
        self._restore_revealed_control(state)

    def _clear_control_reveals(self):
        states, self._control_reveals = self._control_reveals, []
        for state in states:
            self._restore_revealed_control(state)

    @staticmethod
    def _restore_revealed_control(state):
        """Put a control back in place and free its one-shot animation.

        The animation group is parented to the window, so it must be deleted
        explicitly or every page switch leaks a few hundred QObjects. Stop it
        before removing the effect: the effect is deleted with it and a
        running animation would be left without a target.
        """
        widget, effect, group, _movement, end_pos, _serial = state
        try:
            group.stop()
            group.deleteLater()
        except RuntimeError:
            pass
        try:
            if end_pos is not None:
                widget.move(end_pos)
            effect.setOpacity(1.0)
            if widget.graphicsEffect() is effect:
                widget.setGraphicsEffect(None)
        except RuntimeError:
            pass

    def _animate_navigation_reveal(self, route_key: str):
        """Keep navigation items stable while Fluent expands/collapses the rail.

        Applying a second opacity effect to navigation items races the native
        navigation animation and can leave labels with stale geometry after a
        collapse/expand cycle. Page content still uses its normal transition.
        """
        self._clear_reveal("_nav_reveal")

    def _start_reveal(self, widget, state_name: str,
                      start_opacity: float, duration: int):
        if widget.graphicsEffect() is not None:
            return
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(start_opacity)
        widget.setGraphicsEffect(effect)

        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setStartValue(start_opacity)
        animation.setEndValue(1.0)
        animation.setDuration(duration)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        state = (widget, effect, animation)
        setattr(self, state_name, state)
        animation.finished.connect(
            lambda: self._finish_reveal(state_name, state))
        animation.start()

    def _finish_reveal(self, state_name: str, state):
        if getattr(self, state_name, None) is not state:
            return
        setattr(self, state_name, None)
        self._restore_revealed_widget(state)

    def _clear_reveal(self, state_name: str):
        state = getattr(self, state_name, None)
        if state is None:
            return
        setattr(self, state_name, None)
        self._restore_revealed_widget(state)

    @staticmethod
    def _restore_revealed_widget(state):
        widget, effect, animation = state
        try:
            animation.stop()
            animation.deleteLater()
        except RuntimeError:
            pass
        try:
            effect.setOpacity(1.0)
            if widget.graphicsEffect() is effect:
                widget.setGraphicsEffect(None)
        except RuntimeError:
            pass

    def go_dashboard(self):
        self._go_to(self.dashboard)

    def go_stress(self):
        self._go_to(self.stress)

    def go_collab(self):
        self._go_to(self.collab)

    def go_agents(self):
        self._go_to(self.agentView)

    def go_monitor(self):
        self._go_to(self.monitor)

    def go_market(self):
        self._go_to(self.market)

    def go_settings(self):
        self._go_to(self.settingsView)

    def resizeEvent(self, event):
        """窗口大小变化时同步更新 overlay 大小。"""
        super().resizeEvent(event)
        overlay = getattr(self, "_busy_overlay", None)
        if overlay and overlay.isVisible():
            overlay.setGeometry(0, 0, self.width(), self.height())
        # Recalculate navigation item text geometry after the rail animation or
        # a window resize. This prevents clipped/overlapping labels.
        nav = getattr(self, "navigationInterface", None)
        if nav is not None:
            panel = getattr(nav, "panel", None)
            ani = getattr(panel, "expandAni", None) if panel is not None else None
            ani_running = (ani is not None
                           and ani.state() == QAbstractAnimation.State.Running)
            # 展开/收起动画进行中不要触发 panel 重排：布局会覆盖动画中的
            # geometry，使面板停在中间宽度而文字已按展开宽度绘制，形成遮挡。
            if panel is not None and not ani_running:
                panel.updateGeometry()
            nav.update()

    def _on_nav_expand_animation_finished(self):
        """导航动画完成后重排一次，保证文字绘制宽度与面板最终宽度一致。"""
        nav = getattr(self, "navigationInterface", None)
        panel = getattr(nav, "panel", None) if nav is not None else None
        if panel is not None:
            panel.updateGeometry()
            panel.update()

    def showEvent(self, event):
        """窗口首次显示时播放当前页面动画并确保尺寸正确。"""
        super().showEvent(event)
        if self._first_show:
            self._first_show = False
            # One delayed correction is enough. Repeated resizes during the
            # first native paint can trigger qframelesswindow access violations.
            QTimer.singleShot(220, self._ensure_correct_size)

            # The splash screen already provides the startup transition. Do
            # not run a second full-page reveal while Qt is still settling the
            # FluentWindow layout; it causes visible flashing on launch.
            self._clear_control_reveals()
            self._clear_reveal("_nav_reveal")
            self._pending_reveal_interface = None
            self._reveal_serial += 1
        self._ensure_overlay()
    
    def _ensure_correct_size(self):
        """确保窗口尺寸正确（仅在窗口比默认尺寸小时调整，不覆盖用户手动调整）。"""
        if self.isVisible() and not self.isMaximized() and not self.isMinimized():
            current = self.size()
            target = self._default_size
            # 如果当前尺寸比默认小，才调整（避免覆盖用户手动放大）
            if current.width() < target.width() or current.height() < target.height():
                self.resize(target)

    def show_busy(self, text: str = "", sub_text: str = ""):
        """显示全局加载遮罩。"""
        overlay = self._ensure_overlay()
        overlay.show(text, sub_text)

    def hide_busy(self):
        """隐藏全局加载遮罩。"""
        overlay = getattr(self, "_busy_overlay", None)
        if overlay:
            overlay.hide()

    def set_busy_text(self, text: str, sub_text: str = ""):
        """更新加载遮罩的文字。"""
        overlay = getattr(self, "_busy_overlay", None)
        if overlay:
            overlay.set_text(text, sub_text)
