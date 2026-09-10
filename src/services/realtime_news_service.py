# -*- coding: utf-8 -*-
"""多平台实时财经快讯聚合服务。

本模块只做「请求时直连上游 + 进程内短 TTL 缓存」，不落库、不定时抓取。
需要按时间窗回溯历史消息面的场景走 IntelligenceService 的本地资讯池。

时间基准约定：所有对外暴露的 published_at 都是 timezone-aware。
akshare 与第一财经返回北京本地 naive 时间，NewsNow 与雪球返回 epoch 毫秒（UTC 基准），
在各自的适配器里补齐时区，避免 naive 与 aware 混用导致 8 小时偏差。
"""

from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

from src.config import Config, get_config
from src.services.intelligence_service import IntelligenceService

logger = logging.getLogger(__name__)

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

PROVIDER_AKSHARE = "akshare"
PROVIDER_NEWSNOW = "newsnow"
PROVIDER_XUEQIU = "xueqiu"
PROVIDER_YICAI = "yicai"
PROVIDER_AGGREGATE = "aggregate"
PROVIDER_UNAVAILABLE = ""

AGGREGATE_PLATFORM_KEY = "hot"

_XUEQIU_BASE_URL = "https://xueqiu.com"
_XUEQIU_LIVENEWS_PATH = "/statuses/livenews/list.json"
# 快讯接口要求请求方持有匿名 token cookie，缺失时返回 400 / error_code 400016。
# 该 cookie 由行情页下发；站点首页只下发 WAF 的 acw_tc，拿不到 token。
_XUEQIU_BOOTSTRAP_PATH = "/hq"
_XUEQIU_TOKEN_COOKIE = "xq_a_token"
# token 失效时上游用 400 而非 401 表达，所以 400 也要当作重新 bootstrap 的信号
_XUEQIU_AUTH_STATUS = (400, 401, 403)
# 上游每页硬性只返回 10 条，count 传多大都无效，凑数量只能翻页
_XUEQIU_PAGE_SIZE = 10
_XUEQIU_MAX_PAGES = 10

_YICAI_BASE_URL = "https://www.yicai.com"
_YICAI_BRIEF_LIST_PATH = "/api/ajax/getbrieflist"
_YICAI_BRIEF_REFERER = f"{_YICAI_BASE_URL}/brief/"
# pagesize 上限 50；超过时上游仍返回 HTTP 200，但 body 是把错误串逐字符拆开的
# JSON 数组（["p","a","g","e",...]），必须显式识别，否则会被当成 20 条空数据。
_YICAI_MAX_PAGE_SIZE = 50
_YICAI_MAX_PAGES = 4

# 直连上游（雪球、第一财经）共用的 UA
_DIRECT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 daily-stock-analysis-realtime/1.0"
)
# 剩余预算低于此值就不再翻页：塞一个必然超时的请求只会白丢已抓到的部分
_CURSOR_MIN_REQUEST_BUDGET_SEC = 1.0

_BRACKET_TITLE_PATTERN = re.compile(r"^\s*[【\[]([^】\]]{2,60})[】\]]\s*(.*)$", re.S)


class RealtimeNewsServiceError(ValueError):
    """面向用户的参数校验错误（如未知平台），映射为 400。"""


class RealtimeNewsUpstreamError(RuntimeError):
    """上游平台不可用（超时、HTTP 失败、响应格式异常），映射为 503。"""


@dataclass(frozen=True)
class RealtimeNewsPlatform:
    """一个平台 tab 的静态定义。"""

    key: str
    label: str
    provider: str
    upstream: str = ""
    # 上游是否提供发布时间；为 False 的平台不参与聚合排序
    has_published_at: bool = True

    @property
    def available(self) -> bool:
        return self.provider != PROVIDER_UNAVAILABLE


@dataclass(frozen=True)
class RealtimeNewsEntry:
    """归一化后的一条快讯。"""

    platform: str
    platform_label: str
    title: str
    summary: str
    url: str
    published_at: Optional[datetime]
    tag: str = ""


