"""Matplotlib canvases designed for the Qt detail panel."""

from __future__ import annotations

from datetime import datetime, timedelta
import math

import matplotlib.dates as mdates
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

from .theme import COLORS


SERIES = (
    ("sell_order_net_rub", "Sell orders", COLORS["accent"], "-"),
    ("buy_order_net_rub", "Buy orders", COLORS["blue"], "-"),
    ("buy_price_rub", "Себестоимость", "#a78bfa", "--"),
    ("lis_min_price_fee_rub", "Lis +2%", "#f5a000", "-."),
)


class PriceChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.figure = Figure(figsize=(4.1, 4.1), dpi=100, tight_layout=True)
        self.canvas = FigureCanvasQTAgg(self.figure)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.canvas.setMinimumSize(245, 245)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.canvas)
        self.rows = []
        self.visible = {item[0]: True for item in SERIES}
        self.range_days = 7
        self._annotation = None
        self._points = []
        self._pan = None
        self._drawing = False
        self.canvas.mpl_connect("motion_notify_event", self._hover)
        self.canvas.mpl_connect("scroll_event", self._zoom)
        self.canvas.mpl_connect("button_press_event", self._pan_start)
        self.canvas.mpl_connect("button_release_event", self._pan_end)
        self.canvas.mpl_connect("motion_notify_event", self._pan_move)
        self.draw([])

    def sizeHint(self):
        side = max(270, self.width())
        return QSize(side, side)

    def set_history(self, rows):
        self.rows = list(rows or [])
        self.draw()

    def set_range(self, days):
        self.range_days = days
        self.draw()

    def set_series_visible(self, key, visible):
        self.visible[key] = bool(visible)
        self.draw()

    def _filtered_rows(self):
        parsed = []
        for row in self.rows:
            try:
                stamp = datetime.fromisoformat(str(row.get("captured_at")))
            except (TypeError, ValueError):
                continue
            parsed.append((stamp, row))
        if self.range_days and parsed:
            threshold = parsed[-1][0] - timedelta(days=self.range_days)
            parsed = [item for item in parsed if item[0] >= threshold]
        return parsed

    def draw(self, rows=None):
        if self._drawing:
            return
        self._drawing = True
        try:
            self._draw_impl(rows)
        finally:
            self._drawing = False

    def _draw_impl(self, rows=None):
        if rows is not None:
            self.rows = list(rows)
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        self.figure.patch.set_facecolor(COLORS["panel"])
        ax.set_facecolor("#14202c")
        ax.tick_params(colors=COLORS["muted"], labelsize=8)
        for spine in ax.spines.values():
            spine.set_color(COLORS["border"])
        ax.grid(True, color="#2a3948", linewidth=0.6, alpha=0.65)
        ax.set_title("Динамика цены", color=COLORS["muted"], fontsize=9, pad=10)
        self._points = []

        parsed = self._filtered_rows()
        for key, label, color, style in SERIES:
            if not self.visible.get(key, True):
                continue
            points = [(stamp, row.get(key)) for stamp, row in parsed if row.get(key) is not None]
            if not points:
                continue
            xs = [point[0] for point in points]
            ys = [float(point[1]) for point in points]
            line, = ax.plot(
                xs, ys, color=color, linestyle=style, linewidth=1.8,
                marker="o", markersize=2.8, label=label,
            )
            self._points.append((key, label, line, xs, ys))

        if not self._points:
            ax.text(
                0.5, 0.5, "История цен появится\nпосле обновления",
                transform=ax.transAxes, ha="center", va="center",
                color=COLORS["muted"], fontsize=10,
            )
        else:
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
            ax.tick_params(axis="x", rotation=0)
        self._annotation = ax.annotate(
            "", xy=(0, 0), xytext=(10, 12), textcoords="offset points",
            bbox={"boxstyle": "round,pad=0.4", "fc": "#253747", "ec": "#55708a"},
            color="#ffffff", fontsize=8,
        )
        self._annotation.set_visible(False)
        self.figure.subplots_adjust(left=0.17, right=0.97, bottom=0.15, top=0.91)
        self.canvas.draw_idle()

    def _hover(self, event):
        if not event.inaxes or not self._annotation:
            return
        nearest = None
        for _key, label, line, xs, ys in self._points:
            contains, info = line.contains(event)
            if contains and info.get("ind"):
                index = int(info["ind"][0])
                nearest = (label, xs[index], ys[index])
                break
        if not nearest:
            if self._annotation.get_visible():
                self._annotation.set_visible(False)
                self.canvas.draw_idle()
            return
        label, stamp, value = nearest
        self._annotation.xy = (mdates.date2num(stamp), value)
        self._annotation.set_text(
            f"{label}\n{stamp:%d.%m.%Y %H:%M}\n{value:,.2f} ₽".replace(",", " ")
        )
        self._annotation.set_visible(True)
        self.canvas.draw_idle()

    def _zoom(self, event):
        if (
            self._drawing
            or event.button not in {"up", "down"}
            or not event.inaxes
            or event.xdata is None
            or event.ydata is None
        ):
            return
        try:
            scale = 0.8 if event.button == "up" else 1.25
            ax = event.inaxes
            x0, x1 = ax.get_xlim()
            y0, y1 = ax.get_ylim()
            values = (x0, x1, y0, y1, event.xdata, event.ydata)
            if not all(math.isfinite(float(value)) for value in values):
                return
            x_span, y_span = x1 - x0, y1 - y0
            if x_span <= 1e-9 or y_span <= 1e-9:
                return
            if event.button == "up" and (x_span < 1e-5 or y_span < 1e-5):
                return
            if event.button == "down" and (x_span > 1e9 or y_span > 1e12):
                return
            ax.set_xlim(
                event.xdata - (event.xdata - x0) * scale,
                event.xdata + (x1 - event.xdata) * scale,
            )
            ax.set_ylim(
                event.ydata - (event.ydata - y0) * scale,
                event.ydata + (y1 - event.ydata) * scale,
            )
            self.canvas.draw_idle()
        except (ArithmeticError, RuntimeError, TypeError, ValueError):
            return

    def _pan_start(self, event):
        if event.button != 3 or not event.inaxes:
            return
        self._pan = (
            event.inaxes,
            event.xdata,
            event.ydata,
            event.inaxes.get_xlim(),
            event.inaxes.get_ylim(),
        )

    def _pan_move(self, event):
        if not self._pan or event.xdata is None or event.ydata is None:
            return
        ax, start_x, start_y, xlim, ylim = self._pan
        dx, dy = event.xdata - start_x, event.ydata - start_y
        ax.set_xlim(xlim[0] - dx, xlim[1] - dx)
        ax.set_ylim(ylim[0] - dy, ylim[1] - dy)
        self.canvas.draw_idle()

    def _pan_end(self, _event):
        self._pan = None


