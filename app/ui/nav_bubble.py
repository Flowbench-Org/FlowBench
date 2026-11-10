"""Sidebar name bubbles: while the sidebar is collapsed to icons, hovering an
item at once shows its name in a speech bubble that points at the icon."""
from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import (QEasingCurve, QEvent, QObject, QPoint, QPointF,
                            QPropertyAnimation, QRectF, QSize, Qt, QTimer)
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPainterPathStroker
from PySide6.QtWidgets import QApplication, QWidget
from qfluentwidgets import NavigationDisplayMode, isDarkTheme, setFont
from qfluentwidgets.components.widgets.tool_tip import ToolTipFilter

TAIL_W = 7          # how far the pointer sticks out to the left
TAIL_H = 12
RADIUS = 6
PAD_X = 12
PAD_Y = 7
SHADOW = 6          # room around the bubble for its soft shadow
GAP = 4             # between the sidebar item and the pointer's tip
FADE_MS = 120
SWITCH_MS = 90      # moving to the next item within this keeps the bubble up


class NavTipBubble(QWidget):
    """A borderless bubble with a pointer on its left edge."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent, Qt.ToolTip | Qt.FramelessWindowHint
                         | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        setFont(self, 13)
        self._text = ""
        self.target: Optional[QWidget] = None
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(FADE_MS)
        self._fade.setEasingCurve(QEasingCurve.OutCubic)

    def text(self) -> str:
        return self._text

    def _bubble_size(self) -> QSize:
        metrics = QFontMetrics(self.font())
        width = metrics.horizontalAdvance(self._text) + 2 * PAD_X
        height = max(metrics.height() + 2 * PAD_Y, TAIL_H + 2 * RADIUS)
        return QSize(width + TAIL_W, height)

    def tip_offset(self) -> QPoint:
        """Where the pointer's tip is, in the bubble's own coordinates."""
        return QPoint(SHADOW, self.height() // 2)

    def show_for(self, target: QWidget, text: str):
        """Point at ``target`` with ``text``; slides along when already shown."""
        appearing = not self.isVisible()
        self.target = target
        self._text = text
        bubble = self._bubble_size()
        self.resize(bubble.width() + 2 * SHADOW, bubble.height() + 2 * SHADOW)
        tip = target.mapToGlobal(QPoint(target.width() + GAP, target.height() // 2))
        pos = tip - self.tip_offset()
        screen = target.screen()
        if screen is not None:
            area = screen.availableGeometry()
            pos.setY(max(area.top(), min(pos.y(), area.bottom() - self.height() + 1)))
        self.move(pos)
        self.update()
        if appearing:
            self._fade.stop()
            self.setWindowOpacity(0.0)
            self.show()
            self.raise_()
            self._fade.setStartValue(0.0)
            self._fade.setEndValue(1.0)
            self._fade.start()

    def hide_now(self):
        self._fade.stop()
        self.target = None
        self.hide()

    def _body_rect(self) -> QRectF:
        return QRectF(SHADOW + TAIL_W, SHADOW,
                      self.width() - 2 * SHADOW - TAIL_W, self.height() - 2 * SHADOW)

    def bubble_path(self) -> QPainterPath:
        """The rounded box and its pointer as one outline, in local coordinates."""
        rect = self._body_rect()
        body = QPainterPath()
        body.addRoundedRect(rect, RADIUS, RADIUS)
        mid = self.height() / 2
        tail = QPainterPath()
        tail.moveTo(QPointF(SHADOW, mid))
        tail.lineTo(QPointF(rect.left() + 1, mid - TAIL_H / 2))
        tail.lineTo(QPointF(rect.left() + 1, mid + TAIL_H / 2))
        tail.closeSubpath()
        return body.united(tail)

    def paintEvent(self, _event):
        dark = isDarkTheme()
        # Dark: a step lighter than the sidebar so the pointer stays visible.
        fill = QColor(56, 56, 56) if dark else QColor(255, 255, 255)
        border = QColor(255, 255, 255, 46) if dark else QColor(0, 0, 0, 30)
        ink = QColor(255, 255, 255) if dark else QColor(26, 26, 26)
        path = self.bubble_path()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        # Soft shadow: stacked halos, densest right at the edge.
        layer = QColor(0, 0, 0, 9 if dark else 5)
        stroker = QPainterPathStroker()
        stroker.setJoinStyle(Qt.RoundJoin)
        for grow in range(SHADOW, 0, -1):
            stroker.setWidth(grow * 2)
            painter.setBrush(layer)
            painter.drawPath(stroker.createStroke(path).united(path).translated(0, 1))
        painter.setBrush(fill)
        painter.setPen(border)
        painter.drawPath(path)
        painter.setPen(ink)
        painter.drawText(self._body_rect(), Qt.AlignCenter, self._text)


class NavBubbles(QObject):
    """Shows the bubble for sidebar items while the sidebar is collapsed."""

    def __init__(self, panel: QWidget):
        super().__init__(panel)
        self.panel = panel
        self.bubble = NavTipBubble()
        self._texts = {}          # widget -> callable giving its name, or None
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(SWITCH_MS)
        self._hide_timer.timeout.connect(self.bubble.hide_now)
        panel.displayModeChanged.connect(lambda _mode: self.hide())
        # Expanding without animation emits no displayModeChanged, but the
        # panel always changes width.
        panel.installEventFilter(self)
        panel.destroyed.connect(self.bubble.deleteLater)
        app = QApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(
                lambda state: state == Qt.ApplicationActive or self.hide())

    def attach(self, widget: Optional[QWidget], text: Optional[Callable[[], str]] = None):
        """Give ``widget`` a bubble. ``text`` defaults to the item's own label."""
        if widget is None or widget in self._texts:
            return
        # The bubble replaces qfluentwidgets' delayed tooltip for this item.
        for old in widget.findChildren(ToolTipFilter, options=Qt.FindDirectChildrenOnly):
            widget.removeEventFilter(old)
            old.deleteLater()
        self._texts[widget] = text
        widget.installEventFilter(self)
        widget.destroyed.connect(lambda *_args, w=widget: self._forget(w))

    def _forget(self, widget):
        self._texts.pop(widget, None)
        if self.bubble.target is widget:
            self.hide()

    def hide(self):
        self._hide_timer.stop()
        self.bubble.hide_now()

    def _collapsed(self) -> bool:
        return getattr(self.panel, "displayMode", None) == NavigationDisplayMode.COMPACT

    def _text_for(self, widget) -> str:
        getter = self._texts.get(widget)
        if getter is not None:
            return str(getter() or "")
        text = getattr(widget, "text", None)
        return str(text() if callable(text) else widget.toolTip() or "")

    def eventFilter(self, obj, event):
        if obj is self.panel:
            if event.type() == QEvent.Resize and event.size().width() != event.oldSize().width():
                self.hide()
            return False
        if obj in self._texts:
            kind = event.type()
            if kind == QEvent.ToolTip:
                return True                       # no second, native tooltip
            if kind == QEvent.Enter:
                text = self._text_for(obj) if self._collapsed() else ""
                if text:
                    self._hide_timer.stop()
                    self.bubble.show_for(obj, text)
            elif kind == QEvent.Leave:
                if self.bubble.target is obj:
                    self._hide_timer.start()      # unless the next item is entered
            elif kind in (QEvent.Hide, QEvent.MouseButtonPress):
                if self.bubble.target is obj:
                    self.hide()
        return False
