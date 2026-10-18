"""Main PySide6 window and user workflow orchestration."""

from __future__ import annotations

import csv
from datetime import datetime
import os
from pathlib import Path
import urllib.parse
import webbrowser

from PySide6.QtCore import QSortFilterProxyModel, Qt, QThreadPool, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from cstracker.categorization import classify_cs_item_category
from cstracker.config import (
    API_KEY,
    LIS_SKINS_API_BASE,
    LIS_SKINS_API_KEY,
    LIS_SKINS_MARKET_BASE,
)
from cstracker.market import MarketDataService
from cstracker.services.images import ImageRepository, asset_image_url
from cstracker.services.lis_skins import LisSkinsClient

from .charts import InventoryChartDialogContent
from .detail_panel import AssetDetailPanel, SkinDetailPanel
from .dialogs import AssetDialog, SaleDialog, SettingsDialog, SkinDialog, SoldSkinDialog
from .models import AssetTableModel, SkinTableModel
from .theme import COLORS
from .widgets import InsightCard, MetricCard
from .workers import Worker


def _money(value, digits=2):
    return f"{float(value or 0):,.{digits}f} ₽".replace(",", " ")


def _usd(value):
    return f"{float(value or 0):,.2f} $".replace(",", " ")


class PortfolioFilterProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.query = ""
        self.status = "Все"
        self.category = "Все"
        self.setSortCaseSensitivity(Qt.CaseInsensitive)
        self.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.setSortRole(Qt.UserRole)

    def set_filters(self, query=None, status=None, category=None):
        if query is not None:
            self.query = str(query).strip().casefold()
        if status is not None:
            self.status = status
        if category is not None:
            self.category = category
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        record = model.record(source_row)
        if not record:
            return False
        haystack = " ".join(
            str(record.get(key) or "") for key in ("name", "code", "status")
        ).casefold()
        if self.query and self.query not in haystack:
            return False
        if self.status != "Все" and record.get("status") != self.status:
            return False
        if self.category != "Все":
            category = classify_cs_item_category(record.get("name"))
            labels = {
                "Скины": "skin", "Кейсы": "case", "Наклейки": "sticker",
                "Брелоки": "charm", "Агенты": "agent", "Музыка": "music",
            }
            if category != labels.get(self.category):
                return False
        return True


