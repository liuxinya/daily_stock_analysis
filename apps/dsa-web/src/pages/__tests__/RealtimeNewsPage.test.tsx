import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import RealtimeNewsPage from '../RealtimeNewsPage';
import { useMarketBriefStore } from '../../stores/marketBriefStore';

const { listPlatforms, listItems, getWindow, latest, submitJob, getJob } = vi.hoisted(() => ({
  listPlatforms: vi.fn(),
  listItems: vi.fn(),
  getWindow: vi.fn(),
  latest: vi.fn(),
  submitJob: vi.fn(),
  getJob: vi.fn(),
}));

vi.mock('../../api/realtimeNews', () => ({
  realtimeNewsApi: { listPlatforms, listItems },
}));

vi.mock('../../api/marketBrief', () => ({
  marketBriefApi: { getWindow, latest, submitJob, getJob },
}));

const platformList = {
  items: [
    { key: 'hot', label: '财经热榜', provider: 'aggregate', available: true, hasPublishedAt: true },
    { key: 'ths', label: '同花顺', provider: 'akshare', available: true, hasPublishedAt: true },
    { key: 'yicai', label: '第一财经', provider: 'yicai', available: true, hasPublishedAt: true },
    // 后端注册表当前每个平台都有源，但 available: false 仍是接口契约的一部分，
    // 前端要对占位平台展示空态而不是报错，所以这里保留一个合成的占位项
    { key: 'placeholder', label: '占位平台', provider: '', available: false, hasPublishedAt: true },
  ],
  total: 4,
  defaultPlatform: 'hot',
};

const briefWindow = {
  market: 'cn',
  mode: 'weekend' as const,
  modeLabel: '周末',
  phase: 'non_trading',
  windowHint: '上一个交易日收盘至今',
  now: '2026-09-06T21:00:00+08:00',
  start: '2026-09-04T15:00:00+08:00',
  end: '2026-09-06T21:00:00+08:00',
  durationHours: 54,
  degraded: false,
  degradedReason: '',
  warnings: [],
};

const briefPayload = {
  schemaVersion: '2026-09-08',
  mode: 'weekend' as const,
  modeLabel: '周末',
  window: briefWindow,
  itemCount: 12,
  chunkCount: 1,
  model: 'stub-model',
  provider: 'stub',
  result: {
    schemaVersion: '2026-09-08',
    mode: 'weekend' as const,
    modeLabel: '周末',
    windowStart: briefWindow.start,
    windowEnd: briefWindow.end,
    overallTone: 'bullish' as const,
    summary: '周末政策面偏积极。',
    events: [{
      topic: '工信部发文', detail: '通信网建设', category: '政策', importance: 'high' as const,
      evidenceIds: [1], mentionedTerms: ['通信网'],
    }],
    sectors: [{
      name: '通信', direction: 'bullish' as const, strength: 'medium' as const, reason: '政策支持',
      evidenceIds: [1], mentionedTerms: ['通信网'],
    }],
    watchList: [{
      name: '中兴通讯', code: '000063', sector: '通信', direction: 'bullish' as const,
      rationale: '行业相关', evidenceIds: [1],
    }],
    takeaways: [{ topic: '开盘关注通信', detail: '看能否放量', evidenceIds: [1], mentionedTerms: ['通信网'] }],
    risks: ['政策落地节奏不确定'],
    keywords: ['通信'],
    notCovered: ['海外市场'],
    watchListDisclaimer: '个股名称未必出现在资讯原文中。',
  },
  grounding: { dropped: ['watch:某票'], droppedCount: 1, unverifiedTerms: [], invalidEvidenceIds: [] },
  coverage: { totalItems: 12, citedItems: 1 },
  degraded: false,
  degradedReason: '',
  llmCalls: 1,
  durationMs: 1200,
  cached: false,
  createdAt: '2026-09-06T21:05:00+08:00',
};

const itemsResponse = (overrides: Record<string, unknown> = {}) => ({
  platform: 'hot',
  platformLabel: '财经热榜',
  available: true,
  hasPublishedAt: true,
  items: [{
    platform: 'ths', platformLabel: '同花顺', title: '同花顺快讯一条',
    summary: '摘要', url: 'https://example.com/a',
    publishedAt: new Date(Date.now() - 3600 * 1000).toISOString(), tag: '',
  }],
  total: 1,
  page: 1,
  pageSize: 20,
  failedPlatforms: [],
  fetchedAt: '2026-09-07T20:00:00+08:00',
  ...overrides,
});

const renderPage = () => render(
  <MemoryRouter initialEntries={['/realtime-news']}>
    <RealtimeNewsPage />
  </MemoryRouter>,
);

