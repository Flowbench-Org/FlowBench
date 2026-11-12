"""检查更新：查询 GitHub Releases 最新版本，发现新版时弹窗提示。

安装版（由 FlowBench 自带安装程序安装）可以在软件内下载新版安装包，
带进度条，下载完成后自动退出并交给安装程序完成更新、重新打开软件。
"""
import hashlib
import os
import re
import secrets
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests
from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QDesktopServices

APP_VERSION = "1.2.14"
REPO = "Flowbench-Org/FlowBench"
RELEASES_URL = f"https://github.com/{REPO}/releases"
LATEST_URL = f"https://github.com/{REPO}/releases/latest"
_API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"

_keep_alive = []  # 防止后台检查器被 GC

SETUP_ASSET_RE = re.compile(r"^FlowBench-Setup-[0-9A-Za-z._-]+\.exe$", re.IGNORECASE)
INSTALL_MARKER = "FlowBench.install.json"   # written by flowbench_installer
DOWNLOAD_CHUNK = 256 * 1024


class DownloadError(Exception):
    pass


class DownloadCancelled(Exception):
    pass


def download_error_kind(exc: BaseException) -> str:
    """Sort a failed download into what the user can do about it."""
    if isinstance(exc, DownloadError):
        return "corrupt"            # wrong size or hash: the file was not kept
    if isinstance(exc, requests.HTTPError):
        return "http"
    # Checked before OSError: requests' exceptions derive from it.
    if isinstance(exc, requests.RequestException):
        return "network"
    if isinstance(exc, OSError):
        return "disk"
    return "other"


def pick_setup_asset(release: dict):
    """Return the installer asset of a GitHub release, or None."""
    if not isinstance(release, dict):
        return None
    version = str(release.get("tag_name", "")).strip().lstrip("vV")
    candidates = []
    for asset in release.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name", ""))
        url = str(asset.get("browser_download_url", ""))
        parsed = urlparse(url)
        if (SETUP_ASSET_RE.match(name) and parsed.scheme == "https"
                and parsed.hostname == "github.com"):
            candidates.append(asset)
    if not candidates:
        return None
    candidates.sort(key=lambda a: version not in str(a.get("name", "")))
    asset = candidates[0]
    digest = str(asset.get("digest") or "")
    try:
        size = max(0, int(asset.get("size") or 0))
    except (TypeError, ValueError):
        size = 0
    return {"name": str(asset["name"]), "url": str(asset["browser_download_url"]),
            "size": size,
            "sha256": digest[7:].lower() if digest.lower().startswith("sha256:") else ""}


def managed_install_dir():
    """Install folder when running from FlowBench's own installer, else None."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return None
    folder = Path(sys.executable).resolve().parent
    return folder if (folder / INSTALL_MARKER).is_file() else None


def can_self_update(info: dict) -> bool:
    """Download-and-install works for the packaged Windows app with a setup asset."""
    return bool(info.get("asset")) and sys.platform == "win32" and getattr(sys, "frozen", False)


def downloads_dir() -> Path:
    return Path(tempfile.gettempdir()) / "FlowBench" / "updates"


def setup_path_for(tag: str) -> Path:
    safe = re.sub(r"[^0-9A-Za-z._-]", "", str(tag).lstrip("vV")) or "latest"
    return downloads_dir() / f"FlowBench-Setup-{safe}.exe"


def cleanup_downloads(current: str = APP_VERSION, folder=None) -> None:
    """Delete downloaded installers for versions that are no longer newer."""
    folder = Path(folder) if folder else downloads_dir()
    try:
        entries = list(folder.iterdir())
    except OSError:
        return
    for entry in entries:
        match = re.match(r"^FlowBench-Setup-(.+?)\.exe(?:\.[0-9a-f]{8})?(\.part)?$",
                         entry.name, re.IGNORECASE)
        if match and (match.group(2) or not is_newer(match.group(1), current)):
            try:
                entry.unlink()
            except OSError:
                pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verified_download(path: Path, size: int, sha256: str) -> bool:
    try:
        if not path.is_file() or (size and path.stat().st_size != size):
            return False
        return not sha256 or _sha256(path) == sha256
    except OSError:
        return False


def download_file(url: str, dest: Path, size: int = 0, sha256: str = "",
                  progress=None, cancelled=None, session=None) -> Path:
    """Stream ``url`` to ``dest`` via a .part file, then verify size and hash."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Unique per attempt: a cancelled download may still be finishing its
    # last read when the user starts a new one.
    part = dest.with_name(f"{dest.name}.{secrets.token_hex(4)}.part")
    http = session or requests
    try:
        with http.get(url, stream=True, timeout=(10, 30),
                      headers={"Accept": "application/octet-stream"}) as resp:
            resp.raise_for_status()
            total = size or int(resp.headers.get("Content-Length") or 0)
            done = 0
            with open(part, "wb") as out:
                for block in resp.iter_content(DOWNLOAD_CHUNK):
                    if cancelled and cancelled():
                        raise DownloadCancelled()
                    if not block:
                        continue
                    out.write(block)
                    done += len(block)
                    if progress:
                        progress(done, total)
        if size and done != size:
            raise DownloadError(f"size mismatch: got {done} bytes, expected {size}")
        if sha256 and _sha256(part) != sha256.lower():
            raise DownloadError("SHA-256 mismatch")
        os.replace(part, dest)
        return dest
    except BaseException:
        try:
            part.unlink()
        except OSError:
            pass
        raise


