# -*- coding: utf-8 -*-
"""盘中雷达的时序留档，写 ``daban_radar_events``。

为什么要记：评分体系里「盘中因子」（涨速、换手、扫板、题材实时封板数、龙头状态、大盘指数……）日线回测测不到，
只能靠雷达自己按时刻攒数据，攒够 4~6 周后再验证有没有预测力（见 docs/daban-king.md「盘中留档」）。

记什么（只追加，不改）：

- ``seen``：一只票当天第一次进入候选。候选不看两派门控，原始口径是「涨停池 + 炸板池 + 距涨停 ≤ gap_max 的拉升
  异动」，这样周期门控关着的日子也有数据（世界观上线后退潮日也可能放行，需要那些天的样本）。``data.hot`` 标它
  是否落在今日热点题材里；冷门票只记这一条，之后进入热点会再记一条 ``hot=True`` 的 ``seen``。
- ``seal`` / ``open`` / ``reseal``：热点候选的状态变化（封板 / 炸板 / 回封）。
- ``sample``：热点候选里还没封住的，每 ``sample_sec`` 秒一轮，用腾讯快照记现价、距涨停、换手、量比、卖一量，
  不论它这一轮还在不在异动列表里——这样一只票从被发现到收盘的价格路径是完整的。
- ``theme``：每 ``theme_sec`` 秒记一次热点题材与各题材龙 1 / 龙 2 的当下状态（``code`` 为空）。
- ``market``：每 ``market_sec`` 秒记一次三大指数与全市场涨停 / 炸板 / 跌停等指标（``code`` 为空）。
- ``auction``：集合竞价期间每 ``auction_sec`` 秒给昨日涨停 / 炸板的票记竞价价格与成交量。
- ``focus`` / ``score``：进入「重点」那一刻（分数、命中的计分项），以及之后实时分的变化。
- ``override`` / ``worldview``：盘中推翻信号和世界观边界的改动（时刻、改前改后、触发的数据）。

留档失败只记日志，不影响雷达快照。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

STATUS_CANDIDATE = "candidate"
STATUS_SEALED = "sealed"
STATUS_OPENED = "opened"

# 状态变化 → 事件类型。转回 candidate 不记（池子不会把炸板的票退回去）。
_TRANSITIONS = {
    (STATUS_CANDIDATE, STATUS_SEALED): "seal",
    (STATUS_OPENED, STATUS_SEALED): "reseal",
    (STATUS_CANDIDATE, STATUS_OPENED): "open",
    (STATUS_SEALED, STATUS_OPENED): "open",
}

# 进程内状态：交易日 → {"tracked": {code: {...}}, "cold": set, "sample_last", "theme_last", "market_last"}。
# 首次用到某天时从库里恢复（进程重启后接着记）。只留当天。
_STATE: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.Lock()

# 写进 seen / sample 的个股字段（缺的不写）。
_SEEN_FIELDS = ("status", "change_pct", "turnover_rate", "volume_ratio", "circ_mv", "sell1_volume",
                "open_pct", "first_time", "open_num", "seal_amount", "boards", "source")
_SAMPLE_FIELDS = ("status", "change_pct", "turnover_rate", "volume_ratio", "sell1_volume", "high_pct", "volume")


def _sec(hhmmss: str) -> int:
    text = str(hhmmss or "")
    try:
        return int(text[:2]) * 3600 + int(text[2:4]) * 60 + int(text[4:6] or 0)
    except (TypeError, ValueError):
        return -1


def _due(now: str, last: str, interval: int) -> bool:
    return not last or _sec(now) - _sec(last) >= interval


def gap_pct(price: Optional[float], limit_up: Optional[float]) -> Optional[float]:
    """距涨停价的百分点（现价涨到涨停还要涨多少）。"""
    if not price or not limit_up or price <= 0:
        return None
    return round(max(0.0, (float(limit_up) / float(price) - 1) * 100), 3)


def quote_sealed(quote: Dict[str, Any]) -> bool:
    """快照上的封板判定，与雷达一致：现价到涨停价且卖一为 0。"""
    price, limit_up = quote.get("price"), quote.get("limit_up")
    return bool(price and limit_up and price >= limit_up - 1e-6 and int(quote.get("sell1_volume") or 0) == 0)


def _pct(value: Optional[float], base: Optional[float]) -> Optional[float]:
    if value is None or not base:
        return None
    return round((float(value) / float(base) - 1) * 100, 3)


def _pick(source: Dict[str, Any], fields) -> Dict[str, Any]:
    return {key: source[key] for key in fields if source.get(key) not in (None, "", {})}


def _load_state(repository, trade_date: str) -> Dict[str, Any]:
    state = _STATE.get(trade_date)
    if state is not None:
        return state
    for day in list(_STATE):
        if day != trade_date:
            _STATE.pop(day, None)
    try:
        restored = repository.radar_state(trade_date)
    except Exception as exc:  # 读不到就从空状态开始，最坏多写几条 seen
        logger.warning("打板王雷达留档：恢复 %s 的状态失败 %s", trade_date, exc)
        restored = {}
    state = {
        "tracked": dict(restored.get("tracked") or {}),
        "cold": set(restored.get("cold") or ()),
        "sample_last": str(restored.get("sample_last") or ""),
        "theme_last": str(restored.get("theme_last") or ""),
        "market_last": str(restored.get("market_last") or ""),
        "auction_last": str(restored.get("auction_last") or ""),
    }
    _STATE[trade_date] = state
    return state


class RadarRecorder:
    """把一轮雷达快照转成留档事件。外部取数（个股快照、指数快照）都可以注入，便于离线测试。"""

    def __init__(
        self,
        repository,
        rules: Dict[str, Any],
        *,
        fetch_quotes: Optional[Callable[[List[str]], Dict[str, Dict[str, Any]]]] = None,
        fetch_indices: Optional[Callable[[], Dict[str, Dict[str, Any]]]] = None,
    ):
        cfg = (rules or {}).get("radar_log") or {}
        self.repository = repository
        self.enabled = bool(cfg.get("enabled", True))
        self.sample_sec = int(cfg.get("sample_sec", 300))
        self.theme_sec = int(cfg.get("theme_sec", 300))
        self.market_sec = int(cfg.get("market_sec", 60))
        self.auction_sec = int(cfg.get("auction_sec", 60))
        self.fetch_quotes = fetch_quotes
        self.fetch_indices = fetch_indices

    def record(
        self,
        trade_date: str,
        now: str,
        *,
        candidates: List[Dict[str, Any]],
        theme_snapshot: Optional[Dict[str, Any]] = None,
        market: Optional[Dict[str, Any]] = None,
        extra: Optional[List[Dict[str, Any]]] = None,
    ) -> int:
        """写一轮事件，返回写入条数。``candidates`` 见 ``live.radar_candidates``；``extra`` 是调用方算好的事件
        （``focus`` 进入重点、``score`` 分数变动、``override`` 推翻信号、``worldview`` 世界观改动），原样写入。"""
        if not self.enabled:
            return 0
        with _LOCK:
            state = _load_state(self.repository, trade_date)
            events = list(extra or [])
            events.extend(self._candidate_events(state, now, candidates))
            events.extend(self._sample_events(state, now, candidates))
            if theme_snapshot is not None and _due(now, state["theme_last"], self.theme_sec):
                events.append({"at": now, "kind": "theme", "data": theme_snapshot})
                state["theme_last"] = now
            if market is not None and _due(now, state["market_last"], self.market_sec):
                events.append({"at": now, "kind": "market", "data": self._market_data(market)})
                state["market_last"] = now
            if not events:
                return 0
            return self.repository.add_radar_events(trade_date, events)

    def record_auction(self, trade_date: str, now: str, *, quotes: Dict[str, Dict[str, Any]]) -> int:
        """集合竞价（09:15–09:25）：每 ``auction_sec`` 秒给昨日涨停 / 炸板的票记一次竞价价格与成交量。

        陈小群弱转强的条件之一是「集合竞价从负转正，量能是前一日同期 3 倍以上」——竞价阶段的数据不留就没法验证。
        """
        if not self.enabled or not quotes:
            return 0
        with _LOCK:
            state = _load_state(self.repository, trade_date)
            if not _due(now, state.get("auction_last") or "", int(self.auction_sec)):
                return 0
            events = []
            for code, quote in sorted(quotes.items()):
                price, prev_close = quote.get("price"), quote.get("prev_close")
                if not price or not prev_close:
                    continue
                events.append({"at": now, "kind": "auction", "code": code, "name": quote.get("name"), "price": price,
                               "gap_pct": gap_pct(price, quote.get("limit_up")),
                               "data": {k: v for k, v in {"change_pct": _pct(price, prev_close),
                                                          "volume": quote.get("volume"),
                                                          "amount": quote.get("amount")}.items() if v is not None}})
            state["auction_last"] = now
            return self.repository.add_radar_events(trade_date, events) if events else 0

    # ------------------------------------------------------------------ 个股

    def _candidate_events(self, state, now: str, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        tracked, cold = state["tracked"], state["cold"]
        for cand in candidates:
            code = str(cand.get("code") or "")
            if not code:
                continue
            hot = bool(cand.get("theme_key"))
            status = str(cand.get("status") or STATUS_CANDIDATE)
            entry = tracked.get(code)
            if entry is None:
                if not hot and code in cold:
                    continue
                events.append(self._event(now, "seen", cand, {**_pick(cand, _SEEN_FIELDS), "hot": hot}))
                if hot:
                    tracked[code] = {"status": status}
                else:
                    cold.add(code)
                continue
            kind = _TRANSITIONS.get((entry.get("status"), status))
            if kind:
                events.append(self._event(now, kind, cand, _pick(cand, ("first_time", "open_num", "seal_amount"))))
                entry["status"] = status
        return events

    def _sample_events(self, state, now: str, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not _due(now, state["sample_last"], self.sample_sec):
            return []
        tracked = state["tracked"]
        codes = sorted(code for code, entry in tracked.items() if entry.get("status") != STATUS_SEALED)
        if not codes:
            state["sample_last"] = now
            return []
        quotes: Dict[str, Dict[str, Any]] = {}
        if self.fetch_quotes is not None:
            try:
                quotes = self.fetch_quotes(codes) or {}
            except Exception as exc:
                logger.warning("打板王雷达留档：采样快照取数失败，下一轮重试 %s", exc)
                return []
        current = {str(c.get("code") or ""): c for c in candidates}
        events: List[Dict[str, Any]] = []
        for code in codes:
            quote = quotes.get(code) or {}
            cand = current.get(code) or {}
            price = quote.get("price") or cand.get("price")
            if not price:
                continue
            limit_up = quote.get("limit_up") or cand.get("limit_up")
            prev_close = quote.get("prev_close") or cand.get("prev_close")
            status = cand.get("status") or (STATUS_SEALED if quote and quote_sealed(quote) else STATUS_CANDIDATE)
            row = {
                "code": code, "name": quote.get("name") or cand.get("name"), "theme_key": cand.get("theme_key"),
                "price": price, "limit_up": limit_up, "status": status,
                "change_pct": _pct(price, prev_close) if prev_close else cand.get("change_pct"),
                "turnover_rate": quote.get("turnover_rate", cand.get("turnover_rate")),
                "volume_ratio": quote.get("volume_ratio", cand.get("volume_ratio")),
                "sell1_volume": quote.get("sell1_volume", cand.get("sell1_volume")),
                "high_pct": _pct(quote.get("high"), prev_close),
                "volume": quote.get("volume"),
            }
            events.append(self._event(now, "sample", row, _pick(row, _SAMPLE_FIELDS)))
        state["sample_last"] = now
        return events

    @staticmethod
    def _event(now: str, kind: str, cand: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "at": now, "kind": kind, "code": cand.get("code"), "name": cand.get("name"),
            "theme": cand.get("theme_key") or "", "price": cand.get("price"),
            "gap_pct": gap_pct(cand.get("price"), cand.get("limit_up")), "data": data,
        }

    # ------------------------------------------------------------------ 大盘

    def _market_data(self, market: Dict[str, Any]) -> Dict[str, Any]:
        data = {key: value for key, value in market.items() if value is not None}
        if self.fetch_indices is not None:
            try:
                data["indices"] = {
                    symbol: {k: q.get(k) for k in ("price", "change_pct", "high", "low")}
                    for symbol, q in (self.fetch_indices() or {}).items()
                }
            except Exception as exc:
                data["indices_error"] = f"{type(exc).__name__}: {str(exc)[:80]}"
        return data


def clear_state() -> None:
    _STATE.clear()


__all__ = ["RadarRecorder", "clear_state", "gap_pct", "quote_sealed"]
