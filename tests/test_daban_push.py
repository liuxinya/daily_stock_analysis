# -*- coding: utf-8 -*-
"""推送摘要与盘后定时生成的门控。"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

from src.services.daban import push


def _payload():
    pick = {
        "code": "002347", "name": "泰尔股份", "mode_label": "弱转强", "action_label": "可执行", "score": 80.8,
        "grade": "A", "position_pct": 25, "consensus": True,
        "reasons": [{"text": "炸板回封的弱板", "principle_ids": ["K27"]}],
        "triggers": [{"text": "竞价由负转正", "principle_ids": ["C04"]}],
    }
    return {
        "trade_date": "2026-10-20",
        "metrics": {"zt_count": 52, "dt_count": 6, "lianban_count": 14, "max_height": 4, "promotion_rate": 0.3,
                    "yzt_premium": 2.77},
        "stage": {"label": "发酵期"},
        "kobe_stage": {"label": "主升"},
        "themes": [{"name": "机器人", "zt_count": 21, "max_boards": 4, "days": 7, "status_label": "主升",
                    "is_main": True, "leader": {"name": "吉大正元"}}],
        "verdict": {"label": "两派共识日", "votes": {"chen": 2, "kobe": 1}, "summary": "两派信号接近"},
        "lanes": {
            "chen": {"stage_label": "退潮期", "position_cap_label": "0（空仓）", "flat": True,
                     "flat_message": "退潮期空仓是最佳策略", "flat_principles": ["C02"], "actionable": [], "trial": []},
            "kobe": {"stage_label": "主升", "position_cap_label": "分 4 票", "flat": False,
                     "actionable": [pick], "trial": []},
        },
        "conclusion": {"summary": "发酵期", "risks": ["机器人第 7 天"]},
        "disclaimers": {"investment": "不构成投资建议"},
    }


def test_markdown_contains_both_lanes_with_citations():
    text = push.render_markdown(_payload())
    assert "陈小群打法" in text and "92科比打法" in text
    assert "空仓：退潮期空仓是最佳策略" in text
    assert "002347 泰尔股份" in text and "两派共识" in text
    assert "92科比·弱转强" in text            # 理由带出处
    assert "晋级率 30%" in text
    assert "不构成投资建议" in text


def test_push_disabled_by_default():
    result = push.DabanPusher(notifier=Mock(), config=SimpleNamespace(daban_push_enabled=False)).push(_payload())
    assert result["skipped"] is True


def test_push_uses_trade_date_dedup_key():
    notifier = Mock()
    notifier.send_with_results.return_value = SimpleNamespace(success=True)
    result = push.DabanPusher(notifier=notifier, config=SimpleNamespace(daban_push_enabled=True)).push(_payload())
    assert result["ok"] is True
    assert notifier.send_with_results.call_args.kwargs["dedup_key"] == "daban-king:2026-10-20"


def test_tick_skips_when_disabled_or_before_close():
    off = SimpleNamespace(daban_enabled=True, daban_auto_generate=False)
    assert push.run_postmarket_tick(config=off)["reason"] == "disabled"
    on = SimpleNamespace(daban_enabled=True, daban_auto_generate=True)
    result = push.run_postmarket_tick(config=on, now=datetime(2026, 10, 20, 14, 0))
    assert result["reason"] == "not_postmarket"
    result = push.run_postmarket_tick(config=on, now=datetime(2026, 10, 18, 16, 0))   # 周日
    assert result["reason"] == "not_postmarket"


def test_background_task_only_when_auto_generate():
    assert push.build_daban_background_tasks(SimpleNamespace(daban_enabled=True, daban_auto_generate=False),
                                             config_provider=lambda: None) == []
    tasks = push.build_daban_background_tasks(SimpleNamespace(daban_enabled=True, daban_auto_generate=True),
                                              config_provider=lambda: None)
    assert tasks[0]["name"] == push.TICK_TASK_NAME and tasks[0]["interval_seconds"] == push.TICK_SECONDS
