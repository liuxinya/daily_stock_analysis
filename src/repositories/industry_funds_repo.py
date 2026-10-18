# -*- coding: utf-8 -*-
"""行业资金快照的数据访问层。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, delete, desc, select

from src.storage import DatabaseManager, IndustryFundsSnapshot

logger = logging.getLogger(__name__)


class IndustryFundsRepository:
    """行业资金快照的读写。

    所有读取都按 ``trade_date`` + ``created_at`` 倒序取第一条 —— 同一天可能刷新多次
    （手动点、定时任务），后写的覆盖先写的语义由 ``save_snapshot`` 的去重逻辑保证，
    读侧只需取最新。
    """

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    # ------------------------------------------------------------------ 写

    def save_snapshot(self, fields: Dict[str, Any]) -> IndustryFundsSnapshot:
        """落库一份快照。同一 ``trade_date`` 已有记录时**覆盖**而不是追加。

        按交易日去重是必需的：周末连着点刷新会攒出一堆 ``trade_date`` 相同但
        ``etf_delta`` 略有差异的记录，把历史分位的样本带偏。
        """
        trade_date = str(fields.get("trade_date") or "").strip()
        with self.db.get_session() as session:
            existing = None
            if trade_date:
                existing = session.execute(
                    select(IndustryFundsSnapshot)
                    .where(IndustryFundsSnapshot.trade_date == trade_date)
                    .order_by(desc(IndustryFundsSnapshot.created_at), desc(IndustryFundsSnapshot.id))
                    .limit(1)
                ).scalar_one_or_none()

            if existing is not None:
                for key, value in fields.items():
                    if hasattr(existing, key):
                        setattr(existing, key, value)
                session.commit()
                session.refresh(existing)
                return existing

            row = IndustryFundsSnapshot(**fields)
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    # ------------------------------------------------------------------ 读

    def latest_snapshot(self) -> Optional[IndustryFundsSnapshot]:
        with self.db.get_session() as session:
            return session.execute(
                select(IndustryFundsSnapshot)
                .order_by(desc(IndustryFundsSnapshot.created_at), desc(IndustryFundsSnapshot.id))
                .limit(1)
            ).scalar_one_or_none()

    def snapshot_for_trade_date(self, trade_date: str) -> Optional[IndustryFundsSnapshot]:
        normalized = str(trade_date or "").strip()
        if not normalized:
            return None
        with self.db.get_session() as session:
            return session.execute(
                select(IndustryFundsSnapshot)
                .where(IndustryFundsSnapshot.trade_date == normalized)
                .order_by(desc(IndustryFundsSnapshot.created_at), desc(IndustryFundsSnapshot.id))
                .limit(1)
            ).scalar_one_or_none()

    def etf_delta_history(self, *, limit: int = 250) -> List[float]:
        """按交易日正序返回历史 ``etf_delta``，供 ETF 份额因子算历史分位。

        只取非空值。样本不足时打分侧会自动退回固定标尺（见 ``scoring._score_etf_share``），
        所以这里不需要凑数，拿多少算多少。
        """
        with self.db.get_session() as session:
            rows = session.execute(
                select(IndustryFundsSnapshot.etf_delta, IndustryFundsSnapshot.trade_date)
                .where(IndustryFundsSnapshot.etf_delta.is_not(None))
                .order_by(desc(IndustryFundsSnapshot.trade_date))
                .limit(max(1, int(limit)))
            ).all()
        ordered = sorted(
            (row for row in rows if row[0] is not None),
            key=lambda item: str(item[1] or ""),
        )
        return [float(row[0]) for row in ordered]

    def history(self, *, limit: int = 60) -> List[IndustryFundsSnapshot]:
        """最近若干份快照，按交易日倒序。"""
        with self.db.get_session() as session:
            return list(
                session.execute(
                    select(IndustryFundsSnapshot)
                    .order_by(desc(IndustryFundsSnapshot.trade_date), desc(IndustryFundsSnapshot.id))
                    .limit(max(1, int(limit)))
                ).scalars().all()
            )

    # ------------------------------------------------------------------ 维护

    def apply_retention(self, retention_days: int) -> int:
        cutoff = datetime.now() - timedelta(days=max(7, int(retention_days)))
        with self.db.get_session() as session:
            deleted = session.execute(
                delete(IndustryFundsSnapshot).where(
                    and_(IndustryFundsSnapshot.created_at < cutoff)
                )
            )
            session.commit()
            return int(deleted.rowcount or 0)


__all__ = ["IndustryFundsRepository"]