def setup_command(setup: Path, install_dir, lang: str, theme: str, pid: int = None,
                  accent: str = ""):
    """Command line for the downloaded installer.

    A FlowBench-installed copy is updated in place (``--update``); anything
    else (e.g. an old Inno Setup install) gets the normal setup wizard, which
    migrates it.
    """
    if install_dir is None:
        return [str(setup)]
    command = [str(setup), "--update", "--pid", str(os.getpid() if pid is None else pid),
               "--dir", str(install_dir), "--lang", lang, "--theme", theme]
    if accent:
        command += ["--accent", accent]
    return command


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


_TAG_PAGE_RE = re.compile(r"^/[^/]+/[^/]+/releases/tag/([^/?#]+)$")


def latest_tag_from_page(http=requests):
    """The newest release's tag, read from where ``/releases/latest`` leads.

    This page is not counted against the GitHub API's limit of 60 requests an
    hour, but it lists no installer details.
    """
    r = http.head(LATEST_URL, allow_redirects=True, timeout=8,
                  headers={"User-Agent": "FlowBench"})
    r.raise_for_status()
    final = urlparse(str(r.url))
    match = _TAG_PAGE_RE.match(final.path)
    if final.scheme != "https" or final.hostname != "github.com" or not match:
        raise ValueError("no published release")
    return unquote(match.group(1)), str(r.url)


def fetch_latest_release(http=requests) -> dict:
    """Check GitHub for the newest release.

    The API gives the installer for an in-app update. When it fails, for
    example once its hourly limit is used up, the release page still tells
    which version is newest; the update then opens that page in the browser.
    """
    try:
        r = http.get(_API_URL, timeout=8, headers={"Accept": "application/vnd.github+json"})
        r.raise_for_status()
        d = r.json()
        tag = str(d.get("tag_name", ""))
        return {"ok": True, "tag": tag, "url": d.get("html_url") or LATEST_URL,
                "newer": is_newer(tag), "asset": pick_setup_asset(d)}
    except Exception as api_error:
        try:
            tag, url = latest_tag_from_page(http)
        except Exception:
            raise api_error
        return {"ok": True, "tag": tag, "url": url, "newer": is_newer(tag), "asset": None}


def check_error_kind(exc: BaseException) -> str:
    """Sort a failed update check: rate_limit, network or other."""
    if isinstance(exc, requests.HTTPError):
        resp = exc.response
        if resp is not None and resp.status_code in (403, 429):
            remaining = str(resp.headers.get("X-RateLimit-Remaining", ""))
            if remaining == "0" or "rate limit" in str(exc).lower():
                return "rate_limit"
        return "other"
    if isinstance(exc, requests.RequestException):
        return "network"
    return "other"


def _check_failure_text(kind: str, L) -> str:
    if kind == "rate_limit":
        return L("GitHub 暂时限制了查询次数，请过一会儿再试，或者打开发布页查看。",
                 "GitHub is limiting requests for now. Try again later, or open the release page.")
    if kind == "network":
        return L("连不上 GitHub，请检查网络或代理设置。",
                 "Could not reach GitHub. Check your network or proxy settings.")
    return L("GitHub 返回了意外的结果，请稍后再试。",
             "GitHub sent an unexpected reply. Try again later.")


