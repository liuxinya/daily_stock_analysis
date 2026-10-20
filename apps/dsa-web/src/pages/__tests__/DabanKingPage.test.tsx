import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { UiLanguageProvider } from '../../contexts/UiLanguageContext';
import type { DabanLane, DabanOverview, DabanPick } from '../../types/daban';
import DabanKingPage from '../DabanKingPage';

const mockGetOverview = vi.fn();
const mockGetHistory = vi.fn();
const mockSubmitJob = vi.fn();
const mockGetJob = vi.fn();
const mockGetActiveJob = vi.fn();
const mockPush = vi.fn();

vi.mock('../../api/daban', async () => {
  const actual = await vi.importActual<Record<string, unknown>>('../../api/daban');
  return {
    ...actual,
    dabanApi: {
      getOverview: (date?: string) => mockGetOverview(date),
      getHistory: () => mockGetHistory(),
      submitJob: (date?: string) => mockSubmitJob(date),
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

function pick(overrides: Partial<DabanPick> = {}): DabanPick {
  return {
    code: '002347',
    name: '泰尔股份',
    mode: 'M2',
    modeLabel: '弱转强',
    role: 'front',
    roleLabel: '前排',
    themeName: '机器人',
    origin: 'zt',
    action: 'actionable',
    actionLabel: '可执行',
    score: 83.8,
    grade: 'A',
    ruleScore: 80.8,
    llmAdjust: 3,
    llmAdjustReason: '机器人故事空间大',
    riskPenalty: 0,
    dimensions: [{ key: 'A', label: '题材地位', score: 25, max: 25, reason: '主线第 1 名', principleIds: ['C13'] }],
    risks: [],
    facts: ['涨停原因：工业机器人+冶金装备'],
    reasons: [{ text: '今日炸板 1 次后回封，属于弱板', principleIds: ['K27'] }],
    llmNote: null,
    triggers: [{ text: '竞价由负转正，量能 ≥ 昨日同期 3 倍', principleIds: ['K28'] }],
    abandon: [{ text: '竞价大幅高开放弃', principleIds: ['K28'] }],
    positionPct: 25,
    price: 6.61,
    changePct: 10.0,
    boards: 1,
    limitUpType: '换手板',
    notes: [],
    alsoModes: [],
    consensus: true,
    ...overrides,
  };
}

function lane(style: 'chen' | 'kobe', overrides: Partial<DabanLane> = {}): DabanLane {
  return {
    style,
    label: style === 'chen' ? '陈小群打法' : '92科比打法',
    stageKey: style === 'chen' ? 'ferment' : 'main_rise',
    stageLabel: style === 'chen' ? '发酵期' : '主升',
    positionCap: 50,
    positionCapLabel: style === 'chen' ? '30–50%' : '分 4 票，每票 ≤25%',
    positionUsed: 25,
    flat: false,
    flatMessage: '',
    flatPrinciples: [],
    actionable: [pick()],
    trial: [],
    watch: [],
    sellRules: [{ text: style === 'chen' ? '触及 5% 止损线立刻离场' : '第二天不涨停基本都走', principleIds: [style === 'chen' ? 'C08' : 'K21'] }],
    commentary: '',
    commentaryPrinciples: [],
    ...overrides,
  };
}

function buildOverview(overrides: Partial<DabanOverview> = {}): DabanOverview {
  return {
    schemaVersion: 'daban-v1',
    kind: 'full',
    tradeDate: '2026-10-20',
    previousTradeDate: '2026-10-19',
    generatedAt: '2026-10-20T17:20:00',
    durationMs: 77800,
    intraday: false,
    metrics: {
      ztCount: 52, zbCount: 29, touchedCount: 81, zabRate: 0.358, dtCount: 6, lianbanCount: 14, maxHeight: 4,
      secondHeight: 3, leaderSealRatio: 0.024, firstBoardCount: 38, yesterdayZtCount: 46, promotionRate: 0.304,
      tiers: [], yztPremium: 2.77, yztUpRatio: 0.5, bigLossCount: 5, followRate: 0.4,
      delta: { ztCount: 6, dtCount: -16, maxHeight: 1, bigLossCount: -10, promotionRate: 0.118, lianbanCount: 3 },
    },
    stage: {
      key: 'ferment', label: '发酵期', hitRate: 0.667, transitional: false, secondary: null, secondaryLabel: '',
      override: null, fallback: false,
      conditions: [{ stage: 'ferment', label: '发酵期', hitRate: 0.667, items: [
        { key: 'zt', label: '涨停家数', value: 52, threshold: '50–80', hit: true },
      ] }],
    },
    kobeStage: { key: 'main_rise', label: '主升', fallDay: 0, gateKey: 'main_rise' },
    ladder: [{
      boards: 4, label: '4 板', count: 1, promotion: { from: 3, label: '3进4', base: 2, promoted: 1, rate: 0.5 },
      stocks: [{ code: '003029', name: '吉大正元', theme: '', roleLabel: '总龙头', reason: '低空安全+商用密码',
        firstTime: '092500', limitUpType: 'T字板', openNum: 5, sealRatio: 0.024, highDays: '4天4板' }],
    }],
    themes: [{
      key: '机器人', name: '机器人', tags: ['机器人'], rank: 1, isMain: true, ztCount: 21, lianbanCount: 3, maxBoards: 3,
      days: 7, status: 'rise', statusLabel: '主升', novelty: 'same', noveltyLabel: '老题材', premium: 3.9,
      leader: { code: '603278', name: '大业股份', boards: 3 }, members: [], storySpace: 'big', storyLabel: '大',
      storyReason: '政策催化', storySource: 'llm', news: [], newsCount: 30,
    }],
    verdict: {
      preferred: 'both', label: '两派共识日', votes: { chen: 2, kobe: 1 }, overlap: 0.75, summary: '两派信号接近',
      signals: [{ key: 'c4', style: 'chen', label: '处于回暖→发酵→高潮的黄金窗口', hit: true, available: true,
        evidence: '当前 发酵期', principleIds: ['C02'] }],
      modelOpinion: { agree: true, preferred: 'both', reason: '发酵期与主升并不冲突' },
    },
    lanes: { chen: lane('chen'), kobe: lane('kobe') },
    consensusCodes: ['002347'],
    excluded: [{ code: '600178', name: '东安动力', boards: 3, roleLabel: '中位股',
      reason: '中位股：3 板，处在 3 板到最高板（4 板）之间', principleIds: ['K11'] }],
    review: null,
    trackRecord: { chen: { days: 0, picks: 0, avg: null, limitUpRate: null }, kobe: { days: 0, picks: 0, avg: null, limitUpRate: null } },
    conclusion: { summary: '今日情绪处于发酵期', risks: ['机器人已第 7 天'], generatedBy: 'llm', model: 'stub', provider: 'stub', droppedMentions: [] },
    principles: {
      K11: { author: '92科比', ref: '#12', title: '中位股风险最大', quote: '切记切记不要去做中位股' },
      K21: { author: '92科比', ref: '#25', title: '第二天不涨停就走', quote: '第二天不涨停，基本都会走' },
      K27: { author: '92科比', ref: '#32/#33', title: '弱转强', quote: '当天烂板一般第二天竞价会很低' },
      K28: { author: '92科比', ref: '#34', title: '弱转强不要竞价高开', quote: '弱转强不喜欢竞价高开' },
      C02: { author: '陈小群', ref: '二', title: '黄金窗口重仓、退潮空仓', quote: '退潮期空仓是最佳策略' },
      C08: { author: '陈小群', ref: '五.1', title: '5%止损', quote: '触及止损线立刻离场' },
      C10: { author: '陈小群', ref: '五.3', title: '不逆势操作', quote: '退潮期坚决空仓' },
      C13: { author: '陈小群', ref: '六.2/六.3', title: '只做龙头不做杂毛', quote: '只做龙头不做杂毛' },
    },
    modeLabels: { M2: '弱转强' },
    styleLabels: { chen: '陈小群打法', kobe: '92科比打法' },
    sources: {},
    disclaimers: { investment: '不构成任何投资建议', data: '统计口径说明' },
    degraded: false,
    warnings: [],
    cached: true,
    storedAt: null,
    ...overrides,
  };
}

function renderPage() {
  return render(
    <MemoryRouter>
      <UiLanguageProvider>
        <DabanKingPage />
      </UiLanguageProvider>
    </MemoryRouter>
  );
}

describe('DabanKingPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // jsdom 的 navigator.language 是 en-US，显式锁定中文，断言才是在测文案而不是语言。
    window.localStorage.setItem('dsa.uiLanguage', 'zh');
    mockGetHistory.mockResolvedValue([]);
    mockGetActiveJob.mockResolvedValue(null);
  });

  it('shows the empty state and submits a job when nothing has been generated', async () => {
    mockGetOverview.mockRejectedValue(notGeneratedError());
    mockSubmitJob.mockResolvedValue({ job: null, reused: false, coolingDown: true, retryAfterSeconds: 30, message: '请 30 秒后再试' });
    renderPage();

    expect(await screen.findByText('还没有生成过复盘')).toBeInTheDocument();
    fireEvent.click(screen.getAllByTestId('daban-generate')[0]);
    await waitFor(() => expect(mockSubmitJob).toHaveBeenCalled());
    expect(await screen.findByText('请 30 秒后再试')).toBeInTheDocument();
  });

  it('renders cycle, verdict, both lanes and cited reasons', async () => {
    mockGetOverview.mockResolvedValue(buildOverview());
    renderPage();

    expect(await screen.findByText('发酵期')).toBeInTheDocument();
    expect(screen.getByText('两派共识日')).toBeInTheDocument();
    const chenLane = screen.getByRole('region', { name: '陈小群打法' });
    const kobeLane = screen.getByRole('region', { name: '92科比打法' });
    expect(within(chenLane).getByText('泰尔股份')).toBeInTheDocument();
    expect(within(kobeLane).getByText('泰尔股份')).toBeInTheDocument();
    // 理由带出处标签，两派共识徽标出现。
    expect(within(kobeLane).getAllByText(/K27/).length).toBeGreaterThan(0);
    expect(within(chenLane).getAllByText('两派共识').length).toBeGreaterThan(0);
    // 排除名单与免责声明。
    expect(screen.getByText(/排除名单/)).toBeInTheDocument();
    expect(screen.getByText('不构成任何投资建议')).toBeInTheDocument();
  });

  it('shows the Chen lane as flat on a retreat day while the Kobe lane still has a trial pick', async () => {
    mockGetOverview.mockResolvedValue(
      buildOverview({
        stage: { ...buildOverview().stage, key: 'retreat', label: '退潮期' },
        verdict: { ...buildOverview().verdict, preferred: 'kobe', label: '今日更推荐：92科比打法' },
        lanes: {
          chen: lane('chen', {
            stageLabel: '退潮期', positionCap: 0, positionCapLabel: '0（空仓）', flat: true,
            flatMessage: '退潮期空仓是最佳策略', flatPrinciples: ['C02', 'C10'], actionable: [],
          }),
          kobe: lane('kobe', {
            stageLabel: '主跌第 2 天', positionCap: 10, positionCapLabel: '≤10%（尾盘试错）', actionable: [],
            trial: [pick({ action: 'trial', actionLabel: '试错', consensus: false, positionPct: 10 })],
          }),
        },
      })
    );
    renderPage();

    const chenLane = await screen.findByRole('region', { name: '陈小群打法' });
    expect(within(chenLane).getByText(/退潮期空仓是最佳策略/)).toBeInTheDocument();
    expect(within(chenLane).queryByText('泰尔股份')).not.toBeInTheDocument();
    const kobeLane = screen.getByRole('region', { name: '92科比打法' });
    expect(within(kobeLane).getByText('试错')).toBeInTheDocument();
    expect(within(kobeLane).getByText(/今日更推荐/)).toBeInTheDocument();
  });

  it('loads a historical date from the stage strip', async () => {
    mockGetOverview.mockResolvedValue(buildOverview());
    mockGetHistory.mockResolvedValue([
      { tradeDate: '2026-10-20', kind: 'full', stage: 'ferment', stageLabel: '发酵期', kobeStage: 'main_rise', fallDay: 0,
        ztCount: 52, dtCount: 6, maxHeight: 4, promotionRate: 0.3, yztPremium: 2.77, preferredStyle: 'both', intraday: false },
      { tradeDate: '2026-10-19', kind: 'full', stage: 'retreat', stageLabel: '退潮期', kobeStage: 'high_shock', fallDay: 0,
        ztCount: 46, dtCount: 22, maxHeight: 3, promotionRate: 0.19, yztPremium: 0.46, preferredStyle: 'both', intraday: false },
    ]);
    renderPage();

    const day = await screen.findByRole('listitem', { name: '2026-10-19 退潮期' });
    fireEvent.click(day);
    await waitFor(() => expect(mockGetOverview).toHaveBeenLastCalledWith('2026-10-19'));
  });
});
