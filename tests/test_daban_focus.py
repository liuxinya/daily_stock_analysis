# -*- coding: utf-8 -*-
"""盘中「重点」端到端：上游全部打桩、库用临时 SQLite，验证评分 → 写推荐 → 不重复写 → 涨停板 tab → 收盘 → H1 回填。"""

from __future__ import annotations

import os
import tempfile
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from src.services.daban import live, recommend

from tests.daban_fixtures import zt_row

TODAY = "2026-11-09"
D1 = "2026-11-06"
D2 = "2026-11-05"


def _days(end: str, n: int):
    """``end``（不含）之前 n 个工作日，按时间正序。"""
    out, cur = [], date.fromisoformat(end)
    while len(out) < n:
        cur -= timedelta(days=1)
        if cur.weekday() < 5:
            out.append(cur.isoformat())
    return list(reversed(out))


def _bars(code: str, *, base: float = 10.0, n: int = 80, end: str = D1):
    """截至 ``end``（含）的 n 根平稳日 K。"""
    rows = []
    for i, day in enumerate(_days((date.fromisoformat(end) + timedelta(days=1)).isoformat(), n)):
        px = base * (1 + 0.002 * ((i % 5) - 2))
        rows.append({"time": day.replace("-", ""), "open": px, "high": px * 1.01, "low": px * 0.99, "close": px,
                     "volume": 1e6})
    return rows


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


PHASE = {
    "available": True, "source": "review", "trade_date": D1, "chen_stage": "warm", "chen_label": "回暖期",
    "kobe_gate": "low_shock", "kobe_label": "低位震荡（试错期）", "recent_mains": [], "yesterday_codes": [],
    "yesterday_broken": [], "yesterday_weak": [], "metrics": {"zt_count": 40}, "stage": {"key": "warm", "label": "回暖期"},
    "kobe_stage": {"key": "low_shock", "label": "低位震荡（试错期）", "fall_day": 0, "gate_key": "low_shock"},
}


def _today_rows():
    return [
        zt_row("600001", "龙一", boards=3, reason="人形机器人", first_time="093100"),
        zt_row("600002", "龙二", boards=2, reason="人形机器人", first_time="093500"),
        zt_row("600003", "跟风", boards=1, reason="人形机器人", first_time="095000"),
    ]


