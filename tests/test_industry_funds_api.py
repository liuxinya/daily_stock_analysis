# -*- coding: utf-8 -*-
"""行业资金 API 层的契约测试。

用真实的 FastAPI app 走 HTTP 链路，但把 endpoint 用到的 service / job manager 全部换成
桩，所以**不起网络、不跑模型**。目的是锁住接口契约（状态码、字段名、降级语义）。

两个容易踩的点，改动时注意：

1. endpoint 模块是 ``from ... import IndustryFundsService, get_job_manager`` **直接绑定
   名字**的，所以 monkeypatch 必须打在 ``api.v1.endpoints.industry_funds`` 上；打在源模块
   （``src.services...``）上不会生效，测试会静默地跑到真实实现上去。
2. ``IndustryFundsJobManager`` 一旦拿到真实 service 就会**真的发起一次全量抓取**。
   所以必须用它的 ``service_factory`` 注入桩，否则测试会连外网。

路由前缀 ``/api/v1`` 挂在 ``api/app.py``，所以从 app 出发拿 TestClient。
"""

from __future__ import annotations

import warnings

import pytest
from fastapi.testclient import TestClient

warnings.filterwarnings("ignore")

BASE = "/api/v1/industry-funds"


def _stub_overview():
    return {
        "schema_version": "industry-funds-v1",
        "trade_date": "2026-10-16",
        "generated_at": "2026-10-18T20:00:00",
        "duration_ms": 42000,
        "market": {"score": 52.5, "verdict": "neutral", "verdict_label": "中性", "factors": []},
        "industries": {"industries": []},
        "candidates": {"candidates": []},
        "conclusion": {"summary": "测试", "generated_by": "rule_fallback"},
        "sources": {},
        "disclaimers": {"flow": "口径说明", "investment": "免责声明"},
        "degraded": False,
        "degraded_reason": "",
        "warnings": [],
        "cached": True,
    }


class _StubService:
    """替身 service：默认返回一份桩快照，可以被改成"没有缓存"。"""

    def __init__(self, payload=None, gate=None):
        self._payload = payload
        # 给一个 Event 就能把 refresh 挂住，用于测试"任务仍在进行中"的分支。
        self._gate = gate

    def overview(self, *, trade_date=None):
        # 指定历史日期时只有当天有快照，模拟「那天的快照不存在」。
        if trade_date and trade_date != "2026-10-16":
            return None
        return self._payload

    def history(self, *, limit=30):
        return []

    def refresh(self, *, progress=None):
        if progress:
            progress("fetching_flow", "桩任务开始")
        if self._gate is not None:
            self._gate.wait(timeout=10)
        if progress:
            progress("finalizing", "桩任务结束")
        return _stub_overview()


def _make_stub_manager(gate=None):
    """桩 manager：service 是桩，所以任务不会真的抓数据。"""
    from src.services.industry_funds.jobs import IndustryFundsJobManager

    return IndustryFundsJobManager(service_factory=lambda: _StubService(None, gate))


@pytest.fixture()
def endpoint_module():
    from api.v1.endpoints import industry_funds as module

    return module


@pytest.fixture()
def client():
    from api.app import app

    with TestClient(app) as test_client:
        yield test_client


class TestStatus:
    def test_reports_enabled_by_default(self, client):
        response = client.get(f"{BASE}/status")
        assert response.status_code == 200
        # 菜单靠这个字段决定是否显示入口。
        assert response.json()["enabled"] is True


class TestOverview:
    def test_returns_404_with_a_clear_message_when_nothing_generated(
        self, client, endpoint_module, monkeypatch
    ):
        monkeypatch.setattr(endpoint_module, "IndustryFundsService", lambda: _StubService(None))

        response = client.get(f"{BASE}/overview")
        assert response.status_code == 404
        # 错误处理中间件在 detail 已经是 {error, message} 时直接把它当响应体，
        # 所以这里没有 detail 包裹层。
        body = response.json()
        assert body["error"] == "not_found"
        # 前端靠这句话提示用户点刷新，不能是泛泛的 "not found"。
        assert "刷新" in body["message"]

    def test_returns_the_cached_payload(self, client, endpoint_module, monkeypatch):
        monkeypatch.setattr(
            endpoint_module, "IndustryFundsService", lambda: _StubService(_stub_overview())
        )

        response = client.get(f"{BASE}/overview")
        assert response.status_code == 200
        body = response.json()
        assert body["trade_date"] == "2026-10-16"
        assert body["cached"] is True
        # 免责声明必须随载荷一起下发，前端不能自己硬编码一份。
        assert body["disclaimers"]["investment"]

    def test_a_specific_trade_date_reads_that_days_snapshot(self, client, endpoint_module, monkeypatch):
        monkeypatch.setattr(
            endpoint_module, "IndustryFundsService", lambda: _StubService(_stub_overview())
        )
        assert client.get(f"{BASE}/overview", params={"trade_date": "2026-10-16"}).status_code == 200
        # 没有那天的快照时 404，前端据此提示「这一天没有总览」
        assert client.get(f"{BASE}/overview", params={"trade_date": "2026-10-15"}).status_code == 404

    def test_history_returns_an_items_array(self, client, endpoint_module, monkeypatch):
        monkeypatch.setattr(
            endpoint_module, "IndustryFundsService", lambda: _StubService(_stub_overview())
        )

        response = client.get(f"{BASE}/history")
        assert response.status_code == 200
        assert response.json()["items"] == []


