"""图表组件：基于 Qt 官方 QtCharts（QChartView）。

交互（只缩放时间轴，纵轴自动适配可见的那一段数据）：
- 滚轮：以鼠标所在时间点为中心平滑缩放一段（带缓动动画），缩到全貌时
  自动恢复实时跟随；已是全貌时继续向外滚动交给页面滚动
- 左键拖动：放大后左右平移，可随时拖回原来的位置
- 双击：平滑恢复全貌并恢复实时跟随
"""
import math

from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis
from PySide6.QtCore import (QEasingCurve, QMargins, QPointF, Qt,
                            QVariantAnimation)
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy
from qfluentwidgets import isDarkTheme
from qfluentwidgets.common.config import qconfig

ACCENT = "#0078D4"
PURPLE = "#8764B8"
GREEN = "#107C10"

LINE_WIDTH = 3  # 曲线加粗

ZOOM_STEP = 0.8       # 滚轮每一格把可见时间段缩到 80%（反向放大到 125%）
MIN_SPAN = 5.0        # 最多放大到 5 个采样点
ZOOM_MS = 240         # 缩放动画时长


def _nice_step(span: float, ticks: int = 4) -> float:
    """时间轴刻度间隔：取 1/2/5×10^n，且不小于 1 个采样点。"""
    raw = max(span / max(1, ticks), 1e-9)
    base = 10 ** math.floor(math.log10(raw))
    for mult in (1, 2, 5, 10):
        if raw <= mult * base:
            return max(1.0, mult * base)
    return max(1.0, 10 * base)


