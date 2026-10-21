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
const mockGetLive = vi.fn();

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
    dabanLiveApi: {
      get: (force?: boolean) => mockGetLive(force),
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

function renderPage(initialEntry = '/daban-king?tab=review') {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
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

function buildLive(overrides: Record<string, unknown> = {}) {
  return {
    ok: true,
    error: null,
    note: null,
    generatedAt: '101530',
    tradeDate: '2026-10-20',
    trading: true,
    replay: false,
    stale: false,
    pollSec: 10,
    durationMs: 1200,
    lanes: {
      kobe: { phaseKey: 'fall_2', cap: 25, used: 25, picks: 1, locked: 1, maxPicks: 4,
        sellRules: [{ text: '第二天不涨停基本都走', principleIds: ['K21'] }] },
      chen: { phaseKey: 'warm', cap: 20, used: 5, picks: 1, locked: 0, maxPicks: 4,
        sellRules: [{ text: '触及 5% 止损线立刻离场', principleIds: ['C08'] }] },
    },
    previous: { available: true, tradeDate: '2026-10-19', chenStage: 'warm', chenLabel: '回暖期', kobeGate: 'fall_2', kobeLabel: '主跌第 2 天' },
    gateLabels: { kobe: '主跌第 2 天', chen: '回暖期', kobeAction: 'actionable', chenAction: 'trial',
      source: 'computed', tradeDate: '2026-10-19' },
    stats: { limitUp: 52, firstBoards: 38, broken: 29, actionable: 2, trial: 3 },
    hits: [
      {
        code: '002347', name: '泰尔股份', style: 'kobe', styleLabel: '92科比打法', action: 'actionable', actionLabel: '可执行',
        positionPct: 25, status: 'sealed', slot: 1, lockedAt: '09:37:50',
        firstTimeRaw: '093745', themeSource: 'reason', themeHint: '机器人', lockable: true, firstTime: '09:37:45', lastTime: '09:37:45', openNum: 1, sealed: true, limitPrice: 6.61,
        price: 6.61, changePct: 10, turnoverRate: 8.1, circMv: 3.3e9, sealAmount: 5e7, sealRatio: 0.015,
        reason: '工业机器人+冶金装备', highDays: '首板', limitUpType: '换手板', theme: '机器人', themeRank: 1,
        themeMain: true, novelty: '老题材', peers: 2, peerCodes: [], notes: [],
        phase: '主跌第 2 天',
        reasons: [{ text: '前一交易日处于主跌第 2 天，按科比的周期门控今天可以打首板', principleIds: ['K33', 'C24'] }],
      },
      {
        code: '600001', name: '甲股份', style: 'chen', styleLabel: '陈小群打法', action: 'trial', actionLabel: '试错',
        positionPct: 5, status: 'opened', slot: 1, lockedAt: null,
        firstTimeRaw: '092500', themeSource: '', themeHint: '', lockable: false, firstTime: '09:25:00', lastTime: '09:25:00', openNum: 0, sealed: true, limitPrice: 11,
        price: 11, changePct: 10, turnoverRate: 2, circMv: 4e9, sealAmount: 1e8, sealRatio: 0.025,
        reason: '人形机器人', highDays: '首板', limitUpType: '一字板', theme: '机器人', themeRank: 1,
        themeMain: true, novelty: '老题材', peers: 0, peerCodes: [], notes: ['竞价 / 开盘即封：多为一字板，打板买不到，只观察'],
        phase: '回暖期',
        reasons: [{ text: '前一交易日收盘为回暖期，原文此阶段「聚焦新题材首板/二板」', principleIds: ['C15'] }],
      },
      {
        code: '600005', name: '戊股份', style: 'kobe', styleLabel: '92科比打法', action: 'watch', actionLabel: '观察',
        positionPct: 0, status: 'preboard', slot: null, lockedAt: null, gapPct: 4.2, volumeRatio: 3.1,
        sealedInTheme: 7, themeNews: 30, firstTimeRaw: '103000', themeSource: 'history', themeHint: '人工智能', lockable: true,
        firstTime: '10:30:00', lastTime: '—', openNum: 0, sealed: false, limitPrice: 20.5, price: 19.6,
        changePct: 5.0, turnoverRate: 6.0, circMv: 5e9, sealAmount: null, sealRatio: null,
        reason: '', highDays: '', limitUpType: '', theme: '人工智能', themeRank: 2, themeMain: true,
        novelty: '新题材', peers: 7, peerCodes: [], phase: '主跌第 2 天',
        notes: [], reasons: [{ text: '人工智能今日 7 只涨停', principleIds: ['C14'] }],
      },
      {
        code: '600004', name: '丁股份', style: 'kobe', styleLabel: '92科比打法', action: 'watch', actionLabel: '观察',
        positionPct: 0, status: 'preboard', slot: null, lockedAt: null, gapPct: 3.8, volumeRatio: 6.51,
        sealedInTheme: 5, firstTimeRaw: '104000', themeSource: 'history', themeHint: '军工', lockable: false,
        firstTime: '10:40:00', lastTime: '—', openNum: 0, sealed: false, limitPrice: 8.9, price: 8.57,
        changePct: 5.3, turnoverRate: 7.2, circMv: 4.2e9, sealAmount: null, sealRatio: null,
        reason: '', highDays: '', limitUpType: '', theme: '军工', themeRank: 3, themeMain: true,
        novelty: '新题材', peers: 5, peerCodes: [], phase: '主跌第 2 天',
        notes: ['未涨停：现价 8.57，涨停价 8.90，还差 3.8%'],
        reasons: [{ text: '「军工」今日已 5 只涨停，资金在往这个方向走', principleIds: ['C14'] }],
      },
      {
        code: '600003', name: '丙股份', style: 'kobe', styleLabel: '92科比打法', action: 'watch', actionLabel: '观察',
        positionPct: 0, status: 'sealed', slot: null, lockedAt: null, gapPct: null, volumeRatio: null, sealedInTheme: null,
        firstTimeRaw: '103000', themeSource: 'block', themeHint: '机器人概念', lockable: true,
        firstTime: '10:30:00', lastTime: '10:30:00', openNum: 0, sealed: true, limitPrice: 12.3, price: 12.3,
        changePct: 10, turnoverRate: 6, circMv: 4e9, sealAmount: 3e7, sealRatio: 0.008,
        reason: '', highDays: '首板', limitUpType: '换手板', theme: '', themeRank: null, themeMain: false,
        novelty: '', peers: 1, peerCodes: [], phase: '主跌第 2 天',
        notes: ['今天「分 4 个票」的名额已锁定用满，只观察'],
        reasons: [{ text: '同题材此前只有 1 只触板', principleIds: ['K33'] }],
      },
    ],
    blocked: [{ code: '600002', name: '乙股份', style: 'chen', theme: '', reason: '陈小群线：前一交易日收盘为退潮期，此阶段不做首板' }],
    themes: [],
    hotThemes: [
      { key: '机器人概念', name: '机器人概念', rank: 1, quota: 4, used: 1, ztCount: 9, days: 4, share: 0.18, newsCount: 12, candidates: 2 },
      { key: '人工智能', name: '人工智能', rank: 2, quota: 4, used: 0, ztCount: 7, days: 2, share: 0.14, newsCount: 30, candidates: 1 },
    ],
    events: { recent: [{ code: '002347', name: '泰尔股份', type: 'FIRST_LIMIT', date: '2026-10-20', time: '093745', orderAmount: 5e7 }], counts: { first: 80, back: 12, failed: 30 } },
    degradedReasons: [],
    ...overrides,
  };
}

describe('DabanKingPage live radar', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.setItem('dsa.uiLanguage', 'zh');
    mockGetHistory.mockResolvedValue([]);
    mockGetActiveJob.mockResolvedValue(null);
    mockGetOverview.mockResolvedValue(buildOverview());
  });

  it('shows gates, both style columns and facts without any score', async () => {
    mockGetLive.mockResolvedValue(buildLive());
    renderPage('/daban-king?tab=live');

    expect(await screen.findByText('今日门控')).toBeInTheDocument();
    expect(screen.getByText('交易中')).toBeInTheDocument();
    const kobe = screen.getByRole('region', { name: '92科比打法' });
    expect(within(kobe).getByText('泰尔股份')).toBeInTheDocument();
    expect(within(kobe).getByText('同题材先封 2 只')).toBeInTheDocument();
    expect(within(kobe).getByText('可执行')).toBeInTheDocument();
    const chen = screen.getByRole('region', { name: '陈小群打法' });
    expect(within(chen).getAllByText(/一字板/).length).toBeGreaterThan(0);
    expect(within(chen).getByText('试错')).toBeInTheDocument();
    // 不打分：页面不出现打板王指数
    expect(screen.queryByText(/打板王指数明细/)).not.toBeInTheDocument();
    // 名额、打板价、炸板状态、卖出纪律与门控依据都要摆出来
    expect(within(kobe).getByText('第 1 个名额')).toBeInTheDocument();
    expect(within(kobe).getByText('打板价 6.61')).toBeInTheDocument();
    expect(within(kobe).getByText(/名额 1\/4（已锁定 1）/)).toBeInTheDocument();
    expect(within(kobe).getByText('09:37:50 锁定')).toBeInTheDocument();
    expect(within(kobe).getByText(/第二天不涨停基本都走/)).toBeInTheDocument();
    expect(within(chen).getByText('已炸板')).toBeInTheDocument();
    expect(screen.getByText(/2026-10-19 收盘的周期（由涨停池现算）/)).toBeInTheDocument();
    expect(screen.getByText(/今日不允许的涨停股/)).toBeInTheDocument();
  });

  it('separates locked slots from watch candidates and shows the theme source', async () => {
    mockGetLive.mockResolvedValue(buildLive());
    renderPage('/daban-king?tab=live');
    const kobe = await screen.findByRole('region', { name: '92科比打法' });

    // 默认「已锁定」：只有拿到名额的那只
    expect(within(kobe).getByRole('tab', { name: /已锁定 1/ })).toBeInTheDocument();
    expect(within(kobe).getByRole('tab', { name: /未锁定潜力股 3/ })).toBeInTheDocument();
    expect(within(kobe).getByText('泰尔股份')).toBeInTheDocument();
    expect(within(kobe).queryByText('丙股份')).not.toBeInTheDocument();

    // 切到潜力股：未锁定的票出现，并标注题材来源（今日题材榜板块）
    fireEvent.click(within(kobe).getByRole('tab', { name: /未锁定潜力股/ }));
    // 最新的在最上面：未涨停的丁股份（10:40）排在已封板但没有名额的丙股份（10:30）之前
    const potential = within(kobe).getAllByText(/丙股份|丁股份/).map((el) => el.textContent);
    expect(potential[0]).toContain('丁股份');
    expect(within(kobe).getByText('未涨停 · 还差 3.8%')).toBeInTheDocument();
    expect(within(kobe).getByText('量比 6.51')).toBeInTheDocument();
    expect(within(kobe).getByText('题材内已 5 只涨停')).toBeInTheDocument();
    expect(within(kobe).getByText('丙股份')).toBeInTheDocument();
    expect(within(kobe).getByText(/机器人概念/)).toBeInTheDocument();
    expect(within(kobe).getByText(/今日题材榜板块/)).toBeInTheDocument();
    expect(within(kobe).queryByText('泰尔股份')).not.toBeInTheDocument();
  });

  it('filters stocks by the chosen hot theme', async () => {
    mockGetLive.mockResolvedValue(buildLive());
    renderPage('/daban-king?tab=live');

    // 今日主线题材卡片：显示涨停数 / 连续天数 / 新闻条数与候选数
    expect(await screen.findByText('今日主线题材')).toBeInTheDocument();
    expect(screen.getByText('涨停 9 · 连续 4 天 · 新闻 12 条')).toBeInTheDocument();
    expect(screen.getAllByText('候选 2 只').length).toBeGreaterThan(0);
    // 热度排名与名额：第 1 名 4 个名额，已用 1
    expect(screen.getByText('名额 1/4')).toBeInTheDocument();
    expect(screen.getByText('名额 0/4')).toBeInTheDocument();

    const kobe = screen.getByRole('region', { name: '92科比打法' });
    fireEvent.click(within(kobe).getByRole('tab', { name: /未锁定潜力股/ }));
    expect(within(kobe).getByText('丁股份')).toBeInTheDocument();
    expect(within(kobe).getByText('戊股份')).toBeInTheDocument();

    // 点「人工智能」只剩该题材的票
    fireEvent.click(screen.getByRole('tab', { name: /人工智能/ }));
    expect(within(kobe).getByText('戊股份')).toBeInTheDocument();
    expect(within(kobe).queryByText('丁股份')).not.toBeInTheDocument();

    // 回到全部
    fireEvent.click(screen.getByRole('tab', { name: '全部题材' }));
    expect(within(kobe).getByText('丁股份')).toBeInTheDocument();
  });

  it('marks a theme as pending instead of claiming none', async () => {
    mockGetLive.mockResolvedValue(buildLive({
      hits: [{
        ...buildLive().hits[1],
      }],
    }));
    renderPage('/daban-king?tab=live');
    const chen = await screen.findByRole('region', { name: '陈小群打法' });
    expect(within(chen).getAllByText(/题材待公布|一字板/).length).toBeGreaterThan(0);
  });

  it('warns when there is no previous review to gate on', async () => {
    mockGetLive.mockResolvedValue(buildLive({
      previous: { available: false, reason: '还没有生成过盘后复盘，盘中雷达无法判定今天的周期门控' },
      hits: [], blocked: [], stats: { limitUp: 52, firstBoards: 38, broken: 29, actionable: 0, trial: 0 },
    }));
    renderPage('/daban-king?tab=live');
    expect(await screen.findByText(/还没有生成过盘后复盘/)).toBeInTheDocument();
    expect(screen.getAllByText('今天这只票池里没有符合本派门控的首板。').length).toBe(2);
  });

  it('marks a closed market honestly and explains the replay', async () => {
    mockGetLive.mockResolvedValue(buildLive({ trading: false, replay: true, tradeDate: '2026-10-20' }));
    renderPage('/daban-king?tab=live');
    expect(await screen.findByText('非交易时段')).toBeInTheDocument();
    expect(screen.getByText(/2026-10-20 的盘中结果回看/)).toBeInTheDocument();
  });
});
