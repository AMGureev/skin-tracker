"""SQLite persistence and portfolio calculations for every UI frontend."""

from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timedelta
import sqlite3

from .database import migrate_database_if_needed
from .sales import record_skin_sale


MARKET_COMMISSION = 0.10


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _integer(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def skin_status(unlock_date, sell_date):
    if sell_date and str(sell_date) not in {"", "-"}:
        return "Продан"
    try:
        unlock = datetime.fromisoformat(str(unlock_date)[:10]).date()
    except (TypeError, ValueError):
        return "Неизвестно"
    return "Ждет разблокировки" if unlock > date.today() else "Доступен к продаже"


class PortfolioRepository:
    """Owns persistence. Connections are short-lived and thread-local."""

    def __init__(self, db_path):
        self.db_path = str(db_path)
        with self.connection() as conn:
            migrate_database_if_needed(conn)

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.db_path, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def setting(self, key, default=""):
        with self.connection() as conn:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = ?", (str(key),)
            ).fetchone()
        return row["value"] if row else default

    def set_setting(self, key, value):
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO app_settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(key), str(value)),
            )

    def list_skins(self):
        with self.connection() as conn:
            rows = conn.execute("SELECT * FROM skins ORDER BY id DESC").fetchall()
        result = []
        for raw in rows:
            item = dict(raw)
            item["status"] = skin_status(item.get("unlock_date"), item.get("sell_date"))
            result.append(item)
        return result

    def get_skin(self, skin_id):
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM skins WHERE id = ?", (int(skin_id),)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["status"] = skin_status(item.get("unlock_date"), item.get("sell_date"))
        return item

    def add_skin(self, values):
        columns = (
            "name", "quantity", "buy_price", "total_buy_price", "buy_date",
            "unlock_date", "with_commission", "buy_price_usd",
            "total_buy_price_usd", "usd_rate_rub", "target_sell_price_rub",
        )
        payload = [values.get(column) for column in columns]
        with self.connection() as conn:
            cur = conn.execute(
                f"INSERT INTO skins ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                payload,
            )
            skin_id = int(cur.lastrowid)
            conn.execute(
                "UPDATE skins SET purchase_lot_id = ? WHERE id = ?", (skin_id, skin_id)
            )
        return skin_id

    def update_skin(self, skin_id, values):
        allowed = {
            "name", "quantity", "buy_price", "total_buy_price", "buy_date",
            "unlock_date", "buy_price_usd", "total_buy_price_usd", "usd_rate_rub",
            "target_sell_price_rub", "market_asset_id", "market_item_id",
            "sell_date", "sell_price", "profit", "profit_percent",
            "with_sell_commission", "sell_currency", "sell_usd_rate_rub",
        }
        payload = {key: value for key, value in values.items() if key in allowed}
        if not payload:
            return
        assignments = ", ".join(f"{key} = ?" for key in payload)
        with self.connection() as conn:
            conn.execute(
                f"UPDATE skins SET {assignments} WHERE id = ?",
                (*payload.values(), int(skin_id)),
            )

    def bind_market_item(self, skin_id, asset_id, item_id):
        self.update_skin(
            skin_id,
            {"market_asset_id": str(asset_id), "market_item_id": str(item_id)},
        )

    def clear_market_item(self, skin_id):
        self.update_skin(
            skin_id, {"market_asset_id": None, "market_item_id": None}
        )

    def delete_skins(self, skin_ids):
        ids = [int(value) for value in skin_ids]
        if not ids:
            return
        placeholders = ", ".join("?" for _ in ids)
        with self.connection() as conn:
            conn.execute(f"DELETE FROM skins WHERE id IN ({placeholders})", ids)

    def sell_skin(self, skin_id, **values):
        with self.connection() as conn:
            return record_skin_sale(conn, skin_id=skin_id, **values)

    def list_assets(self):
        with self.connection() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM assets ORDER BY id DESC")]

    def get_asset(self, asset_id):
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM assets WHERE id = ?", (int(asset_id),)).fetchone()
        return dict(row) if row else None

    def save_asset(self, values, asset_id=None):
        columns = (
            "asset_group", "code", "name", "amount", "unit", "buy_price_usd",
            "buy_price_rub", "current_price_usd", "current_price_rub",
            "target_sell_price_rub", "updated_at", "notes",
        )
        payload = [values.get(column) for column in columns]
        with self.connection() as conn:
            if asset_id is None:
                cur = conn.execute(
                    f"INSERT INTO assets ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                    payload,
                )
                return int(cur.lastrowid)
            conn.execute(
                f"UPDATE assets SET {', '.join(f'{column} = ?' for column in columns)} WHERE id = ?",
                (*payload, int(asset_id)),
            )
            return int(asset_id)

    def delete_assets(self, asset_ids):
        ids = [int(value) for value in asset_ids]
        if not ids:
            return
        placeholders = ", ".join("?" for _ in ids)
        with self.connection() as conn:
            conn.execute(f"DELETE FROM assets WHERE id IN ({placeholders})", ids)

    def update_asset_quotes(self, quotes):
        now = datetime.now().isoformat(timespec="seconds")
        with self.connection() as conn:
            rows = conn.execute("SELECT id, code FROM assets").fetchall()
            for row in rows:
                quote = quotes.get(str(row["code"]).upper())
                if not quote:
                    continue
                conn.execute(
                    """
                    UPDATE assets
                    SET current_price_usd = ?, current_price_rub = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (quote.get("usd"), quote.get("rub"), now, row["id"]),
                )

    def inventory_metrics(self, buy_prices=None, sell_prices=None, usd_rate=None):
        buy_prices = buy_prices or {}
        sell_prices = sell_prices or {}
        rows = self.list_skins()
        open_rows = [row for row in rows if row["status"] != "Продан"]
        available = [row for row in open_rows if row["status"] == "Доступен к продаже"]
        locked = [row for row in open_rows if row["status"] == "Ждет разблокировки"]
        invested = sum(_number(row.get("total_buy_price")) for row in open_rows)
        sell_value = self._market_value(open_rows, sell_prices)
        buy_value = self._market_value(open_rows, buy_prices)
        available_sell = self._market_value(available, sell_prices)
        available_buy = self._market_value(available, buy_prices)
        locked_sell = self._market_value(locked, sell_prices)
        realized = sum(_number(row.get("profit")) for row in rows if row["status"] == "Продан")
        realized_usd = 0.0
        realized_without_rate = 0
        for row in rows:
            if row["status"] != "Продан":
                continue
            rate = _number(row.get("sell_usd_rate_rub"))
            if rate > 0:
                realized_usd += _number(row.get("profit")) / rate
            else:
                realized_without_rate += 1

        exact_cost_usd = 0.0
        missing_usd = 0
        for row in open_rows:
            value = row.get("total_buy_price_usd")
            if value is not None and _number(value) > 0:
                exact_cost_usd += _number(value)
            elif usd_rate and usd_rate > 0:
                exact_cost_usd += _number(row.get("total_buy_price")) / usd_rate
            else:
                missing_usd += 1

        return {
            "invested": invested,
            "invested_usd": exact_cost_usd if not missing_usd else None,
            "sell_value": sell_value,
            "buy_value": buy_value,
            "sell_profit": sell_value - invested if sell_value else 0.0,
            "buy_profit": buy_value - invested if buy_value else 0.0,
            "realized": realized,
            "realized_usd": realized_usd,
            "realized_average_rate": (
                realized / realized_usd if abs(realized_usd) > 0.000001 else None
            ),
            "realized_without_rate": realized_without_rate,
            "available_count": len(available),
            "available_quantity": sum(_integer(row.get("quantity")) for row in available),
            "available_sell": available_sell,
            "available_buy": available_buy,
            "locked_count": len(locked),
            "locked_sell": locked_sell,
            "position_count": len(open_rows),
            "day_sell": self.stable_delta(1, "sell_order_net_rub"),
            "day_buy": self.stable_delta(1, "buy_order_net_rub"),
            "week_sell": self.stable_delta(7, "sell_order_net_rub"),
            "week_buy": self.stable_delta(7, "buy_order_net_rub"),
        }

    @staticmethod
    def _market_value(rows, prices):
        total = 0.0
        for row in rows:
            price = _number(prices.get(str(row.get("name"))))
            if price > 0:
                total += price * _integer(row.get("quantity")) * (1.0 - MARKET_COMMISSION)
        return total

    def save_market_snapshot(self, buy_prices, sell_prices):
        captured_at = datetime.now().isoformat(timespec="seconds")
        open_rows = [row for row in self.list_skins() if row["status"] != "Продан"]
        invested = sum(_number(row.get("total_buy_price")) for row in open_rows)
        buy_gross = sum(
            _number(buy_prices.get(row["name"])) * _integer(row["quantity"]) for row in open_rows
        )
        sell_gross = sum(
            _number(sell_prices.get(row["name"])) * _integer(row["quantity"]) for row in open_rows
        )
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO inventory_value_history (
                    captured_at, inventory_buy_cost_rub, inventory_sell_gross_rub,
                    inventory_buyorder_gross_rub, priced_sell_count,
                    priced_buy_count, total_positions
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    captured_at, invested, sell_gross, buy_gross,
                    sum(1 for row in open_rows if sell_prices.get(row["name"])),
                    sum(1 for row in open_rows if buy_prices.get(row["name"])),
                    len(open_rows),
                ),
            )
            for row in open_rows:
                buy_gross_item = _number(buy_prices.get(row["name"])) or None
                sell_gross_item = _number(sell_prices.get(row["name"])) or None
                conn.execute(
                    """
                    INSERT INTO item_price_history (
                        captured_at, skin_id, skin_name, quantity, buy_price_rub,
                        total_buy_price_rub, buy_order_gross_rub, sell_order_gross_rub,
                        buy_order_net_rub, sell_order_net_rub, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        captured_at, row["id"], row["name"], row["quantity"],
                        row["buy_price"], row["total_buy_price"], buy_gross_item,
                        sell_gross_item,
                        buy_gross_item * 0.9 if buy_gross_item else None,
                        sell_gross_item * 0.9 if sell_gross_item else None,
                        row["status"],
                    ),
                )
        return captured_at

    def save_lis_snapshot(self, skin_id, skin_name, info, usd_rate):
        if not info or not info.get("price_usd"):
            return
        captured_at = datetime.now().isoformat(timespec="seconds")
        price_usd = _number(info["price_usd"])
        price_rub = price_usd * _number(usd_rate) if usd_rate else None
        with self.connection() as conn:
            latest = conn.execute(
                """
                SELECT * FROM item_price_history
                WHERE skin_id = ? ORDER BY captured_at DESC LIMIT 1
                """,
                (int(skin_id),),
            ).fetchone()
            base = dict(latest) if latest else {}
            if not base:
                skin = conn.execute(
                    "SELECT * FROM skins WHERE id = ?", (int(skin_id),)
                ).fetchone()
                if skin:
                    skin = dict(skin)
                    base = {
                        "quantity": skin.get("quantity", 0),
                        "buy_price_rub": skin.get("buy_price", 0),
                        "total_buy_price_rub": skin.get("total_buy_price", 0),
                        "status": skin_status(
                            skin.get("unlock_date"), skin.get("sell_date")
                        ),
                    }
            conn.execute(
                """
                INSERT INTO item_price_history (
                    captured_at, skin_id, skin_name, quantity, buy_price_rub,
                    total_buy_price_rub, buy_order_gross_rub, sell_order_gross_rub,
                    buy_order_net_rub, sell_order_net_rub, lis_min_price_usd,
                    lis_min_price_rub, lis_min_price_fee_rub, lis_item_class_id,
                    skin_image_url, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    captured_at, int(skin_id), str(skin_name),
                    base.get("quantity", 0), base.get("buy_price_rub", 0),
                    base.get("total_buy_price_rub", 0),
                    base.get("buy_order_gross_rub"), base.get("sell_order_gross_rub"),
                    base.get("buy_order_net_rub"), base.get("sell_order_net_rub"),
                    price_usd, price_rub, price_rub * 1.02 if price_rub else None,
                    info.get("item_class_id"), info.get("image_url"),
                    base.get("status"),
                ),
            )

    def item_history(self, skin_id):
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM item_price_history
                WHERE skin_id = ? ORDER BY captured_at ASC
                """,
                (int(skin_id),),
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_market_prices(self):
        """Restore the last known quote for every name after application restart."""
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT skin_name, buy_order_gross_rub, sell_order_gross_rub
                FROM item_price_history
                ORDER BY captured_at ASC, id ASC
                """
            ).fetchall()
        buy, sell = {}, {}
        for row in rows:
            name = str(row["skin_name"])
            if row["buy_order_gross_rub"] is not None:
                buy[name] = float(row["buy_order_gross_rub"])
            if row["sell_order_gross_rub"] is not None:
                sell[name] = float(row["sell_order_gross_rub"])
        return buy, sell

    def inventory_history(self):
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM inventory_value_history ORDER BY captured_at ASC"
            ).fetchall()
        return [dict(row) for row in rows]

    def stable_delta(self, days, price_column):
        allowed = {"sell_order_net_rub", "buy_order_net_rub"}
        if price_column not in allowed:
            raise ValueError("Unsupported price column")
        current = {
            row["id"]: _integer(row["quantity"])
            for row in self.list_skins()
            if row["status"] == "Доступен к продаже"
        }
        if not current:
            return None
        placeholders = ", ".join("?" for _ in current)
        with self.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT captured_at, skin_id, quantity, {price_column} AS price
                FROM item_price_history
                WHERE skin_id IN ({placeholders}) AND {price_column} IS NOT NULL
                ORDER BY captured_at ASC
                """,
                tuple(current),
            ).fetchall()
        grouped = defaultdict(float)
        counts = defaultdict(set)
        for row in rows:
            sid = _integer(row["skin_id"])
            if current.get(sid) != _integer(row["quantity"]):
                continue
            stamp = str(row["captured_at"])
            grouped[stamp] += _number(row["price"]) * current[sid]
            counts[stamp].add(sid)
        points = []
        for stamp in sorted(grouped):
            if len(counts[stamp]) != len(current):
                continue
            try:
                points.append((datetime.fromisoformat(stamp), grouped[stamp]))
            except ValueError:
                pass
        if len(points) < 2:
            return None
        now, current_value = points[-1]
        candidates = [point for point in points if point[0] <= now - timedelta(days=days)]
        if not candidates or candidates[-1][1] <= 0:
            return None
        previous = candidates[-1][1]
        delta = current_value - previous
        return {"delta": delta, "pct": delta / previous * 100.0}
