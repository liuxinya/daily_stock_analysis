# -*- coding: utf-8 -*-
"""图表数据服务：K 线 / 分时 + 技术指标。

职责分工：
- 上游取数与字段归一化在 ``data_provider/ths_chart_fetcher.py``
- 指标口径由 ``src/stock_analyzer.calculate_macd_series`` 与
  ``src/services/alert_indicators.compute_kdj`` 提供 —— 刻意复用，保证同一只股票
  在分析报告、告警判定和 K 线图上看到的是同一个 MACD / KDJ
- 本模块只做编排：补当日那一根、算指标、裁剪预热段、进程内短 TTL 缓存

不落库。``stock_daily`` 的唯一键 ``(code, date)`` 没有周期维度，周 K 会和日 K 互相
覆盖；图表按需拉取即可，不值得为看图去改那张表的 schema。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

from data_provider import ths_chart_fetcher as ths
from src.config import get_config
from src.services.alert_indicators import compute_kdj
from src.stock_analyzer import calculate_macd_series

logger = logging.getLogger(__name__)

# 指标预热：MA20 要 20 根、MACD 要 26+9 根、KDJ 要 9+ 根。多取 80 根再裁掉，
# 否则返回区间开头几十根的指标会是 NaN，画出来是断线。
_WARMUP_BARS = 80

_MA_WINDOWS = (5, 10, 20)

SUPPORTED_INDICATORS = ("ma", "macd", "kdj")

_MAX_LIMIT = 1200

_cache: Dict[Tuple[Any, ...], Tuple[float, Dict[str, Any]]] = {}
_cache_lock = threading.Lock()


def _cache_get(key: Tuple[Any, ...], ttl: float) -> Optional[Dict[str, Any]]:
    if ttl <= 0:
        return None
    with _cache_lock:
        hit = _cache.get(key)
        if not hit:
            return None
        stored_at, payload = hit
        if time.time() - stored_at > ttl:
            _cache.pop(key, None)
            return None
        return payload


def _cache_put(key: Tuple[Any, ...], payload: Dict[str, Any], ttl: float) -> None:
    if ttl <= 0:
        return
    with _cache_lock:
        _cache[key] = (time.time(), payload)


def clear_cache() -> None:
    """清空进程内缓存（测试与排障用）。"""
    with _cache_lock:
        _cache.clear()


def _clean(value: Any) -> Optional[float]:
    """pandas 的 NaN/Inf 不是合法 JSON，统一落成 None。"""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _normalize_indicators(indicators: Optional[Iterable[str]]) -> Tuple[str, ...]:
    if indicators is None:
        return ("ma",)
    wanted = []
    for item in indicators:
        key = str(item or "").strip().lower()
        if not key:
            continue
        if key not in SUPPORTED_INDICATORS:
            raise ValueError(f"不支持的指标: {item}")
        if key not in wanted:
            wanted.append(key)
    return tuple(wanted)


def _attach_indicators(rows: List[Dict[str, Any]], wanted: Tuple[str, ...]) -> None:
    """就地把指标写回每一行。行数不足时对应指标整体留空，不给半截数据。"""
    if not rows or not wanted:
        return

    frame = pd.DataFrame(
        {
            "high": [row["high"] for row in rows],
            "low": [row["low"] for row in rows],
            "close": [row["close"] for row in rows],
        }
    )

    if "ma" in wanted:
        for window in _MA_WINDOWS:
            series = frame["close"].rolling(window=window).mean()
            for row, value in zip(rows, series):
                row[f"ma{window}"] = _clean(round(value, 3) if value == value else value)

    if "macd" in wanted:
        dif, dea, bar = calculate_macd_series(frame["close"])
        for row, dif_v, dea_v, bar_v in zip(rows, dif, dea, bar):
            row["dif"] = _clean(dif_v if dif_v != dif_v else round(dif_v, 4))
            row["dea"] = _clean(dea_v if dea_v != dea_v else round(dea_v, 4))
            row["macd"] = _clean(bar_v if bar_v != bar_v else round(bar_v, 4))

    if "kdj" in wanted:
        kdj = compute_kdj(frame)
        for row, k_v, d_v, j_v in zip(rows, kdj["k"], kdj["d"], kdj["j"]):
            row["k"] = _clean(k_v if k_v != k_v else round(k_v, 3))
            row["d"] = _clean(d_v if d_v != d_v else round(d_v, 3))
            row["j"] = _clean(j_v if j_v != j_v else round(j_v, 3))


def get_kline(
    code: str,
    *,
    period: str = "day",
    adjust: str = "qfq",
    limit: int = 250,
    indicators: Optional[Iterable[str]] = None,
    with_today: bool = True,
) -> Dict[str, Any]:
    """K 线 + 指标。

    ``with_today`` 对日 K 生效：上游 line 系列在盘中给不出可用的当日行（前复权文件
    当日行不存在，不复权是 OHLC 全空的占位），所以这一根从盘口快照合成，并打上
    ``partial=True``。后复权口径下不合成 —— 快照是不复权价，换算到后复权需要复权
    因子，宁可少一根也不给一根错的。
    """
    config = get_config()
    bounded_limit = max(1, min(int(limit), _MAX_LIMIT))
    wanted = _normalize_indicators(indicators)
    period_key = (period or "day").strip().lower()
    adjust_key = (adjust or "qfq").strip().lower()
    synthesize = bool(with_today) and period_key == "day" and adjust_key in ("none", "qfq")

    cache_key = ("kline", code, period_key, adjust_key, bounded_limit, wanted, synthesize)
    ttl = float(getattr(config, "ths_chart_cache_ttl_sec", 30) or 0)
    cached = _cache_get(cache_key, ttl)
    if cached is not None:
        return cached

    timeout = float(getattr(config, "ths_chart_timeout_sec", 10) or 10)
    payload = ths.fetch_kline(
        code,
        period=period_key,
        adjust=adjust_key,
        limit=bounded_limit + _WARMUP_BARS,
        timeout=timeout,
    )
    rows: List[Dict[str, Any]] = payload["rows"]

    today_source: Optional[str] = None
    if synthesize:
        try:
            snapshot = ths.fetch_snapshot(code, timeout=timeout)
            today_row = ths.build_today_row(snapshot)
        except ths.ThsUpstreamError as exc:
            # 快照失败只影响当日那一根，历史照常返回。
            logger.warning("[图表] %s 当日行合成失败: %s", code, exc)
            today_row = None
        if today_row is not None:
            rows = [row for row in rows if row["time"] != today_row["time"]]
            rows.append(today_row)
            today_source = "snapshot"

    # 周 / 月 / 季的末根是「还没走完的本周 / 本月 / 本季」，上游照常给数但不会标注。
    # 用上游自报的交易日比一下，如实打上 partial，免得把半根当成已收盘的一根来读。
    upstream_today = payload.get("upstream_today")
    if today_source is None and upstream_today and rows and rows[-1]["time"] == upstream_today:
        rows[-1]["partial"] = True
        today_source = "upstream"

    _attach_indicators(rows, wanted)
    rows = rows[-bounded_limit:]
    for row in rows:
        for key in ("open", "high", "low", "close"):
            row[key] = _clean(round(row[key], 3))

    result = {
        "code": payload["code"],
        "name": payload["name"],
        "period": payload["period"],
        "adjust": payload["adjust"],
        "indicators": list(wanted),
        "rows": rows,
        "today_source": today_source,
        "listed_since": payload.get("listed_since"),
        "trading_hours": payload.get("trading_hours"),
        "source": "ths",
    }
    _cache_put(cache_key, result, ttl)
    return result


def get_timeshare(code: str) -> Dict[str, Any]:
    """分时。上游实时，缓存 TTL 与 K 线共用一套配置。"""
    config = get_config()
    cache_key = ("timeshare", code)
    ttl = float(getattr(config, "ths_chart_cache_ttl_sec", 30) or 0)
    cached = _cache_get(cache_key, ttl)
    if cached is not None:
        return cached

    payload = ths.fetch_timeshare(
        code,
        timeout=float(getattr(config, "ths_chart_timeout_sec", 10) or 10),
    )
    payload["source"] = "ths"
    _cache_put(cache_key, payload, ttl)
    return payload
