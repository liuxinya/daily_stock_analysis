# -*- coding: utf-8 -*-
"""消息面汇总的时点与时间窗判定。

回答两个问题：现在该出哪种汇总（盘前 / 盘中 / 盘后 / 周末 / 节假日），
以及该汇总应该覆盖哪一段时间的消息。

为什么单独一个模块：``MarketPhase`` 把周末和节假日都归成 ``non_trading``，仓库里
没有区分两者的方法，而这两种场景的消息面语义不同（周末是两天的消息堆积，长假可能是
七八天）。同时也**不给 ``MarketPhase`` 加枚举值** —— 那会牵动 5 处白名单
（``_phase_booleans``、``market_phase_prompt``、``market_phase_summary``、
``analysis_context_pack_prompt``、``phase_decision_guardrail``），漏一处新 phase
就会被静默降级成 unknown。weekend / holiday 是本模块自己的派生概念。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

from src.core.trading_calendar import (
    MarketPhase,
    build_market_phase_context,
    get_previous_session_close,
    get_session_open,
)

logger = logging.getLogger(__name__)

MODE_PREMARKET = "premarket"
MODE_INTRADAY = "intraday"
MODE_POSTMARKET = "postmarket"
MODE_WEEKEND = "weekend"
MODE_HOLIDAY = "holiday"
MODE_UNKNOWN = "unknown"

MODE_LABELS: Dict[str, str] = {
    MODE_PREMARKET: "盘前",
    MODE_INTRADAY: "盘中",
    MODE_POSTMARKET: "盘后",
    MODE_WEEKEND: "周末",
    MODE_HOLIDAY: "节假日",
    MODE_UNKNOWN: "未知时段",
}

# 各模式的窗口语义，用于 prompt 与前端说明
MODE_WINDOW_HINTS: Dict[str, str] = {
    MODE_PREMARKET: "上一个交易日收盘至今",
    MODE_INTRADAY: "今日开盘至今",
    MODE_POSTMARKET: "今日开盘至今",
    MODE_WEEKEND: "上一个交易日收盘至今",
    MODE_HOLIDAY: "上一个交易日收盘至今",
    MODE_UNKNOWN: "最近一段时间",
}

_INTRADAY_PHASES = {
    MarketPhase.INTRADAY,
    MarketPhase.LUNCH_BREAK,
    MarketPhase.CLOSING_AUCTION,
}

# 交易日历不可用/异常时退化成固定回看时长
DEFAULT_FALLBACK_HOURS = 24
# 窗口上限：长假能合理地跨到 8 天，但再长基本是日历异常，别把一个月的消息灌进 prompt
DEFAULT_MAX_WINDOW_DAYS = 12
# 窗口下限：刚开盘几分钟的"今日开盘至今"太薄，据此出稿没有意义
DEFAULT_MIN_WINDOW_MINUTES = 90


@dataclass(frozen=True)
class BriefWindow:
    """一次汇总的时点与时间窗。所有 datetime 都是市场本地时区的 aware 值。"""

    market: str
    mode: str
    phase: str
    now: datetime
    start: datetime
    end: datetime
    # 时间窗是否因为日历不可用/边界缺失而退化成固定回看
    degraded: bool = False
    degraded_reason: str = ""
    # 起点被窗口下限往前放过（例如刚开盘时把隔夜消息一起纳入）
    extended: bool = False
    warnings: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def label(self) -> str:
        return MODE_LABELS.get(self.mode, MODE_LABELS[MODE_UNKNOWN])

    @property
    def window_hint(self) -> str:
        if self.degraded:
            return MODE_WINDOW_HINTS[MODE_UNKNOWN]
        base = MODE_WINDOW_HINTS.get(self.mode, MODE_WINDOW_HINTS[MODE_UNKNOWN])
        if self.extended:
            return f"{base}（不足最小窗口，已向前延长以纳入隔夜消息）"
        return base

    @property
    def duration_hours(self) -> float:
        return round((self.end - self.start).total_seconds() / 3600, 2)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "market": self.market,
            "mode": self.mode,
            "mode_label": self.label,
            "phase": self.phase,
            "window_hint": self.window_hint,
            "now": self.now.isoformat(timespec="seconds"),
            "start": self.start.isoformat(timespec="seconds"),
            "end": self.end.isoformat(timespec="seconds"),
            "duration_hours": self.duration_hours,
            "degraded": self.degraded,
            "extended": self.extended,
            "degraded_reason": self.degraded_reason,
            "warnings": list(self.warnings),
        }


def resolve_mode(phase: MarketPhase, now: datetime) -> str:
    """把 7 态 MarketPhase 折成 6 种汇总模式。

    周末与节假日都是 ``non_trading``，只能靠 ``weekday()`` 区分 —— 刻意不调
    ``is_market_open()``，它是 fail-open 的，交易日历缺失时周六也会返回 True。
    """
    if phase == MarketPhase.PREMARKET:
        return MODE_PREMARKET
    if phase in _INTRADAY_PHASES:
        return MODE_INTRADAY
    if phase == MarketPhase.POSTMARKET:
        return MODE_POSTMARKET
    if phase == MarketPhase.NON_TRADING:
        return MODE_WEEKEND if now.weekday() >= 5 else MODE_HOLIDAY
    return MODE_UNKNOWN


def resolve_brief_window(
    *,
    market: str = "cn",
    current_time: Optional[datetime] = None,
    fallback_hours: int = DEFAULT_FALLBACK_HOURS,
    max_window_days: int = DEFAULT_MAX_WINDOW_DAYS,
    min_window_minutes: int = DEFAULT_MIN_WINDOW_MINUTES,
) -> BriefWindow:
    """判定当前该出哪种汇总，以及它覆盖的时间窗。"""
    context = build_market_phase_context(market=market, current_time=current_time)
    now = context.market_local_time
    warnings = tuple(context.warnings or ())
    phase = context.phase
    mode = resolve_mode(phase, now)

    def _degraded(reason: str) -> BriefWindow:
        return BriefWindow(
            market=market,
            mode=mode,
            phase=phase.value,
            now=now,
            start=now - timedelta(hours=max(1, int(fallback_hours))),
            end=now,
            degraded=True,
            degraded_reason=reason,
            warnings=warnings,
        )

    # 日历不可用时 build_market_phase_context 的 phase 是 fail-closed（UNKNOWN），
    # 但同一对象里的 effective_daily_bar_date 是 fail-open（退化成自然日），
    # 所以这里必须自己看 warnings，不能只看 phase。
    calendar_broken = any(
        code in warnings for code in ("calendar_unavailable", "calendar_error", "unknown_market")
    )
    if calendar_broken or mode == MODE_UNKNOWN:
        return _degraded("交易日历不可用或时段无法判定，退化为固定回看窗口")

    if mode in (MODE_INTRADAY, MODE_POSTMARKET):
        start = get_session_open(market, current_time=now)
        boundary_reason = "今日开盘时刻不可用"
    else:
        start = get_previous_session_close(market, current_time=now)
        boundary_reason = "上一个交易日收盘时刻不可用"

    if start is None:
        return _degraded(boundary_reason)
    if start >= now:
        # 理论上不该发生；出现说明日历给的边界与当前时刻矛盾，宁可退化也不要负长度窗口
        return _degraded("时间窗边界晚于当前时刻")

    # 窗口下限：刚开盘几分钟时"今日开盘至今"可能只有一两分钟，据此出稿没有意义。
    # 把起点往前放到至少 min_window_minutes，对盘中读者来说恰好覆盖隔夜消息。
    min_span = timedelta(minutes=max(0, int(min_window_minutes)))
    extended = False
    if min_span and now - start < min_span:
        start = now - min_span
        extended = True

    max_span = timedelta(days=max(1, int(max_window_days)))
    if now - start > max_span:
        return BriefWindow(
            market=market, mode=mode, phase=phase.value, now=now,
            start=now - max_span, end=now, degraded=True,
            degraded_reason=f"时间窗超过 {max_window_days} 天上限，已截断",
            warnings=warnings,
        )

    return BriefWindow(
        market=market, mode=mode, phase=phase.value, now=now,
        start=start, end=now, extended=extended, warnings=warnings,
    )
