import { describe, expect, it } from 'vitest';

import { pickHoverIndex } from '../hover';

describe('pickHoverIndex', () => {
  it('接受数字下标', () => {
    expect(pickHoverIndex({ activeTooltipIndex: 7 })).toBe(7);
    expect(pickHoverIndex({ activeTooltipIndex: 0 })).toBe(0);
  });

  it('接受字符串下标（recharts v3 的 TooltipIndex 是 string，只认 number 会导致读数行永不更新）', () => {
    expect(pickHoverIndex({ activeTooltipIndex: '12' })).toBe(12);
    expect(pickHoverIndex({ activeTooltipIndex: '0' })).toBe(0);
  });

  it('activeTooltipIndex 缺失时退到 activeIndex', () => {
    expect(pickHoverIndex({ activeIndex: 5 })).toBe(5);
    expect(pickHoverIndex({ activeIndex: '5' })).toBe(5);
  });

  it('isTooltipActive 为 false 时视为未悬停', () => {
    expect(pickHoverIndex({ activeTooltipIndex: 7, isTooltipActive: false })).toBeNull();
  });

  it('空值、负数与非数字一律视为未悬停', () => {
    for (const state of [
      undefined,
      null,
      {},
      { activeTooltipIndex: null },
      { activeTooltipIndex: -1 },
      { activeTooltipIndex: 'abc' },
      { activeTooltipIndex: '' },
      { activeTooltipIndex: 1.5 },
    ]) {
      expect(pickHoverIndex(state)).toBeNull();
    }
  });
});
