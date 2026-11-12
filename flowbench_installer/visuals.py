"""Drawing and motion for the setup, update and uninstall windows.

Everything is painted with QPainter, so the windows need no image files
besides the logo PNGs that are already bundled, and every effect follows
light and dark mode:

``LogoArt``          the FlowBench logo: the bundled PNG, or a vector copy of
                     it when the PNG is missing.  The vector shapes of its
                     three ribbons drive the "logo forms" intro and the
                     liquid progress fill.
``FlowBackground``   slowly drifting colour glows and network-traffic waves.
``AnimatedLogo``     intro, float, liquid fill (install), drain (uninstall)
                     and the "done" badge.
``Wordmark``         "FlowBench", written in letter by letter.
``FlowProgressBar``  a ProgressBar in the logo's colours with a moving sheen.
``PageStack``        pages slide and fade in.
``FrameClock``       one timer per window that repaints it while it moves.

With Windows' "Animation effects" turned off (``motion=False``) every widget
shows its finished state and nothing moves.
"""
from __future__ import annotations

import math
import random
import time
from pathlib import Path
from typing import Dict, Optional

from PySide6.QtCore import (QAbstractAnimation, QEasingCurve, QObject,
                            QParallelAnimationGroup, QPoint, QPointF,
                            QPropertyAnimation, QRect, QRectF,
                            QSequentialAnimationGroup, QSize, Qt, QTimer)
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QImage,
                           QLinearGradient, QPainter, QPainterPath, QPen,
                           QPolygonF, QRadialGradient)
from PySide6.QtWidgets import (QFrame, QGraphicsOpacityEffect, QStackedWidget,
                               QWidget)
from qfluentwidgets import ProgressBar, isDarkTheme


def now() -> float:
    """The animation clock (tests replace it to render a given moment)."""
    return time.monotonic()


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return low if value < low else high if value > high else value


def ease_out_cubic(t: float) -> float:
    t = clamp(t)
    return 1 - (1 - t) ** 3


def ease_in_out_cubic(t: float) -> float:
    t = clamp(t)
    return 4 * t * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def ease_out_back(t: float) -> float:
    t = clamp(t)
    c1 = 1.70158
    return 1 + (c1 + 1) * (t - 1) ** 3 + c1 * (t - 1) ** 2


def rgba(color: str, alpha: float) -> QColor:
    result = QColor(color)
    result.setAlphaF(clamp(alpha))
    return result


# ------------------------------------------------------------------ logo --

# The logo's three ribbons, traced from app_logo.png (alpha above 50 %) and
# simplified to within 0.6 px of the original.  Units: the logo is 1000 units
# tall and LOGO_WIDTH wide, origin at its top-left corner.  "band" runs on a
# little under "leaf", which is painted on top of it, as in the PNG.
LOGO_WIDTH = 885.0
RIBBONS = ("top", "band", "leaf")
PAINT_ORDER = ("band", "leaf", "top")
INTRO_ORDER = ("leaf", "band", "top")      # the logo builds up from the bottom

_RIBBON_POINTS = {
    "top": (
        26.1, 451.1, 25.1, 451.8, 24, 449.9, 25, 288.6, 26.9, 272.5, 31.8, 248.5, 38.9,
        225.5, 46, 207.4, 58.8, 182.4, 73.6, 159.3, 88.9, 139.3, 109.1, 117.2, 131.3, 96.9,
        150.3, 81.9, 176.4, 64.1, 203.4, 49.1, 236.5, 33.7, 286.6, 16.9, 303.6, 12.6, 340.7,
        5.7, 386.8, 0.7, 436.9, -0.4, 862.7, -0.4, 875.4, 1, 879.8, 3.5, 882.8, 7, 884.5,
        13, 883.8, 22, 880, 41.1, 873, 68.1, 861.8, 100.2, 846.8, 131.3, 836.1, 148.3, 827,
        160.3, 809.6, 178.9, 789.6, 195.7, 766.5, 209.9, 744.5, 219.8, 720.4, 227.5, 702.4,
        231.7, 679.4, 235, 649.3, 236.3, 395.8, 236.3, 365.7, 237, 331.7, 240.3, 307.6,
        244.1, 269.6, 253.5, 231.5, 267.5, 194.4, 285.4, 155.3, 309.3, 139.3, 320.6, 123.2,
        333.5, 104.2, 350.4, 73.9, 381.8, 58.5, 400.8, 47.1, 416.8, 26.1, 451.1,
    ),
    "band": (
        4, 834.2, 0, 833.7, 0.5, 584.2, 1.9, 553.1, 3.8, 538.1, 7.7, 519, 13.7, 499, 22.9,
        477, 32.7, 458.9, 43.9, 442.9, 60.1, 423.8, 76.2, 408.6, 90.9, 396.8, 109.2, 384.3,
        125.3, 374.5, 146.3, 363.5, 169.3, 353.4, 188.4, 346.4, 226.5, 335.2, 250.5, 330.4,
        278.6, 326.6, 307.6, 324.7, 329.7, 324.5, 374.7, 327.4, 417.8, 334.3, 484, 349.1,
        522, 355.9, 549.1, 359, 589.2, 360.7, 641.3, 357.1, 674.3, 351.9, 698.4, 347, 750.5,
        333, 807.6, 312.4, 816.6, 312.5, 822.3, 316.6, 824, 320.6, 824.3, 327.7, 817.7,
        385.8, 808.9, 424.8, 797.9, 455.9, 786.9, 478, 778.9, 491, 767.5, 506.7, 755.5,
        520.1, 746.5, 528.4, 735.5, 537.4, 723.4, 545.5, 710.4, 553.4, 696.4, 560.3, 663.3,
        572.3, 627.3, 580.3, 595.2, 583.7, 558.1, 583.9, 523, 580.4, 484, 573.2, 403.8,
        555.7, 376.8, 551.8, 349.7, 549.8, 321.6, 550, 284.6, 554.8, 255.5, 561.9, 232.5,
        569.8, 219.4, 575.1, 193.4, 587.8, 177.4, 597, 154.3, 612, 139.3, 622.9, 120.2,
        638.8, 96.5, 661.3, 77.8, 681.4, 53.9, 711.4, 37.1, 737.5, 36.3, 739.5, 37.1, 740.2,
        39.1, 739.1, 40.6, 739.5, 34.6, 748.5, 30.6, 757.5, 28.6, 759.5, 15.5, 787.6, 15.5,
        789.6, 10.5, 802.6, 9.5, 809.6, 8.5, 810.6, 7.5, 821.6, 6.5, 822.6, 6.5, 832.7, 4,
        834.2,
    ),
    "leaf": (
        19, 999.1, 12, 999.4, 6, 997.4, 1.9, 993, 0.2, 987, 0, 833.7, 1.5, 832.7, 1.5,
        822.6, 2.5, 821.6, 3.5, 810.6, 4.5, 809.6, 5.5, 802.6, 6.5, 801.6, 10.5, 787.6,
        23.5, 759.5, 25.6, 757.5, 32.6, 743.5, 35.1, 742.2, 35.7, 738.5, 38.1, 739.2, 39.1,
        738.2, 52.2, 720.4, 81.2, 691, 107.2, 670.2, 143.3, 647.2, 185.4, 626.9, 206.4,
        618.9, 231.5, 611, 271.5, 601.9, 299.6, 598, 326.7, 596, 352.7, 595.9, 357.7, 596.4,
        360.7, 597.8, 362, 600.2, 363.2, 615.2, 363.8, 713.4, 362.9, 743.5, 358.8, 772.5,
        350.9, 800.6, 341, 824.6, 325.2, 852.7, 305.9, 878.8, 295.3, 890.8, 283.6, 902.3,
        260, 921.8, 230.5, 941.1, 195.4, 958, 162.3, 970.3, 126.3, 980.3, 73.1, 991.1, 19,
        999.1,
    ),
}

