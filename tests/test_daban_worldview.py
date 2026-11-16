# -*- coding: utf-8 -*-
"""短线世界观：节点、按成员判共性、补涨结束、预期、边界与放行、推翻信号、次日对账。"""

from __future__ import annotations

import pytest

from src.services.daban import worldview as wv

from tests.daban_fixtures import rules, zt_row

PHASE = {"chen_stage": "warm", "chen_label": "回暖期", "kobe_stage": {"fall_day": 0, "label": "低位震荡"}}
RETREAT = {"chen_stage": "retreat", "chen_label": "退潮期", "kobe_stage": {"fall_day": 1, "label": "主跌第 1 天"}}


class TestNodes:
    def test_top_killed_and_high_diverge(self):
        d2 = [zt_row("600001", "龙", boards=4), zt_row("600002", "跟", boards=1)]
        d1 = [zt_row("600003", "新", boards=2)]
        nodes = wv.node_flags(d2_rows=d2, d1_rows=d1, d1_change={"600001": -9.8}, fall_day_d1=0)
        assert nodes["top_killed"] and nodes["high_diverge"] and not nodes["after_fall"]
        assert nodes["labels"] == ["龙头见顶杀高位", "高位分歧"] and "龙（-9.8%）" in wv.node_text(nodes)

    def test_low_board_kill_is_not_a_node(self):
        d2 = [zt_row("600001", "龙", boards=2)]
        nodes = wv.node_flags(d2_rows=d2, d1_rows=d2, d1_change={"600001": -9.8}, fall_day_d1=2)
        assert not nodes["top_killed"] and nodes["after_fall"]


class TestCommonality:
    def test_member_history_decides_new_vs_same(self):
        memory = {"600001": {"算力"}, "600002": {"算力"}, "600003": {"液冷"}}
        same = wv.member_commonality(["600001", "600002", "600003"], memory, ["算力租赁"], rules())
        assert same["kind"] == "same" and same["share"] == 0.667
        new = wv.member_commonality(["600003", "600004", "600005"], memory, ["算力"], rules())
        assert new["kind"] == "new"

    def test_no_recent_mains_is_unknown(self):
        assert wv.member_commonality(["600001", "600002"], {}, [], rules())["kind"] == "unknown"

    def test_catch_up_end_needs_both_signs(self):
        rows = [zt_row("600001", "龙", boards=3, reason="机器人"), zt_row("600002", "低", reason="机器人"),
                zt_row("600003", "低2", reason="机器人")]
        quotes = {"600001": {"price": 9.5, "prev_close": 10.0}, "600002": {"price": 9.8, "prev_close": 10.0},
                  "600003": {"price": 9.9, "prev_close": 10.0}}
        assert wv.catch_up_end("机器人", yesterday_rows=rows, quotes=quotes)["ended"] is True
        quotes["600001"]["price"] = 10.5      # 龙头反包了
        assert wv.catch_up_end("机器人", yesterday_rows=rows, quotes=quotes)["ended"] is False


class TestExpectation:
    def test_levels(self):
        row = zt_row("600001", "甲", boards=1)
        assert wv.expectation_of(row, 4.0, rules())["level"] == "above"
        assert wv.expectation_of(row, -1.0, rules())["level"] == "below"
        assert wv.expectation_of(zt_row("300001", "乙"), 2.0, rules())["gap10"] == 1.0   # 20cm 折算

    def test_summary_by_theme(self):
        rows = [zt_row("600001", "龙", boards=2, reason="机器人"), zt_row("600002", "跟", reason="机器人")]
        quotes = {"600001": {"open": 10.8, "prev_close": 10.0}, "600002": {"open": 10.4, "prev_close": 10.0}}
        out = wv.expectation_summary(rows, quotes, rules(), themes=[{"key": "机器人", "name": "机器人"}])
        assert out["themes"][0]["level"] == "above" and out["themes"][0]["leader"]["code"] == "600001"


