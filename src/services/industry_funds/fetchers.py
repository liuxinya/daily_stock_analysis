# -*- coding: utf-8 -*-
"""行业资金模块的数据抓取层。

设计原则：**单个数据源失败不阻断整页**。每个 fetch 函数都返回统一的信封字典::

    {
        "key": "etf_share",
        "ok": True,                    # 是否拿到了可用数据
        "source": "上海证券交易所 query.sse.com.cn",
        "as_of": "2026-10-16",         # 数据本身的日期，不是抓取时间
        "degraded": False,             # 拿到了但口径不完整（如深市缺历史）
        "degraded_reason": "",
        "error": None,
        "value": {...},                # 供打分层消费的结构化数值
        "raw": {...},                  # 供前端「来源」展开复核的原始片段
    }

关于超时：akshare / requests 的阻塞调用无法真正取消，所以统一用带 deadline 的线程
等待把控制权抢回来（与 ``src/services/cctv_news_analyzer.py`` 的 ``_call_model`` 同一
思路）。超时的那次调用不等待回收，否则又变回阻塞。

**本文件刻意避开了几个已知不可用的上游，改由替代路径实现，改动时勿"顺手简化"：**

1. 东财 ``push2.eastmoney.com`` 在部分网络下被重置（``clist/get`` / ``stock/get``
   直接断连，``ulist.np/get`` 却通，curl_cffi 伪装 TLS 指纹也绕不过）。因此
   ``fund_etf_spot_em`` / ``stock_sector_fund_flow_rank`` / ``stock_board_industry_cons_em``
   / ``stock_zh_a_spot_em`` / ``stock_individual_info_em`` 一律不用。
2. ``ak.fund_etf_scale_sse()`` 默认 ``date=""`` 经 ``"-".join(["","",""])`` 变成
   ``STAT_DATE="--"``，上交所对该值返回**固定快照（593 行全部是 2025-01-15 的死数据）**。
   必须直接调 ``commonQuery.do`` 并传入真实交易日。这是 akshare 的 bug，升级解决不了。
3. ``ak.fund_etf_scale_szse()`` 在 pandas 3.0 下抛 TypeError（``pd.read_excel`` 传了
   bytes）。深交所改走 XLSX 直连（包 ``io.BytesIO``），统计日期另外用一次 JSON 调用取
   ``metadata.subname`` —— 同一个接口的 JSON 版本既无视 ``PAGESIZE``（固定 20 行/页、
   共 54 页），字段还全是 HTML 包着的，不如 XLSX 一次拉全。
4. 深交所的 ETF 份额接口**不支持历史日期**（实测传任何日期参数都返回当日快照），
   所以深市的份额变动只能靠本地按日积累的基线，冷启动时如实降级。沪市接口则支持
   ``STAT_DATE``，T / T-5 两次调用直接算出 5 日变动。
5. 上交所 ``SEC_NAME`` 是**截断短名**（510300 显示成 "300ETF"），光靠名称关键词会让
   沪市宽基少认出一大半，所以宽基判定是"关键词 ∪ 主力代码白名单"，见
   ``constants.MAJOR_BROAD_BASED_CODES``。
6. **同花顺一律不用 akshare 的同花顺函数**（``stock_fund_flow_industry`` /
   ``stock_fund_flow_individual``）。它们靠 ``py_mini_racer`` 跑 ``ths.js`` 算
   ``hexin-v``，而 py_mini_racer 在本模块的并发抓取路径下会把整个进程打成 segfault
   （``mini_racer.dll`` + libffi，C 层段错误，Python 兜不住 —— 对 Web 服务就是后端挂掉）。
   改抓**服务端渲染的落地页**，见 ``ths_client.py``；那里也记录了"用无头浏览器把 token
   抓出来再后端复刻请求"为什么行不通（实测精确配对仍 403）。
"""

from __future__ import annotations

import io
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from src.services.industry_funds.constants import (
    BROAD_BASED_ETF_KEYWORDS,
    MAJOR_BROAD_BASED_CODES,
    SOURCE_LABELS,
    SSE_SHARE_UNIT,
)
from src.services.industry_funds.ths_client import normalize_stock_code as _normalize_code

logger = logging.getLogger(__name__)

# 龙虎榜机构净买的历史回看窗口（自然日）。约 120 个交易日，够算一个稳定的分位，
# 又不至于把东财分页拉太久。
_LHB_LOOKBACK_DAYS = 180

# 两融历史分位用的回看窗口（自然日）。两融是慢变量，给足两年才有意义。
_MARGIN_LOOKBACK_DAYS = 730

# 同花顺个股资金流：落地页每页 50 只，默认按涨跌幅排序。这里只取前几页建立
# "代码 → 当日资金净额" 的映射，够给候选池补充资金方向即可，不必翻完 105 页。
_STOCK_FLOW_MAX_PAGES = 4

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------