# Gradient fills fitted to app_logo.png (light) and app_logo_dark.png (dark):
# (start, end, ((position, colour), ...)), points in logo units.  They are
# only used when the PNGs are not bundled.
_RIBBON_GRADIENTS = {
    "light": {
        "top": ((507.9, -99.3), (329.1, 524.2), (
            (0.021, "#a0edfc"), (0.062, "#a1eafb"), (0.146, "#90e0fb"), (0.479, "#33affc"),
            (0.562, "#21a3fc"), (0.979, "#0c82fc"))),
        "band": ((542.8, 173.1), (156.9, 898.9), (
            (0.021, "#51e7fc"), (0.062, "#55e4fc"), (0.229, "#41cbfc"), (0.396, "#23a9fc"),
            (0.521, "#138afc"), (0.812, "#0962f9"), (0.938, "#0447e1"), (0.979, "#0848df"))),
        "leaf": ((334.4, 585.7), (28.7, 1006.5), (
            (0.021, "#3cacfc"), (0.146, "#4db9fc"), (0.271, "#46b4fc"), (0.396, "#32a1fc"),
            (0.646, "#126ffb"), (0.854, "#054cf4"), (0.979, "#0446f3"))),
    },
    "dark": {
        "top": ((605.8, -147.4), (209.8, 538.6), (
            (0.021, "#7bdefa"), (0.146, "#43adfa"), (0.229, "#2c90fb"), (0.438, "#0a54f8"),
            (0.562, "#0239e5"), (0.646, "#0232d2"), (0.729, "#0232c8"), (0.812, "#0138d1"),
            (0.979, "#0143f1"))),
        "band": ((589.8, 167.3), (111.4, 876.5), (
            (0.021, "#69e3f9"), (0.062, "#63dbfa"), (0.229, "#309ffa"), (0.396, "#0e63fb"),
            (0.479, "#0548f1"), (0.521, "#033ee6"), (0.646, "#022fca"), (0.854, "#0231c7"),
            (0.979, "#021dac"))),
        "leaf": ((373.8, 623), (-10.4, 968.9), (
            (0.021, "#4db9fa"), (0.104, "#4ab5fb"), (0.271, "#2685fb"), (0.438, "#0b59f8"),
            (0.604, "#0234db"), (0.688, "#0129cb"), (0.812, "#0221c2"), (0.896, "#011bc1"),
            (0.979, "#021acd"))),
    },
}


def ribbon_path(name: str) -> QPainterPath:
    values = _RIBBON_POINTS[name]
    points = [QPointF(values[i], values[i + 1]) for i in range(0, len(values), 2)]
    path = QPainterPath()
    path.addPolygon(QPolygonF(points))
    path.closeSubpath()
    return path


def _sweep_line(path: QPainterPath):
    """Start and end of a ribbon along its stem-to-tip direction (bottom-left
    to top-right), so a sweep from 0 to 1 covers it exactly."""
    box = path.boundingRect()
    dx, dy = box.width(), -box.height()
    length = math.hypot(dx, dy) or 1.0
    ux, uy = dx / length, dy / length
    projections = [path.elementAt(i).x * ux + path.elementAt(i).y * uy
                   for i in range(path.elementCount())]
    low, high = min(projections), max(projections)
    centre = box.center()
    c = centre.x() * ux + centre.y() * uy
    start = QPointF(centre.x() + (low - c) * ux, centre.y() + (low - c) * uy)
    end = QPointF(centre.x() + (high - c) * ux, centre.y() + (high - c) * uy)
    return start, end


def _alpha_bounds(image: QImage) -> Optional[QRect]:
    """Bounding box of the pixels that are more than half opaque."""
    alpha = image.convertToFormat(QImage.Format_Alpha8)
    data = bytes(alpha.constBits())
    stride, width = alpha.bytesPerLine(), alpha.width()
    table = bytes(1 if value > 127 else 0 for value in range(256))
    top = bottom = None
    left, right = width, -1
    for y in range(alpha.height()):
        row = data[y * stride:y * stride + width].translate(table)
        first = row.find(1)
        if first < 0:
            continue
        top = y if top is None else top
        bottom = y
        left = min(left, first)
        right = max(right, row.rfind(1))
    if top is None:
        return None
    return QRect(left, top, right - left + 1, bottom - top + 1)


class LogoArt:
    """The logo as images of any size, for the current theme.

    The bundled PNGs are cropped to the logo and used as they are; without
    them the logo is painted from the traced shapes and fitted gradients.
    """

    def __init__(self, light_png: Optional[Path] = None, dark_png: Optional[Path] = None):
        self._files = {False: light_png, True: dark_png}
        self._sources: Dict[bool, Optional[QImage]] = {}
        self._cache: Dict[tuple, QImage] = {}
        self.paths = {name: ribbon_path(name) for name in RIBBONS}
        self.sweeps = {name: _sweep_line(path) for name, path in self.paths.items()}
        self.outline = QPainterPath()
        for name in PAINT_ORDER:
            self.outline = self.outline.united(self.paths[name])

    def _source(self, dark: bool) -> Optional[QImage]:
        if dark not in self._sources:
            image = None
            path = self._files.get(dark) or self._files.get(False)
            if path and Path(path).is_file():
                loaded = QImage(str(path))
                box = None if loaded.isNull() else _alpha_bounds(loaded)
                if box is not None:
                    image = loaded.copy(box).convertToFormat(QImage.Format_ARGB32_Premultiplied)
            self._sources[dark] = image
        return self._sources[dark]

    @property
    def uses_png(self) -> bool:
        return self._source(isDarkTheme()) is not None

    def image(self, height: float, dpr: float = 1.0, dark: Optional[bool] = None) -> QImage:
        """The logo ``height`` logical pixels tall, with ``dpr`` set."""
        dark = isDarkTheme() if dark is None else dark
        h = max(1, round(height * dpr))
        w = max(1, round(height * LOGO_WIDTH / 1000.0 * dpr))
        key = (dark, w, h, dpr)
        cached = self._cache.get(key)
        if cached is None:
            if len(self._cache) > 24:
                self._cache.clear()
            cached = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
            cached.fill(Qt.transparent)
            painter = QPainter(cached)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            source = self._source(dark)
            if source is not None:
                scaled = source.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                painter.drawImage(QPointF((w - scaled.width()) / 2, (h - scaled.height()) / 2),
                                  scaled)
            else:
                painter.scale(h / 1000.0, h / 1000.0)
                self.paint_vector(painter, dark)
            painter.end()
            cached.setDevicePixelRatio(dpr)
            self._cache[key] = cached
        return cached

    def paint_vector(self, painter: QPainter, dark: bool) -> None:
        """Paint the vector logo in logo units."""
        painter.setPen(Qt.NoPen)
        for name in PAINT_ORDER:
            start, end, stops = _RIBBON_GRADIENTS["dark" if dark else "light"][name]
            gradient = QLinearGradient(QPointF(*start), QPointF(*end))
            for position, colour in stops:
                gradient.setColorAt(position, QColor(colour))
            painter.fillPath(self.paths[name], QBrush(gradient))


