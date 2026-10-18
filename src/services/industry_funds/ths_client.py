# -*- coding: utf-8 -*-
"""同花顺数据中心的客户端（纯 HTTP，无 token、无浏览器、无 py_mini_racer）。

## 为什么是"抓落地页"而不是"调 ajax 接口"

同花顺的 ``.../ajax/1/free/1/`` 接口需要一个由页面混淆 JS 实时生成的 ``hexin-v`` 头。
实测结论（务必不要推翻重来）：

- akshare 用 ``py_mini_racer`` 在本地算这个头，但 **py_mini_racer 在本项目的并发抓取
  路径下会把整个进程打成 segfault**（``mini_racer.dll`` + ``libffi-8.dll``，C 层段错误，
  Python 侧兜不住）。对 Web 服务意味着后端进程直接挂掉。
- 用无头浏览器把 ``hexin-v`` 抓出来再交给后端复刻请求，**行不通**：实测精确配对
  （同一 URL + 同一 token + 同一 cookie 罐 + 同一 UA）依然返回
  ``403 Nginx forbidden``，token 无法带出浏览器上下文。
- 但**落地页本身是服务端渲染的**：``https://data.10jqka.com.cn/funds/hyzjl/`` 用普通
  ``requests`` 就能拿到含完整数据表格的 HTML，**不需要任何 token**。分页走
  ``/page/N/`` 路径（注意 ``?page=N`` 查询串无效，会返回第 1 页）。

所以本模块直接抓落地页。相比 akshare 的 ajax 版本，列名还更好——表头自带单位
（``净额(亿)``）。

## 已知限制

个股页 ``/funds/ggzjl/`` 的默认排序是**涨跌幅**而不是净额，且 ``field/xxx/order/desc``
这类排序 URL 返回的是空壳（数据靠 XHR 加载）。所以这份个股数据只能当"当日涨幅榜 +
其资金方向"用，不能当作"主力净流入榜"。见 :func:`fetch_stock_fund_flow`。
"""

from __future__ import annotations

import logging
import re
from io import StringIO
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

BASE_URL = "https://data.10jqka.com.cn"
INDUSTRY_PATH = "/funds/hyzjl/"
STOCK_PATH = "/funds/ggzjl/"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# 行业资金流两张表的列顺序（按位置映射）。表头名会随同花顺模板微调，且「即时」表里
# 「行业涨跌幅」与「领涨股涨跌幅」同名（pandas 会去重成 涨跌幅 / 涨跌幅.1），按位置
# 取才稳。列数一旦对不上就在日志里告警，不要静默错位。
INDUSTRY_NOW_WIDTH = 11
INDUSTRY_RANK_WIDTH = 8
STOCK_WIDTH = 10

# 排行周期 → 路径片段。``board/3`` 这种形式，空串代表「即时」。
_RANK_BOARD = {"3日排行": "3", "5日排行": "5", "10日排行": "10", "20日排行": "20"}

_AMOUNT_UNITS = (("亿", 1e8), ("万", 1e4))