describe('RealtimeNewsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listPlatforms.mockResolvedValue(platformList);
    listItems.mockResolvedValue(itemsResponse());
    getWindow.mockResolvedValue(briefWindow);
    latest.mockRejectedValue({ parsedError: { title: '未找到', message: '没有汇总', rawMessage: '', category: 'http_error' as const, status: 404 } });
    useMarketBriefStore.setState({ job: null, result: null, submitError: null, notice: null });
  });

  it('lands on the brief tab and shows the current window', async () => {
    renderPage();

    expect(await screen.findByRole('tab', { name: '消息面汇总' })).toHaveAttribute('aria-selected', 'true');
    expect(await screen.findByText(/当前时段：周末/)).toBeInTheDocument();
    // 落地页不该去拉平台快讯
    expect(listItems).not.toHaveBeenCalled();
  });

  it('shows an empty state when no brief exists yet', async () => {
    renderPage();
    expect(await screen.findByText('还没有这个时段的消息面汇总')).toBeInTheDocument();
  });

  it('renders a fact layer and an explicitly labelled inference layer', async () => {
    latest.mockResolvedValue(briefPayload);
    renderPage();

    expect(await screen.findByText('关键消息')).toBeInTheDocument();
    expect(await screen.findByText('工信部发文')).toBeInTheDocument();
    expect(await screen.findByText('板块方向')).toBeInTheDocument();
    expect(await screen.findByText('关注标的')).toBeInTheDocument();
    expect(await screen.findByText(/由已确认的板块方向推断/)).toBeInTheDocument();
    // grounding 丢弃要如实说明
    expect(await screen.findByText('已过滤无原文依据的结论')).toBeInTheDocument();
  });

  it('submits a brief job and polls until it succeeds', async () => {
    submitJob.mockResolvedValue({
      jobId: 'job-1', mode: 'weekend', modeLabel: '周末',
      windowStart: briefWindow.start, windowEnd: briefWindow.end,
      status: 'queued', phase: 'queued', phaseLabel: '排队中', message: '已提交',
      totalItems: 0, totalChunks: 0, currentChunk: 0, progress: 0,
      createdAt: 0, updatedAt: 0, result: null, error: null,
    });
    getJob.mockResolvedValue({
      jobId: 'job-1', mode: 'weekend', modeLabel: '周末',
      windowStart: briefWindow.start, windowEnd: briefWindow.end,
      status: 'succeeded', phase: 'succeeded', phaseLabel: '已完成', message: '完成',
      totalItems: 12, totalChunks: 1, currentChunk: 1, progress: 1,
      createdAt: 0, updatedAt: 0, result: briefPayload, error: null,
    });

    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: /生成汇总/ }));

    await waitFor(() => expect(submitJob).toHaveBeenCalledWith(false));
    expect(await screen.findByText('工信部发文')).toBeInTheDocument();
  });

  it('switches to a platform tab and loads its news', async () => {
    renderPage();
    const tab = await screen.findByRole('tab', { name: '同花顺' });

    listItems.mockResolvedValue(itemsResponse({
      platform: 'ths', platformLabel: '同花顺',
    }));
    fireEvent.click(tab);

    await waitFor(() => {
      expect(listItems).toHaveBeenLastCalledWith({ platform: 'ths', page: 1, pageSize: 20 });
    });
    expect(await screen.findByText('同花顺快讯一条')).toBeInTheDocument();
  });

  it('shows a placeholder instead of an error for a platform without any source', async () => {
    renderPage();
    const tab = await screen.findByRole('tab', { name: '占位平台' });

    listItems.mockResolvedValue(itemsResponse({
      platform: 'placeholder', platformLabel: '占位平台', available: false, items: [], total: 0,
    }));
    fireEvent.click(tab);

    expect(await screen.findByText('占位平台暂无可用新闻源')).toBeInTheDocument();
  });

  it('warns about failed platforms without hiding the items that did load', async () => {
    renderPage();
    listItems.mockResolvedValue(itemsResponse({ failedPlatforms: ['yicai'] }));
    fireEvent.click(await screen.findByRole('tab', { name: '财经热榜' }));

    expect(await screen.findByText('部分平台本次没拉到内容')).toBeInTheDocument();
    expect(await screen.findByText('同花顺快讯一条')).toBeInTheDocument();
  });

  it('surfaces a platform api error with a retry action', async () => {
    renderPage();
    listItems.mockRejectedValue({
      parsedError: {
        title: '加载失败', message: '实时新闻接口不可用', rawMessage: '实时新闻接口不可用',
        category: 'http_error' as const, status: 503,
      },
    });
    fireEvent.click(await screen.findByRole('tab', { name: '财经热榜' }));

    expect(await screen.findByText('加载失败')).toBeInTheDocument();
    expect(await screen.findByText('实时新闻接口不可用')).toBeInTheDocument();
  });
});
