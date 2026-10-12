import type React from 'react';

import type { BriefEvidence } from '../../api/marketBrief';

interface BriefEvidenceButtonProps {
  /** 本条结论引用的条目编号（evidence_ids） */
  ids: number[];
  /** 编号 -> 原文条目；只统计能解析到的条目 */
  evidenceById: Map<number, BriefEvidence>;
  onOpen: (items: BriefEvidence[]) => void;
}

/**
 * 事实层每条结论后面的「信源 N」入口。
 *
 * N 以**能在 evidenceById 里解析到的条目数**为准，而非 ids 原始长度：旧 payload 没有
 * evidence 数组时解析不到，按钮整体不渲染（返回 null），避免给出一个点开为空的入口。
 */
export const BriefEvidenceButton: React.FC<BriefEvidenceButtonProps> = ({ ids, evidenceById, onOpen }) => {
  const resolved = ids
    .map((id) => evidenceById.get(id))
    .filter((entry): entry is BriefEvidence => Boolean(entry));

  if (resolved.length === 0) {
    return null;
  }

  return (
    <button
      type="button"
      onClick={() => onOpen(resolved)}
      aria-label={`查看 ${resolved.length} 条引用信源原文`}
      className="inline-flex items-center gap-1 rounded-md border border-cyan/25 bg-cyan/10 px-1.5 py-0.5 text-xxs text-cyan transition-colors hover:bg-cyan/20"
    >
      信源 <span className="tabular-nums">{resolved.length}</span>
    </button>
  );
};

export default BriefEvidenceButton;
