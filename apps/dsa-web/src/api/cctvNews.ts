import apiClient from './index';
import { toCamelCase } from './utils';

export type CctvAnalysisRange = 1 | 3 | 7 | 10 | 20 | 30;
export type MarketDirection = 'bullish' | 'neutral' | 'bearish';
export type EvidenceStrength = 'high' | 'medium' | 'low';

export type CctvNewsDaySummary = {
  newsDate: string;
  itemCount: number;
  charCount: number;
};

export type CctvNewsDayListResponse = {
  startDate: string;
  endDate: string;
  totalDays: number;
  totalItems: number;
  days: CctvNewsDaySummary[];
};

export type CctvNewsItem = {
  id: number;
  newsDate: string | null;
  seq: number;
  title: string;
  summary: string;
  url: string;
  charCount: number;
  fetchedAt: string | null;
  content?: string | null;
};

export type CctvNewsItemListResponse = {
  items: CctvNewsItem[];
  total: number;
  page: number;
  pageSize: number;
  startDate: string;
  endDate: string;
};

export type CctvNewsSyncDayResult = {
  newsDate: string;
  listedCount: number;
  itemCount: number;
  created: number;
  updated: number;
  failedCount: number;
  skippedCount: number;
  isComplete: boolean;
};

export type CctvNewsSyncResponse = {
  startDate: string;
  endDate: string;
  requestedDays: number;
  syncedDays: number;
  created: number;
  updated: number;
  deleted: number;
  days: CctvNewsSyncDayResult[];
};

export type CctvPolicySignal = {
  topic: string;
  detail: string;
  evidenceIds: number[];
  mentionedTerms: string[];
};

export type CctvIndustryOpportunity = {
  name: string;
  direction: MarketDirection;
  strength: EvidenceStrength;
  reason: string;
  evidenceIds: number[];
  mentionedTerms: string[];
};

export type CctvStockCandidate = {
  name: string;
  code: string | null;
  industry: string;
  direction: MarketDirection;
  isLeader: boolean;
  rationale: string;
  evidenceIds: number[];
};

export type CctvInvestorTakeaway = {
  topic: string;
  detail: string;
  evidenceIds: number[];
  mentionedTerms: string[];
};

export type CctvAnalysisBody = {
  schemaVersion: string;
  overallTone: MarketDirection;
  summary: string;
  policySignals: CctvPolicySignal[];
  industries: CctvIndustryOpportunity[];
  stocks: CctvStockCandidate[];
  investorTakeaways: CctvInvestorTakeaway[];
  risks: string[];
  keywords: string[];
  notCovered: string[];
  stockDisclaimer: string;
};

export type CctvAnalysisEvidence = {
  id: number;
  itemId: number;
  newsDate: string | null;
  title: string;
  url: string;
};

export type CctvAnalysisGrounding = {
  dropped?: string[];
  droppedCount?: number;
  invalidEvidenceIds?: number[];
  unverifiedTerms?: string[];
};

export type CctvAnalysisCoverage = {
  chunkCount?: number;
  analyzedChunkCount?: number;
  llmCallCount?: number;
  skippedItemIds?: number[];
  skippedItemCount?: number;
  budgetExhausted?: boolean;
  overBudgetDropped?: number;
  redaction?: {
    redactedTerms?: string[];
    redactedItemCount?: number;
    droppedItemCount?: number;
  };
};

export type CctvNewsAnalysisResponse = {
  schemaVersion: string;
  promptVersion: string;
  startDate: string;
  endDate: string;
  rangeDays: number;
  itemCount: number;
  model: string | null;
  provider: string | null;
  durationMs: number;
  cached: boolean;
  analysisId: number | null;
  createdAt: string | null;
  analysis: CctvAnalysisBody;
  evidence: CctvAnalysisEvidence[];
  grounding: CctvAnalysisGrounding;
  coverage: CctvAnalysisCoverage;
  degraded: boolean;
  degradedReason: string | null;
};

