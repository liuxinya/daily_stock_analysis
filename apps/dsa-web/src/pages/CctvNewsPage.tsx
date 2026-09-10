import type React from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { RefreshCw, Search, Sparkles } from 'lucide-react';

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
import { CctvAnalysisProgress } from '../components/cctv-news/CctvAnalysisProgress';
import { CctvNewsAnalysisPanel } from '../components/cctv-news/CctvNewsAnalysisPanel';
import { CctvNewsDayRail } from '../components/cctv-news/CctvNewsDayRail';
import { CctvNewsList } from '../components/cctv-news/CctvNewsList';
import { CctvNewsTranscriptDrawer } from '../components/cctv-news/CctvNewsTranscriptDrawer';
import { shortDate, weekdayLabel } from '../components/cctv-news/format';
import { useCctvAnalysisStore } from '../stores/cctvAnalysisStore';
import { cctvNewsApi } from '../api/cctvNews';
import type {
  CctvAnalysisRange,
  CctvNewsAnalysisResponse,
  CctvNewsDaySummary,
  CctvNewsItem,
} from '../api/cctvNews';
import { getParsedApiError } from '../api/error';
import type { ParsedApiError } from '../api/error';
import { cn } from '../utils/cn';

const LIST_WINDOW_DAYS = 30;
const SYNC_WINDOW_DAYS = 30;
const SEARCH_PAGE_SIZE = 20;

const ANALYSIS_RANGES: Array<{ value: CctvAnalysisRange; label: string }> = [
  { value: 1, label: '昨天' },
  { value: 3, label: '最近 3 天' },
  { value: 7, label: '最近一周' },
  { value: 10, label: '最近 10 天' },
  { value: 20, label: '最近 20 天' },
];

type TabKey = 'news' | 'analysis';

const TABS: Array<{ key: TabKey; label: string }> = [
  { key: 'news', label: '新闻浏览' },
  { key: 'analysis', label: 'AI 新闻分析' },
];

