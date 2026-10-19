/**
 * 从 recharts 的鼠标事件里取出当前下标。
 *
 * 单独成文件是为了可单测：组件文件受 react-refresh 约束，只应导出组件。
 *
 * recharts v3 的 `activeTooltipIndex` 类型是 `number | TooltipIndex | undefined`，
 * 其中 `TooltipIndex` 是**字符串**。只认 number 的话十字光标永远解析不出下标，
 * 读数行就会一直停在最后一根上。`activeIndex` 是同一个值的别名，留作兜底。
 */

export type RechartsMouseState = {
  activeTooltipIndex?: number | string | null;
  activeIndex?: number | string | null;
  isTooltipActive?: boolean;
};

export const pickHoverIndex = (state: unknown): number | null => {
  const next = state as RechartsMouseState | undefined | null;
  if (!next || next.isTooltipActive === false) return null;
  const raw = next.activeTooltipIndex ?? next.activeIndex;
  if (raw == null) return null;
  const index = typeof raw === 'number' ? raw : Number.parseInt(raw, 10);
  return Number.isInteger(index) && index >= 0 ? index : null;
};