class TestBoundary:
    def test_open_day(self):
        out = wv.decide_boundary(phase=PHASE, nodes={}, rt={}, overrides=[], rules=rules())
        assert out["status"] == "open" and out["open_all"]

    def test_retreat_blocked_unless_node(self):
        closed = wv.decide_boundary(phase=RETREAT, nodes={}, rt={}, overrides=[], rules=rules())
        assert closed["status"] == "closed" and set(closed["blocks"]) == {"retreat", "fall_1"}
        node = wv.decide_boundary(phase=RETREAT, nodes={"top_killed": True}, rt={}, overrides=[], rules=rules())
        assert node["status"] == "open" and node["pass"]["kind"] == "node"

    def test_ice_is_not_passed_by_node(self):
        ice = {"chen_stage": "ice", "kobe_stage": {"fall_day": 0}}
        out = wv.decide_boundary(phase=ice, nodes={"top_killed": True}, rt={}, overrides=[], rules=rules())
        assert out["status"] == "closed"

    def test_market_confirm_and_overrides(self):
        rt = {"market_confirmed": True, "today_stage": "warm", "today_label": "回暖期"}
        assert wv.decide_boundary(phase=RETREAT, nodes={}, rt=rt, overrides=[], rules=rules())["pass"]["kind"] == "market"
        theme = wv.decide_boundary(phase=RETREAT, nodes={}, rt={}, rules=rules(),
                                   overrides=[{"scope": "机器人", "label": "题材异动"}])
        assert theme["status"] == "partial" and theme["open_themes"] == ["机器人"]
        assert wv.candidate_gate(theme_key="机器人", y_boards=0, boundary=theme, theme_info={}, rules=rules()) is None
        assert "周期边界" in wv.candidate_gate(theme_key="其他", y_boards=0, boundary=theme, theme_info={}, rules=rules())

    def test_frozen_stops_new(self):
        out = wv.decide_boundary(phase=PHASE, nodes={}, rt={"frozen": True, "reason": "停发"}, overrides=[], rules=rules())
        assert out["status"] == "frozen"
        assert "停发" in wv.candidate_gate(theme_key="x", y_boards=0, boundary=out, theme_info={}, rules=rules())

    def test_freeze_beats_index_surge_but_not_opened_themes(self):
        """停发是今天盘中按我们买的那批票判的，大盘急拉不解除；题材推翻信号放开的题材照常推荐。"""
        overrides = [{"scope": "all", "label": "大盘急拉"}, {"scope": "机器人", "label": "题材异动"}]
        out = wv.decide_boundary(phase=RETREAT, nodes={}, rt={"frozen": True}, overrides=overrides, rules=rules())
        assert out["status"] == "frozen" and out["open_all"]
        assert "停发" in wv.candidate_gate(theme_key="其他", y_boards=0, boundary=out, theme_info={}, rules=rules())
        assert wv.candidate_gate(theme_key="机器人", y_boards=0, boundary=out, theme_info={}, rules=rules()) is None

    def test_candidate_gates(self):
        open_ = wv.decide_boundary(phase=PHASE, nodes={}, rt={}, overrides=[], rules=rules())
        assert "中位股" in wv.candidate_gate(theme_key="x", y_boards=2, boundary=open_, theme_info={}, rules=rules())
        new = {"commonality": {"kind": "new"}, "timing_ok": False}
        assert "原文时机" in wv.candidate_gate(theme_key="x", y_boards=0, boundary=open_, theme_info=new, rules=rules())
        assert wv.candidate_gate(theme_key="x", y_boards=0, boundary=open_, rules=rules(),
                                 theme_info={**new, "timing_ok": True}) is None
        ended = {"catch_up": {"ended": True, "reason": "补涨结束（科比 #6）"}}
        assert "补涨结束" in wv.candidate_gate(theme_key="x", y_boards=0, boundary=open_, theme_info=ended, rules=rules())


class TestOverrides:
    def test_index_rise_within_window(self):
        history = [("100000", {"sh000001": -0.2}), ("101500", {"sh000001": 0.1}), ("102500", {"sh000001": 0.7})]
        out = wv.detect_overrides(now="102500", index_history=history, rows=[], hot_themes=[], theme_info={},
                                  nodes={}, rules=rules(), names={"sh000001": "上证指数"})
        assert [o["key"] for o in out] == ["index:sh000001"] and out[0]["scope"] == "all"

    def test_index_rise_must_end_positive(self):
        history = [("100000", {"sh000001": -1.5}), ("102000", {"sh000001": -0.5})]
        assert wv.detect_overrides(now="102000", index_history=history, rows=[], hot_themes=[], theme_info={},
                                   nodes={}, rules=rules()) == []

    def test_theme_burst_and_new_theme(self):
        rows = [zt_row(f"60000{i}", f"股{i}", reason="液冷服务器", first_time=f"1035{i}0") for i in range(1, 5)]
        hot = [{"key": "液冷", "name": "液冷", "zt_count": 4}]
        info = {"液冷": {"commonality": {"kind": "new", "share": 0.0}}}
        out = wv.detect_overrides(now="104500", index_history=[], rows=rows, hot_themes=hot, theme_info=info,
                                  nodes={"high_diverge": True}, rules=rules())
        assert {o["kind"] for o in out} == {"theme", "new_theme"}
        late = wv.detect_overrides(now="104500", index_history=[], rows=rows, hot_themes=hot, theme_info=info,
                                   nodes={}, rules=rules())
        assert {o["kind"] for o in late} == {"theme"}       # 不在原文时机：新题材信号不触发


