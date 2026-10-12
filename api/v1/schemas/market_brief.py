# -*- coding: utf-8 -*-
"""消息面汇总模块的 API 响应模型。"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

BriefModeValue = Literal["premarket", "intraday", "postmarket", "weekend", "holiday", "unknown"]


class MarketBriefWindowModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    market: str = "cn"
    mode: BriefModeValue = "unknown"
    mode_label: str = ""
    phase: str = ""
    window_hint: str = ""
    now: str
    start: str
    end: str
    duration_hours: float = 0.0
    # 时间窗因交易日历不可用或边界缺失而退化成固定回看
    degraded: bool = False
    degraded_reason: str = ""
    warnings: List[str] = Field(default_factory=list)


class MarketBriefResponse(BaseModel):
    """一次汇总的完整载荷。result / grounding 结构随 schema_version 演进，故放开 extra。"""

    model_config = ConfigDict(extra="allow")

    schema_version: str
    mode: BriefModeValue = "unknown"
    mode_label: str = ""
    window: Optional[MarketBriefWindowModel] = None
    item_count: int = 0
    chunk_count: int = 0
    model: Optional[str] = None
    provider: Optional[str] = None
    result: Dict[str, Any] = Field(default_factory=dict)
    grounding: Dict[str, Any] = Field(default_factory=dict)
    coverage: Dict[str, Any] = Field(default_factory=dict)
    # 事实层各结论引用到的资讯条目原文（供前端「信源 N」展开）。顶层数组，只含被引用到的
    # 条目；旧版本生成的汇总没有该字段，故给默认空数组。
    evidence: List[Dict[str, Any]] = Field(default_factory=list)
    degraded: bool = False
    degraded_reason: str = ""
    llm_calls: int = 0
    duration_ms: int = 0
    usage: Dict[str, Any] = Field(default_factory=dict)
    cached: bool = False
    created_at: Optional[str] = None


class MarketBriefJobResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    job_id: str
    mode: BriefModeValue = "unknown"
    mode_label: str = ""
    window_start: Optional[str] = None
    window_end: Optional[str] = None
    status: Literal["queued", "running", "succeeded", "failed"] = "queued"
    phase: str = "queued"
    phase_label: str = ""
    message: str = ""
    total_items: int = 0
    total_chunks: int = 0
    current_chunk: int = 0
    progress: float = 0.0
    created_at: float = 0.0
    updated_at: float = 0.0
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class MarketBriefJobRequest(BaseModel):
    # 忽略签名缓存重新生成
    force: bool = False
