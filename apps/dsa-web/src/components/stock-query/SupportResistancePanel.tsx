import type React from 'react';
import { Bar, CartesianGrid, ComposedChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { KlineRow } from '../../api/baiduFinance';
import { text, toNumber, trendClass, withSign } from './format';

/**
 * 压力位 / 支撑位。
 *
 * 数据来自 /sapi/v1/get_analysis_quotation 的 analysisData：yl 是压力位、zc 是
 * 支撑位、price 是取数时的现价。它不在 /vapi/v1/analysis 里，所以分析 tab 也要
 * 取一次 K 线接口。
 *
 * 蜡烛用「涨=空心、跌=实心」的传统画法：红绿在 deutan 下 ΔE≈8，光靠颜色分不出
 * 方向，实心/空心才是不依赖颜色的那一层编码。
 * 两条参考线各自直接标数值，同样不靠颜色认身份。
 */

type CandleShapeProps = {
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  payload?: KlineRow;
};

/**
 * dataKey 给的是 [low, high] 区间，于是 y..y+height 就对应 high..low，
 * 由此推出价格到像素的比例，不必再去拿坐标轴的 scale。
 */
const Candle: React.FC<CandleShapeProps> = ({ x = 0, y = 0, width = 0, height = 0, payload }) => {
  if (!payload) return null;
  const { open, close, high, low } = payload;
  const span = high - low;
  if (!(span > 0) || !(height > 0)) return null;

  const scale = height / span;
  const priceToY = (price: number) => y + (high - price) * scale;

  const rising = close >= open;
  const color = rising ? 'var(--chart-up)' : 'var(--chart-down)';
  const bodyTop = priceToY(Math.max(open, close));
  const bodyHeight = Math.max(1, Math.abs(close - open) * scale);
  const bodyWidth = Math.max(1, width * 0.62);
  const bodyX = x + (width - bodyWidth) / 2;
  const centerX = x + width / 2;

  return (
    <g>
      <line x1={centerX} x2={centerX} y1={y} y2={y + height} stroke={color} strokeWidth={1} />
      <rect
        x={bodyX}
        y={bodyTop}
        width={bodyWidth}
        height={bodyHeight}
        // 涨空心、跌实心：方向不只由颜色承载
        fill={rising ? 'transparent' : color}
        stroke={color}
        strokeWidth={1}
      />
    </g>
  );
};

const CandleTooltip: React.FC<{ active?: boolean; payload?: Array<{ payload: KlineRow }> }> = ({ active, payload }) => {
  const row = payload?.[0]?.payload;
  if (!active || !row) return null;
  return (
    <div className="rounded-xl border border-border bg-elevated/95 px-3 py-2 shadow-lg backdrop-blur">
      <p className="mb-1 text-xs font-medium text-foreground">{row.time}</p>
      <dl className="space-y-0.5">
        {([['开', row.open], ['收', row.close], ['高', row.high], ['低', row.low]] as const).map(([label, value]) => (
          <div key={label} className="flex items-baseline justify-between gap-4 text-xs">
            <dt className="text-muted-text">{label}</dt>
            <dd className="tabular-nums text-foreground">{value.toFixed(2)}</dd>
          </div>
        ))}
        <div className="flex items-baseline justify-between gap-4 text-xs">
          <dt className="text-muted-text">涨跌幅</dt>
          <dd className={`tabular-nums ${trendClass(row.ratio)}`}>{withSign(row.ratio.toFixed(2), '%')}</dd>
        </div>
      </dl>
    </div>
  );
};

type Props = {
  rows: KlineRow[];
  pressure?: string;
  support?: string;
  price?: number;
  updateTime?: string;
};

export const SupportResistancePanel: React.FC<Props> = ({ rows, pressure, support, price, updateTime }) => {
  const hasLevels = Boolean(pressure || support);
  if (!rows.length && !hasLevels) {
    return <p className="py-8 text-center text-sm text-muted-text">暂无压力位/支撑位数据</p>;
  }

  const pressureValue = toNumber(pressure);
  const supportValue = toNumber(support);
  const current = price ?? rows.at(-1)?.close;

  // 参考线可能落在近 100 日区间之外，域要把它们一起算进去，否则线会被裁掉。
  const lows = rows.map((row) => row.low);
  const highs = rows.map((row) => row.high);
  const candidates = [...lows, ...highs, pressureValue, supportValue].filter((n) => n > 0);
  const min = Math.min(...candidates);
  const max = Math.max(...candidates);
  const pad = (max - min) * 0.06 || 1;

  const axisProps = {
    stroke: 'var(--chart-axis)',
    tick: { fontSize: 11, fill: 'hsl(var(--muted-text))' },
    tickLine: false,
  } as const;

  return (
    <div className="space-y-3">
      {/* 数值一律走文本色，身份由左侧色块承载：把 amber/purple 当文字色在暗色底上读不清 */}
      <div className="flex flex-wrap items-baseline gap-x-8 gap-y-2">
        <span className="flex items-baseline gap-2 text-sm text-muted-text">
          <span aria-hidden className="mb-0.5 h-0 w-3.5 self-center border-t-2 border-dashed border-[var(--chart-ma10)]" />
          压力位
          <span className="text-base font-semibold tabular-nums text-foreground">{text(pressure)}</span>
        </span>
        <span className="flex items-baseline gap-2 text-sm text-muted-text">
          <span aria-hidden className="mb-0.5 h-0 w-3.5 self-center border-t-2 border-dashed border-[var(--chart-ma5)]" />
          支撑位
          <span className="text-base font-semibold tabular-nums text-foreground">{text(support)}</span>
        </span>
        <span className="text-sm text-muted-text">
          现价
          <span className="ml-2 text-base font-semibold tabular-nums text-foreground">
            {current != null ? current.toFixed(2) : '--'}
          </span>
        </span>
        {updateTime ? <span className="text-xs text-muted-text">更新于 {updateTime}</span> : null}
      </div>

      <ul className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-secondary-text">
        <li className="flex items-center gap-1.5">
          <span aria-hidden className="h-2.5 w-2 rounded-sm border border-[var(--chart-up)]" />
          涨（空心）
        </li>
        <li className="flex items-center gap-1.5">
          <span aria-hidden className="h-2.5 w-2 rounded-sm bg-[var(--chart-down)]" />
          跌（实心）
        </li>
      </ul>

      {rows.length ? (
        <div className="h-64 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={rows} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
              <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
              <XAxis dataKey="time" {...axisProps} minTickGap={48} />
              <YAxis
                {...axisProps}
                width={64}
                domain={[Number((min - pad).toFixed(2)), Number((max + pad).toFixed(2))]}
                tickFormatter={(v: number) => v.toFixed(2)}
              />
              <Tooltip content={<CandleTooltip />} cursor={{ fill: 'var(--chart-grid)' }} />
              <Bar dataKey={(row: KlineRow) => [row.low, row.high]} shape={<Candle />} isAnimationActive={false} />
              {/* 线上不再挂文字标签：数值已在上方常驻，挂上去会和右端蜡烛叠在一起 */}
              {pressureValue > 0 ? (
                <ReferenceLine y={pressureValue} stroke="var(--chart-ma10)" strokeDasharray="4 4" />
              ) : null}
              {supportValue > 0 ? (
                <ReferenceLine y={supportValue} stroke="var(--chart-ma5)" strokeDasharray="4 4" />
              ) : null}
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      ) : null}
    </div>
  );
};
