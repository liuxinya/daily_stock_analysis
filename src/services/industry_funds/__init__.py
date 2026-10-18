# -*- coding: utf-8 -*-
"""行业资金（主力资金 / 现阶段方向）模块。

五因子复合信号：行业资金流、龙虎榜机构净买、ETF 份额变动、两融余额、估值分位。
数据源取舍与已知上游陷阱见 ``fetchers.py`` 的模块 docstring。
"""

from src.services.industry_funds.constants import (
    FLOW_DISCLAIMER,
    INVESTMENT_DISCLAIMER,
)

__all__ = ["FLOW_DISCLAIMER", "INVESTMENT_DISCLAIMER"]
