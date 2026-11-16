import type React from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronRight, List, RefreshCw, Send } from 'lucide-react';
import {
  INDUSTRY_FUNDS_NOT_GENERATED,
  industryFundsApi,
} from '../api/industryFunds';
import { getParsedApiError, type ParsedApiError } from '../api/error';
import {
  ApiErrorAlert,
  AppPage,
  Button,
  EmptyState,
  InlineAlert,
  PageHeader,
} from '../components/common';
import {
  CompositeScorePanel,
  ConclusionSection,
  FundFlowSection,
  IndustryScoreTable,
  HotMoneyPanel,
  InstitutionFlowPanel,
  NationalTeamPanel,
  RefreshProgress,
} from '../components/industry-funds';
import { useUiLanguage } from '../contexts/UiLanguageContext';
import type { UiTextKey } from '../i18n/uiText';
import type {
  IndustryFundsFlowDate,
  IndustryFundsFlows,
  IndustryFundsHotMoney,
  IndustryFundsJob,
  IndustryFundsOverview,
} from '../types/industryFunds';
import { cn } from '../utils/cn';

const POLL_INTERVAL_MS = 2000;

/** 右上角固定导航：每个 tab 指向它自己的几块内容（id 由各区块自己挂）。 */
const NAV_ITEMS: Record<'overview' | 'institutions' | 'national-team' | 'hot-money',
  { id: string; label: UiTextKey }[]> = {
  overview: [
    { id: 'if-ov-flow', label: 'industryFunds.flow.nav.flow' },
    { id: 'if-ov-signal', label: 'industryFunds.flow.nav.signal' },
    { id: 'if-ov-score', label: 'industryFunds.flow.nav.score' },
    { id: 'if-ov-conclusion', label: 'industryFunds.flow.nav.conclusion' },
  ],
  institutions: [
    { id: 'if-inst-summary', label: 'industryFunds.flow.nav.summary' },
    { id: 'if-inst-stocks', label: 'industryFunds.flow.nav.detail' },
    { id: 'if-inst-trend', label: 'industryFunds.flow.nav.trend' },
  ],
  'national-team': [
    { id: 'if-nt-summary', label: 'industryFunds.flow.nav.summary' },
    { id: 'if-nt-etfs', label: 'industryFunds.flow.nav.detail' },
    { id: 'if-nt-trend', label: 'industryFunds.flow.nav.trend' },
  ],
  'hot-money': [
    { id: 'if-hm-seats', label: 'industryFunds.flow.nav.seats' },
    { id: 'if-hm-matrix', label: 'industryFunds.flow.nav.matrix' },
    { id: 'if-hm-verdict', label: 'industryFunds.flow.nav.verdict' },
  ],
};

