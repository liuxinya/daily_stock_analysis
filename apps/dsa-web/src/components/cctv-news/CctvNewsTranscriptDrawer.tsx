import type React from 'react';
import { ExternalLink } from 'lucide-react';

import { ApiErrorAlert, Drawer, Loading } from '../common';
import type { ParsedApiError } from '../../api/error';
import type { CctvNewsItem } from '../../api/cctvNews';
import { shortDate, weekdayLabel } from './format';

interface CctvNewsTranscriptDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  item: CctvNewsItem | null;
  loading: boolean;
  error: ParsedApiError | null;
}

/**
 * 文字稿详情抽屉。
 *
 * 正文按段落渲染而不是交给 markdown：文字稿是纯文本，用 markdown 反而会把
 * 正文里的符号当语法解析。
 */
export const CctvNewsTranscriptDrawer: React.FC<CctvNewsTranscriptDrawerProps> = ({
  isOpen,
  onClose,
  item,
  loading,
  error,
}) => {
  const paragraphs = (item?.content ?? '')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);

  return (
    <Drawer isOpen={isOpen} onClose={onClose} title="新闻联播文字稿" width="max-w-2xl">
      {error ? <ApiErrorAlert error={error} className="mb-4" /> : null}
      {loading ? <Loading label="正在加载文字稿" /> : null}

      {!loading && item ? (
        <article className="space-y-4">
          <header className="space-y-2 border-b border-border/60 pb-3">
            <div className="flex flex-wrap items-center gap-2 text-xxs text-muted-text">
              <span className="tabular-nums">
                {shortDate(item.newsDate)} {weekdayLabel(item.newsDate)}
              </span>
              <span>第 {item.seq} 条</span>
              <span>{item.charCount} 字</span>
            </div>
            <h2 className="text-base font-semibold leading-relaxed text-foreground">{item.title}</h2>
          </header>

          {paragraphs.length > 0 ? (
            <div className="space-y-3">
              {paragraphs.map((paragraph, index) => (
                <p
                  key={`${item.id}-${index}`}
                  className="text-sm leading-7 text-secondary-text"
                >
                  {paragraph}
                </p>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-text">这条新闻没有文字稿正文。</p>
          )}

          <a
            href={item.url}
            target="_blank"
            rel="noreferrer noopener"
            className="home-accent-pill-link inline-flex items-center gap-1.5 text-sm"
          >
            <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
            在央视网查看原文
          </a>
        </article>
      ) : null}
    </Drawer>
  );
};
