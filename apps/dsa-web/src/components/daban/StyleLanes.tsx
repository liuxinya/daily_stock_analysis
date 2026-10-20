import type React from 'react';
import { Check, Minus, Scale, X } from 'lucide-react';
import { SectionCard } from '../common';
import type { UiTextKey } from '../../i18n/uiText';
import type { DabanLane, DabanPrinciple, DabanStyle, DabanTrackRecord, DabanVerdict } from '../../types/daban';
import { cn } from '../../utils/cn';
import { formatPct, STYLE_ACCENTS, STYLE_TEXT } from './format';
import { CitedLine, PrincipleTags } from './shared';
import { PickCard } from './PickCard';

type Translate = (key: UiTextKey, params?: Record<string, string | number>) => string;

const STYLES: DabanStyle[] = ['chen', 'kobe'];

/** ④ 今日打法裁决：规则投票（逐票可核对）+ 模型补充意见（只展示，不改裁决）。 */
export const StyleVerdictPanel: React.FC<{
  verdict: DabanVerdict;
  principles: Record<string, DabanPrinciple>;
  styleLabels: Record<string, string>;
  trackRecord: DabanTrackRecord;
  t: Translate;
}> = ({ verdict, principles, styleLabels, trackRecord, t }) => (
  <SectionCard title={t('dabanKing.verdictTitle')} subtitle={t('dabanKing.verdictSubtitle')}>
    <div className="flex flex-col gap-3 lg:flex-row lg:items-start">
      <div className="flex min-w-[14rem] flex-col gap-1 rounded-2xl border border-border/60 bg-elevated/40 px-4 py-3">
        <span className="inline-flex items-center gap-2 text-lg font-semibold text-foreground">
          <Scale className="h-5 w-5 text-cyan" aria-hidden="true" />
          {verdict.label}
        </span>
        <span className="text-xs text-secondary-text">
          <span className={STYLE_TEXT.chen}>{styleLabels.chen} {verdict.votes.chen} 票</span>
          {' : '}
          <span className={STYLE_TEXT.kobe}>{styleLabels.kobe} {verdict.votes.kobe} 票</span>
        </span>
        {STYLES.map((style) => {
          const record = trackRecord?.[style];
          if (!record || !record.days) return null;
          return (
            <span key={style} className="text-[11px] text-muted-text">
              {t('dabanKing.trackRecord', {
                style: styleLabels[style],
                days: record.days,
                avg: formatPct(record.avg, 2),
              })}
            </span>
          );
        })}
      </div>
      <p className="flex-1 text-sm leading-relaxed text-foreground">{verdict.summary}</p>
    </div>

    <ul className="mt-3 grid gap-2 sm:grid-cols-2">
      {verdict.signals.map((vote) => (
        <li
          key={vote.key}
          className={cn(
            'flex items-start gap-2 rounded-xl border px-3 py-2 text-xs',
            vote.hit ? 'border-cyan/30 bg-cyan/5' : 'border-border/40 bg-elevated/20'
          )}
        >
          <span className="mt-0.5">
            {!vote.available ? (
              <Minus className="h-3.5 w-3.5 text-muted-text" aria-label="不投票" />
            ) : vote.hit ? (
              <Check className="h-3.5 w-3.5 text-danger" aria-label="命中" />
            ) : (
              <X className="h-3.5 w-3.5 text-muted-text" aria-label="未命中" />
            )}
          </span>
          <span className="flex-1">
            <span className="font-medium text-foreground">{vote.label}</span>
            {vote.style ? (
              <span className={cn('ml-1 text-[10px]', STYLE_TEXT[vote.style])}>→ {styleLabels[vote.style]}</span>
            ) : null}
            <span className="mt-0.5 block text-secondary-text">{vote.evidence}</span>
            <PrincipleTags ids={vote.principleIds} principles={principles} />
          </span>
        </li>
      ))}
    </ul>

    {verdict.modelOpinion?.reason ? (
      <div className="mt-3 rounded-xl border border-border/50 bg-elevated/30 p-3 text-xs">
        <p className="font-medium text-foreground">
          {t('dabanKing.modelOpinion')}
          {verdict.modelOpinion.agree ? `（${t('dabanKing.modelAgree')}）` : `（${t('dabanKing.modelDisagree')}）`}
        </p>
        <p className="mt-1 leading-relaxed text-secondary-text">{verdict.modelOpinion.reason}</p>
      </div>
    ) : null}
  </SectionCard>
);

/** ⑤ 两条推荐线：被裁决推荐的一线排前并高亮；空仓时显示该派原话。 */
export const StyleLanes: React.FC<{
  lanes: Record<DabanStyle, DabanLane>;
  preferred: DabanStyle | 'both';
  principles: Record<string, DabanPrinciple>;
  t: Translate;
}> = ({ lanes, preferred, principles, t }) => {
  const order: DabanStyle[] = preferred === 'kobe' ? ['kobe', 'chen'] : ['chen', 'kobe'];
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {order.map((style) => {
        const lane = lanes[style];
        if (!lane) return null;
        const highlighted = preferred === style || preferred === 'both';
        const picks = [...lane.actionable, ...lane.trial];
        return (
          <section
            key={style}
            aria-label={lane.label}
            className={cn(
              'rounded-2xl border bg-card/75 p-4 shadow-soft-card',
              highlighted ? STYLE_ACCENTS[style] : 'border-subtle opacity-95'
            )}
          >
            <header className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
              <h2 className={cn('text-lg font-semibold', STYLE_TEXT[style])}>
                {lane.label}
                {preferred === style ? <span className="ml-2 text-xs font-normal text-foreground">★ {t('dabanKing.preferred')}</span> : null}
              </h2>
              <span className="text-xs text-secondary-text">
                {lane.stageLabel} · {t('dabanKing.laneCap', { cap: lane.positionCapLabel })}
              </span>
            </header>

            {lane.flat ? (
              <div className="mb-3 rounded-xl border border-success/30 bg-success/5 p-3 text-sm text-foreground">
                <p className="font-semibold">{t('dabanKing.flat')}：{lane.flatMessage}</p>
                <PrincipleTags ids={lane.flatPrinciples} principles={principles} />
              </div>
            ) : null}

            {lane.commentary ? (
              <p className="mb-3 text-xs leading-relaxed text-secondary-text">
                {lane.commentary} <PrincipleTags ids={lane.commentaryPrinciples} principles={principles} />
              </p>
            ) : null}

            {picks.length ? (
              <div className="space-y-3">
                {picks.map((pick) => (
                  <PickCard key={`${style}-${pick.code}`} pick={pick} principles={principles} t={t} />
                ))}
              </div>
            ) : !lane.flat ? (
              <p className="text-xs text-secondary-text">{t('dabanKing.noPicks')}</p>
            ) : null}

            {lane.watch.length ? (
              <details className="mt-3">
                <summary className="cursor-pointer text-xs text-cyan">
                  {t('dabanKing.watchList', { count: lane.watch.length })}
                </summary>
                <div className="mt-2 grid gap-2">
                  {lane.watch.map((pick) => (
                    <PickCard key={`${style}-w-${pick.code}`} pick={pick} principles={principles} t={t} compact />
                  ))}
                </div>
              </details>
            ) : null}

            <div className="mt-3 border-t border-border/40 pt-2">
              <p className="text-[11px] font-medium text-foreground">{t('dabanKing.sellRules')}</p>
              <ul className="mt-1 space-y-1">
                {lane.sellRules.map((item) => (
                  <CitedLine key={item.text} item={item} principles={principles} />
                ))}
              </ul>
            </div>
          </section>
        );
      })}
    </div>
  );
};
