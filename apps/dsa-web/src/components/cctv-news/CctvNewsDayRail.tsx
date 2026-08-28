import type React from 'react';
import { CalendarDays } from 'lucide-react';

import { cn } from '../../utils/cn';
import type { CctvNewsDaySummary } from '../../api/cctvNews';
import { shortDate, weekdayLabel } from './format';

interface CctvNewsDayRailProps {
  days: CctvNewsDaySummary[];
  selectedDate: string | null;
  onSelect: (newsDate: string) => void;
  className?: string;
}

/** 左侧日期导航：近一个月有文字稿的日期，按倒序排列。 */
export const CctvNewsDayRail: React.FC<CctvNewsDayRailProps> = ({
  days,
  selectedDate,
  onSelect,
  className = '',
}) => {
  if (days.length === 0) {
    return null;
  }

  return (
    <nav
      className={cn('flex gap-2 overflow-x-auto pb-1 lg:flex-col lg:overflow-visible lg:pb-0', className)}
      aria-label="新闻日期"
    >
      {days.map((day) => {
        const isActive = day.newsDate === selectedDate;
        return (
          <button
            key={day.newsDate}
            type="button"
            onClick={() => onSelect(day.newsDate)}
            aria-current={isActive ? 'true' : undefined}
            className={cn(
              'flex shrink-0 items-center justify-between gap-3 rounded-xl border px-3 py-2 text-left text-sm transition-all lg:w-full',
              isActive
                ? 'border-cyan/40 bg-cyan/10 text-foreground shadow-soft-card'
                : 'border-border/60 bg-card/60 text-secondary-text hover:border-border hover:bg-hover',
            )}
          >
            <span className="flex items-center gap-2">
              <CalendarDays
                className={cn('h-3.5 w-3.5', isActive ? 'text-cyan' : 'text-muted-text')}
                aria-hidden="true"
              />
              <span className="font-medium tabular-nums">{shortDate(day.newsDate)}</span>
              <span className="text-xxs text-muted-text">{weekdayLabel(day.newsDate)}</span>
            </span>
            <span className="text-xxs tabular-nums text-muted-text">{day.itemCount} 条</span>
          </button>
        );
      })}
    </nav>
  );
};
