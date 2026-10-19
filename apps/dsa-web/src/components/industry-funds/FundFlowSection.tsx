import type React from 'react';
import { useMemo, useState } from 'react';
import { ArrowDownUp, Info, LayoutGrid, Table } from 'lucide-react';
import { ResponsiveContainer, Treemap } from 'recharts';
import type { TreemapNode } from 'recharts';
import type { UiTextKey } from '../../i18n/uiText';
import type { IndustryRow } from '../../types/industryFunds';
import { cn } from '../../utils/cn';
import { Badge } from '../common/Badge';
import { SectionCard } from '../common/SectionCard';
import { Tooltip } from '../common/Tooltip';
import { formatPct, formatSignedYi, formatYi, inOutTone } from './format';

type Translate = (key: UiTextKey) => string;

type SortKey = 'net' | 'net5D' | 'changePct' | 'name';
type ViewMode = 'table' | 'heatmap';
type SizeMetric = 'turnover' | 'net' | 'inflow';

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
  const [viewMode, setViewMode] = useState<ViewMode>('table');
  const [sizeMetric, setSizeMetric] = useState<SizeMetric>('turnover');

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
          {/* 多数行业净流入 = 偏多宽度，A 股口径用红；否则中性灰。 */}
          <Badge variant={inflowCount > industries.length / 2 ? 'danger' : 'default'} size="sm">
            {t('industryFunds.inflowSummary')
              .replace('{inflow}', String(inflowCount))
              .replace('{total}', String(industries.length))}
          </Badge>
          <div className="inline-flex overflow-hidden rounded-lg border border-border/60">
            <ViewToggleButton
              active={viewMode === 'table'}
              onClick={() => setViewMode('table')}
              icon={<Table className="h-3.5 w-3.5" aria-hidden="true" />}
              label={t('industryFunds.viewTable')}
            />
            <ViewToggleButton
              active={viewMode === 'heatmap'}
              onClick={() => setViewMode('heatmap')}
              icon={<LayoutGrid className="h-3.5 w-3.5" aria-hidden="true" />}
              label={t('industryFunds.viewHeatmap')}
            />
          </div>
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

      {viewMode === 'heatmap' ? (
        <FundFlowHeatmap
          industries={industries}
          sizeMetric={sizeMetric}
          onSizeMetricChange={setSizeMetric}
          t={t}
        />
      ) : (
        <>
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
        </>
      )}
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

const ViewToggleButton: React.FC<{
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
}> = ({ active, onClick, icon, label }) => (
  <button
    type="button"
    onClick={onClick}
    aria-pressed={active}
    className={cn(
      'inline-flex items-center gap-1 px-2.5 py-1 text-xs transition-colors',
      active ? 'bg-cyan/15 text-cyan' : 'text-secondary-text hover:text-foreground'
    )}
  >
    {icon}
    {label}
  </button>
);

type SizeMetricDef = { key: SizeMetric; labelKey: UiTextKey; value: (row: IndustryRow) => number };

// 面积指标只用「主力资金」口径下已有的字段：当前数据源（同花顺行业资金流落地页）
// 没有全口径成交额 / 成交量 / 市值，不能照搬同花顺板块热力图那套 size 维度。
const SIZE_METRICS: SizeMetricDef[] = [
  {
    key: 'turnover',
    labelKey: 'industryFunds.metricTurnover',
    value: (row) => (row.inflow ?? 0) + (row.outflow ?? 0),
  },
  { key: 'net', labelKey: 'industryFunds.metricNetAbs', value: (row) => Math.abs(row.net ?? 0) },
  { key: 'inflow', labelKey: 'industryFunds.metricInflow', value: (row) => row.inflow ?? 0 },
];

type HeatNode = { name: string; size: number; net: number; changePct: number | null };

