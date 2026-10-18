"""Data-entry dialogs. They validate values and return plain dictionaries."""

from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
)


def _money_spin(maximum=1_000_000_000):
    widget = QDoubleSpinBox()
    widget.setDecimals(2)
    widget.setRange(0, maximum)
    widget.setGroupSeparatorShown(True)
    widget.setSuffix(" ₽")
    return widget


def _date_edit(value=None):
    widget = QDateEdit()
    widget.setCalendarPopup(True)
    parsed = QDate.fromString(str(value or ""), "yyyy-MM-dd")
    widget.setDate(parsed if parsed.isValid() else QDate.currentDate())
    widget.setDisplayFormat("dd.MM.yyyy")
    return widget


class BaseFormDialog(QDialog):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(470)
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(22, 20, 22, 18)
        self.root.setSpacing(14)
        self.form = QFormLayout()
        self.form.setSpacing(11)
        self.root.addLayout(self.form)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        self.buttons.button(QDialogButtonBox.Save).setText("Сохранить")
        self.buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.root.addWidget(self.buttons)

    def fail(self, message):
        QMessageBox.warning(self, "Проверьте данные", message)


class SkinDialog(BaseFormDialog):
    def __init__(self, skin=None, usd_rate=None, parent=None):
        super().__init__("Редактировать позицию" if skin else "Добавить позицию", parent)
        skin = skin or {}
        self.name = QLineEdit(str(skin.get("name") or ""))
        self.name.setPlaceholderText("P90 | Sunset Lily (Factory New)")
        self.quantity = QSpinBox()
        self.quantity.setRange(1, 100_000)
        self.quantity.setValue(int(skin.get("quantity") or 1))
        self.buy_rub = _money_spin()
        self.buy_rub.setValue(float(skin.get("buy_price") or 0))
        self.rate = QDoubleSpinBox()
        self.rate.setDecimals(4)
        self.rate.setRange(0, 100_000)
        self.rate.setSuffix(" ₽/$")
        self.rate.setValue(float(skin.get("usd_rate_rub") or usd_rate or 0))
        self.buy_usd = QDoubleSpinBox()
        self.buy_usd.setDecimals(4)
        self.buy_usd.setRange(0, 100_000_000)
        self.buy_usd.setSuffix(" $")
        saved_usd = skin.get("buy_price_usd")
        self.buy_usd.setValue(float(saved_usd or 0))
        self.buy_usd.setReadOnly(True)
        self.buy_date = _date_edit(skin.get("buy_date"))
        default_unlock = date.today() + timedelta(days=7)
        self.unlock_date = _date_edit(skin.get("unlock_date") or default_unlock.isoformat())
        self.target = _money_spin()
        self.target.setValue(float(skin.get("target_sell_price_rub") or 0))
        self.commission = QCheckBox("Учитывать комиссию Market 10%")
        self.commission.setChecked(bool(skin.get("with_commission", 1)))

        for label, widget in (
            ("Название", self.name),
            ("Количество", self.quantity),
            ("Цена за штуку", self.buy_rub),
            ("Курс покупки", self.rate),
            ("Покупка за штуку $", self.buy_usd),
            ("Дата покупки", self.buy_date),
            ("Разблокировка", self.unlock_date),
            ("Цель продажи", self.target),
            ("", self.commission),
        ):
            self.form.addRow(label, widget)
        self.buy_rub.valueChanged.connect(self._recalculate_usd)
        self.rate.valueChanged.connect(self._recalculate_usd)
        self._recalculate_usd()

    def _recalculate_usd(self):
        rate = self.rate.value()
        self.buy_usd.setValue(self.buy_rub.value() / rate if rate > 0 else 0)

    def accept(self):
        if not self.name.text().strip():
            return self.fail("Укажите название предмета.")
        if self.buy_rub.value() <= 0:
            return self.fail("Цена покупки должна быть больше нуля.")
        super().accept()

    def values(self):
        quantity = self.quantity.value()
        usd = self.buy_usd.value() if self.rate.value() > 0 else None
        return {
            "name": self.name.text().strip(),
            "quantity": quantity,
            "buy_price": self.buy_rub.value(),
            "total_buy_price": self.buy_rub.value() * quantity,
            "buy_date": self.buy_date.date().toString("yyyy-MM-dd"),
            "unlock_date": self.unlock_date.date().toString("yyyy-MM-dd"),
            "with_commission": int(self.commission.isChecked()),
            "buy_price_usd": usd,
            "total_buy_price_usd": usd * quantity if usd is not None else None,
            "usd_rate_rub": self.rate.value() or None,
            "target_sell_price_rub": self.target.value() or None,
        }


