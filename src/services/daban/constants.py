# -*- coding: utf-8 -*-
"""「我是打板王」的阈值、标签与门控矩阵。

所有阈值都集中在 :data:`DEFAULT_RULES`，可以用 ``DABAN_RULES_FILE`` 指向一个 YAML 做局部覆盖
（深合并，只写想改的键）。默认值来自 ``陈小群.md`` 的阶段表；那份表是二手整理、且对应的是
约 4000 只股票的年代（现在约 5400 只），所以页面上会展示每个条件的命中明细，方便人工校准。
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "daban-v1"

# ---------------------------------------------------------------------- 周期

STAGE_ICE = "ice"
STAGE_WARM = "warm"
STAGE_FERMENT = "ferment"
STAGE_CLIMAX = "climax"
STAGE_DIVERGE = "diverge"
STAGE_RETREAT = "retreat"

# 周期顺序（环形），平局时优先与昨日阶段相邻的那个。
STAGE_CYCLE = (STAGE_ICE, STAGE_WARM, STAGE_FERMENT, STAGE_CLIMAX, STAGE_DIVERGE, STAGE_RETREAT)
# 没有昨日阶段可参照时的平局优先级：偏保守，先认亏钱的阶段。
STAGE_CONSERVATIVE_ORDER = (
    STAGE_RETREAT, STAGE_ICE, STAGE_DIVERGE, STAGE_WARM, STAGE_FERMENT, STAGE_CLIMAX,
)

STAGE_LABELS = {
    STAGE_ICE: "冰点期",
    STAGE_WARM: "回暖期",
    STAGE_FERMENT: "发酵期",
    STAGE_CLIMAX: "高潮期",
    STAGE_DIVERGE: "分歧期",
    STAGE_RETREAT: "退潮期",
}

KOBE_LOW = "low_shock"
KOBE_RISE = "main_rise"
KOBE_HIGH = "high_shock"
KOBE_FALL = "main_fall"

KOBE_LABELS = {
    KOBE_LOW: "低位震荡（试错期）",
    KOBE_RISE: "主升",
    KOBE_HIGH: "高位震荡",
    KOBE_FALL: "主跌",
}

# 六阶段 → 科比四阶段。主跌再按「连续杀跌第 N 天」细分，见 cycle.kobe_phase。
STAGE_TO_KOBE = {
    STAGE_ICE: KOBE_LOW,
    STAGE_WARM: KOBE_LOW,
    STAGE_FERMENT: KOBE_RISE,
    STAGE_CLIMAX: KOBE_RISE,
    STAGE_DIVERGE: KOBE_HIGH,
    STAGE_RETREAT: KOBE_FALL,
}

# ---------------------------------------------------------------------- 模式 / 角色 / 行动

MODE_LEADER = "M1"
MODE_WEAK_TO_STRONG = "M2"
MODE_CATCH_UP = "M3"
MODE_FIRST_BOARD = "M4"
MODE_REBOUND = "M5"
MODE_SWITCH = "M6"
MODE_SUB_NEW = "M7"

MODE_LABELS = {
    MODE_LEADER: "龙头·分歧介入",
    MODE_WEAK_TO_STRONG: "弱转强",
    MODE_CATCH_UP: "低位补涨",
    MODE_FIRST_BOARD: "首板挖掘 / 一进二",
    MODE_REBOUND: "反包二板 / 二波反包",
    MODE_SWITCH: "高低切 / 切换",
    MODE_SUB_NEW: "次新",
}

ROLE_TOTAL_LEADER = "total_leader"
ROLE_THEME_LEADER = "theme_leader"
ROLE_FRONT = "front"
ROLE_CATCH_UP = "catch_up"
ROLE_MIDDLE = "middle"
ROLE_FOLLOWER = "follower"
ROLE_OTHER = "other"

ROLE_LABELS = {
    ROLE_TOTAL_LEADER: "总龙头",
    ROLE_THEME_LEADER: "板块龙头",
    ROLE_FRONT: "前排",
    ROLE_CATCH_UP: "低位补涨",
    ROLE_MIDDLE: "中位股",
    ROLE_FOLLOWER: "跟风",
    ROLE_OTHER: "其他",
}

ACTION_ACTIONABLE = "actionable"
ACTION_TRIAL = "trial"
ACTION_WATCH = "watch"

ACTION_LABELS = {
    ACTION_ACTIONABLE: "可执行",
    ACTION_TRIAL: "试错",
    ACTION_WATCH: "观察",
}
ACTION_ORDER = {ACTION_ACTIONABLE: 0, ACTION_TRIAL: 1, ACTION_WATCH: 2}

STYLE_LABELS = {"chen": "陈小群打法", "kobe": "92科比打法"}

# ---------------------------------------------------------------------- 规则（可被 YAML 覆盖）

# 门控取值：None = 该打法不做这个模式；"actionable" / "trial" / "watch" = 行动级别；
# 带 "@条件" 的表示满足条件才放行，否则降为观察（条件名见 styles._gate_condition）。
_CHEN_GATES = {
    "M1": {"ice": None, "warm": "watch", "ferment": "actionable", "climax": "actionable",
           "diverge": "trial@total_leader", "retreat": None},
    "M2": {"ice": "watch", "warm": "trial", "ferment": "actionable", "climax": "actionable",
           "diverge": "watch", "retreat": None},
    "M3": {"ice": None, "warm": None, "ferment": "trial", "climax": "trial",
           "diverge": None, "retreat": None},
    "M4": {"ice": "watch", "warm": "trial", "ferment": "actionable", "climax": "actionable",
           "diverge": "watch", "retreat": "watch"},
    "M5": {"ice": None, "warm": "trial", "ferment": "trial", "climax": "trial",
           "diverge": "trial@market_alive", "retreat": None},
    "M6": {"ice": "watch", "warm": None, "ferment": None, "climax": None,
           "diverge": None, "retreat": "watch"},
    # 陈小群原文没有次新打法，不放行。
    "M7": {"ice": None, "warm": None, "ferment": None, "climax": None,
           "diverge": None, "retreat": None},
}

_KOBE_GATES = {
    "M1": {"low_shock": "watch", "main_rise": "actionable", "high_shock": "trial",
           "fall_1": None, "fall_2": "trial", "fall_3": "watch"},
    "M2": {"low_shock": "trial", "main_rise": "actionable", "high_shock": "trial",
           "fall_1": None, "fall_2": "watch", "fall_3": "trial"},
    "M3": {"low_shock": None, "main_rise": "actionable@theme_profit", "high_shock": "trial@theme_profit",
           "fall_1": None, "fall_2": None, "fall_3": None},
    "M4": {"low_shock": "trial", "main_rise": "actionable", "high_shock": "trial",
           "fall_1": "watch", "fall_2": "watch", "fall_3": "trial"},
    "M5": {"low_shock": "trial", "main_rise": "trial", "high_shock": "watch",
           "fall_1": None, "fall_2": None, "fall_3": "watch"},
    "M6": {"low_shock": "trial", "main_rise": None, "high_shock": "trial",
           "fall_1": "watch", "fall_2": "trial", "fall_3": "trial"},
    "M7": {"low_shock": "trial@no_mainline", "main_rise": None, "high_shock": None,
           "fall_1": "watch", "fall_2": "trial", "fall_3": "trial"},
}

DEFAULT_RULES: Dict[str, Any] = {
    # 周期判定条件（陈小群阶段表）。
    "stage": {
        "ice": {"zt_max": 30, "lianban_max": 5, "premium_max": -2.0},
        "warm": {"zt_min": 30, "zt_max": 50, "lianban_min": 5, "lianban_max": 8},
        "ferment": {"zt_min": 50, "zt_max": 80, "lianban_min": 8, "lianban_max": 12, "follow_rate_min": 0.30},
        "climax": {"zt_min": 80, "height_min": 5, "seal_ratio_min": 0.03},
        "retreat": {"dt_min": 10, "lianban_max": 3, "premium_max": -3.0},
        "retreat_override_dt": 20,
        "transitional_below": 2.0 / 3.0,
    },
    "big_loss_pct": -5.0,          # 昨日涨停今日跌幅 ≤ 此值记为「大面」
    "death_turnover": 70.0,        # 死亡换手（科比 #35）
    "weak_late_time": "143000",    # 末次封板晚于此时刻视为尾盘偷鸡板
    "early_seal_time": "100000",   # 首封早于此时刻视为封板早
    "low_price": 20.0,             # 补涨「够低价」
    "small_cap": 100e8,            # 补涨「够小盘」（流通市值，元）
    "theme_long_days": 45,         # 题材持续超过此天数扣分（科比 #15：一个半月到两个月）
    "theme_old_days": 30,          # 题材持续超过此天数 A 维度减分
    "main_theme_min_zt": 3,        # 题材至少几只涨停才算主线候选
    "mainline_confirm_share": 0.30,
    "mainline_confirm_height": 4,
    "no_mainline_zt": 5,
    "sub_new_strong_min": 3,
    "new_theme_min_zt": 3,
    "track_record_days": 10,
    "track_record_min_days": 5,
    "track_record_min_gap": 0.5,
    "grade": {"S": 85, "A": 75, "B": 65},
    "max_trial": 6,
    "max_watch": 10,
    "max_candidates": 40,
    "kline_limit": 120,
    "news_themes": 4,
    "backfill_days": 20,
    "gates": {"chen": _CHEN_GATES, "kobe": _KOBE_GATES},
    "lane_caps": {
        "chen": {"ice": 0, "warm": 20, "ferment": 50, "climax": 80, "diverge": 30, "retreat": 0},
        "kobe": {"low_shock": 20, "main_rise": 100, "high_shock": 30, "fall_1": 0, "fall_2": 10, "fall_3": 20},
    },
    "lane_cap_labels": {
        "chen": {"ice": "0（空仓）", "warm": "10–20%", "ferment": "30–50%", "climax": "50–80%",
                 "diverge": "≤30%，只卖不买", "retreat": "0（空仓）"},
        "kobe": {"low_shock": "≤20%（试错）", "main_rise": "分 4 票，每票 ≤25%", "high_shock": "≤30%（轻仓）",
                 "fall_1": "0（只观察）", "fall_2": "≤10%（尾盘试错）", "fall_3": "≤20%（等修复）"},
    },
    # 单票仓位上限（%）。
    "position": {
        "chen": {"leader": 30, "follower": 10, "trial_leader": 15, "trial_follower": 5},
        "kobe": {"actionable": 25, "trial": 10},
    },
}

# ---------------------------------------------------------------------- 文案

# 空仓时线头显示的该派原话。键为（打法, 阶段）。
FLAT_MESSAGES = {
    ("chen", STAGE_ICE): ("冰点期空仓，不抄底，只观察新题材萌芽", ["C01", "C10"]),
    ("chen", STAGE_RETREAT): ("退潮期空仓是最佳策略", ["C02", "C10"]),
    ("kobe", "fall_1"): ("主跌第一天杀跌很恐怖，只观察不动手", ["K09"]),
}

SELL_RULES = {
    "chen": [
        ("触及 5% 止损线立刻离场，不补仓不幻想", ["C08"]),
        ("龙头出现滞涨或炸板时清仓；缩量加速时分批减仓", ["C06"]),
    ],
    "kobe": [
        ("第二天不涨停基本都走，有地位的龙头除外", ["K21"]),
        ("走势与预期不一致，杀伐果断坚决止损", ["K17"]),
    ],
}

PHASE_LABELS = {
    "queued": "排队中",
    "probing": "确认交易日",
    "backfilling": "回补历史",
    "fetching": "抓取涨停数据",
    "analyzing_cycle": "判定情绪周期",
    "selecting": "识别题材与候选",
    "analyzing": "模型解读",
    "reviewing": "复盘昨日推荐",
    "finalizing": "汇总落库",
    "succeeded": "完成",
    "failed": "失败",
}

SOURCE_LABELS = {
    "zt": "涨停池",
    "zb": "炸板池",
    "dt": "跌停池",
    "ladder": "连板天梯",
    "blocks": "涨停题材榜",
    "previous": "昨日涨停今日表现",
    "strong": "强势股池",
    "sub_new": "次新股池",
    "hot": "人气榜",
    "news": "题材新闻",
    "klines": "候选日K",
}

INVESTMENT_DISCLAIMER = (
    "本页是基于 92科比、陈小群两位游资公开理念的规则化复盘工具，所有结论由公开数据按固定规则计算，"
    "模型只负责措辞与有限调分，不构成任何投资建议。短线交易风险极高，请独立判断、自负盈亏。"
)
DATA_DISCLAIMER = (
    "涨停原因来自同花顺涨停数据中心的公开标注；统计口径为沪深主板 + 创业板 + 科创板、剔除 ST，"
    "与东财口径（含北交所）存在差异。阈值来自二手整理的文章，已全部开放配置。"
)


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_rules(config=None) -> Dict[str, Any]:
    """默认规则 + ``DABAN_RULES_FILE`` 覆盖。文件缺失或格式错误时退回默认值并记日志。"""
    path = str(getattr(config, "daban_rules_file", "") or "").strip() if config is not None else ""
    if not path:
        return copy.deepcopy(DEFAULT_RULES)
    try:
        import yaml

        with open(path, "r", encoding="utf-8") as handle:
            override = yaml.safe_load(handle) or {}
        if not isinstance(override, dict):
            raise ValueError("顶层必须是映射")
        return _deep_merge(DEFAULT_RULES, override)
    except Exception as exc:
        logger.warning("打板王：读取规则覆盖文件 %s 失败，使用默认规则：%s", path, exc)
        return copy.deepcopy(DEFAULT_RULES)
