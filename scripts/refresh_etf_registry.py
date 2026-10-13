#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Refresh the exchange-traded fund (ETF) registry seed from public listings.

Produces ``scripts/stock_index_seeds/etf_registry.csv`` covering every
on-exchange (场内) ETF, so the autocomplete index can match the full ETF
universe instead of a small curated subset. ``generate_index_from_csv.py``
then merges this seed into ``apps/dsa-web/public/stocks.index.json`` (both the
full rebuild and ``--etf-only`` paths).

Sources (no Tushare token / points required):
  - Code set + listing venue: Sina ``Market_Center.getHQNodeData`` node
    ``etf_hq_fund`` — the authoritative list of exchange-traded ETFs.
  - Display names: Eastmoney ``fund.eastmoney.com/js/fundcode_search.js`` — the
    public all-fund code→name table (the same upstream akshare's
    ``fund_name_em`` wraps). Falls back to the Sina short name per code.

The venue (``.SH``/``.SZ``) is derived from the Sina symbol and cross-checked
against the code prefix via the generator's own ETF rules, so the written CSV
always passes ``build_etf_entries_from_seed`` / ``validate_etf_registry``.
Rows are sorted by canonical code for byte-stable output.

Usage:
    python scripts/refresh_etf_registry.py            # fetch + overwrite seed
    python scripts/refresh_etf_registry.py --test     # fetch + summary, no write
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

import requests

# Reuse the generator's ETF rules so the CSV we emit is guaranteed loadable.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_index_from_csv import (  # noqa: E402
    _ETF_REGISTRY_SEED_PATH,
    build_etf_entries_from_seed,
    validate_etf_registry,
    _etf_expected_suffix,
)

_SINA_ETF_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData"
)
_EM_FUNDCODE_URL = "https://fund.eastmoney.com/js/fundcode_search.js"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    ),
    "Referer": "https://finance.sina.com.cn/",
}
_TIMEOUT = 20
_PAGE_SIZE = 100
_MAX_PAGES = 60
_DEFAULT_POPULARITY = 90
# A real refresh should return >1000 ETFs; a much smaller result means a
# throttled/partial fetch, and we refuse to clobber the existing seed with it.
_MIN_EXPECTED = 800


