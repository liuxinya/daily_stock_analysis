# -*- coding: utf-8 -*-
"""CCTV《新闻联播》文字稿与 AI 分析 API endpoints。"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from api.v1.schemas.cctv_news import (
    CctvNewsAnalysisJobResponse,
    CctvNewsAnalysisRequest,
    CctvNewsAnalysisResponse,
    CctvNewsDayListResponse,
    CctvNewsItemListResponse,
    CctvNewsItemModel,
    CctvNewsSyncRequest,
    CctvNewsSyncResponse,
)
from api.v1.schemas.common import ErrorResponse
from src.services.cctv_news_analyzer import (
    CctvNewsAnalysisUnavailableError,
    CctvNewsAnalyzer,
    CctvNewsAnalyzerError,
)
from src.services.cctv_news_jobs import CctvNewsAnalysisJobManager
from src.services.cctv_news_service import CctvNewsService, CctvNewsServiceError
from src.services.run_diagnostics import sanitize_diagnostic_text

logger = logging.getLogger(__name__)
router = APIRouter()


def _bad_request(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail={"error": "validation_error", "message": str(exc)})


def _not_found(message: str) -> HTTPException:
    return HTTPException(status_code=404, detail={"error": "not_found", "message": message})


def _unavailable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={"error": "analysis_unavailable", "message": str(exc)},
    )


def _internal_error(message: str, exc: Exception) -> HTTPException:
    sanitized_error = sanitize_diagnostic_text(str(exc), max_length=300) or "internal cctv news error"
    logger.error("%s: %s", message, sanitized_error)
    return HTTPException(
        status_code=500,
        detail={"error": "internal_error", "message": f"{message}: internal cctv news service error"},
    )


@router.get(
    "/days",
    response_model=CctvNewsDayListResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="List CCTV news days with item counts",
)
def list_days(days: int = Query(30, ge=1, le=90)) -> CctvNewsDayListResponse:
    try:
        return CctvNewsDayListResponse(**CctvNewsService().list_days(days=days))
    except CctvNewsServiceError as exc:
        raise _bad_request(exc)
    except Exception as exc:
        raise _internal_error("List cctv news days failed", exc)


@router.get(
    "/items",
    response_model=CctvNewsItemListResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="List CCTV news transcripts",
)
def list_items(
    news_date: Optional[date] = Query(None),
    days: Optional[int] = Query(None, ge=1, le=90),
    query: Optional[str] = Query(None, max_length=100),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    include_content: bool = Query(False),
) -> CctvNewsItemListResponse:
    try:
        return CctvNewsItemListResponse(
            **CctvNewsService().list_items(
                news_date=news_date,
                days=days,
                query=query,
                page=page,
                page_size=page_size,
                include_content=include_content,
            )
        )
    except CctvNewsServiceError as exc:
        raise _bad_request(exc)
    except Exception as exc:
        raise _internal_error("List cctv news items failed", exc)


@router.get(
    "/items/{item_id}",
    response_model=CctvNewsItemModel,
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Get one CCTV news transcript",
)
def get_item(item_id: int) -> CctvNewsItemModel:
    try:
        return CctvNewsItemModel(**CctvNewsService().get_item(item_id))
    except CctvNewsServiceError as exc:
        raise _not_found(str(exc))
    except Exception as exc:
        raise _internal_error("Get cctv news item failed", exc)


@router.post(
    "/sync",
    response_model=CctvNewsSyncResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Sync recent CCTV news transcripts from cctv.com",
)
def sync_news(request: Optional[CctvNewsSyncRequest] = None) -> CctvNewsSyncResponse:
    payload = request or CctvNewsSyncRequest()
    try:
        return CctvNewsSyncResponse(
            **CctvNewsService().sync_recent(days=payload.days, force=payload.force)
        )
    except CctvNewsServiceError as exc:
        raise _bad_request(exc)
    except Exception as exc:
        raise _internal_error("Sync cctv news failed", exc)


def _resolve_analysis_window(service: CctvNewsService, payload: CctvNewsAnalysisRequest):
    """按请求确定分析窗口：给了 news_date 就分析单日，否则按 range_days。"""
    if payload.news_date is not None:
        return payload.news_date, payload.news_date, 1
    range_days = payload.range_days or 7
    start_date, end_date = service.resolve_analysis_window(range_days)
    return start_date, end_date, range_days


def _ensure_analysis_items(
    service: CctvNewsService,
    start_date: date,
    end_date: date,
) -> None:
    """Fetch the requested window once when analysis has no local source data."""
    if service.repo.list_items_for_window(start_date, end_date, limit=1):
        return

    logger.info(
        "分析窗口无新闻数据，自动拉取一次: %s 至 %s",
        start_date.isoformat(),
        end_date.isoformat(),
    )
    try:
        service.sync_range(start_date, end_date, force=True)
    except Exception as exc:  # noqa: BLE001 - convert provider failure to API validation error
        logger.warning("分析前自动拉取新闻失败: %s", sanitize_diagnostic_text(str(exc), max_length=200))
        raise CctvNewsServiceError("分析窗口暂无新闻，自动拉取失败，请稍后重试") from exc

    if not service.repo.list_items_for_window(start_date, end_date, limit=1):
        raise CctvNewsServiceError(
            f"{start_date.isoformat()} 至 {end_date.isoformat()} 窗口内没有文字稿，自动拉取后仍无数据"
        )


@router.post(
    "/analysis",
    response_model=CctvNewsAnalysisResponse,
    responses={
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
    summary="Analyze CCTV news transcripts in the selected window (synchronous)",
)
def analyze_news(request: Optional[CctvNewsAnalysisRequest] = None) -> CctvNewsAnalysisResponse:
    payload = request or CctvNewsAnalysisRequest()
    try:
        service = CctvNewsService()
        start_date, end_date, range_days = _resolve_analysis_window(service, payload)
        _ensure_analysis_items(service, start_date, end_date)
        result = CctvNewsAnalyzer().analyze(
            start_date=start_date,
            end_date=end_date,
            range_days=range_days,
            force=payload.force,
        )
        return CctvNewsAnalysisResponse(**result)
    except (CctvNewsAnalyzerError, CctvNewsServiceError) as exc:
        raise _bad_request(exc)
    except CctvNewsAnalysisUnavailableError as exc:
        raise _unavailable(exc)
    except Exception as exc:
        raise _internal_error("Analyze cctv news failed", exc)


@router.post(
    "/analysis/jobs",
    response_model=CctvNewsAnalysisJobResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Submit a background CCTV news analysis job",
)
def submit_analysis_job(
    request: Optional[CctvNewsAnalysisRequest] = None,
) -> CctvNewsAnalysisJobResponse:
    payload = request or CctvNewsAnalysisRequest()
    try:
        service = CctvNewsService()
        start_date, end_date, range_days = _resolve_analysis_window(service, payload)
        # 分析窗口为空时自动拉取一次，仍无数据才以 400 返回。
        _ensure_analysis_items(service, start_date, end_date)
        job = CctvNewsAnalysisJobManager.get_instance().submit(
            range_days=range_days,
            start_date=start_date,
            end_date=end_date,
            force=payload.force,
            analyzer_factory=CctvNewsAnalyzer,
        )
        return CctvNewsAnalysisJobResponse(**job.to_dict())
    except (CctvNewsAnalyzerError, CctvNewsServiceError) as exc:
        raise _bad_request(exc)
    except Exception as exc:
        raise _internal_error("Submit cctv news analysis job failed", exc)


@router.get(
    "/analysis/jobs/{job_id}",
    response_model=CctvNewsAnalysisJobResponse,
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Poll a background CCTV news analysis job",
)
def get_analysis_job(job_id: str) -> CctvNewsAnalysisJobResponse:
    try:
        job = CctvNewsAnalysisJobManager.get_instance().get_job(job_id)
        if job is None:
            raise _not_found("analysis job not found or expired")
        return CctvNewsAnalysisJobResponse(**job)
    except HTTPException:
        raise
    except Exception as exc:
        raise _internal_error("Get cctv news analysis job failed", exc)


@router.get(
    "/analysis/latest",
    response_model=CctvNewsAnalysisResponse,
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Get the most recent CCTV news analysis",
)
def latest_analysis(range_days: Optional[int] = Query(None, ge=1, le=90)) -> CctvNewsAnalysisResponse:
    try:
        result = CctvNewsAnalyzer().latest(range_days=range_days)
        if result is None:
            raise _not_found("no cctv news analysis yet")
        return CctvNewsAnalysisResponse(**result)
    except HTTPException:
        raise
    except Exception as exc:
        raise _internal_error("Get latest cctv news analysis failed", exc)
