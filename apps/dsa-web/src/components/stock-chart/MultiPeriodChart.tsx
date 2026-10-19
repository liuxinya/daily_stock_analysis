import type React from 'react';
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { ApiErrorAlert, Button, Loading } from '../common';
import { chartApi } from '../../api/chart';
import type { ChartPeriod, KlineBar, KlineResponse, TimeshareResponse } from '../../api/chart';
import { getParsedApiError } from '../../api/error';
import type { ParsedApiError } from '../../api/error';
import { cn } from '../../utils/cn';
import { Candle } from './Candle';
import { pickHoverIndex } from './hover';
import { ChartLegend } from './shared';
import { formatVolume, trendClass, withSign } from './format';

/**
 * 多周期行情图：分时 / 日 / 周 / 月 / 季 K，主图 + MACD + KDJ 三栏常驻。
 *
 * 数据走项目自有的 `/api/v1/chart/*`（后端取同花顺并在服务端算指标），所以 K 线与
 * 指标同源同口径，不会出现「图上的 MACD 和报告里的 MACD 不是一个数」。
 *
 * 三栏共享时间轴与十字光标，各自独立 Y 轴 —— 量级差两三个数量级的序列绝不塞进
 * 同一张图做双 Y 轴，那会让交叉点变成视觉巧合。
 *
 * 视窗（可见根数 + 右端偏移）由 `useViewport` 管：拖动平移、滚轮/双指缩放、方向键
 * 与 +/- 键，以及一个「全览」按钮。只把窗口内的行喂给 recharts，所以无论取回多少
 * 根都不影响渲染开销。
 */

const PERIOD_TABS: Array<{ key: ChartPeriod; label: string }> = [
  { key: 'timeshare', label: '分时' },
  { key: 'day', label: '日K' },
  { key: 'week', label: '周K' },
  { key: 'month', label: '月K' },
  { key: 'quarter', label: '季K' },
];

const MA_SERIES = [
  { key: 'ma5' as const, label: 'MA5', color: 'var(--chart-ma5)' },
  { key: 'ma10' as const, label: 'MA10', color: 'var(--chart-ma10)' },
  { key: 'ma20' as const, label: 'MA20', color: 'var(--chart-ma20)' },
];

/** 一次取够，缩放与平移都在已有数据里做，不边拖边请求。 */
const BAR_LIMIT = 500;

/** 首屏可见根数，以及缩放下限 —— 少于这个数蜡烛就粗得没法读了。 */
const DEFAULT_VISIBLE = 120;
const MIN_VISIBLE = 20;

/**
 * 滚轮 / 双指上下滑的缩放灵敏度：`size *= exp(deltaY * k)`。
 * 鼠标滚轮一格 deltaY≈100，对应约 1.16 倍；触控板的小 delta 则连续跟手。
 */
const ZOOM_SENSITIVITY = 0.0015;

/** 键盘 +/- 一次的缩放比例。 */
const KEY_ZOOM_STEP = 1.2;

const AXIS = {
  stroke: 'var(--chart-axis)',
  tick: { fontSize: 11, fill: 'hsl(var(--muted-text))' },
  tickLine: false,
} as const;

/** OHLC 齐备、并带上「MACD 柱是否在放大」的行。 */
type Candlestick = KlineBar & {
  open: number;
  high: number;
  low: number;
  close: number;
  /** 柱体放大 = 实心，收缩 = 空心。方向之外再给一层不依赖颜色的编码。 */
  macdGrowing: boolean;
};

const hasOhlc = (row: KlineBar): boolean =>
  row.open != null && row.high != null && row.low != null && row.close != null;

const fmtTime = (value: string): string => {
  if (value.length === 8) return `${value.slice(4, 6)}-${value.slice(6, 8)}`;
  if (value.length >= 12) return `${value.slice(8, 10)}:${value.slice(10, 12)}`;
  return value;
};

const num = (value: number | null | undefined, digits = 2): string =>
  value == null ? '--' : value.toFixed(digits);

/**
 * MACD 柱：颜色按正负（A 股红涨绿跌），空心/实心按柱体是收缩还是放大。
 * 两层编码叠加，deutan 下也能读出动能变化方向。
 */
