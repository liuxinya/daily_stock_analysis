# -*- coding: utf-8 -*-
"""「我是打板王」历史回测与月度重标。

用法：
    python scripts/daban_backtest.py                      # 首板 / 一进二回测：同花顺可回溯的全部区间（约一年）
    python scripts/daban_backtest.py --start 2026-04-01 --end 2026-10-20
    python scripts/daban_backtest.py --mode recommend     # 推荐计分表月度重标：输出建议变更清单（不改规则）
    python scripts/daban_backtest.py --mode intraday      # 盘中因子评估：用 daban_radar_events 留档检验第三层因子

数据缓存在 data/daban_backtest/（已在 .gitignore），重跑只补新日期。``--mode recommend`` 先跑一遍默认模式把缓存
补到最新再重标。报告写到 data/daban_backtest/report/（report.md / recommend.md / intraday.json）。
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
    parser.add_argument("--mode", choices=("touch", "recommend", "intraday"), default="touch")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.mode == "recommend":
        return _recommend(args)
    if args.mode == "intraday":
        return _intraday(args)
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


def _recommend(args) -> int:
    from src.config import get_config
    from src.repositories.daban_repo import DabanRepository
    from src.services.daban.backtest import run_backtest
    from src.services.daban.constants import load_rules
    from src.services.daban.rec_backtest import run_recommend

    # 先跑一遍默认模式把缓存补到最新（空缓存时从头建；已缓存的日期不重取）。
    run_backtest(args.start, args.end, cache_dir=args.cache_dir, workers=args.workers)
    rules = load_rules(get_config())
    try:
        live_rows = DabanRepository().recent_recommendations(limit_days=60)
    except Exception as exc:  # 没有库也能只跑回测部分
        logging.warning("读取实盘推荐失败，观察期复核跳过：%s", exc)
        live_rows = []
    report = run_recommend(args.cache_dir, rules=rules, live_rows=live_rows)
    print(f"回测事件 {report['events']} 个，区间 {' ~ '.join(report.get('range') or [])}")
    print(f"建议变更 {len(report['changes'])} 项；观察期实盘复核：{report['observe_live'].get('verdict')}")
    print(f"报告：{os.path.join(args.cache_dir, 'report', 'recommend.md')}（变更需人工确认后写进 DABAN_RULES_FILE）")
    return 0


def _intraday(args) -> int:
    import json

    from src.repositories.daban_repo import DabanRepository
    from src.services.daban import rec_features as rf
    from src.services.daban.kline import limit_pct
    from src.services.daban.rec_backtest import evaluate_intraday
    from src.services.daban.recommend import fetch_bars_raw

    repo = DabanRepository()
    seen = []
    for day in _radar_days(repo):
        for event in repo.radar_events(day, kinds=["seen"]):
            if (event.get("data") or {}).get("hot") and (event.get("data") or {}).get("status") != "sealed" and event.get("price"):
                seen.append(event)
    codes = sorted({e["code"] for e in seen})
    bars = {}
    for code in codes:
        try:
            bars[code] = rf.make_bars(fetch_bars_raw(code, limit=60))
        except Exception as exc:
            logging.warning("%s 日 K 取数失败：%s", code, exc)
    outcomes = {}
    for event in seen:
        k = bars.get(event["code"])
        i0 = k["ix"].get(event["trade_date"].replace("-", "")) if k else None
        if i0 is None or i0 < 1:
            continue
        result = rf.exit_returns(k, i0, float(event["price"]), limit_pct(event["code"]), live=True)
        if result.get("h1") is not None:
            outcomes[(event["trade_date"], event["code"])] = result["h1"]
    report = evaluate_intraday(seen, outcomes)
    out = os.path.join(args.cache_dir, "report", "intraday.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
    print(f"留档候选 {report['samples']} 个 / {report['days']} 个交易日（入选要求 ≥{report['min_n']} 个、≥{report['min_days']} 天）")
    for item in report["factors"]:
        if item["candidate"]:
            print(f"  候选计分项：{item['factor']} {item['cell']} 超额 {item['excess']:+.2f}（{item['n']} 个 / {item['days']} 天）")
    print(f"报告：{out}（升为计分项需人工确认）")
    return 0


def _radar_days(repo):
    from sqlalchemy import select

    from src.storage import DabanRadarEvent

    with repo.db.get_session() as session:
        return sorted(r[0] for r in session.execute(select(DabanRadarEvent.trade_date).distinct()).all())


if __name__ == "__main__":
    raise SystemExit(main())
