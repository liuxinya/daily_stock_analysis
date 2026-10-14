import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { StrictMode } from 'react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import CctvNewsView from '../CctvNewsView';
import { useCctvAnalysisStore } from '../../stores/cctvAnalysisStore';

/** 把当前地址暴露成文本，用来断言跳转目标与查询串。 */
const LocationProbe = () => {
  const location = useLocation();
  return <div data-testid="location">{`${location.pathname}${location.search}`}</div>;
};

const { listDays, listItems, getItem, sync, submitAnalysisJob, getAnalysisJob, latestAnalysis } =
  vi.hoisted(() => ({
    listDays: vi.fn(),
    listItems: vi.fn(),
    getItem: vi.fn(),
    sync: vi.fn(),
    submitAnalysisJob: vi.fn(),
    getAnalysisJob: vi.fn(),
    latestAnalysis: vi.fn(),
  }));

vi.mock('../../api/cctvNews', () => ({
  cctvNewsApi: { listDays, listItems, getItem, sync, submitAnalysisJob, getAnalysisJob, latestAnalysis },
}));

const parsedError = {
  title: '加载失败',
  message: 'CCTV 新闻接口不可用',
  rawMessage: 'CCTV 新闻接口不可用',
  category: 'http_error' as const,
  status: 500,
};

const dayList = {
  startDate: '2026-07-28',
  endDate: '2026-08-25',
  totalDays: 2,
  totalItems: 3,
  days: [
    { newsDate: '2026-08-25', itemCount: 2, charCount: 900 },
    { newsDate: '2026-08-24', itemCount: 1, charCount: 400 },
  ],
};

const itemList = {
  items: [
    {
      id: 11,
      newsDate: '2026-08-25',
      seq: 1,
      title: '国务院部署人工智能+行动',
      summary: '会议提出深入推进人工智能+行动。',
      url: 'https://tv.cctv.com/a.shtml',
      charCount: 520,
      fetchedAt: '2026-08-25T20:30:00',
    },
    {
      id: 12,
      newsDate: '2026-08-25',
      seq: 2,
      title: '全国夏粮再获丰收',
      summary: '今年夏粮再获丰收。',
      url: 'https://tv.cctv.com/b.shtml',
      charCount: 300,
      fetchedAt: '2026-08-25T20:30:00',
    },
  ],
  total: 2,
  page: 1,
  pageSize: 100,
  startDate: '2026-08-25',
  endDate: '2026-08-25',
};

const analysisResult = {
  schemaVersion: '2026-08-26',
  promptVersion: 'cctv-news-analysis-v1',
  startDate: '2026-08-19',
  endDate: '2026-08-25',
  rangeDays: 7,
  itemCount: 20,
  model: 'stub-model',
  provider: 'stub',
  durationMs: 4200,
  cached: false,
  analysisId: 7,
  createdAt: '2026-08-26T09:00:00',
  analysis: {
    schemaVersion: '2026-08-26',
    overallTone: 'bullish' as const,
    summary: '政策面偏积极',
    policySignals: [
      { topic: '人工智能+行动', detail: '国务院部署', evidenceIds: [1], mentionedTerms: ['人工智能'] },
    ],
    industries: [
      {
        name: '算力基础设施',
        direction: 'bullish' as const,
        strength: 'high' as const,
        reason: '会议提到加快算力基础设施建设',
        evidenceIds: [1],
        mentionedTerms: ['算力基础设施'],
      },
      {
        name: '钢铁',
        direction: 'bearish' as const,
        strength: 'medium' as const,
        reason: '产能压减任务下达',
        evidenceIds: [1],
        mentionedTerms: ['产能'],
      },
    ],
    stocks: [
      {
        name: '算力龙头',
        code: '603019',
        industry: '算力基础设施',
        direction: 'bullish' as const,
        isLeader: true,
        rationale: '行业核心标的',
        evidenceIds: [1],
      },
    ],
    investorTakeaways: [
      {
        topic: '夏粮收购超 9000 万吨',
        detail: '总量数据可作为农业链需求参考',
        evidenceIds: [1],
        mentionedTerms: ['夏粮'],
      },
    ],
    risks: ['外部环境不确定'],
    keywords: ['人工智能'],
    notCovered: ['房地产'],
    stockDisclaimer: '个股名称未必出现在新闻联播原文中，属于推断，仅供研究参考。',
  },
  evidence: [
    { id: 1, itemId: 11, newsDate: '2026-08-25', title: '国务院部署人工智能+行动', url: 'https://tv.cctv.com/a.shtml' },
  ],
  grounding: { dropped: ['industry:凭空捏造行业'], droppedCount: 1, invalidEvidenceIds: [], unverifiedTerms: [] },
  coverage: { chunkCount: 3, analyzedChunkCount: 3, llmCallCount: 3, skippedItemIds: [], skippedItemCount: 0 },
  degraded: false,
  degradedReason: null,
};

