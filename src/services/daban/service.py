# -*- coding: utf-8 -*-
"""「我是打板王」编排层：抓取 → 周期 → 题材 → 候选 → 打分 → 模型解读 → 两条打法线 → 落库。

- :meth:`DabanService.overview` 只读库，秒回；没有快照时返回 ``None``，由接口层提示「点生成」。
- :meth:`DabanService.refresh` 真正干活，由后台任务调用（见 ``jobs.py``）。全量一次要打
  十几个上游、拉几十只候选的日 K、搜几个题材的新闻，再调一次模型，实测 30~150 秒，
  超过前端 30 秒的 HTTP 超时，所以不能做成同步接口。
- 首次刷新时库里没有历史，会先按日回补近 ``backfill_days`` 个交易日的周期指标（不调模型），
  否则 Δ、「主跌第 N 天」、题材持续天数都无从算起。

盘中刷新会标 ``intraday=True``：周期结论是临时的，不写推荐记录（避免污染两派战绩），
收盘后再刷新一次会覆盖。
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from statistics import mean
from typing import Any, Callable, Dict, List, Optional

from src.services.daban import cycle as cycle_mod
from src.services.daban.analyzer import MAX_LLM_CANDIDATES, MAX_LLM_THEMES, DabanAnalyzer
from src.services.daban.constants import (
    ACTION_ACTIONABLE,
    ACTION_TRIAL,
    DATA_DISCLAIMER,
    INVESTMENT_DISCLAIMER,
    MODE_LABELS,
    PHASE_LABELS,
    SCHEMA_VERSION,
    STAGE_LABELS,
    STYLE_LABELS,
    load_rules,
)
from src.services.daban.fetchers import (
    effective_trade_date,
    fetch_blocks,
    fetch_em,
    fetch_hot_rank,
    fetch_klines,
    fetch_ladder,
    fetch_limit_pool,
    fetch_theme_news,
    previous_trade_date,
    recent_trade_dates,
    run_parallel,
)
from src.services.daban.kline import analyze_kline, bar_changes, limit_pct
from src.services.daban.patterns import confirm_with_kline, generate_candidates
from src.services.daban.principles import PRINCIPLES
from src.services.daban.roles import assign_roles
from src.services.daban.scoring import finalize_score, score_candidate
from src.services.daban.styles import STYLES, build_lane, decide_style, mark_consensus
from src.services.daban.themes import (
    apply_story,
    build_themes,
    rule_story_space,
    split_reason,
)

logger = logging.getLogger(__name__)

FETCH_TIMEOUT_SEC = 15.0
FETCH_BUDGET_SEC = 60.0
KLINE_BUDGET_SEC = 60.0
NEWS_BUDGET_SEC = 25.0
HOT_TIMEOUT_SEC = 25.0


class DabanUnavailableError(RuntimeError):
    """模块被配置关闭。"""


class DabanUpstreamError(RuntimeError):
    """涨停池这一核心数据源完全不可用，无法生成。"""


def _decode(row) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    try:
        payload = json.loads(row.payload or "{}")
    except (TypeError, ValueError):
        logger.warning("打板王：快照 %s 的 payload 无法解析", getattr(row, "id", "?"))
        return None
    return payload if isinstance(payload, dict) else None


def apply_boards(zt_rows: List[Dict[str, Any]], ladder_env: Dict[str, Any], *, source: str) -> str:
    """把连板数写回涨停池。返回口径说明（用于降级提示）。

    同花顺池接口不带连板数，只能靠天梯；天梯失败时用「N 天 N 板」（天数 == 板数即连续）推断。
    东财池自带 ``lbc``，不需要天梯。
    """
    if source.startswith("东方财富"):
        return ""
    ladder = ((ladder_env or {}).get("value") or {}).get("ladder") if (ladder_env or {}).get("ok") else None
    if ladder is not None:
        for row in zt_rows:
            row["boards"] = int(ladder.get(row["code"], 1))
        return ""
    for row in zt_rows:
        days, boards = int(row.get("hd_days") or 0), int(row.get("hd_boards") or 0)
        row["boards"] = boards if (boards >= 2 and days == boards) else 1
    return "连板天梯不可用，连板数按「N 天 N 板」推断"


class DabanService:
    def __init__(self, repository=None, analyzer: Optional[DabanAnalyzer] = None, config=None):
        from src.config import get_config

        self.config = config or get_config()
        self._repository = repository
        self._analyzer = analyzer
        self.rules = load_rules(self.config)

    @property
    def repository(self):
        if self._repository is None:
            from src.repositories.daban_repo import DabanRepository

            self._repository = DabanRepository()
        return self._repository

    @property
    def analyzer(self) -> DabanAnalyzer:
        if self._analyzer is None:
            self._analyzer = DabanAnalyzer(config=self.config)
        return self._analyzer

    def _require_enabled(self) -> None:
        if not bool(getattr(self.config, "daban_enabled", True)):
            raise DabanUnavailableError("「我是打板王」已通过 DABAN_ENABLED 关闭")

    @property
    def include_bse(self) -> bool:
        return bool(getattr(self.config, "daban_include_bse", False))

    # ------------------------------------------------------------------ 读

    def overview(self, trade_date: Optional[str] = None) -> Optional[Dict[str, Any]]:
        self._require_enabled()
        if trade_date:
            row = self.repository.snapshot_for_trade_date(trade_date)
        else:
            row = self.repository.latest_snapshot(kind="full")
        payload = _decode(row)
        if payload is None:
            return None
        payload["cached"] = True
        payload["stored_at"] = row.created_at.isoformat() if row.created_at else None
        return payload

    def history(self, *, limit: int = 10) -> List[Dict[str, Any]]:
        self._require_enabled()
        return [
            {
                "trade_date": row.trade_date,
                "kind": row.kind,
                "stage": row.stage,
                "stage_label": STAGE_LABELS.get(row.stage or "", ""),
                "kobe_stage": row.kobe_stage,
                "fall_day": row.fall_day,
                "zt_count": row.zt_count,
                "dt_count": row.dt_count,
                "max_height": row.max_height,
                "promotion_rate": row.promotion_rate,
                "yzt_premium": row.yzt_premium,
                "preferred_style": row.preferred_style,
                "intraday": bool(row.intraday),
            }
            for row in self.repository.history(limit=limit)
        ]

    # ------------------------------------------------------------------ 刷新

    def refresh(
        self,
        *,
        progress: Optional[Callable[[str, str], None]] = None,
        trade_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._require_enabled()
        started = time.perf_counter()
        rules = self.rules

        def _report(phase: str, message: str = "") -> None:
            if progress:
                progress(phase, message or PHASE_LABELS.get(phase, phase))

        # 1. 涨停池是核心，先拿到它才知道交易日。
        _report("probing", "确认交易日并抓取涨停池")
        zt_env = fetch_limit_pool("zt", trade_date, timeout=FETCH_TIMEOUT_SEC, include_bse=self.include_bse)
        if not zt_env["ok"] and not trade_date:
            zt_env = fetch_limit_pool(
                "zt", effective_trade_date(), timeout=FETCH_TIMEOUT_SEC, include_bse=self.include_bse
            )
        if not zt_env["ok"]:
            raise DabanUpstreamError(f"涨停池不可用：{zt_env.get('error')}")
        day = zt_env["as_of"] or trade_date or effective_trade_date()
        if not zt_env["value"]["rows"]:
            raise DabanUpstreamError(f"{day} 没有涨停数据（可能不是交易日）")
        intraday = self._is_intraday(zt_env, day)
        prev_day = previous_trade_date(day)

        # 2. 冷启动回补（只回补周期指标，不调模型）。
        self._backfill(day, progress=_report)

        # 3. 其余数据源并行抓。
        _report("fetching", "并行抓取炸板 / 跌停 / 天梯 / 题材榜 / 昨日涨停表现 / 人气榜")
        envs = self._fetch_rest(day)
        envs["zt"] = zt_env
        board_note = apply_boards(zt_env["value"]["rows"], envs["ladder"], source=zt_env["source"])
        if board_note:
            envs["ladder"]["degraded_reason"] = board_note

        zt = zt_env["value"]["rows"]
        zb = (envs["zb"].get("value") or {}).get("rows") or []
        dt = (envs["dt"].get("value") or {}).get("rows") or []
        previous = (envs["previous"].get("value") or {}).get("rows") if envs["previous"]["ok"] else None
        hot_rank = (envs["hot"].get("value") or {}).get("ranks") or {}
        blocks = (envs["blocks"].get("value") or {}).get("blocks") or []

        # 4. 历史：快照摘要 + 昨日涨停池（带涨停原因）+ 近 10 日涨停池。
        history_rows = list(reversed(self.repository.history(limit=12, before=day)))
        history_payloads = {row.trade_date: row for row in history_rows}
        prev_row = history_payloads.get(prev_day) or (history_rows[-1] if history_rows else None)
        prev_payload = _decode(prev_row) or {}
        yesterday_rows = self.repository.pool_rows(prev_day, "zt")
        recent_dates = recent_trade_dates(day, 10)
        pool_history = self.repository.pool_history(recent_dates, "zt")
        recent_mains = self._recent_mains(history_rows)

        # 5. 周期。
        _report("analyzing_cycle", "计算五大指标并判定情绪周期")
        theme_result = build_themes(
            zt=zt, blocks=blocks, history=pool_history, previous=previous, yesterday_rows=yesterday_rows,
            recent_mains=recent_mains, hot_rank=hot_rank, rules=rules,
        )
        themes = theme_result["themes"]
        metrics = cycle_mod.compute_metrics(zt=zt, zb=zb, dt=dt, previous=previous, rules=rules)
        metrics["follow_rate"] = theme_result["follow_rate"]
        cycle_mod.attach_deltas(metrics, prev_payload.get("metrics"))
        stage = cycle_mod.classify_stage(metrics, prev_stage=getattr(prev_row, "stage", None), rules=rules)
        kobe = cycle_mod.kobe_phase(
            stage["key"], metrics,
            [{"stage": r.stage, "yzt_premium": r.yzt_premium} for r in history_rows],
        )

        # 6. 角色与候选。
        _report("selecting", "识别主线、龙头与七种战法候选")
        roles = assign_roles(
            zt=zt, themes=themes, stock_themes=theme_result["stock_themes"],
            max_height=int(metrics.get("max_height") or 0), hot_rank=hot_rank, rules=rules,
        )
        theme_by_key = {t["key"]: t for t in themes}
        y_max = max((int(r.get("y_boards") or 1) for r in previous or []), default=0)
        ctx = {
            "rules": rules,
            "metrics": metrics,
            "themes": themes,
            "theme_by_key": theme_by_key,
            "zt": zt,
            "roles": roles,
            "stock_themes": theme_result["stock_themes"],
            "previous": previous or [],
            "y_max_height": y_max,
            "yesterday_theme": self._yesterday_theme(yesterday_rows, themes),
            "stage_key": stage["key"],
            "kobe": kobe,
            "sub_new": (envs["sub_new"].get("value") or {}).get("rows") or [],
            "hot_rank": hot_rank,
        }
        candidates, excluded, flags = generate_candidates(ctx)

        # 7. 日 K（形态确认 + 股性）与主线新闻，并行。
        codes = self._kline_codes(candidates)
        side = run_parallel(
            {
                "klines": lambda: fetch_klines(
                    codes, day, limit=int(rules.get("kline_limit", 120)), timeout=KLINE_BUDGET_SEC - 5
                ),
                "news": lambda: fetch_theme_news(
                    [t["name"] for t in themes[: int(rules.get("news_themes", 4))]], timeout=NEWS_BUDGET_SEC
                ),
            },
            timeout=KLINE_BUDGET_SEC,
            workers=2,
        )
        envs["klines"] = side["klines"][0] or {"key": "klines", "ok": False, "error": side["klines"][1],
                                               "degraded": True, "degraded_reason": side["klines"][1], "value": {}}
        envs["news"] = side["news"][0] or {"key": "news", "ok": False, "error": side["news"][1],
                                           "degraded": True, "degraded_reason": side["news"][1], "value": {}}
        kline_rows = (envs["klines"].get("value") or {}).get("rows") or {}
        features = {code: analyze_kline(code, rows, today=day) for code, rows in kline_rows.items()}
        candidates = confirm_with_kline(candidates, features)
        self._attach_news(themes, (envs["news"].get("value") or {}).get("news") or {})

        for cand in candidates:
            score_candidate(cand, theme_by_key=theme_by_key, hot_rank=hot_rank, rules=rules)

        # 8. 复盘昨日推荐 → 两派战绩（r1 投票要用）。
        _report("reviewing", "复盘昨日推荐")
        review = self._review_previous(prev_day, day, zt=zt, zb=zb, dt=dt, previous=previous, persist=not intraday)
        track = self._track_record(day)

        max_actionable = int(getattr(self.config, "daban_max_actionable", 4) or 4)
        lanes = self._lanes(candidates, stage, kobe, max_actionable)
        verdict = decide_style(
            metrics=metrics, stage=stage, kobe=kobe, themes=themes, candidates=candidates,
            lanes=lanes, flags=flags, track_record=track, rules=rules,
        )

        # 9. 模型解读。
        _report("analyzing", "模型评估题材故事空间并写理由")
        llm_ctx = self._build_llm_context(day, metrics, stage, kobe, themes, candidates, lanes, verdict, recent_mains)
        conclusion = self.analyzer.analyze(llm_ctx)
        self._apply_conclusion(conclusion, themes, candidates)

        lanes = self._lanes(candidates, stage, kobe, max_actionable)
        consensus = mark_consensus(lanes)
        verdict = decide_style(
            metrics=metrics, stage=stage, kobe=kobe, themes=themes, candidates=candidates,
            lanes=lanes, flags=flags, track_record=track, rules=rules,
        )
        verdict["model_opinion"] = conclusion.get("verdict_opinion")
        for style in STYLES:
            text = (conclusion.get("lanes") or {}).get(style) or {}
            lanes[style]["commentary"] = text.get("commentary") or ""
            lanes[style]["commentary_principles"] = text.get("principle_ids") or []

        _report("finalizing", "汇总并落库")
        payload = {
            "schema_version": SCHEMA_VERSION,
            "kind": "full",
            "trade_date": day,
            "previous_trade_date": prev_day,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "intraday": intraday,
            "metrics": metrics,
            "stage": stage,
            "kobe_stage": kobe,
            "ladder": self._ladder(zt, roles, metrics),
            "themes": themes[:12],
            "verdict": verdict,
            "lanes": lanes,
            "consensus_codes": consensus,
            "excluded": excluded[:40],
            "review": review,
            "track_record": track,
            "conclusion": {
                "summary": conclusion.get("summary") or "",
                "risks": conclusion.get("risks") or [],
                "generated_by": conclusion.get("generated_by"),
                "model": conclusion.get("model") or "",
                "provider": conclusion.get("provider") or "",
                "dropped_mentions": conclusion.get("dropped_mentions") or [],
            },
            "principles": PRINCIPLES,
            "mode_labels": MODE_LABELS,
            "style_labels": STYLE_LABELS,
            "sources": {key: self._source_summary(env) for key, env in envs.items()},
            "disclaimers": {"investment": INVESTMENT_DISCLAIMER, "data": DATA_DISCLAIMER},
            "cached": False,
        }
        warnings = self._warnings(envs, conclusion, intraday)
        payload["warnings"] = warnings
        payload["degraded"] = any(not env.get("ok") for env in envs.values()) or conclusion.get("generated_by") != "llm"
        self._persist(payload, zt=zt, zb=zb, dt=dt, persist_pools=not intraday)
        return payload

    # ------------------------------------------------------------------ 抓取

    def _is_intraday(self, zt_env: Dict[str, Any], day: str) -> bool:
        if day != datetime.now().strftime("%Y-%m-%d"):
            return False
        status = str((zt_env.get("value") or {}).get("trade_status") or "")
        if status:
            return status != "closed"
        return datetime.now().strftime("%H%M") < "1505"

    def _fetch_rest(self, day: str) -> Dict[str, Dict[str, Any]]:
        t, bse = FETCH_TIMEOUT_SEC, self.include_bse
        tasks = {
            "zb": lambda: fetch_limit_pool("zb", day, timeout=t, include_bse=bse),
            "dt": lambda: fetch_limit_pool("dt", day, timeout=t, include_bse=bse),
            "ladder": lambda: fetch_ladder(day, timeout=t),
            "blocks": lambda: fetch_blocks(day, timeout=t + 10),
            "previous": lambda: fetch_em("previous", day, timeout=t, include_bse=bse),
            "strong": lambda: fetch_em("strong", day, timeout=t, include_bse=bse),
            "sub_new": lambda: fetch_em("sub_new", day, timeout=t, include_bse=bse),
            "hot": lambda: fetch_hot_rank(timeout=HOT_TIMEOUT_SEC),
        }
        results = run_parallel(tasks, timeout=FETCH_BUDGET_SEC, workers=len(tasks))
        envs: Dict[str, Dict[str, Any]] = {}
        for key, (env, error) in results.items():
            envs[key] = env or {
                "key": key, "ok": False, "source": "", "as_of": "", "degraded": True,
                "degraded_reason": error or "", "error": error, "value": {},
            }
        return envs

    def _backfill(self, day: str, *, progress: Callable[[str, str], None]) -> int:
        """库里缺的近 N 个交易日按日回补周期指标与池子（同花顺支持历史日期）。"""
        count = int(self.rules.get("backfill_days", 20) or 0)
        if count <= 0:
            return 0
        dates = recent_trade_dates(day, count)
        known = self.repository.known_trade_dates(dates)
        missing = [d for d in dates if d not in known]
        if not missing:
            return 0
        done = 0
        for index, target in enumerate(missing, start=1):
            progress("backfilling", f"回补历史 {index}/{len(missing)}：{target}")
            try:
                if self._backfill_one(target):
                    done += 1
            except Exception as exc:
                logger.warning("打板王：回补 %s 失败 %s", target, exc)
        return done

    def _backfill_one(self, day: str) -> bool:
        t, bse = FETCH_TIMEOUT_SEC, self.include_bse
        results = run_parallel(
            {
                "zt": lambda: fetch_limit_pool("zt", day, timeout=t, include_bse=bse),
                "zb": lambda: fetch_limit_pool("zb", day, timeout=t, include_bse=bse),
                "dt": lambda: fetch_limit_pool("dt", day, timeout=t, include_bse=bse),
                "ladder": lambda: fetch_ladder(day, timeout=t),
                "previous": lambda: fetch_em("previous", day, timeout=t, include_bse=bse),
            },
            timeout=FETCH_BUDGET_SEC,
            workers=5,
        )
        zt_env = results["zt"][0]
        if not zt_env or not zt_env.get("ok") or not zt_env["value"]["rows"]:
            return False
        zt = zt_env["value"]["rows"]
        apply_boards(zt, results["ladder"][0] or {}, source=zt_env["source"])
        zb = ((results["zb"][0] or {}).get("value") or {}).get("rows") or []
        dt = ((results["dt"][0] or {}).get("value") or {}).get("rows") or []
        prev_env = results["previous"][0] or {}
        previous = (prev_env.get("value") or {}).get("rows") if prev_env.get("ok") else None

        history_rows = list(reversed(self.repository.history(limit=12, before=day)))
        prev_row = history_rows[-1] if history_rows else None
        prev_payload = _decode(prev_row) or {}
        theme_result = build_themes(
            zt=zt, blocks=[], history=[], previous=None, yesterday_rows=[],
            recent_mains=[], hot_rank={}, rules=self.rules,
        )
        metrics = cycle_mod.compute_metrics(zt=zt, zb=zb, dt=dt, previous=previous, rules=self.rules)
        metrics["follow_rate"] = theme_result["follow_rate"]
        cycle_mod.attach_deltas(metrics, prev_payload.get("metrics"))
        stage = cycle_mod.classify_stage(metrics, prev_stage=getattr(prev_row, "stage", None), rules=self.rules)
        kobe = cycle_mod.kobe_phase(
            stage["key"], metrics, [{"stage": r.stage, "yzt_premium": r.yzt_premium} for r in history_rows]
        )
        mains = [t for t in theme_result["themes"] if t.get("is_main")]
        payload = {
            "schema_version": SCHEMA_VERSION,
            "kind": "backfill",
            "trade_date": day,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "metrics": metrics,
            "stage": stage,
            "kobe_stage": kobe,
            "ladder": self._ladder(zt, {}, metrics),
            "themes": [
                {k: t.get(k) for k in ("key", "name", "rank", "zt_count", "max_boards", "is_main", "tags")}
                for t in theme_result["themes"][:8]
            ],
            "principles": {},
            "disclaimers": {"investment": INVESTMENT_DISCLAIMER, "data": DATA_DISCLAIMER},
            "warnings": [] if previous is not None else ["昨日涨停今日表现不可用（东财只保留约 30 个交易日）"],
        }
        for pool, rows in (("zt", zt), ("zb", zb), ("dt", dt)):
            self.repository.save_pool_rows(day, pool, rows)
        self.repository.save_snapshot(self._snapshot_fields(payload, kind="backfill", mains=mains))
        return True

    # ------------------------------------------------------------------ 选股辅助

    @staticmethod
    def _recent_mains(history_rows) -> List[str]:
        names: List[str] = []
        for row in history_rows[-10:]:
            for name in str(row.main_themes or "").split("、"):
                name = name.strip()
                if name and name not in names:
                    names.append(name)
        return names

    @staticmethod
    def _yesterday_theme(yesterday_rows: List[Dict[str, Any]], themes: List[Dict[str, Any]]) -> Dict[str, str]:
        """昨日涨停股 → 今日题材 key（按涨停原因的标签匹配），给「首阴 / 核按钮」类候选归题材。"""
        mapping: Dict[str, str] = {}
        for row in yesterday_rows or []:
            tags = split_reason(row.get("reason") or "")
            for theme in themes:
                if any(theme["key"] in tag or tag in theme.get("tags", []) for tag in tags):
                    mapping[row["code"]] = theme["key"]
                    break
        return mapping

    def _kline_codes(self, candidates: List[Dict[str, Any]]) -> List[str]:
        """日 K 只拉一部分候选：形态确认必需的优先，其余按题材排名、板数取前若干。"""
        limit = int(self.rules.get("max_candidates", 40))
        must = [c["code"] for c in candidates if c.get("needs_kline_confirm") or c.get("origin") != "zt"]
        rest = sorted(
            (c for c in candidates if c["code"] not in must),
            key=lambda c: (c.get("theme_rank") or 99, -int(c.get("boards") or 0)),
        )
        codes: List[str] = []
        for code in must + [c["code"] for c in rest]:
            if code not in codes:
                codes.append(code)
        return codes[:limit]

    @staticmethod
    def _attach_news(themes: List[Dict[str, Any]], news: Dict[str, Dict[str, Any]]) -> None:
        for theme in themes:
            item = news.get(theme["name"])
            if item:
                theme["news_count"] = item.get("count")
                theme["news"] = item.get("items") or []
            space, reason = rule_story_space(theme)
            apply_story(theme, space, reason, "rule")

    def _lanes(self, candidates, stage, kobe, max_actionable) -> Dict[str, Dict[str, Any]]:
        return {
            style: build_lane(style, candidates, stage=stage, kobe=kobe, rules=self.rules, max_actionable=max_actionable)
            for style in STYLES
        }

    @staticmethod
    def _build_llm_context(day, metrics, stage, kobe, themes, candidates, lanes, verdict, recent_mains):
        best: Dict[str, Dict[str, Any]] = {}
        for cand in sorted(candidates, key=lambda c: -c["rule_score"]):
            best.setdefault(cand["code"], cand)
        return {
            "trade_date": day,
            "metrics": metrics,
            "stage": stage,
            "kobe_stage": kobe,
            "themes": themes[:MAX_LLM_THEMES],
            "candidates": list(best.values())[:MAX_LLM_CANDIDATES],
            "lanes": lanes,
            "verdict": verdict,
            "recent_mains": recent_mains,
        }

    def _apply_conclusion(self, conclusion, themes, candidates) -> None:
        by_key = {t["key"]: t for t in themes}
        for key, item in (conclusion.get("themes") or {}).items():
            theme = by_key.get(key)
            if not theme:
                continue
            if item.get("story_space"):
                apply_story(theme, item["story_space"], item.get("story_reason") or "", "llm")
            theme["commonality"] = item.get("commonality") or ""
            theme["same_as"] = [by_key[k]["name"] for k in item.get("same_as") or [] if k in by_key]
        notes = conclusion.get("candidates") or {}
        for cand in candidates:
            item = notes.get(cand["code"])
            if item:
                cand["llm_adjust"] = item.get("adjust", 0)
                cand["llm_adjust_reason"] = item.get("reason") or ""
                if item.get("note") and item.get("principle_ids"):
                    cand["llm_note"] = {"text": item["note"], "principle_ids": item["principle_ids"]}
            finalize_score(cand, self.rules)

    @staticmethod
    def _ladder(zt: List[Dict[str, Any]], roles: Dict[str, Dict[str, Any]], metrics: Dict[str, Any]) -> List[Dict[str, Any]]:
        tiers = {t["from"]: t for t in metrics.get("tiers") or []}
        groups: Dict[int, List[Dict[str, Any]]] = {}
        for row in zt:
            groups.setdefault(int(row.get("boards") or 1), []).append(row)
        ladder = []
        for boards in sorted(groups, reverse=True):
            tier = tiers.get(min(boards - 1, 4)) if boards >= 2 else None
            stocks = sorted(groups[boards], key=lambda r: r.get("first_time") or "999999")
            ladder.append({
                "boards": boards,
                "label": "首板" if boards == 1 else f"{boards} 板",
                "count": len(stocks),
                "promotion": tier,
                "stocks": [
                    {
                        "code": r["code"],
                        "name": r["name"],
                        "theme": (roles.get(r["code"]) or {}).get("theme") or "",
                        "role_label": (roles.get(r["code"]) or {}).get("role_label") or "",
                        "reason": r.get("reason") or "",
                        "first_time": r.get("first_time") or "",
                        "limit_up_type": r.get("limit_up_type") or "",
                        "open_num": int(r.get("open_num") or 0),
                        "seal_ratio": (
                            round(float(r["seal_amount"]) / float(r["circ_mv"]), 4)
                            if r.get("seal_amount") and r.get("circ_mv") else None
                        ),
                        "high_days": r.get("high_days") or "",
                    }
                    for r in stocks
                ],
            })
        return ladder

    # ------------------------------------------------------------------ 复盘

    def _review_previous(self, prev_day, day, *, zt, zb, dt, previous, persist: bool) -> Optional[Dict[str, Any]]:
        """昨日两条线的推荐，今天兑现得怎么样（次日涨跌幅、是否涨停）。"""
        picks = self.repository.picks_for(prev_day)
        if not picks:
            return None
        outcomes: Dict[str, Dict[str, Any]] = {}
        for row in previous or []:
            if row.get("change_pct") is not None:
                outcomes[row["code"]] = {"change_pct": round(float(row["change_pct"]), 2), "limit_up": False}
        for row in zb + dt:
            if row.get("change_pct") is not None:
                outcomes[row["code"]] = {"change_pct": round(float(row["change_pct"]), 2), "limit_up": False}
        for row in zt:
            outcomes[row["code"]] = {
                "change_pct": round(float(row.get("change_pct") or limit_pct(row["code"])), 2), "limit_up": True,
            }
        missing = sorted({p["code"] for p in picks} - set(outcomes))
        if missing:
            env = fetch_klines(missing, day, limit=5, timeout=20)
            for code, rows in ((env.get("value") or {}).get("rows") or {}).items():
                if rows and str(rows[-1].get("time") or "").replace("-", "") == day.replace("-", ""):
                    change = bar_changes(rows)[-1]
                    if change is not None:
                        outcomes[code] = {"change_pct": round(change, 2), "limit_up": change >= limit_pct(code) - 0.3}
        if persist:
            self.repository.update_pick_outcomes(prev_day, outcomes)

        summary: Dict[str, Any] = {"trade_date": prev_day, "reviewed_on": day}
        for style in STYLES:
            items = []
            for pick in picks:
                if pick["style"] != style:
                    continue
                outcome = outcomes.get(pick["code"]) or {}
                items.append({
                    "code": pick["code"],
                    "name": pick["name"],
                    "mode": pick["mode"],
                    "mode_label": MODE_LABELS.get(pick["mode"] or "", ""),
                    "action": pick["action"],
                    "change_pct": outcome.get("change_pct"),
                    "limit_up": outcome.get("limit_up"),
                })
            changes = [i["change_pct"] for i in items if i["change_pct"] is not None]
            summary[style] = {
                "count": len(items),
                "limit_up": sum(1 for i in items if i["limit_up"]),
                "avg_change": round(mean(changes), 2) if changes else None,
                "items": items,
            }
        return summary

    def _track_record(self, day: str) -> Dict[str, Any]:
        days = int(self.rules.get("track_record_days", 10))
        rows = self.repository.reviewed_picks(limit_days=days, before=day)
        out: Dict[str, Any] = {}
        for style in STYLES:
            mine = [r for r in rows if r["style"] == style and r["action"] in (ACTION_ACTIONABLE, ACTION_TRIAL)]
            changes = [float(r["next_day_change_pct"]) for r in mine if r["next_day_change_pct"] is not None]
            out[style] = {
                "days": len({r["trade_date"] for r in mine}),
                "picks": len(mine),
                "avg": round(mean(changes), 2) if changes else None,
                "limit_up_rate": round(sum(1 for r in mine if r["next_day_limit_up"]) / len(mine), 4) if mine else None,
            }
        return out

    # ------------------------------------------------------------------ 汇总与落库

    @staticmethod
    def _source_summary(env: Dict[str, Any]) -> Dict[str, Any]:
        value = env.get("value") or {}
        count = None
        for key in ("rows", "blocks", "ladder", "ranks", "news"):
            if isinstance(value.get(key), (list, dict)):
                count = len(value[key])
                break
        return {
            "key": env.get("key"),
            "label": env.get("label") or env.get("key"),
            "ok": bool(env.get("ok")),
            "source": env.get("source") or "",
            "as_of": env.get("as_of") or "",
            "degraded": bool(env.get("degraded")),
            "degraded_reason": env.get("degraded_reason") or "",
            "error": env.get("error"),
            "count": count,
        }

    @staticmethod
    def _warnings(envs, conclusion, intraday) -> List[str]:
        warnings: List[str] = []
        if intraday:
            warnings.append("盘中临时数据：涨停池仍在变化，情绪周期以收盘后的结果为准")
        for env in envs.values():
            label = env.get("label") or env.get("key")
            if not env.get("ok"):
                warnings.append(f"{label}不可用：{env.get('error')}")
            elif env.get("degraded") and env.get("degraded_reason"):
                warnings.append(f"{label}：{env['degraded_reason']}")
        warnings.extend(conclusion.get("warnings") or [])
        seen, unique = set(), []
        for item in warnings:
            if item and item not in seen:
                seen.add(item)
                unique.append(item)
        return unique

    @staticmethod
    def _snapshot_fields(payload: Dict[str, Any], *, kind: str, mains: List[Dict[str, Any]]) -> Dict[str, Any]:
        m = payload.get("metrics") or {}
        stage = payload.get("stage") or {}
        kobe = payload.get("kobe_stage") or {}
        conclusion = payload.get("conclusion") or {}
        return {
            "trade_date": payload["trade_date"],
            "kind": kind,
            "stage": stage.get("key"),
            "kobe_stage": kobe.get("key"),
            "fall_day": kobe.get("fall_day"),
            "zt_count": m.get("zt_count"),
            "zb_count": m.get("zb_count"),
            "dt_count": m.get("dt_count"),
            "lianban_count": m.get("lianban_count"),
            "max_height": m.get("max_height"),
            "promotion_rate": m.get("promotion_rate"),
            "yzt_premium": m.get("yzt_premium"),
            "big_loss_count": m.get("big_loss_count"),
            "zab_rate": m.get("zab_rate"),
            "main_themes": "、".join(t["name"] for t in mains[:3])[:255],
            "preferred_style": (payload.get("verdict") or {}).get("preferred"),
            "intraday": bool(payload.get("intraday")),
            "model": str(conclusion.get("model") or "")[:120] or None,
            "provider": str(conclusion.get("provider") or "")[:64] or None,
            "degraded": bool(payload.get("degraded")),
            "payload": json.dumps(payload, ensure_ascii=False),
        }

    def _persist(self, payload, *, zt, zb, dt, persist_pools: bool) -> None:
        """落库失败只记日志：写不进库不该让用户看不到这次的结果。"""
        day = payload["trade_date"]
        try:
            mains = [t for t in payload.get("themes") or [] if t.get("is_main")]
            self.repository.save_snapshot(self._snapshot_fields(payload, kind="full", mains=mains))
            if persist_pools:
                for pool, rows in (("zt", zt), ("zb", zb), ("dt", dt)):
                    self.repository.save_pool_rows(day, pool, rows)
                picks = []
                for style in STYLES:
                    lane = payload["lanes"][style]
                    for pick in lane["actionable"] + lane["trial"]:
                        picks.append({
                            "style": style, "code": pick["code"], "name": pick["name"],
                            "mode": pick["mode"], "action": pick["action"], "score": pick["score"],
                        })
                self.repository.save_picks(day, picks)
            retention = int(getattr(self.config, "daban_retention_days", 180) or 180)
            deleted = self.repository.apply_retention(retention)
            if deleted:
                logger.info("打板王：按 %d 天保留策略清理了 %d 条历史记录", retention, deleted)
        except Exception as exc:
            logger.warning("打板王：落库失败（不影响本次结果） %s", exc)


__all__ = ["DabanService", "DabanUnavailableError", "DabanUpstreamError", "apply_boards"]
