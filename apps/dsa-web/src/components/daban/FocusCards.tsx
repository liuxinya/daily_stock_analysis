import type React from 'react';
import { Badge } from '../common';
import type { UiTextKey } from '../../i18n/uiText';
import type {
  DabanFocusFlag,
  DabanFocusItem,
  DabanLimitBoardItem,
  DabanPrinciple,
  DabanScoreItem,
  DabanTemper,
} from '../../types/daban';
import { cn } from '../../utils/cn';
import { changeTone, formatClock as fmtClock, formatPct, formatRate, formatSigned as signed } from './format';
import { PrincipleTags, StockLink } from './shared';

type Translate = (key: UiTextKey, params?: Record<string, string | number>) => string;


const FLAG_VARIANTS: Record<DabanFocusFlag, 'warning' | 'default' | 'info' | 'history' | 'danger'> = {
  downgraded: 'warning',
  uncovered: 'default',
  opened_entry: 'info',
  node_day: 'history',
  override: 'danger',
  surge: 'danger',
  unverified: 'warning',
};

const TEMPER_VARIANTS: Record<DabanTemper['tag'], 'danger' | 'info' | 'default'> = {
  active: 'danger',
  normal: 'info',
  inactive: 'default',
  unknown: 'default',
};

/** 计分项一行：分值、情形、出处（原文条目或「系统补充」）、标定时的回测数字。 */
export const ScoreItemLine: React.FC<{ item: DabanScoreItem; principles: Record<string, DabanPrinciple>; t: Translate }> = ({
  item,
  principles,
  t,
}) => (
  <li className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-[11px] leading-relaxed">
    <span className={cn('w-8 shrink-0 text-right font-semibold tabular-nums', item.points > 0 ? 'text-danger' : 'text-success')}>
      {signed(item.points)}
    </span>
    <span className="text-foreground">{item.label}</span>
    {item.supplement ? <Badge variant="default">{t('dabanKing.live.supplement')}</Badge> : null}
    {!item.supplement && item.source ? <span className="text-muted-text">{item.source}</span> : null}
    <PrincipleTags ids={item.principleIds ?? []} principles={principles} />
    {item.n ? (
      <span className="text-muted-text">{t('dabanKing.live.itemBacktest', { n: item.n, days: item.days, excess: signed(item.excess) })}</span>
    ) : null}
  </li>
);

export const TemperLine: React.FC<{ temper: DabanTemper | null | undefined; t: Translate }> = ({ temper, t }) => {
  if (!temper || !temper.text) return null;
  return (
    <p className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px] text-secondary-text">
      <Badge variant={TEMPER_VARIANTS[temper.tag] ?? 'default'}>{t('dabanKing.live.temper', { label: temper.label })}</Badge>
      <span>{temper.text}</span>
      {temper.tag === 'active' ? <span className="text-warning">{t('dabanKing.live.temperRisk')}</span> : null}
    </p>
  );
};