def _gradient_brush(start: QPointF, end: QPointF, profile) -> QBrush:
    """A white brush whose alpha follows ``profile``: [(position, alpha)]."""
    gradient = QLinearGradient(start, end)
    seen = set()
    for position, alpha in sorted(profile):
        position = round(clamp(position), 4)
        if position in seen:
            continue
        seen.add(position)
        gradient.setColorAt(position, rgba("#ffffff", alpha))
    return QBrush(gradient)


def _sweep_brush(start: QPointF, end: QPointF, front: float, soft: float) -> QBrush:
    """Opaque behind ``front``, fading to clear over ``soft`` ahead of it."""
    def alpha(s):
        return clamp((front - s) / soft)
    marks = {0.0, 1.0, clamp(front - soft), clamp(front)}
    return _gradient_brush(start, end, [(s, alpha(s)) for s in marks])


def _band_brush(start: QPointF, end: QPointF, centre: float, width: float, peak: float) -> QBrush:
    """A soft bright band around ``centre`` (a sheen or a glowing front)."""
    profile = [(0.0, 0.0), (1.0, 0.0)]
    for position, alpha in ((centre - width, 0.0), (centre, peak), (centre + width, 0.0)):
        if 0.0 <= position <= 1.0:
            profile.append((position, alpha))
    if not 0.0 <= centre <= 1.0:
        return QBrush(Qt.NoBrush)
    return _gradient_brush(start, end, profile)


# ------------------------------------------------------------ frame clock --

class FrameClock(QObject):
    """Repaints its parent window: about 60 fps while something moves
    quickly, 30 fps otherwise, slower when the window is not active.

    It keeps no Python reference to the window (only Qt's parent link): a
    reference cycle would leave a closed window to the cyclic garbage
    collector, which may run on a worker thread and must never destroy a
    widget there.  The window may define ``dirty_region()`` to limit the
    repaint to what moves.
    """

    FAST, NORMAL, BACKGROUND = 16, 33, 66

    def __init__(self, window: QWidget, enabled: bool = True):
        super().__init__(window)
        self.enabled = enabled
        self._fast_until = 0.0
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._tick)

    def start(self):
        if self.enabled and not self._timer.isActive():
            self._timer.start(self._interval())

    def stop(self):
        self._timer.stop()

    def running(self) -> bool:
        return self._timer.isActive()

    def boost(self, seconds: float):
        """Run at full speed for a while (an intro, a page change)."""
        self._fast_until = max(self._fast_until, now() + seconds)
        if self._timer.isActive():
            self._timer.setInterval(self.FAST)

    def _interval(self) -> int:
        if now() < self._fast_until:
            return self.FAST
        window = self.parent()
        return self.NORMAL if window is not None and window.isActiveWindow() else self.BACKGROUND

    def _tick(self):
        window = self.parent()
        if window is None:
            return
        dirty = getattr(window, "dirty_region", None)
        region = dirty() if dirty is not None else None
        if region is None:
            window.update()
        elif not region.isEmpty():
            window.update(region)
        interval = self._interval()
        if self._timer.interval() != interval:
            self._timer.setInterval(interval)


# ------------------------------------------------------------- background --