def _trading_days(count: int, market: str = "cn") -> List[date]:
    """返回最近若干个交易日，从新到旧。

    exchange-calendars 不可用时退化成"跳过周末"，并在调用方标注降级 —— 宁可给一个
    近似的 T-5，也不要让整个 ETF 因子的 5 日口径直接消失。
    """
    try:
        import exchange_calendars as xcals

        from src.core.trading_calendar import MARKET_EXCHANGE

        ex = MARKET_EXCHANGE.get(market)
        if ex:
            cal = xcals.get_calendar(ex)
            today = date.today()
            anchor = today if cal.is_session(today) else cal.date_to_session(
                today, direction="previous"
            ).date()
            session = cal.date_to_session(anchor, direction="previous")
            out: List[date] = []
            for _ in range(max(1, count)):
                out.append(session.date())
                session = cal.previous_session(session)
            return out
    except Exception as exc:  # 缺依赖 / 日历异常都退化成工作日近似
        logger.warning("行业资金：交易日历不可用，退化为工作日近似（%s）", exc)

    out = []
    cursor = date.today()
    while len(out) < max(1, count):
        if cursor.weekday() < 5:
            out.append(cursor)
        cursor -= timedelta(days=1)
    return out


def _call_with_timeout(
    func: Callable[[], Any], *, timeout: float, label: str
) -> Tuple[Optional[Any], Optional[str]]:
    """带 deadline 的阻塞调用。返回 ``(结果, 错误描述)``，二者必有其一为 None。

    超时后不等待线程结束（``shutdown(wait=False)``）—— 阻塞中的 requests 调用取消
    不掉，等它就是把超时又变回阻塞。
    """
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="industry-funds")
    try:
        future = executor.submit(func)
        try:
            return future.result(timeout=max(1.0, timeout)), None
        except FutureTimeoutError:
            logger.warning("行业资金：%s 超过 %.0fs 未返回，按失败处理", label, timeout)
            return None, f"上游超过 {int(timeout)}s 未返回"
        except Exception as exc:
            logger.warning("行业资金：%s 失败 %s: %s", label, type(exc).__name__, exc)
            return None, f"{type(exc).__name__}: {str(exc)[:180]}"
    finally:
        executor.shutdown(wait=False)


def _envelope(
    key: str,
    *,
    ok: bool,
    as_of: str = "",
    value: Optional[Dict[str, Any]] = None,
    raw: Optional[Dict[str, Any]] = None,
    degraded: bool = False,
    degraded_reason: str = "",
    error: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "key": key,
        "ok": ok,
        "source": SOURCE_LABELS.get(key, ""),
        "as_of": as_of,
        "degraded": degraded,
        "degraded_reason": degraded_reason,
        "error": error,
        "value": value or {},
        "raw": raw or {},
    }


def _failed(key: str, error: str) -> Dict[str, Any]:
    return _envelope(key, ok=False, error=error, degraded=True, degraded_reason=error)


def _to_float(value: Any) -> Optional[float]:
    """把上游五花八门的数值（带逗号字符串 / numpy 标量 / NaN）统一成 float。"""
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "").replace("%", "")
        if not cleaned or cleaned in ("-", "--", "nan"):
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out:  # NaN
        return None
    return out


def _percentile_rank(series: Sequence[float], value: float) -> Optional[float]:
    """value 在 series 中的分位（0-1）。用"小于等于的比例"，平局按半数计。"""
    cleaned = [float(x) for x in series if x is not None and float(x) == float(x)]
    if not cleaned or value is None:
        return None
    below = sum(1 for x in cleaned if x < value)
    equal = sum(1 for x in cleaned if x == value)
    return (below + 0.5 * equal) / len(cleaned)


def _is_broad_based(code: str, name: str) -> bool:
    """宽基 ETF 判定：名称关键词 或 主力宽基代码白名单，二者取并集。

    关键词表对深市够用（全名），对沪市不够用 —— 上交所 ``SEC_NAME`` 是截断短名
    （510300 显示成 "300ETF"），"沪深300" 这类词匹配不上，只靠关键词会让沪市宽基
    少认出一大半（实测只剩 27 只，全是名字里刚好带"科创50"的）。所以要并上代码白名单。
    """
    if str(code or "").strip() in MAJOR_BROAD_BASED_CODES:
        return True
    text = str(name or "")
    return any(keyword in text for keyword in BROAD_BASED_ETF_KEYWORDS)


# ---------------------------------------------------------------------------
# 1. 行业资金流（同花顺）
# ---------------------------------------------------------------------------


