import { create } from 'zustand';

import { sanitizeFollowUpStockCode } from '../utils/chatFollowUp';
import { normalizeStockCode } from '../utils/stockCode';

export type StockChartTarget = {
  /** 已归一化的 A 股 / 场内基金 6 位代码。 */
  code: string;
  name: string;
};

interface StockChartState {
  target: StockChartTarget | null;
  /** 打开行情弹窗。代码不合法时静默忽略，调用方不必自己判空。 */
  open: (code: string | null | undefined, name?: string | null) => void;
  close: () => void;
}

/**
 * 全局行情弹窗 store。
 *
 * 做成 store 而不是层层传 props：「点个股看行情」散落在新闻分析、CCTV 分析、行业
 * 资金等多个互不相干的页面，每处都自己管一份 open/close 状态只会让同一个交互出现
 * N 份实现。各处只调 `open(code, name)`，弹窗本体由 App 全局挂一个。
 *
 * 代码在入口处统一校验、统一归一化：这些代码不少来自模型推断，而行情与图表接口只
 * 认 A 股 / 场内基金的裸 6 位码。先过项目既有的格式规则，再用 `normalizeStockCode`
 * 剥掉交易所前后缀（与后端 `normalize_stock_code` 同一口径），最后仍不是 6 位数字
 * 就不开弹窗 —— 不把一段不确定的文本丢给接口。
 */
export const resolveStockChartCode = (code: string | null | undefined): string | null => {
  const sanitized = sanitizeFollowUpStockCode(code ?? null);
  if (!sanitized) return null;
  const normalized = normalizeStockCode(sanitized);
  return /^\d{6}$/.test(normalized) ? normalized : null;
};

export const useStockChartStore = create<StockChartState>((set) => ({
  target: null,
  open: (code, name) => {
    const normalized = resolveStockChartCode(code);
    if (!normalized) return;
    set({ target: { code: normalized, name: (name ?? '').trim() || normalized } });
  },
  close: () => set({ target: null }),
}));

/** 判断某个代码能否打开行情弹窗 —— 调用方据此决定是否渲染可点入口。 */
export const canOpenStockChart = (code: string | null | undefined): boolean =>
  resolveStockChartCode(code) !== null;
