import type React from 'react';
import { useState } from 'react';
import { Badge, Drawer, SectionCard, StatCard } from '../common';
import { HelpTip } from './HelpTip';
import type { UiTextKey } from '../../i18n/uiText';
import type { InstitutionDay, InstitutionFlow, InstitutionStock } from '../../types/industryFunds';
import { cn } from '../../utils/cn';
import { formatPct, formatSignedYi, formatYi } from './format';

type Translate = (key: UiTextKey, params?: Record<string, string | number>) => string;

const TONE = (value: number | null | undefined) =>
  value === null || value === undefined || value === 0 ? 'text-foreground' : value > 0 ? 'text-danger' : 'text-success';

/**
 * 「机构」tab：龙虎榜机构专用席位的日频净买。
 *
 * 上方是当日汇总 + 个股表（点一行打开抽屉看完整字段），下方是这两周的逐日汇总，选中的那天高亮。
 * 机构席位是交易所披露的，口径最硬；但它只覆盖上榜的票，是「下限」不是全量 —— 页面底部如实注明。
 */
export const InstitutionFlowPanel: React.FC<{
  flow: InstitutionFlow;
  date: string;
  t: Translate;
}> = ({ flow, date, t }) => {
  const [active, setActive] = useState<InstitutionStock | null>(null);
  const day: InstitutionDay | undefined = flow.days.find((item) => item.date === date);
  const rows = day?.stocks ?? [];
  const pending = day?.pending ?? false;
  const positive = rows.filter((row) => (row.net ?? 0) > 0).length;

  return (
    <div className="space-y-4">
      <div id="if-inst-summary" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label={t('industryFunds.flow.inst.net')}
          value={formatSignedYi(day?.net ?? null)}
          hint={t('industryFunds.flow.unitYi')}
          tone={((day?.net ?? 0) >= 0 ? 'danger' : 'success') as 'danger' | 'success'}
        />
        <StatCard label={t('industryFunds.flow.inst.count')} value={pending ? '—' : String(day?.count ?? 0)} />
        <StatCard
          label={t('industryFunds.flow.inst.positive')}
          value={pending ? '—' : `${positive} / ${rows.length}`}
          hint={t('industryFunds.flow.inst.positiveHint')}
        />
        <StatCard
          label={t('industryFunds.flow.inst.buySell')}
          value={`${formatYi(day?.buy ?? null)} / ${formatYi(day?.sell ?? null)}`}
          hint={t('industryFunds.flow.unitYi')}
        />
      </div>

      <div id="if-inst-stocks">
      <SectionCard title={t('industryFunds.flow.inst.stocksTitle', { date })} subtitle={t('industryFunds.flow.inst.stocksHint')}>
        {pending ? (
          <p className="text-xs text-secondary-text">{t('industryFunds.flow.inst.pending')}</p>
        ) : rows.length ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[680px] text-left text-xs">
              <thead className="text-[11px] text-secondary-text">
                <tr className="border-b border-border/50">
                  <th className="py-1.5 pr-3 font-normal">{t('industryFunds.flow.col.stock')}</th>
                  <th className="py-1.5 pr-3 text-right font-normal">{t('industryFunds.flow.inst.buyShort')}<HelpTip text={t('industryFunds.flow.inst.help.buy')} /></th>
                  <th className="py-1.5 pr-3 text-right font-normal">{t('industryFunds.flow.inst.sellShort')}<HelpTip text={t('industryFunds.flow.inst.help.sell')} /></th>
                  <th className="py-1.5 pr-3 text-right font-normal">{t('industryFunds.flow.inst.netShort')}<HelpTip text={t('industryFunds.flow.inst.help.net')} /></th>
                  <th className="py-1.5 pr-3 text-right font-normal">{t('industryFunds.flow.inst.ratio')}<HelpTip text={t('industryFunds.flow.inst.help.ratio')} /></th>
                  <th className="py-1.5 pr-3 text-right font-normal">{t('industryFunds.flow.inst.change')}<HelpTip text={t('industryFunds.flow.inst.help.change')} /></th>
                  <th className="py-1.5 font-normal">{t('industryFunds.flow.col.reason')}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr
                    key={`${row.code}-${row.reason}`}
                    role="button"
                    tabIndex={0}
                    data-testid={`inst-row-${row.code}`}
                    onClick={() => setActive(row)}
                    onKeyDown={(event) => { if (event.key === 'Enter') setActive(row); }}
                    className="cursor-pointer border-b border-border/30 hover:bg-hover/40"
                  >
                    <td className="py-1.5 pr-3">
                      <span className="font-medium text-foreground">{row.name}</span>
                      <span className="ml-1.5 text-muted-text">{row.code}</span>
                    </td>
                    <td className="py-1.5 pr-3 text-right tabular-nums text-secondary-text">{formatYi(row.buy)}</td>
                    <td className="py-1.5 pr-3 text-right tabular-nums text-secondary-text">{formatYi(row.sell)}</td>
                    <td className={cn('py-1.5 pr-3 text-right font-medium tabular-nums', TONE(row.net))}>{formatSignedYi(row.net)}</td>
                    <td className="py-1.5 pr-3 text-right tabular-nums text-secondary-text">{formatPct(row.netRatio, 2)}</td>
                    <td className={cn('py-1.5 pr-3 text-right tabular-nums', TONE(row.changePct))}>{formatPct(row.changePct, 2, true)}</td>
                    <td className="max-w-[18rem] truncate py-1.5 text-muted-text" title={row.reason}>{row.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-xs text-secondary-text">{t('industryFunds.flow.inst.empty')}</p>
        )}
      </SectionCard>
      </div>

      <div id="if-inst-trend">
      <SectionCard title={t('industryFunds.flow.inst.trendTitle')} subtitle={t('industryFunds.flow.twoWeeksHint')}>
        <ul className="space-y-1">
          {[...flow.days].reverse().map((item) => {   /* 最近的在最上面 */
            const selected = item.date === date;
            const max = Math.max(...flow.days.map((d) => Math.abs(d.net ?? 0)), 1);
            const width = Math.round((Math.abs(item.net ?? 0) / max) * 100);
            return (
              <li key={item.date} className={cn('flex items-center gap-3 rounded-lg px-1.5 py-1 text-[11px]', selected && 'bg-elevated/50')}>
                <span className="w-16 shrink-0 tabular-nums text-secondary-text">{item.date.slice(5)}</span>
                <span className={cn('w-20 shrink-0 text-right tabular-nums', TONE(item.net))}>{formatSignedYi(item.net)}</span>
                <span className="flex min-w-0 flex-1 items-center gap-1">
                  <span className="h-1.5 flex-1 rounded-full bg-elevated/60">
                    <span
                      className={cn('block h-1.5 rounded-full', (item.net ?? 0) >= 0 ? 'bg-danger/60' : 'bg-success/60')}
                      style={{ width: `${width}%` }}
                    />
                  </span>
                  <span className="w-16 shrink-0 text-right text-muted-text">
                    {item.pending ? t('industryFunds.flow.pending') : t('industryFunds.flow.inst.countShort', { n: item.count })}
                  </span>
                </span>
              </li>
            );
          })}
        </ul>
        <p className="mt-2 text-[11px] text-muted-text">{t('industryFunds.flow.inst.coverage')}</p>
        {flow.degraded ? <p className="mt-1 text-[11px] text-warning">{flow.degradedReason}</p> : null}
      </SectionCard>
      </div>

      <Drawer isOpen={active !== null} onClose={() => setActive(null)} title={active ? `${active.name} ${active.code}` : ''}>
        {active ? (
          <dl className="space-y-2 text-sm">
            {([
              ['industryFunds.flow.inst.drawer.date', day?.date ?? '—'],
              ['industryFunds.flow.inst.drawer.close', active.close === null ? '—' : active.close.toFixed(2)],
              ['industryFunds.flow.inst.drawer.change', formatPct(active.changePct, 2, true)],
              ['industryFunds.flow.inst.buyShort', `${formatYi(active.buy)} 亿`],
              ['industryFunds.flow.inst.sellShort', `${formatYi(active.sell)} 亿`],
              ['industryFunds.flow.inst.netShort', `${formatSignedYi(active.net)} 亿`],
              ['industryFunds.flow.inst.ratio', formatPct(active.netRatio, 2)],
              ['industryFunds.flow.inst.drawer.turnover', formatPct(active.turnoverRate, 2)],
              ['industryFunds.flow.inst.drawer.seats',
                t('industryFunds.flow.inst.drawer.seatsValue', { buy: active.buyerCount ?? 0, sell: active.sellerCount ?? 0 })],
              ['industryFunds.flow.col.reason', active.reason || '—'],
            ] as [UiTextKey, string][]).map(([key, value]) => (
              <div key={key} className="flex justify-between gap-4 border-b border-border/30 pb-1.5">
                <dt className="text-secondary-text">{t(key)}</dt>
                <dd className="text-right text-foreground">{value}</dd>
              </div>
            ))}
          </dl>
        ) : null}
      </Drawer>
    </div>
  );
};

export const InstitutionBadge: React.FC<{ pending?: boolean; t: Translate }> = ({ pending, t }) => (
  <Badge variant={pending ? 'warning' : 'info'}>{pending ? t('industryFunds.flow.pending') : t('industryFunds.flow.available')}</Badge>
);
