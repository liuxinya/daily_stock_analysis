# -*- coding: utf-8 -*-
"""日 K 形态：涨停识别、连板段、反包、首阴、股性（涨停次日溢价）。

日 K 来自 ``chart_service.get_kline``（同花顺前复权）。涨停按涨幅判断：主板 10%、
创业板 / 科创板 20%、北交所 30%，允许 0.3 个百分点的四舍五入误差。复权价的涨幅与
不复权一致（同一天同比例缩放），所以前复权可以直接用。
"""

from __future__ import annotations

from statistics import mean
from typing import Any, Dict, List, Optional


def limit_pct(code: str) -> float:
    text = str(code or "")
    if text.startswith(("300", "301", "688", "689")):
        return 20.0
    if text.startswith(("92", "8", "43")):
        return 30.0
    return 10.0


def bar_changes(rows: List[Dict[str, Any]]) -> List[Optional[float]]:
    """逐根涨跌幅（%），首根为 None。"""
    out: List[Optional[float]] = [None]
    for prev, cur in zip(rows, rows[1:]):
        pc, cc = prev.get("close"), cur.get("close")
        out.append(((cc / pc) - 1) * 100 if pc and cc is not None else None)
    return out


def limit_flags(code: str, rows: List[Dict[str, Any]]) -> List[bool]:
    threshold = limit_pct(code) - 0.3
    return [chg is not None and chg >= threshold for chg in bar_changes(rows)]


def analyze_kline(code: str, rows: List[Dict[str, Any]], *, today: str) -> Dict[str, Any]:
    """返回候选需要的形态特征。``today`` 为 ``YYYY-MM-DD`` 或 ``YYYYMMDD``。

    ``rows`` 末根若不是 ``today``（盘后上游还没更新），按「今天缺失」处理，
    只有历史类特征（股性、近期连板段）可用。
    """
    today_key = str(today or "").replace("-", "")
    if not rows:
        return {"available": False}
    rows = [r for r in rows if r.get("close") is not None]
    flags = limit_flags(code, rows)
    changes = bar_changes(rows)
    has_today = bool(rows) and str(rows[-1].get("time") or "").replace("-", "") == today_key
    hist_end = len(rows) - 1 if has_today else len(rows)

    # 股性：历史上每次涨停的次日涨跌幅（今天这根不算「次日」样本的起点）。
    next_day = [changes[i + 1] for i in range(1, hist_end - 1) if flags[i] and changes[i + 1] is not None]
    premium = round(mean(next_day), 2) if next_day else None
    limit_count = sum(1 for i in range(hist_end) if flags[i])

    # 今天之前（截至昨日）的最长连板段，以及它结束后过了几天。
    best_streak = 0
    run = 0
    for i in range(hist_end):
        if flags[i]:
            run += 1
            best_streak = max(best_streak, run)
        else:
            run = 0
    window_start = max(0, hist_end - 20)
    recent_streak, recent_end = 0, -1
    run = 0
    for i in range(window_start, hist_end):
        if flags[i]:
            run += 1
            if run >= recent_streak:
                recent_streak, recent_end = run, i
        else:
            run = 0
    gap_days = (hist_end - 1 - recent_end) if recent_end >= 0 else None

    today_change = changes[-1] if has_today else None
    today_limit = flags[-1] if has_today else None
    yesterday_limit = flags[hist_end - 1] if hist_end >= 1 else None
    # 昨日的连板数（截至昨日）。
    y_run = 0
    for i in range(hist_end - 1, -1, -1):
        if flags[i]:
            y_run += 1
        else:
            break

    out = {
        "available": True,
        "bars": len(rows),
        "has_today": has_today,
        "limit_count": limit_count,
        "next_day_premium": premium,
        "next_day_samples": len(next_day),
        "recent_streak": recent_streak,
        "recent_streak_gap": gap_days,
        "best_streak": best_streak,
        "today_change": round(today_change, 2) if today_change is not None else None,
        "today_limit": today_limit,
        "yesterday_limit": yesterday_limit,
        "yesterday_run": y_run,
        "volume_ratio": None,
        "lower_shadow": None,
        "body_pct": None,
    }
    if has_today and len(rows) >= 6:
        vols = [r.get("volume") for r in rows[-6:-1] if r.get("volume")]
        vol = rows[-1].get("volume")
        if vols and vol:
            out["volume_ratio"] = round(vol / mean(vols), 2)
        bar = rows[-1]
        o, h, l, c = bar.get("open"), bar.get("high"), bar.get("low"), bar.get("close")
        if None not in (o, h, l, c) and h > l:
            out["lower_shadow"] = round((min(o, c) - l) / (h - l), 3)
            out["body_pct"] = round((c - o) / o * 100, 2) if o else None
    return out


def is_rebound_first_board(feat: Dict[str, Any]) -> bool:
    """反包首板：近 20 日出现过 ≥3 连板，断板后至少换手 1 天，今天重新涨停（科比 #18）。"""
    return bool(
        feat.get("available")
        and feat.get("today_limit")
        and not feat.get("yesterday_limit")
        and (feat.get("recent_streak") or 0) >= 3
        and feat.get("recent_streak_gap") is not None
        and 1 <= feat["recent_streak_gap"] <= 10
    )


def is_first_negative(feat: Dict[str, Any]) -> bool:
    """首阴：昨日还在连板（≥3），今天收跌（陈小群二波反包的前提）。"""
    return bool(
        feat.get("available")
        and feat.get("has_today")
        and (feat.get("yesterday_run") or 0) >= 3
        and feat.get("today_change") is not None
        and feat["today_change"] < 0
    )


__all__ = [
    "analyze_kline",
    "bar_changes",
    "is_first_negative",
    "is_rebound_first_board",
    "limit_flags",
    "limit_pct",
]
