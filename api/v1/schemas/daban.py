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
    # 短线世界观（给下一个交易日用）、对前一份世界观的对账、次日观察清单、今天「重点」推荐的复盘。
    # 2026-11 之前生成的快照没有这几项，而有已退役的 verdict / lanes / review（extra=allow 原样透传）。
    worldview: Optional[Dict[str, Any]] = None
    worldview_check: Optional[Dict[str, Any]] = None
    worldview_accuracy: Dict[str, Any] = Field(default_factory=dict)
    watchlist: Dict[str, Any] = Field(default_factory=dict)
    focus_review: Dict[str, Any] = Field(default_factory=dict)
    # 旧雷达（锁定 + 名额时期，已停写）的成绩，新口径攒满 20 个交易日前折叠展示，之后为空。
    legacy_live_stats: Dict[str, Any] = Field(default_factory=dict)
    excluded: List[Dict[str, Any]] = Field(default_factory=list)
    conclusion: Dict[str, Any] = Field(default_factory=dict)
    principles: Dict[str, Any] = Field(default_factory=dict)
    mode_labels: Dict[str, str] = Field(default_factory=dict)
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


class DabanLiveResponse(BaseModel):
    """盘中雷达。``trading`` 为 False 时数据是最近一次收盘的，页面要如实标注。

    ``focus``：「重点」tab（按热点题材分组，组内按总分、再按距涨停排序，每只带进入时刻、成本、初始分 / 实时分与
    逐项明细）；``limit_board``：「涨停板」tab（龙 1 / 龙 2 置顶）；``score_rules``：计分表；``worldview``：盘中版世界观
    （边界、推翻信号、改动记录）。2026-11 起不再返回两派的 ``hits`` / ``lanes``（名额与仓位已退役）。
    """

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
    focus: Dict[str, Any] = Field(default_factory=dict)
    limit_board: Dict[str, Any] = Field(default_factory=dict)
    score_rules: Dict[str, Any] = Field(default_factory=dict)
    worldview: Optional[Dict[str, Any]] = None
    # 大盘急拉推荐点（沪深300 30 分钟拉升 ≥0.8 且翻红）；没触发为 None。只是买入时机提示。
    surge_point: Optional[Dict[str, Any]] = None
    hot_themes: List[Dict[str, Any]] = Field(default_factory=list)
    indices: Dict[str, Any] = Field(default_factory=dict)
    feature_status: Optional[Dict[str, Any]] = None
    events: Dict[str, Any] = Field(default_factory=dict)
    degraded_reasons: List[str] = Field(default_factory=list)


class DabanForecastResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    items: List[Dict[str, Any]] = Field(default_factory=list)
    summary: Dict[str, Any] = Field(default_factory=dict)
