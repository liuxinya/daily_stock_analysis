import { describe, expect, it } from 'vitest';

import { absoluteTime, timeAgo } from '../format';

const NOW = new Date('2026-09-07T20:00:00+08:00').getTime();
const minutesAgo = (minutes: number) => new Date(NOW - minutes * 60 * 1000).toISOString();

describe('timeAgo', () => {
  it('returns an empty string for missing or unparsable input', () => {
    expect(timeAgo(null, NOW)).toBe('');
    expect(timeAgo(undefined, NOW)).toBe('');
    expect(timeAgo('not-a-date', NOW)).toBe('');
  });

  it('collapses anything under a minute into 刚刚', () => {
    expect(timeAgo(minutesAgo(0), NOW)).toBe('刚刚');
    expect(timeAgo(minutesAgo(0.5), NOW)).toBe('刚刚');
  });

  it('treats slightly future timestamps as 刚刚 rather than negative values', () => {
    // 各平台服务器时钟不完全同步，上游偶尔会给出略超前的时间
    expect(timeAgo(minutesAgo(-3), NOW)).toBe('刚刚');
  });

  it('formats minutes, hours and days', () => {
    expect(timeAgo(minutesAgo(1), NOW)).toBe('1分钟前');
    expect(timeAgo(minutesAgo(59), NOW)).toBe('59分钟前');
    expect(timeAgo(minutesAgo(60), NOW)).toBe('1小时前');
    expect(timeAgo(minutesAgo(60 * 23), NOW)).toBe('23小时前');
    expect(timeAgo(minutesAgo(60 * 24), NOW)).toBe('1天前');
    expect(timeAgo(minutesAgo(60 * 24 * 6), NOW)).toBe('6天前');
  });

  it('falls back to an absolute date beyond a week', () => {
    const result = timeAgo(minutesAgo(60 * 24 * 30), NOW);
    expect(result).not.toContain('前');
    expect(result).toMatch(/2026/);
  });

  it('respects the timezone offset carried by the backend timestamp', () => {
    // 同一时刻的两种写法必须得到同样的相对时间
    const beijing = '2026-09-07T19:00:00+08:00';
    const utc = '2026-09-07T11:00:00+00:00';
    expect(timeAgo(beijing, NOW)).toBe(timeAgo(utc, NOW));
    expect(timeAgo(beijing, NOW)).toBe('1小时前');
  });
});

describe('absoluteTime', () => {
  it('returns an empty string for missing input', () => {
    expect(absoluteTime(null)).toBe('');
    expect(absoluteTime('nope')).toBe('');
  });

  it('renders a full local datetime', () => {
    expect(absoluteTime('2026-09-07T19:13:00+08:00')).toMatch(/2026/);
  });
});
