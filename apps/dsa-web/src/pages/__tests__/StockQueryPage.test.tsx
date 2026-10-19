import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { RouterProvider, createMemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import StockQueryPage from '../StockQueryPage';

const { getQuote, kline, timeshare } = vi.hoisted(() => ({
  getQuote: vi.fn(),
  kline: vi.fn(),
  timeshare: vi.fn(),
}));

vi.mock('../../api/stocks', () => ({
  stocksApi: { getQuote },
}));

vi.mock('../../api/chart', () => ({
  chartApi: { kline, timeshare },
}));

const renderAt = (entry: string) => {
  const router = createMemoryRouter([{ path: '/stock-query', element: <StockQueryPage /> }], {
    initialEntries: [entry],
  });
  render(<RouterProvider router={router} />);
  return router;
};

beforeEach(() => {
  vi.clearAllMocks();
  getQuote.mockImplementation((code: string) => Promise.resolve({
    stockCode: code,
    stockName: `股票${code}`,
    currentPrice: 12.34,
    change: 0.1,
    changePercent: 0.8,
  }));
  kline.mockResolvedValue({
    code: '000000',
    name: '',
    period: 'day',
    adjust: 'qfq',
    indicators: ['ma', 'macd'],
    rows: [],
    todaySource: null,
    listedSince: null,
    tradingHours: null,
    source: 'ths',
  });
  timeshare.mockResolvedValue({
    code: '000000',
    name: '',
    tradeDate: null,
    prevClose: null,
    tradeTimes: [],
    isTrading: false,
    suspended: false,
    points: [],
    source: 'ths',
  });
});

describe('StockQueryPage entry from other pages', () => {
  it('queries the code passed in the URL instead of the default stock', async () => {
    renderAt('/stock-query?code=603019');

    await waitFor(() => expect(getQuote).toHaveBeenCalledWith('603019'));
    // 首屏不能先为默认股发一轮请求
    expect(getQuote).toHaveBeenCalledTimes(1);
    expect(await screen.findByText('股票603019')).toBeInTheDocument();
    expect(kline).toHaveBeenCalledWith('603019', expect.objectContaining({ period: 'day' }));
  });

  it('falls back to the default stock when the code is not an A-share code', async () => {
    renderAt('/stock-query?code=HK00700');

    await waitFor(() => expect(getQuote).toHaveBeenCalledWith('000960'));
  });

  it('switches the stock when the same route is entered again with another code', async () => {
    const router = renderAt('/stock-query?code=603019');
    await waitFor(() => expect(getQuote).toHaveBeenCalledWith('603019'));

    await router.navigate('/stock-query?code=300750');

    await waitFor(() => expect(getQuote).toHaveBeenCalledWith('300750'));
    expect(await screen.findByText('股票300750')).toBeInTheDocument();
  });

  it('选中后清空搜索框，当前标的由行情头部交代', async () => {
    renderAt('/stock-query?code=603019');
    await waitFor(() => expect(getQuote).toHaveBeenCalledWith('603019'));

    const input = screen.getByLabelText('股票查询');
    // 搜索框不回显当前标的
    expect(input).toHaveValue('');

    fireEvent.change(input, { target: { value: '300750' } });
    expect(input).toHaveValue('300750');
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => expect(getQuote).toHaveBeenCalledWith('300750'));
    expect(input).toHaveValue('');
  });

  it('港股 / 美股候选明确提示不支持，而不是静默无反应', async () => {
    renderAt('/stock-query?code=603019');
    await waitFor(() => expect(getQuote).toHaveBeenCalledWith('603019'));
    getQuote.mockClear();

    const input = screen.getByLabelText('股票查询');
    fireEvent.change(input, { target: { value: 'AAPL' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    const alert = await waitFor(() => screen.getByRole('alert'));
    expect(alert).toHaveTextContent(/只支持 A 股与场内基金/);
    // 输入保留，便于用户自己改；也不会切换标的
    expect(input).toHaveValue('AAPL');
    expect(getQuote).not.toHaveBeenCalled();

    // 一改输入提示就撤掉
    fireEvent.change(input, { target: { value: 'AAP' } });
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('不再依赖百度财经代理：页面只打自有行情与图表接口', async () => {
    renderAt('/stock-query?code=603019');
    await waitFor(() => expect(kline).toHaveBeenCalled());
    // 概览/资金/简况/股评/资讯/财务/分析七个百度 tab 已移除，页面只剩这两条链路
    expect(screen.queryByRole('tab', { name: '资金' })).not.toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: '财务' })).not.toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '日K' })).toBeInTheDocument();
  });
});