def fetch_industry_fund_flow(*, timeout: float) -> Dict[str, Any]:
    """同花顺行业资金流：即时快照 + 5 日累计。

    抓的是**服务端渲染的落地页**（用户给的
    https://data.10jqka.com.cn/funds/hyzjl/ ），即时要两个页面才够 90 个行业。
    不用 akshare 的同花顺函数 —— 那些要 py_mini_racer，会把进程打成 segfault，
    详见 ``ths_client`` 的模块说明。
    """
    from src.services.industry_funds import ths_client

    rows, err_today = _call_with_timeout(
        lambda: ths_client.fetch_industry_fund_flow("即时", timeout=timeout),
        timeout=timeout,
        label="同花顺行业资金流(即时)",
    )
    if not rows:
        return _failed("industry_fund_flow", err_today or "同花顺未返回行业资金流数据")

    five_rows, err_five = _call_with_timeout(
        lambda: ths_client.fetch_industry_fund_flow("5日排行", timeout=timeout),
        timeout=timeout,
        label="同花顺行业资金流(5日)",
    )

    five_day_map: Dict[str, float] = {}
    for record in five_rows or []:
        name = str(record.get("name") or "").strip()
        net_5d = _to_float(record.get("net"))
        if name and net_5d is not None:
            five_day_map[name] = net_5d

    industries: List[Dict[str, Any]] = []
    for record in rows:
        name = str(record.get("name") or "").strip()
        if not name:
            continue
        industries.append(
            {
                "name": name,
                "index": _to_float(record.get("index")),
                "change_pct": _to_float(record.get("change_pct")),
                "inflow": _to_float(record.get("inflow")),
                "outflow": _to_float(record.get("outflow")),
                "net": _to_float(record.get("net")),
                "company_count": _to_float(record.get("company_count")),
                "leader": str(record.get("leader") or "").strip(),
                "leader_change_pct": _to_float(record.get("leader_change_pct")),
                "leader_price": _to_float(record.get("leader_price")),
                # 5 日累计表按行业名对齐。两张表的行业集合应当一致（都是 90 个），
                # 对不上的那些 5 日值留 None，由打分层按缺失处理。
                "net_5d": five_day_map.get(name),
            }
        )

    if not industries:
        return _failed("industry_fund_flow", "同花顺返回了数据但无法解析出行业行")

    nets = [r["net"] for r in industries if r["net"] is not None]
    inflow_count = sum(1 for n in nets if n > 0)
    missing_5d = [r["name"] for r in industries if r["net_5d"] is None]
    degraded = not five_day_map
    degraded_reason = ""
    if degraded:
        degraded_reason = f"5 日排行抓取失败（{err_five or '未返回数据'}），行业分缺少 5 日累计子因子"
    elif missing_5d:
        degraded_reason = f"{len(missing_5d)} 个行业未匹配到 5 日累计净额"

    return _envelope(
        "industry_fund_flow",
        ok=True,
        # 落地页的表格不带数据日期，取交易日历推算的最近交易日 —— 收盘后即为当日。
        as_of=_trading_days(1)[0].isoformat(),
        value={
            "industries": industries,
            "industry_count": len(industries),
            "inflow_count": inflow_count,
            "outflow_count": len(nets) - inflow_count,
            "inflow_ratio": (inflow_count / len(nets)) if nets else None,
            "net_sum": sum(nets) if nets else None,
            "net_median": sorted(nets)[len(nets) // 2] if nets else None,
            "has_5d": bool(five_day_map),
            "missing_5d_count": len(missing_5d),
        },
        raw={
            "note": "净额 / 流入 / 流出 单位：亿元；口径为单笔金额分档推断，非账户身份",
            "sample": industries[:5],
        },
        degraded=degraded or bool(missing_5d),
        degraded_reason=degraded_reason,
        error=err_five if degraded else None,
    )


# ---------------------------------------------------------------------------
# 2. 龙虎榜机构净买（东方财富数据中心）
# ---------------------------------------------------------------------------


def fetch_lhb_institution(*, timeout: float, lookback_days: int = _LHB_LOOKBACK_DAYS) -> Dict[str, Any]:
    """龙虎榜机构专用席位净买。

    这是全流程里口径最硬的一项：机构席位是交易所披露的，不是概率推断。
    返回逐日合计序列（用于算历史分位）与最新交易日的个股明细（用于候选池）。
    """

    def _load():
        import akshare as ak

        end = _trading_days(1)[0]
        start = end - timedelta(days=max(30, int(lookback_days)))
        return ak.stock_lhb_jgmmtj_em(
            start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d")
        )

    df, err = _call_with_timeout(_load, timeout=timeout, label="东财龙虎榜机构买卖统计")
    if df is None or getattr(df, "empty", True):
        return _failed("lhb_institution", err or "龙虎榜机构数据为空")

    daily: Dict[str, float] = {}
    latest_date = ""
    latest_rows: List[Dict[str, Any]] = []
    for record in df.to_dict("records"):
        raw_date = str(record.get("上榜日期") or "").strip()
        net = _to_float(record.get("机构买入净额"))
        if not raw_date or net is None:
            continue
        daily[raw_date] = daily.get(raw_date, 0.0) + net
        if raw_date > latest_date:
            latest_date = raw_date

    if not daily:
        return _failed("lhb_institution", "龙虎榜机构数据无法解析出净买额")

    # 明细只保留最近一个交易日，供候选池消费。
    for record in df.to_dict("records"):
        if str(record.get("上榜日期") or "").strip() != latest_date:
            continue
        latest_rows.append(
            {
                "code": str(record.get("代码") or "").strip(),
                "name": str(record.get("名称") or "").strip(),
                "close": _to_float(record.get("收盘价")),
                "change_pct": _to_float(record.get("涨跌幅")),
                "buyer_count": _to_float(record.get("买方机构数")),
                "seller_count": _to_float(record.get("卖方机构数")),
                "institution_net": _to_float(record.get("机构买入净额")),
                "institution_buy": _to_float(record.get("机构买入总额")),
                "institution_sell": _to_float(record.get("机构卖出总额")),
                "net_ratio": _to_float(record.get("机构净买额占总成交额比")),
                "turnover_rate": _to_float(record.get("换手率")),
                "market_cap": _to_float(record.get("流通市值")),
                "reason": str(record.get("上榜原因") or "").strip(),
            }
        )

    ordered_dates = sorted(daily.keys())
    series = [daily[d] for d in ordered_dates]
    latest_value = daily.get(latest_date, 0.0)
    # 近 5 个交易日滚动合计，比单日更抗单笔大单扰动；分位也用滚动序列算 ——
    # 单日序列里"今天没有个股上榜"会记成 0，正好落在分布中部，凭空给一个中性分。
    rolling_series = [
        sum(series[max(0, i - 4) : i + 1]) for i in range(len(series))
    ]
    rolling_5 = rolling_series[-1] if rolling_series else latest_value
    percentile = _percentile_rank(rolling_series, rolling_5)
    daily_percentile = _percentile_rank(series, latest_value)

    return _envelope(
        "lhb_institution",
        ok=True,
        as_of=latest_date,
        value={
            "latest_date": latest_date,
            "latest_net": latest_value,
            "rolling_5d_net": rolling_5,
            "history_days": len(series),
            "percentile": percentile,
            "daily_percentile": daily_percentile,
            "series_tail": [
                {"date": d, "net": round(daily[d] / 1e8, 4)} for d in ordered_dates[-30:]
            ],
            "stocks": sorted(
                latest_rows,
                key=lambda r: r.get("institution_net") or 0.0,
                reverse=True,
            ),
        },
        raw={
            "note": "机构买入净额原始单位：元；series_tail.net 单位：亿元",
            "window": f"{ordered_dates[0]} ~ {ordered_dates[-1]}",
        },
    )


# ---------------------------------------------------------------------------
# 3. ETF 份额（两交易所直连）
# ---------------------------------------------------------------------------


def _fetch_sse_etf_shares(stat_date: date, *, timeout: float) -> Dict[str, Dict[str, Any]]:
    """上交所 ETF 份额。

    **不要**换成 ``ak.fund_etf_scale_sse()``：它默认把 ``STAT_DATE`` 传成 ``"--"``，
    上交所对这个非法值返回固定的 2025-01-15 老快照，页面会显示两年前的数据却不报错。
    """

    def _load():
        import requests

        params = {
            "isPagination": "true",
            "pageHelp.pageSize": "10000",
            "pageHelp.pageNo": "1",
            "pageHelp.beginPage": "1",
            "pageHelp.cacheSize": "1",
            "pageHelp.endPage": "1",
            "sqlId": "COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L",
            "STAT_DATE": stat_date.strftime("%Y-%m-%d"),
        }
        response = requests.get(
            "https://query.sse.com.cn/commonQuery.do",
            params=params,
            headers={"Referer": "https://www.sse.com.cn/", "User-Agent": _USER_AGENT},
            timeout=min(timeout, 25.0),
        )
        response.raise_for_status()
        return response.json()

    payload, err = _call_with_timeout(_load, timeout=timeout, label=f"上交所ETF份额({stat_date})")
    if not payload:
        logger.warning("行业资金：上交所 ETF 份额抓取失败(%s) %s", stat_date, err)
        return {}

    out: Dict[str, Dict[str, Any]] = {}
    for item in payload.get("result") or []:
        code = str(item.get("SEC_CODE") or "").strip()
        # TOT_VOL 单位是「万份」，而深交所给的是「份」，这里统一折算成「份」。
        volume = _to_float(item.get("TOT_VOL"))
        if not code or volume is None:
            continue
        out[code] = {
            "name": str(item.get("SEC_NAME") or "").strip(),
            "shares": volume * SSE_SHARE_UNIT,
            "type": str(item.get("ETF_TYPE") or "").strip(),
            "date": str(item.get("STAT_DATE") or "").strip(),
        }
    return out


def _fetch_szse_etf_shares(*, timeout: float) -> Tuple[Dict[str, Dict[str, Any]], str]:
    """深交所 ETF 份额（仅当日快照，接口不支持历史日期）。

    返回 ``(数据, 统计日期)``，需要两个请求：

    - **数据走 XLSX**（``SHOWTYPE=xlsx``）。同一个接口的 JSON 版本有两个坑：它无视
      ``PAGESIZE``（固定每页 20 行、共 54 页），且所有字段都是 HTML 包起来的
      （``sys_key`` 是 ``<a ...><u>158000</u></a>``，``dqgm`` 是 ``<a ...>3,592.58</a>``
      且单位是**万份**并按万份四舍五入）。XLSX 一次拉全 1071 行，给的是精确的「份」。
    - **日期走 JSON**，因为 XLSX 不带统计日期，而 JSON 的 ``metadata.subname`` 就是它。
      用 ``PAGESIZE=1`` 把这次请求压到最小。

    ``PAGESIZE`` 在 JSON 版本上无效，所以这里不传——传了也不会生效，反而误导。
    另外不要换成 ``ak.fund_etf_scale_szse()``：它在 pandas 3.0 下把 bytes 直接喂给
    ``pd.read_excel`` 抛 TypeError。
    """
    import io as _io

    def _load_xlsx():
        import pandas as pd
        import requests

        response = requests.get(
            "https://fund.szse.cn/api/report/ShowReport",
            params={
                "SHOWTYPE": "xlsx",
                "CATALOGID": "1000_lf",
                "TABKEY": "tab1",
                "random": "0.07",
            },
            headers={
                "Referer": "https://fund.szse.cn/marketdata/fundslist/index.html",
                "User-Agent": _USER_AGENT,
            },
            timeout=min(timeout, 25.0),
        )
        response.raise_for_status()
        # pandas 3.0 起不再接受裸 bytes，必须包成 file-like。
        return pd.read_excel(_io.BytesIO(response.content), dtype={"基金代码": str})

    def _load_stat_date():
        import requests

        response = requests.get(
            "https://www.szse.cn/api/report/ShowReport/data",
            params={
                "SHOWTYPE": "JSON",
                "CATALOGID": "1000_lf",
                "TABKEY": "tab1",
                "PAGESIZE": "1",
                "random": "0.07",
            },
            headers={
                "Referer": "https://www.szse.cn/marketdata/fundslist/index.html",
                "User-Agent": _USER_AGENT,
            },
            timeout=min(timeout, 20.0),
        )
        response.raise_for_status()
        payload = response.json()
        block = (payload if isinstance(payload, list) else [payload])[0]
        return str((block.get("metadata") or {}).get("subname") or "").strip()

    df, err = _call_with_timeout(_load_xlsx, timeout=timeout, label="深交所ETF份额(XLSX)")
    if df is None or getattr(df, "empty", True):
        logger.warning("行业资金：深交所 ETF 份额抓取失败 %s", err)
        return {}, ""

    stat_date, _ = _call_with_timeout(
        _load_stat_date, timeout=min(timeout, 20.0), label="深交所统计日期"
    )
    stat_date = stat_date or ""

    out: Dict[str, Dict[str, Any]] = {}
    for record in df.to_dict("records"):
        # 深交所这个列表同时含 LOF / 不动产基金，只保留 ETF。
        if str(record.get("基金类别") or "").strip() != "ETF":
            continue
        code = str(record.get("基金代码") or "").strip()
        shares = _to_float(record.get("当前规模(份)"))
        if not code or shares is None:
            continue
        out[code] = {
            "name": str(record.get("基金简称") or "").strip(),
            "shares": shares,
            "type": "",
            "date": stat_date,
        }
    return out, stat_date


def _aggregate_broad_based(shares: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """汇总宽基 ETF 的份额合计与明细。"""
    total = 0.0
    details: List[Dict[str, Any]] = []
    for code, item in shares.items():
        if not _is_broad_based(code, item.get("name", "")):
            continue
        total += item.get("shares") or 0.0
        details.append(
            {
                "code": code,
                "name": item.get("name", ""),
                "shares": item.get("shares"),
                "date": item.get("date", ""),
            }
        )
    details.sort(key=lambda d: d.get("shares") or 0.0, reverse=True)
    return {"total_shares": total, "count": len(details), "details": details}


def _broad_based_deltas(
    today: Dict[str, Dict[str, Any]], baseline: Dict[str, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """逐只宽基 ETF 的份额变动，按变动绝对值从大到小。

    单看一个合计数字看不出"钱进了哪个指数"，所以这里保留逐只变动供前端展示 ——
    这也是用户能自己判断"是不是国家队在买宽基"的关键线索。
    """
    out: List[Dict[str, Any]] = []
    for code, item in today.items():
        if not _is_broad_based(code, item.get("name", "")):
            continue
        base = baseline.get(code)
        if not base:
            continue
        delta = (item.get("shares") or 0.0) - (base.get("shares") or 0.0)
        out.append(
            {
                "code": code,
                "name": item.get("name", ""),
                "shares": item.get("shares"),
                "baseline_shares": base.get("shares"),
                "delta": delta,
            }
        )
    out.sort(key=lambda d: abs(d.get("delta") or 0.0), reverse=True)
    return out


def fetch_etf_shares(*, timeout: float, baseline: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """宽基 ETF 份额变动。

    - 沪市：接口支持指定日期，直接拉 T 与 T-5 两份快照对比，**无冷启动问题**。
    - 深市：接口只给当日快照，变动需要 ``baseline``（本地按日积累的历史快照）。
      没有基线时如实降级，不拿 0 冒充"无变动"。
    """
    days = _trading_days(6)
    today, five_ago = days[0], days[-1]

    sse_today = _fetch_sse_etf_shares(today, timeout=timeout)
    sse_base = _fetch_sse_etf_shares(five_ago, timeout=timeout) if len(days) > 1 else {}
    szse_today, szse_date = _fetch_szse_etf_shares(timeout=timeout)

    if not sse_today and not szse_today:
        return _failed("etf_share", "沪深两市 ETF 份额均抓取失败")

    sse_agg_today = _aggregate_broad_based(sse_today)
    sse_agg_base = _aggregate_broad_based(sse_base)
    szse_agg_today = _aggregate_broad_based(szse_today)

    sse_delta = None
    if sse_agg_base["total_shares"] > 0:
        sse_delta = sse_agg_today["total_shares"] - sse_agg_base["total_shares"]

    # 逐只变动只在沪市可算（深市没有历史快照）。
    sse_deltas = _broad_based_deltas(sse_today, sse_base)

    # 深市基线：优先用调用方传入的本地历史快照，没有就是没有，不拿 0 冒充"无变动"。
    szse_delta = None
    szse_baseline_date = ""
    if baseline:
        stored = baseline.get("szse") or {}
        base_shares = _to_float(stored.get("broad_based_total"))
        base_date = str(stored.get("date") or "")
        if base_shares is not None and base_date and base_date != szse_date:
            szse_delta = szse_agg_today["total_shares"] - base_shares
            szse_baseline_date = base_date

    degraded_parts: List[str] = []
    if sse_delta is None:
        degraded_parts.append("沪市缺少 5 日前快照，无法计算变动")
    if szse_delta is None:
        degraded_parts.append("深市接口不提供历史快照，份额变动需本地累积基线后才有")

    # 总变动只在沪市有值时给出；深市缺席时标注口径，不把它当 0 悄悄并进去。
    combined_delta = None
    if sse_delta is not None:
        combined_delta = sse_delta + (szse_delta or 0.0)

    return _envelope(
        "etf_share",
        ok=True,
        as_of=sse_agg_today["details"][0]["date"] if sse_agg_today["details"] else (
            szse_date or today.isoformat()
        ),
        value={
            "baseline_date": five_ago.isoformat(),
            "sse": {
                "total_shares": sse_agg_today["total_shares"],
                "baseline_shares": sse_agg_base["total_shares"] or None,
                "delta": sse_delta,
                "count": sse_agg_today["count"],
            },
            "szse": {
                "date": szse_date,
                "total_shares": szse_agg_today["total_shares"],
                "delta": szse_delta,
                "baseline_date": szse_baseline_date,
                "count": szse_agg_today["count"],
            },
            "combined_delta": combined_delta,
            "coverage": "沪市 5 日变动 + 深市当日快照" if szse_delta is None else "沪深两市 5 日变动",
            "delta_top": sse_deltas[:10],
            "sse_top": sse_agg_today["details"][:10],
            "szse_top": szse_agg_today["details"][:10],
        },
        raw={
            "note": "份额原始单位：上交所「万份」已折算为「份」；本字段展示单位：亿份",
            "sse_baseline_date": five_ago.isoformat(),
            "sse_etf_count": len(sse_today),
            "szse_etf_count": len(szse_today),
        },
        degraded=bool(degraded_parts),
        degraded_reason="；".join(degraded_parts),
    )


# ---------------------------------------------------------------------------
# 4. 两融余额（全市场沪深合计）
# ---------------------------------------------------------------------------


def fetch_margin(*, timeout: float, lookback_days: int = _MARGIN_LOOKBACK_DAYS) -> Dict[str, Any]:
    """全市场两融余额及其 5 日变化率。

    用 ``stock_margin_account_info``（沪深合计的长历史，单次请求）而不是分别打沪、
    深交易所：深交所接口只支持单日查询，要凑出 5 日序列得循环十几次请求。
    """

    def _load():
        import akshare as ak

        return ak.stock_margin_account_info()

    df, err = _call_with_timeout(_load, timeout=timeout, label="全市场两融账户信息")
    if df is None or getattr(df, "empty", True):
        return _failed("margin", err or "两融数据为空")

    cutoff = date.today() - timedelta(days=max(30, int(lookback_days)))
    series: List[Tuple[str, float, Optional[float]]] = []
    for record in df.to_dict("records"):
        raw_date = str(record.get("日期") or "").strip()
        balance = _to_float(record.get("融资余额"))
        if not raw_date or balance is None:
            continue
        try:
            parsed = datetime.strptime(raw_date[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        if parsed < cutoff:
            continue
        series.append((raw_date[:10], balance, _to_float(record.get("融券余额"))))

    if not series:
        return _failed("margin", "两融数据在回看窗口内没有记录")

    series.sort(key=lambda item: item[0])
    balances = [item[1] for item in series]
    latest_date, latest_balance, latest_short = series[-1]

    change_5d = None
    if len(balances) >= 6 and balances[-6]:
        change_5d = (balances[-1] - balances[-6]) / balances[-6]

    # 分位基于"5 日变化率"的历史分布，而不是余额绝对水平 —— 余额长期上行，
    # 绝对水平的分位永远接近 100%，没有信息量。
    rate_series: List[float] = []
    for idx in range(5, len(balances)):
        if balances[idx - 5]:
            rate_series.append((balances[idx] - balances[idx - 5]) / balances[idx - 5])
    percentile = _percentile_rank(rate_series, change_5d) if change_5d is not None else None

    return _envelope(
        "margin",
        ok=True,
        as_of=latest_date,
        value={
            "latest_date": latest_date,
            "financing_balance": latest_balance,
            "short_balance": latest_short,
            "change_5d": change_5d,
            "percentile": percentile,
            "history_points": len(balances),
            "series_tail": [
                {"date": d, "balance": b} for d, b, _ in series[-30:]
            ],
        },
        raw={
            "note": "融资余额 / 融券余额 单位：亿元；change_5d 为 5 个交易日变化率",
            "window": f"{series[0][0]} ~ {latest_date}",
        },
        degraded=change_5d is None,
        degraded_reason="两融历史点不足以计算 5 日变化率" if change_5d is None else "",
    )


# ---------------------------------------------------------------------------
# 5. 估值分位（乐咕乐股 + 全A）
# ---------------------------------------------------------------------------


def fetch_valuation(*, timeout: float) -> Dict[str, Any]:
    """沪深300 PE 分位 + 全A PB 分位。

    注意 ``stock_index_pe_lg`` 给的是市盈率**中位数**（不是分位），分位得自己从
    259 行历史序列里算；``stock_a_all_pb`` 则自带 quantile 字段。
    """

    def _load_index_pe():
        import akshare as ak

        return ak.stock_index_pe_lg(symbol="沪深300")

    def _load_all_pb():
        import akshare as ak

        return ak.stock_a_all_pb()

    pe_df, pe_err = _call_with_timeout(_load_index_pe, timeout=timeout, label="沪深300 PE 历史")
    pb_df, pb_err = _call_with_timeout(_load_all_pb, timeout=timeout, label="全A PB 分位")

    result: Dict[str, Any] = {}
    errors: List[str] = []

    if pe_df is not None and not getattr(pe_df, "empty", True):
        rows = pe_df.to_dict("records")
        series: List[float] = []
        latest_date = ""
        latest_pe = None
        for record in rows:
            value = _to_float(record.get("滚动市盈率"))
            if value is None:
                continue
            series.append(value)
            record_date = str(record.get("日期") or "").strip()[:10]
            if record_date >= latest_date:
                latest_date = record_date
                latest_pe = value
        if series and latest_pe is not None:
            result["hs300_pe"] = latest_pe
            result["hs300_pe_date"] = latest_date
            result["hs300_pe_percentile"] = _percentile_rank(series, latest_pe)
            result["hs300_pe_points"] = len(series)
            result["hs300_pe_series_tail"] = [
                {"date": str(r.get("日期") or "")[:10], "pe": _to_float(r.get("滚动市盈率"))}
                for r in rows[-24:]
                if _to_float(r.get("滚动市盈率")) is not None
            ]
    else:
        errors.append(f"沪深300 PE: {pe_err or '空数据'}")

    if pb_df is not None and not getattr(pb_df, "empty", True):
        rows = [r for r in pb_df.to_dict("records") if _to_float(r.get("middlePB")) is not None]
        if rows:
            latest = rows[-1]
            quantile = _to_float(latest.get("quantileInAllHistoryMiddlePB"))
            if quantile is None:
                # 上游早期行的分位列是 NaN，缺了就自己算，别让因子凭空消失。
                quantile = _percentile_rank(
                    [_to_float(r.get("middlePB")) for r in rows], _to_float(latest.get("middlePB"))
                )
            result["all_a_pb"] = _to_float(latest.get("middlePB"))
            result["all_a_pb_date"] = str(latest.get("date") or "")[:10]
            result["all_a_pb_percentile"] = quantile
            result["all_a_pb_points"] = len(rows)
    else:
        errors.append(f"全A PB: {pb_err or '空数据'}")

    if "hs300_pe_percentile" not in result and "all_a_pb_percentile" not in result:
        return _failed("valuation", "；".join(errors) or "估值数据全部不可用")

    percentiles = [
        p
        for p in (result.get("hs300_pe_percentile"), result.get("all_a_pb_percentile"))
        if p is not None
    ]
    result["combined_percentile"] = sum(percentiles) / len(percentiles) if percentiles else None

    degraded_parts: List[str] = []
    if "hs300_pe_percentile" not in result:
        degraded_parts.append("沪深300 PE 不可用，估值因子只用了全A PB")
    if "all_a_pb_percentile" not in result:
        degraded_parts.append("全A PB 不可用，估值因子只用了沪深300 PE")

    return _envelope(
        "valuation",
        ok=True,
        as_of=str(result.get("hs300_pe_date") or result.get("all_a_pb_date") or ""),
        value=result,
        raw={
            "note": "分位为 0-1；combined_percentile 是两者的均值，打分时取反向",
            "errors": errors,
        },
        degraded=bool(degraded_parts),
        degraded_reason="；".join(degraded_parts),
    )


# ---------------------------------------------------------------------------
# 6. 关注个股候选池
# ---------------------------------------------------------------------------


def fetch_stock_candidates(*, timeout: float) -> Dict[str, Any]:
    """个股层面的补充数据：龙虎榜个股统计 + 同花顺当日资金方向。

    两个来源的定位不同，不要混为一谈：

    - **龙虎榜个股统计**（东财，近一月）：带机构净买额与多周期涨跌幅，是候选池的正经来源。
    - **同花顺个股资金流**：这一份是**当日涨幅榜**（落地页默认按涨跌幅排序），不是
      "主力净流入榜" —— 实测涨幅榜前排的净额大量为负。因此它只用来给候选池里的个股
      补充资金方向、并标出"涨上榜但资金净流出"的个股，不单独作为入选路径。
    """

    def _load_statistic():
        import akshare as ak

        return ak.stock_lhb_stock_statistic_em(symbol="近一月")

    stat_df, stat_err = _call_with_timeout(
        _load_statistic, timeout=timeout, label="东财龙虎榜个股统计"
    )

    def _load_stock_flow():
        from src.services.industry_funds import ths_client

        return ths_client.fetch_stock_fund_flow(
            timeout=min(timeout, 25.0), max_pages=_STOCK_FLOW_MAX_PAGES
        )

    flow_rows, flow_err = _call_with_timeout(
        _load_stock_flow, timeout=min(timeout, 25.0) + 10.0, label="同花顺个股资金流(涨幅榜)"
    )

    stocks: List[Dict[str, Any]] = []
    if stat_df is not None and not getattr(stat_df, "empty", True):
        for record in stat_df.to_dict("records"):
            code = str(record.get("代码") or "").strip()
            if not code:
                continue
            stocks.append(
                {
                    "code": code.zfill(6) if code.isdigit() else code,
                    "name": str(record.get("名称") or "").strip(),
                    "last_listed": str(record.get("最近上榜日") or "").strip()[:10],
                    "close": _to_float(record.get("收盘价")),
                    "change_pct": _to_float(record.get("涨跌幅")),
                    "listed_count": _to_float(record.get("上榜次数")),
                    "lhb_net": _to_float(record.get("龙虎榜净买额")),
                    "institution_net": _to_float(record.get("机构买入净额")),
                    "buyer_count": _to_float(record.get("买方机构次数")),
                    "seller_count": _to_float(record.get("卖方机构次数")),
                    "change_1m": _to_float(record.get("近1个月涨跌幅")),
                    "change_3m": _to_float(record.get("近3个月涨跌幅")),
                }
            )

    flow_stocks: List[Dict[str, Any]] = []
    for record in flow_rows or []:
        code = _normalize_code(record.get("code"))
        if not code:
            continue
        flow_stocks.append(
            {
                "code": code,
                "name": str(record.get("name") or "").strip(),
                # 单位：元（上游是 "3.16亿" / "-1228.83万" 这种带后缀的字符串，已解析）
                "main_net": _to_float(record.get("main_net")),
                "change_pct": _to_float(record.get("change_pct")),
                "turnover_rate": _to_float(record.get("turnover_rate")),
            }
        )

    degraded_parts: List[str] = []
    if not stocks:
        degraded_parts.append(f"龙虎榜个股统计不可用（{stat_err or '空数据'}）")
    if not flow_stocks:
        degraded_parts.append(
            f"同花顺个股资金流不可用（{flow_err or '未返回数据'}），候选池缺少个股资金方向"
        )

    if not stocks and not flow_stocks:
        return _failed("stock_candidates", "；".join(degraded_parts))

    return _envelope(
        "stock_candidates",
        ok=True,
        as_of=max((s.get("last_listed") or "" for s in stocks), default="")
        or _trading_days(1)[0].isoformat(),
        value={"lhb_stocks": stocks, "flow_stocks": flow_stocks},
        raw={
            "note": (
                "lhb_net / institution_net 单位：元；"
                "flow_stocks.main_net 单位：元，来源为同花顺当日涨幅榜（非净流入榜）"
            ),
            "lhb_count": len(stocks),
            "flow_count": len(flow_stocks),
            "flow_source": SOURCE_LABELS["stock_fund_flow"] if flow_stocks else "",
        },
        degraded=bool(degraded_parts),
        degraded_reason="；".join(degraded_parts),
    )


__all__ = [
    "fetch_industry_fund_flow",
    "fetch_lhb_institution",
    "fetch_etf_shares",
    "fetch_margin",
    "fetch_valuation",
    "fetch_stock_candidates",
]
