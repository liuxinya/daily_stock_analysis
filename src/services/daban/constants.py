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
    MODE_FIRST_BOARD: "一进二",
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
# 陈小群线：每一格都对应原文阶段表里的「操作要点」，没写到的一律不放行（None）。
#   冰点「只观察新题材萌芽」；回暖「聚焦新题材首板/二板」；发酵「围绕龙头或换手龙介入，分歧转一致时加仓」；
#   高潮「坚定锁仓龙头」；分歧「只卖不买」，唯一例外是「龙头低吸（分歧期策略）」只做全市场绝对龙头；
#   退潮「坚决离场」。二波反包原文要求「市场情绪未完全退潮，有新题材接力」，只在分歧期带条件放行。
#   原文「只做龙头不做杂毛」，所以低位补涨不做。
_CHEN_GATES = {
    "M1": {"ice": None, "warm": "watch", "ferment": "actionable", "climax": "actionable",
           "diverge": "trial@total_leader", "retreat": None},
    "M2": {"ice": None, "warm": "trial", "ferment": "actionable", "climax": "watch",
           "diverge": "watch", "retreat": None},
    "M3": {"ice": None, "warm": None, "ferment": None, "climax": None, "diverge": None, "retreat": None},
    "M4": {"ice": "watch@new_theme", "warm": "trial@new_theme", "ferment": "watch", "climax": "watch",
           "diverge": None, "retreat": None},
    "M5": {"ice": None, "warm": None, "ferment": None, "climax": None,
           "diverge": "trial@market_alive", "retreat": None},
    "M6": {"ice": "watch", "warm": None, "ferment": None, "climax": None, "diverge": None, "retreat": None},
    "M7": {"ice": None, "warm": None, "ferment": None, "climax": None, "diverge": None, "retreat": None},
}

