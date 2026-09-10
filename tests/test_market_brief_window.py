# -*- coding: utf-8 -*-
"""消息面汇总的时点与时间窗判定测试。

重点覆盖三类边界：周末与节假日的区分（MarketPhase 把两者都归成 non_trading）、
长假窗口、以及交易日历不可用时的降级。
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import patch

from src.core.trading_calendar import MarketPhase
from src.services.market_brief_window import (
    DEFAULT_FALLBACK_HOURS,
    MODE_HOLIDAY,
    MODE_INTRADAY,
    MODE_POSTMARKET,
    MODE_PREMARKET,
    MODE_UNKNOWN,
    MODE_WEEKEND,
    resolve_brief_window,
    resolve_mode,
)

SH = ZoneInfo("Asia/Shanghai")


def _at(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=SH)


class ResolveModeTest(unittest.TestCase):
    def test_intraday_phases_collapse_into_one_mode(self) -> None:
        monday = _at(2026, 9, 7, 10, 30)
        for phase in (MarketPhase.INTRADAY, MarketPhase.LUNCH_BREAK, MarketPhase.CLOSING_AUCTION):
            self.assertEqual(resolve_mode(phase, monday), MODE_INTRADAY, phase)

    def test_non_trading_splits_by_weekday(self) -> None:
        """周末与节假日在 MarketPhase 里都是 non_trading，只能靠 weekday 区分。"""
        self.assertEqual(resolve_mode(MarketPhase.NON_TRADING, _at(2026, 9, 5, 9, 30)), MODE_WEEKEND)
        self.assertEqual(resolve_mode(MarketPhase.NON_TRADING, _at(2026, 9, 6, 9, 30)), MODE_WEEKEND)
        # 国庆 10-01 是周四
        self.assertEqual(resolve_mode(MarketPhase.NON_TRADING, _at(2026, 10, 1, 9, 30)), MODE_HOLIDAY)

    def test_unknown_phase_maps_to_unknown_mode(self) -> None:
        self.assertEqual(resolve_mode(MarketPhase.UNKNOWN, _at(2026, 9, 7, 10, 0)), MODE_UNKNOWN)


class ResolveBriefWindowTest(unittest.TestCase):
    def test_premarket_window_starts_at_the_previous_close(self) -> None:
        window = resolve_brief_window(market="cn", current_time=_at(2026, 9, 7, 8, 40))
        self.assertEqual(window.mode, MODE_PREMARKET)
        self.assertFalse(window.degraded)
        # 周五 15:00 收盘，因此盘前窗口天然覆盖整个周末
        self.assertEqual(window.start, _at(2026, 9, 4, 15, 0))
        self.assertEqual(window.end, _at(2026, 9, 7, 8, 40))

    def test_intraday_window_starts_at_todays_open(self) -> None:
        for when in (_at(2026, 9, 7, 12, 0), _at(2026, 9, 7, 14, 58)):
            window = resolve_brief_window(market="cn", current_time=when)
            self.assertEqual(window.mode, MODE_INTRADAY, when)
            self.assertEqual(window.start, _at(2026, 9, 7, 9, 30), when)
            self.assertFalse(window.extended, when)

    def test_thin_intraday_window_is_extended_to_cover_overnight(self) -> None:
        """刚开盘时"今日开盘至今"只有几分钟，据此出稿没有意义，起点要往前放。"""
        window = resolve_brief_window(
            market="cn", current_time=_at(2026, 9, 7, 9, 35), min_window_minutes=90,
        )
        self.assertEqual(window.mode, MODE_INTRADAY)
        self.assertTrue(window.extended)
        self.assertEqual(window.start, _at(2026, 9, 7, 8, 5))
        self.assertIn("已向前延长", window.window_hint)
        # 延长不是降级：窗口仍然可用，不该被标成 degraded
        self.assertFalse(window.degraded)

    def test_min_window_can_be_disabled(self) -> None:
        window = resolve_brief_window(
            market="cn", current_time=_at(2026, 9, 7, 9, 35), min_window_minutes=0,
        )
        self.assertFalse(window.extended)
        self.assertEqual(window.start, _at(2026, 9, 7, 9, 30))

    def test_postmarket_window_also_starts_at_todays_open(self) -> None:
        window = resolve_brief_window(market="cn", current_time=_at(2026, 9, 7, 20, 0))
        self.assertEqual(window.mode, MODE_POSTMARKET)
        self.assertEqual(window.start, _at(2026, 9, 7, 9, 30))

    def test_weekend_window_covers_since_friday_close(self) -> None:
        window = resolve_brief_window(market="cn", current_time=_at(2026, 9, 6, 21, 0))
        self.assertEqual(window.mode, MODE_WEEKEND)
        self.assertEqual(window.start, _at(2026, 9, 4, 15, 0))
        self.assertAlmostEqual(window.duration_hours, 54.0, places=1)

    def test_holiday_window_skips_the_whole_break(self) -> None:
        window = resolve_brief_window(market="cn", current_time=_at(2026, 10, 2, 9, 30))
        self.assertEqual(window.mode, MODE_HOLIDAY)
        self.assertEqual(window.start, _at(2026, 9, 30, 15, 0))

    def test_first_session_after_a_long_break_looks_back_over_the_break(self) -> None:
        window = resolve_brief_window(market="cn", current_time=_at(2026, 10, 8, 8, 40))
        self.assertEqual(window.mode, MODE_PREMARKET)
        self.assertEqual(window.start, _at(2026, 9, 30, 15, 0))
        self.assertFalse(window.degraded)
        self.assertGreater(window.duration_hours, 24 * 7)

    def test_window_is_capped(self) -> None:
        window = resolve_brief_window(
            market="cn", current_time=_at(2026, 10, 8, 8, 40), max_window_days=2,
        )
        self.assertTrue(window.degraded)
        self.assertIn("上限", window.degraded_reason)
        self.assertEqual(window.end - window.start, timedelta(days=2))

    def test_unknown_market_is_fail_closed_into_a_fixed_lookback(self) -> None:
        now = _at(2026, 9, 7, 10, 0)
        window = resolve_brief_window(market="xx", current_time=now)
        self.assertEqual(window.mode, MODE_UNKNOWN)
        self.assertTrue(window.degraded)
        self.assertIn("unknown_market", window.warnings)
        self.assertEqual(window.start, now - timedelta(hours=DEFAULT_FALLBACK_HOURS))

    def test_calendar_unavailable_degrades_instead_of_guessing(self) -> None:
        """日历缺失时 effective_daily_bar_date 是 fail-open 的自然日，不能拿来当窗口边界。"""
        now = _at(2026, 9, 7, 8, 40)
        with patch("src.core.trading_calendar._XCALS_AVAILABLE", False):
            window = resolve_brief_window(market="cn", current_time=now, fallback_hours=12)
        self.assertTrue(window.degraded)
        self.assertIn("calendar_unavailable", window.warnings)
        self.assertEqual(window.start, now - timedelta(hours=12))

    def test_missing_boundary_degrades(self) -> None:
        now = _at(2026, 9, 7, 8, 40)
        with patch("src.services.market_brief_window.get_previous_session_close", return_value=None):
            window = resolve_brief_window(market="cn", current_time=now)
        self.assertTrue(window.degraded)
        self.assertIn("收盘时刻不可用", window.degraded_reason)

    def test_to_dict_is_json_safe(self) -> None:
        payload = resolve_brief_window(market="cn", current_time=_at(2026, 9, 7, 8, 40)).to_dict()
        self.assertEqual(payload["mode"], MODE_PREMARKET)
        self.assertEqual(payload["mode_label"], "盘前")
        self.assertTrue(payload["start"].startswith("2026-09-04T15:00:00"))
        self.assertEqual(payload["window_hint"], "上一个交易日收盘至今")
        self.assertIsInstance(payload["warnings"], list)


if __name__ == "__main__":
    unittest.main()
