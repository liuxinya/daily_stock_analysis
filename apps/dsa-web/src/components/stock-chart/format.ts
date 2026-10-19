/**
 * 股票查询看板的取值与格式化工具。
 *
 * 与组件分文件：Vite 的 react-refresh 规则要求一个模块只导出组件，
 * 混着导出普通函数会让热更新退化成整页刷新。
 */

/** 上游用 "--" / "" 表示无数据。 */
export const isBlank = (value: unknown): boolean =>
  value === null
  || value === undefined
  || value === ''
  || value === '--'
  || value === '-';

export const text = (value: unknown, fallback = '--'): string =>
  isBlank(value) ? fallback : String(value);

/** 解析上游的带符号字符串（"+0.14" / "-0.97" / "6.16%"）。 */
export const toNumber = (value: unknown): number => {
  if (typeof value === 'number') return Number.isFinite(value) ? value : 0;
  if (typeof value !== 'string') return 0;
  const parsed = Number(value.replace(/[+%,\s]/g, ''));
  return Number.isFinite(parsed) ? parsed : 0;
};

/**
 * 涨跌方向 -> 语义色（A 股红涨绿跌）。
 * 颜色只是辅助：调用处必须同时保留正负号或文案，deutan 下红绿 ΔE≈8。
 */
export const trendClass = (value: unknown): string => {
  const n = toNumber(value);
  if (n > 0) return 'text-[var(--chart-up)]';
  if (n < 0) return 'text-[var(--chart-down)]';
  return 'text-secondary-text';
};

/** 给数字补上显式正号，保证正负在无色环境下也可读。 */
export const withSign = (value: unknown, suffix = ''): string => {
  if (isBlank(value)) return '--';
  const raw = String(value).trim();
  if (raw.startsWith('+') || raw.startsWith('-')) return `${raw}${suffix}`;
  const n = toNumber(raw);
  if (n > 0) return `+${raw}${suffix}`;
  return `${raw}${suffix}`;
};

/** 成交量 / 成交额的中文量级缩写，K 线图与指标副图共用。 */
export const formatVolume = (value: number): string => {
  if (!Number.isFinite(value) || value === 0) return '--';
  if (Math.abs(value) >= 1e8) return `${(value / 1e8).toFixed(2)}亿`;
  if (Math.abs(value) >= 1e4) return `${(value / 1e4).toFixed(1)}万`;
  return String(value);
};