def _get(url: str, params: Optional[Dict[str, str]] = None) -> requests.Response:
    resp = requests.get(url, params=params, headers=_HEADERS, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp


def fetch_exchange_etf_codes() -> List[Dict[str, str]]:
    """Return the full on-exchange ETF list from Sina: code, venue, short name."""
    out: Dict[str, Dict[str, str]] = {}
    for page in range(1, _MAX_PAGES + 1):
        params = {
            "page": str(page),
            "num": str(_PAGE_SIZE),
            "sort": "symbol",
            "asc": "1",
            "node": "etf_hq_fund",
            "_s_r_a": "init",
        }
        rows = json.loads(_get(_SINA_ETF_URL, params).text) or []
        for row in rows:
            symbol = str(row.get("symbol") or "")
            code = str(row.get("code") or "")
            if not (len(code) == 6 and code.isdigit()):
                continue
            venue = "SH" if symbol.startswith("sh") else "SZ" if symbol.startswith("sz") else ""
            out[code] = {"code": code, "venue": venue, "name": str(row.get("name") or "").strip()}
        if len(rows) < _PAGE_SIZE:
            break
    return list(out.values())


def fetch_fund_names() -> Dict[str, str]:
    """Return a code→name map from Eastmoney's public all-fund table."""
    text = _get(_EM_FUNDCODE_URL).text
    match = re.search(r"var\s+r\s*=\s*(\[.*\])\s*;?\s*$", text.strip(), re.S)
    if not match:
        raise ValueError("unexpected fundcode_search payload (no `var r = [...]`)")
    names: Dict[str, str] = {}
    for item in json.loads(match.group(1)):
        if len(item) >= 3:
            names[str(item[0])] = str(item[2]).strip()
    return names
def build_seed_rows(
    etfs: List[Dict[str, str]], names: Dict[str, str]
) -> List[Dict[str, str]]:
    """Join codes with names into deterministic seed rows (canonical-sorted)."""
    rows: List[Dict[str, str]] = []
    skipped: List[str] = []
    for etf in etfs:
        code = etf["code"]
        # Prefer the venue the code prefix implies; cross-check the Sina symbol.
        expected = _etf_expected_suffix(code)
        venue = expected or etf.get("venue") or ""
        if not venue or (etf.get("venue") and etf["venue"] != venue):
            skipped.append(code)
            continue
        name = names.get(code) or etf.get("name") or ""
        name = name.strip()
        if not name:
            skipped.append(code)
            continue
        rows.append({
            "canonical_code": f"{code}.{venue}",
            "name_zh": name,
            "aliases": [],
            "popularity": _DEFAULT_POPULARITY,
        })
    rows.sort(key=lambda r: str(r["canonical_code"]))
    if skipped:
        print(f"[refresh_etf_registry] skipped {len(skipped)} codes "
              f"(unknown venue / empty name): {skipped[:10]}", flush=True)
    return rows


def write_seed_csv(rows: List[Dict[str, object]], path: Path) -> None:
    """Write the seed CSV deterministically (fixed header, LF line endings)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["canonical_code", "name_zh", "aliases", "popularity"])
        for r in rows:
            aliases = r.get("aliases") or []
            writer.writerow([
                r["canonical_code"],
                r["name_zh"],
                "|".join(aliases) if isinstance(aliases, list) else str(aliases),
                str(r["popularity"]),
            ])
    tmp.replace(path)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="刷新场内 ETF 注册表种子（scripts/stock_index_seeds/etf_registry.csv）"
    )
    parser.add_argument(
        "--test", action="store_true",
        help="抓取并校验后仅打印摘要，不写入 CSV",
    )
    parser.add_argument(
        "--output", type=Path, default=_ETF_REGISTRY_SEED_PATH,
        help="输出 CSV 路径（默认 scripts/stock_index_seeds/etf_registry.csv）",
    )
    args = parser.parse_args(argv)

    try:
        print("[refresh_etf_registry] fetching exchange ETF list (Sina)...", flush=True)
        etfs = fetch_exchange_etf_codes()
        print(f"[refresh_etf_registry] got {len(etfs)} exchange ETFs", flush=True)
        if len(etfs) < _MIN_EXPECTED:
            print(
                f"[refresh_etf_registry] ERROR: only {len(etfs)} ETFs fetched "
                f"(< {_MIN_EXPECTED}); refusing to overwrite the seed with a "
                f"likely partial result.",
                file=sys.stderr,
            )
            return 2

        print("[refresh_etf_registry] fetching fund names (Eastmoney)...", flush=True)
        try:
            names = fetch_fund_names()
            print(f"[refresh_etf_registry] got {len(names)} fund names", flush=True)
        except (requests.RequestException, ValueError) as exc:
            names = {}
            print(f"[refresh_etf_registry] WARN: name table unavailable ({exc}); "
                  f"falling back to Sina short names", flush=True)

        rows = build_seed_rows(etfs, names)
        # Fail fast if the join produced nothing usable.
        if len(rows) < _MIN_EXPECTED:
            print(
                f"[refresh_etf_registry] ERROR: only {len(rows)} rows after join "
                f"(< {_MIN_EXPECTED}); not writing.",
                file=sys.stderr,
            )
            return 2

        # Validate against the generator's own rules before writing.
        entries = build_etf_entries_from_seed(rows)
        validate_etf_registry(entries)

        named = sum(1 for r in rows if names.get(r["canonical_code"].split(".")[0]))
        print(f"[refresh_etf_registry] built {len(rows)} rows "
              f"({named} with Eastmoney names)", flush=True)
        if args.test:
            for r in rows[:5]:
                print(f"    {r['canonical_code']}  {r['name_zh']}")
            print("[refresh_etf_registry] test mode: not writing")
            return 0

        write_seed_csv(rows, args.output)
        print(f"[refresh_etf_registry] wrote {len(rows)} rows -> {args.output}")
        return 0

    except requests.RequestException as exc:
        print(f"[refresh_etf_registry] ERROR: network failure: {exc}", file=sys.stderr)
        return 1
    except (ValueError, OSError) as exc:
        print(f"[refresh_etf_registry] ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
