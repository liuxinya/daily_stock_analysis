# -*- coding: utf-8 -*-
"""短线世界观：两派共用的一份市场判断（Sleep交易员的 11 个维度，判定标准取 92科比、陈小群原文，原文没给标准的用回测定）。

作用分四种：边界（能不能推荐）、计分（见 ``rec_features``）、标签和理由（页面展示）、模型判断（只写文字，不改结论）。

- **盘后版**（:func:`build_worldview`）：收盘后生成，给下一个交易日用，存进快照 payload 的 ``worldview``。
- **盘中版**（:func:`live_worldview`）：以盘后版为起点，只有实时周期重判、推翻信号、停发新推荐三种情况能改结论，
  每次改动记一条（时刻、维度、改前改后、触发数据）。
- **边界**（:func:`decide_boundary` / :func:`candidate_gate`）：冰点 / 退潮 / 主跌第 1 天不推荐；退潮或主跌第 1 天遇到
  「龙头见顶杀高位」照常推荐（回测 15 天按天 +0.86%）；中位股不推荐；新题材只在原文时机推荐；补涨结束停推。
- **推翻信号**（:func:`detect_overrides`，PR 5）：大盘指数急拉、题材异动、新题材出现。触发后只放开触发的那个方向，
  当天不再收回。阈值是暂定值，等盘中留档攒够数据再校准。
- **次日对账**（:func:`reconcile`）：用第二天的实际结果核对前一天的世界观，逐维度累计准确率。
"""

from __future__ import annotations

from statistics import mean
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.services.daban.constants import STAGE_LABELS
from src.services.daban.kline import limit_pct
from src.services.daban.themes import related_tags, split_reason

NODE_LABELS = {
    "top_killed": "龙头见顶杀高位",
    "high_diverge": "高位分歧",
    "after_fall": "主跌之后",
}
NODE_SOURCES = {
    "top_killed": "科比 #9「龙头见顶杀高位的阶段，往往是最好的布局低位新题材的时机」",
    "high_diverge": "科比 #14「当高位出现分歧……新题材往往是一个非常好的接入点」",
    "after_fall": "科比 #9「第二天的杀跌如果还是持续的话，尾盘就可以找买点。第三天肯定会有情绪的修复」",
}
COMMONALITY_LABELS = {"same": "共性相同（补涨 / 发散）", "related": "部分相同（发散）", "new": "共性不同（新题材 / 切换）",
                      "unknown": "无法判断（成员太少或没有近期主线）"}


# ---------------------------------------------------------------------- 节点


def node_flags(*, d2_rows: List[Dict[str, Any]], d1_rows: List[Dict[str, Any]], d1_change: Dict[str, float],
               fall_day_d1: int) -> Dict[str, Any]:
    """D 日开盘前已知的节点（只用 D-2、D-1 的池子）。

    - 龙头见顶杀高位：D-2 的最高板（≥3 板）在 D-1 跌 ≥5%；
    - 高位分歧：D-1 的最高板低于 D-2；
    - 主跌之后：D-1 收盘是主跌第 2 天及以后。
    """
    h2 = max([int(r.get("boards") or 1) for r in d2_rows or []] or [0])
    h1 = max([int(r.get("boards") or 1) for r in d1_rows or []] or [0])
    tops = [r for r in d2_rows or [] if int(r.get("boards") or 1) == h2]
    killed = [r for r in tops if (d1_change.get(r.get("code")) or 0) <= -5]
    flags = {
        "top_killed": bool(h2 >= 3 and killed),
        "high_diverge": bool(d1_rows and d2_rows and h1 < h2),
        "after_fall": int(fall_day_d1 or 0) >= 2,
    }
    return {
        **flags,
        "h2": h2, "h1": h1,
        "killed": [{"code": r.get("code"), "name": r.get("name") or "", "boards": h2,
                    "change": round(float(d1_change.get(r.get("code")) or 0), 2)} for r in killed],
        "labels": [NODE_LABELS[k] for k, v in flags.items() if v],
        "any": any(flags.values()),
    }


def node_text(nodes: Dict[str, Any]) -> str:
    if not nodes or not nodes.get("any"):
        return "无节点"
    parts = []
    if nodes.get("top_killed"):
        names = "、".join(f"{k['name']}（{k['change']:+.1f}%）" for k in nodes.get("killed") or [])
        parts.append(f"龙头见顶杀高位：前一天最高 {nodes.get('h2')} 板{('：' + names) if names else ''}")
    if nodes.get("high_diverge"):
        parts.append(f"高位分歧：最高板 {nodes.get('h2')} → {nodes.get('h1')}")
    if nodes.get("after_fall"):
        parts.append("主跌之后（第 2 天及以后）")
    return "；".join(parts)


# ---------------------------------------------------------------------- 共性（补涨还是切换）


