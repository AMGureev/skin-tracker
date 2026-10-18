"""Sale calculations and transactional splitting of inventory lots."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SaleCalculation:
    available_quantity: int
    sold_quantity: int
    remaining_quantity: int
    cost_basis_rub: float
    gross_rub: float
    net_rub: float
    profit_rub: float
    profit_percent: float


@dataclass(frozen=True)
class SaleResult:
    calculation: SaleCalculation
    sold_row_id: int
    remaining_row_id: int | None
    partial: bool


def calculate_skin_sale(
    *,
    available_quantity,
    sold_quantity,
    total_buy_price_rub,
    sell_price_rub,
    with_commission,
    commission_rate=0.1,
):
    available = int(available_quantity)
    sold = int(sold_quantity)
    total_cost = float(total_buy_price_rub or 0)
    unit_sell = float(sell_price_rub or 0)
    if available <= 0:
        raise ValueError("В этой партии нет предметов для продажи.")
    if sold <= 0:
        raise ValueError("Количество для продажи должно быть больше нуля.")
    if sold > available:
        raise ValueError(f"Нельзя продать {sold}: в партии осталось {available}.")
    if unit_sell <= 0:
        raise ValueError("Цена продажи должна быть больше нуля.")

    cost_basis = total_cost * sold / available
    gross = unit_sell * sold
    net = gross * (1.0 - float(commission_rate)) if with_commission else gross
    profit = net - cost_basis
    profit_percent = profit / cost_basis * 100.0 if cost_basis > 0 else 0.0
    return SaleCalculation(
        available_quantity=available,
        sold_quantity=sold,
        remaining_quantity=available - sold,
        cost_basis_rub=cost_basis,
        gross_rub=gross,
        net_rub=net,
        profit_rub=profit,
        profit_percent=profit_percent,
    )


def record_skin_sale(
    conn,
    *,
    skin_id,
    sold_quantity,
    sell_date_iso,
    sell_price_rub,
    with_commission,
    sell_currency,
    sell_usd_rate_rub,
):
    """Record a full sale or split a partially sold lot in one transaction."""
    with conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM skins WHERE id = ?", (int(skin_id),))
        values = cursor.fetchone()
        if not values:
            raise ValueError("Позиция не найдена.")
        columns = [description[0] for description in cursor.description]
        skin = dict(zip(columns, values))
        if skin.get("sell_date"):
            raise ValueError("Эта партия уже продана.")

        calculation = calculate_skin_sale(
            available_quantity=skin["quantity"],
            sold_quantity=sold_quantity,
            total_buy_price_rub=skin["total_buy_price"],
            sell_price_rub=sell_price_rub,
            with_commission=with_commission,
        )
        lot_id = skin.get("purchase_lot_id") or int(skin_id)
        sale_values = {
            "sell_date": sell_date_iso,
            "sell_price": float(sell_price_rub),
            "profit": calculation.profit_rub,
            "profit_percent": calculation.profit_percent,
            "with_sell_commission": int(bool(with_commission)),
            "sell_currency": str(sell_currency or "RUB"),
            "sell_usd_rate_rub": sell_usd_rate_rub,
        }

        if calculation.remaining_quantity == 0:
            assignments = [f"{column} = ?" for column in sale_values if column in columns]
            params = [sale_values[column] for column in sale_values if column in columns]
            if "purchase_lot_id" in columns:
                assignments.append("purchase_lot_id = ?")
                params.append(lot_id)
            params.append(int(skin_id))
            cursor.execute(
                f"UPDATE skins SET {', '.join(assignments)} WHERE id = ?",
                params,
            )
            return SaleResult(calculation, int(skin_id), None, False)

        sold_ratio = calculation.sold_quantity / calculation.available_quantity
        sold_total_usd = None
        remaining_total_usd = None
        if skin.get("total_buy_price_usd") is not None:
            total_usd = float(skin["total_buy_price_usd"])
            sold_total_usd = total_usd * sold_ratio
            remaining_total_usd = total_usd - sold_total_usd

        remaining_total_rub = float(skin["total_buy_price"]) - calculation.cost_basis_rub
        original_updates = [
            "quantity = ?",
            "total_buy_price = ?",
        ]
        original_params = [
            calculation.remaining_quantity,
            remaining_total_rub,
        ]
        if "total_buy_price_usd" in columns:
            original_updates.append("total_buy_price_usd = ?")
            original_params.append(remaining_total_usd)
        if "purchase_lot_id" in columns:
            original_updates.append("purchase_lot_id = ?")
            original_params.append(lot_id)
        original_params.append(int(skin_id))
        cursor.execute(
            f"UPDATE skins SET {', '.join(original_updates)} WHERE id = ?",
            original_params,
        )

        sold_skin = dict(skin)
        sold_skin.pop("id", None)
        sold_skin["quantity"] = calculation.sold_quantity
        sold_skin["total_buy_price"] = calculation.cost_basis_rub
        if "total_buy_price_usd" in columns:
            sold_skin["total_buy_price_usd"] = sold_total_usd
        sold_skin.update({key: value for key, value in sale_values.items() if key in columns})
        if "purchase_lot_id" in columns:
            sold_skin["purchase_lot_id"] = lot_id
        if "target_sell_price_rub" in columns:
            sold_skin["target_sell_price_rub"] = None
        if "market_asset_id" in columns:
            sold_skin["market_asset_id"] = None
        if "market_item_id" in columns:
            sold_skin["market_item_id"] = None

        insert_columns = [column for column in columns if column != "id"]
        placeholders = ", ".join("?" for _ in insert_columns)
        cursor.execute(
            f"INSERT INTO skins ({', '.join(insert_columns)}) VALUES ({placeholders})",
            [sold_skin.get(column) for column in insert_columns],
        )
        sold_row_id = int(cursor.lastrowid)
        return SaleResult(calculation, sold_row_id, int(skin_id), True)
