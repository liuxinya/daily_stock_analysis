# -*- coding: utf-8 -*-
"""CCTV 新闻 AI 分析的后台任务管理。

分析要串行发很多次 LLM 请求，网络抖动时单次可能耗时数分钟。如果放在 HTTP 请求
里同步跑，用户只能对着转圈等，还会被网关超时打断。因此把它做成后台任务：

- 提交即返回 ``job_id``，分析在后台线程里跑。
- 后台线程通过进度回调实时更新 ``phase`` / ``current_chunk`` / ``total_chunks``，
  前端轮询 ``get_job`` 就能显示"共 N 篇、正在分析第 x/y 段"。
- 完成后结果留在 job 里，前端轮询到 ``succeeded`` 再取结果并弹通知。

任务只存在进程内存中（与 :class:`AnalysisTaskQueue` 的定位一致），重启即清空；
带 TTL 清理，避免长期累积。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

JOB_TTL_SECONDS = 30 * 60
MAX_WORKERS = 2

PHASE_LABELS = {
    "queued": "排队中",
    "reading": "读取新闻",
    "analyzing": "分析新闻",
    "finalizing": "汇总校验",
    "succeeded": "已完成",
    "failed": "失败",
}


@dataclass
class AnalysisJob:
    """一次后台分析任务的完整状态。"""

    job_id: str
    range_days: int
    start_date: str
    end_date: str
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
        progress_ratio = 0.0
        if self.status == "succeeded":
            progress_ratio = 1.0
        elif self.total_chunks > 0 and self.phase in ("analyzing", "finalizing"):
            # 读取占 5%，分段解读占 90%，汇总占剩余
            progress_ratio = 0.05 + 0.90 * (self.current_chunk / self.total_chunks)
            progress_ratio = min(0.99, progress_ratio)
        elif self.phase == "reading":
            progress_ratio = 0.05

        return {
            "job_id": self.job_id,
            "status": self.status,
            "phase": self.phase,
            "phase_label": PHASE_LABELS.get(self.phase, self.phase),
            "message": self.message,
            "range_days": self.range_days,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "total_items": self.total_items,
            "total_chunks": self.total_chunks,
            "current_chunk": self.current_chunk,
            "progress": round(progress_ratio, 3),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "result": self.result,
            "error": self.error,
        }


class CctvNewsAnalysisJobManager:
    """进程内单例，管理后台分析任务的生命周期。"""

    _instance: Optional["CctvNewsAnalysisJobManager"] = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._jobs: Dict[str, AnalysisJob] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="cctv-analysis")

    @classmethod
    def get_instance(cls) -> "CctvNewsAnalysisJobManager":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """测试用：等在飞任务结束后丢弃单例。

        ``wait=True`` 是为了不把工作线程留到用例之后：线程里会构造分析器并读取配置，
        跑在已经被下一个用例改动的全局状态上既没意义也难排查。
        """
        with cls._instance_lock:
            if cls._instance is not None:
                cls._instance._executor.shutdown(wait=True)
            cls._instance = None

    def submit(
        self,
        *,
        range_days: int,
        start_date: date,
        end_date: date,
        force: bool,
        analyzer_factory: Any,
    ) -> AnalysisJob:
        """登记任务并在后台线程执行。``analyzer_factory`` 返回一个 CctvNewsAnalyzer。"""
        self._evict_expired()
        job = AnalysisJob(
            job_id=uuid.uuid4().hex,
            range_days=range_days,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
        )
        with self._lock:
            self._jobs[job.job_id] = job
        self._executor.submit(
            self._run, job.job_id, analyzer_factory, start_date, end_date, range_days, force
        )
        return job

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.to_dict() if job else None

    def _run(
        self,
        job_id: str,
        analyzer_factory: Any,
        start_date: date,
        end_date: date,
        range_days: int,
        force: bool,
    ) -> None:
        self._update(job_id, status="running", phase="reading", message="正在读取新闻文字稿")

        def on_progress(event: Dict[str, Any]) -> None:
            self._update(
                job_id,
                phase=str(event.get("phase") or "analyzing"),
                message=str(event.get("message") or ""),
                total_items=int(event.get("total_items") or 0),
                total_chunks=int(event.get("total_chunks") or 0),
                current_chunk=int(event.get("current_chunk") or 0),
            )

        try:
            analyzer = analyzer_factory()
            result = analyzer.analyze(
                start_date=start_date,
                end_date=end_date,
                range_days=range_days,
                force=force,
                progress=on_progress,
            )
            self._update(
                job_id,
                status="succeeded",
                phase="succeeded",
                message="分析完成",
                result=result,
            )
        except Exception as exc:  # noqa: BLE001 - 后台任务须捕获所有异常并落到 job.error
            from src.services.run_diagnostics import sanitize_diagnostic_text

            reason = sanitize_diagnostic_text(str(exc), max_length=200) or "分析失败"
            logger.warning("CCTV 新闻后台分析失败 job=%s: %s", job_id, reason)
            self._update(job_id, status="failed", phase="failed", message=reason, error=reason)

    def _update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            for key, value in fields.items():
                setattr(job, key, value)
            job.updated_at = time.time()

    def _evict_expired(self) -> None:
        cutoff = time.time() - JOB_TTL_SECONDS
        with self._lock:
            stale = [
                jid
                for jid, job in self._jobs.items()
                if job.updated_at < cutoff and job.status in ("succeeded", "failed")
            ]
            for jid in stale:
                self._jobs.pop(jid, None)
