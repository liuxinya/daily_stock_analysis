# -*- coding: utf-8 -*-
"""盘中时序留档：候选口径不看门控、seen 只记一次、状态变化、定时采样、进程重启后接着记。"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from src.services.daban import live
from src.services.daban import radar_log
from src.services.daban.radar_log import RadarRecorder

from tests.daban_fixtures import rules, zt_row


class _Repo:
    def __init__(self, restored=None):
        self.events: List[Dict[str, Any]] = []
        self.restored = restored or {}

    def add_radar_events(self, day, events):
        self.events.extend({**e, "trade_date": day} for e in events)
        return len(events)

    def radar_state(self, day):
        return self.restored


def _cand(code, status="candidate", *, theme="机器人", price=10.0, limit_up=11.0):
    return {"code": code, "name": f"票{code}", "status": status, "price": price, "limit_up": limit_up,
            "theme_key": theme, "change_pct": 4.0}


def _kinds(repo, code=None):
    return [(e["kind"], e.get("code") or "") for e in repo.events if code is None or e.get("code") == code]


@pytest.fixture(autouse=True)
def _fresh_state():
    # 进程内状态按交易日保留，测试之间要清掉，否则上一个用例的采样时刻会让下一个「还没到时间」。
    radar_log.clear_state()
    yield
    radar_log.clear_state()


class TestRecorder:
    def test_seen_once_then_state_changes(self):
        repo = _Repo()
        rec = RadarRecorder(repo, rules(), fetch_quotes=lambda codes: {})
        rec.record("2026-10-20", "093500", candidates=[_cand("600001")])
        rec.record("2026-10-20", "093510", candidates=[_cand("600001")])
        rec.record("2026-10-20", "094000", candidates=[_cand("600001", "sealed", price=11.0)])
        rec.record("2026-10-20", "100000", candidates=[_cand("600001", "opened", price=10.8)])
        rec.record("2026-10-20", "103000", candidates=[_cand("600001", "sealed", price=11.0)])
        assert [k for k, _ in _kinds(repo, "600001") if k != "sample"] == ["seen", "seal", "open", "reseal"]
        seen = repo.events[0]
        assert seen["data"]["hot"] is True and seen["gap_pct"] == 10.0 and seen["theme"] == "机器人"

    def test_cold_candidates_are_seen_once_and_promoted_when_hot(self):
        """不在热点题材里的票只记一条 seen；之后进了热点，再记一条 hot=True 的 seen 并开始跟踪。"""
        repo = _Repo()
        rec = RadarRecorder(repo, rules(), fetch_quotes=lambda codes: {})
        rec.record("2026-10-20", "093500", candidates=[_cand("600002", theme="")])
        rec.record("2026-10-20", "093600", candidates=[_cand("600002", theme="")])
        rec.record("2026-10-20", "094000", candidates=[_cand("600002", "sealed", theme="")])
        assert _kinds(repo, "600002") == [("seen", "600002")]
        rec.record("2026-10-20", "100000", candidates=[_cand("600002", theme="锂电")])
        assert [e["data"]["hot"] for e in repo.events if e["code"] == "600002" and e["kind"] == "seen"] == [False, True]

    def test_samples_every_interval_even_when_off_the_feed(self):
        """热点候选从异动列表里消失后照样按时采样（价格路径要完整），封住的不再采。"""
        repo = _Repo()
        quotes = {"600001": {"price": 10.5, "limit_up": 11.0, "prev_close": 10.0, "high": 10.6, "sell1_volume": 300},
                  "600003": {"price": 11.0, "limit_up": 11.0, "prev_close": 10.0, "sell1_volume": 0}}
        asked = []

        def fetch(codes):
            asked.append(list(codes))
            return {c: quotes[c] for c in codes if c in quotes}

        rec = RadarRecorder(repo, rules(), fetch_quotes=fetch)
        rec.record("2026-10-20", "093500", candidates=[_cand("600001"), _cand("600003", "sealed")])
        rec.record("2026-10-20", "093700", candidates=[])            # 不到 5 分钟：不采样
        rec.record("2026-10-20", "094100", candidates=[])            # 到了：600001 不在本轮也要采
        samples = [e for e in repo.events if e["kind"] == "sample"]
        assert [s["at"] for s in samples if s["code"] == "600001"] == ["093500", "094100"]
        assert not [s for s in samples if s["code"] == "600003"]     # 已封住的不采
        last = samples[-1]
        assert last["gap_pct"] == round((11.0 / 10.5 - 1) * 100, 3) and last["data"]["change_pct"] == 5.0
        assert last["data"]["sell1_volume"] == 300 and asked[-1] == ["600001"]

    def test_quote_failure_skips_the_sample_and_retries(self):
        repo = _Repo()
        calls = {"n": 0}

        def flaky(codes):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("down")
            return {"600001": {"price": 10.2, "limit_up": 11.0, "prev_close": 10.0}}

        rec = RadarRecorder(repo, rules(), fetch_quotes=flaky)
        rec.record("2026-10-20", "093500", candidates=[_cand("600001")])
        rec.record("2026-10-20", "093510", candidates=[_cand("600001")])
        assert [e["at"] for e in repo.events if e["kind"] == "sample"] == ["093510"]

    def test_theme_and_market_snapshots_are_throttled(self):
        repo = _Repo()
        rec = RadarRecorder(repo, rules(), fetch_quotes=lambda c: {},
                            fetch_indices=lambda: {"sh000001": {"price": 3650.0, "change_pct": 0.8}})
        for now in ("093500", "093530", "093640", "094100"):
            rec.record("2026-10-20", now, candidates=[], theme_snapshot={"hot": []}, market={"zt_count": 20})
        assert [e["at"] for e in repo.events if e["kind"] == "market"] == ["093500", "093640", "094100"]
        assert [e["at"] for e in repo.events if e["kind"] == "theme"] == ["093500", "094100"]
        market = next(e for e in repo.events if e["kind"] == "market")
        assert market["data"]["indices"]["sh000001"]["change_pct"] == 0.8 and not market.get("code")

    def test_index_failure_is_recorded_not_raised(self):
        repo = _Repo()

        def boom():
            raise RuntimeError("tencent down")

        RadarRecorder(repo, rules(), fetch_indices=boom).record(
            "2026-10-20", "093500", candidates=[], market={"zt_count": 3})
        assert "tencent down" in repo.events[0]["data"]["indices_error"]

    def test_restart_resumes_from_stored_state(self):
        """进程重启：已经 seen 过的不再记 seen，已封住的再报封住不重复记 seal。"""
        repo = _Repo(restored={"tracked": {"600001": {"status": "sealed"}}, "cold": {"600002"},
                               "sample_last": "093500", "theme_last": "093500", "market_last": "093500"})
        rec = RadarRecorder(repo, rules(), fetch_quotes=lambda c: {})
        rec.record("2026-10-20", "093530", candidates=[_cand("600001", "sealed"), _cand("600002", theme="")],
                   theme_snapshot={}, market={})
        assert repo.events == []

    def test_disabled_writes_nothing(self):
        repo = _Repo()
        cfg = rules()
        cfg["radar_log"] = {**cfg["radar_log"], "enabled": False}
        RadarRecorder(repo, cfg).record("2026-10-20", "093500", candidates=[_cand("600001")], market={})
        assert repo.events == []


class TestCandidates:
    HOT = [{"key": "机器人概念", "name": "机器人概念", "zt_count": 6, "rank": 1}]

    def test_universe_ignores_style_gates(self):
        """不看边界和分数：涨停池、炸板池、距涨停 ≤6% 的未涨停候选都进留档候选。"""
        rows = [zt_row("600001", "封住", reason="人形机器人")]
        broken = [zt_row("600002", "炸了", reason="")]
        preboard = [
            {"code": "600003", "name": "近", "price": 10.5, "limit_up": 11.0, "prev_close": 10.0, "open": 10.1},
            {"code": "600004", "name": "远", "price": 10.0, "limit_up": 11.0, "prev_close": 10.0},   # 距涨停 10%
            {"code": "600001", "name": "重复", "price": 11.0, "limit_up": 11.0},
        ]
        cands = live.radar_candidates(
            rows=rows, broken=broken, preboard=preboard, quotes={}, theme_of={"600003": "机器人概念"}, gap_max=6.0,
        )
        by = {c["code"]: c for c in cands}
        assert set(by) == {"600001", "600002", "600003"}
        assert by["600001"]["status"] == "sealed" and by["600002"]["status"] == "opened"
        assert by["600003"]["status"] == "candidate" and by["600003"]["open_pct"] == 1.0
        assert by["600003"]["theme_key"] == "机器人概念"            # 题材归属由雷达这一轮给
        assert by["600002"]["theme_key"] == ""                         # 无原因、无记忆：冷门

    def test_theme_snapshot_lists_two_leaders(self):
        themes = [{"key": "机器人", "rank": 1, "zt_count": 3, "max_boards": 2, "lianban_count": 1,
                   "members": [{"code": "600001", "boards": 2, "first_time": "093500"},
                               {"code": "600002", "boards": 1, "first_time": "094000"},
                               {"code": "600003", "boards": 1, "first_time": "100000"}]}]
        quotes = {"600001": {"price": 11.0, "limit_up": 11.0, "sell1_volume": 0}}
        snap = live.radar_theme_snapshot(self.HOT, themes, rows=[zt_row("600001", "龙一", open_num=1)],
                                         quotes=quotes)
        leaders = snap["themes"][0]["leaders"]
        assert [l["code"] for l in leaders] == ["600001", "600002"]
        assert leaders[0]["sealed"] is True and leaders[0]["open_num"] == 1 and leaders[1]["sealed"] is None


class TestAuction:
    def test_auction_is_recorded_once_per_interval(self):
        repo = _Repo()
        rec = RadarRecorder(repo, rules())
        quotes = {"600001": {"name": "甲", "price": 9.8, "prev_close": 10.0, "limit_up": 11.0, "volume": 1200.0}}
        assert rec.record_auction("2026-10-20", "091600", quotes=quotes) == 1
        assert rec.record_auction("2026-10-20", "091630", quotes=quotes) == 0      # 不到 60 秒
        assert rec.record_auction("2026-10-20", "091700", quotes=quotes) == 1
        event = repo.events[0]
        assert event["kind"] == "auction" and event["data"] == {"change_pct": -2.0, "volume": 1200.0}

    def test_extra_events_are_written_as_given(self):
        repo = _Repo()
        rec = RadarRecorder(repo, rules())
        rec.record("2026-10-20", "100000", candidates=[], extra=[{"at": "100000", "kind": "score", "code": "600001",
                                                                   "data": {"from": 5, "to": 0}}])
        assert _kinds(repo) == [("score", "600001")]


class TestServiceWiring:
    """``LiveRadarService._log_radar`` 把失败吞掉只记日志，所以要用真实库确认它确实写进去了。"""

    def test_log_radar_writes_events_to_the_real_table(self, tmp_path, monkeypatch):
        from types import SimpleNamespace

        from data_provider import limit_up_fetcher as lu
        from src.config import Config
        from src.repositories.daban_repo import DabanRepository
        from src.storage import DatabaseManager

        monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "radar.db"))
        Config._instance = None
        DatabaseManager.reset_instance()
        try:
            live.clear_cache()
            monkeypatch.setattr(lu, "fetch_tencent_quotes", lambda codes, timeout=None: {
                c: {"price": 10.4, "limit_up": 11.0, "prev_close": 10.0, "sell1_volume": 100} for c in codes})
            monkeypatch.setattr(lu, "fetch_tencent_index_quotes", lambda symbols=None, timeout=None: {
                "sh000001": {"price": 3650.0, "change_pct": 0.5, "high": 3660.0, "low": 3630.0}})
            repo = DabanRepository()
            svc = live.LiveRadarService(repository=repo, config=SimpleNamespace(
                daban_fetch_timeout_sec=5, daban_rules_file=""))
            preboard = [{"code": "600003", "name": "近", "price": 10.5, "limit_up": 11.0, "prev_close": 10.0,
                         "open": 10.1, "change_pct": 5.0}]
            rows = [zt_row("600001", "封住", reason="人形机器人", first_time="093500")]
            themes = [{"key": "机器人", "rank": 1, "zt_count": 1,
                       "members": [{"code": "600001", "boards": 1, "first_time": "093500"}]}]
            hot = [{"key": "机器人概念", "name": "机器人概念", "zt_count": 5, "rank": 1}]
            focus_row = {"code": "600003", "name": "近", "theme_key": "机器人概念", "cost": 10.5, "entry_gap_pct": 4.76,
                         "init_score": 5, "group_key": "normal", "entry_status": "preboard",
                         "init_detail": {"items": [{"key": "open_gap2_5"}]}}
            svc._log_radar("2026-10-20", "094000", rows=rows, broken=[], preboard=preboard, quotes={},
                           theme_of={"600001": "机器人概念", "600003": "机器人概念"}, hot=hot, themes=themes,
                           rt={"metrics": {"zt_count": 1}, "frozen": False}, inserted=[focus_row],
                           updates={}, overrides=[{"key": "index:sh000001", "scope": "all", "label": "急拉"}],
                           wv_change=None, auction=False, yquotes={}, recording=True)
            kinds = sorted({(e["kind"], e["code"]) for e in repo.radar_events("2026-10-20")})
            assert ("seen", "600001") in kinds and ("seen", "600003") in kinds
            assert ("sample", "600003") in kinds and ("market", "") in kinds and ("theme", "") in kinds
            assert ("focus", "600003") in kinds and ("override", "") in kinds
            market = repo.radar_events("2026-10-20", kinds=["market"])[0]["data"]
            assert market["indices"]["sh000001"]["change_pct"] == 0.5 and market["zt_count"] == 1
        finally:
            DatabaseManager.reset_instance()
            Config._instance = None

    def test_async_logging_skips_while_previous_round_is_writing(self, monkeypatch):
        """留档在后台线程写；上一轮还没写完时这一轮直接跳过，不排队、不阻塞页面。"""
        import threading
        from types import SimpleNamespace

        calls = []
        done = threading.Event()

        def fake_log(self, pool_date, now, **kwargs):
            calls.append(now)
            done.set()

        monkeypatch.setattr(live.LiveRadarService, "_log_radar", fake_log)
        svc = live.LiveRadarService(repository=_Repo(), config=SimpleNamespace(daban_rules_file=""))
        assert live._RADAR_LOG_GATE.acquire(blocking=False)
        try:
            svc._log_radar_async("2026-10-20", "094000")
        finally:
            live._RADAR_LOG_GATE.release()
        assert calls == []
        svc._log_radar_async("2026-10-20", "094010")
        assert done.wait(5) and calls == ["094010"]
        for _ in range(50):                       # 线程结束后闸门要放开
            if live._RADAR_LOG_GATE.acquire(blocking=False):
                live._RADAR_LOG_GATE.release()
                break
            threading.Event().wait(0.05)
        else:
            raise AssertionError("留档线程结束后闸门没有释放")
