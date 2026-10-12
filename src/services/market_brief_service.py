# -*- coding: utf-8 -*-
"""消息面汇总的编排层：定时点判断、取数、调用分析、读最新结果。"""

from __future__ import annotations

import logging
from datetime import datetime, time as dtime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from src.config import Config, get_config
from src.services.market_brief_window import (
    MODE_HOLIDAY,
    MODE_INTRADAY,
    MODE_POSTMARKET,
    MODE_PREMARKET,
    MODE_UNKNOWN,
    MODE_WEEKEND,
    BriefWindow,
    resolve_brief_window,
)

logger = logging.getLogger(__name__)


class MarketBriefServiceError(ValueError):
    """入参非法。由 API 层映射为 400。"""


def _parse_hhmm(raw: Any, default: str) -> dtime:
    text = str(raw or "").strip() or default
    try:
        hour, minute = text.split(":", 1)
        return dtime(hour=int(hour), minute=int(minute))
    except (TypeError, ValueError):
        logger.warning("Invalid market brief time %r; fallback to %s", raw, default)
        hour, minute = default.split(":", 1)
        return dtime(hour=int(hour), minute=int(minute))


class MarketBriefService:
    def __init__(self, *, config: Optional[Config] = None, analyzer=None, repository=None, intelligence=None,
                 realtime_news=None):
        self.config = config or get_config()
        self._analyzer = analyzer
        self._repository = repository
        self._intelligence = intelligence
        self._realtime_news = realtime_news

    @property
    def repository(self):
        if self._repository is None:
            from src.repositories.market_brief_repo import MarketBriefRepository

            self._repository = MarketBriefRepository()
        return self._repository

    @property
    def analyzer(self):
        if self._analyzer is None:
            from src.services.market_brief_analyzer import MarketBriefAnalyzer

            self._analyzer = MarketBriefAnalyzer(repository=self.repository, config=self.config)
        return self._analyzer

    @property
    def intelligence(self):
        if self._intelligence is None:
            from src.services.intelligence_service import IntelligenceService

            self._intelligence = IntelligenceService(config=self.config)
        return self._intelligence

    @property
    def realtime_news(self):
        if self._realtime_news is None:
            from src.services.realtime_news_service import RealtimeNewsService

            self._realtime_news = RealtimeNewsService(config=self.config)
        return self._realtime_news

    @property
    def live_fetch_budget(self) -> float:
        """汇总时直拉快讯接口的整轮预算（秒）；``<= 0`` 表示关闭直拉、只读资讯池。"""
        try:
            return max(0.0, float(getattr(self.config, "market_brief_live_fetch_timeout_sec", 30)))
        except (TypeError, ValueError):  # pragma: no cover - 配置层已 parse 过
            return 30.0

    @property
    def max_items(self) -> int:
        return max(1, int(getattr(self.config, "market_brief_max_items", 300)))

    # ------------------------------------------------------------------ public

    def current_window(self, *, current_time: Optional[datetime] = None) -> BriefWindow:
        return resolve_brief_window(
            market="cn",
            current_time=current_time,
            min_window_minutes=int(getattr(self.config, "market_brief_min_window_minutes", 90)),
        )

    def load_items(self, window: BriefWindow) -> List[Dict[str, Any]]:
        """取窗口内的 A 股资讯条目（**只读本地资讯池**，不打上游）。

        ``undated_fallback=True``：财联社热门、雪球热门股这类源不提供发布时间，
        默认的严格过滤会把它们整体排除掉；消息面汇总需要它们，用 fetched_at 作代理。
        个股/大盘分析仍走默认的严格语义，不受影响。
        """
        page_size = max(1, min(self.max_items, 100))
        collected: List[Dict[str, Any]] = []
        page = 1
        while len(collected) < self.max_items:
            payload = self.intelligence.list_items(
                scope_type="market",
                market="cn",
                published_since=window.start.replace(tzinfo=None),
                published_until=window.end.replace(tzinfo=None),
                undated_fallback=True,
                page=page,
                page_size=page_size,
            )
            items = payload.get("items") or []
            collected.extend(items)
            if len(items) < page_size or len(collected) >= int(payload.get("total") or 0):
                break
            page += 1
        return collected

    def load_live_items(self, window: BriefWindow) -> Tuple[List[Dict[str, Any]], List[str]]:
        """汇总那一刻直接问各快讯接口要窗口内的消息，返回 (条目, 失败平台)。

        这是主取数路径。只靠资讯池的话，窗口能不能填满完全取决于「之前有没有常驻
        进程一直在采」——各家接口单轮只覆盖最近 0.5~4 小时且多数没有历史翻页，
        一旦没采，休市窗口就永久空缺（这正是周末汇总只能看到周五收盘那 5 分钟的原因）。

        整轮失败不抛给调用方：直拉是增强，拉不到就退回资讯池里已有的内容。
        """
        budget = self.live_fetch_budget
        if budget <= 0:
            return [], []
        from src.services.market_brief_material import entries_to_items

        try:
            entries, failed = self.realtime_news.fetch_window_entries(
                since=window.start, until=window.end, budget_sec=budget
            )
        except Exception as exc:
            logger.warning("消息面汇总直拉快讯整轮失败: %s", exc, exc_info=True)
            return [], ["*"]
        return entries_to_items(entries), failed

    def load_material(
        self, window: BriefWindow, *, progress=None
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        """窗口内的最终素材 = 直拉 + 资讯池，去重后超量时分层抽样。

        直拉放在前面：同一条消息两边都有时保留直拉那份，它的发布时间直接来自站点。
        """
        from src.services.market_brief_material import merge_items, stratified_sample

        self._emit(progress, phase="fetching", message="正在拉取各平台窗口内的快讯")
        live_items, failed = self.load_live_items(window)

        self._emit(progress, phase="reading", message="正在读取窗口内资讯")
        pool_items = self.load_items(window)

        merged = merge_items(live_items, pool_items)
        selected = stratified_sample(
            merged,
            max_items=self.max_items,
            # 分桶边界按资讯池那套 naive 约定取（和 load_items 的查询边界一致）。
            # 窗口是市场本地时间、池里是宿主本地时间，非上海时区的宿主上两者会错开
            # 时差 —— 这是资讯池既有的约定不一致，这里只跟随它：窗口过滤已经在
            # fetch_window_entries 里按 aware 上海时间做完，错开只会让分桶略不均匀，
            # 不会丢素材。
            window_start=window.start.replace(tzinfo=None),
            window_end=window.end.replace(tzinfo=None),
        )
        logger.info(
            "[MarketBrief] 素材 直拉=%d 资讯池=%d 去重后=%d 送模型=%d 失败平台=%s",
            len(live_items), len(pool_items), len(merged), len(selected), failed or "无",
        )
        return selected, failed

    @staticmethod
    def _emit(progress, **fields: Any) -> None:
        """进度回调是可选的，且不该因为回调自身出错而中断取数。"""
        if progress is None:
            return
        try:
            progress(**fields)
        except Exception:  # pragma: no cover - defensive
            logger.debug("Market brief progress callback failed", exc_info=True)

    def ensure_items(self, window: BriefWindow) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """取窗口内素材；为空时先就地采集一轮再重试。

        这是用户显式触发路径（页面按钮 / API 提交）的兜底：定时采集没开、或者刚开盘
        窗口里还没攒下东西时，不该直接把用户挡在 400 上。与 CCTV 模块提交分析前
        先同步一次是同一套做法。
        """
        items = self.load_items(window)
        if items:
            return items, {"collected": False}

        from src.services.intelligence_collector import run_collect_once

        # force=True：这是明确的用户意图，不受 NEWS_INTEL_COLLECT_ENABLED 定时开关限制
        collect_result = run_collect_once(config=self.config, force=True)
        return self.load_items(window), {"collected": True, "collect": collect_result}

    def load_items_or_raise(
        self, window: BriefWindow, *, progress=None
    ) -> List[Dict[str, Any]]:
        """取窗口内素材；仍为空就抛出带真实原因的 ``MarketBriefAnalyzerError``。

        三个入口（API 提交、后台 job、定时/CLI ``generate``）共用这一个判断：否则同一个
        「窗口为空」语义会在不同入口给出不同的提示，而只有 API 那条拿得到就地采集的结果。
        """
        from src.services.market_brief_analyzer import MarketBriefAnalyzerError

        items, failed = self.load_material(window, progress=progress)
        if items:
            return items
        # 直拉和资讯池都空了，才去动那条「强制采集一轮」的老兜底
        items, info = self.ensure_items(window)
        if not items:
            raise MarketBriefAnalyzerError(
                self.empty_window_message(
                    window, collect=info.get("collect"), failed_platforms=failed
                )
            )
        return items

    def empty_window_message(
        self,
        window: BriefWindow,
        *,
        collect: Optional[Dict[str, Any]] = None,
        failed_platforms: Optional[List[str]] = None,
    ) -> str:
        """窗口为空时给出**对得上实际原因**的提示，而不是一律叫用户去开采集开关。

        ``collect`` 是 ``ensure_items`` 那一轮就地采集的结果，``failed_platforms`` 是
        直拉时挂掉的平台。整轮采集全失败（代理劫持、DNS、上游集体不可用）和
        「采集开关没开」是两种完全不同的故障，但以前都只会看到同一句
        「建议开启 NEWS_INTEL_COLLECT_ENABLED」，把人往错的方向带。
        """
        from src.services.intelligence_collector import collect_enabled

        newest = self.repository_newest_item_time()
        parts = [f"{window.label}窗口（{window.start:%m-%d %H:%M} 至 {window.end:%m-%d %H:%M}）内没有可用的资讯条目"]
        if newest is not None:
            parts.append(f"资讯池里最新一条是 {newest:%m-%d %H:%M}")

        live_failure = self._describe_live_failure(failed_platforms)
        if live_failure:
            parts.append(live_failure)

        collect_failure = self._describe_collect_failure(collect)
        if collect_failure:
            parts.append(collect_failure)
        elif not collect_enabled(self.config):
            parts.append(
                "刚才已就地采集过一轮仍然为空；建议开启 NEWS_INTEL_COLLECT_ENABLED 让采集持续增量落库，"
                "否则休市或开盘初期的窗口经常是空的"
            )
        else:
            parts.append("定时采集已开启，可能是上游暂时没有新内容，稍后再试")
        return "；".join(parts) + "。"

    def _describe_live_failure(self, failed_platforms: Optional[List[str]]) -> Optional[str]:
        """直拉有平台挂掉时说清楚是哪几个；全挂通常是网络/代理问题而不是上游没消息。"""
        if not failed_platforms:
            return None
        if failed_platforms == ["*"]:
            return "汇总时直拉快讯接口整轮失败；请检查网络与代理设置（参见实时新闻页的 503 排障）"
        from src.services.realtime_news_service import PLATFORM_BY_KEY

        labels = [
            PLATFORM_BY_KEY[key].label if key in PLATFORM_BY_KEY else key
            for key in failed_platforms
        ]
        return f"直拉快讯时 {('、'.join(labels))} 没拉到内容"

    @staticmethod
    def _describe_collect_failure(collect: Optional[Dict[str, Any]]) -> Optional[str]:
        """就地采集这一轮本身就失败时，返回一句说明；否则返回 None。"""
        if not collect:
            return None
        if not collect.get("ok"):
            error = str(collect.get("error") or "").strip()
            suffix = f"（{error}）" if error else ""
            return f"刚才的就地采集整轮失败{suffix}；请先检查网络与资讯源配置，再重试"
        sources = int(collect.get("sources") or 0)
        failed = int(collect.get("failed") or 0)
        if failed <= 0:
            return None
        first_error = str(collect.get("first_error") or "").strip()
        suffix = f"，首个错误：{first_error}" if first_error else ""
        return (
            f"刚才就地采集了 {sources} 个资讯源，其中 {failed} 个失败{suffix}；"
            "请到设置页检查资讯源状态与网络（含代理设置），修好后再重试"
        )

    def repository_newest_item_time(self) -> Optional[datetime]:
        """资讯池里最新一条的时间，用于把空窗口的原因说清楚。"""
        try:
            payload = self.intelligence.list_items(
                scope_type="market", market="cn", undated_fallback=True, page=1, page_size=1,
            )
        except Exception:
            return None
        items = payload.get("items") or []
        if not items:
            return None
        raw = items[0].get("published_at") or items[0].get("fetched_at")
        if not raw:
            return None
        try:
            return datetime.fromisoformat(str(raw))
        except (TypeError, ValueError):
            return None

    def generate(
        self,
        *,
        current_time: Optional[datetime] = None,
        force: bool = False,
        progress=None,
    ) -> Dict[str, Any]:
        window = self.current_window(current_time=current_time)
        items = self.load_items_or_raise(window, progress=progress)
        return self.analyzer.analyze(window=window, items=items, force=force, progress=progress)

    def latest(self, *, mode: Optional[str] = None) -> Optional[Dict[str, Any]]:
        return self.analyzer.latest(mode=mode)

    def should_generate(
        self, *, current_time: Optional[datetime] = None
    ) -> Tuple[bool, str, Optional[BriefWindow]]:
        """定时任务的判重与时点判断，返回 (是否该生成, 原因, 窗口)。"""
        if not bool(getattr(self.config, "market_brief_enabled", False)):
            return False, "disabled", None

        window = self.current_window(current_time=current_time)
        now = window.now
        if window.mode == MODE_UNKNOWN:
            # 时段判定不出来时不出稿：宁可没有，也不要给一份基于错误窗口的汇总
            return False, "mode_unknown", window

        if window.mode == MODE_INTRADAY:
            interval = max(10, int(getattr(self.config, "market_brief_intraday_interval_minutes", 60)))
            last = self.repository.latest_created_at(mode=MODE_INTRADAY)
            if last is not None and last >= now.replace(tzinfo=None) - timedelta(minutes=interval):
                return False, "intraday_interval_not_reached", window
            return True, "intraday_due", window

        if window.mode == MODE_PREMARKET:
            target = _parse_hhmm(getattr(self.config, "market_brief_premarket_time", None), "08:40")
        elif window.mode in (MODE_WEEKEND, MODE_HOLIDAY):
            target = _parse_hhmm(getattr(self.config, "market_brief_closed_time", None), "09:30")
        elif window.mode == MODE_POSTMARKET:
            # 盘后在收盘后任意时刻都可以出，一个窗口只出一次
            target = dtime(hour=0, minute=0)
        else:  # pragma: no cover - 上面已穷举
            return False, f"unsupported_mode:{window.mode}", window

        if now.timetz().replace(tzinfo=None) < target:
            return False, "time_not_reached", window
        existing = self.repository.find_for_window(
            mode=window.mode, window_start=window.start.replace(tzinfo=None)
        )
        if existing is not None:
            return False, "already_generated", window
        return True, f"{window.mode}_due", window


BRIEF_TASK_NAME = "market_brief_generate"
# 定时任务按固定间隔轮询，具体出稿时点由 should_generate 判断。
# 10 分钟足够贴近配置的时点，又不会频繁查库。
BRIEF_TICK_SECONDS = 10 * 60


def run_brief_tick(*, config: Optional[Config] = None) -> Dict[str, Any]:
    """一次定时检查：到点且本窗口还没出过稿就生成一份。"""
    service = MarketBriefService(config=config or get_config())
    should, reason, window = service.should_generate()
    if not should:
        return {"ok": True, "generated": False, "reason": reason}

    try:
        payload = service.generate()
    except Exception as exc:
        logger.warning("消息面汇总定时生成失败: %s", exc)
        return {"ok": False, "generated": False, "reason": reason, "error": str(exc)}

    return {
        "ok": True,
        "generated": True,
        "reason": reason,
        "mode": window.mode if window else None,
        "degraded": bool(payload.get("degraded")),
        "item_count": int(payload.get("item_count") or 0),
    }


def build_market_brief_background_tasks(config: Config, *, config_provider) -> List[Dict[str, Any]]:
    """构造定时生成任务，结构与 ``add_background_task`` 的 kwargs 一致。"""
    if not bool(getattr(config, "market_brief_enabled", False)):
        return []

    def brief_task() -> None:
        # 每轮重新读配置：开关与时点都可能在运行期被设置页改掉
        result = run_brief_tick(config=config_provider())
        if result.get("generated"):
            logger.info(
                "[MarketBrief] 已生成 %s 汇总（%s 条素材，degraded=%s）",
                result.get("mode"), result.get("item_count"), result.get("degraded"),
            )
        elif not result.get("ok"):
            logger.warning("[MarketBrief] 生成失败: %s", result.get("error"))

    return [{
        "task": brief_task,
        "interval_seconds": BRIEF_TICK_SECONDS,
        # 首次注册不立刻跑：进程刚起来时资讯池可能还没采过，等第一个 tick 更稳
        "run_immediately": False,
        "name": BRIEF_TASK_NAME,
    }]
