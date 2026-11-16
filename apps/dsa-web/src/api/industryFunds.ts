import apiClient from './index';
import { toCamelCase } from './utils';
import type {
  IndustryFundsFlowDates,
  IndustryFundsHotMoney,
  HotMoneySeatDetail,
  IndustryFundsFlows,
  IndustryFundsHistoryItem,
  IndustryFundsJob,
  IndustryFundsJobSubmit,
  IndustryFundsOverview,
  IndustryFundsPushResult,
} from '../types/industryFunds';

/** 404 = 还没生成过快照，是正常状态而不是错误，调用方据此提示「点刷新生成」。 */
export const INDUSTRY_FUNDS_NOT_GENERATED = 'not_generated';

export const industryFundsApi = {
  getStatus: async (): Promise<{ enabled: boolean }> => {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/industry-funds/status');
    return toCamelCase<{ enabled: boolean }>(response.data);
  },

  /**
   * 读缓存快照。**不会触发抓取** —— 全量抓取要 30~90 秒，会超过客户端 30 秒超时，
   * 所以刷新必须走 `submitJob` 走后台任务。
   *
   * 没有缓存时抛出标记了 `INDUSTRY_FUNDS_NOT_GENERATED` 的错误，由页面区分
   * 「还没生成」与「真出错」两种状态。
   */
  getOverview: async (tradeDate?: string): Promise<IndustryFundsOverview> => {
    try {
      const response = await apiClient.get<Record<string, unknown>>(
        '/api/v1/industry-funds/overview',
        { params: tradeDate ? { trade_date: tradeDate } : undefined }
      );
      return toCamelCase<IndustryFundsOverview>(response.data);
    } catch (error) {
      const status = (error as { response?: { status?: number } })?.response?.status;
      if (status === 404) {
        const marker = new Error(INDUSTRY_FUNDS_NOT_GENERATED);
        (marker as Error & { code?: string }).code = INDUSTRY_FUNDS_NOT_GENERATED;
        throw marker;
      }
      throw error;
    }
  },

  /** 日期 tab：最近 N 个交易日，以及哪天有「总览」快照。 */
  getFlowDates: async (days = 10): Promise<IndustryFundsFlowDates> => {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/industry-funds/flow-dates', {
      params: { days },
    });
    return toCamelCase<IndustryFundsFlowDates>(response.data);
  },

  /** 汪汪队 + 机构的日频序列（含每日明细）。首次约 5~10 秒，之后走服务端 10 分钟缓存。 */
  getFlows: async (days = 10): Promise<IndustryFundsFlows> => {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/industry-funds/flows', {
      params: { days },
    });
    return toCamelCase<IndustryFundsFlows>(response.data);
  },

  /** 游资席位 × 股票的买卖矩阵。首次约 3~5 秒，之后走服务端 10 分钟缓存。 */
  getHotMoney: async (days = 10, seats = 10): Promise<IndustryFundsHotMoney> => {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/industry-funds/hot-money', {
      params: { days, seats },
    });
    return toCamelCase<IndustryFundsHotMoney>(response.data);
  },

  /** 某个席位（或一个昵称下的多家营业部）某天的逐股买卖，供席位悬停气泡用。 */
  getSeatDetail: async (codes: string[], date: string): Promise<HotMoneySeatDetail> => {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/industry-funds/hot-money/seat', {
      params: { codes: codes.join(','), date },
    });
    return toCamelCase<HotMoneySeatDetail>(response.data);
  },

  getHistory: async (limit = 30): Promise<IndustryFundsHistoryItem[]> => {
    const response = await apiClient.get<Record<string, unknown>>(
      '/api/v1/industry-funds/history',
      { params: { limit } }
    );
    const payload = toCamelCase<{ items: IndustryFundsHistoryItem[] }>(response.data);
    return payload.items ?? [];
  },

  submitJob: async (): Promise<IndustryFundsJobSubmit> => {
    const response = await apiClient.post<Record<string, unknown>>('/api/v1/industry-funds/jobs');
    return toCamelCase<IndustryFundsJobSubmit>(response.data);
  },

  getJob: async (jobId: string): Promise<IndustryFundsJob> => {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/industry-funds/jobs/${jobId}`
    );
    return toCamelCase<IndustryFundsJob>(response.data);
  },

  /** 页面加载时接上「刷新到一半关掉页面」的进度；没有进行中的任务时返回 null。 */
  getActiveJob: async (): Promise<IndustryFundsJob | null> => {
    try {
      const response = await apiClient.get<Record<string, unknown>>(
        '/api/v1/industry-funds/jobs'
      );
      return toCamelCase<IndustryFundsJob>(response.data);
    } catch (error) {
      const status = (error as { response?: { status?: number } })?.response?.status;
      if (status === 404) {
        return null;
      }
      throw error;
    }
  },

  push: async (): Promise<IndustryFundsPushResult> => {
    const response = await apiClient.post<Record<string, unknown>>('/api/v1/industry-funds/push');
    return toCamelCase<IndustryFundsPushResult>(response.data);
  },
};

export default industryFundsApi;
