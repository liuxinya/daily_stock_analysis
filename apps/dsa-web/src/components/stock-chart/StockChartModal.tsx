import type React from 'react';
import { useCallback, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { ExternalLink, X } from 'lucide-react';

import { useStockChartStore } from '../../stores/stockChartStore';
import { StockChartPanel } from './StockChartPanel';

/**
 * 全局行情弹窗。
 *
 * 挂在 App 的 Routes 外（和 CctvAnalysisNotifier 同一层），这样切路由不会把弹窗卸载掉。
 *
 * 任何页面只要调 `useStockChartStore().open(code, name)` 就能唤出它，不必各自维护
 * 一份 open/close 状态，也不必把弹窗组件重复挂到每个面板里。
 *
 * z-index 取 55：压住页面内容与侧滑抽屉（50），但低于全局通知（60）和移动端导航
 * 抽屉（90）—— 那两者在弹窗开着时仍应可达。
 */

const CLOSE_LABEL = '关闭行情弹窗';

/** 同花顺个股页。弹窗里只有自有数据的图，想看资金/财务/股东那些就跳过去。 */
const THS_STOCK_PAGE = 'https://stockpage.10jqka.com.cn';

export const StockChartModal: React.FC = () => {
  const target = useStockChartStore((state) => state.target);
  const close = useStockChartStore((state) => state.close);

  const handleKeyDown = useCallback(
    (event: KeyboardEvent) => {
      if (event.key === 'Escape') close();
    },
    [close],
  );

  useEffect(() => {
    if (!target) return;
    document.addEventListener('keydown', handleKeyDown);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [target, handleKeyDown]);

  if (!target) return null;

  const titleId = 'stock-chart-modal-title';

  return createPortal(
    <div className="fixed inset-0 z-[55] flex items-end justify-center sm:items-center" role="presentation">
      <div
        className="absolute inset-0 bg-background/80 backdrop-blur-sm transition-opacity"
        onClick={close}
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="relative flex max-h-[92vh] w-full max-w-4xl flex-col overflow-hidden rounded-t-2xl border border-border/80 bg-card shadow-2xl sm:max-h-[90vh] sm:rounded-2xl"
      >
        <div className="flex items-start justify-between gap-3 border-b border-border/60 px-5 py-4">
          <div className="min-w-0">
            <span className="label-uppercase">QUOTE &amp; CHART</span>
            <h2 id={titleId} className="mt-1 flex flex-wrap items-baseline gap-2 text-lg font-semibold text-foreground">
              {target.name}
              <span className="text-sm font-normal tabular-nums text-muted-text">{target.code}</span>
            </h2>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <a
              href={`${THS_STOCK_PAGE}/${encodeURIComponent(target.code)}`}
              target="_blank"
              rel="noreferrer noopener"
              aria-label={`在同花顺新标签页打开 ${target.name}(${target.code})`}
              className="home-accent-pill-link inline-flex items-center gap-1.5 text-xs"
            >
              <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
              在同花顺打开
            </a>
            <button
              type="button"
              onClick={close}
              aria-label={CLOSE_LABEL}
              className="inline-flex h-9 w-9 items-center justify-center rounded-xl border border-border/70 bg-card/80 text-secondary-text transition-colors hover:bg-hover hover:text-foreground"
            >
              <X className="h-5 w-5" aria-hidden="true" />
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-5">
          <StockChartPanel code={target.code} variant="modal" />
        </div>
      </div>
    </div>,
    document.body,
  );
};
