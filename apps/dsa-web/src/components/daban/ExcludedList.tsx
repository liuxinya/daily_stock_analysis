import type React from 'react';
import { Collapsible } from '../common';
import type { UiTextKey } from '../../i18n/uiText';
import type { DabanExcluded, DabanPrinciple, DabanReview, DabanStyle } from '../../types/daban';
import { cn } from '../../utils/cn';
import { changeTone, formatPct } from './format';
import { PrincipleTags, StockLink } from './shared';

type Translate = (key: UiTextKey, params?: Record<string, string | number>) => string;

/** ⑦ 排除名单：中位股、跟风、死亡换手……每只都写明为什么不做。 */
export const ExcludedList: React.FC<{
  excluded: DabanExcluded[];
  principles: Record<string, DabanPrinciple>;
  t: Translate;
}> = ({ excluded, principles, t }) => {
  if (!excluded?.length) return null;
  return (
    <Collapsible title={t('dabanKing.excludedTitle', { count: excluded.length })}>
      <ul className="space-y-1.5 px-4 pb-4">
        {excluded.map((item) => (
          <li key={item.code} className="flex flex-col gap-0.5 text-xs sm:flex-row sm:items-center sm:gap-3">
            <span className="w-44 shrink-0">
              <StockLink code={item.code} name={item.name} />
            </span>
            <span className="flex-1 text-secondary-text">
              {item.reason} <PrincipleTags ids={item.principleIds} principles={principles} />
            </span>
          </li>
        ))}
      </ul>
    </Collapsible>
  );
};

/** 昨日推荐 → 今日表现（科比 #22：不断修正自己）。 */
export const ReviewPanel: React.FC<{
  review: DabanReview | null;
  styleLabels: Record<string, string>;
  t: Translate;
}> = ({ review, styleLabels, t }) => {
  if (!review) return null;
  const styles: DabanStyle[] = ['chen', 'kobe'];
  return (
    <Collapsible title={t('dabanKing.reviewTitle', { date: review.tradeDate })}>
      <div className="grid gap-3 px-4 pb-4 sm:grid-cols-2">
        {styles.map((style) => {
          const lane = review[style];
          return (
            <div key={style} className="rounded-xl border border-border/50 bg-elevated/30 p-3">
              <p className="text-xs font-medium text-foreground">{styleLabels[style]}</p>
              {lane?.count ? (
                <>
                  <p className="mt-1 text-[11px] text-secondary-text">
                    {t('dabanKing.reviewSummary', { count: lane.count, limitUp: lane.limitUp })}
                    <span className={cn('ml-1', changeTone(lane.avgChange))}>{formatPct(lane.avgChange, 2)}</span>
                  </p>
                  <ul className="mt-2 space-y-1">
                    {lane.items.map((item) => (
                      <li key={item.code} className="flex items-center justify-between gap-2 text-xs">
                        <StockLink code={item.code} name={item.name} />
                        <span className="text-[10px] text-muted-text">{item.modeLabel}</span>
                        <span className={cn('tabular-nums', changeTone(item.changePct))}>
                          {formatPct(item.changePct)}
                          {item.limitUp ? ' 涨停' : ''}
                        </span>
                      </li>
                    ))}
                  </ul>
                </>
              ) : (
                <p className="mt-1 text-[11px] text-secondary-text">{t('dabanKing.reviewEmpty')}</p>
              )}
            </div>
          );
        })}
      </div>
    </Collapsible>
  );
};
