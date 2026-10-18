import type React from 'react';
import { Loader2 } from 'lucide-react';
import type { UiTextKey } from '../../i18n/uiText';
import type { IndustryFundsJob } from '../../types/industryFunds';
import { cn } from '../../utils/cn';

type Translate = (key: UiTextKey) => string;

type Props = {
  job: IndustryFundsJob;
  t: Translate;
};

/**
 * 刷新进度条。
 *
 * 刷新要并行打 6 个上游再调一次模型（30~90 秒），所以必须让用户看到它确实在动 ——
 * 否则用户会以为点坏了，然后反复点，把上游打到风控。
 */
export const RefreshProgress: React.FC<Props> = ({ job, t }) => {
  const percent = Math.round(Math.max(0, Math.min(1, job.progress ?? 0)) * 100);
  const failed = job.status === 'failed';

  return (
    <div
      className={cn(
        'rounded-2xl border p-3',
        failed ? 'border-danger/30 bg-danger/5' : 'border-cyan/25 bg-cyan/5'
      )}
      role="status"
      aria-live="polite"
    >
      <div className="mb-2 flex items-center justify-between gap-3 text-xs">
        <span className="flex items-center gap-2 text-foreground">
          {failed ? null : <Loader2 className="h-3.5 w-3.5 animate-spin text-cyan" aria-hidden="true" />}
          {failed ? t('industryFunds.refreshFailed') : job.phaseLabel || job.message}
        </span>
        <span className="tabular-nums text-secondary-text">{percent}%</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-elevated">
        <div
          className={cn('h-full rounded-full transition-all duration-500', failed ? 'bg-danger' : 'bg-cyan')}
          style={{ width: `${percent}%` }}
        />
      </div>
      {failed && job.error ? (
        <p className="mt-2 break-all text-[11px] text-danger">{job.error}</p>
      ) : null}
    </div>
  );
};
