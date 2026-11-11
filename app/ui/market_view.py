# -*- coding: utf-8 -*-
"""插件页面：Pivot 双页 — 本地插件管理 + 插件市场。

- 本地插件：启停/重载/删除/导入，从设置页迁移而来
- 插件市场：浏览社区插件、安装、搜索
- 发布向导：选择本地插件 + 图标，支持一键通过 GitHub API 提交 PR 上架
- 图标支持 base64 data URI 或 http(s) 直链
"""
import base64
import datetime
import hashlib
import json
import os
import shutil
import threading
import unicodedata

import requests
from PySide6.QtCore import (QEvent, QEasingCurve, QPoint, QPropertyAnimation,
                            QRect, QRectF, QSize, Qt, QTimer, QUrl, Signal)
from PySide6.QtGui import (QColor, QDesktopServices, QIcon, QKeySequence,
                           QPainter, QPainterPath, QPixmap, QShortcut)
from PySide6.QtWidgets import (QApplication, QFileDialog, QGridLayout,
                               QHBoxLayout, QLabel, QLayout, QStackedWidget,
                               QVBoxLayout, QWidget)
from qfluentwidgets import (Action, BodyLabel, CaptionLabel, ComboBox,
                            FluentIcon as FIF, FluentIconBase, IconWidget,
                            IndeterminateProgressRing, InfoBar, isDarkTheme,
                            LineEditButton, MessageBox, MessageBoxBase, Pivot,
                            CheckableMenu, PrimaryPushButton, PushButton,
                            ScrollArea, SearchLineEdit, SimpleCardWidget,
                            StrongBodyLabel, SubtitleLabel, SwitchButton,
                            TextEdit, ToolButton, TransparentDropDownToolButton,
                            VerticalSeparator, qconfig, setCustomStyleSheet)

from app.services.market import (INDEX_EDIT_URL, MarketClient,
                                 GITHUB_OAUTH_CLIENT_ID, device_flow_start,
                                 device_flow_poll, is_valid_market_plugin_id)
from app.services.plugins import _i18n_text, plugin_manager, plugins_dir
from app.services.settings import settings
from app.ui.i18n import L

class _ChevronIcon(FluentIconBase):
    """Drop-down chevron that can point up while its panel is open.

    FluentIcon only ships a downward chevron (ARROW_DOWN, the one ComboBox
    uses); the upward one is the same glyph rotated half a turn.
    """

    def __init__(self, up: bool = False):
        self.up = up

    def path(self, theme=None):
        return FIF.ARROW_DOWN.path() if theme is None else FIF.ARROW_DOWN.path(theme)

    def render(self, painter, rect, theme=None, indexes=None, **attributes):
        kwargs = {} if theme is None else {"theme": theme}
        if not self.up:
            FIF.ARROW_DOWN.render(painter, rect, indexes=indexes, **kwargs, **attributes)
            return
        rect = QRectF(rect)
        painter.save()
        painter.translate(rect.center())
        painter.rotate(180)
        painter.translate(-rect.center())
        FIF.ARROW_DOWN.render(painter, rect, indexes=indexes, **kwargs, **attributes)
        painter.restore()


_CHEVRON_DOWN = _ChevronIcon(False)
_CHEVRON_UP = _ChevronIcon(True)

_ICON_SIZE = 40
_MAX_ICON_BYTES = 64 * 1024


