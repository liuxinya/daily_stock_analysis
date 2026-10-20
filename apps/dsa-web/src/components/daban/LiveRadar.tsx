import type React from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Activity, Radio, RefreshCw } from 'lucide-react';
import { Badge, EmptyState, InlineAlert, SectionCard } from '../common';
import type { UiTextKey } from '../../i18n/uiText';
import type { DabanLive, DabanLiveHit, DabanPrinciple, DabanStyle } from '../../types/daban';
import { cn } from '../../utils/cn';
import { ACTION_VARIANTS, changeTone, formatPct, formatRate, STYLE_TEXT } from './format';
import { CitedLine, StockLink } from './shared';

type Translate = (key: UiTextKey, params?: Record<string, string | number>) => string;

const DEFAULT_POLL_SEC = 10;

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

/** 每一派一行；事实（时间、开板次数、同伴数、封单）全部摆出来，不打分、不排序。 */
const HitRow: React.FC<{
  hit: DabanLiveHit;
  principles: Record<string, DabanPrinciple>;
  t: Translate;
  isNew: boolean;
}> = ({ hit, principles, t, isNew }) => (
  <div
    className={cn(
      'rounded-xl border px-3 py-2',
      hit.action === 'actionable' ? 'border-danger/40 bg-danger/5' : 'border-border/50 bg-elevated/30'
    )}
  >
    <div className="flex flex-wrap items-center gap-2">
      <Badge variant={ACTION_VARIANTS[hit.action]}>{hit.actionLabel}</Badge>
      <StockLink code={hit.code} name={hit.name} />
      {isNew ? <Badge variant="warning">{t('dabanKing.live.new')}</Badge> : null}
      <span className="text-[11px] tabular-nums text-secondary-text">
        {t('dabanKing.live.firstTime')} {hit.firstTime}
      </span>
      {hit.openNum > 0 ? (
        <span className="text-[11px] text-warning">{t('dabanKing.live.opened', { n: hit.openNum })}</span>
      ) : null}
      {hit.peers > 0 ? (
        <span className="text-[11px] text-cyan">{t('dabanKing.live.peers', { n: hit.peers })}</span>
      ) : (
        <span className="text-[11px] text-muted-text">{t('dabanKing.live.noPeers')}</span>
      )}
      {hit.status === 'opened' ? (
        <Badge variant="warning">{t('dabanKing.live.brokeNow')}</Badge>
      ) : hit.sealed === true ? (
        <Badge variant="success">{t('dabanKing.live.sealed')}</Badge>
      ) : hit.sealed === false ? (
        <Badge variant="warning">{t('dabanKing.live.unsealed')}</Badge>
      ) : null}
      {hit.slot ? <span className="text-[11px] text-foreground">{t('dabanKing.live.slot', { n: hit.slot })}</span> : null}
      {hit.lockedAt ? (
        <span className="text-[11px] text-muted-text">{t('dabanKing.live.lockedAt', { time: hit.lockedAt })}</span>
      ) : null}
      {hit.status === 'missing' ? <Badge variant="default">{t('dabanKing.live.missing')}</Badge> : null}
      {hit.positionPct > 0 ? (
        <span className="text-[11px] text-secondary-text">{t('dabanKing.position', { value: hit.positionPct })}</span>
      ) : null}
      {hit.limitPrice ? (
        <span className="text-[11px] tabular-nums text-secondary-text">{t('dabanKing.live.limitPrice', { value: hit.limitPrice.toFixed(2) })}</span>
      ) : null}
    </div>
    <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-secondary-text">
      {hit.theme ? (
        <span>
          {hit.theme}
          {hit.themeRank ? ` #${hit.themeRank}` : ''}
          {hit.themeMain ? ` · ${t('dabanKing.mainline')}` : ''}
        </span>
      ) : (
        <span className="text-muted-text">{t('dabanKing.live.noTheme')}</span>
      )}
      {hit.sealAmount ? <span>{t('dabanKing.live.sealAmount', { value: (hit.sealAmount / 1e8).toFixed(2) })}</span> : null}
      {hit.sealRatio !== null ? <span>{t('dabanKing.live.sealRatio', { value: formatRate(hit.sealRatio, 2) })}</span> : null}
      {hit.turnoverRate !== null ? <span>{t('dabanKing.live.turnover', { value: hit.turnoverRate.toFixed(1) })}</span> : null}
      {hit.circMv ? <span>{t('dabanKing.live.circMv', { value: (hit.circMv / 1e8).toFixed(0) })}</span> : null}
      {hit.limitUpType ? <span>{hit.limitUpType}</span> : null}
      {hit.highDays ? <span>{hit.highDays}</span> : null}
      {hit.changePct !== null ? (
        <span className={changeTone(hit.changePct)}>{formatPct(hit.changePct)}</span>
      ) : null}
    </div>
    {hit.reason ? <p className="mt-0.5 text-[11px] text-muted-text">{t('dabanKing.live.reason')}：{hit.reason}</p> : null}
    <ul className="mt-1 space-y-0.5">
      {hit.reasons.map((item) => (
        <CitedLine key={item.text} item={item} principles={principles} />
      ))}
    </ul>
    {hit.notes.length ? <p className="mt-1 text-[11px] text-warning">{hit.notes.join('；')}</p> : null}
  </div>
);

