import apiClient from './index';
import { toCamelCase } from './utils';

export type RealtimeNewsPlatform = {
  key: string;
  label: string;
  provider: string;
  /** 上游暂无可用新闻源时为 false，页面展示空态而不是报错 */
  available: boolean;
  /** 上游是否提供发布时间；为 false 的平台不参与「财经热榜」聚合排序 */
  hasPublishedAt: boolean;
  /** 是否支持「按任意关键词全文搜索」；为 false 的平台在搜索模式下展示空态 */
  supportsSearch: boolean;
};

export type RealtimeNewsPlatformListResponse = {
  items: RealtimeNewsPlatform[];
  total: number;
  defaultPlatform: string;
};

export type RealtimeNewsItem = {
  platform: string;
  platformLabel: string;
  title: string;
  summary: string;
  url: string;
  /** 带时区偏移的 ISO8601；上游不提供时间时为 null */
  publishedAt: string | null;
  tag: string;
};

export type RealtimeNewsItemListResponse = {
  platform: string;
  platformLabel: string;
  available: boolean;
  hasPublishedAt: boolean;
  items: RealtimeNewsItem[];
  total: number;
  page: number;
  pageSize: number;
  /** 聚合模式下失败的平台 key */
  failedPlatforms: string[];
  fetchedAt: string | null;
  /** 仅搜索模式回显：本次使用的搜索关键词（市场级 /items 为 null） */
  query: string | null;
};

export type RealtimeNewsItemListQuery = {
  platform?: string;
  page?: number;
  pageSize?: number;
};

export type RealtimeNewsSearchQuery = {
  platform?: string;
  /** 任意搜索关键词（股票名、行业、事件等） */
  q: string;
  page?: number;
  pageSize?: number;
};

function toItemListParams(query: RealtimeNewsItemListQuery = {}): Record<string, string | number> {
  const params: Record<string, string | number> = {};
  if (query.platform) params.platform = query.platform;
  if (query.page !== undefined) params.page = query.page;
  if (query.pageSize !== undefined) params.page_size = query.pageSize;
  return params;
}

function toSearchParams(query: RealtimeNewsSearchQuery): Record<string, string | number> {
  const params: Record<string, string | number> = { q: query.q };
  if (query.platform) params.platform = query.platform;
  if (query.page !== undefined) params.page = query.page;
  if (query.pageSize !== undefined) params.page_size = query.pageSize;
  return params;
}

export const realtimeNewsApi = {
  listPlatforms: async (): Promise<RealtimeNewsPlatformListResponse> => {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/realtime-news/platforms');
    return toCamelCase<RealtimeNewsPlatformListResponse>(response.data);
  },

  // 请求时直连上游，首次未命中缓存时会慢一些（聚合要并发拉多个平台），因此放宽超时。
  listItems: async (query: RealtimeNewsItemListQuery = {}): Promise<RealtimeNewsItemListResponse> => {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/realtime-news/items', {
      params: toItemListParams(query),
      timeout: 30000,
    });
    return toCamelCase<RealtimeNewsItemListResponse>(response.data);
  },

  // 关键词搜索：按任意关键词全文检索（东财/财联社）；聚合（hot）会并发拉多个平台，放宽超时。
  searchItems: async (query: RealtimeNewsSearchQuery): Promise<RealtimeNewsItemListResponse> => {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/realtime-news/search', {
      params: toSearchParams(query),
      timeout: 30000,
    });
    return toCamelCase<RealtimeNewsItemListResponse>(response.data);
  },
};

export default realtimeNewsApi;
