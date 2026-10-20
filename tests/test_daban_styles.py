# -*- coding: utf-8 -*-
"""两条打法线的门控、仓位与「今日更推荐哪种打法」的裁决。"""

from __future__ import annotations

from src.services.daban import styles

from tests.daban_fixtures import metrics, rules


def _cand(code, mode="M4", *, score=80.0, role="front", signals=None, conditions=None, origin="zt"):
    return {
        "code": code, "name": f"股{code}", "mode": mode, "mode_label": mode, "role": role, "role_label": role,
        "theme": "机器人", "theme_name": "机器人", "theme_is_main": True, "origin": origin,
        "score": score, "grade": "A", "rule_score": score, "llm_adjust": 0, "risk_penalty": 0,
        "dimensions": [], "risks": [], "facts": [], "notes": [],
        "signals": signals if signals is not None else [
            {"text": "两派都认的理由", "principle_ids": ["K11", "C13"]},
        ],
        "triggers": [{"text": "科比触发", "principle_ids": ["K12"]}, {"text": "小群触发", "principle_ids": ["C12"]}],
        "abandon": [], "conditions": conditions or {},
    }


def _stage(key):
    return {"key": key, "label": key}


def _kobe(gate_key, fall_day=0):
    return {"key": gate_key, "label": gate_key, "gate_key": gate_key, "fall_day": fall_day}


def _lane(style, candidates, stage="ferment", kobe="main_rise", fall_day=0, max_actionable=4):
    return styles.build_lane(style, candidates, stage=_stage(stage), kobe=_kobe(kobe, fall_day),
                             rules=rules(), max_actionable=max_actionable)


class TestLanes:
    def test_chen_is_flat_in_retreat_while_kobe_trials_on_fall_day_two(self):
        cands = [_cand("600001", "M1", role="total_leader",
                       signals=[{"text": "龙头", "principle_ids": ["K09", "C05"]}])]
        chen = _lane("chen", cands, stage="retreat", kobe="fall_2", fall_day=2)
        kobe = _lane("kobe", cands, stage="retreat", kobe="fall_2", fall_day=2)
        assert chen["flat"] and chen["position_cap"] == 0
        assert "空仓" in chen["flat_message"]
        assert not chen["actionable"] and not chen["trial"]
        assert kobe["trial"] and kobe["trial"][0]["code"] == "600001"
        assert kobe["position_cap"] == 10

    def test_reasons_keep_only_the_lanes_own_principles(self):
        cand = _cand("600001")
        chen = _lane("chen", [cand])
        kobe = _lane("kobe", [cand])
        chen_ids = {pid for r in chen["actionable"][0]["reasons"] for pid in r["principle_ids"]}
        kobe_ids = {pid for r in kobe["actionable"][0]["reasons"] for pid in r["principle_ids"]}
        assert chen_ids == {"C13"} and kobe_ids == {"K11"}
        assert [t["text"] for t in chen["actionable"][0]["triggers"]] == ["小群触发"]

    def test_candidate_without_any_chen_principle_does_not_enter_chen_lane(self):
        cand = _cand("600001", signals=[{"text": "只有科比", "principle_ids": ["K10"]}])
        assert not _lane("chen", [cand])["actionable"]
        assert _lane("kobe", [cand])["actionable"]

    def test_actionable_is_capped_at_four_picks(self):
        cands = [_cand(f"60000{i}", score=90 - i) for i in range(6)]
        lane = _lane("kobe", cands)
        assert len(lane["actionable"]) == 4
        assert all(p["position_pct"] <= 25 for p in lane["actionable"])
        assert lane["position_used"] <= 100

    def test_lane_position_cap_demotes_overflow_to_watch(self):
        cands = [_cand(f"60000{i}", "M1", role="theme_leader", score=90 - i) for i in range(3)]
        lane = _lane("chen", cands)      # 发酵期上限 50%，龙头单票 30%
        assert lane["position_used"] <= 50
        assert any("仓位上限" in note for p in lane["watch"] for note in p["notes"])

    def test_low_score_only_watches(self):
        lane = _lane("kobe", [_cand("600001", score=60)])
        assert not lane["actionable"] and lane["watch"][0]["action"] == "watch"

    def test_conditional_gate_falls_back_to_watch(self):
        # 陈小群分歧期的龙头只做「全市场绝对龙头」低吸。
        cand = _cand("600001", "M1", role="theme_leader", conditions={"total_leader": False},
                     signals=[{"text": "x", "principle_ids": ["C05"]}])
        lane = _lane("chen", [cand], stage="diverge")
        assert lane["watch"] and "绝对龙头" in lane["watch"][0]["notes"][0]

    def test_same_stock_keeps_best_mode_and_lists_others(self):
        lane = _lane("kobe", [_cand("600001", "M4", score=70), _cand("600001", "M2", score=82)])
        assert len(lane["actionable"]) == 1
        pick = lane["actionable"][0]
        assert pick["mode"] == "M2" and pick["also_modes"] == ["M4"]

    def test_total_leader_is_pinned_in_watch(self):
        # 科比低位震荡期：龙头只观察；其余首板分数 < 65 也进观察。总龙头分数最低也要排第一。
        cands = [_cand(f"60000{i}", score=60 + i) for i in range(5)]
        cands.append(_cand("600009", "M1", role="total_leader", score=40,
                           signals=[{"text": "x", "principle_ids": ["C13", "K22"]}]))
        lane = _lane("kobe", cands, stage="warm", kobe="low_shock")
        assert lane["watch"][0]["code"] == "600009"

    def test_consensus_marks_shared_picks(self):
        cand = _cand("600001")
        lanes = {"chen": _lane("chen", [cand]), "kobe": _lane("kobe", [cand])}
        assert styles.mark_consensus(lanes) == ["600001"]
        assert lanes["chen"]["actionable"][0]["consensus"]