# 平台注册表。顺序即前端 tab 顺序。
# 当前每个平台都有可用上游；`PROVIDER_UNAVAILABLE` 仍保留给「已知平台但暂无源」的占位场景
# （接口返回 200 + 空列表，前端展示空态），需要时直接加一行即可。
PLATFORMS: Tuple[RealtimeNewsPlatform, ...] = (
    RealtimeNewsPlatform(AGGREGATE_PLATFORM_KEY, "财经热榜", PROVIDER_AGGREGATE),
    RealtimeNewsPlatform("ths", "同花顺", PROVIDER_AKSHARE, "stock_info_global_ths"),
    RealtimeNewsPlatform("eastmoney", "东方财富", PROVIDER_AKSHARE, "stock_info_global_em"),
    RealtimeNewsPlatform("wallstreetcn", "华尔街见闻", PROVIDER_NEWSNOW, "wallstreetcn-quick"),
    RealtimeNewsPlatform("xueqiu", "雪球", PROVIDER_XUEQIU, "livenews"),
    RealtimeNewsPlatform("jin10", "金十数据", PROVIDER_NEWSNOW, "jin10"),
    RealtimeNewsPlatform("caixin", "财新", PROVIDER_AKSHARE, "stock_news_main_cx", has_published_at=False),
    RealtimeNewsPlatform("sina", "新浪财经", PROVIDER_AKSHARE, "stock_info_global_sina"),
    RealtimeNewsPlatform("yicai", "第一财经", PROVIDER_YICAI, "brief"),
    RealtimeNewsPlatform("gelonghui", "格隆汇", PROVIDER_NEWSNOW, "gelonghui"),
)

PLATFORM_BY_KEY: Dict[str, RealtimeNewsPlatform] = {item.key: item for item in PLATFORMS}


# 每个 akshare 上游的列名映射。akshare 各接口列名不统一，且部分接口缺标题或缺时间。
_AKSHARE_COLUMN_SPECS: Dict[str, Dict[str, str]] = {
    "stock_info_global_ths": {"title": "标题", "summary": "内容", "time": "发布时间", "url": "链接"},
    "stock_info_global_em": {"title": "标题", "summary": "摘要", "time": "发布时间", "url": "链接"},
    # 新浪只返回「时间 + 内容」，标题需要从内容开头的【】里抽
    "stock_info_global_sina": {"summary": "内容", "time": "时间"},
    # 财新只返回「tag + summary + url」，既无标题也无发布时间
    "stock_news_main_cx": {"summary": "summary", "url": "url", "tag": "tag"},
}


def _clean_text(value: Any) -> str:
    """去标签、压缩空白。复用资讯池模块的实现，避免两处各写一份。"""
    return IntelligenceService._clean_text(str(value or ""))


def _split_bracket_title(text: str) -> Tuple[str, str]:
    """把「【标题】正文」拆成标题和正文；没有【】时返回空标题。"""
    matched = _BRACKET_TITLE_PATTERN.match(text or "")
    if not matched:
        return "", text or ""
    return _clean_text(matched.group(1)), _clean_text(matched.group(2))


def _parse_beijing_time(value: Any) -> Optional[datetime]:
    """解析 akshare 返回的北京本地时间字符串，补上 Asia/Shanghai 时区。"""
    raw = _clean_text(value)
    if not raw:
        return None
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, pattern).replace(tzinfo=SHANGHAI_TZ)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=SHANGHAI_TZ)


def _parse_epoch_millis(value: Any) -> Optional[datetime]:
    """解析 epoch 毫秒时间戳（UTC 基准），落到 Asia/Shanghai 的 aware 值。"""
    try:
        millis = int(value)
    except (TypeError, ValueError):
        return None
    if millis <= 0:
        return None
    try:
        return datetime.fromtimestamp(millis / 1000, tz=SHANGHAI_TZ)
    except (OverflowError, OSError, ValueError):
        return None


def _dedupe_key(title: str) -> str:
    """聚合去重用的标题归一化键：只保留中英文与数字，截前 40 个字符。"""
    return re.sub(r"[^0-9a-z一-鿿]+", "", (title or "").lower())[:40]


def _fallback_title(summary: str) -> str:
    """缺标题时用摘要开头兜一个，避免前端渲染空标题。"""
    text = _clean_text(summary)
    if len(text) <= 40:
        return text
    return text[:40].rstrip() + "…"


