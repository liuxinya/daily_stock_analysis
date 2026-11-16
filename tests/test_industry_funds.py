# -*- coding: utf-8 -*-
"""行业资金模块的单元测试。

绝大部分用例是**离线**的：用构造的 ``fetched`` 字典驱动打分层，不起网络。这既是速度
考虑，也是唯一能稳定复现"某个数据源挂了"这类分支的办法。

网络冒烟测试单独放在文件末尾，需要显式设 ``INDUSTRY_FUNDS_NETWORK_TESTS=1`` 才跑。
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from src.services.industry_funds import scoring
from src.services.industry_funds import service as service_mod
from src.services.industry_funds.service import IndustryFundsService
from src.services.industry_funds.ths_client import (
    _parse_amount,
    _parse_pct,
    normalize_stock_code,
)

YI = 1e8


# ---------------------------------------------------------------------------
# 解析工具
# ---------------------------------------------------------------------------


class TestThsParsing:
    """同花顺落地页的解析工具。

    这几个函数处理的是真实上游的脏数据形态，测试用例全部来自实测样本。
    """

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("3.16亿", 3.16 * YI),
            ("-2.05亿", -2.05 * YI),
            ("-1228.83万", -1228.83 * 1e4),
            ("1.69亿", 1.69 * YI),
            ("", None),
            ("-", None),
            (None, None),
            (3.5, 3.5),
        ],
    )
    def test_parse_amount(self, raw, expected):
        """个股表的资金列是带「亿/万」后缀的字符串，行业表却是浮点数。"""
        result = _parse_amount(raw)
        if expected is None:
            assert result is None
        else:
            assert result == pytest.approx(expected)

    @pytest.mark.parametrize(
        "raw,expected",
        [("20.04%", 20.04), ("-3.83%", -3.83), ("", None), ("--", None), (None, None)],
    )
    def test_parse_pct(self, raw, expected):
        assert _parse_pct(raw) == expected

    @pytest.mark.parametrize(
        "raw,expected",
        [
            (300045, "300045"),
            ("300045", "300045"),
            # 同花顺返回整型，深市 000001 会变成 1 —— 不补零会错配到别的股票。
            (1, "000001"),
            ("1", "000001"),
            ("000001", "000001"),
            (600519.0, "600519"),
            ("", ""),
            (None, ""),
        ],
    )
    def test_normalize_stock_code(self, raw, expected):
        assert normalize_stock_code(raw) == expected


# ---------------------------------------------------------------------------
# 打分：构造数据
# ---------------------------------------------------------------------------


def _fetched(
    *,
    inflow_ratio: float = 0.5,
    lhb_percentile: float = 0.5,
    etf_delta: float = 0.0,
    margin_percentile: float = 0.5,
    valuation_percentile: float = 0.5,
    omit: tuple = (),
    degrade: tuple = (),
):
    """构造一份 ``fetched``，只填打分用得到的字段。``omit`` 里的源按"抓取失败"处理。"""
    data = {
        "industry_fund_flow": {
            "ok": True,
            "source": "ths",
            "as_of": "2026-10-16",
            "degraded": False,
            "degraded_reason": "",
            "error": None,
            "value": {
                "inflow_ratio": inflow_ratio,
                "inflow_count": int(inflow_ratio * 100),
                "industry_count": 100,
                "net_sum": 1.0,
                "net_median": 0.5,
            },
        },
        "lhb_institution": {
            "ok": True,
            "source": "em",
            "as_of": "2026-10-16",
            "degraded": False,
            "degraded_reason": "",
            "error": None,
            "value": {
                "percentile": lhb_percentile,
                "rolling_5d_net": 1e9,
                "latest_net": 3e8,
                "history_days": 120,
                "stocks": [],
            },
        },
        "etf_share": {
            "ok": True,
            "source": "sse",
            "as_of": "2026-10-16",
            "degraded": False,
            "degraded_reason": "",
            "error": None,
            "value": {
                "combined_delta": etf_delta,
                "coverage": "沪市",
                "baseline_date": "2026-10-09",
                "sse": {"delta": etf_delta},
                "szse": {"delta": None},
                "delta_top": [],
            },
        },
        "margin": {
            "ok": True,
            "source": "margin",
            "as_of": "2026-10-16",
            "degraded": False,
            "degraded_reason": "",
            "error": None,
            "value": {
                "percentile": margin_percentile,
                "change_5d": 0.01,
                "financing_balance": 24500.0,
                "history_points": 480,
            },
        },
        "valuation": {
            "ok": True,
            "source": "legu",
            "as_of": "2026-10-16",
            "degraded": False,
            "degraded_reason": "",
            "error": None,
            "value": {
                "combined_percentile": valuation_percentile,
                "hs300_pe": 12.3,
                "hs300_pe_percentile": valuation_percentile,
                "all_a_pb": 2.5,
                "all_a_pb_percentile": valuation_percentile,
            },
        },
        "stock_candidates": {
            "ok": True,
            "source": "em",
            "as_of": "2026-10-16",
            "degraded": False,
            "degraded_reason": "",
            "error": None,
            "value": {"lhb_stocks": [], "flow_stocks": []},
        },
    }
    for key in omit:
        entry = data[key]
        entry.update(
            {"ok": False, "error": "测试构造的失败", "value": {}, "degraded": True,
             "degraded_reason": "测试构造的失败"}
        )
    for key in degrade:
        data[key]["degraded"] = True
        data[key]["degraded_reason"] = "测试构造的口径不完整"
    return data


class TestMarketScoring:
    def test_all_factors_available_uses_full_weight(self):
        result = scoring.score_market(_fetched())
        assert result["available_weight"] == pytest.approx(1.0)
        assert result["renormalized"] is False
        assert len([f for f in result["factors"] if f["available"]]) == 5
        # 每个因子的权重之和应当是 1
        assert sum(f["weight"] for f in result["factors"]) == pytest.approx(1.0)

    def test_neutral_inputs_land_near_midpoint(self):
        """所有因子都取中位输入时，复合分应当落在中性区间。"""
        result = scoring.score_market(_fetched())
        assert result["verdict"] == "neutral"
        assert 45 <= result["score"] <= 65

    def test_verdict_boundaries_match_doc(self):
        """判定口径：≥65 偏多 / 45–65 中性 / <45 偏空。两个阈值点都落在中性侧（含），
        与 docs/industry-funds.md 一致。"""
        assert scoring._verdict(65.0)[0] == "bullish"
        assert scoring._verdict(64.9)[0] == "neutral"
        assert scoring._verdict(45.0)[0] == "neutral"
        assert scoring._verdict(44.9)[0] == "bearish"

    def test_bullish_inputs_score_high(self):
        result = scoring.score_market(
            _fetched(
                inflow_ratio=1.0,
                lhb_percentile=1.0,
                etf_delta=300 * YI,
                margin_percentile=1.0,
                valuation_percentile=0.0,  # 估值分位低 = 便宜 = 偏多
            )
        )
        assert result["score"] == pytest.approx(100.0, abs=0.5)
        assert result["verdict"] == "bullish"

    def test_bearish_inputs_score_low(self):
        result = scoring.score_market(
            _fetched(
                inflow_ratio=0.0,
                lhb_percentile=0.0,
                etf_delta=-300 * YI,
                margin_percentile=0.0,
                valuation_percentile=1.0,  # 估值分位高 = 贵 = 偏空
            )
        )
        assert result["score"] == pytest.approx(0.0, abs=0.5)
        assert result["verdict"] == "bearish"

    def test_valuation_is_reversed(self):
        """估值是唯一的反向因子：分位越低得分越高。"""
        cheap = scoring.score_market(_fetched(valuation_percentile=0.1))
        expensive = scoring.score_market(_fetched(valuation_percentile=0.9))
        cheap_factor = next(f for f in cheap["factors"] if f["key"] == "valuation")
        expensive_factor = next(f for f in expensive["factors"] if f["key"] == "valuation")
        assert cheap_factor["score"] > expensive_factor["score"]

    def test_missing_factor_renormalizes_instead_of_scoring_zero(self):
        """某个源挂掉时按剩余因子重新归一，不能把它当 0 分算 —— 那会让一次抓取失败
        直接变成一条看空结论。"""
        full = scoring.score_market(_fetched())
        missing = scoring.score_market(_fetched(omit=("valuation",)))

        assert missing["renormalized"] is True
        assert missing["available_weight"] == pytest.approx(0.9)
        assert missing["score"] == pytest.approx(full["score"], abs=1.0)
        assert missing["degraded"] is True
        assert any("估值分位" in w for w in missing["warnings"])

        valuation = next(f for f in missing["factors"] if f["key"] == "valuation")
        assert valuation["available"] is False
        assert valuation["score"] is None
        assert valuation["effective_weight"] == 0.0

    def test_all_sources_failed_yields_no_score(self):
        """全部失败时不能瞎给分，要如实返回"无法判断"。"""
        result = scoring.score_market(
            _fetched(omit=("industry_fund_flow", "lhb_institution", "etf_share", "margin", "valuation"))
        )
        assert result["score"] is None
        assert result["verdict"] == "unknown"
        assert result["degraded"] is True

    def test_etf_delta_uses_fallback_scale_without_history(self):
        """本地还没攒够 ETF 份额历史时走固定标尺，且把这件事说清楚。"""
        result = scoring.score_market(_fetched(etf_delta=75 * YI), etf_delta_history=[])
        factor = next(f for f in result["factors"] if f["key"] == "etf_share")
        assert factor["detail"]["method"] == "fallback_scale"
        assert "满量程" in factor["basis"]
        # 正流入应当高于中性 10 分
        assert factor["score"] > 10

    def test_etf_delta_switches_to_percentile_with_history(self):
        """攒够样本后自动切到历史分位，不让人造标尺长期留在结果里。"""
        history = [float(i) for i in range(25)]
        result = scoring.score_market(_fetched(etf_delta=24 * YI), etf_delta_history=history)
        factor = next(f for f in result["factors"] if f["key"] == "etf_share")
        assert factor["detail"]["method"] == "percentile"
        assert factor["score"] > 18  # 落在历史最高附近

    def test_every_factor_exposes_its_provenance(self):
        """「完全可解释」的落点：每个因子都要给出原始值、依据、来源、日期。"""
        result = scoring.score_market(_fetched())
        for factor in result["factors"]:
            assert factor["label"]
            assert factor["raw_display"] and factor["raw_display"] != "—"
            assert factor["basis"]
            assert factor["source"]
            assert factor["as_of"]
            assert factor["normalization"]
            assert factor["max_score"] == 20.0


# ---------------------------------------------------------------------------
# 行业分
# ---------------------------------------------------------------------------


def _industry(name, net, net_5d=None, change_pct=0.0, leader="某某", leader_pct=0.0):
    return {
        "name": name,
        "net": net,
        "net_5d": net_5d,
        "change_pct": change_pct,
        "leader": leader,
        "leader_change_pct": leader_pct,
        "company_count": 50,
        "index": 1000.0,
        "inflow": 10.0,
        "outflow": 9.0,
        "leader_price": 10.0,
    }


def _industry_fetched(industries):
    return {
        "industry_fund_flow": {
            "ok": True,
            "source": "ths",
            "as_of": "2026-10-16",
            "degraded": False,
            "degraded_reason": "",
            "error": None,
            "value": {"industries": industries},
        }
    }


class TestIndustryScoring:
    def test_ranks_by_composite_not_by_single_field(self):
        rows = [
            _industry("甲", net=10.0, net_5d=-5.0, change_pct=1.0),
            _industry("乙", net=5.0, net_5d=8.0, change_pct=5.0),
            _industry("丙", net=-8.0, net_5d=-9.0, change_pct=-3.0),
        ]
        result = scoring.score_industries(_industry_fetched(rows))
        names = [r["name"] for r in result["industries"]]
        assert names[0] == "乙"  # 5 日与涨跌幅都强
        assert names[-1] == "丙"
        assert [r["rank"] for r in result["industries"]] == [1, 2, 3]

    def test_missing_5d_redistributes_its_weight(self):
        rows = [_industry("甲", net=10.0), _industry("乙", net=-10.0)]
        result = scoring.score_industries(_industry_fetched(rows))
        assert "net_5d" not in result["weights"]
        assert sum(result["weights"].values()) == pytest.approx(1.0)
        assert result["degraded"] is True
        assert any("5 日" in w for w in result["warnings"])

    def test_empty_input_degrades_instead_of_raising(self):
        result = scoring.score_industries({"industry_fund_flow": {"value": {}, "ok": False}})
        assert result["industries"] == []
        assert result["degraded"] is True

    def test_each_industry_carries_component_breakdown(self):
        rows = [_industry("甲", net=10.0, net_5d=5.0, change_pct=2.0, leader_pct=3.0)]
        result = scoring.score_industries(_industry_fetched(rows))
        components = result["industries"][0]["components"]
        assert {c["key"] for c in components} == {"net_today", "net_5d", "change_pct", "breadth"}
        for component in components:
            assert component["normalization"]


# ---------------------------------------------------------------------------
# 候选池
# ---------------------------------------------------------------------------


class TestStockCandidates:
    def _fetched(self, *, lhb_stocks, flow_stocks, industries, stat_stocks=None):
        return {
            "lhb_institution": {
                "ok": True,
                "source": "em",
                "as_of": "2026-10-16",
                "value": {"stocks": lhb_stocks},
            },
            "stock_candidates": {
                "ok": True,
                "source": "em",
                "degraded_reason": "",
                "value": {"lhb_stocks": list(stat_stocks or []), "flow_stocks": flow_stocks},
            },
            "industry_fund_flow": {
                "ok": True,
                "value": {"industries": industries},
            },
        }

    def test_only_positive_institution_net_is_selected(self):
        fetched = self._fetched(
            lhb_stocks=[
                {"code": "600000", "name": "买入股", "institution_net": 5e8},
                {"code": "600001", "name": "卖出股", "institution_net": -3e8},
                {"code": "600002", "name": "零净买", "institution_net": 0},
            ],
            flow_stocks=[],
            industries=[],
        )
        result = scoring.build_stock_candidates(fetched, {"industries": []})
        codes = [c["code"] for c in result["candidates"]]
        assert "600000" in codes
        assert "600001" not in codes
        assert "600002" not in codes

    def test_flow_data_enriches_but_never_selects(self):
        """同花顺个股页是涨幅榜，不是净流入榜 —— 不能靠它新增候选。"""
        fetched = self._fetched(
            lhb_stocks=[{"code": "600000", "name": "甲", "institution_net": 5e8}],
            flow_stocks=[
                {"code": "600000", "name": "甲", "main_net": 2e8},
                {"code": "300999", "name": "榜上但没入选", "main_net": 9e8},
            ],
            industries=[],
        )
        result = scoring.build_stock_candidates(fetched, {"industries": []})
        codes = [c["code"] for c in result["candidates"]]
        assert "600000" in codes
        assert "300999" not in codes
        enriched = next(c for c in result["candidates"] if c["code"] == "600000")
        assert enriched["metrics"]["main_net"] == 2e8

    def test_outflow_on_gainer_list_is_flagged(self):
        fetched = self._fetched(
            lhb_stocks=[{"code": "600000", "name": "甲", "institution_net": 5e8}],
            flow_stocks=[{"code": "600000", "name": "甲", "main_net": -2e8}],
            industries=[],
        )
        result = scoring.build_stock_candidates(fetched, {"industries": []})
        row = result["candidates"][0]
        assert "净流出" in row["flow_warning"]
        assert result["outflow_flagged_count"] == 1

    def test_industry_outflow_filter_applies_to_leaders(self):
        industries = scoring.score_industries(
            _industry_fetched([
                _industry("强势行业", net=20.0, leader="龙头甲"),
                _industry("弱势行业", net=-20.0, leader="龙头乙"),
            ])
        )["industries"]
        fetched = self._fetched(lhb_stocks=[], flow_stocks=[], industries=[])
        result = scoring.build_stock_candidates(fetched, {"industries": industries})

        names = [c["name"] for c in result["candidates"]]
        assert "龙头甲" in names
        assert "龙头乙" not in names
        assert result["dropped_count"] == 1
        assert result["dropped_by_industry"][0]["name"] == "龙头乙"

    def test_leader_resolved_to_lhb_code_merges_instead_of_duplicating(self):
        """同一只股票既是龙虎榜机构净买（路 1，按真实代码入池）又是行业领涨股（路 2，
        按 ``LEADER::`` 占位入池）时，解析出真实代码后必须并成一条，而不是留下两条同码记录。"""
        industries = scoring.score_industries(
            _industry_fetched([_industry("强势行业", net=20.0, leader="甲")])
        )["industries"]
        fetched = self._fetched(
            lhb_stocks=[{"code": "600000", "name": "甲", "institution_net": 5e8}],
            flow_stocks=[],
            industries=[],
            # 龙虎榜个股统计把领涨股名字解析到真实代码。
            stat_stocks=[{"code": "600000", "name": "甲", "institution_net": 5e8}],
        )
        result = scoring.build_stock_candidates(fetched, {"industries": industries})

        codes = [c["code"] for c in result["candidates"]]
        assert codes.count("600000") == 1
        assert result["total_pool"] == 1
        # 两条入选理由应合并到同一条记录上。
        merged = next(c for c in result["candidates"] if c["code"] == "600000")
        assert "龙虎榜机构净买" in merged["reasons"]
        assert any("行业龙头" in r for r in merged["reasons"])
        # sources 的两条路径计数都应命中这唯一的一条。
        assert result["sources"]["龙虎榜机构净买"] == 1
        assert result["sources"]["行业龙头"] == 1

    def test_unresolved_leader_code_is_not_leaked(self):
        """领涨股在龙虎榜个股统计里查不到真实代码时，占位 ``LEADER::`` 前缀必须抹掉，
        不能泄漏到候选的代码字段。"""
        industries = scoring.score_industries(
            _industry_fetched([_industry("强势行业", net=20.0, leader="无代码龙头")])
        )["industries"]
        fetched = self._fetched(
            lhb_stocks=[], flow_stocks=[], industries=[], stat_stocks=[]
        )
        result = scoring.build_stock_candidates(fetched, {"industries": industries})

        leaked = [c for c in result["candidates"] if c["code"].startswith("LEADER::")]
        assert leaked == []
        row = next(c for c in result["candidates"] if c["name"] == "无代码龙头")
        assert row["code"] == ""


# ---------------------------------------------------------------------------
# 编排层：缓存新鲜度与抓取总预算（离线，不起网络）
# ---------------------------------------------------------------------------


class _Row:
    """替身快照行：只提供 service 读缓存会用到的几个属性。"""

    def __init__(self, payload, created_at=None):
        self.id = 1
        self.payload = payload
        self.created_at = created_at


class _Repo:
    def __init__(self, row):
        self._row = row

    def latest_snapshot(self):
        return self._row


def _overview_service(payload_dict, *, ttl=30):
    row = _Row(json.dumps(payload_dict, ensure_ascii=False))
    cfg = SimpleNamespace(
        industry_funds_enabled=True,
        industry_funds_cache_ttl_minutes=ttl,
    )
    return IndustryFundsService(repository=_Repo(row), config=cfg)


class TestOverviewFreshness:
    """INDUSTRY_FUNDS_CACHE_TTL_MINUTES：超窗只提示，不自动重抓。"""

    def test_fresh_snapshot_is_not_stale(self):
        payload = {"generated_at": datetime.now().isoformat(timespec="seconds")}
        out = _overview_service(payload, ttl=30).overview()
        assert out["stale"] is False
        assert out["cache_ttl_minutes"] == 30
        assert out["age_minutes"] is not None and out["age_minutes"] <= 1

    def test_snapshot_past_ttl_is_stale(self):
        old = (datetime.now() - timedelta(minutes=90)).isoformat(timespec="seconds")
        out = _overview_service({"generated_at": old}, ttl=30).overview()
        assert out["stale"] is True
        assert out["age_minutes"] >= 89

    def test_ttl_zero_disables_staleness(self):
        old = (datetime.now() - timedelta(hours=10)).isoformat(timespec="seconds")
        out = _overview_service({"generated_at": old}, ttl=0).overview()
        assert out["stale"] is False

    def test_unparseable_timestamp_is_never_stale(self):
        out = _overview_service({"generated_at": "not-a-date"}, ttl=30).overview()
        assert out["age_minutes"] is None
        assert out["stale"] is False


# @@BUDGET_TESTS@@


_BUDGET_SOURCES = (
    "fetch_industry_fund_flow",
    "fetch_lhb_institution",
    "fetch_etf_shares",
    "fetch_margin",
    "fetch_valuation",
    "fetch_stock_candidates",
)
_BUDGET_KEYS = {
    "industry_fund_flow",
    "lhb_institution",
    "etf_share",
    "margin",
    "valuation",
    "stock_candidates",
}


def _budget_service(total_timeout):
    cfg = SimpleNamespace(
        industry_funds_enabled=True,
        industry_funds_fetch_timeout_sec=30,
        industry_funds_total_timeout_sec=total_timeout,
    )
    return IndustryFundsService(repository=_Repo(None), config=cfg)


class TestFetchTotalBudget:
    """INDUSTRY_FUNDS_TOTAL_TIMEOUT_SEC：并行抓取阶段的外层兜底。"""

    def test_budget_degrades_stragglers(self, monkeypatch):
        def _slow(*_a, **_k):
            time.sleep(0.3)
            return {"key": "x", "ok": True, "source": "s", "as_of": "2026-10-16", "value": {}, "raw": {}}

        for name in _BUDGET_SOURCES:
            monkeypatch.setattr(service_mod, name, _slow)

        fetched = _budget_service(0.05)._fetch_all()

        assert set(fetched) == _BUDGET_KEYS
        budgeted = [v for v in fetched.values() if "total_budget_exceeded" in str(v.get("error") or "")]
        assert budgeted, "总预算耗尽时未返回的源应被标注降级"
        for entry in budgeted:
            assert entry["ok"] is False and entry["degraded"] is True

    def test_budget_zero_waits_for_all(self, monkeypatch):
        def _quick(*_a, **_k):
            return {"key": "x", "ok": True, "source": "s", "as_of": "2026-10-16", "value": {}, "raw": {}}

        for name in _BUDGET_SOURCES:
            monkeypatch.setattr(service_mod, name, _quick)

        fetched = _budget_service(0)._fetch_all()

        assert set(fetched) == _BUDGET_KEYS
        assert all(entry.get("ok") for entry in fetched.values())


# ---------------------------------------------------------------------------
# 网络冒烟测试（显式开启才跑）
# ---------------------------------------------------------------------------

_NETWORK = os.getenv("INDUSTRY_FUNDS_NETWORK_TESTS") == "1"

network = pytest.mark.skipif(
    not _NETWORK, reason="需要网络：设 INDUSTRY_FUNDS_NETWORK_TESTS=1 开启"
)


@network
class TestThsNetworkSmoke:
    """打真实上游的冒烟测试。

    重点回归两个曾经踩过的坑：上交所 ETF 份额返回 2025-01-15 的死数据，以及
    同花顺落地页解析。
    """

    def test_industry_flow_returns_about_90_industries(self):
        from src.services.industry_funds import ths_client

        rows = ths_client.fetch_industry_fund_flow("即时", timeout=30.0)
        assert len(rows) >= 80, f"只解析出 {len(rows)} 个行业，分页可能失效"
        assert all(r["name"] for r in rows)
        assert any(r["net"] is not None for r in rows)
        # 领涨股只有「即时」表有
        assert any(r["leader"] for r in rows)

    def test_industry_flow_rank_variant_parses(self):
        from src.services.industry_funds import ths_client

        rows = ths_client.fetch_industry_fund_flow("5日排行", timeout=30.0)
        assert len(rows) >= 80
        # 排行表的列顺序与即时表不同，这里确认没有按错位置
        assert any(r["net"] is not None for r in rows)

    def test_sse_etf_shares_are_not_stale(self):
        """回归：akshare 的 fund_etf_scale_sse 默认参数会返回 2025-01-15 的死数据。

        注意要用**交易日**而不是 ``date.today()``：生产代码走的是
        ``fetchers._trading_days(1)[0]``，周末/节假日直接传今天上游会返回空，
        测出来的"陈旧"其实是测试自己传错了日期。
        """
        from datetime import date, timedelta

        from src.services.industry_funds import fetchers

        trade_date = fetchers._trading_days(1)[0]
        shares = fetchers._fetch_sse_etf_shares(trade_date, timeout=30.0)
        assert len(shares) > 500, f"交易日 {trade_date} 只返回了 {len(shares)} 条上交所 ETF 份额"
        dates = {item["date"] for item in shares.values() if item.get("date")}
        assert dates, "上交所返回的数据没有统计日期"
        newest = max(dates)
        assert newest >= (date.today() - timedelta(days=10)).isoformat(), (
            f"上交所 ETF 份额数据陈旧：{newest}"
        )

    def test_szse_etf_shares_parse_with_bytesio_fix(self):
        """回归：pandas 3.0 下 pd.read_excel(bytes) 会抛 TypeError。"""
        from src.services.industry_funds import fetchers

        shares, stat_date = fetchers._fetch_szse_etf_shares(timeout=30.0)
        assert len(shares) > 500
        assert stat_date, "深交所没解析出统计日期"


class TestEtfShareMissingToday:
    """沪市当日份额没取到时不能拿 0 减基线：会算出一个「-数千亿份」的假赎回。"""

    def test_missing_sse_today_gives_no_delta(self, monkeypatch):
        from datetime import date

        from src.services.industry_funds import fetchers

        base = {"510300": {"code": "510300", "name": "沪深300ETF", "shares": 100e8, "date": "2026-11-10"}}
        szse = {"159919": {"code": "159919", "name": "沪深300ETF", "shares": 50e8, "date": "2026-11-16"}}
        monkeypatch.setattr(fetchers, "_fetch_sse_etf_shares", lambda day, *, timeout: {} if day != date(2026, 11, 10) else base)
        monkeypatch.setattr(fetchers, "_fetch_szse_etf_shares", lambda *, timeout: (szse, "2026-11-16"))
        monkeypatch.setattr(fetchers, "_trading_days", lambda count, market="cn": [
            date(2026, 11, 16), date(2026, 11, 15), date(2026, 11, 14), date(2026, 11, 13), date(2026, 11, 12),
            date(2026, 11, 10)])
        out = fetchers.fetch_etf_shares(timeout=1.0)
        assert out["ok"] is True
        assert out["value"]["sse"]["delta"] is None and out["value"]["combined_delta"] is None
        assert "沪市当日份额未取到" in out["degraded_reason"]

    def test_normal_case_still_reports_the_delta(self, monkeypatch):
        from datetime import date

        from src.services.industry_funds import fetchers

        today = {"510300": {"code": "510300", "name": "沪深300ETF", "shares": 110e8, "date": "2026-11-16"}}
        base = {"510300": {"code": "510300", "name": "沪深300ETF", "shares": 100e8, "date": "2026-11-10"}}
        monkeypatch.setattr(fetchers, "_fetch_sse_etf_shares",
                            lambda day, *, timeout: today if day == date(2026, 11, 16) else base)
        monkeypatch.setattr(fetchers, "_fetch_szse_etf_shares", lambda *, timeout: ({}, ""))
        monkeypatch.setattr(fetchers, "_trading_days", lambda count, market="cn": [
            date(2026, 11, 16), date(2026, 11, 15), date(2026, 11, 14), date(2026, 11, 13), date(2026, 11, 12),
            date(2026, 11, 10)])
        out = fetchers.fetch_etf_shares(timeout=1.0)
        assert out["value"]["sse"]["delta"] == 10e8
        assert out["value"]["combined_delta"] == 10e8


class TestFlowModules:
    """资金流向两个模块：口径、缺失日不冒充 0、深市只给当日、涨跌方向。"""

    def _days(self, monkeypatch, n=3):
        from datetime import date

        from src.services.industry_funds import flows

        days = [date(2026, 11, 10 + i) for i in range(n + 1)]
        monkeypatch.setattr(flows, "_trading_days", lambda count, market="cn": list(reversed(days)))
        return [d.isoformat() for d in days]

    def test_missing_sse_day_is_none_not_zero(self, monkeypatch):
        from src.services.industry_funds import flows

        days = self._days(monkeypatch)
        counts = {"2026-11-10": 100.0, "2026-11-11": 130.0, "2026-11-12": 90.0}   # 11-13 没数据
        monkeypatch.setattr(flows, "_sse_shares_by_date", lambda dates, *, timeout: (
            {d: {"510300": {"shares": counts[d] * 1e8, "date": d}} for d in dates if d in counts}, ""))
        monkeypatch.setattr(flows, "_etf_daily", lambda *, timeout, lookback_days=60: (
            {"510300": {d: {"amount": 1e9, "close": 4.0} for d in days}}, ""))
        monkeypatch.setattr(flows, "_szse_today", lambda *, timeout: (None, {}, "深市不可用"))
        out = flows.build_national_team(3, timeout=1)
        by_date = {d["date"]: d for d in out["days"]}
        assert by_date["2026-11-11"]["net_shares"] == 30.0
        assert by_date["2026-11-11"]["net_amount"] == 120.0            # 30 亿份 × 4 元 = 120 亿元
        assert by_date["2026-11-13"]["net_shares"] is None             # 没数据 → None
        assert by_date["2026-11-13"]["sse_missing"] is True
        # 连续净申购从最近一个有数据的日子往前数：11-12 是 -10，断开
        assert out["inflow_streak"] == 0
        assert out["as_of"] == "2026-11-12"

    def test_turnover_ratio_uses_previous_20_days(self, monkeypatch):
        from src.services.industry_funds import flows

        days = self._days(monkeypatch)
        history = {f"2026-10-{d:02d}": {"amount": 100.0, "close": 4.0} for d in range(1, 21)}
        today = {d: {"amount": 300.0, "close": 4.0} for d in days}
        monkeypatch.setattr(flows, "_sse_shares_by_date", lambda dates, *, timeout: ({}, ""))
        monkeypatch.setattr(flows, "_etf_daily", lambda *, timeout, lookback_days=60: ({"510300": {**history, **today}}, ""))
        monkeypatch.setattr(flows, "_szse_today", lambda *, timeout: (None, {}, ""))
        out = flows.build_national_team(3, timeout=1)
        last = out["days"][-1]
        # 前 17 天 100、窗口内 3 天 300：20 日均值 130，倍数 2.308
        assert last["turnover_ratio"] == pytest.approx(2.308, abs=0.01)

    def test_institutions_mark_undisclosed_day_pending(self, monkeypatch):
        from src.services.industry_funds import flows

        self._days(monkeypatch)
        class _DF:
            empty = False
            def to_dict(self, _how):
                return [
                    {"上榜日期": "2026-11-11", "代码": "600001", "名称": "甲", "机构买入净额": 2e8,
                     "机构买入总额": 3e8, "机构卖出总额": 1e8, "买方机构数": 2, "卖方机构数": 1,
                     "机构净买额占总成交额比": 5.0, "涨跌幅": 9.9, "收盘价": 11.0, "换手率": 12.0,
                     "上榜原因": "日涨幅偏离值达 7%"},
                ]
        monkeypatch.setattr(flows, "_call_with_timeout", lambda fn, *, timeout, label: (_DF(), None))
        out = flows.build_institutions(3, timeout=1)
        by_date = {d["date"]: d for d in out["days"]}
        assert by_date["2026-11-11"]["net"] == 2.0 and by_date["2026-11-11"]["count"] == 1
        assert by_date["2026-11-11"]["stocks"][0]["net"] == 2.0
        # 后面两天没有数据 = 还没披露，不能记成 0
        assert by_date["2026-11-12"]["net"] is None and by_date["2026-11-12"]["pending"] is True

    def test_each_module_degrades_on_its_own(self, monkeypatch):
        from src.services.industry_funds import flows

        self._days(monkeypatch)
        monkeypatch.setattr(flows, "build_national_team", lambda days, *, timeout: {"series": [], "degraded": True})
        monkeypatch.setattr(flows, "build_institutions", lambda days, *, timeout: {"series": [], "degraded": True})
        flows.clear_cache()
        out = flows.flow_series(3, timeout=1)
        assert out["national_team"]["degraded"] and out["institutions"]["degraded"]
        assert out["days"]


class TestFlowBaseline:
    """ETF 份额基线只认相邻交易日：缺失日不能跨日累计，也不能拿 0 冒充。"""

    def _setup(self, monkeypatch, counts):
        from datetime import date

        from src.services.industry_funds import flows

        days = [date(2026, 11, 10 + i) for i in range(4)]      # 11-10 基线 + 11-11..11-13
        monkeypatch.setattr(flows, "_trading_days", lambda count, market="cn": list(reversed(days)))
        monkeypatch.setattr(flows, "_sse_shares_by_date", lambda dates, *, timeout: (
            {d: {"510300": {"shares": counts[d] * 1e8, "date": d}} for d in dates if d in counts}, ""))
        monkeypatch.setattr(flows, "_etf_daily", lambda *, timeout, lookback_days=60: (
            {"510300": {d: {"amount": 1e9, "close": 4.0} for d in [x.isoformat() for x in days]}}, ""))
        monkeypatch.setattr(flows, "_szse_today", lambda *, timeout: (None, {}, ""))
        return flows.build_national_team(3, timeout=1)

    def test_gap_day_is_unknown_and_does_not_carry_over(self, monkeypatch):
        # 11-11 有、11-12 缺、11-13 有：11-13 的基线（11-12）缺失 → 未知，而不是用 11-11 累计两天
        out = self._setup(monkeypatch, {"2026-11-10": 100.0, "2026-11-11": 110.0, "2026-11-13": 130.0})
        by_date = {d["date"]: d for d in out["days"]}
        assert by_date["2026-11-11"]["net_shares"] == 10.0
        assert by_date["2026-11-12"]["net_shares"] is None and by_date["2026-11-12"]["sse_missing"] is True
        assert by_date["2026-11-13"]["net_shares"] is None
        assert by_date["2026-11-13"]["baseline_missing"] is True
        assert out["degraded"] is True and "缺相邻基线" in out["degraded_reason"]

    def test_first_day_needs_the_window_baseline(self, monkeypatch):
        out = self._setup(monkeypatch, {"2026-11-11": 110.0, "2026-11-12": 120.0, "2026-11-13": 130.0})
        by_date = {d["date"]: d for d in out["days"]}
        assert by_date["2026-11-11"]["net_shares"] is None and by_date["2026-11-11"]["baseline_missing"] is True
        assert by_date["2026-11-12"]["net_shares"] == 10.0


class TestHotMoney:
    """游资席位矩阵：剔除通道/机构席位、按日期聚合、同日同票合并、每股与每席位都对上。"""

    def test_matrix_aggregates_and_excludes_channels(self, monkeypatch):
        from datetime import date

        from src.services.industry_funds import flows, hot_money

        days = [date(2026, 11, 10 + i) for i in range(3)]      # 基线 + 11-11、11-12
        monkeypatch.setattr(flows, "_trading_days", lambda count, market="cn": list(reversed(days)))

        class _DF:
            def __init__(self, rows):
                self._rows = rows
                self.empty = not rows
            def to_dict(self, _how):
                return self._rows

        seats = [
            {"营业部名称": "某某证券上海分公司", "营业部代码": "S1", "买入总金额": 3e8, "卖出总金额": 1e8, "总买卖净额": 2e8},
            {"营业部名称": "某某证券深圳分公司", "营业部代码": "S2", "买入总金额": 1e8, "卖出总金额": 1e8, "总买卖净额": 0.0},
            {"营业部名称": "沪股通专用", "营业部代码": "HK", "买入总金额": 9e8, "卖出总金额": 0.0, "总买卖净额": 9e8},
            {"营业部名称": "机构专用", "营业部代码": "JG", "买入总金额": 9e8, "卖出总金额": 0.0, "总买卖净额": 9e8},
        ]
        monkeypatch.setattr(hot_money, "_call_with_timeout", lambda fn, *, timeout, label: (_DF(seats), None))
        trades = {
            "S1": [{"TRADE_DATE": date(2026, 11, 11), "SECURITY_CODE": "600001", "SECURITY_NAME_ABBR": "甲", "ACT_BUY": 2e8, "ACT_SELL": 0.5e8},
                   # 同一笔因为第二个上榜原因被重复列出：数值完全相同，只算一次
                   {"TRADE_DATE": date(2026, 11, 11), "SECURITY_CODE": "600001", "SECURITY_NAME_ABBR": "甲",
                    "ACT_BUY": 2e8, "ACT_SELL": 0.5e8, "EXPLANATION": "日换手率达到20%的前5只证券"},
                   {"TRADE_DATE": date(2026, 11, 12), "SECURITY_CODE": "600002", "SECURITY_NAME_ABBR": "乙", "ACT_BUY": 0.5e8, "ACT_SELL": 0.9e8}],
            "S2": [{"TRADE_DATE": date(2026, 11, 11), "SECURITY_CODE": "600001", "SECURITY_NAME_ABBR": "甲", "ACT_BUY": 0.2e8, "ACT_SELL": 0.0}],
        }
        def _fake_trades(code, window_dates, *, timeout):
            return hot_money._parse_trade_rows(trades.get(code, []), window_dates), ""

        monkeypatch.setattr(hot_money, "_seat_trades", _fake_trades)

        out = hot_money.build_hot_money(2, 10, timeout=1)
        assert [s["code"] for s in out["seats"]] == ["S1", "S2"]        # 通道和机构被剔除
        day = next(d for d in out["dates"] if d["date"] == "2026-11-11")
        row = next(r for r in day["rows"] if r["code"] == "600001")
        # 同日同票同一席位合并；两个席位都买到同一只票
        assert row["cells"]["S1"] == {"buy": 2.0, "sell": 0.5, "net": 1.5}
        assert row["cells"]["S2"] == {"buy": 0.2, "sell": 0.0, "net": 0.2}
        assert row["net"] == 1.7 and row["buy"] == 2.2 and row["sell"] == 0.5
        # 最近日期排最前
        assert out["dates"][0]["date"] == "2026-11-12"
        assert "通道" in out["coverage"]

class TestHotMoneyDedupe:
    """同一只票同一天多个上榜原因时的两个坑：重复行会翻倍、三日榜与单日榜区间不同。"""

    def test_duplicate_board_rows_are_counted_once(self):
        from datetime import date

        from src.services.industry_funds import hot_money

        day = {"2026-11-16"}
        rows = [
            {"TRADE_DATE": date(2026, 11, 16), "SECURITY_CODE": "002343", "SECURITY_NAME_ABBR": "慈文传媒",
             "ACT_BUY": 86678981.6, "ACT_SELL": 1723925, "EXPLANATION": "日振幅值达到15%的前5只证券"},
            {"TRADE_DATE": date(2026, 11, 16), "SECURITY_CODE": "002343", "SECURITY_NAME_ABBR": "慈文传媒",
             "ACT_BUY": 86678981.6, "ACT_SELL": 1723925, "EXPLANATION": "日换手率达到20%的前5只证券"},
        ]
        out = hot_money._parse_trade_rows(rows, day)
        cell = out["2026-11-16"]["002343"]
        assert cell["buy"] == 0.8668 and cell["sell"] == 0.0172      # 不是 1.7 亿

    def test_single_day_board_wins_over_the_three_day_board(self):
        from datetime import date

        from src.services.industry_funds import hot_money

        rows = [
            {"TRADE_DATE": date(2026, 11, 16), "SECURITY_CODE": "000595", "SECURITY_NAME_ABBR": "新能股份",
             "ACT_BUY": 18368530.0, "ACT_SELL": 58984166.86, "EXPLANATION": "日涨幅偏离值达到7%的前5只证券"},
            {"TRADE_DATE": date(2026, 11, 16), "SECURITY_CODE": "000595", "SECURITY_NAME_ABBR": "新能股份",
             "ACT_BUY": 78287546.0, "ACT_SELL": 59296539.86,
             "EXPLANATION": "连续三个交易日内，涨幅偏离值累计达到20%的证券"},
        ]
        cell = hot_money._parse_trade_rows(rows, {"2026-11-16"})["2026-11-16"]["000595"]
        # 单日榜 -4061.56 万（四舍五入到亿元再折回会有 1 万的误差），关键是不能是两者相加
        assert round((cell["buy"] - cell["sell"]) * 10000) == -4061

    def test_only_the_three_day_board_is_used_when_that_is_all_there_is(self):
        from datetime import date

        from src.services.industry_funds import hot_money

        rows = [{"TRADE_DATE": date(2026, 11, 16), "SECURITY_CODE": "600001", "SECURITY_NAME_ABBR": "甲",
                 "ACT_BUY": 3e8, "ACT_SELL": 1e8, "EXPLANATION": "连续三个交易日内，涨幅偏离值累计达到20%的证券"}]
        cell = hot_money._parse_trade_rows(rows, {"2026-11-16"})["2026-11-16"]["600001"]
        assert cell["buy"] == 3.0 and cell["sell"] == 1.0


class TestSeatAliases:
    """昵称映射：能对上就用昵称当列头，对不上就显示营业部；只收录核对过的条目。"""

    def test_verified_alias_maps_to_the_seat(self):
        from src.services.industry_funds.seat_aliases import alias_for

        assert alias_for("国泰海通证券股份有限公司上海长宁区江苏路证券营业部") == "章盟主"
        assert alias_for("中信证券股份有限公司上海虹口区溧阳路证券营业部") == "孙哥"
        assert alias_for("中国银河证券股份有限公司绍兴鲁迅中路证券营业部") == "流沙河"
        assert alias_for("国泰海通证券股份有限公司南京太平南路证券营业部") == "作手新一"
        assert alias_for("华泰证券股份有限公司上海武定路证券营业部") == "炒新一族"
        assert alias_for("华鑫证券有限责任公司上海宛平南路证券营业部") == "炒股养家"
        assert alias_for("东吴证券股份有限公司苏州相城大道证券营业部") == "余哥"
        assert alias_for("国泰海通证券股份有限公司武汉紫阳东路证券营业部") == "紫阳东路"
        assert alias_for("国新证券股份有限公司北京分公司") == "国新北京分"

    def test_every_person_alias_has_a_basis_and_groups_are_not_mapped(self):
        """人名的昵称必须带核对依据；组合类昵称（多个席位）一律不映射，否则必然是错的。"""
        from src.services.industry_funds.seat_aliases import SEAT_ALIASES, alias_for

        for _keyword, alias, source in SEAT_ALIASES:
            if len(alias) >= 3 and alias not in ("紫阳东路", "益田路", "金田路", "深南东路", "上海自贸区", "国新北京分"):
                assert source, alias
        for group in ("佛山系", "杭州帮", "温州帮", "低位挖掘", "拉萨天团", "量化打板"):
            assert all(alias != group for _k, alias, _s in SEAT_ALIASES)

    def test_unknown_seat_has_no_alias(self):
        from src.services.industry_funds.seat_aliases import alias_for

        assert alias_for("高盛(中国)证券有限责任公司上海浦东新区世纪大道证券营业部") is None
        assert alias_for("") is None

    def test_seat_columns_carry_the_alias(self, monkeypatch):
        from datetime import date

        from src.services.industry_funds import flows, hot_money

        monkeypatch.setattr(flows, "_trading_days", lambda count, market="cn": [date(2026, 11, 13), date(2026, 11, 12)])

        class _DF:
            empty = False
            def to_dict(self, _how):
                return [{"营业部名称": "国泰海通证券股份有限公司上海长宁区江苏路证券营业部", "营业部代码": "10025080",
                         "买入总金额": 3e8, "卖出总金额": 1e8, "总买卖净额": 2e8}]

        monkeypatch.setattr(hot_money, "_call_with_timeout", lambda fn, *, timeout, label: (_DF(), None))
        monkeypatch.setattr(hot_money, "_seat_trades", lambda code, window, *, timeout: ({}, ""))
        out = hot_money.build_hot_money(2, 10, timeout=1)
        seat = out["seats"][0]
        assert seat["alias"] == "章盟主" and "逐笔核对" in seat["alias_source"]
        assert seat["name"].endswith("江苏路证券营业部")     # 全名仍然留给悬停
        assert "人工整理" in out["alias_note"]

class TestSeatColumnsPriority:
    """选列：人名类昵称优先占列；同一昵称下的多个营业部合并成一列。"""

    def test_columns_follow_the_buy_amount_even_when_a_nickname_is_known(self, monkeypatch):
        """矩阵列只看买入额：昵称只影响列头显示，不改变谁进列；按昵称找人去「游资席位」区。"""
        from datetime import date

        from src.services.industry_funds import flows, hot_money

        monkeypatch.setattr(flows, "_trading_days",
                            lambda count, market="cn": [date(2026, 11, 13), date(2026, 11, 12), date(2026, 11, 11)])

        class _DF:
            empty = False
            def to_dict(self, _how):
                return [
                    # 买入额最大，但识别不出昵称
                    {"营业部名称": "某证券股份有限公司总部", "营业部代码": "BIG", "买入总金额": 90e8,
                     "卖出总金额": 1e8, "总买卖净额": 89e8},
                    # 名气大、当天买得少：仍然要占一列
                    {"营业部名称": "中信证券股份有限公司上海虹口区溧阳路证券营业部", "营业部代码": "SUN",
                     "买入总金额": 0.2e8, "卖出总金额": 0.1e8, "总买卖净额": 0.1e8},
                ]

        monkeypatch.setattr(hot_money, "_call_with_timeout", lambda fn, *, timeout, label: (_DF(), None))
        monkeypatch.setattr(hot_money, "_seat_trades", lambda code, window, *, timeout: ({}, ""))
        out = hot_money.build_hot_money(2, 1, timeout=1)
        assert [s["code"] for s in out["seats"]] == ["BIG"]        # 买得最多的进列，哪怕没昵称
        assert [s["alias"] for s in out["all_seats"]] == ["孙哥", None]   # 全部席位里人名排在前面

    def test_same_alias_is_merged_into_one_column(self, monkeypatch):
        from datetime import date

        from src.services.industry_funds import flows, hot_money

        monkeypatch.setattr(flows, "_trading_days",
                            lambda count, market="cn": [date(2026, 11, 13), date(2026, 11, 12), date(2026, 11, 11)])

        class _DF:
            empty = False
            def to_dict(self, _how):
                return [
                    {"营业部名称": "华泰证券股份有限公司深圳益田路荣超商务中心证券营业部", "营业部代码": "Y1",
                     "买入总金额": 1e8, "卖出总金额": 0.2e8, "总买卖净额": 0.8e8},
                    {"营业部名称": "华鑫证券有限责任公司深圳益田路证券营业部", "营业部代码": "Y2",
                     "买入总金额": 0.5e8, "卖出总金额": 0.1e8, "总买卖净额": 0.4e8},
                ]

        monkeypatch.setattr(hot_money, "_call_with_timeout", lambda fn, *, timeout, label: (_DF(), None))

        def _trades(code, window, *, timeout):
            cell = {"name": "甲", "buy": 1.0 if code == "Y1" else 0.5, "sell": 0.0}
            return {d: {"600001": dict(cell)} for d in window}, ""

        monkeypatch.setattr(hot_money, "_seat_trades", _trades)
        out = hot_money.build_hot_money(2, 10, timeout=1)
        assert len(out["seats"]) == 1 and out["seats"][0]["alias"] == "益田路"
        assert out["seats"][0]["buy"] == 1.5 and set(out["seats"][0]["codes"]) == {"Y1", "Y2"}
        day = next(d for d in out["dates"] if d["rows"])
        assert day["rows"][0]["cells"]["益田路"]["buy"] == 1.5      # 两个席位合并到同一列


class TestHotMoneyVerdict:
    """结论只用已经发生的变化描述：参与度、净买率、资金落在涨票还是跌票。"""

    def _rows(self):
        return [
            {"date": "2026-11-16", "seats": 190, "buy": 67.0, "sell": 59.0, "net": 8.0, "net_ratio": 0.12,
             "down_buy_share": 0.54, "limit_share": 0.36, "up_share": 0.57},
            {"date": "2026-11-13", "seats": 192, "buy": 69.8, "sell": 62.4, "net": 7.3, "net_ratio": 0.11,
             "down_buy_share": 0.42, "limit_share": 0.35, "up_share": 0.58},
            {"date": "2026-11-12", "seats": 178, "buy": 67.9, "sell": 63.5, "net": 4.5, "net_ratio": 0.07,
             "down_buy_share": 0.31, "limit_share": 0.39, "up_share": 0.62},
            {"date": "2026-11-11", "seats": 198, "buy": 94.3, "sell": 86.7, "net": 7.6, "net_ratio": 0.08,
             "down_buy_share": 0.01, "limit_share": 0.56, "up_share": 0.82},
            {"date": "2026-11-10", "seats": 153, "buy": 57.7, "sell": 60.1, "net": -2.4, "net_ratio": -0.04,
             "down_buy_share": 0.06, "limit_share": 0.72, "up_share": 0.88},
            {"date": "2026-11-09", "seats": 194, "buy": 79.3, "sell": 61.6, "net": 17.7, "net_ratio": 0.22,
             "down_buy_share": 0.19, "limit_share": 0.46, "up_share": 0.69},
        ]

    def test_shift_to_buying_decliners_is_reported(self):
        from src.services.industry_funds.hot_money import _verdict

        out = _verdict(self._rows())
        assert "转为接跌" in out["tags"]
        assert "落在下跌股上的买入额" in out["text"]

    def test_big_drop_in_net_ratio_is_reported_as_weaker_attack(self):
        from src.services.industry_funds.hot_money import _verdict

        rows = self._rows()
        for row in rows[:3]:
            row["net_ratio"] = 0.05
        out = _verdict(rows)
        assert "进攻减弱" in out["tags"]

    def test_too_few_days_does_not_guess(self):
        from src.services.industry_funds.hot_money import _verdict

        out = _verdict(self._rows()[:3])
        assert "样本不足" in out["text"] and out["tags"] == []


class TestHotMoneyChangeAndSeatDetail:
    """矩阵每行带当天涨跌幅；席位悬停明细按席位（或一个昵称下的多家营业部）合并。"""

    def test_rows_carry_the_change_pct(self, monkeypatch):
        from datetime import date

        from src.services.industry_funds import flows, hot_money

        monkeypatch.setattr(flows, "_trading_days",
                            lambda count, market="cn": [date(2026, 11, 16), date(2026, 11, 13), date(2026, 11, 12)])

        monkeypatch.setattr(hot_money, "_call_with_timeout", lambda fn, *, timeout, label: (_EmptyDF(), None))
        monkeypatch.setattr(hot_money, "_seat_columns", lambda days, seats, *, timeout: (
            [{"code": "S1", "codes": ["S1"], "name": "某营业部", "short": "某", "alias": None, "alias_source": "",
              "buy": 2.0, "sell": 0.0, "net": 2.0, "days": 1, "kind": 2}], "", [], {}))
        monkeypatch.setattr(hot_money, "_daily_market", lambda days, *, timeout: ({}, ""))
        monkeypatch.setattr(hot_money, "_seat_trades", lambda code, window, *, timeout: (
            {d: {"600001": {"name": "甲", "buy": 2.0, "sell": 0.0, "chg": -9.99}} for d in window}, ""))
        out = hot_money.build_hot_money(2, 5, timeout=1)
        row = next(r for d in out["dates"] for r in d["rows"] if r["code"] == "600001")
        assert row["change_pct"] == -9.99

    def test_seat_day_detail_sums_multiple_seats_and_keeps_the_change(self, monkeypatch):
        from src.services.industry_funds import hot_money

        calls = []

        def _trades(code, window, *, timeout):
            calls.append(code)
            return {d: {"600001": {"name": "甲", "buy": 1.0 if code == "A" else 0.5, "sell": 0.2, "chg": 3.2}}
                    for d in window}, ""

        monkeypatch.setattr(hot_money, "_seat_trades", _trades)
        out = hot_money.seat_day_detail(["A", "B"], "2026-11-16", timeout=1)
        assert calls == ["A", "B"]
        assert out["stocks"][0] == {"code": "600001", "name": "甲", "buy": 1.5, "sell": 0.4, "net": 1.1,
                                    "change_pct": 3.2}


class _EmptyDF:
    empty = True
