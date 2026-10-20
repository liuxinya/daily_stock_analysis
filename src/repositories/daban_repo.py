# -*- coding: utf-8 -*-
"""「我是打板王」快照 / 池子 / 推荐的数据访问层。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import and_, delete, desc, select

from src.storage import DabanForecast, DabanLiveHit, DabanPick, DabanPoolRow, DabanSnapshot, DatabaseManager

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

    def latest_snapshot_before(self, trade_date: str, *, kind: Optional[str] = "full") -> Optional[DabanSnapshot]:
        """严格早于 ``trade_date`` 的最近一份快照（盘中判断今日门控只能用它，避免用到今天自己的结论）。"""
        with self.db.get_session() as session:
            query = select(DabanSnapshot).where(DabanSnapshot.trade_date < str(trade_date or ""))
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

    def pool_history_since(self, since: str, pool: str = "zt") -> List[tuple]:
        """``since`` 之后所有日期的池子行，``[(trade_date, rows), ...]`` 按日期正序。"""
        with self.db.get_session() as session:
            rows = session.execute(
                select(DabanPoolRow).where(and_(DabanPoolRow.pool == pool, DabanPoolRow.trade_date >= since))
            ).scalars().all()
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(row.trade_date, []).append({key: getattr(row, key) for key in _POOL_FIELDS})
        return [(d, grouped[d]) for d in sorted(grouped)]

    def reason_memory(self, since: str, pool: str = "zt", *, until: Optional[str] = None) -> List[tuple]:
        """``[since, until)`` 区间的 ``(code, reason)`` 去重列表。只取两列，比整行 ORM 快一个量级。

        ``until`` 不含当天：回看历史某天时，不能用到那天之后的涨停原因（前视）。
        """
        conditions = [DabanPoolRow.pool == pool, DabanPoolRow.trade_date >= since]
        if until:
            conditions.append(DabanPoolRow.trade_date < until)
        with self.db.get_session() as session:
            rows = session.execute(
                select(DabanPoolRow.code, DabanPoolRow.reason).where(and_(*conditions)).distinct()
            ).all()
        return [(row[0], row[1]) for row in rows if row[0] and row[1]]

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

    # ------------------------------------------------------------------ 盘中雷达 / 首板预判

    def upsert_live_hits(self, trade_date: str, hits: List[Dict[str, Any]], *, final: bool) -> List[Dict[str, Any]]:
        """按 (style, code) 去重写入，返回本次**新插入**的行。

        - 已有的行不改动作、仓位、名额序号、首次出现时刻——那是用户当时看到的东西；
        - 同一派同一名额序号只能属于一只票（多进程同时分配时的最后一道防线）；
        - ``final=True``（收盘）时，给已有行写入是否封到收盘、开板次数。
        """
        inserted: List[Dict[str, Any]] = []
        with self.db.get_session() as session:
            existing = {
                (r.style, r.code): r for r in
                session.execute(select(DabanLiveHit).where(DabanLiveHit.trade_date == trade_date)).scalars().all()
            }
            taken = {(r.style, r.slot) for r in existing.values() if r.slot}
            for hit in hits:
                key = (hit.get("style"), hit.get("code"))
                row = existing.get(key)
                if row is None:
                    slot_key = (hit.get("style"), hit.get("slot"))
                    if hit.get("slot") and slot_key in taken:
                        # 名额已被另一只票锁定（两个进程同时分配）：不覆盖，下一轮按已锁定名额重新分配。
                        logger.warning("打板王：%s %s 的名额 %s 已被占用，跳过留档", trade_date, key, hit.get("slot"))
                        continue
                    record = DabanLiveHit(trade_date=trade_date, **hit)
                    session.add(record)
                    existing[key] = record
                    if hit.get("slot"):
                        taken.add(slot_key)
                    inserted.append(hit)
                elif final:
                    if hit.get("sealed_close") is not None:
                        row.sealed_close = hit.get("sealed_close")
                    if hit.get("open_count") is not None:
                        row.open_count = hit.get("open_count")
            session.commit()
        return inserted

    def pending_live_dates(self) -> List[str]:
        """还有未回填标的的交易日，按时间正序。"""
        with self.db.get_session() as session:
            rows = session.execute(
                select(DabanLiveHit.trade_date).where(DabanLiveHit.reviewed_at.is_(None)).distinct()
            ).all()
        return sorted(row[0] for row in rows)

    def live_hits(self, trade_date: str) -> List[Dict[str, Any]]:
        with self.db.get_session() as session:
            rows = session.execute(select(DabanLiveHit).where(DabanLiveHit.trade_date == trade_date)).scalars().all()
            return [_live_dict(row) for row in rows]

    def update_live_outcomes(self, trade_date: str, outcomes: Dict[str, Dict[str, Any]]) -> int:
        updated = 0
        with self.db.get_session() as session:
            rows = session.execute(select(DabanLiveHit).where(DabanLiveHit.trade_date == trade_date)).scalars().all()
            for row in rows:
                outcome = outcomes.get(row.code)
                if outcome is None:
                    continue
                row.next_open_ret = outcome.get("open_ret")
                row.next_close_ret = outcome.get("close_ret")
                row.reviewed_at = datetime.now()
                updated += 1
            session.commit()
        return updated

    def reviewed_live_hits(self, *, limit_days: int = 20, before: Optional[str] = None) -> List[Dict[str, Any]]:
        with self.db.get_session() as session:
            query = select(DabanLiveHit.trade_date).where(DabanLiveHit.reviewed_at.is_not(None))
            if before:
                query = query.where(DabanLiveHit.trade_date < before)
            dates = [r[0] for r in session.execute(
                query.distinct().order_by(desc(DabanLiveHit.trade_date)).limit(max(1, int(limit_days)))
            ).all()]
            if not dates:
                return []
            rows = session.execute(select(DabanLiveHit).where(
                and_(DabanLiveHit.trade_date.in_(dates), DabanLiveHit.reviewed_at.is_not(None))
            )).scalars().all()
            return [_live_dict(row) for row in rows]

    def save_forecasts(self, made_on: str, target_date: str, rows: List[Dict[str, Any]]) -> None:
        with self.db.get_session() as session:
            session.execute(delete(DabanForecast).where(DabanForecast.made_on == made_on))
            for row in rows:
                session.add(DabanForecast(made_on=made_on, target_date=target_date, **row))
            session.commit()

    def forecasts_for(self, target_date: str) -> List[Dict[str, Any]]:
        with self.db.get_session() as session:
            rows = session.execute(select(DabanForecast).where(DabanForecast.target_date == target_date)).scalars().all()
            return [_forecast_dict(row) for row in rows]

    def evaluate_forecasts(self, target_date: str, touched: set, sealed: set) -> int:
        updated = 0
        with self.db.get_session() as session:
            rows = session.execute(select(DabanForecast).where(DabanForecast.target_date == target_date)).scalars().all()
            for row in rows:
                row.touched = row.code in touched or row.code in sealed
                row.sealed = row.code in sealed
                row.evaluated_at = datetime.now()
                updated += 1
            session.commit()
        return updated

    def evaluated_forecasts(self, *, limit_days: int = 60) -> List[Dict[str, Any]]:
        with self.db.get_session() as session:
            dates = [r[0] for r in session.execute(
                select(DabanForecast.target_date).where(DabanForecast.evaluated_at.is_not(None))
                .distinct().order_by(desc(DabanForecast.target_date)).limit(max(1, int(limit_days)))
            ).all()]
            if not dates:
                return []
            rows = session.execute(select(DabanForecast).where(
                and_(DabanForecast.target_date.in_(dates), DabanForecast.evaluated_at.is_not(None))
            )).scalars().all()
            return [_forecast_dict(row) for row in rows]

    # ------------------------------------------------------------------ 维护

    def apply_retention(self, retention_days: int) -> int:
        cutoff = (datetime.now() - timedelta(days=max(30, int(retention_days)))).strftime("%Y-%m-%d")
        deleted = 0
        with self.db.get_session() as session:
            for model in (DabanSnapshot, DabanPoolRow, DabanPick, DabanLiveHit):
                result = session.execute(delete(model).where(model.trade_date < cutoff))
                deleted += int(result.rowcount or 0)
            result = session.execute(delete(DabanForecast).where(DabanForecast.made_on < cutoff))
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


def _live_dict(row: DabanLiveHit) -> Dict[str, Any]:
    return {
        "trade_date": row.trade_date, "style": row.style, "code": row.code, "name": row.name,
        "action": row.action, "first_time": row.first_time, "seen_at": row.seen_at, "limit_price": row.limit_price,
        "position_pct": row.position_pct, "slot": row.slot, "sealed_close": row.sealed_close,
        "open_count": row.open_count,
        "theme": row.theme, "phase": row.phase, "origin": row.origin,
        "next_open_ret": row.next_open_ret, "next_close_ret": row.next_close_ret,
        "reviewed": row.reviewed_at is not None,
    }


def _forecast_dict(row: DabanForecast) -> Dict[str, Any]:
    return {
        "made_on": row.made_on, "target_date": row.target_date, "code": row.code, "name": row.name,
        "theme": row.theme, "source": row.source, "touched": row.touched, "sealed": row.sealed,
        "evaluated": row.evaluated_at is not None,
    }


def decode_payload(row) -> Optional[Dict[str, Any]]:
    """快照行 → payload dict；解析失败按无缓存处理。"""
    if row is None:
        return None
    try:
        payload = json.loads(row.payload or "{}")
    except (TypeError, ValueError):
        logger.warning("打板王：快照 %s 的 payload 无法解析", getattr(row, "id", "?"))
        return None
    return payload if isinstance(payload, dict) else None


__all__ = ["DabanRepository", "decode_payload"]
