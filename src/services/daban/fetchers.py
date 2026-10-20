# -*- coding: utf-8 -*-
"""「我是打板王」的抓取层：每个数据源一个 envelope，单源失败只降级对应指标。

envelope 结构与行业资金一致：``{key, ok, source, as_of, degraded, degraded_reason, error, value}``。
``value`` 里放解析好的行，服务层只认 envelope，不直接碰上游。

主源 / 兜底：

- 涨停 / 炸板 / 跌停池：同花顺为主（带涨停原因），失败时退东财（无涨停原因，题材归因降级）。
- 连板数：同花顺天梯为主；天梯失败而池子来自东财时，直接用东财的 ``lbc``。
- 昨日涨停今日表现、强势股、次新：只有东财有。
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from data_provider import limit_up_fetcher as lu
from src.services.daban.constants import SOURCE_LABELS

logger = logging.getLogger(__name__)


def _envelope(
    key: str,
    *,
    ok: bool,
    source: str = "",
    as_of: str = "",
    value: Optional[Dict[str, Any]] = None,
    degraded: bool = False,
    degraded_reason: str = "",
    error: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "key": key,
        "label": SOURCE_LABELS.get(key, key),
        "ok": ok,
        "source": source,
        "as_of": as_of,
        "degraded": degraded,
        "degraded_reason": degraded_reason,
        "error": error,
        "value": value or {},
    }


def failed(key: str, error: str) -> Dict[str, Any]:
    return _envelope(key, ok=False, error=error, degraded=True, degraded_reason=error)


def _err(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc)[:180]}"


def _filter_universe(rows: List[Dict[str, Any]], include_bse: bool) -> List[Dict[str, Any]]:
    return [r for r in rows if lu.in_universe(r.get("code"), r.get("name"), include_bse=include_bse)]


# ---------------------------------------------------------------------- 交易日


def previous_trade_date(day: str, *, max_back: int = 15) -> str:
    """``YYYY-MM-DD`` 的上一个 A 股交易日。日历不可用时只跳过周末。"""
    from src.core.trading_calendar import is_market_open

    current = datetime.strptime(day, "%Y-%m-%d").date()
    for _ in range(max_back):
        current -= timedelta(days=1)
        if current.weekday() >= 5:
            continue
        if is_market_open("cn", current):
            return current.isoformat()
    return (datetime.strptime(day, "%Y-%m-%d").date() - timedelta(days=1)).isoformat()


def recent_trade_dates(end: str, count: int) -> List[str]:
    """``end``（不含）之前的 ``count`` 个交易日，按时间正序。"""
    dates: List[str] = []
    cursor = end
    for _ in range(count):
        cursor = previous_trade_date(cursor)
        dates.append(cursor)
    return list(reversed(dates))


def effective_trade_date() -> str:
    from src.core.trading_calendar import get_effective_trading_date

    try:
        value = get_effective_trading_date("cn")
        return value.isoformat() if isinstance(value, date) else str(value)
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------- 池子


def fetch_limit_pool(kind: str, trade_date: Optional[str], *, timeout: float, include_bse: bool) -> Dict[str, Any]:
    """涨停 / 炸板 / 跌停池（``kind`` = zt / zb / dt）。同花顺失败时退东财。"""
    try:
        result = lu.fetch_ths_pool(kind, trade_date, timeout=timeout)
        rows = _filter_universe(result["rows"], include_bse)
        return _envelope(
            kind, ok=True, source="同花顺涨停数据中心", as_of=result["date"],
            value={"rows": rows, "counts": result["counts"], "trade_status": result["trade_status"]},
        )
    except Exception as exc:
        ths_error = _err(exc)
        logger.warning("打板王：同花顺 %s 池失败，尝试东财：%s", kind, ths_error)
    if not trade_date:
        return failed(kind, f"同花顺失败（{ths_error}），且东财兜底需要明确日期")
    try:
        rows = _filter_universe(lu.fetch_em_pool(kind, trade_date, timeout=timeout), include_bse)
        return _envelope(
            kind, ok=True, source="东方财富（兜底）", as_of=trade_date,
            value={"rows": rows, "counts": {}, "trade_status": ""},
            degraded=True,
            degraded_reason=f"同花顺不可用，改用东财；{'无涨停原因，题材归因降级' if kind == 'zt' else '口径略有差异'}",
        )
    except Exception as exc:
        return failed(kind, f"同花顺：{ths_error}；东财：{_err(exc)}")


def fetch_ladder(trade_date: Optional[str], *, timeout: float) -> Dict[str, Any]:
    try:
        ladder = lu.fetch_ths_ladder(trade_date, timeout=timeout)
        return _envelope("ladder", ok=True, source="同花顺连板天梯", as_of=trade_date or "", value={"ladder": ladder})
    except Exception as exc:
        return failed("ladder", _err(exc))


def fetch_blocks(trade_date: Optional[str], *, timeout: float) -> Dict[str, Any]:
    try:
        blocks = lu.fetch_ths_blocks(trade_date, timeout=timeout)
        return _envelope("blocks", ok=True, source="同花顺涨停题材榜", as_of=trade_date or "", value={"blocks": blocks})
    except Exception as exc:
        return failed("blocks", _err(exc))


def fetch_em(kind: str, trade_date: str, *, timeout: float, include_bse: bool) -> Dict[str, Any]:
    """东财独有的池：previous（昨日涨停今日表现）/ strong / sub_new。"""
    try:
        rows = _filter_universe(lu.fetch_em_pool(kind, trade_date, timeout=timeout), include_bse)
        return _envelope(kind, ok=True, source="东方财富", as_of=trade_date, value={"rows": rows})
    except Exception as exc:
        return failed(kind, _err(exc))


def fetch_hot_rank(*, timeout: float) -> Dict[str, Any]:
    """东财人气榜前 100 → ``{code: rank}``。复用 DataFetcherManager 的多源降级。"""

    def _run():
        from data_provider.base import DataFetcherManager

        return DataFetcherManager().get_hot_stocks(100)

    rows, error = call_with_timeout(_run, timeout=timeout, label="人气榜")
    if error or not rows:
        return failed("hot", error or "人气榜返回空")
    ranks = {}
    for index, row in enumerate(rows, start=1):
        code = str(row.get("code") or "").strip()
        if code:
            ranks[code] = int(row.get("rank") or index)
    return _envelope("hot", ok=True, source=str(rows[0].get("source") or "人气榜"), value={"ranks": ranks})


# ---------------------------------------------------------------------- 日 K / 新闻


def fetch_klines(codes: List[str], trade_date: str, *, limit: int, timeout: float) -> Dict[str, Any]:
    """候选的日 K（前复权）。只保留 ``trade_date`` 及以前的 K 线，回看历史日也不会偷看未来。"""
    from src.services import chart_service

    cutoff = trade_date.replace("-", "")
    rows_by_code: Dict[str, List[Dict[str, Any]]] = {}
    errors: List[str] = []

    def _one(code: str):
        payload = chart_service.get_kline(code, limit=limit, indicators=[], with_today=True)
        return [r for r in payload.get("rows") or [] if str(r.get("time") or "").replace("-", "") <= cutoff]

    results = run_parallel({code: (lambda c=code: _one(c)) for code in codes}, timeout=timeout, workers=8)
    for code, (rows, error) in results.items():
        if error:
            errors.append(f"{code}: {error}")
        else:
            rows_by_code[code] = rows or []
    if codes and not rows_by_code:
        return failed("klines", "；".join(errors[:3]) or "全部失败")
    return _envelope(
        "klines", ok=True, source="同花顺行情", as_of=trade_date,
        value={"rows": rows_by_code},
        degraded=bool(errors),
        degraded_reason=f"{len(errors)} 只候选的日 K 取数失败" if errors else "",
    )


def fetch_theme_news(names: List[str], *, timeout: float) -> Dict[str, Any]:
    """主线题材近 3 日新闻（财联社 / 同花顺 / 东财等关键词搜索，无需 key）。"""
    from src.services.news_search_service import NewsSearchService

    service = NewsSearchService()

    def _one(name: str):
        result = service.search_all(keyword=name, days=3, limit=30, budget_sec=max(5.0, timeout - 2))
        return {
            "count": int(result.get("total") or 0),
            "items": [
                {
                    "title": item.get("title") or "",
                    "platform_label": item.get("platform_label") or "",
                    "published_at": item.get("published_at") or "",
                    "url": item.get("url") or "",
                }
                for item in (result.get("items") or [])[:5]
            ],
            "failed_platforms": result.get("failed_platforms") or [],
        }

    results = run_parallel({name: (lambda n=name: _one(n)) for name in names}, timeout=timeout, workers=4)
    news = {name: value for name, (value, error) in results.items() if not error and value is not None}
    errors = [f"{name}: {error}" for name, (_, error) in results.items() if error]
    if names and not news:
        return failed("news", "；".join(errors[:3]) or "全部失败")
    return _envelope(
        "news", ok=True, source="新闻搜索", value={"news": news},
        degraded=bool(errors), degraded_reason="；".join(errors[:3]),
    )


# ---------------------------------------------------------------------- 并发工具


def call_with_timeout(func: Callable[[], Any], *, timeout: float, label: str) -> Tuple[Optional[Any], Optional[str]]:
    """带 deadline 的阻塞调用。超时后不等线程结束（阻塞中的 requests 取消不掉）。"""
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="daban")
    try:
        future = executor.submit(func)
        try:
            return future.result(timeout=max(1.0, timeout)), None
        except FutureTimeoutError:
            logger.warning("打板王：%s 超过 %.0fs 未返回", label, timeout)
            return None, f"超过 {int(timeout)}s 未返回"
        except Exception as exc:
            logger.warning("打板王：%s 失败 %s", label, _err(exc))
            return None, _err(exc)
    finally:
        executor.shutdown(wait=False)


def run_parallel(
    tasks: Dict[str, Callable[[], Any]], *, timeout: float, workers: int = 8
) -> Dict[str, Tuple[Optional[Any], Optional[str]]]:
    """并发跑一组任务，共用一个墙钟预算。返回 ``{key: (结果, 错误)}``。"""
    import time

    results: Dict[str, Tuple[Optional[Any], Optional[str]]] = {}
    if not tasks:
        return results
    pool = ThreadPoolExecutor(max_workers=max(1, min(workers, len(tasks))), thread_name_prefix="daban-fetch")
    deadline = time.monotonic() + max(1.0, timeout)
    try:
        futures = {key: pool.submit(fn) for key, fn in tasks.items()}
        for key, future in futures.items():
            try:
                results[key] = (future.result(timeout=max(0.1, deadline - time.monotonic())), None)
            except FutureTimeoutError:
                results[key] = (None, f"超过总预算 {int(timeout)}s")
            except Exception as exc:
                results[key] = (None, _err(exc))
    finally:
        pool.shutdown(wait=False)
    return results


__all__ = [
    "call_with_timeout",
    "effective_trade_date",
    "failed",
    "fetch_blocks",
    "fetch_em",
    "fetch_hot_rank",
    "fetch_klines",
    "fetch_ladder",
    "fetch_limit_pool",
    "fetch_theme_news",
    "previous_trade_date",
    "recent_trade_dates",
    "run_parallel",
]
