import type React from 'react';

import { MultiPeriodChart } from './MultiPeriodChart';
import { StockQuoteHeader } from './StockQuoteHeader';

/**
 * 单只股票的行情面板：实时行情 + 多周期图表（分时 / 日 / 周 / 月 / 季 K + MACD / KDJ）。
 *
 * 这是「看一只股票」的唯一一份实现，股票查询页与全局行情弹窗都用它，所以两处的
 * 口径、刷新节奏、空态与报错表现天然一致。行情与图表走两条独立链路（自有数据源 /
 * 同花顺），任一条失败只影响自己那一块。
 */

/** 自动刷新间隔。盘中末根 K 线随行情变化，30s 兼顾实时性与上游压力。 */
export const STOCK_CHART_REFRESH_MS = 30_000;

type StockChartPanelProps = {
  /** 已校验过的 A 股 / 场内基金 6 位代码。 */
  code: string;
  /** `page` 带股票名与成交量/额，`modal` 的名字已在弹窗标题里。 */
  variant?: 'page' | 'modal';
  refreshIntervalMs?: number;
};

export const StockChartPanel: React.FC<StockChartPanelProps> = ({
  code,
  variant = 'page',
  refreshIntervalMs = STOCK_CHART_REFRESH_MS,
}) => (
  <div className="space-y-4">
    <section className="rounded-2xl border border-border bg-elevated/40 p-4">
      <StockQuoteHeader code={code} variant={variant} refreshIntervalMs={refreshIntervalMs} />
    </section>

    <section className="rounded-2xl border border-border bg-elevated/40 p-4">
      <MultiPeriodChart code={code} refreshIntervalMs={refreshIntervalMs} />
    </section>
  </div>
);
