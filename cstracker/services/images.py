"""Image URL resolution and cached downloads."""

from __future__ import annotations

import threading

import requests


_METAL_IMAGE_URLS = {
    "XAU": "https://images-of-elements.com/gold-2.jpg",
    "XAG": "https://images-of-elements.com/silver-2.jpg",
    "XPD": "https://images-of-elements.com/palladium-2.jpg",
    "XPT": "https://images-of-elements.com/platinum.jpg",
}

_CURRENCY_IMAGE_URLS = {
    "USD": "https://flagcdn.com/w640/us.png",
    "EUR": "https://flagcdn.com/w640/eu.png",
    "CNY": "https://flagcdn.com/w640/cn.png",
}


def steam_item_image_url(item_class_id, size=360):
    class_id = str(item_class_id or "").strip()
    if not class_id:
        return None
    return (
        "https://community.akamai.steamstatic.com/economy/image/class/730/"
        f"{class_id}/{int(size)}fx{int(size)}f"
    )


def asset_image_url(asset_group, code):
    group = str(asset_group or "").upper()
    asset_code = str(code or "").upper()
    if group in {"METAL", "МЕТАЛЛЫ", "МЕТАЛЛ"}:
        return _METAL_IMAGE_URLS.get(asset_code)
    if group in {"CURRENCY", "ВАЛЮТЫ", "ВАЛЮТА"}:
        return _CURRENCY_IMAGE_URLS.get(asset_code)
    return _METAL_IMAGE_URLS.get(asset_code) or _CURRENCY_IMAGE_URLS.get(asset_code)


class ImageRepository:
    """Loads images once and safely reuses their bytes across UI requests."""

    def __init__(self, session=None):
        self._session = session or requests.Session()
        self._cache = {}
        self._lock = threading.Lock()

    def download(self, url, *, timeout=12):
        if not url:
            return None
        with self._lock:
            cached = self._cache.get(url)
        if cached:
            return cached

        response = self._session.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "SkinTracker/1.0"},
        )
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "")
        if "image" not in content_type.lower() and len(response.content) < 100:
            return None

        data = response.content
        with self._lock:
            self._cache[url] = data
        return data