class TestJobSubmission:
    def test_submits_a_new_job(self, client, endpoint_module, monkeypatch):
        manager = _make_stub_manager()
        monkeypatch.setattr(endpoint_module, "get_job_manager", lambda: manager)

        response = client.post(f"{BASE}/jobs")
        assert response.status_code == 200
        body = response.json()
        assert body["job"]["job_id"]
        assert body["reused"] is False
        assert body["cooling_down"] is False

    def test_reuses_the_running_job_instead_of_starting_a_second_one(
        self, client, endpoint_module, monkeypatch
    ):
        """连点刷新不能开两个任务打上游。

        桩 refresh 用 Event 挂住，否则它会瞬间跑完、任务不再是 running，
        第二次提交就会合法地新建任务，测不到复用分支。
        """
        import threading

        gate = threading.Event()
        manager = _make_stub_manager(gate)
        monkeypatch.setattr(endpoint_module, "get_job_manager", lambda: manager)

        try:
            first = client.post(f"{BASE}/jobs").json()
            second = client.post(f"{BASE}/jobs").json()

            assert second["reused"] is True
            assert second["job"]["job_id"] == first["job"]["job_id"]
        finally:
            gate.set()

    def test_returns_404_for_an_unknown_job(self, client, endpoint_module, monkeypatch):
        monkeypatch.setattr(endpoint_module, "get_job_manager", _make_stub_manager)

        response = client.get(f"{BASE}/jobs/does-not-exist")
        assert response.status_code == 404

    def test_active_job_returns_404_when_idle(self, client, endpoint_module, monkeypatch):
        monkeypatch.setattr(endpoint_module, "get_job_manager", _make_stub_manager)

        response = client.get(f"{BASE}/jobs")
        assert response.status_code == 404

    def test_polls_a_job_through_to_success(self, client, endpoint_module, monkeypatch):
        """桩任务应当很快跑完，并带上完整 result —— 轮询契约不能变。"""
        import time

        manager = _make_stub_manager()
        monkeypatch.setattr(endpoint_module, "get_job_manager", lambda: manager)

        job_id = client.post(f"{BASE}/jobs").json()["job"]["job_id"]

        deadline = time.time() + 10
        job = None
        while time.time() < deadline:
            job = client.get(f"{BASE}/jobs/{job_id}").json()
            if job["status"] in ("succeeded", "failed"):
                break
            time.sleep(0.05)

        assert job is not None
        assert job["status"] == "succeeded", job.get("error")
        assert job["progress"] == 1.0
        assert job["phase_label"] == "已完成"
        assert job["result"]["trade_date"] == "2026-10-16"


class TestPush:
    def test_reports_disabled_push_as_skipped_not_an_error(
        self, client, endpoint_module, monkeypatch
    ):
        """推送未启用是配置状态，不是故障 —— 不能报 500。默认即为关闭。"""
        monkeypatch.setattr(
            endpoint_module, "IndustryFundsService", lambda: _StubService(_stub_overview())
        )

        response = client.post(f"{BASE}/push")
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert body["skipped"] is True

    def test_says_so_when_there_is_nothing_to_push(self, client, endpoint_module, monkeypatch):
        """启用推送但还没生成过快照时要给出可操作的提示，而不是空消息。

        推送开关优先于载荷检查（先看配置、再看数据），所以这里直接把 pusher 换成桩，
        单独验证"没有可推送内容"这一条分支。
        """
        from src.services.industry_funds import push as push_module

        class _StubPusher:
            def __init__(self, **kwargs):
                pass

            def push(self, payload):
                assert payload is None
                return {"ok": False, "skipped": True, "message": "还没有可推送的快照，请先刷新"}

        monkeypatch.setattr(push_module, "IndustryFundsPusher", _StubPusher)
        monkeypatch.setattr(endpoint_module, "IndustryFundsService", lambda: _StubService(None))

        response = client.post(f"{BASE}/push")
        assert response.status_code == 200
        body = response.json()
        assert body["skipped"] is True
        assert "刷新" in body["message"]

