# -*- coding: utf-8 -*-
"""行业资金刷新的后台任务管理。

刷新一次要并行打 6 个上游再调一次模型，实测 30~90 秒，放在 HTTP 请求里同步跑一定会
被前端 30 秒的超时切断，所以做成后台任务：提交即返回 job_id，前端轮询进度。

与 ``market_brief_jobs`` 的差别：

- **带冷却期**。上游（同花顺 / 交易所）对频率敏感，而且同日重复刷新还会污染 ETF 份额
  分位的样本，所以两次刷新之间强制间隔 ``industry_funds_refresh_cooldown_sec``。
- **去重在 job 层**。同一时刻只允许一个刷新任务在跑，重复提交直接复用已有 job。

任务只存在进程内存，重启即清空，带 TTL 清理。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

JOB_TTL_SECONDS = 30 * 60
MAX_WORKERS = 1  # 刷新是重活且上游对频率敏感，串行执行
ACTIVE_STATUSES = ("queued", "running")


@dataclass
class RefreshJob:
    """一次后台刷新任务的完整状态。"""

    job_id: str
    status: str = "queued"  # queued | running | succeeded | failed
    phase: str = "queued"
    message: str = "任务已提交，正在排队"
    progress: float = 0.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        from src.services.industry_funds.constants import PHASE_LABELS

        return {
            "job_id": self.job_id,
            "status": self.status,
            "phase": self.phase,
            "phase_label": PHASE_LABELS.get(self.phase, self.phase),
            "message": self.message,
            "progress": round(self.progress, 4),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "result": self.result,
            "error": self.error,
        }


class IndustryFundsJobManager:
    """进程内的刷新任务管理器。"""

    # 每个抓取阶段推进的进度档位，让进度条看起来是在动的。
    _PHASE_PROGRESS = {
        "queued": 0.02,
        "fetching_flow": 0.05,
        "fetching_lhb": 0.20,
        "fetching_etf": 0.35,
        "fetching_margin": 0.50,
        "fetching_valuation": 0.62,
        "scoring": 0.75,
        "analyzing": 0.85,
        "finalizing": 0.95,
        "succeeded": 1.0,
        "failed": 1.0,
    }

    def __init__(self, service_factory: Optional[Callable[[], Any]] = None):
        self._lock = threading.Lock()
        self._jobs: Dict[str, RefreshJob] = {}
        self._executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="industry-funds-job")
        self._service_factory = service_factory
        self._last_finished_at: float = 0.0

    # ------------------------------------------------------------------ 依赖

    def _service(self):
        if self._service_factory is not None:
            return self._service_factory()
        from src.services.industry_funds.service import IndustryFundsService

        return IndustryFundsService()

    # ------------------------------------------------------------------ 提交

    def submit(self, *, force: bool = False) -> Dict[str, Any]:
        """提交一次刷新。已有任务在跑时直接返回它（去重）。"""
        from src.config import get_config

        config = get_config()
        cooldown = float(getattr(config, "industry_funds_refresh_cooldown_sec", 60))

        with self._lock:
            self._prune_locked()

            active = self._active_job_locked()
            if active is not None:
                return {
                    "job": active.to_dict(),
                    "reused": True,
                    "message": "已有刷新任务在进行中，直接复用该任务",
                }

            # 冷却是防上游风控的硬约束，force 也不跳过 —— 否则前端连点就能打爆上游。
            elapsed = time.time() - self._last_finished_at
            if self._last_finished_at > 0 and elapsed < cooldown:
                wait = int(cooldown - elapsed)
                return {
                    "job": None,
                    "reused": False,
                    "cooling_down": True,
                    "retry_after_seconds": max(1, wait),
                    "message": f"距上次刷新不足 {int(cooldown)} 秒，请 {max(1, wait)} 秒后再试",
                }

            job = RefreshJob(job_id=uuid.uuid4().hex)
            self._jobs[job.job_id] = job

        self._executor.submit(self._run, job.job_id)
        return {"job": job.to_dict(), "reused": False, "message": "刷新任务已提交"}

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            self._prune_locked()
            job = self._jobs.get(str(job_id or "").strip())
            return job.to_dict() if job is not None else None

    def active(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._active_job_locked()
            return job.to_dict() if job is not None else None

    # ------------------------------------------------------------------ 执行

    def _run(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = "running"
            job.phase = "fetching_flow"
            job.message = "开始抓取数据源"
            job.updated_at = time.time()

        def _progress(phase: str, message: str = "") -> None:
            with self._lock:
                current = self._jobs.get(job_id)
                if current is None:
                    return
                current.phase = phase
                if message:
                    current.message = message
                current.progress = max(
                    current.progress, self._PHASE_PROGRESS.get(phase, current.progress)
                )
                current.updated_at = time.time()

        try:
            payload = self._service().refresh(progress=_progress)
            with self._lock:
                job = self._jobs.get(job_id)
                if job is not None:
                    job.status = "succeeded"
                    job.phase = "succeeded"
                    job.progress = 1.0
                    job.message = "刷新完成"
                    job.result = payload
                    job.updated_at = time.time()
        except Exception as exc:
            logger.exception("行业资金：刷新任务失败")
            with self._lock:
                job = self._jobs.get(job_id)
                if job is not None:
                    job.status = "failed"
                    job.phase = "failed"
                    job.progress = 1.0
                    job.message = "刷新失败"
                    job.error = f"{type(exc).__name__}: {str(exc)[:300]}"
                    job.updated_at = time.time()
        finally:
            with self._lock:
                self._last_finished_at = time.time()

    # ------------------------------------------------------------------ 内部

    def _active_job_locked(self) -> Optional[RefreshJob]:
        for job in self._jobs.values():
            if job.status in ACTIVE_STATUSES:
                return job
        return None

    def _prune_locked(self) -> None:
        cutoff = time.time() - JOB_TTL_SECONDS
        stale = [
            job_id
            for job_id, job in self._jobs.items()
            if job.updated_at < cutoff and job.status not in ACTIVE_STATUSES
        ]
        for job_id in stale:
            self._jobs.pop(job_id, None)


# 进程级单例：job 状态要跨请求存活，必须共用同一个管理器。
_MANAGER: Optional[IndustryFundsJobManager] = None
_MANAGER_LOCK = threading.Lock()


def get_job_manager() -> IndustryFundsJobManager:
    global _MANAGER
    if _MANAGER is None:
        with _MANAGER_LOCK:
            if _MANAGER is None:
                _MANAGER = IndustryFundsJobManager()
    return _MANAGER


__all__ = ["IndustryFundsJobManager", "RefreshJob", "get_job_manager"]
