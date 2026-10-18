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

    def overview(self):
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
