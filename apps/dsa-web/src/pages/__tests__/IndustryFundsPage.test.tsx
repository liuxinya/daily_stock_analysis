import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { UiLanguageProvider } from '../../contexts/UiLanguageContext';
import type { IndustryFundsOverview } from '../../types/industryFunds';
import IndustryFundsPage from '../IndustryFundsPage';

const mockGetOverview = vi.fn();
const mockGetHistory = vi.fn();
const mockSubmitJob = vi.fn();
const mockGetJob = vi.fn();
const mockGetActiveJob = vi.fn();
const mockPush = vi.fn();

vi.mock('../../api/industryFunds', async () => {
  const actual = await vi.importActual<Record<string, unknown>>('../../api/industryFunds');
  return {
    ...actual,
    industryFundsApi: {
      getOverview: () => mockGetOverview(),
      getHistory: () => mockGetHistory(),
      submitJob: () => mockSubmitJob(),
      getJob: (jobId: string) => mockGetJob(jobId),
      getActiveJob: () => mockGetActiveJob(),
      push: () => mockPush(),
      getStatus: async () => ({ enabled: true }),
    },
  };
});

function notGeneratedError(): Error & { code?: string } {
  const error = new Error('not_generated') as Error & { code?: string };
  error.code = 'not_generated';
  return error;
}

function buildOverview(): IndustryFundsOverview {
  return {
    schemaVersion: 'industry-funds-v1',
    tradeDate: '2026-10-16',
    generatedAt: '2026-10-18T20:00:00',
    durationMs: 42000,
    market: {
      score: 52.5,
      verdict: 'neutral',
      verdictLabel: '中性',
      availableWeight: 1,
      renormalized: false,
      thresholds: { bullish: 65, bearish: 45 },
      warnings: [],
      degraded: false,
      degradedReason: '',
      factors: [
        {
          key: 'etf_share',
          label: 'ETF 份额变动',
          weight: 0.3,
          effectiveWeight: 0.3,
          direction: '份额净增越多越偏多',
          normalization: '宽基 ETF 份额 5 日净增额的历史分位',
          rawDisplay: '宽基 ETF 份额 5 日净增 +203.98 亿份',
          basis: '±150 亿份按满量程线性映射',
          score: 18.6,
          maxScore: 20,
          available: true,
          source: '上海证券交易所 query.sse.com.cn',
          asOf: '2026-10-16',
          error: '',
          detail: { combinedDeltaYi: 203.98, method: 'fallback_scale' },
        },
      ],
    },
    industries: {
      weights: { netToday: 0.35, net5D: 0.3, changePct: 0.2, breadth: 0.15 },
      labels: { netToday: '当日净额' },
      normalization: {},
      degraded: false,
      degradedReason: '',
      warnings: [],
      industries: [
        {
          rank: 1,
          name: '军工装备',
          score: 88.5,
          index: 2080.19,
          changePct: 5.61,
          inflow: 130.21,
          outflow: 111.38,
          net: 18.83,
          net5D: -16.37,
          companyCount: 82,
          leader: '晟楠科技',
          leaderChangePct: 29.98,
          leaderPrice: 22.46,
          components: [
            {
              key: 'net_today',
              label: '当日净额',
              weight: 0.35,
              percentile: 0.99,
              score: 99,
              normalization: '当日净额在 90 个行业中的横截面分位',
            },
          ],
        },
      ],
    },
    candidates: {
      candidates: [
        {
          code: '603228',
          name: '景旺电子',
          reasons: ['龙虎榜机构净买'],
          metrics: {
            institutionNet: 1063211723.57,
            industryNet: 18.83,
            changePct: 9.99,
          },
        },
        {
          code: '300045',
          name: '华力创通',
          reasons: ['行业龙头（军工装备 当日领涨）'],
          metrics: { mainNet: -205000000, industryNet: 18.83 },
          flowWarning: '当日资金净流出，注意拉高出货风险',
        },
      ],
      totalPool: 2,
      droppedByIndustry: [],
      droppedCount: 0,
      outflowFlaggedCount: 1,
      sources: { 龙虎榜机构净买: 1, 行业龙头: 1 },
      warnings: [],
      degraded: false,
      degradedReason: '',
    },
    conclusion: {
      summary: '复合信号 52.5 分，判定为中性。',
      directions: [
        { name: '军工装备', stance: 'bullish', reason: '资金净流入居前', evidence: ['当日净额 18.83 亿元'] },
      ],
      stockNotes: [{ code: '603228', name: '景旺电子', note: '机构净买明显' }],
      risks: ['主力资金为推断值，非真实账户身份。'],
      generatedBy: 'llm',
      droppedMentions: [],
      warnings: [],
    },
    sources: {
      industry_fund_flow: {
        key: 'industry_fund_flow',
        label: '行业资金流',
        ok: true,
        source: '同花顺数据中心',
        asOf: '2026-10-16',
        degraded: false,
        degradedReason: '',
        error: null,
        value: {},
        raw: { note: '净额单位：亿元' },
      },
    },
    disclaimers: {
      flow: '「主力资金」是按单笔成交金额分档推断的统计值，并非真实账户身份。',
      investment: '本页内容不构成投资建议。',
    },
    factorLabels: {},
    degraded: false,
    degradedReason: '',
    warnings: [],
    cached: true,
  };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <UiLanguageProvider>
        <IndustryFundsPage />
      </UiLanguageProvider>
    </MemoryRouter>,
  );
}

