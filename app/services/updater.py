"""检查更新：查询 GitHub Releases 最新版本，发现新版时弹窗提示。"""
import threading

import requests
from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QDesktopServices

APP_VERSION = "1.2.7"
REPO = "Carlown/FlowBench"
RELEASES_URL = f"https://github.com/{REPO}/releases"
LATEST_URL = f"https://github.com/{REPO}/releases/latest"
_API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"

_keep_alive = []  # 防止后台检查器被 GC


def _ver_tuple(v: str):
    nums = []
    for part in v.strip().lstrip("vV").split("."):
        digits = ""
        for ch in part:
            if ch.isdigit():
                digits += ch
            else:
                break
        nums.append(int(digits) if digits else 0)
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums[:3])


def is_newer(latest: str, current: str = APP_VERSION) -> bool:
    try:
        return _ver_tuple(latest) > _ver_tuple(current)
    except Exception:
        return False


class _Checker(QObject):
    done = Signal(dict)

    def go(self):
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        try:
            r = requests.get(_API_URL, timeout=8,
                             headers={"Accept": "application/vnd.github+json"})
            r.raise_for_status()
            d = r.json()
            tag = str(d.get("tag_name", ""))
            self.done.emit({"ok": True, "tag": tag,
                            "url": d.get("html_url") or LATEST_URL,
                            "newer": is_newer(tag)})
        except Exception as e:
            self.done.emit({"ok": False, "error": str(e)})


def check_for_updates(parent=None, manual: bool = False, on_finished=None):
    """后台检查 GitHub 最新 Release。

    manual=False（启动自动检查）：仅在有新版本且未跳过时弹窗；
    manual=True（设置页手动检查）：无论结果都给出提示。
    on_finished: 检查完成后的回调（无论成功失败）。
    """
    from app.ui.i18n import L
    from app.services.settings import settings

    # 自动检查：如果用户关闭了自动检查，直接返回
    if not manual and not settings.auto_check_updates:
        if on_finished:
            try:
                on_finished()
            except Exception:
                pass
        return

    c = _Checker()
    _keep_alive.append(c)

    def on_done(d):
        try:
            try:
                _keep_alive.remove(c)
            except ValueError:
                pass
            try:
                if not d.get("ok"):
                    if manual:
                        from qfluentwidgets import InfoBar
                        InfoBar.error(L("检查更新失败", "Update check failed"),
                                      L(f"无法连接 GitHub：{d.get('error', '')}",
                                        f"Cannot reach GitHub: {d.get('error', '')}"),
                                      parent=parent, duration=5000)
                elif d.get("newer"):
                    # 自动检查时，如果该版本已被用户跳过，不再弹窗
                    if not manual and settings.skip_version == d["tag"]:
                        pass
                    else:
                        _show_update_dialog(parent, d["tag"], d["url"], L)
                elif manual:
                    from qfluentwidgets import InfoBar
                    InfoBar.success(L("已是最新版本", "Up to date"),
                                    L(f"当前版本 v{APP_VERSION}，与 GitHub 最新版本一致。",
                                      f"You are on the latest version (v{APP_VERSION})."),
                                    parent=parent, duration=4000)
            except Exception:
                import traceback
                traceback.print_exc()
        finally:
            if on_finished:
                try:
                    on_finished()
                except Exception:
                    pass

    c.done.connect(on_done)
    c.go()


def _show_update_dialog(parent, tag, url, L):
    from qfluentwidgets import MessageBoxBase, TitleLabel, BodyLabel, CheckBox
    from app.services.settings import settings

    # MessageBoxBase：内容加入 viewLayout，随内容自动调整卡片大小
    box = MessageBoxBase(parent)

    title = TitleLabel(L(f"发现新版本 {tag}", f"New version {tag} available"), box)
    msg = BodyLabel(L(f"当前版本 v{APP_VERSION}，GitHub 已发布 {tag}。\n是否前往下载更新？",
                      f"Current version v{APP_VERSION}; {tag} is now on GitHub.\n"
                      f"Open the download page?"), box)
    msg.setWordWrap(True)

    skipCheck = CheckBox(L("跳过此版本（不再提示本次更新）", "Skip this version (don't remind me again)"), box)
    autoCheck = CheckBox(L("不再自动检查更新", "Don't check for updates automatically"), box)

    box.viewLayout.addWidget(title)
    box.viewLayout.addWidget(msg)
    box.viewLayout.addWidget(skipCheck)
    box.viewLayout.addWidget(autoCheck)

    box.widget.setFixedWidth(480)

    box.yesButton.setText(L("去更新", "Update"))
    box.cancelButton.setText(L("稍后再说", "Later"))

    if box.exec():
        QDesktopServices.openUrl(QUrl(url or LATEST_URL))

    # 保存用户选择
    if skipCheck.isChecked():
        settings.set("skip_version", tag)
    else:
        # 如果用户没勾选跳过，但当前 skip_version 正是此版本，清除它（手动触发更新时）
        if settings.skip_version == tag:
            settings.set("skip_version", "")
    if autoCheck.isChecked():
        if settings.set("auto_check_updates", False):
            _sync_settings_view(parent)


def _sync_settings_view(parent):
    """Immediately refresh the Settings page after changing update checks."""
    if parent is None:
        return
    try:
        window = parent.window() if hasattr(parent, "window") else parent
        view = getattr(window, "settingsView", None)
        sync = getattr(view, "_sync_preference_controls", None)
        if callable(sync):
            sync()
    except Exception:
        # A headless updater check may not have a Settings page.
        pass
