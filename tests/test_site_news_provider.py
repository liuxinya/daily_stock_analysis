# -*- coding: utf-8 -*-
"""免 Key 站内新闻搜索 provider 的回归测试。

全部用打桩的假 ``NewsSearchService``，不打任何真实上游。
"""

from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

from src.search_service import (
    SearchResponse,
    SearchService,
    SiteNewsSearchProvider,
    _ANALYSIS_SITE_NEWS_BUDGET_SEC,
)


def _item(
    *,
    title: str = "中科曙光发布最新经营数据",
    summary: str = "公司披露季度经营情况。",
    url: str = "https://example.com/news",
    platform: str = "cls",
    platform_label: str = "财联社",
    published_at: str = "2026-10-18T09:30:00+08:00",
) -> dict:
    return {
        "platform": platform,
        "platform_label": platform_label,
        "title": title,
        "summary": summary,
        "url": url,
        "source": platform_label,
        "published_at": published_at,
    }


def _payload(*, items=None, failed_platforms=None, query: str = "中科曙光") -> dict:
    items = list(items) if items is not None else [_item()]
    return {
        "platform": "all",
        "platform_label": "全部平台",
        "query": query,
        "window_days": 3,
        "items": items,
        "total": len(items),
        "pages_scanned": 5,
        "failed_platforms": list(failed_platforms or []),
        "fetched_at": "2026-10-18T15:19:00+08:00",
    }


