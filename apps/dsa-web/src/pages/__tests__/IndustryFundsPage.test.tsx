import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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
const mockGetFlowDates = vi.fn();
const mockGetFlows = vi.fn();
const mockGetHotMoney = vi.fn();
const mockGetSeatDetail = vi.fn();

vi.mock('../../api/industryFunds', async () => {
  const actual = await vi.importActual<Record<string, unknown>>('../../api/industryFunds');
  return {
    ...actual,
    industryFundsApi: {
      getFlowDates: (days: number) => mockGetFlowDates(days),
      getFlows: (days: number) => mockGetFlows(days),
      getHotMoney: (days: number, seats: number) => mockGetHotMoney(days, seats),
      getSeatDetail: (codes: string[], date: string) => mockGetSeatDetail(codes, date),
      getOverview: (tradeDate?: string) => mockGetOverview(tradeDate),
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
    expect(await screen.findByRole('heading', { name: '资金流向' })).toBeInTheDocument();
    // (b) 复合信号
    expect(screen.getByText('五因子复合信号')).toBeInTheDocument();
    expect(screen.getByText('行业得分排行')).toBeInTheDocument();
    // (c) 结论
    expect(screen.getAllByText('结论').length).toBeGreaterThan(0);
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
    expect(await screen.findByRole('heading', { name: '资金流向' }, { timeout: 8000 })).toBeInTheDocument();
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

    await screen.findByRole('heading', { name: '资金流向' });
    expect(screen.queryByText(/新鲜度窗口/)).not.toBeInTheDocument();
  });

  it('toggles the fund-flow section to a heatmap with a size-metric selector', async () => {
    mockGetOverview.mockResolvedValue(buildOverview());

    renderPage();
    await screen.findByRole('heading', { name: '资金流向' });

    // 切到热力图：出现面积指标下拉、可选的主力口径指标，以及数据口径说明。
    fireEvent.click(screen.getByRole('button', { name: /热力图/ }));
    expect(await screen.findByText('面积指标')).toBeInTheDocument();
    expect(screen.getByRole('option', { name: '主力成交额(流入+流出)' })).toBeInTheDocument();
    // 口径说明必须明说当前数据源没有全口径成交额/成交量/市值，避免误读为真实成交数据。
    expect(screen.getByText(/暂无全口径成交额\/成交量\/市值/)).toBeInTheDocument();
  });
});

