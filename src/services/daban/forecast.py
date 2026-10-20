# -*- coding: utf-8 -*-
"""首板预判（只做向前验证，不在页面上推荐）。

科比的原话是「先于市场发现可发酵的舆论事件」（#9）和「根据新闻去挖掘一些票」（#38），
本质是靠新闻预判，而项目里没有可回溯的历史新闻语料，所以**无法回测**。回测试过的替代口径
（从当天炸板股里挑「明天可能首板」）一年 6229 例，次日涨停 7.7%、收盘卖均值 -0.54%，
几乎所有分组为负，因此不做推荐。

这里做的是「记录 + 向前验证」：每个交易日收盘后按发酵中的主线题材挑出候选，记下日期与来源，
下一交易日收盘回填是否触板 / 封板。攒够样本再决定它值不值得放进页面。
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.services.daban.themes import split_reason

ACTIVE_STATUS = ("start", "rise")


def _related(a: set, b: set) -> bool:
    return any(x in y or y in x for x in a for y in b if len(x) >= 2 and len(y) >= 2)


def active_themes(themes: List[Dict[str, Any]], rules: Dict[str, Any]) -> List[Dict[str, Any]]:
    """发酵中的主线：主线、仍在启动 / 主升、持续不超过 15 天、涨停数达标。"""
    min_zt = int(rules.get("main_theme_min_zt", 3))
    picked = [
        t for t in themes
        if t.get("is_main") and t.get("status") in ACTIVE_STATUS
        and int(t.get("days") or 0) <= 15 and int(t.get("zt_count") or 0) >= min_zt
    ]
    picked.sort(key=lambda t: (t.get("rank") or 99))
    return picked


def build_forecasts(
    *,
    themes: List[Dict[str, Any]],
    zt: List[Dict[str, Any]],
    broken: List[Dict[str, Any]],
    reason_memory: Dict[str, set],
    limit_memory: Dict[str, int],
    rules: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """挑出次日首板候选。来源两类，都带 ``source`` 便于回填后分开统计：

    - ``broken``：今天炸板、且涨停原因属于该题材（今天已经走到过涨停，最有可能是下一个）；
    - ``memory``：近 60 天在该题材里涨停过 ≥2 次、今天没有涨停（与科比「持续性强、反复被资金选中」一致）。
    """
    per_theme = int(rules.get("forecast_per_theme", 8))
    cap = int(rules.get("forecast_max", 30))
    limit_codes = {r["code"] for r in zt}
    out: List[Dict[str, Any]] = []
    seen = set()

    for theme in active_themes(themes, rules):
        tags = set(theme.get("tags") or []) | {theme["key"]}
        picks: List[Dict[str, Any]] = []
        for row in broken:
            code = row["code"]
            if code in limit_codes or code in seen:
                continue
            own = set(split_reason(row.get("reason") or "")) or reason_memory.get(code) or set()
            if own and _related(tags, own):
                picks.append({"code": code, "name": row.get("name") or "", "theme": theme["name"], "source": "broken"})
        for code, mem_tags in reason_memory.items():
            if code in limit_codes or code in seen or len(picks) >= per_theme * 2:
                continue
            if (limit_memory.get(code) or 0) < 2:
                continue
            if not _related(tags, mem_tags):
                continue
            picks.append({"code": code, "name": "", "theme": theme["name"], "source": "memory"})
        for pick in picks[:per_theme]:
            if pick["code"] in seen:
                continue
            seen.add(pick["code"])
            out.append(pick)
            if len(out) >= cap:
                return out
    return out[:cap]


def summarize_forecasts(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """按来源统计命中率（触板率 / 封板率），给「值不值得上线」的判断依据。"""
    groups: Dict[str, Dict[str, int]] = {}
    for row in rows:
        if row.get("touched") is None:
            continue
        bucket = groups.setdefault(row.get("source") or "unknown", {"n": 0, "touched": 0, "sealed": 0})
        bucket["n"] += 1
        bucket["touched"] += 1 if row.get("touched") else 0
        bucket["sealed"] += 1 if row.get("sealed") else 0
    out = {
        key: {
            "n": v["n"],
            "touch_rate": round(v["touched"] / v["n"], 4) if v["n"] else None,
            "seal_rate": round(v["sealed"] / v["n"], 4) if v["n"] else None,
        }
        for key, v in groups.items()
    }
    days = len({row.get("target_date") for row in rows if row.get("evaluated")})
    return {"groups": out, "days": days, "total": sum(v["n"] for v in groups.values())}


__all__ = ["active_themes", "build_forecasts", "summarize_forecasts"]
