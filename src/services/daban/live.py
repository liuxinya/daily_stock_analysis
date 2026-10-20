# -*- coding: utf-8 -*-
"""盘中首板雷达：开盘后用实时数据判断「现在这个板，按两派打法能不能打」。

设计前提来自两处证据：

1. 回测（``scripts/daban_backtest.py``，一年 18263 次触板）显示，**封板那一刻能知道的信息**里，
   同题材同伴数、这只票的题材记忆都没有统计上的区分度；真正有区分度的是**前一交易日收盘时的周期**
   ——主跌第二天之后打首板 +1.95%，主跌第一天之后约 0 且前后半段都差。所以盘中只按「前一交易日的周期」
   放行，不按盘中现象临时提高等级。
2. 盈亏几乎全由「最后封不封得住」决定（封住 +2.65%，炸板 -4.40%），而买入那一刻无从知道。
   所以这里**不打分、不承诺胜率**，只给行动级别和可核对的事实。

关键口径（都是为了让盘中看到的、收盘留档的、回测统计的是同一件事）：

- **门控锚定涨停池所属交易日的前一交易日**，不是「库里最近一份复盘」。库里没有那一天的复盘时现算。
- **炸板的首板也是标的**：盘中雷达给过的票后来炸了，用户已经按打板价买了，必须留在名单与成绩单里
  （否则成绩单只剩封住的，变成幸存者偏差）。
- **仓位先到先得**：按首次触板时间分配，每派最多「分四个票」（科比 #21），且不超过该派当前阶段的
  仓位上限；用满之后的只给观察。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.services.daban.constants import (
    ACTION_ACTIONABLE,
    ACTION_LABELS,
    ACTION_TRIAL,
    ACTION_WATCH,
    MODE_FIRST_BOARD,
    STAGE_LABELS,
    STYLE_LABELS,
    load_rules,
)
from src.services.daban.kline import limit_pct
from src.services.daban.principles import filter_for_style
from src.services.daban.themes import build_themes, split_reason

logger = logging.getLogger(__name__)

STYLE_KOBE = "kobe"
STYLE_CHEN = "chen"
STYLES = (STYLE_CHEN, STYLE_KOBE)
ACTION_ORDER = {ACTION_ACTIONABLE: 0, ACTION_TRIAL: 1, ACTION_WATCH: 2}

# 盘中快照的进程内缓存：页面每 10 秒轮询，不必每轮都打满上游。
_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_MEMORY_CACHE: Dict[str, Tuple[float, Dict[str, set]]] = {}
# 前一交易日上下文：键为前一交易日；值为 (写入时间, 上下文)。现算结果 10 分钟内有效，期间若生成了复盘会自动切换。
_PREV_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_REVIEW_CACHE: Dict[str, float] = {}
_NOTIFIED: Dict[str, set] = {}
_ALLOCATION_LOCK = threading.RLock()

# 每派、每个阶段的理由与出处。原文没有说过的不挂出处。
KOBE_PHASE_REASONS = {
    "low_shock": ("前一交易日处于低位震荡：这是题材试错期，低位试错要有勇气，只做绝对低位的首板", ["K39", "K33"]),
    "main_rise": ("前一交易日处于主升段：绝对低位做首板", ["K33"]),
    "high_shock": ("前一交易日处于高位震荡：轻仓应对，只做绝对低位的首板", ["K08", "K33"]),
    "fall_2": ("前一交易日是主跌第二天：第二天的杀跌如果还在持续，尾盘就可以找买点，第三天肯定会有情绪的修复", ["K09"]),
    "fall_3": ("主跌已持续三天以上：主跌之后回到低位试错期，试错要有勇气", ["K39", "K09"]),
}
CHEN_PHASE_REASONS = {
    "ice": ("前一交易日收盘为冰点期：不抄底，只观察新题材萌芽", ["C16"]),
    "warm": ("前一交易日收盘为回暖期：聚焦新题材首板/二板，轻仓试错", ["C15", "C24"]),
    "ferment": ("前一交易日收盘为发酵期：围绕龙头或换手龙介入，首板只观察", ["C17"]),
    "climax": ("前一交易日收盘为高潮期：坚定锁仓龙头，首板只观察", ["C23"]),
}
LIVE_SELL_RULES = {
    STYLE_KOBE: [
        ("超短做隔日：今天打的板是为了赚明天的钱，明天超预期才多拿一天", ["K36"]),
        ("第二天不涨停基本都走", ["K21"]),
        ("走势与预期不一致，杀伐果断坚决止损", ["K17"]),
    ],
    STYLE_CHEN: [
        ("触及 5% 止损线立刻离场，不补仓不幻想", ["C08"]),
    ],
}


def _fmt_time(value: str) -> str:
    text = str(value or "")
    return f"{text[:2]}:{text[2:4]}:{text[4:6]}" if len(text) >= 6 else "—"


def _seal_ratio(seal: Optional[float], circ: Optional[float]) -> Optional[float]:
    return (seal / circ) if (seal and circ) else None


def limit_price_of(row: Dict[str, Any], quote: Optional[Dict[str, Any]] = None) -> Optional[float]:
    """打板价（涨停价）。优先用盘口的涨停价；没有时由现价与涨跌幅倒推昨收再算。"""
    if quote and quote.get("limit_up"):
        return float(quote["limit_up"])
    price, change = row.get("price"), row.get("change_pct")
    if price is None:
        return None
    if change is None or change <= -100:
        return float(price)
    prev_close = float(price) / (1 + float(change) / 100)
    return round(prev_close * (1 + limit_pct(row.get("code") or "") / 100) + 1e-9, 2)


def _related(a: Iterable[str], b: Iterable[str]) -> bool:
    return any(x in y or y in x for x in a for y in b if len(x) >= 2 and len(y) >= 2)


def _theme_for(tags: set, themes: List[Dict[str, Any]], stock_themes: Dict[str, List[str]], code: str):
    """股票所属题材：涨停股按当日聚类；炸板股没有涨停原因，按历史涨停原因落到当日题材里。"""
    keys = stock_themes.get(code) or []
    by_key = {t["key"]: t for t in themes}
    if keys and keys[0] in by_key:
        return by_key[keys[0]]
    for theme in themes:
        if tags and _related(tags, [theme["key"], *theme.get("tags", [])]):
            return theme
    return None


def _peers(code: str, first: str, own: set, touches: List[Dict[str, Any]], tags_of: Dict[str, set]) -> List[str]:
    """同题材里，**在它首次触板之前**已经触板（含后来炸板）的股票。只看之前，没有前视。"""
    if not own or not first:
        return []
    out = []
    for other in touches:
        if other["code"] == code or not other.get("first_time") or other["first_time"] >= first:
            continue
        if _related(own, tags_of.get(other["code"]) or set()):
            out.append(other["code"])
    return out


def _gate(rules: Dict[str, Any], style: str, phase_key: str) -> Optional[str]:
    return ((rules.get("live_gates") or {}).get(style) or {}).get(phase_key)


def _cap(rules: Dict[str, Any], style: str, phase_key: str) -> int:
    return int(((rules.get("live_caps") or {}).get(style) or {}).get(phase_key, 0) or 0)


def _position(style: str, action: str, rules: Dict[str, Any]) -> int:
    """首板永远不是龙头：陈小群线按「跟风不超过 10%」，科比线按「分四个票」。"""
    table = (rules.get("position") or {}).get(style) or {}
    if style == STYLE_CHEN:
        key = "follower" if action == ACTION_ACTIONABLE else "trial_follower"
        return int(table.get(key, 10 if action == ACTION_ACTIONABLE else 5))
    return int(table.get("actionable" if action == ACTION_ACTIONABLE else "trial", 25 if action == ACTION_ACTIONABLE else 10))


def filter_reasons(items: List[Dict[str, Any]], style: str) -> List[Dict[str, Any]]:
    out = []
    for item in items:
        ids = filter_for_style(item.get("principle_ids") or [], style)
        if ids:
            out.append({"text": item["text"], "principle_ids": ids})
    return out


def _blocked_reason(style: str, phase: Dict[str, Any]) -> str:
    if style == STYLE_KOBE:
        return f"科比线：前一交易日处于{phase.get('kobe_label') or '—'}，此阶段不打首板（第一天杀跌是很恐怖的）"
    return f"陈小群线：前一交易日收盘为{phase.get('chen_label') or '—'}，此阶段不做首板"


# ---------------------------------------------------------------------- 核心：今天的首板，两派能不能打


def _touches(rows, broken, yesterday_codes) -> List[Dict[str, Any]]:
    """当天的首板触板：当前封着的 1 板 + 触板后炸开的票（昨天已涨停的是二板尝试，排除）。"""
    sealed_codes = {r["code"] for r in rows}
    out = []
    for row in rows:
        if int(row.get("boards") or 1) == 1:
            out.append({**row, "status": "sealed"})
    for row in broken:
        if row["code"] in sealed_codes or row["code"] in yesterday_codes or not row.get("first_time"):
            continue
        out.append({**row, "status": "opened", "boards": 1})
    out.sort(key=lambda r: str(r.get("first_time") or "999999"))
    return out


def build_hits(
    *,
    rows: List[Dict[str, Any]],
    broken: List[Dict[str, Any]],
    phase: Dict[str, Any],
    rules: Dict[str, Any],
    quotes: Optional[Dict[str, Dict[str, Any]]] = None,
    memory: Optional[Dict[str, set]] = None,
    recent_mains: Optional[List[str]] = None,
    yesterday_codes: Optional[Iterable[str]] = None,
    max_picks: int = 4,
    locked: Optional[Dict[str, Dict[str, Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """给定「当前涨停池 / 炸板池」与「前一交易日的周期」，算出两派各自能做什么。

    盘中快照与收盘留档都调这个函数，保证两边口径一致。``locked`` 是当天已经给出的名额
    （``{style: {code: 留档行}}``），它们原样保留，只把剩下的名额分给新出现的票。
    """
    quotes = quotes or {}
    memory = memory or {}
    yesterday = set(yesterday_codes or [])
    theme_result = build_themes(
        zt=rows, blocks=[], history=[], previous=None, yesterday_rows=[],
        recent_mains=list(recent_mains or []), hot_rank={}, rules=rules,
    )
    themes = theme_result["themes"]
    stock_themes = theme_result["stock_themes"]
    touches = _touches(rows, broken, yesterday)
    tags_of: Dict[str, set] = {}
    for row in [*rows, *broken]:
        tags_of[row["code"]] = set(split_reason(row.get("reason") or "")) or memory.get(row["code"]) or set()

    min_peers = int(rules.get("live_chen_min_peers", 2))
    open_time = str(rules.get("live_open_time", "093100"))
    late = str(rules.get("weak_late_time", "143000"))
    kobe_key = phase.get("kobe_gate") or ""
    chen_key = phase.get("chen_stage") or ""

    hits: List[Dict[str, Any]] = []
    blocked: List[Dict[str, Any]] = []
    for row in touches:
        code = row["code"]
        first_time = str(row.get("first_time") or "")
        own = tags_of.get(code) or set()
        theme = _theme_for(own, themes, stock_themes, code)
        peer_codes = _peers(code, first_time, own, touches, tags_of)
        quote = quotes.get(code) or {}
        if row["status"] == "opened":
            sealed: Optional[bool] = False
        elif quote:
            sealed = bool(quote.get("price") and quote.get("limit_up") and quote["price"] >= quote["limit_up"] - 1e-6
                          and int(quote.get("sell1_volume") or 0) == 0)
        else:
            sealed = None

        # 降为观察：都用「买入那一刻能看到的信息」（首次触板时间、板型），不用尾盘才知道的末封时间。
        notes: List[str] = []
        cap_watch = False
        if first_time and first_time <= open_time:
            notes.append("开盘一分钟内就触板：多为一字或秒板，打板买不到（回测 -0.72%），只观察")
            cap_watch = True
        if "一字" in str(row.get("limit_up_type") or ""):
            notes.append("一字板：盘中没有买入机会，只观察")
            cap_watch = True
        if first_time and first_time >= late:
            notes.append("尾盘才触板：科比把尾盘偷鸡板算弱板（#33），只观察")
            cap_watch = True
        if row["status"] == "opened":
            notes.append("已炸板：若已按打板价买入，按本派卖出纪律处理")
        elif int(row.get("open_num") or 0) >= 3:
            notes.append(f"已开板 {row['open_num']} 次，封板反复，注意封单是否还在")

        per_style: Dict[str, Tuple[Optional[str], List[Dict[str, Any]], List[str]]] = {}

        kobe_action = _gate(rules, STYLE_KOBE, kobe_key)
        text, ids = KOBE_PHASE_REASONS.get(kobe_key, ("", []))
        per_style[STYLE_KOBE] = (kobe_action, [{"text": text, "principle_ids": ids}] if text else [], [])

        chen_action = _gate(rules, STYLE_CHEN, chen_key)
        text, ids = CHEN_PHASE_REASONS.get(chen_key, ("", []))
        chen_reasons = [{"text": text, "principle_ids": ids}] if text else []
        chen_notes: List[str] = []
        if chen_action in (ACTION_ACTIONABLE, ACTION_TRIAL):
            if not theme or theme.get("novelty") != "new":
                chen_notes.append("陈小群此阶段只聚焦新题材，这只不属于新题材，只观察")
                chen_action = ACTION_WATCH
            if len(peer_codes) >= min_peers:
                chen_reasons.append({"text": f"资金合力：同题材在它之前已有 {len(peer_codes)} 只触板",
                                     "principle_ids": ["C14"]})
            else:
                chen_notes.append(f"同题材此前只有 {len(peer_codes)} 只触板，还没形成资金合力（要求 ≥{min_peers}），只观察")
                chen_action = ACTION_WATCH
        per_style[STYLE_CHEN] = (chen_action, chen_reasons, chen_notes)

        for style in STYLES:
            action, reasons, extra_notes = per_style[style]
            if action is None:
                blocked.append({"code": code, "name": row.get("name") or "", "style": style,
                                "theme": theme["name"] if theme else "", "reason": _blocked_reason(style, phase)})
                continue
            if cap_watch:
                action = ACTION_WATCH
            hits.append({
                "code": code,
                "name": row.get("name") or "",
                "style": style,
                "style_label": STYLE_LABELS[style],
                "action": action,
                "action_label": ACTION_LABELS[action],
                "position_pct": 0,
                "status": row["status"],
                "first_time": _fmt_time(first_time),
                "first_time_raw": first_time,
                "last_time": _fmt_time(str(row.get("last_time") or first_time)),
                "open_num": int(row.get("open_num") or 0),
                "sealed": sealed,
                "limit_price": limit_price_of(row, quote),
                "price": quote.get("price") or row.get("price"),
                "change_pct": row.get("change_pct"),
                "turnover_rate": row.get("turnover_rate"),
                "circ_mv": row.get("circ_mv"),
                "seal_amount": row.get("seal_amount") if row["status"] == "sealed" else None,
                "seal_ratio": _seal_ratio(row.get("seal_amount"), row.get("circ_mv")) if row["status"] == "sealed" else None,
                "reason": row.get("reason") or "",
                "high_days": row.get("high_days") or "",
                "limit_up_type": row.get("limit_up_type") or "",
                "theme": theme["name"] if theme else "",
                "theme_rank": theme["rank"] if theme else None,
                "theme_main": bool(theme and theme.get("is_main")),
                "novelty": theme["novelty_label"] if theme else "",
                "peers": len(peer_codes),
                "peer_codes": peer_codes[:6],
                "reasons": filter_reasons(reasons, style),
                "notes": [*notes, *extra_notes],
                "phase": phase.get("kobe_label") if style == STYLE_KOBE else phase.get("chen_label"),
                "mode": MODE_FIRST_BOARD,
                "slot": None,
                "locked_at": None,
            })

    lanes = _allocate(hits, phase=phase, rules=rules, max_picks=max_picks, locked=locked or {})
    hits.sort(key=lambda h: (h["style"], ACTION_ORDER[h["action"]], h["first_time_raw"]))
    return {
        "hits": hits,
        "blocked": blocked[:60],
        "themes": themes[:8],
        "lanes": lanes,
        "stats": {
            "limit_up": len(rows),
            "first_boards": sum(1 for t in touches if t["status"] == "sealed"),
            "first_touches": len(touches),
            "broken": len(broken),
            "actionable": sum(1 for h in hits if h["action"] == ACTION_ACTIONABLE),
            "trial": sum(1 for h in hits if h["action"] == ACTION_TRIAL),
        },
    }


def _hit_from_record(record: Dict[str, Any], style: str, phase: Dict[str, Any]) -> Dict[str, Any]:
    """已锁定、但这一轮在涨停池 / 炸板池里都看不到的票（上游缺数据）：照留档原样显示，名额不动。"""
    first = str(record.get("first_time") or "")
    return {
        "code": record["code"], "name": record.get("name") or "", "style": style, "style_label": STYLE_LABELS[style],
        "action": record.get("action") or ACTION_TRIAL, "action_label": ACTION_LABELS.get(record.get("action"), ""),
        "position_pct": int(record.get("position_pct") or 0), "status": "missing",
        "first_time": _fmt_time(first), "first_time_raw": first, "last_time": "—", "open_num": int(record.get("open_count") or 0),
        "sealed": None, "limit_price": record.get("limit_price"), "price": None, "change_pct": None,
        "turnover_rate": None, "circ_mv": None, "seal_amount": None, "seal_ratio": None, "reason": "",
        "high_days": "", "limit_up_type": "", "theme": record.get("theme") or "", "theme_rank": None,
        "theme_main": False, "novelty": "", "peers": 0, "peer_codes": [], "reasons": [],
        "notes": ["这一轮在涨停池和炸板池里都没看到这只（上游缺数据），名额照旧锁定"],
        "phase": record.get("phase") or (phase.get("kobe_label") if style == STYLE_KOBE else phase.get("chen_label")),
        "mode": MODE_FIRST_BOARD, "slot": record.get("slot"), "locked_at": None,
    }


def _allocate(
    hits: List[Dict[str, Any]],
    *,
    phase: Dict[str, Any],
    rules: Dict[str, Any],
    max_picks: int,
    locked: Dict[str, Dict[str, Dict[str, Any]]],
) -> Dict[str, Any]:
    """按首次触板时间先到先得：每派最多 ``max_picks`` 只（科比「分四个票」），且不超过阶段仓位上限。

    **名额一旦给出当天就锁定**：A 股 T+1，上午用掉的仓位当天腾不出来；成绩单也必须记当时真实给出的票。
    所以已锁定的票原样保留动作、仓位、名额序号（哪怕后来炸板、或题材聚类变化让它此刻不再符合条件），
    只把剩余名额与剩余仓位分给新出现的票。数据晚到、但首次触板更早的票不会把已锁定的挤掉，只进观察。
    炸板的票照样占名额——那一刻已经按打板价买了。
    """
    lanes: Dict[str, Any] = {}
    for style in STYLES:
        key = (phase.get("kobe_gate") if style == STYLE_KOBE else phase.get("chen_stage")) or ""
        cap = _cap(rules, style, key)
        mine_locked = dict(locked.get(style) or {})
        present = {h["code"] for h in hits if h["style"] == style}
        for code, record in mine_locked.items():
            if code not in present:
                hits.append(_hit_from_record(record, style, phase))
        used = sum(int(r.get("position_pct") or 0) for r in mine_locked.values())
        taken = sorted(int(r["slot"]) for r in mine_locked.values() if r.get("slot"))
        count = len(mine_locked)
        last_locked = max((str(r.get("first_time") or "") for r in mine_locked.values()), default="")
        next_slot = (max(taken) if taken else count) + 1

        mine = sorted((h for h in hits if h["style"] == style), key=lambda h: h["first_time_raw"])
        for hit in mine:
            record = mine_locked.get(hit["code"])
            if record is not None:
                hit["action"] = record.get("action") or hit["action"]
                hit["action_label"] = ACTION_LABELS.get(hit["action"], hit["action_label"])
                hit["position_pct"] = int(record.get("position_pct") or 0)
                hit["slot"] = record.get("slot")
                hit["locked_at"] = _fmt_time(str(record.get("seen_at") or record.get("first_time") or ""))
                continue
            if hit["action"] == ACTION_WATCH:
                continue
            want = _position(style, hit["action"], rules)
            room = cap - used
            late = bool(last_locked) and hit["first_time_raw"] < last_locked
            if count >= max_picks or room <= 0:
                reason = (f"今天「分 {max_picks} 个票」的名额已锁定用满" if count >= max_picks
                          else f"本派当前阶段仓位上限 {cap}% 已锁定用满")
                if late:
                    reason += "；它首次触板更早，但数据晚到，已锁定的名额不会让出"
                hit["notes"].append(reason + "，只观察")
                hit["action"] = ACTION_WATCH
                hit["action_label"] = ACTION_LABELS[ACTION_WATCH]
                continue
            hit["position_pct"] = min(want, room)
            used += hit["position_pct"]
            count += 1
            hit["slot"] = next_slot
            next_slot += 1
        lanes[style] = {
            "phase_key": key,
            "cap": cap,
            "used": used,
            "picks": count,
            "locked": len(mine_locked),
            "max_picks": max_picks,
            "sell_rules": [{"text": text, "principle_ids": ids} for text, ids in LIVE_SELL_RULES[style]],
        }
    return lanes


def locked_from_records(records: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """留档行 → ``{style: {code: 行}}``。早期没有名额序号的行按首次触板时间补一个，保证序号连续。"""
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for record in sorted(records, key=lambda r: (str(r.get("first_time") or ""), r.get("code") or "")):
        if record.get("action") not in (ACTION_ACTIONABLE, ACTION_TRIAL):
            continue
        style_map = out.setdefault(record["style"], {})
        row = dict(record)
        if not row.get("slot"):
            row["slot"] = max([int(r.get("slot") or 0) for r in style_map.values()] + [0]) + 1
        style_map[row["code"]] = row
    return out


# ---------------------------------------------------------------------- 时间


def _beijing_now():
    from datetime import datetime, timedelta, timezone

    return datetime.now(timezone(timedelta(hours=8)))


def _now_hhmmss() -> str:
    return _beijing_now().strftime("%H%M%S")


def _today() -> str:
    return _beijing_now().strftime("%Y-%m-%d")


def is_trading_day(day: str) -> bool:
    from datetime import datetime

    from src.core.trading_calendar import is_market_open

    value = datetime.strptime(day, "%Y-%m-%d").date()
    return value.weekday() < 5 and is_market_open("cn", value)


def market_open_now(now_hhmmss: Optional[str] = None, *, day: Optional[str] = None) -> bool:
    """A 股交易时段：交易日的 09:15（集合竞价开始）到 15:00。"""
    t = now_hhmmss or _now_hhmmss()
    if not ("091500" <= t <= "150000"):
        return False
    try:
        return is_trading_day(day or _today())
    except Exception:
        return True


# ---------------------------------------------------------------------- 服务


class LiveRadarService:
    """盘中快照：每次调用打 5 个上游（涨停池、天梯、炸板池、封板事件流、腾讯快照），缓存数秒。"""

    def __init__(self, repository=None, config=None):
        from src.config import get_config

        self.config = config or get_config()
        self._repository = repository
        self.rules = load_rules(self.config)

    @property
    def repository(self):
        if self._repository is None:
            from src.repositories.daban_repo import DabanRepository

            self._repository = DabanRepository()
        return self._repository

    @property
    def max_picks(self) -> int:
        return int(getattr(self.config, "daban_max_actionable", 4) or 4)

    # ------------------------------------------------------------------ 前一交易日上下文

    def previous_context(self, pool_date: str) -> Dict[str, Any]:
        """``pool_date`` 的**前一交易日**收盘时的周期，是盘中门控唯一允许用的周期信息。

        优先读库里那一天的复盘（完整复盘或冷启动回补都行，两者的周期算法相同）；没有时用最近 3 个交易日的
        涨停 / 炸板 / 跌停池现算。现算结果缓存 10 分钟，期间生成了复盘会自动切换过去。
        """
        from src.services.daban.fetchers import previous_trade_date

        prev = previous_trade_date(pool_date)
        cached = _PREV_CACHE.get(prev)
        if cached and (cached[1].get("source") == "review" or time.time() - cached[0] < 600):
            return cached[1]
        context = self._context_from_review(prev, pool_date) or self._compute_previous_context(prev)
        if context.get("available"):
            _PREV_CACHE[prev] = (time.time(), context)
        return context

    def _context_from_review(self, prev: str, pool_date: str) -> Optional[Dict[str, Any]]:
        from src.repositories.daban_repo import decode_payload

        try:
            payload = decode_payload(self.repository.snapshot_for_trade_date(prev))
        except Exception as exc:
            logger.warning("打板王雷达：读取 %s 的复盘失败 %s", prev, exc)
            return None
        if not payload or not (payload.get("stage") or {}).get("key"):
            return None
        stage = payload.get("stage") or {}
        kobe = payload.get("kobe_stage") or {}
        recent: List[str] = []
        try:
            for row in self.repository.history(limit=10, before=pool_date):
                for name in str(row.main_themes or "").split("、"):
                    if name.strip() and name.strip() not in recent:
                        recent.append(name.strip())
            yesterday = {r["code"] for r in self.repository.pool_rows(prev, "zt")}
        except Exception as exc:
            logger.warning("打板王雷达：读取近期主线 / 昨日涨停失败 %s", exc)
            yesterday = set()
        return {
            "available": True,
            "source": "review",
            "trade_date": prev,
            "chen_stage": stage.get("key"),
            "chen_label": stage.get("label") or STAGE_LABELS.get(stage.get("key") or "", ""),
            "kobe_gate": kobe.get("gate_key"),
            "kobe_label": kobe.get("label"),
            "recent_mains": recent,
            "yesterday_codes": sorted(yesterday),
        }

    def _compute_previous_context(self, prev: str) -> Dict[str, Any]:
        """从 ``prev`` 及其之前两个交易日的涨停 / 炸板 / 跌停池现算 ``prev`` 的周期（不调模型，约 5 秒）。

        需要 3 天：判定要「较昨日」的变化，科比四阶段还要数连续杀跌天数。
        """
        from src.services.daban import cycle as cycle_mod
        from src.services.daban.fetchers import (
            fetch_em, fetch_ladder, fetch_limit_pool, previous_trade_date, run_parallel,
        )

        timeout = float(getattr(self.config, "daban_fetch_timeout_sec", 15) or 15)
        try:
            days = [prev]
            for _ in range(2):
                days.append(previous_trade_date(days[-1]))
            days.reverse()  # 时间正序：prev-2, prev-1, prev

            def _load(day: str) -> Dict[str, Dict[str, Any]]:
                tasks = {
                    "zt": lambda: fetch_limit_pool("zt", day, timeout=timeout, include_bse=False),
                    "zb": lambda: fetch_limit_pool("zb", day, timeout=timeout, include_bse=False),
                    "dt": lambda: fetch_limit_pool("dt", day, timeout=timeout, include_bse=False),
                    "ladder": lambda: fetch_ladder(day, timeout=timeout),
                    "previous": lambda: fetch_em("previous", day, timeout=timeout, include_bse=False),
                }
                return {key: (env or {}) for key, (env, _) in run_parallel(tasks, timeout=timeout, workers=5).items()}

            loaded = {day: _load(day) for day in days}

            def _rows(day: str, key: str) -> List[Dict[str, Any]]:
                env = loaded[day].get(key) or {}
                return ((env.get("value") or {}).get("rows") or []) if env.get("ok") else []

            if not _rows(prev, "zt"):
                raise RuntimeError(f"{prev} 没有拿到涨停数据（可能不是交易日或上游不可用）")

            recent: List[str] = []
            metrics: Dict[str, Dict[str, Any]] = {}
            for day in days:
                zt = [dict(row) for row in _rows(day, "zt")]
                ladder = ((loaded[day].get("ladder") or {}).get("value") or {}).get("ladder") or {}
                for row in zt:
                    row["boards"] = int(ladder.get(row["code"], 1))
                prev_env = loaded[day].get("previous") or {}
                previous = _rows(day, "previous") if prev_env.get("ok") else None
                metrics[day] = cycle_mod.compute_metrics(
                    zt=zt, zb=_rows(day, "zb"), dt=_rows(day, "dt"), previous=previous, rules=self.rules,
                )
                day_themes = build_themes(zt=zt, blocks=[], history=[], previous=None, yesterday_rows=[],
                                          recent_mains=[], hot_rank={}, rules=self.rules)["themes"]
                metrics[day]["follow_rate"] = (
                    round(day_themes[0]["zt_count"] / len(zt), 4) if day_themes and zt else None
                )
                for theme in day_themes:
                    if theme.get("is_main") and theme["name"] not in recent:
                        recent.append(theme["name"])

            history: List[Dict[str, Any]] = []
            prev_stage: Optional[str] = None
            stage: Dict[str, Any] = {}
            for index, day in enumerate(days):
                cycle_mod.attach_deltas(metrics[day], metrics[days[index - 1]] if index else None)
                stage = cycle_mod.classify_stage(metrics[day], prev_stage=prev_stage, rules=self.rules)
                history.append({"stage": stage["key"], "yzt_premium": metrics[day].get("yzt_premium")})
                prev_stage = stage["key"]
            kobe = cycle_mod.kobe_phase(stage["key"], metrics[prev], history[:-1])
            return {
                "available": True,
                "source": "computed",
                "trade_date": prev,
                "chen_stage": stage["key"],
                "chen_label": stage["label"],
                "kobe_gate": kobe.get("gate_key"),
                "kobe_label": kobe.get("label"),
                "recent_mains": recent,
                "yesterday_codes": sorted(r["code"] for r in _rows(prev, "zt")),
            }
        except Exception as exc:
            logger.warning("打板王雷达：现算 %s 的周期失败 %s", prev, exc)
            return {
                "available": False,
                "trade_date": prev,
                "reason": f"无法判定 {prev} 的情绪周期（{type(exc).__name__}）；可先生成一次盘后复盘",
            }

    def memory(self, days: int = 90, *, until: Optional[str] = None) -> Dict[str, set]:
        """``until``（不含）之前 ``days`` 天的涨停原因记忆。炸板股 / 同伴当下没有涨停原因，只能靠历史。

        ``until`` 默认今天：当天的涨停原因已经在涨停池里，不需要记忆，也不能让历史回看用到未来。按 2 分钟缓存。
        """
        from datetime import datetime, timedelta

        until = until or _today()
        key = f"memory:{until}"
        cached = _MEMORY_CACHE.get(key)
        if cached and (time.time() - cached[0]) < 120:
            return cached[1]
        since = (datetime.strptime(until, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
        out: Dict[str, set] = {}
        try:
            for code, reason in self.repository.reason_memory(since, "zt", until=until):
                tags = set(split_reason(reason or ""))
                if tags:
                    out.setdefault(code, set()).update(tags)
        except Exception as exc:
            logger.warning("打板王雷达：读取涨停原因记忆失败 %s", exc)
        _MEMORY_CACHE[key] = (time.time(), out)
        return out

    # ------------------------------------------------------------------ 实时快照

    def snapshot(self, *, force: bool = False) -> Dict[str, Any]:
        ttl = float(getattr(self.config, "daban_live_cache_ttl_sec", 5) or 0)
        cached = _CACHE.get("live")
        if cached and not force and (time.time() - cached[0]) < ttl:
            return cached[1]

        from data_provider import limit_up_fetcher as lu

        started = time.time()
        timeout = float(getattr(self.config, "daban_fetch_timeout_sec", 15) or 15)
        today, now = _today(), _now_hhmmss()
        trading = market_open_now(now, day=today)
        payload: Dict[str, Any] = {
            "generated_at": now,
            "trade_date": today,
            "trading": trading,
            "replay": False,
            "stale": False,
            "poll_sec": int(getattr(self.config, "daban_live_poll_sec", 10) or 10),
            "degraded_reasons": [],
        }

        try:
            pool = lu.fetch_ths_pool("zt", None, timeout=timeout)
        except Exception as exc:
            payload.update({"ok": False, "error": f"涨停池不可用：{type(exc).__name__}: {str(exc)[:160]}"})
            _CACHE["live"] = (time.time(), payload)
            return payload
        pool_date = pool["date"] or today
        payload["trade_date"] = pool_date
        # 上游给的不是今天：交易日盘前 / 非交易日是正常的（显示上一交易日的盘中结果）；交易时段内则是数据滞后。
        payload["replay"] = pool_date != today
        payload["stale"] = trading and now >= "093000" and pool_date != today
        if payload["stale"]:
            payload["degraded_reasons"].append(f"交易时段内涨停池仍是 {pool_date} 的数据，上游可能延迟")

        rows = [r for r in pool["rows"] if lu.in_universe(r["code"], r.get("name")) and not r.get("is_new")]
        ladder: Optional[Dict[str, int]] = None
        try:
            ladder = lu.fetch_ths_ladder(None, timeout=timeout)
        except Exception as exc:
            payload["degraded_reasons"].append(f"连板天梯不可用，连板数按「N 天 N 板」推断：{str(exc)[:60]}")
        for row in rows:
            if ladder is not None:
                row["boards"] = int(ladder.get(row["code"], 1))
            else:
                days_, boards_ = int(row.get("hd_days") or 0), int(row.get("hd_boards") or 0)
                row["boards"] = boards_ if (boards_ >= 2 and days_ == boards_) else 1

        try:
            broken = [r for r in lu.fetch_ths_pool("zb", None, timeout=timeout)["rows"]
                      if lu.in_universe(r["code"], r.get("name"))]
        except Exception as exc:
            broken = []
            payload["degraded_reasons"].append(f"炸板池不可用（炸板的首板不会出现在名单里）：{str(exc)[:60]}")

        try:
            events = [e for e in lu.fetch_ths_changes(timeout=timeout)
                      if e["date"] in (pool_date, "") and lu.in_universe(e["code"], e.get("name"))]
        except Exception as exc:
            events = []
            payload["degraded_reasons"].append(f"封板事件流不可用：{str(exc)[:60]}")

        try:
            quotes = lu.fetch_tencent_quotes([r["code"] for r in [*rows, *broken]], timeout=timeout)
        except Exception as exc:
            quotes = {}
            payload["degraded_reasons"].append(f"盘口快照不可用（无法确认是否真封住）：{str(exc)[:60]}")

        phase = self.previous_context(pool_date)
        payload["previous"] = {k: v for k, v in phase.items() if k not in ("yesterday_codes",)}
        payload["events"] = _summarize_events(events, now if not payload["replay"] else "150000")
        if not phase.get("available"):
            payload.update({
                "ok": True, "hits": [], "blocked": [], "themes": [], "lanes": {},
                "stats": {"limit_up": len(rows), "first_boards": 0, "first_touches": 0, "broken": len(broken),
                          "actionable": 0, "trial": 0},
                "note": phase.get("reason"),
            })
            payload["duration_ms"] = int((time.time() - started) * 1000)
            _CACHE["live"] = (time.time(), payload)
            return payload

        recording = trading and not payload["replay"]
        # 读已锁定名额 → 分配剩余名额 → 留档，必须是一个原子步骤：页面轮询和后台任务可能同时跑，
        # 否则两边各自把同一个空名额分给不同的票。
        with _ALLOCATION_LOCK:
            locked = self.locked(pool_date)
            built = build_hits(
                rows=rows, broken=broken, phase=phase, rules=self.rules, quotes=quotes,
                memory=self.memory(until=pool_date), recent_mains=phase.get("recent_mains"),
                yesterday_codes=phase.get("yesterday_codes"), max_picks=self.max_picks, locked=locked,
            )
            fresh = self._record(pool_date, built["hits"]) if recording else []
        payload.update({
            "ok": True,
            "hits": built["hits"],
            "blocked": built["blocked"],
            "themes": built["themes"],
            "lanes": built["lanes"],
            "stats": built["stats"],
            "gate_labels": {
                "kobe": phase.get("kobe_label"),
                "chen": phase.get("chen_label"),
                "kobe_action": _gate(self.rules, STYLE_KOBE, phase.get("kobe_gate") or ""),
                "chen_action": _gate(self.rules, STYLE_CHEN, phase.get("chen_stage") or ""),
                "source": phase.get("source"),
                "trade_date": phase.get("trade_date"),
            },
        })
        if fresh:
            self._notify(pool_date, fresh, phase)
        self._maybe_review(today, now)
        payload["duration_ms"] = int((time.time() - started) * 1000)
        _CACHE["live"] = (time.time(), payload)
        return payload

    # ------------------------------------------------------------------ 留档 / 推送 / 回填

    def locked(self, trade_date: str) -> Dict[str, Dict[str, Dict[str, Any]]]:
        """当天已经给出的名额（盘中留档 + 收盘补录）。读不到时按空处理，不阻断快照。"""
        try:
            return locked_from_records(self.repository.live_hits(trade_date))
        except Exception as exc:
            logger.warning("打板王雷达：读取已锁定名额失败 %s", exc)
            return {}

    def _record(self, trade_date: str, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """名额第一次给出的那一刻就留档（之后不改），这才是用户真正看到并可能下单的东西。"""
        calls = [h for h in hits if h["action"] in (ACTION_ACTIONABLE, ACTION_TRIAL) and not h.get("locked_at")]
        if not calls:
            return []
        try:
            inserted = self.repository.upsert_live_hits(
                trade_date, [live_hit_record(h, origin="live", seen_at=_now_hhmmss()) for h in calls], final=False,
            )
        except Exception as exc:
            logger.warning("打板王雷达：盘中留档失败 %s", exc)
            return []
        keys = {(row["style"], row["code"]) for row in inserted}
        return [h for h in calls if (h["style"], h["code"]) in keys]

    def _notify(self, trade_date: str, hits: List[Dict[str, Any]], phase: Dict[str, Any]) -> None:
        if not bool(getattr(self.config, "daban_live_push_enabled", False)):
            return
        sent = _NOTIFIED.setdefault(trade_date, set())
        todo = [h for h in hits if (h["style"], h["code"]) not in sent]
        if not todo:
            return
        try:
            from src.notification import NotificationBuilder, NotificationService

            notifier = NotificationService()
            for hit in todo:
                sent.add((hit["style"], hit["code"]))
                body = render_hit_alert(hit)
                content = NotificationBuilder.build_simple_alert(
                    title=f"打板王雷达 · {hit['style_label']} {hit['action_label']}：{hit['code']} {hit['name']}",
                    content=body,
                    alert_type="info",
                )
                notifier.send_with_results(
                    content, route_type="alert", dedup_key=f"daban-live:{trade_date}:{hit['style']}:{hit['code']}",
                )
        except Exception as exc:
            logger.warning("打板王雷达：推送失败 %s: %s", type(exc).__name__, exc)

    def _maybe_review(self, today: str, now: str) -> None:
        """回填已经到了「次日收盘」的雷达标的。10 分钟最多做一次，不拖慢快照。"""
        last = _REVIEW_CACHE.get("review")
        if last and time.time() - last < 600:
            return
        _REVIEW_CACHE["review"] = time.time()
        try:
            review_pending_live_hits(self.repository, today=today, now=now)
        except Exception as exc:
            logger.warning("打板王雷达：回填失败 %s", exc)


def live_hit_record(hit: Dict[str, Any], *, origin: str, seen_at: Optional[str] = None) -> Dict[str, Any]:
    """雷达标的 → ``daban_live_hits`` 一行。买入价按打板价（涨停价）记，与回测口径一致。"""
    return {
        "style": hit["style"],
        "code": hit["code"],
        "name": hit.get("name") or "",
        "action": hit["action"],
        "first_time": hit.get("first_time_raw") or str(hit.get("first_time") or "").replace(":", ""),
        "seen_at": seen_at or hit.get("first_time_raw") or "",
        "limit_price": hit.get("limit_price"),
        "position_pct": hit.get("position_pct"),
        "slot": hit.get("slot"),
        "sealed_close": None,
        "open_count": hit.get("open_num"),
        "theme": (hit.get("theme") or "")[:64],
        "phase": hit.get("phase"),
        "origin": origin,
    }


def render_hit_alert(hit: Dict[str, Any]) -> str:
    lines = [
        f"**{hit['style_label']}｜{hit['action_label']}｜建议仓位 ≤{hit['position_pct']}%**",
        f"首次触板 {hit['first_time']}，打板价 {hit.get('limit_price') or '—'}",
    ]
    facts = []
    if hit.get("theme"):
        facts.append(f"题材 {hit['theme']}")
    facts.append(f"同题材先封 {hit.get('peers', 0)} 只")
    if hit.get("seal_ratio") is not None:
        facts.append(f"封单比 {hit['seal_ratio']:.2%}")
    if hit.get("reason"):
        facts.append(f"涨停原因 {hit['reason']}")
    lines.append("，".join(facts))
    for item in hit.get("reasons") or []:
        lines.append(f"- {item['text']}（{'、'.join(item['principle_ids'])}）")
    for note in hit.get("notes") or []:
        lines.append(f"- 注意：{note}")
    for text, ids in LIVE_SELL_RULES.get(hit["style"], []):
        lines.append(f"- 卖出纪律：{text}（{'、'.join(ids)}）")
    lines.append("> 雷达只做提醒，不构成投资建议。")
    return "\n".join(lines)


def review_pending_live_hits(repository, *, today: str, now: str) -> int:
    """把「次日已经收盘」的雷达标的回填次日开盘 / 收盘收益（相对打板价）。返回回填的交易日数。"""
    from src.services.daban.fetchers import fetch_klines, next_trade_date

    done = 0
    for trade_date in repository.pending_live_dates():
        target = next_trade_date(trade_date)
        if target > today or (target == today and now < "150500"):
            continue
        pending = [h for h in repository.live_hits(trade_date) if not h.get("reviewed") and h.get("limit_price")]
        codes = sorted({h["code"] for h in pending})
        if not codes:
            continue
        env = fetch_klines(codes, target, limit=5, timeout=25)
        rows_by_code = (env.get("value") or {}).get("rows") or {}
        outcomes: Dict[str, Dict[str, Any]] = {}
        for hit in pending:
            rows = rows_by_code.get(hit["code"]) or []
            bar = next((r for r in rows if str(r.get("time") or "").replace("-", "") == target.replace("-", "")), None)
            if not bar:
                continue
            buy = float(hit["limit_price"])
            outcomes[hit["code"]] = {
                "open_ret": round((bar["open"] / buy - 1) * 100, 2),
                "close_ret": round((bar["close"] / buy - 1) * 100, 2),
            }
        if outcomes:
            repository.update_live_outcomes(trade_date, outcomes)
            done += 1
    return done


def _summarize_events(events: List[Dict[str, Any]], now: str) -> Dict[str, Any]:
    """最近 30 分钟内的封板 / 回封 / 炸板事件，给页面做「刚刚发生」的滚动。"""
    def _minutes(hhmmss: str) -> int:
        try:
            return int(hhmmss[:2]) * 60 + int(hhmmss[2:4])
        except (TypeError, ValueError):
            return 0

    limit = _minutes(now) - 30
    fresh = sorted((e for e in events if _minutes(e["time"]) >= limit), key=lambda e: e["time"], reverse=True)
    return {
        "recent": fresh[:20],
        "counts": {
            "first": sum(1 for e in events if e["type"] == "FIRST_LIMIT"),
            "back": sum(1 for e in events if e["type"] == "LIMIT_BACK"),
            "failed": sum(1 for e in events if e["type"] == "LIMIT_FAILED"),
        },
    }


LIVE_TASK_NAME = "daban_king_live"
LIVE_TASK_SECONDS = 30  # Scheduler 主循环 30 秒一轮，后台不可能更快；更快的刷新靠页面轮询。


def build_live_background_tasks(config, *, config_provider) -> List[Dict[str, Any]]:
    """交易时段后台轮询（不开页面也留档、推送）。需要 ``SCHEDULE_ENABLED`` 与 ``DABAN_LIVE_PUSH_ENABLED``。"""
    if not bool(getattr(config, "daban_enabled", True)) or not bool(getattr(config, "daban_live_enabled", True)):
        return []
    if not bool(getattr(config, "daban_live_push_enabled", False)):
        return []

    def live_task() -> None:
        if not market_open_now():
            return
        try:
            LiveRadarService(config=config_provider()).snapshot(force=True)
        except Exception as exc:
            logger.warning("[打板王雷达] 后台轮询失败 %s", exc)

    return [{"task": live_task, "interval_seconds": LIVE_TASK_SECONDS, "run_immediately": False, "name": LIVE_TASK_NAME}]


def clear_cache() -> None:
    _CACHE.clear()
    _MEMORY_CACHE.clear()
    _PREV_CACHE.clear()
    _REVIEW_CACHE.clear()
    _NOTIFIED.clear()


__all__ = [
    "LIVE_TASK_NAME", "LIVE_TASK_SECONDS", "LiveRadarService", "build_hits", "build_live_background_tasks",
    "clear_cache", "filter_reasons", "is_trading_day", "limit_price_of", "live_hit_record", "locked_from_records",
    "market_open_now",
    "render_hit_alert", "review_pending_live_hits",
]