/**
 * 盘中首板雷达。
 *
 * 只回答「按前一交易日的周期，现在这个板两派能不能打」，以及几个可以核对的事实。
 * **不打分**：回测显示封板时刻可知的信息没有预测力，打分只会制造虚假的确定感。
 */
export const LiveRadar: React.FC<{
  principles: Record<string, DabanPrinciple>;
  styleLabels: Record<string, string>;
  t: Translate;
}> = ({ principles, styleLabels, t }) => {
  const [live, setLive] = useState<DabanLive | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [auto, setAuto] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<string>('');
  const [newCodes, setNewCodes] = useState<Set<string>>(new Set());
  const seen = useRef<Set<string>>(new Set());
  const tickRef = useRef(0);

  const refresh = useCallback((force: boolean) => {
    void fetchLive(force)
      .then((payload) => {
        const fresh = new Set<string>();
        payload.hits.forEach((hit) => {
          const key = `${hit.style}:${hit.code}`;
          if (!seen.current.has(key)) {
            seen.current.add(key);
            if (tickRef.current > 1) fresh.add(key);
          }
        });
        setNewCodes(fresh);
        setLive(payload);
        setError(null);
        setLastUpdated(payload.generatedAt || '');
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

  const styles: DabanStyle[] = ['chen', 'kobe'];
  const byStyle = (style: DabanStyle) => live.hits.filter((hit) => hit.style === style);
  const refreshDisabled = !live.previous?.available;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-border/50 bg-card/70 px-4 py-3 text-xs">
        <span className={cn('inline-flex items-center gap-1.5', live.trading ? 'text-danger' : 'text-secondary-text')}>
          <Radio className={cn('h-3.5 w-3.5', live.trading && 'animate-pulse')} aria-hidden="true" />
          {live.trading ? t('dabanKing.live.trading') : t('dabanKing.live.closed')}
        </span>
        <span className="text-secondary-text">
          {t('dabanKing.live.updated', { time: lastUpdated ? `${lastUpdated.slice(0, 2)}:${lastUpdated.slice(2, 4)}:${lastUpdated.slice(4, 6)}` : '—' })}
        </span>
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
        <span className="text-muted-text">{t('dabanKing.live.takeMs', { ms: live.durationMs })}</span>
      </div>

      {live.stale ? <InlineAlert variant="warning" message={t('dabanKing.live.stale')} /> : null}
      {live.replay && !live.stale ? (
        <InlineAlert variant="info" message={t('dabanKing.live.replay', { date: live.tradeDate })} />
      ) : null}
      {live.degradedReasons.length ? (
        <InlineAlert
          variant="info"
          message={<ul className="list-disc space-y-0.5 pl-4">{live.degradedReasons.map((r) => <li key={r}>{r}</li>)}</ul>}
        />
      ) : null}
      {!live.previous?.available ? (
        <InlineAlert variant="warning" message={live.previous?.reason || t('dabanKing.live.needReview')} />
      ) : null}

      <SectionCard title={t('dabanKing.live.gateTitle')} subtitle={t('dabanKing.live.gateSubtitle')}>
        <div className="grid gap-3 sm:grid-cols-2">
          {styles.map((style) => {
            const action = style === 'chen' ? live.gateLabels.chenAction : live.gateLabels.kobeAction;
            const phase = style === 'chen' ? live.gateLabels.chen : live.gateLabels.kobe;
            return (
              <div key={style} className="rounded-xl border border-border/50 bg-elevated/30 p-3">
                <p className={cn('text-sm font-semibold', STYLE_TEXT[style])}>{styleLabels[style]}</p>
                <p className="mt-1 text-xs text-secondary-text">
                  {t('dabanKing.live.prevPhase', { phase: phase || '—' })}
                </p>
                <p className="mt-1 text-xs text-foreground">
                  {action
                    ? t('dabanKing.live.allowed', { action: action === 'actionable' ? t('dabanKing.live.actActionable') : action === 'trial' ? t('dabanKing.live.actTrial') : t('dabanKing.live.actWatch') })
                    : t('dabanKing.live.forbidden')}
                </p>
              </div>
            );
          })}
        </div>
        {live.gateLabels.tradeDate ? (
          <p className="mt-2 text-[11px] text-secondary-text">
            {t('dabanKing.live.gateSource', {
              date: live.gateLabels.tradeDate,
              source: live.gateLabels.source === 'review' ? t('dabanKing.live.sourceReview') : t('dabanKing.live.sourceComputed'),
            })}
          </p>
        ) : null}
        <p className="mt-2 text-[11px] text-muted-text">{t('dabanKing.live.note')}</p>
        <div className="mt-2 flex flex-wrap gap-3 text-xs text-secondary-text">
          <span>{t('dabanKing.live.statLimitUp', { n: live.stats.limitUp ?? 0 })}</span>
          <span>{t('dabanKing.live.statFirst', { n: live.stats.firstBoards ?? 0 })}</span>
          <span>{t('dabanKing.live.statTouches', { n: live.stats.firstTouches ?? 0 })}</span>
          <span>{t('dabanKing.live.statBroken', { n: live.stats.broken ?? 0 })}</span>
          <span className="text-danger">{t('dabanKing.live.statActionable', { n: live.stats.actionable ?? 0 })}</span>
          <span className="text-warning">{t('dabanKing.live.statTrial', { n: live.stats.trial ?? 0 })}</span>
        </div>
      </SectionCard>

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

      <div className="grid gap-4 lg:grid-cols-2">
        {styles.map((style) => {
          const rows = byStyle(style);
          const lane = live.lanes?.[style];
          const calls = rows.filter((hit) => hit.action !== 'watch');
          const watch = rows.filter((hit) => hit.action === 'watch');
          return (
            <section key={style} aria-label={styleLabels[style]} className="rounded-2xl border border-subtle bg-card/70 p-4">
              <h2 className={cn('mb-2 flex items-center gap-2 text-lg font-semibold', STYLE_TEXT[style])}>
                <Activity className="h-4 w-4" aria-hidden="true" />
                {styleLabels[style]}
                <span className="text-xs font-normal text-secondary-text">
                  {lane
                    ? t('dabanKing.live.laneUsage', { picks: lane.picks, max: lane.maxPicks, locked: lane.locked ?? 0, used: lane.used, cap: lane.cap })
                    : t('dabanKing.live.calls', { n: rows.length })}
                </span>
              </h2>
              {rows.length ? (
                <div className="space-y-2">
                  {calls.map((hit) => (
                    <HitRow
                      key={`${hit.style}-${hit.code}`}
                      hit={hit}
                      principles={principles}
                      t={t}
                      isNew={newCodes.has(`${hit.style}:${hit.code}`)}
                    />
                  ))}
                  {watch.length ? (
                    <details className="rounded-xl border border-border/40 bg-elevated/20 px-3 py-2">
                      <summary className="cursor-pointer text-[11px] text-cyan">
                        {t('dabanKing.live.watchOnly', { n: watch.length })}
                      </summary>
                      <div className="mt-2 space-y-2">
                        {watch.map((hit) => (
                          <HitRow
                            key={`${hit.style}-w-${hit.code}`}
                            hit={hit}
                            principles={principles}
                            t={t}
                            isNew={false}
                          />
                        ))}
                      </div>
                    </details>
                  ) : null}
                  {!calls.length ? (
                    <p className="text-xs text-secondary-text">{t('dabanKing.live.onlyWatch')}</p>
                  ) : null}
                </div>
              ) : (
                <p className="text-xs text-secondary-text">{t('dabanKing.live.noCalls')}</p>
              )}
              {lane?.sellRules?.length ? (
                <div className="mt-3 border-t border-border/40 pt-2">
                  <p className="text-[11px] font-medium text-foreground">{t('dabanKing.sellRules')}</p>
                  <ul className="mt-1 space-y-1">
                    {lane.sellRules.map((item) => (
                      <CitedLine key={item.text} item={item} principles={principles} />
                    ))}
                  </ul>
                </div>
              ) : null}
            </section>
          );
        })}
      </div>

      {live.blocked.length ? (
        <details className="rounded-2xl border border-border/50 bg-card/60 px-4 py-3">
          <summary className="cursor-pointer text-xs text-cyan">
            {t('dabanKing.live.blockedTitle', { n: live.blocked.length })}
          </summary>
          <ul className="mt-2 space-y-1">
            {live.blocked.map((item) => (
              <li key={`${item.style}-${item.code}`} className="flex flex-wrap items-center gap-2 text-[11px]">
                <span className="w-44">
                  <StockLink code={item.code} name={item.name} />
                </span>
                <span className={STYLE_TEXT[item.style]}>{styleLabels[item.style]}</span>
                <span className="text-secondary-text">{item.reason}</span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      <p className="pb-4 text-center text-[11px] text-secondary-text">
        {refreshDisabled ? '' : t('dabanKing.live.footer')}
      </p>
    </div>
  );
};
