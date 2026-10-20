# -*- coding: utf-8 -*-
"""题材聚类、个股角色、七种战法候选与打板王指数。"""

from __future__ import annotations

from src.services.daban import patterns, scoring
from src.services.daban.cycle import compute_metrics
from src.services.daban.roles import assign_roles
from src.services.daban.themes import build_themes, split_reason

from tests.daban_fixtures import prev_row, robot_day, rules, zt_row


def _context(zt, *, previous=None, stage_key="ferment", kobe=None, recent_mains=("机器人",), sub_new=()):
    r = rules()
    theme_result = build_themes(zt=zt, blocks=[], history=[], previous=previous, yesterday_rows=[],
                                recent_mains=list(recent_mains), hot_rank={}, rules=r)
    m = compute_metrics(zt=zt, zb=[], dt=[], previous=previous, rules=r)
    themes = theme_result["themes"]
    roles = assign_roles(zt=zt, themes=themes, stock_themes=theme_result["stock_themes"],
                         max_height=m["max_height"], hot_rank={}, rules=r)
    return {
        "rules": r, "metrics": m, "themes": themes, "theme_by_key": {t["key"]: t for t in themes},
        "zt": zt, "roles": roles, "stock_themes": theme_result["stock_themes"], "previous": previous or [],
        "y_max_height": max((p["y_boards"] for p in previous or []), default=0), "yesterday_theme": {},
        "stage_key": stage_key, "kobe": kobe or {"fall_day": 0}, "sub_new": list(sub_new), "hot_rank": {},
    }


def _modes(candidates, code):
    return sorted(c["mode"] for c in candidates if c["code"] == code)


class TestThemes:
    def test_reason_tags_drop_company_catalysts(self):
        assert split_reason("人形机器人+业绩预增+大额订单+低空经济概念股活跃") == ["人形机器人", "低空经济"]

    def test_tags_sharing_a_core_word_merge_into_one_theme(self):
        ctx = _context(robot_day())
        top = ctx["themes"][0]
        assert top["key"] == "机器人"
        assert {"人形机器人", "工业机器人", "机器人结构件"} <= set(top["tags"])
        assert top["is_main"] and top["leader"]["code"] == "003029"

    def test_novelty_against_recent_mainlines(self):
        ctx = _context(robot_day(), recent_mains=("机器人",))
        by_key = {t["key"]: t for t in ctx["themes"]}
        assert by_key["机器人"]["novelty"] == "same"
        assert by_key["PCB"]["novelty"] == "new"


class TestRoles:
    def test_middle_board_is_excluded(self):
        ctx = _context(robot_day())
        assert ctx["roles"]["600178"]["role"] == "middle"
        candidates, excluded, _ = patterns.generate_candidates(ctx)
        assert not _modes(candidates, "600178")
        reason = next(e for e in excluded if e["code"] == "600178")
        assert "中位股" in reason["reason"] and reason["principle_ids"] == ["K11"]

    def test_follower_in_mainline_is_excluded(self):
        ctx = _context(robot_day())
        assert ctx["roles"]["603119"]["role"] == "follower"
        _, excluded, _ = patterns.generate_candidates(ctx)
        assert any(e["code"] == "603119" and "C13" in e["principle_ids"] for e in excluded)

    def test_three_board_top_is_not_middle(self):
        # 最高只有 3 板时，「3 板到最高板 - 1」区间为空，不能把空间板当中位股。
        zt = [zt_row("600001", "甲", boards=3, reason="人形机器人"), zt_row("600002", "乙", reason="人形机器人"),
              zt_row("600003", "丙", reason="人形机器人")]
        ctx = _context(zt)
        assert ctx["roles"]["600001"]["role"] == "total_leader"


