# -*- coding: utf-8 -*-
"""盘中「重点」推荐：评分、留档、成绩回填、复盘统计。

规则（评分体系 v2 + 世界观边界，2026-11-08 定）：

- **进入「重点」** 要同时满足：① 还买得进——没封板且距涨停 ≤ 6%，或封过板后炸开、现价低于涨停价；② 属于当下的
  热点题材（够热、在升温、有新闻）；③ 没被世界观边界挡住；④ 总分 ≥ 门槛（0）。
- **一只票一天只记一次**：第一次进入那一刻的现价就是成本，初始分和各项取值当场存下，之后不改；实时分每轮重算，
  收盘时的最后一次记为收盘分。进入后不撤回：分数掉到门槛以下也留在列表里，标「已降分」，照样计成绩。
- **不设数量上限、不给仓位**。同一题材内按总分、再按距涨停排序。
- **成绩**：主口径 H1（买入当天封住的按科比 #25 不涨停当天收盘卖、涨停续拿；没封住的次日高开开盘卖、低开收盘卖），
  次日开盘卖、次日收盘卖作对照。
- **不前视**：评判推荐行为只用推荐时刻之前的数据；推荐之后发生的事（封板、炸板、新闻）在复盘里单独列为「当时不可知」。

特征计算与回测共用 ``rec_features``；日 K 在候选第一次进入视野时后台现取，取到之前不进「重点」（避免初始分残缺）。
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from statistics import mean
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from src.services.daban import rec_features as rf
from src.services.daban.kline import limit_pct
from src.services.daban.themes import normalize_tag, related_tags, split_reason

logger = logging.getLogger(__name__)

STATUS_PREBOARD = "preboard"
STATUS_OPENED = "opened"
STATUS_SEALED = "sealed"

GROUP_NORMAL = "normal"
GROUP_OBSERVE = "observe"
GROUP_LABELS = {GROUP_NORMAL: "计分推荐", GROUP_OBSERVE: "观察期（一进二）"}

FLAG_DOWNGRADED = "downgraded"
FLAG_UNCOVERED = "uncovered"
FLAG_OPENED_ENTRY = "opened_entry"
FLAG_NODE_DAY = "node_day"
FLAG_OVERRIDE = "override"
FLAG_SURGE = "surge"  # 大盘急拉推荐点触发后的窗口里进入（只是买入时机标记，成绩照常统计）
FLAG_UNVERIFIED = "unverified"  # 前一交易日周期挡住、由推荐点窗口放开（一年只有 7 天样本）

CTX_RETRY_SEC = 60.0  # 昨日上下文准备失败后，隔多久再试


# ---------------------------------------------------------------------- 日 K 取数（可注入，便于测试）


def fetch_bars_qfq(code: str, *, limit: int = 270, timeout: float = 10.0) -> List[Dict[str, Any]]:
    """特征用前复权日 K（与回测缓存同一个上游、同一种复权）。"""
    from data_provider import ths_chart_fetcher as ths

    payload = ths.fetch_kline(code, period="day", adjust="qfq", limit=limit, timeout=timeout)
    return [{k: row.get(k) for k in ("time", "open", "high", "low", "close", "volume")}
            for row in payload.get("rows") or [] if row.get("close") is not None]


def fetch_bars_raw(code: str, *, limit: int = 12, timeout: float = 10.0) -> List[Dict[str, Any]]:
    """成绩用不复权日 K：成本是当时的真实成交价，前复权会把除权日之前的价格整体下移。含合成的当日行。"""
    from src.services import chart_service

    payload = chart_service.get_kline(code, adjust="none", limit=limit, indicators=[], with_today=True)
    return list(payload.get("rows") or [])


# ---------------------------------------------------------------------- 特征底表


class FeatureBook:
    """某个交易日（``trade_date`` = 买入日 D）的推荐特征：昨日上下文 + 每只候选开盘前就能算好的部分。

    昨日上下文要 D-1 的涨停 / 炸板池、D-2 的涨停池、近 90 天的涨停原因（题材记忆）、近 10 日主线：优先读库，
    库里没有的日子从同花顺补（同花顺支持历史日期）。候选的日 K 在第一次被请求时后台取，取到之前 ``get`` 返回 None。
    """

    def __init__(
        self,
        trade_date: str,
        *,
        repository,
        rules: Dict[str, Any],
        fetch_bars: Optional[Callable[[str], List[Dict[str, Any]]]] = None,
        fetch_pool: Optional[Callable[[str, str], Optional[List[Dict[str, Any]]]]] = None,
        fetch_previous: Optional[Callable[[str], Optional[List[Dict[str, Any]]]]] = None,
        workers: Optional[int] = None,
        synchronous: bool = False,
    ):
        cfg = rules.get("feature_book") or {}
        self.trade_date = trade_date
        self.ymd = trade_date.replace("-", "")
        self.repository = repository
        self.rules = rules
        self.fetch_bars = fetch_bars or (lambda code: fetch_bars_qfq(code, limit=int(cfg.get("kline_limit", 270))))
        self.fetch_pool = fetch_pool or _fetch_pool_upstream
        self.fetch_previous = fetch_previous or _fetch_previous_upstream
        self.synchronous = synchronous
        self.ctx: Optional[Dict[str, Any]] = None
        self.ctx_error: str = ""
        self.memory: Dict[str, set] = {}
        self.bars: Dict[str, Optional[Dict[str, Any]]] = {}
        self.base: Dict[str, Dict[str, Any]] = {}
        self.failed: Dict[str, float] = {}
        self.inflight: set = set()
        self._lock = threading.RLock()
        self._ctx_started = False
        self._ctx_failed_at = 0.0
        self._executor = None if synchronous else ThreadPoolExecutor(
            max_workers=max(1, int(workers or cfg.get("workers", 6))), thread_name_prefix="daban-feat")

    # -------------------------------------------------------------- 昨日上下文

    def ensure_ctx(self) -> bool:
        """昨日上下文准备好了返回 True；没准备好时（后台）开始准备。"""
        with self._lock:
            if self.ctx is not None:
                return True
            if self._ctx_started:
                return False
            if time.time() - self._ctx_failed_at < CTX_RETRY_SEC:
                return False  # 上次失败不久：等一会再重试，不在每次快照都打一轮上游
            self._ctx_started = True
        if self.synchronous:
            self._build_ctx()
            return self.ctx is not None
        threading.Thread(target=self._build_ctx, daemon=True, name="daban-feat-ctx").start()
        return False

    def _build_ctx(self) -> None:
        from src.services.daban.fetchers import previous_trade_date, recent_trade_dates

        try:
            d1 = previous_trade_date(self.trade_date)
            d2 = previous_trade_date(d1)
            zt1 = self._pool(d1, "zt")
            if not zt1:
                raise RuntimeError(f"{d1} 的涨停池为空（库里没有、上游也没取到）")
            zb1 = self._pool(d1, "zb") or []
            zt2 = self._pool(d2, "zt") or []
            history_dates = recent_trade_dates(d1, 10)
            history = [(day, self._pool(day, "zt") or []) for day in history_dates]
            previous = self.fetch_previous(d1)
            recent_mains = self._recent_mains(d1, history)
            ctx = rf.yesterday_context(zt_rows=zt1, zb_rows=zb1, history=history, yesterday_rows=zt2,
                                       previous=previous, recent_mains=recent_mains, rules=self.rules)
            memory = self._memory(d1)
            with self._lock:
                self.memory = memory
                self.ctx = {**ctx, "d1": d1, "d2": d2, "zt1_rows": zt1, "zt2_rows": zt2, "zb1_rows": zb1,
                            "previous": previous, "recent_mains": recent_mains}
                self.ctx_error = ""
            self._warm()
        except Exception as exc:
            logger.warning("打板王推荐：准备 %s 的昨日上下文失败 %s", self.trade_date, exc)
            with self._lock:
                self.ctx_error = f"{type(exc).__name__}: {str(exc)[:120]}"
                self._ctx_failed_at = time.time()
                self._ctx_started = False  # CTX_RETRY_SEC 之后重试

    def _pool(self, day: str, pool: str) -> Optional[List[Dict[str, Any]]]:
        rows: List[Dict[str, Any]] = []
        try:
            rows = list(self.repository.pool_rows(day, pool))
        except Exception as exc:
            logger.debug("打板王推荐：读 %s %s 池失败 %s", day, pool, exc)
        if not rows:
            rows = list(self.fetch_pool(day, pool) or [])
        out = []
        for row in rows:
            row = dict(row)
            if pool == "zt":
                row["boards"] = int(row.get("boards") or 1)
                first, last = str(row.get("first_time") or ""), str(row.get("last_time") or "")
                if row.get("open_num") is None:
                    # 库里的池子没有开板次数：末封晚于首封 = 开过板（与 live.weak_codes 同口径）
                    row["open_num"] = 1 if (first.isdigit() and last.isdigit() and last > first) else 0
            out.append(row)
        return out

    def _recent_mains(self, d1: str, history: List[Tuple[str, List[Dict[str, Any]]]]) -> List[str]:
        from src.services.daban.themes import build_themes

        names: List[str] = []
        stored: Dict[str, str] = {}
        try:
            for row in self.repository.history(limit=12, before=d1):
                stored[row.trade_date] = str(row.main_themes or "")
        except Exception:
            stored = {}
        for day, rows in history:
            if stored.get(day):
                day_mains = [n.strip() for n in stored[day].split("、") if n.strip()]
            else:
                day_mains = [t["name"] for t in build_themes(zt=rows, blocks=[], history=[], previous=None, yesterday_rows=[],
                                                           recent_mains=[], hot_rank={}, rules=self.rules)["themes"]
                             if t.get("is_main")]
            for name in day_mains:
                if name not in names:
                    names.append(name)
        return names

    def _memory(self, d1: str) -> Dict[str, set]:
        """D（不含）之前 90 天的涨停原因。库里没有的交易日从同花顺补（只要涨停原因）。"""
        from src.services.daban.fetchers import recent_trade_dates

        since = (datetime.strptime(self.trade_date, "%Y-%m-%d") - timedelta(days=90)).strftime("%Y-%m-%d")
        out: Dict[str, set] = {}
        known: set = set()
        try:
            for code, reason in self.repository.reason_memory(since, "zt", until=self.trade_date):
                out.setdefault(code, set()).update(split_reason(reason or ""))
            known = {day for day, _ in self.repository.pool_history_since(since, "zt")}
        except Exception as exc:
            logger.debug("打板王推荐：读涨停原因记忆失败 %s", exc)
        days = [d for d in recent_trade_dates(self.trade_date, 64) if d >= since and d not in known]
        if days:
            from src.services.daban.fetchers import run_parallel

            results = run_parallel({d: (lambda day=d: self.fetch_pool(day, "zt")) for d in days}, timeout=45, workers=4)
            for rows, _ in results.values():
                for row in rows or []:
                    tags = split_reason(row.get("reason") or "")
                    if tags:
                        out.setdefault(str(row.get("code") or ""), set()).update(tags)
        return out

    def _warm(self) -> None:
        """盘前预算：昨日涨停 / 炸板的票先把日 K 取好（它们最可能成为今天的候选）。"""
        ctx = self.ctx or {}
        codes = list(dict.fromkeys([*(ctx.get("zt") or {}), *(ctx.get("zb") or set())]))
        self.request(codes)

    # -------------------------------------------------------------- 个股

    def request(self, codes: Iterable[str]) -> None:
        if self.ctx is None:
            self.ensure_ctx()
            return
        todo = []
        now = time.time()
        with self._lock:
            for code in codes:
                if not code or code in self.base or code in self.inflight:
                    continue
                if now - self.failed.get(code, 0) < 60:
                    continue
                self.inflight.add(code)
                todo.append(code)
        for code in todo:
            if self.synchronous:
                self._compute(code)
            else:
                self._executor.submit(self._compute, code)

    def _bars_of(self, code: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            if code in self.bars:
                return self.bars[code]
        bars = rf.make_bars(self.fetch_bars(code) or [], before=self.ymd)
        with self._lock:
            self.bars[code] = bars
        return bars

    def _compute(self, code: str) -> None:
        try:
            ctx = self.ctx or {}
            bars = self._bars_of(code)
            d1 = str(ctx.get("d1") or "").replace("-", "")
            if not bars or len(bars["t"]) < 30:
                result = {"code": code, "ready": True, "skip": "日 K 不足 30 根（次新或停牌），回测未覆盖"}
            elif bars["t"][-1] != d1:
                result = {"code": code, "ready": True, "skip": "昨天没有交易（停牌），回测未覆盖"}
            else:
                idx = len(bars["t"])
                tags = list(self.memory.get(code) or [])
                base = rf.base_features(code=code, k=bars, idx=idx, ctx=ctx, tags=tags)
                leader = (base.get("theme") or {}).get("leader_code")
                if leader and leader != code:
                    leader_bars = self._bars_of(leader)
                    if leader_bars:
                        base = rf.base_features(code=code, k=bars, idx=idx, ctx=ctx, tags=tags, leader_bars=leader_bars)
                result = {**base, "ready": True, "skip": ""}
            with self._lock:
                self.base[code] = result
        except Exception as exc:
            logger.debug("打板王推荐：%s 特征计算失败 %s", code, exc)
            with self._lock:
                self.failed[code] = time.time()
        finally:
            with self._lock:
                self.inflight.discard(code)

    def get(self, code: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self.base.get(code)

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {"ctx_ready": self.ctx is not None, "ctx_error": self.ctx_error, "ready": len(self.base),
                    "pending": len(self.inflight), "failed": len(self.failed)}

    def close(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=False)


def _fetch_pool_upstream(day: str, pool: str) -> Optional[List[Dict[str, Any]]]:
    from data_provider import limit_up_fetcher as lu

    try:
        result = lu.fetch_ths_pool(pool, day, timeout=12)
        rows = [dict(r) for r in result["rows"] if lu.in_universe(r.get("code"), r.get("name"))]
        if pool == "zt":
            try:
                ladder = lu.fetch_ths_ladder(day, timeout=12)
                for row in rows:
                    row["boards"] = int(ladder.get(row["code"], 1))
            except Exception:
                for row in rows:
                    days_, boards_ = int(row.get("hd_days") or 0), int(row.get("hd_boards") or 0)
                    row["boards"] = boards_ if (boards_ >= 2 and days_ == boards_) else 1
        return rows
    except Exception as exc:
        logger.debug("打板王推荐：同花顺 %s %s 池取数失败 %s", day, pool, exc)
        return None


def _fetch_previous_upstream(day: str) -> Optional[List[Dict[str, Any]]]:
    """D-2 涨停股在 D-1 的表现（东财，保留约 30 个交易日）。取不到时题材内溢价按缺失处理。"""
    from data_provider import limit_up_fetcher as lu

    try:
        return [r for r in lu.fetch_em_pool("previous", day, timeout=12) if lu.in_universe(r.get("code"), r.get("name"))]
    except Exception as exc:
        logger.debug("打板王推荐：%s 昨日涨停表现取数失败 %s", day, exc)
        return None


_BOOKS: Dict[str, FeatureBook] = {}
_BOOKS_LOCK = threading.Lock()


def feature_book(trade_date: str, *, repository, rules: Dict[str, Any]) -> FeatureBook:
    with _BOOKS_LOCK:
        book = _BOOKS.get(trade_date)
        if book is None:
            for day in list(_BOOKS):
                _BOOKS.pop(day).close()
            book = _BOOKS[trade_date] = FeatureBook(trade_date, repository=repository, rules=rules)
        return book


def clear_books() -> None:
    with _BOOKS_LOCK:
        for book in _BOOKS.values():
            book.close()
        _BOOKS.clear()


# ---------------------------------------------------------------------- 题材归属


def hot_theme_of(*, code: str, own_tags: Iterable[str], memory: Dict[str, set], block_names: Dict[str, List[str]],
                 concepts: Optional[Dict[str, Dict[str, Any]]], hot: List[Dict[str, Any]]) -> Tuple[str, str]:
    """把一只票落到今日热点题材上，返回 ``(热点 key, 来源)``。来源依次：当日涨停原因、历史涨停原因、题材榜、所属概念。"""
    index = {normalize_tag(str(h.get("key") or "")): str(h.get("key") or "") for h in hot or []}

    def _match(tags: Iterable[str]) -> str:
        names = [str(t) for t in tags if t]
        for name in names:
            key = index.get(normalize_tag(name))
            if key:
                return key
        for item in hot or []:
            if names and related_tags(names, [item.get("key") or ""]):
                return str(item.get("key") or "")
        return ""

    for source, tags in (("reason", list(own_tags or [])), ("history", list(memory.get(code) or [])),
                         ("block", list(block_names.get(code) or []))):
        key = _match(tags)
        if key:
            return key, source
    info = (concepts or {}).get(code) or {}
    key = _match([*(info.get("concepts") or []), info.get("industry") or ""])
    return (key, "concept") if key else ("", "")


# ---------------------------------------------------------------------- 评分与「重点」


def _gap(price: Optional[float], limit_up: Optional[float]) -> Optional[float]:
    if not price or not limit_up:
        return None
    return round(max(0.0, (float(limit_up) / float(price) - 1) * 100), 2)


def score_live(base: Dict[str, Any], quote: Dict[str, Any], table: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """今天已开盘、特征已就绪时算分；否则 None。"""
    if not base or base.get("skip") or not quote.get("open") or not quote.get("prev_close"):
        return None
    cells = rf.full_cells(base, today_open=quote["open"], ref_close=quote["prev_close"], limit_up=quote.get("limit_up"),
                          gap_max=float(table.get("gap_max", 6.0)))
    scored = rf.score_cells(cells, table)
    return {**scored, "cells": cells}


def temper_view(info: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    info = info or {}
    tag = info.get("tag") or "unknown"
    return {"tag": tag, "label": rf.TEMPER_LABELS.get(tag, ""), "text": rf.temper_text(info),
            "seal_rate": info.get("seal_rate"), "cont_rate": info.get("cont_rate"), "covered": bool(info.get("covered"))}


def _item_view(item: Dict[str, Any]) -> Dict[str, Any]:
    keys = ("key", "dim", "cell", "points", "label", "source", "principle_ids", "supplement", "n", "days", "excess", "halves")
    return {k: item.get(k) for k in keys if k in item}


def build_focus(
    *,
    trade_date: str,
    now: str,
    candidates: List[Dict[str, Any]],
    quotes: Dict[str, Dict[str, Any]],
    sealed_codes: Iterable[str],
    opened_codes: Iterable[str],
    hot: List[Dict[str, Any]],
    boundary: Dict[str, Any],
    theme_info: Dict[str, Dict[str, Any]],
    book: Optional[FeatureBook],
    recorded: Dict[str, Dict[str, Any]],
    rules: Dict[str, Any],
    recording: bool,
    surge: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """一轮「重点」：已记录的推荐（实时状态 + 实时分）+ 这一轮新进入的 + 没进入的候选和原因。

    ``surge``：当天的大盘急拉推荐点（``worldview.surge_point``）；``now`` 落在 [触发时刻, 截止时刻) 里时，新进入的票打标记。
    """
    from src.services.daban.worldview import candidate_gate

    table = rules.get("rec_score") or {}
    threshold = int(table.get("threshold", 0))
    gap_max = float(table.get("gap_max", 6.0))
    sealed_codes = set(sealed_codes or ())
    opened_codes = set(opened_codes or ()) - sealed_codes
    hot_by_key = {str(h.get("key") or ""): h for h in hot or []}
    if book is not None:
        book.request([c["code"] for c in candidates if c.get("theme_key")])
        book.request(list(recorded))

    book_status = book.status() if book is not None else {}
    new_rows: List[Dict[str, Any]] = []
    live_updates: Dict[str, Dict[str, Any]] = {}
    watching: List[Dict[str, Any]] = []
    for cand in candidates:
        code = cand["code"]
        if code in recorded:
            continue
        quote = quotes.get(code) or cand
        base = book.get(code) if book is not None else None
        reason = ""
        scored = None
        if not cand.get("theme_key"):
            continue  # 不在热点题材里：不评分，也不列进候选（留档里有）
        if book is None:
            reason = "非交易时段：回放不评分"
        elif not book_status.get("ctx_ready"):
            err = str(book_status.get("ctx_error") or "")
            reason = f"评分暂停：昨日上下文没准备好（{err[:60]}），稍后自动重试" if err else "评分中：昨日上下文还在准备"
        elif base is None:
            reason = "评分中：日 K 还在取"
        elif base.get("skip"):
            reason = base["skip"]
        else:
            scored = score_live(base, quote, table)
            if scored is None:
                reason = "今天还没开盘价，暂不评分"
        if not reason:
            info = theme_info.get(cand["theme_key"]) or {}
            reason = candidate_gate(theme_key=cand["theme_key"], y_boards=int(base.get("y_boards") or 0),
                                    boundary=boundary, theme_info=info, rules=rules) or ""
            if not reason and scored["total"] < threshold:
                reason = f"总分 {scored['total']} 低于门槛 {threshold}"
        gap = _gap(cand.get("price"), cand.get("limit_up"))
        view = {
            "code": code, "name": cand.get("name") or "", "status": cand.get("status"), "price": cand.get("price"),
            "gap_pct": gap, "change_pct": cand.get("change_pct"), "theme_key": cand["theme_key"],
            "theme_name": (hot_by_key.get(cand["theme_key"]) or {}).get("name") or cand["theme_key"],
            "score": scored["total"] if scored else None,
            "items": [_item_view(i) for i in (scored or {}).get("items") or []],
            "reason": reason,
        }
        if reason:
            watching.append(view)
            continue
        flags = []
        if not (base.get("temper") or {}).get("covered"):
            flags.append(FLAG_UNCOVERED)
        if cand.get("status") == STATUS_OPENED:
            flags.append(FLAG_OPENED_ENTRY)
        if (boundary.get("pass") or {}).get("kind") == "node":
            flags.append(FLAG_NODE_DAY)
        if cand["theme_key"] in (boundary.get("open_themes") or []) or (boundary.get("pass") or {}).get("kind") == "override":
            flags.append(FLAG_OVERRIDE)
        if surge and str(surge.get("at") or "") <= now < str(surge.get("until") or ""):
            flags.append(FLAG_SURGE)
        if (boundary.get("pass") or {}).get("kind") == "surge":
            flags.append(FLAG_UNVERIFIED)
        observe = scored.get("observe")
        new_rows.append({
            "code": code, "name": cand.get("name") or "", "theme_key": cand["theme_key"], "theme_name": view["theme_name"],
            "entered_at": now, "cost": cand.get("price"), "entry_status": cand.get("status"), "entry_gap_pct": gap,
            "init_score": scored["total"], "live_score": scored["total"],
            "group_key": GROUP_OBSERVE if observe else GROUP_NORMAL, "observe_key": (observe or {}).get("key"),
            "flags": ",".join(flags), "temper_tag": (base.get("temper") or {}).get("tag"),
            "covered": bool((base.get("temper") or {}).get("covered")),
            "init_detail": {
                "items": [_item_view(i) for i in scored["items"]], "cells": scored["cells"],
                "observe": observe, "temper": base.get("temper"),
                "features": {k: base.get(k) for k in ("y_state", "y_quality", "y_boards", "volume_ratio", "price_band",
                                                       "regime", "corr_band", "has_memory")},
                "theme": base.get("theme"),
                "quote": {k: quote.get(k) for k in ("price", "open", "prev_close", "limit_up", "turnover_rate",
                                                     "volume_ratio", "sell1_volume", "change_pct")},
                "boundary": {k: boundary.get(k) for k in ("status", "blocks", "block_label", "pass", "open_themes", "reasons", "cycle")},
                "nodes": (boundary.get("nodes") or {}).get("labels") or [],
                "theme_info": theme_info.get(cand["theme_key"]) or {},
                "hot": {k: (hot_by_key.get(cand["theme_key"]) or {}).get(k) for k in ("rank", "zt_count", "days", "news_count")},
            },
            "live_detail": {"items": [_item_view(i) for i in scored["items"]], "at": now},
        })

    rows = list(recorded.values()) + ([{**r, "_new": True} for r in new_rows] if recording else [])
    items: List[Dict[str, Any]] = []
    for row in rows:
        code = row["code"]
        quote = quotes.get(code) or {}
        price = quote.get("price")
        base = book.get(code) if book is not None else None
        live = score_live(base, quote, table) if base else None
        live_score = live["total"] if live else row.get("live_score")
        live_items = [_item_view(i) for i in live["items"]] if live else ((row.get("live_detail") or {}).get("items") or [])
        flags = [f for f in str(row.get("flags") or "").split(",") if f]
        newly_down = live_score is not None and live_score < threshold and FLAG_DOWNGRADED not in flags
        if newly_down:
            flags.append(FLAG_DOWNGRADED)
        if live and not row.get("_new") and (live_score != row.get("live_score") or newly_down):
            live_updates[code] = {"live_score": live_score, "live_detail": {"items": live_items, "at": now},
                                  "flags": ",".join(flags), "_change": {"from": row.get("live_score"), "to": live_score}}
        if code in sealed_codes:
            status = STATUS_SEALED
        elif code in opened_codes:
            status = STATUS_OPENED
        else:
            status = STATUS_PREBOARD
        detail = row.get("init_detail") or {}
        cost = row.get("cost")
        items.append({
            "code": code, "name": row.get("name") or "", "theme_key": row.get("theme_key") or "",
            "theme_name": row.get("theme_name") or row.get("theme_key") or "",
            "entered_at": row.get("entered_at") or "", "cost": cost, "entry_status": row.get("entry_status"),
            "price": price, "gap_pct": 0.0 if status == STATUS_SEALED else _gap(price, quote.get("limit_up")),
            "ret_now": round((price / cost - 1) * 100, 2) if price and cost else None,
            "change_pct": (round((price / quote["prev_close"] - 1) * 100, 2) if price and quote.get("prev_close") else None),
            "status": status, "init_score": row.get("init_score"), "live_score": live_score,
            "items": live_items, "init_items": detail.get("items") or [],
            "group_key": row.get("group_key") or GROUP_NORMAL, "observe": detail.get("observe"),
            "flags": flags, "temper": temper_view(detail.get("temper")), "is_new": bool(row.get("_new")),
            "features": detail.get("features") or {},
        })
    order = {str(h.get("key") or ""): i for i, h in enumerate(hot or [])}
    groups: Dict[str, Dict[str, Any]] = {}
    for item in items:
        key = item["theme_key"]
        group = groups.setdefault(key, {"theme_key": key, "theme_name": item["theme_name"],
                                         "rank": (hot_by_key.get(key) or {}).get("rank"), "hot": key in hot_by_key,
                                         "items": []})
        group["items"].append(item)
    def _rank(i):
        return (FLAG_SURGE not in i["flags"], -(i["live_score"] if i["live_score"] is not None else -999),
                i["gap_pct"] if i["gap_pct"] is not None else 99)

    for group in groups.values():
        group["items"].sort(key=_rank)
    ordered = sorted(groups.values(), key=lambda g: (order.get(g["theme_key"], 999), g["theme_name"]))
    watching.sort(key=lambda w: (order.get(w["theme_key"], 999), -(w["score"] if w["score"] is not None else -999)))
    # 置顶：大盘急拉推荐点窗口里进入的票（组内也排最前）。只影响展示顺序，成绩照常统计。
    pinned = sorted([i for i in items if FLAG_SURGE in i["flags"]], key=_rank)
    return {"groups": ordered, "count": len(items), "pinned": pinned, "new_rows": new_rows if recording else [],
            "live_updates": live_updates, "watching": watching[:60], "threshold": threshold, "gap_max": gap_max}


# ---------------------------------------------------------------------- 涨停板 tab


def build_limit_board(*, rows: List[Dict[str, Any]], quotes: Dict[str, Dict[str, Any]], hot: List[Dict[str, Any]],
                      theme_of: Dict[str, str], book: Optional[FeatureBook], recommended: Iterable[str]) -> Dict[str, Any]:
    """已封板的票按热点题材分组，每组龙 1 / 龙 2 打标记放最上面（龙头排序与盘后一致：板数 → N 天 M 板 → 首封时间）。

    不评分、不记成绩；被推荐过的标「已推荐」；龙头附连板成功率（科比 #29：看风向标明天有没有人接）。
    """
    from src.services.daban.radar_log import quote_sealed

    recommended = set(recommended or ())
    hot_by_key = {str(h.get("key") or ""): h for h in hot or []}
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows or []:
        key = theme_of.get(row["code"]) or ""
        groups.setdefault(key, []).append(row)
    out = []
    for key, members in groups.items():
        members.sort(key=lambda r: (-int(r.get("boards") or 1), -int(r.get("hd_boards") or 0),
                                    str(r.get("first_time") or "999999")))
        items = []
        for index, row in enumerate(members):
            quote = quotes.get(row["code"]) or {}
            base = book.get(row["code"]) if book is not None else None
            temper = temper_view((base or {}).get("temper")) if base and not base.get("skip") else None
            seal, circ = row.get("seal_amount"), row.get("circ_mv")
            items.append({
                "code": row["code"], "name": row.get("name") or "", "boards": int(row.get("boards") or 1),
                "high_days": row.get("high_days") or "", "first_time": row.get("first_time") or "",
                "last_time": row.get("last_time") or "", "open_num": int(row.get("open_num") or 0),
                "limit_up_type": row.get("limit_up_type") or "", "reason": row.get("reason") or "",
                "seal_amount": seal, "seal_ratio": (seal / circ) if (seal and circ) else None,
                "sealed_now": quote_sealed(quote) if quote else None,
                "leader_rank": index + 1 if (key and index < 2) else None,
                "recommended": row["code"] in recommended,
                "cont_rate": (temper or {}).get("cont_rate"), "temper": temper,
            })
        out.append({"theme_key": key, "theme_name": (hot_by_key.get(key) or {}).get("name") or key or "未归入热点题材",
                    "rank": (hot_by_key.get(key) or {}).get("rank"), "hot": key in hot_by_key, "count": len(items),
                    "items": items})
    order = {str(h.get("key") or ""): i for i, h in enumerate(hot or [])}
    out.sort(key=lambda g: (0 if g["hot"] else 1, order.get(g["theme_key"], 999), -g["count"]))
    return {"groups": out, "count": sum(g["count"] for g in out)}


# ---------------------------------------------------------------------- 成绩回填（H1）


def last_closed_date(today: str, now: str) -> str:
    """已经收盘的最后一个交易日（YYYYMMDD）。今天 15:05 之前今天还没收盘。"""
    from src.services.daban.fetchers import previous_trade_date
    from src.services.daban.live import is_trading_day

    try:
        trading = is_trading_day(today)
    except Exception:
        trading = datetime.strptime(today, "%Y-%m-%d").weekday() < 5
    if trading and now >= "150500":
        return today.replace("-", "")
    return previous_trade_date(today).replace("-", "")


def settle_outcome(rec: Dict[str, Any], bars_rows: List[Dict[str, Any]], *, last_closed: str) -> Optional[Dict[str, Any]]:
    """一条推荐的 H1 / 次日开盘卖 / 次日收盘卖。日 K 是不复权的，``last_closed`` 之后的 K 线不用。"""
    bars = rf.make_bars(bars_rows)
    if not bars:
        return None
    keep = [i for i, t in enumerate(bars["t"]) if t <= last_closed]
    if not keep:
        return None
    last = keep[-1]
    i0 = bars["ix"].get(str(rec["trade_date"]).replace("-", ""))
    if i0 is None or i0 < 1 or not rec.get("cost"):
        return None
    sealed = rec.get("sealed_close")
    return rf.exit_returns(bars, i0, float(rec["cost"]), limit_pct(rec["code"]), last=last, live=True,
                           sealed=None if sealed is None else bool(sealed))


SETTLE_BARS_BASE = 20      # H1 最多持有 5 个交易日，正常只需要十几根
SETTLE_BARS_MAX = 160      # 兜底上限（约半年），避免异常数据把上游拉爆


def _bars_needed(today: str, earliest_trade_date: str) -> int:
    """按最早的待结算买入日估算要拉多少根日 K（日历日 × 0.75 折算交易日，再加缓冲）。"""
    from datetime import datetime

    try:
        gap = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(earliest_trade_date, "%Y-%m-%d")).days
    except (TypeError, ValueError):
        return SETTLE_BARS_BASE
    return max(SETTLE_BARS_BASE, min(SETTLE_BARS_MAX, int(gap * 0.75) + SETTLE_BARS_BASE))


def settle_pending(repository, *, today: str, now: str,
                   fetch: Optional[Callable[[str], List[Dict[str, Any]]]] = None) -> int:
    """回填所有还没结案的推荐（H1 持有中的也会更新）。返回更新的条数。

    ``fetch`` 的默认实现按**最早一笔待结算的买入日**决定回溯长度：服务停用或结算连续失败
    超过 ~20 个交易日时，固定拉 20 根就会丢掉买入日那一根，``settle_outcome`` 永远返回 None。
    """
    pending = repository.pending_recommendations(before_or_on=today)
    if not pending:
        return 0
    if fetch is None:
        earliest = min(str(r["trade_date"]) for r in pending)
        limit = _bars_needed(today, earliest)
        fetch = lambda code: fetch_bars_raw(code, limit=limit)   # noqa: E731
    last_closed = last_closed_date(today, now)
    codes = sorted({r["code"] for r in pending if r["trade_date"].replace("-", "") < last_closed
                    or (r["trade_date"].replace("-", "") == last_closed and now >= "150500")})
    if not codes:
        return 0
    from src.services.daban.fetchers import run_parallel

    results = run_parallel({c: (lambda code=c: fetch(code)) for c in codes}, timeout=40, workers=6)
    updates: Dict[int, Dict[str, Any]] = {}
    for rec in pending:
        rows, _ = results.get(rec["code"]) or (None, None)
        if not rows:
            continue
        outcome = settle_outcome(rec, rows, last_closed=last_closed)
        if outcome is None or (outcome["status"] == "pending" and outcome.get("f1") is None):
            continue
        updates[rec["id"]] = {
            "sealed_close": rec.get("sealed_close") if rec.get("sealed_close") is not None else outcome.get("sealed"),
            "h1_ret": outcome.get("h1"), "h1_exit_date": outcome.get("h1_exit"), "h1_rule": outcome.get("h1_rule"),
            "next_open_ret": outcome.get("f1"), "next_close_ret": outcome.get("f2"),
            "settle_status": outcome["status"],
        }
    return repository.update_recommendation_outcomes(updates)


# ---------------------------------------------------------------------- 复盘统计


def _ret_stats(rows: List[Dict[str, Any]], key: str) -> Dict[str, Any]:
    values = [float(r[key]) for r in rows if r.get(key) is not None]
    if not values:
        return {"n": 0, "mean": None, "win": None}
    return {"n": len(values), "mean": round(mean(values), 2), "win": round(sum(v > 0 for v in values) / len(values), 4)}


def _bucket(score: Optional[int]) -> str:
    """分档的键只用 ASCII：前端会把键转成 camelCase，「< 0」这类键会被转坏。"""
    if score is None:
        return "none"
    if score < 0:
        return "neg"
    if score == 0:
        return "zero"
    return "pos"


def focus_stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """推荐成绩：三个口径；按分档、观察组、节点日、进入时状态分组；以及封住率。"""
    def _block(part: List[Dict[str, Any]]) -> Dict[str, Any]:
        sealed = [r for r in part if r.get("sealed_close") is not None]
        return {
            "n": len(part), "days": len({r["trade_date"] for r in part}),
            "h1": _ret_stats(part, "h1_ret"), "next_open": _ret_stats(part, "next_open_ret"),
            "next_close": _ret_stats(part, "next_close_ret"),
            "seal_rate": round(sum(bool(r["sealed_close"]) for r in sealed) / len(sealed), 4) if sealed else None,
            "holding": sum(1 for r in part if r.get("settle_status") == "holding"),
        }

    def _flags(r):
        return set(str(r.get("flags") or "").split(","))

    out = {"all": _block(rows), "by_score": {}, "by_group": {}, "by_node": {}, "by_entry": {}, "by_temper": {}}
    for key in ("neg", "zero", "pos"):
        out["by_score"][key] = _block([r for r in rows if _bucket(r.get("init_score")) == key])
    for key, label in GROUP_LABELS.items():
        out["by_group"][key] = {**_block([r for r in rows if (r.get("group_key") or GROUP_NORMAL) == key]), "label": label}
    out["by_node"]["node"] = _block([r for r in rows if FLAG_NODE_DAY in _flags(r)])
    out["by_node"]["other"] = _block([r for r in rows if FLAG_NODE_DAY not in _flags(r)])
    out["by_entry"][STATUS_PREBOARD] = _block([r for r in rows if r.get("entry_status") != STATUS_OPENED])
    out["by_entry"][STATUS_OPENED] = _block([r for r in rows if r.get("entry_status") == STATUS_OPENED])
    for tag in ("active", "normal", "inactive", "unknown"):
        out["by_temper"][tag] = _block([r for r in rows if (r.get("temper_tag") or "unknown") == tag])
    return out


def item_accuracy(rows: List[Dict[str, Any]], table: Dict[str, Any], *, min_n: int = 20) -> List[Dict[str, Any]]:
    """每个计分项的实盘成绩对回测预期：实盘 H1 超额（相对全部推荐）和回测超额是否同号，偏离时给修正建议。"""
    done = [r for r in rows if r.get("h1_ret") is not None]
    if not done:
        return []
    base = mean(float(r["h1_ret"]) for r in done)
    out = []
    for item in table.get("items") or []:
        part = [r for r in done if item.get("key") in {i.get("key") for i in ((r.get("init_detail") or {}).get("items") or [])}]
        if not part:
            out.append({"key": item["key"], "label": item.get("label"), "n": 0, "expected": item.get("excess"),
                        "actual": None, "verdict": "no_sample"})
            continue
        actual = round(mean(float(r["h1_ret"]) for r in part) - base, 2)
        if len(part) < min_n:
            verdict = "insufficient"
        elif (actual > 0) == (float(item.get("excess") or 0) > 0):
            verdict = "consistent"
        else:
            verdict = "suggest_review"
        out.append({"key": item["key"], "label": item.get("label"), "n": len(part), "expected": item.get("excess"),
                    "actual": actual, "verdict": verdict})
    return out


def attribution(rec: Dict[str, Any], events: List[Dict[str, Any]], news: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """一只推荐的分差归因：初始分到收盘分逐项变化、推荐之后发生的事（当时不可知）、推荐之前已有但没纳入的（漏项）。

    评判推荐行为只用推荐时刻之前的数据；推荐之后的事件只放进「当时不可知」，不用来评判当时的推荐。
    """
    entered = str(rec.get("entered_at") or "")
    init_items = {i.get("key"): i for i in ((rec.get("init_detail") or {}).get("items") or [])}
    close_items = {i.get("key"): i for i in ((rec.get("close_detail") or rec.get("live_detail") or {}).get("items") or [])}
    diff = []
    for key in sorted(set(init_items) | set(close_items)):
        a, b = init_items.get(key), close_items.get(key)
        if a and b and a.get("points") == b.get("points"):
            continue
        diff.append({"key": key, "label": (a or b).get("label"), "init": (a or {}).get("points"), "close": (b or {}).get("points")})
    after = [{"at": e.get("at"), "kind": e.get("kind"), "price": e.get("price")} for e in events
             if e.get("kind") in ("seal", "open", "reseal") and str(e.get("at") or "") > entered]
    before_news, after_news = [], []
    for item in news or []:
        stamp = _news_hhmmss(item.get("published_at"), rec.get("trade_date"))
        entry = {"title": item.get("title"), "published_at": item.get("published_at"), "url": item.get("url")}
        if stamp is None:
            continue
        (before_news if stamp <= entered else after_news).append(entry)
    return {
        "init_score": rec.get("init_score"), "close_score": rec.get("close_score"), "diff": diff,
        "unknowable": after, "after_news": after_news[:5],
        # 推荐时刻之前已经发布的个股新闻：评分体系当时没有纳入个股新闻，全部记为「漏项」，用来决定要不要补这个数据源
        "missed": before_news[:5],
    }


def _news_hhmmss(published_at: Any, trade_date: Any) -> Optional[str]:
    text = str(published_at or "").strip()
    day = str(trade_date or "")
    if len(text) < 16 or not text.startswith(day):
        return None
    return text[11:13] + text[14:16] + (text[17:19] if len(text) >= 19 else "00")


__all__ = [
    "FLAG_DOWNGRADED", "FLAG_NODE_DAY", "FLAG_OPENED_ENTRY", "FLAG_OVERRIDE", "FLAG_SURGE", "FLAG_UNCOVERED", "FLAG_UNVERIFIED", "FeatureBook",
    "GROUP_LABELS", "GROUP_NORMAL", "GROUP_OBSERVE", "attribution", "build_focus", "build_limit_board", "clear_books",
    "feature_book", "fetch_bars_qfq", "fetch_bars_raw", "focus_stats", "hot_theme_of", "item_accuracy",
    "last_closed_date", "score_live", "settle_outcome", "settle_pending", "temper_view",
]