class FlowBackground:
    """Soft glows behind network-traffic waves, cheap enough to animate.

    The gradient and the glows are painted once into a cached image.  Each
    wave is a pre-drawn strip whose pattern repeats every window width, so
    sliding it sideways makes it flow; the packets riding the waves are small
    pre-drawn streaks.  Only the band the waves move in needs repainting.
    """

    # colour, alpha in light mode, alpha in dark mode, radius (x width), centre
    GLOWS = (
        ("#2f7bff", 0.20, 0.42, 0.62, (0.10, 0.16)),
        ("#1fc8ee", 0.17, 0.30, 0.55, (0.94, 0.86)),
        ("#7b6cff", 0.10, 0.26, 0.46, (0.72, 0.02)),
        ("#1aa3f0", 0.10, 0.22, 0.38, (0.30, 0.98)),
    )
    # height (x window height), amplitude (px), cycles per width (wave, ripple),
    # flow speed (px/s), phase
    WAVES = (
        (0.70, 15.0, (1, 3), 24.0, 0.0),
        (0.76, 20.0, (1, 2), -17.0, 1.7),
        (0.82, 12.0, (2, 5), 31.0, 3.1),
        (0.88, 18.0, (1, 3), -14.0, 4.4),
        (0.94, 10.0, (2, 3), 20.0, 5.6),
    )

    def __init__(self, motion: bool = True):
        self.motion = motion
        self._cache: Dict[tuple, QImage] = {}
        self.epoch = now()

    def time(self) -> float:
        return now() - self.epoch if self.motion else 12.0

    def _cached(self, key, make) -> QImage:
        image = self._cache.get(key)
        if image is None:
            if len(self._cache) > 40:
                self._cache.clear()
            image = self._cache[key] = make()
        return image

    @staticmethod
    def _canvas(width: float, height: float, dpr: float) -> QImage:
        image = QImage(max(1, round(width * dpr)), max(1, round(height * dpr)),
                       QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        image.setDevicePixelRatio(dpr)
        return image

    @staticmethod
    def _paint_glow(painter: QPainter, centre: QPointF, radius: float, colour: str, alpha: float):
        gradient = QRadialGradient(centre, radius)
        for position, factor in ((0.0, 1.0), (0.35, 0.62), (0.7, 0.18), (1.0, 0.0)):
            gradient.setColorAt(position, rgba(colour, alpha * factor))
        painter.fillRect(QRectF(centre.x() - radius, centre.y() - radius, 2 * radius, 2 * radius),
                         QBrush(gradient))

    def _static(self, w: float, h: float, dark: bool, dpr: float) -> QImage:
        def make():
            image = self._canvas(w, h, dpr)
            painter = QPainter(image)
            base = QLinearGradient(0, 0, w, h)
            base.setColorAt(0.0, QColor("#0a1020" if dark else "#f8fbff"))
            base.setColorAt(1.0, QColor("#0b1a34" if dark else "#ecf3fd"))
            painter.fillRect(QRectF(0, 0, w, h), QBrush(base))
            for colour, a_light, a_dark, radius, centre in self.GLOWS:
                self._paint_glow(painter, QPointF(centre[0] * w, centre[1] * h), radius * w,
                                 colour, a_dark if dark else a_light)
            painter.end()
            return image
        return self._cached(("static", dark, w, h, dpr), make)

    def _curve(self, index: int, u: float, width: float) -> float:
        """Height of wave ``index`` (relative to its centre line) at ``u``."""
        _level, amplitude, (main, ripple), _speed, phase = self.WAVES[index]
        k = 2 * math.pi / width
        return (amplitude * math.sin(k * main * u + phase)
                + amplitude * 0.35 * math.sin(k * ripple * u + phase * 1.7))

    def _strip(self, index: int, w: float, dark: bool, dpr: float) -> QImage:
        amplitude = self.WAVES[index][1]
        height = math.ceil(amplitude * 2.7 + 8)

        def make():
            image = self._canvas(w, height, dpr)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.Antialiasing)
            path = QPainterPath()
            steps = 72
            for i in range(steps + 1):
                u = -12 + (w + 24) * i / steps
                point = QPointF(u, height / 2 + self._curve(index, u, w))
                if i == 0:
                    path.moveTo(point)
                else:
                    path.lineTo(point)
            alpha = 0.32 if dark else 0.24
            gradient = QLinearGradient(0, 0, w, 0)     # repeats with the strip
            gradient.setColorAt(0.0, rgba("#3d8bff" if dark else "#2b7bf6", alpha))
            gradient.setColorAt(0.5, rgba("#3fd9f5" if dark else "#22c3ee", alpha))
            gradient.setColorAt(1.0, rgba("#3d8bff" if dark else "#2b7bf6", alpha))
            painter.strokePath(path, QPen(QBrush(gradient), 1.3))
            painter.end()
            return image
        return self._cached(("wave", index, dark, w, dpr), make)

    def _streak(self, dark: bool, dpr: float, forward: bool) -> QImage:
        def make():
            image = self._canvas(40, 14, dpr)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.Antialiasing)
            if not forward:
                painter.translate(40, 0)
                painter.scale(-1, 1)
            colour = "#7fe6ff" if dark else "#1f9df0"
            tail = QLinearGradient(4, 0, 32, 0)
            tail.setColorAt(0.0, rgba(colour, 0.0))
            tail.setColorAt(1.0, rgba(colour, 0.55))
            painter.setPen(QPen(QBrush(tail), 1.6, Qt.SolidLine, Qt.RoundCap))
            painter.drawLine(QPointF(4, 7), QPointF(32, 7))
            head = QRadialGradient(QPointF(32, 7), 7)
            head.setColorAt(0.0, rgba("#ffffff" if dark else colour, 0.95))
            head.setColorAt(0.3, rgba(colour, 0.6))
            head.setColorAt(1.0, rgba(colour, 0.0))
            painter.fillRect(QRectF(25, 0, 14, 14), QBrush(head))
            painter.end()
            return image
        return self._cached(("streak", dark, dpr, forward), make)

    def moving_rect(self, rect: QRectF) -> QRectF:
        """The band the waves and packets move in; the rest stays still."""
        top = min(level * rect.height() - amplitude * 1.35 - 12
                  for level, amplitude, *_rest in self.WAVES)
        return QRectF(rect.left(), rect.top() + top, rect.width(), rect.height() - top)

    def paint(self, painter: QPainter, rect: QRectF, dark: bool, dpr: float = 1.0):
        t = self.time()
        w, h = rect.width(), rect.height()
        painter.save()
        painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
        painter.drawImage(rect.topLeft(), self._static(w, h, dark, dpr))
        for index, (level, amplitude, _cycles, speed, phase) in enumerate(self.WAVES):
            strip = self._strip(index, w, dark, dpr)
            strip_h = strip.height() / dpr
            offset = (speed * t) % w
            breath = 3.0 * math.sin(2 * math.pi * t / (11.0 + 2.3 * index) + phase)
            top = round(rect.top() + level * h - strip_h / 2 + breath)
            left = round(rect.left() - offset)
            painter.drawImage(QPointF(left, top), strip)
            painter.drawImage(QPointF(left + w, top), strip)
            # Packets riding the wave, a little faster than it flows.
            forward = speed > 0
            streak = self._streak(dark, dpr, forward)
            span = w + 80
            for packet in range(2):
                travel = abs(speed) * 2.6 * t + packet * span / 2 + index * 97
                x = travel % span - 40
                if not forward:
                    x = w - x
                y = level * h + breath + self._curve(index, x + offset, w)
                head = 32 if forward else 8
                painter.drawImage(QPointF(round(rect.left() + x - head), round(rect.top() + y - 7)),
                                  streak)
        painter.restore()


# ------------------------------------------------------------ the logo ---

INTRO_SWEEPS = {"leaf": (0.20, 0.70), "band": (0.36, 0.76), "top": (0.52, 0.78)}
INTRO_SOFT = 0.22               # width of a sweep's soft front (of the ribbon length)
INTRO_SHEEN = (1.22, 0.65)      # start, duration
INTRO_END = 1.95


