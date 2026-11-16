# -*- coding: utf-8 -*-
"""把「我是打板王」的当日结论推送到已配置的通知渠道，以及可选的盘后定时生成。

推送沿用 :class:`~src.notification.NotificationService` 的扇出与按交易日去重（``dedup_key``），
手动点一次、定时任务再触发一次不会发两条一样的消息。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.services.daban.principles import PRINCIPLES

logger = logging.getLogger(__name__)

TICK_TASK_NAME = "daban_king_postmarket"
TICK_SECONDS = 600
POSTMARKET_AFTER = "1530"
PREOPEN_BEFORE = "0900"  # 开盘前（以及非交易日）补生成上一个交易日漏掉的盘后复盘


def _cite(ids: List[str]) -> str:
    tags = [f"{PRINCIPLES[i]['author']}·{PRINCIPLES[i]['title']}" for i in ids or [] if i in PRINCIPLES]
    return f"（{'；'.join(tags[:2])}）" if tags else ""


def _fmt_pct(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:+.2f}%"


def render_markdown(payload: Dict[str, Any]) -> str:
    """IM 版摘要：周期、主线、世界观边界与节点、今天「重点」推荐的成绩、明天的观察清单。"""
    m = payload.get("metrics") or {}
    stage = payload.get("stage") or {}
    kobe = payload.get("kobe_stage") or {}
    worldview = payload.get("worldview") or {}
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
    if worldview:
        lines += ["", f"**明天（{worldview.get('for_date') or '—'}）的边界**：{worldview.get('boundary_text') or '—'}"]
        node = next((d for d in worldview.get("dims") or [] if d.get("key") == "node"), None)
        if node and node.get("value") and node["value"] != "无节点":
            lines.append(f"节点：{node.get('detail') or node['value']}")
    mains = [t for t in payload.get("themes") or [] if t.get("is_main")]
    if mains:
        lines += ["", "**主线题材**"]
        for theme in mains[:3]:
            leader = (theme.get("leader") or {}).get("name") or "—"
            common = (theme.get("commonality") or {}).get("label")
            lines.append(
                f"- {theme['name']}：涨停 {theme['zt_count']} 只，最高 {theme['max_boards']} 板（{leader}），"
                f"持续第 {theme['days']} 天，{theme.get('status_label', '')}" + (f"，{common}" if common else "")
            )
            if theme.get("expectation_text"):
                lines.append(f"  预期：{theme['expectation_text']}")
    review = payload.get("focus_review") or {}
    if review.get("count"):
        done = [i for i in review.get("items") or [] if i.get("h1_ret") is not None]
        sealed = sum(1 for i in review.get("items") or [] if i.get("sealed_close"))
        lines += ["", f"**今天「重点」推荐 {review['count']} 只**：收盘封住 {sealed} 只"
                      + (f"，已结案 {len(done)} 只" if done else "")]
        for item in (review.get("items") or [])[:6]:
            lines.append(f"- {item['code']} {item.get('name') or ''}｜{item.get('theme_name') or '—'}｜"
                         f"初始 {item.get('init_score')} 分→收盘 {item.get('close_score')} 分｜成本 {item.get('cost')}"
                         + ("｜封住" if item.get("sealed_close") else ""))
    watch = payload.get("watchlist") or {}
    if watch.get("items"):
        lines += ["", "**明天观察清单**（盘后不推荐，明天盘中满足条件时由雷达统一评分）"]
        for item in watch["items"][:6]:
            trigger = (item.get("triggers") or [{}])[0]
            lines.append(f"- {item['code']} {item['name']}｜{'/'.join(x for x in item.get('modes') or [] if x)}｜"
                         f"{item.get('theme_name') or '—'}")
            if trigger.get("text"):
                lines.append(f"  触发：{trigger['text']}{_cite(trigger.get('principle_ids'))}")
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
                title=f"我是打板王 · 短线世界观与次日观察（{trade_date or '—'}）",
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
    """交易日 15:30 之后、当天还没有正式（非盘中）快照时生成一次，按配置推送。

    补生成：开盘前（09:00 之前）和非交易日，上一个交易日收盘后没生成过（服务当时没开 / 生成失败）就补上，
    第二天开盘时世界观和次日观察清单才有。交易时段内不补：题材榜、人气榜只有「当下」，补出来的不是那天收盘的样子。
    """
    from src.config import get_config
    from src.core.trading_calendar import is_market_open

    config = config or get_config()
    if not bool(getattr(config, "daban_enabled", True)) or not bool(getattr(config, "daban_auto_generate", False)):
        return {"ok": True, "generated": False, "reason": "disabled"}
    now = now or datetime.now()
    today = now.strftime("%Y-%m-%d")
    hhmm = now.strftime("%H%M")
    trading = is_market_open("cn", now.date())
    if trading and hhmm >= POSTMARKET_AFTER:
        target, catch_up = today, False
    elif not trading or hhmm < PREOPEN_BEFORE:
        from src.services.daban.fetchers import previous_trade_date

        target, catch_up = previous_trade_date(today), True
    else:
        return {"ok": True, "generated": False, "reason": "not_postmarket"}

    from src.repositories.daban_repo import DabanRepository
    from src.services.daban.jobs import get_job_manager

    row = DabanRepository().snapshot_for_trade_date(target)
    if row is not None and row.kind == "full" and not row.intraday:
        return {"ok": True, "generated": False, "reason": "already_generated", "trade_date": target}
    try:
        result = get_job_manager().run_sync(trade_date=target)
    except Exception as exc:
        return {"ok": False, "generated": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    if not result.get("ok"):
        return {"ok": True, "generated": False, "reason": result.get("message")}
    push = None
    if bool(getattr(config, "daban_auto_push", False)):
        push = DabanPusher(config=config).push(result.get("payload"))
    return {"ok": True, "generated": True, "trade_date": target, "catch_up": catch_up, "push": push}


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

    # 服务启动时先检查一次（后台线程里跑，不挡启动）：开盘前重启服务也来得及补上上一个交易日的复盘。
    return [{
        "task": daban_task,
        "interval_seconds": TICK_SECONDS,
        "run_immediately": True,
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