class _SearchSuggestCard(SimpleCardWidget):
    """带真实透明圆角、可适配深浅主题的搜索悬浮卡片。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setBorderRadius(10)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setBackgroundColor(self._normalBackgroundColor())

    def _normalBackgroundColor(self):
        # Fully opaque: even alpha 250 let the plugin cards underneath show
        # through as faint text.
        return QColor(45, 45, 45) if isDarkTheme() else QColor(250, 250, 250)

    def _hoverBackgroundColor(self):
        return self._normalBackgroundColor()

    def _pressedBackgroundColor(self):
        return self._normalBackgroundColor()

# 插件分类：JSON 里的 category 值 -> (中文, English)
_PLUGIN_CATEGORIES = (
    ("tool", ("工具", "Tools")),
    ("protocol", ("协议", "Protocols")),
    ("ui", ("界面", "UI & Pages")),
    ("other", ("其他", "Misc")),
)


def _category_label(key: str):
    """分类 key 转双语标签；未知 key 归入“其他”。"""
    for k, label in _PLUGIN_CATEGORIES:
        if k == key:
            return label
    return _PLUGIN_CATEGORIES[-1][1]


def _entry_category(entry: dict) -> str:
    """读取条目分类，缺省/非法值归入 other。"""
    v = str(entry.get("category", "") or "").strip().lower()
    return v if any(k == v for k, _ in _PLUGIN_CATEGORIES) else "other"


def _normalize_search_text(value) -> str:
    """统一全半角、大小写和 Unicode 组合形式，便于多语言搜索。"""
    return unicodedata.normalize("NFKC", str(value or "")).casefold()


# 无图标插件的默认底色池（按插件 ID 稳定选取，同插件颜色不变）
_FALLBACK_COLORS = ("#E81123", "#F7630C", "#CA500F", "#FFB900",
                    "#107C10", "#038387", "#0078D4", "#8764B8", "#C239B3")


def _fallback_color(entry: dict) -> str:
    """根据插件 ID/名称稳定取一个底色（同一插件每次颜色相同）。"""
    key = str(entry.get("id") or entry.get("name") or "?")
    n = 0
    for ch in key:
        n = (n * 31 + ord(ch)) & 0xFFFFFFFF
    return _FALLBACK_COLORS[n % len(_FALLBACK_COLORS)]


def _contrast_text_color(background: str) -> str:
    """按相对亮度选择对比度更高的深色或白色文字。"""
    color = QColor(background)
    channels = []
    for value in (color.redF(), color.greenF(), color.blueF()):
        channels.append(value / 12.92 if value <= 0.04045
                        else ((value + 0.055) / 1.055) ** 2.4)
    luminance = (
        0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]
    )
    dark_contrast = (luminance + 0.05) / 0.05
    light_contrast = 1.05 / (luminance + 0.05)
    return "#111111" if dark_contrast >= light_contrast else "#FFFFFF"


def _solid_heart_icon(color="#E81123") -> QIcon:
    """生成实心收藏爱心；按钮本身的 Fluent 边框不受影响。"""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    path = QPainterPath()
    path.moveTo(32, 58)
    path.cubicTo(28, 54, 5, 39, 5, 20)
    path.cubicTo(5, 8, 18, 1, 28, 8)
    path.cubicTo(30, 9, 31, 11, 32, 13)
    path.cubicTo(33, 11, 34, 9, 36, 8)
    path.cubicTo(46, 1, 59, 8, 59, 20)
    path.cubicTo(59, 39, 36, 54, 32, 58)
    painter.drawPath(path)
    painter.end()
    return QIcon(pixmap)

# GitHub 一键发布相关常量
_REPO_OWNER = "Flowbench-Org"
_REPO_NAME = "FlowBench"
_REPO_HOMEPAGE = f"https://github.com/{_REPO_OWNER}/{_REPO_NAME}"
# The GitHub account that publishes the official plugins (a person, so it did
# not change when the repository moved to the organization).
_OFFICIAL_PUBLISHER = "Carlown"
_OFFICIAL_HOMEPAGES = (f"https://github.com/{_REPO_OWNER}/", "https://github.com/Carlown/")
_REPO_BRANCH = "master"
_MARKET_DIR = "marketplace"
_GH_API = "https://api.github.com"


def gh_headers(token):
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "FlowBench-Plugin-Publisher",
    }


def gh_get_user(token):
    r = requests.get(f"{_GH_API}/user", headers=gh_headers(token), timeout=15)
    r.raise_for_status()
    return r.json()["login"]


def gh_can_push(token):
    """当前 token 用户是否对上游仓库有 push 权限。"""
    r = requests.get(f"{_GH_API}/repos/{_REPO_OWNER}/{_REPO_NAME}",
                     headers=gh_headers(token), timeout=15)
    if r.status_code != 200:
        return False
    return bool((r.json().get("permissions") or {}).get("push"))


def gh_get_file(repo_api, path, branch, headers):
    """读取文件，返回 (text_content, sha)；不存在返回 (None, None)。"""
    r = requests.get(f"{repo_api}/contents/{path}", headers=headers,
                     params={"ref": branch}, timeout=10)
    if r.status_code == 404:
        return None, None
    r.raise_for_status()
    info = r.json()
    return base64.b64decode(info["content"]).decode("utf-8"), info["sha"]


def gh_get_json_file(repo_api, path, branch, headers):
    """读取 JSON 文件，返回 (data, sha)；不存在返回 (None, None)。"""
    text, sha = gh_get_file(repo_api, path, branch, headers)
    if text is None:
        return None, None
    return json.loads(text), sha


def gh_put_file(repo_api, path, content_b64, message, branch, headers, sha=None):
    payload = {"message": message, "content": content_b64, "branch": branch}
    if sha:
        payload["sha"] = sha
    r = requests.put(f"{repo_api}/contents/{path}", headers=headers,
                     json=payload, timeout=20)
    if r.status_code not in (200, 201):
        r.raise_for_status()
    return r.json()


def gh_delete_file(repo_api, path, message, branch, headers, sha):
    r = requests.delete(f"{repo_api}/contents/{path}", headers=headers,
                        params={"message": message, "branch": branch, "sha": sha},
                        timeout=15)
    if r.status_code not in (200, 202):
        r.raise_for_status()
    return r.json()


def gh_check_scopes(token):
    """返回当前 token 的 scope 集合。"""
    r = requests.get(f"{_GH_API}/user", headers=gh_headers(token), timeout=15)
    r.raise_for_status()
    scopes = r.headers.get("X-OAuth-Scopes", "") or ""
    return {s.strip() for s in scopes.split(",") if s.strip()}


def gh_file_sha(repo_api, path, branch, headers):
    """只检查文件是否存在并返回 sha；不存在返回 None。不解析内容。"""
    r = requests.get(f"{repo_api}/contents/{path}", headers=headers,
                     params={"ref": branch}, timeout=10)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json().get("sha")


def decode_data_uri_icon(v: str):
    """解析不超过 64 KiB 的图片 data URI；非法内容返回 None。"""
    if not isinstance(v, str) or not v.startswith("data:image/") or "," not in v:
        return None
    try:
        b64 = v.split(",", 1)[1]
        if len(b64) > ((_MAX_ICON_BYTES + 2) // 3) * 4 + 8:
            return None
        data = base64.b64decode(b64, validate=True)
        return data if len(data) <= _MAX_ICON_BYTES else None
    except Exception:
        return None


def _download_icon_bytes(url: str):
    """下载不超过 64 KiB 的图标，避免远程大文件占用过量内存。"""
    with requests.get(url, timeout=8, stream=True) as r:
        r.raise_for_status()
        size_header = int(r.headers.get("Content-Length", 0) or 0)
        if size_header > _MAX_ICON_BYTES:
            raise ValueError("icon exceeds 64 KiB limit")
        data = bytearray()
        for chunk in r.iter_content(8192):
            if not chunk:
                continue
            data.extend(chunk)
            if len(data) > _MAX_ICON_BYTES:
                raise ValueError("icon exceeds 64 KiB limit")
        return bytes(data)


def load_icon_async(icon_field, callback):
    data = decode_data_uri_icon(icon_field)
    if data is not None:
        callback(data)
        return
    if isinstance(icon_field, str) and icon_field.startswith(("http://", "https://")):
        def _work():
            try:
                data = _download_icon_bytes(icon_field)
                QTimer.singleShot(0, lambda: callback(data))
            except Exception:
                pass
        threading.Thread(target=_work, daemon=True).start()


class PluginIconLabel(QLabel):
    """插件图标：无图标时显示插件名首字 + 专属底色块。"""

    def __init__(self, entry: dict, parent=None):
        super().__init__(parent)
        self.setFixedSize(_ICON_SIZE, _ICON_SIZE)
        self.setAlignment(Qt.AlignCenter)
        name = entry.get("name", "?")
        if isinstance(name, (tuple, list)) and len(name) == 2:
            name = name[0] if L("中", "en") == "中" else name[1]
        self._fallback_text(name, _fallback_color(entry))

        def _set(data):
            pm = QPixmap()
            if pm.loadFromData(data):
                self.setStyleSheet("")
                self.setPixmap(pm.scaled(_ICON_SIZE, _ICON_SIZE,
                                         Qt.KeepAspectRatio,
                                         Qt.SmoothTransformation))
        load_icon_async(entry.get("icon", ""), _set)

    def _fallback_text(self, name: str, color: str):
        ch = (str(name)[:1] or "?").upper()
        self.setText(ch)
        self.setStyleSheet(
            f"color:#FFFFFF; font-size:18px; font-weight:600;"
            f"background:{color}; border-radius:6px;")


class MarketCard(SimpleCardWidget):
    def __init__(self, entry: dict, view: "PluginMarketPage", parent=None):
        super().__init__(parent)
        self.entry = entry
        self.view = view
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(14)

        lay.addWidget(PluginIconLabel(entry, self))

        col = QVBoxLayout()
        col.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.addWidget(StrongBodyLabel(
            f"{self._txt(entry.get('name'))}  v{entry.get('version', '?')}", self))
        title_row.addStretch(1)
        self.favoriteBtn = ToolButton(FIF.HEART, self)
        self.favoriteBtn.setFixedSize(30, 30)
        self.favoriteBtn.clicked.connect(self._toggle_favorite)
        title_row.addWidget(self.favoriteBtn)
        col.addLayout(title_row)
        category = _i18n_text(_category_label(_entry_category(entry)))
        meta = " · ".join(str(x) for x in
                          (category, entry.get("date"), entry.get("author"),
                           self._txt(entry.get("description"))) if x)
        dlab = CaptionLabel(meta, self)
        dlab.setWordWrap(True)
        col.addWidget(dlab)
        lay.addLayout(col, 1)

        # 作者保留原来的右侧纵向操作列；普通用户使用居中的安装 + 收藏操作列。
        btn_wrap = QWidget(self)
        btn_col = QVBoxLayout(btn_wrap)
        btn_col.setContentsMargins(0, 0, 0, 0)
        btn_col.setSpacing(4)

        self.ownerActions = QWidget(btn_wrap)
        owner_col = QVBoxLayout(self.ownerActions)
        owner_col.setContentsMargins(0, 0, 0, 0)
        owner_col.setSpacing(4)
        self.btn = PrimaryPushButton(btn_wrap)
        self.btn.setFixedWidth(96)
        self.btn.clicked.connect(self._install)
        owner_col.addWidget(self.btn)
        self.unpubBtn = PushButton(L("下架", "Unpublish"), btn_wrap)
        self.unpubBtn.setFixedWidth(96)
        self._refresh_unpublish_style()
        qconfig.themeChanged.connect(self._refresh_unpublish_style)
        self.unpubBtn.clicked.connect(self._unpublish)
        owner_col.addWidget(self.unpubBtn)
        btn_col.addWidget(self.ownerActions)

        self.userActions = QWidget(btn_wrap)
        user_row = QHBoxLayout(self.userActions)
        user_row.setContentsMargins(0, 0, 0, 0)
        user_row.setSpacing(8)
        user_row.setAlignment(Qt.AlignCenter)
        self.userInstallBtn = PrimaryPushButton(self.userActions)
        self.userInstallBtn.setFixedWidth(96)
        self.userInstallBtn.clicked.connect(self._install)
        user_row.addWidget(self.userInstallBtn, 0, Qt.AlignCenter)
        self.userFavoriteBtn = ToolButton(FIF.HEART, self.userActions)
        self.userFavoriteBtn.setFixedSize(30, 30)
        self.userFavoriteBtn.clicked.connect(self._toggle_favorite)
        user_row.addWidget(self.userFavoriteBtn, 0, Qt.AlignCenter)
        btn_col.addWidget(self.userActions)

        lay.addWidget(btn_wrap, 0, Qt.AlignVCenter)
        self.set_owner_state(self.view.can_unpublish(entry))
        self.refresh_state()
        self.refresh_favorite()

    def set_owner_state(self, is_owner: bool):
        """作者显示原有管理布局，普通用户显示居中的安装/收藏布局。"""
        self.ownerActions.setVisible(bool(is_owner))
        self.userActions.setVisible(not is_owner)
        self.favoriteBtn.setVisible(bool(is_owner))
        self.userFavoriteBtn.setVisible(not is_owner)
        self._is_owner = bool(is_owner)

    def _refresh_unpublish_style(self, *_):
        """主题重载后恢复“下架”按钮的危险操作语义色。"""
        from qfluentwidgets import setCustomStyleSheet
        setCustomStyleSheet(
            self.unpubBtn,
            "PushButton{color:#e81123;}",
            "PushButton{color:#e81123;}")

    @staticmethod
    def _txt(v):
        if isinstance(v, (tuple, list)) and len(v) == 2:
            return v[0] if L("中", "en") == "中" else v[1]
        return str(v)

    def refresh_state(self):
        st = MarketClient.installed_state(self.entry)
        if st == "disabled":
            text, enabled = L("启用", "Enable"), True
        elif st == "same":
            text, enabled = L("已安装", "Installed"), False
        elif st == "update":
            text, enabled = L("更新", "Update"), True
        else:
            text, enabled = L("安装", "Install"), True
        for button in (self.btn, self.userInstallBtn):
            button.setText(text)
            button.setEnabled(enabled)

    def refresh_favorite(self):
        pid = str(self.entry.get("id", ""))
        favorite = self.view.is_favorite(pid)
        icon = _solid_heart_icon() if favorite else FIF.HEART
        tooltip = (L("取消收藏", "Remove from favorites") if favorite
                   else L("收藏插件", "Add to favorites"))
        name = self._txt(self.entry.get("name"))
        accessible = (L(f"取消收藏：{name}", f"Remove from favorites: {name}")
                      if favorite else
                      L(f"收藏：{name}", f"Add to favorites: {name}"))
        for button in (self.favoriteBtn, self.userFavoriteBtn):
            button.setIcon(icon)
            button.setToolTip(tooltip)
            button.setAccessibleName(accessible)

    def _toggle_favorite(self):
        self.view.toggle_favorite(str(self.entry.get("id", "")))

    def _install(self):
        # 已安装但被禁用：直接启用，无需重新下载
        st = MarketClient.installed_state(self.entry)
        if st == "disabled":
            pid = self.entry.get("id", "")
            plugin_manager.set_enabled(pid, True)
            InfoBar.success(L("已启用", "Enabled"),
                            L(f"{pid} 已启用", f"{pid} enabled"),
                            parent=self.window(), duration=3000)
            self.refresh_state()
            return
        for button in (self.btn, self.userInstallBtn):
            button.setEnabled(False)
            button.setText(L("下载中…", "Downloading…"))
        self.view.install_card(self)

    def _unpublish(self):
        """下架插件：创建移除 PR。"""
        pid = self.entry.get("id", "?")
        name = self._txt(self.entry.get("name"))
        box = MessageBox(
            L("下架插件", "Unpublish Plugin"),
            L(f"确定要下架插件「{name}」吗？\n\n"
              f"将创建一个 Pull Request 从市场索引中移除该插件，"
              f"PR 合并后插件不再对用户可见。需要 GitHub 授权。",
              f"Unpublish plugin \"{name}\"?\n\n"
              f"This will create a Pull Request removing it from the marketplace index. "
              f"It will no longer be visible after the PR is merged. GitHub authorization required."),
            self.window())
        if not box.exec():
            return
        self.view.unpublish_card(self)


# ---------- 本地插件页 ----------

class _StatePill(QWidget):
    """插件状态小标签：运行中（绿）/ 已禁用（灰）/ 加载失败（红），按当前主题取色。"""

    # (浅色文字, 深色文字)；底色用同一颜色的低透明度
    _COLORS = {
        "loaded": ("#0f7b0f", "#6ccb5f"),
        "error": ("#c42b1c", "#ff99a4"),
    }
    _NEUTRAL = ("#5f5f5f", "#c5c5c5")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = ""
        self._text = ""
        font = self.font()
        font.setPixelSize(12)
        self.setFont(font)
        qconfig.themeChanged.connect(self.update)

    def set_state(self, state: str, text: str):
        self._state, self._text = state, text
        self.setAccessibleName(text)
        self.updateGeometry()
        self.update()

    def text(self) -> str:
        return self._text

    def color(self) -> QColor:
        light, dark = self._COLORS.get(self._state, self._NEUTRAL)
        return QColor(dark if isDarkTheme() else light)

    def sizeHint(self):
        fm = self.fontMetrics()
        return QSize(fm.horizontalAdvance(self._text) + 16, fm.height() + 6)

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, _e):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        color = self.color()
        fill = QColor(color)
        fill.setAlpha(46 if isDarkTheme() else 30)
        painter.setPen(Qt.NoPen)
        painter.setBrush(fill)
        rect = QRectF(self.rect())
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        painter.setPen(color)
        painter.drawText(self.rect(), Qt.AlignCenter, self._text)


class LocalPluginCard(SimpleCardWidget):
    """一个本地插件的卡片，和插件市场的卡片同一种外框。

    左边图标，中间名称、版本、状态和简介，右边一行操作：重载、删除，
    分隔线后面是启用开关。支持增量更新（避免重建导致跳变）。
    """

    def __init__(self, rec, parent=None):
        super().__init__(parent)
        self._rec = rec
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(14)

        # 图标（与市场卡片同样大小）
        self._icon_label = QLabel(self)
        self._icon_label.setFixedSize(_ICON_SIZE, _ICON_SIZE)
        self._icon_label.setAlignment(Qt.AlignCenter)
        self._set_local_icon(self._icon_label, rec)
        lay.addWidget(self._icon_label, 0, Qt.AlignVCenter)

        self._text_col = QVBoxLayout()
        self._text_col.setContentsMargins(0, 0, 0, 0)
        self._text_col.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        self._title_label = StrongBodyLabel("", self)
        title_row.addWidget(self._title_label)
        self._version_label = CaptionLabel("", self)
        title_row.addWidget(self._version_label)
        self._state_pill = _StatePill(self)
        title_row.addWidget(self._state_pill, 0, Qt.AlignVCenter)
        title_row.addStretch(1)
        self._text_col.addLayout(title_row)
        self._desc_label = CaptionLabel("", self)
        self._desc_label.setWordWrap(True)
        self._text_col.addWidget(self._desc_label)
        self._error_label = CaptionLabel("", self)
        self._error_label.setWordWrap(True)
        setCustomStyleSheet(self._error_label,
                            "FluentLabelBase{color:#c42b1c;}",
                            "FluentLabelBase{color:#ff99a4;}")
        self._error_label.hide()
        self._text_col.addWidget(self._error_label)
        lay.addLayout(self._text_col, 1)

        # 右侧操作：一行，竖直居中；每张卡片的按钮相同，所以上下对齐
        self.actions = QWidget(self)
        act = QHBoxLayout(self.actions)
        act.setContentsMargins(0, 0, 0, 0)
        act.setSpacing(8)
        self._reload_btn = PushButton(FIF.SYNC, L("重载", "Reload"), self.actions)
        self._reload_btn.clicked.connect(lambda _=False: self._reload(self._rec.pid))
        act.addWidget(self._reload_btn)
        self._del_btn = PushButton(FIF.DELETE.colored(QColor("#c42b1c"), QColor("#ff99a4")),
                                   L("删除", "Remove"), self.actions)
        setCustomStyleSheet(self._del_btn, "PushButton{color:#c42b1c;}",
                            "PushButton{color:#ff99a4;}")
        self._del_btn.clicked.connect(
            lambda _=False: self._remove(self._rec.pid, _i18n_text(self._rec.display_name)))
        act.addWidget(self._del_btn)
        act.addSpacing(4)
        self._separator = VerticalSeparator(self.actions)
        self._separator.setFixedHeight(24)
        act.addWidget(self._separator, 0, Qt.AlignVCenter)
        act.addSpacing(4)
        self._switch = SwitchButton(self.actions)
        self._switch.setOnText("")
        self._switch.setOffText("")
        self._switch.label.hide()           # 状态写在名称旁的标签里，开关本身不带字
        self._switch.checkedChanged.connect(
            lambda v: plugin_manager.set_enabled(self._rec.pid, v))
        act.addWidget(self._switch, 0, Qt.AlignVCenter)
        lay.addWidget(self.actions, 0, Qt.AlignVCenter)

        self.update_from_record(rec)

    # ---------- 增量更新 ----------

    def update_from_record(self, rec):
        """根据最新的 _PluginRecord 增量更新显示。"""
        self._rec = rec
        name = _i18n_text(rec.display_name) or rec.pid
        self._title_label.setText(name)
        ver = str(rec.display_version or "").strip()
        if ver and ver[:1] not in "vV":
            ver = f"v{ver}"
        self._version_label.setText(ver)
        self._version_label.setVisible(bool(ver))
        # 更新图标（加载完成后 display_name/icon 才可用，需同步刷新，
        # 否则首次 refresh 时插件尚未加载，图标会一直停留在 pid 首字母）
        self._refresh_icon(rec)
        self._update_desc(rec)
        self._update_state(rec)
        # 更新开关（不触发信号）
        enabled = rec.state == "loaded"
        self._switch.blockSignals(True)
        self._switch.setChecked(enabled)
        self._switch.blockSignals(False)
        tip = (L(f"停用插件：{name}", f"Disable plugin: {name}") if enabled
               else L(f"启用插件：{name}", f"Enable plugin: {name}"))
        self._switch.setToolTip(tip)
        self._switch.setAccessibleName(tip)
        self._reload_btn.setAccessibleName(L(f"重载插件：{name}", f"Reload plugin: {name}"))
        self._del_btn.setAccessibleName(L(f"删除插件：{name}", f"Remove plugin: {name}"))

    def _refresh_icon(self, rec):
        """重设图标：先清掉兜底文字/样式，再按优先级设置真实图标或首字。"""
        self._icon_label.setText("")
        self._icon_label.setStyleSheet("")
        self._icon_label.setPixmap(QPixmap())
        self._set_local_icon(self._icon_label, rec)

    def _update_desc(self, rec):
        """更新作者/描述行。"""
        desc_parts = []
        author = rec.display_author
        if author:
            desc_parts.append(str(author))
        desc = _i18n_text(rec.display_description)
        if desc:
            desc_parts.append(desc)
        self._desc_label.setText(" · ".join(desc_parts))
        self._desc_label.setVisible(bool(desc_parts))

    def _update_state(self, rec):
        """更新状态标签和错误行。"""
        state_map = {
            "loaded": L("运行中", "Running"),
            "disabled": L("已禁用", "Disabled"),
            "error": L("加载失败", "Load failed"),
            "unloaded": L("未加载", "Not loaded"),
        }
        self._state_pill.set_state(rec.state, state_map.get(rec.state, str(rec.state)))
        error = str(rec.error or "") if rec.state == "error" else ""
        self._state_pill.setToolTip(error)
        self._error_label.setText(error.splitlines()[0] if error else "")
        self._error_label.setToolTip(error)
        self._error_label.setVisible(bool(error))

    @staticmethod
    def _set_local_icon(label: QLabel, rec):
        """设置本地插件图标：市场图标 > 插件自定义 > 彩色首字。"""
        from app.services.plugins import resolve_plugin_icon, plugin_icon_path
        pm = None
        # 优先用市场图标文件
        ip = plugin_icon_path(rec.pid)
        if ip:
            pm = QPixmap(ip)
        # 其次用插件类 icon 属性（rec.plugin 可能因禁用而为 None，用缓存的 display_icon）
        if pm is None:
            ic = resolve_plugin_icon(rec.plugin, rec.pid, rec.path,
                                     icon_val=rec.display_icon)
            if ic is not None:
                try:
                    pm = ic.pixmap(_ICON_SIZE, _ICON_SIZE)
                except Exception:
                    pm = None
        if pm is not None and not pm.isNull():
            label.setPixmap(pm.scaled(_ICON_SIZE, _ICON_SIZE, Qt.KeepAspectRatio,
                                      Qt.SmoothTransformation))
            return
        # 兜底：彩色首字块
        name = _i18n_text(rec.display_name)
        ch = (str(name)[:1] or "?").upper()
        label.setText(ch)
        bg = _fallback_color({"id": rec.pid})
        label.setStyleSheet(
            f"color:#FFFFFF; font-size:18px; font-weight:600;"
            f"background:{bg};"
            f" border-radius:6px;")

    def _reload(self, pid):
        name = _i18n_text(self._rec.display_name) or pid
        ok = plugin_manager.reload(pid)
        if ok:
            InfoBar.success(L("重载完成", "Reloaded"),
                            L(f"插件已重新加载：{name}",
                              f"Plugin reloaded: {name}"),
                            parent=self.window(), duration=2500)
        else:
            rec = plugin_manager.record(pid)
            msg = rec.error if rec and rec.error else L("未知错误", "Unknown error")
            InfoBar.error(L("重载失败", "Reload failed"), str(msg).splitlines()[0],
                          parent=self.window(), duration=5000)

    def _remove(self, pid, name):
        box = MessageBox(L("删除插件", "Remove Plugin"),
                         L(f"确定删除插件\"{name}\"？插件文件将从磁盘移除。",
                           f"Remove plugin \"{name}\"? Its files will be deleted."),
                         self.window())
        if not box.exec():
            return
        if plugin_manager.remove(pid):
            InfoBar.success(L("已删除", "Removed"), name, parent=self.window())


class LocalPluginsPage(QWidget):
    """本地插件管理页。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("localPluginsPage")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 8, 0, 0)
        root.setSpacing(12)

        warn = CaptionLabel(L(
            "插件为第三方代码，拥有与主程序相同的权限，请仅安装可信来源的插件；"
            "插件行为同样受免责声明约束。",
            "Plugins are third-party code with the same privileges as the app. "
            "Only install from trusted sources; the disclaimer applies."), self)
        warn.setWordWrap(True)
        root.addWidget(warn)

        # 列表区域独立滚动：警告条和底部按钮行始终固定可见，
        # 插件多了以后只滚动列表本身，不再滚动整个页面
        self.listScroll = ScrollArea(self)
        self.listScroll.setWidgetResizable(True)
        self.listScroll.enableTransparentBackground()
        self.listScroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.listHost = QWidget()
        self.listHost.setObjectName("localListHost")
        self.listHost.setStyleSheet("#localListHost{background: transparent;}")
        self.listLay = QVBoxLayout(self.listHost)
        self.listLay.setContentsMargins(0, 0, 0, 0)
        self.listLay.setSpacing(8)
        self.listLay.addStretch(1)
        self.listScroll.setWidget(self.listHost)
        root.addWidget(self.listScroll, 1)

        brow = QHBoxLayout()
        importBtn = PushButton(L("导入插件…", "Import Plugin…"), self)
        importBtn.clicked.connect(self._import_plugin)
        brow.addWidget(importBtn)
        dirBtn = PushButton(L("打开插件目录", "Open Plugin Folder"), self)
        dirBtn.clicked.connect(self._open_dir)
        brow.addWidget(dirBtn)
        rescanBtn = PushButton(L("重新扫描", "Rescan"), self)
        rescanBtn.clicked.connect(self._rescan)
        brow.addWidget(rescanBtn)
        brow.addStretch(1)
        root.addLayout(brow)

        plugin_manager.changed.connect(lambda: QTimer.singleShot(0, self.refresh))
        self.refresh()

    def refresh(self):
        """增量刷新本地插件列表：保留已有行，只更新变化部分。"""
        records = plugin_manager.discover()
        # 收集现有行
        existing = {}
        for i in range(self.listLay.count()):
            w = self.listLay.itemAt(i).widget()
            if w is not None and isinstance(w, LocalPluginCard):
                existing[w._rec.pid] = w
        # 按最新 records 顺序更新/添加
        seen = set()
        row_index = 0
        for rec in records:
            pid = rec.pid
            seen.add(pid)
            row = existing.get(pid)
            if row is not None:
                row.update_from_record(rec)
                current_idx = self.listLay.indexOf(row)
                if current_idx != row_index:
                    self.listLay.removeWidget(row)
                    self.listLay.insertWidget(row_index, row)
            else:
                new_row = LocalPluginCard(rec, self)
                self.listLay.insertWidget(row_index, new_row)
            row_index += 1
        # 删除已不存在的行
        for pid, row in existing.items():
            if pid not in seen:
                row.setParent(None)
                row.deleteLater()
        # 空状态提示
        empty_label = None
        for i in range(self.listLay.count()):
            w = self.listLay.itemAt(i).widget()
            if w is not None and not isinstance(w, LocalPluginCard):
                empty_label = w
                break
        if not records:
            if empty_label is None:
                empty_label = CaptionLabel(L(
                    "暂无插件。点击\"导入插件…\"添加 .py 插件文件，"
                    "或将插件放入插件目录后重新扫描。",
                    "No plugins yet. Use \"Import Plugin…\" to add a .py file, "
                    "or drop plugins into the folder and rescan."), self)
                empty_label.setWordWrap(True)
                self.listLay.insertWidget(0, empty_label)
        elif empty_label is not None:
            empty_label.setParent(None)
            empty_label.deleteLater()

    def _import_plugin(self):
        path, _ = QFileDialog.getOpenFileName(
            self, L("选择插件文件", "Select plugin file"),
            "", L("Python 插件 (*.py);;所有文件 (*.*)", "Python plugin (*.py);;All files (*.*)"))
        if not path:
            return
        ok, msg = plugin_manager.import_from(path)
        if ok:
            InfoBar.success(L("导入成功", "Imported"),
                            L("插件已导入并启用", "Plugin imported and enabled"),
                            parent=self.window(), duration=3000)
        else:
            InfoBar.error(L("导入失败", "Import failed"), msg,
                          parent=self.window(), duration=5000)

    def _open_dir(self):
        os.startfile(plugins_dir())

    def _rescan(self):
        n = plugin_manager.load_all()
        self.refresh()
        InfoBar.success(L("扫描完成", "Rescan done"),
                        L(f"共加载 {n} 个插件", f"{n} plugin(s) loaded"),
                        parent=self.window(), duration=2500)


