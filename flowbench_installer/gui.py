"""Setup window: a four-step wizard for installs and a compact update window.

Both share the look of the uninstall window (``InstallerWindow``): a frameless
window over an animated background, a globe button that switches between
中文 and English, and a button that switches between light and dark mode.

Wizard:  welcome (the logo forms) -> install location -> progress -> done
Update:  one compact window that installs on its own and reopens FlowBench
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable, List, Optional

from PySide6.QtCore import (QEvent, QPoint, QPointF, QRect, QRectF, QSize, QThread, Qt,
                            QTimer, Signal)
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QRegion
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (Action, BodyLabel, CaptionLabel, CheckableMenu, CheckBox,
                            FluentIcon, HyperlinkButton, IconWidget, InfoBarIcon,
                            LineEdit, MenuIndicatorType, MessageBox, PrimaryPushButton,
                            PushButton, SubtitleLabel, Theme, ToolTipFilter,
                            ToolTipPosition, TransparentPushButton, TransparentToolButton,
                            getFont, isDarkTheme, qconfig, setTheme)
from qframelesswindow import FramelessWindow, TitleBar, TitleBarButton

from flowbench_installer import core, visuals, winplat
from flowbench_installer.session import SetupSession

SECONDARY = (QColor(0, 0, 0, 158), QColor(255, 255, 255, 178))
ERROR = (QColor("#c42b1c"), QColor("#ff99a4"))
WARNING = (QColor("#9d5d00"), QColor("#fce100"))


class StepWorker(QThread):
    """Run ``fn(progress, cancelled)`` off the GUI thread."""

    progressed = Signal(int, str)
    succeeded = Signal(object)
    failed = Signal(str, object)     # kind: in_use | cancelled | error

    def __init__(self, fn: Callable, parent=None):
        super().__init__(parent)
        self._fn = fn
        self._cancel = False
        self._last = (-1, 0.0)

    def cancel(self):
        self._cancel = True

    def _progress(self, pct: int, text: str):
        now = time.monotonic()
        last_pct, last_time = self._last
        if pct != last_pct or now - last_time > 0.1:
            self._last = (pct, now)
            self.progressed.emit(int(pct), text)

    def run(self):
        try:
            result = self._fn(self._progress, lambda: self._cancel)
        except core.AppInUseError as exc:
            self.failed.emit("in_use", exc)
        except core.InstallCancelled as exc:
            self.failed.emit("cancelled", exc)
        except core.InstallError as exc:
            self.failed.emit("error", exc)
        except Exception as exc:            # unexpected: still report it
            self.failed.emit("error", exc)
        else:
            self.succeeded.emit(result)


def motion_enabled(platform) -> bool:
    """Follow Windows' "Animation effects" setting."""
    check = getattr(platform, "animations_enabled", None)
    try:
        return bool(check()) if check else True
    except Exception:
        return True


def size_text(size: int) -> str:
    if size >= 1024 ** 3:
        return f"{size / 1024 ** 3:.1f} GB"
    return f"{max(1, round(size / 1024 ** 2))} MB"


def same_path(a, b) -> bool:
    try:
        return (os.path.normcase(os.path.abspath(str(a)))
                == os.path.normcase(os.path.abspath(str(b))))
    except (TypeError, ValueError):
        return False


def label(cls, parent, size: Optional[int] = None, weight=QFont.Normal, colors=None,
          centered=False, wrap=False):
    widget = cls(parent)
    if size is not None:
        widget.setFont(getFont(size, weight))
    if colors is not None:
        widget.setTextColor(*colors)
    if centered:
        widget.setAlignment(Qt.AlignCenter)
    widget.setWordWrap(wrap)
    return widget


