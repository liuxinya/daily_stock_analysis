# -*- coding: utf-8 -*-
"""「我是打板王」快照 / 池子 / 推荐的数据访问层。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import and_, delete, desc, select

from src.storage import DabanPick, DabanPoolRow, DabanSnapshot, DatabaseManager

logger = logging.getLogger(__name__)

_POOL_FIELDS = (
    "code", "name", "boards", "change_pct", "reason", "limit_up_type",
    "first_time", "last_time", "seal_amount", "circ_mv",
)


class DabanRepository:
    """所有写入都按 ``trade_date`` 覆盖：同一天重复刷新不会攒出重复记录。"""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    # ------------------------------------------------------------------ 快照

    def save_snapshot(self, fields: Dict[str, Any]) -> None:
        """落一份快照。完整快照不会被同日的回补记录覆盖。"""
        trade_date = str(fields.get("trade_date") or "").strip()
        kind = str(fields.get("kind") or "full")
        with self.db.get_session() as session:
            existing = session.execute(
                select(DabanSnapshot)
                .where(DabanSnapshot.trade_date == trade_date)
                .order_by(desc(DabanSnapshot.created_at), desc(DabanSnapshot.id))
                .limit(1)
            ).scalar_one_or_none()
            if existing is not None:
                if existing.kind == "full" and kind == "backfill":
                    return
                for key, value in fields.items():
                    if hasattr(existing, key):
                        setattr(existing, key, value)
                existing.created_at = datetime.now()
            else:
                session.add(DabanSnapshot(**fields))
            session.commit()

    def latest_snapshot(self, *, kind: Optional[str] = "full") -> Optional[DabanSnapshot]:
        with self.db.get_session() as session:
            query = select(DabanSnapshot)
            if kind:
                query = query.where(DabanSnapshot.kind == kind)
            return session.execute(
                query.order_by(desc(DabanSnapshot.trade_date), desc(DabanSnapshot.created_at)).limit(1)
            ).scalar_one_or_none()

    def snapshot_for_trade_date(self, trade_date: str) -> Optional[DabanSnapshot]:
        with self.db.get_session() as session:
            return session.execute(
                select(DabanSnapshot)
                .where(DabanSnapshot.trade_date == str(trade_date or "").strip())
                .order_by(desc(DabanSnapshot.created_at), desc(DabanSnapshot.id))
                .limit(1)
            ).scalar_one_or_none()

    def history(self, *, limit: int = 10, before: Optional[str] = None) -> List[DabanSnapshot]:
        """最近若干份快照（含回补），按交易日倒序。``before`` 为不含的上界。"""
        with self.db.get_session() as session:
            query = select(DabanSnapshot)
            if before:
                query = query.where(DabanSnapshot.trade_date < before)
            return list(
                session.execute(
                    query.order_by(desc(DabanSnapshot.trade_date), desc(DabanSnapshot.id)).limit(max(1, int(limit)))
                ).scalars().all()
            )

    def known_trade_dates(self, dates: Iterable[str]) -> set:
        wanted = [str(d) for d in dates]
        if not wanted:
            return set()
        with self.db.get_session() as session:
            rows = session.execute(
                select(DabanSnapshot.trade_date).where(DabanSnapshot.trade_date.in_(wanted))
            ).all()
        return {row[0] for row in rows}

    # ------------------------------------------------------------------ 池子

    def save_pool_rows(self, trade_date: str, pool: str, rows: List[Dict[str, Any]]) -> None:
        with self.db.get_session() as session:
            session.execute(
                delete(DabanPoolRow).where(and_(DabanPoolRow.trade_date == trade_date, DabanPoolRow.pool == pool))
            )
            for row in rows:
                record = {key: row.get(key) for key in _POOL_FIELDS}
                record["reason"] = str(record.get("reason") or "")[:255]
                session.add(DabanPoolRow(trade_date=trade_date, pool=pool, **record))
            session.commit()

    def pool_rows(self, trade_date: str, pool: str = "zt") -> List[Dict[str, Any]]:
        with self.db.get_session() as session:
            rows = session.execute(
                select(DabanPoolRow).where(and_(DabanPoolRow.trade_date == trade_date, DabanPoolRow.pool == pool))
            ).scalars().all()
            return [{key: getattr(row, key) for key in _POOL_FIELDS} for row in rows]

    def pool_history(self, dates: List[str], pool: str = "zt") -> List[tuple]:
        """``[(trade_date, rows), ...]``，按日期正序，只返回库里有的日期。"""
        if not dates:
            return []
        with self.db.get_session() as session:
            rows = session.execute(
                select(DabanPoolRow).where(and_(DabanPoolRow.trade_date.in_(dates), DabanPoolRow.pool == pool))
            ).scalars().all()
            grouped: Dict[str, List[Dict[str, Any]]] = {}
            for row in rows:
                grouped.setdefault(row.trade_date, []).append({key: getattr(row, key) for key in _POOL_FIELDS})
        return [(d, grouped[d]) for d in sorted(grouped)]

    # ------------------------------------------------------------------ 推荐

    def save_picks(self, trade_date: str, picks: List[Dict[str, Any]]) -> None:
        with self.db.get_session() as session:
            session.execute(delete(DabanPick).where(DabanPick.trade_date == trade_date))
            for pick in picks:
                session.add(DabanPick(trade_date=trade_date, **pick))
            session.commit()

    def picks_for(self, trade_date: str) -> List[Dict[str, Any]]:
        with self.db.get_session() as session:
            rows = session.execute(select(DabanPick).where(DabanPick.trade_date == trade_date)).scalars().all()
            return [_pick_dict(row) for row in rows]

    def update_pick_outcomes(self, trade_date: str, outcomes: Dict[str, Dict[str, Any]]) -> None:
        if not outcomes:
            return
        with self.db.get_session() as session:
            rows = session.execute(select(DabanPick).where(DabanPick.trade_date == trade_date)).scalars().all()
            for row in rows:
                outcome = outcomes.get(row.code)
                if outcome is None:
                    continue
                row.next_day_change_pct = outcome.get("change_pct")
                row.next_day_limit_up = outcome.get("limit_up")
                row.reviewed_at = datetime.now()
            session.commit()

    def reviewed_picks(self, *, limit_days: int = 10, before: Optional[str] = None) -> List[Dict[str, Any]]:
        """最近 ``limit_days`` 个有回填结果的交易日的推荐。"""
        with self.db.get_session() as session:
            query = select(DabanPick.trade_date).where(DabanPick.next_day_change_pct.is_not(None))
            if before:
                query = query.where(DabanPick.trade_date < before)
            dates = [
                row[0] for row in session.execute(
                    query.distinct().order_by(desc(DabanPick.trade_date)).limit(max(1, int(limit_days)))
                ).all()
            ]
            if not dates:
                return []
            rows = session.execute(
                select(DabanPick).where(and_(DabanPick.trade_date.in_(dates), DabanPick.next_day_change_pct.is_not(None)))
            ).scalars().all()
            return [_pick_dict(row) for row in rows]

    # ------------------------------------------------------------------ 维护

    def apply_retention(self, retention_days: int) -> int:
        cutoff = (datetime.now() - timedelta(days=max(30, int(retention_days)))).strftime("%Y-%m-%d")
        deleted = 0
        with self.db.get_session() as session:
            for model in (DabanSnapshot, DabanPoolRow, DabanPick):
                result = session.execute(delete(model).where(model.trade_date < cutoff))
                deleted += int(result.rowcount or 0)
            session.commit()
        return deleted


def _pick_dict(row: DabanPick) -> Dict[str, Any]:
    return {
        "trade_date": row.trade_date,
        "style": row.style,
        "code": row.code,
        "name": row.name,
        "mode": row.mode,
        "action": row.action,
        "score": row.score,
        "next_day_change_pct": row.next_day_change_pct,
        "next_day_limit_up": row.next_day_limit_up,
    }


__all__ = ["DabanRepository"]
