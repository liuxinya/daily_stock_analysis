import { beforeEach, describe, expect, it, vi } from 'vitest';

import { chartApi } from '../chart';

const { get } = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock('../index', () => ({
  default: { get },
}));

const lastCall = () => get.mock.calls.at(-1) as [string, { params: Record<string, unknown> }];

describe('chartApi.kline', () => {
  beforeEach(() => {
    get.mockReset();
    get.mockResolvedValue({ data: { code: '002594', rows: [], today_source: null } });
  });

  it('缺省取日 K 前复权 250 根，不带 indicators 与 with_today', async () => {
    await chartApi.kline('002594');
    const [url, options] = lastCall();
    expect(url).toBe('/api/v1/chart/kline');
    expect(options.params).toEqual({
      code: '002594',
      period: 'day',
      adjust: 'qfq',
      limit: 250,
    });
  });

  it('指标按逗号拼接，with_today 显式传入时才出现', async () => {
    await chartApi.kline('002594', {
      period: 'week',
      adjust: 'none',
      limit: 60,
      indicators: ['ma', 'kdj'],
      withToday: false,
    });
    expect(lastCall()[1].params).toEqual({
      code: '002594',
      period: 'week',
      adjust: 'none',
      limit: 60,
      indicators: 'ma,kdj',
      with_today: false,
    });
  });

  it('空指标数组不发 indicators 参数（让后端用缺省值）', async () => {
    await chartApi.kline('002594', { indicators: [] });
    expect(lastCall()[1].params).not.toHaveProperty('indicators');
  });

  it('响应的 snake_case 字段转成 camelCase', async () => {
    get.mockResolvedValue({
      data: {
        code: '002594',
        name: '比亚迪',
        period: 'day',
        adjust: 'qfq',
        indicators: ['ma'],
        today_source: 'snapshot',
        listed_since: '20110630',
        trading_hours: '0930-1130,1300-1500',
        rows: [{ time: '20261019', close: 83.5, turnover_rate: 0.6, partial: true }],
        source: 'ths',
      },
    });
    const result = await chartApi.kline('002594');
    expect(result.todaySource).toBe('snapshot');
    expect(result.listedSince).toBe('20110630');
    expect(result.tradingHours).toBe('0930-1130,1300-1500');
    expect(result.rows[0].turnoverRate).toBe(0.6);
    expect(result.rows[0].partial).toBe(true);
  });
});

describe('chartApi.timeshare', () => {
  beforeEach(() => {
    get.mockReset();
  });

  it('只带 code，并把分时字段转成 camelCase', async () => {
    get.mockResolvedValue({
      data: {
        code: '002594',
        trade_date: '20261019',
        prev_close: 82.72,
        trade_times: ['0930-1130', '1300-1500'],
        is_trading: true,
        suspended: false,
        points: [{ time: '0930', price: 82.5, avg_price: 82.5, volume: 56000, amount: 4620000 }],
        source: 'ths',
      },
    });
    const result = await chartApi.timeshare('002594');
    expect(lastCall()[0]).toBe('/api/v1/chart/timeshare');
    expect(lastCall()[1].params).toEqual({ code: '002594' });
    expect(result.tradeDate).toBe('20261019');
    expect(result.prevClose).toBe(82.72);
    expect(result.isTrading).toBe(true);
    expect(result.points[0].avgPrice).toBe(82.5);
  });
});
