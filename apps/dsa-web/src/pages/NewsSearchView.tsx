import type React from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Search } from 'lucide-react';

import { ApiErrorAlert, Button, Card, EmptyState, Input, Loading } from '../components/common';
import { RealtimeNewsList } from '../components/realtime-news/RealtimeNewsList';
import type { RealtimeNewsItem } from '../api/realtimeNews';
import { newsSearchApi } from '../api/newsSearch';
import type { NewsSearchItemListResponse, NewsSearchPlatform } from '../api/newsSearch';
import { getParsedApiError } from '../api/error';
import type { ParsedApiError } from '../api/error';
import { cn } from '../utils/cn';

/** 默认看最近 7 天：各站点关键词命中的条目本就不多，窗口太窄常常只剩个位数。 */
const WINDOW_DAYS = 7;
const RESULT_LIMIT = 50;

/**
 * 「新闻搜索」视图：把关键词交给各站点自己的搜索接口。
 *
 * 由 NewsAnalysisPage 作为顶层 tab 之一挂载，因此不自带 AppPage 容器与页面标题。
 * 后端逐页打上游（最多 4 页）后按时间倒序返回时间窗内的条目，前端只负责编排与展示。
 */
const NewsSearchView: React.FC = () => {
  const [platforms, setPlatforms] = useState<NewsSearchPlatform[]>([]);
  const [platformsError, setPlatformsError] = useState<ParsedApiError | null>(null);
  const [platformsLoading, setPlatformsLoading] = useState(true);
  const [activePlatform, setActivePlatform] = useState('');

  // draft 是输入框里的字，keyword 是真正提交过的那次；两者分开才不会边打字边打上游。
  const [draft, setDraft] = useState('');
  const [keyword, setKeyword] = useState('');

  const [data, setData] = useState<NewsSearchItemListResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);

  // 同一「平台 + 关键词」的结果缓存：切 tab 回来不重复打上游（一次搜索最多 4 个请求）。
  const cacheRef = useRef(new Map<string, NewsSearchItemListResponse>());
  const requestIdRef = useRef(0);

  const loadPlatforms = useCallback(async () => {
    setPlatformsLoading(true);
    setPlatformsError(null);
    try {
      const response = await newsSearchApi.listPlatforms();
      setPlatforms(response.items);
      setActivePlatform((current) => current || response.defaultPlatform);
    } catch (loadError) {
      setPlatformsError(getParsedApiError(loadError));
    } finally {
      setPlatformsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadPlatforms();
  }, [loadPlatforms]);

  const loadResults = useCallback(async (platform: string, query: string) => {
    const cacheKey = `${platform}|${query}`;
    const cached = cacheRef.current.get(cacheKey);
    if (cached) {
      setData(cached);
      setError(null);
      setLoading(false);
      return;
    }

    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    setData(null);
    setError(null);
    setLoading(true);
    try {
      const response = await newsSearchApi.search({
        q: query,
        platform,
        days: WINDOW_DAYS,
        limit: RESULT_LIMIT,
      });
      if (requestIdRef.current !== requestId) return;
      cacheRef.current.set(cacheKey, response);
      setData(response);
    } catch (loadError) {
      if (requestIdRef.current !== requestId) return;
      setError(getParsedApiError(loadError));
    } finally {
      if (requestIdRef.current === requestId) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!keyword || !activePlatform) return;
    void loadResults(activePlatform, keyword);
  }, [keyword, activePlatform, loadResults]);

  const handleSubmit = (event?: React.FormEvent) => {
    event?.preventDefault();
    const next = draft.trim();
    if (!next || next === keyword) return;
    cacheRef.current.clear();
    setKeyword(next);
  };

  const handleSelectPlatform = (key: string) => {
    if (key === activePlatform) return;
    setActivePlatform(key);
  };

  const listItems: RealtimeNewsItem[] = (data?.items ?? []).map((item) => ({
    platform: item.platform,
    platformLabel: item.platformLabel,
    title: item.title,
    summary: item.summary,
    url: item.url,
    publishedAt: item.publishedAt,
    tag: item.source,
  }));

  return (
    <div className="space-y-5">
      <form className="flex flex-wrap items-center gap-2" onSubmit={handleSubmit}>
        <div className="min-w-[240px] flex-1 sm:max-w-md">
          <Input
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="输入关键词搜索新闻，如 比亚迪 / 固态电池 / 美联储降息"
            aria-label="新闻搜索关键词"
          />
        </div>
        <Button type="submit" variant="primary" size="lg" disabled={!draft.trim()}>
          <Search className="mr-1.5 h-4 w-4" aria-hidden="true" />
          搜索
        </Button>
      </form>

      {platformsError ? (
        <ApiErrorAlert error={platformsError} onAction={() => void loadPlatforms()} actionLabel="重试" />
      ) : null}

      <Card variant="bordered" padding="md">
        {platformsLoading && platforms.length === 0 ? (
          <Loading label="正在加载平台列表" />
        ) : (
          <div role="tablist" aria-label="新闻搜索平台" className="flex flex-wrap gap-2">
            {platforms.map((platform) => (
              <button
                key={platform.key}
                type="button"
                role="tab"
                aria-selected={activePlatform === platform.key}
                onClick={() => handleSelectPlatform(platform.key)}
                className={cn(
                  'rounded-full border px-4 py-1.5 text-sm transition-colors',
                  activePlatform === platform.key
                    ? 'border-[hsl(var(--primary))] bg-[hsl(var(--primary))]/10 font-medium text-[hsl(var(--primary))]'
                    : 'border-border text-secondary-text hover:border-[hsl(var(--primary))]/40 hover:text-foreground',
                )}
              >
                {platform.label}
              </button>
            ))}
          </div>
        )}
      </Card>

      {!keyword ? (
        <EmptyState
          icon={<Search className="h-6 w-6" />}
          title="输入关键词开始搜索"
          description={`选择上方平台，查看该站点最近 ${WINDOW_DAYS} 天内与关键词相关的新闻。`}
        />
      ) : null}

      {keyword ? (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-semibold text-foreground">
              {data?.platformLabel || '搜索结果'} · {data?.total ?? 0} 条
            </h2>
            <span className="text-xxs text-muted-text">
              关键词「{keyword}」· 近 {WINDOW_DAYS} 天
              {data ? ` · 上游翻页 ${data.pagesScanned} 次` : ''}
            </span>
          </div>

          {error ? (
            <ApiErrorAlert
              error={error}
              onAction={() => void loadResults(activePlatform, keyword)}
              actionLabel="重试"
            />
          ) : (
            <RealtimeNewsList
              items={listItems}
              loading={loading}
              platformLabel={data?.platformLabel || '该平台'}
              unavailable={false}
            />
          )}
        </div>
      ) : null}
    </div>
  );
};

export default NewsSearchView;
