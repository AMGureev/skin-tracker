"""Public configuration; private API keys stay outside the repository."""

import json
import os
from pathlib import Path
import sys


def _read_local_config(path=None):
    if path is None:
        base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
        path = base / "config.local.json"
    try:
        data = Path(path).read_text(encoding="utf-8")
        config = json.loads(data)
    except (OSError, ValueError):
        return {}
    return config if isinstance(config, dict) else {}


_LOCAL_CONFIG = _read_local_config()
API_KEY = os.environ.get("CSMARKET_API_KEY", _LOCAL_CONFIG.get("CSMARKET_API_KEY", "YOUR_API_KEY_HERE"))
LIS_SKINS_API_KEY = os.environ.get("LIS_SKINS_API_KEY", _LOCAL_CONFIG.get("LIS_SKINS_API_KEY", ""))
LIS_SKINS_API_BASE = "https://api.lis-skins.com/v1"
LIS_SKINS_MARKET_BASE = "https://new.lis-skins.com/market/csgo"

COEFFICIENT_BUY_ORDER = 0.9
COEFFICIENT_LIS = 1.02
MARKET_COMMISSION_RATE = 0.1

CATEGORY_FILTER_LABELS = (
    "Все",
    "Скины",
    "Кейсы",
    "Наклейки",
    "Брелки",
    "Агенты",
    "Музыка",
)