const IndustryFundsPage: React.FC = () => {
  const { t } = useUiLanguage();

  const [overview, setOverview] = useState<IndustryFundsOverview | null>(null);
  const [notGenerated, setNotGenerated] = useState(false);
  const [loadError, setLoadError] = useState<ParsedApiError | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const [job, setJob] = useState<IndustryFundsJob | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [actionMessage, setActionMessage] = useState<{ tone: 'info' | 'success' | 'danger'; text: string } | null>(
    null
  );
  const [isPushing, setIsPushing] = useState(false);

  /** 页面四个 tab：总览（现有行业资金）/ 机构 / 汪汪队 / 游资。 */
  const [tab, setTab] = useState<'overview' | 'institutions' | 'national-team' | 'hot-money'>('overview');
  /** 游资席位矩阵（按需加载：只有切到该 tab 才拉）。 */
  const [hotMoney, setHotMoney] = useState<IndustryFundsHotMoney | null>(null);
  const [hotMoneyError, setHotMoneyError] = useState<ParsedApiError | null>(null);
  /** 日期 tab：最近两周的交易日。日期选择也用 tab 切换，不用日期控件。 */
  const [flowDates, setFlowDates] = useState<IndustryFundsFlowDate[]>([]);
  const [selectedDate, setSelectedDate] = useState('');
  const [flows, setFlows] = useState<IndustryFundsFlows | null>(null);
  const [flowsError, setFlowsError] = useState<ParsedApiError | null>(null);
  /** 指定的历史日期没有总览快照时记下那一天，用于区别于「从没生成过」。 */
  const [missingDate, setMissingDate] = useState('');
  /** 右上角导航是否展开 */
  const [navOpen, setNavOpen] = useState(true);

  const pollTimer = useRef<number | null>(null);

  const loadOverview = useCallback(async (tradeDate?: string) => {
    try {
      const payload = await industryFundsApi.getOverview(tradeDate);
      setOverview(payload);
      setNotGenerated(false);
      setLoadError(null);
      setMissingDate('');
    } catch (error) {
      const code = (error as Error & { code?: string })?.code;
      if (code === INDUSTRY_FUNDS_NOT_GENERATED) {
        setOverview(null);
        setNotGenerated(true);
        setLoadError(null);
        // 指定历史日期但没有那天的快照：不是「从没生成过」，页面要分开提示。
        setMissingDate(tradeDate ?? '');
      } else {
        setLoadError(getParsedApiError(error));
      }
    } finally {
      setIsLoading(false);
    }
  }, []);

  const loadFlows = useCallback(async () => {
    try {
      const dates = await industryFundsApi.getFlowDates(10);
      setFlowDates(dates.days);
      const latestWithOverview = [...dates.days].reverse().find((day) => day.hasOverview);
      setSelectedDate((current) => current || latestWithOverview?.date || dates.days[dates.days.length - 1]?.date || dates.today);
    } catch (error) {
      setFlowsError(getParsedApiError(error));
    }
    try {
      const payload = await industryFundsApi.getFlows(10);
      setFlows(payload);
      setFlowsError(null);
    } catch (error) {
      setFlowsError(getParsedApiError(error));
    }
  }, []);

  useEffect(() => {
    void loadFlows();
  }, [loadFlows]);

  const loadHotMoney = useCallback(async () => {
    try {
      setHotMoney(await industryFundsApi.getHotMoney(10, 10));
      setHotMoneyError(null);
    } catch (error) {
      setHotMoneyError(getParsedApiError(error));
    }
  }, []);

  useEffect(() => {
    if (tab === 'hot-money' && !hotMoney) void loadHotMoney();
  }, [hotMoney, loadHotMoney, tab]);

  const stopPolling = useCallback(() => {
    if (pollTimer.current !== null) {
      window.clearTimeout(pollTimer.current);
      pollTimer.current = null;
    }
  }, []);

  /** 轮询任务直到结束。结束（无论成败）后重新读一次缓存。 */
  const pollJob = useCallback(
    (jobId: string) => {
      stopPolling();
      const tick = async () => {
        try {
          const current = await industryFundsApi.getJob(jobId);
          setJob(current);
          if (current.status === 'succeeded' || current.status === 'failed') {
            stopPolling();
            if (current.status === 'succeeded') {
              setActionMessage({ tone: 'success', text: t('industryFunds.refreshDone') });
            }
            await loadOverview();
            await loadFlows();
            setHotMoney(null);   // 下次切到「游资」时重新拉（榜单可能刚披露）
            return;
          }
        } catch (error) {
          stopPolling();
          setLoadError(getParsedApiError(error));
          return;
        }
        pollTimer.current = window.setTimeout(tick, POLL_INTERVAL_MS);
      };
      pollTimer.current = window.setTimeout(tick, POLL_INTERVAL_MS);
    },
    [loadFlows, loadOverview, stopPolling, t]
  );

  /**
   * 资金流向的日期 tab 与两周序列。
   *
   * 序列首次要 5~10 秒（沪市份额按日回溯 + 龙虎榜 + ETF 日线），所以与总览分开加载：
   * 总览照常秒回，机构 / 汪汪队两个 tab 各自显示自己的加载态。
   */
  // 首次加载：读缓存，并接上「刷新到一半关掉页面」的进行中任务。
  useEffect(() => {
    let active = true;
    void (async () => {
      await loadOverview();
      if (!active) return;
      try {
        const activeJob = await industryFundsApi.getActiveJob();
        if (active && activeJob) {
          setJob(activeJob);
          pollJob(activeJob.jobId);
        }
      } catch {
        // 没有进行中的任务是常态，忽略。
      }
    })();
    return () => {
      active = false;
      stopPolling();
    };
  }, [loadOverview, pollJob, stopPolling]);

  /** 总览按**选中日期**加载：不再特殊处理「选中的正好是最新那天」，否则重复点击会跳回最新。 */
  const handleSelectDate = useCallback(
    async (date: string) => {
      setSelectedDate(date);
      if (tab !== 'overview') return;
      await loadOverview(date || undefined);
    },
    [loadOverview, tab]
  );

  /** 切到总览时，也要按当前选中的日期加载（在机构 / 汪汪队里选的历史日期会带过来）。 */
  const handleSelectTab = useCallback(
    async (key: 'overview' | 'institutions' | 'national-team' | 'hot-money') => {
      setTab(key);
      if (key === 'overview') {
        await loadOverview(selectedDate || undefined);
      }
    },
    [loadOverview, selectedDate]
  );

  /** 当前 tab 默认落在「最近一个有数据的日子」：今天盘中份额 / 龙虎榜都还没出。 */
  const moduleDefaultDate = (values: (number | null)[]): string => {
    const dates = flows?.days ?? [];
    for (let i = dates.length - 1; i >= 0; i -= 1) {
      if (values[i] !== null && values[i] !== undefined) return dates[i];
    }
    return dates[dates.length - 1] ?? selectedDate;
  };

  const institutionDate = selectedDate || moduleDefaultDate((flows?.institutions.series ?? []).map((item) => item.net));
  const nationalDate = selectedDate || moduleDefaultDate((flows?.nationalTeam.series ?? []).map((item) => item.netShares));

  const handleRefresh = async () => {
    setIsSubmitting(true);
    setActionMessage(null);
    try {
      const result = await industryFundsApi.submitJob();
      if (result.coolingDown) {
        setActionMessage({ tone: 'info', text: result.message });
        return;
      }
      if (result.job) {
        setJob(result.job);
        if (result.reused) {
          setActionMessage({ tone: 'info', text: result.message });
        }
        pollJob(result.job.jobId);
      }
    } catch (error) {
      setLoadError(getParsedApiError(error));
    } finally {
      setIsSubmitting(false);
    }
  };

  const handlePush = async () => {
    setIsPushing(true);
    setActionMessage(null);
    try {
      const result = await industryFundsApi.push();
      setActionMessage({
        tone: result.ok ? 'success' : result.skipped ? 'info' : 'danger',
        text: result.message,
      });
    } catch (error) {
      setLoadError(getParsedApiError(error));
    } finally {
      setIsPushing(false);
    }
  };

  const isRunning = job !== null && (job.status === 'queued' || job.status === 'running');

  const flowSource = overview?.sources?.industryFundFlow;
  const industryDisclaimer =
    overview?.disclaimers?.flow ?? t('industryFunds.defaultFlowDisclaimer');

  return (
    <AppPage>
      <PageHeader
        eyebrow="A-Share"
        title={t('industryFunds.title')}
        description={t('industryFunds.description')}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={handlePush}
              isLoading={isPushing}
              disabled={!overview || isPushing}
            >
              <Send className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
              {t('industryFunds.push')}
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={handleRefresh}
              isLoading={isSubmitting || isRunning}
              disabled={isSubmitting || isRunning}
              data-testid="industry-funds-refresh"
            >
              <RefreshCw className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
              {isRunning ? t('industryFunds.refreshing') : t('industryFunds.refresh')}
            </Button>
          </div>
        }
      />

      {loadError ? (
        <ApiErrorAlert error={loadError} className="mb-4" />
      ) : null}

      {actionMessage ? (
        <InlineAlert
          variant={actionMessage.tone}
          className="mb-4"
          message={actionMessage.text}
        />
      ) : null}

      {job ? (
        <div className="mb-4">
          <RefreshProgress job={job} t={t} />
        </div>
      ) : null}

      {/* 右上角固定导航：可收起；宽度随内容（w-max），点击直接定位到当前 tab 的模块 */}
      <nav
        aria-label={t('industryFunds.flow.navLabel')}
        data-testid="flow-nav"
        className="fixed right-3 top-28 z-30 hidden flex-col items-end gap-1.5 xl:flex"
      >
        <button
          type="button"
          data-testid="flow-nav-toggle"
          aria-expanded={navOpen}
          aria-label={navOpen ? t('industryFunds.flow.navCollapse') : t('industryFunds.flow.navExpand')}
          title={navOpen ? t('industryFunds.flow.navCollapse') : t('industryFunds.flow.navExpand')}
          onClick={() => setNavOpen((prev) => !prev)}
          className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-cyan/60 bg-elevated text-cyan shadow-[0_6px_20px_rgba(3,8,20,0.35)] transition-colors hover:bg-cyan/15"
        >
          {navOpen ? <ChevronRight className="h-4 w-4" aria-hidden="true" /> : <List className="h-4 w-4" aria-hidden="true" />}
        </button>
        {navOpen ? (
          <div
            className="flex w-max flex-col gap-0.5 rounded-xl border border-cyan/40 bg-elevated/95 p-1.5 shadow-[0_16px_40px_rgba(3,8,20,0.45)] backdrop-blur"
          >
            <p className="px-2 pb-1 pt-0.5 text-[10px] uppercase tracking-wide text-muted-text">
              {t('industryFunds.flow.navTitle')}
            </p>
            {NAV_ITEMS[tab].map((item) => (
              <button
                key={item.id}
                type="button"
                data-testid={`flow-nav-${item.id}`}
                onClick={() => document.getElementById(item.id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
                className="whitespace-nowrap rounded-lg px-2 py-1 text-left text-[11px] text-foreground transition-colors hover:bg-cyan/15"
              >
                {t(item.label)}
              </button>
            ))}
          </div>
        ) : null}
      </nav>

      {/* 页面 tab：总览（现有行业资金）/ 机构 / 汪汪队 */}
      <div role="tablist" aria-label={t('industryFunds.flow.tabsLabel')} className="mb-3 flex flex-wrap gap-2">
        {(['overview', 'institutions', 'national-team', 'hot-money'] as const).map((key) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            data-testid={`flow-tab-${key}`}
            onClick={() => void handleSelectTab(key)}
            className={cn(
              'rounded-full border px-4 py-1.5 text-sm transition-colors',
              tab === key
                ? 'border-[hsl(var(--primary))] bg-[hsl(var(--primary))]/10 font-medium text-[hsl(var(--primary))]'
                : 'border-border text-secondary-text hover:border-[hsl(var(--primary))]/40 hover:text-foreground'
            )}
          >
            {t(`industryFunds.flow.tab.${key}` as const)}
          </button>
        ))}
      </div>

      {/* 日期 tab：最近两周，日期选择也是 tab 切换 */}
      {flowDates.length ? (
        <div role="tablist" aria-label={t('industryFunds.flow.datesLabel')} className="mb-4 flex flex-wrap gap-1.5">
          {/* 最近的日期放最前面（左边） */}
          {[...flowDates].reverse().map((day) => {
            const active = day.date === selectedDate;
            return (
              <button
                key={day.date}
                type="button"
                role="tab"
                aria-selected={active}
                data-testid={`flow-date-${day.date}`}
                onClick={() => void handleSelectDate(day.date)}
                title={day.hasOverview ? t('industryFunds.flow.dateHasOverview') : t('industryFunds.flow.dateNoOverview')}
                className={cn(
                  'rounded-lg border px-2.5 py-1 text-xs tabular-nums transition-colors',
                  active ? 'border-cyan/60 bg-cyan/10 font-medium text-cyan' : 'border-border/50 text-secondary-text hover:text-foreground',
                  !day.hasOverview && tab === 'overview' ? 'opacity-60' : ''
                )}
              >
                {day.date.slice(5)}
                {day.hasOverview ? <span className="ml-1 text-[9px] text-cyan">●</span> : null}
              </button>
            );
          })}
        </div>
      ) : null}

      {/* 机构 / 汪汪队：与总览分开加载（首次 5~10 秒） */}
      {tab === 'institutions' ? (
        flowsError ? (
          <ApiErrorAlert error={flowsError} className="mb-4" />
        ) : !flows ? (
          <div className="py-16 text-center text-sm text-secondary-text">{t('industryFunds.flow.loading')}</div>
        ) : (
          <InstitutionFlowPanel flow={flows.institutions} date={institutionDate} t={t} />
        )
      ) : null}

      {tab === 'hot-money' ? (
        hotMoneyError ? (
          <ApiErrorAlert error={hotMoneyError} className="mb-4" />
        ) : !hotMoney ? (
          <div className="py-16 text-center text-sm text-secondary-text">{t('industryFunds.flow.loading')}</div>
        ) : (
          <HotMoneyPanel data={hotMoney} date={selectedDate} t={t} />
        )
      ) : null}

      {tab === 'national-team' ? (
        flowsError ? (
          <ApiErrorAlert error={flowsError} className="mb-4" />
        ) : !flows ? (
          <div className="py-16 text-center text-sm text-secondary-text">{t('industryFunds.flow.loading')}</div>
        ) : (
          <NationalTeamPanel flow={flows.nationalTeam} date={nationalDate} t={t} />
        )
      ) : null}

      {tab === 'overview' ? (isLoading ? (
        <div className="py-16 text-center text-sm text-secondary-text">{t('common.loading')}</div>
      ) : missingDate ? (
        <InlineAlert
          variant="info"
          message={t('industryFunds.flow.overviewMissing', { date: missingDate })}
        />
      ) : notGenerated || !overview ? (
        <EmptyState
          title={t('industryFunds.emptyTitle')}
          description={t('industryFunds.emptyDescription')}
          action={
            <Button
              variant="primary"
              size="md"
              onClick={handleRefresh}
              isLoading={isSubmitting || isRunning}
              disabled={isSubmitting || isRunning}
            >
              <RefreshCw className="mr-1.5 h-4 w-4" aria-hidden="true" />
              {isRunning ? t('industryFunds.refreshing') : t('industryFunds.refresh')}
            </Button>
          }
        />
      ) : (
        <div className="space-y-5">
          {overview.cached && overview.stale ? (
            <InlineAlert
              variant="info"
              message={t('industryFunds.staleHint')
                .replace('{age}', String(overview.ageMinutes ?? '—'))
                .replace('{ttl}', String(overview.cacheTtlMinutes ?? '—'))}
            />
          ) : null}

          {overview.degraded && overview.warnings?.length ? (
            <InlineAlert
              variant="warning"
              title={t('industryFunds.dataWarnings')}
              message={
                <ul className="list-disc space-y-1 pl-4">
                  {overview.warnings.map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              }
            />
          ) : null}

          {/* (a) 资金流向 */}
          <div id="if-ov-flow" />
          <FundFlowSection
            industries={overview.industries?.industries ?? []}
            disclaimer={industryDisclaimer}
            sourceNote={
              flowSource
                ? `${t('industryFunds.source')}: ${flowSource.source}${
                    flowSource.asOf ? `\u3000${flowSource.asOf}` : ''
                  }`
                : ''
            }
            t={t}
          />

          {/* (b) 复合信号 */}
          <div id="if-ov-signal" />
          <CompositeScorePanel
            market={overview.market}
            sources={overview.sources ?? {}}
            cached={overview.cached}
            tradeDate={overview.tradeDate}
            generatedAt={overview.generatedAt}
            t={t}
          />

          <div id="if-ov-score" />
          <IndustryScoreTable result={overview.industries} t={t} />

          {/* (c) 结论 */}
          <div id="if-ov-conclusion" />
          <ConclusionSection
            conclusion={overview.conclusion}
            candidates={overview.candidates}
            disclaimers={overview.disclaimers ?? {}}
            t={t}
          />

          <p className="pb-4 text-center text-[11px] text-secondary-text">
            {t('industryFunds.footerMeta')
              .replace('{date}', overview.tradeDate || '—')
              .replace('{duration}', (overview.durationMs / 1000).toFixed(1))}
          </p>
        </div>
      )) : null}
    </AppPage>
  );
};

export default IndustryFundsPage;
