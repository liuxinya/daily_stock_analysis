# -*- coding: utf-8 -*-
"""首板 / 一进二回测的口径测试：买卖价、停牌、一字买不到、事件生成。全部用合成日 K，不连网。"""

from __future__ import annotations

import pytest

from src.services.daban import backtest as bt

DAYS = ["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16"]


def _bar(day, o, h, low, c, v=1e6):
    return {"time": day.replace("-", ""), "open": o, "high": h, "low": low, "close": c, "volume": v}


def _book(**series):
    return bt.KlineBook({code: [_bar(d, *ohlc) for d, ohlc in zip(["2026-10-09", *DAYS], bars)]
                         for code, bars in series.items()})


class TestOutcomes:
    def test_sealed_first_board_holds_one_more_day_when_next_day_limits(self):
        # 10.00 → 首板 11.00；次日涨停 12.10；第三天 12.00 收。
        book = _book(**{"600001": [(10, 10, 10, 10), (10, 11, 10, 11), (11.5, 12.1, 11.5, 12.1),
                                   (12.3, 12.5, 11.8, 12.0), (12, 12, 12, 12), (12, 12, 12, 12)]})
        out = bt.first_touch_outcome(book, "600001", DAYS, 0, sealed=True)
        assert out["s1_open"] == pytest.approx((12.3 / 11 - 1) * 100)
        assert out["s1_close"] == pytest.approx((12.0 / 11 - 1) * 100)

    def test_broken_touch_is_bought_at_limit_and_sold_next_day(self):
        # 盘中触板 11.00，收 10.40（炸板）；次日开 10.20、收 10.00。
        book = _book(**{"600002": [(10, 10, 10, 10), (10, 11, 10, 10.4), (10.2, 10.5, 9.9, 10.0),
                                   (10, 10, 10, 10), (10, 10, 10, 10), (10, 10, 10, 10)]})
        out = bt.first_touch_outcome(book, "600002", DAYS, 0, sealed=False)
        assert out["s1_open"] == pytest.approx((10.2 / 11 - 1) * 100)
        assert out["d0_close_vs_buy"] < 0

    def test_one_word_second_board_is_not_buyable(self):
        book = _book(**{"600003": [(10, 10, 10, 10), (10, 11, 10, 11), (12.1, 12.1, 12.1, 12.1),
                                   (12, 12, 12, 12), (12, 12, 12, 12), (12, 12, 12, 12)]})
        out = bt.one_into_two_outcome(book, "600003", DAYS, 0)
        assert out["n1_yizi"] and not out["buyable"] and out["s2_open"] is None

    def test_one_into_two_bought_at_second_limit(self):
        book = _book(**{"600004": [(10, 10, 10, 10), (10, 11, 10, 11), (11.3, 12.1, 11.2, 11.6),
                                   (11.4, 11.8, 11.0, 11.2), (11, 11, 11, 11), (11, 11, 11, 11)]})
        out = bt.one_into_two_outcome(book, "600004", DAYS, 0)
        assert out["buyable"] and not out["n1_sealed"]
        assert out["n1_gap"] == pytest.approx((11.3 / 11 - 1) * 100)
        assert out["s2_open"] == pytest.approx((11.4 / 12.1 - 1) * 100)

    def test_suspension_drops_the_event(self):
        rows = [_bar("2026-10-09", 10, 10, 10, 10), _bar("2026-10-12", 10, 11, 10, 11), _bar("2026-10-14", 11, 11, 11, 11)]
        book = bt.KlineBook({"600005": rows})
        assert bt.one_into_two_outcome(book, "600005", DAYS, 0) is None
        assert bt.broken_outcome(book, "600005", DAYS, 0) is None

    def test_twenty_percent_board_threshold(self):
        # 创业板 10% 不算涨停。
        book = _book(**{"300001": [(10, 10, 10, 10), (10, 11, 10, 11), (11, 11, 11, 11),
                                   (11, 11, 11, 11), (11, 11, 11, 11), (11, 11, 11, 11)]})
        assert bt.first_touch_outcome(book, "300001", DAYS, 0, sealed=True) is None


class TestReplay:
    def test_replay_produces_three_event_kinds_with_split(self):
        def zt(code, name, reason="人形机器人"):
            return {"code": code, "name": name, "reason": reason, "limit_up_type": "换手板", "first_time": "100000",
                    "hd_boards": 1, "turnover_rate": 8.0, "circ_mv": 5e9, "price": 10.0, "seal_amount": 5e7,
                    "open_num": 0, "suc_rate": 0.8}

        pools = {d: {"zt": [], "zb": [], "dt": [], "ladder": {}} for d in DAYS}
        pools[DAYS[0]]["zt"] = [zt("600001", "甲"), zt("600011", "乙"), zt("600012", "丙")]
        pools[DAYS[0]]["zb"] = [{
            "code": "600002", "name": "丁", "change_pct": 6.0, "open_num": 1,
            "first_time": "100000", "turnover_rate": 9.0, "circ_mv": 5e9, "price": 10.0,
        }]
        flat = [(10, 10, 10, 10)] * 5
        book = _book(
            **{"600001": [(10, 10, 10, 10), (10, 11, 10, 11), (11.3, 12.1, 11.2, 11.6), *flat[:3]],
               "600011": [(10, 10, 10, 10), (10, 11, 10, 11), *flat[:4]],
               "600012": [(10, 10, 10, 10), (10, 11, 10, 11), *flat[:4]],
               "600002": [(10, 10, 10, 10), (10, 11, 10, 10.6), *flat[:4]]}
        )
        result = bt.replay(pools, book)
        assert {e["code"] for e in result.one_into_two} == {"600001", "600011", "600012"}
        touched = {(e["code"], e["sealed"]) for e in result.touches}
        assert ("600002", False) in touched and ("600001", True) in touched
        assert result.broken and result.broken[0]["code"] == "600002"
        assert {e["split"] for e in result.one_into_two} <= {"前半段", "后半段"}
        report = bt.build_report(result, min_n=1)
        assert report["o12"]["all"]["n"] == 3
        assert "一进二" in bt.render_markdown(report)
