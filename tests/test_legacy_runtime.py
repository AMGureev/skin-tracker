from datetime import datetime, timedelta
import os
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch

import requests
from matplotlib.backend_bases import MouseEvent

from cstracker.services.http import WorkerHttpClient
from cstracker.sales import record_skin_sale
from skin_tracker import SkinTracker


class LegacyRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = tk.Tk()
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.rate_patch = patch("skin_tracker.fetch_usd_rub_rate_external", return_value=(80.0, "test"))
        self.rate_patch.start()
        self.app = SkinTracker(self.root, db_path=os.path.join(self.directory.name, "skins.db"), auto_start=False)
        self.pump(0.08)

    def tearDown(self):
        if not self.app._closed:
            self.app.close()
        self.rate_patch.stop()
        self.directory.cleanup()
        self.assertEqual(self.errors, [])

    def pump(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.001)

    def add_skin(self, name="P90 | Sunset Lily (Factory New)", quantity=10):
        with self.app.conn:
            cursor = self.app.conn.execute(
                """INSERT INTO skins (name, quantity, buy_price, total_buy_price, buy_date,
                   unlock_date, with_commission, buy_price_usd, total_buy_price_usd, usd_rate_rub)
                   VALUES (?, ?, 100, ?, '2026-01-01', '2026-01-08', 1, 1.25, ?, 80)""",
                (name, quantity, quantity * 100, quantity * 1.25),
            )
        return cursor.lastrowid

    def select(self, skin_id):
        self.app.load_data()
        self.app.tree.selection_set(f"skin:{skin_id}")
        self.app._on_skins_tree_select()
        self.pump(0.32)

    def heartbeat(self, seconds):
        times = [time.monotonic()]

        def beat():
            times.append(time.monotonic())
            self.root.after(10, beat)

        self.root.after(10, beat)
        self.pump(seconds)
        # Cancel the live timer, not just the first (already fired) handle.
        for pending in self.root.tk.call("after", "info"):
            script = str(self.root.tk.call("after", "info", pending)[0])
            if "beat" in script:
                self.root.after_cancel(pending)
        gaps = [right - left for left, right in zip(times, times[1:])]
        print(f"UI heartbeat: {len(times)} ticks, worst gap {max(gaps, default=0):.3f}s")
        self.assertGreater(len(times), seconds * 30)
        self.assertLess(max(gaps, default=0), 0.25)

    def test_delayed_market_request_keeps_ui_responsive_and_recovers(self):
        skin_id = self.add_skin()
        app = self.app

        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return {"items": [{"market_hash_name": "P90 | Sunset Lily (Factory New)", "price": 150}]}

        def delayed(*args, **kwargs):
            time.sleep(0.4)
            return Response()

        with patch.object(app._http, "get", side_effect=delayed):
            start = time.monotonic()
            app.update_market_prices(silent=True)
            self.assertLess(time.monotonic() - start, 0.1)
            self.heartbeat(1.0)
        self.assertFalse(app._market_prices_running)
        self.assertEqual(app.sell_order_prices["P90 | Sunset Lily (Factory New)"], 150)
        self.assertGreater(app.conn.execute("SELECT COUNT(*) FROM item_price_history WHERE skin_id=?", (skin_id,)).fetchone()[0], 0)
        with patch.object(app._http, "get", side_effect=requests.Timeout("offline")):
            app.update_market_prices(silent=True)
            self.pump(0.08)
        self.assertFalse(app._market_prices_running)

    def test_rate_failure_does_not_repeat_network_from_table_or_kpis(self):
        app = self.app
        app._usd_rub_rate_cache = None
        app._rate_checked_at = 0
        with patch("skin_tracker.fetch_usd_rub_rate_external", side_effect=requests.Timeout("offline")) as fetch:
            app._refresh_usd_rate_footer()
            self.pump(0.08)
            for _ in range(8):
                app.load_data()
                app._compute_and_show_skins_kpis()
                app._refresh_usd_rate_footer()
            self.assertEqual(fetch.call_count, 1)

    def test_selection_survives_reload_and_chart_canvas_is_reused(self):
        skin_id = self.add_skin()
        app = self.app
        start = datetime.now() - timedelta(days=2)
        app.sales_history["P90 | Sunset Lily (Factory New)"] = [
            [(start + timedelta(hours=i)).timestamp(), 100 + i] for i in range(20)
        ]
        with patch.object(app, "_lis_lowest_item_info", return_value=None) as fetch:
            self.select(skin_id)
            chart = app._skin_price_chart
            for _ in range(10):
                app.load_data()
            self.pump(0.2)
            self.assertEqual(app.tree.selection(), (f"skin:{skin_id}",))
            self.assertIs(app._skin_price_chart, chart)
            self.assertEqual(fetch.call_count, 1)
            chart.canvas.draw()
            x, y = chart.axes.transData.transform((sum(chart.axes.get_xlim()) / 2, sum(chart.axes.get_ylim()) / 2))
            for button in ["down"] * 120 + ["up"] * 240:
                event = MouseEvent("scroll_event", chart.canvas, x, y, button=button)
                chart.canvas.callbacks.process("scroll_event", event)
            self.pump(0.08)
            chart.canvas.draw()

    def test_superseded_selection_cannot_replace_current_item(self):
        first = self.add_skin()
        second = self.add_skin("AWP | Graphite (Minimal Wear)")
        app = self.app

        def delayed_info(name):
            time.sleep(0.3 if name.startswith("P90") else 0.05)
            return {"price_usd": 2, "item_class_id": None, "image_url": None}

        with patch.object(app, "_lis_lowest_item_info", side_effect=delayed_info):
            self.select(first)
            self.select(second)
            self.pump(0.4)
        self.assertEqual(app._skin_detail_title.get(), "AWP | Graphite (Minimal Wear)")
        self.assertEqual(app.conn.execute("SELECT COUNT(*) FROM item_price_history WHERE skin_id=?", (first,)).fetchone()[0], 0)
        self.assertEqual(app.conn.execute("SELECT COUNT(*) FROM item_price_history WHERE skin_id=?", (second,)).fetchone()[0], 1)

    def test_search_is_debounced(self):
        app = self.app
        with patch.object(app, "load_data") as load:
            for text in ("P", "P9", "P90", "P90 |", "P90 | Sunset"):
                app.name_search_var.set(text)
            self.pump(0.3)
            self.assertEqual(load.call_count, 1)

    def test_delayed_assets_keep_ui_responsive(self):
        app = self.app
        with app.conn:
            app.conn.execute("INSERT INTO assets (asset_group, code, name, amount, unit, buy_price_rub) VALUES ('CURRENCY', 'USD', 'USD', 10, 'USD', 70)")

        def delayed(pairs):
            time.sleep(0.5)
            return {("CURRENCY", "USD"): (1.0, 80.0)}

        with patch.object(app, "_fetch_assets_api_prices", side_effect=delayed):
            app.update_assets_prices(silent=True)
            self.heartbeat(0.7)
        self.assertEqual(app.conn.execute("SELECT current_price_rub FROM assets").fetchone()[0], 80)

    def test_close_while_request_is_running_does_not_wait(self):
        released = threading.Event()
        app = self.app
        app._tasks.submit("slow-close", lambda: released.wait(3), lambda value: self.fail("Closed UI callback ran"))
        self.pump(0.03)
        start = time.monotonic()
        app.close()
        self.assertLess(time.monotonic() - start, 0.15)
        released.set()

    def test_http_client_rejects_ui_thread_network(self):
        with self.assertRaisesRegex(RuntimeError, "UI thread"):
            WorkerHttpClient().get("https://example.invalid")

    def test_background_results_and_errors_are_delivered_on_ui_thread(self):
        ids, exceptions = [], []
        owner = threading.get_ident()
        self.app._tasks.submit("thread-test", threading.get_ident,
                               lambda worker: ids.append((worker, threading.get_ident())))
        self.app._tasks.submit("error-test", lambda: 1 / 0, error=lambda exc: exceptions.append((type(exc), threading.get_ident())))
        self.pump(0.1)
        self.assertNotEqual(ids[0][0], owner)
        self.assertEqual(ids[0][1], owner)
        self.assertEqual(exceptions, [(ZeroDivisionError, owner)])

    def test_partial_sale_preserves_quantities_usd_cost_and_sale_rate(self):
        skin_id = self.add_skin(quantity=10)
        result = record_skin_sale(self.app.conn, skin_id=skin_id, sold_quantity=5,
                                  sell_date_iso="2026-10-09", sell_price_rub=150,
                                  with_commission=True, sell_currency="RUB", sell_usd_rate_rub=78)
        self.assertTrue(result.partial)
        rows = self.app.conn.execute("SELECT quantity, total_buy_price_usd, sell_usd_rate_rub FROM skins ORDER BY sell_date IS NULL DESC").fetchall()
        self.assertEqual(rows, [(5, 6.25, None), (5, 6.25, 78.0)])

    def test_private_market_action_captures_key_before_worker_starts(self):
        from cstracker.ui.market_actions import MarketActions
        app = self.app
        app._set_app_setting(app.SETTINGS_KEY_MARKET_API, "test-key")
        delivered, worker_threads = [], []

        def get(method, *, api_key, **params):
            self.assertEqual(api_key, "test-key")
            worker_threads.append(threading.get_ident())
            return {"success": True, "items": []}

        with patch.object(app, "_cs_market_get", side_effect=get):
            MarketActions(app).request("items", delivered.append)
            self.pump(0.1)
        self.assertEqual(len(delivered), 1)
        self.assertNotEqual(worker_threads[0], threading.get_ident())

    def test_history_loading_is_async(self):
        skin_id = self.add_skin()
        app = self.app
        app._set_app_setting(app.SETTINGS_KEY_MARKET_API, "test-key")
        with patch.object(app, "_lis_lowest_item_info", return_value=None):
            self.select(skin_id)

        def delayed(*args, **kwargs):
            time.sleep(0.5)
            return [[datetime.now().timestamp(), 125]]

        with patch.object(app, "get_sales_history", side_effect=delayed):
            app.load_single_item_history()
            self.heartbeat(0.7)
        self.assertIn("P90 | Sunset Lily (Factory New)", app.sales_history)

    def test_slow_rate_requests_share_one_job_and_use_ui_callbacks(self):
        app = self.app
        callbacks = []

        def delayed(session):
            time.sleep(0.4)
            return 81.0, "test"

        with patch("skin_tracker.fetch_usd_rub_rate_external", side_effect=delayed) as fetch:
            for _ in range(3):
                app._request_usd_rate(lambda result: callbacks.append(result), force=True)
            app._refresh_usd_rate_footer()
            self.heartbeat(0.6)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(callbacks, [(81.0, "test")] * 3)

    def test_sale_edit_rate_uses_its_own_window(self):
        app = self.app
        app.edit_sell_window = tk.Toplevel(self.root)
        app.edit_exchange_rate_var = tk.StringVar(app.edit_sell_window, value="1")
        app.fetch_exchange_rate_for_edit()
        self.pump(0.1)
        self.assertEqual(app.edit_exchange_rate_var.get(), "80.0000")
        app.edit_sell_window.destroy()


if __name__ == "__main__":
    unittest.main()