def member_commonality(members: Iterable[str], memory: Dict[str, set], recent_mains: Iterable[str],
                       rules: Dict[str, Any]) -> Dict[str, Any]:
    """按成员判共性：题材成员近 90 天的涨停原因落在近 10 日主线里的比例（科比 #7/#8「完全不同的东西才是新」）。

    比字面更可靠：同一个共性换个名字（「AI 算力」→「液冷服务器」）字面对不上，成员的历史涨停原因对得上。
    没有历史涨停记录的成员算「不在近期主线里」。
    """
    cfg = rules.get("worldview") or {}
    mains = [m for m in recent_mains or [] if m]
    codes = [c for c in dict.fromkeys(members or []) if c]
    if len(codes) < 2 or not mains:
        # 成员太少，或者本地还没有近 10 日主线（冷启动）：没有比较对象，不能把一切都当成新题材挡掉。
        return {"kind": "unknown", "label": COMMONALITY_LABELS["unknown"], "share": None, "n": len(codes), "matched": 0}
    matched = sum(1 for c in codes if related_tags(memory.get(c) or set(), mains))
    share = matched / len(codes)
    if share >= float(cfg.get("commonality_same_min", 0.5)):
        kind = "same"
    elif share <= float(cfg.get("commonality_new_max", 0.2)):
        kind = "new"
    else:
        kind = "related"
    return {"kind": kind, "label": COMMONALITY_LABELS[kind], "share": round(share, 3), "n": len(codes), "matched": matched}


def theme_members(name: str, rows: List[Dict[str, Any]], blocks: Optional[List[Dict[str, Any]]] = None) -> List[str]:
    """题材当下的成员：涨停原因相关的涨停股 + 题材榜上的成员。"""
    out = [r["code"] for r in rows or [] if related_tags(split_reason(r.get("reason") or ""), [name])]
    for block in blocks or []:
        if related_tags([block.get("name") or ""], [name]):
            out.extend(str(m.get("code") or "") for m in block.get("members") or [])
    return [c for c in dict.fromkeys(out) if c]


