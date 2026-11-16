/** 「我是打板王」接口载荷（后端 snake_case 经 toCamelCase 转换后的形状）。 */

export type DabanStyle = 'chen' | 'kobe';
export type DabanAction = 'actionable' | 'trial' | 'watch';

export type DabanCited = {
  text: string;
  principleIds: string[];
};

export type DabanPrinciple = {
  author: string;
  ref: string;
  title: string;
  quote: string;
};

export type DabanStageCondition = {
  key: string;
  label: string;
  value: number | null;
  threshold: string;
  hit: boolean | null;
};

export type DabanStageEntry = {
  stage: string;
  label: string;
  hitRate: number;
  items: DabanStageCondition[];
};

export type DabanStage = {
  key: string;
  label: string;
  hitRate: number;
  transitional: boolean;
  secondary: string | null;
  secondaryLabel: string;
  override: string | null;
  fallback: boolean;
  conditions: DabanStageEntry[];
};

export type DabanKobeStage = {
  key: string;
  label: string;
  fallDay: number;
  gateKey: string;
};

export type DabanTier = {
  from: number;
  label: string;
  base: number;
  promoted: number;
  rate: number | null;
};

export type DabanMetrics = {
  ztCount: number;
  zbCount: number;
  touchedCount: number;
  zabRate: number | null;
  dtCount: number;
  lianbanCount: number;
  maxHeight: number;
  secondHeight: number;
  leaderSealRatio: number | null;
  firstBoardCount: number;
  yesterdayZtCount: number | null;
  promotionRate: number | null;
  tiers: DabanTier[];
  yztPremium: number | null;
  yztUpRatio: number | null;
  bigLossCount: number | null;
  followRate: number | null;
  delta: Record<string, number | null>;
};

export type DabanLadderStock = {
  code: string;
  name: string;
  theme: string;
  roleLabel: string;
  reason: string;
  firstTime: string;
  limitUpType: string;
  openNum: number;
  sealRatio: number | null;
  highDays: string;
};

export type DabanLadderTier = {
  boards: number;
  label: string;
  count: number;
  promotion: DabanTier | null;
  stocks: DabanLadderStock[];
};

export type DabanNews = {
  title: string;
  platformLabel: string;
  publishedAt: string;
  url: string;
};

export type DabanTheme = {
  key: string;
  name: string;
  tags: string[];
  rank: number;
  isMain: boolean;
  ztCount: number;
  lianbanCount: number;
  maxBoards: number;
  days: number;
  status: string;
  statusLabel: string;
  novelty: string;
  noveltyLabel: string;
  premium: number | null;
  leader: { code: string; name: string; boards: number } | null;
  members: { code: string; name: string; boards: number; firstTime: string; limitUpType: string }[];
  storySpace: 'big' | 'medium' | 'small' | null;
  storyLabel: string;
  storyReason: string;
  storySource: 'llm' | 'rule' | '';
  /** 按成员判的共性（规则）：成员近 90 天的涨停原因落在近 10 日主线里的比例 */
  /** 2026-11 之前的快照把共性存成字符串，读取时要兼容 */
  commonality?: DabanCommonality | string | null;
  /** 模型写的共性说明 / 明天的预期判断（只展示） */
  commonalityText?: string;
  expectationText?: string;
  expectationPrinciples?: string[];
  sameAs?: string[];
  news: DabanNews[];
  newsCount: number | null;
};

export type DabanExcluded = {
  code: string;
  name: string;
  boards: number;
  roleLabel: string;
  reason: string;
  principleIds: string[];
};

export type DabanSource = {
  key: string;
  label: string;
  ok: boolean;
  source: string;
  asOf: string;
  degraded: boolean;
  degradedReason: string;
  error: string | null;
  count: number | null;
};

export type DabanLegacyLiveStat = {
  label: string;
  days: number;
  picks: number;
  sealedRate: number | null;
  openMean: number | null;
  closeMean: number | null;
  winRate: number | null;
};

