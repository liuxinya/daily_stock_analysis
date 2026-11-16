# -*- coding: utf-8 -*-
"""「我是打板王」API 契约测试：状态码、字段、not_generated 语义、任务复用与冷却。

endpoint 模块是 ``from ... import DabanService, get_job_manager`` 直接绑定名字的，monkeypatch
必须打在 ``api.v1.endpoints.daban`` 上；job manager 用 ``service_factory`` 注入桩，避免连外网。
"""

from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

BASE = "/api/v1/daban"


def _payload(kind="full"):
    return {
        "schema_version": "daban-v1",
        "kind": kind,
        "trade_date": "2026-10-20",
        "metrics": {"zt_count": 52},
        "stage": {"key": "ferment", "label": "发酵期"},
        "worldview": {"for_date": "2026-10-21", "boundary_text": "明天可以推荐", "dims": []},
        "watchlist": {"items": []},
        "focus_review": {"count": 0, "items": []},
        "disclaimers": {"investment": "免责声明"},
        "cached": True,
    }


class _StubService:
    def __init__(self, payload=None, gate=None):
        self._payload = payload
        self._gate = gate
        self.seen_dates = []

    def overview(self, trade_date=None):
        self.seen_dates.append(trade_date)
        return self._payload

    def history(self, *, limit=10):
        return [{"trade_date": "2026-10-20", "kind": "full", "stage": "ferment", "stage_label": "发酵期"}]

    def refresh(self, *, progress=None, trade_date=None):
        if progress:
            progress("fetching", "桩任务开始")
        if self._gate is not None:
            self._gate.wait(timeout=10)
        return _payload()


@pytest.fixture()
def module():
    from api.v1.endpoints import daban

    return daban


@pytest.fixture()
def client():
    from api.app import app

    with TestClient(app) as test_client:
        yield test_client


def test_status_enabled_by_default(client):
    response = client.get(f"{BASE}/status")
    assert response.status_code == 200 and response.json()["enabled"] is True


def test_overview_404_when_not_generated(client, module, monkeypatch):
    monkeypatch.setattr(module, "DabanService", lambda: _StubService(None))
    response = client.get(f"{BASE}/overview")
    assert response.status_code == 404
    assert response.json()["error"] == "not_generated"


def test_backfill_snapshot_is_reported_as_not_generated(client, module, monkeypatch):
    monkeypatch.setattr(module, "DabanService", lambda: _StubService(_payload(kind="backfill")))
    response = client.get(f"{BASE}/overview", params={"trade_date": "2026-10-19"})
    assert response.status_code == 404 and response.json()["error"] == "not_generated"


def test_overview_returns_payload_and_passes_date(client, module, monkeypatch):
    stub = _StubService(_payload())
    monkeypatch.setattr(module, "DabanService", lambda: stub)
    response = client.get(f"{BASE}/overview", params={"trade_date": "2026-10-20"})
    assert response.status_code == 200
    body = response.json()
    assert body["worldview"]["boundary_text"] == "明天可以推荐" and body["disclaimers"]["investment"]
    assert body["watchlist"] == {"items": []} and body["focus_review"]["count"] == 0
    assert stub.seen_dates == ["2026-10-20"]


def test_overview_rejects_bad_date(client):
    response = client.get(f"{BASE}/overview", params={"trade_date": "20261020"})
    assert response.status_code == 400


def test_history(client, module, monkeypatch):
    monkeypatch.setattr(module, "DabanService", lambda: _StubService(_payload()))
    response = client.get(f"{BASE}/history")
    assert response.status_code == 200 and response.json()["items"][0]["stage_label"] == "发酵期"


def test_job_submit_reuse_and_cooldown(client, module, monkeypatch):
    from src.services.daban.jobs import DabanJobManager

    gate = threading.Event()
    manager = DabanJobManager(service_factory=lambda: _StubService(_payload(), gate))
    monkeypatch.setattr(module, "get_job_manager", lambda: manager)

    first = client.post(f"{BASE}/jobs", json={"trade_date": "2026-10-20"}).json()
    assert first["job"]["trade_date"] == "2026-10-20" and first["reused"] is False
    second = client.post(f"{BASE}/jobs", json={}).json()
    assert second["reused"] is True and second["job"]["job_id"] == first["job"]["job_id"]
    assert client.get(f"{BASE}/jobs").json()["job_id"] == first["job"]["job_id"]

    gate.set()
    for _ in range(50):
        job = client.get(f"{BASE}/jobs/{first['job']['job_id']}").json()
        if job["status"] == "succeeded":
            break
        time.sleep(0.05)
    assert job["status"] == "succeeded" and job["result"]["trade_date"] == "2026-10-20"

    cooling = client.post(f"{BASE}/jobs", json={}).json()
    assert cooling["cooling_down"] is True and cooling["job"] is None
    assert client.get(f"{BASE}/jobs").status_code == 404


def test_job_rejects_bad_date(client):
    assert client.post(f"{BASE}/jobs", json={"trade_date": "yesterday"}).status_code == 400


def test_push_skipped_when_disabled(client, module, monkeypatch):
    monkeypatch.setattr(module, "DabanService", lambda: _StubService(_payload()))
    body = client.post(f"{BASE}/push").json()
    assert body["ok"] is False and body["skipped"] is True and "DABAN_PUSH_ENABLED" in body["message"]


def test_old_snapshot_fields_pass_through(client, module, monkeypatch):
    """2026-11 之前生成的快照还带着退役的 verdict / lanes，读历史日期时原样返回、不报错。"""
    old = {**_payload(), "verdict": {"label": "两派共识日"}, "lanes": {"chen": {}}}
    old.pop("worldview")
    monkeypatch.setattr(module, "DabanService", lambda: _StubService(old))
    body = client.get(f"{BASE}/overview").json()
    assert body["verdict"]["label"] == "两派共识日" and body["worldview"] is None


def test_live_returns_focus_and_limit_board(client, module, monkeypatch):
    from src.services.daban import live

    payload = {"ok": True, "trade_date": "2026-10-20", "trading": True,
               "focus": {"groups": [{"theme_key": "机器人", "items": [{"code": "600001", "init_score": 5}]}], "count": 1},
               "limit_board": {"groups": [], "count": 0}, "score_rules": {"threshold": 0},
               "worldview": {"boundary": {"status": "open"}}}
    monkeypatch.setattr(live.LiveRadarService, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(live.LiveRadarService, "snapshot", lambda self, force=False: payload)
    body = client.get(f"{BASE}/live").json()
    assert body["focus"]["count"] == 1 and body["score_rules"]["threshold"] == 0
    assert body["worldview"]["boundary"]["status"] == "open" and "hits" not in body
