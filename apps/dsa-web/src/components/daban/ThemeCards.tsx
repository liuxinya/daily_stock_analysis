import type React from 'react';
import { Badge, SectionCard } from '../common';
import type { UiTextKey } from '../../i18n/uiText';
import type { DabanTheme } from '../../types/daban';
import { cn } from '../../utils/cn';
import { changeTone, formatPct } from './format';
import { StockLink } from './shared';

type Translate = (key: UiTextKey, params?: Record<string, string | number>) => string;

const STORY_VARIANTS = { big: 'danger', medium: 'warning', small: 'default' } as const;
const NOVELTY_VARIANTS: Record<string, 'history' | 'info' | 'default'> = { new: 'history', related: 'info', same: 'default' };

/** ③ 主线题材：陈小群「主线识别」+ 科比「题材预判（故事能讲多大）」。 */
export const ThemeCards: React.FC<{ themes: DabanTheme[]; t: Translate }> = ({ themes, t }) => {
  const shown = (themes ?? []).slice(0, 6);
  if (!shown.length) return null;

  return (
    <SectionCard title={t('dabanKing.themesTitle')} subtitle={t('dabanKing.themesSubtitle')}>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {shown.map((theme) => (
          <div
            key={theme.key}
            className={cn(
              'rounded-xl border p-3',
              theme.isMain ? 'border-danger/30 bg-danger/5' : 'border-border/50 bg-elevated/30'
            )}
          >
            <div className="flex items-start justify-between gap-2">
              <div>
                <p className="text-sm font-semibold text-foreground">
                  <span className="mr-1 text-xs text-muted-text">#{theme.rank}</span>
                  {theme.name}
                </p>
                <p className="mt-0.5 text-[11px] text-secondary-text">
                  涨停 {theme.ztCount} · 最高 {theme.maxBoards} 板 · {t('dabanKing.themeDays', { days: theme.days })}
                </p>
              </div>
              <div className="flex flex-wrap justify-end gap-1">
                {theme.isMain ? <Badge variant="danger">{t('dabanKing.mainline')}</Badge> : null}
                <Badge variant="default">{theme.statusLabel}</Badge>
                <Badge variant={NOVELTY_VARIANTS[theme.novelty] ?? 'default'}>{theme.noveltyLabel}</Badge>
              </div>
            </div>

            {theme.storySpace ? (
              <div className="mt-2 text-[11px] leading-relaxed">
                <Badge variant={STORY_VARIANTS[theme.storySpace]}>
                  {t('dabanKing.storySpace', { label: theme.storyLabel })}
                  {theme.storySource === 'rule' ? `（${t('dabanKing.storyRule')}）` : ''}
                </Badge>
                {theme.storyReason ? <p className="mt-1 text-secondary-text">{theme.storyReason}</p> : null}
                {theme.commonality ? <p className="mt-1 text-muted-text">{theme.commonality}</p> : null}
              </div>
            ) : null}

            {theme.premium !== null ? (
              <p className="mt-2 text-[11px] text-secondary-text">
                {t('dabanKing.themePremium')}{' '}
                <span className={changeTone(theme.premium)}>{formatPct(theme.premium)}</span>
              </p>
            ) : null}

            <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1">
              {theme.members.slice(0, 6).map((member) => (
                <span key={member.code} className="inline-flex items-center gap-1 text-xs">
                  <StockLink code={member.code} name={member.name} />
                  <span className="text-[10px] text-muted-text">{member.boards}板</span>
                </span>
              ))}
            </div>

            {theme.news?.length ? (
              <ul className="mt-2 space-y-0.5 border-t border-border/40 pt-2">
                {theme.news.slice(0, 3).map((item) => (
                  <li key={`${item.title}-${item.publishedAt}`} className="truncate text-[11px] text-secondary-text">
                    {item.url ? (
                      <a href={item.url} target="_blank" rel="noreferrer noopener" className="hover:text-cyan hover:underline">
                        {item.title}
                      </a>
                    ) : (
                      item.title
                    )}
                    {item.platformLabel ? <span className="ml-1 text-muted-text">· {item.platformLabel}</span> : null}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ))}
      </div>
    </SectionCard>
  );
};