class _FakeNewsSearchService:
    """记录调用参数的假聚合搜索服务。"""

    def __init__(self, *, payload=None, error: Exception | None = None) -> None:
        self.payload = payload if payload is not None else _payload()
        self.error = error
        self.calls: list = []

    def search_all(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.payload


class SiteNewsProviderIdentityTest(unittest.TestCase):
    """免 Key、且只服务「最新消息」维度。"""

    def setUp(self) -> None:
        self.provider = SiteNewsSearchProvider()

    def test_is_available_without_any_api_key(self) -> None:
        self.assertEqual([], self.provider._api_keys)
        self.assertTrue(self.provider.is_available)

    def test_serves_only_the_latest_news_dimension(self) -> None:
        self.assertTrue(self.provider.serves("latest_news"))
        for dimension in ("announcements", "earnings", "risk_check", "market_analysis", "industry"):
            with self.subTest(dimension=dimension):
                self.assertFalse(self.provider.serves(dimension))

    def test_paid_providers_still_serve_every_dimension(self) -> None:
        """能力声明是纯增量：没声明 served_dimensions 的 provider 不受影响。"""
        from src.search_service import TavilySearchProvider

        tavily = TavilySearchProvider(["key-1"])
        self.assertTrue(tavily.serves("announcements"))
        self.assertTrue(tavily.serves("latest_news"))


class SiteNewsProviderSearchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = SiteNewsSearchProvider()
        self.service = _FakeNewsSearchService()

    def _search(self, query: str = "中科曙光 603019 最新 新闻 重大 事件", **kwargs):
        with patch.object(
            SiteNewsSearchProvider, "_news_search", return_value=self.service
        ):
            return self.provider.search(query, **kwargs)

    def test_maps_entries_into_search_results(self) -> None:
        response = self._search(keyword="中科曙光", max_results=5)

        self.assertTrue(response.success)
        self.assertEqual(1, len(response.results))
        item = response.results[0]
        self.assertEqual("中科曙光发布最新经营数据", item.title)
        self.assertEqual("公司披露季度经营情况。", item.snippet)
        self.assertEqual("https://example.com/news", item.url)
        self.assertEqual("财联社", item.source)
        self.assertEqual("2026-10-18T09:30:00+08:00", item.published_date)

    def test_published_date_stays_parseable_by_the_downstream_normalizer(self) -> None:
        """站内条目给的是带偏移的 ISO8601，下游按时间窗过滤时必须能解析。"""
        response = self._search(keyword="中科曙光")

        normalized = SearchService._normalize_news_publish_date(
            response.results[0].published_date
        )
        self.assertEqual(date(2026, 10, 18), normalized)

    def test_explicit_keyword_wins_over_the_query_phrase(self) -> None:
        self._search(query="中科曙光 603019 最新 新闻", keyword="贵州茅台")

        self.assertEqual("贵州茅台", self.service.calls[0]["keyword"])

    def test_keyword_falls_back_to_the_first_query_token(self) -> None:
        self._search(query="中科曙光 603019 最新 新闻")

        self.assertEqual("中科曙光", self.service.calls[0]["keyword"])

    def test_blank_keyword_fails_without_calling_the_upstreams(self) -> None:
        response = self._search(query="   ")

        self.assertFalse(response.success)
        self.assertEqual([], response.results)
        self.assertEqual([], self.service.calls)

    def test_limit_and_analysis_budget_are_forwarded(self) -> None:
        self._search(keyword="中科曙光", max_results=7, days=3)

        call = self.service.calls[0]
        self.assertEqual(7, call["limit"])
        self.assertEqual(3, call["days"])
        self.assertEqual(_ANALYSIS_SITE_NEWS_BUDGET_SEC, call["budget_sec"])

    def test_partial_platform_failure_keeps_the_results(self) -> None:
        self.service.payload = _payload(failed_platforms=["ths", "jin10"])

        response = self._search(keyword="中科曙光")

        self.assertTrue(response.success)
        self.assertEqual(1, len(response.results))

    def test_all_platforms_failing_marks_the_response_unsuccessful(self) -> None:
        """全线失败要记成"搜了但没拿到"，而不是"没搜"。"""
        self.service.payload = _payload(
            items=[],
            failed_platforms=["cls", "ths", "eastmoney", "wallstreetcn", "jin10"],
        )

        response = self._search(keyword="中科曙光")

        self.assertFalse(response.success)
        self.assertEqual([], response.results)
        self.assertIn("全部站点检索失败", response.error_message or "")

    def test_upstream_exception_is_reported_as_a_failed_response(self) -> None:
        self.service.error = RuntimeError("boom")

        response = self._search(keyword="中科曙光")

        self.assertFalse(response.success)
        self.assertIn("boom", response.error_message or "")


class SiteNewsProviderRegistrationTest(unittest.TestCase):
    """站内搜索是「一个付费渠道都没有」时的兜底。"""

    def test_registered_when_no_paid_provider_is_configured(self) -> None:
        service = SearchService(searxng_public_instances_enabled=False)

        self.assertEqual([SiteNewsSearchProvider], [type(p) for p in service._providers])
        self.assertTrue(service.is_available)

    def test_not_registered_when_a_paid_provider_is_configured(self) -> None:
        """已有付费渠道时不该再多打 5 家站点，也不该把两套来源混进同一次检索。"""
        service = SearchService(
            tavily_keys=["dummy_key"],
            searxng_public_instances_enabled=False,
        )

        self.assertNotIn(SiteNewsSearchProvider, [type(p) for p in service._providers])


class SiteNewsProviderIntelDispatchTest(unittest.TestCase):
    """只装免 Key provider 时，情报检索只跑它能服务的维度。"""

    def test_only_the_latest_news_dimension_is_dispatched(self) -> None:
        service = SearchService(searxng_public_instances_enabled=False)
        self.assertEqual([SiteNewsSearchProvider], [type(p) for p in service._providers])

        seen: list = []

        def fake_search(query, **kwargs):
            seen.append(kwargs.get("keyword"))
            return SearchResponse(query=query, results=[], provider="stub")

        with patch.object(service._providers[0], "search", side_effect=fake_search):
            results = service.search_comprehensive_intel(
                stock_code="603019",
                stock_name="中科曙光",
                max_searches=5,
            )

        # 其余维度没有能服务的 provider，必须被跳过而不是把整轮检索 break 掉
        self.assertEqual(["latest_news"], list(results.keys()))
        self.assertEqual(["中科曙光"], seen)


if __name__ == "__main__":
    unittest.main()
