# -*- coding: utf-8 -*-
"""行业资金模块的 LLM 解读层。

**模型不参与打分**。五因子的数值、分位与权重全部由 ``scoring.py`` 用确定性规则算出，
模型只负责把这些已经算好的结果写成一段人话（关注方向、风险提示）。

这样做是因为金融场景下模型编造标的的代价很高：如果让模型自己挑股票，它会给出
看起来合理但根本不存在的代码。因此这里做了三层约束：

1. prompt 里明确列出**只允许提及的个股白名单**（候选池里的代码 + 名称）；
2. 返回结果里的个股逐条比对白名单，越界的直接丢弃并记入 ``dropped_mentions``；
3. 模型不可用 / 超时 / 返回无法解析时，退回 :func:`_rule_based_fallback` ——
   用规则把同样的数字拼成结论，保证页面永远有结论可看，不会因为模型问题白屏。
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

ANALYSIS_CALL_TYPE = "industry_funds"
ANALYSIS_TEMPERATURE = 0.2
# 留出余量：推理型模型会先输出 reasoning，正文预算不足会以 finish_reason=length 收尾、
# content 为空，上游会当成空响应直接判失败。
ANALYSIS_MAX_OUTPUT_TOKENS = 4096

MAX_DIRECTIONS = 6
MAX_STOCK_NOTES = 12
MAX_RISKS = 6

SYSTEM_PROMPT = """你是一名严谨的 A 股市场研究员。你会收到一份**已经计算完成**的量化信号汇总，
你的唯一任务是用简洁、克制的中文把它解读成面向普通投资者的结论。

严格遵守以下规则：
1. 所有数值、评分、分位都来自给定数据，**不得修改、不得外推、不得补充你没有看到的数据**。
2. 提及个股时**只能**使用「允许提及的个股」清单里的代码和名称。清单之外的任何标的
   一律不得提及，包括你记忆里的知名公司。
3. 打分基于「主力资金」类指标，而这类指标是按成交金额分档推断的、并非真实账户身份。
   提到相关结论时要体现这一不确定性，不要写成板上钉钉的判断。
4. 不得给出具体的买卖价位、目标价或收益承诺。
5. 只输出一个 JSON 对象，不要输出 markdown 代码块标记，不要输出任何解释性前后缀。

