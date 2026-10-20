# -*- coding: utf-8 -*-
"""理念条目库的约束：规则里引用的每个 id 都在库里；摘句必须是原文原话。

原文（``92科比.md`` / ``陈小群.md``）不入库，本地有这两个文件时才做逐字核对。
"""

from __future__ import annotations

import os
import re

import pytest

from src.services.daban import constants
from src.services.daban.principles import PRINCIPLES

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULE_FILES = ("patterns.py", "scoring.py", "styles.py", "constants.py", "analyzer.py")
_ID = re.compile(r'"([KC]\d{2})"')


def _cited_ids():
    ids = set()
    for name in RULE_FILES:
        with open(os.path.join(ROOT, "src", "services", "daban", name), encoding="utf-8") as handle:
            ids.update(_ID.findall(handle.read()))
    return ids


def test_every_cited_id_exists():
    assert _cited_ids() - set(PRINCIPLES) == set()


def test_ids_match_authors():
    for pid, item in PRINCIPLES.items():
        assert item["author"] == ("92科比" if pid.startswith("K") else "陈小群"), pid


@pytest.mark.parametrize("author,filename", [("92科比", "92科比.md"), ("陈小群", "陈小群.md")])
def test_quotes_are_verbatim(author, filename):
    path = os.path.join(ROOT, filename)
    if not os.path.exists(path):
        pytest.skip(f"本地没有原文 {filename}")
    with open(path, encoding="utf-8") as handle:
        text = re.sub(r"\s+", "", handle.read())
    for pid, item in PRINCIPLES.items():
        if item["author"] != author:
            continue
        for segment in item["quote"].split("……"):
            assert re.sub(r"\s+", "", segment.strip("。；，")) in text, f"{pid} 摘句不是原文：{segment}"


def test_chen_gates_never_enable_catch_up_or_sub_new():
    gates = constants.DEFAULT_RULES["gates"]["chen"]
    assert all(v is None for v in gates["M3"].values())
    assert all(v is None for v in gates["M7"].values())
