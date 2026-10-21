# -*- coding: utf-8 -*-
"""涨停数据中心适配器：同花顺 ``data.10jqka.com.cn/dataapi/limit_up/*`` 为主，东财 ``push2ex`` 为辅。

为什么不走 akshare 的 ``stock_zt_pool_*_em``：那一组函数内部 ``requests.get`` 不带超时，
项目里只能用 ``_akshare_call_with_timeout`` 起子进程兜底，一次刷新要打十几个池子，开销不划算；
而且 akshare 只覆盖东财，拿不到同花顺独有的「涨停原因」。这里直接打上游、自己控超时。

上游特性（2026-10-20 实测）：

- 同花顺：**无 token / 无 cookie**，``status_code == 0`` 即成功，**支持历史日期**；
  非交易日返回 ``total == 0`` 的空池（不报错）。每只股票带 ``reason_type``（涨停原因，
  ``+`` 分隔）、``high_days``（「21天12板」，N 天内 M 次涨停，不一定连续）、``limit_up_type``
  （换手板 / 一字板 / T字板）、``limit_up_suc_rate``（历史封板率）。首封 / 末封时间是
  **epoch 秒字符串**。``filter=HS,GEM2STAR`` 不含北交所。
- 同花顺的 ``high_days_value`` 编码为 ``板数 << 16 | 天数``，与 ``high_days`` 文本一致。
- 连板数（连续涨停天数）**不在池接口里**，只有 ``continuous_limit_up`` 天梯给出，
  天梯只列 ≥2 板，不在天梯里的涨停股就是 1 板。
- 东财 ``getYesterdayZTPool?date=X`` 返回的是 **X 的上一交易日涨停股在 X 当天的表现**
  （``zdp`` 为 X 当日涨跌幅，``ylbc`` 为昨日连板数）。东财池子含北交所与 ST，价格单位为厘。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

_THS_BASE = "https://data.10jqka.com.cn/dataapi/limit_up"
_EM_BASE = "https://push2ex.eastmoney.com"
_EM_UT = "7eea3edcaed734bea9cbfc24409ed989"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# 同花顺池接口要求的字段 id 列表；照抄页面请求，少了会缺列。
_THS_FIELDS = "199112,10,9001,330323,330324,330325,9002,330329,133971,133970,1968584,3475914,9003,9004"
_THS_FILTER = "HS,GEM2STAR"
_THS_PAGE_SIZE = 200
_THS_MAX_PAGES = 10

# 同花顺三类池：路径 → 默认排序字段。
THS_POOLS = {
    "zt": ("limit_up_pool", "330324"),
    "zb": ("open_limit_pool", "199112"),
    "dt": ("lower_limit_pool", "199112"),
}

# 东财池：接口名 → 排序参数。
EM_POOLS = {
    "zt": ("getTopicZTPool", "fbt:asc"),
    "zb": ("getTopicZBPool", "fbt:asc"),
    "dt": ("getTopicDTPool", "fund:asc"),
    "previous": ("getYesterdayZTPool", "zs:desc"),
    "strong": ("getTopicQSPool", "zdp:desc"),
    "sub_new": ("getTopicCXPooll", "ods:asc"),  # 上游接口名本身就是两个 l
}

_DEFAULT_TIMEOUT = 12.0
_MAX_ATTEMPTS = 2
_BEIJING = timezone(timedelta(hours=8))
_HIGH_DAYS_RE = re.compile(r"(\d+)\s*天\s*(\d+)\s*板")


class LimitUpUpstreamError(RuntimeError):
    """上游不可用、响应不完整或结构不符合预期。"""


# ---------------------------------------------------------------------- 通用工具


def to_ymd(value: Any) -> str:
    """把 ``2026-10-20`` / ``20261020`` / ``date`` 统一成上游要的 ``YYYYMMDD``。"""
    if value is None:
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y%m%d")
    return re.sub(r"\D", "", str(value))[:8]


def to_iso_date(value: Any) -> str:
    """``YYYYMMDD`` → ``YYYY-MM-DD``；无法识别时原样返回。"""
    text = to_ymd(value)
    if len(text) == 8:
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return str(value or "")


def is_bse_code(code: str) -> bool:
    """北交所代码：920xxx、8xxxxx、43xxxx。"""
    text = str(code or "").strip()
    return text.startswith(("92", "8", "43"))


def is_st_name(name: str) -> bool:
    return "ST" in str(name or "").upper()


def in_universe(code: str, name: str, *, include_bse: bool = False) -> bool:
    """统计口径：沪深主板 + 创业板 + 科创板，剔除 ST；北交所可选。"""
    if not re.fullmatch(r"\d{6}", str(code or "").strip()):
        return False
    if is_st_name(name):
        return False
    if is_bse_code(code) and not include_bse:
        return False
    return True


def parse_high_days(text: Any) -> Tuple[int, int]:
    """「21天12板」→ ``(21, 12)``；「首板」→ ``(1, 1)``；无法识别 → ``(0, 0)``。"""
    raw = str(text or "").strip()
    if not raw:
        return 0, 0
    if raw == "首板":
        return 1, 1
    match = _HIGH_DAYS_RE.search(raw)
    if match:
        return int(match.group(1)), int(match.group(2))
    return 0, 0


def epoch_to_hhmmss(value: Any) -> str:
    """同花顺首封 / 末封时间（epoch 秒字符串）→ 北京时间 ``HHMMSS``。"""
    try:
        seconds = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return ""
    if seconds <= 0:
        return ""
    return datetime.fromtimestamp(seconds, tz=_BEIJING).strftime("%H%M%S")


def int_to_hhmmss(value: Any) -> str:
    """东财 ``fbt`` 之类的 ``92500`` → ``092500``。"""
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return ""
    if number <= 0:
        return ""
    return f"{number:06d}"


def _num(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed:  # NaN
        return None
    return parsed


def _get_json(url: str, params: Dict[str, Any], *, timeout: Optional[float]) -> Dict[str, Any]:
    """GET 一个 JSON；5xx、截断 body、非 JSON 都重试一次，最后一次失败如实抛出。"""
    last_error: Optional[Exception] = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            response = requests.get(
                url, params=params, headers=_HEADERS, timeout=float(timeout or _DEFAULT_TIMEOUT)
            )
            if response.status_code != 200:
                raise LimitUpUpstreamError(f"上游 HTTP {response.status_code}: {url}")
            try:
                payload = response.json()
            except ValueError as exc:
                raise LimitUpUpstreamError(
                    f"上游响应不是完整 JSON（{len(response.content)} 字节）: {url}"
                ) from exc
            if not isinstance(payload, dict):
                raise LimitUpUpstreamError(f"上游响应不是 JSON 对象: {url}")
            return payload
        except (LimitUpUpstreamError, requests.RequestException) as exc:
            last_error = exc
            logger.debug("[涨停数据] 第 %s/%s 次请求失败: %s", attempt, _MAX_ATTEMPTS, exc)
    raise LimitUpUpstreamError(f"上游请求失败（已重试 {_MAX_ATTEMPTS} 次）: {last_error}")


# ---------------------------------------------------------------------- 同花顺


def _ths_payload(path: str, params: Dict[str, Any], *, timeout: Optional[float]) -> Any:
    payload = _get_json(f"{_THS_BASE}/{path}", params, timeout=timeout)
    if payload.get("status_code") != 0:
        raise LimitUpUpstreamError(
            f"同花顺 {path} 返回 status_code={payload.get('status_code')} msg={payload.get('status_msg')}"
        )
    if "data" not in payload:
        raise LimitUpUpstreamError(f"同花顺 {path} 响应缺少 data 字段")
    return payload["data"]


def normalize_ths_row(item: Dict[str, Any]) -> Dict[str, Any]:
    """同花顺池接口一行 → 统一行结构（连板数 ``boards`` 由天梯回填）。"""
    hd_days, hd_boards = parse_high_days(item.get("high_days"))
    circ_mv = _num(item.get("currency_value"))
    turnover = _num(item.get("turnover_rate"))
    return {
        "code": str(item.get("code") or "").strip(),
        "name": str(item.get("name") or "").strip(),
        "price": _num(item.get("latest")),
        "change_pct": _num(item.get("change_rate")),
        "turnover_rate": turnover,
        "circ_mv": circ_mv,
        # 池接口不给成交额，用 换手率 × 流通市值 近似（同一口径下排序足够）。
        "amount": (turnover / 100.0 * circ_mv) if (turnover is not None and circ_mv) else None,
        "boards": 1,
        "hd_days": hd_days,
        "hd_boards": hd_boards,
        "high_days": str(item.get("high_days") or "").strip(),
        "limit_up_type": str(item.get("limit_up_type") or "").strip(),
        "open_num": int(_num(item.get("open_num")) or 0),
        "change_tag": str(item.get("change_tag") or "").strip(),
        "first_time": epoch_to_hhmmss(item.get("first_limit_up_time")),
        "last_time": epoch_to_hhmmss(item.get("last_limit_up_time")),
        "seal_amount": _num(item.get("order_amount")),
        "suc_rate": _num(item.get("limit_up_suc_rate")),
        "reason": str(item.get("reason_type") or "").strip(),
        "industry": "",
        "is_new": bool(item.get("is_new")),
        "is_again_limit": bool(item.get("is_again_limit")),
        "source": "ths",
    }


def fetch_ths_pool(
    kind: str, trade_date: Any = None, *, timeout: Optional[float] = None
) -> Dict[str, Any]:
    """拉一个同花顺池（``zt`` 涨停 / ``zb`` 炸板 / ``dt`` 跌停），自动翻页。

    ``trade_date`` 为空时取上游的「最新交易日」（收盘后即当天，盘中为当天实时）。
    返回 ``{date, rows, counts, trade_status}``：``counts`` 是上游顶层的涨停 / 跌停统计
    （含「曾涨停」``history_num`` 与封板率 ``rate``），``trade_status`` 为 ``closed`` 等。
    """
    if kind not in THS_POOLS:
        raise ValueError(f"未知的同花顺池: {kind}")
    path, order_field = THS_POOLS[kind]
    rows: List[Dict[str, Any]] = []
    meta: Dict[str, Any] = {}
    for page in range(1, _THS_MAX_PAGES + 1):
        params: Dict[str, Any] = {
            "page": page,
            "limit": _THS_PAGE_SIZE,
            "field": _THS_FIELDS,
            "filter": _THS_FILTER,
            "order_field": order_field,
            "order_type": 0,
        }
        ymd = to_ymd(trade_date)
        if ymd:
            params["date"] = ymd
        data = _ths_payload(path, params, timeout=timeout)
        if not isinstance(data, dict) or not isinstance(data.get("info"), list):
            raise LimitUpUpstreamError(f"同花顺 {path} 的 data.info 结构异常")
        if page == 1:
            meta = data
        rows.extend(normalize_ths_row(item) for item in data["info"] if isinstance(item, dict))
        total = int(_num((data.get("page") or {}).get("total")) or 0)
        if len(rows) >= total or not data["info"]:
            break
    return {
        "date": to_iso_date(meta.get("date") or trade_date),
        "rows": rows,
        "counts": {
            "limit_up": (meta.get("limit_up_count") or {}).get("today") or {},
            "limit_down": (meta.get("limit_down_count") or {}).get("today") or {},
            "limit_up_yesterday": (meta.get("limit_up_count") or {}).get("yesterday") or {},
        },
        "trade_status": str((meta.get("trade_status") or {}).get("id") or ""),
    }


def fetch_ths_ladder(trade_date: Any = None, *, timeout: Optional[float] = None) -> Dict[str, int]:
    """连板天梯 → ``{code: 连板数}``（只含 ≥2 板）。"""
    params: Dict[str, Any] = {"filter": _THS_FILTER}
    ymd = to_ymd(trade_date)
    if ymd:
        params["date"] = ymd
    data = _ths_payload("continuous_limit_up", params, timeout=timeout)
    if not isinstance(data, list):
        raise LimitUpUpstreamError("同花顺 continuous_limit_up 的 data 不是列表")
    ladder: Dict[str, int] = {}
    for tier in data:
        if not isinstance(tier, dict):
            continue
        height = int(_num(tier.get("height")) or 0)
        for item in tier.get("code_list") or []:
            code = str((item or {}).get("code") or "").strip()
            if code:
                ladder[code] = int(_num((item or {}).get("continue_num")) or height)
    return ladder


def fetch_ths_blocks(trade_date: Any = None, *, timeout: Optional[float] = None) -> List[Dict[str, Any]]:
    """涨停题材榜（同花顺概念板块）。body 很大（>200KB），``reason_info`` 长文丢弃不留。"""
    params: Dict[str, Any] = {"filter": _THS_FILTER}
    ymd = to_ymd(trade_date)
    if ymd:
        params["date"] = ymd
    data = _ths_payload("block_top", params, timeout=timeout)
    if not isinstance(data, list):
        raise LimitUpUpstreamError("同花顺 block_top 的 data 不是列表")
    blocks: List[Dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        members = []
        for stock in item.get("stock_list") or []:
            if not isinstance(stock, dict):
                continue
            members.append({
                "code": str(stock.get("code") or "").strip(),
                "name": str(stock.get("name") or "").strip(),
                "boards": int(_num(stock.get("continue_num")) or 1),
                "is_st": bool(stock.get("is_st")),
            })
        blocks.append({
            "code": str(item.get("code") or "").strip(),
            "name": str(item.get("name") or "").strip(),
            "change_pct": _num(item.get("change")),
            "limit_up_num": int(_num(item.get("limit_up_num")) or 0),
            "continuous_num": int(_num(item.get("continuous_plate_num")) or 0),
            "high": str(item.get("high") or "").strip(),
            "days": int(_num(item.get("days")) or 0),
            "members": members,
        })
    return blocks


# ---------------------------------------------------------------------- 东财


def normalize_em_row(kind: str, item: Dict[str, Any]) -> Dict[str, Any]:
    """东财池一行 → 统一行结构。价格单位是厘，统一换成元。"""
    price = _num(item.get("p"))
    stat = item.get("zttj") or {}
    hd_days = int(_num(stat.get("days")) or 0)
    hd_boards = int(_num(stat.get("ct")) or 0)
    open_num = int(_num(item.get("zbc")) or 0)
    row = {
        "code": str(item.get("c") or "").strip(),
        "name": str(item.get("n") or "").strip(),
        "price": (price / 1000.0) if price is not None else None,
        "change_pct": _num(item.get("zdp")),
        "turnover_rate": _num(item.get("hs")),
        "circ_mv": _num(item.get("ltsz")),
        "amount": _num(item.get("amount")),
        "boards": int(_num(item.get("lbc")) or 1) if kind == "zt" else 0,
        "hd_days": hd_days,
        "hd_boards": hd_boards,
        "high_days": ("首板" if hd_boards == 1 and hd_days == 1 else f"{hd_days}天{hd_boards}板") if hd_boards else "",
        # 东财不区分一字 / 换手：首封即末封且开盘封死才算一字，粗略判断仅供兜底展示。
        "limit_up_type": "",
        "open_num": open_num,
        "change_tag": "LIMIT_BACK" if (kind == "zt" and open_num > 0) else "",
        "first_time": int_to_hhmmss(item.get("fbt")),
        "last_time": int_to_hhmmss(item.get("lbt")),
        "seal_amount": _num(item.get("fund")),
        "suc_rate": None,
        "reason": "",
        "industry": str(item.get("hybk") or "").strip(),
        "is_new": False,
        "is_again_limit": open_num > 0,
        "source": "em",
    }
    if kind == "zt" and row["first_time"] and row["first_time"] == row["last_time"] and row["first_time"] <= "093000":
        row["limit_up_type"] = "一字板" if (row["turnover_rate"] or 0) < 3 else "换手板"
    elif kind == "zt":
        row["limit_up_type"] = "换手板"
    if kind == "previous":
        row["y_boards"] = int(_num(item.get("ylbc")) or 1)
        row["y_first_time"] = int_to_hhmmss(item.get("yfbt"))
    if kind == "dt":
        row["dt_days"] = int(_num(item.get("days")) or 1)
    if kind == "sub_new":
        row["open_days"] = int(_num(item.get("ods")) or 0)
        row["ipo_date"] = to_iso_date(item.get("ipod"))
    if kind == "strong":
        row["volume_ratio"] = _num(item.get("lb"))
        row["new_high"] = bool(item.get("nh"))
    return row


def fetch_em_pool(kind: str, trade_date: Any, *, timeout: Optional[float] = None) -> List[Dict[str, Any]]:
    """拉一个东财池。``trade_date`` 必填（东财不接受空日期）。

    东财只保留近 30 个交易日左右的历史，更早的日期会返回空池或 ``rc != 0``。
    """
    if kind not in EM_POOLS:
        raise ValueError(f"未知的东财池: {kind}")
    endpoint, sort = EM_POOLS[kind]
    ymd = to_ymd(trade_date)
    if not ymd:
        raise ValueError("东财涨停池必须指定日期")
    payload = _get_json(
        f"{_EM_BASE}/{endpoint}",
        {
            "ut": _EM_UT,
            "dpt": "wz.ztzt",
            "Pageindex": 0,
            "pagesize": 10000,
            "sort": sort,
            "date": ymd,
        },
        timeout=timeout,
    )
    if payload.get("rc") not in (0, None):
        raise LimitUpUpstreamError(f"东财 {endpoint} 返回 rc={payload.get('rc')}")
    data = payload.get("data")
    if data is None:
        # 非交易日 / 超出保留期时东财返回 data=null，按空池处理。
        return []
    pool = data.get("pool") if isinstance(data, dict) else None
    if not isinstance(pool, list):
        raise LimitUpUpstreamError(f"东财 {endpoint} 的 data.pool 结构异常")
    return [normalize_em_row(kind, item) for item in pool if isinstance(item, dict)]


# ---------------------------------------------------------------------- 盘中事件流


def fetch_ths_changes(*, timeout: Optional[float] = None) -> List[Dict[str, Any]]:
    """同花顺涨停数据中心的封板事件流（页面每 5 秒轮询的那个接口）。

    返回当天全部事件：``FIRST_LIMIT`` 首次封板、``LIMIT_BACK`` 回封、``LIMIT_FAILED`` 炸板。
    ``time`` 是 epoch 秒；开盘前可能还是上一交易日的事件，调用方要按日期过滤。
    """
    data = _ths_payload("change", {"_": int(datetime.now().timestamp() * 1000)}, timeout=timeout)
    if not isinstance(data, list):
        raise LimitUpUpstreamError("同花顺 change 的 data 不是列表")
    events = []
    for item in data:
        if not isinstance(item, dict) or not item.get("code"):
            continue
        try:
            epoch = int(float(item.get("time")))
        except (TypeError, ValueError):
            continue
        moment = datetime.fromtimestamp(epoch, tz=_BEIJING)
        events.append({
            "code": str(item["code"]).strip(),
            "name": str(item.get("name") or "").strip(),
            "type": str(item.get("type") or "").strip(),
            "date": moment.strftime("%Y-%m-%d"),
            "time": moment.strftime("%H%M%S"),
            "order_amount": _num(item.get("order_amount")),
        })
    return events


# 东财盘口异动类型：4 封涨停、16 打开涨停、8201 火箭发射（涨速快）、8202 快速反弹。
# 8201/8202 的 ``i`` 是 "涨幅小数,触发价,涨幅小数"，正好用来抓「还没涨停但正在拉升」的票。
_EM_CHANGE_TYPES = {
    "seal": (4, "FIRST_LIMIT"),
    "open": (16, "LIMIT_FAILED"),
    "rise": (8201, "RISE"),
    "rebound": (8202, "REBOUND"),
}


def fetch_em_changes(kind: str, *, timeout: Optional[float] = None) -> List[Dict[str, Any]]:
    """东财盘口异动：``seal`` 封涨停板（type 4）/ ``open`` 打开涨停板（type 16）。

    东财只给 ``HHMMSS`` 不给日期，开盘前返回的可能是上一交易日的数据，调用方要按时段过滤。
    """
    if kind not in _EM_CHANGE_TYPES:
        raise ValueError(f"未知的东财异动类型: {kind}")
    type_id, normalized = _EM_CHANGE_TYPES[kind]
    payload = _get_json(
        f"{_EM_BASE}/getAllStockChanges",
        {"type": type_id, "pageindex": 0, "pagesize": 5000, "ut": _EM_UT, "dpt": "wzchanges"},
        timeout=timeout,
    )
    rows = ((payload.get("data") or {}).get("allstock")) or []
    out = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("c"):
            continue
        change = price = None
        parts = str(row.get("i") or "").split(",")
        if len(parts) >= 2:
            change, price = _num(parts[0]), _num(parts[1])
        out.append({
            "code": str(row["c"]).strip(),
            "name": str(row.get("n") or "").strip(),
            "type": normalized,
            "date": "",
            "time": int_to_hhmmss(row.get("tm")),
            "order_amount": None,
            "change": change * 100 if normalized in ("RISE", "REBOUND") and change is not None else None,
            "price": price,
        })
    return out


# ---------------------------------------------------------------------- 个股所属概念（东财选股器）


_EM_XUANGU_URL = "https://data.eastmoney.com/dataapi/xuangu/list"
_EM_XUANGU_STY = "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,CONCEPT,INDUSTRY"


def fetch_em_concepts(codes: List[str], *, timeout: Optional[float] = None) -> Dict[str, Dict[str, Any]]:
    """批量取个股所属概念与行业（东财选股器，一次最多几百只）。

    用途：盘中候选股当下没有涨停原因、本地也没有历史涨停记录时，用它兜底判断「属于哪个板块」。
    返回 ``{code: {"industry": str, "concepts": [str, ...]}}``；失败抛异常，由调用方降级。
    """
    wanted = [str(c).strip() for c in codes if str(c).strip()]
    out: Dict[str, Dict[str, Any]] = {}
    for start in range(0, len(wanted), 200):
        batch = wanted[start:start + 200]
        flt = "(SECURITY_CODE in (" + ",".join(f'"{c}"' for c in batch) + "))"
        payload = _get_json(
            _EM_XUANGU_URL,
            {"st": "SECURITY_CODE", "sr": "-1", "ps": len(batch), "p": 1, "sty": _EM_XUANGU_STY,
             "filter": flt, "source": "SELECT", "client": "WEB"},
            timeout=timeout,
        )
        rows = ((payload.get("result") or {}).get("data")) or []
        for row in rows:
            code = str(row.get("SECURITY_CODE") or "").strip()
            if not code:
                continue
            out[code] = {
                "name": str(row.get("SECURITY_NAME_ABBR") or "").strip(),
                "industry": str(row.get("INDUSTRY") or "").strip(),
                "concepts": [str(x).strip() for x in (row.get("CONCEPT") or []) if str(x).strip()],
            }
    return out


# ---------------------------------------------------------------------- 腾讯批量行情


_TENCENT_URL = "https://qt.gtimg.cn/q="
_TENCENT_BATCH = 600


def tencent_symbol(code: str) -> str:
    text = str(code or "").strip()
    return ("sh" if text.startswith(("6", "9")) else "sz") + text


def parse_tencent_line(line: str) -> Optional[Dict[str, Any]]:
    """``v_sh600000="1~名称~代码~现价~昨收~今开~...";`` → 行情 dict。字段位置 2026-10-20 实测：

    3 现价、4 昨收、5 今开、20 卖一量、30 时间戳（YYYYMMDDHHMMSS）、33 最高、34 最低、
    38 换手率、44 流通市值（亿）、47 涨停价、48 跌停价。
    """
    if "~" not in line:
        return None
    fields = line.split("~")
    if len(fields) < 49:
        return None
    try:
        return {
            "code": fields[2],
            "name": fields[1],
            "price": float(fields[3]),
            "prev_close": float(fields[4]),
            "open": float(fields[5]),
            "sell1_volume": int(float(fields[20] or 0)),
            "time": fields[30],
            "high": float(fields[33]),
            "low": float(fields[34]),
            "turnover_rate": _num(fields[38]),
            "circ_mv": (_num(fields[44]) or 0) * 1e8 or None,
            "limit_up": float(fields[47]),
            "limit_down": float(fields[48]),
            "volume_ratio": _num(fields[49]),
        }
    except (TypeError, ValueError):
        return None


def fetch_tencent_quotes(codes: List[str], *, timeout: Optional[float] = None) -> Dict[str, Dict[str, Any]]:
    """批量行情快照（腾讯，无 token），每批 600 只。单批失败只少这一批，不整体报错。"""
    out: Dict[str, Dict[str, Any]] = {}
    symbols = [tencent_symbol(c) for c in codes if c]
    for start in range(0, len(symbols), _TENCENT_BATCH):
        batch = symbols[start:start + _TENCENT_BATCH]
        try:
            response = requests.get(_TENCENT_URL + ",".join(batch), headers=_HEADERS,
                                    timeout=float(timeout or _DEFAULT_TIMEOUT))
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("[腾讯行情] 批量取数失败（%d 只）: %s", len(batch), exc)
            continue
        for line in response.text.split(";"):
            quote = parse_tencent_line(line)
            if quote:
                out[quote["code"]] = quote
    return out


__all__ = [
    "EM_POOLS",
    "THS_POOLS",
    "LimitUpUpstreamError",
    "epoch_to_hhmmss",
    "fetch_em_changes",
    "fetch_em_concepts",
    "fetch_em_pool",
    "fetch_tencent_quotes",
    "fetch_ths_changes",
    "fetch_ths_blocks",
    "fetch_ths_ladder",
    "fetch_ths_pool",
    "in_universe",
    "int_to_hhmmss",
    "is_bse_code",
    "is_st_name",
    "normalize_em_row",
    "normalize_ths_row",
    "parse_high_days",
    "parse_tencent_line",
    "tencent_symbol",
    "to_iso_date",
    "to_ymd",
]
