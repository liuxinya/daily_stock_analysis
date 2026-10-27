# -*- coding: utf-8 -*-
"""「我是打板王」的 API 响应模型。

载荷的嵌套结构（两条打法线、投票、题材）随规则演进，所以放开 ``extra``，只把前端要稳定依赖的
顶层字段显式声明出来，与行业资金 / 消息面汇总的做法一致。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class DabanOverviewResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str
    kind: str = "full"
    trade_date: str = ""
    previous_trade_date: str = ""
    generated_at: str = ""
    duration_ms: int = 0
    intraday: bool = False
    metrics: Dict[str, Any] = Field(default_factory=dict)
    stage: Dict[str, Any] = Field(default_factory=dict)
    kobe_stage: Dict[str, Any] = Field(default_factory=dict)
    ladder: List[Dict[str, Any]] = Field(default_factory=list)
    themes: List[Dict[str, Any]] = Field(default_factory=list)
    verdict: Dict[str, Any] = Field(default_factory=dict)
    lanes: Dict[str, Any] = Field(default_factory=dict)
    consensus_codes: List[str] = Field(default_factory=list)
    excluded: List[Dict[str, Any]] = Field(default_factory=list)
    review: Optional[Dict[str, Any]] = None
    track_record: Dict[str, Any] = Field(default_factory=dict)
    conclusion: Dict[str, Any] = Field(default_factory=dict)
    principles: Dict[str, Any] = Field(default_factory=dict)
    mode_labels: Dict[str, str] = Field(default_factory=dict)
    style_labels: Dict[str, str] = Field(default_factory=dict)
    sources: Dict[str, Any] = Field(default_factory=dict)
    disclaimers: Dict[str, str] = Field(default_factory=dict)
    degraded: bool = False
    warnings: List[str] = Field(default_factory=list)
    cached: bool = False
    stored_at: Optional[str] = None


class DabanJobModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    job_id: str
    trade_date: Optional[str] = None
    status: str = "queued"
    phase: str = "queued"
    phase_label: str = ""
    message: str = ""
    progress: float = 0.0
    created_at: float = 0.0
    updated_at: float = 0.0
    result: Optional[DabanOverviewResponse] = None
    error: Optional[str] = None


class DabanJobSubmitRequest(BaseModel):
    trade_date: Optional[str] = Field(default=None, description="YYYY-MM-DD；为空表示最新交易日")


class DabanJobSubmitResponse(BaseModel):
    """与行业资金相同的三态：正常提交 / 复用进行中的任务 / 冷却中。"""

    model_config = ConfigDict(extra="allow")

    job: Optional[DabanJobModel] = None
    reused: bool = False
    cooling_down: bool = False
    retry_after_seconds: int = 0
    message: str = ""


class DabanPushResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    ok: bool = False
    skipped: bool = False
    message: str = ""
    trade_date: str = ""


class DabanHistoryItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    trade_date: str = ""
    kind: str = "full"
    stage: Optional[str] = None
    stage_label: str = ""
    kobe_stage: Optional[str] = None
    fall_day: Optional[int] = None
    zt_count: Optional[int] = None
    dt_count: Optional[int] = None
    max_height: Optional[int] = None
    promotion_rate: Optional[float] = None
    yzt_premium: Optional[float] = None
    preferred_style: Optional[str] = None
    intraday: bool = False


class DabanHistoryResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    items: List[DabanHistoryItem] = Field(default_factory=list)


class DabanLiveHit(BaseModel):
    model_config = ConfigDict(extra="allow")

    code: str
    name: str = ""
    style: str
    style_label: str = ""
    action: str
    action_label: str = ""
    position_pct: int = 0
    first_time: str = ""
    last_time: str = ""
    open_num: int = 0
    sealed: Optional[bool] = None
    limit_price: Optional[float] = None
    price: Optional[float] = None
    change_pct: Optional[float] = None
    turnover_rate: Optional[float] = None
    circ_mv: Optional[float] = None
    seal_amount: Optional[float] = None
    seal_ratio: Optional[float] = None
    reason: str = ""
    high_days: str = ""
    limit_up_type: str = ""
    theme: str = ""
    theme_key: str = ""
    theme_rank: Optional[int] = None
    theme_main: bool = False
    novelty: str = ""
    peers: int = 0
    peer_codes: List[str] = Field(default_factory=list)
    reasons: List[Dict[str, Any]] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)
    phase: Optional[str] = None


class DabanLiveResponse(BaseModel):
    """盘中雷达。``trading`` 为 False 时数据是最近一次收盘的，页面要如实标注。"""

    model_config = ConfigDict(extra="allow")

    ok: bool = True
    error: Optional[str] = None
    note: Optional[str] = None
    generated_at: str = ""
    trade_date: str = ""
    trading: bool = False
    stale: bool = False
    duration_ms: int = 0
    previous: Dict[str, Any] = Field(default_factory=dict)
    gate_labels: Dict[str, Any] = Field(default_factory=dict)
    realtime_gate: Dict[str, Any] = Field(default_factory=dict)
    stats: Dict[str, Any] = Field(default_factory=dict)
    hits: List[DabanLiveHit] = Field(default_factory=list)
    blocked: List[Dict[str, Any]] = Field(default_factory=list)
    themes: List[Dict[str, Any]] = Field(default_factory=list)
    events: Dict[str, Any] = Field(default_factory=dict)
    degraded_reasons: List[str] = Field(default_factory=list)


class DabanForecastResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    items: List[Dict[str, Any]] = Field(default_factory=list)
    summary: Dict[str, Any] = Field(default_factory=dict)