const CctvNewsPage: React.FC = () => {
  const location = useLocation();
  useEffect(() => {
    document.title = 'CCTV 新闻 - DSA';
  }, []);

  const [activeTab, setActiveTab] = useState<TabKey>('news');

  useEffect(() => {
    const requestedTab = new URLSearchParams(location.search).get('tab');
    if (requestedTab === 'news' || requestedTab === 'analysis') {
      setActiveTab(requestedTab);
    }
  }, [location.search]);

  const [days, setDays] = useState<CctvNewsDaySummary[]>([]);
  const [daysLoading, setDaysLoading] = useState(true);
  const [daysError, setDaysError] = useState<ParsedApiError | null>(null);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);

  const [items, setItems] = useState<CctvNewsItem[]>([]);
  const [itemsTotal, setItemsTotal] = useState(0);
  const [itemsPage, setItemsPage] = useState(1);
  const [itemsLoading, setItemsLoading] = useState(false);
  const [itemsError, setItemsError] = useState<ParsedApiError | null>(null);

  const [searchDraft, setSearchDraft] = useState('');
  const [activeSearch, setActiveSearch] = useState('');

  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);
  const [syncError, setSyncError] = useState<ParsedApiError | null>(null);

  const [transcript, setTranscript] = useState<CctvNewsItem | null>(null);
  const [transcriptOpen, setTranscriptOpen] = useState(false);
  const [transcriptLoading, setTranscriptLoading] = useState(false);
  const [transcriptError, setTranscriptError] = useState<ParsedApiError | null>(null);

  const [rangeDays, setRangeDays] = useState<CctvAnalysisRange>(7);

  // 分析任务与结果放在跨路由 store 里：切走再回来进度不丢，完成后全局通知。
  const analysisJob = useCctvAnalysisStore((state) => state.job);
  const analysisResult = useCctvAnalysisStore((state) => state.result);
  const analysisSubmitError = useCctvAnalysisStore((state) => state.submitError);
  const submitAnalysis = useCctvAnalysisStore((state) => state.submit);

  const daysRequestIdRef = useRef(0);
  const itemsRequestIdRef = useRef(0);
  const transcriptRequestIdRef = useRef(0);

  const loadDays = useCallback(async (preferredDate?: string | null) => {
    const requestId = daysRequestIdRef.current + 1;
    daysRequestIdRef.current = requestId;
    const isLatestRequest = () => daysRequestIdRef.current === requestId;

    setDaysLoading(true);
    try {
      const response = await cctvNewsApi.listDays(LIST_WINDOW_DAYS);
      // A superseded request may still provide a valid freshness snapshot for
      // the route-entry check; only its state writes are discarded.
      if (!isLatestRequest()) return response;
      setDays(response.days);
      setDaysError(null);
      setSelectedDate((current) => {
        const wanted = preferredDate ?? current;
        if (wanted && response.days.some((day) => day.newsDate === wanted)) {
          return wanted;
        }
        return response.days[0]?.newsDate ?? null;
      });
      return response;
    } catch (error) {
      if (!isLatestRequest()) return null;
      setDaysError(getParsedApiError(error));
      return null;
    } finally {
      if (isLatestRequest()) setDaysLoading(false);
    }
  }, []);

  const autoSyncInFlightRef = useRef<Promise<void> | null>(null);
  const autoSyncStartedRef = useRef(false);
  useEffect(() => {
    if (autoSyncStartedRef.current) return;
    autoSyncStartedRef.current = true;
    // 注意：这里不能用 cleanup 里置位的 cancelled 标志来拦 setState。
    // StrictMode 下 effect 会“执行→清理→重放”，重放会命中上面的 ref 守卫直接
    // return，而首次执行的 cancelled 已被清理函数置真——若据此跳过
    // setSyncing(false)，按钮会永远停在“同步中”。真正卸载后 setState 只是
    // no-op，直接更新即可；loadDays 自带请求序号守卫，不怕过期写入。
    const checkLatestNews = async () => {
      const initialLoad = loadDays();
      // The sync endpoint performs the freshness check server-side and skips
      // complete/recently attempted dates. Start it immediately on route entry
      // instead of waiting for the directory response, which can be cancelled
      // when React replays an effect or the user changes routes quickly.
      if (autoSyncInFlightRef.current) {
        await autoSyncInFlightRef.current;
        await loadDays();
        return;
      }

      setSyncing(true);
      setSyncError(null);
      const syncPromise = cctvNewsApi.sync({ days: 2 }).then(() => undefined);
      autoSyncInFlightRef.current = syncPromise;
      try {
        // The backend skips complete/recently attempted days, so this remains
        // cheap on repeat visits while filling a missing current day promptly.
        await syncPromise;
        // Do not keep the toolbar blocked while the follow-up directory refresh
        // waits on a slow or stale response.
        setSyncing(false);
        const initialResponse = await initialLoad;
        if (initialResponse) await loadDays();
      } catch (error) {
        setSyncError(getParsedApiError(error));
      } finally {
        if (autoSyncInFlightRef.current === syncPromise) autoSyncInFlightRef.current = null;
        setSyncing(false);
      }
    };
    void checkLatestNews();
  }, [location.pathname, loadDays]);

  const loadItems = useCallback(
    async (pageOverride?: number) => {
      const requestId = itemsRequestIdRef.current + 1;
      itemsRequestIdRef.current = requestId;
      const isLatestRequest = () => itemsRequestIdRef.current === requestId;

      // 搜索态跨整个窗口分页；否则只看选中的那一天，一天十几条不需要分页。
      const searching = activeSearch.trim().length > 0;
      if (!searching && !selectedDate) {
        setItems([]);
        setItemsTotal(0);
        return;
      }

      setItemsLoading(true);
      try {
        const response = await cctvNewsApi.listItems(
          searching
            ? {
              days: LIST_WINDOW_DAYS,
              query: activeSearch.trim(),
              page: pageOverride ?? itemsPage,
              pageSize: SEARCH_PAGE_SIZE,
            }
            : { newsDate: selectedDate ?? undefined, pageSize: 100 },
        );
        if (!isLatestRequest()) return;
        setItems(response.items);
        setItemsTotal(response.total);
        setItemsError(null);
        if (pageOverride !== undefined) setItemsPage(pageOverride);
      } catch (error) {
        if (!isLatestRequest()) return;
        setItemsError(getParsedApiError(error));
      } finally {
        if (isLatestRequest()) setItemsLoading(false);
      }
    },
    [activeSearch, itemsPage, selectedDate],
  );

  useEffect(() => {
    void loadItems();
  }, [loadItems]);

  const handleSync = useCallback(async () => {
    setSyncing(true);
    setSyncError(null);
    setSyncMessage(null);
    try {
      const response = await cctvNewsApi.sync({ days: SYNC_WINDOW_DAYS });
      const incomplete = response.days.filter((day) => !day.isComplete).length;
      setSyncMessage(
        `已检查 ${response.requestedDays} 天，实际抓取 ${response.syncedDays} 天，`
        + `新增 ${response.created} 条、更新 ${response.updated} 条`
        + (incomplete > 0 ? `，其中 ${incomplete} 天暂无完整文字稿（当天 20:00 后才会更新）` : ''),
      );
      await loadDays();
    } catch (error) {
      setSyncError(getParsedApiError(error));
    } finally {
      setSyncing(false);
    }
  }, [loadDays]);

  const openTranscript = useCallback(async (itemOrId: CctvNewsItem | number) => {
    const requestId = transcriptRequestIdRef.current + 1;
    transcriptRequestIdRef.current = requestId;
    const isLatestRequest = () => transcriptRequestIdRef.current === requestId;

    const itemId = typeof itemOrId === 'number' ? itemOrId : itemOrId.id;
    setTranscriptOpen(true);
    setTranscriptError(null);
    setTranscript(typeof itemOrId === 'number' ? null : itemOrId);
    setTranscriptLoading(true);
    try {
      const detail = await cctvNewsApi.getItem(itemId);
      if (!isLatestRequest()) return;
      setTranscript(detail);
    } catch (error) {
      if (!isLatestRequest()) return;
      setTranscriptError(getParsedApiError(error));
    } finally {
      if (isLatestRequest()) setTranscriptLoading(false);
    }
  }, []);

  /** 发起后台分析。传 newsDate 表示只分析这一天。 */
  const runAnalysis = useCallback(
    (options: { force?: boolean; newsDate?: string } = {}) => {
      void submitAnalysis({
        rangeDays: options.newsDate ? undefined : rangeDays,
        newsDate: options.newsDate,
        force: options.force,
      });
    },
    [rangeDays, submitAnalysis],
  );

  /** 从新闻浏览页直接分析某一天，并切到分析 tab 看进度。 */
  const analyzeSingleDay = useCallback(
    (newsDate: string) => {
      setActiveTab('analysis');
      runAnalysis({ newsDate, force: true });
    },
    [runAnalysis],
  );

  // 首次切到分析 tab 时先看有没有已有结果，避免用户看到空面板还得先点一次按钮。
  const [fallbackResult, setFallbackResult] = useState<CctvNewsAnalysisResponse | null>(null);
  const latestLoadedRef = useRef(false);
  useEffect(() => {
    if (activeTab !== 'analysis' || latestLoadedRef.current) return;
    latestLoadedRef.current = true;
    void (async () => {
      try {
        const response = await cctvNewsApi.latestAnalysis();
        setFallbackResult(response);
      } catch {
        // 没有历史分析时后端返回 404，属正常情况，保持空态即可
      }
    })();
  }, [activeTab]);

  const analysis = analysisResult ?? fallbackResult;
  const analysisRunning = analysisJob?.status === 'queued' || analysisJob?.status === 'running';

  const submitSearch = useCallback(() => {
    setItemsPage(1);
    setActiveSearch(searchDraft);
  }, [searchDraft]);

  const clearSearch = useCallback(() => {
    setSearchDraft('');
    setActiveSearch('');
    setItemsPage(1);
  }, []);

  const searching = activeSearch.trim().length > 0;
  const totalPages = Math.max(1, Math.ceil(itemsTotal / SEARCH_PAGE_SIZE));

  return (
    <AppPage className="space-y-5">
      <PageHeader
        eyebrow="CCTV News"
        title="新闻联播"
        description="按天浏览近一个月的《新闻联播》文字稿，并基于选定时间窗口内的原文做 AI 政策与产业解读。"
        actions={(
          <Button
            variant="secondary"
            size="md"
            onClick={() => void handleSync()}
            isLoading={syncing}
            loadingText="同步中"
          >
            <RefreshCw className="mr-1.5 h-4 w-4" aria-hidden="true" />
            同步新闻
          </Button>
        )}
      />

      {syncError ? <ApiErrorAlert error={syncError} onDismiss={() => setSyncError(null)} /> : null}
      {syncMessage ? (
        <InlineAlert
          variant="success"
          title="同步完成"
          message={syncMessage}
          action={(
            <button type="button" className="home-accent-link text-sm" onClick={() => setSyncMessage(null)}>
              知道了
            </button>
          )}
        />
      ) : null}
      {daysError ? <ApiErrorAlert error={daysError} onAction={() => void loadDays()} actionLabel="重试" /> : null}

      <div className="flex gap-1 border-b border-border/60" role="tablist" aria-label="CCTV 新闻视图">
        {TABS.map((tab) => (
          <button
            key={tab.key}
            type="button"
            role="tab"
            aria-selected={activeTab === tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={cn(
              '-mb-px border-b-2 px-4 py-2 text-sm transition-colors',
              activeTab === tab.key
                ? 'border-cyan text-foreground'
                : 'border-transparent text-secondary-text hover:text-foreground',
            )}
          >
            {tab.key === 'analysis' ? (
              <span className="inline-flex items-center gap-1.5">
                <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
                {tab.label}
              </span>
            ) : (
              tab.label
            )}
          </button>
        ))}
      </div>

      {activeTab === 'news' ? (
        <section className="space-y-4">
          <div className="flex flex-wrap items-end gap-2">
            <div className="w-full max-w-sm">
              <Input
                label="搜索文字稿"
                placeholder="输入关键词，搜索近 30 天全文"
                value={searchDraft}
                onChange={(event) => setSearchDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault();
                    submitSearch();
                  }
                }}
              />
            </div>
            <Button variant="secondary" size="lg" onClick={submitSearch}>
              <Search className="mr-1.5 h-4 w-4" aria-hidden="true" />
              搜索
            </Button>
            {searching ? (
              <Button variant="ghost" size="lg" onClick={clearSearch}>
                返回按天浏览
              </Button>
            ) : null}
          </div>

          {daysLoading ? <Loading label="正在加载新闻日期" /> : null}

          {!daysLoading && days.length === 0 ? (
            <EmptyState
              title="本地还没有新闻联播文字稿"
              description="点击「同步新闻」抓取最近 30 天的文字稿。首次同步需要一到两分钟，之后只补缺失的日期。"
              action={(
                <Button variant="primary" onClick={() => void handleSync()} isLoading={syncing} loadingText="同步中">
                  同步最近 30 天
                </Button>
              )}
            />
          ) : null}

          {!daysLoading && days.length > 0 ? (
            <div className="grid gap-4 lg:grid-cols-[220px_1fr]">
              {searching ? null : (
                <CctvNewsDayRail
                  days={days}
                  selectedDate={selectedDate}
                  onSelect={(newsDate) => {
                    setSelectedDate(newsDate);
                    setItemsPage(1);
                  }}
                />
              )}

              <div className={cn('space-y-3', searching ? 'lg:col-span-2' : '')}>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h2 className="text-sm font-semibold text-foreground">
                    {searching
                      ? `搜索「${activeSearch}」共 ${itemsTotal} 条`
                      : `${selectedDate ?? '--'} ${weekdayLabel(selectedDate)} · ${itemsTotal} 条`}
                  </h2>
                  {!searching && selectedDate ? (
                    <div className="flex items-center gap-3">
                      <span className="text-xxs text-muted-text">{shortDate(selectedDate)} 播出</span>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => analyzeSingleDay(selectedDate)}
                        disabled={analysisRunning || itemsTotal === 0}
                      >
                        <Sparkles className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
                        分析这天
                      </Button>
                    </div>
                  ) : null}
                </div>

                {itemsError ? (
                  <ApiErrorAlert error={itemsError} onAction={() => void loadItems()} actionLabel="重试" />
                ) : null}

                <CctvNewsList
                  items={items}
                  loading={itemsLoading}
                  onOpenTranscript={(item) => void openTranscript(item)}
                  emptyDescription={
                    searching ? '换个关键词试试，或点「同步新闻」补齐更早的日期。' : undefined
                  }
                />

                {searching && totalPages > 1 ? (
                  <Pagination
                    currentPage={itemsPage}
                    totalPages={totalPages}
                    onPageChange={(page) => void loadItems(page)}
                    className="mt-4"
                  />
                ) : null}
              </div>
            </div>
          ) : null}
        </section>
      ) : null}

      {activeTab === 'analysis' ? (
        <section className="space-y-4">
          <Card variant="bordered" padding="md">
            <div className="flex flex-col gap-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm text-secondary-text">分析范围</span>
                <div className="flex gap-1 rounded-xl border border-border/60 bg-elevated/50 p-1" role="group" aria-label="分析时间范围">
                  {ANALYSIS_RANGES.map((option) => (
                    <button
                      key={option.value}
                      type="button"
                      aria-pressed={rangeDays === option.value}
                      onClick={() => setRangeDays(option.value)}
                      className={cn(
                        'rounded-lg px-3 py-1.5 text-sm transition-colors',
                        rangeDays === option.value
                          ? 'bg-cyan/15 text-cyan'
                          : 'text-secondary-text hover:text-foreground',
                      )}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <Button
                  variant="primary"
                  onClick={() => runAnalysis()}
                  isLoading={analysisRunning}
                  loadingText="分析中"
                >
                  <Sparkles className="mr-1.5 h-4 w-4" aria-hidden="true" />
                  开始分析
                </Button>
                {analysis ? (
                  <Button
                    variant="ghost"
                    onClick={() => runAnalysis({ force: true })}
                    disabled={analysisRunning}
                  >
                    重新分析
                  </Button>
                ) : null}
                <span className="text-xxs leading-relaxed text-muted-text">
                  分析只使用所选范围内的文字稿原文，行业结论都会附带可点开的原文依据。
                </span>
              </div>
            </div>
          </Card>

          {analysisSubmitError ? (
            <ApiErrorAlert
              error={analysisSubmitError}
              actionLabel="重试"
              onAction={() => runAnalysis()}
            />
          ) : null}

          {analysisJob && analysisRunning ? <CctvAnalysisProgress job={analysisJob} /> : null}

          {analysisJob?.status === 'failed' ? (
            <InlineAlert
              variant="danger"
              title="分析失败"
              message={analysisJob.error || analysisJob.message || '分析未能完成，请重试。'}
              action={(
                <button type="button" className="home-accent-link text-sm" onClick={() => runAnalysis({ force: true })}>
                  重试
                </button>
              )}
            />
          ) : null}

          {!analysisRunning && !analysis && !analysisSubmitError && analysisJob?.status !== 'failed' ? (
            <EmptyState
              title="还没有分析结果"
              description="选择时间范围后点击「开始分析」，也可以在「新闻浏览」里对某一天单独分析。分析在后台进行，可以切换页面，完成后会通知你。"
            />
          ) : null}

          {analysis ? (
            <CctvNewsAnalysisPanel
              result={analysis}
              onOpenEvidence={(itemId) => void openTranscript(itemId)}
            />
          ) : null}
        </section>
      ) : null}

      <CctvNewsTranscriptDrawer
        isOpen={transcriptOpen}
        onClose={() => setTranscriptOpen(false)}
        item={transcript}
        loading={transcriptLoading}
        error={transcriptError}
      />
    </AppPage>
  );
};

export default CctvNewsPage;
