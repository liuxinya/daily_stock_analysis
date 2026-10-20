# -*- coding: utf-8 -*-
"""主线题材识别（陈小群「主线识别」+ 科比「题材预判」）。

归因靠同花顺涨停池的 ``reason_type``（「PCB+AI服务器+HDI板」），按 ``+`` 切成标签。
标签里混着两类东西：题材（人形机器人、飞行汽车）和公司自身的催化（业绩预增、大额订单），
后者对「主线」没有意义，用 :data:`_GENERIC_RE` 剔掉。剩下的标签按「互相包含」合并成题材
（「人形机器人」「机器人结构件」并入「机器人」），再用同花顺题材榜校准板块涨幅与持续天数。

同义但字面不包含的合并（「抖音小店」与「网红直播」）留给模型，规则层不硬猜。
"""

from __future__ import annotations

import re
from statistics import mean
from typing import Any, Dict, Iterable, List, Optional, Tuple

_GENERIC_RE = re.compile(
    r"业绩|季报|年报|中报|预增|扭亏|增长|增持|回购|股息|分红|澄清|订单|中标|定点|获奖|出口|销售|客户|"
    r"签约|合同|募投|投资|扩产|产能|报告|公告|股权|控股|参股|子公司|收购|复牌|摘帽"
)
# 太宽、合并后会把不相干题材捏在一起的短词，不作为合并根。
_TOO_BROAD = {
    "汽车", "AI", "电子", "材料", "科技", "智能", "数据", "服务", "制造", "设备", "新能源", "能源", "国企", "央企",
    "零部件", "结构件", "元器件", "新材料", "系统", "平台", "解决方案", "产业园", "一体化",
}
_SUFFIX_RE = re.compile(r"(概念股活跃|概念股|概念|板块|产业链|产业)$")
_QUOTE_RE = re.compile(r"[“”\"'‘’《》]")

STATUS_LABELS = {"start": "启动", "rise": "主升", "diverge": "高位分歧", "fade": "退潮"}
NOVELTY_LABELS = {"same": "老题材", "related": "共性发散", "new": "新题材"}
STORY_LABELS = {"big": "大", "medium": "中", "small": "小"}


def normalize_tag(tag: str) -> str:
    text = _QUOTE_RE.sub("", str(tag or "")).strip()
    text = _SUFFIX_RE.sub("", text).strip()
    return text


def split_reason(reason: str) -> List[str]:
    """涨停原因 → 题材标签（已去掉公司催化类标签）。"""
    tags = []
    for part in str(reason or "").split("+"):
        tag = normalize_tag(part)
        if len(tag) < 2 or _GENERIC_RE.search(tag):
            continue
        if tag not in tags:
            tags.append(tag)
    return tags


def _common_suffix(a: str, b: str) -> str:
    n = 0
    while n < min(len(a), len(b)) and a[-1 - n] == b[-1 - n]:
        n += 1
    return a[len(a) - n:] if n else ""


def _cores(tags: Iterable[str]) -> set:
    """两两标签的公共后缀（≥3 字）当作「核心词」：人形机器人 / 工业机器人 → 机器人。

    中文概念名大多是「修饰 + 中心词」，中心词在后，所以只取后缀；太宽的词（零部件、材料）不要。
    """
    items = sorted(set(tags))
    cores = set()
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            core = _common_suffix(a, b)
            if len(core) >= 3 and core not in _TOO_BROAD and not _GENERIC_RE.search(core):
                cores.add(core)
    return cores