class TestPatterns:
    def test_total_leader_gets_m1_with_divergence_trigger(self):
        candidates, _, _ = patterns.generate_candidates(_context(robot_day()))
        lead = next(c for c in candidates if c["code"] == "003029" and c["mode"] == "M1")
        ids = {pid for s in lead["signals"] for pid in s["principle_ids"]}
        assert {"C13", "K22", "K14"} <= ids

    def test_one_word_leader_is_marked_wait_for_divergence(self):
        zt = robot_day()
        zt[0].update(limit_up_type="一字板", turnover=1.2)
        candidates, _, _ = patterns.generate_candidates(_context(zt))
        lead = next(c for c in candidates if c["code"] == "003029" and c["mode"] == "M1")
        assert "一致板，等分歧" in lead["notes"]

    def test_weak_board_in_mainline_becomes_weak_to_strong(self):
        candidates, _, _ = patterns.generate_candidates(_context(robot_day()))
        assert "M2" in _modes(candidates, "002347")   # 炸板 1 次回封、属主线

    def test_death_turnover_blocks_weak_to_strong(self):
        zt = robot_day()
        zt[4].update(turnover_rate=75.0)
        candidates, excluded, _ = patterns.generate_candidates(_context(zt))
        assert "M2" not in _modes(candidates, "002347")

    def test_isolated_weak_board_is_excluded_only_when_no_other_mode_fits(self):
        zt = robot_day() + [zt_row("600999", "孤立", reason="竹制餐厨具", open_num=2, price=40, circ_mv=300e8)]
        candidates, excluded, _ = patterns.generate_candidates(_context(zt))
        assert not _modes(candidates, "600999")
        assert any(e["code"] == "600999" and "板块效应" in e["reason"] for e in excluded)

    def test_rebound_first_board_needs_kline_confirmation(self):
        candidates, _, _ = patterns.generate_candidates(_context(robot_day()))
        rebound = [c for c in candidates if c["code"] == "605058" and c["mode"] == "M5"]
        assert rebound and rebound[0]["needs_kline_confirm"]
        # 日 K 显示近期没有 ≥3 连板 → 反包首板不成立，候选被丢弃。
        features = {"605058": {"available": True, "today_limit": True, "yesterday_limit": False,
                               "recent_streak": 1, "recent_streak_gap": 3}}
        kept = patterns.confirm_with_kline(rebound, features)
        assert kept == []

    def test_first_negative_leader_from_yesterday(self):
        previous = [prev_row("002889", "东方嘉盛", y_boards=4, change_pct=-6.7)]
        ctx = _context(robot_day(), previous=previous)
        ctx["yesterday_theme"] = {"002889": "机器人"}
        candidates, _, _ = patterns.generate_candidates(ctx)
        assert {"M1", "M2", "M5"} <= set(_modes(candidates, "002889"))
        m1 = next(c for c in candidates if c["code"] == "002889" and c["mode"] == "M1")
        # 昨天 4 板、今天最高也只有 4 板：仍是空间板，按总龙头对待（陈小群：龙头低吸只做绝对龙头）。
        assert m1["origin"] == "previous" and m1["role"] == "total_leader"
        assert m1["conditions"]["total_leader"] is True

    def test_yesterday_leader_overtaken_today_is_no_longer_total_leader(self):
        previous = [prev_row("002889", "东方嘉盛", y_boards=3, change_pct=-6.7)]
        ctx = _context(robot_day(), previous=previous)    # 今天吉大正元 4 板
        candidates, _, _ = patterns.generate_candidates(ctx)
        m1 = next(c for c in candidates if c["code"] == "002889" and c["mode"] == "M1")
        assert m1["role"] == "theme_leader" and m1["conditions"]["total_leader"] is False


class TestScoring:
    def _scored(self, ctx):
        candidates, _, _ = patterns.generate_candidates(ctx)
        for cand in candidates:
            scoring.score_candidate(cand, theme_by_key=ctx["theme_by_key"], hot_rank={}, rules=ctx["rules"])
        return candidates

    def test_dimensions_sum_to_rule_score_and_stay_within_max(self):
        for cand in self._scored(_context(robot_day())):
            assert abs(sum(d["score"] for d in cand["dimensions"]) - cand["rule_score"]) < 0.11
            assert all(0 <= d["score"] <= d["max"] for d in cand["dimensions"])
            assert sum(d["max"] for d in cand["dimensions"]) == 100
            assert all(d["principle_ids"] for d in cand["dimensions"])

    def test_llm_adjust_is_clipped_to_ten(self):
        cand = self._scored(_context(robot_day()))[0]
        base = cand["rule_score"] - cand["risk_penalty"]
        cand["llm_adjust"] = 25
        scoring.finalize_score(cand, rules())
        assert cand["llm_adjust"] == 10
        assert cand["score"] == round(max(0.0, min(100.0, base + 10)), 1)

    def test_long_running_theme_is_penalized(self):
        ctx = _context(robot_day())
        ctx["theme_by_key"]["机器人"]["days"] = 50
        cand = next(c for c in self._scored(ctx) if c["code"] == "603278")
        assert any("K13" in r["principle_ids"] for r in cand["risks"])

    def test_grades(self):
        r = rules()
        assert [scoring.grade_of(v, r) for v in (90, 80, 70, 50)] == ["S", "A", "B", "C"]
