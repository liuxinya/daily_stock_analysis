import type React from 'react';
import { Loader2 } from 'lucide-react';

import { Card } from '../common';
import type { CctvNewsAnalysisJob } from '../../api/cctvNews';

interface CctvAnalysisProgressProps {
  job: CctvNewsAnalysisJob;
}

/**
 * 后台分析进度条。
 *
 * 明确告诉用户：共多少篇新闻、当前处于哪个阶段（读取/分析/汇总）、分析到第几段，
 * 让长耗时任务"看得见在动"。
 */
export const CctvAnalysisProgress: React.FC<CctvAnalysisProgressProps> = ({ job }) => {
  const percent = Math.round((job.progress || 0) * 100);
  const chunkText =
    job.totalChunks > 0 && (job.phase === 'analyzing' || job.phase === 'finalizing')
      ? `第 ${job.currentChunk}/${job.totalChunks} 段`
      : '';

  return (
    <Card variant="bordered" padding="md">
      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <Loader2 className="h-4 w-4 animate-spin text-cyan" aria-hidden="true" />
          <span className="text-sm font-medium text-foreground">{job.phaseLabel || '分析中'}</span>
          {chunkText ? <span className="text-xxs text-muted-text">{chunkText}</span> : null}
          <span className="ml-auto text-xxs tabular-nums text-muted-text">{percent}%</span>
        </div>

        <div
          className="h-2 w-full overflow-hidden rounded-full bg-elevated/70"
          role="progressbar"
          aria-valuenow={percent}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="分析进度"
        >
          <div
            className="h-full rounded-full bg-primary-gradient transition-all duration-500"
            style={{ width: `${Math.max(4, percent)}%` }}
          />
        </div>

        <p className="text-sm leading-relaxed text-secondary-text">
          {job.totalItems > 0 ? `共 ${job.totalItems} 篇新闻 · ` : ''}
          {job.message || '正在准备…'}
        </p>
        <p className="text-xxs text-muted-text">
          分析在后台进行，可以切换到其他页面，完成后会通知你。
        </p>
      </div>
    </Card>
  );
};
