# -*- coding: utf-8 -*-
"""情绪周期：五大指标的计算与六阶段判定（陈小群），附科比四阶段映射。

判定是**透明投票**：每个阶段的每个条件都输出 命中 / 未命中 / 数据缺失，页面逐条展示，
用户可以对着数字核对「为什么今天是这个阶段」。数据缺失的条件按未命中计（宁可保守）。
"""

from __future__ import annotations

from statistics import mean
from typing import Any, Dict, List, Optional

from src.services.daban.constants import (
    KOBE_FALL,
    KOBE_LABELS,
    STAGE_CONSERVATIVE_ORDER,
    STAGE_CYCLE,
    STAGE_ICE,
    STAGE_LABELS,
    STAGE_RETREAT,
    STAGE_TO_KOBE,
)


def _round(value: Optional[float], digits: int = 2) -> Optional[float]:
    return None if value is None else round(float(value), digits)


def compute_metrics(
    *,
    zt: List[Dict[str, Any]],
    zb: List[Dict[str, Any]],
    dt: List[Dict[str, Any]],
    previous: Optional[List[Dict[str, Any]]],
    rules: Dict[str, Any],
) -> Dict[str, Any]:
    """从当日池子算出周期指标。``previous`` 为昨日涨停股在今日的表现（缺失时为 None）。"""
    zt_count = len(zt)
    zb_count = len(zb)
    boards = sorted((int(row.get("boards") or 1) for row in zt), reverse=True)
    max_height = boards[0] if boards else 0
    second_height = next((b for b in boards if b < max_height), 0) if boards else 0
    leaders = [row for row in zt if int(row.get("boards") or 1) == max_height and max_height >= 2]

    seal_ratios = []
    for row in leaders:
        seal, circ = row.get("seal_amount"), row.get("circ_mv")
        if seal and circ:
            seal_ratios.append(float(seal) / float(circ))

    metrics: Dict[str, Any] = {
        "zt_count": zt_count,
        "zb_count": zb_count,
        "touched_count": zt_count + zb_count,
        "zab_rate": _round(zb_count / (zt_count + zb_count), 4) if (zt_count + zb_count) else None,
        "dt_count": len(dt),
        "lianban_count": sum(1 for b in boards if b >= 2),
        "max_height": max_height,
        "second_height": second_height,
        "leader_codes": [row["code"] for row in leaders],
        "leader_seal_ratio": _round(max(seal_ratios), 4) if seal_ratios else None,
        "first_board_count": sum(1 for b in boards if b == 1),
        "yesterday_zt_count": None,
        "promotion_rate": None,
        "tiers": [],
        "yzt_premium": None,
        "yzt_up_ratio": None,
        "big_loss_count": None,
        "follow_rate": None,
        "delta": {},
    }

    if previous is not None:
        today_boards = {row["code"]: int(row.get("boards") or 1) for row in zt}
        metrics["yesterday_zt_count"] = len(previous)
        if previous:
            metrics["promotion_rate"] = _round(metrics["lianban_count"] / len(previous), 4)
            changes = [float(r["change_pct"]) for r in previous if r.get("change_pct") is not None]
            if changes:
                metrics["yzt_premium"] = _round(mean(changes), 2)
                metrics["yzt_up_ratio"] = _round(sum(1 for c in changes if c > 0) / len(changes), 4)
                threshold = float(rules.get("big_loss_pct", -5.0))
                metrics["big_loss_count"] = sum(1 for c in changes if c <= threshold)
        metrics["tiers"] = _promotion_tiers(previous, today_boards)
    return metrics


def _promotion_tiers(previous: List[Dict[str, Any]], today_boards: Dict[str, int]) -> List[Dict[str, Any]]:
    """分层晋级率：昨日 k 板的股票今天是否成了 k+1 板。4 板及以上合并为「高位」。"""
    buckets: Dict[int, Dict[str, int]] = {}
    for row in previous:
        k = min(int(row.get("y_boards") or 1), 4)
        bucket = buckets.setdefault(k, {"base": 0, "promoted": 0})
        bucket["base"] += 1
        if today_boards.get(row["code"]) == int(row.get("y_boards") or 1) + 1:
            bucket["promoted"] += 1
    tiers = []
    for k in sorted(buckets):
        base, promoted = buckets[k]["base"], buckets[k]["promoted"]
        label = f"{k}进{k + 1}" if k < 4 else "4板+晋级"
        tiers.append({
            "from": k,
            "label": label,
            "base": base,
            "promoted": promoted,
            "rate": _round(promoted / base, 4) if base else None,
        })
    return tiers


