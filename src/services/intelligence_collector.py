# -*- coding: utf-8 -*-
"""资讯池定时采集。

与「分析前自动拉取」（``IntelligenceService.refresh_auto_sources``）是两条独立链路：
那条有 60 分钟进程内冷却、且失败也进冷却，目的是别让并发分析反复打外网；
这条是专职采集器，有自己的开关和节奏。

存在的理由：各家快讯接口单次只返回最近一小段（实测新浪约 10 分钟、财联社约 27 分钟、
同花顺约 31 分钟），盘前/盘中/周末的消息面汇总要按时间窗回看，就必须持续增量落库。

本模块被两处共用 —— 常驻服务的 ``RuntimeSchedulerService`` 和 ``main.py --schedule``
的 CLI 路径，以及 market-brief workflow 里的一次性脚本。放在这里而不是各写一份，
是因为仓库里 agent_event_monitor 就是两份重复实现，改一处会漏另一处。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from src.config import Config, get_config

logger = logging.getLogger(__name__)

COLLECT_TASK_NAME = "news_intel_collect"


def collect_enabled(config: Config) -> bool:
    return bool(getattr(config, "news_intel_collect_enabled", False))


def collect_interval_seconds(config: Config) -> int:
    """采集间隔（秒）。调度主循环 30 秒轮询，低于 60 秒没有实际意义。"""
    minutes = getattr(config, "news_intel_collect_interval_minutes", 5)
    try:
        minutes = max(1, int(minutes))
    except (TypeError, ValueError):  # pragma: no cover - defensive branch
        logger.warning("Invalid NEWS_INTEL_COLLECT_INTERVAL_MINUTES=%r; use fallback 5", minutes)
        minutes = 5
    return minutes * 60


def run_collect_once(*, config: Optional[Config] = None, force: bool = False) -> Dict[str, Any]:
    """跑一轮采集：补齐并启用内置源，然后拉取全部启用源。

    ``force=True`` 忽略 ``NEWS_INTEL_COLLECT_ENABLED``，供用户显式触发的路径使用
    （例如页面上点「生成汇总」时窗口为空）—— 那是明确的用户意图，不该被定时开关挡住。

    单源失败由 ``fetch_enabled_sources`` 内部 fail-open 处理；这里只在整体异常时
    返回 ``ok=False``，绝不抛给调度器（抛了也会被 Scheduler 吞掉，但那样就丢了诊断信息）。
    """
    active_config = config or get_config()
    if not force and not collect_enabled(active_config):
        return {"ok": True, "skipped": True, "reason": "disabled"}

    # 延迟导入：intelligence_service 会拉起 storage/DB，不该在模块导入期发生
    from src.services.intelligence_service import IntelligenceService

    service = IntelligenceService(config=active_config)
    try:
        ensured = service.ensure_default_sources_enabled()
        fetched = service.fetch_enabled_sources()
    except Exception as exc:
        logger.warning("Intelligence scheduled collection failed: %s", exc)
        return {"ok": False, "skipped": False, "error": str(exc)}

    results = fetched.get("results") or []
    succeeded = sum(1 for item in results if item.get("ok"))
    saved = sum(int(item.get("saved_count") or 0) for item in results if item.get("ok"))
    return {
        "ok": True,
        "skipped": False,
        "sources": len(results),
        "succeeded": succeeded,
        "failed": len(results) - succeeded,
        "saved_count": saved,
        "created_sources": int(ensured.get("created_count") or 0),
        "enabled_sources": int(ensured.get("enabled_count") or 0),
    }


def build_intelligence_collect_background_tasks(
    config: Config,
    *,
    config_provider: Callable[[], Config],
) -> List[Dict[str, Any]]:
    """构造调度器背景任务，结构与 ``add_background_task`` 的 kwargs 一致。"""
    if not collect_enabled(config):
        return []

    def collect_task() -> None:
        # 每轮重新读配置：间隔和开关都可能在运行期被设置页改掉
        result = run_collect_once(config=config_provider())
        if result.get("skipped"):
            return
        if not result.get("ok"):
            logger.warning("[IntelCollect] collection failed: %s", result.get("error"))
            return
        logger.info(
            "[IntelCollect] sources=%s ok=%s failed=%s saved=%s",
            result.get("sources"), result.get("succeeded"),
            result.get("failed"), result.get("saved_count"),
        )

    return [{
        "task": collect_task,
        "interval_seconds": collect_interval_seconds(config),
        "run_immediately": True,
        "name": COLLECT_TASK_NAME,
    }]
