# -*- coding: utf-8 -*-
"""多平台新闻搜索的回归测试。

适配器层用固定 JSON 打桩 ``_http_get`` / ``_http_post``（不打网络），服务层用假 fetcher
覆盖翻页、时间窗、相关性过滤与去重；endpoint 层直接调函数验证 400/503 映射。
"""

from __future__ import annotations

import time
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from fastapi import HTTPException

from api.v1.endpoints import news_search as news_search_endpoint
from src.services import news_search_service
from src.services.news_search_service import (
    NewsSearchEntry,
    NewsSearchService,
    NewsSearchServiceError,
    NewsSearchUpstreamError,
    SHANGHAI_TZ,
    _matches_keyword,
    _fetch_cls_page,
    _fetch_eastmoney_page,
    _fetch_jin10_page,
    _fetch_ths_page,
    _fetch_wallstreetcn_page,
)


class _FakeResponse:
    def __init__(self, *, text: str = "", payload=None) -> None:
        self.text = text
        self._payload = payload

    def raise_for_status(self) -> None:  # noqa: D102 - 测试替身
        return None

    def json(self):
        return self._payload


def _entry(title: str, *, minutes_ago: int = 10, summary: str = "", platform: str = "cls",
           label: str = "财联社", url: str = "", source: str = "") -> NewsSearchEntry:
    return NewsSearchEntry(
        platform=platform,
        platform_label=label,
        title=title,
        summary=summary,
        url=url,
        published_at=datetime.now(SHANGHAI_TZ) - timedelta(minutes=minutes_ago),
        source=source,
    )