def tier_rate(metrics: Optional[Dict[str, Any]], k: int) -> Optional[float]:
    for tier in (metrics or {}).get("tiers") or []:
        if tier.get("from") == k:
            return tier.get("rate")
    return None


def attach_deltas(metrics: Dict[str, Any], prev: Optional[Dict[str, Any]]) -> None:
    """相对上一交易日的变化。没有昨日指标时各项为 None（判定里按未命中计）。"""
    keys = ("zt_count", "dt_count", "max_height", "big_loss_count", "yzt_premium", "promotion_rate", "lianban_count")
    delta: Dict[str, Optional[float]] = {}
    for key in keys:
        today_value = metrics.get(key)
        prev_value = (prev or {}).get(key)
        delta[key] = (
            _round(float(today_value) - float(prev_value), 4)
            if today_value is not None and prev_value is not None
            else None
        )
    today_t1, prev_t1 = tier_rate(metrics, 1), tier_rate(prev, 1)
    delta["tier1_rate"] = _round(today_t1 - prev_t1, 4) if today_t1 is not None and prev_t1 is not None else None
    metrics["delta"] = delta


# ---------------------------------------------------------------------- 阶段判定


def _cond(key: str, label: str, value: Any, threshold: str, hit: Optional[bool]) -> Dict[str, Any]:
    return {"key": key, "label": label, "value": value, "threshold": threshold, "hit": hit}


def _lt(value, limit):
    return None if value is None else value < limit


def _le(value, limit):
    return None if value is None else value <= limit


def _gt(value, limit):
    return None if value is None else value > limit


def _ge(value, limit):
    return None if value is None else value >= limit


def _between(value, low, high):
    return None if value is None else (low <= value <= high)


