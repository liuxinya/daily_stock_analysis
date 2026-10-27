import type React from 'react';
import { useState } from 'react';
import { ChevronDown } from 'lucide-react';
import { SectionCard, StatCard } from '../common';
import type { UiTextKey } from '../../i18n/uiText';
import type { DabanHistoryItem, DabanOverview } from '../../types/daban';
import { cn } from '../../utils/cn';
import { HitIcon } from './shared';
import {
  changeTone,
  formatDelta,
  formatPct,
  formatRate,
  riskDeltaTone,
  STAGE_DOTS,
  STAGE_TONES,
} from './format';

type Translate = (key: UiTextKey, params?: Record<string, string | number>) => string;

type Props = {
  overview: DabanOverview;
  history: DabanHistoryItem[];
  selectedDate: string | null;
  onSelectDate: (date: string) => void;
  t: Translate;
};

/**
 * ① 情绪周期仪表：阶段大字 + 五大指标 + 六阶段条件核对 + 近 10 日阶段条带。
 *
 * 条件核对表默认收起但必须可展开 —— 阈值来自二手整理的文章，用户要能对着数字判断
 * 「今天被判成这个阶段到底对不对」。
 */
export const CycleDashboard: React.FC<Props> = ({ overview, history, selectedDate, onSelectDate, t }) => {
  const [showConditions, setShowConditions] = useState(false);
  const { metrics: m, stage, kobeStage } = overview;
  const d = m.delta ?? {};
  const strip = [...history].reverse();

  const deltaHint = (value: number | null | undefined, tone: string, digits = 0, suffix = '') =>
    value === null || value === undefined ? null : (
      <span className={tone}>{t('dabanKing.vsYesterday', { delta: `${formatDelta(value, digits)}${suffix}` })}</span>
    );

  return (
    <SectionCard title={t('dabanKing.cycleTitle')} subtitle={t('dabanKing.cycleSubtitle')}>
      <div className="flex flex-col gap-4 lg:flex-row lg:items-center">
        <div
          className={cn(
            'flex min-w-[13rem] flex-col gap-1 rounded-2xl border px-4 py-3',
            STAGE_TONES[stage.key] ?? 'border-border bg-elevated'
          )}
        >
          <span className="text-2xl font-semibold">{stage.label}</span>
          <span className="text-xs text-secondary-text">
            {t('dabanKing.kobeView')}：{kobeStage.label}
          </span>
          <span className="text-xs text-secondary-text">
            {t('dabanKing.hitRate', { hit: formatRate(stage.hitRate) })}
            {stage.transitional && stage.secondaryLabel
              ? ` · ${t('dabanKing.transitional', { stage: stage.secondaryLabel })}`
              : ''}
          </span>
          {stage.override ? <span className="text-[11px] text-foreground">{stage.override}</span> : null}
        </div>
        {overview.conclusion?.summary ? (
          <p className="flex-1 text-sm leading-relaxed text-foreground">{overview.conclusion.summary}</p>
        ) : null}
      </div>

      <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard
          label={t('dabanKing.metric.zt')}
          value={m.ztCount}
          hint={deltaHint(d.ztCount, changeTone(d.ztCount))}
        />
        <StatCard label={t('dabanKing.metric.lianban')} value={m.lianbanCount} hint={deltaHint(d.lianbanCount, changeTone(d.lianbanCount))} />
        <StatCard
          label={t('dabanKing.metric.height')}
          value={`${m.maxHeight} 板`}
          hint={deltaHint(d.maxHeight, changeTone(d.maxHeight))}
        />
        <StatCard
          label={t('dabanKing.metric.promotion')}
          value={formatRate(m.promotionRate)}
          hint={deltaHint(
            d.promotionRate === null || d.promotionRate === undefined ? null : d.promotionRate * 100,
            changeTone(d.promotionRate),
            1,
            ' 个百分点'
          )}
        />
        <StatCard
          label={t('dabanKing.metric.premium')}
          value={<span className={changeTone(m.yztPremium)}>{formatPct(m.yztPremium, 2)}</span>}
          hint={m.yztUpRatio === null ? null : `上涨占比 ${formatRate(m.yztUpRatio)}`}
        />
        <StatCard label={t('dabanKing.metric.dt')} value={m.dtCount} hint={deltaHint(d.dtCount, riskDeltaTone(d.dtCount))} />
        <StatCard
          label={t('dabanKing.metric.bigLoss')}
          value={m.bigLossCount ?? '—'}
          hint={deltaHint(d.bigLossCount, riskDeltaTone(d.bigLossCount))}
        />
        <StatCard label={t('dabanKing.metric.zab')} value={formatRate(m.zabRate)} hint={`炸板 ${m.zbCount} 只`} />
      </div>

      {strip.length ? (
        <div className="mt-4">
          <p className="mb-2 text-xs text-secondary-text">{t('dabanKing.strip')}</p>
          <div className="flex flex-wrap gap-1.5" role="list">
            {strip.map((item) => {
              const active = (selectedDate ?? overview.tradeDate) === item.tradeDate;
              return (
                <button
                  key={item.tradeDate}
                  type="button"
                  role="listitem"
                  onClick={() => onSelectDate(item.tradeDate)}
                  aria-pressed={active}
                  aria-label={`${item.tradeDate} ${item.stageLabel}`}
                  className={cn(
                    'flex min-w-[4.5rem] flex-col items-start rounded-xl border px-2 py-1.5 text-left transition-colors',
                    active ? 'border-cyan/60 bg-cyan/10' : 'border-border/50 bg-elevated/30 hover:bg-hover',
                    item.kind !== 'full' && 'opacity-70'
                  )}
                >
                  <span className="text-[10px] tabular-nums text-muted-text">{item.tradeDate.slice(5)}</span>
                  <span className="mt-0.5 inline-flex items-center gap-1 text-xs text-foreground">
                    <span className={cn('h-1.5 w-1.5 rounded-full', STAGE_DOTS[item.stage ?? ''] ?? 'bg-muted-text')} />
                    {item.stageLabel || '—'}
                  </span>
                  <span className="text-[10px] tabular-nums text-secondary-text">
                    {item.ztCount ?? '—'}停 · {item.maxHeight ?? '—'}板
                    {item.kind !== 'full' ? ` · ${t('dabanKing.stripBackfill')}` : ''}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      ) : null}

      <button
        type="button"
        onClick={() => setShowConditions((value) => !value)}
        aria-expanded={showConditions}
        className="mt-4 inline-flex items-center gap-1 text-xs text-cyan hover:underline"
      >
        {t('dabanKing.conditionsToggle')}
        <ChevronDown className={cn('h-3.5 w-3.5 transition-transform', showConditions && 'rotate-180')} aria-hidden="true" />
      </button>
      {showConditions ? (
        <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {stage.conditions.map((entry) => (
            <div
              key={entry.stage}
              className={cn(
                'rounded-xl border p-3',
                entry.stage === stage.key ? 'border-cyan/50 bg-cyan/5' : 'border-border/50 bg-elevated/30'
              )}
            >
              <div className="mb-2 flex items-center justify-between text-xs">
                <span className="font-medium text-foreground">{entry.label}</span>
                <span className="tabular-nums text-secondary-text">{formatRate(entry.hitRate)}</span>
              </div>
              <ul className="space-y-1">
                {entry.items.map((item) => (
                  <li key={item.key} className="flex items-center gap-2 text-[11px] text-secondary-text">
                    <HitIcon hit={item.hit} />
                    <span className="flex-1">{item.label}</span>
                    <span className="tabular-nums text-foreground">
                      {item.value === null || item.value === undefined ? '—' : String(Math.round(item.value * 100) / 100)}
                    </span>
                    <span className="tabular-nums">{item.threshold}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      ) : null}
    </SectionCard>
  );
};
