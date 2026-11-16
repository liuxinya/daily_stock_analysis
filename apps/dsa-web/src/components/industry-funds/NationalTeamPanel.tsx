import type React from 'react';
import { SectionCard, StatCard } from '../common';
import { HelpTip } from './HelpTip';
import type { UiTextKey } from '../../i18n/uiText';
import type { NationalTeamDay, NationalTeamFlow } from '../../types/industryFunds';
import { cn } from '../../utils/cn';
import { formatSignedYi, formatYi } from './format';

type Translate = (key: UiTextKey, params?: Record<string, string | number>) => string;

const TONE = (value: number | null | undefined) =>
  value === null || value === undefined || value === 0 ? 'text-foreground' : value > 0 ? 'text-danger' : 'text-success';

/**
 * 「汪汪队」tab：宽基 ETF 的份额净申购 + 成交额倍数。
 *
 * 份额是交易所披露的，方向可验证；成交额放大只能说明「有人在买」，分不清申赎 —— 两个数并列展示，不合成分数。
 * 沪市可回溯两周；深市接口只给当日，页面如实标注「从今天起积累」。
 */
export const NationalTeamPanel: React.FC<{
  flow: NationalTeamFlow;
  date: string;
  t: Translate;
}> = ({ flow, date, t }) => {
  const day: NationalTeamDay | undefined = flow.days.find((item) => item.date === date);
  const rows = day?.etfs ?? [];
  const missing = day?.sseMissing ?? false;

  return (
    <div className="space-y-4">
      <div id="if-nt-summary" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label={t('industryFunds.flow.nt.netShares')}
          value={missing ? '—' : `${formatSignedYi(day?.netShares ?? null)} 亿份`}
          hint={missing ? t('industryFunds.flow.nt.notPublished') : t('industryFunds.flow.nt.netSharesHint')}
          tone={((day?.netShares ?? 0) >= 0 ? 'danger' : 'success') as 'danger' | 'success'}
        />
        <StatCard
          label={t('industryFunds.flow.nt.netAmount')}
          value={missing ? '—' : `${formatSignedYi(day?.netAmount ?? null)} 亿元`}
          hint={t('industryFunds.flow.nt.netAmountHint')}
        />
        <StatCard
          label={t('industryFunds.flow.nt.turnover')}
          value={day?.turnoverRatio === null || day?.turnoverRatio === undefined ? '—' : `${day.turnoverRatio.toFixed(2)}×`}
          hint={t('industryFunds.flow.nt.turnoverHint', { amount: formatYi(day?.turnover ?? null) })}
        />
        <StatCard
          label={t('industryFunds.flow.nt.streak')}
          value={`${flow.inflowStreak} ${t('industryFunds.flow.days')}`}
          hint={t('industryFunds.flow.nt.streakHint')}
        />
      </div>

      <div id="if-nt-etfs">
      <SectionCard title={t('industryFunds.flow.nt.etfTitle', { date })} subtitle={t('industryFunds.flow.nt.etfHint')}>
        {missing ? (
          <p className="text-xs text-secondary-text">{t('industryFunds.flow.nt.notPublished')}</p>
        ) : rows.length ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-left text-xs">
              <thead className="text-[11px] text-secondary-text">
                <tr className="border-b border-border/50">
                  <th className="py-1.5 pr-3 font-normal">{t('industryFunds.flow.col.etf')}</th>
                  <th className="py-1.5 pr-3 text-right font-normal">{t('industryFunds.flow.nt.colShares')}<HelpTip text={t('industryFunds.flow.nt.help.shares')} /></th>
                  <th className="py-1.5 pr-3 text-right font-normal">{t('industryFunds.flow.nt.colDelta')}<HelpTip text={t('industryFunds.flow.nt.help.delta')} /></th>
                  <th className="py-1.5 pr-3 text-right font-normal">{t('industryFunds.flow.nt.colAmount')}<HelpTip text={t('industryFunds.flow.nt.help.amount')} /></th>
                  <th className="py-1.5 pr-3 text-right font-normal">{t('industryFunds.flow.nt.colTurnover')}<HelpTip text={t('industryFunds.flow.nt.help.turnover')} /></th>
                  <th className="py-1.5 text-right font-normal">{t('industryFunds.flow.nt.colRatio')}<HelpTip text={t('industryFunds.flow.nt.help.ratio')} /></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.code} className="border-b border-border/30" data-testid={`nt-row-${row.code}`}>
                    <td className="py-1.5 pr-3">
                      <span className="font-medium text-foreground">{row.name}</span>
                      <span className="ml-1.5 text-muted-text">{row.code}</span>
                    </td>
                    <td className="py-1.5 pr-3 text-right tabular-nums text-secondary-text">{formatYi(row.shares, 2)}</td>
                    <td className={cn('py-1.5 pr-3 text-right tabular-nums', TONE(row.deltaShares))}>{formatSignedYi(row.deltaShares)}</td>
                    <td className={cn('py-1.5 pr-3 text-right tabular-nums', TONE(row.deltaAmount))}>{formatSignedYi(row.deltaAmount)}</td>
                    <td className="py-1.5 pr-3 text-right tabular-nums text-secondary-text">{formatYi(row.turnover)}</td>
                    <td className="py-1.5 text-right tabular-nums text-secondary-text">
                      {row.turnoverRatio === null ? '—' : `${row.turnoverRatio.toFixed(2)}×`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-xs text-secondary-text">{t('industryFunds.flow.nt.empty')}</p>
        )}
        <p className="mt-2 text-[11px] text-muted-text">
          {t('industryFunds.flow.nt.unitNote')}
          {flow.coverage?.sse ? ` · ${flow.coverage.sse}` : ''}
          {flow.coverage?.szse ? ` · ${flow.coverage.szse}` : ''}
        </p>
        {flow.degraded ? <p className="mt-1 text-[11px] text-warning">{flow.degradedReason}</p> : null}
      </SectionCard>
      </div>

      <div id="if-nt-trend">
      <SectionCard title={t('industryFunds.flow.nt.trendTitle')} subtitle={t('industryFunds.flow.twoWeeksHint')}>
        <ul className="space-y-1">
          {[...flow.days].reverse().map((item) => {   /* 最近的在最上面 */
            const selected = item.date === date;
            const max = Math.max(...flow.days.map((d) => Math.abs(d.netShares ?? 0)), 1);
            const width = Math.round((Math.abs(item.netShares ?? 0) / max) * 100);
            return (
              <li key={item.date} className={cn('flex items-center gap-3 rounded-lg px-1.5 py-1 text-[11px]', selected && 'bg-elevated/50')}>
                <span className="w-16 shrink-0 tabular-nums text-secondary-text">{item.date.slice(5)}</span>
                <span className={cn('w-24 shrink-0 text-right tabular-nums', TONE(item.netShares))}>
                  {item.netShares === null ? t('industryFunds.flow.pending') : `${formatSignedYi(item.netShares)} 亿份`}
                </span>
                <span className="flex min-w-0 flex-1 items-center gap-1">
                  <span className="h-1.5 flex-1 rounded-full bg-elevated/60">
                    <span
                      className={cn('block h-1.5 rounded-full', (item.netShares ?? 0) >= 0 ? 'bg-danger/60' : 'bg-success/60')}
                      style={{ width: `${width}%` }}
                    />
                  </span>
                  <span className="w-20 shrink-0 text-right text-muted-text">
                    {item.turnoverRatio === null ? '—' : `${item.turnoverRatio.toFixed(2)}×`}
                  </span>
                </span>
              </li>
            );
          })}
        </ul>
        {flow.szse?.etfs.length ? (
          <p className="mt-2 text-[11px] text-muted-text">
            {t('industryFunds.flow.nt.szseLine', { date: flow.szse.date ?? '—', n: flow.szse.etfs.length })}
            {flow.szseNote ? ` · ${flow.szseNote}` : ''}
          </p>
        ) : null}
        <p className="mt-1 text-[11px] text-muted-text">{t('industryFunds.flow.nt.coverage')}</p>
      </SectionCard>
      </div>
    </div>
  );
};