# ---------- 插件市场页 ----------

class PluginMarketPage(QWidget):
    """插件市场浏览页。"""

    _MAX_SEARCH_HISTORY = settings.SEARCH_HISTORY_LIMIT
    _MAX_HOT_SEARCHES = 5

    # 跨线程信号：下架流程
    unpubAuthNeeded = Signal(str, str)       # code, uri（设备授权时浏览器打开）
    unpubAuthOk = Signal(object, str, object)  # entry, token, card
    unpubOk = Signal(str, bool, object)      # url, is_direct, card
    unpubErr = Signal(str, object)           # msg, card
    identityReady = Signal(str)              # 当前 GitHub 登录名

    def __init__(self, parent=None):
        super().__init__(parent)
        self._github_login = ""
        self.unpubAuthNeeded.connect(self._on_unpub_auth_needed)
        self.unpubAuthOk.connect(lambda e, t, c: self._do_unpublish(e, t, c))
        self.unpubOk.connect(self._on_unpub_ok)
        self.unpubErr.connect(self._on_unpub_err)
        self.identityReady.connect(self._set_github_identity)
        self.setObjectName("pluginMarketPage")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 8, 0, 0)
        root.setSpacing(10)

        # 搜索 + 筛选 + 排序 + 工具栏
        topbar = QHBoxLayout()
        topbar.setSpacing(8)
        self.searchEdit = SearchLineEdit(self)
        self.searchEdit.setPlaceholderText(L("搜索插件名称、作者、描述…",
                                             "Search by name, author, description…"))
        self.searchEdit.setAccessibleName(L("搜索插件", "Search plugins"))
        self.searchEdit.setClearButtonEnabled(True)
        # 搜索框内下拉箭头：放在放大镜按钮正左侧。
        # Chevron, like every other drop-down; FIF.DOWN is the arrow used by
        # the sort-direction button next to it and read as "sort".
        self.searchDropDownBtn = LineEditButton(_CHEVRON_DOWN, self.searchEdit)
        self.searchDropDownBtn.setFixedSize(29, 25)
        search_btn_index = self.searchEdit.hBoxLayout.indexOf(
            self.searchEdit.searchButton)
        self.searchEdit.hBoxLayout.insertWidget(
            search_btn_index, self.searchDropDownBtn, 0, Qt.AlignRight)
        self.searchEdit.setTextMargins(0, 0, 89, 0)
        self.searchDropDownBtn.setToolTip(
            L("展开搜索推荐", "Show search suggestions"))
        self.searchDropDownBtn.setAccessibleName(
            L("展开搜索推荐", "Show search suggestions"))
        # 在按下时只切换一次；不能等 release 后的 clicked，否则 Windows
        # 顶层 Tool 窗口先隐藏、按钮松开又会把面板重新展开。
        self.searchDropDownBtn.pressed.connect(self._toggle_search_suggestions)
        self._search_commit_timer = QTimer(self)
        self._search_commit_timer.setSingleShot(True)
        self._search_commit_timer.setInterval(650)
        self._search_commit_timer.timeout.connect(self._commit_pending_search)
        self.searchEdit.textChanged.connect(self._on_search_text_changed)
        self.searchEdit.searchSignal.connect(self._submit_search)
        self.searchEdit.installEventFilter(self)
        self.searchShortcut = QShortcut(QKeySequence.Find, self)
        self.searchShortcut.activated.connect(self._focus_search)
        topbar.addWidget(self.searchEdit, 1)

        # 筛选按钮：按类型筛选
        # 注意：普通 ToolButton.setMenu 走的是 Qt 原生 QToolButton.setMenu(QMenu)，
        # 而 RoundMenu 不是 QMenu，菜单弹不出来；必须用 DropDown 系列（自带
        # setMenu(RoundMenu) + 点击弹出逻辑）。
        self._filter_cat = "all"
        self._favorites_only = False
        self.filterBtn = TransparentDropDownToolButton(FIF.FILTER, self)
        self.filterBtn.setToolTip(L("筛选类型和安装状态",
                                    "Filter by type and install status"))
        self.filterMenu = CheckableMenu(parent=self)
        self._filter_actions = []
        filter_items = [("all", ("全部", "All"))] + list(_PLUGIN_CATEGORIES)
        for key, label in filter_items:
            act = Action(_i18n_text(label), checkable=True, parent=self)
            act.setChecked(key == "all")
            act.triggered.connect(lambda _c, k=key: self._set_filter_cat(k))
            self.filterMenu.addAction(act)
            self._filter_actions.append((act, key))
        self.filterMenu.addSeparator()
        self._filter_state = "all"
        self._filter_state_actions = []
        state_items = (
            ("all", ("全部状态", "All states")),
            ("absent", ("未安装", "Not installed")),
            ("same", ("已安装", "Installed")),
            ("update", ("可更新", "Updates available")),
            ("disabled", ("已禁用", "Disabled")),
        )
        for key, label in state_items:
            act = Action(_i18n_text(label), checkable=True, parent=self)
            act.setChecked(key == "all")
            act.triggered.connect(lambda _c, k=key: self._set_filter_state(k))
            self.filterMenu.addAction(act)
            self._filter_state_actions.append((act, key))
        self.filterMenu.addSeparator()
        self.favoriteFilterAction = Action(
            L("仅看收藏", "Favorites only"), checkable=True, parent=self)
        self.favoriteFilterAction.triggered.connect(self._set_favorites_only)
        self.filterMenu.addAction(self.favoriteFilterAction)
        self.resetFilterAction = Action(
            L("清除全部筛选", "Clear all filters"), parent=self)
        self.resetFilterAction.triggered.connect(self._reset_market_filters)
        self.filterMenu.addAction(self.resetFilterAction)
        self.filterBtn.setMenu(self.filterMenu)
        topbar.addWidget(self.filterBtn)

        # 排序：长条下拉选排序方式 + 按钮切正序/倒序
        self._sort_desc = True
        self.sortCombo = ComboBox(self)
        self.sortCombo.addItems([L("按时间", "Time"), L("按名称", "Name"),
                                 L("按作者", "Author"), L("按版本", "Version"),
                                 L("按状态", "Status")])
        self.sortCombo.setCurrentIndex(0)
        self.sortCombo.currentIndexChanged.connect(lambda _i: self._resort())
        self.sortDirBtn = ToolButton(FIF.DOWN, self)
        self.sortDirBtn.setToolTip(L("倒序（点击切换）", "Descending (click to toggle)"))
        self.sortDirBtn.clicked.connect(self._toggle_sort_dir)
        topbar.addWidget(self.sortCombo)
        topbar.addWidget(self.sortDirBtn)

        self.pubBtn = PushButton(L("发布插件…", "Publish a Plugin…"), self)
        self.pubBtn.clicked.connect(self._publish)
        topbar.addWidget(self.pubBtn)
        self.refreshBtn = PushButton(L("刷新", "Refresh"), self)
        self.refreshBtn.clicked.connect(self._load)
        topbar.addWidget(self.refreshBtn)
        root.addLayout(topbar)

        # 点击搜索框后出现的热门搜索 / 搜索历史面板。
        # 热门按 jsDelivr 统计的近 30 天真实安装次数排序。
        self._hot_searches = []          # [(关键词, 安装次数), ...]
        self._hot_entries = []
        self._install_counts = {}
        saved_favorites = settings.plugin_market_favorites
        self._favorite_ids = {
            str(x).strip() for x in saved_favorites if str(x).strip()
        } if isinstance(saved_favorites, list) else set()
        saved_history = settings.plugin_market_search_history
        self._search_history = []
        seen_history = set()
        if isinstance(saved_history, list):
            for value in saved_history:
                term = str(value or "").strip()[:100]
                folded = _normalize_search_text(term)
                if term and folded not in seen_history:
                    self._search_history.append(term)
                    seen_history.add(folded)
                if len(self._search_history) >= self._MAX_SEARCH_HISTORY:
                    break
        self._suggestions_open = False
        self._height_animations = {}
        self._deactivate_check_pending = False
        self.searchSuggestPanel = _SearchSuggestCard(self)
        self.searchSuggestPanel.installEventFilter(self)
        suggest_lay = QVBoxLayout(self.searchSuggestPanel)
        # 顶层窗口的默认布局约束会把 minimumHeight 锁到 sizeHint，导致
        # geometry 动画只能在结束时瞬间隐藏；允许窗口真正收缩到 0。
        suggest_lay.setSizeConstraint(QLayout.SetNoConstraint)
        self.searchSuggestPanel.setMinimumHeight(0)
        suggest_lay.setContentsMargins(16, 12, 16, 12)
        suggest_lay.setSpacing(8)

        hot_head = QHBoxLayout()
        hot_head.setContentsMargins(0, 0, 0, 0)
        self.hotTitle = StrongBodyLabel(L("热门搜索", "Popular searches"),
                                        self.searchSuggestPanel)
        hot_head.addWidget(self.hotTitle)
        hot_head.addStretch(1)
        # 说明排名依据：真实安装次数，或者暂时还没有统计。
        self.hotNote = CaptionLabel("", self.searchSuggestPanel)
        hot_head.addWidget(self.hotNote)
        suggest_lay.addLayout(hot_head)
        self.hotHost = QWidget(self.searchSuggestPanel)
        hot_lay = QVBoxLayout(self.hotHost)
        hot_lay.setContentsMargins(0, 0, 0, 0)
        hot_lay.setSpacing(6)
        self._hot_buttons = []
        rank_colors = ("#E53935", "#F6B73C", "#4F86F7", "#7F8C9A", "#666666")
        for rank, color in enumerate(rank_colors, 1):
            btn = PushButton("", self.hotHost)
            btn.setMinimumHeight(38)
            btn_lay = QHBoxLayout(btn)
            btn_lay.setContentsMargins(8, 4, 8, 4)
            btn_lay.setSpacing(8)
            rank_label = QLabel(str(rank), btn)
            rank_label.setAlignment(Qt.AlignCenter)
            rank_label.setFixedSize(26, 26)
            rank_label.setStyleSheet(
                f"background: {color}; color: {_contrast_text_color(color)}; "
                "border-radius: 13px; "
                "font-size: 13px; font-weight: 700;")
            rank_label.setAttribute(Qt.WA_TransparentForMouseEvents)
            btn_lay.addWidget(rank_label)
            term_label = QLabel("", btn)
            term_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            term_label.setAttribute(Qt.WA_TransparentForMouseEvents)
            btn_lay.addWidget(term_label, 1)
            count_label = QLabel("", btn)
            count_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            count_label.setAttribute(Qt.WA_TransparentForMouseEvents)
            btn_lay.addWidget(count_label)
            btn.clicked.connect(
                lambda _checked=False, b=btn: self._use_search_term(
                    b.property("searchTerm")))
            hot_lay.addWidget(btn)
            self._hot_buttons.append((btn, rank_label, term_label, count_label))
        suggest_lay.addWidget(self.hotHost)

        history_head = QHBoxLayout()
        history_head.setContentsMargins(0, 2, 0, 0)
        self.historyTitle = StrongBodyLabel(L("搜索历史", "Search history"),
                                            self.searchSuggestPanel)
        history_head.addWidget(self.historyTitle)
        history_head.addStretch(1)
        self.historyClearBtn = PushButton(L("全部清空", "Clear all"),
                                          self.searchSuggestPanel)
        self.historyClearBtn.setAccessibleName(
            L("清空全部搜索历史", "Clear all search history"))
        self.historyClearBtn.clicked.connect(self._clear_search_history)
        history_head.addWidget(self.historyClearBtn)
        suggest_lay.addLayout(history_head)

        self.historyHost = QWidget(self.searchSuggestPanel)
        self.historyLay = QGridLayout(self.historyHost)
        self.historyLay.setContentsMargins(0, 0, 0, 0)
        self.historyLay.setHorizontalSpacing(6)
        self.historyLay.setVerticalSpacing(6)
        for column in range(3):
            self.historyLay.setColumnStretch(column, 1)
        self.historyEmpty = CaptionLabel(
            L("暂无推荐，可直接输入关键词搜索",
              "No suggestions yet; type a keyword to search"),
            self.historyHost)
        self.historyLay.addWidget(self.historyEmpty, 0, 0, 1, 3)
        self._history_rows = []
        for _i in range(self._MAX_SEARCH_HISTORY):
            row = QWidget(self.historyHost)
            row_lay = QHBoxLayout(row)
            row_lay.setContentsMargins(0, 0, 0, 0)
            row_lay.setSpacing(4)
            term_btn = PushButton("", row)
            term_btn.setMinimumHeight(34)
            term_btn.clicked.connect(
                lambda _checked=False, b=term_btn: self._use_search_term(
                    b.property("searchTerm")))
            row_lay.addWidget(term_btn, 1)
            delete_btn = ToolButton(FIF.DELETE, row)
            delete_btn.setToolTip(L("删除这条历史", "Delete this search"))
            delete_btn.clicked.connect(
                lambda _checked=False, b=delete_btn: self._delete_search_term(
                    b.property("searchTerm")))
            row_lay.addWidget(delete_btn)
            self.historyLay.addWidget(row, 1 + _i // 3, _i % 3)
            self._history_rows.append((row, term_btn, delete_btn))
        suggest_lay.addWidget(self.historyHost)
        self._refresh_search_suggestions()
        self.searchSuggestPanel.hide()
        QTimer.singleShot(0, self._sync_search_suggest_width)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        qconfig.themeChanged.connect(self._on_search_theme_changed)

        self.statusLabel = CaptionLabel(L("正在加载市场…", "Loading marketplace…"), self)
        root.addWidget(self.statusLabel)
        self.spinner = IndeterminateProgressRing(self)
        self.spinner.setFixedSize(28, 28)
        root.addWidget(self.spinner, 0, Qt.AlignCenter)

        # 列表区域独立滚动：顶部搜索/筛选/排序栏和状态栏始终固定可见，
        # 插件多了以后只滚动卡片列表，不再滚动整个页面
        self.listScroll = ScrollArea(self)
        self.listScroll.setWidgetResizable(True)
        self.listScroll.enableTransparentBackground()
        self.listScroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.listHost = QWidget()
        self.listHost.setObjectName("marketListHost")
        self.listHost.setStyleSheet("#marketListHost{background: transparent;}")
        self.listLay = QVBoxLayout(self.listHost)
        self.listLay.setContentsMargins(0, 0, 0, 0)
        self.listLay.setSpacing(8)
        self.listLay.addStretch(1)
        self.listScroll.setWidget(self.listHost)
        root.addWidget(self.listScroll, 1)

        self.client = MarketClient()
        self.client.index_ready.connect(self._on_index)
        self.client.fetch_error.connect(self._on_error)
        self.client.download_ready.connect(self._on_downloaded)
        self.client.download_failed.connect(self._on_download_failed)
        self.client.install_counts_ready.connect(self._on_install_counts)
        plugin_manager.changed.connect(lambda: QTimer.singleShot(0, self._refresh_buttons))
        self._load_github_identity()
        self._all_cards = []
        self._last_visible_count = 0
        self._market_source_suffix = ""
        self._incompatible_count = 0
        self._load()

    def _load_github_identity(self):
        """异步读取当前授权账号，用于只给插件作者显示下架操作。"""
        cached_login = str(settings.github_login or "").strip()
        token = str(settings.github_token or "").strip()
        if not token:
            self._set_github_identity(cached_login)
            return

        def _work():
            try:
                login = gh_get_user(token)
            except Exception:
                # GitHub 暂时离线或 token 已过期时，沿用最近一次成功身份；
                # 下架动作本身仍会要求重新授权，不会绕过 GitHub 校验。
                login = cached_login
            else:
                settings.set("github_login", login)
            self.identityReady.emit(login)

        threading.Thread(target=_work, daemon=True).start()

    def _set_github_identity(self, login):
        self._github_login = str(login or "").strip().casefold()
        for i in range(self.listLay.count()):
            card = self.listLay.itemAt(i).widget()
            if isinstance(card, MarketCard):
                card.set_owner_state(self.can_unpublish(card.entry))

    def can_unpublish(self, entry: dict) -> bool:
        """只有当前 GitHub 账号是该条目发布者时才允许下架。"""
        if not self._github_login:
            return False
        publisher = str(entry.get("publisher", "") or "").strip().casefold()
        if not publisher:
            # 兼容旧索引：只有作者字段恰好等于 GitHub 登录名才放行。
            publisher = str(entry.get("author", "") or "").strip().casefold()
        return bool(publisher and publisher == self._github_login)

    @staticmethod
    def _normalize_market_entry(entry: dict) -> dict:
        """给旧版官方索引补齐发布者字段，保证离线缓存也能识别作者。"""
        if (not entry.get("publisher")
                and str(entry.get("author", "")).strip() == "FlowBench"
                and str(entry.get("homepage", "")).startswith(_OFFICIAL_HOMEPAGES)):
            entry["publisher"] = _OFFICIAL_PUBLISHER
        return entry

    def _load(self):
        self.statusLabel.setText(L("正在加载市场…", "Loading marketplace…"))
        self.spinner.show()
        self._clear_cards()
        self.client.fetch_index()

    def _clear_cards(self):
        self._all_cards = []
        while self.listLay.count() > 1:
            item = self.listLay.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()

    def _on_index(self, entries, from_cache):
        from app.services.updater import APP_VERSION, _ver_tuple
        entries = [self._normalize_market_entry(e) for e in entries]
        self.spinner.hide()
        # Multiple rapid refresh clicks can leave several fetch threads in
        # flight. Clear the actual widgets for every response, not just the
        # Python list, so late responses cannot duplicate cards on screen.
        self._clear_cards()
        self._incompatible_count = 0
        for e in entries:
            minv = str(e.get("min_app", ""))
            if minv and _ver_tuple(minv) > _ver_tuple(APP_VERSION):
                self._incompatible_count += 1
                continue
            card = MarketCard(e, self)
            self.listLay.insertWidget(self.listLay.count() - 1, card)
            self._all_cards.append(card)
        self._market_source_suffix = (
            L("（离线缓存）", " (offline cache)") if from_cache else "")
        # Only plugins that have a card here: searching for one that is hidden
        # as incompatible would find nothing.
        self._update_hot_searches([card.entry for card in self._all_cards])
        if self._all_cards:
            self.client.fetch_install_counts()
        self._update_filter_counts()
        self._resort()
        self._apply_filter(self.searchEdit.text())

    def eventFilter(self, watched, event):
        """处理下拉生命周期、外部点击、键盘和窗口位置变化。"""
        if watched is self.searchSuggestPanel and event.type() == QEvent.Hide:
            self._suggestions_open = False
            self.searchDropDownBtn.setIcon(_CHEVRON_DOWN)
            self.searchDropDownBtn.setToolTip(
                L("展开搜索推荐", "Show search suggestions"))
            self.searchDropDownBtn.setAccessibleName(
                L("展开搜索推荐", "Show search suggestions"))

        if event.type() == QEvent.ApplicationDeactivate:
            # Windows 上非激活 Tool 窗口与主窗口切换时也可能短暂发出该事件。
            # 立即隐藏会发生“箭头点击先隐藏、clicked 随后又重新展开”的竞争，
            # 因而延迟到收起动画结束后再确认应用是否真的失去激活。
            self._schedule_application_deactivate_check()
        elif watched is self and event.type() in (QEvent.Hide, QEvent.Close):
            self._force_hide_search_suggestions()
            self._search_commit_timer.stop()
        elif (self._suggestions_open and event.type() == QEvent.MouseButtonPress
              # 原生 Windows 输入会先把同一次点击发给 QWindow/页面顶层，
              # 随后才发给真正的按钮，不能只根据 watched 判断内外区域。
              and not self._is_search_popup_event(watched, event)):
            self._hide_search_suggestions()
        elif (self._suggestions_open and event.type() == QEvent.KeyPress
              and event.key() == Qt.Key_Escape):
            self._hide_search_suggestions()
            self.searchEdit.setFocus()
            return True
        elif (self._suggestions_open and event.type() in (QEvent.Move, QEvent.Resize)
              and watched is self.window()):
            QTimer.singleShot(0, self._resize_open_search_suggestions)

        if watched is self.searchEdit:
            if event.type() == QEvent.Resize:
                QTimer.singleShot(0, self._sync_search_suggest_width)
            elif (event.type() == QEvent.MouseButtonPress
                  and event.button() == Qt.LeftButton):
                self._show_search_suggestions()
            elif event.type() == QEvent.KeyPress and event.key() == Qt.Key_Down:
                self._show_search_suggestions()
                return True
        return super().eventFilter(watched, event)

    def _is_search_popup_widget(self, watched):
        """判断事件对象是否属于搜索框或它的悬浮下拉层。"""
        widget = watched if isinstance(watched, QWidget) else None
        while widget is not None:
            if widget in (self.searchEdit, self.searchSuggestPanel):
                return True
            widget = widget.parentWidget()
        return False

    def _is_search_popup_event(self, watched, event):
        """按事件对象和全局命中控件共同判断点击是否位于搜索区域。"""
        if self._is_search_popup_widget(watched):
            return True
        global_position = getattr(event, "globalPosition", None)
        if callable(global_position):
            point = global_position().toPoint()
            # Windows 的透明/Mica 窗口上 widgetAt() 可能返回 None，先直接
            # 比较真实全局矩形，确保箭头和悬浮层内所有按钮都能收到点击。
            for widget in (self.searchEdit, self.searchSuggestPanel):
                if widget.isVisible():
                    top_left = widget.mapToGlobal(QPoint(0, 0))
                    if QRect(top_left, widget.size()).contains(point):
                        return True
            hit = QApplication.widgetAt(point)
            if self._is_search_popup_widget(hit):
                return True
        return False

    def _sync_search_suggest_width(self):
        """悬浮面板与搜索框等宽，并始终贴在搜索框下沿。"""
        width = self.searchEdit.width()
        if width > 0:
            self.searchSuggestPanel.setFixedWidth(width)
            if self.searchSuggestPanel.isVisible():
                QTimer.singleShot(0, self._resize_open_search_suggestions)

    def _popup_geometries(self, requested_height):
        """计算页面内的展开/收起位置；下方不足时自动向上展开。"""
        top_left = self.searchEdit.mapTo(self, QPoint(0, 0))
        bottom_left = self.searchEdit.mapTo(
            self,
            QPoint(0, self.searchEdit.height()))
        available = self.rect()
        gap = 4
        width = min(self.searchEdit.width(), available.width())
        x = max(available.left(), min(top_left.x(),
                                     available.right() - width + 1))
        below = max(0, available.bottom() - bottom_left.y() - gap + 1)
        above = max(0, top_left.y() - available.top() - gap)
        open_upward = below < requested_height and above > below
        room = above if open_upward else below
        height = max(1, min(int(requested_height), max(1, room)))
        if open_upward:
            collapsed = QRect(x, top_left.y() - gap, width, 0)
            expanded = QRect(x, top_left.y() - gap - height, width, height)
        else:
            collapsed = QRect(x, bottom_left.y() + gap, width, 0)
            expanded = QRect(x, bottom_left.y() + gap, width, height)
        return collapsed, expanded

    def _animate_panel_geometry(self, start, end, finished=None):
        """悬浮下拉层的滑入/滑出动画。"""
        old = self._height_animations.pop("panel", None)
        if old is not None:
            old.stop()
        animation = QPropertyAnimation(
            self.searchSuggestPanel, b"geometry", self)
        animation.setDuration(180)
        animation.setStartValue(start)
        animation.setEndValue(end)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        self._height_animations["panel"] = animation

        def _done():
            if self._height_animations.get("panel") is not animation:
                return
            self._height_animations.pop("panel", None)
            if finished is not None:
                finished()

        animation.finished.connect(_done)
        animation.start()

    def _show_search_suggestions(self):
        """从搜索框下沿滑出悬浮的热门搜索或搜索历史。"""
        self._refresh_search_suggestions()
        self.searchSuggestPanel.setBackgroundColor(
            self.searchSuggestPanel._normalBackgroundColor())
        self._sync_search_suggest_width()
        if self._suggestions_open and self.searchSuggestPanel.isVisible():
            return
        self._suggestions_open = True
        self.searchDropDownBtn.setIcon(_CHEVRON_UP)
        self.searchDropDownBtn.setToolTip(
            L("收起搜索推荐", "Hide search suggestions"))
        self.searchDropDownBtn.setAccessibleName(
            L("收起搜索推荐", "Hide search suggestions"))
        collapsed, end = self._popup_geometries(
            self.searchSuggestPanel.sizeHint().height())
        start = (self.searchSuggestPanel.geometry()
                 if self.searchSuggestPanel.isVisible() else collapsed)
        if not self.searchSuggestPanel.isVisible():
            self.searchSuggestPanel.setGeometry(start)
            self.searchSuggestPanel.show()
        self.searchSuggestPanel.raise_()
        self._animate_panel_geometry(start, end)

    def _toggle_search_suggestions(self):
        if self._suggestions_open and self.searchSuggestPanel.isVisible():
            self._hide_search_suggestions()
        else:
            self._show_search_suggestions()

    def _resize_open_search_suggestions(self):
        """热门榜与历史切换时，让悬浮层平滑适配新高度。"""
        if not self._suggestions_open or not self.searchSuggestPanel.isVisible():
            return
        start = self.searchSuggestPanel.geometry()
        _collapsed, end = self._popup_geometries(
            self.searchSuggestPanel.sizeHint().height())
        if start != end:
            self._animate_panel_geometry(start, end)

    def _hide_search_suggestions(self):
        """将悬浮推荐层向上滑回搜索框。"""
        if self._search_commit_timer.isActive():
            self._commit_pending_search()
        if not self.searchSuggestPanel.isVisible():
            self._suggestions_open = False
            self.searchDropDownBtn.setIcon(_CHEVRON_DOWN)
            self.searchDropDownBtn.setAccessibleName(
                L("展开搜索推荐", "Show search suggestions"))
            return
        self._suggestions_open = False
        self.searchDropDownBtn.setIcon(_CHEVRON_DOWN)
        self.searchDropDownBtn.setToolTip(
            L("展开搜索推荐", "Show search suggestions"))
        self.searchDropDownBtn.setAccessibleName(
            L("展开搜索推荐", "Show search suggestions"))
        start = self.searchSuggestPanel.geometry()
        collapsed, _expanded = self._popup_geometries(start.height())
        end = collapsed

        def _collapsed():
            if not self._suggestions_open:
                self.searchSuggestPanel.hide()

        self._animate_panel_geometry(start, end, _collapsed)

    def _schedule_application_deactivate_check(self):
        """避免 Tool 窗口产生的瞬时失活打断箭头收起动画。"""
        if self._deactivate_check_pending:
            return
        self._deactivate_check_pending = True

        def _check():
            self._deactivate_check_pending = False
            app = QApplication.instance()
            if app is not None and app.applicationState() != Qt.ApplicationActive:
                self._force_hide_search_suggestions()

        QTimer.singleShot(220, _check)

    def _force_hide_search_suggestions(self):
        """切页/失活时立即清理顶层悬浮窗和未完成动画。"""
        animation = self._height_animations.pop("panel", None)
        if animation is not None:
            animation.stop()
        self._suggestions_open = False
        self.searchSuggestPanel.hide()
        self.searchDropDownBtn.setIcon(_CHEVRON_DOWN)
        self.searchDropDownBtn.setToolTip(
            L("展开搜索推荐", "Show search suggestions"))
        self.searchDropDownBtn.setAccessibleName(
            L("展开搜索推荐", "Show search suggestions"))

    def _on_search_theme_changed(self, *_args):
        """主题切换时立即刷新已打开下拉层，而不是等到下次打开。"""
        self.searchSuggestPanel.setBackgroundColor(
            self.searchSuggestPanel._normalBackgroundColor())
        self._refresh_search_suggestions()
        self.searchSuggestPanel.update()

    def _focus_search(self):
        """Ctrl+F 聚焦搜索框，但不违背“点击才弹出”的交互。"""
        self.searchEdit.setFocus()
        self.searchEdit.selectAll()

    def _submit_search(self, term):
        """回车或放大镜执行搜索、保存历史并收起下拉层。"""
        self._commit_search(term)
        self._hide_search_suggestions()

    def _commit_pending_search(self):
        """把停止输入后的有效关键词写入搜索历史。"""
        term = self.searchEdit.text().strip()
        if term:
            self._commit_search(term)

    def _on_search_text_changed(self, text):
        """实时筛选，并在停止输入后把关键词加入搜索历史。"""
        self._apply_filter(text)
        self._search_commit_timer.stop()
        if str(text or "").strip():
            self._search_commit_timer.start()

    def _install_count(self, entry) -> int:
        """近 30 天的真实安装次数；不在仓库里的插件没有统计，记 0。"""
        file_value = entry.get("file")
        if not isinstance(file_value, str):
            return 0
        return int(self._install_counts.get(file_value, 0) or 0)

    def _on_install_counts(self, counts):
        self._install_counts = dict(counts or {})
        self._update_hot_searches(self._hot_entries)

    def _update_hot_searches(self, entries):
        """热门词：按近 30 天安装次数排序，次数相同（含还没有统计）时新上架的在前。"""
        self._hot_entries = list(entries or [])

        def score(entry):
            return self._install_count(entry), str(entry.get("date", "") or "")

        hot = []
        folded_terms = set()
        for entry in sorted(self._hot_entries, key=score, reverse=True):
            term = _i18n_text(entry.get("name", "")).strip()
            folded = term.casefold()
            if term and folded not in folded_terms:
                hot.append((term, self._install_count(entry)))
                folded_terms.add(folded)
            if len(hot) >= self._MAX_HOT_SEARCHES:
                break
        self._hot_searches = hot
        self._refresh_search_suggestions()

    @staticmethod
    def _install_count_text(count: int) -> str:
        if count <= 0:
            return ""
        if count == 1:
            return L("1 次安装", "1 install")
        return L(f"{count:,} 次安装", f"{count:,} installs")

    def _refresh_search_suggestions(self):
        """刷新热门词和历史列表，不重建控件以避免界面闪动。"""
        any_counts = any(count > 0 for _term, count in self._hot_searches)
        # jsDelivr publishes its statistics about two days late, so a fresh
        # install is not counted at once; say so rather than look broken.
        self.hotNote.setText(
            L("按近 30 天安装次数", "By installs in the last 30 days") if any_counts
            else L("安装统计约有两天延迟，暂按上架时间排列",
                   "Install stats lag about two days; newest first for now"))
        self.hotNote.setToolTip(
            L("安装次数来自 jsDelivr 的公开下载统计，每天更新一次，新的安装大约两天后才会算进来。",
              "Install counts come from jsDelivr's public download statistics, which update "
              "daily; a new install shows up after about two days."))
        for i, (btn, _rank_label, term_label, count_label) in enumerate(self._hot_buttons):
            visible = i < len(self._hot_searches)
            term, count = self._hot_searches[i] if visible else ("", 0)
            btn.setProperty("searchTerm", term)
            term_label.setText(term)
            term_label.setStyleSheet(
                "color: #FFFFFF; background: transparent;"
                if isDarkTheme()
                else "color: #1F1F1F; background: transparent;")
            count_label.setText(self._install_count_text(count))
            count_label.setStyleSheet(
                "color: rgba(255, 255, 255, 0.62); background: transparent;"
                if isDarkTheme()
                else "color: rgba(0, 0, 0, 0.55); background: transparent;")
            btn.setToolTip(term)
            btn.setAccessibleName(
                L(f"第 {i + 1} 名热门搜索：{term}",
                  f"Popular search #{i + 1}: {term}"))
            btn.setVisible(visible)

        has_history = bool(self._search_history)
        show_hot = bool(self._hot_searches)       # shown next to the history too
        show_empty = not show_hot and not has_history
        self.hotTitle.setVisible(show_hot)
        self.hotNote.setVisible(show_hot)
        self.hotHost.setVisible(show_hot)
        self.historyTitle.setVisible(has_history)
        self.historyClearBtn.setVisible(has_history)
        self.historyHost.setVisible(has_history or show_empty)
        self.historyEmpty.setVisible(show_empty)
        for i, (row, term_btn, delete_btn) in enumerate(self._history_rows):
            visible = i < len(self._search_history)
            term = self._search_history[i] if visible else ""
            term_btn.setProperty("searchTerm", term)
            term_btn.setText(term)
            term_btn.setToolTip(term)
            term_btn.setAccessibleName(
                L(f"搜索历史：{term}", f"Search history: {term}"))
            delete_btn.setProperty("searchTerm", term)
            delete_btn.setAccessibleName(
                L(f"删除搜索历史：{term}", f"Delete search history: {term}"))
            row.setVisible(visible)
        QTimer.singleShot(0, self._resize_open_search_suggestions)

    def _commit_search(self, term):
        """按回车或点击搜索按钮后，将有效关键词写入最近历史。"""
        self._search_commit_timer.stop()
        term = str(term or "").strip()[:100]
        if not term:
            return
        folded = _normalize_search_text(term)
        old_history = list(self._search_history)
        self._search_history = [x for x in self._search_history
                                if _normalize_search_text(x) != folded]
        self._search_history.insert(0, term)
        del self._search_history[self._MAX_SEARCH_HISTORY:]
        if self._search_history != old_history:
            settings.set("plugin_market_search_history", self._search_history)
        self._refresh_search_suggestions()

    def _use_search_term(self, term):
        term = str(term or "").strip()
        if not term:
            return
        self.searchEdit.setText(term)
        self._commit_search(term)
        self._hide_search_suggestions()
        self.searchEdit.setFocus()

    def _delete_search_term(self, term):
        folded = _normalize_search_text(str(term or "").strip())
        self._search_history = [x for x in self._search_history
                                if _normalize_search_text(x) != folded]
        settings.set("plugin_market_search_history", self._search_history)
        self._refresh_search_suggestions()

    def _clear_search_history(self):
        self._search_history = []
        settings.set("plugin_market_search_history", [])
        self._refresh_search_suggestions()

    def is_favorite(self, pid: str) -> bool:
        return str(pid or "") in self._favorite_ids

    def toggle_favorite(self, pid: str):
        pid = str(pid or "").strip()
        if not pid:
            return
        if pid in self._favorite_ids:
            self._favorite_ids.remove(pid)
        else:
            self._favorite_ids.add(pid)
        settings.set("plugin_market_favorites", sorted(self._favorite_ids))
        for card in self._all_cards:
            if str(card.entry.get("id", "")) == pid:
                card.refresh_favorite()
                break
        self._update_filter_counts()
        self._apply_filter(self.searchEdit.text())

    def _set_favorites_only(self, checked):
        self._favorites_only = bool(checked)
        self.favoriteFilterAction.setChecked(self._favorites_only)
        self._update_filter_tooltip()
        self._apply_filter(self.searchEdit.text())

    def _set_filter_cat(self, key: str):
        """切换类型筛选，并同步菜单勾选状态。"""
        self._filter_cat = key
        for act, k in self._filter_actions:
            act.setChecked(k == key)
        self._update_filter_tooltip()
        self._apply_filter(self.searchEdit.text())

    def _set_filter_state(self, key: str):
        """按未安装、已安装、可更新或已禁用筛选。"""
        self._filter_state = key
        for act, k in self._filter_state_actions:
            act.setChecked(k == key)
        self._update_filter_tooltip()
        self._apply_filter(self.searchEdit.text())

    def _reset_market_filters(self):
        self._filter_cat = "all"
        self._filter_state = "all"
        self._favorites_only = False
        for act, key in self._filter_actions:
            act.setChecked(key == "all")
        for act, key in self._filter_state_actions:
            act.setChecked(key == "all")
        self.favoriteFilterAction.setChecked(False)
        self.searchEdit.clear()
        self._update_filter_tooltip()
        self._apply_filter("")

    def _update_filter_tooltip(self):
        parts = []
        if self._filter_cat != "all":
            parts.append(_i18n_text(_category_label(self._filter_cat)))
        state_labels = {
            "absent": ("未安装", "Not installed"),
            "same": ("已安装", "Installed"),
            "update": ("可更新", "Updates available"),
            "disabled": ("已禁用", "Disabled"),
        }
        if self._filter_state != "all":
            parts.append(_i18n_text(state_labels[self._filter_state]))
        if self._favorites_only:
            parts.append(L("仅看收藏", "Favorites only"))
        if parts:
            self.filterBtn.setToolTip(
                L(f"当前筛选：{'、'.join(parts)}",
                  f"Active filters: {', '.join(parts)}"))
        else:
            self.filterBtn.setToolTip(
                L("筛选类型和安装状态", "Filter by type and install status"))

    def _update_filter_counts(self):
        """在筛选菜单中显示各分类和安装状态的实时数量。"""
        cards = getattr(self, "_all_cards", [])
        category_counts = {"all": len(cards)}
        state_counts = {"all": len(cards)}
        for card in cards:
            category = _entry_category(card.entry)
            state = MarketClient.installed_state(card.entry)
            category_counts[category] = category_counts.get(category, 0) + 1
            state_counts[state] = state_counts.get(state, 0) + 1

        for act, key in self._filter_actions:
            label = L("全部", "All") if key == "all" \
                else _i18n_text(_category_label(key))
            act.setText(f"{label} ({category_counts.get(key, 0)})")
        state_labels = {
            "all": ("全部状态", "All states"),
            "absent": ("未安装", "Not installed"),
            "same": ("已安装", "Installed"),
            "update": ("可更新", "Updates available"),
            "disabled": ("已禁用", "Disabled"),
        }
        for act, key in self._filter_state_actions:
            label = _i18n_text(state_labels[key])
            act.setText(f"{label} ({state_counts.get(key, 0)})")
        self.favoriteFilterAction.setText(
            L(f"仅看收藏 ({len(self._favorite_ids)})",
              f"Favorites only ({len(self._favorite_ids)})"))

    def _toggle_sort_dir(self):
        """正序 ↔ 倒序切换。"""
        self._sort_desc = not self._sort_desc
        if self._sort_desc:
            self.sortDirBtn.setIcon(FIF.DOWN)
            self.sortDirBtn.setToolTip(L("倒序（点击切换）", "Descending (click to toggle)"))
        else:
            self.sortDirBtn.setIcon(FIF.UP)
            self.sortDirBtn.setToolTip(L("正序（点击切换）", "Ascending (click to toggle)"))
        self._resort()

    def _sort_key(self, card):
        """当前排序方式对应的比较键。"""
        from app.services.updater import _ver_tuple
        e = card.entry
        idx = self.sortCombo.currentIndex()
        if idx == 0:      # 时间（缺日期视为最旧）
            return str(e.get("date", "") or "0000-00-00")
        if idx == 1:      # 名称（取当前语言文本）
            return _i18n_text(e.get("name", "")).lower()
        if idx == 2:      # 作者
            return str(e.get("author", "") or "").lower()
        if idx == 3:
            return _ver_tuple(str(e.get("version", "0") or "0"))  # 版本
        # 状态倒序：可更新 > 未安装 > 已禁用 > 已安装
        state_order = {"same": 1, "disabled": 2, "absent": 3, "update": 4}
        return state_order.get(MarketClient.installed_state(e), 0)

    def _resort(self):
        """按当前排序方式重排卡片（保持末尾 stretch）。"""
        if not self._all_cards:
            return
        cards = sorted(self._all_cards, key=self._sort_key,
                       reverse=self._sort_desc)
        self._all_cards = cards
        for card in cards:
            self.listLay.removeWidget(card)
        for card in cards:
            self.listLay.insertWidget(self.listLay.count() - 1, card)

    def _apply_filter(self, kw: str):
        raw_kw = str(kw or "").strip()
        tokens = [x for x in _normalize_search_text(raw_kw).split() if x]
        visible = 0
        for card in getattr(self, "_all_cards", []):
            e = card.entry
            ok = True
            if self._filter_cat != "all":
                ok = (_entry_category(e) == self._filter_cat)
            state = MarketClient.installed_state(e)
            if ok and self._filter_state != "all":
                ok = state == self._filter_state
            if ok and self._favorites_only:
                ok = str(e.get("id", "")) in self._favorite_ids
            if ok and tokens:
                name = e.get("name", "")
                if isinstance(name, (tuple, list)):
                    name = " ".join(str(x) for x in name)
                desc = e.get("description", "")
                if isinstance(desc, (tuple, list)):
                    desc = " ".join(str(x) for x in desc)
                cat_label = " ".join(_category_label(_entry_category(e)))
                state_labels = {
                    "absent": "未安装 not installed available",
                    "same": "已安装 installed",
                    "update": "可更新 update updates available",
                    "disabled": "已禁用 disabled",
                }
                hay = _normalize_search_text(" ".join(str(x) for x in (
                    e.get("id", ""), name, e.get("author", ""), desc,
                    e.get("version", ""), cat_label, state_labels.get(state, ""))))
                ok = all(token in hay for token in tokens)
            card.setVisible(ok)
            if ok:
                visible += 1
        self._last_visible_count = visible
        total = len(getattr(self, "_all_cards", []))
        active = bool(tokens or self._filter_cat != "all"
                      or self._filter_state != "all" or self._favorites_only)
        src = self._market_source_suffix
        incompatible = self._incompatible_count
        upgrade_zh = f"；另有 {incompatible} 个需要升级 FlowBench" if incompatible else ""
        upgrade_en = (f"; {incompatible} require a newer FlowBench"
                      if incompatible else "")
        if not total:
            self.statusLabel.setText(L(
                f"暂无可安装的插件{src}{upgrade_zh}",
                f"No installable plugins{src}{upgrade_en}"))
        elif active and not visible:
            detail_zh = f"“{raw_kw}”" if raw_kw else "当前筛选条件"
            detail_en = f'"{raw_kw}"' if raw_kw else "the active filters"
            self.statusLabel.setText(L(
                f"没有找到匹配 {detail_zh} 的插件，可清空关键词或筛选",
                f"No plugins match {detail_en}; clear the query or filters"))
        elif active:
            self.statusLabel.setText(L(
                f"匹配 {visible} / {total} 个插件{src}{upgrade_zh}",
                f"{visible} / {total} plugin(s) match{src}{upgrade_en}"))
        else:
            self.statusLabel.setText(L(
                f"共 {total} 个插件{src}{upgrade_zh}",
                f"{total} plugin(s){src}{upgrade_en}"))

    def _on_error(self, msg):
        self.spinner.hide()
        self.statusLabel.setText(L(
            f"市场加载失败：{msg}\n请检查网络后点击\"刷新\"。",
            f"Failed to load marketplace: {msg}\nCheck your network and hit Refresh."))

    def _refresh_buttons(self):
        for i in range(self.listLay.count()):
            w = self.listLay.itemAt(i).widget()
            if isinstance(w, MarketCard):
                w.refresh_state()
        # 安装、更新、启用后，状态筛选结果也要立即重新计算。
        self._update_filter_counts()
        self._apply_filter(self.searchEdit.text())
        if self.sortCombo.currentIndex() == 4:
            self._resort()

    def install_card(self, card: MarketCard):
        self._active_card = card
        self.client.install(card.entry)

    def unpublish_card(self, card: MarketCard):
        """下架插件：需要 GitHub 授权后创建移除 PR。"""
        entry = card.entry
        pid = entry.get("id", "?")
        if not self.can_unpublish(entry):
            InfoBar.warning(
                L("无权下架", "Unpublish not allowed"),
                L("只有插件作者可以下架自己的插件。",
                  "Only the plugin publisher can unpublish this plugin."),
                parent=self.window(), duration=5000)
            return
        token = (settings.github_token or "").strip()
        card.unpubBtn.setEnabled(False)
        card.unpubBtn.setText(L("下架中…", "Unpublishing…"))
        if not token:
            if GITHUB_OAUTH_CLIENT_ID:
                self._unpublish_device_flow(entry, card)
            else:
                card.unpubBtn.setEnabled(True)
                card.unpubBtn.setText(L("下架", "Unpublish"))
                InfoBar.warning(L("需要授权", "Authorization required"),
                                L("请先发布一个插件完成授权，下架也需要 GitHub 身份。",
                                  "Publish a plugin first to complete authorization; unpublish also requires GitHub identity."),
                                parent=self.window(), duration=6000)
            return
        # 后台检查 scope 后执行下架
        def _check():
            try:
                scopes = gh_check_scopes(token)
                if "workflow" not in scopes and gh_can_push(token):
                    settings.set("github_token", "")
                    self._unpublish_device_flow(entry, card)
                    return
            except Exception:
                # 401/网络异常：保留本地作者身份，但要求重新授权后再下架。
                settings.set("github_token", "")
                if GITHUB_OAUTH_CLIENT_ID:
                    self._unpublish_device_flow(entry, card)
                    return
            self._do_unpublish(entry, token, card)
        threading.Thread(target=_check, daemon=True).start()

    def _unpublish_device_flow(self, entry, card):
        """无 Token 时走浏览器设备授权后再下架。"""
        def _work():
            try:
                d = device_flow_start()
                code = str(d["user_code"])
                uri = str(d.get("verification_uri_complete")
                          or f"{d['verification_uri']}?user_code={code}")
                self.unpubAuthNeeded.emit(code, uri)
                token = device_flow_poll(d["device_code"],
                                         d.get("interval", 5),
                                         d.get("expires_in", 900))
                settings.set("github_token", token)
                settings.set("github_login", gh_get_user(token))
                self.unpubAuthOk.emit(entry, token, card)
            except Exception as e:
                self.unpubErr.emit(str(e), card)
        threading.Thread(target=_work, daemon=True).start()

    def _on_unpub_auth_needed(self, code, uri):
        """主线程：打开浏览器进行设备授权。"""
        QDesktopServices.openUrl(QUrl(uri))
        InfoBar.info(L("需要授权", "Authorization required"),
                     L(f"请在浏览器中授权（代码 {code}），授权后将自动继续下架。",
                       f"Authorize in browser (code {code}), unpublish will continue automatically."),
                     parent=self.window(), duration=10000)

    def _do_unpublish(self, entry, token, card):
        """后台线程执行下架。"""
        def _work():
            try:
                url, is_direct = self._github_unpublish(token, entry)
                self.unpubOk.emit(url, is_direct, card)
            except Exception as e:
                self.unpubErr.emit(str(e), card)
        threading.Thread(target=_work, daemon=True).start()

    def _on_unpub_ok(self, url, is_direct, card):
        card.unpubBtn.setEnabled(True)
        card.unpubBtn.setText(L("下架", "Unpublish"))
        pid = card.entry.get("id", "?")
        if is_direct:
            InfoBar.success(L("下架成功", "Unpublished"),
                            L(f"插件 {pid} 已从市场下架，其他用户刷新后不再显示。",
                              f"Plugin {pid} removed from marketplace. It disappears for others after refresh."),
                            parent=self.window(), duration=6000)
            # 直接下架成功，从列表移除卡片
            self._remove_card_from_list(card)
        else:
            InfoBar.success(L("下架成功", "Unpublished"),
                            L(f"插件 {pid} 的下架请求已提交，将在几秒内自动生效。",
                              f"Unpublish request for {pid} submitted. It will take effect within seconds."),
                            parent=self.window(), duration=6000)

    def _remove_card_from_list(self, card):
        """从市场列表中移除一张卡片（下架成功后调用）。"""
        card.setParent(None)
        if card in self._all_cards:
            self._all_cards.remove(card)
        self._apply_filter()
        count = len([c for c in self._all_cards if isinstance(c, MarketCard)])
        self.statusLabel.setText(
            L(f"共 {count} 个插件", f"{count} plugin(s) total"))

    def _on_unpub_err(self, msg, card):
        card.unpubBtn.setEnabled(True)
        card.unpubBtn.setText(L("下架", "Unpublish"))
        if "401" in msg or "Bad credentials" in msg:
            settings.set("github_token", "")
        InfoBar.error(L("下架失败", "Unpublish failed"), msg,
                      parent=self.window(), duration=8000)

    @staticmethod
    def _github_unpublish(token, entry):
        """下架插件。

        - 有写权限：直接从 master 删除索引条目和插件文件，立即生效。
        - 无写权限：Fork + 分支 + PR。
        返回 (url, is_direct)。
        """
        pid = entry.get("id", "?")
        headers = gh_headers(token)
        upstream = f"{_GH_API}/repos/{_REPO_OWNER}/{_REPO_NAME}"
        username = gh_get_user(token)
        publisher = str(entry.get("publisher", "") or entry.get("author", ""))
        if publisher.strip() and publisher.strip().casefold() != username.strip().casefold():
            raise PermissionError("GitHub account is not the plugin publisher")
        can_push = gh_can_push(token)
        idx_path = f"{_MARKET_DIR}/plugins-index.json"
        plugin_path = f"{_MARKET_DIR}/{pid}.py"

        def _remove_from_index(repo_api, branch):
            idx_data, idx_sha = gh_get_json_file(repo_api, idx_path, branch, headers)
            if idx_data is None:
                raise Exception("plugins-index.json not found")
            before = len(idx_data.get("plugins", []))
            idx_data["plugins"] = [p for p in idx_data.get("plugins", [])
                                   if p.get("id") != pid]
            if before == len(idx_data["plugins"]):
                raise Exception(f"Plugin {pid} not found in index")
            new_b64 = base64.b64encode(
                json.dumps(idx_data, ensure_ascii=False, indent=2).encode()).decode()
            gh_put_file(repo_api, idx_path, new_b64,
                        f"Unpublish plugin: {pid}", branch, headers, idx_sha)

        # ---------- 路径 A：直接提交到 master ----------
        if can_push:
            _remove_from_index(upstream, _REPO_BRANCH)
            # 同时删除插件源码文件（如果存在）
            file_sha = gh_file_sha(upstream, plugin_path, _REPO_BRANCH, headers)
            if file_sha:
                try:
                    gh_delete_file(upstream, plugin_path,
                                   f"Remove plugin file: {pid}",
                                   _REPO_BRANCH, headers, file_sha)
                except Exception:
                    pass
            return (f"https://github.com/{_REPO_OWNER}/{_REPO_NAME}/commits/{_REPO_BRANCH}",
                    True)

        # ---------- 路径 B：Fork + PR ----------
        r = requests.post(f"{upstream}/forks", headers=headers, timeout=30)
        if r.status_code not in (200, 202):
            r.raise_for_status()
        fork_api = f"{_GH_API}/repos/{r.json()['full_name']}"
        fork_full = r.json()["full_name"]

        import time as _t
        for _ in range(10):
            if requests.get(fork_api, headers=headers, timeout=10).status_code == 200:
                break
            _t.sleep(1)
        try:
            requests.post(f"{fork_api}/merge-upstream", headers=headers,
                          json={"branch": _REPO_BRANCH}, timeout=15)
        except Exception:
            pass

        r = requests.get(f"{fork_api}/git/refs/heads/{_REPO_BRANCH}",
                         headers=headers, timeout=10)
        r.raise_for_status()
        master_sha = r.json()["object"]["sha"]
        branch = f"unpublish-plugin/{pid}"
        requests.delete(f"{fork_api}/git/refs/heads/{branch}",
                        headers=headers, timeout=10)
        r = requests.post(f"{fork_api}/git/refs", headers=headers, json={
            "ref": f"refs/heads/{branch}", "sha": master_sha,
        }, timeout=10)
        r.raise_for_status()

        _remove_from_index(fork_api, branch)

        r = requests.post(f"{upstream}/pulls", headers=headers, json={
            "title": f"Unpublish plugin: {pid}",
            "head": f"{fork_full.split('/')[0]}:{branch}",
            "base": _REPO_BRANCH,
            "body": L(
                f"## 下架插件\n\n- **插件 ID**: {pid}\n- **名称**: {entry.get('name', '')}\n\n"
                f"由 FlowBench 插件市场一键下架功能自动创建。",
                f"## Unpublish plugin\n\n- **Plugin ID**: {pid}\n- **Name**: {entry.get('name', '')}\n\n"
                f"Created automatically by FlowBench Marketplace."),
        }, timeout=15)
        if r.status_code not in (200, 201):
            raise Exception(f"PR creation failed ({r.status_code}): {r.text}")
        return r.json()["html_url"], False

    def _on_downloaded(self, entry, tmp_path):
        # Save the marketplace icon before import_from() emits the loaded
        # signal.  MainWindow resolves the navigation icon from this file
        # while handling that signal; saving it afterwards makes the icon
        # appear only after a restart.
        self._save_plugin_icon(entry)
        ok, msg = plugin_manager.import_from(tmp_path)
        try:
            shutil.rmtree(os.path.dirname(tmp_path), ignore_errors=True)
        except Exception:
            pass
        if ok:
            InfoBar.success(L("安装成功", "Installed"),
                            L(f"{entry.get('id')} 已安装并启用",
                              f"{entry.get('id')} installed and enabled"),
                            parent=self.window(), duration=4000)
        else:
            InfoBar.error(L("安装失败", "Install failed"), msg,
                          parent=self.window(), duration=6000)
        self._refresh_buttons()

    @staticmethod
    def _save_plugin_icon(entry: dict):
        """把市场条目的图标（data URI 或 URL）保存为 {pid}.icon.png。"""
        pid = entry.get("id", "")
        if not is_valid_market_plugin_id(pid):
            return
        icon_val = entry.get("icon", "")
        if not icon_val:
            return
        try:
            data = None
            if isinstance(icon_val, str) and icon_val.startswith("data:"):
                data = decode_data_uri_icon(icon_val)
            elif isinstance(icon_val, str) and icon_val.startswith(("http://", "https://")):
                data = _download_icon_bytes(icon_val)
            if data:
                from app.services.plugins import plugins_dir
                dst = os.path.join(plugins_dir(), f"{pid}.icon.png")
                with open(dst, "wb") as f:
                    f.write(data)
        except Exception:
            pass

    def _on_download_failed(self, pid, msg):
        InfoBar.error(L("下载失败", "Download failed"),
                      L(f"{pid}：{msg}", f"{pid}: {msg}"),
                      parent=self.window(), duration=6000)
        self._refresh_buttons()

    def _publish(self):
        PublishDialog(self.window()).exec()


# ---------- 插件主页面（Pivot 容器） ----------


class MarketView(QWidget):
    """插件页面：Pivot 切换本地插件 / 插件市场。

    页面本身不再滚动（标题和 Pivot 固定），各子页内部自行滚动列表。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("marketView")

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 22, 28, 28)
        root.setSpacing(14)

        root.addWidget(SubtitleLabel(L("插件", "Plugins"), self))

        self.pivot = Pivot(self)
        self.stack = QStackedWidget(self)
        # 关键：内部 QStackedWidget 必须显式透明，否则主题切换时
        # 子类 QWidget 会回退到调色板绘制，叠加 Mica 出现底色反转
        # （浅色模式黑底、深色模式白底）。
        self.stack.setStyleSheet("QStackedWidget{background: transparent; border: none;}")
        self.stack.setAttribute(Qt.WA_StyledBackground, True)

        self.localPage = LocalPluginsPage(self)
        self.marketPage = PluginMarketPage(self)
        # 两个页面容器也显式透明，避免同样的回退底色问题
        self.localPage.setStyleSheet("#localPluginsPage{background: transparent;}")
        self.marketPage.setStyleSheet("#pluginMarketPage{background: transparent;}")
        self.localPage.setAttribute(Qt.WA_StyledBackground, True)
        self.marketPage.setAttribute(Qt.WA_StyledBackground, True)

        self.stack.addWidget(self.localPage)
        self.stack.addWidget(self.marketPage)

        self.pivot.addItem(
            routeKey="local",
            text=L("本地插件", "Local Plugins"),
            onClick=lambda: self.stack.setCurrentWidget(self.localPage))
        self.pivot.addItem(
            routeKey="market",
            text=L("插件市场", "Marketplace"),
            onClick=lambda: self.stack.setCurrentWidget(self.marketPage))
        self.pivot.setCurrentItem("market")
        self.stack.setCurrentWidget(self.marketPage)

        root.addWidget(self.pivot)
        root.addWidget(self.stack, 1)


# ---------- 发布对话框 ----------

def _scan_plugin_meta(path):
    """AST 解析插件源码，提取 FlowBenchPlugin 子类的类属性元数据
    （name/version/author/description/icon/category）。
    不执行插件代码，禁用/加载失败的插件也能安全读取。"""
    import ast
    meta = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())
    except Exception:
        return meta
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        bases = {b.id for b in node.bases if isinstance(b, ast.Name)}
        if "FlowBenchPlugin" not in bases:
            continue
        for item in node.body:
            if (isinstance(item, ast.Assign) and len(item.targets) == 1
                    and isinstance(item.targets[0], ast.Name)):
                key = item.targets[0].id
                val = item.value
                if isinstance(val, ast.Constant):
                    meta.setdefault(key, val.value)
                elif isinstance(val, (ast.Tuple, ast.List)):
                    parts = [e.value for e in val.elts
                             if isinstance(e, ast.Constant)]
                    if len(parts) == len(val.elts):
                        meta.setdefault(key, tuple(parts))
        if meta:
            break
    return meta


class PublishDialog(MessageBoxBase):
    """发布向导：生成条目 JSON，支持一键通过 GitHub API 提交 PR。"""

    # 工作线程拿到设备码后发回主线程，由主线程打开浏览器（跨线程 GUI 操作必须用信号）
    deviceReady = Signal(str, str)   # user_code, verification_uri
    proceedWithPublish = Signal(object, str)  # entry, token（授权成功后回主线程发布）
    reauthNeeded = Signal(object)    # entry（旧 token scope 不足，需重新授权）
    statusUpdate = Signal(str)       # 发布过程中的状态文字
    publishOk = Signal(str)          # pr_url
    publishFailed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(L("发布插件到市场", "Publish to Marketplace"))
        self.widget.setMinimumWidth(620)
        self._icon_data_uri = ""
        self.deviceReady.connect(self._on_device_ready)
        self.proceedWithPublish.connect(self._do_publish)
        self.reauthNeeded.connect(self._device_login_then_publish)
        self.publishOk.connect(self._on_publish_ok)
        self.publishFailed.connect(self._on_publish_err)

        self.titleLabel = StrongBodyLabel(
            L("发布插件到市场", "Publish to Marketplace"), self.widget)
        self.viewLayout.addWidget(self.titleLabel)

        steps = BodyLabel(L(
            "上架流程（免费，无需服务器）：\n"
            "1. 选择要发布的本地插件和图标；\n"
            "2. 首次点击\"一键发布\"会打开浏览器，点一次\"授权\"即可；\n"
            "3. 之后每次发布只需一键，PR 合并后即上架。",
            "How publishing works (free, no server):\n"
            "1. Pick the local plugin and icon;\n"
            "2. The first \"Publish\" click opens your browser for a one-time authorization;\n"
            "3. After that every publish is one click. It goes live once the PR is merged."),
            self.widget)
        steps.setWordWrap(True)
        self.viewLayout.addWidget(steps)

        # 本地插件选择（禁用/未加载的插件也可发布：元数据从源码 AST 读取）
        row = QHBoxLayout()
        row.addWidget(BodyLabel(L("本地插件", "Local plugins"), self.widget))
        self.combo = ComboBox(self.widget)
        self._combo_pids = []
        for rec in (plugin_manager.records() or plugin_manager.discover()):
            meta = {} if rec.plugin is not None else _scan_plugin_meta(rec.path)
            name = (meta.get("name") or rec.display_name or rec.pid)
            if isinstance(name, (tuple, list)):
                name = _i18n_text(name) if len(name) == 2 else (name[0] if name else rec.pid)
            ver = meta.get("version") or rec.display_version or "?"
            label = f"{name} (v{ver})"
            if rec.plugin is None:   # 状态标记：让用户知道为何未加载
                mark = {"disabled": L("已禁用", "disabled"),
                        "error": L("加载失败", "load failed")}.get(
                            rec.state, L("未加载", "not loaded"))
                label += f" · {mark}"
            self.combo.addItem(label)
            self._combo_pids.append(rec.pid)
        self.combo.currentIndexChanged.connect(self._on_plugin_changed)
        row.addWidget(self.combo, 1)
        self.viewLayout.addLayout(row)

        # 分类选择（自动识别，可手动改）
        crow = QHBoxLayout()
        crow.addWidget(BodyLabel(L("插件分类", "Category"), self.widget))
        self.catCombo = ComboBox(self.widget)
        self.catCombo.addItems(
            _i18n_text(label) for _, label in _PLUGIN_CATEGORIES)
        self.catCombo.currentIndexChanged.connect(lambda _i: self._generate())
        crow.addWidget(self.catCombo, 1)
        self.viewLayout.addLayout(crow)

        # 图标选择
        irow = QHBoxLayout()
        self.iconPreview = QLabel(L("无图标", "No icon"), self.widget)
        self.iconPreview.setFixedSize(_ICON_SIZE, _ICON_SIZE)
        self.iconPreview.setAlignment(Qt.AlignCenter)
        irow.addWidget(self.iconPreview)
        pickBtn = PushButton(L("选择图标 (PNG/JPG)…", "Pick Icon (PNG/JPG)…"), self.widget)
        pickBtn.clicked.connect(self._pick_icon)
        irow.addWidget(pickBtn)
        irow.addStretch(1)
        self.viewLayout.addLayout(irow)

        # GitHub Token（仅在既无缓存又未配置浏览器一键授权时才显示）
        self.tokenRow = QWidget(self.widget)
        trow = QHBoxLayout(self.tokenRow)
        trow.setContentsMargins(0, 0, 0, 0)
        trow.addWidget(BodyLabel(L("GitHub Token", "GitHub Token"), self.tokenRow))
        from qfluentwidgets import PasswordLineEdit
        self.tokenEdit = PasswordLineEdit(self.tokenRow)
        self.tokenEdit.setPlaceholderText(
            L("ghp_xxxxxxxxxxxx（public_repo, workflow 权限）",
              "ghp_xxxxxxxxxxxx (public_repo, workflow)"))
        saved = settings.github_token or ""
        if saved:
            self.tokenEdit.setText(saved)
        trow.addWidget(self.tokenEdit, 1)
        tokenLink = PushButton(L("获取 Token", "Get Token"), self.tokenRow)
        tokenLink.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl("https://github.com/settings/tokens/new?scopes=public_repo,workflow&description=FlowBench%20Plugin%20Publish")))
        trow.addWidget(tokenLink)
        self.viewLayout.addWidget(self.tokenRow)
        if settings.github_token or GITHUB_OAUTH_CLIENT_ID:
            self.tokenRow.hide()

        # JSON 预览
        self.jsonBox = TextEdit(self.widget)
        self.jsonBox.setReadOnly(True)
        self.jsonBox.setFixedHeight(160)
        self.viewLayout.addWidget(self.jsonBox)

        # 按钮行
        btns = QHBoxLayout()
        self.publishBtn = PrimaryPushButton(
            L("一键发布", "Publish (1-Click)"), self.widget)
        self.publishBtn.clicked.connect(self._one_click_publish)
        btns.addWidget(self.publishBtn)
        copyBtn = PushButton(L("复制 JSON", "Copy JSON"), self.widget)
        copyBtn.clicked.connect(self._copy)
        btns.addWidget(copyBtn)
        openBtn = PushButton(L("手动提交页面", "Manual Submission"), self.widget)
        openBtn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(INDEX_EDIT_URL)))
        btns.addWidget(openBtn)
        btns.addStretch(1)
        self.viewLayout.addLayout(btns)

        # 设备授权码展示区（拿到设备码时显示，点击即复制）
        self.codePanel = QWidget(self.widget)
        self.codePanel.setObjectName("codePanel")
        self.codePanel.setStyleSheet(
            "#codePanel{background:rgba(0,120,212,0.12); border:1px solid rgba(0,120,212,0.4);"
            " border-radius:8px;}")
        cpLay = QVBoxLayout(self.codePanel)
        cpLay.setContentsMargins(14, 10, 14, 10)
        cpLay.setSpacing(6)
        codeHint = BodyLabel(L(
            "请在浏览器中输入以下授权码，或直接点击复制：",
            "Enter this code in your browser, or click to copy:"), self.codePanel)
        cpLay.addWidget(codeHint)
        # 同一行：授权码（左） + 重新打开浏览器按钮（右）
        codeRow = QHBoxLayout()
        codeRow.setSpacing(10)
        # 可点击的授权码区域：整块都能点，hover 高亮反馈
        self.codeClickBox = QWidget(self.codePanel)
        self.codeClickBox.setObjectName("codeClickBox")
        self.codeClickBox.setCursor(Qt.PointingHandCursor)
        self.codeClickBox.setToolTip(L("点击复制授权码", "Click to copy code"))
        cbl = QHBoxLayout(self.codeClickBox)
        cbl.setContentsMargins(14, 6, 14, 6)
        cbl.setSpacing(8)
        self.codeLabel = QLabel("------", self.codeClickBox)
        self.codeLabel.setAlignment(Qt.AlignCenter)
        self.codeLabel.setStyleSheet(
            "font-size:20px; font-weight:700; letter-spacing:2px;"
            "font-family:'Consolas','Courier New',monospace; color:#0078D4;")
        # 授权码等宽、不可压缩，否则会被布局挤压导致截断
        self.codeLabel.setMinimumWidth(
            self.codeLabel.fontMetrics().horizontalAdvance("W" * 9) + 6)
        cbl.addWidget(self.codeLabel)
        copyIco = IconWidget(FIF.COPY, self.codeClickBox)
        copyIco.setFixedSize(16, 16)
        cbl.addWidget(copyIco)
        # 预留足够宽度：label 最小宽 + 左右边距 + 间距 + 复制图标
        self.codeClickBox.setMinimumWidth(
            self.codeLabel.minimumWidth() + 28 + 8 + 16)
        self.codeClickBox.setStyleSheet(
            "#codeClickBox{background:rgba(0,120,212,0.10); border-radius:6px;"
            " border:1px dashed rgba(0,120,212,0.35);}"
            "#codeClickBox:hover{background:rgba(0,120,212,0.22);"
            " border:1px solid #0078D4;}")
        self.codeClickBox.mousePressEvent = lambda _e: self._copy_device_code()
        codeRow.addWidget(self.codeClickBox)
        codeRow.addStretch(1)
        self.openBrowserBtn = PushButton(
            L("重新打开浏览器", "Reopen Browser"), self.codePanel)
        self.openBrowserBtn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(self._device_uri)))
        codeRow.addWidget(self.openBrowserBtn)
        cpLay.addLayout(codeRow)
        self.viewLayout.addWidget(self.codePanel)
        self.codePanel.hide()
        self._device_uri = ""
        self._device_code = ""

        # 状态/提示
        self.statusLabel = CaptionLabel("", self.widget)
        self.statusLabel.setWordWrap(True)
        self.viewLayout.addWidget(self.statusLabel)
        self.statusUpdate.connect(self.statusLabel.setText)

        tip = CaptionLabel(L(
            "图标会以 base64 内嵌进索引（上限 64KB）；sha256 用于完整性校验。"
            "Token 仅保存在本地，用于创建 PR。",
            "Icon is base64-embedded in the index (max 64KB); sha256 for integrity. "
            "Token is stored locally and used only to create the PR."), self.widget)
        tip.setWordWrap(True)
        self.viewLayout.addWidget(tip)

        self.yesButton.setText(L("关闭", "Close"))
        self.cancelButton.hide()
        QTimer.singleShot(0, lambda: self._on_plugin_changed(self.combo.currentIndex()))

    def _pick_icon(self):
        path, _ = QFileDialog.getOpenFileName(
            self, L("选择图标", "Pick an icon"),
            "", L("图片 (*.png *.jpg *.jpeg)", "Images (*.png *.jpg *.jpeg)"))
        if not path:
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
        except Exception as e:
            InfoBar.error(L("读取失败", "Read failed"), str(e), parent=self.window())
            return
        if len(data) > _MAX_ICON_BYTES:
            InfoBar.warning(L("图标过大", "Icon too large"),
                            L(f"上限 64KB，当前 {len(data) // 1024}KB",
                              f"Max 64KB, got {len(data) // 1024}KB"),
                            parent=self.window())
            return
        ext = "png" if path.lower().endswith(".png") else "jpeg"
        self._icon_data_uri = f"data:image/{ext};base64," + base64.b64encode(data).decode()
        pm = QPixmap()
        if pm.loadFromData(data):
            self.iconPreview.setPixmap(pm.scaled(_ICON_SIZE, _ICON_SIZE,
                                                 Qt.KeepAspectRatio,
                                                 Qt.SmoothTransformation))
        self._generate()

    @staticmethod
    def _auto_category(rec, p) -> str:
        """根据插件实际能力自动识别分类：
        显式 category 属性 > 注册了协议 > 自定义页面 > 工具。"""
        cat = str(getattr(p, "category", "") or "").strip().lower()
        if any(k == cat for k, _ in _PLUGIN_CATEGORIES):
            return cat
        from app.services.plugins import FlowBenchPlugin
        if any(v.get("pid") == rec.pid
               for v in plugin_manager._protocols.values()):
            return "protocol"
        if type(p).create_widget is not FlowBenchPlugin.create_widget:
            return "ui"
        return "tool"

    def _on_plugin_changed(self, _i):
        """切换所选插件时：自动识别分类（不覆盖用户后续手改），再生成 JSON。"""
        idx = self.combo.currentIndex()
        pid = self._combo_pids[idx] if 0 <= idx < len(self._combo_pids) else None
        rec = plugin_manager.record(pid) if pid else None
        if rec is not None:
            if rec.plugin is not None:
                auto = self._auto_category(rec, rec.plugin)
            else:   # 未加载：从源码声明的 category 识别，无法识别则归为工具
                auto = str(_scan_plugin_meta(rec.path).get(
                    "category", "") or "").strip().lower()
                if not any(k == auto for k, _ in _PLUGIN_CATEGORIES):
                    auto = "tool"
            ci = [k for k, _ in _PLUGIN_CATEGORIES].index(auto)
            if self.catCombo.currentIndex() != ci:
                self.catCombo.blockSignals(True)
                self.catCombo.setCurrentIndex(ci)
                self.catCombo.blockSignals(False)
        self._generate()

    def _generate(self):
        idx = self.combo.currentIndex()
        pid = self._combo_pids[idx] if 0 <= idx < len(self._combo_pids) else None
        rec = plugin_manager.record(pid) if pid else None
        if rec is None:
            self.jsonBox.setPlainText("")
            return None
        p = rec.plugin
        # 未加载（禁用/失败）时从源码 AST 读取元数据，发布不受加载状态影响
        meta = {} if p is not None else _scan_plugin_meta(rec.path)
        digest = ""
        try:
            with open(rec.path, "rb") as f:
                digest = hashlib.sha256(f.read()).hexdigest()
        except Exception:
            pass

        def _tup(v):
            return list(v) if isinstance(v, (tuple, list)) else [str(v), str(v)]

        name = getattr(p, "name", None) if p is not None else meta.get("name")
        version = (getattr(p, "version", None) if p is not None
                   else meta.get("version"))
        author = (getattr(p, "author", None) if p is not None
                  else meta.get("author"))
        desc = (getattr(p, "description", None) if p is not None
                else meta.get("description"))
        entry = {
            "id": rec.pid,
            "name": _tup(name or rec.pid),
            "version": str(version or "1.0"),
            "author": str(author or ""),
            "description": _tup(desc or ""),
            "category": [k for k, _ in _PLUGIN_CATEGORIES][
                max(0, self.catCombo.currentIndex())],
            "date": datetime.date.today().isoformat(),
            "file": f"{rec.pid}.py",
            "sha256": digest,
            "min_app": "1.0.7",
            "homepage": _REPO_HOMEPAGE,
        }
        if self._icon_data_uri:
            entry["icon"] = self._icon_data_uri
        text = json.dumps(entry, ensure_ascii=False, indent=2)
        self.jsonBox.setPlainText(text)
        return entry

    def _copy(self):
        QApplication.clipboard().setText(self.jsonBox.toPlainText())
        InfoBar.success(L("已复制", "Copied"),
                        L("条目 JSON 已复制到剪贴板", "Entry JSON copied to clipboard"),
                        parent=self.window(), duration=2500)

    # ---------- 一键发布 ----------
    def _one_click_publish(self):
        entry = self._generate()
        if entry is None:
            InfoBar.warning(L("无法发布", "Cannot publish"),
                            L("请先选择一个本地插件", "Please select a local plugin first"),
                            parent=self.window())
            return
        token = (settings.github_token or "").strip() or self.tokenEdit.text().strip()
        if token and GITHUB_OAUTH_CLIENT_ID:
            # 后台检查 token scope，不够就自动重新授权
            def _check():
                try:
                    scopes = gh_check_scopes(token)
                    needs_reauth = "workflow" not in scopes and gh_can_push(token)
                except Exception:
                    needs_reauth = False
                if needs_reauth:
                    settings.set("github_token", "")
                    self.reauthNeeded.emit(entry)
                else:
                    self.proceedWithPublish.emit(entry, token)
            threading.Thread(target=_check, daemon=True).start()
            return
        if not token:
            if GITHUB_OAUTH_CLIENT_ID:
                # 浏览器一键授权（首次一次，之后 Token 自动缓存）
                self._device_login_then_publish(entry)
            else:
                self.tokenRow.show()
                InfoBar.warning(L("需要授权", "Authorization required"),
                                L("首次发布请在浏览器中确认授权，或填写 GitHub Token",
                                  "Confirm authorization in the browser for the first publish, "
                                  "or enter a GitHub Token"),
                                parent=self.window())
            return
        self._do_publish(entry, token)

    def _device_login_then_publish(self, entry):
        """浏览器一键授权（GitHub Device Flow）：打开浏览器让用户点一次确认。"""
        self.publishBtn.setEnabled(False)
        self.publishBtn.setText(L("正在请求授权…", "Requesting authorization…"))
        self.statusLabel.setText(L("正在连接 GitHub…", "Connecting to GitHub…"))

        def _work():
            try:
                d = device_flow_start()
                code = str(d["user_code"])
                # verification_uri_complete 可带码直达，省去手动输入
                uri = str(d.get("verification_uri_complete")
                          or f"{d['verification_uri']}?user_code={code}")
                # 切回主线程打开浏览器 + 更新 UI
                self.deviceReady.emit(code, uri)
                token = device_flow_poll(d["device_code"],
                                         d.get("interval", 5),
                                         d.get("expires_in", 900))
                settings.set("github_token", token)
                self.proceedWithPublish.emit(entry, token)
            except Exception as e:
                self.publishFailed.emit(str(e))

        threading.Thread(target=_work, daemon=True).start()

    def _on_device_ready(self, code: str, uri: str):
        """主线程：显示大字号授权码面板，打开浏览器到 GitHub 授权页。"""
        self._device_code = code
        self._device_uri = uri
        self.codeLabel.setText(code)
        # 按实际授权码宽度调整，保证不同长度的码都不被截断
        self.codeLabel.setMinimumWidth(
            self.codeLabel.fontMetrics().horizontalAdvance(code) + 6)
        self.codePanel.show()
        self.publishBtn.setText(L("等待浏览器授权…", "Waiting for browser…"))
        self.statusLabel.setText(L(
            "请在打开的浏览器中点击「Authorize」完成授权。若浏览器未自动打开，"
            "请点击右侧「重新打开浏览器」，或手动访问 github.com/login/device 并输入代码。",
            "Click Authorize in the opened browser. If it didn't open, "
            "click 'Reopen Browser' on the right or visit github.com/login/device and enter the code."))
        opened = QDesktopServices.openUrl(QUrl(uri))
        if not opened:
            InfoBar.warning(L("浏览器未打开", "Browser didn't open"),
                            L("请点击「重新打开浏览器」按钮或手动复制代码",
                              "Click 'Reopen Browser' or copy the code manually"),
                            parent=self.window(), duration=6000)

    def _copy_device_code(self):
        """复制设备授权码到剪贴板。"""
        if not self._device_code:
            return
        QApplication.clipboard().setText(self._device_code)
        InfoBar.success(L("已复制", "Copied"),
                        L(f"授权码 {self._device_code} 已复制",
                          f"Code {self._device_code} copied"),
                        parent=self.window(), duration=2500)

    def _do_publish(self, entry, token):
        """携带授权执行实际发布。"""
        settings.set("github_token", token)
        pid = entry["id"]
        rec = plugin_manager.record(pid)
        if rec is None or not os.path.exists(rec.path):
            InfoBar.error(L("插件文件缺失", "Plugin file missing"),
                          L("找不到插件源文件", "Cannot find plugin source file"),
                          parent=self.window())
            return

        with open(rec.path, "rb") as f:
            plugin_bytes = f.read()

        self.publishBtn.setEnabled(False)
        self.publishBtn.setText(L("发布中…", "Publishing…"))
        self.statusLabel.setText(L("正在连接 GitHub…", "Connecting to GitHub…"))

        def _work():
            try:
                result = self._github_publish(token, entry, plugin_bytes)
                self.publishOk.emit(result)
            except Exception as e:
                self.publishFailed.emit(str(e))

        threading.Thread(target=_work, daemon=True).start()

    def _github_publish(self, token, entry, plugin_bytes):
        """实际的 GitHub API 调用流程。

        - 有写权限：直接提交到 master，立即上架，返回提交页 URL。
        - 无写权限：Fork + 分支 + PR，返回 PR URL。
        """
        headers = gh_headers(token)
        pid = entry["id"]
        upstream = f"{_GH_API}/repos/{_REPO_OWNER}/{_REPO_NAME}"
        username = gh_get_user(token)
        settings.set("github_login", username)
        # 用稳定的 GitHub 登录名记录发布者，市场卡片据此只向作者显示“下架”。
        entry["publisher"] = username
        can_push = gh_can_push(token)

        # ---------- 路径 A：所有者/协作者，直接提交到 master ----------
        if can_push:
            self.statusUpdate.emit(L("检测到仓库写权限，直接上架…",
                                     "Write access detected, publishing directly…"))
            plugin_b64 = base64.b64encode(plugin_bytes).decode()
            plugin_path = f"{_MARKET_DIR}/{pid}.py"

            old_sha = gh_file_sha(upstream, plugin_path, _REPO_BRANCH, headers)
            self.statusUpdate.emit(L("正在上传插件文件…", "Uploading plugin file…"))
            gh_put_file(upstream, plugin_path, plugin_b64,
                        f"Publish plugin: {pid}", _REPO_BRANCH, headers, old_sha)

            self.statusUpdate.emit(L("正在更新插件索引…", "Updating plugin index…"))
            idx_path = f"{_MARKET_DIR}/plugins-index.json"
            idx_data, idx_sha = gh_get_json_file(upstream, idx_path, _REPO_BRANCH, headers)
            if idx_data is None:
                idx_data, idx_sha = {"plugins": []}, None
            plugins = [p for p in idx_data.get("plugins", []) if p.get("id") != pid]
            plugins.append(entry)
            idx_data["plugins"] = plugins
            new_b64 = base64.b64encode(
                json.dumps(idx_data, ensure_ascii=False, indent=2).encode()).decode()
            gh_put_file(upstream, idx_path, new_b64,
                        f"Add plugin to index: {pid}", _REPO_BRANCH, headers, idx_sha)
            return f"https://github.com/{_REPO_OWNER}/{_REPO_NAME}/commits/{_REPO_BRANCH}"

        # ---------- 路径 B：外部贡献者，Fork + PR ----------
        self.statusUpdate.emit(
            L(f"正在 Fork 仓库（{username}）…", f"Forking repository ({username})…"))

        r = requests.post(f"{upstream}/forks", headers=headers, timeout=30)
        if r.status_code not in (200, 202):
            r.raise_for_status()
        fork_api = f"{_GH_API}/repos/{r.json()['full_name']}"

        import time as _t
        for _ in range(10):
            if requests.get(fork_api, headers=headers, timeout=10).status_code == 200:
                break
            _t.sleep(1)

        # 同步 fork master 与上游，避免冲突
        try:
            requests.post(f"{fork_api}/merge-upstream", headers=headers,
                          json={"branch": _REPO_BRANCH}, timeout=15)
        except Exception:
            pass

        r = requests.get(f"{fork_api}/git/refs/heads/{_REPO_BRANCH}",
                         headers=headers, timeout=10)
        r.raise_for_status()
        master_sha = r.json()["object"]["sha"]

        branch_name = f"publish-plugin/{pid}"
        requests.delete(f"{fork_api}/git/refs/heads/{branch_name}",
                        headers=headers, timeout=10)
        r = requests.post(f"{fork_api}/git/refs", headers=headers, json={
            "ref": f"refs/heads/{branch_name}", "sha": master_sha,
        }, timeout=10)
        r.raise_for_status()

        self.statusUpdate.emit(L("正在上传插件文件…", "Uploading plugin file…"))
        plugin_b64 = base64.b64encode(plugin_bytes).decode()
        gh_put_file(fork_api, f"{_MARKET_DIR}/{pid}.py", plugin_b64,
                    f"Publish plugin: {pid}", branch_name, headers)

        self.statusUpdate.emit(L("正在更新插件索引…", "Updating plugin index…"))
        idx_path = f"{_MARKET_DIR}/plugins-index.json"
        idx_data, idx_sha = gh_get_json_file(fork_api, idx_path, branch_name, headers)
        if idx_data is None:
            idx_data, idx_sha = {"plugins": []}, None
        plugins = [p for p in idx_data.get("plugins", []) if p.get("id") != pid]
        plugins.append(entry)
        idx_data["plugins"] = plugins
        new_b64 = base64.b64encode(
            json.dumps(idx_data, ensure_ascii=False, indent=2).encode()).decode()
        gh_put_file(fork_api, idx_path, new_b64,
                    f"Add plugin: {pid}", branch_name, headers, idx_sha)

        self.statusUpdate.emit(L("正在提交，等待人工审核…",
                                 "Submitting, awaiting manual review…"))
        pr_body = L(
            f"## 新插件提交\n\n"
            f"- **插件 ID**: {pid}\n"
            f"- **名称**: {entry['name'][0]} / {entry['name'][1]}\n"
            f"- **版本**: {entry['version']}\n"
            f"- **作者**: {entry.get('author', '')}\n\n"
            f"由 FlowBench 客户端一键发布。",
            f"## New plugin submission\n\n"
            f"- **Plugin ID**: {pid}\n"
            f"- **Name**: {entry['name'][0]} / {entry['name'][1]}\n"
            f"- **Version**: {entry['version']}\n"
            f"- **Author**: {entry.get('author', '')}\n\n"
            f"Published from the FlowBench client.")
        r = requests.post(f"{upstream}/pulls", headers=headers, json={
            "title": f"Publish plugin: {pid}",
            "head": f"{username}:{branch_name}",
            "base": _REPO_BRANCH,
            "body": pr_body,
            "maintainer_can_modify": True,
        }, timeout=15)
        if r.status_code not in (200, 201):
            raise Exception(f"PR creation failed ({r.status_code}): {r.text}")
        return r.json()["html_url"]

    def _on_publish_ok(self, url):
        self.publishBtn.setEnabled(True)
        self.publishBtn.setText(L("一键发布", "Publish (1-Click)"))
        self.codePanel.hide()
        is_direct = "/commits/" in url
        if is_direct:
            self.statusLabel.setText(
                L(f"✓ 已直接上架：{url}", f"✓ Published directly: {url}"))
            InfoBar.success(L("发布成功", "Published"),
                            L("插件已直接上架，其他用户刷新市场即可看到。",
                              "Plugin is now live. Other users will see it after refreshing the marketplace."),
                            parent=self.window(), duration=6000)
        else:
            self.statusLabel.setText(
                L(f"✓ 已提交，等待审核：{url}", f"✓ Submitted, awaiting review: {url}"))
            InfoBar.success(L("发布成功", "Published"),
                            L("插件已提交，审核通过并合并 PR 后上架。",
                              "Plugin submitted. It will go live after the PR is reviewed and merged."),
                            parent=self.window(), duration=6000)
        QDesktopServices.openUrl(QUrl(url))

    def _on_publish_err(self, msg):
        self.publishBtn.setEnabled(True)
        self.publishBtn.setText(L("一键发布", "Publish (1-Click)"))
        self.codePanel.hide()
        self.statusLabel.setText(L(f"发布失败：{msg}", f"Publish failed: {msg}"))
        # Token 失效（被撤销/过期）：清除缓存，下次点击重新走浏览器授权
        if "401" in msg or "Bad credentials" in msg:
            settings.set("github_token", "")
        InfoBar.error(L("发布失败", "Publish failed"),
                      msg, parent=self.window(), duration=8000)
