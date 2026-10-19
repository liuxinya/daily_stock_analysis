import type React from 'react';

/**
 * 蜡烛图的单根形状。
 *
 * 挂法：`<Bar dataKey={(row) => [row.low, row.high]} shape={<Candle />} />`。
 * recharts 给的 `dataKey` 是 `[low, high]` 区间，于是 `y..y+height` 对应
 * `high..low`，由此推出价格到像素的比例，不必再去拿坐标轴的 scale。
 *
 * 画法用「涨=空心、跌=实心」的传统方式：红绿在 deutan 色盲下 ΔE≈8，光靠颜色分不
 * 出方向，实心/空心才是不依赖颜色的那一层编码。
 */

/** 画一根蜡烛只需要 OHLC，用结构化最小类型，任何带这四个字段的行都能直接喂进来。 */
export type CandleDatum = {
  open: number;
  close: number;
  high: number;
  low: number;
};

type CandleShapeProps = {
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  payload?: CandleDatum;
};

export const Candle: React.FC<CandleShapeProps> = ({ x = 0, y = 0, width = 0, height = 0, payload }) => {
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

  // 不套 <g>：recharts 本来就把 shape 渲染在自己的 <Layer> 里，少一层就是每屏少 100+ 个节点。
  return (
    <>
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
    </>
  );
};
