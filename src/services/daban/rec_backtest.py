# -*- coding: utf-8 -*-
"""推荐评分的回测、月度重标与盘中因子评估（``scripts/daban_backtest.py --mode recommend / intraday``）。

- **回放**：同一份 ``rec_features``，模拟「盘中拉升到距涨停 ``gap``% 时按现价买入」，H1 卖出。只用 D-1 收盘前的
  数据加 D 日开盘价（不前视）。数据来自 ``data/daban_backtest`` 的缓存（``run_backtest`` 拉的那一份）。
- **月度重标**：按入选条件（样本 ≥200、覆盖 ≥50 个交易日、前后半段同号、|超额| ≥0.3 个百分点）重算计分表，
  和当前规则逐项对比，输出「建议变更清单」；观察期的 4 种一进二情形用实盘成绩复核。**只出建议，不改规则**，
  变更由用户确认后写进 ``DABAN_RULES_FILE``。
- **盘中因子**：用 ``daban_radar_events`` 的留档按同样的入选条件（样本 ≥300、覆盖 ≥20 个交易日）评估第三层因子，
  样本不够时如实报「样本不足」。
"""

from __future__ import annotations

import json
import logging
import math
import os
from statistics import mean, pstdev
from typing import Any, Dict, List, Optional, Tuple

from src.services.daban import cycle as cycle_mod
from src.services.daban import rec_features as rf
from src.services.daban.constants import load_rules
from src.services.daban.kline import limit_pct

logger = logging.getLogger(__name__)

SHRINK = 200
HORIZON = 6


# ---------------------------------------------------------------------- 数据


def load_cache(data_dir: str) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    pools: Dict[str, Dict[str, Any]] = {}
    pdir = os.path.join(data_dir, "pools")
    for name in sorted(os.listdir(pdir)):
        if name.endswith(".json"):
            with open(os.path.join(pdir, name), encoding="utf-8") as fh:
                pools[name[:-5]] = json.load(fh)
    klines: Dict[str, Dict[str, Any]] = {}
    kdir = os.path.join(data_dir, "klines")
    for name in os.listdir(kdir):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(kdir, name), encoding="utf-8") as fh:
            bars = rf.make_bars(json.load(fh) or [])
        if bars and len(bars["t"]) >= 30:
            klines[name[:-5]] = bars
    return pools, klines


def _universe(rows) -> List[Dict[str, Any]]:
    from data_provider.limit_up_fetcher import in_universe

    return [dict(r) for r in rows or [] if in_universe(r.get("code"), r.get("name")) and not r.get("is_new")]


def _change(klines, code: str, ymd: str) -> Optional[float]:
    k = klines.get(code)
    i = k["ix"].get(ymd) if k else None
    return rf.pct(k["c"][i], k["c"][i - 1]) if i else None


def _days_before(day: str, n: int) -> str:
    from datetime import datetime, timedelta

    return (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=n)).strftime("%Y-%m-%d")


