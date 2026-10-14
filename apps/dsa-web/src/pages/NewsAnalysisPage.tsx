import type React from 'react';
import { Suspense, lazy, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Search } from 'lucide-react';

import { AppPage, EmptyState, Loading } from '../components/common';
import { cn } from '../utils/cn';

// 两个视图各自较重（消息面汇总 / CCTV 分析组件），保持按 tab 懒加载，避免首屏把两份都拉下来。
const RealtimeNewsView = lazy(() => import('./RealtimeNewsView'));
const CctvNewsView = lazy(() => import('./CctvNewsView'));

const TABS = [
  { key: 'realtime', label: '实时新闻', title: '实时新闻 - DSA' },
  { key: 'search', label: '新闻搜索', title: '新闻搜索 - DSA' },
  { key: 'cctv', label: 'CCTV新闻', title: 'CCTV 新闻 - DSA' },
] as const;

type TabKey = (typeof TABS)[number]['key'];

const DEFAULT_TAB: TabKey = 'realtime';

const resolveTab = (raw: string | null): TabKey =>
  TABS.some((tab) => tab.key === raw) ? (raw as TabKey) : DEFAULT_TAB;

/**
 * 「新闻分析」页：把实时新闻与 CCTV 新闻聚合到同一个页面，用顶部 tab 切换。
 *
 * - 实时新闻：各财经站点快讯 + AI 汇总与分析（原 `/realtime-news`）。
 * - 新闻搜索：占位，功能待定。
 * - CCTV新闻：《新闻联播》文字稿与 AI 解读（原 `/cctv-news`）。
 *
 * 当前 tab 存在 URL 的 `tab` 参数里，便于外部入口直接深链到某个 tab（例如 CCTV 分析
 * 完成通知跳 `/news-analysis?tab=cctv&ctab=analysis`）。
 */
const NewsAnalysisPage: React.FC = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab = resolveTab(searchParams.get('tab'));

  useEffect(() => {
    const label = TABS.find((tab) => tab.key === activeTab)?.title;
    document.title = label ?? '新闻分析 - DSA';
  }, [activeTab]);

  /** 切外层 tab 时只保留 tab 参数，顺带清掉 CCTV 的内层 `ctab`，避免状态串台。 */
  const handleSelectTab = (key: TabKey) => {
    if (key === activeTab) return;
    setSearchParams(key === DEFAULT_TAB ? {} : { tab: key }, { replace: true });
  };

  return (
    <AppPage className="space-y-5">
      <div role="tablist" aria-label="新闻分析视图" className="flex flex-wrap gap-2">
        {TABS.map((tab) => (
          <button
            key={tab.key}
            type="button"
            role="tab"
            aria-selected={activeTab === tab.key}
            onClick={() => handleSelectTab(tab.key)}
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

      <Suspense fallback={<Loading label="正在加载" />}>
        {activeTab === 'realtime' ? <RealtimeNewsView /> : null}
        {activeTab === 'search' ? (
          <EmptyState
            icon={<Search className="h-6 w-6" />}
            title="新闻搜索"
            description="该功能待定，暂未开放。"
          />
        ) : null}
        {activeTab === 'cctv' ? <CctvNewsView /> : null}
      </Suspense>
    </AppPage>
  );
};

export default NewsAnalysisPage;
