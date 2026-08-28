# -*- coding: utf-8 -*-
"""CCTV《新闻联播》文字稿抓取与解析。

数据来源为央视网栏目页：

- 当日节目单：``https://tv.cctv.com/lm/xwlb/day/YYYYMMDD.shtml``，每个 ``li``
  下有指向单条新闻页的链接，标题取 ``a[title]``（内部文本会混入 ``i`` 角标）。
- 单条新闻页：正文位于 ``div.content_area`` 的段落里，标题取 ``<title>``
  或 ``og:title``，均带 ``[视频]`` 前缀。

本模块只负责"取回并结构化单日文字稿"，不落库、不做 LLM 分析，便于用固定
HTML 做确定性单测。抓取结果用 :class:`DayFetchResult` 显式区分"节目单拿不到"、
"部分条目失败"和"完整成功"，不静默丢条——上层要据此决定是否落库、是否重试。
"""

from __future__ import annotations

import codecs
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

DAY_INDEX_URL_TEMPLATE = "https://tv.cctv.com/lm/xwlb/day/{day}.shtml"
ARTICLE_HOST_ALLOWLIST = ("tv.cctv.com", "news.cctv.com")

# 更早的日期在已下线风险较高的旧站（cctv.cntv.cn），页面结构完全不同，不支持。
EARLIEST_SUPPORTED_DAY = date(2016, 2, 3)

DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_BACKOFF_SECONDS = 0.6
DEFAULT_REQUEST_INTERVAL_SECONDS = 0.15
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_SEGMENTS_PER_DAY = 80
MIN_CONTENT_CHARS = 20

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_TITLE_PREFIX_RE = re.compile(r"^\s*\[(?:视频|图文|快讯|组图)\]\s*")
_LEAD_RE = re.compile(r"^央视网消息\s*[（(]\s*新闻联播\s*[)）]\s*[:：]\s*")
_INLINE_SPACE_RE = re.compile(r"[ \t　]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")
_CHARSET_HEADER_RE = re.compile(r"charset=([\w\-]+)")
_CHARSET_META_RE = re.compile(rb"""charset=["']?([\w\-]+)""", re.IGNORECASE)


@dataclass(frozen=True)
class CctvNewsSegment:
    """单条新闻联播文字稿。"""

    news_date: date
    seq: int
    title: str
    content: str
    url: str

    @property
    def char_count(self) -> int:
        return len(self.content)


@dataclass
class DayFetchResult:
    """单日抓取结果，带完整性标记。"""

    news_date: date
    segments: List[CctvNewsSegment] = field(default_factory=list)
    index_available: bool = False
    listed_count: int = 0
    failed_urls: List[str] = field(default_factory=list)
    skipped_urls: List[str] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        """节目单可达、无失败条目、且至少抓到一条正文。"""
        return self.index_available and not self.failed_urls and bool(self.segments)


def _clean_title(raw: str) -> str:
    title = _TITLE_PREFIX_RE.sub("", (raw or "").strip())
    return _INLINE_SPACE_RE.sub(" ", title).strip()


def _clean_content(raw: str) -> str:
    text = (raw or "").replace(" ", " ").replace("\r\n", "\n").replace("\r", "\n")
    lines = [_INLINE_SPACE_RE.sub(" ", line).strip() for line in text.split("\n")]
    body = "\n".join(line for line in lines if line)
    return _BLANK_LINES_RE.sub("\n\n", _LEAD_RE.sub("", body)).strip()


def _resolve_encoding(headers: Optional[Dict[str, str]], raw: bytes) -> str:
    """按"响应头 charset -> HTML meta charset -> utf-8"的顺序确定解码方式。

    不能用 ``requests`` 的 ``apparent_encoding``：流式读完之后 ``response.content``
    已被消费，访问它会抛 RuntimeError。而 ``response.encoding`` 在响应头缺少
    charset 时会退回 ISO-8859-1，会把中文糊成乱码。
    """
    content_type = ((headers or {}).get("Content-Type") or "").lower()
    match = _CHARSET_HEADER_RE.search(content_type)
    if not match:
        match = _CHARSET_META_RE.search(raw[:4096])
        if match:
            candidate = match.group(1).decode("ascii", errors="ignore")
            return _normalize_encoding(candidate)
        return "utf-8"
    return _normalize_encoding(match.group(1))


def _normalize_encoding(candidate: str) -> str:
    name = (candidate or "").strip().strip("'\"").lower()
    if not name or name == "iso-8859-1":
        return "utf-8"
    try:
        codecs.lookup(name)
    except LookupError:
        return "utf-8"
    return name


def _is_allowed_article_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    return host in ARTICLE_HOST_ALLOWLIST


def parse_day_index(html: str) -> List[Dict[str, str]]:
    """解析当日节目单，返回按播出顺序去重后的 ``{"url", "title"}`` 列表。

    每个 ``li`` 里通常有两个指向同一条新闻的链接（缩略图 + 标题），因此按 URL
    去重并保留首次出现顺序。标题一律取 ``title`` 属性：链接内部文本混有
    ``<i class="sql0">完整版</i>`` 这类悬浮角标。
    """
    soup = BeautifulSoup(html or "", "html.parser")
    seen: set = set()
    items: List[Dict[str, str]] = []

    for anchor in soup.select("li a[href]"):
        url = (anchor.get("href") or "").strip()
        if not url or url in seen or not _is_allowed_article_url(url):
            continue
        title = _clean_title(anchor.get("title") or anchor.get("alt") or "")
        if not title:
            continue
        seen.add(url)
        items.append({"url": url, "title": title})
        if len(items) >= MAX_SEGMENTS_PER_DAY:
            logger.warning("新闻联播节目单条目超过 %s 条，已截断", MAX_SEGMENTS_PER_DAY)
            break

    return items


