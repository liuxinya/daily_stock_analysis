import type React from 'react';
import { useMemo, useState } from 'react';
import type { FinanceSheet } from '../../api/baiduFinance';
import { cn } from '../../utils/cn';
import { text, trendClass } from './format';

/**
 * 财务：主要指标 / 利润表 / 资产负债表 / 现金流量表 + 主营构成。
 *
 * 上游 chartInfo[].body 每行是 [期间, 值, 同比, 值, 同比, …]，与 header 里的
 * 指标名按顺序一一对应。财报的习惯读法是「指标为行、期间为列」，所以这里做一次
 * 转置，而不是照搬上游的行列方向。
 */

type SheetKey = 'mainSheet' | 'profitSheetV2' | 'balanceSheetV2' | 'cashFlowSheetV2';

const SHEETS: Array<{ key: SheetKey; label: string }> = [
  { key: 'mainSheet', label: '主要指标' },
  { key: 'profitSheetV2', label: '利润表' },
  { key: 'balanceSheetV2', label: '资产负债表' },
  { key: 'cashFlowSheetV2', label: '现金流量表' },
];

type MainOpEntry = {
  title?: string;
  income?: string;
  incomeUnit?: string;
  header?: string[];
  body?: Array<Record<string, string>>;
};

type Cell = { value: string; yoy: string | null };

/** 把 [期间, 值, 同比, …] 的行结构转成 metrics × periods 的矩阵。 */
const transpose = (header: string[], body: string[][]) => {
  const periods = body.map((row) => row[0] ?? '');
  const metrics = header.map((name, metricIndex) => ({
    name,
    cells: body.map((row): Cell => ({
      value: row[1 + metricIndex * 2] ?? '--',
      yoy: row[2 + metricIndex * 2] ?? null,
    })),
  }));
  return { periods, metrics };
};

export const FinanceTab: React.FC<{
  sheets?: Partial<Record<SheetKey, FinanceSheet>>;
  mainOperation?: { title?: string; list?: MainOpEntry[] };
}> = ({ sheets, mainOperation }) => {
  const [active, setActive] = useState<SheetKey>('mainSheet');
  const available = SHEETS.filter((sheet) => sheets?.[sheet.key]?.chartInfo?.length);
  const current = sheets?.[active] ?? sheets?.[available[0]?.key ?? 'mainSheet'];
  const block = current?.chartInfo?.[0];

  const table = useMemo(() => {
    if (!block?.header?.length || !block?.body?.length) return null;
    return transpose(block.header, block.body);
  }, [block]);

  const latestOp = mainOperation?.list?.[0];

  return (
    <div className="space-y-4">
      <section className="rounded-2xl border border-border bg-elevated/40 p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap gap-1.5">
            {(available.length ? available : SHEETS).map((sheet) => (
              <button
                key={sheet.key}
                type="button"
                onClick={() => setActive(sheet.key)}
                className={cn(
                  'rounded-full border px-3 py-1 text-xs transition-colors',
                  active === sheet.key
                    ? 'border-[hsl(var(--primary))] bg-[hsl(var(--primary))]/10 text-[hsl(var(--primary))]'
                    : 'border-border text-secondary-text hover:text-foreground',
                )}
              >
                {sheet.label}
              </button>
            ))}
          </div>
          {current?.unit ? <span className="text-xs text-muted-text">单位：{current.unit}</span> : null}
        </div>

        {table ? (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr className="border-b border-border">
                  <th className="sticky left-0 bg-elevated px-3 py-2 text-left text-xs font-medium text-muted-text">
                    指标
                  </th>
                  {table.periods.map((period) => (
                    <th key={period} className="px-3 py-2 text-right text-xs font-medium text-muted-text">
                      {period}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {table.metrics.map((metric) => (
                  <tr key={metric.name} className="border-b border-border/50 last:border-0">
                    <td className="sticky left-0 bg-elevated px-3 py-2 text-secondary-text">{metric.name}</td>
                    {metric.cells.map((cell, index) => (
                      <td key={index} className="px-3 py-2 text-right tabular-nums text-foreground">
                        {text(cell.value)}
                        {cell.yoy && cell.yoy !== '--' ? (
                          <span className={cn('ml-1.5 text-xs', trendClass(cell.yoy))}>{cell.yoy}%</span>
                        ) : null}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="py-8 text-center text-sm text-muted-text">暂无财务数据</p>
        )}
        <p className="mt-2 text-xs text-muted-text">数值后的小字为同比变动</p>
      </section>

      {latestOp ? (
        <section className="rounded-2xl border border-border bg-elevated/40 p-4">
          <div className="mb-3 flex items-baseline justify-between gap-3">
            <h3 className="text-sm font-semibold text-foreground">
              {text(mainOperation?.title, '主营构成')}
              <span className="ml-2 text-xs font-normal text-muted-text">{text(latestOp.title)}</span>
            </h3>
            {latestOp.income ? (
              <span className="text-xs text-muted-text">营收 {latestOp.income}</span>
            ) : null}
          </div>
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr className="border-b border-border">
                  {(latestOp.header ?? []).map((cell) => (
                    <th key={cell} className="px-3 py-2 text-left text-xs font-medium text-muted-text">
                      {cell}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(latestOp.body ?? []).map((row, index) => (
                  <tr key={index} className="border-b border-border/50 last:border-0">
                    {Object.values(row).map((cell, cellIndex) => (
                      <td key={cellIndex} className="px-3 py-2 tabular-nums text-foreground">
                        {text(cell)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
    </div>
  );
};
