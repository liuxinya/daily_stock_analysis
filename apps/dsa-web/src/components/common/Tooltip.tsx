import type React from 'react';
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { cn } from '../../utils/cn';

interface TooltipProps {
  content: React.ReactNode;
  children: React.ReactNode;
  side?: 'top' | 'bottom' | 'right';
  focusable?: boolean;
  className?: string;
  contentClassName?: string;
  /** 允许鼠标移进气泡（可复制里面文字），移出后延迟关闭 */
  interactive?: boolean;
}

type TooltipStyle = {
  top: number;
  left: number;
};

export const Tooltip: React.FC<TooltipProps> = ({
  content,
  children,
  side = 'top',
  focusable = false,
  className = '',
  contentClassName = '',
  interactive = false,
}) => {
  const triggerRef = useRef<HTMLSpanElement | null>(null);
  const tooltipRef = useRef<HTMLSpanElement | null>(null);
  const tooltipId = useId();
  const [open, setOpen] = useState(false);
  const closeTimer = useRef<number | null>(null);
  const [resolvedSide, setResolvedSide] = useState<'top' | 'bottom' | 'right'>(side);
  const [style, setStyle] = useState<TooltipStyle>({ top: 0, left: 0 });

  const updatePosition = useCallback(() => {
    const trigger = triggerRef.current;
    const tooltip = tooltipRef.current;
    if (!trigger || !tooltip) {
      return;
    }

    const triggerRect = trigger.getBoundingClientRect();
    const tooltipRect = tooltip.getBoundingClientRect();
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;
    const gap = 10;
    const margin = 8;

    let nextSide = side;
    let top = 0;
    let left = 0;
    if (side === 'right') {
      // 右侧：垂直与触发器居中，右边放不下就翻到左边。
      top = triggerRect.top + triggerRect.height / 2 - tooltipRect.height / 2;
      left = triggerRect.right + gap;
      if (left + tooltipRect.width > viewportWidth - margin) {
        left = triggerRect.left - tooltipRect.width - gap;
      }
    } else {
      top = side === 'top' ? triggerRect.top - tooltipRect.height - gap : triggerRect.bottom + gap;
      if (side === 'top' && top < margin) {
        nextSide = 'bottom';
        top = triggerRect.bottom + gap;
      } else if (side === 'bottom' && top + tooltipRect.height > viewportHeight - margin) {
        nextSide = 'top';
        top = triggerRect.top - tooltipRect.height - gap;
      }
      left = triggerRect.left + triggerRect.width / 2 - tooltipRect.width / 2;
    }

    left = Math.max(margin, Math.min(left, viewportWidth - tooltipRect.width - margin));
    top = Math.max(margin, Math.min(top, viewportHeight - tooltipRect.height - margin));

    setResolvedSide(nextSide);
    setStyle({ top, left });
  }, [side]);

  useLayoutEffect(() => {
    if (!open) {
      return;
    }

    const frameId = window.requestAnimationFrame(() => {
      updatePosition();
    });

    return () => {
      window.cancelAnimationFrame(frameId);
    };
  }, [open, content, updatePosition]);

  useEffect(() => {
    if (!open) {
      return;
    }

    const handleViewportChange = () => updatePosition();
    window.addEventListener('resize', handleViewportChange);
    window.addEventListener('scroll', handleViewportChange, true);

    return () => {
      window.removeEventListener('resize', handleViewportChange);
      window.removeEventListener('scroll', handleViewportChange, true);
    };
  }, [open, updatePosition]);

  if (!content) {
    return <>{children}</>;
  }

  return (
    <>
      <span
        ref={triggerRef}
        className={cn('inline-flex', className)}
        onMouseEnter={() => {
          if (closeTimer.current !== null) {
            window.clearTimeout(closeTimer.current);
            closeTimer.current = null;
          }
          setOpen(true);
        }}
        onMouseLeave={() => {
          if (!interactive) {
            setOpen(false);
            return;
          }
          // 可交互气泡：留一点时间让鼠标从触发器移到气泡上，别一离开就消失。
          closeTimer.current = window.setTimeout(() => setOpen(false), 160);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            setOpen(false);
          }
        }}
        tabIndex={focusable ? 0 : undefined}
        aria-describedby={open ? tooltipId : undefined}
      >
        {children}
      </span>

      {typeof document !== 'undefined' && open
        ? createPortal(
            <span
              ref={tooltipRef}
              id={tooltipId}
              role="tooltip"
              style={{
                position: 'fixed',
                top: style.top,
                left: style.left,
              }}
              onMouseEnter={() => {
                if (closeTimer.current !== null) {
                  window.clearTimeout(closeTimer.current);
                  closeTimer.current = null;
                }
              }}
              onMouseLeave={() => {
                if (interactive) setOpen(false);
              }}
              className={cn(
                interactive ? 'pointer-events-auto select-text' : 'pointer-events-none',
                'z-[120] min-w-max max-w-[18rem] rounded-xl border border-border/70 bg-elevated/95 px-3 py-1.5 text-xs leading-5 text-foreground shadow-[0_16px_40px_rgba(3,8,20,0.18)] backdrop-blur-xl',
                resolvedSide === 'top' ? 'origin-bottom' : 'origin-top',
                contentClassName,
              )}
            >
              {content}
            </span>,
            document.body,
          )
        : null}
    </>
  );
};