def build_contexts(pools, klines, rules) -> Tuple[List[str], Dict[str, Dict[str, Any]]]:
    """逐日算「这一天收盘后」的上下文：题材（给下一天当昨日题材）、周期、节点（给下一天用）。"""
    from src.services.daban.worldview import node_flags

    days = sorted(pools)
    ctxs: Dict[str, Dict[str, Any]] = {}
    history: List[Tuple[str, List[Dict[str, Any]]]] = []
    mains_history: List[List[str]] = []
    stage_history: List[Dict[str, Any]] = []
    prev_metrics, prev_stage = None, None
    for i, day in enumerate(days):
        p = pools[day]
        ladder = p.get("ladder") or {}
        zt = _universe(p.get("zt"))
        for row in zt:
            row["boards"] = int(ladder.get(row["code"], 1))
        zb, dt = _universe(p.get("zb")), _universe(p.get("dt"))
        previous, yesterday_rows = None, []
        if i > 0:
            yesterday_rows = ctxs[days[i - 1]]["zt_rows"]
            previous = []
            for row in yesterday_rows:
                chg = _change(klines, row["code"], day.replace("-", ""))
                if chg is not None:
                    previous.append({**row, "change_pct": chg, "y_boards": row.get("boards")})
        recent = [n for names in mains_history[-10:] for n in names]
        ctx = rf.yesterday_context(zt_rows=zt, zb_rows=zb, history=history[-10:], yesterday_rows=yesterday_rows,
                                   previous=previous, recent_mains=recent, rules=rules)
        metrics = cycle_mod.compute_metrics(zt=zt, zb=zb, dt=dt, previous=previous, rules=rules)
        top = ctx["themes"][0] if ctx["themes"] else None
        metrics["follow_rate"] = round(top["zt_count"] / len(zt), 4) if (top and zt) else None
        cycle_mod.attach_deltas(metrics, prev_metrics)
        stage = cycle_mod.classify_stage(metrics, prev_stage=prev_stage, rules=rules)
        kobe = cycle_mod.kobe_phase(stage["key"], metrics, stage_history)
        ctx.update({"zt_rows": zt, "stage": stage["key"], "fall_day": int(kobe.get("fall_day") or 0)})
        # 节点：给下一天用（D-2 = 前一天，D-1 = 今天）
        if i > 0:
            ctx["nodes_next"] = node_flags(
                d2_rows=ctxs[days[i - 1]]["zt_rows"], d1_rows=zt,
                d1_change={r["code"]: r["change_pct"] for r in previous or []},
                fall_day_d1=ctx["fall_day"],
            )
        else:
            ctx["nodes_next"] = {}
        ctxs[day] = ctx
        history.append((day, zt))
        stage_history.append({"stage": stage["key"], "yzt_premium": metrics.get("yzt_premium")})
        mains_history.append([t["name"] for t in ctx["themes"] if t.get("is_main")])
        prev_metrics, prev_stage = metrics, stage["key"]
    return days, ctxs