export type DabanOverview = {
  schemaVersion: string;
  kind: string;
  tradeDate: string;
  previousTradeDate: string;
  generatedAt: string;
  durationMs: number;
  intraday: boolean;
  metrics: DabanMetrics;
  stage: DabanStage;
  kobeStage: DabanKobeStage;
  ladder: DabanLadderTier[];
  themes: DabanTheme[];
  /** 短线世界观（给下一个交易日用）。2026-11 之前生成的快照没有这一项。 */
  worldview?: DabanWorldview | null;
  worldviewCheck?: { forDate: string; checks: DabanWorldviewCheck[] } | null;
  worldviewAccuracy?: { days: number; dims: Record<string, { label: string; n: number; ok: number; rate: number | null }> };
  watchlist?: DabanWatchlist;
  focusReview?: DabanFocusReview;
  /** 旧雷达（锁定 + 名额时期，已停写）的成绩；新口径攒满 20 个交易日前折叠展示，之后为空。 */
  legacyLiveStats?: Record<string, DabanLegacyLiveStat>;
  excluded: DabanExcluded[];
  forecastReview?: { groups: Record<string, { n: number; touchRate: number | null; sealRate: number | null }>; days: number; total: number };
  conclusion: {
    summary: string;
    bigPicture?: { text: string; principleIds: string[] };
    risks: string[];
    generatedBy: 'llm' | 'rule_fallback' | string;
    model: string;
    provider: string;
    droppedMentions: string[];
  };
  /** 键是条目 id（K01 / C04），转换时保留原样。 */
  principles: Record<string, DabanPrinciple>;
  modeLabels: Record<string, string>;
  sources: Record<string, DabanSource>;
  disclaimers: { investment?: string; data?: string };
  degraded: boolean;
  warnings: string[];
  cached: boolean;
  storedAt: string | null;
};

export type DabanJob = {
  jobId: string;
  tradeDate: string | null;
  status: 'queued' | 'running' | 'succeeded' | 'failed';
  phase: string;
  phaseLabel: string;
  message: string;
  progress: number;
  createdAt: number;
  updatedAt: number;
  result: DabanOverview | null;
  error: string | null;
};

export type DabanJobSubmit = {
  job: DabanJob | null;
  reused: boolean;
  coolingDown: boolean;
  retryAfterSeconds: number;
  message: string;
};

export type DabanPushResult = {
  ok: boolean;
  skipped: boolean;
  message: string;
  tradeDate: string;
};

export type DabanHistoryItem = {
  tradeDate: string;
  kind: string;
  stage: string | null;
  stageLabel: string;
  kobeStage: string | null;
  fallDay: number | null;
  ztCount: number | null;
  dtCount: number | null;
  maxHeight: number | null;
  promotionRate: number | null;
  yztPremium: number | null;
  preferredStyle: DabanStyle | 'both' | null;
  intraday: boolean;
};

export type DabanCommonality = {
  kind: 'same' | 'related' | 'new' | 'unknown';
  label: string;
  share: number | null;
  n: number;
  matched: number;
};

/** 计分表的一项（与后端 `rec_score.items` 同一份）。 */
export type DabanScoreItem = {
  key: string;
  dim: string;
  cell: string;
  points: number;
  label: string;
  source: string;
  principleIds: string[];
  /** 原文没有出处、数据有效的项：页面标「系统补充」 */
  supplement: boolean;
  n: number;
  days: number;
  excess: number;
  halves: (number | null)[];
};

export type DabanObserveItem = {
  key: string;
  dim: string;
  cell: string;
  wouldBe: number;
  label: string;
  n: number;
  days: number;
  excess: number;
  halves: (number | null)[];
};

