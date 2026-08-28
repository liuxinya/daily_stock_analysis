import { render, screen, waitFor } from '@testing-library/react';
import { RouterProvider, createMemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import StockQueryPage from '../StockQueryPage';

const { getQuote, kline, fundFlow } = vi.hoisted(() => ({
  getQuote: vi.fn(),
  kline: vi.fn(),
  fundFlow: vi.fn(),
}));

vi.mock('../../api/stocks', () => ({
  stocksApi: { getQuote },
}));

vi.mock('../../api/baiduFinance', () => ({
  baiduFinanceApi: {
    kline,
    fundFlow,
    company: vi.fn(),
    talks: vi.fn(),
    sentiment: vi.fn(),
    finance: vi.fn(),
    mainOperation: vi.fn(),
    analysis: vi.fn(),
  },
  parseMarketData: () => [],
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
  kline.mockResolvedValue({ Result: {} });
  fundFlow.mockResolvedValue({ Result: {} });
});

describe('StockQueryPage entry from other pages', () => {
  it('queries the code passed in the URL instead of the default stock', async () => {
    renderAt('/stock-query?code=603019');

    await waitFor(() => expect(getQuote).toHaveBeenCalledWith('603019'));
    // 首屏不能先为默认股发一轮请求
    expect(getQuote).toHaveBeenCalledTimes(1);
    expect(await screen.findByText('股票603019')).toBeInTheDocument();
    expect(kline).toHaveBeenCalledWith('603019');
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
});
