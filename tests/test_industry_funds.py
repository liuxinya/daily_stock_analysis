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
