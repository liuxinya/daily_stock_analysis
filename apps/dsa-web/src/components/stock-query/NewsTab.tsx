import type React from 'react';
import { ExternalLink } from 'lucide-react';
import type { SentimentItem } from '../../api/baiduFinance';
import { text } from './format';

/**
 * 资讯：AI 舆情列表。
 *
 * 刻意不渲染 benefitType。实测该字段只有 0/1 两种取值，且 1 同时出现在
 * 「扣非净利润创四年新高」和「主力资金净卖出 1.67 亿」上，显然不是利好/利空，
 * 更像「是否含观点」。语义不明就不标 —— 在财经数据上猜一个多空标签，比不标更糟。
 */

/** 上游给的是秒级 unix 时间戳字符串。 */
const formatTime = (value?: string): string => {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds <= 0) return '--';
  const date = new Date(seconds * 1000);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
};

export const NewsTab: React.FC<{ items?: SentimentItem[] }> = ({ items }) => {
  const list = (items ?? []).filter((item) => item.title || item.abstract);

  if (!list.length) {
    return <p className="py-10 text-center text-sm text-muted-text">暂无资讯</p>;
  }

  return (
    <ul className="space-y-3">
      {list.map((item, index) => (
        <li
          key={`${item.title ?? index}-${index}`}
          className="rounded-2xl border border-border bg-elevated/40 p-4 transition-colors hover:border-[hsl(var(--primary))]/40"
        >
          <h3 className="mb-1.5 text-sm font-medium leading-snug text-foreground">{text(item.title)}</h3>
          {item.abstract ? (
            <p className="text-sm leading-relaxed text-secondary-text">{item.abstract}</p>
          ) : null}
          <p className="mt-2 flex flex-wrap items-center gap-x-3 text-xs text-muted-text">
            <span>{text(item.provider)}</span>
            <span>{formatTime(item.publishTime)}</span>
            {item.originUrl ? (
              <a
                href={item.originUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-[hsl(var(--primary))] hover:underline"
              >
                原文
                <ExternalLink className="h-3 w-3" aria-hidden />
              </a>
            ) : null}
          </p>
        </li>
      ))}
    </ul>
  );
};