class PlainTitleButton(TitleBarButton):
    """Minimise or close, drawn with a couple of lines (the stock close
    button renders an SVG on every repaint) and centred at any height."""

    def __init__(self, glyph: str, parent=None):
        super().__init__(parent)
        self.glyph = glyph

    def paintEvent(self, event):
        painter = QPainter(self)
        colour, background = self._getColors()
        painter.setPen(Qt.NoPen)
        painter.setBrush(background)
        painter.drawRect(self.rect())
        painter.setRenderHint(QPainter.Antialiasing, self.glyph == "close")
        pen = QPen(colour, 1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        c = QPointF(self.width() / 2, self.height() / 2)
        if self.glyph == "close":
            painter.drawLine(c + QPointF(-5, -5), c + QPointF(5, 5))
            painter.drawLine(c + QPointF(5, -5), c + QPointF(-5, 5))
        else:
            y = round(c.y())
            painter.drawLine(round(c.x() - 5), y, round(c.x() + 5), y)
        painter.end()


class InstallerTitleBar(TitleBar):
    """Logo and title on the left; language, theme, minimise and close on
    the right.  The window is a fixed size, so there is no maximise."""

    HEIGHT = 44

    def __init__(self, window):
        super().__init__(window)
        self.setFixedHeight(self.HEIGHT)
        self.maxBtn.hide()
        self.setDoubleClickEnabled(False)
        layout = self.hBoxLayout
        for name, glyph, action in (("minBtn", "min", window.showMinimized),
                                    ("closeBtn", "close", window.close)):
            stock = getattr(self, name)
            index = layout.indexOf(stock)
            layout.removeWidget(stock)
            stock.hide()
            stock.deleteLater()
            button = PlainTitleButton(glyph, self)
            button.clicked.connect(action)
            layout.insertWidget(index, button, 0, Qt.AlignRight)
            setattr(self, name, button)
        for button in (self.minBtn, self.maxBtn, self.closeBtn):
            button.setFixedSize(46, self.HEIGHT)
        self.logo = visuals.LogoMark(window.art, 18, self)
        self.titleLabel = label(CaptionLabel, self, 12, colors=SECONDARY)
        layout.insertSpacing(0, 16)
        layout.insertWidget(1, self.logo, 0, Qt.AlignVCenter)
        layout.insertSpacing(2, 9)
        layout.insertWidget(3, self.titleLabel, 0, Qt.AlignVCenter)
        self.languageButton = TransparentToolButton(FluentIcon.GLOBE, self)
        self.themeButton = TransparentToolButton(FluentIcon.QUIET_HOURS, self)
        index = layout.indexOf(self.minBtn)
        for offset, button in enumerate((self.languageButton, self.themeButton)):
            button.setFixedSize(36, 32)
            button.setIconSize(QSize(16, 16))
            button.installEventFilter(ToolTipFilter(button, 350, ToolTipPosition.BOTTOM))
            layout.insertWidget(index + offset, button, 0, Qt.AlignVCenter)
        layout.insertSpacing(index + 2, 8)
        self.apply_theme()

    def apply_theme(self):
        dark = isDarkTheme()
        fg = QColor(255, 255, 255) if dark else QColor(0, 0, 0)
        for button in (self.minBtn, self.maxBtn):
            button.setNormalColor(fg)
            button.setHoverColor(fg)
            button.setPressedColor(fg)
            button.setHoverBackgroundColor(QColor(255, 255, 255, 21) if dark else QColor(0, 0, 0, 20))
            button.setPressedBackgroundColor(QColor(255, 255, 255, 40) if dark else QColor(0, 0, 0, 40))
        self.closeBtn.setNormalColor(fg)
        self.closeBtn.setHoverColor(QColor(255, 255, 255))
        self.closeBtn.setPressedColor(QColor(255, 255, 255))
        self.closeBtn.setHoverBackgroundColor(QColor(196, 43, 28))
        self.closeBtn.setPressedBackgroundColor(QColor(200, 64, 49))
        self.themeButton.setIcon(FluentIcon.BRIGHTNESS if dark else FluentIcon.QUIET_HOURS)


class InstallerWindow(FramelessWindow):
    """Look shared by the setup, update and uninstall windows: the animated
    background, the title bar with its language and theme buttons, a page
    stack, message boxes, and texts that follow the chosen language."""

    languageChanged = Signal(bool)

    def __init__(self, *, chinese: Optional[bool] = None, resources: Optional[Path] = None,
                 platform=winplat, motion: Optional[bool] = None, parent=None):
        super().__init__(parent)
        self.platform = platform
        self.chinese = platform.system_prefers_chinese() if chinese is None else bool(chinese)
        self.resources = Path(resources) if resources else None
        self.motion = motion_enabled(platform) if motion is None else bool(motion)
        self._texts = []
        self.art = visuals.LogoArt(self._resource("app_logo.png"),
                                   self._resource("app_logo_dark.png"))
        self.background = visuals.FlowBackground(self.motion)
        self._animated: List[QWidget] = []
        self.clock = visuals.FrameClock(self, self.motion, self._dirty_region)
        self.setObjectName("SetupWindow")
        self.setTitleBar(InstallerTitleBar(self))
        self.setResizeEnabled(False)
        icon = self._resource("app.ico")
        if icon:
            self.setWindowIcon(QIcon(str(icon)))
        self.titleBar.languageButton.clicked.connect(self.show_language_menu)
        self.titleBar.themeButton.clicked.connect(self.toggle_theme)
        self.titleBar.languageButton.setToolTip("语言 · Language")
        self.titleBar.languageButton.setAccessibleName("语言 · Language")
        self._overlay = visuals.FadeOverlay(self)
        self._shown_once = False
        qconfig.themeChanged.connect(self._on_theme_changed)

    # -- text ---------------------------------------------------------------

    def t(self, zh: str, en: str) -> str:
        return zh if self.chinese else en

    def bilingual(self, widget, zh: str, en: str, setter: str = "setText"):
        """Give ``widget`` a text that follows the language switch."""
        self._texts.append((widget, setter, zh, en))
        getattr(widget, setter)(zh if self.chinese else en)
        return widget

    def set_language(self, chinese: bool):
        chinese = bool(chinese)
        if chinese == self.chinese:
            return
        self._crossfade()
        self.chinese = chinese
        for widget, setter, zh, en in self._texts:
            getattr(widget, setter)(zh if chinese else en)
        self.retranslate()
        self.languageChanged.emit(chinese)

    def retranslate(self):
        """Texts that depend on the state; subclasses add theirs."""
        dark = isDarkTheme()
        tip = self.t("切换到浅色模式", "Switch to light mode") if dark else \
            self.t("切换到深色模式", "Switch to dark mode")
        self.titleBar.themeButton.setToolTip(tip)
        self.titleBar.themeButton.setAccessibleName(tip)

    def show_language_menu(self):
        menu = CheckableMenu(parent=self, indicatorType=MenuIndicatorType.CHECK)
        for chinese, name in ((True, "简体中文"), (False, "English")):
            action = Action(name, self)
            action.setCheckable(True)
            action.setChecked(chinese == self.chinese)
            action.triggered.connect(lambda _checked=False, c=chinese: self.set_language(c))
            menu.addAction(action)
        self._language_menu = menu
        button = self.titleBar.languageButton
        width = menu.sizeHint().width()
        menu.exec(button.mapToGlobal(QPoint(button.width() - width + 12, button.height())))

    # -- theme --------------------------------------------------------------

    def toggle_theme(self):
        self._crossfade()
        setTheme(Theme.LIGHT if isDarkTheme() else Theme.DARK)

    def _on_theme_changed(self, *_args):
        self.titleBar.apply_theme()
        self.retranslate()
        self.update()

    def _crossfade(self):
        """Fade the old look out over the new one."""
        if not self.motion or not self.isVisible():
            return
        self._overlay.start(self.grab())
        self.clock.boost(visuals.FadeOverlay.DURATION + 0.1)

    # -- frame --------------------------------------------------------------

    def animated(self, widget):
        """Repaint ``widget`` on every frame while it is shown and moving."""
        self._animated.append(widget)
        return widget

    def _dirty_region(self) -> QRegion:
        if self._overlay.active():
            return QRegion(self.rect())
        region = QRegion(self.background.moving_rect(QRectF(self.rect())).toAlignedRect())
        for widget in self._animated:
            if widget.isVisible() and widget.is_animating():
                region += QRect(widget.mapTo(self, QPoint(0, 0)), widget.size())
        return region

    def _resource(self, name: str) -> Optional[Path]:
        if self.resources and (self.resources / name).is_file():
            return self.resources / name
        return None

    def _body(self) -> QVBoxLayout:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, self.titleBar.height(), 0, 0)
        root.setSpacing(0)
        return root

    def _build_pages(self) -> visuals.PageStack:
        self.pages = visuals.PageStack(self, self.motion)
        self._body().addWidget(self.pages, 1)
        return self.pages

    def _page(self, margins=(44, 16, 44, 24)):
        page = QWidget(self.pages)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(*margins)
        layout.setSpacing(0)
        return page, layout

    def _button_row(self, layout, *buttons, leading=None, centered=False):
        row = QHBoxLayout()
        row.setSpacing(10)
        if leading is not None:
            row.addWidget(leading)
        row.addStretch(1)
        for button in buttons:
            button.setMinimumWidth(112)
            button.setFixedHeight(34)
            row.addWidget(button)
        if centered:
            row.addStretch(1)
        layout.addLayout(row)
        return row

    def _error_text(self, exc) -> str:
        if isinstance(exc, core.InstallError):
            return exc.text(self.chinese)
        return f"{type(exc).__name__}: {exc}"

    def _set_elided(self, widget, text: str):
        metrics = widget.fontMetrics()
        widget.setText(metrics.elidedText(text, Qt.ElideMiddle, max(200, widget.width())))
        widget.setToolTip(text if widget.text() != text else "")

    def paintEvent(self, event):
        painter = QPainter(self)
        self.background.paint(painter, QRectF(self.rect()), isDarkTheme(),
                              self.devicePixelRatioF())
        painter.end()

    def showEvent(self, event):
        super().showEvent(event)
        self.clock.start()
        if not self._shown_once:
            self._shown_once = True
            self.first_shown()

    def first_shown(self):
        """Start the intro animations (subclasses)."""

    def hideEvent(self, event):
        super().hideEvent(event)
        self.clock.stop()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            if self.isMinimized():
                self.clock.stop()
            elif self.isVisible():
                self.clock.start()

    # -- prompts ------------------------------------------------------------

    def ask_retry(self, title: str, text: str) -> bool:
        """Modal Retry/Cancel prompt (tests replace this)."""
        box = MessageBox(title, text, self)
        box.yesButton.setText(self.t("重试", "Retry"))
        box.cancelButton.setText(self.t("取消", "Cancel"))
        return bool(box.exec())

    def _warn(self, title, text):
        box = MessageBox(title, text, self)
        box.cancelButton.hide()
        box.yesButton.setText(self.t("确定", "OK"))
        box.exec()


