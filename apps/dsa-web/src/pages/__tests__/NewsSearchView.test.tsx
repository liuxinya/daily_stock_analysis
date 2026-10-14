import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import NewsSearchView from '../NewsSearchView';

const { listPlatforms, search } = vi.hoisted(() => ({
  listPlatforms: vi.fn(),
  search: vi.fn(),
}));

vi.mock('../../api/newsSearch', () => ({
  newsSearchApi: { listPlatforms, search },
}));

const platformList = {
  items: [
    { key: 'cls', label: '财联社' },
    { key: 'ths', label: '同花顺-问财' },
  ],
  total: 2,
  defaultPlatform: 'cls',
};

const itemsResponse = (overrides: Record<string, unknown> = {}) => ({
  platform: 'cls',
  platformLabel: '财联社',
  query: '比亚迪',
  windowDays: 2,
  items: [{
    platform: 'cls',
    platformLabel: '财联社',
    title: '比亚迪固态电池提速',
    summary: '多家车企加码布局',
    url: 'https://www.cls.cn/detail/909',
    source: '财联社',
    publishedAt: new Date(Date.now() - 3600 * 1000).toISOString(),
  }],
  total: 1,
  pagesScanned: 2,
  fetchedAt: '2026-10-14T18:00:00+08:00',
  ...overrides,
});

const renderView = () => render(<NewsSearchView />);

const submitKeyword = (value: string) => {
  fireEvent.change(screen.getByLabelText('新闻搜索关键词'), { target: { value } });
  fireEvent.click(screen.getByRole('button', { name: /搜索/ }));
};

describe('NewsSearchView', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listPlatforms.mockResolvedValue(platformList);
    search.mockResolvedValue(itemsResponse());
  });

  it('先显示搜索提示，不主动打上游', async () => {
    renderView();

    expect(await screen.findByText('输入关键词开始搜索')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '财联社' })).toHaveAttribute('aria-selected', 'true');
    expect(search).not.toHaveBeenCalled();
  });

  it('提交关键词后按默认平台搜索并渲染结果', async () => {
    renderView();
    await screen.findByRole('tab', { name: '财联社' });

    submitKeyword('比亚迪');

    await waitFor(() => {
      expect(search).toHaveBeenCalledWith({ q: '比亚迪', platform: 'cls', days: 7, limit: 50 });
    });
    expect(await screen.findByText('比亚迪固态电池提速')).toBeInTheDocument();
    expect(screen.getByText(/关键词「比亚迪」/)).toBeInTheDocument();
  });

  it('切换平台会用同一关键词再搜一次', async () => {
    renderView();
    await screen.findByRole('tab', { name: '财联社' });
    submitKeyword('比亚迪');
    await waitFor(() => expect(search).toHaveBeenCalledTimes(1));

    search.mockResolvedValue(itemsResponse({ platform: 'ths', platformLabel: '同花顺-问财', items: [{
      platform: 'ths', platformLabel: '同花顺-问财', title: '问财：比亚迪大汉上市',
      summary: '', url: 'http://news.10jqka.com.cn/1.shtml', source: '同花顺财经', publishedAt: null,
    }] }));
    fireEvent.click(screen.getByRole('tab', { name: '同花顺-问财' }));

    await waitFor(() => {
      expect(search).toHaveBeenLastCalledWith({ q: '比亚迪', platform: 'ths', days: 7, limit: 50 });
    });
    expect(await screen.findByText('问财：比亚迪大汉上市')).toBeInTheDocument();
  });

  it('切回已搜过的平台直接复用缓存，不重复打上游', async () => {
    renderView();
    await screen.findByRole('tab', { name: '财联社' });
    submitKeyword('比亚迪');
    await waitFor(() => expect(search).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole('tab', { name: '同花顺-问财' }));
    await waitFor(() => expect(search).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByRole('tab', { name: '财联社' }));

    expect(await screen.findByText('比亚迪固态电池提速')).toBeInTheDocument();
    expect(search).toHaveBeenCalledTimes(2);
  });

  it('上游失败时展示可重试的错误', async () => {
    renderView();
    await screen.findByRole('tab', { name: '财联社' });
    search.mockRejectedValue({
      parsedError: {
        title: '搜索失败', message: '上游接口不可用', rawMessage: '上游接口不可用',
        category: 'http_error' as const, status: 503,
      },
    });

    submitKeyword('比亚迪');

    expect(await screen.findByText('搜索失败')).toBeInTheDocument();
    expect(screen.getByText('上游接口不可用')).toBeInTheDocument();
  });
});
