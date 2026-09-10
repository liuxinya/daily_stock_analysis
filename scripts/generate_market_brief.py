#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""到点就生成一份消息面汇总，供 market-brief workflow 在常驻服务之外兜底。

判重与时点判断都在 ``MarketBriefService.should_generate`` 里，因此这个脚本可以
被高频调用（例如每小时一次）而不会重复出稿。

退出码：生成失败返回 1；未到时点或已生成过属于正常跳过，返回 0。
"""

from __future__ import annotations

import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services.market_brief_service import run_brief_tick  # noqa: E402


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    result = run_brief_tick()
    print(json.dumps(result, ensure_ascii=False))

    if not result.get("ok"):
        print(f"消息面汇总生成失败: {result.get('error')}", file=sys.stderr)
        return 1
    if not result.get("generated"):
        print(f"本次跳过：{result.get('reason')}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
