import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { UiLanguageProvider } from '../../contexts/UiLanguageContext';
import type { DabanFocusItem, DabanOverview } from '../../types/daban';
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
    worldview: {
      version: 'wv-1', forDate: '2026-10-21', basedOn: '2026-10-20', boundaryText: '明天可以推荐：热点题材里、总分 ≥ 0 的票进「重点」',
      blocked: false, nodes: { labels: ['龙头见顶杀高位'] }, mainThemes: ['机器人'],
      dims: [
        { key: 'money_effect', label: '赚钱效应', value: '赚钱效应强', detail: '昨日涨停今日 +2.77%', role: '周期判定的输入',
          source: '科比 #28', tone: 'good' },
        { key: 'node', label: '节点', value: '龙头见顶杀高位', detail: '前一天最高 5 板', role: '节点日标记', source: '科比 #9', tone: 'good' },
      ],
    },
    worldviewCheck: { forDate: '2026-10-20', checks: [{ key: 'mainline', label: '主线', ok: true, detail: '昨天主线 机器人 仍在前 3' }] },
    worldviewAccuracy: { days: 1, dims: { mainline: { label: '主线', n: 1, ok: 1, rate: 1 } } },
    watchlist: {
      forDate: '2026-10-21', items: [{
        code: '002347', name: '泰尔股份', themeName: '机器人', themeRank: 1, roleLabel: '前排', boards: 1, modes: ['弱转强'],
        triggers: [{ text: '竞价由负转正，量能 ≥ 昨日同期 3 倍', principleIds: ['K28'] }],
        abandon: [{ text: '竞价大幅高开放弃', principleIds: ['K28'] }], facts: [], notes: [],
      }],
    },
    focusReview: {
      tradeDate: '2026-10-20', count: 1,
      items: [{
        code: '603278', name: '大业股份', themeName: '机器人', enteredAt: '100500', cost: 10.5, entryStatus: 'preboard',
        initScore: 5, liveScore: 0, closeScore: 0, groupKey: 'normal', flags: '', temperTag: 'normal', sealedClose: true,
        h1Ret: null, h1ExitDate: null, h1Rule: null, nextOpenRet: null, nextCloseRet: null, settleStatus: 'pending',
        attribution: { initScore: 5, closeScore: 0, diff: [{ key: 'open_gap2_5', label: '昨日没触板，今天高开 2~5%', init: 5, close: null }],
          unknowable: [{ at: '103000', kind: 'seal', price: 11 }], afterNews: [], missed: [] },
      }],
      stats: {
        all: { n: 12, days: 5, h1: { n: 12, mean: 0.8, win: 0.58 }, nextOpen: { n: 12, mean: 0.3, win: 0.5 },
          nextClose: { n: 12, mean: 0.2, win: 0.5 }, sealRate: 0.25, holding: 1 },
        byScore: {} as never, byGroup: {} as never, byNode: {} as never, byEntry: {} as never, byTemper: {},
      },
      itemsAccuracy: [],
      observeLive: { observe: { n: 0 }, rest: { n: 12 }, verdict: 'insufficient' },
    },
    excluded: [{ code: '600178', name: '东安动力', boards: 3, roleLabel: '中位股',
      reason: '中位股：3 板，处在 3 板到最高板（4 板）之间', principleIds: ['K11'] }],
    conclusion: {
      summary: '今日情绪处于发酵期', risks: ['机器人已第 7 天'], generatedBy: 'llm', model: 'stub', provider: 'stub', droppedMentions: [],
      bigPicture: { text: '指数平稳，机器人主线在发酵', principleIds: ['K11'] },
    },
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

  it('renders cycle, worldview, watchlist and the focus track record', async () => {
    mockGetOverview.mockResolvedValue(buildOverview());
    renderPage();

    expect(await screen.findByText('发酵期')).toBeInTheDocument();
    expect(screen.getByText('短线世界观')).toBeInTheDocument();
    expect(screen.getByText(/明天可以推荐/)).toBeInTheDocument();
    expect(within(screen.getByTestId('wv-node')).getByText('龙头见顶杀高位')).toBeInTheDocument();
    expect(screen.getByText(/指数平稳，机器人主线在发酵/)).toBeInTheDocument();
    expect(screen.getByText(/对账：昨天那一份世界观/)).toBeInTheDocument();
    // 观察清单：盘后不推荐，只列触发 / 放弃条件
    expect(screen.getByText('2026-10-21 观察清单')).toBeInTheDocument();
    expect(screen.getByText(/竞价由负转正/)).toBeInTheDocument();
    // 成绩单：分组成绩、初始分→收盘分、归因
    expect(within(screen.getByTestId('focus-stats')).getByText(/12 只 \/ 5 天/)).toBeInTheDocument();
    expect(screen.getByText(/初始 \+5 分 → 收盘 0 分/)).toBeInTheDocument();
    expect(screen.getByText(/昨日没触板，今天高开 2~5% \+5→—/)).toBeInTheDocument();
    expect(screen.getByText(/推荐之后才发生/)).toBeInTheDocument();
    // 两条推荐线与仓位已退役
    expect(screen.queryByText('两派共识日')).not.toBeInTheDocument();
    expect(screen.queryByText(/仓位/)).not.toBeInTheDocument();
    expect(screen.getByText(/排除名单/)).toBeInTheDocument();
    expect(screen.getByText('不构成任何投资建议')).toBeInTheDocument();
  });

  it('tells the user to regenerate an old review without a worldview', async () => {
    mockGetOverview.mockResolvedValue(buildOverview({ worldview: null, watchlist: undefined, focusReview: undefined }));
    renderPage();
    expect(await screen.findByText(/旧版生成的，没有世界观/)).toBeInTheDocument();
  });

  it('renders an old snapshot whose new fields come back as empty objects', async () => {
    // 接口对旧快照把 worldview 填成 {}、focus_review / watchlist / legacy_live_stats 填成 {}（不是 undefined），
    // 页面要当「没有这一块」处理，不能因为 review.items 不存在就整页报「页面加载失败」。
    mockGetOverview.mockResolvedValue(buildOverview({
      worldview: {} as never, focusReview: {} as never, watchlist: {} as never, legacyLiveStats: {},
    }));
    renderPage();
    expect(await screen.findByText(/2026-10-20/)).toBeInTheDocument();
    expect(screen.getByText(/旧版生成的，没有世界观/)).toBeInTheDocument();
    expect(screen.queryByText('页面加载失败')).not.toBeInTheDocument();
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


function focusItem(overrides: Partial<DabanFocusItem> = {}): DabanFocusItem {
  return {
    code: '600100', name: '候选股份', themeKey: '机器人概念', themeName: '机器人概念', enteredAt: '100000', cost: 10.6,
    entryStatus: 'preboard', price: 10.8, gapPct: 1.85, retNow: 1.89, changePct: 8.0, status: 'preboard',
    initScore: 5, liveScore: 5,
    items: [{ key: 'open_gap2_5', dim: '昨日状态×今日开盘', cell: '昨日未触板｜｜高开 2-5%', points: 5,
      label: '昨日没触板，今天高开 2~5%', source: '系统补充，无原文出处', principleIds: [], supplement: true,
      n: 10187, days: 204, excess: 0.49, halves: [0.8, 0.14] }],
    initItems: [], groupKey: 'normal', observe: null, flags: [],
    temper: { tag: 'active', label: '活跃', text: '近一年冲板 14 次、封住 6 次；没封住的次日平均 -2.5%', sealRate: 0.43,
      contRate: 0.2, covered: true },
    isNew: false,
    ...overrides,
  };
}

function buildLive(overrides: Record<string, unknown> = {}) {
  return {
    ok: true, error: null, note: null, generatedAt: '101530', tradeDate: '2026-10-20', trading: true, replay: false,
    stale: false, pollSec: 10, durationMs: 1200,
    previous: {
      available: true, source: 'review', tradeDate: '2026-10-19', chenStage: 'warm', chenLabel: '回暖期',
      kobeGate: 'low_shock', kobeLabel: '低位震荡',
      metrics: { ztCount: 52, lianbanCount: 14, dtCount: 6, maxHeight: 4, promotionRate: 0.3043, yztPremium: 2.77, bigLossCount: 5 },
      stage: {
        key: 'warm', label: '回暖期', hitRate: 0.6667, transitional: false, secondary: null, secondaryLabel: '',
        override: null, fallback: false,
        conditions: [{ stage: 'warm', label: '回暖期', hitRate: 0.6667, items: [
          { key: 'promotion_delta', label: '晋级率较昨日', value: 0.02, threshold: '上升', hit: true },
        ] }],
      },
    },
    realtimeGate: { enabled: true, active: true, upgraded: false, frozen: false, reason: '实时重判已开启（当日涨停 25 只）' },
    stats: { limitUp: 3, broken: 1, candidates: 2, hotCandidates: 2, focus: 1 },
    focus: {
      count: 1, threshold: 0, gapMax: 6,
      groups: [{ themeKey: '机器人概念', themeName: '机器人概念', rank: 1, hot: true, items: [focusItem()] }],
      watching: [{ code: '600200', name: '扣分股', status: 'preboard', price: 10.5, gapPct: 4.8, changePct: 5,
        themeKey: '机器人概念', themeName: '机器人概念', score: -8, items: [], reason: '总分 -8 低于门槛 0' }],
    },
    limitBoard: {
      count: 3,
      groups: [{ themeKey: '机器人概念', themeName: '机器人概念', rank: 1, hot: true, count: 3, items: [
        { code: '600001', name: '龙一', boards: 3, highDays: '3天3板', firstTime: '093100', lastTime: '093100', openNum: 0,
          limitUpType: '换手板', reason: '人形机器人', sealAmount: 1e8, sealRatio: 0.02, sealedNow: true, leaderRank: 1,
          recommended: false, contRate: 0.25, temper: null },
        { code: '600002', name: '龙二', boards: 2, highDays: '2天2板', firstTime: '093500', lastTime: '093500', openNum: 0,
          limitUpType: '换手板', reason: '人形机器人', sealAmount: 5e7, sealRatio: 0.01, sealedNow: true, leaderRank: 2,
          recommended: true, contRate: null, temper: null },
        { code: '600003', name: '跟风', boards: 1, highDays: '首板', firstTime: '095000', lastTime: '095000', openNum: 1,
          limitUpType: '换手板', reason: '人形机器人', sealAmount: null, sealRatio: null, sealedNow: true, leaderRank: null,
          recommended: false, contRate: null, temper: null },
      ] }],
    },
    scoreRules: {
      threshold: 0, gapMax: 6, range: [-31, 5], observeText: '科比 #12 认为一进二是机会',
      baseline: { n: 89901, days: 209, h1Mean: 0.09, h1Win: 0.56 },
      items: [{ key: 'open_gap5', dim: '昨日状态×今日开盘', cell: '昨日未触板｜｜高开 ≥5%（未涨停）', points: -8,
        label: '昨日没触板，今天高开 ≥5% 但没涨停', source: '科比 #34', principleIds: ['K28'], supplement: false,
        n: 1740, days: 199, excess: -0.9, halves: [-0.35, -1.36] }],
      observe: [{ key: 'o12_hard_low', dim: '昨日状态×今日开盘', cell: 'x', wouldBe: -11, label: '昨日首板（硬板），今天低开后被拉起',
        n: 407, days: 162, excess: -1.69, halves: [-1.62, -1.75] }],
    },
    worldview: {
      version: 'wv-1', forDate: '2026-10-20', basedOn: '2026-10-19', dims: [], boundaryText: '', blocked: false, nodes: {},
      boundary: { status: 'open', blocks: [], blockLabel: '', openAll: true, openThemes: [], pass: null, frozen: false,
        reasons: [], cycle: { chen: '回暖期', kobe: '低位震荡', tradeDate: '2026-10-19' } },
      overrides: [], changes: [],
    },
    indices: { sh000001: { name: '上证指数', price: 3000, prevClose: 2990, changePct: 0.33, high: null, low: null } },
    featureStatus: { ctxReady: true, ctxError: '', ready: 5, pending: 0, failed: 0 },
    hotThemes: [{ key: '机器人概念', name: '机器人概念', rank: 1, ztCount: 9, days: 4, share: 0.18, newsCount: 12,
      commonality: { kind: 'same', label: '共性相同（补涨 / 发散）', share: 0.6, n: 5, matched: 3 } }],
    events: { recent: [], counts: {} },
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

  it('shows the boundary and scored focus cards with cost, score items and temper', async () => {
    mockGetLive.mockResolvedValue(buildLive());
    renderPage('/daban-king?tab=live');

    const boundary = await screen.findByTestId('boundary');
    expect(within(boundary).getByText(/可以推荐/)).toBeInTheDocument();
    const card = screen.getByTestId('focus-600100');
    expect(within(card).getByText('+5 分')).toBeInTheDocument();
    expect(within(card).getByText('成本 10.60')).toBeInTheDocument();
    expect(within(card).getByText('进入 10:00:00')).toBeInTheDocument();
    expect(within(card).getByText('昨日没触板，今天高开 2~5%')).toBeInTheDocument();
    expect(within(card).getByText('系统补充')).toBeInTheDocument();
    expect(within(card).getByText('股性：活跃')).toBeInTheDocument();
    expect(screen.getByText('上证指数')).toBeInTheDocument();
    // 没有名额、仓位建议、两派线头
    expect(screen.queryByText(/名额/)).not.toBeInTheDocument();
    expect(screen.queryByText(/仓位 ≤|建议仓位/)).not.toBeInTheDocument();
    expect(screen.queryByText('92科比打法')).not.toBeInTheDocument();
    // 没进「重点」的候选写明原因
    expect(screen.getByText('总分 -8 低于门槛 0')).toBeInTheDocument();
  });

  it('switches to the limit-up tab with leader 1 / leader 2 on top', async () => {
    mockGetLive.mockResolvedValue(buildLive());
    renderPage('/daban-king?tab=live');
    fireEvent.click(await screen.findByRole('tab', { name: '涨停板 3' }));

    const first = screen.getByTestId('limit-600001');
    expect(within(first).getByText('龙 1')).toBeInTheDocument();
    expect(within(screen.getByTestId('limit-600002')).getByText('龙 2')).toBeInTheDocument();
    expect(within(screen.getByTestId('limit-600002')).getByText('已推荐')).toBeInTheDocument();
    expect(within(first).getByText('连板成功率 25%')).toBeInTheDocument();
    expect(screen.queryByTestId('focus-600100')).not.toBeInTheDocument();
  });

  it('marks downgraded and observation entries', async () => {
    mockGetLive.mockResolvedValue(buildLive({
      focus: { count: 1, watching: [], groups: [{ themeKey: '机器人概念', themeName: '机器人概念', rank: 1, hot: true, items: [
        focusItem({ liveScore: -3, flags: ['downgraded'], groupKey: 'observe',
          observe: { key: 'o12_hard_low', dim: '', cell: '', wouldBe: -11, label: '昨日首板（硬板），今天低开后被拉起',
            n: 407, days: 162, excess: -1.69, halves: [] } }),
      ] }] },
    }));
    renderPage('/daban-king?tab=live');
    const card = await screen.findByTestId('focus-600100');
    expect(within(card).getByText('已降分')).toBeInTheDocument();
    expect(within(card).getByText('观察期')).toBeInTheDocument();
    expect(within(card).getByText('初始 +5 分')).toBeInTheDocument();
  });

  it('renders a legacy string commonality instead of NaN%', async () => {
    // 2026-11 之前的快照把 commonality 存成字符串：不能去读 .label/.share
    const overview = buildOverview();
    (overview.themes[0] as unknown as { commonality: string }).commonality = '与近 10 日主线机器人一致，属老题材';
    mockGetOverview.mockResolvedValue(overview);
    renderPage();
    expect(await screen.findByText('与近 10 日主线机器人一致，属老题材')).toBeInTheDocument();
    expect(screen.queryByText(/NaN%/)).not.toBeInTheDocument();
  });

  it('pins entries made right after the index surge entry point', async () => {
    const pinnedItem = focusItem({ code: '600300', name: '急拉进', flags: ['surge'], enteredAt: '101000' });
    mockGetLive.mockResolvedValue(buildLive({
      surgePoint: { symbol: 'sh000300', at: '100500', until: '103500', rise: 0.9, change: 0.7, threshold: 0.8,
        windowMin: 30, label: '大盘急拉推荐点：沪深300 30 分钟拉升 0.90 个百分点（现 +0.70%）', active: true },
      focus: { count: 2, watching: [], pinned: [pinnedItem], groups: [{ themeKey: '机器人概念', themeName: '机器人概念',
        rank: 1, hot: true, items: [pinnedItem, focusItem()] }] },
    }));
    renderPage('/daban-king?tab=live');
    const banner = await screen.findByTestId('surge-point');
    expect(within(banner).getByText(/大盘急拉推荐点 10:05/)).toBeInTheDocument();
    const pinned = screen.getByTestId('focus-pinned');
    expect(within(pinned).getByText('大盘急拉推荐点')).toBeInTheDocument();
    // 置顶的票不在题材分组里重复出现
    expect(screen.getAllByTestId('focus-600300')).toHaveLength(1);
    expect(screen.getByTestId('focus-600100')).toBeInTheDocument();
  });

  it('says when the surge window opens a blocked day and marks entries unverified', async () => {
    mockGetLive.mockResolvedValue(buildLive({
      surgePoint: { symbol: 'sh000300', at: '100500', until: '103500', rise: 0.9, change: 0.7, threshold: 0.8,
        windowMin: 30, label: '大盘急拉推荐点：沪深300', active: true, opensBlocked: true },
      focus: { count: 1, watching: [], pinned: [], groups: [{ themeKey: '机器人概念', themeName: '机器人概念', rank: 1,
        hot: true, items: [focusItem({ flags: ['surge', 'unverified'] })] }] },
    }));
    renderPage('/daban-king?tab=live');
    expect(await screen.findByText(/这 30 分钟放开（未验证/)).toBeInTheDocument();
    expect(within(screen.getByTestId('focus-600100')).getByText('未验证')).toBeInTheDocument();
  });

  it('explains a closed boundary and the override that opened a theme', async () => {
    mockGetLive.mockResolvedValue(buildLive({
      worldview: {
        version: 'wv-1', forDate: '2026-10-20', basedOn: '2026-10-19', dims: [], boundaryText: '', blocked: true, nodes: {},
        boundary: { status: 'partial', blocks: ['retreat'], blockLabel: '退潮期', openAll: false, openThemes: ['机器人概念'],
          pass: null, frozen: false, reasons: ['前一交易日收盘是退潮期：管住手'], cycle: { chen: '退潮期', kobe: '高位震荡' } },
        overrides: [{ kind: 'theme', scope: '机器人概念', key: 'theme:机器人概念', at: '103000', label: '题材异动：「机器人概念」15 分钟内新增封板 3 只' }],
        changes: [{ at: '103000', dim: 'boundary', from: 'closed', to: 'partial', reasons: ['推翻信号'] }],
      },
    }));
    renderPage('/daban-king?tab=live');
    const boundary = await screen.findByTestId('boundary');
    expect(within(boundary).getByText('管住手，但推翻信号放开了部分题材')).toBeInTheDocument();
    expect(within(boundary).getByText(/10:30:00 题材异动/)).toBeInTheDocument();
    expect(within(boundary).getByText(/阈值暂定/)).toBeInTheDocument();
  });

  it('filters both tabs by the chosen hot theme', async () => {
    mockGetLive.mockResolvedValue(buildLive({
      hotThemes: [
        { key: '机器人概念', name: '机器人概念', rank: 1, ztCount: 9, days: 4, share: 0.18, newsCount: 12 },
        { key: '人工智能', name: '人工智能', rank: 2, ztCount: 5, days: 2, share: 0.1, newsCount: 4 },
      ],
    }));
    renderPage('/daban-king?tab=live');
    await screen.findByTestId('focus-600100');
    fireEvent.click(screen.getByRole('tab', { name: /人工智能/ }));
    expect(screen.queryByTestId('focus-600100')).not.toBeInTheDocument();
    expect(screen.getByText('现在没有进入「重点」的票。')).toBeInTheDocument();
  });

  it('warns when there is no previous review to gate on', async () => {
    mockGetLive.mockResolvedValue(buildLive({ previous: { available: false, reason: '无法判定 2026-10-19 的情绪周期' }, worldview: null }));
    renderPage('/daban-king?tab=live');
    expect(await screen.findByText('无法判定 2026-10-19 的情绪周期')).toBeInTheDocument();
  });

  it('marks a closed market honestly and explains the replay', async () => {
    mockGetLive.mockResolvedValue(buildLive({ trading: false, replay: true }));
    renderPage('/daban-king?tab=live');
    expect(await screen.findByText('非交易时段')).toBeInTheDocument();
    expect(screen.getByText(/2026-10-20/)).toBeInTheDocument();
  });
});