export type DabanScoreRules = {
  threshold: number;
  gapMax: number;
  range: [number, number];
  items: DabanScoreItem[];
  observe: DabanObserveItem[];
  observeText: string;
  baseline: { n?: number; days?: number; h1Mean?: number; h1Win?: number; sealRate?: number };
};

export type DabanTemper = {
  /** active 活跃 / normal 一般 / inactive 不活跃 / unknown 冲板不足 3 次 */
  tag: 'active' | 'normal' | 'inactive' | 'unknown';
  label: string;
  text: string;
  sealRate: number | null;
  contRate: number | null;
  covered: boolean;
};

export type DabanFocusFlag = 'downgraded' | 'uncovered' | 'opened_entry' | 'node_day' | 'override' | 'surge' | 'unverified';

/** 大盘急拉推荐点：沪深300 近 30 分钟拉升 ≥0.8 个百分点且翻红。只是买入时机提示，不改边界和评分。 */
export type DabanSurgePoint = {
  symbol: string;
  /** 触发时刻 / 标记截止时刻（HHMMSS） */
  at: string;
  until: string;
  rise: number;
  change: number;
  threshold: number;
  windowMin: number;
  label: string;
  /** 现在是否还在标记窗口里 */
  active: boolean;
  /** 前一交易日周期挡住、这个窗口放开了推荐（未验证） */
  opensBlocked?: boolean;
};

/** 「重点」tab 的一只票：进入那一刻的成本和初始分不再改，实时分每轮重算。 */
export type DabanFocusItem = {
  code: string;
  name: string;
  themeKey: string;
  themeName: string;
  /** 进入「重点」的时刻（HHMMSS） */
  enteredAt: string;
  /** 进入那一刻的现价，记作成本 */
  cost: number | null;
  /** preboard = 没封板时进入；opened = 炸开后进入 */
  entryStatus: 'preboard' | 'opened';
  price: number | null;
  gapPct: number | null;
  /** 现价相对成本 */
  retNow: number | null;
  changePct: number | null;
  /** 当前状态：preboard 未封板 / opened 炸开 / sealed 已封住 */
  status: 'preboard' | 'opened' | 'sealed';
  initScore: number | null;
  liveScore: number | null;
  items: DabanScoreItem[];
  initItems: DabanScoreItem[];
  /** normal 计分推荐 / observe 观察期（一进二） */
  groupKey: 'normal' | 'observe';
  observe: DabanObserveItem | null;
  flags: DabanFocusFlag[];
  temper: DabanTemper;
  isNew: boolean;
};

export type DabanFocusGroup = {
  themeKey: string;
  themeName: string;
  rank: number | null;
  hot: boolean;
  items: DabanFocusItem[];
};

/** 在热点题材里、但这一轮没进「重点」的候选，以及原因。 */
export type DabanWatchingItem = {
  code: string;
  name: string;
  status: string;
  price: number | null;
  gapPct: number | null;
  changePct: number | null;
  themeKey: string;
  themeName: string;
  score: number | null;
  items: DabanScoreItem[];
  reason: string;
};

export type DabanLimitBoardItem = {
  code: string;
  name: string;
  boards: number;
  highDays: string;
  firstTime: string;
  lastTime: string;
  openNum: number;
  limitUpType: string;
  reason: string;
  sealAmount: number | null;
  sealRatio: number | null;
  sealedNow: boolean | null;
  /** 1 = 龙 1，2 = 龙 2，null = 其他 */
  leaderRank: 1 | 2 | null;
  recommended: boolean;
  /** 连板成功率（科比 #29：看风向标明天有没有人接） */
  contRate: number | null;
  temper: DabanTemper | null;
};

export type DabanLimitBoardGroup = {
  themeKey: string;
  themeName: string;
  rank: number | null;
  hot: boolean;
  count: number;
  items: DabanLimitBoardItem[];
};

