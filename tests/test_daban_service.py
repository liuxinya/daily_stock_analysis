# -*- coding: utf-8 -*-
"""编排层：抓取降级、按交易日覆盖落库、冷启动回补、短线世界观、次日观察清单、「重点」推荐收盘复盘。

所有上游都打桩在 ``src.services.daban.service`` 模块上（服务层是 ``from ... import`` 直接绑定名字的，
打在 fetchers 模块上不生效）；数据库用临时 SQLite，模型用返回空串的桩（走规则兜底）。
"""

from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.services.daban import service as service_mod
from src.services.daban.analyzer import DabanAnalyzer

from tests.daban_fixtures import prev_row, robot_day, zt_row


def _env(key, rows=None, *, ok=True, source="同花顺涨停数据中心", as_of="2026-10-20", **value):
    payload = dict(value)
    if rows is not None:
        payload["rows"] = rows
    return {"key": key, "label": key, "ok": ok, "source": source, "as_of": as_of, "degraded": not ok,
            "degraded_reason": "" if ok else "down", "error": None if ok else "down", "value": payload}


@pytest.fixture()
def db(monkeypatch):
    from src.config import Config
    from src.storage import DatabaseManager

    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("DATABASE_PATH", os.path.join(tmp, "daban.db"))
        Config._instance = None
        DatabaseManager.reset_instance()
        yield
        DatabaseManager.reset_instance()
        Config._instance = None


@pytest.fixture()
def upstream(monkeypatch):
    """可按日期定制的上游桩：``state["zt"][date]`` 等。"""
    state = {
        "zt": {"2026-10-20": robot_day(), "2026-10-19": [zt_row("002889", "东方嘉盛", boards=3, reason="机器人概念")]},
        "previous": {"2026-10-20": [prev_row("002889", "东方嘉盛", y_boards=3, change_pct=-6.7),
                                    prev_row("603278", "大业股份", y_boards=1, change_pct=10.0)]},
        "ladder": {"2026-10-20": {"003029": 4, "600178": 3, "600860": 3, "603278": 2}},
        "fail": set(),
    }

    def fetch_limit_pool(kind, trade_date, *, timeout, include_bse):
        day = trade_date or "2026-10-20"
        if kind in state["fail"]:
            return _env(kind, ok=False, as_of=day)
        rows = [dict(r) for r in state.get(kind, {}).get(day, [])] if kind == "zt" else []
        return _env(kind, rows, as_of=day, counts={}, trade_status="closed")

    def fetch_ladder(trade_date, *, timeout):
        if "ladder" in state["fail"]:
            return _env("ladder", ok=False)
        return _env("ladder", ladder=state["ladder"].get(trade_date, {}))

    def fetch_em(kind, trade_date, *, timeout, include_bse):
        if kind in state["fail"]:
            return _env(kind, ok=False)
        return _env(kind, [dict(r) for r in state.get(kind, {}).get(trade_date, [])], source="东方财富")

    monkeypatch.setattr(service_mod, "fetch_limit_pool", fetch_limit_pool)
    monkeypatch.setattr(service_mod, "fetch_ladder", fetch_ladder)
    monkeypatch.setattr(service_mod, "fetch_em", fetch_em)
    monkeypatch.setattr(service_mod, "fetch_blocks", lambda trade_date, *, timeout: _env("blocks", blocks=[]))
    monkeypatch.setattr(service_mod, "fetch_hot_rank", lambda *, timeout: _env("hot", ranks={"003029": 3}))
    monkeypatch.setattr(service_mod, "fetch_klines", lambda codes, day, *, limit, timeout: _env("klines", rows={}))
    monkeypatch.setattr(service_mod, "fetch_theme_news", lambda names, *, timeout: _env("news", news={}))
    # 指数与个股新闻不走外网
    monkeypatch.setattr(service_mod.DabanService, "_indices", staticmethod(lambda: {}))
    monkeypatch.setattr(service_mod.DabanService, "_stock_news", lambda self, recs, day: {})
    monkeypatch.setattr(service_mod, "next_trade_date", lambda day, **kw: {"2026-10-19": "2026-10-20"}.get(day, "2026-10-21"))
    return state


def _service(**config):
    cfg = SimpleNamespace(
        daban_enabled=True, daban_include_bse=False, daban_max_actionable=4, daban_rules_file="",
        daban_llm_timeout_sec=10, daban_reasoning_effort="", daban_retention_days=180,
    )
    for key, value in config.items():
        setattr(cfg, key, value)
    llm = Mock()
    llm.generate_text_with_metadata.return_value = SimpleNamespace(text="", model="stub", provider="stub")
    llm.get_generation_backend_identity.return_value = ("stub", "stub")
    svc = service_mod.DabanService(analyzer=DabanAnalyzer(analyzer=llm, config=cfg), config=cfg)
    svc.rules["backfill_days"] = 0
    return svc


