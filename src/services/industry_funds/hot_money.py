# -*- coding: utf-8 -*-
"""游资：龙虎榜营业部席位的买卖矩阵（某只股票 × 各游资席位，当天买卖额）。

口径说明（页面上也要写）：
- 游资没有官方身份，交易所只披露**营业部席位**。这里按「最近两周买入额最大的活跃营业部」选列，
  一个席位背后可能是某位知名游资，也可能多人共用，所以列名用营业部名，不做身份断言；
- 只覆盖**上榜**的票（龙虎榜门槛），没上榜的交易看不到；
- 「沪股通专用 / 深股通专用 / 机构专用」是通道和机构，不是游资，剔除；
- 同一只票同一天可能因为多个上榜原因重复出现，按日期 + 股票汇总。

数据源（免费）：
- 每日活跃营业部 ``stock_lhb_hyyyb_em``（拿到席位及其当日买入总额、营业部代码）；
- 营业部历史明细 ``stock_lhb_yyb_detail_em``（按席位拉全部历史，本地筛出窗口内的日期与股票）。
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Tuple

from src.services.industry_funds.fetchers import _call_with_timeout, _to_float
from src.services.industry_funds.flows import _cached, _dates, _yi
from src.services.industry_funds.seat_aliases import PERSON_ALIASES, alias_entry

logger = logging.getLogger(__name__)

SEAT_LIMIT_DEFAULT = 10
MAX_SEATS = 20
ALL_SEATS_LIMIT = 60        # 「游资席位」区最多回这么多条，够两行收起也能展开看全部
ROW_LIMIT = 60          # 页面一次展示多少行（按净买绝对值排序）
EXCLUDED_SEATS = ("沪股通专用", "深股通专用", "机构专用")


def _short_name(name: str) -> str:
    """营业部名压成列头能放下的短名。"""
    text = str(name or "").strip()
    for token in ("股份有限公司", "有限责任公司", "证券营业部", "营业部", "证券"):
        text = text.replace(token, "")
    return text[:18] or str(name or "")[:18]


def _seat_columns(days: int, seats: int, *, timeout: float) -> Tuple[List[Dict[str, Any]], str, List[Dict[str, Any]], Dict[str, Dict[str, float]]]:
    """窗口内的活跃营业部，按「能不能识别成游资」排优先级选列。

    排序：人名类昵称（章盟主、孙哥…）> 街道类昵称 > 未识别的营业部，类别内按买入额倒序。
    同一个昵称下的多个营业部（例如几家「益田路」「金田路」）**合并成一列**，避免同名列重复出现。
    """
    window = _dates(days)
    if not window:
        return [], "取不到交易日历", [], {}
    start, end = window[0].replace("-", ""), window[-1].replace("-", "")

    def _load():
        import akshare as ak

        return ak.stock_lhb_hyyyb_em(start_date=start, end_date=end)

    df, err = _call_with_timeout(_load, timeout=timeout, label="东财每日活跃营业部")
    if df is None or getattr(df, "empty", True):
        return [], err or "活跃营业部数据为空", [], {}

    totals: Dict[str, Dict[str, Any]] = {}
    window_dates = set(window)
    seat_days: Dict[str, Dict[str, float]] = {}
    for record in df.to_dict("records"):
        name = str(record.get("营业部名称") or "").strip()
        code = str(record.get("营业部代码") or "").strip()
        if not name or not code or any(bad in name for bad in EXCLUDED_SEATS):
            continue
        buy, sell = _to_float(record.get("买入总金额")) or 0.0, _to_float(record.get("卖出总金额")) or 0.0
        row = totals.setdefault(code, {"code": code, "name": name, "buy": 0.0, "sell": 0.0,
                                       "net": 0.0, "days": 0})
        row["buy"] += buy
        row["sell"] += sell
        row["net"] += _to_float(record.get("总买卖净额")) or 0.0
        row["days"] += 1
        day = str(record.get("上榜日") or "")[:10]
        if day in window_dates:
            slot = seat_days.setdefault(day, {"seats": 0, "buy": 0.0, "sell": 0.0})
            slot["seats"] += 1
            slot["buy"] += buy
            slot["sell"] += sell

    groups: Dict[str, Dict[str, Any]] = {}
    for row in totals.values():
        entry = alias_entry(row["name"]) or {}
        alias = entry.get("alias")
        key = alias or row["code"]
        group = groups.setdefault(key, {"code": key, "codes": [], "names": [], "alias": alias,
                                        "alias_source": entry.get("source") or "", "buy": 0.0, "sell": 0.0,
                                        "net": 0.0, "days": 0})
        group["codes"].append(row["code"])
        group["names"].append(row["name"])
        group["buy"] += row["buy"]
        group["sell"] += row["sell"]
        group["net"] += row["net"]
        group["days"] += row["days"]

    for group in groups.values():
        group["buy"] = _yi(group["buy"])
        group["sell"] = _yi(group["sell"])
        group["net"] = _yi(group["net"])
        group["name"] = "、".join(group["names"][:4]) + ("…" if len(group["names"]) > 4 else "")
        group["short"] = _short_name(group["names"][0])
        group["kind"] = 0 if group["alias"] in PERSON_ALIASES else (1 if group["alias"] else 2)

    limit = max(1, min(seats, MAX_SEATS))
    # 矩阵的列按**买入额**取前 N：列是「这周钱最多流向哪些席位」，昵称只决定列头怎么显示。
    # 想按昵称找人（孙哥、流沙河这些当周买得少的），看下面的「游资席位」区，那里是全部席位。
    ordered = sorted(groups.values(), key=lambda g: -g["buy"])[:limit]
    return ordered, "", list(groups.values()), seat_days


_DETAIL_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_DETAIL_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"}
_DETAIL_PAGE_SIZE = 500
_DETAIL_MAX_PAGES = 10
MULTI_DAY_MARK = "连续三个交易日"


def _seat_trades(code: str, window_dates: set, *, timeout: float) -> Tuple[Dict[str, Dict[str, Dict[str, float]]], str]:
    """一个席位在窗口内的逐日逐股买卖：``{date: {stock_code: {name, buy, sell}}}``。

    直接调东财 datacenter：**在服务端按日期过滤**，只回窗口内的几十行（akshare 的同名函数会把
    这个席位 20 年的全部历史翻页拉下来，实测单个席位经常超过 30 秒）。
    """
    import requests

    days = sorted(window_dates)
    if not days:
        return {}, "窗口为空"
    flt = (f'(OPERATEDEPT_CODE="{code}")(TRADE_DATE>=\'{days[0]}\')(TRADE_DATE<=\'{days[-1]}\')')
    raw: List[Dict[str, Any]] = []
    for page in range(1, _DETAIL_MAX_PAGES + 1):
        params = {
            "sortColumns": "TRADE_DATE,SECURITY_CODE", "sortTypes": "-1,1",
            "pageSize": str(_DETAIL_PAGE_SIZE), "pageNumber": str(page),
            "reportName": "RPT_OPERATEDEPT_TRADE_DETAILSNEW", "columns": "ALL", "source": "WEB", "client": "WEB",
            "filter": flt,
        }
        try:
            response = requests.get(_DETAIL_URL, params=params, headers=_DETAIL_HEADERS, timeout=timeout)
            response.raise_for_status()
            result = (response.json() or {}).get("result") or {}
        except Exception as exc:  # noqa: BLE001
            return _parse_trade_rows(raw, window_dates), f"{type(exc).__name__}: {str(exc)[:80]}"
        rows = result.get("data") or []
        if not rows:
            break
        raw.extend(rows)
        if page >= int(result.get("pages") or 1):
            break
    return _parse_trade_rows(raw, window_dates), ""


def _parse_trade_rows(rows: List[Dict[str, Any]], window_dates: set) -> Dict[str, Dict[str, Dict[str, float]]]:
    """东财原始行 → ``{date: {stock: {name, buy, sell}}}``（单位亿元）。

    同一只票同一天可能因为**多个上榜原因**被重复列出：
    - 同一个原因下同一席位只出现一次，但不同原因（如「日振幅 15%」「日换手 20%」）会把**同一笔**再列一遍，
      数值完全相同的行必须去重，否则金额翻倍；
    - 「连续三个交易日」的三日榜与单日榜是**不同区间**的成交（不是重复），一律优先取单日榜，
      因为页面按天看、和多数行情软件的口径一致；只有三日榜时用它。
    """
    buckets: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for record in rows:
        day = str(record.get("TRADE_DATE") or "")[:10]
        if day not in window_dates:
            continue
        stock = str(record.get("SECURITY_CODE") or "").strip()
        if not stock:
            continue
        kind = "multi" if MULTI_DAY_MARK in str(record.get("EXPLANATION") or "") else "short"
        buy = _yi(_to_float(record.get("ACT_BUY"))) or 0.0
        sell = _yi(_to_float(record.get("ACT_SELL"))) or 0.0
        slot = buckets.setdefault((day, stock), {"name": str(record.get("SECURITY_NAME_ABBR") or "").strip(),
                                                 "chg": _to_float(record.get("CHANGE_RATE")),
                                                 "short": None, "multi": None})
        if slot.get("chg") is None:
            slot["chg"] = _to_float(record.get("CHANGE_RATE"))
        current = slot[kind]
        # 同一原因下出现多行：数值相同的直接去重；万一不同（上游重复但数值有差异），保留绝对值大的那笔。
        if current is None or abs(buy - sell) > abs(current["buy"] - current["sell"]):
            slot[kind] = {"buy": buy, "sell": sell}

    out: Dict[str, Dict[str, Dict[str, float]]] = {}
    for (day, stock), slot in buckets.items():
        chosen = slot["short"] or slot["multi"]
        if chosen is None:
            continue
        out.setdefault(day, {})[stock] = {"name": slot["name"], "buy": chosen["buy"], "sell": chosen["sell"],
                                          "chg": slot.get("chg")}
    return out


def _daily_market(days: List[str], *, timeout: float) -> Tuple[Dict[str, Dict[str, float]], str]:
    """每天「龙虎榜整体」的结构：上榜只数、涨/跌只数占比、各自拿到的买入额占比、涨停（≥9.5%）占比。

    口径是**全龙虎榜**（含机构专用与沪深股通），因为东财的按日汇总只到股票层、拆不出席位类型；
    页面上要在表头写明，别和「游资口径」的活跃席位/买卖额混着读。
    """
    out: Dict[str, Dict[str, float]] = {}
    notes: List[str] = []

    def _load(day: str) -> None:
        def _fetch():
            import akshare as ak

            return ak.stock_lhb_detail_em(start_date=day.replace("-", ""), end_date=day.replace("-", ""))

        df, err = _call_with_timeout(_fetch, timeout=timeout, label=f"龙虎榜结构 {day}")
        if df is None or getattr(df, "empty", True):
            notes.append(f"{day}: {err or '空数据'}")
            return
        records = df.to_dict("records")
        buy_all = sum(_to_float(r.get("龙虎榜买入额")) or 0.0 for r in records)
        up = [r for r in records if (_to_float(r.get("涨跌幅")) or 0.0) > 0]
        down = [r for r in records if (_to_float(r.get("涨跌幅")) or 0.0) < 0]
        hot = [r for r in records if (_to_float(r.get("涨跌幅")) or 0.0) >= 9.5]
        if not records or buy_all <= 0:
            return
        out[day] = {
            "listed": len(records),
            "up_share": len(up) / len(records),
            "up_buy_share": sum(_to_float(r.get("龙虎榜买入额")) or 0.0 for r in up) / buy_all,
            "down_share": len(down) / len(records),
            "down_buy_share": sum(_to_float(r.get("龙虎榜买入额")) or 0.0 for r in down) / buy_all,
            "limit_share": len(hot) / len(records),
        }

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="industry-hot-money-day") as pool:
        list(pool.map(_load, days))
    return out, "；".join(notes[:2])


def _day_row(day: str, seat: Dict[str, float], market: Dict[str, float]) -> Dict[str, Any]:
    buy, sell = _yi(seat.get("buy")), _yi(seat.get("sell"))
    net = round((buy or 0.0) - (sell or 0.0), 4)
    return {
        "date": day,
        "seats": int(seat.get("seats") or 0),
        "buy": buy, "sell": sell, "net": net,
        "net_ratio": round(net / buy, 4) if buy else None,
        "listed": int(market.get("listed") or 0),
        "up_share": round(market["up_share"], 4) if "up_share" in market else None,
        "up_buy_share": round(market["up_buy_share"], 4) if "up_buy_share" in market else None,
        "down_share": round(market["down_share"], 4) if "down_share" in market else None,
        "down_buy_share": round(market["down_buy_share"], 4) if "down_buy_share" in market else None,
        "limit_share": round(market["limit_share"], 4) if "limit_share" in market else None,
    }


def _verdict(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """把逐日数据压成两三句话：参与度、净买率、资金落在涨票还是跌票。

    只描述**已经发生**的变化，不做预测。样本不足（少于 4 天）时如实说数据不够。
    """
    usable = [r for r in rows if r.get("buy")]
    if len(usable) < 4:
        return {"text": "样本不足 4 个交易日，先不下结论。", "tags": []}
    recent, prior = usable[:3], usable[3:8]
    if not prior:
        return {"text": "只有最近几天的数据，先不下结论。", "tags": []}

    def avg(part, key):
        vals = [r[key] for r in part if r.get(key) is not None]
        return sum(vals) / len(vals) if vals else None

    seats_now, seats_before = avg(recent, "seats"), avg(prior, "seats")
    net_now, net_before = avg(recent, "net_ratio"), avg(prior, "net_ratio")
    down_now, down_before = avg(recent, "down_buy_share"), avg(prior, "down_buy_share")
    limit_now, limit_before = avg(recent, "limit_share"), avg(prior, "limit_share")

    tags: List[str] = []
    parts: List[str] = []
    if seats_now is not None and seats_before:
        drop = seats_now / seats_before - 1
        if drop <= -0.15:
            tags.append("参与度下降")
            parts.append(f"活跃席位从 {seats_before:.0f} 降到 {seats_now:.0f}（{drop:+.0%}），愿意出手的人少了")
        elif drop >= 0.15:
            tags.append("参与度上升")
            parts.append(f"活跃席位从 {seats_before:.0f} 升到 {seats_now:.0f}（{drop:+.0%}），出手的人在变多")
        else:
            parts.append(f"活跃席位基本持平（{seats_before:.0f} → {seats_now:.0f}）")
    if net_now is not None and net_before is not None:
        if net_now < net_before - 0.03:
            tags.append("进攻减弱")
            parts.append(f"净买占买入从 {net_before:.0%} 降到 {net_now:.0%}，买卖接近对半、不再重仓押方向")
        elif net_now > net_before + 0.03:
            tags.append("进攻增强")
            parts.append(f"净买占买入从 {net_before:.0%} 升到 {net_now:.0%}，敢往一个方向压")
    if down_now is not None and down_before is not None:
        if down_now > down_before + 0.08:
            tags.append("转为接跌")
            parts.append(f"落在下跌股上的买入额从 {down_before:.0%} 升到 {down_now:.0%}，从追涨转成接跌")
        elif down_now < down_before - 0.08:
            tags.append("转为追涨")
            parts.append(f"落在下跌股上的买入额从 {down_before:.0%} 降到 {down_now:.0%}，资金回到上涨股")
    if limit_now is not None and limit_before is not None and abs(limit_now - limit_before) >= 0.1:
        parts.append(f"上榜股票里涨停/大涨的占比从 {limit_before:.0%} 变成 {limit_now:.0%}")
    if not parts:
        parts.append("各项指标和前几天差不多，没有明显转向")
    return {"text": "；".join(parts) + "。", "tags": tags}


def build_hot_money(days: int = 10, seats: int = SEAT_LIMIT_DEFAULT, *, timeout: float = 45.0) -> Dict[str, Any]:
    """游资席位 × 股票的买卖矩阵（最近 ``days`` 个交易日）。"""
    window = _dates(days)
    columns, col_note, columns_all, seat_days = _seat_columns(days, seats, timeout=timeout)
    if not columns:
        return {"days": window, "seats": [], "all_seats": [], "dates": [], "degraded": True,
                "degraded_reason": col_note or "没有活跃营业部数据"}

    window_set = set(window)
    trades: Dict[str, Dict[str, Dict[str, Dict[str, float]]]] = {}
    failures: List[str] = []

    def _load(column: Dict[str, Any]) -> None:
        merged: Dict[str, Dict[str, Dict[str, float]]] = {}
        notes: List[str] = []
        for code in column["codes"]:
            cells, note = _seat_trades(code, window_set, timeout=timeout)
            if note:
                notes.append(note)
            for day_key, stock_map in cells.items():
                bucket = merged.setdefault(day_key, {})
                for stock, cell in stock_map.items():
                    target = bucket.setdefault(stock, {"name": cell["name"], "buy": 0.0, "sell": 0.0,
                                                        "chg": cell.get("chg")})
                    target["buy"] = round(target["buy"] + cell["buy"], 4)
                    target["sell"] = round(target["sell"] + cell["sell"], 4)
                    if target.get("chg") is None:
                        target["chg"] = cell.get("chg")
        trades[column["code"]] = merged
        if notes:
            failures.append(f"{column['short']}: {notes[0]}")

    market_days: Dict[str, Dict[str, float]] = {}
    market_note = ""
    with ThreadPoolExecutor(max_workers=6, thread_name_prefix="industry-hot-money") as pool:
        market_future = pool.submit(_daily_market, list(reversed(window)), timeout=timeout)
        list(pool.map(_load, columns))
        market_days, market_note = market_future.result()

    per_day: List[Dict[str, Any]] = []
    for day in reversed(window):                    # 最近的在最前面
        rows: Dict[str, Dict[str, Any]] = {}
        for column in columns:
            for code, cell in (trades.get(column["code"]) or {}).get(day, {}).items():
                row = rows.setdefault(code, {"code": code, "name": cell.get("name") or "", "net": 0.0,
                                             "buy": 0.0, "sell": 0.0, "change_pct": cell.get("chg"), "cells": {}})
                if row.get("change_pct") is None:
                    row["change_pct"] = cell.get("chg")
                net = round(cell["buy"] - cell["sell"], 4)
                row["buy"] = round(row["buy"] + cell["buy"], 4)
                row["sell"] = round(row["sell"] + cell["sell"], 4)
                row["net"] = round(row["net"] + net, 4)
                row["cells"][column["code"]] = {"buy": cell["buy"], "sell": cell["sell"], "net": net}
        ordered = sorted(rows.values(), key=lambda r: abs(r["net"]), reverse=True)
        per_day.append({"date": day, "rows": ordered[:ROW_LIMIT], "total": len(ordered)})

    all_groups = sorted(columns_all, key=lambda g: (g["kind"], -g["buy"]))[:ALL_SEATS_LIMIT] \
        if columns_all else []
    daily = [_day_row(day, seat_days.get(day) or {}, market_days.get(day) or {}) for day in reversed(window)]
    return {
        "days": window,
        "daily": daily,
        "verdict": _verdict(daily),
        "market_note": market_note,
        "seats": columns,
        "all_seats": all_groups,
        "dates": per_day,
        "row_limit": ROW_LIMIT,
        "degraded": bool(failures),
        "degraded_reason": "；".join(failures[:3]),
        "coverage": "龙虎榜披露的上榜个股；通道（沪股通 / 深股通）与机构专用席位已剔除",
        "alias_note": "列头是人工整理的游资昵称（同一昵称下的多个营业部合并成一列），可能滞后；悬停看营业部全名与核对依据",
    }


def seat_day_detail(codes: List[str], day: str, *, timeout: float = 20.0) -> Dict[str, Any]:
    """某个席位（或一个昵称下的多个席位）某天的逐股买卖，给「游资席位」区的悬停气泡用。"""
    merged: Dict[str, Dict[str, Any]] = {}
    notes: List[str] = []
    for code in [c for c in codes if c]:
        cells, note = _seat_trades(code, {day}, timeout=timeout)
        if note:
            notes.append(note)
        for stock, cell in (cells.get(day) or {}).items():
            row = merged.setdefault(stock, {"code": stock, "name": cell.get("name") or "", "buy": 0.0, "sell": 0.0})
            row["buy"] = round(row["buy"] + (cell.get("buy") or 0.0), 4)
            row["sell"] = round(row["sell"] + (cell.get("sell") or 0.0), 4)
            if row.get("change_pct") is None:
                row["change_pct"] = cell.get("chg")
    stocks = sorted(({**row, "net": round(row["buy"] - row["sell"], 4)} for row in merged.values()),
                    key=lambda r: r["net"], reverse=True)
    return {"date": day, "stocks": stocks, "degraded": bool(notes), "degraded_reason": "；".join(notes[:2])}


def hot_money(days: int = 10, seats: int = SEAT_LIMIT_DEFAULT, *, timeout: float = 45.0) -> Dict[str, Any]:
    days = max(2, min(int(days), 30))
    seats = max(3, min(int(seats), MAX_SEATS))
    return _cached(f"hot-money:{days}:{seats}", lambda: build_hot_money(days, seats, timeout=timeout))


__all__ = ["EXCLUDED_SEATS", "build_hot_money", "hot_money", "seat_day_detail"]
