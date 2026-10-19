# -*- coding: utf-8 -*-
"""
===================================
图表数据接口（K 线 / 分时）
===================================

职责：
1. 提供多周期 K 线 + MACD / KDJ / MA 指标
2. 提供分时

与 ``/api/v1/baidu-finance/*`` 的区别：那个是路径白名单的原样透传（字段不归一化、
依赖 acs-token 风控）；这里是归一化接口，上游换源只需改 ``data_provider`` 一层，
前端契约不变。
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, Query

from api.v1.errors import api_error
from api.v1.schemas.chart import KlineResponse, TimeshareResponse
from data_provider.ths_chart_fetcher import ThsUpstreamError
from src.services import chart_service

logger = logging.getLogger(__name__)

router = APIRouter()


def _parse_indicators(raw: Optional[str]) -> Optional[List[str]]:
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return []
    return [item.strip() for item in text.split(",") if item.strip()]


@router.get(
    "/kline",
    response_model=KlineResponse,
    responses={
        200: {"description": "K 线与指标"},
        422: {"description": "参数不合法"},
        502: {"description": "上游不可用"},
    },
    summary="获取多周期 K 线与技术指标",
    description=(
        "周期支持 day / week / month / quarter；复权口径 none / qfq / hfq 仅对 day 生效，"
        "周月季上游只有前复权一份。indicators 以逗号分隔，可选 ma / macd / kdj，缺省为 ma。"
    ),
)
def get_kline(
    code: str = Query(..., description="股票代码（A 股 / 场内基金 6 位）"),
    period: str = Query("day", description="K 线周期"),
    adjust: str = Query("qfq", description="复权口径，仅 period=day 生效"),
    limit: int = Query(250, ge=1, le=1200, description="返回根数"),
    indicators: Optional[str] = Query(None, description="逗号分隔：ma,macd,kdj"),
    with_today: bool = Query(True, description="日 K 是否用盘口快照补上未完成的当日那一根"),
) -> KlineResponse:
    """多周期 K 线 + 指标。

    盘中上游的 line 文件给不出可用的当日行，所以日 K 的最后一根默认由盘口快照合成，
    该根带 ``partial=true``，``today_source=snapshot``。后复权口径不合成（快照是不
    复权价，换算需要复权因子），此时 ``today_source`` 为 null。
    """
    try:
        payload = chart_service.get_kline(
            code,
            period=period,
            adjust=adjust,
            limit=limit,
            indicators=_parse_indicators(indicators),
            with_today=with_today,
        )
    except ValueError as exc:
        raise api_error(422, "invalid_request", str(exc)) from exc
    except ThsUpstreamError as exc:
        logger.warning("[图表] K 线上游不可用 code=%s period=%s: %s", code, period, exc)
        raise api_error(502, "upstream_unavailable", f"行情上游暂时不可用: {exc}") from exc

    return KlineResponse(**payload)


@router.get(
    "/timeshare",
    response_model=TimeshareResponse,
    responses={
        200: {"description": "分时"},
        422: {"description": "参数不合法"},
        502: {"description": "上游不可用"},
    },
    summary="获取分时",
    description="逐分钟价格 / 均价 / 成交量，含昨收与交易时段，用于画分时图。",
)
def get_timeshare(
    code: str = Query(..., description="股票代码（A 股 / 场内基金 6 位）"),
) -> TimeshareResponse:
    """分时。上游实时（末点与盘口快照同分）。"""
    try:
        payload = chart_service.get_timeshare(code)
    except ValueError as exc:
        raise api_error(422, "invalid_request", str(exc)) from exc
    except ThsUpstreamError as exc:
        logger.warning("[图表] 分时上游不可用 code=%s: %s", code, exc)
        raise api_error(502, "upstream_unavailable", f"行情上游暂时不可用: {exc}") from exc

    return TimeshareResponse(**payload)
