# -*- coding: utf-8 -*-
"""消息面汇总的素材层：把快讯接口的直拉结果接成分析器要的条目，并在超量时分层抽样。

为什么单独一个模块：``market_brief_service`` 是编排层（判时点、调分析、读结果），
而「上游条目适配」和「超量时怎么挑」是两件独立且纯粹的事，放在一起会把编排层撑成
什么都干的大文件。这里只有纯函数 + 一次取数，不碰 DB、不碰 LLM。

与本地资讯池（``IntelligenceService``）的分工：资讯池是「持续攒下来的历史」，
这里是「汇总那一刻现问上游要一遍」。各家快讯接口单轮只覆盖最近 0.5~4 小时且多数
没有历史翻页，所以光靠资讯池，一旦没有常驻进程持续采集，休市窗口就会是空的；
反过来直拉也补不回上游已经翻不到的更早内容，两者是互补关系，合并后再去重。
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 每个时间桶的跨度：52 小时的周末窗口会切成 26 个桶，轮转取数保证两天都被覆盖到，
# 而不是像「取最新 N 条」那样全压在最后几小时。
BUCKET_HOURS = 2
# 上游把「站点标红的重要快讯」映射成这个 tag（财联社 level=A/B、雪球 mark=1、
# 第一财经 IsImportant），同一个桶里优先选它们。
IMPORTANT_TAG = "重要"
# 抽样用的内部标记键，不进 prompt（分析器只读 title/summary/url/source/时间字段）
IMPORTANT_KEY = "_brief_important"


def _dedupe_key(title: str) -> str:
    """标题归一化键，与 ``realtime_news_service._dedupe_key`` 同一套规则。

    同一条消息常被多个平台同时发，且资讯池里的 NewsNow 源与直拉的站点源经常是
    同一条内容的不同链接，只按 url 去重会留下大量重复。
    """
    return re.sub(r"[^0-9a-z一-鿿]+", "", (title or "").lower())[:40]


def entries_to_items(entries: Sequence[Any]) -> List[Dict[str, Any]]:
    """``RealtimeNewsEntry`` → 分析器要的条目 dict。

    ``published_at`` 按资讯池的约定落成 **naive 本地时间**（与
    ``IntelligenceService._parse_datetime`` 同一个写法），这样直拉条目和池里条目
    能在同一个时间基准上排序、分桶、比边界。
    """
    items: List[Dict[str, Any]] = []
    for entry in entries:
        published = getattr(entry, "published_at", None)
        if published is not None and published.tzinfo is not None:
            published = published.astimezone().replace(tzinfo=None)
        items.append({
            "source_name": getattr(entry, "platform_label", "") or getattr(entry, "platform", ""),
            "source_type": "realtime",
            "title": getattr(entry, "title", "") or "",
            "summary": getattr(entry, "summary", "") or "",
            "url": getattr(entry, "url", "") or "",
            "published_at": published.isoformat(timespec="seconds") if published else None,
            "fetched_at": None,
            IMPORTANT_KEY: getattr(entry, "tag", "") == IMPORTANT_TAG,
        })
    return items


def item_time(item: Dict[str, Any]) -> Optional[datetime]:
    """条目时间：优先 ``published_at``，退到 ``fetched_at``；解析不出来返回 None。

    池里的值是 ISO 字符串（``_item_to_dict`` 序列化过），直拉的也统一成 ISO 字符串，
    所以这里只处理字符串与 datetime 两种形态。
    """
    for key in ("published_at", "fetched_at"):
        raw = item.get(key)
        if isinstance(raw, datetime):
            return raw.replace(tzinfo=None) if raw.tzinfo is not None else raw
        if not raw:
            continue
        try:
            parsed = datetime.fromisoformat(str(raw))
        except (TypeError, ValueError):
            continue
        return parsed.replace(tzinfo=None) if parsed.tzinfo is not None else parsed
    return None


def _sort_key(item: Dict[str, Any]) -> Tuple[datetime, str, str]:
    """排序/选取用的全序键。

    时间之外再带上 url 与标题：同一时刻的条目必须有确定顺序，否则同一批素材两次
    汇总会选出不同的子集，``evidence_signature`` 永远对不上，2 小时缓存形同不存在。
    """
    stamp = item_time(item) or datetime.min
    return (stamp, str(item.get("url") or ""), str(item.get("title") or ""))


def merge_items(*groups: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按标题（退到 url）去重后按时间倒序。靠前的 group 优先保留。

    调用方把直拉结果放在前面：同一条消息两边都有时，直拉那份的发布时间更准
    （池里的 ``published_at`` 可能是上游补的，也可能为空靠 ``fetched_at`` 兜）。
    """
    seen: set = set()
    merged: List[Dict[str, Any]] = []
    for group in groups:
        for item in group:
            if not str(item.get("title") or "").strip():
                continue  # 分析器会丢掉无标题条目，这里先过滤免得占去重名额
            marker = _dedupe_key(str(item.get("title") or "")) or str(item.get("url") or "")
            if not marker or marker in seen:
                continue
            seen.add(marker)
            merged.append(item)
    merged.sort(key=_sort_key, reverse=True)
    return merged


