import type React from 'react';
import { cn } from '../../utils/cn';

/**
 * 股票查询看板的共享展示组件。
 *
 * 数据全部来自百度财经透传接口，字段可能缺失或为占位符（"--"），格式化与空值
 * 兜底放在 ./format。
 */

type KeyValueGridProps = {
  items: Array<{ label: string; value: React.ReactNode; className?: string }>;
  columns?: 2 | 3 | 4;
  className?: string;
};

/** 概览顶部那种紧凑的「标签 + 数值」网格。 */
export const KeyValueGrid: React.FC<KeyValueGridProps> = ({ items, columns = 4, className }) => (
  <dl
    className={cn(
      'grid gap-x-6 gap-y-3',
      columns === 2 && 'grid-cols-2',
      columns === 3 && 'grid-cols-2 sm:grid-cols-3',
      columns === 4 && 'grid-cols-2 sm:grid-cols-3 lg:grid-cols-4',
      className,
    )}
  >
    {items.map((item) => (
      <div key={item.label} className="flex items-baseline justify-between gap-2">
        <dt className="shrink-0 text-xs text-muted-text">{item.label}</dt>
        <dd className={cn('text-right text-sm font-medium tabular-nums text-foreground', item.className)}>
          {item.value}
        </dd>
      </div>
    ))}
  </dl>
);

type DataTableProps = {
  header: string[];
  rows: React.ReactNode[][];
  className?: string;
  emptyHint?: string;
};

/**
 * 朴素表格。图表的 contrast WARN 需要「可见标签或表格视图」作为补偿，
 * 看板里凡是有图的区块都同时给出对应表格。
 */
export const DataTable: React.FC<DataTableProps> = ({ header, rows, className, emptyHint = '暂无数据' }) => {
  if (!rows.length) {
    return <p className="py-6 text-center text-sm text-muted-text">{emptyHint}</p>;
  }
  return (
    <div className={cn('overflow-x-auto', className)}>
      <table className="w-full min-w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-border">
            {header.map((cell) => (
              <th key={cell} className="px-3 py-2 text-left text-xs font-medium text-muted-text">
                {cell}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={rowIndex} className="border-b border-border/50 last:border-0">
              {row.map((cell, cellIndex) => (
                <td
                  key={cellIndex}
                  className={cn('px-3 py-2 tabular-nums text-foreground', cellIndex === 0 && 'text-secondary-text')}
                >
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

/** 图表图例：>=2 序列必须常驻，避免只用颜色承载身份。 */
export const ChartLegend: React.FC<{ items: Array<{ label: string; color: string; value?: string }> }> = ({ items }) => (
  <ul className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
    {items.map((item) => (
      <li key={item.label} className="flex items-center gap-1.5 text-xs text-secondary-text">
        <span aria-hidden className="h-0.5 w-3.5 rounded-full" style={{ background: item.color }} />
        <span>{item.label}</span>
        {item.value ? <span className="tabular-nums text-foreground">{item.value}</span> : null}
      </li>
    ))}
  </ul>
);
