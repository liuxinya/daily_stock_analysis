import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data_provider.akshare_fetcher import AkshareFetcher


def _make_akshare_fetcher() -> AkshareFetcher:
    with patch(
        "data_provider.akshare_fetcher.get_config",
        return_value=SimpleNamespace(enable_eastmoney_patch=False),
    ):
        return AkshareFetcher(sleep_min=0, sleep_max=0)


@pytest.fixture
def _available_breaker():
    """ETF 行情分流不受熔断影响：固定返回“可用”的熔断器。"""
    breaker = MagicMock()
    breaker.is_available.return_value = True
    with patch(
        "data_provider.akshare_fetcher.get_realtime_circuit_breaker",
        return_value=breaker,
    ):
        yield breaker


class TestEtfRealtimeQuoteRouting:
    """回归：ETF 行情按 source 分流，复用与个股一致的腾讯/新浪单码直连接口。

    此前所有 source 都被并到 ``fund_etf_spot_em``（东财 push2 全量拉取），导致
    默认优先级里本应最稳的腾讯直连对 ETF 失效——个股能出行情、ETF 却 404。
    """

    def test_tencent_source_uses_direct_quote_not_fund_spot(self, _available_breaker):
        fetcher = _make_akshare_fetcher()
        with patch.object(
            fetcher, "_get_stock_realtime_quote_tencent", return_value="tx"
        ) as tx, patch.object(
            fetcher, "_get_etf_realtime_quote", return_value="etf"
        ) as etf, patch.object(
            fetcher, "_get_stock_realtime_quote_sina", return_value="sina"
        ) as sina:
            result = fetcher.get_realtime_quote("159852", source="tencent")

        assert result == "tx"
        tx.assert_called_once_with("159852")
        etf.assert_not_called()
        sina.assert_not_called()

    def test_sina_source_uses_direct_quote_not_fund_spot(self, _available_breaker):
        fetcher = _make_akshare_fetcher()
        with patch.object(
            fetcher, "_get_stock_realtime_quote_sina", return_value="sina"
        ) as sina, patch.object(
            fetcher, "_get_etf_realtime_quote", return_value="etf"
        ) as etf, patch.object(
            fetcher, "_get_stock_realtime_quote_tencent", return_value="tx"
        ) as tx:
            result = fetcher.get_realtime_quote("515230", source="sina")

        assert result == "sina"
        sina.assert_called_once_with("515230")
        etf.assert_not_called()
        tx.assert_not_called()

    def test_em_source_still_uses_fund_etf_spot(self, _available_breaker):
        fetcher = _make_akshare_fetcher()
        with patch.object(
            fetcher, "_get_etf_realtime_quote", return_value="etf"
        ) as etf, patch.object(
            fetcher, "_get_stock_realtime_quote_tencent", return_value="tx"
        ) as tx, patch.object(
            fetcher, "_get_stock_realtime_quote_sina", return_value="sina"
        ) as sina:
            result = fetcher.get_realtime_quote("159852", source="em")

        assert result == "etf"
        etf.assert_called_once_with("159852")
        tx.assert_not_called()
        sina.assert_not_called()

    def test_tencent_source_honors_matching_circuit_breaker_key(self, _available_breaker):
        """ETF 走腾讯直连时应检查 ``akshare_tencent`` 熔断键，而非 ``akshare_etf``。"""
        fetcher = _make_akshare_fetcher()
        with patch.object(fetcher, "_get_stock_realtime_quote_tencent", return_value="tx"):
            fetcher.get_realtime_quote("159852", source="tencent")

        _available_breaker.is_available.assert_called_once_with("akshare_tencent")
