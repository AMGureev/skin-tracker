"""Lis Skins API client and public market-link formatting."""

from __future__ import annotations

from datetime import datetime
import re
import threading

import requests

from .images import steam_item_image_url


def build_market_url(name, market_base):
    text = str(name or "").strip().lower()
    text = text.replace("™", "").replace("★", "")
    text = re.sub(r"[|()/]+", " ", text)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    slug = re.sub(r"-+", "-", text).strip("-")
    return f"{market_base}/{slug}/" if slug else market_base


class LisSkinsClient:
    """Small API client responsible for search, matching and quote caching."""

    def __init__(self, api_base, api_key_provider, market_base, session=None, cache_ttl=600):
        self.api_base = api_base.rstrip("/")
        self.market_base = market_base.rstrip("/")
        self._api_key_provider = api_key_provider
        self._session = session or requests.Session()
        self._cache_ttl = int(cache_ttl)
        self._cache = {}
        self._cache_lock = threading.Lock()

    def market_url(self, name):
        return build_market_url(name, self.market_base)

    def search(self, skin_name, *, limit=60):
        api_key = str(self._api_key_provider() or "").strip()
        name = str(skin_name or "").strip()
        if not api_key or not name:
            return []
        try:
            response = self._session.get(
                f"{self.api_base}/market/search",
                headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
                params=[("game", "csgo"), ("names[]", name)],
                timeout=12,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError, TypeError):
            return []
        data = payload.get("data", []) if isinstance(payload, dict) else []
        items = [item for item in data if isinstance(item, dict)]
        return items[:limit] if limit else items

    def lowest_item(self, skin_name):
        name = str(skin_name or "").strip()
        if not name:
            return None
        now = datetime.now()
        with self._cache_lock:
            cached = self._cache.get(name)
        if cached and (now - cached["at"]).total_seconds() < self._cache_ttl:
            return cached["item"]

        candidates = []
        for item in self.search(name):
            if str(item.get("name", "")).strip().casefold() != name.casefold():
                continue
            try:
                price = float(item.get("price") or 0)
            except (TypeError, ValueError):
                price = 0.0
            if price > 0:
                candidates.append((price, item))

        info = None
        if candidates:
            price, item = min(candidates, key=lambda pair: pair[0])
            class_id = item.get("item_class_id")
            info = {
                "price_usd": price,
                "item_id": item.get("id"),
                "item_class_id": str(class_id) if class_id else None,
                "image_url": steam_item_image_url(class_id),
            }
        with self._cache_lock:
            self._cache[name] = {"at": now, "item": info}
        return info

    def lowest_price_usd(self, skin_name):
        item = self.lowest_item(skin_name)
        return item.get("price_usd") if item else None