export type DabanWorldviewDim = {
  key: string;
  label: string;
  value: string;
  detail: string;
  /** 这个维度在系统里起什么作用（边界 / 计分 / 标签 / 模型判断） */
  role: string;
  source: string;
  tone: 'good' | 'bad' | 'neutral';
  /** 盘中版的实时补充 */
  live?: string;
};

export type DabanOverrideSignal = {
  kind: 'index' | 'theme' | 'new_theme';
  /** all = 放开全部；否则是被放开的热点题材 key */
  scope: string;
  key: string;
  at: string;
  label: string;
};

export type DabanBoundary = {
  /** open 可以推荐 / partial 只放开部分题材 / closed 管住手 / frozen 停发新推荐 */
  status: 'open' | 'partial' | 'closed' | 'frozen';
  blocks: string[];
  blockLabel: string;
  openAll: boolean;
  openThemes: string[];
  pass: { kind: 'node' | 'market' | 'override'; label: string } | null;
  frozen: boolean;
  reasons: string[];
  cycle?: { chen?: string; kobe?: string; fallDay?: number; tradeDate?: string };
};

export type DabanWorldview = {
  version: string;
  forDate: string;
  basedOn: string;
  dims: DabanWorldviewDim[];
  boundaryText: string;
  blocked: boolean;
  nodes: { labels?: string[]; topKilled?: boolean; highDiverge?: boolean; afterFall?: boolean };
  mainThemes?: string[];
  summary?: string;
  /** 以下只在盘中版出现 */
  boundary?: DabanBoundary;
  overrides?: DabanOverrideSignal[];
  changes?: { at: string; dim: string; from: string | null; to: string; reasons: string[] }[];
};

export type DabanWorldviewCheck = { key: string; label: string; ok: boolean | null; detail: string };

export type DabanWatchItem = {
  code: string;
  name: string;
  themeName: string;
  themeRank: number | null;
  roleLabel: string;
  boards: number | null;
  modes: string[];
  triggers: DabanCited[];
  abandon: DabanCited[];
  facts: string[];
  notes: string[];
};

export type DabanWatchlist = { forDate?: string; boundary?: string; blocked?: boolean; items: DabanWatchItem[] };

export type DabanRetStat = { n: number; mean: number | null; win: number | null };

export type DabanFocusStat = {
  n: number;
  days: number;
  h1: DabanRetStat;
  nextOpen: DabanRetStat;
  nextClose: DabanRetStat;
  sealRate: number | null;
  holding: number;
  label?: string;
};

export type DabanFocusReviewItem = {
  code: string;
  name: string;
  themeName: string;
  enteredAt: string;
  cost: number | null;
  entryStatus: string;
  initScore: number | null;
  liveScore: number | null;
  closeScore: number | null;
  groupKey: string;
  flags: string;
  temperTag: string | null;
  sealedClose: boolean | null;
  h1Ret: number | null;
  h1ExitDate: string | null;
  h1Rule: string | null;
  nextOpenRet: number | null;
  nextCloseRet: number | null;
  settleStatus: 'pending' | 'holding' | 'done' | null;
  attribution: {
    initScore: number | null;
    closeScore: number | null;
    diff: { key: string; label: string; init: number | null; close: number | null }[];
    unknowable: { at: string; kind: string; price: number | null }[];
    afterNews: { title: string; publishedAt: string; url: string }[];
    missed: { title: string; publishedAt: string; url: string }[];
  };
};

export type DabanFocusReview = {
  tradeDate: string;
  count: number;
  items: DabanFocusReviewItem[];
  stats: {
    all: DabanFocusStat;
    byScore: Record<'neg' | 'zero' | 'pos', DabanFocusStat>;
    byGroup: Record<'normal' | 'observe', DabanFocusStat>;
    byNode: Record<'node' | 'other', DabanFocusStat>;
    byEntry: Record<'preboard' | 'opened', DabanFocusStat>;
    byTemper: Record<string, DabanFocusStat>;
  };
  itemsAccuracy: { key: string; label: string; n: number; expected: number | null; actual: number | null; verdict: string }[];
  observeLive: { observe: { n: number; h1Mean?: number; win?: number }; rest: { n: number; h1Mean?: number; win?: number }; verdict: string };
};