_KOBE_GATES = {
    "M1": {"low_shock": "watch", "main_rise": "actionable", "high_shock": "trial",
           "fall_1": None, "fall_2": "trial", "fall_3": "watch"},
    "M2": {"low_shock": "trial", "main_rise": "actionable", "high_shock": "trial",
           "fall_1": None, "fall_2": "watch", "fall_3": "trial"},
    "M3": {"low_shock": None, "main_rise": "actionable@theme_profit", "high_shock": "trial@theme_profit",
           "fall_1": None, "fall_2": None, "fall_3": None},
    # 一进二：回测一年整体次日溢价为负（docs/daban-king.md「首板回测」），只给试错；主跌第一天回测最差，不做。
    "M4": {"low_shock": "trial", "main_rise": "trial", "high_shock": "trial",
           "fall_1": None, "fall_2": "watch", "fall_3": "trial"},
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
    "low_price": 20.0,             # 补涨「够低价」；盘中雷达的低价标准也用它
    "o12_max_price": 40.0,         # 一进二：股价不低于此值只观察（科比「低价为王」，回测 ≥40 元为负）
    "live_chen_min_peers": 2,      # 盘中雷达：陈小群线要求同题材在它之前已有几只触板（市场共识）
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
    # 盘中首板雷达：按「前一交易日收盘」的周期决定今天能不能打首板（陈小群「每日开盘前用5大指标判断情绪周期，
    # 确定仓位上限」）。科比线依据 #9 / #12 / #33，主跌第二天回测最好（+1.95%），主跌第一天回测为负、原文
    # 「第一天杀跌是很恐怖的」，不做。陈小群线只有回暖期「聚焦新题材首板/二板」，其余阶段只观察或空仓。
    "live_gates": {
        "kobe": {"low_shock": "trial", "main_rise": "trial", "high_shock": "trial",
                 "fall_1": None, "fall_2": "actionable", "fall_2_early": "trial", "fall_3": "trial"},
        "chen": {"ice": "watch", "warm": "trial", "ferment": "watch", "climax": "watch",
                 "diverge": None, "retreat": None},
    },
    # 盘中雷达每派、每阶段的总仓位上限（%）。原文只有陈小群的阶段仓位与科比「分四个票」「轻仓」，
    # 科比各阶段的具体数字是系统参数（低位试错 20、主升分四票、高位轻仓 30、主跌第二天之后一个满额名额），可覆盖。
    "live_caps": {
        "kobe": {"low_shock": 20, "main_rise": 100, "high_shock": 30, "fall_1": 0, "fall_2": 25,
                 "fall_2_early": 25, "fall_3": 20},
        # 陈小群线：回暖期「聚焦新题材首板/二板」；发酵期「围绕龙头或换手龙介入，分歧转一致时加仓」，
        # 弱转强正是原文最核心的模式，所以发酵期也给仓位。高潮期只锁龙头、分歧期只卖、退潮空仓。
        "chen": {"ice": 0, "warm": 20, "ferment": 20, "climax": 0, "diverge": 0, "retreat": 0},
    },
    # 盘中实时门控：前一交易日的周期只做底线，盘中拿到市场验证后按今天的周期行事。
    # 陈小群「市场永远是对的，预判必须得到市场验证才执行交易」；科比「一旦打脸了，就要修正自己的观点」。
    "realtime_gate": {
        "enabled": True,
        "min_zt_sample": 20,            # 样本量门槛：当日涨停数到这个量级才开始实时重判（09:35 只有几只时比什么都不可靠）
        "confirm_rounds": 2,            # 升级 / 降级都要连续 N 轮满足才生效，避免一轮噪声来回翻
        "zab_rate_max": 0.35,           # 升级要求封板质量：炸板率高于此值不算赚钱效应恢复
        "peak_drawdown": 0.3,           # 降级信号之一：涨停数从当日峰值回落比例
        "theme_accel_window_min": 15,   # 轮动豁免窗口：最近 N 分钟
        "theme_accel_min": 2,           # 该窗口内某个热点题材新增封板 ≥N 只 = 资金在换题材而不是在撤
        "fall2_late_time": "140000",    # 科比「主跌第二天……尾盘就可以找买点」：第 2 天的可执行只在尾盘放行
    },
    # 未涨停候选（涨速快、还没封板）：目的是让人买得进，而不是事后报告已封板的票。
    "preboard": {
        "enabled": True,
        "window_min": 10,          # 只看最近 N 分钟内的拉升异动
        "fetch_timeout_sec": 4,    # 异动取数单独的超时：雷达 10 秒一轮，不能让它占满 15 秒的总预算
        "reuse_sec": 90,           # 异动取数失败时，沿用最近一次成功结果的时间窗（候选本身只看 10 分钟内的异动）
        "gap_max": 6.0,            # 距涨停价最多几个百分点（再远就赶不上）
        "min_change": 3.0,         # 当日涨幅下限
        "max_per_style": 20,       # 每派候选上限（真正的名额由题材热度配额决定）
        "min_price": 2.0,          # 过滤低价噪声
        "max_circ_mv": 500e8,      # 流通市值上限（科比的低价小盘取向，作为硬上限放宽）
        "hot_min_zt": 3,           # 热点板块门槛：当日涨停数（与盘后主线的 3 只对齐）
        "hot_min_share": 0.05,     # 或占全市场涨停的 5%
        "hot_min_days": 2,         # 升温：板块连续上榜天数；或比前一日涨停数增加
        "hot_news_min": 3,         # 新闻门槛：该题材近 3 日相关新闻条数
        "news_theme_limit": 8,     # 每轮最多给几个题材查新闻（控制耗时）
        "quota_top_n": 3,          # 热度排名前 N 的题材
        "quota_top": 4,            # 前 N 名题材各给几个锁定名额
        "quota_other": 2,          # 其余题材各给几个锁定名额
    },
    "live_open_time": "093100",    # 开盘一分钟内封板的视为竞价 / 秒板（多为一字，打板买不到；回测 -0.72%）
    "live_max_watch": 30,
    "forecast_per_theme": 8,
    "forecast_max": 30,
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
    ("chen", STAGE_ICE): ("冰点期空仓，不抄底，只观察新题材萌芽", ["C16", "C01"]),
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
