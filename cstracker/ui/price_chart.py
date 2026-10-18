"""Persistent, square price chart for the legacy item sidebar."""

from datetime import timedelta
import tkinter as tk
import numpy as np
import matplotlib.dates as mdates
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg


def dispose_canvas(canvas):
    if canvas is None:
        return
    widget = canvas.get_tk_widget()
    for attr in ("_idle_draw_id", "_event_loop_id"):
        timer = getattr(canvas, attr, None)
        if timer:
            widget.after_cancel(timer)
            setattr(canvas, attr, None)
    for connection in getattr(canvas.figure, "_tracker_connections", []):
        canvas.mpl_disconnect(connection)
    widget.destroy()
    canvas.figure.clear()


class ItemPriceChart:
    def __init__(self, parent, colors, font, interactions):
        self.colors = colors
        self.rows = []
        self._signature = None
        self.range = tk.StringVar(parent, value="30Д")
        controls = tk.Frame(parent, bg=colors["bg_panel"])
        controls.pack(fill=tk.X, pady=(0, 4))
        for key in ("7Д", "30Д", "90Д", "1Г", "Все"):
            tk.Button(controls, text=key, command=lambda k=key: self.set_range(k),
                      bg=colors["bg_elevated"], fg=colors["fg_dim"], bd=0,
                      padx=5, pady=2, font=font).pack(side=tk.RIGHT, padx=2)
        self.figure = Figure(figsize=(3, 3), dpi=96, facecolor=colors["bg_panel"])
        self.axes = self.figure.add_subplot(111)
        self.axes.set_facecolor(colors["entry_bg"])
        self.axes.tick_params(colors=colors["fg_dim"], labelsize=7)
        self.axes.set_title("Динамика цены", color=colors["fg_dim"], fontsize=8)
        self.axes.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
        self.axes.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3, maxticks=5))
        for spine in self.axes.spines.values():
            spine.set_color(colors["border"])
        self.axes.grid(True, alpha=0.25, color=colors["border"])
        self.lines, self.toggles, self.series = [], [], []
        for index, (label, color, style) in enumerate((
            ("Sell orders", "#35d0ba", "-"), ("Buy orders", "#7ad3ff", "-"),
            ("Себестоимость", "#a78bfa", "--"), ("Lis +2%", "#f59e0b", "-."),
        )):
            if index % 2 == 0:
                legend = tk.Frame(parent, bg=colors["bg_panel"])
                legend.pack(fill=tk.X)
            toggle = tk.BooleanVar(parent, value=True)
            tk.Checkbutton(legend, text=label, variable=toggle, command=self.redraw,
                           bg=colors["bg_panel"], fg=color, activeforeground=color,
                           activebackground=colors["bg_panel"], selectcolor=colors["entry_bg"],
                           font=font).pack(side=tk.LEFT, padx=(0, 8))
            line, = self.axes.plot([], [], color=color, linestyle=style, linewidth=1.5,
                                   marker="o" if index != 2 else None, markersize=2.5)
            self.lines.append(line)
            self.toggles.append(toggle)
            self.series.append({"line": line, "label": label, "color": color, "point_labels": []})
        self.empty = self.axes.text(0.5, 0.5, "Нет истории цены", transform=self.axes.transAxes,
                                   color=colors["fg_dim"], ha="center", va="center", fontsize=8)
        self.figure.subplots_adjust(left=0.19, right=0.96, bottom=0.15, top=0.91)
        self.canvas = FigureCanvasTkAgg(self.figure, master=parent)
        self.canvas.get_tk_widget().configure(width=280, height=280, cursor="crosshair")
        self.canvas.get_tk_widget().pack(anchor=tk.CENTER, pady=(6, 0))
        self.annotation = interactions.attach(self.figure, self.axes, self.series, hover_radius=12)

    def set_data(self, rows):
        signature = tuple(rows)
        if signature == self._signature:
            return
        self._signature = signature
        self.rows = rows
        self.annotation.set_visible(False)
        self.redraw()

    def clear(self):
        self.set_data([])

    def set_range(self, key):
        self.range.set(key)
        self.redraw()

    def redraw(self):
        rows = self.rows
        if rows and self.range.get() != "Все":
            days = {"7Д": 7, "30Д": 30, "90Д": 90, "1Г": 365}[self.range.get()]
            cutoff = rows[-1][0] - timedelta(days=days)
            rows = [row for row in rows if row[0] >= cutoff]
        # Bound rendering cost while retaining extrema in each time bucket.
        if len(rows) > 800:
            indices = {0, len(rows) - 1}
            for bucket in np.array_split(np.arange(len(rows)), 100):
                indices.add(int(bucket[0]))
                indices.add(int(bucket[-1]))
                for column in range(1, 5):
                    valid = [int(i) for i in bucket if rows[int(i)][column] is not None]
                    if valid:
                        indices.add(min(valid, key=lambda i: rows[i][column]))
                        indices.add(max(valid, key=lambda i: rows[i][column]))
            rows = [rows[i] for i in sorted(indices)]
        dates = [row[0] for row in rows]
        labels = [date.strftime("%d.%m.%Y %H:%M") for date in dates]
        visible_values = []
        for index, line in enumerate(self.lines):
            values = [float(row[index + 1]) if row[index + 1] is not None else np.nan for row in rows]
            line.set_data(dates, values)
            line.set_visible(self.toggles[index].get())
            self.series[index]["point_labels"] = labels
            if line.get_visible():
                visible_values.extend(value for value in values if np.isfinite(value))
        self.empty.set_visible(not rows)
        if rows:
            left, right = dates[0], dates[-1]
            pad = max((right - left) * 0.025, timedelta(minutes=30))
            self.axes.set_xlim(left - pad, right + pad)
        if visible_values:
            low, high = min(visible_values), max(visible_values)
            pad = max((high - low) * 0.12, 1)
            self.axes.set_ylim(max(0, low - pad), high + pad)
        reset = getattr(self.annotation, "_tracker_set_home", None)
        if reset:
            reset()
        self.canvas.draw_idle()

    def close(self):
        dispose_canvas(self.canvas)