class TestWorldview:
    def test_build_has_eleven_dims_and_boundary(self):
        themes = [{"key": "机器人", "name": "机器人", "rank": 1, "zt_count": 5, "is_main": True,
                   "members": [{"code": "600001", "name": "龙", "boards": 3}], "leader": {"name": "龙"}}]
        view = wv.build_worldview(for_date="2026-11-09", based_on="2026-11-06", metrics={"yzt_premium": 1.2},
                                  stage={"key": "retreat", "label": "退潮期"}, kobe={"label": "主跌第 1 天", "fall_day": 1},
                                  themes=themes, theme_info={}, nodes={"top_killed": True, "labels": ["龙头见顶杀高位"]},
                                  zt_rows=[zt_row("600001", "龙", boards=3)], weak=["600002"], rules=rules())
        assert len(view["dims"]) == 11 and view["blocked"] is False
        assert "照常推荐" in view["boundary_text"]

    def test_reconcile(self):
        prev = {"dims": [{}], "stage": "warm", "stage_label": "回暖期", "main_themes": ["机器人"],
                "themes": [{"key": "机器人", "name": "机器人", "is_main": True,
                            "leaders": [{"code": "600001", "name": "龙"}], "commonality": {"kind": "same"},
                            "first_boards": ["600002"]}]}
        out = wv.reconcile(prev, metrics={"yzt_premium": 1.5},
                           themes=[{"name": "机器人", "max_boards": 4, "members": [{"code": "600001"}]}],
                           zt_rows=[zt_row("600001", "龙", boards=4)], previous=[{"code": "600002", "change_pct": 3.0}])
        checks = {c["key"]: c["ok"] for c in out["checks"]}
        assert checks == {"cycle": True, "mainline": True, "leader": True, "catch_up": True}


class TestSurgePoint:
    def test_needs_rise_within_window_and_positive(self):
        series = [("093500", {"sh000300": -0.3}), ("094500", {"sh000300": -0.2}), ("100500", {"sh000300": 0.6})]
        hit = wv.surge_point(series, rules())
        assert hit["at"] == "100500" and hit["until"] == "103500" and hit["rise"] == pytest.approx(0.9)
        # 拉升够了但还没翻红：不算
        assert wv.surge_point([("093500", {"sh000300": -1.2}), ("100500", {"sh000300": -0.3})], rules()) is None
        # 不到 30 分钟没有参考点：不算
        assert wv.surge_point([("093500", {"sh000300": -0.3}), ("095500", {"sh000300": 0.6})], rules()) is None

    def test_opening_gap_is_not_a_surge(self):
        # 参考点只用 09:35 之后的分时：开盘跳空（09:30 的点）不算急拉
        series = [("093000", {"sh000300": -0.5}), ("093500", {"sh000300": 0.5}), ("100500", {"sh000300": 0.7})]
        assert wv.surge_point(series, rules()) is None

    def test_first_trigger_of_the_day_wins(self):
        series = [("093500", {"sh000300": 0.0}), ("100500", {"sh000300": 0.9}), ("103500", {"sh000300": 1.8})]
        assert wv.surge_point(series, rules())["at"] == "100500"


class TestBlockedDaySurge:
    """被前一交易日周期挡住的日子：大盘指数急拉只提示；沪深300 推荐点只放开它的 30 分钟窗口，标未验证。"""

    INDEX = {"kind": "index", "scope": "all", "key": "index:sh000001", "label": "大盘指数急拉（只提示，不放开）：上证"}

    def test_index_override_no_longer_opens_a_blocked_day(self):
        out = wv.decide_boundary(phase=RETREAT, nodes={}, rt={}, overrides=[self.INDEX], rules=rules())
        assert out["status"] == "closed" and out["pass"] is None
        assert any("只提示" in r for r in out["reasons"])

    def test_index_override_can_be_switched_back_on(self):
        cfg = rules()
        cfg["overrides"] = {**cfg["overrides"], "index_opens": True}
        out = wv.decide_boundary(phase=RETREAT, nodes={}, rt={}, overrides=[self.INDEX], rules=cfg)
        assert out["status"] == "open" and out["pass"]["kind"] == "override"

    def test_surge_window_opens_only_while_active(self):
        surge = {"at": "100500", "until": "103500", "label": "大盘急拉推荐点：沪深300", "active": True}
        inside = wv.decide_boundary(phase=RETREAT, nodes={}, rt={}, overrides=[self.INDEX], rules=rules(), surge=surge)
        assert inside["status"] == "open" and inside["pass"]["kind"] == "surge" and "未验证" in inside["pass"]["label"]
        after = wv.decide_boundary(phase=RETREAT, nodes={}, rt={}, overrides=[self.INDEX], rules=rules(),
                                   surge={**surge, "active": False})
        assert after["status"] == "closed"

    def test_freeze_still_wins_over_the_surge_window(self):
        surge = {"at": "100500", "until": "103500", "label": "推荐点", "active": True}
        out = wv.decide_boundary(phase=RETREAT, nodes={}, rt={"frozen": True}, overrides=[], rules=rules(), surge=surge)
        assert out["status"] == "frozen"
        assert "停发" in wv.candidate_gate(theme_key="x", y_boards=0, boundary=out, theme_info={}, rules=rules())

    def test_index_override_label_says_notice_only(self):
        history = [("100000", {"sh000001": -0.2}), ("102500", {"sh000001": 0.7})]
        out = wv.detect_overrides(now="102500", index_history=history, rows=[], hot_themes=[], theme_info={},
                                  nodes={}, rules=rules(), names={"sh000001": "上证指数"})
        assert "只提示" in out[0]["label"]
