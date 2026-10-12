# -*- coding: utf-8 -*-
"""多平台实时财经快讯 API endpoints。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.realtime_news import (
    RealtimeNewsItemListResponse,
    RealtimeNewsPlatformListResponse,
)
from src.services.realtime_news_service import (
    AGGREGATE_PLATFORM_KEY,
    RealtimeNewsService,
    RealtimeNewsServiceError,
    RealtimeNewsUpstreamError,
)
from src.services.run_diagnostics import sanitize_diagnostic_text

logger = logging.getLogger(__name__)
router = APIRouter()


def _bad_request(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail={"error": "validation_error", "message": str(exc)})


def _unavailable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={"error": "upstream_unavailable", "message": str(exc)},
    )


def _internal_error(message: str, exc: Exception) -> HTTPException:
    sanitized_error = sanitize_diagnostic_text(str(exc), max_length=300) or "internal realtime news error"
    logger.error("%s: %s", message, sanitized_error)
    return HTTPException(
        status_code=500,
        detail={"error": "internal_error", "message": f"{message}: internal realtime news service error"},
    )


@router.get(
    "/platforms",
    response_model=RealtimeNewsPlatformListResponse,
    responses={500: {"model": ErrorResponse}},
    summary="List realtime news platforms",
)
def list_platforms() -> RealtimeNewsPlatformListResponse:
    try:
        return RealtimeNewsPlatformListResponse(**RealtimeNewsService().list_platforms())
    except Exception as exc:
        raise _internal_error("List realtime news platforms failed", exc)


@router.get(
    "/items",
    response_model=RealtimeNewsItemListResponse,
    responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="List latest news of one platform",
)
def list_items(
    platform: str = Query(AGGREGATE_PLATFORM_KEY, max_length=32),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> RealtimeNewsItemListResponse:
    try:
        return RealtimeNewsItemListResponse(
            **RealtimeNewsService().list_latest(platform=platform, page=page, page_size=page_size)
        )
    except RealtimeNewsServiceError as exc:
        raise _bad_request(exc)
    except RealtimeNewsUpstreamError as exc:
        raise _unavailable(exc)
    except Exception as exc:
        raise _internal_error("List realtime news items failed", exc)


@router.get(
    "/search",
    response_model=RealtimeNewsItemListResponse,
    responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Search news of one platform by keyword",
)
def search_items(
    q: str = Query(..., max_length=64, description="任意搜索关键词（股票名、行业、事件等）"),
    platform: str = Query(AGGREGATE_PLATFORM_KEY, max_length=32),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> RealtimeNewsItemListResponse:
    try:
        return RealtimeNewsItemListResponse(
            **RealtimeNewsService().list_search_news(
                platform=platform, q=q, page=page, page_size=page_size
            )
        )
    except RealtimeNewsServiceError as exc:
        raise _bad_request(exc)
    except RealtimeNewsUpstreamError as exc:
        raise _unavailable(exc)
    except Exception as exc:
        raise _internal_error("Search realtime news failed", exc)
