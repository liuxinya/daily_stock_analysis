# -*- coding: utf-8 -*-
"""CCTV《新闻联播》AI 分析的结构化输出契约。

分成两层，前端也要按两层呈现：

- **新闻事实层**（``policy_signals`` / ``industries``）：必须能在给定文字稿里找到
  依据，每条都带 ``evidence_ids``（指向参与本次分析的新闻编号）和
  ``mentioned_terms``（在原文中逐字出现的词）。服务端会逐条校验，站不住的直接丢弃。
- **模型推断层**（``stocks``）：个股名称基本不会出现在新闻联播里，属于"行业 →
  龙头公司"的模型知识映射。这一层必须挂在已通过校验的行业上，并且明确标注为推断。

这样既满足"只依据文字稿分析"，又不假装个股推荐也是新闻原文里的事实。
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

CCTV_NEWS_ANALYSIS_SCHEMA_VERSION = "2026-08-26"

MarketDirection = Literal["bullish", "neutral", "bearish"]
EvidenceStrength = Literal["high", "medium", "low"]

STOCK_INFERENCE_DISCLAIMER = (
    "个股名称未必出现在新闻联播原文中，属于由「新闻中出现的行业/政策方向」推断的"
    "行业龙头映射，仅供研究参考，不构成投资建议。"
)


class PolicySignal(BaseModel):
    """文字稿里出现的政策/会议/表态信号。"""

    model_config = ConfigDict(extra="allow")

    topic: str
    detail: str = ""
    evidence_ids: List[int] = Field(default_factory=list)
    mentioned_terms: List[str] = Field(default_factory=list)


class IndustryOpportunity(BaseModel):
    """由文字稿直接支撑的行业方向判断。"""

    model_config = ConfigDict(extra="allow")

    name: str
    direction: MarketDirection = "neutral"
    strength: EvidenceStrength = "low"
    reason: str = ""
    evidence_ids: List[int] = Field(default_factory=list)
    mentioned_terms: List[str] = Field(default_factory=list)


class StockCandidate(BaseModel):
    """基于行业方向推断出的关注个股（模型知识，非新闻原文）。"""

    model_config = ConfigDict(extra="allow")

    name: str
    code: Optional[str] = None
    industry: str = ""
    direction: MarketDirection = "bullish"
    is_leader: bool = False
    rationale: str = ""
    evidence_ids: List[int] = Field(default_factory=list)


class InvestorTakeaway(BaseModel):
    """对股民有实际参考价值的要点。"""

    model_config = ConfigDict(extra="allow")

    topic: str
    detail: str = ""
    evidence_ids: List[int] = Field(default_factory=list)
    mentioned_terms: List[str] = Field(default_factory=list)


class CctvNewsAnalysisResult(BaseModel):
    """一次窗口分析的完整结果。"""

    model_config = ConfigDict(extra="allow")

    schema_version: str = CCTV_NEWS_ANALYSIS_SCHEMA_VERSION
    overall_tone: MarketDirection = "neutral"
    summary: str = ""
    policy_signals: List[PolicySignal] = Field(default_factory=list)
    industries: List[IndustryOpportunity] = Field(default_factory=list)
    stocks: List[StockCandidate] = Field(default_factory=list)
    investor_takeaways: List[InvestorTakeaway] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list)
    not_covered: List[str] = Field(default_factory=list)
    stock_disclaimer: str = STOCK_INFERENCE_DISCLAIMER
