"""Общие виджеты дашборда для CS Skin Tracker (tkinter)."""

import tkinter as tk
from tkinter import ttk


def metric_card(parent, colors, fonts, title):
    """A compact KPI card that can be placed in a responsive grid."""
    outer = tk.Frame(
        parent, bg=colors["bg_card"], highlightbackground=colors["border_soft"],
        highlightthickness=1,
    )
    accent = tk.Frame(outer, bg=colors["accent_muted"], height=3)
    accent.pack(fill=tk.X)
    inner = tk.Frame(outer, bg=colors["bg_card"], padx=15, pady=6)
    inner.pack(fill=tk.BOTH, expand=True)

    body = tk.Frame(inner, bg=colors["bg_card"])
    body.pack(fill=tk.BOTH, expand=True)
    left = tk.Frame(body, bg=colors["bg_card"])
    left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    spark_canvas = tk.Canvas(
        body, width=88, height=24, bg=colors["bg_card"],
        highlightthickness=0, bd=0,
    )
    spark_canvas.pack(side=tk.RIGHT, padx=(8, 0))

    title_row = tk.Frame(left, bg=colors["bg_card"])
    title_row.pack(fill=tk.X)
    icon_var = tk.StringVar(value="")
    icon_lbl = tk.Label(
        title_row, textvariable=icon_var, bg=colors["bg_card"], fg=colors["accent"],
        font=fonts["icon"], anchor=tk.W,
    )
    icon_lbl.pack(side=tk.LEFT)
    tk.Label(
        title_row, text=title, bg=colors["bg_card"], fg=colors["fg_dim"],
        font=fonts["small"], anchor=tk.W,
    ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))
    value_var = tk.StringVar(value="—")
    sub_var = tk.StringVar(value="")
    value_lbl = tk.Label(
        inner, textvariable=value_var, bg=colors["bg_card"], fg=colors["fg"],
        font=fonts["value"], anchor=tk.W,
    )
    value_lbl.pack(fill=tk.X, pady=(3, 0))
    sub_lbl = tk.Label(
        inner, textvariable=sub_var, bg=colors["bg_card"], fg=colors["accent_green"],
        font=fonts["small"], anchor=tk.W,
    )
    sub_lbl.pack(fill=tk.X, pady=(1, 0))
    return {
        "frame": outer,
        "accent": accent,
        "icon": icon_var,
        "value": value_var,
        "sub": sub_var,
        "value_label": value_lbl,
        "sub_label": sub_lbl,
        "spark_canvas": spark_canvas,
    }


def sidebar_nav_btn(parent, colors, fonts, text, command, *, active=False, badge=None,
                    reduce_motion=False):
    """Кнопка пункта бокового меню."""
    bg = colors["nav_active"] if active else colors["bg_sidebar"]
    fg = colors["fg"] if active else colors["fg_dim"]
    row = tk.Frame(parent, bg=bg, cursor="hand2")
    row.pack(fill=tk.X, pady=2)

    indicator = tk.Frame(row, bg=colors["accent"] if active else bg, width=3)
    indicator.pack(side=tk.LEFT, fill=tk.Y)

    lbl = tk.Label(
        row, text=text, bg=bg, fg=fg, font=fonts["nav"],
        anchor=tk.W, padx=13, pady=10, cursor="hand2",
    )
    lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)

    badge_lbl = None
    if badge is not None:
        badge_lbl = tk.Label(
            row, text=str(badge), bg=colors["accent"], fg="#0d1117",
            font=fonts["badge"], padx=6, pady=1,
        )
        badge_lbl.pack(side=tk.RIGHT, padx=(0, 10))

    def _activate(_e=None):
        command()

    state = {"active": active, "hover": False, "job": None}

    def _blend(first, second, amount):
        channels = [round(int(first[i:i + 2], 16) * (1 - amount) + int(second[i:i + 2], 16) * amount)
                    for i in (1, 3, 5)]
        return "#" + "".join(f"{channel:02X}" for channel in channels)

    def _paint(background, foreground, stripe):
        row.configure(bg=background)
        lbl.configure(bg=background, fg=foreground)
        indicator.configure(bg=stripe)

    def _transition():
        if state["job"] is not None:
            row.after_cancel(state["job"])
            state["job"] = None
        target_bg = colors["nav_active"] if state["active"] else (
            colors["bg_elevated"] if state["hover"] else colors["bg_sidebar"]
        )
        target_fg = colors["fg"] if state["active"] or state["hover"] else colors["fg_dim"]
        target_stripe = colors["accent"] if state["active"] else target_bg
        if reduce_motion or not row.winfo_viewable():
            _paint(target_bg, target_fg, target_stripe)
            return
        start_bg, start_fg, start_stripe = row.cget("bg"), lbl.cget("fg"), indicator.cget("bg")

        def frame(step):
            amount = 1 - (1 - step / 6) ** 2
            _paint(_blend(start_bg, target_bg, amount),
                   _blend(start_fg, target_fg, amount),
                   _blend(start_stripe, target_stripe, amount))
            state["job"] = row.after(20, frame, step + 1) if step < 6 else None

        frame(1)

    def set_active(value):
        state["active"] = bool(value)
        _transition()

    def stop():
        if state["job"] is not None:
            row.after_cancel(state["job"])
            state["job"] = None

    def _hover(_e=None):
        state["hover"] = True
        _transition()

    def _leave(_e=None):
        state["hover"] = False
        _transition()

    for w in (row, lbl):
        w.bind("<Button-1>", _activate)
        w.bind("<Enter>", _hover)
        w.bind("<Leave>", _leave)
    if badge_lbl:
        badge_lbl.bind("<Button-1>", _activate)
        badge_lbl.bind("<Enter>", _hover)
        badge_lbl.bind("<Leave>", _leave)
    return {"row": row, "label": lbl, "badge": badge_lbl,
            "set_active": set_active, "stop": stop}


def detail_row(parent, colors, fonts, label_var, value_var):
    """Строка «метка — значение» в панели деталей."""
    row = tk.Frame(parent, bg=colors["bg_panel"], pady=3)
    row.pack(fill=tk.X)
    tk.Label(row, textvariable=label_var, bg=colors["bg_panel"], fg=colors["fg_dim"], font=fonts["small"], anchor=tk.W, width=14).pack(side=tk.LEFT)
    tk.Label(row, textvariable=value_var, bg=colors["bg_panel"], fg=colors["fg"], font=fonts["ui"], anchor=tk.E).pack(side=tk.RIGHT, fill=tk.X, expand=True)
    return row
