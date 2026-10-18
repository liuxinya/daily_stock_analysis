# -*- coding: utf-8 -*-
"""把行业资金结论推送到已配置的通知渠道。

用项目既有的 :class:`~src.notification.NotificationService`，它会**扇出到 ``.env`` 里
所有已配置的渠道**（钉钉 / 飞书 / Telegram / 邮件 …），所以这里不需要知道具体推给谁。

推送内容带 ``dedup_key``（按交易日），同一天重复推送会被通知层去重 —— 手动点一次、
定时任务再触发一次不会给用户发两条一样的消息。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def render_markdown(payload: Dict[str, Any]) -> str:
    """把一份快照 payload 渲染成适合 IM 阅读的 markdown。

    刻意做得比页面短：IM 里没人会读 90 行行业表，所以只给大盘结论、前 5 个关注方向
    和候选个股，以及必要的风险提示。
    """
    market = payload.get("market") or {}
    industries = (payload.get("industries") or {}).get("industries") or []
    candidates = payload.get("candidates") or {}
    conclusion = payload.get("conclusion") or {}
    disclaimers = payload.get("disclaimers") or {}

    lines: List[str] = []
    lines.append(f"**交易日 {payload.get('trade_date') or '—'}**")
    lines.append("")

    score = market.get("score")
    if score is None:
        lines.append("**复合信号：数据不足，无法给出判断**")
    else:
        thresholds = market.get("thresholds") or {}
        lines.append(
            f"**复合信号：{score} / 100（{market.get('verdict_label')}）**"
            f"　偏多 ≥{thresholds.get('bullish')}，偏空 ≤{thresholds.get('bearish')}"
        )
    lines.append("")

    if conclusion.get("summary"):
        lines.append(str(conclusion["summary"]))
        lines.append("")

    lines.append("**五因子明细**")
    for factor in market.get("factors") or []:
        score_text = "不可用" if factor.get("score") is None else f"{factor['score']:.1f}/20"
        lines.append(
            f"- {factor.get('label')}（权重 {float(factor.get('weight') or 0):.0%}）："
            f"{score_text}　{factor.get('raw_display')}"
        )
    lines.append("")

    directions = conclusion.get("directions") or []
    if directions:
        lines.append("**关注方向**")
        stance_labels = {"bullish": "偏多", "neutral": "中性", "bearish": "偏空"}
        for item in directions[:5]:
            lines.append(
                f"- {item.get('name')}（{stance_labels.get(item.get('stance'), '中性')}）："
                f"{item.get('reason')}"
            )
        lines.append("")
    elif industries:
        lines.append("**行业资金净流入前 5**")
        for row in industries[:5]:
            lines.append(
                f"- {row.get('name')}：净额 {row.get('net')} 亿元，"
                f"5 日累计 {row.get('net_5d')} 亿元，领涨股 {row.get('leader')}"
            )
        lines.append("")

    rows = candidates.get("candidates") or []
    if rows:
        lines.append("**关注个股（规则筛选，非推荐）**")
        for row in rows[:8]:
            metrics = row.get("metrics") or {}
            bits: List[str] = []
            if metrics.get("institution_net") is not None:
                bits.append(f"机构净买 {metrics['institution_net'] / 1e8:,.2f} 亿元")
            if metrics.get("main_net") is not None:
                bits.append(f"当日资金净额 {metrics['main_net'] / 1e8:+,.2f} 亿元")
            if metrics.get("industry"):
                bits.append(f"所属行业 {metrics['industry']}")
            lines.append(
                f"- {row.get('code')} {row.get('name')}｜{' + '.join(row.get('reasons') or [])}"
                + (f"｜{'；'.join(bits)}" if bits else "")
            )
        lines.append("")

    if conclusion.get("stock_notes"):
        lines.append("**个股解读**")
        for item in conclusion["stock_notes"][:8]:
            lines.append(f"- {item.get('code')} {item.get('name')}：{item.get('note')}")
        lines.append("")

    risks = conclusion.get("risks") or []
    if risks:
        lines.append("**风险提示**")
        for risk in risks:
            lines.append(f"- {risk}")
        lines.append("")

    if payload.get("degraded") and payload.get("warnings"):
        lines.append("**数据完整性提示**")
        for warning in (payload.get("warnings") or [])[:5]:
            lines.append(f"- {warning}")
        lines.append("")

    if disclaimers.get("flow"):
        lines.append(f"> {disclaimers['flow']}")
        lines.append("")
    if disclaimers.get("investment"):
        lines.append(f"> {disclaimers['investment']}")

    return "\n".join(lines).strip()


class IndustryFundsPusher:
    """把结论推送到通知渠道。"""

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
        """推送一份快照。**永不抛异常**，失败以 ``ok=False`` 返回给接口层。"""
        if not bool(getattr(self.config, "industry_funds_push_enabled", False)):
            return {
                "ok": False,
                "skipped": True,
                "message": "推送未启用：请把 .env 里的 INDUSTRY_FUNDS_PUSH_ENABLED 设为 true",
            }
        if not payload:
            return {"ok": False, "skipped": True, "message": "还没有可推送的快照，请先刷新"}

        trade_date = str(payload.get("trade_date") or "").strip()
        try:
            from src.notification import NotificationBuilder

            body = render_markdown(payload)
            content = NotificationBuilder.build_simple_alert(
                title=f"行业资金 · 主力资金与现阶段方向（{trade_date or '—'}）",
                content=body,
                alert_type="info",
            )
            result = self.notifier.send_with_results(
                content,
                route_type="report",
                dedup_key=f"industry-funds:{trade_date}" if trade_date else None,
            )
            ok = bool(getattr(result, "success", False))
            return {
                "ok": ok,
                "skipped": False,
                "message": "推送已发送" if ok else "推送失败：没有渠道发送成功，请检查 .env 里的通知渠道配置",
                "trade_date": trade_date,
            }
        except Exception as exc:
            logger.warning("行业资金：推送失败 %s: %s", type(exc).__name__, exc)
            return {
                "ok": False,
                "skipped": False,
                "message": f"推送异常：{type(exc).__name__}: {str(exc)[:200]}",
            }


__all__ = ["IndustryFundsPusher", "render_markdown"]
