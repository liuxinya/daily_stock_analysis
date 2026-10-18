import type React from 'react';
import { useMemo, useState } from 'react';
import { ArrowDownUp, Info } from 'lucide-react';
import type { UiTextKey } from '../../i18n/uiText';
import type { IndustryRow } from '../../types/industryFunds';
import { cn } from '../../utils/cn';
import { Badge } from '../common/Badge';
import { SectionCard } from '../common/SectionCard';
import { Tooltip } from '../common/Tooltip';
import { formatPct, formatSignedYi, formatYi, inOutTone } from './format';

type Translate = (key: UiTextKey) => string;

type SortKey = 'net' | 'net5D' | 'changePct' | 'name';

type Props = {
  industries: IndustryRow[];
  disclaimer: string;
  sourceNote: string;
  t: Translate;
};

const INITIAL_ROWS = 15;

/**
 * 第一段：行业资金流向。
 *
 * 表头常驻一句口径说明 —— 「主力资金」是按单笔成交金额分档推断的统计值，不是账户身份。
 * 这句话必须留在最显眼的位置，否则用户很容易把它当成真实持仓数据。
 */
export const FundFlowSection: React.FC<Props> = ({ industries, disclaimer, sourceNote, t }) => {
  const [sortKey, setSortKey] = useState<SortKey>('net');
  const [descending, setDescending] = useState(true);
  const [showAll, setShowAll] = useState(false);

  const sorted = useMemo(() => {
    const rows = [...industries];
    rows.sort((a, b) => {
      if (sortKey === 'name') {
        const result = a.name.localeCompare(b.name, 'zh-CN');
        return descending ? -result : result;
      }
      const left = (a[sortKey] ?? Number.NEGATIVE_INFINITY) as number;
      const right = (b[sortKey] ?? Number.NEGATIVE_INFINITY) as number;
      return descending ? right - left : left - right;
    });
    return rows;
  }, [industries, sortKey, descending]);

  const visible = showAll ? sorted : sorted.slice(0, INITIAL_ROWS);
  const inflowCount = industries.filter((row) => (row.net ?? 0) > 0).length;

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) {
      setDescending(!descending);
      return;
    }
    setSortKey(key);
    setDescending(key !== 'name');
  };

  return (
    <SectionCard
      title={t('industryFunds.sectionFundFlow')}
      subtitle={t('industryFunds.sectionFundFlowHint')}
      actions={
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={inflowCount > industries.length / 2 ? 'success' : 'default'} size="sm">
            {t('industryFunds.inflowSummary')
              .replace('{inflow}', String(inflowCount))
              .replace('{total}', String(industries.length))}
          </Badge>
        </div>
      }
    >
      <div className="mb-3 flex items-start gap-2 rounded-xl border border-cyan/20 bg-cyan/5 p-3 text-xs text-secondary-text">
        <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-cyan" aria-hidden="true" />
        <div>
          <p>{disclaimer}</p>
          {sourceNote ? <p className="mt-1">{sourceNote}</p> : null}
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[58rem] border-collapse text-sm">
          <thead>
            <tr className="border-b border-border/60 text-left text-xs text-secondary-text">
              <th className="py-2 pr-3 font-medium">{t('industryFunds.colRank')}</th>
              <SortHeader
                label={t('industryFunds.colIndustry')}
                active={sortKey === 'name'}
                descending={descending}
                onClick={() => toggleSort('name')}
              />
              <SortHeader
                label={t('industryFunds.colNet')}
                active={sortKey === 'net'}
                descending={descending}
                onClick={() => toggleSort('net')}
                align="right"
              />
              <SortHeader
                label={t('industryFunds.colNet5d')}
                active={sortKey === 'net5D'}
                descending={descending}
                onClick={() => toggleSort('net5D')}
                align="right"
              />
              <SortHeader
                label={t('industryFunds.colChange')}
                active={sortKey === 'changePct'}
                descending={descending}
                onClick={() => toggleSort('changePct')}
                align="right"
              />
              <th className="py-2 pr-3 text-right font-medium">{t('industryFunds.colInflow')}</th>
              <th className="py-2 pr-3 text-right font-medium">{t('industryFunds.colOutflow')}</th>
              <th className="py-2 pr-3 text-right font-medium">
                {t('industryFunds.colCompanies')}
              </th>
              <th className="py-2 pr-3 font-medium">{t('industryFunds.colLeader')}</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((row, index) => (
              <tr key={row.name} className="border-b border-border/30 hover:bg-hover/40">
                <td className="py-2 pr-3 text-xs text-secondary-text">{index + 1}</td>
                <td className="py-2 pr-3 font-medium text-foreground">{row.name}</td>
                <td className={cn('py-2 pr-3 text-right tabular-nums', inOutTone(row.net))}>
                  {formatSignedYi(row.net)}
                </td>
                <td className={cn('py-2 pr-3 text-right tabular-nums', inOutTone(row.net5D))}>
                  {formatSignedYi(row.net5D)}
                </td>
                <td className={cn('py-2 pr-3 text-right tabular-nums', inOutTone(row.changePct))}>
                  {formatPct(row.changePct, 2, true)}
                </td>
                <td className="py-2 pr-3 text-right tabular-nums text-secondary-text">
                  {formatYi(row.inflow)}
                </td>
                <td className="py-2 pr-3 text-right tabular-nums text-secondary-text">
                  {formatYi(row.outflow)}
                </td>
                <td className="py-2 pr-3 text-right tabular-nums text-secondary-text">
                  {row.companyCount ?? '—'}
                </td>
                <td className="py-2 pr-3">
                  {row.leader ? (
                    <span className="flex items-baseline gap-1.5">
                      <span className="text-foreground">{row.leader}</span>
                      <span className={cn('text-xs tabular-nums', inOutTone(row.leaderChangePct))}>
                        {formatPct(row.leaderChangePct, 2, true)}
                      </span>
                    </span>
                  ) : (
                    <span className="text-secondary-text">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {sorted.length > INITIAL_ROWS ? (
        <button
          type="button"
          onClick={() => setShowAll(!showAll)}
          className="mt-3 text-xs text-cyan transition-colors hover:text-cyan/80"
        >
          {showAll
            ? t('industryFunds.showLess')
            : t('industryFunds.showAll').replace('{count}', String(sorted.length))}
        </button>
      ) : null}
    </SectionCard>
  );
};

const SortHeader: React.FC<{
  label: string;
  active: boolean;
  descending: boolean;
  onClick: () => void;
  align?: 'left' | 'right';
}> = ({ label, active, descending, onClick, align = 'left' }) => (
  <th className={cn('py-2 pr-3 font-medium', align === 'right' && 'text-right')}>
    <Tooltip content={label}>
      <button
        type="button"
        onClick={onClick}
        className={cn(
          'inline-flex items-center gap-1 transition-colors hover:text-foreground',
          active && 'text-foreground'
        )}
      >
        {label}
        <ArrowDownUp
          className={cn('h-3 w-3', active ? 'opacity-100' : 'opacity-40')}
          style={active && !descending ? { transform: 'scaleY(-1)' } : undefined}
        />
      </button>
    </Tooltip>
  </th>
);