class TestRefresh:
    def test_full_payload_and_persistence(self, db, upstream):
        svc = _service()
        payload = svc.refresh(trade_date="2026-10-20")
        assert payload["kind"] == "full" and payload["trade_date"] == "2026-10-20"
        assert payload["metrics"]["max_height"] == 4         # 连板数来自天梯
        assert "lanes" not in payload and "verdict" not in payload   # 两条推荐线与打法裁决已退役
        assert len(payload["worldview"]["dims"]) == 11 and payload["worldview"]["for_date"] == "2026-10-21"
        assert payload["watchlist"]["items"] and payload["watchlist"]["for_date"] == "2026-10-21"
        assert payload["conclusion"]["generated_by"] == "rule_fallback" and payload["conclusion"]["big_picture"]["text"]
        assert any(e["code"] == "600178" for e in payload["excluded"])   # 中位股
        # 读侧只读库。
        stored = svc.overview()
        assert stored["trade_date"] == "2026-10-20" and stored["cached"] is True
        assert svc.overview("2026-10-19") is None

    def test_same_trade_date_overwrites(self, db, upstream):
        svc = _service()
        svc.refresh(trade_date="2026-10-20")
        svc.refresh(trade_date="2026-10-20")
        history = svc.history(limit=10)
        assert [item["trade_date"] for item in history] == ["2026-10-20"]

    def test_side_source_failure_only_degrades(self, db, upstream):
        upstream["fail"].update({"zb", "previous"})
        payload = _service().refresh(trade_date="2026-10-20")
        assert payload["degraded"]
        assert payload["metrics"]["yzt_premium"] is None
        assert any("previous" in w for w in payload["warnings"])

    def test_missing_ladder_infers_boards_from_high_days(self, db, upstream):
        upstream["fail"].add("ladder")
        payload = _service().refresh(trade_date="2026-10-20")
        assert payload["metrics"]["max_height"] == 4         # robot_day 的 hd_days == hd_boards
        assert payload["sources"]["ladder"]["degraded_reason"]

    def test_core_pool_failure_raises(self, db, upstream):
        upstream["fail"].add("zt")
        with pytest.raises(service_mod.DabanUpstreamError):
            _service().refresh(trade_date="2026-10-20")

    def test_disabled(self, db, upstream):
        with pytest.raises(service_mod.DabanUnavailableError):
            _service(daban_enabled=False).overview()


class TestBackfillAndReview:
    def test_backfill_writes_light_snapshots_without_calling_the_model(self, db, upstream, monkeypatch):
        monkeypatch.setattr(service_mod, "recent_trade_dates", lambda end, count: ["2026-10-19"])
        svc = _service()
        svc.rules["backfill_days"] = 1
        svc.refresh(trade_date="2026-10-20")
        kinds = {item["trade_date"]: item["kind"] for item in svc.history(limit=10)}
        assert kinds == {"2026-10-19": "backfill", "2026-10-20": "full"}
        assert svc.analyzer.analyzer.generate_text_with_metadata.call_count == 1
        # 回补日不是完整复盘，overview 按日期读到的是轻量记录，接口层会当作未生成。
        assert svc.overview("2026-10-19")["kind"] == "backfill"

    def test_worldview_is_reconciled_the_next_day(self, db, upstream):
        svc = _service()
        svc.refresh(trade_date="2026-10-19")
        payload = svc.refresh(trade_date="2026-10-20")
        check = payload["worldview_check"]
        assert check and check["for_date"] == "2026-10-20"
        assert payload["worldview_accuracy"]["days"] >= 1

    def test_no_picks_are_written(self, db, upstream):
        svc = _service()
        svc.refresh(trade_date="2026-10-20")
        assert svc.repository.picks_for("2026-10-20") == []


