import camelcaseKeys from 'camelcase-keys';
import apiClient from './index';
import { toCamelCase } from './utils';
import type {
  DabanHistoryItem,
  DabanJob,
  DabanJobSubmit,
  DabanOverview,
  DabanPushResult,
} from '../types/daban';

/** 404 = 该交易日还没生成过复盘，是正常状态而不是错误，页面据此提示「点生成」。 */
export const DABAN_NOT_GENERATED = 'not_generated';

const BASE = '/api/v1/daban';

/**
 * 这几个字典的键是 id（理念条目 K01 / C04、模式 M1），不能被转成 k01 / m1，
 * 否则页面按 id 查出处会全部落空。路径写的是转换前的 snake_case 原名。
 */
const ID_KEYED_PATHS = ['principles', 'mode_labels', 'style_labels'];

function convertOverview<T>(data: unknown, prefix = ''): T {
  if (data === null || data === undefined) {
    return data as T;
  }
  return camelcaseKeys(data as Record<string, unknown>, {
    deep: true,
    stopPaths: ID_KEYED_PATHS.map((path) => `${prefix}${path}`),
  }) as T;
}

function isNotFound(error: unknown): boolean {
  return (error as { response?: { status?: number } })?.response?.status === 404;
}

export const dabanApi = {
  getStatus: async (): Promise<{ enabled: boolean }> => {
    const response = await apiClient.get<Record<string, unknown>>(`${BASE}/status`);
    return toCamelCase<{ enabled: boolean }>(response.data);
  },

  /**
   * 读已落库的复盘。**不会触发抓取**（全量生成 30~150 秒，超过客户端 30 秒超时）。
   * 没有快照时抛出标记了 `DABAN_NOT_GENERATED` 的错误。
   */
  getOverview: async (tradeDate?: string): Promise<DabanOverview> => {
    try {
      const response = await apiClient.get<Record<string, unknown>>(`${BASE}/overview`, {
        params: tradeDate ? { trade_date: tradeDate } : undefined,
      });
      return convertOverview<DabanOverview>(response.data);
    } catch (error) {
      if (isNotFound(error)) {
        const marker = new Error(DABAN_NOT_GENERATED);
        (marker as Error & { code?: string }).code = DABAN_NOT_GENERATED;
        throw marker;
      }
      throw error;
    }
  },

  getHistory: async (limit = 10): Promise<DabanHistoryItem[]> => {
    const response = await apiClient.get<Record<string, unknown>>(`${BASE}/history`, { params: { limit } });
    return toCamelCase<{ items: DabanHistoryItem[] }>(response.data).items ?? [];
  },

  submitJob: async (tradeDate?: string): Promise<DabanJobSubmit> => {
    const response = await apiClient.post<Record<string, unknown>>(
      `${BASE}/jobs`,
      tradeDate ? { trade_date: tradeDate } : {}
    );
    return convertOverview<DabanJobSubmit>(response.data, 'job.result.');
  },

  getJob: async (jobId: string): Promise<DabanJob> => {
    const response = await apiClient.get<Record<string, unknown>>(`${BASE}/jobs/${jobId}`);
    return convertOverview<DabanJob>(response.data, 'result.');
  },

  /** 接上「生成到一半关掉页面」的进度；没有进行中的任务时返回 null。 */
  getActiveJob: async (): Promise<DabanJob | null> => {
    try {
      const response = await apiClient.get<Record<string, unknown>>(`${BASE}/jobs`);
      return convertOverview<DabanJob>(response.data, 'result.');
    } catch (error) {
      if (isNotFound(error)) {
        return null;
      }
      throw error;
    }
  },

  push: async (): Promise<DabanPushResult> => {
    const response = await apiClient.post<Record<string, unknown>>(`${BASE}/push`);
    return toCamelCase<DabanPushResult>(response.data);
  },
};

export default dabanApi;
