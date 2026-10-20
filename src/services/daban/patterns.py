# -*- coding: utf-8 -*-
"""七种战法的候选生成器（M1–M7）。

两条打法线共用同一批候选；每个候选进哪条线、以什么行动级别进，由 ``styles`` 的门控矩阵决定。
这里只回答「盘后看，这只票符不符合某个模式的形态」，并给出次日的触发 / 放弃条件
（陈小群：预判必须经市场验证才执行）。

每条 ``signals`` 都挂理念条目 id；同时被两派支持的点同时挂 K 和 C，两条线各取自己的那部分。
``facts`` 是不带出处的客观数据（涨停原因、几天几板），两条线都显示。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from src.services.daban.constants import (
    MODE_CATCH_UP,
    MODE_FIRST_BOARD,
    MODE_LABELS,
    MODE_LEADER,
    MODE_REBOUND,
    MODE_SUB_NEW,
    MODE_SWITCH,
    MODE_WEAK_TO_STRONG,
    ROLE_CATCH_UP,
    ROLE_FOLLOWER,
    ROLE_FRONT,
    ROLE_LABELS,
    ROLE_MIDDLE,
    ROLE_OTHER,
    ROLE_THEME_LEADER,
    ROLE_TOTAL_LEADER,
    STAGE_ICE,
    STAGE_RETREAT,
)
from src.services.daban.kline import is_rebound_first_board
from src.services.daban.roles import is_low_price_small_cap


def _sig(text: str, *ids: str) -> Dict[str, Any]:
    return {"text": text, "principle_ids": list(ids)}


def _fmt_yi(value: Optional[float]) -> str:
    return "—" if value is None else f"{value / 1e8:.1f} 亿"


def _is_yizi(row: Dict[str, Any]) -> bool:
    return "一字" in str(row.get("limit_up_type") or "")


def _seal_ratio(row: Dict[str, Any]) -> Optional[float]:
    seal, circ = row.get("seal_amount"), row.get("circ_mv")
    if seal and circ:
        return float(seal) / float(circ)
    return None


class CandidateBuilder:
    """收集候选与排除名单；同一只票同一模式只留一条。"""

    def __init__(self, ctx: Dict[str, Any]):
        self.ctx = ctx
        self.rules = ctx["rules"]
        self.candidates: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self.excluded: Dict[str, Dict[str, Any]] = {}
        self.rejected: Dict[str, List[Dict[str, Any]]] = {}

    # ------------------------------------------------------------------ 公共

    def theme(self, key: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.ctx["theme_by_key"].get(key) if key else None

    def _context_signals(
        self, row: Dict[str, Any], role: str, theme: Optional[Dict[str, Any]], existing: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """模式之外、两派都认可的通用理由（主线地位、位置、低价、股性、人气）。

        只补充模式理由里还没出现过的出处，避免同一条理念重复出现。
        """
        used = {pid for item in existing for pid in item.get("principle_ids") or []}
        out: List[Dict[str, Any]] = []

        def _add(text: str, *ids: str) -> None:
            fresh = [pid for pid in ids if pid not in used]
            if fresh:
                out.append(_sig(text, *fresh))
                used.update(fresh)

        boards = int(row.get("boards") or row.get("y_boards") or 0)
        if theme and theme.get("is_main"):
            _add(
                f"属于主线「{theme['name']}」（第 {theme['rank']} 名，涨停 {theme['zt_count']} 只），"
                "是市场用真金白银砸出来的共识",
                "C13", "C14",
            )
            if theme.get("premium") is not None and theme["premium"] > 0:
                _add(f"题材内昨日涨停今日平均 {theme['premium']:+.1f}%，赚钱效应在延续", "K24", "C20")
        if role in (ROLE_TOTAL_LEADER, ROLE_THEME_LEADER):
            _add("绝对高位做龙头", "K33")
        elif 1 <= boards <= 2:
            _add(f"{boards} 板，绝对低位（不碰中位股）", "K33", "K11")
        price, circ = row.get("price"), row.get("circ_mv")
        if price is not None and circ and price <= 15 and circ <= 80e8:
            _add(f"股价 {price:.2f} 元、流通市值 {circ / 1e8:.0f} 亿，低价小盘", "K23")
        suc = row.get("suc_rate")
        if suc is not None and suc >= 0.7:
            _add(f"历史封板成功率 {suc:.0%}（同花顺口径，近似股性）", "K25")
        rank = self.ctx.get("hot_rank", {}).get(row.get("code"))
        if rank is not None and rank <= 50:
            _add(f"人气榜第 {rank} 名，人气带来流动性溢价", "K01")
        return out

    def add(
        self,
        row: Dict[str, Any],
        mode: str,
        *,
        role: str,
        theme_key: Optional[str],
        signals: List[Dict[str, Any]],
        triggers: List[Dict[str, Any]],
        abandon: List[Dict[str, Any]],
        origin: str,
        conditions: Optional[Dict[str, bool]] = None,
        notes: Optional[List[str]] = None,
        needs_kline_confirm: bool = False,
    ) -> None:
        code = row["code"]
        if code in self.excluded:
            return
        theme = self.theme(theme_key)
        facts = []
        if row.get("reason"):
            facts.append(f"涨停原因：{row['reason']}")
        if row.get("high_days"):
            facts.append(f"近期涨停：{row['high_days']}")
        if theme:
            facts.append(
                f"所属题材：{theme['name']}（第 {theme['rank']} 名，涨停 {theme['zt_count']} 只，"
                f"持续第 {theme['days']} 天，{theme['status_label']}）"
            )
        self.candidates[(code, mode)] = {
            "code": code,
            "name": row.get("name") or "",
            "mode": mode,
            "mode_label": MODE_LABELS[mode],
            "role": role,
            "role_label": ROLE_LABELS.get(role, role),
            "theme": theme_key,
            "theme_name": theme["name"] if theme else "",
            "theme_rank": theme["rank"] if theme else None,
            "theme_is_main": bool(theme and theme.get("is_main")),
            "origin": origin,
            "price": row.get("price"),
            "change_pct": row.get("change_pct"),
            "turnover_rate": row.get("turnover_rate"),
            "circ_mv": row.get("circ_mv"),
            "amount": row.get("amount"),
            "boards": int(row.get("boards") or row.get("y_boards") or 0),
            "limit_up_type": row.get("limit_up_type") or "",
            "first_time": row.get("first_time") or "",
            "last_time": row.get("last_time") or "",
            "open_num": int(row.get("open_num") or 0),
            "seal_ratio": _seal_ratio(row),
            "suc_rate": row.get("suc_rate"),
            "reason": row.get("reason") or "",
            "facts": facts,
            "signals": signals + self._context_signals(row, role, theme, signals),
            "triggers": triggers,
            "abandon": abandon,
            "conditions": conditions or {},
            "notes": notes or [],
            "needs_kline_confirm": needs_kline_confirm,
        }

    def reject(self, row: Dict[str, Any], reason: str, *ids: str, role: str = "") -> None:
        """某个模式不成立。只有这只票最终一个模式都没进时，才把第一条原因写进排除名单。"""
        self.rejected.setdefault(row["code"], []).append({
            "code": row["code"],
            "name": row.get("name") or "",
            "boards": int(row.get("boards") or row.get("y_boards") or 0),
            "role_label": ROLE_LABELS.get(role, ""),
            "reason": reason,
            "principle_ids": list(ids),
        })

    def finish(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        excluded = dict(self.excluded)
        kept_codes = {code for code, _ in self.candidates}
        for code, items in self.rejected.items():
            if code not in kept_codes and code not in excluded:
                excluded[code] = items[0]
        return list(self.candidates.values()), list(excluded.values())

    def exclude(self, row: Dict[str, Any], reason: str, *ids: str, role: str = "") -> None:
        code = row["code"]
        for key in [k for k in self.candidates if k[0] == code]:
            self.candidates.pop(key, None)
        if code not in self.excluded:
            self.excluded[code] = {
                "code": code,
                "name": row.get("name") or "",
                "boards": int(row.get("boards") or row.get("y_boards") or 0),
                "role_label": ROLE_LABELS.get(role, ""),
                "reason": reason,
                "principle_ids": list(ids),
            }


# ---------------------------------------------------------------------- 触发 / 放弃条件模板

_M2_TRIGGERS = [
    _sig("集合竞价从负转正，量能 ≥ 前一日同期 3 倍", "C04"),
    _sig("开盘 5 分钟内快速拉升，不低于 7%", "C04"),
    _sig("是盘中被买上去的，不是竞价高开", "K28"),
    _sig("同题材有股同步走强（板块效应）", "C04"),
    _sig("只能扫板：弱转强缩量，犹豫就买不到", "K28"),
]
_M2_ABANDON = [
    _sig("竞价高开放弃：做盘迹象太明显（高开幅度阈值为系统参数）", "K28"),
    _sig("板块无跟随的孤立拉升放弃", "C04"),
    _sig("弱转强次日、强转强第三天一般是卖点，别恋战", "K27"),
]


def _context_flags(ctx: Dict[str, Any]) -> Dict[str, bool]:
    m = ctx["metrics"]
    rules = ctx["rules"]
    themes = ctx["themes"]
    top = themes[0] if themes else None
    premium = m.get("yzt_premium")
    new_theme = any(
        t["novelty"] == "new" and t["zt_count"] >= int(rules.get("new_theme_min_zt", 3)) for t in themes
    )
    leader_hit = any(
        int(r.get("y_boards") or 0) >= max(3, ctx.get("y_max_height") or 0)
        and r.get("change_pct") is not None
        and r["change_pct"] <= -5
        for r in ctx.get("previous") or []
    )
    return {
        "market_alive": (m.get("zt_count") or 0) >= 30 and (premium is None or premium > -1) and new_theme,
        "no_mainline": (top["zt_count"] if top else 0) < int(rules.get("no_mainline_zt", 5)),
        "high_leader_hit": leader_hit,
        "switch_context": bool(
            leader_hit
            or ((m.get("delta") or {}).get("max_height") or 0) < 0
            or ctx["stage_key"] in (STAGE_RETREAT, STAGE_ICE)
            or ctx["kobe"].get("fall_day", 0) >= 1
        ),
    }


def _theme_profit(theme: Optional[Dict[str, Any]]) -> bool:
    """补涨前提：题材已出赚钱效应、亏钱效应未显现（科比 #5）。"""
    if not theme:
        return False
    premium = theme.get("premium")
    if premium is not None:
        return premium > 0
    return theme.get("status") in ("start", "rise")