class _Checker(QObject):
    done = Signal(dict)

    def go(self):
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        cleanup_downloads()
        try:
            self.done.emit(fetch_latest_release())
        except Exception as e:
            self.done.emit({"ok": False, "error": str(e), "kind": check_error_kind(e)})


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
                        _show_check_failure(parent, d, L)
                elif d.get("newer"):
                    # 自动检查时，如果该版本已被用户跳过，不再弹窗
                    if not manual and settings.skip_version == d["tag"]:
                        pass
                    else:
                        _show_update_dialog(parent, d["tag"], d["url"], L, d)
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


def _show_check_failure(parent, result, L):
    """A short reason plus a link to the release page; the raw error is the tooltip."""
    from PySide6.QtCore import Qt
    from qfluentwidgets import HyperlinkButton, InfoBar, InfoBarIcon, InfoBarPosition
    from app.ui import info_bar_fit
    info_bar_fit.install()
    bar = InfoBar(InfoBarIcon.ERROR, L("检查更新失败", "Update check failed"),
                  _check_failure_text(result.get("kind", "other"), L),
                  Qt.Horizontal, True, 8000, InfoBarPosition.TOP_RIGHT, parent)
    bar.setToolTip(str(result.get("error", "")))
    # The browser may get through where the app cannot (proxy, VPN), and the
    # link must be in place before the bar is shown and positioned.
    bar.addWidget(HyperlinkButton(LATEST_URL, L("打开发布页", "Open release page"), bar))
    bar.show()
    return bar


def _show_update_dialog(parent, tag, url, L, info=None):
    from qfluentwidgets import MessageBoxBase, TitleLabel, BodyLabel, CheckBox
    from app.services.settings import settings

    info = info or {}
    self_update = can_self_update(info)

    # MessageBoxBase：内容加入 viewLayout，随内容自动调整卡片大小
    box = MessageBoxBase(parent)

    title = TitleLabel(L(f"发现新版本 {tag}", f"New version {tag} available"), box)
    if self_update:
        text = L(f"当前版本 v{APP_VERSION}，GitHub 已发布 {tag}。\n"
                 "点“立即更新”会在软件内下载并自动安装，完成后自动重新打开 FlowBench。",
                 f"Current version v{APP_VERSION}; {tag} is now on GitHub.\n"
                 "Update now downloads and installs it, then reopens FlowBench automatically.")
    else:
        text = L(f"当前版本 v{APP_VERSION}，GitHub 已发布 {tag}。\n是否前往下载更新？",
                 f"Current version v{APP_VERSION}; {tag} is now on GitHub.\n"
                 f"Open the download page?")
    msg = BodyLabel(text, box)
    msg.setWordWrap(True)

    skipCheck = CheckBox(L("跳过此版本（不再提示本次更新）", "Skip this version (don't remind me again)"), box)
    autoCheck = CheckBox(L("不再自动检查更新", "Don't check for updates automatically"), box)

    box.viewLayout.addWidget(title)
    box.viewLayout.addWidget(msg)
    box.viewLayout.addWidget(skipCheck)
    box.viewLayout.addWidget(autoCheck)

    box.widget.setFixedWidth(480)

    box.yesButton.setText(L("立即更新", "Update now") if self_update else L("去更新", "Update"))
    box.cancelButton.setText(L("稍后再说", "Later"))

    accepted = box.exec()

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

    if accepted:
        if self_update:
            start_self_update(parent, tag, info["asset"], url)
        else:
            QDesktopServices.openUrl(QUrl(url or LATEST_URL))


