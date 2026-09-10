# -*- coding: utf-8 -*-
"""消息面汇总结果的数据访问层。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from sqlalchemy import and_, delete, desc, select

from src.storage import DatabaseManager, MarketBriefAnalysis

logger = logging.getLogger(__name__)


class MarketBriefRepository:
    """DB access layer for market brief analyses."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    def save_analysis(self, fields: Dict[str, Any]) -> MarketBriefAnalysis:
        row = MarketBriefAnalysis(**fields)
        with self.db.get_session() as session:
            session.add(row)
            session.commit()
            session.refresh(row)
        return row

    def find_analysis_by_signature(
        self, signature: str, *, created_after: Optional[datetime] = None
    ) -> Optional[MarketBriefAnalysis]:
        """按证据指纹命中已有汇总，避免同一批消息重复调用 LLM。"""
        normalized = (signature or "").strip()
        if not normalized:
            return None
        conditions = [MarketBriefAnalysis.evidence_signature == normalized]
        if created_after is not None:
            conditions.append(MarketBriefAnalysis.created_at >= created_after)
        with self.db.get_session() as session:
            return session.execute(
                select(MarketBriefAnalysis)
                .where(and_(*conditions))
                .order_by(desc(MarketBriefAnalysis.created_at), desc(MarketBriefAnalysis.id))
                .limit(1)
            ).scalar_one_or_none()

    def latest_analysis(self, *, mode: Optional[str] = None) -> Optional[MarketBriefAnalysis]:
        conditions = []
        if mode:
            conditions.append(MarketBriefAnalysis.mode == str(mode))
        where_clause = and_(*conditions) if conditions else True
        with self.db.get_session() as session:
            return session.execute(
                select(MarketBriefAnalysis)
                .where(where_clause)
                .order_by(desc(MarketBriefAnalysis.created_at), desc(MarketBriefAnalysis.id))
                .limit(1)
            ).scalar_one_or_none()

    def find_for_window(self, *, mode: str, window_start: datetime) -> Optional[MarketBriefAnalysis]:
        """这个模式的这个窗口是否已经出过稿。

        定时生成任务靠它判重，因此不需要另存调度状态；``window_start`` 由交易日历
        决定（例如盘前窗口的起点就是上一个交易日收盘），同一窗口内重复触发会命中。
        """
        with self.db.get_session() as session:
            return session.execute(
                select(MarketBriefAnalysis)
                .where(and_(
                    MarketBriefAnalysis.mode == str(mode),
                    MarketBriefAnalysis.window_start == window_start,
                ))
                .order_by(desc(MarketBriefAnalysis.created_at), desc(MarketBriefAnalysis.id))
                .limit(1)
            ).scalar_one_or_none()

    def latest_created_at(self, *, mode: str) -> Optional[datetime]:
        row = self.latest_analysis(mode=mode)
        return row.created_at if row is not None else None

    def apply_retention(self, retention_days: int) -> int:
        cutoff = datetime.now() - timedelta(days=max(1, int(retention_days)))
        with self.db.get_session() as session:
            deleted = session.execute(
                delete(MarketBriefAnalysis).where(MarketBriefAnalysis.created_at < cutoff)
            )
            session.commit()
            return int(deleted.rowcount or 0)