/** 「重点」里的一只票：总分（实时 / 初始）、进入时刻、成本、现价、距涨停、状态、逐项明细、股性。 */
export const FocusCard: React.FC<{
  item: DabanFocusItem;
  principles: Record<string, DabanPrinciple>;
  t: Translate;
  isNew: boolean;
}> = ({ item, principles, t, isNew }) => {
  const scoreChanged = item.liveScore !== null && item.initScore !== null && item.liveScore !== item.initScore;
  return (
    <div
      data-testid={`focus-${item.code}`}
      className={cn(
        'rounded-xl border px-3 py-2',
        item.status === 'sealed' ? 'border-danger/40 bg-danger/5' : 'border-border/50 bg-elevated/30'
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={cn(
            'inline-flex min-w-[3rem] justify-center rounded-lg border px-1.5 py-0.5 text-sm font-semibold tabular-nums',
            (item.liveScore ?? 0) > 0 ? 'border-danger/40 text-danger' : (item.liveScore ?? 0) < 0 ? 'border-success/40 text-success'
              : 'border-border text-foreground'
          )}
        >
          {t('dabanKing.live.score', { score: signed(item.liveScore) })}
        </span>
        <StockLink code={item.code} name={item.name} />
        {isNew ? <Badge variant="warning">{t('dabanKing.live.new')}</Badge> : null}
        <Badge variant={item.status === 'sealed' ? 'danger' : item.status === 'opened' ? 'warning' : 'info'}>
          {t(`dabanKing.live.status.${item.status}` as UiTextKey)}
        </Badge>
        {item.groupKey === 'observe' ? <Badge variant="history">{t('dabanKing.live.observe')}</Badge> : null}
        {item.flags.map((flag) => (
          <Badge key={flag} variant={FLAG_VARIANTS[flag] ?? 'default'}>{t(`dabanKing.live.flag.${flag}` as UiTextKey)}</Badge>
        ))}
      </div>
      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] tabular-nums text-secondary-text">
        <span>{t('dabanKing.live.enteredAt', { time: fmtClock(item.enteredAt) })}</span>
        <span>{t('dabanKing.live.cost', { value: item.cost?.toFixed(2) ?? '—' })}</span>
        <span>{t('dabanKing.live.price', { value: item.price?.toFixed(2) ?? '—' })}</span>
        {item.status !== 'sealed' && item.gapPct !== null ? (
          <span>{t('dabanKing.live.gap', { value: item.gapPct.toFixed(1) })}</span>
        ) : null}
        <span className={changeTone(item.retNow)}>{t('dabanKing.live.retNow', { value: formatPct(item.retNow, 2) })}</span>
        {item.changePct !== null ? <span className={changeTone(item.changePct)}>{formatPct(item.changePct)}</span> : null}
        {scoreChanged ? <span className="text-warning">{t('dabanKing.live.initScore', { score: signed(item.initScore) })}</span> : null}
      </div>
      {item.items.length ? (
        <ul className="mt-1 space-y-0.5">
          {item.items.map((row) => (
            <ScoreItemLine key={row.key} item={row} principles={principles} t={t} />
          ))}
        </ul>
      ) : (
        <p className="mt-1 text-[11px] text-muted-text">{t('dabanKing.live.zeroScore')}</p>
      )}
      {item.observe ? <p className="mt-1 text-[11px] text-purple">{t('dabanKing.live.observeNote', { label: item.observe.label })}</p> : null}
      <TemperLine temper={item.temper} t={t} />
    </div>
  );
};

/** 「涨停板」里的一只票：龙 1 / 龙 2 标记、连板成功率、封单。不评分、不记成绩。 */
export const LimitBoardRow: React.FC<{ item: DabanLimitBoardItem; t: Translate }> = ({ item, t }) => (
  <div
    data-testid={`limit-${item.code}`}
    className={cn('rounded-xl border px-3 py-2', item.leaderRank ? 'border-danger/40 bg-danger/5' : 'border-border/50 bg-elevated/30')}
  >
    <div className="flex flex-wrap items-center gap-2">
      {item.leaderRank ? <Badge variant="danger">{t(item.leaderRank === 1 ? 'dabanKing.live.leader1' : 'dabanKing.live.leader2')}</Badge> : null}
      <StockLink code={item.code} name={item.name} />
      <span className="text-[11px] text-foreground">{item.boards >= 2 ? t('dabanKing.live.boards', { n: item.boards }) : item.highDays || t('dabanKing.live.firstBoard')}</span>
      {item.recommended ? <Badge variant="info">{t('dabanKing.live.recommended')}</Badge> : null}
      {item.sealedNow === false ? <Badge variant="warning">{t('dabanKing.live.unsealed')}</Badge> : null}
      <span className="text-[11px] tabular-nums text-secondary-text">{t('dabanKing.live.firstTime')} {fmtClock(item.firstTime)}</span>
      {item.openNum > 0 ? <span className="text-[11px] text-warning">{t('dabanKing.live.opened', { n: item.openNum })}</span> : null}
    </div>
    <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-secondary-text">
      {item.contRate !== null ? <span className="text-cyan">{t('dabanKing.live.contRate', { value: formatRate(item.contRate) })}</span> : null}
      {item.sealAmount ? <span>{t('dabanKing.live.sealAmount', { value: (item.sealAmount / 1e8).toFixed(2) })}</span> : null}
      {item.sealRatio !== null ? <span>{t('dabanKing.live.sealRatio', { value: formatRate(item.sealRatio, 2) })}</span> : null}
      {item.limitUpType ? <span>{item.limitUpType}</span> : null}
      {item.reason ? <span className="text-muted-text">{item.reason}</span> : null}
    </div>
  </div>
);
