def parse_positive_float(raw):
    if raw is None:
        return None
    s = str(raw).strip().replace(",", ".")
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return v if v > 0 else None


def parse_optional_usd_rate(raw):
    """Положительный курс: сколько RUB за 1 USD."""
    return parse_positive_float(raw)


def fetch_usd_rub_rate(http_session):
    """
    Курс RUB за 1 USD. Источник: API ЦБ (daily_json).
    Возвращает (курс, описание источника).
    """
    r = http_session.get("https://www.cbr-xml-daily.ru/daily_json.js", timeout=14)
    r.raise_for_status()
    data = r.json()
    usd = (data.get("Valute") or {}).get("USD") or {}
    val = usd.get("Value")
    if not val:
        raise ValueError("В ответе ЦБ нет курса USD")
    return float(val), "cbr-xml-daily.ru"