describe('IndustryFundsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // 界面语言默认取自 navigator.language，在 jsdom 里是 en-US。这里显式锁定中文，
    // 否则断言的到底是文案还是语言就成了运气问题。
    window.localStorage.setItem('dsa.uiLanguage', 'zh');
    mockGetActiveJob.mockResolvedValue(null);
    mockGetHistory.mockResolvedValue([]);
  });

  it('shows the empty state when no snapshot has been generated yet', async () => {
    mockGetOverview.mockRejectedValue(notGeneratedError());

    renderPage();

    expect(await screen.findByText('还没有生成过数据')).toBeInTheDocument();
    expect(screen.getByTestId('industry-funds-refresh')).toBeInTheDocument();
  });

  it('renders all three sections from a cached snapshot', async () => {
    mockGetOverview.mockResolvedValue(buildOverview());

    renderPage();

    // (a) 资金流向
    expect(await screen.findByText('资金流向')).toBeInTheDocument();
    // (b) 复合信号
    expect(screen.getByText('五因子复合信号')).toBeInTheDocument();
    expect(screen.getByText('行业得分排行')).toBeInTheDocument();
    // (c) 结论
    expect(screen.getByText('结论')).toBeInTheDocument();
  });

  it('always shows the fund-flow disclaimer, since the metric is inferred', async () => {
    mockGetOverview.mockResolvedValue(buildOverview());

    renderPage();

    // 免责声明在资金流向区和结论区各出现一次，这里断言"至少出现"而不是唯一匹配。
    const matches = await screen.findAllByText(/「主力资金」是按单笔成交金额分档推断的统计值/);
    expect(matches.length).toBeGreaterThanOrEqual(1);
  });

  it('flags candidates whose fund flow is negative', async () => {
    mockGetOverview.mockResolvedValue(buildOverview());

    renderPage();

    expect(await screen.findByText('当日资金净流出，注意拉高出货风险')).toBeInTheDocument();
    expect(screen.getByText('1 只当日资金净流出')).toBeInTheDocument();
  });

  it('keeps the reason and provenance columns visible for every factor', async () => {
    mockGetOverview.mockResolvedValue(buildOverview());

    renderPage();

    expect(await screen.findByText('归一化依据')).toBeInTheDocument();
    expect(screen.getByText('±150 亿份按满量程线性映射')).toBeInTheDocument();
    expect(screen.getByText('上海证券交易所 query.sse.com.cn')).toBeInTheDocument();
  });

  it('renders the 5-day net and the camelCase ETF-delta detail (regressions on the key mapping)', async () => {
    mockGetOverview.mockResolvedValue(buildOverview());

    renderPage();

    // 5 日净额列：字段名曾是 net5d，与后端 camelCase 后的 net5D 对不上，整列恒显示「—」。
    // 行业得分排行 + 资金流向两张表都会渲染这个值，这里只要求至少命中一次。
    const net5d = await screen.findAllByText('-16.37');
    expect(net5d.length).toBeGreaterThanOrEqual(1);

    // 展开 ETF 因子的「来源与原始数据」，detail 里的 combinedDeltaYi 曾被按 snake_case 读取，
    // 导致下钻明细空白。锁死真实 camelCase 读取路径。
    fireEvent.click(screen.getByLabelText('来源与原始数据'));
    expect(await screen.findByText('203.98 亿份')).toBeInTheDocument();
  });

  it('submits a refresh job and reloads the snapshot when it finishes', async () => {
    mockGetOverview
      .mockRejectedValueOnce(notGeneratedError())
      .mockResolvedValueOnce(buildOverview());
    mockSubmitJob.mockResolvedValue({
      job: {
        jobId: 'j1',
        status: 'queued',
        phase: 'queued',
        phaseLabel: '排队中',
        message: '任务已提交',
        progress: 0,
        createdAt: 0,
        updatedAt: 0,
        result: null,
        error: null,
      },
      reused: false,
      coolingDown: false,
      retryAfterSeconds: 0,
      message: '刷新任务已提交',
    });
    mockGetJob.mockResolvedValue({
      jobId: 'j1',
      status: 'succeeded',
      phase: 'succeeded',
      phaseLabel: '已完成',
      message: '刷新完成',
      progress: 1,
      createdAt: 0,
      updatedAt: 0,
      result: null,
      error: null,
    });

    renderPage();
    const refreshButton = await screen.findByTestId('industry-funds-refresh');
    fireEvent.click(refreshButton);

    await waitFor(() => expect(mockSubmitJob).toHaveBeenCalledTimes(1));
    // 轮询到 succeeded 后应当重新读取缓存并渲染三段内容。
    expect(await screen.findByText('资金流向', {}, { timeout: 8000 })).toBeInTheDocument();
  });

  it('surfaces the cooldown message instead of silently doing nothing', async () => {
    mockGetOverview.mockRejectedValue(notGeneratedError());
    mockSubmitJob.mockResolvedValue({
      job: null,
      reused: false,
      coolingDown: true,
      retryAfterSeconds: 42,
      message: '距上次刷新不足 60 秒，请 42 秒后再试',
    });

    renderPage();
    fireEvent.click(await screen.findByTestId('industry-funds-refresh'));

    expect(await screen.findByText('距上次刷新不足 60 秒，请 42 秒后再试')).toBeInTheDocument();
  });

  it('explains when the push channel is not enabled', async () => {
    mockGetOverview.mockResolvedValue(buildOverview());
    mockPush.mockResolvedValue({
      ok: false,
      skipped: true,
      message: '推送未启用：请把 .env 里的 INDUSTRY_FUNDS_PUSH_ENABLED 设为 true',
      tradeDate: '2026-10-16',
    });

    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: /推送到通知渠道/ }));

    expect(
      await screen.findByText(/推送未启用/),
    ).toBeInTheDocument();
  });

  it('shows a freshness hint when the cached snapshot is past its TTL window', async () => {
    mockGetOverview.mockResolvedValue({
      ...buildOverview(),
      stale: true,
      cacheTtlMinutes: 30,
      ageMinutes: 90,
    });

    renderPage();

    // 文案里会带上 90 分钟 / 30 分钟窗口；只断言稳定的「新鲜度窗口」短语，避开数字格式的脆弱匹配。
    expect(await screen.findByText(/新鲜度窗口/)).toBeInTheDocument();
  });

  it('does not show the freshness hint for a fresh snapshot', async () => {
    // 默认 fixture 不带 stale 字段，等价于「新鲜」，不应出现过时提示。
    mockGetOverview.mockResolvedValue(buildOverview());

    renderPage();

    await screen.findByText('资金流向');
    expect(screen.queryByText(/新鲜度窗口/)).not.toBeInTheDocument();
  });
});
