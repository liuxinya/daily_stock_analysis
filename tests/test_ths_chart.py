import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data_provider import ths_chart_fetcher as ths


def _jsonp(body: str, name: str = "quotebridge_v6_line_hs_002594_01_last") -> str:
    return f"{name}({body})"


DAY_BODY = (
    '{"name":"\\u6bd4\\u4e9a\\u8fea","total":"3702","num":3,"start":"20110630",'
    '"today":"20261019","rt":"0930-1130,1300-1500","year":{"2026":189},'
    '"data":"20261015,80.63,82.47,80.11,82.24,24275403,1982838700.00,0.696,,,0;'
    '20261016,82.24,83.39,81.85,82.72,20968562,1733332800.00,0.601,,25300.00,2092816;'
    '20261019,,,,82.70,0,0.00,0.000,,,0"}'
)


class TestParseJsonp:
    def test_parses_valid_payload(self):
        payload = ths._parse_jsonp(_jsonp(DAY_BODY), url="u", required=("data",))
        assert payload["name"] == "比亚迪"
        assert payload["today"] == "20261019"

    def test_rejects_truncated_body(self):
        """上游会返回 HTTP 200 + 截断 body，这是唯一的拦截点。"""
        truncated = _jsonp(DAY_BODY)[:120]
        with pytest.raises(ths.ThsUpstreamError, match="JSONP 包裹不完整"):
            ths._parse_jsonp(truncated, url="u", required=("data",))

    def test_rejects_non_jsonp(self):
        with pytest.raises(ths.ThsUpstreamError, match="不是预期的 JSONP"):
            ths._parse_jsonp("<html>502 Bad Gateway</html>", url="u", required=("data",))

    def test_rejects_missing_required_field(self):
        with pytest.raises(ths.ThsUpstreamError, match="缺少字段"):
            ths._parse_jsonp(_jsonp('{"name":"x"}'), url="u", required=("data",))

    def test_rejects_broken_json(self):
        with pytest.raises(ths.ThsUpstreamError, match="JSON 解析失败"):
            ths._parse_jsonp(_jsonp('{"data":'), url="u", required=("data",))


class TestParseRows:
    def test_drops_intraday_placeholder_row(self):
        """``20261019,,,,82.70,0,0.00`` 这种 OHLC 全空的占位行不能当成一根 K 线。"""
        rows = ths.parse_kline_rows(
            "20261016,82.24,83.39,81.85,82.72,20968562,1733332800.00,0.601,,,0;"
            "20261019,,,,82.70,0,0.00,0.000,,,0"
        )
        assert [row["time"] for row in rows] == ["20261016"]
        assert rows[0]["open"] == 82.24
        assert rows[0]["turnover_rate"] == 0.601
        assert rows[0]["partial"] is False

    def test_ignores_blank_and_short_lines(self):
        assert ths.parse_kline_rows("") == []
        assert ths.parse_kline_rows(";;20261016,1,2,3") == []


class TestNormalizeSymbol:
    @pytest.mark.parametrize(
        "code",
        ["002594", "sz002594", "SZ002594", "002594.SZ", " 002594 "],
    )
    def test_accepts_a_share_forms(self, code):
        assert ths.normalize_symbol(code) == "hs_002594"

    def test_accepts_etf(self):
        assert ths.normalize_symbol("510300") == "hs_510300"

    @pytest.mark.parametrize("code", ["AAPL", "00700", "HK00700", "7203.T", "abc", ""])
    def test_rejects_unverified_markets(self, code):
        """港股 / 美股前缀实测能返回 200，但没做数值交叉验证，v1 显式拒绝。"""
        with pytest.raises(ValueError):
            ths.normalize_symbol(code)


class TestResolvePeriodCode:
    @pytest.mark.parametrize(
        ("adjust", "expected"),
        [("none", "00"), ("qfq", "01"), ("hfq", "02")],
    )
    def test_day_codes_by_adjust(self, adjust, expected):
        assert ths.resolve_period_code("day", adjust) == (expected, adjust)

    @pytest.mark.parametrize(
        ("period", "expected"),
        [("week", "11"), ("month", "21"), ("quarter", "91")],
    )
    def test_non_day_is_always_qfq(self, period, expected):
        """周 / 月 / 季上游只有前复权一份，如实回报实际口径而不是调用方传的值。"""
        assert ths.resolve_period_code(period, "hfq") == (expected, "qfq")

    @pytest.mark.parametrize("period", ["min5", "min1", "year", ""])
    def test_rejects_unsupported_period(self, period):
        if period == "":
            assert ths.resolve_period_code(period, "qfq") == ("01", "qfq")
            return
        with pytest.raises(ValueError, match="不支持的周期"):
            ths.resolve_period_code(period, "qfq")

    def test_rejects_unsupported_adjust(self):
        with pytest.raises(ValueError, match="不支持的复权口径"):
            ths.resolve_period_code("day", "bfq")


SNAPSHOT = {
    "trade_date": "20261019",
    "open": 82.5,
    "high": 84.15,
    "low": 82.3,
    "price": 83.53,
    "volume": 19563278.0,
    "amount": 1634894400.0,
    "suspended": False,
}


class TestBuildTodayRow:
    def test_builds_partial_row(self):
        row = ths.build_today_row(dict(SNAPSHOT))
        assert row["time"] == "20261019"
        assert (row["open"], row["high"], row["low"], row["close"]) == (82.5, 84.15, 82.3, 83.53)
        assert row["partial"] is True
        assert row["turnover_rate"] is None

    @pytest.mark.parametrize(
        "override",
        [
            {"suspended": True},
            {"volume": 0},
            {"open": None},
            {"trade_date": None},
            {"price": None},
        ],
    )
    def test_returns_none_when_not_trustworthy(self, override):
        """宁可少一根，不要给一根假的。"""
        snapshot = dict(SNAPSHOT)
        snapshot.update(override)
        assert ths.build_today_row(snapshot) is None