def normalize_akshare_records(
    records: List[Dict[str, Any]],
    platform: RealtimeNewsPlatform,
    *,
    limit: int,
) -> List[RealtimeNewsEntry]:
    """把 akshare DataFrame 的 records 归一化成 RealtimeNewsEntry。

    单独抽出来是为了让单测能直接喂固定 records，不需要打网络。
    """
    spec = _AKSHARE_COLUMN_SPECS.get(platform.upstream)
    if not spec:
        raise RealtimeNewsServiceError(f"unsupported akshare upstream: {platform.upstream}")

    entries: List[RealtimeNewsEntry] = []
    for row in records[:limit]:
        if not isinstance(row, dict):
            continue
        summary = _clean_text(row.get(spec["summary"])) if spec.get("summary") else ""
        title = _clean_text(row.get(spec["title"])) if spec.get("title") else ""
        if not title:
            title, remainder = _split_bracket_title(summary)
            if title:
                summary = remainder
        if not title:
            title = _fallback_title(summary)
        if not title:
            continue
        entries.append(RealtimeNewsEntry(
            platform=platform.key,
            platform_label=platform.label,
            title=title,
            summary=summary if summary != title else "",
            url=_clean_text(row.get(spec["url"])) if spec.get("url") else "",
            published_at=_parse_beijing_time(row.get(spec["time"])) if spec.get("time") else None,
            tag=_clean_text(row.get(spec["tag"])) if spec.get("tag") else "",
        ))
    return entries


def normalize_newsnow_payload(
    payload: Any,
    platform: RealtimeNewsPlatform,
    *,
    limit: int,
) -> List[RealtimeNewsEntry]:
    """把 NewsNow 的 JSON 响应归一化成 RealtimeNewsEntry。"""
    if not isinstance(payload, dict):
        raise RealtimeNewsUpstreamError("invalid NewsNow response: expected object")
    items = payload.get("items")
    if not isinstance(items, list):
        raise RealtimeNewsUpstreamError("invalid NewsNow response: missing items")

    entries: List[RealtimeNewsEntry] = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        extra = item.get("extra") if isinstance(item.get("extra"), dict) else {}
        title = _clean_text(item.get("title"))
        if not title:
            continue
        # 资讯池模块把发布时间解析成 **本地 naive**（与它的 fetched_at 同基）；
        # naive.astimezone() 按系统时区补上偏移，得到与上游同一时刻的 aware 值。
        naive_local = IntelligenceService._parse_datetime_or_timestamp(
            item.get("pubDate") or extra.get("date")
        )
        entries.append(RealtimeNewsEntry(
            platform=platform.key,
            platform_label=platform.label,
            title=title,
            summary=_clean_text(extra.get("info") or extra.get("hover")),
            url=_clean_text(item.get("url") or item.get("mobileUrl")),
            published_at=naive_local.astimezone() if naive_local else None,
        ))
    return entries


def _xueqiu_items(payload: Any) -> List[Any]:
    """校验并取出雪球快讯响应里的 items 列表。"""
    if not isinstance(payload, dict):
        raise RealtimeNewsUpstreamError("invalid Xueqiu response: expected object")
    items = payload.get("items")
    if not isinstance(items, list):
        raise RealtimeNewsUpstreamError("invalid Xueqiu response: missing items")
    return items


def _xueqiu_status_url(item: Dict[str, Any]) -> str:
    """取快讯永久链接。

    上游 ``target`` 固定给 ``http://``，统一升到 https。这里只放行 https 与
    站内相对路径：链接会被前端直接渲染成 ``<a href>``，白名单掉 scheme 比事后转义稳。
    """
    target = _clean_text(item.get("target"))
    if target.startswith("http://"):
        target = "https://" + target[len("http://"):]
    if target.startswith("https://"):
        return target
    if target.startswith("/"):
        return f"{_XUEQIU_BASE_URL}{target}"
    return ""


def _xueqiu_is_marked(value: Any) -> bool:
    """上游 mark=1 表示在页面上标红的重要快讯。"""
    try:
        return int(value) == 1
    except (TypeError, ValueError):
        return False


