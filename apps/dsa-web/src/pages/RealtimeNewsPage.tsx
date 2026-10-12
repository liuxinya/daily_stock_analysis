import type React from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { RefreshCw, Search, Sparkles, X } from 'lucide-react';

import {
  ApiErrorAlert,
  AppPage,
  Button,
  Card,
  EmptyState,
  InlineAlert,
  Input,
  Loading,
  PageHeader,
  Pagination,
} from '../components/common';
import { RealtimeNewsList } from '../components/realtime-news/RealtimeNewsList';
import { absoluteTime } from '../components/realtime-news/format';
import { MarketBriefPanel } from '../components/market-brief/MarketBriefPanel';
import { MarketBriefProgress } from '../components/market-brief/MarketBriefProgress';
import { shortDateTime } from '../components/market-brief/format';
import { useMarketBriefStore } from '../stores/marketBriefStore';
import { realtimeNewsApi } from '../api/realtimeNews';
import type { RealtimeNewsItemListResponse, RealtimeNewsPlatform } from '../api/realtimeNews';
import { marketBriefApi } from '../api/marketBrief';
import type { MarketBriefWindow } from '../api/marketBrief';
import { getParsedApiError } from '../api/error';
import type { ParsedApiError } from '../api/error';
import { cn } from '../utils/cn';

const PAGE_SIZE = 20;
const AGGREGATE_PLATFORM = 'hot';
/** 消息面汇总与平台 tab 并列，用一个不会与平台 key 冲突的哨兵值区分 */
const BRIEF_TAB = '__brief__';

