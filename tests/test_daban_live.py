# -*- coding: utf-8 -*-
"""盘中首板雷达：门控、降级、先到先得的仓位分配、炸板留档、前一交易日上下文、留档与回填。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.services.daban import live
from src.services.daban.forecast import active_themes, build_forecasts, summarize_forecasts

from tests.daban_fixtures import rules, zt_row

PHASE_HIGH = {"available": True, "trade_date": "2026-10-19", "chen_stage": "warm", "chen_label": "回暖期",
              "kobe_gate": "high_shock", "kobe_label": "高位震荡"}
PHASE_RETREAT = {"available": True, "trade_date": "2026-10-19", "chen_stage": "retreat", "chen_label": "退潮期",
                 "kobe_gate": "fall_1", "kobe_label": "主跌第 1 天"}
PHASE_BEST = {"available": True, "trade_date": "2026-10-19", "chen_stage": "warm", "chen_label": "回暖期",
              "kobe_gate": "fall_2", "kobe_label": "主跌第 2 天"}
PHASE_RISE = {"available": True, "trade_date": "2026-10-19", "chen_stage": "ferment", "chen_label": "发酵期",
              "kobe_gate": "main_rise", "kobe_label": "主升"}


def _run(rows, *, phase=PHASE_HIGH, broken=(), quotes=None, memory=None, recent_mains=(), yesterday=(), max_picks=4):
    return live.build_hits(rows=rows, broken=list(broken), phase=phase, rules=rules(), quotes=quotes or {},
                           memory=memory or {}, recent_mains=list(recent_mains), yesterday_codes=list(yesterday),
                           max_picks=max_picks)


def _hit(result, code, style="kobe"):
    return next(h for h in result["hits"] if h["code"] == code and h["style"] == style)


def _robot(code, name, first_time, **kw):
    return zt_row(code, name, reason="人形机器人", first_time=first_time, **kw)


class TestGates:
    def test_kobe_trial_in_high_shock_cites_light_position(self):
        hit = _hit(_run([_robot("600001", "甲", "100000")]), "600001")
        assert hit["action"] == "trial"
        assert {"K08", "K33"} <= {pid for r in hit["reasons"] for pid in r["principle_ids"]}

    def test_kobe_refuses_after_first_fall_day(self):
        result = _run([_robot("600001", "甲", "100000")], phase=PHASE_RETREAT)
        assert not [h for h in result["hits"] if h["style"] == "kobe"]
        assert any(b["style"] == "kobe" and "主跌第 1 天" in b["reason"] for b in result["blocked"])

    def test_kobe_actionable_after_second_fall_day_cites_k09(self):
        hit = _hit(_run([_robot("600001", "甲", "100000")], phase=PHASE_BEST), "600001")
        assert hit["action"] == "actionable"
        assert any("K09" in r["principle_ids"] for r in hit["reasons"])

    def test_chen_needs_new_theme_and_peers(self):
        rows = [_robot("600001", "甲", "100000"), _robot("600002", "乙", "101000"), _robot("600003", "丙", "102000")]
        result = _run(rows)
        assert _hit(result, "600001", "chen")["action"] == "watch"
        assert _hit(result, "600002", "chen")["action"] == "watch"
        third = _hit(result, "600003", "chen")
        assert third["peers"] == 2 and third["action"] == "trial"
        assert any("C14" in r["principle_ids"] for r in third["reasons"])

    def test_chen_new_theme_filter_uses_recent_mains(self):
        rows = [_robot("600001", "甲", "100000"), _robot("600002", "乙", "101000"), _robot("600003", "丙", "102000")]
        third = _hit(_run(rows, recent_mains=["机器人"]), "600003", "chen")
        assert third["action"] == "watch" and any("新题材" in n for n in third["notes"])

    def test_chen_refuses_retreat(self):
        result = _run([_robot("600001", "甲", "100000")], phase=PHASE_RETREAT)
        assert not [h for h in result["hits"] if h["style"] == "chen"]

    def test_only_first_boards(self):
        rows = [_robot("600001", "甲", "100000"), zt_row("600002", "乙", boards=3, reason="人形机器人")]
        assert {h["code"] for h in _run(rows)["hits"]} == {"600001"}


class TestDemotions:
    def test_auction_touch_is_watch(self):
        hit = _hit(_run([_robot("600001", "甲", "092500")]), "600001")
        assert hit["action"] == "watch" and any("开盘一分钟" in n for n in hit["notes"])

    def test_one_word_board_is_watch(self):
        hit = _hit(_run([_robot("600001", "甲", "100000", limit_up_type="一字板")]), "600001")
        assert hit["action"] == "watch"

    def test_late_first_touch_is_watch(self):
        assert _hit(_run([_robot("600001", "甲", "143500")]), "600001")["action"] == "watch"

    def test_late_reseal_does_not_change_the_call(self):
        # 10:00 首次触板、14:50 回封：买点在 10:00，那时看不到 14:50 的事，不能事后降级。
        hit = _hit(_run([_robot("600001", "甲", "100000", last_time="145000", open_num=2)]), "600001")
        assert hit["action"] == "trial"


class TestAllocation:
    def test_first_come_first_served_within_cap(self):
        # 高位震荡：科比线总仓位 30%，试错每只 10% → 只有最早触板的三只拿到名额。
        rows = [_robot(f"6000{i:02d}", f"股{i}", f"10{i:02d}00") for i in range(1, 7)]
        kobe = [h for h in _run(rows)["hits"] if h["style"] == "kobe"]
        funded = [h for h in kobe if h["action"] != "watch"]
        assert [h["code"] for h in funded] == ["600001", "600002", "600003"]
        assert sum(h["position_pct"] for h in funded) == 30
        assert all("用满" in " ".join(h["notes"]) for h in kobe if h["action"] == "watch")

    def test_max_four_picks(self):
        rows = [_robot(f"6000{i:02d}", f"股{i}", f"10{i:02d}00") for i in range(1, 8)]
        result = _run(rows, phase=PHASE_RISE)
        funded = [h for h in result["hits"] if h["style"] == "kobe" and h["action"] != "watch"]
        assert sorted(h["slot"] for h in funded) == [1, 2, 3, 4]
        assert result["lanes"]["kobe"]["picks"] == 4

    def test_configured_max_picks(self):
        rows = [_robot(f"6000{i:02d}", f"股{i}", f"10{i:02d}00") for i in range(1, 8)]
        funded = [h for h in _run(rows, phase=PHASE_RISE, max_picks=2)["hits"]
                  if h["style"] == "kobe" and h["action"] != "watch"]
        assert len(funded) == 2

    def test_broken_first_board_keeps_its_slot(self):
        # 甲 09:40 触板后炸开；乙 10:00 封住。名额先到先得，甲炸了也不把名额让出去。
        broken = [{**_robot("600001", "甲", "094000"), "change_pct": 4.0}]
        result = _run([_robot("600002", "乙", "100000")], broken=broken, phase=PHASE_BEST)
        first = _hit(result, "600001")
        assert first["status"] == "opened" and first["sealed"] is False and first["slot"] == 1
        assert first["seal_amount"] is None
        assert _hit(result, "600002")["action"] == "watch"     # 主跌第二天之后只有一个满额名额
        assert result["stats"]["first_touches"] == 2

    def test_yesterday_limit_up_in_broken_pool_is_not_a_first_board(self):
        broken = [{**_robot("600001", "甲", "094000"), "change_pct": 4.0}]
        assert not _run([], broken=broken, yesterday=["600001"])["hits"]

    def test_lanes_carry_sell_rules(self):
        lanes = _run([_robot("600001", "甲", "100000")])["lanes"]
        assert any("K21" in r["principle_ids"] for r in lanes["kobe"]["sell_rules"])
        assert any("C08" in r["principle_ids"] for r in lanes["chen"]["sell_rules"])


def _locked(*rows):
    return live.locked_from_records([
        {"style": style, "code": code, "name": code, "action": action, "position_pct": pct, "slot": slot,
         "first_time": first, "seen_at": first, "limit_price": 11.0, "theme": "", "phase": "", "open_count": 0}
        for style, code, action, pct, slot, first in rows
    ])


class TestLock:
    """名额一旦给出当天锁定：已给的不动，只分剩下的；晚到的票不会把已锁定的挤掉。"""

    def test_late_arrival_cannot_bump_a_locked_slot(self):
        # 主升：4 个名额。09:42 前已锁定 4 只；09:40 首次触板的票 09:43 才出现在数据里。
        locked = _locked(*[("kobe", f"6000{i}", "trial", 10, i, f"0941{i}0") for i in range(1, 5)])
        rows = [_robot(f"6000{i}", f"股{i}", f"0941{i}0") for i in range(1, 5)] + [_robot("600099", "晚到", "094000")]
        result = live.build_hits(rows=rows, broken=[], phase=PHASE_RISE, rules=rules(), locked=locked)
        late = _hit(result, "600099")
        assert late["action"] == "watch" and "数据晚到" in " ".join(late["notes"])
        kept = [h for h in result["hits"] if h["style"] == "kobe" and h["slot"]]
        assert sorted(h["code"] for h in kept) == ["60001", "60002", "60003", "60004"]
        assert all(h["locked_at"] for h in kept)
        assert result["lanes"]["kobe"]["picks"] == 4 and result["lanes"]["kobe"]["locked"] == 4

    def test_locked_pick_keeps_action_even_if_conditions_change(self):
        # 陈小群线 10:20 锁定了一只「试错」；之后近期主线判定让它不再是新题材，名额照旧。
        locked = _locked(("chen", "600003", "trial", 5, 1, "102000"))
        rows = [_robot("600001", "甲", "100000"), _robot("600002", "乙", "101000"), _robot("600003", "丙", "102000")]
        result = live.build_hits(rows=rows, broken=[], phase=PHASE_HIGH, rules=rules(),
                                 recent_mains=["机器人"], locked=locked)
        hit = _hit(result, "600003", "chen")
        assert hit["action"] == "trial" and hit["slot"] == 1 and hit["position_pct"] == 5

    def test_remaining_slots_and_room_go_to_new_picks(self):
        # 高位震荡：仓位上限 30%，已锁定 2 只各 10%，新票只剩 1 个 10% 的名额，序号接着排。
        locked = _locked(("kobe", "600001", "trial", 10, 1, "100000"), ("kobe", "600002", "trial", 10, 2, "100500"))
        rows = [_robot("600001", "甲", "100000"), _robot("600002", "乙", "100500"),
                _robot("600003", "丙", "101000"), _robot("600004", "丁", "101500")]
        result = live.build_hits(rows=rows, broken=[], phase=PHASE_HIGH, rules=rules(), locked=locked)
        assert _hit(result, "600003")["slot"] == 3 and _hit(result, "600003")["position_pct"] == 10
        assert _hit(result, "600004")["action"] == "watch"
        assert result["lanes"]["kobe"]["used"] == 30

    def test_locked_pick_missing_from_pools_is_still_shown(self):
        locked = _locked(("kobe", "600001", "trial", 10, 1, "100000"))
        result = live.build_hits(rows=[], broken=[], phase=PHASE_HIGH, rules=rules(), locked=locked)
        hit = _hit(result, "600001")
        assert hit["status"] == "missing" and hit["slot"] == 1 and hit["action"] == "trial"
        assert result["lanes"]["kobe"]["picks"] == 1

    def test_records_without_slot_get_sequential_slots(self):
        locked = live.locked_from_records([
            {"style": "kobe", "code": "600002", "action": "trial", "first_time": "100500", "slot": None},
            {"style": "kobe", "code": "600001", "action": "trial", "first_time": "100000", "slot": None},
            {"style": "kobe", "code": "600003", "action": "watch", "first_time": "101000", "slot": None},
        ])
        assert {code: row["slot"] for code, row in locked["kobe"].items()} == {"600001": 1, "600002": 2}


class TestFacts:
    def test_quote_confirms_seal_without_citation(self):
        quotes = {"600001": {"price": 11.0, "limit_up": 11.0, "sell1_volume": 0}}
        hit = _hit(_run([_robot("600001", "甲", "100000", price=11.0)], quotes=quotes), "600001")
        assert hit["sealed"] is True and hit["limit_price"] == 11.0
        assert not any("盘口" in r["text"] for r in hit["reasons"])

    def test_limit_price_derived_without_quote(self):
        assert live.limit_price_of({"code": "600001", "price": 11.0, "change_pct": 10.0}) == 11.0
        assert live.limit_price_of({"code": "300001", "price": 9.5, "change_pct": -5.0}) == 12.0

    def test_peers_have_no_lookahead(self):
        result = _run([_robot("600001", "甲", "100000"), _robot("600002", "乙", "110000")])
        assert _hit(result, "600001")["peers"] == 0 and _hit(result, "600002")["peers"] == 1


# ---------------------------------------------------------------------- 服务层


class _Repo:
    def __init__(self, snapshot=None):
        self.snapshot = snapshot
        self.live = {}

    def snapshot_for_trade_date(self, day):
        return self.snapshot if self.snapshot and self.snapshot.trade_date == day else None

    def history(self, *, limit=10, before=None):
        return []

    def pool_rows(self, day, pool="zt"):
        return [{"code": "600009"}]

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
        }))
        ctx = _service(_Repo(snap)).previous_context("2026-10-20")
        assert ctx["source"] == "review" and ctx["kobe_gate"] == "fall_2"
        assert ctx["yesterday_codes"] == ["600009"]

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


class TestRecordingAndPush:
    def test_first_sighting_is_recorded_once(self):
        repo = _Repo()
        svc = _service(repo)
        hits = _run([_robot("600001", "甲", "100000")])["hits"]
        assert [h["code"] for h in svc._record("2026-10-20", hits)] == ["600001"]
        assert svc._record("2026-10-20", hits) == []
        row = repo.live["2026-10-20"][("kobe", "600001")]
        assert row["origin"] == "live" and row["limit_price"] and row["seen_at"]

    def test_recorded_slots_survive_the_next_refresh(self):
        """第一轮锁定 3 只（高位震荡 30%）；第二轮数据里多了一只更早触板的，名单不变、不重复留档。"""
        repo = _Repo()
        svc = _service(repo)
        first = [_robot(f"6000{i}", f"股{i}", f"10{i}000") for i in range(1, 4)]
        hits = live.build_hits(rows=first, broken=[], phase=PHASE_HIGH, rules=rules(), locked=svc.locked("2026-10-20"))["hits"]
        recorded = svc._record("2026-10-20", hits)
        assert sorted(h["code"] for h in recorded if h["style"] == "kobe") == ["60001", "60002", "60003"]
        second = first + [_robot("600099", "晚到", "095900")]
        result = live.build_hits(rows=second, broken=[], phase=PHASE_HIGH, rules=rules(), locked=svc.locked("2026-10-20"))
        assert _hit(result, "600099")["action"] == "watch"
        second_round = svc._record("2026-10-20", result["hits"])
        # 科比线名额已用满：不新增、不改动。
        assert not [h for h in second_round if h["style"] == "kobe"]
        assert sorted(code for style, code in repo.live["2026-10-20"] if style == "kobe") == ["60001", "60002", "60003"]
        # 陈小群线还有剩余仓位：晚到的同伴让 60002 此刻满足「资金合力」，可以拿剩下的名额，序号接在后面。
        chen_new = [h for h in second_round if h["style"] == "chen"]
        assert [h["code"] for h in chen_new] == ["60002"] and chen_new[0]["slot"] == 2

    def test_push_is_deduplicated(self, monkeypatch):
        import src.notification as notification

        sent = []

        class _Notifier:
            def send_with_results(self, content, **kw):
                sent.append(kw["dedup_key"])

        monkeypatch.setattr(notification, "NotificationService", lambda: _Notifier())
        monkeypatch.setattr(notification.NotificationBuilder, "build_simple_alert",
                            staticmethod(lambda **kw: kw["content"]))
        live.clear_cache()
        svc = _service(_Repo(), daban_live_push_enabled=True)
        hits = [h for h in _run([_robot("600001", "甲", "100000")])["hits"] if h["action"] != "watch"]
        svc._notify("2026-10-20", hits, PHASE_HIGH)
        svc._notify("2026-10-20", hits, PHASE_HIGH)
        assert sent == ["daban-live:2026-10-20:kobe:600001"]

    def test_push_disabled_sends_nothing(self, monkeypatch):
        import src.notification as notification

        monkeypatch.setattr(notification, "NotificationService", lambda: (_ for _ in ()).throw(AssertionError))
        hits = [h for h in _run([_robot("600001", "甲", "100000")])["hits"] if h["action"] != "watch"]
        _service(_Repo())._notify("2026-10-20", hits, PHASE_HIGH)

    def test_alert_text_has_sell_rules(self):
        text = live.render_hit_alert(_hit(_run([_robot("600001", "甲", "100000")]), "600001"))
        assert "卖出纪律" in text and "K21" in text and "不构成投资建议" in text


class TestReview:
    def test_review_waits_for_next_close(self, monkeypatch):
        from src.services.daban import fetchers

        calls = []

        class Repo:
            def pending_live_dates(self):
                return ["2026-10-20"]

            def live_hits(self, day):
                return [{"code": "600001", "limit_price": 11.0, "reviewed": False}]

            def update_live_outcomes(self, day, outcomes):
                calls.append(outcomes)

        monkeypatch.setattr(fetchers, "next_trade_date", lambda day, **kw: "2026-10-21")
        monkeypatch.setattr(fetchers, "fetch_klines", lambda codes, day, *, limit, timeout: {
            "value": {"rows": {"600001": [{"time": "20261021", "open": 11.55, "close": 12.1}]}}})
        assert live.review_pending_live_hits(Repo(), today="2026-10-21", now="140000") == 0
        assert live.review_pending_live_hits(Repo(), today="2026-10-21", now="151000") == 1
        assert calls[0]["600001"] == {"open_ret": 5.0, "close_ret": 10.0}


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


@pytest.mark.parametrize("now,day,expected", [
    ("093000", "2026-10-20", True), ("145959", "2026-10-20", True),
    ("091000", "2026-10-20", False), ("150100", "2026-10-20", False), ("100000", "2026-10-18", False),
])
def test_market_open_window(now, day, expected):
    assert live.market_open_now(now, day=day) is expected