def normalize_xueqiu_payload(
    payload: Any,
    platform: RealtimeNewsPlatform,
    *,
    limit: int,
) -> List[RealtimeNewsEntry]:
    """把雪球 7×24 快讯的 JSON 响应归一化成 RealtimeNewsEntry。

    上游只给一个 ``text`` 字段，是「【标题】正文」形式，但约半数条目没有 `【】`，
    这时按新浪的同样做法用正文开头兜一个标题。
    """
    entries: List[RealtimeNewsEntry] = []
    for item in _xueqiu_items(payload)[:max(0, limit)]:
        if not isinstance(item, dict):
            continue
        text = _clean_text(item.get("text"))
        if not text:
            continue
        title, summary = _split_bracket_title(text)
        if not title:
            title, summary = _fallback_title(text), text
        if not title:
            continue
        entries.append(RealtimeNewsEntry(
            platform=platform.key,
            platform_label=platform.label,
            title=title,
            summary=summary if summary != title else "",
            url=_xueqiu_status_url(item),
            published_at=_parse_epoch_millis(item.get("created_at")),
            # mark=1 是上游在页面上标红的重要快讯，映射成前端的 tag 徽标
            tag="重要" if _xueqiu_is_marked(item.get("mark")) else "",
        ))
    return entries


def _yicai_items(payload: Any) -> List[Any]:
    """校验并取出第一财经快讯响应里的条目列表。

    正常响应是对象数组。参数越界时上游不走 HTTP 错误码，而是返回把提示串逐字符
    拆开的数组（``["p","a","g","e",...]``），所以这里要按「首个元素不是对象」识别，
    否则会被当成一页合法但全被跳过的数据静默缓存成空列表。
    """
    if not isinstance(payload, list):
        raise RealtimeNewsUpstreamError("invalid Yicai response: expected array")
    if payload and not isinstance(payload[0], dict):
        # 503 的 message 会原样回给调用方，所以上游原文只进日志，不进异常文案
        logger.warning(
            "Yicai rejected the request: %s", _clean_text("".join(map(str, payload[:120])))[:120]
        )
        raise RealtimeNewsUpstreamError("Yicai rejected the request parameters")
    return payload


def _yicai_brief_url(item: Dict[str, Any]) -> str:
    """取快讯详情页链接。

    上游 ``url`` 是站内相对路径（``/brief/<id>.html``、VIP 稿件是 ``/vip/brief/...``），
    ``ShareUrl`` 是 m 站绝对地址。与雪球同样只放行 https 与站内相对路径。
    """
    for key in ("url", "ShareUrl"):
        value = _clean_text(item.get(key))
        if value.startswith("/"):
            return f"{_YICAI_BASE_URL}{value}"
        if value.startswith("https://"):
            return value
        if value.startswith("http://"):
            return "https://" + value[len("http://"):]
    return ""


def normalize_yicai_payload(
    payload: Any,
    platform: RealtimeNewsPlatform,
    *,
    limit: int,
) -> List[RealtimeNewsEntry]:
    """把第一财经 7×24 快讯的 JSON 响应归一化成 RealtimeNewsEntry。

    上游标题与正文是分开的字段，比雪球干净；VIP 稿件（``LiveType=1``）只有标题、
    正文为空，仍然保留——它是有效的快讯标题，正文留空由前端不渲染摘要。
    """
    entries: List[RealtimeNewsEntry] = []
    for item in _yicai_items(payload)[:max(0, limit)]:
        if not isinstance(item, dict):
            continue
        summary = _clean_text(item.get("LiveContent"))
        title = _clean_text(item.get("LiveTitle") or item.get("NewsTitle"))
        if not title:
            title, remainder = _split_bracket_title(summary)
            if title:
                summary = remainder
        if not title:
            title = _fallback_title(summary)
        if not title:
            continue
        entries.append(RealtimeNewsEntry(
            platform=platform.key,
            platform_label=platform.label,
            title=title,
            summary=summary if summary != title else "",
            url=_yicai_brief_url(item),
            # CreateDate 是北京本地 naive 时间（2026-09-10T15:23:22）
            published_at=_parse_beijing_time(item.get("CreateDate")),
            # 上游在页面上标红的重要快讯，映射成前端的 tag 徽标
            tag="重要" if item.get("IsImportant") else "",
        ))
    return entries