class PlatformNormalizeTest(unittest.TestCase):
    """各平台一页响应 -> 统一条目。"""

    def test_cls_strips_bracket_title_and_builds_detail_url(self) -> None:
        payload = {"errno": 0, "data": {"telegram": {"data": [
            {"descr": "【比亚迪固态电池提速】财联社10月13日电，多家车企加码布局。",
             "id": 909, "time": 1791957281},
        ]}}}
        with patch.object(news_search_service, "_http_post", return_value=_FakeResponse(payload=payload)):
            entries, next_cursor = _fetch_cls_page("比亚迪", None, timeout=5)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].title, "比亚迪固态电池提速")
        self.assertIn("多家车企加码布局", entries[0].summary)
        self.assertEqual(entries[0].url, "https://www.cls.cn/detail/909")
        self.assertIsNotNone(entries[0].published_at)
        # 不足一页 -> 没有下一页
        self.assertIsNone(next_cursor)

    def test_cls_merges_telegram_and_depth_sections(self) -> None:
        telegram = {"errno": 0, "data": {"telegram": {"data": [
            {"descr": "【比亚迪快讯】财联社10月13日电，短讯。", "id": 1, "time": 1791957281},
        ]}}}
        depth = {"errno": 0, "data": {"depth": {"data": [
            {"title": "比亚迪深度", "descr": "财联社10月14日讯，长文。", "id": 2, "time": 1791963519},
        ]}}}
        asked: list = []

        def fake_post(_url, **kwargs):
            section = kwargs["json"]["type"]
            asked.append(section)
            return _FakeResponse(payload=telegram if section == "telegram" else depth)

        with patch.object(news_search_service, "_http_post", side_effect=fake_post):
            entries, next_cursor = _fetch_cls_page("比亚迪", None, timeout=5)

        # 两个 section 各打一次，合并后按时间倒序（深度那条更新）
        self.assertEqual(asked, ["telegram", "depth"])
        self.assertEqual([entry.title for entry in entries], ["比亚迪深度", "比亚迪快讯"])
        self.assertIsNone(next_cursor)

    def test_cls_continues_paging_while_either_section_is_full(self) -> None:
        full_telegram = {"errno": 0, "data": {"telegram": {"data": [
            {"descr": f"【比亚迪{i}】财联社10月13日电。", "id": i, "time": 1791957281}
            for i in range(news_search_service._PAGE_SIZE)
        ]}}}
        empty_depth = {"errno": 0, "data": {"depth": {"data": []}}}

        def fake_post(_url, **kwargs):
            return _FakeResponse(payload=full_telegram if kwargs["json"]["type"] == "telegram" else empty_depth)

        with patch.object(news_search_service, "_http_post", side_effect=fake_post):
            _, next_cursor = _fetch_cls_page("比亚迪", None, timeout=5)

        self.assertEqual(next_cursor, 1)

    def test_eastmoney_unwraps_jsonp_and_parses_beijing_time(self) -> None:
        body = ('cb({"code":0,"result":{"cmsArticleWebOld":['
                '{"date":"2026-10-14 16:31:39","title":"<em>比亚迪</em>、吉利集体声明",'
                '"content":"来源：比亚迪汽车","mediaName":"证券时报",'
                '"url":"http://finance.eastmoney.com/a/1.html"}]}})')
        with patch.object(news_search_service, "_http_get", return_value=_FakeResponse(text=body)):
            entries, _ = _fetch_eastmoney_page("比亚迪", None, timeout=5)

        self.assertEqual(entries[0].title, "比亚迪、吉利集体声明")  # <em> 已剥掉
        self.assertEqual(entries[0].source, "证券时报")
        self.assertEqual(entries[0].published_at.hour, 16)

    def test_wallstreetcn_uses_content_text_and_next_cursor(self) -> None:
        payload = {"code": 20000, "data": {"next_cursor": 3, "items": [
            {"title": "比亚迪：期待与国内同行一道", "content_text": "比亚迪发布声明。",
             "display_time": 1791957281, "uri": "https://wallstreetcn.com/livenews/3178426",
             "global_channel_name": "7x24快讯"},
        ]}}
        with patch.object(news_search_service, "_http_get", return_value=_FakeResponse(payload=payload)):
            entries, next_cursor = _fetch_wallstreetcn_page("比亚迪", None, timeout=5)

        self.assertEqual(entries[0].summary, "比亚迪发布声明。")
        self.assertEqual(entries[0].source, "7x24快讯")
        self.assertEqual(next_cursor, 3)

    def test_jin10_splits_bracket_title_and_uses_offset_as_cursor(self) -> None:
        payload = {"status": 200, "data": {"list": [
            {"id": "1", "title": "【比亚迪：共同推动汽车行业健康发展】金十数据10月14日讯，比亚迪发布声明。",
             "offset": 1791956940775, "display_datetime": "2026-10-14 13:49:00"},
        ]}}
        with patch.object(news_search_service, "_http_get", return_value=_FakeResponse(payload=payload)):
            entries, next_cursor = _fetch_jin10_page("比亚迪", None, timeout=5)

        self.assertEqual(entries[0].title, "比亚迪：共同推动汽车行业健康发展")
        self.assertIn("金十数据10月14日讯", entries[0].summary)
        self.assertEqual(next_cursor, 1791956940775)

    def test_ths_maps_summary_source_and_publish_time(self) -> None:
        payload = {"status_code": 0, "data": [
            {"title": "比亚迪大汉正式上市", "summary": "售价23.59万元起",
             "url": "http://news.10jqka.com.cn/1.shtml", "publish_time": 1791957281,
             "publish_date": "2026-10-14 18:00:56",
             "extra": {"publish_source": "同花顺财经", "host_name": "news.10jqka.com.cn"}},
        ]}
        with patch.object(news_search_service, "_http_post", return_value=_FakeResponse(payload=payload)):
            entries, _ = _fetch_ths_page("比亚迪", None, timeout=5)

        self.assertEqual(entries[0].title, "比亚迪大汉正式上市")
        self.assertEqual(entries[0].source, "同花顺财经")

    def test_ths_nonzero_status_is_upstream_error(self) -> None:
        payload = {"status_code": 2, "status_msg": "param error", "data": []}
        with patch.object(news_search_service, "_http_post", return_value=_FakeResponse(payload=payload)):
            with self.assertRaises(NewsSearchUpstreamError):
                _fetch_ths_page("比亚迪", None, timeout=5)


