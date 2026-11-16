# -*- coding: utf-8 -*-
"""盘中雷达：找到热点题材和题材里可能涨停、用户还买得进的票，按计分表评分，进入「重点」的每一只都记成绩。

2026-11-08 起（评分体系 v2 + 短线世界观）：

- **不锁定、不设名额、不给仓位**。两派的门控合并成一份世界观边界（见 ``worldview.decide_boundary``）：前一交易日收盘
  是冰点 / 退潮 / 主跌第 1 天时管住手；「龙头见顶杀高位」节点日、盘中市场验证、推翻信号（大盘急拉 / 题材异动 /
  新题材出现）放开对应的方向。
- **「重点」tab**：还买得进（没封板且距涨停 ≤ 6%，或炸开后现价低于涨停价）、属于当下热点题材、没被边界挡住、总分 ≥ 0。
  进入那一刻的现价记作成本，初始分当场存下（``daban_recommendations``）。同一题材内按分数、再按距涨停排序。
- **「涨停板」tab**：已封住的票按题材分组，龙 1 / 龙 2 打标记置顶，不评分、不记成绩。
- 特征计算与回测共用 ``rec_features``（口径一致），成绩按 H1 回填（见 ``recommend.settle_pending``）。
- 时序留档（``daban_radar_events``）照常记所有候选，给第三层盘中因子攒验证数据；集合竞价（09:15–09:30）也记。

旧的「两派线头 / 名额 / 仓位」与 ``daban_live_hits`` 已停写；旧表保留只读，历史回填照常跑完。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.services.daban.constants import (
    ACTION_ACTIONABLE,
    ACTION_TRIAL,
    ACTION_WATCH,
    STAGE_LABELS,
    load_rules,
)
from src.services.daban.kline import limit_pct
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
# 盘中留档的单飞闸：同一时刻只有一个留档线程在写（见 LiveRadarService._log_radar_async）。
_RADAR_LOG_GATE = threading.Lock()
# 上一轮留档还没写完时，这一轮产生的**一次性**事件（推荐点 / 推翻信号 / 边界改动 / 新进入重点 / 分数变化）先排队，
# 下一轮成功留档时补写。采样、seen 这类可重复的事件仍然直接丢。
_PENDING_LOG_EVENTS: List[Dict[str, Any]] = []
_PENDING_LOG_LOCK = threading.Lock()
_PENDING_LOG_MAX = 400
# 「重点」写入的原子区：页面轮询和后台任务可能同时跑，读已记录 → 写新进入必须串行，否则同一只票会写两遍。
_FOCUS_LOCK = threading.RLock()
# 全市场扫描：(写入时间, 行) 与是否有扫描在跑。
_SCAN_CACHE: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
_SCAN_INFLIGHT = threading.Event()
# 昨日涨停股今日行情（预期、补涨结束要用）：(写入时间, 行情)。
_YQUOTE_CACHE: Dict[str, Tuple[float, Dict[str, Dict[str, Any]]]] = {}
# 指数分时：交易日 → [(HHMMSS, {symbol: 涨跌幅})]，推翻信号「大盘急拉」要看最近 N 分钟的拉升。
_INDEX_SERIES: Dict[str, List[Tuple[str, Dict[str, float]]]] = {}
_INDEX_LAST: Dict[str, Tuple[float, Dict[str, Dict[str, Any]]]] = {}
# 当天已触发的推翻信号（触发后当天不收回）与世界观改动记录：交易日 → 状态。首次用到时从留档恢复。
_WV_STATE: Dict[str, Dict[str, Any]] = {}


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


def _gate(rules: Dict[str, Any], style: str, phase_key: str) -> Optional[str]:
    return ((rules.get("live_gates") or {}).get(style) or {}).get(phase_key)


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
      「切换」）。降级只停发新推荐，已进入「重点」的不动：A 股 T+1 撤不回来，成绩单也必须记当时真实给出的票。
    - 两者都要连续 ``confirm_rounds`` 轮满足才生效，且当日涨停数要到 ``min_zt_sample`` 才开始判。
    """
    cfg = (rules.get("realtime_gate") or {})
    base_keys = {STYLE_CHEN: str(phase.get("chen_stage") or ""),
                 STYLE_KOBE: _late_swap(rules, _advance_fall_day(str(phase.get("kobe_gate") or "")), now)}
    out: Dict[str, Any] = {
        "enabled": bool(cfg.get("enabled", True)),
        "active": False, "upgraded": False, "frozen": False, "market_confirmed": False,
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
        out["market_confirmed"] = True
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
                             f"还在加速封板——资金在换题材不是在撤，不停发新推荐")
        else:
            out["frozen"] = True
            out["reason"] = (f"赚钱效应回落（溢价 {premium_text}、{weak_bits[0]}）且没有题材在接力："
                             f"停发新推荐，已进入「重点」的照常记成绩")
    else:
        out["reason"] = (f"实时重判已开启（当日涨停 {zt} 只）：还需连续 {rounds} 轮确认，"
                         f"当前按前一交易日的周期门控")
    return out


