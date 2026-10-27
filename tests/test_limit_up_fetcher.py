# -*- coding: utf-8 -*-
"""涨停数据中心适配器（同花顺 dataapi/limit_up + 东财 push2ex）的离线测试。

上游两个坑必须锁住：HTTP 200 + 截断 body（JSON 解析失败要当上游异常，不能当空池），
以及东财价格单位是厘、``getYesterdayZTPool`` 的 ``zdp`` 是「今天」的涨跌幅。
"""

from __future__ import annotations

import os

import pytest

from data_provider import limit_up_fetcher as lu


class _Resp:
    def __init__(self, payload=None, *, status=200, broken=False):
        self._payload = payload
        self.status_code = status
        self._broken = broken
        self.content = b"{\"status_code\":0,\"da" if broken else b"{}"

    def json(self):
        if self._broken:
            raise ValueError("truncated")
        return self._payload


def _ths_item(code, name, **extra):
    item = {
        "code": code, "name": name, "latest": 10.0, "change_rate": 10.0, "turnover_rate": 8.0,
        "currency_value": 5e9, "high_days": "首板", "limit_up_type": "换手板", "open_num": None,
        "change_tag": "FIRST_LIMIT", "first_limit_up_time": "1792373101", "last_limit_up_time": "1792373101",
        "order_amount": 5e7, "limit_up_suc_rate": 0.8, "reason_type": "人形机器人+业绩预增", "is_new": 0,
        "is_again_limit": 0, "time_preview": [1, 2, 3],
    }
    item.update(extra)
    return item


class TestParsers:
    @pytest.mark.parametrize("text,expected", [
        ("21天12板", (21, 12)), ("首板", (1, 1)), ("3天3板", (3, 3)), ("", (0, 0)), (None, (0, 0)), ("abc", (0, 0)),
    ])
    def test_parse_high_days(self, text, expected):
        assert lu.parse_high_days(text) == expected

    def test_epoch_seconds_become_beijing_hhmmss(self):
        # 1792373101 = 2026-10-19 09:25:01 北京时间（集合竞价封板）。
        assert lu.epoch_to_hhmmss("1792373101") == "092501"
        assert lu.epoch_to_hhmmss(None) == ""
        assert lu.epoch_to_hhmmss("0") == ""

    def test_universe_drops_st_and_bse_by_default(self):
        assert lu.in_universe("600000", "浦发银行")
        assert not lu.in_universe("600001", "*ST某某")
        assert not lu.in_universe("920006", "晟楠科技")
        assert lu.in_universe("920006", "晟楠科技", include_bse=True)
        assert not lu.in_universe("abc", "x")

    def test_ths_row_keeps_reason_and_amount_estimate(self):
        row = lu.normalize_ths_row(_ths_item("605058", "澳弘电子", high_days="21天12板", open_num=4,
                                             change_tag="LIMIT_BACK"))
        assert row["reason"] == "人形机器人+业绩预增"
        assert (row["hd_days"], row["hd_boards"]) == (21, 12)
        assert row["open_num"] == 4 and row["change_tag"] == "LIMIT_BACK"
        # 池接口不带成交额，用 换手 × 流通市值 估算。
        assert row["amount"] == pytest.approx(0.08 * 5e9)
        # 连板数由天梯回填，归一化时一律先记 1。
        assert row["boards"] == 1

    def test_em_row_converts_price_from_li_to_yuan(self):
        row = lu.normalize_em_row("previous", {"c": "000576", "n": "甘化科工", "p": 9170, "zdp": -4.97,
                                               "ylbc": 2, "yfbt": 95057, "zttj": {"days": 3, "ct": 2}})
        assert row["price"] == pytest.approx(9.17)
        assert row["change_pct"] == pytest.approx(-4.97)
        assert row["y_boards"] == 2 and row["y_first_time"] == "095057"
        assert row["high_days"] == "3天2板"