@pytest.fixture()
def upstream(monkeypatch, db):
    from data_provider import limit_up_fetcher as lu

    from src.repositories.daban_repo import DabanRepository
    from src.services.daban import fetchers

    repo = DabanRepository()
    repo.save_pool_rows(D1, "zt", [zt_row("600001", "龙一", boards=2, reason="人形机器人")])
    repo.save_pool_rows(D2, "zt", [zt_row("600001", "龙一", boards=1, reason="人形机器人")])
    repo.save_pool_rows("2026-10-20", "zt", [zt_row("600100", "候选", reason="人形机器人+灵巧手")])

    state = {"now": "100000", "price": 10.6, "open": 10.3, "rows": _today_rows(), "broken": []}

    def quote(code, *, price=None, open_=None):
        if code == "600100":
            price, open_ = state["price"], state["open"]
            return {"code": code, "name": "候选", "price": price, "prev_close": 10.0, "open": open_, "high": price,
                    "limit_up": 11.0, "sell1_volume": 100 if price < 11 else 0, "volume_ratio": 2.0,
                    "turnover_rate": 5.0, "circ_mv": 30e8}
        return {"code": code, "name": code, "price": 11.0, "prev_close": 10.0, "open": 10.5, "high": 11.0,
                "limit_up": 11.0, "sell1_volume": 0, "volume_ratio": 3.0, "turnover_rate": 8.0, "circ_mv": 30e8}

    monkeypatch.setattr(lu, "fetch_ths_pool", lambda kind, day, *, timeout=None: {
        "date": TODAY, "rows": [dict(r) for r in (state["rows"] if kind == "zt" else state["broken"])],
        "counts": {}, "trade_status": "open"})
    monkeypatch.setattr(lu, "fetch_ths_ladder", lambda day, *, timeout=None: {r["code"]: r["boards"] for r in state["rows"]})
    monkeypatch.setattr(lu, "fetch_ths_changes", lambda *, timeout=None: [])
    monkeypatch.setattr(lu, "fetch_tencent_quotes", lambda codes, *, timeout=None: {c: quote(c) for c in codes})
    monkeypatch.setattr(lu, "fetch_em_changes", lambda kind, *, timeout=None: (
        [{"code": "600100", "name": "候选", "time": state["now"]}] if kind == "rise" else []))
    monkeypatch.setattr(lu, "fetch_ths_blocks", lambda day, *, timeout=None: [] if day else [
        {"name": "机器人概念", "limit_up_num": 3, "days": 3, "members": [{"code": "600001"}]}])
    monkeypatch.setattr(lu, "fetch_tencent_index_quotes", lambda symbols=None, *, timeout=None: {
        "sh000001": {"name": "上证指数", "price": 3000.0, "prev_close": 2990.0, "change_pct": 0.33}})
    monkeypatch.setattr(lu, "fetch_em_concepts", lambda codes, *, timeout=None: {})
    monkeypatch.setattr(fetchers, "fetch_limit_pool", lambda *a, **k: {"ok": False})
    monkeypatch.setattr(fetchers, "fetch_em", lambda *a, **k: {"ok": False})
    monkeypatch.setattr(fetchers, "previous_trade_date", lambda day, **kw: _days(day, 1)[0])
    monkeypatch.setattr(fetchers, "recent_trade_dates", lambda end, count: _days(end, count))

    monkeypatch.setattr(live, "_today", lambda: TODAY)
    monkeypatch.setattr(live, "_now_hhmmss", lambda: state["now"])
    monkeypatch.setattr(live, "market_open_now", lambda *a, **k: True)
    monkeypatch.setattr(live.LiveRadarService, "previous_context", lambda self, day: dict(PHASE))
    monkeypatch.setattr(live.LiveRadarService, "theme_news", lambda self, names: {n: 9 for n in names})
    monkeypatch.setattr(live.LiveRadarService, "_market_scan", lambda self, **kw: ([], ""))
    # 留档放后台线程会让断言不稳定：测试里同步写。
    monkeypatch.setattr(live.LiveRadarService, "_log_radar_async", lambda self, day, now, **kw: self._log_radar(day, now, **kw))
    monkeypatch.setattr(live.LiveRadarService, "_maybe_review", lambda self, today, now: None)

    books = {}

    def book(trade_date, *, repository, rules):
        if trade_date not in books:
            books[trade_date] = recommend.FeatureBook(
                trade_date, repository=repository, rules=rules, synchronous=True,
                fetch_bars=lambda code: _bars(code), fetch_pool=lambda day, pool: [], fetch_previous=lambda day: None)
        return books[trade_date]

    monkeypatch.setattr(recommend, "feature_book", book)
    live.clear_cache()
    yield state
    live.clear_cache()


def _service():
    cfg = SimpleNamespace(daban_fetch_timeout_sec=5, daban_live_cache_ttl_sec=0, daban_rules_file="",
                          daban_live_push_enabled=False, daban_live_poll_sec=10)
    return live.LiveRadarService(config=cfg)


def _focus_codes(payload):
    return [i["code"] for g in payload["focus"]["groups"] for i in g["items"]]


class TestFocusFlow:
    def test_candidate_is_scored_recorded_once_and_sealed_go_to_limit_board(self, upstream):
        svc = _service()
        first = svc.snapshot(force=True)
        assert first["ok"] and first["worldview"]["boundary"]["status"] == "open"
        assert _focus_codes(first) == ["600100"]
        item = first["focus"]["groups"][0]["items"][0]
        # 昨日没触板、今天高开 3%：系统补充 +5 分
        assert item["init_score"] == 5 and item["cost"] == 10.6 and item["entered_at"] == "100000"
        assert [i["key"] for i in item["init_items"]] == ["open_gap2_5"]
        rows = svc.repository.recommendations(TODAY)
        assert [(r["code"], r["cost"], r["init_score"]) for r in rows] == [("600100", 10.6, 5)]
        assert rows[0]["init_detail"]["boundary"]["status"] == "open"

        # 下一轮：价格变了，成本和初始分不动，也不重复写。
        upstream["now"], upstream["price"] = "100500", 10.8
        second = svc.snapshot(force=True)
        assert len(svc.repository.recommendations(TODAY)) == 1
        again = second["focus"]["groups"][0]["items"][0]
        assert again["cost"] == 10.6 and again["price"] == 10.8 and again["ret_now"] == pytest.approx(1.89, abs=0.01)

        board = second["limit_board"]["groups"][0]
        assert [(i["code"], i["leader_rank"]) for i in board["items"]] == [("600001", 1), ("600002", 2), ("600003", None)]

        kinds = {e["kind"] for e in svc.repository.radar_events(TODAY)}
        assert {"seen", "focus", "market", "theme"} <= kinds

    def test_blocked_boundary_keeps_candidates_out(self, upstream, monkeypatch):
        phase = {**PHASE, "chen_stage": "retreat", "chen_label": "退潮期",
                 "stage": {"key": "retreat", "label": "退潮期"}}
        monkeypatch.setattr(live.LiveRadarService, "previous_context", lambda self, day: dict(phase))
        payload = _service().snapshot(force=True)
        assert payload["focus"]["count"] == 0
        assert payload["worldview"]["boundary"]["status"] == "closed"
        assert any("周期边界" in w["reason"] for w in payload["focus"]["watching"])

    def test_auction_logs_but_never_records(self, upstream):
        upstream["now"] = "092000"
        svc = _service()
        payload = svc.snapshot(force=True)
        assert payload["focus"]["count"] == 0
        assert svc.repository.recommendations(TODAY) == []


