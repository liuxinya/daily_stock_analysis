# -*- coding: utf-8 -*-
"""推荐评分的特征与成绩：格子口径与回测标定一致、只读买入日之前的 K 线、H1 / 对照卖法、股性。"""

from __future__ import annotations

import pytest

from src.services.daban import rec_features as rf

from tests.daban_fixtures import rules, zt_row


def _bars(closes, *, opens=None, highs=None, lows=None, start=20260101, volumes=None):
    rows = []
    for i, close in enumerate(closes):
        o = (opens or closes)[i]
        rows.append({"time": str(start + i), "open": o, "high": (highs or [max(o, close)] * len(closes))[i],
                     "low": (lows or [min(o, close)] * len(closes))[i], "close": close,
                     "volume": (volumes or [1e6] * len(closes))[i]})
    return rf.make_bars(rows)


def _ctx(zt=(), zb=()):
    return rf.yesterday_context(zt_rows=list(zt), zb_rows=list(zb), history=[], yesterday_rows=[], previous=None,
                                recent_mains=[], rules=rules())


class TestCells:
    def test_yesterday_state_and_open_band(self):
        k = _bars([10.0] * 40)
        ctx = _ctx(zt=[zt_row("600001", "甲", boards=1, open_num=1, reason="人形机器人")])
        base = rf.base_features(code="600001", k=k, idx=len(k["t"]), ctx=ctx, tags=[])
        assert base["y_state"] == "昨日涨停·首板" and base["y_quality"] == rf.QUALITY_WEAK
        cells = rf.full_cells(base, today_open=9.9, ref_close=10.0)
        assert cells[rf.DIM_OPEN] == "昨日涨停·首板｜弱板（开过板/尾盘封）｜低开"
        assert cells[rf.DIM_MODE] == rf.MODE_INTRADAY
        assert cells[rf.DIM_PRICE] == "10cm｜10-20元"

    def test_20cm_open_band_is_scaled_to_10cm(self):
        k = _bars([30.0] * 40)
        base = rf.base_features(code="300001", k=k, idx=len(k["t"]), ctx=_ctx(), tags=[])
        # 20cm 高开 6% ≈ 10cm 高开 3%
        assert rf.full_cells(base, today_open=31.8, ref_close=30.0)[rf.DIM_OPEN] == "昨日未触板｜｜高开 2-5%"
        assert base[rf.DIM_PRICE] == "20cm｜20-40元"

    def test_open_at_trigger_is_open_mode(self):
        k = _bars([10.0] * 40)
        base = rf.base_features(code="600001", k=k, idx=len(k["t"]), ctx=_ctx(), tags=[])
        # 触发价 = 11 / 1.06 ≈ 10.377
        assert rf.full_cells(base, today_open=10.4, ref_close=10.0)[rf.DIM_MODE] == rf.MODE_AT_OPEN

    def test_only_reads_bars_before_idx(self):
        closes = [10.0] * 30 + [10.0] * 9 + [99.0]
        k = _bars(closes, volumes=[1e6] * 39 + [9e9])
        a = rf.base_features(code="600001", k=k, idx=39, ctx=_ctx(), tags=[])
        b = rf.base_features(code="600001", k=_bars(closes[:39]), idx=39, ctx=_ctx(), tags=[])
        assert {key: a[key] for key in rf.DIMS if key in a} == {key: b[key] for key in rf.DIMS if key in b}
        assert a["temper"] == b["temper"]

    def test_volume_ratio_band(self):
        k = _bars([10.0] * 40, volumes=[1e6] * 39 + [4e6])
        base = rf.base_features(code="600001", k=k, idx=40, ctx=_ctx(), tags=[])
        assert base[rf.DIM_VOLUME] == "≥3 倍" and base["volume_ratio"] == pytest.approx(4.0)

    def test_theme_link_uses_history_tags(self):
        k = _bars([10.0] * 40)
        zt = [zt_row("600010", "龙", boards=2, reason="人形机器人"), zt_row("600011", "二", reason="人形机器人")]
        base = rf.base_features(code="600001", k=k, idx=40, ctx=_ctx(zt=zt), tags=["人形机器人"])
        assert base["theme"]["key"] == "人形机器人" and base[rf.DIM_THEME] == "启动｜新题材"
        assert base[rf.DIM_FOLLOW] == "无数据｜启动"     # 龙头日 K 没给：相关性无数据


