# -*- coding: utf-8 -*-
"""CCTV《新闻联播》模块的 API 响应模型。"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

MarketDirectionValue = Literal["bullish", "neutral", "bearish"]
EvidenceStrengthValue = Literal["high", "medium", "low"]
AnalysisRangeValue = Literal[1, 3, 7, 10, 20, 30]


class CctvNewsDaySummary(BaseModel):
    news_date: str
    item_count: int = 0
    char_count: int = 0


class CctvNewsDayListResponse(BaseModel):
    start_date: str
    end_date: str
    total_days: int = 0
    total_items: int = 0
    days: List[CctvNewsDaySummary] = Field(default_factory=list)


class CctvNewsItemModel(BaseModel):
    id: int
    news_date: Optional[str] = None
    seq: int = 0
    title: str
    summary: str = ""
    url: str
    char_count: int = 0
    fetched_at: Optional[str] = None
    content: Optional[str] = None


class CctvNewsItemListResponse(BaseModel):
    items: List[CctvNewsItemModel] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 50
    start_date: str
    end_date: str


class CctvNewsSyncDayResult(BaseModel):
    news_date: str
    listed_count: int = 0
    item_count: int = 0
    created: int = 0
    updated: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    is_complete: bool = False


class CctvNewsSyncResponse(BaseModel):
    start_date: str
    end_date: str
    requested_days: int = 0
    synced_days: int = 0
    created: int = 0
    updated: int = 0
    deleted: int = 0
    days: List[CctvNewsSyncDayResult] = Field(default_factory=list)


class CctvNewsSyncRequest(BaseModel):
    days: Optional[int] = Field(None, ge=1, le=90)
    force: bool = False


class CctvNewsAnalysisRequest(BaseModel):
    range_days: Optional[AnalysisRangeValue] = 7
    news_date: Optional[date] = None  # 指定单日分析；给了就忽略 range_days
    force: bool = False


class InvestorTakeawayModel(BaseModel):
    topic: str
    detail: str = ""
    evidence_ids: List[int] = Field(default_factory=list)
    mentioned_terms: List[str] = Field(default_factory=list)


class PolicySignalModel(BaseModel):
    topic: str
    detail: str = ""
    evidence_ids: List[int] = Field(default_factory=list)
    mentioned_terms: List[str] = Field(default_factory=list)


class IndustryOpportunityModel(BaseModel):
    name: str
    direction: MarketDirectionValue = "neutral"
    strength: EvidenceStrengthValue = "low"
    reason: str = ""
    evidence_ids: List[int] = Field(default_factory=list)
    mentioned_terms: List[str] = Field(default_factory=list)


class StockCandidateModel(BaseModel):
    name: str
    code: Optional[str] = None
    industry: str = ""
    direction: MarketDirectionValue = "bullish"
    is_leader: bool = False
    rationale: str = ""
    evidence_ids: List[int] = Field(default_factory=list)


class CctvNewsAnalysisBody(BaseModel):
    schema_version: str = ""
    overall_tone: MarketDirectionValue = "neutral"
    summary: str = ""
    policy_signals: List[PolicySignalModel] = Field(default_factory=list)
    industries: List[IndustryOpportunityModel] = Field(default_factory=list)
    stocks: List[StockCandidateModel] = Field(default_factory=list)
    investor_takeaways: List[InvestorTakeawayModel] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list)
    not_covered: List[str] = Field(default_factory=list)
    stock_disclaimer: str = ""


class CctvNewsEvidenceModel(BaseModel):
    id: int
    item_id: int
    news_date: Optional[str] = None
    title: str = ""
    url: str = ""


class CctvNewsAnalysisResponse(BaseModel):
    schema_version: str = ""
    prompt_version: str = ""
    start_date: str
    end_date: str
    range_days: int = 0
    item_count: int = 0
    model: Optional[str] = None
    provider: Optional[str] = None
    duration_ms: int = 0
    cached: bool = False
    analysis_id: Optional[int] = None
    created_at: Optional[str] = None
    analysis: CctvNewsAnalysisBody
    evidence: List[CctvNewsEvidenceModel] = Field(default_factory=list)
    grounding: Dict[str, Any] = Field(default_factory=dict)
    coverage: Dict[str, Any] = Field(default_factory=dict)
    degraded: bool = False
    degraded_reason: Optional[str] = None


class CctvNewsAnalysisJobResponse(BaseModel):
    """后台分析任务的状态与进度。``result`` 仅在 status=succeeded 时有值。"""

    job_id: str
    status: str
    phase: str
    phase_label: str = ""
    message: str = ""
    range_days: int = 0
    start_date: str = ""
    end_date: str = ""
    total_items: int = 0
    total_chunks: int = 0
    current_chunk: int = 0
    progress: float = 0.0
    created_at: float = 0.0
    updated_at: float = 0.0
    result: Optional[CctvNewsAnalysisResponse] = None
    error: Optional[str] = None
