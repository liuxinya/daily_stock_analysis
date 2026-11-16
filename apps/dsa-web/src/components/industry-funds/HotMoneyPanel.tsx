import type React from 'react';
import { useEffect, useMemo, useState } from 'react';
import { Badge, SectionCard, Tooltip } from '../common';
import { industryFundsApi } from '../../api/industryFunds';
import { resolveStockChartCode, useStockChartStore } from '../../stores/stockChartStore';
import type { UiTextKey } from '../../i18n/uiText';
import type {
  HotMoneyDaily,
  HotMoneyDay,
  HotMoneyRow,
  HotMoneySeat,
  HotMoneySeatStock,
  IndustryFundsHotMoney,
} from '../../types/industryFunds';
import { cn } from '../../utils/cn';
import { formatPct, formatSignedYi, formatYi } from './format';
import { HelpTip } from './HelpTip';

type Translate = (key: UiTextKey, params?: Record<string, string | number>) => string;

const TONE = (value: number | null | undefined) =>
  value === null || value === undefined || value === 0 ? 'text-muted-text' : value > 0 ? 'text-danger' : 'text-success';

/**
 * 「游资」tab：龙虎榜营业部席位 × 股票的买卖矩阵。
 *
 * 第一列是股票，后面每一列是一个席位（最近两周买入额最大的活跃营业部），格子里是该席位当天在这只票上的
 * 净买额，悬停能看到买入 / 卖出。点格子或股票打开抽屉看完整买卖。
 *
 * 只展示、不参与打分。游资没有官方身份：交易所只披露营业部席位，不做「这是某某游资」的断言。
 */