def _merge_tags(tag_codes: Dict[str, set]) -> Dict[str, Tuple[List[str], set]]:
    """按字面包含合并标签。返回 ``{题材名: (标签列表, 股票代码集合)}``，题材名取组里最短的标签。

    先补上公共核心词（见 :func:`_cores`）作为虚拟标签，「人形机器人」「工业机器人」「机器人结构件」
    才会通过「都包含机器人」合到一起。
    """
    tag_codes = dict(tag_codes)
    for core in _cores(tag_codes):
        tag_codes.setdefault(core, set())
    tags = sorted(tag_codes, key=lambda t: (len(t), t))
    parent = {tag: tag for tag in tags}

    def find(tag: str) -> str:
        while parent[tag] != tag:
            parent[tag] = parent[parent[tag]]
            tag = parent[tag]
        return tag

    for i, short in enumerate(tags):
        if short in _TOO_BROAD:
            continue
        for longer in tags[i + 1:]:
            if short in longer:
                root_short, root_long = find(short), find(longer)
                if root_short != root_long:
                    # 让更短的标签当根，题材名更概括。
                    if (len(root_short), root_short) <= (len(root_long), root_long):
                        parent[root_long] = root_short
                    else:
                        parent[root_short] = root_long

    groups: Dict[str, Tuple[List[str], set]] = {}
    for tag in tags:
        root = find(tag)
        members, codes = groups.setdefault(root, ([], set()))
        members.append(tag)
        codes.update(tag_codes[tag])
    # 只由虚拟核心词构成、没有任何股票的组没有意义。
    return {root: group for root, group in groups.items() if group[1]}


def _theme_match(key: str, tags: Iterable[str], text: str) -> bool:
    """某段涨停原因 / 板块名是否属于这个题材。"""
    normalized = normalize_tag(text)
    if not normalized:
        return False
    if key in normalized or normalized in key:
        return len(min(key, normalized, key=len)) >= 2
    return any(tag in normalized for tag in tags)