def stage_conditions(metrics: Dict[str, Any], rules: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """六个阶段各自的条件清单。阈值全部取自 ``rules["stage"]``。"""
    s = rules["stage"]
    m = metrics
    d = metrics.get("delta") or {}
    zt, lb, dt = m.get("zt_count"), m.get("lianban_count"), m.get("dt_count")
    premium, height = m.get("yzt_premium"), m.get("max_height")
    ice, warm, fer, cli, ret = s["ice"], s["warm"], s["ferment"], s["climax"], s["retreat"]
    return {
        "ice": [
            _cond("zt", "涨停家数", zt, f"< {ice['zt_max']}", _lt(zt, ice["zt_max"])),
            _cond("lianban", "连板支数", lb, f"< {ice['lianban_max']}", _lt(lb, ice["lianban_max"])),
            _cond("premium", "昨日涨停今日均涨幅", premium, f"≤ {ice['premium_max']}%", _le(premium, ice["premium_max"])),
        ],
        "warm": [
            _cond("zt", "涨停家数", zt, f"{warm['zt_min']}–{warm['zt_max']}", _between(zt, warm["zt_min"], warm["zt_max"])),
            _cond("lianban", "连板支数", lb, f"{warm['lianban_min']}–{warm['lianban_max']}",
                  _between(lb, warm["lianban_min"], warm["lianban_max"])),
            _cond("promotion_delta", "晋级率较昨日", d.get("promotion_rate"), "上升", _gt(d.get("promotion_rate"), 0)),
        ],
        "ferment": [
            _cond("zt", "涨停家数", zt, f"{fer['zt_min']}–{fer['zt_max']}", _between(zt, fer["zt_min"], fer["zt_max"])),
            _cond("lianban", "连板支数", lb, f"{fer['lianban_min']}–{fer['lianban_max']}",
                  _between(lb, fer["lianban_min"], fer["lianban_max"])),
            _cond("follow_rate", "主线集中度", m.get("follow_rate"), f"> {fer['follow_rate_min']:.0%}",
                  _gt(m.get("follow_rate"), fer["follow_rate_min"])),
        ],
        "climax": [
            _cond("zt", "涨停家数", zt, f"> {cli['zt_min']}", _gt(zt, cli["zt_min"])),
            _cond("height", "连板高度", height, f"≥ {cli['height_min']} 板", _ge(height, cli["height_min"])),
            _cond("seal_ratio", "龙头封单/流通市值", m.get("leader_seal_ratio"), f"≥ {cli['seal_ratio_min']:.0%}",
                  _ge(m.get("leader_seal_ratio"), cli["seal_ratio_min"])),
        ],
        "diverge": [
            _cond("zt_delta", "涨停家数较昨日", d.get("zt_count"), "减少", _lt(d.get("zt_count"), 0)),
            _cond("height_delta", "连板高度较昨日", d.get("max_height"), "回落", _lt(d.get("max_height"), 0)),
            _cond("big_loss_delta", "大面股较昨日", d.get("big_loss_count"), "增多", _gt(d.get("big_loss_count"), 0)),
        ],
        "retreat": [
            _cond("dt", "跌停家数", dt, f"> {ret['dt_min']}", _gt(dt, ret["dt_min"])),
            _cond("lianban", "连板支数", lb, f"< {ret['lianban_max']}", _lt(lb, ret["lianban_max"])),
            _cond("premium", "昨日涨停今日均涨幅", premium, f"≤ {ret['premium_max']}%", _le(premium, ret["premium_max"])),
        ],
    }


def _cycle_distance(a: str, b: str) -> int:
    ia, ib = STAGE_CYCLE.index(a), STAGE_CYCLE.index(b)
    diff = abs(ia - ib)
    return min(diff, len(STAGE_CYCLE) - diff)


def _band_fallback(zt_count: int) -> str:
    """所有阶段一条都没命中时，只按涨停家数分档（置信度标低）。"""
    if zt_count < 30:
        return "ice"
    if zt_count <= 50:
        return "warm"
    if zt_count <= 80:
        return "ferment"
    return "climax"


def classify_stage(
    metrics: Dict[str, Any], *, prev_stage: Optional[str], rules: Dict[str, Any]
) -> Dict[str, Any]:
    """六阶段判定：命中率最高者胜；平局取与昨日阶段相邻者，无昨日时取更保守的阶段。"""
    conditions = stage_conditions(metrics, rules)
    table = []
    for key in STAGE_CYCLE:
        items = conditions[key]
        hits = sum(1 for item in items if item["hit"] is True)
        table.append({"stage": key, "label": STAGE_LABELS[key], "hit_rate": round(hits / len(items), 4), "items": items})

    def _rank(entry: Dict[str, Any]):
        if prev_stage in STAGE_CYCLE:
            tie = _cycle_distance(entry["stage"], prev_stage)
        else:
            tie = STAGE_CONSERVATIVE_ORDER.index(entry["stage"])
        return (-entry["hit_rate"], tie, STAGE_CONSERVATIVE_ORDER.index(entry["stage"]))

    ranked = sorted(table, key=_rank)
    best, second = ranked[0], ranked[1]
    override = None
    fallback = False
    key = best["stage"]
    if (metrics.get("dt_count") or 0) > float(rules["stage"]["retreat_override_dt"]):
        override = f"跌停 {metrics.get('dt_count')} 家 > {rules['stage']['retreat_override_dt']}，直接判退潮（陈小群：>20 家明确退潮）"
        key = STAGE_RETREAT
    elif best["hit_rate"] == 0:
        fallback = True
        key = _band_fallback(int(metrics.get("zt_count") or 0))

    hit_rate = next(entry["hit_rate"] for entry in table if entry["stage"] == key)
    transitional = override is None and not fallback and hit_rate < float(rules["stage"]["transitional_below"])
    secondary = second["stage"] if transitional and second["hit_rate"] > 0 else None
    return {
        "key": key,
        "label": STAGE_LABELS[key],
        "hit_rate": hit_rate,
        "transitional": bool(transitional and secondary),
        "secondary": secondary,
        "secondary_label": STAGE_LABELS.get(secondary or "", ""),
        "override": override,
        "fallback": fallback,
        "conditions": table,
    }


def kobe_phase(stage_key: str, metrics: Dict[str, Any], history: List[Dict[str, Any]]) -> Dict[str, Any]:
    """科比四阶段。主跌按「连续杀跌第 N 天」细分（科比：第二天尾盘找买点，第三天必修复）。

    杀跌日 = 阶段为退潮或冰点，且昨日涨停今日均涨幅为负（缺失时只看阶段）。
    ``history`` 为此前交易日的快照摘要，按时间正序。
    """

    def _is_fall(stage: Optional[str], premium: Optional[float]) -> bool:
        return stage in (STAGE_RETREAT, STAGE_ICE) and (premium is None or premium < 0)

    streak = 0
    if _is_fall(stage_key, metrics.get("yzt_premium")):
        streak = 1
        for item in reversed(history or []):
            if _is_fall(item.get("stage"), item.get("yzt_premium")):
                streak += 1
            else:
                break

    if streak >= 1:
        day = min(streak, 3)
        return {
            "key": KOBE_FALL,
            "label": f"{KOBE_LABELS[KOBE_FALL]}第 {streak} 天",
            "fall_day": streak,
            "gate_key": f"fall_{day}",
        }
    key = STAGE_TO_KOBE.get(stage_key, "low_shock")
    if key == KOBE_FALL:
        # 退潮但昨日涨停溢价为正：杀跌已在缓和，按高位震荡对待。
        key = "high_shock"
    return {"key": key, "label": KOBE_LABELS[key], "fall_day": 0, "gate_key": key}
