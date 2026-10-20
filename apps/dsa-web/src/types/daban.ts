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