describe('资金流向 tabs', () => {
  const flowDates = {
    days: [
      { date: '2026-11-13', hasOverview: true },
      { date: '2026-11-16', hasOverview: false },
    ],
    today: '2026-11-16',
  };
  const flows = {
    days: ['2026-11-13', '2026-11-16'],
    nationalTeam: {
      days: [
        { date: '2026-11-13', netShares: -17.28, netAmount: -51.21, etfCount: 2, turnover: 300, turnoverRatio: 0.71, etfs: [
          { code: '510300', name: '沪深300ETF华泰柏瑞', shares: 456.2, deltaShares: -2.0, deltaAmount: -8.2, turnover: 34.9, turnoverRatio: 0.9 },
        ] },
        { date: '2026-11-16', netShares: null, netAmount: null, etfCount: 0, turnover: null, turnoverRatio: null, sseMissing: true, etfs: [] },
      ],
      series: [
        { date: '2026-11-13', netShares: -17.28, netAmount: -51.21, turnoverRatio: 0.71 },
        { date: '2026-11-16', netShares: null, netAmount: null, turnoverRatio: null },
      ],
      inflowStreak: 0, asOf: '2026-11-13', degraded: false, degradedReason: '', coverage: { sse: '沪市份额可回溯', szse: '深市只看当日' },
      szse: { date: '2026-11-16', etfs: [{ code: '159919', name: '沪深300ETF嘉实', shares: 88.0 }] },
    },
    institutions: {
      days: [
        { date: '2026-11-13', net: -8.65, buy: 20, sell: 28.65, count: 2, stocks: [
          { code: '600001', name: '甲股', close: 11, changePct: 9.9, buyerCount: 3, sellerCount: 1, buy: 1.5, sell: 0.2, net: 1.3, netRatio: 6.5, turnoverRate: 12.3, reason: '日涨幅偏离值达 7%' },
          { code: '600002', name: '乙股', close: 8, changePct: -3.2, buyerCount: 0, sellerCount: 2, buy: 0, sell: 1.0, net: -1.0, netRatio: -2.1, turnoverRate: 8.0, reason: '日跌幅偏离值达 7%' },
        ] },
        { date: '2026-11-16', net: null, buy: null, sell: null, count: 0, pending: true, stocks: [] },
      ],
      series: [
        { date: '2026-11-13', net: -8.65, count: 2 },
        { date: '2026-11-16', net: null, count: 0 },
      ],
      asOf: '2026-11-13', degraded: false, degradedReason: '',
    },
    generatedAt: '2026-11-16T13:40:00', durationMs: 7000,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    mockGetOverview.mockResolvedValue(buildOverview());
    mockGetHistory.mockResolvedValue([]);
    mockGetActiveJob.mockRejectedValue(new Error('none'));
    mockGetFlowDates.mockResolvedValue(flowDates);
    mockGetFlows.mockResolvedValue(flows);
  });

  it('renders the three top-level tabs with 总览 selected first', async () => {
    renderPage();
    expect(await screen.findByTestId('flow-tab-overview')).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('flow-tab-institutions')).toHaveAttribute('aria-selected', 'false');
    expect(screen.getByTestId('flow-tab-national-team')).toBeInTheDocument();
  });

  it('switches to the institutions tab and opens a drawer for one stock', async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId('flow-tab-institutions'));
    const row = await screen.findByTestId('inst-row-600001');
    expect(screen.getByText('甲股')).toBeInTheDocument();
    fireEvent.click(row);
    expect((await screen.findAllByText(/买卖机构数|Seats/)).length).toBeGreaterThan(0);
    expect((await screen.findAllByText(/买 3 家 \/ 卖 1 家|3 buying \/ 1 selling/)).length).toBeGreaterThan(0);
  });

  it('marks an undisclosed dragon-tiger day as pending instead of zero', async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId('flow-tab-institutions'));
    fireEvent.click(await screen.findByTestId('flow-date-2026-11-16'));
    expect((await screen.findAllByText(/还没披露|not published yet/i)).length).toBeGreaterThan(0);
  });

  it('shows the 汪汪队 tab with share changes and the missing-SSE notice', async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId('flow-tab-national-team'));
    expect(await screen.findByText('沪深300ETF华泰柏瑞')).toBeInTheDocument();
    fireEvent.click(await screen.findByTestId('flow-date-2026-11-16'));
    await waitFor(() => expect(screen.getByTestId('flow-date-2026-11-16')).toHaveAttribute('aria-selected', 'true'));
    expect((await screen.findAllByText(/还没更新|not out yet/i)).length).toBeGreaterThan(0);
  });






  it('sorts by any seat column and opens the stock chart from the name', async () => {
    const seats = [
      { code: 'S1', codes: ['S1'], name: '某营业部一', short: '一部', alias: null, buy: 5, sell: 1, net: 4, days: 1 },
      { code: 'S2', codes: ['S2'], name: '某营业部二', short: '二部', alias: null, buy: 4, sell: 1, net: 3, days: 1 },
    ];
    mockGetHotMoney.mockResolvedValue({
      days: ['2026-11-13'], seats, allSeats: seats,
      dates: [{ date: '2026-11-13', total: 2, rows: [
        { code: '600001', name: '甲股', buy: 1, sell: 4, net: -3, changePct: 1.1,
          cells: { S1: { buy: 1, sell: 4, net: -3 }, S2: { buy: 3, sell: 0, net: 3 } } },
        { code: '600002', name: '乙股', buy: 4, sell: 0, net: 4, changePct: -1.1,
          cells: { S1: { buy: 4, sell: 0, net: 4 }, S2: { buy: 1, sell: 1, net: 0 } } },
      ] }],
      rowLimit: 60, degraded: false, degradedReason: '', coverage: '龙虎榜上榜个股',
    });
    renderPage();
    fireEvent.click(await screen.findByTestId('flow-tab-hot-money'));
    fireEvent.click(await screen.findByTestId('flow-date-2026-11-13'));
    const order = () => screen.getAllByTestId(/^hm-row-/).map((el) => el.getAttribute('data-testid'));
    await waitFor(() => expect(order()).toEqual(['hm-row-600002', 'hm-row-600001']));   // 合计净买降序
    // 按 S1 列降序：甲股 -3 在乙股 +4 之后（仍然 4 在前），切升序后甲股在前
    fireEvent.click(screen.getByTestId('hm-sort-S1'));
    await waitFor(() => expect(order()).toEqual(['hm-row-600002', 'hm-row-600001']));
    fireEvent.click(screen.getByTestId('hm-sort-S1'));
    await waitFor(() => expect(order()).toEqual(['hm-row-600001', 'hm-row-600002']));
    // 第一列不再显示代码；名称是打开行情的按钮
    const row = screen.getByTestId('hm-row-600001');
    expect(within(row).queryByText('600001')).toBeNull();
    const open = screen.getByTestId('hm-open-600001');
    expect(open).toHaveTextContent('甲股');
    fireEvent.click(open);       // 不抛错即可（全局 store 打开行情弹窗）
  });


  it('fetches a seat detail once and reuses it on later hovers', async () => {
    mockGetSeatDetail.mockResolvedValue({
      date: '2026-11-16', degraded: false, degradedReason: '',
      stocks: [{ code: '600009', name: '丙股', buy: 1, sell: 0.2, net: 0.8, changePct: 2.2 }],
    });
    const seat = { code: 'S9', codes: ['S9'], name: '某证券深圳分公司', short: '某深圳', alias: '孙哥', buy: 1, sell: 0.2, net: 0.8, days: 1 };
    mockGetHotMoney.mockResolvedValue({
      days: ['2026-11-16'], seats: [seat], allSeats: [seat],
      dates: [{ date: '2026-11-16', total: 1, rows: [{
        code: '600009', name: '丙股', buy: 1, sell: 0.2, net: 0.8, changePct: 2.2,
        cells: { S9: { buy: 1, sell: 0.2, net: 0.8 } },
      }] }],
      rowLimit: 60, degraded: false, degradedReason: '', coverage: '龙虎榜上榜个股',
    });
    renderPage();
    fireEvent.click(await screen.findByTestId('flow-tab-hot-money'));
    fireEvent.click(await screen.findByTestId('flow-date-2026-11-16'));
    const chip = await screen.findByTestId('hm-seat-S9');
    const amount = within(chip).getByText('+0.80');
    fireEvent.mouseEnter(amount);
    await waitFor(() => expect(mockGetSeatDetail).toHaveBeenCalledTimes(1));
    fireEvent.mouseLeave(amount);
    fireEvent.mouseEnter(amount);          // 再悬停一次：走缓存，不再请求
    await new Promise((resolve) => { setTimeout(resolve, 50); });
    expect(mockGetSeatDetail).toHaveBeenCalledTimes(1);
  });

  it('shows the change percent column and loads seat detail on hover', async () => {
    mockGetSeatDetail.mockResolvedValue({
      date: '2026-11-13', degraded: false, degradedReason: '',
      stocks: [{ code: '600001', name: '甲股', buy: 3, sell: 1, net: 2, changePct: -9.9 }],
    });
    const seat = { code: 'S1', codes: ['S1'], name: '某证券上海分公司', short: '某上海', alias: '章盟主', buy: 3, sell: 1, net: 2, days: 1 };
    mockGetHotMoney.mockResolvedValue({
      days: ['2026-11-13'], seats: [seat], allSeats: [seat],
      dates: [{ date: '2026-11-13', total: 1, rows: [{
        code: '600001', name: '甲股', buy: 3, sell: 1, net: 2, changePct: -9.9,
        cells: { S1: { buy: 3, sell: 1, net: 2 } },
      }] }],
      rowLimit: 60, degraded: false, degradedReason: '', coverage: '龙虎榜上榜个股',
    });
    renderPage();
    fireEvent.click(await screen.findByTestId('flow-tab-hot-money'));
    fireEvent.click(await screen.findByTestId('flow-date-2026-11-13'));
    const row = await screen.findByTestId('hm-row-600001');
    expect(within(row).getByText('-9.90%')).toBeInTheDocument();          // 涨跌幅列
    // 悬停席位金额 → 拉该席位当天明细并展示
    const chip = await screen.findByTestId('hm-seat-S1');
    fireEvent.mouseEnter(within(chip).getByText('+2.00'));
    await waitFor(() => expect(mockGetSeatDetail).toHaveBeenCalledWith(['S1'], '2026-11-13'));
    expect(await screen.findByText('格式：买入 / 卖出 净买（亿元）')).toBeInTheDocument();
  });

  it('sorts the matrix by total net buy and toggles direction', async () => {
    mockGetHotMoney.mockResolvedValue({
      days: ['2026-11-13'],
      seats: [{ code: 'S1', name: '某证券上海分公司', short: '某上海', alias: '章盟主', buy: 3, sell: 1, net: 2, days: 1 }],
      allSeats: [{ code: 'S1', name: '某证券上海分公司', short: '某上海', alias: '章盟主', buy: 3, sell: 1, net: 2, days: 1 }],
      dates: [{ date: '2026-11-13', total: 2, rows: [
        { code: '600001', name: '甲股', buy: 3, sell: 1, net: 2, cells: { S1: { buy: 3, sell: 1, net: 2 } } },
        { code: '600002', name: '乙股', buy: 1, sell: 4, net: -3, cells: { S1: { buy: 1, sell: 4, net: -3 } } },
      ] }],
      daily: [{ date: '2026-11-13', seats: 190, buy: 67, sell: 59, net: 8, netRatio: 0.12, listed: 70,
        upShare: 0.57, upBuyShare: 0.46, downShare: 0.43, downBuyShare: 0.54, limitShare: 0.36 }],
      verdict: { text: '活跃席位基本持平；落在下跌股上的买入额从 17% 升到 54%，从追涨转成接跌。', tags: ['转为接跌'] },
      rowLimit: 60, degraded: false, degradedReason: '', coverage: '龙虎榜上榜个股',
    });
    renderPage();
    fireEvent.click(await screen.findByTestId('flow-tab-hot-money'));
    fireEvent.click(await screen.findByTestId('flow-date-2026-11-13'));
    const order = () => screen.getAllByTestId(/^hm-row-/).map((el) => el.getAttribute('data-testid'));
    await waitFor(() => expect(order()).toEqual(['hm-row-600001', 'hm-row-600002']));   // 默认降序
    fireEvent.click(screen.getByTestId('hm-sort-toggle'));
    await waitFor(() => expect(order()).toEqual(['hm-row-600002', 'hm-row-600001']));   // 升序
    expect(screen.getByTestId('hm-sort-toggle')).toHaveTextContent('▲');
  });

  it('collapses the seat chips to two rows and expands on demand', async () => {
    const seats = Array.from({ length: 12 }, (_, i) => ({
      code: `S${i}`, name: `营业部${i}`, short: `营业部${i}`, alias: i === 0 ? '章盟主' : null,
      buy: 10 - i, sell: 1, net: 9 - i, days: 1,
    }));
    mockGetHotMoney.mockResolvedValue({
      days: ['2026-11-13'], seats, allSeats: seats,
      dates: [{ date: '2026-11-13', total: 0, rows: [] }],
      daily: [{ date: '2026-11-13', seats: 190, buy: 67, sell: 59, net: 8, netRatio: 0.12, listed: 70,
        upShare: 0.57, upBuyShare: 0.46, downShare: 0.43, downBuyShare: 0.54, limitShare: 0.36 }],
      verdict: { text: '活跃席位基本持平；落在下跌股上的买入额从 17% 升到 54%，从追涨转成接跌。', tags: ['转为接跌'] },
      rowLimit: 60, degraded: false, degradedReason: '', coverage: '龙虎榜上榜个股',
    });
    renderPage();
    fireEvent.click(await screen.findByTestId('flow-tab-hot-money'));
    const toggle = await screen.findByTestId('hm-seats-toggle');
    expect(toggle).toHaveTextContent('12');
    fireEvent.click(toggle);
    expect(screen.getByTestId('hm-seats-toggle')).not.toHaveTextContent('12');
  });

  it('renders the hot-money matrix with stocks as rows and seats as columns', async () => {
    mockGetHotMoney.mockResolvedValue({
      days: ['2026-11-13'],
      seats: [
        { code: 'S1', name: '某证券上海分公司', short: '某上海', buy: 3, sell: 1, net: 2, days: 1 },
        { code: 'S2', name: '某证券深圳分公司', short: '某深圳', buy: 0.5, sell: 0.1, net: 0.4, days: 1 },
      ],
      dates: [{ date: '2026-11-13', total: 1, rows: [{
        code: '600001', name: '甲股', buy: 3.5, sell: 1.1, net: 2.4,
        cells: { S1: { buy: 3, sell: 1, net: 2 }, S2: { buy: 0.5, sell: 0.1, net: 0.4 } },
      }] }],
      daily: [{ date: '2026-11-13', seats: 190, buy: 67, sell: 59, net: 8, netRatio: 0.12, listed: 70,
        upShare: 0.57, upBuyShare: 0.46, downShare: 0.43, downBuyShare: 0.54, limitShare: 0.36 }],
      verdict: { text: '活跃席位基本持平；落在下跌股上的买入额从 17% 升到 54%，从追涨转成接跌。', tags: ['转为接跌'] },
      rowLimit: 60, degraded: false, degradedReason: '', coverage: '龙虎榜上榜个股',
    });
    renderPage();
    fireEvent.click(await screen.findByTestId('flow-tab-hot-money'));
    expect(await screen.findByTestId('hot-money-table')).toBeInTheDocument();
    fireEvent.click(await screen.findByTestId('flow-date-2026-11-13'));
    const row = await screen.findByTestId('hm-row-600001');
    expect(within(row).getByText('甲股')).toBeInTheDocument();
    expect(within(row).getByText('+2.00')).toBeInTheDocument();     // S1 净买
    expect(within(row).getByText('+0.40')).toBeInTheDocument();     // S2 净买
    // 悬停股票名出气泡，列出每个席位的买入 / 卖出（不再需要点开抽屉）
    fireEvent.mouseEnter(within(row).getByText('甲股'));
    const bubble = await screen.findByRole('tooltip');
    expect(within(bubble).getAllByText('某上海').length).toBeGreaterThan(0);
    expect(within(bubble).getByText('+2.00')).toBeInTheDocument();
  });

  it('shows the hot-money verdict table with explained headers', async () => {
    mockGetHotMoney.mockResolvedValue({
      days: ['2026-11-13'],
      seats: [{ code: 'S1', name: '某证券上海分公司', short: '某上海', alias: '章盟主', buy: 3, sell: 1, net: 2, days: 1 }],
      dates: [{ date: '2026-11-13', total: 1, rows: [
        { code: '600001', name: '甲股', buy: 3, sell: 1, net: 2, cells: { S1: { buy: 3, sell: 1, net: 2 } } },
      ] }],
      daily: [{ date: '2026-11-13', seats: 190, buy: 67, sell: 59, net: 8, netRatio: 0.12, listed: 70,
        upShare: 0.57, upBuyShare: 0.46, downShare: 0.43, downBuyShare: 0.54, limitShare: 0.36 }],
      verdict: { text: '活跃席位基本持平；落在下跌股上的买入额从 17% 升到 54%，从追涨转成接跌。', tags: ['转为接跌'] },
      rowLimit: 60, degraded: false, degradedReason: '', coverage: '龙虎榜上榜个股',
    });
    renderPage();
    fireEvent.click(await screen.findByTestId('flow-tab-hot-money'));
    expect(await screen.findByTestId('hm-verdict')).toHaveTextContent(/接跌/);
    const table = await screen.findByTestId('hm-daily-table');
    expect(within(table).getByText('上涨股占比')).toBeInTheDocument();
    // 表头问号：悬停给出该指标的口径
    const tips = within(table).getAllByTestId('help-tip');
    expect(tips.length).toBeGreaterThan(3);
    fireEvent.mouseEnter(tips[0]);
    expect(await screen.findByRole('tooltip')).toHaveTextContent(/营业部席位/);
  });

  it('collapses and expands the fixed page nav', async () => {
    renderPage();
    expect(await screen.findByTestId('flow-nav-if-ov-signal')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('flow-nav-toggle'));
    await waitFor(() => expect(screen.queryByTestId('flow-nav-if-ov-signal')).not.toBeInTheDocument());
    expect(screen.getByTestId('flow-nav-toggle')).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(screen.getByTestId('flow-nav-toggle'));
    expect(await screen.findByTestId('flow-nav-if-ov-signal')).toBeInTheDocument();
  });

  it('jumps to a section from the fixed page nav', async () => {
    const scrolled: string[] = [];
    Element.prototype.scrollIntoView = vi.fn(function (this: Element) {
      scrolled.push(this.id);
    }) as unknown as typeof Element.prototype.scrollIntoView;
    renderPage();
    fireEvent.click(await screen.findByTestId('flow-nav-if-ov-signal'));
    expect(scrolled).toContain('if-ov-signal');
  });

  it('shows the hot-money nickname in the header when one is mapped', async () => {
    mockGetHotMoney.mockResolvedValue({
      days: ['2026-11-13'],
      seats: [{ code: 'S1', name: '国泰海通证券股份有限公司上海长宁区江苏路证券营业部', short: '国泰海通上海长宁区江苏路',
                alias: '章盟主', buy: 3, sell: 1, net: 2, days: 1 }],
      dates: [{ date: '2026-11-13', total: 1, rows: [{
        code: '600001', name: '甲股', buy: 3, sell: 1, net: 2, cells: { S1: { buy: 3, sell: 1, net: 2 } },
      }] }],
      daily: [{ date: '2026-11-13', seats: 190, buy: 67, sell: 59, net: 8, netRatio: 0.12, listed: 70,
        upShare: 0.57, upBuyShare: 0.46, downShare: 0.43, downBuyShare: 0.54, limitShare: 0.36 }],
      verdict: { text: '没有明显转向。', tags: [] },
      rowLimit: 60, degraded: false, degradedReason: '', coverage: '龙虎榜上榜个股',
    });
    renderPage();
    fireEvent.click(await screen.findByTestId('flow-tab-hot-money'));
    fireEvent.click(await screen.findByTestId('flow-date-2026-11-13'));
    const head = await screen.findByTestId('hm-head-S1');
    expect(within(head).getByText('章盟主')).toBeInTheDocument();       // 表头用昵称
    expect(head).toHaveAttribute('title', '国泰海通证券股份有限公司上海长宁区江苏路证券营业部');  // 悬停看营业部
  });

  it('keeps the selected date when switching back to the overview tab', async () => {
    mockGetOverview.mockImplementation(async (tradeDate?: string) => ({
      ...buildOverview(),
      tradeDate: tradeDate ?? '2026-11-16',
    }));
    renderPage();
    fireEvent.click(await screen.findByTestId('flow-tab-institutions'));
    fireEvent.click(await screen.findByTestId('flow-date-2026-11-13'));
    await waitFor(() => expect(screen.getByTestId('flow-date-2026-11-13')).toHaveAttribute('aria-selected', 'true'));
    fireEvent.click(screen.getByTestId('flow-tab-overview'));
    await waitFor(() => expect(mockGetOverview).toHaveBeenCalledWith('2026-11-13'));
  });

  it('reloads the flow series after a manual refresh finishes', async () => {
    mockGetActiveJob.mockRejectedValue(new Error('none'));
    mockSubmitJob.mockResolvedValue({ job: { jobId: 'j1', status: 'queued' } });
    mockGetJob.mockResolvedValue({ jobId: 'j1', status: 'succeeded' });
    renderPage();
    await waitFor(() => expect(mockGetFlows).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByTestId('industry-funds-refresh'));
    await waitFor(() => expect(mockGetFlows).toHaveBeenCalledTimes(2), { timeout: 5000 });
  });

  it('asks for a refresh instead of pretending a missing historical overview exists', async () => {
    mockGetOverview.mockImplementation(async (tradeDate?: string) => {
      if (tradeDate === '2026-11-16') throw notGeneratedError();
      return buildOverview();
    });
    renderPage();
    fireEvent.click(await screen.findByTestId('flow-date-2026-11-16'));
    expect(await screen.findByText(/2026-11-16 没有总览快照|No overview snapshot for 2026-11-16/)).toBeInTheDocument();
  });
});