def _match_block(key: str, tags: List[str], blocks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    best = None
    for block in blocks or []:
        name = normalize_tag(block.get("name") or "")
        if not name:
            continue
        if name == key or name in tags:
            return block
        if best is None and (key in name or name in key) and len(min(key, name, key=len)) >= 2:
            best = block
    return best


def _consecutive_days(key: str, tags: List[str], history: List[Tuple[str, List[Dict[str, Any]]]]) -> int:
    """从昨天往前数，题材每天至少 2 只涨停的连续天数（不含今天）。``history`` 按日期正序。"""
    days = 0
    for _, rows in reversed(history):
        hits = sum(1 for row in rows if _theme_match(key, tags, row.get("reason") or ""))
        if hits >= 2:
            days += 1
        else:
            break
    return days


def leader_sort_key(row: Dict[str, Any], hot_rank: Dict[str, int]):
    """龙头裁决：板数高 → N 天 M 板里 M 多（辨识度）→ 今日首封早 → 人气榜靠前。"""
    return (
        -int(row.get("boards") or 1),
        -int(row.get("hd_boards") or 0),
        str(row.get("first_time") or "999999"),
        hot_rank.get(row.get("code"), 10_000),
    )


def build_themes(
    *,
    zt: List[Dict[str, Any]],
    blocks: List[Dict[str, Any]],
    history: List[Tuple[str, List[Dict[str, Any]]]],
    previous: Optional[List[Dict[str, Any]]],
    yesterday_rows: List[Dict[str, Any]],
    recent_mains: List[str],
    hot_rank: Dict[str, int],
    rules: Dict[str, Any],
) -> Dict[str, Any]:
    """返回 ``{themes: [...], stock_themes: {code: [key, ...]}, follow_rate}``。

    - ``history``：此前交易日的涨停池行（含 reason），按日期正序，用于算持续天数。
    - ``previous`` / ``yesterday_rows``：昨日涨停股今日表现 / 昨日涨停池（带涨停原因），
      两者按代码关联出「题材内昨日涨停溢价」。
    - ``recent_mains``：近 10 个交易日的主线题材名，用来判断新 / 老题材。
    """
    by_code = {row["code"]: row for row in zt}
    tag_codes: Dict[str, set] = {}
    for row in zt:
        for tag in split_reason(row.get("reason") or ""):
            tag_codes.setdefault(tag, set()).add(row["code"])
    groups = _merge_tags(tag_codes)

    y_reason = {row["code"]: row.get("reason") or "" for row in yesterday_rows or []}
    y_change = {row["code"]: row.get("change_pct") for row in previous or []}

    themes: List[Dict[str, Any]] = []
    for key, (tags, codes) in groups.items():
        if len(codes) < 2:
            continue
        rows = sorted((by_code[c] for c in codes if c in by_code), key=lambda r: leader_sort_key(r, hot_rank))
        lianban = sum(1 for r in rows if int(r.get("boards") or 1) >= 2)
        max_boards = int(rows[0].get("boards") or 1) if rows else 0
        block = _match_block(key, tags, blocks)
        prior_days = _consecutive_days(key, tags, history)
        days = int(block.get("days") or 0) if block else 0
        days = max(days, prior_days + 1)

        y_members = [code for code, reason in y_reason.items() if _theme_match(key, tags, reason)]
        y_changes = [float(y_change[c]) for c in y_members if y_change.get(c) is not None]
        premium = round(mean(y_changes), 2) if y_changes else None
        yesterday_count = len(y_members) if yesterday_rows else None

        themes.append({
            "key": key,
            "name": key,
            "tags": tags[:8],
            "zt_count": len(rows),
            "lianban_count": lianban,
            "max_boards": max_boards,
            "score": len(rows) + 2 * lianban + max_boards,
            "leader": {"code": rows[0]["code"], "name": rows[0]["name"], "boards": max_boards} if rows else None,
            "members": [
                {
                    "code": r["code"],
                    "name": r["name"],
                    "boards": int(r.get("boards") or 1),
                    "first_time": r.get("first_time") or "",
                    "limit_up_type": r.get("limit_up_type") or "",
                }
                for r in rows
            ],
            "days": days,
            "yesterday_count": yesterday_count,
            "premium": premium,
            "block": (
                {k: block.get(k) for k in ("code", "name", "change_pct", "limit_up_num", "days")} if block else None
            ),
        })

    themes.sort(key=lambda t: (-t["score"], -t["zt_count"], t["key"]))
    min_zt = int(rules.get("main_theme_min_zt", 3))
    for index, theme in enumerate(themes, start=1):
        theme["rank"] = index
        theme["is_main"] = index <= 3 and theme["zt_count"] >= min_zt
        theme["status"] = _status(theme)
        theme["status_label"] = STATUS_LABELS[theme["status"]]
        theme["novelty"] = _novelty(theme["key"], theme["tags"], recent_mains)
        theme["novelty_label"] = NOVELTY_LABELS[theme["novelty"]]
        theme.update({"story_space": None, "story_label": "", "story_reason": "", "story_source": "",
                      "news": [], "news_count": None})

    stock_themes: Dict[str, List[str]] = {}
    for theme in themes:
        for member in theme["members"]:
            stock_themes.setdefault(member["code"], []).append(theme["key"])

    top = themes[0] if themes else None
    follow_rate = round(top["zt_count"] / len(zt), 4) if (top and zt) else None
    return {"themes": themes, "stock_themes": stock_themes, "follow_rate": follow_rate}


def _status(theme: Dict[str, Any]) -> str:
    premium = theme.get("premium")
    y_count = theme.get("yesterday_count")
    shrinking = y_count is not None and theme["zt_count"] < y_count
    if theme["days"] <= 2:
        return "start"
    if premium is not None and premium < -2 and shrinking:
        return "fade"
    if shrinking or (premium is not None and premium < 0):
        return "diverge"
    return "rise"


def _novelty(key: str, tags: List[str], recent_mains: List[str]) -> str:
    """与近 10 日主线比：同名 → 老题材；字面互相包含 → 共性发散；都不沾 → 新题材。"""
    recent = [normalize_tag(name) for name in recent_mains or [] if name]
    if key in recent:
        return "same"
    for name in recent:
        if len(name) >= 2 and (name in key or key in name or any(name in tag or tag in name for tag in tags)):
            return "related"
    return "new"


def rule_story_space(theme: Dict[str, Any]) -> Tuple[str, str]:
    """模型不可用时的故事空间估计：只看新闻热度与涨停规模，页面标注「规则估计」。"""
    news = theme.get("news_count") or 0
    zt = theme.get("zt_count") or 0
    if news >= 10 and zt >= 6:
        return "big", f"近 3 日相关新闻 {news} 条、涨停 {zt} 只，发酵面较广"
    if news >= 3 or zt >= 4:
        return "medium", f"近 3 日相关新闻 {news} 条、涨停 {zt} 只"
    return "small", f"近 3 日相关新闻 {news} 条、涨停 {zt} 只，暂未看到持续发酵"


def apply_story(theme: Dict[str, Any], space: str, reason: str, source: str) -> None:
    if space not in STORY_LABELS:
        return
    theme["story_space"] = space
    theme["story_label"] = STORY_LABELS[space]
    theme["story_reason"] = str(reason or "")[:300]
    theme["story_source"] = source