class SetupWindow(InstallerWindow):
    """Interactive wizard (``update=False``) or the in-app updater's window."""

    finished = Signal(bool)
    WIZARD_SIZE = QSize(720, 480)
    UPDATE_SIZE = QSize(560, 268)

    def __init__(self, payload: Path, info: dict, *, update: bool = False,
                 target: Optional[Path] = None, wait_pid: int = 0,
                 chinese: Optional[bool] = None, resources: Optional[Path] = None,
                 platform=winplat, wait_timeout: float = 60.0,
                 motion: Optional[bool] = None, parent=None):
        super().__init__(chinese=chinese, resources=resources, platform=platform,
                         motion=motion, parent=parent)
        self.payload = Path(payload)
        self.info = info
        self.update_mode = update
        self.wait_pid = int(wait_pid or 0)
        self.wait_timeout = wait_timeout
        self.session: Optional[SetupSession] = None
        self.worker: Optional[StepWorker] = None
        self.busy = False
        self.succeeded = False
        self._close_after_cancel = False
        self._staging = False
        self._retry_stage = True
        self._legacy = None
        self.space_low = False
        self.phase = "updating" if update else "welcome"
        self._progress = (0, "")
        self._error = None
        self._warnings: List[tuple] = []

        registered = platform.registered_install_dir()
        self.default_target = Path(registered or platform.default_install_dir())
        self.target = Path(target or registered or platform.default_install_dir())

        if update:
            self._build_update_ui()
            self.setFixedSize(self.UPDATE_SIZE)
            QTimer.singleShot(0, self.start_update)
        else:
            self._build_wizard_ui()
            self.setFixedSize(self.WIZARD_SIZE)
        self.retranslate()

    # -- helpers ------------------------------------------------------------

    @property
    def version(self) -> str:
        return self.info.get("version", "")

    def retranslate(self):
        super().retranslate()
        if self.update_mode:
            self.setWindowTitle(self.t(f"FlowBench {self.version} 更新",
                                       f"FlowBench {self.version} Update"))
            self.titleBar.titleLabel.setText(self.t("FlowBench 更新", "FlowBench Update"))
            self._render_update()
            return
        self.setWindowTitle(self.t(f"FlowBench {self.version} 安装", f"FlowBench {self.version} Setup"))
        self.titleBar.titleLabel.setText(self.t("FlowBench 安装程序", "FlowBench Setup"))
        self._refresh_location()
        self._render_progress()
        self._render_done()

    # -- wizard UI ------------------------------------------------------------

    def _build_wizard_ui(self):
        self._build_pages()
        self.welcomePage = self._build_welcome_page()
        self.locationPage = self._build_location_page()
        self.progressPage = self._build_progress_page()
        self.donePage = self._build_done_page()
        for page in (self.welcomePage, self.locationPage, self.progressPage, self.donePage):
            self.pages.addWidget(page)

    def _build_welcome_page(self):
        page, layout = self._page(margins=(40, 0, 40, 34))
        layout.addStretch(3)
        self.heroLogo = self.animated(
            visuals.AnimatedLogo(self.art, 112, QSize(420, 168), self.motion, page))
        layout.addWidget(self.heroLogo, 0, Qt.AlignHCenter)
        self.wordmark = self.animated(visuals.Wordmark(42, self.motion, page))
        layout.addWidget(self.wordmark, 0, Qt.AlignHCenter)
        self.taglineLabel = label(BodyLabel, page, 15, colors=SECONDARY, centered=True)
        self.bilingual(self.taglineLabel, "网络压力测试工具", "Network stress testing tool")
        layout.addWidget(self.taglineLabel, 0, Qt.AlignHCenter)
        layout.addSpacing(14)
        self.versionPill = visuals.Pill(f"v{self.version}", page)
        layout.addWidget(self.versionPill, 0, Qt.AlignHCenter)
        layout.addStretch(2)
        self.startButton = PrimaryPushButton(page)
        self.startButton.setFixedSize(220, 40)
        self.bilingual(self.startButton, "开始安装", "Get started")
        self.startButton.clicked.connect(self.show_location)
        layout.addWidget(self.startButton, 0, Qt.AlignHCenter)
        return page

    def _build_location_page(self):
        page, layout = self._page(margins=(48, 22, 48, 26))
        self.locationTitle = label(SubtitleLabel, page, 24, QFont.DemiBold)
        self.bilingual(self.locationTitle, "选择安装位置", "Choose where to install")
        layout.addWidget(self.locationTitle)
        layout.addSpacing(6)
        self.locationHint = label(BodyLabel, page, 14, colors=SECONDARY, wrap=True)
        self.bilingual(self.locationHint,
                       "FlowBench 会安装到下面的文件夹。可以直接用默认位置，也可以换一个文件夹。",
                       "FlowBench will be installed in the folder below. "
                       "Keep the default, or choose another folder.")
        layout.addWidget(self.locationHint)
        layout.addSpacing(18)

        card = visuals.GlassCard(page)
        inner = QVBoxLayout(card)
        inner.setContentsMargins(16, 14, 16, 10)
        inner.setSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(10)
        folder = IconWidget(FluentIcon.FOLDER, card)
        folder.setFixedSize(18, 18)
        self.pathEdit = LineEdit(card)
        self.pathEdit.setText(str(self.target))
        self.pathEdit.setClearButtonEnabled(False)
        self.pathEdit.textChanged.connect(self._refresh_location)
        self.browseButton = PushButton(card)
        self.bilingual(self.browseButton, "更改…", "Browse…")
        self.browseButton.clicked.connect(self._browse)
        row.addWidget(folder)
        row.addWidget(self.pathEdit, 1)
        row.addWidget(self.browseButton)
        inner.addLayout(row)
        info = QHBoxLayout()
        info.setContentsMargins(28, 0, 0, 0)
        self.spaceLabel = label(CaptionLabel, card, 12, colors=SECONDARY)
        self.defaultButton = HyperlinkButton(card)
        self.bilingual(self.defaultButton, "恢复默认位置", "Use the default folder")
        self.defaultButton.clicked.connect(self._use_default_target)
        info.addWidget(self.spaceLabel, 1)
        info.addWidget(self.defaultButton)
        inner.addLayout(info)
        layout.addWidget(card)

        layout.addSpacing(16)
        self.desktopCheck = CheckBox(page)
        self.bilingual(self.desktopCheck, "创建桌面快捷方式", "Create a desktop shortcut")
        layout.addWidget(self.desktopCheck)
        layout.addSpacing(12)
        note = QHBoxLayout()
        note.setSpacing(8)
        self.noteIcon = IconWidget(InfoBarIcon.INFORMATION, page)
        self.noteIcon.setFixedSize(16, 16)
        self.noteLabel = label(CaptionLabel, page, 12, colors=SECONDARY, wrap=True)
        note.addWidget(self.noteIcon, 0, Qt.AlignTop)
        note.addWidget(self.noteLabel, 1)
        layout.addLayout(note)
        layout.addStretch(1)

        self.backButton = TransparentPushButton(FluentIcon.LEFT_ARROW, "", page)
        self.bilingual(self.backButton, "上一步", "Back")
        self.backButton.clicked.connect(self.show_welcome)
        self.cancelButton = PushButton(page)
        self.bilingual(self.cancelButton, "取消", "Cancel")
        self.cancelButton.clicked.connect(self.close)
        self.installButton = PrimaryPushButton(page)
        self.installButton.clicked.connect(self.start_install)
        self._button_row(layout, self.cancelButton, self.installButton, leading=self.backButton)
        self.backButton.setMinimumWidth(0)
        return page

    def _build_progress_page(self):
        page, layout = self._page(margins=(64, 0, 64, 26))
        layout.addStretch(2)
        self.progressLogo = self.animated(
            visuals.AnimatedLogo(self.art, 100, QSize(360, 150), self.motion, page))
        self.progressLogo.set_liquid("fill", 0.0, immediate=True)
        layout.addWidget(self.progressLogo, 0, Qt.AlignHCenter)
        layout.addSpacing(4)
        self.progressTitle = label(SubtitleLabel, page, 20, QFont.DemiBold, centered=True)
        layout.addWidget(self.progressTitle)
        layout.addSpacing(18)
        self.progressBar = self.animated(visuals.FlowProgressBar(page, self.motion))
        self.progressBar.setRange(0, 100)
        layout.addWidget(self.progressBar)
        layout.addSpacing(8)
        info = QHBoxLayout()
        self.progressDetail = label(CaptionLabel, page, 12, colors=SECONDARY)
        self.percentLabel = label(CaptionLabel, page, 12, QFont.DemiBold, colors=SECONDARY)
        self.percentLabel.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.percentLabel.setMinimumWidth(40)
        info.addWidget(self.progressDetail, 1)
        info.addWidget(self.percentLabel)
        layout.addLayout(info)
        layout.addSpacing(6)
        self.progressError = label(BodyLabel, page, 13, colors=ERROR, wrap=True)
        self.progressError.hide()
        layout.addWidget(self.progressError)
        layout.addStretch(3)
        self.stopButton = PushButton(page)
        self.stopButton.clicked.connect(self._cancel_install)
        self.retryButton = PrimaryPushButton(page)
        self.bilingual(self.retryButton, "重试", "Retry")
        self.retryButton.clicked.connect(self._retry)
        self.retryButton.hide()
        self._button_row(layout, self.stopButton, self.retryButton)
        return page

    def _build_done_page(self):
        page, layout = self._page(margins=(56, 0, 56, 30))
        layout.addStretch(2)
        self.doneLogo = self.animated(
            visuals.AnimatedLogo(self.art, 104, QSize(360, 156), self.motion, page))
        layout.addWidget(self.doneLogo, 0, Qt.AlignHCenter)
        layout.addSpacing(6)
        self.doneTitle = label(SubtitleLabel, page, 24, QFont.DemiBold, centered=True)
        self.bilingual(self.doneTitle, "FlowBench 已安装完成", "FlowBench is installed")
        layout.addWidget(self.doneTitle)
        layout.addSpacing(8)
        self.doneDetail = label(BodyLabel, page, 13, colors=SECONDARY, centered=True, wrap=True)
        layout.addWidget(self.doneDetail)
        layout.addSpacing(6)
        self.doneWarnings = label(CaptionLabel, page, 12, colors=WARNING, centered=True, wrap=True)
        self.doneWarnings.hide()
        layout.addWidget(self.doneWarnings)
        layout.addStretch(3)
        self.finishButton = PushButton(page)
        self.bilingual(self.finishButton, "完成", "Finish")
        self.finishButton.clicked.connect(self._finish)
        self.openButton = PrimaryPushButton(page)
        self.bilingual(self.openButton, "打开 FlowBench", "Open FlowBench")
        self.openButton.clicked.connect(self._open_app)
        self._button_row(layout, self.finishButton, self.openButton, centered=True)
        self.openButton.setMinimumWidth(150)
        return page

    def first_shown(self):
        if self.update_mode or not self.motion:
            return
        self.heroLogo.play_intro(0.12)
        self.wordmark.play(1.0)
        visuals.fade_in(self.taglineLabel, 1.3)
        visuals.fade_in(self.versionPill, 1.45)
        visuals.fade_in(self.startButton, 1.6)
        self.clock.boost(2.6)

    def _finish_intro(self):
        self.heroLogo.skip_intro()
        self.wordmark.finish()
        for widget in (self.taglineLabel, self.versionPill, self.startButton):
            visuals.finish_fade(widget)

    # -- location page --------------------------------------------------------

    def show_location(self):
        self._finish_intro()
        self.phase = "location"
        self._refresh_location()
        self.pages.setCurrentWidget(self.locationPage)
        self.clock.boost(0.6)

    def show_welcome(self):
        self.phase = "welcome"
        self.pages.setCurrentWidget(self.welcomePage, back=True)
        self.clock.boost(0.6)

    def chosen_target(self) -> Path:
        return Path(self.pathEdit.text().strip().strip('"'))

    def _browse(self):
        start = str(self.chosen_target().parent)
        chosen = QFileDialog.getExistingDirectory(
            self, self.t("选择安装位置", "Choose install location"), start)
        if chosen:
            path = Path(chosen)
            if path.name.lower() != core.APP_NAME.lower():
                path = path / core.APP_NAME
            self.pathEdit.setText(str(path))

    def _use_default_target(self):
        self.pathEdit.setText(str(self.default_target))

    def _legacy_installs(self):
        if self._legacy is None:
            self._legacy = [entry for entry in self.platform.find_legacy_installs()
                            if entry.get("location")]
        return self._legacy

    def _refresh_location(self):
        if self.update_mode:
            return
        target = self.chosen_target()
        existing = core.read_marker(target) if target.is_dir() else {}
        installed = existing.get("version") or ""
        present = bool(installed) or (target / core.APP_EXE).is_file()
        notes = []
        if present:
            self.installButton.setText(self.t("重新安装", "Reinstall") if installed == self.version
                                       else self.t("更新", "Update"))
            name = f"FlowBench {installed}".strip()
            notes.append(self.t(f"这里已安装 {name}，将替换为 {self.version}，你的设置会保留。",
                                f"{name} is installed here and will be replaced "
                                f"by {self.version}. Your settings are kept."))
        else:
            self.installButton.setText(self.t("安装", "Install"))
        if not getattr(self, "_desktop_set", False):
            self._desktop_set = True
            links = self.platform.shortcut_paths()
            self.desktopCheck.setChecked(Path(links["desktop"]).exists() or not present)
        for entry in self._legacy_installs():
            notes.append(self.t(
                f"检测到旧版 FlowBench {entry.get('version') or ''}（{entry['location']}），"
                "安装完成后会自动卸载它，Windows 可能会请求一次管理员确认。",
                f"An older FlowBench {entry.get('version') or ''} was found in {entry['location']}. "
                "It will be removed after installing; Windows may ask for administrator approval once."))
        self.noteLabel.setText("\n".join(notes))
        self.noteLabel.setVisible(bool(notes))
        self.noteIcon.setVisible(bool(notes))
        self.defaultButton.setVisible(not same_path(target, self.default_target))
        self._refresh_space(target)

    def _refresh_space(self, target: Path):
        need = int(self.info.get("unpacked_bytes") or 0)
        free = core.free_space(target)
        low = bool(need) and free is not None and free < need * 1.05 + 32 * 1024 ** 2
        if low:
            text = self.t(f"磁盘空间不足：需要约 {size_text(need)}，可用 {size_text(free)}",
                          f"Not enough space: needs about {size_text(need)}, "
                          f"{size_text(free)} free")
        elif need and free is not None:
            text = self.t(f"需要约 {size_text(need)}，可用 {size_text(free)}",
                          f"Needs about {size_text(need)} · {size_text(free)} free")
        elif need:
            text = self.t(f"需要约 {size_text(need)}", f"Needs about {size_text(need)}")
        elif free is not None:
            text = self.t(f"可用 {size_text(free)}", f"{size_text(free)} free")
        else:
            text = ""
        self.spaceLabel.setText(text)
        self.spaceLabel.setTextColor(*(ERROR if low else SECONDARY))
        self.space_low = low

    # -- wizard flow ---------------------------------------------------------

    def start_install(self):
        target = self.chosen_target()
        try:
            core.check_target(target)
        except core.InstallError as exc:
            self._warn(self.t("无法安装到这里", "Cannot install here"), self._error_text(exc))
            return
        self.target = target
        self.session = SetupSession(self.payload, self.info, target,
                                    desktop_shortcut=self.desktopCheck.isChecked(),
                                    platform=self.platform)
        if not self._wait_until_closed():
            return
        self.phase = "installing"
        self._progress = (0, "")
        self._error = None
        self.progressBar.resume()
        self.progressBar.setValue(0)
        self.progressLogo.set_liquid("fill", 0.0, immediate=True)
        self.pages.setCurrentWidget(self.progressPage)
        self.clock.boost(0.6)
        self._run_steps(stage=True)

    def _wait_until_closed(self) -> bool:
        while self.session.blocking_pids():
            if not self.ask_retry(
                    self.t("FlowBench 正在运行", "FlowBench is running"),
                    self.t("请先退出 FlowBench（右键托盘图标 → 退出），然后点“重试”。",
                           "Quit FlowBench first (right-click the tray icon → Quit), then click Retry.")):
                return False
        return True

    def _run_steps(self, stage: bool):
        session = self.session

        def work(progress, cancelled):
            if stage:
                session.stage(progress, cancelled)
            session.activate(progress)
            return session.finalize(progress)

        self.busy = True
        self._staging = stage
        self._error = None
        self.progressBar.resume()
        if not self.update_mode:
            self.stopButton.setEnabled(stage)
            self._render_progress()
        self.worker = StepWorker(work, self)
        self.worker.progressed.connect(self._on_progress)
        self.worker.succeeded.connect(self._on_success)
        self.worker.failed.connect(self._on_failure)
        self.worker.start()

    def _progress_text(self) -> str:
        pct, detail = self._progress
        if self.update_mode and self.busy and pct == 0 and not detail and self.wait_pid:
            return self.t("正在等待 FlowBench 退出…", "Waiting for FlowBench to exit…")
        if pct >= SetupSession.STAGE_END:
            if detail and pct >= 97:
                return self.t(f"正在卸载旧版：{detail}", f"Removing the old version: {detail}")
            return self.t("正在完成安装…", "Finishing…")
        if detail:
            return self.t("正在解压：", "Extracting: ") + detail
        return ""

    def _on_progress(self, pct: int, detail: str):
        self._progress = (pct, detail)
        self.progressBar.setValue(pct)
        self.percentLabel.setText(f"{pct}%")
        logo = self.updateLogo if self.update_mode else self.progressLogo
        logo.set_liquid("fill", pct / 100.0)
        if pct >= SetupSession.STAGE_END:
            if self._staging and not self.update_mode:
                self.stopButton.setEnabled(False)
            self._staging = False
        label_ = self.updateDetail if self.update_mode else self.progressDetail
        self._set_elided(label_, self._progress_text())

    def _render_progress(self):
        if self.update_mode:
            return
        failed = self.phase == "failed"
        if failed:
            self.progressTitle.setText(self.t("安装没有完成", "Setup did not finish"))
            self.progressError.setText(self._error_text(self._error) if self._error else "")
            self.progressDetail.setText("")
            self.stopButton.setText(self.t("关闭", "Close"))
        else:
            self.progressTitle.setText(self.t(f"正在安装 FlowBench {self.version}…",
                                              f"Installing FlowBench {self.version}…"))
            self.stopButton.setText(self.t("取消", "Cancel"))
            self._set_elided(self.progressDetail, self._progress_text())
        self.progressError.setVisible(failed)
        self.retryButton.setVisible(failed)
        self.percentLabel.setText("" if failed else f"{self._progress[0]}%")

    def _cancel_install(self):
        if self.worker and self.busy:
            self.worker.cancel()
            self.stopButton.setEnabled(False)
        elif not self.busy:
            self.close()

    def _on_success(self, warnings):
        self.busy = False
        self.succeeded = True
        self._warnings = list(warnings or [])
        self.progressBar.setValue(100)
        if self.update_mode:
            self._update_done()
            return
        self.phase = "done"
        self._render_done()
        self.doneLogo.show_badge()
        self.pages.setCurrentWidget(self.donePage)
        self.clock.boost(1.4)

    def _render_done(self):
        if self.update_mode:
            return
        self.doneDetail.setText(self.t(f"安装位置：{self.target}", f"Installed to {self.target}"))
        lines = [self._warning_text(w) for w in self._warnings]
        self.doneWarnings.setText("\n".join(lines))
        self.doneWarnings.setVisible(bool(lines))

    def _warning_text(self, warning) -> str:
        kind, subject, detail = warning
        if kind == "legacy":
            return self.t(f"旧版（{subject}）没有卸载成功，可以稍后在“设置 → 应用”里手动卸载。",
                          f"The old version in {subject} was not removed; "
                          "uninstall it from Settings → Apps.")
        if kind == "shortcut":
            return self.t(f"快捷方式创建失败：{subject}", f"Could not create the shortcut {subject}")
        return self.t("没能写入“应用和功能”里的卸载信息。",
                      "Could not register FlowBench in Settings → Apps.")

    def _on_failure(self, kind: str, exc):
        self.busy = False
        if kind == "cancelled":
            if self.session:
                self.session.discard()
            if self._close_after_cancel:
                self.close()
                return
            self.phase = "location"
            self._refresh_location()
            self.pages.setCurrentWidget(self.locationPage, back=True)
            return
        if kind == "in_use" and not self.update_mode:
            # Always ask: the folder can be locked by something we cannot see
            # (an Explorer window, an antivirus scan), so never retry silently.
            if self.ask_retry(
                    self.t("安装位置正在被使用", "The install folder is in use"),
                    self.t("请退出 FlowBench，并关闭打开了这个文件夹的资源管理器窗口，然后点“重试”。",
                           "Quit FlowBench and close any Explorer window showing this folder, "
                           "then click Retry.")):
                self._run_steps(stage=False)
                return
        self._show_error(exc, staged=self.session.job.staging is not None)

    def _show_error(self, exc, staged: bool):
        self._retry_stage = not staged
        self._error = exc
        self.progressBar.error()
        if self.update_mode:
            self.phase = "update_failed"
            self._render_update()
            return
        self.phase = "failed"
        self.stopButton.setEnabled(True)
        self._render_progress()

    def _retry(self):
        if not self.update_mode:
            if not self._wait_until_closed():
                return
            self.phase = "installing"
            self._render_progress()
        else:
            self.phase = "updating"
            self._render_update()
        self._run_steps(stage=self._retry_stage)

    def _finish(self):
        self.finished.emit(True)
        self.close()

    def _open_app(self):
        try:
            self.session.launch_app()
        except Exception:
            pass
        self.finished.emit(True)
        self.close()

    # -- update UI -------------------------------------------------------------

    def _build_update_ui(self):
        body = QWidget(self)
        row = QHBoxLayout(body)
        row.setContentsMargins(22, 0, 30, 22)
        row.setSpacing(16)
        self.updateLogo = self.animated(
            visuals.AnimatedLogo(self.art, 84, QSize(132, 150), self.motion, body))
        self.updateLogo.set_liquid("fill", 0.0, immediate=True)
        row.addWidget(self.updateLogo, 0, Qt.AlignVCenter)
        column = QVBoxLayout()
        column.setSpacing(0)
        column.addStretch(1)
        self.updateTitle = label(SubtitleLabel, body, 18, QFont.DemiBold, wrap=True)
        column.addWidget(self.updateTitle)
        column.addSpacing(14)
        self.progressBar = self.animated(visuals.FlowProgressBar(body, self.motion))
        self.progressBar.setRange(0, 100)
        column.addWidget(self.progressBar)
        column.addSpacing(8)
        info = QHBoxLayout()
        self.updateDetail = label(CaptionLabel, body, 12, colors=SECONDARY, wrap=True)
        self.percentLabel = label(CaptionLabel, body, 12, QFont.DemiBold, colors=SECONDARY)
        self.percentLabel.setAlignment(Qt.AlignRight | Qt.AlignTop)
        self.percentLabel.setMinimumWidth(40)
        info.addWidget(self.updateDetail, 1)
        info.addWidget(self.percentLabel, 0, Qt.AlignTop)
        column.addLayout(info)
        column.addStretch(1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.updateClose = PushButton(body)
        self.bilingual(self.updateClose, "关闭", "Close")
        self.updateClose.clicked.connect(self.close)
        self.updateRetry = PrimaryPushButton(body)
        self.bilingual(self.updateRetry, "重试", "Retry")
        self.updateRetry.clicked.connect(self._retry)
        for button in (self.updateClose, self.updateRetry):
            button.setMinimumWidth(100)
            button.setFixedHeight(32)
            button.hide()
            buttons.addWidget(button)
        column.addLayout(buttons)
        row.addLayout(column, 1)
        self._body().addWidget(body, 1)

    def _render_update(self):
        if not self.update_mode:
            return
        failed = self.phase == "update_failed"
        if failed:
            self.updateTitle.setText(self.t("更新没有完成", "The update did not finish"))
            self.updateDetail.setText(self._error_text(self._error) if self._error else "")
        elif self.phase == "update_done":
            self.updateTitle.setText(self.t("更新完成", "Update complete"))
            self.updateDetail.setText(self.t("正在重新打开 FlowBench…", "Reopening FlowBench…"))
        else:
            self.updateTitle.setText(self.t(f"正在更新到 FlowBench {self.version}…",
                                            f"Updating to FlowBench {self.version}…"))
            self._set_elided(self.updateDetail, self._progress_text())
        self.updateDetail.setTextColor(*(ERROR if failed else SECONDARY))
        self.updateRetry.setVisible(failed)
        self.updateClose.setVisible(failed)
        self.percentLabel.setText("" if failed else f"{self._progress[0]}%")

    def start_update(self):
        self.session = SetupSession(self.payload, self.info, self.target,
                                    desktop_shortcut=None, update=True,
                                    platform=self.platform)
        pid, timeout, platform = self.wait_pid, self.wait_timeout, self.platform
        session = self.session

        def work(progress, cancelled):
            if pid:
                progress(0, "")
                if not platform.wait_for_exit(pid, timeout):
                    raise core.AppInUseError("FlowBench did not exit.",
                                             "FlowBench 没有退出，请手动退出后点“重试”。")
            session.stage(progress, cancelled)
            session.activate(progress)
            return session.finalize(progress)

        self.busy = True
        self._staging = True
        self.phase = "updating"
        self._render_update()
        self.worker = StepWorker(work, self)
        self.worker.progressed.connect(self._on_progress)
        self.worker.succeeded.connect(self._on_success)
        self.worker.failed.connect(self._on_failure)
        self.worker.start()

    def _update_done(self):
        self.progressBar.setValue(100)
        self._progress = (100, "")
        try:
            self.session.launch_app()
        except Exception as exc:
            self.succeeded = False
            self._show_error(exc, staged=False)
            return
        self.phase = "update_done"
        self._render_update()
        self.updateLogo.show_badge()
        self.clock.boost(1.2)
        self.finished.emit(True)
        QTimer.singleShot(1100 if self.motion else 600, self.close)

    # -- closing ---------------------------------------------------------------

    def closeEvent(self, event):
        if self.busy:
            if self._staging and self.worker:
                # Cancel extraction, then close once the worker has cleaned up.
                self._close_after_cancel = True
                self.worker.cancel()
            event.ignore()
            return
        if self.session and not self.succeeded:
            self.session.discard()
        if self.update_mode and not self.succeeded:
            # Do not leave the user without FlowBench after a failed update.
            exe = self.target / core.APP_EXE
            if exe.is_file() and not self.platform.running_instances([exe]):
                try:
                    self.platform.launch_detached(exe, (), self.target)
                except Exception:
                    pass
        event.accept()
