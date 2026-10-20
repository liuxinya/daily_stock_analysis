# -*- coding: utf-8 -*-
"""「我是打板王」的首板 / 一进二历史回测。

用途：两篇原文对首板只给了定位（科比：绝对低位做首板或一进二；陈小群：回暖期聚焦新题材首板 / 二板），
没有给任何量化标准。这里用同花顺涨停数据中心的历史数据（约一年滚动窗口）回放每一个交易日，
统计不同特征下首板的次日表现，给阈值提供数据依据，而不是凭经验拍。

关键点：

- **周期、题材、角色直接复用线上模块**（``cycle`` / ``themes`` / ``roles``），回测里的「主线」「阶段」
  与页面上看到的是同一套口径。唯一差别是回测不拉同花顺题材榜（体积太大），题材持续天数只按本地
  历史推算。
- 「昨日涨停今日表现」用昨日涨停股的日 K 推算，不依赖只保留 30 天的东财 previous 池。
- 结果按特征分桶输出样本数、次日涨停率、次日一字率、次日高开幅度和三种买法（都是理论成交：涨停价买入，未计手续费、滑点和排队买不到）：

- **首板打板**：盘中第一次触板就以涨停价买入，**不论最后封没封住**（只看封住的首板是幸存者偏差，
  会漏掉打板买进后炸板的亏损）。封住的次日涨停则多拿一天（科比 #23），否则次日卖（科比 #25）。
- **一进二**：首板次日盘中触板、且不是一字（买得到）时以涨停价买入，卖出规则同上。
- **炸板次日**：炸板当天收盘买入，次日卖。用来看「首板预判」候选池的底色。

卖出价同时给「开盘卖」与「收盘卖」两种，两人都没写具体卖点，只说「第二天不涨停基本都会走」。
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import mean, median, pstdev
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from src.services.daban import cycle as cycle_mod
from src.services.daban.constants import load_rules
from src.services.daban.kline import limit_pct
from src.services.daban.roles import assign_roles
from src.services.daban.themes import build_themes, split_reason

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = os.path.join("data", "daban_backtest")
TOLERANCE = 0.3  # 涨停判定允许的涨幅误差（百分点），与 kline.limit_flags 一致


# ---------------------------------------------------------------------- 缓存


class BacktestCache:
    """把每日池子与每只股票的日 K 缓存到本地 JSON，重跑回测不必再打上游。"""

    def __init__(self, root: str = DEFAULT_CACHE_DIR):
        self.root = root
        os.makedirs(os.path.join(root, "pools"), exist_ok=True)
        os.makedirs(os.path.join(root, "klines"), exist_ok=True)

    def _read(self, path: str) -> Optional[Any]:
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return None

    def _write(self, path: str, payload: Any) -> None:
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        os.replace(tmp, path)

    def pools(self, day: str, fetch: Callable[[str], Optional[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
        path = os.path.join(self.root, "pools", f"{day}.json")
        cached = self._read(path)
        if cached is not None:
            return cached
        payload = fetch(day)
        if payload is not None:
            self._write(path, payload)
        return payload

    def kline(self, code: str, fetch: Callable[[str], Optional[List[Dict[str, Any]]]], *, min_last: str) -> Optional[List[Dict[str, Any]]]:
        """``min_last``：缓存的最后一根 K 线早于它就视为过期重抓（YYYYMMDD）。"""
        path = os.path.join(self.root, "klines", f"{code}.json")
        cached = self._read(path)
        if cached and str(cached[-1].get("time") or "") >= min_last:
            return cached
        rows = fetch(code)
        if rows:
            self._write(path, rows)
        return rows


def fetch_day_pools(day: str, *, timeout: float = 20.0) -> Optional[Dict[str, Any]]:
    """一天的涨停 / 炸板 / 跌停池与连板天梯。非交易日（涨停池为空）返回 None。"""
    from data_provider import limit_up_fetcher as lu

    zt = lu.fetch_ths_pool("zt", day, timeout=timeout)
    if not zt["rows"]:
        return None
    return {
        "date": day,
        "zt": zt["rows"],
        "zb": lu.fetch_ths_pool("zb", day, timeout=timeout)["rows"],
        "dt": lu.fetch_ths_pool("dt", day, timeout=timeout)["rows"],
        "ladder": lu.fetch_ths_ladder(day, timeout=timeout),
    }


def fetch_kline_rows(code: str, *, limit: int = 400, timeout: float = 15.0) -> Optional[List[Dict[str, Any]]]:
    from data_provider import ths_chart_fetcher as ths

    payload = ths.fetch_kline(code, period="day", adjust="qfq", limit=limit, timeout=timeout)
    return [
        {k: row.get(k) for k in ("time", "open", "high", "low", "close", "volume")}
        for row in payload.get("rows") or []
        if row.get("close") is not None
    ]


# ---------------------------------------------------------------------- 日 K 结果


class KlineBook:
    """按代码索引的日 K，提供「某天之后第 n 根」与涨停 / 一字判定。"""

    def __init__(self, klines: Dict[str, List[Dict[str, Any]]]):
        self.rows = klines
        self.index = {code: {str(r["time"]): i for i, r in enumerate(rows)} for code, rows in klines.items()}

    def at(self, code: str, day: str, offset: int = 0) -> Optional[Dict[str, Any]]:
        idx = self.index.get(code, {}).get(day.replace("-", ""))
        if idx is None:
            return None
        j = idx + offset
        rows = self.rows[code]
        return rows[j] if 0 <= j < len(rows) else None

    def change(self, code: str, day: str) -> Optional[float]:
        bar, prev = self.at(code, day), self.at(code, day, -1)
        if not bar or not prev or not prev.get("close"):
            return None
        return (bar["close"] / prev["close"] - 1) * 100

    def limit_count(self, code: str, day: str, lookback: int) -> Optional[int]:
        """``day`` 之前 ``lookback`` 根里的涨停次数（不含 ``day``）。"""
        idx = self.index.get(code, {}).get(day.replace("-", ""))
        if idx is None:
            return None
        rows, pct, count = self.rows[code], limit_pct(code), 0
        for j in range(max(1, idx - lookback), idx):
            if rows[j - 1]["close"] and (rows[j]["close"] / rows[j - 1]["close"] - 1) * 100 >= pct - TOLERANCE:
                count += 1
        return count


def _is_limit(change_pct: Optional[float], code: str) -> bool:
    return change_pct is not None and change_pct >= limit_pct(code) - TOLERANCE


def _consecutive(book: KlineBook, code: str, days: List[str], i: int, count: int) -> Optional[List[Dict[str, Any]]]:
    """第 i 个交易日起连续 ``count`` 根 K 线；中间有停牌（日期对不上）返回 None。"""
    if i + count > len(days):
        return None
    bars = []
    for k in range(count):
        bar = book.at(code, days[i], k)
        if not bar or str(bar["time"]) != days[i + k].replace("-", ""):
            return None
        bars.append(bar)
    return bars


def _ret(exit_price: float, buy: float) -> float:
    return (exit_price / buy - 1) * 100


def _hold_exit(bars: List[Dict[str, Any]], start: int, code: str) -> Tuple[float, float]:
    """买入日为 ``bars[start - 1]``，从 ``bars[start]`` 起卖：当天涨停就再拿一天（科比 #23），否则当天卖。

    返回 ``(开盘卖出价, 收盘卖出价)``。拿到最后一根还在涨停也就在那一根卖。
    """
    j = start
    while j < len(bars) - 1 and _is_limit(_ret(bars[j]["close"], bars[j - 1]["close"]), code):
        j += 1
    return bars[j]["open"], bars[j]["close"]


def first_touch_outcome(book: KlineBook, code: str, days: List[str], i: int, *, sealed: bool) -> Optional[Dict[str, Any]]:
    """首板打板：第 i 天盘中触板时以涨停价买入（不论最后封没封住）。

    封住的次日涨停则多拿一天；没封住（炸板）的次日就卖。卖出给开盘、收盘两个价。
    这是诚实口径：只算封住的首板会漏掉打板买进后炸板的那部分亏损（幸存者偏差）。
    """
    prev = book.at(code, days[i], -1)
    bars = _consecutive(book, code, days, i, 3)
    if not prev or not bars or not prev.get("close"):
        return None
    d0 = bars[0]
    if _ret(d0["high"], prev["close"]) < limit_pct(code) - TOLERANCE:
        return None  # 日 K 与池子对不上（复权或数据缺口），丢弃
    buy = d0["high"]
    if sealed:
        sell_open, sell_close = _hold_exit(bars, 1, code)
    else:
        sell_open, sell_close = bars[1]["open"], bars[1]["close"]
    return {
        "d0_close_vs_buy": _ret(d0["close"], buy),
        "n1_gap": _ret(bars[1]["open"], d0["close"]),
        "s1_open": _ret(sell_open, buy),
        "s1_close": _ret(sell_close, buy),
    }


def one_into_two_outcome(book: KlineBook, code: str, days: List[str], i: int) -> Optional[Dict[str, Any]]:
    """一进二：第 i 天封住首板；第 i+1 天盘中触板且不是一字（买得到）时以涨停价买入。

    第 i+1 天开盘幅度在买入前就已知，所以「高开放弃」可以按 ``n1_gap`` 分桶直接检验。
    """
    bars = _consecutive(book, code, days, i, 4) or _consecutive(book, code, days, i, 3)
    if not bars:
        return None
    pct, base = limit_pct(code), bars[0]["close"]
    n1 = bars[1]
    n1_change = _ret(n1["close"], base)
    n1_touch = _ret(n1["high"], base) >= pct - TOLERANCE
    n1_sealed = _is_limit(n1_change, code)
    n1_yizi = n1_sealed and (n1["high"] - n1["low"]) / base < 0.003
    out = {
        "n1_gap": _ret(n1["open"], base),
        "n1_change": n1_change,
        "n1_touch": n1_touch,
        "n1_sealed": n1_sealed,
        "n1_yizi": n1_yizi,
        "buyable": n1_touch and not n1_yizi,
        "s2_open": None,
        "s2_close": None,
    }
    if out["buyable"]:
        buy = n1["high"]
        if n1_sealed:
            sell_open, sell_close = _hold_exit(bars, 2, code)
        else:
            sell_open, sell_close = bars[2]["open"], bars[2]["close"]
        out["s2_open"] = _ret(sell_open, buy)
        out["s2_close"] = _ret(sell_close, buy)
    return out


def broken_outcome(book: KlineBook, code: str, days: List[str], i: int) -> Optional[Dict[str, Any]]:
    """炸板股（首板预判候选）：收盘买入，次日开盘 / 收盘卖；记录次日是否涨停。"""
    bars = _consecutive(book, code, days, i, 2)
    if not bars:
        return None
    base, n1 = bars[0]["close"], bars[1]
    return {
        "n1_gap": _ret(n1["open"], base),
        "n1_touch": _ret(n1["high"], base) >= limit_pct(code) - TOLERANCE,
        "n1_sealed": _is_limit(_ret(n1["close"], base), code),
        "s3_open": _ret(n1["open"], base),
        "s3_close": _ret(n1["close"], base),
    }


# ---------------------------------------------------------------------- 分桶


def _band(value: Optional[float], edges: Iterable[float], labels: List[str]) -> str:
    if value is None:
        return "无数据"
    for edge, label in zip(edges, labels):
        if value < edge:
            return label
    return labels[-1]


def time_bucket(hhmmss: str) -> str:
    t = str(hhmmss or "")
    if not t:
        return "无数据"
    if t <= "093000":
        return "竞价/开盘即封"
    if t < "100000":
        return "09:30-10:00"
    if t < "113100":
        return "10:00-11:30"
    if t < "143000":
        return "13:00-14:30"
    return "14:30 以后（尾盘）"


def turnover_bucket(v):
    return _band(v, (3, 8, 15, 25, 1e9), ["<3%", "3-8%", "8-15%", "15-25%", "≥25%"])


def circ_bucket(v):
    return _band(None if v is None else v / 1e8, (30, 60, 100, 200, 1e12), ["<30亿", "30-60亿", "60-100亿", "100-200亿", "≥200亿"])


def price_bucket(v):
    return _band(v, (5, 10, 20, 40, 1e9), ["<5元", "5-10元", "10-20元", "20-40元", "≥40元"])


def seal_bucket(seal, circ):
    ratio = (seal / circ * 100) if (seal and circ) else None
    return _band(ratio, (0.5, 1, 3, 1e9), ["<0.5%", "0.5-1%", "1-3%", "≥3%"])


def gap_bucket(v):
    return _band(v, (-3, 0, 3, 6, 9, 1e9), ["低开 >3%", "低开 0-3%", "高开 0-3%", "高开 3-6%", "高开 6-9%", "高开 ≥9%（含一字）"])


def rate_bucket(v):
    return _band(None if v is None else v * 100, (15, 25, 35, 1e9), ["<15%", "15-25%", "25-35%", "≥35%"])


def premium_bucket(v):
    return _band(v, (-2, 0, 2, 4, 1e9), ["≤-2%", "-2~0%", "0~2%", "2~4%", "≥4%"])


def zt_bucket(v):
    return _band(v, (30, 50, 80, 1e9), ["<30家", "30-50家", "50-80家", "≥80家"])


def count_bucket(v):
    return _band(v, (1, 2, 4, 1e9), ["0次", "1次", "2-3次", "≥4次"])


# ---------------------------------------------------------------------- 回放


@dataclass
class ReplayResult:
    days: List[str]
    touches: List[Dict[str, Any]]
    one_into_two: List[Dict[str, Any]]
    broken: List[Dict[str, Any]]
    day_meta: List[Dict[str, Any]]


def _universe(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    from data_provider.limit_up_fetcher import in_universe

    return [dict(r) for r in rows if in_universe(r.get("code"), r.get("name")) and not r.get("is_new")]


def _theme_link(tags: List[str], themes: List[Dict[str, Any]]) -> Tuple[str, Optional[Dict[str, Any]]]:
    """标签落在当日哪个题材里（按排名先到先得）。"""
    for theme in themes:
        keys = [theme["key"], *theme.get("tags", [])]
        if any(k in tag or tag in k for tag in tags for k in keys if len(k) >= 2):
            return ("主线" if theme.get("is_main") else "非主线题材"), theme
    return "无题材", None


def _day_features(stage, kobe, metrics) -> Dict[str, Any]:
    tier1 = cycle_mod.tier_rate(metrics, 1)
    fall = int(kobe.get("fall_day") or 0)
    return {
        "stage": stage["label"],
        "kobe": kobe["label"] if not fall else f"主跌第{min(fall, 3)}天{'起' if fall >= 3 else ''}",
        "day_zt": zt_bucket(metrics.get("zt_count")),
        "day_tier1": rate_bucket(tier1),
        "day_premium": premium_bucket(metrics.get("yzt_premium")),
    }


def _common(row: Dict[str, Any], code: str, book: KlineBook, day: str) -> Dict[str, Any]:
    return {
        "first_time": time_bucket(row.get("first_time")),
        "circ_mv": circ_bucket(row.get("circ_mv")),
        "price": price_bucket(row.get("price")),
        "market": "20%涨跌幅" if limit_pct(code) == 20 else "10%涨跌幅",
        "limit_memory": count_bucket(book.limit_count(code, day, 60)),
    }


def replay(pools: Dict[str, Dict[str, Any]], book: KlineBook, *, rules: Optional[Dict[str, Any]] = None) -> ReplayResult:
    """按交易日顺序回放，生成三类事件：首板触板、一进二、炸板（首板预判候选）。

    口径：

    - 首板触板的「环境」特征取**前一交易日收盘**（打板是盘中决策，当天的周期要收盘才知道）。
    - 一进二的特征全部取首板当天收盘（买点在次日，信息都已知），另加次日开盘幅度（买前可见）。
    - 炸板股没有同花顺涨停原因，题材关联用它近 90 天的历史涨停原因。
    """
    rules = rules or load_rules(None)
    days = sorted(pools)
    touches: List[Dict[str, Any]] = []
    one_into_two: List[Dict[str, Any]] = []
    broken: List[Dict[str, Any]] = []
    day_meta: List[Dict[str, Any]] = []
    history: List[Tuple[str, List[Dict[str, Any]]]] = []
    stage_history: List[Dict[str, Any]] = []
    mains_history: List[List[str]] = []
    reason_memory: Dict[str, List[Tuple[str, str]]] = {}
    prev_metrics: Optional[Dict[str, Any]] = None
    prev_stage: Optional[str] = None
    prev_features: Optional[Dict[str, Any]] = None
    prev_main_themes: List[Dict[str, Any]] = []
    half = days[len(days) // 2] if days else ""

    for i, day in enumerate(days):
        p = pools[day]
        ladder = p.get("ladder") or {}
        zt = _universe(p.get("zt") or [])
        for row in zt:
            row["boards"] = int(ladder.get(row["code"], 1))
        zb, dt = _universe(p.get("zb") or []), _universe(p.get("dt") or [])
        zt_codes = {row["code"] for row in zt}

        previous = None
        yesterday_rows: List[Dict[str, Any]] = []
        yesterday_codes: set = set()
        if i > 0:
            y = pools[days[i - 1]]
            y_ladder = y.get("ladder") or {}
            yesterday_rows = _universe(y.get("zt") or [])
            yesterday_codes = {row["code"] for row in yesterday_rows}
            previous = []
            for row in yesterday_rows:
                chg = book.change(row["code"], day)
                if chg is not None:
                    previous.append({**row, "change_pct": chg, "y_boards": int(y_ladder.get(row["code"], 1))})

        recent_mains = [name for names in mains_history[-10:] for name in names]
        theme_result = build_themes(
            zt=zt, blocks=[], history=history[-10:], previous=previous, yesterday_rows=yesterday_rows,
            recent_mains=recent_mains, hot_rank={}, rules=rules,
        )
        themes = theme_result["themes"]
        metrics = cycle_mod.compute_metrics(zt=zt, zb=zb, dt=dt, previous=previous, rules=rules)
        metrics["follow_rate"] = theme_result["follow_rate"]
        cycle_mod.attach_deltas(metrics, prev_metrics)
        stage = cycle_mod.classify_stage(metrics, prev_stage=prev_stage, rules=rules)
        kobe = cycle_mod.kobe_phase(stage["key"], metrics, stage_history)
        roles = assign_roles(
            zt=zt, themes=themes, stock_themes=theme_result["stock_themes"],
            max_height=int(metrics.get("max_height") or 0), hot_rank={}, rules=rules,
        )
        theme_by_key = {t["key"]: t for t in themes}
        features = _day_features(stage, kobe, metrics)
        day_meta.append({"date": day, **features, "zt": metrics.get("zt_count"),
                         "tier1": cycle_mod.tier_rate(metrics, 1), "premium": metrics.get("yzt_premium")})
        split = "前半段" if day < half else "后半段"
        env_prev = {f"prev_{k}": v for k, v in (prev_features or {}).items()} if prev_features else {
            f"prev_{k}": "无数据" for k in features
        }
        prev_mains = prev_main_themes

        def _memory_link(code: str) -> str:
            """盘中打板时已知的题材信息：这只票过去 90 天的涨停原因是否命中昨日主线。"""
            tags = [t for _, reason in reason_memory.get(code, []) for t in split_reason(reason)]
            if not tags:
                return "近 90 天无涨停（无记忆）"
            hit = any(k in tag or tag in k for tag in tags for theme in prev_mains
                      for k in [theme["key"], *theme.get("tags", [])] if len(k) >= 2)
            return "历史涨停原因命中昨日主线" if hit else "有涨停记忆但不在昨日主线"

        # --- 一进二 + 首板触板（封住的那部分） ------------------------------------
        for row in zt:
            if int(row.get("boards") or 1) != 1:
                continue
            code = row["code"]
            info = roles.get(code) or {}
            theme = theme_by_key.get(info.get("theme") or "")
            common = _common(row, code, book, day)
            link = "主线" if info.get("theme_is_main") else ("非主线题材" if theme else "无题材")
            yizi = "一字" in str(row.get("limit_up_type") or "")

            o12 = one_into_two_outcome(book, code, days, i)
            if o12 is not None:
                one_into_two.append({
                    "date": day, "code": code, "name": row["name"], "split": split, **features, **common,
                    "first_kind": "近期首次涨停" if int(row.get("hd_boards") or 1) <= 1 else "N天1板（有涨停记忆）",
                    "theme_link": link,
                    "theme_rank": ("无题材" if not theme else (f"第{theme['rank']}名" if theme["rank"] <= 3 else "第4名以后")),
                    "novelty": theme["novelty_label"] if theme else "无题材",
                    "role": info.get("role_label") or "其他",
                    "theme_size": _band(theme["zt_count"] if theme else 0, (2, 4, 7, 1e9), ["孤立（1只）", "2-3只", "4-6只", "≥7只"]),
                    "board_type": row.get("limit_up_type") or "无数据",
                    "open_num": count_bucket(int(row.get("open_num") or 0)).replace("次", "次炸板"),
                    "turnover": turnover_bucket(row.get("turnover_rate")),
                    "seal": seal_bucket(row.get("seal_amount"), row.get("circ_mv")),
                    "suc_rate": _band(None if row.get("suc_rate") is None else row["suc_rate"] * 100,
                                      (50, 70, 85, 1e9), ["<50%", "50-70%", "70-85%", "≥85%"]),
                    "n1_gap_band": gap_bucket(o12["n1_gap"]),
                    **o12,
                })

            if code in yesterday_codes or yizi:
                continue  # 一字首板盘中买不到，不计入打板样本
            touch = first_touch_outcome(book, code, days, i, sealed=True)
            if touch is not None:
                touches.append({"date": day, "code": code, "split": split, "sealed": True, **env_prev, **common,
                                "theme_link_eod": link, "memory_link": _memory_link(code), **touch})

        # --- 炸板：首板触板（没封住的那部分）+ 首板预判候选 ------------------------
        for row in zb:
            code = row["code"]
            if code in zt_codes:
                continue
            memory_tags = [t for _, reason in reason_memory.get(code, []) for t in split_reason(reason)]
            link, _ = _theme_link(memory_tags, themes)
            common = _common(row, code, book, day)
            if code not in yesterday_codes:
                touch = first_touch_outcome(book, code, days, i, sealed=False)
                if touch is not None:
                    touches.append({"date": day, "code": code, "split": split, "sealed": False, **env_prev, **common,
                                    "theme_link_eod": link, "memory_link": _memory_link(code), **touch})
            outcome = broken_outcome(book, code, days, i)
            if outcome is not None:
                broken.append({
                    "date": day, "code": code, "name": row["name"], "split": split, **features, **common,
                    "theme_link": link if memory_tags else "近 90 天无涨停记录",
                    "was_limit_yesterday": "昨日涨停（断板）" if code in yesterday_codes else "昨日未涨停",
                    "close_change": _band(row.get("change_pct"), (0, 3, 6, 1e9), ["收跌", "0-3%", "3-6%", "≥6%"]),
                    "open_num": count_bucket(int(row.get("open_num") or 0)).replace("次", "次开板"),
                    "turnover": turnover_bucket(row.get("turnover_rate")),
                    **outcome,
                })

        for row in zt:
            reason_memory.setdefault(row["code"], []).append((day, row.get("reason") or ""))
            reason_memory[row["code"]] = [x for x in reason_memory[row["code"]] if x[0] >= _days_before(day, 90)]
        history.append((day, zt))
        stage_history.append({"stage": stage["key"], "yzt_premium": metrics.get("yzt_premium")})
        mains_history.append([t["name"] for t in themes if t.get("is_main")])
        prev_metrics, prev_stage, prev_features = metrics, stage["key"], features
        prev_main_themes = [t for t in themes if t.get("is_main")]
    return ReplayResult(days=days, touches=touches, one_into_two=one_into_two, broken=broken, day_meta=day_meta)


def _days_before(day: str, n: int) -> str:
    return (datetime.strptime(day, "%Y-%m-%d").date() - timedelta(days=n)).isoformat()


# ---------------------------------------------------------------------- 统计


def _summ(values: List[float]) -> Dict[str, Optional[float]]:
    """``t`` 是均值 / 标准误。|t| < 2 的分组差异基本可以当噪声看。"""
    if not values:
        return {"n": 0, "mean": None, "median": None, "win": None, "t": None}
    avg = mean(values)
    t = None
    if len(values) >= 2:
        sd = pstdev(values)
        t = round(avg / (sd / len(values) ** 0.5), 2) if sd else None
    return {
        "n": len(values),
        "mean": round(avg, 2),
        "median": round(median(values), 2),
        "win": round(sum(1 for v in values if v > 0) / len(values), 4),
        "t": t,
    }


def _rate(events: List[Dict[str, Any]], key: str) -> Optional[float]:
    return round(sum(1 for e in events if e.get(key)) / len(events), 4) if events else None


def _halves(events: List[Dict[str, Any]], key: str) -> Dict[str, Optional[float]]:
    return {
        part: _summ([e[key] for e in events if e.get("split") == part and e.get(key) is not None])["mean"]
        for part in ("前半段", "后半段")
    }


def summarize(events: List[Dict[str, Any]], kind: str) -> Dict[str, Any]:
    """``kind``：touch（首板打板）/ o12（一进二）/ broken（炸板次日）。"""
    if not events:
        return {"n": 0}
    out: Dict[str, Any] = {"n": len(events)}
    if kind == "touch":
        out["sealed"] = _rate(events, "sealed")
        out["open"] = _summ([e["s1_open"] for e in events])
        out["close"] = _summ([e["s1_close"] for e in events])
        out["halves"] = _halves(events, "s1_open")
    elif kind == "o12":
        buyable = [e for e in events if e["buyable"]]
        out["touch"] = _rate(events, "n1_touch")
        out["yizi"] = _rate(events, "n1_yizi")
        out["buyable"] = _rate(events, "buyable")
        out["sealed_given_buy"] = _rate(buyable, "n1_sealed")
        out["n1_gap"] = round(mean(e["n1_gap"] for e in events), 2)
        out["open"] = _summ([e["s2_open"] for e in buyable])
        out["close"] = _summ([e["s2_close"] for e in buyable])
        out["halves"] = _halves(buyable, "s2_open")
    else:
        out["touch"] = _rate(events, "n1_touch")
        out["sealed"] = _rate(events, "n1_sealed")
        out["open"] = _summ([e["s3_open"] for e in events])
        out["close"] = _summ([e["s3_close"] for e in events])
        out["halves"] = _halves(events, "s3_close")
    return out


def group_stats(events: List[Dict[str, Any]], kind: str, *keys: str, min_n: int = 1) -> List[Dict[str, Any]]:
    groups: Dict[Tuple[str, ...], List[Dict[str, Any]]] = {}
    for event in events:
        groups.setdefault(tuple(str(event.get(k)) for k in keys), []).append(event)
    rows = [
        {"bucket": " × ".join(bucket), **summarize(items, kind)}
        for bucket, items in groups.items()
        if len(items) >= min_n
    ]
    rows.sort(key=lambda r: r["bucket"])
    return rows


# theme_link_eod 用的是收盘后的题材聚类（盘中不可知），只作对照，不能拿来定盘中规则。
TOUCH_FEATURES = (
    "prev_stage", "prev_kobe", "prev_day_tier1", "prev_day_premium", "memory_link", "theme_link_eod",
    "first_time", "circ_mv", "price", "market", "limit_memory", "sealed",
)
O12_FEATURES = (
    "stage", "kobe", "day_zt", "day_tier1", "day_premium", "first_kind", "theme_link", "theme_rank", "novelty",
    "role", "theme_size", "board_type", "first_time", "open_num", "turnover", "circ_mv", "price", "seal",
    "suc_rate", "market", "limit_memory", "n1_gap_band",
)
O12_CROSSES = (
    ("stage", "theme_link"), ("day_tier1", "theme_link"), ("theme_link", "first_time"), ("theme_link", "n1_gap_band"),
    ("theme_link", "novelty"), ("theme_link", "turnover"), ("stage", "n1_gap_band"),
)
BROKEN_FEATURES = (
    "stage", "kobe", "day_tier1", "day_premium", "theme_link", "was_limit_yesterday", "close_change",
    "first_time", "open_num", "turnover", "circ_mv", "price", "market", "limit_memory",
)


def build_report(result: ReplayResult, *, min_n: int = 30) -> Dict[str, Any]:
    t, o, b = result.touches, result.one_into_two, result.broken
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "range": [result.days[0], result.days[-1]] if result.days else [],
        "trading_days": len(result.days),
        "touch": {
            "all": summarize(t, "touch"),
            "features": {k: group_stats(t, "touch", k) for k in TOUCH_FEATURES},
            "crosses": {
                "prev_kobe × memory_link": group_stats(t, "touch", "prev_kobe", "memory_link", min_n=min_n),
                "memory_link × first_time": group_stats(t, "touch", "memory_link", "first_time", min_n=min_n),
            },
        },
        "o12": {
            "all": summarize(o, "o12"),
            "features": {k: group_stats(o, "o12", k) for k in O12_FEATURES},
            "crosses": {" × ".join(pair): group_stats(o, "o12", *pair, min_n=min_n) for pair in O12_CROSSES},
        },
        "broken": {
            "all": summarize(b, "broken"),
            "features": {k: group_stats(b, "broken", k) for k in BROKEN_FEATURES},
            "crosses": {"theme_link × stage": group_stats(b, "broken", "theme_link", "stage", min_n=min_n)},
        },
        "days": result.day_meta,
    }


def _pct(v):
    return "—" if v is None else f"{v * 100:.1f}%"


def _num(v):
    return "—" if v is None else f"{v:+.2f}"


_HEADERS = {
    "touch": ("| 分组 | 样本 | 封住率 | 次日开盘卖 均值 | 胜率 | 次日收盘卖 均值 | 胜率 | 开盘卖 前/后半段 |",
              "|---|---|---|---|---|---|---|---|"),
    "o12": ("| 分组 | 样本 | 次日触板 | 次日一字 | 可买 | 买后封住 | 次日高开 | 开盘卖 均值 | 胜率 | 收盘卖 均值 | 胜率 | 开盘卖 前/后半段 |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|"),
    "broken": ("| 分组 | 样本 | 次日触板 | 次日涨停 | 次日开盘 均值 | 次日收盘 均值 | 胜率 | 收盘 前/后半段 |",
               "|---|---|---|---|---|---|---|---|"),
}


def _row(r: Dict[str, Any], kind: str) -> str:
    h = r.get("halves") or {}
    halves = f"{_num(h.get('前半段'))} / {_num(h.get('后半段'))}"
    if kind == "touch":
        return (f"| {r['bucket']} | {r['n']} | {_pct(r['sealed'])} | {_num(r['open']['mean'])} | {_pct(r['open']['win'])} | "
                f"{_num(r['close']['mean'])} | {_pct(r['close']['win'])} | {halves} |")
    if kind == "o12":
        return (f"| {r['bucket']} | {r['n']} | {_pct(r['touch'])} | {_pct(r['yizi'])} | {_pct(r['buyable'])} | "
                f"{_pct(r['sealed_given_buy'])} | {_num(r['n1_gap'])} | {_num(r['open']['mean'])} | {_pct(r['open']['win'])} | "
                f"{_num(r['close']['mean'])} | {_pct(r['close']['win'])} | {halves} |")
    return (f"| {r['bucket']} | {r['n']} | {_pct(r['touch'])} | {_pct(r['sealed'])} | {_num(r['open']['mean'])} | "
            f"{_num(r['close']['mean'])} | {_pct(r['close']['win'])} | {halves} |")


def render_markdown(report: Dict[str, Any]) -> str:
    lines = [
        "# 打板王首板回测",
        "",
        f"区间 {report['range'][0]} ~ {report['range'][1]}，{report['trading_days']} 个交易日。",
        "收益为理论成交（涨停价买入，未计手续费、滑点与排队买不到），单位 %。",
        "",
    ]
    sections = (
        ("touch", "一、首板打板（触板即买，含炸板）", "环境特征取前一交易日收盘，盘中决策时已知。"),
        ("o12", "二、一进二（首板次日触板买，一字买不到不计）", "特征取首板当天收盘；次日高开幅度在买入前已知。"),
        ("broken", "三、炸板股次日（首板预判候选）", "炸板当天收盘买入。"),
    )
    for kind, title, note in sections:
        block = report[kind]
        lines += [f"## {title}", "", note, ""]
        for name, rows in [("全部", [{"bucket": "全部", **block["all"]}]), *block["features"].items(),
                           *[(f"交叉：{k}", v) for k, v in block["crosses"].items()]]:
            rows = [r for r in rows if r.get("n")]
            if not rows:
                continue
            lines += [f"### {name}", "", *_HEADERS[kind]]
            lines += [_row(r, kind) for r in rows]
            lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------- 编排


def candidate_days(start: str, end: str) -> List[str]:
    from src.core.trading_calendar import is_market_open

    cur = datetime.strptime(start, "%Y-%m-%d").date()
    stop = datetime.strptime(end, "%Y-%m-%d").date()
    out = []
    while cur <= stop:
        if cur.weekday() < 5 and is_market_open("cn", cur):
            out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def _parallel(items: List[str], fn: Callable[[str], Any], *, workers: int, label: str) -> Dict[str, Any]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results: Dict[str, Any] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="daban-bt") as pool:
        futures = {pool.submit(fn, item): item for item in items}
        for future in as_completed(futures):
            item = futures[future]
            try:
                results[item] = future.result()
            except Exception as exc:  # 单只失败不影响整体，记日志继续
                logger.warning("回测：%s %s 失败：%s", label, item, exc)
                results[item] = None
            done += 1
            if done % 200 == 0:
                logger.info("回测：%s 进度 %d/%d", label, done, len(items))
    return results


def run_backtest(
    start: str,
    end: str,
    *,
    cache_dir: str = DEFAULT_CACHE_DIR,
    workers: int = 6,
    pause: float = 0.05,
) -> Dict[str, Any]:
    """拉数（带缓存）→ 回放 → 统计。返回报告 dict，同时写入 ``cache_dir/report``。"""
    cache = BacktestCache(cache_dir)

    def _pool(day: str):
        time.sleep(pause)
        return cache.pools(day, fetch_day_pools)

    pool_results = _parallel(candidate_days(start, end), _pool, workers=min(workers, 4), label="池子")
    pools = {day: p for day, p in pool_results.items() if p}
    if len(pools) < 5:
        raise RuntimeError(f"{start} ~ {end} 只拿到 {len(pools)} 个交易日的数据，无法回测")
    days = sorted(pools)

    codes = sorted({
        r["code"] for p in pools.values() for key in ("zt", "zb") for r in _universe(p.get(key) or [])
    })
    min_last = days[-1].replace("-", "")

    def _kline(code: str):
        time.sleep(pause)
        return cache.kline(code, fetch_kline_rows, min_last=min_last)

    logger.info("回测：%d 个交易日、%d 只股票的日 K", len(days), len(codes))
    klines = {code: rows for code, rows in _parallel(codes, _kline, workers=workers, label="日K").items() if rows}
    result = replay(pools, KlineBook(klines))
    report = build_report(result)
    report["kline_coverage"] = {"codes": len(codes), "with_kline": len(klines)}

    out_dir = os.path.join(cache_dir, "report")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=1)
    with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as handle:
        handle.write(render_markdown(report))
    with open(os.path.join(out_dir, "events.json"), "w", encoding="utf-8") as handle:
        json.dump({"touches": result.touches, "one_into_two": result.one_into_two, "broken": result.broken},
                  handle, ensure_ascii=False)
    return report


__all__ = [
    "BacktestCache", "KlineBook", "ReplayResult", "broken_outcome", "build_report", "candidate_days",
    "fetch_day_pools", "first_touch_outcome", "group_stats", "one_into_two_outcome", "render_markdown", "replay",
    "run_backtest", "summarize",
]
