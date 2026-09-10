import apiClient from './index';
import { toCamelCase } from './utils';

export type BriefMode = 'premarket' | 'intraday' | 'postmarket' | 'weekend' | 'holiday' | 'unknown';
export type MarketDirection = 'bullish' | 'neutral' | 'bearish';
export type EvidenceStrength = 'high' | 'medium' | 'low';

export type MarketBriefWindow = {
  market: string;
  mode: BriefMode;
  modeLabel: string;
  phase: string;
  windowHint: string;
  now: string;
  start: string;
  end: string;
  durationHours: number;
  /** 交易日历不可用或边界缺失时退化成固定回看窗口 */
  degraded: boolean;
  degradedReason: string;
  warnings: string[];
};

export type BriefEvent = {
  topic: string;
  detail: string;
  category: string;
  importance: EvidenceStrength;
  evidenceIds: number[];
  mentionedTerms: string[];
};

export type BriefSector = {
  name: string;
  direction: MarketDirection;
  strength: EvidenceStrength;
  reason: string;
  evidenceIds: number[];
  mentionedTerms: string[];
};

export type BriefWatchItem = {
  name: string;
  code: string | null;
  sector: string;
  direction: MarketDirection;
  rationale: string;
  evidenceIds: number[];
};

export type BriefTakeaway = {
  topic: string;
  detail: string;
  evidenceIds: number[];
  mentionedTerms: string[];
};

export type MarketBriefResult = {
  schemaVersion: string;
  mode: BriefMode;
  modeLabel: string;
  windowStart: string | null;
  windowEnd: string | null;
  overallTone: MarketDirection;
  summary: string;
  events: BriefEvent[];
  sectors: BriefSector[];
  watchList: BriefWatchItem[];
  takeaways: BriefTakeaway[];
  risks: string[];
  keywords: string[];
  notCovered: string[];
  watchListDisclaimer: string;
};

export type MarketBriefPayload = {
  schemaVersion: string;
  mode: BriefMode;
  modeLabel: string;
  window: MarketBriefWindow | null;
  itemCount: number;
  chunkCount: number;
  model: string | null;
  provider: string | null;
  result: MarketBriefResult;
  grounding: {
    dropped: string[];
    droppedCount: number;
    unverifiedTerms: string[];
    invalidEvidenceIds: number[];
  };
  coverage: { totalItems: number; citedItems: number };
  degraded: boolean;
  degradedReason: string;
  llmCalls: number;
  durationMs: number;
  cached: boolean;
  createdAt: string | null;
};

export type MarketBriefJob = {
  jobId: string;
  mode: BriefMode;
  modeLabel: string;
  windowStart: string | null;
  windowEnd: string | null;
  status: 'queued' | 'running' | 'succeeded' | 'failed';
  phase: string;
  phaseLabel: string;
  message: string;
  totalItems: number;
  totalChunks: number;
  currentChunk: number;
  progress: number;
  createdAt: number;
  updatedAt: number;
  result: MarketBriefPayload | null;
  error: string | null;
};

export const marketBriefApi = {
  getWindow: async (): Promise<MarketBriefWindow> => {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/market-brief/window');
    return toCamelCase<MarketBriefWindow>(response.data);
  },

  latest: async (mode?: string): Promise<MarketBriefPayload> => {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/market-brief/latest', {
      params: mode ? { mode } : {},
    });
    return toCamelCase<MarketBriefPayload>(response.data);
  },

  submitJob: async (force = false): Promise<MarketBriefJob> => {
    const response = await apiClient.post<Record<string, unknown>>(
      '/api/v1/market-brief/jobs',
      { force },
    );
    return toCamelCase<MarketBriefJob>(response.data);
  },

  getJob: async (jobId: string): Promise<MarketBriefJob> => {
    const response = await apiClient.get<Record<string, unknown>>(`/api/v1/market-brief/jobs/${jobId}`);
    return toCamelCase<MarketBriefJob>(response.data);
  },
};

export default marketBriefApi;
