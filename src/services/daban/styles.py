# -*- coding: utf-8 -*-
"""两条打法线（陈小群 / 92科比）与「今日更推荐哪种打法」的裁决。

两篇文章在退潮、分歧期的主张互相冲突（陈小群：退潮坚决空仓；科比：主跌第二天尾盘找买点）。
这里不做调和，而是分成两条线各按自己的门控矩阵出推荐，再用一组**透明投票**裁决当天更适合
哪一派。每一票都输出命中与否和依据，页面逐票展示。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from src.services.daban.constants import (
    ACTION_ACTIONABLE,
    ACTION_LABELS,
    ACTION_ORDER,
    ACTION_TRIAL,
    ACTION_WATCH,
    FLAT_MESSAGES,
    MODE_WEAK_TO_STRONG,
    ROLE_THEME_LEADER,
    ROLE_TOTAL_LEADER,
    SELL_RULES,
    STAGE_CLIMAX,
    STAGE_DIVERGE,
    STAGE_FERMENT,
    STAGE_RETREAT,
    STAGE_WARM,
    STYLE_LABELS,
)
from src.services.daban.principles import STYLE_CHEN, STYLE_KOBE, filter_for_style

STYLES = (STYLE_CHEN, STYLE_KOBE)


def _gate(rules: Dict[str, Any], style: str, mode: str, stage_key: str) -> Tuple[Optional[str], Optional[str]]:
    raw = ((rules.get("gates") or {}).get(style) or {}).get(mode, {}).get(stage_key)
    if not raw:
        return None, None
    action, _, cond = str(raw).partition("@")
    return action, (cond or None)


def _style_items(items: List[Dict[str, Any]], style: str) -> List[Dict[str, Any]]:
    """只留带本派出处的条目，出处也只留本派的。"""
    out = []
    for item in items or []:
        ids = filter_for_style(item.get("principle_ids") or [], style)
        if ids:
            out.append({"text": item["text"], "principle_ids": ids})
    return out


def _position(style: str, action: str, role: str, rules: Dict[str, Any]) -> int:
    table = (rules.get("position") or {}).get(style) or {}
    if action == ACTION_WATCH:
        return 0
    if style == STYLE_CHEN:
        leader = role in (ROLE_TOTAL_LEADER, ROLE_THEME_LEADER)
        if action == ACTION_ACTIONABLE:
            return int(table.get("leader" if leader else "follower", 10))
        return int(table.get("trial_leader" if leader else "trial_follower", 5))
    return int(table.get("actionable" if action == ACTION_ACTIONABLE else "trial", 10))


def _pick(cand: Dict[str, Any], style: str, action: str, rules: Dict[str, Any], notes: List[str]) -> Dict[str, Any]:
    reasons = _style_items(cand.get("signals") or [], style)
    llm = cand.get("llm_note")
    llm_note = None
    if llm:
        ids = filter_for_style(llm.get("principle_ids") or [], style)
        if ids:
            llm_note = {"text": llm["text"], "principle_ids": ids}
    return {
        "code": cand["code"],
        "name": cand["name"],
        "mode": cand["mode"],
        "mode_label": cand["mode_label"],
        "role": cand["role"],
        "role_label": cand["role_label"],
        "theme": cand.get("theme"),
        "theme_name": cand.get("theme_name") or "",
        "origin": cand.get("origin"),
        "action": action,
        "action_label": ACTION_LABELS[action],
        "score": cand["score"],
        "grade": cand["grade"],
        "rule_score": cand["rule_score"],
        "llm_adjust": cand.get("llm_adjust", 0),
        "llm_adjust_reason": cand.get("llm_adjust_reason") or "",
        "risk_penalty": cand.get("risk_penalty", 0),
        "dimensions": cand.get("dimensions") or [],
        "risks": cand.get("risks") or [],
        "facts": cand.get("facts") or [],
        "reasons": reasons,
        "llm_note": llm_note,
        "triggers": _style_items(cand.get("triggers") or [], style),
        "abandon": _style_items(cand.get("abandon") or [], style),
        "position_pct": _position(style, action, cand["role"], rules),
        "price": cand.get("price"),
        "change_pct": cand.get("change_pct"),
        "boards": cand.get("boards"),
        "limit_up_type": cand.get("limit_up_type") or "",
        "turnover_rate": cand.get("turnover_rate"),
        "circ_mv": cand.get("circ_mv"),
        "notes": list(cand.get("notes") or []) + notes,
        "also_modes": [],
        "consensus": False,
    }


def build_lane(
    style: str,
    candidates: List[Dict[str, Any]],
    *,
    stage: Dict[str, Any],
    kobe: Dict[str, Any],
    rules: Dict[str, Any],
    max_actionable: int,
) -> Dict[str, Any]:
    """按某一派的门控矩阵，把共用候选筛成这一条线的推荐。"""
    stage_key = stage["key"] if style == STYLE_CHEN else kobe["gate_key"]
    stage_label = stage["label"] if style == STYLE_CHEN else kobe["label"]
    cap = int(((rules.get("lane_caps") or {}).get(style) or {}).get(stage_key, 0))
    cap_label = ((rules.get("lane_cap_labels") or {}).get(style) or {}).get(stage_key, f"{cap}%")
    grade_floor = float((rules.get("grade") or {}).get("B", 65))

    gated: List[Dict[str, Any]] = []
    for cand in candidates:
        action, cond = _gate(rules, style, cand["mode"], stage_key)
        if action is None:
            continue
        notes: List[str] = []
        if cond and not (cand.get("conditions") or {}).get(cond):
            notes.append(_COND_NOTES.get(cond, "放行条件未满足"))
            action = ACTION_WATCH
        if action != ACTION_WATCH and cand["score"] < grade_floor:
            notes.append(f"打板王指数 {cand['score']:.0f} < {grade_floor:.0f}，只进观察")
            action = ACTION_WATCH
        if cap <= 0 and action != ACTION_WATCH:
            action = ACTION_WATCH
        pick = _pick(cand, style, action, rules, notes)
        if not pick["reasons"]:
            # 这一派的理念里找不到支撑这只票的理由，就不放进这一派。
            continue
        gated.append(pick)

    # 同一只票多个模式：留行动级别最高、分数最高的那个，其余记为「也符合」。
    best: Dict[str, Dict[str, Any]] = {}
    for pick in gated:
        current = best.get(pick["code"])
        rank = (ACTION_ORDER[pick["action"]], -pick["score"])
        if current is None or rank < (ACTION_ORDER[current["action"]], -current["score"]):
            if current is not None:
                pick["also_modes"] = sorted({*current["also_modes"], current["mode_label"]})
            best[pick["code"]] = pick
        else:
            current["also_modes"] = sorted({*current["also_modes"], pick["mode_label"]})

    ordered = sorted(best.values(), key=lambda p: (ACTION_ORDER[p["action"]], -p["score"]))
    actionable: List[Dict[str, Any]] = []
    trial: List[Dict[str, Any]] = []
    watch: List[Dict[str, Any]] = []
    used = 0
    max_trial = int(rules.get("max_trial", 6))
    max_watch = int(rules.get("max_watch", 8))
    for pick in ordered:
        action = pick["action"]
        if action == ACTION_ACTIONABLE and len(actionable) >= max_actionable:
            pick["notes"].append(f"超过「分 {max_actionable} 票」上限，降为试错")
            action = ACTION_TRIAL
        if action != ACTION_WATCH:
            room = cap - used
            if room <= 0:
                pick["notes"].append("本打法当前阶段的仓位上限已用满，降为观察")
                action = ACTION_WATCH
            else:
                pick["position_pct"] = min(pick["position_pct"], room)
        if action == ACTION_TRIAL and len(trial) >= max_trial:
            action = ACTION_WATCH
        pick["action"] = action
        pick["action_label"] = ACTION_LABELS[action]
        if action == ACTION_WATCH:
            pick["position_pct"] = 0
            watch.append(pick)
            continue
        used += pick["position_pct"]
        (actionable if action == ACTION_ACTIONABLE else trial).append(pick)

    # 观察区：总龙头置顶（空间板是情绪标杆，分数再低也要让人看到），其余按分数截断。
    watch.sort(key=lambda p: (0 if p["role"] == ROLE_TOTAL_LEADER else 1, -p["score"]))
    watch = watch[:max_watch]

    flat_text, flat_ids = FLAT_MESSAGES.get((style, stage_key), ("", []))
    return {
        "style": style,
        "label": STYLE_LABELS[style],
        "stage_key": stage_key,
        "stage_label": stage_label,
        "position_cap": cap,
        "position_cap_label": cap_label,
        "position_used": used,
        "flat": cap <= 0,
        "flat_message": flat_text if cap <= 0 else "",
        "flat_principles": flat_ids if cap <= 0 else [],
        "actionable": actionable,
        "trial": trial,
        "watch": watch,
        "sell_rules": [{"text": text, "principle_ids": ids} for text, ids in SELL_RULES[style]],
        "commentary": "",
        "commentary_principles": [],
    }


_COND_NOTES = {
    "total_leader": "分歧期只做全市场绝对龙头的低吸",
    "market_alive": "「大势未死」不成立（涨停不足 30 家或昨日涨停亏钱、无新题材接力）",
    "theme_profit": "题材还没打出赚钱效应（题材内昨日涨停今日不赚钱）",
    "no_mainline": "当前仍有主线，次新不是资金去处",
}


def mark_consensus(lanes: Dict[str, Dict[str, Any]]) -> List[str]:
    """两条线都放进可执行 / 试错的票，标「两派共识」。"""
    def _codes(lane):
        return {p["code"] for p in lane["actionable"] + lane["trial"]}

    shared = _codes(lanes[STYLE_CHEN]) & _codes(lanes[STYLE_KOBE])
    for lane in lanes.values():
        for pick in lane["actionable"] + lane["trial"] + lane["watch"]:
            pick["consensus"] = pick["code"] in shared
    return sorted(shared)


# ---------------------------------------------------------------------- 打法裁决


def _vote(key: str, style: str, label: str, hit: Optional[bool], evidence: str, ids: List[str]) -> Dict[str, Any]:
    return {
        "key": key,
        "style": style,
        "label": label,
        "hit": bool(hit),
        "available": hit is not None,
        "evidence": evidence,
        "principle_ids": ids,
    }


def decide_style(
    *,
    metrics: Dict[str, Any],
    stage: Dict[str, Any],
    kobe: Dict[str, Any],
    themes: List[Dict[str, Any]],
    candidates: List[Dict[str, Any]],
    lanes: Dict[str, Dict[str, Any]],
    flags: Dict[str, bool],
    track_record: Optional[Dict[str, Any]],
    rules: Dict[str, Any],
) -> Dict[str, Any]:
    d = metrics.get("delta") or {}
    stage_key = stage["key"]
    top = themes[0] if themes else None
    zt_count = metrics.get("zt_count") or 0
    votes: List[Dict[str, Any]] = []

    # c1 亏钱效应仍在扩散
    spreading = [
        name for name, value in (
            ("跌停增多", (d.get("dt_count") or 0) > 0),
            ("大面股增多", (d.get("big_loss_count") or 0) > 0),
            ("昨日涨停溢价走低", (d.get("yzt_premium") or 0) < 0),
        ) if value
    ]
    c1 = stage_key in (STAGE_DIVERGE, STAGE_RETREAT) and bool(spreading)
    votes.append(_vote(
        "c1", STYLE_CHEN, "亏钱效应仍在扩散", c1,
        f"阶段 {stage['label']}；" + ("、".join(spreading) if spreading else "亏钱指标未继续恶化"),
        ["C10", "C02"],
    ))

    # c2 主线已被市场确认
    share = (top["zt_count"] / zt_count) if (top and zt_count) else 0.0
    c2 = bool(top) and share >= float(rules.get("mainline_confirm_share", 0.30)) and (
        top.get("max_boards") or 0) >= int(rules.get("mainline_confirm_height", 4))
    votes.append(_vote(
        "c2", STYLE_CHEN, "主线已被市场确认", c2,
        (f"第一主线「{top['name']}」涨停 {top['zt_count']} 只（占 {share:.0%}），最高 {top['max_boards']} 板"
         if top else "没有成型的题材"),
        ["C13", "C14"],
    ))

    # c3 有合格弱转强
    w2s = [c for c in candidates if c["mode"] == MODE_WEAK_TO_STRONG and c.get("theme_is_main")]
    chen_w2s = [p for p in lanes[STYLE_CHEN]["actionable"] + lanes[STYLE_CHEN]["trial"] if p["mode"] == MODE_WEAK_TO_STRONG]
    votes.append(_vote(
        "c3", STYLE_CHEN, "有合格的弱转强标的", bool(chen_w2s),
        f"主线弱板候选 {len(w2s)} 只，陈小群线可做 {len(chen_w2s)} 只",
        ["C04"],
    ))

    # c4 黄金窗口
    c4 = stage_key in (STAGE_WARM, STAGE_FERMENT, STAGE_CLIMAX)
    votes.append(_vote("c4", STYLE_CHEN, "处于回暖→发酵→高潮的黄金窗口", c4, f"当前 {stage['label']}", ["C02"]))

    # k1 主跌边际衰竭
    fall_day = int(kobe.get("fall_day") or 0)
    easing = (d.get("dt_count") is not None and d["dt_count"] < 0) or (
        d.get("yzt_premium") is not None and d["yzt_premium"] > 0)
    votes.append(_vote(
        "k1", STYLE_KOBE, "主跌第二天起，杀跌边际衰竭", fall_day >= 2 and easing,
        f"连续杀跌第 {fall_day} 天；" + ("跌停减少或昨日涨停溢价回升" if easing else "杀跌尚未缓和"),
        ["K09"],
    ))

    # k2 高低切信号
    tier1 = d.get("tier1_rate")
    new_themes = [t for t in themes if t["novelty"] == "new" and t["zt_count"] >= int(rules.get("new_theme_min_zt", 3))]
    high_fall = flags.get("high_leader_hit") or (d.get("max_height") or 0) < 0
    k2 = bool(high_fall) and ((tier1 is not None and tier1 > 0) or bool(new_themes))
    votes.append(_vote(
        "k2", STYLE_KOBE, "高位见顶、低位新题材起来（高低切）", k2,
        ("高位核按钮或高度回落；" if high_fall else "高位尚未见顶；")
        + (f"新题材 {'、'.join(t['name'] for t in new_themes[:3])}" if new_themes else "无成型新题材")
        + (f"，1进2 晋级率 {'回升' if (tier1 or 0) > 0 else '未回升'}" if tier1 is not None else ""),
        ["K05", "K12", "K26"],
    ))

    # k3 无主线可炒
    sub_new_strong = sum(1 for c in candidates if c["mode"] == "M7")
    k3 = flags.get("no_mainline", False) and sub_new_strong >= int(rules.get("sub_new_strong_min", 3))
    votes.append(_vote(
        "k3", STYLE_KOBE, "主流无题材、次新走强", k3,
        f"第一题材涨停 {top['zt_count'] if top else 0} 只；强势次新 {sub_new_strong} 只",
        ["K16", "K20"],
    ))

    # k4 可发酵事件
    fermentable = [
        t for t in themes
        if t.get("story_space") == "big" and t["novelty"] != "same"
        and t["zt_count"] < int(rules.get("main_theme_min_zt", 3)) + 3
    ]
    story_known = any(t.get("story_space") for t in themes)
    votes.append(_vote(
        "k4", STYLE_KOBE, "有故事空间大、尚未被充分认可的新题材", bool(fermentable) if story_known else None,
        ("、".join(f"{t['name']}（故事空间{t['story_label']}）" for t in fermentable[:3]) or "未发现")
        if story_known else "故事空间未评估（模型不可用）",
        ["K10"],
    ))

    # r1 近期战绩
    record = track_record or {}
    chen_avg, kobe_avg = (record.get(STYLE_CHEN) or {}).get("avg"), (record.get(STYLE_KOBE) or {}).get("avg")
    min_days = int(rules.get("track_record_min_days", 5))
    enough = min((record.get(STYLE_CHEN) or {}).get("days", 0), (record.get(STYLE_KOBE) or {}).get("days", 0)) >= min_days
    gap = float(rules.get("track_record_min_gap", 0.5))
    if enough and chen_avg is not None and kobe_avg is not None and abs(chen_avg - kobe_avg) >= gap:
        winner = STYLE_CHEN if chen_avg > kobe_avg else STYLE_KOBE
        votes.append(_vote(
            "r1", winner, "近期战绩更好", True,
            f"近 {rules.get('track_record_days', 10)} 日推荐次日平均：陈小群 {chen_avg:+.2f}%，92科比 {kobe_avg:+.2f}%",
            ["K32"],
        ))
    else:
        votes.append(_vote(
            "r1", "", "近期战绩更好", None,
            "样本不足或两派差距不大，本票不投" if not enough else
            f"两派次日平均差距不足 {gap}%（陈小群 {chen_avg}，92科比 {kobe_avg}）",
            ["K32"],
        ))

    chen_votes = sum(1 for v in votes if v["hit"] and v["style"] == STYLE_CHEN)
    kobe_votes = sum(1 for v in votes if v["hit"] and v["style"] == STYLE_KOBE)
    chen_codes = {p["code"] for p in lanes[STYLE_CHEN]["actionable"]}
    kobe_codes = {p["code"] for p in lanes[STYLE_KOBE]["actionable"]}
    union = chen_codes | kobe_codes
    overlap = len(chen_codes & kobe_codes) / len(union) if union else 0.0

    if abs(chen_votes - kobe_votes) <= 1 or overlap >= 0.5:
        preferred = "both"
    else:
        preferred = STYLE_CHEN if chen_votes > kobe_votes else STYLE_KOBE
    if preferred == "both":
        label = "两派共识日" if overlap >= 0.5 else "两派势均力敌"
    else:
        label = f"今日更推荐：{STYLE_LABELS[preferred]}"
    return {
        "preferred": preferred,
        "label": label,
        "votes": {STYLE_CHEN: chen_votes, STYLE_KOBE: kobe_votes},
        "overlap": round(overlap, 4),
        "signals": votes,
        "summary": _verdict_summary(preferred, votes, lanes),
        "model_opinion": None,
    }


def _verdict_summary(preferred: str, votes: List[Dict[str, Any]], lanes: Dict[str, Dict[str, Any]]) -> str:
    hits = {style: [v["label"] for v in votes if v["hit"] and v["style"] == style] for style in STYLES}
    if preferred == "both":
        return (
            f"陈小群线依据：{'、'.join(hits[STYLE_CHEN]) or '无'}；92科比线依据：{'、'.join(hits[STYLE_KOBE]) or '无'}。"
            "两派信号接近，两条线都可以参考，仓位按各自上限。"
        )
    other = STYLE_KOBE if preferred == STYLE_CHEN else STYLE_CHEN
    lane = lanes[preferred]
    return (
        f"{STYLE_LABELS[preferred]}占优：{'、'.join(hits[preferred])}。"
        f"该线当前 {lane['stage_label']}，仓位上限 {lane['position_cap_label']}。"
        f"{STYLE_LABELS[other]}依据：{'、'.join(hits[other]) or '无'}。"
    )


__all__ = ["STYLES", "build_lane", "decide_style", "mark_consensus"]