def replay(pools, klines, *, rules: Optional[Dict[str, Any]] = None, gap: float = 6.0,
           warmup: int = 40) -> List[Dict[str, Any]]:
    """一年回放 → 事件列表。每个事件带 8 个维度的格子、总分、H1 / F1 / F2、前后半段标记和当天的世界观边界。"""
    rules = rules or load_rules(None)
    table = rules.get("rec_score") or {}
    days, ctxs = build_contexts(pools, klines, rules)
    tdays = [d.replace("-", "") for d in days]
    half = days[(len(days) + warmup) // 2]
    events: List[Dict[str, Any]] = []
    eligible: set = set()
    memory: Dict[str, List[Tuple[str, str]]] = {}
    for i, day in enumerate(days):
        if warmup <= i < len(days) - HORIZON:
            prev = ctxs[days[i - 1]]
            td = tdays[i]
            for code in eligible:
                k = klines.get(code)
                idx = k["ix"].get(td) if k else None
                if not idx or idx + HORIZON >= len(k["t"]) or any(
                        k["t"][idx + m] != tdays[i + m] for m in range(-1, HORIZON + 1)):
                    continue
                lp = limit_pct(code)
                c0, o, h = k["c"][idx - 1], k["o"][idx], k["h"][idx]
                if (rf.pct(o, c0) or 0) >= lp - rf.TOL:
                    continue  # 开盘即涨停：买不到
                lim = c0 * (1 + lp / 100)
                trig = lim / (1 + gap / 100)
                if h < trig:
                    continue
                cost = o if o >= trig else trig
                tags = rf.memory_tags(r for _, r in memory.get(code, []))
                leader = None
                base = rf.base_features(code=code, k=k, idx=idx, ctx=prev, tags=tags)
                if base.get("theme") and base["theme"].get("leader_code") not in (None, code):
                    leader = klines.get(base["theme"]["leader_code"])
                    if leader is not None:
                        base = rf.base_features(code=code, k=k, idx=idx, ctx=prev, tags=tags, leader_bars=leader)
                cells = rf.full_cells(base, today_open=o, ref_close=c0, limit_up=lim, gap_max=gap)
                scored = rf.score_cells(cells, table)
                outcome = rf.exit_returns(k, idx, cost, lp)
                events.append({
                    "date": day, "code": code, "split": "前半段" if day < half else "后半段",
                    "cells": cells, "score": scored["total"], "observe": (scored["observe"] or {}).get("key"),
                    "sealed": outcome["sealed"], "h1": outcome["h1"], "f1": outcome["f1"], "f2": outcome["f2"],
                    "temper": base["temper"]["tag"], "y_boards": base["y_boards"],
                    "stage": prev["stage"], "fall_day": prev["fall_day"], "nodes": prev.get("nodes_next") or {},
                })
        for row in ctxs[day]["zt_rows"]:
            memory.setdefault(row["code"], []).append((day, row.get("reason") or ""))
        cutoff = _days_before(day, 90)
        for code in list(memory):
            memory[code] = [x for x in memory[code] if x[0] >= cutoff]
        eligible |= set(ctxs[day]["zt"]) | ctxs[day]["zb"]
    return [e for e in events if e["h1"] is not None]


# ---------------------------------------------------------------------- 标定


def _cells_table(events: List[Dict[str, Any]], *, strict: bool, base: Optional[float] = None) -> Dict[Tuple[str, str], Dict[str, Any]]:
    base = mean(e["h1"] for e in events) if base is None else base
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for dim in rf.DIMS:
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for e in events:
            groups.setdefault(e["cells"].get(dim, "无数据"), []).append(e)
        for cell, es in groups.items():
            xs = [e["h1"] - base for e in es]
            raw = mean(xs)
            halves = []
            for s in ("前半段", "后半段"):
                part = [e["h1"] for e in es if e["split"] == s]
                halves.append(round(mean(part) - base, 2) if len(part) >= 30 else None)
            n_days = len({e["date"] for e in es})
            if strict:
                ok = (len(es) >= 200 and n_days >= 50 and abs(raw) >= 0.3 and None not in halves
                      and all((v > 0) == (raw > 0) for v in halves))
            else:
                ok = len(es) >= 150 and n_days >= 30 and abs(raw) >= 0.3
            out[(dim, cell)] = {"dim": dim, "cell": cell, "n": len(es), "days": n_days, "excess": round(raw, 2),
                                "halves": halves, "points": max(-20, min(20, round(sum(xs) / (len(xs) + SHRINK) * 10))),
                                "selected": ok, "win": round(mean(e["h1"] > 0 for e in es), 4)}
    return out


def _observe_keys(table: Dict[str, Any]) -> set:
    return {(o.get("dim"), o.get("cell")) for o in table.get("observe") or []}


def _score(e: Dict[str, Any], points: Dict[Tuple[str, str], int]) -> int:
    return sum(points.get((dim, e["cells"].get(dim)), 0) for dim in rf.DIMS)


def _line(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {"n": 0}
    by_day: Dict[str, List[float]] = {}
    for e in rows:
        by_day.setdefault(e["date"], []).append(e["h1"])
    return {"n": len(rows), "days": len(by_day), "h1_mean": round(mean(e["h1"] for e in rows), 3),
            "win": round(mean(e["h1"] > 0 for e in rows), 4), "day_mean": round(mean(mean(v) for v in by_day.values()), 3),
            "seal": round(mean(bool(e["sealed"]) for e in rows), 4)}


# 定稿之后才发现、还没进计分表的候选项：每次月度重标都在「总分 ≥ 门槛」的推荐里复核一次，满足入选条件也只进建议清单。
CANDIDATE_ITEMS: List[Dict[str, Any]] = [
    {"key": "temper_active", "label": "股性活跃（近一年冲板封住率 ≥ 35%）·10cm",
     "source": "系统补充（世界观草案第三节，事后发现，先过一次重标再用）",
     "scope": lambda e: str(e["cells"].get(rf.DIM_PRICE) or "").startswith("10cm"),
     "match": lambda e: e.get("temper") == "active"},
]


def _candidate_stats(events: List[Dict[str, Any]], threshold: int) -> List[Dict[str, Any]]:
    out = []
    for item in CANDIDATE_ITEMS:
        pool = [e for e in events if e["score"] >= threshold and item["scope"](e)]
        hit = [e for e in pool if item["match"](e)]
        if not pool or not hit:
            out.append({"key": item["key"], "label": item["label"], "source": item["source"], "n": len(hit), "selected": False})
            continue
        base = mean(e["h1"] for e in pool)
        xs = [e["h1"] - base for e in hit]
        raw = mean(xs)
        halves = []
        for split in ("前半段", "后半段"):
            part = [e["h1"] for e in hit if e["split"] == split]
            halves.append(round(mean(part) - base, 2) if len(part) >= 30 else None)
        days = len({e["date"] for e in hit})
        ok = (len(hit) >= 200 and days >= 50 and abs(raw) >= 0.3 and None not in halves
              and all((v > 0) == (raw > 0) for v in halves))
        out.append({"key": item["key"], "label": item["label"], "source": item["source"], "n": len(hit), "days": days,
                    "excess": round(raw, 2), "halves": halves, "selected": ok,
                    "points": max(-20, min(20, round(sum(xs) / (len(xs) + SHRINK) * 10)))})
    return out


def recalibrate(events: List[Dict[str, Any]], rules: Dict[str, Any]) -> Dict[str, Any]:
    """按入选条件重算计分表，与当前规则逐项对比，并做一次样本外门槛检验。观察期情形不进表。"""
    table = rules.get("rec_score") or {}
    threshold = int(table.get("threshold", 0))
    observe = _observe_keys(table)
    current = {(i["dim"], i["cell"]): int(i["points"]) for i in table.get("items") or []}
    cells = _cells_table(events, strict=True)
    proposed = {key: c["points"] for key, c in cells.items() if c["selected"] and key not in observe}
    changes: List[Dict[str, Any]] = []
    for key in sorted(set(current) | set(proposed)):
        cur, new = current.get(key), proposed.get(key)
        if cur == new:
            continue
        stat = cells.get(key) or {}
        kind = "add" if cur is None else ("remove" if new is None else "change")
        changes.append({"kind": kind, "dim": key[0], "cell": key[1], "current": cur, "proposed": new,
                        "n": stat.get("n"), "days": stat.get("days"), "excess": stat.get("excess"),
                        "halves": stat.get("halves")})
    train = [e for e in events if e["split"] == "前半段"]
    test = [e for e in events if e["split"] == "后半段"]
    fit = {key: c["points"] for key, c in _cells_table(train, strict=False).items()
           if c["selected"] and key not in observe}
    oos = {
        "keep": _line([e for e in test if _score(e, fit) >= threshold]),
        "drop": _line([e for e in test if _score(e, fit) < threshold]),
    }
    current_split = {
        "keep": _line([e for e in events if _score(e, current) >= threshold]),
        "drop": _line([e for e in events if _score(e, current) < threshold]),
    }
    observe_stats = []
    for item in table.get("observe") or []:
        part = [e for e in events if e["cells"].get(item["dim"]) == item["cell"]]
        stat = cells.get((item["dim"], item["cell"])) or {}
        observe_stats.append({"key": item.get("key"), "label": item.get("label"), **_line(part),
                              "excess": stat.get("excess"), "halves": stat.get("halves")})
    return {"baseline": _line(events), "changes": changes, "oos": oos, "current_split": current_split,
            "observe": observe_stats, "candidates": _candidate_stats(events, threshold),
            "range": [events[0]["date"], events[-1]["date"]] if events else []}


def review_observe_live(live_rows: List[Dict[str, Any]], *, min_n: int = 30, min_days: int = 10) -> Dict[str, Any]:
    """观察期复核：实盘里观察组和其余推荐比 H1。方向和回测一致（观察组更差）就建议按回测扣分。"""
    done = [r for r in live_rows if r.get("h1_ret") is not None]
    obs = [r for r in done if r.get("group_key") == "observe"]
    rest = [r for r in done if r.get("group_key") != "observe"]

    def _stat(rows):
        if not rows:
            return {"n": 0}
        return {"n": len(rows), "days": len({r["trade_date"] for r in rows}),
                "h1_mean": round(mean(float(r["h1_ret"]) for r in rows), 3),
                "win": round(mean(float(r["h1_ret"]) > 0 for r in rows), 4)}

    out = {"observe": _stat(obs), "rest": _stat(rest), "verdict": "insufficient"}
    if len(obs) >= min_n and out["observe"].get("days", 0) >= min_days and rest:
        worse = out["observe"]["h1_mean"] < out["rest"]["h1_mean"] and out["observe"]["win"] < out["rest"]["win"]
        out["verdict"] = "deduct" if worse else "keep"
    return out


def render_recommend_md(report: Dict[str, Any]) -> str:
    def _fmt(line: Dict[str, Any]) -> str:
        if not line.get("n"):
            return "无样本"
        return (f"{line['n']} 次 / {line.get('days', 0)} 天，H1 平均 {line['h1_mean']:+.2f}%，胜率 {line['win']:.1%}，"
                f"按天平均 {line.get('day_mean', 0):+.2f}%")

    lines = ["# 打板王推荐计分表月度重标", "",
             f"回测区间 {' ~ '.join(report.get('range') or ['—'])}；基准：{_fmt(report.get('baseline') or {})}", "",
             "## 当前计分表的效果（全年，样本内）", "",
             f"- 推荐（总分 ≥ 门槛）：{_fmt(report['current_split']['keep'])}",
             f"- 挡掉：{_fmt(report['current_split']['drop'])}", "",
             "## 样本外（前半段定分、后半段检验）", "",
             f"- 推荐：{_fmt(report['oos']['keep'])}", f"- 挡掉：{_fmt(report['oos']['drop'])}", "",
             "## 建议变更清单（由你确认后才改）", ""]
    if not report["changes"]:
        lines.append("- 无：当前计分表仍满足入选条件")
    for c in report["changes"]:
        verb = {"add": "新增", "remove": "去掉", "change": "改分"}[c["kind"]]
        lines.append(f"- {verb}「{c['dim']}：{c['cell']}」：{c['current']} → {c['proposed']}（样本 {c['n']} / {c['days']} 天，"
                     f"超额 {c['excess']}，前/后半段 {c['halves']}）")
    lines += ["", "## 候选计分项（定稿后发现，在推荐里复核；满足入选条件也由你确认后才加）", ""]
    for c in report.get("candidates") or []:
        if not c.get("n"):
            lines.append(f"- {c['label']}：无样本")
            continue
        verdict = f"满足入选条件，建议 {c['points']:+d} 分" if c.get("selected") else "未满足入选条件"
        lines.append(f"- {c['label']}：{c['n']} 次 / {c.get('days', 0)} 天，超额 {c.get('excess')}，"
                     f"前/后半段 {c.get('halves')} → {verdict}（{c['source']}）")
    lines += ["", "## 观察期复核（4 种一进二情形）", ""]
    for item in report.get("observe") or []:
        lines.append(f"- {item['label']}：回测 {_fmt(item)}")
    live = report.get("observe_live") or {}
    if live:
        verdict = {"insufficient": "实盘样本不足，继续观察", "deduct": "实盘与回测方向一致（观察组更差）：建议按回测扣分",
                   "keep": "实盘与回测方向相反：建议继续保留"}[live.get("verdict", "insufficient")]
        lines.append(f"- 实盘：观察组 {live.get('observe')}；其余推荐 {live.get('rest')} → {verdict}")
    return "\n".join(lines) + "\n"


def run_recommend(data_dir: str, *, rules: Optional[Dict[str, Any]] = None, live_rows: Optional[List[Dict[str, Any]]] = None,
                  out_dir: Optional[str] = None) -> Dict[str, Any]:
    rules = rules or load_rules(None)
    pools, klines = load_cache(data_dir)
    events = replay(pools, klines, rules=rules, gap=float((rules.get("rec_score") or {}).get("gap_max", 6.0)))
    report = recalibrate(events, rules)
    report["observe_live"] = review_observe_live(live_rows or [])
    report["events"] = len(events)
    out_dir = out_dir or os.path.join(data_dir, "report")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "recommend.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
    with open(os.path.join(out_dir, "recommend.md"), "w", encoding="utf-8") as fh:
        fh.write(render_recommend_md(report))
    return report


# ---------------------------------------------------------------------- 盘中因子（第三层）


INTRADAY_FACTORS = {
    "量比": ("volume_ratio", (1, 2, 3, 5, 1e9), ["<1", "1-2", "2-3", "3-5", "≥5"]),
    "换手率": ("turnover_rate", (3, 8, 15, 30, 1e9), ["<3%", "3-8%", "8-15%", "15-30%", "≥30%"]),
    "今开幅度": ("open_pct", (0, 2, 5, 1e9), ["低开", "高开 0-2%", "高开 2-5%", "高开 ≥5%"]),
}


def _time_band(at: str) -> str:
    return rf.band(float(str(at or "0")[:4] or 0), (1000, 1100, 1330, 1430, 1e9),
                   ["10:00 前", "10:00-11:00", "11:00-13:30", "13:30-14:30", "14:30 后"])


def evaluate_intraday(seen: List[Dict[str, Any]], outcomes: Dict[Tuple[str, str], float], *,
                      min_n: int = 300, min_days: int = 20) -> Dict[str, Any]:
    """``seen``：留档里每只候选第一次进入视野（热点）的那一行；``outcomes``：(日期, 代码) → 按那一刻现价买入的 H1。"""
    rows = []
    for ev in seen:
        key = (ev["trade_date"], ev["code"])
        if key not in outcomes:
            continue
        data = ev.get("data") or {}
        row = {"date": ev["trade_date"], "h1": outcomes[key], "cells": {"触发时段": _time_band(ev.get("at"))}}
        for name, (field, edges, labels) in INTRADAY_FACTORS.items():
            row["cells"][name] = rf.band(data.get(field), edges, labels)
        rows.append(row)
    days = sorted({r["date"] for r in rows})
    out: Dict[str, Any] = {"samples": len(rows), "days": len(days), "min_n": min_n, "min_days": min_days,
                           "factors": [], "status": "insufficient"}
    if len(rows) < min_n or len(days) < min_days:
        return out
    half = days[len(days) // 2]
    base = mean(r["h1"] for r in rows)
    out["status"] = "ok"
    for name in ["触发时段", *INTRADAY_FACTORS]:
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            groups.setdefault(r["cells"][name], []).append(r)
        for cell, part in sorted(groups.items()):
            raw = mean(r["h1"] for r in part) - base
            halves = [mean(r["h1"] for r in part if (r["date"] < half) == first) - base
                      if sum((r["date"] < half) == first for r in part) >= 30 else None for first in (True, False)]
            n_days = len({r["date"] for r in part})
            ok = (len(part) >= min_n and n_days >= min_days and abs(raw) >= 0.3 and None not in halves
                  and all((v > 0) == (raw > 0) for v in halves))
            out["factors"].append({"factor": name, "cell": cell, "n": len(part), "days": n_days, "excess": round(raw, 2),
                                   "halves": [None if v is None else round(v, 2) for v in halves], "candidate": ok,
                                   "points": max(-20, min(20, round((raw * len(part)) / (len(part) + SHRINK) * 10)))})
    return out


def day_stats(values: List[float]) -> Dict[str, Any]:
    if not values:
        return {"days": 0}
    return {"days": len(values), "mean": round(mean(values), 3),
            "se": round(pstdev(values) / math.sqrt(len(values)), 3) if len(values) > 1 else None}


__all__ = [
    "INTRADAY_FACTORS", "build_contexts", "day_stats", "evaluate_intraday", "load_cache", "recalibrate",
    "render_recommend_md", "replay", "review_observe_live", "run_recommend",
]
