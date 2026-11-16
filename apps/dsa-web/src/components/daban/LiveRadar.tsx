import type React from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Radio, RefreshCw } from 'lucide-react';
import { Badge, EmptyState, InlineAlert, SectionCard } from '../common';
import type { UiTextKey } from '../../i18n/uiText';
import type { DabanLive, DabanPrinciple } from '../../types/daban';
import { cn } from '../../utils/cn';
import { FocusCard, LimitBoardRow, ScoreItemLine } from './FocusCards';
import { formatClock, formatPct, formatRate, formatSigned } from './format';
import { HitIcon, PrincipleTags, StockLink } from './shared';

type Translate = (key: UiTextKey, params?: Record<string, string | number>) => string;

const DEFAULT_POLL_SEC = 10;

type RadarTab = 'focus' | 'limit';

/** 热度色阶：排名越靠前越红（0°），越靠后越蓝（210°）。用于题材卡片的边框与底色。 */
const heatHue = (index: number, total: number) => {
  if (total <= 1) return 0;
  return Math.round((index / (total - 1)) * 210);
};

/** 取数不碰 state：模块级纯 fetcher，setState 只在 promise 回调里（仓库的 hooks 规则要求）。 */
const fetchLive = async (force: boolean) => {
  const { dabanLiveApi } = await import('../../api/daban');
  return dabanLiveApi.get(force);
};

/** 是否在 A 股交易时段：一律按北京时间判断，与浏览器所在时区无关。节假日由后端 ``trading`` 字段兜底。 */
const inTradingWindow = () => {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Shanghai', weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: false,
  }).formatToParts(new Date());
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? '';
  const hhmm = Number(get('hour')) * 100 + Number(get('minute'));
  return !['Sat', 'Sun'].includes(get('weekday')) && hhmm >= 915 && hhmm <= 1500;
};

const BOUNDARY_TONES: Record<string, string> = {
  open: 'border-danger/40 bg-danger/5',
  partial: 'border-warning/40 bg-warning/5',
  closed: 'border-border/60 bg-elevated/30',
  frozen: 'border-warning/40 bg-warning/5',
};

/**
 * 盘中雷达：找热点题材和题材里可能涨停、用户还买得进的票。
 *
 * - 「重点」：评分达标（总分 ≥ 门槛）、还买得进的票，进入那一刻的现价记作成本，全部记成绩；同一题材内按分数排序。
 * - 「涨停板」：已封住的票，每个题材的龙 1 / 龙 2 置顶，不评分、不记成绩。
 * - 顶部是今天的世界观边界：能不能推荐、为什么、盘中有没有推翻信号放开了哪个方向。
 */
