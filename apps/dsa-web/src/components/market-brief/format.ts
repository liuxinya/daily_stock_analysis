/**
 * 消息面汇总模块的取值与格式化工具。
 *
 * 与组件分文件：Vite 的 react-refresh 规则要求一个模块只导出组件。
 */

import type { MarketDirection, EvidenceStrength } from '../../api/marketBrief';

export const directionLabel = (direction: MarketDirection): string => {
  if (direction === 'bullish') return '利好';
  if (direction === 'bearish') return '利空';
  return '中性';
};

/** A 股习惯红涨绿跌，所以利好用 danger(红)、利空用 success(绿)。 */
export const directionBadgeVariant = (
  direction: MarketDirection,
): 'danger' | 'success' | 'default' => {
  if (direction === 'bullish') return 'danger';
  if (direction === 'bearish') return 'success';
  return 'default';
};

export const strengthLabel = (strength: EvidenceStrength): string => {
  if (strength === 'high') return '依据充分';
  if (strength === 'medium') return '依据一般';
  return '依据较弱';
};

export const importanceLabel = (importance: EvidenceStrength): string => {
  if (importance === 'high') return '重要';
  if (importance === 'medium') return '一般';
  return '次要';
};

export const toneSummary = (direction: MarketDirection): string => {
  if (direction === 'bullish') return '整体偏积极';
  if (direction === 'bearish') return '整体偏谨慎';
  return '整体中性';
};

/** "2026-09-06T21:00:00+08:00" -> "09-06 21:00" */
export const shortDateTime = (iso: string | null | undefined): string => {
  if (!iso) return '--';
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())} ${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`;
};