const runningJob = {
  jobId: 'job-1',
  status: 'running' as const,
  phase: 'analyzing',
  phaseLabel: '分析新闻',
  message: '正在分析第 2/3 段（本段 5 条）',
  rangeDays: 7,
  startDate: '2026-08-19',
  endDate: '2026-08-25',
  totalItems: 20,
  totalChunks: 3,
  currentChunk: 2,
  progress: 0.65,
  createdAt: 0,
  updatedAt: 0,
  result: null,
  error: null,
};

const succeededJob = {
  ...runningJob,
  status: 'succeeded' as const,
  phase: 'succeeded',
  phaseLabel: '已完成',
  message: '分析完成',
  currentChunk: 3,
  progress: 1,
  result: analysisResult,
};

beforeEach(() => {
  vi.clearAllMocks();
  listDays.mockResolvedValue(dayList);
  listItems.mockResolvedValue(itemList);
  getItem.mockResolvedValue({ ...itemList.items[0], content: '第一段正文。\n第二段正文。' });
  sync.mockResolvedValue({
    startDate: '2026-07-28',
    endDate: '2026-08-26',
    requestedDays: 30,
    syncedDays: 2,
    created: 30,
    updated: 0,
    deleted: 0,
    days: [{ newsDate: '2026-08-26', listedCount: 0, itemCount: 0, created: 0, updated: 0, failedCount: 0, skippedCount: 0, isComplete: false }],
  });
  submitAnalysisJob.mockResolvedValue(runningJob);
  getAnalysisJob.mockResolvedValue(succeededJob);
  latestAnalysis.mockRejectedValue({ parsedError: { ...parsedError, status: 404 } });
  useCctvAnalysisStore.setState({ job: null, result: null, submitError: null, notice: null });
});

/** 个股表的“问股”“查询”会调 navigate，所以整页都要跑在 Router 里。 */
const renderPage = (initialEntries: string[] = ['/news-analysis?tab=cctv']) => render(
  <MemoryRouter initialEntries={initialEntries}>
    <Routes>
      <Route path="/news-analysis" element={<CctvNewsView />} />
      <Route path="/chat" element={<div>chat-page</div>} />
      <Route path="/stock-query" element={<div>stock-query-page</div>} />
    </Routes>
    <LocationProbe />
  </MemoryRouter>,
);

