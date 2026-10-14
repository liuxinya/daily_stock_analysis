import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import NewsAnalysisPage from '../NewsAnalysisPage';

// 三个视图各自很重，这里只关心 tab 编排，所以替换成哨兵节点。
vi.mock('../RealtimeNewsView', () => ({ default: () => <div>realtime-view</div> }));
vi.mock('../NewsSearchView', () => ({ default: () => <div>news-search-view</div> }));
vi.mock('../CctvNewsView', () => ({ default: () => <div>cctv-view</div> }));

const renderPage = (entry = '/news-analysis') => render(
  <MemoryRouter initialEntries={[entry]}>
    <NewsAnalysisPage />
  </MemoryRouter>,
);

describe('NewsAnalysisPage', () => {
  it('默认落在「实时新闻」tab 并渲染实时新闻视图', async () => {
    renderPage();

    expect(screen.getByRole('tab', { name: '实时新闻' })).toHaveAttribute('aria-selected', 'true');
    expect(await screen.findByText('realtime-view')).toBeInTheDocument();
  });

  it('切到「CCTV新闻」tab 渲染 CCTV 视图', async () => {
    renderPage();
    fireEvent.click(screen.getByRole('tab', { name: 'CCTV新闻' }));

    // setSearchParams 走 router 的 transition，选中态要等导航落地
    expect(await screen.findByText('cctv-view')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByRole('tab', { name: 'CCTV新闻' })).toHaveAttribute('aria-selected', 'true');
    });
  });

  it('「新闻搜索」tab 渲染搜索视图', async () => {
    renderPage('/news-analysis?tab=search');

    expect(screen.getByRole('tab', { name: '新闻搜索' })).toHaveAttribute('aria-selected', 'true');
    expect(await screen.findByText('news-search-view')).toBeInTheDocument();
  });

  it('支持从 URL 深链到某个 tab（CCTV 分析完成通知用）', async () => {
    renderPage('/news-analysis?tab=cctv&ctab=analysis');

    expect(screen.getByRole('tab', { name: 'CCTV新闻' })).toHaveAttribute('aria-selected', 'true');
    expect(await screen.findByText('cctv-view')).toBeInTheDocument();
  });

  it('未知 tab 参数回落到「实时新闻」', async () => {
    renderPage('/news-analysis?tab=not-a-tab');

    expect(screen.getByRole('tab', { name: '实时新闻' })).toHaveAttribute('aria-selected', 'true');
    expect(await screen.findByText('realtime-view')).toBeInTheDocument();
  });
});
