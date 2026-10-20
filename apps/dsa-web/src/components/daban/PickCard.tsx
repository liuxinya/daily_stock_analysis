import type React from 'react';
import { useState } from 'react';
import { ChevronDown, Handshake } from 'lucide-react';
import { Badge } from '../common';
import type { UiTextKey } from '../../i18n/uiText';
import type { DabanPick, DabanPrinciple } from '../../types/daban';
import { cn } from '../../utils/cn';
import { ACTION_VARIANTS, changeTone, formatPct, gradeTone } from './format';
import { CitedLine, PrincipleTags, StockLink } from './shared';

type Translate = (key: UiTextKey, params?: Record<string, string | number>) => string;

type Props = {
  pick: DabanPick;
  principles: Record<string, DabanPrinciple>;
  t: Translate;
  compact?: boolean;
};

/**
 * 一张推荐卡片：评级与分数、模式、行动级别、理由（每条带出处）、次日触发 / 放弃条件、
 * 七维得分与模型调分。观察区用 ``compact`` 只显示头部与第一条理由。
 */
export const PickCard: React.FC<Props> = ({ pick, principles, t, compact = false }) => {
  const [open, setOpen] = useState(!compact && pick.action === 'actionable');

  return (
    <article
      className={cn(
        'rounded-xl border p-3',
        pick.action === 'actionable' ? 'border-danger/30 bg-danger/5' : 'border-border/50 bg-elevated/30'
      )}
      aria-label={`${pick.name} ${pick.modeLabel}`}
    >
      <header className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <StockLink code={pick.code} name={pick.name} />
          <div className="mt-1 flex flex-wrap items-center gap-1">
            <Badge variant={ACTION_VARIANTS[pick.action]}>{pick.actionLabel}</Badge>
            <Badge variant="info">{pick.modeLabel}</Badge>
            <Badge variant="default">{pick.roleLabel}</Badge>
            {pick.themeName ? <Badge variant="default">{pick.themeName}</Badge> : null}
            {pick.consensus ? (
              <Badge variant="history">
                <Handshake className="mr-0.5 inline h-3 w-3" aria-hidden="true" />
                {t('dabanKing.consensus')}
              </Badge>
            ) : null}
          </div>
        </div>
        <div className="shrink-0 text-right">
          <div className={cn('text-2xl font-semibold tabular-nums leading-none', gradeTone(pick.grade))}>
            {Math.round(pick.score)}
          </div>
          <div className="mt-0.5 text-[10px] text-secondary-text">
            {pick.grade} · {pick.positionPct > 0 ? t('dabanKing.position', { value: pick.positionPct }) : t('dabanKing.watch')}
          </div>
          {pick.changePct !== null ? (
            <div className={cn('text-[10px] tabular-nums', changeTone(pick.changePct))}>今日 {formatPct(pick.changePct)}</div>
          ) : null}
        </div>
      </header>

      {pick.notes?.length ? (
        <p className="mt-1.5 text-[11px] text-warning">{pick.notes.join('；')}</p>
      ) : null}

      <ul className="mt-2 space-y-1">
        {(compact ? pick.reasons.slice(0, 1) : pick.reasons).map((item) => (
          <CitedLine key={item.text} item={item} principles={principles} />
        ))}
        {!compact && pick.llmNote ? (
          <CitedLine item={pick.llmNote} principles={principles} bullet="✎" className="text-secondary-text" />
        ) : null}
      </ul>

      {!compact ? (
        <>
          {pick.facts?.length ? (
            <ul className="mt-2 space-y-0.5">
              {pick.facts.map((fact) => (
                <li key={fact} className="text-[11px] text-muted-text">
                  {fact}
                </li>
              ))}
            </ul>
          ) : null}

          {pick.triggers?.length ? (
            <div className="mt-2">
              <p className="text-[11px] font-medium text-foreground">{t('dabanKing.triggers')}</p>
              <ul className="mt-1 space-y-1">
                {pick.triggers.map((item) => (
                  <CitedLine key={item.text} item={item} principles={principles} bullet="☐" />
                ))}
              </ul>
            </div>
          ) : null}

          {pick.abandon?.length ? (
            <div className="mt-2">
              <p className="text-[11px] font-medium text-foreground">{t('dabanKing.abandon')}</p>
              <ul className="mt-1 space-y-1">
                {pick.abandon.map((item) => (
                  <CitedLine key={item.text} item={item} principles={principles} bullet="✕" />
                ))}
              </ul>
            </div>
          ) : null}

          <button
            type="button"
            onClick={() => setOpen((value) => !value)}
            aria-expanded={open}
            className="mt-2 inline-flex items-center gap-1 text-[11px] text-cyan hover:underline"
          >
            {t('dabanKing.dimensions')}
            <ChevronDown className={cn('h-3 w-3 transition-transform', open && 'rotate-180')} aria-hidden="true" />
          </button>
          {open ? (
            <div className="mt-2 space-y-1.5">
              {pick.dimensions.map((dim) => (
                <div key={dim.key} className="text-[11px]">
                  <div className="flex items-center gap-2">
                    <span className="w-24 shrink-0 text-secondary-text">
                      {dim.key} {dim.label}
                    </span>
                    <span className="h-1.5 w-20 overflow-hidden rounded-full bg-elevated" aria-hidden="true">
                      <span className="block h-full rounded-full bg-cyan" style={{ width: `${(dim.score / dim.max) * 100}%` }} />
                    </span>
                    <span className="tabular-nums text-foreground">
                      {dim.score}/{dim.max}
                    </span>
                  </div>
                  <p className="pl-[6.5rem] text-muted-text">
                    {dim.reason} <PrincipleTags ids={dim.principleIds} principles={principles} />
                  </p>
                </div>
              ))}
              <p className="text-[11px] text-secondary-text">
                规则分 {pick.ruleScore}
                {pick.llmAdjust ? ` · ${t('dabanKing.llmAdjust', { value: pick.llmAdjust > 0 ? `+${pick.llmAdjust}` : pick.llmAdjust })}` : ''}
                {pick.riskPenalty ? ` · ${t('dabanKing.riskPenalty', { value: pick.riskPenalty })}` : ''}
              </p>
              {pick.llmAdjustReason ? <p className="text-[11px] text-muted-text">✎ {pick.llmAdjustReason}</p> : null}
              {pick.risks?.length ? (
                <ul className="space-y-0.5">
                  {pick.risks.map((risk) => (
                    <li key={risk.text} className="text-[11px] text-warning">
                      ⚠ {risk.text}（-{risk.points}） <PrincipleTags ids={risk.principleIds} principles={principles} />
                    </li>
                  ))}
                </ul>
              ) : null}
              {pick.alsoModes?.length ? (
                <p className="text-[11px] text-muted-text">{t('dabanKing.alsoModes', { modes: pick.alsoModes.join('、') })}</p>
              ) : null}
            </div>
          ) : null}
        </>
      ) : null}
    </article>
  );
};
