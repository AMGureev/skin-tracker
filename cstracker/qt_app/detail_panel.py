"""Compact detail cards displayed next to the portfolio tables."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from .charts import PriceChart, SERIES
from .models import money, short_date
from .theme import COLORS


class SkinDetailPanel(QFrame):
    sell_requested = Signal(int)
    edit_requested = Signal(int)
    market_requested = Signal(str)
    lis_requested = Signal(str)
    range_changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setMinimumWidth(300)
        self.setMaximumWidth(390)
        self.skin = None
        self._pixmap = None
        root = QVBoxLayout(self)
        root.setContentsMargins(11, 10, 11, 11)
        root.setSpacing(5)

        eyebrow = QLabel("ПОЗИЦИЯ")
        eyebrow.setObjectName("Muted")
        root.addWidget(eyebrow)
        self.title = QLabel("Выберите позицию")
        self.title.setWordWrap(True)
        self.title.setStyleSheet("font-size: 16px; font-weight: 700;")
        root.addWidget(self.title)
        self.status = QLabel(" ")
        self.status.setObjectName("Muted")
        root.addWidget(self.status)

        hero = QHBoxLayout()
        hero.setSpacing(12)
        self.image = QLabel("Нет изображения")
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setWordWrap(True)
        self.image.setFixedSize(104, 104)
        self.image.setStyleSheet(
            f"background: #121d28; border: 1px solid {COLORS['border']}; border-radius: 6px;"
        )
        hero.addWidget(self.image)
        result_box = QVBoxLayout()
        result_label = QLabel("Результат")
        result_label.setObjectName("Muted")
        self.result = QLabel("—")
        self.result.setWordWrap(True)
        self.result.setStyleSheet("font-size: 17px; font-weight: 750;")
        result_box.addWidget(result_label)
        result_box.addWidget(self.result)
        result_box.addStretch()
        hero.addLayout(result_box, 1)
        root.addLayout(hero)

        self.metrics = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(7)
        labels = (
            ("buy", "Покупка"), ("current", "Текущая"),
            ("roi", "ROI"), ("quantity", "Количество"),
            ("total", "Всего"), ("unlock", "Разблокировка"),
            ("state", "Статус"),
        )
        for index, (key, caption) in enumerate(labels):
            row = index // 2
            column = (index % 2) * 2
            label = QLabel(caption)
            label.setObjectName("Muted")
            value = QLabel("—")
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            value.setStyleSheet("font-size: 10px;")
            self.metrics[key] = value
            if key == "state":
                grid.addWidget(label, row, 0)
                grid.addWidget(value, row, 1, 1, 3)
            else:
                grid.addWidget(label, row, column)
                grid.addWidget(value, row, column + 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        root.addLayout(grid)

        self.sell_button = QPushButton("Продать")
        self.sell_button.setProperty("primary", True)
        self.sell_button.setStyleSheet("padding: 6px;")
        self.sell_button.clicked.connect(
            lambda: self.sell_requested.emit(int(self.skin["id"])) if self.skin else None
        )
        root.addWidget(self.sell_button)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.edit_button = QPushButton("Изменить")
        self.market_button = QPushButton("Market")
        self.lis_button = QPushButton("Lis")
        self.edit_button.clicked.connect(
            lambda: self.edit_requested.emit(int(self.skin["id"])) if self.skin else None
        )
        self.market_button.clicked.connect(
            lambda: self.market_requested.emit(str(self.skin["name"])) if self.skin else None
        )
        self.lis_button.clicked.connect(
            lambda: self.lis_requested.emit(str(self.skin["name"])) if self.skin else None
        )
        for button in (self.edit_button, self.market_button, self.lis_button):
            button.setStyleSheet("padding: 6px;")
            actions.addWidget(button)
        root.addLayout(actions)

        ranges = QHBoxLayout()
        ranges.addStretch()
        self.range_group = QButtonGroup(self)
        self.range_group.setExclusive(True)
        for label, days in (("Все", None), ("1Г", 365), ("90Д", 90), ("30Д", 30), ("7Д", 7)):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setMaximumWidth(48)
            button.setStyleSheet("padding: 4px 6px; font-size: 10px;")
            if days == 7:
                button.setChecked(True)
            button.clicked.connect(lambda _checked=False, value=days: self._set_range(value))
            self.range_group.addButton(button)
            ranges.addWidget(button)
        root.addLayout(ranges)

        checks = QGridLayout()
        checks.setHorizontalSpacing(6)
        self.series_checks = {}
        for index, (key, label, color, _style) in enumerate(SERIES):
            check = QCheckBox(label)
            check.setChecked(True)
            check.setStyleSheet(f"color: {color}; font-size: 10px;")
            check.toggled.connect(
                lambda visible, series_key=key: self.chart.set_series_visible(series_key, visible)
            )
            checks.addWidget(check, index // 2, index % 2)
            self.series_checks[key] = check
        root.addLayout(checks)

        self.chart = PriceChart()
        self.chart.setFixedHeight(286)
        root.addWidget(self.chart)
        self.loading = QLabel("")
        self.loading.setObjectName("Muted")
        self.loading.setAlignment(Qt.AlignCenter)
        root.addWidget(self.loading)
        self.clear()

    def clear(self):
        self.skin = None
        self.title.setText("Выберите позицию")
        self.status.setText("Справа появятся изображение, показатели и история цен.")
        self.image.clear()
        self.image.setText("Нет изображения")
        self.result.setText("—")
        for value in self.metrics.values():
            value.setText("—")
        self.chart.set_history([])
        for button in (self.sell_button, self.edit_button, self.market_button, self.lis_button):
            button.setEnabled(False)

    def set_skin(self, skin):
        self.skin = dict(skin)
        self.title.setText(str(skin["name"]))
        self.status.setText(str(skin["status"]))
        purchase = float(skin.get("total_buy_price") or 0)
        current = float(skin.get("current_value") or purchase)
        result = (
            float(skin.get("profit") or 0)
            if skin["status"] == "Продан"
            else current - purchase
        )
        pct = float(skin.get("profit_percent") or 0)
        if skin["status"] != "Продан" and purchase:
            pct = result / purchase * 100
        tone = COLORS["profit"] if result >= 0 else COLORS["loss"]
        self.result.setText(
            f"{result:+,.2f} ₽\n({pct:+.2f}%)".replace(",", " ")
        )
        self.result.setStyleSheet(f"font-size: 17px; font-weight: 750; color: {tone};")
        self.metrics["buy"].setText(money(skin.get("buy_price")))
        self.metrics["current"].setText(money(skin.get("current_unit")))
        self.metrics["roi"].setText(f"{pct:+.2f}%")
        self.metrics["quantity"].setText(str(skin.get("quantity") or 0))
        self.metrics["total"].setText(money(skin.get("total_buy_price")))
        self.metrics["unlock"].setText(short_date(skin.get("unlock_date")))
        self.metrics["state"].setText(str(skin["status"]))
        sold = skin["status"] == "Продан"
        self.sell_button.setVisible(not sold)
        self.market_button.setVisible(not sold)
        self.edit_button.setEnabled(True)
        self.lis_button.setEnabled(True)
        self.sell_button.setEnabled(not sold)
        self.market_button.setEnabled(not sold)
        self.image.clear()
        self.image.setText("Загрузка...")

    def set_image_bytes(self, data):
        if not data:
            self.image.setText("Изображение недоступно")
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            self.image.setText("Изображение недоступно")
            return
        self._pixmap = pixmap
        self.image.setPixmap(
            pixmap.scaled(
                self.image.size() - type(self.image.size())(12, 12),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        )

    def set_history(self, rows):
        self.chart.set_history(rows)

    def set_loading(self, message=""):
        self.loading.setText(message)

    def _set_range(self, days):
        self.chart.set_range(days)
        self.range_changed.emit(days)


class AssetDetailPanel(QFrame):
    edit_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setMinimumWidth(300)
        self.setMaximumWidth(390)
        self.asset = None
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)
        eyebrow = QLabel("АКТИВ")
        eyebrow.setObjectName("Muted")
        root.addWidget(eyebrow)
        self.title = QLabel("Выберите актив")
        self.title.setWordWrap(True)
        self.title.setStyleSheet("font-size: 18px; font-weight: 700;")
        root.addWidget(self.title)
        self.image = QLabel("Нет изображения")
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setMinimumHeight(230)
        self.image.setStyleSheet(
            f"background: #121d28; border: 1px solid {COLORS['border']}; border-radius: 7px;"
        )
        root.addWidget(self.image)
        self.result = QLabel("—")
        self.result.setStyleSheet("font-size: 22px; font-weight: 750;")
        root.addWidget(self.result)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        self.info.setObjectName("Muted")
        root.addWidget(self.info)
        root.addStretch()
        self.edit_button = QPushButton("Изменить")
        self.edit_button.clicked.connect(
            lambda: self.edit_requested.emit(int(self.asset["id"])) if self.asset else None
        )
        self.edit_button.setEnabled(False)
        root.addWidget(self.edit_button)

    def set_asset(self, asset):
        self.asset = dict(asset)
        self.title.setText(f"{asset.get('name')} ({asset.get('code')})")
        buy = float(asset.get("buy_price_rub") or 0) * float(asset.get("amount") or 0)
        current = float(asset.get("current_price_rub") or 0) * float(asset.get("amount") or 0)
        result = current - buy
        pct = result / buy * 100 if buy else 0
        tone = COLORS["profit"] if result >= 0 else COLORS["loss"]
        self.result.setText(f"{result:+,.2f} ₽ ({pct:+.2f}%)".replace(",", " "))
        self.result.setStyleSheet(f"font-size: 22px; font-weight: 750; color: {tone};")
        self.info.setText(
            f"Количество: {asset.get('amount')} {asset.get('unit') or ''}\n"
            f"Покупка: {money(buy)}\nТекущая стоимость: {money(current)}"
        )
        self.image.setText("Загрузка...")
        self.edit_button.setEnabled(True)

    def set_image_bytes(self, data):
        pixmap = QPixmap()
        if data and pixmap.loadFromData(data):
            self.image.setPixmap(
                pixmap.scaled(
                    self.image.size() - type(self.image.size())(24, 24),
                    Qt.KeepAspectRatio, Qt.SmoothTransformation,
                )
            )
        else:
            self.image.setText("Изображение недоступно")
