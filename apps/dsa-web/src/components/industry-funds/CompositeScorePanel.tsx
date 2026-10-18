import type React from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { useState } from 'react';
import type { UiTextKey } from '../../i18n/uiText';
import type { MarketFactor, MarketScore, SourceEntry } from '../../types/industryFunds';
import { cn } from '../../utils/cn';
import { Badge } from '../common/Badge';
import { InlineAlert } from '../common/InlineAlert';
import { SectionCard } from '../common/SectionCard';
import { Tooltip } from '../common/Tooltip';
import {
  formatIstTime,
  formatPct,
  formatPercentile,
  formatScore,
  formatWeight,
  verdictBorder,
  verdictTone,
} from './format';

type Translate = (key: UiTextKey) => string;

type Props = {
  market: MarketScore;
  sources: Record<string, SourceEntry>;
  cached: boolean;
  tradeDate: string;
  generatedAt: string;
  t: Translate;
};

/**
 * 复合分 + 五因子明细。
 *
 * 表格是这个页面「完全可解释」的落点：每一行都能看到原始值、归一化依据、得分、权重、
 * 数据源与数据日期，用户不需要相信一个黑箱标量。
 */
export const CompositeScorePanel: React.FC<Props> = ({
  market,
  sources,
  cached,
  tradeDate,
  generatedAt,
  t,
}) => {
  const [expanded, setExpanded] = useState<string | null>(null);
  const score = market.score;

  return (
    <SectionCard
      title={t('industryFunds.compositeScore')}
      subtitle={t('industryFunds.sectionSignal')}
      actions={
        <div className="flex flex-wrap items-center justify-end gap-2">
          <Badge variant={cached ? 'default' : 'info'} size="sm">
            {tradeDate || '—'}
          </Badge>
          {cached ? (
            <Tooltip content={`${t('industryFunds.generatedAt')}: ${formatIstTime(generatedAt)}`}>
              <Badge variant="default" size="sm">
                {t('industryFunds.cached')}
              </Badge>
            </Tooltip>
          ) : null}
        </div>
      }
    >
      <div className={cn('mb-5 rounded-2xl border p-4', verdictBorder(market.verdict))}>
        <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
          <div>
            <span className="label-uppercase text-secondary-text">
              {t('industryFunds.compositeScore')}
            </span>
            <p className={cn('mt-1 text-4xl font-bold leading-none', verdictTone(market.verdict))}>
              {score === null ? '—' : formatScore(score, 1)}
              <span className="ml-1 text-base font-normal text-secondary-text">/ 100</span>
            </p>
          </div>
          <div>
            <span className="label-uppercase text-secondary-text">
              {t('industryFunds.verdict')}
            </span>
            <p className={cn('mt-1 text-xl font-semibold', verdictTone(market.verdict))}>
              {market.verdictLabel}
            </p>
          </div>
          <div className="min-w-[12rem] flex-1 text-xs text-secondary-text">
            <p>
              {t('industryFunds.bullishAt')} ≥ {market.thresholds?.bullish ?? '—'}
              {t('industryFunds.bearishAt')} &lt; {market.thresholds?.bearish ?? '—'}
            </p>
            <p className="mt-1">
              {t('industryFunds.availableWeight')}:{' '}
              <span className={cn(market.renormalized ? 'text-danger' : 'text-foreground')}>
                {formatWeight(market.availableWeight)}
              </span>
              {market.renormalized ? `\u3000(${t('industryFunds.renormalized')})` : ''}
            </p>
          </div>
        </div>
      </div>

      {market.warnings.length > 0 ? (
        <InlineAlert
          variant="warning"
          className="mb-4"
          title={t('industryFunds.dataWarnings')}
          message={
            <ul className="list-disc space-y-1 pl-4">
              {market.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          }
        />
      ) : null}

      <div className="overflow-x-auto">
        <table className="w-full min-w-[62rem] border-collapse text-sm">
          <thead>
            <tr className="border-b border-border/60 text-left text-xs text-secondary-text">
              <th className="py-2 pr-3 font-medium">{t('industryFunds.colFactor')}</th>
              <th className="py-2 pr-3 font-medium">{t('industryFunds.colWeight')}</th>
              <th className="py-2 pr-3 font-medium">{t('industryFunds.colScore')}</th>
              <th className="py-2 pr-3 font-medium">{t('industryFunds.colRawValue')}</th>
              <th className="py-2 pr-3 font-medium">{t('industryFunds.colBasis')}</th>
              <th className="py-2 pr-3 font-medium">{t('industryFunds.colSource')}</th>
              <th className="py-2 pr-3 font-medium">{t('industryFunds.colAsOf')}</th>
              <th className="w-8 py-2" />
            </tr>
          </thead>
          <tbody>
            {market.factors.map((factor) => (
              <FactorRow
                key={factor.key}
                factor={factor}
                source={sources[factor.key] ?? sources[camelKey(factor.key)] ?? null}
                expanded={expanded === factor.key}
                onToggle={() => setExpanded(expanded === factor.key ? null : factor.key)}
                t={t}
              />
            ))}
          </tbody>
        </table>
      </div>
    </SectionCard>
  );
};

/** 后端的 `sources` 键在 camelCase 转换后可能变形，两种形式都试一下。 */
function camelKey(key: string): string {
  return key.replace(/_([a-z])/g, (_, c: string) => c.toUpperCase());
}

type FactorRowProps = {
  factor: MarketFactor;
  source: SourceEntry | null;
  expanded: boolean;
  onToggle: () => void;
  t: Translate;
};

const FactorRow: React.FC<FactorRowProps> = ({ factor, source, expanded, onToggle, t }) => {
  const scoreRatio =
    factor.score === null ? 0 : Math.max(0, Math.min(1, factor.score / factor.maxScore));
  const isNeutral = factor.score !== null && Math.abs(factor.score - factor.maxScore / 2) < 0.5;

  return (
    <>
      <tr className={cn('border-b border-border/30', !factor.available && 'opacity-60')}>
        <td className="py-3 pr-3 align-top">
          <div className="font-medium text-foreground">{factor.label}</div>
          <div className="mt-0.5 text-xs text-secondary-text">{factor.direction}</div>
        </td>
        <td className="py-3 pr-3 align-top text-secondary-text">
          {formatWeight(factor.weight)}
          {factor.available && Math.abs(factor.effectiveWeight - factor.weight) > 0.001 ? (
            <div className="text-xs">→ {formatWeight(factor.effectiveWeight)}</div>
          ) : null}
        </td>
        <td className="py-3 pr-3 align-top">
          {factor.available ? (
            <div className="flex items-center gap-2">
              <span
                className={cn(
                  'w-12 font-semibold tabular-nums',
                  isNeutral ? 'text-secondary-text' : 'text-foreground'
                )}
              >
                {formatScore(factor.score, 1)}
                <span className="text-xs font-normal text-secondary-text">/{factor.maxScore}</span>
              </span>
              <span className="h-1.5 w-16 overflow-hidden rounded-full bg-elevated">
                <span
                  className="block h-full rounded-full bg-cyan"
                  style={{ width: `${scoreRatio * 100}%` }}
                />
              </span>
            </div>
          ) : (
            <span className="text-secondary-text">{t('industryFunds.unavailable')}</span>
          )}
        </td>
        <td className="py-3 pr-3 align-top text-foreground">{factor.rawDisplay}</td>
        <td className="max-w-[18rem] py-3 pr-3 align-top text-xs text-secondary-text">
          {factor.basis}
        </td>
        <td className="max-w-[14rem] py-3 pr-3 align-top text-xs text-secondary-text">
          {factor.source || '—'}
        </td>
        <td className="whitespace-nowrap py-3 pr-3 align-top text-xs text-secondary-text">
          {factor.asOf || '—'}
        </td>
        <td className="py-3 align-top">
          <button
            type="button"
            onClick={onToggle}
            aria-expanded={expanded}
            aria-label={t('industryFunds.sourceDetails')}
            className="rounded p-0.5 text-secondary-text transition-colors hover:text-foreground"
          >
            {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          </button>
        </td>
      </tr>

      {factor.error ? (
        <tr className="border-b border-border/30">
          <td colSpan={8} className="pb-2 pr-3">
            <p className="text-xs text-danger">{factor.error}</p>
          </td>
        </tr>
      ) : null}

      {expanded ? (
        <tr className="border-b border-border/30 bg-elevated/40">
          <td colSpan={8} className="px-3 py-3">
            <FactorDetail factor={factor} source={source} t={t} />
          </td>
        </tr>
      ) : null}
    </>
  );
};

const FactorDetail: React.FC<{
  factor: MarketFactor;
  source: SourceEntry | null;
  t: Translate;
}> = ({ factor, source, t }) => {
  const detail = factor.detail ?? {};
  const rows: [string, string][] = [];

  const numeric: [string, UiTextKey, (v: number) => string][] = [
    ['netSumYi', 'industryFunds.detailNetSum', (v) => `${v} 亿元`],
    ['netMedianYi', 'industryFunds.detailNetMedian', (v) => `${v} 亿元`],
    ['latestNetYi', 'industryFunds.detailLatestNet', (v) => `${v} 亿元`],
    ['rolling5dNetYi', 'industryFunds.detailRolling5d', (v) => `${v} 亿元`],
    ['combinedDeltaYi', 'industryFunds.detailEtfDelta', (v) => `${v} 亿份`],
    ['sseDeltaYi', 'industryFunds.detailSseDelta', (v) => `${v} 亿份`],
    ['szseDeltaYi', 'industryFunds.detailSzseDelta', (v) => `${v} 亿份`],
    ['financingBalanceYi', 'industryFunds.detailMarginBalance', (v) => `${v} 亿元`],
    ['change5D', 'industryFunds.detailMarginChange', (v) => formatPct(v * 100, 2, true)],
    ['change5dRaw', 'industryFunds.detailMarginChange', (v) => formatPct(v * 100, 2, true)],
    ['hs300Pe', 'industryFunds.detailHs300Pe', (v) => v.toFixed(2)],
    ['allAPb', 'industryFunds.detailAllAPb', (v) => v.toFixed(2)],
  ];

  for (const [key, labelKey, render] of numeric) {
    const value = detail[key];
    if (typeof value === 'number' && Number.isFinite(value)) {
      rows.push([t(labelKey), render(value)]);
    }
  }

  const percentProps: [string, UiTextKey][] = [
    ['hs300PePercentile', 'industryFunds.detailHs300PePct'],
    ['allAPbPercentile', 'industryFunds.detailAllAPbPct'],
    ['combinedPercentile', 'industryFunds.detailCombinedPct'],
  ];
  for (const [key, labelKey] of percentProps) {
    const value = detail[key];
    if (typeof value === 'number' && Number.isFinite(value)) {
      rows.push([t(labelKey), formatPercentile(value, 1)]);
    }
  }

  if (typeof detail.coverage === 'string' && detail.coverage) {
    rows.push([t('industryFunds.detailCoverage'), detail.coverage]);
  }
  if (typeof detail.baselineDate === 'string' && detail.baselineDate) {
    rows.push([t('industryFunds.detailBaselineDate'), detail.baselineDate]);
  }
  if (typeof detail.method === 'string' && detail.method) {
    rows.push([
      t('industryFunds.detailMethod'),
      detail.method === 'percentile'
        ? t('industryFunds.methodPercentile')
        : t('industryFunds.methodFallback'),
    ]);
  }
  if (typeof detail.historyDays === 'number') {
    rows.push([t('industryFunds.detailHistoryDays'), String(detail.historyDays)]);
  }
  if (typeof detail.historyPoints === 'number') {
    rows.push([t('industryFunds.detailHistoryPoints'), String(detail.historyPoints)]);
  }
  if (typeof detail.inflowCount === 'number' && typeof detail.industryCount === 'number') {
    rows.push([
      t('industryFunds.detailInflowCount'),
      `${detail.inflowCount} / ${detail.industryCount}`,
    ]);
  }

  const deltaTop = Array.isArray(detail.deltaTop) ? detail.deltaTop : [];
  const stockCount = typeof detail.stockCount === 'number' ? detail.stockCount : null;

  return (
    <div className="space-y-3">
      <div className="grid gap-x-6 gap-y-1.5 sm:grid-cols-2 lg:grid-cols-3">
        {rows.map(([label, value]) => (
          <div key={label} className="flex items-baseline justify-between gap-3 text-xs">
            <span className="text-secondary-text">{label}</span>
            <span className="tabular-nums text-foreground">{value}</span>
          </div>
        ))}
        {stockCount !== null ? (
          <div className="flex items-baseline justify-between gap-3 text-xs">
            <span className="text-secondary-text">{t('industryFunds.detailStockCount')}</span>
            <span className="tabular-nums text-foreground">{stockCount}</span>
          </div>
        ) : null}
      </div>

      {source ? (
        <div className="rounded-xl border border-border/50 bg-card/60 p-3 text-xs">
          <p className="mb-1 text-secondary-text">
            {t('industryFunds.sourceDetails')}:{' '}
            <span className="text-foreground">{source.source}</span>
          </p>
          {source.raw?.note ? (
            <p className="text-secondary-text">{String(source.raw.note)}</p>
          ) : null}
          {source.raw?.window ? (
            <p className="text-secondary-text">
              {t('industryFunds.detailWindow')}: {String(source.raw.window)}
            </p>
          ) : null}
          {Object.keys(source.raw ?? {}).length > 0 ? (
            <details className="mt-2">
              <summary className="cursor-pointer text-secondary-text hover:text-foreground">
                {t('industryFunds.rawJson')}
              </summary>
              <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-elevated/60 p-2 text-[11px] text-secondary-text">
                {JSON.stringify(source.raw, null, 2)}
              </pre>
            </details>
          ) : null}
        </div>
      ) : null}

      {deltaTop.length > 0 ? (
        <div className="text-xs">
          <p className="mb-1 text-secondary-text">{t('industryFunds.detailEtfTop')}</p>
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {deltaTop.map((item, index) => {
              const row = item as Record<string, unknown>;
              const delta = typeof row.delta === 'number' ? row.delta / 1e8 : null;
              return (
                <span key={`${row.code}-${index}`} className="tabular-nums">
                  <span className="text-secondary-text">{String(row.code ?? '')}</span>{' '}
                  <span className="text-foreground">{String(row.name ?? '')}</span>{' '}
                  <span className={delta !== null && delta > 0 ? 'text-success' : 'text-danger'}>
                    {delta === null ? '—' : `${delta > 0 ? '+' : ''}${delta.toFixed(2)} 亿份`}
                  </span>
                </span>
              );
            })}
          </div>
        </div>
      ) : null}
    </div>
  );
};
