import type React from 'react';
import { Loader2 } from 'lucide-react';
import type { UiTextKey } from '../../i18n/uiText';
import type { DabanJob } from '../../types/daban';
import { cn } from '../../utils/cn';

type Translate = (key: UiTextKey, params?: Record<string, string | number>) => string;

/** 生成进度条：首次生成要回补历史，可能接近两分钟，必须让用户看到它在动。 */
export const JobProgress: React.FC<{ job: DabanJob; t: Translate }> = ({ job, t }) => {
  const percent = Math.round(Math.max(0, Math.min(1, job.progress ?? 0)) * 100);
  const failed = job.status === 'failed';
  return (
    <div
      className={cn('rounded-2xl border p-3', failed ? 'border-danger/30 bg-danger/5' : 'border-cyan/25 bg-cyan/5')}
      role="status"
      aria-live="polite"
    >
      <div className="mb-2 flex items-center justify-between gap-3 text-xs">
        <span className="flex items-center gap-2 text-foreground">
          {failed ? null : <Loader2 className="h-3.5 w-3.5 animate-spin text-cyan" aria-hidden="true" />}
          {failed ? t('dabanKing.generateFailed') : job.message || job.phaseLabel}
        </span>
        <span className="tabular-nums text-secondary-text">{percent}%</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-elevated">
        <div
          className={cn('h-full rounded-full transition-all duration-500', failed ? 'bg-danger' : 'bg-cyan')}
          style={{ width: `${percent}%` }}
        />
      </div>
      {failed && job.error ? <p className="mt-2 break-all text-[11px] text-danger">{job.error}</p> : null}
    </div>
  );
};
