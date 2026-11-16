# -*- coding: utf-8 -*-
"""资金流向：汪汪队（宽基 ETF）与机构（龙虎榜机构席位）的日频序列。

两者都只做**展示**，不参与行业打分（权重需要重新标定才敢动，见 ``scoring``）。

口径：
- **汪汪队**：宽基 ETF 份额净申购（份额增量 × 当日收盘价，估算净流入金额）+ 宽基 ETF 成交额相对
  20 日均值的倍数。汇金这类资金以申购宽基 ETF 为主，份额是交易所披露的，方向可验证；成交额放大
  只能说明「有人在买」，分不清申赎，所以两个数并列展示，不合成一个分数。
- **机构**：龙虎榜机构专用席位净买。交易所披露，口径最硬，但只覆盖上榜的票（是下限，不是全量）。

历史回溯能力（决定「最近两周」哪些能回补、哪些只能现攒）：
- 沪市 ETF 份额：接口支持任意交易日 → 2 周可回补；
- 深市 ETF 份额：接口只给当日 → 从今天起每天刷新才会积累；
- 宽基 ETF 成交额 / 收盘价：日 K 按日期区间拉 → 2 周可回补；
- 龙虎榜机构：按日期区间拉 → 2 周可回补。
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from src.services.industry_funds.fetchers import (
    _call_with_timeout,
    _fetch_sse_etf_shares,
    _trading_days,
    _to_float,
)

logger = logging.getLogger(__name__)

# 汇金等资金最常买的宽基 ETF。净申购与成交额倍数都只看这些。
BROAD_ETF = {
    "510300": "沪深300ETF华泰柏瑞",
    "510310": "沪深300ETF易方达",
    "510330": "沪深300ETF华夏",
    "159919": "沪深300ETF嘉实",
    "510050": "上证50ETF",
    "510500": "中证500ETF",
    "588000": "科创50ETF",
    "159915": "创业板ETF",
}

FLOW_DAYS_DEFAULT = 10          # 最近两周（10 个交易日）
FLOW_CACHE_TTL_SEC = 600
_FLOW_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_FLOW_LOCK = threading.Lock()


def clear_cache() -> None:
    with _FLOW_LOCK:
        _FLOW_CACHE.clear()


def _yi(value: Optional[float], unit: float = 1e8) -> Optional[float]:
    return round(value / unit, 4) if value is not None else None


def _window(days: int) -> List[date]:
    """最近 ``days`` 个交易日（正序，末尾 = 最近一个交易日）+ 它前面那一个交易日做基线。

    ``_trading_days`` 是从新到旧返回的，这里反转后再切。
    """
    ascending = list(reversed(_trading_days(max(2, int(days)) + 1)))
    return ascending[-(int(days) + 1):]


def _dates(days: int) -> List[str]:
    """窗口内的交易日（不含基线日）。"""
    return [d.isoformat() for d in _window(days)[1:]]


def _avg_before(series: Dict[str, Dict[str, float]], day: str, field: str, n: int = 20) -> Optional[float]:
    """``day`` 之前最近 ``n`` 个交易日的均值（不含当天，避免自己比自己）。"""
    values = [v[field] for d, v in sorted(series.items()) if d < day and v.get(field)]
    if not values:
        return None
    tail = values[-n:]
    return sum(tail) / len(tail)


# ---------------------------------------------------------------------- 汪汪队


def _sse_shares_by_date(dates: List[str], *, timeout: float) -> Tuple[Dict[str, Dict[str, Dict[str, Any]]], str]:
    """沪市宽基 ETF 每日份额快照：``{date: {code: {...}}}``。"""
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    notes: List[str] = []

    def _load(day: str) -> None:
        try:
            shares = _fetch_sse_etf_shares(date.fromisoformat(day), timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{day} 沪市 ETF 份额取数失败：{type(exc).__name__}")
            return
        picked = {code: item for code, item in (shares or {}).items() if code in BROAD_ETF}
        if picked:
            out[day] = picked

    with ThreadPoolExecutor(max_workers=8, thread_name_prefix="industry-flows-sse") as pool:
        list(pool.map(_load, dates))
    return out, "；".join(notes[:2])


def _etf_daily(*, timeout: float, lookback_days: int = 60) -> Tuple[Dict[str, Dict[str, Dict[str, float]]], str]:
    """宽基 ETF 每日成交额与收盘价：``{code: {date: {"amount": 元, "close": 元}}}``。

    走同花顺日 K（``data_provider.ths_chart_fetcher``）：akshare 的 ``fund_etf_hist_em`` 并发拉 8 只会被
    上游断连（实测 RemoteDisconnected），同花顺同一个 farm 给 ETF 也带成交额，且能按年回溯。
    """
    from data_provider import ths_chart_fetcher as ths

    out: Dict[str, Dict[str, Dict[str, float]]] = {}
    notes: Dict[str, str] = {}

    def _load(code: str) -> None:
        def _fetch():
            return ths.fetch_kline(code, period="day", adjust="", limit=max(30, lookback_days), timeout=timeout)

        payload, err = _call_with_timeout(_fetch, timeout=timeout, label=f"ETF 日线 {code}")
        if payload is None:
            notes[code] = err or "取数失败"
            return
        series: Dict[str, Dict[str, float]] = {}
        for row in payload.get("rows") or []:
            day = str(row.get("time") or "")
            amount = row.get("amount")
            close = row.get("close")
            if len(day) == 8 and (amount or close):
                series[f"{day[:4]}-{day[4:6]}-{day[6:]}"] = {"amount": float(amount or 0), "close": float(close or 0)}
        if series:
            out[code] = series

    with ThreadPoolExecutor(max_workers=8, thread_name_prefix="industry-flows-etf") as pool:
        list(pool.map(_load, list(BROAD_ETF)))
    reason = "；".join(f"{code} {msg}" for code, msg in list(notes.items())[:3])
    return out, reason


def _szse_today(*, timeout: float) -> Tuple[Optional[str], Dict[str, Dict[str, Any]], str]:
    """深市 ETF 份额只有当日快照（接口不支持历史日期），拿不到就如实降级。"""
    from src.services.industry_funds.fetchers import _fetch_szse_etf_shares

    def _load():
        return _fetch_szse_etf_shares(timeout=timeout)

    result, err = _call_with_timeout(_load, timeout=timeout, label="深交所 ETF 份额")
    if not result:
        return None, {}, err or "深市 ETF 份额取不到"
    shares, stat_date = result
    picked = {code: item for code, item in (shares or {}).items() if code in BROAD_ETF}
    return (stat_date or None), picked, ""


def build_national_team(days: int = FLOW_DAYS_DEFAULT, *, timeout: float = 30.0) -> Dict[str, Any]:
    """汪汪队：最近 ``days`` 个交易日的宽基 ETF 净申购与成交额倍数。

    某个交易日沪市还没更新（盘中 / 节假日）时该日 ``net_shares`` 为 None，不拿 0 冒充「没变动」。
    """
    window = [d.isoformat() for d in _window(days)]
    base_day, dates = window[0], window[1:]
    sse, sse_note = _sse_shares_by_date(window, timeout=timeout)
    daily, daily_note = _etf_daily(timeout=timeout)
    szse_date, szse_shares, szse_note = _szse_today(timeout=timeout)

    # 基线只认「相邻交易日」：跨过缺失日拿更早的快照，会把多日变动算成单日净申购。
    previous_of = {day: (dates[i - 1] if i else base_day) for i, day in enumerate(dates)}
    per_day: List[Dict[str, Any]] = []
    for day in dates:
        prev = previous_of[day]
        rows: List[Dict[str, Any]] = []
        net_shares: Optional[float] = None
        net_amount: Optional[float] = None
        missing = not sse.get(day)
        baseline_missing = not sse.get(prev)
        if missing or baseline_missing:
            # 当天或基线缺数据：给 None（未知），不拿 0 冒充，也不跨日累计。
            for code, name in BROAD_ETF.items():
                item = (sse.get(day) or {}).get(code)
                if not item:
                    continue
                bars = daily.get(code) or {}
                today_amount = (bars.get(day) or {}).get("amount")
                avg20 = _avg_before(bars, day, "amount")
                rows.append({
                    "code": code, "name": name, "shares": _yi(item.get("shares")), "delta_shares": None,
                    "delta_amount": None, "turnover": _yi(today_amount),
                    "turnover_ratio": round(today_amount / avg20, 3) if (today_amount and avg20) else None,
                })
        else:
            shares_total = 0.0
            amount_total = 0.0
            priced = False
            for code, name in BROAD_ETF.items():
                item = (sse.get(day) or {}).get(code)
                if not item:
                    continue
                base = (sse.get(prev) or {}).get(code)
                delta = None
                if base and base.get("shares") and item.get("shares"):
                    delta = float(item["shares"]) - float(base["shares"])
                    shares_total += delta
                bars = daily.get(code) or {}
                close = (bars.get(day) or {}).get("close")
                amount = delta * close if (delta is not None and close) else None
                if amount is not None:
                    amount_total += amount
                    priced = True
                today_amount = (bars.get(day) or {}).get("amount")
                avg20 = _avg_before(bars, day, "amount")
                rows.append({
                    "code": code, "name": name,
                    "shares": _yi(item.get("shares")),
                    "delta_shares": _yi(delta),
                    "delta_amount": _yi(amount),
                    "turnover": _yi(today_amount),
                    "turnover_ratio": round(today_amount / avg20, 3) if (today_amount and avg20) else None,
                })
            net_shares = _yi(shares_total)
            net_amount = _yi(amount_total) if priced else None
        rows.sort(key=lambda r: abs(r.get("delta_amount") or r.get("delta_shares") or 0), reverse=True)
        turnover_values = [((daily.get(c) or {}).get(day) or {}).get("amount") for c in BROAD_ETF]
        turnover_total = sum(v for v in turnover_values if v) or None
        avg_total = [m for m in (_avg_before(daily.get(c) or {}, day, "amount") for c in BROAD_ETF) if m]
        per_day.append({
            "date": day,
            "net_shares": net_shares,
            "net_amount": net_amount,
            "etf_count": len(rows),
            "turnover": _yi(turnover_total),
            "turnover_ratio": round(turnover_total / sum(avg_total), 3) if (turnover_total and avg_total) else None,
            "sse_missing": missing,
            "baseline_missing": baseline_missing,
            "etfs": rows,
        })

    missing_baselines = [d["date"] for d in per_day if d.get("baseline_missing")]
    series = [{"date": d["date"], "net_shares": d["net_shares"], "net_amount": d["net_amount"],
               "turnover_ratio": d["turnover_ratio"]} for d in per_day]
    inflow_days = 0
    for item in reversed(series):
        if item.get("net_shares") is None:
            continue
        if (item.get("net_shares") or 0) > 0:
            inflow_days += 1
        else:
            break
    return {
        "days": per_day,
        "series": series,
        "inflow_streak": inflow_days,
        "as_of": next((d["date"] for d in reversed(per_day) if not d["sse_missing"]), ""),
        "base_date": base_day,
        "coverage": {
            "sse": "沪市份额可回溯" if sse else "沪市份额不可用",
            "szse": (f"深市只有 {szse_date} 当日快照（接口不支持历史），从今天起每日刷新才会积累"
                     if szse_shares else "深市份额不可用"),
        },
        "degraded": bool(sse_note or daily_note or missing_baselines),
        "degraded_reason": "；".join(x for x in (
            sse_note, daily_note,
            f"{len(missing_baselines)} 个交易日缺相邻基线（份额未知，按未知展示）" if missing_baselines else "",
        ) if x),
        "szse": {"date": szse_date, "etfs": [
            {"code": c, "name": n, "shares": _yi((szse_shares.get(c) or {}).get("shares"))}
            for c, n in BROAD_ETF.items() if c in szse_shares
        ]},
        "szse_note": szse_note,
    }


# ---------------------------------------------------------------------- 机构


def build_institutions(days: int = FLOW_DAYS_DEFAULT, *, timeout: float = 30.0) -> Dict[str, Any]:
    """机构：最近 ``days`` 个交易日的龙虎榜机构席位净买（每日汇总 + 当日个股明细）。"""
    dates = _dates(days)
    if not dates:
        return {"days": [], "series": [], "degraded": True, "degraded_reason": "取不到交易日历"}
    start, end = dates[0].replace("-", ""), dates[-1].replace("-", "")

    def _load():
        import akshare as ak

        return ak.stock_lhb_jgmmtj_em(start_date=start, end_date=end)

    df, err = _call_with_timeout(_load, timeout=timeout, label="东财龙虎榜机构买卖统计")
    if df is None or getattr(df, "empty", True):
        return {"days": [], "series": [], "degraded": True, "degraded_reason": err or "龙虎榜机构数据为空"}

    by_day: Dict[str, Dict[str, Any]] = {
        d: {"net": 0.0, "buy": 0.0, "sell": 0.0, "count": 0, "stocks": []} for d in dates
    }
    for record in df.to_dict("records"):
        day = str(record.get("上榜日期") or "").strip()[:10]
        bucket = by_day.get(day)
        if bucket is None:
            continue
        net = _to_float(record.get("机构买入净额")) or 0.0
        buy = _to_float(record.get("机构买入总额")) or 0.0
        sell = _to_float(record.get("机构卖出总额")) or 0.0
        bucket["net"] += net
        bucket["buy"] += buy
        bucket["sell"] += sell
        bucket["count"] += 1
        bucket["stocks"].append({
            "code": str(record.get("代码") or "").strip(),
            "name": str(record.get("名称") or "").strip(),
            "close": _to_float(record.get("收盘价")),
            "change_pct": _to_float(record.get("涨跌幅")),
            "buyer_count": _to_float(record.get("买方机构数")),
            "seller_count": _to_float(record.get("卖方机构数")),
            "buy": _yi(buy),
            "sell": _yi(sell),
            "net": _yi(net),
            "net_ratio": _to_float(record.get("机构净买额占总成交额比")),
            "turnover_rate": _to_float(record.get("换手率")),
            "reason": str(record.get("上榜原因") or "").strip(),
        })

    per_day = []
    for day in dates:
        bucket = by_day[day]
        stocks = sorted(bucket["stocks"], key=lambda s: s.get("net") or 0, reverse=True)
        pending = bucket["count"] == 0
        per_day.append({
            "date": day,
            "net": _yi(bucket["net"]) if not pending else None,
            "buy": _yi(bucket["buy"]) if not pending else None,
            "sell": _yi(bucket["sell"]) if not pending else None,
            "count": bucket["count"], "pending": pending, "stocks": stocks,
        })
    return {
        "days": per_day,
        "series": [{"date": d["date"], "net": d["net"], "count": d["count"]} for d in per_day],
        "as_of": dates[-1],
        "degraded": False,
        "degraded_reason": "",
    }


# ---------------------------------------------------------------------- 汇总


def _cached(key: str, builder) -> Dict[str, Any]:
    now = time.time()
    with _FLOW_LOCK:
        hit = _FLOW_CACHE.get(key)
        if hit and now - hit[0] < FLOW_CACHE_TTL_SEC:
            return hit[1]
    value = builder()
    with _FLOW_LOCK:
        _FLOW_CACHE[key] = (time.time(), value)
    return value


def flow_series(days: int = FLOW_DAYS_DEFAULT, *, timeout: float = 30.0) -> Dict[str, Any]:
    """资金流向：两个模块的两周序列 + 交易日列表。任一路失败只降级该模块。"""
    days = max(2, min(int(days), 30))

    def _build() -> Dict[str, Any]:
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="industry-flows") as pool:
            nt_future = pool.submit(build_national_team, days, timeout=timeout)
            inst_future = pool.submit(build_institutions, days, timeout=timeout)
            national = nt_future.result()
            institutions = inst_future.result()
        return {
            "days": _dates(days),
            "national_team": national,
            "institutions": institutions,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }

    return _cached(f"flows:{days}", _build)


def flow_dates(days: int = FLOW_DAYS_DEFAULT) -> Dict[str, Any]:
    """日期 tab 用：最近 ``days`` 个交易日（正序）+ 该日有没有总览快照。"""
    dates = _dates(days)
    have_overview = set()
    try:
        from src.repositories.industry_funds_repo import IndustryFundsRepository

        have_overview = {row.trade_date for row in IndustryFundsRepository().history(limit=60)}
    except Exception as exc:  # noqa: BLE001
        logger.debug("资金流向：读取已有快照日期失败 %s", exc)
    return {
        "days": [{"date": d, "has_overview": d in have_overview} for d in dates],
        "today": _trading_days(1)[0].isoformat(),
    }


__all__ = [
    "BROAD_ETF", "FLOW_DAYS_DEFAULT", "build_institutions", "build_national_team", "clear_cache", "flow_dates",
    "flow_series",
]
