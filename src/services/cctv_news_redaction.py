# -*- coding: utf-8 -*-
"""送入 LLM 前对文字稿做的输入规范化。

**为什么需要这一层**：实测国内模型通道（如百度千帆 + deepseek 系）对涉及最高
领导人的文本会触发内容策略，返回固定话术或直接给出空 ``content``。因为《新闻
联播》头条几乎每天涉及领导人活动，不做处理的话"AI 新闻分析"会系统性失败——
表现就是接口报 503。

处理原则：

- **只改送给模型的副本**。入库原文、前端展示的文字稿详情、证据链接全部保持原样，
  用户看到的永远是央视网原文。
- **只做删减和角色化替换，绝不添加内容**。替换后的文本仍然是原文的子集信息，
  不会给模型引入任何新事实，因此不影响"只依据文字稿分析"的约束。
- 行业分析真正需要的是政策方向和产业名词，这些在替换后完整保留。

替换词表可以通过 ``CCTV_NEWS_LLM_REDACT_TERMS`` 覆盖，格式 ``词:占位,词:占位``，
只写词时用默认占位。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

DEFAULT_PLACEHOLDER = "国家领导人"

# 默认词表：实测会让国内通道拒答的称谓。命中后替换为角色占位，
# 政策与产业信息不受影响。
DEFAULT_REDACT_TERMS: Tuple[str, ...] = (
    "习近平",
    "李强",
    "赵乐际",
    "王沪宁",
    "蔡奇",
    "丁薛祥",
    "李希",
    "韩正",
)

# 判断一条新闻"是否含产业信息"的关键词。用于降级阶段：只有在带原文的完整
# 尝试被拒答之后，才退到"只送有产业信息的条目"。
INDUSTRY_SIGNAL_TERMS: Tuple[str, ...] = (
    "产业", "制造", "企业", "经济", "投资", "消费", "出口", "进口", "贸易",
    "科技", "创新", "人工智能", "算力", "数据", "芯片", "半导体", "新能源",
    "电力", "能源", "煤炭", "石油", "天然气", "钢铁", "有色", "化工",
    "农业", "粮食", "种业", "养殖", "医药", "医疗", "生物", "疫苗",
    "汽车", "船舶", "航空", "航天", "铁路", "交通", "物流", "港口",
    "建筑", "基建", "水利", "房地产", "住房", "金融", "银行", "保险",
    "证券", "债券", "利率", "税收", "财政", "货币", "信贷", "补贴",
    "文旅", "旅游", "教育", "养老", "环保", "绿色", "低碳", "碳排放",
    "通信", "5G", "卫星", "机器人", "装备", "材料", "开放", "改革",
    "政策", "规划", "十五五", "高质量发展", "内需", "市场",
)


@dataclass
class RedactionStats:
    """本次规范化做了什么，用于诊断与前端如实告知。"""

    redacted_terms: List[str] = field(default_factory=list)
    redacted_item_count: int = 0
    dropped_item_count: int = 0

    @property
    def applied(self) -> bool:
        return bool(self.redacted_terms) or self.dropped_item_count > 0

    def as_dict(self) -> Dict[str, object]:
        return {
            "redacted_terms": sorted(set(self.redacted_terms)),
            "redacted_item_count": self.redacted_item_count,
            "dropped_item_count": self.dropped_item_count,
        }


def parse_redact_terms(raw: Optional[str]) -> Dict[str, str]:
    """解析 ``词:占位,词`` 形式的配置；为空时返回默认词表。"""
    mapping: Dict[str, str] = {}
    text = (raw or "").strip()
    if not text:
        return {term: DEFAULT_PLACEHOLDER for term in DEFAULT_REDACT_TERMS}

    for chunk in text.split(","):
        entry = chunk.strip()
        if not entry:
            continue
        if ":" in entry:
            term, placeholder = entry.split(":", 1)
        else:
            term, placeholder = entry, DEFAULT_PLACEHOLDER
        term = term.strip()
        placeholder = placeholder.strip() or DEFAULT_PLACEHOLDER
        if term:
            mapping[term] = placeholder
    return mapping


def redact_text(text: str, mapping: Dict[str, str]) -> Tuple[str, List[str]]:
    """把命中的词替换成角色占位，返回 ``(新文本, 命中的词)``。"""
    result = text or ""
    hits: List[str] = []
    for term, placeholder in mapping.items():
        if term and term in result:
            result = result.replace(term, placeholder)
            hits.append(term)
    return result, hits


def has_industry_signal(text: str) -> bool:
    """粗判一条新闻是否带产业/经济信息。"""
    body = text or ""
    return any(term in body for term in INDUSTRY_SIGNAL_TERMS)


def _collapse_placeholders(text: str, placeholders: Iterable[str]) -> str:
    """把"国家领导人、国家领导人、国家领导人"这类连续重复压成一个。"""
    result = text
    for placeholder in set(placeholders):
        if not placeholder:
            continue
        escaped = re.escape(placeholder)
        result = re.sub(rf"(?:{escaped})(?:\s*[、和与及,，]\s*{escaped})+", placeholder, result)
    return result


def select_within_budget(
    prepared: Sequence[Dict[str, object]],
    *,
    max_chars: int,
) -> Tuple[List[Dict[str, object]], int]:
    """总字数超预算时，优先保留带产业信息的条目。

    长窗口（20/30 天）的文字稿总量远超单次可送入的上限，必须裁剪。裁剪顺序是
    "有产业信息的优先、日期新的优先"，因为纯人事/外交条目对行业分析没有贡献。
    返回 ``(保留的条目, 丢弃条数)``，保留结果仍按原时间顺序。
    """
    budget = max(1500, int(max_chars))
    total = sum(
        len(str(entry.get("content") or "")) + len(str(entry.get("title") or ""))
        for entry in prepared
    )
    if total <= budget:
        return list(prepared), 0

    def priority(indexed: Tuple[int, Dict[str, object]]) -> Tuple[int, int]:
        index, entry = indexed
        body = f"{entry.get('title') or ''}\n{entry.get('content') or ''}"
        # 有产业信号排前面；同组内按原顺序倒序（新的在前）
        return (0 if has_industry_signal(body) else 1, -index)

    ranked = sorted(enumerate(prepared), key=priority)
    kept_indexes: List[int] = []
    used = 0
    for index, entry in ranked:
        size = len(str(entry.get("content") or "")) + len(str(entry.get("title") or ""))
        if used + size > budget and kept_indexes:
            continue
        kept_indexes.append(index)
        used += size

    kept = [prepared[i] for i in sorted(kept_indexes)]
    return kept, len(prepared) - len(kept)


def normalize_items_for_llm(
    items: Sequence[object],
    *,
    redact_terms: Optional[str] = None,
    industry_only: bool = False,
) -> Tuple[List[Dict[str, object]], RedactionStats]:
    """把 ORM 条目转成"可安全送入 LLM"的轻量结构。

    返回的每个 dict 含 ``item``（原 ORM 行，用于证据映射与事实校验）、
    ``title``、``content``（规范化后的文本）。``industry_only=True`` 时丢弃
    没有产业信号的条目——这是拒答后的降级手段，不是默认行为。
    """
    mapping = parse_redact_terms(redact_terms)
    stats = RedactionStats()
    prepared: List[Dict[str, object]] = []

    for item in items:
        raw_title = getattr(item, "title", "") or ""
        raw_content = getattr(item, "content", "") or ""

        title, title_hits = redact_text(raw_title, mapping)
        content, content_hits = redact_text(raw_content, mapping)
        hits = title_hits + content_hits
        if hits:
            stats.redacted_terms.extend(hits)
            stats.redacted_item_count += 1
            placeholders = [mapping[term] for term in hits if term in mapping]
            title = _collapse_placeholders(title, placeholders)
            content = _collapse_placeholders(content, placeholders)

        if industry_only and not has_industry_signal(f"{raw_title}\n{raw_content}"):
            stats.dropped_item_count += 1
            continue

        prepared.append({"item": item, "title": title, "content": content})

    if stats.applied:
        logger.info(
            "新闻联播分析输入已规范化：脱敏 %s 条，丢弃无产业信息 %s 条",
            stats.redacted_item_count,
            stats.dropped_item_count,
        )
    return prepared, stats
