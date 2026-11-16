# -*- coding: utf-8 -*-
"""推荐评分的特征与计分：盘中雷达和回测共用这一份代码，两边的口径不会慢慢分叉。

口径来自评分体系 v2（2026-11-08 定稿）：模拟「盘中拉升到距涨停 6% 时按现价买入」，买入时只用那一刻已知的信息
——昨天收盘以前的日线、昨天的池子与题材、近 90 天的涨停原因，加上今天的开盘价。计分表按 H1 收益标定
（每 0.1 个百分点 1 分，0 分 = 同批候选的平均水平），见 ``constants.DEFAULT_RULES["rec_score"]``。

日线用「列表字典」表示（``{"t": [...], "o": [...], "h": [...], "l": [...], "c": [...], "v": [...], "ix": {t: i}}``），
所有函数只读下标 ``< idx`` 的 K 线：回测里 ``idx`` 是买入日 D 的下标，盘中 K 线只到昨天、``idx = len``。
"""

from __future__ import annotations

import math
from statistics import mean
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.services.daban.kline import limit_pct
from src.services.daban.themes import build_themes, split_reason

TOL = 0.3  # 涨停判定允许的涨幅误差（百分点），与回测一致

DIM_OPEN = "昨日状态×今日开盘"
DIM_MODE = "触发方式"
DIM_FOLLOW = "跟风程度×题材状态"
DIM_THEME = "题材状态×新老"
DIM_PRICE = "涨跌幅制度×股价"
DIM_MA = "均线"
DIM_VOLUME = "昨日量比"
DIM_R20 = "近20日涨幅"
DIMS = (DIM_OPEN, DIM_MODE, DIM_FOLLOW, DIM_THEME, DIM_PRICE, DIM_MA, DIM_VOLUME, DIM_R20)

QUALITY_WEAK = "弱板（开过板/尾盘封）"
QUALITY_HARD = "硬板"
OPEN_LABELS = ["低开", "高开 0-2%", "高开 2-5%", "高开 ≥5%（未涨停）"]
MODE_AT_OPEN = "开盘即达到触发价"
MODE_INTRADAY = "盘中被买上去"


# ---------------------------------------------------------------------- 基础


def pct(a: Optional[float], b: Optional[float]) -> Optional[float]:
    return (float(a) / float(b) - 1) * 100 if (a is not None and b) else None


def band(value: Optional[float], edges: Iterable[float], labels: List[str]) -> str:
    if value is None:
        return "无数据"
    for edge, label in zip(edges, labels):
        if value < edge:
            return label
    return labels[-1]


