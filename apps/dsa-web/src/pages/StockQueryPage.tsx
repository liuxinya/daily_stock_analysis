import type React from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { AppPage, ApiErrorAlert, Loading, PageHeader } from '../components/common';
import { StockAutocomplete } from '../components/StockAutocomplete';
import { AnalysisTab } from '../components/stock-query/AnalysisTab';
import { FinanceTab } from '../components/stock-query/FinanceTab';
import { FundFlowPanel } from '../components/stock-query/FundFlowPanel';
import { NewsTab } from '../components/stock-query/NewsTab';
import { PriceChart } from '../components/stock-query/PriceChart';
import { ProfileTab } from '../components/stock-query/ProfileTab';
import { TalksTab } from '../components/stock-query/TalksTab';
import { KeyValueGrid } from '../components/stock-query/shared';
import { text, trendClass, withSign } from '../components/stock-query/format';
import { baiduFinanceApi, isEtfCode, parseMarketData } from '../api/baiduFinance';
import type { KlineRow } from '../api/baiduFinance';
import { getParsedApiError } from '../api/error';
import type { ParsedApiError } from '../api/error';
import { stocksApi } from '../api/stocks';
import type { StockQuote } from '../api/stocks';
import { cn } from '../utils/cn';

/**
 * 股票查询看板。
 *
 * 数据来源分两路：
 * - 价格头部走项目自有行情链路（data_provider 多源 + fallback）。
 * - 其余七个 tab 走 /api/v1/baidu-finance 透传，因为项目现有 provider 拿不到
 *   A 股的三大报表、机构评级、大单拆分与股吧数据。
 *
 * 每个 tab 首次进入才请求，切回来复用已取到的结果；上游随时可能改字段或限流，
 * 因此单个 tab 失败只影响它自己。
 */

const TABS = [
  { key: 'overview', label: '概览' },
  { key: 'fundflow', label: '资金' },
  { key: 'profile', label: '简况' },
  { key: 'talks', label: '股评' },
  { key: 'news', label: '资讯' },
  { key: 'finance', label: '财务' },
  { key: 'analysis', label: '分析' },
] as const;

type TabKey = (typeof TABS)[number]['key'];

/** ETF 在百度上游没有公司简况 / 股吧 / 三表 / 评分，这些 tab 对 ETF 隐藏。 */
const ETF_HIDDEN_TABS = new Set<TabKey>(['profile', 'talks', 'finance', 'analysis']);

const DEFAULT_CODE = '000960';

/** 去掉市场前缀，百度接口要的是裸 6 位代码。 */
const normalizeCode = (raw: string): string => {
  const digits = raw.replace(/\D/g, '');
  return digits.slice(0, 6);
};

/** 外部入口用 ?code= 指定标的（如新闻分析里的“查询”），非 A 股 6 位代码时回落到默认股。 */
const resolveEntryCode = (raw: string | null): string => {
  const normalized = normalizeCode(raw ?? '');
  return normalized.length === 6 ? normalized : DEFAULT_CODE;
};

type TabState<T> = { data?: T; loading: boolean; error?: ParsedApiError };

const initialState = <T,>(): TabState<T> => ({ loading: false });

