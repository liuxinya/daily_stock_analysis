import type React from 'react';
import { useState, useEffect, useCallback, useRef } from 'react';
import type { ParsedApiError } from '../../api/error';
import { getParsedApiError } from '../../api/error';
import { ApiErrorAlert, Badge, Card } from '../common';
import { DashboardPanelHeader, DashboardStateBlock } from '../dashboard';
import { newsSearchApi } from '../../api/newsSearch';
import type { NewsSearchItem } from '../../api/newsSearch';
import type { ReportLanguage } from '../../types/analysis';
import { getReportText, normalizeReportLanguage } from '../../utils/reportLanguage';
import { normalizeStockCode } from '../../utils/stockCode';

interface ReportNewsProps {
  /** 报告所属股票代码；与 stockName 一起决定新闻搜索的关键词 */
  stockCode?: string;
  /** 股票名称，优先级高于代码——各站点按公司名收录新闻 */
  stockName?: string;
  limit?: number;
  language?: ReportLanguage;
}

/** 与「新闻分析 → 新闻搜索」页保持一致的时间窗。 */
const NEWS_SEARCH_WINDOW_DAYS = 7;

const NEWS_SOURCE_TEXT = {
  zh: {
    sourceLabel: '新闻搜索 · 全部平台',
    sourceHint: '来源：按股票多平台聚合的站内新闻搜索；是否用于分析以输入数据块为准。',
    partialFailed: '部分平台本次未返回结果，内容可能不完整；可点「刷新」重试。',
  },
  en: {
    sourceLabel: 'News search · all platforms',
    sourceHint: 'Source: multi-platform keyword news search; analysis input is shown in Input Blocks.',
    partialFailed: 'Some platforms returned nothing this time, so coverage may be incomplete. Use Refresh to retry.',
  },
  ko: {
    sourceLabel: '뉴스 검색 · 전체 플랫폼',
    sourceHint: '출처: 종목 기준 다중 플랫폼 뉴스 검색이며, 분석 사용 여부는 입력 데이터 블록 기준입니다.',
    partialFailed: '일부 플랫폼이 이번에 결과를 반환하지 않아 내용이 불완전할 수 있습니다. 새로고침으로 다시 시도하세요.',
  },
} as const;

/**
 * 资讯区组件 - 终端风格
 *
 * 取数走「新闻分析 → 新闻搜索」的全平台聚合接口：优先用股票名称、没有名称时才回退到
 * 纯数字代码做关键词（上游按公司名收录新闻，且后端按关键词字面命中做相关性过滤）。
 * 这里取的是**实时搜索结果**，不是本次分析实际喂给 LLM 的资讯——后者只体现在输入数据块里，
 * 所以文案上要把这条界限讲清楚。
 */
export const ReportNews: React.FC<ReportNewsProps> = ({
  stockCode,
  stockName,
  limit = 8,
  language = 'zh',
}) => {
  const reportLanguage = normalizeReportLanguage(language);
  const text = getReportText(reportLanguage);
  const sourceText = NEWS_SOURCE_TEXT[reportLanguage];

  const [isLoading, setIsLoading] = useState(false);
  const [items, setItems] = useState<NewsSearchItem[]>([]);
  const [failedPlatforms, setFailedPlatforms] = useState<string[]>([]);
  const [error, setError] = useState<ParsedApiError | null>(null);

  const keyword = (stockName || '').trim() || normalizeStockCode(stockCode || '');

  // 切换股票时要丢弃上一只股票的迟到响应，否则会出现「标题是茅台、列表是中科曙光」。
  const requestIdRef = useRef(0);

  const fetchNews = useCallback(async () => {
    if (!keyword) return;
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    setIsLoading(true);
    setError(null);

    try {
      const response = await newsSearchApi.searchAll({
        q: keyword,
        days: NEWS_SEARCH_WINDOW_DAYS,
        limit,
      });
      if (requestIdRef.current !== requestId) return;
      setItems(response.items || []);
      setFailedPlatforms(response.failedPlatforms || []);
    } catch (err) {
      if (requestIdRef.current !== requestId) return;
      setError(getParsedApiError(err));
    } finally {
      if (requestIdRef.current === requestId) setIsLoading(false);
    }
  }, [keyword, limit]);

  useEffect(() => {
    setItems([]);
    setFailedPlatforms([]);
    setError(null);

    if (keyword) {
      fetchNews();
    }
  }, [keyword, fetchNews]);

  if (!keyword) {
    return null;
  }

  return (
    <Card variant="bordered" padding="md" className="home-panel-card">
      <DashboardPanelHeader
        eyebrow={text.newsFeed}
        title={text.relatedNews}
        actions={(
          <div className="flex items-center gap-2">
            {isLoading ? (
              <div className="home-spinner h-3.5 w-3.5 animate-spin border-2" aria-hidden="true" />
            ) : null}
            <span className="home-accent-chip px-2 py-0.5 text-xs text-muted-text">
              {sourceText.sourceLabel}
            </span>
            <button
              type="button"
              onClick={() => void fetchNews()}
              className="home-accent-link text-xs"
              aria-label={text.refresh}
            >
              {text.refresh}
            </button>
          </div>
        )}
      />
      <p className="mb-3 text-xs leading-5 text-muted-text">
        {sourceText.sourceHint}
      </p>

      {error && !isLoading && (
        <ApiErrorAlert
          error={error}
          actionLabel={text.retry}
          onAction={() => void fetchNews()}
          dismissLabel={text.dismiss}
        />
      )}

      {isLoading && !error && (
        <DashboardStateBlock
          compact
          loading
          title={text.loadingNews}
        />
      )}

      {!isLoading && !error && items.length === 0 && (
        <DashboardStateBlock
          compact
          title={text.noNews}
          description={text.noNewsDescription}
          icon={(
            <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 14l-7-7m0 0l-7 7m7-7v18" />
            </svg>
          )}
        />
      )}

      {!isLoading && !error && items.length > 0 && (
        <div className="space-y-3 text-left">
          {items.map((item, index) => (
            <div
              key={`${item.url || item.title}-${index}`}
              className="home-subpanel home-news-item group p-4"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1 min-w-0 text-left">
                  <p className="home-news-title text-sm font-medium leading-6 text-foreground text-left">
                    {item.title}
                  </p>
                  {item.summary && (
                    <p className="home-news-snippet mt-2 text-sm leading-6 text-secondary-text text-left overflow-hidden [display:-webkit-box] [-webkit-line-clamp:3] [-webkit-box-orient:vertical]">
                      {item.summary}
                    </p>
                  )}
                  {/* 多平台聚合后要标出来源，否则同一个关键词下分不清哪条来自哪家 */}
                  {item.platformLabel && (
                    <div className="mt-2">
                      <Badge size="sm" variant="info">{item.platformLabel}</Badge>
                    </div>
                  )}
                </div>
                {item.url && (
                  <a
                    href={item.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="home-accent-pill-link shrink-0 whitespace-nowrap px-2.5 py-1 text-xs"
                    aria-label={text.openLink}
                  >
                    {text.openLink}
                    <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={2}
                        d="M14 3h7m0 0v7m0-7L10 14"
                      />
                    </svg>
                  </a>
                )}
              </div>
            </div>
          ))}

        </div>
      )}

      {!isLoading && !error && failedPlatforms.length > 0 && (
        <p className="mt-3 text-xs leading-5 text-muted-text">
          {sourceText.partialFailed}
        </p>
      )}
    </Card>
  );
};