class _Downloader(QObject):
    progress = Signal(int, int)
    done = Signal(dict)

    def __init__(self, asset: dict, dest: Path):
        super().__init__()
        self.asset = asset
        self.dest = Path(dest)
        self._cancel = False

    def go(self):
        self._cancel = False
        threading.Thread(target=self._work, daemon=True).start()

    def cancel(self):
        self._cancel = True

    def _work(self):
        try:
            if verified_download(self.dest, self.asset.get("size", 0),
                                 self.asset.get("sha256", "")):
                path = self.dest          # already downloaded earlier
            else:
                path = download_file(self.asset["url"], self.dest,
                                     self.asset.get("size", 0),
                                     self.asset.get("sha256", ""),
                                     progress=self.progress.emit,
                                     cancelled=lambda: self._cancel)
            self.done.emit({"ok": True, "path": str(path)})
        except DownloadCancelled:
            self.done.emit({"ok": False, "cancelled": True})
        except Exception as e:
            self.done.emit({"ok": False, "error": str(e), "kind": download_error_kind(e)})


def _format_mb(n: int) -> str:
    return f"{n / (1024 * 1024):.1f} MB"


def _download_failure_text(kind: str, L) -> str:
    if kind == "network":
        return L("无法连接 GitHub，或者下载中途断开了。", "Could not reach GitHub, or the download was interrupted.")
    if kind == "http":
        return L("GitHub 暂时无法提供这个文件。", "GitHub could not provide the file right now.")
    if kind == "corrupt":
        return L("下载的文件不完整或已损坏，已经删除。", "The downloaded file was incomplete or damaged, so it was deleted.")
    if kind == "disk":
        return L("无法保存安装包，请检查磁盘空间。", "Could not save the installer. Check that there is enough disk space.")
    return L("下载没有完成。", "The download did not finish.")


def _make_download_dialog(parent, tag, asset, L, page_url=None):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QPushButton
    from qfluentwidgets import CaptionLabel, MessageBoxBase, ProgressBar, SubtitleLabel

    name = str(asset.get("name") or f"FlowBench-Setup-{tag.lstrip('vV')}.exe")

    class UpdateDownloadDialog(MessageBoxBase):
        """下载新版安装包，带进度条；失败可重试，或改用浏览器从发布页下载。"""

        def __init__(self):
            super().__init__(parent)
            self.setup_path = None
            self.page_url = page_url or LATEST_URL
            self.setup_name = name
            self.opened_browser = False
            self.titleLabel = SubtitleLabel(L(f"正在下载 FlowBench {tag}", f"Downloading FlowBench {tag}"), self)
            self.bar = ProgressBar(self)
            self.bar.setRange(0, 1000)
            self.bar.setValue(0)
            self.detail = CaptionLabel(L("正在连接 GitHub…", "Connecting to GitHub…"), self)
            self.detail.setWordWrap(True)
            self.viewLayout.addWidget(self.titleLabel)
            self.viewLayout.addSpacing(6)
            self.viewLayout.addWidget(self.bar)
            self.viewLayout.addWidget(self.detail)
            self.widget.setFixedWidth(500)       # three equal buttons once it fails
            self.yesButton.setText(L("重试", "Retry"))
            self.yesButton.hide()
            # Same look as the dialog's own secondary button.
            self.browserButton = QPushButton(L("用浏览器下载", "Open in browser"), self.buttonGroup)
            self.browserButton.setObjectName("cancelButton")
            self.browserButton.setAttribute(Qt.WA_LayoutUsesWidgetRect)
            self.browserButton.clicked.connect(self.open_in_browser)
            self.buttonLayout.insertWidget(1, self.browserButton, 1, Qt.AlignVCenter)
            self.browserButton.hide()
            self.cancelButton.setText(L("取消", "Cancel"))
            self._started = 0.0
            self.downloader = _Downloader(asset, setup_path_for(tag))
            self.downloader.progress.connect(self._on_progress)
            self.downloader.done.connect(self._on_done)
            self.start()

        def start(self):
            self.yesButton.hide()
            self.browserButton.hide()
            self.cancelButton.setText(L("取消", "Cancel"))
            self.detail.setToolTip("")
            self.titleLabel.setText(L(f"正在下载 FlowBench {tag}", f"Downloading FlowBench {tag}"))
            self.detail.setText(L("正在连接 GitHub…", "Connecting to GitHub…"))
            self.bar.setError(False)
            self.bar.setValue(0)
            self._started = time.monotonic()
            self.downloader.go()

        def validate(self):
            # “重试”按钮：重新下载，不关闭对话框
            self.start()
            return False

        def reject(self):
            self.downloader.cancel()
            super().reject()

        def open_in_browser(self):
            if QDesktopServices.openUrl(QUrl(self.page_url)):
                self.opened_browser = True
                self.reject()
                return
            self.detail.setText(L(f"无法打开浏览器，请手动访问：\n{self.page_url}",
                                  f"Could not open a browser. Visit this page yourself:\n{self.page_url}"))
            self.detail.setTextInteractionFlags(Qt.TextSelectableByMouse)

        def _on_progress(self, done: int, total: int):
            elapsed = max(0.001, time.monotonic() - self._started)
            speed = f"{_format_mb(int(done / elapsed))}/s"
            if total:
                self.bar.setValue(min(1000, int(done * 1000 / total)))
                self.detail.setText(f"{_format_mb(done)} / {_format_mb(total)} · {speed}")
            else:
                self.detail.setText(f"{_format_mb(done)} · {speed}")

        def _on_done(self, result: dict):
            if result.get("ok"):
                self.setup_path = result["path"]
                self.bar.setValue(1000)
                self.detail.setText(L("下载完成，正在启动安装程序…", "Downloaded. Starting the installer…"))
                self.accept()
            elif not result.get("cancelled"):
                self.titleLabel.setText(L("下载失败", "Download failed"))
                reason = _download_failure_text(result.get("kind", "other"), L)
                # The file name on its own line: it has no spaces to wrap at.
                hint = L(f"点“重试”再试一次，或者点“用浏览器下载”，从发布页下载并运行：\n{name}",
                         f"Click Retry to try again, or Open in browser to download and run "
                         f"this file from the release page:\n{name}")
                self.detail.setText(f"{reason}\n{hint}")
                self.detail.setToolTip(result.get("error", ""))    # the technical detail
                self.bar.setError(True)
                self.yesButton.show()
                self.browserButton.show()
                self.cancelButton.setText(L("关闭", "Close"))

    return UpdateDownloadDialog()


