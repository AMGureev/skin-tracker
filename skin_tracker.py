import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime, timedelta
import sqlite3
import math
import requests
import json
import threading
import re
from collections import defaultdict
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.dates as mdates
import numpy as np
import urllib.parse
import xml.etree.ElementTree as ET
import os
import sys
import webbrowser
import time
import logging
from bisect import bisect_right

from cstracker.background import BackgroundTasks
from cstracker.services.http import WorkerHttpClient
from cstracker.ui.price_chart import ItemPriceChart
from cstracker.ui.market_actions import MarketActions

from cstracker.categorization import classify_cs_item_category
from cstracker.config import (
    API_KEY,
    CATEGORY_FILTER_LABELS,
    COEFFICIENT_BUY_ORDER,
    COEFFICIENT_LIS,
    LIS_SKINS_API_BASE,
    LIS_SKINS_API_KEY,
    LIS_SKINS_MARKET_BASE,
    MARKET_COMMISSION_RATE,
)
from cstracker.currency import (
    fetch_usd_rub_rate as fetch_usd_rub_rate_external,
    parse_optional_usd_rate,
    parse_positive_float,
)
from cstracker.database import (
    create_database as create_database_schema,
    migrate_database_if_needed as migrate_database_schema,
)
from cstracker.dashboard_ui import metric_card, sidebar_nav_btn
from cstracker.services import (
    ImageRepository,
    LisSkinsClient,
    asset_image_url,
    steam_item_image_url,
)
from cstracker.sales import calculate_skin_sale, record_skin_sale
from cstracker.ui import (
    ChartInteractionController,
    add_navigation_toolbar,
    build_detail_panel,
    render_preview_image,
)
from cstracker.ui.theme import COLORS

MARKET_API_BASE = "https://market.csgo.com/api/v2"

_SKINS_TABLE_COLUMNS = (
    ("id", "ID", 40),
    ("name", "Название скина", 220),
    ("quantity", "Кол-во", 60),
    ("price", "Цена за шт ₽", 80),
    ("total", "Всего ₽", 90),
    ("buy_date", "Дата покупки", 100),
    ("unlock_date", "Разблокировка", 100),
    ("sell_date", "Дата продажи", 100),
    ("sell_price", "Цена продажи", 90),
    ("profit", "Профит", 100),
    ("status", "Статус", 120),
    ("order_buy", "Order Buy", 180),
    ("order_sell", "Order Sell", 180),
    ("growth_pct", "Рост %", 90),
    ("target_sell", "Цель ₽", 135),
    ("buy_usd", "Покупка $", 100),
    ("rate_usd", "Курс ₽/$", 78),
    ("history", "History", 90),
)

_ASSETS_TABLE_COLUMNS = (
    ("id", "ID", 40),
    ("group", "Тип", 90),
    ("code", "Код", 90),
    ("name", "Название", 220),
    ("amount", "Количество", 100),
    ("unit", "Ед.", 80),
    ("buy_usd", "Покупка $", 100),
    ("buy_rub", "Покупка ₽", 100),
    ("cur_usd", "Текущая $", 100),
    ("cur_rub", "Текущая ₽", 100),
    ("value_usd", "Текущая сумма $", 130),
    ("value_rub", "Текущая сумма ₽", 130),
    ("growth_pct", "Рост %", 90),
    ("target_sell", "Цель ₽", 135),
    ("updated", "Обновлено", 145),
)

_DEFAULT_VISIBLE_COLUMNS = {
    "skins": (
        "name", "quantity", "price", "total", "unlock_date", "status",
        "order_sell", "growth_pct", "target_sell", "history",
    ),
    "assets": (
        "name", "code", "amount", "buy_rub", "cur_rub", "value_rub",
        "growth_pct", "target_sell", "updated",
    ),
}

_KPI_ICONS = {
    "Затраты на инвентарь": "◈",
    "Текущая стоимость": "↗",
    "Ожидаемый профит": "◇",
    "Зафиксировано": "✓",
    "Доступно к продаже": "◎",
    "Ждёт разблокировки": "◷",
    "Вложено": "◈",
    "Текущая оценка": "↗",
    "Прибыль / убыток": "◇",
    "Позиций": "◎",
    "Обновление": "◷",
}