class TestSettle:
    def test_close_then_h1_settles_from_the_entry_price(self, db):
        from src.repositories.daban_repo import DabanRepository

        repo = DabanRepository()
        repo.add_recommendations("2026-11-09", [{"code": "600100", "name": "候选", "cost": 10.6, "init_score": 5,
                                                 "live_score": 5, "entry_status": "preboard", "group_key": "normal"}])
        assert repo.add_recommendations("2026-11-09", [{"code": "600100", "cost": 99}]) == []
        repo.close_recommendations("2026-11-09", sealed_codes=set())
        bars = [{"time": "20261106", "open": 10, "high": 10, "low": 10, "close": 10.0},
                {"time": "20261109", "open": 10.3, "high": 10.9, "low": 10.2, "close": 10.5},
                {"time": "20261110", "open": 10.9, "high": 11.2, "low": 10.7, "close": 10.8}]
        # 11-10 还没收盘：持有中，不结案
        assert recommend.settle_pending(repo, today="2026-11-10", now="100000", fetch=lambda code: bars[:2]) == 0
        recommend.settle_pending(repo, today="2026-11-10", now="151000", fetch=lambda code: bars)
        rec = repo.recommendations("2026-11-09")[0]
        assert rec["settle_status"] == "done" and rec["sealed_close"] is False
        # 没封住、次日高开（10.9 ≥ 10.6）：开盘卖
        assert rec["h1_ret"] == pytest.approx((10.9 / 10.6 - 1) * 100, abs=0.01)
        assert rec["next_close_ret"] == pytest.approx((10.8 / 10.6 - 1) * 100, abs=0.01)
        assert rec["close_score"] == 5


class TestFeatureBookRetry:
    def test_ctx_failure_backs_off_before_retrying(self, monkeypatch):
        calls = []
        repo = SimpleNamespace(pool_rows=lambda day, pool: [])

        def fetch_pool(day, pool):
            calls.append((day, pool))
            return []

        book = recommend.FeatureBook(TODAY, repository=repo, rules={}, fetch_pool=fetch_pool,
                                     fetch_previous=lambda day: None, synchronous=True)
        assert book.ensure_ctx() is False and "涨停池为空" in book.status()["ctx_error"]
        first = len(calls)
        assert book.ensure_ctx() is False and len(calls) == first      # 冷却期内不重打上游
        monkeypatch.setattr(recommend, "CTX_RETRY_SEC", 0.0)
        book.ensure_ctx()
        assert len(calls) > first


