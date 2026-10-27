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
  commonality?: string;
  sameAs?: string[];
  news: DabanNews[];
  newsCount: number | null;
};

export type DabanDimension = {
  key: string;
  label: string;
  score: number;
  max: number;
  reason: string;
  principleIds: string[];
};

export type DabanRisk = {
  points: number;
  text: string;
  principleIds: string[];
};

export type DabanPick = {
  code: string;
  name: string;
  mode: string;
  modeLabel: string;
  role: string;
  roleLabel: string;
  themeName: string;
  origin: string;
  action: DabanAction;
  actionLabel: string;
  score: number;
  grade: string;
  ruleScore: number;
  llmAdjust: number;
  llmAdjustReason: string;
  riskPenalty: number;
  dimensions: DabanDimension[];
  risks: DabanRisk[];
  facts: string[];
  reasons: DabanCited[];
  llmNote: DabanCited | null;
  triggers: DabanCited[];
  abandon: DabanCited[];
  positionPct: number;
  price: number | null;
  changePct: number | null;
  boards: number;
  limitUpType: string;
  notes: string[];
  alsoModes: string[];
  consensus: boolean;
};

export type DabanLane = {
  style: DabanStyle;
  label: string;
  stageKey: string;
  stageLabel: string;
  positionCap: number;
  positionCapLabel: string;
  positionUsed: number;
  flat: boolean;
  flatMessage: string;
  flatPrinciples: string[];
  actionable: DabanPick[];
  trial: DabanPick[];
  watch: DabanPick[];
  sellRules: DabanCited[];
  commentary: string;
  commentaryPrinciples: string[];
};

export type DabanVote = {
  key: string;
  style: DabanStyle | '';
  label: string;
  hit: boolean;
  available: boolean;
  evidence: string;
  principleIds: string[];
};

export type DabanVerdict = {
  preferred: DabanStyle | 'both';
  label: string;
  votes: Record<DabanStyle, number>;
  overlap: number;
  signals: DabanVote[];
  summary: string;
  modelOpinion: { agree: boolean; preferred: DabanStyle | 'both' | null; reason: string } | null;
};

export type DabanExcluded = {
  code: string;
  name: string;
  boards: number;
  roleLabel: string;
  reason: string;
  principleIds: string[];
};

export type DabanReviewItem = {
  code: string;
  name: string;
  mode: string;
  modeLabel: string;
  action: string;
  changePct: number | null;
  limitUp: boolean | null;
};

export type DabanReviewLane = {
  count: number;
  limitUp: number;
  avgChange: number | null;
  items: DabanReviewItem[];
};

export type DabanReview = {
  tradeDate: string;
  reviewedOn: string;
  chen: DabanReviewLane;
  kobe: DabanReviewLane;
};

export type DabanTrackRecord = Record<
  DabanStyle,
  { days: number; picks: number; avg: number | null; limitUpRate: number | null }
>;

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
  verdict: DabanVerdict;
  lanes: Record<DabanStyle, DabanLane>;
  consensusCodes: string[];
  excluded: DabanExcluded[];
  review: DabanReview | null;
  liveStats?: Record<DabanStyle, { days: number; picks: number; sealedRate: number | null; openMean: number | null; closeMean: number | null; winRate: number | null }>;
  forecastReview?: { groups: Record<string, { n: number; touchRate: number | null; sealRate: number | null }>; days: number; total: number };
  liveReview?: { calls: number; sealed: number; actionable: number; phase: { chen?: string; kobe?: string } } | null;
  trackRecord: DabanTrackRecord;
  conclusion: {
    summary: string;
    risks: string[];
    generatedBy: 'llm' | 'rule_fallback' | string;
    model: string;
    provider: string;
    droppedMentions: string[];
  };
  /** 键是条目 id（K01 / C04），转换时保留原样。 */
  principles: Record<string, DabanPrinciple>;
  modeLabels: Record<string, string>;
  styleLabels: Record<string, string>;
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

