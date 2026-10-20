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
