import type React from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Crown, RefreshCw, Send } from 'lucide-react';
import { DABAN_NOT_GENERATED, dabanApi } from '../api/daban';
import { getParsedApiError, type ParsedApiError } from '../api/error';
import { ApiErrorAlert, AppPage, Button, EmptyState, InlineAlert, PageHeader } from '../components/common';
import {
  BoardLadder,
  CycleDashboard,
  ExcludedList,
  JobProgress,
  ReviewPanel,
  StyleLanes,
  StyleVerdictPanel,
  ThemeCards,
} from '../components/daban';
import { useUiLanguage } from '../contexts/UiLanguageContext';
import type { DabanHistoryItem, DabanJob, DabanOverview } from '../types/daban';

const POLL_INTERVAL_MS = 2000;

type ActionMessage = { tone: 'info' | 'success' | 'danger'; text: string };

/**
 * 「我是打板王」：按 92科比、陈小群的理念做盘后复盘。
 *
 * 页面顺序就是两人的复盘顺序：先定周期（陈小群「周期定位」），再过涨停梯队（科比 #38），
 * 再看主线（「主线识别」），最后才是打法裁决与两条推荐线。生成走后台任务（30~150 秒），
 * 写法与行业资金页一致。
 */
const DabanKingPage: React.FC = () => {
  const { t } = useUiLanguage();

  const [overview, setOverview] = useState<DabanOverview | null>(null);
  const [history, setHistory] = useState<DabanHistoryItem[]>([]);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [notGenerated, setNotGenerated] = useState(false);
  const [loadError, setLoadError] = useState<ParsedApiError | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const [job, setJob] = useState<DabanJob | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isPushing, setIsPushing] = useState(false);
  const [actionMessage, setActionMessage] = useState<ActionMessage | null>(null);

  const pollTimer = useRef<number | null>(null);

  const loadOverview = useCallback(async (tradeDate?: string | null) => {
    try {
      const payload = await dabanApi.getOverview(tradeDate ?? undefined);
      setOverview(payload);
      setNotGenerated(false);
      setLoadError(null);
    } catch (error) {
      if ((error as Error & { code?: string })?.code === DABAN_NOT_GENERATED) {
        setOverview(null);
        setNotGenerated(true);
        setLoadError(null);
      } else {
        setLoadError(getParsedApiError(error));
      }
    } finally {
      setIsLoading(false);
    }
    try {
      setHistory(await dabanApi.getHistory(10));
    } catch {
      // 条带只是辅助信息，取不到不影响主体。
    }
  }, []);

  const stopPolling = useCallback(() => {
    if (pollTimer.current !== null) {
      window.clearTimeout(pollTimer.current);
      pollTimer.current = null;
    }
  }, []);

  const pollJob = useCallback(
    (jobId: string) => {
      stopPolling();
      const tick = async () => {
        try {
          const current = await dabanApi.getJob(jobId);
          setJob(current);
          if (current.status === 'succeeded' || current.status === 'failed') {
            stopPolling();
            if (current.status === 'succeeded') {
              setActionMessage({ tone: 'success', text: t('dabanKing.generateDone') });
              setSelectedDate(current.result?.tradeDate ?? current.tradeDate ?? null);
              await loadOverview(current.result?.tradeDate ?? current.tradeDate);
            }
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

  // 首次加载：读最近一次复盘，并接上「生成到一半关掉页面」的任务。
  useEffect(() => {
    let active = true;
    void (async () => {
      await loadOverview(null);
      if (!active) return;
      try {
        const activeJob = await dabanApi.getActiveJob();
        if (active && activeJob) {
          setJob(activeJob);
          pollJob(activeJob.jobId);
        }
      } catch {
        // 没有进行中的任务是常态。
      }
    })();
    return () => {
      active = false;
      stopPolling();
    };
  }, [loadOverview, pollJob, stopPolling]);

  const handleGenerate = async (tradeDate?: string | null) => {
    setIsSubmitting(true);
    setActionMessage(null);
    try {
      const result = await dabanApi.submitJob(tradeDate ?? undefined);
      if (result.coolingDown) {
        setActionMessage({ tone: 'info', text: result.message });
        return;
      }
      if (result.job) {
        setJob(result.job);
        if (result.reused) setActionMessage({ tone: 'info', text: result.message });
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
      const result = await dabanApi.push();
      setActionMessage({ tone: result.ok ? 'success' : result.skipped ? 'info' : 'danger', text: result.message });
    } catch (error) {
      setLoadError(getParsedApiError(error));
    } finally {
      setIsPushing(false);
    }
  };

  const handleSelectDate = (tradeDate: string) => {
    setSelectedDate(tradeDate);
    setActionMessage(null);
    void loadOverview(tradeDate);
  };

  const isRunning = job !== null && (job.status === 'queued' || job.status === 'running');
  const generateButton = (size: 'sm' | 'md', tradeDate?: string | null) => (
    <Button
      variant="primary"
      size={size}
      onClick={() => void handleGenerate(tradeDate)}
      isLoading={isSubmitting || isRunning}
      disabled={isSubmitting || isRunning}
      data-testid="daban-generate"
    >
      <RefreshCw className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
      {isRunning ? t('dabanKing.generating') : tradeDate ? t('dabanKing.generateDate', { date: tradeDate }) : t('dabanKing.generate')}
    </Button>
  );

  return (
    <AppPage>
      <PageHeader
        eyebrow="A-Share · 短线"
        title={t('dabanKing.title')}
        description={t('dabanKing.description')}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="secondary" size="sm" onClick={handlePush} isLoading={isPushing} disabled={!overview || isPushing}>
              <Send className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
              {t('dabanKing.push')}
            </Button>
            {generateButton('sm')}
          </div>
        }
      />

      {loadError ? <ApiErrorAlert error={loadError} className="mb-4" /> : null}
      {actionMessage ? <InlineAlert variant={actionMessage.tone} className="mb-4" message={actionMessage.text} /> : null}
      {job && (isRunning || job.status === 'failed') ? (
        <div className="mb-4">
          <JobProgress job={job} t={t} />
        </div>
      ) : null}

      {isLoading ? (
        <div className="py-16 text-center text-sm text-secondary-text">{t('common.loading')}</div>
      ) : notGenerated || !overview ? (
        <EmptyState
          icon={<Crown className="h-6 w-6" aria-hidden="true" />}
          title={selectedDate ? t('dabanKing.emptyDateTitle', { date: selectedDate }) : t('dabanKing.emptyTitle')}
          description={t('dabanKing.emptyDescription')}
          action={generateButton('md', selectedDate)}
        />
      ) : (
        <div className="space-y-5">
          {overview.intraday ? <InlineAlert variant="warning" message={t('dabanKing.intradayHint')} /> : null}
          {overview.warnings?.length ? (
            <InlineAlert
              variant="info"
              title={t('dabanKing.dataWarnings')}
              message={
                <ul className="list-disc space-y-1 pl-4">
                  {overview.warnings.map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              }
            />
          ) : null}

          <CycleDashboard
            overview={overview}
            history={history}
            selectedDate={selectedDate}
            onSelectDate={handleSelectDate}
            t={t}
          />
          <BoardLadder ladder={overview.ladder} t={t} />
          <ThemeCards themes={overview.themes} t={t} />
          <StyleVerdictPanel
            verdict={overview.verdict}
            principles={overview.principles}
            styleLabels={overview.styleLabels}
            trackRecord={overview.trackRecord}
            t={t}
          />
          <StyleLanes
            lanes={overview.lanes}
            preferred={overview.verdict.preferred}
            principles={overview.principles}
            t={t}
          />
          <ReviewPanel review={overview.review} styleLabels={overview.styleLabels} t={t} />
          <ExcludedList excluded={overview.excluded} principles={overview.principles} t={t} />

          {overview.conclusion?.risks?.length ? (
            <InlineAlert
              variant="warning"
              title={t('dabanKing.risks')}
              message={
                <ul className="list-disc space-y-1 pl-4">
                  {overview.conclusion.risks.map((risk) => (
                    <li key={risk}>{risk}</li>
                  ))}
                </ul>
              }
            />
          ) : null}

          <div className="space-y-1 rounded-xl border border-border/40 bg-elevated/30 p-3 text-[11px] leading-relaxed text-secondary-text">
            {overview.disclaimers?.investment ? <p>{overview.disclaimers.investment}</p> : null}
            {overview.disclaimers?.data ? <p>{overview.disclaimers.data}</p> : null}
            <p>
              {t('dabanKing.footerMeta', {
                date: overview.tradeDate || '—',
                duration: ((overview.durationMs ?? 0) / 1000).toFixed(1),
                by: overview.conclusion?.generatedBy === 'llm' ? overview.conclusion.model || 'LLM' : t('dabanKing.ruleFallback'),
              })}
            </p>
          </div>
        </div>
      )}
    </AppPage>
  );
};

export default DabanKingPage;
