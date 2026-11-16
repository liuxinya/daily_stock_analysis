# -*- coding: utf-8 -*-
"""「我是打板王」快照 / 池子 / 推荐的数据访问层。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import and_, delete, desc, or_, select

from src.storage import (
    DabanForecast,
    DabanLiveHit,
    DabanPick,
    DabanPoolRow,
    DabanRadarEvent,
    DabanRecommendation,
    DabanSnapshot,
    DatabaseManager,
)

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
        """落一份快照。收盘后的完整快照不会被同日的回补记录覆盖；只在盘中生成过的那份可以——
        它的指标是生成那一刻的，回补拿到的是收盘数据（回补只处理今天之前的日期）。"""
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
                if existing.kind == "full" and not existing.intraday and kind == "backfill":
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

    def known_trade_dates(self, dates: Iterable[str], *, closed_only: bool = False) -> set:
        """库里有快照的日期。``closed_only=True`` 时不算「只在盘中生成过」的那些天：那份快照的指标是
        生成那一刻的，池子也没落库，冷启动回补要把它当作缺失。"""
        wanted = [str(d) for d in dates]
        if not wanted:
            return set()
        with self.db.get_session() as session:
            rows = session.execute(
                select(DabanSnapshot.trade_date, DabanSnapshot.kind, DabanSnapshot.intraday)
                .where(DabanSnapshot.trade_date.in_(wanted))
            ).all()
        return {row[0] for row in rows if not (closed_only and row[1] == "full" and row[2])}

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
        - ``final=True``（收盘）时，给已有行写入是否封到收盘、开板次数，并补上盘中还不知道的真实首封时间。
        """
        inserted: List[Dict[str, Any]] = []
        with self.db.get_session() as session:
            existing = {
                (r.style, r.code): r for r in
                session.execute(select(DabanLiveHit).where(DabanLiveHit.trade_date == trade_date)).scalars().all()
            }
            # 只有「有时间锚点」的行才占名额：竞价期间写下的空时间行不占位（与读取口径一致）。名额给的是
            # 还没涨停的候选，那时只有添加时间，所以 found_at 与 first_time 任一有效即算。
            taken = {(r.style, r.slot) for r in existing.values()
                     if r.slot and (_valid_hhmmss(r.first_time) or _valid_hhmmss(r.found_at))}
            for hit in hits:
                key = (hit.get("style"), hit.get("code"))
                row = existing.get(key)
                if row is None:
                    if hit.get("origin") == "live" and not hit.get("slot"):
                        # 正式名额没带序号（旧调用 / 外部写入）：按该打法当天已有的最大序号接上。
                        used = [slot for style, slot in taken if style == hit.get("style")]
                        hit = {**hit, "slot": (max(used) if used else 0) + 1}
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
                elif hit.get("origin") == "live" and row.origin == "preboard":
                    # 之前作为「未涨停候选」留过档，现在锁定它：升级为正式名额，保留首次出现时刻。
                    row.action = hit.get("action") or row.action
                    row.position_pct = hit.get("position_pct")
                    row.slot = hit.get("slot")
                    row.origin = "live"
                    row.seen_at = hit.get("seen_at") or row.seen_at
                    row.found_at = row.found_at or hit.get("found_at")
                    row.gap_pct = hit.get("gap_pct")
                elif final:
                    # 盘中锁定时它还没封板，首封时间那时为空：收盘知道了就补上，不覆盖已有的。
                    if not _valid_hhmmss(row.first_time) and _valid_hhmmss(hit.get("first_time")):
                        row.first_time = hit.get("first_time")
                    if hit.get("sealed_close") is not None:
                        row.sealed_close = hit.get("sealed_close")
                    if hit.get("open_count") is not None:
                        row.open_count = hit.get("open_count")
            session.commit()
        return inserted

    def mark_preboard_seals(self, trade_date: str, sealed_codes: set) -> int:
        """收盘时给未涨停候选写「最终有没有封住」。"""
        updated = 0
        with self.db.get_session() as session:
            rows = session.execute(
                select(DabanLiveHit).where(and_(DabanLiveHit.trade_date == trade_date,
                                                DabanLiveHit.origin == "preboard"))
            ).scalars().all()
            for row in rows:
                row.sealed_close = row.code in sealed_codes
                updated += 1
            session.commit()
        return updated

    def reconcile_live_hits(self, trade_date: str, *, zt: List[Dict[str, Any]], zb: List[Dict[str, Any]]) -> int:
        """按当天收盘的涨停 / 炸板池，给盘中留档的行补「是否封到收盘」、开板次数和真实首封时间。

        用在「盘中留过档、收盘后没生成复盘」的那些天（冷启动回补时）。只填空着的字段，不改已经对过账的行；
        不新增行。没出现在涨停池里的一律记为没封住（炸板或根本没触板）。
        """
        sealed = {str(r.get("code") or ""): r for r in zt or []}
        broken = {str(r.get("code") or ""): r for r in zb or []}
        updated = 0
        with self.db.get_session() as session:
            rows = session.execute(select(DabanLiveHit).where(DabanLiveHit.trade_date == trade_date)).scalars().all()
            for row in rows:
                if row.sealed_close is not None:
                    continue
                source = sealed.get(row.code) or broken.get(row.code)
                row.sealed_close = row.code in sealed
                if row.origin != "preboard" and source is not None:
                    if not _valid_hhmmss(row.first_time) and _valid_hhmmss(source.get("first_time")):
                        row.first_time = str(source.get("first_time"))
                    if row.open_count is None and source.get("open_num") is not None:
                        row.open_count = int(source.get("open_num") or 0)
                updated += 1
            session.commit()
        return updated

    def reconcile_stored_live_hits(self, *, before: str) -> List[str]:
        """``before`` 之前、留档里还有「是否封到收盘」为空的交易日，用库里存的涨停 / 炸板池补上。

        给那些池子已经落库（冷启动回补过）、但盘中留档当时没对账的日子用。只读本地库，不访问上游。
        返回补过的交易日。
        """
        with self.db.get_session() as session:
            dates = [row[0] for row in session.execute(
                select(DabanLiveHit.trade_date)
                .where(and_(DabanLiveHit.trade_date < before, DabanLiveHit.sealed_close.is_(None)))
                .distinct()
            ).all()]
        done: List[str] = []
        for day in sorted(dates):
            zt = self.pool_rows(day, "zt")
            if not zt:
                continue  # 池子没落库：等冷启动回补那天从上游取
            self.reconcile_live_hits(day, zt=zt, zb=self.pool_rows(day, "zb"))
            done.append(day)
        return done

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

    def update_live_outcomes(self, trade_date: str, outcomes: Dict[tuple, Dict[str, Any]]) -> int:
        """``outcomes`` 以 ``(style, code)`` 为键：两派锁定同一只票时成本不同，各回各的。"""
        updated = 0
        with self.db.get_session() as session:
            rows = session.execute(select(DabanLiveHit).where(DabanLiveHit.trade_date == trade_date)).scalars().all()
            for row in rows:
                outcome = outcomes.get((row.style, row.code))
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

    # ------------------------------------------------------------------ 雷达时序留档

    def add_radar_events(self, trade_date: str, events: List[Dict[str, Any]]) -> int:
        """追加一批雷达事件（只增不改）。``data`` 是 dict，存成 JSON。"""
        if not events:
            return 0
        with self.db.get_session() as session:
            for event in events:
                session.add(DabanRadarEvent(
                    trade_date=trade_date,
                    at=str(event.get("at") or ""),
                    kind=str(event.get("kind") or ""),
                    code=str(event.get("code") or ""),
                    name=(str(event.get("name") or "")[:64] or None),
                    theme=(str(event.get("theme") or "")[:64] or None),
                    price=event.get("price"),
                    gap_pct=event.get("gap_pct"),
                    data=json.dumps(event.get("data") or {}, ensure_ascii=False),
                ))
            session.commit()
        return len(events)

    def radar_events(self, trade_date: str, *, code: Optional[str] = None,
                     kinds: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
        conditions = [DabanRadarEvent.trade_date == trade_date]
        if code is not None:
            conditions.append(DabanRadarEvent.code == code)
        if kinds:
            conditions.append(DabanRadarEvent.kind.in_(list(kinds)))
        with self.db.get_session() as session:
            rows = session.execute(
                select(DabanRadarEvent).where(and_(*conditions)).order_by(DabanRadarEvent.id)
            ).scalars().all()
            return [_radar_dict(row) for row in rows]

    def radar_state(self, trade_date: str) -> Dict[str, Any]:
        """当天已经留过的状态，进程重启后接着记：不重复写 ``seen``，状态变化和采样节奏也接得上。

        返回 ``{"tracked": {code: {"status"}}, "cold": set, "sample_last", "theme_last", "market_last"}``，
        与 ``src.services.daban.radar_log`` 的进程内状态同构。
        """
        with self.db.get_session() as session:
            rows = session.execute(
                select(DabanRadarEvent.kind, DabanRadarEvent.code, DabanRadarEvent.at, DabanRadarEvent.data)
                .where(DabanRadarEvent.trade_date == trade_date)
                .order_by(DabanRadarEvent.id)
            ).all()
        tracked: Dict[str, Dict[str, Any]] = {}
        cold: set = set()
        last = {"sample": "", "theme": "", "market": "", "auction": ""}
        for kind, code, at, data in rows:
            if kind in last:
                last[kind] = max(last[kind], at or "")
                if kind != "sample":
                    continue
            if kind not in ("seen", "seal", "reseal", "open"):
                continue
            if kind == "seen":
                try:
                    payload = json.loads(data or "{}")
                except (TypeError, ValueError):
                    payload = {}
                if payload.get("hot"):
                    tracked[code] = {"status": payload.get("status") or "candidate"}
                else:
                    cold.add(code)
            elif code in tracked and kind in ("seal", "reseal"):
                tracked[code]["status"] = "sealed"
            elif code in tracked and kind == "open":
                tracked[code]["status"] = "opened"
        return {"tracked": tracked, "cold": cold, "sample_last": last["sample"],
                "theme_last": last["theme"], "market_last": last["market"], "auction_last": last["auction"]}

    # ------------------------------------------------------------------ 「重点」推荐

    def add_recommendations(self, trade_date: str, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """新进入「重点」的票。同一天同一只已有就跳过（一只票一天只记一次，第一次的成本和初始分不改）。返回真正写入的行。"""
        if not rows:
            return []
        inserted: List[Dict[str, Any]] = []
        with self.db.get_session() as session:
            existing = {r[0] for r in session.execute(
                select(DabanRecommendation.code).where(DabanRecommendation.trade_date == trade_date)).all()}
            for row in rows:
                code = str(row.get("code") or "")
                if not code or code in existing:
                    continue
                record = {key: row.get(key) for key in _REC_FIELDS if key in row}
                for key in ("init_detail", "live_detail", "close_detail"):
                    if isinstance(record.get(key), (dict, list)):
                        record[key] = json.dumps(record[key], ensure_ascii=False)
                session.add(DabanRecommendation(trade_date=trade_date, settle_status="pending", **record))
                existing.add(code)
                inserted.append(row)
            session.commit()
        return inserted

    def update_recommendation_live(self, trade_date: str, updates: Dict[str, Dict[str, Any]], *,
                                   close: bool = False) -> int:
        """实时分（``close=True`` 时同时写收盘分）。``updates``：``{code: {live_score, live_detail, flags?}}``。"""
        if not updates:
            return 0
        done = 0
        with self.db.get_session() as session:
            rows = session.execute(select(DabanRecommendation).where(and_(
                DabanRecommendation.trade_date == trade_date, DabanRecommendation.code.in_(list(updates))))).scalars().all()
            for row in rows:
                item = updates[row.code]
                if item.get("live_score") is not None:
                    row.live_score = int(item["live_score"])
                if item.get("live_detail") is not None:
                    row.live_detail = json.dumps(item["live_detail"], ensure_ascii=False)
                if item.get("flags") is not None:
                    row.flags = str(item["flags"])[:128]
                if close:
                    row.close_score = row.live_score
                    row.close_detail = row.live_detail
                done += 1
            session.commit()
        return done

    def close_recommendations(self, trade_date: str, *, sealed_codes: set) -> int:
        """收盘：收盘分 = 最后一次实时分；是否封到收盘按收盘涨停池写。只填空着的。"""
        done = 0
        with self.db.get_session() as session:
            rows = session.execute(select(DabanRecommendation).where(
                DabanRecommendation.trade_date == trade_date)).scalars().all()
            for row in rows:
                if row.close_score is None:
                    row.close_score = row.live_score if row.live_score is not None else row.init_score
                    row.close_detail = row.live_detail or row.init_detail
                if row.sealed_close is None:
                    row.sealed_close = row.code in sealed_codes
                done += 1
            session.commit()
        return done

    def recommendations(self, trade_date: str) -> List[Dict[str, Any]]:
        with self.db.get_session() as session:
            rows = session.execute(select(DabanRecommendation).where(DabanRecommendation.trade_date == trade_date)
                                   .order_by(DabanRecommendation.id)).scalars().all()
            return [_rec_dict(row) for row in rows]

    def pending_recommendations(self, *, before_or_on: str) -> List[Dict[str, Any]]:
        with self.db.get_session() as session:
            rows = session.execute(select(DabanRecommendation).where(and_(
                DabanRecommendation.trade_date <= before_or_on,
                or_(DabanRecommendation.settle_status.is_(None), DabanRecommendation.settle_status != "done"),
            ))).scalars().all()
            return [_rec_dict(row) for row in rows]

    def update_recommendation_outcomes(self, updates: Dict[int, Dict[str, Any]]) -> int:
        if not updates:
            return 0
        done = 0
        with self.db.get_session() as session:
            rows = session.execute(select(DabanRecommendation).where(
                DabanRecommendation.id.in_(list(updates)))).scalars().all()
            for row in rows:
                item = updates[row.id]
                for key in ("sealed_close", "h1_ret", "h1_exit_date", "h1_rule", "next_open_ret", "next_close_ret",
                            "settle_status"):
                    if key in item:
                        setattr(row, key, item[key])
                row.settled_at = datetime.now()
                done += 1
            session.commit()
        return done

    def recent_recommendations(self, *, limit_days: int = 20, before: Optional[str] = None) -> List[Dict[str, Any]]:
        """最近 ``limit_days`` 个有推荐的交易日（``before`` 不含）。"""
        with self.db.get_session() as session:
            query = select(DabanRecommendation.trade_date)
            if before:
                query = query.where(DabanRecommendation.trade_date < before)
            dates = [r[0] for r in session.execute(
                query.distinct().order_by(desc(DabanRecommendation.trade_date)).limit(max(1, int(limit_days)))).all()]
            if not dates:
                return []
            rows = session.execute(select(DabanRecommendation).where(
                DabanRecommendation.trade_date.in_(dates))).scalars().all()
            return [_rec_dict(row) for row in rows]

    # ------------------------------------------------------------------ 维护

    def apply_retention(self, retention_days: int) -> int:
        cutoff = (datetime.now() - timedelta(days=max(30, int(retention_days)))).strftime("%Y-%m-%d")
        deleted = 0
        with self.db.get_session() as session:
            for model in (DabanSnapshot, DabanPoolRow, DabanPick, DabanLiveHit, DabanRadarEvent, DabanRecommendation):
                result = session.execute(delete(model).where(model.trade_date < cutoff))
                deleted += int(result.rowcount or 0)
            result = session.execute(delete(DabanForecast).where(DabanForecast.made_on < cutoff))
            deleted += int(result.rowcount or 0)
            session.commit()
        return deleted


def _radar_dict(row: DabanRadarEvent) -> Dict[str, Any]:
    try:
        data = json.loads(row.data or "{}")
    except (TypeError, ValueError):
        data = {}
    return {
        "trade_date": row.trade_date, "at": row.at, "kind": row.kind, "code": row.code, "name": row.name,
        "theme": row.theme, "price": row.price, "gap_pct": row.gap_pct, "data": data,
    }


_REC_FIELDS = (
    "code", "name", "theme_key", "theme_name", "entered_at", "cost", "entry_status", "entry_gap_pct", "init_score",
    "live_score", "close_score", "group_key", "observe_key", "flags", "temper_tag", "covered", "init_detail",
    "live_detail", "close_detail",
)


def _json_or_empty(text: Optional[str]) -> Dict[str, Any]:
    try:
        value = json.loads(text or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _rec_dict(row: DabanRecommendation) -> Dict[str, Any]:
    out = {"id": row.id, "trade_date": row.trade_date}
    for key in _REC_FIELDS:
        out[key] = getattr(row, key)
    for key in ("init_detail", "live_detail", "close_detail"):
        out[key] = _json_or_empty(out[key])
    for key in ("sealed_close", "h1_ret", "h1_exit_date", "h1_rule", "next_open_ret", "next_close_ret", "settle_status"):
        out[key] = getattr(row, key)
    return out


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


def _valid_hhmmss(value: Any) -> bool:
    text = str(value or "").strip()
    return len(text) >= 4 and text.isdigit()


def _live_dict(row: DabanLiveHit) -> Dict[str, Any]:
    return {
        "trade_date": row.trade_date, "style": row.style, "code": row.code, "name": row.name,
        "action": row.action, "first_time": row.first_time, "found_at": row.found_at, "seen_at": row.seen_at,
        "limit_price": row.limit_price,
        "position_pct": row.position_pct, "slot": row.slot, "sealed_close": row.sealed_close,
        "open_count": row.open_count,
        "theme": row.theme, "phase": row.phase, "origin": row.origin, "gap_pct": row.gap_pct,
        "seen_price": row.seen_price,
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
