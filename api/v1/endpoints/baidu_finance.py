# -*- coding: utf-8 -*-
"""
===================================
百度财经数据代理接口
===================================

职责：
1. 为「股票查询」看板代理百度财经（finance.pae.baidu.com）的行情接口
2. 规避浏览器跨域限制：前端只请求本服务，由后端转发

设计约束：
- 上游路径来自本文件的 ``_UPSTREAM_PATHS`` 白名单，绝不接受调用方传入路径，
  避免变成任意地址转发（SSRF）。
- 上游按 TLS 指纹拦截客户端：httpx / requests 一律返回 403，因此走 curl_cffi
  的浏览器指纹模拟。
- 上游为非公开接口，字段与可用性随时可能变化；本模块只做透传，不做字段
  归一化，解析责任留给前端，便于上游变更时只改一处。
- 仅供本地自用看板；不纳入分析主流程，失败不影响其它功能。
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from curl_cffi import requests as curl_requests
from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from api.v1.errors import api_error

logger = logging.getLogger(__name__)

router = APIRouter()

_UPSTREAM_BASE = "https://finance.pae.baidu.com"

# 短 key -> 上游路径。key 由前端使用，上游变更时只需改这里。
_UPSTREAM_PATHS: Dict[str, str] = {
    # 概览：盘口 / 分时 / K 线
    "quotation": "/vapi/v1/getquotation",
    "candlestick": "/sapi/v1/get_candlestick_event",
    "kline-event": "/sapi/v1/kline_event_info",
    "overview-widget": "/vapi/v1/overviewwidget",
    "related-block": "/api/getrelatedblock",
    # 资金
    "fundflow": "/vapi/v1/fundflow",
    # 简况
    "stock-widget": "/api/stockwidget",
    "selfselect": "/selfselect/openapi",
    "ranks": "/sapi/v1/ranks",
    "industry-chain": "/sapi/v1/industry-chain/stock",
    "supplychain": "/sapi/v1/supplychain/total",
    # 股评
    "comm-statistics": "/vapi/v1/commstatistics",
    "bulletscreen": "/sapi/v1/bulletscreen",
    # 资讯
    "sentiment-list": "/vapi/sentimentlist",
    "express-news": "/selfselect/expressnews",
    # 财务
    "mainop": "/vapi/v1/mainop",
    # 分析
    "analysis": "/vapi/v1/analysis",
    "analysis-quotation": "/sapi/v1/get_analysis_quotation",
}

# 上游会依据 Referer / UA 判定来源，缺失时可能返回空结果。
_UPSTREAM_HEADERS = {
    "Referer": "https://finance.baidu.com/",
    "Origin": "https://finance.baidu.com",
    # 上游对该 vendor Accept 有特殊分支，沿用浏览器实际取值。
    "Accept": "application/vnd.finance-web.v1+json",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# curl_cffi 的浏览器 TLS 指纹；Python 原生 TLS 会被上游 403。
_IMPERSONATE = "chrome"

_TIMEOUT_SECONDS = 15.0

# 不向上游转发的请求参数（本服务自身的控制参数预留位）。
_DROPPED_PARAMS = frozenset()


@router.get("/endpoints")
async def list_endpoints() -> Dict[str, Any]:
    """返回可用的代理 key 列表，便于前端与排障时对照。"""
    return {
        "base": _UPSTREAM_BASE,
        "endpoints": sorted(_UPSTREAM_PATHS.keys()),
    }


@router.get("/{endpoint_key}")
async def proxy(endpoint_key: str, request: Request) -> Any:
    """
    将请求转发到百度财经对应接口并原样返回 JSON。

    Args:
        endpoint_key: ``_UPSTREAM_PATHS`` 中的 key
        request: 其查询参数会转发给上游（``finClientType`` 缺省补 ``pc``）

    Returns:
        上游返回的 JSON。上游不可用时返回 502，key 非法时返回 404。
    """
    upstream_path = _UPSTREAM_PATHS.get(endpoint_key)
    if not upstream_path:
        raise api_error(
            404,
            "unknown_endpoint",
            f"未知的代理 endpoint: {endpoint_key}",
            detail={"available": sorted(_UPSTREAM_PATHS.keys())},
        )

    params = {
        key: value
        for key, value in request.query_params.items()
        if key not in _DROPPED_PARAMS
    }
    params.setdefault("finClientType", "pc")

    url = f"{_UPSTREAM_BASE}{upstream_path}"

    def _fetch():
        return curl_requests.get(
            url,
            params=params,
            headers=_UPSTREAM_HEADERS,
            timeout=_TIMEOUT_SECONDS,
            impersonate=_IMPERSONATE,
        )

    try:
        # curl_cffi 为同步实现，放到线程池避免阻塞事件循环。
        response = await run_in_threadpool(_fetch)
    except Exception as exc:  # curl_cffi 的异常类型随后端变化，统一按上游不可用处理
        logger.warning("百度财经接口请求失败: %s: %s", endpoint_key, exc)
        raise api_error(502, "upstream_unavailable", f"上游接口不可用: {endpoint_key}") from exc

    if response.status_code >= 400:
        logger.warning(
            "百度财经接口返回 %s: %s params=%s", response.status_code, endpoint_key, params
        )
        raise api_error(
            502,
            "upstream_error",
            f"上游接口返回 {response.status_code}: {endpoint_key}",
        )

    try:
        return response.json()
    except ValueError as exc:
        # 上游偶发返回 HTML（风控页 / 维护页），此时按上游异常处理。
        logger.warning("百度财经接口返回非 JSON: %s", endpoint_key)
        raise api_error(
            502,
            "upstream_invalid_payload",
            f"上游返回非 JSON 内容: {endpoint_key}",
            detail={"body_prefix": response.text[:200]},
        ) from exc
