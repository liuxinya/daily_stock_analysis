import { beforeEach, describe, expect, it } from 'vitest';

import { canOpenStockChart, useStockChartStore } from '../stockChartStore';

describe('stockChartStore', () => {
  beforeEach(() => {
    useStockChartStore.setState({ target: null });
  });

  it('接受 A 股 6 位码并归一化大小写与空白', () => {
    useStockChartStore.getState().open('  603533 ', '掌阅科技');
    expect(useStockChartStore.getState().target).toEqual({ code: '603533', name: '掌阅科技' });
  });

  it('接受带交易所前缀的写法', () => {
    useStockChartStore.getState().open('sz002594', '比亚迪');
    expect(useStockChartStore.getState().target?.code).toBe('002594');
  });

  it('名称缺失时退回用代码，避免弹窗标题空着', () => {
    useStockChartStore.getState().open('510300', '   ');
    expect(useStockChartStore.getState().target).toEqual({ code: '510300', name: '510300' });
  });

  it.each([null, undefined, '', 'AAPL', '00700', 'HK00700', '7203.T', 'LEADER::宁德时代', '－－'])(
    '非 A 股 6 位码（%s）静默忽略，不打开弹窗',
    (code) => {
      useStockChartStore.getState().open(code as string | null, '随便');
      expect(useStockChartStore.getState().target).toBeNull();
    },
  );

  it('close 清空标的', () => {
    useStockChartStore.getState().open('603533', '掌阅科技');
    useStockChartStore.getState().close();
    expect(useStockChartStore.getState().target).toBeNull();
  });

  it('canOpenStockChart 与 open 的放行口径一致', () => {
    for (const code of ['603533', '002594', '510300', 'SH600519', '600519.SH']) {
      expect(canOpenStockChart(code)).toBe(true);
    }
    for (const code of [null, undefined, '', 'AAPL', '00700', 'LEADER::x']) {
      expect(canOpenStockChart(code)).toBe(false);
    }
  });
});
