# -*- coding: utf-8 -*-
"""行业资金模块的 API 响应模型。

结构与 ``market_brief.py`` 保持一致：核心嵌套结构随 schema 演进，因此放开 ``extra``，
只把前端要稳定依赖的字段（尤其是 ``degraded`` / ``degraded_reason`` / ``warnings``）
显式声明出来。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class IndustryFundsSourceModel(BaseModel):
    """单个数据源的抓取结果，供前端「来源」展开复核。"""

    model_config = ConfigDict(extra="allow")

    key: str
    label: str = ""
    ok: bool = False
    source: str = ""
    as_of: str = ""
    degraded: bool = False
    degraded_reason: str = ""
    error: Optional[str] = None
    value: Dict[str, Any] = Field(default_factory=dict)
    raw: Dict[str, Any] = Field(default_factory=dict)


class IndustryFundsOverviewResponse(BaseModel):
    """一次刷新的完整载荷。"""

    model_config = ConfigDict(extra="allow")

    schema_version: str
    trade_date: str = ""
    generated_at: str = ""
    duration_ms: int = 0
    market: Dict[str, Any] = Field(default_factory=dict)
    industries: Dict[str, Any] = Field(default_factory=dict)
    candidates: Dict[str, Any] = Field(default_factory=dict)
    conclusion: Dict[str, Any] = Field(default_factory=dict)
    sources: Dict[str, IndustryFundsSourceModel] = Field(default_factory=dict)
    disclaimers: Dict[str, str] = Field(default_factory=dict)
    factor_labels: Dict[str, str] = Field(default_factory=dict)
    degraded: bool = False
    degraded_reason: str = ""
    warnings: List[str] = Field(default_factory=list)
    # 读取缓存时为 True，本次刚抓取完成为 False。
    cached: bool = False
    stored_at: Optional[str] = None
    # 新鲜度：overview 读缓存时填充。stale=True 表示距生成已超过 cache_ttl_minutes，
    # 仅作提示，不会自动重抓；age_minutes 为距今分钟数，无法判断时为 None。
    stale: bool = False
    cache_ttl_minutes: int = 0
    age_minutes: Optional[int] = None


class IndustryFundsJobModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    job_id: str
    status: str = "queued"
    phase: str = "queued"
    phase_label: str = ""
    message: str = ""
    progress: float = 0.0
    created_at: float = 0.0
    updated_at: float = 0.0
    result: Optional[IndustryFundsOverviewResponse] = None
    error: Optional[str] = None


class IndustryFundsJobSubmitResponse(BaseModel):
    """提交刷新的结果。

    三种情况都在这一个模型里表达，前端按 ``reused`` / ``cooling_down`` 分支处理：

    - 正常提交：``job`` 有值，``reused=False``，``cooling_down=False``
    - 复用进行中的任务：``job`` 有值，``reused=True``（冷却是防上游风控的硬约束，
      所以连点刷新时会复用而不是排队）
    - 冷却中：``job=None``，``cooling_down=True``，``retry_after_seconds`` 告知等待时长
    """

    model_config = ConfigDict(extra="allow")

    job: Optional[IndustryFundsJobModel] = None
    reused: bool = False
    cooling_down: bool = False
    retry_after_seconds: int = 0
    message: str = ""


class IndustryFundsPushResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    ok: bool = False
    skipped: bool = False
    message: str = ""
    trade_date: str = ""


class IndustryFundsHistoryItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    trade_date: str = ""
    score: Optional[float] = None
    verdict: Optional[str] = None
    etf_delta: Optional[float] = None
    created_at: Optional[str] = None


class IndustryFundsHistoryResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    items: List[IndustryFundsHistoryItem] = Field(default_factory=list)
