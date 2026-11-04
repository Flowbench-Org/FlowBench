"""Uninstall window: the same Fluent look as the setup wizard, in three steps
(confirm → progress → done)."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QHBoxLayout
from qfluentwidgets import (BodyLabel, CaptionLabel, CheckBox, IconWidget,
                            InfoBarIcon, PrimaryPushButton, ProgressBar,
                            PushButton, StrongBodyLabel, SubtitleLabel)

from flowbench_installer import core, winplat
from flowbench_installer.gui import InstallerWindow, StepWorker
from flowbench_installer.session import UninstallSession


class UninstallWindow(InstallerWindow):
    finished = Signal(bool)

    def __init__(self, target: Path, *, parent_pid: int = 0,
                 data_dir: Optional[Path] = None, chinese: Optional[bool] = None,
                 resources: Optional[Path] = None, platform=winplat,
                 wait_timeout: float = 30.0, parent=None):
        super().__init__(chinese=chinese, resources=resources, platform=platform,
                         parent=parent)
        self.target = Path(target)
        self.data_dir = Path(data_dir) if data_dir else core.user_data_dir()
        self.parent_pid = int(parent_pid or 0)
        self.wait_timeout = wait_timeout
        self.session: Optional[UninstallSession] = None
        self.worker: Optional[StepWorker] = None
        self.busy = False
        self.succeeded = False
        self.version = str(core.read_marker(self.target).get("version") or "")

        self.setWindowTitle(self.t("卸载 FlowBench", "Uninstall FlowBench"))
        self._build_frame(f"FlowBench {self.version}".strip(),
                          self.t("网络压力测试工具 · 卸载向导",
                                 "Network stress testing tool · Uninstall"))
        self.confirmPage = self._build_confirm_page()
        self.progressPage = self._build_progress_page()
        self.donePage = self._build_done_page()
        for page in (self.confirmPage, self.progressPage, self.donePage):
            self.pages.addWidget(page)
        self._refresh_data_note()
        self.setFixedSize(600, 430)

    # -- pages ---------------------------------------------------------------

    def _build_confirm_page(self):
        page, layout = self._page()
        layout.addWidget(StrongBodyLabel(
            self.t("将从这台电脑上移除 FlowBench", "FlowBench will be removed from this PC"), page))
        self.locationLabel = CaptionLabel(
            self.t(f"安装位置：{self.target}", f"Installed in {self.target}"), page)
        self.locationLabel.setWordWrap(True)
        layout.addWidget(self.locationLabel)
        layout.addSpacing(10)
        self.removeDataCheck = CheckBox(
            self.t("同时删除我的设置、插件和日志", "Also delete my settings, plugins and logs"), page)
        self.removeDataCheck.stateChanged.connect(lambda _state: self._refresh_data_note())
        layout.addWidget(self.removeDataCheck)
        self.dataNote = CaptionLabel("", page)
        self.dataNote.setWordWrap(True)
        self.dataNote.setContentsMargins(28, 0, 0, 0)     # line up with the box's text
        layout.addWidget(self.dataNote)
        self.cancelButton = PushButton(self.t("取消", "Cancel"), page)
        self.cancelButton.clicked.connect(self.close)
        self.uninstallButton = PrimaryPushButton(self.t("卸载", "Uninstall"), page)
        self.uninstallButton.clicked.connect(self.start_uninstall)
        self._button_row(layout, self.cancelButton, self.uninstallButton)
        return page

    def _build_progress_page(self):
        page, layout = self._page()
        self.progressTitle = SubtitleLabel(self.t("正在卸载 FlowBench…", "Uninstalling FlowBench…"), page)
        self.progressBar = ProgressBar(page)
        self.progressBar.setRange(0, 100)
        self.progressDetail = CaptionLabel("", page)
        self.progressError = BodyLabel("", page)
        self.progressError.setWordWrap(True)
        self.progressError.hide()
        layout.addWidget(self.progressTitle)
        layout.addSpacing(8)
        layout.addWidget(self.progressBar)
        layout.addWidget(self.progressDetail)
        layout.addWidget(self.progressError)
        self.closeButton = PushButton(self.t("关闭", "Close"), page)
        self.closeButton.clicked.connect(self.close)
        self.retryButton = PrimaryPushButton(self.t("重试", "Retry"), page)
        self.retryButton.clicked.connect(self.start_uninstall)
        self._button_row(layout, self.closeButton, self.retryButton)
        self.closeButton.hide()
        self.retryButton.hide()
        return page

    def _build_done_page(self):
        page, layout = self._page()
        row = QHBoxLayout()
        row.setSpacing(10)
        self.doneIcon = IconWidget(InfoBarIcon.SUCCESS, page)
        self.doneIcon.setFixedSize(22, 22)
        self.doneTitle = SubtitleLabel(self.t("FlowBench 已卸载", "FlowBench has been uninstalled"), page)
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
        self.finishButton = PrimaryPushButton(self.t("完成", "Finish"), page)
        self.finishButton.clicked.connect(self._finish)
        self._button_row(layout, self.finishButton)
        return page

    def _refresh_data_note(self):
        # The folder goes on its own line: a path has no spaces to wrap at.
        if self.removeDataCheck.isChecked():
            text = self.t("将删除下面的文件夹，包括已授权的目标、测试配置和已安装的插件，无法恢复：",
                          "This deletes the folder below, including authorized targets, "
                          "test settings and installed plugins. It cannot be undone:")
            self.dataNote.setTextColor(QColor("#C42B1C"), QColor("#FF99A4"))
        else:
            text = self.t("不勾选时会保留，重新安装后继续使用：",
                          "If unchecked, they are kept and used again if you reinstall:")
            self.dataNote.setTextColor(QColor(0, 0, 0), QColor(255, 255, 255))
        self.dataNote.setText(f"{text}\n{self.data_dir}")

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
        self.progressTitle.setText(self.t("正在卸载 FlowBench…", "Uninstalling FlowBench…"))
        self.progressError.hide()
        self.closeButton.hide()
        self.retryButton.hide()
        self.progressBar.setValue(0)
        self.pages.setCurrentWidget(self.progressPage)
        self.busy = True
        self.worker = StepWorker(self.session.run, self)
        self.worker.progressed.connect(self._on_progress)
        self.worker.succeeded.connect(self._on_success)
        self.worker.failed.connect(self._on_failure)
        self.worker.start()

    def _on_progress(self, pct: int, detail: str):
        self.progressBar.setValue(pct)
        if pct < UninstallSession.FILES_END:
            text = self.t("正在删除：", "Removing: ") + detail if detail else ""
        elif pct < 94:
            text = self.t("正在删除快捷方式和卸载信息…", "Removing shortcuts and the Apps entry…")
        elif pct < 100:
            text = self.t("正在删除设置和日志…", "Deleting settings and logs…")
        else:
            text = ""
        self._set_elided(self.progressDetail, text)

    def _on_success(self, warnings):
        self.busy = False
        self.succeeded = True
        if self.session.remove_user_data:
            detail = self.t("你的设置、插件和日志也已删除。",
                            "Your settings, plugins and logs were deleted too.")
        else:
            detail = self.t(f"你的设置、插件和日志保留在：\n{self.data_dir}",
                            f"Your settings, plugins and logs are kept in:\n{self.data_dir}")
        self.doneDetail.setText(detail)
        lines = [self._warning_text(w) for w in warnings or []]
        self.doneWarnings.setText("\n".join(lines))
        self.doneWarnings.setVisible(bool(lines))
        self.pages.setCurrentWidget(self.donePage)

    def _warning_text(self, warning) -> str:
        kind, subject, _detail = warning
        if kind == "user_data":
            return self.t(f"有些设置或日志没能删除，可以手动删除 {subject}。",
                          f"Some settings or logs could not be deleted; remove {subject} by hand.")
        return self.t("没能删除“设置 → 应用”里的卸载信息。",
                      "Could not remove FlowBench from Settings → Apps.")

    def _on_failure(self, kind: str, exc):
        self.busy = False
        if kind == "in_use" and self.ask_retry(
                self.t("安装文件夹正在被使用", "The install folder is in use"),
                self.t("请退出 FlowBench，并关闭打开了这个文件夹的资源管理器窗口，然后点“重试”。",
                       "Quit FlowBench and close any Explorer window showing this folder, "
                       "then click Retry.")):
            self.start_uninstall()
            return
        self.progressTitle.setText(self.t("卸载没有完成", "Uninstall did not finish"))
        self.progressError.setText(self._error_text(exc))
        self.progressError.show()
        self.progressDetail.setText("")
        self.closeButton.show()
        self.retryButton.show()

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

