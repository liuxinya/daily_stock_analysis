# -*- coding: utf-8 -*-
"""月度重标：入选条件、建议变更清单、候选计分项、观察期复核、第三层盘中因子的样本门槛。"""

from __future__ import annotations

from src.services.daban import rec_backtest as rb
from src.services.daban import rec_features as rf


def _events(n_days=250, per_day=6, *, hot_cell="≥3 倍", hot_ret=-2.0, temper_every=0):
    """``per_day`` 只/天；每天第一只落在 ``hot_cell``（量比格子），H1 = ``hot_ret``，其余 H1 = +0.5。"""
    out = []
    for d in range(n_days):
        day = f"2026{(d // 28) + 1:02d}{(d % 28) + 1:02d}"
        for i in range(per_day):
            hot = i == 0
            out.append({
                "date": day, "code": f"6{d:03d}{i:02d}", "split": "前半段" if d < n_days // 2 else "后半段",
                "cells": {rf.DIM_VOLUME: hot_cell if hot else "1-2 倍", rf.DIM_PRICE: "10cm｜10-20元"},
                "score": 0, "observe": None, "sealed": False, "h1": hot_ret if hot else 0.5,
                "temper": "active" if (temper_every and i == 1 and d % temper_every == 0) else "normal",
            })
    return out


def test_recalibrate_proposes_a_strong_cell_and_keeps_rules_untouched():
    rules = {"rec_score": {"threshold": 0, "items": [], "observe": []}}
    report = rb.recalibrate(_events(), rules)
    change = next(c for c in report["changes"] if c["cell"] == "≥3 倍")
    assert change["kind"] == "add" and change["proposed"] < 0 and change["current"] is None
    assert rules["rec_score"]["items"] == []            # 只出建议，不改规则
    md = rb.render_recommend_md(report)
    assert "建议变更清单" in md and "候选计分项" in md


def test_recalibrate_reports_no_change_when_table_matches():
    rules = {"rec_score": {"threshold": 0, "items": [], "observe": []}}
    proposed = next(c for c in rb.recalibrate(_events(), rules)["changes"] if c["cell"] == "≥3 倍")
    rules["rec_score"]["items"] = [{"dim": rf.DIM_VOLUME, "cell": "≥3 倍", "points": proposed["proposed"]}]
    assert [c for c in rb.recalibrate(_events(), rules)["changes"] if c["cell"] == "≥3 倍"] == []


def test_candidate_item_needs_samples_and_same_sign_halves():
    few = rb._candidate_stats(_events(temper_every=10), threshold=0)[0]
    assert few["key"] == "temper_active" and few["n"] == 25 and few["selected"] is False
    many = _events(n_days=300, temper_every=1)
    for e in many:
        if e["temper"] == "active":
            e["h1"] = -1.0
    stat = rb._candidate_stats(many, threshold=0)[0]
    assert stat["n"] == 300 and stat["selected"] is True and stat["points"] < 0


def test_observe_review_verdicts():
    def rows(group, h1, n, days):
        return [{"trade_date": f"d{i % days}", "group_key": group, "h1_ret": h1} for i in range(n)]

    assert rb.review_observe_live(rows("observe", -1.0, 10, 5))["verdict"] == "insufficient"
    worse = rows("observe", -1.0, 40, 12) + rows("normal", 1.0, 40, 12)
    assert rb.review_observe_live(worse)["verdict"] == "deduct"
    better = rows("observe", 2.0, 40, 12) + rows("normal", 1.0, 40, 12)
    assert rb.review_observe_live(better)["verdict"] == "keep"


def test_intraday_factors_wait_for_enough_days():
    seen = [{"trade_date": f"2026-12-{d:02d}", "code": f"6000{i:02d}", "at": "100500", "data": {"volume_ratio": 4}}
            for d in range(1, 11) for i in range(40)]
    outcomes = {(s["trade_date"], s["code"]): 1.0 for s in seen}
    out = rb.evaluate_intraday(seen, outcomes)
    assert out["status"] == "insufficient" and out["samples"] == 400 and out["days"] == 10
