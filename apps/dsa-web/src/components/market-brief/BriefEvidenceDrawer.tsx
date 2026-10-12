import type React from 'react';
import { ExternalLink } from 'lucide-react';

import { Badge, Drawer } from '../common';
import type { BriefEvidence } from '../../api/marketBrief';
import { shortDateTime } from './format';

interface BriefEvidenceDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  items: BriefEvidence[];
}

/**
 * 事实层结论的「信源」原文抽屉。
 *
 * 列出该条结论引用到的 N 条资讯：标题、来源、时间、摘要，以及**仅当有真实链接时**的
 * 「原文」外链（无链接条目的 url 已在后端过滤成空串，这里不渲染死链）。
 */
export const BriefEvidenceDrawer: React.FC<BriefEvidenceDrawerProps> = ({ isOpen, onClose, items }) => {
  return (
    <Drawer isOpen={isOpen} onClose={onClose} title="引用信源原文" width="max-w-2xl">
      {items.length === 0 ? (
        <p className="text-sm text-muted-text">这条结论没有可展示的信源原文。</p>
      ) : (
        <ul className="space-y-4">
          {items.map((item) => (
            <li key={item.id} className="space-y-2 border-b border-border/60 pb-4 last:border-b-0 last:pb-0">
              <div className="flex flex-wrap items-center gap-2 text-xxs text-muted-text">
                <span className="tabular-nums">[{item.id}]</span>
                {item.source ? <Badge size="sm">{item.source}</Badge> : null}
                <span className="tabular-nums">{shortDateTime(item.publishedAt)}</span>
              </div>
              <h3 className="text-sm font-semibold leading-relaxed text-foreground">{item.title}</h3>
              {item.summary ? (
                <p className="text-sm leading-7 text-secondary-text">{item.summary}</p>
              ) : null}
              {item.url ? (
                <a
                  href={item.url}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="home-accent-pill-link inline-flex items-center gap-1.5 text-sm"
                >
                  <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                  查看原文
                </a>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </Drawer>
  );
};

export default BriefEvidenceDrawer;
