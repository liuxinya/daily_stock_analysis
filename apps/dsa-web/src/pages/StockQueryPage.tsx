import type React from 'react';
import { useCallback, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { AppPage, PageHeader } from '../components/common';
import { StockAutocomplete } from '../components/StockAutocomplete';
import { StockChartPanel } from '../components/stock-chart/StockChartPanel';
import type { Market } from '../types/stockIndex';

/**
 * 股票查询看板：实时行情 + 多周期图表。
 *
 * 内容区整块交给 `StockChartPanel` —— 与全局行情弹窗是同一个组件，所以页面与弹窗
 * 的指标口径、刷新节奏、空态与报错表现不会分叉。
 *
 * 行情走项目自有链路（data_provider 多源 + fallback），K 线与分时走
 * `/api/v1/chart/*`（后端取同花顺并在服务端算 MACD / KDJ）。
 */

const DEFAULT_CODE = '000960';

/**
 * 图表链路覆盖的市场。
 *
 * 搜索框是全站共用的 `StockAutocomplete`，下拉里能选到港股 / 美股 / 日股 / 韩股，
 * 但 `/api/v1/chart/*` 只验证过 A 股与场内基金，所以这里要挡一道并明确告知，
 * 而不是静默什么都不发生。
 */
const SUPPORTED_MARKETS = new Set<Market>(['CN', 'ETF', 'BSE']);

const UNSUPPORTED_HINT = '行情图表目前只支持 A 股与场内基金（6 位代码），港股 / 美股 / 日韩股暂未接入。';

/** 行情与图表接口要的都是裸 6 位代码。 */
const normalizeCode = (raw: string): string => {
  const digits = raw.replace(/\D/g, '');
  return digits.slice(0, 6);
};

/** 外部入口用 ?code= 指定标的，非 A 股 6 位代码时回落到默认股。 */
const resolveEntryCode = (raw: string | null): string => {
  const normalized = normalizeCode(raw ?? '');
  return normalized.length === 6 ? normalized : DEFAULT_CODE;
};

export const StockQueryPage: React.FC = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  /**
   * 当前标的直接从 `?code=` 推导，不另存一份 state。
   *
   * 这样「页面内查询」与「外部带 ?code= 进来」（如行情弹窗的「在股票查询页打开」、
   * CCTV 分析的「查询」）走的是同一条路径，不需要再写一个 URL→state 的同步 effect，
   * 也顺带让地址可分享、刷新后仍停在同一只股票上。
   */
  const code = resolveEntryCode(searchParams.get('code'));
  /**
   * 搜索框只承载「正在搜什么」，不回显当前标的 —— 当前是哪只股票由下方行情头部的
   * 名称与代码交代，选中后清空输入框，可以直接接着搜下一只。
   */
  const [query, setQuery] = useState('');
  const [hint, setHint] = useState<string | null>(null);

  const handleChange = useCallback((value: string) => {
    setQuery(value);
    // 一改输入就撤掉提示，别让旧错误一直挂着
    setHint(null);
  }, []);

  const handleSubmit = useCallback(
    (raw: string, _name?: string, _source?: string, metadata?: { market?: Market }) => {
      // 优先看 market，而不是看位数：三星 005930.KS 去掉非数字也是 6 位，
      // 只按位数判会把它当成 A 股的 005930 画出来。
      if (metadata?.market && !SUPPORTED_MARKETS.has(metadata.market)) {
        setHint(UNSUPPORTED_HINT);
        return;
      }
      const next = normalizeCode(raw);
      if (next.length !== 6) {
        // 保留输入让用户自己改，不要静默清掉他刚敲的东西
        setHint(UNSUPPORTED_HINT);
        return;
      }
      setHint(null);
      setQuery('');
      setSearchParams({ code: next }, { replace: true });
    },
    [setSearchParams],
  );

  return (
    <AppPage>
      <PageHeader
        title="股票查询"
        description="实时行情与多周期 K 线：分时 / 日 / 周 / 月 / 季，可叠加 MACD、KDJ"
      />

      <div className="mb-4 mt-4 max-w-xl">
        <StockAutocomplete
          value={query}
          onChange={handleChange}
          onSubmit={handleSubmit}
          placeholder="输入股票代码或名称，如 000960"
          ariaLabel="股票查询"
          className={hint ? 'border-danger/50' : undefined}
        />
        {hint ? (
          <p role="alert" className="mt-2 text-xs text-danger">
            {hint}
          </p>
        ) : null}
      </div>

      <StockChartPanel code={code} variant="page" />
    </AppPage>
  );
};

export default StockQueryPage;
