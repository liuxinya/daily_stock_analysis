# -*- coding: utf-8 -*-
"""推送摘要与盘后定时生成的门控。"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

from src.services.daban import push


def _payload():
    return {
        "trade_date": "2026-10-20",
        "metrics": {"zt_count": 52, "dt_count": 6, "lianban_count": 14, "max_height": 4, "promotion_rate": 0.3,
                    "yzt_premium": 2.77},
        "stage": {"label": "发酵期"},
        "kobe_stage": {"label": "主升"},
        "themes": [{"name": "机器人", "zt_count": 21, "max_boards": 4, "days": 7, "status_label": "主升",
                    "is_main": True, "leader": {"name": "吉大正元"}, "commonality": {"label": "共性相同（补涨 / 发散）"},
                    "expectation_text": "龙头高开 5% 以上算超预期"}],
        "worldview": {"for_date": "2026-10-21", "boundary_text": "明天可以推荐",
                      "dims": [{"key": "node", "value": "龙头见顶杀高位", "detail": "前一天最高 5 板"}]},
        "focus_review": {"count": 1, "items": [{"code": "002347", "name": "泰尔股份", "theme_name": "机器人",
                                                "init_score": 5, "close_score": 0, "cost": 6.4, "sealed_close": True,
                                                "h1_ret": None}]},
        "watchlist": {"items": [{"code": "603278", "name": "大业股份", "modes": ["弱转强"], "theme_name": "机器人",
                                 "triggers": [{"text": "竞价由负转正", "principle_ids": ["C04"]}]}]},
        "conclusion": {"summary": "发酵期", "risks": ["机器人第 7 天"]},
        "disclaimers": {"investment": "不构成投资建议"},
    }


def test_markdown_has_worldview_focus_and_watchlist():
    text = push.render_markdown(_payload())
    assert "明天（2026-10-21）的边界**：明天可以推荐" in text and "前一天最高 5 板" in text
    assert "今天「重点」推荐 1 只" in text and "002347 泰尔股份" in text and "初始 5 分→收盘 0 分" in text
    assert "明天观察清单" in text and "603278 大业股份" in text and "陈小群·弱转强打板" in text
    assert "预期：龙头高开" in text and "晋级率 30%" in text and "不构成投资建议" in text
    assert "仓位" not in text


def test_push_disabled_by_default():
    result = push.DabanPusher(notifier=Mock(), config=SimpleNamespace(daban_push_enabled=False)).push(_payload())
    assert result["skipped"] is True


def test_push_uses_trade_date_dedup_key():
    notifier = Mock()
    notifier.send_with_results.return_value = SimpleNamespace(success=True)
    result = push.DabanPusher(notifier=notifier, config=SimpleNamespace(daban_push_enabled=True)).push(_payload())
    assert result["ok"] is True
    assert notifier.send_with_results.call_args.kwargs["dedup_key"] == "daban-king:2026-10-20"


def _stub_tick(monkeypatch, *, existing=None):
    """盘后任务的库和生成任务打桩：``existing`` = {交易日: (kind, intraday)}。返回被要求生成的交易日列表。"""
    from src.repositories import daban_repo
    from src.services.daban import jobs

    calls = []
    rows = {day: SimpleNamespace(kind=kind, intraday=intraday) for day, (kind, intraday) in (existing or {}).items()}
    monkeypatch.setattr(daban_repo, "DabanRepository", lambda: SimpleNamespace(snapshot_for_trade_date=rows.get))
    manager = SimpleNamespace(run_sync=lambda trade_date=None: calls.append(trade_date) or {"ok": True, "payload": {}})
    monkeypatch.setattr(jobs, "get_job_manager", lambda: manager)
    return calls


def test_tick_skips_when_disabled_or_during_session(monkeypatch):
    calls = _stub_tick(monkeypatch)
    off = SimpleNamespace(daban_enabled=True, daban_auto_generate=False)
    assert push.run_postmarket_tick(config=off)["reason"] == "disabled"
    on = SimpleNamespace(daban_enabled=True, daban_auto_generate=True)
    result = push.run_postmarket_tick(config=on, now=datetime(2026, 10, 20, 14, 0))
    assert result["reason"] == "not_postmarket" and calls == []


def test_tick_generates_today_after_close(monkeypatch):
    calls = _stub_tick(monkeypatch, existing={"2026-10-19": ("full", False)})
    on = SimpleNamespace(daban_enabled=True, daban_auto_generate=True)
    result = push.run_postmarket_tick(config=on, now=datetime(2026, 10, 20, 15, 40))
    assert result["generated"] and result["catch_up"] is False and calls == ["2026-10-20"]


def test_tick_catches_up_a_missed_close_before_the_open(monkeypatch):
    """上一个交易日收盘后没生成过（只有盘中快照）：开盘前和周末补上；已生成过的不重复。"""
    on = SimpleNamespace(daban_enabled=True, daban_auto_generate=True)
    calls = _stub_tick(monkeypatch, existing={"2026-10-16": ("full", True)})
    result = push.run_postmarket_tick(config=on, now=datetime(2026, 10, 19, 8, 30))      # 周一开盘前
    assert result["catch_up"] is True and calls == ["2026-10-16"]
    calls = _stub_tick(monkeypatch)
    push.run_postmarket_tick(config=on, now=datetime(2026, 10, 18, 16, 0))                # 周日
    assert calls == ["2026-10-16"]
    calls = _stub_tick(monkeypatch, existing={"2026-10-16": ("full", False)})
    result = push.run_postmarket_tick(config=on, now=datetime(2026, 10, 19, 8, 30))
    assert result["reason"] == "already_generated" and calls == []
    assert push.run_postmarket_tick(config=on, now=datetime(2026, 10, 19, 9, 5))["reason"] == "not_postmarket"


def test_background_task_only_when_auto_generate():
    assert push.build_daban_background_tasks(SimpleNamespace(daban_enabled=True, daban_auto_generate=False),
                                             config_provider=lambda: None) == []
    tasks = push.build_daban_background_tasks(SimpleNamespace(daban_enabled=True, daban_auto_generate=True),
                                              config_provider=lambda: None)
    assert tasks[0]["name"] == push.TICK_TASK_NAME and tasks[0]["interval_seconds"] == push.TICK_SECONDS
    assert tasks[0]["run_immediately"] is True       # 启动就检查一次，开盘前重启也能补上上一个交易日