export const LiveRadar: React.FC<{
  principles: Record<string, DabanPrinciple>;
  t: Translate;
}> = ({ principles, t }) => {
  const [live, setLive] = useState<DabanLive | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [auto, setAuto] = useState(true);
  const [tab, setTab] = useState<RadarTab>('focus');
  const [themeFilter, setThemeFilter] = useState<string | null>(null);
  const [newCodes, setNewCodes] = useState<Set<string>>(new Set());
  const seen = useRef<Set<string>>(new Set());
  const tickRef = useRef(0);

  const refresh = useCallback((force: boolean) => {
    void fetchLive(force)
      .then((payload) => {
        const fresh = new Set<string>();
        (payload.focus?.groups ?? []).forEach((group) => group.items.forEach((item) => {
          if (!seen.current.has(item.code)) {
            seen.current.add(item.code);
            if (tickRef.current > 0) fresh.add(item.code);
          }
        }));
        setNewCodes(fresh);
        setLive(payload);
        setError(null);
        tickRef.current += 1;
      })
      .catch((err: unknown) => {
        setError((err as Error)?.message || t('dabanKing.live.error'));
      })
      .finally(() => setLoading(false));
  }, [t]);

  const pollSec = Math.max(5, live?.pollSec || DEFAULT_POLL_SEC);
  useEffect(() => {
    refresh(false);
    if (!auto) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible' && inTradingWindow()) {
        refresh(false);
      }
    }, pollSec * 1000);
    return () => window.clearInterval(timer);
  }, [auto, refresh, pollSec]);

  if (loading && !live) {
    return <div className="py-16 text-center text-sm text-secondary-text">{t('common.loading')}</div>;
  }
  if (error && !live) {
    return <InlineAlert variant="danger" message={error} />;
  }
  if (!live) {
    return <EmptyState title={t('dabanKing.live.emptyTitle')} description={t('dabanKing.live.emptyDescription')} />;
  }

  const view = live.worldview;
  const boundary = view?.boundary;
  const rt = live.realtimeGate;
  const prevStage = live.previous?.stage;
  const prevMetrics = live.previous?.metrics;
  const gateConditions = prevStage?.conditions?.find((entry) => entry.stage === prevStage.key)?.items ?? [];
  const sortedThemes = [...(live.hotThemes ?? [])].sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99) || b.ztCount - a.ztCount);
  const inTheme = (key: string) => themeFilter === null || key === themeFilter;
  // 推荐点置顶的票单独放在最上面，题材分组里不再重复出现。
  const pinnedCodes = new Set((live.focus?.pinned ?? []).map((item) => item.code));
  const focusGroups = (live.focus?.groups ?? [])
    .filter((group) => inTheme(group.themeKey))
    .map((group) => ({ ...group, items: group.items.filter((item) => !pinnedCodes.has(item.code)) }))
    .filter((group) => group.items.length);
  const limitGroups = (live.limitBoard?.groups ?? []).filter((group) => inTheme(group.themeKey));
  const watching = (live.focus?.watching ?? []).filter((item) => inTheme(item.themeKey));
  const pinned = (live.focus?.pinned ?? []).filter((item) => inTheme(item.themeKey));
  const focusCount = (live.focus?.groups ?? []).reduce((sum, group) => sum + group.items.length, 0);
  const rules = live.scoreRules;
  const indices = Object.entries(live.indices ?? {});
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-border/50 bg-card/70 px-4 py-3 text-xs">
        <span className={cn('inline-flex items-center gap-1.5', live.trading ? 'text-danger' : 'text-secondary-text')}>
          <Radio className={cn('h-3.5 w-3.5', live.trading && 'animate-pulse')} aria-hidden="true" />
          {live.trading ? t('dabanKing.live.trading') : t('dabanKing.live.closed')}
        </span>
        <span className="text-secondary-text">{t('dabanKing.live.updated', { time: formatClock(live.generatedAt) })}</span>
        <label className="inline-flex items-center gap-1.5 text-secondary-text">
          <input type="checkbox" checked={auto} onChange={(event) => setAuto(event.target.checked)} />
          {t('dabanKing.live.auto', { sec: pollSec })}
        </label>
        <button
          type="button"
          onClick={() => refresh(true)}
          className="inline-flex items-center gap-1 rounded-full border border-border px-2.5 py-1 text-cyan hover:bg-hover"
        >
          <RefreshCw className="h-3 w-3" aria-hidden="true" />
          {t('dabanKing.live.refresh')}
        </button>
        {indices.map(([symbol, quote]) => (
          <span key={symbol} className="tabular-nums text-secondary-text">
            {quote.name} <span className={quote.changePct > 0 ? 'text-danger' : quote.changePct < 0 ? 'text-success' : ''}>
              {formatPct(quote.changePct, 2)}
            </span>
          </span>
        ))}
        <span className="text-muted-text">{t('dabanKing.live.takeMs', { ms: live.durationMs })}</span>
      </div>

      {live.stale ? <InlineAlert variant="warning" message={t('dabanKing.live.stale')} /> : null}
      {live.replay && !live.stale ? <InlineAlert variant="info" message={t('dabanKing.live.replay', { date: live.tradeDate })} /> : null}
      {live.degradedReasons?.length ? (
        <InlineAlert
          variant="info"
          message={<ul className="list-disc space-y-0.5 pl-4">{(live.degradedReasons ?? []).map((r) => <li key={r}>{r}</li>)}</ul>}
        />
      ) : null}
      {!live.previous?.available ? (
        <InlineAlert variant="warning" message={live.previous?.reason || live.note || t('dabanKing.live.needReview')} />
      ) : null}
      {live.featureStatus && !live.featureStatus.ctxReady ? (
        <InlineAlert variant="info" message={t('dabanKing.live.featurePending', { error: live.featureStatus.ctxError || '—' })} />
      ) : null}

      {live.surgePoint ? (
        <div
          data-testid="surge-point"
          className={cn('rounded-2xl border px-4 py-3', live.surgePoint.active ? 'border-danger/60 bg-danger/10' : 'border-border/50 bg-card/70')}
        >
          <p className="text-sm font-semibold text-danger">{t('dabanKing.live.surgeTitle', { at: formatClock(live.surgePoint.at) })}</p>
          <p className="mt-0.5 text-xs text-foreground">{live.surgePoint.label}</p>
          <p className="mt-0.5 text-[11px] leading-relaxed text-secondary-text">
            {t(live.surgePoint.active ? 'dabanKing.live.surgeActive' : 'dabanKing.live.surgeDone', {
              at: formatClock(live.surgePoint.at), until: formatClock(live.surgePoint.until),
            })}
          </p>
          {live.surgePoint.opensBlocked ? (
            <p className="mt-0.5 text-[11px] leading-relaxed text-warning">{t('dabanKing.live.surgeOpensBlocked')}</p>
          ) : null}
        </div>
      ) : null}

      {boundary ? (
        <SectionCard title={t('dabanKing.live.boundaryTitle')} subtitle={t('dabanKing.live.boundarySubtitle')}>
          <div className={cn('rounded-xl border p-3', BOUNDARY_TONES[boundary.status] ?? BOUNDARY_TONES.closed)} data-testid="boundary">
            <p className="text-sm font-semibold text-foreground">{t(`dabanKing.live.boundary.${boundary.status}` as UiTextKey)}</p>
            <p className="mt-1 text-[11px] text-secondary-text">
              {t('dabanKing.live.cycleLine', {
                date: boundary.cycle?.tradeDate ?? live.previous?.tradeDate ?? '—',
                chen: boundary.cycle?.chen || '—',
                kobe: boundary.cycle?.kobe || '—',
              })}
            </p>
            <ul className="mt-1 space-y-0.5">
              {boundary.reasons.map((reason) => (
                <li key={reason} className="text-[11px] leading-relaxed text-foreground">· {reason}</li>
              ))}
            </ul>
            {view?.overrides?.length ? (
              <div className="mt-2 border-t border-border/40 pt-2">
                <p className="text-[11px] font-medium text-foreground">
                  {t('dabanKing.live.overridesTitle')}
                  <span className="ml-1 font-normal text-muted-text">{t('dabanKing.live.overridesProvisional')}</span>
                </p>
                <ul className="mt-0.5 space-y-0.5">
                  {view.overrides.map((item) => (
                    <li key={item.key} className="text-[11px] text-danger">{formatClock(item.at)} {item.label}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            {view?.changes?.length ? (
              <details className="mt-2 text-[11px] text-secondary-text">
                <summary className="cursor-pointer text-cyan">{t('dabanKing.live.changesTitle', { n: view.changes.length })}</summary>
                <ul className="mt-1 space-y-0.5">
                  {view.changes.map((change) => (
                    <li key={`${change.at}-${change.to}`}>
                      {formatClock(change.at)}：{change.from ? t(`dabanKing.live.boundary.${change.from}` as UiTextKey) : '—'} →{' '}
                      {t(`dabanKing.live.boundary.${change.to}` as UiTextKey)}（{change.reasons.join('；')}）
                    </li>
                  ))}
                </ul>
              </details>
            ) : null}
          </div>
          {rt?.enabled && rt.reason ? (
            <div className={cn('mt-2 rounded-xl border p-2.5 text-[11px]',
              rt.upgraded ? 'border-danger/40 bg-danger/5' : rt.frozen ? 'border-warning/40 bg-warning/5' : 'border-border/40 bg-elevated/20')}>
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className={cn('font-semibold', rt.upgraded ? 'text-danger' : rt.frozen ? 'text-warning' : 'text-secondary-text')}>
                  {rt.upgraded ? t('dabanKing.live.rtUpgraded', { phase: rt.todayLabel || '—' })
                    : rt.frozen ? t('dabanKing.live.rtFrozen')
                      : rt.active ? t('dabanKing.live.rtWatching') : t('dabanKing.live.rtIdle')}
                </span>
                {rt.accelTheme ? <span className="text-cyan">{t('dabanKing.live.rtAccel', { theme: rt.accelTheme })}</span> : null}
              </div>
              <p className="mt-1 text-secondary-text">{rt.reason}</p>
              {rt.metrics ? (
                <p className="mt-1 tabular-nums text-muted-text">
                  {t('dabanKing.live.rtMetrics', {
                    zt: rt.metrics.ztCount ?? 0, zb: rt.metrics.zbCount ?? 0, dt: rt.metrics.dtCount ?? 0,
                    premium: formatPct(rt.metrics.yztPremium), zab: formatRate(rt.metrics.zabRate),
                  })}
                </p>
              ) : null}
            </div>
          ) : null}
          {prevStage ? (
            <details className="mt-2 rounded-xl border border-border/40 bg-elevated/20 p-2.5 text-[11px]">
              <summary className="cursor-pointer text-secondary-text">
                {t('dabanKing.live.gateBasis')} <span className="font-semibold text-foreground">{prevStage.label}</span>{' '}
                {t('dabanKing.hitRate', { hit: formatRate(prevStage.hitRate) })}
              </summary>
              {prevStage.override ? <p className="mt-1 text-warning">{prevStage.override}</p> : null}
              {prevMetrics ? (
                <p className="mt-1 tabular-nums text-secondary-text">
                  {t('dabanKing.live.gateMetrics', {
                    zt: prevMetrics.ztCount ?? 0, lianban: prevMetrics.lianbanCount ?? 0, dt: prevMetrics.dtCount ?? 0,
                    height: prevMetrics.maxHeight ?? 0, promotion: formatRate(prevMetrics.promotionRate),
                    premium: formatPct(prevMetrics.yztPremium), bigLoss: prevMetrics.bigLossCount ?? 0,
                  })}
                </p>
              ) : null}
              <ul className="mt-1.5 space-y-0.5">
                {gateConditions.map((item) => (
                  <li key={item.label} className="flex items-center gap-1.5 text-secondary-text">
                    <HitIcon hit={item.hit} />
                    <span>{item.label}</span>
                    <span className="tabular-nums text-foreground">{item.value ?? '—'}</span>
                    <span className="text-muted-text">{t('dabanKing.live.gateExpect', { threshold: item.threshold })}</span>
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </SectionCard>
      ) : null}
      {live.events?.recent?.length ? (
        <SectionCard title={t('dabanKing.live.eventsTitle')} subtitle={t('dabanKing.live.eventsSubtitle')}>
          <div className="mb-2 flex gap-3 text-xs text-secondary-text">
            <span className="text-danger">{t('dabanKing.live.evFirst', { n: live.events.counts?.first ?? 0 })}</span>
            <span className="text-cyan">{t('dabanKing.live.evBack', { n: live.events.counts?.back ?? 0 })}</span>
            <span className="text-success">{t('dabanKing.live.evFailed', { n: live.events.counts?.failed ?? 0 })}</span>
          </div>
          <ul className="flex flex-wrap gap-x-4 gap-y-1 text-[11px]">
            {live.events.recent.slice(0, 12).map((event) => (
              <li key={`${event.code}-${event.type}-${event.time}`} className="text-secondary-text">
                <span className="tabular-nums">{event.time.slice(0, 2)}:{event.time.slice(2, 4)}</span>{' '}
                <span className={event.type === 'LIMIT_FAILED' ? 'text-success' : event.type === 'LIMIT_BACK' ? 'text-cyan' : 'text-danger'}>
                  {event.name}
                </span>{' '}
                {event.type === 'LIMIT_FAILED' ? t('dabanKing.live.evOpen') : event.type === 'LIMIT_BACK' ? t('dabanKing.live.evBackOne') : t('dabanKing.live.evSeal')}
              </li>
            ))}
          </ul>
        </SectionCard>
      ) : null}

      {sortedThemes.length ? (
        <SectionCard title={t('dabanKing.live.hotTitle')} subtitle={t('dabanKing.live.hotSubtitle')}>
          <div className="flex flex-wrap gap-2" role="tablist" aria-label={t('dabanKing.live.hotTitle')}>
            <button
              type="button"
              role="tab"
              aria-selected={themeFilter === null}
              onClick={() => setThemeFilter(null)}
              className={cn('rounded-xl border px-3 py-2 text-left text-xs transition-colors',
                themeFilter === null ? 'border-cyan/60 bg-cyan/10 text-cyan' : 'border-border/50 bg-elevated/30 hover:bg-hover')}
            >
              {t('dabanKing.live.hotAll')}
            </button>
            {sortedThemes.map((theme, index) => {
              const hue = heatHue(index, sortedThemes.length);
              const active = themeFilter === theme.key;
              const inFocus = (live.focus?.groups ?? []).find((group) => group.themeKey === theme.key)?.items.length ?? 0;
              return (
                <button
                  key={theme.key}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  onClick={() => setThemeFilter(theme.key)}
                  style={{
                    borderColor: `hsl(${hue} 75% ${active ? 55 : 45}% / ${active ? 0.85 : 0.35})`,
                    backgroundColor: `hsl(${hue} 70% 50% / ${active ? 0.16 : 0.07})`,
                  }}
                  className="min-w-[9rem] rounded-xl border px-3 py-2 text-left transition-colors hover:brightness-110"
                >
                  <span className="flex items-center gap-1.5 text-xs font-medium text-foreground">
                    <span className="inline-flex h-4 w-4 items-center justify-center rounded-full text-[9px] text-white"
                          style={{ backgroundColor: `hsl(${hue} 75% 45%)` }} aria-hidden="true">
                      {theme.rank ?? index + 1}
                    </span>
                    {theme.name}
                  </span>
                  <span className="mt-0.5 block text-[10px] text-secondary-text">
                    {t('dabanKing.live.hotStats', { zt: theme.ztCount, days: theme.days, news: theme.newsCount ?? '—' })}
                  </span>
                  {theme.commonality && theme.commonality.kind !== 'unknown' ? (
                    <span className="block text-[10px] text-muted-text">{theme.commonality.label}</span>
                  ) : null}
                  {theme.catchUpEnd ? <span className="block text-[10px] text-warning">{t('dabanKing.live.catchUpEnd')}</span> : null}
                  <span className="block text-[10px] text-cyan">{t('dabanKing.live.hotFocus', { n: inFocus })}</span>
                </button>
              );
            })}
          </div>
        </SectionCard>
      ) : null}

      <section className="rounded-2xl border border-subtle bg-card/70 p-4">
        <div role="tablist" aria-label={t('dabanKing.live.radarTabs')} className="mb-3 flex flex-wrap gap-2">
          {(['focus', 'limit'] as const).map((key) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={tab === key}
              onClick={() => setTab(key)}
              className={cn('rounded-full border px-3 py-1 text-xs transition-colors',
                tab === key ? 'border-cyan/60 bg-cyan/10 font-medium text-cyan' : 'border-border/50 text-secondary-text hover:text-foreground')}
            >
              {key === 'focus' ? t('dabanKing.live.tabFocus', { n: focusCount }) : t('dabanKing.live.tabLimit', { n: live.limitBoard?.count ?? 0 })}
            </button>
          ))}
        </div>

        {tab === 'focus' ? (
          <div className="space-y-3">
            <p className="text-[11px] leading-relaxed text-muted-text">
              {t('dabanKing.live.focusHint', { threshold: rules?.threshold ?? 0, gap: rules?.gapMax ?? 6 })}
            </p>
            {pinned.length ? (
              <div className="space-y-2 rounded-xl border border-danger/40 bg-danger/5 p-2" data-testid="focus-pinned">
                <p className="text-xs font-semibold text-danger">{t('dabanKing.live.pinnedTitle', { n: pinned.length })}</p>
                {pinned.map((item) => (
                  <FocusCard key={`pin-${item.code}`} item={item} principles={principles} t={t} isNew={newCodes.has(item.code)} />
                ))}
              </div>
            ) : null}
            {focusGroups.length ? focusGroups.map((group) => (
              <div key={group.themeKey} className="space-y-2">
                <p className="text-xs font-semibold text-foreground">
                  {group.rank ? <span className="mr-1 text-muted-text">#{group.rank}</span> : null}
                  {group.themeName}
                  <span className="ml-1 font-normal text-secondary-text">{t('dabanKing.live.calls', { n: group.items.length })}</span>
                </p>
                {group.items.map((item) => (
                  <FocusCard key={item.code} item={item} principles={principles} t={t} isNew={newCodes.has(item.code)} />
                ))}
              </div>
            )) : <p className="text-xs text-secondary-text">{t('dabanKing.live.noFocus')}</p>}
            {watching.length ? (
              <details className="rounded-xl border border-border/50 bg-elevated/20 px-3 py-2">
                <summary className="cursor-pointer text-xs text-cyan">{t('dabanKing.live.watchingTitle', { n: watching.length })}</summary>
                <ul className="mt-2 space-y-1">
                  {watching.map((item) => (
                    <li key={item.code} className="flex flex-wrap items-center gap-2 text-[11px]">
                      <span className="w-44"><StockLink code={item.code} name={item.name} /></span>
                      <span className="text-muted-text">{item.themeName}</span>
                      {item.score !== null ? <span className="tabular-nums text-foreground">{t('dabanKing.live.score', { score: formatSigned(item.score) })}</span> : null}
                      {item.gapPct !== null ? <span className="tabular-nums text-secondary-text">{t('dabanKing.live.gap', { value: item.gapPct.toFixed(1) })}</span> : null}
                      <span className="text-secondary-text">{item.reason}</span>
                    </li>
                  ))}
                </ul>
              </details>
            ) : null}
          </div>
        ) : (
          <div className="space-y-3">
            <p className="text-[11px] leading-relaxed text-muted-text">{t('dabanKing.live.limitHint')}</p>
            {limitGroups.length ? limitGroups.map((group) => (
              <div key={group.themeKey || 'none'} className="space-y-2">
                <p className="text-xs font-semibold text-foreground">
                  {group.rank ? <span className="mr-1 text-muted-text">#{group.rank}</span> : null}
                  {group.themeName}
                  <span className="ml-1 font-normal text-secondary-text">{t('dabanKing.live.calls', { n: group.count })}</span>
                </p>
                {group.items.map((item) => <LimitBoardRow key={item.code} item={item} t={t} />)}
              </div>
            )) : <p className="text-xs text-secondary-text">{t('dabanKing.live.noLimit')}</p>}
          </div>
        )}
      </section>

      {rules?.items?.length ? (
        <details className="rounded-2xl border border-border/50 bg-card/60 px-4 py-3">
          <summary className="cursor-pointer text-xs text-cyan">
            {t('dabanKing.live.rulesTitle', { lo: rules.range?.[0] ?? 0, hi: formatSigned(rules.range?.[1] ?? 0) })}
          </summary>
          <p className="mt-2 text-[11px] leading-relaxed text-secondary-text">
            {t('dabanKing.live.rulesHint', {
              n: rules.baseline?.n ?? 0, days: rules.baseline?.days ?? 0,
              h1: formatPct(rules.baseline?.h1Mean, 2), win: formatRate(rules.baseline?.h1Win),
            })}
          </p>
          <ul className="mt-2 space-y-0.5">
            {rules.items.map((item) => <ScoreItemLine key={item.key} item={item} principles={principles} t={t} />)}
          </ul>
          {rules.observe?.length ? (
            <div className="mt-2 border-t border-border/40 pt-2">
              <p className="text-[11px] font-medium text-purple">{t('dabanKing.live.observeTitle')}</p>
              <p className="text-[11px] text-secondary-text">{rules.observeText}</p>
              <ul className="mt-1 space-y-0.5">
                {rules.observe.map((item) => (
                  <li key={item.key} className="text-[11px] text-secondary-text">
                    · {item.label}：{t('dabanKing.live.observeWouldBe', { points: item.wouldBe, n: item.n, days: item.days, excess: formatSigned(item.excess) })}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          <p className="mt-2 text-[11px] text-muted-text">
            <Badge variant="default">{t('dabanKing.live.supplement')}</Badge> {t('dabanKing.live.supplementHint')}{' '}
            <PrincipleTags ids={['K32']} principles={principles} />
          </p>
        </details>
      ) : null}

      <p className="pb-4 text-center text-[11px] text-secondary-text">{t('dabanKing.live.footer')}</p>
    </div>
  );
};
