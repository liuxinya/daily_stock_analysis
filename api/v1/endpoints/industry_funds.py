# -*- coding: utf-8 -*-
"""行业资金（主力资金 / 现阶段方向）API endpoints。

刷新是重活（并行打 6 个上游 + 一次模型调用，实测 30~90 秒），而前端 HTTP 客户端超时是
30 秒，所以刷新走后台任务：``POST /jobs`` 提交、``GET /jobs/{id}`` 轮询，结果落到
``GET /overview`` 的缓存里。这一点不能改成同步接口。
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query

from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.industry_funds import (
    IndustryFundsHistoryResponse,
    IndustryFundsJobModel,
    IndustryFundsJobSubmitResponse,
    IndustryFundsOverviewResponse,
    IndustryFundsPushResponse,
)
from src.services.industry_funds.jobs import get_job_manager
from src.services.industry_funds.service import (
    IndustryFundsService,
    IndustryFundsUnavailableError,
)
from src.services.run_diagnostics import sanitize_diagnostic_text

logger = logging.getLogger(__name__)
router = APIRouter()


def _disabled(exc: IndustryFundsUnavailableError) -> HTTPException:
    return HTTPException(status_code=404, detail={"error": "not_enabled", "message": str(exc)})


def _not_found(message: str) -> HTTPException:
    return HTTPException(status_code=404, detail={"error": "not_found", "message": message})


def _internal_error(message: str, exc: Exception) -> HTTPException:
    sanitized = sanitize_diagnostic_text(str(exc), max_length=300) or "internal industry funds error"
    logger.error("%s: %s", message, sanitized)
    return HTTPException(
        status_code=500,
        detail={"error": "internal_error", "message": f"{message}: internal industry funds error"},
    )


@router.get(
    "/status",
    summary="Whether the industry funds module is enabled",
)
def get_status() -> Dict[str, Any]:
    """给左侧菜单判断是否显示入口用（与选股菜单的做法一致）。"""
    from src.config import get_config

    config = get_config()
    return {"enabled": bool(getattr(config, "industry_funds_enabled", True))}


@router.get(
    "/overview",
    response_model=IndustryFundsOverviewResponse,
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Read the cached snapshot (never triggers a fetch)",
)
def get_overview() -> IndustryFundsOverviewResponse:
    """只读缓存，秒回。没有缓存时返回 404，由前端提示「点刷新生成」。"""
    try:
        payload = IndustryFundsService().overview()
    except IndustryFundsUnavailableError as exc:
        raise _disabled(exc)
    except Exception as exc:
        raise _internal_error("Read industry funds overview failed", exc)
    if payload is None:
        raise _not_found("还没有生成过行业资金快照，请点击刷新生成")
    return IndustryFundsOverviewResponse(**payload)


@router.get(
    "/history",
    response_model=IndustryFundsHistoryResponse,
    responses={500: {"model": ErrorResponse}},
    summary="Recent snapshot summaries for the trend strip",
)
def get_history(limit: int = Query(30, ge=1, le=250)) -> IndustryFundsHistoryResponse:
    try:
        items = IndustryFundsService().history(limit=limit)
    except IndustryFundsUnavailableError as exc:
        raise _disabled(exc)
    except Exception as exc:
        raise _internal_error("Read industry funds history failed", exc)
    return IndustryFundsHistoryResponse(items=items)


@router.post(
    "/jobs",
    response_model=IndustryFundsJobSubmitResponse,
    responses={500: {"model": ErrorResponse}},
    summary="Submit a background refresh job",
)
def submit_job() -> IndustryFundsJobSubmitResponse:
    """提交刷新。已有任务在跑或处于冷却期时不会新建任务，如实返回原因。"""
    from src.config import get_config

    config = get_config()
    if not bool(getattr(config, "industry_funds_enabled", True)):
        raise _disabled(IndustryFundsUnavailableError("行业资金模块已关闭"))
    try:
        result = get_job_manager().submit()
    except Exception as exc:
        raise _internal_error("Submit industry funds refresh job failed", exc)
    return IndustryFundsJobSubmitResponse(**result)


@router.get(
    "/jobs/{job_id}",
    response_model=IndustryFundsJobModel,
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Poll a refresh job",
)
def get_job(job_id: str) -> IndustryFundsJobModel:
    try:
        job = get_job_manager().get(job_id)
    except Exception as exc:
        raise _internal_error("Read industry funds job failed", exc)
    if job is None:
        raise _not_found("任务不存在或已过期")
    return IndustryFundsJobModel(**job)


@router.get(
    "/jobs",
    response_model=IndustryFundsJobModel,
    responses={404: {"model": ErrorResponse}},
    summary="The currently running refresh job, if any",
)
def get_active_job() -> IndustryFundsJobModel:
    """页面加载时用它接上「刷新到一半被关掉页面」的进度。"""
    job = get_job_manager().active()
    if job is None:
        raise _not_found("当前没有进行中的刷新任务")
    return IndustryFundsJobModel(**job)


@router.post(
    "/push",
    response_model=IndustryFundsPushResponse,
    responses={500: {"model": ErrorResponse}},
    summary="Push the latest conclusion to configured notification channels",
)
def push() -> IndustryFundsPushResponse:
    """把最近一份快照的结论推到 ``.env`` 里已配置的所有通知渠道。

    推送内容与页面一致但更短（IM 里没人读 90 行行业表）。未启用推送时返回
    ``skipped=True`` 而不是报错 —— 这是配置状态，不是故障。
    """
    from src.config import get_config
    from src.services.industry_funds.push import IndustryFundsPusher

    config = get_config()
    try:
        payload = IndustryFundsService().overview()
    except IndustryFundsUnavailableError as exc:
        raise _disabled(exc)
    except Exception as exc:
        raise _internal_error("Read industry funds overview for push failed", exc)

    try:
        result = IndustryFundsPusher(config=config).push(payload)
    except Exception as exc:
        raise _internal_error("Push industry funds conclusion failed", exc)
    return IndustryFundsPushResponse(**result)
