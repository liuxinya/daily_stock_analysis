/** 「行业资金」页面的展示格式化工具。 */

export const YI = 1e8;

/** 金额（元）→ 亿元字符串。 */
export function formatYiFromYuan(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return `${(value / YI).toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })} 亿`;
}

/** 已经是「亿元」的值 → 带符号字符串。 */
export function formatYi(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return value.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

/** 带正负号的金额，用于资金净额这类有方向的数字。 */
export function formatSignedYi(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  const sign = value > 0 ? '+' : '';
  return `${sign}${formatYi(value, digits)}`;
}

export function formatPct(value: number | null | undefined, digits = 2, signed = false): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  const sign = signed && value > 0 ? '+' : '';
  return `${sign}${value.toFixed(digits)}%`;
}

/** 0-1 的分位 → 百分数字符串。 */
export function formatPercentile(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return `${(value * 100).toFixed(digits)}%`;
}

export function formatWeight(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return `${(value * 100).toFixed(0)}%`;
}

export function formatScore(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return value.toFixed(digits);
}

export function formatIstTime(value: string | null | undefined): string {
  if (!value) return '—';
  // 后端给的是本地时间字符串，直接按字面展示，避免时区二次换算。
  return value.replace('T', ' ').slice(0, 19);
}

/**
 * 资金净额的方向色调。A 股口径（红涨绿跌）：正数（净流入）用红、负数（净流出）用绿，
 * 0 视为中性，避免满屏红绿。注意 text-danger 是红、text-success 是绿，这里是有意的
 * 「正红负绿」，不是写反了。
 */
export function inOutTone(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value) || value === 0) {
    return 'text-secondary-text';
  }
  return value > 0 ? 'text-danger' : 'text-success';
}

/** 复合分的判定色调。A 股口径：偏多用红、偏空用绿。 */
export function verdictTone(verdict: string): string {
  if (verdict === 'bullish') return 'text-danger';
  if (verdict === 'bearish') return 'text-success';
  return 'text-secondary-text';
}

export function verdictBorder(verdict: string): string {
  if (verdict === 'bullish') return 'border-danger/35';
  if (verdict === 'bearish') return 'border-success/35';
  return 'border-border/60';
}

export const STANCE_LABELS: Record<string, string> = {
  bullish: '偏多',
  neutral: '中性',
  bearish: '偏空',
};
