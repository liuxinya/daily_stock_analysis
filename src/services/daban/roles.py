# -*- coding: utf-8 -*-
"""个股角色：总龙头 / 板块龙头 / 前排 / 低位补涨 / 中位股 / 跟风。

- 中位股（科比 #12）：连板数在 3 到「全市场最高板 − 1」之间、且不是主线龙头。最高板只有
  3 板时这个区间是空的，不会误伤。
- 跟风（陈小群「只做龙头不做杂毛」）：主线里既不是前排、也不够低价小盘（补涨的前提）。
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.services.daban.constants import (
    ROLE_CATCH_UP,
    ROLE_FOLLOWER,
    ROLE_FRONT,
    ROLE_LABELS,
    ROLE_MIDDLE,
    ROLE_OTHER,
    ROLE_THEME_LEADER,
    ROLE_TOTAL_LEADER,
)
from src.services.daban.themes import leader_sort_key


def is_low_price_small_cap(row: Dict[str, Any], rules: Dict[str, Any]) -> bool:
    price, circ = row.get("price"), row.get("circ_mv")
    return (
        price is not None
        and circ is not None
        and float(price) <= float(rules.get("low_price", 20.0))
        and float(circ) <= float(rules.get("small_cap", 100e8))
    )


def assign_roles(
    *,
    zt: List[Dict[str, Any]],
    themes: List[Dict[str, Any]],
    stock_themes: Dict[str, List[str]],
    max_height: int,
    hot_rank: Dict[str, int],
    rules: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    theme_by_key = {t["key"]: t for t in themes}
    main_keys = [t["key"] for t in themes if t.get("is_main")]
    theme_leaders = {
        t["leader"]["code"]: t["key"] for t in themes if t.get("is_main") and t.get("leader")
    }

    top_rows = sorted(
        (r for r in zt if int(r.get("boards") or 1) == max_height and max_height >= 2),
        key=lambda r: leader_sort_key(r, hot_rank),
    )
    total_leader = top_rows[0]["code"] if top_rows else None

    # 每个主线题材里按首封时间排出前 3 只首板，作为「前排」。
    early_first: Dict[str, set] = {}
    for key in main_keys:
        firsts = sorted(
            (m for m in theme_by_key[key]["members"] if m["boards"] == 1),
            key=lambda m: m.get("first_time") or "999999",
        )
        early_first[key] = {m["code"] for m in firsts[:3]}

    roles: Dict[str, Dict[str, Any]] = {}
    for row in zt:
        code = row["code"]
        boards = int(row.get("boards") or 1)
        keys = stock_themes.get(code) or []
        primary = keys[0] if keys else None
        main_key = next((k for k in keys if k in main_keys), None)

        if code == total_leader:
            role = ROLE_TOTAL_LEADER
        elif code in theme_leaders and boards >= 2:
            role = ROLE_THEME_LEADER
            main_key = theme_leaders[code]
        elif 3 <= boards <= max_height - 1:
            role = ROLE_MIDDLE
        elif main_key and (boards == 2 or code in early_first.get(main_key, set())):
            role = ROLE_FRONT
        elif main_key and boards <= 2 and is_low_price_small_cap(row, rules):
            role = ROLE_CATCH_UP
        elif main_key:
            role = ROLE_FOLLOWER
        else:
            role = ROLE_OTHER

        theme_key = main_key or primary
        theme = theme_by_key.get(theme_key) if theme_key else None
        roles[code] = {
            "role": role,
            "role_label": ROLE_LABELS[role],
            "theme": theme_key,
            "theme_rank": theme["rank"] if theme else None,
            "theme_is_main": bool(theme and theme.get("is_main")),
        }
    return roles


__all__ = ["assign_roles", "is_low_price_small_cap"]
