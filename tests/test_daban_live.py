# -*- coding: utf-8 -*-
"""盘中首板雷达：门控、降级、先到先得的仓位分配、炸板留档、前一交易日上下文、留档与回填。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.services.daban import live
from src.services.daban.forecast import active_themes, build_forecasts, summarize_forecasts

from tests.daban_fixtures import rules, zt_row

PHASE_HIGH = {"available": True, "trade_date": "2026-10-19", "chen_stage": "warm", "chen_label": "回暖期",
              "kobe_gate": "high_shock", "kobe_label": "高位震荡"}
PHASE_RETREAT = {"available": True, "trade_date": "2026-10-19", "chen_stage": "retreat", "chen_label": "退潮期",
                 "kobe_gate": "fall_1", "kobe_label": "主跌第 1 天"}
PHASE_BEST = {"available": True, "trade_date": "2026-10-19", "chen_stage": "warm", "chen_label": "回暖期",
              "kobe_gate": "fall_2", "kobe_label": "主跌第 2 天"}
PHASE_RISE = {"available": True, "trade_date": "2026-10-19", "chen_stage": "ferment", "chen_label": "发酵期",
              "kobe_gate": "main_rise", "kobe_label": "主升"}


def _run(rows, *, phase=PHASE_HIGH, broken=(), quotes=None, memory=None, recent_mains=(), yesterday=(), max_picks=4):
    return live.build_hits(rows=rows, broken=list(broken), phase=phase, rules=rules(), quotes=quotes or {},
                           memory=memory or {}, recent_mains=list(recent_mains), yesterday_codes=list(yesterday),
                           max_picks=max_picks)


def _hit(result, code, style="kobe"):
    return next(h for h in result["hits"] if h["code"] == code and h["style"] == style)


def _robot(code, name, first_time, **kw):
    return zt_row(code, name, reason="人形机器人", first_time=first_time, **kw)


class TestGates:
    def test_kobe_trial_in_high_shock_cites_light_position(self):
        hit = _hit(_run([_robot("600001", "甲", "100000")]), "600001")
        # 门控放行 = 试错；但这只已经封板、买不进去，所以动作降为观察（不再占名额）
        assert hit["gate_action"] == "trial"
        assert hit["action"] == "watch" and any("已经封板" in n for n in hit["notes"])
        assert {"K08", "K33"} <= {pid for r in hit["reasons"] for pid in r["principle_ids"]}

    def test_kobe_refuses_after_first_fall_day(self):
        result = _run([_robot("600001", "甲", "100000")], phase=PHASE_RETREAT)
        assert not [h for h in result["hits"] if h["style"] == "kobe"]
        assert any(b["style"] == "kobe" and "主跌第 1 天" in b["reason"] for b in result["blocked"])

    def test_kobe_actionable_after_second_fall_day_cites_k09(self):
        hit = _hit(_run([_robot("600001", "甲", "100000")], phase=PHASE_BEST), "600001")
        assert hit["gate_action"] == "actionable"
        assert any("K09" in r["principle_ids"] for r in hit["reasons"])

    def test_chen_needs_new_theme_and_peers(self):
        rows = [_robot("600001", "甲", "100000"), _robot("600002", "乙", "101000"), _robot("600003", "丙", "102000")]
        result = _run(rows)
        assert _hit(result, "600001", "chen")["gate_action"] == "watch"
        assert _hit(result, "600002", "chen")["gate_action"] == "watch"
        third = _hit(result, "600003", "chen")
        assert third["peers"] == 2 and third["gate_action"] == "trial"
        assert any("C14" in r["principle_ids"] for r in third["reasons"])

    def test_chen_new_theme_filter_uses_recent_mains(self):
        rows = [_robot("600001", "甲", "100000"), _robot("600002", "乙", "101000"), _robot("600003", "丙", "102000")]
        third = _hit(_run(rows, recent_mains=["机器人"]), "600003", "chen")
        assert third["gate_action"] == "watch" and any("新题材" in n for n in third["notes"])

    def test_chen_refuses_retreat(self):
        result = _run([_robot("600001", "甲", "100000")], phase=PHASE_RETREAT)
        assert not [h for h in result["hits"] if h["style"] == "chen"]

    def test_only_first_boards(self):
        rows = [_robot("600001", "甲", "100000"), zt_row("600002", "乙", boards=3, reason="人形机器人")]
        assert {h["code"] for h in _run(rows)["hits"]} == {"600001"}


class TestDemotions:
    def test_auction_touch_is_watch(self):
        hit = _hit(_run([_robot("600001", "甲", "092500")]), "600001")
        assert hit["action"] == "watch" and any("开盘一分钟" in n for n in hit["notes"])

    def test_one_word_board_is_watch(self):
        hit = _hit(_run([_robot("600001", "甲", "100000", limit_up_type="一字板")]), "600001")
        assert hit["action"] == "watch"

    def test_late_first_touch_is_watch(self):
        assert _hit(_run([_robot("600001", "甲", "143500")]), "600001")["action"] == "watch"

    def test_late_reseal_does_not_change_the_call(self):
        # 10:00 首次触板、14:50 回封：买点在 10:00，那时看不到 14:50 的事，不能事后降级。
        hit = _hit(_run([_robot("600001", "甲", "100000", last_time="145000", open_num=2)]), "600001")
        assert hit["gate_action"] == "trial"


class TestAllocation:
    def test_closest_to_limit_gets_the_slots(self):
        """锁定 = 此刻还能买：只锁未涨停候选，按距涨停越近越优先，且不超过阶段仓位上限。"""
        rows = [_pre(f"6000{i:02d}", f"股{i}", gap) for i, gap in [(1, 5.5), (2, 1.0), (3, 3.0), (4, 0.5), (5, 4.0)]]
        result = _preboard_run(rows, phase=PHASE_HIGH)      # 高位震荡：总仓位 30%，每只 10%
        funded = [h for h in result["hits"] if h["style"] == "kobe" and h["slot"]]
        assert [h["code"] for h in funded] == ["600004", "600002", "600003"]
        assert sum(h["position_pct"] for h in funded) == 30
        assert all("用满" in " ".join(h["notes"]) for h in result["hits"]
                   if h["style"] == "kobe" and not h["slot"])

    def test_max_four_picks(self):
        """「我一般是分四个票操作」（科比 #21）：名额总数本身就是纪律，不靠仓位上限间接限制。

        两个题材各有 4 个配额、主升期仓位上限 100%（单只 10%），两道约束都不拦；只有名额总数拦得住。
        """
        cfg = rules()
        blocks = [{"name": "机器人概念", "limit_up_num": 9, "days": 3, "members": []},
                  {"name": "人工智能", "limit_up_num": 7, "days": 3, "members": []}]
        rows, mem = [], {}
        for idx, theme in enumerate(["机器人", "人工智能"]):
            for k in range(1, 5):
                code = f"60{idx}{k:03d}"
                rows.append(_pre(code, f"{theme}{k}", 0.5 + k / 10))
                mem[code] = {theme}
        result = live.build_hits(rows=[], broken=[], phase=PHASE_RISE, rules=cfg, memory=mem,
                                 preboard=rows, blocks=blocks, max_picks=4,
                                 news_lookup=lambda names: {n: 9 for n in names})
        funded = [h for h in result["hits"] if h["style"] == "kobe" and h["slot"]]
        assert sorted(h["slot"] for h in funded) == [1, 2, 3, 4]
        assert result["lanes"]["kobe"]["picks"] == 4
        assert any("名额 4 个已锁定用满" in " ".join(h["notes"]) for h in result["hits"]
                   if h["style"] == "kobe" and not h["slot"])

    def test_theme_quota_top_three_get_four_others_two(self):
        """热度排名前三的题材各 4 个锁定名额，其余各 2 个（仓位上限与名额总数放开时只看配额）。"""
        cfg = rules()
        cfg["live_caps"]["kobe"]["main_rise"] = 1000          # 只看配额，不让仓位上限干扰
        blocks = [
            {"name": "机器人概念", "limit_up_num": 9, "days": 3, "members": []},   # 第 1 名 → 4
            {"name": "人工智能", "limit_up_num": 7, "days": 3, "members": []},     # 第 2 名 → 4
            {"name": "储能", "limit_up_num": 5, "days": 3, "members": []},         # 第 3 名 → 4
            {"name": "军工", "limit_up_num": 4, "days": 3, "members": []},         # 第 4 名 → 2
        ]
        rows, mem = [], {}
        for idx, theme in enumerate(["机器人", "人工智能", "储能", "军工"]):
            for k in range(1, 7):
                code = f"60{idx}{k:03d}"
                rows.append(_pre(code, f"{theme}{k}", 1.0))
                mem[code] = {theme}
        result = live.build_hits(rows=[], broken=[], phase=PHASE_RISE, rules=cfg, memory=mem,
                                 preboard=rows, blocks=blocks, max_picks=20,
                                 news_lookup=lambda names: {n: 9 for n in names})
        by_theme = {}
        for h in result["hits"]:
            if h["style"] == "kobe" and h["slot"]:
                by_theme[h["theme"]] = by_theme.get(h["theme"], 0) + 1
        assert by_theme == {"机器人概念": 4, "人工智能": 4, "储能": 4, "军工": 2}
        quotas = {t["name"]: t["quota"] for t in result["hot_themes"]}
        assert quotas == {"机器人概念": 4, "人工智能": 4, "储能": 4, "军工": 2}

    def test_new_theme_ranked_into_top_three_gets_four_slots(self):
        """盘中新题材挤进前三，名额自动变成 4 个。"""
        cfg = rules()
        cfg["live_caps"]["kobe"]["main_rise"] = 1000
        blocks = [{"name": "人工智能", "limit_up_num": 9, "days": 3, "members": []},
                  {"name": "储能", "limit_up_num": 8, "days": 3, "members": []},
                  {"name": "军工", "limit_up_num": 7, "days": 3, "members": []},
                  {"name": "新题材", "limit_up_num": 6, "days": 2, "members": []}]   # 第 4 名 → 2
        rows = [_pre(f"60000{i}", f"新{i}", 1.0) for i in range(1, 5)]
        mem = {r["code"]: {"新题材"} for r in rows}
        result = live.build_hits(rows=[], broken=[], phase=PHASE_RISE, rules=cfg, memory=mem,
                                 preboard=rows, blocks=blocks,
                                 news_lookup=lambda names: {n: 9 for n in names})
        assert result["hot_themes"][3]["quota"] == 2
        assert sum(1 for h in result["hits"] if h["theme"] == "新题材" and h["slot"]) == 2

        # 新题材热度超上来（涨停数变多，排到第 2）→ 名额 4
        hotter = [{"name": "人工智能", "limit_up_num": 9, "days": 3, "members": []},
                  {"name": "新题材", "limit_up_num": 8, "days": 2, "members": []},
                  {"name": "储能", "limit_up_num": 7, "days": 3, "members": []},
                  {"name": "军工", "limit_up_num": 6, "days": 3, "members": []}]
        live.clear_cache()
        result = live.build_hits(rows=[], broken=[], phase=PHASE_RISE, rules=cfg, memory=mem,
                                 preboard=rows, blocks=hotter,
                                 news_lookup=lambda names: {n: 9 for n in names})
        quota = next(t["quota"] for t in result["hot_themes"] if t["name"] == "新题材")
        assert quota == 4
        assert sum(1 for h in result["hits"] if h["theme"] == "新题材" and h["slot"]) == 4

    def test_sealed_first_board_never_takes_a_slot(self):
        """已经封板的票出现时已经买不进去，不占名额，只作对照。"""
        result = _run([_robot("600001", "甲", "100000")], phase=PHASE_BEST)
        hit = _hit(result, "600001")
        assert hit["slot"] is None and hit["action"] == "watch"
        assert result["lanes"]["kobe"]["picks"] == 0

    def test_lanes_carry_sell_rules(self):
        lanes = _run([_robot("600001", "甲", "100000")])["lanes"]
        assert any("K21" in r["principle_ids"] for r in lanes["kobe"]["sell_rules"])
        assert any("C08" in r["principle_ids"] for r in lanes["chen"]["sell_rules"])


class TestThemeCards:
    """今日主线题材卡片上的数：名额与候选都按派分开，候选与页面「未锁定潜力股」同源同判据。"""

    def test_theme_key_bridges_block_and_reason_naming(self):
        """卡片的 key 来自题材榜（「机器人概念」），票的题材名来自涨停原因聚类（「人形机器人」）。

        两套写法必须落到同一个 ``theme_key`` 上，否则页面按题材一筛就什么都剩不下。
        """
        rows = [zt_row("600001", "甲", reason="人形机器人", first_time="100000"),
                zt_row("600002", "乙", reason="人形机器人", first_time="101000")]
        result = live.build_hits(rows=rows, broken=[], phase=PHASE_BEST, rules=rules(), blocks=HOT_BLOCK,
                                 news_lookup=lambda names: {n: 9 for n in names})
        assert [t["key"] for t in result["hot_themes"]] == ["机器人概念"]
        for hit in result["hits"]:
            assert hit["theme"] == "人形机器人"            # 展示名照旧给涨停原因聚类的结果
            assert hit["theme_key"] == "机器人概念"         # 筛选 / 名额 / 候选计数一律用卡片的 key

    def test_slots_and_candidates_are_counted_per_style(self):
        """一个题材的名额是「每派各 quota 个」，不是两派共用；候选 = 该派没拿到名额的票数。"""
        rows = [_pre(f"6000{i:02d}", f"股{i}", 0.5 + i / 10) for i in range(1, 8)]
        result = _preboard_run(rows, phase=PHASE_RISE, yesterday_weak={r["code"] for r in rows})
        theme = result["hot_themes"][0]
        assert theme["key"] == "机器人概念" and theme["quota"] == 4
        # 两派各自锁满 4 个（题材配额 4、每派名额总数 4），剩下 3 只各自留在自己的候选里
        assert theme["by_style"] == {"chen": {"used": 4, "candidates": 3},
                                     "kobe": {"used": 4, "candidates": 3}}
        assert (theme["used"], theme["candidates"]) == (8, 6)
        for style in ("chen", "kobe"):
            mine = [h for h in result["hits"] if h["style"] == style]
            assert sum(1 for h in mine if h["slot"] is None) == theme["by_style"][style]["candidates"]
            assert result["lanes"][style]["themes"][0]["used"] == 4

    def test_locked_record_without_a_live_row_still_counts_on_its_theme(self):
        """已锁定但这一轮上游没返回的票（留档行）也要算在它题材的名额里。"""
        locked = _locked(("kobe", "600099", "trial", 10, 1, "094000"))
        result = _preboard_run([_pre("600001", "甲", 1.0)], phase=PHASE_RISE, locked=locked)
        missing = _hit(result, "600099")
        assert missing["status"] == "missing" and missing["theme_key"] == "机器人概念"
        assert result["hot_themes"][0]["by_style"]["kobe"] == {"used": 2, "candidates": 0}


HOT_BLOCK = [{"name": "机器人概念", "limit_up_num": 5, "days": 3, "members": []}]


def _pre(code, name, gap, *, volume=5.0, first="100000", change=6.0):
    """一只还没涨停、距涨停 gap% 的候选。"""
    price = 10.0
    return {"code": code, "name": name, "price": price, "limit_up": round(price * (1 + gap / 100), 2),
            "change_pct": change, "circ_mv": 5e9, "volume_ratio": volume, "turnover_rate": 8.0,
            "time": first}


def _preboard_run(rows, *, phase=PHASE_BEST, max_picks=4, locked=None, yesterday_weak=()):
    codes = {r["code"] for r in rows}
    return live.build_hits(
        rows=[], broken=[], phase=phase, rules=rules(), memory={c: {"机器人"} for c in codes},
        preboard=rows, blocks=HOT_BLOCK, news_lookup=lambda names: {n: 9 for n in names},
        max_picks=max_picks, locked=locked or {}, yesterday_weak=set(yesterday_weak),
    )


def _locked(*rows):
    return live.locked_from_records([
        {"style": style, "code": code, "name": code, "action": action, "position_pct": pct, "slot": slot,
         "first_time": first, "seen_at": first, "limit_price": 11.0, "theme": "机器人", "phase": "",
         "open_count": 0}
        for style, code, action, pct, slot, first in rows
    ])


class TestLock:
    """名额一旦给出当天锁定：已给的不动，只分剩下的；更近的晚到票也不会把已锁定的挤掉。"""

    def test_locked_slots_survive_a_closer_late_arrival(self):
        locked = _locked(*[("kobe", f"6000{i}", "trial", 10, i, f"0941{i}0") for i in range(1, 5)])
        rows = [_pre(f"6000{i}", f"股{i}", 1.0, first=f"0941{i}0") for i in range(1, 5)]
        rows.append(_pre("600099", "晚到", 0.2, first="094000"))
        result = live.build_hits(rows=[], broken=[], phase=PHASE_RISE, rules=rules(),
                                 memory={r["code"]: {"机器人"} for r in rows},
                                 preboard=rows, blocks=HOT_BLOCK,
                                 news_lookup=lambda names: {n: 9 for n in names}, locked=locked)
        late = _hit(result, "600099")
        assert late["action"] == "watch" and "已满" in " ".join(late["notes"])
        kept = sorted(h["code"] for h in result["hits"] if h["style"] == "kobe" and h["slot"])
        assert kept == ["60001", "60002", "60003", "60004"]
        assert all(h["locked_at"] for h in result["hits"] if h["slot"])

    def test_locked_pick_keeps_action_even_if_conditions_change(self):
        locked = _locked(("chen", "600003", "trial", 5, 1, "102000"))
        rows = [_pre("600001", "甲", 1.0, first="100000"), _pre("600002", "乙", 1.0, first="101000"),
                _pre("600003", "丙", 1.0, first="102000")]
        result = live.build_hits(rows=[], broken=[], phase=PHASE_HIGH, rules=rules(),
                                 memory={r["code"]: {"机器人"} for r in rows}, preboard=rows,
                                 blocks=HOT_BLOCK, news_lookup=lambda names: {n: 9 for n in names},
                                 recent_mains=["机器人"], locked=locked)
        hit = _hit(result, "600003", "chen")
        assert hit["action"] == "trial" and hit["slot"] == 1 and hit["position_pct"] == 5

    def test_remaining_slots_and_room_go_to_new_picks(self):
        locked = _locked(("kobe", "600001", "trial", 10, 1, "100000"), ("kobe", "600002", "trial", 10, 2, "100500"))
        rows = [_pre("600001", "甲", 3.0, first="100000"), _pre("600002", "乙", 3.0, first="100500"),
                _pre("600003", "丙", 1.0, first="101000"), _pre("600004", "丁", 0.5, first="101500")]
        result = live.build_hits(rows=[], broken=[], phase=PHASE_HIGH, rules=rules(),
                                 memory={r["code"]: {"机器人"} for r in rows}, preboard=rows,
                                 blocks=HOT_BLOCK, news_lookup=lambda names: {n: 9 for n in names}, locked=locked)
        # 剩余 10% 仓位给更近的丁（0.5%），丙（1%）只观察
        assert _hit(result, "600004")["slot"] == 3 and _hit(result, "600004")["position_pct"] == 10
        assert _hit(result, "600003")["slot"] is None
        assert result["lanes"]["kobe"]["used"] == 30

    def test_locked_pick_missing_from_data_is_still_shown(self):
        locked = _locked(("kobe", "600001", "trial", 10, 1, "100000"))
        result = live.build_hits(rows=[], broken=[], phase=PHASE_HIGH, rules=rules(), locked=locked)
        hit = _hit(result, "600001")
        assert hit["status"] == "missing" and hit["slot"] == 1 and hit["action"] == "trial"
        assert result["lanes"]["kobe"]["picks"] == 1

    def test_records_without_first_time_never_hold_a_slot(self):
        locked = live.locked_from_records([
            {"style": "kobe", "code": "600001", "action": "trial", "first_time": "—", "slot": 1},
            {"style": "kobe", "code": "600002", "action": "trial", "first_time": "100000", "slot": 2},
        ])
        assert set(locked["kobe"]) == {"600002"}

    def test_records_without_slot_get_sequential_slots(self):
        locked = live.locked_from_records([
            {"style": "kobe", "code": "600002", "action": "trial", "first_time": "100500", "slot": None},
            {"style": "kobe", "code": "600001", "action": "trial", "first_time": "100000", "slot": None},
            {"style": "kobe", "code": "600003", "action": "watch", "first_time": "101000", "slot": None},
        ])
        assert {code: row["slot"] for code, row in locked["kobe"].items()} == {"600001": 1, "600002": 2}


class TestFacts:
    def test_quote_confirms_seal_without_citation(self):
        quotes = {"600001": {"price": 11.0, "limit_up": 11.0, "sell1_volume": 0}}
        hit = _hit(_run([_robot("600001", "甲", "100000", price=11.0)], quotes=quotes), "600001")
        assert hit["sealed"] is True and hit["limit_price"] == 11.0
        assert not any("盘口" in r["text"] for r in hit["reasons"])

    def test_limit_price_derived_without_quote(self):
        assert live.limit_price_of({"code": "600001", "price": 11.0, "change_pct": 10.0}) == 11.0
        assert live.limit_price_of({"code": "300001", "price": 9.5, "change_pct": -5.0}) == 12.0

    def test_peers_have_no_lookahead(self):
        result = _run([_robot("600001", "甲", "100000"), _robot("600002", "乙", "110000")])
        assert _hit(result, "600001")["peers"] == 0 and _hit(result, "600002")["peers"] == 1


# ---------------------------------------------------------------------- 服务层


class _Repo:
    def __init__(self, snapshot=None, pools=None):
        self.snapshot = snapshot
        self.pools = pools or {}
        self.live = {}

    def snapshot_for_trade_date(self, day):
        return self.snapshot if self.snapshot and self.snapshot.trade_date == day else None

    def history(self, *, limit=10, before=None):
        return []

    def pool_rows(self, day, pool="zt"):
        return self.pools.get(pool, [{"code": "600009"}])

    def reason_memory(self, since, pool="zt", *, until=None):
        return []

    def upsert_live_hits(self, day, hits, *, final):
        rows = self.live.setdefault(day, {})
        inserted = []
        for hit in hits:
            key = (hit["style"], hit["code"])
            if key not in rows:
                rows[key] = dict(hit)
                inserted.append(hit)
        return inserted

    def pending_live_dates(self):
        return []

    def live_hits(self, day):
        return [dict(row) for row in self.live.get(day, {}).values()]


def _service(repo, **overrides):
    config = SimpleNamespace(
        daban_fetch_timeout_sec=10, daban_live_cache_ttl_sec=0, daban_rules_file="", daban_max_actionable=4,
        daban_live_push_enabled=False, daban_live_poll_sec=10,
    )
    for key, value in overrides.items():
        setattr(config, key, value)
    return live.LiveRadarService(repository=repo, config=config)


def _pools(monkeypatch, zt_count=45):
    from src.services.daban import fetchers

    def pool(kind, day, *, timeout, include_bse):
        rows = [zt_row(f"60{i:04d}", f"股{i}", reason="人形机器人") for i in range(zt_count)] if kind == "zt" else []
        return {"ok": True, "value": {"rows": rows}}

    monkeypatch.setattr(fetchers, "fetch_limit_pool", pool)
    monkeypatch.setattr(fetchers, "fetch_ladder", lambda day, *, timeout: {"ok": True, "value": {"ladder": {}}})
    monkeypatch.setattr(fetchers, "fetch_em", lambda kind, day, *, timeout, include_bse: {"ok": False})


class TestPreviousContext:
    def test_uses_the_review_of_the_previous_trade_day(self, monkeypatch):
        from src.services.daban import fetchers

        live.clear_cache()
        monkeypatch.setattr(fetchers, "previous_trade_date", lambda day, **kw: {"2026-10-20": "2026-10-19"}[day])
        snap = SimpleNamespace(trade_date="2026-10-19", id=1, payload=json.dumps({
            "stage": {"key": "retreat", "label": "退潮期"}, "kobe_stage": {"gate_key": "fall_2", "label": "主跌第 2 天"},
            "metrics": {"zt_count": 33, "dt_count": 49, "lianban_count": 9},
        }))
        ctx = _service(_Repo(snap)).previous_context("2026-10-20")
        assert ctx["source"] == "review" and ctx["kobe_gate"] == "fall_2"
        assert ctx["yesterday_codes"] == ["600009"]
        # 门控依据要一起带出来：页面上的「今日门控」卡要能解释为什么是这个阶段。
        assert ctx["metrics"]["dt_count"] == 49 and ctx["stage"]["key"] == "retreat"
        assert ctx["kobe_stage"]["gate_key"] == "fall_2"

    def test_phase_reason_explains_both_open_and_closed_gates(self):
        cfg = rules()
        allowed = live._phase_reason("kobe", PHASE_BEST, cfg)
        assert "主跌第二天" in allowed["text"] and allowed["principle_ids"] == ["K09"]
        blocked = live._phase_reason("kobe", PHASE_RETREAT, cfg)
        assert "此阶段不打首板" in blocked["text"] and blocked["principle_ids"] == []
        assert "退潮期" in live._phase_reason("chen", PHASE_RETREAT, cfg)["text"]

    def test_yesterday_weak_excludes_clean_limit_ups(self, monkeypatch):
        """弱转强只认昨日的弱板：炸板 / 盘中开过板 / 尾盘偷鸡板。干净涨停的票今天再涨是二板，不是弱转强。"""
        from src.services.daban import fetchers

        live.clear_cache()
        monkeypatch.setattr(fetchers, "previous_trade_date", lambda day, **kw: {"2026-10-20": "2026-10-19"}[day])
        snap = SimpleNamespace(trade_date="2026-10-19", id=1, payload=json.dumps({
            "stage": {"key": "warm", "label": "回暖期"}, "kobe_stage": {"gate_key": "fall_2", "label": "主跌第 2 天"},
        }))
        pools = {
            "zt": [
                {"code": "600001", "first_time": "100000", "last_time": "100000"},   # 干净涨停 → 不算弱
                {"code": "600002", "first_time": "100000", "last_time": "133000"},   # 盘中开过板 → 弱
                {"code": "600003", "first_time": "144500", "last_time": "144500"},   # 尾盘偷鸡板 → 弱
            ],
            "zb": [{"code": "600004"}],                                             # 炸板 → 弱
        }
        ctx = _service(_Repo(snap, pools=pools)).previous_context("2026-10-20")
        assert ctx["yesterday_codes"] == ["600001", "600002", "600003"]
        assert ctx["yesterday_weak"] == ["600002", "600003", "600004"]

    def test_weak_codes_prefers_open_num_when_upstream_gives_it(self):
        rows = [{"code": "600001", "first_time": "100000", "last_time": "100000", "open_num": 2},
                {"code": "600002", "first_time": "100000", "last_time": "100000", "open_num": 0}]
        assert live.weak_codes(rows, [], late="143000") == ["600001"]

    def test_older_review_is_never_used(self, monkeypatch):
        """库里只有更早的复盘时，不能拿来门控今天，要现算前一交易日。"""
        from src.services.daban import fetchers

        live.clear_cache()
        cursor = {"2026-10-20": "2026-10-19", "2026-10-19": "2026-10-16", "2026-10-16": "2026-10-15"}
        monkeypatch.setattr(fetchers, "previous_trade_date", lambda day, **kw: cursor[day])
        _pools(monkeypatch)
        old = SimpleNamespace(trade_date="2026-10-15", id=1, payload=json.dumps({"stage": {"key": "climax"}}))
        ctx = _service(_Repo(old)).previous_context("2026-10-20")
        assert ctx["source"] == "computed" and ctx["trade_date"] == "2026-10-19"
        assert ctx["yesterday_codes"] and ctx["recent_mains"]

    def test_upstream_failure_is_unavailable(self, monkeypatch):
        from src.services.daban import fetchers

        live.clear_cache()
        monkeypatch.setattr(fetchers, "previous_trade_date",
                            lambda day, **kw: "2026-10-19" if day == "2026-10-20" else "2026-10-16")

        def boom(*a, **k):
            raise RuntimeError("down")

        for name in ("fetch_limit_pool", "fetch_ladder", "fetch_em"):
            monkeypatch.setattr(fetchers, name, boom)
        ctx = _service(_Repo()).previous_context("2026-10-20")
        assert ctx["available"] is False and "2026-10-19" in ctx["reason"]


class TestRecordingAndPush:
    def test_first_sighting_is_recorded_once(self):
        repo = _Repo()
        svc = _service(repo)
        hits = _preboard_run([_pre("600001", "甲", 1.0)])["hits"]
        assert [h["code"] for h in svc._record("2026-10-20", hits)] == ["600001"]
        assert svc._record("2026-10-20", hits) == []
        row = repo.live["2026-10-20"][("kobe", "600001")]
        assert row["origin"] == "live" and row["limit_price"] and row["seen_at"]
        assert row["seen_price"] == 10.0 and row["slot"]

    def test_recorded_slots_survive_the_next_refresh(self):
        """第一轮锁定 3 只（高位震荡 30%）；第二轮数据里多了一只更早触板的，名单不变、不重复留档。"""
        repo = _Repo()
        svc = _service(repo)
        first = [_pre(f"6000{i}", f"股{i}", 1.0 + i / 10, first=f"10{i}000") for i in range(1, 4)]
        hits = _preboard_run(first, phase=PHASE_HIGH)["hits"]
        recorded = svc._record("2026-10-20", hits)
        assert sorted(h["code"] for h in recorded if h["style"] == "kobe") == ["60001", "60002", "60003"]
        second = first + [_pre("600099", "晚到", 0.2, first="095900")]
        result = live.build_hits(rows=[], broken=[], phase=PHASE_HIGH, rules=rules(),
                                 memory={r["code"]: {"机器人"} for r in second}, preboard=second,
                                 blocks=HOT_BLOCK, news_lookup=lambda names: {n: 9 for n in names},
                                 locked=svc.locked("2026-10-20"))
        assert _hit(result, "600099")["action"] == "watch"
        second_round = svc._record("2026-10-20", result["hits"])
        # 科比线名额已用满：不新增、不改动。
        assert not [h for h in second_round if h["style"] == "kobe"]
        assert sorted(code for style, code in repo.live["2026-10-20"] if style == "kobe") == ["60001", "60002", "60003"]
        # 已锁定的三个名额序号不变，晚到的票不占位。
        assert sorted(h["slot"] for h in result["hits"] if h["style"] == "kobe" and h["slot"]) == [1, 2, 3]

    def test_push_is_deduplicated(self, monkeypatch):
        import src.notification as notification

        sent = []

        class _Notifier:
            def send_with_results(self, content, **kw):
                sent.append(kw["dedup_key"])

        monkeypatch.setattr(notification, "NotificationService", lambda: _Notifier())
        monkeypatch.setattr(notification.NotificationBuilder, "build_simple_alert",
                            staticmethod(lambda **kw: kw["content"]))
        live.clear_cache()
        svc = _service(_Repo(), daban_live_push_enabled=True)
        hits = [h for h in _preboard_run([_pre("600001", "甲", 1.0)])["hits"] if h["action"] != "watch"]
        svc._notify("2026-10-20", hits, PHASE_HIGH)
        svc._notify("2026-10-20", hits, PHASE_HIGH)
        assert sent == ["daban-live:2026-10-20:kobe:600001"]

    def test_push_disabled_sends_nothing(self, monkeypatch):
        import src.notification as notification

        monkeypatch.setattr(notification, "NotificationService", lambda: (_ for _ in ()).throw(AssertionError))
        hits = [h for h in _run([_robot("600001", "甲", "100000")])["hits"] if h["action"] != "watch"]
        _service(_Repo())._notify("2026-10-20", hits, PHASE_HIGH)

    def test_alert_text_has_sell_rules(self):
        text = live.render_hit_alert(_hit(_run([_robot("600001", "甲", "100000")]), "600001"))
        assert "卖出纪律" in text and "K21" in text and "不构成投资建议" in text


class TestReview:
    def test_review_waits_for_next_close(self, monkeypatch):
        from src.services.daban import fetchers

        calls = []

        class Repo:
            def pending_live_dates(self):
                return ["2026-10-20"]

            def live_hits(self, day):
                return [{"code": "600001", "limit_price": 11.0, "reviewed": False, "first_time": "100000"}]

            def update_live_outcomes(self, day, outcomes):
                calls.append(outcomes)

        monkeypatch.setattr(fetchers, "next_trade_date", lambda day, **kw: "2026-10-21")
        monkeypatch.setattr(fetchers, "fetch_klines", lambda codes, day, *, limit, timeout: {
            "value": {"rows": {"600001": [{"time": "20261021", "open": 11.55, "close": 12.1}]}}})
        assert live.review_pending_live_hits(Repo(), today="2026-10-21", now="140000") == 0
        assert live.review_pending_live_hits(Repo(), today="2026-10-21", now="151000") == 1
        assert calls[0]["600001"] == {"open_ret": 5.0, "close_ret": 10.0}


class TestForecast:
    def test_active_themes_filter(self):
        themes = [
            {"key": "机器人", "name": "机器人", "is_main": True, "status": "rise", "days": 7, "zt_count": 10, "rank": 1},
            {"key": "老", "name": "老", "is_main": True, "status": "diverge", "days": 9, "zt_count": 6, "rank": 2},
            {"key": "久", "name": "久", "is_main": True, "status": "rise", "days": 30, "zt_count": 6, "rank": 3},
        ]
        assert [t["key"] for t in active_themes(themes, rules())] == ["机器人"]

    def test_sources(self):
        themes = [{"key": "机器人", "name": "机器人", "is_main": True, "status": "rise", "days": 5,
                   "zt_count": 6, "rank": 1, "tags": ["机器人"]}]
        picks = build_forecasts(themes=themes, zt=[{"code": "600001"}],
                                broken=[{"code": "600002", "name": "炸", "reason": "人形机器人"}],
                                reason_memory={"600003": {"人形机器人"}}, limit_memory={"600003": 3}, rules=rules())
        assert {(p["code"], p["source"]) for p in picks} == {("600002", "broken"), ("600003", "memory")}

    def test_memory_source_needs_two_limit_ups(self):
        themes = [{"key": "机器人", "name": "机器人", "is_main": True, "status": "rise", "days": 5,
                   "zt_count": 6, "rank": 1, "tags": ["机器人"]}]
        assert build_forecasts(themes=themes, zt=[], broken=[], reason_memory={"600003": {"人形机器人"}},
                               limit_memory={"600003": 1}, rules=rules()) == []

    def test_summary(self):
        rows = [{"source": "broken", "touched": True, "sealed": True, "target_date": "d1", "evaluated": True},
                {"source": "broken", "touched": False, "sealed": False, "target_date": "d2", "evaluated": True}]
        assert summarize_forecasts(rows)["groups"]["broken"] == {"n": 2, "touch_rate": 0.5, "seal_rate": 0.5}


def _boom():
    raise RuntimeError("Read timed out")


class TestChangeFeedDegradation:
    """拉升异动：东财 push2ex 在 09:30 后几分钟会读超时 / 断连。一路失败不清空候选，也不拖垮另一路。"""

    EVENTS = [{"code": "600001", "name": "甲", "type": "RISE", "date": "", "time": "093500",
               "order_amount": None, "change": 6.0, "price": 10.5}]

    def _svc(self, monkeypatch, outcomes):
        from data_provider import limit_up_fetcher as lu

        live.clear_cache()
        calls = {"n": 0}

        def fetch(kind, *, timeout):
            calls["n"] += 1
            outcome = outcomes[min(calls["n"], len(outcomes)) - 1]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        monkeypatch.setattr(lu, "fetch_em_changes", fetch)
        return _service(_Repo())

    def test_failure_reuses_the_last_good_feed(self, monkeypatch):
        svc = self._svc(monkeypatch, [self.EVENTS, RuntimeError("Read timed out")])
        assert svc._changes(("rise",), timeout=4, reuse_sec=90) == ({"rise": self.EVENTS}, [])
        feeds, notes = svc._changes(("rise",), timeout=4, reuse_sec=90)
        assert feeds["rise"] == self.EVENTS
        assert len(notes) == 1 and "沿用" in notes[0] and "Read timed out" in notes[0]

    def test_cache_is_not_reused_past_the_window(self, monkeypatch):
        svc = self._svc(monkeypatch, [self.EVENTS, RuntimeError("Read timed out")])
        svc._changes(("rise",), timeout=4, reuse_sec=90)
        feeds, notes = svc._changes(("rise",), timeout=4, reuse_sec=0)
        assert feeds["rise"] == [] and "不可用" in notes[0]

    def test_failure_without_cache_is_empty_but_does_not_raise(self, monkeypatch):
        svc = self._svc(monkeypatch, [RuntimeError("RemoteDisconnected")])
        feeds, notes = svc._changes(("rise",), timeout=4, reuse_sec=90)
        assert feeds["rise"] == [] and "这一路的未涨停候选为空" in notes[0]

    def test_one_kind_failing_does_not_take_down_the_other(self, monkeypatch):
        from data_provider import limit_up_fetcher as lu

        live.clear_cache()
        monkeypatch.setattr(lu, "fetch_em_changes",
                            lambda kind, *, timeout: self.EVENTS if kind == "rise" else _boom())
        feeds, notes = _service(_Repo())._changes(("rise", "rebound"), timeout=4, reuse_sec=90)
        assert feeds["rise"] == self.EVENTS and feeds["rebound"] == []
        assert len(notes) == 1 and "rebound" in notes[0]


PHASE_RETREAT_BASE = {"available": True, "trade_date": "2026-10-22", "chen_stage": "retreat",
                      "chen_label": "退潮期", "kobe_gate": "fall_1", "kobe_label": "主跌第 1 天",
                      "stage": {"key": "retreat"}, "metrics": {"zt_count": 33, "dt_count": 49}}


def _rt_metrics(**kw):
    base = {"zt_count": 55, "zb_count": 12, "dt_count": 6, "yzt_premium": 3.38, "zab_rate": 0.18,
            "lianban_count": 12, "max_height": 6, "promotion_rate": 0.36, "follow_rate": 0.2}
    base.update(kw)
    return base


def _decide(metrics, *, now="133000", accel=None, state=None, today=None, phase=None):
    today = today or {"stage": {"key": "ferment", "label": "发酵期"},
                      "kobe_stage": {"gate_key": "main_rise", "label": "主升"}}
    return live.decide_realtime_gate(
        rules=rules(), phase=phase or PHASE_RETREAT_BASE, metrics=metrics, today=today,
        now=now, accel_theme=accel, state=state if state is not None else {},
    )


class TestRealtimeGate:
    """盘中实时门控：前一交易日只做底线，今天的 tape 给出市场验证后按今天的周期行事。"""

    def test_fall_day_advances_and_late_swap(self):
        # 科比原文「第二天的杀跌……尾盘就可以找买点」说的是当天；昨天是第 1 天，今天就按第 2 天门控。
        assert live._advance_fall_day("fall_1") == "fall_2"
        assert live._advance_fall_day("fall_3") == "fall_3" and live._advance_fall_day("main_rise") == "main_rise"
        cfg = rules()
        assert live._late_swap(cfg, "fall_2", "093500") == "fall_2_early"   # 尾盘之前只试错
        assert live._late_swap(cfg, "fall_2", "140001") == "fall_2"         # 尾盘才给可执行
        assert live._gate(cfg, "kobe", "fall_2_early") == "trial"
        assert live._gate(cfg, "kobe", "fall_2") == "actionable"

    def test_small_sample_keeps_the_previous_day_gate(self):
        out = _decide(_rt_metrics(zt_count=12), now="093500")
        assert out["active"] is False and out["upgraded"] is False
        assert out["keys"]["chen"] == "retreat" and out["keys"]["kobe"] == "fall_2_early"
        assert "样本太小" in out["reason"]

    def test_upgrade_needs_two_consecutive_rounds(self):
        state = {}
        first = _decide(_rt_metrics(), state=state)
        assert first["active"] is True and first["upgraded"] is False and "还需连续 2 轮" in first["reason"]
        second = _decide(_rt_metrics(), state=state)
        assert second["upgraded"] is True and second["keys"]["chen"] == "ferment"
        assert "赚钱效应已被市场验证" in second["reason"]

    def test_upgrade_only_moves_toward_more_open(self):
        """今天比昨天更差时不降级——降级只走「资金撤了」那条路，且只停发新名额。"""
        state = {}
        today = {"stage": {"key": "ice", "label": "冰点期"}, "kobe_stage": {"gate_key": "fall_1", "label": "主跌第 1 天"}}
        phase = {**PHASE_RETREAT_BASE, "chen_stage": "warm", "chen_label": "回暖期"}
        for _ in range(2):
            out = _decide(_rt_metrics(), state=state, today=today, phase=phase)
        assert out["keys"]["chen"] == "warm" and out["upgraded"] is False

    def test_freeze_needs_negative_premium_plus_a_weak_signal(self):
        state = {"zt_peak": 60}
        for _ in range(2):
            out = _decide(_rt_metrics(zt_count=30, yzt_premium=-2.0), state=state)
        assert out["frozen"] is True and "停发新名额" in out["reason"]

    def test_rotation_exempts_the_freeze(self):
        """大盘走弱但有热点题材在加速：资金在换题材不是在撤，不能停发名额。"""
        state = {"zt_peak": 60}
        for _ in range(2):
            out = _decide(_rt_metrics(zt_count=30, yzt_premium=-2.0), state=state, accel="机器人概念")
        assert out["frozen"] is False and "资金在换题材" in out["reason"]

    def test_negative_premium_alone_does_not_freeze(self):
        state = {}
        for _ in range(2):
            out = _decide(_rt_metrics(yzt_premium=-0.5, zab_rate=0.1, zt_count=55), state=state)
        assert out["frozen"] is False

    def test_theme_accelerating_counts_recent_seals_by_reason(self):
        hot = [{"name": "机器人概念", "zt_count": 9}, {"name": "储能", "zt_count": 4}]
        rows = [zt_row("600001", "甲", reason="人形机器人", first_time="132000"),
                zt_row("600002", "乙", reason="机器人概念", first_time="132500"),
                zt_row("600003", "丙", reason="储能", first_time="100000")]
        assert live._theme_accelerating(rows, hot, now="133000", window_min=15, min_new=2) == "机器人概念"
        assert live._theme_accelerating(rows, hot, now="133000", window_min=15, min_new=3) is None
        assert live._theme_accelerating(rows, hot, now="150000", window_min=15, min_new=2) is None

    def test_freeze_stops_new_slots_but_keeps_locked_ones(self):
        rows = [_pre("600001", "甲", 1.0), _pre("600002", "乙", 2.0)]
        locked = _locked(("kobe", "600001", "trial", 10, 1, "095000"))
        result = live.build_hits(
            rows=[], broken=[], phase=PHASE_BEST, rules=rules(), memory={c["code"]: {"机器人"} for c in rows},
            preboard=rows, blocks=HOT_BLOCK, news_lookup=lambda names: {n: 9 for n in names},
            locked=locked, freeze=True,
        )
        kobe = [h for h in result["hits"] if h["style"] == "kobe"]
        assert [h["code"] for h in kobe if h["slot"]] == ["600001"]           # 已锁定的原样保留
        new = next(h for h in kobe if h["code"] == "600002")
        assert new["slot"] is None and new["action"] == "watch"
        assert any("已停发新名额" in n for n in new["notes"])


@pytest.mark.parametrize("now,day,expected", [
    ("093000", "2026-10-20", True), ("145959", "2026-10-20", True),
    ("091000", "2026-10-20", False), ("150100", "2026-10-20", False), ("100000", "2026-10-18", False),
])
def test_market_open_window(now, day, expected):
    assert live.market_open_now(now, day=day) is expected


class TestThemeFallback:
    """题材来源：涨停原因 → 历史涨停原因 → 当日题材榜板块 → 所属概念；四级都空就舍弃，不留师出无名的票。"""

    def test_reason_wins(self):
        rows = [_robot("600001", "甲", "100000"), _robot("600002", "乙", "100500")]
        hit = _hit(_run(rows), "600001")
        assert hit["theme_source"] == "reason" and hit["theme"] and hit["theme_rank"]

    def test_history_when_reason_missing(self):
        # 集合竞价期间涨停池不给原因：用历史涨停原因判断题材。
        row = zt_row("600001", "甲", reason="", first_time="100000")
        hit = _hit(_run([row], memory={"600001": {"人形机器人"}}), "600001")
        assert hit["theme_source"] == "history" and "人形机器人" in hit["theme_hint"]

    def test_block_when_no_reason_and_no_history(self):
        row = zt_row("600001", "甲", reason="", first_time="100000")
        blocks = [{"name": "机器人概念", "limit_up_num": 18, "members": [{"code": "600001"}]}]
        result = live.build_hits(rows=[row], broken=[], phase=PHASE_HIGH, rules=rules(), blocks=blocks)
        hit = _hit(result, "600001")
        assert hit["theme_source"] == "block" and hit["theme_hint"] == "机器人概念"

    def test_nothing_known_is_marked_not_claimed(self):
        row = zt_row("600001", "甲", reason="", first_time="100000")
        hit = _hit(_run([row]), "600001")
        assert hit["theme_source"] == "" and hit["theme"] == "" and hit["theme_hint"] == ""


class TestNamelessIsDropped:
    """同花顺炸板池不给涨停原因，这些票靠所属概念兜底；连概念也挂不上今日题材的，两派都不碰。"""

    ZT = [_robot("600001", "甲", "100000"), _robot("600002", "乙", "100500"), _robot("600003", "丙", "101000")]

    def _broken(self, code="600009"):
        return zt_row(code, "炸", reason="", first_time="101500", open_num=1)

    def _run(self, concepts, *, raises=False):
        def lookup(codes):
            if raises:
                raise RuntimeError("东财概念接口 500")
            return {c: concepts for c in codes}

        return live.build_hits(rows=list(self.ZT), broken=[self._broken()], phase=PHASE_BEST, rules=rules(),
                               concepts_lookup=lookup)

    def test_concept_matching_a_live_theme_fills_the_theme(self):
        # 概念里正好有一个今天在涨停的题材：师出有名，进名单并标注来源是「所属概念」。
        hit = _hit(self._run({"concepts": ["机器人概念", "融资融券"], "industry": "通用设备"}), "600009")
        assert hit["theme_source"] == "concept" and hit["theme"]
        assert all("师出无名" not in b["reason"] for b in self._run(
            {"concepts": ["机器人概念"], "industry": ""})["blocked"])

    def test_concept_picks_the_hottest_matching_theme(self):
        # 一只票挂几十个概念很常见，只认与当日题材相交的那部分，并取其中最热的一个。
        rows = [*self.ZT, zt_row("600004", "丁", reason="一带一路", first_time="101000")]
        result = live.build_hits(rows=rows, broken=[self._broken()], phase=PHASE_BEST, rules=rules(),
                                 concepts_lookup=lambda codes: {
                                     c: {"concepts": ["一带一路", "机器人概念", "参股银行"], "industry": ""}
                                     for c in codes})
        hit = _hit(result, "600009")
        assert hit["theme_source"] == "concept"
        assert hit["theme"] == _hit(result, "600001")["theme"] != _hit(result, "600004")["theme"]

    def test_nameless_row_is_dropped_from_both_styles(self):
        result = self._run({"concepts": ["参股银行", "滨海新区"], "industry": "基础建设"})
        assert not [h for h in result["hits"] if h["code"] == "600009"]
        reasons = [b["reason"] for b in result["blocked"] if b["code"] == "600009"]
        assert len(reasons) == 2 and all("师出无名" in r for r in reasons)

    def test_concept_lookup_failure_keeps_the_row(self):
        # 兜底接口挂了不能把名单清空：这一级按「没查过」处理，照旧显示「题材待公布」。
        hit = _hit(self._run({}, raises=True), "600009")
        assert hit["theme_source"] == "" and hit["theme_hint"] == ""
        assert not [b for b in self._run({}, raises=True)["blocked"] if b["code"] == "600009"]


class TestAuctionIsNotLockable:
    def test_missing_first_time_cannot_take_a_slot(self):
        row = zt_row("600001", "甲", reason="", first_time="")
        result = _run([row], phase=PHASE_BEST)
        hit = _hit(result, "600001")
        assert hit["lockable"] is False and hit["action"] == "watch"
        assert any("集合竞价" in n for n in hit["notes"])
        assert result["lanes"]["kobe"]["picks"] == 0

    def test_record_skips_unlockable(self):
        """已封板的票（买不进去）不会被留成名额；只有未涨停候选会被记录。"""
        repo = _Repo()
        svc = _service(repo)
        sealed = _run([_robot("600002", "乙", "100000")], phase=PHASE_BEST)["hits"]
        assert svc._record("2026-10-21", sealed) == []
        pre = _preboard_run([_pre("600001", "甲", 1.0)])["hits"]
        assert [h["code"] for h in svc._record("2026-10-21", pre) if h["style"] == "kobe"] == ["600001"]


class TestPreboard:
    """未涨停候选：目的是让人买得进，而不是事后报告已经封板的票。"""

    PHASE = PHASE_BEST

    THEMES = [{"key": "机器人", "name": "机器人", "zt_count": 5, "rank": 1, "is_main": True, "novelty": "new",
               "tags": ["机器人"]}]
    BLOCKS = [{"name": "机器人概念", "limit_up_num": 5, "members": [{"code": "600100"}]}]

    def _row(self, code, price, limit_up, change, **kw):
        return {"code": code, "name": kw.pop("name", f"股{code}"), "price": price, "limit_up": limit_up,
                "change_pct": change, "circ_mv": 5e9, "volume_ratio": 5.0, "turnover_rate": 8.0,
                "time": "100000", **kw}

    def _build(self, rows, *, memory=None, yesterday=(), phase=PHASE_BEST):
        return live.build_hits(
            rows=[], broken=[], phase=phase, rules=rules(), memory=memory or {"600001": {"机器人"}},
            yesterday_codes=list(yesterday), themes_override=None,
        ) if False else live._preboard_hits(
            phase=phase, rules=rules(), themes=self.THEMES, themes_by_key={}, sealed_codes=set(),
            broken_codes=set(), yesterday=set(yesterday), memory=memory or {"600001": {"机器人"}},
            max_picks=4, preboard_rows=rows, blocks=self.BLOCKS,
        )

    def test_hot_threshold_and_warming(self):
        """热点要够热（≥3 只或占全市场 5%）且在升温（连续上榜 ≥2 天或比昨天多）。"""
        row = self._row("600001", 10.6, 11.0, 6.0)
        # 只有 2 只涨停、且只上榜 1 天、也没比昨天多 → 不算热点
        weak = [{"name": "机器人概念", "limit_up_num": 2, "days": 1, "members": []}]
        assert live._preboard_hits(phase=self.PHASE, rules=rules(), themes=[], themes_by_key={},
                                   sealed_codes={"600100", "600101", "600102", "600103", "600104",
                                                 "600105", "600106", "600107", "600108", "600109"},
                                   broken_codes=set(), yesterday=set(), memory={"600001": {"机器人"}},
                                   max_picks=4, preboard_rows=[row], blocks=weak) == []
        # 连续上榜 2 天 → 升温，但 2 只 / 全市场 10 只 = 20% ≥ 5% → 够热
        warm = [{"name": "机器人概念", "limit_up_num": 2, "days": 2, "members": []}]
        assert live._preboard_hits(phase=self.PHASE, rules=rules(), themes=[], themes_by_key={},
                                   sealed_codes={"600100", "600101", "600102", "600103", "600104",
                                                 "600105", "600106", "600107", "600108", "600109"},
                                   broken_codes=set(), yesterday=set(), memory={"600001": {"机器人"}},
                                   max_picks=4, preboard_rows=[row], blocks=warm)
        # 昨日涨停数更少 → 今天变多，也算升温
        assert live._preboard_hits(phase=self.PHASE, rules=rules(), themes=[], themes_by_key={},
                                   sealed_codes={"600100", "600101", "600102", "600103", "600104",
                                                 "600105", "600106", "600107", "600108", "600109"},
                                   broken_codes=set(), yesterday=set(), memory={"600001": {"机器人"}},
                                   max_picks=4, preboard_rows=[row], blocks=weak,
                                   prev_blocks={"机器人": 0})

    def test_news_is_a_gate(self):
        """题材近 3 日新闻不够，就不算「正在发酵」。"""
        row = self._row("600001", 10.6, 11.0, 6.0)
        blocks = [{"name": "机器人概念", "limit_up_num": 5, "days": 3, "members": []}]

        def news_lookup(names):
            return {name: 1 for name in names}          # 只有 1 条新闻 < 门槛 3

        assert live._preboard_hits(phase=self.PHASE, rules=rules(), themes=[], themes_by_key={},
                                   sealed_codes=set(), broken_codes=set(), yesterday=set(),
                                   memory={"600001": {"机器人"}}, max_picks=4,
                                   preboard_rows=[row], blocks=blocks, news_lookup=news_lookup) == []

        def enough(names):
            return {name: 5 for name in names}

        hits = live._preboard_hits(phase=self.PHASE, rules=rules(), themes=[], themes_by_key={},
                                   sealed_codes=set(), broken_codes=set(), yesterday=set(),
                                   memory={"600001": {"机器人"}}, max_picks=4,
                                   preboard_rows=[row], blocks=blocks, news_lookup=enough)
        assert hits and hits[0]["theme_news"] == 5

    def test_suffix_normalization_matches_concept_blocks(self):
        """「人形机器人」要能对上「机器人概念」——先去掉「概念/板块/产业链」后缀再比。"""
        assert live._related({"人形机器人"}, {"机器人概念"})
        assert live._related({"军工电子"}, {"军工板块"})
        assert not live._related({"白酒"}, {"半导体"})

    def test_concept_fallback_for_stocks_without_history(self):
        row = self._row("600001", 10.6, 11.0, 6.0)
        blocks = [{"name": "机器人概念", "limit_up_num": 5, "days": 3, "members": []}]

        def concepts(codes):
            return {c: {"industry": "汽车", "concepts": ["机器人概念", "人形机器人"]} for c in codes}

        hits = live._preboard_hits(phase=self.PHASE, rules=rules(), themes=[], themes_by_key={},
                                   sealed_codes=set(), broken_codes=set(), yesterday=set(),
                                   memory={}, max_picks=4, preboard_rows=[row], blocks=blocks,
                                   concepts_lookup=concepts)
        assert hits and hits[0]["theme_source"] == "concept"

    def test_near_limit_rising_stock_in_hot_theme_is_suggested(self):
        hits = self._build([self._row("600001", 10.0, 11.0, 4.0)])   # 距涨停 10% → 太远
        assert hits == []
        hits = self._build([self._row("600001", 10.6, 11.0, 6.0)])   # 距涨停 3.8%
        # 题材是新题材且板块内 5 只涨停：两派都符合（陈小群线回暖期 + 新题材 + 资金合力）
        assert sorted(h["style"] for h in hits) == ["chen", "kobe"]
        hit = hits[0]
        assert hit["status"] == "preboard" and hit["gap_pct"] == 3.77
        assert hit["action"] in ("trial", "actionable")
        assert hit["theme"] and hit["sealed_in_theme"] >= 2

    def test_already_sealed_or_broken_are_excluded(self):
        row = self._row("600001", 10.6, 11.0, 6.0)
        for kwargs in ({"sealed_codes": {"600001"}, "broken_codes": set()},
                       {"sealed_codes": set(), "broken_codes": {"600001"}}):
            assert live._preboard_hits(phase=PHASE_BEST, rules=rules(), themes=self.THEMES, themes_by_key={},
                                       yesterday=set(), memory={"600001": {"机器人"}}, max_picks=4,
                                       preboard_rows=[row], blocks=self.BLOCKS, **kwargs) == []

    def test_yesterday_limit_up_is_only_for_chen_weak_to_strong(self):
        """昨日涨停的票不再算科比的「首板」，但陈小群的弱转强会用它们（昨日烂板今日被买上去）。"""
        row = self._row("600001", 10.6, 11.0, 6.0)
        hits = live._preboard_hits(phase=PHASE_HIGH, rules=rules(), themes=self.THEMES, themes_by_key={},
                                   sealed_codes=set(), broken_codes=set(), yesterday={"600001"},
                                   memory={"600001": {"机器人"}}, max_picks=4,
                                   preboard_rows=[row], blocks=self.BLOCKS,
                                   yesterday_weak={"600001"})
        assert [h["style"] for h in hits] == ["chen"]
        assert hits[0]["mode"] == live.MODE_WEAK_TO_STRONG

    def test_without_hot_theme_theme_it_is_not_suggested(self):
        hits = self._build([self._row("600009", 10.6, 11.0, 6.0, name="无题材股")], memory={})
        assert hits == []

    def test_far_from_limit_or_weak_stock_is_dropped(self):
        assert self._build([self._row("600001", 9.0, 11.0, 2.0)]) == []      # 涨幅不够
        assert self._build([self._row("600001", 10.9, 11.0, 5.0)]) is not None  # 很近，保留

    def test_chen_lane_needs_new_theme_and_peers(self):
        old = [{"key": "机器人", "name": "机器人", "zt_count": 5, "rank": 1, "is_main": True,
                "novelty": "same", "tags": ["机器人"]}]
        hits = live._preboard_hits(phase=PHASE_HIGH, rules=rules(), themes=old, themes_by_key={},
                                   sealed_codes=set(), broken_codes=set(), yesterday=set(),
                                   memory={"600001": {"机器人"}}, max_picks=4,
                                   preboard_rows=[self._row("600001", 10.6, 11.0, 6.0)], blocks=self.BLOCKS)
        assert [h["style"] for h in hits] == ["kobe"]

    def test_phase_gate_blocks_preboard_too(self):
        assert self._build([self._row("600001", 10.6, 11.0, 6.0)], phase=PHASE_RETREAT) == []
