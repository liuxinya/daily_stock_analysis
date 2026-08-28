import type React from 'react';
import { ExternalLink, FileText, Newspaper } from 'lucide-react';

import { Badge, Card, EmptyState, Loading } from '../common';
import type { CctvNewsItem } from '../../api/cctvNews';
import { shortDate, weekdayLabel } from './format';

interface CctvNewsListProps {
  items: CctvNewsItem[];
  loading: boolean;
  onOpenTranscript: (item: CctvNewsItem) => void;
  emptyDescription?: string;
}

/**
 * 新闻卡片列表：标题 + 文字总结（从正文确定性截取），点"查看文字稿"看全文。
 *
 * 摘要不经过 LLM，因此不会出现原文没有的内容。
 */
export const CctvNewsList: React.FC<CctvNewsListProps> = ({
  items,
  loading,
  onOpenTranscript,
  emptyDescription,
}) => {
  if (loading) {
    return <Loading label="正在加载新闻联播文字稿" />;
  }

  if (items.length === 0) {
    return (
      <EmptyState
        icon={<Newspaper className="h-6 w-6" />}
        title="这一天还没有文字稿"
        description={emptyDescription ?? '央视网通常在当天 20:00 之后放出文字稿，可以先点右上角「同步新闻」。'}
      />
    );
  }

  return (
    <ul className="space-y-3">
      {items.map((item) => (
        <li key={item.id}>
          <Card variant="bordered" padding="md" hoverable>
            <div className="flex flex-col gap-2">
              <div className="flex flex-wrap items-center gap-2 text-xxs text-muted-text">
                <Badge variant="info" size="sm">
                  第 {item.seq} 条
                </Badge>
                <span className="tabular-nums">
                  {shortDate(item.newsDate)} {weekdayLabel(item.newsDate)}
                </span>
                <span>{item.charCount} 字</span>
              </div>

              <h3 className="text-sm font-semibold leading-relaxed text-foreground">{item.title}</h3>

              {item.summary ? (
                <p className="text-sm leading-relaxed text-secondary-text">{item.summary}</p>
              ) : null}

              <div className="flex items-center gap-3 pt-1">
                <button
                  type="button"
                  onClick={() => onOpenTranscript(item)}
                  className="home-accent-link inline-flex items-center gap-1.5 text-sm"
                  aria-label={`查看文字稿：${item.title}`}
                >
                  <FileText className="h-3.5 w-3.5" aria-hidden="true" />
                  查看文字稿
                </button>
                <a
                  href={item.url}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="inline-flex items-center gap-1.5 text-sm text-muted-text transition-colors hover:text-foreground"
                  aria-label={`在央视网打开：${item.title}`}
                >
                  <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                  央视网原文
                </a>
              </div>
            </div>
          </Card>
        </li>
      ))}
    </ul>
  );
};
