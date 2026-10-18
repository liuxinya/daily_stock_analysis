import apiClient from './index';
import { toCamelCase } from './utils';

/**
 * 新闻搜索代理客户端。
 *
 * 后端 `/api/v1/news-search/*` 负责把关键词打到各站点自己的搜索接口并归一化，
 * 因此这里的类型只声明页面实际读取的字段。
 */

export type NewsSearchPlatform = {
  key: string;
  label: string;
};

export type NewsSearchPlatformListResponse = {
  items: NewsSearchPlatform[];
  total: number;
  defaultPlatform: string;
};

export type NewsSearchItem = {
  platform: string;
  platformLabel: string;
  title: string;
  summary: string;
  url: string;
  /** 媒体名 / 频道名；上游不提供时为空 */
  source: string;
  /** 带时区偏移的 ISO8601；上游不提供时间时为 null */
  publishedAt: string | null;
};

export type NewsSearchItemListResponse = {
  platform: string;
  platformLabel: string;
  query: string;
  windowDays: number;
  items: NewsSearchItem[];
  total: number;
  pagesScanned: number;
  fetchedAt: string | null;
};

/** 全平台聚合搜索的响应。多一个失败平台列表；聚合 fail-open，不会因为上游问题整体 503。 */
export type NewsSearchAggregateResponse = NewsSearchItemListResponse & {
  /** 本次未取到结果的平台 key（超时或上游异常），为空表示全部平台都正常 */
  failedPlatforms: string[];
};

export type NewsSearchQuery = {
  q: string;
  platform?: string;
  /** 时间窗（天）；后端默认 7 天 */
  days?: number;
  limit?: number;
};

export type NewsSearchAggregateQuery = Omit<NewsSearchQuery, 'platform'>;

export const newsSearchApi = {
  listPlatforms: async (): Promise<NewsSearchPlatformListResponse> => {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/news-search/platforms');
    return toCamelCase<NewsSearchPlatformListResponse>(response.data);
  },

  // 后端要逐页打上游（最多 4 页），首屏比普通接口慢，放宽超时。
  search: async (query: NewsSearchQuery): Promise<NewsSearchItemListResponse> => {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/news-search/items', {
      params: {
        q: query.q,
        ...(query.platform ? { platform: query.platform } : {}),
        ...(query.days !== undefined ? { days: query.days } : {}),
        ...(query.limit !== undefined ? { limit: query.limit } : {}),
      },
      timeout: 40000,
    });
    return toCamelCase<NewsSearchItemListResponse>(response.data);
  },

  // 一次并发打 5 个平台，后端有 30s 总预算，所以超时要比单平台更宽。
  searchAll: async (query: NewsSearchAggregateQuery): Promise<NewsSearchAggregateResponse> => {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/news-search/aggregate', {
      params: {
        q: query.q,
        ...(query.days !== undefined ? { days: query.days } : {}),
        ...(query.limit !== undefined ? { limit: query.limit } : {}),
      },
      timeout: 45000,
    });
    return toCamelCase<NewsSearchAggregateResponse>(response.data);
  },
};

export default newsSearchApi;