class AnimatedLogo(QWidget):
    """The logo with its effects, centred in a canvas larger than the logo
    (particles and the glow spill around it)."""

    def __init__(self, art: LogoArt, logo_height: int, canvas: QSize, motion: bool = True,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.art = art
        self.logo_height = logo_height
        self.motion = motion
        self.setFixedSize(canvas)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._intro_start: Optional[float] = None
        self._liquid: Optional[str] = None        # "fill" | "drain"
        self._target = 0.0
        self._level = 0.0
        self._level_time = now()
        self._badge_start: Optional[float] = None
        self._badge_kind = "success"
        self._ghost = False
        self._float = True
        self._born = now()
        self._buffer: Optional[QImage] = None
        self._particles = None
        self._glow_key = None
        self._glow_image: Optional[QImage] = None

    # -- control ------------------------------------------------------------

    def play_intro(self, delay: float = 0.0):
        self._intro_start = now() + delay if self.motion else None
        self.update()

    def skip_intro(self):
        self._intro_start = None
        self.update()

    def intro_done(self) -> bool:
        return self._intro_start is None or now() - self._intro_start >= INTRO_END

    def set_liquid(self, mode: Optional[str], progress: float = 0.0, immediate: bool = False):
        """``fill``: the logo fills up as ``progress`` goes 0 -> 1 (install);
        ``drain``: it empties (uninstall); None: the plain logo."""
        self._liquid = mode
        self._target = 0.0 if mode is None else (
            clamp(progress) if mode == "fill" else 1.0 - clamp(progress))
        if immediate or not self.motion:
            self._level = self._target
            self._level_time = now()
        self.update()

    def level(self) -> float:
        return self._level

    def show_badge(self, ghost: bool = False, kind: str = "success"):
        self._badge_start = now()
        self._badge_kind = kind
        self._ghost = ghost
        self._liquid = None
        self.update()

    def clear_badge(self):
        self._badge_start = None
        self._ghost = False
        self.update()

    def has_badge(self) -> bool:
        return self._badge_start is not None

    def is_animating(self) -> bool:
        return self.motion          # it floats, glows and ripples while shown

    # -- geometry -------------------------------------------------------------

    def logo_rect(self) -> QRectF:
        h = float(self.logo_height)
        dpr = self.devicePixelRatioF() or 1.0
        w = round(h * LOGO_WIDTH / 1000.0 * dpr) / dpr       # whole pixels, like the art
        offset = 0.0
        if self.motion and self._float and self.intro_done():
            offset = 2.5 * math.sin(2 * math.pi * (now() - self._born) / 5.0)
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2 + offset, w, h)

    # -- painting -------------------------------------------------------------

    def _canvas(self, rect: QRectF, dpr: float) -> QImage:
        margin = 3
        w = max(1, round((rect.width() + 2 * margin) * dpr))
        h = max(1, round((rect.height() + 2 * margin) * dpr))
        buffer = self._buffer
        if buffer is None or buffer.width() != w or buffer.height() != h \
                or buffer.devicePixelRatio() != dpr:
            buffer = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
            buffer.setDevicePixelRatio(dpr)
            self._buffer = buffer
        buffer.fill(Qt.transparent)
        return buffer

    def _compose(self, rect: QRectF, dpr: float, dark: bool, mask, overlay=None) -> QImage:
        """Logo art seen through ``mask`` (painted in logo units), plus an
        ``overlay`` painted only where the logo shows."""
        margin = 3
        canvas = self._canvas(rect, dpr)
        art = self.art.image(rect.height(), dpr, dark)
        # Exactly the art's pixels: SourceIn below leaves the rest of the
        # canvas alone, so the mask must not reach past them.
        art_rect = QRectF(0, 0, art.width() / dpr, art.height() / dpr)
        scale = rect.height() / 1000.0
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.translate(margin, margin)
        painter.save()
        painter.setClipRect(art_rect)
        painter.scale(scale, scale)
        mask(painter)
        painter.restore()
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.drawImage(art_rect, art)
        if overlay is not None:
            painter.setCompositionMode(QPainter.CompositionMode_SourceAtop)
            painter.scale(scale, scale)
            overlay(painter)
        painter.end()
        return canvas

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        dark = isDarkTheme()
        dpr = self.devicePixelRatioF()
        rect = self.logo_rect()
        t = now()
        intro = None
        if self._intro_start is not None:
            intro = t - self._intro_start
            if intro >= INTRO_END:
                self._intro_start = None
                intro = None
        self._paint_glow(painter, rect, dark, t, intro)
        if self._liquid is not None:
            self._paint_liquid(painter, rect, dpr, dark, t)
        elif intro is not None:
            self._paint_intro(painter, rect, dpr, dark, intro)
        else:
            self._paint_still(painter, rect, dpr, dark, t)
        if self._badge_start is not None:
            self._paint_badge(painter, rect, dark, t - self._badge_start)
        painter.end()

    def _paint_glow(self, painter, rect, dark, t, intro):
        if self._ghost:
            return
        strength = 1.0
        if intro is not None:
            strength = ease_out_cubic((intro - 0.7) / 0.8) if intro > 0.7 else 0.0
        elif self.motion:
            strength = 0.85 + 0.15 * math.sin(2 * math.pi * (t - self._born) / 4.0)
        if self._liquid is not None:
            strength *= 0.35 + 0.65 * self._level
        if strength <= 0:
            return
        # Fades out before the widget's edge, so the edge never shows.
        radius = min(rect.height() * 0.95, self.width() / 2 - 4, self.height() / 2 - 4)
        if radius < 8:
            return
        dpr = self.devicePixelRatioF()
        key = (dark, round(radius), dpr)
        if self._glow_key != key:
            self._glow_key = key
            size = max(2, round(2 * radius * dpr))
            image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
            image.fill(Qt.transparent)
            glow = QPainter(image)
            gradient = QRadialGradient(QPointF(size / 2, size / 2), size / 2)
            peak = 0.34 if dark else 0.20
            gradient.setColorAt(0.0, rgba("#2f8bff", peak))
            gradient.setColorAt(0.45, rgba("#22c3ee", peak * 0.45))
            gradient.setColorAt(1.0, rgba("#22c3ee", 0.0))
            glow.fillRect(image.rect(), QBrush(gradient))
            glow.end()
            image.setDevicePixelRatio(dpr)
            self._glow_image = image
        centre = rect.center()
        painter.setOpacity(clamp(strength))
        painter.drawImage(QPointF(centre.x() - radius, centre.y() - radius), self._glow_image)
        painter.setOpacity(1.0)

    def _paint_still(self, painter, rect, dpr, dark, t):
        if self._ghost:
            painter.setOpacity(0.22 if dark else 0.18)
            painter.drawImage(rect, self.art.image(rect.height(), dpr, dark))
            painter.setOpacity(1.0)
            return
        sheen = None
        if self.motion:
            cycle = (t - self._born) % 7.0      # a light glint every 7 s
            if cycle < 0.9:
                sheen = cycle / 0.9 * 1.4 - 0.2
        if sheen is None:
            painter.drawImage(rect, self.art.image(rect.height(), dpr, dark))
            return
        diagonal = (QPointF(0, 1000), QPointF(LOGO_WIDTH, 0))
        outline = self.art.outline

        def mask(p):
            p.fillPath(outline, QColor("#ffffff"))
            p.strokePath(outline, QPen(QColor("#ffffff"), 6))

        def overlay(p):
            p.fillRect(QRectF(-10, -10, LOGO_WIDTH + 20, 1020),
                       _band_brush(*diagonal, sheen, 0.16, 0.30 if dark else 0.38))

        image = self._compose(rect, dpr, dark, mask, overlay)
        painter.drawImage(QPointF(rect.left() - 3, rect.top() - 3), image)

    def _paint_intro(self, painter, rect, dpr, dark, t):
        fronts = {}
        for name, (start, duration) in INTRO_SWEEPS.items():
            fronts[name] = ease_in_out_cubic((t - start) / duration) * (1 + INTRO_SOFT)
        sheen_start, sheen_length = INTRO_SHEEN
        sheen = None
        if sheen_start <= t <= sheen_start + sheen_length:
            sheen = (t - sheen_start) / sheen_length * 1.4 - 0.2
        paths, sweeps = self.art.paths, self.art.sweeps
        dilate = 3.0 * 1000.0 / max(1.0, rect.height())     # ~1.5 px around the edges

        def mask(p):
            for name in INTRO_ORDER:
                front = fronts[name]
                if front <= 0:
                    continue
                if front >= 1 + INTRO_SOFT:
                    brush = QBrush(QColor("#ffffff"))
                else:
                    brush = _sweep_brush(*sweeps[name], front, INTRO_SOFT)
                p.fillPath(paths[name], brush)
                p.strokePath(paths[name], QPen(brush, dilate))

        def overlay(p):
            for name in INTRO_ORDER:
                front = fronts[name]
                if 0 < front < 1 + INTRO_SOFT:
                    glow = _band_brush(*sweeps[name], front - INTRO_SOFT * 0.55,
                                       INTRO_SOFT * 0.45, 0.55 if dark else 0.75)
                    p.fillPath(paths[name], glow)
            if sheen is not None:
                p.fillRect(QRectF(-10, -10, LOGO_WIDTH + 20, 1020),
                           _band_brush(QPointF(0, 1000), QPointF(LOGO_WIDTH, 0),
                                       sheen, 0.16, 0.30 if dark else 0.42))

        settle = 0.955 + 0.045 * ease_out_cubic((t - 0.15) / 1.3)
        image = self._compose(rect, dpr, dark, mask, overlay)
        painter.save()
        centre = rect.center()
        painter.translate(centre)
        painter.scale(settle, settle)
        painter.translate(-centre)
        painter.drawImage(QPointF(rect.left() - 3, rect.top() - 3), image)
        painter.restore()
        self._paint_particles(painter, rect, dark, t)

    def _make_particles(self):
        rng = random.Random(20251112)
        particles = []
        counts = {"leaf": 18, "band": 26, "top": 26}
        centre = QPointF(LOGO_WIDTH / 2, 500)
        for name in INTRO_ORDER:
            path = self.art.paths[name]
            box = path.boundingRect()
            start_line, end_line = self.art.sweeps[name]
            dx, dy = end_line.x() - start_line.x(), end_line.y() - start_line.y()
            length2 = dx * dx + dy * dy or 1.0
            sweep_start, sweep_length = INTRO_SWEEPS[name]
            made = 0
            while made < counts[name]:
                target = QPointF(rng.uniform(box.left(), box.right()),
                                 rng.uniform(box.top(), box.bottom()))
                if not path.contains(target):
                    continue
                made += 1
                s = ((target.x() - start_line.x()) * dx + (target.y() - start_line.y()) * dy) / length2
                arrive = sweep_start + clamp(s) * sweep_length * 0.8
                travel = rng.uniform(0.45, 0.75)
                depart = max(0.0, arrive - travel)
                angle = rng.uniform(0, 2 * math.pi)
                distance = rng.uniform(620, 980)
                origin = QPointF(centre.x() + math.cos(angle) * distance,
                                 centre.y() + math.sin(angle) * distance * 0.75)
                mid = (origin + target) / 2
                nx, ny = -(target.y() - origin.y()), target.x() - origin.x()
                swirl = rng.uniform(0.18, 0.32) * (1 if rng.random() < 0.5 else -1)
                control = QPointF(mid.x() + nx * swirl, mid.y() + ny * swirl)
                particles.append((origin, control, target, depart, arrive,
                                  rng.uniform(1.3, 2.5), rng.random() < 0.5))
        return particles

    def _paint_particles(self, painter, rect, dark, t):
        if self._particles is None:
            self._particles = self._make_particles()
        scale = rect.height() / 1000.0
        painter.save()
        painter.setPen(Qt.NoPen)
        for origin, control, target, depart, arrive, size, cyan in self._particles:
            if t < depart or t > arrive + 0.12:
                continue
            u = ease_in_out_cubic((t - depart) / max(0.01, arrive - depart))
            a, b = (1 - u) ** 2, 2 * (1 - u) * u
            x = a * origin.x() + b * control.x() + u * u * target.x()
            y = a * origin.y() + b * control.y() + u * u * target.y()
            alpha = clamp((t - depart) / 0.18) * clamp((arrive + 0.12 - t) / 0.2)
            colour = ("#9ff0ff" if cyan else "#ffffff") if dark else ("#22b8f0" if cyan else "#2b6ff5")
            painter.setBrush(rgba(colour, 0.85 * alpha))
            point = QPointF(rect.left() + x * scale, rect.top() + y * scale)
            painter.drawEllipse(point, size, size)
        painter.restore()

    def _paint_liquid(self, painter, rect, dpr, dark, t):
        if self.motion:
            elapsed = clamp(t - self._level_time, 0.0, 0.5)
            self._level += (self._target - self._level) * (1 - math.exp(-elapsed * 5.0))
            if abs(self._target - self._level) < 0.001:
                self._level = self._target
        else:
            self._level = self._target
        self._level_time = t
        level = self._level
        art = self.art.image(rect.height(), dpr, dark)
        painter.setOpacity(0.22 if dark else 0.17)
        painter.drawImage(rect, art)
        painter.setOpacity(1.0)
        if level <= 0.002:
            return
        if level >= 0.998:
            painter.drawImage(rect, art)
            return
        phase = (t - self._born) if self.motion else 0.0
        amplitude = 24.0 * math.sqrt(clamp(4 * level * (1 - level)))

        def surface(offset, shift):
            base = 1000.0 * (1 - level) + offset
            points = []
            for i in range(37):
                x = -20 + i * (LOGO_WIDTH + 40) / 36
                y = (base + amplitude * math.sin(2 * math.pi * x / 520 + phase * 2.2 + shift)
                     + amplitude * 0.45 * math.sin(2 * math.pi * x / 300 - phase * 1.7 + shift * 1.3))
                points.append(QPointF(x, y))
            return points

        back = surface(-14.0, 2.1)
        front = surface(0.0, 0.0)

        def region(points):
            path = QPainterPath(QPointF(-20, 1060))
            for point in points:
                path.lineTo(point)
            path.lineTo(LOGO_WIDTH + 20, 1060)
            path.closeSubpath()
            return path

        def mask(p):
            p.fillPath(region(back), rgba("#ffffff", 0.45))
            p.setCompositionMode(QPainter.CompositionMode_Source)
            p.fillPath(region(front), QColor("#ffffff"))
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)

        def overlay(p):
            line = QPainterPath(front[0])
            for point in front[1:]:
                line.lineTo(point)
            p.strokePath(line, QPen(rgba("#ffffff", 0.55 if dark else 0.7), 9.0))

        image = self._compose(rect, dpr, dark, mask, overlay)
        painter.drawImage(QPointF(rect.left() - 3, rect.top() - 3), image)

    def _paint_badge(self, painter, rect, dark, t):
        if not self.motion:
            t = 10.0
        h = rect.height()
        radius = h * 0.155
        centre = QPointF(rect.left() + rect.width() * 0.80, rect.top() + h * 0.84)
        painter.save()
        painter.setPen(Qt.NoPen)
        if t < 1.0:
            ring = radius + h * 0.6 * ease_out_cubic(t)
            pen = QPen(rgba("#22c3ee", 0.38 * (1 - t)), 2.0)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(centre, ring, ring)
            painter.setPen(Qt.NoPen)
        scale = ease_out_back(t / 0.45)
        if scale > 0:
            painter.setBrush(QColor("#1b2233") if dark else QColor("#ffffff"))
            painter.drawEllipse(centre, (radius + 3.5) * scale, (radius + 3.5) * scale)
            fill = {"success": ("#0f7b0f", "#6ccb5f")}.get(self._badge_kind, ("#0f7b0f", "#6ccb5f"))
            painter.setBrush(QColor(fill[1] if dark else fill[0]))
            painter.drawEllipse(centre, radius * scale, radius * scale)
        progress = ease_out_cubic((t - 0.2) / 0.3)
        if progress > 0:
            r = radius
            points = [QPointF(centre.x() - 0.42 * r, centre.y() + 0.02 * r),
                      QPointF(centre.x() - 0.12 * r, centre.y() + 0.32 * r),
                      QPointF(centre.x() + 0.45 * r, centre.y() - 0.30 * r)]
            lengths = [math.dist((points[i].x(), points[i].y()), (points[i + 1].x(), points[i + 1].y()))
                       for i in range(2)]
            remaining = progress * sum(lengths)
            path = QPainterPath(points[0])
            for i, length in enumerate(lengths):
                if remaining <= 0:
                    break
                part = min(1.0, remaining / length)
                a, b = points[i], points[i + 1]
                path.lineTo(a + (b - a) * part)
                remaining -= length
            pen = QPen(QColor(0, 0, 0, 230) if dark else QColor("#ffffff"), max(1.6, r * 0.2))
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)
        painter.restore()