const MacdBar: React.FC<{ x?: number; y?: number; width?: number; height?: number; payload?: Candlestick }> = ({
  x = 0,
  y = 0,
  width = 0,
  height = 0,
  payload,
}) => {
  const value = payload?.macd;
  if (value == null) return null;
  const color = value >= 0 ? 'var(--chart-up)' : 'var(--chart-down)';
  const barWidth = Math.max(1, width * 0.5);
  const barHeight = Math.max(Math.abs(height), 0.75);
  return (
    <rect
      x={x + (width - barWidth) / 2}
      y={y}
      width={barWidth}
      height={barHeight}
      fill={payload?.macdGrowing ? color : 'transparent'}
      stroke={color}
      strokeWidth={1}
    />
  );
};

/**
 * 副图的表头条：指标名 + 当前（或光标所在）根的各序列值，对齐同花顺的读法。
 *
 * 刻意不做成浮在图上的绝对定位层 —— 那样会压住上一栏的曲线，三栏挤在一起反而读不清。
 */
const PaneLabel: React.FC<{
  title: string;
  items: Array<{ label: string; value: string; color: string }>;
}> = ({ title, items }) => (
  <div className="flex flex-wrap items-center gap-x-4 gap-y-0.5 px-1 pb-0.5 text-xxs">
    <span className="font-semibold tracking-wide text-foreground">{title}</span>
    {items.map((item) => (
      <span key={item.label} className="tabular-nums" style={{ color: item.color }}>
        {item.label} {item.value}
      </span>
    ))}
  </div>
);

/**
 * 主图的读数行：十字光标所在那一根的完整数据。
 *
 * 位置对齐同花顺 —— 逐根的 OHLC / 量额不该只藏在 tooltip 里，常驻一行才能边移边读，
 * 也同时满足图表对比度的「可见标签」补偿要求。光标离开时回落到最后一根。
 */
const MainPaneLabel: React.FC<{ row?: Candlestick }> = ({ row }) => {
  const amplitude = row?.open ? ((row.close - row.open) / row.open) * 100 : null;
  return (
    <div className="space-y-0.5 px-1 text-xxs">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-0.5">
        <span className="font-semibold tracking-wide text-foreground">{row?.time ?? '--'}</span>
        <span className="text-muted-text">
          开 <span className="tabular-nums text-foreground">{num(row?.open)}</span>
        </span>
        <span className="text-muted-text">
          高 <span className="tabular-nums text-foreground">{num(row?.high)}</span>
        </span>
        <span className="text-muted-text">
          低 <span className="tabular-nums text-foreground">{num(row?.low)}</span>
        </span>
        <span className="text-muted-text">
          收 <span className={cn('tabular-nums', trendClass(amplitude))}>{num(row?.close)}</span>
        </span>
        <span className="text-muted-text">
          振幅{' '}
          <span className={cn('tabular-nums', trendClass(amplitude))}>
            {amplitude == null ? '--' : withSign(amplitude.toFixed(2), '%')}
          </span>
        </span>
        <span className="text-muted-text">
          量 <span className="tabular-nums text-foreground">{row ? formatVolume(row.volume) : '--'}</span>
        </span>
        <span className="text-muted-text">
          额 <span className="tabular-nums text-foreground">{row ? formatVolume(row.amount) : '--'}</span>
        </span>
        {row?.turnoverRate != null ? (
          <span className="text-muted-text">
            换手 <span className="tabular-nums text-foreground">{row.turnoverRate.toFixed(2)}%</span>
          </span>
        ) : null}
        {row?.partial ? <span className="text-muted-text">（未走完）</span> : null}
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-0.5">
        <span className="font-semibold tracking-wide text-foreground">MA</span>
        {MA_SERIES.map((series) => (
          <span key={series.key} className="tabular-nums" style={{ color: series.color }}>
            {series.label} {num(row?.[series.key])}
          </span>
        ))}
      </div>
    </div>
  );
};

/** 一栏副图：表头条 + 图，上方留出分隔线与间距。 */
const SubPane: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div className="mt-4 border-t border-border/40 pt-2">{children}</div>
);

type Viewport = {
  /** 可见根数。 */
  size: number;
  /** 右端隐藏了多少根。0 = 贴着最新一根。 */
  rightOffset: number;
};

const clampViewport = (viewport: Viewport, total: number): Viewport => {
  const size = Math.max(MIN_VISIBLE, Math.min(Math.round(viewport.size), Math.max(total, MIN_VISIBLE)));
  const maxRight = Math.max(0, total - size);
  return { size, rightOffset: Math.max(0, Math.min(Math.round(viewport.rightOffset), maxRight)) };
};