def stratified_sample(
    items: Sequence[Dict[str, Any]],
    *,
    max_items: int,
    window_start: datetime,
    window_end: datetime,
) -> List[Dict[str, Any]]:
    """超量时按时间分层抽样，而不是截最新的 N 条。

    周末窗口能拉到四五百条，其中收盘后那几分钟和周日傍晚最密集；直接截最新 N 条
    会把整个周六丢掉，等于白拉。这里把窗口切成 ``BUCKET_HOURS`` 小时一个桶，
    轮转着每桶取一条（桶内「重要」优先、再按时间新），所以每一个时间片都会先被
    覆盖到，密集时段只在别的桶取空之后才拿到额外配额。

    返回值按时间倒序，保持分段时的时间局部性。
    """
    if max_items <= 0:
        return []
    kept = list(items)
    if len(kept) <= max_items:
        return sorted(kept, key=_sort_key, reverse=True)

    span_hours = max(0.0, (window_end - window_start).total_seconds() / 3600)
    bucket_count = max(1, min(int(math.ceil(span_hours / BUCKET_HOURS)) or 1, max_items))
    bucket_span = timedelta(hours=max(BUCKET_HOURS, span_hours / bucket_count))

    # 无时间条目（池里 undated_fallback 带回来的）不参与时间分层，统一垫在最后
    buckets: Dict[int, List[Dict[str, Any]]] = {}
    undated: List[Dict[str, Any]] = []
    for item in kept:
        stamp = item_time(item)
        if stamp is None:
            undated.append(item)
            continue
        offset = (stamp - window_start).total_seconds()
        index = int(offset // bucket_span.total_seconds()) if bucket_span.total_seconds() > 0 else 0
        buckets.setdefault(min(max(index, 0), bucket_count - 1), []).append(item)

    for bucket in buckets.values():
        # 桶内优先级：重要标记 > 时间新 > 全序键兜底
        bucket.sort(key=lambda item: (bool(item.get(IMPORTANT_KEY)), _sort_key(item)), reverse=True)

    selected: List[Dict[str, Any]] = []
    # 新桶优先：配额不够时宁可让更久远的时间片少一条
    order = sorted(buckets.keys(), reverse=True)
    cursor = 0
    while len(selected) < max_items and order:
        drained: List[int] = []
        for index in order:
            if len(selected) >= max_items:
                break
            bucket = buckets[index]
            if cursor >= len(bucket):
                drained.append(index)
                continue
            selected.append(bucket[cursor])
        order = [index for index in order if index not in drained]
        cursor += 1

    if len(selected) < max_items and undated:
        selected.extend(undated[: max_items - len(selected)])
    return sorted(selected, key=_sort_key, reverse=True)
