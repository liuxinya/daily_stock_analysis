# -*- coding: utf-8 -*-
"""多平台实时快讯聚合服务的回归测试。

适配器层用固定 records / JSON 做确定性断言，不打网络；雪球的游标翻页与匿名
token 续期在 HTTP 边界打桩、保留真实分页逻辑；服务层通过 patch ``_fetch``
覆盖缓存、分页、聚合排序去重与单源失败降级。
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import requests

from src.services import realtime_news_service
from src.services.realtime_news_service import (
    PLATFORM_BY_KEY,
    PROVIDER_UNAVAILABLE,
    RealtimeNewsEntry,
    RealtimeNewsPlatform,
    RealtimeNewsService,
    RealtimeNewsServiceError,
    RealtimeNewsUpstreamError,
    SHANGHAI_TZ,
    TAG_DEPTH,
    normalize_akshare_records,
    normalize_cls_payload,
    normalize_cls_search,
    normalize_em_search,
    normalize_newsnow_payload,
    normalize_xueqiu_payload,
    normalize_yicai_payload,
)


def _config(**overrides):
    base = {
        "realtime_news_cache_ttl_sec": 60.0,
        "realtime_news_fetch_timeout_sec": 8.0,
        "realtime_news_max_items_per_platform": 50,
        "newsnow_base_url": "https://newsnow.example.com",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _entry(platform: str, title: str, minute: int, url: str = "") -> RealtimeNewsEntry:
    return RealtimeNewsEntry(
        platform=platform,
        platform_label=platform,
        title=title,
        summary="",
        url=url,
        published_at=datetime(2026, 9, 7, 19, minute, tzinfo=SHANGHAI_TZ),
    )


class AkshareNormalizeTest(unittest.TestCase):
    """akshare 各接口列名不统一，且部分接口缺标题或缺时间。"""

    def test_ths_maps_columns_and_attaches_shanghai_timezone(self) -> None:
        records = [{
            "标题": "科源制药：罗替高汀原料药获批",
            "内容": "科源制药公告，公司近日收到批准通知书。",
            "发布时间": "2026-09-07 19:57:28",
            "链接": "https://news.10jqka.com.cn/1.shtml",
        }]
        entries = normalize_akshare_records(records, PLATFORM_BY_KEY["ths"], limit=10)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry.title, "科源制药：罗替高汀原料药获批")
        self.assertEqual(entry.url, "https://news.10jqka.com.cn/1.shtml")
        # 上游给的是北京本地时间，必须补上时区，否则与 NewsNow 的 UTC 混排会差 8 小时
        self.assertEqual(entry.published_at, datetime(2026, 9, 7, 19, 57, 28, tzinfo=SHANGHAI_TZ))
        self.assertEqual(entry.published_at.utcoffset().total_seconds(), 8 * 3600)

    def test_sina_derives_title_from_bracket_prefix(self) -> None:
        """新浪只返回「时间 + 内容」，标题要从内容开头的【】里抽。"""
        records = [{"时间": "2026-09-07 19:04:34", "内容": "【利和兴：实控人拟减持不超2%】利和兴公告称，控股股东计划减持。"}]
        entries = normalize_akshare_records(records, PLATFORM_BY_KEY["sina"], limit=10)
        self.assertEqual(entries[0].title, "利和兴：实控人拟减持不超2%")
        self.assertEqual(entries[0].summary, "利和兴公告称，控股股东计划减持。")
        self.assertEqual(entries[0].url, "")

    def test_caixin_without_title_or_time_falls_back(self) -> None:
        """财新只返回 tag/summary/url，没有标题也没有发布时间。"""
        summary = "日本可能出售了包括美债在内的部分外国证券持仓，为之前一个月里规模空前的汇市干预筹措资金"
        entries = normalize_akshare_records(
            [{"tag": "市场动态", "summary": summary, "url": "https://database.caixin.com/1.html"}],
            PLATFORM_BY_KEY["caixin"],
            limit=10,
        )
        entry = entries[0]
        self.assertTrue(entry.title.endswith("…"))
        self.assertEqual(entry.tag, "市场动态")
        self.assertIsNone(entry.published_at)
        self.assertFalse(PLATFORM_BY_KEY["caixin"].has_published_at)

    def test_limit_is_applied_and_untitled_rows_are_dropped(self) -> None:
        records = [{"标题": f"第{index}条", "摘要": "", "发布时间": "", "链接": ""} for index in range(5)]
        records.append({"标题": "", "摘要": "", "发布时间": "", "链接": ""})
        entries = normalize_akshare_records(records, PLATFORM_BY_KEY["eastmoney"], limit=3)
        self.assertEqual([item.title for item in entries], ["第0条", "第1条", "第2条"])

    def test_unsupported_upstream_is_rejected(self) -> None:
        broken = PLATFORM_BY_KEY["ths"].__class__("x", "X", "akshare", "not_a_real_interface")
        with self.assertRaises(RealtimeNewsServiceError):
            normalize_akshare_records([{"标题": "a"}], broken, limit=1)


class NewsnowNormalizeTest(unittest.TestCase):
    def test_epoch_millis_becomes_timezone_aware_instant(self) -> None:
        payload = {"items": [{
            "title": "国际油价短线拉升",
            "url": "https://wallstreetcn.com/livenews/1",
            "pubDate": 1757243805000,
            "extra": {"info": "沙特阿美炼油厂遭袭"},
        }]}
        entries = normalize_newsnow_payload(payload, PLATFORM_BY_KEY["wallstreetcn"], limit=10)
        published = entries[0].published_at
        # 断言的是"同一时刻"，不是某个具体偏移：偏移取决于运行环境时区，
        # 而 aware datetime 的相等比较本身就是按时刻算的。
        self.assertIsNotNone(published.tzinfo)
        self.assertIsNotNone(published.utcoffset())
        self.assertEqual(published, datetime.fromtimestamp(1757243805, tz=timezone.utc))
        self.assertEqual(entries[0].summary, "沙特阿美炼油厂遭袭")

    def test_items_without_title_are_skipped(self) -> None:
        payload = {"items": [{"title": "", "url": "u"}, {"title": "有标题", "url": "v"}]}
        entries = normalize_newsnow_payload(payload, PLATFORM_BY_KEY["jin10"], limit=10)
        self.assertEqual([item.title for item in entries], ["有标题"])

    def test_malformed_payload_is_upstream_error(self) -> None:
        for payload in ([], {"data": []}):
            with self.assertRaises(RealtimeNewsUpstreamError):
                normalize_newsnow_payload(payload, PLATFORM_BY_KEY["jin10"], limit=10)


class XueqiuNormalizeTest(unittest.TestCase):
    """雪球 7×24 只给一个 text 字段，标题要自己拆；约半数条目没有 `【】`。"""

    PLATFORM = PLATFORM_BY_KEY["xueqiu"]

    def test_bracket_title_is_split_and_epoch_millis_becomes_beijing_time(self) -> None:
        payload = {"next_max_id": 4882101, "items": [{
            "id": 4882165,
            "text": "【沪深两市成交额突破1万亿】据财联社盯盘数据显示，较上一日此时缩量超1000亿。",
            "mark": 1,
            "target": "http://xueqiu.com/5124430882/408747036",
            "created_at": 1789009420000,
            "status_id": 408747036,
        }]}
        entries = normalize_xueqiu_payload(payload, self.PLATFORM, limit=10)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry.title, "沪深两市成交额突破1万亿")
        self.assertEqual(entry.summary, "据财联社盯盘数据显示，较上一日此时缩量超1000亿。")
        # 上游固定给 http，必须升到 https，否则前端外链走明文
        self.assertEqual(entry.url, "https://xueqiu.com/5124430882/408747036")
        # created_at 是 epoch 毫秒（UTC 基准），断言的是同一时刻
        self.assertEqual(entry.published_at, datetime.fromtimestamp(1789009420, tz=timezone.utc))
        self.assertEqual(entry.published_at.utcoffset().total_seconds(), 8 * 3600)
        # mark=1 是上游标红的重要快讯
        self.assertEqual(entry.tag, "重要")

    def test_text_without_bracket_falls_back_to_prefix_title(self) -> None:
        text = "《科创板日报》10日讯，Sensor Tower 平台显示，2026年8月共38个中国厂商入围全球手游发行商收入榜Top 100。"
        entries = normalize_xueqiu_payload(
            {"items": [{"text": text, "mark": 0, "target": "http://xueqiu.com/1/2", "created_at": 1789009420000}]},
            self.PLATFORM,
            limit=10,
        )
        self.assertTrue(entries[0].title.endswith("…"))
        self.assertEqual(entries[0].summary, text)
        self.assertEqual(entries[0].tag, "")

    def test_unsafe_or_missing_target_yields_empty_url(self) -> None:
        """target 会被前端直接渲染成 <a href>，非 https/站内路径必须丢掉。"""
        payload = {"items": [
            {"text": "【一】正文", "target": "javascript:alert(1)", "created_at": 1789009420000},
            {"text": "【二】正文", "target": "", "created_at": 1789009420000},
            {"text": "【三】正文", "target": "/5124430882/408747036", "created_at": 1789009420000},
        ]}
        entries = normalize_xueqiu_payload(payload, self.PLATFORM, limit=10)
        self.assertEqual(
            [item.url for item in entries],
            ["", "", "https://xueqiu.com/5124430882/408747036"],
        )

    def test_items_without_text_are_skipped(self) -> None:
        payload = {"items": [{"text": ""}, "not-a-dict", {"text": "【有正文】内容", "created_at": 1789009420000}]}
        entries = normalize_xueqiu_payload(payload, self.PLATFORM, limit=10)
        self.assertEqual([item.title for item in entries], ["有正文"])

    def test_limit_slices_raw_items(self) -> None:
        """与 akshare/NewsNow 适配器一致：limit 截的是上游条目，翻页累计在 _fetch 层做。"""
        payload = {"items": [
            {"text": f"【第{index}条】正文", "created_at": 1789009420000} for index in range(5)
        ]}
        entries = normalize_xueqiu_payload(payload, self.PLATFORM, limit=3)
        self.assertEqual([item.title for item in entries], ["第0条", "第1条", "第2条"])
        self.assertEqual(normalize_xueqiu_payload(payload, self.PLATFORM, limit=0), [])

    def test_bad_created_at_leaves_published_at_none(self) -> None:
        payload = {"items": [{"text": "【无时间】正文", "created_at": None}]}
        self.assertIsNone(normalize_xueqiu_payload(payload, self.PLATFORM, limit=10)[0].published_at)

    def test_malformed_payload_is_upstream_error(self) -> None:
        for payload in ([], {"data": []}, None):
            with self.assertRaises(RealtimeNewsUpstreamError):
                normalize_xueqiu_payload(payload, self.PLATFORM, limit=10)


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload=None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is _NO_JSON:
            raise ValueError("not json")
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


_NO_JSON = object()


class _FakeSession:
    """只实现 _fetch_xueqiu_page / _xueqiu_session_with_token 用到的那几个成员。"""

    def __init__(self, responses, cookies=None) -> None:
        self.headers: dict = {}
        self.cookies: dict = dict(cookies or {})
        self._responses = list(responses)
        self.calls: list = []
        self.closed = False

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs.get("params")))
        return self._responses.pop(0)

    def close(self) -> None:
        self.closed = True


def _xueqiu_page(start_id: int, count: int = 10, next_max_id=None) -> dict:
    items = [{
        "id": start_id - index,
        "text": f"【第{start_id - index}条】正文",
        "mark": 0,
        "target": f"http://xueqiu.com/1/{start_id - index}",
        "created_at": 1789009420000 - index * 1000,
    } for index in range(count)]
    payload: dict = {"items": items}
    if next_max_id is not None:
        payload["next_max_id"] = next_max_id
    return payload


class XueqiuFetchTest(unittest.TestCase):
    """上游每页硬性只给 10 条，数量靠 max_id 游标翻页累计，这一层是主要风险点。"""

    PLATFORM = PLATFORM_BY_KEY["xueqiu"]

    def setUp(self) -> None:
        realtime_news_service.reset_xueqiu_session()
        self.addCleanup(realtime_news_service.reset_xueqiu_session)

    def _fetch(self, pages, *, limit: int, timeout: float = 8.0):
        seen_cursors: list = []

        def _page(*, max_id: str, timeout: float):
            seen_cursors.append(max_id)
            value = pages[len(seen_cursors) - 1]
            if isinstance(value, Exception):
                raise value
            return value

        with patch.object(realtime_news_service, "_fetch_xueqiu_page", _page):
            entries = realtime_news_service._fetch_xueqiu(self.PLATFORM, limit=limit, timeout=timeout)
        return entries, seen_cursors

    def test_pages_until_limit_and_forwards_the_cursor(self) -> None:
        pages = [
            _xueqiu_page(100, next_max_id=91),
            _xueqiu_page(90, next_max_id=81),
            _xueqiu_page(80, next_max_id=71),
        ]
        entries, cursors = self._fetch(pages, limit=25)
        self.assertEqual(len(entries), 25)
        # 首页不带游标，之后每页都用上一页响应里的 next_max_id
        self.assertEqual(cursors, ["", "91", "81"])
        self.assertEqual(entries[0].title, "第100条")

    def test_stops_when_upstream_has_no_next_cursor(self) -> None:
        entries, cursors = self._fetch([_xueqiu_page(100, count=4)], limit=50)
        self.assertEqual(len(entries), 4)
        self.assertEqual(cursors, [""])

    def test_stops_when_cursor_does_not_advance(self) -> None:
        """上游若忽略 max_id 一直回同一页，必须停下而不是打满 MAX_PAGES。"""
        entries, cursors = self._fetch([_xueqiu_page(100, next_max_id=91)] * 5, limit=50)
        self.assertEqual(cursors, ["", "91"])
        # 第二页与首页完全重复，按 url 去重后没有新增，循环结束
        self.assertEqual(len(entries), 10)

    def test_first_page_failure_marks_the_platform_unavailable(self) -> None:
        with self.assertRaises(RealtimeNewsUpstreamError):
            self._fetch([RealtimeNewsUpstreamError("Xueqiu request failed")], limit=50)

    def test_later_page_failure_keeps_what_was_already_fetched(self) -> None:
        pages = [_xueqiu_page(100, next_max_id=91), RealtimeNewsUpstreamError("boom")]
        entries, cursors = self._fetch(pages, limit=50)
        self.assertEqual(len(entries), 10)
        self.assertEqual(cursors, ["", "91"])

    def test_exhausted_budget_stops_paging_instead_of_failing(self) -> None:
        entries, cursors = self._fetch([_xueqiu_page(100, next_max_id=91)] * 3, limit=50, timeout=0.0)
        self.assertEqual(entries, [])
        self.assertEqual(cursors, [])

    def test_token_rejection_rebuilds_the_session_and_retries_once(self) -> None:
        payload = _xueqiu_page(100, count=1)
        sessions = [
            _FakeSession([_FakeResponse(400)]),
            _FakeSession([_FakeResponse(200, payload)]),
        ]
        refreshes: list = []

        def _session(*, timeout: float, force_refresh: bool = False):
            refreshes.append(force_refresh)
            return sessions[len(refreshes) - 1]

        with patch.object(realtime_news_service, "_xueqiu_session_with_token", _session):
            result = realtime_news_service._fetch_xueqiu_page(max_id="", timeout=5.0)
        self.assertEqual(result, payload)
        # 上游用 400 表达 token 失效，所以第二次必须强制重建会话
        self.assertEqual(refreshes, [False, True])

    def test_token_rejected_twice_is_upstream_error(self) -> None:
        sessions = [_FakeSession([_FakeResponse(400)]), _FakeSession([_FakeResponse(400)])]

        def _session(*, timeout: float, force_refresh: bool = False):
            return sessions.pop(0)

        with patch.object(realtime_news_service, "_xueqiu_session_with_token", _session):
            with self.assertRaises(RealtimeNewsUpstreamError):
                realtime_news_service._fetch_xueqiu_page(max_id="", timeout=5.0)

    def test_invalid_json_is_upstream_error(self) -> None:
        session = _FakeSession([_FakeResponse(200, _NO_JSON)])
        with patch.object(realtime_news_service, "_xueqiu_session_with_token", lambda **_: session):
            with self.assertRaises(RealtimeNewsUpstreamError):
                realtime_news_service._fetch_xueqiu_page(max_id="", timeout=5.0)

    def test_bootstrap_without_token_cookie_is_upstream_error(self) -> None:
        """站点首页只下发 WAF cookie；拿不到 token 就必须报上游不可用，而不是静默空列表。"""
        session = _FakeSession([_FakeResponse(200)], cookies={"acw_tc": "x"})
        with patch.object(realtime_news_service.requests, "Session", lambda: session):
            with self.assertRaises(RealtimeNewsUpstreamError):
                realtime_news_service._xueqiu_session_with_token(timeout=5.0)
        self.assertTrue(session.closed)

    def test_session_with_token_is_reused_across_pages(self) -> None:
        session = _FakeSession(
            [_FakeResponse(200), _FakeResponse(200, _xueqiu_page(100, count=1))],
            cookies={"xq_a_token": "anonymous"},
        )
        with patch.object(realtime_news_service.requests, "Session", lambda: session):
            first = realtime_news_service._xueqiu_session_with_token(timeout=5.0)
            second = realtime_news_service._xueqiu_session_with_token(timeout=5.0)
        self.assertIs(first, second)
        # 只 bootstrap 了一次
        self.assertEqual(len(session.calls), 1)


class YicaiNormalizeTest(unittest.TestCase):
    """第一财经标题与正文是分开的字段，时间是北京本地 naive ISO。"""

    PLATFORM = PLATFORM_BY_KEY["yicai"]

    def test_maps_fields_and_attaches_shanghai_timezone(self) -> None:
        payload = [{
            "LiveID": 103359393,
            "LiveTitle": "李超：优化发行上市制度",
            "LiveContent": "9月10日下午，证监会副主席李超在国新办新闻发布会上表示。",
            "CreateDate": "2026-09-10T15:23:22",
            "IsImportant": True,
            "LiveType": 0,
            "url": "/brief/103359393.html",
            "ShareUrl": "https://m.yicai.com/brief/103359393.html",
        }]
        entries = normalize_yicai_payload(payload, self.PLATFORM, limit=10)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry.title, "李超：优化发行上市制度")
        self.assertEqual(entry.summary, "9月10日下午，证监会副主席李超在国新办新闻发布会上表示。")
        # 上游给站内相对路径，要补成绝对地址前端才能外链
        self.assertEqual(entry.url, "https://www.yicai.com/brief/103359393.html")
        # CreateDate 是北京本地 naive，必须补时区，否则与雪球的 UTC 基准混排差 8 小时
        self.assertEqual(entry.published_at, datetime(2026, 9, 10, 15, 23, 22, tzinfo=SHANGHAI_TZ))
        self.assertEqual(entry.published_at.utcoffset().total_seconds(), 8 * 3600)
        self.assertEqual(entry.tag, "重要")

    def test_vip_brief_without_content_keeps_the_title(self) -> None:
        """LiveType=1 是 VIP 稿件，正文为空但标题有效，不该被丢掉。"""
        payload = [{
            "LiveTitle": "【盘中特供】多家光通信龙头企业亮相光博会",
            "LiveContent": "",
            "CreateDate": "2026-09-10T14:30:11",
            "LiveType": 1,
            "IsImportant": True,
            "url": "/vip/brief/103359278.html",
        }]
        entries = normalize_yicai_payload(payload, self.PLATFORM, limit=10)
        self.assertEqual(entries[0].title, "【盘中特供】多家光通信龙头企业亮相光博会")
        self.assertEqual(entries[0].summary, "")
        self.assertEqual(entries[0].url, "https://www.yicai.com/vip/brief/103359278.html")

    def test_pagesize_rejection_is_upstream_error_not_empty_page(self) -> None:
        """pagesize 越界时上游回 HTTP 200 + 逐字符拆开的错误串，不能当成空数据静默缓存。"""
        payload = list("pagesize not invalidate")
        with self.assertRaises(RealtimeNewsUpstreamError):
            normalize_yicai_payload(payload, self.PLATFORM, limit=10)

    def test_missing_title_falls_back_to_content(self) -> None:
        payload = [
            {"LiveTitle": "", "LiveContent": "【沪指收涨】两市成交额破万亿。", "CreateDate": "2026-09-10T15:00:00"},
            {"LiveTitle": "", "NewsTitle": "备用标题", "LiveContent": "正文", "CreateDate": "2026-09-10T15:00:00"},
        ]
        entries = normalize_yicai_payload(payload, self.PLATFORM, limit=10)
        self.assertEqual([item.title for item in entries], ["沪指收涨", "备用标题"])
        self.assertEqual(entries[0].summary, "两市成交额破万亿。")

    def test_unsafe_or_missing_url_yields_empty_url(self) -> None:
        payload = [
            {"LiveTitle": "一", "url": "javascript:alert(1)", "ShareUrl": ""},
            {"LiveTitle": "二", "url": "", "ShareUrl": "http://m.yicai.com/brief/1.html"},
        ]
        entries = normalize_yicai_payload(payload, self.PLATFORM, limit=10)
        self.assertEqual([item.url for item in entries], ["", "https://m.yicai.com/brief/1.html"])

    def test_limit_slices_raw_items_and_skips_untitled(self) -> None:
        payload = [{"LiveTitle": f"第{index}条"} for index in range(5)]
        payload.append({"LiveTitle": "", "LiveContent": ""})
        entries = normalize_yicai_payload(payload, self.PLATFORM, limit=3)
        self.assertEqual([item.title for item in entries], ["第0条", "第1条", "第2条"])
        self.assertEqual(normalize_yicai_payload(payload, self.PLATFORM, limit=0), [])
        self.assertEqual(len(normalize_yicai_payload(payload, self.PLATFORM, limit=10)), 5)

    def test_malformed_payload_is_upstream_error(self) -> None:
        for payload in ({"items": []}, None, "text"):
            with self.assertRaises(RealtimeNewsUpstreamError):
                normalize_yicai_payload(payload, self.PLATFORM, limit=10)


def _yicai_page(start_id: int, count: int = 20) -> list:
    return [{
        "LiveID": start_id - index,
        "LiveTitle": f"第{start_id - index}条",
        "LiveContent": "正文",
        "CreateDate": "2026-09-10T15:00:00",
        "IsImportant": False,
        "url": f"/brief/{start_id - index}.html",
    } for index in range(count)]


class YicaiFetchTest(unittest.TestCase):
    """游标是「上一页最后一条的 id」，且 pagesize 有 50 的硬上限。"""

    PLATFORM = PLATFORM_BY_KEY["yicai"]

    def _fetch(self, pages, *, limit: int, timeout: float = 8.0):
        calls: list = []

        def _page(*, nid: str, page: int, page_size: int, timeout: float):
            calls.append({"nid": nid, "page": page, "page_size": page_size})
            value = pages[len(calls) - 1]
            if isinstance(value, Exception):
                raise value
            return value

        with patch.object(realtime_news_service, "_fetch_yicai_page", _page):
            entries = realtime_news_service._fetch_yicai(self.PLATFORM, limit=limit, timeout=timeout)
        return entries, calls

    def test_default_limit_needs_a_single_request(self) -> None:
        """pagesize 上限刚好等于默认 limit，常态下不该翻页。"""
        entries, calls = self._fetch([_yicai_page(500, count=50)], limit=50)
        self.assertEqual(len(entries), 50)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], {"nid": "", "page": 1, "page_size": 50})

    def test_cursor_is_the_last_item_id_and_page_increments(self) -> None:
        entries, calls = self._fetch([_yicai_page(500, count=50), _yicai_page(450, count=50)], limit=60)
        self.assertEqual(len(entries), 60)
        # 首页不带 nid；第二页的 nid 是首页最后一条的 LiveID（500 - 49）
        self.assertEqual([call["nid"] for call in calls], ["", "451"])
        self.assertEqual([call["page"] for call in calls], [1, 2])
        self.assertEqual(entries[0].title, "第500条")

    def test_page_size_is_clamped_to_the_upstream_cap(self) -> None:
        """limit 超过 50 时不能直接把 pagesize 传上去，否则上游回错误串。"""
        _, calls = self._fetch([_yicai_page(500, count=50), _yicai_page(450, count=50)], limit=80)
        self.assertEqual({call["page_size"] for call in calls}, {50})

    def test_short_page_ends_pagination(self) -> None:
        entries, calls = self._fetch([_yicai_page(500, count=3)], limit=50)
        self.assertEqual(len(entries), 3)
        self.assertEqual(len(calls), 1)

    def test_first_page_failure_marks_the_platform_unavailable(self) -> None:
        with self.assertRaises(RealtimeNewsUpstreamError):
            self._fetch([RealtimeNewsUpstreamError("Yicai request failed")], limit=50)

    def test_malformed_later_page_keeps_what_was_already_fetched(self) -> None:
        """第 2 页返回越界错误串时，不能把第 1 页的结果一起丢掉。"""
        pages = [_yicai_page(500, count=50), list("pagesize not invalidate")]
        entries, calls = self._fetch(pages, limit=100)
        self.assertEqual(len(entries), 50)
        self.assertEqual(len(calls), 2)


class YicaiRequestTest(unittest.TestCase):
    """校验发出去的请求形状：首页不带 nid，翻页才带 type/nid。"""

    def test_params_and_headers(self) -> None:
        captured: dict = {}

        def _get(url, **kwargs):
            captured.update({"url": url, **kwargs})
            return _FakeResponse(200, _yicai_page(500, count=1))

        with patch.object(realtime_news_service.requests, "get", _get):
            realtime_news_service._fetch_yicai_page(nid="", page=1, page_size=50, timeout=5.0)
        self.assertEqual(captured["url"], "https://www.yicai.com/api/ajax/getbrieflist")
        self.assertEqual(captured["params"], {"page": 1, "pagesize": 50, "id": 0, "action": "mix"})
        self.assertEqual(captured["headers"]["Referer"], "https://www.yicai.com/brief/")

        with patch.object(realtime_news_service.requests, "get", _get):
            realtime_news_service._fetch_yicai_page(nid="481", page=2, page_size=50, timeout=5.0)
        self.assertEqual(
            captured["params"],
            {"page": 2, "pagesize": 50, "id": 0, "action": "mix", "type": 0, "nid": "481"},
        )

    def test_transport_and_json_failures_are_upstream_errors(self) -> None:
        def _raise(url, **kwargs):
            raise requests.ConnectionError("boom")

        with patch.object(realtime_news_service.requests, "get", _raise):
            with self.assertRaises(RealtimeNewsUpstreamError):
                realtime_news_service._fetch_yicai_page(nid="", page=1, page_size=50, timeout=5.0)

        with patch.object(realtime_news_service.requests, "get", lambda url, **kw: _FakeResponse(200, _NO_JSON)):
            with self.assertRaises(RealtimeNewsUpstreamError):
                realtime_news_service._fetch_yicai_page(nid="", page=1, page_size=50, timeout=5.0)

        with patch.object(realtime_news_service.requests, "get", lambda url, **kw: _FakeResponse(503)):
            with self.assertRaises(RealtimeNewsUpstreamError):
                realtime_news_service._fetch_yicai_page(nid="", page=1, page_size=50, timeout=5.0)


class ClsNormalizeTest(unittest.TestCase):
    """财联社电报：独立 title 字段，ctime 是 epoch 秒，level A/B 为站点标红的重要电报。"""

    PLATFORM = PLATFORM_BY_KEY["cls"]

    def test_titled_item_strips_bracket_prefix_and_builds_detail_url(self) -> None:
        payload = {"data": {"roll_data": [{
            "id": 2497850,
            "title": "克普勒：9月末中东原油出口量超过伊朗战事前水平",
            "content": "【克普勒：9月末中东原油出口量超过伊朗战事前水平】财联社10月5日电，数据显示出口回升。",
            "brief": "【克普勒：9月末中东原油出口量超过伊朗战事前水平】财联社10月5日电，数据显示出口回升。",
            "ctime": 1791180720,
            "level": "C",
        }]}}
        entries = normalize_cls_payload(payload, self.PLATFORM, limit=10)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry.title, "克普勒：9月末中东原油出口量超过伊朗战事前水平")
        # 正文开头重复的「【标题】」被剥掉，只留正文做摘要
        self.assertEqual(entry.summary, "财联社10月5日电，数据显示出口回升。")
        self.assertEqual(entry.url, "https://www.cls.cn/detail/2497850")
        # ctime 是 epoch 秒（UTC 基准），断言的是同一时刻并带 +08:00 偏移
        self.assertEqual(entry.published_at, datetime.fromtimestamp(1791180720, tz=timezone.utc))
        self.assertEqual(entry.published_at.utcoffset().total_seconds(), 8 * 3600)
        self.assertEqual(entry.tag, "")

    def test_untitled_item_falls_back_to_brief_prefix(self) -> None:
        payload = {"data": {"roll_data": [{
            "id": 1,
            "title": "",
            "brief": "财联社10月5日电，泰铢兑美元汇率下跌至33.74，创下自7月底以来的最低水平。",
            "content": "财联社10月5日电，泰铢兑美元汇率下跌至33.74，创下自7月底以来的最低水平，交易清淡。",
            "ctime": 1791180000,
            "level": "C",
        }]}}
        entry = normalize_cls_payload(payload, self.PLATFORM, limit=10)[0]
        self.assertTrue(entry.title.endswith("…"))
        # content 比 brief 完整，摘要取 content
        self.assertTrue(entry.summary.endswith("交易清淡。"))

    def test_level_a_or_b_maps_to_important_tag(self) -> None:
        payload = {"data": {"roll_data": [
            {"id": 1, "title": "甲", "content": "正文", "ctime": 1791180000, "level": "A"},
            {"id": 2, "title": "乙", "content": "正文", "ctime": 1791180000, "level": "b"},
            {"id": 3, "title": "丙", "content": "正文", "ctime": 1791180000, "level": "C"},
        ]}}
        entries = normalize_cls_payload(payload, self.PLATFORM, limit=10)
        self.assertEqual([e.tag for e in entries], ["重要", "重要", ""])

    def test_ads_are_dropped(self) -> None:
        payload = {"data": {"roll_data": [
            {"id": 1, "title": "广告", "content": "推广", "ctime": 1791180000, "is_ad": 1},
            {"id": 2, "title": "正常电报", "content": "正文", "ctime": 1791180000},
        ]}}
        entries = normalize_cls_payload(payload, self.PLATFORM, limit=10)
        self.assertEqual([e.title for e in entries], ["正常电报"])

    def test_limit_slices_raw_items(self) -> None:
        payload = {"data": {"roll_data": [
            {"id": index, "title": f"第{index}条", "content": "正文", "ctime": 1791180000}
            for index in range(5)
        ]}}
        entries = normalize_cls_payload(payload, self.PLATFORM, limit=3)
        self.assertEqual([e.title for e in entries], ["第0条", "第1条", "第2条"])
        self.assertEqual(normalize_cls_payload(payload, self.PLATFORM, limit=0), [])

    def test_malformed_payload_is_upstream_error(self) -> None:
        for payload in ([], {"data": []}, {"data": {"roll_data": "x"}}, None):
            with self.assertRaises(RealtimeNewsUpstreamError):
                normalize_cls_payload(payload, self.PLATFORM, limit=10)


class ClsRequestTest(unittest.TestCase):
    """校验发出去的请求形状：固定 app/os/sv + name，sign 由参数算出。"""

    PLATFORM = PLATFORM_BY_KEY["cls"]

    def test_sign_matches_the_live_web_algorithm(self) -> None:
        # 线上实测：telegraph 接口这组参数的 sign 固定为该值，守住签名算法不被改坏
        params = {"app": "CailianpressWeb", "name": "telegraph", "os": "web", "sv": "8.7.9"}
        self.assertEqual(
            realtime_news_service._cls_sign(params), "6c73b056a64891cdc257dcf1914464ad"
        )

    def test_params_headers_and_sign(self) -> None:
        captured: dict = {}

        def _get(url, **kwargs):
            captured.update({"url": url, **kwargs})
            return _FakeResponse(200, {"data": {"roll_data": [
                {"id": 1, "title": "电报", "content": "正文", "ctime": 1791180000}
            ]}})

        with patch.object(realtime_news_service.requests, "get", _get):
            entries = realtime_news_service._fetch_cls(self.PLATFORM, limit=50, timeout=5.0)
        self.assertEqual(captured["url"], "https://www.cls.cn/api/cache")
        params = captured["params"]
        self.assertEqual(params["app"], "CailianpressWeb")
        self.assertEqual(params["os"], "web")
        self.assertEqual(params["name"], "telegraph")
        # sign 必须覆盖包含 name 在内的全部签名参数
        signed = {k: v for k, v in params.items() if k != "sign"}
        self.assertEqual(params["sign"], realtime_news_service._cls_sign(signed))
        self.assertEqual(captured["headers"]["Referer"], "https://www.cls.cn/")
        self.assertEqual([e.title for e in entries], ["电报"])

    def test_transport_and_json_and_http_failures_are_upstream_errors(self) -> None:
        def _raise(url, **kwargs):
            raise requests.ConnectionError("boom")

        with patch.object(realtime_news_service.requests, "get", _raise):
            with self.assertRaises(RealtimeNewsUpstreamError):
                realtime_news_service._fetch_cls(self.PLATFORM, limit=10, timeout=5.0)

        with patch.object(realtime_news_service.requests, "get", lambda url, **kw: _FakeResponse(200, _NO_JSON)):
            with self.assertRaises(RealtimeNewsUpstreamError):
                realtime_news_service._fetch_cls(self.PLATFORM, limit=10, timeout=5.0)

        with patch.object(realtime_news_service.requests, "get", lambda url, **kw: _FakeResponse(503)):
            with self.assertRaises(RealtimeNewsUpstreamError):
                realtime_news_service._fetch_cls(self.PLATFORM, limit=10, timeout=5.0)


class UpstreamFailureReasonTest(unittest.TestCase):
    """失败原因要能区分「上游挂了」和「本机代理把请求劫走了」。

    这两类以前都只报一句 request failed，页面上看不出该查哪边。
    """

    def test_proxy_and_timeout_and_http_status_are_named(self) -> None:
        reason = realtime_news_service._request_failure_reason
        self.assertEqual(
            reason(requests.exceptions.InvalidSchema("Missing dependencies for SOCKS support.")),
            "SOCKS proxy configured but PySocks is not installed",
        )
        self.assertEqual(reason(requests.exceptions.ProxyError("boom")), "proxy error")
        self.assertEqual(reason(requests.exceptions.ReadTimeout("slow")), "timeout")
        self.assertEqual(reason(requests.ConnectionError("down")), "connection error")

    def test_newsnow_error_carries_the_platform_and_the_reason(self) -> None:
        def _raise(url, **kwargs):
            raise requests.exceptions.ProxyError("tunnel refused")

        with patch.object(realtime_news_service.requests, "get", _raise):
            with self.assertRaises(RealtimeNewsUpstreamError) as ctx:
                realtime_news_service._fetch_newsnow(
                    PLATFORM_BY_KEY["jin10"], limit=10, timeout=5.0,
                    base_url="https://newsnow.example.com",
                )
        message = str(ctx.exception)
        self.assertIn("jin10", message)
        self.assertIn("proxy error", message)
        # 不回显上游异常原文：这条信息会经 API 到前端
        self.assertNotIn("tunnel refused", message)


class RealtimeNewsServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        RealtimeNewsService.reset_cache()
        self.addCleanup(RealtimeNewsService.reset_cache)
        self.service = RealtimeNewsService(config=_config())

    def test_unknown_platform_is_rejected(self) -> None:
        with self.assertRaises(RealtimeNewsServiceError):
            self.service.list_latest(platform="not-a-platform")

    def test_platform_without_upstream_returns_empty_instead_of_error(self) -> None:
        """占位平台（已知平台但暂无源）返回 200 + 空列表，不报错也不打上游。

        当前注册表里每个平台都有可用上游，所以这里造一个临时占位项来守住这条分支，
        避免以后再加占位平台时才发现降级路径已经坏了。
        """
        placeholder = RealtimeNewsPlatform("placeholder", "占位平台", PROVIDER_UNAVAILABLE)
        with patch.dict(realtime_news_service.PLATFORM_BY_KEY, {placeholder.key: placeholder}):
            with patch.object(RealtimeNewsService, "_fetch", side_effect=AssertionError("不应打上游")):
                result = self.service.list_latest(platform="placeholder")
        self.assertFalse(result["available"])
        self.assertEqual(result["items"], [])
        self.assertEqual(result["total"], 0)

    def test_every_registered_platform_has_an_upstream(self) -> None:
        """注册表不该留下没接源的 tab：界面新闻已移除，新增占位项要显式确认过。"""
        self.assertEqual([item.key for item in realtime_news_service.PLATFORMS if not item.available], [])

    def test_platforms_listing_covers_registry(self) -> None:
        listing = self.service.list_platforms()
        self.assertEqual(listing["total"], len(listing["items"]))
        self.assertEqual(listing["default_platform"], "hot")
        self.assertEqual(listing["items"][0]["key"], "hot")

    def test_cache_prevents_a_second_upstream_call(self) -> None:
        with patch.object(RealtimeNewsService, "_fetch", return_value=[_entry("ths", "标题", 30)]) as fetch:
            self.service.list_latest(platform="ths")
            self.service.list_latest(platform="ths")
        self.assertEqual(fetch.call_count, 1)

    def test_zero_ttl_disables_cache(self) -> None:
        service = RealtimeNewsService(config=_config(realtime_news_cache_ttl_sec=0.0))
        with patch.object(RealtimeNewsService, "_fetch", return_value=[_entry("ths", "标题", 30)]) as fetch:
            service.list_latest(platform="ths")
            service.list_latest(platform="ths")
        self.assertEqual(fetch.call_count, 2)

    def test_pagination_slices_without_refetching(self) -> None:
        entries = [_entry("ths", f"第{index}条", 30, url=f"u{index}") for index in range(25)]
        with patch.object(RealtimeNewsService, "_fetch", return_value=entries) as fetch:
            first = self.service.list_latest(platform="ths", page=1, page_size=10)
            second = self.service.list_latest(platform="ths", page=3, page_size=10)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(first["total"], 25)
        self.assertEqual(len(first["items"]), 10)
        self.assertEqual(len(second["items"]), 5)
        self.assertEqual(second["items"][0]["title"], "第20条")

    def test_published_at_is_serialized_with_offset(self) -> None:
        with patch.object(RealtimeNewsService, "_fetch", return_value=[_entry("ths", "标题", 30)]):
            result = self.service.list_latest(platform="ths")
        self.assertEqual(result["items"][0]["published_at"], "2026-09-07T19:30:00+08:00")


class RealtimeNewsAggregateTest(unittest.TestCase):
    """「财经热榜」= 多平台聚合，按时间倒序混排。"""

    def setUp(self) -> None:
        RealtimeNewsService.reset_cache()
        self.addCleanup(RealtimeNewsService.reset_cache)
        self.service = RealtimeNewsService(config=_config())

    @staticmethod
    def _fake_fetch(payloads):
        def _fetch(_self, platform):
            value = payloads.get(platform.key, [])
            if isinstance(value, Exception):
                raise value
            return value
        return _fetch

    def test_entries_are_sorted_newest_first(self) -> None:
        payloads = {
            "ths": [_entry("ths", "旧闻", 10, url="a")],
            "sina": [_entry("sina", "新闻", 50, url="b")],
            "eastmoney": [_entry("eastmoney", "中间", 30, url="c")],
        }
        with patch.object(RealtimeNewsService, "_fetch", self._fake_fetch(payloads)):
            result = self.service.list_latest(platform="hot", page_size=10)
        self.assertEqual([item["title"] for item in result["items"]], ["新闻", "中间", "旧闻"])
        self.assertEqual(result["failed_platforms"], [])

    def test_same_story_from_two_platforms_is_deduped_keeping_newest(self) -> None:
        """同一条消息常被多平台同时发，且新浪不给 url，所以按标题归一化去重。"""
        payloads = {
            "ths": [_entry("ths", "科源制药：罗替高汀原料药获批", 20, url="https://10jqka/1")],
            "sina": [_entry("sina", "科源制药：罗替高汀原料药获批！", 40)],
        }
        with patch.object(RealtimeNewsService, "_fetch", self._fake_fetch(payloads)):
            result = self.service.list_latest(platform="hot", page_size=10)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["platform"], "sina")

    def test_one_failing_source_does_not_break_the_page(self) -> None:
        payloads = {
            "ths": [_entry("ths", "同花顺快讯", 30, url="a")],
            "sina": RealtimeNewsUpstreamError("akshare fetch timeout"),
            "jin10": [_entry("jin10", "金十快讯", 40, url="b")],
        }
        with patch.object(RealtimeNewsService, "_fetch", self._fake_fetch(payloads)):
            result = self.service.list_latest(platform="hot", page_size=10)
        self.assertEqual([item["title"] for item in result["items"]], ["金十快讯", "同花顺快讯"])
        self.assertEqual(result["failed_platforms"], ["sina"])

    def test_platforms_without_publish_time_are_excluded_from_aggregate(self) -> None:
        """财新没有发布时间，无法参与倒序，不进聚合。"""
        payloads = {"caixin": [_entry("caixin", "财新精选", 30, url="a")]}
        with patch.object(RealtimeNewsService, "_fetch", self._fake_fetch(payloads)):
            result = self.service.list_latest(platform="hot", page_size=10)
        self.assertEqual(result["items"], [])


def _yicai_dated_page(start_id: int, *, day: int, count: int = 50) -> list:
    """同一页内日期一致的第一财经页，用来构造「翻过窗口起点」的场景。"""
    return [{
        "LiveID": start_id - index,
        "LiveTitle": f"第{start_id - index}条",
        "LiveContent": "正文",
        "CreateDate": f"2026-10-{day:02d}T15:00:00",
        "IsImportant": False,
        "url": f"/brief/{start_id - index}.html",
    } for index in range(count)]


def _dated_entry(platform: str, title: str, hour: int, minute: int) -> RealtimeNewsEntry:
    return RealtimeNewsEntry(
        platform=platform,
        platform_label=platform,
        title=title,
        summary="",
        url=f"https://example.com/{title}",
        published_at=datetime(2026, 9, 7, hour, minute, tzinfo=SHANGHAI_TZ),
    )


class WindowFetchTest(unittest.TestCase):
    """消息面汇总的窗口回看取数：深翻到越过窗口起点即止，单平台失败不影响其余。"""

    def _yicai_window_fetch(self, pages, *, stop_before):
        calls: list = []

        def _page(*, nid: str, page: int, page_size: int, timeout: float):
            calls.append(nid)
            return pages[len(calls) - 1]

        with patch.object(realtime_news_service, "_fetch_yicai_page", _page):
            entries = realtime_news_service._fetch_yicai(
                PLATFORM_BY_KEY["yicai"], limit=600, timeout=30.0,
                max_pages=12, stop_before=stop_before,
            )
        return entries, calls

    def test_paging_stops_once_a_page_reaches_past_the_window_start(self) -> None:
        """窗口起点之前的内容对汇总没用，翻到了就不该继续烧预算。"""
        pages = [
            _yicai_dated_page(300, day=11),  # 窗口内
            _yicai_dated_page(250, day=10),  # 窗口内
            _yicai_dated_page(200, day=8),   # 已越过起点：补完本页即停
            _yicai_dated_page(150, day=7),   # 不该被请求
        ]
        entries, calls = self._yicai_window_fetch(
            pages, stop_before=datetime(2026, 10, 9, 15, 0, tzinfo=SHANGHAI_TZ)
        )
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(entries), 150)

    def test_paging_without_stop_before_keeps_the_limit_semantics(self) -> None:
        """默认 None 时不看时间，只按 limit / 页数上限 / 上游翻完停 —— 与页面取数一致。"""
        pages = [_yicai_dated_page(300 - index * 50, day=8) for index in range(3)]
        pages.append(_yicai_dated_page(150, day=7, count=20))  # 不足 page_size：上游翻完了
        entries, calls = self._yicai_window_fetch(pages, stop_before=None)
        self.assertEqual(len(calls), 4)
        self.assertEqual(len(entries), 170)

    def test_window_fetch_filters_to_the_window_and_is_fail_open(self) -> None:
        since = datetime(2026, 9, 7, 19, 0, tzinfo=SHANGHAI_TZ)
        until = datetime(2026, 9, 7, 19, 59, tzinfo=SHANGHAI_TZ)
        payloads = {
            "ths": [
                _dated_entry("ths", "窗口内", 19, 30),
                _dated_entry("ths", "窗口之前", 18, 30),
            ],
            "sina": RealtimeNewsUpstreamError("akshare fetch timeout"),
        }
        asked: list = []

        def _window_entries_for(_self, platform, since_arg, budget):
            asked.append(platform.key)
            value = payloads.get(platform.key, [])
            if isinstance(value, Exception):
                raise value
            return value

        service = RealtimeNewsService(config=_config())
        with patch.object(RealtimeNewsService, "_window_entries_for", _window_entries_for):
            entries, failed = service.fetch_window_entries(since=since, until=until, budget_sec=5.0)

        self.assertEqual([item.title for item in entries], ["窗口内"])
        self.assertEqual(failed, ["sina"])
        # 财新没有发布时间，无法按窗口过滤，不参与回看取数
        self.assertNotIn("caixin", asked)
        self.assertNotIn("hot", asked)


# ---------------------------------------------------------------------------
# 关键词全文搜索：各平台按任意关键词检索，复用市场级的归一化/缓存/聚合骨架。
# ---------------------------------------------------------------------------


def _sec(minute: int) -> int:
    return int(datetime(2026, 9, 7, 19, minute, tzinfo=SHANGHAI_TZ).timestamp())


class EastmoneySearchNormalizeTest(unittest.TestCase):
    """东方财富关键词全文搜索：date=北京本地时间，tag=媒体名；JSONP 外壳剥离。"""

    PLATFORM = PLATFORM_BY_KEY["eastmoney"]

    def test_search_maps_fields_and_media_tag(self) -> None:
        payload = {"result": {"cmsArticleWebOld": [
            {"title": "贵州茅台三季报点评", "content": "净利润同比增长",
             "mediaName": "证券时报", "url": "http://finance.eastmoney.com/a/1.html",
             "date": "2026-09-07 19:13:00"},
        ]}}
        entry = normalize_em_search(payload, self.PLATFORM, limit=10)[0]
        self.assertEqual(entry.title, "贵州茅台三季报点评")
        self.assertEqual(entry.summary, "净利润同比增长")
        self.assertEqual(entry.url, "https://finance.eastmoney.com/a/1.html")
        self.assertEqual(entry.tag, "证券时报")
        self.assertEqual(entry.published_at, datetime(2026, 9, 7, 19, 13, tzinfo=SHANGHAI_TZ))

    def test_search_tag_blank_when_media_equals_platform_label(self) -> None:
        payload = {"result": {"cmsArticleWebOld": [
            {"title": "标题", "content": "正文", "mediaName": "东方财富", "date": "2026-09-07 19:13"},
        ]}}
        self.assertEqual(normalize_em_search(payload, self.PLATFORM, limit=10)[0].tag, "")

    def test_search_missing_rows_returns_empty(self) -> None:
        self.assertEqual(normalize_em_search({"result": {}}, self.PLATFORM, limit=10), [])

    def test_search_malformed_payload_is_upstream_error(self) -> None:
        with self.assertRaises(RealtimeNewsUpstreamError):
            normalize_em_search([], self.PLATFORM, limit=10)

    def test_strip_jsonp_unwraps_callback(self) -> None:
        self.assertEqual(realtime_news_service._strip_jsonp('cb({"a": 1})'), {"a": 1})

    def test_strip_jsonp_without_parens_raises(self) -> None:
        with self.assertRaises(ValueError):
            realtime_news_service._strip_jsonp("not jsonp")


class ClsSearchNormalizeTest(unittest.TestCase):
    """财联社关键词全文搜索：time=epoch 秒，descr 含 <em> 高亮由清洗剥掉，深度文章 tag=深度。"""

    PLATFORM = PLATFORM_BY_KEY["cls"]

    def test_telegram_extracts_bracket_title_and_detail_url(self) -> None:
        payload = {"data": {"telegram": {"data": [
            {"descr": "【贵州茅台】公司拟<em>回购</em>股份", "id": 909, "time": _sec(13)},
        ]}}}
        entry = normalize_cls_search(payload, self.PLATFORM, limit=10, type_="telegram", tag="")[0]
        self.assertEqual(entry.title, "贵州茅台")
        self.assertEqual(entry.summary, "公司拟回购股份")
        self.assertEqual(entry.url, "https://www.cls.cn/detail/909")
        self.assertEqual(entry.tag, "")
        self.assertEqual(entry.published_at, datetime(2026, 9, 7, 19, 13, tzinfo=SHANGHAI_TZ))

    def test_explicit_title_strips_duplicated_prefix_in_body(self) -> None:
        payload = {"data": {"telegram": {"data": [
            {"title": "贵州茅台", "descr": "【贵州茅台】正文内容", "id": 1, "time": _sec(10)},
        ]}}}
        entry = normalize_cls_search(payload, self.PLATFORM, limit=10, type_="telegram", tag="")[0]
        self.assertEqual(entry.title, "贵州茅台")
        self.assertEqual(entry.summary, "正文内容")

    def test_depth_articles_carry_depth_tag(self) -> None:
        payload = {"data": {"depth": {"data": [
            {"title": "深度解读茅台", "descr": "长文", "id": 2, "time": _sec(11)},
        ]}}}
        entry = normalize_cls_search(payload, self.PLATFORM, limit=10, type_="depth", tag=TAG_DEPTH)[0]
        self.assertEqual(entry.tag, TAG_DEPTH)

    def test_missing_section_returns_empty(self) -> None:
        payload = {"data": {"telegram": {"data": []}}}
        self.assertEqual(
            normalize_cls_search(payload, self.PLATFORM, limit=10, type_="depth", tag=TAG_DEPTH), []
        )

    def test_malformed_payload_is_upstream_error(self) -> None:
        with self.assertRaises(RealtimeNewsUpstreamError):
            normalize_cls_search({"data": []}, self.PLATFORM, limit=10, type_="telegram", tag="")


class CombineSearchSourcesTest(unittest.TestCase):
    """单平台多来源（电报+深度）合并：按时间倒序去重，单源失败降级，全失败才判不可用。"""

    def test_sources_are_merged_sorted_and_trimmed(self) -> None:
        telegram = lambda: [_entry("cls", "旧闻", 10, url="a"), _entry("cls", "新闻", 50, url="b")]
        depth = lambda: [_entry("cls", "深度", 30, url="c")]
        combined = realtime_news_service._combine_search_sources([telegram, depth], limit=2)
        self.assertEqual([e.title for e in combined], ["新闻", "深度"])

    def test_same_title_across_sources_is_deduped(self) -> None:
        a = lambda: [_entry("cls", "贵州茅台回购公告", 20, url="https://x/1")]
        b = lambda: [_entry("cls", "贵州茅台回购公告！", 40)]
        combined = realtime_news_service._combine_search_sources([a, b], limit=10)
        self.assertEqual(len(combined), 1)

    def test_one_failing_source_keeps_the_other(self) -> None:
        ok = lambda: [_entry("cls", "新闻", 30, url="a")]

        def boom():
            raise RealtimeNewsUpstreamError("depth failed")

        combined = realtime_news_service._combine_search_sources([boom, ok], limit=10)
        self.assertEqual([e.title for e in combined], ["新闻"])

    def test_all_sources_failing_raises_first_error(self) -> None:
        def boom1():
            raise RealtimeNewsUpstreamError("first")

        def boom2():
            raise RealtimeNewsUpstreamError("second")

        with self.assertRaises(RealtimeNewsUpstreamError) as ctx:
            realtime_news_service._combine_search_sources([boom1, boom2], limit=10)
        self.assertIn("first", str(ctx.exception))

    def test_entries_without_time_sort_last(self) -> None:
        timed = _entry("cls", "有时间", 30, url="a")
        undated = RealtimeNewsEntry("cls", "cls", "无时间", "", "b", None)
        merged = realtime_news_service._merge_sort_dedupe([undated, timed])
        self.assertEqual([e.title for e in merged], ["有时间", "无时间"])


class RealtimeNewsSearchServiceTest(unittest.TestCase):
    """list_search_news 调度：未知平台校验、空关键词校验、不支持平台空态、单平台拉取、聚合混排、缓存隔离。"""

    def setUp(self) -> None:
        RealtimeNewsService.reset_cache()
        self.addCleanup(RealtimeNewsService.reset_cache)
        self.service = RealtimeNewsService(config=_config())

    @staticmethod
    def _fake_fetch_search(payloads):
        def _fetch_search(_self, platform, keyword):
            value = payloads.get(platform.key, [])
            if isinstance(value, Exception):
                raise value
            return value
        return _fetch_search

    def test_unknown_platform_is_rejected(self) -> None:
        with self.assertRaises(RealtimeNewsServiceError):
            self.service.list_search_news(platform="not-a-platform", q="贵州茅台")

    def test_empty_keyword_is_rejected(self) -> None:
        with self.assertRaises(RealtimeNewsServiceError):
            self.service.list_search_news(platform="hot", q="   ")

    def test_unsupported_platform_returns_empty_but_echoes_query(self) -> None:
        with patch.object(RealtimeNewsService, "_fetch_search", side_effect=AssertionError("不应拉搜索源")):
            result = self.service.list_search_news(platform="ths", q="贵州茅台")
        self.assertFalse(result["available"])
        self.assertEqual(result["items"], [])
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["query"], "贵州茅台")

    def test_single_platform_dispatch_echoes_query_and_items(self) -> None:
        entries = [_entry("cls", "茅台快讯", 30, url="a")]
        with patch.object(RealtimeNewsService, "_fetch_search", return_value=entries):
            result = self.service.list_search_news(platform="cls", q="贵州茅台")
        self.assertTrue(result["available"])
        self.assertTrue(result["has_published_at"])
        self.assertEqual(result["query"], "贵州茅台")
        self.assertEqual([item["title"] for item in result["items"]], ["茅台快讯"])

    def test_aggregate_merges_supported_platforms_and_reports_failures(self) -> None:
        payloads = {
            "eastmoney": [_entry("eastmoney", "东财搜索", 50, url="b")],
            "cls": RealtimeNewsUpstreamError("cls search timeout"),
        }
        with patch.object(RealtimeNewsService, "_fetch_search", self._fake_fetch_search(payloads)):
            result = self.service.list_search_news(platform="hot", q="新能源", page_size=10)
        self.assertEqual([item["title"] for item in result["items"]], ["东财搜索"])
        self.assertEqual(result["failed_platforms"], ["cls"])
        self.assertEqual(result["query"], "新能源")

    def test_search_cache_is_isolated_from_market_level(self) -> None:
        with patch.object(RealtimeNewsService, "_fetch_search",
                          return_value=[_entry("cls", "搜索", 30, url="s")]) as fetch_search, \
             patch.object(RealtimeNewsService, "_fetch",
                          return_value=[_entry("cls", "市场", 30, url="m")]) as fetch:
            self.service.list_search_news(platform="cls", q="贵州茅台")
            self.service.list_search_news(platform="cls", q="贵州茅台")
            self.service.list_latest(platform="cls")
        self.assertEqual(fetch_search.call_count, 1)
        self.assertEqual(fetch.call_count, 1)

    def test_different_keywords_do_not_share_cache(self) -> None:
        with patch.object(RealtimeNewsService, "_fetch_search",
                          return_value=[_entry("cls", "搜索", 30, url="s")]) as fetch_search:
            self.service.list_search_news(platform="cls", q="贵州茅台")
            self.service.list_search_news(platform="cls", q="比亚迪")
        self.assertEqual(fetch_search.call_count, 2)


if __name__ == "__main__":
    unittest.main()