const FundFlowHeatmap: React.FC<{
  industries: IndustryRow[];
  sizeMetric: SizeMetric;
  onSizeMetricChange: (metric: SizeMetric) => void;
  t: Translate;
}> = ({ industries, sizeMetric, onSizeMetricChange, t }) => {
  const metricDef = SIZE_METRICS.find((m) => m.key === sizeMetric) ?? SIZE_METRICS[0];

  const data = useMemo<HeatNode[]>(
    () =>
      industries
        .map((row) => ({
          name: row.name,
          size: Math.max(0, metricDef.value(row)),
          net: row.net ?? 0,
          changePct: row.changePct,
        }))
        .filter((node) => node.size > 0)
        .sort((a, b) => b.size - a.size),
    [industries, metricDef]
  );

  // 深浅按「当日净额」的相对强度，最大绝对值兜底为 1，避免除零。
  const maxNetAbs = useMemo(
    () => data.reduce((max, node) => Math.max(max, Math.abs(node.net)), 0) || 1,
    [data]
  );

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <label className="flex items-center gap-2 text-xs text-secondary-text">
          {t('industryFunds.heatmapMetric')}
          <select
            value={sizeMetric}
            onChange={(e) => onSizeMetricChange(e.target.value as SizeMetric)}
            className="rounded-lg border border-border/60 bg-elevated px-2 py-1 text-xs text-foreground"
          >
            {SIZE_METRICS.map((metric) => (
              <option key={metric.key} value={metric.key}>
                {t(metric.labelKey)}
              </option>
            ))}
          </select>
        </label>
        <div className="flex items-center gap-3 text-xs text-secondary-text">
          <span className="flex items-center gap-1">
            <span
              className="inline-block h-2.5 w-2.5 rounded-sm"
              style={{ backgroundColor: 'hsl(var(--color-danger))' }}
            />
            {t('industryFunds.legendInflow')}
          </span>
          <span className="flex items-center gap-1">
            <span
              className="inline-block h-2.5 w-2.5 rounded-sm"
              style={{ backgroundColor: 'hsl(var(--color-success))' }}
            />
            {t('industryFunds.legendOutflow')}
          </span>
        </div>
      </div>

      {data.length === 0 ? (
        <p className="py-10 text-center text-sm text-secondary-text">
          {t('industryFunds.heatmapEmpty')}
        </p>
      ) : (
        <div className="h-[28rem] w-full">
          <ResponsiveContainer width="100%" height="100%">
            <Treemap
              data={data}
              dataKey="size"
              nameKey="name"
              isAnimationActive={false}
              content={(node) => renderHeatCell(node, maxNetAbs, metricDef, t)}
            />
          </ResponsiveContainer>
        </div>
      )}

      <p className="mt-3 text-xs text-secondary-text">{t('industryFunds.heatmapNote')}</p>
    </div>
  );
};

function renderHeatCell(
  node: TreemapNode,
  maxNetAbs: number,
  metricDef: SizeMetricDef,
  t: Translate
): React.ReactElement {
  const { x, y, width, height, depth } = node;
  // 扁平数据下根节点 depth===0 会铺满整个容器，跳过以免盖住瓦片。
  if (depth === 0 || !(width > 0) || !(height > 0)) {
    return <g />;
  }
  const name = typeof node.name === 'string' ? node.name : '';
  const net = typeof node.net === 'number' ? node.net : 0;
  const size = typeof node.size === 'number' ? node.size : 0;
  const changePct = typeof node.changePct === 'number' ? node.changePct : null;
  // A 股口径：净流入(正)红、净流出(负)绿，与 inOutTone 一致；深浅随净额强度。
  const colorVar = net > 0 ? '--color-danger' : net < 0 ? '--color-success' : '--border';
  const intensity = Math.min(1, Math.abs(net) / maxNetAbs);
  const fillOpacity = net === 0 ? 0.25 : 0.22 + 0.63 * intensity;
  const showName = width > 44 && height > 26;
  const showValue = width > 54 && height > 44;
  return (
    <g>
      <rect
        x={x}
        y={y}
        width={width}
        height={height}
        fill={`hsl(var(${colorVar}))`}
        fillOpacity={fillOpacity}
        stroke="hsl(var(--background))"
        strokeWidth={1}
      >
        <title>{`${name}｜${t('industryFunds.colNet')} ${formatSignedYi(net, 2)}｜${t('industryFunds.colChange')} ${formatPct(changePct, 2, true)}｜${t(metricDef.labelKey)} ${formatYi(size, 2)}`}</title>
      </rect>
      {showName ? (
        <text
          x={x + 6}
          y={y + 17}
          fontSize={11}
          fill="hsl(var(--foreground))"
          className="pointer-events-none"
        >
          {name}
        </text>
      ) : null}
      {showValue ? (
        <text
          x={x + 6}
          y={y + 32}
          fontSize={10}
          fill="hsl(var(--secondary-text))"
          className="pointer-events-none"
        >
          {formatYi(size, 1)}
        </text>
      ) : null}
    </g>
  );
}
