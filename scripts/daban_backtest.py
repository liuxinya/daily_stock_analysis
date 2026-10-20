# -*- coding: utf-8 -*-
"""「我是打板王」首板 / 一进二历史回测。

用法：
    python scripts/daban_backtest.py                      # 同花顺可回溯的全部区间（约一年）
    python scripts/daban_backtest.py --start 2026-04-01 --end 2026-10-20

数据缓存在 data/daban_backtest/（已在 .gitignore），重跑只补新日期。
报告写到 data/daban_backtest/report/report.md 与 report.json。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> int:
    parser = argparse.ArgumentParser(description="打板王首板 / 一进二历史回测")
    parser.add_argument("--start", default=(date.today() - timedelta(days=365)).isoformat())
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--cache-dir", default=os.path.join("data", "daban_backtest"))
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from src.services.daban.backtest import run_backtest

    report = run_backtest(args.start, args.end, cache_dir=args.cache_dir, workers=args.workers)
    touch, o12, zb = report["touch"]["all"], report["o12"]["all"], report["broken"]["all"]
    print(f"区间 {report['range'][0]} ~ {report['range'][1]}，{report['trading_days']} 个交易日")
    print(f"首板打板 {touch['n']} 例：封住率 {touch['sealed']:.1%}，次日开盘卖均值 {touch['open']['mean']}%")
    print(
        f"一进二 {o12['n']} 例：可买 {o12['buyable']:.1%}，买后封住 {o12['sealed_given_buy']:.1%}，"
        f"次日开盘卖均值 {o12['open']['mean']}%"
    )
    print(f"炸板次日 {zb['n']} 例：次日涨停 {zb['sealed']:.1%}，次日收盘均值 {zb['close']['mean']}%")
    print(f"报告：{os.path.join(args.cache_dir, 'report', 'report.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
