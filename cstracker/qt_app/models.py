"""Qt table models for skins and non-CS assets."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from .theme import COLORS


def money(value, suffix=" ₽"):
    try:
        return f"{float(value):,.2f}{suffix}".replace(",", " ")
    except (TypeError, ValueError):
        return "—"


def short_date(value):
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(str(value)[:10]).strftime("%d.%m.%Y")
    except ValueError:
        return str(value)


class RecordTableModel(QAbstractTableModel):
    columns = ()

    def __init__(self, records=None, parent=None):
        super().__init__(parent)
        self.records = list(records or [])

    def set_records(self, records):
        self.beginResetModel()
        self.records = list(records or [])
        self.endResetModel()

    def record(self, row):
        if 0 <= row < len(self.records):
            return self.records[row]
        return None

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.records)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.columns[section][1]
        if role == Qt.DisplayRole and orientation == Qt.Vertical:
            return section + 1
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        record = self.records[index.row()]
        key, _title, formatter = self.columns[index.column()]
        raw = record.get(key)
        if role == Qt.DisplayRole:
            return formatter(raw, record) if formatter else str(raw if raw is not None else "—")
        if role == Qt.UserRole:
            return raw
        if role == Qt.TextAlignmentRole and isinstance(raw, (int, float)):
            return Qt.AlignRight | Qt.AlignVCenter
        if role == Qt.ForegroundRole:
            return self.foreground(record, key)
        if role == Qt.ToolTipRole:
            return str(raw) if raw is not None else None
        return None

    def foreground(self, record, key):
        return None


class SkinTableModel(RecordTableModel):
    columns = (
        ("name", "Название", None),
        ("quantity", "Кол-во", None),
        ("buy_price", "Цена за шт.", lambda value, _: money(value)),
        ("total_buy_price", "Всего", lambda value, _: money(value)),
        ("buy_date", "Покупка", lambda value, _: short_date(value)),
        ("unlock_date", "Разблокировка", lambda value, _: short_date(value)),
        ("profit", "Профит", lambda value, _: money(value) if value is not None else "—"),
        ("status", "Статус", None),
        ("order_buy", "Order Buy", lambda value, _: money(value) if value else "—"),
        ("order_sell", "Order Sell", lambda value, _: money(value) if value else "—"),
        ("growth_pct", "Рост", lambda value, _: f"{value:+.1f}%" if value is not None else "—"),
        ("target_sell_price_rub", "Цель", lambda value, _: money(value) if value else "—"),
        ("buy_price_usd", "Покупка $", lambda value, _: money(value, " $") if value else "—"),
        ("usd_rate_rub", "Курс ₽/$", lambda value, _: f"{float(value):.2f}" if value else "—"),
    )

    def foreground(self, record, key):
        if key in {"profit", "growth_pct"}:
            value = record.get(key)
            if value is not None:
                return QColor(COLORS["profit"] if float(value) >= 0 else COLORS["loss"])
        if key == "status":
            tone = {
                "Продан": COLORS["muted"],
                "Доступен к продаже": COLORS["profit"],
                "Ждет разблокировки": COLORS["warning"],
            }.get(record.get("status"))
            return QColor(tone) if tone else None
        return None


class AssetTableModel(RecordTableModel):
    columns = (
        ("name", "Название", None),
        ("code", "Код", None),
        ("amount", "Количество", lambda value, _: f"{float(value):,.4f}".rstrip("0").rstrip(".")),
        ("unit", "Ед.", None),
        ("buy_price_rub", "Покупка ₽", lambda value, _: money(value)),
        ("current_price_rub", "Текущая ₽", lambda value, _: money(value)),
        ("value_rub", "Стоимость ₽", lambda value, _: money(value)),
        ("growth_pct", "Рост", lambda value, _: f"{value:+.1f}%" if value is not None else "—"),
        ("target_sell_price_rub", "Цель", lambda value, _: money(value) if value else "—"),
        ("updated_at", "Обновлено", None),
    )

    def foreground(self, record, key):
        if key == "growth_pct" and record.get(key) is not None:
            return QColor(COLORS["profit"] if float(record[key]) >= 0 else COLORS["loss"])
        return None