class SkinTracker:
    ASSET_CURRENCY_OPTIONS = [
        ("USD", "Доллар (USD)"),
        ("EUR", "Евро (EUR)"),
        ("CNY", "Юань (CNY)"),
    ]
    ASSET_METAL_OPTIONS = [
        ("XAU", "Золото (XAU)"),
        ("XAG", "Серебро (XAG)"),
        ("XPD", "Палладий (XPD)"),
        ("XPT", "Платина (XPT)"),
    ]

    def __init__(self, root, *, db_path=None, auto_start=True):
        self.root = root
        self._db_path_override = db_path
        self._closed = False
        self._reduce_motion = os.environ.get("SKIN_TRACKER_REDUCED_MOTION") == "1"
        self._tasks = BackgroundTasks(root, on_busy=self._show_background_activity)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.title("CS Skin Tracker")
        self.root.geometry("1600x920")
        self.root.minsize(1140, 720)
        self._setup_theme()
        self._chart_interactions = ChartInteractionController(self._c)
        self.current_profile = None
        self._usd_rub_rate_cache = None
        self._usd_rub_rate_cache_src = None
        self._http = WorkerHttpClient()
        self._rate_waiters = []
        self._rate_running = False
        self._rate_checked_at = 0.0
        self._series_cache = {}
        self._metrics_signature = None
        self._skin_rows_cache_key = None
        self._skin_rows_cache = ()
        self.sales_history = {}
        self._images = ImageRepository(session=self._http)
        self._lis = LisSkinsClient(
            LIS_SKINS_API_BASE,
            self._get_lis_api_key,
            LIS_SKINS_MARKET_BASE,
            session=self._http,
        )
        self._market_prices_lock = threading.Lock()
        self._market_prices_running = False
        self._tk_image_refs = {}
        self._skin_detail_token = 0
        self._skin_detail_loading_job = None
        self._popout_chart = None
        self._popout_window = None
        self._table_sort_state = {
            "skins": {"column": None, "reverse": False},
            "assets": {"column": None, "reverse": False},
        }
        self._table_hover_items = {}
        self._skin_row_names = {}
        self.SETTINGS_KEY_AUTO_SKINS = "auto_fetch_skins_on_entry"
        self.SETTINGS_KEY_AUTO_ASSETS = "auto_fetch_assets_on_entry"
        self.SETTINGS_KEY_MARKET_API = "market_csgo_api_key"
        self.SETTINGS_KEY_PRICE_ALERTS = "price_target_alerts_enabled"
        self.SETTINGS_KEY_PRICE_ALERTS_STATE = "price_target_alerts_state"
        # Создание базы данных
        self.create_database()
        # Мигрируем данные, если это необходимо
        self.migrate_database_if_needed()
        self._usd_rub_rate_cache = parse_positive_float(self._get_app_setting("cached_usd_rub_rate", ""))
        self._usd_rub_rate_cache_src = "сохранённый курс"
        # Создание интерфейса
        self.create_widgets()
        self.load_data()
        self.load_assets_data()
        if auto_start:
            self.show_profile_selector()

    def _show_background_activity(self, count):
        bar = getattr(self, "_background_progress", None)
        if bar is None or self._closed:
            return
        if count:
            if not bar.winfo_manager():
                bar.pack(side=tk.RIGHT, padx=8)
                bar.start(70)
        else:
            bar.stop()
            bar.pack_forget()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._tasks.close()
        for group in (getattr(self, "_sidebar_profile_btns", {}),
                      getattr(self, "_sidebar_nav_btns", {})):
            for button in group.values():
                button["stop"]()
        for group in (getattr(self, "_skins_kpi_cards", ()),
                      getattr(self, "_assets_kpi_cards", ())):
            for card in group:
                canvas = card.get("spark_canvas")
                job = getattr(canvas, "_tracker_animation_job", None)
                if job:
                    canvas.after_cancel(job)
        for name in ("_background_progress", "_skin_loading_bar", "_asset_loading_bar"):
            bar = getattr(self, name, None)
            if bar is not None:
                bar.stop()
        for name in ("_skin_chart_frame", "_asset_chart_frame"):
            frame = getattr(self, name, None)
            if frame is not None:
                self._clear_detail_chart(frame)
        if self._popout_chart is not None:
            self._popout_chart.close()
            self._popout_chart = None
        self.conn.close()
        self.root.destroy()

    @staticmethod
    def _app_base_dir():
        """Папка .py при разработке, папка .exe при сборке PyInstaller."""
        if getattr(sys, "frozen", False):
            return os.path.dirname(os.path.abspath(sys.executable))
        return os.path.dirname(os.path.abspath(__file__))

    @staticmethod
    def _app_db_path():
        """skins.db рядом с приложением — не зависит от текущей папки запуска."""
        return os.path.join(SkinTracker._app_base_dir(), "skins.db")

    def create_database(self):
        self._db_file = self._db_path_override or self._app_db_path()
        self.conn = sqlite3.connect(self._db_file)
        create_database_schema(self.conn)

    def migrate_database_if_needed(self):
        """Мигрирует данные из текстового формата в формат DATE, если это необходимо"""
        migrate_database_schema(self.conn)
        self._ensure_market_columns()
        self._ensure_item_price_history_table()

    def _ensure_market_columns(self):
        """Колонки для связи строки учёта с лотом на market.csgo.com (витрина/продажа)."""
        cur = self.conn.cursor()
        cur.execute("PRAGMA table_info(skins)")
        col_names = {row[1] for row in cur.fetchall()}
        changed = False
        if "market_asset_id" not in col_names:
            cur.execute("ALTER TABLE skins ADD COLUMN market_asset_id TEXT")
            changed = True
        if "market_item_id" not in col_names:
            cur.execute("ALTER TABLE skins ADD COLUMN market_item_id TEXT")
            changed = True
        if changed:
            self.conn.commit()

    def _get_app_setting(self, key, default="1"):
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT value FROM app_settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row[0] if row is not None else default
        except sqlite3.Error:
            return default

    def _set_app_setting(self, key, value):
        """Запись настройки в skins.db (app_settings)."""
        cursor = self.conn.cursor()
        cursor.execute(
            'INSERT OR REPLACE INTO app_settings ("key", value) VALUES (?, ?)',
            (key, value if value is not None else ""),
        )
        self.conn.commit()

    def _save_settings_snapshot(self, *, auto_skins, auto_assets, price_alerts, api_key_text, show_message=True, parent=None):
        """Сохраняет все настройки из окна «Настройки» одним слепком в базу."""
        api_key_text = (api_key_text or "").strip()
        self._set_app_setting(self.SETTINGS_KEY_AUTO_SKINS, "1" if auto_skins else "0")
        self._set_app_setting(self.SETTINGS_KEY_AUTO_ASSETS, "1" if auto_assets else "0")
        self._set_app_setting(
            self.SETTINGS_KEY_PRICE_ALERTS, "1" if price_alerts else "0"
        )
        self._set_app_setting(self.SETTINGS_KEY_MARKET_API, api_key_text)
        saved_key = (self._get_app_setting(self.SETTINGS_KEY_MARKET_API, "") or "").strip()
        if saved_key != api_key_text:
            raise RuntimeError("Не удалось записать API-ключ в базу данных.")
        if show_message:
            db_name = os.path.basename(getattr(self, "_db_file", "skins.db"))
            key_note = "ключ записан" if api_key_text else "ключ очищён (будут env/config)"
            messagebox.showinfo(
                "Настройки сохранены",
                f"Изменения записаны в {db_name}:\n"
                f"• автообновление скинов и активов\n"
                f"• оповещения о целевой цене\n"
                f"• API market.csgo.com — {key_note}",
                parent=parent,
            )
        return True

    def _get_market_api_key(self):
        """Ключ API market.csgo.com: настройки приложения → CSMARKET_API_KEY → cstracker.config."""
        v = (self._get_app_setting(self.SETTINGS_KEY_MARKET_API, "") or "").strip()
        if v:
            return v
        env = (os.environ.get("CSMARKET_API_KEY") or "").strip()
        if env:
            return env
        return (API_KEY or "").strip()

    def _settings_btn_kw(self, *, primary=False):
        if primary:
            return dict(
                bg=self._c["accent"], fg="#0d1117", activebackground="#5eead4",
                activeforeground="#0d1117", font=self._font_ui_bold, relief=tk.FLAT,
                padx=18, pady=8, cursor="hand2", bd=0,
            )
        return dict(
            bg=self._c["bg_elevated"], fg=self._c["fg"], activebackground=self._c["border"],
            activeforeground=self._c["fg"], font=self._font_ui_bold, relief=tk.FLAT,
            padx=18, pady=8, cursor="hand2", bd=0,
        )

    def open_settings(self):
        win = tk.Toplevel(self.root)
        win.title("Настройки")
        self._style_toplevel(win)
        win.resizable(True, True)
        win.transient(self.root)
        try:
            win.grab_set()
        except tk.TclError:
            pass

        pad = ttk.Frame(win, style="App.TFrame", padding=18)

        # Сначала два чекбокса автообновления (как в ранней версии) — не прячем под блоком API.
        box = ttk.LabelFrame(
            pad,
            text="  Автообновление при входе в профиль  ",
            style="Card.TLabelframe",
            padding=(14, 10),
        )
        box.pack(fill=tk.X, pady=(0, 12))

        v_skins = tk.IntVar(
            value=1 if self._get_app_setting(self.SETTINGS_KEY_AUTO_SKINS) == "1" else 0
        )
        v_assets = tk.IntVar(
            value=1 if self._get_app_setting(self.SETTINGS_KEY_AUTO_ASSETS) == "1" else 0
        )

        chk_kw = {**self._chk_kw(), "bg": self._c["bg_card"]}

        tk.Checkbutton(
            box,
            text="Автоматически загружать цены с маркета при входе в профиль «Скины»",
            variable=v_skins,
            **chk_kw,
        ).pack(anchor=tk.W, pady=(0, 8))

        tk.Label(
            box,
            text="Если выключено — данные с маркета обновляются только по кнопке «Маркет» → «Цены с маркета».",
            **self._label_kw(card=True),
        ).pack(anchor=tk.W, pady=(0, 14))

        tk.Checkbutton(
            box,
            text="Автоматически обновлять текущие цены по API при входе в профиль «Активы»",
            variable=v_assets,
            **chk_kw,
        ).pack(anchor=tk.W, pady=(0, 8))

        tk.Label(
            box,
            text="Если выключено — курсы и котировки подгружаются вручную кнопкой «Обновить цены по API».",
            **self._label_kw(card=True),
        ).pack(anchor=tk.W)

        alert_box = ttk.LabelFrame(
            pad,
            text="  Оповещения о целевой цене  ",
            style="Card.TLabelframe",
            padding=(14, 10),
        )
        alert_box.pack(fill=tk.X, pady=(0, 12))
        v_alerts = tk.IntVar(
            value=1 if self._get_app_setting(self.SETTINGS_KEY_PRICE_ALERTS, "1") == "1" else 0
        )

        tk.Checkbutton(
            alert_box,
            text="Уведомлять, когда Order Buy/Sell (−10%) достигли желаемой цены",
            variable=v_alerts,
            **chk_kw,
        ).pack(anchor=tk.W, pady=(0, 8))
        tk.Label(
            alert_box,
            text="Сравнение с желаемой ценой — по значению после комиссии 10% (как в колонках Order Buy/Sell). "
                 "Повторно не оповещает, пока цена снова не упадёт ниже цели.",
            **self._label_kw(card=True),
            wraplength=480,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(0, 10))

        cols_row = tk.Frame(alert_box, bg=self._c["bg_card"])
        cols_row.pack(anchor=tk.W)
        ttk.Button(
            cols_row,
            text="Колонки «Скины»…",
            style="Primary.TButton",
            command=lambda: self.open_table_columns_window("skins"),
        ).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(
            cols_row,
            text="Колонки «Активы»…",
            style="Primary.TButton",
            command=lambda: self.open_table_columns_window("assets"),
        ).pack(side=tk.LEFT)

        api_box = ttk.LabelFrame(
            pad,
            text="  API-ключ market.csgo.com  ",
            style="Card.TLabelframe",
            padding=(14, 10),
        )
        api_box.pack(fill=tk.X, pady=(0, 12))
        tk.Label(
            api_box,
            text="Ключ с сайта market.csgo.com (раздел API). Нужен для истории продаж, витрины и торговых методов.",
            **self._label_kw(card=True),
            wraplength=480,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(0, 8))
        api_key_var = tk.StringVar(value=self._get_app_setting(self.SETTINGS_KEY_MARKET_API, ""))
        api_entry = tk.Entry(api_box, textvariable=api_key_var, width=52, show="•", **self._entry_kw())
        api_entry.pack(anchor=tk.W, fill=tk.X, pady=(0, 6))

        def _fallback_hint():
            saved = (self._get_app_setting(self.SETTINGS_KEY_MARKET_API, "") or "").strip()
            if saved:
                return ""
            if (os.environ.get("CSMARKET_API_KEY") or "").strip():
                return "Сейчас ключ не в базе — используется CSMARKET_API_KEY."
            fk = (API_KEY or "").strip()
            if fk and fk != "YOUR_API_KEY_HERE":
                return "Сейчас ключ не в базе — используется cstracker/config.py."
            return ""

        _fh = _fallback_hint()
        if _fh:
            fh_kw = self._label_kw(card=True)
            fh_kw["fg"] = self._c["accent_muted"]
            tk.Label(api_box, text=_fh, **fh_kw).pack(anchor=tk.W, pady=(0, 4))

        api_status_var = tk.StringVar(value="")
        status_kw = self._label_kw(card=True)
        status_kw["fg"] = self._c["info"]
        tk.Label(api_box, textvariable=api_status_var, **status_kw).pack(anchor=tk.W, pady=(0, 4))

        api_btn_row = tk.Frame(api_box, bg=self._c["bg_card"])
        api_btn_row.pack(anchor=tk.W, pady=(4, 0))
        ttk.Button(
            api_btn_row,
            text="Показать / скрыть",
            style="Primary.TButton",
            command=lambda: api_entry.config(show="" if api_entry.cget("show") else "•"),
        ).pack(side=tk.LEFT)

        hint_kw = self._label_kw(card=True)
        hint_kw["fg"] = self._c["fg_dim"]
        tk.Label(
            api_box,
            text="Пустое поле API: снова используются переменная CSMARKET_API_KEY или config.py. "
                 "Чтобы применить изменения, нажмите «Сохранить» внизу окна.",
            wraplength=480,
            justify=tk.LEFT,
            **hint_kw,
        ).pack(anchor=tk.W, pady=(8, 0))

        def _settings_snapshot():
            win.update_idletasks()
            api_raw = (api_entry.get() or api_key_var.get() or "").strip()
            try:
                self._save_settings_snapshot(
                    auto_skins=bool(v_skins.get()),
                    auto_assets=bool(v_assets.get()),
                    price_alerts=bool(v_alerts.get()),
                    api_key_text=api_raw,
                    show_message=True,
                    parent=win,
                )
                api_status_var.set(
                    f"Сохранено {datetime.now().strftime('%H:%M:%S')} → "
                    f"{os.path.basename(getattr(self, '_db_file', 'skins.db'))}"
                )
            except Exception as exc:
                messagebox.showerror(
                    "Ошибка сохранения",
                    f"Не удалось записать настройки:\n{exc}",
                    parent=win,
                )

        def _settings_dirty():
            api_raw = (api_entry.get() or api_key_var.get() or "").strip()
            saved_api = (self._get_app_setting(self.SETTINGS_KEY_MARKET_API, "") or "").strip()
            if api_raw != saved_api:
                return True
            if ("1" if v_skins.get() else "0") != self._get_app_setting(self.SETTINGS_KEY_AUTO_SKINS):
                return True
            if ("1" if v_assets.get() else "0") != self._get_app_setting(self.SETTINGS_KEY_AUTO_ASSETS):
                return True
            if ("1" if v_alerts.get() else "0") != self._get_app_setting(self.SETTINGS_KEY_PRICE_ALERTS, "1"):
                return True
            return False

        def _close_settings():
            if _settings_dirty():
                ans = messagebox.askyesnocancel(
                    "Несохранённые изменения",
                    "Сохранить настройки перед закрытием?",
                    parent=win,
                )
                if ans is None:
                    return
                if ans:
                    _settings_snapshot()
            try:
                win.grab_release()
            except tk.TclError:
                pass
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", _close_settings)

        footer = tk.Frame(win, bg=self._c["bg"], padx=18, pady=14)
        tk.Button(footer, text="Сохранить", command=_settings_snapshot, **self._settings_btn_kw(primary=True)).pack(
            side=tk.LEFT
        )
        tk.Button(footer, text="Закрыть", command=_close_settings, **self._settings_btn_kw()).pack(
            side=tk.RIGHT
        )
        sep = tk.Frame(win, bg=self._c["border"], height=1)
        footer.pack(side=tk.BOTTOM, fill=tk.X)
        sep.pack(side=tk.BOTTOM, fill=tk.X)
        pad.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        win.update_idletasks()
        win.geometry("540x780")
        win.minsize(500, 560)
        footer.lift()

    def fetch_usd_rub_rate(self):
        """Worker-only network call. UI calculations use the last known rate."""
        if threading.current_thread() is threading.main_thread():
            if self._usd_rub_rate_cache:
                return self._usd_rub_rate_cache, self._usd_rub_rate_cache_src
            raise ValueError("Курс ещё загружается. Укажите курс вручную или нажмите «Курс API».")
        return fetch_usd_rub_rate_external(self._http)

    def _request_usd_rate(self, success=None, error=None, *, force=False):
        if success or error:
            self._rate_waiters.append((success, error))
        if self._rate_running:
            return
        age = time.monotonic() - self._rate_checked_at
        if not force and self._rate_checked_at > 0 and age < (600 if self._usd_rub_rate_cache else 60):
            self._deliver_rate_waiters()
            return

        def loaded(result):
            self._rate_running = False
            rate, source = result
            self._usd_rub_rate_cache = rate
            self._usd_rub_rate_cache_src = source
            self._rate_checked_at = time.monotonic()
            self._set_app_setting("cached_usd_rub_rate", str(rate))
            self._rate_label_var.set(f"{rate:.2f} ₽")
            self._deliver_rate_waiters()
            self._compute_and_show_skins_kpis()

        def failed(exc):
            self._rate_running = False
            self._rate_checked_at = time.monotonic()
            self._rate_label_var.set(f"{self._usd_rub_rate_cache:.2f} ₽" if self._usd_rub_rate_cache else "— (нет сети)")
            self._deliver_rate_waiters(exc)

        self._rate_running = self._tasks.submit("exchange-rate", self.fetch_usd_rub_rate, loaded, failed)

    def _deliver_rate_waiters(self, error=None):
        waiters, self._rate_waiters = self._rate_waiters, []
        for success, failed in waiters:
            if error is not None or not self._usd_rub_rate_cache:
                if failed:
                    failed(error or ValueError("Курс недоступен. Укажите его вручную."))
            elif success:
                success((self._usd_rub_rate_cache, self._usd_rub_rate_cache_src))

    @staticmethod
    def _parse_optional_usd_rate(raw):
        return parse_optional_usd_rate(raw)

    @staticmethod
    def _parse_positive_float(raw):
        return parse_positive_float(raw)

    def _parse_optional_positive_float(self, raw):
        raw = (raw or "").strip()
        if not raw:
            return None
        return self._parse_positive_float(raw)

    @staticmethod
    def _market_net_price(gross_price):
        """Цена с маркета за вычетом комиссии 10% (как «−10%» в Order Buy / Order Sell)."""
        try:
            p = float(gross_price or 0)
        except (TypeError, ValueError):
            return 0.0
        return p * COEFFICIENT_BUY_ORDER if p > 0 else 0.0

    @staticmethod
    def _fmt_rub_kpi(amount):
        return f"{float(amount):,.2f} ₽".replace(",", " ")

    @staticmethod
    def _fmt_pct_kpi(pct):
        return f"+{pct:.2f}%" if pct >= 0 else f"{pct:.2f}%"

    @staticmethod
    def _fmt_short_unlock_date(iso_date):
        if not iso_date:
            return ""
        months = ("янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")
        try:
            d = datetime.strptime(str(iso_date)[:10], "%Y-%m-%d")
            return f"{d.day} {months[d.month - 1]}"
        except ValueError:
            return str(iso_date)

    def _sum_market_net_rub(self, rows, price_map):
        """Сумма net-цены (−10%) по списку (name, quantity) и словарю gross-цен маркета."""
        total = 0.0
        priced = 0
        for name, qty in rows:
            try:
                q = int(qty or 0)
            except (TypeError, ValueError):
                q = 0
            if q <= 0:
                continue
            gross = float((price_map or {}).get(str(name), 0) or 0)
            if gross <= 0:
                continue
            total += self._market_net_price(gross) * q
            priced += 1
        return total, priced

    def _current_usd_rate(self):
        return self._usd_rub_rate_cache

    @staticmethod
    def _fmt_usd_kpi(amount, *, signed=False):
        if amount is None:
            return "курс $ недоступен"
        fmt = "{:+,.2f} $" if signed else "{:,.2f} $"
        return fmt.format(float(amount)).replace(",", " ")

    def _inventory_cost_usd(self, where_sql, params, usd_rate):
        cursor = self.conn.cursor()
        cursor.execute(
            f"""
            SELECT total_buy_price, total_buy_price_usd
            FROM skins
            WHERE {where_sql}
            """,
            params,
        )
        total_usd = 0.0
        fallback_count = 0
        missing_count = 0
        for total_rub, stored_usd in cursor.fetchall():
            try:
                usd = float(stored_usd) if stored_usd is not None else 0.0
            except (TypeError, ValueError):
                usd = 0.0
            if usd > 0:
                total_usd += usd
                continue
            try:
                rub = float(total_rub or 0.0)
            except (TypeError, ValueError):
                rub = 0.0
            if rub > 0 and usd_rate and usd_rate > 0:
                total_usd += rub / usd_rate
                fallback_count += 1
            elif rub > 0:
                missing_count += 1
        return total_usd, fallback_count, missing_count

    def _sale_usd_rate_to_save(self, exchange_rate):
        try:
            rate = float(exchange_rate or 0)
        except (TypeError, ValueError):
            rate = 0.0
        if rate > 1:
            return rate
        return self._current_usd_rate()

    def _get_lis_api_key(self):
        return (os.environ.get("LIS_SKINS_API_KEY") or LIS_SKINS_API_KEY or "").strip()

    def _lis_skin_url(self, name):
        return self._lis.market_url(name)

    @staticmethod
    def _steam_item_image_url(item_class_id, size=360):
        return steam_item_image_url(item_class_id, size=size)

    @staticmethod
    def _asset_image_url(asset_group, code):
        return asset_image_url(asset_group, code)

    def _download_image_bytes(self, url, *, timeout=12):
        return self._images.download(url, timeout=timeout)

    def _set_preview_image(self, label, image_bytes, *, ref_key, size=None, placeholder="Изображение недоступно"):
        if size is None:
            if str(ref_key).startswith("skin"):
                size = getattr(self, "_skin_preview_size", (116, 82))
            elif str(ref_key).startswith("asset"):
                size = getattr(self, "_asset_preview_size", (116, 82))
            else:
                size = (116, 82)
        render_preview_image(
            label,
            image_bytes,
            refs=self._tk_image_refs,
            ref_key=ref_key,
            size=size,
            background=self._c["entry_bg"],
            placeholder=placeholder,
        )

    def _set_detail_loading(self, active, text="Загрузка..."):
        widgets = getattr(self, "_skin_loading_widgets", None)
        if not widgets:
            return
        label, bar = widgets
        if active:
            label.configure(text=f"⟳ {text}")
            if not label.winfo_ismapped():
                label.pack(anchor=tk.W, pady=(0, 4))
            if not bar.winfo_ismapped():
                bar.pack(fill=tk.X, pady=(0, 10))
            try:
                bar.start(70)
            except tk.TclError:
                pass
        else:
            try:
                bar.stop()
            except tk.TclError:
                pass
            label.pack_forget()
            bar.pack_forget()

    def _set_asset_loading(self, active, text="Загрузка..."):
        widgets = getattr(self, "_asset_loading_widgets", None)
        if not widgets:
            return
        label, bar = widgets
        if active:
            label.configure(text=f"⟳ {text}")
            if not label.winfo_ismapped():
                label.pack(anchor=tk.W, pady=(0, 4))
            if not bar.winfo_ismapped():
                bar.pack(fill=tk.X, pady=(0, 10))
            try:
                bar.start(70)
            except tk.TclError:
                pass
        else:
            try:
                bar.stop()
            except tk.TclError:
                pass
            label.pack_forget()
            bar.pack_forget()

    def _lis_search_items(self, skin_name, *, limit=60):
        return self._lis.search(skin_name, limit=limit)

    def _lis_lowest_price_usd(self, skin_name):
        return self._lis.lowest_price_usd(skin_name)

    def _lis_lowest_item_info(self, skin_name):
        return self._lis.lowest_item(skin_name)

    def _save_lis_item_snapshot(self, skin_id, skin_name, lis_info, usd_rate):
        if not lis_info or not lis_info.get("price_usd"):
            return
        try:
            skin_id = int(skin_id)
        except (TypeError, ValueError):
            return
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, name, quantity, buy_price, total_buy_price, unlock_date, sell_date
            FROM skins
            WHERE id = ?
            """,
            (skin_id,),
        )
        row = cursor.fetchone()
        if not row:
            return
        captured_at = datetime.now().isoformat(timespec="seconds")
        _id, name, qty, buy_price, total_buy, unlock_date, sell_date = row
        buy_gross = float((getattr(self, "buy_order_prices", None) or {}).get(str(name), 0) or 0)
        sell_gross = float((getattr(self, "sell_order_prices", None) or {}).get(str(name), 0) or 0)
        lis_usd = float(lis_info["price_usd"])
        lis_rub = lis_usd * usd_rate if usd_rate and usd_rate > 0 else None
        lis_fee_rub = lis_rub * COEFFICIENT_LIS if lis_rub else None
        cursor.execute(
            """
            INSERT INTO item_price_history (
                captured_at, skin_id, skin_name, quantity, buy_price_rub,
                total_buy_price_rub, buy_order_gross_rub, sell_order_gross_rub,
                buy_order_net_rub, sell_order_net_rub,
                lis_min_price_usd, lis_min_price_rub, lis_min_price_fee_rub,
                lis_item_class_id, skin_image_url, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                captured_at,
                skin_id,
                str(name or skin_name),
                int(qty or 0),
                float(buy_price or 0),
                float(total_buy or 0),
                buy_gross if buy_gross > 0 else None,
                sell_gross if sell_gross > 0 else None,
                self._market_net_price(buy_gross) if buy_gross > 0 else None,
                self._market_net_price(sell_gross) if sell_gross > 0 else None,
                lis_usd,
                lis_rub,
                lis_fee_rub,
                lis_info.get("item_class_id"),
                lis_info.get("image_url"),
                self._skin_status_from_db_dates(unlock_date, sell_date),
            ),
        )
        self.conn.commit()

    def open_lis_skin_page(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("Lis Skins", "Выберите предмет в таблице.")
            return
        if len(selected) != 1:
            messagebox.showinfo("Lis Skins", "Выберите ровно одну позицию.")
            return
        values = self.tree.item(selected[0], "values")
        name = self._selected_skin_original_name(values)
        if not name or name == "—":
            messagebox.showinfo("Lis Skins", "Не удалось определить название предмета.")
            return
        webbrowser.open(self._lis_skin_url(name))

    @staticmethod
    def _format_growth_pct(current_price, base_price):
        try:
            current = float(current_price or 0)
            base = float(base_price or 0)
        except (TypeError, ValueError):
            return "-"
        if current <= 0 or base <= 0:
            return "-"
        pct = (current - base) / base * 100.0
        sign = "+" if pct > 0 else ""
        return f"{sign}{pct:.1f}%"

    @staticmethod
    def _format_target_progress(target_price, current_price):
        try:
            target = float(target_price or 0)
        except (TypeError, ValueError):
            return "-"
        if target <= 0:
            return "-"
        try:
            current = float(current_price or 0)
        except (TypeError, ValueError):
            current = 0.0
        if current <= 0:
            return f"{target:.2f} (нет цены)"
        closeness = current / target * 100.0
        return f"{target:.2f} ({closeness:.1f}%)"

    @staticmethod
    def _extract_percent(text):
        match = re.search(r"[-+]?\d+(?:[.,]\d+)?\s*%", str(text or ""))
        if not match:
            return None
        try:
            return float(match.group(0).replace("%", "").replace(",", ".").strip())
        except ValueError:
            return None

    @staticmethod
    def _compact_skin_name(name):
        text = str(name or "").strip()
        if not text:
            return "—"
        wear_map = {
            "Factory New": "FN",
            "Minimal Wear": "MW",
            "Field-Tested": "FT",
            "Well-Worn": "WW",
            "Battle-Scarred": "BS",
        }
        wear = ""
        wear_match = re.search(r"\(([^)]+)\)\s*$", text)
        if wear_match:
            wear = wear_map.get(wear_match.group(1), wear_match.group(1))
            text = text[:wear_match.start()].strip()
        if " | " in text:
            weapon, skin = text.split(" | ", 1)
            line2 = skin if not wear else f"{skin} · {wear}"
            return f"{weapon}\n{line2}"
        return text if not wear else f"{text}\n{wear}"

    @staticmethod
    def _sparkline(values):
        vals = [float(v) for v in values if v is not None]
        if len(vals) < 2:
            return "▁▁▁▁▁▁"
        blocks = "▁▂▃▄▅▆▇█"
        lo, hi = min(vals), max(vals)
        if hi == lo:
            return blocks[3] * min(len(vals), 16)
        sample = vals[-16:]
        return "".join(blocks[min(7, max(0, int((v - lo) / (hi - lo) * 7)))] for v in sample)

    def _draw_sparkline_canvas(self, canvas, values, *, tone="neutral"):
        if canvas is None:
            return
        vals = [float(v) for v in (values or []) if v is not None][-24:]
        signature = (tuple(vals), tone)
        if getattr(canvas, "_tracker_signature", None) == signature:
            return
        canvas._tracker_signature = signature
        previous_job = getattr(canvas, "_tracker_animation_job", None)
        if previous_job:
            canvas.after_cancel(previous_job)
            canvas._tracker_animation_job = None
        w = max(canvas.winfo_width(), int(canvas.cget("width")), 40)
        h = max(canvas.winfo_height(), int(canvas.cget("height")), 24)
        color = {
            "profit": self._c["profit"],
            "loss": self._c["loss"],
            "warn": self._c["warn_orange"],
            "accent": self._c["accent"],
        }.get(tone, self._c["accent"])
        lo, hi = (min(vals), max(vals)) if vals else (0.0, 1.0)
        span = hi - lo or 1.0
        points = [
            (4 + (w - 8) * i / max(1, len(vals) - 1),
             6 + (h - 14) * (1 - (value - lo) / span))
            for i, value in enumerate(vals)
        ]

        def draw(step):
            if self._closed or not canvas.winfo_exists():
                return
            canvas.delete("all")
            canvas.create_line(0, h - 8, w, h - 8, fill=self._c["border_soft"])
            if len(points) < 2:
                canvas.create_line(4, h - 8, w - 4, h - 8, fill=color, width=2)
            else:
                count = max(2, round(len(points) * step / 7))
                flat = [coord for point in points[:count] for coord in point]
                canvas.create_line(*flat, fill=color, width=2.2, smooth=True)
            canvas._tracker_animation_job = (
                canvas.after(22, draw, step + 1) if step < 7 else None
            )

        draw(7 if self._reduce_motion or not canvas.winfo_viewable() else 1)

    def _set_kpi_card(self, card, *, icon="", value="—", sub="", tone="neutral", spark=None):
        colors = {
            "profit": self._c["profit"],
            "loss": self._c["loss"],
            "neutral": self._c["fg_muted"],
            "accent": self._c["accent"],
            "warn": self._c["warn_orange"],
        }
        color = colors.get(tone, self._c["fg_muted"])
        if icon:
            card["icon"].set(icon)
        card["value"].set(value)
        card["sub"].set(sub)
        card["sub_label"].configure(fg=color)
        card["accent"].configure(bg=color if tone in ("profit", "loss", "warn") else self._c["accent_muted"])
        if spark is not None:
            self._draw_sparkline_canvas(card.get("spark_canvas"), spark, tone=tone)

    def _selected_skin_original_name(self, values):
        try:
            skin_id = int(values[0])
        except (TypeError, ValueError, IndexError):
            return values[1] if len(values) > 1 else "—"
        return self._skin_row_names.get(skin_id, values[1] if len(values) > 1 else "—")

    def _ensure_item_price_history_table(self):
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS item_price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                captured_at TEXT NOT NULL,
                skin_id INTEGER NOT NULL,
                skin_name TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 0,
                buy_price_rub REAL NOT NULL DEFAULT 0,
                total_buy_price_rub REAL NOT NULL DEFAULT 0,
                buy_order_gross_rub REAL,
                sell_order_gross_rub REAL,
                buy_order_net_rub REAL,
                sell_order_net_rub REAL,
                lis_min_price_usd REAL,
                lis_min_price_rub REAL,
                lis_min_price_fee_rub REAL,
                lis_item_class_id TEXT,
                skin_image_url TEXT,
                status TEXT,
                FOREIGN KEY (skin_id) REFERENCES skins(id)
            )
            """
        )
        cur.execute("PRAGMA table_info(item_price_history)")
        have = {row[1] for row in cur.fetchall()}
        for col, decl in (
            ("lis_min_price_usd", "REAL"),
            ("lis_min_price_rub", "REAL"),
            ("lis_min_price_fee_rub", "REAL"),
            ("lis_item_class_id", "TEXT"),
            ("skin_image_url", "TEXT"),
        ):
            if col not in have:
                cur.execute(f"ALTER TABLE item_price_history ADD COLUMN {col} {decl}")
                have.add(col)
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_item_price_history_skin_time
            ON item_price_history (skin_id, captured_at)
            """
        )
        self.conn.commit()

    def _settings_key_visible_columns(self, table_key):
        return f"{table_key}_table_visible_columns"

    def _get_visible_table_columns(self, table_key, all_column_ids):
        raw = self._get_app_setting(self._settings_key_visible_columns(table_key), "")
        if not raw.strip():
            defaults = _DEFAULT_VISIBLE_COLUMNS.get(table_key, all_column_ids)
            return [column_id for column_id in defaults if column_id in all_column_ids]
        try:
            saved = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return list(all_column_ids)
        if not isinstance(saved, list):
            return list(all_column_ids)
        visible = [c for c in all_column_ids if c in saved]
        return visible if visible else [all_column_ids[0]]

    def _save_visible_table_columns(self, table_key, visible_ids):
        self._set_app_setting(
            self._settings_key_visible_columns(table_key),
            json.dumps(list(visible_ids), ensure_ascii=False),
        )

    def _apply_table_column_visibility(self, tree, table_key, all_column_ids):
        visible = self._get_visible_table_columns(table_key, all_column_ids)
        tree.configure(displaycolumns=visible)

    def _tree_column_id_at_event(self, tree, event_x):
        col_ref = tree.identify_column(event_x)
        if not col_ref or col_ref == "#0":
            return None
        try:
            idx = int(col_ref[1:]) - 1
        except ValueError:
            return None
        disp = tree.cget("displaycolumns")
        if not disp or disp == "#all":
            cols = tree.cget("columns")
        else:
            cols = disp
        if 0 <= idx < len(cols):
            return cols[idx]
        return None

    def _table_meta(self, table_key):
        return _SKINS_TABLE_COLUMNS if table_key == "skins" else _ASSETS_TABLE_COLUMNS

    def _configure_interactive_tree(self, tree, table_key):
        """Общее поведение таблиц: hover, курсор, Enter и двойной клик."""
        tree.configure(cursor="hand2", takefocus=True)
        tree.tag_configure("hover", background=self._c["row_hover"])
        tree.tag_configure("row_even", background=self._c["bg_table"], foreground=self._c["fg_muted"])
        tree.tag_configure("row_odd", background=self._c["row_alt"], foreground=self._c["fg_muted"])
        tree.bind(
            "<Motion>",
            lambda event, t=tree, key=table_key: self._on_table_motion(t, key, event),
            add="+",
        )
        tree.bind(
            "<Leave>",
            lambda _event, t=tree, key=table_key: self._clear_table_hover(t, key),
            add="+",
        )
        tree.bind(
            "<Double-1>",
            lambda event, t=tree, key=table_key: self._on_table_double_click(t, key, event),
        )
        tree.bind(
            "<Return>",
            lambda _event, t=tree, key=table_key: self._open_selected_table_row(t, key),
        )

    def _on_table_motion(self, tree, table_key, event):
        region = tree.identify_region(event.x, event.y)
        tree.configure(cursor="hand2" if region in ("cell", "heading") else "arrow")
        item = tree.identify_row(event.y) if region == "cell" else ""
        previous = self._table_hover_items.get(table_key)
        if previous == item:
            return
        self._clear_table_hover(tree, table_key)
        if item:
            tags = list(tree.item(item, "tags"))
            if "hover" not in tags:
                tree.item(item, tags=(*tags, "hover"))
            self._table_hover_items[table_key] = item

    def _clear_table_hover(self, tree, table_key):
        item = self._table_hover_items.pop(table_key, None)
        if not item or not tree.exists(item):
            return
        tags = tuple(tag for tag in tree.item(item, "tags") if tag != "hover")
        tree.item(item, tags=tags)

    def _on_table_double_click(self, tree, table_key, event):
        item = tree.identify_row(event.y)
        if not item:
            return "break"
        tree.selection_set(item)
        tree.focus(item)
        column_id = self._tree_column_id_at_event(tree, event.x)
        if table_key == "skins":
            if column_id == "history":
                self.show_history_graph()
            elif column_id == "target_sell":
                self.open_set_skin_target_window()
            else:
                self.open_edit_window()
        elif column_id == "target_sell":
            self.open_set_asset_target_window()
        else:
            self.open_edit_asset_window()
        return "break"

    def _open_selected_table_row(self, tree, table_key):
        if not tree.selection():
            return "break"
        if table_key == "skins":
            self.open_edit_window()
        else:
            self.open_edit_asset_window()
        return "break"

    @staticmethod
    def _table_sort_number(value):
        if value in (None, "", "-"):
            return float("-inf")
        text = str(value).replace(",", ".").replace("\u00a0", " ")
        match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
        return float(match.group(0)) if match else float("-inf")

    @staticmethod
    def _table_sort_date(value):
        text = str(value or "").strip()
        for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%Y %H:%M", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        return datetime.min

    def sort_table(self, tree, table_key, column_id):
        """Сортирует любую таблицу и показывает направление в заголовке."""
        state = self._table_sort_state[table_key]
        state["reverse"] = not state["reverse"] if state["column"] == column_id else False
        state["column"] = column_id

        columns = [meta[0] for meta in self._table_meta(table_key)]
        column_index = columns.index(column_id)
        numeric_columns = {
            "id", "quantity", "price", "total", "sell_price", "profit",
            "order_buy", "order_sell", "growth_pct", "target_sell", "history",
            "buy_usd", "rate_usd", "amount", "buy_rub", "cur_usd", "cur_rub",
            "value_usd", "value_rub",
        }
        date_columns = {"buy_date", "unlock_date", "sell_date", "updated"}

        def sort_key(item_id):
            values = tree.item(item_id, "values")
            value = values[column_index] if column_index < len(values) else ""
            if column_id in numeric_columns:
                return self._table_sort_number(value)
            if column_id in date_columns:
                return self._table_sort_date(value)
            return str(value or "").casefold()

        items = list(tree.get_children(""))
        items.sort(key=sort_key, reverse=state["reverse"])
        for index, item_id in enumerate(items):
            tree.move(item_id, "", index)

        arrow = " ▼" if state["reverse"] else " ▲"
        for current_id, heading, _width in self._table_meta(table_key):
            tree.heading(current_id, text=heading + (arrow if current_id == column_id else ""))

    def open_table_columns_window(self, table_key):
        if table_key == "skins":
            meta = _SKINS_TABLE_COLUMNS
            tree = self.tree
            title = "Колонки таблицы «Скины»"
        elif table_key == "assets":
            meta = _ASSETS_TABLE_COLUMNS
            tree = self.assets_tree
            title = "Колонки таблицы «Активы»"
        else:
            return
        all_ids = [m[0] for m in meta]
        visible_now = set(self._get_visible_table_columns(table_key, all_ids))

        win = tk.Toplevel(self.root)
        win.title(title)
        self._style_toplevel(win)
        win.transient(self.root)
        try:
            win.grab_set()
        except tk.TclError:
            pass

        frame = ttk.Frame(win, style="App.TFrame", padding=16)
        frame.pack(fill=tk.BOTH, expand=True)
        tk.Label(
            frame,
            text="Отметьте колонки, которые нужно показывать. Данные в скрытых колонках сохраняются.",
            **self._label_kw(),
            wraplength=420,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(0, 10))

        vars_map = {}
        chk_frame = ttk.Frame(frame, style="App.TFrame")
        chk_frame.pack(fill=tk.BOTH, expand=True)
        for col_id, label, _w in meta:
            v = tk.IntVar(value=1 if col_id in visible_now else 0)
            vars_map[col_id] = v
            tk.Checkbutton(
                chk_frame,
                text=label,
                variable=v,
                **self._chk_kw(),
            ).pack(anchor=tk.W, pady=2)

        def select_all():
            for v in vars_map.values():
                v.set(1)

        def select_none():
            for i, v in enumerate(vars_map.values()):
                v.set(1 if i == 0 else 0)

        presets = tk.Frame(chk_frame, bg=self._c["bg"])
        presets.pack(fill=tk.X, pady=(8, 4))
        ttk.Button(presets, text="Все", style="ToolbarMuted.TButton", command=select_all).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        ttk.Button(presets, text="Только первая", style="ToolbarMuted.TButton", command=select_none).pack(
            side=tk.LEFT
        )

        actions = ttk.Frame(frame, style="App.TFrame")
        actions.pack(fill=tk.X, pady=(12, 0))

        def save():
            chosen = [cid for cid in all_ids if vars_map[cid].get()]
            if not chosen:
                messagebox.showwarning("Колонки", "Должна остаться хотя бы одна видимая колонка.", parent=win)
                return
            self._save_visible_table_columns(table_key, chosen)
            self._apply_table_column_visibility(tree, table_key, all_ids)
            win.destroy()

        ttk.Button(actions, text="Сохранить", style="Success.TButton", command=save).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(actions, text="Отмена", style="Danger.TButton", command=win.destroy).pack(side=tk.LEFT)
        self._autosize_toplevel(win, min_w=360, min_h=320)

    def _load_price_alert_state(self):
        raw = self._get_app_setting(self.SETTINGS_KEY_PRICE_ALERTS_STATE, "{}")
        try:
            data = json.loads(raw or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_price_alert_state(self, state):
        self._set_app_setting(self.SETTINGS_KEY_PRICE_ALERTS_STATE, json.dumps(state, ensure_ascii=False))

    def _price_alerts_enabled(self):
        return self._get_app_setting(self.SETTINGS_KEY_PRICE_ALERTS, "1") == "1"

    def _check_target_price_alerts(self, buy_prices, sell_prices):
        if not self._price_alerts_enabled():
            return
        buy_prices = buy_prices or {}
        sell_prices = sell_prices or {}
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, name, target_sell_price_rub
            FROM skins
            WHERE (sell_date IS NULL OR sell_date = '' OR sell_date = '-')
              AND target_sell_price_rub IS NOT NULL
              AND target_sell_price_rub > 0
            """
        )
        rows = cursor.fetchall()
        if not rows:
            return

        state = self._load_price_alert_state()
        hits = []
        for skin_id, name, target_raw in rows:
            try:
                target = float(target_raw)
            except (TypeError, ValueError):
                continue
            if target <= 0:
                continue
            skin_key = str(skin_id)
            buy_net = self._market_net_price(buy_prices.get(name, 0))
            sell_net = self._market_net_price(sell_prices.get(name, 0))

            key_buy = f"{skin_key}:buy"
            key_sell = f"{skin_key}:sell"

            if buy_net >= target:
                if not state.get(key_buy):
                    hits.append({
                        "name": name,
                        "channel": "Order Buy (−10%)",
                        "price": buy_net,
                        "target": target,
                    })
                    state[key_buy] = True
            else:
                state.pop(key_buy, None)

            if sell_net >= target:
                if not state.get(key_sell):
                    hits.append({
                        "name": name,
                        "channel": "Order Sell (−10%)",
                        "price": sell_net,
                        "target": target,
                    })
                    state[key_sell] = True
            else:
                state.pop(key_sell, None)

        self._save_price_alert_state(state)
        if not hits:
            return

        lines = []
        for item in hits:
            lines.append(
                f"• {item['name']} — {item['channel']}: {item['price']:.2f} ₽ "
                f"(цель {item['target']:.2f} ₽)"
            )
        n = len(hits)
        title = "Целевая цена достигнута"
        body = f"Достигнута желаемая цена ({n}):\n\n" + "\n".join(lines)
        try:
            self.root.bell()
        except tk.TclError:
            pass
        messagebox.showinfo(title, body)

    def _buy_usd_rub_rate_preview(self, total_raw_usd):
        """Предпросмотр курса при покупке в USD: приоритет — сумма в ₽ + $, затем курс вручную, затем кэш API."""
        rb = (getattr(self, "buy_rub_equiv_total_var", None) and self.buy_rub_equiv_total_var.get() or "").strip()
        if rb:
            rub = self._parse_positive_float(rb)
            if rub and total_raw_usd > 0:
                return rub / total_raw_usd, "из суммы ₽ и $"
            # непустое, но не число — не подменяем курсом ниже (пользователь правит поле)
            try:
                float(rb.replace(",", "."))
            except ValueError:
                return None, None
        manual_r = self._parse_optional_usd_rate(self.manual_usd_rate_var.get())
        if manual_r is not None:
            return manual_r, "вручную"
        if self._usd_rate_cache:
            return self._usd_rate_cache, "с кнопки"
        return None, None

    def _buy_usd_rub_rate_save(self, total_raw_usd):
        """Курс для сохранения покупки в USD (та же логика; без кэша — API)."""
        rb = (getattr(self, "buy_rub_equiv_total_var", None) and self.buy_rub_equiv_total_var.get() or "").strip()
        if rb:
            rub = self._parse_positive_float(rb)
            if rub is None:
                raise ValueError("Некорректная сумма в поле «итого в ₽» (нужно положительное число).")
            if total_raw_usd <= 0:
                raise ValueError("Сумма в $ для расчёта курса должна быть больше нуля.")
            return rub / total_raw_usd
        raw_man = (self.manual_usd_rate_var.get() or "").strip()
        if raw_man:
            manual_r = self._parse_optional_usd_rate(raw_man)
            if manual_r is None:
                raise ValueError("Некорректный курс в поле «Курс вручную».")
            return manual_r
        rate, _src = self.fetch_usd_rub_rate()
        return rate

    def _sell_usd_to_rub_rate(self, *, currency, usd_per_item_str, rub_per_item_str, rate_str):
        """Эффективный курс ₽ за $ при продаже: из цены $/шт + ₽/шт или из поля курса."""
        if currency != "USD":
            try:
                return float((rate_str or "1").replace(",", "."))
            except ValueError:
                return 1.0
        usd = self._parse_positive_float(usd_per_item_str)
        rs = (rub_per_item_str or "").strip()
        if rs and usd:
            rub = self._parse_positive_float(rs)
            if rub:
                return rub / usd
            raise ValueError("Некорректная цена в поле «за штуку в ₽».")
        try:
            r = float((rate_str or "0").replace(",", "."))
        except ValueError:
            r = 0.0
        if r > 0:
            return r
        raise ValueError("Для продажи в USD укажите курс или обе цены за штуку ($ и ₽).")

    def _setup_theme(self):
        self._c = COLORS
        self._font_ui = ("Segoe UI", 10)
        self._font_ui_bold = ("Segoe UI", 10, "bold")
        self._font_title = ("Segoe UI", 17, "bold")
        self._font_small = ("Segoe UI", 9)
        self._font_kpi_value = ("Segoe UI", 17, "bold")
        self._font_detail_value = ("Segoe UI", 18, "bold")
        self._font_nav = ("Segoe UI", 10)
        self._font_badge = ("Segoe UI", 8, "bold")
        self._font_spark = ("Segoe UI", 12)
        self._font_icon = ("Segoe UI Symbol", 17)
        self._fonts = {
            'ui': self._font_ui,
            'small': self._font_small,
            'value': self._font_kpi_value,
            'nav': self._font_nav,
            'badge': self._font_badge,
            'spark': self._font_spark,
            'icon': self._font_icon,
        }

        self.root.configure(bg=self._c['bg'])
        style = ttk.Style(self.root)
        try:
            style.theme_use('clam')
        except tk.TclError:
            pass

        c = self._c
        style.configure('App.TFrame', background=c['bg'])
        style.configure('Card.TLabelframe', background=c['bg_card'], foreground=c['fg_dim'],
                        bordercolor=c['border'], borderwidth=1, relief='solid')
        style.configure('Card.TLabelframe.Label', background=c['bg_card'], foreground=c['fg_dim'],
                        font=self._font_ui_bold)
        style.configure('Header.TLabel', background=c['bg'], foreground=c['fg'], font=self._font_title)
        style.configure('Sub.TLabel', background=c['bg'], foreground=c['fg_dim'], font=self._font_small)

        btn_pad = {'padding': (12, 8)}
        btn_border = dict(borderwidth=0, relief='flat')
        style.configure(
            'Accent.TButton', background=c['accent'], foreground=c['bg'],
            focusthickness=1, focuscolor=c['accent'], font=self._font_ui_bold, **btn_pad, **btn_border
        )
        style.map(
            'Accent.TButton',
            background=[('active', '#9CF2E3'), ('disabled', c['border']), ('pressed', '#56C5B4')],
            foreground=[('disabled', c['fg_dim'])],
            relief=[('pressed', 'flat'), ('!pressed', 'flat')],
        )

        style.configure(
            'Primary.TButton', background=c['bg_elevated'], foreground=c['fg'],
            focusthickness=1, focuscolor=c['accent'], font=self._font_ui_bold, **btn_pad, **btn_border
        )
        style.map(
            'Primary.TButton',
            background=[('active', c['row_hover']), ('disabled', c['bg_card']), ('pressed', c['nav_active'])],
            foreground=[('disabled', c['fg_dim']), ('active', c['fg'])],
            relief=[('pressed', 'flat'), ('!pressed', 'flat')],
        )

        style.configure(
            'Success.TButton', background=c['success'], foreground='#f4f9f6',
            focusthickness=1, focuscolor=c['success'], font=self._font_ui_bold, **btn_pad, **btn_border
        )
        style.map(
            'Success.TButton',
            background=[('active', '#32AF83'), ('disabled', c['border']), ('pressed', '#197856')],
            relief=[('pressed', 'flat'), ('!pressed', 'flat')],
        )

        style.configure(
            'Danger.TButton', background=c['danger'], foreground='#fff5f5',
            focusthickness=1, focuscolor=c['danger'], font=self._font_ui_bold, **btn_pad, **btn_border
        )
        style.map(
            'Danger.TButton',
            background=[('active', '#E8657E'), ('disabled', c['border']), ('pressed', '#A73C55')],
            relief=[('pressed', 'flat'), ('!pressed', 'flat')],
        )

        style.configure(
            'ToolbarMuted.TButton', background=c['bg_elevated'], foreground=c['fg'],
            focusthickness=1, focuscolor=c['accent'], font=self._font_ui_bold, **btn_pad, **btn_border
        )
        style.map(
            'ToolbarMuted.TButton',
            background=[('active', c['row_hover']), ('disabled', c['bg_card']), ('pressed', c['nav_active'])],
            foreground=[('disabled', c['fg_dim']), ('active', c['fg'])],
            relief=[('pressed', 'flat'), ('!pressed', 'flat')],
        )

        style.configure(
            'Ghost.TButton', background=c['entry_bg'], foreground=c['fg_muted'],
            focusthickness=1, focuscolor=c['accent'], font=self._font_ui_bold, **btn_pad, **btn_border
        )
        style.map(
            'Ghost.TButton',
            background=[('active', c['bg_elevated']), ('disabled', c['bg_card']), ('pressed', c['nav_active'])],
            foreground=[('disabled', c['fg_dim']), ('active', c['fg'])],
            relief=[('pressed', 'flat'), ('!pressed', 'flat')],
        )

        detail_btn_pad = {'padding': (6, 4)}
        for detail_style, background, foreground in (
            ('DetailSuccess.TButton', c['success'], c['fg']),
            ('DetailPrimary.TButton', c['bg_elevated'], c['fg']),
            ('DetailGhost.TButton', c['entry_bg'], c['fg_muted']),
        ):
            style.configure(
                detail_style,
                background=background,
                foreground=foreground,
                font=self._font_small,
                borderwidth=0,
                relief='flat',
                **detail_btn_pad,
            )
        style.map('DetailSuccess.TButton', background=[('active', '#32AF83'), ('pressed', '#197856')])
        style.map('DetailPrimary.TButton', background=[('active', c['row_hover']), ('pressed', c['nav_active'])])
        style.map('DetailGhost.TButton', background=[('active', c['bg_elevated']), ('pressed', c['bg_table'])])

        style.configure('Treeview',
                        background=c['bg_table'], foreground=c['fg_muted'], fieldbackground=c['bg_table'],
                        rowheight=46, font=self._font_ui, borderwidth=0, relief='flat')
        style.configure('Treeview.Heading', background=c['bg_elevated'], foreground=c['fg'],
                        font=self._font_ui_bold, relief='flat', borderwidth=0, padding=(8, 9))
        style.map('Treeview.Heading',
                  background=[('active', c['accent_muted'])],
                  foreground=[('active', c['accent'])])
        style.map('Treeview',
                  background=[('selected', c['selected'])],
                  foreground=[('selected', '#0f1419')])

        style.configure('Vertical.TScrollbar', background=c['border'], troughcolor=c['bg'],
                        bordercolor=c['bg'], arrowcolor=c['fg_dim'], borderwidth=0)
        style.configure('Horizontal.TScrollbar', background=c['border'], troughcolor=c['bg'],
                        bordercolor=c['bg'], arrowcolor=c['fg_dim'], borderwidth=0)

        style.configure('TCombobox', fieldbackground=c['entry_bg'], background=c['bg_elevated'],
                        foreground=c['fg'], arrowcolor=c['accent'], borderwidth=1, padding=4)
        style.map('TCombobox',
                  fieldbackground=[('readonly', c['entry_bg'])],
                  selectbackground=[('readonly', c['entry_bg'])])

    def _style_toplevel(self, win):
        win.configure(bg=self._c['bg'])

    def _autosize_toplevel(self, win, *, extra_w=0, extra_h=0, min_w=None, min_h=None):
        """Подгоняет размер окна под текущий контент (requested size)."""
        try:
            win.update_idletasks()
            req_w = win.winfo_reqwidth() + int(extra_w or 0)
            req_h = win.winfo_reqheight() + int(extra_h or 0)

            sw = win.winfo_screenwidth()
            sh = win.winfo_screenheight()
            max_w = max(320, int(sw * 0.92))
            max_h = max(240, int(sh * 0.92))

            w = min(req_w, max_w)
            h = min(req_h, max_h)
            if min_w is not None:
                w = max(w, int(min_w))
            if min_h is not None:
                h = max(h, int(min_h))

            win.minsize(min(w, max_w), min(h, max_h))
            win.geometry(f"{w}x{h}")
        except tk.TclError:
            pass

    def _current_scope_ids(self):
        """Для статистики: выделенные строки (если есть) иначе всё, что сейчас в таблице."""
        ids = []
        try:
            sel = list(self.tree.selection())
        except Exception:
            sel = []

        items = sel if sel else list(self.tree.get_children())
        for iid in items:
            try:
                vals = self.tree.item(iid).get("values") or []
                if not vals:
                    continue
                ids.append(int(vals[0]))
            except Exception:
                continue

        seen = set()
        out = []
        for i in ids:
            if i not in seen:
                seen.add(i)
                out.append(i)
        return out

    def _entry_kw(self):
        return {
            'bg': self._c['entry_bg'], 'fg': self._c['fg'], 'insertbackground': self._c['fg'],
            'readonlybackground': self._c['bg_elevated'], 'relief': tk.FLAT, 'highlightthickness': 1,
            'highlightbackground': self._c['border'], 'highlightcolor': self._c['accent'],
            'font': self._font_ui,
        }

    def _table_scrollbar_kw(self):
        return {
            "bg": self._c["border"],
            "troughcolor": self._c["entry_bg"],
            "activebackground": self._c["accent_muted"],
            "highlightthickness": 0,
            "bd": 0,
            "relief": tk.FLAT,
        }

    def _skins_dropdown_menu_kw(self):
        """Общий стиль tk.Menu для панели скинов (родитель — root, не ttk)."""
        return dict(
            tearoff=0,
            bg=self._c['bg_elevated'],
            fg=self._c['fg'],
            activebackground=self._c['accent'],
            activeforeground='#0f1419',
            bd=0,
            font=self._font_ui,
            relief=tk.FLAT,
        )

    def _skins_popup_menu_below(self, button, menu):
        """Показать контекстное меню под кнопкой (ttk.Button + tk.Menu — нормальный клик и отрисовка)."""
        if 'disabled' in button.state():
            return
        self.root.update_idletasks()
        x = button.winfo_rootx()
        y = button.winfo_rooty() + button.winfo_height()
        menu.tk_popup(int(x), int(y))

    def _skins_popup_market_menu(self):
        self._skins_popup_menu_below(self.market_button, self._skins_market_menu)

    def _skins_popup_more_menu(self):
        self._skins_popup_menu_below(self._skins_more_button, self._skins_more_menu)

    def _skins_toolbar_btn_busy(self, btn, busy, *, idle_text, busy_text="…"):
        """Индикатор загрузки на кнопке панели скинов (ttk)."""
        if not btn:
            return
        if busy:
            btn.state(["disabled"])
            btn.configure(text=busy_text)
            self.root.update_idletasks()
        else:
            btn.state(["!disabled"])
            btn.configure(text=idle_text)

    def _label_kw(self, *, accent=False, bold=False, card=False):
        return {
            'bg': self._c['bg_card'] if card else self._c['bg'],
            'fg': self._c['accent'] if accent else self._c['fg_dim'],
            'font': self._font_ui_bold if bold else self._font_ui,
        }

    def _chk_kw(self):
        return {
            'bg': self._c['bg'], 'fg': self._c['fg'], 'activebackground': self._c['bg'],
            'activeforeground': self._c['fg'], 'selectcolor': self._c['entry_bg'],
            'font': self._font_ui,
        }

    def show_profile_selector(self):
        self.switch_profile("skins")

    def switch_profile(self, profile):
        if self.current_profile == profile:
            return
        self.current_profile = profile
        self._highlight_sidebar_profile(profile)
        if profile == "skins":
            self.assets_main_frame.pack_forget()
            self.skins_main_frame.pack(fill=tk.BOTH, expand=True)
            self._update_topbar_for_profile("skins")
            if hasattr(self, "_top_market_btn"):
                self._top_market_btn.pack(side=tk.RIGHT, padx=(6, 0))
            self.load_data()
            if self._get_app_setting(self.SETTINGS_KEY_AUTO_SKINS) == "1":
                self.update_market_prices(silent=True)
            return
        self.skins_main_frame.pack_forget()
        self.assets_main_frame.pack(fill=tk.BOTH, expand=True)
        self._update_topbar_for_profile("assets")
        if hasattr(self, "_top_market_btn"):
            self._top_market_btn.pack_forget()
        self.load_assets_data()
        if self._get_app_setting(self.SETTINGS_KEY_AUTO_ASSETS) == "1":
            self.update_assets_prices(silent=True)

    def _highlight_sidebar_profile(self, profile):
        for key, btn in getattr(self, "_sidebar_profile_btns", {}).items():
            active = key == profile
            btn["set_active"](active)
            if btn.get("badge"):
                btn["badge"].configure(bg=self._c["accent"] if active else self._c["bg_elevated"])

    def _highlight_sidebar_nav(self, nav_key):
        self._active_nav = nav_key
        for key, btn in getattr(self, "_sidebar_nav_btns", {}).items():
            btn["set_active"](key == nav_key)

    def _update_topbar_for_profile(self, profile):
        if profile == "skins":
            self._page_title.configure(text="Портфель скинов")
            self._page_subtitle.configure(text="Позиции, рынок и результат в одном месте")
            self._top_add_btn.configure(text="  +  Добавить", command=self.open_add_window)
            self._top_refresh_btn.configure(command=self.refresh_all)
            self._global_search_entry.configure(textvariable=self.name_search_var)
            self._search_placeholder = "Поиск по названию скина..."
        else:
            self._page_title.configure(text="Другие активы")
            self._page_subtitle.configure(text="Курсы, оценка и динамика портфеля")
            self._top_add_btn.configure(text="  +  Добавить актив", command=self.open_add_asset_window)
            self._top_refresh_btn.configure(command=self._refresh_assets_all)
            self._global_search_entry.configure(textvariable=self.asset_name_search_var)
            self._search_placeholder = "Поиск по активам..."
        self._apply_search_placeholder()

    def _refresh_assets_all(self):
        self.update_assets_prices(silent=False)

    def _focus_global_search(self, _event=None):
        try:
            self._global_search_entry.focus_set()
            self._clear_search_placeholder()
            self._global_search_entry.selection_range(0, tk.END)
        except tk.TclError:
            pass
        return "break"

    def _clear_search_placeholder(self, _event=None):
        if getattr(self, "_search_placeholder_active", False):
            self._global_search_entry.delete(0, tk.END)
            self._global_search_entry.configure(fg=self._c["fg"])
            self._search_placeholder_active = False

    def _apply_search_placeholder(self, _event=None):
        if not hasattr(self, "_global_search_entry"):
            return
        value = self._global_search_entry.get()
        if value and not getattr(self, "_search_placeholder_active", False):
            return
        self._search_placeholder_active = True
        self._global_search_entry.configure(fg=self._c["fg_dim"])
        self._global_search_entry.delete(0, tk.END)
        self._global_search_entry.insert(0, getattr(self, "_search_placeholder", "Поиск..."))

    def _search_text(self, var):
        value = (var.get() or "").strip()
        if getattr(self, "_search_placeholder_active", False) and value == getattr(self, "_search_placeholder", ""):
            return ""
        return value

    def _sidebar_nav_skins(self, action):
        self.switch_profile("skins")
        self._highlight_sidebar_nav(action)
        if action == "overview":
            self.status_filter_var.set("Все")
            self.load_data()
        elif action == "sales":
            self.status_filter_var.set("Продан")
            self.load_data()
        elif action == "charts":
            self.show_inventory_value_dynamics()
        elif action == "settings":
            self.open_settings()

    def _sidebar_nav_assets(self, action):
        self.switch_profile("assets")
        self._highlight_sidebar_nav(action)
        if action == "settings":
            self.open_settings()

    def _update_sync_footer(self):
        ts = datetime.now().strftime("%d.%m.%Y %H:%M")
        if hasattr(self, "_sync_label_var"):
            self._sync_label_var.set(f"Синхронизация: {ts}")
        self._last_sync_ts = ts

    def _refresh_usd_rate_footer(self):
        if not hasattr(self, "_rate_label_var"):
            return
        rate = self._current_usd_rate()
        self._rate_label_var.set(f"{rate:.2f} ₽" if rate else "Загрузка…")
        self._request_usd_rate()

    def _skin_status_from_db_dates(self, unlock_date, sell_date):
        if sell_date and sell_date != "-":
            return "Продан"
        try:
            if unlock_date:
                unlock = datetime.strptime(str(unlock_date)[:10], "%Y-%m-%d").date()
                if unlock > datetime.now().date():
                    return "Ждет разблокировки"
            return "Доступен для продажи"
        except (TypeError, ValueError):
            return "Неизвестно"

    def _save_item_price_snapshots(self, captured_at):
        self._ensure_item_price_history_table()
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, name, quantity, buy_price, total_buy_price, unlock_date, sell_date
            FROM skins
            WHERE sell_date IS NULL OR sell_date = '' OR sell_date = '-'
            """
        )
        rows = cursor.fetchall()
        buy_prices = getattr(self, "buy_order_prices", None) or {}
        sell_prices = getattr(self, "sell_order_prices", None) or {}
        payload = []
        for skin_id, name, qty_raw, buy_price_raw, total_buy_raw, unlock_date, sell_date in rows:
            try:
                qty = int(qty_raw or 0)
            except (TypeError, ValueError):
                qty = 0
            if qty <= 0:
                continue
            buy_gross = float(buy_prices.get(str(name), 0) or 0)
            sell_gross = float(sell_prices.get(str(name), 0) or 0)
            payload.append(
                (
                    captured_at,
                    int(skin_id),
                    str(name),
                    qty,
                    float(buy_price_raw or 0),
                    float(total_buy_raw or 0),
                    buy_gross if buy_gross > 0 else None,
                    sell_gross if sell_gross > 0 else None,
                    self._market_net_price(buy_gross) if buy_gross > 0 else None,
                    self._market_net_price(sell_gross) if sell_gross > 0 else None,
                    self._skin_status_from_db_dates(unlock_date, sell_date),
                )
            )
        if payload:
            cursor.executemany(
                """
                INSERT INTO item_price_history (
                    captured_at, skin_id, skin_name, quantity, buy_price_rub,
                    total_buy_price_rub, buy_order_gross_rub, sell_order_gross_rub,
                    buy_order_net_rub, sell_order_net_rub, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                payload,
            )
            self.conn.commit()

    def _current_available_skin_rows(self):
        now_iso = datetime.now().strftime("%Y-%m-%d")
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, name, quantity, buy_price, total_buy_price
            FROM skins
            WHERE (sell_date IS NULL OR sell_date = '' OR sell_date = '-')
              AND unlock_date <= ?
            """,
            (now_iso,),
        )
        return cursor.fetchall()

    def _available_inventory_series(self, price_column="sell_order_net_rub"):
        if price_column not in {"sell_order_net_rub", "buy_order_net_rub"}:
            price_column = "sell_order_net_rub"
        cache_key = (self.conn.total_changes, datetime.now().date(), price_column)
        if cache_key in self._series_cache:
            return self._series_cache[cache_key]
        current = {}
        for skin_id, name, qty, buy_price, total_buy in self._current_available_skin_rows():
            try:
                current[int(skin_id)] = int(qty or 0)
            except (TypeError, ValueError):
                continue
        if not current:
            return []
        placeholders = ",".join("?" for _ in current)
        cursor = self.conn.cursor()
        cursor.execute(
            f"""
            SELECT captured_at, skin_id, quantity, {price_column}
            FROM item_price_history
            WHERE skin_id IN ({placeholders})
              AND status = 'Доступен для продажи'
              AND {price_column} IS NOT NULL
            ORDER BY captured_at ASC
            """,
            tuple(current.keys()),
        )
        grouped = defaultdict(float)
        counts = defaultdict(int)
        for captured_at, skin_id, qty, price_net in cursor:
            try:
                sid = int(skin_id)
                qty_i = int(qty or 0)
            except (TypeError, ValueError):
                continue
            if current.get(sid) != qty_i:
                continue
            grouped[str(captured_at)] += float(price_net or 0) * qty_i
            counts[str(captured_at)] += 1
        required = len(current)
        series = []
        for stamp in sorted(grouped.keys()):
            if counts[stamp] == required:
                try:
                    dt = datetime.fromisoformat(str(stamp))
                except ValueError:
                    continue
                series.append((dt, grouped[stamp]))
        self._series_cache = {key: value for key, value in self._series_cache.items() if key[:2] == cache_key[:2]}
        self._series_cache[cache_key] = series
        return series

    def _stable_available_delta(self, days, price_column="sell_order_net_rub"):
        series = self._available_inventory_series(price_column)
        if len(series) < 2:
            return None
        current_dt, current_value = series[-1]
        target = current_dt - timedelta(days=days)
        index = bisect_right(series, (target, float("inf"))) - 1
        if index < 0:
            return None
        previous_dt, previous_value = series[index]
        if previous_value <= 0:
            return None
        delta = current_value - previous_value
        pct = delta / previous_value * 100.0
        return {"delta": delta, "pct": pct, "current": current_value, "previous": previous_value}

    def _fmt_money_pair_delta(self, sell_delta, buy_delta, label):
        def part(prefix, info):
            if not info:
                return f"{prefix} нет снимка"
            return f"{prefix} {info['delta']:+,.0f} ₽ ({info['pct']:+.2f}%)".replace(",", " ")
        return f"{part('Sell', sell_delta)} · {part('Buy', buy_delta)} за {label}"

    def _compute_and_show_skins_kpis(self):
        if not hasattr(self, "_skins_kpi_cards"):
            return
        cursor = self.conn.cursor()
        now_iso = datetime.now().strftime("%Y-%m-%d")
        invested = avail_cost = locked_cost = 0.0
        locked_nearest = None
        all_rows, avail_rows, locked_rows = [], [], []
        for name, quantity, cost, unlock_date in cursor.execute(
            "SELECT name, quantity, total_buy_price, unlock_date FROM skins WHERE sell_date IS NULL"
        ):
            invested += float(cost or 0)
            pair = (name, quantity)
            all_rows.append(pair)
            if unlock_date and unlock_date > now_iso:
                locked_cost += float(cost or 0)
                locked_rows.append(pair)
                if locked_nearest is None or unlock_date < locked_nearest:
                    locked_nearest = unlock_date
            else:
                avail_cost += float(cost or 0)
                avail_rows.append(pair)
        locked_count = len(locked_rows)
        avail_count = len(avail_rows)

        sell_prices = getattr(self, "sell_order_prices", None) or {}
        buy_prices = getattr(self, "buy_order_prices", None) or {}

        sell_value, _ = self._sum_market_net_rub(all_rows, sell_prices)
        buy_value, _ = self._sum_market_net_rub(all_rows, buy_prices)
        if sell_value <= 0:
            sell_value = invested
        sell_profit = sell_value - invested
        sell_pct = (sell_profit / invested * 100.0) if invested > 0 else 0.0
        buy_profit = buy_value - invested
        buy_pct = (buy_profit / invested * 100.0) if invested > 0 and buy_value > 0 else 0.0
        usd_rate = self._current_usd_rate()

        def _usd_line(amount):
            if not usd_rate or usd_rate <= 0:
                return "курс $ недоступен"
            return f"≈ {amount / usd_rate:,.2f} $".replace(",", " ")

        def _dual_usd_line(sell_amt, buy_amt, *, signed=False):
            if not usd_rate or usd_rate <= 0:
                return "курс $ недоступен"
            sell_usd = sell_amt / usd_rate if sell_amt is not None else None
            buy_usd = buy_amt / usd_rate if buy_amt is not None and (buy_amt > 0 or signed) else None
            sell_txt = self._fmt_usd_kpi(sell_usd, signed=signed)
            buy_txt = self._fmt_usd_kpi(buy_usd, signed=signed) if buy_usd is not None else "—"
            return f"$ Sell {sell_txt} · Buy {buy_txt}"

        invested_usd, invested_usd_fallback, invested_usd_missing = self._inventory_cost_usd(
            "sell_date IS NULL",
            (),
            usd_rate,
        )
        if invested_usd_missing:
            invested_usd_sub = "часть $ без курса"
        elif invested_usd_fallback:
            invested_usd_sub = f"себест. {self._fmt_usd_kpi(invested_usd)} · часть по текущему"
        else:
            invested_usd_sub = f"себест. {self._fmt_usd_kpi(invested_usd)}"

        avail_sell, _ = self._sum_market_net_rub(avail_rows, sell_prices)
        avail_buy, _ = self._sum_market_net_rub(avail_rows, buy_prices)
        locked_sell, _ = self._sum_market_net_rub(locked_rows, sell_prices)
        locked_buy, _ = self._sum_market_net_rub(locked_rows, buy_prices)

        def _dual_market_line(sell_amt, buy_amt):
            sell_txt = self._fmt_rub_kpi(sell_amt) if sell_amt > 0 else "—"
            buy_txt = self._fmt_rub_kpi(buy_amt) if buy_amt > 0 else "—"
            return f"Sell {sell_txt} · Buy {buy_txt}"

        cards = self._skins_kpi_cards
        cursor.execute(
            """
            SELECT inventory_sell_gross_rub, inventory_buy_cost_rub
            FROM inventory_value_history
            ORDER BY captured_at DESC
            LIMIT 24
            """
        )
        history_rows = cursor.fetchall()[::-1]
        spark_sell = [float(r[0] or 0) * (1.0 - MARKET_COMMISSION_RATE) for r in history_rows]
        spark_cost = [float(r[1] or 0) for r in history_rows]
        stable_sell_series = self._available_inventory_series("sell_order_net_rub")
        stable_buy_series = self._available_inventory_series("buy_order_net_rub")
        stable_series = stable_sell_series
        stable_values = [value for _dt, value in stable_series]
        stable_day_sell = self._stable_available_delta(1, "sell_order_net_rub")
        stable_week_sell = self._stable_available_delta(7, "sell_order_net_rub")
        stable_day_buy = self._stable_available_delta(1, "buy_order_net_rub")
        stable_week_buy = self._stable_available_delta(7, "buy_order_net_rub")

        def _delta_text(delta_info, label):
            if not delta_info:
                return f"нет снимка за {label}"
            return (
                f"{delta_info['delta']:+,.0f} ₽ · "
                f"{delta_info['pct']:+.2f}% за {label}"
            ).replace(",", " ")

        self._set_kpi_card(
            cards[0],
            icon="◈",
            value=self._fmt_rub_kpi(invested),
            sub=invested_usd_sub,
            tone="neutral",
            spark=spark_cost,
        )
        self._set_kpi_card(
            cards[1],
            icon="↗",
            value=f"Sell {self._fmt_rub_kpi(sell_value)}",
            sub=f"Buy {self._fmt_rub_kpi(buy_value) if buy_value > 0 else '—'} · {_dual_usd_line(sell_value, buy_value)}",
            tone="profit" if (stable_week_sell or {"delta": sell_pct})["delta"] >= 0 else "loss",
            spark=stable_values or spark_sell,
        )

        buy_profit_txt = f"{buy_profit:+,.0f} ₽".replace(",", " ") if buy_value > 0 else "—"
        self._set_kpi_card(
            cards[2],
            icon="↗",
            value=f"Sell {sell_profit:+,.0f} ₽".replace(",", " "),
            sub=f"Buy {buy_profit_txt} · {_dual_usd_line(sell_profit, buy_profit if buy_value > 0 else None, signed=True)}",
            tone="profit" if sell_profit >= 0 else "loss",
            spark=[v - invested for v in (stable_values or spark_sell)] if (stable_values or spark_sell) else None,
        )

        cursor.execute(
            """
            SELECT profit, sell_usd_rate_rub
            FROM skins
            WHERE profit IS NOT NULL
              AND sell_date IS NOT NULL
              AND sell_date != ''
              AND sell_date != '-'
            """
        )
        realized_profit = 0.0
        realized_profit_usd = 0.0
        realized_missing_rate = 0
        for profit_raw, sell_rate_raw in cursor.fetchall():
            try:
                profit_rub = float(profit_raw or 0.0)
            except (TypeError, ValueError):
                profit_rub = 0.0
            realized_profit += profit_rub
            try:
                sell_rate = float(sell_rate_raw or 0.0)
            except (TypeError, ValueError):
                sell_rate = 0.0
            if sell_rate > 0:
                realized_profit_usd += profit_rub / sell_rate
            else:
                realized_missing_rate += 1
        if abs(realized_profit_usd) > 0.000001:
            avg_realized_rate = realized_profit / realized_profit_usd
            realized_sub = (
                f"{self._fmt_usd_kpi(realized_profit_usd, signed=True)} · "
                f"ср. {avg_realized_rate:.2f} ₽/$"
            )
            if realized_missing_rate:
                realized_sub += f" · {realized_missing_rate} без курса"
        else:
            realized_sub = "нет курса продаж" if realized_missing_rate else self._fmt_usd_kpi(0, signed=True)
        self._set_kpi_card(
            cards[3],
            icon="✓",
            value=f"{realized_profit:+,.0f} ₽".replace(",", " "),
            sub=realized_sub,
            tone="profit" if realized_profit >= 0 else "loss",
            spark=None,
        )

        avail_sub = f"{avail_count} поз."
        avail_value = f"Sell {self._fmt_rub_kpi(avail_sell)}" if avail_sell > 0 else f"Buy {self._fmt_rub_kpi(avail_buy)}" if avail_buy > 0 else "—"
        if avail_sell <= 0 and avail_buy <= 0:
            avail_value = self._fmt_rub_kpi(avail_cost)
            avail_sub = f"{avail_count} поз. · нет цен маркета"
        else:
            avail_sub = (
                f"Buy {self._fmt_rub_kpi(avail_buy) if avail_buy > 0 else '—'} · "
                f"{_dual_usd_line(avail_sell, avail_buy)} · {avail_count} поз."
            )
        self._set_kpi_card(cards[4], icon="◎", value=avail_value, sub=avail_sub, tone="neutral")

        locked_sub = self._fmt_short_unlock_date(locked_nearest) if locked_nearest else f"{locked_count} поз."
        locked_value = self._fmt_rub_kpi(locked_sell) if locked_sell > 0 else self._fmt_rub_kpi(locked_buy) if locked_buy > 0 else "—"
        if locked_sell <= 0 and locked_buy <= 0:
            locked_value = self._fmt_rub_kpi(locked_cost)
            locked_sub = (
                f"{self._fmt_short_unlock_date(locked_nearest)} · нет цен маркета"
                if locked_nearest
                else f"{locked_count} поз. · нет цен маркета"
            )
        elif locked_nearest:
            locked_sub = f"{locked_sub} · {locked_count} поз."
        self._set_kpi_card(cards[5], icon="◷", value=locked_value, sub=locked_sub, tone="warn")

        if hasattr(self, "_positions_badge_var"):
            self._positions_badge_var.set(str(len(all_rows)))

        insights = getattr(self, "_skins_insight_cards", None)
        if insights:
            def _delta(values, offset):
                if len(values) < 2:
                    return None
                current = values[-1]
                prev = values[max(0, len(values) - 1 - offset)]
                return current - prev

            self._set_insight(
                insights, "today",
                self._fmt_money_pair_delta(stable_day_sell, stable_day_buy, "день"),
                tone="profit" if (stable_day_sell or stable_day_buy or {"delta": 0})["delta"] >= 0 else "loss",
            )
            self._set_insight(
                insights, "week",
                self._fmt_money_pair_delta(stable_week_sell, stable_week_buy, "неделю"),
                tone="profit" if (stable_week_sell or stable_week_buy or {"delta": 0})["delta"] >= 0 else "loss",
            )
            self._set_insight(
                insights, "roi", self._fmt_pct_kpi(sell_pct),
                tone="profit" if sell_pct >= 0 else "loss",
            )
            cursor.execute("SELECT name, quantity, buy_price FROM skins WHERE sell_date IS NULL")
            ranked = []
            for name, qty_raw, buy_price_raw in cursor.fetchall():
                try:
                    qty = int(qty_raw or 0)
                    buy_price = float(buy_price_raw or 0)
                    sell_price = float(sell_prices.get(name, 0) or 0) * 0.9
                    if qty > 0 and buy_price > 0 and sell_price > 0:
                        pct = (sell_price - buy_price) / buy_price * 100
                        ranked.append((pct, str(name)))
                except (TypeError, ValueError):
                    continue
            if ranked:
                best_pct, best_name = max(ranked, key=lambda item: item[0])
                worst_pct, worst_name = min(ranked, key=lambda item: item[0])
                self._set_insight(insights, "best", f"{self._compact_skin_name(best_name).replace(chr(10), ' ')}\n{best_pct:+.1f}%", tone="profit" if best_pct >= 0 else "loss")
                self._set_insight(insights, "worst", f"{self._compact_skin_name(worst_name).replace(chr(10), ' ')}\n{worst_pct:+.1f}%", tone="profit" if worst_pct >= 0 else "loss")
            else:
                self._set_insight(insights, "best", "Нет цен маркета", tone="neutral")
                self._set_insight(insights, "worst", "Нет цен маркета", tone="neutral")

    def _compute_and_show_assets_kpis(self, total_buy_rub=0, total_current_rub=0, total_buy_usd=0, total_current_usd=0):
        if not hasattr(self, "_assets_kpi_cards"):
            return
        pnl = total_current_rub - total_buy_rub
        pct = (pnl / total_buy_rub * 100.0) if total_buy_rub > 0 else 0.0
        cards = self._assets_kpi_cards
        self._set_kpi_card(cards[0], icon="◈", value=f"{total_buy_rub:,.0f} ₽".replace(",", " "), sub=f"{total_buy_usd:,.2f} $".replace(",", " "), tone="neutral")
        self._set_kpi_card(cards[1], icon="↗", value=f"{total_current_rub:,.0f} ₽".replace(",", " "), sub=f"{total_current_usd:,.2f} $".replace(",", " "), tone="profit" if pct >= 0 else "loss")
        self._set_kpi_card(cards[2], icon="↗", value=f"{pnl:+,.0f} ₽".replace(",", " "), sub=f"{pct:+.2f}%", tone="profit" if pnl >= 0 else "loss")
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM assets")
        self._set_kpi_card(cards[3], icon="◎", value=str(cursor.fetchone()[0] or 0), sub="всего позиций", tone="neutral")
        self._set_kpi_card(cards[4], icon="◷", value="—", sub="после обновления", tone="neutral")
        insights = getattr(self, "_assets_insight_cards", None)
        if insights:
            self._set_insight(insights, "today", "—", tone="neutral")
            self._set_insight(insights, "week", "—", tone="neutral")
            self._set_insight(insights, "roi", f"{pct:+.2f}%", tone="profit" if pct >= 0 else "loss")
            cursor.execute(
                """
                SELECT name, code, buy_price_rub, current_price_rub
                FROM assets
                WHERE buy_price_rub IS NOT NULL AND buy_price_rub > 0
                  AND current_price_rub IS NOT NULL AND current_price_rub > 0
                """
            )
            ranked = []
            for name, code, buy_rub, cur_rub in cursor.fetchall():
                pct_item = (float(cur_rub) - float(buy_rub)) / float(buy_rub) * 100
                ranked.append((pct_item, str(name or code)))
            if ranked:
                best_pct, best_name = max(ranked, key=lambda item: item[0])
                worst_pct, worst_name = min(ranked, key=lambda item: item[0])
                self._set_insight(insights, "best", f"{best_name}\n{best_pct:+.1f}%", tone="profit" if best_pct >= 0 else "loss")
                self._set_insight(insights, "worst", f"{worst_name}\n{worst_pct:+.1f}%", tone="profit" if worst_pct >= 0 else "loss")
            else:
                self._set_insight(insights, "best", "Нет текущих цен", tone="neutral")
                self._set_insight(insights, "worst", "Нет текущих цен", tone="neutral")

    def _on_skins_tree_select(self, _event=None):
        self._tasks.debounce("skin-selection", 90, self._render_skin_selection)

    def _render_skin_selection(self):
        sel = self.tree.selection()
        if not sel:
            self._clear_skin_detail()
            return
        values = self.tree.item(sel[0], "values")
        self._fill_skin_detail(values)

    def _on_assets_tree_select(self, _event=None):
        self._tasks.debounce("asset-selection", 90, self._render_asset_selection)

    def _render_asset_selection(self):
        sel = self.assets_tree.selection()
        if not sel:
            self._clear_asset_detail()
            return
        values = self.assets_tree.item(sel[0], "values")
        self._fill_asset_detail(values)

    def _clear_skin_detail(self):
        self._skin_detail_token += 1
        self._detail_skin_key = None
        self._tasks.cancel("skin-detail")
        self._skin_detail_title.set("Выберите позицию")
        self._skin_detail_sub.set("")
        self._skin_detail_profit.set("")
        if hasattr(self, "_skin_detail_profit_label"):
            self._skin_detail_profit_label.configure(fg=self._c["fg_dim"])
        self._update_skin_detail_actions("—")
        self._set_detail_loading(False)
        if hasattr(self, "_skin_image_label"):
            self._set_preview_image(self._skin_image_label, None, ref_key="skin", placeholder="Выберите позицию")
        for var in self._skin_detail_fields.values():
            var.set("—")
        self._clear_detail_chart(self._skin_chart_frame)

    def _clear_asset_detail(self):
        self._asset_image_token = None
        self._tasks.cancel("asset-image")
        self._asset_detail_title.set("Выберите актив")
        self._asset_detail_sub.set("")
        self._asset_detail_profit.set("")
        if hasattr(self, "_asset_detail_profit_label"):
            self._asset_detail_profit_label.configure(fg=self._c["fg_dim"])
        self._set_asset_loading(False)
        if hasattr(self, "_asset_image_label"):
            self._set_preview_image(self._asset_image_label, None, ref_key="asset", placeholder="Выберите актив")
        for var in self._asset_detail_fields.values():
            var.set("—")
        self._clear_detail_chart(self._asset_chart_frame)

    def _clear_detail_chart(self, frame):
        chart = getattr(self, "_skin_price_chart", None)
        if frame is getattr(self, "_skin_chart_frame", None) and chart is not None:
            if self._closed:
                chart.close()
                self._skin_price_chart = None
            else:
                chart.clear()
            return
        for widget in frame.winfo_children():
            widget.destroy()

    @staticmethod
    def _format_chart_timestamp(value):
        try:
            timestamp = float(value)
            if timestamp > 100_000_000_000:
                timestamp /= 1000.0
            return datetime.fromtimestamp(timestamp).strftime("%d.%m.%Y %H:%M")
        except (TypeError, ValueError, OSError, OverflowError):
            return str(value)

    def _attach_chart_interactions(self, fig, ax, series, *, hover_radius=14):
        return self._chart_interactions.attach(
            fig, ax, series, hover_radius=hover_radius,
        )


    def _add_chart_toolbar(self, canvas, parent):
        return add_navigation_toolbar(
            canvas,
            parent,
            colors=self._c,
            font_small=self._font_small,
        )


    def _fill_skin_detail(self, values):
        cols = getattr(self, "_skins_table_columns", [m[0] for m in _SKINS_TABLE_COLUMNS])
        data = {cols[i]: values[i] if i < len(values) else "—" for i in range(len(cols))}
        name = self._selected_skin_original_name(values)
        self._skin_detail_title.set(name)
        self._skin_detail_sub.set(data.get("status", ""))
        self._update_skin_detail_actions(data.get("status", ""))
        profit_raw = data.get("profit", "-")
        detail_profit = None
        detail_pct = None
        if profit_raw and profit_raw != "-":
            self._skin_detail_profit.set(profit_raw)
            detail_pct = self._extract_percent(profit_raw)
            try:
                detail_profit = float(str(profit_raw).replace(",", ".").split()[0])
            except (TypeError, ValueError):
                detail_profit = None
        else:
            sell_net = (getattr(self, "sell_order_prices", None) or {}).get(name, 0)
            try:
                qty = int(data.get("quantity", 0) or 0)
                total_buy = float(str(data.get("total", "0")).replace(",", "."))
            except (TypeError, ValueError):
                qty, total_buy = 0, 0.0
            if sell_net and qty:
                net = float(sell_net) * 0.9 * qty
                p = net - total_buy
                pp = (p / total_buy * 100) if total_buy else 0
                self._skin_detail_profit.set(f"{p:+,.2f} ₽  ({pp:+.2f}%)".replace(",", " "))
                detail_profit = p
                detail_pct = pp
            else:
                self._skin_detail_profit.set("—")
        if hasattr(self, "_skin_detail_profit_label"):
            if detail_profit is None:
                tone = self._c["fg_dim"]
            else:
                tone = self._c["profit"] if detail_profit >= 0 else self._c["loss"]
            self._skin_detail_profit_label.configure(fg=tone)
        try:
            qty = int(data.get("quantity", 0) or 0)
        except (TypeError, ValueError):
            qty = 0
        try:
            buy_price = float(str(data.get("price", "0")).replace(",", "."))
            total_buy = float(str(data.get("total", "0")).replace(",", "."))
        except (TypeError, ValueError):
            buy_price, total_buy = 0.0, 0.0
        sell_order = (getattr(self, "sell_order_prices", None) or {}).get(name, 0)
        current_net = float(sell_order or 0) * 0.9 * qty if qty else 0.0
        current_text = f"{current_net:,.2f} ₽".replace(",", " ") if current_net else "—"
        roi_text = f"{detail_pct:+.2f}%" if detail_pct is not None else "—"
        mapping = {
            "Покупка": f"{buy_price:,.2f} ₽".replace(",", " ") if buy_price else data.get("price", "—"),
            "Текущая": current_text,
            "ROI": roi_text,
            "Количество": data.get("quantity", "—"),
            "Всего": data.get("total", "—"),
            "Разблокировка": data.get("unlock_date", "—"),
            "Статус": data.get("status", "—"),
        }
        for label, var in self._skin_detail_fields.items():
            var.set(str(mapping.get(label, "—")))
        try:
            skin_id = int(values[0])
        except (TypeError, ValueError, IndexError):
            skin_id = None
        self._draw_skin_detail_chart(name, skin_id=skin_id)
        detail_key = (skin_id, name)
        if getattr(self, "_detail_skin_key", None) != detail_key:
            self._detail_skin_key = detail_key
            self._refresh_skin_detail_async(skin_id, name)

    def _update_skin_detail_actions(self, status):
        actions = getattr(self, "_skin_detail_action_buttons", None)
        if not actions:
            return
        for button in actions.values():
            button.grid_remove()
        is_sold = str(status or "").strip() == "Продан"
        if is_sold:
            actions["edit"].grid(row=0, column=0, sticky="ew", padx=(0, 4))
            actions["lis"].grid(row=0, column=1, columnspan=2, sticky="ew", padx=(4, 0))
            return
        actions["sell"].grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 5))
        actions["edit"].grid(row=1, column=0, sticky="ew", padx=(0, 3))
        actions["market"].grid(row=1, column=1, sticky="ew", padx=3)
        actions["lis"].grid(row=1, column=2, sticky="ew", padx=(3, 0))

    def _refresh_skin_detail_async(self, skin_id, skin_name):
        if not hasattr(self, "_skin_image_label"):
            return
        self._skin_detail_token += 1
        token = self._skin_detail_token
        usd_rate = self._current_usd_rate()
        self._set_preview_image(self._skin_image_label, None, ref_key="skin", placeholder="Загрузка…")
        self._set_detail_loading(True, "Lis Skins и изображение")

        def worker():
            info = self._lis_lowest_item_info(skin_name)
            image = None
            if info and info.get("image_url"):
                try:
                    image = self._download_image_bytes(info["image_url"])
                except (requests.RequestException, ValueError):
                    pass
            return info, image

        def finish(result):
            if token != self._skin_detail_token:
                return
            current = self.tree.selection()
            if not current or str(self.tree.item(current[0], "values")[0]) != str(skin_id):
                return
            info, image = result
            if info:
                self._save_lis_item_snapshot(skin_id, skin_name, info, usd_rate)
                self._draw_skin_detail_chart(skin_name, skin_id=skin_id)
            self._set_preview_image(self._skin_image_label, image, ref_key="skin", placeholder="Изображение не найдено")
            self._set_detail_loading(False)

        self._tasks.submit("skin-detail", worker, finish,
                           lambda exc: self._set_detail_loading(False), replace=True)

    def _load_asset_image_async(self, asset_group, code):
        url = self._asset_image_url(asset_group, code)
        self._asset_image_token = (asset_group, code)
        token = self._asset_image_token
        self._tasks.cancel("asset-image")
        if not url:
            self._set_preview_image(self._asset_image_label, None, ref_key="asset", placeholder="Нет изображения")
            self._set_asset_loading(False)
            return
        self._set_preview_image(self._asset_image_label, None, ref_key="asset", placeholder="Загрузка…")
        self._set_asset_loading(True, "изображение актива")

        def finish(data):
            if self._asset_image_token == token:
                self._set_preview_image(self._asset_image_label, data, ref_key="asset", placeholder="Изображение не найдено")

        self._tasks.submit("asset-image", lambda: self._download_image_bytes(url), finish,
                           lambda exc: finish(None), lambda: self._set_asset_loading(False), replace=True)

    def _fill_asset_detail(self, values):
        cols = getattr(self, "_assets_table_columns", [m[0] for m in _ASSETS_TABLE_COLUMNS])
        data = {cols[i]: values[i] if i < len(values) else "—" for i in range(len(cols))}
        self._asset_detail_title.set(data.get("name", "—"))
        self._asset_detail_sub.set(f"{data.get('group', '')} · {data.get('code', '')}")
        growth = data.get("growth_pct", "—")
        self._asset_detail_profit.set(growth if growth != "—" else "—")
        if hasattr(self, "_asset_detail_profit_label"):
            pct = self._extract_percent(growth)
            if pct is None:
                color = self._c["fg_dim"]
            else:
                color = self._c["profit"] if pct >= 0 else self._c["loss"]
            self._asset_detail_profit_label.configure(fg=color)
        mapping = {
            "Количество": data.get("amount", "—"),
            "Покупка ₽": data.get("buy_rub", "—"),
            "Текущая ₽": data.get("cur_rub", "—"),
            "Сумма ₽": data.get("value_rub", "—"),
            "Цель": data.get("target_sell", "—"),
            "Обновлено": data.get("updated", "—"),
        }
        for label, var in self._asset_detail_fields.items():
            var.set(str(mapping.get(label, "—")))
        self._load_asset_image_async(data.get("group", ""), data.get("code", ""))

    def _item_price_history_rows(self, skin_name, skin_id=None):
        cursor = self.conn.cursor()
        if skin_id is not None:
            cursor.execute(
                """
                SELECT captured_at, sell_order_net_rub, buy_order_net_rub, buy_price_rub,
                       lis_min_price_fee_rub
                FROM item_price_history
                WHERE skin_id = ?
                ORDER BY captured_at ASC
                """,
                (skin_id,),
            )
        else:
            cursor.execute(
                """
                SELECT captured_at, sell_order_net_rub, buy_order_net_rub, buy_price_rub,
                       lis_min_price_fee_rub
                FROM item_price_history
                WHERE skin_name = ?
                ORDER BY captured_at ASC
                """,
                (skin_name,),
            )
        rows = []
        for captured_at, sell_net, buy_net, cost, lis_fee in cursor.fetchall():
            try:
                dt = datetime.fromisoformat(str(captured_at))
            except ValueError:
                continue
            rows.append((dt, sell_net, buy_net, cost, lis_fee))
        return rows

    def _save_single_item_price_snapshot(self, skin_id, skin_name):
        try:
            skin_id = int(skin_id)
        except (TypeError, ValueError):
            return
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, name, quantity, buy_price, total_buy_price, unlock_date, sell_date
            FROM skins
            WHERE id = ?
            """,
            (skin_id,),
        )
        row = cursor.fetchone()
        if not row:
            return
        captured_at = datetime.now().isoformat(timespec="seconds")
        _id, name, qty, buy_price, total_buy, unlock_date, sell_date = row
        buy_gross = float((getattr(self, "buy_order_prices", None) or {}).get(str(name), 0) or 0)
        sell_gross = float((getattr(self, "sell_order_prices", None) or {}).get(str(name), 0) or 0)
        cursor.execute(
            """
            INSERT INTO item_price_history (
                captured_at, skin_id, skin_name, quantity, buy_price_rub,
                total_buy_price_rub, buy_order_gross_rub, sell_order_gross_rub,
                buy_order_net_rub, sell_order_net_rub, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                captured_at,
                skin_id,
                str(name),
                int(qty or 0),
                float(buy_price or 0),
                float(total_buy or 0),
                buy_gross if buy_gross > 0 else None,
                sell_gross if sell_gross > 0 else None,
                self._market_net_price(buy_gross) if buy_gross > 0 else None,
                self._market_net_price(sell_gross) if sell_gross > 0 else None,
                self._skin_status_from_db_dates(unlock_date, sell_date),
            ),
        )
        self.conn.commit()

    def _skin_chart_rows(self, skin_name, skin_id):
        rows = self._item_price_history_rows(skin_name, skin_id)
        history = self.sales_history.get(skin_name, [])
        if not rows and history:
            rows = [
                (datetime.fromtimestamp(float(ts) / (1000.0 if float(ts) > 100_000_000_000 else 1.0)), float(price), None, None, None)
                for ts, price in sorted(history, key=lambda point: point[0])
            ]
        return rows

    def _draw_skin_detail_chart(self, skin_name, *, skin_id=None):
        rows = self._skin_chart_rows(skin_name, skin_id)
        chart = getattr(self, "_skin_price_chart", None)
        if chart is None:
            for widget in self._skin_chart_frame.winfo_children():
                widget.destroy()
            chart = ItemPriceChart(self._skin_chart_frame, self._c, self._font_small, self._chart_interactions)
            self._skin_price_chart = chart
        chart.set_data(rows)

    def open_selected_skin_chart(self):
        selected = self.tree.selection()
        if not selected:
            return
        values = self.tree.item(selected[0], "values")
        skin_id = int(values[0])
        skin_name = self._selected_skin_original_name(values)
        rows = self._skin_chart_rows(skin_name, skin_id)
        if self._popout_window is not None and self._popout_window.winfo_exists():
            self._popout_window.title(f"История цены · {skin_name}")
            self._popout_chart.set_data(rows)
            self._popout_window.lift()
            return
        window = tk.Toplevel(self.root)
        self._style_toplevel(window)
        window.title(f"История цены · {skin_name}")
        window.geometry("640x610")
        window.minsize(460, 460)
        chart = ItemPriceChart(window, self._c, self._font_small, self._chart_interactions)
        chart.canvas.get_tk_widget().configure(width=580, height=470)
        chart.set_data(rows)
        self._popout_window, self._popout_chart = window, chart

        def close_chart():
            chart.close()
            self._popout_chart = self._popout_window = None
            window.destroy()

        window.protocol("WM_DELETE_WINDOW", close_chart)

    def _build_app_shell(self):
        shell = tk.Frame(self.root, bg=self._c["bg"])
        shell.pack(fill=tk.BOTH, expand=True)
        self._sidebar = tk.Frame(shell, bg=self._c["bg_sidebar"], width=220)
        self._sidebar.pack(side=tk.LEFT, fill=tk.Y)
        self._sidebar.pack_propagate(False)
        self._main_col = ttk.Frame(shell, style="App.TFrame")
        self._main_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._build_sidebar()
        self._build_topbar(self._main_col)
        self._content_stack = ttk.Frame(self._main_col, style="App.TFrame", padding=(20, 0, 20, 16))
        self._content_stack.pack(fill=tk.BOTH, expand=True)

    def _build_sidebar(self):
        c, f = self._c, self._fonts
        logo = tk.Frame(self._sidebar, bg=c["bg_sidebar"], pady=22, padx=18)
        logo.pack(fill=tk.X)
        tk.Label(logo, text="ST", bg=c["accent"], fg=c["bg"], font=("Segoe UI", 13, "bold"), padx=9, pady=5).pack(side=tk.LEFT)
        brand = tk.Frame(logo, bg=c["bg_sidebar"])
        brand.pack(side=tk.LEFT, padx=(10, 0))
        tk.Label(brand, text="SKIN TRACKER", bg=c["bg_sidebar"], fg=c["fg"], font=("Segoe UI", 10, "bold")).pack(anchor=tk.W)
        tk.Label(brand, text="PORTFOLIO DESK", bg=c["bg_sidebar"], fg=c["fg_dim"], font=("Segoe UI", 8)).pack(anchor=tk.W)

        nav_box = tk.Frame(self._sidebar, bg=c["bg_sidebar"])
        nav_box.pack(fill=tk.BOTH, expand=True, padx=10, pady=(10, 0))
        tk.Label(nav_box, text="РАБОЧАЯ ОБЛАСТЬ", bg=c["bg_sidebar"], fg=c["fg_dim"],
                 font=("Segoe UI", 8, "bold"), anchor=tk.W).pack(fill=tk.X, padx=12, pady=(0, 8))
        self._sidebar_profile_btns = {}
        self._sidebar_profile_btns["skins"] = sidebar_nav_btn(
            nav_box, c, f, "◈   Скины", lambda: self.switch_profile("skins"), active=True,
            reduce_motion=self._reduce_motion,
        )
        self._sidebar_profile_btns["assets"] = sidebar_nav_btn(
            nav_box, c, f, "◇   Активы", lambda: self.switch_profile("assets"),
            reduce_motion=self._reduce_motion,
        )
        tk.Frame(nav_box, bg=c["border"], height=1).pack(fill=tk.X, pady=15, padx=8)
        tk.Label(nav_box, text="АНАЛИТИКА", bg=c["bg_sidebar"], fg=c["fg_dim"],
                 font=("Segoe UI", 8, "bold"), anchor=tk.W).pack(fill=tk.X, padx=12, pady=(0, 8))

        self._sidebar_nav_btns = {}
        self._positions_badge_var = tk.StringVar(value="0")
        self._sidebar_nav_btns["overview"] = sidebar_nav_btn(
            nav_box, c, f, "▣   Обзор",
            lambda: self._sidebar_nav_skins("overview"), active=True,
            reduce_motion=self._reduce_motion,
        )
        self._sidebar_nav_btns["positions"] = sidebar_nav_btn(
            nav_box, c, f, "≡   Позиции",
            lambda: self._sidebar_nav_skins("overview"),
            badge=self._positions_badge_var.get(),
            reduce_motion=self._reduce_motion,
        )
        if self._sidebar_nav_btns["positions"].get("badge"):
            self._sidebar_nav_btns["positions"]["badge"].configure(textvariable=self._positions_badge_var)
        self._sidebar_nav_btns["sales"] = sidebar_nav_btn(
            nav_box, c, f, "↗   Продажи", lambda: self._sidebar_nav_skins("sales"),
            reduce_motion=self._reduce_motion,
        )
        self._sidebar_nav_btns["charts"] = sidebar_nav_btn(
            nav_box, c, f, "⌁   Графики", lambda: self._sidebar_nav_skins("charts"),
            reduce_motion=self._reduce_motion,
        )
        self._sidebar_nav_btns["settings"] = sidebar_nav_btn(
            nav_box, c, f, "⚙   Настройки", lambda: self.open_settings(),
            reduce_motion=self._reduce_motion,
        )

        foot = tk.Frame(self._sidebar, bg=c["bg_card"], padx=16, pady=14,
                        highlightbackground=c["border_soft"], highlightthickness=1)
        foot.pack(side=tk.BOTTOM, fill=tk.X, padx=14, pady=16)
        tk.Label(foot, text="КУРС USD / RUB", bg=c["bg_card"], fg=c["fg_dim"], font=f["small"]).pack(anchor=tk.W)
        self._rate_label_var = tk.StringVar(value="—")
        tk.Label(foot, textvariable=self._rate_label_var, bg=c["bg_card"], fg=c["fg"], font=f["value"]).pack(anchor=tk.W, pady=(6, 0))
        self._sync_label_var = tk.StringVar(value="")
        tk.Label(foot, textvariable=self._sync_label_var, bg=c["bg_card"], fg=c["fg_dim"], font=f["small"], wraplength=175).pack(anchor=tk.W, pady=(8, 0))
        self._refresh_usd_rate_footer()

    def _build_topbar(self, parent):
        bar = tk.Frame(parent, bg=self._c["bg"], padx=22, pady=18)
        bar.pack(fill=tk.X)
        heading = tk.Frame(bar, bg=self._c["bg"])
        heading.pack(fill=tk.X, pady=(0, 14))
        tk.Label(heading, text="ВАШ ПОРТФЕЛЬ  /  ОБЗОР", bg=self._c["bg"],
                 fg=self._c["accent"], font=("Segoe UI", 8, "bold")).pack(anchor=tk.W)
        self._page_title = tk.Label(heading, text="Портфель скинов", bg=self._c["bg"],
                                    fg=self._c["fg"], font=("Segoe UI", 23, "bold"))
        self._page_title.pack(anchor=tk.W, pady=(2, 0))
        self._page_subtitle = tk.Label(heading, text="Позиции, рынок и результат в одном месте",
                                       bg=self._c["bg"], fg=self._c["fg_dim"], font=self._font_ui)
        self._page_subtitle.pack(anchor=tk.W, pady=(2, 0))
        actions = tk.Frame(bar, bg=self._c["bg"])
        actions.pack(fill=tk.X)
        self._background_progress = ttk.Progressbar(actions, mode="indeterminate", length=65)
        search_wrap = tk.Frame(actions, bg=self._c["entry_bg"], highlightbackground=self._c["border_soft"], highlightthickness=1)
        search_wrap.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 12))
        tk.Label(search_wrap, text="⌕", bg=self._c["entry_bg"], fg=self._c["accent"],
                 font=("Segoe UI Symbol", 15)).pack(side=tk.LEFT, padx=(12, 6))
        self.name_search_var = tk.StringVar()
        self.asset_name_search_var = tk.StringVar()
        self.asset_name_search_var.trace_add("write", lambda *_: self._tasks.debounce("asset-search", 180, self.load_assets_data))
        self._global_search_entry = tk.Entry(search_wrap, textvariable=self.name_search_var, **self._entry_kw(), bd=0)
        self._global_search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=7, padx=(0, 8))
        self._search_placeholder = "Поиск по названию скина..."
        self._search_placeholder_active = False
        self._global_search_entry.bind("<FocusIn>", self._clear_search_placeholder)
        self._global_search_entry.bind("<FocusOut>", self._apply_search_placeholder)
        self.root.after_idle(self._apply_search_placeholder)
        tk.Label(search_wrap, text="Ctrl+K", bg=self._c["entry_bg"], fg=self._c["fg_dim"], font=self._font_small).pack(side=tk.RIGHT, padx=10)
        self._top_add_btn = ttk.Button(actions, text="  +  Добавить", style="Accent.TButton", command=self.open_add_window)
        self._top_add_btn.pack(side=tk.RIGHT, padx=(6, 0))
        self._top_refresh_btn = ttk.Button(actions, text="Обновить", style="Primary.TButton", command=self.refresh_all)
        self._top_refresh_btn.pack(side=tk.RIGHT, padx=(6, 0))
        self._skins_market_menu = tk.Menu(self.root, **self._skins_dropdown_menu_kw())
        self._skins_market_menu.add_command(label="Цены с маркета", command=self.update_market_prices)
        self._skins_market_menu.add_separator()
        self._skins_market_menu.add_command(label="Витрина: выставить", command=self.open_cs_market_list_window)
        self._skins_market_menu.add_command(label="Витрина: цена", command=self.open_cs_market_set_price_window)
        self._skins_market_menu.add_command(label="Витрина: снять", command=self.open_cs_market_remove_window)
        self._top_market_btn = ttk.Button(
            actions, text="Маркет ▾", style="Primary.TButton", command=self._skins_popup_market_menu,
        )
        self._top_market_btn.pack(side=tk.RIGHT, padx=(6, 0))
        self.market_button = self._top_market_btn
        self.root.bind_all("<Control-k>", self._focus_global_search)
        self.root.bind_all("<Control-K>", self._focus_global_search)

    def _build_kpi_row(self, parent, titles, attr_name):
        row = tk.Frame(parent, bg=self._c["bg"])
        row.pack(fill=tk.X, pady=(0, 8))
        columns = 3 if len(titles) > 3 else len(titles)
        for column in range(columns):
            row.grid_columnconfigure(column, weight=1, uniform="metric")
        cards = []
        for index, title in enumerate(titles):
            card = metric_card(row, self._c, self._fonts, title)
            card["frame"].grid(row=index // columns, column=index % columns,
                               sticky="nsew", padx=(0, 10), pady=(0, 6))
            card["icon"].set(_KPI_ICONS.get(title, ""))
            cards.append(card)
        setattr(self, attr_name, cards)
        return row

    def _build_insight_row(self, parent, attr_name):
        row = tk.Frame(parent, bg=self._c["bg"])
        row.pack(fill=tk.X, pady=(0, 14))
        cards = {}
        for key, title in (
            ("today", "Сегодня"),
            ("week", "За неделю"),
            ("roi", "Доходность"),
            ("best", "Лучший актив"),
            ("worst", "Худший актив"),
        ):
            box = tk.Frame(
                row, bg=self._c["bg_card"], padx=14, pady=12,
                highlightbackground=self._c["border_soft"], highlightthickness=1,
            )
            box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))
            tk.Label(box, text=title, bg=self._c["bg_card"], fg=self._c["fg_dim"], font=self._font_small).pack(anchor=tk.W)
            value = tk.StringVar(value="—")
            label = tk.Label(box, textvariable=value, bg=self._c["bg_card"], fg=self._c["fg"], font=self._font_ui_bold, anchor=tk.W, justify=tk.LEFT)
            label.pack(fill=tk.X, pady=(5, 0))
            cards[key] = {"value": value, "label": label}
        setattr(self, attr_name, cards)
        return row

    def _set_insight(self, cards, key, value, *, tone="neutral"):
        if not cards or key not in cards:
            return
        color = {
            "profit": self._c["profit"],
            "loss": self._c["loss"],
            "neutral": self._c["fg_muted"],
            "warn": self._c["warn_orange"],
        }.get(tone, self._c["fg_muted"])
        cards[key]["value"].set(value)
        cards[key]["label"].configure(fg=color)

    def _build_detail_panel(self, parent, *, mode):
        return build_detail_panel(self, parent, mode=mode)

    def _build_assets_dashboard(self, main):
        self._build_kpi_row(
            main,
            ("Вложено", "Текущая оценка", "Прибыль / убыток", "Позиций", "Обновление"),
            "_assets_kpi_cards",
        )
        self._build_insight_row(main, "_assets_insight_cards")
        body = tk.Frame(main, bg=self._c["bg"])
        body.pack(fill=tk.BOTH, expand=True)
        self._build_detail_panel(body, mode="assets")
        left = tk.Frame(body, bg=self._c["bg"])
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))

        hdr = tk.Frame(left, bg=self._c["bg"])
        hdr.pack(fill=tk.X, pady=(0, 6))
        tk.Label(hdr, text="Активы", bg=self._c["bg"], fg=self._c["fg"], font=self._font_title).pack(side=tk.LEFT)
        self._assets_table_count_var = tk.StringVar(value="0 позиций")
        tk.Label(
            hdr, textvariable=self._assets_table_count_var, bg=self._c["bg"],
            fg=self._c["fg_dim"], font=self._font_small,
        ).pack(side=tk.LEFT, padx=(10, 0), pady=(5, 0))
        more_m = tk.Menu(self.root, **self._skins_dropdown_menu_kw())
        more_m.add_command(label="Вид таблицы…", command=lambda: self.open_table_columns_window("assets"))
        more_m.add_command(label="Удалить выбранные", command=self.delete_selected_assets)
        ttk.Button(hdr, text="Ещё ▾", style="ToolbarMuted.TButton", command=lambda: self._skins_popup_menu_below(
            hdr, more_m)).pack(side=tk.RIGHT)
        tk.Label(
            hdr, text="Двойной клик — открыть · заголовок — сортировка",
            bg=self._c["bg"], fg=self._c["fg_dim"], font=self._font_small,
        ).pack(side=tk.RIGHT, padx=(0, 10), pady=(5, 0))

        filter_row = tk.Frame(left, bg=self._c["bg_card"], padx=10, pady=8,
                              highlightbackground=self._c["border"], highlightthickness=1)
        filter_row.pack(fill=tk.X, pady=(0, 8))
        tk.Label(filter_row, text="Тип:", **self._label_kw(card=True, bold=True)).pack(side=tk.LEFT, padx=(0, 6))
        self.asset_group_filter_var = tk.StringVar(value="Все")
        group_combo = ttk.Combobox(filter_row, textvariable=self.asset_group_filter_var,
                                   values=["Все", "Металлы", "Валюты"], state="readonly", width=12)
        group_combo.pack(side=tk.LEFT, padx=(0, 14))
        group_combo.bind("<<ComboboxSelected>>", lambda _e: self.load_assets_data())
        tk.Label(filter_row, text="Результат:", **self._label_kw(card=True, bold=True)).pack(side=tk.LEFT, padx=(0, 6))
        self.asset_result_filter_var = tk.StringVar(value="Все")
        result_combo = ttk.Combobox(filter_row, textvariable=self.asset_result_filter_var,
                                  values=["Все", "В прибыли", "В просадке"], state="readonly", width=12)
        result_combo.pack(side=tk.LEFT)
        result_combo.bind("<<ComboboxSelected>>", lambda _e: self.load_assets_data())

        table_wrap = tk.Frame(left, bg=self._c["bg_card"], highlightbackground=self._c["border"], highlightthickness=1)
        table_wrap.pack(fill=tk.BOTH, expand=True)
        table_frame = ttk.Frame(table_wrap, style="App.TFrame", padding=4)
        table_frame.pack(fill=tk.BOTH, expand=True)
        assets_columns = [m[0] for m in _ASSETS_TABLE_COLUMNS]
        self.assets_tree = ttk.Treeview(table_frame, columns=assets_columns, show="headings", height=16, selectmode="extended")
        self._assets_table_columns = assets_columns
        for col_id, heading, width in _ASSETS_TABLE_COLUMNS:
            self.assets_tree.heading(
                col_id, text=heading,
                command=lambda c=col_id: self.sort_table(self.assets_tree, "assets", c),
            )
            self.assets_tree.column(col_id, width=width, minwidth=70, stretch=True)
        self._apply_table_column_visibility(self.assets_tree, "assets", assets_columns)
        v_scrollbar = ttk.Scrollbar(
            table_frame, orient=tk.VERTICAL, command=self.assets_tree.yview,
            style="Vertical.TScrollbar",
        )
        h_scrollbar = ttk.Scrollbar(
            table_frame, orient=tk.HORIZONTAL, command=self.assets_tree.xview,
            style="Horizontal.TScrollbar",
        )
        self.assets_tree.configure(yscrollcommand=v_scrollbar.set, xscrollcommand=h_scrollbar.set)
        self.assets_tree.grid(row=0, column=0, sticky="nsew")
        v_scrollbar.grid(row=0, column=1, sticky="ns")
        h_scrollbar.grid(row=1, column=0, sticky="ew")
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)
        self.assets_tree.bind("<<TreeviewSelect>>", self._on_assets_tree_select)
        self.assets_tree.bind("<Button-3>", self.show_assets_context_menu)
        self._configure_interactive_tree(self.assets_tree, "assets")
        self.assets_context_menu = tk.Menu(
            self.root, tearoff=0, bg=self._c["bg_elevated"], fg=self._c["fg"],
            activebackground=self._c["accent"], activeforeground="#0d1117",
            bd=0, font=self._font_ui, relief=tk.FLAT,
        )
        self.assets_context_menu.add_command(label="Редактировать", command=self.open_edit_asset_window)
        self.assets_context_menu.add_command(label="Желаемая цена", command=self.open_set_asset_target_window)
        self.assets_context_menu.add_separator()
        self.assets_context_menu.add_command(label="Вид таблицы…", command=lambda: self.open_table_columns_window("assets"))
        self.assets_context_menu.add_separator()
        self.assets_context_menu.add_command(label="Удалить", command=self.delete_selected_assets)

    def load_assets_data(self):
        if not hasattr(self, "assets_tree"):
            return
        previous = set(self.assets_tree.get_children())
        visible = set()
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, asset_group, code, name, amount, unit, buy_price_usd, buy_price_rub,
                   current_price_usd, current_price_rub, target_sell_price_rub, updated_at
            FROM assets
            ORDER BY asset_group, code
            """
        )
        rows = cursor.fetchall()
        group_filter = getattr(self, "asset_group_filter_var", tk.StringVar(value="Все")).get()
        result_filter = getattr(self, "asset_result_filter_var", tk.StringVar(value="Все")).get()
        total_current_rub = 0.0
        total_current_usd = 0.0
        total_buy_rub = 0.0
        total_buy_usd = 0.0
        needle = self._search_text(getattr(self, "asset_name_search_var", tk.StringVar())).lower()
        visible_index = 0
        previous_iid = None
        for row in rows:
            group_name = row[1]
            if group_filter == "Металлы" and group_name != "METAL":
                continue
            if group_filter == "Валюты" and group_name != "CURRENCY":
                continue
            label = f"{row[3] or ''} {row[2] or ''}".lower()
            if needle and needle not in label:
                continue

            amount = float(row[4] or 0.0)
            buy_usd = float(row[6] or 0.0)
            buy_rub = float(row[7] or 0.0)
            cur_usd = float(row[8] or 0.0)
            cur_rub = float(row[9] or 0.0)
            value_usd = amount * cur_usd
            value_rub = amount * cur_rub
            pnl_rub = value_rub - (amount * buy_rub)
            if result_filter == "В прибыли" and pnl_rub < 0:
                continue
            if result_filter == "В просадке" and pnl_rub >= 0:
                continue

            total_current_usd += value_usd
            total_current_rub += value_rub
            total_buy_usd += amount * buy_usd
            total_buy_rub += amount * buy_rub
            stripe_tag = "row_odd" if visible_index % 2 else "row_even"
            result_tag = "asset_loss" if pnl_rub < 0 else "asset_neutral"
            iid = f"asset:{row[0]}"
            visible.add(iid)
            self._upsert_table_row(
                self.assets_tree, iid, visible_index, values=(
                    row[0], row[1], row[2], row[3] or "-", f"{amount:.6g}", row[5],
                    f"{float(row[6]):.4f}" if row[6] is not None else "-",
                    f"{float(row[7]):.4f}" if row[7] is not None else "-",
                    f"{cur_usd:.6f}" if cur_usd else "-",
                    f"{cur_rub:.6f}" if cur_rub else "-",
                    f"{value_usd:.4f}" if cur_usd else "-",
                    f"{value_rub:.4f}" if cur_rub else "-",
                    self._format_growth_pct(cur_rub, buy_rub),
                    self._format_target_progress(row[10], cur_rub),
                    row[11] or "-",
                ), tags=(stripe_tag, result_tag), previous_iid=previous_iid
            )
            previous_iid = iid
            visible_index += 1
        removed = previous - visible
        if removed:
            self.assets_tree.delete(*removed)
        self.assets_tree.tag_configure(
            "asset_neutral", foreground=self._c["fg_muted"],
        )
        self.assets_tree.tag_configure(
            "asset_loss", foreground=self._c["loss"],
        )
        if hasattr(self, "_assets_table_count_var"):
            count = len(self.assets_tree.get_children(""))
            self._assets_table_count_var.set(f"{count} поз.")
        self._update_assets_stats(total_current_rub, total_buy_rub, total_current_usd, total_buy_usd)
        self._update_sync_footer()
        if self.assets_tree.selection():
            self._on_assets_tree_select()
        else:
            self._clear_asset_detail()

    def show_assets_context_menu(self, event):
        item = self.assets_tree.identify_row(event.y)
        if item:
            if item not in self.assets_tree.selection():
                self.assets_tree.selection_set(item)
            self.assets_context_menu.post(event.x_root, event.y_root)

    def open_set_asset_target_window(self):
        selected = self.assets_tree.selection()
        if not selected:
            messagebox.showinfo("Желаемая цена", "Выберите актив в таблице.")
            return
        if len(selected) != 1:
            messagebox.showinfo("Желаемая цена", "Выберите ровно один актив.")
            return
        values = self.assets_tree.item(selected[0], "values")
        asset_id = values[0] if values else None
        if not asset_id:
            messagebox.showerror("Ошибка", "Не удалось определить ID актива.")
            return

        cursor = self.conn.cursor()
        cursor.execute("SELECT name, code, target_sell_price_rub FROM assets WHERE id = ?", (asset_id,))
        row = cursor.fetchone()
        if not row:
            messagebox.showerror("Ошибка", "Актив не найден.")
            return

        win = tk.Toplevel(self.root)
        win.title("Желаемая цена актива")
        self._style_toplevel(win)
        frame = ttk.Frame(win, style='App.TFrame', padding=16)
        frame.pack(fill=tk.BOTH, expand=True)
        tk.Label(frame, text=f"{row[0] or row[1]}", **self._label_kw(bold=True)).pack(anchor=tk.W, pady=(0, 8))
        tk.Label(frame, text="Желаемая цена продажи за 1 единицу, ₽", **self._label_kw()).pack(anchor=tk.W)
        target_var = tk.StringVar(value=f"{float(row[2]):.4f}" if row[2] is not None else "")
        tk.Entry(frame, textvariable=target_var, width=28, **self._entry_kw()).pack(anchor=tk.W, pady=(4, 12))

        def save():
            target = self._parse_optional_positive_float(target_var.get())
            if (target_var.get() or "").strip() and target is None:
                messagebox.showerror("Ошибка", "Цена должна быть положительным числом или пустой.")
                return
            cursor.execute("UPDATE assets SET target_sell_price_rub = ? WHERE id = ?", (target, asset_id))
            self.conn.commit()
            win.destroy()
            self.load_assets_data()

        actions = ttk.Frame(frame, style='App.TFrame')
        actions.pack(fill=tk.X)
        ttk.Button(actions, text="Сохранить", style='Success.TButton', command=save).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(actions, text="Отмена", style='Danger.TButton', command=win.destroy).pack(side=tk.LEFT)
        self._autosize_toplevel(win, min_w=360, min_h=180)

    def _update_assets_stats(self, total_current_rub, total_buy_rub, total_current_usd, total_buy_usd):
        self._compute_and_show_assets_kpis(total_buy_rub, total_current_rub, total_buy_usd, total_current_usd)
        if hasattr(self, "_assets_kpi_cards") and self._assets_kpi_cards:
            now = datetime.now().strftime("%d.%m.%Y %H:%M")
            self._assets_kpi_cards[4]["value"].set(now)
            self._assets_kpi_cards[4]["sub"].set("последнее обновление")

    def _asset_options_by_group(self, group_code):
        if group_code == "METAL":
            return self.ASSET_METAL_OPTIONS
        return self.ASSET_CURRENCY_OPTIONS

    def open_add_asset_window(self):
        self._open_asset_form_window(mode="add")

    def open_edit_asset_window(self):
        selected = self.assets_tree.selection()
        if not selected:
            messagebox.showinfo("Редактирование", "Выберите актив в таблице.")
            return
        values = self.assets_tree.item(selected[0], "values")
        asset_id = values[0] if values else None
        if not asset_id:
            messagebox.showerror("Ошибка", "Не удалось определить ID актива.")
            return
        self._open_asset_form_window(mode="edit", asset_id=int(asset_id))

    def delete_selected_assets(self):
        selected = self.assets_tree.selection()
        if not selected:
            messagebox.showinfo("Удаление", "Выберите хотя бы один актив.")
            return
        ids = []
        for item in selected:
            values = self.assets_tree.item(item, "values")
            if values and values[0]:
                ids.append(int(values[0]))
        if not ids:
            messagebox.showerror("Ошибка", "Не удалось определить ID выбранных активов.")
            return
        if not messagebox.askyesno("Подтверждение", f"Удалить выбранные активы ({len(ids)} шт.)?"):
            return
        cursor = self.conn.cursor()
        cursor.executemany("DELETE FROM assets WHERE id = ?", [(asset_id,) for asset_id in ids])
        self.conn.commit()
        self.load_assets_data()

    def _open_asset_form_window(self, *, mode, asset_id=None):
        edit_data = None
        if mode == "edit":
            cursor = self.conn.cursor()
            cursor.execute(
                """
                SELECT id, asset_group, code, name, amount, unit, buy_price_usd, buy_price_rub,
                       notes, target_sell_price_rub
                FROM assets
                WHERE id = ?
                """,
                (asset_id,),
            )
            edit_data = cursor.fetchone()
            if not edit_data:
                messagebox.showerror("Ошибка", "Запись актива не найдена.")
                return

        win = tk.Toplevel(self.root)
        win.title("Редактировать актив" if mode == "edit" else "Добавить актив")
        self._style_toplevel(win)
        win.grab_set()
        frame = ttk.Frame(win, style='App.TFrame', padding=12)
        frame.pack(fill=tk.BOTH, expand=True)

        group_var = tk.StringVar(value=(edit_data[1] if edit_data else "CURRENCY"))
        asset_display_var = tk.StringVar()
        amount_var = tk.StringVar(value=(f"{float(edit_data[4]):.6g}" if edit_data else "1"))
        unit_var = tk.StringVar(value=(edit_data[5] if edit_data else "unit"))
        buy_usd_var = tk.StringVar(value=(f"{float(edit_data[6]):.6g}" if edit_data and edit_data[6] is not None else ""))
        buy_rub_var = tk.StringVar(value=(f"{float(edit_data[7]):.6g}" if edit_data and edit_data[7] is not None else ""))
        notes_var = tk.StringVar(value=((edit_data[8] or "") if edit_data else ""))
        target_sell_var = tk.StringVar(
            value=(f"{float(edit_data[9]):.6g}" if edit_data and edit_data[9] is not None else "")
        )

        tk.Label(frame, text="Тип актива:", **self._label_kw()).grid(row=0, column=0, sticky='w', padx=(0, 8), pady=5)
        group_combo = ttk.Combobox(
            frame, textvariable=group_var,
            values=["CURRENCY", "METAL"], state="readonly", width=24
        )
        group_combo.grid(row=0, column=1, sticky='w', pady=5)

        tk.Label(frame, text="Что куплено:", **self._label_kw()).grid(row=1, column=0, sticky='w', padx=(0, 8), pady=5)
        asset_combo = ttk.Combobox(frame, textvariable=asset_display_var, state="readonly", width=24)
        asset_combo.grid(row=1, column=1, sticky='w', pady=5)

        tk.Label(frame, text="Количество:", **self._label_kw()).grid(row=2, column=0, sticky='w', padx=(0, 8), pady=5)
        tk.Entry(frame, textvariable=amount_var, width=26, **self._entry_kw()).grid(row=2, column=1, sticky='w', pady=5)
        tk.Label(frame, text="Единица:", **self._label_kw()).grid(row=3, column=0, sticky='w', padx=(0, 8), pady=5)
        tk.Entry(frame, textvariable=unit_var, width=26, **self._entry_kw()).grid(row=3, column=1, sticky='w', pady=5)

        tk.Label(frame, text="Курс покупки за 1 единицу (₽):", **self._label_kw()).grid(
            row=4, column=0, sticky='w', padx=(0, 8), pady=5
        )
        tk.Entry(frame, textvariable=buy_rub_var, width=26, **self._entry_kw()).grid(row=4, column=1, sticky='w', pady=5)
        tk.Label(frame, text="Эквивалент за 1 единицу ($, опц.):", **self._label_kw()).grid(
            row=5, column=0, sticky='w', padx=(0, 8), pady=5
        )
        tk.Entry(frame, textvariable=buy_usd_var, width=26, **self._entry_kw()).grid(row=5, column=1, sticky='w', pady=5)
        tk.Label(frame, text="Желаемая цена продажи за 1 единицу (₽):", **self._label_kw()).grid(
            row=6, column=0, sticky='w', padx=(0, 8), pady=5
        )
        tk.Entry(frame, textvariable=target_sell_var, width=26, **self._entry_kw()).grid(
            row=6, column=1, sticky='w', pady=5
        )
        tk.Label(frame, text="Комментарий:", **self._label_kw()).grid(row=7, column=0, sticky='w', padx=(0, 8), pady=5)
        tk.Entry(frame, textvariable=notes_var, width=26, **self._entry_kw()).grid(row=7, column=1, sticky='w', pady=5)

        def _sync_options(*_args):
            options = self._asset_options_by_group(group_var.get())
            display_values = [label for _code, label in options]
            asset_combo.configure(values=display_values)
            current = asset_display_var.get()
            if current not in display_values:
                asset_display_var.set(display_values[0] if display_values else "")
            if group_var.get() == "METAL" and not unit_var.get().strip():
                unit_var.set("g")
            if group_var.get() == "CURRENCY" and not unit_var.get().strip():
                unit_var.set("unit")

        def _display_for_code(code):
            for opt_code, label in self._asset_options_by_group(group_var.get()):
                if opt_code == code:
                    return label
            return ""

        if edit_data:
            asset_display_var.set(_display_for_code(edit_data[2]))
        _sync_options()
        group_var.trace_add("write", _sync_options)

        def _save():
            selected_display = asset_display_var.get()
            code = None
            name = None
            for opt_code, label in self._asset_options_by_group(group_var.get()):
                if label == selected_display:
                    code = opt_code
                    name = label.split(" (", 1)[0]
                    break
            if not code:
                messagebox.showerror("Ошибка", "Выберите актив из списка.")
                return
            try:
                amount = float((amount_var.get() or "").replace(",", "."))
                if amount <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Ошибка", "Количество должно быть положительным числом.")
                return
            buy_rub = self._parse_positive_float(buy_rub_var.get())
            if (buy_rub_var.get() or "").strip() and buy_rub is None:
                messagebox.showerror("Ошибка", "Курс в ₽ должен быть положительным числом.")
                return
            buy_usd = self._parse_positive_float(buy_usd_var.get())
            if (buy_usd_var.get() or "").strip() and buy_usd is None:
                messagebox.showerror("Ошибка", "Цена в $ должна быть положительным числом.")
                return
            target_sell = self._parse_optional_positive_float(target_sell_var.get())
            if (target_sell_var.get() or "").strip() and target_sell is None:
                messagebox.showerror("Ошибка", "Желаемая цена продажи должна быть положительным числом.")
                return

            cursor = self.conn.cursor()
            payload = (
                group_var.get(),
                code,
                name,
                amount,
                (unit_var.get() or "").strip() or ("g" if group_var.get() == "METAL" else "unit"),
                buy_usd,
                buy_rub,
                target_sell,
                (notes_var.get() or "").strip(),
            )
            if mode == "edit":
                cursor.execute(
                    """
                    UPDATE assets
                    SET asset_group = ?, code = ?, name = ?, amount = ?, unit = ?,
                        buy_price_usd = ?, buy_price_rub = ?, target_sell_price_rub = ?, notes = ?
                    WHERE id = ?
                    """,
                    payload + (asset_id,),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO assets (
                        asset_group, code, name, amount, unit, buy_price_usd, buy_price_rub,
                        target_sell_price_rub, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    payload,
                )
            self.conn.commit()
            win.destroy()
            self.load_assets_data()

        actions = ttk.Frame(frame, style='App.TFrame')
        actions.grid(row=8, column=0, columnspan=2, sticky='e', pady=(10, 0))
        ttk.Button(actions, text="Сохранить", style='Success.TButton', command=_save).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(actions, text="Отмена", style='Danger.TButton', command=win.destroy).pack(side=tk.LEFT)
        self._autosize_toplevel(win, extra_w=35, extra_h=30, min_w=480, min_h=320)

    def _fetch_assets_api_prices(self, pairs):
        usd_rub, _ = self.fetch_usd_rub_rate()
        prices = {}
        if not pairs:
            return prices

        currency_codes = sorted({code for grp, code in pairs if grp == "CURRENCY"})
        metal_codes = sorted({code for grp, code in pairs if grp == "METAL"})

        if currency_codes:
            rub_by_code = {}
            response = self._http.get("https://www.cbr-xml-daily.ru/daily_json.js", timeout=14)
            response.raise_for_status()
            valutes = response.json().get("Valute", {})
            for code in currency_codes:
                if code in ("USD", "RUB"):
                    continue
                entry = valutes.get(code, {})
                value = entry.get("Value")
                if value:
                    rub_by_code[code] = float(value) / float(entry.get("Nominal") or 1)
            for code in currency_codes:
                if code == "USD":
                    prices[("CURRENCY", code)] = (1.0, usd_rub)
                    continue
                if code == "RUB":
                    prices[("CURRENCY", code)] = (1.0 / usd_rub, 1.0)
                    continue
                rub_per_unit = rub_by_code.get(code)
                if rub_per_unit:
                    usd_per_unit = rub_per_unit / usd_rub
                    prices[("CURRENCY", code)] = (usd_per_unit, rub_per_unit)

        if metal_codes:
            metal_map = {"XAU": "1", "XAG": "2", "XPT": "3", "XPD": "4"}
            today = datetime.now().strftime("%d/%m/%Y")
            response = self._http.get(
                "https://www.cbr.ru/scripts/xml_metall.asp",
                params={"date_req1": (datetime.now() - timedelta(days=10)).strftime("%d/%m/%Y"), "date_req2": today},
                timeout=14,
            )
            response.raise_for_status()
            rub_by_code = {}
            xml_text = response.text or ""
            start = xml_text.find("<Metall")
            end = xml_text.rfind("</Metall>")
            parsed_with_et = False
            if start != -1 and end != -1 and end > start:
                xml_block = xml_text[start:end + len("</Metall>")]
                try:
                    root = ET.fromstring(xml_block)
                    for rec in root.findall(".//Record"):
                        code_num = rec.attrib.get("Code")
                        buy_val = (rec.findtext("Buy") or "").replace(",", ".").strip()
                        for alias, num in metal_map.items():
                            if code_num == num:
                                try:
                                    rub_by_code[alias] = float(buy_val)
                                except ValueError:
                                    pass
                    parsed_with_et = True
                except ET.ParseError:
                    parsed_with_et = False
            if not parsed_with_et:
                records = re.findall(
                    r'<Record[^>]*Code="(\d+)"[^>]*>.*?<Buy>\s*([^<]+)\s*</Buy>.*?</Record>',
                    xml_text,
                    flags=re.IGNORECASE | re.DOTALL,
                )
                for code_num, buy_val in records:
                    for alias, num in metal_map.items():
                        if code_num == num:
                            val = (buy_val or "").replace(",", ".").strip()
                            try:
                                rub_by_code[alias] = float(val)
                            except ValueError:
                                pass
            for code in metal_codes:
                rub_val = rub_by_code.get(code)
                if rub_val:
                    prices[("METAL", code)] = (rub_val / usd_rub, rub_val)
        return prices

    def update_assets_prices(self, silent=False):
        pairs = self.conn.execute("SELECT DISTINCT asset_group, code FROM assets").fetchall()
        if not pairs:
            return
        button = self._top_refresh_btn

        def loaded(prices):
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with self.conn:
                self.conn.executemany(
                    """UPDATE assets SET current_price_usd = ?, current_price_rub = ?, updated_at = ?
                       WHERE asset_group = ? AND code = ?""",
                    [(usd, rub, now, group, code) for (group, code), (usd, rub) in prices.items()],
                )
            self.load_assets_data()
            if not silent:
                messagebox.showinfo("Готово", "Цены активов обновлены по API.")

        def finished():
            button.state(["!disabled"])
            button.configure(text="Обновить")

        if self._tasks.submit("asset-prices", lambda: self._fetch_assets_api_prices(pairs), loaded,
                              lambda exc: messagebox.showerror("Ошибка", f"Не удалось обновить цены активов: {type(exc).__name__}") if not silent else None,
                              finished):
            if self.current_profile == "assets":
                button.state(["disabled"])
                button.configure(text="Обновление…")

    def create_widgets(self):
        self.start_date_var = tk.StringVar(value="01.01.2020")
        self.end_date_var = tk.StringVar(value=datetime.now().strftime("%d.%m.%Y"))
        self.buy_order_prices, self.sell_order_prices = self._restore_market_prices()
        self._detail_chart_canvas = None
        self._active_nav = "overview"

        self._build_app_shell()
        self.skins_main_frame = ttk.Frame(self._content_stack, style="App.TFrame")
        self.assets_main_frame = ttk.Frame(self._content_stack, style="App.TFrame")
        self._main_content = self.skins_main_frame

        main = self.skins_main_frame
        self._build_kpi_row(
            main,
            ("Затраты на инвентарь", "Текущая стоимость", "Ожидаемый профит",
             "Зафиксировано", "Доступно к продаже", "Ждёт разблокировки"),
            "_skins_kpi_cards",
        )
        self._build_insight_row(main, "_skins_insight_cards")
        body = tk.Frame(main, bg=self._c["bg"])
        body.pack(fill=tk.BOTH, expand=True)
        self._build_detail_panel(body, mode="skins")
        left = tk.Frame(body, bg=self._c["bg"])
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))

        hdr = tk.Frame(left, bg=self._c["bg"])
        hdr.pack(fill=tk.X, pady=(0, 6))
        tk.Label(hdr, text="Позиции", bg=self._c["bg"], fg=self._c["fg"], font=self._font_title).pack(side=tk.LEFT)
        self._skins_table_count_var = tk.StringVar(value="0 позиций")
        tk.Label(
            hdr, textvariable=self._skins_table_count_var, bg=self._c["bg"],
            fg=self._c["fg_dim"], font=self._font_small,
        ).pack(side=tk.LEFT, padx=(10, 0), pady=(5, 0))
        self._skins_more_menu = tk.Menu(self.root, **self._skins_dropdown_menu_kw())
        self._skins_more_menu.add_command(label="Экспорт JSON", command=self.export_skins)
        self._skins_more_menu.add_command(label="Экспорт выбранных", command=self.export_selected_skins)
        self._skins_more_menu.add_command(label="Импорт JSON", command=self.import_skins)
        self._skins_more_menu.add_separator()
        self._skins_more_menu.add_command(label="Вид таблицы…", command=lambda: self.open_table_columns_window("skins"))
        self._skins_more_menu.add_separator()
        self._skins_more_menu.add_command(label="Удалить выбранные…", command=self.delete_selected_items)
        self._skins_more_btn = ttk.Button(hdr, text="Ещё ▾", style="ToolbarMuted.TButton",
                                          command=lambda: self._skins_popup_menu_below(self._skins_more_btn, self._skins_more_menu))
        self._skins_more_btn.pack(side=tk.RIGHT)
        tk.Label(
            hdr, text="Двойной клик — открыть · заголовок — сортировка",
            bg=self._c["bg"], fg=self._c["fg_dim"], font=self._font_small,
        ).pack(side=tk.RIGHT, padx=(0, 10), pady=(5, 0))

        filter_row = tk.Frame(left, bg=self._c["bg_card"], padx=10, pady=8,
                              highlightbackground=self._c["border"], highlightthickness=1)
        filter_row.pack(fill=tk.X, pady=(0, 8))
        tk.Label(filter_row, text="Статус:", **self._label_kw(card=True, bold=True)).pack(side=tk.LEFT, padx=(0, 6))
        self.status_filter_var = tk.StringVar(value="Все")
        status_combo = ttk.Combobox(
            filter_row, textvariable=self.status_filter_var,
            values=["Все", "Ждет разблокировки", "Доступен для продажи", "Продан"],
            state="readonly", width=18,
        )
        status_combo.pack(side=tk.LEFT, padx=(0, 12))
        status_combo.bind("<<ComboboxSelected>>", lambda _e: self.load_data())
        tk.Label(filter_row, text="Категория:", **self._label_kw(card=True, bold=True)).pack(side=tk.LEFT, padx=(0, 6))
        self.category_filter_var = tk.StringVar(value="Все")
        cat_combo = ttk.Combobox(filter_row, textvariable=self.category_filter_var,
                                 values=list(CATEGORY_FILTER_LABELS), state="readonly", width=14)
        cat_combo.pack(side=tk.LEFT, padx=(0, 12))
        cat_combo.bind("<<ComboboxSelected>>", lambda _e: self.load_data())
        tk.Label(filter_row, text="Order Sell:", **self._label_kw(card=True, bold=True)).pack(side=tk.LEFT, padx=(0, 6))
        self.order_sell_profit_filter_var = tk.StringVar(value="Все")
        osp_combo = ttk.Combobox(filter_row, textvariable=self.order_sell_profit_filter_var,
                                 values=["Все", "В плюс", "В минус"], state="readonly", width=10)
        osp_combo.pack(side=tk.LEFT)
        osp_combo.bind("<<ComboboxSelected>>", lambda _e: self.load_data())
        self.name_search_var.trace_add("write", lambda *_: self._tasks.debounce("skin-search", 180, self.load_data))

        table_wrap = tk.Frame(left, bg=self._c["bg_card"], highlightbackground=self._c["border"], highlightthickness=1)
        table_wrap.pack(fill=tk.BOTH, expand=True)
        table_frame = ttk.Frame(table_wrap, style="App.TFrame", padding=4)
        table_frame.pack(fill=tk.BOTH, expand=True)
        skins_columns = [m[0] for m in _SKINS_TABLE_COLUMNS]
        self.tree = ttk.Treeview(table_frame, columns=skins_columns, show="headings", height=16, selectmode="extended")
        self._skins_table_columns = skins_columns
        for col_id, heading, width in _SKINS_TABLE_COLUMNS:
            self.tree.heading(
                col_id, text=heading,
                command=lambda c=col_id: self.sort_table(self.tree, "skins", c),
            )
            self.tree.column(col_id, width=width, minwidth=70, stretch=True)
        self._apply_table_column_visibility(self.tree, "skins", skins_columns)
        v_scrollbar = ttk.Scrollbar(
            table_frame, orient=tk.VERTICAL, command=self.tree.yview,
            style="Vertical.TScrollbar",
        )
        h_scrollbar = ttk.Scrollbar(
            table_frame, orient=tk.HORIZONTAL, command=self.tree.xview,
            style="Horizontal.TScrollbar",
        )
        self.tree.configure(yscrollcommand=v_scrollbar.set, xscrollcommand=h_scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        v_scrollbar.grid(row=0, column=1, sticky="ns")
        h_scrollbar.grid(row=1, column=0, sticky="ew")
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self._on_skins_tree_select)
        self.tree.bind("<Button-3>", self.show_context_menu)
        self._configure_interactive_tree(self.tree, "skins")
        self.context_menu = tk.Menu(
            self.root, tearoff=0, bg=self._c["bg_elevated"], fg=self._c["fg"],
            activebackground=self._c["accent"], activeforeground="#0d1117",
            bd=0, font=self._font_ui, relief=tk.FLAT,
        )
        self.context_menu.add_command(label="Продать", command=self.open_sell_window)
        self.context_menu.add_command(label="Редактировать", command=self.open_edit_window)
        self.context_menu.add_command(label="Желаемая цена", command=self.open_set_skin_target_window)
        self.context_menu.add_command(label="Загрузить историю", command=self.load_single_item_history)
        self.context_menu.add_command(label="Открыть на Lis", command=self.open_lis_skin_page)
        self.context_menu.add_command(label="Витрина: выставить", command=self.open_cs_market_list_window)
        self.context_menu.add_command(label="Витрина: цена", command=self.open_cs_market_set_price_window)
        self.context_menu.add_command(label="Витрина: снять", command=self.open_cs_market_remove_window)
        self.context_menu.add_command(label="Удалить выбранные", command=self.delete_selected_items)

        self.skins_refresh_button = self._top_refresh_btn
        self.skins_graph_button = self._top_refresh_btn

        self._build_assets_dashboard(self.assets_main_frame)
        self.assets_main_frame.pack_forget()
        self.skins_main_frame.pack(fill=tk.BOTH, expand=True)
        self.current_profile = "skins"

    def _restore_market_prices(self):
        buy, sell = {}, {}
        rows = self.conn.execute(
            """SELECT h.skin_name, h.buy_order_gross_rub, h.sell_order_gross_rub
               FROM item_price_history h
               JOIN (SELECT skin_name, MAX(id) AS id FROM item_price_history
                     WHERE buy_order_gross_rub IS NOT NULL OR sell_order_gross_rub IS NOT NULL
                     GROUP BY skin_name) latest ON latest.id = h.id"""
        ).fetchall()
        for name, buy_price, sell_price in rows:
            if buy_price is not None:
                buy[name] = float(buy_price)
            if sell_price is not None:
                sell[name] = float(sell_price)
        return buy, sell

    def create_summary_widgets(self):
        """Совместимость: статистика теперь в KPI-карточках."""
        pass

    def refresh_all(self):
        b = getattr(self, "skins_refresh_button", None) or getattr(self, "_top_refresh_btn", None)
        self._skins_toolbar_btn_busy(
            b, True, idle_text="Обновить", busy_text="Обновление…"
        )
        try:
            self.load_data()
            self._refresh_usd_rate_footer()
        finally:
            self._skins_toolbar_btn_busy(b, False, idle_text="Обновить")

    def show_context_menu(self, event):
        item = self.tree.identify_row(event.y)
        if item:
            if item not in self.tree.selection():
                self.tree.selection_set(item)
            values = self.tree.item(item, "values")
            cols = getattr(self, "_skins_table_columns", [m[0] for m in _SKINS_TABLE_COLUMNS])
            data = {cols[i]: values[i] if i < len(values) else "" for i in range(len(cols))}
            is_sold = str(data.get("status", "")).strip() == "Продан"
            for label in ("Продать", "Витрина: выставить", "Витрина: цена", "Витрина: снять"):
                try:
                    self.context_menu.entryconfigure(label, state=(tk.DISABLED if is_sold else tk.NORMAL))
                except tk.TclError:
                    pass
            self.context_menu.post(event.x_root, event.y_root)

    def open_set_skin_target_window(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("Желаемая цена", "Выберите скин в таблице.")
            return
        if len(selected) != 1:
            messagebox.showinfo("Желаемая цена", "Выберите ровно одну позицию.")
            return
        values = self.tree.item(selected[0], "values")
        skin_id = values[0] if values else None
        if not skin_id:
            messagebox.showerror("Ошибка", "Не удалось определить ID скина.")
            return

        cursor = self.conn.cursor()
        cursor.execute("SELECT name, target_sell_price_rub FROM skins WHERE id = ?", (skin_id,))
        row = cursor.fetchone()
        if not row:
            messagebox.showerror("Ошибка", "Скин не найден.")
            return

        win = tk.Toplevel(self.root)
        win.title("Желаемая цена скина")
        self._style_toplevel(win)
        frame = ttk.Frame(win, style='App.TFrame', padding=16)
        frame.pack(fill=tk.BOTH, expand=True)
        tk.Label(frame, text=row[0], **self._label_kw(bold=True)).pack(anchor=tk.W, pady=(0, 8))
        tk.Label(frame, text="Желаемая цена продажи за 1 штуку, ₽", **self._label_kw()).pack(anchor=tk.W)
        target_var = tk.StringVar(value=f"{float(row[1]):.2f}" if row[1] is not None else "")
        tk.Entry(frame, textvariable=target_var, width=28, **self._entry_kw()).pack(anchor=tk.W, pady=(4, 12))

        def save():
            target = self._parse_optional_positive_float(target_var.get())
            if (target_var.get() or "").strip() and target is None:
                messagebox.showerror("Ошибка", "Цена должна быть положительным числом или пустой.")
                return
            cursor.execute("UPDATE skins SET target_sell_price_rub = ? WHERE id = ?", (target, skin_id))
            self.conn.commit()
            win.destroy()
            self.load_data()

        actions = ttk.Frame(frame, style='App.TFrame')
        actions.pack(fill=tk.X)
        ttk.Button(actions, text="Сохранить", style='Success.TButton', command=save).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(actions, text="Отмена", style='Danger.TButton', command=win.destroy).pack(side=tk.LEFT)
        self._autosize_toplevel(win, min_w=360, min_h=180)

    def sort_column(self, col):
        """Обратная совместимость со старым обработчиком сортировки."""
        self.sort_table(self.tree, "skins", col)

    def get_column_index(self, col_name):
        """Получение индекса колонки по имени"""
        columns = getattr(self, "_skins_table_columns", [m[0] for m in _SKINS_TABLE_COLUMNS])
        return columns.index(col_name)

    def on_history_click(self, event):
        """Обработчик двойного клика по колонке History"""
        item = self.tree.identify_row(event.y)
        col_id = self._tree_column_id_at_event(self.tree, event.x)
        if item and col_id == "history":
            self.tree.selection_set(item)
            self.show_history_graph()

    def show_history_graph(self):
        """Отображение графика истории продаж"""
        selected = self.tree.selection()
        if not selected:
            return

        item = self.tree.item(selected[0])
        values = item['values']
        skin_name = self._selected_skin_original_name(values)

        # Получаем историю продаж из словаря (загружена при обновлении цен)
        history_data = self.sales_history.get(skin_name, [])
        if not history_data:
            messagebox.showwarning("Предупреждение", f"Нет данных истории продаж для {skin_name}")
            return

        # Создаем новое окно для графика
        graph_window = tk.Toplevel(self.root)
        graph_window.title(f"История продаж: {skin_name}")
        graph_window.geometry("800x600")
        graph_window.minsize(520, 360)
        self._style_toplevel(graph_window)

        _lbl = '#e8edf4'
        _line = '#ffc857'
        _grid = '#5a6a7d'

        # Создаем фигуру matplotlib
        fig, ax = plt.subplots(figsize=(10, 6))
        fig.patch.set_facecolor(self._c['bg_card'])
        ax.set_facecolor(self._c['entry_bg'])
        ax.tick_params(colors=_lbl, labelsize=10)
        ax.spines['bottom'].set_color(_grid)
        ax.spines['top'].set_color(_grid)
        ax.spines['left'].set_color(_grid)
        ax.spines['right'].set_color(_grid)
        ax.xaxis.label.set_color(_lbl)
        ax.yaxis.label.set_color(_lbl)
        ax.title.set_color('#ffffff')

        # Подготавливаем данные для графика
        # Берем последние 50 сделок (или все, если меньше)
        recent_history = history_data[:50]
        # Переворачиваем массив, чтобы последние сделки были справа
        recent_history.reverse()
        # Создаем массивы для осей
        x_values = list(range(1, len(recent_history) + 1))
        y_values = [deal[1] for deal in recent_history]  # Цены сделок

        # Строим график (контрастные цвета на тёмном фоне)
        line, = ax.plot(
            x_values, y_values, 'o-', markersize=6, linewidth=2.2,
            color=_line, markerfacecolor='#ff9f1c',
            markeredgecolor='#fff8e7', markeredgewidth=0.8)
        ax.set_xlabel('Номер сделки (1 — самая старая из отображаемых)')
        ax.set_ylabel('Цена (RUB)')
        ax.set_title(f'История продаж: {skin_name}')
        ax.grid(True, alpha=0.45, color=_grid, linestyle='--')

        # Поворачиваем подписи оси X для лучшей читаемости
        plt.setp(ax.get_xticklabels(), rotation=45, ha="right")

        # Встраиваем график в tkinter
        canvas = FigureCanvasTkAgg(fig, master=graph_window)
        canvas.draw()
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        canvas.get_tk_widget().configure(cursor="crosshair")
        point_labels = [self._format_chart_timestamp(deal[0]) for deal in recent_history]
        self._attach_chart_interactions(
            fig,
            ax,
            [{
                "line": line,
                "label": skin_name,
                "point_labels": point_labels,
                "color": _line,
            }],
        )
        self._add_chart_toolbar(canvas, graph_window)

        btn_bar = ttk.Frame(graph_window, style='App.TFrame', padding=(0, 0, 0, 10))
        btn_bar.pack(fill=tk.X)
        ttk.Button(btn_bar, text="Закрыть", style='Primary.TButton',
                   command=graph_window.destroy).pack()
        self._autosize_toplevel(graph_window, min_w=520, min_h=360)

    def get_sales_history(self, item_name, *, api_key):
        response = self._http.get(
            f"{MARKET_API_BASE}/get-list-items-info",
            params={"key": api_key, "list_hash_name[]": item_name}, timeout=(5, 20),
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("success"):
            raise ValueError("Маркет не вернул историю. Проверьте API-ключ в настройках.")
        history = (data.get("data") or {}).get(item_name, {}).get("history", [])
        return sorted(history, key=lambda row: row[0], reverse=True)

    def _cs_market_api_key_ok(self):
        k = self._get_market_api_key()
        if not k or k == "YOUR_API_KEY_HERE":
            messagebox.showerror(
                "CS Market",
                "Укажите API-ключ: «Настройки» → блок «API-ключ market.csgo.com», "
                "либо переменная окружения CSMARKET_API_KEY, либо cstracker/config.py.",
            )
            return False
        return True

    def _cs_market_get(self, method, *, api_key, **params):
        q = {"key": api_key, **params}
        r = self._http.get(f"{MARKET_API_BASE}/{method}", params=q, timeout=(5, 20))
        r.raise_for_status()
        return r.json()

    @staticmethod
    def _price_to_market_api_int(amount, currency):
        """Цена в API: RUB ×100 (копейки), USD/EUR ×1000."""
        x = float(amount)
        if currency == "RUB":
            return max(1, int(round(x * 100)))
        return max(1, int(round(x * 1000)))

    @staticmethod
    def _market_api_int_to_display(price_int, currency):
        if price_int is None:
            return 0.0
        p = int(price_int)
        if currency == "RUB":
            return p / 100.0
        return p / 1000.0

    def _cs_market_skin_row(self, skin_id):
        cur = self.conn.cursor()
        cur.execute(
            "SELECT id, name, market_asset_id, market_item_id FROM skins WHERE id = ?",
            (skin_id,),
        )
        return cur.fetchone()

    def open_cs_market_list_window(self):
        MarketActions(self).open_list()

    def open_cs_market_remove_window(self):
        MarketActions(self).open_remove()

    def open_cs_market_set_price_window(self):
        MarketActions(self).open_set_price()

    def open_add_window(self):
        self.add_window = tk.Toplevel(self.root)
        self.add_window.title("Добавить покупку")
        self.add_window.geometry("440x760")
        self.add_window.resizable(True, True)
        self.add_window.minsize(400, 580)
        self._style_toplevel(self.add_window)

        pad = {'padx': 20, 'pady': 3}

        # Переменные
        self.name_var = tk.StringVar()
        self.quantity_var = tk.StringVar(value="1")
        self.price_var = tk.StringVar()
        self.price_unit_var = tk.StringVar(value="per_item") # Новая переменная для единицы измерения
        self.buy_currency_var = tk.StringVar(value="RUB")
        self._usd_rate_cache = None
        self.commission_var = tk.BooleanVar(value=True)
        self.buy_date_var = tk.StringVar(value=datetime.now().strftime("%d.%m.%Y"))
        self.days_var = tk.StringVar(value="7")

        tk.Label(self.add_window, text="Новая покупка", **self._label_kw(accent=True, bold=True)).pack(anchor=tk.W, **pad)

        tk.Label(self.add_window, text="Название скина", **self._label_kw()).pack(anchor=tk.W, **pad)
        tk.Entry(self.add_window, textvariable=self.name_var, width=42, **self._entry_kw()).pack(**pad)

        tk.Label(self.add_window, text="Количество", **self._label_kw()).pack(anchor=tk.W, **pad)
        tk.Entry(self.add_window, textvariable=self.quantity_var, width=42, **self._entry_kw()).pack(**pad)

        tk.Label(self.add_window, text="Валюта цены покупки", **self._label_kw()).pack(anchor=tk.W, **pad)
        buy_cur = ttk.Combobox(self.add_window, textvariable=self.buy_currency_var,
                               values=["RUB", "USD"], state="readonly", width=39)
        buy_cur.pack(**pad)
        buy_cur.bind('<<ComboboxSelected>>', self._on_buy_currency_changed)

        rate_row = tk.Frame(self.add_window, bg=self._c['bg'])
        rate_row.pack(fill=tk.X, **pad)
        ttk.Button(rate_row, text="Обновить курс USD → RUB", style='Primary.TButton',
                   command=self.refresh_buy_usd_rate_for_add).pack(side=tk.LEFT, padx=(0, 8))
        self.add_usd_rate_label = tk.Label(
            rate_row, text="Для USD нажмите кнопку — курс зафиксируется при сохранении",
            **self._label_kw())
        self.add_usd_rate_label.pack(side=tk.LEFT, anchor=tk.W)

        self.manual_usd_rate_var = tk.StringVar()
        tk.Label(
            self.add_window,
            text="Курс ₽/$ вручную (если не считаем из двух сумм ниже и не жмём «Обновить курс»)",
            **self._label_kw()).pack(anchor=tk.W, **pad)
        tk.Entry(self.add_window, textvariable=self.manual_usd_rate_var, width=42, **self._entry_kw()).pack(**pad)
        self.manual_usd_rate_var.trace_add('write', lambda *_: self.on_price_change())

        tk.Label(self.add_window, text="Цена (в выбранной валюте)", **self._label_kw()).pack(anchor=tk.W, **pad)
        tk.Entry(self.add_window, textvariable=self.price_var, width=42, **self._entry_kw()).pack(**pad)

        self.target_sell_price_var = tk.StringVar()
        tk.Label(self.add_window, text="Желаемая цена продажи за 1 штуку, ₽ (опц.)", **self._label_kw()).pack(anchor=tk.W, **pad)
        tk.Entry(self.add_window, textvariable=self.target_sell_price_var, width=42, **self._entry_kw()).pack(**pad)

        tk.Label(self.add_window, text="Цена указана за", **self._label_kw()).pack(anchor=tk.W, **pad)
        price_unit_combo = ttk.Combobox(self.add_window, textvariable=self.price_unit_var,
                                        values=["1 штуку", "все штуки"], state="readonly", width=39)
        price_unit_combo.pack(**pad)

        tk.Checkbutton(self.add_window, text="Учитывать комиссию Lis (2%)",
                       variable=self.commission_var, **self._chk_kw()).pack(anchor=tk.W, **pad)

        self.buy_rub_equiv_total_var = tk.StringVar()
        tk.Label(
            self.add_window,
            text="Или итого в ₽ за эту покупку (та же сделка, что сумма в $ выше; до Lis 2%) — курс = ₽ ÷ $",
            **self._label_kw()).pack(anchor=tk.W, **pad)
        tk.Entry(self.add_window, textvariable=self.buy_rub_equiv_total_var, width=42, **self._entry_kw()).pack(**pad)
        self.buy_rub_equiv_total_var.trace_add('write', lambda *_: self.on_price_change())

        tk.Label(self.add_window, text="Дата покупки (дд.мм.гггг)", **self._label_kw()).pack(anchor=tk.W, **pad)
        tk.Entry(self.add_window, textvariable=self.buy_date_var, width=42, **self._entry_kw()).pack(**pad)

        tk.Label(self.add_window, text="Дней до разблокировки", **self._label_kw()).pack(anchor=tk.W, **pad)
        tk.Entry(self.add_window, textvariable=self.days_var, width=42, **self._entry_kw()).pack(**pad)

        self.sell_info_label = tk.Label(
            self.add_window, text="", justify=tk.LEFT, anchor=tk.W,
            bg=self._c['bg'], fg=self._c['info'], font=self._font_small, wraplength=380)
        self.sell_info_label.pack(fill=tk.BOTH, expand=True, pady=(12, 8), padx=20)

        def _sync_add_wrap(_event=None):
            try:
                w = max(self.add_window.winfo_width() - 48, 220)
                self.sell_info_label.config(wraplength=w)
            except tk.TclError:
                pass

        self.add_window.bind("<Configure>", lambda e: _sync_add_wrap())

        button_frame = tk.Frame(self.add_window, bg=self._c['bg'])
        button_frame.pack(pady=16)
        ttk.Button(button_frame, text="Рассчитать", style='Accent.TButton',
                   command=self.calculate_sell_price).pack(side=tk.LEFT, padx=4)
        ttk.Button(button_frame, text="Сохранить", style='Success.TButton',
                   command=self.save_purchase).pack(side=tk.LEFT, padx=4)
        ttk.Button(button_frame, text="Отмена", style='Danger.TButton',
                   command=self.add_window.destroy).pack(side=tk.LEFT, padx=4)

        # Обновление информации при изменении цены или количества
        self.price_var.trace('w', self.on_price_change)
        self.quantity_var.trace('w', self.on_price_change)
        self.commission_var.trace('w', self.on_price_change)
        self.price_unit_var.trace('w', self.on_price_change) # Добавляем отслеживание для unit
        self._autosize_toplevel(self.add_window, min_w=400, min_h=580)

    def _on_buy_currency_changed(self, event=None):
        if self.buy_currency_var.get() == "USD":
            self.add_usd_rate_label.config(
                text="Курс: кнопка API, поле вручную или итого в ₽ ÷ сумма в $ — см. поля ниже")
            self._usd_rate_cache = None
        else:
            self.add_usd_rate_label.config(text="")
            self._usd_rate_cache = None
        self.calculate_sell_price()

    def refresh_buy_usd_rate_for_add(self):
        if self.buy_currency_var.get() != "USD":
            messagebox.showinfo("Курс", "Выберите валюту USD для покупки.")
            return
        window, label = self.add_window, self.add_usd_rate_label

        def loaded(result):
            if window.winfo_exists():
                rate, source = result
                self._usd_rate_cache = rate
                label.config(text=f"{rate:.4f} ₽/$ ({source})")
                self.calculate_sell_price()

        label.config(text="Загрузка курса…")
        self._request_usd_rate(loaded, lambda exc: messagebox.showerror("Курс", str(exc)) if window.winfo_exists() else None, force=True)

    def on_price_change(self, *args):
        self.calculate_sell_price()

    def calculate_sell_price(self):
        try:
            quantity = int(self.quantity_var.get() or 0)
            price_input = float(self.price_var.get() or 0)
            with_commission = self.commission_var.get()
            price_unit = self.price_unit_var.get() # Получаем выбранную единицу
            currency = self.buy_currency_var.get()

            if quantity <= 0 or price_input <= 0:
                self.sell_info_label.config(text="Введите корректные данные", fg=self._c['warn'])
                return

            if price_unit == "per_item" or price_unit == "1 штуку":
                unit_in_cur = price_input
                total_raw_in_cur = quantity * unit_in_cur
            elif price_unit == "total" or price_unit == "все штуки":
                total_raw_in_cur = price_input
                unit_in_cur = total_raw_in_cur / quantity
            else:
                self.sell_info_label.config(text="Неверная единица измерения цены", fg=self._c['warn'])
                return

            if currency == "USD":
                rate, src_note = self._buy_usd_rub_rate_preview(total_raw_in_cur)
                if rate is None:
                    self.sell_info_label.config(
                        text="Валюта USD: укажите итого в ₽ (та же сделка), или курс вручную, или «Обновить курс».",
                        fg=self._c['warn'])
                    return
                total_buy_raw_rub = total_raw_in_cur * rate
                buy_price_per_item_rub = unit_in_cur * rate
                extra_usd = (
                    f"\nВ USD: {unit_in_cur:.2f} $/шт, всего {total_raw_in_cur:.2f} $ (до Lis)\n"
                    f"Курс предпросмотра: {rate:.4f} ₽/$ ({src_note})"
                )
            else:
                total_buy_raw_rub = total_raw_in_cur
                buy_price_per_item_rub = unit_in_cur
                extra_usd = ""

            if with_commission:
                total_buy = total_buy_raw_rub * 1.02
            else:
                total_buy = total_buy_raw_rub

            buy_price_per_item = total_buy / quantity

            break_even_price_per_item = total_buy / (quantity * 0.9)
            target_total = total_buy * 1.10
            target_sell_price_per_item = target_total / (quantity * 0.9)

            self.sell_info_label.config(
                fg=self._c['info'],
                text=f"Цена за штуку: {buy_price_per_item:.2f} руб. (в базе, с учётом Lis при вкл.){extra_usd}\n"

                     f"Общая стоимость покупки: {total_buy:.2f} руб.\n"

                     f"Цена продажи для возврата затрат: {break_even_price_per_item:.2f} руб./шт\n"

                     f"Цена продажи для +10% профита: {target_sell_price_per_item:.2f} руб./шт\n"

                     f"Общая цена продажи (возврат): {break_even_price_per_item * quantity:.2f} руб.\n"

                     f"Общая цена продажи (+10%): {target_sell_price_per_item * quantity:.2f} руб.\n"
            )
            self._autosize_toplevel(self.add_window, min_w=400, min_h=580)
        except ValueError:
            self.sell_info_label.config(text="Введите корректные данные", fg=self._c['warn'])
            self._autosize_toplevel(self.add_window, min_w=400, min_h=580)
        except ZeroDivisionError:
             self.sell_info_label.config(text="Количество не может быть 0", fg=self._c['warn'])
             self._autosize_toplevel(self.add_window, min_w=400, min_h=580)
        except Exception as e:
            self.sell_info_label.config(text=f"Ошибка расчета: {str(e)}", fg=self._c['warn'])
            self._autosize_toplevel(self.add_window, min_w=400, min_h=580)

    def save_purchase(self):
        try:
            name = self.name_var.get().strip()
            quantity = int(self.quantity_var.get())
            price_input = float(self.price_var.get())
            buy_date = self.buy_date_var.get()
            days = int(self.days_var.get())
            with_commission = self.commission_var.get()
            price_unit = self.price_unit_var.get() # Получаем выбранную единицу
            currency = self.buy_currency_var.get()
            target_raw = self.target_sell_price_var.get() if hasattr(self, "target_sell_price_var") else ""
            target_sell_price = self._parse_optional_positive_float(target_raw)
            if (target_raw or "").strip() and target_sell_price is None:
                messagebox.showerror("Ошибка", "Желаемая цена продажи должна быть положительным числом.")
                return

            if not name:
                messagebox.showerror("Ошибка", "Введите название скина")
                return

            # Проверка даты
            try:
                datetime.strptime(buy_date, "%d.%m.%Y")
            except ValueError:
                messagebox.showerror("Ошибка", "Введите корректную дату (дд.мм.гггг)")
                return

            if price_unit == "per_item" or price_unit == "1 штуку":
                unit_in_cur = price_input
                total_raw_in_cur = quantity * unit_in_cur
            elif price_unit == "total" or price_unit == "все штуки":
                total_raw_in_cur = price_input
                unit_in_cur = total_raw_in_cur / quantity
            else:
                raise ValueError("Неверная единица измерения цены")

            buy_price_usd = None
            total_buy_price_usd = None
            usd_rate_rub = None

            if currency == "USD":
                try:
                    rate = self._buy_usd_rub_rate_save(total_raw_in_cur)
                except ValueError as e:
                    messagebox.showerror("Курс", str(e))
                    return
                usd_rate_rub = rate
                total_buy_raw_rub = total_raw_in_cur * rate
                buy_price_usd = unit_in_cur
                if with_commission:
                    total_buy = total_buy_raw_rub * 1.02
                    total_buy_price_usd = total_raw_in_cur * 1.02
                else:
                    total_buy = total_buy_raw_rub
                    total_buy_price_usd = total_raw_in_cur
            else:
                total_buy_raw_rub = total_raw_in_cur
                if with_commission:
                    total_buy = total_buy_raw_rub * 1.02
                else:
                    total_buy = total_buy_raw_rub
                raw_rate = (self.manual_usd_rate_var.get() or "").strip()
                if raw_rate:
                    rate = self._parse_optional_usd_rate(raw_rate)
                    if not rate:
                        messagebox.showerror("Курс", "Некорректный курс ₽/$.")
                        return
                    usd_rate_rub = rate

            buy_price_per_item = total_buy / quantity
            if currency != "USD" and usd_rate_rub:
                buy_price_usd = buy_price_per_item / usd_rate_rub
                total_buy_price_usd = total_buy / usd_rate_rub

            # Рассчитываем дату разблокировки
            buy_datetime = datetime.strptime(buy_date, "%d.%m.%Y")
            unlock_datetime = buy_datetime + timedelta(days=days)

            # Сохраняем в базу данных (даты в формате YYYY-MM-DD для SQLite)
            cursor = self.conn.cursor()
            cursor.execute('''
                INSERT INTO skins (name, quantity, buy_price, total_buy_price,
                                 buy_date, unlock_date, with_commission,
                                 buy_price_usd, total_buy_price_usd, usd_rate_rub,
                                 target_sell_price_rub)
                VALUES (?, ?, ?, ?, date(?), date(?), ?, ?, ?, ?, ?)
            ''', (name, quantity, buy_price_per_item, total_buy,
                  buy_datetime.strftime("%Y-%m-%d"),
                  unlock_datetime.strftime("%Y-%m-%d"),
                  with_commission,
                  buy_price_usd, total_buy_price_usd, usd_rate_rub,
                  target_sell_price))
            self.conn.commit()
            self.add_window.destroy()
            self.refresh_all()
            messagebox.showinfo("Успех", "Покупка успешно добавлена")
        except ValueError as ve:
            messagebox.showerror("Ошибка", f"Введите корректные данные: {ve}")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Произошла ошибка: {str(e)}")


    def open_sell_window(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Предупреждение", "Выберите скин для продажи")
            return
        if len(selected) != 1:
            messagebox.showwarning("Продажа", "Выберите ровно одну позицию для продажи.")
            return

        item = self.tree.item(selected[0])
        values = item['values']
        skin_id = values[0]
        if len(values) > 10 and str(values[10]).strip() == "Продан":
            messagebox.showinfo("Продажа", "Эта партия уже продана.")
            return
        try:
            available_quantity = int(values[2])
        except (TypeError, ValueError, IndexError):
            messagebox.showerror("Продажа", "Не удалось определить остаток партии.")
            return
        if available_quantity <= 0:
            messagebox.showinfo("Продажа", "В этой партии не осталось предметов.")
            return

        self.sell_window = tk.Toplevel(self.root)
        self.sell_window.title("Продать предметы")
        self.sell_window.geometry("400x590")
        self.sell_window.resizable(True, True)
        self.sell_window.minsize(360, 480)
        self._style_toplevel(self.sell_window)

        pad = {'padx': 18, 'pady': 3}

        self.sell_price_var = tk.StringVar()
        self.sell_currency_var = tk.StringVar(value="RUB") # Новая переменная для валюты
        default_sell_rate = getattr(self, "_usd_rub_rate_cache", None)
        self.exchange_rate_var = tk.StringVar(
            value=f"{default_sell_rate:.4f}" if default_sell_rate else ""
        ) # Переменная для курса
        self.sell_date_var = tk.StringVar(value=datetime.now().strftime("%d.%m.%Y"))
        self.sell_commission_var = tk.BooleanVar(value=True)
        self.sell_quantity_var = tk.StringVar(value=str(available_quantity))
        self._sell_available_quantity = available_quantity
        self._sell_total_buy_price = float(str(values[4]).replace(",", "."))

        tk.Label(self.sell_window, text="Продажа", **self._label_kw(accent=True, bold=True)).pack(anchor=tk.W, **pad)
        tk.Label(self.sell_window, text=f"{values[1]}", **self._label_kw(bold=True)).pack(anchor=tk.W, **pad)
        tk.Label(
            self.sell_window,
            text=f"В партии: {available_quantity}  ·  Себестоимость остатка: {values[4]} руб.",
            **self._label_kw(),
        ).pack(anchor=tk.W, **pad)

        tk.Label(self.sell_window, text="Сколько продано", **self._label_kw()).pack(anchor=tk.W, **pad)
        tk.Entry(
            self.sell_window,
            textvariable=self.sell_quantity_var,
            width=34,
            **self._entry_kw(),
        ).pack(**pad)
        tk.Label(
            self.sell_window,
            text=f"От 1 до {available_quantity}. Непроданная часть останется отдельной доступной партией.",
            **self._label_kw(),
        ).pack(anchor=tk.W, **pad)

        tk.Label(self.sell_window, text="Цена продажи за штуку", **self._label_kw()).pack(anchor=tk.W, **pad)
        tk.Entry(self.sell_window, textvariable=self.sell_price_var, width=34, **self._entry_kw()).pack(**pad)

        self.sell_rub_per_item_var = tk.StringVar()
        tk.Label(
            self.sell_window,
            text="За штуку в ₽ (опционально; если указать вместе с ценой в $ — курс посчитается как ₽ ÷ $)",
            **self._label_kw()).pack(anchor=tk.W, **pad)
        tk.Entry(self.sell_window, textvariable=self.sell_rub_per_item_var, width=34, **self._entry_kw()).pack(**pad)

        tk.Label(self.sell_window, text="Валюта продажи", **self._label_kw()).pack(anchor=tk.W, **pad)
        currency_combo = ttk.Combobox(self.sell_window, textvariable=self.sell_currency_var,
                                      values=["RUB", "USD"], state="readonly", width=31)
        currency_combo.pack(**pad)
        currency_combo.bind('<<ComboboxSelected>>', lambda e: self.on_currency_change())

        rate_frame = tk.Frame(self.sell_window, bg=self._c['bg'])
        rate_frame.pack(fill=tk.X, **pad)
        tk.Label(rate_frame, text="Курс ₽/$", **self._label_kw()).pack(side=tk.LEFT, padx=(0, 8))
        tk.Entry(rate_frame, textvariable=self.exchange_rate_var, width=12, **self._entry_kw()).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(rate_frame, text="Курс API", style='Primary.TButton',
                   command=self.fetch_exchange_rate).pack(side=tk.LEFT)

        tk.Label(self.sell_window, text="Дата продажи (дд.мм.гггг)", **self._label_kw()).pack(anchor=tk.W, **pad)
        tk.Entry(self.sell_window, textvariable=self.sell_date_var, width=34, **self._entry_kw()).pack(**pad)

        tk.Checkbutton(self.sell_window, text="Учитывать комиссию продажи (10%)",
                       variable=self.sell_commission_var, **self._chk_kw()).pack(anchor=tk.W, **pad)

        self.profit_info_label = tk.Label(
            self.sell_window, text="", justify=tk.LEFT, anchor=tk.W,
            bg=self._c['bg'], fg=self._c['info'], font=self._font_small, wraplength=340)
        self.profit_info_label.pack(fill=tk.BOTH, expand=True, pady=(10, 6), padx=18)

        def _sync_sell_wrap(_event=None):
            try:
                w = max(self.sell_window.winfo_width() - 36, 220)
                self.profit_info_label.config(wraplength=w)
            except tk.TclError:
                pass

        self.sell_window.bind("<Configure>", lambda e: _sync_sell_wrap())

        self.sell_price_var.trace('w', self.on_sell_price_change)
        self.sell_quantity_var.trace('w', self.on_sell_price_change)
        self.sell_rub_per_item_var.trace('w', self.on_sell_price_change)
        self.sell_currency_var.trace('w', self.on_sell_price_change)
        self.exchange_rate_var.trace('w', self.on_sell_price_change)
        self.sell_commission_var.trace('w', self.on_sell_price_change)

        button_frame = tk.Frame(self.sell_window, bg=self._c['bg'])
        button_frame.pack(pady=16)
        ttk.Button(button_frame, text="Рассчитать", style='Accent.TButton',
                   command=self.calculate_profit).pack(side=tk.LEFT, padx=4)
        ttk.Button(button_frame, text="Продать", style='Success.TButton',
                   command=lambda: self.sell_skin(skin_id)).pack(side=tk.LEFT, padx=4)
        ttk.Button(button_frame, text="Отмена", style='Danger.TButton',
                   command=self.sell_window.destroy).pack(side=tk.LEFT, padx=4)

        self._autosize_toplevel(self.sell_window, min_w=360, min_h=480)

    def on_currency_change(self):
        """Обновляет поле курса при смене валюты на USD"""
        if self.sell_currency_var.get() == "USD":
            self.fetch_exchange_rate()

    def fetch_exchange_rate(self):
        window, variable = self.sell_window, self.exchange_rate_var
        self._request_usd_rate(
            lambda result: variable.set(f"{result[0]:.4f}") if window.winfo_exists() else None,
            lambda exc: messagebox.showerror("Курс", str(exc)) if window.winfo_exists() else None,
            force=True,
        )

    def on_sell_price_change(self, *args):
        self.calculate_profit()

    def calculate_profit(self):
        try:
            sell_price_input = float(self.sell_price_var.get() or 0)
            currency = self.sell_currency_var.get()
            available_quantity = int(self._sell_available_quantity)
            sold_quantity = int(self.sell_quantity_var.get() or 0)
            total_buy_price = float(self._sell_total_buy_price)
            with_sell_commission = self.sell_commission_var.get()

            if sell_price_input <= 0:
                self.profit_info_label.config(text="", fg=self._c['info'])
                return

            try:
                exchange_rate = self._sell_usd_to_rub_rate(
                    currency=currency,
                    usd_per_item_str=self.sell_price_var.get(),
                    rub_per_item_str=getattr(self, "sell_rub_per_item_var", None) and self.sell_rub_per_item_var.get(),
                    rate_str=self.exchange_rate_var.get(),
                )
            except ValueError as e:
                self.profit_info_label.config(text=str(e), fg=self._c['warn'])
                return

            rub_aux = (getattr(self, "sell_rub_per_item_var", None) and self.sell_rub_per_item_var.get() or "").strip()
            dual = currency == "USD" and rub_aux and self._parse_positive_float(rub_aux)
            sell_price_rub = sell_price_input * exchange_rate if currency == "USD" else sell_price_input
            calculation = calculate_skin_sale(
                available_quantity=available_quantity,
                sold_quantity=sold_quantity,
                total_buy_price_rub=total_buy_price,
                sell_price_rub=sell_price_rub,
                with_commission=with_sell_commission,
            )

            rate_line = ""
            if currency == "USD":
                src = "из ₽ и $ за шт" if dual else "из поля курса"
                rate_line = f"\nКурс: {exchange_rate:.4f} ₽/$ ({src})"

            self.profit_info_label.config(
                fg=self._c['info'],
                text=(
                    f"Продаётся: {calculation.sold_quantity} из {calculation.available_quantity}"
                    f" · останется: {calculation.remaining_quantity}\n"
                    f"Цена за штуку: {sell_price_rub:.2f} RUB ({sell_price_input:.2f} {currency})\n"
                    f"Себестоимость проданной части: {calculation.cost_basis_rub:.2f} RUB\n"
                    f"До комиссии: {calculation.gross_rub:.2f} RUB\n"
                    f"После комиссии: {calculation.net_rub:.2f} RUB\n"
                    f"Профит: {calculation.profit_rub:.2f} руб. "
                    f"({calculation.profit_percent:.1f}%){rate_line}"
                ),
            )
        except ValueError as exc:
            self.profit_info_label.config(text=str(exc), fg=self._c['warn'])
        except Exception as exc:
            self.profit_info_label.config(text=f"Ошибка расчета: {exc}", fg=self._c['warn'])


    def sell_skin(self, skin_id):
        try:
            sell_price_input = float(self.sell_price_var.get())
            sold_quantity = int(self.sell_quantity_var.get())
            sell_currency = self.sell_currency_var.get()
            try:
                exchange_rate = self._sell_usd_to_rub_rate(
                    currency=sell_currency,
                    usd_per_item_str=self.sell_price_var.get(),
                    rub_per_item_str=self.sell_rub_per_item_var.get(),
                    rate_str=self.exchange_rate_var.get(),
                )
            except ValueError as exc:
                messagebox.showerror("Курс", str(exc))
                return

            sell_date = self.sell_date_var.get()
            try:
                sell_datetime = datetime.strptime(sell_date, "%d.%m.%Y")
            except ValueError:
                messagebox.showerror("Ошибка", "Введите корректную дату (дд.мм.гггг)")
                return

            sell_price_rub = (
                sell_price_input * exchange_rate
                if sell_currency == "USD"
                else sell_price_input
            )
            result = record_skin_sale(
                self.conn,
                skin_id=skin_id,
                sold_quantity=sold_quantity,
                sell_date_iso=sell_datetime.strftime("%Y-%m-%d"),
                sell_price_rub=sell_price_rub,
                with_commission=self.sell_commission_var.get(),
                sell_currency=sell_currency,
                sell_usd_rate_rub=self._sale_usd_rate_to_save(exchange_rate),
            )

            self.sell_window.destroy()
            self.refresh_all()
            if result.remaining_row_id is not None:
                for child in self.tree.get_children(""):
                    values = self.tree.item(child, "values")
                    if values and str(values[0]) == str(result.remaining_row_id):
                        self.tree.selection_set(child)
                        self.tree.focus(child)
                        self.tree.see(child)
                        self._on_skins_tree_select()
                        break

            if result.partial:
                message = (
                    f"Продано: {result.calculation.sold_quantity}.\n"
                    f"Осталось в партии: {result.calculation.remaining_quantity}.\n"
                    "Проданная часть сохранена отдельной строкой."
                )
            else:
                message = (
                    f"Партия из {result.calculation.sold_quantity} предметов "
                    "продана полностью."
                )
            messagebox.showinfo("Продажа сохранена", message)
        except ValueError as exc:
            messagebox.showerror("Ошибка", str(exc))
        except Exception as exc:
            messagebox.showerror("Ошибка", f"Произошла ошибка: {exc}")



    def delete_selected_items(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Удаление", "Выберите одну или несколько позиций в таблице.")
            return

        n = len(selected)
        q = f"Удалить {n} позиций из базы?" if n > 1 else "Удалить эту позицию из базы?"
        if not messagebox.askyesno("Подтверждение", q):
            return
        ids = [self.tree.item(i)["values"][0] for i in selected]
        cursor = self.conn.cursor()
        ph = ",".join("?" * len(ids))
        cursor.execute(f"DELETE FROM skins WHERE id IN ({ph})", ids)
        self.conn.commit()
        self.refresh_all()
        messagebox.showinfo("Успех", f"Удалено записей: {len(ids)}")

    def export_skins(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("Все файлы", "*.*")],
            title="Экспорт скинов")
        if not path:
            return
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT * FROM skins ORDER BY id")
            col_names = [d[0] for d in cursor.description]
            rows = cursor.fetchall()
            skins = [dict(zip(col_names, r)) for r in rows]
            payload = {
                "format": "cstracker_skins",
                "version": 2,
                "exported_at": datetime.now().isoformat(timespec="seconds"),
                "skins": skins,
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            messagebox.showinfo("Экспорт", f"Сохранено записей: {len(skins)}")
        except Exception as e:
            messagebox.showerror("Экспорт", str(e))

    def export_selected_skins(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Экспорт", "Выберите одну или несколько позиций в таблице.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("Все файлы", "*.*")],
            title="Экспорт выбранных позиций")
        if not path:
            return
        try:
            ids = [self.tree.item(i)["values"][0] for i in selected]
            cursor = self.conn.cursor()
            ph = ",".join("?" * len(ids))
            cursor.execute(f"SELECT * FROM skins WHERE id IN ({ph}) ORDER BY id", ids)
            col_names = [d[0] for d in cursor.description]
            rows = cursor.fetchall()
            skins = [dict(zip(col_names, r)) for r in rows]
            payload = {
                "format": "cstracker_skins",
                "version": 2,
                "exported_at": datetime.now().isoformat(timespec="seconds"),
                "selection_only": True,
                "skins": skins,
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            messagebox.showinfo("Экспорт", f"Сохранено выбранных записей: {len(skins)}")
        except Exception as e:
            messagebox.showerror("Экспорт", str(e))

    def import_skins(self):
        path = filedialog.askopenfilename(
            filetypes=[("JSON", "*.json"), ("Все файлы", "*.*")],
            title="Импорт скинов")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            messagebox.showerror("Импорт", f"Не удалось прочитать файл: {e}")
            return
        skins = data.get("skins")
        if not skins or not isinstance(skins, list):
            messagebox.showerror("Импорт", "В файле нет списка «skins».")
            return
        mode = messagebox.askyesnocancel(
            "Импорт",
            "Да — добавить к текущей базе\n"
            "Нет — очистить таблицу и загрузить только из файла\n"
            "Отмена — выход")
        if mode is None:
            return
        cursor = self.conn.cursor()
        cursor.execute("PRAGMA table_info(skins)")
        db_cols = [r[1] for r in cursor.fetchall()]
        insert_cols = [c for c in db_cols if c != "id"]
        placeholders = ", ".join("?" * len(insert_cols))
        col_sql = ", ".join(insert_cols)
        defaults = {
            "with_commission": True,
            "with_sell_commission": False,
            "sell_currency": "RUB",
        }
        try:
            if mode is False:
                cursor.execute("DELETE FROM skins")
            n_ok, n_skip = 0, 0
            for s in skins:
                if not isinstance(s, dict):
                    n_skip += 1
                    continue
                if not s.get("name"):
                    n_skip += 1
                    continue
                if s.get("quantity") is None or s.get("buy_price") is None or s.get("total_buy_price") is None:
                    n_skip += 1
                    continue
                vals = []
                for c in insert_cols:
                    v = s.get(c)
                    if v is None and c in defaults:
                        v = defaults[c]
                    if c in ("buy_date", "unlock_date", "sell_date") and v:
                        if isinstance(v, str) and len(v) >= 10 and v[2] == "." and v[5] == ".":
                            try:
                                v = datetime.strptime(v.strip()[:10], "%d.%m.%Y").strftime("%Y-%m-%d")
                            except ValueError:
                                pass
                    vals.append(v)
                try:
                    cursor.execute(
                        f"INSERT INTO skins ({col_sql}) VALUES ({placeholders})",
                        vals)
                    n_ok += 1
                except sqlite3.Error:
                    n_skip += 1
            self.conn.commit()
            self.refresh_all()
            messagebox.showinfo("Импорт", f"Импортировано: {n_ok}, пропущено: {n_skip}")
        except Exception as e:
            messagebox.showerror("Импорт", str(e))

    def get_skin_status(self, buy_date, unlock_date, sell_date):
        """Определяет статус скина"""
        if sell_date and sell_date != "-":
            return "Продан"
        # Проверяем дату разблокировки
        try:
            # Пытаемся распарсить дату в формате DD.MM.YYYY (из таблицы)
            if isinstance(unlock_date, str):
                unlock_datetime = datetime.strptime(unlock_date, "%d.%m.%Y")
            # Если дата уже в формате DATE из БД (YYYY-MM-DD), парсим её так
            elif isinstance(unlock_date, str):  # Этот elif технически избыточен, но логика яснее
                unlock_datetime = datetime.strptime(unlock_date, "%Y-%m-%d")
            else:
                # Если unlock_date уже datetime объект (маловероятно, но на всякий случай)
                unlock_datetime = unlock_date

            current_datetime = datetime.now()
            if current_datetime < unlock_datetime:
                return "Ждет разблокировки"
            else:
                return "Доступен для продажи"
        except Exception as e:  # Ловим любую ошибку парсинга или сравнения
            # print(f"Ошибка определения статуса: {e}") # Для отладки, можно убрать
            return "Неизвестно"  # Возвращаем "Неизвестно" только если не удалось разобрать дату


    def update_market_prices(self, silent=False):
        """Обновление цен с маркета (только buy orders и sell orders)"""
        with self._market_prices_lock:
            if self._market_prices_running:
                return
            self._market_prices_running = True

        if not silent:
            self.market_button.state(['disabled'])
            self.market_button.configure(text='Маркет ▾…')

        def _done(success, payload):
            try:
                if success:
                    buy_prices, sell_prices = payload
                    self.buy_order_prices = buy_prices
                    self.sell_order_prices = sell_prices
                    self._check_target_price_alerts(buy_prices, sell_prices)
                    self.save_inventory_snapshot()
                    self.load_data()
                    if not silent:
                        messagebox.showinfo(
                            "Успех",
                            "Цены с маркета обновлены.\n"
                            f"Buy orders: {len(self.buy_order_prices)} позиций\n"
                            f"Sell orders: {len(self.sell_order_prices)} позиций\n"
                            "Снимок стоимости инвентаря сохранён.",
                        )
                else:
                    if not silent:
                        messagebox.showerror("Ошибка", payload)
            finally:
                if not silent:
                    self.market_button.state(['!disabled'])
                    self.market_button.configure(text='Маркет ▾')
                    self._update_sync_footer()
                    self._refresh_usd_rate_footer()
                with self._market_prices_lock:
                    self._market_prices_running = False

        def _worker():
            try:
                buy_orders_url = "https://market.csgo.com/api/v2/prices/orders/RUB.json"
                sell_orders_url = "https://market.csgo.com/api/v2/prices/RUB.json"

                buy_response = self._http.get(buy_orders_url, timeout=30)
                buy_response.raise_for_status()
                buy_orders_data = buy_response.json()

                sell_response = self._http.get(sell_orders_url, timeout=30)
                sell_response.raise_for_status()
                sell_orders_data = sell_response.json()

                buy_prices = {}
                for item in buy_orders_data.get("items", []):
                    name = item.get("market_hash_name", "")
                    price = item.get("price", 0)
                    if name and price:
                        buy_prices[name] = float(price)

                sell_prices = {}
                for item in sell_orders_data.get("items", []):
                    name = item.get("market_hash_name", "")
                    price = item.get("price", 0)
                    if name and price:
                        sell_prices[name] = float(price)

                return buy_prices, sell_prices
            except Exception:
                raise

        self._tasks.submit(
            "market-prices", _worker,
            lambda result: _done(True, result),
            lambda exc: _done(False, f"Не удалось загрузить цены: {type(exc).__name__}. Проверьте подключение."),
        )

    def _calculate_inventory_valuation(self):
        """Считает текущую оценку инвентаря по загруженным ценам маркета."""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT name, quantity, total_buy_price
            FROM skins
            WHERE sell_date IS NULL
        """)
        rows = cursor.fetchall()

        inv_sell_rub = 0.0
        inv_buyorder_rub = 0.0
        inv_buy_rub = 0.0
        priced_sell_cnt = 0
        priced_buy_cnt = 0

        sell_prices = getattr(self, "sell_order_prices", None) or {}
        buy_prices = getattr(self, "buy_order_prices", None) or {}
        for name, qty, total_buy in rows:
            try:
                qty_i = int(qty or 0)
            except Exception:
                qty_i = 0
            inv_buy_rub += float(total_buy or 0)
            if qty_i <= 0:
                continue

            sp = sell_prices.get(str(name), 0.0)
            if sp:
                inv_sell_rub += float(sp) * qty_i
                priced_sell_cnt += 1

            bp = buy_prices.get(str(name), 0.0)
            if bp:
                inv_buyorder_rub += float(bp) * qty_i
                priced_buy_cnt += 1

        return {
            "inventory_buy_cost_rub": inv_buy_rub,
            "inventory_sell_gross_rub": inv_sell_rub,
            "inventory_buyorder_gross_rub": inv_buyorder_rub,
            "priced_sell_count": priced_sell_cnt,
            "priced_buy_count": priced_buy_cnt,
            "total_positions": len(rows),
        }

    def save_inventory_snapshot(self):
        """Сохраняет снимок текущей стоимости инвентаря в историю."""
        snapshot = self._calculate_inventory_valuation()
        captured_at = datetime.now().isoformat(timespec="seconds")
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO inventory_value_history (
                captured_at,
                inventory_buy_cost_rub,
                inventory_sell_gross_rub,
                inventory_buyorder_gross_rub,
                priced_sell_count,
                priced_buy_count,
                total_positions
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                captured_at,
                snapshot["inventory_buy_cost_rub"],
                snapshot["inventory_sell_gross_rub"],
                snapshot["inventory_buyorder_gross_rub"],
                snapshot["priced_sell_count"],
                snapshot["priced_buy_count"],
                snapshot["total_positions"],
            ),
        )
        self.conn.commit()
        self._save_item_price_snapshots(captured_at)

    def show_inventory_value_dynamics(self):
        """Показывает график динамики стоимости инвентаря по сохраненным снимкам."""
        gb = getattr(self, "skins_graph_button", None)
        self._skins_toolbar_btn_busy(
            gb, True, idle_text="График", busy_text="Строим график…"
        )
        try:
            cursor = self.conn.cursor()
            cursor.execute("""
                SELECT captured_at, inventory_buy_cost_rub, inventory_sell_gross_rub, inventory_buyorder_gross_rub
                FROM inventory_value_history
                ORDER BY captured_at ASC
            """)
            rows = cursor.fetchall()
            if not rows:
                messagebox.showwarning(
                    "Нет данных",
                    "История стоимости инвентаря пуста.\n"
                    "Нажмите «Цены с маркета», чтобы сделать первый снимок.",
                )
                return

            timestamps = []
            buy_cost = []
            sell_net = []
            buyorder_net = []
            order_net_mult = 1.0 - MARKET_COMMISSION_RATE

            for captured_at, buy_rub, sell_rub, buyorder_rub in rows:
                try:
                    dt = datetime.fromisoformat(str(captured_at))
                except ValueError:
                    try:
                        dt = datetime.strptime(str(captured_at), "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        continue
                timestamps.append(dt)
                buy_cost.append(float(buy_rub or 0))
                sell_net.append(float(sell_rub or 0) * order_net_mult)
                buyorder_net.append(float(buyorder_rub or 0) * order_net_mult)

            if not timestamps:
                messagebox.showwarning("Нет данных", "Не удалось прочитать сохранённые снимки стоимости.")
                return

            graph_window = tk.Toplevel(self.root)
            graph_window.title("Динамика стоимости инвентаря")
            graph_window.geometry("980x620")
            graph_window.minsize(600, 420)
            self._style_toplevel(graph_window)

            _lbl = '#e8edf4'
            _grid = '#5a6a7d'

            fig, ax = plt.subplots(figsize=(11, 6))
            fig.patch.set_facecolor(self._c['bg_card'])
            ax.set_facecolor(self._c['entry_bg'])
            ax.tick_params(colors=_lbl, labelsize=10)
            ax.spines['bottom'].set_color(_grid)
            ax.spines['top'].set_color(_grid)
            ax.spines['left'].set_color(_grid)
            ax.spines['right'].set_color(_grid)
            ax.xaxis.label.set_color(_lbl)
            ax.yaxis.label.set_color(_lbl)
            ax.title.set_color('#ffffff')

            sell_line, = ax.plot(
                timestamps, sell_net, marker='o', linewidth=2.2, markersize=4,
                color='#ffc857', label='Оценка по sell orders (net −10%)',
            )
            buy_line, = ax.plot(
                timestamps, buyorder_net, marker='o', linewidth=2.0, markersize=4,
                color='#7ad3ff', label='Оценка по buy orders (net −10%)',
            )
            cost_line, = ax.plot(
                timestamps, buy_cost, marker='o', markersize=3,
                linestyle='--', linewidth=1.8,
                color='#c4cbd5', label='Себестоимость',
            )

            ax.set_xlabel('Дата и время снимка')
            ax.set_ylabel('Стоимость (RUB)')
            ax.set_title('Динамика стоимости инвентаря (ордера — после комиссии 10%)')
            ax.grid(True, alpha=0.45, color=_grid, linestyle='--')
            ax.legend(
                loc='upper center', bbox_to_anchor=(0.5, 1.12), ncol=3,
                facecolor=self._c['bg_elevated'], edgecolor=_grid, labelcolor=_lbl,
            )

            ax.xaxis.set_major_formatter(mdates.DateFormatter('%d.%m %H:%M'))
            fig.autofmt_xdate(rotation=35)
            fig.tight_layout()

            chart_controls = tk.Frame(graph_window, bg=self._c["bg"], padx=10)
            chart_controls.pack(fill=tk.X, pady=(8, 0))
            visible_sell_var = tk.IntVar(value=1)
            visible_buy_var = tk.IntVar(value=1)
            visible_cost_var = tk.IntVar(value=1)
            ignore_outliers_var = tk.IntVar(value=0)
            log_scale_var = tk.IntVar(value=0)
            selected_range = tk.StringVar(value="ALL")
            canvas_holder = {"canvas": None}

            def visible_values():
                values = []
                for line, enabled in (
                    (sell_line, visible_sell_var.get()),
                    (buy_line, visible_buy_var.get()),
                    (cost_line, visible_cost_var.get()),
                ):
                    if enabled and line.get_visible():
                        values.extend(float(v) for v in line.get_ydata() if float(v) > 0)
                return values

            def apply_chart_view():
                sell_line.set_visible(bool(visible_sell_var.get()))
                buy_line.set_visible(bool(visible_buy_var.get()))
                cost_line.set_visible(bool(visible_cost_var.get()))
                key = selected_range.get()
                if key == "ALL":
                    ax.set_xlim(min(timestamps), max(timestamps))
                else:
                    days = {"7D": 7, "30D": 30, "90D": 90, "1Y": 365}[key]
                    right = max(timestamps)
                    left = right - timedelta(days=days)
                    ax.set_xlim(max(min(timestamps), left), right)
                ax.set_yscale("log" if log_scale_var.get() else "linear")
                values = visible_values()
                if values:
                    if ignore_outliers_var.get() and len(values) >= 4:
                        hi = float(np.percentile(values, 95))
                        lo = float(np.percentile(values, 5))
                    else:
                        lo, hi = min(values), max(values)
                    if log_scale_var.get():
                        lo = max(lo, 1.0)
                    pad = max((hi - lo) * 0.12, hi * 0.02, 1.0)
                    ax.set_ylim(max(1.0 if log_scale_var.get() else 0.0, lo - pad), hi + pad)
                ax.legend(
                    loc='upper center', bbox_to_anchor=(0.5, 1.12), ncol=3,
                    facecolor=self._c['bg_elevated'], edgecolor=_grid, labelcolor=_lbl,
                )
                if canvas_holder["canvas"] is not None:
                    canvas_holder["canvas"].draw_idle()

            def set_range(key):
                selected_range.set(key)
                apply_chart_view()

            for text, var, color in (
                ("Sell", visible_sell_var, "#ffc857"),
                ("Buy", visible_buy_var, "#7ad3ff"),
                ("Себестоимость", visible_cost_var, "#c4cbd5"),
            ):
                tk.Checkbutton(
                    chart_controls, text=f"━━ {text}", variable=var, command=apply_chart_view,
                    bg=self._c["bg"], fg=color, activebackground=self._c["bg"],
                    activeforeground=color, selectcolor=self._c["entry_bg"],
                    font=self._font_ui_bold,
                ).pack(side=tk.LEFT, padx=(0, 10))

            tk.Frame(chart_controls, bg=self._c["border_soft"], width=1, height=24).pack(side=tk.LEFT, padx=(2, 10))
            for key in ("7D", "30D", "90D", "1Y", "ALL"):
                ttk.Button(
                    chart_controls, text=key, style="Ghost.TButton",
                    command=lambda k=key: set_range(k),
                ).pack(side=tk.LEFT, padx=(0, 4))
            tk.Checkbutton(
                chart_controls, text="Игнорировать выбросы", variable=ignore_outliers_var,
                command=apply_chart_view, bg=self._c["bg"], fg=self._c["fg_dim"],
                activebackground=self._c["bg"], activeforeground=self._c["fg"],
                selectcolor=self._c["entry_bg"], font=self._font_small,
            ).pack(side=tk.RIGHT, padx=(10, 0))
            tk.Checkbutton(
                chart_controls, text="Лог шкала", variable=log_scale_var,
                command=apply_chart_view, bg=self._c["bg"], fg=self._c["fg_dim"],
                activebackground=self._c["bg"], activeforeground=self._c["fg"],
                selectcolor=self._c["entry_bg"], font=self._font_small,
            ).pack(side=tk.RIGHT, padx=(10, 0))

            canvas = FigureCanvasTkAgg(fig, master=graph_window)
            canvas_holder["canvas"] = canvas
            apply_chart_view()
            canvas.draw()
            canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
            canvas.get_tk_widget().configure(cursor="crosshair")
            point_labels = [stamp.strftime("%d.%m.%Y %H:%M") for stamp in timestamps]
            self._attach_chart_interactions(
                fig,
                ax,
                [
                    {
                        "line": sell_line,
                        "label": "Sell orders (net −10%)",
                        "point_labels": point_labels,
                        "color": "#ffc857",
                    },
                    {
                        "line": buy_line,
                        "label": "Buy orders (net −10%)",
                        "point_labels": point_labels,
                        "color": "#7ad3ff",
                    },
                    {
                        "line": cost_line,
                        "label": "Себестоимость",
                        "point_labels": point_labels,
                        "color": "#c4cbd5",
                    },
                ],
            )
            self._add_chart_toolbar(canvas, graph_window)

            btn_bar = ttk.Frame(graph_window, style='App.TFrame', padding=(0, 0, 0, 10))
            btn_bar.pack(fill=tk.X)
            ttk.Button(btn_bar, text="Закрыть", style='Primary.TButton',
                       command=graph_window.destroy).pack()
            self._autosize_toplevel(graph_window, min_w=600, min_h=420)
        finally:
            self._skins_toolbar_btn_busy(gb, False, idle_text="График")

    # --- НОВЫЕ/ОБНОВЛЕННЫЕ ФУНКЦИИ ---

    def open_edit_window(self):
        """Открытие окна редактирования скина"""
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("Предупреждение", "Выберите скин для редактирования")
            return
        if len(selected) != 1:
            messagebox.showwarning("Редактирование", "Выберите ровно одну позицию для редактирования.")
            return

        item = self.tree.item(selected[0])
        values = item['values']
        skin_id = values[0]

        # Получаем полные данные из БД
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM skins WHERE id = ?', (skin_id,))
        skin_data = cursor.fetchone()
        if not skin_data:
            messagebox.showerror("Ошибка", "Скин не найден в базе данных")
            return

        # Проверяем, продан ли скин
        if skin_data[7] and skin_data[7] != "-":  # sell_date
            self.open_edit_sell_window(skin_id, skin_data)
        else:
            self.open_edit_purchase_window(skin_id, skin_data)

    def open_edit_purchase_window(self, skin_id, skin_data):
        """Открытие окна редактирования параметров покупки (не проданный скин)"""
        self.edit_window = tk.Toplevel(self.root)
        self.edit_window.title("Редактировать покупку скина")
        self.edit_window.geometry("420x600")
        self.edit_window.resizable(True, True)
        self.edit_window.minsize(380, 480)
        self._style_toplevel(self.edit_window)

        pad = {'padx': 20, 'pady': 3}

        # Переменные (заполняем текущими значениями)
        self.edit_name_var = tk.StringVar(value=skin_data[1])  # name
        self.edit_quantity_var = tk.StringVar(value=str(skin_data[2]))  # quantity
        self.edit_buy_price_var = tk.StringVar(value=str(skin_data[3]))  # buy_price
        # Цена за все штуки - для отображения в редактировании
        self.edit_total_buy_price_var = tk.StringVar(value=str(skin_data[4])) # total_buy_price
        self.edit_target_sell_price_var = tk.StringVar(
            value=f"{float(skin_data[19]):.2f}" if len(skin_data) > 19 and skin_data[19] is not None else ""
        )
        # Выбираем единицу измерения на основе цены за штуку
        self.edit_price_unit_var = tk.StringVar(value="per_item") # По умолчанию за штуку

        # Преобразуем даты из формата DATE в формат DD.MM.YYYY для отображения
        try:
            buy_date_obj = datetime.strptime(skin_data[5], "%Y-%m-%d")
            buy_date_display = buy_date_obj.strftime("%d.%m.%Y")
        except (ValueError, TypeError):
            buy_date_display = skin_data[5] if skin_data[5] else ""
        try:
            unlock_date_obj = datetime.strptime(skin_data[6], "%Y-%m-%d")
            unlock_date_display = unlock_date_obj.strftime("%d.%m.%Y")
        except (ValueError, TypeError):
            unlock_date_display = skin_data[6] if skin_data[6] else ""

        self.edit_buy_date_var = tk.StringVar(value=buy_date_display)  # buy_date
        self.edit_unlock_date_var = tk.StringVar(value=unlock_date_display)  # unlock_date
        self.edit_with_commission_var = tk.BooleanVar(value=bool(skin_data[11]))  # with_commission

        tk.Label(self.edit_window, text="Редактирование покупки", **self._label_kw(accent=True, bold=True)).pack(anchor=tk.W, **pad)

        tk.Label(self.edit_window, text="Название скина", **self._label_kw()).pack(anchor=tk.W, **pad)
        tk.Entry(self.edit_window, textvariable=self.edit_name_var, width=42, **self._entry_kw()).pack(**pad)

        tk.Label(self.edit_window, text="Количество", **self._label_kw()).pack(anchor=tk.W, **pad)
        tk.Entry(self.edit_window, textvariable=self.edit_quantity_var, width=42, **self._entry_kw()).pack(**pad)

        tk.Label(self.edit_window, text="Цена за штуку", **self._label_kw()).pack(anchor=tk.W, **pad)
        tk.Entry(self.edit_window, textvariable=self.edit_buy_price_var, width=42, **self._entry_kw()).pack(**pad)

        tk.Label(self.edit_window, text="Общая цена (только просмотр)", **self._label_kw()).pack(anchor=tk.W, **pad)
        tk.Entry(self.edit_window, textvariable=self.edit_total_buy_price_var, width=42,
                 state='readonly', **self._entry_kw()).pack(**pad)

        tk.Label(self.edit_window, text="Желаемая цена продажи за 1 штуку, ₽ (опц.)", **self._label_kw()).pack(anchor=tk.W, **pad)
        tk.Entry(self.edit_window, textvariable=self.edit_target_sell_price_var, width=42, **self._entry_kw()).pack(**pad)

        tk.Label(self.edit_window, text="Меняется цена за 1 штуку — общая пересчитывается при сохранении", **self._label_kw()).pack(anchor=tk.W, **pad)

        tk.Checkbutton(self.edit_window, text="Учитывать комиссию Lis (2%)",
                       variable=self.edit_with_commission_var, **self._chk_kw()).pack(anchor=tk.W, **pad)

        if len(skin_data) > 16 and (
                skin_data[14] is not None or skin_data[15] is not None or skin_data[16] is not None):
            bits = []
            if skin_data[14] is not None:
                bits.append(f"{skin_data[14]:.2f} $/шт")
            if skin_data[15] is not None:
                bits.append(f"всего {skin_data[15]:.2f} $")
            if skin_data[16] is not None:
                bits.append(f"курс {skin_data[16]:.2f} ₽/$")
            tk.Label(
                self.edit_window,
                text="Зафиксировано при покупке (USD): " + " · ".join(bits),
                **self._label_kw()).pack(anchor=tk.W, **pad)

        # Редактирование курса USD/RUB (покупка)
        self.edit_usd_rate_rub_var = tk.StringVar(
            value=f"{skin_data[16]:.4f}" if len(skin_data) > 16 and skin_data[16] else "")
        rate_row = tk.Frame(self.edit_window, bg=self._c['bg'])
        rate_row.pack(fill=tk.X, **pad)
        tk.Label(rate_row, text="Курс ₽/$ (покупка)", **self._label_kw()).pack(side=tk.LEFT, padx=(0, 8))
        tk.Entry(rate_row, textvariable=self.edit_usd_rate_rub_var, width=14, **self._entry_kw()).pack(
            side=tk.LEFT, padx=(0, 8))

        def _fetch_rate_into_purchase_edit():
            window, variable = self.edit_window, self.edit_usd_rate_rub_var
            self._request_usd_rate(
                lambda result: variable.set(f"{result[0]:.4f}") if window.winfo_exists() else None,
                lambda exc: messagebox.showerror("Курс", str(exc)) if window.winfo_exists() else None,
                force=True,
            )

        ttk.Button(rate_row, text="Курс API", style='Primary.TButton',
                   command=_fetch_rate_into_purchase_edit).pack(side=tk.LEFT)

        tk.Label(self.edit_window, text="Дата покупки (дд.мм.гггг)", **self._label_kw()).pack(anchor=tk.W, **pad)
        tk.Entry(self.edit_window, textvariable=self.edit_buy_date_var, width=42, **self._entry_kw()).pack(**pad)

        tk.Label(self.edit_window, text="Дата разблокировки (дд.мм.гггг)", **self._label_kw()).pack(anchor=tk.W, **pad)
        tk.Entry(self.edit_window, textvariable=self.edit_unlock_date_var, width=42, **self._entry_kw()).pack(**pad)

        button_frame = tk.Frame(self.edit_window, bg=self._c['bg'])
        button_frame.pack(pady=20)
        ttk.Button(button_frame, text="Сохранить", style='Success.TButton',
                   command=lambda: self.save_edited_purchase(skin_id)).pack(side=tk.LEFT, padx=4)
        ttk.Button(button_frame, text="Отмена", style='Danger.TButton',
                   command=self.edit_window.destroy).pack(side=tk.LEFT, padx=4)

        self._autosize_toplevel(self.edit_window, min_w=380, min_h=480)

    def save_edited_purchase(self, skin_id):
        """Сохранение отредактированной информации о покупке скина"""
        try:
            name = self.edit_name_var.get().strip()
            quantity = int(self.edit_quantity_var.get())
            buy_price_per_item = float(self.edit_buy_price_var.get()) # Цена за штуку
            buy_date = self.edit_buy_date_var.get()
            unlock_date = self.edit_unlock_date_var.get()
            with_commission = self.edit_with_commission_var.get()
            target_sell_price = self._parse_optional_positive_float(self.edit_target_sell_price_var.get())
            if (self.edit_target_sell_price_var.get() or "").strip() and target_sell_price is None:
                messagebox.showerror("Ошибка", "Желаемая цена продажи должна быть положительным числом.")
                return
            usd_rate_rub = None
            if hasattr(self, "edit_usd_rate_rub_var"):
                raw = (self.edit_usd_rate_rub_var.get() or "").strip()
                if raw:
                    usd_rate_rub = self._parse_optional_usd_rate(raw)
                    if not usd_rate_rub:
                        messagebox.showerror("Ошибка", "Некорректный курс ₽/$ в редактировании покупки.")
                        return

            if not name:
                messagebox.showerror("Ошибка", "Введите название скина")
                return

            # Проверка дат
            try:
                buy_datetime = datetime.strptime(buy_date, "%d.%m.%Y")
                unlock_datetime = datetime.strptime(unlock_date, "%d.%m.%Y")
            except ValueError:
                messagebox.showerror("Ошибка", "Введите корректные даты (дд.мм.гггг)")
                return

            # Рассчитываем общую стоимость
            total_buy_price_raw = quantity * buy_price_per_item
            total_buy_price = total_buy_price_raw
            if with_commission:
                total_buy_price *= 1.02
            buy_price_usd = None
            total_buy_price_usd = None
            if usd_rate_rub:
                buy_price_usd = buy_price_per_item / usd_rate_rub
                total_buy_price_usd = total_buy_price / usd_rate_rub

            # Обновляем запись в БД (даты в формате YYYY-MM-DD для SQLite)
            cursor = self.conn.cursor()
            if usd_rate_rub is None:
                cursor.execute('''
                    UPDATE skins
                    SET name = ?, quantity = ?, buy_price = ?, total_buy_price = ?,
                        buy_date = date(?), unlock_date = date(?), with_commission = ?,
                        target_sell_price_rub = ?
                    WHERE id = ?
                ''', (name, quantity, buy_price_per_item, total_buy_price,
                      buy_datetime.strftime("%Y-%m-%d"),
                      unlock_datetime.strftime("%Y-%m-%d"),
                      with_commission, target_sell_price, skin_id))
            else:
                cursor.execute('''
                    UPDATE skins
                    SET name = ?, quantity = ?, buy_price = ?, total_buy_price = ?,
                        buy_date = date(?), unlock_date = date(?), with_commission = ?,
                        buy_price_usd = ?, total_buy_price_usd = ?, usd_rate_rub = ?,
                        target_sell_price_rub = ?
                    WHERE id = ?
                ''', (name, quantity, buy_price_per_item, total_buy_price,
                      buy_datetime.strftime("%Y-%m-%d"),
                      unlock_datetime.strftime("%Y-%m-%d"),
                      with_commission, buy_price_usd, total_buy_price_usd,
                      usd_rate_rub, target_sell_price, skin_id))
            self.conn.commit()
            self.edit_window.destroy()
            self.refresh_all()
            messagebox.showinfo("Успех", "Информация о покупке скина обновлена")
        except ValueError:
            messagebox.showerror("Ошибка", "Введите корректные данные")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Произошла ошибка: {str(e)}")


    def open_edit_sell_window(self, skin_id, skin_data):
        """Открытие окна редактирования параметров продажи (проданный скин)"""
        self.edit_sell_window = tk.Toplevel(self.root)
        self.edit_sell_window.title("Редактировать продажу скина")
        self.edit_sell_window.geometry("380x520")
        self.edit_sell_window.resizable(True, True)
        self.edit_sell_window.minsize(340, 420)
        self._style_toplevel(self.edit_sell_window)

        pad = {'padx': 18, 'pady': 3}

        # Переменные (заполняем текущими значениями)
        self.edit_sell_price_input_var = tk.StringVar() # Цена в валюте пользователя
        self.edit_sell_currency_var = tk.StringVar(value=skin_data[13] if skin_data[13] else 'RUB') # sell_currency
        saved_sell_rate = None
        if len(skin_data) > 20 and skin_data[20]:
            try:
                saved_sell_rate = float(skin_data[20])
            except (TypeError, ValueError):
                saved_sell_rate = None
        self.edit_exchange_rate_var = tk.StringVar(value=f"{saved_sell_rate:.4f}" if saved_sell_rate else "1.0") # Курс
        # Устанавливаем цену ввода и курс, если валюта не RUB
        if self.edit_sell_currency_var.get() == 'USD' and skin_data[8]: # sell_price
            current_rub_price = skin_data[8]
            current_rate = saved_sell_rate or 1.0
            input_price = current_rub_price / current_rate
            self.edit_sell_price_input_var.set(f"{input_price:.2f}")
            self.edit_exchange_rate_var.set(f"{current_rate:.4f}")
        else: # RUB
            self.edit_sell_price_input_var.set(str(skin_data[8]) if skin_data[8] else "")

        # Преобразуем дату продажи из формата DATE в формат DD.MM.YYYY для отображения
        try:
            sell_date_obj = datetime.strptime(skin_data[7], "%Y-%m-%d")
            sell_date_display = sell_date_obj.strftime("%d.%m.%Y")
        except (ValueError, TypeError):
            sell_date_display = skin_data[7] if skin_data[7] and skin_data[7] != "-" else datetime.now().strftime("%d.%m.%Y")

        self.edit_sell_date_var = tk.StringVar(value=sell_date_display)
        self.edit_sell_commission_var = tk.BooleanVar(value=bool(skin_data[12]))  # with_sell_commission

        tk.Label(self.edit_sell_window, text="Редактирование продажи", **self._label_kw(accent=True, bold=True)).pack(anchor=tk.W, **pad)
        tk.Label(self.edit_sell_window, text=f"{skin_data[1]}", **self._label_kw(bold=True)).pack(anchor=tk.W, **pad)
        tk.Label(self.edit_sell_window, text=f"Количество: {skin_data[2]}  ·  Вложено: {skin_data[4]:.2f} руб.", **self._label_kw()).pack(anchor=tk.W, **pad)

        tk.Label(self.edit_sell_window, text="Цена продажи за штуку (в валюте)", **self._label_kw()).pack(anchor=tk.W, **pad)
        tk.Entry(self.edit_sell_window, textvariable=self.edit_sell_price_input_var, width=34, **self._entry_kw()).pack(**pad)

        self.edit_sell_rub_per_item_var = tk.StringVar()
        if self.edit_sell_currency_var.get() == "USD" and skin_data[8]:
            try:
                self.edit_sell_rub_per_item_var.set(f"{float(skin_data[8]):.2f}")
            except (TypeError, ValueError):
                pass
        tk.Label(
            self.edit_sell_window,
            text="За штуку в ₽ (опционально; вместе с $ — курс = ₽ ÷ $)",
            **self._label_kw()).pack(anchor=tk.W, **pad)
        tk.Entry(self.edit_sell_window, textvariable=self.edit_sell_rub_per_item_var, width=34, **self._entry_kw()).pack(**pad)

        tk.Label(self.edit_sell_window, text="Валюта продажи", **self._label_kw()).pack(anchor=tk.W, **pad)
        currency_combo = ttk.Combobox(self.edit_sell_window, textvariable=self.edit_sell_currency_var,
                                      values=["RUB", "USD"], state="readonly", width=31)
        currency_combo.pack(**pad)
        currency_combo.bind('<<ComboboxSelected>>', lambda e: self.on_edit_currency_change())

        rate_frame = tk.Frame(self.edit_sell_window, bg=self._c['bg'])
        rate_frame.pack(fill=tk.X, **pad)
        tk.Label(rate_frame, text="Курс ₽/$", **self._label_kw()).pack(side=tk.LEFT, padx=(0, 8))
        tk.Entry(rate_frame, textvariable=self.edit_exchange_rate_var, width=12, **self._entry_kw()).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(rate_frame, text="Курс API", style='Primary.TButton',
                   command=self.fetch_exchange_rate_for_edit).pack(side=tk.LEFT)

        tk.Label(self.edit_sell_window, text="Дата продажи (дд.мм.гггг)", **self._label_kw()).pack(anchor=tk.W, **pad)
        tk.Entry(self.edit_sell_window, textvariable=self.edit_sell_date_var, width=34, **self._entry_kw()).pack(**pad)

        tk.Checkbutton(self.edit_sell_window, text="Учитывать комиссию продажи (10%)",
                       variable=self.edit_sell_commission_var, **self._chk_kw()).pack(anchor=tk.W, **pad)

        self.edit_profit_info_label = tk.Label(
            self.edit_sell_window, text="", justify=tk.LEFT, anchor=tk.W,
            bg=self._c['bg'], fg=self._c['info'], font=self._font_small, wraplength=340)
        self.edit_profit_info_label.pack(fill=tk.BOTH, expand=True, pady=(10, 6), padx=18)

        def _sync_edit_sell_wrap(_event=None):
            try:
                w = max(self.edit_sell_window.winfo_width() - 36, 220)
                self.edit_profit_info_label.config(wraplength=w)
            except tk.TclError:
                pass

        self.edit_sell_window.bind("<Configure>", lambda e: _sync_edit_sell_wrap())

        self.edit_sell_price_input_var.trace('w', self.on_edit_sell_price_change)
        self.edit_sell_rub_per_item_var.trace('w', self.on_edit_sell_price_change)
        self.edit_sell_currency_var.trace('w', self.on_edit_sell_price_change)
        self.edit_exchange_rate_var.trace('w', self.on_edit_sell_price_change)
        self.edit_sell_commission_var.trace('w', self.on_edit_sell_price_change)

        self.on_edit_sell_price_change()

        button_frame = tk.Frame(self.edit_sell_window, bg=self._c['bg'])
        button_frame.pack(pady=16)
        ttk.Button(button_frame, text="Рассчитать", style='Accent.TButton',
                   command=self.calculate_edit_profit).pack(side=tk.LEFT, padx=4)
        ttk.Button(button_frame, text="Сохранить", style='Success.TButton',
                   command=lambda: self.save_edited_sell(skin_id, skin_data)).pack(side=tk.LEFT, padx=4)
        ttk.Button(button_frame, text="Отмена", style='Danger.TButton',
                   command=self.edit_sell_window.destroy).pack(side=tk.LEFT, padx=4)

        self._autosize_toplevel(self.edit_sell_window, min_w=340, min_h=420)

    def on_edit_currency_change(self):
        """Обновляет поле курса при смене валюты на USD в окне редактирования"""
        if self.edit_sell_currency_var.get() == "USD":
            self.fetch_exchange_rate_for_edit()

    def fetch_exchange_rate_for_edit(self):
        window, variable = self.edit_sell_window, self.edit_exchange_rate_var
        self._request_usd_rate(
            lambda result: variable.set(f"{result[0]:.4f}") if window.winfo_exists() else None,
            lambda exc: messagebox.showerror("Курс", str(exc)) if window.winfo_exists() else None,
            force=True,
        )

    def on_edit_sell_price_change(self, *args):
        self.calculate_edit_profit()

    def calculate_edit_profit(self):
        try:
            # Получаем данные из полей редактирования продажи
            sell_price_input_str = self.edit_sell_price_input_var.get()
            if not sell_price_input_str:
                self.edit_profit_info_label.config(text="", fg=self._c['info'])
                return
            sell_price_input = float(sell_price_input_str)
            currency = self.edit_sell_currency_var.get()
            with_sell_commission = self.edit_sell_commission_var.get()

            # Получаем количество и цену покупки из текущего окна (или можно из БД)
            selected = self.tree.selection()
            if not selected:
                return
            item = self.tree.item(selected[0])
            values = item['values']
            quantity = int(values[2])
            total_buy_price = float(str(values[4]).replace(',', '.'))

            if sell_price_input <= 0:
                return

            try:
                exchange_rate = self._sell_usd_to_rub_rate(
                    currency=currency,
                    usd_per_item_str=sell_price_input_str,
                    rub_per_item_str=self.edit_sell_rub_per_item_var.get(),
                    rate_str=self.edit_exchange_rate_var.get(),
                )
            except ValueError as e:
                self.edit_profit_info_label.config(text=str(e), fg=self._c['warn'])
                return

            rub_aux = (self.edit_sell_rub_per_item_var.get() or "").strip()
            dual = currency == "USD" and rub_aux and self._parse_positive_float(rub_aux)

            # Переводим цену продажи в RUB
            if currency == "USD":
                sell_price_rub = sell_price_input * exchange_rate
            else: # RUB
                sell_price_rub = sell_price_input

            # Рассчитываем профит
            total_sell_price_before_commission = sell_price_rub * quantity
            if with_sell_commission:
                total_sell_price_after_commission = total_sell_price_before_commission * 0.9  # Вычитаем 10% комиссии
            else:
                 total_sell_price_after_commission = total_sell_price_before_commission

            profit = total_sell_price_after_commission - total_buy_price
            profit_percent = (profit / total_buy_price * 100) if total_buy_price > 0 else 0

            rate_line = ""
            if currency == "USD":
                src = "из ₽ и $ за шт" if dual else "из поля курса"
                rate_line = f"\nКурс: {exchange_rate:.4f} ₽/$ ({src})"

            self.edit_profit_info_label.config(
                fg=self._c['info'],
                text=f"Цена продажи за штуку: {sell_price_rub:.2f} RUB ({sell_price_input:.2f} {currency})\n"

                     f"Общая сумма продажи до комиссии: {total_sell_price_before_commission:.2f} RUB\n"

                     f"После комиссии: {total_sell_price_after_commission:.2f} RUB\n"

                     f"Профит: {profit:.2f} руб. ({profit_percent:.1f}%){rate_line}"
            )
        except ValueError:
            self.edit_profit_info_label.config(text="Введите корректную цену", fg=self._c['warn'])
        except Exception as e:
            self.edit_profit_info_label.config(text=f"Ошибка расчета: {e}", fg=self._c['warn'])

    def save_edited_sell(self, skin_id, skin_data):
        """Сохранение отредактированной информации о продаже скина"""
        try:
            sell_price_input = float(self.edit_sell_price_input_var.get())
            sell_currency = self.edit_sell_currency_var.get()
            try:
                exchange_rate = self._sell_usd_to_rub_rate(
                    currency=sell_currency,
                    usd_per_item_str=self.edit_sell_price_input_var.get(),
                    rub_per_item_str=self.edit_sell_rub_per_item_var.get(),
                    rate_str=self.edit_exchange_rate_var.get(),
                )
            except ValueError as e:
                messagebox.showerror("Курс", str(e))
                return
            sell_usd_rate_rub = self._sale_usd_rate_to_save(exchange_rate)
            sell_date = self.edit_sell_date_var.get()
            with_sell_commission = self.edit_sell_commission_var.get()

            quantity = skin_data[2]  # Получаем из переданных данных
            total_buy_price = skin_data[4]  # Получаем из переданных данных

            # Проверка даты
            try:
                sell_datetime = datetime.strptime(sell_date, "%d.%m.%Y")
            except ValueError:
                messagebox.showerror("Ошибка", "Введите корректную дату (дд.мм.гггг)")
                return

            # Переводим цену продажи в RUB для хранения
            if sell_currency == "USD":
                sell_price_rub = sell_price_input * exchange_rate
            else: # RUB
                sell_price_rub = sell_price_input

            # Рассчитываем профит (аналогично sell_skin)
            total_sell_price_before_commission = sell_price_rub * quantity
            if with_sell_commission:
                total_sell_price_after_commission = total_sell_price_before_commission * 0.9  # Вычитаем 10% комиссии
            else:
                 total_sell_price_after_commission = total_sell_price_before_commission

            profit = total_sell_price_after_commission - total_buy_price
            profit_percent = (profit / total_buy_price * 100) if total_buy_price > 0 else 0

            # Обновляем запись в БД (только поля продажи, дата в формате YYYY-MM-DD для SQLite, цена в RUB)
            cursor = self.conn.cursor()
            cursor.execute('''
                UPDATE skins
                SET sell_date = date(?), sell_price = ?, profit = ?, profit_percent = ?,
                    with_sell_commission = ?, sell_currency = ?, sell_usd_rate_rub = ?
                WHERE id = ?
            ''', (
                sell_datetime.strftime("%Y-%m-%d"), sell_price_rub, profit, profit_percent,
                with_sell_commission, sell_currency, sell_usd_rate_rub, skin_id,
            ))
            self.conn.commit()
            self.edit_sell_window.destroy()
            self.refresh_all()
            messagebox.showinfo("Успех", "Информация о продаже скина обновлена")
        except ValueError:
            messagebox.showerror("Ошибка", "Введите корректную цену")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Произошла ошибка: {str(e)}")

    def load_single_item_history(self):
        selected = self.tree.selection()
        if len(selected) != 1:
            messagebox.showwarning("История", "Выберите ровно одну позицию.")
            return
        if not self._cs_market_api_key_ok():
            return
        values = self.tree.item(selected[0], "values")
        skin_name, skin_id = self._selected_skin_original_name(values), int(values[0])
        api_key = self._get_market_api_key()

        def loaded(history):
            if not history:
                messagebox.showwarning("История", f"История для {skin_name} пуста.")
                return
            self.sales_history[skin_name] = history
            self._save_single_item_price_snapshot(skin_id, skin_name)
            self.load_data()
            current = self.tree.selection()
            if current and str(self.tree.item(current[0], "values")[0]) == str(skin_id):
                self._draw_skin_detail_chart(skin_name, skin_id=skin_id)

        self._tasks.submit(
            f"history:{skin_id}", lambda: self.get_sales_history(skin_name, api_key=api_key), loaded,
            lambda exc: messagebox.showerror("История", f"Не удалось загрузить историю: {type(exc).__name__}"),
        )

    def load_data(self):
        previous = set(self.tree.get_children())
        visible = set()
        self._skin_row_names = {}

        # Filter the same database revision without allocating a fresh result set
        # for every search keystroke. All writes use this connection.
        revision = self.conn.total_changes
        if revision != self._skin_rows_cache_key:
            query = '''
            SELECT id, name, quantity, buy_price, total_buy_price,
                   buy_date, unlock_date, sell_date, sell_price, profit, profit_percent, with_sell_commission, sell_currency,
                   buy_price_usd, total_buy_price_usd, usd_rate_rub, target_sell_price_rub
            FROM skins
            ORDER BY buy_date DESC,
                     COALESCE(purchase_lot_id, id) DESC,
                     sell_date IS NULL DESC,
                     id ASC
        '''
            self._skin_rows_cache = self.conn.execute(query).fetchall()
            self._skin_rows_cache_key = revision
        all_data = self._skin_rows_cache

        # Применяем фильтр по статусу, категории и поиску по названию
        status_filter = self.status_filter_var.get()
        cat_filter = self.category_filter_var.get()
        cat_map = {
            "Скины": "skin",
            "Кейсы": "case",
            "Наклейки": "sticker",
            "Брелки": "charm",
            "Агенты": "agent",
            "Музыка": "music",
        }
        needle = self._search_text(self.name_search_var).casefold()
        order_sell_profit_filter = self.order_sell_profit_filter_var.get()
        filtered_data = []
        for row in all_data:
            if needle and needle not in (row[1] or "").casefold():
                continue
            if cat_filter != "Все":
                want = cat_map.get(cat_filter)
                if want and classify_cs_item_category(row[1]) != want:
                    continue
            # Преобразуем даты из формата DATE в формат DD.MM.YYYY для отображения
            try:
                buy_date_display = datetime.strptime(row[5], "%Y-%m-%d").strftime("%d.%m.%Y") if row[5] else "-"
            except (ValueError, TypeError):
                buy_date_display = row[5] if row[5] else "-"
            try:
                unlock_date_display = datetime.strptime(row[6], "%Y-%m-%d").strftime("%d.%m.%Y") if row[6] else "-"
            except (ValueError, TypeError):
                unlock_date_display = row[6] if row[6] else "-"
            try:
                sell_date_display = datetime.strptime(row[7], "%Y-%m-%d").strftime("%d.%m.%Y") if row[7] and row[7] != "-" else "-"
            except (ValueError, TypeError):
                sell_date_display = row[7] if row[7] and row[7] != "-" else "-"

            # Создаем временный кортеж с преобразованными датами для фильтрации
            temp_row = (
                row[0], row[1], row[2], row[3], row[4],
                buy_date_display, unlock_date_display, sell_date_display,
                row[8], row[9], row[10], row[11], row[12]
            )
            status = self.get_skin_status(temp_row[5], temp_row[6], temp_row[7])

            # Фильтр "продать сейчас по order sell в плюс/минус" применяем только к доступным (unsold + unlocked)
            if order_sell_profit_filter != "Все":
                if status != "Доступен для продажи":
                    continue
                try:
                    qty_i = int(row[2] or 0)
                except Exception:
                    qty_i = 0
                sell_order_price = self.sell_order_prices.get(row[1], 0) if getattr(self, "sell_order_prices", None) else 0
                if qty_i <= 0 or sell_order_price <= 0:
                    continue
                # net с учетом комиссии продажи -10%
                net_sell_rub = float(sell_order_price) * 0.9 * qty_i
                invested_rub = float(row[4] or 0.0)
                profit_now = net_sell_rub - invested_rub
                if order_sell_profit_filter == "В плюс" and profit_now < 0:
                    continue
                if order_sell_profit_filter == "В минус" and profit_now >= 0:
                    continue

            if status_filter == "Все" or status == status_filter:
                filtered_data.append((
                    row[0], row[1], row[2], row[3], row[4],
                    buy_date_display, unlock_date_display, sell_date_display,
                    row[8], row[9], row[10], row[11], row[12],
                    row[13], row[14], row[15], row[16],
                ))

        previous_iid = None
        for index, row in enumerate(filtered_data):
            # Определяем статус
            status = self.get_skin_status(row[5], row[6], row[7])
            if row[7] and row[7] != "-":  # Если есть дата продажи
                # Показываем цену продажи с валютой
                sell_price_formatted = f"{row[8]:.2f}" if row[8] else "-"
                sell_currency = row[12] if row[12] else "RUB"
                if row[9] is not None and row[10] is not None:
                    profit_text = f"{row[9]:.2f} ({row[10]:.1f}%)"
                else:
                    profit_text = "-"
                # В таблице отображаем цену продажи в формате "цена валюта"
                sell_price_display = f"{sell_price_formatted} {sell_currency}"
            else:
                sell_price_display = "-"
                profit_text = "-"

            buy_usd_disp = f"{row[13]:.2f} $" if row[13] is not None else "-"
            rate_disp = f"{row[15]:.2f}" if row[15] is not None else "-"

            # Получаем цены с маркета и форматируем
            buy_order_price = self.buy_order_prices.get(row[1], 0)
            sell_order_price = self.sell_order_prices.get(row[1], 0)
            buy_order_net = self._market_net_price(buy_order_price)
            if buy_order_price > 0:
                buy_order_text = f"{buy_order_price:.2f} (-10% {buy_order_net:.2f})"
            else:
                buy_order_text = "-"
            if sell_order_price > 0:
                sell_net = self._market_net_price(sell_order_price)
                sell_order_text = f"{sell_order_price:.2f} (-10% {sell_net:.2f})"
            else:
                sell_order_text = "-"
            growth_pct_text = self._format_growth_pct(buy_order_net, row[3])
            target_sell_text = self._format_target_progress(row[16], buy_order_net)

            # Получаем среднюю цену из истории продаж (последние 10 сделок)
            history_data = self.sales_history.get(row[1], [])
            if history_data:
                # Берем последние 10 сделок или все, если меньше
                last_deals = history_data[:10]
                avg_price = sum(deal[1] for deal in last_deals) / len(last_deals)
                history_text = f"{avg_price:.2f}"
            else:
                history_text = "-"

            # Форматируем данные для отображения
            values = (
                row[0],  # id
                self._compact_skin_name(row[1]),  # name
                row[2],  # quantity
                f"{row[3]:.2f}",  # buy_price
                f"{row[4]:.2f}",  # total_buy_price
                row[5],  # buy_date
                row[6],  # unlock_date
                row[7] if row[7] and row[7] != "-" else "-",  # sell_date
                sell_price_display,  # sell_price (с валютой)
                profit_text,  # profit
                status,  # status
                buy_order_text,  # order_buy (форматировано)
                sell_order_text,  # order_sell (форматировано)
                growth_pct_text,
                target_sell_text,
                buy_usd_disp,
                rate_disp,
                history_text  # history
            )
            self._skin_row_names[int(row[0])] = row[1]
            # Определяем тег для цветового выделения
            if status == "Продан":
                status_tag = 'sold'
            elif status == "Ждет разблокировки":
                status_tag = 'locked'
            else:
                pct = self._extract_percent(growth_pct_text)
                status_tag = 'loss' if pct is not None and pct < 0 else 'available'

            stripe_tag = "row_odd" if index % 2 else "row_even"
            iid = f"skin:{row[0]}"
            visible.add(iid)
            self._upsert_table_row(self.tree, iid, index, values=values,
                                   tags=(stripe_tag, status_tag), previous_iid=previous_iid)
            previous_iid = iid

        removed = previous - visible
        if removed:
            self.tree.delete(*removed)

        # Подсветка строк
        self.tree.tag_configure('sold', foreground=self._c['fg_dim'])
        self.tree.tag_configure('locked', foreground=self._c['warn_orange'])
        self.tree.tag_configure('available', foreground=self._c['fg_muted'])
        self.tree.tag_configure('loss', foreground=self._c['loss'])
        if hasattr(self, "_skins_table_count_var"):
            count = len(self.tree.get_children(""))
            self._skins_table_count_var.set(f"{count} поз.")
        signature = (self.conn.total_changes, id(self.buy_order_prices), id(self.sell_order_prices),
                     self._usd_rub_rate_cache, datetime.now().date())
        if signature != self._metrics_signature:
            self._metrics_signature = signature
            self._compute_and_show_skins_kpis()
        if self.tree.selection():
            self._on_skins_tree_select()
        else:
            self._clear_skin_detail()

    @staticmethod
    def _upsert_table_row(tree, iid, index, *, values, tags, previous_iid=None):
        if tree.exists(iid):
            if tree.item(iid, "values") != tuple(map(str, values)):
                tree.item(iid, values=values)
            if tree.item(iid, "tags") != tags:
                tree.item(iid, tags=tags)
            if tree.prev(iid) != (previous_iid or ""):
                tree.move(iid, "", index)
        else:
            tree.insert("", index, iid=iid, values=values, tags=tags)

    def load_summary(self):
        """Refresh the dashboard metrics."""
        self._compute_and_show_skins_kpis()

if __name__ == "__main__":
    import argparse
    from cstracker.diagnostics import install_error_logging
    parser = argparse.ArgumentParser(description="Legacy Tkinter Skin Tracker")
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--diagnostics-output")
    parser.add_argument("--db-path")
    args = parser.parse_args()
    root = tk.Tk()
    if args.smoke_test:
        from cstracker.legacy_smoke import run
        output = args.diagnostics_output or os.path.join(SkinTracker._app_base_dir(), "legacy-smoke.json")
        sys.exit(run(root, SkinTracker, output))
    install_error_logging(root, SkinTracker._app_base_dir())
    app = SkinTracker(root, db_path=args.db_path)
    root.mainloop()