export const HotMoneyPanel: React.FC<{
  data: IndustryFundsHotMoney;
  date: string;
  t: Translate;
}> = ({ data, date, t }) => {
  const openChart = useStockChartStore((state) => state.open);
  const openStockChart = (code: string, name: string) => {
    const chartCode = resolveStockChartCode(code);
    if (chartCode) openChart(chartCode, name);
  };
  const day: HotMoneyDay | undefined = data.dates.find((item) => item.date === date);
  const seats = data.seats;
  /** 排序：默认按合计净买降序；点任意席位列头也能按那一列排序。 */
  const [sort, setSort] = useState<{ key: string; desc: boolean }>({ key: 'total', desc: true });
  const toggleSort = (key: string) => setSort((prev) => (prev.key === key ? { key, desc: !prev.desc } : { key, desc: true }));
  const [showAllSeats, setShowAllSeats] = useState(false);
  const rows = useMemo(() => {
    const list = [...(day?.rows ?? [])];
    const value = (row: HotMoneyRow) => (sort.key === 'total' ? row.net : row.cells[sort.key]?.net ?? null);
    list.sort((a, b) => {
      const left = value(a);
      const right = value(b);
      if (left === null && right === null) return Math.abs(b.net) - Math.abs(a.net);
      if (left === null) return 1;              // 这一列没数据的排最后，升降序都一样
      if (right === null) return -1;
      return (sort.desc ? right - left : left - right) || Math.abs(b.net) - Math.abs(a.net);
    });
    return list;
  }, [day?.rows, sort]);
  /** 席位区展示全部能拿到的席位（含没进矩阵列的），默认只露两行。 */
  const chipSeats = data.allSeats?.length ? data.allSeats : seats;

  return (
    <div className="space-y-4">
      <div id="if-hm-seats">
      <SectionCard title={t('industryFunds.flow.hm.seatsTitle')} subtitle={t('industryFunds.flow.hm.seatsHint')}>
        <div className={cn('flex flex-wrap gap-1.5', !showAllSeats && 'max-h-16 overflow-hidden')}>
          {chipSeats.map((seat) => (
            <span
              key={seat.code}
              data-testid={`hm-seat-${seat.code}`}
              className="inline-flex items-center gap-1 rounded-full border border-border/60 bg-elevated/30 px-2.5 py-1 text-[11px] text-secondary-text"
            >
              <Tooltip content={<SeatInfo seat={seat} t={t} />} side="top" contentClassName="max-w-xs">
                <span className="cursor-help border-b border-dotted border-border/60">{seat.alias || seat.short}</span>
              </Tooltip>
              <Tooltip
                content={<SeatDayDetail key={`${seat.code}-${date}`} seat={seat} date={date} t={t} />}
                side="top"
                contentClassName="max-w-sm"
                interactive
              >
                <span className={cn('cursor-help tabular-nums', TONE(seat.net))}>{formatSignedYi(seat.net)}</span>
              </Tooltip>
            </span>
          ))}
        </div>
        {chipSeats.length > 8 ? (
          <button
            type="button"
            data-testid="hm-seats-toggle"
            onClick={() => setShowAllSeats((prev) => !prev)}
            className="mt-1.5 text-[11px] text-cyan hover:underline"
          >
            {showAllSeats
              ? t('industryFunds.flow.hm.seatsCollapse')
              : t('industryFunds.flow.hm.seatsExpand', { n: chipSeats.length })}
          </button>
        ) : null}
        <p className="mt-2 text-[11px] text-muted-text">
          {t('industryFunds.flow.hm.seatNote')}
          {data.aliasNote ? ` ${data.aliasNote}` : ''}
        </p>
      </SectionCard>
      </div>

      <div id="if-hm-matrix">
      <SectionCard
        title={t('industryFunds.flow.hm.matrixTitle', { date })}
        subtitle={t('industryFunds.flow.hm.matrixHint', { n: day?.total ?? 0, limit: data.rowLimit })}
      >
        {rows.length ? (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs" data-testid="hot-money-table">
              <thead className="text-[11px] text-secondary-text">
                <tr className="border-b border-border/50">
                  <th className="sticky left-0 z-10 whitespace-nowrap bg-card/95 py-1.5 pr-3 font-normal">
                    {t('industryFunds.flow.col.stock')}
                  </th>
                  <th className="whitespace-nowrap py-1.5 pr-3 text-right font-normal">{t('industryFunds.flow.hm.colChange')}</th>
                  <th className="whitespace-nowrap py-1.5 pr-3 text-right font-normal">
                    <button
                      type="button"
                      data-testid="hm-sort-toggle"
                      onClick={() => toggleSort('total')}
                      title={t('industryFunds.flow.hm.sortHint')}
                      className="inline-flex items-center gap-1 whitespace-nowrap hover:text-foreground"
                    >
                      {t('industryFunds.flow.hm.colTotal')}
                      <span className="text-[9px] text-cyan">{sort.key === 'total' ? (sort.desc ? '▼' : '▲') : '↕'}</span>
                    </button>
                    <HelpTip text={t('industryFunds.flow.hm.help.colTotal')} />
                  </th>
                  {seats.map((seat) => (
                    <th
                      key={seat.code}
                      title={seat.aliasSource ? `${seat.name}\n依据：${seat.aliasSource}` : seat.name}
                      data-testid={`hm-head-${seat.code}`}
                      className="whitespace-nowrap py-1.5 pr-3 text-right font-normal"
                    >
                      <button
                        type="button"
                        data-testid={`hm-sort-${seat.code}`}
                        onClick={() => toggleSort(seat.code)}
                        title={t('industryFunds.flow.hm.sortHint')}
                        className="inline-flex items-center gap-1 whitespace-nowrap hover:text-foreground"
                      >
                        {seat.alias ? (
                          <span className="border-b border-dotted border-border/70">{seat.alias}</span>
                        ) : (
                          seat.short
                        )}
                        <span className="text-[9px] text-cyan">{sort.key === seat.code ? (sort.desc ? '▼' : '▲') : '↕'}</span>
                      </button>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.code} className="border-b border-border/30 hover:bg-hover/40" data-testid={`hm-row-${row.code}`}>
                    <td className="sticky left-0 z-10 whitespace-nowrap bg-card/95 py-1.5 pr-3">
                      <Tooltip
                        content={<HotMoneyBubble row={row} seats={seats} t={t} />}
                        side="right"
                        contentClassName="max-w-sm"
                        interactive
                      >
                        <button
                          type="button"
                          data-testid={`hm-open-${row.code}`}
                          onClick={() => openStockChart(row.code, row.name)}
                          title={t('industryFunds.flow.hm.openChart', { name: row.name, code: row.code })}
                          className="whitespace-nowrap border-b border-dotted border-border/70 font-medium text-foreground hover:text-cyan"
                        >
                          {row.name}
                        </button>
                      </Tooltip>
                    </td>
                    <td className={cn('whitespace-nowrap py-1.5 pr-3 text-right tabular-nums', TONE(row.changePct ?? null))}>
                      {row.changePct === null || row.changePct === undefined ? '—' : formatPct(row.changePct, 2, true)}
                    </td>
                    <td className={cn('py-1.5 pr-3 text-right font-medium tabular-nums', TONE(row.net))}>
                      {formatSignedYi(row.net)}
                    </td>
                    {seats.map((seat) => {
                      const cell = row.cells[seat.code];
                      return (
                        <td
                          key={seat.code}
                          className={cn('py-1.5 pr-3 text-right tabular-nums', TONE(cell?.net ?? null))}
                          title={cell ? `${t('industryFunds.flow.hm.buyShort')} ${formatYi(cell.buy)} / ${t('industryFunds.flow.hm.sellShort')} ${formatYi(cell.sell)}` : ''}
                        >
                          {cell ? formatSignedYi(cell.net) : '—'}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-xs text-secondary-text">{t('industryFunds.flow.hm.empty')}</p>
        )}
        <p className="mt-2 text-[11px] text-muted-text">{data.coverage}</p>
        {data.degraded ? <p className="mt-1 text-[11px] text-warning">{data.degradedReason}</p> : null}
      </SectionCard>
      </div>

      <div id="if-hm-verdict">
        <SectionCard title={t('industryFunds.flow.hm.verdictTitle')} subtitle={t('industryFunds.flow.hm.verdictHint')}>
          {data.verdict?.tags?.length ? (
            <div className="mb-2 flex flex-wrap gap-1.5">
              {data.verdict.tags.map((tag) => <Badge key={tag} variant="info">{tag}</Badge>)}
            </div>
          ) : null}
          <p className="text-xs leading-relaxed text-foreground" data-testid="hm-verdict">{data.verdict?.text}</p>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-[11px]" data-testid="hm-daily-table">
              <thead className="text-secondary-text">
                <tr className="border-b border-border/50">
                  <th className="py-1.5 pr-3 font-normal">{t('industryFunds.flow.hm.col.date')}</th>
                  <th className="py-1.5 pr-3 text-right font-normal">
                    {t('industryFunds.flow.hm.col.seats')}<HelpTip text={t('industryFunds.flow.hm.help.seats')} />
                  </th>
                  <th className="py-1.5 pr-3 text-right font-normal">
                    {t('industryFunds.flow.hm.col.buy')}<HelpTip text={t('industryFunds.flow.hm.help.buy')} />
                  </th>
                  <th className="py-1.5 pr-3 text-right font-normal">
                    {t('industryFunds.flow.hm.col.net')}<HelpTip text={t('industryFunds.flow.hm.help.net')} />
                  </th>
                  <th className="py-1.5 pr-3 text-right font-normal">
                    {t('industryFunds.flow.hm.col.netRatio')}<HelpTip text={t('industryFunds.flow.hm.help.netRatio')} />
                  </th>
                  <th className="py-1.5 pr-3 text-right font-normal">
                    {t('industryFunds.flow.hm.col.upShare')}<HelpTip text={t('industryFunds.flow.hm.help.upShare')} />
                  </th>
                  <th className="py-1.5 pr-3 text-right font-normal">
                    {t('industryFunds.flow.hm.col.downBuy')}<HelpTip text={t('industryFunds.flow.hm.help.downBuy')} />
                  </th>
                  <th className="py-1.5 text-right font-normal">
                    {t('industryFunds.flow.hm.col.limit')}<HelpTip text={t('industryFunds.flow.hm.help.limit')} />
                  </th>
                </tr>
              </thead>
              <tbody>
                {(data.daily ?? []).map((row: HotMoneyDaily) => (
                  <tr
                    key={row.date}
                    className={cn('border-b border-border/30', row.date === date && 'bg-elevated/40')}
                    data-testid={`hm-daily-${row.date}`}
                  >
                    <td className="py-1.5 pr-3 tabular-nums text-secondary-text">{row.date.slice(5)}</td>
                    <td className="py-1.5 pr-3 text-right tabular-nums text-foreground">{row.seats || '—'}</td>
                    <td className="py-1.5 pr-3 text-right tabular-nums text-secondary-text">{formatYi(row.buy)}</td>
                    <td className={cn('py-1.5 pr-3 text-right tabular-nums', TONE(row.net))}>{formatSignedYi(row.net)}</td>
                    <td className="py-1.5 pr-3 text-right tabular-nums text-secondary-text">{formatPct(row.netRatio === null ? null : row.netRatio * 100, 1)}</td>
                    <td className="py-1.5 pr-3 text-right tabular-nums text-secondary-text">{formatPct(row.upShare === null ? null : row.upShare * 100, 1)}</td>
                    <td className="py-1.5 pr-3 text-right tabular-nums text-secondary-text">{formatPct(row.downBuyShare === null ? null : row.downBuyShare * 100, 1)}</td>
                    <td className="py-1.5 text-right tabular-nums text-secondary-text">{formatPct(row.limitShare === null ? null : row.limitShare * 100, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-[11px] text-muted-text">{t('industryFunds.flow.hm.verdictNote')}</p>
          {data.marketNote ? <p className="mt-1 text-[11px] text-warning">{data.marketNote}</p> : null}
        </SectionCard>
      </div>

    </div>
  );
};

/** 股票名悬停气泡：按席位列出这只票当天的买入 / 卖出 / 净买，比点开抽屉快。 */
const HotMoneyBubble: React.FC<{ row: HotMoneyRow; seats: IndustryFundsHotMoney['seats']; t: Translate }> = ({
  row, seats, t,
}) => {
  const traded = seats.filter((seat) => row.cells[seat.code]);
  return (
    <span className="block text-left">
      <span className="block text-xs font-medium text-foreground">
        {row.name} {row.code}
      </span>
      <span className="mt-1 block text-[11px] text-secondary-text">
        {t('industryFunds.flow.hm.drawerTotal', {
          buy: formatYi(row.buy), sell: formatYi(row.sell), net: formatSignedYi(row.net),
        })}
      </span>
      <span className="mt-1.5 block">
        {traded.map((seat) => {
          const cell = row.cells[seat.code];
          return (
            <span key={seat.code} className="flex items-center justify-between gap-3 py-0.5 text-[11px]">
              <span className="text-secondary-text">{seat.alias || seat.short}</span>
              <span className="tabular-nums">
                <span className="text-muted-text">{formatYi(cell.buy)}</span>
                <span className="mx-1 text-muted-text">/</span>
                <span className="text-muted-text">{formatYi(cell.sell)}</span>
                <span className={cn('ml-2', TONE(cell.net))}>{formatSignedYi(cell.net)}</span>
              </span>
            </span>
          );
        })}
      </span>
      <span className="mt-1 block text-[10px] text-muted-text">{t('industryFunds.flow.unitYi')}</span>
    </span>
  );
};

/** 悬停席位名：这个席位是谁（营业部全名、昵称依据、窗口内买卖）。 */
const SeatInfo: React.FC<{ seat: HotMoneySeat; t: Translate }> = ({ seat, t }) => (
  <span className="block text-left">
    <span className="block text-xs font-medium text-foreground">{seat.alias || seat.short}</span>
    <span className="mt-1 block text-[11px] leading-relaxed text-secondary-text">{seat.name}</span>
    {seat.aliasSource ? (
      <span className="mt-0.5 block text-[10px] text-muted-text">{t('industryFunds.flow.hm.aliasBasis', { source: seat.aliasSource })}</span>
    ) : null}
    <span className="mt-1 block text-[11px] text-secondary-text">
      {t('industryFunds.flow.hm.seatWindow', { buy: formatYi(seat.buy), sell: formatYi(seat.sell), net: formatSignedYi(seat.net) })}
    </span>
    <span className="mt-0.5 block text-[10px] text-muted-text">{t('industryFunds.flow.unitYi')}</span>
  </span>
);

/** 席位当天明细的进程内缓存：悬停一次就记住，反复悬停不再打接口。 */
const SEAT_DETAIL_CACHE = new Map<string, { stocks: HotMoneySeatStock[]; error: string }>();
const SEAT_DETAIL_INFLIGHT = new Map<string, Promise<{ stocks: HotMoneySeatStock[]; error: string }>>();
const SEAT_DETAIL_CACHE_MAX = 200;

const loadSeatDetail = (codes: string, date: string): Promise<{ stocks: HotMoneySeatStock[]; error: string }> => {
  const key = `${codes}|${date}`;
  const cached = SEAT_DETAIL_CACHE.get(key);
  if (cached) return Promise.resolve(cached);
  const inflight = SEAT_DETAIL_INFLIGHT.get(key);
  if (inflight) return inflight;
  const task = industryFundsApi
    .getSeatDetail(codes.split(','), date)
    .then((detail) => {
      const value = { stocks: detail.stocks ?? [], error: '' };
      SEAT_DETAIL_CACHE.set(key, value);
      if (SEAT_DETAIL_CACHE.size > SEAT_DETAIL_CACHE_MAX) {
        const oldest = SEAT_DETAIL_CACHE.keys().next().value;
        if (oldest) SEAT_DETAIL_CACHE.delete(oldest);
      }
      return value;
    })
    .catch((error: Error) => ({ stocks: [], error: error.message || '' }))
    .finally(() => {
      SEAT_DETAIL_INFLIGHT.delete(key);
    });
  SEAT_DETAIL_INFLIGHT.set(key, task);
  return task;
};

/** 悬停席位金额：这个席位在选中那天的逐股买卖（按需拉，打开气泡才请求；同一天同席位只请求一次）。 */
const SeatDayDetail: React.FC<{ seat: HotMoneySeat; date: string; t: Translate }> = ({ seat, date, t }) => {
  // 不在 effect 里同步 setState（会触发级联渲染）：初始 null = 加载中，结果只在 then / catch 里落，
  // 换席位或换日期时由调用方用 key 重挂载，状态自然重置。
  const codes = (seat.codes?.length ? seat.codes : [seat.code]).join(',');
  const [state, setState] = useState<{ stocks: HotMoneySeatStock[]; error: string } | null>(
    SEAT_DETAIL_CACHE.get(`${codes}|${date}`) ?? null,
  );

  useEffect(() => {
    let active = true;
    void loadSeatDetail(codes, date).then((value) => {
      if (active) setState(value);
    });
    return () => {
      active = false;
    };
  }, [codes, date]);

  return (
    <span className="block text-left">
      <span className="block text-xs font-medium text-foreground">
        {seat.alias || seat.short} · {date}
      </span>
      {state === null ? (
        <span className="mt-1 block text-[11px] text-secondary-text">{t('industryFunds.flow.hm.seatDetailLoading')}</span>
      ) : state.error ? (
        <span className="mt-1 block text-[11px] text-warning">{state.error}</span>
      ) : state.stocks.length ? (
        <span className="mt-1 block">
          {state.stocks.map((stock) => (
            <span key={stock.code} className="flex items-center justify-between gap-3 py-0.5 text-[11px]">
              <span className="text-secondary-text">{stock.name}</span>
              <span className="tabular-nums">
                <span className="text-muted-text">{formatYi(stock.buy)}</span>
                <span className="mx-1 text-muted-text">/</span>
                <span className="text-muted-text">{formatYi(stock.sell)}</span>
                <span className={cn('ml-2', TONE(stock.net))}>{formatSignedYi(stock.net)}</span>
              </span>
            </span>
          ))}
          <span className="mt-1 block text-[10px] text-muted-text">
            {t('industryFunds.flow.hm.seatDetailLegend')}
          </span>
        </span>
      ) : (
        <span className="mt-1 block text-[11px] text-secondary-text">{t('industryFunds.flow.hm.seatDetailEmpty')}</span>
      )}
    </span>
  );
};
