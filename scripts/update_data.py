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
MASTERS_OUT = ROOT / "data" / "masters_analysis.json"
TZ_TAIPEI = timezone(timedelta(hours=8))


def get_json(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": "MarketPulse/0.1"})
    with urllib.request.urlopen(request, timeout=25) as response:
        return json.loads(response.read().decode("utf-8-sig"))


def yahoo_chart(symbol: str, range_name: str = "5d"):
    query = urllib.parse.urlencode({"range": range_name, "interval": "1d", "events": "history"})
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol, safe='')}?{query}"
    payload = get_json(url)["chart"]["result"][0]
    meta = payload.get("meta", {})
    quotes = payload.get("indicators", {}).get("quote", [{}])[0]
    closes = [value for value in quotes.get("close", []) if value is not None]
    history = [value for value in closes if isinstance(value, (int, float))]
    return {
        "symbol": symbol,
        "currency": meta.get("currency"),
        "latest": closes[-1] if closes else None,
        "previous": closes[-2] if len(closes) > 1 else None,
        "as_of": datetime.fromtimestamp(payload["timestamp"][-1], tz=timezone.utc).astimezone(TZ_TAIPEI).isoformat() if payload.get("timestamp") else None,
        "history": history,
    }


def percentile(value, history):
    values = sorted(item for item in history if isinstance(item, (int, float)))
    if value is None or not values:
        return None
    below = sum(item <= value for item in values)
    return round((below / len(values)) * 100, 1)


def build_masters_analysis(output):
    derived = output.get("derived", {})
    percentiles = derived.get("percentiles_1y", {})
    breadth = derived.get("taiwan_breadth", {})
    vix_pct = percentiles.get("vix") or 50
    rate_pct = percentiles.get("us10y") or 50
    breadth_ratio = breadth.get("advance_ratio")
    breadth_ratio = breadth_ratio if isinstance(breadth_ratio, (int, float)) else 50
    taiwan_change = derived.get("taiwan_daily_change") or 0
    risk = 0
    risk += 25 if rate_pct >= 80 else 12 if rate_pct >= 60 else 0
    risk += 25 if vix_pct >= 80 else 12 if vix_pct >= 60 else 0
    risk += 25 if breadth_ratio < 40 else 12 if breadth_ratio < 50 else 0
    risk += 25 if taiwan_change <= -2 else 12 if taiwan_change < 0 else 0
    risk = min(100, risk)
    defensive = risk >= 55
    return {
        "generated_at": output.get("updated_at"),
        "disclaimer": "這是依公開投資思想建立的分析模型，不是本人觀點、真實引言或個別投資建議。",
        "method": "固定規則先產生可驗證版本；未設定 GEMINI_API_KEY，因此不呼叫生成式 AI。",
        "masters": [
            {
                "id": "value",
                "name": "價值投資模型",
                "based_on": "長期價值與安全邊際",
                "attitude": "強烈防禦" if defensive and rate_pct >= 80 else "等待好價格",
                "color": "red" if defensive else "yellow",
                "focus": f"美國 10 年期利率歷史百分位：{rate_pct:.1f}%",
                "comment": "當無風險利率偏高、資產價格也不便宜，先保留現金與短債，等待更好的買進價格。",
            },
            {
                "id": "macro",
                "name": "總經平衡模型",
                "based_on": "成長、通膨與分散配置",
                "attitude": "黃金與防守優先" if defensive else "維持分散配置",
                "color": "red" if defensive else "green",
                "focus": f"美股一起上漲比例：{breadth_ratio:.1f}%",
                "comment": "當市場由少數股票支撐，就不把全部資金押在單一方向，優先保持不同資產的平衡。",
            },
            {
                "id": "liquidity",
                "name": "流動性轉折模型",
                "based_on": "資金速度與市場內部強弱",
                "attitude": "小心行情轉弱" if breadth_ratio < 45 or rate_pct >= 80 else "觀察中",
                "color": "red" if breadth_ratio < 45 or rate_pct >= 80 else "yellow",
                "focus": f"上漲股票比例：{breadth_ratio:.1f}%",
                "comment": "不要只看指數。若一起上漲的股票變少，代表資金集中，市場轉弱時波動可能放大。",
            },
            {
                "id": "credit",
                "name": "信用週期模型",
                "based_on": "信用風險與安全邊際",
                "attitude": "防守第一" if vix_pct >= 70 or defensive else "保持警覺",
                "color": "red" if vix_pct >= 70 or defensive else "yellow",
                "focus": f"恐慌指數歷史百分位：{vix_pct:.1f}%",
                "comment": "信用與波動率通常會先透露壓力；在市場還沒確認前，先降低追高與過度槓桿。",
            },
        ],
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
            output["market"][label] = yahoo_chart(symbol, "1y")
            output["sources"][label] = "https://finance.yahoo.com/"
        except Exception as exc:
            errors.append(f"Yahoo {symbol}: {exc}")

    # Transparent history-based signals. Percentiles use the trailing one-year daily history.
    taiwan = output["market"].get("taiwan_index", {})
    vix = output["market"].get("vix", {}).get("latest")
    sp500 = output["market"].get("sp500", {})
    us10y = output["market"].get("us10y_proxy", {})
    gold = output["market"].get("gold", {})
    breadth = output["market"].get("taiwan_breadth", {}).get("data", {})

    def count_breadth(key):
        raw = breadth.get(key, "0")
        try:
            return int(str(raw).split("(")[0].replace(",", ""))
        except (ValueError, TypeError):
            return 0

    advancing = count_breadth("上漲(漲停)")
    declining = count_breadth("下跌(跌停)")
    breadth_total = advancing + declining
    vix_pct = percentile(vix, output["market"].get("vix", {}).get("history", []))
    us10y_pct = percentile(us10y.get("latest"), us10y.get("history", []))
    gold_pct = percentile(gold.get("latest"), gold.get("history", []))
    output["derived"] = {
        "taiwan_daily_change": taiwan.get("change_percent"),
        "vix_level": vix,
        "vix_status": "high" if isinstance(vix, (int, float)) and vix >= 25 else "normal",
        "sp500_daily_change": (sp500.get("latest") - sp500.get("previous")) / sp500.get("previous") * 100 if sp500.get("latest") and sp500.get("previous") else None,
        "percentiles_1y": {"vix": vix_pct, "us10y": us10y_pct, "gold": gold_pct},
        "taiwan_breadth": {"advancing": advancing, "declining": declining, "advance_ratio": round(advancing / breadth_total * 100, 1) if breadth_total else None},
        "data_window": "近一年每日資料",
        "note": "Daily public-data snapshot. Yahoo Finance values may be delayed; verify before trading.",
    }
    if errors:
        output["status"] = "partial"
        output["errors"] = errors

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    MASTERS_OUT.write_text(json.dumps(build_masters_analysis(output), ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": output["status"], "updated_at": now, "errors": errors}, ensure_ascii=False))


if __name__ == "__main__":
    main()
