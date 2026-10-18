import re


def classify_cs_item_category(market_hash_name):
    """
    Грубая классификация по market_hash_name для фильтра в UI.
    Возвращает: skin | case | sticker | charm | agent | music | other
    """
    if not market_hash_name:
        return "other"
    n = market_hash_name.strip()
    low = n.lower()

    if "music kit" in low or "набор музыки" in low:
        return "music"
    if "sticker" in low or "наклей" in low or n.startswith("Sticker |"):
        return "sticker"
    if "charm" in low or "брелок" in low or "подвеск" in low:
        return "charm"
    if "agent" in low or re.search(r"\bAgent\b", n):
        return "agent"
    if " case" in low or low.endswith(" case") or "кейс" in low or n.endswith(" Case"):
        return "case"
    return "skin"