export type DabanLiveHit = {
  code: string;
  name: string;
  style: DabanStyle;
  styleLabel: string;
  action: DabanAction;
  actionLabel: string;
  positionPct: number;
  /** sealed = 当前封着；opened = 触板后已炸开；preboard = 还没涨停正在拉升；missing = 已锁定但这一轮上游没返回它 */
  status: 'sealed' | 'opened' | 'preboard' | 'missing';
  /** 距涨停价还差几个百分点（仅 preboard） */
  gapPct?: number | null;
  /** 量比（仅 preboard，来自腾讯快照） */
  volumeRatio?: number | null;
  /** 该题材今日已涨停几只（preboard 的「资金合力」证据） */
  sealedInTheme?: number | null;
  /** 该题材近 3 日新闻条数（preboard 的新闻门槛）；null = 还没取到 */
  themeNews?: number | null;
  /** 先到先得分到的名额序号（1 起）；没分到名额为 null */
  slot: number | null;
  /** 名额锁定的时刻（HH:MM:SS）；本轮新分配的为 null */
  lockedAt: string | null;
  firstTime: string;
  firstTimeRaw: string;
  lastTime: string;
  openNum: number;
  sealed: boolean | null;
  limitPrice: number | null;
  price: number | null;
  changePct: number | null;
  turnoverRate: number | null;
  circMv: number | null;
  sealAmount: number | null;
  sealRatio: number | null;
  reason: string;
  highDays: string;
  limitUpType: string;
  theme: string;
  /**
   * 该票归属的今日热点题材 key（`hotThemes[].key`），空串 = 贴不上任何热点题材。
   * `theme` 是展示名，两套口径的写法不一样（「机器人」vs「机器人概念」），筛选只能用这个。
   */
  themeKey: string;
  themeRank: number | null;
  themeMain: boolean;
  novelty: string;
  /** reason = 当日涨停原因；history = 历史涨停原因；block = 当日题材榜；concept = 东财所属概念；空 = 还不知道 */
  themeSource: 'reason' | 'history' | 'block' | 'concept' | 'record' | '';
  themeHint: string;
  /** 首次封板时间未知（集合竞价）时不可占名额 */
  lockable: boolean;
  peers: number;
  peerCodes: string[];
  reasons: DabanCited[];
  notes: string[];
  phase: string | null;
};

export type DabanHotTheme = {
  key: string;
  name: string;
  ztCount: number;
  days: number;
  share: number;
  newsCount: number | null;
  /** 两派合计的未锁定候选数 / 已锁定名额数；分派的数在 `byStyle` 里 */
  candidates: number;
  /** 单派的题材名额上限（两派各自有这么多，不是合起来） */
  quota: number;
  used: number;
  /** 每派各自的「已锁定名额」与「未锁定候选」。候选与页面「未锁定潜力股」同源同判据。 */
  byStyle?: Partial<Record<DabanStyle, { used: number; candidates: number }>>;
  novelty?: string;
  rank?: number | null;
};

export type DabanLiveEvent = { code: string; name: string; type: string; date: string; time: string; orderAmount: number | null };

export type DabanLiveLane = {
  phaseKey: string;
  cap: number;
  used: number;
  picks: number;
  locked: number;
  maxPicks: number;
  sellRules: DabanCited[];
};

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
  lanes: Partial<Record<DabanStyle, DabanLiveLane>>;
  previous: {
    available?: boolean;
    tradeDate?: string;
    chenStage?: string;
    chenLabel?: string;
    kobeGate?: string;
    kobeLabel?: string;
    reason?: string;
    mainThemes?: { name: string; rank: number; novelty: string }[];
    /** 门控依据：前一交易日的指标与六阶段条件核对，与盘后复盘同一份结构 */
    metrics?: DabanMetrics;
    stage?: DabanStage;
    kobeStage?: DabanKobeStage;
  };
  gateLabels: {
    kobe?: string;
    chen?: string;
    kobeAction?: string | null;
    chenAction?: string | null;
    /** 陈小群弱转强的放行级别（首板与弱转强的门控不同） */
    chenW2sAction?: string | null;
    source?: 'review' | 'computed';
    tradeDate?: string;
    /** 该派当前阶段「为什么能做 / 为什么不做」的原话与出处 */
    chenReason?: { text: string; principleIds: string[] };
    kobeReason?: { text: string; principleIds: string[] };
  };
  /** 盘中实时门控：前一交易日只做底线，今天的 tape 给出市场验证后按今天的周期行事 */
  realtimeGate?: {
    enabled?: boolean;
    /** 当日涨停数够样本量、已开始实时重判 */
    active?: boolean;
    /** 已按今天的周期放宽门控 */
    upgraded?: boolean;
    /** 赚钱效应回落且无题材接力，已停发新名额 */
    frozen?: boolean;
    reason?: string;
    /** 仍在加速封板的热点题材（轮动豁免的依据） */
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
  stats: { limitUp?: number; firstBoards?: number; firstTouches?: number; broken?: number; actionable?: number; trial?: number };
  hits: DabanLiveHit[];
  blocked: { code: string; name: string; style: DabanStyle; theme: string; reason: string }[];
  themes: DabanTheme[];
  /** 今日热点题材（够热 + 在升温 + 有新闻），页面用作筛选 tab */
  hotThemes: DabanHotTheme[];
  events: {
    recent?: DabanLiveEvent[];
    counts?: { first?: number; back?: number; failed?: number };
  };
  degradedReasons: string[];
};

export type DabanForecastSummary = {
  groups: Record<string, { n: number; touchRate: number | null; sealRate: number | null }>;
  days: number;
  total: number;
};