class InventoryChartDialogContent(QWidget):
    def __init__(self, history, parent=None):
        super().__init__(parent)
        self.figure = Figure(figsize=(10, 6), dpi=100, tight_layout=True)
        self.canvas = FigureCanvasQTAgg(self.figure)
        layout = QVBoxLayout(self)
        toolbar = NavigationToolbar2QT(self.canvas, self)
        toolbar.setStyleSheet("border: 0; background: transparent;")
        layout.addWidget(toolbar)
        layout.addWidget(self.canvas)
        self._draw(history)

    def _draw(self, history):
        ax = self.figure.add_subplot(111)
        self.figure.patch.set_facecolor(COLORS["panel"])
        ax.set_facecolor("#14202c")
        points = []
        for row in history:
            try:
                points.append((datetime.fromisoformat(row["captured_at"]), row))
            except (TypeError, ValueError):
                pass
        if points:
            xs = [item[0] for item in points]
            ax.plot(
                xs,
                [float(item[1]["inventory_sell_gross_rub"] or 0) * 0.9 for item in points],
                color=COLORS["accent"], marker="o", markersize=3, label="Sell net -10%",
            )
            ax.plot(
                xs,
                [float(item[1]["inventory_buyorder_gross_rub"] or 0) * 0.9 for item in points],
                color=COLORS["blue"], marker="o", markersize=3, label="Buy net -10%",
            )
            ax.plot(
                xs,
                [float(item[1]["inventory_buy_cost_rub"] or 0) for item in points],
                color="#a78bfa", linestyle="--", label="Себестоимость",
            )
            ax.legend(facecolor=COLORS["panel"], labelcolor=COLORS["text"])
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
        else:
            ax.text(0.5, 0.5, "Нет снимков", transform=ax.transAxes, ha="center")
        ax.set_title("Динамика стоимости инвентаря", color=COLORS["text"])
        ax.grid(True, color=COLORS["border"], alpha=0.7)
        ax.tick_params(colors=COLORS["muted"])
        for spine in ax.spines.values():
            spine.set_color(COLORS["border"])
        self.canvas.draw()
