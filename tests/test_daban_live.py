# -*- coding: utf-8 -*-
"""盘中雷达：前一交易日上下文、实时周期重判、异动降级、候选口径、全市场扫描、旧表回填、后台任务。

「重点」评分与推荐的端到端链路见 ``tests/test_daban_focus.py``，计分与世界观见 ``test_daban_rec_features.py`` /
``test_daban_worldview.py``。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.services.daban import live
from src.services.daban.forecast import active_themes, build_forecasts, summarize_forecasts

from tests.daban_fixtures import rules, zt_row


class _Repo:
    def __init__(self, snapshot=None, pools=None):
        self.snapshot = snapshot
        self.pools = pools or {}
        self.live = {}

    def snapshot_for_trade_date(self, day):
        return self.snapshot if self.snapshot and self.snapshot.trade_date == day else None

    def history(self, *, limit=10, before=None):
        return []

    def pool_rows(self, day, pool="zt"):
        return self.pools.get(pool, [{"code": "600009"}])

    def reason_memory(self, since, pool="zt", *, until=None):
        return []

    def upsert_live_hits(self, day, hits, *, final):
        rows = self.live.setdefault(day, {})
        inserted = []
        for hit in hits:
            key = (hit["style"], hit["code"])
            if key not in rows:
                rows[key] = dict(hit)
                inserted.append(hit)
        return inserted

    def pending_live_dates(self):
        return []

    def live_hits(self, day):
        return [dict(row) for row in self.live.get(day, {}).values()]


def _service(repo, **overrides):
    config = SimpleNamespace(
        daban_fetch_timeout_sec=10, daban_live_cache_ttl_sec=0, daban_rules_file="", daban_max_actionable=4,
        daban_live_push_enabled=False, daban_live_poll_sec=10,
    )
    for key, value in overrides.items():
        setattr(config, key, value)
    return live.LiveRadarService(repository=repo, config=config)


def _pools(monkeypatch, zt_count=45):
    from src.services.daban import fetchers

    def pool(kind, day, *, timeout, include_bse):
        rows = [zt_row(f"60{i:04d}", f"股{i}", reason="人形机器人") for i in range(zt_count)] if kind == "zt" else []
        return {"ok": True, "value": {"rows": rows}}

    monkeypatch.setattr(fetchers, "fetch_limit_pool", pool)
    monkeypatch.setattr(fetchers, "fetch_ladder", lambda day, *, timeout: {"ok": True, "value": {"ladder": {}}})
    monkeypatch.setattr(fetchers, "fetch_em", lambda kind, day, *, timeout, include_bse: {"ok": False})


class TestPreviousContext:
    def test_uses_the_review_of_the_previous_trade_day(self, monkeypatch):
        from src.services.daban import fetchers

        live.clear_cache()
        monkeypatch.setattr(fetchers, "previous_trade_date", lambda day, **kw: {"2026-10-20": "2026-10-19"}[day])
        snap = SimpleNamespace(trade_date="2026-10-19", id=1, payload=json.dumps({
            "stage": {"key": "retreat", "label": "退潮期"}, "kobe_stage": {"gate_key": "fall_2", "label": "主跌第 2 天"},
            "metrics": {"zt_count": 33, "dt_count": 49, "lianban_count": 9},
        }))
        ctx = _service(_Repo(snap)).previous_context("2026-10-20")
        assert ctx["source"] == "review" and ctx["kobe_gate"] == "fall_2"
        assert ctx["yesterday_codes"] == ["600009"]
        # 门控依据要一起带出来：页面上的「今日门控」卡要能解释为什么是这个阶段。
        assert ctx["metrics"]["dt_count"] == 49 and ctx["stage"]["key"] == "retreat"
        assert ctx["kobe_stage"]["gate_key"] == "fall_2"

    def test_yesterday_weak_excludes_clean_limit_ups(self, monkeypatch):
        """弱转强只认昨日的弱板：炸板 / 盘中开过板 / 尾盘偷鸡板。干净涨停的票今天再涨是二板，不是弱转强。"""
        from src.services.daban import fetchers

        live.clear_cache()
        monkeypatch.setattr(fetchers, "previous_trade_date", lambda day, **kw: {"2026-10-20": "2026-10-19"}[day])
        snap = SimpleNamespace(trade_date="2026-10-19", id=1, payload=json.dumps({
            "stage": {"key": "warm", "label": "回暖期"}, "kobe_stage": {"gate_key": "fall_2", "label": "主跌第 2 天"},
        }))
        pools = {
            "zt": [
                {"code": "600001", "first_time": "100000", "last_time": "100000"},   # 干净涨停 → 不算弱
                {"code": "600002", "first_time": "100000", "last_time": "133000"},   # 盘中开过板 → 弱
                {"code": "600003", "first_time": "144500", "last_time": "144500"},   # 尾盘偷鸡板 → 弱
            ],
            "zb": [{"code": "600004"}],                                             # 炸板 → 弱
        }
        ctx = _service(_Repo(snap, pools=pools)).previous_context("2026-10-20")
        assert ctx["yesterday_codes"] == ["600001", "600002", "600003"]
        assert ctx["yesterday_weak"] == ["600002", "600003", "600004"]

    def test_weak_codes_prefers_open_num_when_upstream_gives_it(self):
        rows = [{"code": "600001", "first_time": "100000", "last_time": "100000", "open_num": 2},
                {"code": "600002", "first_time": "100000", "last_time": "100000", "open_num": 0}]
        assert live.weak_codes(rows, [], late="143000") == ["600001"]

    def test_older_review_is_never_used(self, monkeypatch):
        """库里只有更早的复盘时，不能拿来门控今天，要现算前一交易日。"""
        from src.services.daban import fetchers

        live.clear_cache()
        cursor = {"2026-10-20": "2026-10-19", "2026-10-19": "2026-10-16", "2026-10-16": "2026-10-15"}
        monkeypatch.setattr(fetchers, "previous_trade_date", lambda day, **kw: cursor[day])
        _pools(monkeypatch)
        old = SimpleNamespace(trade_date="2026-10-15", id=1, payload=json.dumps({"stage": {"key": "climax"}}))
        ctx = _service(_Repo(old)).previous_context("2026-10-20")
        assert ctx["source"] == "computed" and ctx["trade_date"] == "2026-10-19"
        assert ctx["yesterday_codes"] and ctx["recent_mains"]

    def test_upstream_failure_is_unavailable(self, monkeypatch):
        from src.services.daban import fetchers

        live.clear_cache()
        monkeypatch.setattr(fetchers, "previous_trade_date",
                            lambda day, **kw: "2026-10-19" if day == "2026-10-20" else "2026-10-16")

        def boom(*a, **k):
            raise RuntimeError("down")

        for name in ("fetch_limit_pool", "fetch_ladder", "fetch_em"):
            monkeypatch.setattr(fetchers, name, boom)
        ctx = _service(_Repo()).previous_context("2026-10-20")
        assert ctx["available"] is False and "2026-10-19" in ctx["reason"]


class TestReview:
    def test_review_waits_for_next_close(self, monkeypatch):
        from src.services.daban import fetchers

        calls = []

        class Repo:
            def pending_live_dates(self):
                return ["2026-10-20"]

            def live_hits(self, day):
                return [{"style": "kobe", "code": "600001", "limit_price": 11.0, "reviewed": False,
                         "first_time": "100000"}]

            def update_live_outcomes(self, day, outcomes):
                calls.append(outcomes)

        monkeypatch.setattr(fetchers, "next_trade_date", lambda day, **kw: "2026-10-21")
        monkeypatch.setattr(fetchers, "fetch_klines", lambda codes, day, *, limit, timeout: {
            "value": {"rows": {"600001": [{"time": "20261021", "open": 11.55, "close": 12.1}]}}})
        assert live.review_pending_live_hits(Repo(), today="2026-10-21", now="140000") == 0
        assert live.review_pending_live_hits(Repo(), today="2026-10-21", now="151000") == 1
        assert calls[0][("kobe", "600001")] == {"open_ret": 5.0, "close_ret": 10.0}

    def test_same_code_in_both_styles_keeps_its_own_cost(self, monkeypatch):
        """两派锁定同一只票、锁定时刻的现价不同：各按自己的成本回填，不能互相覆盖。"""
        from src.services.daban import fetchers

        calls = []

        class Repo:
            def pending_live_dates(self):
                return ["2026-10-20"]

            def live_hits(self, day):
                base = {"code": "600001", "limit_price": 11.0, "reviewed": False, "first_time": "100000"}
                return [{**base, "style": "kobe", "seen_price": 10.0}, {**base, "style": "chen", "seen_price": 11.0}]

            def update_live_outcomes(self, day, outcomes):
                calls.append(outcomes)

        monkeypatch.setattr(fetchers, "next_trade_date", lambda day, **kw: "2026-10-21")
        monkeypatch.setattr(fetchers, "fetch_klines", lambda codes, day, *, limit, timeout: {
            "value": {"rows": {"600001": [{"time": "20261021", "open": 11.0, "close": 12.1}]}}})
        live.review_pending_live_hits(Repo(), today="2026-10-21", now="151000")
        assert calls[0][("kobe", "600001")]["open_ret"] == 10.0
        assert calls[0][("chen", "600001")]["open_ret"] == 0.0


class TestBackgroundTask:
    @staticmethod
    def _cfg(**kw):
        base = dict(daban_enabled=True, daban_live_enabled=True, daban_auto_generate=False,
                    daban_live_push_enabled=False)
        base.update(kw)
        return SimpleNamespace(**base)

    def test_polls_when_auto_generate_is_on_even_without_push(self):
        tasks = live.build_live_background_tasks(self._cfg(daban_auto_generate=True), config_provider=lambda: None)
        assert [t["name"] for t in tasks] == [live.LIVE_TASK_NAME]

    def test_push_alone_still_polls(self):
        assert live.build_live_background_tasks(self._cfg(daban_live_push_enabled=True), config_provider=lambda: None)

    def test_nothing_when_both_off_or_radar_disabled(self):
        assert live.build_live_background_tasks(self._cfg(), config_provider=lambda: None) == []
        assert live.build_live_background_tasks(
            self._cfg(daban_auto_generate=True, daban_live_enabled=False), config_provider=lambda: None) == []


class TestIntradayReviewIsNotAGate:
    def test_intraday_only_snapshot_is_recomputed(self, monkeypatch):
        """前一交易日只在盘中生成过复盘：那份指标是盘中某一刻的，不能当收盘周期门控，要用池子现算。"""
        from src.services.daban import fetchers

        live.clear_cache()
        cursor = {"2026-10-20": "2026-10-19", "2026-10-19": "2026-10-16", "2026-10-16": "2026-10-15"}
        monkeypatch.setattr(fetchers, "previous_trade_date", lambda day, **kw: cursor[day])
        _pools(monkeypatch)
        snap = SimpleNamespace(trade_date="2026-10-19", id=1, intraday=True, payload=json.dumps({
            "stage": {"key": "ice", "label": "冰点期"}, "kobe_stage": {"gate_key": "fall_1"},
            "metrics": {"zt_count": 4}}))
        ctx = _service(_Repo(snap)).previous_context("2026-10-20")
        assert ctx["source"] == "computed" and ctx["trade_date"] == "2026-10-19"


class TestForecast:
    def test_active_themes_filter(self):
        themes = [
            {"key": "机器人", "name": "机器人", "is_main": True, "status": "rise", "days": 7, "zt_count": 10, "rank": 1},
            {"key": "老", "name": "老", "is_main": True, "status": "diverge", "days": 9, "zt_count": 6, "rank": 2},
            {"key": "久", "name": "久", "is_main": True, "status": "rise", "days": 30, "zt_count": 6, "rank": 3},
        ]
        assert [t["key"] for t in active_themes(themes, rules())] == ["机器人"]

    def test_sources(self):
        themes = [{"key": "机器人", "name": "机器人", "is_main": True, "status": "rise", "days": 5,
                   "zt_count": 6, "rank": 1, "tags": ["机器人"]}]
        picks = build_forecasts(themes=themes, zt=[{"code": "600001"}],
                                broken=[{"code": "600002", "name": "炸", "reason": "人形机器人"}],
                                reason_memory={"600003": {"人形机器人"}}, limit_memory={"600003": 3}, rules=rules())
        assert {(p["code"], p["source"]) for p in picks} == {("600002", "broken"), ("600003", "memory")}

    def test_memory_source_needs_two_limit_ups(self):
        themes = [{"key": "机器人", "name": "机器人", "is_main": True, "status": "rise", "days": 5,
                   "zt_count": 6, "rank": 1, "tags": ["机器人"]}]
        assert build_forecasts(themes=themes, zt=[], broken=[], reason_memory={"600003": {"人形机器人"}},
                               limit_memory={"600003": 1}, rules=rules()) == []

    def test_summary(self):
        rows = [{"source": "broken", "touched": True, "sealed": True, "target_date": "d1", "evaluated": True},
                {"source": "broken", "touched": False, "sealed": False, "target_date": "d2", "evaluated": True}]
        assert summarize_forecasts(rows)["groups"]["broken"] == {"n": 2, "touch_rate": 0.5, "seal_rate": 0.5}


def _boom():
    raise RuntimeError("Read timed out")


class TestChangeFeedDegradation:
    """拉升异动：东财 push2ex 在 09:30 后几分钟会读超时 / 断连。一路失败不清空候选，也不拖垮另一路。"""

    EVENTS = [{"code": "600001", "name": "甲", "type": "RISE", "date": "", "time": "093500",
               "order_amount": None, "change": 6.0, "price": 10.5}]

    def _svc(self, monkeypatch, outcomes):
        from data_provider import limit_up_fetcher as lu

        live.clear_cache()
        calls = {"n": 0}

        def fetch(kind, *, timeout):
            calls["n"] += 1
            outcome = outcomes[min(calls["n"], len(outcomes)) - 1]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        monkeypatch.setattr(lu, "fetch_em_changes", fetch)
        return _service(_Repo())

    def test_failure_reuses_the_last_good_feed(self, monkeypatch):
        svc = self._svc(monkeypatch, [self.EVENTS, RuntimeError("Read timed out")])
        assert svc._changes(("rise",), timeout=4, reuse_sec=90) == ({"rise": self.EVENTS}, [])
        feeds, notes = svc._changes(("rise",), timeout=4, reuse_sec=90)
        assert feeds["rise"] == self.EVENTS
        assert len(notes) == 1 and "沿用" in notes[0] and "Read timed out" in notes[0]

    def test_cache_is_not_reused_past_the_window(self, monkeypatch):
        svc = self._svc(monkeypatch, [self.EVENTS, RuntimeError("Read timed out")])
        svc._changes(("rise",), timeout=4, reuse_sec=90)
        feeds, notes = svc._changes(("rise",), timeout=4, reuse_sec=0)
        assert feeds["rise"] == [] and "不可用" in notes[0]

    def test_failure_without_cache_is_empty_but_does_not_raise(self, monkeypatch):
        svc = self._svc(monkeypatch, [RuntimeError("RemoteDisconnected")])
        feeds, notes = svc._changes(("rise",), timeout=4, reuse_sec=90)
        assert feeds["rise"] == [] and "这一路的未涨停候选为空" in notes[0]

    def test_one_kind_failing_does_not_take_down_the_other(self, monkeypatch):
        from data_provider import limit_up_fetcher as lu

        live.clear_cache()
        monkeypatch.setattr(lu, "fetch_em_changes",
                            lambda kind, *, timeout: self.EVENTS if kind == "rise" else _boom())
        feeds, notes = _service(_Repo())._changes(("rise", "rebound"), timeout=4, reuse_sec=90)
        assert feeds["rise"] == self.EVENTS and feeds["rebound"] == []
        assert len(notes) == 1 and "rebound" in notes[0]


PHASE_RETREAT_BASE = {"available": True, "trade_date": "2026-10-22", "chen_stage": "retreat",
                      "chen_label": "退潮期", "kobe_gate": "fall_1", "kobe_label": "主跌第 1 天",
                      "stage": {"key": "retreat"}, "metrics": {"zt_count": 33, "dt_count": 49}}


def _rt_metrics(**kw):
    base = {"zt_count": 55, "zb_count": 12, "dt_count": 6, "yzt_premium": 3.38, "zab_rate": 0.18,
            "lianban_count": 12, "max_height": 6, "promotion_rate": 0.36, "follow_rate": 0.2}
    base.update(kw)
    return base


def _decide(metrics, *, now="133000", accel=None, state=None, today=None, phase=None):
    today = today or {"stage": {"key": "ferment", "label": "发酵期"},
                      "kobe_stage": {"gate_key": "main_rise", "label": "主升"}}
    return live.decide_realtime_gate(
        rules=rules(), phase=phase or PHASE_RETREAT_BASE, metrics=metrics, today=today,
        now=now, accel_theme=accel, state=state if state is not None else {},
    )


class TestRealtimeGate:
    """盘中实时门控：前一交易日只做底线，今天的 tape 给出市场验证后按今天的周期行事。"""

    def test_fall_day_advances_and_late_swap(self):
        # 科比原文「第二天的杀跌……尾盘就可以找买点」说的是当天；昨天是第 1 天，今天就按第 2 天门控。
        assert live._advance_fall_day("fall_1") == "fall_2"
        assert live._advance_fall_day("fall_3") == "fall_3" and live._advance_fall_day("main_rise") == "main_rise"
        cfg = rules()
        assert live._late_swap(cfg, "fall_2", "093500") == "fall_2_early"   # 尾盘之前只试错
        assert live._late_swap(cfg, "fall_2", "140001") == "fall_2"         # 尾盘才给可执行
        assert live._gate(cfg, "kobe", "fall_2_early") == "trial"
        assert live._gate(cfg, "kobe", "fall_2") == "actionable"

    def test_small_sample_keeps_the_previous_day_gate(self):
        out = _decide(_rt_metrics(zt_count=12), now="093500")
        assert out["active"] is False and out["upgraded"] is False
        assert out["keys"]["chen"] == "retreat" and out["keys"]["kobe"] == "fall_2_early"
        assert "样本太小" in out["reason"]

    def test_upgrade_needs_two_consecutive_rounds(self):
        state = {}
        first = _decide(_rt_metrics(), state=state)
        assert first["active"] is True and first["upgraded"] is False and "还需连续 2 轮" in first["reason"]
        second = _decide(_rt_metrics(), state=state)
        assert second["upgraded"] is True and second["keys"]["chen"] == "ferment"
        assert "赚钱效应已被市场验证" in second["reason"]

    def test_upgrade_only_moves_toward_more_open(self):
        """今天比昨天更差时不降级——降级只走「资金撤了」那条路，且只停发新名额。"""
        state = {}
        today = {"stage": {"key": "ice", "label": "冰点期"}, "kobe_stage": {"gate_key": "fall_1", "label": "主跌第 1 天"}}
        phase = {**PHASE_RETREAT_BASE, "chen_stage": "warm", "chen_label": "回暖期"}
        for _ in range(2):
            out = _decide(_rt_metrics(), state=state, today=today, phase=phase)
        assert out["keys"]["chen"] == "warm" and out["upgraded"] is False

    def test_freeze_needs_negative_premium_plus_a_weak_signal(self):
        state = {"zt_peak": 60}
        for _ in range(2):
            out = _decide(_rt_metrics(zt_count=30, yzt_premium=-2.0), state=state)
        assert out["frozen"] is True and "停发新推荐" in out["reason"]

    def test_rotation_exempts_the_freeze(self):
        """大盘走弱但有热点题材在加速：资金在换题材不是在撤，不能停发名额。"""
        state = {"zt_peak": 60}
        for _ in range(2):
            out = _decide(_rt_metrics(zt_count=30, yzt_premium=-2.0), state=state, accel="机器人概念")
        assert out["frozen"] is False and "资金在换题材" in out["reason"]

    def test_negative_premium_alone_does_not_freeze(self):
        state = {}
        for _ in range(2):
            out = _decide(_rt_metrics(yzt_premium=-0.5, zab_rate=0.1, zt_count=55), state=state)
        assert out["frozen"] is False

    def test_theme_accelerating_counts_recent_seals_by_reason(self):
        hot = [{"name": "机器人概念", "zt_count": 9}, {"name": "储能", "zt_count": 4}]
        rows = [zt_row("600001", "甲", reason="人形机器人", first_time="132000"),
                zt_row("600002", "乙", reason="机器人概念", first_time="132500"),
                zt_row("600003", "丙", reason="储能", first_time="100000")]
        assert live._theme_accelerating(rows, hot, now="133000", window_min=15, min_new=2) == "机器人概念"
        assert live._theme_accelerating(rows, hot, now="133000", window_min=15, min_new=3) is None
        assert live._theme_accelerating(rows, hot, now="150000", window_min=15, min_new=2) is None


@pytest.mark.parametrize("now,day,expected", [
    ("093000", "2026-10-20", True), ("145959", "2026-10-20", True),
    ("091000", "2026-10-20", False), ("150100", "2026-10-20", False), ("100000", "2026-10-18", False),
])
def test_market_open_window(now, day, expected):
    assert live.market_open_now(now, day=day) is expected


class TestCandidates:
    def test_buyable_means_unsealed_and_close_or_opened(self):
        rows = [zt_row("600001", "封死"), zt_row("600002", "池里但已开板")]
        broken = [zt_row("600003", "炸了")]
        quotes = {
            "600001": {"price": 11.0, "limit_up": 11.0, "prev_close": 10.0},
            "600002": {"price": 10.8, "limit_up": 11.0, "prev_close": 10.0},
            "600003": {"price": 10.5, "limit_up": 11.0, "prev_close": 10.0},
        }
        preboard = [
            {"code": "600004", "name": "近", "price": 10.5, "limit_up": 11.0, "gap_pct": 4.76},
            {"code": "600005", "name": "远", "price": 10.0, "limit_up": 11.0, "gap_pct": 10.0},
        ]
        got = {c["code"]: c["status"] for c in live.focus_candidates(
            rows=rows, broken=broken, preboard=preboard, quotes=quotes, gap_max=6.0)}
        assert got == {"600002": "opened", "600003": "opened", "600004": "preboard"}

    def test_preboard_row_rejects_sealed_quotes(self):
        assert live._preboard_row({"code": "600001", "price": 11.0, "prev_close": 10.0, "limit_up": 11.0},
                                  found="100000", source="scan") is None
        row = live._preboard_row({"code": "600001", "price": 10.5, "prev_close": 10.0, "limit_up": 11.0},
                                 found="100000", source="scan")
        assert row["gap_pct"] == 4.76 and row["change_pct"] == 5.0 and row["source"] == "scan"

    def test_scan_market_batches_and_keeps_risers(self, monkeypatch):
        from data_provider import limit_up_fetcher as lu

        batches = []

        def quotes(codes, *, timeout=None):
            batches.append(len(codes))
            return {c: {"code": c, "price": 10.5 if c.endswith("1") else 9.5, "prev_close": 10.0} for c in codes}

        monkeypatch.setattr(lu, "fetch_tencent_quotes", quotes)
        codes = [f"60{i:04d}" for i in range(1300)]
        out = live.scan_market(timeout=3, workers=2, codes=codes)
        assert sorted(batches) == [100, 600, 600]
        assert all(q["price"] >= q["prev_close"] for q in out) and len(out) == 130

    def test_universe_excludes_bse_and_indices(self, monkeypatch):
        from src.data import stock_index_loader

        monkeypatch.setattr(stock_index_loader, "get_stock_name_index_map", lambda: {
            "600001.SH": "甲", "000001.SZ": "平安", "300001.SZ": "创", "688001.SH": "科", "920001.BJ": "北",
            "000001": "上证", "sh000001": "上证指数"})
        assert live.universe_codes() == ["000001", "300001", "600001", "688001"]


class TestScoreRulesView:
    def test_range_and_threshold(self):
        view = live.score_rules_view(rules())
        assert view["threshold"] == 0 and view["gap_max"] == 6.0
        lo, hi = view["range"]
        assert lo < 0 < hi and len(view["observe"]) == 4

    def test_alert_text(self):
        row = {"code": "600001", "name": "甲", "theme_name": "机器人", "init_score": 5, "entry_status": "preboard",
               "entered_at": "100000", "cost": 10.5, "entry_gap_pct": 4.76,
               "init_detail": {"items": [{"points": 5, "label": "昨日没触板，今天高开 2~5%", "source": "系统补充，无原文出处"}]}}
        text = live.render_focus_alert(row, {"pass": {"label": "节点日：龙头见顶杀高位"}})
        assert "总分 +5" in text and "10:00:00" in text and "节点日" in text and "H1" in text
        assert "仓位" not in text and "不构成投资建议" in text


class TestCodexReviewFixes:
    def test_short_tencent_line_drops_only_that_quote(self):
        from data_provider import limit_up_fetcher as lu

        fields = ["1", "甲", "600001", "10.5", "10.0", "10.2"] + ["0"] * 43       # 正好 49 段，没有量比
        fields[47], fields[48] = "11.00", "9.00"
        quote = lu.parse_tencent_line("~".join(fields))
        assert quote["code"] == "600001" and quote["volume_ratio"] is None
        assert lu.parse_tencent_line("~".join(fields[:30])) is None

    def test_concept_string_is_split_by_comma(self):
        from data_provider import limit_up_fetcher as lu

        assert lu._concept_list("机器人,液冷服务器，光伏概念") == ["机器人", "液冷服务器", "光伏概念"]
        assert lu._concept_list(["机器人", " "]) == ["机器人"] and lu._concept_list(None) == []

    def test_change_events_from_the_future_are_dropped(self, monkeypatch):
        from data_provider import limit_up_fetcher as lu

        live.clear_cache()
        events = [{"code": c, "name": c, "type": "RISE", "date": "", "time": t, "change": 6.0, "price": 10.5}
                  for c, t in (("600001", "100000"), ("600002", "145500"))]     # 600002 是上一交易日尾盘的
        monkeypatch.setattr(lu, "fetch_em_changes", lambda kind, *, timeout: events if kind == "rise" else [])
        asked = []
        monkeypatch.setattr(lu, "fetch_tencent_quotes", lambda codes, *, timeout=None: asked.extend(codes) or {})
        svc = _service(_Repo())
        monkeypatch.setattr(svc, "_market_scan", lambda *, now, timeout: ([], ""))
        svc._preboard_rows(now="100500", timeout=4, payload={"degraded_reasons": []}, exclude=set())
        assert asked == ["600001"]


class TestPendingLogEvents:
    """上一轮留档还在跑时，本轮的一次性事件要排队补写，不能丢。"""

    def test_one_off_events_survive_a_busy_round(self, monkeypatch):
        live.clear_cache()
        recorded = []
        svc = _service(_Repo())
        monkeypatch.setattr(svc, "_log_radar", lambda day, now, **kw: recorded.append((now, kw)))

        # 模拟上一轮还在写：占住 gate
        live._RADAR_LOG_GATE.acquire()
        try:
            svc._log_radar_async("2026-11-09", "101000", rows=[], broken=[], preboard=[], quotes={}, theme_of={},
                                 hot=[], themes=[], rt={}, inserted=[{"code": "600100", "name": "甲"}], updates={},
                                 overrides=[{"kind": "index", "scope": "all", "key": "index:sh000001"}],
                                 wv_change=None, auction=False, yquotes={}, recording=True,
                                 surge={"at": "100500", "until": "103500", "label": "推荐点"})
        finally:
            live._RADAR_LOG_GATE.release()
        assert recorded == []          # 这一轮没写

        # 下一轮成功留档：排队的事件用原始时刻补写
        svc._log_radar_async("2026-11-09", "101500", rows=[], broken=[], preboard=[], quotes={}, theme_of={},
                             hot=[], themes=[], rt={}, inserted=[], updates={}, overrides=[], wv_change=None,
                             auction=False, yquotes={}, recording=True, surge=None)
        for _ in range(50):
            if recorded:
                break
            import time as _t
            _t.sleep(0.02)
        assert recorded, "补写没有发生"
        now, kw = recorded[0]
        assert now == "101500" and len(kw["queued"]) == 1
        assert kw["queued"][0]["at"] == "101000"
        assert kw["queued"][0]["surge"]["at"] == "100500"
        assert kw["queued"][0]["inserted"][0]["code"] == "600100"

    def test_queue_is_bounded(self):
        live.clear_cache()
        for i in range(live._PENDING_LOG_MAX + 20):
            live.LiveRadarService._queue_log_events(f"10{i:04d}", {"inserted": [{"code": f"6{i:05d}"}]})
        assert len(live._PENDING_LOG_EVENTS) == live._PENDING_LOG_MAX
        live.clear_cache()
        assert live._PENDING_LOG_EVENTS == []