def parse_article(html: str) -> Optional[Tuple[str, str]]:
    """解析单条新闻页，返回 ``(标题, 正文)``；拿不到正文时返回 ``None``。

    纯视频条目（无文字稿）会走到这个 ``None`` 分支，由调用方记为 skipped。
    """
    soup = BeautifulSoup(html or "", "html.parser")

    container = soup.select_one("div.content_area") or soup.select_one("div.cnt_bd")
    if container is None:
        return None

    paragraphs = [p.get_text(" ", strip=True) for p in container.find_all("p")]
    content = _clean_content("\n\n".join(p for p in paragraphs if p))
    if not content:
        content = _clean_content(container.get_text("\n", strip=True))
    if len(content) < MIN_CONTENT_CHARS:
        return None

    title = ""
    meta = soup.find("meta", attrs={"property": "og:title"})
    if meta and meta.get("content"):
        title = _clean_title(meta["content"])
    if not title:
        heading = soup.select_one("h1, h3, .video_title, .title_con")
        if heading:
            title = _clean_title(heading.get_text(" ", strip=True))
    if not title and soup.title and soup.title.string:
        title = _clean_title(soup.title.string)

    return title, content


class CctvNewsFetcher:
    """按天抓取《新闻联播》文字稿。

    只做网络与解析，不感知数据库。默认串行抓取并保留礼貌间隔——单日十几到
    二十几条，串行足够快，也不给央视网压力。
    """

    def __init__(
        self,
        *,
        session: Optional[requests.Session] = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        request_interval_seconds: float = DEFAULT_REQUEST_INTERVAL_SECONDS,
    ) -> None:
        self._session = session or requests.Session()
        self._timeout = max(1.0, min(float(timeout_seconds), 60.0))
        self._max_retries = max(0, min(int(max_retries), 5))
        self._request_interval = max(0.0, min(float(request_interval_seconds), 5.0))

    def fetch_day(self, news_date: date) -> DayFetchResult:
        """抓取指定日期的全部文字稿。

        节目单不可达时返回 ``index_available=False``；单条失败记入
        ``failed_urls``，无文字稿的纯视频条目记入 ``skipped_urls``。
        """
        result = DayFetchResult(news_date=news_date)
        if news_date < EARLIEST_SUPPORTED_DAY:
            logger.warning(
                "新闻联播文字稿仅支持 %s 之后的日期，跳过 %s",
                EARLIEST_SUPPORTED_DAY.isoformat(),
                news_date.isoformat(),
            )
            return result

        index_html = self._get(DAY_INDEX_URL_TEMPLATE.format(day=news_date.strftime("%Y%m%d")))
        if index_html is None:
            return result

        result.index_available = True
        listed = parse_day_index(index_html)
        result.listed_count = len(listed)
        if not listed:
            logger.info("新闻联播 %s 节目单为空（当天可能未更新）", news_date.isoformat())
            return result

        seq = 0
        for item in listed:
            article_html = self._get(item["url"])
            if article_html is None:
                result.failed_urls.append(item["url"])
                continue
            parsed = parse_article(article_html)
            if parsed is None:
                result.skipped_urls.append(item["url"])
                continue
            title, content = parsed
            seq += 1
            result.segments.append(
                CctvNewsSegment(
                    news_date=news_date,
                    seq=seq,
                    title=title or item["title"],
                    content=content,
                    url=item["url"],
                )
            )

        logger.info(
            "新闻联播 %s 抓取完成：列出 %s 条，入库 %s 条，失败 %s 条，无文字稿 %s 条",
            news_date.isoformat(),
            result.listed_count,
            len(result.segments),
            len(result.failed_urls),
            len(result.skipped_urls),
        )
        return result

    def _get(self, url: str) -> Optional[str]:
        """带重试地取回页面文本；全部尝试失败返回 ``None``。"""
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        last_error: Optional[str] = None

        for attempt in range(self._max_retries + 1):
            if attempt > 0 or self._request_interval:
                time.sleep(
                    DEFAULT_RETRY_BACKOFF_SECONDS * attempt
                    if attempt > 0
                    else self._request_interval
                )
            response = None
            try:
                response = self._session.get(
                    url, headers=headers, timeout=self._timeout, stream=True
                )
                if response.status_code == 404:
                    logger.debug("新闻联播页面不存在：%s", url)
                    return None
                response.raise_for_status()
                return self._read_limited_text(response)
            except Exception as exc:  # noqa: BLE001 - 上游异常统一转成重试/None
                last_error = type(exc).__name__
            finally:
                if response is not None:
                    response.close()

        logger.warning("新闻联播页面抓取失败（%s 次尝试，最后错误 %s）：%s",
                       self._max_retries + 1, last_error, url)
        return None

    @staticmethod
    def _read_limited_text(response: requests.Response) -> str:
        """按 :data:`MAX_RESPONSE_BYTES` 上限读取响应体，避免异常大页面打爆内存。"""
        chunks: List[bytes] = []
        total = 0
        for chunk in response.iter_content(chunk_size=8192):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise ValueError("cctv response is too large")
            chunks.append(chunk)
        raw = b"".join(chunks)
        return raw.decode(_resolve_encoding(response.headers, raw), errors="replace")