class TestThsPool:
    def test_pages_until_total(self, monkeypatch):
        pages = {
            1: {"status_code": 0, "data": {"page": {"total": 3}, "info": [_ths_item("600001", "甲"), _ths_item("600002", "乙")],
                                           "date": "20261019", "limit_up_count": {"today": {"num": 3}},
                                           "trade_status": {"id": "closed"}}},
            2: {"status_code": 0, "data": {"page": {"total": 3}, "info": [_ths_item("600003", "丙")]}},
        }
        calls = []

        def fake_get(url, params=None, **kwargs):
            calls.append(params["page"])
            return _Resp(pages[params["page"]])

        monkeypatch.setattr(lu, "_THS_PAGE_SIZE", 2)
        monkeypatch.setattr(lu.requests, "get", fake_get)
        result = lu.fetch_ths_pool("zt", "2026-10-19")
        assert calls == [1, 2]
        assert [r["code"] for r in result["rows"]] == ["600001", "600002", "600003"]
        assert result["date"] == "2026-10-19"
        assert result["counts"]["limit_up"]["num"] == 3
        assert result["trade_status"] == "closed"

    def test_truncated_body_is_an_upstream_error_not_an_empty_pool(self, monkeypatch):
        monkeypatch.setattr(lu.time, "sleep", lambda _s: None)
        monkeypatch.setattr(lu.requests, "get", lambda *a, **k: _Resp(broken=True))
        with pytest.raises(lu.LimitUpUpstreamError):
            lu.fetch_ths_pool("zt", "2026-10-19")

    def test_retries_wait_between_attempts(self, monkeypatch):
        """开盘那几分钟东财会直接断连：两次请求挤在同一秒里等于只试了一次，必须退避。"""
        slept, calls = [], []
        monkeypatch.setattr(lu.time, "sleep", lambda s: slept.append(s))

        def boom(*a, **k):
            calls.append(1)
            raise lu.requests.ConnectionError("Connection aborted")

        monkeypatch.setattr(lu.requests, "get", boom)
        with pytest.raises(lu.LimitUpUpstreamError):
            lu.fetch_em_changes("rise", timeout=1)
        assert len(calls) == lu._MAX_ATTEMPTS
        assert len(slept) == lu._MAX_ATTEMPTS - 1
        assert all(s >= lu._RETRY_BACKOFF_SEC for s in slept)

    def test_nonzero_status_code_raises(self, monkeypatch):
        monkeypatch.setattr(lu.requests, "get", lambda *a, **k: _Resp({"status_code": -1, "status_msg": "bad"}))
        with pytest.raises(lu.LimitUpUpstreamError):
            lu.fetch_ths_pool("zt", "2026-10-19")

    def test_ladder_maps_code_to_board_count(self, monkeypatch):
        payload = {"status_code": 0, "data": [
            {"height": 3, "code_list": [{"code": "002889", "continue_num": 3}]},
            {"height": 2, "code_list": [{"code": "000576", "continue_num": 2}, {"code": "002085"}]},
        ]}
        monkeypatch.setattr(lu.requests, "get", lambda *a, **k: _Resp(payload))
        assert lu.fetch_ths_ladder("20261019") == {"002889": 3, "000576": 2, "002085": 2}


class TestEmPool:
    def test_null_data_means_empty_pool(self, monkeypatch):
        monkeypatch.setattr(lu.requests, "get", lambda *a, **k: _Resp({"rc": 0, "data": None}))
        assert lu.fetch_em_pool("previous", "2026-10-18") == []

    def test_requires_a_date(self):
        with pytest.raises(ValueError):
            lu.fetch_em_pool("zt", None)


# ---------------------------------------------------------------------------
# 网络冒烟测试（显式开启才跑）：确认上游字段没变
# ---------------------------------------------------------------------------

_NETWORK = os.getenv("DABAN_NETWORK_TESTS") == "1"


@pytest.mark.network
@pytest.mark.skipif(not _NETWORK, reason="需要网络：设 DABAN_NETWORK_TESTS=1 开启")
class TestNetworkSmoke:
    DAY = "2026-10-19"   # 已收盘的历史交易日，数字固定：涨停 46、跌停 22、最高 3 板

    def test_ths_pools_and_ladder(self):
        zt = lu.fetch_ths_pool("zt", self.DAY, timeout=20)
        assert len(zt["rows"]) >= 30 and all(r["reason"] for r in zt["rows"])
        assert zt["counts"]["limit_up"].get("num")
        assert lu.fetch_ths_pool("dt", self.DAY, timeout=20)["rows"]
        ladder = lu.fetch_ths_ladder(self.DAY, timeout=20)
        assert ladder and max(ladder.values()) >= 2

    def test_ths_blocks(self):
        blocks = lu.fetch_ths_blocks(self.DAY, timeout=30)
        assert blocks and blocks[0]["members"] and blocks[0]["days"] >= 1

    def test_em_previous_pool(self):
        rows = lu.fetch_em_pool("previous", "2026-10-20", timeout=20)
        assert rows and all("y_boards" in r for r in rows)
