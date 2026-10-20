# -*- coding: utf-8 -*-
"""「我是打板王」刷新的后台任务管理。

结构照搬行业资金的 ``jobs.py``：单 worker 串行、冷却期防上游风控、同一时刻只跑一个任务
（重复提交复用）、任务只在进程内存、带 TTL 清理。区别是提交时可以带 ``trade_date``，
用来生成历史某一天的复盘（同花顺涨停数据中心支持历史日期）。
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
ACTIVE_STATUSES = ("queued", "running")


@dataclass
class DabanJob:
    job_id: str
    trade_date: Optional[str] = None
    status: str = "queued"  # queued | running | succeeded | failed
    phase: str = "queued"
    message: str = "任务已提交，正在排队"
    progress: float = 0.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        from src.services.daban.constants import PHASE_LABELS

        return {
            "job_id": self.job_id,
            "trade_date": self.trade_date,
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


class DabanJobManager:
    _PHASE_PROGRESS = {
        "queued": 0.02,
        "probing": 0.05,
        "backfilling": 0.10,
        "fetching": 0.35,
        "analyzing_cycle": 0.45,
        "selecting": 0.55,
        "reviewing": 0.65,
        "analyzing": 0.75,
        "finalizing": 0.95,
        "succeeded": 1.0,
        "failed": 1.0,
    }

    def __init__(self, service_factory: Optional[Callable[[], Any]] = None):
        self._lock = threading.Lock()
        self._jobs: Dict[str, DabanJob] = {}
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="daban-job")
        self._service_factory = service_factory
        self._last_finished_at: float = 0.0

    def _service(self):
        if self._service_factory is not None:
            return self._service_factory()
        from src.services.daban.service import DabanService

        return DabanService()

    def submit(self, *, trade_date: Optional[str] = None) -> Dict[str, Any]:
        from src.config import get_config

        cooldown = float(getattr(get_config(), "daban_refresh_cooldown_sec", 60))
        with self._lock:
            self._prune_locked()
            active = self._active_job_locked()
            if active is not None:
                return {"job": active.to_dict(), "reused": True, "message": "已有生成任务在进行中，直接复用该任务"}
            elapsed = time.time() - self._last_finished_at
            if self._last_finished_at > 0 and elapsed < cooldown:
                wait = max(1, int(cooldown - elapsed))
                return {
                    "job": None,
                    "reused": False,
                    "cooling_down": True,
                    "retry_after_seconds": wait,
                    "message": f"距上次生成不足 {int(cooldown)} 秒，请 {wait} 秒后再试",
                }
            job = DabanJob(job_id=uuid.uuid4().hex, trade_date=trade_date or None)
            self._jobs[job.job_id] = job
        self._executor.submit(self._run, job.job_id)
        return {"job": job.to_dict(), "reused": False, "message": "生成任务已提交"}

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            self._prune_locked()
            job = self._jobs.get(str(job_id or "").strip())
            return job.to_dict() if job is not None else None

    def active(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._active_job_locked()
            return job.to_dict() if job is not None else None

    def run_sync(self, *, trade_date: Optional[str] = None) -> Dict[str, Any]:
        """给定时任务用：不经过线程池，直接跑一次（仍然尊重「已有任务在跑」）。"""
        with self._lock:
            if self._active_job_locked() is not None:
                return {"ok": False, "skipped": True, "message": "已有生成任务在进行中"}
        try:
            payload = self._service().refresh(trade_date=trade_date)
            return {"ok": True, "payload": payload}
        finally:
            with self._lock:
                self._last_finished_at = time.time()

    def _run(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = "running"
            job.phase = "probing"
            job.message = "开始生成"
            job.updated_at = time.time()
            trade_date = job.trade_date

        def _progress(phase: str, message: str = "") -> None:
            with self._lock:
                current = self._jobs.get(job_id)
                if current is None:
                    return
                current.phase = phase
                if message:
                    current.message = message
                current.progress = max(current.progress, self._PHASE_PROGRESS.get(phase, current.progress))
                current.updated_at = time.time()

        try:
            payload = self._service().refresh(progress=_progress, trade_date=trade_date)
            with self._lock:
                job = self._jobs.get(job_id)
                if job is not None:
                    job.status = "succeeded"
                    job.phase = "succeeded"
                    job.progress = 1.0
                    job.message = "生成完成"
                    job.result = payload
                    job.updated_at = time.time()
        except Exception as exc:
            logger.exception("打板王：生成任务失败")
            with self._lock:
                job = self._jobs.get(job_id)
                if job is not None:
                    job.status = "failed"
                    job.phase = "failed"
                    job.progress = 1.0
                    job.message = "生成失败"
                    job.error = f"{type(exc).__name__}: {str(exc)[:300]}"
                    job.updated_at = time.time()
        finally:
            with self._lock:
                self._last_finished_at = time.time()

    def _active_job_locked(self) -> Optional[DabanJob]:
        for job in self._jobs.values():
            if job.status in ACTIVE_STATUSES:
                return job
        return None

    def _prune_locked(self) -> None:
        cutoff = time.time() - JOB_TTL_SECONDS
        stale = [jid for jid, job in self._jobs.items() if job.updated_at < cutoff and job.status not in ACTIVE_STATUSES]
        for jid in stale:
            self._jobs.pop(jid, None)


_MANAGER: Optional[DabanJobManager] = None
_MANAGER_LOCK = threading.Lock()


def get_job_manager() -> DabanJobManager:
    global _MANAGER
    if _MANAGER is None:
        with _MANAGER_LOCK:
            if _MANAGER is None:
                _MANAGER = DabanJobManager()
    return _MANAGER


__all__ = ["DabanJob", "DabanJobManager", "get_job_manager"]
