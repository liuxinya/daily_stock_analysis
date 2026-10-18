/**
 * 「行业资金」页面类型。
 *
 * 后端返回 snake_case，API 层用 `toCamelCase` 转成 camelCase 后即为这里的形状。
 */

export type FundVerdict = 'bullish' | 'neutral' | 'bearish' | 'unknown';

/** 单个因子的可解释结果 —— 「完全可解释」就体现在这些字段上。 */
export type MarketFactor = {
  key: string;
  label: string;
  /** 配置权重（0-1）。 */
  weight: number;
  /** 数据缺失重新归一后实际参与的权重；不可用时为 0。 */
  effectiveWeight: number;
  direction: string;
  normalization: string;
  /** 原始值的人类可读形式。 */
  rawDisplay: string;
  /** 分数是怎么算出来的。 */
  basis: string;
  /** 0-20；不可用时为 null。 */
  score: number | null;
  maxScore: number;
  available: boolean;
  source: string;
  asOf: string;
  error: string;
  detail: Record<string, unknown>;
};

export type MarketScore = {
  score: number | null;
  verdict: FundVerdict;
  verdictLabel: string;
  factors: MarketFactor[];
  availableWeight: number;
  renormalized: boolean;
  thresholds: { bullish?: number; bearish?: number };
  warnings: string[];
  degraded: boolean;
  degradedReason: string;
};

export type IndustryComponent = {
  key: string;
  label: string;
  weight: number;
  percentile: number | null;
  score: number | null;
  normalization: string;
};

export type IndustryRow = {
  rank: number;
  name: string;
  score: number | null;
  index: number | null;
  changePct: number | null;
  inflow: number | null;
  outflow: number | null;
  net: number | null;
  net5d: number | null;
  companyCount: number | null;
  leader: string;
  leaderChangePct: number | null;
  leaderPrice: number | null;
  components: IndustryComponent[];
};

export type IndustryResult = {
  industries: IndustryRow[];
  weights: Record<string, number>;
  labels: Record<string, string>;
  normalization: Record<string, string>;
  degraded: boolean;
  degradedReason: string;
  warnings: string[];
};

export type CandidateMetrics = {
  institutionNet?: number | null;
  lhbNet?: number | null;
  mainNet?: number | null;
  buyerCount?: number | null;
  sellerCount?: number | null;
  listedCount?: number | null;
  changePct?: number | null;
  change1m?: number | null;
  close?: number | null;
  marketCap?: number | null;
  reason?: string;
  lastListed?: string;
  industry?: string;
  industryScore?: number | null;
  industryNet?: number | null;
  industryNet5d?: number | null;
  leaderChangePct?: number | null;
  leaderPrice?: number | null;
  turnoverRate?: number | null;
};

export type StockCandidate = {
  code: string;
  name: string;
  /** 为什么入选，如「龙虎榜机构净买」「行业龙头（军工装备 当日领涨）」。 */
  reasons: string[];
  metrics: CandidateMetrics;
  /** 涨幅榜上但当日资金净流出时的提醒。 */
  flowWarning?: string;
};

export type StockCandidates = {
  candidates: StockCandidate[];
  totalPool: number;
  droppedByIndustry: { code: string; name: string; industryNet: number }[];
  droppedCount: number;
  outflowFlaggedCount: number;
  sources: Record<string, number>;
  warnings: string[];
  degraded: boolean;
  degradedReason: string;
};

export type ConclusionStockNote = {
  code: string;
  name: string;
  note: string;
};

export type ConclusionDirection = {
  name: string;
  stance: 'bullish' | 'neutral' | 'bearish';
  reason: string;
  evidence: string[];
};

export type Conclusion = {
  summary: string;
  directions: ConclusionDirection[];
  stockNotes: ConclusionStockNote[];
  risks: string[];
  /** `llm` 表示模型生成；`rule_fallback` 表示模型不可用时的确定性兜底结论。 */
  generatedBy: 'llm' | 'rule_fallback';
  model?: string;
  provider?: string;
  /** 被白名单拦下的越界个股提及。 */
  droppedMentions: string[];
  warnings: string[];
};

/** 单个数据源的抓取情况，供「来源」展开复核。 */
export type SourceEntry = {
  key: string;
  label: string;
  ok: boolean;
  source: string;
  asOf: string;
  degraded: boolean;
  degradedReason: string;
  error: string | null;
  value: Record<string, unknown>;
  raw: Record<string, unknown>;
};

export type IndustryFundsOverview = {
  schemaVersion: string;
  tradeDate: string;
  generatedAt: string;
  durationMs: number;
  market: MarketScore;
  industries: IndustryResult;
  candidates: StockCandidates;
  conclusion: Conclusion;
  sources: Record<string, SourceEntry>;
  disclaimers: { flow?: string; investment?: string };
  factorLabels: Record<string, string>;
  degraded: boolean;
  degradedReason: string;
  warnings: string[];
  cached: boolean;
  storedAt?: string | null;
};

export type IndustryFundsJob = {
  jobId: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed';
  phase: string;
  phaseLabel: string;
  message: string;
  progress: number;
  createdAt: number;
  updatedAt: number;
  result: IndustryFundsOverview | null;
  error: string | null;
};

export type IndustryFundsJobSubmit = {
  job: IndustryFundsJob | null;
  reused: boolean;
  coolingDown: boolean;
  retryAfterSeconds: number;
  message: string;
};

export type IndustryFundsPushResult = {
  ok: boolean;
  skipped: boolean;
  message: string;
  tradeDate: string;
};

export type IndustryFundsHistoryItem = {
  tradeDate: string;
  score: number | null;
  verdict: string | null;
  etfDelta: number | null;
  createdAt: string | null;
};