class TestFlowEndpoints:
    """资金流向两个 tab 的数据接口：日期 tab + 两周序列。"""

    def test_flow_dates_lists_trading_days(self, client, monkeypatch):
        from src.services.industry_funds import flows

        monkeypatch.setattr(flows, "flow_dates", lambda days: {
            "days": [{"date": "2026-11-13", "has_overview": True}, {"date": "2026-11-16", "has_overview": False}],
            "today": "2026-11-16",
        })
        response = client.get(f"{BASE}/flow-dates", params={"days": 10})
        assert response.status_code == 200
        body = response.json()
        assert [d["date"] for d in body["days"]] == ["2026-11-13", "2026-11-16"]
        assert body["days"][0]["has_overview"] is True and body["days"][1]["has_overview"] is False

    def test_flows_returns_both_modules(self, client, monkeypatch):
        from src.services.industry_funds import flows

        monkeypatch.setattr(flows, "flow_series", lambda days: {
            "days": ["2026-11-13", "2026-11-16"],
            "national_team": {"series": [{"date": "2026-11-13", "net_shares": -17.3}], "inflow_streak": 0,
                              "degraded": False, "degraded_reason": ""},
            "institutions": {"series": [{"date": "2026-11-13", "net": -8.65, "count": 38}], "degraded": False,
                             "degraded_reason": ""},
            "generated_at": "2026-11-16T13:40:00",
            "duration_ms": 7000,
        })
        response = client.get(f"{BASE}/flows", params={"days": 10})
        assert response.status_code == 200
        body = response.json()
        assert body["days"] == ["2026-11-13", "2026-11-16"]
        assert body["national_team"]["series"][0]["net_shares"] == -17.3
        assert body["institutions"]["series"][0]["net"] == -8.65

    def test_days_out_of_range_is_rejected(self, client):
        assert client.get(f"{BASE}/flows", params={"days": 1}).status_code == 422
        assert client.get(f"{BASE}/flows", params={"days": 99}).status_code == 422

    def test_hot_money_returns_a_seat_matrix(self, client, monkeypatch):
        from src.services.industry_funds import hot_money

        monkeypatch.setattr(hot_money, "hot_money", lambda days, seats: {
            "days": ["2026-11-13"], "seats": [{"code": "S1", "name": "某营业部", "short": "某", "net": 2.0, "buy": 3.0,
                                               "sell": 1.0, "days": 1}],
            "dates": [{"date": "2026-11-13", "total": 1, "rows": [{"code": "600001", "name": "甲", "buy": 3.0,
                                                                    "sell": 1.0, "net": 2.0,
                                                                    "cells": {"S1": {"buy": 3.0, "sell": 1.0, "net": 2.0}}}]}],
            "row_limit": 60, "degraded": False, "degraded_reason": "", "coverage": "龙虎榜上榜个股",
        })
        response = client.get(f"{BASE}/hot-money", params={"days": 10, "seats": 10})
        assert response.status_code == 200
        body = response.json()
        assert body["seats"][0]["code"] == "S1"
        assert body["dates"][0]["rows"][0]["cells"]["S1"]["net"] == 2.0

    def test_hot_money_seat_count_is_bounded(self, client):
        assert client.get(f"{BASE}/hot-money", params={"seats": 2}).status_code == 422
        assert client.get(f"{BASE}/hot-money", params={"seats": 50}).status_code == 422

    def test_new_endpoints_respect_the_module_switch(self, client, monkeypatch):
        """INDUSTRY_FUNDS_ENABLED=false 时整组接口一起不可用，新接口不能留后门。"""
        from src.config import get_config

        config = get_config()
        monkeypatch.setattr(config, "industry_funds_enabled", False)
        for path in ("/flows", "/flow-dates", "/hot-money"):
            response = client.get(f"{BASE}{path}")
            assert response.status_code == 404
            assert response.json()["error"] == "not_enabled"



class TestHotMoneySeatEndpoint:
    def test_returns_one_seat_day_detail(self, client, monkeypatch):
        from src.services.industry_funds import hot_money

        monkeypatch.setattr(hot_money, "seat_day_detail", lambda codes, day, timeout=20: {
            "date": day, "stocks": [{"code": "600001", "name": "甲", "buy": 1.0, "sell": 0.2, "net": 0.8,
                                     "change_pct": 9.9}],
            "degraded": False, "degraded_reason": "",
        })
        response = client.get(f"{BASE}/hot-money/seat", params={"codes": "10025080", "date": "2026-11-16"})
        assert response.status_code == 200
        body = response.json()
        assert body["date"] == "2026-11-16" and body["stocks"][0]["net"] == 0.8

    def test_rejects_a_bad_date_or_empty_codes(self, client):
        assert client.get(f"{BASE}/hot-money/seat",
                          params={"codes": "10025080", "date": "2026/11/16"}).status_code == 400
        assert client.get(f"{BASE}/hot-money/seat",
                          params={"codes": " ", "date": "2026-11-16"}).status_code == 400
