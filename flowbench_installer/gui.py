"""Setup window: a three-step wizard for installs and a compact update window."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QThread, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QStackedWidget,
                               QVBoxLayout, QWidget)
from qfluentwidgets import (BodyLabel, CaptionLabel, CheckBox, FluentIcon,
                            IconWidget, InfoBarIcon, LineEdit, MessageBox,
                            PrimaryPushButton, ProgressBar, PushButton,
                            StrongBodyLabel, SubtitleLabel, Theme, TitleLabel,
                            isDarkTheme, setTheme)

from flowbench_installer import core, winplat
from flowbench_installer.session import SetupSession


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


class InstallerWindow(QWidget):
    """Look shared by the setup and uninstall windows: background, logo
    header, title bar colour, button rows and message boxes."""

    def __init__(self, *, chinese: Optional[bool] = None,
                 resources: Optional[Path] = None, platform=winplat, parent=None):
        super().__init__(parent)
        self.platform = platform
        self.chinese = platform.system_prefers_chinese() if chinese is None else chinese
        self.resources = Path(resources) if resources else None
        self.setObjectName("SetupWindow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        icon = self._resource("app.ico")
        if icon:
            self.setWindowIcon(QIcon(str(icon)))
        self._apply_background()

    def t(self, zh: str, en: str) -> str:
        return zh if self.chinese else en

    def _resource(self, name: str) -> Optional[Path]:
        if self.resources and (self.resources / name).is_file():
            return self.resources / name
        return None

    def _apply_background(self):
        color = "#202020" if isDarkTheme() else "#F7F9FC"
        self.setStyleSheet(f"#SetupWindow {{ background: {color}; }}")

    def _logo(self, size: int) -> QLabel:
        label = QLabel(self)
        name = "app_logo_dark.png" if isDarkTheme() else "app_logo.png"
        path = self._resource(name) or self._resource("app_logo.png")
        if path:
            pix = QPixmap(str(path))
            if not pix.isNull():
                label.setPixmap(pix.scaled(size, size, Qt.KeepAspectRatio,
                                           Qt.SmoothTransformation))
        label.setFixedSize(size, size)
        return label

    def _error_text(self, exc) -> str:
        if isinstance(exc, core.InstallError):
            return exc.text(self.chinese)
        return f"{type(exc).__name__}: {exc}"

    def showEvent(self, event):
        super().showEvent(event)
        try:
            self.platform.set_dark_title_bar(int(self.winId()), isDarkTheme())
        except Exception:
            pass

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

    def _build_frame(self, title: str, subtitle: str) -> QStackedWidget:
        """Logo + title header above a page stack; returns the stack."""
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 28, 32, 24)
        root.setSpacing(0)

        header = QHBoxLayout()
        header.setSpacing(16)
        header.addWidget(self._logo(56), 0, Qt.AlignTop)
        head_text = QVBoxLayout()
        head_text.setSpacing(2)
        self.titleLabel = TitleLabel(title, self)
        self.subtitleLabel = CaptionLabel(subtitle, self)
        head_text.addWidget(self.titleLabel)
        head_text.addWidget(self.subtitleLabel)
        header.addLayout(head_text, 1)
        root.addLayout(header)
        root.addSpacing(24)

        self.pages = QStackedWidget(self)
        self.pages.setAttribute(Qt.WA_TranslucentBackground, True)
        root.addWidget(self.pages, 1)
        return self.pages

    def _page(self):
        page = QWidget(self.pages)
        page.setAttribute(Qt.WA_TranslucentBackground, True)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        return page, layout

    def _button_row(self, layout, *buttons):
        row = QHBoxLayout()
        row.addStretch(1)
        for button in buttons:
            button.setMinimumWidth(104)
            row.addWidget(button)
        layout.addStretch(1)
        layout.addLayout(row)

    def _set_elided(self, label, text: str):
        metrics = label.fontMetrics()
        label.setText(metrics.elidedText(text, Qt.ElideMiddle, max(200, label.width())))


class SetupWindow(InstallerWindow):
    """Interactive wizard (``update=False``) or silent-ish updater window."""

    finished = Signal(bool)

    def __init__(self, payload: Path, info: dict, *, update: bool = False,
                 target: Optional[Path] = None, wait_pid: int = 0,
                 chinese: Optional[bool] = None, resources: Optional[Path] = None,
                 platform=winplat, wait_timeout: float = 60.0, parent=None):
        super().__init__(chinese=chinese, resources=resources, platform=platform,
                         parent=parent)
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

        registered = platform.registered_install_dir()
        self.target = Path(target or registered or platform.default_install_dir())
        self.setWindowTitle(self.t(f"FlowBench {self.version} 安装", f"FlowBench {self.version} Setup"))

        if update:
            self._build_update_ui()
            self.setFixedSize(460, 200)
            QTimer.singleShot(0, self.start_update)
        else:
            self._build_wizard_ui()
            self.setFixedSize(600, 430)

    # -- helpers ------------------------------------------------------------

    @property
    def version(self) -> str:
        return self.info.get("version", "")

    # -- wizard UI ------------------------------------------------------------

    def _build_wizard_ui(self):
        self._build_frame(f"FlowBench {self.version}",
                          self.t("网络压力测试工具 · 安装向导", "Network stress testing tool · Setup"))
        self.welcomePage = self._build_welcome_page()
        self.progressPage = self._build_progress_page()
        self.donePage = self._build_done_page()
        for page in (self.welcomePage, self.progressPage, self.donePage):
            self.pages.addWidget(page)
        self._refresh_welcome()

    def _build_welcome_page(self):
        page, layout = self._page()
        layout.addWidget(StrongBodyLabel(self.t("安装位置", "Install location"), page))
        row = QHBoxLayout()
        row.setSpacing(8)
        self.pathEdit = LineEdit(page)
        self.pathEdit.setText(str(self.target))
        self.pathEdit.setClearButtonEnabled(False)
        self.pathEdit.textChanged.connect(self._refresh_welcome)
        self.browseButton = PushButton(self.t("更改…", "Browse…"), page)
        self.browseButton.clicked.connect(self._browse)
        row.addWidget(self.pathEdit, 1)
        row.addWidget(self.browseButton)
        layout.addLayout(row)
        self.desktopCheck = CheckBox(self.t("创建桌面快捷方式", "Create a desktop shortcut"), page)
        layout.addSpacing(4)
        layout.addWidget(self.desktopCheck)
        self.noteLabel = CaptionLabel("", page)
        self.noteLabel.setWordWrap(True)
        layout.addSpacing(6)
        layout.addWidget(self.noteLabel)
        self.cancelButton = PushButton(self.t("取消", "Cancel"), page)
        self.cancelButton.clicked.connect(self.close)
        self.installButton = PrimaryPushButton(self.t("安装", "Install"), page)
        self.installButton.clicked.connect(self.start_install)
        self._button_row(layout, self.cancelButton, self.installButton)
        return page

    def _build_progress_page(self):
        page, layout = self._page()
        self.progressTitle = SubtitleLabel(
            self.t(f"正在安装 FlowBench {self.version}…", f"Installing FlowBench {self.version}…"), page)
        self.progressBar = ProgressBar(page)
        self.progressBar.setRange(0, 100)
        self.progressDetail = CaptionLabel("", page)
        self.progressDetail.setWordWrap(False)
        self.progressError = BodyLabel("", page)
        self.progressError.setWordWrap(True)
        self.progressError.hide()
        layout.addWidget(self.progressTitle)
        layout.addSpacing(8)
        layout.addWidget(self.progressBar)
        layout.addWidget(self.progressDetail)
        layout.addWidget(self.progressError)
        self.stopButton = PushButton(self.t("取消", "Cancel"), page)
        self.stopButton.clicked.connect(self._cancel_install)
        self.retryButton = PrimaryPushButton(self.t("重试", "Retry"), page)
        self.retryButton.clicked.connect(self._retry)
        self.retryButton.hide()
        self._button_row(layout, self.stopButton, self.retryButton)
        return page

    def _build_done_page(self):
        page, layout = self._page()
        row = QHBoxLayout()
        row.setSpacing(10)
        self.doneIcon = IconWidget(InfoBarIcon.SUCCESS, page)
        self.doneIcon.setFixedSize(22, 22)
        self.doneTitle = SubtitleLabel(self.t("FlowBench 已安装完成", "FlowBench is installed"), page)
        row.addWidget(self.doneIcon)
        row.addWidget(self.doneTitle, 1)
        layout.addLayout(row)
        self.doneDetail = CaptionLabel("", page)
        self.doneDetail.setWordWrap(True)
        layout.addWidget(self.doneDetail)
        self.doneWarnings = BodyLabel("", page)
        self.doneWarnings.setWordWrap(True)
        self.doneWarnings.hide()
        layout.addWidget(self.doneWarnings)
        layout.addSpacing(6)
        self.launchCheck = CheckBox(self.t("立即运行 FlowBench", "Launch FlowBench now"), page)
        self.launchCheck.setChecked(True)
        layout.addWidget(self.launchCheck)
        self.finishButton = PrimaryPushButton(self.t("完成", "Finish"), page)
        self.finishButton.clicked.connect(self._finish)
        self._button_row(layout, self.finishButton)
        return page

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

    def _refresh_welcome(self):
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
        legacy = [e for e in self.platform.find_legacy_installs()
                  if e.get("location")]
        for entry in legacy:
            notes.append(self.t(
                f"检测到旧版 FlowBench {entry.get('version') or ''}（{entry['location']}），"
                "安装完成后会自动卸载它，Windows 可能会请求一次管理员确认。",
                f"An older FlowBench {entry.get('version') or ''} was found in {entry['location']}. "
                "It will be removed after installing; Windows may ask for administrator approval once."))
        self.noteLabel.setText("\n".join(notes))
        self.noteLabel.setVisible(bool(notes))

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
        self.pages.setCurrentWidget(self.progressPage)
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
        self.progressError.hide()
        self.retryButton.hide()
        if not self.update_mode:
            self.stopButton.setText(self.t("取消", "Cancel"))
            self.stopButton.setEnabled(stage)
        self.worker = StepWorker(work, self)
        self.worker.progressed.connect(self._on_progress)
        self.worker.succeeded.connect(self._on_success)
        self.worker.failed.connect(self._on_failure)
        self.worker.start()

    def _on_progress(self, pct: int, detail: str):
        self.progressBar.setValue(pct)
        if pct >= SetupSession.STAGE_END:
            if self._staging and not self.update_mode:
                self.stopButton.setEnabled(False)
            self._staging = False
            text = self.t("正在完成安装…", "Finishing…")
            if detail and pct >= 97:
                text = self.t(f"正在卸载旧版：{detail}", f"Removing the old version: {detail}")
        else:
            text = self.t("正在解压：", "Extracting: ") + detail if detail else ""
        label = self.progressDetail if not self.update_mode else self.updateDetail
        self._set_elided(label, text)

    def _cancel_install(self):
        if self.worker and self.busy:
            self.worker.cancel()
            self.stopButton.setEnabled(False)
        elif not self.busy:
            self.close()

    def _on_success(self, warnings):
        self.busy = False
        self.succeeded = True
        if self.update_mode:
            self._update_done()
            return
        self.doneDetail.setText(self.t(f"安装位置：{self.target}", f"Installed to {self.target}"))
        lines = [self._warning_text(w) for w in warnings or []]
        self.doneWarnings.setText("\n".join(lines))
        self.doneWarnings.setVisible(bool(lines))
        self.pages.setCurrentWidget(self.donePage)

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
            self.pages.setCurrentWidget(self.welcomePage)
            self._refresh_welcome()
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
        self._show_error(self._error_text(exc), staged=self.session.job.staging is not None)

    def _show_error(self, text: str, staged: bool):
        self._retry_stage = not staged
        if self.update_mode:
            self.updateDetail.setText(text)
            self.updateTitle.setText(self.t("更新没有完成", "The update did not finish"))
            self.updateRetry.show()
            self.updateClose.show()
            return
        self.progressTitle.setText(self.t("安装没有完成", "Setup did not finish"))
        self.progressError.setText(text)
        self.progressError.show()
        self.retryButton.show()
        self.stopButton.setText(self.t("关闭", "Close"))
        self.stopButton.setEnabled(True)

    def _retry(self):
        if not self.update_mode:
            self.progressTitle.setText(
                self.t(f"正在安装 FlowBench {self.version}…", f"Installing FlowBench {self.version}…"))
            if not self._wait_until_closed():
                return
        else:
            self.updateTitle.setText(self.t(f"正在更新到 FlowBench {self.version}…",
                                            f"Updating to FlowBench {self.version}…"))
            self.updateRetry.hide()
            self.updateClose.hide()
        self._run_steps(stage=self._retry_stage)

    def _finish(self):
        if self.launchCheck.isChecked():
            try:
                self.session.launch_app()
            except Exception:
                pass
        self.finished.emit(True)
        self.close()

    # -- update UI -------------------------------------------------------------

    def _build_update_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(10)
        header = QHBoxLayout()
        header.setSpacing(14)
        header.addWidget(self._logo(40), 0, Qt.AlignTop)
        self.updateTitle = SubtitleLabel(self.t(f"正在更新到 FlowBench {self.version}…",
                                                f"Updating to FlowBench {self.version}…"), self)
        header.addWidget(self.updateTitle, 1)
        root.addLayout(header)
        self.progressBar = ProgressBar(self)
        self.progressBar.setRange(0, 100)
        root.addWidget(self.progressBar)
        self.updateDetail = CaptionLabel("", self)
        self.updateDetail.setWordWrap(True)
        root.addWidget(self.updateDetail)
        row = QHBoxLayout()
        row.addStretch(1)
        self.updateClose = PushButton(self.t("关闭", "Close"), self)
        self.updateClose.clicked.connect(self.close)
        self.updateRetry = PrimaryPushButton(self.t("重试", "Retry"), self)
        self.updateRetry.clicked.connect(self._retry)
        for button in (self.updateClose, self.updateRetry):
            button.setMinimumWidth(96)
            button.hide()
            row.addWidget(button)
        root.addStretch(1)
        root.addLayout(row)

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

        self.updateDetail.setText(self.t("正在等待 FlowBench 退出…", "Waiting for FlowBench to exit…"))
        self.busy = True
        self._staging = True
        self.worker = StepWorker(work, self)
        self.worker.progressed.connect(self._on_progress)
        self.worker.succeeded.connect(self._on_success)
        self.worker.failed.connect(self._on_failure)
        self.worker.start()

    def _update_done(self):
        self.progressBar.setValue(100)
        self.updateDetail.setText(self.t("更新完成，正在重新打开 FlowBench…",
                                         "Update complete. Reopening FlowBench…"))
        try:
            self.session.launch_app()
        except Exception as exc:
            self._show_error(self._error_text(exc), staged=False)
            self.succeeded = False
            return
        self.finished.emit(True)
        QTimer.singleShot(600, self.close)

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
