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
    MODE_WEAK_TO_STRONG,
    STAGE_LABELS,
    STYLE_LABELS,
    load_rules,
)
from src.services.daban.kline import limit_pct
from src.services.daban.principles import filter_for_style
from src.services.daban.themes import build_themes, normalize_tag, split_reason

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
_BLOCK_CACHE: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
# 实时周期的补充取数（当日跌停池、昨日涨停今日表现）与阶段历史；值为 (写入时间, 数据)。
_CYCLE_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_HISTORY_CACHE: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
# 实时门控的当日状态：涨停峰值与连续确认轮数。键为交易日。
_RT_STATE: Dict[str, Dict[str, Any]] = {}
# 拉升异动：键为异动类型；值为 (写入时间, 事件列表)。取数失败时沿用最近一次成功结果（见 ``_changes``）。
_CHANGE_CACHE: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
_PREV_BLOCK_CACHE: Dict[str, Dict[str, int]] = {}
_NEWS_CACHE: Dict[str, Tuple[float, int]] = {}
_NEWS_INFLIGHT: set = set()
_CONCEPT_CACHE: Dict[str, Dict[str, Any]] = {}
_REVIEW_CACHE: Dict[str, float] = {}
_NOTIFIED: Dict[str, set] = {}
_ALLOCATION_LOCK = threading.RLock()

