"""Network integrations used by the desktop application."""

from __future__ import annotations

import requests
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET

from .currency import fetch_usd_rub_rate


class MarketDataService:
    def __init__(self, session=None):
        self.session = session or requests.Session()

    def usd_rub_rate(self):
        return fetch_usd_rub_rate(self.session)

    def market_prices(self):
        buy_response = self.session.get(
            "https://market.csgo.com/api/v2/prices/orders/RUB.json", timeout=30
        )
        buy_response.raise_for_status()
        sell_response = self.session.get(
            "https://market.csgo.com/api/v2/prices/RUB.json", timeout=30
        )
        sell_response.raise_for_status()
        buy = self._price_map(buy_response.json())
        sell = self._price_map(sell_response.json())
        return buy, sell

    @staticmethod
    def _price_map(payload):
        result = {}
        for item in (payload or {}).get("items", []):
            name = str(item.get("market_hash_name") or "").strip()
            try:
                price = float(item.get("price") or 0)
            except (TypeError, ValueError):
                price = 0.0
            if name and price > 0:
                result[name] = price
        return result

    def asset_quotes(self, usd_rub):
        quotes = {
            "USD": {"usd": 1.0, "rub": float(usd_rub)},
            "RUB": {"usd": 1.0 / float(usd_rub), "rub": 1.0},
        }
        response = self.session.get(
            "https://www.cbr-xml-daily.ru/daily_json.js", timeout=15
        )
        response.raise_for_status()
        valutes = response.json().get("Valute", {})
        for code, entry in valutes.items():
            value = float(entry.get("Value") or 0)
            nominal = float(entry.get("Nominal") or 1)
            rub = value / nominal
            if rub > 0:
                quotes[code] = {"usd": rub / usd_rub, "rub": rub}

        now = datetime.now()
        today = now.strftime("%d/%m/%Y")
        week_ago = (now - timedelta(days=10)).strftime("%d/%m/%Y")
        metals = self.session.get(
            "https://www.cbr.ru/scripts/xml_metall.asp",
            params={"date_req1": week_ago, "date_req2": today},
            timeout=15,
        )
        metals.raise_for_status()
        aliases = {"1": "XAU", "2": "XAG", "3": "XPT", "4": "XPD"}
        text = metals.text or ""
        start, end = text.find("<Metall"), text.rfind("</Metall>")
        if start >= 0 and end > start:
            root = ET.fromstring(text[start:end + len("</Metall>")])
            latest = {}
            for record in root.findall(".//Record"):
                code = aliases.get(record.attrib.get("Code"))
                raw = (record.findtext("Buy") or "").replace(",", ".")
                try:
                    rub = float(raw)
                except ValueError:
                    continue
                if code and rub > 0:
                    latest[code] = rub
            for code, rub in latest.items():
                quotes[code] = {"usd": rub / usd_rub, "rub": rub}
        return quotes

    def private_get(self, api_key, method, **params):
        response = self.session.get(
            f"https://market.csgo.com/api/v2/{method}",
            params={"key": api_key, **params},
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("success") is False:
            raise RuntimeError(payload.get("error") or payload.get("message") or str(payload))
        return payload

    @staticmethod
    def price_to_api(amount, currency):
        multiplier = 100 if currency == "RUB" else 1000
        return max(1, int(round(float(amount) * multiplier)))

    @staticmethod
    def price_from_api(amount, currency):
        divisor = 100 if currency == "RUB" else 1000
        return float(amount or 0) / divisor