/**
 * 图表视窗：拖动平移 + 滚轮/双指缩放 + 键盘操作。
 *
 * 用「右端偏移」而不是「起始下标」记位置：自动刷新时上游会在末尾追加一根，右端偏移
 * 为 0 时视窗会自然跟上最新那根，往左拖看历史时也不会被刷新拽回去。
 *
 * 缩放锚定右端（和看盘软件一致），不跟随光标 —— 锚点跟手反而让连续缩放难以预期。
 */
const useViewport = (total: number, containerRef: React.RefObject<HTMLDivElement | null>) => {
  const [viewport, setViewport] = useState<Viewport>({ size: DEFAULT_VISIBLE, rightOffset: 0 });
  const pointers = useRef(new Map<number, number>());
  const drag = useRef<{ x: number; rightOffset: number; size: number } | null>(null);
  const pinch = useRef<{ distance: number; size: number } | null>(null);
  // 双指横滑一次只有几个像素，累到够一根再走，否则会一直被 round 抹成 0。
  const panRemainder = useRef(0);
  const pending = useRef<Viewport | null>(null);
  const frame = useRef<number | null>(null);

  // 视窗始终夹在数据范围内再落回 state，避免一直往边界外拖之后要拖很久才回得来。
  const view = clampViewport(viewport, total);
  // 事件回调里要读「当前视窗」，但 render 阶段读 ref 是被禁的，所以在 effect 里同步。
  const viewRef = useRef(view);
  useEffect(() => {
    viewRef.current = view;
  }, [view]);

  /**
   * 合并到一帧里提交，给**滚轮**用。
   *
   * 滚轮（尤其触控板）一次手势能连发几十个事件，远快于一帧，不合并就是三栏图表每秒
   * 重绘上百次。
   */
  const commit = useCallback(
    (next: Viewport) => {
      pending.current = next;
      if (frame.current != null) return;
      frame.current = window.requestAnimationFrame(() => {
        frame.current = null;
        const target = pending.current;
        pending.current = null;
        if (target) setViewport(clampViewport(target, total));
      });
    },
    [total],
  );

  /**
   * 立即提交，给**指针拖动与双指捏合**用。
   *
   * 浏览器本来就把 pointermove 对齐到帧派发，再过一层 rAF 只是白白多等一帧 ——
   * 那一帧就是「滑动后 K 线才动」里能去掉的那部分延迟。顺手取消待执行的帧，
   * 免得滚轮留下的旧目标值反过来盖掉这次。
   */
  const applyNow = useCallback(
    (next: Viewport) => {
      if (frame.current != null) {
        window.cancelAnimationFrame(frame.current);
        frame.current = null;
      }
      pending.current = null;
      setViewport(clampViewport(next, total));
    },
    [total],
  );

  useEffect(() => () => {
    if (frame.current != null) window.cancelAnimationFrame(frame.current);
  }, []);

  const barPixels = useCallback(
    (size: number): number => {
      const width = containerRef.current?.clientWidth ?? 0;
      return width > 0 ? width / size : 0;
    },
    [containerRef],
  );

  const zoomBy = useCallback(
    (factor: number) => commit({ ...viewRef.current, size: viewRef.current.size * factor }),
    [commit],
  );

  const panBy = useCallback(
    (bars: number) => commit({ ...viewRef.current, rightOffset: viewRef.current.rightOffset + bars }),
    [commit],
  );

  const reset = useCallback(() => setViewport({ size: DEFAULT_VISIBLE, rightOffset: 0 }), []);
  const showAll = useCallback(() => setViewport({ size: Math.max(total, MIN_VISIBLE), rightOffset: 0 }), [total]);

  /**
   * 滚轮 / 触控板双指：
   * - 上下滑（或鼠标滚轮）缩放。向上滑 `deltaY > 0`，看到的日期区间变宽。
   * - 左右滑平移。向右滑 `deltaX < 0`，看到更早的日期，和拖动方向一致。
   *
   * 缩放用 `exp(deltaY * k)` 而不是固定倍数：触控板一次手势会连发几十个小 delta，
   * 固定 1.2 倍会瞬间缩到底；指数形式对鼠标滚轮（一格约 100）和触控板都线性跟手。
   *
   * React 的 onWheel 是 passive 的，拿不到 preventDefault，只能自己挂非 passive 监听。
   */
  useEffect(() => {
    const node = containerRef.current;
    if (!node) return;
    const onWheel = (event: WheelEvent) => {
      if (!event.deltaX && !event.deltaY) return;
      event.preventDefault();
      if (Math.abs(event.deltaX) > Math.abs(event.deltaY)) {
        const perBar = barPixels(viewRef.current.size);
        if (!perBar) return;
        panRemainder.current += event.deltaX / perBar;
        const bars = Math.trunc(panRemainder.current);
        if (!bars) return;
        panRemainder.current -= bars;
        panBy(-bars);
        return;
      }
      zoomBy(Math.exp(event.deltaY * ZOOM_SENSITIVITY));
    };
    node.addEventListener('wheel', onWheel, { passive: false });
    return () => node.removeEventListener('wheel', onWheel);
  }, [zoomBy, panBy, barPixels, containerRef]);

  const handlePointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    pointers.current.set(event.pointerId, event.clientX);
    event.currentTarget.setPointerCapture(event.pointerId);
    if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      pinch.current = { distance: Math.abs(a - b) || 1, size: viewRef.current.size };
      drag.current = null;
      return;
    }
    drag.current = { x: event.clientX, rightOffset: viewRef.current.rightOffset, size: viewRef.current.size };
  };

  const handlePointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!pointers.current.has(event.pointerId)) return;
    pointers.current.set(event.pointerId, event.clientX);

    const start = pinch.current;
    if (start && pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      const distance = Math.abs(a - b) || 1;
      applyNow({ ...viewRef.current, size: start.size * (start.distance / distance) });
      return;
    }

    const from = drag.current;
    if (!from) return;
    const perBar = barPixels(from.size);
    if (!perBar) return;
    // 往右拖是把内容拉向右边 -> 看到更早的日期
    const bars = Math.round((event.clientX - from.x) / perBar);
    applyNow({ ...viewRef.current, rightOffset: from.rightOffset + bars });
  };

  const handlePointerUp = (event: React.PointerEvent<HTMLDivElement>) => {
    pointers.current.delete(event.pointerId);
    if (pointers.current.size < 2) pinch.current = null;
    if (pointers.current.size === 0) drag.current = null;
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const step = Math.max(1, Math.round(viewRef.current.size / 10));
    switch (event.key) {
      case 'ArrowLeft':
        panBy(step);
        break;
      case 'ArrowRight':
        panBy(-step);
        break;
      case '+':
      case '=':
        zoomBy(1 / KEY_ZOOM_STEP);
        break;
      case '-':
      case '_':
        zoomBy(KEY_ZOOM_STEP);
        break;
      case 'Home':
        reset();
        break;
      default:
        return;
    }
    event.preventDefault();
  };

  return {
    view,
    reset,
    showAll,
    isZoomed: view.size !== DEFAULT_VISIBLE || view.rightOffset !== 0,
    onPointerDown: handlePointerDown,
    onPointerMove: handlePointerMove,
    onPointerUp: handlePointerUp,
    onKeyDown: handleKeyDown,
  };
};

