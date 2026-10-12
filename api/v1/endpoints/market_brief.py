# -*- coding: utf-8 -*-
"""盘前/盘中/周末消息面 AI 汇总 API endpoints。"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.market_brief import (
    MarketBriefJobRequest,
    MarketBriefJobResponse,
    MarketBriefResponse,
    MarketBriefWindowModel,
)
from src.services.market_brief_analyzer import (
    MarketBriefAnalyzerError,
    MarketBriefUnavailableError,
)
from src.services.market_brief_jobs import MarketBriefJobManager
from src.services.market_brief_service import MarketBriefService, MarketBriefServiceError
from src.services.run_diagnostics import sanitize_diagnostic_text

logger = logging.getLogger(__name__)
router = APIRouter()


def _bad_request(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail={"error": "validation_error", "message": str(exc)})


def _not_found(message: str) -> HTTPException:
    return HTTPException(status_code=404, detail={"error": "not_found", "message": message})


def _unavailable(exc: Exception) -> HTTPException:
    return HTTPException(status_code=503, detail={"error": "analysis_unavailable", "message": str(exc)})


def _internal_error(message: str, exc: Exception) -> HTTPException:
    sanitized = sanitize_diagnostic_text(str(exc), max_length=300) or "internal market brief error"
    logger.error("%s: %s", message, sanitized)
    return HTTPException(
        status_code=500,
        detail={"error": "internal_error", "message": f"{message}: internal market brief service error"},
    )


@router.get(
    "/window",
    response_model=MarketBriefWindowModel,
    responses={500: {"model": ErrorResponse}},
    summary="Resolve the current brief mode and time window",
)
def get_window() -> MarketBriefWindowModel:
    try:
        return MarketBriefWindowModel(**MarketBriefService().current_window().to_dict())
    except Exception as exc:
        raise _internal_error("Resolve market brief window failed", exc)


@router.get(
    "/latest",
    response_model=MarketBriefResponse,
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Get the most recent brief",
)
def get_latest(mode: Optional[str] = Query(None, max_length=32)) -> MarketBriefResponse:
    try:
        payload = MarketBriefService().latest(mode=mode)
    except Exception as exc:
        raise _internal_error("Load latest market brief failed", exc)
    if payload is None:
        raise _not_found("还没有生成过消息面汇总")
    return MarketBriefResponse(**payload)


@router.post(
    "/jobs",
    response_model=MarketBriefJobResponse,
    responses={
        400: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
    summary="Submit a background brief job",
)
def submit_job(payload: Optional[MarketBriefJobRequest] = None) -> MarketBriefJobResponse:
    request = payload or MarketBriefJobRequest()
    try:
        service = MarketBriefService()
        window = service.current_window()
        # 关闭直拉（预算 0）时沿用原来的同步预检：空窗口在这里就 400，不变成必然失败的 job。
        # 开着直拉时**不能**在这里预检 —— 那会同步打一轮上游（预算 30 秒），
        # 点「生成汇总」要等半分钟才拿到 job_id；素材判定交给 job 自己（它的
        # fetching / reading 阶段会把真实原因写进 job.error，前端已有渲染）。
        if service.live_fetch_budget <= 0:
            service.load_items_or_raise(window)
        job = MarketBriefJobManager.get_instance().submit(service=service, force=request.force)
        return MarketBriefJobResponse(**job.to_dict())
    except (MarketBriefAnalyzerError, MarketBriefServiceError) as exc:
        raise _bad_request(exc)
    except MarketBriefUnavailableError as exc:
        raise _unavailable(exc)
    except Exception as exc:
        raise _internal_error("Submit market brief job failed", exc)


@router.get(
    "/jobs/{job_id}",
    response_model=MarketBriefJobResponse,
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Poll a background brief job",
)
def get_job(job_id: str) -> MarketBriefJobResponse:
    try:
        job = MarketBriefJobManager.get_instance().get_job(job_id)
    except Exception as exc:
        raise _internal_error("Load market brief job failed", exc)
    if job is None:
        raise _not_found("任务不存在或已过期")
    return MarketBriefJobResponse(**job)
