/**
 * CCTV 新闻模块的取值与格式化工具。
 *
 * 与组件分文件：Vite 的 react-refresh 规则要求一个模块只导出组件，
 * 混着导出普通函数会让热更新退化成整页刷新。
 */

import type { EvidenceStrength, MarketDirection } from '../../api/cctvNews';

const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];

/** "2026-08-25" -> "08-25"，避免列表里重复出现年份。 */
export const shortDate = (iso: string | null | undefined): string => {
  if (!iso) return '--';
  const parts = iso.split('-');
  return parts.length === 3 ? `${parts[1]}-${parts[2]}` : iso;
};

/** "2026-08-25" -> "周一"。按日期字符串构造，避免时区把日期推前一天。 */
export const weekdayLabel = (iso: string | null | undefined): string => {
  if (!iso) return '';
  const [year, month, day] = iso.split('-').map(Number);
  if (!year || !month || !day) return '';
  const date = new Date(Date.UTC(year, month - 1, day));
  return WEEKDAYS[date.getUTCDay()] ?? '';
};

export const formatDateTime = (iso: string | null | undefined): string => {
  if (!iso) return '--';
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleString('zh-CN', { hour12: false });
};

/**
 * 方向 -> 文案。
 *
 * 颜色只是辅助：调用处必须同时展示文案，红绿在色觉障碍下区分度很低。
 */
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

export const toneSummary = (direction: MarketDirection): string => {
  if (direction === 'bullish') return '整体偏积极';
  if (direction === 'bearish') return '整体偏谨慎';
  return '整体中性';
};