def make_bars(rows: Iterable[Dict[str, Any]], *, before: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """日 K 行（``time`` 为 YYYYMMDD 或 YYYY-MM-DD）→ 列表字典。``before``（YYYYMMDD，不含）截掉当天及以后。"""
    out: Dict[str, Any] = {"t": [], "o": [], "h": [], "l": [], "c": [], "v": []}
    cutoff = str(before or "").replace("-", "")
    for row in rows or []:
        t = str(row.get("time") or "").replace("-", "")[:8]
        if not t or (cutoff and t >= cutoff):
            continue
        try:
            o, h, lo, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (o and h and c):
            continue
        out["t"].append(t)
        out["o"].append(o)
        out["h"].append(h)
        out["l"].append(lo)
        out["c"].append(c)
        out["v"].append(float(row.get("volume") or 0))
    if not out["t"]:
        return None
    out["ix"] = {t: i for i, t in enumerate(out["t"])}
    return out


def limit_close(k: Dict[str, Any], j: int, lp: float) -> bool:
    return j >= 1 and (pct(k["c"][j], k["c"][j - 1]) or 0) >= lp - TOL


def _corr(xs: List[float], ys: List[float]) -> Optional[float]:
    n = len(xs)
    if n < 15:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if not sx or not sy:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


# ---------------------------------------------------------------------- 昨日上下文


def yesterday_context(
    *,
    zt_rows: List[Dict[str, Any]],
    zb_rows: List[Dict[str, Any]],
    history: List[Tuple[str, List[Dict[str, Any]]]],
    yesterday_rows: List[Dict[str, Any]],
    previous: Optional[List[Dict[str, Any]]],
    recent_mains: List[str],
    rules: Dict[str, Any],
) -> Dict[str, Any]:
    """D-1 的池子与题材（口径同回测：不拉题材榜，持续天数按本地历史推算）。

    - ``zt_rows`` / ``zb_rows``：D-1 的涨停 / 炸板池，涨停行要带 ``boards``（连板数）、``open_num``、``last_time``；
    - ``history``：D-1 之前 10 个交易日的涨停池 ``[(date, rows)]``，算题材持续天数；
    - ``yesterday_rows`` / ``previous``：D-2 的涨停池、D-2 涨停股在 D-1 的表现，算题材内溢价；
    - ``recent_mains``：D-1 之前 10 个交易日的主线题材名，判新老题材（字面口径，与标定时一致）。
    """
    zt = [dict(r) for r in zt_rows or []]
    result = build_themes(
        zt=zt, blocks=[], history=list(history or [])[-10:], previous=previous,
        yesterday_rows=list(yesterday_rows or []), recent_mains=list(recent_mains or []), hot_rank={}, rules=rules,
    )
    return {
        "zt": {str(r.get("code") or ""): r for r in zt if r.get("code")},
        "zb": {str(r.get("code") or "") for r in zb_rows or [] if r.get("code")},
        "themes": result["themes"],
    }


def theme_link(tags: Iterable[str], themes: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """历史涨停原因落在昨天哪个题材里（按题材排名先到先得，与回测 ``_theme_link`` 一致）。"""
    tags = [t for t in tags if t]
    for theme in themes or []:
        keys = [theme["key"], *theme.get("tags", [])]
        if any(k in tag or tag in k for tag in tags for k in keys if len(k) >= 2):
            return theme
    return None


def memory_tags(reasons: Iterable[str]) -> List[str]:
    return [tag for reason in reasons or [] for tag in split_reason(reason or "")]


# ---------------------------------------------------------------------- 个股特征


def leader_corr_band(k: Dict[str, Any], idx: int, leader: Optional[Dict[str, Any]]) -> Optional[str]:
    """近 40 个交易日与题材龙头的日收益相关系数（两边要是同一对相邻交易日才算一对）。"""
    if not leader or idx < 2:
        return None
    xs: List[float] = []
    ys: List[float] = []
    for j in range(max(1, idx - 40), idx):
        jb = leader["ix"].get(k["t"][j])
        if jb and jb >= 1 and leader["t"][jb - 1] == k["t"][j - 1]:
            xs.append(pct(k["c"][j], k["c"][j - 1]) or 0.0)
            ys.append(pct(leader["c"][jb], leader["c"][jb - 1]) or 0.0)
    value = _corr(xs, ys)
    return None if value is None else band(value, (0.3, 0.5, 0.7, 1e9), ["<0.3", "0.3-0.5", "0.5-0.7", "≥0.7"])


def base_features(
    *,
    code: str,
    k: Dict[str, Any],
    idx: int,
    ctx: Dict[str, Any],
    tags: Iterable[str],
    leader_bars: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """开盘前就能算好的部分（不含今天的开盘幅度和触发方式）。``idx`` 是买入日的下标，只读它之前的 K 线。"""
    lp = limit_pct(code)
    c0 = k["c"][idx - 1]
    out: Dict[str, Any] = {"code": code, "lp": lp, "c0": c0}

    zrow = (ctx.get("zt") or {}).get(code)
    if zrow:
        boards = int(zrow.get("boards") or 1)
        out["y_state"] = "昨日涨停·" + ("首板" if boards == 1 else ("2板" if boards == 2 else "≥3板"))
        weak = int(zrow.get("open_num") or 0) > 0 or str(zrow.get("last_time") or "") >= "143000"
        out["y_quality"] = QUALITY_WEAK if weak else QUALITY_HARD
        out["y_boards"] = boards
    elif code in (ctx.get("zb") or set()):
        out["y_state"], out["y_quality"], out["y_boards"] = "昨日炸板", "", 0
    else:
        out["y_state"], out["y_quality"], out["y_boards"] = "昨日未触板", "", 0

    vols = k["v"][max(0, idx - 6):idx - 1]
    ratio = (k["v"][idx - 1] / mean(vols)) if (vols and mean(vols) and k["v"][idx - 1]) else None
    out["volume_ratio"] = round(ratio, 3) if ratio is not None else None
    out[DIM_VOLUME] = band(ratio, (0.8, 1.5, 3, 1e9), ["缩量 <0.8", "0.8-1.5", "1.5-3", "≥3 倍"])

    closes = k["c"][max(0, idx - 60):idx]
    if len(closes) >= 20:
        above = sum(1 for m in (mean(closes[-5:]), mean(closes[-10:]), mean(closes[-20:])) if c0 >= m)
        out[DIM_MA] = {3: "全部站上", 0: "全部跌破"}.get(above, "部分站上")
    else:
        out[DIM_MA] = "无数据"
    out[DIM_R20] = (band(pct(c0, k["c"][idx - 21]), (-10, 0, 10, 30, 1e9),
                         ["跌超10%", "-10~0%", "0~10%", "10~30%", "≥30%"]) if idx >= 21 else "无数据")
    out["price_band"] = band(c0, (5, 10, 20, 40, 1e9), ["<5元", "5-10元", "10-20元", "20-40元", "≥40元"])
    out["regime"] = "20cm" if lp == 20 else "10cm"
    out[DIM_PRICE] = f"{out['regime']}｜{out['price_band']}"

    tags = list(tags or [])
    theme = theme_link(tags, ctx.get("themes") or []) if tags else None
    out["has_memory"] = bool(tags)
    if theme:
        leader = (theme.get("leader") or {}).get("code")
        status = theme.get("status_label") or "无数据"
        novelty = theme.get("novelty_label") or "无数据"
        if leader == code:
            corr = "自己就是龙头"
        else:
            corr = leader_corr_band(k, idx, leader_bars) or "无数据"
        out["theme"] = {"key": theme["key"], "name": theme.get("name") or theme["key"], "status_label": status,
                        "novelty_label": novelty, "days": theme.get("days"), "rank": theme.get("rank"),
                        "leader_code": leader, "leader_name": (theme.get("leader") or {}).get("name") or ""}
        out[DIM_FOLLOW] = f"{corr}｜{status}"
        out[DIM_THEME] = f"{status}｜{novelty}"
        out["corr_band"] = corr
    else:
        out["theme"] = None
        out[DIM_FOLLOW] = "无题材｜无题材"
        out[DIM_THEME] = "无题材｜无题材"
        out["corr_band"] = None
    out["temper"] = temper(k, idx, lp)
    return out


def open_band(today_open: Optional[float], ref_close: Optional[float], lp: float) -> str:
    gap = pct(today_open, ref_close)
    return band(None if gap is None else gap / lp * 10, (0, 2, 5, 1e9), OPEN_LABELS)


def full_cells(base: Dict[str, Any], *, today_open: Optional[float], ref_close: Optional[float],
               limit_up: Optional[float] = None, gap_max: float = 6.0) -> Dict[str, str]:
    """``base_features`` + 今天的开盘 → 8 个维度的格子（键与回测标定一致）。今天还没开盘时返回空。"""
    if not today_open or not ref_close:
        return {}
    lp = base["lp"]
    limit = float(limit_up) if limit_up else float(ref_close) * (1 + lp / 100)
    trigger = limit / (1 + gap_max / 100)
    return {
        DIM_OPEN: f"{base['y_state']}｜{base['y_quality']}｜{open_band(today_open, ref_close, lp)}",
        DIM_MODE: MODE_AT_OPEN if today_open >= trigger else MODE_INTRADAY,
        DIM_FOLLOW: base[DIM_FOLLOW],
        DIM_THEME: base[DIM_THEME],
        DIM_PRICE: base[DIM_PRICE],
        DIM_MA: base[DIM_MA],
        DIM_VOLUME: base[DIM_VOLUME],
        DIM_R20: base[DIM_R20],
    }


# ---------------------------------------------------------------------- 股性


def temper(k: Dict[str, Any], idx: int, lp: float, *, gap: float = 6.0, lookback: int = 250) -> Dict[str, Any]:
    """股性：只用买入日之前 ``lookback`` 根 K 线（``j + 1 < idx``，次日结果不前视）。

    - 冲板封住率：盘中最高价到过距涨停 ``gap``% 以内（且不是开盘即涨停）的日子里，收盘封住的比例；
    - 没封住的那些次日按 H1 的没封住口径（高开开盘卖、低开收盘卖）平均多少；
    - 连板成功率（科比 #29）：历史每次涨停后，次日继续涨停的比例；
    - 是否在回测覆盖范围：近一年摸到过涨停价（回测样本只含进过涨停 / 炸板池的票）。
    """
    approaches = sealed = limits = cont = 0
    touched = False
    unsealed_next: List[float] = []
    for j in range(max(1, idx - lookback), idx - 1):
        c_prev = k["c"][j - 1]
        lim = c_prev * (1 + lp / 100)
        trig = lim / (1 + gap / 100)
        if (pct(k["h"][j], c_prev) or 0) >= lp - TOL:
            touched = True
        if limit_close(k, j, lp):
            limits += 1
            cont += int(limit_close(k, j + 1, lp))
        if (pct(k["o"][j], c_prev) or 0) >= lp - TOL or k["h"][j] < trig:
            continue
        approaches += 1
        is_sealed = limit_close(k, j, lp)
        sealed += int(is_sealed)
        if not is_sealed:
            cost = k["o"][j] if k["o"][j] >= trig else trig
            n = j + 1
            exit_px = k["o"][n] if k["o"][n] >= cost else k["c"][n]
            unsealed_next.append(pct(exit_px, cost) or 0.0)
    rate = sealed / approaches if approaches >= 3 else None
    if rate is None:
        tag = "unknown"
    elif rate >= 0.35:
        tag = "active"
    elif rate >= 0.10:
        tag = "normal"
    else:
        tag = "inactive"
    return {
        "tag": tag,
        "approaches": approaches,
        "sealed": sealed,
        "seal_rate": round(rate, 4) if rate is not None else None,
        "unsealed_next_avg": round(mean(unsealed_next), 2) if unsealed_next else None,
        "limits": limits,
        "cont": cont,
        "cont_rate": round(cont / limits, 4) if limits >= 2 else None,
        "covered": touched,
    }


TEMPER_LABELS = {"active": "活跃", "normal": "一般", "inactive": "不活跃", "unknown": "冲板不足 3 次"}


def temper_text(info: Dict[str, Any]) -> str:
    if not info:
        return ""
    parts = [f"近一年冲板 {info.get('approaches', 0)} 次、封住 {info.get('sealed', 0)} 次"]
    if info.get("unsealed_next_avg") is not None:
        parts.append(f"没封住的次日平均 {info['unsealed_next_avg']:+.1f}%")
    if info.get("cont_rate") is not None:
        parts.append(f"涨停 {info.get('limits', 0)} 次、次日连板 {info.get('cont', 0)} 次")
    return "；".join(parts)


# ---------------------------------------------------------------------- 成绩（H1 / 次日开盘卖 / 次日收盘卖）

H1_RULE_SEALED = "买入当天封住：按科比 #25，不涨停当天收盘卖，涨停继续拿（最多拿到第 5 天）"
H1_RULE_UNSEALED_OPEN = "买入当天没封住，次日高开：开盘卖"
H1_RULE_UNSEALED_CLOSE = "买入当天没封住，次日低开：等到收盘卖"


def yizi_down(k: Dict[str, Any], j: int, lp: float) -> bool:
    return k["h"][j] == k["l"][j] and (pct(k["c"][j], k["c"][j - 1]) or 0) <= -(lp - TOL)


def _sell(k: Dict[str, Any], j: int, field: str, lp: float, last: int, *, live: bool) -> Optional[Tuple[float, int]]:
    """在第 j 根按开盘 / 收盘卖；一字跌停卖不出去，顺延到下一根开盘。

    回测（``live=False``）与研究脚本一致：顺延到 ``last`` 为止。盘中（``live=True``）``last`` 是已经收盘的最后一根，
    还没走到卖点、或卖点那根是一字跌停而下一根还没出来时返回 None（持有中）。
    """
    if j > last:
        return None
    while j < last and yizi_down(k, j, lp):
        j, field = j + 1, "o"
    if live and yizi_down(k, j, lp):
        return None
    return k[field][j], j


def exit_returns(k: Dict[str, Any], i0: int, cost: float, lp: float, *, last: Optional[int] = None,
                 live: bool = False, hold_days: int = 5, sealed: Optional[bool] = None) -> Dict[str, Any]:
    """买入日下标 ``i0``、成本 ``cost`` → H1、次日开盘卖（F1）、次日收盘卖（F2）。收益单位：%。

    H1：买入当天收盘封住的，按科比 #25 不涨停当天收盘卖、涨停继续拿（最多到第 ``hold_days`` 天）；没封住的，次日
    竞价挂成本价卖单——高开在开盘卖，低开等到收盘卖。一字跌停卖不出去时顺延到下一个交易日开盘。
    ``last`` 默认是最后一根 K 线；盘中传「已经收盘的最后一根」，没走完的口径记为持有中（值为 None）。
    ``sealed`` 给定时以它为准（盘中用收盘涨停池的结论，比日 K 按涨幅推算更准）。
    """
    last = len(k["t"]) - 1 if last is None else last
    out: Dict[str, Any] = {"sealed": None, "h1": None, "h1_exit": None, "h1_rule": None, "f1": None, "f2": None,
                           "status": "pending"}
    if i0 > last:
        return out
    sealed = limit_close(k, i0, lp) if sealed is None else bool(sealed)
    out["sealed"] = sealed
    f1 = _sell(k, i0 + 1, "o", lp, last, live=live)
    f2 = _sell(k, i0 + 1, "c", lp, last, live=live)
    out["f1"] = round(pct(f1[0], cost), 3) if f1 else None
    out["f2"] = round(pct(f2[0], cost), 3) if f2 else None
    if sealed:
        j = i0 + 1
        while j < i0 + hold_days and j <= last and limit_close(k, j, lp):
            j += 1
        sold = _sell(k, j, "c", lp, last, live=live) if j <= last else None
        rule = H1_RULE_SEALED
    elif i0 + 1 <= last:
        gap_up = k["o"][i0 + 1] >= cost
        sold = _sell(k, i0 + 1, "o" if gap_up else "c", lp, last, live=live)
        rule = H1_RULE_UNSEALED_OPEN if gap_up else H1_RULE_UNSEALED_CLOSE
    else:
        sold, rule = None, None
    if sold:
        out.update({"h1": round(pct(sold[0], cost), 3), "h1_exit": k["t"][sold[1]], "h1_rule": rule, "status": "done"})
    else:
        out["status"] = "holding" if i0 + 1 <= last or sealed else "pending"
        out["h1_rule"] = rule
    return out


# ---------------------------------------------------------------------- 计分


def score_cells(cells: Dict[str, str], table: Dict[str, Any]) -> Dict[str, Any]:
    """按计分表算总分。返回 ``{total, items: [命中的计分项], observe: 命中的观察期情形或 None}``。"""
    items: List[Dict[str, Any]] = []
    for item in table.get("items") or []:
        if cells.get(item.get("dim")) == item.get("cell"):
            items.append(dict(item))
    observe = None
    for item in table.get("observe") or []:
        if cells.get(item.get("dim")) == item.get("cell"):
            observe = dict(item)
            break
    return {"total": int(sum(int(i.get("points") or 0) for i in items)), "items": items, "observe": observe}


def score_range(table: Dict[str, Any]) -> Tuple[int, int]:
    """理论分数范围：每个维度最多命中一格。"""
    lo = hi = 0
    by_dim: Dict[str, List[int]] = {}
    for item in table.get("items") or []:
        by_dim.setdefault(item.get("dim"), []).append(int(item.get("points") or 0))
    for values in by_dim.values():
        lo += min([0, *values])
        hi += max([0, *values])
    return lo, hi


__all__ = [
    "DIMS", "DIM_FOLLOW", "DIM_MA", "DIM_MODE", "DIM_OPEN", "DIM_PRICE", "DIM_R20", "DIM_THEME", "DIM_VOLUME",
    "H1_RULE_SEALED", "H1_RULE_UNSEALED_CLOSE", "H1_RULE_UNSEALED_OPEN",
    "MODE_AT_OPEN", "MODE_INTRADAY", "OPEN_LABELS", "QUALITY_HARD", "QUALITY_WEAK", "TEMPER_LABELS", "TOL",
    "band", "base_features", "exit_returns", "full_cells", "leader_corr_band", "limit_close", "make_bars",
    "memory_tags", "open_band", "pct", "score_cells", "score_range", "temper", "temper_text", "theme_link",
    "yesterday_context", "yizi_down",
]
