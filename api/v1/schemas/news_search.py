# -*- coding: utf-8 -*-
"""新闻搜索（多平台站内搜索）模块的 API 响应模型。"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class NewsSearchPlatformModel(BaseModel):
    key: str
    label: str


class NewsSearchPlatformListResponse(BaseModel):
    items: List[NewsSearchPlatformModel] = Field(default_factory=list)
    total: int = 0
    default_platform: str


class NewsSearchItemModel(BaseModel):
    platform: str
    platform_label: str = ""
    title: str
    summary: str = ""
    url: str = ""
    # 媒体名 / 频道名；上游不提供时为空
    source: str = ""
    # 带时区偏移的 ISO8601；上游不提供时间时为 null
    published_at: Optional[str] = None


class NewsSearchItemListResponse(BaseModel):
    platform: str
    platform_label: str = ""
    # 本次使用的搜索关键词（回显，便于前端丢弃迟到响应）
    query: str = ""
    # 生效的时间窗（天）
    window_days: int = 7
    items: List[NewsSearchItemModel] = Field(default_factory=list)
    total: int = 0
    # 为凑满时间窗向上游翻了几页
    pages_scanned: int = 0
    fetched_at: Optional[str] = None


class NewsSearchAggregateResponse(NewsSearchItemListResponse):
    """全平台聚合搜索的响应。

    比单平台多一个 ``failed_platforms``（平台 key）。聚合是 fail-open 的：某个平台超时或
    上游异常只会出现在这里，不影响其余平台的结果，因此这个接口不会因为上游问题返回 503。
    """

    failed_platforms: List[str] = Field(default_factory=list)
