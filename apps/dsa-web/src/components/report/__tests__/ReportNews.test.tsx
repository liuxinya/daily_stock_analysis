import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { newsSearchApi } from '../../../api/newsSearch';
import type { NewsSearchAggregateResponse } from '../../../api/newsSearch';
import { ReportNews } from '../ReportNews';

vi.mock('../../../api/newsSearch', () => ({
  newsSearchApi: {
    searchAll: vi.fn(),
  },
}));

const buildResponse = (
  overrides: Partial<NewsSearchAggregateResponse> = {},
): NewsSearchAggregateResponse => ({
  platform: 'all',
  platformLabel: '全部平台',
  query: '中科曙光',
  windowDays: 7,
  items: [],
  total: 0,
  pagesScanned: 5,
  fetchedAt: '2026-10-18T15:19:00+08:00',
  failedPlatforms: [],
  ...overrides,
});

describe('ReportNews', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders aggregated news items with platform badges and refreshes with preserved subpanel styling', async () => {
    vi.mocked(newsSearchApi.searchAll).mockResolvedValue(buildResponse({
      total: 1,
      items: [
        {
          platform: 'cls',
          platformLabel: '财联社',
          title: '中科曙光发布最新经营数据',
          summary: '公司披露季度经营情况，市场关注度提升。',
          url: 'https://example.com/news',
          source: '财联社',
          publishedAt: '2026-10-18T09:30:00+08:00',
        },
      ],
    }));

    const { container } = render(<ReportNews stockName="中科曙光" stockCode="603019.SH" />);

    expect(await screen.findByText('中科曙光发布最新经营数据')).toBeInTheDocument();
    expect(screen.getByText('公司披露季度经营情况，市场关注度提升。')).toBeVisible();
    expect(screen.getByText('财联社')).toBeVisible();
    expect(screen.getByRole('link', { name: '跳转' })).toHaveAttribute('href', 'https://example.com/news');
    expect(screen.getByText('新闻搜索 · 全部平台')).toBeVisible();
    expect(screen.getByText('来源：按股票多平台聚合的站内新闻搜索；是否用于分析以输入数据块为准。')).toBeVisible();
    expect(container.querySelector('.home-panel-card')).toBeTruthy();
    expect(container.querySelector('.home-subpanel')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '刷新' }));

    await waitFor(() => {
      expect(newsSearchApi.searchAll).toHaveBeenCalledTimes(2);
    });
  });

  it('searches by stock name because upstream sites index news by company name', async () => {
    vi.mocked(newsSearchApi.searchAll).mockResolvedValue(buildResponse());

    render(<ReportNews stockName="中科曙光" stockCode="603019.SH" />);

    await waitFor(() => {
      expect(newsSearchApi.searchAll).toHaveBeenCalledWith({
        q: '中科曙光',
        days: 7,
        limit: 8,
      });
    });
  });

  it('falls back to the normalized stock code when no name is available', async () => {
    vi.mocked(newsSearchApi.searchAll).mockResolvedValue(buildResponse());

    render(<ReportNews stockCode="603019.SH" />);

    await waitFor(() => {
      expect(newsSearchApi.searchAll).toHaveBeenCalledWith({
        q: '603019',
        days: 7,
        limit: 8,
      });
    });
  });

  it('renders nothing when neither stock name nor code is available', () => {
    const { container } = render(<ReportNews />);

    expect(container).toBeEmptyDOMElement();
    expect(newsSearchApi.searchAll).not.toHaveBeenCalled();
  });

  it('hints at partial upstream failures without dropping the results', async () => {
    vi.mocked(newsSearchApi.searchAll).mockResolvedValue(buildResponse({
      total: 1,
      failedPlatforms: ['ths', 'jin10'],
      items: [
        {
          platform: 'cls',
          platformLabel: '财联社',
          title: '中科曙光获机构调研',
          summary: '机构关注度提升。',
          url: 'https://example.com/partial',
          source: '财联社',
          publishedAt: '2026-10-18T10:00:00+08:00',
        },
      ],
    }));

    render(<ReportNews stockName="中科曙光" />);

    expect(await screen.findByText('中科曙光获机构调研')).toBeInTheDocument();
    expect(screen.getByText('部分平台本次未返回结果，内容可能不完整；可点「刷新」重试。')).toBeVisible();
  });

  it('renders the empty state when the search returns nothing', async () => {
    vi.mocked(newsSearchApi.searchAll).mockResolvedValue(buildResponse());

    render(<ReportNews stockName="中科曙光" />);

    expect(await screen.findByText('暂无相关资讯')).toBeInTheDocument();
    expect(screen.getByText('可稍后刷新以获取最新资讯。')).toBeInTheDocument();
  });

  it('localizes the empty state description for english reports', async () => {
    vi.mocked(newsSearchApi.searchAll).mockResolvedValue(buildResponse());

    render(<ReportNews stockName="Microchip" language="en" />);

    expect(await screen.findByText('No related news')).toBeInTheDocument();
    expect(screen.getByText('Refresh later to check for the latest updates.')).toBeInTheDocument();
    expect(screen.getByText('News search · all platforms')).toBeVisible();
  });

  it('renders the error state and supports retry', async () => {
    vi.mocked(newsSearchApi.searchAll)
      .mockRejectedValueOnce(new Error('network failed'))
      .mockResolvedValueOnce(buildResponse({
        total: 1,
        items: [
          {
            platform: 'eastmoney',
            platformLabel: '东方财富',
            title: '重试成功',
            summary: '第二次请求成功返回。',
            url: 'https://example.com/retry',
            source: '东方财富',
            publishedAt: '2026-10-18T11:00:00+08:00',
          },
        ],
      }));

    render(<ReportNews stockName="中科曙光" />);

    expect(await screen.findByRole('alert')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '重试' }));

    expect(await screen.findByText('重试成功')).toBeInTheDocument();
  });
});