def _parse_pct(value: Any) -> Optional[float]:
    """``"20.04%"`` → ``20.04``。"""
    if value is None:
        return None
    text = str(value).strip().replace("%", "").replace(",", "")
    if not text or text in ("-", "--"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_amount(value: Any) -> Optional[float]:
    """``"3.16亿"`` → ``316000000.0``，``"-1228.83万"`` → ``-12288300.0``。

    个股表的资金列是带「亿/万」后缀的字符串，行业表则是已经折算成亿元的浮点数，
    所以两个解析器分开用，不要混。
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        out = float(value)
        return None if out != out else out
    text = str(value).strip().replace(",", "")
    if not text or text in ("-", "--"):
        return None
    for suffix, scale in _AMOUNT_UNITS:
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            try:
                return float(text) * scale
            except ValueError:
                return None
    try:
        return float(text)
    except ValueError:
        return None


def normalize_stock_code(value: Any) -> str:
    """股票代码补足 6 位。

    同花顺返回的代码是整型，深市 ``000001`` 会变成 ``1``，直接用会错配到别的股票。
    """
    if value is None:
        return ""
    text = str(value).strip()
    if text in ("", "-", "--", "nan", "None"):
        return ""
    if text.endswith(".0"):
        text = text[:-2]
    if text.isdigit():
        return text.zfill(6)
    return text


# 模块内沿用短名，保持既有引用不变。
_normalize_code = normalize_stock_code


def _parse_tables(html: str) -> List[Any]:
    import pandas as pd

    try:
        return pd.read_html(StringIO(html))
    except ValueError:
        return []


def _page_count(html: str) -> int:
    """从分页控件读总页数；读不到按 1 页处理。"""
    match = re.search(r'class="page_info"[^>]*>\s*(\d+)\s*/\s*(\d+)', html)
    if match:
        try:
            return max(1, int(match.group(2)))
        except ValueError:
            return 1
    return 1


def _get_html(session, url: str, *, timeout: float, referer: str) -> str:
    """抓一个落地页并返回解码后的 HTML。"""
    response = session.get(
        url,
        headers={"User-Agent": _USER_AGENT, "Referer": referer},
        timeout=max(5.0, timeout),
    )
    response.raise_for_status()
    # 同花顺页面是 GBK 系列；requests 有时会猜成 ISO-8859-1，中文会全乱。
    if not response.encoding or response.encoding.lower() in ("iso-8859-1", "ascii"):
        response.encoding = response.apparent_encoding or "gbk"
    return response.text


def _fetch_paged(
    session,
    path: str,
    *,
    timeout: float,
    referer: str,
    max_pages: Optional[int] = None,
) -> List[Any]:
    """按 ``/page/N/`` 翻页抓取，返回每页的第一张 DataFrame。"""
    first_url = f"{BASE_URL}{path}"
    first_html = _get_html(session, first_url, timeout=timeout, referer=referer)
    frames = _parse_tables(first_html)
    if not frames:
        return []

    total = _page_count(first_html)
    if max_pages is not None:
        total = min(total, max(1, int(max_pages)))

    out = [frames[0]]
    for page in range(2, total + 1):
        url = f"{BASE_URL}{path.rstrip('/')}/page/{page}/"
        try:
            html = _get_html(session, url, timeout=timeout, referer=referer)
        except Exception as exc:
            logger.warning("同花顺翻页失败 %s: %s", url, exc)
            break
        page_frames = _parse_tables(html)
        if not page_frames:
            break
        out.append(page_frames[0])
    return out


def fetch_industry_fund_flow(
    symbol: str = "即时",
    *,
    timeout: float = 30.0,
    session=None,
) -> List[Dict[str, Any]]:
    """行业资金流。``symbol`` ∈ {即时, 3日排行, 5日排行, 10日排行, 20日排行}。

    数值单位：资金为**亿元**，涨跌幅为**百分数**（如 5.61 表示 5.61%）。
    只有「即时」表带 ``领涨股`` 字段，排行表不带。
    """
    import requests

    session = session or requests.Session()
    board = _RANK_BOARD.get(symbol, "")
    is_now = not board
    path = f"{INDUSTRY_PATH}board/{board}/" if board else INDUSTRY_PATH
    referer = f"{BASE_URL}{INDUSTRY_PATH}"

    frames = _fetch_paged(session, path, timeout=timeout, referer=referer)
    if not frames:
        raise RuntimeError(f"同花顺行业资金流落地页未解析出表格（symbol={symbol}）")

    width = INDUSTRY_NOW_WIDTH if is_now else INDUSTRY_RANK_WIDTH
    rows: List[Dict[str, Any]] = []
    for frame in frames:
        if len(frame.columns) != width:
            logger.warning(
                "同花顺行业表列数 %d != 预期 %d（symbol=%s），解析可能错位",
                len(frame.columns), width, symbol,
            )
        for raw in frame.itertuples(index=False):
            values = list(raw)
            name = str(values[1]).strip()
            if not name or name == "nan":
                continue
            if is_now:
                rows.append(
                    {
                        "name": name,
                        "index": values[2],
                        "change_pct": _parse_pct(values[3]),
                        "inflow": values[4],
                        "outflow": values[5],
                        "net": values[6],
                        "company_count": values[7],
                        "leader": str(values[8]).strip(),
                        "leader_change_pct": _parse_pct(values[9]),
                        "leader_price": values[10],
                    }
                )
            else:
                rows.append(
                    {
                        "name": name,
                        "company_count": values[2],
                        "index": values[3],
                        "change_pct": _parse_pct(values[4]),
                        "inflow": values[5],
                        "outflow": values[6],
                        "net": values[7],
                        "leader": "",
                        "leader_change_pct": None,
                        "leader_price": None,
                    }
                )
    return rows


def fetch_stock_fund_flow(
    *,
    timeout: float = 25.0,
    max_pages: int = 4,
    session=None,
) -> List[Dict[str, Any]]:
    """个股资金流（当日**涨幅榜**，含其资金净额）。

    ⚠️ 这份数据的排序维度是**涨跌幅**，不是净额 —— ``field/xxx/order/desc`` 那些排序
    URL 返回的是没有数据的空壳。所以它**不能**当作"主力净流入榜"来用。它的正确用途是：

    - 建立 ``代码 → 当日资金净额`` 的映射，用来给候选池里的个股补充资金方向；
    - 反过来标出"涨幅榜上但资金净流出"的个股（拉高出货的嫌疑）。

    返回的 ``main_net`` 单位是**元**。
    """
    import requests

    session = session or requests.Session()
    referer = f"{BASE_URL}{STOCK_PATH}"
    frames = _fetch_paged(
        session, STOCK_PATH, timeout=timeout, referer=referer, max_pages=max_pages
    )
    if not frames:
        raise RuntimeError("同花顺个股资金流落地页未解析出表格")

    rows: List[Dict[str, Any]] = []
    for frame in frames:
        if len(frame.columns) != STOCK_WIDTH:
            logger.warning(
                "同花顺个股表列数 %d != 预期 %d，解析可能错位", len(frame.columns), STOCK_WIDTH
            )
        for raw in frame.itertuples(index=False):
            values = list(raw)
            code = _normalize_code(values[1])
            if not code:
                continue
            rows.append(
                {
                    "code": code,
                    "name": str(values[2]).strip(),
                    "price": values[3],
                    "change_pct": _parse_pct(values[4]),
                    "turnover_rate": _parse_pct(values[5]),
                    "inflow": _parse_amount(values[6]),
                    "outflow": _parse_amount(values[7]),
                    "main_net": _parse_amount(values[8]),
                    "amount": _parse_amount(values[9]),
                }
            )
    return rows


__all__ = [
    "fetch_industry_fund_flow",
    "fetch_stock_fund_flow",
    "normalize_stock_code",
    "BASE_URL",
]