/**
 * 副图序列用主图均线那三个色槽，而不是新引入色值 —— 那三个已按主题的 lightness
 * band 校验过，暗色下还单独调过步进。
 */
const DIF_COLOR = 'var(--chart-ma10)';
const DEA_COLOR = 'var(--chart-ma5)';
const K_COLOR = 'var(--chart-ma10)';
const D_COLOR = 'var(--chart-ma5)';
const J_COLOR = 'var(--chart-ma20)';

type HoverHandler = (index: number | null) => void;

/**
 * 副图只要十字光标那条竖线，不要再弹一个数值框 —— 数值已经常驻在各自的表头条上。
 * recharts 的竖线是 Tooltip 的 cursor 画的，所以这里挂一个空内容的 Tooltip。
 */
const NoTooltipBox: React.FC = () => null;

const hoverProps = (onHover: HoverHandler) => ({
  onMouseMove: (state: unknown) => onHover(pickHoverIndex(state)),
  onMouseLeave: () => onHover(null),
});

const KlineTooltip: React.FC<{ active?: boolean; payload?: Array<{ payload: Candlestick }> }> = ({
  active,
  payload,
}) => {
  const row = payload?.[0]?.payload;
  if (!active || !row) return null;
  const amplitude = row.open ? ((row.close - row.open) / row.open) * 100 : null;
  const lines: Array<[string, string, string?]> = [
    ['开盘', num(row.open)],
    ['收盘', num(row.close)],
    ['最高', num(row.high)],
    ['最低', num(row.low)],
    ['振幅', amplitude == null ? '--' : withSign(amplitude.toFixed(2), '%'), trendClass(amplitude)],
    ['成交量', formatVolume(row.volume)],
  ];
  return (
    <div className="rounded-xl border border-border bg-elevated/95 px-3 py-2 shadow-lg backdrop-blur">
      <p className="mb-1 text-xs font-medium text-foreground">
        {row.time}
        {row.partial ? <span className="ml-2 text-xxs text-muted-text">未走完</span> : null}
      </p>
      <dl className="space-y-0.5">
        {lines.map(([label, value, klass]) => (
          <div key={label} className="flex items-baseline justify-between gap-4 text-xs">
            <dt className="text-muted-text">{label}</dt>
            <dd className={cn('tabular-nums', klass ?? 'text-foreground')}>{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
};

type PaneProps = {
  rows: Candlestick[];
  syncId: string;
  onHover: HoverHandler;
};

/**
 * 三栏都用 memo 包一层：只移动光标时 `rows` 不变，于是只有读数行重渲染，
 * 不会把三张图连带重绘一遍。
 */
const KlinePane = memo<PaneProps>(({ rows, syncId, onHover }) => {
  const domain = useMemo(() => {
    let min = Number.POSITIVE_INFINITY;
    let max = Number.NEGATIVE_INFINITY;
    for (const row of rows) {
      if (row.low > 0 && row.low < min) min = row.low;
      if (row.high > max) max = row.high;
    }
    if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 1] as const;
    const pad = (max - min) * 0.06 || 1;
    return [Number((min - pad).toFixed(2)), Number((max + pad).toFixed(2))] as const;
  }, [rows]);

  return (
    <div className="h-[300px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart
          data={rows}
          margin={{ top: 6, right: 8, bottom: 4, left: 0 }}
          syncId={syncId}
          {...hoverProps(onHover)}
        >
          <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
          <XAxis dataKey="time" {...AXIS} minTickGap={48} tickFormatter={fmtTime} hide />
          <YAxis {...AXIS} width={60} domain={domain} tickFormatter={(value: number) => value.toFixed(2)} />
          {/* recharts 的 Tooltip 默认带 400ms 过渡，拖动时整个提示框和光标会跟着缓动，看着就是「滑了之后才跟上」 */}
          <Tooltip content={<KlineTooltip />} cursor={{ fill: 'var(--chart-grid)' }} isAnimationActive={false} />
          <Bar dataKey={(row: Candlestick) => [row.low, row.high]} shape={<Candle />} isAnimationActive={false} />
          {MA_SERIES.map((series) => (
            <Line
              key={series.key}
              type="monotone"
              dataKey={series.key}
              stroke={series.color}
              strokeWidth={1.5}
              dot={false}
              connectNulls
              isAnimationActive={false}
              name={series.label}
            />
          ))}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
});
KlinePane.displayName = 'KlinePane';

const MacdPane = memo<PaneProps>(({ rows, syncId, onHover }) => (
  <div className="h-32 w-full">
    <ResponsiveContainer width="100%" height="100%">
      <ComposedChart data={rows} margin={{ top: 6, right: 8, bottom: 4, left: 0 }} syncId={syncId} {...hoverProps(onHover)}>
        <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
        <XAxis dataKey="time" {...AXIS} hide />
        <YAxis {...AXIS} width={60} tickFormatter={(value: number) => value.toFixed(2)} />
        <Tooltip content={<NoTooltipBox />} cursor={{ fill: 'var(--chart-grid)' }} isAnimationActive={false} />
        <ReferenceLine y={0} stroke="var(--chart-axis)" />
        <Bar dataKey="macd" shape={<MacdBar />} isAnimationActive={false} name="MACD" />
        <Line type="monotone" dataKey="dif" stroke={DIF_COLOR} strokeWidth={1.5} dot={false} connectNulls isAnimationActive={false} name="DIF" />
        <Line type="monotone" dataKey="dea" stroke={DEA_COLOR} strokeWidth={1.5} dot={false} connectNulls isAnimationActive={false} name="DEA" />
      </ComposedChart>
    </ResponsiveContainer>
  </div>
));
MacdPane.displayName = 'MacdPane';

const KdjPane = memo<PaneProps>(({ rows, syncId, onHover }) => (
  <div className="h-40 w-full">
    <ResponsiveContainer width="100%" height="100%">
      <ComposedChart data={rows} margin={{ top: 6, right: 8, bottom: 4, left: 0 }} syncId={syncId} {...hoverProps(onHover)}>
        <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
        <XAxis dataKey="time" {...AXIS} minTickGap={48} tickFormatter={fmtTime} />
        <YAxis {...AXIS} width={60} domain={[0, 100]} ticks={[0, 20, 50, 80, 100]} />
        <Tooltip content={<NoTooltipBox />} cursor={{ fill: 'var(--chart-grid)' }} isAnimationActive={false} />
        {/* 20 / 80 是超卖超买参考线，画出来才不用靠记忆读数值 */}
        <ReferenceLine y={80} stroke="var(--chart-axis)" strokeDasharray="4 4" />
        <ReferenceLine y={20} stroke="var(--chart-axis)" strokeDasharray="4 4" />
        <Line type="monotone" dataKey="k" stroke={K_COLOR} strokeWidth={1.5} dot={false} connectNulls isAnimationActive={false} name="K" />
        <Line type="monotone" dataKey="d" stroke={D_COLOR} strokeWidth={1.5} dot={false} connectNulls isAnimationActive={false} name="D" />
        <Line type="monotone" dataKey="j" stroke={J_COLOR} strokeWidth={1.5} dot={false} connectNulls isAnimationActive={false} name="J" />
      </ComposedChart>
    </ResponsiveContainer>
  </div>
));
KdjPane.displayName = 'KdjPane';

const TimeshareTooltip: React.FC<{
  active?: boolean;
  payload?: Array<{ payload: { time: string; price: number; avgPrice: number | null; volume: number } }>;
  prevClose?: number | null;
}> = ({ active, payload, prevClose }) => {
  const point = payload?.[0]?.payload;
  if (!active || !point) return null;
  const change = prevClose ? ((point.price - prevClose) / prevClose) * 100 : null;
  const lines: Array<[string, string, string?]> = [
    ['价格', num(point.price), trendClass(change)],
    ['均价', num(point.avgPrice)],
    ['涨跌幅', change == null ? '--' : withSign(change.toFixed(2), '%'), trendClass(change)],
    ['成交量', formatVolume(point.volume)],
  ];
  return (
    <div className="rounded-xl border border-border bg-elevated/95 px-3 py-2 shadow-lg backdrop-blur">
      <p className="mb-1 text-xs font-medium text-foreground">{fmtTime(point.time)}</p>
      <dl className="space-y-0.5">
        {lines.map(([label, value, klass]) => (
          <div key={label} className="flex items-baseline justify-between gap-4 text-xs">
            <dt className="text-muted-text">{label}</dt>
            <dd className={cn('tabular-nums', klass ?? 'text-foreground')}>{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
};

const TimesharePane: React.FC<{ data: TimeshareResponse }> = ({ data }) => {
  const { points, prevClose } = data;
  if (!points.length) {
    return <p className="py-10 text-center text-sm text-muted-text">暂无分时数据</p>;
  }

  // 昨收居中：分时图的惯例，涨跌在视觉上才对称可比。
  const base = prevClose ?? points[0].price;
  const deviation = Math.max(...points.map((point) => Math.abs(point.price - base)), base * 0.002);
  const last = points.at(-1);
  const change = prevClose && last ? ((last.price - prevClose) / prevClose) * 100 : null;

  return (
    <div className="space-y-2">
      <ChartLegend
        items={[
          { label: '价格', color: 'hsl(var(--foreground))', value: num(last?.price) },
          { label: '均价', color: DEA_COLOR, value: num(last?.avgPrice) },
          { label: '昨收', color: 'var(--chart-axis)', value: num(prevClose) },
        ]}
      />
      <div className="h-64 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={points} margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
            <XAxis dataKey="time" {...AXIS} minTickGap={56} tickFormatter={fmtTime} />
            <YAxis
              {...AXIS}
              width={60}
              domain={[Number((base - deviation).toFixed(2)), Number((base + deviation).toFixed(2))]}
              tickFormatter={(value: number) => value.toFixed(2)}
            />
            <Tooltip
              content={<TimeshareTooltip prevClose={prevClose} />}
              cursor={{ stroke: 'var(--chart-axis)', strokeDasharray: '3 3' }}
              isAnimationActive={false}
            />
            {base ? <ReferenceLine y={base} stroke="var(--chart-axis)" strokeDasharray="4 4" /> : null}
            {/* Line 默认 1500ms 入场动画：每 30 秒自动刷新都会重放一遍，关掉 */}
            <Line type="monotone" dataKey="price" stroke="hsl(var(--foreground))" strokeWidth={2} dot={false} isAnimationActive={false} name="价格" />
            <Line type="monotone" dataKey="avgPrice" stroke={DEA_COLOR} strokeWidth={1.5} dot={false} connectNulls isAnimationActive={false} name="均价" />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <p className="text-xs text-muted-text">
        {data.tradeDate ? `${data.tradeDate} · ` : ''}
        昨收 {num(prevClose)}
        {change == null ? '' : ` · 现价较昨收 ${withSign(change.toFixed(2), '%')}`}
        {data.suspended ? ' · 停牌' : data.isTrading ? ' · 交易中' : ' · 非交易时段'}
      </p>
    </div>
  );
};

type ChartFrame = {
  kline?: KlineResponse;
  timeshare?: TimeshareResponse;
  error?: ParsedApiError;
};

type ChartState = ChartFrame & {
  /** 这一帧属于哪个「代码+周期」；渲染时据此判断是否还在首次加载。 */
  key: string;
};

/** 取数不碰 state。MACD 与 KDJ 常驻，所以一次把两组指标都要回来，切周期才重取。 */
const fetchFrame = async (code: string, period: ChartPeriod): Promise<ChartFrame> => {
  try {
    if (period === 'timeshare') {
      return { timeshare: await chartApi.timeshare(code) };
    }
    return {
      kline: await chartApi.kline(code, {
        period,
        limit: BAR_LIMIT,
        indicators: ['ma', 'macd', 'kdj'],
      }),
    };
  } catch (error) {
    return { error: getParsedApiError(error) };
  }
};

type MultiPeriodChartProps = {
  code: string;
  /** 自动刷新间隔（毫秒）。不传则只在挂载与切换周期时取数。 */
  refreshIntervalMs?: number;
};

const TAB_CLASS = 'rounded-full border px-3 py-1 text-xs transition-colors';

export const MultiPeriodChart: React.FC<MultiPeriodChartProps> = ({ code, refreshIntervalMs }) => {
  const [period, setPeriod] = useState<ChartPeriod>('day');
  const [state, setState] = useState<ChartState>({ key: '' });
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  /**
   * 拖动标记只用 ref，不用 state：state 一变就会把三栏图表重渲染一遍，而那一帧正好
   * 挡在「第一次位移」前面，表现就是「滑了一下 K 线才动」。ref 既能在 pointerdown
   * 同一拍内立刻生效，又不额外触发渲染。
   */
  const draggingRef = useRef(false);
  const generation = useRef(0);
  const viewKey = `${code}|${period}`;

  const load = useCallback((key: string, target: string, nextPeriod: ChartPeriod) => {
    generation.current += 1;
    const issuedAt = generation.current;
    void fetchFrame(target, nextPeriod).then((frame) => {
      if (generation.current !== issuedAt) return;
      setState({ key, ...frame });
    });
  }, []);

  useEffect(() => {
    load(viewKey, code, period);
    if (!refreshIntervalMs) return;
    const timer = window.setInterval(() => {
      // 页面切到后台时不刷；key 不变所以是静默更新，不会闪 loading 也不会重置视窗。
      if (document.visibilityState === 'visible') {
        load(viewKey, code, period);
      }
    }, refreshIntervalMs);
    return () => window.clearInterval(timer);
  }, [load, viewKey, code, period, refreshIntervalMs]);

  const matched = state.key === viewKey;
  const frame: ChartFrame = matched ? state : {};
  const loading = !matched;

  const allCandles = useMemo<Candlestick[]>(() => {
    const base = (frame.kline?.rows ?? []).filter(hasOhlc);
    return base.flatMap((row, index) => {
      const { open, high, low, close } = row;
      if (open == null || high == null || low == null || close == null) return [];
      const previous = Math.abs(base[index - 1]?.macd ?? 0);
      return [{ ...row, open, high, low, close, macdGrowing: Math.abs(row.macd ?? 0) >= previous }];
    });
  }, [frame.kline]);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const {
    view,
    reset: resetView,
    showAll,
    isZoomed,
    onPointerDown,
    onPointerMove,
    onPointerUp,
    onKeyDown,
  } = useViewport(allCandles.length, containerRef);
  const { size, rightOffset } = view;
  const rows = useMemo(() => {
    const end = allCandles.length - rightOffset;
    return allCandles.slice(Math.max(0, end - size), end);
  }, [allCandles, size, rightOffset]);

  const handleHover = useCallback((index: number | null) => {
    if (draggingRef.current) return;
    setHoverIndex(index);
  }, []);

  const beginDrag = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    draggingRef.current = true;
    onPointerDown(event);
  }, [onPointerDown]);

  const endDrag = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    draggingRef.current = false;
    onPointerUp(event);
  }, [onPointerUp]);

  const labelRow = (hoverIndex != null ? rows[hoverIndex] : undefined) ?? rows.at(-1);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <div role="tablist" aria-label="K 线周期" className="flex flex-wrap gap-1.5">
          {PERIOD_TABS.map((tab) => (
            <button
              key={tab.key}
              type="button"
              role="tab"
              aria-selected={period === tab.key}
              onClick={() => setPeriod(tab.key)}
              className={cn(
                TAB_CLASS,
                period === tab.key
                  ? 'border-[hsl(var(--primary))] bg-[hsl(var(--primary))]/10 font-medium text-[hsl(var(--primary))]'
                  : 'border-border text-secondary-text hover:border-[hsl(var(--primary))]/40 hover:text-foreground',
              )}
            >
              {tab.label}
            </button>
          ))}
        </div>
        {period !== 'timeshare' && rows.length ? (
          <div className="ml-auto flex items-center gap-1.5">
            <Button variant="ghost" size="xsm" className="text-xxs" onClick={showAll}>
              全览
            </Button>
            {isZoomed ? (
              <Button variant="outline" size="xsm" className="text-xxs" onClick={resetView}>
                复位
              </Button>
            ) : null}
          </div>
        ) : null}
      </div>

      {loading ? <Loading /> : null}
      {frame.error ? <ApiErrorAlert error={frame.error} /> : null}

      {frame.timeshare ? <TimesharePane data={frame.timeshare} /> : null}

      {frame.kline && rows.length ? (
        <div className="space-y-2">
          <MainPaneLabel row={labelRow} />
          <div
            ref={containerRef}
            tabIndex={0}
            role="group"
            aria-label="K 线图，可拖动或双指左右滑动平移，双指上下滑动或滚轮缩放，方向键平移、加减键缩放"
            className="cursor-grab touch-none select-none rounded-xl py-1 active:cursor-grabbing focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan/50"
            onPointerDown={beginDrag}
            onPointerMove={onPointerMove}
            onPointerUp={endDrag}
            onPointerCancel={endDrag}
            onKeyDown={onKeyDown}
          >
            <KlinePane rows={rows} syncId={viewKey} onHover={handleHover} />
            <SubPane>
              <PaneLabel
                title="MACD"
                items={[
                  { label: 'DIF', value: num(labelRow?.dif, 2), color: DIF_COLOR },
                  { label: 'DEA', value: num(labelRow?.dea, 2), color: DEA_COLOR },
                  { label: 'MACD', value: num(labelRow?.macd, 2), color: 'hsl(var(--secondary-text))' },
                ]}
              />
              <MacdPane rows={rows} syncId={viewKey} onHover={handleHover} />
            </SubPane>
            <SubPane>
              <PaneLabel
                title="KDJ"
                items={[
                  { label: 'K', value: num(labelRow?.k, 2), color: K_COLOR },
                  { label: 'D', value: num(labelRow?.d, 2), color: D_COLOR },
                  { label: 'J', value: num(labelRow?.j, 2), color: J_COLOR },
                ]}
              />
              <KdjPane rows={rows} syncId={viewKey} onHover={handleHover} />
            </SubPane>
          </div>
          <p className="text-xs text-muted-text">
            {rows[0]?.time} ~ {rows.at(-1)?.time} · 显示 {rows.length}/{allCandles.length} 根
            {frame.kline.adjust === 'qfq' ? ' · 前复权' : frame.kline.adjust === 'hfq' ? ' · 后复权' : ' · 不复权'}
            {frame.kline.todaySource === 'snapshot'
              ? ' · 末根为盘中实时合成，尚未收盘'
              : frame.kline.todaySource === 'upstream'
                ? ' · 末根本期未走完'
                : ''}
          </p>
          <p className="text-xxs leading-relaxed text-muted-text">
            读法：蜡烛涨空心 / 跌实心；MACD 柱按正负取色，柱体放大为实心、收缩为空心。
            <br />
            操作：拖动或双指左右滑动平移；双指上下滑动（向上看更广、向下看更少）或滚轮缩放；
            聚焦图表后 ← → 平移、+ − 缩放、Home 复位。
          </p>
        </div>
      ) : null}

      {frame.kline && !allCandles.length ? (
        <p className="py-10 text-center text-sm text-muted-text">暂无 K 线数据</p>
      ) : null}
    </div>
  );
};
