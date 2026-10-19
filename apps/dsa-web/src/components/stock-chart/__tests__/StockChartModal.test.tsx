import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import { StockChartModal } from '../StockChartModal';
import { useStockChartStore } from '../../../stores/stockChartStore';

const { getQuote, kline, timeshare } = vi.hoisted(() => ({
  getQuote: vi.fn(),
  kline: vi.fn(),
  timeshare: vi.fn(),
}));

vi.mock('../../../api/stocks', () => ({ stocksApi: { getQuote } }));
vi.mock('../../../api/chart', () => ({ chartApi: { kline, timeshare } }));

const renderModal = () => render(
  <MemoryRouter>
    <StockChartModal />
  </MemoryRouter>,
);

/** 200 根合成日 K：首屏可见 120 根，留出平移与缩放的余量。 */
const ROWS = Array.from({ length: 200 }, (_, index) => {
  const close = 80 + Math.sin(index / 7) * 5;
  return {
    time: `2026${String(((index % 12) + 1)).padStart(2, '0')}${String((index % 28) + 1).padStart(2, '0')}`,
    open: close - 0.3,
    high: close + 0.5,
    low: close - 0.6,
    close,
    volume: 1_000_000 + index,
    amount: (1_000_000 + index) * close,
    turnoverRate: 0.5,
    partial: index === 199,
    ma5: close, ma10: close, ma20: close,
    dif: -1.2 + index / 500, dea: -1.3 + index / 500, macd: 0.2 - index / 1000,
    k: 40 + (index % 30), d: 35 + (index % 25), j: 50 + (index % 40),
  };
});

const KLINE = {
  code: '002594',
  name: '比亚迪',
  period: 'day' as const,
  adjust: 'qfq' as const,
  indicators: ['ma', 'macd', 'kdj'],
  rows: ROWS,
  todaySource: 'snapshot' as const,
  listedSince: '20110630',
  tradingHours: '0930-1130,1300-1500',
  source: 'ths',
};