# 每派、每个阶段的理由与出处。原文没有说过的不挂出处。
KOBE_PHASE_REASONS = {
    "low_shock": ("当前周期为低位震荡：这是题材试错期，低位试错要有勇气，只做绝对低位的首板", ["K39", "K33"]),
    "main_rise": ("当前周期为主升段：绝对低位做首板", ["K33"]),
    "high_shock": ("当前周期为高位震荡：轻仓应对，只做绝对低位的首板", ["K08", "K33"]),
    "fall_2": ("今天是主跌第二天：第二天的杀跌如果还在持续，尾盘就可以找买点，第三天肯定会有情绪的修复", ["K09"]),
    "fall_2_early": ("今天是主跌第二天，但原文的买点在尾盘：尾盘之前只试错，不给可执行", ["K09"]),
    "fall_3": ("主跌已持续三天以上：主跌之后回到低位试错期，试错要有勇气", ["K39", "K09"]),
}
CHEN_PHASE_REASONS = {
    "ice": ("当前周期为冰点期：不抄底，只观察新题材萌芽", ["C16"]),
    "warm": ("当前周期为回暖期：聚焦新题材首板/二板，轻仓试错", ["C15", "C24"]),
    "ferment": ("当前周期为发酵期：围绕龙头或换手龙介入，首板只观察", ["C17"]),
    "climax": ("当前周期为高潮期：坚定锁仓龙头，首板只观察", ["C23"]),
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
    """题材名/标签是否相关。两边都先去掉「概念 / 板块 / 产业链」这类后缀，
    「人形机器人」才能对上「机器人概念」。"""
    left = {normalize_tag(x) for x in a if x}
    right = {normalize_tag(y) for y in b if y}
    return any(x in y or y in x for x in left for y in right if len(x) >= 2 and len(y) >= 2)


def _code_blocks(blocks: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """题材榜的成员 → ``{code: [板块名, ...]}``（板块按涨停数从强到弱排列，与上游返回顺序一致）。"""
    out: Dict[str, List[str]] = {}
    for block in blocks or []:
        name = str(block.get("name") or "").strip()
        if not name:
            continue
        for member in block.get("members") or []:
            code = str(member.get("code") or "").strip()
            if code:
                names = out.setdefault(code, [])
                if name not in names:
                    names.append(name)
    return out


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


def _hot_index(hot: Optional[List[Dict[str, Any]]]) -> Dict[str, str]:
    """归一化题材名 → 今日热点题材 key。

    同一个题材在两套口径里写法不同：题材榜给「机器人概念」，涨停原因聚类出来的是「机器人」
    （``normalize_tag`` 会把「概念 / 板块 / 产业链」后缀去掉）。归一后它们是同一个 key。
    """
    index: Dict[str, str] = {}
    for item in hot or []:
        key = str(item.get("key") or "")
        if key:
            index.setdefault(normalize_tag(key), key)
    return index


def _hot_key_for(tags: Iterable[str], hot: Optional[List[Dict[str, Any]]], index: Dict[str, str]) -> str:
    """把一只票的题材标签落到今日热点题材上，返回热点题材的 key。

    先归一后精确匹配，再按「互相包含」取最热的一个（``hot`` 已按热度排序）。贴不上返回空串——
    这种票不占题材名额，页面上只在「全部题材」里出现。
    """
    names = [str(tag) for tag in tags if tag]
    for name in names:
        key = index.get(normalize_tag(name))
        if key:
            return key
    for item in hot or []:
        key = str(item.get("key") or "")
        if key and _related(names, [key]):
            return key
    return ""


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


_GATE_RANK = {None: 0, ACTION_WATCH: 1, ACTION_TRIAL: 2, ACTION_ACTIONABLE: 3}


def _advance_fall_day(gate_key: str) -> str:
    """盘前只能假设「今天延续昨天」：昨天是主跌第 N 天，今天就按第 N+1 天门控。

    科比原话「第一天杀跌是很恐怖的，但是**第二天**的杀跌如果还是持续的话，尾盘就可以找买点」说的是
    **当天**是主跌第几天；回测里「主跌第 2 天 +1.95%」也标在买入当天。所以门控表的键一律按「今日是
    主跌第几天」理解：盘前用前一交易日的天数 +1 做估计，盘中由实时重判按今天的 tape 纠正。
    """
    if not str(gate_key or "").startswith("fall_"):
        return gate_key
    try:
        day = int(str(gate_key).split("_")[1])
    except (IndexError, ValueError):
        return gate_key
    return f"fall_{min(day + 1, 3)}"


def _minus_minutes(now: str, minutes: int) -> str:
    """``HHMMSS`` 往前推 N 分钟（只用于盘中同一天的比较，不跨日）。"""
    text = str(now or "").strip()
    if len(text) < 4 or not text[:4].isdigit():
        return "000000"
    total = max(0, int(text[:2]) * 60 + int(text[2:4]) - max(0, int(minutes)))
    return f"{total // 60:02d}{total % 60:02d}00"


def _theme_accelerating(
    rows: List[Dict[str, Any]], hot: List[Dict[str, Any]], *, now: str, window_min: int, min_new: int,
) -> Optional[str]:
    """最近 ``window_min`` 分钟内有没有热点题材在加速封板。

    这是降级的豁免条件：大盘或昨日主线走弱，但资金只是换了题材（科比讲的「切换」——「但凡没有原来
    题材属性的股票和板块，都是资金愿意尝试的板块」），就不该停发名额，否则正好错过轮动那一波。
    归因用涨停原因（覆盖率接近 100%），不用题材榜成员（上游只给每个板块很少几只）。
    """
    cutoff = _minus_minutes(now, window_min)
    names = [str(item.get("name") or "") for item in (hot or []) if item.get("name")]
    if not names:
        return None
    counts: Dict[str, int] = {}
    for row in rows or []:
        first = str(row.get("first_time") or "")
        if not _valid_time(first) or first < cutoff:
            continue
        tags = split_reason(row.get("reason") or "")
        for name in names:
            if tags and _related(tags, [name]):
                counts[name] = counts.get(name, 0) + 1
    if not counts:
        return None
    name, count = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
    return name if count >= max(1, int(min_new)) else None


def _fall_label(gate_key: str) -> str:
    """``fall_2`` / ``fall_2_early`` → 「主跌第 2 天」。只用于把生效门控键说成人话。"""
    text = str(gate_key or "")
    if not text.startswith("fall_"):
        return ""
    digits = "".join(ch for ch in text if ch.isdigit())
    return f"主跌第 {digits} 天" if digits else ""


def _late_swap(rules: Dict[str, Any], gate_key: str, now: str) -> str:
    """科比「第二天的杀跌如果还是持续的话，**尾盘**就可以找买点」：主跌第 2 天的可执行只在尾盘放行，
    尾盘之前按试错对待（原文此时还在等买点，不是不看）。"""
    if gate_key != "fall_2":
        return gate_key
    late = str((rules.get("realtime_gate") or {}).get("fall2_late_time", "140000"))
    return gate_key if str(now or "") >= late else "fall_2_early"


def realtime_phase(
    *, metrics: Dict[str, Any], prev_stage: Optional[str], history: List[Dict[str, Any]], rules: Dict[str, Any],
) -> Dict[str, Any]:
    """用**今天的** tape 现算今天的周期（陈小群六阶段 + 科比四阶段），口径与盘后复盘完全一致。"""
    from src.services.daban import cycle as cycle_mod

    stage = cycle_mod.classify_stage(metrics, prev_stage=prev_stage, rules=rules)
    kobe = cycle_mod.kobe_phase(stage["key"], metrics, history or [])
    return {"stage": stage, "kobe_stage": kobe}


def _apply_upgrade(rules: Dict[str, Any], base_keys: Dict[str, str], today: Dict[str, Any],
                   now: str) -> Dict[str, Any]:
    """只往更放行的方向替换门控键：市场验证过的今天的周期，比昨天的收盘更有发言权。"""
    today_keys = {STYLE_CHEN: str((today.get("stage") or {}).get("key") or ""),
                  STYLE_KOBE: _late_swap(rules, str((today.get("kobe_stage") or {}).get("gate_key") or ""), now)}
    keys, changed = dict(base_keys), False
    for style in STYLES:
        base, fresh = base_keys[style], today_keys[style]
        if not fresh or fresh == base:
            continue
        if _GATE_RANK.get(_gate(rules, style, fresh), 0) > _GATE_RANK.get(_gate(rules, style, base), 0):
            keys[style], changed = fresh, True
    return {"keys": keys, "changed": changed}


def decide_realtime_gate(
    *,
    rules: Dict[str, Any],
    phase: Dict[str, Any],
    metrics: Dict[str, Any],
    today: Dict[str, Any],
    now: str,
    accel_theme: Optional[str],
    state: Dict[str, Any],
) -> Dict[str, Any]:
    """盘中实时门控：前一交易日的周期做底线，今天的 tape 给出市场验证后按今天的周期行事。

    - **升级**（赚钱效应恢复）：昨日涨停今日溢价转正（科比 #28「看昨日涨停股今日表现」、陈小群五大
      指标之一）、涨停数不低于昨日收盘、封板质量过关（炸板率不高）。满足后按今天的周期查门控表，
      **只在更放行的方向生效**。
    - **降级**（资金撤了）：溢价转负是必要条件，再叠加炸板率抬升 / 跌停增多 / 涨停数从当日峰值回落
      中任意一条。**但只要还有热点题材在加速封板就不降级**——那是资金在换题材，不是在撤（科比讲的
      「切换」）。降级只停发新名额，已锁定的不动：A 股 T+1 撤不回来，成绩单也必须记当时真实给出的票。
    - 两者都要连续 ``confirm_rounds`` 轮满足才生效，且当日涨停数要到 ``min_zt_sample`` 才开始判。
    """
    cfg = (rules.get("realtime_gate") or {})
    base_keys = {STYLE_CHEN: str(phase.get("chen_stage") or ""),
                 STYLE_KOBE: _late_swap(rules, _advance_fall_day(str(phase.get("kobe_gate") or "")), now)}
    out: Dict[str, Any] = {
        "enabled": bool(cfg.get("enabled", True)),
        "active": False, "upgraded": False, "frozen": False,
        "keys": dict(base_keys), "accel_theme": accel_theme, "reason": "",
        "labels": {STYLE_CHEN: phase.get("chen_label") or "",
                   STYLE_KOBE: _fall_label(base_keys[STYLE_KOBE]) or phase.get("kobe_label") or ""},
        "today_stage": (today.get("stage") or {}).get("key"),
        "today_label": (today.get("stage") or {}).get("label"),
        "today_kobe": (today.get("kobe_stage") or {}).get("gate_key"),
        "today_kobe_label": (today.get("kobe_stage") or {}).get("label"),
    }
    zt, sample = int(metrics.get("zt_count") or 0), int(cfg.get("min_zt_sample", 20))
    if not out["enabled"]:
        return out
    if zt < sample:
        out["reason"] = f"当日涨停 {zt} 只不足 {sample} 只，样本太小，仍按前一交易日的周期门控"
        return out
    out["active"] = True
    state["zt_peak"] = peak = max(int(state.get("zt_peak") or 0), zt)
    premium, zab = metrics.get("yzt_premium"), metrics.get("zab_rate")
    prev_zt = int(((phase.get("metrics") or {}).get("zt_count")) or 0)
    prev_dt = int(((phase.get("metrics") or {}).get("dt_count")) or 0)
    zab_max, rounds = float(cfg.get("zab_rate_max", 0.35)), max(1, int(cfg.get("confirm_rounds", 2)))

    up_ok = (premium is not None and premium > 0 and zt >= prev_zt and (zab is None or zab <= zab_max))
    weak_bits: List[str] = []
    if zab is not None and zab > zab_max:
        weak_bits.append(f"炸板率 {zab:.0%} 高于 {zab_max:.0%}")
    if int(metrics.get("dt_count") or 0) > prev_dt:
        weak_bits.append(f"跌停 {metrics.get('dt_count')} 只多于昨日 {prev_dt} 只")
    if peak > 0 and (peak - zt) / peak >= float(cfg.get("peak_drawdown", 0.3)):
        weak_bits.append(f"涨停数从当日峰值 {peak} 回落到 {zt}")
    down_ok = premium is not None and premium < 0 and bool(weak_bits)

    state["up_rounds"] = (state.get("up_rounds") or 0) + 1 if up_ok else 0
    state["down_rounds"] = (state.get("down_rounds") or 0) + 1 if down_ok else 0
    premium_text = f"{premium:+.2f}%" if premium is not None else "未取到"
    if state["up_rounds"] >= rounds:
        applied = _apply_upgrade(rules, base_keys, today, now)
        out["keys"], out["upgraded"] = applied["keys"], applied["changed"]
        if applied["changed"]:
            out["labels"] = {STYLE_CHEN: out["today_label"] or out["labels"][STYLE_CHEN],
                             STYLE_KOBE: out["today_kobe_label"] or out["labels"][STYLE_KOBE]}
        out["reason"] = (f"昨日涨停今日溢价 {premium_text}、当日涨停 {zt} 只（昨收 {prev_zt}）"
                         f"{'、炸板率 %.0f%%' % (zab * 100) if zab is not None else ''}："
                         f"赚钱效应已被市场验证，按今天的周期（{out['today_label'] or '—'}）门控")
    elif state["down_rounds"] >= rounds:
        if accel_theme:
            out["reason"] = (f"赚钱效应回落（溢价 {premium_text}、{weak_bits[0]}），但「{accel_theme}」"
                             f"还在加速封板——资金在换题材不是在撤，不停发名额")
        else:
            out["frozen"] = True
            out["reason"] = (f"赚钱效应回落（溢价 {premium_text}、{weak_bits[0]}）且没有题材在接力："
                             f"停发新名额，已锁定的按本派卖出纪律处理")
    else:
        out["reason"] = (f"实时重判已开启（当日涨停 {zt} 只）：还需连续 {rounds} 轮确认，"
                         f"当前按前一交易日的周期门控")
    return out


def filter_reasons(items: List[Dict[str, Any]], style: str) -> List[Dict[str, Any]]:
    out = []
    for item in items:
        ids = filter_for_style(item.get("principle_ids") or [], style)
        if ids:
            out.append({"text": item["text"], "principle_ids": ids})
    return out


NAMELESS_REASON = (
    "师出无名：没有当日涨停原因、近 90 天涨停记录，所属概念也挂不上今天在涨停的题材——"
    "两派都只做有题材逻辑的票"
)


def _blocked_reason(style: str, phase: Dict[str, Any]) -> str:
    if style == STYLE_KOBE:
        return f"科比线：当前周期判定为{phase.get('kobe_label') or '—'}，此阶段不打首板（第一天杀跌是很恐怖的）"
    return f"陈小群线：当前周期判定为{phase.get('chen_label') or '—'}，此阶段不做首板"


def _phase_reason(style: str, phase: Dict[str, Any], rules: Dict[str, Any]) -> Dict[str, Any]:
    """门控的具体原因：放行的阶段给该派原话，关闭的阶段给「此阶段不打首板」的理由。

    页面上的「今日门控」卡要能直接看出「为什么今天不出手」，不用用户去翻被挡列表里的某一只票。
    """
    key = (phase.get("kobe_gate") if style == STYLE_KOBE else phase.get("chen_stage")) or ""
    if _gate(rules, style, key) is None:
        return {"text": _blocked_reason(style, phase), "principle_ids": []}
    table = KOBE_PHASE_REASONS if style == STYLE_KOBE else CHEN_PHASE_REASONS
    text, ids = table.get(key, ("", []))
    return {"text": text, "principle_ids": list(ids)}


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
    blocks: Optional[List[Dict[str, Any]]] = None,
    preboard: Optional[List[Dict[str, Any]]] = None,
    prev_blocks: Optional[Dict[str, int]] = None,
    news_lookup: Optional[Any] = None,
    concepts_lookup: Optional[Any] = None,
    yesterday_weak: Optional[set] = None,
    freeze: bool = False,
) -> Dict[str, Any]:
    """给定「当前涨停池 / 炸板池」与「前一交易日的周期」，算出两派各自能做什么。

    盘中快照与收盘留档都调这个函数，保证两边口径一致。``locked`` 是当天已经给出的名额
    （``{style: {code: 留档行}}``），它们原样保留，只把剩下的名额分给新出现的票。
    """
    quotes = quotes or {}
    memory = memory or {}
    yesterday = set(yesterday_codes or [])
    theme_result = build_themes(
        zt=rows, blocks=list(blocks or []), history=[], previous=None, yesterday_rows=[],
        recent_mains=list(recent_mains or []), hot_rank={}, rules=rules,
    )
    themes = theme_result["themes"]
    stock_themes = theme_result["stock_themes"]
    touches = _touches(rows, broken, yesterday)
    # 涨停原因是首选；没有（集合竞价期间上游不给）时依次退到历史涨停原因、当日题材榜的板块归属、
    # 所属概念。题材榜是「现在哪些板块在涨停」的直接答案，比历史记忆更贴近当下热点。
    block_names = _code_blocks(blocks or [])
    tags_of: Dict[str, set] = {}
    tags_source: Dict[str, str] = {}
    for row in [*rows, *broken]:
        own = set(split_reason(row.get("reason") or ""))
        if own:
            tags_of[row["code"]], tags_source[row["code"]] = own, "reason"
            continue
        hist = memory.get(row["code"]) or set()
        if hist:
            tags_of[row["code"]], tags_source[row["code"]] = hist, "history"
            continue
        board = set(block_names.get(row["code"]) or [])
        tags_of[row["code"]] = board
        tags_source[row["code"]] = "block" if board else ""

    # 第四级：东财选股器的所属概念。同花顺炸板池不给涨停原因（实测当日 100% 为空），这些票既没有
    # 当日原因、也常常没有历史涨停记录。概念列表很宽（一只票挂 30 多个很常见），整份拿来当题材等于
    # 随便挂一个，所以只取**与当日题材 / 题材榜相交**的那部分，并取其中最热的一个：「它的概念里正好
    # 有一个今天在涨停的题材」才算师出有名。
    heat_by_name = [(t["key"], int(t.get("zt_count") or 0)) for t in themes]
    heat_by_name += [(str(b.get("name") or ""), int(b.get("limit_up_num") or 0)) for b in (blocks or [])]
    nameless = sorted(code for code, tags in tags_of.items() if not tags)
    concepts_checked = False
    if nameless and concepts_lookup is not None:
        try:
            extra = concepts_lookup(nameless) or {}
        except Exception as exc:
            logger.warning("打板王雷达：涨停 / 炸板股概念兜底取数失败 %s", exc)
            extra = {}
        # 整批都没返回说明接口没给数据（不是这些票都没概念）：这一级按「没查过」处理，宁可保留也不舍弃。
        concepts_checked = bool(extra)
        for code in nameless:
            info = extra.get(code) or {}
            raw = [*(info.get("concepts") or []), info.get("industry") or ""]
            matched = sorted(((heat, name) for name, heat in heat_by_name
                              if name and _related(raw, [name])), reverse=True)
            if matched:
                tags_of[code] = {matched[0][1]}
                tags_source[code] = "concept"

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
        # 师出无名：四级来源都查过还是没有任何涨停原因 / 题材。科比要「有逻辑的低位首板」、陈小群要
        # 「新题材 + 资金合力」，两派都不打无名票，所以这种票不进名单，只在「今日不允许」里留痕。
        nameless_row = concepts_checked and not own and theme is None
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
        lockable = True
        if not first_time:
            # 集合竞价期间上游不给首次封板时间，竞价封单也可能撤：这种一律不占名额。
            notes.append("首次封板时间还没拿到（集合竞价期间上游未提供）：先观察，等 9:30 后再判断")
            cap_watch, lockable = True, False
        elif first_time <= open_time:
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
            if nameless_row:
                blocked.append({"code": code, "name": row.get("name") or "", "style": style,
                                "theme": "", "reason": NAMELESS_REASON})
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
                "theme_source": tags_source.get(code) or "",
                "theme_hint": (theme["name"] if theme
                               else (block_names.get(code) or [None])[0]
                               or "、".join(sorted(own)[:3])),
                "lockable": lockable,
                "peers": len(peer_codes),
                "peer_codes": peer_codes[:6],
                "reasons": filter_reasons(reasons, style),
                "notes": [*notes, *extra_notes],
                "phase": phase.get("kobe_label") if style == STYLE_KOBE else phase.get("chen_label"),
                "mode": MODE_FIRST_BOARD,
                "slot": None,
                "locked_at": None,
                "gate_action": action,
            })

    hot_themes = _hot_themes(
        blocks=blocks, themes=themes, cfg=(rules.get("preboard") or {}),
        market_size=max(1, len(rows) + len(broken)), prev_blocks=prev_blocks, news_lookup=news_lookup,
    )
    preboard_hits = _preboard_hits(
        phase=phase, rules=rules, themes=themes, themes_by_key={t["key"]: t for t in themes},
        sealed_codes={r["code"] for r in rows}, broken_codes={r["code"] for r in broken},
        yesterday=yesterday, memory=memory, max_picks=max_picks,
        preboard_rows=list(preboard or []), blocks=blocks, prev_blocks=prev_blocks,
        news_lookup=news_lookup, concepts_lookup=concepts_lookup, yesterday_weak=yesterday_weak,
        hot=hot_themes,
    )
    hits.extend(preboard_hits)

    # 每只票落在哪个今日热点题材上。题材名额、页面筛选、题材卡片上的候选数全部用这个 key，
    # 避免「机器人」（涨停原因聚类）和「机器人概念」（题材榜）被当成两个题材——那会让页面
    # 按题材一筛就什么都剩不下。
    hot_index = _hot_index(hot_themes)
    for hit in hits:
        hit["theme_key"] = _hot_key_for(
            [hit.get("theme") or "", *(tags_of.get(hit["code"]) or ())], hot_themes, hot_index,
        )

    lanes = _allocate(hits, phase=phase, rules=rules, max_picks=max_picks, locked=locked or {},
                      hot=hot_themes, freeze=freeze)
    _theme_counts(hits, hot_themes)
    hits.sort(key=lambda h: (h["style"], ACTION_ORDER[h["action"]], h["first_time_raw"]))
    return {
        "hits": hits,
        "blocked": blocked[:60],
        "themes": themes[:8],
        "hot_themes": hot_themes,
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


def _hot_themes(
    *,
    blocks: Optional[List[Dict[str, Any]]],
    themes: List[Dict[str, Any]],
    cfg: Dict[str, Any],
    market_size: int,
    prev_blocks: Optional[Dict[str, int]] = None,
    news_lookup: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """今日热点题材：够热 + 在升温 + 有新闻催化。盘中雷达的「主线」用它，页面上作为筛选 tab。

    - 够热：板块当日涨停 ≥3 只（与盘后主线口径一致），或占全市场涨停 ≥5%；
    - 升温：连续上榜 ≥2 天，或当日涨停数比前一交易日多；
    - 新闻：近 3 日相关新闻 ≥3 条（新闻不可用时降级放行）。
    """
    min_zt = int(cfg.get("hot_min_zt", 3))
    min_share = float(cfg.get("hot_min_share", 0.05))
    min_days = int(cfg.get("hot_min_days", 2))
    prev_counts = prev_blocks or {}
    hot: List[Dict[str, Any]] = []
    for block in blocks or []:
        name, count = str(block.get("name") or "").strip(), int(block.get("limit_up_num") or 0)
        if not name:
            continue
        days = int(block.get("days") or 0)
        share = count / market_size
        prev = prev_counts.get(normalize_tag(name))
        warming = days >= min_days or (prev is not None and count > prev)
        if (count >= min_zt or share >= min_share) and warming:
            hot.append({"key": name, "name": name, "zt_count": count, "rank": None, "novelty": "",
                        "days": days, "share": round(share, 4), "news_count": None})
    known = {normalize_tag(h["key"]) for h in hot}
    for theme in themes:
        if normalize_tag(theme["key"]) in known:
            continue
        if int(theme.get("zt_count") or 0) >= min_zt:
            hot.append({"key": theme["key"], "name": theme["name"], "zt_count": int(theme.get("zt_count") or 0),
                        "rank": theme.get("rank"), "novelty": theme.get("novelty") or "",
                        "days": theme.get("days") or 0,
                        "share": round(int(theme.get("zt_count") or 0) / market_size, 4), "news_count": None})

    news_limit = int(cfg.get("news_theme_limit", 8))
    news: Dict[str, int] = {}
    if news_lookup is not None and hot:
        try:
            news = news_lookup([h["name"] for h in hot[:news_limit]]) or {}
        except Exception as exc:
            logger.warning("打板王雷达：题材新闻取数失败 %s", exc)
    news_min = int(cfg.get("hot_news_min", 3))
    for item in hot:
        item["news_count"] = news.get(item["name"])
    hot = [h for h in hot if h["news_count"] is None or h["news_count"] >= news_min]
    hot.sort(key=lambda h: (-int(h["zt_count"] or 0), -(h.get("news_count") or 0)))
    # 热度排名决定锁定名额：前 N 名各 4 个（可配），其余各 2 个。盘中新题材挤进前 N 名时自动变成 4 个。
    # 名额是「每派各自」的，不是两派共用，见 _allocate。
    top_n = int(cfg.get("quota_top_n", 3))
    quota_top = int(cfg.get("quota_top", 4))
    quota_other = int(cfg.get("quota_other", 2))
    for index, item in enumerate(hot, start=1):
        item["rank"] = index
        item["quota"] = quota_top if index <= top_n else quota_other
    return hot


def _preboard_hits(
    *,
    phase: Dict[str, Any],
    rules: Dict[str, Any],
    themes: List[Dict[str, Any]],
    themes_by_key: Dict[str, Dict[str, Any]],
    sealed_codes: set,
    broken_codes: set,
    yesterday: set,
    memory: Dict[str, set],
    max_picks: int,
    preboard_rows: List[Dict[str, Any]],
    blocks: Optional[List[Dict[str, Any]]] = None,
    prev_blocks: Optional[Dict[str, int]] = None,
    news_lookup: Optional[Any] = None,
    concepts_lookup: Optional[Any] = None,
    yesterday_weak: Optional[set] = None,
    hot: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """还没涨停、但正在快速拉升的候选——目的是让人**买得进去**，而不是事后报告已经封板的票。

    数据：东财「火箭发射 / 快速反弹」异动（约 4 秒延迟）+ 腾讯快照（现价、涨停价、量比）。

    热点判定（三道门槛，全部写在卡片上）：

    1. **够热**：板块当日涨停 ≥3 只（与盘后「主线」口径一致），或占全市场涨停 ≥5%；
    2. **在升温**：板块连续上榜 ≥2 天，或当日涨停数比前一日多——1 天的散点爆发不算发酵；
    3. **有新闻催化**：板块名近 3 日相关新闻 ≥3 条（新闻服务不可用时降级为放行，并在页面标注）。

    候选股当下没有涨停原因：先用近 90 天的历史涨停原因贴热点板块，贴不上再用东财选股器的
    所属概念兜底。这些是**观察候选**，不占名额；卡片给出距涨停幅度与封板价。
    """
    cfg = rules.get("preboard") or {}
    if not cfg.get("enabled", True) or not preboard_rows:
        return []
    gap_max = float(cfg.get("gap_max", 6.0))
    min_change = float(cfg.get("min_change", 3.0))
    min_price = float(cfg.get("min_price", 2.0))
    max_circ = float(cfg.get("max_circ_mv", 500e8))
    per_style = int(cfg.get("max_per_style", 6))

    weak_set = set(yesterday_weak or ())
    basics = []
    for row in preboard_rows:
        code = row.get("code")
        price, limit_up = row.get("price"), row.get("limit_up")
        change = row.get("change_pct")
        if not code or code in sealed_codes or code in broken_codes:
            continue
        if not price or not limit_up or limit_up <= price or price < min_price:
            continue
        if change is None or change < min_change:
            continue
        gap = (limit_up / price - 1) * 100
        if gap > gap_max:
            continue
        if row.get("circ_mv") and row["circ_mv"] > max_circ:
            continue
        if "ST" in str(row.get("name") or "").upper():
            continue
        basics.append({**row, "gap_pct": round(gap, 2),
                       "weak_yesterday": row["code"] in weak_set,
                       "was_limit_yesterday": row["code"] in yesterday})
    if not basics:
        return []

    if hot is None:
        hot = _hot_themes(blocks=blocks, themes=themes, cfg=cfg,
                          market_size=max(1, len(sealed_codes) + len(broken_codes)),
                          prev_blocks=prev_blocks, news_lookup=news_lookup)
    if not hot:
        return []

    # ---- 把候选挂到热点板块上：先历史涨停原因，再东财所属概念
    matches: List[Dict[str, Any]] = []
    unmatched: List[Dict[str, Any]] = []
    for row in basics:
        tags = memory.get(row["code"]) or set()
        theme = _theme_for(tags, hot, {}, row["code"])
        if theme is not None:
            matches.append({"row": row, "theme": theme, "source": "history" if tags else ""})
        else:
            unmatched.append(row)
    if unmatched and concepts_lookup is not None:
        try:
            extra = concepts_lookup([r["code"] for r in unmatched]) or {}
        except Exception as exc:
            logger.warning("打板王雷达：概念兜底取数失败 %s", exc)
            extra = {}
        for row in unmatched:
            info = extra.get(row["code"]) or {}
            tags = set(info.get("concepts") or [])
            if info.get("industry"):
                tags.add(info["industry"])
            theme = _theme_for(tags, hot, {}, row["code"])
            if theme is not None:
                matches.append({"row": row, "theme": theme, "source": "concept"})

    matches.sort(key=lambda m: (-int(m["theme"].get("zt_count") or 0), m["row"]["gap_pct"],
                                -(m["row"].get("volume_ratio") or 0)))
    out: List[Dict[str, Any]] = []
    for style in STYLES:
        key = (phase.get("kobe_gate") if style == STYLE_KOBE else phase.get("chen_stage")) or ""
        gate = _gate(rules, style, key)
        if gate is None:
            continue
        entries: List[tuple] = []
        if style == STYLE_CHEN:
            min_peers = int(rules.get("live_chen_min_peers", 2))
            # (a) 新题材首板：原文只在回暖期「聚焦新题材首板/二板」。
            if key == "warm":
                entries += [(m, "first") for m in matches
                            if m["theme"].get("novelty") in ("new", "新题材")
                            and not m["row"].get("was_limit_yesterday")
                            and int(m["theme"].get("zt_count") or 0) >= min_peers]
            # (b) 弱转强：陈小群最核心的模式。昨日烂板 / 炸板，今日被买上去、逼近涨停。
            #     原文没有把它限定在某个阶段，回暖与发酵期都允许（发酵期是「分歧转一致」）。
            if key in ("warm", "ferment"):
                seen = {m["row"]["code"] for m, _ in entries}
                entries += [(m, "w2s") for m in matches
                            if m["row"].get("weak_yesterday") and m["row"]["code"] not in seen
                            and int(m["theme"].get("zt_count") or 0) >= min_peers]
            action = ACTION_TRIAL
        else:
            entries = [(m, "first") for m in matches if not m["row"].get("was_limit_yesterday")]
            action = ACTION_ACTIONABLE if gate == ACTION_ACTIONABLE else ACTION_TRIAL

        for item, kind in entries[:per_style]:
            row, theme = item["row"], item["theme"]
            news_text = (f"近 3 日新闻 {theme['news_count']} 条" if theme.get("news_count") is not None
                         else "新闻数据未取到")
            if kind == "w2s":
                reasons = [
                    {"text": "昨日是弱板（炸板 / 盘中开过板 / 尾盘偷鸡板），今天正在被买上去——弱转强的观察窗口",
                     "principle_ids": ["C04", "K27"]},
                    {"text": f"「{theme['name']}」今日已 {theme['zt_count']} 只涨停，有板块效应可以配合",
                     "principle_ids": ["C04"]},
                    {"text": f"{news_text}，题材有催化", "principle_ids": ["K19"]},
                ]
                notes = [
                    f"弱转强：昨日弱板，今日涨 {row.get('change_pct') or 0:.1f}%，距涨停 {row['gap_pct']:.1f}%",
                    "触发：开盘后被买上去、封板时扫板（不是竞价高开）；板块要有跟随",
                ]
                mode = MODE_WEAK_TO_STRONG
            else:
                reasons = [
                    {"text": f"「{theme['name']}」今日已 {theme['zt_count']} 只涨停，连续第 {theme.get('days') or 1} 天上榜，{news_text}",
                     "principle_ids": ["C14", "K19"]},
                    {"text": "还没涨停：在发酵的题材里等它封板，属于首板预判", "principle_ids": ["K10", "K33"]},
                ]
                notes = [
                    f"未涨停：现价 {row['price']:.2f}，涨停价 {row['limit_up']:.2f}，还差 {row['gap_pct']:.1f}%",
                    f"封板价 {row['limit_up']:.2f}——封住时才谈打板；不封就不要追",
                ]
                mode = MODE_FIRST_BOARD
            out.append({
                "code": row["code"], "name": row.get("name") or "", "style": style,
                "style_label": STYLE_LABELS[style], "action": action,
                "action_label": ACTION_LABELS[action], "position_pct": 0,
                "status": "preboard", "slot": None, "locked_at": None, "lockable": True,
                "first_time": _fmt_time(row.get("time")), "first_time_raw": row.get("time") or "",
                "last_time": "—", "open_num": 0, "sealed": False, "limit_price": row["limit_up"],
                "price": row["price"], "change_pct": row.get("change_pct"),
                "turnover_rate": row.get("turnover_rate"), "circ_mv": row.get("circ_mv"),
                "seal_amount": None, "seal_ratio": None, "reason": "", "high_days": "", "limit_up_type": "",
                "theme": theme["name"], "theme_rank": theme.get("rank"),
                "theme_main": bool(theme.get("is_main")), "novelty": theme.get("novelty") or "",
                "theme_source": item["source"] or "concept", "theme_hint": theme["name"],
                "gap_pct": row["gap_pct"], "volume_ratio": row.get("volume_ratio"),
                "sealed_in_theme": int(theme.get("zt_count") or 0), "theme_news": theme.get("news_count"),
                "peers": int(theme.get("zt_count") or 0), "peer_codes": [],
                "reasons": filter_reasons(reasons, style), "notes": notes,
                "phase": phase.get("kobe_label") if style == STYLE_KOBE else phase.get("chen_label"),
                "mode": mode, "gate_action": action,
            })
    return out


def _theme_counts(hits: List[Dict[str, Any]], hot: Optional[List[Dict[str, Any]]]) -> None:
    """把分配结果回写到题材卡片：每派各自的「已锁定名额」与「未锁定候选」。

    必须**分派统计**：``_allocate`` 里每派按自己已锁定的票独立消耗题材名额，两派名额互不相通，
    把两派加在一起显示会让一个题材看起来早就满了。

    候选数和页面「未锁定潜力股」标签取自同一组 ``hits``、同一个判据（有没有拿到名额），所以
    点某个题材时两个数一定对得上；这里必须在 ``_allocate`` 之后调用，名额此时才定下来。
    """
    locked_by: Dict[str, Dict[str, int]] = {}
    free_by: Dict[str, Dict[str, int]] = {}
    for hit in hits:
        key = str(hit.get("theme_key") or "")
        if not key:
            continue
        bucket = locked_by if hit.get("slot") is not None else free_by
        per_style = bucket.setdefault(key, {})
        per_style[hit["style"]] = per_style.get(hit["style"], 0) + 1
    for item in hot or []:
        key = str(item.get("key") or "")
        used, free = locked_by.get(key, {}), free_by.get(key, {})
        item["by_style"] = {style: {"used": used.get(style, 0), "candidates": free.get(style, 0)}
                            for style in STYLES}
        item["used"] = sum(used.values())
        item["candidates"] = sum(free.values())


def _hit_from_record(
    record: Dict[str, Any],
    style: str,
    phase: Dict[str, Any],
    *,
    hot: Optional[List[Dict[str, Any]]] = None,
    hot_index: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """已锁定、但这一轮在涨停池 / 炸板池里都看不到的票（上游缺数据）：照留档原样显示，名额不动。"""
    first = str(record.get("first_time") or "")
    theme = record.get("theme") or ""
    return {
        "code": record["code"], "name": record.get("name") or "", "style": style, "style_label": STYLE_LABELS[style],
        "action": record.get("action") or ACTION_TRIAL, "action_label": ACTION_LABELS.get(record.get("action"), ""),
        "position_pct": int(record.get("position_pct") or 0), "status": "missing",
        "first_time": _fmt_time(first), "first_time_raw": first, "last_time": "—", "open_num": int(record.get("open_count") or 0),
        "sealed": None, "limit_price": record.get("limit_price"), "price": None, "change_pct": None,
        "turnover_rate": None, "circ_mv": None, "seal_amount": None, "seal_ratio": None, "reason": "",
        "high_days": "", "limit_up_type": "", "theme": theme, "theme_rank": None,
        "theme_key": _hot_key_for([theme], hot, hot_index or {}),
        "theme_main": False, "novelty": "", "peers": 0, "peer_codes": [], "reasons": [],
        "theme_source": "record" if theme else "", "theme_hint": theme, "lockable": False,
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
    hot: Optional[List[Dict[str, Any]]] = None,
    freeze: bool = False,
) -> Dict[str, Any]:
    """按题材名额分配：热度前 N 名的题材各 ``quota_top`` 个锁定名额，其余各 ``quota_other`` 个；
    同时不超过该派当前阶段的仓位上限。

    **题材名额按派独立**：两派的门控、仓位、纪律都不一样，所以「机器人概念 4 个名额」是陈小群
    4 个、科比 4 个，不是两派合起来 4 个。题材卡片上的数也必须分派显示（见 :func:`_theme_counts`）。

    **锁定 = 此刻还能买**：名额只发给「还没涨停、正在拉升、距涨停很近」的候选（`status=preboard`）。
    已经封板或炸板的票不再占名额——它们出现时已经买不进去了，只作为对照展示。

    名额一旦给出当天就锁定：成绩单要记当时真实给出的票，不能事后换。已锁定的票原样保留动作、仓位与
    名额序号（哪怕后来炸板、或不封），只把剩余名额与剩余仓位分给新出现的未涨停候选。
    排序按「距涨停越近越优先」，同距按量比。
    """
    lanes: Dict[str, Any] = {}
    hot_index = _hot_index(hot)
    for style in STYLES:
        key = (phase.get("kobe_gate") if style == STYLE_KOBE else phase.get("chen_stage")) or ""
        cap = _cap(rules, style, key)
        mine_locked = dict(locked.get(style) or {})
        present = {h["code"] for h in hits if h["style"] == style}
        for code, record in mine_locked.items():
            if code not in present:
                hits.append(_hit_from_record(record, style, phase, hot=hot, hot_index=hot_index))
        used = sum(int(r.get("position_pct") or 0) for r in mine_locked.values())
        taken = sorted(int(r["slot"]) for r in mine_locked.values() if r.get("slot"))
        count = len(mine_locked)
        # 每个题材已锁定的数量。键一律是今日热点题材的 key（见 _hot_key_for），留档里存的是展示名，
        # 所以这里要先映射回去，否则「机器人」和「机器人概念」会各占一份名额。
        # 名额按派独立消耗：这个 dict 在 style 循环里，两派互不相通。
        quota_by_theme = {str(t.get("key") or ""): int(t.get("quota") or 0) for t in (hot or [])}
        rank_by_theme = {str(t.get("key") or ""): int(t.get("rank") or 0) for t in (hot or [])}
        display_by_theme = {str(t.get("key") or ""): str(t.get("name") or "") for t in (hot or [])}
        theme_used: Dict[str, int] = {}
        for record in mine_locked.values():
            name = _hot_key_for([record.get("theme") or ""], hot, hot_index)
            theme_used[name] = theme_used.get(name, 0) + 1
        last_locked = max((str(r.get("first_time") or "") for r in mine_locked.values()), default="")
        next_slot = (max(taken) if taken else count) + 1

        candidates = sorted(
            (h for h in hits if h["style"] == style),
            key=lambda h: (h.get("gap_pct") if h.get("gap_pct") is not None else 99, -(h.get("volume_ratio") or 0)),
        )
        for hit in candidates:
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
            if freeze:
                # 盘中赚钱效应回落且没有题材在接力：停发新名额。已锁定的在上面原样保留，这里只拦新的。
                hit["notes"].append("盘中赚钱效应回落且没有题材在接力，已停发新名额，只观察")
                hit["action"] = ACTION_WATCH
                hit["action_label"] = ACTION_LABELS[ACTION_WATCH]
                continue
            if hit.get("status") != "preboard":
                # 已经封板 / 炸板：出现时已经买不进去，不占名额，只作对照。
                if hit["action"] in (ACTION_ACTIONABLE, ACTION_TRIAL):
                    hit["notes"].append("已经封板，承接不到；锁定只发给还没涨停的候选")
                hit["action"] = ACTION_WATCH
                hit["action_label"] = ACTION_LABELS[ACTION_WATCH]
                continue
            want = _position(style, hit["action"], rules)
            room = cap - used
            late = bool(last_locked) and hit["first_time_raw"] < last_locked
            theme_name = str(hit.get("theme_key") or "")
            quota = quota_by_theme.get(theme_name, int((rules.get("preboard") or {}).get("quota_other", 2)))
            theme_rank = rank_by_theme.get(theme_name)
            if not theme_name or theme_used.get(theme_name, 0) >= quota:
                hit["notes"].append(
                    (f"「{display_by_theme.get(theme_name, theme_name)}」今日热度第 {theme_rank} 名，"
                     f"本派锁定名额 {quota} 个已满" if theme_name and theme_rank
                     else f"「{display_by_theme.get(theme_name) or hit.get('theme') or '无题材'}」"
                          "不在今日热点里或名额已满")
                    + "，只观察"
                )
                hit["action"] = ACTION_WATCH
                hit["action_label"] = ACTION_LABELS[ACTION_WATCH]
                continue
            if room <= 0:
                reason = f"本派当前阶段仓位上限 {cap}% 已锁定用满"
                if late:
                    reason += "；它首次触板更早，但数据晚到，已锁定的名额不会让出"
                hit["notes"].append(reason + "，只观察")
                hit["action"] = ACTION_WATCH
                hit["action_label"] = ACTION_LABELS[ACTION_WATCH]
                continue
            if count >= max_picks:
                # 科比「我一般是分四个票操作」：名额个数本身就是纪律，不能只靠仓位上限间接限制
                # （主升期仓位上限 100%、单只 10%，不拦的话一天能发 10 个名额）。
                hit["notes"].append(f"本派当天名额 {max_picks} 个已锁定用满（分四个票），只观察")
                hit["action"] = ACTION_WATCH
                hit["action_label"] = ACTION_LABELS[ACTION_WATCH]
                continue
            hit["position_pct"] = min(want, room)
            used += hit["position_pct"]
            count += 1
            theme_used[theme_name] = theme_used.get(theme_name, 0) + 1
            hit["slot"] = next_slot
            next_slot += 1
            hit["theme_quota"] = quota
            hit["theme_rank"] = theme_rank
        lanes[style] = {
            "themes": [{"key": t.get("key"), "name": t.get("name"), "rank": t.get("rank"),
                        "quota": t.get("quota"), "used": theme_used.get(str(t.get("key") or ""), 0)}
                       for t in (hot or [])],
            "phase_key": key,
            "cap": cap,
            "used": used,
            "picks": count,
            "locked": len(mine_locked),
            "max_picks": max_picks,
            "sell_rules": [{"text": text, "principle_ids": ids} for text, ids in LIVE_SELL_RULES[style]],
        }
    return lanes


def _valid_time(value: Any) -> bool:
    """首次封板时间是否有效（``HHMMSS``）。早期版本会把「—」写进来，一律视为无效。"""
    text = str(value or "").strip()
    return len(text) >= 4 and text.isdigit()


def weak_codes(zt_rows: Iterable[Dict[str, Any]], broken_codes: Iterable[str], *, late: str) -> List[str]:
    """昨日的弱票：炸板 + 涨停池里的烂板（盘中开过板）与尾盘板（末封 ≥ ``late``）。

    弱转强要求前一日是「分歧 / 烂板 / 核按钮」（陈小群原文），科比也把尾盘偷鸡板算弱板（#33）。
    昨日**干净涨停**的票今天再被买上去是二板 / 强转强，不是弱转强，不能混进来。

    口径与盘后 M2 一致（炸板回封 / 末封 ≥14:30）。开过板优先看 ``open_num``（上游池接口给）；
    库里的留档没有这一列，退化为「末封时间晚于首封时间」——实测与 ``open_num`` 完全一致。
    """
    weak = {str(code) for code in (broken_codes or ()) if code}
    for row in zt_rows or []:
        code = str(row.get("code") or "")
        if not code:
            continue
        first, last = str(row.get("first_time") or ""), str(row.get("last_time") or "")
        opened = int(row.get("open_num") or 0) >= 1 or (
            _valid_time(first) and _valid_time(last) and last > first
        )
        if opened or (_valid_time(last) and last >= str(late)):
            weak.add(code)
    return sorted(weak)


def locked_from_records(records: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """留档行 → ``{style: {code: 行}}``。早期没有名额序号的行按首次触板时间补一个，保证序号连续。

    没有首次封板时间的行一律不算锁定：那是集合竞价期间上游还没给时间时写下的，竞价封单可能撤掉，
    不该占名额（早期版本会写进去，读的时候在这里兜住）。
    """
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for record in sorted(records, key=lambda r: (str(r.get("first_time") or ""), r.get("code") or "")):
        if record.get("action") not in (ACTION_ACTIONABLE, ACTION_TRIAL):
            continue
        if not _valid_time(record.get("first_time")):
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
            yesterday_rows = list(self.repository.pool_rows(prev, "zt"))
            yesterday = {r["code"] for r in yesterday_rows}
            yesterday_broken = {r["code"] for r in self.repository.pool_rows(prev, "zb")}
        except Exception as exc:
            logger.warning("打板王雷达：读取近期主线 / 昨日涨停失败 %s", exc)
            yesterday, yesterday_rows, yesterday_broken = set(), [], set()
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
            "yesterday_broken": sorted(yesterday_broken),
            "yesterday_weak": weak_codes(yesterday_rows, yesterday_broken,
                                         late=str(self.rules.get("weak_late_time", "143000"))),
            # 门控的具体原因：前一交易日的指标、六阶段条件核对、科比的连续杀跌天数。
            # 与盘后复盘同一份结构（``metrics`` / ``stage`` / ``kobe_stage``），页面可以直接复用同一套渲染。
            "metrics": payload.get("metrics") or {},
            "stage": stage,
            "kobe_stage": kobe,
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
                "yesterday_broken": sorted(r["code"] for r in _rows(prev, "zb")),
                "yesterday_weak": weak_codes(_rows(prev, "zt"),
                                             [r["code"] for r in _rows(prev, "zb")],
                                             late=str(self.rules.get("weak_late_time", "143000"))),
                "metrics": metrics[prev],
                "stage": stage,
                "kobe_stage": kobe,
            }
        except Exception as exc:
            logger.warning("打板王雷达：现算 %s 的周期失败 %s", prev, exc)
            return {
                "available": False,
                "trade_date": prev,
                "reason": f"无法判定 {prev} 的情绪周期（{type(exc).__name__}）；可先生成一次盘后复盘",
            }

    def _changes(self, kinds: Tuple[str, ...], *, timeout: float,
                 reuse_sec: float) -> Tuple[Dict[str, List[Dict[str, Any]]], List[str]]:
        """拉升异动（东财盘口异动）。返回 ``({异动类型: 事件列表}, 降级说明列表)``。

        东财 push2ex 在 09:30 开盘那几分钟会读超时或直接断连（实测 2026-10-23 09:30–09:32 连续多轮：
        ``Read timed out`` 与 ``RemoteDisconnected`` 各半）。三件事要分开处理：

        - **超时要短**：雷达 10 秒一轮，这一路用 15 秒预算 × 2 次重试能把一轮拖到 30 秒以上；
        - **两类并发、共用预算**：一路慢不该让另一路排队等；
        - **失败要能沿用**：候选本来只看「最近 10 分钟的异动」，复用一份 ``reuse_sec`` 秒内的事件流
          不改变语义，但能让一轮取数失败不至于把候选清空。
        """
        from data_provider import limit_up_fetcher as lu

        from src.services.daban.fetchers import run_parallel

        results = run_parallel(
            {kind: (lambda k=kind: lu.fetch_em_changes(k, timeout=timeout)) for kind in kinds},
            timeout=timeout + 1, workers=len(kinds) or 1,
        )
        out: Dict[str, List[Dict[str, Any]]] = {}
        notes: List[str] = []
        for kind in kinds:
            events, err = results.get(kind) or (None, "未执行")
            if events is not None:
                _CHANGE_CACHE[kind] = (time.time(), list(events))
                out[kind] = list(events)
                continue
            cached = _CHANGE_CACHE.get(kind)
            age = (time.time() - cached[0]) if cached else None
            if cached and age is not None and age < reuse_sec:
                out[kind] = list(cached[1])
                notes.append(f"拉升异动（{kind}）取数失败，沿用 {int(age)} 秒前的一份：{str(err)[:60]}")
            else:
                out[kind] = []
                notes.append(f"拉升异动（{kind}）不可用，这一路的未涨停候选为空：{str(err)[:60]}")
        return out, notes

    def _realtime_gate(self, pool_date: str, *, rows, broken, phase, now: str, payload) -> Dict[str, Any]:
        """组装实时门控需要的输入，交给 :func:`decide_realtime_gate` 判。失败一律按「不活跃」降级。"""
        cfg = self.rules.get("realtime_gate") or {}
        if not cfg.get("enabled", True):
            return {"enabled": False, "active": False, "upgraded": False, "frozen": False, "reason": "",
                    "keys": {STYLE_CHEN: str(phase.get("chen_stage") or ""),
                             STYLE_KOBE: str(phase.get("kobe_gate") or "")}}
        from src.services.daban import cycle as cycle_mod
        from src.services.daban.themes import build_themes

        timeout = float(getattr(self.config, "daban_fetch_timeout_sec", 15) or 15)
        extra = self._cycle_inputs(pool_date, timeout=timeout)
        for note in extra.get("notes") or []:
            if note not in payload["degraded_reasons"]:
                payload["degraded_reasons"].append(note)
        metrics = cycle_mod.compute_metrics(
            zt=rows, zb=broken, dt=extra.get("dt") or [], previous=extra.get("previous"), rules=self.rules,
        )
        day_themes = build_themes(zt=rows, blocks=[], history=[], previous=None, yesterday_rows=[],
                                  recent_mains=[], hot_rank={}, rules=self.rules)["themes"]
        metrics["follow_rate"] = round(day_themes[0]["zt_count"] / len(rows), 4) if day_themes and rows else None
        # 「较昨日」的几条判定（晋级率上升、连板高度回落、大面增多）要有 delta 才算得出来。
        cycle_mod.attach_deltas(metrics, phase.get("metrics") or None)
        today = realtime_phase(metrics=metrics, prev_stage=(phase.get("stage") or {}).get("key"),
                               history=self.stage_history(pool_date), rules=self.rules)
        # 轮动豁免要的是「通过热点三道门槛的题材在加速」，所以这里按同一套 cfg 现算一份热点名单。
        hot_now = _hot_themes(blocks=self.blocks(), themes=[], cfg=(self.rules.get("preboard") or {}),
                              market_size=max(1, len(rows) + len(broken)),
                              prev_blocks=self.previous_block_counts(pool_date), news_lookup=self.theme_news)
        accel = _theme_accelerating(rows, hot_now, now=now,
                                    window_min=int(cfg.get("theme_accel_window_min", 15)),
                                    min_new=int(cfg.get("theme_accel_min", 2)))
        decision = decide_realtime_gate(
            rules=self.rules, phase=phase, metrics=metrics, today=today, now=now,
            accel_theme=accel, state=_RT_STATE.setdefault(pool_date, {}),
        )
        decision["metrics"] = {key: metrics.get(key) for key in (
            "zt_count", "zb_count", "dt_count", "lianban_count", "max_height",
            "promotion_rate", "yzt_premium", "zab_rate", "follow_rate")}
        return decision

    def _cycle_inputs(self, pool_date: str, *, timeout: float) -> Dict[str, Any]:
        """实时周期要补的两路数据：当日跌停池、昨日涨停票今日表现（赚钱效应）。

        两路并发、共用预算、缓存 30 秒——它们变化比涨停池慢，没必要跟着 10 秒轮询跑。
        """
        cached = _CYCLE_CACHE.get(pool_date)
        if cached and (time.time() - cached[0]) < 30:
            return cached[1]
        from src.services.daban.fetchers import fetch_em, fetch_limit_pool, run_parallel

        budget = min(timeout, 6.0)
        results = run_parallel(
            {
                "dt": lambda: fetch_limit_pool("dt", pool_date, timeout=budget, include_bse=False),
                "previous": lambda: fetch_em("previous", pool_date, timeout=budget, include_bse=False),
            },
            timeout=budget + 1, workers=2,
        )
        out: Dict[str, Any] = {"dt": [], "previous": None, "notes": []}
        for key in ("dt", "previous"):
            env, err = results.get(key) or (None, "未执行")
            rows = ((env or {}).get("value") or {}).get("rows") if (env or {}).get("ok") else None
            if rows is None:
                label = "跌停池" if key == "dt" else "昨日涨停今日表现"
                out["notes"].append(f"{label}不可用（实时周期的这一项按缺失处理）：{str(err)[:50]}")
                continue
            out[key] = rows
        _CYCLE_CACHE[pool_date] = (time.time(), out)
        return out

    def stage_history(self, before: str) -> List[Dict[str, Any]]:
        """此前交易日的阶段与昨日涨停溢价摘要，按时间正序——科比要靠它数「连续杀跌第几天」。"""
        cached = _HISTORY_CACHE.get(before)
        if cached and (time.time() - cached[0]) < 600:
            return cached[1]
        try:
            rows = self.repository.history(limit=10, before=before)
        except Exception as exc:
            logger.warning("打板王雷达：读取阶段历史失败 %s", exc)
            return []
        out = [{"stage": row.stage, "yzt_premium": row.yzt_premium} for row in reversed(rows) if row.stage]
        _HISTORY_CACHE[before] = (time.time(), out)
        return out

    def blocks(self) -> List[Dict[str, Any]]:
        """当日涨停题材榜（同花顺 block_top）。盘中每轮都用，缓存 20 秒；失败返回空，不阻断快照。

        新封板的票要过一小会儿才会出现在板块成员里，缓存太长会让「题材」一直空着，所以取 20 秒。
        """
        cached = _BLOCK_CACHE.get("blocks")
        if cached and (time.time() - cached[0]) < 20:
            return cached[1]
        try:
            from data_provider import limit_up_fetcher as lu

            value = lu.fetch_ths_blocks(None, timeout=float(getattr(self.config, "daban_fetch_timeout_sec", 15) or 15))
        except Exception as exc:
            logger.warning("打板王雷达：题材榜不可用（题材改用历史涨停原因）%s", exc)
            value = []
        _BLOCK_CACHE["blocks"] = (time.time(), value)
        return value

    def previous_block_counts(self, pool_date: str) -> Dict[str, int]:
        """前一交易日各板块的涨停数，用来判断「今天比昨天多」。按日缓存。"""
        from src.services.daban.fetchers import previous_trade_date

        prev = previous_trade_date(pool_date)
        cached = _PREV_BLOCK_CACHE.get(prev)
        if cached is not None:
            return cached
        try:
            from data_provider import limit_up_fetcher as lu

            blocks = lu.fetch_ths_blocks(prev, timeout=float(getattr(self.config, "daban_fetch_timeout_sec", 15) or 15))
            counts = {normalize_tag(b.get("name") or ""): int(b.get("limit_up_num") or 0) for b in blocks}
        except Exception as exc:
            logger.warning("打板王雷达：读取 %s 的题材榜失败（升温改用连续上榜天数）%s", prev, exc)
            counts = {}
        _PREV_BLOCK_CACHE[prev] = counts
        return counts

    def theme_news(self, names: List[str]) -> Dict[str, int]:
        """题材近 3 日新闻条数。新闻也是热点门槛：没有新闻催化的价格联动不算发酵。

        并发查、6 秒预算、缓存 10 分钟；整批失败返回空 dict（调用方降级为放行并标注）。
        """
        if not names:
            return {}
        now = time.time()
        cached = {name: count for name, (ts, count) in _NEWS_CACHE.items() if now - ts < 600}
        missing = [name for name in names if name not in cached and name not in _NEWS_INFLIGHT][:6]
        if missing:
            # 新闻查询慢（每个题材要打多家站点），不能拖慢 10 秒一次的轮询：后台补缓存，
            # 这一轮先按「新闻未取到」放行并在卡片上标注，下一轮生效。
            for name in missing:
                _NEWS_INFLIGHT.add(name)
            threading.Thread(target=self._fill_news, args=(missing,), daemon=True).start()
        return {name: cached[name] for name in names if name in cached}

    @staticmethod
    def _fill_news(names: List[str]) -> None:
        from concurrent.futures import ThreadPoolExecutor

        from src.services.news_search_service import NewsSearchService

        service = NewsSearchService()

        def _one(name: str):
            try:
                result = service.search_all(keyword=name, days=3, limit=30, budget_sec=5)
                return name, int(result.get("total") or 0)
            except Exception as exc:
                logger.debug("打板王雷达：%s 新闻取数失败 %s", name, exc)
                return name, None
            finally:
                _NEWS_INFLIGHT.discard(name)

        pool = ThreadPoolExecutor(max_workers=min(4, len(names)), thread_name_prefix="daban-news")
        try:
            for name, count in pool.map(_one, names):
                if count is not None:
                    _NEWS_CACHE[name] = (time.time(), count)
        finally:
            pool.shutdown(wait=False)

    def _theme_news_blocking(self, names: List[str]) -> Dict[str, int]:
        """同步版（仅测试 / 离线补数据用）：等新闻结果返回。"""
        out: Dict[str, int] = {}
        self._fill_news(list(names))
        now = time.time()
        for name in names:
            item = _NEWS_CACHE.get(name)
            if item and now - item[0] < 600:
                out[name] = item[1]
        return out

    def stock_concepts(self, codes: List[str]) -> Dict[str, Dict[str, Any]]:
        """候选股的所属概念（东财选股器），用于没有历史涨停记录的票判断题材。按日缓存。"""
        if not codes:
            return {}
        cached = _CONCEPT_CACHE.get("concepts") or {}
        missing = [c for c in codes if c not in cached]
        if missing:
            try:
                from data_provider import limit_up_fetcher as lu

                cached.update(lu.fetch_em_concepts(
                    missing, timeout=float(getattr(self.config, "daban_fetch_timeout_sec", 15) or 15)
                ))
            except Exception as exc:
                logger.warning("打板王雷达：个股概念取数失败 %s", exc)
            _CONCEPT_CACHE["concepts"] = cached
        return {c: cached[c] for c in codes if c in cached}

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

        # 未涨停候选：东财「火箭发射 / 快速反弹」异动 → 腾讯快照。没有它就只剩事后报告已封板的票。
        preboard_rows: List[Dict[str, Any]] = []
        if (self.rules.get("preboard") or {}).get("enabled", True):
            cfg = self.rules.get("preboard") or {}
            window = int(cfg.get("window_min", 10))
            now_min = int(now[:2]) * 60 + int(now[2:4])
            rise_events: Dict[str, Dict[str, Any]] = {}
            feeds, notes = self._changes(
                ("rise", "rebound"),
                timeout=min(timeout, float(cfg.get("fetch_timeout_sec", 4) or 4)),
                reuse_sec=float(cfg.get("reuse_sec", 90) or 0),
            )
            payload["degraded_reasons"].extend(notes)
            for kind in ("rise", "rebound"):
                for event in feeds.get(kind) or []:
                    hh, mm = int(event["time"][:2] or 0), int(event["time"][2:4] or 0)
                    if event["code"] in rise_events or (now_min - (hh * 60 + mm)) > window:
                        continue
                    rise_events[event["code"]] = event
            try:
                if rise_events:
                    snap = lu.fetch_tencent_quotes(list(rise_events), timeout=timeout)
                    for code, quote in snap.items():
                        event = rise_events.get(code)
                        if not event or not quote.get("price") or not quote.get("prev_close"):
                            continue
                        preboard_rows.append({
                            "code": code, "name": quote.get("name") or event.get("name") or "",
                            "price": quote["price"], "limit_up": quote.get("limit_up"),
                            "change_pct": round((quote["price"] / quote["prev_close"] - 1) * 100, 2),
                            "circ_mv": quote.get("circ_mv"), "volume_ratio": quote.get("volume_ratio"),
                            "turnover_rate": quote.get("turnover_rate"), "time": event.get("time") or "",
                        })
            except Exception as exc:
                payload["degraded_reasons"].append(f"候选盘口快照不可用（未涨停候选为空）：{str(exc)[:60]}")

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

        # 盘中实时门控：前一交易日的周期只做底线，今天的 tape 给出市场验证后按今天的周期行事。
        # 必须在 build_hits 之前决定，因为它改的就是喂给 build_hits 的门控键。
        rt = {"enabled": False, "active": False, "upgraded": False, "frozen": False, "reason": ""}
        if not payload["replay"]:
            try:
                rt = self._realtime_gate(pool_date, rows=rows, broken=broken, phase=phase, now=now, payload=payload)
            except Exception as exc:
                logger.warning("打板王雷达：实时门控判定失败，按前一交易日的周期门控 %s", exc)
                payload["degraded_reasons"].append(f"实时门控不可用，按前一交易日的周期门控：{str(exc)[:60]}")
            if rt.get("enabled") and rt.get("keys"):
                phase = {**phase, "chen_stage": rt["keys"][STYLE_CHEN], "kobe_gate": rt["keys"][STYLE_KOBE],
                         "chen_label": (rt.get("labels") or {}).get(STYLE_CHEN) or phase.get("chen_label"),
                         "kobe_label": (rt.get("labels") or {}).get(STYLE_KOBE) or phase.get("kobe_label")}
        payload["realtime_gate"] = rt

        # 集合竞价（09:15–09:30）期间的封单可以撤、涨停原因也没有，不占名额；09:30 后才开始锁定。
        recording = trading and not payload["replay"] and now >= "093000"
        # 读已锁定名额 → 分配剩余名额 → 留档，必须是一个原子步骤：页面轮询和后台任务可能同时跑，
        # 否则两边各自把同一个空名额分给不同的票。
        with _ALLOCATION_LOCK:
            locked = self.locked(pool_date)
            built = build_hits(
                rows=rows, broken=broken, phase=phase, rules=self.rules, quotes=quotes,
                memory=self.memory(until=pool_date), recent_mains=phase.get("recent_mains"),
                yesterday_codes=phase.get("yesterday_codes"), max_picks=self.max_picks, locked=locked,
                blocks=self.blocks() if not payload["replay"] else [], preboard=preboard_rows,
                prev_blocks=self.previous_block_counts(pool_date) if not payload["replay"] else {},
                news_lookup=self.theme_news if not payload["replay"] else None,
                concepts_lookup=self.stock_concepts if not payload["replay"] else None,
                yesterday_weak=set(phase.get("yesterday_weak") or ()),
                freeze=bool(rt.get("frozen")),
            )
            fresh = self._record(pool_date, built["hits"]) if recording else []
            if recording:
                self._record_preboard(pool_date, built["hits"])
        payload.update({
            "ok": True,
            "hits": built["hits"],
            "blocked": built["blocked"],
            "themes": built["themes"],
            "hot_themes": built.get("hot_themes") or [],
            "lanes": built["lanes"],
            "stats": built["stats"],
            "gate_labels": {
                "kobe": phase.get("kobe_label"),
                "chen": phase.get("chen_label"),
                "kobe_action": _gate(self.rules, STYLE_KOBE, phase.get("kobe_gate") or ""),
                "chen_action": _gate(self.rules, STYLE_CHEN, phase.get("chen_stage") or ""),
                # 陈小群：首板只在回暖期做；弱转强在最核心的模式里，回暖 / 发酵期都可试错。
                "chen_w2s_action": ("trial" if phase.get("chen_stage") in ("warm", "ferment")
                                    and _gate(self.rules, STYLE_CHEN, phase.get("chen_stage") or "") is not None
                                    else None),
                "source": phase.get("source"),
                "trade_date": phase.get("trade_date"),
                "chen_reason": _phase_reason(STYLE_CHEN, phase, self.rules),
                "kobe_reason": _phase_reason(STYLE_KOBE, phase, self.rules),
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
        calls = [
            h for h in hits
            if h["action"] in (ACTION_ACTIONABLE, ACTION_TRIAL)
            and not h.get("locked_at") and h.get("lockable", True) and h.get("first_time_raw")
        ]
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

    def _record_preboard(self, trade_date: str, hits: List[Dict[str, Any]]) -> None:
        """未涨停候选也留档（``origin=preboard``、动作 watch）：不占名额，只用来统计当日封板率。"""
        rows = [h for h in hits if h.get("status") == "preboard"]
        if not rows:
            return
        try:
            self.repository.upsert_live_hits(
                trade_date, [live_hit_record(h, origin="preboard") for h in rows], final=False,
            )
        except Exception as exc:
            logger.warning("打板王雷达：未涨停候选留档失败 %s", exc)

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
        "first_time": (hit.get("first_time_raw") or "") if _valid_time(hit.get("first_time_raw")) else "",
        "seen_at": seen_at or hit.get("first_time_raw") or "",
        "limit_price": hit.get("limit_price"),
        "position_pct": hit.get("position_pct"),
        "slot": hit.get("slot"),
        "sealed_close": None,
        "open_count": hit.get("open_num"),
        "theme": (hit.get("theme") or "")[:64],
        "phase": hit.get("phase"),
        "origin": origin,
        "gap_pct": hit.get("gap_pct"),
        "seen_price": hit.get("price"),
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
        pending = [h for h in repository.live_hits(trade_date)
                   if not h.get("reviewed") and h.get("limit_price") and _valid_time(h.get("first_time"))
                   and h.get("origin") != "preboard"]
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
            # 成本：锁定那一刻的现价（当时真能买到的价）；老记录没有时退回打板价。
            cost = hit.get("seen_price") or hit["limit_price"]
            buy = float(cost)
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
    _BLOCK_CACHE.clear()
    _CHANGE_CACHE.clear()
    _CYCLE_CACHE.clear()
    _HISTORY_CACHE.clear()
    _RT_STATE.clear()
    _PREV_BLOCK_CACHE.clear()
    _NEWS_CACHE.clear()
    _NEWS_INFLIGHT.clear()
    _CONCEPT_CACHE.clear()
    _MEMORY_CACHE.clear()
    _PREV_CACHE.clear()
    _REVIEW_CACHE.clear()
    _NOTIFIED.clear()


__all__ = [
    "LIVE_TASK_NAME", "LIVE_TASK_SECONDS", "LiveRadarService", "build_hits", "build_live_background_tasks",
    "clear_cache", "filter_reasons", "is_trading_day", "limit_price_of", "live_hit_record", "locked_from_records",
    "market_open_now",
    "render_hit_alert", "review_pending_live_hits", "weak_codes",
]
