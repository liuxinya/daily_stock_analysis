import type React from 'react';
import { useMemo } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { KlineRow } from '../../api/baiduFinance';
import { ChartLegend } from './shared';
import { trendClass, withSign } from './format';

/**
 * 日 K 走势：收盘价 + MA5/MA10/MA20，成交量单独一张图。
 *
 * 价格与成交量量级差两三个数量级，因此拆成两张共享 X 轴的图，而不是双 Y 轴
 * ——双轴会让两条序列的交叉点变成视觉巧合，是最常见的图表错误。
 */

const MA_SERIES = [
  { key: 'ma5' as const, label: 'MA5', color: 'var(--chart-ma5)' },
  { key: 'ma10' as const, label: 'MA10', color: 'var(--chart-ma10)' },
  { key: 'ma20' as const, label: 'MA20', color: 'var(--chart-ma20)' },
];

const fmtVolume = (value: number): string => {
  if (!Number.isFinite(value) || value === 0) return '--';
  if (Math.abs(value) >= 1e8) return `${(value / 1e8).toFixed(2)}亿`;
  if (Math.abs(value) >= 1e4) return `${(value / 1e4).toFixed(1)}万`;
  return String(value);
};

const KlineTooltip: React.FC<{ active?: boolean; payload?: Array<{ payload: KlineRow }> }> = ({ active, payload }) => {
  const row = payload?.[0]?.payload;
  if (!active || !row) return null;
  const rows: Array<[string, string, string?]> = [
    ['开盘', row.open.toFixed(2)],
    ['收盘', row.close.toFixed(2)],
    ['最高', row.high.toFixed(2)],
    ['最低', row.low.toFixed(2)],
    ['涨跌幅', withSign(row.ratio.toFixed(2), '%'), trendClass(row.ratio)],
    ['成交量', fmtVolume(row.volume)],
  ];
  return (
    <div className="rounded-xl border border-border bg-elevated/95 px-3 py-2 shadow-lg backdrop-blur">
      <p className="mb-1 text-xs font-medium text-foreground">{row.time}</p>
      <dl className="space-y-0.5">
        {rows.map(([label, value, cls]) => (
          <div key={label} className="flex items-baseline justify-between gap-4 text-xs">
            <dt className="text-muted-text">{label}</dt>
            <dd className={`tabular-nums ${cls ?? 'text-foreground'}`}>{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
};

type PriceChartProps = {
  rows: KlineRow[];
  pressure?: string;
  support?: string;
};

export const PriceChart: React.FC<PriceChartProps> = ({ rows, pressure, support }) => {
  const last = rows.at(-1);
  const legendItems = useMemo(() => ([
    { label: '收盘', color: 'hsl(var(--foreground))', value: last ? last.close.toFixed(2) : undefined },
    ...MA_SERIES.map((series) => ({
      label: series.label,
      color: series.color,
      value: last?.[series.key] != null ? Number(last[series.key]).toFixed(2) : undefined,
    })),
  ]), [last]);

  if (!rows.length) {
    return <p className="py-10 text-center text-sm text-muted-text">暂无 K 线数据</p>;
  }

  const axisProps = {
    stroke: 'var(--chart-axis)',
    tick: { fontSize: 11, fill: 'hsl(var(--muted-text))' },
    tickLine: false,
  } as const;

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <ChartLegend items={legendItems} />
        {pressure || support ? (
          <p className="text-xs text-secondary-text">
            压力位 <span className="tabular-nums text-[var(--chart-up)]">{pressure ?? '--'}</span>
            <span className="mx-2 text-border">|</span>
            支撑位 <span className="tabular-nums text-[var(--chart-down)]">{support ?? '--'}</span>
          </p>
        ) : null}
      </div>

      <div className="h-64 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 6, right: 8, bottom: 0, left: 0 }} syncId="kline">
            <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
            <XAxis dataKey="time" {...axisProps} minTickGap={48} />
            <YAxis {...axisProps} domain={['auto', 'auto']} width={64} tickFormatter={(v: number) => v.toFixed(2)} />
            <Tooltip content={<KlineTooltip />} cursor={{ stroke: 'var(--chart-axis)', strokeDasharray: '3 3' }} />
            <Line
              type="monotone"
              dataKey="close"
              stroke="hsl(var(--foreground))"
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4 }}
              name="收盘"
            />
            {MA_SERIES.map((series) => (
              <Line
                key={series.key}
                type="monotone"
                dataKey={series.key}
                stroke={series.color}
                strokeWidth={2}
                dot={false}
                connectNulls
                name={series.label}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div className="h-24 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} margin={{ top: 0, right: 8, bottom: 0, left: 0 }} syncId="kline">
            <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
            <XAxis dataKey="time" {...axisProps} minTickGap={48} hide />
            <YAxis {...axisProps} width={64} tickFormatter={fmtVolume} />
            <Tooltip content={<KlineTooltip />} cursor={{ fill: 'var(--chart-grid)' }} />
            {/* 成交量刻意不用 MA 的任何一个色：同一个色在相邻两图里代表两件事会误读 */}
            <Bar dataKey="volume" name="成交量" radius={[2, 2, 0, 0]} fill="hsl(var(--muted-text))" fillOpacity={0.55} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <p className="text-xs text-muted-text">上：收盘价与均线 · 下：成交量（共用时间轴，未使用双 Y 轴）</p>
    </div>
  );
};
