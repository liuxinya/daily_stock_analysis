import type React from 'react';
import { Badge, Collapsible, SectionCard } from '../common';
import type { UiTextKey } from '../../i18n/uiText';
import type {
  DabanFocusReview,
  DabanFocusStat,
  DabanLegacyLiveStat,
  DabanOverview,
  DabanPrinciple,
  DabanWatchlist,
} from '../../types/daban';
import { cn } from '../../utils/cn';
import { changeTone, formatClock, formatPct, formatRate, formatSigned } from './format';
import { CitedLine, PrincipleTags, StockLink } from './shared';

type Translate = (key: UiTextKey, params?: Record<string, string | number>) => string;

const TONE_TEXT = { good: 'text-danger', bad: 'text-success', neutral: 'text-foreground' } as const;

/** 短线世界观（11 个维度）：给下一个交易日用的边界、节点和各维度结论；附昨天那一份的对账。 */
export const WorldviewPanel: React.FC<{
  overview: DabanOverview;
  principles: Record<string, DabanPrinciple>;
  t: Translate;
}> = ({ overview, principles, t }) => {
  const view = overview.worldview;
  // 2026-11 之前生成的快照没有世界观：``worldview`` 为 null，接口也可能把它填成空对象。
  if (!view?.dims?.length) {
    return (
      <SectionCard title={t('dabanKing.worldview.title')}>
        <p className="text-xs text-secondary-text">{t('dabanKing.worldview.legacy')}</p>
      </SectionCard>
    );
  }
  const big = overview.conclusion?.bigPicture;
  const check = overview.worldviewCheck;
  const accuracy = Object.entries(overview.worldviewAccuracy?.dims ?? {});
  return (
    <SectionCard title={t('dabanKing.worldview.title')} subtitle={t('dabanKing.worldview.subtitle', { date: view.forDate, base: view.basedOn })}>
      <div className={cn('rounded-xl border p-3', view.blocked ? 'border-border/60 bg-elevated/30' : 'border-danger/40 bg-danger/5')}>
        <p className="text-sm font-semibold text-foreground">{view.boundaryText}</p>
        {big?.text ? (
          <p className="mt-1 text-xs leading-relaxed text-secondary-text">
            {big.text} <PrincipleTags ids={big.principleIds ?? []} principles={principles} />
          </p>
        ) : null}
      </div>
      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        {view.dims.map((dim) => (
          <div key={dim.key} className="rounded-xl border border-border/50 bg-elevated/30 p-2.5" data-testid={`wv-${dim.key}`}>
            <p className="text-[11px] text-muted-text">{dim.label}</p>
            <p className={cn('text-xs font-medium', TONE_TEXT[dim.tone] ?? TONE_TEXT.neutral)}>{dim.value}</p>
            {dim.detail ? <p className="mt-0.5 text-[11px] leading-relaxed text-secondary-text">{dim.detail}</p> : null}
            <p className="mt-0.5 text-[10px] text-muted-text">{t('dabanKing.worldview.role', { role: dim.role })}｜{dim.source}</p>
          </div>
        ))}
      </div>
      {check?.checks?.length ? (
        <div className="mt-3 border-t border-border/40 pt-2">
          <p className="text-xs font-medium text-foreground">{t('dabanKing.worldview.checkTitle', { date: check.forDate })}</p>
          <ul className="mt-1 space-y-0.5">
            {check.checks.map((item) => (
              <li key={item.key} className="text-[11px] text-secondary-text">
                <span className={item.ok ? 'text-danger' : item.ok === false ? 'text-success' : 'text-muted-text'}>
                  {item.ok ? '✓' : item.ok === false ? '✗' : '—'}
                </span>{' '}
                {item.label}：{item.detail}
              </li>
            ))}
          </ul>
          {accuracy.length ? (
            <p className="mt-1 text-[11px] text-muted-text">
              {t('dabanKing.worldview.accuracy', { days: overview.worldviewAccuracy?.days ?? 0 })}
              {accuracy.map(([, row]) => ` ${row.label} ${formatRate(row.rate)}（${row.ok}/${row.n}）`).join('；')}
            </p>
          ) : null}
        </div>
      ) : null}
    </SectionCard>
  );
};