输出 JSON 结构：
{
  "summary": "一段 100-200 字的总体判断，说明当前复合信号处于什么状态、主要由哪些因子驱动",
  "directions": [
    {"name": "关注方向（行业或风格）", "stance": "bullish|neutral|bearish",
     "reason": "为什么关注它，引用具体数字", "evidence": ["引用的关键数据点"]}
  ],
  "stock_notes": [
    {"code": "600000", "name": "某公司", "note": "关注理由，引用入选依据里的数字"}
  ],
  "risks": ["风险提示，要具体，不要泛泛而谈"]
}"""


def _fmt_yi(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value / 1e8:,.{digits}f} 亿元"


def _fmt_pct(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.{digits}f}%"


class IndustryFundsAnalyzer:
    """把打分结果写成结论文本，带白名单约束与规则兜底。"""

    def __init__(self, analyzer=None, config=None):
        from src.config import get_config

        self.config = config or get_config()
        self._analyzer = analyzer
        self._llm_disabled = False

    @property
    def analyzer(self):
        if self._analyzer is None:
            from src.analyzer import get_analyzer

            self._analyzer = get_analyzer()
        return self._analyzer

    # ------------------------------------------------------------------ public

    def analyze(
        self,
        *,
        market: Dict[str, Any],
        industry_result: Dict[str, Any],
        candidates: Dict[str, Any],
        fetched: Dict[str, Any],
        progress=None,
    ) -> Dict[str, Any]:
        """生成结论。**永不抛异常** —— 任何失败都退回规则兜底。"""
        warnings: List[str] = []
        whitelist = self._build_whitelist(candidates)
        prompt = self._build_prompt(market, industry_result, candidates, fetched, whitelist)

        if progress:
            progress("analyzing")
        raw_text, model, provider, error = self._call_model(prompt)
        if not raw_text:
            warnings.append(f"模型不可用或返回为空（{error or '未返回内容'}），已退回规则化结论")
            fallback = self._rule_based_fallback(market, industry_result, candidates)
            fallback["warnings"] = warnings
            fallback["model"] = model
            fallback["provider"] = provider
            return fallback

        parsed = self._parse_json(raw_text)
        if not parsed:
            warnings.append("模型返回内容不是可解析的 JSON，已退回规则化结论")
            fallback = self._rule_based_fallback(market, industry_result, candidates)
            fallback["warnings"] = warnings
            fallback["model"] = model
            fallback["provider"] = provider
            return fallback

        result = self._ground(parsed, whitelist)
        result["generated_by"] = "llm"
        result["model"] = model
        result["provider"] = provider
        result["warnings"] = warnings + result.get("grounding_warnings", [])
        return result

    # ------------------------------------------------------------------ prompt

    def _build_whitelist(self, candidates: Dict[str, Any]) -> Dict[str, str]:
        """允许模型提及的个股：候选池里的 代码 → 名称。"""
        out: Dict[str, str] = {}
        for row in candidates.get("candidates") or []:
            code = str(row.get("code") or "").strip()
            name = str(row.get("name") or "").strip()
            if code and not code.startswith("LEADER::"):
                out[code] = name
        return out

    def _build_prompt(
        self,
        market: Dict[str, Any],
        industry_result: Dict[str, Any],
        candidates: Dict[str, Any],
        fetched: Dict[str, Any],
        whitelist: Dict[str, str],
    ) -> str:
        lines: List[str] = []

        lines.append("## 一、大盘复合信号")
        lines.append(
            f"复合分：{market.get('score')} / 100（{market.get('verdict_label')}），"
            f"可用因子权重合计 {_fmt_pct(market.get('available_weight'), 0)}"
        )
        lines.append("")
        lines.append("| 因子 | 权重 | 得分(满分20) | 原始值 | 归一化依据 | 数据源 | 数据日期 |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for factor in market.get("factors") or []:
            score = factor.get("score")
            lines.append(
                "| {label} | {weight:.0%} | {score} | {raw} | {basis} | {source} | {as_of} |".format(
                    label=factor.get("label", ""),
                    weight=float(factor.get("weight") or 0.0),
                    score="不可用" if score is None else f"{score:.2f}",
                    raw=str(factor.get("raw_display") or "—").replace("|", "/"),
                    basis=str(factor.get("basis") or "—").replace("|", "/"),
                    source=str(factor.get("source") or "—").replace("|", "/"),
                    as_of=str(factor.get("as_of") or "—"),
                )
            )
        if market.get("warnings"):
            lines.append("")
            lines.append("**数据完整性提示：**")
            for warning in market["warnings"]:
                lines.append(f"- {warning}")

        lines.append("")
        lines.append("## 二、行业资金流排名（同花顺 90 个行业分类）")
        industries = industry_result.get("industries") or []
        if industries:
            lines.append("资金净流入前 10：")
            for row in industries[:10]:
                lines.append(
                    f"- {row.get('rank')}. {row.get('name')}：得分 {row.get('score')}，"
                    f"当日净额 {row.get('net')} 亿元，5 日累计 {row.get('net_5d')} 亿元，"
                    f"涨跌幅 {row.get('change_pct')}%，领涨股 {row.get('leader')}"
                    f"（{row.get('leader_change_pct')}%）"
                )
            lines.append("")
            lines.append("资金净流出后 10：")
            for row in industries[-10:]:
                lines.append(
                    f"- {row.get('rank')}. {row.get('name')}：得分 {row.get('score')}，"
                    f"当日净额 {row.get('net')} 亿元，涨跌幅 {row.get('change_pct')}%"
                )
            lines.append("")
            weights = industry_result.get("weights") or {}
            labels = industry_result.get("labels") or {}
            weight_bits = [
                f"{labels.get(key, key)}/{float(weight):.0%}" for key, weight in weights.items()
            ]
            lines.append("行业分子因子权重：" + "、".join(weight_bits))
            if industry_result.get("warnings"):
                for warning in industry_result["warnings"]:
                    lines.append(f"- 提示：{warning}")
        else:
            lines.append("（行业资金流不可用）")

        lines.append("")
        lines.append("## 三、关注个股候选池")
        lines.append("入选依据（两路入选：龙虎榜机构净买 / 行业龙头；另有同花顺当日资金方向作佐证）：")
        rows = candidates.get("candidates") or []
        if rows:
            for row in rows:
                metrics = row.get("metrics") or {}
                bits: List[str] = []
                if metrics.get("institution_net") is not None:
                    bits.append(f"机构净买 {_fmt_yi(metrics['institution_net'])}")
                if metrics.get("main_net") is not None:
                    bits.append(f"当日资金净额 {_fmt_yi(metrics['main_net'])}")
                if metrics.get("industry"):
                    bits.append(
                        f"所属行业 {metrics['industry']}（行业分 {metrics.get('industry_score')}，"
                        f"行业当日净额 {metrics.get('industry_net')} 亿元）"
                    )
                if metrics.get("change_pct") is not None:
                    bits.append(f"涨跌幅 {metrics['change_pct']}%")
                if metrics.get("market_cap") is not None:
                    bits.append(f"流通市值 {metrics['market_cap']} 亿元")
                if metrics.get("reason"):
                    bits.append(f"上榜原因：{metrics['reason']}")
                warning = f"｜⚠️ {row['flow_warning']}" if row.get("flow_warning") else ""
                lines.append(
                    f"- {row.get('code')} {row.get('name')}｜入选路径：{' + '.join(row.get('reasons') or [])}"
                    f"｜{'；'.join(bits) if bits else '无明细'}{warning}"
                )
        else:
            lines.append("（候选池为空）")
        if candidates.get("warnings"):
            for warning in candidates["warnings"]:
                lines.append(f"- 提示：{warning}")

        lines.append("")
        lines.append("## 四、允许提及的个股（清单之外一律不得提及）")
        if whitelist:
            lines.append("、".join(f"{code} {name}" for code, name in whitelist.items()))
        else:
            lines.append("（无，因此结论中不要提及任何具体个股）")

        lines.append("")
        lines.append("## 五、数据源可信度说明")
        lines.append(
            "「主力资金」「行业资金流」为按单笔成交金额分档推断的统计值，非真实账户身份；"
            "龙虎榜机构席位、两融余额、ETF 份额为官方或准官方披露。解读时请体现这一差异。"
        )
        for key, label in (
            ("lhb_institution", "龙虎榜机构净买"),
            ("etf_share", "ETF 份额"),
            ("margin", "两融余额"),
            ("valuation", "估值分位"),
        ):
            entry = fetched.get(key) or {}
            if entry.get("degraded"):
                lines.append(f"- {label}口径不完整：{entry.get('degraded_reason') or entry.get('error')}")

        lines.append("")
        lines.append("请按 system 要求的 JSON 结构输出结论。")
        return "\n".join(lines)

    # ------------------------------------------------------------------ model

    def _resolve_reasoning_effort(self, model: str) -> Optional[str]:
        """只对支持 reasoning_effort 的模型族透传（同 market_brief 的处理）。"""
        effort = str(getattr(self.config, "industry_funds_reasoning_effort", "low") or "").strip().lower()
        if effort not in {"low", "high", "max"}:
            return None
        model_name = str(model or "").strip().lower().rsplit("/", 1)[-1]
        if model_name.startswith(("glm-5", "deepseek-v4")):
            return effort
        return None

    def _backend_identity(self) -> tuple[str, str]:
        try:
            provider, model = self.analyzer.get_generation_backend_identity()
            return str(model or ""), str(provider or "")
        except Exception as exc:
            logger.warning("行业资金：读取生成后端身份失败 %s", exc)
            return "", ""

    def _call_model(self, prompt: str) -> tuple[str, str, str, str]:
        """调用一次模型。返回 ``(文本, 模型, 提供方, 错误描述)``，失败时文本为空串。

        必须自己兜超时：``generate_text_with_metadata`` 没有暴露 timeout，底层在连接
        挂死时会无限等待，后台任务就会永远停在 analyzing 这一段。
        """
        if self._llm_disabled:
            return "", "", "", "模型已在本次运行中被标记为不可用"

        model_hint, provider_hint = self._backend_identity()
        timeout_seconds = max(
            10.0,
            min(float(getattr(self.config, "industry_funds_llm_timeout_sec", 120)), 600.0),
        )
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="industry-funds-llm")
        started = time.perf_counter()
        try:
            reasoning_effort = self._resolve_reasoning_effort(model_hint)
            future = executor.submit(
                self.analyzer.generate_text_with_metadata,
                f"{SYSTEM_PROMPT}\n\n{prompt}",
                max_tokens=ANALYSIS_MAX_OUTPUT_TOKENS,
                temperature=ANALYSIS_TEMPERATURE,
                **({"reasoning_effort": reasoning_effort} if reasoning_effort else {}),
            )
            try:
                generation = future.result(timeout=timeout_seconds)
            except FuturesTimeoutError:
                logger.warning("行业资金：模型调用超过 %.0fs 放弃", timeout_seconds)
                return "", model_hint, provider_hint, f"超过 {int(timeout_seconds)}s 超时"
        except Exception as exc:
            self._llm_disabled = True
            logger.warning("行业资金：模型调用异常 %s: %s", type(exc).__name__, exc)
            return "", model_hint, provider_hint, f"{type(exc).__name__}: {str(exc)[:160]}"
        finally:
            # 超时的那次调用无法取消，不等它结束，否则又变回阻塞等待。
            executor.shutdown(wait=False)

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        text = (getattr(generation, "text", "") or "").strip()
        model = getattr(generation, "model", None) or model_hint
        provider = getattr(generation, "provider", None) or provider_hint
        logger.info("行业资金：模型返回 %d 字符，用时 %dms", len(text), elapsed_ms)
        return text, str(model or ""), str(provider or ""), ""

    # ------------------------------------------------------------------ parse

    def _parse_json(self, text: str) -> Optional[Dict[str, Any]]:
        from src.agent.runner import try_parse_json

        payload = try_parse_json(text)
        if isinstance(payload, dict):
            return payload
        # 有些模型会把 JSON 包在 markdown 代码块里，try_parse_json 已处理大部分情况，
        # 这里再兜一次裸花括号提取。
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                candidate = json.loads(text[start : end + 1])
                if isinstance(candidate, dict):
                    return candidate
            except Exception:
                pass
        logger.warning("行业资金：模型返回内容无法解析为 JSON")
        return None

    def _ground(self, parsed: Dict[str, Any], whitelist: Dict[str, str]) -> Dict[str, Any]:
        """按白名单裁剪模型输出，越界的个股一律丢弃并记录。"""
        dropped: List[str] = []

        directions = []
        for item in (parsed.get("directions") or [])[:MAX_DIRECTIONS]:
            if not isinstance(item, dict):
                continue
            stance = str(item.get("stance") or "neutral").strip().lower()
            if stance not in {"bullish", "neutral", "bearish"}:
                stance = "neutral"
            evidence = [
                str(x) for x in (item.get("evidence") or []) if isinstance(x, (str, int, float))
            ][:6]
            directions.append(
                {
                    "name": str(item.get("name") or "").strip(),
                    "stance": stance,
                    "reason": str(item.get("reason") or "").strip(),
                    "evidence": evidence,
                }
            )

        stock_notes = []
        for item in (parsed.get("stock_notes") or [])[:MAX_STOCK_NOTES]:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or "").strip()
            name = str(item.get("name") or "").strip()
            if code not in whitelist:
                dropped.append(f"{code} {name}".strip())
                continue
            stock_notes.append(
                {
                    "code": code,
                    "name": whitelist.get(code) or name,
                    "note": str(item.get("note") or "").strip(),
                }
            )

        risks = [str(x).strip() for x in (parsed.get("risks") or []) if str(x).strip()][:MAX_RISKS]

        grounding_warnings: List[str] = []
        if dropped:
            grounding_warnings.append(
                "模型提到了候选池之外的个股，已自动剔除：" + "、".join(dropped)
            )

        return {
            "summary": str(parsed.get("summary") or "").strip(),
            "directions": directions,
            "stock_notes": stock_notes,
            "risks": risks,
            "dropped_mentions": dropped,
            "grounding_warnings": grounding_warnings,
        }

    # ------------------------------------------------------------------ fallback

    def _rule_based_fallback(
        self,
        market: Dict[str, Any],
        industry_result: Dict[str, Any],
        candidates: Dict[str, Any],
    ) -> Dict[str, Any]:
        """模型不可用时的确定性结论：用同样的数字直接拼，不留白。"""
        score = market.get("score")
        verdict = market.get("verdict_label") or "无法判断"
        factors = market.get("factors") or []

        drivers = sorted(
            [f for f in factors if f.get("available") and f.get("score") is not None],
            key=lambda f: f["score"] - (f["max_score"] / 2),
            reverse=True,
        )
        if score is None:
            summary = "本轮五个因子的数据源全部不可用，无法给出复合判断，请检查网络或稍后重试。"
        else:
            strongest = drivers[0] if drivers else None
            weakest = drivers[-1] if len(drivers) > 1 else None
            parts = [
                f"五因子复合信号为 {score} 分，判定为「{verdict}」"
                f"（偏多阈值 {market.get('thresholds', {}).get('bullish')}，"
                f"偏空阈值 {market.get('thresholds', {}).get('bearish')}）。"
            ]
            if strongest:
                parts.append(
                    f"最偏多的因子是{strongest['label']}（{strongest['score']} 分，{strongest['raw_display']}）。"
                )
            if weakest and weakest is not strongest:
                parts.append(
                    f"最偏空的因子是{weakest['label']}（{weakest['score']} 分，{weakest['raw_display']}）。"
                )
            if market.get("renormalized"):
                parts.append(
                    f"注意：本轮只有 {_fmt_pct(market.get('available_weight'), 0)} 权重的因子可用，"
                    "结论的稳健性低于五因子齐全时。"
                )
            summary = "".join(parts)

        directions = []
        for row in (industry_result.get("industries") or [])[:MAX_DIRECTIONS]:
            directions.append(
                {
                    "name": str(row.get("name") or ""),
                    "stance": "bullish" if (row.get("net") or 0) > 0 else "bearish",
                    "reason": (
                        f"行业资金流得分 {row.get('score')}（当日净额 {row.get('net')} 亿元，"
                        f"5 日累计 {row.get('net_5d')} 亿元，涨跌幅 {row.get('change_pct')}%）"
                    ),
                    "evidence": [
                        f"当日净额 {row.get('net')} 亿元",
                        f"5 日累计 {row.get('net_5d')} 亿元",
                        f"领涨股 {row.get('leader')}（{row.get('leader_change_pct')}%）",
                    ],
                }
            )

        stock_notes = []
        for row in (candidates.get("candidates") or [])[:MAX_STOCK_NOTES]:
            metrics = row.get("metrics") or {}
            bits: List[str] = []
            if metrics.get("institution_net") is not None:
                bits.append(f"龙虎榜机构净买 {_fmt_yi(metrics['institution_net'])}")
            if metrics.get("main_net") is not None:
                bits.append(f"当日资金净额 {_fmt_yi(metrics['main_net'])}")
            if metrics.get("industry"):
                bits.append(f"所属行业 {metrics['industry']}")
            stock_notes.append(
                {
                    "code": str(row.get("code") or ""),
                    "name": str(row.get("name") or ""),
                    "note": "入选路径：" + " + ".join(row.get("reasons") or []) + (
                        f"；{'；'.join(bits)}" if bits else ""
                    ),
                }
            )

        risks = [
            "「主力资金」「行业资金流」为按成交金额分档推断的统计值，非真实账户身份，"
            "拆单与涨停封单都会造成失真。",
            "复合信号是日频盘后指标，不反映盘中突发消息与政策变化。",
        ]
        if market.get("renormalized"):
            risks.append("本轮存在数据源缺失，部分因子权重已按比例分摊，结论稳健性下降。")
        if candidates.get("degraded"):
            risks.append(f"关注个股候选池口径不完整：{candidates.get('degraded_reason')}")

        return {
            "summary": summary,
            "directions": directions,
            "stock_notes": stock_notes,
            "risks": risks,
            "dropped_mentions": [],
            "grounding_warnings": [],
            "generated_by": "rule_fallback",
        }


__all__ = ["IndustryFundsAnalyzer"]