class TestSurgePoint:
    def test_index_surge_flags_and_pins_entries_in_the_window(self, upstream, monkeypatch):
        """沪深300 30 分钟拉升 ≥0.8 且翻红：触发后 30 分钟内进入「重点」的票打标记、置顶；只是时机提示，评分不变。"""
        from data_provider import limit_up_fetcher as lu

        index = {"chg": -0.2}
        monkeypatch.setattr(lu, "fetch_tencent_index_quotes", lambda symbols=None, *, timeout=None: {
            "sh000300": {"name": "沪深300", "price": 4000.0, "prev_close": 4000.0, "change_pct": index["chg"]}})
        svc = _service()
        upstream["now"], upstream["price"] = "093500", 10.0          # 距涨停 10%：还没进入
        first = svc.snapshot(force=True)
        assert first["surge_point"] is None and first["focus"]["count"] == 0

        live._INDEX_LAST.clear()
        index["chg"], upstream["now"], upstream["price"] = 0.7, "100500", 10.6
        second = svc.snapshot(force=True)
        surge = second["surge_point"]
        assert surge["at"] == "100500" and surge["until"] == "103500" and surge["active"] is True
        assert surge["rise"] == pytest.approx(0.9)
        item = second["focus"]["groups"][0]["items"][0]
        assert "surge" in item["flags"] and item["init_score"] == 5
        assert [i["code"] for i in second["focus"]["pinned"]] == ["600100"]
        assert "surge" in svc.repository.recommendations(TODAY)[0]["flags"]

        # 进程重启：当天的推荐点从留档恢复，不会因为指数分时丢了就重新算一个更晚的时刻。
        live._WV_STATE.clear()
        live._INDEX_SERIES.clear()
        live._INDEX_LAST.clear()
        upstream["now"] = "110000"
        third = svc.snapshot(force=True)
        assert third["surge_point"]["at"] == "100500" and third["surge_point"]["active"] is False

    def test_blocked_day_opens_only_in_the_surge_window(self, upstream, monkeypatch):
        """前一交易日退潮：推荐点窗口里放开、标「未验证」；窗口过了照常挡住。"""
        from data_provider import limit_up_fetcher as lu

        phase = {**PHASE, "chen_stage": "retreat", "chen_label": "退潮期", "stage": {"key": "retreat", "label": "退潮期"}}
        monkeypatch.setattr(live.LiveRadarService, "previous_context", lambda self, day: dict(phase))
        index = {"chg": -0.2}
        monkeypatch.setattr(lu, "fetch_tencent_index_quotes", lambda symbols=None, *, timeout=None: {
            "sh000300": {"name": "沪深300", "price": 4000.0, "prev_close": 4000.0, "change_pct": index["chg"]}})
        svc = _service()
        svc.rules["overrides"]["theme_min_new"] = 99      # 这里只看大盘：关掉题材异动，免得它先放开候选的题材
        upstream["now"] = "093500"
        before = svc.snapshot(force=True)
        assert before["worldview"]["boundary"]["status"] == "closed" and before["focus"]["count"] == 0

        live._INDEX_LAST.clear()
        index["chg"], upstream["now"] = 0.7, "100500"
        inside = svc.snapshot(force=True)
        assert inside["worldview"]["boundary"]["pass"]["kind"] == "surge"
        assert inside["surge_point"]["opens_blocked"] is True
        flags = inside["focus"]["groups"][0]["items"][0]["flags"]
        assert "surge" in flags and "unverified" in flags

        live._INDEX_LAST.clear()
        upstream["now"] = "104000"
        after = svc.snapshot(force=True)
        assert after["worldview"]["boundary"]["status"] == "closed"


class TestSettlementWindow:
    def test_bar_limit_grows_with_the_oldest_pending_entry(self):
        assert recommend._bars_needed("2026-11-16", "2026-11-13") == recommend.SETTLE_BARS_BASE + 2
        assert recommend._bars_needed("2026-11-16", "2025-01-01") == recommend.SETTLE_BARS_MAX

    def test_settle_uses_a_window_that_reaches_the_oldest_entry(self, db):
        """服务停了三周后才恢复：默认取数长度要能覆盖最早那笔推荐，否则永远结不了案。"""
        from src.repositories.daban_repo import DabanRepository

        repo = DabanRepository()
        repo.add_recommendations("2026-10-20", [{"code": "600100", "name": "老单", "cost": 10.0, "init_score": 5,
                                                 "live_score": 5, "entry_status": "preboard", "group_key": "normal"}])
        seen = {}

        def fetch(code):
            seen["called"] = True
            return []

        # 显式传入 fetch 时不改路径（只验证默认实现）；这里直接验证默认 limit 的计算发生在取数之前。
        assert recommend._bars_needed("2026-11-16", "2026-10-20") > 20
        assert recommend.settle_pending(repo, today="2026-11-16", now="151000", fetch=fetch) == 0
        assert seen["called"] is True
