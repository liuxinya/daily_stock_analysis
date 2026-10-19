import os
import sys
from types import SimpleNamespace

import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data_provider import ths_chart_fetcher as ths
from src.services import chart_service
from src.services.alert_indicators import compute_kdj
from src.stock_analyzer import StockTrendAnalyzer

CLOSES = [
    10.0, 10.4, 10.2, 10.9, 11.3, 11.1, 11.8, 12.2, 12.0, 12.6,
    12.4, 13.1, 12.8, 13.4, 13.9, 13.5, 14.2, 14.0, 14.7, 15.1,
    14.8, 15.5, 15.2, 15.9, 16.3, 16.0, 16.7, 17.1, 16.8, 17.4,
    17.1, 17.8, 18.2, 17.9, 18.5, 18.1, 18.8, 19.2, 18.9, 19.5,
]


def _rows(closes=None):
    closes = closes or CLOSES
    out = []
    for index, close in enumerate(closes):
        out.append(
            {
                "time": f"202601{index + 1:02d}",
                "open": close - 0.2,
                "high": close + 0.3,
                "low": close - 0.4,
                "close": close,
                "volume": 1000.0 + index,
                "amount": (1000.0 + index) * close,
                "turnover_rate": 0.5,
                "partial": False,
            }
        )
    return out


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    """缓存关掉，避免用例之间互相串。"""
    monkeypatch.setattr(
        chart_service,
        "get_config",
        lambda: SimpleNamespace(ths_chart_timeout_sec=10, ths_chart_cache_ttl_sec=0),
    )
    chart_service.clear_cache()


class TestIndicatorConsistency:
    """指标必须和报告 / 告警用的是同一份口径 —— 这是把公式提到公共函数的意义。"""

    def test_macd_matches_stock_analyzer(self):
        rows = _rows()
        chart_service._attach_indicators(rows, ("macd",))
        expected = StockTrendAnalyzer()._calculate_macd(pd.DataFrame({"close": CLOSES}))
        assert rows[-1]["dif"] == pytest.approx(expected["MACD_DIF"].iloc[-1], abs=1e-4)
        assert rows[-1]["dea"] == pytest.approx(expected["MACD_DEA"].iloc[-1], abs=1e-4)
        assert rows[-1]["macd"] == pytest.approx(expected["MACD_BAR"].iloc[-1], abs=1e-4)

    def test_kdj_matches_alert_indicators_and_j_formula(self):
        rows = _rows()
        chart_service._attach_indicators(rows, ("kdj",))
        frame = pd.DataFrame(
            {
                "high": [row["high"] for row in rows],
                "low": [row["low"] for row in rows],
                "close": [row["close"] for row in rows],
            }
        )
        expected = compute_kdj(frame)
        last = rows[-1]
        assert last["k"] == pytest.approx(expected["k"].iloc[-1], abs=1e-3)
        assert last["d"] == pytest.approx(expected["d"].iloc[-1], abs=1e-3)
        assert last["j"] == pytest.approx(3 * last["k"] - 2 * last["d"], abs=1e-2)

    def test_ma_warmup_is_null_not_zero(self):
        rows = _rows()
        chart_service._attach_indicators(rows, ("ma",))
        assert rows[0]["ma20"] is None
        assert rows[-1]["ma20"] == pytest.approx(sum(CLOSES[-20:]) / 20, abs=1e-3)

    def test_unknown_indicator_is_rejected(self):
        with pytest.raises(ValueError, match="不支持的指标"):
            chart_service.get_kline("002594", indicators=["boll"])


def _stub_payload(rows, *, period="day", adjust="qfq", upstream_today=None):
    return {
        "code": "002594",
        "name": "比亚迪",
        "period": period,
        "adjust": adjust,
        "rows": rows,
        "total_available": "3702",
        "listed_since": "20110630",
        "trading_hours": "0930-1130,1300-1500",
        "upstream_today": upstream_today,
    }