describe('CctvNewsView', () => {
  it('auto-syncs the latest days when the news directory is stale', async () => {
    const staleDayList = {
      startDate: '2026-07-29',
      endDate: '2026-08-26',
      totalDays: 0,
      totalItems: 0,
      days: [],
    };
    listDays.mockResolvedValueOnce(staleDayList).mockResolvedValueOnce(dayList);

    renderPage();

    await waitFor(() => {
      expect(sync).toHaveBeenCalledWith({ days: 2 });
    });
    expect(await screen.findByText('国务院部署人工智能+行动')).toBeInTheDocument();
    expect(listDays).toHaveBeenCalledTimes(2);
  });

  it('opens the analysis tab when requested by the result notification', async () => {
    renderPage(['/news-analysis?tab=cctv&ctab=analysis']);

    expect(await screen.findByRole('tab', { name: /AI 新闻分析/ })).toHaveAttribute('aria-selected', 'true');
  });

  it('loads the day rail and the latest day transcripts', async () => {
    renderPage();

    expect(await screen.findByText('国务院部署人工智能+行动')).toBeInTheDocument();
    expect(screen.getByText('全国夏粮再获丰收')).toBeInTheDocument();
    expect(listDays).toHaveBeenCalledWith(30);
    await waitFor(() => {
      expect(listItems).toHaveBeenCalledWith({ newsDate: '2026-08-25', pageSize: 100 });
    });
  });

  it('opens the transcript drawer with the full text', async () => {
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: '查看文字稿：国务院部署人工智能+行动' }));

    expect(await screen.findByText('第一段正文。')).toBeInTheDocument();
    expect(screen.getByText('第二段正文。')).toBeInTheDocument();
    expect(getItem).toHaveBeenCalledWith(11);
  });

  it('searches transcripts across the whole window', async () => {
    renderPage();
    await screen.findByText('国务院部署人工智能+行动');

    fireEvent.change(screen.getByLabelText('搜索文字稿'), { target: { value: '算力' } });
    fireEvent.click(screen.getByRole('button', { name: /搜索/ }));

    await waitFor(() => {
      expect(listItems).toHaveBeenCalledWith({ days: 30, query: '算力', page: 1, pageSize: 20 });
    });
    expect(await screen.findByText('搜索「算力」共 2 条')).toBeInTheDocument();
  });

  it('submits a background job for the selected range and shows grounded results', async () => {
    renderPage();
    await screen.findByText('国务院部署人工智能+行动');

    fireEvent.click(screen.getByRole('tab', { name: /AI 新闻分析/ }));
    fireEvent.click(screen.getByRole('button', { name: '最近 10 天' }));
    fireEvent.click(screen.getByRole('button', { name: /开始分析/ }));

    await waitFor(() => {
      expect(submitAnalysisJob).toHaveBeenCalledWith({
        rangeDays: 10,
        newsDate: undefined,
        force: undefined,
      });
    });

    // 行业名同时出现在标题、原文关键词 chip 和个股表的行业列里
    expect((await screen.findAllByText('算力基础设施')).length).toBeGreaterThan(0);
    expect(screen.getByText('算力龙头')).toBeInTheDocument();
    expect(screen.getByText('龙头')).toBeInTheDocument();
    // 利好/利空分区都要出现
    expect(screen.getByText('利好行业方向')).toBeInTheDocument();
    expect(screen.getByText('利空行业方向')).toBeInTheDocument();
    expect(screen.getByText('钢铁')).toBeInTheDocument();
    // 对股民重要的信息
    expect(screen.getByText('对股民重要的信息')).toBeInTheDocument();
    expect(screen.getByText('夏粮收购超 9000 万吨')).toBeInTheDocument();
    // 被丢弃的无依据结论要如实告知用户
    expect(screen.getByText('已过滤无原文依据的结论')).toBeInTheDocument();
    expect(screen.getByText(/仅供研究参考/)).toBeInTheDocument();
  });

  it('shows analysis progress while the background job is running', async () => {
    // 任务停在 running：进度条应显示总篇数与当前段
    getAnalysisJob.mockResolvedValue(runningJob);

    renderPage();
    await screen.findByText('国务院部署人工智能+行动');
    fireEvent.click(screen.getByRole('tab', { name: /AI 新闻分析/ }));
    fireEvent.click(screen.getByRole('button', { name: /开始分析/ }));

    expect(await screen.findByText('分析新闻')).toBeInTheDocument();
    expect(screen.getByText('第 2/3 段')).toBeInTheDocument();
    expect(screen.getByText('65%')).toBeInTheDocument();
    expect(screen.getByText(/共 20 篇新闻/)).toBeInTheDocument();
    expect(screen.getByText(/可以切换到其他页面/)).toBeInTheDocument();
    expect(screen.getByRole('progressbar', { name: '分析进度' })).toHaveAttribute(
      'aria-valuenow',
      '65',
    );
  });

  it('analyzes a single day from the news tab', async () => {
    renderPage();
    await screen.findByText('国务院部署人工智能+行动');

    fireEvent.click(screen.getByRole('button', { name: /分析这天/ }));

    await waitFor(() => {
      expect(submitAnalysisJob).toHaveBeenCalledWith({
        rangeDays: undefined,
        newsDate: '2026-08-25',
        force: true,
      });
    });
  });

  it('opens the source transcript from an analysis evidence chip', async () => {
    renderPage();
    await screen.findByText('国务院部署人工智能+行动');

    fireEvent.click(screen.getByRole('tab', { name: /AI 新闻分析/ }));
    fireEvent.click(screen.getByRole('button', { name: /开始分析/ }));
    await screen.findAllByText('算力基础设施');

    fireEvent.click(
      screen.getAllByRole('button', { name: '查看依据新闻：08-25 国务院部署人工智能+行动' })[0],
    );

    expect(await screen.findByText('第一段正文。')).toBeInTheDocument();
  });

  it('jumps from a recommended stock to the chat page with a prefilled buy-timing question', async () => {
    renderPage();
    await screen.findByText('国务院部署人工智能+行动');

    fireEvent.click(screen.getByRole('tab', { name: /AI 新闻分析/ }));
    fireEvent.click(screen.getByRole('button', { name: /开始分析/ }));
    await screen.findAllByText('算力基础设施');

    fireEvent.click(screen.getByRole('button', { name: '问股：算力龙头(603019)现在适合购买吗？' }));

    expect(await screen.findByText('chat-page')).toBeInTheDocument();
    const location = screen.getByTestId('location').textContent ?? '';
    expect(location.startsWith('/chat?')).toBe(true);
    const params = new URLSearchParams(location.slice('/chat?'.length));
    expect(params.get('stock')).toBe('603019');
    expect(params.get('name')).toBe('算力龙头');
    expect(params.get('q')).toBe('算力龙头(603019)现在适合购买吗？');
    expect(params.get('send')).toBe('1');
  });

  it('jumps from a recommended stock to the stock query page', async () => {
    renderPage();
    await screen.findByText('国务院部署人工智能+行动');

    fireEvent.click(screen.getByRole('tab', { name: /AI 新闻分析/ }));
    fireEvent.click(screen.getByRole('button', { name: /开始分析/ }));
    await screen.findAllByText('算力基础设施');

    fireEvent.click(screen.getByRole('button', { name: '查询 算力龙头(603019) 行情' }));

    expect(await screen.findByText('stock-query-page')).toBeInTheDocument();
    expect(screen.getByTestId('location')).toHaveTextContent('/stock-query?code=603019');
  });

  it('surfaces API errors through ApiErrorAlert', async () => {
    listDays.mockRejectedValueOnce({ parsedError });

    renderPage();

    expect(await screen.findByText('加载失败')).toBeInTheDocument();
    expect(screen.getByText('CCTV 新闻接口不可用')).toBeInTheDocument();
  });

  it('auto-syncs when nothing is stored yet', async () => {
    listDays.mockResolvedValueOnce({ ...dayList, totalDays: 0, totalItems: 0, days: [] });

    renderPage();

    await waitFor(() => expect(sync).toHaveBeenCalledWith({ days: 2 }));
    expect(await screen.findByText('国务院部署人工智能+行动')).toBeInTheDocument();
  });

  it('releases the syncing state after auto-sync even when StrictMode replays the effect', async () => {
    render(
      <StrictMode>
        <MemoryRouter initialEntries={['/news-analysis?tab=cctv']}>
          <Routes>
            <Route path="/news-analysis" element={<CctvNewsView />} />
          </Routes>
        </MemoryRouter>
      </StrictMode>,
    );

    await waitFor(() => expect(sync).toHaveBeenCalled());
    // 自动同步完成后按钮必须回到可点击的“同步新闻”，不能停在“同步中”。
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /同步新闻/ })).toBeEnabled();
    });
    expect(screen.queryByText('同步中')).not.toBeInTheDocument();
  });
});