class HoverChart(QChartView):
    """实时滚动折线图（Qt Charts 官方实现）。"""

    def __init__(self, window_points: int = 60, y_min: float | None = 0.0):
        self._chart = QChart()
        super().__init__(self._chart)
        self._win = window_points
        self._y_min = y_min if y_min is not None else 0.0
        self._auto = True          # 跟随实时数据（显示全貌）
        self._view = None          # 放大时的可见时间段 (x0, x1)
        self._target = None        # 动画目标时间段
        self._follow_after = False  # 动画结束后是否恢复实时跟随
        self._pan = None           # 拖动平移：(起点像素 x, x0, x1)
        self._series: dict[str, tuple[QLineSeries, str]] = {}

        # 外观：透明背景融入卡片，图例置顶
        self.setRenderHint(QPainter.Antialiasing)
        # 不再使用框选放大：放大后无法拖回，体验差。
        self.setRubberBand(QChartView.RubberBand.NoRubberBand)
        self.setMouseTracking(True)
        self._chart.legend().setAlignment(Qt.AlignTop)
        self._chart.legend().setBackgroundVisible(False)
        self._chart.setBackgroundVisible(False)
        self._chart.setPlotAreaBackgroundVisible(False)
        self._chart.setMargins(QMargins(0, 0, 0, 0))  # 紧凑边距
        # QChart 默认在图例和绘图区外再留约 9px 的布局边距；在卡片里这部分
        # 只会把绘图区压扁（主页 860px 高时纵轴只剩 2~3 个刻度）。
        self._chart.layout().setContentsMargins(0, 0, 0, 0)
        self._chart.setBackgroundRoundness(0)
        self.setFrameShape(QChartView.Shape.NoFrame)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(170)

        # 坐标轴
        self._ax = QValueAxis()
        self._ay = QValueAxis()
        for ax in (self._ax, self._ay):
            ax.setLabelFormat("%g")
        self._ax.setGridLineVisible(False)
        # 时间轴刻度固定在整数采样点上，平移/缩放时刻度跟着数据走而不是乱跳
        self._ax.setTickType(QValueAxis.TickType.TicksDynamic)
        self._ax.setTickAnchor(0)
        self._chart.addAxis(self._ax, Qt.AlignBottom)
        self._chart.addAxis(self._ay, Qt.AlignLeft)
        self._set_x_range(0.0, float(self._win))
        self._ay.setRange(self._y_min, 100)   # 尚无数据时显示 0~100

        self._zoom_ani = QVariantAnimation(self)
        self._zoom_ani.setDuration(ZOOM_MS)
        self._zoom_ani.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._zoom_ani.valueChanged.connect(self._on_zoom_frame)
        self._zoom_ani.finished.connect(self._on_zoom_finished)

        # 主题自适应配色（深色→浅字，浅色→深字）；主题切换时实时刷新
        self._apply_theme()
        qconfig.themeChanged.connect(self._apply_theme)

    def _apply_theme(self, *_):
        """按当前深/浅色主题调整文字与网格颜色。"""
        dark = isDarkTheme()
        label = QColor("#DDD") if dark else QColor("#222")     # 图例文字
        axis = QColor("#999") if dark else QColor("#555")      # 坐标刻度
        grid = QColor("#3A3A3A") if dark else QColor("#E2E2E2")  # 网格线
        line = QColor("#666") if dark else QColor("#BBB")      # 轴线
        self._chart.legend().setLabelColor(label)
        self._ax.setLabelsColor(axis)
        self._ay.setLabelsColor(axis)
        self._ay.setGridLineColor(grid)
        self._ax.setGridLineColor(grid)
        self._ax.setLinePenColor(line)
        self._ay.setLinePenColor(line)

    # ---- public ----

    def add_series(self, name: str, color: str, unit: str = ""):
        s = QLineSeries(self)
        s.setName(name)
        pen = QPen(QColor(color), LINE_WIDTH)  # 加粗曲线
        pen.setCosmetic(True)  # 缩放时保持视觉线宽
        s.setPen(pen)
        s.setPointsVisible(False)
        self._chart.addSeries(s)
        s.attachAxis(self._ax)
        s.attachAxis(self._ay)
        self._series[name] = (s, unit)
        return s

    def addLegend(self, offset=None):
        """兼容旧 API：Qt Charts 图例内置，无需调用。"""
        pass

    def is_zoomed(self) -> bool:
        """是否处于放大（暂停实时跟随）状态。"""
        return not self._auto

    def visible_range(self) -> tuple[float, float]:
        return self._ax.min(), self._ax.max()

    def set_window_points(self, window_points: int):
        """安全调整实时窗口大小，并恢复自动跟随。"""
        try:
            points = int(window_points)
        except (TypeError, ValueError):
            return self._win
        self._win = max(2, points)
        self._zoom_ani.stop()
        self._end_pan()
        self._target = None
        self._view = None
        self._auto = True
        self._fit_axes_to_series()
        return self._win

    def reset_zoom(self, animated: bool = True):
        """恢复全貌并恢复实时跟随。"""
        self._end_pan()
        if animated:
            self._animate_to(self._full_range(), follow=True)
        else:
            self._zoom_ani.stop()
            self._target = None
            self._view = None
            self._auto = True
            self._fit_axes_to_series()

    def set_data(self, data_dict: dict[str, list[float]]):
        """更新曲线数据；实时跟随时坐标轴显示全貌，放大时保持当前时间段。"""
        for name, (s, _unit) in self._series.items():
            y = data_dict.get(name, [])
            yv = y[-self._win:] if len(y) > self._win else y
            s.replace([QPointF(i, v) for i, v in enumerate(yv)])
        if self._auto:
            self._fit_axes_to_series()
        elif self._zoom_ani.state() != QVariantAnimation.State.Running:
            self._set_x_range(*self._clamp(*self.visible_range()))

    # ---- 坐标轴 ----

    def _full_range(self) -> tuple[float, float]:
        n_max = max((s.count() for s, _unit in self._series.values()), default=0)
        return 0.0, float(max(self._win, n_max, 1))

    def _clamp(self, x0: float, x1: float) -> tuple[float, float]:
        """把时间段限制在数据范围内，保持宽度不变。"""
        lo, hi = self._full_range()
        span = min(max(x1 - x0, 1e-6), hi - lo)
        x0 = max(lo, min(x0, hi - span))
        return x0, x0 + span

    def _set_x_range(self, x0: float, x1: float):
        self._ax.setRange(x0, x1)
        self._ax.setTickInterval(_nice_step(x1 - x0))
        self._fit_y(x0, x1)

    def _fit_y(self, x0: float, x1: float):
        """纵轴按可见时间段内的数据自适应（含两侧相邻点，避免线段被截断）。"""
        y_max = self._y_min
        for series, _unit in self._series.values():
            for point in series.pointsVector():
                if x0 - 1 <= point.x() <= x1 + 1:
                    y_max = max(y_max, point.y())
        top = y_max if y_max > self._y_min else self._y_min + 1.0
        self._ay.setRange(self._y_min, top)
        self._ay.applyNiceNumbers()

    def _fit_axes_to_series(self):
        """按当前曲线与窗口大小重排坐标轴（全貌）。"""
        self._set_x_range(*self._full_range())

    # ---- 平滑缩放 ----

    def _animate_to(self, target: tuple[float, float], follow: bool = False):
        self._zoom_ani.stop()
        x0, x1 = self.visible_range()
        self._target = target
        self._follow_after = follow
        self._auto = False          # 动画过程中不让实时数据把坐标轴拉回全貌
        self._view = None if follow else target
        self._zoom_ani.setStartValue(QPointF(x0, x1))
        self._zoom_ani.setEndValue(QPointF(*target))
        self._zoom_ani.start()
        self._update_cursor()

    def _on_zoom_frame(self, value):
        if isinstance(value, QPointF):
            self._set_x_range(value.x(), value.y())

    def _on_zoom_finished(self):
        self._target = None
        if self._follow_after:
            self._follow_after = False
            self._view = None
            self._auto = True
            self._fit_axes_to_series()
        else:
            self._view = self.visible_range()
        self._update_cursor()

    def _value_at(self, pos: QPointF) -> float:
        """鼠标位置对应的时间轴数值。"""
        plot = self._chart.plotArea()
        x0, x1 = self.visible_range()
        if plot.width() <= 0:
            return (x0 + x1) / 2
        chart_pos = self._chart.mapFromScene(self.mapToScene(pos.toPoint()))
        frac = (chart_pos.x() - plot.left()) / plot.width()
        return x0 + max(0.0, min(1.0, frac)) * (x1 - x0)

    def wheelEvent(self, ev):
        steps = ev.angleDelta().y() / 120.0
        if not steps:
            ev.ignore()
            return
        lo, hi = self._full_range()
        full_span = hi - lo
        x0, x1 = self._target or self.visible_range()
        span = max(x1 - x0, 1e-6)
        if steps < 0 and span >= full_span - 1e-6:
            ev.ignore()  # 已是全貌：交给页面滚动
            return
        new_span = span * (ZOOM_STEP ** steps)
        new_span = max(min(MIN_SPAN, full_span), min(full_span, new_span))
        anchor = self._value_at(ev.position())
        ratio = (anchor - x0) / span
        n0 = anchor - ratio * new_span
        if new_span >= full_span - 1e-6:
            self._animate_to((lo, hi), follow=True)
        else:
            self._animate_to(self._clamp(n0, n0 + new_span))
        ev.accept()

    # ---- 拖动平移 ----

    def _update_cursor(self):
        if self._pan is not None:
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
        elif not self._auto and not self._follow_after:
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.viewport().unsetCursor()

    def _end_pan(self):
        if self._pan is not None:
            self._pan = None
            self._update_cursor()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton and self.is_zoomed() and not self._follow_after:
            self._zoom_ani.stop()
            self._target = None
            x0, x1 = self.visible_range()
            self._pan = (ev.position().x(), x0, x1)
            self._update_cursor()
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseReleaseEvent(self, ev):
        if self._pan is not None and ev.button() == Qt.MouseButton.LeftButton:
            self._view = self.visible_range()
            self._end_pan()
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    def mouseDoubleClickEvent(self, ev):
        """双击：平滑恢复全貌并恢复实时跟随。"""
        self.reset_zoom()
        ev.accept()

    # ---- 悬停数值 / 拖动 ----

    def mouseMoveEvent(self, ev):
        pos = ev.position()
        if self._pan is not None:
            start_x, x0, x1 = self._pan
            width = self._chart.plotArea().width()
            if width > 0:
                shift = -(pos.x() - start_x) / width * (x1 - x0)
                self._set_x_range(*self._clamp(x0 + shift, x1 + shift))
            ev.accept()
            return
        lines = []
        for name, (s, unit) in self._series.items():
            if s.count() == 0:
                continue
            v = self.chart().mapToValue(pos, s)
            idx = max(0, min(s.count() - 1, int(round(v.x()))))
            ys = s.at(idx).y()
            lines.append(f"{name}: {ys:.1f}{unit}")
        self.setToolTip("\n".join(lines) if lines else "")
        super().mouseMoveEvent(ev)
