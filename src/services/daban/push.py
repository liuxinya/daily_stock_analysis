# -*- coding: utf-8 -*-
"""把「我是打板王」的当日结论推送到已配置的通知渠道，以及可选的盘后定时生成。

推送沿用 :class:`~src.notification.NotificationService` 的扇出与按交易日去重（``dedup_key``），
手动点一次、定时任务再触发一次不会发两条一样的消息。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.services.daban.constants import STYLE_LABELS
from src.services.daban.principles import PRINCIPLES

logger = logging.getLogger(__name__)

TICK_TASK_NAME = "daban_king_postmarket"
TICK_SECONDS = 600
POSTMARKET_AFTER = "1530"


def _cite(ids: List[str]) -> str:
    tags = [f"{PRINCIPLES[i]['author']}·{PRINCIPLES[i]['title']}" for i in ids or [] if i in PRINCIPLES]
    return f"（{'；'.join(tags[:2])}）" if tags else ""


def _fmt_pct(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:+.2f}%"


def render_markdown(payload: Dict[str, Any]) -> str:
    """IM 版摘要：周期、主线、打法裁决、两条线 Top 4 与触发条件。"""
    m = payload.get("metrics") or {}
    stage = payload.get("stage") or {}
    kobe = payload.get("kobe_stage") or {}
    verdict = payload.get("verdict") or {}
    lines: List[str] = [f"**交易日 {payload.get('trade_date') or '—'}**", ""]
    lines.append(f"**情绪周期：{stage.get('label', '—')}**（92科比视角：{kobe.get('label', '—')}）")
    promotion = m.get("promotion_rate")
    lines.append(
        f"涨停 {m.get('zt_count')}｜跌停 {m.get('dt_count')}｜连板 {m.get('lianban_count')} 支｜最高 {m.get('max_height')} 板｜"
        f"晋级率 {'—' if promotion is None else format(promotion, '.0%')}｜"
        f"昨日涨停今日 {_fmt_pct(m.get('yzt_premium'))}"
    )
    summary = (payload.get("conclusion") or {}).get("summary")
    if summary:
        lines += ["", summary]
    mains = [t for t in payload.get("themes") or [] if t.get("is_main")]
    if mains:
        lines += ["", "**主线题材**"]
        for theme in mains[:3]:
            leader = (theme.get("leader") or {}).get("name") or "—"
            lines.append(
                f"- {theme['name']}：涨停 {theme['zt_count']} 只，最高 {theme['max_boards']} 板（{leader}），"
                f"持续第 {theme['days']} 天，{theme.get('status_label', '')}"
            )
    lines += ["", f"**{verdict.get('label', '打法裁决')}**（陈小群 {verdict.get('votes', {}).get('chen', 0)} 票 / "
                  f"92科比 {verdict.get('votes', {}).get('kobe', 0)} 票）"]
    if verdict.get("summary"):
        lines.append(verdict["summary"])

    for style in ("chen", "kobe"):
        lane = (payload.get("lanes") or {}).get(style) or {}
        lines += ["", f"**{STYLE_LABELS[style]}｜{lane.get('stage_label', '—')}｜仓位上限 {lane.get('position_cap_label', '—')}**"]
        if lane.get("flat"):
            lines.append(f"- 空仓：{lane.get('flat_message')}{_cite(lane.get('flat_principles') or [])}")
        picks = (lane.get("actionable") or []) + (lane.get("trial") or [])
        for pick in picks[:4]:
            reason = (pick.get("reasons") or [{}])[0]
            trigger = (pick.get("triggers") or [{}])[0]
            lines.append(
                f"- {pick['code']} {pick['name']}｜{pick['mode_label']}｜{pick['action_label']}｜"
                f"{pick['score']:.0f} 分（{pick['grade']}）｜仓位 ≤{pick['position_pct']}%"
                + ("｜两派共识" if pick.get("consensus") else "")
            )
            if reason.get("text"):
                lines.append(f"  理由：{reason['text']}{_cite(reason.get('principle_ids'))}")
            if trigger.get("text"):
                lines.append(f"  次日条件：{trigger['text']}")
        if not picks and not lane.get("flat"):
            lines.append("- 暂无达标标的")

    risks = (payload.get("conclusion") or {}).get("risks") or []
    if risks:
        lines += ["", "**风险提示**"] + [f"- {r}" for r in risks[:4]]
    disclaimer = (payload.get("disclaimers") or {}).get("investment")
    if disclaimer:
        lines += ["", f"> {disclaimer}"]
    return "\n".join(lines).strip()


class DabanPusher:
    def __init__(self, notifier=None, config=None):
        from src.config import get_config

        self.config = config or get_config()
        self._notifier = notifier

    @property
    def notifier(self):
        if self._notifier is None:
            from src.notification import NotificationService

            self._notifier = NotificationService()
        return self._notifier

    def push(self, payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """**永不抛异常**，失败以 ``ok=False`` 返回。"""
        if not bool(getattr(self.config, "daban_push_enabled", False)):
            return {"ok": False, "skipped": True, "message": "推送未启用：请把 .env 里的 DABAN_PUSH_ENABLED 设为 true"}
        if not payload:
            return {"ok": False, "skipped": True, "message": "还没有可推送的结果，请先生成"}
        trade_date = str(payload.get("trade_date") or "").strip()
        try:
            from src.notification import NotificationBuilder

            content = NotificationBuilder.build_simple_alert(
                title=f"我是打板王 · 情绪周期与两派推荐（{trade_date or '—'}）",
                content=render_markdown(payload),
                alert_type="info",
            )
            result = self.notifier.send_with_results(
                content, route_type="report", dedup_key=f"daban-king:{trade_date}" if trade_date else None,
            )
            ok = bool(getattr(result, "success", False))
            return {
                "ok": ok,
                "skipped": False,
                "message": "推送已发送" if ok else "推送失败：没有渠道发送成功，请检查通知渠道配置",
                "trade_date": trade_date,
            }
        except Exception as exc:
            logger.warning("打板王：推送失败 %s: %s", type(exc).__name__, exc)
            return {"ok": False, "skipped": False, "message": f"推送异常：{type(exc).__name__}: {str(exc)[:200]}"}


# ---------------------------------------------------------------------- 盘后定时生成


def run_postmarket_tick(*, config=None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """交易日 15:30 之后、当天还没有正式（非盘中）快照时生成一次，按配置推送。"""
    from src.config import get_config
    from src.core.trading_calendar import is_market_open

    config = config or get_config()
    if not bool(getattr(config, "daban_enabled", True)) or not bool(getattr(config, "daban_auto_generate", False)):
        return {"ok": True, "generated": False, "reason": "disabled"}
    now = now or datetime.now()
    if not is_market_open("cn", now.date()) or now.strftime("%H%M") < POSTMARKET_AFTER:
        return {"ok": True, "generated": False, "reason": "not_postmarket"}

    from src.repositories.daban_repo import DabanRepository
    from src.services.daban.jobs import get_job_manager

    today = now.strftime("%Y-%m-%d")
    row = DabanRepository().snapshot_for_trade_date(today)
    if row is not None and row.kind == "full" and not row.intraday:
        return {"ok": True, "generated": False, "reason": "already_generated"}
    try:
        result = get_job_manager().run_sync(trade_date=today)
    except Exception as exc:
        return {"ok": False, "generated": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    if not result.get("ok"):
        return {"ok": True, "generated": False, "reason": result.get("message")}
    push = None
    if bool(getattr(config, "daban_auto_push", False)):
        push = DabanPusher(config=config).push(result.get("payload"))
    return {"ok": True, "generated": True, "push": push}


def build_daban_background_tasks(config, *, config_provider) -> List[Dict[str, Any]]:
    """与 market brief 的 builder 同构，结构对应 ``Scheduler.add_background_task`` 的 kwargs。"""
    if not bool(getattr(config, "daban_enabled", True)) or not bool(getattr(config, "daban_auto_generate", False)):
        return []

    def daban_task() -> None:
        result = run_postmarket_tick(config=config_provider())
        if result.get("generated"):
            logger.info("[打板王] 已生成盘后复盘，推送：%s", (result.get("push") or {}).get("message"))
        elif not result.get("ok"):
            logger.warning("[打板王] 盘后生成失败：%s", result.get("error"))

    return [{
        "task": daban_task,
        "interval_seconds": TICK_SECONDS,
        "run_immediately": False,
        "name": TICK_TASK_NAME,
    }]


__all__ = [
    "DabanPusher",
    "TICK_SECONDS",
    "TICK_TASK_NAME",
    "build_daban_background_tasks",
    "render_markdown",
    "run_postmarket_tick",
]
