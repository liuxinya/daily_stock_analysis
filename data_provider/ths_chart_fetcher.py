# -*- coding: utf-8 -*-
"""同花顺行情 CDN（``d.10jqka.com.cn``）图表数据适配器。

为什么单独一个模块、不接入 ``DataFetcherManager``：
``BaseFetcher._fetch_raw_data(code, start, end)`` 的抽象签名里没有周期通道，
`stock_daily` 表的唯一键 ``(code, date)`` 也没有周期维度（周 K 会和日 K 互相覆盖）。
图表数据按需拉取、不落库，所以走旁路，不去动那两处契约。

上游特性（2026-10 实测）：

- 纯静态 JSONP 文件，**无 token / 无 cookie / 不校验 Referer**，``Access-Control-Allow-Origin: *``。
- 日 K 前复权与 akshare 东财 ``stock_zh_a_hist(adjust="qfq")`` 收盘价、成交量逐笔一致。
- **盘中「当日 K 线」在 line 系列里不可用**：前复权文件当日行不存在，不复权是
  OHLC 全空、成交量 0、收盘价等于昨收的占位行。当日那一根必须从 ``realhead`` 合成。
- **会返回 HTTP 200 + 截断 body**（实测撞到过 211 字节），另有偶发 openresty 502/504。
  所以每个响应都要校验 JSONP 包裹与必需字段，不能只看状态码。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://d.10jqka.com.cn"

# 上游不校验 Referer / UA，但给一个正常 UA 可以避免被将来加的规则误伤。
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

_DEFAULT_TIMEOUT = 10.0

# 偶发 502/504 与截断 body 都靠重试恢复；次数给小，避免把慢故障放大成长等待。
_MAX_ATTEMPTS = 3

# JSONP 函数名前缀；上游所有图表接口都是这个前缀，可据此判断 body 是否完整。
_JSONP_PREFIX = "quotebridge_"
_JSONP_RE = re.compile(r"^(" + _JSONP_PREFIX + r"[A-Za-z0-9_]*)\((.*)\)\s*;?\s*$", re.S)

# 周期 -> 上游 period 码。日 K 三种复权各有一个码；周 / 月 / 季只有前复权一份。
_DAY_CODES = {"none": "00", "qfq": "01", "hfq": "02"}
_PERIOD_CODES = {"week": "11", "month": "21", "quarter": "91"}

# 周 / 月 / 季实际是前复权口径（与 01 的历史价位一致），如实回报而不是回报调用方传的值。
_IMPLICIT_ADJUST = "qfq"

SUPPORTED_PERIODS = ("day", "week", "month", "quarter")
SUPPORTED_ADJUSTS = tuple(_DAY_CODES.keys())

# realhead 的字段 id（无文档，实测与分时累计量/额、均价、涨跌停 ±10% 三路对账确认）。
_RH_OPEN = "7"
_RH_HIGH = "8"
_RH_LOW = "9"
_RH_PRICE = "10"
_RH_PREV_CLOSE = "6"
_RH_VOLUME = "13"
_RH_AMOUNT = "19"
_RH_AVG_PRICE = "1378761"
_RH_CHANGE_PCT = "199112"
_RH_LIMIT_UP = "69"
_RH_LIMIT_DOWN = "70"


class ThsUpstreamError(RuntimeError):
    """上游不可用、响应不完整或无法解析。"""


def normalize_symbol(code: str) -> str:
    """把股票代码归一成上游的 ``<prefix>_<code>`` 形式。

    v1 只支持 A 股与场内基金（``hs_`` 前缀，沪深不分）。港股（``hk_HK0700``）、
    美股（``usa_AAPL``）、指数（``hs_1A0001``）的前缀实测可返回 200，但没有做过
    数值交叉验证，所以这里显式拒绝，避免把未验证的映射当成可用能力。
    """
    raw = (code or "").strip().upper()
    if not raw:
        raise ValueError("股票代码为空")

    # 去掉交易所前缀 / 后缀：SH600519、600519.SH、SZ002594 等
    stripped = re.sub(r"^(SH|SZ|BJ)", "", raw)
    stripped = re.sub(r"\.(SH|SZ|SS|BJ)$", "", stripped)
    if re.fullmatch(r"\d{6}", stripped):
        return f"hs_{stripped}"

    raise ValueError(f"暂不支持的代码格式: {code}（当前仅支持 A 股 / 场内基金 6 位代码）")


def resolve_period_code(period: str, adjust: str) -> Tuple[str, str]:
    """返回 ``(上游 period 码, 实际生效的复权口径)``。"""
    period_key = (period or "day").strip().lower()
    adjust_key = (adjust or "qfq").strip().lower()

    if period_key == "day":
        if adjust_key not in _DAY_CODES:
            raise ValueError(f"不支持的复权口径: {adjust}")
        return _DAY_CODES[adjust_key], adjust_key

    code = _PERIOD_CODES.get(period_key)
    if not code:
        raise ValueError(f"不支持的周期: {period}")
    return code, _IMPLICIT_ADJUST


def _parse_jsonp(text: str, *, url: str, required: Tuple[str, ...]) -> Dict[str, Any]:
    """剥掉 JSONP 包裹并校验必需字段。

    上游会返回 HTTP 200 + 截断 body，这里是唯一的拦截点：函数名前缀、括号配对、
    JSON 可解析、必需字段存在，四项缺一即当上游异常，不让空数据静默流下去。
    """
    body = (text or "").strip()
    if not body.startswith(_JSONP_PREFIX):
        raise ThsUpstreamError(f"上游响应不是预期的 JSONP（{len(body)} 字节）: {url}")

    match = _JSONP_RE.match(body)
    if not match:
        raise ThsUpstreamError(f"上游响应 JSONP 包裹不完整（{len(body)} 字节）: {url}")

    try:
        payload = json.loads(match.group(2))
    except json.JSONDecodeError as exc:
        raise ThsUpstreamError(f"上游响应 JSON 解析失败: {url}") from exc

    if not isinstance(payload, dict):
        raise ThsUpstreamError(f"上游响应不是 JSON 对象: {url}")

    missing = [key for key in required if key not in payload]
    if missing:
        raise ThsUpstreamError(f"上游响应缺少字段 {missing}: {url}")

    return payload


def _fetch_jsonp(
    path: str,
    *,
    required: Tuple[str, ...],
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """取一个 JSONP 文件；502/504 与截断 body 都重试，最后一次失败如实抛出。"""
    url = f"{_BASE_URL}{path}"
    budget = float(timeout or _DEFAULT_TIMEOUT)
    last_error: Optional[Exception] = None

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            response = requests.get(url, headers=_HEADERS, timeout=budget)
            if response.status_code >= 500:
                raise ThsUpstreamError(f"上游 {response.status_code}: {url}")
            if response.status_code != 200:
                # 404 等客户端错误重试没有意义（代码或周期码不存在），直接抛。
                raise ThsUpstreamError(f"上游 {response.status_code}: {url}")
            return _parse_jsonp(response.text, url=url, required=required)
        except ThsUpstreamError as exc:
            last_error = exc
            if "404" in str(exc):
                raise
        except requests.RequestException as exc:
            last_error = exc
        logger.debug("[同花顺] 第 %s/%s 次取数失败: %s", attempt, _MAX_ATTEMPTS, last_error)

    raise ThsUpstreamError(f"上游取数失败（已重试 {_MAX_ATTEMPTS} 次）: {last_error}")


def _to_float(value: Any) -> Optional[float]:
    text = str(value if value is not None else "").strip()
    if not text:
        return None
    try:
        parsed = float(text)
    except ValueError:
        return None
    return parsed


def parse_kline_rows(data: Optional[str]) -> List[Dict[str, Any]]:
    """把 ``data`` 串解析成行。

    格式：``日期,开,高,低,收,成交量(股),成交额(元),换手率%,,?,?``，``;`` 分行。
    末尾三列含义不明，只取前 8 列。

    盘中不复权文件里会出现 ``20261019,,,,82.70,0,0.00,0.000,,,0`` 这种 OHLC 全空的
    占位行 —— 它不携带任何信息，直接丢掉，当日那一根交给 ``build_today_row`` 合成。
    """
    rows: List[Dict[str, Any]] = []
    for line in (data or "").split(";"):
        cells = line.strip().split(",")
        if len(cells) < 7 or not cells[0].strip():
            continue
        open_, high, low, close = (_to_float(cells[i]) for i in (1, 2, 3, 4))
        if None in (open_, high, low, close):
            continue
        rows.append(
            {
                "time": cells[0].strip(),
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": _to_float(cells[5]) or 0.0,
                "amount": _to_float(cells[6]) or 0.0,
                "turnover_rate": _to_float(cells[7]) if len(cells) > 7 else None,
                "partial": False,
            }
        )
    return rows


def fetch_snapshot(code: str, *, timeout: Optional[float] = None) -> Dict[str, Any]:
    """盘口快照（``realhead``）。盘中实时，是当日那一根 K 线的唯一可靠来源。"""
    symbol = normalize_symbol(code)
    payload = _fetch_jsonp(
        f"/v6/realhead/{symbol}/defer/last.js",
        required=("items",),
        timeout=timeout,
    )
    items = payload.get("items")
    if not isinstance(items, dict):
        raise ThsUpstreamError("realhead 响应 items 不是对象")

    update_time = str(items.get("updateTime") or "").strip()
    return {
        "code": str(items.get("5") or "").strip() or symbol.split("_", 1)[-1],
        "name": str(items.get("name") or "").strip(),
        "price": _to_float(items.get(_RH_PRICE)),
        "open": _to_float(items.get(_RH_OPEN)),
        "high": _to_float(items.get(_RH_HIGH)),
        "low": _to_float(items.get(_RH_LOW)),
        "prev_close": _to_float(items.get(_RH_PREV_CLOSE)),
        "volume": _to_float(items.get(_RH_VOLUME)),
        "amount": _to_float(items.get(_RH_AMOUNT)),
        "avg_price": _to_float(items.get(_RH_AVG_PRICE)),
        "change_percent": _to_float(items.get(_RH_CHANGE_PCT)),
        "limit_up": _to_float(items.get(_RH_LIMIT_UP)),
        "limit_down": _to_float(items.get(_RH_LIMIT_DOWN)),
        "trade_date": update_time[:10].replace("-", "") or None,
        "update_time": update_time or None,
        "suspended": bool(items.get("stop")),
    }


def build_today_row(snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """用盘口快照合成「当日」那一根未完成的日 K。

    只在 OHLC 与日期都齐备时才产出，并打上 ``partial=True`` 让调用方能区分对待。
    停牌（``stop``）或尚未开盘（开盘价缺失 / 成交量为 0）时返回 None —— 宁可少一根，
    不要给一根假的。
    """
    trade_date = snapshot.get("trade_date")
    open_ = snapshot.get("open")
    high = snapshot.get("high")
    low = snapshot.get("low")
    close = snapshot.get("price")
    if not trade_date or None in (open_, high, low, close):
        return None
    if snapshot.get("suspended") or not open_ or not (snapshot.get("volume") or 0):
        return None

    return {
        "time": trade_date,
        "open": float(open_),
        "high": float(high),
        "low": float(low),
        "close": float(close),
        "volume": float(snapshot.get("volume") or 0.0),
        "amount": float(snapshot.get("amount") or 0.0),
        "turnover_rate": None,
        "partial": True,
    }


def fetch_kline(
    code: str,
    *,
    period: str = "day",
    adjust: str = "qfq",
    limit: int = 250,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """取 K 线。

    ``last.js`` 对日 K 只给最近 140 根，不够时按年回溯 ``<year>.js``（与 last.js
    同为 CSV 格式）；周 / 月 / 季的 ``last.js`` 本身就是全量，直接截尾即可。
    刻意不碰 ``all.js`` —— 那是另一套压缩列式格式，按年取就能完全绕开。
    """
    symbol = normalize_symbol(code)
    period_code, effective_adjust = resolve_period_code(period, adjust)
    base = f"/v6/line/{symbol}/{period_code}"

    payload = _fetch_jsonp(f"{base}/last.js", required=("data",), timeout=timeout)
    merged: Dict[str, Dict[str, Any]] = {row["time"]: row for row in parse_kline_rows(payload.get("data"))}

    # 只有日 K 的 last.js 是近端窗口（约 140 根）需要回溯；周 / 月 / 季的 last.js
    # 本身就是全量，再去取年度文件纯属浪费请求，还会撞上偶发 502。
    if period_code in _DAY_CODES.values():
        years = sorted((str(y) for y in (payload.get("year") or {})), reverse=True)
        for year in years:
            if len(merged) >= limit:
                break
            try:
                year_payload = _fetch_jsonp(f"{base}/{year}.js", required=("data",), timeout=timeout)
            except ThsUpstreamError as exc:
                # 回溯失败不致命：已有的近端数据照常返回，只少了深度。
                logger.warning("[同花顺] %s %s 年度回溯失败: %s", symbol, year, exc)
                break
            for row in parse_kline_rows(year_payload.get("data")):
                merged.setdefault(row["time"], row)

    rows = [merged[key] for key in sorted(merged)][-limit:]
    return {
        "code": symbol.split("_", 1)[-1],
        "name": str(payload.get("name") or "").strip(),
        "period": (period or "day").strip().lower(),
        "adjust": effective_adjust,
        "rows": rows,
        "total_available": payload.get("total"),
        "listed_since": payload.get("start"),
        "trading_hours": payload.get("rt"),
        # 上游自报的当前交易日，用来判断末根是否还没走完，省掉本地时区推算。
        "upstream_today": str(payload.get("today") or "").strip() or None,
    }


def fetch_timeshare(code: str, *, timeout: Optional[float] = None) -> Dict[str, Any]:
    """分时。上游实时（末点与 realhead 的 updateTime 同分）。

    ``data`` 每行为 ``时间,价格,成交额(元),均价,成交量(股)``。
    """
    symbol = normalize_symbol(code)
    payload = _fetch_jsonp(f"/v6/time/{symbol}/last.js", required=(symbol,), timeout=timeout)
    block = payload.get(symbol)
    if not isinstance(block, dict):
        raise ThsUpstreamError("分时响应结构异常")

    points: List[Dict[str, Any]] = []
    for line in str(block.get("data") or "").split(";"):
        cells = line.strip().split(",")
        if len(cells) < 5 or not cells[0].strip():
            continue
        price = _to_float(cells[1])
        if price is None:
            continue
        points.append(
            {
                "time": cells[0].strip(),
                "price": price,
                "amount": _to_float(cells[2]) or 0.0,
                "avg_price": _to_float(cells[3]),
                "volume": _to_float(cells[4]) or 0.0,
            }
        )

    return {
        "code": symbol.split("_", 1)[-1],
        "name": str(block.get("name") or "").strip(),
        "trade_date": str(block.get("date") or "").strip() or None,
        "prev_close": _to_float(block.get("pre")),
        "trade_times": block.get("tradeTime") or [],
        "is_trading": bool(block.get("isTrading")),
        "suspended": bool(block.get("stop")),
        "points": points,
    }


