# -*- coding: utf-8 -*-
"""「我是打板王」API endpoints。

生成是重活（十几个上游 + 几十只候选的日 K + 题材新闻 + 一次模型调用，首次还要回补历史），
实测 30~150 秒，超过前端 30 秒的 HTTP 超时，所以走后台任务：``POST /jobs`` 提交、
``GET /jobs/{id}`` 轮询，结果落库后由 ``GET /overview`` 只读返回。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException, Query

from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.daban import (
    DabanHistoryResponse,
    DabanJobModel,
    DabanJobSubmitRequest,
    DabanJobSubmitResponse,
    DabanOverviewResponse,
    DabanPushResponse,
)
from src.services.daban.jobs import get_job_manager
from src.services.daban.service import DabanService, DabanUnavailableError
from src.services.run_diagnostics import sanitize_diagnostic_text

logger = logging.getLogger(__name__)
router = APIRouter()

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _disabled(exc: Exception) -> HTTPException:
    return HTTPException(status_code=404, detail={"error": "not_enabled", "message": str(exc)})


def _not_found(message: str, code: str = "not_found") -> HTTPException:
    return HTTPException(status_code=404, detail={"error": code, "message": message})


def _bad_request(message: str) -> HTTPException:
    return HTTPException(status_code=400, detail={"error": "validation_error", "message": message})


def _internal_error(message: str, exc: Exception) -> HTTPException:
    sanitized = sanitize_diagnostic_text(str(exc), max_length=300) or "internal daban error"
    logger.error("%s: %s", message, sanitized)
    return HTTPException(status_code=500, detail={"error": "internal_error", "message": f"{message}: internal daban error"})


def _validate_date(value: Optional[str]) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    if not _DATE_RE.match(text):
        raise _bad_request("trade_date 格式应为 YYYY-MM-DD")
    return text


@router.get("/status", summary="Whether the daban module is enabled")
def get_status() -> Dict[str, Any]:
    from src.config import get_config

    return {"enabled": bool(getattr(get_config(), "daban_enabled", True))}


@router.get(
    "/overview",
    response_model=DabanOverviewResponse,
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Read the stored snapshot (never triggers a fetch)",
)
def get_overview(trade_date: Optional[str] = Query(None, description="YYYY-MM-DD；为空取最近一次")) -> DabanOverviewResponse:
    day = _validate_date(trade_date)
    try:
        payload = DabanService().overview(day)
    except DabanUnavailableError as exc:
        raise _disabled(exc)
    except Exception as exc:
        raise _internal_error("Read daban overview failed", exc)
    if payload is None or payload.get("kind") != "full":
        raise _not_found(
            f"{day} 还没有生成过打板王复盘" if day else "还没有生成过打板王复盘，请点击生成",
            code="not_generated",
        )
    return DabanOverviewResponse(**payload)


@router.get(
    "/history",
    response_model=DabanHistoryResponse,
    responses={500: {"model": ErrorResponse}},
    summary="Recent cycle snapshots for the stage strip",
)
def get_history(limit: int = Query(10, ge=1, le=60)) -> DabanHistoryResponse:
    try:
        items = DabanService().history(limit=limit)
    except DabanUnavailableError as exc:
        raise _disabled(exc)
    except Exception as exc:
        raise _internal_error("Read daban history failed", exc)
    return DabanHistoryResponse(items=items)


@router.post(
    "/jobs",
    response_model=DabanJobSubmitResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Submit a background generation job",
)
def submit_job(body: Optional[DabanJobSubmitRequest] = Body(default=None)) -> DabanJobSubmitResponse:
    from src.config import get_config

    if not bool(getattr(get_config(), "daban_enabled", True)):
        raise _disabled(DabanUnavailableError("「我是打板王」已关闭"))
    day = _validate_date(body.trade_date if body else None)
    try:
        result = get_job_manager().submit(trade_date=day)
    except Exception as exc:
        raise _internal_error("Submit daban job failed", exc)
    return DabanJobSubmitResponse(**result)


@router.get(
    "/jobs/{job_id}",
    response_model=DabanJobModel,
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Poll a generation job",
)
def get_job(job_id: str) -> DabanJobModel:
    try:
        job = get_job_manager().get(job_id)
    except Exception as exc:
        raise _internal_error("Read daban job failed", exc)
    if job is None:
        raise _not_found("任务不存在或已过期")
    return DabanJobModel(**job)


@router.get(
    "/jobs",
    response_model=DabanJobModel,
    responses={404: {"model": ErrorResponse}},
    summary="The currently running generation job, if any",
)
def get_active_job() -> DabanJobModel:
    job = get_job_manager().active()
    if job is None:
        raise _not_found("当前没有进行中的生成任务")
    return DabanJobModel(**job)


@router.post(
    "/push",
    response_model=DabanPushResponse,
    responses={500: {"model": ErrorResponse}},
    summary="Push the latest snapshot to configured notification channels",
)
def push() -> DabanPushResponse:
    from src.config import get_config
    from src.services.daban.push import DabanPusher

    config = get_config()
    try:
        payload = DabanService().overview()
    except DabanUnavailableError as exc:
        raise _disabled(exc)
    except Exception as exc:
        raise _internal_error("Read daban overview for push failed", exc)
    try:
        result = DabanPusher(config=config).push(payload)
    except Exception as exc:
        raise _internal_error("Push daban conclusion failed", exc)
    return DabanPushResponse(**result)