class RelevanceTest(unittest.TestCase):
    def test_keyword_must_appear_in_text(self) -> None:
        hit = _entry("比亚迪大汉上市", summary="售价公布")
        miss = _entry("中概股佳裕达收涨约57%", summary="脑再生科技涨约15%")
        self.assertTrue(_matches_keyword(hit, "比亚迪"))
        self.assertFalse(_matches_keyword(miss, "比亚迪"))

    def test_multi_token_keyword_requires_all_tokens(self) -> None:
        entry = _entry("比亚迪大汉上市", summary="新车发布")
        self.assertTrue(_matches_keyword(entry, "比亚迪 上市"))
        self.assertFalse(_matches_keyword(entry, "比亚迪 光伏"))


class CollectTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = NewsSearchService()
        self.cursors: list = []

    def _fetcher(self, pages):
        """pages: [(entries, next_cursor), ...]；记录每次拿到的 cursor。"""
        remaining = list(pages)

        def fetch(_keyword, cursor, *, timeout):
            self.cursors.append(cursor)
            return remaining.pop(0) if remaining else ([], None)

        return fetch

    def _search(self, pages, *, platform="cls", keyword="比亚迪", days=2, limit=10):
        with patch.dict(news_search_service._PAGE_FETCHERS, {platform: self._fetcher(pages)}):
            return self.service.search(platform=platform, keyword=keyword, days=days, limit=limit)

    def test_drops_items_outside_window_and_sorts_by_time(self) -> None:
        pages = [([
            _entry("比亚迪旧闻", minutes_ago=60 * 24 * 5),
            _entry("比亚迪快讯", minutes_ago=30),
        ], None)]
        result = self._search(pages)

        self.assertEqual([item["title"] for item in result["items"]], ["比亚迪快讯"])
        self.assertEqual(result["window_days"], 2)

    def test_offtopic_rows_are_filtered_out(self) -> None:
        pages = [([
            _entry("比亚迪快讯", minutes_ago=30),
            _entry("中概指数收涨", minutes_ago=20),
        ], None)]
        result = self._search(pages)

        self.assertEqual([item["title"] for item in result["items"]], ["比亚迪快讯"])

    def test_same_title_across_pages_is_deduped(self) -> None:
        pages = [
            ([_entry("比亚迪集体声明", minutes_ago=30, url="a")], "next"),
            ([_entry("比亚迪集体声明！", minutes_ago=20, url="b")], None),
        ]
        result = self._search(pages)

        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["pages_scanned"], 2)

    def test_time_ordered_platform_stops_once_a_page_is_older_than_window(self) -> None:
        pages = [
            ([_entry("比亚迪快讯", minutes_ago=30)], "next"),
            ([_entry("比亚迪旧闻", minutes_ago=60 * 24 * 9)], "next"),
            ([_entry("比亚迪更旧", minutes_ago=60 * 24 * 10)], None),
        ]
        result = self._search(pages)

        # 第二页整页都早于窗口，时间倒序的平台不必再翻第三页
        self.assertEqual(result["pages_scanned"], 2)
        self.assertEqual([item["title"] for item in result["items"]], ["比亚迪快讯"])

    def test_relevance_ordered_platform_keeps_paging_through_old_pages(self) -> None:
        pages = [
            ([_entry("比亚迪旧闻", minutes_ago=60 * 24 * 9, platform="ths", label="同花顺-问财")], "next"),
            ([_entry("比亚迪快讯", minutes_ago=30, platform="ths", label="同花顺-问财")], None),
        ]
        result = self._search(pages, platform="ths")

        # 相关度排序的平台不能靠「本页都早于窗口」提前停，否则会漏掉后面的新条目
        self.assertEqual(result["pages_scanned"], 2)
        self.assertEqual([item["title"] for item in result["items"]], ["比亚迪快讯"])

    def test_stops_paging_once_limit_is_reached(self) -> None:
        pages = [
            ([_entry(f"比亚迪{i}", minutes_ago=i) for i in range(1, 6)], "next"),
            ([_entry("比亚迪后面的", minutes_ago=99)], None),
        ]
        result = self._search(pages, limit=3)

        self.assertEqual(result["pages_scanned"], 1)
        self.assertEqual(len(result["items"]), 3)


