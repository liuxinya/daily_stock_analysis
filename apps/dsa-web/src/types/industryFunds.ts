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
  net5D: number | null;
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
  change1M?: number | null;
  close?: number | null;
  marketCap?: number | null;
  reason?: string;
  lastListed?: string;
  industry?: string;
  industryScore?: number | null;
  industryNet?: number | null;
  industryNet5D?: number | null;
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
  /** 新鲜度：读缓存时由后端填充。stale 为真表示距生成已超过 cacheTtlMinutes，仅提示不自动重抓。 */
  stale?: boolean;
  cacheTtlMinutes?: number;
  ageMinutes?: number | null;
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

/** 资金流向的日期 tab：最近 N 个交易日，has_overview 表示那天有没有「总览」快照。 */
export type IndustryFundsFlowDate = { date: string; hasOverview: boolean };

export type IndustryFundsFlowDates = { days: IndustryFundsFlowDate[]; today: string };

/** 汪汪队：单只宽基 ETF 在某个交易日的份额变动与成交额。单位：份/亿元（amount 已折算成亿元）。 */
export type NationalTeamEtf = {
  code: string;
  name: string;
  shares: number | null;
  deltaShares: number | null;
  deltaAmount: number | null;
  turnover: number | null;
  turnoverRatio: number | null;
};

export type NationalTeamDay = {
  date: string;
  /** 当日宽基 ETF 份额净申购（亿份）；该日沪市份额还没更新时为 null，不是 0 */
  netShares: number | null;
  /** 估算净流入（亿元）= 份额净申购 × 当日收盘价 */
  netAmount: number | null;
  etfCount: number;
  turnover: number | null;
  turnoverRatio: number | null;
  /** 该日沪市份额未取到（盘中 / 节假日上游还没更新） */
  sseMissing?: boolean;
  etfs: NationalTeamEtf[];
};

export type NationalTeamFlow = {
  days: NationalTeamDay[];
  series: { date: string; netShares: number | null; netAmount: number | null; turnoverRatio: number | null }[];
  inflowStreak: number;
  asOf: string;
  baseDate?: string;
  coverage?: { sse?: string; szse?: string };
  degraded: boolean;
  degradedReason: string;
  szse?: { date: string | null; etfs: { code: string; name: string; shares: number | null }[] };
  szseNote?: string;
};

/** 龙虎榜机构席位净买：单只个股在某天的机构买卖。单位：亿元。 */
export type InstitutionStock = {
  code: string;
  name: string;
  close: number | null;
  changePct: number | null;
  buyerCount: number | null;
  sellerCount: number | null;
  buy: number | null;
  sell: number | null;
  net: number | null;
  /** 机构净买额占总成交额比（%） */
  netRatio: number | null;
  turnoverRate: number | null;
  reason: string;
};

export type InstitutionDay = {
  date: string;
  net: number | null;
  buy: number | null;
  sell: number | null;
  count: number;
  /** 该日龙虎榜还没披露（收盘后才发布） */
  pending?: boolean;
  stocks: InstitutionStock[];
};

export type InstitutionFlow = {
  days: InstitutionDay[];
  series: { date: string; net: number | null; count: number }[];
  asOf: string;
  degraded: boolean;
  degradedReason: string;
};

export type IndustryFundsFlows = {
  days: string[];
  nationalTeam: NationalTeamFlow;
  institutions: InstitutionFlow;
  generatedAt: string;
  durationMs: number;
};

/** 游资席位矩阵：列 = 营业部席位，行 = 股票，格子里是该席位当天的买卖额（亿元）。 */
export type HotMoneySeat = {
  code: string;
  name: string;
  /** 压短后的列头名（营业部短名） */
  short: string;
  /** 人工整理的游资昵称；为空时列头显示营业部短名 */
  alias?: string | null;
  /** 昵称的核对依据（悬停时展示，方便判断是否过期） */
  aliasSource?: string;
  /** 这一列/这条席位背后的营业部代码（同昵称合并时会有多个） */
  codes?: string[];
  buy: number | null;
  sell: number | null;
  net: number | null;
  days: number;
};

export type HotMoneyCell = { buy: number; sell: number; net: number };

export type HotMoneyRow = {
  code: string;
  name: string;
  buy: number;
  sell: number;
  net: number;
  /** 该票当天涨跌幅（%），来自龙虎榜明细；取不到为 null */
  changePct?: number | null;
  /** 席位代码 → 该席位在这只票上的买卖 */
  cells: Record<string, HotMoneyCell>;
};

export type HotMoneyDay = { date: string; rows: HotMoneyRow[]; total: number };

/** 逐日的游资参与度与龙虎榜结构（结论表用）。席位/买卖是游资口径，结构是全龙虎榜口径。 */
export type HotMoneyDaily = {
  date: string;
  /** 当天有买入记录的营业部席位数（剔除通道 / 机构专用） */
  seats: number;
  /** 营业部口径的买入 / 卖出 / 净买（亿元） */
  buy: number | null;
  sell: number | null;
  net: number | null;
  /** 净买 / 买入 */
  netRatio: number | null;
  /** 以下为**全龙虎榜**口径（含机构专用与沪深股通），不是游资口径 */
  listed: number;
  upShare: number | null;
  upBuyShare: number | null;
  downShare: number | null;
  downBuyShare: number | null;
  limitShare: number | null;
};

export type HotMoneyVerdict = { text: string; tags: string[] };

export type IndustryFundsHotMoney = {
  days: string[];
  /** 矩阵的列（按「人名昵称 > 街道昵称 > 未识别」排序，最多 20 个） */
  seats: HotMoneySeat[];
  /** 「游资席位」区展示的全部席位（含未进列的），默认收起成两行 */
  allSeats?: HotMoneySeat[];
  dates: HotMoneyDay[];
  daily: HotMoneyDaily[];
  verdict: HotMoneyVerdict;
  marketNote?: string;
  rowLimit: number;
  degraded: boolean;
  degradedReason: string;
  coverage: string;
  /** 列头昵称是人工整理的说明 */
  aliasNote?: string;
};

/** 某个游资席位（或一个昵称下的多家营业部）某天的逐股买卖，用于席位悬停气泡。 */
export type HotMoneySeatStock = {
  code: string;
  name: string;
  buy: number;
  sell: number;
  net: number;
  changePct?: number | null;
};

export type HotMoneySeatDetail = {
  date: string;
  stocks: HotMoneySeatStock[];
  degraded: boolean;
  degradedReason: string;
};