def _fetch_akshare(platform: RealtimeNewsPlatform, *, limit: int, timeout: float) -> List[RealtimeNewsEntry]:
    """akshare 不暴露 timeout，用独立线程兜一个 deadline，避免上游卡死拖住整个请求。"""

    def _call() -> List[RealtimeNewsEntry]:
        import akshare as ak  # 延迟导入：akshare 首次导入较慢，不拖累服务启动

        fetch = getattr(ak, platform.upstream, None)
        if fetch is None:
            raise RealtimeNewsUpstreamError(f"akshare interface not found: {platform.upstream}")
        frame = fetch()
        records = [] if frame is None else frame.to_dict("records")
        return normalize_akshare_records(records, platform, limit=limit)

    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"rt-news-{platform.key}")
    try:
        future = pool.submit(_call)
        try:
            return future.result(timeout=timeout)
        except FuturesTimeoutError as exc:
            raise RealtimeNewsUpstreamError(
                f"akshare fetch timeout after {timeout:g}s: {platform.upstream}"
            ) from exc
        except (RealtimeNewsServiceError, RealtimeNewsUpstreamError):
            raise
        except Exception as exc:
            # akshare 内部的网络/解析异常统一归为上游不可用，避免暴露成 500
            raise RealtimeNewsUpstreamError(f"akshare fetch failed: {platform.upstream}") from exc
    finally:
        # wait=False：上游真卡住时不阻塞本次请求；悬挂线程随进程回收，缓存不会写入坏结果
        pool.shutdown(wait=False, cancel_futures=True)


def _fetch_newsnow(
    platform: RealtimeNewsPlatform,
    *,
    limit: int,
    timeout: float,
    base_url: str,
) -> List[RealtimeNewsEntry]:
    try:
        response = requests.get(
            f"{base_url.rstrip('/')}/api/s",
            params={"id": platform.upstream},
            timeout=timeout,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 daily-stock-analysis-realtime/1.0"
                ),
                "Accept": "application/json",
            },
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise RealtimeNewsUpstreamError(f"NewsNow request failed: {platform.upstream}") from exc
    except ValueError as exc:  # json 解析失败
        raise RealtimeNewsUpstreamError(f"NewsNow returned invalid JSON: {platform.upstream}") from exc
    return normalize_newsnow_payload(payload, platform, limit=limit)


_xueqiu_session: Optional[requests.Session] = None
_xueqiu_session_lock = threading.Lock()


def reset_xueqiu_session() -> None:
    """丢弃进程内缓存的雪球匿名会话。token 失效或测试隔离时调用。"""
    global _xueqiu_session
    with _xueqiu_session_lock:
        session, _xueqiu_session = _xueqiu_session, None
    if session is not None:
        session.close()