class LogoMark(QWidget):
    """The still logo, small (the title bar)."""

    def __init__(self, art: LogoArt, height: int, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.art = art
        self.setFixedSize(math.ceil(height * LOGO_WIDTH / 1000.0), height)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.drawImage(QRectF(self.rect()), self.art.image(self.height(), self.devicePixelRatioF()))
        painter.end()


# --------------------------------------------------------------- wordmark --

class Wordmark(QWidget):
    """"FlowBench" in the logo's colours, written in letter by letter."""

    TEXT = "FlowBench"
    STAGGER, DURATION, RISE = 0.045, 0.5, 10.0

    def __init__(self, pixel_size: int = 40, motion: bool = True, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.motion = motion
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        font = QFont()
        font.setFamilies(["Segoe UI Variable Display", "Segoe UI", "Microsoft YaHei UI",
                          "Microsoft YaHei", "PingFang SC"])
        font.setPixelSize(pixel_size)
        font.setWeight(QFont.DemiBold)
        self.setFont(font)
        metrics = QFontMetricsF(font)
        self._offsets = [metrics.horizontalAdvance(self.TEXT[:i]) for i in range(len(self.TEXT))]
        self._width = metrics.horizontalAdvance(self.TEXT)
        self._ascent = metrics.ascent()
        self.setFixedSize(math.ceil(self._width) + 8, math.ceil(metrics.height() + self.RISE / 2) + 4)
        self.setAccessibleName(self.TEXT)
        self._start: Optional[float] = None
        self._letters_key = None
        self._images = []

    def text(self) -> str:
        return self.TEXT

    def play(self, delay: float = 0.0):
        self._start = now() + delay if self.motion else None
        self.update()

    def finish(self):
        self._start = None
        self.update()

    def done(self) -> bool:
        return self._start is None or now() - self._start > \
            self.STAGGER * len(self.TEXT) + self.DURATION

    def is_animating(self) -> bool:
        return self._start is not None

    def _letters(self, dark: bool, dpr: float):
        """Each letter pre-drawn in its slice of the word's gradient (text
        with a gradient pen is slow to draw every frame)."""
        key = (dark, dpr)
        if self._letters_key != key:
            self._letters_key = key
            height = self.height()
            baseline = 2 + self._ascent
            gradient = QLinearGradient(4, 0, 4 + self._width, 0)
            gradient.setColorAt(0.0, QColor("#5aa8ff" if dark else "#0b5ff2"))
            gradient.setColorAt(1.0, QColor("#6ee6fa" if dark else "#16aee8"))
            pieces = [(0.0, self.TEXT)] + [(self._offsets[i], char) for i, char in enumerate(self.TEXT)]
            images = []
            for offset, text in pieces:
                image = QImage(max(1, round(self.width() * dpr)), max(1, round(height * dpr)),
                               QImage.Format_ARGB32_Premultiplied)
                image.fill(Qt.transparent)
                image.setDevicePixelRatio(dpr)
                painter = QPainter(image)
                painter.setRenderHint(QPainter.Antialiasing)
                painter.setRenderHint(QPainter.TextAntialiasing)
                painter.setFont(self.font())
                painter.setPen(QPen(QBrush(gradient), 1))
                painter.drawText(QPointF(4 + offset, baseline), text)
                painter.end()
                images.append(image)
            self._images = images
        return self._images

    def paintEvent(self, event):
        painter = QPainter(self)
        images = self._letters(isDarkTheme(), self.devicePixelRatioF())
        elapsed = None if self._start is None else now() - self._start
        if elapsed is not None and elapsed > self.STAGGER * len(self.TEXT) + self.DURATION:
            self._start = elapsed = None
        if elapsed is None:
            painter.drawImage(QPointF(0, 0), images[0])
        else:
            for index in range(len(self.TEXT)):
                u = ease_out_cubic((elapsed - index * self.STAGGER) / self.DURATION)
                if u <= 0:
                    continue
                painter.setOpacity(u)
                painter.drawImage(QPointF(0, (1 - u) * self.RISE), images[index + 1])
        painter.end()


# ------------------------------------------------------------ small parts --

class Pill(QWidget):
    """A small rounded label, e.g. the version on the welcome page."""

    def __init__(self, text: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        font = QFont()
        font.setFamilies(["Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC"])
        font.setPixelSize(12)
        font.setWeight(QFont.DemiBold)
        self.setFont(font)
        self._text = ""
        self.setText(text)

    def text(self) -> str:
        return self._text

    def setText(self, text: str):
        self._text = text
        metrics = QFontMetricsF(self.font())
        self.setFixedSize(math.ceil(metrics.horizontalAdvance(text)) + 22, 22)
        self.setAccessibleName(text)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        dark = isDarkTheme()
        painter.setPen(QPen(rgba("#78b4ff", 0.30) if dark else rgba("#0b5ff2", 0.16), 1))
        painter.setBrush(rgba("#3d8bff", 0.16) if dark else rgba("#0b6ff2", 0.08))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 10.5, 10.5)
        painter.setPen(QColor("#9cc8ff") if dark else QColor("#0b56d6"))
        painter.setFont(self.font())
        painter.drawText(QRectF(self.rect()), Qt.AlignCenter, self._text)
        painter.end()


class GlassCard(QFrame):
    """A translucent rounded panel that lets the animated background through."""

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        dark = isDarkTheme()
        painter.setPen(QPen(QColor(255, 255, 255, 24) if dark else QColor(15, 40, 90, 22), 1))
        painter.setBrush(QColor(255, 255, 255, 14) if dark else QColor(255, 255, 255, 190))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)
        painter.end()


class FlowProgressBar(ProgressBar):
    """Progress in the logo's blue-to-cyan gradient, with a sheen gliding
    along the filled part while it is running."""

    def __init__(self, parent: Optional[QWidget] = None, motion: bool = True):
        super().__init__(parent, useAni=motion)
        self.motion = motion
        self.setFixedHeight(6)
        self._born = now()

    def is_animating(self) -> bool:
        """The sheen moves while the bar is part-way and not stopped."""
        return (self.motion and self.minimum() < self.value() < self.maximum()
                and not (self.isError() or self.isPaused()))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        dark = isDarkTheme()
        rect = QRectF(self.rect())
        radius = rect.height() / 2
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 30) if dark else QColor(10, 40, 90, 22))
        painter.drawRoundedRect(rect, radius, radius)
        span = self.maximum() - self.minimum()
        fraction = clamp((self.val - self.minimum()) / span) if span > 0 else 0.0
        width = rect.width() * fraction
        if width >= 1:
            bar = QRectF(rect.left(), rect.top(), max(width, rect.height()), rect.height())
            if self.isError() or self.isPaused():
                painter.setBrush(self.barColor())
            else:
                gradient = QLinearGradient(rect.left(), 0, rect.right(), 0)
                gradient.setColorAt(0.0, QColor("#3d8bff" if dark else "#1565f5"))
                gradient.setColorAt(1.0, QColor("#5be0f7" if dark else "#22c3ee"))
                painter.setBrush(QBrush(gradient))
            painter.drawRoundedRect(bar, radius, radius)
            if self.motion and not (self.isError() or self.isPaused()) and fraction < 1:
                cycle = ((now() - self._born) % 1.8) / 1.8
                centre = bar.left() + cycle * (bar.width() + 120) - 60
                sheen = QLinearGradient(centre - 60, 0, centre + 60, 0)
                sheen.setColorAt(0.0, rgba("#ffffff", 0.0))
                sheen.setColorAt(0.5, rgba("#ffffff", 0.45 if not dark else 0.35))
                sheen.setColorAt(1.0, rgba("#ffffff", 0.0))
                painter.setClipRect(bar)
                painter.setBrush(QBrush(sheen))
                painter.drawRoundedRect(bar, radius, radius)
        painter.end()


