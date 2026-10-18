# -*- coding: utf-8 -*-
"""多平台新闻搜索：把用户关键词交给各站点自己的搜索接口，归一化成统一条目。

与「实时新闻」的区别：那边是各平台的市场级快讯 feed（不接受关键词），这里是把关键词
打到各站点的站内搜索接口，所以每个平台的上游、参数与翻页方式都不一样。

上游都是非公开接口，字段随时可能变；本模块只做归一化与「最近 N 天」时间窗过滤，
不落库。翻页由本模块承担：按平台翻若干页，收集时间窗内的条目后按发布时间倒序返回。

各平台的接口形状（均为浏览器端抓包 + 直连验证得到）：

- 财联社  ``POST /api/sw``：签名只覆盖 app/os/sv 公共参数，关键词走 body；``page`` 0 起。
- 同花顺-问财 ``POST /gateway/mobilesearch/comprehensive/search``：body 必须带 ``channels``，
  否则返回 status_code=2；结果是按相关度排的，需要自己按时间过滤。
- 东方财富 ``GET search-api-web.eastmoney.com/search/jsonp``：JSONP 外壳；``pageIndex`` 1 起。
- 华尔街见闻 ``GET api-one-wscn.awtmt.com/apiv1/search/live``：``cursor`` 是页码（首页留空）。
- 金十数据 ``GET search-open-api.jin10.com/offset/search``：必须带 ``x-app-id`` / ``x-version``
  两个头，缺一即 502；``offset`` 用上一页最后一条的毫秒时间戳。
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import monotonic
from typing import Any, Callable, Dict, List, Optional, Tuple

from curl_cffi import requests as _http
from curl_cffi.requests import exceptions as _http_exceptions

from src.services.realtime_news_service import (
    _CLS_BASE_URL,
    _CLS_COMMON_PARAMS,
    _DIRECT_USER_AGENT,
    _clean_text,
    _cls_sign,
    _dedupe_key,
    _parse_beijing_time,
    _parse_epoch_millis,
    _parse_epoch_seconds,
    _split_bracket_title,
    SHANGHAI_TZ,
)

logger = logging.getLogger(__name__)

# 单次请求超时（秒）。各平台搜索接口都在 1s 内返回，给足余量但不至于拖死页面。
_REQUEST_TIMEOUT_SEC = 12.0

# 全平台聚合（``search_all``）挂起所有平台的总预算（秒）。单平台最坏要翻满
# ``_MAX_PAGES`` 页，每页一次请求，理论最坏 48s；首页等不起，因此聚合整体加一个
# 墙钟预算，超预算的平台按失败处理，不拖住已经拿到的结果。
_AGGREGATE_BUDGET_SEC = 30.0

# 统一走 curl_cffi 的 Chrome TLS 指纹：东方财富的搜索接口按 TLS 指纹分流，原生 requests
# 会被降级成只有 passportWeb 的响应，curl / curl_cffi 才拿得到 cmsArticleWebOld。
# 其余平台用同一客户端只是为了一致，它们本身对指纹不敏感。
_IMPERSONATE = "chrome"
_RequestError = _http_exceptions.RequestException


def _http_get(url: str, **kwargs: Any) -> Any:
    return _http.get(url, impersonate=_IMPERSONATE, **kwargs)


def _http_post(url: str, **kwargs: Any) -> Any:
    return _http.post(url, impersonate=_IMPERSONATE, **kwargs)


def _failure_reason(exc: Exception) -> str:
    """把请求异常归成一个固定词表里的短语。

    与 ``realtime_news_service._request_failure_reason`` 同义，但那边按 requests 的异常
    类判定，而本模块走 curl_cffi（两套异常同名不同类），因此这里按类名判定，两边都认。
    """
    name = type(exc).__name__
    if name == "InvalidSchema" and "socks" in str(exc).lower():
        return "SOCKS proxy configured but PySocks is not installed"
    if name == "ProxyError":
        return "proxy error"
    if name in {"SSLError", "CertificateVerifyError"}:
        return "TLS error"
    if name in {"Timeout", "ConnectTimeout", "ReadTimeout"}:
        return "timeout"
    if name == "HTTPError":
        status = getattr(getattr(exc, "response", None), "status_code", None)
        return f"HTTP {status}" if status else "request failed"
    if name in {"ConnectionError", "DNSError"}:
        return "connection error"
    return "request failed"


# 每页向上游要多少条。太大上游会截断，太小翻页次数多。
_PAGE_SIZE = 20

# 每个平台单次搜索最多翻几页。翻页是为了凑够时间窗内的条目，不是无上限抓全量。
_MAX_PAGES = 4

# 时间窗与返回条数默认值。窗口放宽到 7 天：各站点的搜索结果本就不多，
# 2 天常常只剩个位数条目；再往前相关性下降，靠 `_matches_keyword` 兜底过滤。
DEFAULT_WINDOW_DAYS = 7
DEFAULT_LIMIT = 50
_MAX_LIMIT = 200

# 搜索结果里命中关键词的 <em> 高亮标签，归一化前剥掉。
_EM_HIGHLIGHT_PATTERN = re.compile(r"</?em>", re.IGNORECASE)
# 金十/见闻正文里的 HTML 标签。
_TAG_PATTERN = re.compile(r"<[^>]+>")


class NewsSearchServiceError(ValueError):
    """面向用户的参数校验错误（如未知平台），映射为 400。"""


class NewsSearchUpstreamError(RuntimeError):
    """上游平台不可用（超时、HTTP 失败、响应格式异常），映射为 503。"""


@dataclass(frozen=True)
class NewsSearchPlatform:
    """一个平台 tab 的静态定义。"""

    key: str
    label: str
    # 上游结果是否已按发布时间倒序。为 False（相关度排序）时不能靠「本页都早于窗口」提前停。
    time_ordered: bool = True


@dataclass(frozen=True)
class NewsSearchEntry:
    """归一化后的一条搜索结果。"""

    platform: str
    platform_label: str
    title: str
    summary: str
    url: str
    published_at: Optional[datetime]
    source: str = ""


# 平台注册表。顺序即前端 tab 顺序。
PLATFORMS: Tuple[NewsSearchPlatform, ...] = (
    NewsSearchPlatform("cls", "财联社"),
    NewsSearchPlatform("ths", "同花顺-问财", time_ordered=False),
    NewsSearchPlatform("eastmoney", "东方财富", time_ordered=False),
    NewsSearchPlatform("wallstreetcn", "华尔街见闻"),
    NewsSearchPlatform("jin10", "金十数据"),
)

PLATFORM_BY_KEY: Dict[str, NewsSearchPlatform] = {item.key: item for item in PLATFORMS}

# 聚合搜索在响应里回显的「平台」。它不是真实平台，只是一个标识；``PLATFORMS`` 里的
# key 都是具体站点，不会与它撞名。
_ALL_PLATFORMS_KEY = "all"
_ALL_PLATFORMS_LABEL = "全部平台"


def _strip_jsonp(text: str) -> Any:
    """剥掉 JSONP 外壳 ``cb({...})`` 取出 JSON；找不到括号时按解析失败处理。"""
    start = text.find("(")
    end = text.rfind(")")
    if start < 0 or end <= start:
        raise ValueError("not a JSONP payload")
    return json.loads(text[start + 1:end])


def _plain_text(value: Any) -> str:
    """剥 <em> 高亮与 HTML 标签后再压缩空白，用于见闻/金十的富文本正文。"""
    raw = _EM_HIGHLIGHT_PATTERN.sub("", str(value or ""))
    return _clean_text(_TAG_PATTERN.sub(" ", raw))


def _headline(text: str, limit: int = 40) -> str:
    """没有独立标题时，用正文开头兜一个，避免前端渲染空标题。"""
    cleaned = _clean_text(text)
    return cleaned if len(cleaned) <= limit else f"{cleaned[:limit]}…"


# ============================================================
# 财联社
# ============================================================

_CLS_SEARCH_PATH = "/api/sw"
# 财联社搜索要分两个 section 打：telegram 是电报快讯，depth 是资讯/深度文章。
# 不能用 type=all —— 它会把 rn 摊薄到十几个 section 上（每段 2 条），拿不到完整的一页。
_CLS_SEARCH_SECTIONS = ("telegram", "depth")


def _fetch_cls_section(
    keyword: str, section_name: str, page: int, *, timeout: float
) -> Tuple[List[NewsSearchEntry], int]:
    """打一次 ``/api/sw``，取某一个 section 的条目，返回 (条目, 上游给的条数)。"""
    query = dict(_CLS_COMMON_PARAMS)
    query["sign"] = _cls_sign(query)
    body = {"type": section_name, "keyword": keyword, "rn": _PAGE_SIZE, "page": page}
    body.update(_CLS_COMMON_PARAMS)
    try:
        response = _http_post(
            f"{_CLS_BASE_URL}{_CLS_SEARCH_PATH}",
            params=query,
            json=body,
            timeout=timeout,
            headers={
                "User-Agent": _DIRECT_USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Referer": f"{_CLS_BASE_URL}/searchPage",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        response.raise_for_status()
        payload = response.json()
    except _RequestError as exc:
        raise NewsSearchUpstreamError(f"财联社搜索失败: {_failure_reason(exc)}") from exc
    except ValueError as exc:
        raise NewsSearchUpstreamError("财联社搜索返回非 JSON") from exc

    section = (payload.get("data") or {}).get(section_name) if isinstance(payload, dict) else None
    rows = section.get("data") if isinstance(section, dict) else None
    if not isinstance(rows, list):
        return [], 0

    entries: List[NewsSearchEntry] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        # 电报的 title 基本为空、正文以「【标题】」开头；深度文章有独立 title。
        body_text = _plain_text(row.get("descr") or row.get("brief") or "")
        title = _plain_text(row.get("title"))
        if title:
            prefix = f"【{title}】"
            if body_text.startswith(prefix):
                body_text = body_text[len(prefix):]
        else:
            title, remainder = _split_bracket_title(body_text)
            body_text = remainder if title else body_text
        if not title:
            title = _headline(body_text)
        if not title:
            continue
        article_id = _clean_text(row.get("id"))
        entries.append(NewsSearchEntry(
            platform="cls",
            platform_label="财联社",
            title=title,
            summary=body_text if body_text != title else "",
            url=f"{_CLS_BASE_URL}/detail/{article_id}" if article_id else "",
            published_at=_parse_epoch_seconds(row.get("time") or row.get("ctime")),
        ))
    return entries, len(rows)


def _fetch_cls_page(keyword: str, cursor: Any, *, timeout: float) -> Tuple[List[NewsSearchEntry], Any]:
    """财联社一页 = 电报 + 资讯/深度两段合并，按时间倒序去重。"""
    page = 0 if cursor is None else int(cursor)
    merged: List[NewsSearchEntry] = []
    has_more = False
    for section_name in _CLS_SEARCH_SECTIONS:
        entries, row_count = _fetch_cls_section(keyword, section_name, page, timeout=timeout)
        merged.extend(entries)
        # 任一段给满一页就继续翻
        has_more = has_more or row_count >= _PAGE_SIZE

    merged.sort(
        key=lambda item: item.published_at or datetime.min.replace(tzinfo=SHANGHAI_TZ),
        reverse=True,
    )
    return _dedupe_by_title(merged), (page + 1 if has_more else None)


# ============================================================
# 同花顺-问财
# ============================================================

_THS_SEARCH_URL = "https://search.10jqka.com.cn/gateway/mobilesearch/comprehensive/search"
_THS_ORIGIN = "https://search.10jqka.com.cn"
# 综合搜索必须显式给 channels：缺省时上游返回 status_code=2 + 空列表。
_THS_CHANNELS = ["news_filter", "web", "announcement", "report", "interact"]
_THS_APP_ID = "wencai_pc"


def _fetch_ths_page(keyword: str, cursor: Any, *, timeout: float) -> Tuple[List[NewsSearchEntry], Any]:
    offset = 0 if cursor is None else int(cursor)
    body = {
        "offset": offset,
        "size": _PAGE_SIZE,
        "app_id": _THS_APP_ID,
        "query": keyword,
        "channels": list(_THS_CHANNELS),
        "scroll_mode": "web",
        "platform": "pc",
        "slots": [],
    }
    try:
        response = _http_post(
            _THS_SEARCH_URL,
            json=body,
            timeout=timeout,
            headers={
                "User-Agent": _DIRECT_USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Content-Type": "application/json",
                "Origin": _THS_ORIGIN,
                "Referer": f"{_THS_ORIGIN}/",
            },
        )
        response.raise_for_status()
        payload = response.json()
    except _RequestError as exc:
        raise NewsSearchUpstreamError(f"同花顺搜索失败: {_failure_reason(exc)}") from exc
    except ValueError as exc:
        raise NewsSearchUpstreamError("同花顺搜索返回非 JSON") from exc

    if not isinstance(payload, dict):
        raise NewsSearchUpstreamError("同花顺搜索响应格式异常")
    if payload.get("status_code") not in (0, None):
        raise NewsSearchUpstreamError(f"同花顺搜索返回错误: {payload.get('status_msg') or payload.get('status_code')}")
    rows = payload.get("data")
    if not isinstance(rows, list):
        return [], None

    entries: List[NewsSearchEntry] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = _plain_text(row.get("title"))
        summary = _plain_text(row.get("summary") or row.get("source_original") or "")
        if not title:
            title = _headline(summary)
        if not title:
            continue
        extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
        entries.append(NewsSearchEntry(
            platform="ths",
            platform_label="同花顺-问财",
            title=title,
            summary=summary if summary != title else "",
            url=_clean_text(row.get("url")),
            published_at=_parse_epoch_seconds(row.get("publish_time"))
            or _parse_beijing_time(row.get("publish_date")),
            source=_clean_text(extra.get("publish_source") or extra.get("host_name")),
        ))
    return entries, (offset + _PAGE_SIZE if len(rows) >= _PAGE_SIZE else None)


# ============================================================
# 东方财富
# ============================================================

_EM_SEARCH_URL = "https://search-api-web.eastmoney.com/search/jsonp"
_EM_SEARCH_CALLBACK = "jsonp_dsa_news_search"  # JSONP 回调名，仅用于剥壳，无业务含义
_EM_REFERER = "https://so.eastmoney.com/"


def _fetch_eastmoney_page(keyword: str, cursor: Any, *, timeout: float) -> Tuple[List[NewsSearchEntry], Any]:
    page = 1 if cursor is None else int(cursor)
    param = {
        "uid": "",
        "keyword": keyword,
        "type": ["cmsArticleWebOld"],
        "client": "web",
        "clientType": "web",
        "clientVersion": "curr",
        "param": {
            "cmsArticleWebOld": {
                "searchScope": "default",
                "sort": "default",
                "pageIndex": page,
                "pageSize": _PAGE_SIZE,
                "preTag": "",
                "postTag": "",
            }
        },
    }
    try:
        response = _http_get(
            _EM_SEARCH_URL,
            params={"cb": _EM_SEARCH_CALLBACK, "param": json.dumps(param, ensure_ascii=False)},
            timeout=timeout,
            headers={
                "User-Agent": _DIRECT_USER_AGENT,
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Referer": _EM_REFERER,
            },
        )
        response.raise_for_status()
        payload = _strip_jsonp(response.text)
    except _RequestError as exc:
        raise NewsSearchUpstreamError(f"东方财富搜索失败: {_failure_reason(exc)}") from exc
    except ValueError as exc:
        raise NewsSearchUpstreamError("东方财富搜索返回非法 JSONP") from exc

    rows = None
    if isinstance(payload, dict):
        result = payload.get("result")
        rows = result.get("cmsArticleWebOld") if isinstance(result, dict) else None
    if not isinstance(rows, list):
        return [], None

    entries: List[NewsSearchEntry] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = _plain_text(row.get("title"))
        summary = _plain_text(row.get("content"))
        if not title:
            title = _headline(summary)
        if not title:
            continue
        source = _plain_text(row.get("mediaName"))
        entries.append(NewsSearchEntry(
            platform="eastmoney",
            platform_label="东方财富",
            title=title,
            summary=summary if summary != title else "",
            url=_clean_text(row.get("url")),
            published_at=_parse_beijing_time(row.get("date")),
            source=source,
        ))
    return entries, (page + 1 if len(rows) >= _PAGE_SIZE else None)


# ============================================================
# 华尔街见闻
# ============================================================

_WSCN_SEARCH_URL = "https://api-one-wscn.awtmt.com/apiv1/search/live"
_WSCN_REFERER = "https://wallstreetcn.com/"


def _fetch_wallstreetcn_page(keyword: str, cursor: Any, *, timeout: float) -> Tuple[List[NewsSearchEntry], Any]:
    # cursor 是页码：首页留空，响应里给 next_cursor。
    params: Dict[str, Any] = {"query": keyword, "limit": _PAGE_SIZE}
    if cursor not in (None, ""):
        params["cursor"] = cursor
    try:
        response = _http_get(
            _WSCN_SEARCH_URL,
            params=params,
            timeout=timeout,
            headers={
                "User-Agent": _DIRECT_USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Referer": _WSCN_REFERER,
            },
        )
        response.raise_for_status()
        payload = response.json()
    except _RequestError as exc:
        raise NewsSearchUpstreamError(f"华尔街见闻搜索失败: {_failure_reason(exc)}") from exc
    except ValueError as exc:
        raise NewsSearchUpstreamError("华尔街见闻搜索返回非 JSON") from exc

    data = payload.get("data") if isinstance(payload, dict) else None
    rows = data.get("items") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return [], None

    entries: List[NewsSearchEntry] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = _plain_text(row.get("title") or row.get("highlight_title") or "")
        summary = _plain_text(row.get("content_text") or row.get("content") or "")
        if not title:
            title = _headline(summary)
        if not title:
            continue
        entries.append(NewsSearchEntry(
            platform="wallstreetcn",
            platform_label="华尔街见闻",
            title=title,
            summary=summary if summary != title else "",
            url=_clean_text(row.get("uri")),
            published_at=_parse_epoch_seconds(row.get("display_time")),
            source=_clean_text(row.get("global_channel_name")),
        ))
    next_cursor = data.get("next_cursor") if isinstance(data, dict) else None
    return entries, next_cursor


# ============================================================
# 金十数据
# ============================================================

_JIN10_SEARCH_URL = "https://search-open-api.jin10.com/offset/search"
_JIN10_REFERER = "https://search.jin10.com/"
# 站点 Web 客户端的公开 app id / 版本号：缺任一个上游直接 502。
_JIN10_HEADERS = {"x-app-id": "SRvUdAEfVxLO5NAd", "x-version": "1.0.1"}


def _fetch_jin10_page(keyword: str, cursor: Any, *, timeout: float) -> Tuple[List[NewsSearchEntry], Any]:
    # offset 是上一页最后一条的毫秒时间戳，不是页码。
    offset = 0 if cursor is None else int(cursor)
    try:
        response = _http_get(
            _JIN10_SEARCH_URL,
            params={"order": 1, "type": "flash", "keyword": keyword, "offset": offset,
                    "rewrite": 1, "rank": "hot"},
            timeout=timeout,
            headers={
                "User-Agent": _DIRECT_USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Referer": _JIN10_REFERER,
                **_JIN10_HEADERS,
            },
        )
        response.raise_for_status()
        payload = response.json()
    except _RequestError as exc:
        raise NewsSearchUpstreamError(f"金十数据搜索失败: {_failure_reason(exc)}") from exc
    except ValueError as exc:
        raise NewsSearchUpstreamError("金十数据搜索返回非 JSON") from exc

    data = payload.get("data") if isinstance(payload, dict) else None
    rows = data.get("list") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return [], None

    entries: List[NewsSearchEntry] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        nested = row.get("data")
        nested_content = nested.get("content") if isinstance(nested, dict) else None
        # 上游把整段「【标题】正文」同时放在 content / data.content / title 里，任取其一。
        raw = _plain_text(row.get("content") or nested_content or row.get("title") or "")
        title, remainder = _split_bracket_title(raw)
        if title:
            summary = remainder
        else:
            title = _headline(raw)
            summary = raw
        if not title:
            continue
        entries.append(NewsSearchEntry(
            platform="jin10",
            platform_label="金十数据",
            title=title,
            summary=summary if summary != title else "",
            url="",  # 快讯没有独立正文页，前端只展示文本
            published_at=_parse_epoch_millis(row.get("offset"))
            or _parse_beijing_time(row.get("display_datetime")),
        ))
    next_cursor = rows[-1].get("offset") if rows and isinstance(rows[-1], dict) else None
    return entries, next_cursor


# 平台 key -> 单页拉取函数。签名统一为 (keyword, cursor, *, timeout) -> (entries, next_cursor)。
_PAGE_FETCHERS: Dict[str, Callable[..., Tuple[List[NewsSearchEntry], Any]]] = {
    "cls": _fetch_cls_page,
    "ths": _fetch_ths_page,
    "eastmoney": _fetch_eastmoney_page,
    "wallstreetcn": _fetch_wallstreetcn_page,
    "jin10": _fetch_jin10_page,
}


def _entry_to_dict(entry: NewsSearchEntry) -> Dict[str, Any]:
    return {
        "platform": entry.platform,
        "platform_label": entry.platform_label,
        "title": entry.title,
        "summary": entry.summary,
        "url": entry.url,
        "source": entry.source,
        "published_at": entry.published_at.isoformat(timespec="seconds") if entry.published_at else None,
    }


class NewsSearchService:
    """按平台把关键词打到各站点自己的搜索接口，返回时间窗内的条目。"""

    def list_platforms(self) -> Dict[str, Any]:
        return {
            "items": [{"key": item.key, "label": item.label} for item in PLATFORMS],
            "total": len(PLATFORMS),
            "default_platform": PLATFORMS[0].key,
        }

    def search(
        self,
        *,
        platform: str,
        keyword: str,
        days: int = DEFAULT_WINDOW_DAYS,
        limit: int = DEFAULT_LIMIT,
    ) -> Dict[str, Any]:
        key = (platform or "").strip().lower()
        target = PLATFORM_BY_KEY.get(key)
        if target is None:
            raise NewsSearchServiceError(f"unknown platform: {platform}")

        cleaned = _clean_text(keyword)
        if not cleaned:
            raise NewsSearchServiceError("搜索关键词不能为空")
        window_days = max(1, min(int(days), 30))
        capped_limit = max(1, min(int(limit), _MAX_LIMIT))

        since = datetime.now(SHANGHAI_TZ) - timedelta(days=window_days)
        entries, pages_scanned = self._collect(target, cleaned, since=since, limit=capped_limit)
        return {
            "platform": target.key,
            "platform_label": target.label,
            "query": cleaned,
            "window_days": window_days,
            "items": [_entry_to_dict(entry) for entry in entries],
            "total": len(entries),
            "pages_scanned": pages_scanned,
            "fetched_at": datetime.now(SHANGHAI_TZ).isoformat(timespec="seconds"),
        }

    def search_all(
        self,
        *,
        keyword: str,
        days: int = DEFAULT_WINDOW_DAYS,
        limit: int = DEFAULT_LIMIT,
        budget_sec: float = _AGGREGATE_BUDGET_SEC,
    ) -> Dict[str, Any]:
        """并发打全部平台，合并成一个跨平台结果集。

        与 ``search`` 的两处关键差别：

        1. **fail-open**：单个平台超时或上游异常只记入 ``failed_platforms``，不影响其余
           平台的结果，也不向调用方抛错——5 家上游里挂一两家是常态，不该拖垮整页。
        2. **总预算**（默认 ``_AGGREGATE_BUDGET_SEC``，可用 ``budget_sec`` 调小）：每个
           future 的等待都收敛到同一个墙钟 deadline 上，且收尾用 ``shutdown(wait=False)``，
           不等掉队线程。这里刻意不用 ``with ThreadPoolExecutor(...)``：它的 ``__exit__``
           会 join 所有线程，而单平台最坏要翻 4 页、每页都可能顶满
           ``_REQUEST_TIMEOUT_SEC``，等下去会把调用方拖到近 48s，预算就失去意义了。
           分析链路（``SiteNewsSearchProvider``）会传一个更短的预算，首页交互则用默认值。

        每个平台仍各自走 ``_collect`` 的翻页、关键词过滤与时间窗，最后统一按发布时间倒序
        再按标题去重，所以同一条消息被多站同发只会留一条。
        """
        cleaned = _clean_text(keyword)
        if not cleaned:
            raise NewsSearchServiceError("搜索关键词不能为空")
        window_days = max(1, min(int(days), 30))
        capped_limit = max(1, min(int(limit), _MAX_LIMIT))

        since = datetime.now(SHANGHAI_TZ) - timedelta(days=window_days)
        merged: List[NewsSearchEntry] = []
        failed_platforms: List[str] = []
        pages_scanned = 0

        deadline = monotonic() + max(0.1, float(budget_sec))
        pool = ThreadPoolExecutor(
            max_workers=max(1, len(PLATFORMS)),
            thread_name_prefix="news-search-aggregate",
        )
        futures = {
            pool.submit(self._collect, target, cleaned, since=since, limit=capped_limit): target
            for target in PLATFORMS
        }
        try:
            for future, target in futures.items():
                try:
                    entries, pages = future.result(timeout=max(0.1, deadline - monotonic()))
                except Exception as exc:
                    failed_platforms.append(target.key)
                    logger.warning(
                        "News search aggregate platform failed platform=%s: %s", target.key, exc
                    )
                    continue
                merged.extend(entries)
                pages_scanned += pages
        finally:
            pool.shutdown(wait=False)

        merged.sort(
            key=lambda item: item.published_at or datetime.min.replace(tzinfo=SHANGHAI_TZ),
            reverse=True,
        )
        deduped = _dedupe_by_title(merged)[:capped_limit]
        return {
            "platform": _ALL_PLATFORMS_KEY,
            "platform_label": _ALL_PLATFORMS_LABEL,
            "query": cleaned,
            "window_days": window_days,
            "items": [_entry_to_dict(entry) for entry in deduped],
            "total": len(deduped),
            "pages_scanned": pages_scanned,
            "failed_platforms": failed_platforms,
            "fetched_at": datetime.now(SHANGHAI_TZ).isoformat(timespec="seconds"),
        }

    def _collect(
        self,
        platform: NewsSearchPlatform,
        keyword: str,
        *,
        since: datetime,
        limit: int,
    ) -> Tuple[List[NewsSearchEntry], int]:
        """翻页收集时间窗内的条目。

        时间倒序的平台一旦翻到整页都早于窗口就停；相关度排序的平台（同花顺、东财）
        没有这个保证，只能翻满 ``_MAX_PAGES`` 再按时间过滤。
        """
        fetcher = _PAGE_FETCHERS.get(platform.key)
        if fetcher is None:  # pragma: no cover - 注册表与 fetcher 表恒同步
            raise NewsSearchServiceError(f"platform has no search upstream: {platform.key}")

        collected: List[NewsSearchEntry] = []
        cursor: Any = None
        pages_scanned = 0
        for _ in range(_MAX_PAGES):
            page_entries, cursor = fetcher(keyword, cursor, timeout=_REQUEST_TIMEOUT_SEC)
            pages_scanned += 1
            # 各站点的搜索都是模糊匹配，翻到第二页起常混进与关键词无关的快讯
            # （见闻会退化成市场级直播流），这里按关键词做一次相关性过滤。
            page_entries = [e for e in page_entries if _matches_keyword(e, keyword)]
            collected.extend(page_entries)

            if sum(1 for e in collected if e.published_at and e.published_at >= since) >= limit:
                break
            if cursor is None:
                break
            if platform.time_ordered and page_entries and all(
                e.published_at is not None and e.published_at < since for e in page_entries
            ):
                break

        in_window = [e for e in collected if e.published_at and e.published_at >= since]
        in_window.sort(key=lambda e: e.published_at, reverse=True)
        return _dedupe_by_title(in_window)[:limit], pages_scanned


def _matches_keyword(entry: NewsSearchEntry, keyword: str) -> bool:
    """判断条目是否真的提到关键词。

    按空白切词后要求全部命中：单关键词时就是子串匹配，多关键词时避免只命中其中一个
    就把整条留下。标题与摘要合起来看——金十条目的标题是从正文抽的，东财的媒体名在
    单独字段里，都算正文的一部分。
    """
    haystack = f"{entry.title} {entry.summary} {entry.source}".lower()
    tokens = [token for token in keyword.lower().split() if token]
    if not tokens:
        return True
    return all(token in haystack for token in tokens)


def _dedupe_by_title(entries: List[NewsSearchEntry]) -> List[NewsSearchEntry]:
    """按标题归一化去重（同一条消息常被多站点同时发）。"""
    seen: set = set()
    deduped: List[NewsSearchEntry] = []
    for entry in entries:
        marker = _dedupe_key(entry.title) or entry.url
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(entry)
    return deduped
