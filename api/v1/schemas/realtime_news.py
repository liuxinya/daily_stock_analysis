# -*- coding: utf-8 -*-
"""实时新闻（多平台快讯浏览）模块的 API 响应模型。"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class RealtimeNewsPlatformModel(BaseModel):
    key: str
    label: str
    provider: str = ""
    available: bool = True
    has_published_at: bool = True
    # 是否支持「按任意关键词全文搜索」；为 False 的平台在搜索模式下展示空态
    supports_search: bool = False


class RealtimeNewsPlatformListResponse(BaseModel):
    items: List[RealtimeNewsPlatformModel] = Field(default_factory=list)
    total: int = 0
    default_platform: str


class RealtimeNewsItemModel(BaseModel):
    platform: str
    platform_label: str = ""
    title: str
    summary: str = ""
    url: str = ""
    # 带时区偏移的 ISO8601（如 2026-09-07T19:13:00+08:00）；上游不提供时间时为 null
    published_at: Optional[str] = None
    tag: str = ""


class RealtimeNewsItemListResponse(BaseModel):
    platform: str
    platform_label: str = ""
    available: bool = True
    has_published_at: bool = True
    items: List[RealtimeNewsItemModel] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20
    # 聚合模式下失败的平台 key；单源失败不影响整体返回
    failed_platforms: List[str] = Field(default_factory=list)
    fetched_at: Optional[str] = None
    # 仅搜索模式下回显：本次使用的搜索关键词（市场级 /items 不返回）
    query: Optional[str] = None