/** 次日观察清单：盘后不推荐、不打分，只列明天要盯的票和触发 / 放弃条件，明天盘中满足条件时由雷达统一评分。 */
export const WatchlistPanel: React.FC<{
  watchlist: DabanWatchlist | undefined;
  principles: Record<string, DabanPrinciple>;
  t: Translate;
}> = ({ watchlist, principles, t }) => {
  if (!watchlist?.items?.length) return null;
  return (
    <SectionCard
      title={t('dabanKing.watchlist.title', { date: watchlist.forDate ?? '—' })}
      subtitle={t('dabanKing.watchlist.subtitle')}
    >
      <div className="grid gap-2 lg:grid-cols-2">
        {watchlist.items.map((item) => (
          <div key={item.code} className="rounded-xl border border-border/50 bg-elevated/30 p-2.5">
            <div className="flex flex-wrap items-center gap-2">
              <StockLink code={item.code} name={item.name} />
              {item.modes.filter(Boolean).map((mode) => <Badge key={mode} variant="default">{mode}</Badge>)}
              {item.themeName ? <span className="text-[11px] text-secondary-text">{item.themeName}</span> : null}
              {item.roleLabel ? <span className="text-[11px] text-muted-text">{item.roleLabel}</span> : null}
            </div>
            {item.triggers.length ? (
              <ul className="mt-1 space-y-0.5">
                {item.triggers.slice(0, 3).map((sig) => (
                  <CitedLine key={sig.text} item={sig} principles={principles} bullet={t('dabanKing.watchlist.trigger')} />
                ))}
              </ul>
            ) : null}
            {item.abandon.length ? (
              <ul className="mt-0.5 space-y-0.5">
                {item.abandon.slice(0, 2).map((sig) => (
                  <CitedLine key={sig.text} item={sig} principles={principles} bullet={t('dabanKing.watchlist.abandon')}
                             className="text-secondary-text" />
                ))}
              </ul>
            ) : null}
          </div>
        ))}
      </div>
    </SectionCard>
  );
};

const statText = (stat: DabanFocusStat | undefined, t: Translate) =>
  stat && stat.n
    ? t('dabanKing.focusReview.statLine', {
      n: stat.n, days: stat.days, seal: formatRate(stat.sealRate),
      h1: formatPct(stat.h1.mean, 2), win: formatRate(stat.h1.win),
      open: formatPct(stat.nextOpen.mean, 2), close: formatPct(stat.nextClose.mean, 2), holding: stat.holding,
    })
    : t('dabanKing.focusReview.noSample');

