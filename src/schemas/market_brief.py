# -*- coding: utf-8 -*-
"""盘前/盘中/周末消息面汇总的结构化输出契约。

与 CCTV 新闻分析同样分两层，前端也要按两层呈现：

- **消息事实层**（``events`` / ``sectors`` / ``takeaways``）：必须能在参与本次汇总的
  资讯条目里找到依据，每条都带 ``evidence_ids``（指向条目编号）和 ``mentioned_terms``
  （在原文中逐字出现的词）。服务端逐条校验，站不住的直接丢弃。
- **模型推断层**（``watch_list``）：个股大多不会在快讯里被点名，属于"板块 → 相关公司"
  的模型知识映射。必须挂在已通过校验的板块上，并明确标注为推断。

与 CCTV 那份的差别在于多了时段语义：同一批消息在盘前、盘中和周末该给的结论不一样，
所以结果里带 ``mode`` 与 ``window_*``，并要求模型按时段给"下一步该看什么"。
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

MARKET_BRIEF_SCHEMA_VERSION = "2026-09-08"

MarketDirection = Literal["bullish", "neutral", "bearish"]
EvidenceStrength = Literal["high", "medium", "low"]
BriefMode = Literal["premarket", "intraday", "postmarket", "weekend", "holiday", "unknown"]

WATCH_LIST_DISCLAIMER = (
    "个股名称未必出现在资讯原文中，属于由「消息中出现的板块/政策方向」推断的相关标的映射，"
    "仅供研究参考，不构成投资建议。"
)


class BriefEvent(BaseModel):
    """窗口内的一条关键消息（政策、事件、数据、公司动作）。"""

    model_config = ConfigDict(extra="allow")

    topic: str
    detail: str = ""
    category: str = ""
    importance: EvidenceStrength = "low"
    evidence_ids: List[int] = Field(default_factory=list)
    mentioned_terms: List[str] = Field(default_factory=list)


class BriefSector(BaseModel):
    """由消息直接支撑的板块方向判断。"""

    model_config = ConfigDict(extra="allow")

    name: str
    direction: MarketDirection = "neutral"
    strength: EvidenceStrength = "low"
    reason: str = ""
    evidence_ids: List[int] = Field(default_factory=list)
    mentioned_terms: List[str] = Field(default_factory=list)


class BriefWatchItem(BaseModel):
    """由板块方向推断出的关注股票（模型知识，非资讯原文）。"""

    model_config = ConfigDict(extra="allow")

    name: str
    code: Optional[str] = None
    sector: str = ""
    direction: MarketDirection = "bullish"
    rationale: str = ""
    evidence_ids: List[int] = Field(default_factory=list)


class BriefTakeaway(BaseModel):
    """按当前时段给出的要点与观察条件。"""

    model_config = ConfigDict(extra="allow")

    topic: str
    detail: str = ""
    evidence_ids: List[int] = Field(default_factory=list)
    mentioned_terms: List[str] = Field(default_factory=list)


class MarketBriefResult(BaseModel):
    """一次消息面汇总的完整结果。"""

    model_config = ConfigDict(extra="allow")

    schema_version: str = MARKET_BRIEF_SCHEMA_VERSION
    mode: BriefMode = "unknown"
    mode_label: str = ""
    window_start: Optional[str] = None
    window_end: Optional[str] = None
    overall_tone: MarketDirection = "neutral"
    summary: str = ""
    events: List[BriefEvent] = Field(default_factory=list)
    sectors: List[BriefSector] = Field(default_factory=list)
    watch_list: List[BriefWatchItem] = Field(default_factory=list)
    takeaways: List[BriefTakeaway] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list)
    not_covered: List[str] = Field(default_factory=list)
    watch_list_disclaimer: str = WATCH_LIST_DISCLAIMER
