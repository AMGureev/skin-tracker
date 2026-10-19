"""Compact, responsive detail cards for skins and portfolio assets."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk


SKIN_PREVIEW_SIZE = (132, 132)
ASSET_PREVIEW_SIZE = (132, 132)


def _metric_cell(parent, owner, label, value_var, row, column, *, span=1):
    c = owner._c
    cell = tk.Frame(parent, bg=c["bg_panel"], padx=1, pady=1)
    cell.grid(
        row=row, column=column, columnspan=span, sticky="ew",
        padx=(0, 10 if column == 0 and span == 1 else 0),
    )
    tk.Label(
        cell, text=label, bg=c["bg_panel"], fg=c["fg_dim"],
        font=owner._font_small, anchor=tk.W,
    ).pack(side=tk.LEFT)
    tk.Label(
        cell, textvariable=value_var, bg=c["bg_panel"], fg=c["fg"],
        font=owner._font_small, anchor=tk.E, justify=tk.RIGHT,
    ).pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(5, 0))


def _build_header(inner, owner, *, title_var, subtitle_var, image_attr, box_attr, loading_prefix):
    c = owner._c
    header = tk.Frame(inner, bg=c["bg_panel"])
    header.grid(row=0, column=0, sticky="ew")
    tk.Label(
        header, text="Позиция" if loading_prefix == "skin" else "Актив",
        bg=c["bg_panel"], fg=c["fg_dim"], font=owner._font_small,
    ).pack(side=tk.LEFT)
    if loading_prefix == "skin":
        ttk.Button(header, text="График ↗", style="DetailGhost.TButton",
                   command=owner.open_selected_skin_chart).pack(side=tk.RIGHT)
    tk.Label(
        inner, textvariable=title_var, bg=c["bg_panel"], fg=c["fg"],
        font=("Segoe UI", 13, "bold"), wraplength=290, justify=tk.LEFT,
    ).grid(row=1, column=0, sticky="ew", pady=(3, 0))
    tk.Label(
        inner, textvariable=subtitle_var, bg=c["bg_panel"], fg=c["fg_dim"],
        font=owner._font_small,
    ).grid(row=2, column=0, sticky="w", pady=(2, 7))

    preview_size = SKIN_PREVIEW_SIZE if loading_prefix == "skin" else ASSET_PREVIEW_SIZE
    hero = tk.Frame(inner, bg=c["bg_panel"], height=preview_size[1])
    hero.grid(row=3, column=0, sticky="ew")
    hero.grid_propagate(False)
    hero.grid_rowconfigure(0, weight=1, minsize=preview_size[1])
    hero.grid_columnconfigure(1, weight=1)
    hero.grid_columnconfigure(0, minsize=preview_size[0])
    box = tk.Frame(
        hero, bg=c["entry_bg"], width=preview_size[0], height=preview_size[1],
        highlightbackground=c["border_soft"], highlightthickness=1,
    )
    box.grid(row=0, column=0, sticky="nw", padx=(0, 12))
    box.pack_propagate(False)
    image_label = tk.Label(
        box, text="Изображение загружается...", bg=c["entry_bg"], fg=c["fg_dim"],
        compound=tk.CENTER, font=owner._font_small, wraplength=preview_size[0] - 10,
    )
    image_label.pack(fill=tk.BOTH, expand=True)
    setattr(owner, box_attr, box)
    setattr(owner, image_attr, image_label)
    inner.grid_rowconfigure(3, minsize=preview_size[1])

    summary = tk.Frame(hero, bg=c["bg_panel"], width=140, height=preview_size[1])
    summary.grid(row=0, column=1, sticky="nsew")
    summary.pack_propagate(False)
    return hero, summary


def _build_skin_panel(owner, inner):
    c = owner._c
    owner._skin_preview_size = SKIN_PREVIEW_SIZE
    owner._skin_detail_title = tk.StringVar(value="Выберите позицию")
    owner._skin_detail_sub = tk.StringVar(value="")
    owner._skin_detail_profit = tk.StringVar(value="")
    _, summary = _build_header(
        inner, owner,
        title_var=owner._skin_detail_title,
        subtitle_var=owner._skin_detail_sub,
        image_attr="_skin_image_label",
        box_attr="_skin_image_box",
        loading_prefix="skin",
    )
    tk.Label(
        summary, text="Результат", bg=c["bg_panel"], fg=c["fg_dim"],
        font=owner._font_small,
    ).pack(anchor=tk.W)
    owner._skin_detail_profit_label = tk.Label(
        summary, textvariable=owner._skin_detail_profit, bg=c["bg_panel"],
        fg=c["fg_dim"], font=("Segoe UI", 13, "bold"),
        wraplength=130, justify=tk.LEFT,
    )
    owner._skin_detail_profit_label.pack(anchor=tk.W, pady=(4, 0))

    owner._skin_loading_label = tk.Label(
        summary, text="", bg=c["bg_panel"], fg=c["fg_dim"], font=owner._font_small,
    )
    owner._skin_loading_bar = ttk.Progressbar(summary, mode="indeterminate", length=120)
    owner._skin_loading_widgets = (owner._skin_loading_label, owner._skin_loading_bar)

    fields = tk.Frame(inner, bg=c["bg_panel"])
    fields.grid(row=4, column=0, sticky="ew", pady=(8, 4))
    fields.grid_columnconfigure(0, weight=1, uniform="detail")
    fields.grid_columnconfigure(1, weight=1, uniform="detail")
    owner._skin_detail_fields = {}
    labels = ("Покупка", "Текущая", "ROI", "Количество", "Всего", "Разблокировка", "Статус")
    for label in labels:
        owner._skin_detail_fields[label] = tk.StringVar(value="—")
    placements = (
        ("Покупка", 0, 0, 1), ("Текущая", 0, 1, 1),
        ("ROI", 1, 0, 1), ("Количество", 1, 1, 1),
        ("Всего", 2, 0, 1), ("Разблокировка", 2, 1, 1),
        ("Статус", 3, 0, 2),
    )
    for label, row, column, span in placements:
        _metric_cell(fields, owner, label, owner._skin_detail_fields[label], row, column, span=span)

    actions = tk.Frame(inner, bg=c["bg_panel"])
    actions.grid(row=5, column=0, sticky="ew", pady=(5, 7))
    for column in range(3):
        actions.grid_columnconfigure(column, weight=1, uniform="actions")
    owner._skin_sell_button = ttk.Button(actions, text="💸  Продать", style="DetailSuccess.TButton", command=owner.open_sell_window)
    owner._skin_edit_button = ttk.Button(actions, text="✎ Изменить", style="DetailPrimary.TButton", command=owner.open_edit_window)
    owner._skin_market_button = ttk.Button(actions, text="↗ Маркет", style="DetailGhost.TButton", command=owner.open_cs_market_list_window)
    owner._skin_lis_button = ttk.Button(actions, text="↗ Lis", style="DetailGhost.TButton", command=owner.open_lis_skin_page)
    owner._skin_detail_action_buttons = {
        "sell": owner._skin_sell_button,
        "edit": owner._skin_edit_button,
        "market": owner._skin_market_button,
        "lis": owner._skin_lis_button,
    }
    owner._skin_actions_frame = actions
    owner._update_skin_detail_actions("—")

    owner._skin_chart_frame = tk.Frame(inner, bg=c["bg_panel"])
    owner._skin_chart_frame.grid(row=6, column=0, sticky="ew", pady=(3, 0))
    inner.grid_rowconfigure(6, weight=0)


def _build_asset_panel(owner, inner):
    c = owner._c
    owner._asset_preview_size = ASSET_PREVIEW_SIZE
    owner._asset_detail_title = tk.StringVar(value="Выберите актив")
    owner._asset_detail_sub = tk.StringVar(value="")
    owner._asset_detail_profit = tk.StringVar(value="")
    _, summary = _build_header(
        inner, owner,
        title_var=owner._asset_detail_title,
        subtitle_var=owner._asset_detail_sub,
        image_attr="_asset_image_label",
        box_attr="_asset_image_box",
        loading_prefix="asset",
    )
    tk.Label(
        summary, text="Изменение", bg=c["bg_panel"], fg=c["fg_dim"], font=owner._font_small,
    ).pack(anchor=tk.W)
    owner._asset_detail_profit_label = tk.Label(
        summary, textvariable=owner._asset_detail_profit, bg=c["bg_panel"],
        fg=c["fg_dim"], font=("Segoe UI", 13, "bold"), wraplength=130, justify=tk.LEFT,
    )
    owner._asset_detail_profit_label.pack(anchor=tk.W, pady=(4, 0))
    owner._asset_loading_label = tk.Label(summary, text="", bg=c["bg_panel"], fg=c["fg_dim"], font=owner._font_small)
    owner._asset_loading_bar = ttk.Progressbar(summary, mode="indeterminate", length=120)
    owner._asset_loading_widgets = (owner._asset_loading_label, owner._asset_loading_bar)

    fields = tk.Frame(inner, bg=c["bg_panel"])
    fields.grid(row=4, column=0, sticky="ew", pady=(8, 5))
    fields.grid_columnconfigure(0, weight=1, uniform="detail")
    fields.grid_columnconfigure(1, weight=1, uniform="detail")
    owner._asset_detail_fields = {}
    labels = ("Количество", "Покупка ₽", "Текущая ₽", "Сумма ₽", "Цель", "Обновлено")
    for label in labels:
        owner._asset_detail_fields[label] = tk.StringVar(value="—")
    for index, label in enumerate(labels):
        _metric_cell(fields, owner, label, owner._asset_detail_fields[label], index // 2, index % 2)

    actions = tk.Frame(inner, bg=c["bg_panel"])
    actions.grid(row=5, column=0, sticky="ew", pady=(5, 0))
    actions.grid_columnconfigure(0, weight=1, uniform="asset_actions")
    actions.grid_columnconfigure(1, weight=1, uniform="asset_actions")
    ttk.Button(actions, text="✎ Изменить", style="DetailPrimary.TButton", command=owner.open_edit_asset_window).grid(row=0, column=0, sticky="ew", padx=(0, 4))
    ttk.Button(actions, text="🎯 Цель", style="DetailGhost.TButton", command=owner.open_set_asset_target_window).grid(row=0, column=1, sticky="ew", padx=(4, 0))
    owner._asset_chart_frame = tk.Frame(inner, bg=c["bg_panel"])


def build_detail_panel(owner, parent, *, mode):
    c = owner._c
    panel = tk.Frame(parent, bg=c["bg"], width=330)
    panel.pack(side=tk.RIGHT, fill=tk.Y)
    panel.pack_propagate(False)
    inner = tk.Frame(
        panel, bg=c["bg_panel"], padx=14, pady=12,
        highlightbackground=c["border_soft"], highlightthickness=1,
    )
    inner.pack(fill=tk.BOTH, expand=True, padx=(8, 0))
    inner.grid_columnconfigure(0, weight=1)
    if mode == "skins":
        _build_skin_panel(owner, inner)
    else:
        _build_asset_panel(owner, inner)
    return panel