/** 「重点」推荐成绩单：今天的每一只（初始分→收盘分、逐项归因、成绩）+ 近 20 个交易日的分组成绩与计分项准确率。 */
export const FocusReviewPanel: React.FC<{
  review: DabanFocusReview | undefined;
  legacy?: Record<string, DabanLegacyLiveStat>;
  t: Translate;
}> = ({ review, legacy, t }) => {
  // 2026-11 之前生成的快照没有推荐成绩单：``focusReview`` 是空对象（接口的默认值），不是 undefined。
  if (!review || !Array.isArray(review.items)) return null;
  const stats = review.stats;
  const legacyRows = Object.entries(legacy ?? {}).filter(([, row]) => row?.picks);
  return (
    <SectionCard title={t('dabanKing.focusReview.title')} subtitle={t('dabanKing.focusReview.subtitle')}>
      <div className="space-y-1 text-[11px] text-secondary-text" data-testid="focus-stats">
        <p className="text-xs text-foreground">{t('dabanKing.focusReview.all')}：{statText(stats?.all, t)}</p>
        <p>{t('dabanKing.focusReview.byGroup.normal')}：{statText(stats?.byGroup?.normal, t)}</p>
        <p className="text-purple">{t('dabanKing.focusReview.byGroup.observe')}：{statText(stats?.byGroup?.observe, t)}</p>
        <p>{t('dabanKing.focusReview.byScore.pos')}：{statText(stats?.byScore?.pos, t)}</p>
        <p>{t('dabanKing.focusReview.byScore.zero')}：{statText(stats?.byScore?.zero, t)}</p>
        <p>{t('dabanKing.focusReview.byNode.node')}：{statText(stats?.byNode?.node, t)}</p>
        <p>{t('dabanKing.focusReview.byEntry.opened')}：{statText(stats?.byEntry?.opened, t)}</p>
        {review.observeLive?.verdict ? (
          <p className="text-muted-text">{t(`dabanKing.focusReview.observeVerdict.${review.observeLive.verdict}` as UiTextKey)}</p>
        ) : null}
      </div>

      {review.itemsAccuracy?.some((row) => row.n) ? (
        <Collapsible title={t('dabanKing.focusReview.accuracyTitle')}>
          <ul className="space-y-0.5 px-4 pb-3 text-[11px] text-secondary-text">
            {review.itemsAccuracy.filter((row) => row.n).map((row) => (
              <li key={row.key}>
                {row.label}：{t('dabanKing.focusReview.accuracyLine', {
                  n: row.n, expected: formatSigned(row.expected), actual: formatSigned(row.actual),
                  verdict: t(`dabanKing.focusReview.verdict.${row.verdict}` as UiTextKey),
                })}
              </li>
            ))}
          </ul>
        </Collapsible>
      ) : null}

      {legacyRows.length ? (
        <Collapsible title={t('dabanKing.focusReview.legacyTitle')}>
          <ul className="space-y-0.5 px-4 pb-3 text-[11px] text-secondary-text" data-testid="legacy-live-stats">
            {legacyRows.map(([style, row]) => (
              <li key={style}>
                {t('dabanKing.focusReview.legacyLine', {
                  style: row.label, picks: row.picks, days: row.days, sealed: formatRate(row.sealedRate),
                  open: formatPct(row.openMean, 2), close: formatPct(row.closeMean, 2), win: formatRate(row.winRate),
                })}
              </li>
            ))}
          </ul>
        </Collapsible>
      ) : null}

      <div className="mt-3">
        <p className="text-xs font-medium text-foreground">{t('dabanKing.focusReview.today', { date: review.tradeDate, n: review.count })}</p>
        {review.items.length ? (
          <ul className="mt-1 space-y-2">
            {review.items.map((item) => (
              <li key={item.code} className="rounded-xl border border-border/50 bg-elevated/30 p-2.5 text-[11px]">
                <div className="flex flex-wrap items-center gap-2">
                  <StockLink code={item.code} name={item.name} />
                  <span className="text-secondary-text">{item.themeName}</span>
                  <span className="tabular-nums text-secondary-text">
                    {t('dabanKing.focusReview.entry', { time: formatClock(item.enteredAt), cost: item.cost?.toFixed(2) ?? '—' })}
                  </span>
                  <span className="tabular-nums text-foreground">
                    {t('dabanKing.focusReview.scores', { init: formatSigned(item.initScore), close: formatSigned(item.closeScore) })}
                  </span>
                  {item.sealedClose ? <Badge variant="danger">{t('dabanKing.focusReview.sealed')}</Badge> : null}
                  {item.groupKey === 'observe' ? <Badge variant="history">{t('dabanKing.live.observe')}</Badge> : null}
                  {item.h1Ret !== null ? (
                    <span className={cn('tabular-nums', changeTone(item.h1Ret))}>H1 {formatPct(item.h1Ret, 2)}</span>
                  ) : (
                    <span className="text-muted-text">{t(`dabanKing.focusReview.settle.${item.settleStatus ?? 'pending'}` as UiTextKey)}</span>
                  )}
                </div>
                {item.attribution?.diff?.length ? (
                  <p className="mt-1 text-secondary-text">
                    {t('dabanKing.focusReview.diff')}：
                    {item.attribution.diff.map((d) => `${d.label} ${formatSigned(d.init)}→${formatSigned(d.close)}`).join('；')}
                  </p>
                ) : null}
                {item.attribution?.unknowable?.length ? (
                  <p className="mt-0.5 text-muted-text">
                    {t('dabanKing.focusReview.unknowable')}：
                    {item.attribution.unknowable.map((e) => `${formatClock(e.at)} ${t(`dabanKing.focusReview.event.${e.kind}` as UiTextKey)}`).join('、')}
                  </p>
                ) : null}
                {item.attribution?.missed?.length ? (
                  <p className="mt-0.5 text-warning">
                    {t('dabanKing.focusReview.missed')}：{item.attribution.missed.map((n) => n.title).join('；')}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-[11px] text-secondary-text">{t('dabanKing.focusReview.empty')}</p>
        )}
      </div>
    </SectionCard>
  );
};
