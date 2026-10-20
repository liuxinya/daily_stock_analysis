# -*- coding: utf-8 -*-
"""「我是打板王」测试共用的数据构造器。

行结构与 ``data_provider.limit_up_fetcher`` 归一化后的结构一致（字段名、单位都相同），
数值取自 2026-10-19 / 10-20 同花顺涨停数据中心实测响应的裁剪版，避免测试连外网。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.services.daban.constants import DEFAULT_RULES, load_rules


def rules(**overrides) -> Dict[str, Any]:
    out = load_rules(None)
    out.update(overrides)
    return out


def zt_row(
    code: str,
    name: str,
    *,
    boards: int = 1,
    reason: str = "",
    price: float = 10.0,
    circ_mv: float = 50e8,
    turnover: float = 8.0,
    limit_up_type: str = "换手板",
    first_time: str = "100000",
    last_time: Optional[str] = None,
    open_num: int = 0,
    seal_amount: float = 5e7,
    suc_rate: Optional[float] = 0.8,
    hd_days: Optional[int] = None,
    hd_boards: Optional[int] = None,
    change_pct: float = 10.0,
) -> Dict[str, Any]:
    hd_d = hd_days if hd_days is not None else boards
    hd_b = hd_boards if hd_boards is not None else boards
    return {
        "code": code,
        "name": name,
        "price": price,
        "change_pct": change_pct,
        "turnover_rate": turnover,
        "circ_mv": circ_mv,
        "amount": turnover / 100 * circ_mv,
        "boards": boards,
        "hd_days": hd_d,
        "hd_boards": hd_b,
        "high_days": "首板" if hd_b == 1 else f"{hd_d}天{hd_b}板",
        "limit_up_type": limit_up_type,
        "open_num": open_num,
        "change_tag": "LIMIT_BACK" if open_num else "FIRST_LIMIT",
        "first_time": first_time,
        "last_time": last_time or first_time,
        "seal_amount": seal_amount,
        "suc_rate": suc_rate,
        "reason": reason,
        "industry": "",
        "is_new": False,
        "is_again_limit": bool(open_num),
        "source": "ths",
    }


def prev_row(code: str, name: str, *, y_boards: int = 1, change_pct: float = 1.0) -> Dict[str, Any]:
    return {
        "code": code,
        "name": name,
        "price": 10.0,
        "change_pct": change_pct,
        "turnover_rate": 10.0,
        "circ_mv": 40e8,
        "amount": 4e8,
        "boards": 0,
        "y_boards": y_boards,
        "reason": "",
        "source": "em",
    }


def plain_row(code: str, name: str, *, change_pct: float) -> Dict[str, Any]:
    return {"code": code, "name": name, "change_pct": change_pct, "price": 10.0, "circ_mv": 40e8}


def metrics(**values) -> Dict[str, Any]:
    """一组「什么都没命中」的周期指标，按需覆盖。"""
    base = {
        "zt_count": 0,
        "zb_count": 0,
        "dt_count": 0,
        "lianban_count": 0,
        "max_height": 0,
        "yzt_premium": None,
        "promotion_rate": None,
        "big_loss_count": None,
        "follow_rate": None,
        "leader_seal_ratio": None,
        "tiers": [],
        "delta": {},
    }
    base.update(values)
    return base


def robot_day() -> List[Dict[str, Any]]:
    """一个「机器人」主线发酵的交易日：1 只 4 板总龙头、2 只 3 板中位、若干首板 / 2 板。"""
    return [
        zt_row("003029", "吉大正元", boards=4, reason="人形机器人+商用密码", turnover=24.0, price=24.0,
               circ_mv=42e8, first_time="092500"),
        zt_row("600178", "东安动力", boards=3, reason="飞行汽车+增程动力", price=11.3, circ_mv=52e8),
        zt_row("600860", "京城股份", boards=3, reason="飞行汽车+机器人集成", price=8.7, circ_mv=38e8),
        zt_row("603278", "大业股份", boards=2, reason="人形机器人+胎圈钢丝", price=10.9, circ_mv=37e8,
               first_time="094500"),
        zt_row("002347", "泰尔股份", boards=1, reason="工业机器人+冶金装备", price=6.6, circ_mv=33e8,
               open_num=1, first_time="093500", last_time="101000"),
        zt_row("603626", "科森科技", boards=1, reason="机器人结构件+苹果供应链", price=22.2, circ_mv=123e8,
               first_time="102000"),
        zt_row("003021", "兆威机电", boards=1, reason="人形机器人+灵巧手", price=67.2, circ_mv=140e8,
               first_time="134000"),
        zt_row("603119", "浙江荣泰", boards=1, reason="人形机器人+精密结构件", price=45.9, circ_mv=217e8,
               first_time="135000"),
        zt_row("605058", "澳弘电子", boards=1, reason="PCB+AI服务器", price=72.4, circ_mv=103e8,
               hd_days=21, hd_boards=12, open_num=4, first_time="105600", last_time="145500"),
        zt_row("300852", "四会富仕", boards=1, reason="AI服务器PCB+工业控制PCB", price=76.9, circ_mv=119e8),
    ]


__all__ = ["DEFAULT_RULES", "metrics", "plain_row", "prev_row", "robot_day", "rules", "zt_row"]
