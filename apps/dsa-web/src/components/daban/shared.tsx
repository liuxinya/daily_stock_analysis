import type React from 'react';
import { CandlestickChart, Check, Minus, X } from 'lucide-react';
import { Tooltip } from '../common';
import { resolveStockChartCode, useStockChartStore } from '../../stores/stockChartStore';
import type { DabanCited, DabanPrinciple } from '../../types/daban';
import { cn } from '../../utils/cn';
import { principleLabel } from './format';

/** 条件核对的命中标记：命中 / 未命中 / 数据缺失。周期仪表与盘中雷达的门控卡共用。 */
export const HitIcon: React.FC<{ hit: boolean | null }> = ({ hit }) => {
  if (hit === true) return <Check className="h-3.5 w-3.5 text-danger" aria-label="命中" />;
  if (hit === false) return <X className="h-3.5 w-3.5 text-muted-text" aria-label="未命中" />;
  return <Minus className="h-3.5 w-3.5 text-muted-text" aria-label="数据缺失" />;
};

/** 代码 + 名称，点击打开全局行情 / K 线弹窗（与行业资金候选股同一写法）。 */
export const StockLink: React.FC<{ code: string; name: string; className?: string }> = ({ code, name, className }) => {
  const openStockChart = useStockChartStore((state) => state.open);
  const chartCode = resolveStockChartCode(code);
  if (!chartCode) {
    return (
      <span className={cn('inline-flex items-center gap-1.5', className)}>
        <span className="font-mono text-xs text-secondary-text">{code}</span>
        <span className="font-medium text-foreground">{name}</span>
      </span>
    );
  }
  return (
    <button
      type="button"
      onClick={() => openStockChart(chartCode, name)}
      aria-label={`查看 ${name}(${chartCode}) 的实时行情与 K 线`}
      className={cn(
        'group inline-flex items-center gap-1.5 rounded text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan/60',
        className
      )}
    >
      <span className="font-mono text-xs text-secondary-text group-hover:text-cyan">{chartCode}</span>
      <span className="font-medium text-foreground underline decoration-dotted underline-offset-4 group-hover:text-cyan">
        {name}
      </span>
      <CandlestickChart className="h-3 w-3 text-muted-text group-hover:text-cyan" aria-hidden="true" />
    </button>
  );
};

/** 理念出处标签：悬停显示原文摘句，让用户核对理由是否真是文章里说过的。 */
export const PrincipleTags: React.FC<{ ids: string[]; principles: Record<string, DabanPrinciple> }> = ({
  ids,
  principles,
}) => {
  if (!ids?.length) return null;
  return (
    <span className="inline-flex flex-wrap gap-1 align-middle">
      {ids.map((id) => {
        const item = principles[id];
        const isKobe = id.startsWith('K');
        return (
          <Tooltip
            key={id}
            focusable
            content={
              item ? (
                <span className="block max-w-xs text-left">
                  <span className="block font-medium">
                    {item.author} {item.ref}：{item.title}
                  </span>
                  <span className="mt-1 block text-secondary-text">「{item.quote}」</span>
                </span>
              ) : (
                id
              )
            }
          >
            <span
              className={cn(
                'inline-flex items-center rounded-md border px-1.5 py-px text-[10px] leading-4',
                isKobe ? 'border-purple/25 bg-purple/10 text-purple' : 'border-cyan/25 bg-cyan/10 text-cyan'
              )}
            >
              {id} {principleLabel(id, principles).split('·')[1] ?? ''}
            </span>
          </Tooltip>
        );
      })}
    </span>
  );
};

/** 一条带出处的理由。 */
export const CitedLine: React.FC<{
  item: DabanCited;
  principles: Record<string, DabanPrinciple>;
  bullet?: string;
  className?: string;
}> = ({ item, principles, bullet = '·', className }) => (
  <li className={cn('text-xs leading-relaxed text-foreground', className)}>
    <span className="mr-1 text-muted-text">{bullet}</span>
    {item.text} <PrincipleTags ids={item.principleIds} principles={principles} />
  </li>
);
