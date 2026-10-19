import type React from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { RefreshCw } from 'lucide-react';

import { ApiErrorAlert, Button, Loading } from '../common';
import { stocksApi } from '../../api/stocks';
import type { StockQuote } from '../../api/stocks';
import { getParsedApiError } from '../../api/error';
import type { ParsedApiError } from '../../api/error';
import { cn } from '../../utils/cn';
import { KeyValueGrid } from './shared';
import { text, trendClass, withSign } from './format';

/**
 * 实时行情头部。走项目自有行情链路（`/api/v1/stocks/{code}/quote`，data_provider
 * 多源 + fallback），与图表那条同花顺链路各自独立失败。
 *
 * 两种排版共用一份取数：`page` 给股票查询页（带股票名与成交量/额），`modal` 给
 * 行情弹窗（名字已在弹窗标题里，只留价格与四项盘口）。
 */

type PanelState = {
  /**
   * 这一帧属于哪只股票。
   *
   * 「是否在加载」由它与当前股票是否一致推导，不单独存 loading：effect 里同步
   * setState 会引发级联渲染（react-hooks/set-state-in-effect）。
   */
  code: string;
  quote?: StockQuote;
  error?: ParsedApiError;
  refreshedAt?: string;
};

const EMPTY_STATE: PanelState = { code: '' };

/** 取数不碰 state，方便在 effect 与手动刷新之间共用。 */
const fetchQuote = async (target: string): Promise<Omit<PanelState, 'code' | 'refreshedAt'>> => {
  try {
    return { quote: await stocksApi.getQuote(target) };
  } catch (error) {
    return { error: getParsedApiError(error) };
  }
};

type StockQuoteHeaderProps = {
  code: string;
  variant?: 'page' | 'modal';
  /** 自动刷新间隔（毫秒）。不传则只在挂载与换股时取数。 */
  refreshIntervalMs?: number;
};

export const StockQuoteHeader: React.FC<StockQuoteHeaderProps> = ({
  code,
  variant = 'page',
  refreshIntervalMs,
}) => {
  const [state, setState] = useState<PanelState>(EMPTY_STATE);
  // 换股时作废在途响应，避免迟到的数据画到另一只股票上。
  const generation = useRef(0);

  const refresh = useCallback((target: string, silent: boolean) => {
    generation.current += 1;
    const issuedAt = generation.current;

    void fetchQuote(target).then((frame) => {
      if (generation.current !== issuedAt) return;
      setState((prev) => {
        const next: PanelState = {
          ...frame,
          code: target,
          refreshedAt: new Date().toLocaleTimeString('zh-CN', { hour12: false }),
        };
        // 自动刷新失败时留住上一帧，否则界面会无故空掉。
        if (silent && prev.code === target && frame.error) {
          next.quote = prev.quote;
        }
        return next;
      });
    });
  }, []);

  useEffect(() => {
    if (!code) return;
    refresh(code, false);
    if (!refreshIntervalMs) return;
    const timer = window.setInterval(() => {
      // 页面切到后台时不刷：没人看的请求只是在给上游加压。
      if (document.visibilityState === 'visible') {
        refresh(code, true);
      }
    }, refreshIntervalMs);
    return () => window.clearInterval(timer);
  }, [code, refresh, refreshIntervalMs]);

  const view = state.code === code ? state : EMPTY_STATE;
  const quote = view.quote;
  const loading = view.code !== code;
  const isPage = variant === 'page';

  const stats = [
    { label: '今开', value: text(quote?.open?.toFixed(2)) },
    { label: '最高', value: text(quote?.high?.toFixed(2)) },
    { label: '最低', value: text(quote?.low?.toFixed(2)) },
    { label: '昨收', value: text(quote?.prevClose?.toFixed(2)) },
    ...(isPage
      ? [
          { label: '成交量', value: quote?.volume != null ? `${(quote.volume / 1e4).toFixed(2)}万` : '--' },
          { label: '成交额', value: quote?.amount != null ? `${(quote.amount / 1e8).toFixed(2)}亿` : '--' },
        ]
      : []),
  ];

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-foreground">实时行情</h3>
        <div className="flex items-center gap-2">
          <span className="text-xxs text-muted-text">
            {view.refreshedAt ? `${view.refreshedAt} 刷新` : ''}
            {refreshIntervalMs ? `${view.refreshedAt ? ' · ' : ''}每 ${refreshIntervalMs / 1000} 秒自动刷新` : ''}
          </span>
          <Button
            variant="outline"
            size="xsm"
            className="gap-1 text-xxs"
            onClick={() => refresh(code, true)}
            aria-label="立即刷新行情"
          >
            <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
            刷新
          </Button>
        </div>
      </div>

      {loading ? <Loading /> : null}
      {view.error ? <ApiErrorAlert error={view.error} /> : null}
      {quote ? (
        <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-3">
          <div>
            {isPage ? (
              <div className="flex items-baseline gap-2">
                <h2 className="text-2xl font-semibold text-foreground">{text(quote.stockName)}</h2>
                <span className="text-sm text-muted-text">{quote.stockCode}</span>
              </div>
            ) : null}
            <div className={cn('flex items-baseline gap-3', isPage && 'mt-1')}>
              <span
                className={cn(
                  'font-semibold tabular-nums',
                  isPage ? 'text-4xl' : 'text-3xl',
                  trendClass(quote.changePercent),
                )}
              >
                {quote.currentPrice?.toFixed(2) ?? '--'}
              </span>
              <span className={cn('tabular-nums', isPage ? 'text-lg' : 'text-base', trendClass(quote.changePercent))}>
                {withSign(quote.change?.toFixed(2))}
              </span>
              <span className={cn('tabular-nums', isPage ? 'text-lg' : 'text-base', trendClass(quote.changePercent))}>
                {withSign(quote.changePercent?.toFixed(2), '%')}
              </span>
            </div>
            {quote.updateTime ? (
              <p className="mt-1 text-xs text-muted-text">
                行情时间 {quote.updateTime.replace('T', ' ').slice(0, 19)}
              </p>
            ) : null}
          </div>
          <KeyValueGrid
            columns={isPage ? 3 : 4}
            className={cn('grow', isPage ? 'min-w-[280px]' : 'min-w-[260px]')}
            items={stats}
          />
        </div>
      ) : null}
    </div>
  );
};
