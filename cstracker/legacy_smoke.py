"""Isolated executable self-test; never opens the user's portfolio database."""

from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import threading
import time

from matplotlib.backend_bases import MouseEvent


def run(root, tracker_class, output):
    name = "P90 | Sunset Lily (Factory New)"
    owner = threading.get_ident()

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"items": [{"market_hash_name": name, "price": 150}]}

    class SlowHttp:
        def get(self, *args, **kwargs):
            if threading.get_ident() == owner:
                raise RuntimeError("Smoke test detected UI-thread networking")
            time.sleep(0.4)
            return Response()

    class SmokeTracker(tracker_class):
        def fetch_usd_rub_rate(self):
            return 80.0, "self-test"

        def _lis_lowest_item_info(self, skin_name):
            time.sleep(0.25)
            return {"price_usd": 2.0, "item_class_id": None, "image_url": None}

    report = {"ok": False, "backend": "TkAgg", "errors": []}
    root.withdraw()
    root.report_callback_exception = lambda kind, error, tb: report["errors"].append(f"{kind.__name__}: {error}")
    with tempfile.TemporaryDirectory(prefix="tracker-smoke-") as directory:
        app = SmokeTracker(root, db_path=str(Path(directory) / "skins.db"), auto_start=False)
        try:
            app._http = SlowHttp()
            with app.conn:
                cursor = app.conn.execute(
                    """INSERT INTO skins (name, quantity, buy_price, total_buy_price, buy_date,
                       unlock_date, with_commission) VALUES (?, 10, 100, 1000, '2026-01-01', '2026-01-08', 1)""",
                    (name,),
                )
                skin_id = cursor.lastrowid
            start = datetime.now() - timedelta(days=2)
            app.sales_history[name] = [[(start + timedelta(hours=i)).timestamp(), 100 + i] for i in range(24)]
            app.load_data()
            app.tree.selection_set(f"skin:{skin_id}")
            app._render_skin_selection()
            root.update()
            chart = app._skin_price_chart
            chart.canvas.draw()
            x, y = chart.axes.transData.transform((sum(chart.axes.get_xlim()) / 2, sum(chart.axes.get_ylim()) / 2))
            for button in ("up", "down") * 40:
                chart.canvas.callbacks.process("scroll_event", MouseEvent("scroll_event", chart.canvas, x, y, button=button))
            ticks = [time.monotonic()]
            timer = None

            def beat():
                nonlocal timer
                ticks.append(time.monotonic())
                timer = root.after(15, beat)

            timer = root.after(15, beat)
            app.update_market_prices(silent=True)
            deadline = time.monotonic() + 1.5
            while time.monotonic() < deadline:
                root.update()
                time.sleep(0.001)
            root.after_cancel(timer)
            gaps = [right - left for left, right in zip(ticks, ticks[1:])]
            report.update(ticks=len(ticks), max_ui_gap_ms=round(max(gaps) * 1000, 2),
                          market_loaded=app.sell_order_prices.get(name) == 150,
                          selection_preserved=app.tree.selection() == (f"skin:{skin_id}",),
                          chart_reused=app._skin_price_chart is chart)
            report["ok"] = (not report["errors"] and report["market_loaded"]
                            and report["selection_preserved"] and report["chart_reused"]
                            and len(ticks) > 30 and max(gaps) < 0.25)
        except Exception as error:
            report["errors"].append(f"{type(error).__name__}: {error}")
        finally:
            app.close()
    Path(output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1
