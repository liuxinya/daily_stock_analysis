import type React from 'react';
import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { CheckCircle2, TriangleAlert, XCircle } from 'lucide-react';

import { useCctvAnalysisStore } from '../../stores/cctvAnalysisStore';
import { cn } from '../../utils/cn';

const TONE_STYLE: Record<string, { border: string; icon: React.ReactNode }> = {
  success: {
    border: 'border-success/40',
    icon: <CheckCircle2 className="h-5 w-5 text-success" aria-hidden="true" />,
  },
  warning: {
    border: 'border-warning/40',
    icon: <TriangleAlert className="h-5 w-5 text-warning" aria-hidden="true" />,
  },
  error: {
    border: 'border-danger/40',
    icon: <XCircle className="h-5 w-5 text-danger" aria-hidden="true" />,
  },
};

/**
 * 全局挂载的 CCTV 分析完成通知。
 *
 * 挂在 Shell 外层，因此后台分析结束时无论用户停在哪个路由都会弹出；点击可跳转到
 * 分析页查看结果。8 秒后自动消失。
 */
export const CctvAnalysisNotifier: React.FC = () => {
  const notice = useCctvAnalysisStore((state) => state.notice);
  const dismissNotice = useCctvAnalysisStore((state) => state.dismissNotice);
  const navigate = useNavigate();

  // 只做一件事：为当前通知起一个自动关闭的定时器。notice 本身就是显示与否的
  // 唯一来源，不再镜像成本地 state（那会在 effect 里同步 setState）。
  useEffect(() => {
    if (!notice) {
      return;
    }
    const timer = setTimeout(() => dismissNotice(), 8000);
    return () => clearTimeout(timer);
  }, [notice, dismissNotice]);

  if (!notice) {
    return null;
  }

  const tone = TONE_STYLE[notice.tone] ?? TONE_STYLE.success;
  const canOpen = notice.tone !== 'error';

  return (
    <div className="fixed bottom-6 right-6 z-[60] w-[min(92vw,22rem)] animate-slide-in-right">
      <div className={cn('glass-panel-lg flex gap-3 border p-4 shadow-soft-card-strong', tone.border)}>
        <div className="mt-0.5 shrink-0">{tone.icon}</div>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-foreground">{notice.title}</p>
          <p className="mt-0.5 text-sm leading-relaxed text-secondary-text">{notice.message}</p>
          <div className="mt-2 flex items-center gap-3">
            {canOpen ? (
              <button
                type="button"
                className="home-accent-link text-sm"
                onClick={() => {
                  dismissNotice();
                  navigate('/cctv-news');
                }}
              >
                查看结果
              </button>
            ) : null}
            <button
              type="button"
              className="text-sm text-muted-text transition-colors hover:text-foreground"
              onClick={() => dismissNotice()}
            >
              关闭
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