const RealtimeNewsPage: React.FC = () => {
  useEffect(() => {
    document.title = '实时新闻 - DSA';
  }, []);

  const [activeTab, setActiveTab] = useState<string>(BRIEF_TAB);
  const [platforms, setPlatforms] = useState<RealtimeNewsPlatform[]>([]);
  const [platformsError, setPlatformsError] = useState<ParsedApiError | null>(null);
  const [platformsLoading, setPlatformsLoading] = useState(true);

  const [page, setPage] = useState(1);
  const [data, setData] = useState<RealtimeNewsItemListResponse | null>(null);
  const [itemsLoading, setItemsLoading] = useState(false);
  const [itemsError, setItemsError] = useState<ParsedApiError | null>(null);

  // 关键词搜索：输入关键词后各平台 tab 改按关键词全文检索；清空回到市场级 feed。
  const [searchInput, setSearchInput] = useState('');
  const [keyword, setKeyword] = useState('');

  const [briefWindow, setBriefWindow] = useState<MarketBriefWindow | null>(null);
  const [briefLoading, setBriefLoading] = useState(false);
  const [briefLoadError, setBriefLoadError] = useState<ParsedApiError | null>(null);

  const briefJob = useMarketBriefStore((state) => state.job);
  const briefResult = useMarketBriefStore((state) => state.result);
  const briefSubmitError = useMarketBriefStore((state) => state.submitError);
  const submitBrief = useMarketBriefStore((state) => state.submit);
  const setBriefResult = useMarketBriefStore((state) => state.setResult);

  // 单调递增的请求 id：切 tab 很快时先发出的响应可能后到，只认最后一次请求的结果
  const platformsRequestIdRef = useRef(0);
  const itemsRequestIdRef = useRef(0);
  const briefLoadedRef = useRef(false);

  const briefRunning = Boolean(briefJob && (briefJob.status === 'queued' || briefJob.status === 'running'));

  const loadPlatforms = useCallback(async () => {
    const requestId = platformsRequestIdRef.current + 1;
    platformsRequestIdRef.current = requestId;
    setPlatformsLoading(true);
    setPlatformsError(null);
    try {
      const response = await realtimeNewsApi.listPlatforms();
      if (platformsRequestIdRef.current !== requestId) return;
      setPlatforms(response.items);
    } catch (error) {
      if (platformsRequestIdRef.current !== requestId) return;
      setPlatformsError(getParsedApiError(error));
    } finally {
      if (platformsRequestIdRef.current === requestId) setPlatformsLoading(false);
    }
  }, []);

  const loadItems = useCallback(
    async (platform: string, targetPage: number, query: string) => {
      const requestId = itemsRequestIdRef.current + 1;
      itemsRequestIdRef.current = requestId;
      setItemsLoading(true);
      setItemsError(null);
      try {
        // 有关键词走 /search（按关键词全文检索），否则走 /items（市场级 feed）。
        const response = query
          ? await realtimeNewsApi.searchItems({
              platform,
              q: query,
              page: targetPage,
              pageSize: PAGE_SIZE,
            })
          : await realtimeNewsApi.listItems({ platform, page: targetPage, pageSize: PAGE_SIZE });
        if (itemsRequestIdRef.current !== requestId) return;
        setData(response);
      } catch (error) {
        if (itemsRequestIdRef.current !== requestId) return;
        setData(null);
        setItemsError(getParsedApiError(error));
      } finally {
        if (itemsRequestIdRef.current === requestId) setItemsLoading(false);
      }
    },
    [],
  );

  /** 进汇总 tab 时先拉当前窗口与最新一份结果，让用户不必先点按钮才有内容看。 */
  const loadBrief = useCallback(async () => {
    setBriefLoading(true);
    setBriefLoadError(null);
    try {
      const window = await marketBriefApi.getWindow();
      setBriefWindow(window);
    } catch (error) {
      setBriefLoadError(getParsedApiError(error));
    }
    try {
      const latest = await marketBriefApi.latest();
      setBriefResult(latest);
    } catch (error) {
      // 还没生成过是 404，属于正常空态，不当作错误
      const parsed = getParsedApiError(error);
      if (parsed.status !== 404) setBriefLoadError(parsed);
    } finally {
      setBriefLoading(false);
    }
  }, [setBriefResult]);

  useEffect(() => {
    void loadPlatforms();
  }, [loadPlatforms]);

  useEffect(() => {
    if (activeTab === BRIEF_TAB) {
      if (briefLoadedRef.current) return;
      briefLoadedRef.current = true;
      void loadBrief();
      return;
    }
    void loadItems(activeTab, page, keyword);
  }, [loadItems, loadBrief, activeTab, page, keyword]);

  const handleSelectTab = (key: string) => {
    if (key === activeTab) return;
    setActiveTab(key);
    setPage(1);
  };

  /** 提交关键词：各平台 tab 改按该关键词检索；从汇总 tab 搜索时顺带切到聚合，直接看结果。 */
  const handleSearchSubmit = useCallback(
    (event?: React.FormEvent) => {
      event?.preventDefault();
      const next = searchInput.trim();
      if (!next) return;
      setKeyword(next);
      setPage(1);
      setActiveTab((current) => (current === BRIEF_TAB ? AGGREGATE_PLATFORM : current));
    },
    [searchInput],
  );

  /** 清除关键词，回到市场级 feed。 */
  const clearSearch = useCallback(() => {
    setKeyword('');
    setSearchInput('');
    setPage(1);
  }, []);

  const activeMeta = platforms.find((item) => item.key === activeTab);
  const platformLabel = data?.platformLabel || activeMeta?.label || '实时';
  const totalPages = Math.max(1, Math.ceil((data?.total ?? 0) / PAGE_SIZE));
  const failedLabels = (data?.failedPlatforms ?? []).map(
    (key) => platforms.find((item) => item.key === key)?.label ?? key,
  );
  const showingBrief = activeTab === BRIEF_TAB;

  return (
    <AppPage className="space-y-5">
      <PageHeader
        eyebrow="Realtime News"
        title="实时新闻"
        description="按平台切换查看各财经站点的最新快讯；「消息面汇总」按当前时段（盘前 / 盘中 / 周末 / 节假日）对窗口内消息做 AI 汇总。"
        actions={showingBrief ? (
          <Button
            variant="secondary"
            size="md"
            onClick={() => void submitBrief(Boolean(briefResult))}
            isLoading={briefRunning}
            loadingText="汇总中"
          >
            <Sparkles className="mr-1.5 h-4 w-4" aria-hidden="true" />
            {briefResult ? '重新汇总' : '生成汇总'}
          </Button>
        ) : (
          <Button
            variant="secondary"
            size="md"
            onClick={() => void loadItems(activeTab, page, keyword)}
            isLoading={itemsLoading}
            loadingText="刷新中"
          >
            <RefreshCw className="mr-1.5 h-4 w-4" aria-hidden="true" />
            刷新
          </Button>
        )}
      />

      {platformsError ? (
        <ApiErrorAlert error={platformsError} onAction={() => void loadPlatforms()} actionLabel="重试" />
      ) : null}

      <Card variant="bordered" padding="md">
        {platformsLoading && platforms.length === 0 ? (
          <Loading label="正在加载平台列表" />
        ) : (
          <div role="tablist" aria-label="新闻视图与平台" className="flex flex-wrap gap-2">
            <button
              key={BRIEF_TAB}
              type="button"
              role="tab"
              aria-selected={showingBrief}
              onClick={() => handleSelectTab(BRIEF_TAB)}
              className={cn(
                'rounded-full border px-4 py-1.5 text-sm transition-colors',
                showingBrief
                  ? 'border-[hsl(var(--primary))] bg-[hsl(var(--primary))]/10 font-medium text-[hsl(var(--primary))]'
                  : 'border-border text-secondary-text hover:border-[hsl(var(--primary))]/40 hover:text-foreground',
              )}
            >
              消息面汇总
            </button>
            {platforms.map((platform) => (
              <button
                key={platform.key}
                type="button"
                role="tab"
                aria-selected={activeTab === platform.key}
                onClick={() => handleSelectTab(platform.key)}
                className={cn(
                  'rounded-full border px-4 py-1.5 text-sm transition-colors',
                  activeTab === platform.key
                    ? 'border-[hsl(var(--primary))] bg-[hsl(var(--primary))]/10 font-medium text-[hsl(var(--primary))]'
                    : 'border-border text-secondary-text hover:border-[hsl(var(--primary))]/40 hover:text-foreground',
                  // 暂无可用新闻源的平台保留入口但弱化，点进去是空态说明
                  !platform.available && activeTab !== platform.key && 'text-muted-text',
                )}
              >
                {platform.label}
              </button>
            ))}
          </div>
        )}
      </Card>

      {/* 关键词搜索：输入后各平台按该关键词全文检索；清空回到市场级 feed。 */}
      <div className="space-y-2">
        <form className="flex flex-wrap items-center gap-2" onSubmit={handleSearchSubmit}>
          <div className="min-w-[240px] flex-1 sm:max-w-sm">
            <Input
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
              placeholder="搜索关键词看相关新闻（如 比亚迪 / 新能源 / 美联储降息）"
              aria-label="按关键词搜索新闻"
              trailingAction={
                <button
                  type="submit"
                  className="inline-flex h-8 w-8 items-center justify-center rounded-lg border border-border/40 bg-muted/20 text-muted-text transition-colors hover:border-[hsl(var(--primary))]/40 hover:text-[hsl(var(--primary))]"
                  aria-label="搜索"
                >
                  <Search className="h-4 w-4" aria-hidden="true" />
                </button>
              }
            />
          </div>
          {keyword ? (
            <button
              type="button"
              onClick={clearSearch}
              className="inline-flex items-center gap-1.5 rounded-full border border-[hsl(var(--primary))] bg-[hsl(var(--primary))]/10 px-3 py-1.5 text-sm font-medium text-[hsl(var(--primary))] transition-colors hover:bg-[hsl(var(--primary))]/20"
              aria-label={`清除搜索：${keyword}`}
            >
              {keyword}
              <X className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          ) : null}
        </form>
        {keyword ? (
          <p className="text-xxs text-muted-text">
            正在搜索「{keyword}」的相关新闻；清除后回到市场级快讯。
          </p>
        ) : null}
      </div>

      {showingBrief ? (
        <div role="tabpanel" className="space-y-4">
          {briefWindow ? (
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-sm font-semibold text-foreground">
                当前时段：{briefWindow.modeLabel} · {briefWindow.windowHint}
              </h2>
              <span className="text-xxs text-muted-text">
                {shortDateTime(briefWindow.start)} → {shortDateTime(briefWindow.end)}
                （{briefWindow.durationHours} 小时）
              </span>
            </div>
          ) : null}

          {briefLoadError ? (
            <ApiErrorAlert error={briefLoadError} onAction={() => void loadBrief()} actionLabel="重试" />
          ) : null}
          {briefSubmitError ? (
            <ApiErrorAlert
              error={briefSubmitError}
              onAction={() => void submitBrief(false)}
              actionLabel="重试"
            />
          ) : null}

          {briefJob && briefRunning ? <MarketBriefProgress job={briefJob} /> : null}

          {briefJob?.status === 'failed' && !briefRunning ? (
            <InlineAlert
              variant="danger"
              title="上一次汇总失败"
              message={briefJob.error || briefJob.message || '请稍后重试。'}
            />
          ) : null}

          {briefLoading && !briefResult ? <Loading label="正在读取最新汇总" /> : null}

          {!briefLoading && !briefResult && !briefRunning && !briefLoadError ? (
            <EmptyState
              icon={<Sparkles className="h-6 w-6" />}
              title="还没有这个时段的消息面汇总"
              description="点右上角「生成汇总」现在跑一份；开启 MARKET_BRIEF_ENABLED 后系统会在盘前、盘中与休市日自动生成。"
            />
          ) : null}

          {briefResult ? <MarketBriefPanel payload={briefResult} /> : null}
        </div>
      ) : (
        <div role="tabpanel" className="space-y-4">
          {failedLabels.length > 0 ? (
            <InlineAlert
              variant="warning"
              title="部分平台本次没拉到内容"
              message={`${failedLabels.join('、')} 暂时不可用，已跳过；其余平台内容正常展示。`}
            />
          ) : null}

          {itemsError ? (
            <ApiErrorAlert
              error={itemsError}
              onAction={() => void loadItems(activeTab, page, keyword)}
              actionLabel="重试"
            />
          ) : null}

          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-semibold text-foreground">
              {platformLabel} · {data?.total ?? 0} 条
            </h2>
            {data?.fetchedAt ? (
              <span className="text-xxs text-muted-text">拉取于 {absoluteTime(data.fetchedAt)}</span>
            ) : null}
          </div>

          {itemsError ? null : (
            <RealtimeNewsList
              items={data?.items ?? []}
              loading={itemsLoading}
              unavailable={data ? !data.available : false}
              platformLabel={platformLabel}
              showPlatformBadge={activeTab === AGGREGATE_PLATFORM}
              unavailableTitle={keyword ? `${platformLabel}不支持关键词搜索` : undefined}
              unavailableDescription={
                keyword
                  ? '该平台只提供市场级快讯，没有全文搜索接口；换到财经热榜 / 财联社 / 东方财富，或清除关键词再看。'
                  : undefined
              }
              emptyTitle={keyword ? `没搜到「${keyword}」的相关新闻` : undefined}
              emptyDescription={
                keyword
                  ? '换个关键词，或到「财经热榜」聚合搜索，也可以稍后再刷新。'
                  : undefined
              }
            />
          )}

          {totalPages > 1 ? (
            <Pagination
              currentPage={page}
              totalPages={totalPages}
              onPageChange={(next) => setPage(next)}
              className="mt-4"
            />
          ) : null}
        </div>
      )}
    </AppPage>
  );
};

export default RealtimeNewsPage;