class TestFocusReview:
    def test_close_scores_seal_and_review(self, db, upstream):
        """收盘：收盘分 = 最后一次实时分，是否封住按收盘涨停池写；复盘列出逐项归因和分组成绩。"""
        svc = _service()
        svc.repository.add_recommendations("2026-10-20", [
            {"code": "603278", "name": "大业股份", "cost": 10.5, "init_score": 5, "live_score": 0,
             "entered_at": "100000", "entry_status": "preboard", "group_key": "normal",
             "init_detail": {"items": [{"key": "open_gap2_5", "points": 5, "label": "高开 2~5%"}]},
             "live_detail": {"items": []}},
            {"code": "600999", "name": "没封", "cost": 8.0, "init_score": 0, "live_score": 0, "entered_at": "103000",
             "entry_status": "opened", "group_key": "observe"},
        ])
        payload = svc.refresh(trade_date="2026-10-20")
        rows = {r["code"]: r for r in svc.repository.recommendations("2026-10-20")}
        assert rows["603278"]["sealed_close"] is True and rows["603278"]["close_score"] == 0
        assert rows["600999"]["sealed_close"] is False
        review = payload["focus_review"]
        assert review["count"] == 2
        item = next(i for i in review["items"] if i["code"] == "603278")
        assert item["attribution"]["diff"] == [{"key": "open_gap2_5", "label": "高开 2~5%", "init": 5, "close": None}]
        assert review["stats"]["by_group"]["observe"]["n"] == 1

    def test_legacy_live_stats_until_twenty_new_days(self, db, upstream):
        """旧雷达成绩：新口径没满 20 个交易日时折叠展示，满了就不再给。"""
        svc = _service()
        svc.repository.upsert_live_hits("2026-10-16", [{
            "style": "kobe", "code": "600001", "name": "甲", "action": "trial", "first_time": "", "found_at": "100000",
            "seen_at": "100001", "limit_price": 11.0, "slot": 1, "sealed_close": True, "origin": "live"}], final=True)
        svc.repository.update_live_outcomes("2026-10-16", {("kobe", "600001"): {"open_ret": 2.0, "close_ret": -1.0}})
        payload = svc.refresh(trade_date="2026-10-20")
        stat = payload["legacy_live_stats"]["kobe"]
        assert stat["picks"] == 1 and stat["close_mean"] == -1.0 and stat["label"] == "科比线"
        assert svc._legacy_live_stats("2026-10-20", {"stats": {"all": {"days": 20}}}) == {}


class TestMissedCloseDays:
    """收盘后没生成过复盘的那些天：下一次生成时补上收盘数据。"""

    def test_backfill_replaces_an_intraday_only_snapshot(self, db, upstream, monkeypatch):
        """前一天只在盘中生成过：那份指标是盘中某一刻的。冷启动回补把它当作缺失，用收盘数据重算并补池子。"""
        monkeypatch.setattr(service_mod, "recent_trade_dates", lambda end, count: ["2026-10-19"])
        svc = _service()
        svc.repository.save_snapshot({
            "trade_date": "2026-10-19", "kind": "full", "intraday": True, "stage": "ice", "zt_count": 4,
            "payload": "{}",
        })
        svc.repository.upsert_live_hits("2026-10-19", [{
            "style": "kobe", "code": "002889", "name": "东方嘉盛", "action": "trial", "first_time": "",
            "found_at": "100000", "seen_at": "100005", "limit_price": 16.98, "seen_price": 16.79, "slot": 1,
            "sealed_close": None, "theme": "机器人", "origin": "live",
        }], final=False)
        assert svc.repository.known_trade_dates(["2026-10-19"], closed_only=True) == set()
        svc.rules["backfill_days"] = 1
        svc.refresh(trade_date="2026-10-20")
        row = svc.repository.snapshot_for_trade_date("2026-10-19")
        assert row.kind == "backfill" and not row.intraday and row.zt_count == 1
        assert [r["code"] for r in svc.repository.pool_rows("2026-10-19", "zt")] == ["002889"]
        assert svc.repository.live_hits("2026-10-19")[0]["sealed_close"] is True
        # 收盘后的完整快照仍然不会被回补覆盖。
        assert svc.repository.known_trade_dates(["2026-10-20"], closed_only=True) == {"2026-10-20"}

    def test_stored_pools_reconcile_old_live_hits(self, db, upstream):
        """旧雷达留档（已停写）没对过账：生成时用本地池子补，不访问上游。"""
        svc = _service()
        svc.repository.save_pool_rows("2026-10-16", "zt", [zt_row("600001", "封住", first_time="100500")])
        svc.repository.save_pool_rows("2026-10-16", "zb", [zt_row("600002", "炸了")])
        svc.repository.upsert_live_hits("2026-10-16", [
            {"style": "kobe", "code": code, "name": code, "action": "trial", "first_time": "", "found_at": "100000",
             "seen_at": "100001", "limit_price": 11.0, "slot": slot, "sealed_close": None, "origin": "live"}
            for slot, code in ((1, "600001"), (2, "600002"))
        ], final=False)
        svc.refresh(trade_date="2026-10-20")
        rows = {r["code"]: r for r in svc.repository.live_hits("2026-10-16")}
        assert rows["600001"]["sealed_close"] is True and rows["600001"]["first_time"] == "100500"
        assert rows["600002"]["sealed_close"] is False


