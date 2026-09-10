# -*- coding: utf-8 -*-
"""消息面汇总的后台任务管理。

汇总要串行发多次 LLM 请求，放在 HTTP 请求里同步跑会被网关超时打断，所以做成后台任务：
提交即返回 job_id，前端轮询进度。任务只存在进程内存，重启即清空，带 TTL 清理。

与 CCTV 那份的差别：**同一模式同一窗口正在跑时直接复用已有 job**。CCTV 的
submit 没有去重，重复点会开两个 job（靠 analyzer 内部签名缓存兜），这里的
定时任务与手动按钮可能同时触发，去重放在 job 层更直接。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

JOB_TTL_SECONDS = 30 * 60
MAX_WORKERS = 2

PHASE_LABELS = {
    "queued": "排队中",
    "reading": "读取资讯",
    "analyzing": "分析消息面",
    "finalizing": "汇总校验",
    "succeeded": "已完成",
    "failed": "失败",
}

ACTIVE_STATUSES = ("queued", "running")


@dataclass
class BriefJob:
    """一次后台汇总任务的完整状态。"""

    job_id: str
    mode: str
    mode_label: str = ""
    window_start: Optional[str] = None
    window_end: Optional[str] = None
    status: str = "queued"  # queued | running | succeeded | failed
    phase: str = "queued"
    message: str = "任务已提交，正在排队"
    total_items: int = 0
    total_chunks: int = 0
    current_chunk: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        if self.status == "succeeded":
            progress = 1.0
        elif self.total_chunks > 0 and self.phase in ("analyzing", "finalizing"):
            progress = min(0.99, 0.05 + 0.90 * (self.current_chunk / self.total_chunks))
        elif self.phase == "reading":
            progress = 0.05
        else:
            progress = 0.0
        return {
            "job_id": self.job_id,
            "mode": self.mode,
            "mode_label": self.mode_label,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "status": self.status,
            "phase": self.phase,
            "phase_label": PHASE_LABELS.get(self.phase, self.phase),
            "message": self.message,
            "total_items": self.total_items,
            "total_chunks": self.total_chunks,
            "current_chunk": self.current_chunk,
            "progress": round(progress, 4),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "result": self.result,
            "error": self.error,
        }


class MarketBriefJobManager:
    """进程内单例的后台任务管理器。"""

    _instance: Optional["MarketBriefJobManager"] = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._jobs: Dict[str, BriefJob] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="market-brief-job")

    @classmethod
    def get_instance(cls) -> "MarketBriefJobManager":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """测试用：等线程池收尾再丢掉实例，避免线程泄漏到下一个用例。"""
        with cls._instance_lock:
            if cls._instance is not None:
                cls._instance._executor.shutdown(wait=True)
            cls._instance = None

    def submit(self, *, service=None, force: bool = False) -> BriefJob:
        from src.services.market_brief_service import MarketBriefService

        active_service = service or MarketBriefService()
        window = active_service.current_window()

        self._evict_expired()
        with self._lock:
            # 同一模式同一窗口已在跑就复用，避免定时任务与手动按钮撞车
            for existing in self._jobs.values():
                if (
                    existing.status in ACTIVE_STATUSES
                    and existing.mode == window.mode
                    and existing.window_start == window.start.isoformat(timespec="seconds")
                ):
                    return existing

            job = BriefJob(
                job_id=uuid.uuid4().hex,
                mode=window.mode,
                mode_label=window.label,
                window_start=window.start.isoformat(timespec="seconds"),
                window_end=window.end.isoformat(timespec="seconds"),
            )
            self._jobs[job.job_id] = job

        self._executor.submit(self._run, job.job_id, active_service, force)
        return job

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.to_dict() if job is not None else None

    def _run(self, job_id: str, service, force: bool) -> None:
        from src.services.market_brief_analyzer import (
            MarketBriefAnalyzerError,
            MarketBriefUnavailableError,
        )
        from src.services.run_diagnostics import sanitize_diagnostic_text

        def on_progress(**fields: Any) -> None:
            self._update(job_id, **fields)

        try:
            self._update(job_id, status="running", phase="reading", message="正在读取窗口内资讯")
            window = service.current_window()
            items, _ = service.ensure_items(window)
            self._update(job_id, total_items=len(items))
            payload = service.analyzer.analyze(
                window=window, items=items, force=force, progress=on_progress,
            )
            self._update(
                job_id, status="succeeded", phase="succeeded",
                message="汇总已完成", result=payload,
            )
        except (MarketBriefAnalyzerError, MarketBriefUnavailableError) as exc:
            self._update(job_id, status="failed", phase="failed", message="汇总失败", error=str(exc))
        except Exception as exc:
            self._update(
                job_id, status="failed", phase="failed", message="汇总失败",
                error=sanitize_diagnostic_text(str(exc), max_length=300) or "internal market brief error",
            )

    def _update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            for key, value in fields.items():
                if hasattr(job, key):
                    setattr(job, key, value)
            job.updated_at = time.time()

    def _evict_expired(self) -> None:
        cutoff = time.time() - JOB_TTL_SECONDS
        with self._lock:
            for job_id in [
                key for key, job in self._jobs.items()
                if job.status in ("succeeded", "failed") and job.updated_at < cutoff
            ]:
                self._jobs.pop(job_id, None)