# ---------------------------------------------------------------------- 生成


def generate_candidates(ctx: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, bool]]:
    """返回 ``(候选列表, 排除名单, 场景标记)``。"""
    b = CandidateBuilder(ctx)
    flags = _context_flags(ctx)
    rules = ctx["rules"]
    death = float(rules.get("death_turnover", 70.0))
    late = str(rules.get("weak_late_time", "143000"))
    max_height = int(ctx["metrics"].get("max_height") or 0)
    roles = ctx["roles"]

    # --- 当日涨停池 ---------------------------------------------------------
    for row in ctx["zt"]:
        if row.get("is_new"):
            continue
        info = roles.get(row["code"]) or {}
        role = info.get("role", ROLE_OTHER)
        theme_key = info.get("theme")
        theme = b.theme(theme_key)
        is_main = bool(theme and theme.get("is_main"))
        boards = int(row.get("boards") or 1)
        turnover = row.get("turnover_rate")
        yizi = _is_yizi(row)

        if role == ROLE_MIDDLE:
            b.exclude(
                row,
                f"中位股：{boards} 板，处在 3 板到最高板（{max_height} 板）之间，三进四、四进五淘汰率最高",
                "K11", role=role,
            )
            continue
        if role == ROLE_FOLLOWER:
            b.exclude(
                row,
                f"主线「{theme['name'] if theme else ''}」里的跟风股：既非前排，也不够低价小盘",
                "C13", "K20", role=role,
            )
            continue

        weak = row.get("change_tag") == "LIMIT_BACK" or (row.get("last_time") or "") >= late
        weak_bits = []
        if row.get("change_tag") == "LIMIT_BACK":
            weak_bits.append(f"炸板 {row.get('open_num') or 1} 次后回封")
        if (row.get("last_time") or "") >= late:
            weak_bits.append(f"末次封板 {row['last_time'][:2]}:{row['last_time'][2:4]}（尾盘板）")

        # M1 龙头
        if role in (ROLE_TOTAL_LEADER, ROLE_THEME_LEADER):
            consistent = yizi or ((turnover or 0) < 3 and (row.get("first_time") or "999999") <= "093500")
            label = "全市场最高板" if role == ROLE_TOTAL_LEADER else f"主线「{theme['name'] if theme else ''}」最高板"
            signals = [_sig(f"{label}（{boards} 板），辨识度最高", "C13", "K33")]
            if consistent:
                signals.append(_sig("今日一致（一字 / 缩量秒板）：一致顶一字的走不远，等分歧再介入", "K14"))
                triggers = [_sig("等分歧：开板换手、或低开承接后再介入，不追一字", "K14", "K07")]
            else:
                signals.append(_sig(f"今日换手 {turnover or 0:.1f}%，是分歧中走出来的换手板", "K14", "K07"))
                triggers = [_sig("次日分歧时介入：打板或低吸都可以，看好、分歧、买入", "K07")]
            triggers.append(_sig("只锁主线龙头；缩量加速时分批减仓", "C06"))
            if boards >= 6:
                signals.append(_sig("连板高度 ≥6，打开了市场空间", "C18"))
            b.add(
                row, MODE_LEADER, role=role, theme_key=theme_key, origin="zt",
                signals=signals, triggers=triggers,
                abandon=[
                    _sig("龙头滞涨或炸板时清仓", "C06"),
                    _sig("同题材补涨股隔日亏钱、龙头低吸不反包：周期见顶信号", "K04"),
                ],
                conditions={"total_leader": role == ROLE_TOTAL_LEADER, **flags},
                notes=["一致板，等分歧"] if consistent else [],
            )

        # M2 弱转强（弱板）
        if weak and not yizi and not is_main:
            b.reject(row, "弱板但不属于主线：弱转强必须有板块效应配合，孤立个股不做", "C04", role=role)
        elif weak and not yizi and turnover is not None and turnover > death:
            b.reject(row, f"换手 {turnover:.0f}% 超过 {death:.0f}%，死亡换手不会弱转强", "K29", role=role)
        elif weak and not yizi:
            signals = [_sig("今日" + "、".join(weak_bits) + "，属于弱板", "K27", "C04")]
            if boards >= 6:
                signals.append(_sig(f"{boards} 板高标，6 板以上的票都会出现弱转强", "K30"))
            b.add(
                row, MODE_WEAK_TO_STRONG, role=role, theme_key=theme_key, origin="zt",
                signals=signals, triggers=list(_M2_TRIGGERS), abandon=list(_M2_ABANDON),
                conditions=flags,
            )

        # M3 低位补涨
        lowcap_relay = boards <= 2 and is_main and role in (ROLE_FRONT, ROLE_CATCH_UP) and is_low_price_small_cap(row, rules)
        if lowcap_relay and yizi:
            b.reject(row, "一字板：买不到，次日接力资金也不友好", "K29", role=role)
        elif lowcap_relay:
            leader = theme.get("leader") if theme else None
            signals = [
                _sig(
                    f"主线「{theme['name']}」的低位{boards}板，股价 {row.get('price') or 0:.2f} 元、"
                    f"流通市值 {_fmt_yi(row.get('circ_mv'))}，够低价够小盘",
                    "K20", "K23",
                ),
            ]
            if leader:
                signals.append(_sig(f"题材龙头 {leader['name']}（{leader['boards']} 板）已打出空间，眼红效应", "K03"))
            if flags["high_leader_hit"]:
                signals.append(_sig("高位龙头核按钮，对低位影响不大、反而助补涨", "K26"))
            b.add(
                row, MODE_CATCH_UP, role=role, theme_key=theme_key, origin="zt",
                signals=signals,
                triggers=[
                    _sig("眼快手快：竞价或开盘即参与，稍慢一步就买不到", "K34"),
                ],
                abandon=[_sig("低位补涨隔日不赚钱、龙头低吸不反包：补涨结束，停止参与", "K04")],
                conditions={"theme_profit": _theme_profit(theme), **flags},
            )

        # M4 一进二：今天首板，次日打二板。原文只给了定位（科比 #12「低位就是做首板或者一进二」，
        # 陈小群回暖期「聚焦新题材首板/二板」），没有给选股标准；这里只按原文做筛选，
        # 首封时间等不是原文观点的信息放进 facts，不挂出处。
        first_board = boards == 1 and role != ROLE_FOLLOWER and theme and (is_main or theme.get("novelty") == "new")
        if first_board and yizi:
            b.reject(row, "一字首板：买不到，次日接力资金也不友好", "K29", role=role)
        elif first_board:
            is_new = theme.get("novelty") == "new"
            signals = [_sig(f"「{theme['name']}」{'主线' if is_main else '新题材'}的换手首板：绝对低位做首板 / 一进二", "K33")]
            if is_new:
                signals.append(_sig(f"「{theme['name']}」是近 10 日主线之外的新题材", "C15"))
            notes = ["回测：一进二过去一年整体次日溢价为负，只作试错（见 docs/daban-king.md）"]
            if row.get("first_time"):
                notes.append(f"首封 {row['first_time'][:2]}:{row['first_time'][2:4]}（系统补充信息，非原文观点）")
            # 原文与回测都支持的放弃条件：尾盘板（科比 #33 算弱板）、高价股（#27 低价为王）只观察。
            cap_action = None
            if (row.get("last_time") or "") >= late:
                signals.append(_sig("末次封板在尾盘：尾盘偷鸡板也算弱板，只观察", "K27"))
                cap_action = "watch"
            price = row.get("price")
            if price is not None and price >= float(rules.get("o12_max_price", 40.0)):
                signals.append(_sig(f"股价 {price:.2f} 元偏高：妖股大多是低价股，只观察", "K23"))
                cap_action = "watch"
            b.add(
                row, MODE_FIRST_BOARD, role=role, theme_key=theme_key, origin="zt",
                signals=signals,
                triggers=[
                    _sig("次日确认题材仍在扩散（同题材新增涨停）再打二板，不提前预判", "C12", "C22"),
                    _sig("复盘时查首板新闻，判断故事能不能继续讲", "K31", "K10"),
                ],
                abandon=[
                    _sig("次日一字或高开 ≥9% 不追：一字板对接力资金很不友好（回测同样为负）", "K29"),
                    _sig("昨日涨停今日普遍没有溢价（亏钱效应）时放弃", "K24", "C20"),
                ],
                conditions={**flags, "new_theme": is_new},
                notes=notes,
            )
            if cap_action:
                b.candidates[(row["code"], MODE_FIRST_BOARD)]["cap_action"] = cap_action

        # M5 反包首板（日 K 确认）
        if boards == 1 and int(row.get("hd_boards") or 0) >= 3:
            b.add(
                row, MODE_REBOUND, role=role, theme_key=theme_key, origin="zt",
                signals=[_sig(f"近期 {row.get('high_days')}，断板后今日重新涨停 = 反包首板", "K15", "K22")],
                triggers=[_sig("反包首板次日可能被闷杀：只在次日封住反包二板时打板", "K15", "C07")],
                abandon=[_sig("次日不能以涨停形式反包就放弃", "C07")],
                conditions=flags,
                needs_kline_confirm=True,
            )

        # M6 高低切 / 切换
        if boards <= 2 and not yizi and theme and theme.get("novelty") == "new" and flags["switch_context"]:
            b.add(
                row, MODE_SWITCH, role=role, theme_key=theme_key, origin="zt",
                signals=[
                    _sig(f"「{theme['name']}」与近 10 日主线完全不同，是新共识的候选（新题材萌芽）", "K05", "C16"),
                    _sig("出现在高位分歧 / 主跌之后，正是新题材的接入点", "K12", "K35"),
                ],
                triggers=[
                    _sig("控制仓位试错，次日题材有扩散（新增涨停）再加", "K38", "C12"),
                ],
                abandon=[_sig("次日题材无扩散、走势与预期不符，坚决止损", "K17")],
                conditions=flags,
            )

    # --- 昨日涨停池：高标核按钮 / 首阴（弱转强、龙头低吸、二波反包） ------------
    today_codes = {row["code"] for row in ctx["zt"]}
    y_max = int(ctx.get("y_max_height") or 0)
    for row in ctx.get("previous") or []:
        code = row["code"]
        y_boards = int(row.get("y_boards") or 1)
        change = row.get("change_pct")
        if code in today_codes or change is None or y_boards < 3:
            continue
        theme_key = (ctx["stock_themes"].get(code) or [None])[0] or ctx["yesterday_theme"].get(code)
        theme = b.theme(theme_key)
        is_y_leader = y_boards >= max(3, y_max)
        status = "核按钮" if change <= -7 else ("首阴" if change < 0 else "断板")
        base = {**row, "boards": y_boards}
        # 昨日最高标今天被别人超过了，就不再是「全市场绝对龙头」，按板块龙头对待。
        still_top = y_boards >= max_height
        leader_role = ROLE_TOTAL_LEADER if still_top else ROLE_THEME_LEADER
        if is_y_leader and change >= 0:
            # M1：最高标断板但没跌（分歧），看反包 / 二波（科比 K21、K22；陈小群 C05）
            b.add(
                base, MODE_LEADER, role=leader_role, theme_key=theme_key, origin="previous",
                signals=[
                    _sig(f"昨日 {y_boards} 板的全市场最高标，今日断板但收涨（{change:+.1f}%），是龙头的分歧日", "K22", "K07"),
                    _sig("有地位的龙头不涨停也可以锁一下仓，看反包", "K21"),
                ],
                triggers=[
                    _sig("次日以涨停反包确认二波，再按龙头对待", "K22"),
                    _sig("看好、分歧、买入：分歧日打板或低吸都可以", "K07"),
                ],
                abandon=[_sig("次日不反包、继续走弱：龙头见顶，转而关注低位新题材", "K04", "K12")],
                conditions={**flags, "total_leader": still_top},
                notes=["断板，不是涨停股"],
            )
        if change < 0 and is_y_leader:
            # M1：龙头恐慌低吸（陈小群 C05）/ 主跌第二天尾盘（科比 K09）
            b.add(
                base, MODE_LEADER, role=leader_role, theme_key=theme_key, origin="previous",
                signals=[
                    _sig(f"昨日 {y_boards} 板的全市场最高标，今日{status}（{change:+.1f}%）", "C05", "K22"),
                    _sig("龙头有多条命：恐慌杀跌是低吸 / 反包的观察点", "K22", "C05"),
                ],
                triggers=[
                    _sig("缩量止跌或收出长下影时低吸，次日不反包就止损", "C05"),
                    _sig("主跌第二天若仍在杀，尾盘找买点", "K09"),
                ],
                abandon=[_sig("次日不反包、继续亏损甚至核按钮：果断止损", "C05", "K04")],
                conditions={**flags, "total_leader": still_top},
                notes=[f"{status}，不是涨停股"],
            )
        if change <= -3 and y_boards >= 3 and theme and theme.get("is_main"):
            # M2：主线高标核按钮后的弱转强
            b.add(
                base, MODE_WEAK_TO_STRONG, role=ROLE_THEME_LEADER, theme_key=theme_key, origin="previous",
                signals=[_sig(f"主线高标 {y_boards} 板今日{status}（{change:+.1f}%），次日有弱转强预期", "K27", "C04")]
                + ([_sig("6 板以上的票都会出现弱转强", "K30")] if y_boards >= 6 else []),
                triggers=list(_M2_TRIGGERS), abandon=list(_M2_ABANDON), conditions=flags,
                notes=[f"{status}，不是涨停股"],
            )
        if change < 0 and y_boards >= 3:
            # M5：首阴次日反包（陈小群二波反包）
            b.add(
                base, MODE_REBOUND, role=leader_role if is_y_leader else ROLE_OTHER,
                theme_key=theme_key, origin="previous",
                signals=[_sig(f"{y_boards} 连板后今日首阴（{change:+.1f}%）：大势未死，龙头不死", "C07", "K22")],
                triggers=[_sig("次日以涨停形式反包、吃掉今天阴线实体才成立", "C07")],
                abandon=[_sig("次日不涨停即视为反包失败；只用小仓位博弈", "C07")],
                conditions=flags,
                notes=["首阴，不是涨停股"],
            )

    # --- 次新股池（科比 K16 / K20） -----------------------------------------
    for row in ctx.get("sub_new") or []:
        if row.get("change_pct") is None or row["change_pct"] < 5 or not row.get("open_days"):
            continue
        b.add(
            {**row, "boards": 0}, MODE_SUB_NEW, role=ROLE_OTHER, theme_key=None, origin="sub_new",
            signals=[
                _sig(f"开板后第 {row['open_days']} 天的次新，今日 {row['change_pct']:+.1f}%", "K16"),
                _sig("主流题材亏钱时，次新是资金的去处", "K20"),
            ],
            triggers=[_sig("只在市场无题材可炒、次新形成合力时参与", "K16")],
            abandon=[_sig("主流题材重新出现赚钱效应时，次新让位", "K16")],
            conditions=flags,
        )

    candidates, excluded = b.finish()
    return candidates, excluded, flags