def start_self_update(parent, tag, asset, url=None):
    """下载安装包 → 退出软件 → 安装程序更新并重新打开 FlowBench。"""
    from qfluentwidgets import InfoBar, MessageBox
    from app.ui.i18n import L, current_lang
    from app.services.settings import settings

    box = _make_download_dialog(parent, tag, asset, L, url)
    if not box.exec() or not box.setup_path:
        if box.opened_browser:
            InfoBar.success(L("已在浏览器中打开发布页", "Opened the release page in your browser"),
                            L(f"下载 {box.setup_name} 后运行它，就能完成更新。",
                              f"Download {box.setup_name} and run it to finish updating."),
                            parent=parent, duration=8000)
        return False

    try:
        from app.services.stress import engine
        running = bool(getattr(engine, "running", False))
    except Exception:
        running = False
    if running:
        ask = MessageBox(L("测试正在进行", "A test is running"),
                         L("现在更新会停止当前测试。要立即更新吗？\n选“稍后”的话，安装包会保留，下次更新不用重新下载。",
                           "Updating now stops the running test. Update now?\n"
                           "If you choose Later, the download is kept for next time."),
                         parent)
        ask.yesButton.setText(L("立即更新", "Update now"))
        ask.cancelButton.setText(L("稍后", "Later"))
        if not ask.exec():
            return False

    command = setup_command(Path(box.setup_path), managed_install_dir(),
                            "zh" if current_lang() == "zh-CN" else "en",
                            "dark" if settings.theme == "dark" else "light",
                            accent=str(settings.theme_color or ""))
    try:
        from flowbench_installer.winplat import launch_detached
        launch_detached(command[0], command[1:], Path(box.setup_path).parent)
    except Exception as e:
        InfoBar.error(L("无法启动安装程序", "Could not start the installer"), str(e),
                      parent=parent, duration=6000)
        if url:
            QDesktopServices.openUrl(QUrl(url))
        return False
    _quit_for_update(parent)
    return True


def _quit_for_update(parent):
    """退出软件，让安装程序替换文件（托盘最小化设置不影响）。"""
    from PySide6.QtWidgets import QApplication
    window = parent.window() if parent is not None and hasattr(parent, "window") else None
    quit_app = getattr(window, "_quit_app", None)
    if callable(quit_app):
        quit_app()
    else:
        QApplication.quit()


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
