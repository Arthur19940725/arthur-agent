import re

import requests
from langchain_core.tools import tool

from api.monitor import monitor

TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q={symbol}"
TENCENT_HINT_URL = "https://smartbox.gtimg.cn/s3/"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


def _normalize_cn_code(code: str) -> str:
    raw = code.strip().lower()
    if raw.startswith(("sh", "sz", "bj", "hk")):
        return raw
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 6:
        if digits.startswith(("5", "6", "9")):
            return f"sh{digits}"
        if digits.startswith(("0", "1", "2", "3")):
            return f"sz{digits}"
        if digits.startswith(("4", "8")):
            return f"bj{digits}"
    return raw


def _resolve_cn_symbol(query: str) -> str:
    symbol = _normalize_cn_code(query)
    if re.fullmatch(r"(sh|sz|bj|hk)\d{5,6}", symbol):
        return symbol

    resp = requests.get(
        TENCENT_HINT_URL,
        params={"q": query, "t": "all"},
        timeout=15,
    )
    resp.raise_for_status()
    text = resp.content.decode("utf-8", errors="ignore")
    match = re.search(r"(sh|sz|bj|hk)~(\d{6})~", text, re.I)
    if not match:
        return symbol
    return f"{match.group(1).lower()}{match.group(2)}"


def _fetch_tencent_quote(symbol: str) -> str:
    resp = requests.get(TENCENT_QUOTE_URL.format(symbol=symbol), timeout=15)
    resp.raise_for_status()
    text = resp.content.decode("gbk", errors="ignore").strip()
    if "~" not in text:
        return ""
    fields = text.split('"')[1].split("~")
    if len(fields) < 46 or not fields[1] or fields[3] in {"", "0.00"}:
        return ""
    return "\n".join([
        f"标的：{fields[1]} ({fields[2]})",
        f"现价：{fields[3]}",
        f"昨收：{fields[4]}",
        f"开盘：{fields[5]}",
        f"最高：{fields[33]}",
        f"最低：{fields[34]}",
        f"涨跌额：{fields[31]}",
        f"涨跌幅：{fields[32]}%",
        f"成交量(手)：{fields[36]}",
        f"成交额(万)：{fields[37]}",
        f"换手率：{fields[38]}%",
        f"市盈率：{fields[39]}",
        f"流通市值(亿)：{fields[44]}",
        f"总市值(亿)：{fields[45]}",
    ])


def _fetch_yahoo_quote(symbol: str) -> str:
    resp = requests.get(
        YAHOO_CHART_URL.format(symbol=symbol),
        params={"interval": "1d", "range": "5d"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=15,
    )
    resp.raise_for_status()
    result = ((resp.json().get("chart") or {}).get("result") or [None])[0]
    if not result:
        return ""
    meta = result.get("meta") or {}
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    if price is None:
        return ""
    change = None
    change_pct = None
    if prev:
        change = round(float(price) - float(prev), 4)
        change_pct = round(change / float(prev) * 100, 2)
    return "\n".join([
        f"标的：{meta.get('shortName') or symbol} ({meta.get('symbol') or symbol})",
        f"现价：{price} {meta.get('currency', '')}".strip(),
        f"昨收：{prev}",
        f"涨跌额：{change}",
        f"涨跌幅：{change_pct}%",
        f"最高：{meta.get('regularMarketDayHigh')}",
        f"最低：{meta.get('regularMarketDayLow')}",
        f"成交量：{meta.get('regularMarketVolume')}",
        f"交易所：{meta.get('exchangeName')}",
    ])


@tool
def get_stock_quote(symbol: str) -> str:
    """
    查询个股实时行情。用户问现价、涨跌、成交额、市值或市盈率时必须使用本工具，不要用网络搜索代替。
    :param symbol: 股票代码或名称，例如 600519、贵州茅台、AAPL
    :return: 现价、涨跌幅、成交额等行情原文；不要编造数字
    """
    query = (symbol or "").strip()
    monitor.report_tool(tool_name="股票行情工具", args={"symbol": query})
    if not query:
        return "请提供股票代码或名称，例如 600519、贵州茅台、AAPL。"

    errors = []
    cn_symbol = _resolve_cn_symbol(query)
    try:
        quote = _fetch_tencent_quote(cn_symbol)
        if quote:
            return quote
    except Exception as exc:
        errors.append(f"A股行情查询失败：{exc}")

    try:
        quote = _fetch_yahoo_quote(query.upper())
        if quote:
            return quote
    except Exception as exc:
        errors.append(f"海外行情查询失败：{exc}")

    detail = "；".join(errors) if errors else "未匹配到有效标的"
    return f"未找到股票行情：{query}。{detail}"
