import type React from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { RefreshCw, Send } from 'lucide-react';
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
  RefreshProgress,
} from '../components/industry-funds';
import { useUiLanguage } from '../contexts/UiLanguageContext';
import type { IndustryFundsJob, IndustryFundsOverview } from '../types/industryFunds';

const POLL_INTERVAL_MS = 2000;

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

  const pollTimer = useRef<number | null>(null);

  const loadOverview = useCallback(async () => {
    try {
      const payload = await industryFundsApi.getOverview();
      setOverview(payload);
      setNotGenerated(false);
      setLoadError(null);
    } catch (error) {
      const code = (error as Error & { code?: string })?.code;
      if (code === INDUSTRY_FUNDS_NOT_GENERATED) {
        setOverview(null);
        setNotGenerated(true);
        setLoadError(null);
      } else {
        setLoadError(getParsedApiError(error));
      }
    } finally {
      setIsLoading(false);
    }
  }, []);

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
    [loadOverview, stopPolling, t]
  );

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

      {isLoading ? (
        <div className="py-16 text-center text-sm text-secondary-text">{t('common.loading')}</div>
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
          <CompositeScorePanel
            market={overview.market}
            sources={overview.sources ?? {}}
            cached={overview.cached}
            tradeDate={overview.tradeDate}
            generatedAt={overview.generatedAt}
            t={t}
          />

          <IndustryScoreTable result={overview.industries} t={t} />

          {/* (c) 结论 */}
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
      )}
    </AppPage>
  );
};

export default IndustryFundsPage;
