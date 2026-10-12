# -*- coding: utf-8 -*-
"""资讯池定时采集的回归测试。

采集是消息面汇总的数据前提：各家快讯接口单次只返回最近一小段，不持续增量落库，
盘前/周末的时间窗查询就是空的。这里覆盖开关、聚合统计、失败不外抛，以及
两个调度入口（常驻服务与 CLI）注册出的任务结构。
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.services.intelligence_collector import (
    COLLECT_TASK_NAME,
    build_intelligence_collect_background_tasks,
    collect_interval_seconds,
    run_collect_once,
)


def _config(**overrides):
    base = {"news_intel_collect_enabled": True, "news_intel_collect_interval_minutes": 5}
    base.update(overrides)
    return SimpleNamespace(**base)


def _fake_service(*, results=None, ensured=None):
    service = MagicMock()
    service.ensure_default_sources_enabled.return_value = ensured or {
        "created_count": 2, "enabled_count": 1,
    }
    service.fetch_enabled_sources.return_value = {"results": results or []}
    return service


class CollectIntervalTest(unittest.TestCase):
    def test_minutes_become_seconds(self) -> None:
        self.assertEqual(collect_interval_seconds(_config(news_intel_collect_interval_minutes=7)), 420)

    def test_invalid_and_too_small_values_fall_back(self) -> None:
        self.assertEqual(collect_interval_seconds(_config(news_intel_collect_interval_minutes="x")), 300)
        self.assertEqual(collect_interval_seconds(_config(news_intel_collect_interval_minutes=0)), 60)


class RunCollectOnceTest(unittest.TestCase):
    def test_disabled_skips_without_touching_the_service(self) -> None:
        with patch("src.services.intelligence_service.IntelligenceService") as service_cls:
            result = run_collect_once(config=_config(news_intel_collect_enabled=False))
        self.assertTrue(result["ok"])
        self.assertTrue(result["skipped"])
        service_cls.assert_not_called()

    def test_aggregates_per_source_results(self) -> None:
        results = [
            {"ok": True, "saved_count": 12},
            {"ok": True, "saved_count": 3},
            {"ok": False, "error": "upstream request failed"},
        ]
        service = _fake_service(results=results)
        with patch("src.services.intelligence_service.IntelligenceService", return_value=service):
            result = run_collect_once(config=_config())
        self.assertEqual(result["sources"], 3)
        self.assertEqual(result["succeeded"], 2)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["saved_count"], 15)
        self.assertEqual(result["created_sources"], 2)
        # 调用方（消息面汇总的空窗口提示）要能把失败原因转述给用户
        self.assertEqual(result["first_error"], "upstream request failed")
        service.ensure_default_sources_enabled.assert_called_once()
        service.fetch_enabled_sources.assert_called_once()

    def test_first_error_is_none_when_every_source_succeeded(self) -> None:
        service = _fake_service(results=[{"ok": True, "saved_count": 1}])
        with patch("src.services.intelligence_service.IntelligenceService", return_value=service):
            result = run_collect_once(config=_config())
        self.assertEqual(result["failed"], 0)
        self.assertIsNone(result["first_error"])

    def test_unexpected_error_is_reported_not_raised(self) -> None:
        """调度器会吞掉异常，所以这里必须自己把失败变成可观测的返回值。"""
        service = _fake_service()
        service.ensure_default_sources_enabled.side_effect = RuntimeError("db locked")
        with patch("src.services.intelligence_service.IntelligenceService", return_value=service):
            result = run_collect_once(config=_config())
        self.assertFalse(result["ok"])
        self.assertIn("db locked", result["error"])


class BuildBackgroundTasksTest(unittest.TestCase):
    def test_disabled_registers_nothing(self) -> None:
        self.assertEqual(
            build_intelligence_collect_background_tasks(
                _config(news_intel_collect_enabled=False),
                config_provider=lambda: _config(news_intel_collect_enabled=False),
            ),
            [],
        )

    def test_task_shape_and_config_is_reread_each_round(self) -> None:
        config = _config(news_intel_collect_interval_minutes=6)
        seen = []

        def provider():
            seen.append(1)
            return config

        entries = build_intelligence_collect_background_tasks(config, config_provider=provider)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["name"], COLLECT_TASK_NAME)
        self.assertEqual(entry["interval_seconds"], 360)
        self.assertTrue(entry["run_immediately"])

        service = _fake_service(results=[{"ok": True, "saved_count": 1}])
        with patch("src.services.intelligence_service.IntelligenceService", return_value=service):
            entry["task"]()
        # 闭包不捕获 config 对象，每轮通过 provider 重新读，设置页改了才能生效
        self.assertEqual(len(seen), 1)
        service.fetch_enabled_sources.assert_called_once()


class RuntimeSchedulerIntegrationTest(unittest.TestCase):
    """常驻服务侧：采集任务与 event monitor 并存，且不重复立即执行。"""

    def _service(self, config):
        from src.services.runtime_scheduler import RuntimeSchedulerService

        service = RuntimeSchedulerService(config_provider=lambda: config)
        service._reload_config = lambda: config
        return service

    def test_collect_task_is_registered_alongside_event_monitor(self) -> None:
        config = SimpleNamespace(
            agent_event_monitor_enabled=False,
            news_intel_collect_enabled=True,
            news_intel_collect_interval_minutes=5,
        )
        service = self._service(config)
        tasks = service._current_background_tasks(config)
        self.assertEqual([item["name"] for item in tasks], [COLLECT_TASK_NAME])
        self.assertTrue(tasks[0]["run_immediately"])

        # 第二次 reconcile 复用闭包，且不再立即执行一遍
        again = service._current_background_tasks(config)
        self.assertIs(again[0]["task"], tasks[0]["task"])
        self.assertFalse(again[0]["run_immediately"])

    def test_interval_change_takes_effect_without_rebuilding_the_task(self) -> None:
        config = SimpleNamespace(
            agent_event_monitor_enabled=False,
            news_intel_collect_enabled=True,
            news_intel_collect_interval_minutes=5,
        )
        service = self._service(config)
        first = service._current_background_tasks(config)[0]
        config.news_intel_collect_interval_minutes = 9
        second = service._current_background_tasks(config)[0]
        self.assertEqual(second["interval_seconds"], 540)
        self.assertIs(second["task"], first["task"])

    def test_disabling_clears_the_cached_task(self) -> None:
        config = SimpleNamespace(
            agent_event_monitor_enabled=False,
            news_intel_collect_enabled=True,
            news_intel_collect_interval_minutes=5,
        )
        service = self._service(config)
        service._current_background_tasks(config)
        config.news_intel_collect_enabled = False
        self.assertEqual(service._current_background_tasks(config), [])
        # 重新开启应视为首次注册，允许立即跑一轮
        config.news_intel_collect_enabled = True
        self.assertTrue(service._current_background_tasks(config)[0]["run_immediately"])


if __name__ == "__main__":
    unittest.main()
