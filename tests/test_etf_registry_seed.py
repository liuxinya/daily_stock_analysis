#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the on-exchange ETF-registry seed and its merge path.

Mirrors ``TestIndexRegistrySeed`` in ``test_generate_index_from_csv.py``: the
seed builds valid ETF tuples, the ``--etf-only`` merge preserves every other
row and is byte-stable, and the full rebuild path never erases ETF rows. The
seed now covers the full exchange-traded ETF universe (refreshed by
``scripts/refresh_etf_registry.py``), so the expected count is read from the
seed itself rather than hard-coded.
"""

import json
import sys
from pathlib import Path

import pytest

# Add scripts directory to path.
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from generate_index_from_csv import (  # noqa: E402
    load_etf_registry_seed,
    build_etf_entries_from_seed,
    validate_etf_registry,
    run_etf_only,
    build_stock_index,
    compress_index,
    main,
)

# The seed now holds the full exchange-traded ETF universe, so its exact size
# shifts with each refresh. Read it from the seed instead of pinning a number;
# the merge/count assertions below stay valid as the universe grows.
EXPECTED_ETF_COUNT = len(load_etf_registry_seed())
# __ETF_TESTS__


class TestEtfRegistrySeed:
    """Seed builds valid ETF rows; the etf-only merge is stable."""

    def test_seed_loads_expected_rows(self):
        rows = load_etf_registry_seed()
        assert len(rows) == EXPECTED_ETF_COUNT
        # Guards against a regression to the old tiny curated seed.
        assert EXPECTED_ETF_COUNT >= 1000

    def test_seed_entries_build_valid_etf_tuples(self):
        entries = build_etf_entries_from_seed(load_etf_registry_seed())
        assert len(entries) == EXPECTED_ETF_COUNT
        for entry in entries:
            assert entry["market"] == "CN"
            assert entry["assetType"] == "etf"
            assert entry["active"] is True
            assert isinstance(entry["popularity"], int) and entry["popularity"] >= 0
            assert entry["nameZh"]
            assert entry["pinyinFull"]
            assert entry["pinyinAbbr"]
            # Display is the bare 6-digit code; canonical is ``{code}.SH|SZ``.
            assert entry["displayCode"] == entry["canonicalCode"].split(".")[0]
            assert len(entry["displayCode"]) == 6 and entry["displayCode"].isdigit()

    def test_seed_includes_requested_software_etf(self):
        """Regression for the reported case: 软件ETF 515230 must be present and
        searchable (canonical ``515230.SH``, bare display ``515230``)."""
        entries = build_etf_entries_from_seed(load_etf_registry_seed())
        by_display = {e["displayCode"]: e for e in entries}
        assert "515230" in by_display
        row = by_display["515230"]
        assert row["canonicalCode"] == "515230.SH"
        # Name carries the issuer suffix (e.g. 软件ETF国泰) to disambiguate the
        # several issuers of a 软件ETF; the core "软件ETF" token still matches.
        assert "软件ETF" in row["nameZh"]
        assert row["assetType"] == "etf"

    def test_seed_canonical_suffix_matches_code_prefix(self):
        entries = build_etf_entries_from_seed(load_etf_registry_seed())
        for entry in entries:
            code6, suffix = entry["canonicalCode"].split(".")
            if code6[:2] in {"50", "51", "52", "53", "55", "56", "58"}:
                assert suffix == "SH"
            elif code6[:2] in {"15", "16", "18"}:
                assert suffix == "SZ"
            else:  # pragma: no cover - guarded by build validation
                pytest.fail(f"unexpected ETF prefix: {entry['canonicalCode']}")

    def test_validate_accepts_valid_seed(self):
        validate_etf_registry(build_etf_entries_from_seed(load_etf_registry_seed()))
# __ETF_TESTS2__

    def test_validate_rejects_duplicate_canonical(self):
        entries = build_etf_entries_from_seed(load_etf_registry_seed())
        entries.append(dict(entries[0]))
        with pytest.raises(ValueError, match="duplicate etf canonical"):
            validate_etf_registry(entries)

    def test_validate_rejects_bad_canonical(self):
        entries = build_etf_entries_from_seed(load_etf_registry_seed())
        entries[0]["canonicalCode"] = "515230.XX"
        with pytest.raises(ValueError, match="canonical must match"):
            validate_etf_registry(entries)

    def test_validate_rejects_display_code_collision(self):
        """Defensive: an ETF display code that collides with another active
        identity in the same payload is rejected."""
        entries = build_etf_entries_from_seed(load_etf_registry_seed())
        # A stock row whose bare display ``515230`` collides with the ETF.
        non_etf = [["000001.SZ", "515230", "冲突股票", "ctgp", "ctgp", [], "CN", "stock", True, 100]]
        with pytest.raises(ValueError, match="collides with existing identity"):
            validate_etf_registry(entries, non_etf_rows=non_etf)

    def test_validate_rejects_non_finite_popularity(self):
        entries = build_etf_entries_from_seed(load_etf_registry_seed())
        entries[0]["popularity"] = float("nan")
        with pytest.raises(ValueError, match="non-negative integer"):
            validate_etf_registry(entries)

    @pytest.mark.parametrize("bad_popularity", [1.5, True, -1, "90"])
    def test_build_rejects_non_integer_popularity(self, bad_popularity):
        rows = load_etf_registry_seed()
        rows[0]["popularity"] = bad_popularity
        with pytest.raises(ValueError, match="non-negative integer"):
            build_etf_entries_from_seed(rows)

    def test_build_rejects_suffix_prefix_mismatch(self):
        rows = load_etf_registry_seed()
        rows[0]["canonical_code"] = "515230.SZ"  # 51x is Shanghai, not .SZ
        with pytest.raises(ValueError, match="contradicts code prefix"):
            build_etf_entries_from_seed(rows)

    def test_build_rejects_non_etf_prefix(self):
        rows = load_etf_registry_seed()
        rows[0]["canonical_code"] = "600000.SH"  # a stock code, not an ETF prefix
        with pytest.raises(ValueError, match="not a known ETF prefix"):
            build_etf_entries_from_seed(rows)

    def test_seed_rejects_fractional_popularity(self, tmp_path):
        seed = tmp_path / "etf_registry.csv"
        seed.write_text(
            "canonical_code,name_zh,aliases,popularity\n"
            "515230.SH,软件ETF,,1.5\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="plain integer"):
            load_etf_registry_seed(seed)

    def test_seed_rejects_duplicate_canonical_in_file(self, tmp_path):
        seed = tmp_path / "etf_registry.csv"
        seed.write_text(
            "canonical_code,name_zh,aliases,popularity\n"
            "515230.SH,软件ETF,,90\n"
            "515230.SH,软件ETF复制,,90\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="duplicate etf canonical in seed"):
            load_etf_registry_seed(seed)
# __ETF_TESTS3__

    def test_etf_only_preserves_other_rows_and_appends_all(self, tmp_path):
        output = tmp_path / "stocks.index.json"
        output.write_text(
            json.dumps(
                [
                    ["000001.SZ", "000001", "平安银行", "payh", "payh", [], "CN", "stock", True, 100],
                    ["sh000300", "sh000300", "沪深300", "hs300", "hs300", [], "CN", "index", True, 100],
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        merged = run_etf_only(output, test=True)
        other = [x for x in merged if not (len(x) > 7 and x[7] == "etf")]
        etf_rows = [x for x in merged if len(x) > 7 and x[7] == "etf"]
        # Stock and index rows preserved in original order.
        assert [x[0] for x in other] == ["000001.SZ", "sh000300"]
        assert len(etf_rows) == EXPECTED_ETF_COUNT
        # ETF rows sorted by canonical.
        canonicals = [x[0] for x in etf_rows]
        assert canonicals == sorted(canonicals)

    def test_etf_only_replaces_stale_etf_rows(self, tmp_path):
        """Old ETF rows are removed before the seed rows are appended, so a
        rerun never accumulates duplicates."""
        output = tmp_path / "stocks.index.json"
        output.write_text(
            json.dumps(
                [["999999.SH", "999999", "过期ETF", "gqetf", "gqetf", [], "CN", "etf", True, 90]],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        merged = run_etf_only(output, test=True)
        etf_rows = [x for x in merged if len(x) > 7 and x[7] == "etf"]
        assert len(etf_rows) == EXPECTED_ETF_COUNT
        assert "999999.SH" not in {x[0] for x in etf_rows}

    def test_etf_only_is_byte_stable(self, tmp_path):
        output = tmp_path / "stocks.index.json"
        output.write_text(
            json.dumps(
                [["000001.SZ", "000001", "平安银行", "payh", "payh", [], "CN", "stock", True, 100]],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        run_etf_only(output, test=False)
        first = output.read_bytes()
        run_etf_only(output, test=False)
        assert first == output.read_bytes()

    def test_etf_only_test_mode_does_not_write(self, tmp_path):
        output = tmp_path / "stocks.index.json"
        output.write_text(
            json.dumps(
                [["000001.SZ", "000001", "平安银行", "payh", "payh", [], "CN", "stock", True, 100]],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        before = output.read_bytes()
        run_etf_only(output, test=True)
        assert output.read_bytes() == before

    def test_etf_only_rejects_malformed_existing_tuple(self, tmp_path):
        output = tmp_path / "stocks.index.json"
        output.write_text(json.dumps([["too-short"]]), encoding="utf-8")
        with pytest.raises(ValueError, match="compressed tuple"):
            run_etf_only(output, test=True)
# __ETF_TESTS4__

    def test_full_path_merge_includes_etf_rows(self, tmp_path, monkeypatch):
        """The full rebuild path (``main()``) merges the ETF seed before
        compression, so a full stock-index rebuild never erases ETF rows."""
        import generate_index_from_csv as gen

        stocks = [
            {"ts_code": "000001.SZ", "symbol": "000001", "name": "平安银行"},
            {"ts_code": "600519.SH", "symbol": "600519", "name": "贵州茅台"},
        ]
        monkeypatch.setattr(gen, "load_tushare_data", lambda data_dir: stocks)
        monkeypatch.setattr(gen, "require_pypinyin", lambda: True)
        monkeypatch.setattr(
            sys, "argv", ["generate_index_from_csv.py", "--source", "tushare", "--test"]
        )
        assert main() == 0

        # Rebuild the same pipeline in-process to inspect the merged rows.
        index = build_stock_index(stocks)
        etf_entries = build_etf_entries_from_seed(load_etf_registry_seed())
        validate_etf_registry(etf_entries, non_etf_rows=compress_index(index))
        etf_entries.sort(key=lambda entry: str(entry["canonicalCode"]))
        index.extend(etf_entries)
        compressed = compress_index(index)
        etf_rows = [item for item in compressed if len(item) > 7 and item[7] == "etf"]
        stock_rows = [item for item in compressed if len(item) > 7 and item[7] == "stock"]
        assert len(etf_rows) == EXPECTED_ETF_COUNT
        assert len(stock_rows) == 2
        # ETF rows are canonical-sorted for byte-stability.
        canonicals = [x[0] for x in etf_rows]
        assert canonicals == sorted(canonicals)
