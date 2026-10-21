# -*- coding: utf-8 -*-
"""编排层：抓取降级、按交易日覆盖落库、冷启动回补、两派推荐记录与次日复盘。

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
        assert set(payload["lanes"]) == {"chen", "kobe"}
        assert payload["verdict"]["preferred"] in ("chen", "kobe", "both")
        assert payload["conclusion"]["generated_by"] == "rule_fallback"
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

    def test_previous_picks_are_reviewed_next_day(self, db, upstream):
        svc = _service()
        svc.repository.save_picks("2026-10-19", [
            {"style": "kobe", "code": "603278", "name": "大业股份", "mode": "M3", "action": "trial", "score": 70},
            {"style": "chen", "code": "002889", "name": "东方嘉盛", "mode": "M1", "action": "trial", "score": 75},
        ])
        payload = svc.refresh(trade_date="2026-10-20")
        review = payload["review"]
        assert review["kobe"]["limit_up"] == 1                # 大业股份今天涨停
        assert review["chen"]["avg_change"] == pytest.approx(-6.7)
        stored = {p["code"]: p for p in svc.repository.picks_for("2026-10-19")}
        assert stored["603278"]["next_day_limit_up"] is True
        assert stored["002889"]["next_day_change_pct"] == pytest.approx(-6.7)
        # 今天的推荐也落库了，明天复盘用。
        assert svc.repository.picks_for("2026-10-20")


PHASE_WARM = {"available": True, "trade_date": "2026-10-19", "chen_stage": "warm", "chen_label": "回暖期",
              "kobe_gate": "fall_2", "kobe_label": "主跌第 2 天"}


class TestLiveAndForecastTracking:
    @staticmethod
    def _allow_all(monkeypatch):
        """把「前一交易日周期」固定成放行的阶段；这里测的是留档 / 回填链路，不是周期分类。"""
        from src.services.daban import live as live_mod

        live_mod.clear_cache()
        monkeypatch.setattr(live_mod.LiveRadarService, "previous_context", lambda self, day: dict(PHASE_WARM))

    def test_close_marks_preboard_seals_and_never_locks_sealed_boards(self, db, upstream, monkeypatch):
        """收盘只做两件事：给未涨停候选写「最终封没封住」，以及保留盘中锁定的名额。

        已经封板的票不占名额、也不补录——出现时已经买不进去了。
        """
        self._allow_all(monkeypatch)
        svc = _service()
        svc.repository.upsert_live_hits("2026-10-20", [
            {"style": "kobe", "code": "603278", "name": "大业股份", "action": "watch", "first_time": "093500",
             "seen_at": "093500", "limit_price": 12.0, "seen_price": 11.6, "gap_pct": 3.4, "position_pct": 0,
             "sealed_close": None, "open_count": 0, "theme": "机器人", "phase": "主跌第 2 天", "origin": "preboard"},
        ], final=False)
        payload = svc.refresh(trade_date="2026-10-20")
        rows = svc.repository.live_hits("2026-10-20")
        assert [r["code"] for r in rows] == ["603278"]
        assert rows[0]["origin"] == "preboard" and rows[0]["sealed_close"] is True   # 大业股份当天涨停
        assert payload["live_review"]["calls"] == 0        # 没有已封板被锁成名额

    def test_intraday_lock_is_kept_and_reviewed_from_the_seen_price(self, db, upstream, monkeypatch):
        from src.services.daban import fetchers

        self._allow_all(monkeypatch)
        svc = _service()
        svc.repository.upsert_live_hits("2026-10-20", [
            {"style": "kobe", "code": "002347", "name": "泰尔股份", "action": "trial", "first_time": "093745",
             "seen_at": "093800", "limit_price": 6.61, "seen_price": 6.40, "gap_pct": 3.3, "position_pct": 10,
             "sealed_close": None, "open_count": 0, "theme": "机器人", "phase": "主跌第 2 天", "origin": "live"},
        ], final=False)
        svc.refresh(trade_date="2026-10-20")
        kept = svc.repository.live_hits("2026-10-20")[0]
        assert kept["origin"] == "live" and kept["position_pct"] == 10 and kept["slot"] == 1

        # 次日：以锁定时的现价 6.40 为成本，而不是打板价 6.61
        monkeypatch.setattr(fetchers, "next_trade_date", lambda day, **kw: "2026-10-21")
        monkeypatch.setattr(fetchers, "fetch_klines", lambda codes, day, *, limit, timeout: _env(
            "klines", rows={c: [{"time": "20261021", "open": 6.4, "high": 6.9, "low": 6.3, "close": 6.72}]
                            for c in codes}))
        upstream["zt"]["2026-10-21"] = [zt_row("002889", "东方嘉盛", reason="机器人概念")]
        svc.refresh(trade_date="2026-10-21")
        reviewed = [h for h in svc.repository.live_hits("2026-10-20") if h["reviewed"]]
        assert reviewed and reviewed[0]["next_close_ret"] == pytest.approx((6.72 / 6.40 - 1) * 100, abs=0.05)

    def test_forecasts_are_recorded_for_the_next_trade_date(self, db, upstream, monkeypatch):
        monkeypatch.setattr(service_mod, "next_trade_date", lambda day, **kw: "2026-10-21")
        monkeypatch.setattr(service_mod, "build_forecasts", lambda **kw: [
            {"code": "600999", "name": "候选", "theme": "机器人", "source": "broken"},
        ])
        svc = _service()
        svc.refresh(trade_date="2026-10-20")
        rows = svc.repository.forecasts_for("2026-10-21")
        assert [(r["code"], r["source"], r["evaluated"]) for r in rows] == [("600999", "broken", False)]


class TestLockedSlotsAtClose:
    def test_close_recording_keeps_intraday_slots_and_only_fills_the_rest(self, db, upstream, monkeypatch):
        """盘中 10:00 前锁定的名额，收盘补录时原样保留；更早触板但盘中没记到的票不会挤掉它。"""
        from src.services.daban import live as live_mod

        live_mod.clear_cache()
        monkeypatch.setattr(live_mod.LiveRadarService, "previous_context", lambda self, day: {
            "available": True, "trade_date": "2026-10-19", "chen_stage": "retreat", "chen_label": "退潮期",
            "kobe_gate": "fall_2", "kobe_label": "主跌第 2 天",       # 科比线只有 1 个 25% 的满额名额
        })
        svc = _service()
        svc.repository.upsert_live_hits("2026-10-20", [{
            "style": "kobe", "code": "603626", "name": "科森科技", "action": "actionable", "first_time": "102000",
            "seen_at": "102005", "limit_price": 22.21, "position_pct": 25, "slot": 1, "sealed_close": None,
            "open_count": 0, "theme": "机器人", "phase": "主跌第 2 天", "origin": "live",
        }], final=False)
        payload = svc.refresh(trade_date="2026-10-20")
        rows = [h for h in svc.repository.live_hits("2026-10-20") if h["style"] == "kobe"]
        assert [(h["code"], h["slot"], h["origin"]) for h in rows] == [("603626", 1, "live")]
        assert rows[0]["sealed_close"] is True                     # 收盘写入是否封住
        assert payload["live_review"]["locked_intraday"] == 1


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
