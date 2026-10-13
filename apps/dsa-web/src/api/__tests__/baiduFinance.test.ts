import { beforeEach, describe, expect, it, vi } from 'vitest';
import { baiduFinanceApi, isEtfCode, parseMarketData } from '../baiduFinance';

const { get } = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock('../index', () => ({
  default: { get },
}));

const BASE = '/api/v1/baidu-finance';
const lastCall = () => get.mock.calls.at(-1) as [string, { params: Record<string, string> }];

describe('isEtfCode', () => {
  it('把 51/52/56/58/15/16/18 开头的六位码判为 ETF', () => {
    for (const code of ['515230', '159852', '560050', '588000', '162605', '184801', '520010']) {
      expect(isEtfCode(code)).toBe(true);
    }
  });

  it('主板/创业板/科创板个股不是 ETF', () => {
    for (const code of ['600519', '000960', '300750', '688981', '002594']) {
      expect(isEtfCode(code)).toBe(false);
    }
  });

  it('非六位或含非数字的输入不是 ETF', () => {
    for (const code of ['5152', '5152300', 'hk00700', 'AAPL', '']) {
      expect(isEtfCode(code)).toBe(false);
    }
  });
});

describe('baiduFinanceApi ETF 分流', () => {
  beforeEach(() => {
    get.mockReset();
    get.mockResolvedValue({ data: { ResultCode: 0, Result: {} } });
  });

  it('ETF 日K 改走 getquotation（quotation key）并带 ETF 判别参数', async () => {
    await baiduFinanceApi.kline('515230');
    const [url, options] = lastCall();
    expect(url).toBe(`${BASE}/quotation`);
    expect(options.params).toMatchObject({
      srcid: '5353',
      code: '515230',
      query: '515230',
      financeType: 'etf',
      stock_type: 'ab',
      group: 'quotation_kline_ab',
      ktype: 'day',
    });
  });

  it('股票 日K 仍走 analysis-quotation，不带 ETF 判别参数', async () => {
    await baiduFinanceApi.kline('600519');
    const [url, options] = lastCall();
    expect(url).toBe(`${BASE}/analysis-quotation`);
    expect(options.params).toMatchObject({
      group: 'quotation_analysis_kline',
      code: '600519',
      market_type: 'ab',
    });
    expect(options.params.financeType).toBeUndefined();
  });

  it('ETF 资金流向带 finance_type/type = etf', async () => {
    await baiduFinanceApi.fundFlow('159852');
    const [url, options] = lastCall();
    expect(url).toBe(`${BASE}/fundflow`);
    expect(options.params).toMatchObject({ finance_type: 'etf', type: 'etf', code: '159852' });
  });

  it('股票 资金流向仍为 finance_type/type = stock', async () => {
    await baiduFinanceApi.fundFlow('600519');
    expect(lastCall()[1].params).toMatchObject({ finance_type: 'stock', type: 'stock' });
  });

  it('ETF 资讯 financeType = etf', async () => {
    await baiduFinanceApi.sentiment('515230');
    expect(lastCall()[1].params).toMatchObject({ financeType: 'etf', code: '515230' });
  });

  it('股票 资讯 financeType 保持 stock', async () => {
    await baiduFinanceApi.sentiment('600519');
    expect(lastCall()[1].params).toMatchObject({ financeType: 'stock' });
  });
});

describe('parseMarketData', () => {
  it('按 keys 下标解析分号/逗号 CSV；缺列的均线回落为 null', () => {
    const rows = parseMarketData({
      keys: ['time', 'open', 'close', 'high', 'low', 'volume', 'amount', 'ratio', 'ma5avgprice'],
      marketData: '2026-01-02,10,11,12,9,100,1000,1.5,10.5;2026-01-03,11,12,13,10,200,2000,2.0',
    });
    expect(rows).toHaveLength(2);
    expect(rows[0]).toMatchObject({ time: '2026-01-02', open: 10, close: 11, high: 12, low: 9, ma5: 10.5 });
    expect(rows[1].ma5).toBeNull();
  });
});