export type DabanHotTheme = {
  key: string;
  name: string;
  ztCount: number;
  days: number;
  share: number;
  newsCount: number | null;
  novelty?: string;
  rank?: number | null;
  /** 按成员判的共性：补涨 / 发散 / 新题材 */
  commonality?: DabanCommonality | null;
  /** 科比 #6 补涨结束：该题材停推补涨 */
  catchUpEnd?: boolean;
};

export type DabanLiveEvent = { code: string; name: string; type: string; date: string; time: string; orderAmount: number | null };

export type DabanIndexQuote = { name: string; price: number; prevClose: number; changePct: number; high: number | null; low: number | null };

export type DabanLive = {
  ok: boolean;
  error: string | null;
  note: string | null;
  generatedAt: string;
  tradeDate: string;
  trading: boolean;
  /** 涨停池不是今天的（盘前 / 非交易日回看上一交易日） */
  replay: boolean;
  stale: boolean;
  pollSec: number;
  durationMs: number;
  previous: {
    available?: boolean;
    source?: 'review' | 'computed';
    tradeDate?: string;
    chenStage?: string;
    chenLabel?: string;
    kobeGate?: string;
    kobeLabel?: string;
    reason?: string;
    /** 前一交易日的指标与六阶段条件核对，与盘后复盘同一份结构 */
    metrics?: DabanMetrics;
    stage?: DabanStage;
    kobeStage?: DabanKobeStage;
  };
  /** 盘中实时周期重判：前一交易日只做底线，今天的 tape 给出市场验证后按今天的周期 */
  realtimeGate?: {
    enabled?: boolean;
    active?: boolean;
    upgraded?: boolean;
    marketConfirmed?: boolean;
    /** 赚钱效应回落且无题材接力，已停发新推荐 */
    frozen?: boolean;
    reason?: string;
    accelTheme?: string | null;
    todayLabel?: string | null;
    todayKobeLabel?: string | null;
    metrics?: {
      ztCount?: number | null;
      dtCount?: number | null;
      zbCount?: number | null;
      yztPremium?: number | null;
      zabRate?: number | null;
    };
  };
  stats: { limitUp?: number; broken?: number; candidates?: number; hotCandidates?: number; focus?: number };
  /** 「重点」tab：评分达标、还买得进、记成绩 */
  focus: {
    groups: DabanFocusGroup[];
    count: number;
    /** 大盘急拉推荐点窗口里进入的票（置顶展示，成绩照常统计） */
    pinned?: DabanFocusItem[];
    watching: DabanWatchingItem[];
    threshold?: number;
    gapMax?: number;
  };
  /** 当天的大盘急拉推荐点；没触发为 null */
  surgePoint?: DabanSurgePoint | null;
  /** 「涨停板」tab：已封住的票，龙 1 / 龙 2 置顶，不记成绩 */
  limitBoard: { groups: DabanLimitBoardGroup[]; count: number };
  scoreRules: DabanScoreRules;
  /** 盘中版世界观：边界、推翻信号、改动记录 */
  worldview: DabanWorldview | null;
  indices?: Record<string, DabanIndexQuote>;
  featureStatus?: { ctxReady: boolean; ctxError: string; ready: number; pending: number; failed: number } | null;
  /** 今日热点题材（够热 + 在升温 + 有新闻），页面用作筛选 tab */
  hotThemes: DabanHotTheme[];
  events: {
    recent?: DabanLiveEvent[];
    counts?: { first?: number; back?: number; failed?: number };
  };
  degradedReasons?: string[];
};

export type DabanForecastSummary = {
  groups: Record<string, { n: number; touchRate: number | null; sealRate: number | null }>;
  days: number;
  total: number;
};
