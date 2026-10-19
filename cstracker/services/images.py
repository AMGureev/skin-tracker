"""Image URL resolution and cached downloads."""

from __future__ import annotations

import threading
from collections import OrderedDict

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
    """A byte-bounded LRU for previews shared by background workers."""

    def __init__(self, session=None, *, max_cache_bytes=24 * 1024 * 1024,
                 max_image_bytes=5 * 1024 * 1024):
        self._session = session
        self._local = threading.local()
        self._cache = OrderedDict()
        self._cache_bytes = 0
        self._max_cache_bytes = max_cache_bytes
        self._max_image_bytes = max_image_bytes
        self._lock = threading.Lock()

    def download(self, url, *, timeout=12):
        if not url:
            return None
        with self._lock:
            cached = self._cache.get(url)
            if cached is not None:
                self._cache.move_to_end(url)
                return cached

        session = self._session
        if session is None:
            session = getattr(self._local, "session", None)
            if session is None:
                session = self._local.session = requests.Session()
        response = session.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "SkinTracker/1.0"},
            stream=True,
        )
        try:
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").lower()
            if content_type and not content_type.startswith("image/"):
                return None
            declared = response.headers.get("Content-Length")
            if declared:
                try:
                    if int(declared) > self._max_image_bytes:
                        return None
                except ValueError:
                    pass
            chunks = []
            size = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                size += len(chunk)
                if size > self._max_image_bytes:
                    return None
                chunks.append(chunk)
            data = b"".join(chunks)
        finally:
            response.close()
        if not data or len(data) > self._max_cache_bytes:
            return data or None
        with self._lock:
            old = self._cache.pop(url, None)
            if old is not None:
                self._cache_bytes -= len(old)
            while self._cache and self._cache_bytes + len(data) > self._max_cache_bytes:
                _, evicted = self._cache.popitem(last=False)
                self._cache_bytes -= len(evicted)
            self._cache[url] = data
            self._cache_bytes += len(data)
        return data
