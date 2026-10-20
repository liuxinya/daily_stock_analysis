# -*- coding: utf-8 -*-
"""情绪周期：五大指标、六阶段透明投票、科比四阶段与「主跌第 N 天」。"""

from __future__ import annotations

from src.services.daban import cycle

from tests.daban_fixtures import metrics, prev_row, rules, zt_row


def _stage(m, prev=None):
    return cycle.classify_stage(m, prev_stage=prev, rules=rules())


class TestMetrics:
    def test_counts_height_and_premium(self):
        zt = [zt_row("600001", "甲", boards=3, seal_amount=2e8, circ_mv=40e8),
              zt_row("600002", "乙", boards=2), zt_row("600003", "丙")]
        zb = [zt_row("600010", "炸")]
        dt = [zt_row("600020", "跌")]
        previous = [prev_row("600001", "甲", y_boards=2, change_pct=10.0),
                    prev_row("600002", "乙", y_boards=1, change_pct=10.0),
                    prev_row("600030", "丁", y_boards=1, change_pct=-6.0),
                    prev_row("600031", "戊", y_boards=1, change_pct=2.0)]
        m = cycle.compute_metrics(zt=zt, zb=zb, dt=dt, previous=previous, rules=rules())
        assert m["zt_count"] == 3 and m["dt_count"] == 1
        assert m["zab_rate"] == 0.25
        assert m["lianban_count"] == 2 and m["max_height"] == 3 and m["second_height"] == 2
        assert m["leader_seal_ratio"] == 0.05
        assert m["promotion_rate"] == 0.5          # 2 连板 / 昨日 4 只涨停
        assert m["yzt_premium"] == 4.0             # (10+10-6+2)/4
        assert m["big_loss_count"] == 1            # -6% ≤ -5%
        tiers = {t["label"]: (t["base"], t["promoted"]) for t in m["tiers"]}
        assert tiers == {"1进2": (3, 1), "2进3": (1, 1)}

    def test_missing_previous_leaves_premium_unknown(self):
        m = cycle.compute_metrics(zt=[zt_row("600001", "甲")], zb=[], dt=[], previous=None, rules=rules())
        assert m["yzt_premium"] is None and m["promotion_rate"] is None and m["tiers"] == []

    def test_deltas_against_previous_day(self):
        m = metrics(zt_count=50, dt_count=4, max_height=5)
        cycle.attach_deltas(m, metrics(zt_count=40, dt_count=10, max_height=6))
        assert m["delta"]["zt_count"] == 10 and m["delta"]["dt_count"] == -6 and m["delta"]["max_height"] == -1
        cycle.attach_deltas(m, None)
        assert m["delta"]["zt_count"] is None


class TestStage:
    def test_ice(self):
        assert _stage(metrics(zt_count=20, lianban_count=2, yzt_premium=-3.0))["key"] == "ice"

    def test_warm(self):
        m = metrics(zt_count=40, lianban_count=6, delta={"promotion_rate": 0.05})
        assert _stage(m)["key"] == "warm"

    def test_ferment(self):
        assert _stage(metrics(zt_count=65, lianban_count=10, follow_rate=0.4))["key"] == "ferment"

    def test_climax(self):
        assert _stage(metrics(zt_count=95, max_height=7, leader_seal_ratio=0.05))["key"] == "climax"

    def test_diverge(self):
        m = metrics(zt_count=60, lianban_count=20, delta={"zt_count": -20, "max_height": -2, "big_loss_count": 5})
        assert _stage(m)["key"] == "diverge"

    def test_retreat(self):
        assert _stage(metrics(zt_count=35, dt_count=15, lianban_count=2, yzt_premium=-4.0))["key"] == "retreat"

    def test_more_than_twenty_limit_downs_forces_retreat(self):
        # 2026-10-19 实测：涨停 46、跌停 22，分歧期条件命中更多，但陈小群「跌停 >20 明确退潮」优先。
        m = metrics(zt_count=46, dt_count=22, lianban_count=11, max_height=3, yzt_premium=0.46,
                    delta={"zt_count": -13, "max_height": -8, "big_loss_count": 0})
        result = _stage(m)
        assert result["key"] == "retreat"
        assert result["override"]

    def test_every_condition_is_reported(self):
        result = _stage(metrics(zt_count=40))
        assert len(result["conditions"]) == 6
        for entry in result["conditions"]:
            assert all({"label", "value", "threshold", "hit"} <= set(item) for item in entry["items"])

    def test_missing_data_counts_as_miss_not_hit(self):
        result = _stage(metrics(zt_count=20, lianban_count=2))  # 昨日涨停表现缺失
        ice = next(e for e in result["conditions"] if e["stage"] == "ice")
        assert [i["hit"] for i in ice["items"]] == [True, True, None]

    def test_tie_prefers_the_stage_adjacent_to_yesterday(self):
        # 涨停 40 → 回暖 1/3；连板 10 → 发酵 1/3：平局。昨天是冰点，回暖离它更近。
        m = metrics(zt_count=40, lianban_count=10)
        assert _stage(m, prev="ice")["key"] == "warm"
        assert _stage(m, prev="climax")["key"] == "ferment"

    def test_transitional_when_best_hit_rate_is_low(self):
        m = metrics(zt_count=40, lianban_count=10)
        result = _stage(m, prev="ice")
        assert result["transitional"] and result["secondary"] == "ferment"

    def test_tie_without_yesterday_is_conservative(self):
        # 涨停 85（高潮 1/3）但连板 0（冰点、退潮各 1/3）：三方平局、没有昨日阶段可参照时，
        # 先认亏钱的阶段——宁可错过，不在看不清时给重仓。
        result = _stage(metrics(zt_count=85, max_height=2))
        assert result["key"] == "retreat" and not result["fallback"]
        assert _stage(metrics(zt_count=85, max_height=2), prev="ferment")["key"] == "climax"


class TestKobePhase:
    def test_maps_six_to_four(self):
        assert cycle.kobe_phase("ferment", metrics(), [])["key"] == "main_rise"
        assert cycle.kobe_phase("diverge", metrics(), [])["key"] == "high_shock"
        assert cycle.kobe_phase("warm", metrics(), [])["key"] == "low_shock"

    def test_counts_consecutive_fall_days(self):
        history = [
            {"stage": "ferment", "yzt_premium": 2.0},
            {"stage": "retreat", "yzt_premium": -3.0},
            {"stage": "retreat", "yzt_premium": -1.0},
        ]
        phase = cycle.kobe_phase("retreat", metrics(yzt_premium=-2.0), history)
        assert phase["fall_day"] == 3 and phase["gate_key"] == "fall_3"
        phase = cycle.kobe_phase("retreat", metrics(yzt_premium=-2.0), history[:2])
        assert phase["fall_day"] == 2 and phase["gate_key"] == "fall_2"

    def test_retreat_with_positive_premium_is_not_a_fall_day(self):
        phase = cycle.kobe_phase("retreat", metrics(yzt_premium=0.5), [])
        assert phase["fall_day"] == 0 and phase["key"] == "high_shock"