export type CctvAnalysisJobStatus = 'queued' | 'running' | 'succeeded' | 'failed';

export type CctvNewsAnalysisJob = {
  jobId: string;
  status: CctvAnalysisJobStatus;
  phase: string;
  phaseLabel: string;
  message: string;
  rangeDays: number;
  startDate: string;
  endDate: string;
  totalItems: number;
  totalChunks: number;
  currentChunk: number;
  progress: number;
  createdAt: number;
  updatedAt: number;
  result: CctvNewsAnalysisResponse | null;
  error: string | null;
};

export type CctvAnalysisSubmit = {
  rangeDays?: CctvAnalysisRange;
  newsDate?: string;
  force?: boolean;
};

export type CctvNewsItemListQuery = {
  newsDate?: string;
  days?: number;
  query?: string;
  page?: number;
  pageSize?: number;
  includeContent?: boolean;
};

function toItemListParams(query: CctvNewsItemListQuery = {}): Record<string, string | number | boolean> {
  const params: Record<string, string | number | boolean> = {};
  if (query.newsDate) params.news_date = query.newsDate;
  if (query.days !== undefined) params.days = query.days;
  if (query.query) params.query = query.query;
  if (query.page !== undefined) params.page = query.page;
  if (query.pageSize !== undefined) params.page_size = query.pageSize;
  if (query.includeContent !== undefined) params.include_content = query.includeContent;
  return params;
}

export const cctvNewsApi = {
  listDays: async (days = 30): Promise<CctvNewsDayListResponse> => {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/cctv-news/days', {
      params: { days },
    });
    return toCamelCase<CctvNewsDayListResponse>(response.data);
  },

  listItems: async (query: CctvNewsItemListQuery = {}): Promise<CctvNewsItemListResponse> => {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/cctv-news/items', {
      params: toItemListParams(query),
    });
    return toCamelCase<CctvNewsItemListResponse>(response.data);
  },

  getItem: async (itemId: number): Promise<CctvNewsItem> => {
    const response = await apiClient.get<Record<string, unknown>>(`/api/v1/cctv-news/items/${itemId}`);
    return toCamelCase<CctvNewsItem>(response.data);
  },

  // 抓取要逐条访问央视网页面，单日十几条，因此给足超时。
  sync: async (params: { days?: number; force?: boolean } = {}): Promise<CctvNewsSyncResponse> => {
    const response = await apiClient.post<Record<string, unknown>>(
      '/api/v1/cctv-news/sync',
      { days: params.days, force: params.force ?? false },
      { timeout: 300000 },
    );
    return toCamelCase<CctvNewsSyncResponse>(response.data);
  },

  // 提交后台分析任务，立即返回 job；前端轮询 getAnalysisJob 看进度。
  submitAnalysisJob: async (params: CctvAnalysisSubmit): Promise<CctvNewsAnalysisJob> => {
    const body: Record<string, unknown> = { force: params.force ?? false };
    if (params.newsDate) body.news_date = params.newsDate;
    else body.range_days = params.rangeDays ?? 7;
    const response = await apiClient.post<Record<string, unknown>>(
      '/api/v1/cctv-news/analysis/jobs',
      body,
    );
    return toCamelCase<CctvNewsAnalysisJob>(response.data);
  },

  getAnalysisJob: async (jobId: string): Promise<CctvNewsAnalysisJob> => {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/cctv-news/analysis/jobs/${jobId}`,
    );
    return toCamelCase<CctvNewsAnalysisJob>(response.data);
  },

  latestAnalysis: async (rangeDays?: number): Promise<CctvNewsAnalysisResponse> => {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/cctv-news/analysis/latest', {
      params: rangeDays ? { range_days: rangeDays } : {},
    });
    return toCamelCase<CctvNewsAnalysisResponse>(response.data);
  },
};