# ------------------------------------------------------------ transitions --

def fade_in(widget: QWidget, delay: float, duration: float = 0.42, motion: bool = True):
    """Fade ``widget`` in after ``delay`` seconds; returns the animation."""
    if not motion:
        return None
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(0.0)
    widget.setGraphicsEffect(effect)
    group = QSequentialAnimationGroup(widget)
    group.addPause(int(delay * 1000))
    animation = QPropertyAnimation(effect, b"opacity")
    animation.setDuration(int(duration * 1000))
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    animation.setEasingCurve(QEasingCurve.OutCubic)
    group.addAnimation(animation)

    def done():
        if widget.graphicsEffect() is effect:
            widget.setGraphicsEffect(None)
    group.finished.connect(done)
    group.start(QAbstractAnimation.DeleteWhenStopped)
    return group


def finish_fade(widget: QWidget):
    """Show a widget that is still fading in, at once."""
    for group in widget.findChildren(QSequentialAnimationGroup):
        group.setCurrentTime(group.totalDuration())
    if isinstance(widget.graphicsEffect(), QGraphicsOpacityEffect):
        widget.setGraphicsEffect(None)


class PageStack(QStackedWidget):
    """A page stack whose pages slide in (from the right going forward,
    from the left going back) and fade in."""

    SLIDE, DURATION = 36, 0.32

    def __init__(self, parent: Optional[QWidget] = None, motion: bool = True):
        super().__init__(parent)
        self.motion = motion
        self._running = None

    def setCurrentWidget(self, widget: QWidget, back: bool = False):
        previous = self.currentWidget()
        self._finish()
        super().setCurrentWidget(widget)
        if not self.motion or previous is None or previous is widget or not self.isVisible():
            return
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(0.0)
        widget.setGraphicsEffect(effect)
        group = QParallelAnimationGroup(self)
        slide = QPropertyAnimation(widget, b"pos")
        slide.setDuration(int(self.DURATION * 1000))
        slide.setStartValue(QPoint(-self.SLIDE if back else self.SLIDE, 0))
        slide.setEndValue(QPoint(0, 0))
        slide.setEasingCurve(QEasingCurve.OutCubic)
        fade = QPropertyAnimation(effect, b"opacity")
        fade.setDuration(int(self.DURATION * 820))
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.OutCubic)
        group.addAnimation(slide)
        group.addAnimation(fade)
        group.finished.connect(self._finish)
        self._running = (group, widget, effect)
        group.start()

    def _finish(self):
        running, self._running = self._running, None
        if running is None:
            return
        group, widget, effect = running
        group.stop()
        widget.move(0, 0)
        if widget.graphicsEffect() is effect:
            widget.setGraphicsEffect(None)
        group.deleteLater()


class FadeOverlay(QWidget):
    """Fades a snapshot of the window out over its new look (a language or
    theme change)."""

    DURATION = 0.3

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._snapshot = None
        self._start = 0.0
        self.hide()

    def start(self, snapshot):
        self._snapshot = snapshot
        self._start = now()
        self.setGeometry(self.parentWidget().rect())
        self.raise_()
        self.show()
        self.update()

    def active(self) -> bool:
        return self._snapshot is not None

    def paintEvent(self, event):
        if self._snapshot is None:
            return
        progress = (now() - self._start) / self.DURATION
        if progress >= 1:
            self._snapshot = None
            QTimer.singleShot(0, self.hide)
            return
        painter = QPainter(self)
        painter.setOpacity(1 - ease_out_cubic(progress))
        painter.drawPixmap(0, 0, self._snapshot)
        painter.end()
