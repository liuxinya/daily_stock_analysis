import type React from 'react';
import type { FundFlowGroup, FundFlowSpread } from '../../api/baiduFinance';
import { DataTable } from './shared';
import { text, toNumber, trendClass, withSign } from './format';

/**
 * 资金分布：按单量档位的流入/流出对比。
 *
 * 原站用 8 段环形图表达（4 档 × 进出），但环形图同时混两个极性会让「净额」
 * 这个真正的问题变得难读。这里换成对称的双向条：流出向左、流入向右，中间
 * 是中性基线 —— 极性即位置，颜色只是补充。
 *
 * 红绿在 deutan 下 ΔE≈8，所以每一侧都常驻数值、百分比与「流入/流出」文案，
 * 并且下方给出等价表格；去掉颜色也能读。
 */

const TIERS: Array<{ key: keyof FundFlowSpread; label: string }> = [
  { key: 'superGrp', label: '特大单' },
  { key: 'largeGrp', label: '大单' },
  { key: 'mediumGrp', label: '中单' },
  { key: 'littleGrp', label: '小单' },
];

type Row = {
  label: string;
  inValue: number;
  outValue: number;
  inRate: string;
  outRate: string;
  net: string;
};

export const FundFlowPanel: React.FC<{ spread?: FundFlowSpread }> = ({ spread }) => {
  if (!spread) {
    return <p className="py-8 text-center text-sm text-muted-text">暂无资金流向数据</p>;
  }

  const unit = text(spread.unit, '亿');
  const rows: Row[] = TIERS.map((tier) => {
    const group = (spread[tier.key] ?? {}) as FundFlowGroup;
    return {
      label: tier.label,
      inValue: toNumber(group.turnoverIn),
      outValue: toNumber(group.turnoverOut),
      inRate: text(group.turnoverInRate),
      outRate: text(group.turnoverOutRate),
      net: text(group.netTurnover),
    };
  });

  const peak = Math.max(1, ...rows.flatMap((row) => [row.inValue, row.outValue]));
  const pct = (value: number) => `${Math.min(100, (value / peak) * 100)}%`;

  const main = spread.todayMainFlow;

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {[
          { label: `流入合计（${unit}）`, value: text(spread.turnoverInTotal), cls: 'text-[var(--chart-up)]' },
          { label: `流出合计（${unit}）`, value: text(spread.turnoverOutTotal), cls: 'text-[var(--chart-down)]' },
          { label: `净额（${unit}）`, value: withSign(spread.turnoverNetTotal), cls: trendClass(spread.turnoverNetTotal) },
          { label: `主力净额（${unit}）`, value: withSign(spread.mainNetTotal), cls: trendClass(spread.mainNetTotal) },
        ].map((item) => (
          <div key={item.label} className="rounded-2xl border border-border bg-elevated/50 px-3 py-2.5">
            <p className="text-xs text-muted-text">{item.label}</p>
            <p className={`mt-1 text-lg font-semibold tabular-nums ${item.cls}`}>{item.value}</p>
          </div>
        ))}
      </div>

      <div className="space-y-3">
        <div className="flex items-center justify-between text-xs text-secondary-text">
          <span className="flex items-center gap-1.5">
            <span aria-hidden className="h-2 w-2 rounded-sm bg-[var(--chart-down)]" />
            流出
          </span>
          <span className="text-muted-text">单位：{unit}</span>
          <span className="flex items-center gap-1.5">
            流入
            <span aria-hidden className="h-2 w-2 rounded-sm bg-[var(--chart-up)]" />
          </span>
        </div>

        {rows.map((row) => (
          <div key={row.label} className="grid grid-cols-[1fr_auto_1fr] items-center gap-3">
            <div className="flex items-center justify-end gap-2">
              <span className="shrink-0 text-xs tabular-nums text-secondary-text">
                {row.outValue.toFixed(2)}
                <span className="ml-1 text-muted-text">{row.outRate}</span>
              </span>
              <div className="h-4 w-full max-w-[42%] grow overflow-hidden rounded-l-md bg-hover/40" dir="rtl">
                <div
                  className="h-full rounded-l-md bg-[var(--chart-down)]"
                  style={{ width: pct(row.outValue) }}
                />
              </div>
            </div>

            <span className="w-14 text-center text-xs font-medium text-foreground">{row.label}</span>

            <div className="flex items-center gap-2">
              <div className="h-4 w-full max-w-[42%] grow overflow-hidden rounded-r-md bg-hover/40">
                <div
                  className="h-full rounded-r-md bg-[var(--chart-up)]"
                  style={{ width: pct(row.inValue) }}
                />
              </div>
              <span className="shrink-0 text-xs tabular-nums text-secondary-text">
                {row.inValue.toFixed(2)}
                <span className="ml-1 text-muted-text">{row.inRate}</span>
              </span>
            </div>
          </div>
        ))}
      </div>

      {main ? (
        <p className="text-sm text-secondary-text">
          主力今日流入 <span className="tabular-nums text-[var(--chart-up)]">{text(main.mainIn)}</span>
          ，流出 <span className="tabular-nums text-[var(--chart-down)]">{text(main.mainOut)}</span>
          ，净额 <span className={`tabular-nums ${trendClass(main.mainNetIn)}`}>{withSign(main.mainNetIn)}</span>
          <span className="ml-1 text-muted-text">（{unit}）</span>
        </p>
      ) : null}

      {spread.analysis?.content ? (
        <div className="rounded-2xl border border-border bg-elevated/40 px-4 py-3">
          <p className="text-sm text-foreground">{spread.analysis.content}</p>
          {spread.analysis.list?.length ? (
            <ul className="mt-2 flex flex-wrap gap-x-6 gap-y-1">
              {spread.analysis.list.map((item, index) => (
                <li key={index} className="text-xs text-muted-text">
                  {text(item.desc)}
                  <span className="ml-1 tabular-nums text-foreground">{text(item.content)}</span>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      <details className="rounded-2xl border border-border">
        <summary className="cursor-pointer px-4 py-2.5 text-sm text-secondary-text">表格视图</summary>
        <div className="px-2 pb-2">
          <DataTable
            header={['档位', `流入(${unit})`, '流入占比', `流出(${unit})`, '流出占比', `净额(${unit})`]}
            rows={rows.map((row) => [
              row.label,
              row.inValue.toFixed(2),
              row.inRate,
              row.outValue.toFixed(2),
              row.outRate,
              <span className={trendClass(row.net)}>{withSign(row.net)}</span>,
            ])}
          />
        </div>
      </details>

      {spread.updateTime ? (
        <p className="text-xs text-muted-text">更新于 {spread.updateTime}</p>
      ) : null}
    </div>
  );
};
