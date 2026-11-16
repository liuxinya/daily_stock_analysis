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
    "small_cap": 100e8,            # 补涨「够小盘」（流通市值，元）
    "theme_long_days": 45,         # 题材持续超过此天数扣分（科比 #15：一个半月到两个月）
    "theme_old_days": 30,          # 题材持续超过此天数 A 维度减分
    "main_theme_min_zt": 3,        # 题材至少几只涨停才算主线候选
    "no_mainline_zt": 5,
    "new_theme_min_zt": 3,
    "grade": {"S": 85, "A": 75, "B": 65},
    "max_candidates": 40,
    "kline_limit": 120,
    "news_themes": 4,
    "backfill_days": 20,
    # 盘中实时周期重判用的放行级别表：只用来判断「今天的周期是否比昨天收盘更放行」（见 live.decide_realtime_gate），
    # 推荐的边界本身由 worldview 决定。
    "live_gates": {
        "kobe": {"low_shock": "trial", "main_rise": "trial", "high_shock": "trial",
                 "fall_1": None, "fall_2": "actionable", "fall_2_early": "trial", "fall_3": "trial"},
        "chen": {"ice": "watch", "warm": "trial", "ferment": "watch", "climax": "watch",
                 "diverge": None, "retreat": None},
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
        "min_change": 3.0,         # 当日涨幅下限（拉升异动；全市场扫描用 market_scan.min_change）
        "min_price": 2.0,          # 过滤低价噪声
        "max_circ_mv": 500e8,      # 流通市值上限（科比的低价小盘取向，作为硬上限放宽）
        "hot_min_zt": 3,           # 热点板块门槛：当日涨停数（与盘后主线的 3 只对齐）
        "hot_min_share": 0.05,     # 或占全市场涨停的 5%
        "hot_min_days": 2,         # 升温：板块连续上榜天数；或比前一日涨停数增加
        "hot_news_min": 3,         # 新闻门槛：该题材近 3 日相关新闻条数
        "news_theme_limit": 8,     # 每轮最多给几个题材查新闻（控制耗时）
    },
    # 盘中时序留档（daban_radar_events）：给「盘中因子」攒验证数据。间隔是「至少隔多久」，实际精度受轮询间隔限制
    # （开着页面约 10 秒，只有后台任务时约 30 秒）。
    "radar_log": {
        "enabled": True,
        "sample_sec": 300,   # 热点候选里还没封住的，多久采一次价格路径
        "theme_sec": 300,    # 热点题材与龙 1 / 龙 2 状态
        "market_sec": 60,    # 三大指数与全市场涨停 / 炸板 / 跌停
        "auction_sec": 60,   # 集合竞价期间，昨日涨停 / 炸板票的竞价价格与成交量
    },
    # 盘中「重点」推荐的计分表（评分体系 v2，2026-11-08 定稿，按 H1 收益标定）。
    # 每 0.1 个百分点的 H1 超额记 1 分，0 分 = 同批候选的平均水平；门槛 = 总分 ≥ threshold。
    # dim / cell 必须与 rec_features 生成的格子字面一致（回测与盘中共用）。n / days / excess / halves 是标定时的回测数字，
    # 页面原样展示。整张表可以用 DABAN_RULES_FILE 覆盖（列表整体替换），月度重标的建议见 scripts/daban_backtest.py --mode recommend。
    "rec_score": {
        "gap_max": 6.0,
        "threshold": 0,
        "items": [
            {"key": "open_gap5", "dim": "昨日状态×今日开盘", "cell": "昨日未触板｜｜高开 ≥5%（未涨停）", "points": -8,
             "label": "昨日没触板，今天高开 ≥5% 但没涨停", "source": "科比 #34「弱转强不喜欢竞价高开」",
             "principle_ids": ["K28"], "supplement": False, "n": 1740, "days": 199, "excess": -0.90, "halves": [-0.35, -1.36]},
            {"key": "theme_start_old", "dim": "题材状态×新老", "cell": "启动｜老题材", "points": -8,
             "label": "所属题材第 1~2 天，但属于老题材", "source": "系统补充，无原文出处",
             "principle_ids": [], "supplement": True, "n": 3375, "days": 155, "excess": -0.80, "halves": [-0.37, -1.09]},
            {"key": "cm20_10_20", "dim": "涨跌幅制度×股价", "cell": "20cm｜10-20元", "points": -6,
             "label": "20cm 且股价 10~20 元", "source": "系统补充，无原文出处",
             "principle_ids": [], "supplement": True, "n": 650, "days": 183, "excess": -0.78, "halves": [-0.51, -0.95]},
            {"key": "cm20_20_40", "dim": "涨跌幅制度×股价", "cell": "20cm｜20-40元", "points": -5,
             "label": "20cm 且股价 20~40 元", "source": "科比 #27「低价为王」（20cm 是系统补充）",
             "principle_ids": ["K23"], "supplement": False, "n": 1089, "days": 197, "excess": -0.54, "halves": [-0.80, -0.38]},
            {"key": "cm20_40", "dim": "涨跌幅制度×股价", "cell": "20cm｜≥40元", "points": -5,
             "label": "20cm 且股价 ≥ 40 元", "source": "科比 #27「低价为王」（20cm 是系统补充）",
             "principle_ids": ["K23"], "supplement": False, "n": 2414, "days": 201, "excess": -0.50, "halves": [-0.55, -0.47]},
            {"key": "vol3", "dim": "昨日量比", "cell": "≥3 倍", "points": -5,
             "label": "昨日量比 ≥ 3 倍（对前 5 日）", "source": "系统补充，无原文出处",
             "principle_ids": [], "supplement": True, "n": 2348, "days": 204, "excess": -0.49, "halves": [-0.35, -0.67]},
            {"key": "cm20_5_10", "dim": "涨跌幅制度×股价", "cell": "20cm｜5-10元", "points": -4,
             "label": "20cm 且股价 5~10 元", "source": "系统补充，无原文出处",
             "principle_ids": [], "supplement": True, "n": 287, "days": 142, "excess": -0.74, "halves": [-0.54, -0.86]},
            {"key": "follow_07", "dim": "跟风程度×题材状态", "cell": "≥0.7｜启动", "points": -4,
             "label": "和题材龙头高度同涨同跌（近 40 日相关系数 ≥ 0.7），题材第 1~2 天",
             "source": "陈小群 C13「只做龙头不做杂毛」", "principle_ids": ["C13"], "supplement": False,
             "n": 399, "days": 137, "excess": -0.66, "halves": [-0.86, -0.52]},
            {"key": "follow_05", "dim": "跟风程度×题材状态", "cell": "0.5-0.7｜启动", "points": -4,
             "label": "和题材龙头相关系数 0.5~0.7，题材第 1~2 天", "source": "陈小群 C13「只做龙头不做杂毛」",
             "principle_ids": ["C13"], "supplement": False, "n": 1472, "days": 197, "excess": -0.48, "halves": [-0.25, -0.65]},
            {"key": "open_gap2_5", "dim": "昨日状态×今日开盘", "cell": "昨日未触板｜｜高开 2-5%", "points": 5,
             "label": "昨日没触板，今天高开 2~5%", "source": "系统补充，无原文出处",
             "principle_ids": [], "supplement": True, "n": 10187, "days": 204, "excess": 0.49, "halves": [0.80, 0.14]},
            {"key": "broken_gap2_5", "dim": "昨日状态×今日开盘", "cell": "昨日炸板｜｜高开 2-5%", "points": 4,
             "label": "昨日炸板，今天高开 2~5% 后被买上去", "source": "陈小群 C04 弱转强「前一日分歧/烂板，次日开盘快速翻红上板」",
             "principle_ids": ["C04"], "supplement": False, "n": 318, "days": 134, "excess": 0.64, "halves": [0.64, 0.63]},
        ],
        # 观察期（2026-11-08 定）：和科比原文冲突的 4 种一进二情形回测已够硬，但先照常推荐、不扣分，成绩单独成组，
        # 首次月度重标时用实盘数据复核。would_be = 按回测本应扣的分。
        "observe": [
            {"key": "o12_hard_low", "dim": "昨日状态×今日开盘", "cell": "昨日涨停·首板｜硬板｜低开", "would_be": -11,
             "label": "昨日首板（硬板），今天低开后被拉起", "n": 407, "days": 162, "excess": -1.69, "halves": [-1.62, -1.75]},
            {"key": "o12_weak_low", "dim": "昨日状态×今日开盘", "cell": "昨日涨停·首板｜弱板（开过板/尾盘封）｜低开", "would_be": -7,
             "label": "昨日首板（弱板），今天低开后被拉起", "n": 551, "days": 187, "excess": -0.90, "halves": [-0.87, -0.93]},
            {"key": "o12_hard_gap5", "dim": "昨日状态×今日开盘", "cell": "昨日涨停·首板｜硬板｜高开 ≥5%（未涨停）", "would_be": -6,
             "label": "昨日首板（硬板），今天高开 ≥5% 未涨停", "n": 545, "days": 181, "excess": -0.78, "halves": [-0.32, -1.29]},
            {"key": "o12_hard_gap2_5", "dim": "昨日状态×今日开盘", "cell": "昨日涨停·首板｜硬板｜高开 2-5%", "would_be": -3,
             "label": "昨日首板（硬板），今天高开 2~5%", "n": 1010, "days": 197, "excess": -0.37, "halves": [-0.51, -0.22]},
        ],
        "observe_text": "科比 #12 认为「低位就是做首板或者一进二」；回测（距涨停 6% 买入、按 H1 卖出）这一类前后半年都为负",
        "baseline": {"n": 89901, "days": 209, "h1_mean": 0.09, "h1_win": 0.56, "seal_rate": 0.13},
    },
    # 世界观边界（不打分，只决定能不能推荐）。依据见 .claude/reviews/daban-scoring/worldview-draft.md 与 docs/daban-king.md。
    "worldview": {
        "block_stages": ["ice", "retreat"],   # 陈小群「退潮期坚决空仓」「冰点期不抄底」：前一交易日收盘是这两个阶段时不推荐
        "block_fall_day": 1,                  # 科比 #9「第一天杀跌是很恐怖的」：前一交易日是主跌第 1 天时不推荐
        "middle_boards": [2],                 # 科比 #12「不要去做中位股」：昨日 2 板今天冲 3 板，回测 -0.88%
        # 节点「龙头见顶杀高位」当天，退潮期 / 主跌第 1 天的边界放行（回测 15 天按天 +0.86%，用户 2026-11-08 定）
        "node_pass": ["retreat", "fall_1"],
        # 新题材只在原文说的时机推荐（科比 #9 / #14）；「低位已有赚钱效应」回测无区分度，已去掉
        "new_theme_timing": ["top_killed", "high_diverge", "after_fall"],
        "commonality_same_min": 0.5,          # 成员历史涨停原因落在近 10 日主线里的比例 ≥ 此值 = 共性相同（补涨 / 发散）
        "commonality_new_max": 0.2,           # ≤ 此值 = 共性不同（新题材 / 切换）
        "catch_up_end": True,                 # 科比 #6 补涨结束的两个征兆同时出现时，该题材停推补涨
    },
    # 盘中推翻信号（PR 5）：昨天收盘判定「管住手」的方向，盘中出现下面任一信号时只放开触发的那个方向。
    # 阈值是暂定值（provisional），要用 daban_radar_events 攒下的盘中数据校准后再改；页面会标「暂定」。
    "overrides": {
        "enabled": True,
        "provisional": True,
        "index_window_min": 30,
        "index_rise": {"sh000001": 0.8, "sz399001": 1.0, "sz399006": 1.5},   # N 分钟内涨幅（百分点）
        # 大盘指数急拉是否放开全部。默认 False（只提示）：回测（2025-12 ~ 2026-11）里它在被挡住的日子放开的票
        # 每笔 H1 -0.21、次日收盘卖胜率 44%（22 天）；被挡住的日子改由 surge_point 只放开那 30 分钟。
        "index_opens": False,
        "theme_window_min": 15,
        "theme_min_new": 3,       # 某热点题材 N 分钟内新增封板 ≥ 此值
        "new_theme_min_zt": 4,    # 新题材（成员共性不同）当日涨停数 ≥ 此值，且在原文时机上
    },
    # 大盘急拉推荐点：沪深300 近 30 分钟拉升 ≥ 0.8 个百分点且此刻翻红，触发后 30 分钟内进入「重点」的票打标记、置顶、推送。
    # 不改评分、不单独统计成绩。前一交易日周期挡住的日子，只在这 30 分钟里放开推荐、标「未验证」（一年 7 天样本，
    # 每笔 H1 +0.80 ± 0.96）。回测（2025-12 ~ 2026-11，开放的日子）：
    # 触发约 28 天 / 年；触发后 30 分钟内进入每笔 H1 +1.81、胜率 69%（17 天，前后半年 +1.70 / +1.88）；
    # 进入时正在急拉的票当天收盘胜 72%、次日开盘卖胜 61%、次日收盘卖胜 71%。参考点只用 09:35 之后的分时（不算开盘跳空）。
    "surge_point": {
        "enabled": True,
        "symbol": "sh000300",
        "window_min": 30,
        "rise": 0.8,              # 近 window_min 分钟涨幅（百分点）≥ 此值
        "pin_min": 30,            # 触发后多少分钟内进入「重点」的票打标记
        "ref_from": "093500",     # 参考点最早的时刻（开盘跳空不算急拉）
    },
    # 预期基准（科比 #23 #32）：昨日涨停股按「板数 × 板质量」统计今天开盘幅度（20cm 折算成 10cm）的历史分位，
    # 低于 P25 = 低于预期，高于 P75 = 超预期。一年回测算出，月度重标时更新。
    "expectation": {
        "首板|硬板": [-0.36, 0.94, 3.05, 0.139],
        "首板|弱板": [-0.74, 0.62, 2.81, 0.151],
        "首板|一字板": [2.69, 7.27, 10.00, 0.491],
        "2板|硬板": [-0.26, 2.04, 6.72, 0.262],
        "2板|弱板": [-1.36, 0.60, 4.08, 0.242],
        "2板|一字板": [5.08, 9.97, 10.02, 0.648],
        "≥3板|硬板": [0.93, 5.77, 9.98, 0.401],
        "≥3板|弱板": [-1.96, 0.95, 5.86, 0.340],
        "≥3板|一字板": [5.06, 9.99, 10.02, 0.725],
    },
    # 全市场扫描：东财拉升异动只给「最近 10 分钟触发过异动」的票，慢慢涨上来的会漏掉；隔一段时间用腾讯批量行情扫一遍。
    "market_scan": {"enabled": True, "interval_sec": 60, "workers": 4, "min_change": 2.0},
    # 推荐特征的日 K：盘中进入视野时后台取，取到之前不进「重点」。
    "feature_book": {"workers": 6, "kline_limit": 270, "warm_days": 3},
    "forecast_per_theme": 8,
    "forecast_max": 30,
}

# ---------------------------------------------------------------------- 文案

PHASE_LABELS = {
    "queued": "排队中",
    "probing": "确认交易日",
    "backfilling": "回补历史",
    "fetching": "抓取涨停数据",
    "analyzing_cycle": "判定情绪周期",
    "selecting": "识别题材与候选",
    "analyzing": "模型写大局观",
    "reviewing": "生成世界观并复盘「重点」推荐",
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
    "本页是基于 92科比、陈小群两位游资公开理念的规则化复盘工具，所有结论由公开数据按固定规则和回测标定的计分表计算，"
    "模型只写大局观与题材预期判断，不构成任何投资建议。短线交易风险极高，请独立判断、自负盈亏。"
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
