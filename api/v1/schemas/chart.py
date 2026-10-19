# -*- coding: utf-8 -*-
"""
===================================
图表数据相关模型
===================================

职责：
1. 定义多周期 K 线与技术指标模型
2. 定义分时模型

数据来自同花顺行情 CDN（见 ``data_provider/ths_chart_fetcher``），指标在服务端
按项目既有口径计算（MACD 复用 ``stock_analyzer``，KDJ 复用 ``alert_indicators``）。
"""

from typing import List, Optional

from pydantic import BaseModel, Field


class KlineBar(BaseModel):
    """一根 K 线。指标字段按请求的 ``indicators`` 填充，未请求或预热不足时为 null。"""

    time: str = Field(..., description="日期 YYYYMMDD（分钟周期为 YYYYMMDDHHMM）")
    open: Optional[float] = Field(None, description="开盘价")
    high: Optional[float] = Field(None, description="最高价")
    low: Optional[float] = Field(None, description="最低价")
    close: Optional[float] = Field(None, description="收盘价")
    volume: float = Field(0.0, description="成交量（股）")
    amount: float = Field(0.0, description="成交额（元）")
    turnover_rate: Optional[float] = Field(None, description="换手率 (%)")
    partial: bool = Field(False, description="是否为未完成的当日/当期 K 线（由盘口快照合成）")

    ma5: Optional[float] = Field(None, description="5 日均线")
    ma10: Optional[float] = Field(None, description="10 日均线")
    ma20: Optional[float] = Field(None, description="20 日均线")

    dif: Optional[float] = Field(None, description="MACD 快线 DIF")
    dea: Optional[float] = Field(None, description="MACD 信号线 DEA")
    macd: Optional[float] = Field(None, description="MACD 柱 (DIF-DEA)*2")

    k: Optional[float] = Field(None, description="KDJ 的 K")
    d: Optional[float] = Field(None, description="KDJ 的 D")
    j: Optional[float] = Field(None, description="KDJ 的 J = 3K-2D")


class KlineResponse(BaseModel):
    """多周期 K 线 + 指标"""

    code: str = Field(..., description="股票代码")
    name: Optional[str] = Field(None, description="股票名称")
    period: str = Field(..., description="周期：day / week / month / quarter")
    adjust: str = Field(..., description="实际生效的复权口径：none / qfq / hfq")
    indicators: List[str] = Field(default_factory=list, description="本次计算的指标")
    rows: List[KlineBar] = Field(default_factory=list, description="按时间升序")
    today_source: Optional[str] = Field(
        None,
        description=(
            "末根未完成 K 线的来源：snapshot = 日 K 由盘口快照合成；"
            "upstream = 周/月/季的本期未走完、数据取自上游；null = 末根已收盘"
        ),
    )
    listed_since: Optional[str] = Field(None, description="上游给出的首个交易日")
    trading_hours: Optional[str] = Field(None, description="交易时段，如 0930-1130,1300-1500")
    source: str = Field("ths", description="数据来源标识")


class TimesharePoint(BaseModel):
    """分时的一个点"""

    time: str = Field(..., description="时间 HHMM")
    price: float = Field(..., description="价格")
    avg_price: Optional[float] = Field(None, description="当日均价")
    volume: float = Field(0.0, description="该分钟成交量（股）")
    amount: float = Field(0.0, description="该分钟成交额（元）")


class TimeshareResponse(BaseModel):
    """分时"""

    code: str = Field(..., description="股票代码")
    name: Optional[str] = Field(None, description="股票名称")
    trade_date: Optional[str] = Field(None, description="交易日 YYYYMMDD")
    prev_close: Optional[float] = Field(None, description="昨收价，画基准线用")
    trade_times: List[str] = Field(default_factory=list, description="交易时段，如 ['0930-1130','1300-1500']")
    is_trading: bool = Field(False, description="当前是否在交易中")
    suspended: bool = Field(False, description="是否停牌")
    points: List[TimesharePoint] = Field(default_factory=list, description="按时间升序")
    source: str = Field("ths", description="数据来源标识")
