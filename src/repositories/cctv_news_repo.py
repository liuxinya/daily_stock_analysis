# -*- coding: utf-8 -*-
"""CCTV《新闻联播》文字稿与 AI 分析结果的数据访问层。"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import and_, delete, desc, func, or_, select
from sqlalchemy.exc import IntegrityError

from src.storage import (
    CctvNewsAnalysis,
    CctvNewsDay,
    CctvNewsItem,
    DatabaseManager,
)

logger = logging.getLogger(__name__)

MAX_PAGE_SIZE = 100
MAX_ANALYSIS_ITEMS = 2000


class CctvNewsRepository:
    """DB access layer for CCTV Xinwen Lianbo transcripts and analyses."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    # === 文字稿写入 ===

    def upsert_items(self, items: Iterable[Dict[str, Any]]) -> Tuple[int, int]:
        """按 ``url`` 幂等写入文字稿，返回 ``(新增数, 更新数)``。"""
        created = 0
        updated = 0
        with self.db.get_session() as session:
            for fields in items:
                url = (fields.get("url") or "").strip()
                title = (fields.get("title") or "").strip()
                content = fields.get("content") or ""
                if not url or not title or not content:
                    continue

                row_fields = dict(fields)
                row_fields["url"] = url
                row_fields["title"] = title[:300]
                row_fields.setdefault("char_count", len(content))
                summary = row_fields.get("summary")
                if summary:
                    row_fields["summary"] = summary[:400]

                existing = session.execute(
                    select(CctvNewsItem).where(CctvNewsItem.url == url).limit(1)
                ).scalar_one_or_none()
                if existing is not None:
                    existing.title = row_fields["title"]
                    existing.content = content
                    existing.summary = row_fields.get("summary") or existing.summary
                    existing.seq = row_fields.get("seq", existing.seq)
                    existing.char_count = row_fields["char_count"]
                    existing.news_date = row_fields.get("news_date", existing.news_date)
                    existing.updated_at = datetime.now()
                    updated += 1
                    continue

                try:
                    with session.begin_nested():
                        session.add(CctvNewsItem(**row_fields))
                        session.flush()
                    created += 1
                except IntegrityError:
                    continue
            session.commit()
        return created, updated

    def record_day(
        self,
        news_date: date,
        *,
        item_count: int,
        listed_count: int,
        failed_count: int,
        skipped_count: int,
        is_complete: bool,
    ) -> None:
        """写入/更新单日抓取状态。"""
        with self.db.get_session() as session:
            existing = session.execute(
                select(CctvNewsDay).where(CctvNewsDay.news_date == news_date).limit(1)
            ).scalar_one_or_none()
            now = datetime.now()
            if existing is None:
                session.add(
                    CctvNewsDay(
                        news_date=news_date,
                        item_count=item_count,
                        listed_count=listed_count,
                        failed_count=failed_count,
                        skipped_count=skipped_count,
                        is_complete=is_complete,
                        last_synced_at=now,
                    )
                )
            else:
                existing.item_count = item_count
                existing.listed_count = listed_count
                existing.failed_count = failed_count
                existing.skipped_count = skipped_count
                existing.is_complete = is_complete
                existing.last_synced_at = now
            session.commit()

    # === 文字稿查询 ===

    def get_day_status(self, news_date: date) -> Optional[CctvNewsDay]:
        with self.db.get_session() as session:
            return session.execute(
                select(CctvNewsDay).where(CctvNewsDay.news_date == news_date).limit(1)
            ).scalar_one_or_none()

    def list_complete_dates(self, start_date: date, end_date: date) -> List[date]:
        """返回窗口内已完整抓取的日期，用于跳过重复同步。"""
        with self.db.get_session() as session:
            rows = session.execute(
                select(CctvNewsDay.news_date).where(
                    and_(
                        CctvNewsDay.news_date >= start_date,
                        CctvNewsDay.news_date <= end_date,
                        CctvNewsDay.is_complete.is_(True),
                    )
                )
            ).scalars().all()
        return list(rows)

    def list_day_summaries(
        self,
        start_date: date,
        end_date: date,
    ) -> List[Dict[str, Any]]:
        """按日期聚合窗口内的条数与字数，倒序返回。"""
        with self.db.get_session() as session:
            rows = session.execute(
                select(
                    CctvNewsItem.news_date,
                    func.count(CctvNewsItem.id),
                    func.coalesce(func.sum(CctvNewsItem.char_count), 0),
                )
                .where(
                    and_(
                        CctvNewsItem.news_date >= start_date,
                        CctvNewsItem.news_date <= end_date,
                    )
                )
                .group_by(CctvNewsItem.news_date)
                .order_by(desc(CctvNewsItem.news_date))
            ).all()
        return [
            {
                "news_date": row[0],
                "item_count": int(row[1] or 0),
                "char_count": int(row[2] or 0),
            }
            for row in rows
        ]

    def list_items(
        self,
        *,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        query: Optional[str] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> Tuple[List[CctvNewsItem], int]:
        """分页查询文字稿，按播出日期倒序、当天顺序正序。"""
        conditions = []
        if start_date is not None:
            conditions.append(CctvNewsItem.news_date >= start_date)
        if end_date is not None:
            conditions.append(CctvNewsItem.news_date <= end_date)
        if query and query.strip():
            pattern = f"%{query.strip()}%"
            conditions.append(
                or_(CctvNewsItem.title.like(pattern), CctvNewsItem.content.like(pattern))
            )
        where_clause = and_(*conditions) if conditions else True

        safe_page = max(1, int(page))
        safe_size = max(1, min(int(page_size), MAX_PAGE_SIZE))

        with self.db.get_session() as session:
            total = session.execute(
                select(func.count(CctvNewsItem.id))
                .select_from(CctvNewsItem)
                .where(where_clause)
            ).scalar() or 0
            rows = session.execute(
                select(CctvNewsItem)
                .where(where_clause)
                .order_by(desc(CctvNewsItem.news_date), CctvNewsItem.seq, CctvNewsItem.id)
                .offset((safe_page - 1) * safe_size)
                .limit(safe_size)
            ).scalars().all()
        return list(rows), int(total)

    def list_items_for_window(
        self,
        start_date: date,
        end_date: date,
        *,
        limit: int = MAX_ANALYSIS_ITEMS,
    ) -> List[CctvNewsItem]:
        """取窗口内全部文字稿（不分页），按时间正序，供 AI 分析使用。"""
        safe_limit = max(1, min(int(limit), MAX_ANALYSIS_ITEMS))
        with self.db.get_session() as session:
            rows = session.execute(
                select(CctvNewsItem)
                .where(
                    and_(
                        CctvNewsItem.news_date >= start_date,
                        CctvNewsItem.news_date <= end_date,
                    )
                )
                .order_by(CctvNewsItem.news_date, CctvNewsItem.seq, CctvNewsItem.id)
                .limit(safe_limit)
            ).scalars().all()
        return list(rows)

    def get_item(self, item_id: int) -> Optional[CctvNewsItem]:
        with self.db.get_session() as session:
            return session.execute(
                select(CctvNewsItem).where(CctvNewsItem.id == int(item_id)).limit(1)
            ).scalar_one_or_none()

    def latest_news_date(self) -> Optional[date]:
        with self.db.get_session() as session:
            return session.execute(select(func.max(CctvNewsItem.news_date))).scalar()

    # === AI 分析结果 ===

    def save_analysis(self, fields: Dict[str, Any]) -> CctvNewsAnalysis:
        row = CctvNewsAnalysis(**fields)
        with self.db.get_session() as session:
            session.add(row)
            session.commit()
            session.refresh(row)
        return row

    def find_analysis_by_signature(self, signature: str) -> Optional[CctvNewsAnalysis]:
        """按证据指纹命中已有分析，避免同一批新闻重复调用 LLM。"""
        normalized = (signature or "").strip()
        if not normalized:
            return None
        with self.db.get_session() as session:
            return session.execute(
                select(CctvNewsAnalysis)
                .where(CctvNewsAnalysis.evidence_signature == normalized)
                .order_by(desc(CctvNewsAnalysis.created_at), desc(CctvNewsAnalysis.id))
                .limit(1)
            ).scalar_one_or_none()

    def latest_analysis(self, *, range_days: Optional[int] = None) -> Optional[CctvNewsAnalysis]:
        conditions = []
        if range_days is not None:
            conditions.append(CctvNewsAnalysis.range_days == int(range_days))
        where_clause = and_(*conditions) if conditions else True
        with self.db.get_session() as session:
            return session.execute(
                select(CctvNewsAnalysis)
                .where(where_clause)
                .order_by(desc(CctvNewsAnalysis.created_at), desc(CctvNewsAnalysis.id))
                .limit(1)
            ).scalar_one_or_none()

    # === 保留期清理 ===

    def apply_retention(self, retention_days: int) -> Tuple[int, int, int]:
        """删除超过保留期的文字稿、日状态与分析结果，返回各自删除条数。"""
        cutoff = (datetime.now() - timedelta(days=max(1, int(retention_days)))).date()
        with self.db.get_session() as session:
            items = session.execute(
                delete(CctvNewsItem).where(CctvNewsItem.news_date < cutoff)
            )
            days = session.execute(
                delete(CctvNewsDay).where(CctvNewsDay.news_date < cutoff)
            )
            analyses = session.execute(
                delete(CctvNewsAnalysis).where(CctvNewsAnalysis.end_date < cutoff)
            )
            session.commit()
            return (
                int(items.rowcount or 0),
                int(days.rowcount or 0),
                int(analyses.rowcount or 0),
            )