class MainWindow(QMainWindow):
    def __init__(self, repository, *, start_network=True):
        super().__init__()
        self.repository = repository
        self.thread_pool = QThreadPool.globalInstance()
        self.market = MarketDataService()
        self.images = ImageRepository()
        self.lis = LisSkinsClient(
            LIS_SKINS_API_BASE,
            self._lis_key,
            LIS_SKINS_MARKET_BASE,
        )
        self.buy_prices, self.sell_prices = self.repository.latest_market_prices()
        self.usd_rate = None
        self._busy_jobs = 0
        self._workers = set()
        self._detail_token = 0
        self.setWindowTitle("Skin Tracker")
        self.resize(1780, 920)
        self.setMinimumSize(1180, 700)
        self._build_ui()
        self._build_shortcuts()
        self.refresh()
        if start_network:
            QTimer.singleShot(50, self.refresh_currency_rate)
            if self.repository.setting("auto_fetch_skins_on_entry", "0") == "1":
                QTimer.singleShot(300, lambda: self.update_market_prices(silent=True))

    def _build_ui(self):
        root = QWidget()
        root.setObjectName("AppRoot")
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        shell.addWidget(self._build_sidebar())

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(14, 12, 14, 12)
        content_layout.setSpacing(12)
        content_layout.addWidget(self._build_topbar())
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.hide()
        content_layout.addWidget(self.progress)
        self.pages = QStackedWidget()
        self.skins_page = self._build_skins_page()
        self.assets_page = self._build_assets_page()
        self.pages.addWidget(self.skins_page)
        self.pages.addWidget(self.assets_page)
        content_layout.addWidget(self.pages, 1)
        shell.addWidget(content, 1)

    def _build_sidebar(self):
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(164)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(10, 14, 10, 12)
        layout.setSpacing(5)
        brand = QLabel("CS  SKIN TRACKER")
        brand.setStyleSheet(
            f"font-weight: 800; font-size: 13px; color: {COLORS['accent']}; padding: 8px;"
        )
        layout.addWidget(brand)
        layout.addSpacing(12)
        self.nav_buttons = {}
        for index, (key, label) in enumerate(
            (("skins", "Скины"), ("assets", "Активы"))
        ):
            button = QPushButton(label)
            button.setProperty("nav", True)
            button.setProperty("active", index == 0)
            button.clicked.connect(lambda _checked=False, name=key: self.show_page(name))
            layout.addWidget(button)
            self.nav_buttons[key] = button
        layout.addSpacing(12)
        for label, handler in (
            ("Обзор", lambda: self.show_page("skins")),
            ("Продажи", lambda: self._set_skin_status("Продан")),
            ("Графики", self.show_inventory_chart),
            ("Настройки", self.open_settings),
        ):
            button = QPushButton(label)
            button.setProperty("nav", True)
            button.clicked.connect(handler)
            layout.addWidget(button)
        layout.addStretch()
        self.rate_label = QLabel("Курс $: загрузка...")
        self.rate_label.setObjectName("Muted")
        self.rate_label.setWordWrap(True)
        layout.addWidget(self.rate_label)
        self.sync_label = QLabel("Снимок: —")
        self.sync_label.setObjectName("Muted")
        self.sync_label.setWordWrap(True)
        layout.addWidget(self.sync_label)
        return sidebar

    def _build_topbar(self):
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по названию, скину или коду...")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._search_changed)
        layout.addWidget(self.search, 1)
        self.graph_button = QPushButton("График")
        self.graph_button.clicked.connect(self.show_inventory_chart)
        self.refresh_button = QPushButton("Обновить цены")
        self.refresh_button.clicked.connect(self.update_market_prices)
        self.add_button = QPushButton("+ Добавить")
        self.add_button.setProperty("primary", True)
        self.add_button.clicked.connect(self.add_current)
        layout.addWidget(self.graph_button)
        layout.addWidget(self.refresh_button)
        layout.addWidget(self.add_button)
        return bar

    def _build_kpis(self, titles):
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        cards = []
        for title in titles:
            card = MetricCard(title)
            layout.addWidget(card, 1)
            cards.append(card)
        return container, cards

    def _build_skins_page(self):
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(11)
        kpis, self.skin_kpis = self._build_kpis(
            (
                "Затраты на инвентарь", "Текущая стоимость", "Ожидаемый профит",
                "Зафиксировано", "Доступно к продаже", "Ждет разблокировки",
            )
        )
        root.addWidget(kpis)
        insight_row = QWidget()
        insight_layout = QHBoxLayout(insight_row)
        insight_layout.setContentsMargins(0, 0, 0, 0)
        insight_layout.setSpacing(10)
        self.insights = {}
        for key, title in (
            ("day", "Сегодня"), ("week", "За неделю"), ("roi", "Доходность"),
            ("best", "Лучший актив"), ("worst", "Худший актив"),
        ):
            card = InsightCard(title)
            insight_layout.addWidget(card, 1)
            self.insights[key] = card
        root.addWidget(insight_row)

        heading = QHBoxLayout()
        title = QLabel("Позиции")
        title.setObjectName("SectionTitle")
        self.skin_count = QLabel()
        self.skin_count.setObjectName("Muted")
        heading.addWidget(title)
        heading.addWidget(self.skin_count)
        heading.addStretch()
        self.export_button = QPushButton("Экспорт")
        self.export_button.clicked.connect(self.export_skins)
        self.more_button = QPushButton("Еще")
        menu = QMenu(self.more_button)
        menu.addAction("Импорт CSV", self.import_skins)
        menu.addAction("Удалить выбранные", self.delete_selected_skins)
        menu.addSeparator()
        menu.addAction("Market: выставить", self.market_list_selected)
        menu.addAction("Market: изменить цену", self.market_reprice_selected)
        menu.addAction("Market: снять", self.market_remove_selected)
        self.more_button.setMenu(menu)
        heading.addWidget(self.export_button)
        heading.addWidget(self.more_button)
        root.addLayout(heading)

        filters = QFrame()
        filters.setObjectName("Card")
        filter_layout = QHBoxLayout(filters)
        filter_layout.setContentsMargins(12, 8, 12, 8)
        self.status_filter = QComboBox()
        self.status_filter.addItems(
            ("Все", "Доступен к продаже", "Ждет разблокировки", "Продан")
        )
        self.category_filter = QComboBox()
        self.category_filter.addItems(
            ("Все", "Скины", "Кейсы", "Наклейки", "Брелоки", "Агенты", "Музыка")
        )
        self.status_filter.currentTextChanged.connect(self._skin_filters_changed)
        self.category_filter.currentTextChanged.connect(self._skin_filters_changed)
        filter_layout.addWidget(QLabel("Статус:"))
        filter_layout.addWidget(self.status_filter)
        filter_layout.addSpacing(10)
        filter_layout.addWidget(QLabel("Категория:"))
        filter_layout.addWidget(self.category_filter)
        filter_layout.addStretch()
        root.addWidget(filters)

        splitter = QSplitter(Qt.Horizontal)
        self.skin_model = SkinTableModel()
        self.skin_proxy = PortfolioFilterProxy()
        self.skin_proxy.setSourceModel(self.skin_model)
        self.skin_table = self._table(self.skin_proxy)
        self.skin_table.selectionModel().selectionChanged.connect(self._skin_selection_changed)
        self.skin_table.doubleClicked.connect(lambda _index: self.edit_selected_skin())
        self.skin_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.skin_table.customContextMenuRequested.connect(self._skin_context_menu)
        self.skin_detail = SkinDetailPanel()
        self.skin_detail.sell_requested.connect(self.sell_skin)
        self.skin_detail.edit_requested.connect(self.edit_skin)
        self.skin_detail.market_requested.connect(self.open_market)
        self.skin_detail.lis_requested.connect(self.open_lis)
        detail_scroll = QScrollArea()
        detail_scroll.setWidgetResizable(True)
        detail_scroll.setFrameShape(QFrame.NoFrame)
        detail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        detail_scroll.setWidget(self.skin_detail)
        detail_scroll.setMinimumWidth(310)
        detail_scroll.setMaximumWidth(402)
        splitter.addWidget(self.skin_table)
        splitter.addWidget(detail_scroll)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([1300, 330])
        root.addWidget(splitter, 1)
        return page

    def _build_assets_page(self):
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(11)
        kpis, self.asset_kpis = self._build_kpis(
            ("Вложено", "Текущая оценка", "Прибыль / убыток", "Позиций")
        )
        root.addWidget(kpis)
        header = QHBoxLayout()
        title = QLabel("Активы")
        title.setObjectName("SectionTitle")
        header.addWidget(title)
        header.addStretch()
        update = QPushButton("Обновить котировки")
        update.clicked.connect(self.update_asset_quotes)
        delete = QPushButton("Удалить")
        delete.setProperty("danger", True)
        delete.clicked.connect(self.delete_selected_assets)
        header.addWidget(update)
        header.addWidget(delete)
        root.addLayout(header)
        splitter = QSplitter(Qt.Horizontal)
        self.asset_model = AssetTableModel()
        self.asset_proxy = PortfolioFilterProxy()
        self.asset_proxy.setSourceModel(self.asset_model)
        self.asset_table = self._table(self.asset_proxy)
        self.asset_table.selectionModel().selectionChanged.connect(self._asset_selection_changed)
        self.asset_table.doubleClicked.connect(lambda _index: self.edit_selected_asset())
        self.asset_detail = AssetDetailPanel()
        self.asset_detail.edit_requested.connect(self.edit_asset)
        splitter.addWidget(self.asset_table)
        splitter.addWidget(self.asset_detail)
        splitter.setStretchFactor(0, 1)
        splitter.setSizes([1300, 330])
        root.addWidget(splitter, 1)
        return page

    @staticmethod
    def _table(model):
        table = QTableView()
        table.setModel(model)
        table.setAlternatingRowColors(True)
        table.setSortingEnabled(True)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setShowGrid(False)
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(42)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def _build_shortcuts(self):
        for sequence, callback in (
            (QKeySequence("Ctrl+K"), self.search.setFocus),
            (QKeySequence("Ctrl+N"), self.add_current),
            (QKeySequence.Refresh, self.update_market_prices),
            (QKeySequence.Delete, self.delete_current),
        ):
            action = QAction(self)
            action.setShortcut(sequence)
            action.triggered.connect(callback)
            self.addAction(action)

    def show_page(self, name):
        is_skins = name == "skins"
        self.pages.setCurrentWidget(self.skins_page if is_skins else self.assets_page)
        for key, button in self.nav_buttons.items():
            button.setProperty("active", key == name)
            button.style().unpolish(button)
            button.style().polish(button)
        self.refresh_button.setVisible(is_skins)
        self.graph_button.setVisible(is_skins)
        self.add_button.setText("+ Добавить позицию" if is_skins else "+ Добавить актив")
        self.search.setPlaceholderText(
            "Поиск по названию скина..." if is_skins else "Поиск по активам..."
        )
        self._search_changed(self.search.text())

    def refresh(self):
        self._refresh_skins()
        self._refresh_assets()

    def _refresh_skins(self):
        selected_id = self._selected_skin_id()
        records = self.repository.list_skins()
        for row in records:
            buy = float(self.buy_prices.get(row["name"], 0) or 0) * 0.9
            sell = float(self.sell_prices.get(row["name"], 0) or 0) * 0.9
            row["order_buy"] = buy or None
            row["order_sell"] = sell or None
            current = sell or buy or float(row.get("buy_price") or 0)
            row["current_unit"] = current
            row["current_value"] = current * int(row.get("quantity") or 0)
            purchase = float(row.get("buy_price") or 0)
            row["growth_pct"] = (current - purchase) / purchase * 100 if purchase else None
        self.skin_model.set_records(records)
        self.skin_count.setText(f"{len(records)} поз.")
        self._update_skin_kpis(records)
        if selected_id is not None:
            self._restore_selection(self.skin_table, self.skin_proxy, "id", selected_id)

    def _refresh_assets(self):
        selected_id = self._selected_asset_id()
        records = self.repository.list_assets()
        for row in records:
            amount = float(row.get("amount") or 0)
            current = float(row.get("current_price_rub") or 0)
            buy = float(row.get("buy_price_rub") or 0)
            row["value_rub"] = current * amount
            row["growth_pct"] = (current - buy) / buy * 100 if buy else None
        self.asset_model.set_records(records)
        self._update_asset_kpis(records)
        if selected_id is not None:
            self._restore_selection(self.asset_table, self.asset_proxy, "id", selected_id)

    def _update_skin_kpis(self, records):
        metrics = self.repository.inventory_metrics(
            self.buy_prices, self.sell_prices, self.usd_rate
        )
        history = self.repository.inventory_history()
        cost_spark = [float(row["inventory_buy_cost_rub"] or 0) for row in history]
        sell_spark = [float(row["inventory_sell_gross_rub"] or 0) * 0.9 for row in history]
        profit_spark = [
            sell - cost for sell, cost in zip(sell_spark, cost_spark)
        ]
        self.skin_kpis[0].set_metric(
            _money(metrics["invested"]),
            f"себест. {_usd(metrics['invested_usd'])}" if metrics["invested_usd"] is not None else "не для всех позиций известен курс",
            spark=cost_spark,
        )
        sell = metrics["sell_value"]
        buy = metrics["buy_value"]
        tone = "profit" if metrics["sell_profit"] >= 0 else "loss"
        self.skin_kpis[1].set_metric(
            f"Sell {_money(sell)}",
            f"Buy {_money(buy)} · {_usd(sell / self.usd_rate) if self.usd_rate and sell else '—'}",
            tone=tone,
            spark=sell_spark,
        )
        self.skin_kpis[2].set_metric(
            f"Sell {_money(metrics['sell_profit'])}",
            f"Buy {_money(metrics['buy_profit'])}",
            tone=tone,
            spark=profit_spark,
        )
        realized_tone = "profit" if metrics["realized"] >= 0 else "loss"
        realized_sub = _usd(metrics["realized_usd"])
        if metrics["realized_average_rate"] is not None:
            realized_sub += f" · ср. {metrics['realized_average_rate']:.2f} ₽/$"
        if metrics["realized_without_rate"]:
            realized_sub += f" · {metrics['realized_without_rate']} без курса"
        self.skin_kpis[3].set_metric(
            f"{metrics['realized']:+,.2f} ₽".replace(",", " "),
            realized_sub,
            tone=realized_tone,
        )
        self.skin_kpis[4].set_metric(
            f"Sell {_money(metrics['available_sell'])}",
            (
                f"Buy {_money(metrics['available_buy'])} · "
                f"{_usd(metrics['available_sell'] / self.usd_rate) if self.usd_rate else 'курс недоступен'} · "
                f"{metrics['available_quantity']} шт."
            ),
            tone="profit",
        )
        self.skin_kpis[5].set_metric(
            _money(metrics["locked_sell"]),
            f"{metrics['locked_count']} поз.",
            tone="warning",
        )
        self.insights["day"].set_value(
            self._delta_pair(metrics["day_sell"], metrics["day_buy"], "день"),
            self._delta_tone(metrics["day_sell"]),
        )
        self.insights["week"].set_value(
            self._delta_pair(metrics["week_sell"], metrics["week_buy"], "неделю"),
            self._delta_tone(metrics["week_sell"]),
        )
        roi = metrics["sell_profit"] / metrics["invested"] * 100 if metrics["invested"] else 0
        self.insights["roi"].set_value(f"{roi:+.2f}%", "profit" if roi >= 0 else "loss")
        open_rows = [row for row in records if row["status"] != "Продан" and row["growth_pct"] is not None]
        if open_rows:
            best = max(open_rows, key=lambda row: row["growth_pct"])
            worst = min(open_rows, key=lambda row: row["growth_pct"])
            self.insights["best"].set_value(
                f"{best['name']}\n{best['growth_pct']:+.1f}%", "profit"
            )
            self.insights["worst"].set_value(
                f"{worst['name']}\n{worst['growth_pct']:+.1f}%", "loss"
            )
        else:
            self.insights["best"].set_value("Нет данных")
            self.insights["worst"].set_value("Нет данных")

    def _update_asset_kpis(self, records):
        invested = sum(
            float(row.get("buy_price_rub") or 0) * float(row.get("amount") or 0)
            for row in records
        )
        current = sum(float(row.get("value_rub") or 0) for row in records)
        result = current - invested
        tone = "profit" if result >= 0 else "loss"
        self.asset_kpis[0].set_metric(_money(invested))
        self.asset_kpis[1].set_metric(_money(current), tone=tone)
        self.asset_kpis[2].set_metric(_money(result), tone=tone)
        self.asset_kpis[3].set_metric(str(len(records)))

    @staticmethod
    def _delta_pair(sell, buy, label):
        def part(name, value):
            if not value:
                return f"{name}: нет снимка"
            return f"{name} {value['delta']:+,.0f} ₽ ({value['pct']:+.2f}%)".replace(",", " ")
        return f"{part('Sell', sell)} · {part('Buy', buy)} за {label}"

    @staticmethod
    def _delta_tone(value):
        if not value:
            return "neutral"
        return "profit" if value["delta"] >= 0 else "loss"

    def _search_changed(self, text):
        if self.pages.currentWidget() == self.skins_page:
            self.skin_proxy.set_filters(query=text)
        else:
            self.asset_proxy.set_filters(query=text)

    def _skin_filters_changed(self):
        self.skin_proxy.set_filters(
            status=self.status_filter.currentText(),
            category=self.category_filter.currentText(),
        )

    def _set_skin_status(self, status):
        self.show_page("skins")
        self.status_filter.setCurrentText(status)

    def _selected_source_rows(self, table, proxy):
        rows = sorted({index.row() for index in table.selectionModel().selectedRows()})
        return [proxy.mapToSource(proxy.index(row, 0)).row() for row in rows]

    def _selected_skin(self):
        rows = self._selected_source_rows(self.skin_table, self.skin_proxy)
        return self.skin_model.record(rows[0]) if rows else None

    def _selected_skin_id(self):
        row = self._selected_skin()
        return int(row["id"]) if row else None

    def _selected_asset(self):
        rows = self._selected_source_rows(self.asset_table, self.asset_proxy)
        return self.asset_model.record(rows[0]) if rows else None

    def _selected_asset_id(self):
        row = self._selected_asset()
        return int(row["id"]) if row else None

    @staticmethod
    def _restore_selection(table, proxy, key, value):
        model = proxy.sourceModel()
        for row, record in enumerate(model.records):
            if record.get(key) == value:
                source = model.index(row, 0)
                mapped = proxy.mapFromSource(source)
                if mapped.isValid():
                    table.selectRow(mapped.row())
                    table.scrollTo(mapped)
                break

    def _skin_selection_changed(self):
        skin = self._selected_skin()
        if not skin:
            self.skin_detail.clear()
            return
        self.skin_detail.set_skin(skin)
        self.skin_detail.set_history(self.repository.item_history(skin["id"]))
        self._load_skin_detail(skin)

    def _load_skin_detail(self, skin):
        self._detail_token += 1
        token = self._detail_token
        self.skin_detail.set_loading("Обновляем Lis и изображение...")

        def task():
            info = self.lis.lowest_item(skin["name"])
            if info:
                self.repository.save_lis_snapshot(
                    skin["id"], skin["name"], info, self.usd_rate
                )
            history = self.repository.item_history(skin["id"])
            image_url = info.get("image_url") if info else None
            if not image_url:
                for row in reversed(history):
                    if row.get("skin_image_url"):
                        image_url = row["skin_image_url"]
                        break
            image = self.images.download(image_url) if image_url else None
            return token, skin["id"], history, image

        def done(payload):
            request_token, skin_id, history, image = payload
            selected = self._selected_skin()
            if request_token != self._detail_token or not selected or selected["id"] != skin_id:
                return
            self.skin_detail.set_history(history)
            self.skin_detail.set_image_bytes(image)
            self.skin_detail.set_loading("")

        def detail_finished():
            selected = self._selected_skin()
            if token == self._detail_token and selected and selected["id"] == skin["id"]:
                self.skin_detail.set_loading("")

        self.run_background(
            task,
            done,
            error_message=None,
            on_finished=detail_finished,
            show_progress=False,
        )

    def _asset_selection_changed(self):
        asset = self._selected_asset()
        if not asset:
            return
        self.asset_detail.set_asset(asset)
        url = asset_image_url(asset.get("asset_group"), asset.get("code"))
        if url:
            self.run_background(
                lambda: self.images.download(url),
                self.asset_detail.set_image_bytes,
                error_message=None,
                show_progress=False,
            )
        else:
            self.asset_detail.set_image_bytes(None)

    def add_current(self):
        if self.pages.currentWidget() == self.skins_page:
            dialog = SkinDialog(usd_rate=self.usd_rate, parent=self)
            if dialog.exec() == QDialog.Accepted:
                skin_id = self.repository.add_skin(dialog.values())
                self.refresh()
                self._restore_selection(self.skin_table, self.skin_proxy, "id", skin_id)
        else:
            dialog = AssetDialog(parent=self)
            if dialog.exec() == QDialog.Accepted:
                asset_id = self.repository.save_asset(dialog.values())
                self.refresh()
                self._restore_selection(self.asset_table, self.asset_proxy, "id", asset_id)

    def edit_selected_skin(self):
        skin = self._selected_skin()
        if skin:
            self.edit_skin(skin["id"])

    def edit_skin(self, skin_id):
        skin = self.repository.get_skin(skin_id)
        if not skin:
            return
        dialog = (
            SoldSkinDialog(skin, self.usd_rate, self)
            if skin["status"] == "Продан"
            else SkinDialog(skin, self.usd_rate, self)
        )
        if dialog.exec() == QDialog.Accepted:
            self.repository.update_skin(skin_id, dialog.values())
            self.refresh()

    def sell_skin(self, skin_id):
        skin = self.repository.get_skin(skin_id)
        if not skin or skin["status"] == "Продан":
            return
        dialog = SaleDialog(skin, self.usd_rate, self)
        if dialog.exec() == QDialog.Accepted:
            try:
                result = self.repository.sell_skin(skin_id, **dialog.values())
            except ValueError as error:
                QMessageBox.warning(self, "Продажа не сохранена", str(error))
                return
            self.refresh()
            calculation = result.calculation
            QMessageBox.information(
                self,
                "Продажа сохранена",
                (
                    f"Продано: {calculation.sold_quantity} шт.\n"
                    f"Осталось: {calculation.remaining_quantity} шт.\n"
                    f"Профит: {calculation.profit_rub:+,.2f} ₽"
                ).replace(",", " "),
            )

    def edit_selected_asset(self):
        asset = self._selected_asset()
        if asset:
            self.edit_asset(asset["id"])

    def edit_asset(self, asset_id):
        asset = self.repository.get_asset(asset_id)
        if not asset:
            return
        dialog = AssetDialog(asset, self)
        if dialog.exec() == QDialog.Accepted:
            self.repository.save_asset(dialog.values(), asset_id)
            self.refresh()

    def delete_current(self):
        if self.pages.currentWidget() == self.skins_page:
            self.delete_selected_skins()
        else:
            self.delete_selected_assets()

    def delete_selected_skins(self):
        rows = self._selected_source_rows(self.skin_table, self.skin_proxy)
        ids = [self.skin_model.record(row)["id"] for row in rows]
        if not ids:
            return
        if QMessageBox.question(
            self, "Удалить позиции?", f"Будет удалено: {len(ids)}."
        ) == QMessageBox.Yes:
            self.repository.delete_skins(ids)
            self.refresh()

    def delete_selected_assets(self):
        rows = self._selected_source_rows(self.asset_table, self.asset_proxy)
        ids = [self.asset_model.record(row)["id"] for row in rows]
        if not ids:
            return
        if QMessageBox.question(
            self, "Удалить активы?", f"Будет удалено: {len(ids)}."
        ) == QMessageBox.Yes:
            self.repository.delete_assets(ids)
            self.refresh()

    def _skin_context_menu(self, position):
        skin = self._selected_skin()
        if not skin:
            return
        menu = QMenu(self)
        if skin["status"] != "Продан":
            menu.addAction("Продать", lambda: self.sell_skin(skin["id"]))
            menu.addAction("Открыть на Market", lambda: self.open_market(skin["name"]))
            menu.addSeparator()
            menu.addAction("Market API: выставить", self.market_list_selected)
            menu.addAction("Market API: изменить цену", self.market_reprice_selected)
            menu.addAction("Market API: снять", self.market_remove_selected)
        menu.addAction("Изменить", lambda: self.edit_skin(skin["id"]))
        menu.addAction("Открыть на Lis", lambda: self.open_lis(skin["name"]))
        menu.addSeparator()
        menu.addAction("Удалить", self.delete_selected_skins)
        menu.exec(self.skin_table.viewport().mapToGlobal(position))

    def refresh_currency_rate(self):
        def done(payload):
            self.usd_rate, source = payload
            self.rate_label.setText(f"Курс $: {self.usd_rate:.2f} ₽\n{source}")
            self._refresh_skins()

        self.run_background(
            self.market.usd_rub_rate,
            done,
            "Не удалось получить курс доллара",
            show_progress=False,
        )

    def update_market_prices(self, _checked=False, silent=False):
        self.refresh_button.setEnabled(False)
        self.refresh_button.setText("Обновление...")

        def done(payload):
            self.buy_prices, self.sell_prices = payload
            captured = self.repository.save_market_snapshot(
                self.buy_prices, self.sell_prices
            )
            self.sync_label.setText(
                f"Снимок: {datetime.fromisoformat(captured):%d.%m.%Y %H:%M}"
            )
            self.refresh()
            if not silent:
                QMessageBox.information(
                    self,
                    "Цены обновлены",
                    f"Buy orders: {len(self.buy_prices)}\nSell orders: {len(self.sell_prices)}",
                )

        def finished():
            self.refresh_button.setEnabled(True)
            self.refresh_button.setText("Обновить цены")

        self.run_background(
            self.market.market_prices,
            done,
            None if silent else "Не удалось загрузить цены Market",
            finished,
            show_progress=not silent,
        )

    def update_asset_quotes(self):
        if not self.usd_rate:
            return QMessageBox.warning(self, "Нет курса", "Сначала дождитесь курса доллара.")

        def done(quotes):
            self.repository.update_asset_quotes(quotes)
            self._refresh_assets()

        self.run_background(
            lambda: self.market.asset_quotes(self.usd_rate),
            done,
            "Не удалось обновить котировки активов",
        )

    def run_background(
        self,
        function,
        on_result=None,
        error_message="Ошибка",
        on_finished=None,
        *,
        show_progress=True,
    ):
        if show_progress:
            self._busy_jobs += 1
            self.progress.show()
        worker = Worker(function)
        self._workers.add(worker)
        if on_result:
            worker.signals.result.connect(on_result)

        def failed(traceback_text):
            if error_message:
                detail = traceback_text.strip().splitlines()[-1] if traceback_text else ""
                QMessageBox.warning(self, error_message, detail)

        def finished():
            if show_progress:
                self._busy_jobs = max(0, self._busy_jobs - 1)
                if self._busy_jobs == 0:
                    self.progress.hide()
            if on_finished:
                on_finished()
            # The finished signal is emitted just before QRunnable.run returns.
            # Defer release so the wrapper cannot disappear inside its own call.
            QTimer.singleShot(100, lambda job=worker: self._workers.discard(job))

        worker.signals.error.connect(failed)
        worker.signals.finished.connect(finished)
        self.thread_pool.start(worker)

    def open_lis(self, name):
        QDesktopServices.openUrl(QUrl(self.lis.market_url(name)))

    @staticmethod
    def open_market(name):
        slug = urllib.parse.quote(str(name), safe="")
        QDesktopServices.openUrl(QUrl(f"https://market.csgo.com/en/{slug}"))

    def show_inventory_chart(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Динамика стоимости инвентаря")
        dialog.resize(1040, 660)
        layout = QVBoxLayout(dialog)
        layout.addWidget(InventoryChartDialogContent(self.repository.inventory_history()))
        close = QPushButton("Закрыть")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close, alignment=Qt.AlignCenter)
        dialog.exec()

    def open_settings(self):
        SettingsDialog(self.repository, self).exec()

    def _market_api_key(self):
        return self.repository.setting("market_csgo_api_key", "") or API_KEY

    def _market_api_ready(self):
        key = str(self._market_api_key() or "").strip()
        if not key or key == "YOUR_API_KEY_HERE":
            QMessageBox.warning(
                self,
                "Market API",
                "Укажите API-ключ market.csgo.com в настройках.",
            )
            return None
        return key

    def market_list_selected(self):
        skin = self._selected_skin()
        key = self._market_api_ready()
        if not skin or not key or skin["status"] == "Продан":
            return
        if skin.get("market_item_id"):
            return QMessageBox.information(
                self, "Market API", "К позиции уже привязан выставленный лот."
            )

        def done(payload):
            items = [
                item for item in payload.get("items", [])
                if item.get("market_hash_name") == skin["name"]
                and str(item.get("tradable")) in {"1", "true", "True"}
            ]
            if not items:
                answer = QMessageBox.question(
                    self,
                    "Предмет не найден",
                    "Обновить инвентарь Market? После запроса подождите 10–20 секунд и повторите.",
                )
                if answer == QMessageBox.Yes:
                    self.run_background(
                        lambda: self.market.private_get(key, "update-inventory"),
                        lambda _value: QMessageBox.information(
                            self, "Market API", "Обновление запрошено."
                        ),
                        "Не удалось обновить инвентарь Market",
                    )
                return
            labels = [
                f"asset {item.get('id')} · ориентир {item.get('market_price', '—')}"
                for item in items
            ]
            selected, ok = QInputDialog.getItem(
                self, "Выставить на Market", "Экземпляр", labels, 0, False
            )
            if not ok:
                return
            item = items[labels.index(selected)]
            default = float(skin.get("order_sell") or skin.get("target_sell_price_rub") or 0)
            price, ok = QInputDialog.getDouble(
                self, "Выставить на Market", "Цена, ₽", default, 0.01, 1_000_000_000, 2
            )
            if not ok:
                return

            def listed(response):
                item_id = response.get("item_id")
                if item_id is None:
                    raise RuntimeError("Market не вернул item_id.")
                self.repository.bind_market_item(skin["id"], item["id"], item_id)
                self.refresh()
                QMessageBox.information(self, "Market API", f"Лот выставлен: {item_id}.")

            self.run_background(
                lambda: self.market.private_get(
                    key,
                    "add-to-sale",
                    id=item["id"],
                    price=self.market.price_to_api(price, "RUB"),
                    cur="RUB",
                ),
                listed,
                "Не удалось выставить предмет",
            )

        self.run_background(
            lambda: self.market.private_get(key, "my-inventory", lang="ru"),
            done,
            "Не удалось загрузить инвентарь Market",
        )

    def _market_active_lot(self, skin, key, callback):
        def done(payload):
            candidates = [
                item for item in payload.get("items", [])
                if str(item.get("status")) == "1"
                and (
                    str(item.get("item_id")) == str(skin.get("market_item_id"))
                    if skin.get("market_item_id")
                    else item.get("market_hash_name") == skin["name"]
                )
            ]
            if not candidates:
                QMessageBox.information(self, "Market API", "Активный лот не найден.")
                return
            if len(candidates) == 1:
                callback(candidates[0])
                return
            labels = [
                f"{item.get('item_id')} · "
                f"{self.market.price_from_api(item.get('price'), item.get('currency') or 'RUB'):.2f} "
                f"{item.get('currency') or 'RUB'}"
                for item in candidates
            ]
            selected, ok = QInputDialog.getItem(
                self, "Выберите лот", "Активные лоты", labels, 0, False
            )
            if ok:
                callback(candidates[labels.index(selected)])

        self.run_background(
            lambda: self.market.private_get(key, "items"),
            done,
            "Не удалось загрузить лоты Market",
        )

    def market_reprice_selected(self):
        skin = self._selected_skin()
        key = self._market_api_ready()
        if not skin or not key:
            return

        def edit(lot):
            currency = lot.get("currency") or "RUB"
            old = self.market.price_from_api(lot.get("price"), currency)
            price, ok = QInputDialog.getDouble(
                self, "Цена лота", f"Новая цена, {currency}", old, 0.01, 1_000_000_000, 2
            )
            if not ok:
                return
            self.run_background(
                lambda: self.market.private_get(
                    key,
                    "set-price",
                    item_id=int(lot["item_id"]),
                    price=self.market.price_to_api(price, currency),
                    cur=currency,
                ),
                lambda _value: QMessageBox.information(
                    self, "Market API", "Цена обновлена."
                ),
                "Не удалось изменить цену",
            )

        self._market_active_lot(skin, key, edit)

    def market_remove_selected(self):
        skin = self._selected_skin()
        key = self._market_api_ready()
        if not skin or not key:
            return

        def remove(lot):
            if QMessageBox.question(
                self, "Снять лот?", f"item_id {lot.get('item_id')}"
            ) != QMessageBox.Yes:
                return
            currency = lot.get("currency") or "RUB"

            def removed(_value):
                self.repository.clear_market_item(skin["id"])
                self.refresh()
                QMessageBox.information(self, "Market API", "Лот снят.")

            self.run_background(
                lambda: self.market.private_get(
                    key,
                    "set-price",
                    item_id=int(lot["item_id"]),
                    price=0,
                    cur=currency,
                ),
                removed,
                "Не удалось снять лот",
            )

        self._market_active_lot(skin, key, remove)

    def export_skins(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Экспорт позиций", "skins-export.csv", "CSV (*.csv)"
        )
        if not path:
            return
        records = self.repository.list_skins()
        columns = (
            "id", "name", "quantity", "buy_price", "total_buy_price",
            "buy_date", "unlock_date", "sell_date", "sell_price", "profit",
            "profit_percent", "buy_price_usd", "usd_rate_rub",
        )
        with open(path, "w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(records)

    def import_skins(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Импорт позиций", "", "CSV (*.csv)"
        )
        if not path:
            return
        imported = 0
        try:
            with open(path, "r", newline="", encoding="utf-8-sig") as stream:
                for row in csv.DictReader(stream):
                    if not row.get("name"):
                        continue
                    quantity = int(float(row.get("quantity") or 1))
                    buy_price = float(str(row.get("buy_price") or "0").replace(",", "."))
                    if buy_price <= 0:
                        continue
                    values = {
                        "name": row["name"],
                        "quantity": quantity,
                        "buy_price": buy_price,
                        "total_buy_price": float(row.get("total_buy_price") or buy_price * quantity),
                        "buy_date": row.get("buy_date") or datetime.now().date().isoformat(),
                        "unlock_date": row.get("unlock_date") or datetime.now().date().isoformat(),
                        "with_commission": 1,
                        "buy_price_usd": float(row["buy_price_usd"]) if row.get("buy_price_usd") else None,
                        "total_buy_price_usd": None,
                        "usd_rate_rub": float(row["usd_rate_rub"]) if row.get("usd_rate_rub") else None,
                        "target_sell_price_rub": None,
                    }
                    if values["buy_price_usd"]:
                        values["total_buy_price_usd"] = values["buy_price_usd"] * quantity
                    self.repository.add_skin(values)
                    imported += 1
        except (OSError, ValueError, csv.Error) as error:
            return QMessageBox.warning(self, "Импорт не выполнен", str(error))
        self.refresh()
        QMessageBox.information(self, "Импорт", f"Добавлено позиций: {imported}.")

    def _lis_key(self):
        return self.repository.setting("lis_skins_api_key", "") or LIS_SKINS_API_KEY