export const StockQueryPage: React.FC = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  // 首屏直接用 ?code= 初始化，避免先为默认股发一轮请求再作废。
  const [input, setInput] = useState(() => resolveEntryCode(searchParams.get('code')));
  const [code, setCode] = useState(() => resolveEntryCode(searchParams.get('code')));
  const [activeTab, setActiveTab] = useState<TabKey>('overview');

  // ETF 与股票走不同的上游接口，且 ETF 没有简况/财务等 tab，页面据此分流。
  const isEtf = useMemo(() => isEtfCode(code), [code]);
  const visibleTabs = useMemo(
    () => (isEtf ? TABS.filter((tab) => !ETF_HIDDEN_TABS.has(tab.key)) : TABS),
    [isEtf],
  );

  const [quote, setQuote] = useState<TabState<StockQuote>>(initialState);
  const [kline, setKline] = useState<TabState<{
    rows: KlineRow[];
    pressure?: string;
    support?: string;
    price?: number;
    levelsUpdateTime?: string;
  }>>(initialState);
  const [fundFlow, setFundFlow] = useState<TabState<Awaited<ReturnType<typeof baiduFinanceApi.fundFlow>>>>(initialState);
  const [company, setCompany] = useState<TabState<Awaited<ReturnType<typeof baiduFinanceApi.company>>>>(initialState);
  const [talks, setTalks] = useState<TabState<Awaited<ReturnType<typeof baiduFinanceApi.talks>>>>(initialState);
  const [news, setNews] = useState<TabState<Awaited<ReturnType<typeof baiduFinanceApi.sentiment>>>>(initialState);
  const [finance, setFinance] = useState<TabState<Awaited<ReturnType<typeof baiduFinanceApi.finance>>>>(initialState);
  const [mainOp, setMainOp] = useState<TabState<Awaited<ReturnType<typeof baiduFinanceApi.mainOperation>>>>(initialState);
  const [analysis, setAnalysis] = useState<TabState<Awaited<ReturnType<typeof baiduFinanceApi.analysis>>>>(initialState);

  /**
   * 换股时用于丢弃上一只股票的迟到响应。
   *
   * 自有行情接口要 3 秒上下，切换标的时上一次请求往往还在飞；若不作废，它落地后
   * 会把新股票的数据覆盖回旧的（实测过：600519 已渲染后被迟到的 000960 覆盖）。
   */
  const generation = useRef(0);

  const resetAll = useCallback(() => {
    generation.current += 1;
    setQuote(initialState);
    setKline(initialState);
    setFundFlow(initialState);
    setCompany(initialState);
    setTalks(initialState);
    setNews(initialState);
    setFinance(initialState);
    setMainOp(initialState);
    setAnalysis(initialState);
  }, []);

  const handleSubmit = useCallback((raw: string) => {
    const next = normalizeCode(raw);
    if (next.length !== 6) return;
    resetAll();
    setCode(next);
  }, [resetAll]);

  /**
   * 已停留在本页时再次带 ?code= 进来（例如从新闻分析连续点两只股票的“查询”），
   * 组件不会重挂，所以要在这里换标的。首屏那次参数与初始 code 相同，直接清掉即可，
   * 否则会把正在飞的首屏请求作废又不补发。
   */
  useEffect(() => {
    const raw = searchParams.get('code');
    if (raw === null) return;
    const next = normalizeCode(raw);
    if (next.length === 6 && next !== code) {
      setInput(next);
      handleSubmit(next);
    }
    setSearchParams({}, { replace: true });
  }, [code, handleSubmit, searchParams, setSearchParams]);

  /** 通用取数：已有数据或正在取时不重复发请求；迟到的响应直接丢掉。 */
  const load = useCallback(
    async <T,>(
      state: TabState<T>,
      setState: React.Dispatch<React.SetStateAction<TabState<T>>>,
      fetcher: () => Promise<T>,
    ) => {
      if (state.loading || state.data !== undefined) return;
      const issuedAt = generation.current;
      setState({ loading: true });
      try {
        const data = await fetcher();
        if (generation.current !== issuedAt) return;
        setState({ loading: false, data });
      } catch (error) {
        if (generation.current !== issuedAt) return;
        setState({ loading: false, error: getParsedApiError(error) });
      }
    },
    [],
  );

  // 价格头部在任何 tab 下都显示，所以跟着 code 走。
  useEffect(() => {
    void load(quote, setQuote, () => stocksApi.getQuote(code));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code]);

  /** 概览与分析都要用这份 K 线：分析 tab 的压力位/支撑位也来自同一个接口。 */
  const loadKline = useCallback(
    () => load(kline, setKline, async () => {
      const payload = await baiduFinanceApi.kline(code);
      return {
        rows: parseMarketData(payload.Result?.newMarketData),
        pressure: payload.Result?.analysisData?.yl,
        support: payload.Result?.analysisData?.zc,
        price: payload.Result?.analysisData?.price,
        levelsUpdateTime: payload.Result?.analysisData?.updateTime,
      };
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [kline, code],
  );

  useEffect(() => {
    if (activeTab === 'overview') {
      void loadKline();
      void load(fundFlow, setFundFlow, () => baiduFinanceApi.fundFlow(code));
    }
    if (activeTab === 'fundflow') {
      void load(fundFlow, setFundFlow, () => baiduFinanceApi.fundFlow(code));
    }
    if (activeTab === 'news') {
      void load(news, setNews, () => baiduFinanceApi.sentiment(code));
    }
    // 以下 tab 仅股票有，ETF 不请求（这些 tab 对 ETF 已隐藏）。
    if (!isEtf) {
      if (activeTab === 'profile') {
        void load(company, setCompany, () => baiduFinanceApi.company(code));
      }
      if (activeTab === 'talks') {
        void load(talks, setTalks, () => baiduFinanceApi.talks(code));
      }
      if (activeTab === 'finance') {
        void load(finance, setFinance, () => baiduFinanceApi.finance(code));
        void load(mainOp, setMainOp, () => baiduFinanceApi.mainOperation(code));
      }
      if (activeTab === 'analysis') {
        void load(analysis, setAnalysis, () => baiduFinanceApi.analysis(code));
        void loadKline();
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, code]);

  // 切到 ETF 时若仍停留在 ETF 不支持的 tab（如财务），回落到概览。
  useEffect(() => {
    if (isEtf && ETF_HIDDEN_TABS.has(activeTab)) {
      setActiveTab('overview');
    }
  }, [isEtf, activeTab]);

  const spread = fundFlow.data?.Result?.content?.fundFlowSpread?.result;
  const newCompany = company.data?.Result?.content?.newCompany as never;
  const sentimentItems = news.data?.Result?.[0]?.TplData?.aiSentimentXcxListInfo?.sentimentListInfo;
  const financeContent = finance.data?.Result?.content as never;

  const headerStats = useMemo(() => {
    const q = quote.data;
    return [
      { label: '今开', value: q?.open?.toFixed(2) ?? '--' },
      { label: '最高', value: q?.high?.toFixed(2) ?? '--' },
      { label: '最低', value: q?.low?.toFixed(2) ?? '--' },
      { label: '昨收', value: q?.prevClose?.toFixed(2) ?? '--' },
      {
        label: '成交量',
        value: q?.volume != null ? `${(q.volume / 1e4).toFixed(2)}万` : '--',
      },
      {
        label: '成交额',
        value: q?.amount != null ? `${(q.amount / 1e8).toFixed(2)}亿` : '--',
      },
    ];
  }, [quote.data]);

  return (
    <AppPage>
      <PageHeader title="股票查询" description="行情、资金、财务与分析看板" />

      <div className="mb-4 mt-4 max-w-xl">
        <StockAutocomplete
          value={input}
          onChange={setInput}
          onSubmit={(picked) => handleSubmit(picked)}
          placeholder="输入股票代码或名称，如 000960"
          ariaLabel="股票查询"
        />
      </div>

      <section className="mb-4 rounded-2xl border border-border bg-elevated/40 p-4">
        {quote.loading ? <Loading /> : null}
        {quote.error ? <ApiErrorAlert error={quote.error} /> : null}
        {quote.data ? (
          <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-4">
            <div>
              <div className="flex items-baseline gap-2">
                <h2 className="text-2xl font-semibold text-foreground">{text(quote.data.stockName)}</h2>
                <span className="text-sm text-muted-text">{quote.data.stockCode}</span>
              </div>
              <div className="mt-1 flex items-baseline gap-3">
                <span className={cn('text-4xl font-semibold tabular-nums', trendClass(quote.data.changePercent))}>
                  {quote.data.currentPrice?.toFixed(2) ?? '--'}
                </span>
                <span className={cn('text-lg tabular-nums', trendClass(quote.data.changePercent))}>
                  {withSign(quote.data.change?.toFixed(2))}
                </span>
                <span className={cn('text-lg tabular-nums', trendClass(quote.data.changePercent))}>
                  {withSign(quote.data.changePercent?.toFixed(2), '%')}
                </span>
              </div>
              {quote.data.updateTime ? (
                <p className="mt-1 text-xs text-muted-text">更新于 {quote.data.updateTime.replace('T', ' ').slice(0, 19)}</p>
              ) : null}
            </div>
            <KeyValueGrid columns={3} className="min-w-[280px] grow" items={headerStats} />
          </div>
        ) : null}
      </section>

      <div role="tablist" aria-label="股票信息分类" className="mb-4 flex flex-wrap gap-2">
        {visibleTabs.map((tab) => (
          <button
            key={tab.key}
            type="button"
            role="tab"
            aria-selected={activeTab === tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={cn(
              'rounded-full border px-4 py-1.5 text-sm transition-colors',
              activeTab === tab.key
                ? 'border-[hsl(var(--primary))] bg-[hsl(var(--primary))]/10 font-medium text-[hsl(var(--primary))]'
                : 'border-border text-secondary-text hover:border-[hsl(var(--primary))]/40 hover:text-foreground',
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div role="tabpanel">
        {activeTab === 'overview' ? (
          <div className="space-y-4">
            <section className="rounded-2xl border border-border bg-elevated/40 p-4">
              <h3 className="mb-3 text-sm font-semibold text-foreground">日 K 走势</h3>
              {kline.loading ? <Loading /> : null}
              {kline.error ? <ApiErrorAlert error={kline.error} /> : null}
              {kline.data ? (
                <PriceChart rows={kline.data.rows} pressure={kline.data.pressure} support={kline.data.support} />
              ) : null}
            </section>
            <section className="rounded-2xl border border-border bg-elevated/40 p-4">
              <h3 className="mb-3 text-sm font-semibold text-foreground">资金分布</h3>
              {fundFlow.loading ? <Loading /> : null}
              {fundFlow.error ? <ApiErrorAlert error={fundFlow.error} /> : null}
              {fundFlow.data ? <FundFlowPanel spread={spread} /> : null}
            </section>
          </div>
        ) : null}

        {activeTab === 'fundflow' ? (
          <section className="rounded-2xl border border-border bg-elevated/40 p-4">
            {fundFlow.loading ? <Loading /> : null}
            {fundFlow.error ? <ApiErrorAlert error={fundFlow.error} /> : null}
            {fundFlow.data ? <FundFlowPanel spread={spread} /> : null}
          </section>
        ) : null}

        {activeTab === 'profile' ? (
          <>
            {company.loading ? <Loading /> : null}
            {company.error ? <ApiErrorAlert error={company.error} /> : null}
            {company.data ? <ProfileTab data={newCompany} /> : null}
          </>
        ) : null}

        {activeTab === 'talks' ? (
          <>
            {talks.loading ? <Loading /> : null}
            {talks.error ? <ApiErrorAlert error={talks.error} /> : null}
            {talks.data ? (
              <TalksTab
                vote={talks.data.Result?.voteData as never}
                posts={(talks.data.Result?.content?.list ?? []) as never}
              />
            ) : null}
          </>
        ) : null}

        {activeTab === 'news' ? (
          <>
            {news.loading ? <Loading /> : null}
            {news.error ? <ApiErrorAlert error={news.error} /> : null}
            {news.data ? <NewsTab items={sentimentItems} /> : null}
          </>
        ) : null}

        {activeTab === 'finance' ? (
          <>
            {finance.loading ? <Loading /> : null}
            {finance.error ? <ApiErrorAlert error={finance.error} /> : null}
            {finance.data ? (
              <FinanceTab sheets={financeContent} mainOperation={mainOp.data?.Result as never} />
            ) : null}
          </>
        ) : null}

        {activeTab === 'analysis' ? (
          <>
            {analysis.loading || kline.loading ? <Loading /> : null}
            {analysis.error ? <ApiErrorAlert error={analysis.error} /> : null}
            {analysis.data ? (
              <AnalysisTab
                data={analysis.data.Result as never}
                klineRows={kline.data?.rows}
                pressure={kline.data?.pressure}
                support={kline.data?.support}
                price={kline.data?.price}
                levelsUpdateTime={kline.data?.levelsUpdateTime}
              />
            ) : null}
          </>
        ) : null}
      </div>

      <p className="mt-6 text-xs text-muted-text">
        除价格头部外，本页数据经后端代理取自百度财经的非公开接口，仅供本地查看；上游字段或可用性变化时本页会失效。
      </p>
    </AppPage>
  );
};

export default StockQueryPage;