def test_radar_events_round_trip_and_retention(db):
    from src.repositories.daban_repo import DabanRepository

    repo = DabanRepository()
    repo.add_radar_events("2026-10-20", [
        {"at": "093500", "kind": "seen", "code": "600001", "name": "甲", "theme": "机器人", "price": 10.0,
         "gap_pct": 4.5, "data": {"hot": True, "status": "candidate"}},
        {"at": "093600", "kind": "seen", "code": "600002", "data": {"hot": False}},
        {"at": "094000", "kind": "seal", "code": "600001", "data": {"first_time": "094000"}},
        {"at": "094000", "kind": "market", "data": {"zt_count": 12}},
    ])
    events = repo.radar_events("2026-10-20", code="600001")
    assert [e["kind"] for e in events] == ["seen", "seal"] and events[0]["data"]["hot"] is True
    state = repo.radar_state("2026-10-20")
    assert state["tracked"] == {"600001": {"status": "sealed"}} and state["cold"] == {"600002"}
    assert state["market_last"] == "094000" and state["sample_last"] == ""
    # 保留策略按「今天往前 N 天」算，用一个足够早的日期验证会被清掉。
    repo.add_radar_events("2020-01-06", [{"at": "093500", "kind": "market", "data": {}}])
    repo.apply_retention(30)
    assert repo.radar_events("2020-01-06") == []


def test_existing_live_hit_table_gets_new_columns(tmp_path, monkeypatch):
    """早期版本建的 daban_live_hits 没有 slot 等列：启动时自动补列，不丢数据。"""
    import sqlite3

    from src.config import Config
    from src.storage import DatabaseManager

    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE daban_live_hits (id INTEGER PRIMARY KEY, trade_date VARCHAR(16) NOT NULL, "
            "style VARCHAR(8) NOT NULL, code VARCHAR(16) NOT NULL, name VARCHAR(64), action VARCHAR(16), "
            "first_time VARCHAR(8), limit_price FLOAT, sealed_close BOOLEAN, open_count INTEGER, theme VARCHAR(64), "
            "phase VARCHAR(32), next_open_ret FLOAT, next_close_ret FLOAT, reviewed_at DATETIME, created_at DATETIME)"
        )
        conn.execute("INSERT INTO daban_live_hits (trade_date, style, code, action) VALUES ('2026-10-16','kobe','600001','trial')")
    monkeypatch.setenv("DATABASE_PATH", str(path))
    Config._instance = None
    DatabaseManager.reset_instance()
    try:
        from src.repositories.daban_repo import DabanRepository

        rows = DabanRepository().live_hits("2026-10-16")
        assert rows == [{**rows[0], "code": "600001", "slot": None, "origin": None}]
        with sqlite3.connect(path) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(daban_live_hits)")}
        assert {"slot", "seen_at", "position_pct", "origin"} <= columns
    finally:
        DatabaseManager.reset_instance()
        Config._instance = None

class TestOverviewMergesSettlement:
    def test_stored_review_shows_the_latest_settlement(self, db, upstream):
        """快照里冻结的「待结算」不会一直挂着：读 overview 时用推荐表的最新状态覆盖。"""
        svc = _service()
        svc.repository.add_recommendations("2026-10-20", [{
            "code": "600300", "name": "已结算", "cost": 10.0, "init_score": 5, "live_score": 5,
            "entry_status": "preboard", "group_key": "normal"}])
        payload = svc.refresh(trade_date="2026-10-20")
        item = next(i for i in payload["focus_review"]["items"] if i["code"] == "600300")
        assert item["settle_status"] == "pending"          # 生成那一刻还没结算
        svc.repository.update_recommendation_outcomes({
            rec["id"]: {"h1_ret": 3.2, "settle_status": "done", "h1_rule": "unsealed_open", "next_open_ret": 3.2,
                        "next_close_ret": 1.0, "sealed_close": False}
            for rec in svc.repository.recommendations("2026-10-20") if rec["code"] == "600300"})
        stored = svc.overview("2026-10-20")
        merged = next(i for i in stored["focus_review"]["items"] if i["code"] == "600300")
        assert merged["settle_status"] == "done" and merged["h1_ret"] == 3.2
