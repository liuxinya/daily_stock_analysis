# -*- coding: utf-8 -*-
"""多平台新闻搜索 API endpoints。

把用户关键词转发到各站点自己的搜索接口（财联社 / 同花顺-问财 / 东方财富 / 华尔街见闻 /
金十数据），归一化后按发布时间倒序返回时间窗内的条目。上游都是非公开接口，只做透传式
归一化，不落库。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.news_search import (
    NewsSearchItemListResponse,
    NewsSearchPlatformListResponse,
)
from src.services.news_search_service import (
    DEFAULT_LIMIT,
    DEFAULT_WINDOW_DAYS,
    PLATFORMS,
    NewsSearchService,
    NewsSearchServiceError,
    NewsSearchUpstreamError,
)
from src.services.run_diagnostics import sanitize_diagnostic_text

logger = logging.getLogger(__name__)
router = APIRouter()

# 注册表里的第一个平台即默认 tab。
_DEFAULT_PLATFORM = PLATFORMS[0].key


def _bad_request(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail={"error": "validation_error", "message": str(exc)})


def _unavailable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={"error": "upstream_unavailable", "message": str(exc)},
    )


def _internal_error(message: str, exc: Exception) -> HTTPException:
    sanitized_error = sanitize_diagnostic_text(str(exc), max_length=300) or "internal news search error"
    logger.error("%s: %s", message, sanitized_error)
    return HTTPException(
        status_code=500,
        detail={"error": "internal_error", "message": f"{message}: internal news search service error"},
    )


@router.get(
    "/platforms",
    response_model=NewsSearchPlatformListResponse,
    responses={500: {"model": ErrorResponse}},
    summary="List news search platforms",
)
def list_platforms() -> NewsSearchPlatformListResponse:
    try:
        return NewsSearchPlatformListResponse(**NewsSearchService().list_platforms())
    except Exception as exc:
        raise _internal_error("List news search platforms failed", exc)


@router.get(
    "/items",
    response_model=NewsSearchItemListResponse,
    responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="Search one platform by keyword",
)
def search_items(
    q: str = Query(..., min_length=1, max_length=64, description="搜索关键词（股票名、行业、事件等）"),
    platform: str = Query(_DEFAULT_PLATFORM, max_length=32),
    days: int = Query(DEFAULT_WINDOW_DAYS, ge=1, le=30, description="时间窗（天），只返回该窗口内的条目"),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=200),
) -> NewsSearchItemListResponse:
    try:
        return NewsSearchItemListResponse(
            **NewsSearchService().search(platform=platform, keyword=q, days=days, limit=limit)
        )
    except NewsSearchServiceError as exc:
        raise _bad_request(exc)
    except NewsSearchUpstreamError as exc:
        raise _unavailable(exc)
    except Exception as exc:
        raise _internal_error("Search news items failed", exc)