def _hot_themes(
    *,
    blocks: Optional[List[Dict[str, Any]]],
    themes: List[Dict[str, Any]],
    cfg: Dict[str, Any],
    market_size: int,
    prev_blocks: Optional[Dict[str, int]] = None,
    news_lookup: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """今日热点题材：够热 + 在升温 + 有新闻催化。「重点」只在这些题材里推荐，页面上作为筛选 tab。

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
    for index, item in enumerate(hot, start=1):
        item["rank"] = index
    return hot


def _valid_time(value: Any) -> bool:
    """首次封板时间是否有效（``HHMMSS``）。早期版本会把「—」写进来，一律视为无效。"""
    text = str(value or "").strip()
    return len(text) >= 4 and text.isdigit()


def _record_times(record: Dict[str, Any]) -> Tuple[str, str]:
    """留档行的（添加时间, 真实首封时间）。两者都可能为空。

    现在两个时刻分开存：``found_at`` 是雷达第一次看到这只票的时刻，``first_time`` 只记真的封板。
    早期的行没有 ``found_at`` 列，未涨停候选把发现时刻写进了 ``first_time``（这种行 ``gap_pct``
    不为空），所以那种行要读成「有添加时间、没有首封时间」，否则页面会把发现时刻当成首封时间显示。
    """
    found = str(record.get("found_at") or "")
    first = str(record.get("first_time") or "")
    if not found and record.get("gap_pct") is not None:
        found, first = first or str(record.get("seen_at") or ""), ""
    return (found if _valid_time(found) else ""), (first if _valid_time(first) else "")


def _record_anchor(record: Dict[str, Any]) -> str:
    """留档行在当天的时间锚点：有添加时间就用它，否则用首封时间。两者都没有的返回空串。"""
    found, first = _record_times(record)
    return found or first


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

    # ------------------------------------------------------------------ 前一交易日上下文

    def previous_context(self, pool_date: str) -> Dict[str, Any]:
        """``pool_date`` 的**前一交易日**收盘时的周期，是盘中门控唯一允许用的周期信息。

        优先读库里那一天的复盘（完整复盘或冷启动回补都行，两者的周期算法相同）；没有时用最近 3 个交易日的
        涨停 / 炸板 / 跌停池现算。现算结果、以及没有世界观的复盘（冷启动回补）缓存 10 分钟，期间补生成了
        完整复盘会自动切换过去；有世界观的完整复盘当天不会再变，一直缓存。
        """
        from src.services.daban.fetchers import previous_trade_date

        prev = previous_trade_date(pool_date)
        cached = _PREV_CACHE.get(prev)
        if cached and ((cached[1].get("source") == "review" and cached[1].get("worldview"))
                       or time.time() - cached[0] < 600):
            return cached[1]
        context = self._context_from_review(prev, pool_date) or self._compute_previous_context(prev)
        if context.get("available"):
            _PREV_CACHE[prev] = (time.time(), context)
        return context

    def _context_from_review(self, prev: str, pool_date: str) -> Optional[Dict[str, Any]]:
        from src.repositories.daban_repo import decode_payload

        try:
            row = self.repository.snapshot_for_trade_date(prev)
            payload = decode_payload(row)
        except Exception as exc:
            logger.warning("打板王雷达：读取 %s 的复盘失败 %s", prev, exc)
            return None
        if row is not None and bool(getattr(row, "intraday", False)):
            # 那天只在盘中生成过：指标是生成那一刻的（例如 10:30 只有 4 只涨停），不是收盘的周期，不能拿来门控。
            # 改为用那天的涨停 / 炸板 / 跌停池现算。
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
            # 前一交易日盘后生成的世界观（给今天用）；旧版复盘没有这一项。
            "worldview": payload.get("worldview"),
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
                # 接口正常返回、但没有这只票（新股 / 退市）：记成空，当天不再反复查。
                for code in missing:
                    cached.setdefault(code, {})
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

    # ------------------------------------------------------------------ 候选来源

    def _preboard_rows(self, *, now: str, timeout: float, payload: Dict[str, Any], exclude: set) -> List[Dict[str, Any]]:
        """未涨停候选：东财「火箭发射 / 快速反弹」异动（最近 N 分钟）+ 全市场扫描（慢慢涨上来、没触发异动的票）。"""
        from data_provider import limit_up_fetcher as lu

        cfg = self.rules.get("preboard") or {}
        if not cfg.get("enabled", True):
            return []
        window = int(cfg.get("window_min", 10))
        now_min = int(now[:2]) * 60 + int(now[2:4])
        rise_events: Dict[str, Dict[str, Any]] = {}
        feeds, notes = self._changes(("rise", "rebound"), timeout=min(timeout, float(cfg.get("fetch_timeout_sec", 4) or 4)),
                                     reuse_sec=float(cfg.get("reuse_sec", 90) or 0))
        payload["degraded_reasons"].extend(notes)
        for kind in ("rise", "rebound"):
            for event in feeds.get(kind) or []:
                try:
                    hh, mm = int(event["time"][:2] or 0), int(event["time"][2:4] or 0)
                except (TypeError, ValueError):
                    continue
                age = now_min - (hh * 60 + mm)
                # 东财只给时分秒不给日期：开盘前 / 上游延迟时可能回上一交易日的异动，晚于现在的一律丢掉
                # （留 1 分钟给两边时钟误差）。
                if event["code"] in rise_events or event["code"] in exclude or not (-1 <= age <= window):
                    continue
                rise_events[event["code"]] = event
        out: Dict[str, Dict[str, Any]] = {}
        try:
            snap = lu.fetch_tencent_quotes(list(rise_events), timeout=timeout) if rise_events else {}
        except Exception as exc:
            snap = {}
            payload["degraded_reasons"].append(f"候选盘口快照不可用：{str(exc)[:60]}")
        for code, quote in snap.items():
            event = rise_events.get(code) or {}
            row = _preboard_row(quote, found=str(event.get("time") or ""), source="change")
            if row:
                out[code] = row
        scan, scan_note = self._market_scan(now=now, timeout=timeout)
        if scan_note:
            payload["degraded_reasons"].append(scan_note)
        for quote in scan:
            code = quote.get("code")
            if not code or code in out or code in exclude:
                continue
            row = _preboard_row(quote, found=now, source="scan")
            if row:
                out[code] = row
        min_change = float(cfg.get("min_change", 3.0))
        scan_min = float((self.rules.get("market_scan") or {}).get("min_change", 2.0))
        min_price = float(cfg.get("min_price", 2.0))
        max_circ = float(cfg.get("max_circ_mv", 500e8))
        gap_max = float((self.rules.get("rec_score") or {}).get("gap_max", cfg.get("gap_max", 6.0)))
        keep = []
        for row in out.values():
            if not lu.in_universe(row["code"], row.get("name")) or "ST" in str(row.get("name") or "").upper():
                continue
            if row["price"] < min_price or (row.get("circ_mv") and row["circ_mv"] > max_circ):
                continue
            if row["change_pct"] < (min_change if row["source"] == "change" else scan_min):
                continue
            if row["gap_pct"] is None or row["gap_pct"] > gap_max:
                continue
            keep.append(row)
        return keep

    def _market_scan(self, *, now: str, timeout: float) -> Tuple[List[Dict[str, Any]], str]:
        """全市场扫描（腾讯批量行情，约 5200 只 / 9 批）。后台线程跑，快照只读最近一份；离当前超过 3 个间隔就不用。"""
        cfg = self.rules.get("market_scan") or {}
        if not cfg.get("enabled", True):
            return [], ""
        interval = float(cfg.get("interval_sec", 60) or 60)
        cached = _SCAN_CACHE.get("scan")
        age = (time.time() - cached[0]) if cached else None
        if (age is None or age >= interval) and not _SCAN_INFLIGHT.is_set():
            _SCAN_INFLIGHT.set()
            threading.Thread(target=self._run_scan, args=(timeout, int(cfg.get("workers", 4) or 4)),
                             daemon=True, name="daban-scan").start()
        if cached and age is not None and age < interval * 3:
            return cached[1], ""
        return [], "全市场扫描还没有结果（只用拉升异动找候选）" if cached is None else ""

    @staticmethod
    def _run_scan(timeout: float, workers: int) -> None:
        try:
            rows = scan_market(timeout=timeout, workers=workers)
            _SCAN_CACHE["scan"] = (time.time(), rows)
        except Exception as exc:
            logger.warning("打板王雷达：全市场扫描失败 %s", exc)
        finally:
            _SCAN_INFLIGHT.clear()

    def _yesterday_quotes(self, codes: List[str], *, timeout: float) -> Dict[str, Dict[str, Any]]:
        from data_provider import limit_up_fetcher as lu

        cached = _YQUOTE_CACHE.get("y")
        if cached and time.time() - cached[0] < 30:
            return cached[1]
        try:
            quotes = lu.fetch_tencent_quotes(codes, timeout=timeout) if codes else {}
        except Exception as exc:
            logger.warning("打板王雷达：昨日涨停股行情取数失败 %s", exc)
            quotes = {}
        _YQUOTE_CACHE["y"] = (time.time(), quotes)
        return quotes

    def _indices(self, trade_date: str, now: str, *, timeout: float, record: bool) -> Dict[str, Dict[str, Any]]:
        """主要指数快照，30 秒一次；交易时段内追加到当天的分时序列（推翻信号「大盘急拉」用）。"""
        from data_provider import limit_up_fetcher as lu

        cached = _INDEX_LAST.get(trade_date)
        if cached and time.time() - cached[0] < 30:
            return cached[1]
        try:
            quotes = lu.fetch_tencent_index_quotes(timeout=min(timeout, 5.0))
        except Exception as exc:
            logger.debug("打板王雷达：指数行情取数失败 %s", exc)
            return cached[1] if cached else {}
        _INDEX_LAST[trade_date] = (time.time(), quotes)
        if record:
            series = _INDEX_SERIES.setdefault(trade_date, [])
            if not series:
                series.extend(self._restore_index_series(trade_date))
            series.append((now, {s: q["change_pct"] for s, q in quotes.items() if q.get("change_pct") is not None}))
            del series[:-240]
        return quotes

    def _restore_index_series(self, trade_date: str) -> List[Tuple[str, Dict[str, float]]]:
        try:
            events = self.repository.radar_events(trade_date, kinds=["market"])
        except Exception:
            return []
        out = []
        for event in events:
            indices = (event.get("data") or {}).get("indices") or {}
            values = {s: v.get("change_pct") for s, v in indices.items() if v.get("change_pct") is not None}
            if values:
                out.append((str(event.get("at") or ""), values))
        return out[-240:]

    def _wv_state(self, trade_date: str) -> Dict[str, Any]:
        """当天的推翻信号与世界观改动记录。进程重启后从留档恢复（推翻信号当天不收回）。"""
        state = _WV_STATE.get(trade_date)
        if state is not None:
            return state
        for day in list(_WV_STATE):
            _WV_STATE.pop(day, None)
        state = {"overrides": {}, "changes": [], "status": None, "surge": None}
        try:
            for event in self.repository.radar_events(trade_date, kinds=["override", "worldview", "surge"]):
                data = event.get("data") or {}
                if event["kind"] == "surge" and data.get("at") and state["surge"] is None:
                    state["surge"] = data
                elif event["kind"] == "override" and data.get("key"):
                    state["overrides"][data["key"]] = data
                elif event["kind"] == "worldview":
                    state["changes"].append({"at": event.get("at"), **data})
                    state["status"] = data.get("to")
        except Exception as exc:
            logger.debug("打板王雷达：恢复 %s 的世界观状态失败 %s", trade_date, exc)
        _WV_STATE[trade_date] = state
        return state

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
            "generated_at": now, "trade_date": today, "trading": trading, "replay": False, "stale": False,
            "poll_sec": int(getattr(self.config, "daban_live_poll_sec", 10) or 10), "degraded_reasons": [],
            "focus": {"groups": [], "count": 0, "watching": []}, "limit_board": {"groups": [], "count": 0},
            "score_rules": score_rules_view(self.rules),
        }
        try:
            pool = lu.fetch_ths_pool("zt", None, timeout=timeout)
        except Exception as exc:
            payload.update({"ok": False, "error": f"涨停池不可用：{type(exc).__name__}: {str(exc)[:160]}"})
            _CACHE["live"] = (time.time(), payload)
            return payload
        pool_date = pool["date"] or today
        payload["trade_date"] = pool_date
        payload["replay"] = pool_date != today
        payload["stale"] = trading and now >= "093000" and pool_date != today
        if payload["stale"]:
            payload["degraded_reasons"].append(f"交易时段内涨停池仍是 {pool_date} 的数据，上游可能延迟")
        live_now = not payload["replay"]

        rows = [r for r in pool["rows"] if lu.in_universe(r["code"], r.get("name")) and not r.get("is_new")]
        try:
            ladder: Optional[Dict[str, int]] = lu.fetch_ths_ladder(None, timeout=timeout)
        except Exception as exc:
            ladder = None
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
            payload["degraded_reasons"].append(f"炸板池不可用（炸开的票不会进候选）：{str(exc)[:60]}")
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
        preboard = (self._preboard_rows(now=now, timeout=timeout, payload=payload,
                                        exclude={r["code"] for r in [*rows, *broken]}) if live_now else [])

        phase = self.previous_context(pool_date)
        payload["previous"] = {k: v for k, v in phase.items() if k not in ("yesterday_codes", "worldview")}
        payload["events"] = _summarize_events(events, now if live_now else "150000")
        recording = trading and live_now and now >= "093000"
        logging_on = trading and live_now
        if not phase.get("available"):
            payload.update({"ok": True, "note": phase.get("reason"), "worldview": None})
            payload["duration_ms"] = int((time.time() - started) * 1000)
            _CACHE["live"] = (time.time(), payload)
            return payload

        rt: Dict[str, Any] = {"enabled": False, "active": False, "upgraded": False, "frozen": False, "reason": ""}
        if live_now:
            try:
                rt = self._realtime_gate(pool_date, rows=rows, broken=broken, phase=phase, now=now, payload=payload)
            except Exception as exc:
                logger.warning("打板王雷达：实时门控判定失败，按前一交易日的周期 %s", exc)
                payload["degraded_reasons"].append(f"实时周期重判不可用，按前一交易日的周期：{str(exc)[:60]}")
        payload["realtime_gate"] = rt

        blocks = self.blocks() if live_now else []
        themes = build_themes(zt=rows, blocks=list(blocks), history=[], previous=None, yesterday_rows=[],
                              recent_mains=list(phase.get("recent_mains") or []), hot_rank={}, rules=self.rules)["themes"]
        hot = _hot_themes(blocks=blocks, themes=themes, cfg=(self.rules.get("preboard") or {}),
                          market_size=max(1, len(rows) + len(broken)),
                          prev_blocks=self.previous_block_counts(pool_date) if live_now else {},
                          news_lookup=self.theme_news if live_now else None)
        from src.services.daban import recommend as rec
        from src.services.daban import worldview as wv

        book = rec.feature_book(pool_date, repository=self.repository, rules=self.rules) if live_now else None
        if book is not None:
            book.ensure_ctx()
        memory = self.memory(until=pool_date)
        gap_max = float((self.rules.get("rec_score") or {}).get("gap_max", 6.0))
        candidates = focus_candidates(rows=rows, broken=broken, preboard=preboard, quotes=quotes, gap_max=gap_max)
        theme_of, theme_source = self._assign_themes(
            [*rows, *broken, *preboard], hot=hot, memory=memory, blocks=blocks, live_now=live_now)
        for cand in candidates:
            cand["theme_key"] = theme_of.get(cand["code"], "")
            cand["theme_source"] = theme_source.get(cand["code"], "")

        ctx = (book.ctx if book is not None else None) or {}
        nodes = self._nodes(ctx, phase)
        yrows = list(ctx.get("zt1_rows") or [])
        ycodes = [r["code"] for r in yrows] + sorted(ctx.get("zb") or ())
        yquotes = self._yesterday_quotes(ycodes, timeout=timeout) if (live_now and ycodes) else {}
        theme_info = self._theme_info(hot, rows=rows, blocks=blocks, memory=memory, phase=phase, nodes=nodes,
                                      yesterday_rows=yrows, yquotes=yquotes)
        indices = self._indices(pool_date, now, timeout=timeout, record=logging_on) if live_now else {}
        state = self._wv_state(pool_date)
        fresh_overrides: List[Dict[str, Any]] = []
        if live_now and now >= "093000":
            found = wv.detect_overrides(now=now, index_history=_INDEX_SERIES.get(pool_date) or [], rows=rows,
                                        hot_themes=hot, theme_info=theme_info, nodes=nodes, rules=self.rules,
                                        names={s: q.get("name") for s, q in indices.items()})
            for item in found:
                if item["key"] not in state["overrides"]:
                    state["overrides"][item["key"]] = item
                    fresh_overrides.append(item)
        fresh_surge = None
        if live_now and state.get("surge") is None:
            # 大盘急拉推荐点：当天只认第一次触发（指数分时只留最近 240 个点，触发后就记进当天状态和留档，不再重算）。
            found_surge = wv.surge_point(_INDEX_SERIES.get(pool_date) or [], self.rules,
                                         names={s: q.get("name") for s, q in indices.items()})
            if found_surge:
                state["surge"] = fresh_surge = found_surge
        surge = state.get("surge")
        overrides = list(state["overrides"].values())
        surge_now = ({**surge, "active": str(surge.get("at") or "") <= now < str(surge.get("until") or "")}
                     if surge else None)
        boundary = wv.decide_boundary(phase=phase, nodes=nodes, rt=rt, overrides=overrides, rules=self.rules,
                                      surge=surge_now)
        wv_change = None
        if state["status"] != boundary["status"]:
            change = {"at": now, "dim": "boundary", "from": state["status"], "to": boundary["status"],
                      "reasons": boundary["reasons"][:4]}
            if state["status"] is not None or boundary["status"] != "open":
                state["changes"].append(change)
                wv_change = change
            state["status"] = boundary["status"]

        sealed_now = {r["code"] for r in rows if r["code"] not in {c["code"] for c in candidates}}
        opened_now = {c["code"] for c in candidates if c["status"] == "opened"}
        focus_quotes = {**quotes, **{p["code"]: p for p in preboard}}
        if live_now:
            # 已进入「重点」、但这一轮不在任何候选来源里的票（回落到 6% 以外）：单独补行情，实时分和浮盈才算得出来。
            try:
                known = [r["code"] for r in self.repository.recommendations(pool_date)]
            except Exception:
                known = []
            missing = [c for c in known if c not in focus_quotes]
            if missing:
                try:
                    focus_quotes.update(lu.fetch_tencent_quotes(missing, timeout=timeout))
                except Exception as exc:
                    logger.debug("打板王雷达：补「重点」行情失败 %s", exc)
        inserted: List[Dict[str, Any]] = []
        with _FOCUS_LOCK:
            try:
                recorded = {r["code"]: r for r in self.repository.recommendations(pool_date)}
            except Exception as exc:
                logger.warning("打板王雷达：读取当天推荐失败 %s", exc)
                recorded = {}
            focus = rec.build_focus(
                trade_date=pool_date, now=now, candidates=candidates, quotes=focus_quotes,
                sealed_codes=sealed_now, opened_codes=opened_now, hot=hot, boundary=boundary, theme_info=theme_info,
                book=book, recorded=recorded, rules=self.rules, recording=recording, surge=surge)
            if recording:
                try:
                    inserted = self.repository.add_recommendations(pool_date, focus["new_rows"])
                    if focus["live_updates"]:
                        self.repository.update_recommendation_live(pool_date, focus["live_updates"])
                except Exception as exc:
                    logger.warning("打板王雷达：写入「重点」推荐失败 %s", exc)
                    inserted = []
        expectation = (wv.expectation_summary(yrows, yquotes, self.rules, themes=hot)
                       if (yrows and live_now and now >= "092500") else None)
        base_view = phase.get("worldview") or self._fallback_worldview(phase, ctx, nodes, for_date=pool_date)
        payload.update({
            "ok": True,
            "focus": {k: focus[k] for k in ("groups", "count", "pinned", "watching", "threshold", "gap_max")},
            "surge_point": ({**surge_now, "opens_blocked": (boundary.get("pass") or {}).get("kind") == "surge"}
                            if surge_now else None),
            "limit_board": rec.build_limit_board(rows=[r for r in rows if r["code"] in sealed_now], quotes=quotes, hot=hot,
                                                 theme_of=theme_of, book=book,
                                                 recommended={*recorded, *(r["code"] for r in inserted)}),
            "hot_themes": [{**h, "commonality": (theme_info.get(h["key"]) or {}).get("commonality"),
                            "catch_up_end": (theme_info.get(h["key"]) or {}).get("catch_up", {}).get("ended", False)}
                           for h in hot],
            "worldview": wv.live_worldview(base_view, boundary=boundary, overrides=overrides, rt=rt, expectation=expectation,
                                           indices=indices, changes=state["changes"][-20:]),
            "indices": indices,
            "feature_status": book.status() if book is not None else None,
            "stats": {"limit_up": len(rows), "broken": len(broken), "candidates": len(candidates),
                      "hot_candidates": sum(1 for c in candidates if c["theme_key"]), "focus": focus["count"]},
        })
        if fresh_surge:
            self._notify_surge(pool_date, fresh_surge, boundary)
        if inserted:
            self._notify(pool_date, inserted, boundary)
        if logging_on:
            self._log_radar_async(pool_date, now, rows=rows, broken=broken, preboard=preboard, quotes=quotes,
                                  theme_of=theme_of, hot=hot, themes=themes, rt=rt, inserted=inserted,
                                  updates=focus["live_updates"], overrides=fresh_overrides, wv_change=wv_change,
                                  surge=fresh_surge,
                                  auction=(now < "093000"), yquotes=yquotes, recording=recording)
        self._maybe_review(today, now)
        payload["duration_ms"] = int((time.time() - started) * 1000)
        _CACHE["live"] = (time.time(), payload)
        return payload

    def _assign_themes(self, rows: List[Dict[str, Any]], *, hot: List[Dict[str, Any]], memory: Dict[str, set],
                       blocks: List[Dict[str, Any]], live_now: bool) -> Tuple[Dict[str, str], Dict[str, str]]:
        """每只票落在哪个今日热点题材上：当日涨停原因 → 历史涨停原因 → 题材榜 → 所属概念（只对前三级都没贴上的查）。"""
        from src.services.daban.recommend import hot_theme_of

        block_names = _code_blocks(blocks)
        theme_of: Dict[str, str] = {}
        source: Dict[str, str] = {}
        unmatched: List[str] = []
        for row in rows:
            code = row["code"]
            if code in theme_of:
                continue
            key, src = hot_theme_of(code=code, own_tags=split_reason(row.get("reason") or ""), memory=memory,
                                    block_names=block_names, concepts=None, hot=hot)
            theme_of[code], source[code] = key, src
            if not key:
                unmatched.append(code)
        if unmatched and hot and live_now:
            concepts = self.stock_concepts(sorted(set(unmatched))[:300])
            for code in unmatched:
                key, src = hot_theme_of(code=code, own_tags=[], memory={}, block_names={}, concepts=concepts, hot=hot)
                if key:
                    theme_of[code], source[code] = key, src
        return theme_of, source

    @staticmethod
    def _nodes(ctx: Dict[str, Any], phase: Dict[str, Any]) -> Dict[str, Any]:
        """今天开盘前已知的节点。昨日上下文还没准备好时返回空（这一轮按没有节点处理，下一轮补上）。"""
        from src.services.daban.worldview import node_flags

        if not ctx.get("zt1_rows"):
            return {}
        change = {str(r.get("code") or ""): float(r["change_pct"]) for r in ctx.get("previous") or []
                  if r.get("change_pct") is not None}
        return node_flags(d2_rows=ctx.get("zt2_rows") or [], d1_rows=ctx.get("zt1_rows") or [], d1_change=change,
                          fall_day_d1=int((phase.get("kobe_stage") or {}).get("fall_day") or 0))

    def _theme_info(self, hot: List[Dict[str, Any]], *, rows, blocks, memory, phase, nodes, yesterday_rows,
                    yquotes) -> Dict[str, Dict[str, Any]]:
        from src.services.daban import worldview as wv

        info: Dict[str, Dict[str, Any]] = {}
        recent = list(phase.get("recent_mains") or [])
        ok_timing = wv.timing_ok(nodes, self.rules)
        for theme in hot or []:
            name = str(theme.get("name") or theme.get("key") or "")
            members = wv.theme_members(name, rows, blocks)
            commonality = wv.member_commonality(members, memory, recent, self.rules)
            catch = (wv.catch_up_end(name, yesterday_rows=yesterday_rows, quotes=yquotes)
                     if (self.rules.get("worldview") or {}).get("catch_up_end", True) and commonality["kind"] == "same"
                     else {"ended": False, "reason": ""})
            info[theme["key"]] = {"commonality": commonality, "timing_ok": ok_timing, "catch_up": catch}
        return info

    def _fallback_worldview(self, phase: Dict[str, Any], ctx: Dict[str, Any], nodes: Dict[str, Any], *,
                            for_date: str) -> Dict[str, Any]:
        """前一交易日没有盘后世界观（没生成过复盘）时，用门控上下文和昨日题材现拼一份。"""
        from src.services.daban.worldview import build_worldview

        return build_worldview(
            for_date=for_date, based_on=str(phase.get("trade_date") or ""), metrics=phase.get("metrics") or {},
            stage=phase.get("stage") or {"key": phase.get("chen_stage"), "label": phase.get("chen_label")},
            kobe=phase.get("kobe_stage") or {"label": phase.get("kobe_label")}, themes=list(ctx.get("themes") or []),
            theme_info={}, nodes=nodes, zt_rows=list(ctx.get("zt1_rows") or []),
            weak=phase.get("yesterday_weak") or [], rules=self.rules,
            summary="前一交易日没有生成盘后复盘，这一份由门控数据现拼，没有模型解读",
        )

    # ------------------------------------------------------------------ 推送 / 留档 / 回填

    def _notify(self, trade_date: str, rows: List[Dict[str, Any]], boundary: Dict[str, Any]) -> None:
        if not bool(getattr(self.config, "daban_live_push_enabled", False)):
            return
        sent = _NOTIFIED.setdefault(trade_date, set())
        todo = [r for r in rows if r["code"] not in sent]
        if not todo:
            return
        try:
            from src.notification import NotificationBuilder, NotificationService

            notifier = NotificationService()
            for row in todo:
                sent.add(row["code"])
                surge_tag = "【大盘急拉推荐点】" if "surge" in str(row.get("flags") or "").split(",") else ""
                content = NotificationBuilder.build_simple_alert(
                    title=f"打板王雷达 · {surge_tag}新进入重点：{row['code']} {row.get('name') or ''}（{row.get('init_score'):+d} 分）",
                    content=render_focus_alert(row, boundary), alert_type="info",
                )
                notifier.send_with_results(content, route_type="alert", dedup_key=f"daban-focus:{trade_date}:{row['code']}")
        except Exception as exc:
            logger.warning("打板王雷达：推送失败 %s: %s", type(exc).__name__, exc)

    def _notify_surge(self, trade_date: str, surge: Dict[str, Any], boundary: Dict[str, Any]) -> None:
        """大盘急拉推荐点触发时推一次（按交易日去重）。之后窗口里新进入「重点」的票照常推，标题带推荐点标记。"""
        if not bool(getattr(self.config, "daban_live_push_enabled", False)):
            return
        try:
            from src.notification import NotificationBuilder, NotificationService

            until = str(surge.get("until") or "")
            content = NotificationBuilder.build_simple_alert(
                title=f"打板王雷达 · 大盘急拉推荐点 {_fmt_time(surge.get('at'))[:5]}",
                content=(f"{surge.get('label') or ''}\n\n{_fmt_time(surge.get('at'))[:5]} ~ {_fmt_time(until)[:5]} 新进入「重点」的票置顶并推送。"
                         "回测（开放的日子）：触发后 30 分钟内进入的票每笔 H1 +1.81%、胜率 69%（一年约 28 天，样本不大）。"
                         + ("今天前一交易日周期挡住，只在这 30 分钟里放开推荐（未验证：这种日子一年只有 7 天样本）。"
                            if (boundary.get("pass") or {}).get("kind") == "surge" else "不改评分。")),
                alert_type="info",
            )
            NotificationService().send_with_results(content, route_type="alert", dedup_key=f"daban-surge:{trade_date}")
        except Exception as exc:
            logger.warning("打板王雷达：推送大盘急拉推荐点失败 %s: %s", type(exc).__name__, exc)

    def _log_radar_async(self, pool_date: str, now: str, **kwargs) -> None:
        """留档放到后台线程：采样和指数快照要再打一次腾讯行情，不能拖慢页面轮询。

        上一轮还没写完就跳过这一轮的采样与 seen —— 它们按「第一次出现 / 状态变化 / 到点采样」记，
        跳过的变化下一轮照样能补上。但**一次性事件**（推荐点、推翻信号、边界改动、新进入重点、分数变化）
        不会重发：调用方已经更新了内存状态和推荐表，所以这里先把它们排队，下一轮成功留档时补写。
        """
        if not _RADAR_LOG_GATE.acquire(blocking=False):
            self._queue_log_events(now, kwargs)
            return

        def _run() -> None:
            try:
                queued = self._drain_log_events()
                self._log_radar(pool_date, now, queued=queued, **kwargs)
            finally:
                _RADAR_LOG_GATE.release()

        try:
            threading.Thread(target=_run, daemon=True, name="daban-radar-log").start()
        except Exception:
            _RADAR_LOG_GATE.release()
            raise

    @staticmethod
    def _queue_log_events(now: str, kwargs: Dict[str, Any]) -> None:
        """把这一轮的一次性事件存起来（带原始时刻），等下一轮留档补写。"""
        pending = {
            "at": now,
            "inserted": list(kwargs.get("inserted") or []),
            "updates": dict(kwargs.get("updates") or {}),
            "overrides": list(kwargs.get("overrides") or []),
            "wv_change": kwargs.get("wv_change"),
            "surge": kwargs.get("surge"),
        }
        if not (pending["inserted"] or pending["updates"] or pending["overrides"] or pending["wv_change"] or pending["surge"]):
            return
        with _PENDING_LOG_LOCK:
            _PENDING_LOG_EVENTS.append(pending)
            del _PENDING_LOG_EVENTS[:-_PENDING_LOG_MAX]

    @staticmethod
    def _drain_log_events() -> List[Dict[str, Any]]:
        with _PENDING_LOG_LOCK:
            queued = list(_PENDING_LOG_EVENTS)
            _PENDING_LOG_EVENTS.clear()
        return queued

    def _log_radar(self, pool_date: str, now: str, *, rows, broken, preboard, quotes, theme_of, hot, themes, rt,
                   inserted, updates, overrides, wv_change, auction, yquotes, recording, surge=None,
                   queued: Optional[List[Dict[str, Any]]] = None) -> None:
        """把这一轮写进时序留档（``daban_radar_events``）。失败只记日志，不影响快照。"""
        from data_provider import limit_up_fetcher as lu

        from src.services.daban.radar_log import RadarRecorder

        timeout = min(5.0, float(getattr(self.config, "daban_fetch_timeout_sec", 15) or 15))
        try:
            recorder = RadarRecorder(
                self.repository, self.rules,
                fetch_quotes=lambda codes: lu.fetch_tencent_quotes(codes, timeout=timeout),
                fetch_indices=lambda: lu.fetch_tencent_index_quotes(timeout=timeout),
            )
            if auction:
                # 集合竞价：给昨日涨停 / 炸板的票记竞价价格与量（陈小群弱转强「竞价从负转正，量能是前一日同期 3 倍以上」）
                recorder.record_auction(pool_date, now, quotes=yquotes)
            extra: List[Dict[str, Any]] = []
            # 先补写上一轮因为留档忙而排队的一次性事件（时刻用它们自己的 at）。
            for item in queued or []:
                at = str(item.get("at") or now)
                for row in item.get("inserted") or []:
                    extra.append({"at": at, "kind": "focus", "code": row.get("code"), "name": row.get("name"),
                                  "theme": row.get("theme_key"), "price": row.get("cost"),
                                  "gap_pct": row.get("entry_gap_pct"),
                                  "data": {"score": row.get("init_score"), "group": row.get("group_key"),
                                           "items": [i.get("key") for i in (row.get("init_detail") or {}).get("items") or []],
                                           "status": row.get("entry_status")}})
                for code, change_item in (item.get("updates") or {}).items():
                    change = change_item.get("_change") or {}
                    extra.append({"at": at, "kind": "score", "code": code,
                                  "data": {"from": change.get("from"), "to": change.get("to"),
                                           "items": [i.get("key") for i in (change_item.get("live_detail") or {}).get("items") or []]}})
                for override in item.get("overrides") or []:
                    extra.append({"at": at, "kind": "override",
                                  "theme": override.get("scope") if override.get("scope") != "all" else "",
                                  "data": override})
                if item.get("wv_change"):
                    extra.append({"at": at, "kind": "worldview", "data": item["wv_change"]})
                if item.get("surge"):
                    extra.append({"at": at, "kind": "surge", "data": item["surge"]})
            for row in inserted or []:
                extra.append({"at": now, "kind": "focus", "code": row["code"], "name": row.get("name"),
                              "theme": row.get("theme_key"), "price": row.get("cost"), "gap_pct": row.get("entry_gap_pct"),
                              "data": {"score": row.get("init_score"), "group": row.get("group_key"),
                                       "items": [i.get("key") for i in (row.get("init_detail") or {}).get("items") or []],
                                       "status": row.get("entry_status")}})
            for code, item in (updates or {}).items():
                change = item.get("_change") or {}
                extra.append({"at": now, "kind": "score", "code": code,
                              "data": {"from": change.get("from"), "to": change.get("to"),
                                       "items": [i.get("key") for i in (item.get("live_detail") or {}).get("items") or []]}})
            for item in overrides or []:
                extra.append({"at": now, "kind": "override", "theme": item.get("scope") if item.get("scope") != "all" else "",
                              "data": item})
            if wv_change:
                extra.append({"at": now, "kind": "worldview", "data": wv_change})
            if surge:
                extra.append({"at": now, "kind": "surge", "data": surge})
            if not recording:
                extra = [e for e in extra if e["kind"] in ("override", "worldview", "surge")]
            candidates = radar_candidates(
                rows=rows, broken=broken, preboard=preboard, quotes=quotes, theme_of=theme_of,
                gap_max=float((self.rules.get("rec_score") or {}).get("gap_max", 6.0)),
            ) if not auction else []
            metrics = dict((rt or {}).get("metrics") or {})
            metrics.setdefault("zt_count", len(rows))
            metrics.setdefault("zb_count", len(broken))
            recorder.record(
                pool_date, now, candidates=candidates,
                theme_snapshot=radar_theme_snapshot(hot, themes, rows=rows, quotes=quotes) if not auction else None,
                market={**metrics, "frozen": bool((rt or {}).get("frozen")), "upgraded": bool((rt or {}).get("upgraded"))},
                extra=extra,
            )
        except Exception as exc:
            logger.warning("打板王雷达：盘中留档失败 %s: %s", type(exc).__name__, exc)

    def _maybe_review(self, today: str, now: str) -> None:
        """回填「重点」推荐的 H1 成绩（以及旧表 ``daban_live_hits`` 剩下的次日收益）。10 分钟最多一次，后台跑。"""
        last = _REVIEW_CACHE.get("review")
        if last and time.time() - last < 600:
            return
        _REVIEW_CACHE["review"] = time.time()

        def _run() -> None:
            try:
                from src.services.daban.recommend import settle_pending

                settle_pending(self.repository, today=today, now=now)
            except Exception as exc:
                logger.warning("打板王雷达：推荐成绩回填失败 %s", exc)
            try:
                review_pending_live_hits(self.repository, today=today, now=now)
            except Exception as exc:
                logger.warning("打板王雷达：旧雷达标的回填失败 %s", exc)

        threading.Thread(target=_run, daemon=True, name="daban-settle").start()


def review_pending_live_hits(repository, *, today: str, now: str) -> int:
    """把「次日已经收盘」的雷达标的回填次日开盘 / 收盘收益（相对打板价）。返回回填的交易日数。"""
    from src.services.daban.fetchers import fetch_klines, next_trade_date

    done = 0
    for trade_date in repository.pending_live_dates():
        target = next_trade_date(trade_date)
        if target > today or (target == today and now < "150500"):
            continue
        pending = [h for h in repository.live_hits(trade_date)
                   if not h.get("reviewed") and h.get("limit_price") and _record_anchor(h)
                   and h.get("origin") != "preboard"]
        codes = sorted({h["code"] for h in pending})
        if not codes:
            continue
        env = fetch_klines(codes, target, limit=5, timeout=25)
        rows_by_code = (env.get("value") or {}).get("rows") or {}
        # 按 (打法, 代码) 回填：两派可能锁定同一只票，但锁定时刻不同、成本不同，按代码会互相覆盖。
        outcomes: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for hit in pending:
            rows = rows_by_code.get(hit["code"]) or []
            bar = next((r for r in rows if str(r.get("time") or "").replace("-", "") == target.replace("-", "")), None)
            if not bar:
                continue
            # 成本：锁定那一刻的现价（当时真能买到的价）；老记录没有时退回打板价。
            cost = hit.get("seen_price") or hit["limit_price"]
            buy = float(cost)
            outcomes[(str(hit.get("style") or ""), hit["code"])] = {
                "open_ret": round((bar["open"] / buy - 1) * 100, 2),
                "close_ret": round((bar["close"] / buy - 1) * 100, 2),
            }
        if outcomes:
            repository.update_live_outcomes(trade_date, outcomes)
            done += 1
    return done


# ---------------------------------------------------------------------- 候选与「重点」的模块级工具


def _preboard_row(quote: Dict[str, Any], *, found: str, source: str) -> Optional[Dict[str, Any]]:
    """腾讯快照 → 未涨停候选行。已经到涨停价的不算（那是封板，进涨停池）。"""
    price, prev_close, limit_up = quote.get("price"), quote.get("prev_close"), quote.get("limit_up")
    if not price or not prev_close or not limit_up or price >= limit_up - 1e-6:
        return None
    return {
        "code": quote["code"], "name": quote.get("name") or "", "price": price, "limit_up": limit_up,
        "prev_close": prev_close, "open": quote.get("open"), "high": quote.get("high"),
        "change_pct": round((price / prev_close - 1) * 100, 2), "gap_pct": round((limit_up / price - 1) * 100, 2),
        "circ_mv": quote.get("circ_mv"), "volume_ratio": quote.get("volume_ratio"),
        "turnover_rate": quote.get("turnover_rate"), "sell1_volume": quote.get("sell1_volume"),
        "volume": quote.get("volume"), "time": found, "source": source,
    }


def universe_codes() -> List[str]:
    """沪深主板 + 创业板 + 科创板的股票代码（来自本地股票索引，不含北交所、指数、基金）。"""
    import re

    from src.data.stock_index_loader import get_stock_name_index_map

    out = []
    for key in get_stock_name_index_map():
        text = str(key)
        if re.fullmatch(r"\d{6}\.(SH|SZ)", text):
            code = text[:6]
            if code.startswith(("60", "68", "00", "30")):
                out.append(code)
    return sorted(set(out))


def scan_market(*, timeout: float, workers: int = 4, codes: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """全市场腾讯快照（每批 600 只，并发 ``workers`` 批）。只返回当天涨幅 ≥ 0 的行，减少下游计算。"""
    from concurrent.futures import ThreadPoolExecutor

    from data_provider import limit_up_fetcher as lu

    codes = codes if codes is not None else universe_codes()
    batches = [codes[i:i + 600] for i in range(0, len(codes), 600)]
    out: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="daban-scan") as pool:
        for quotes in pool.map(lambda batch: lu.fetch_tencent_quotes(batch, timeout=timeout), batches):
            for quote in (quotes or {}).values():
                if quote.get("price") and quote.get("prev_close") and quote["price"] >= quote["prev_close"]:
                    out.append(quote)
    return out


def focus_candidates(*, rows: List[Dict[str, Any]], broken: List[Dict[str, Any]], preboard: List[Dict[str, Any]],
                     quotes: Dict[str, Dict[str, Any]], gap_max: float) -> List[Dict[str, Any]]:
    """用户还买得进的票：没封板且距涨停 ≤ ``gap_max``，或封过板后炸开、现价低于涨停价。

    涨停池里的票如果盘口显示现价已低于涨停价（池子还没来得及把它挪去炸板池），也按炸开处理。
    """
    out: Dict[str, Dict[str, Any]] = {}
    for row in [*rows, *broken]:
        code = row["code"]
        quote = quotes.get(code) or {}
        price, limit_up = quote.get("price"), quote.get("limit_up") or limit_price_of(row, quote)
        if not price or not limit_up or price >= limit_up - 1e-6:
            continue
        out[code] = {
            "code": code, "name": row.get("name") or quote.get("name") or "", "status": "opened", "price": price,
            "limit_up": limit_up, "prev_close": quote.get("prev_close"), "open": quote.get("open"),
            "change_pct": round((price / quote["prev_close"] - 1) * 100, 2) if quote.get("prev_close") else row.get("change_pct"),
            "first_time": row.get("first_time") or "", "open_num": row.get("open_num"), "reason": row.get("reason") or "",
        }
    for row in preboard:
        if row["code"] in out or row.get("gap_pct") is None or row["gap_pct"] > gap_max:
            continue
        out[row["code"]] = {**row, "status": "preboard"}
    return list(out.values())


def score_rules_view(rules: Dict[str, Any]) -> Dict[str, Any]:
    """计分表（页面展示用，与算分用的是同一份规则）。"""
    from src.services.daban.rec_features import score_range

    table = rules.get("rec_score") or {}
    lo, hi = score_range(table)
    return {"threshold": table.get("threshold", 0), "gap_max": table.get("gap_max", 6.0), "range": [lo, hi],
            "items": table.get("items") or [], "observe": table.get("observe") or [],
            "observe_text": table.get("observe_text") or "", "baseline": table.get("baseline") or {}}


def render_focus_alert(row: Dict[str, Any], boundary: Optional[Dict[str, Any]] = None) -> str:
    detail = row.get("init_detail") or {}
    status = "炸开后回落" if row.get("entry_status") == "opened" else "还没封板"
    lines = [
        f"**{row.get('theme_name') or row.get('theme_key') or '—'}｜总分 {row.get('init_score'):+d}｜{status}**",
        f"进入 {_fmt_time(row.get('entered_at') or '')}，现价 {row.get('cost')}（记作成本），距涨停 {row.get('entry_gap_pct')}%",
    ]
    for item in detail.get("items") or []:
        lines.append(f"- {item.get('points'):+d} {item.get('label')}（{item.get('source')}）")
    if detail.get("observe"):
        lines.append(f"- 观察期：{detail['observe'].get('label')}，按科比原文不扣分，成绩单独统计")
    temper = detail.get("temper") or {}
    if temper.get("approaches"):
        from src.services.daban.rec_features import TEMPER_LABELS, temper_text

        lines.append(f"- 股性 {TEMPER_LABELS.get(temper.get('tag'), '')}：{temper_text(temper)}")
    if boundary and boundary.get("pass"):
        lines.append(f"- {boundary['pass'].get('label')}")
    lines.append("- 成绩按 H1 记：当天封住按科比 #25 不涨停就收盘卖、涨停续拿；没封住次日高开开盘卖、低开收盘卖")
    lines.append("> 雷达只做提醒，不构成投资建议。")
    return "\n".join(lines)


def radar_candidates(
    *,
    rows: List[Dict[str, Any]],
    broken: List[Dict[str, Any]],
    preboard: List[Dict[str, Any]],
    quotes: Dict[str, Dict[str, Any]],
    theme_of: Dict[str, str],
    gap_max: float,
) -> List[Dict[str, Any]]:
    """盘中留档的候选口径：涨停池 + 炸板池 + 距涨停 ≤ ``gap_max`` 的未涨停候选，**不看边界和分数**。

    冰点、退潮这些「管住手」的日子也要有样本，之后才能验证要不要放行。``theme_key`` 为空 = 不在今日热点题材里。
    """
    from src.services.daban.radar_log import STATUS_CANDIDATE, STATUS_OPENED, STATUS_SEALED

    out: Dict[str, Dict[str, Any]] = {}
    for status, source in ((STATUS_SEALED, rows), (STATUS_OPENED, broken)):
        for row in source:
            code = str(row.get("code") or "")
            if not code or code in out:
                continue
            quote = quotes.get(code) or {}
            prev_close = quote.get("prev_close")
            out[code] = {
                "code": code, "name": row.get("name") or quote.get("name") or "", "status": status,
                "price": quote.get("price") or row.get("price"), "limit_up": limit_price_of(row, quote),
                "prev_close": prev_close, "change_pct": row.get("change_pct"),
                "turnover_rate": row.get("turnover_rate"), "volume_ratio": quote.get("volume_ratio"),
                "circ_mv": row.get("circ_mv"), "sell1_volume": quote.get("sell1_volume"),
                "open_pct": (round((quote["open"] / prev_close - 1) * 100, 3)
                             if quote.get("open") and prev_close else None),
                "first_time": row.get("first_time") or "", "open_num": row.get("open_num"),
                "seal_amount": row.get("seal_amount") if status == STATUS_SEALED else None,
                "boards": row.get("boards"), "theme_key": theme_of.get(code, ""),
            }
    for row in preboard:
        code = str(row.get("code") or "")
        gap = row.get("gap_pct")
        if gap is None and row.get("limit_up") and row.get("price"):
            gap = (row["limit_up"] / row["price"] - 1) * 100
        if not code or code in out or gap is None or gap > gap_max:
            continue
        prev_close = row.get("prev_close")
        out[code] = {
            "code": code, "name": row.get("name") or "", "status": STATUS_CANDIDATE,
            "price": row.get("price"), "limit_up": row.get("limit_up"), "prev_close": prev_close,
            "change_pct": row.get("change_pct"), "turnover_rate": row.get("turnover_rate"),
            "volume_ratio": row.get("volume_ratio"), "circ_mv": row.get("circ_mv"),
            "sell1_volume": row.get("sell1_volume"), "source": row.get("source"),
            "open_pct": (round((row["open"] / prev_close - 1) * 100, 3) if row.get("open") and prev_close else None),
            "first_time": "", "theme_key": theme_of.get(code, ""),
        }
    return list(out.values())


def radar_theme_snapshot(
    hot_themes: List[Dict[str, Any]],
    themes: List[Dict[str, Any]],
    *,
    rows: List[Dict[str, Any]],
    quotes: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """热点题材与各题材龙 1 / 龙 2 的当下状态。龙头排序与盘后一致（板数 → N 天 M 板 → 首封时间 → 人气）。"""
    from src.services.daban.radar_log import quote_sealed

    by_code = {str(r.get("code") or ""): r for r in rows}

    def _leader(member: Dict[str, Any]) -> Dict[str, Any]:
        row = by_code.get(member.get("code") or "") or {}
        quote = quotes.get(member.get("code") or "") or {}
        return {
            "code": member.get("code"), "boards": member.get("boards"), "first_time": member.get("first_time"),
            "open_num": row.get("open_num"), "seal_amount": row.get("seal_amount"),
            "sealed": quote_sealed(quote) if quote else None,
        }

    return {
        "hot": [{k: t.get(k) for k in ("key", "rank", "zt_count", "days", "share", "news_count")}
                for t in hot_themes],
        "themes": [
            {"key": t.get("key"), "rank": t.get("rank"), "zt_count": t.get("zt_count"),
             "max_boards": t.get("max_boards"), "lianban_count": t.get("lianban_count"),
             "leaders": [_leader(m) for m in (t.get("members") or [])[:2]]}
            for t in themes
        ],
    }


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
    """交易时段后台轮询（不开页面也评分、留档）。

    开关：雷达开着（``DABAN_LIVE_ENABLED``），且打开了打板王自动运行（``DABAN_AUTO_GENERATE``）或雷达推送
    （``DABAN_LIVE_PUSH_ENABLED``）。推送本身仍只看 ``DABAN_LIVE_PUSH_ENABLED``，见 :meth:`LiveRadarService._notify`。
    不依赖 ``SCHEDULE_ENABLED``（见 ``RuntimeSchedulerService._standalone_background_tasks``）。
    """
    if not bool(getattr(config, "daban_enabled", True)) or not bool(getattr(config, "daban_live_enabled", True)):
        return []
    if not (bool(getattr(config, "daban_auto_generate", False))
            or bool(getattr(config, "daban_live_push_enabled", False))):
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
    for cache in (_CACHE, _BLOCK_CACHE, _CHANGE_CACHE, _CYCLE_CACHE, _HISTORY_CACHE, _RT_STATE, _PREV_BLOCK_CACHE,
                  _NEWS_CACHE, _CONCEPT_CACHE, _MEMORY_CACHE, _PREV_CACHE, _REVIEW_CACHE, _NOTIFIED, _SCAN_CACHE,
                  _YQUOTE_CACHE, _INDEX_SERIES, _INDEX_LAST, _WV_STATE):
        cache.clear()
    _NEWS_INFLIGHT.clear()
    _SCAN_INFLIGHT.clear()
    with _PENDING_LOG_LOCK:
        _PENDING_LOG_EVENTS.clear()
    from src.services.daban.radar_log import clear_state
    from src.services.daban.recommend import clear_books

    clear_state()
    clear_books()


__all__ = [
    "LIVE_TASK_NAME", "LIVE_TASK_SECONDS", "LiveRadarService", "build_live_background_tasks", "clear_cache",
    "decide_realtime_gate", "focus_candidates", "is_trading_day", "limit_price_of", "market_open_now",
    "radar_candidates", "radar_theme_snapshot", "render_focus_alert", "review_pending_live_hits", "scan_market",
    "score_rules_view", "universe_codes", "weak_codes",
]