class TestScore:
    def test_score_and_observe(self):
        table = rules()["rec_score"]
        cells = {rf.DIM_OPEN: "昨日涨停·首板｜硬板｜低开", rf.DIM_PRICE: "20cm｜≥40元", rf.DIM_VOLUME: "≥3 倍"}
        out = rf.score_cells(cells, table)
        assert out["total"] == -10 and {i["key"] for i in out["items"]} == {"cm20_40", "vol3"}
        assert out["observe"]["key"] == "o12_hard_low"   # 一进二观察期：照常推荐、不扣分

    def test_range(self):
        lo, hi = rf.score_range(rules()["rec_score"])
        assert (lo, hi) == (-31, 5)      # 与评分体系 v2 定稿一致


class TestExits:
    def test_unsealed_gap_up_sells_at_open(self):
        k = _bars([10.0, 10.5, 10.4], opens=[10.0, 10.2, 10.8], highs=[10.0, 10.9, 10.9])
        out = rf.exit_returns(k, 1, 10.5, 10)
        assert out["sealed"] is False and out["h1"] == pytest.approx((10.8 / 10.5 - 1) * 100, abs=1e-3)
        assert out["h1_rule"] == rf.H1_RULE_UNSEALED_OPEN and out["status"] == "done"

    def test_unsealed_gap_down_holds_to_close(self):
        k = _bars([10.0, 10.5, 10.6], opens=[10.0, 10.2, 10.3])
        out = rf.exit_returns(k, 1, 10.5, 10)
        assert out["h1"] == pytest.approx((10.6 / 10.5 - 1) * 100, abs=1e-3) and out["h1_rule"] == rf.H1_RULE_UNSEALED_CLOSE

    def test_sealed_rides_limit_ups(self):
        # D 封住 11.0，D+1 再涨停 12.1，D+2 收 12.0 不涨停：D+2 收盘卖
        k = _bars([10.0, 11.0, 12.1, 12.0], opens=[10.0, 10.5, 11.5, 12.3])
        out = rf.exit_returns(k, 1, 10.4, 10)
        assert out["sealed"] is True and out["h1_exit"] == k["t"][3]
        assert out["h1"] == pytest.approx((12.0 / 10.4 - 1) * 100, abs=1e-3)

    def test_live_holding_until_exit_bar_closes(self):
        k = _bars([10.0, 11.0, 12.1], opens=[10.0, 10.5, 11.5])
        out = rf.exit_returns(k, 1, 10.4, 10, live=True)
        assert out["status"] == "holding" and out["h1"] is None
        assert out["f1"] == pytest.approx((11.5 / 10.4 - 1) * 100, abs=1e-3)

    def test_yizi_down_defers_to_next_open(self):
        k = _bars([10.0, 10.5, 9.45, 9.0], opens=[10.0, 10.2, 9.45, 9.2], highs=[10.0, 10.9, 9.45, 9.3],
                  lows=[10.0, 10.1, 9.45, 8.9])
        out = rf.exit_returns(k, 1, 10.5, 10)
        assert out["h1"] == pytest.approx((9.2 / 10.5 - 1) * 100, abs=1e-3)
        live = rf.exit_returns(k, 1, 10.5, 10, last=2, live=True)
        assert live["h1"] is None and live["status"] == "holding"

    def test_sealed_override(self):
        k = _bars([10.0, 10.98, 11.0], opens=[10.0, 10.5, 11.2])
        assert rf.exit_returns(k, 1, 10.4, 10)["sealed"] is True        # 9.8%，在 TOL 内
        assert rf.exit_returns(k, 1, 10.4, 10, sealed=False)["h1_rule"] == rf.H1_RULE_UNSEALED_OPEN


class TestTemper:
    def test_seal_rate_and_cont_rate(self):
        # 三次冲板：两次封住（其中一次次日连板），一次没封住
        closes = [10, 11.0, 12.1, 12.0, 12.5, 13.75, 13.0, 13.2, 14.0, 13.5, 13.5]
        highs = [10, 11.0, 12.1, 12.2, 12.6, 13.75, 13.2, 13.3, 14.5, 13.6, 13.5]
        opens = [float(closes[0])] + [float(c) for c in closes[:-1]]          # 每天平开，盘中被买上去
        k = _bars([float(c) for c in closes], opens=opens, highs=[float(h) for h in highs])
        info = rf.temper(k, len(k["t"]), 10)
        assert info["approaches"] >= info["sealed"] >= 1 and info["limits"] >= 2
        assert info["cont_rate"] is not None and info["covered"] is True
        assert "冲板" in rf.temper_text(info)
