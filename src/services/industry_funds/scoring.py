# -*- coding: utf-8 -*-
"""行业资金模块的打分层。

两条独立打分线：

- :func:`score_market` —— 大盘复合分（0-100）。五个因子各自归一化到 0-20 分后加权求和。
  每个因子都返回「原始值 / 归一化依据 / 得分 / 权重 / 来源 / 数据时间」，这是"完全
  可解释"的落点：用户能逐项看出分数是怎么来的，而不是接受一个黑箱标量。
- :func:`score_industries` —— 同花顺 90 个行业各自的得分（0-100）。用**横截面分位**
  （该值在 90 个行业里的排名）而不是历史分位，因为同花顺行业资金流只有当日与 3/5/10/20
  日排行快照，没有可用的长历史序列。

另外 :func:`build_stock_candidates` 负责候选池的三路合并。

**打分不调用模型**。LLM 只在下游把这里算好的结果写成文字，见 ``analyzer.py``。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

from src.services.industry_funds.constants import (
    ETF_DELTA_FALLBACK_SCALE_YI,
    ETF_DELTA_PERCENTILE_MIN_POINTS,
    FACTOR_MAX_SCORE,
    INDUSTRY_FACTOR_LABELS,
    INDUSTRY_FACTOR_NORMALIZATION,
    INDUSTRY_FACTOR_WEIGHTS,
    MARKET_BEARISH_THRESHOLD,
    MARKET_BULLISH_THRESHOLD,
    MARKET_FACTOR_DIRECTION,
    MARKET_FACTOR_LABELS,
    MARKET_FACTOR_NORMALIZATION,
    MARKET_FACTOR_WEIGHTS,
)

logger = logging.getLogger(__name__)

YI = 1e8


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _clamp(value: float, low: float = 0.0, high: float = FACTOR_MAX_SCORE) -> float:
    return max(low, min(high, value))


def _percentile_rank(series: Sequence[float], value: float) -> Optional[float]:
    """value 在 series 中的分位（0-1）。平局按半数计，与 fetchers 侧口径一致。"""
    cleaned = [float(x) for x in series if x is not None and float(x) == float(x)]
    if not cleaned or value is None:
        return None
    below = sum(1 for x in cleaned if x < value)
    equal = sum(1 for x in cleaned if x == value)
    return (below + 0.5 * equal) / len(cleaned)


def _fmt_yi(value: Optional[float], digits: int = 2) -> str:
    """把「元」格式化成「亿元」的展示串。"""
    if value is None:
        return "—"
    return f"{value / YI:,.{digits}f} 亿元"


def _fmt_pct(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.{digits}f}%"


def _factor(
    key: str,
    *,
    raw_display: str,
    basis: str,
    score: Optional[float],
    source: str,
    as_of: str,
    detail: Optional[Dict[str, Any]] = None,
    error: str = "",
) -> Dict[str, Any]:
    """构造单个因子的可解释结果。``score is None`` 表示该因子本轮不可用。"""
    return {
        "key": key,
        "label": MARKET_FACTOR_LABELS.get(key, key),
        "weight": MARKET_FACTOR_WEIGHTS.get(key, 0.0),
        "direction": MARKET_FACTOR_DIRECTION.get(key, ""),
        "normalization": MARKET_FACTOR_NORMALIZATION.get(key, ""),
        "raw_display": raw_display,
        "basis": basis,
        "score": None if score is None else round(_clamp(score), 2),
        "max_score": FACTOR_MAX_SCORE,
        "available": score is not None,
        "source": source,
        "as_of": as_of,
        "error": error,
        "detail": detail or {},
    }


# ---------------------------------------------------------------------------
# 大盘五因子
# ---------------------------------------------------------------------------


def _score_industry_flow(fetched: Dict[str, Any]) -> Dict[str, Any]:
    entry = fetched.get("industry_fund_flow") or {}
    value = entry.get("value") or {}
    ratio = value.get("inflow_ratio")
    if ratio is None:
        return _factor(
            "industry_fund_flow",
            raw_display="—",
            basis="数据不可用",
            score=None,
            source=entry.get("source", ""),
            as_of=entry.get("as_of", ""),
            error=entry.get("error") or "行业资金流不可用",
        )

    # 线性映射：净流入行业占比 50% 记 10 分（中性），100% 记 20 分，0% 记 0 分。
    # 之所以用"家数占比"而不是"净额合计"，是因为净额总量受少数权重股影响极大，
    # 而家数占比天然落在 0-1，不需要额外的历史标定就能跨时间比较。
    score = _clamp(float(ratio) * FACTOR_MAX_SCORE)
    return _factor(
        "industry_fund_flow",
        raw_display=f"净流入 {value.get('inflow_count')} / {value.get('industry_count')} 个行业"
        f"（{_fmt_pct(ratio, 1)}）",
        basis=f"占比 {_fmt_pct(ratio, 1)} × 满分 20；50% 即中性",
        score=score,
        source=entry.get("source", ""),
        as_of=entry.get("as_of", ""),
        detail={
            "net_sum_yi": None if value.get("net_sum") is None else round(value["net_sum"], 2),
            "net_median_yi": None if value.get("net_median") is None else round(value["net_median"], 2),
            "inflow_count": value.get("inflow_count"),
            "industry_count": value.get("industry_count"),
        },
        error=entry.get("degraded_reason", "") if entry.get("degraded") else "",
    )


def _score_lhb(fetched: Dict[str, Any]) -> Dict[str, Any]:
    entry = fetched.get("lhb_institution") or {}
    value = entry.get("value") or {}
    percentile = value.get("percentile")
    if percentile is None:
        return _factor(
            "lhb_institution",
            raw_display="—",
            basis="数据不可用",
            score=None,
            source=entry.get("source", ""),
            as_of=entry.get("as_of", ""),
            error=entry.get("error") or "龙虎榜机构数据不可用",
        )

    score = _clamp(float(percentile) * FACTOR_MAX_SCORE)
    return _factor(
        "lhb_institution",
        raw_display=f"近 5 日机构净买 {_fmt_yi(value.get('rolling_5d_net'))}"
        f"（当日 {_fmt_yi(value.get('latest_net'))}）",
        basis=f"在 {value.get('history_days')} 个交易日滚动序列中处于 {_fmt_pct(percentile, 1)} 分位",
        score=score,
        source=entry.get("source", ""),
        as_of=entry.get("as_of", ""),
        detail={
            "latest_net_yi": None if value.get("latest_net") is None else round(value["latest_net"] / YI, 4),
            "rolling_5d_net_yi": None if value.get("rolling_5d_net") is None else round(value["rolling_5d_net"] / YI, 4),
            "history_days": value.get("history_days"),
            "stock_count": len(value.get("stocks") or []),
        },
    )


def _score_etf_share(fetched: Dict[str, Any], delta_history: Sequence[float]) -> Dict[str, Any]:
    entry = fetched.get("etf_share") or {}
    value = entry.get("value") or {}
    delta = value.get("combined_delta")
    if delta is None:
        return _factor(
            "etf_share",
            raw_display="—",
            basis="数据不可用",
            score=None,
            source=entry.get("source", ""),
            as_of=entry.get("as_of", ""),
            error=entry.get("error") or "ETF 份额变动不可用",
        )

    delta_yi = delta / YI
    score: Optional[float] = None
    basis = ""
    method = ""

    # 本地快照攒够点后切历史分位，否则退回一个有明确说明的线性标尺。
    # 先做分位是为了避免"标尺"这种人造阈值长期留在结果里。
    usable_history = [float(x) for x in delta_history if x is not None]
    if len(usable_history) >= ETF_DELTA_PERCENTILE_MIN_POINTS:
        percentile = _percentile_rank(usable_history, delta)
        if percentile is not None:
            score = _clamp(percentile * FACTOR_MAX_SCORE)
            basis = f"在本地累积的 {len(usable_history)} 期 5 日变动中处于 {_fmt_pct(percentile, 1)} 分位"
            method = "percentile"
    if score is None:
        ratio = max(-1.0, min(1.0, delta_yi / ETF_DELTA_FALLBACK_SCALE_YI))
        score = _clamp(FACTOR_MAX_SCORE / 2 + (FACTOR_MAX_SCORE / 2) * ratio)
        basis = (
            f"±{ETF_DELTA_FALLBACK_SCALE_YI:g} 亿份按满量程线性映射（当前 {delta_yi:+,.2f} 亿份）；"
            f"本地快照满 {ETF_DELTA_PERCENTILE_MIN_POINTS} 期后自动改用历史分位"
        )
        method = "fallback_scale"

    return _factor(
        "etf_share",
        raw_display=f"宽基 ETF 份额 5 日净增 {delta_yi:+,.2f} 亿份（口径：{value.get('coverage', '')}）",
        basis=basis,
        score=score,
        source=entry.get("source", ""),
        as_of=entry.get("as_of", ""),
        detail={
            "combined_delta_yi": round(delta_yi, 4),
            "sse_delta_yi": None if (value.get("sse") or {}).get("delta") is None
            else round(value["sse"]["delta"] / YI, 4),
            "szse_delta_yi": None if (value.get("szse") or {}).get("delta") is None
            else round(value["szse"]["delta"] / YI, 4),
            "baseline_date": value.get("baseline_date"),
            "method": method,
            "delta_top": (value.get("delta_top") or [])[:8],
            "history_points": len(usable_history),
        },
        error=entry.get("degraded_reason", "") if entry.get("degraded") else "",
    )


def _score_margin(fetched: Dict[str, Any]) -> Dict[str, Any]:
    entry = fetched.get("margin") or {}
    value = entry.get("value") or {}
    change = value.get("change_5d")
    percentile = value.get("percentile")
    if change is None or percentile is None:
        return _factor(
            "margin",
            raw_display="—",
            basis="数据不可用",
            score=None,
            source=entry.get("source", ""),
            as_of=entry.get("as_of", ""),
            error=entry.get("error") or entry.get("degraded_reason") or "两融数据不可用",
        )

    score = _clamp(float(percentile) * FACTOR_MAX_SCORE)
    return _factor(
        "margin",
        raw_display=f"两融余额 {value.get('financing_balance'):,.0f} 亿元，5 日变化 {change * 100:+.2f}%",
        basis=f"5 日变化率在 {value.get('history_points')} 个交易日历史中处于 {_fmt_pct(percentile, 1)} 分位",
        score=score,
        source=entry.get("source", ""),
        as_of=entry.get("as_of", ""),
        detail={
            "financing_balance_yi": value.get("financing_balance"),
            "change_5d": change,
            "history_points": value.get("history_points"),
        },
    )


def _score_valuation(fetched: Dict[str, Any]) -> Dict[str, Any]:
    entry = fetched.get("valuation") or {}
    value = entry.get("value") or {}
    percentile = value.get("combined_percentile")
    if percentile is None:
        return _factor(
            "valuation",
            raw_display="—",
            basis="数据不可用",
            score=None,
            source=entry.get("source", ""),
            as_of=entry.get("as_of", ""),
            error=entry.get("error") or "估值分位不可用",
        )

    # 估值是唯一的**反向**因子：分位越低（越便宜）得分越高。
    score = _clamp((1.0 - float(percentile)) * FACTOR_MAX_SCORE)
    return _factor(
        "valuation",
        raw_display=(
            f"沪深300 PE {value.get('hs300_pe')}（分位 {_fmt_pct(value.get('hs300_pe_percentile'), 1)}）"
            f"｜全A PB {value.get('all_a_pb')}（分位 {_fmt_pct(value.get('all_a_pb_percentile'), 1)}）"
        ),
        basis=f"综合分位 {_fmt_pct(percentile, 1)} 取反向 → (1 − {percentile:.3f}) × 20",
        score=score,
        source=entry.get("source", ""),
        as_of=entry.get("as_of", ""),
        detail={
            "combined_percentile": percentile,
            "hs300_pe": value.get("hs300_pe"),
            "hs300_pe_percentile": value.get("hs300_pe_percentile"),
            "all_a_pb": value.get("all_a_pb"),
            "all_a_pb_percentile": value.get("all_a_pb_percentile"),
            "reversed": True,
        },
        error=entry.get("degraded_reason", "") if entry.get("degraded") else "",
    )


def _verdict(score: float) -> tuple[str, str]:
    if score >= MARKET_BULLISH_THRESHOLD:
        return "bullish", "偏多"
    if score < MARKET_BEARISH_THRESHOLD:
        return "bearish", "偏空"
    return "neutral", "中性"


def score_market(
    fetched: Dict[str, Any], *, etf_delta_history: Sequence[float] = ()
) -> Dict[str, Any]:
    """大盘复合分。返回 ``{score, verdict, verdict_label, factors, weights, warnings, degraded}``。

    某个因子不可用时，**按剩余可用因子的权重重新归一**，而不是把它当 0 分算进去 ——
    后者会让一次抓取失败直接变成一条看空结论。重新归一的事实会记在 ``renormalized`` 里。
    """
    factors = [
        _score_industry_flow(fetched),
        _score_lhb(fetched),
        _score_etf_share(fetched, etf_delta_history),
        _score_margin(fetched),
        _score_valuation(fetched),
    ]

    available = [f for f in factors if f["available"]]
    warnings: List[str] = []
    renormalized = False

    if not available:
        return {
            "score": None,
            "verdict": "unknown",
            "verdict_label": "无法判断",
            "factors": factors,
            "available_weight": 0.0,
            "renormalized": False,
            "coverage": 0.0,
            "warnings": ["五个因子的数据源本轮全部不可用，无法给出复合信号"],
            "degraded": True,
            "degraded_reason": "所有数据源均不可用",
        }

    total_weight = sum(f["weight"] for f in available)
    if total_weight <= 0:
        return {
            "score": None,
            "verdict": "unknown",
            "verdict_label": "无法判断",
            "factors": factors,
            "available_weight": 0.0,
            "renormalized": False,
            "coverage": 0.0,
            "warnings": ["可用因子权重合计为 0，无法给出复合信号"],
            "degraded": True,
            "degraded_reason": "可用因子权重异常",
        }

    missing = [f for f in factors if not f["available"]]
    if missing:
        renormalized = True
        for f in missing:
            warnings.append(f"{f['label']}不可用（{f['error'] or '未取到数据'}），其权重已按比例分摊到其余因子")

    composite = 0.0
    # 因子分是 0-20 分制，加权求和后仍是 0-20，必须再放大到 0-100 ——
    # 否则下面的偏多/偏空阈值（65/45）就是一套对不上的刻度。
    scale = 100.0 / FACTOR_MAX_SCORE
    for f in available:
        effective_weight = f["weight"] / total_weight
        f["effective_weight"] = round(effective_weight, 4)
        composite += f["score"] * effective_weight * scale
    for f in missing:
        f["effective_weight"] = 0.0

    composite = _clamp(composite, 0.0, 100.0)
    verdict, verdict_label = _verdict(composite)

    degraded_factors = [f for f in factors if f.get("error")]
    for f in degraded_factors:
        if f["available"]:
            warnings.append(f"{f['label']}口径不完整：{f['error']}")

    return {
        "score": round(composite, 2),
        "verdict": verdict,
        "verdict_label": verdict_label,
        "factors": factors,
        # 可用因子的原始权重合计：100% 表示五因子齐全，越低说明结论越依赖少数数据源。
        "available_weight": round(total_weight, 4),
        "coverage": round(total_weight, 4),
        "renormalized": renormalized,
        "thresholds": {
            "bullish": MARKET_BULLISH_THRESHOLD,
            "bearish": MARKET_BEARISH_THRESHOLD,
        },
        "warnings": warnings,
        "degraded": bool(missing) or bool(degraded_factors),
        "degraded_reason": "；".join(warnings),
    }


# ---------------------------------------------------------------------------
# 行业分
# ---------------------------------------------------------------------------


def _cross_section_scores(values: List[Optional[float]]) -> List[Optional[float]]:
    """把一组值映射成 0-1 的横截面分位（在组内的相对位置）。

    用横截面而不是时间序列，是因为同花顺行业资金流没有可用的长历史 —— 只有当日和
    3/5/10/20 日排行快照。行业之间横向比"今天谁更受资金青睐"正是这个表要回答的问题。
    """
    present = [v for v in values if v is not None]
    if len(present) < 2:
        return [None for _ in values]
    ordered = sorted(present)
    n = len(ordered)
    out: List[Optional[float]] = []
    for value in values:
        if value is None:
            out.append(None)
            continue
        below = sum(1 for x in ordered if x < value)
        equal = sum(1 for x in ordered if x == value)
        out.append((below + 0.5 * equal) / n)
    return out


def score_industries(fetched: Dict[str, Any]) -> Dict[str, Any]:
    """给同花顺 90 个行业各打一个 0-100 分，按分排序返回。

    四个子因子全部是**该行业自身的资金流字段**（用户选定同花顺分类口径后，这个分类
    拿不到成分股与行业 PE，所以行业分里没有龙虎榜与估值因子 —— 这是该口径下的固有
    限制，已在 ``INDUSTRY_FACTOR_NORMALIZATION`` 与页面说明里如实标注）。
    """
    entry = fetched.get("industry_fund_flow") or {}
    industries: List[Dict[str, Any]] = list((entry.get("value") or {}).get("industries") or [])
    if not industries:
        return {
            "industries": [],
            "weights": INDUSTRY_FACTOR_WEIGHTS,
            "degraded": True,
            "degraded_reason": entry.get("error") or "行业资金流不可用，无法计算行业分",
            "warnings": [entry.get("error") or "行业资金流不可用"],
        }

    net_today = [row.get("net") for row in industries]
    net_5d = [row.get("net_5d") for row in industries if row.get("net_5d") is not None]
    change_pct = [row.get("change_pct") for row in industries]
    leader_pct = [row.get("leader_change_pct") for row in industries]

    pct_today = _cross_section_scores(net_today)
    pct_5d = _cross_section_scores([row.get("net_5d") for row in industries])
    pct_change = _cross_section_scores(change_pct)
    pct_leader = _cross_section_scores(leader_pct)

    has_5d = bool(net_5d)
    weights = dict(INDUSTRY_FACTOR_WEIGHTS)
    if not has_5d:
        # 没有 5 日数据时把它的权重按比例分给其余三项，别让它变成 0 分拖低所有行业。
        redistributed = weights.pop("net_5d")
        remaining = sum(weights.values())
        if remaining > 0:
            for key in list(weights.keys()):
                weights[key] = weights[key] + redistributed * (weights[key] / remaining)
        else:
            weights = {"net_today": 1.0}

    scored: List[Dict[str, Any]] = []
    for idx, row in enumerate(industries):
        components = {
            "net_today": pct_today[idx],
            "net_5d": pct_5d[idx],
            "change_pct": pct_change[idx],
            "breadth": pct_leader[idx],
        }
        total = 0.0
        used_weight = 0.0
        parts: List[Dict[str, Any]] = []
        for key, weight in weights.items():
            component = components.get(key)
            if component is None:
                parts.append(
                    {
                        "key": key,
                        "label": INDUSTRY_FACTOR_LABELS.get(key, key),
                        "weight": round(weight, 4),
                        "percentile": None,
                        "score": None,
                        "normalization": INDUSTRY_FACTOR_NORMALIZATION.get(key, ""),
                    }
                )
                continue
            used_weight += weight
            total += component * 100.0 * weight
            parts.append(
                {
                    "key": key,
                    "label": INDUSTRY_FACTOR_LABELS.get(key, key),
                    "weight": round(weight, 4),
                    "percentile": round(component, 4),
                    "score": round(component * 100.0, 2),
                    "normalization": INDUSTRY_FACTOR_NORMALIZATION.get(key, ""),
                }
            )

        score = (total / used_weight) if used_weight > 0 else None
        scored.append(
            {
                **row,
                "score": None if score is None else round(_clamp(score, 0.0, 100.0), 2),
                "components": parts,
            }
        )

    scored.sort(key=lambda r: r.get("score") if r.get("score") is not None else -1.0, reverse=True)
    for rank, row in enumerate(scored, start=1):
        row["rank"] = rank

    return {
        "industries": scored,
        "weights": {k: round(v, 4) for k, v in weights.items()},
        "labels": INDUSTRY_FACTOR_LABELS,
        "normalization": INDUSTRY_FACTOR_NORMALIZATION,
        "degraded": bool(entry.get("degraded")) or not has_5d,
        "degraded_reason": entry.get("degraded_reason", "") if not has_5d else "",
        "warnings": (
            ["5 日累计净额不可用，其权重已分摊到其余子因子"]
            if not has_5d
            else []
        ),
    }


# ---------------------------------------------------------------------------
# 关注个股候选池
# ---------------------------------------------------------------------------


def build_stock_candidates(
    fetched: Dict[str, Any],
    industry_result: Dict[str, Any],
    *,
    limit: int = 20,
    top_industry_count: int = 5,
) -> Dict[str, Any]:
    """合并出关注个股候选池。

    两路入选 + 一路补充：

    1. **龙虎榜机构净买** —— 当日机构专用席位净买额为正的个股，按净买额降序。
    2. **行业龙头** —— 行业分前 ``top_industry_count`` 个行业的「领涨股」。
    3. **同花顺个股资金流（补充，不入选）** —— 那份数据是**当日涨幅榜**，实测涨幅榜
       前排的资金净额大量为负，拿它当"主力净流入榜"会把资金在净流出的股票选进来。
       所以只用来给候选池的个股补上当日资金方向，并标出净流出的（``flow_warning``）。

    合并后**过滤掉所属行业资金为净流出的个股**，并给每只股票记下 ``reasons``（为什么
    入选）与 ``metrics``（入选依据的字段明细），让用户能自己复核而不是只看一个名字。
    """
    lhb_entry = fetched.get("lhb_institution") or {}
    candidates_entry = fetched.get("stock_candidates") or {}
    lhb_stocks = list((lhb_entry.get("value") or {}).get("stocks") or [])
    stat_stocks = list((candidates_entry.get("value") or {}).get("lhb_stocks") or [])
    flow_stocks = list((candidates_entry.get("value") or {}).get("flow_stocks") or [])

    industries = list(industry_result.get("industries") or [])
    industry_by_name = {row.get("name"): row for row in industries}

    pool: Dict[str, Dict[str, Any]] = {}

    def _upsert(code: str, name: str, reason: str, metrics: Dict[str, Any]) -> None:
        code = str(code or "").strip()
        if not code:
            return
        row = pool.setdefault(
            code,
            {
                "code": code,
                "name": str(name or "").strip(),
                "reasons": [],
                "metrics": {},
            },
        )
        if name and not row["name"]:
            row["name"] = str(name).strip()
        if reason not in row["reasons"]:
            row["reasons"].append(reason)
        row["metrics"].update({k: v for k, v in metrics.items() if v is not None})

    # 路 1：龙虎榜机构专用席位净买为正
    for stock in lhb_stocks:
        net = stock.get("institution_net")
        if net is None or net <= 0:
            continue
        _upsert(
            stock.get("code"),
            stock.get("name"),
            "龙虎榜机构净买",
            {
                "institution_net": net,
                "buyer_count": stock.get("buyer_count"),
                "seller_count": stock.get("seller_count"),
                "change_pct": stock.get("change_pct"),
                "close": stock.get("close"),
                "market_cap": stock.get("market_cap"),
                "reason": stock.get("reason"),
                "last_listed": lhb_entry.get("as_of"),
            },
        )

    # 路 2：行业分靠前行业的领涨股
    for row in industries[: max(0, int(top_industry_count))]:
        leader = str(row.get("leader") or "").strip()
        if not leader:
            continue
        # 领涨股本身没有代码字段，用业界惯例的占位说明，代码交由路 1 / 路 3 补全。
        _upsert(
            f"LEADER::{leader}",
            leader,
            f"行业龙头（{row.get('name')} 当日领涨）",
            {
                "industry": row.get("name"),
                "industry_score": row.get("score"),
                "industry_net": row.get("net"),
                "industry_net_5d": row.get("net_5d"),
                "leader_change_pct": row.get("leader_change_pct"),
                "leader_price": row.get("leader_price"),
            },
        )

    # 路 3 不再是独立入选路径。同花顺个股落地页是**当日涨幅榜**（不是净流入榜），
    # 拿它当"主力净流入居前"会选出资金在净流出的股票。改成查表补充：给候选池里的
    # 个股补上当日资金方向，并标出"涨上榜但资金净流出"的（拉高出货的嫌疑）。
    flow_by_code: Dict[str, Dict[str, Any]] = {}
    for stock in flow_stocks:
        code = str(stock.get("code") or "").strip()
        if code:
            flow_by_code[code] = stock

    # 用龙虎榜个股统计补全领涨股的代码与基本面字段。领涨股此前是用占位键
    # ``LEADER::<name>`` 存进池子的；一旦解析出真实代码，必须把它从占位键挪到真实代码键。
    # 否则同一只股票若又走了路 1（龙虎榜机构净买，按真实代码入池），会在池子里留下两条
    # 同码记录，最终产出重复候选，total_pool 与 sources 也随之虚高。
    stat_by_name = {str(s.get("name") or "").strip(): s for s in stat_stocks}
    for placeholder_key, row in list(pool.items()):
        if not row["code"].startswith("LEADER::"):
            continue
        matched = stat_by_name.get(row["name"])
        if not matched:
            continue
        row["metrics"].setdefault("institution_net", matched.get("institution_net"))
        row["metrics"].setdefault("lhb_net", matched.get("lhb_net"))
        row["metrics"].setdefault("listed_count", matched.get("listed_count"))
        row["metrics"].setdefault("change_1m", matched.get("change_1m"))

        real_code = str(matched.get("code") or "").strip()
        if not real_code:
            continue
        existing = pool.get(real_code)
        if existing is not None and existing is not row:
            # 路 1 已按真实代码建过条目 —— 把龙头这条并进去（合并入选理由与补充指标），
            # 而不是留下两条同码记录。路 1 自身的指标更权威，用 setdefault 不覆盖。
            for reason in row["reasons"]:
                if reason not in existing["reasons"]:
                    existing["reasons"].append(reason)
            for metric_key, metric_value in row["metrics"].items():
                if metric_value is not None:
                    existing["metrics"].setdefault(metric_key, metric_value)
            pool.pop(placeholder_key, None)
        else:
            # 没有重复，直接把占位键换成真实代码。
            row["code"] = real_code
            pool.pop(placeholder_key, None)
            pool[real_code] = row

    # 仍未解析出真实代码的领涨股，把占位前缀抹掉，避免 ``LEADER::`` 泄漏到候选、
    # 被剔除列表以及下游（Web 代码列、IM 推送）。前端/推送另有兜底处理历史快照。
    for row in pool.values():
        if row["code"].startswith("LEADER::"):
            row["code"] = ""

    # 记录领涨股所属行业的资金方向，供过滤使用
    for row in pool.values():
        industry_name = row["metrics"].get("industry")
        if industry_name and industry_name in industry_by_name:
            industry_row = industry_by_name[industry_name]
            row["metrics"]["industry_net"] = industry_row.get("net")
            row["metrics"]["industry_score"] = industry_row.get("score")

    # 补充第 3 路的个股资金方向（只补已有的，不新增候选）
    for row in pool.values():
        flow = flow_by_code.get(row["code"])
        if not flow:
            continue
        row["metrics"]["main_net"] = flow.get("main_net")
        row["metrics"]["turnover_rate"] = flow.get("turnover_rate")
        if flow.get("main_net") is not None and flow["main_net"] < 0:
            # 涨幅榜上但资金净流出 —— 真实存在的出货形态，必须让用户看到而不是藏着。
            row["flow_warning"] = "当日资金净流出，注意拉高出货风险"

    filtered: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []
    for row in pool.values():
        industry_net = row["metrics"].get("industry_net")
        # 只有"确定所属行业且该行业净流出"才剔除；未匹配到行业的（龙虎榜路线）保留，
        # 因为它们在个股层面本身已有正向证据。
        if industry_net is not None and industry_net < 0:
            dropped.append({"code": row["code"], "name": row["name"], "industry_net": industry_net})
            continue
        filtered.append(row)

    # 排序：机构净买优先，其次个股资金净额，再次行业分。
    def _sort_key(row: Dict[str, Any]) -> tuple:
        metrics = row["metrics"]
        return (
            metrics.get("institution_net") or 0.0,
            metrics.get("main_net") or 0.0,
            metrics.get("industry_score") or 0.0,
        )

    filtered.sort(key=_sort_key, reverse=True)
    limited = filtered[: max(1, int(limit))]

    warnings: List[str] = []
    if not flow_stocks:
        warnings.append(
            f"同花顺个股资金流不可用（{(candidates_entry.get('degraded_reason') or '未取到数据')}），"
            "候选个股缺少当日资金方向佐证"
        )
    if not lhb_stocks:
        warnings.append("当日无龙虎榜机构专用席位数据，候选池缺少机构净买线索")

    outflow_flagged = [r for r in filtered if r.get("flow_warning")]

    return {
        "candidates": limited,
        "total_pool": len(filtered),
        "dropped_by_industry": dropped[:20],
        "dropped_count": len(dropped),
        "outflow_flagged_count": len(outflow_flagged),
        "sources": {
            "龙虎榜机构净买": len([r for r in filtered if "龙虎榜机构净买" in r["reasons"]]),
            "行业龙头": len([r for r in filtered if any("行业龙头" in x for x in r["reasons"])]),
            # 只作为佐证，不是入选路径 —— 列在这里是为了让用户看清覆盖了多少只。
            "个股资金方向已补充": len([r for r in filtered if r["metrics"].get("main_net") is not None]),
        },
        "warnings": warnings,
        "degraded": bool(warnings),
        "degraded_reason": "；".join(warnings),
    }


__all__ = ["score_market", "score_industries", "build_stock_candidates"]
