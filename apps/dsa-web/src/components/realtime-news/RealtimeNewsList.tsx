import type React from 'react';
import { ExternalLink, Radio } from 'lucide-react';

import { Badge, Card, EmptyState, Loading } from '../common';
import type { RealtimeNewsItem } from '../../api/realtimeNews';
import { absoluteTime, timeAgo } from './format';

interface RealtimeNewsListProps {
  items: RealtimeNewsItem[];
  loading: boolean;
  /** 平台暂无可用新闻源时展示占位文案，而不是「加载失败」 */
  unavailable?: boolean;
  platformLabel: string;
  /** 聚合模式下才需要在每条上标出来源平台 */
  showPlatformBadge?: boolean;
}

/**
 * 快讯列表：标题 + 摘要 + 来源 + 相对时间 + 原文链接。
 *
 * 上游快讯接口只提供这几个字段，没有缩略图、阅读数和作者，所以不做这些占位。
 * 新浪等平台不返回 url，此时不渲染原文链接。
 */
export const RealtimeNewsList: React.FC<RealtimeNewsListProps> = ({
  items,
  loading,
  unavailable = false,
  platformLabel,
  showPlatformBadge = false,
}) => {
  if (loading) {
    return <Loading label={`正在加载${platformLabel}快讯`} />;
  }

  if (unavailable) {
    return (
      <EmptyState
        icon={<Radio className="h-6 w-6" />}
        title={`${platformLabel}暂无可用新闻源`}
        description="该平台目前没有公开的快讯接口，接入后这里会自动显示最新内容。"
      />
    );
  }

  if (items.length === 0) {
    return (
      <EmptyState
        icon={<Radio className="h-6 w-6" />}
        title="暂时没有拉到快讯"
        description="上游可能刚好没有新内容，稍后再刷新试试。"
      />
    );
  }

  return (
    <ul className="space-y-3">
      {items.map((item, index) => (
        <li key={item.url || `${item.platform}-${item.title}-${index}`}>
          <Card variant="bordered" padding="md" hoverable>
            <div className="flex flex-col gap-2">
              <h3 className="text-sm font-semibold leading-relaxed text-foreground">{item.title}</h3>

              {item.summary ? (
                <p className="line-clamp-3 text-sm leading-relaxed text-secondary-text">{item.summary}</p>
              ) : null}

              <div className="flex flex-wrap items-center gap-2 text-xxs text-muted-text">
                {showPlatformBadge ? (
                  <Badge variant="info" size="sm">
                    {item.platformLabel}
                  </Badge>
                ) : null}
                {item.tag ? <Badge size="sm">{item.tag}</Badge> : null}
                {item.publishedAt ? (
                  <span className="tabular-nums" aria-label={absoluteTime(item.publishedAt)}>
                    {timeAgo(item.publishedAt)}
                  </span>
                ) : null}
                {item.url ? (
                  <a
                    href={item.url}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="inline-flex items-center gap-1.5 text-xxs text-muted-text transition-colors hover:text-foreground"
                    aria-label={`打开原文：${item.title}`}
                  >
                    <ExternalLink className="h-3 w-3" aria-hidden="true" />
                    原文
                  </a>
                ) : null}
              </div>
            </div>
          </Card>
        </li>
      ))}
    </ul>
  );
};