class AggregateSearchTest(unittest.TestCase):
    """``search_all``：并发打全部平台后合并，单平台失败不影响整体。"""

    def setUp(self) -> None:
        self.service = NewsSearchService()

    def _fetchers(self, per_platform):
        """per_platform: {platform_key: entries} 或 {platform_key: Exception}。

        未列出的平台一律补成「返回空页」的假 fetcher——聚合会把 5 家全打一遍，
        漏掉任何一个都会落到真 fetcher 上打网络。
        """

        def make(value):
            def fetch(_keyword, _cursor, *, timeout):
                if isinstance(value, Exception):
                    raise value
                return value, None

            return fetch

        return {
            platform.key: make(per_platform.get(platform.key, []))
            for platform in news_search_service.PLATFORMS
        }

    def _search_all(self, per_platform, **kwargs):
        with patch.dict(news_search_service._PAGE_FETCHERS, self._fetchers(per_platform)):
            return self.service.search_all(keyword=kwargs.pop("keyword", "比亚迪"), **kwargs)

    def test_merges_all_platforms_and_sorts_by_time(self) -> None:
        result = self._search_all({
            "cls": [_entry("比亚迪快讯", minutes_ago=30)],
            "eastmoney": [_entry("比亚迪获机构调研", minutes_ago=10,
                                 platform="eastmoney", label="东方财富")],
            "ths": [],
            "wallstreetcn": [],
            "jin10": [],
        })

        self.assertEqual([item["title"] for item in result["items"]],
                         ["比亚迪获机构调研", "比亚迪快讯"])
        self.assertEqual(result["platform"], "all")
        self.assertEqual(result["platform_label"], "全部平台")
        self.assertEqual(result["failed_platforms"], [])

    def test_same_title_across_platforms_is_deduped(self) -> None:
        result = self._search_all({
            "cls": [_entry("比亚迪集体声明", minutes_ago=30, url="a")],
            "eastmoney": [_entry("比亚迪集体声明！", minutes_ago=20, url="b",
                                 platform="eastmoney", label="东方财富")],
        })

        # 先按时间倒序再按标题去重，所以留下的是较新的那条（东财的 b）
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["url"], "b")
        self.assertEqual(result["total"], 1)

    def test_limit_truncates_the_merged_result(self) -> None:
        result = self._search_all({
            "cls": [_entry(f"比亚迪{i}", minutes_ago=i) for i in range(1, 5)],
            "eastmoney": [_entry("比亚迪机构调研", minutes_ago=9,
                                 platform="eastmoney", label="东方财富")],
        }, limit=2)

        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(result["total"], 2)

    def test_pages_scanned_is_summed_across_platforms(self) -> None:
        result = self._search_all({
            "cls": [_entry("比亚迪快讯", minutes_ago=30)],
            "ths": [_entry("比亚迪公告", minutes_ago=20, platform="ths", label="同花顺-问财")],
        })

        # 两个平台各翻一页，其余平台返回空列表也算各翻一页
        self.assertEqual(result["pages_scanned"], 5)

    def test_single_platform_failure_is_isolated(self) -> None:
        result = self._search_all({
            "cls": [_entry("比亚迪快讯", minutes_ago=30)],
            "jin10": NewsSearchUpstreamError("金十数据搜索失败: timeout"),
        })

        self.assertEqual(result["failed_platforms"], ["jin10"])
        self.assertEqual([item["title"] for item in result["items"]], ["比亚迪快讯"])

    def test_all_platforms_failing_still_returns_a_fail_open_response(self) -> None:
        result = self._search_all({
            key: NewsSearchUpstreamError("upstream boom") for key in
            ("cls", "ths", "eastmoney", "wallstreetcn", "jin10")
        })

        self.assertEqual(result["items"], [])
        self.assertEqual(sorted(result["failed_platforms"]),
                         ["cls", "eastmoney", "jin10", "ths", "wallstreetcn"])

    def test_budget_sec_bounds_the_wait_and_marks_slow_platforms_failed(self) -> None:
        """预算耗尽时不等掉队线程：慢平台记失败，已拿到的结果照常返回。"""
        def slow(_keyword, _cursor, *, timeout):
            time.sleep(0.5)
            return [_entry("比亚迪慢速快讯", minutes_ago=15)], None

        fetchers = self._fetchers({"cls": slow})
        started = time.monotonic()
        with patch.dict(news_search_service._PAGE_FETCHERS, fetchers):
            result = self.service.search_all(keyword="比亚迪", budget_sec=0.05)
        elapsed = time.monotonic() - started

        self.assertIn("cls", result["failed_platforms"])
        # 其余平台是瞬时返回的假 fetcher，不该被慢平台拖住
        self.assertLess(elapsed, 1.0)

    def test_empty_keyword_is_rejected(self) -> None:
        with self.assertRaises(NewsSearchServiceError):
            self.service.search_all(keyword="   ")

    def test_limit_is_clamped_to_max(self) -> None:
        with patch.object(NewsSearchService, "_collect", return_value=([], 1)) as collect:
            self.service.search_all(keyword="比亚迪", limit=9999)
        self.assertEqual(collect.call_args.kwargs["limit"], news_search_service._MAX_LIMIT)


class ServiceValidationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = NewsSearchService()

    def test_unknown_platform_is_rejected(self) -> None:
        with self.assertRaises(NewsSearchServiceError):
            self.service.search(platform="not-a-platform", keyword="比亚迪")

    def test_empty_keyword_is_rejected(self) -> None:
        with self.assertRaises(NewsSearchServiceError):
            self.service.search(platform="cls", keyword="   ")

    def test_limit_is_clamped_to_max(self) -> None:
        with patch.object(NewsSearchService, "_collect", return_value=([], 1)) as collect:
            self.service.search(platform="cls", keyword="比亚迪", limit=9999)
        self.assertEqual(collect.call_args.kwargs["limit"], news_search_service._MAX_LIMIT)

    def test_list_platforms_returns_registry_order(self) -> None:
        result = self.service.list_platforms()

        self.assertEqual(result["default_platform"], "cls")
        self.assertEqual([item["label"] for item in result["items"]],
                         ["财联社", "同花顺-问财", "东方财富", "华尔街见闻", "金十数据"])


class EndpointMappingTest(unittest.TestCase):
    """endpoint 层只做参数校验与异常映射，直接调函数即可覆盖。"""

    def test_service_error_maps_to_400(self) -> None:
        with patch.object(news_search_service.NewsSearchService, "search",
                          side_effect=NewsSearchServiceError("unknown platform: x")):
            with self.assertRaises(HTTPException) as ctx:
                news_search_endpoint.search_items(q="比亚迪", platform="x", days=2, limit=50)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_upstream_error_maps_to_503(self) -> None:
        with patch.object(news_search_service.NewsSearchService, "search",
                          side_effect=NewsSearchUpstreamError("金十数据搜索失败: timeout")):
            with self.assertRaises(HTTPException) as ctx:
                news_search_endpoint.search_items(q="比亚迪", platform="jin10", days=2, limit=50)
        self.assertEqual(ctx.exception.status_code, 503)

    def test_platforms_endpoint_shape(self) -> None:
        response = news_search_endpoint.list_platforms()
        self.assertEqual(response.total, 5)
        self.assertEqual(response.default_platform, "cls")

    def test_aggregate_service_error_maps_to_400(self) -> None:
        with patch.object(news_search_service.NewsSearchService, "search_all",
                          side_effect=NewsSearchServiceError("搜索关键词不能为空")):
            with self.assertRaises(HTTPException) as ctx:
                news_search_endpoint.search_all_items(q="x", days=7, limit=50)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_aggregate_surfaces_failed_platforms_instead_of_503(self) -> None:
        payload = {
            "platform": "all",
            "platform_label": "全部平台",
            "query": "比亚迪",
            "window_days": 7,
            "items": [],
            "total": 0,
            "pages_scanned": 5,
            "failed_platforms": ["jin10"],
            "fetched_at": "2026-10-18T15:19:00+08:00",
        }
        with patch.object(news_search_service.NewsSearchService, "search_all", return_value=payload):
            response = news_search_endpoint.search_all_items(q="比亚迪", days=7, limit=50)

        self.assertEqual(response.failed_platforms, ["jin10"])
        self.assertEqual(response.platform, "all")
        self.assertEqual(response.window_days, 7)



if __name__ == "__main__":
    unittest.main()
