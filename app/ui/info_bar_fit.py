"""Keep every InfoBar inside its window.

qfluentwidgets wraps InfoBar text by character count, up to 120 per line.
That is about twice too wide for Chinese and cannot break a URL, so a long
message pushes the bar, and its close button, past the window edge. This
wraps by measured pixel width instead, and fits the bar again after a widget
(such as a link) is added to it.
"""
from __future__ import annotations

import re

from PySide6.QtCore import Qt
from qfluentwidgets import InfoBar

MANAGER_MARGIN = 24      # InfoBarManager's gap between a bar and the window edge
MIN_TEXT_WIDTH = 160
# Words, a URL split after each "/", and single CJK or other characters.
_TOKENS = re.compile(r"\s+|[^\s/⺀-鿿　-〿＀-￯]+/?|.", re.S)

_original_add_widget = InfoBar.addWidget
_installed = False


def wrap_to_width(text: str, metrics, width: int) -> list:
    """Break ``text`` into lines no wider than ``width`` pixels.

    Breaks between words, between CJK characters and after "/" in URLs; a
    single piece that is still too wide is cut where it has to be.
    """
    width = max(1, int(width))
    fits = lambda s: metrics.horizontalAdvance(s) <= width
    lines = []
    for paragraph in str(text or "").split("\n"):
        line = ""
        for token in _TOKENS.findall(paragraph):
            if fits(line + token):
                line += token
                continue
            if line.strip():
                lines.append(line.rstrip())
                line = token.lstrip()
            else:
                line += token
            while line and not fits(line):
                cut = 1
                while cut < len(line) and fits(line[:cut + 1]):
                    cut += 1
                lines.append(line[:cut])
                line = line[cut:]
        lines.append(line.rstrip())
    return lines


def _fit_text(bar):
    parent = bar.parent()
    room = (parent.width() if parent is not None else 900) - 2 * MANAGER_MARGIN
    bar.titleLabel.setText(bar.title)
    bar.contentLabel.ensurePolished()
    bar.contentLabel.setText("")
    # Child layouts keep their old size hints until each is invalidated.
    for layout in (bar.textLayout, bar.widgetLayout, bar.hBoxLayout):
        layout.invalidate()
    others = bar.hBoxLayout.sizeHint().width()
    if bar.orient == Qt.Vertical:
        # Title, content and widgets share one column; only its width is free.
        others -= bar.textLayout.sizeHint().width()
    width = max(MIN_TEXT_WIDTH, room - others)
    lines = wrap_to_width(bar.content, bar.contentLabel.fontMetrics(), width)
    bar.contentLabel.setText("\n".join(lines))
    bar.adjustSize()


def _add_widget(bar, widget, stretch=0):
    _original_add_widget(bar, widget, stretch)
    bar._adjustText()


def install():
    """Patch InfoBar once; every bar created afterwards stays inside its window."""
    global _installed
    if _installed:
        return
    InfoBar._adjustText = _fit_text
    InfoBar.addWidget = _add_widget
    _installed = True
