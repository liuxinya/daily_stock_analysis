# -*- coding: utf-8 -*-
"""个股质量分（0–100，「打板王指数」），两条打法线共用。

``总分 = A–G 七维规则分（满分 100）+ 模型调整（±10）− 风险扣分（0～20）``，截断到 0–100。
周期适配**不进分数**：同一只票在两条线里分数相同，能不能做、做多大由各派门控矩阵决定。

每个维度都输出「得分 / 满分 / 一句理由 / 出处」，页面逐条展示。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.services.daban.constants import (
    MODE_LEADER,
    MODE_WEAK_TO_STRONG,
    ROLE_CATCH_UP,
    ROLE_FRONT,
    ROLE_THEME_LEADER,
    ROLE_TOTAL_LEADER,
)

DIMENSIONS = (
    ("A", "题材地位", 25),
    ("B", "个股地位", 20),
    ("C", "位置", 15),
    ("D", "板质量与形态", 15),
    ("E", "股性", 10),
    ("F", "人气与流动性", 10),
    ("G", "低价小盘", 5),
)


def _dim(key: str, score: float, reason: str, *ids: str) -> Dict[str, Any]:
    label, full = next((lbl, full) for k, lbl, full in DIMENSIONS if k == key)
    return {
        "key": key,
        "label": label,
        "score": round(max(0.0, min(float(full), score)), 1),
        "max": full,
        "reason": reason,
        "principle_ids": list(ids),
    }


def _score_theme(cand: Dict[str, Any], theme: Optional[Dict[str, Any]], rules: Dict[str, Any]) -> Dict[str, Any]:
    if not theme:
        return _dim("A", 0, "不属于任何涨停题材", "C13")
    rank_score = {1: 15, 2: 10, 3: 6}.get(theme["rank"], 0) if theme.get("is_main") else 0
    life = {"start": 10, "rise": 10, "diverge": 4, "fade": 0}.get(theme.get("status"), 0)
    days = int(theme.get("days") or 0)
    old = int(rules.get("theme_old_days", 30))
    if days > old:
        life = max(0, life - 4)
    where = f"主线第 {theme['rank']} 名" if theme.get("is_main") else f"非主线（第 {theme['rank']} 名）"
    return _dim(
        "A", rank_score + life,
        f"「{theme['name']}」{where}，{theme['status_label']}，持续第 {days} 天",
        "C13", "K13",
    )


def _score_role(cand: Dict[str, Any]) -> Dict[str, Any]:
    role = cand.get("role")
    score = {ROLE_TOTAL_LEADER: 20, ROLE_THEME_LEADER: 16, ROLE_FRONT: 10, ROLE_CATCH_UP: 8}.get(role, 4)
    return _dim("B", score, cand.get("role_label") or "其他", "C13", "K33")


def _score_position(cand: Dict[str, Any]) -> Dict[str, Any]:
    boards = int(cand.get("boards") or 0)
    role = cand.get("role")
    if role in (ROLE_TOTAL_LEADER, ROLE_THEME_LEADER):
        return _dim("C", 15, f"{boards} 板龙头，绝对高位", "K33")
    if 1 <= boards <= 2:
        return _dim("C", 15, f"{boards} 板，绝对低位", "K33")
    if boards == 0:
        return _dim("C", 8, "非涨停股，位置按中性计", "K11")
    return _dim("C", 5, f"{boards} 板，偏中位", "K11")


def _score_board(cand: Dict[str, Any], rules: Dict[str, Any]) -> Dict[str, Any]:
    kline = cand.get("kline") or {}
    if cand.get("origin") != "zt":
        score, bits = 3.0, []
        shadow, ratio, change = kline.get("lower_shadow"), kline.get("volume_ratio"), kline.get("today_change")
        if shadow is not None and shadow >= 0.3:
            score += 5
            bits.append("长下影")
        if ratio is not None and ratio < 1:
            score += 4
            bits.append("缩量")
        if change is not None and change > -9:
            score += 3
            bits.append("未封死跌停")
        return _dim("D", score, "、".join(bits) or "日K形态无明显止跌信号", "C05")

    board_type = cand.get("limit_up_type") or ""
    turnover = cand.get("turnover_rate") or 0.0
    death = float(rules.get("death_turnover", 70.0))
    score = 6.0 if "换手" in board_type else (4.0 if "T" in board_type else 2.0)
    bits = [board_type or "涨停"]
    if (cand.get("first_time") or "") >= str(rules.get("weak_late_time", "143000")) and cand.get("mode") != MODE_WEAK_TO_STRONG:
        # 尾盘板（科比 #33 算弱板）：只对非弱转强模式减分，弱转强本来就要弱板。
        score -= 2
        bits.append("尾盘板（弱板）")
    ratio = cand.get("seal_ratio")
    if ratio is not None:
        score += 3 if ratio >= 0.03 else (2 if ratio >= 0.01 else 1)
        bits.append(f"封单/流通市值 {ratio:.1%}")
    if 5 <= turnover <= 30:
        score += 3
        bits.append(f"换手 {turnover:.1f}%")
    elif turnover > death:
        score = min(score, 3)
        bits.append(f"换手 {turnover:.0f}%（死亡换手）")
    if cand.get("mode") in (MODE_WEAK_TO_STRONG, MODE_LEADER) and int(cand.get("open_num") or 0) > 0:
        score += 2
        bits.append("炸板回封（分歧中换手）")
    return _dim("D", score, "、".join(bits), "K14", "K29", "K27")


def _score_temper(cand: Dict[str, Any]) -> Dict[str, Any]:
    kline = cand.get("kline") or {}
    suc = cand.get("suc_rate")
    premium = kline.get("next_day_premium")
    score = 0.0
    bits = []
    if suc is not None:
        score += 6 * float(suc)
        bits.append(f"历史封板成功率 {float(suc):.0%}")
    else:
        score += 3
        bits.append("封板率无数据（按中性计）")
    if premium is None:
        score += 1
        bits.append("涨停次日溢价无样本")
    else:
        score += 4 if premium >= 3 else (3 if premium >= 1 else (2 if premium >= 0 else 0))
        bits.append(f"近半年涨停 {kline.get('next_day_samples') or 0} 次，次日平均 {premium:+.1f}%")
    return _dim("E", score, "；".join(bits), "K25")


def _score_popularity(cand: Dict[str, Any], hot_rank: Dict[str, int]) -> Dict[str, Any]:
    rank = hot_rank.get(cand["code"])
    score = 0.0
    bits = []
    if rank is not None:
        score += 5 if rank <= 20 else (4 if rank <= 50 else 3)
        bits.append(f"人气榜第 {rank} 名")
    elif hot_rank:
        bits.append("未进人气榜前 100")
    else:
        bits.append("人气榜不可用")
    amount = cand.get("amount")
    if amount:
        score += 3 if amount >= 10e8 else (2 if amount >= 3e8 else (1 if amount >= 1e8 else 0))
        bits.append(f"成交约 {amount / 1e8:.1f} 亿")
    turnover = cand.get("turnover_rate") or 0
    score += 2 if turnover >= 5 else (1 if turnover >= 2 else 0)
    return _dim("F", score, "，".join(bits), "K01")


def _score_cheap(cand: Dict[str, Any]) -> Dict[str, Any]:
    price, circ = cand.get("price"), cand.get("circ_mv")
    score = 0.0
    bits = []
    if price is not None:
        score += 3 if price <= 10 else (2 if price <= 20 else (1 if price <= 30 else 0))
        bits.append(f"股价 {price:.2f} 元")
    if circ:
        score += 2 if circ <= 50e8 else (1 if circ <= 100e8 else 0)
        bits.append(f"流通市值 {circ / 1e8:.0f} 亿")
    return _dim("G", score, "，".join(bits) or "无价格数据", "K23", "K20")


def _risks(cand: Dict[str, Any], theme: Optional[Dict[str, Any]], rules: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if theme:
        if int(theme.get("days") or 0) > int(rules.get("theme_long_days", 45)):
            items.append({"points": 6, "text": f"题材已持续 {theme['days']} 天，超过一个半月", "principle_ids": ["K13"]})
        if theme.get("status") == "fade":
            items.append({"points": 5, "text": "题材内昨日涨停今日大面积亏钱，进入退潮", "principle_ids": ["K04"]})
        elif theme.get("premium") is not None and theme["premium"] < -3:
            items.append({"points": 4, "text": f"题材内昨日涨停今日平均 {theme['premium']:+.1f}%，亏钱效应在扩散",
                          "principle_ids": ["K04"]})
    kline = cand.get("kline") or {}
    if (
        cand.get("role") in (ROLE_TOTAL_LEADER, ROLE_THEME_LEADER)
        and kline.get("volume_ratio") is not None
        and kline["volume_ratio"] >= 2
        and (kline.get("today_change") or 0) < 5
    ):
        items.append({"points": 5, "text": f"高位放量（量比 {kline['volume_ratio']:.1f}）但涨幅不足 5%，有滞涨迹象",
                      "principle_ids": ["C06", "C23"]})
    turnover = cand.get("turnover_rate") or 0
    if turnover > float(rules.get("death_turnover", 70.0)):
        items.append({"points": 6, "text": f"换手 {turnover:.0f}%，死亡换手", "principle_ids": ["K29"]})
    return items


def grade_of(score: float, rules: Dict[str, Any]) -> str:
    bands = rules.get("grade") or {}
    if score >= bands.get("S", 85):
        return "S"
    if score >= bands.get("A", 75):
        return "A"
    if score >= bands.get("B", 65):
        return "B"
    return "C"


def score_candidate(
    cand: Dict[str, Any],
    *,
    theme_by_key: Dict[str, Dict[str, Any]],
    hot_rank: Dict[str, int],
    rules: Dict[str, Any],
) -> None:
    """就地写入 ``dimensions`` / ``rule_score`` / ``risks`` / ``score`` / ``grade``。"""
    theme = theme_by_key.get(cand.get("theme") or "")
    dims = [
        _score_theme(cand, theme, rules),
        _score_role(cand),
        _score_position(cand),
        _score_board(cand, rules),
        _score_temper(cand),
        _score_popularity(cand, hot_rank),
        _score_cheap(cand),
    ]
    risks = _risks(cand, theme, rules)
    cand["dimensions"] = dims
    cand["rule_score"] = round(sum(d["score"] for d in dims), 1)
    cand["risks"] = risks
    cand["risk_penalty"] = min(20, sum(item["points"] for item in risks))
    cand.setdefault("llm_adjust", 0)
    finalize_score(cand, rules)


def finalize_score(cand: Dict[str, Any], rules: Dict[str, Any]) -> None:
    adjust = max(-10, min(10, int(cand.get("llm_adjust") or 0)))
    cand["llm_adjust"] = adjust
    total = cand["rule_score"] + adjust - cand["risk_penalty"]
    cand["score"] = round(max(0.0, min(100.0, total)), 1)
    cand["grade"] = grade_of(cand["score"], rules)


__all__ = ["DIMENSIONS", "finalize_score", "grade_of", "score_candidate"]
