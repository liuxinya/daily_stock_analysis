import type React from 'react';
import { useState } from 'react';
import { Badge, SectionCard } from '../common';
import type { UiTextKey } from '../../i18n/uiText';
import type { DabanLadderTier } from '../../types/daban';
import { cn } from '../../utils/cn';
import { formatRate, formatTime } from './format';
import { StockLink } from './shared';

type Translate = (key: UiTextKey, params?: Record<string, string | number>) => string;

const FIRST_BOARD_PREVIEW = 12;

/** ② 连板梯队：从最高板往下排，层间标晋级率（科比 #38：复盘先把当天涨停的过一遍）。 */
export const BoardLadder: React.FC<{ ladder: DabanLadderTier[]; t: Translate }> = ({ ladder, t }) => {
  const [showAllFirst, setShowAllFirst] = useState(false);
  if (!ladder?.length) return null;

  return (
    <SectionCard title={t('dabanKing.ladderTitle')} subtitle={t('dabanKing.ladderSubtitle')}>
      <div className="space-y-3">
        {ladder.map((tier) => {
          const isFirst = tier.boards === 1;
          const stocks = isFirst && !showAllFirst ? tier.stocks.slice(0, FIRST_BOARD_PREVIEW) : tier.stocks;
          return (
            <div key={tier.boards} className="flex flex-col gap-2 sm:flex-row sm:items-start">
              <div className="flex w-28 shrink-0 flex-col">
                <span className={cn('text-sm font-semibold', tier.boards >= 3 ? 'text-danger' : 'text-foreground')}>
                  {tier.label}
                  <span className="ml-1 text-xs font-normal text-secondary-text">×{tier.count}</span>
                </span>
                {tier.promotion ? (
                  <span className="text-[11px] text-secondary-text">
                    {t('dabanKing.promotionRate', {
                      label: tier.promotion.label,
                      rate: formatRate(tier.promotion.rate),
                    })}
                    <span className="ml-1 tabular-nums">
                      ({tier.promotion.promoted}/{tier.promotion.base})
                    </span>
                  </span>
                ) : null}
              </div>
              <div className="flex flex-1 flex-wrap gap-2">
                {stocks.map((stock) => (
                  <div
                    key={stock.code}
                    className="min-w-[10rem] rounded-xl border border-border/50 bg-elevated/30 px-2.5 py-1.5"
                  >
                    <StockLink code={stock.code} name={stock.name} />
                    <div className="mt-1 flex flex-wrap items-center gap-1 text-[10px] text-secondary-text">
                      {stock.roleLabel ? <Badge variant="info">{stock.roleLabel}</Badge> : null}
                      {stock.limitUpType ? (
                        <Badge variant={stock.limitUpType.includes('一字') ? 'warning' : 'default'}>{stock.limitUpType}</Badge>
                      ) : null}
                      <span>首封 {formatTime(stock.firstTime)}</span>
                      {stock.openNum ? <span>炸 {stock.openNum}</span> : null}
                      {stock.sealRatio !== null ? <span>封单 {formatRate(stock.sealRatio, 1)}</span> : null}
                    </div>
                    {stock.reason ? (
                      <p className="mt-0.5 max-w-[16rem] truncate text-[10px] text-muted-text">{stock.reason}</p>
                    ) : null}
                  </div>
                ))}
                {isFirst && tier.stocks.length > FIRST_BOARD_PREVIEW ? (
                  <button
                    type="button"
                    onClick={() => setShowAllFirst((value) => !value)}
                    className="self-center text-xs text-cyan hover:underline"
                  >
                    {showAllFirst ? t('dabanKing.hideDetail') : t('dabanKing.showAll', { count: tier.stocks.length })}
                  </button>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>
    </SectionCard>
  );
};
