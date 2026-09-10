#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""跑一轮资讯池采集，供 market-brief workflow 在常驻服务之外兜底。

用途：盘前/盘中/周末的消息面汇总要按时间窗回看，而各家快讯接口单次只返回最近一小段，
必须持续增量落库。常驻服务（--serve）有自己的调度器；没有常驻服务的部署（例如只用
GitHub Actions）靠这个脚本每小时跑一次。

退出码：全部源都失败时返回 1，让 workflow 能看见上游整体不可用；
部分失败沿用 fail-open 语义返回 0。
"""

from __future__ import annotations

import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services.intelligence_collector import run_collect_once  # noqa: E402


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    result = run_collect_once()
    print(json.dumps(result, ensure_ascii=False))

    if result.get("skipped"):
        print("NEWS_INTEL_COLLECT_ENABLED 未开启，跳过采集", file=sys.stderr)
        return 0
    if not result.get("ok"):
        return 1
    sources = int(result.get("sources") or 0)
    if sources and not int(result.get("succeeded") or 0):
        print(f"全部 {sources} 个资讯源采集失败", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