class SaleDialog(BaseFormDialog):
    def __init__(self, skin, usd_rate=None, parent=None):
        super().__init__("Продажа позиции", parent)
        self.skin = skin
        heading = QLabel(f"{skin['name']}\nВ партии: {skin['quantity']} шт.")
        heading.setStyleSheet("font-size: 16px; font-weight: 700;")
        self.root.insertWidget(0, heading)
        self.quantity = QSpinBox()
        self.quantity.setRange(1, int(skin["quantity"]))
        self.quantity.setValue(int(skin["quantity"]))
        self.currency = QComboBox()
        self.currency.addItems(["RUB", "USD"])
        self.price = QDoubleSpinBox()
        self.price.setDecimals(2)
        self.price.setRange(0, 1_000_000_000)
        self.price.setGroupSeparatorShown(True)
        self.rate = QDoubleSpinBox()
        self.rate.setDecimals(4)
        self.rate.setRange(0, 100_000)
        self.rate.setValue(float(usd_rate or skin.get("usd_rate_rub") or 0))
        self.date = _date_edit()
        self.commission = QCheckBox("Вычесть комиссию 10%")
        self.commission.setChecked(True)
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        self.preview.setStyleSheet("font-size: 15px; font-weight: 650;")
        for label, widget in (
            ("Продать количество", self.quantity),
            ("Валюта цены", self.currency),
            ("Цена за штуку", self.price),
            ("Курс ₽/$", self.rate),
            ("Дата продажи", self.date),
            ("", self.commission),
            ("Результат", self.preview),
        ):
            self.form.addRow(label, widget)
        for control in (self.quantity, self.price, self.rate):
            control.valueChanged.connect(self._preview)
        self.currency.currentTextChanged.connect(self._preview)
        self.commission.toggled.connect(self._preview)
        self._preview()

    def _rub_price(self):
        return self.price.value() * self.rate.value() if self.currency.currentText() == "USD" else self.price.value()

    def _preview(self):
        sold = self.quantity.value()
        cost = float(self.skin["total_buy_price"]) * sold / int(self.skin["quantity"])
        gross = self._rub_price() * sold
        net = gross * 0.9 if self.commission.isChecked() else gross
        profit = net - cost
        pct = profit / cost * 100 if cost else 0
        self.preview.setText(
            f"{profit:+,.2f} ₽ ({pct:+.2f}%)\nОстанется: {int(self.skin['quantity']) - sold} шт."
            .replace(",", " ")
        )
        self.preview.setStyleSheet(
            f"font-size: 15px; font-weight: 650; color: {'#34d399' if profit >= 0 else '#ff4d5f'};"
        )

    def accept(self):
        if self.price.value() <= 0:
            return self.fail("Укажите цену продажи.")
        if self.currency.currentText() == "USD" and self.rate.value() <= 0:
            return self.fail("Для продажи в долларах нужен курс.")
        super().accept()

    def values(self):
        return {
            "sold_quantity": self.quantity.value(),
            "sell_date_iso": self.date.date().toString("yyyy-MM-dd"),
            "sell_price_rub": self._rub_price(),
            "with_commission": self.commission.isChecked(),
            "sell_currency": self.currency.currentText(),
            "sell_usd_rate_rub": self.rate.value() or None,
        }


class SoldSkinDialog(BaseFormDialog):
    def __init__(self, skin, usd_rate=None, parent=None):
        super().__init__("Редактировать продажу", parent)
        self.skin = skin
        self.sell_date = _date_edit(skin.get("sell_date"))
        self.sell_price = _money_spin()
        self.sell_price.setValue(float(skin.get("sell_price") or 0))
        self.currency = QComboBox()
        self.currency.addItems(["RUB", "USD"])
        self.currency.setCurrentText(str(skin.get("sell_currency") or "RUB"))
        self.rate = QDoubleSpinBox()
        self.rate.setDecimals(4)
        self.rate.setRange(0, 100_000)
        self.rate.setSuffix(" ₽/$")
        self.rate.setValue(float(skin.get("sell_usd_rate_rub") or usd_rate or 0))
        self.commission = QCheckBox("Вычесть комиссию 10%")
        self.commission.setChecked(bool(skin.get("with_sell_commission", 0)))
        self.preview = QLabel()
        self.preview.setStyleSheet("font-size: 15px; font-weight: 650;")
        for label, widget in (
            ("Дата продажи", self.sell_date),
            ("Цена за штуку, ₽", self.sell_price),
            ("Исходная валюта", self.currency),
            ("Курс при продаже", self.rate),
            ("", self.commission),
            ("Результат", self.preview),
        ):
            self.form.addRow(label, widget)
        self.sell_price.valueChanged.connect(self._preview)
        self.commission.toggled.connect(self._preview)
        self._preview()

    def _calculation(self):
        cost = float(self.skin.get("total_buy_price") or 0)
        gross = self.sell_price.value() * int(self.skin.get("quantity") or 0)
        net = gross * 0.9 if self.commission.isChecked() else gross
        profit = net - cost
        pct = profit / cost * 100 if cost else 0.0
        return profit, pct

    def _preview(self):
        profit, pct = self._calculation()
        self.preview.setText(
            f"{profit:+,.2f} ₽ ({pct:+.2f}%)".replace(",", " ")
        )
        self.preview.setStyleSheet(
            f"font-size: 15px; font-weight: 650; color: {'#34d399' if profit >= 0 else '#ff4d5f'};"
        )

    def accept(self):
        if self.sell_price.value() <= 0:
            return self.fail("Цена продажи должна быть больше нуля.")
        if self.currency.currentText() == "USD" and self.rate.value() <= 0:
            return self.fail("Для продажи в долларах нужен курс.")
        super().accept()

    def values(self):
        profit, pct = self._calculation()
        return {
            "sell_date": self.sell_date.date().toString("yyyy-MM-dd"),
            "sell_price": self.sell_price.value(),
            "profit": profit,
            "profit_percent": pct,
            "with_sell_commission": int(self.commission.isChecked()),
            "sell_currency": self.currency.currentText(),
            "sell_usd_rate_rub": self.rate.value() or None,
        }


