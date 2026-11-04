"""图表组件：基于 Qt 官方 QtCharts（QChartView）。

缩放交互为 Qt Charts 官方行为：
- 左键拖框选：放大所选区域
- 右键拖框选：缩小
- 滚轮：以鼠标为中心放大/缩小
- 双击：复位缩放并恢复自动跟随
"""
from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis
from PySide6.QtCore import QMargins, QPointF, Qt, QElapsedTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy
from qfluentwidgets import isDarkTheme
from qfluentwidgets.common.config import qconfig

ACCENT = "#0078D4"
PURPLE = "#8764B8"
GREEN = "#107C10"

LINE_WIDTH = 3  # 曲线加粗


class HoverChart(QChartView):
    """实时滚动折线图（Qt Charts 官方实现）。"""

    def __init__(self, window_points: int = 60, y_min: float | None = 0.0):
        self._chart = QChart()
        super().__init__(self._chart)
        self._win = window_points
        self._y_min = y_min if y_min is not None else 0.0
        self._auto = True
        self._series: dict[str, tuple[QLineSeries, str]] = {}

        # 外观：透明背景融入卡片，图例置顶
        self.setRenderHint(QPainter.Antialiasing)
        self.setRubberBand(QChartView.RectangleRubberBand)
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
        self._dbl_timer = QElapsedTimer()  # 双击复位时间标记

        # 坐标轴
        self._ax = QValueAxis()
        self._ay = QValueAxis()
        for ax in (self._ax, self._ay):
            ax.setLabelFormat("%g")
        self._ax.setGridLineVisible(False)
        self._ax.setRange(0, self._win)
        self._ay.setRange(self._y_min, 100)
        self._chart.addAxis(self._ax, Qt.AlignBottom)
        self._chart.addAxis(self._ay, Qt.AlignLeft)

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

    def set_window_points(self, window_points: int):
        """安全调整实时窗口大小，并恢复自动跟随。"""
        try:
            points = int(window_points)
        except (TypeError, ValueError):
            return self._win
        self._win = max(2, points)
        self.chart().zoomReset()
        self._auto = True
        self._fit_axes_to_series()
        return self._win

    def _fit_axes_to_series(self):
        """按当前曲线与窗口大小重排坐标轴。"""
        n_max = 0
        y_max = self._y_min
        for _name, (series, _unit) in self._series.items():
            n_max = max(n_max, series.count())
            for point in series.pointsVector():
                y_max = max(y_max, point.y())
        top = y_max if y_max > self._y_min else self._y_min + 1.0
        self._ax.setRange(0, max(self._win, n_max, 1))
        self._ay.setRange(self._y_min, top)
        self._ay.applyNiceNumbers()

    def set_data(self, data_dict: dict[str, list[float]]):
        """更新曲线数据；自动模式下坐标轴跟随最新数据。"""
        for name, (s, _unit) in self._series.items():
            y = data_dict.get(name, [])
            yv = y[-self._win:] if len(y) > self._win else y
            s.replace([QPointF(i, v) for i, v in enumerate(yv)])
        if self._auto:
            self._fit_axes_to_series()

    # ---- 缩放交互 ----

    def wheelEvent(self, ev):
        self._auto = False  # 用户手动缩放 → 暂停自动跟随
        if ev.angleDelta().y() > 0:
            self.chart().zoomIn(1.1)
        else:
            self.chart().zoomOut(1.1)
        ev.accept()

    def mouseReleaseEvent(self, ev):
        # 框选缩放结束后暂停自动跟随（双击复位后紧随的 release 除外）
        if ev.button() in (Qt.LeftButton, Qt.RightButton):
            if not self._dbl_timer.isValid() or self._dbl_timer.elapsed() > 400:
                self._auto = False
        super().mouseReleaseEvent(ev)

    def mouseDoubleClickEvent(self, ev):
        """双击：复位缩放并恢复自动跟随。"""
        self.chart().zoomReset()
        self._auto = True
        self._dbl_timer.start()
        # 立即按当前数据重排坐标轴
        self._fit_axes_to_series()
        ev.accept()

    # ---- 悬停数值 ----

    def mouseMoveEvent(self, ev):
        pos = ev.position()
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