def _verdict(*, stage="ferment", kobe=None, m=None, themes=None, candidates=(), lanes=None, flags=None, track=None):
    lanes = lanes or {"chen": _lane("chen", []), "kobe": _lane("kobe", [])}
    return styles.decide_style(
        metrics=m or metrics(), stage=_stage(stage), kobe=kobe or _kobe("main_rise"),
        themes=themes or [], candidates=list(candidates), lanes=lanes, flags=flags or {},
        track_record=track, rules=rules(),
    )


class TestVerdict:
    def _vote(self, verdict, key):
        return next(v for v in verdict["signals"] if v["key"] == key)

    def test_retreat_with_spreading_losses_favours_chen_vote(self):
        v = _verdict(stage="retreat", m=metrics(delta={"dt_count": 5}))
        assert self._vote(v, "c1")["hit"] and not self._vote(v, "c4")["hit"]

    def test_fall_day_two_with_easing_favours_kobe(self):
        v = _verdict(stage="retreat", kobe=_kobe("fall_2", 2), m=metrics(delta={"dt_count": -4, "yzt_premium": 1.0}))
        assert self._vote(v, "k1")["hit"]

    def test_mainline_confirmed(self):
        theme = {"name": "机器人", "zt_count": 21, "max_boards": 5, "novelty": "same", "story_space": None}
        v = _verdict(m=metrics(zt_count=52), themes=[theme])
        assert self._vote(v, "c2")["hit"]

    def test_story_vote_unavailable_without_model(self):
        theme = {"name": "物理AI", "zt_count": 3, "max_boards": 1, "novelty": "new", "story_space": None}
        vote = self._vote(_verdict(themes=[theme]), "k4")
        assert vote["available"] is False and vote["hit"] is False

    def test_track_record_needs_enough_samples(self):
        thin = {"chen": {"days": 2, "avg": 3.0}, "kobe": {"days": 2, "avg": -1.0}}
        assert self._vote(_verdict(track=thin), "r1")["available"] is False
        rich = {"chen": {"days": 6, "avg": 3.0}, "kobe": {"days": 6, "avg": -1.0}}
        vote = self._vote(_verdict(track=rich), "r1")
        assert vote["hit"] and vote["style"] == "chen"

    def test_close_votes_mean_both(self):
        assert _verdict()["preferred"] == "both"       # 发酵期只有 c4 一票，差 1 票

    def test_clear_majority_picks_a_side(self):
        theme = {"name": "机器人", "zt_count": 21, "max_boards": 5, "novelty": "same", "story_space": None}
        cand = _cand("600001", "M2", signals=[{"text": "x", "principle_ids": ["C04"]}])
        lanes = {"chen": _lane("chen", [cand]), "kobe": _lane("kobe", [])}
        v = _verdict(m=metrics(zt_count=52), themes=[theme], candidates=[cand], lanes=lanes)
        assert v["votes"]["chen"] == 3 and v["preferred"] == "chen"
        assert "陈小群" in v["label"]

    def test_large_overlap_means_consensus_day(self):
        cand = _cand("600001")
        lanes = {"chen": _lane("chen", [cand]), "kobe": _lane("kobe", [cand])}
        theme = {"name": "机器人", "zt_count": 21, "max_boards": 5, "novelty": "same", "story_space": None}
        v = _verdict(m=metrics(zt_count=52), themes=[theme], lanes=lanes)
        assert v["preferred"] == "both" and v["label"] == "两派共识日"