def confirm_with_kline(candidates: List[Dict[str, Any]], features: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """用日 K 校验需要形态确认的候选（反包首板），并给所有候选挂上形态特征。"""
    kept = []
    for cand in candidates:
        feat = features.get(cand["code"]) or {}
        cand["kline"] = {k: feat.get(k) for k in (
            "available", "limit_count", "next_day_premium", "next_day_samples", "recent_streak",
            "today_change", "volume_ratio", "lower_shadow",
        )}
        if cand.get("needs_kline_confirm"):
            if not feat.get("available"):
                cand["notes"].append("日K不可用，反包形态未经确认")
            elif not is_rebound_first_board(feat):
                continue
        if cand["mode"] == MODE_LEADER and cand["origin"] == "previous" and feat.get("available"):
            shadow = feat.get("lower_shadow")
            ratio = feat.get("volume_ratio")
            if shadow is not None and shadow >= 0.3:
                cand["signals"].append(_sig(f"今日收出长下影（下影占振幅 {shadow:.0%}）", "C05"))
            if ratio is not None and ratio < 1:
                cand["signals"].append(_sig(f"今日缩量（量比 {ratio:.2f}），有止跌迹象", "C05"))
        kept.append(cand)
    return kept


__all__ = ["CandidateBuilder", "confirm_with_kline", "generate_candidates"]
