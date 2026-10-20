# -*- coding: utf-8 -*-
"""「我是打板王」的理念条目库。

规则引擎与模型写出的每条理由都必须挂这里的 id，页面显示为出处标签，方便逐条核对
「这条理由是不是两篇文章里说过的」。K = 92科比（``ref`` 为原文条目编号），C = 陈小群
（``ref`` 为原文章节号）。模型输出里出现库外 id 会被丢弃，见 ``analyzer._ground``。

``quote`` 只摘原文短句，不做改写；``title`` 是页面标签上的短名。
"""

from __future__ import annotations

from typing import Dict, Iterable, List

AUTHOR_KOBE = "92科比"
AUTHOR_CHEN = "陈小群"

STYLE_KOBE = "kobe"
STYLE_CHEN = "chen"

_K = AUTHOR_KOBE
_C = AUTHOR_CHEN

PRINCIPLES: Dict[str, Dict[str, str]] = {
    "K01": {"author": _K, "ref": "#3", "title": "人气带来流动性溢价", "quote": "短线最核心的东西就是人气带来的流动性溢价，仅此而已。"},
    "K02": {"author": _K, "ref": "#4", "title": "龙头、补涨、切换", "quote": "短线操作思路的核心：龙头、补涨、切换。"},
    "K03": {"author": _K, "ref": "#5", "title": "补涨的心理逻辑", "quote": "龙头断板时，赚钱的人想继续赚，而亏钱的人并没有扩大面积，所以补涨有了天时地利人和的环境。"},
    "K04": {"author": _K, "ref": "#6", "title": "补涨结束的征兆", "quote": "做低位补涨的，不但没有利润，隔日开始亏损；做龙头低吸反包的，不但没有反包，继续亏损。"},
    "K05": {"author": _K, "ref": "#7/#8", "title": "切换必须完全不同", "quote": "切换的本质是改变共识……这里的新，一定是相对老的概念，完全不同的东西才是新。"},
    "K06": {"author": _K, "ref": "#9", "title": "情绪四阶段", "quote": "一个周期，分为四个阶段：低位震荡、主升、高位震荡、主跌。"},
    "K07": {"author": _K, "ref": "#9", "title": "主升段分歧买入", "quote": "你觉得这个题材的故事还能讲，要敢于大胆的在分歧的时候介入……看好，分歧，买入。"},
    "K08": {"author": _K, "ref": "#9", "title": "高位震荡轻仓", "quote": "我个人一般来说，是不建议博弈穿越（二波）的……高位震荡阶段，我会选择轻仓去应对。"},
    "K09": {"author": _K, "ref": "#9", "title": "主跌第二天尾盘找买点", "quote": "第一天杀跌是很恐怖的，但是第二天的杀跌，如果还是持续的话，尾盘就可以找买点。第三天肯定会有情绪的修复。"},
    "K10": {"author": _K, "ref": "#9/#11", "title": "先于市场发现可发酵事件", "quote": "先于市场发现可发酵的舆论事件，是一种高效的赚钱模式。"},
    "K11": {"author": _K, "ref": "#12", "title": "中位股风险最大", "quote": "切记切记不要去做中位股……尤其是在三进四、四进五时，淘汰率是非常高的。"},
    "K12": {"author": _K, "ref": "#14", "title": "新题材的最好时机", "quote": "当高位出现分歧或低位已经打出赚钱效应的时候，新题材往往是一个非常好的接入点。"},
    "K13": {"author": _K, "ref": "#15", "title": "大题材炒一个半月到两个月", "quote": "一个大级别的题材炒作通常是一个半月到两个月的时间。"},
    "K14": {"author": _K, "ref": "#17", "title": "妖股是换手走出来的", "quote": "好的妖股都是换手走出来的……一旦大家都认为好，都去顶一字的话，一般就不会走太远。"},
    "K15": {"author": _K, "ref": "#18", "title": "反包二板是好买点", "quote": "反包二板可以确认一个股票的二波启动，反包首板，第二天可能会被闷杀。"},
    "K16": {"author": _K, "ref": "#19", "title": "无题材时做次新", "quote": "每一波注册制新股，都是一定在没有题材可炒作的情况下才能走出来。"},
    "K17": {"author": _K, "ref": "#20", "title": "超短坚决止损", "quote": "当股票的走势与自己的预期不一致的时候，一定要杀伐果断，坚决止损。"},
    "K18": {"author": _K, "ref": "#21", "title": "分仓不加杠杆", "quote": "我一般是分四个票操作……真正的高手都是靠分仓，靠稳定才能做出来的。"},
    "K19": {"author": _K, "ref": "#23", "title": "持续性靠新闻发酵", "quote": "持续性需要题材不断的有新闻发酵，赚钱效应不断的延伸。"},
    "K20": {"author": _K, "ref": "#24", "title": "主流亏钱切次新超跌", "quote": "在主流题材亏钱的情况下，可以切换注册制次新、可转债、超跌……只要这个票够低价，够小盘一定会有人给你接力的。"},
    "K21": {"author": _K, "ref": "#25", "title": "第二天不涨停就走", "quote": "第二天不涨停，基本都会走，除非一些有地位的龙头股。"},
    "K22": {"author": _K, "ref": "#26", "title": "龙头有多条命", "quote": "龙头有多条命，有反包机会，有二波机会。"},
    "K23": {"author": _K, "ref": "#27", "title": "低价为王", "quote": "妖股大多是低价股才容易走出来，低价为王。"},
    "K24": {"author": _K, "ref": "#28", "title": "看昨日涨停今日表现", "quote": "昨日涨停的今天都基本没有溢价，那就是亏钱效应。昨日涨停的今天溢价多，那就是赚钱效应。"},
    "K25": {"author": _K, "ref": "#29", "title": "股性", "quote": "股性就是需要回顾它的历史，连板的成功率、每次涨停的溢价率。"},
    "K26": {"author": _K, "ref": "#30", "title": "高位核按钮利好低位", "quote": "高位核按钮对低位影响不大，高位龙头挂了，有时反而有助于低位补涨。"},
    "K27": {"author": _K, "ref": "#32/#33", "title": "弱转强", "quote": "当天烂板一般第二天竞价会很低，但是开盘后买盘很凶……就是弱转强。尾盘偷鸡板，这种也算是弱板。"},
    "K28": {"author": _K, "ref": "#34", "title": "弱转强不要竞价高开", "quote": "弱转强不喜欢竞价高开……弱转强不是体现在竞价上，而是被买上去的。"},
    "K29": {"author": _K, "ref": "#35", "title": "缩量板一字板不利接力", "quote": "缩量板和一字板对接力的资金是很不友好的……死亡换手就是换手率超过70%。"},
    "K30": {"author": _K, "ref": "#36", "title": "高标都会弱转强", "quote": "6个板以上的票都会出现弱转强。"},
    "K31": {"author": _K, "ref": "#38", "title": "复盘先过涨停", "quote": "首先把当天涨停的股票过一遍，然后再看看昨天涨停的股今天的溢价……再看看首板的票有没有什么新闻。"},
    "K32": {"author": _K, "ref": "#22", "title": "不断修正自己", "quote": "股市就是个经常打脸的地方，一旦打脸了，你就要修正自己的观点。"},
    "C01": {"author": _C, "ref": "二", "title": "情绪六阶段", "quote": "冰点期、回暖期、发酵期、高潮期、分歧期、退潮期，每个阶段对应明确策略与仓位上限。"},
    "C02": {"author": _C, "ref": "二", "title": "黄金窗口重仓、退潮空仓", "quote": "只在回暖→发酵→高潮的“黄金窗口”重仓，退潮期空仓是最佳策略。"},
    "C03": {"author": _C, "ref": "三", "title": "五大量化指标", "quote": "涨停家数、连板高度、连板晋级率、昨日涨停表现、大面股数量。"},
    "C04": {"author": _C, "ref": "四.1", "title": "弱转强打板", "quote": "集合竞价从负转正，量能是前一日同期3倍以上；开盘5分钟内快速拉升，不低于7%；必须有板块效应配合。"},
    "C05": {"author": _C, "ref": "四.2", "title": "龙头低吸", "quote": "必须是全市场绝对龙头……缩量止跌或长下影线出现时介入，次日不反包则果断止损。"},
    "C06": {"author": _C, "ref": "四.3", "title": "高位锁仓", "quote": "只锁主线龙头，不锁跟风；缩量加速时开始分批减仓；龙头出现滞涨或炸板时清仓。"},
    "C07": {"author": _C, "ref": "四.4", "title": "二波反包", "quote": "大势未死，龙头不死……反包必须以涨停形式出现，只适合小仓位博弈。"},
    "C08": {"author": _C, "ref": "五.1", "title": "5%止损", "quote": "触及止损线立刻离场，不补仓不幻想。"},
    "C09": {"author": _C, "ref": "五.2", "title": "单票仓位限制", "quote": "主线龙头不超过总资金30%，跟风不超过10%。"},
    "C10": {"author": _C, "ref": "五.3", "title": "不逆势操作", "quote": "退潮期坚决空仓，绝不抱有侥幸心理。"},
    "C11": {"author": _C, "ref": "五.4", "title": "不迷信席位", "quote": "龙虎榜滞后易被反向收割。"},
    "C12": {"author": _C, "ref": "五.5", "title": "预判须经市场验证", "quote": "市场永远是对的，预判必须得到市场验证才执行交易。"},
    "C13": {"author": _C, "ref": "六.2/六.3", "title": "只做龙头不做杂毛", "quote": "找出政策/产业/资金共振的主线题材……选择板块内辨识度最高、带动性最强的标的，只做龙头不做杂毛。"},
    "C14": {"author": _C, "ref": "七.1", "title": "真金白银的共识才是主线", "quote": "市场用真金白银砸出来的共识才是真主线，别信自己的意淫。"},
}


def is_known(principle_id: str) -> bool:
    return str(principle_id or "") in PRINCIPLES


def style_of(principle_id: str) -> str:
    """条目所属打法：K → kobe，C → chen，未知 → 空串。"""
    pid = str(principle_id or "")
    if pid.startswith("K") and pid in PRINCIPLES:
        return STYLE_KOBE
    if pid.startswith("C") and pid in PRINCIPLES:
        return STYLE_CHEN
    return ""


def filter_known(ids: Iterable[str]) -> List[str]:
    """保序去重，只留库内 id。"""
    seen = set()
    out: List[str] = []
    for pid in ids or []:
        text = str(pid or "").strip().upper()
        if text in PRINCIPLES and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def filter_for_style(ids: Iterable[str], style: str) -> List[str]:
    """只留某一派自己的条目（陈小群线不挂 K 条目，科比线不挂 C 条目）。"""
    return [pid for pid in filter_known(ids) if style_of(pid) == style]


__all__ = [
    "AUTHOR_CHEN",
    "AUTHOR_KOBE",
    "PRINCIPLES",
    "STYLE_CHEN",
    "STYLE_KOBE",
    "filter_for_style",
    "filter_known",
    "is_known",
    "style_of",
]