def catch_up_end(name: str, *, yesterday_rows: List[Dict[str, Any]], quotes: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """科比 #6 补涨结束的两个征兆：低位补涨股隔日亏钱，且龙头没有反包。

    - 低位补涨：这个题材昨天的首板，今天平均涨跌幅为负；
    - 龙头不反包：这个题材昨天最高板的那只，今天也在跌。
    规则近似，页面标出处，复盘时对账。
    """
    members = [r for r in yesterday_rows or [] if related_tags(split_reason(r.get("reason") or ""), [name])]
    if len(members) < 2:
        return {"ended": False, "reason": "", "low_avg": None}

    def _chg(code: str) -> Optional[float]:
        q = quotes.get(code) or {}
        return (q["price"] / q["prev_close"] - 1) * 100 if q.get("price") and q.get("prev_close") else None

    lows = [c for c in (_chg(r["code"]) for r in members if int(r.get("boards") or 1) == 1) if c is not None]
    top = max(members, key=lambda r: int(r.get("boards") or 1))
    top_chg = _chg(top["code"])
    low_avg = round(mean(lows), 2) if lows else None
    ended = bool(low_avg is not None and low_avg < 0 and top_chg is not None and top_chg < 0
                 and int(top.get("boards") or 1) >= 2)
    reason = (f"补涨结束（科比 #6）：昨日首板今天平均 {low_avg:+.1f}%，龙头 {top.get('name') or top['code']} 今天 {top_chg:+.1f}% 没有反包"
              if ended else "")
    return {"ended": ended, "reason": reason, "low_avg": low_avg, "leader_change": None if top_chg is None else round(top_chg, 2)}


# ---------------------------------------------------------------------- 预期


def expectation_key(row: Dict[str, Any]) -> str:
    boards = int(row.get("boards") or 1)
    tier = "首板" if boards == 1 else ("2板" if boards == 2 else "≥3板")
    if "一字" in str(row.get("limit_up_type") or ""):
        quality = "一字板"
    else:
        first, last = str(row.get("first_time") or ""), str(row.get("last_time") or "")
        weak = int(row.get("open_num") or 0) > 0 or last >= "143000" or (first.isdigit() and last.isdigit() and last > first)
        quality = "弱板" if weak else "硬板"
    return f"{tier}|{quality}"


def expectation_of(row: Dict[str, Any], open_gap: Optional[float], rules: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """昨日涨停股今天的开盘幅度（%）对历史基准：低于 P25 = 低于预期，高于 P75 = 超预期。20cm 折算成 10cm。"""
    if open_gap is None:
        return None
    key = expectation_key(row)
    base = (rules.get("expectation") or {}).get(key)
    if not base:
        return None
    gap10 = float(open_gap) / limit_pct(str(row.get("code") or "")) * 10
    p25, p50, p75, cont = base
    level = "above" if gap10 > p75 else ("below" if gap10 < p25 else "inline")
    return {"key": key, "level": level, "label": {"above": "超预期", "below": "低于预期", "inline": "符合预期"}[level],
            "gap10": round(gap10, 2), "p25": p25, "p75": p75, "cont_rate": cont}


def expectation_summary(yesterday_rows: List[Dict[str, Any]], quotes: Dict[str, Dict[str, Any]], rules: Dict[str, Any],
                        *, themes: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """09:25 竞价出来后：昨日涨停股逐只判预期，再汇总到题材（超预期占多数 = 题材超预期）和龙头（风向标）。"""
    per: Dict[str, Dict[str, Any]] = {}
    for row in yesterday_rows or []:
        q = quotes.get(row.get("code") or "") or {}
        gap = (q["open"] / q["prev_close"] - 1) * 100 if q.get("open") and q.get("prev_close") else None
        item = expectation_of(row, gap, rules)
        if item:
            per[row["code"]] = {**item, "name": row.get("name") or "", "boards": int(row.get("boards") or 1)}
    counts = {lvl: sum(1 for v in per.values() if v["level"] == lvl) for lvl in ("above", "inline", "below")}
    theme_rows = []
    for theme in themes or []:
        name = theme.get("name") or theme.get("key") or ""
        members = [r for r in yesterday_rows or [] if r.get("code") in per
                   and related_tags(split_reason(r.get("reason") or ""), [name])]
        if not members:
            continue
        above = sum(1 for r in members if per[r["code"]]["level"] == "above")
        below = sum(1 for r in members if per[r["code"]]["level"] == "below")
        lead = max(members, key=lambda r: int(r.get("boards") or 1))
        level = "above" if above > len(members) / 2 else ("below" if below > len(members) / 2 else "inline")
        theme_rows.append({"theme": theme.get("key") or name, "name": name, "n": len(members), "above": above, "below": below,
                           "level": level, "leader": {"code": lead["code"], "name": lead.get("name") or "",
                                                      "label": per[lead["code"]]["label"]}})
    return {"available": bool(per), "counts": counts, "stocks": per, "themes": theme_rows}


# ---------------------------------------------------------------------- 边界


def decide_boundary(*, phase: Dict[str, Any], nodes: Dict[str, Any], rt: Optional[Dict[str, Any]],
                    overrides: List[Dict[str, Any]], rules: Dict[str, Any],
                    surge: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """今天能不能推荐（只看市场级，个股级见 :func:`candidate_gate`）。

    底线是前一交易日收盘的周期：冰点 / 退潮 / 主跌第 1 天管住手。放行按优先级：
    1. 节点「龙头见顶杀高位」：退潮 / 主跌第 1 天照常推荐（冰点不放行，没有回测依据）；
    2. 盘中市场验证（实时周期连续确认、今天的周期已不在冰点 / 退潮）；
    3. 大盘急拉推荐点（``surge``，:func:`surge_point`）：只在它的 30 分钟标记窗口里放开全部（``surge["active"]``），
       标「未验证」——被挡住的日子一年只有 7 天样本（每笔 H1 +0.80 ± 0.96）；
    4. 推翻信号：题材异动 / 新题材只放开那个题材。推翻信号「大盘指数急拉」默认只提示、不放开
       （``overrides.index_opens``）：回测里它在被挡住的日子放开的票每笔 H1 -0.21（22 天），窗口之外的 -0.30。
    盘中赚钱效应回落且没有题材在接力时停发新推荐（推翻信号放开的题材除外）。停发优先于大盘急拉：大盘急拉推翻的是
    前一交易日的周期边界，停发是今天盘中、按我们实际买的那批票（昨日涨停溢价、炸板率）判的，更新也更贴近成绩。
    """
    cfg = rules.get("worldview") or {}
    rt = rt or {}
    stage = str(phase.get("chen_stage") or (phase.get("stage") or {}).get("key") or "")
    fall_day = int((phase.get("kobe_stage") or {}).get("fall_day") or phase.get("fall_day") or 0)
    blocks: List[str] = []
    if stage in (cfg.get("block_stages") or []):
        blocks.append(stage)
    if fall_day and fall_day == int(cfg.get("block_fall_day", 1)):
        blocks.append("fall_1")
    block_label = "、".join(STAGE_LABELS.get(b, "") or ("主跌第 1 天" if b == "fall_1" else b) for b in blocks)
    out: Dict[str, Any] = {
        "blocks": blocks, "block_label": block_label, "open_all": not blocks, "open_themes": [], "pass": None,
        "frozen": bool(rt.get("frozen")), "reasons": [], "status": "open",
        "cycle": {"chen": phase.get("chen_label") or STAGE_LABELS.get(stage, ""),
                  "kobe": phase.get("kobe_label") or (phase.get("kobe_stage") or {}).get("label") or "",
                  "fall_day": fall_day, "trade_date": phase.get("trade_date")},
        "nodes": nodes or {},
    }
    if blocks:
        out["reasons"].append(f"前一交易日收盘是{block_label}：管住手（陈小群「退潮期坚决空仓」、科比 #9「第一天杀跌是很恐怖的」）")
        node_pass = set(cfg.get("node_pass") or [])
        index_opens = bool((rules.get("overrides") or {}).get("index_opens", False))
        override_all = [o for o in overrides or [] if o.get("scope") == "all" and (index_opens or o.get("kind") != "index")]
        if (nodes or {}).get("top_killed") and all(b in node_pass for b in blocks):
            out["open_all"] = True
            out["pass"] = {"kind": "node", "label": "节点日：龙头见顶杀高位（科比 #9），和陈小群退潮空仓冲突，按回测照常推荐"}
        elif rt.get("market_confirmed") and rt.get("today_stage") not in (cfg.get("block_stages") or []):
            out["open_all"] = True
            out["pass"] = {"kind": "market", "label": f"盘中市场验证：今天的周期为{rt.get('today_label') or '—'}（陈小群「预判必须得到市场验证」）"}
        elif (surge or {}).get("active"):
            out["open_all"] = True
            out["pass"] = {"kind": "surge", "label": f"{surge.get('label') or '大盘急拉推荐点'}：只放开推荐点窗口"
                                                     f"（未验证：被挡住的日子一年只有 7 天样本）"}
        elif override_all:
            out["open_all"] = True
            out["pass"] = {"kind": "override", "label": override_all[0].get("label") or "推翻信号"}
        if not out["pass"] and any(o.get("kind") == "index" for o in overrides or []):
            out["reasons"].append("大盘指数急拉只提示、不放开（回测里放开的票平均亏钱）；等沪深300 推荐点触发才放开 30 分钟")
        out["open_themes"] = sorted({o["scope"] for o in overrides or [] if o.get("scope") not in (None, "", "all")})
    else:
        out["open_themes"] = sorted({o["scope"] for o in overrides or [] if o.get("scope") not in (None, "", "all")})
    if out["pass"]:
        out["reasons"].append(out["pass"]["label"])
    if out["frozen"]:
        out["reasons"].append(rt.get("reason") or "盘中赚钱效应回落且没有题材在接力：停发新推荐")
    if out["frozen"]:
        out["status"] = "frozen"
    elif out["open_all"]:
        out["status"] = "open"
    elif out["open_themes"]:
        out["status"] = "partial"
        out["reasons"].append("推翻信号只放开：" + "、".join(out["open_themes"]))
    else:
        out["status"] = "closed"
    return out


def candidate_gate(*, theme_key: str, y_boards: int, boundary: Dict[str, Any], theme_info: Optional[Dict[str, Any]],
                   rules: Dict[str, Any]) -> Optional[str]:
    """个股级边界。返回挡掉的理由；放行返回 None。"""
    cfg = rules.get("worldview") or {}
    opened = theme_key in (boundary.get("open_themes") or [])
    if int(y_boards or 0) in [int(x) for x in cfg.get("middle_boards") or []]:
        return f"中位股：昨日 {y_boards} 板今天冲 {int(y_boards) + 1} 板（科比 #12「切记不要去做中位股」，回测 -0.88%）"
    if boundary.get("frozen") and not opened:
        return "盘中赚钱效应回落且没有题材在接力：停发新推荐"
    if not boundary.get("open_all") and not opened:
        return f"周期边界：前一交易日收盘是{boundary.get('block_label') or '管住手的阶段'}，没有推翻信号放开这个方向"
    info = theme_info or {}
    if (info.get("commonality") or {}).get("kind") == "new" and not info.get("timing_ok"):
        return "新题材不在原文时机（科比 #9 / #14：龙头见顶杀高位、高位分歧、主跌之后），只观察"
    if (info.get("catch_up") or {}).get("ended"):
        return info["catch_up"]["reason"]
    return None


def surge_point(index_history: List[Tuple[str, Dict[str, float]]], rules: Dict[str, Any],
                names: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
    """当天第一次「大盘急拉推荐点」：``symbol`` 近 ``window_min`` 分钟涨幅 ≥ ``rise`` 个百分点，且此刻翻红。

    参考点 = ``window_min`` 分钟之前（或更早）最近的一个点，且不早于 ``ref_from``（开盘跳空不算急拉），与回测口径一致。
    只是买入时机的提示：不改边界、不改评分。没触发返回 None。
    """
    cfg = rules.get("surge_point") or {}
    if not cfg.get("enabled", True):
        return None
    symbol = str(cfg.get("symbol") or "sh000300")
    window, rise_min = int(cfg.get("window_min", 30)), float(cfg.get("rise", 0.8))
    ref_from = _hhmm_minutes(str(cfg.get("ref_from") or "093500"))
    series = sorted((_hhmm_minutes(t), t, v[symbol]) for t, v in index_history or []
                    if v.get(symbol) is not None and _hhmm_minutes(t) >= ref_from)
    for i, (now_min, now, current) in enumerate(series):
        refs = [v for m, _, v in series[:i] if m <= now_min - window]
        if not refs or current <= 0:
            continue
        rise = current - refs[-1]
        if rise >= rise_min:
            until = now_min + int(cfg.get("pin_min", 30))
            label = (names or {}).get(symbol) or "沪深300"
            return {
                "symbol": symbol, "at": now, "until": f"{until // 60:02d}{until % 60:02d}00",
                "rise": round(rise, 3), "change": round(current, 3), "threshold": rise_min, "window_min": window,
                "label": f"大盘急拉推荐点：{label} {window} 分钟拉升 {rise:.2f} 个百分点（现 {current:+.2f}%）",
            }
    return None


def timing_ok(nodes: Dict[str, Any], rules: Dict[str, Any]) -> bool:
    keys = (rules.get("worldview") or {}).get("new_theme_timing") or []
    return any((nodes or {}).get(k) for k in keys)


# ---------------------------------------------------------------------- 推翻信号（PR 5）


def _hhmm_minutes(hhmmss: str) -> int:
    text = str(hhmmss or "")
    try:
        return int(text[:2]) * 60 + int(text[2:4])
    except (TypeError, ValueError):
        return -1


def detect_overrides(
    *,
    now: str,
    index_history: List[Tuple[str, Dict[str, float]]],
    rows: List[Dict[str, Any]],
    hot_themes: List[Dict[str, Any]],
    theme_info: Dict[str, Dict[str, Any]],
    nodes: Dict[str, Any],
    rules: Dict[str, Any],
    names: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """这一轮新触发的推翻信号（调用方负责当天去重、持久）。"""
    cfg = rules.get("overrides") or {}
    if not cfg.get("enabled", True):
        return []
    out: List[Dict[str, Any]] = []
    now_min = _hhmm_minutes(now)
    window = int(cfg.get("index_window_min", 30))
    for symbol, threshold in (cfg.get("index_rise") or {}).items():
        series = [(t, v.get(symbol)) for t, v in index_history if v.get(symbol) is not None
                  and 0 <= now_min - _hhmm_minutes(t) <= window]
        if len(series) < 2:
            continue
        current = series[-1][1]
        low = min(v for _, v in series)
        rise = current - low
        if current > 0 and rise >= float(threshold):
            label = (names or {}).get(symbol, symbol)
            notice = "" if cfg.get("index_opens", False) else "（只提示，不放开）"
            out.append({"kind": "index", "scope": "all", "key": f"index:{symbol}", "at": now,
                        "label": f"大盘指数急拉{notice}：{label} {window} 分钟内拉升 {rise:.2f} 个百分点（现 {current:+.2f}%）",
                        "data": {"symbol": symbol, "rise": round(rise, 3), "change": round(current, 3), "threshold": threshold}})
    t_window, t_min = int(cfg.get("theme_window_min", 15)), int(cfg.get("theme_min_new", 3))
    cutoff = now_min - t_window
    for theme in hot_themes or []:
        name = str(theme.get("name") or theme.get("key") or "")
        fresh = [r for r in rows or [] if _hhmm_minutes(str(r.get("first_time") or "")) >= cutoff >= 0
                 and related_tags(split_reason(r.get("reason") or ""), [name])]
        if len(fresh) >= t_min:
            out.append({"kind": "theme", "scope": theme.get("key"), "key": f"theme:{theme.get('key')}", "at": now,
                        "label": f"题材异动：「{name}」{t_window} 分钟内新增封板 {len(fresh)} 只",
                        "data": {"count": len(fresh), "codes": [r["code"] for r in fresh][:8], "threshold": t_min}})
    if timing_ok(nodes, rules):
        for theme in hot_themes or []:
            info = theme_info.get(theme.get("key")) or {}
            if (info.get("commonality") or {}).get("kind") == "new" and int(theme.get("zt_count") or 0) >= int(cfg.get("new_theme_min_zt", 4)):
                out.append({"kind": "new_theme", "scope": theme.get("key"), "key": f"new_theme:{theme.get('key')}", "at": now,
                            "label": (f"新题材出现：「{theme.get('name')}」涨停 {theme.get('zt_count')} 只，成员和近期主线共性不同，"
                                      f"且在原文时机上（{node_text(nodes)}）"),
                            "data": {"zt_count": theme.get("zt_count"), "share": (info.get("commonality") or {}).get("share")}})
    return out


# ---------------------------------------------------------------------- 结论对象


def _dim(key: str, label: str, value: str, detail: str, role: str, source: str, tone: str = "neutral") -> Dict[str, Any]:
    return {"key": key, "label": label, "value": value, "detail": detail, "role": role, "source": source, "tone": tone}


def money_effect(metrics: Dict[str, Any]) -> Tuple[str, str]:
    premium = metrics.get("yzt_premium")
    if premium is None:
        return "未知", "neutral"
    if premium <= -2:
        return "亏钱效应明显", "bad"
    if premium < 0:
        return "偏亏钱", "bad"
    if premium < 2:
        return "有赚钱效应", "good"
    return "赚钱效应强", "good"


def build_worldview(
    *,
    for_date: str,
    based_on: str,
    metrics: Dict[str, Any],
    stage: Dict[str, Any],
    kobe: Dict[str, Any],
    themes: List[Dict[str, Any]],
    theme_info: Dict[str, Dict[str, Any]],
    nodes: Dict[str, Any],
    zt_rows: List[Dict[str, Any]],
    weak: Iterable[str],
    rules: Dict[str, Any],
    indices: Optional[Dict[str, Dict[str, Any]]] = None,
    summary: str = "",
    names: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """盘后版世界观：``based_on`` 收盘后生成，给 ``for_date`` 用。``names`` 补炸板池等不在涨停池里的股票名。"""
    m = metrics or {}
    money, money_tone = money_effect(m)
    zab = m.get("zab_rate")
    max_h = int(m.get("max_height") or 0)
    leaders = [r for r in zt_rows or [] if int(r.get("boards") or 1) == max_h and max_h >= 2]
    mains = [t for t in themes or [] if t.get("is_main")]
    weak = list(weak or [])
    names = {**{c: n for c, n in (names or {}).items() if n},
             **{r["code"]: r.get("name") or r["code"] for r in zt_rows or []}}
    catch = [t for t in themes[:8] if (theme_info.get(t["key"]) or {}).get("commonality", {}).get("kind") == "same" and not t.get("is_main")]
    switch = [t for t in themes[:8] if (theme_info.get(t["key"]) or {}).get("commonality", {}).get("kind") == "new"]
    fall_day = int(kobe.get("fall_day") or 0)
    cfg = rules.get("worldview") or {}
    blocked = stage.get("key") in (cfg.get("block_stages") or []) or fall_day == int(cfg.get("block_fall_day", 1))
    node_pass = blocked and nodes.get("top_killed")
    if not blocked:
        boundary = "可以推荐：按计分表评分、热点题材里推荐"
    elif node_pass:
        boundary = "照常推荐：节点日（龙头见顶杀高位）放行退潮 / 主跌第 1 天"
    else:
        boundary = "管住手：盘中出现推翻信号（大盘急拉、题材异动、新题材出现）时只放开触发的方向"
    idx_text = "、".join(f"{v.get('name') or k} {v.get('change_pct'):+.2f}%" for k, v in (indices or {}).items()
                        if v.get("change_pct") is not None) or "指数行情未取到"
    dims = [
        _dim("money_effect", "赚钱效应", money,
             f"昨日涨停今日 {_pct(m.get('yzt_premium'))}，上涨占比 {_rate(m.get('yzt_up_ratio'))}，大面 {m.get('big_loss_count') if m.get('big_loss_count') is not None else '—'} 只，炸板率 {_rate(zab)}",
             "周期判定的输入；盘中实时门控（升级 / 停发）", "Sleep「等赚钱氛围来了再进场」；科比 #28；陈小群五大指标④", money_tone),
        _dim("cycle", "周期", f"{stage.get('label') or '—'}｜科比：{kobe.get('label') or '—'}",
             (f"过渡态，次选{stage.get('secondary_label')}" if stage.get("transitional") else f"命中率 {_rate(stage.get('hit_rate'))}")
             + (f"；{stage.get('override')}" if stage.get("override") else ""),
             "边界：主跌第 1 天、冰点、退潮不推荐", "陈小群六阶段；科比四阶段", "bad" if blocked else "neutral"),
        _dim("emotion", "情绪", ("总龙头 " + "、".join(f"{r.get('name')}（{max_h} 板）" for r in leaders[:3])) if leaders else "没有 2 板以上的总龙头",
             "板块核心：" + ("；".join(f"{t['name']} → {(t.get('leader') or {}).get('name') or '—'}" for t in mains[:3]) or "—"),
             "「涨停板」tab 的龙 1 / 龙 2 标记", "Sleep「分清板块核心与市场核心」"),
        _dim("mainline", "主线", "、".join(f"{t['name']}（{t['zt_count']} 只）" for t in mains[:3]) or "暂无成型主线",
             "涨停数 + 2×连板数 + 最高板排名前 3 且 ≥3 只；盘中按热点题材三道门槛（够热、在升温、有新闻）",
             "资格：不在热点题材里不推荐", "陈小群 C13 / C14；科比 #23"),
        _dim("leader", "龙头", "；".join(
            f"{t['name']}：" + "、".join(f"{x['name']}（{x['boards']} 板）" for x in (t.get("members") or [])[:2]) for t in mains[:3]) or "—",
             "板数 → N 天 M 板 → 首封时间 → 人气榜", "龙 1 / 龙 2；跟风扣分", "科比 #17；陈小群「辨识度最高、带动性最强」"),
        _dim("catch_up", "补涨", "、".join(t["name"] for t in catch[:4]) or "无",
             "和近期主线共性相同（成员历史涨停原因 ≥50% 落在主线里）的非主线题材；补涨结束按科比 #6 两个征兆停推",
             "题材标签；补涨结束后该题材停推", "科比 #5 #6 #8"),
        _dim("switch", "切换", "、".join(t["name"] for t in switch[:4]) or "无",
             "和近期主线共性不同（≤20%）；只在节点日推荐：" + ("今天在时机上" if timing_ok(nodes, rules) else "今天不在时机上，新题材只观察"),
             "新题材资格与时机", "科比 #7 #8「完全不同的东西才是新」"),
        _dim("node", "节点", "、".join(nodes.get("labels") or []) or "无节点", node_text(nodes),
             "节点日标记；新题材时机；退潮期遇龙头见顶杀高位照常推荐", "Sleep「等待节点，抓住节点」；科比 #9 #14",
             "good" if nodes.get("top_killed") else "neutral"),
        _dim("expectation", "预期", "09:25 竞价后判断",
             "昨日涨停股按「板数 × 板质量」对历史开盘分位：低于 P25 = 低于预期，高于 P75 = 超预期；题材看多数，龙头当风向标",
             "标签；题材和龙头的预期判断", "科比 #23 #32；Sleep「分清符合、超、低于预期」"),
        _dim("weak_to_strong", "弱转强", f"明天的观察对象 {len(weak)} 只" + (("：" + "、".join(names.get(c, c) for c in weak[:5])) if weak else ""),
             "弱 = 炸板 / 开过板 / 14:30 后封板；强 = 今天被买上去（不是竞价高开）、扫板、板块配合",
             "计分：昨日炸板今天高开 2~5% +4；扫板、板块配合在盘中留档验证", "科比 #32~#34；陈小群 C04"),
        _dim("big_picture", "大局观", idx_text, summary or "模型解读未生成",
             "模型解读；指数急拉是盘中推翻信号", "Sleep「结合预期、催化、资金、情绪、板块合力判断个股阶段」"),
    ]
    return {
        "version": "wv-1",
        "for_date": for_date,
        "based_on": based_on,
        "dims": dims,
        "boundary_text": boundary,
        "blocked": bool(blocked and not node_pass),
        "nodes": nodes,
        "stage": stage.get("key"),
        "stage_label": stage.get("label"),
        "kobe_label": kobe.get("label"),
        "fall_day": fall_day,
        "main_themes": [t["name"] for t in mains[:3]],
        "themes": [
            {"key": t["key"], "name": t["name"], "rank": t.get("rank"), "zt_count": t.get("zt_count"),
             "is_main": bool(t.get("is_main")), "commonality": (theme_info.get(t["key"]) or {}).get("commonality"),
             "leaders": [{"code": x["code"], "name": x["name"], "boards": x.get("boards")} for x in (t.get("members") or [])[:2]],
             "first_boards": [x["code"] for x in t.get("members") or [] if int(x.get("boards") or 1) == 1][:20]}
            for t in (themes or [])[:8]
        ],
        "weak": weak[:60],
        "summary": summary,
    }


def live_worldview(base: Optional[Dict[str, Any]], *, boundary: Dict[str, Any], overrides: List[Dict[str, Any]],
                   rt: Optional[Dict[str, Any]], expectation: Optional[Dict[str, Any]],
                   indices: Optional[Dict[str, Dict[str, Any]]], changes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """盘中版：盘后版的结论加上盘中允许改动的三项（实时周期、推翻信号、停发）以及预期、大盘的实时值。"""
    view = dict(base or {"version": "wv-1", "dims": [], "nodes": boundary.get("nodes") or {}})
    dims = [dict(d) for d in view.get("dims") or []]
    by_key = {d["key"]: d for d in dims}
    rt = rt or {}
    if "cycle" in by_key and rt.get("active"):
        by_key["cycle"]["live"] = rt.get("reason") or ""
    if "node" in by_key and overrides:
        by_key["node"]["live"] = "；".join(o["label"] for o in overrides)
    if "expectation" in by_key and expectation and expectation.get("available"):
        c = expectation.get("counts") or {}
        by_key["expectation"]["value"] = f"超预期 {c.get('above', 0)} / 符合 {c.get('inline', 0)} / 低于 {c.get('below', 0)}"
        lead = [f"{t['name']}：{t['leader']['name']}{t['leader']['label']}" for t in (expectation.get("themes") or [])[:4]]
        by_key["expectation"]["live"] = "；".join(lead)
    if "big_picture" in by_key and indices:
        by_key["big_picture"]["live"] = "、".join(
            f"{v.get('name') or k} {v.get('change_pct'):+.2f}%" for k, v in indices.items() if v.get("change_pct") is not None)
    view["dims"] = dims
    view["boundary"] = boundary
    view["overrides"] = overrides
    view["changes"] = changes
    return view


# ---------------------------------------------------------------------- 次日对账


def reconcile(prev: Optional[Dict[str, Any]], *, metrics: Dict[str, Any], themes: List[Dict[str, Any]],
              zt_rows: List[Dict[str, Any]], previous: Optional[List[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    """用 ``for_date`` 当天的实际结果核对那一份世界观。每一项给 ok（True / False / None = 没法判）。"""
    if not prev or not prev.get("dims"):
        return None
    checks: List[Dict[str, Any]] = []
    premium = (metrics or {}).get("yzt_premium")
    stage = prev.get("stage")
    if premium is not None and stage:
        bullish = stage in ("warm", "ferment", "climax")
        ok = (premium > 0) if bullish else (premium <= 0)
        checks.append({"key": "cycle", "label": "周期", "ok": ok,
                       "detail": f"判{prev.get('stage_label')}，今天昨日涨停溢价 {premium:+.2f}%（陈小群：为正代表情绪延续）"})
    top3 = [t["name"] for t in (themes or [])[:3]]
    mains = prev.get("main_themes") or []
    if mains:
        hit = [m for m in mains if related_tags([m], top3)]
        checks.append({"key": "mainline", "label": "主线", "ok": len(hit) * 2 >= len(mains),
                       "detail": f"昨天主线 {'、'.join(mains)}；今天仍在前 3：{'、'.join(hit) or '无'}"})
    by_code = {r["code"]: r for r in zt_rows or []}
    leader_ok, leader_total = 0, 0
    for theme in prev.get("themes") or []:
        if not theme.get("is_main") or not theme.get("leaders"):
            continue
        lead = theme["leaders"][0]
        leader_total += 1
        today = [t for t in themes or [] if related_tags([t["name"]], [theme["name"]])]
        row = by_code.get(lead["code"])
        if row and today:
            top = (today[0].get("members") or [{}])[0]
            if top.get("code") == lead["code"] or int(row.get("boards") or 1) >= int(today[0].get("max_boards") or 0):
                leader_ok += 1
    if leader_total:
        checks.append({"key": "leader", "label": "龙头", "ok": leader_ok * 2 >= leader_total,
                       "detail": f"昨天主线龙 1 今天仍是题材最高板 / 最先封板：{leader_ok}/{leader_total}"})
    change = {r["code"]: r.get("change_pct") for r in previous or [] if r.get("change_pct") is not None}
    prev_counts = {t["key"]: int(t.get("zt_count") or 0) for t in prev.get("themes") or []}
    for kind, key, label in (("new", "switch", "切换"), ("same", "catch_up", "补涨")):
        marked = [t for t in prev.get("themes") or [] if (t.get("commonality") or {}).get("kind") == kind]
        if not marked:
            continue
        good = 0
        for theme in marked:
            today = [t for t in themes or [] if related_tags([t["name"]], [theme["name"]])]
            if kind == "new":
                good += int(bool(today) and int(today[0].get("zt_count") or 0) > prev_counts.get(theme["key"], 0))
            else:
                lows = [change[c] for c in theme.get("first_boards") or [] if c in change]
                good += int(bool(lows) and mean(lows) > 0)
        checks.append({"key": key, "label": label, "ok": good * 2 >= len(marked),
                       "detail": f"{'标为新题材的今天涨停数增加' if kind == 'new' else '标为补涨的低位股今天赚钱'}：{good}/{len(marked)}"})
    return {"for_date": prev.get("for_date"), "checks": checks}


def _pct(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:+.2f}%"


def _rate(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.0%}"


__all__ = [
    "COMMONALITY_LABELS", "NODE_LABELS", "NODE_SOURCES", "build_worldview", "candidate_gate", "catch_up_end",
    "decide_boundary", "detect_overrides", "expectation_key", "expectation_of", "expectation_summary",
    "live_worldview", "member_commonality", "money_effect", "node_flags", "node_text", "reconcile", "surge_point",
    "theme_members", "timing_ok",
]
