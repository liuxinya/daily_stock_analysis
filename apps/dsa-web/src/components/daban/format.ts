/** 「我是打板王」页面的格式化与配色工具（单独成文件：react-refresh 规则不允许组件文件导出非组件）。 */

import type { DabanAction, DabanPrinciple, DabanStyle } from '../../types/daban';

export function formatPct(value: number | null | undefined, digits = 1, signed = true): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  const sign = signed && value > 0 ? '+' : '';
  return `${sign}${value.toFixed(digits)}%`;
}

/** 0–1 比例 → 百分数。 */
export function formatRate(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return `${(value * 100).toFixed(digits)}%`;
}

export function formatDelta(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(digits)}`;
}

/** HHMMSS → HH:MM。 */
export function formatTime(value: string | null | undefined): string {
  if (!value || value.length < 4) return '—';
  return `${value.slice(0, 2)}:${value.slice(2, 4)}`;
}

/** A 股配色：涨红跌绿，必须同时带 +/- 号（红绿色弱用户分不清）。 */
export function changeTone(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value) || value === 0) return 'text-secondary-text';
  return value > 0 ? 'text-danger' : 'text-success';
}

/** 「越大越危险」的指标（跌停、大面、炸板率）增加时显示为风险色。 */
export function riskDeltaTone(value: number | null | undefined): string {
  if (value === null || value === undefined || value === 0) return 'text-secondary-text';
  return value > 0 ? 'text-success' : 'text-danger';
}

export const STAGE_TONES: Record<string, string> = {
  ice: 'border-cyan/30 bg-cyan/10 text-cyan',
  warm: 'border-warning/30 bg-warning/10 text-warning',
  ferment: 'border-danger/30 bg-danger/10 text-danger',
  climax: 'border-danger/50 bg-danger/20 text-danger',
  diverge: 'border-purple/30 bg-purple/10 text-purple',
  retreat: 'border-success/30 bg-success/10 text-success',
};

export const STAGE_DOTS: Record<string, string> = {
  ice: 'bg-cyan',
  warm: 'bg-warning',
  ferment: 'bg-danger/70',
  climax: 'bg-danger',
  diverge: 'bg-purple',
  retreat: 'bg-success',
};

export const ACTION_VARIANTS: Record<DabanAction, 'danger' | 'warning' | 'default'> = {
  actionable: 'danger',
  trial: 'warning',
  watch: 'default',
};

export const STYLE_ACCENTS: Record<DabanStyle, string> = {
  chen: 'border-cyan/40',
  kobe: 'border-purple/40',
};

export const STYLE_TEXT: Record<DabanStyle, string> = {
  chen: 'text-cyan',
  kobe: 'text-purple',
};

export function gradeTone(grade: string): string {
  switch (grade) {
    case 'S':
      return 'text-danger';
    case 'A':
      return 'text-warning';
    case 'B':
      return 'text-cyan';
    default:
      return 'text-secondary-text';
  }
}

export function principleLabel(id: string, principles: Record<string, DabanPrinciple>): string {
  const item = principles[id];
  return item ? `${item.author}·${item.title}` : id;
}
