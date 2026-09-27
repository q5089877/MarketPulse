"""Fetch public market data for MarketPulse.

This first version intentionally uses no Gemini key. It stores raw snapshots and
simple derived indicators in data/market_data.json for the static dashboard.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "market_data.json"
TZ_TAIPEI = timezone(timedelta(hours=8))


def get_json(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": "MarketPulse/0.1"})
    with urllib.request.urlopen(request, timeout=25) as response:
        return json.loads(response.read().decode("utf-8-sig"))


def yahoo_chart(symbol: str):
    query = urllib.parse.urlencode({"range": "5d", "interval": "1d", "events": "history"})
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol, safe='')}?{query}"
    payload = get_json(url)["chart"]["result"][0]
    meta = payload.get("meta", {})
    quotes = payload.get("indicators", {}).get("quote", [{}])[0]
    closes = [value for value in quotes.get("close", []) if value is not None]
    return {
        "symbol": symbol,
        "currency": meta.get("currency"),
        "latest": closes[-1] if closes else None,
        "previous": closes[-2] if len(closes) > 1 else None,
        "as_of": datetime.fromtimestamp(payload["timestamp"][-1], tz=timezone.utc).astimezone(TZ_TAIPEI).isoformat() if payload.get("timestamp") else None,
    }


def find_twse(rows, key, value):
    return next((row for row in rows if row.get(key) == value), None)


def roc_date_to_gregorian(roc_date: str) -> str:
    return f"{int(roc_date[:3]) + 1911}{roc_date[3:]}"


def main():
    now = datetime.now(TZ_TAIPEI).isoformat()
    errors = []
    output = {"updated_at": now, "status": "ok", "sources": {}, "market": {}, "derived": {}}

    try:
        index_rows = get_json("https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX")
        twii = find_twse(index_rows, "指數", "發行量加權股價指數")
        output["sources"]["twse"] = "https://openapi.twse.com.tw/"
        output["market"]["taiwan_index"] = {
            "value": float(twii["收盤指數"]) if twii and twii.get("收盤指數") else None,
            "change_percent": float(twii["漲跌百分比"]) if twii and twii.get("漲跌百分比") not in (None, "-", "") else None,
            "date": twii.get("日期") if twii else None,
        }
    except Exception as exc:
        errors.append(f"TWSE index: {exc}")

    try:
        index_date = output["market"].get("taiwan_index", {}).get("date")
        report_date = roc_date_to_gregorian(index_date) if index_date else ""
        report = get_json(f"https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date={report_date}&type=MS")
        breadth_table = next((table for table in report.get("tables", []) if table.get("title") == "漲跌證券數合計"), {})
        breadth = {row[0]: row[1] for row in breadth_table.get("data", [])}
        breadth_date = index_date
        output["market"]["taiwan_breadth"] = {
            "data": breadth,
            "date": breadth_date,
            "is_current": bool(breadth),
        }
        output["sources"]["twse_breadth"] = "https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&type=MS"
        if not breadth:
            errors.append(f"TWSE breadth unavailable for {report_date}")
    except Exception as exc:
        errors.append(f"TWSE breadth: {exc}")

    for label, symbol in {
        "sp500": "^GSPC",
        "nasdaq": "^IXIC",
        "vix": "^VIX",
        "us10y_proxy": "^TNX",
        "gold": "GC=F",
        "taiwan_index_market": "^TWII",
    }.items():
        try:
            output["market"][label] = yahoo_chart(symbol)
            output["sources"][label] = "https://finance.yahoo.com/"
        except Exception as exc:
            errors.append(f"Yahoo {symbol}: {exc}")

    # Simple, transparent signals; later versions can replace these with history-based percentiles.
    taiwan = output["market"].get("taiwan_index", {})
    vix = output["market"].get("vix", {}).get("latest")
    sp500 = output["market"].get("sp500", {})
    output["derived"] = {
        "taiwan_daily_change": taiwan.get("change_percent"),
        "vix_level": vix,
        "vix_status": "high" if isinstance(vix, (int, float)) and vix >= 25 else "normal",
        "sp500_daily_change": (sp500.get("latest") - sp500.get("previous")) / sp500.get("previous") * 100 if sp500.get("latest") and sp500.get("previous") else None,
        "note": "Daily public-data snapshot. Yahoo Finance values may be delayed; verify before trading.",
    }
    if errors:
        output["status"] = "partial"
        output["errors"] = errors

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": output["status"], "updated_at": now, "errors": errors}, ensure_ascii=False))


if __name__ == "__main__":
    main()