class TestGetKline:
    def test_synthesizes_today_and_flags_partial(self, monkeypatch):
        rows = _rows()
        monkeypatch.setattr(ths, "fetch_kline", lambda *a, **k: _stub_payload(rows))
        monkeypatch.setattr(
            ths,
            "fetch_snapshot",
            lambda *a, **k: {
                "trade_date": "20260299",
                "open": 19.4, "high": 20.1, "low": 19.3, "price": 20.0,
                "volume": 123.0, "amount": 2460.0, "suspended": False,
            },
        )
        result = chart_service.get_kline("002594", limit=5, indicators=["ma"])
        assert result["today_source"] == "snapshot"
        assert len(result["rows"]) == 5
        last = result["rows"][-1]
        assert (last["time"], last["close"], last["partial"]) == ("20260299", 20.0, True)
        # 预热段被裁掉后，首根的 MA20 仍应有值（多取 80 根算完再裁）
        assert result["rows"][0]["ma20"] is not None

    def test_replaces_upstream_placeholder_for_same_day(self, monkeypatch):
        rows = _rows()
        rows.append(
            {
                "time": "20260299", "open": 19.5, "high": 19.5, "low": 19.5, "close": 19.5,
                "volume": 0.0, "amount": 0.0, "turnover_rate": None, "partial": False,
            }
        )
        monkeypatch.setattr(ths, "fetch_kline", lambda *a, **k: _stub_payload(rows))
        monkeypatch.setattr(
            ths,
            "fetch_snapshot",
            lambda *a, **k: {
                "trade_date": "20260299",
                "open": 19.4, "high": 20.1, "low": 19.3, "price": 20.0,
                "volume": 123.0, "amount": 2460.0, "suspended": False,
            },
        )
        result = chart_service.get_kline("002594", limit=3)
        times = [row["time"] for row in result["rows"]]
        assert times.count("20260299") == 1
        assert result["rows"][-1]["close"] == 20.0

    def test_hfq_does_not_synthesize(self, monkeypatch):
        """快照是不复权价，换算到后复权需要复权因子，宁可少一根。"""
        called = []
        monkeypatch.setattr(ths, "fetch_kline", lambda *a, **k: _stub_payload(_rows(), adjust="hfq"))
        monkeypatch.setattr(ths, "fetch_snapshot", lambda *a, **k: called.append(1))
        result = chart_service.get_kline("002594", adjust="hfq", limit=3)
        assert result["today_source"] is None
        assert called == []

    def test_snapshot_failure_keeps_history(self, monkeypatch):
        def boom(*a, **k):
            raise ths.ThsUpstreamError("上游 502")

        monkeypatch.setattr(ths, "fetch_kline", lambda *a, **k: _stub_payload(_rows()))
        monkeypatch.setattr(ths, "fetch_snapshot", boom)
        result = chart_service.get_kline("002594", limit=3)
        assert result["today_source"] is None
        assert len(result["rows"]) == 3

    def test_flags_unfinished_week_from_upstream_today(self, monkeypatch):
        rows = _rows()
        monkeypatch.setattr(
            ths,
            "fetch_kline",
            lambda *a, **k: _stub_payload(rows, period="week", upstream_today=rows[-1]["time"]),
        )
        result = chart_service.get_kline("002594", period="week", limit=3)
        assert result["today_source"] == "upstream"
        assert result["rows"][-1]["partial"] is True
        assert result["rows"][-2]["partial"] is False


class TestFetchRetry:
    def test_retries_then_succeeds(self, monkeypatch):
        attempts = {"n": 0}
        body = 'quotebridge_x({"data":"20260101,1,2,0.5,1.5,10,15.0,0.1,,,0"})'

        def fake_get(url, **kwargs):
            attempts["n"] += 1
            if attempts["n"] == 1:
                return SimpleNamespace(status_code=502, text="502 Bad Gateway")
            return SimpleNamespace(status_code=200, text=body)

        monkeypatch.setattr(ths.requests, "get", fake_get)
        payload = ths._fetch_jsonp("/x", required=("data",))
        assert attempts["n"] == 2
        assert ths.parse_kline_rows(payload["data"])[0]["close"] == 1.5

    def test_gives_up_after_max_attempts(self, monkeypatch):
        monkeypatch.setattr(
            ths.requests,
            "get",
            lambda url, **kwargs: SimpleNamespace(status_code=504, text=""),
        )
        with pytest.raises(ths.ThsUpstreamError, match="已重试"):
            ths._fetch_jsonp("/x", required=("data",))

    def test_does_not_retry_404(self, monkeypatch):
        attempts = {"n": 0}

        def fake_get(url, **kwargs):
            attempts["n"] += 1
            return SimpleNamespace(status_code=404, text="")

        monkeypatch.setattr(ths.requests, "get", fake_get)
        with pytest.raises(ths.ThsUpstreamError, match="404"):
            ths._fetch_jsonp("/x", required=("data",))
        assert attempts["n"] == 1


class TestCache:
    def test_reuses_payload_within_ttl(self, monkeypatch):
        monkeypatch.setattr(
            chart_service,
            "get_config",
            lambda: SimpleNamespace(ths_chart_timeout_sec=10, ths_chart_cache_ttl_sec=60),
        )
        chart_service.clear_cache()
        calls = {"n": 0}

        def counting_fetch(*a, **k):
            calls["n"] += 1
            return _stub_payload(_rows())

        monkeypatch.setattr(ths, "fetch_kline", counting_fetch)
        monkeypatch.setattr(ths, "fetch_snapshot", lambda *a, **k: {"trade_date": None})
        chart_service.get_kline("002594", limit=3)
        chart_service.get_kline("002594", limit=3)
        assert calls["n"] == 1
        chart_service.clear_cache()
        chart_service.get_kline("002594", limit=3)
        assert calls["n"] == 2
