# -*- coding: utf-8 -*-
"""行业资金模块的编排层。

把「抓取 → 打分 → 解读 → 落库」串起来，并负责缓存语义：

- :meth:`IndustryFundsService.overview` 只读缓存，秒回；没有缓存时返回 ``None``，
  由接口层告诉前端"还没生成过，请点刷新"，而不是同步抓一次把请求挂住。
- :meth:`IndustryFundsService.refresh` 真正干活，由后台任务调用（见 ``jobs.py``）。

为什么刷新必须走后台任务：前端 ``apiClient`` 的超时是 30 秒，而全量抓取 +
模型解读实测要 30~90 秒，同步接口一定会被切断。
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.services.industry_funds import scoring
from src.services.industry_funds.analyzer import IndustryFundsAnalyzer
from src.services.industry_funds.constants import (
    FLOW_DISCLAIMER,
    INVESTMENT_DISCLAIMER,
    MARKET_FACTOR_LABELS,
    PHASE_LABELS,
    SOURCE_LABELS,
)
from src.services.industry_funds.fetchers import (
    fetch_etf_shares,
    fetch_industry_fund_flow,
    fetch_lhb_institution,
    fetch_margin,
    fetch_stock_candidates,
    fetch_valuation,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "industry-funds-v1"

# 抓取阶段 → 进度阶段的映射，前端进度条按这个走。
_FETCH_PHASES: Tuple[Tuple[str, str], ...] = (
    ("industry_fund_flow", "fetching_flow"),
    ("lhb_institution", "fetching_lhb"),
    ("etf_share", "fetching_etf"),
    ("margin", "fetching_margin"),
    ("valuation", "fetching_valuation"),
)


class IndustryFundsUnavailableError(RuntimeError):
    """模块被配置关闭。"""


class IndustryFundsService:
    """行业资金的抓取、打分、解读与缓存。"""

    def __init__(self, repository=None, analyzer: Optional[IndustryFundsAnalyzer] = None, config=None):
        from src.config import get_config

        self.config = config or get_config()
        self._repository = repository
        self._analyzer = analyzer

    # ------------------------------------------------------------------ 依赖

    @property
    def repository(self):
        if self._repository is None:
            from src.repositories.industry_funds_repo import IndustryFundsRepository

            self._repository = IndustryFundsRepository()
        return self._repository

    @property
    def analyzer(self) -> IndustryFundsAnalyzer:
        if self._analyzer is None:
            self._analyzer = IndustryFundsAnalyzer(config=self.config)
        return self._analyzer

    def _require_enabled(self) -> None:
        if not bool(getattr(self.config, "industry_funds_enabled", True)):
            raise IndustryFundsUnavailableError(
                "行业资金模块已通过 INDUSTRY_FUNDS_ENABLED 关闭"
            )

    # ------------------------------------------------------------------ 读缓存

    def overview(self) -> Optional[Dict[str, Any]]:
        """读取最近一份快照。没有缓存时返回 ``None``。"""
        self._require_enabled()
        row = self.repository.latest_snapshot()
        if row is None:
            return None
        payload = self._decode_payload(row)
        if payload is None:
            return None
        payload["cached"] = True
        payload["stored_at"] = row.created_at.isoformat() if row.created_at else None
        return payload

    def history(self, *, limit: int = 30) -> List[Dict[str, Any]]:
        """最近若干份快照的摘要（不含完整 payload），供前端画趋势。"""
        self._require_enabled()
        return [
            {
                "trade_date": row.trade_date,
                "score": row.score,
                "verdict": row.verdict,
                "etf_delta": row.etf_delta,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in self.repository.history(limit=limit)
        ]

    @staticmethod
    def _decode_payload(row) -> Optional[Dict[str, Any]]:
        try:
            payload = json.loads(row.payload or "{}")
        except (TypeError, ValueError):
            logger.warning("行业资金：快照 %s 的 payload 无法解析，按无缓存处理", row.id)
            return None
        return payload if isinstance(payload, dict) else None

    # ------------------------------------------------------------------ 刷新

    def refresh(
        self, *, progress: Optional[Callable[[str, str], None]] = None
    ) -> Dict[str, Any]:
        """抓取全部数据源，打分，生成结论并落库。返回完整 payload。"""
        self._require_enabled()
        started = time.perf_counter()

        def _report(phase: str, message: str = "") -> None:
            if progress:
                progress(phase, message or PHASE_LABELS.get(phase, phase))

        _report("fetching_flow", "并行抓取 6 个数据源")
        fetched = self._fetch_all()

        _report("scoring", "计算大盘复合信号与行业分")
        etf_delta_history = self._etf_delta_history()
        market = scoring.score_market(fetched, etf_delta_history=etf_delta_history)
        industry_result = scoring.score_industries(fetched)
        candidates = scoring.build_stock_candidates(
            fetched,
            industry_result,
            limit=int(getattr(self.config, "industry_funds_candidate_limit", 20)),
        )

        _report("analyzing", "生成结论解读")
        conclusion = self.analyzer.analyze(
            market=market,
            industry_result=industry_result,
            candidates=candidates,
            fetched=fetched,
        )

        _report("finalizing", "汇总校验并落库")
        payload = self._build_payload(
            fetched=fetched,
            market=market,
            industry_result=industry_result,
            candidates=candidates,
            conclusion=conclusion,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        self._persist(payload, fetched, market)
        payload["cached"] = False
        return payload

    def _fetch_all(self) -> Dict[str, Any]:
        """并行抓取所有数据源。

        并发跑是必要的：六个源串行要一两分钟，而它们互不依赖。同花顺那一路走的是
        服务端渲染的落地页（纯 requests），没有 py_mini_racer 那种不能并发的限制，
        所以这里不需要为它做任何串行化处理。
        """
        timeout = float(getattr(self.config, "industry_funds_fetch_timeout_sec", 30))
        baseline = self._latest_etf_baseline()

        tasks = {
            "industry_fund_flow": lambda: fetch_industry_fund_flow(timeout=timeout),
            "lhb_institution": lambda: fetch_lhb_institution(timeout=timeout),
            "etf_share": lambda: fetch_etf_shares(timeout=timeout, baseline=baseline),
            "margin": lambda: fetch_margin(timeout=timeout),
            "valuation": lambda: fetch_valuation(timeout=timeout),
            "stock_candidates": lambda: fetch_stock_candidates(timeout=timeout),
        }

        fetched: Dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=len(tasks), thread_name_prefix="industry-funds-fetch") as pool:
            futures = {key: pool.submit(fn) for key, fn in tasks.items()}
            for key, future in futures.items():
                try:
                    fetched[key] = future.result()
                except Exception as exc:
                    # 抓取层已经自己兜了异常，走到这里说明是编排层的意外错误，
                    # 也要降级而不是把整次刷新打断。
                    logger.warning("行业资金：%s 抓取阶段异常 %s", key, exc)
                    fetched[key] = {
                        "key": key,
                        "ok": False,
                        "source": SOURCE_LABELS.get(key, ""),
                        "as_of": "",
                        "degraded": True,
                        "degraded_reason": f"{type(exc).__name__}: {exc}",
                        "error": f"{type(exc).__name__}: {str(exc)[:180]}",
                        "value": {},
                        "raw": {},
                    }
        return fetched

    def _latest_etf_baseline(self) -> Optional[Dict[str, Any]]:
        """给深市 ETF 份额变动用的本地基线（深交所接口不提供历史快照）。"""
        row = self.repository.latest_snapshot()
        if row is None:
            return None
        payload = self._decode_payload(row)
        if payload is None:
            return None
        szse = ((payload.get("sources") or {}).get("etf_share") or {}).get("value", {}).get("szse") or {}
        total = szse.get("total_shares")
        stat_date = szse.get("date")
        if total is None or not stat_date:
            return None
        return {"szse": {"broad_based_total": total, "date": stat_date}}

    def _etf_delta_history(self) -> List[float]:
        try:
            return self.repository.etf_delta_history(limit=250)
        except Exception as exc:
            logger.warning("行业资金：读取 ETF 份额变动历史失败 %s", exc)
            return []

    # ------------------------------------------------------------------ 组装与落库

    def _build_payload(
        self,
        *,
        fetched: Dict[str, Any],
        market: Dict[str, Any],
        industry_result: Dict[str, Any],
        candidates: Dict[str, Any],
        conclusion: Dict[str, Any],
        duration_ms: int,
    ) -> Dict[str, Any]:
        warnings: List[str] = []
        for entry in fetched.values():
            if not entry.get("ok"):
                warnings.append(f"{MARKET_FACTOR_LABELS.get(entry['key'], entry['key'])}抓取失败：{entry.get('error')}")
            elif entry.get("degraded") and entry.get("degraded_reason"):
                warnings.append(f"{MARKET_FACTOR_LABELS.get(entry['key'], entry['key'])}口径不完整：{entry['degraded_reason']}")
        warnings.extend(market.get("warnings") or [])
        warnings.extend(industry_result.get("warnings") or [])
        warnings.extend(candidates.get("warnings") or [])
        warnings.extend(conclusion.get("warnings") or [])

        # 去重但保序 —— 同一条降级原因会被多个环节各报一次。
        seen = set()
        unique_warnings = []
        for warning in warnings:
            if warning and warning not in seen:
                seen.add(warning)
                unique_warnings.append(warning)

        trade_date = self._resolve_trade_date(fetched)
        degraded = (
            any(not entry.get("ok") for entry in fetched.values())
            or bool(market.get("degraded"))
            or bool(industry_result.get("degraded"))
        )

        return {
            "schema_version": SCHEMA_VERSION,
            "trade_date": trade_date,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "duration_ms": duration_ms,
            "market": market,
            "industries": industry_result,
            "candidates": candidates,
            "conclusion": conclusion,
            "sources": {
                key: {
                    "key": key,
                    "label": MARKET_FACTOR_LABELS.get(key, key),
                    "ok": bool(entry.get("ok")),
                    "source": entry.get("source", ""),
                    "as_of": entry.get("as_of", ""),
                    "degraded": bool(entry.get("degraded")),
                    "degraded_reason": entry.get("degraded_reason", ""),
                    "error": entry.get("error"),
                    "value": entry.get("value") or {},
                    "raw": entry.get("raw") or {},
                }
                for key, entry in fetched.items()
            },
            "factor_labels": MARKET_FACTOR_LABELS,
            "disclaimers": {
                "flow": FLOW_DISCLAIMER,
                "investment": INVESTMENT_DISCLAIMER,
            },
            "degraded": degraded,
            "degraded_reason": "；".join(unique_warnings),
            "warnings": unique_warnings,
            "cached": False,
        }

    @staticmethod
    def _resolve_trade_date(fetched: Dict[str, Any]) -> str:
        """数据所属交易日：优先行业资金流的 as_of（最贴近"今天这批资金数据"）。"""
        for key in ("industry_fund_flow", "lhb_institution", "etf_share", "margin", "valuation"):
            as_of = str((fetched.get(key) or {}).get("as_of") or "").strip()
            if as_of:
                return as_of
        return datetime.now().strftime("%Y-%m-%d")

    def _persist(self, payload: Dict[str, Any], fetched: Dict[str, Any], market: Dict[str, Any]) -> None:
        """落库。失败只记日志 —— 缓存写不进去不该让用户看不到这次的结果。"""
        try:
            etf_value = ((fetched.get("etf_share") or {}).get("value") or {})
            lhb_value = ((fetched.get("lhb_institution") or {}).get("value") or {})
            margin_value = ((fetched.get("margin") or {}).get("value") or {})
            valuation_value = ((fetched.get("valuation") or {}).get("value") or {})

            self.repository.save_snapshot(
                {
                    "trade_date": payload["trade_date"],
                    "score": market.get("score"),
                    "verdict": market.get("verdict"),
                    "etf_delta": etf_value.get("combined_delta"),
                    "lhb_rolling_5d": lhb_value.get("rolling_5d_net"),
                    "margin_balance": margin_value.get("financing_balance"),
                    "valuation_percentile": valuation_value.get("combined_percentile"),
                    "model": str(payload.get("conclusion", {}).get("model") or "")[:120] or None,
                    "provider": str(payload.get("conclusion", {}).get("provider") or "")[:64] or None,
                    "degraded": bool(payload.get("degraded")),
                    "payload": json.dumps(payload, ensure_ascii=False),
                }
            )
            retention = int(getattr(self.config, "industry_funds_retention_days", 90))
            deleted = self.repository.apply_retention(retention)
            if deleted:
                logger.info("行业资金：按 %d 天保留策略清理了 %d 份历史快照", retention, deleted)
        except Exception as exc:
            logger.warning("行业资金：快照落库失败（不影响本次结果） %s", exc)


__all__ = ["IndustryFundsService", "IndustryFundsUnavailableError", "SCHEMA_VERSION"]
