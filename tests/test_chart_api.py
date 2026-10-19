# -*- coding: utf-8 -*-
"""``/api/v1/chart/*`` 端点的回归测试：参数校验、错误映射与响应装配。

服务层打桩，不联网。上游解析与指标口径的覆盖在 ``test_ths_chart.py`` 与
``test_chart_service.py``。
"""

from __future__ import annotations

import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from api.v1.endpoints import chart
from data_provider.ths_chart_fetcher import ThsUpstreamError


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(chart.router, prefix="/api/v1/chart")
    return TestClient(app)


def _kline_payload(**overrides):
    payload = {
        "code": "002594",
        "name": "比亚迪",
        "period": "day",
        "adjust": "qfq",
        "indicators": ["ma", "macd"],
        "rows": [
            {
                "time": "20261016",
                "open": 82.24, "high": 83.39, "low": 81.85, "close": 82.72,
                "volume": 20968562.0, "amount": 1733332800.0, "turnover_rate": 0.601,
                "partial": False, "ma5": 82.636, "dif": -1.35, "dea": -1.37, "macd": 0.03,
            },
            {
                "time": "20261019",
                "open": 82.5, "high": 84.15, "low": 82.3, "close": 83.53,
                "volume": 19563278.0, "amount": 1634894400.0, "turnover_rate": None,
                "partial": True, "ma5": 82.422, "dif": -1.23, "dea": -1.34, "macd": 0.22,
            },
        ],
        "today_source": "snapshot",
        "listed_since": "20110630",
        "trading_hours": "0930-1130,1300-1500",
        "source": "ths",
    }
    payload.update(overrides)
    return payload


class TestKlineEndpoint:
    def test_returns_rows_and_partial_flag(self, client, monkeypatch):
        captured = {}

        def fake_get_kline(code, **kwargs):
            captured["code"] = code
            captured.update(kwargs)
            return _kline_payload()

        monkeypatch.setattr(chart.chart_service, "get_kline", fake_get_kline)
        response = client.get("/api/v1/chart/kline", params={"code": "002594", "indicators": "ma,macd"})

        assert response.status_code == 200
        body = response.json()
        assert body["today_source"] == "snapshot"
        assert body["rows"][-1]["partial"] is True
        assert body["rows"][-1]["turnover_rate"] is None
        assert captured["code"] == "002594"
        assert captured["indicators"] == ["ma", "macd"]
        assert captured["period"] == "day"
        assert captured["with_today"] is True

    def test_blank_indicators_means_empty_list(self, client, monkeypatch):
        """``indicators=`` 与不传是两件事：显式空表示「一个都不要算」。"""
        captured = {}
        monkeypatch.setattr(
            chart.chart_service,
            "get_kline",
            lambda code, **kwargs: captured.update(kwargs) or _kline_payload(indicators=[]),
        )
        client.get("/api/v1/chart/kline", params={"code": "002594", "indicators": ""})
        assert captured["indicators"] == []

        captured.clear()
        client.get("/api/v1/chart/kline", params={"code": "002594"})
        assert captured["indicators"] is None

    def test_value_error_maps_to_422(self, client, monkeypatch):
        def boom(code, **kwargs):
            raise ValueError("暂不支持的代码格式: AAPL")

        monkeypatch.setattr(chart.chart_service, "get_kline", boom)
        response = client.get("/api/v1/chart/kline", params={"code": "AAPL"})
        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "invalid_request"

    def test_upstream_error_maps_to_502(self, client, monkeypatch):
        def boom(code, **kwargs):
            raise ThsUpstreamError("上游 502")

        monkeypatch.setattr(chart.chart_service, "get_kline", boom)
        response = client.get("/api/v1/chart/kline", params={"code": "002594"})
        assert response.status_code == 502
        assert response.json()["detail"]["error"] == "upstream_unavailable"

    def test_limit_is_bounded_by_fastapi(self, client, monkeypatch):
        monkeypatch.setattr(chart.chart_service, "get_kline", lambda code, **kwargs: _kline_payload())
        assert client.get("/api/v1/chart/kline", params={"code": "002594", "limit": 0}).status_code == 422
        assert client.get("/api/v1/chart/kline", params={"code": "002594", "limit": 2000}).status_code == 422

    def test_code_is_required(self, client):
        assert client.get("/api/v1/chart/kline").status_code == 422


class TestTimeshareEndpoint:
    def test_returns_points(self, client, monkeypatch):
        monkeypatch.setattr(
            chart.chart_service,
            "get_timeshare",
            lambda code: {
                "code": "002594",
                "name": "比亚迪",
                "trade_date": "20261019",
                "prev_close": 82.72,
                "trade_times": ["0930-1130", "1300-1500"],
                "is_trading": True,
                "suspended": False,
                "points": [
                    {"time": "0930", "price": 82.5, "avg_price": 82.5, "volume": 56000.0, "amount": 4620000.0},
                ],
                "source": "ths",
            },
        )
        response = client.get("/api/v1/chart/timeshare", params={"code": "002594"})
        assert response.status_code == 200
        body = response.json()
        assert body["prev_close"] == 82.72
        assert body["points"][0]["avg_price"] == 82.5

    def test_upstream_error_maps_to_502(self, client, monkeypatch):
        def boom(code):
            raise ThsUpstreamError("上游取数失败")

        monkeypatch.setattr(chart.chart_service, "get_timeshare", boom)
        response = client.get("/api/v1/chart/timeshare", params={"code": "002594"})
        assert response.status_code == 502
