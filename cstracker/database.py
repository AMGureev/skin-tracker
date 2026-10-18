from datetime import datetime


_SKINS_OPTIONAL_COLUMNS = [
    ("sell_date", "TEXT"),
    ("sell_price", "REAL"),
    ("profit", "REAL"),
    ("profit_percent", "REAL"),
    ("with_commission", "INTEGER NOT NULL DEFAULT 1"),
    ("with_sell_commission", "INTEGER NOT NULL DEFAULT 0"),
    ("sell_currency", "TEXT DEFAULT 'RUB'"),
    ("buy_price_usd", "REAL"),
    ("total_buy_price_usd", "REAL"),
    ("usd_rate_rub", "REAL"),
    ("market_asset_id", "TEXT"),
    ("market_item_id", "TEXT"),
    ("target_sell_price_rub", "REAL"),
    ("sell_usd_rate_rub", "REAL"),
    ("purchase_lot_id", "INTEGER"),
]

_ASSETS_OPTIONAL_COLUMNS = [
    ("name", "TEXT"),
    ("unit", "TEXT"),
    ("buy_price_usd", "REAL"),
    ("buy_price_rub", "REAL"),
    ("current_price_usd", "REAL"),
    ("current_price_rub", "REAL"),
    ("target_sell_price_rub", "REAL"),
    ("updated_at", "TEXT"),
    ("notes", "TEXT"),
]


def _existing_columns(conn, table):
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def _ensure_columns(conn, table, definitions):
    cur = conn.cursor()
    have = _existing_columns(conn, table)
    for col, decl in definitions:
        if col not in have:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            have.add(col)
    conn.commit()


def create_database(conn):
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS skins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            buy_price REAL NOT NULL,
            total_buy_price REAL NOT NULL,
            buy_date TEXT NOT NULL,
            unlock_date TEXT NOT NULL,
            sell_date TEXT,
            sell_price REAL,
            profit REAL,
            profit_percent REAL,
            with_commission INTEGER NOT NULL DEFAULT 1,
            with_sell_commission INTEGER NOT NULL DEFAULT 0,
            sell_currency TEXT DEFAULT 'RUB',
            buy_price_usd REAL,
            total_buy_price_usd REAL,
            usd_rate_rub REAL,
            market_asset_id TEXT,
            market_item_id TEXT,
            target_sell_price_rub REAL,
            sell_usd_rate_rub REAL,
            purchase_lot_id INTEGER
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_group TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            amount REAL NOT NULL DEFAULT 0,
            unit TEXT,
            buy_price_usd REAL,
            buy_price_rub REAL,
            current_price_usd REAL,
            current_price_rub REAL,
            target_sell_price_rub REAL,
            updated_at TEXT,
            notes TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS inventory_value_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            captured_at TEXT NOT NULL,
            inventory_buy_cost_rub REAL NOT NULL DEFAULT 0,
            inventory_sell_gross_rub REAL NOT NULL DEFAULT 0,
            inventory_buyorder_gross_rub REAL NOT NULL DEFAULT 0,
            priced_sell_count INTEGER NOT NULL DEFAULT 0,
            priced_buy_count INTEGER NOT NULL DEFAULT 0,
            total_positions INTEGER NOT NULL DEFAULT 0
        )
        """
    )
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
    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_item_price_history_skin_time
        ON item_price_history (skin_id, captured_at)
        """
    )
    conn.commit()


def _migrate_skin_dates(conn):
    """Даты вида дд.мм.гггг → YYYY-MM-DD для совместимости со старыми базами."""
    cur = conn.cursor()
    cur.execute("SELECT id, buy_date, unlock_date, sell_date FROM skins")
    rows = cur.fetchall()
    for rid, buy_date, unlock_date, sell_date in rows:
        updates = []
        vals = []
        for col, val in (
            ("buy_date", buy_date),
            ("unlock_date", unlock_date),
            ("sell_date", sell_date),
        ):
            if not val or not isinstance(val, str):
                continue
            if len(val) == 10 and val[2] == "." and val[5] == ".":
                try:
                    iso = datetime.strptime(val, "%d.%m.%Y").strftime("%Y-%m-%d")
                    updates.append(f"{col} = ?")
                    vals.append(iso)
                except ValueError:
                    pass
        if updates:
            vals.append(rid)
            cur.execute(
                f"UPDATE skins SET {', '.join(updates)} WHERE id = ?",
                vals,
            )
    conn.commit()


def migrate_database_if_needed(conn):
    create_database(conn)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='skins'")
    if cur.fetchone():
        _ensure_columns(conn, "skins", _SKINS_OPTIONAL_COLUMNS)
        _migrate_skin_dates(conn)
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='assets'")
    if cur.fetchone():
        _ensure_columns(conn, "assets", _ASSETS_OPTIONAL_COLUMNS)
