from datetime import date
import os
import tempfile
import unittest

from cstracker.qt_app.models import SkinTableModel
from cstracker.repository import PortfolioRepository
from cstracker.services.lis_skins import build_market_url


class RepositoryTest(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.repo = PortfolioRepository(self.path)

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.path + suffix)
            except FileNotFoundError:
                pass

    def add_lot(self, quantity=10):
        return self.repo.add_skin(
            {
                "name": "P90 | Sunset Lily (Factory New)",
                "quantity": quantity,
                "buy_price": 100.0,
                "total_buy_price": 100.0 * quantity,
                "buy_date": "2026-07-01",
                "unlock_date": "2026-07-08",
                "with_commission": 1,
                "buy_price_usd": 1.25,
                "total_buy_price_usd": 1.25 * quantity,
                "usd_rate_rub": 80.0,
                "target_sell_price_rub": 160.0,
            }
        )

    def test_partial_sale_splits_lot_and_preserves_usd_cost(self):
        skin_id = self.add_lot()
        result = self.repo.sell_skin(
            skin_id,
            sold_quantity=4,
            sell_date_iso=date.today().isoformat(),
            sell_price_rub=150.0,
            with_commission=True,
            sell_currency="RUB",
            sell_usd_rate_rub=78.0,
        )
        self.assertTrue(result.partial)
        rows = self.repo.list_skins()
        open_row = next(row for row in rows if row["status"] != "Продан")
        sold_row = next(row for row in rows if row["status"] == "Продан")
        self.assertEqual(open_row["quantity"], 6)
        self.assertEqual(sold_row["quantity"], 4)
        self.assertAlmostEqual(open_row["total_buy_price_usd"], 7.5)
        self.assertAlmostEqual(sold_row["total_buy_price_usd"], 5.0)
        self.assertAlmostEqual(sold_row["profit"], 140.0)

    def test_market_and_lis_snapshots_feed_history(self):
        skin_id = self.add_lot(quantity=2)
        self.repo.save_market_snapshot(
            {"P90 | Sunset Lily (Factory New)": 120.0},
            {"P90 | Sunset Lily (Factory New)": 140.0},
        )
        self.repo.save_lis_snapshot(
            skin_id,
            "P90 | Sunset Lily (Factory New)",
            {
                "price_usd": 2.0,
                "item_class_id": "123",
                "image_url": "https://example.test/image.png",
            },
            80.0,
        )
        history = self.repo.item_history(skin_id)
        self.assertEqual(len(history), 2)
        self.assertAlmostEqual(history[-1]["lis_min_price_fee_rub"], 163.2)
        self.assertEqual(history[-1]["quantity"], 2)
        buy, sell = self.repo.latest_market_prices()
        self.assertEqual(buy["P90 | Sunset Lily (Factory New)"], 120.0)
        self.assertEqual(sell["P90 | Sunset Lily (Factory New)"], 140.0)

    def test_cost_usd_uses_saved_purchase_rate(self):
        self.add_lot(quantity=2)
        metrics = self.repo.inventory_metrics(usd_rate=100.0)
        self.assertAlmostEqual(metrics["invested_usd"], 2.5)


class ModelAndUrlTest(unittest.TestCase):
    def test_model_exposes_numeric_sort_role(self):
        model = SkinTableModel(
            [{"name": "A", "quantity": 5, "buy_price": 10, "status": "Продан"}]
        )
        index = model.index(0, 1)
        self.assertEqual(model.data(index), "5")
        self.assertEqual(model.data(index, 256), 5)

    def test_lis_slug_matches_public_market(self):
        url = build_market_url(
            "Sawed-Off | Jungle Thicket (Factory New)",
            "https://new.lis-skins.com/market/csgo",
        )
        self.assertEqual(
            url,
            "https://new.lis-skins.com/market/csgo/sawed-off-jungle-thicket-factory-new/",
        )


if __name__ == "__main__":
    unittest.main()

