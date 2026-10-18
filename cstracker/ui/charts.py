"""Reusable matplotlib interactions for embedded application charts."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
import time

import numpy as np
from matplotlib.backends.backend_tkagg import NavigationToolbar2Tk


class ChartInteractionController:
    """Adds tooltip, pin, wheel zoom and right-button pan to a chart."""

    def __init__(self, colors):
        self._colors = colors

    def attach(self, fig, ax, series, *, hover_radius=14):
        colors = self._colors
        initial_xlim = ax.get_xlim()
        initial_ylim = ax.get_ylim()
        state = {"pinned": False, "drag": None, "point": None, "motion_at": 0.0}
        annotation = ax.annotate(
            "", xy=(0, 0), xytext=(12, 12), textcoords="offset points",
            color="#f2f6fb", fontsize=9,
            bbox=dict(
                boxstyle="round,pad=0.45", fc=colors["bg_elevated"],
                ec=colors["accent"], alpha=0.97,
            ),
            arrowprops=dict(arrowstyle="->", color=colors["accent"]),
            zorder=20,
        )
        annotation.set_visible(False)

        def set_home():
            nonlocal initial_xlim, initial_ylim
            initial_xlim, initial_ylim = ax.get_xlim(), ax.get_ylim()
            state.update(pinned=False, drag=None, point=None)
            annotation.set_visible(False)

        annotation._tracker_set_home = set_home

        def nearest_point(event):
            if event.inaxes != ax or event.x is None or event.y is None:
                return None
            best = None
            for entry in series:
                line = entry["line"]
                if not line.get_visible():
                    continue
                points = line.get_xydata()
                if len(points) == 0:
                    continue
                finite_mask = np.isfinite(points[:, 0]) & np.isfinite(points[:, 1])
                if not np.any(finite_mask):
                    continue
                original_indices = np.where(finite_mask)[0]
                finite_points = points[finite_mask]
                display_points = ax.transData.transform(finite_points)
                distances = np.hypot(
                    display_points[:, 0] - event.x,
                    display_points[:, 1] - event.y,
                )
                compact_index = int(np.argmin(distances))
                index = int(original_indices[compact_index])
                distance = float(distances[compact_index])
                if distance <= hover_radius and (best is None or distance < best[0]):
                    best = (distance, entry, index, points[index])
            return best

        def show_point(found):
            _distance, entry, index, point = found
            point_key = (id(entry["line"]), index)
            if state["point"] == point_key and annotation.get_visible():
                return
            state["point"] = point_key
            labels = entry.get("point_labels") or []
            point_label = labels[index] if index < len(labels) else f"Точка {index + 1}"
            value = float(point[1])
            annotation.xy = (point[0], point[1])
            annotation.set_text(
                f"{entry['label']}\n{point_label}\n{value:,.2f} ₽".replace(",", " ")
            )
            color = entry.get("color", colors["accent"])
            annotation.get_bbox_patch().set_edgecolor(color)
            annotation.arrow_patch.set_color(color)
            annotation.set_visible(True)
            fig.canvas.draw_idle()

        def on_motion(event):
            now = time.monotonic()
            if now - state["motion_at"] < 0.05:
                return
            state["motion_at"] = now
            toolbar = getattr(fig.canvas, "toolbar", None)
            if toolbar is not None and getattr(toolbar, "mode", ""):
                return
            drag = state["drag"]
            if drag and event.inaxes == ax and event.xdata is not None and event.ydata is not None:
                start_x, start_y, xlim, ylim = drag
                dx = (event.x - start_x) / ax.bbox.width * (xlim[1] - xlim[0])
                dy = (event.y - start_y) / ax.bbox.height * (ylim[1] - ylim[0])
                ax.set_xlim(xlim[0] - dx, xlim[1] - dx)
                ax.set_ylim(ylim[0] - dy, ylim[1] - dy)
                fig.canvas.draw_idle()
                return
            if state["pinned"]:
                return
            found = nearest_point(event)
            if found:
                show_point(found)
            elif annotation.get_visible():
                annotation.set_visible(False)
                fig.canvas.draw_idle()

        def on_press(event):
            toolbar = getattr(fig.canvas, "toolbar", None)
            if toolbar is not None and getattr(toolbar, "mode", ""):
                return
            if event.dblclick and event.inaxes == ax:
                state["pinned"] = False
                state["drag"] = None
                annotation.set_visible(False)
                ax.set_xlim(initial_xlim)
                ax.set_ylim(initial_ylim)
                fig.canvas.draw_idle()
                return
            if event.button == 3 and event.inaxes == ax:
                if event.xdata is not None and event.ydata is not None:
                    state["drag"] = (event.x, event.y, ax.get_xlim(), ax.get_ylim())
                return
            if event.button != 1:
                return
            found = nearest_point(event)
            state["pinned"] = bool(found)
            if found:
                show_point(found)
            else:
                annotation.set_visible(False)
                fig.canvas.draw_idle()

        def on_release(event):
            toolbar = getattr(fig.canvas, "toolbar", None)
            if toolbar is not None and getattr(toolbar, "mode", ""):
                return
            if event.button == 3:
                state["drag"] = None

        def on_scroll(event):
            if event.inaxes != ax or event.xdata is None or event.ydata is None:
                return
            toolbar = getattr(fig.canvas, "toolbar", None)
            if toolbar is not None and getattr(toolbar, "mode", ""):
                return
            if event.button not in ("up", "down"):
                return
            scale = 0.8 if event.button == "up" else 1.25
            xlim, ylim = ax.get_xlim(), ax.get_ylim()
            x, y = event.xdata, event.ydata
            original_width = max(initial_xlim[1] - initial_xlim[0], 1e-8)
            original_height = max(initial_ylim[1] - initial_ylim[0], 1e-8)
            if not original_width * 1e-5 <= (xlim[1] - xlim[0]) * scale <= original_width * 10:
                return
            if not original_height * 1e-5 <= (ylim[1] - ylim[0]) * scale <= original_height * 10:
                return
            ax.set_xlim(x - (x - xlim[0]) * scale, x + (xlim[1] - x) * scale)
            ax.set_ylim(y - (y - ylim[0]) * scale, y + (ylim[1] - y) * scale)
            state["point"] = None
            annotation.set_visible(False)
            fig.canvas.draw_idle()

        fig._tracker_connections = [
            fig.canvas.mpl_connect("motion_notify_event", on_motion),
            fig.canvas.mpl_connect("button_press_event", on_press),
            fig.canvas.mpl_connect("button_release_event", on_release),
            fig.canvas.mpl_connect("scroll_event", on_scroll),
        ]
        return annotation


def add_navigation_toolbar(canvas, parent, *, colors, font_small):
    toolbar_frame = tk.Frame(parent, bg=colors["bg"])
    toolbar_frame.pack(fill=tk.X, padx=10, pady=(0, 4))
    toolbar = NavigationToolbar2Tk(canvas, toolbar_frame, pack_toolbar=False)
    toolbar.update()
    controls = ttk.Frame(toolbar_frame, style="App.TFrame")
    controls.pack(side=tk.LEFT)
    for text, command in (
        ("Сброс", toolbar.home), ("Назад", toolbar.back),
        ("Вперёд", toolbar.forward), ("Сдвиг", toolbar.pan),
        ("Область", toolbar.zoom), ("Сохранить", toolbar.save_figure),
    ):
        ttk.Button(
            controls, text=text, style="ToolbarMuted.TButton", command=command,
        ).pack(side=tk.LEFT, padx=(0, 4))
    tk.Label(
        toolbar_frame,
        text="Колесо: масштаб · ПКМ: сдвиг · клик: закрепить · двойной клик: сброс",
        bg=colors["bg"], fg=colors["fg_dim"], font=font_small,
    ).pack(side=tk.RIGHT, padx=10)
    return toolbar
