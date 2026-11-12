"""Uninstall window: the same look as the setup wizard, in three steps
(confirm -> progress -> done).  The logo drains as the files go."""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout
from qfluentwidgets import (BodyLabel, CaptionLabel, CheckBox, PrimaryPushButton,
                            PushButton, SubtitleLabel)

from flowbench_installer import core, visuals, winplat
from flowbench_installer.gui import ERROR, SECONDARY, WARNING, InstallerWindow, StepWorker, label
from flowbench_installer.session import UninstallSession


class UninstallWindow(InstallerWindow):
    finished = Signal(bool)
    SIZE = QSize(720, 480)

    def __init__(self, target: Path, *, parent_pid: int = 0,
                 data_dir: Optional[Path] = None, chinese: Optional[bool] = None,
                 resources: Optional[Path] = None, platform=winplat,
                 wait_timeout: float = 30.0, motion: Optional[bool] = None, parent=None):
        super().__init__(chinese=chinese, resources=resources, platform=platform,
                         motion=motion, parent=parent)
        self.target = Path(target)
        self.data_dir = Path(data_dir) if data_dir else core.user_data_dir()
        self.parent_pid = int(parent_pid or 0)
        self.wait_timeout = wait_timeout
        self.session: Optional[UninstallSession] = None
        self.worker: Optional[StepWorker] = None
        self.busy = False
        self.succeeded = False
        self.version = str(core.read_marker(self.target).get("version") or "")
        self.phase = "confirm"
        self._progress = (0, "")
        self._error = None
        self._warnings: List[tuple] = []

        self._build_pages()
        self.confirmPage = self._build_confirm_page()
        self.progressPage = self._build_progress_page()
        self.donePage = self._build_done_page()
        for page in (self.confirmPage, self.progressPage, self.donePage):
            self.pages.addWidget(page)
        self.setFixedSize(self.SIZE)
        self.retranslate()

    # -- pages ---------------------------------------------------------------

    def _build_confirm_page(self):
        page, layout = self._page(margins=(52, 6, 52, 26))
        layout.addStretch(2)
        top = QHBoxLayout()
        top.setSpacing(20)
        self.confirmLogo = self.animated(
            visuals.AnimatedLogo(self.art, 92, QSize(150, 150), self.motion, page))
        top.addWidget(self.confirmLogo, 0, Qt.AlignVCenter)
        column = QVBoxLayout()
        column.setSpacing(6)
        column.addStretch(1)
        self.titleLabel = label(SubtitleLabel, page, 26, QFont.DemiBold)
        self.bilingual(self.titleLabel, "卸载 FlowBench", "Uninstall FlowBench")
        column.addWidget(self.titleLabel)
        self.subtitleLabel = label(BodyLabel, page, 14, colors=SECONDARY)
        column.addWidget(self.subtitleLabel)
        self.locationLabel = label(CaptionLabel, page, 12, colors=SECONDARY, wrap=True)
        column.addWidget(self.locationLabel)
        column.addStretch(1)
        top.addLayout(column, 1)
        layout.addLayout(top)
        layout.addSpacing(14)

        card = visuals.GlassCard(page)
        inner = QVBoxLayout(card)
        inner.setContentsMargins(16, 14, 16, 14)
        inner.setSpacing(6)
        self.removeDataCheck = CheckBox(card)
        self.bilingual(self.removeDataCheck, "同时删除我的设置、插件和日志",
                       "Also delete my settings, plugins and logs")
        self.removeDataCheck.stateChanged.connect(self._on_remove_data_toggled)
        inner.addWidget(self.removeDataCheck)
        self.dataNote = label(CaptionLabel, card, 12, colors=SECONDARY, wrap=True)
        self.dataNote.setContentsMargins(28, 0, 0, 0)     # line up with the box's text
        inner.addWidget(self.dataNote)
        layout.addWidget(card)
        layout.addStretch(3)

        self.cancelButton = PushButton(page)
        self.bilingual(self.cancelButton, "取消", "Cancel")
        self.cancelButton.clicked.connect(self.close)
        self.uninstallButton = PrimaryPushButton(page)
        self.bilingual(self.uninstallButton, "卸载", "Uninstall")
        self.uninstallButton.clicked.connect(self.start_uninstall)
        self._button_row(layout, self.cancelButton, self.uninstallButton)
        return page

    def _build_progress_page(self):
        page, layout = self._page(margins=(64, 0, 64, 26))
        layout.addStretch(2)
        self.progressLogo = self.animated(
            visuals.AnimatedLogo(self.art, 100, QSize(360, 150), self.motion, page))
        self.progressLogo.set_liquid("drain", 0.0, immediate=True)
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
        self.closeButton = PushButton(page)
        self.bilingual(self.closeButton, "关闭", "Close")
        self.closeButton.clicked.connect(self.close)
        self.retryButton = PrimaryPushButton(page)
        self.bilingual(self.retryButton, "重试", "Retry")
        self.retryButton.clicked.connect(self.start_uninstall)
        self._button_row(layout, self.closeButton, self.retryButton)
        self.closeButton.hide()
        self.retryButton.hide()
        return page

    def _build_done_page(self):
        page, layout = self._page(margins=(56, 0, 56, 30))
        layout.addStretch(2)
        self.doneLogo = self.animated(
            visuals.AnimatedLogo(self.art, 104, QSize(360, 156), self.motion, page))
        layout.addWidget(self.doneLogo, 0, Qt.AlignHCenter)
        layout.addSpacing(6)
        self.doneTitle = label(SubtitleLabel, page, 24, QFont.DemiBold, centered=True)
        self.bilingual(self.doneTitle, "FlowBench 已卸载", "FlowBench has been uninstalled")
        layout.addWidget(self.doneTitle)
        layout.addSpacing(8)
        self.doneDetail = label(BodyLabel, page, 13, colors=SECONDARY, centered=True, wrap=True)
        layout.addWidget(self.doneDetail)
        layout.addSpacing(6)
        self.doneWarnings = label(CaptionLabel, page, 12, colors=WARNING, centered=True, wrap=True)
        self.doneWarnings.hide()
        layout.addWidget(self.doneWarnings)
        layout.addStretch(3)
        self.finishButton = PrimaryPushButton(page)
        self.bilingual(self.finishButton, "完成", "Finish")
        self.finishButton.clicked.connect(self._finish)
        self._button_row(layout, self.finishButton, centered=True)
        return page

    def first_shown(self):
        if self.motion:
            self.confirmLogo.play_intro(0.1)
            self.clock.boost(2.2)

    # -- text ----------------------------------------------------------------

    def retranslate(self):
        super().retranslate()
        self.setWindowTitle(self.t("卸载 FlowBench", "Uninstall FlowBench"))
        self.titleBar.titleLabel.setText(self.t("FlowBench 卸载程序", "FlowBench Uninstaller"))
        if self.version:
            self.subtitleLabel.setText(self.t(f"版本 {self.version} · 网络压力测试工具",
                                              f"Version {self.version} · Network stress testing tool"))
        else:
            self.subtitleLabel.setText(self.t("网络压力测试工具", "Network stress testing tool"))
        self.locationLabel.setText(self.t(f"安装位置：{self.target}", f"Installed in {self.target}"))
        self._refresh_data_note()
        self._render_progress()
        self._render_done()

    def _on_remove_data_toggled(self, _state=None):
        self._refresh_data_note()

    def _refresh_data_note(self):
        # The folder goes on its own line: a path has no spaces to wrap at.
        if self.removeDataCheck.isChecked():
            text = self.t("将删除下面的文件夹，包括已授权的目标、测试配置和已安装的插件，无法恢复：",
                          "This deletes the folder below, including authorized targets, "
                          "test settings and installed plugins. It cannot be undone:")
            self.dataNote.setTextColor(*ERROR)
        else:
            text = self.t("不勾选时会保留，重新安装后继续使用：",
                          "If unchecked, they are kept and used again if you reinstall:")
            self.dataNote.setTextColor(*SECONDARY)
        self.dataNote.setText(f"{text}\n{self.data_dir}")

    def _progress_text(self) -> str:
        pct, detail = self._progress
        if pct < UninstallSession.FILES_END:
            return self.t("正在删除：", "Removing: ") + detail if detail else ""
        if pct < 94:
            return self.t("正在删除快捷方式和卸载信息…", "Removing shortcuts and the Apps entry…")
        if pct < 100:
            return self.t("正在删除设置和日志…", "Deleting settings and logs…")
        return ""

    def _render_progress(self):
        failed = self.phase == "failed"
        if failed:
            self.progressTitle.setText(self.t("卸载没有完成", "Uninstall did not finish"))
            self.progressError.setText(self._error_text(self._error) if self._error else "")
            self.progressDetail.setText("")
        else:
            self.progressTitle.setText(self.t("正在卸载 FlowBench…", "Uninstalling FlowBench…"))
            self._set_elided(self.progressDetail, self._progress_text())
        self.progressError.setVisible(failed)
        self.closeButton.setVisible(failed)
        self.retryButton.setVisible(failed)
        self.percentLabel.setText("" if failed else f"{self._progress[0]}%")

    def _render_done(self):
        if self.session is not None and self.session.remove_user_data:
            detail = self.t("你的设置、插件和日志也已删除。",
                            "Your settings, plugins and logs were deleted too.")
        else:
            detail = self.t(f"你的设置、插件和日志保留在：\n{self.data_dir}",
                            f"Your settings, plugins and logs are kept in:\n{self.data_dir}")
        self.doneDetail.setText(detail)
        lines = [self._warning_text(w) for w in self._warnings]
        self.doneWarnings.setText("\n".join(lines))
        self.doneWarnings.setVisible(bool(lines))

    def _warning_text(self, warning) -> str:
        kind, subject, _detail = warning
        if kind == "user_data":
            return self.t(f"有些设置或日志没能删除，可以手动删除 {subject}。",
                          f"Some settings or logs could not be deleted; remove {subject} by hand.")
        return self.t("没能删除“设置 → 应用”里的卸载信息。",
                      "Could not remove FlowBench from Settings → Apps.")

    # -- flow ----------------------------------------------------------------

    def start_uninstall(self):
        if self.busy:
            return
        self.session = UninstallSession(
            self.target, remove_user_data=self.removeDataCheck.isChecked(),
            data_dir=self.data_dir, parent_pid=self.parent_pid,
            wait_timeout=self.wait_timeout, platform=self.platform)
        self.parent_pid = 0          # only the first attempt needs to wait for it
        while self.session.blocking_pids():
            if not self.ask_retry(
                    self.t("FlowBench 正在运行", "FlowBench is running"),
                    self.t("请先退出 FlowBench（右键托盘图标 → 退出），然后点“重试”。",
                           "Quit FlowBench first (right-click the tray icon → Quit), then click Retry.")):
                return
        self.phase = "running"
        self._error = None
        self._progress = (0, "")
        self.progressBar.resume()
        self.progressBar.setValue(0)
        self.progressLogo.set_liquid("drain", 0.0, immediate=True)
        self._render_progress()
        self.pages.setCurrentWidget(self.progressPage)
        self.clock.boost(0.6)
        self.busy = True
        self.worker = StepWorker(self.session.run, self)
        self.worker.progressed.connect(self._on_progress)
        self.worker.succeeded.connect(self._on_success)
        self.worker.failed.connect(self._on_failure)
        self.worker.start()

    def _on_progress(self, pct: int, detail: str):
        self._progress = (pct, detail)
        self.progressBar.setValue(pct)
        self.percentLabel.setText(f"{pct}%")
        self.progressLogo.set_liquid("drain", pct / 100.0)
        self._set_elided(self.progressDetail, self._progress_text())

    def _on_success(self, warnings):
        self.busy = False
        self.succeeded = True
        self.phase = "done"
        self._warnings = list(warnings or [])
        self.progressBar.setValue(100)
        self._render_done()
        self.doneLogo.show_badge(ghost=True)
        self.pages.setCurrentWidget(self.donePage)
        self.clock.boost(1.4)

    def _on_failure(self, kind: str, exc):
        self.busy = False
        if kind == "in_use" and self.ask_retry(
                self.t("安装文件夹正在被使用", "The install folder is in use"),
                self.t("请退出 FlowBench，并关闭打开了这个文件夹的资源管理器窗口，然后点“重试”。",
                       "Quit FlowBench and close any Explorer window showing this folder, "
                       "then click Retry.")):
            self.start_uninstall()
            return
        self.phase = "failed"
        self._error = exc
        self.progressBar.error()
        self._render_progress()

    def _finish(self):
        self.finished.emit(True)
        self.close()

    def closeEvent(self, event):
        if self.busy:
            event.ignore()      # deleting files: there is nothing safe to cancel
            return
        if not self.succeeded:
            self.finished.emit(False)
        event.accept()
