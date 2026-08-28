# -*- coding: utf-8 -*-
"""CCTV《新闻联播》文字稿的同步与查询服务。

职责边界：
- :mod:`src.services.cctv_news_fetcher` 只负责抓取与解析；
- 本模块负责"哪天需要抓、抓完怎么落库、前端怎么查"；
- :mod:`src.services.cctv_news_analyzer` 负责基于文字稿的 LLM 分析。

摘要（``summary``）是从正文确定性截取的首句片段，不经过 LLM，因此不会引入
任何原文没有的信息；解读性内容一律走 analyzer，并要求给出引用编号。
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from src.config import Config, get_config
from src.core.trading_calendar import get_market_now
from src.repositories.cctv_news_repo import CctvNewsRepository
from src.services.cctv_news_fetcher import (
    EARLIEST_SUPPORTED_DAY,
    CctvNewsFetcher,
    DayFetchResult,
)
from src.storage import CctvNewsItem

logger = logging.getLogger(__name__)

DEFAULT_LIST_DAYS = 30
MAX_LIST_DAYS = 90
MAX_SYNC_DAYS_PER_CALL = 45
SUMMARY_MAX_CHARS = 120
INCOMPLETE_DAY_RETRY_MINUTES = 30

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？；!?;])")


class CctvNewsServiceError(ValueError):
    """入参非法或窗口不可用。由 API 层映射为 400。"""


def build_summary(content: str, *, max_chars: int = SUMMARY_MAX_CHARS) -> str:
    """从正文确定性地截出摘要：按句累加到长度上限，超出则加省略号。"""
    text = " ".join((content or "").split())
    if not text:
        return ""
    if len(text) <= max_chars:
        return text

    summary = ""
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        if not sentence:
            continue
        if summary and len(summary) + len(sentence) > max_chars:
            break
        summary += sentence
        if len(summary) >= max_chars:
            break
    if not summary:
        summary = text[:max_chars]
    return summary[:max_chars].rstrip() + "…"


class CctvNewsService:
    """新闻联播文字稿的同步与查询入口。"""

    def __init__(
        self,
        repository: Optional[CctvNewsRepository] = None,
        fetcher: Optional[CctvNewsFetcher] = None,
        config: Optional[Config] = None,
    ) -> None:
        self.config = config or get_config()
        self.repo = repository or CctvNewsRepository()
        self._fetcher = fetcher

    @property
    def fetcher(self) -> CctvNewsFetcher:
        """惰性构造 fetcher，便于测试注入，也避免只查询时白建 session。"""
        if self._fetcher is None:
            self._fetcher = CctvNewsFetcher(
                timeout_seconds=float(
                    getattr(self.config, "cctv_news_fetch_timeout_sec", 10.0)
                )
            )
        return self._fetcher

    # === 窗口计算 ===

    def today(self) -> date:
        """以北京时间为准的"今天"，避免服务器时区导致差一天。"""
        return get_market_now("cn").date()

    def resolve_window(self, days: Optional[int]) -> Tuple[date, date]:
        """把"最近 N 天"换算成 ``(start_date, end_date)``，两端均包含。"""
        span = DEFAULT_LIST_DAYS if days is None else int(days)
        if span < 1:
            raise CctvNewsServiceError("days must be >= 1")
        if span > MAX_LIST_DAYS:
            raise CctvNewsServiceError(f"days must be <= {MAX_LIST_DAYS}")
        end_date = self.today()
        start_date = end_date - timedelta(days=span - 1)
        if start_date < EARLIEST_SUPPORTED_DAY:
            start_date = EARLIEST_SUPPORTED_DAY
        return start_date, end_date

    # === 同步 ===

    def sync_recent(self, *, days: Optional[int] = None, force: bool = False) -> Dict[str, Any]:
        start_date, end_date = self.resolve_window(days)
        return self.sync_range(start_date, end_date, force=force)

    def sync_range(
        self,
        start_date: date,
        end_date: date,
        *,
        force: bool = False,
    ) -> Dict[str, Any]:
        """按天同步 ``[start_date, end_date]``，已完整抓取的日期默认跳过。"""
        if start_date > end_date:
            raise CctvNewsServiceError("start_date must not be after end_date")
        span = (end_date - start_date).days + 1
        if span > MAX_SYNC_DAYS_PER_CALL:
            raise CctvNewsServiceError(
                f"sync window must not exceed {MAX_SYNC_DAYS_PER_CALL} days"
            )

        pending = self._pending_dates(start_date, end_date, force=force)
        results: List[Dict[str, Any]] = []
        created_total = 0
        updated_total = 0

        for news_date in pending:
            fetch_result = self.fetcher.fetch_day(news_date)
            created, updated = self._persist_day(fetch_result)
            created_total += created
            updated_total += updated
            results.append(
                {
                    "news_date": news_date.isoformat(),
                    "listed_count": fetch_result.listed_count,
                    "item_count": len(fetch_result.segments),
                    "created": created,
                    "updated": updated,
                    "failed_count": len(fetch_result.failed_urls),
                    "skipped_count": len(fetch_result.skipped_urls),
                    "is_complete": fetch_result.is_complete,
                }
            )

        retention = self._apply_retention_quietly(created_total, updated_total)

        return {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "requested_days": span,
            "synced_days": len(pending),
            "created": created_total,
            "updated": updated_total,
            "deleted": retention,
            "days": results,
        }

    def _apply_retention_quietly(self, created: int, updated: int) -> int:
        """写入路径顺手做保留期清理；失败只记 warning，不影响本次同步结果。"""
        if created == 0 and updated == 0:
            return 0
        try:
            return self.apply_retention()["deleted_items"]
        except Exception as exc:  # noqa: BLE001 - 清理失败不该让同步整体失败
            logger.warning("新闻联播文字稿保留期清理失败：%s", exc)
            return 0

    def _pending_dates(self, start_date: date, end_date: date, *, force: bool) -> List[date]:
        """筛出需要抓取的日期：已完整的跳过，刚失败过的短时间内不重试。"""
        if force:
            candidates = self._iter_dates(start_date, end_date)
            return [d for d in candidates if d >= EARLIEST_SUPPORTED_DAY]

        complete = set(self.repo.list_complete_dates(start_date, end_date))
        retry_floor = datetime.now() - timedelta(minutes=INCOMPLETE_DAY_RETRY_MINUTES)
        pending: List[date] = []
        for news_date in self._iter_dates(start_date, end_date):
            if news_date < EARLIEST_SUPPORTED_DAY or news_date in complete:
                continue
            status = self.repo.get_day_status(news_date)
            if status is not None and status.last_synced_at and status.last_synced_at > retry_floor:
                # 当天节目单还没更新（通常 20:00 后才有）时会反复抓空，这里加冷却。
                continue
            pending.append(news_date)
        return pending

    @staticmethod
    def _iter_dates(start_date: date, end_date: date) -> List[date]:
        """从新到旧返回日期列表：最近的先抓，前端更快看到内容。"""
        span = (end_date - start_date).days
        return [end_date - timedelta(days=offset) for offset in range(span + 1)]

    def _persist_day(self, fetch_result: DayFetchResult) -> Tuple[int, int]:
        created = 0
        updated = 0
        if fetch_result.segments:
            created, updated = self.repo.upsert_items(
                [
                    {
                        "news_date": segment.news_date,
                        "seq": segment.seq,
                        "title": segment.title,
                        "content": segment.content,
                        "summary": build_summary(segment.content),
                        "url": segment.url,
                        "char_count": segment.char_count,
                        "fetched_at": datetime.now(),
                    }
                    for segment in fetch_result.segments
                ]
            )
        self.repo.record_day(
            fetch_result.news_date,
            item_count=len(fetch_result.segments),
            listed_count=fetch_result.listed_count,
            failed_count=len(fetch_result.failed_urls),
            skipped_count=len(fetch_result.skipped_urls),
            is_complete=fetch_result.is_complete,
        )
        return created, updated

    # === 查询 ===

    def list_days(self, *, days: Optional[int] = None) -> Dict[str, Any]:
        """按天聚合的目录，供前端左侧日期列表使用。"""
        start_date, end_date = self.resolve_window(days)
        summaries = self.repo.list_day_summaries(start_date, end_date)
        return {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "total_days": len(summaries),
            "total_items": sum(int(row["item_count"]) for row in summaries),
            "days": [
                {
                    "news_date": row["news_date"].isoformat(),
                    "item_count": int(row["item_count"]),
                    "char_count": int(row["char_count"]),
                }
                for row in summaries
            ],
        }

    def list_items(
        self,
        *,
        news_date: Optional[date] = None,
        days: Optional[int] = None,
        query: Optional[str] = None,
        page: int = 1,
        page_size: int = 50,
        include_content: bool = False,
    ) -> Dict[str, Any]:
        """分页查询文字稿。``news_date`` 优先于 ``days``。"""
        if news_date is not None:
            start_date = end_date = news_date
        else:
            start_date, end_date = self.resolve_window(days)

        rows, total = self.repo.list_items(
            start_date=start_date,
            end_date=end_date,
            query=query,
            page=page,
            page_size=page_size,
        )
        return {
            "items": [self._item_to_dict(row, include_content=include_content) for row in rows],
            "total": int(total),
            "page": max(1, int(page)),
            "page_size": max(1, int(page_size)),
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        }

    def get_item(self, item_id: int) -> Dict[str, Any]:
        row = self.repo.get_item(item_id)
        if row is None:
            raise CctvNewsServiceError(f"cctv news item {item_id} not found")
        return self._item_to_dict(row, include_content=True)

    def apply_retention(self) -> Dict[str, Any]:
        retention_days = int(getattr(self.config, "cctv_news_retention_days", 60))
        items, days, analyses = self.repo.apply_retention(retention_days)
        return {
            "retention_days": retention_days,
            "deleted_items": items,
            "deleted_days": days,
            "deleted_analyses": analyses,
        }

    @staticmethod
    def _item_to_dict(row: CctvNewsItem, *, include_content: bool) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "id": int(row.id),
            "news_date": row.news_date.isoformat() if row.news_date else None,
            "seq": int(row.seq or 0),
            "title": row.title,
            "summary": row.summary or "",
            "url": row.url,
            "char_count": int(row.char_count or 0),
            "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
        }
        if include_content:
            payload["content"] = row.content or ""
        return payload