def _xueqiu_session_with_token(*, timeout: float, force_refresh: bool = False) -> requests.Session:
    """拿一个已持有匿名 token 的会话，进程内复用。

    雪球快讯接口要求 ``xq_a_token`` cookie，裸调返回 400。该 cookie 由行情页
    下发（站点首页只给 WAF 的 acw_tc），所以这里先 GET 一次行情页做 bootstrap。
    不需要账号，也不读取任何用户凭据。

    会话本身不是线程安全的，但雪球平台的抓取被 ``_entries_for`` 的 per-platform
    锁串行化，同一时刻只有一个调用方在用它。
    """
    global _xueqiu_session
    with _xueqiu_session_lock:
        if force_refresh and _xueqiu_session is not None:
            _xueqiu_session.close()
            _xueqiu_session = None
        if _xueqiu_session is not None and _xueqiu_session.cookies.get(_XUEQIU_TOKEN_COOKIE):
            return _xueqiu_session

        session = requests.Session()
        session.headers.update({
            "User-Agent": _DIRECT_USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": f"{_XUEQIU_BASE_URL}/",
        })
        try:
            response = session.get(f"{_XUEQIU_BASE_URL}{_XUEQIU_BOOTSTRAP_PATH}", timeout=timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            session.close()
            raise RealtimeNewsUpstreamError("Xueqiu bootstrap request failed") from exc
        if not session.cookies.get(_XUEQIU_TOKEN_COOKIE):
            session.close()
            raise RealtimeNewsUpstreamError(
                f"Xueqiu bootstrap did not issue the {_XUEQIU_TOKEN_COOKIE} cookie"
            )
        _xueqiu_session = session
        return session


def _fetch_xueqiu_page(*, max_id: str, timeout: float) -> Any:
    """拉一页快讯；token 被拒时重建匿名会话再试一次。"""
    for attempt in range(2):
        session = _xueqiu_session_with_token(timeout=timeout, force_refresh=attempt > 0)
        try:
            response = session.get(
                f"{_XUEQIU_BASE_URL}{_XUEQIU_LIVENEWS_PATH}",
                params={"count": _XUEQIU_PAGE_SIZE, "max_id": max_id},
                timeout=timeout,
            )
        except requests.RequestException as exc:
            raise RealtimeNewsUpstreamError("Xueqiu request failed") from exc
        if response.status_code in _XUEQIU_AUTH_STATUS and attempt == 0:
            logger.info("Xueqiu rejected the anonymous token (HTTP %s), refreshing", response.status_code)
            continue
        if response.status_code >= 400:
            raise RealtimeNewsUpstreamError(f"Xueqiu request failed with HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise RealtimeNewsUpstreamError("Xueqiu returned invalid JSON") from exc
    raise RealtimeNewsUpstreamError("Xueqiu rejected the refreshed anonymous token")


def _fetch_cursor_pages(
    platform: RealtimeNewsPlatform,
    *,
    limit: int,
    timeout: float,
    max_pages: int,
    fetch_page: Callable[[str, int, float], Any],
    normalize: Callable[..., List[RealtimeNewsEntry]],
    next_cursor: Callable[[Any], str],
) -> List[RealtimeNewsEntry]:
    """按游标翻页凑满 limit 条，供直连上游的 provider 共用。

    这类上游单页条数有硬上限，凑够 limit 必须翻页，所以 timeout 在这里是
    **整轮抓取的总预算**而不是单请求超时：预算耗尽或中途某页失败时返回已抓到的
    部分，只有第一页就失败才算平台不可用。

    ``fetch_page(cursor, index, remaining)`` 拿一页原始 payload；
    ``next_cursor(payload)`` 从原始 payload 推下一页游标（不能用归一化后的结果推，
    归一化会丢条目）；返回空串表示没有下一页。
    """
    deadline = time.monotonic() + timeout
    entries: List[RealtimeNewsEntry] = []
    seen: set = set()
    cursor = ""
    for index in range(max_pages):
        remaining = deadline - time.monotonic()
        if remaining < _CURSOR_MIN_REQUEST_BUDGET_SEC:
            break
        try:
            payload = fetch_page(cursor, index, remaining)
            # 解析和推游标也放进 try：中途某页格式异常时同样保留已抓到的部分，
            # 否则第 2 页一坏就把第 1 页的结果一起丢掉，与下面的降级约定不一致
            page_entries = normalize(payload, platform, limit=limit - len(entries))
            following = next_cursor(payload)
        except RealtimeNewsUpstreamError:
            if not entries:
                raise
            logger.warning(
                "Realtime news pagination stopped early platform=%s items=%d", platform.key, len(entries)
            )
            break

        added = 0
        for entry in page_entries:
            # 游标翻页理论上不重叠，仍按 url 兜一层，防上游忽略游标时刷出重复
            marker = entry.url or _dedupe_key(entry.title)
            if marker in seen:
                continue
            seen.add(marker)
            entries.append(entry)
            added += 1
            if len(entries) >= limit:
                break

        # 没有新条目或游标没前进就停，避免上游异常时空转
        if len(entries) >= limit or not added or not following or following == cursor:
            break
        cursor = following
    return entries


def _fetch_xueqiu(platform: RealtimeNewsPlatform, *, limit: int, timeout: float) -> List[RealtimeNewsEntry]:
    """雪球 7×24 快讯。上游每页硬性只给 10 条（``count`` 传再大也无效）。"""
    return _fetch_cursor_pages(
        platform,
        limit=limit,
        timeout=timeout,
        max_pages=_XUEQIU_MAX_PAGES,
        fetch_page=lambda cursor, _index, remaining: _fetch_xueqiu_page(max_id=cursor, timeout=remaining),
        normalize=normalize_xueqiu_payload,
        next_cursor=lambda payload: _clean_text(payload.get("next_max_id")),
    )


def _fetch_yicai_page(*, nid: str, page: int, page_size: int, timeout: float) -> Any:
    """拉一页第一财经快讯。无需 cookie 与鉴权，一次 GET 即可。"""
    params: Dict[str, Any] = {"page": page, "pagesize": page_size, "id": 0, "action": "mix"}
    if nid:
        # nid 是上一页最后一条的 id，开区间；给了 nid 时上游按它定位窗口，page 只作陪衬
        params.update({"type": 0, "nid": nid})
    try:
        response = requests.get(
            f"{_YICAI_BASE_URL}{_YICAI_BRIEF_LIST_PATH}",
            params=params,
            timeout=timeout,
            headers={
                "User-Agent": _DIRECT_USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Referer": _YICAI_BRIEF_REFERER,
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        raise RealtimeNewsUpstreamError("Yicai request failed") from exc
    except ValueError as exc:  # json 解析失败
        raise RealtimeNewsUpstreamError("Yicai returned invalid JSON") from exc


def _yicai_next_cursor(payload: Any, *, page_size: int) -> str:
    """下一页游标 = 本页最后一条的 id。必须从原始 payload 取，归一化会丢条目。

    与雪球不同，上游不给「还有没有下一页」的标记，游标总能从最后一条推出来，
    所以只能靠「本页不足 page_size」判断到底了，否则会白翻一页空数据。
    """
    items = _yicai_items(payload)
    if len(items) < page_size or not isinstance(items[-1], dict):
        return ""
    return _clean_text(items[-1].get("LiveID") or items[-1].get("id"))


def _fetch_yicai(platform: RealtimeNewsPlatform, *, limit: int, timeout: float) -> List[RealtimeNewsEntry]:
    """第一财经 7×24 快讯。pagesize 上限 50，默认 limit 一页就够，超了才翻页。"""
    page_size = max(1, min(limit, _YICAI_MAX_PAGE_SIZE))
    return _fetch_cursor_pages(
        platform,
        limit=limit,
        timeout=timeout,
        max_pages=_YICAI_MAX_PAGES,
        fetch_page=lambda cursor, index, remaining: _fetch_yicai_page(
            nid=cursor, page=index + 1, page_size=page_size, timeout=remaining
        ),
        normalize=normalize_yicai_payload,
        next_cursor=lambda payload: _yicai_next_cursor(payload, page_size=page_size),
    )


@dataclass
class _CacheEntry:
    entries: List[RealtimeNewsEntry] = field(default_factory=list)
    expires_at: float = 0.0


class RealtimeNewsService:
    """按平台拉取最新快讯，带进程内 TTL 缓存与并发去重。"""

    _cache: Dict[str, _CacheEntry] = {}
    _cache_lock = threading.Lock()
    _fetch_locks: Dict[str, threading.Lock] = {}

    def __init__(self, config: Optional[Config] = None):
        self.config = config or get_config()

    @classmethod
    def reset_cache(cls) -> None:
        with cls._cache_lock:
            cls._cache.clear()
            cls._fetch_locks.clear()

    def list_platforms(self) -> Dict[str, Any]:
        return {
            "items": [
                {
                    "key": item.key,
                    "label": item.label,
                    "provider": item.provider,
                    "available": item.available,
                    "has_published_at": item.has_published_at,
                }
                for item in PLATFORMS
            ],
            "total": len(PLATFORMS),
            "default_platform": AGGREGATE_PLATFORM_KEY,
        }

    def list_latest(self, *, platform: str, page: int = 1, page_size: int = 20) -> Dict[str, Any]:
        key = (platform or AGGREGATE_PLATFORM_KEY).strip().lower()
        target = PLATFORM_BY_KEY.get(key)
        if target is None:
            raise RealtimeNewsServiceError(f"unknown platform: {platform}")
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 100))

        failed: List[str] = []
        if not target.available:
            entries: List[RealtimeNewsEntry] = []
        elif target.provider == PROVIDER_AGGREGATE:
            entries, failed = self._aggregate_entries()
        else:
            entries = self._entries_for(target)

        start = (page - 1) * page_size
        return {
            "platform": target.key,
            "platform_label": target.label,
            "available": target.available,
            "has_published_at": target.has_published_at,
            "items": [_entry_to_dict(entry) for entry in entries[start:start + page_size]],
            "total": len(entries),
            "page": page,
            "page_size": page_size,
            "failed_platforms": failed,
            "fetched_at": datetime.now(SHANGHAI_TZ).isoformat(timespec="seconds"),
        }

    def _aggregate_entries(self) -> Tuple[List[RealtimeNewsEntry], List[str]]:
        """并发拉取所有可用且带发布时间的平台，单源失败不影响整体。"""
        sources = [
            item for item in PLATFORMS
            if item.available and item.provider != PROVIDER_AGGREGATE and item.has_published_at
        ]
        merged: List[RealtimeNewsEntry] = []
        failed: List[str] = []
        # 每个源自己已有 deadline，这里再加一点余量做整体兜底，避免单源拖死整页
        budget = max(1.0, min(float(self.config.realtime_news_fetch_timeout_sec), 30.0)) + 2.0
        with ThreadPoolExecutor(max_workers=max(1, len(sources))) as pool:
            futures = {pool.submit(self._entries_for, item): item for item in sources}
            for future, item in futures.items():
                try:
                    merged.extend(future.result(timeout=budget))
                except Exception as exc:
                    failed.append(item.key)
                    logger.warning("Realtime news aggregate source failed platform=%s: %s", item.key, exc)

        # 先按时间倒序再去重，保证同一条消息保留最新的那个平台版本
        merged.sort(key=lambda item: item.published_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        seen: set = set()
        deduped: List[RealtimeNewsEntry] = []
        for entry in merged:
            # 同一条消息常被多个平台同时发，且部分平台（如新浪）不给 url，
            # 所以按标题归一化去重，url 只作兜底。
            marker = _dedupe_key(entry.title) or entry.url
            if marker in seen:
                continue
            seen.add(marker)
            deduped.append(entry)
        return deduped, failed

    def _entries_for(self, platform: RealtimeNewsPlatform) -> List[RealtimeNewsEntry]:
        ttl = max(0.0, float(self.config.realtime_news_cache_ttl_sec))
        cached = self._read_cache(platform.key)
        if cached is not None:
            return cached

        with self._cache_lock:
            lock = self._fetch_locks.setdefault(platform.key, threading.Lock())
        with lock:
            # 双检：并发请求同一平台时，后到的直接复用前一个刚写入的缓存
            cached = self._read_cache(platform.key)
            if cached is not None:
                return cached
            entries = self._fetch(platform)
            with self._cache_lock:
                self._cache[platform.key] = _CacheEntry(entries=entries, expires_at=time.monotonic() + ttl)
            return entries

    def _read_cache(self, key: str) -> Optional[List[RealtimeNewsEntry]]:
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached is not None and cached.expires_at > time.monotonic():
                return cached.entries
        return None

    def _fetch(self, platform: RealtimeNewsPlatform) -> List[RealtimeNewsEntry]:
        limit = max(1, int(self.config.realtime_news_max_items_per_platform))
        timeout = max(1.0, min(float(self.config.realtime_news_fetch_timeout_sec), 30.0))
        if platform.provider == PROVIDER_AKSHARE:
            return _fetch_akshare(platform, limit=limit, timeout=timeout)
        if platform.provider == PROVIDER_NEWSNOW:
            return _fetch_newsnow(
                platform,
                limit=limit,
                timeout=timeout,
                base_url=self.config.newsnow_base_url,
            )
        if platform.provider == PROVIDER_XUEQIU:
            return _fetch_xueqiu(platform, limit=limit, timeout=timeout)
        if platform.provider == PROVIDER_YICAI:
            return _fetch_yicai(platform, limit=limit, timeout=timeout)
        raise RealtimeNewsServiceError(f"platform has no upstream: {platform.key}")


def _entry_to_dict(entry: RealtimeNewsEntry) -> Dict[str, Any]:
    return {
        "platform": entry.platform,
        "platform_label": entry.platform_label,
        "title": entry.title,
        "summary": entry.summary,
        "url": entry.url,
        "published_at": entry.published_at.isoformat(timespec="seconds") if entry.published_at else None,
        "tag": entry.tag,
    }