class AssetDialog(BaseFormDialog):
    def __init__(self, asset=None, parent=None):
        super().__init__("Редактировать актив" if asset else "Добавить актив", parent)
        asset = asset or {}
        self.group = QComboBox()
        self.group.addItems(["CURRENCY", "METAL", "OTHER"])
        self.group.setCurrentText(str(asset.get("asset_group") or "CURRENCY"))
        self.code = QLineEdit(str(asset.get("code") or ""))
        self.name = QLineEdit(str(asset.get("name") or ""))
        self.amount = QDoubleSpinBox()
        self.amount.setDecimals(6)
        self.amount.setRange(0, 1_000_000_000)
        self.amount.setValue(float(asset.get("amount") or 0))
        self.unit = QLineEdit(str(asset.get("unit") or "шт."))
        self.buy_usd = QDoubleSpinBox()
        self.buy_usd.setRange(0, 1_000_000_000)
        self.buy_usd.setDecimals(4)
        self.buy_usd.setValue(float(asset.get("buy_price_usd") or 0))
        self.buy_rub = _money_spin()
        self.buy_rub.setValue(float(asset.get("buy_price_rub") or 0))
        self.current_usd = QDoubleSpinBox()
        self.current_usd.setRange(0, 1_000_000_000)
        self.current_usd.setDecimals(4)
        self.current_usd.setValue(float(asset.get("current_price_usd") or 0))
        self.current_rub = _money_spin()
        self.current_rub.setValue(float(asset.get("current_price_rub") or 0))
        self.target = _money_spin()
        self.target.setValue(float(asset.get("target_sell_price_rub") or 0))
        self.notes = QTextEdit(str(asset.get("notes") or ""))
        self.notes.setMaximumHeight(75)
        for label, widget in (
            ("Тип", self.group), ("Код", self.code), ("Название", self.name),
            ("Количество", self.amount), ("Единица", self.unit),
            ("Цена покупки $", self.buy_usd), ("Цена покупки ₽", self.buy_rub),
            ("Текущая цена $", self.current_usd), ("Текущая цена ₽", self.current_rub),
            ("Цель продажи", self.target), ("Заметки", self.notes),
        ):
            self.form.addRow(label, widget)

    def accept(self):
        if not self.code.text().strip() or not self.name.text().strip():
            return self.fail("Укажите код и название актива.")
        if self.amount.value() <= 0:
            return self.fail("Количество должно быть больше нуля.")
        super().accept()

    def values(self):
        return {
            "asset_group": self.group.currentText(),
            "code": self.code.text().strip().upper(),
            "name": self.name.text().strip(),
            "amount": self.amount.value(),
            "unit": self.unit.text().strip(),
            "buy_price_usd": self.buy_usd.value() or None,
            "buy_price_rub": self.buy_rub.value() or None,
            "current_price_usd": self.current_usd.value() or None,
            "current_price_rub": self.current_rub.value() or None,
            "target_sell_price_rub": self.target.value() or None,
            "updated_at": None,
            "notes": self.notes.toPlainText().strip(),
        }


class SettingsDialog(BaseFormDialog):
    def __init__(self, repository, parent=None):
        super().__init__("Настройки", parent)
        self.repository = repository
        self.lis_key = QLineEdit(repository.setting("lis_skins_api_key", ""))
        self.lis_key.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        self.market_key = QLineEdit(repository.setting("market_csgo_api_key", ""))
        self.market_key.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        self.auto_market = QCheckBox("Обновлять цены Market при запуске")
        self.auto_market.setChecked(repository.setting("auto_fetch_skins_on_entry", "0") == "1")
        self.form.addRow("Lis Skins API", self.lis_key)
        self.form.addRow("Market API", self.market_key)
        self.form.addRow("", self.auto_market)

    def accept(self):
        self.repository.set_setting("lis_skins_api_key", self.lis_key.text().strip())
        self.repository.set_setting("market_csgo_api_key", self.market_key.text().strip())
        self.repository.set_setting(
            "auto_fetch_skins_on_entry", "1" if self.auto_market.isChecked() else "0"
        )
        super().accept()