describe('StockChartModal', () => {
  afterEach(() => cleanup());

  beforeEach(() => {
    getQuote.mockReset();
    kline.mockReset();
    timeshare.mockReset();
    useStockChartStore.setState({ target: null });
    getQuote.mockResolvedValue({
      stockCode: '002594', stockName: '比亚迪', currentPrice: 83.53,
      change: 0.81, changePercent: 0.98, open: 82.5, high: 84.15, low: 82.3,
      prevClose: 82.72, volume: 19563278, amount: 1634894400,
      updateTime: '2026-10-19T14:03:00',
    });
    kline.mockResolvedValue(KLINE);
    timeshare.mockResolvedValue({
      code: '002594', name: '比亚迪', tradeDate: '20261019', prevClose: 82.72,
      tradeTimes: ['0930-1130', '1300-1500'], isTrading: true, suspended: false,
      points: [
        { time: '0930', price: 82.5, avgPrice: 82.5, volume: 56000, amount: 4620000 },
        { time: '0931', price: 82.79, avgPrice: 82.6, volume: 376900, amount: 31138083 },
      ],
      source: 'ths',
    });
  });

  it('store 为空时不渲染任何东西', () => {
    renderModal();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('store 写入标的后弹出，并按代码取行情与日 K', async () => {
    renderModal();
    useStockChartStore.getState().open('002594', '比亚迪');

    const dialog = await waitFor(() => screen.getByRole('dialog'));
    expect(within(dialog).getByText('比亚迪')).toBeInTheDocument();
    expect(within(dialog).getByText('002594')).toBeInTheDocument();
    expect(getQuote).toHaveBeenCalledWith('002594');
    expect(kline).toHaveBeenCalledWith('002594', expect.objectContaining({ period: 'day' }));
    // 末根由盘口快照合成时必须如实标注，不能让人把半根当成已收盘的一根
    await waitFor(() =>
      expect(within(dialog).getByText(/末根为盘中实时合成，尚未收盘/)).toBeInTheDocument(),
    );
  });

  it('可切到分时，昨收作为基准线一并给出', async () => {
    renderModal();
    useStockChartStore.getState().open('002594', '比亚迪');
    const dialog = await waitFor(() => screen.getByRole('dialog'));

    fireEvent.click(within(dialog).getByRole('tab', { name: '分时' }));

    expect(timeshare).toHaveBeenCalledWith('002594');
    await waitFor(() => expect(within(dialog).getByText(/昨收 82\.72/)).toBeInTheDocument());
  });

  it('MACD 与 KDJ 常驻：一次把两组指标都要回来，不再有副图切换', async () => {
    renderModal();
    useStockChartStore.getState().open('002594', '比亚迪');
    const dialog = await waitFor(() => screen.getByRole('dialog'));

    expect(kline).toHaveBeenCalledWith(
      '002594',
      expect.objectContaining({ indicators: ['ma', 'macd', 'kdj'] }),
    );
    // 两栏都画出来，并各自常驻一行数值
    await waitFor(() => expect(within(dialog).getByText('MACD')).toBeInTheDocument());
    expect(within(dialog).getByText('KDJ')).toBeInTheDocument();
    expect(within(dialog).queryByRole('tab', { name: 'KDJ' })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole('tab', { name: '不显示' })).not.toBeInTheDocument();
  });

  it('键盘可平移与缩放视窗，Home 复位', async () => {
    renderModal();
    useStockChartStore.getState().open('002594', '比亚迪');
    const dialog = await waitFor(() => screen.getByRole('dialog'));
    const chart = await waitFor(() => within(dialog).getByRole('group', { name: /K 线图/ }));

    // 首屏只显示最近 120 根，不是全部 200 根
    expect(within(dialog).getByText(/显示 120\/200 根/)).toBeInTheDocument();
    const firstVisible = ROWS[80].time;
    expect(within(dialog).getByText(new RegExp(firstVisible))).toBeInTheDocument();

    fireEvent.keyDown(chart, { key: '-' });
    await waitFor(() => expect(within(dialog).getByText(/显示 144\/200 根/)).toBeInTheDocument());

    fireEvent.keyDown(chart, { key: 'Home' });
    await waitFor(() => expect(within(dialog).getByText(/显示 120\/200 根/)).toBeInTheDocument());

    // 往左平移后窗口右端不再贴着最新一根
    fireEvent.keyDown(chart, { key: 'ArrowLeft' });
    await waitFor(() => expect(within(dialog).getByText(new RegExp(ROWS[68].time))).toBeInTheDocument());
  });

  it('主图读数行常驻末根的完整数据（光标移动时换成所在那一根）', async () => {
    renderModal();
    useStockChartStore.getState().open('002594', '比亚迪');
    const dialog = await waitFor(() => screen.getByRole('dialog'));

    const last = ROWS[199];
    await waitFor(() => expect(within(dialog).getByText(last.time)).toBeInTheDocument());
    // 开/高/低/收 + 量额 + 换手 + 未走完标注都在读数行上，不只藏在 tooltip 里
    expect(within(dialog).getByText(last.open.toFixed(2))).toBeInTheDocument();
    expect(within(dialog).getByText(last.high.toFixed(2))).toBeInTheDocument();
    expect(within(dialog).getByText(last.low.toFixed(2))).toBeInTheDocument();
    expect(within(dialog).getByText('（未走完）')).toBeInTheDocument();
    expect(within(dialog).getByText(/MA5/)).toBeInTheDocument();
  });

  it('双指上下滑 / 滚轮：向上看更广，向下看更少', async () => {
    renderModal();
    useStockChartStore.getState().open('002594', '比亚迪');
    const dialog = await waitFor(() => screen.getByRole('dialog'));
    const chart = await waitFor(() => within(dialog).getByRole('group', { name: /K 线图/ }));
    expect(within(dialog).getByText(/显示 120\/200 根/)).toBeInTheDocument();

    // 向上滑（自然滚动下 deltaY > 0）-> 区间变宽。120 * exp(100*0.0015) ≈ 139
    // 滚轮缩放经过 requestAnimationFrame 合并提交，全量跑并发时那一帧可能被推到 1s 之后，
    // 所以这两处放宽 waitFor 的窗口，默认 1s 会偶发超时。
    fireEvent.wheel(chart, { deltaY: 100 });
    await waitFor(
      () => expect(within(dialog).getByText(/显示 139\/200 根/)).toBeInTheDocument(),
      { timeout: 5000 },
    );

    // 向下滑 -> 区间变窄，回到 120 附近
    fireEvent.wheel(chart, { deltaY: -100 });
    await waitFor(
      () => expect(within(dialog).getByText(/显示 120\/200 根/)).toBeInTheDocument(),
      { timeout: 5000 },
    );
  });

  it('「全览」把全部根数放进视窗', async () => {
    renderModal();
    useStockChartStore.getState().open('002594', '比亚迪');
    const dialog = await waitFor(() => screen.getByRole('dialog'));

    fireEvent.click(within(dialog).getByRole('button', { name: '全览' }));
    await waitFor(() => expect(within(dialog).getByText(/显示 200\/200 根/)).toBeInTheDocument());
  });

  it('提供跳同花顺个股页的外链，按代码拼接且新标签打开', async () => {
    renderModal();
    useStockChartStore.getState().open('002594', '比亚迪');
    const dialog = await waitFor(() => screen.getByRole('dialog'));

    const link = within(dialog).getByRole('link', { name: /在同花顺新标签页打开 比亚迪\(002594\)/ });
    expect(link).toHaveAttribute('href', 'https://stockpage.10jqka.com.cn/002594');
    expect(link).toHaveAttribute('target', '_blank');
    // 外链必须带 noopener，别把 window.opener 交出去
    expect(link.getAttribute('rel')).toMatch(/noopener/);
  });

  it('Escape 与关闭按钮都能关掉', async () => {
    renderModal();
    useStockChartStore.getState().open('002594', '比亚迪');
    await waitFor(() => screen.getByRole('dialog'));

    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

    useStockChartStore.getState().open('002594', '比亚迪');
    const dialog = await waitFor(() => screen.getByRole('dialog'));
    fireEvent.click(within(dialog).getByRole('button', { name: '关闭行情弹窗' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('行情挂了仍然画图，两条链路互不拖垮', async () => {
    getQuote.mockRejectedValue(new Error('quote down'));
    renderModal();
    useStockChartStore.getState().open('002594', '比亚迪');

    const dialog = await waitFor(() => screen.getByRole('dialog'));
    await waitFor(() => expect(within(dialog).getByRole('alert')).toBeInTheDocument());
    expect(within(dialog).getByRole('tab', { name: '日K' })).toBeInTheDocument();
    expect(kline).toHaveBeenCalled();
  });
});
