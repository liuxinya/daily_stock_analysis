#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Refresh local stock autocomplete index assets.

Default flow:
1. Fetch Tushare stock lists into ``data/`` with ``--a-rk`` for A-share name correction.
2. Best-effort refresh of the on-exchange ETF registry seed from public listings
   (no Tushare token required); a failure here is non-fatal and keeps the existing seed.
3. Generate ``apps/dsa-web/public/stocks.index.json`` from CSV plus JP/KR seed rows
   and the ETF/index registries.
4. Copy the generated index to ``static/stocks.index.json`` for backend use.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
WEB_INDEX_PATH = REPO_ROOT / "apps" / "dsa-web" / "public" / "stocks.index.json"
STATIC_INDEX_PATH = REPO_ROOT / "static" / "stocks.index.json"


def _run(command: Sequence[str]) -> None:
    print(f"[refresh_stock_index] $ {' '.join(command)}", flush=True)
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    subprocess.run(command, cwd=REPO_ROOT, check=True, env=env)


def _has_tushare_token() -> bool:
    env_path = REPO_ROOT / ".env"
    try:
        from dotenv import load_dotenv  # type: ignore
    except ImportError:
        if env_path.is_file():
            for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                key, sep, value = line.partition("=")
                if sep and key.strip() == "TUSHARE_TOKEN" and value.strip().strip("'\""):
                    return True
        return bool(os.getenv("TUSHARE_TOKEN", "").strip())

    load_dotenv(env_path)
    return bool(os.getenv("TUSHARE_TOKEN", "").strip())


def _sync_static_index() -> None:
    if not WEB_INDEX_PATH.is_file():
        raise FileNotFoundError(f"generated Web index not found: {WEB_INDEX_PATH}")
    STATIC_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(WEB_INDEX_PATH, STATIC_INDEX_PATH)
    print(f"[refresh_stock_index] synced {WEB_INDEX_PATH} -> {STATIC_INDEX_PATH}", flush=True)


def _refresh_etf_seed() -> None:
    """Best-effort refresh of the ETF registry seed; never fatal.

    The seed is merged into the index by ``generate_index_from_csv.py``. Keeping
    it fresh here means the one-click flow also picks up newly listed ETFs, but a
    network failure must not break the (token-gated) stock-index refresh, so we
    only warn and fall back to the committed seed.
    """
    command = [sys.executable, "scripts/refresh_etf_registry.py"]
    print(f"[refresh_stock_index] $ {' '.join(command)}", flush=True)
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    try:
        result = subprocess.run(command, cwd=REPO_ROOT, env=env)
    except OSError as exc:
        print(f"[refresh_stock_index] WARN: ETF seed refresh skipped ({exc}); "
              f"using existing seed", file=sys.stderr)
        return
    if result.returncode != 0:
        print(f"[refresh_stock_index] WARN: ETF seed refresh exit "
              f"{result.returncode}; using existing seed", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="刷新股票自动补全索引")
    parser.add_argument(
        "--skip-fetch",
        action="store_true",
        help="跳过 Tushare 抓取，仅用现有 data/stock_list_*.csv 重新生成索引",
    )
    parser.add_argument(
        "--skip-etf",
        action="store_true",
        help="跳过场内 ETF 注册表刷新，直接使用现有 etf_registry.csv",
    )
    args = parser.parse_args(argv)

    try:
        if args.skip_fetch:
            print("[refresh_stock_index] skip Tushare fetch; using existing CSV files")
        else:
            if not _has_tushare_token():
                print(
                    "[refresh_stock_index] ERROR: missing TUSHARE_TOKEN. "
                    "Set it in .env or environment, or rerun with --skip-fetch.",
                    file=sys.stderr,
                )
                return 2
            _run([sys.executable, "scripts/fetch_tushare_stock_list.py", "--a-rk"])

        if args.skip_etf:
            print("[refresh_stock_index] skip ETF seed refresh; using existing seed")
        else:
            _refresh_etf_seed()

        _run([sys.executable, "scripts/generate_index_from_csv.py", "--source", "tushare"])
        _sync_static_index()

    except subprocess.CalledProcessError as exc:
        print(
            f"[refresh_stock_index] ERROR: command failed with exit code {exc.returncode}",
            file=sys.stderr,
        )
        return exc.returncode or 1
    except (OSError, RuntimeError) as exc:
        print(f"[refresh_stock_index] ERROR: {exc}", file=sys.stderr)
        return 1

    print("[refresh_stock_index] done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
