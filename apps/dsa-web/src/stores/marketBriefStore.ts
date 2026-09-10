import { create } from 'zustand';

import { marketBriefApi } from '../api/marketBrief';
import type { MarketBriefJob, MarketBriefPayload } from '../api/marketBrief';
import { getParsedApiError } from '../api/error';
import type { ParsedApiError } from '../api/error';

const POLL_INTERVAL_MS = 2000;

export type MarketBriefNotice = {
  id: number;
  tone: 'success' | 'warning' | 'error';
  title: string;
  message: string;
};

interface MarketBriefState {
  job: MarketBriefJob | null;
  result: MarketBriefPayload | null;
  submitError: ParsedApiError | null;
  notice: MarketBriefNotice | null;

  submit: (force?: boolean) => Promise<void>;
  setResult: (payload: MarketBriefPayload | null) => void;
  dismissNotice: () => void;
}

/**
 * 消息面汇总的跨路由任务 store。
 *
 * 轮询定时器挂在模块作用域而不是组件里，所以用户切走、组件卸载后轮询仍在继续；
 * 任务结束时写入 notice，由全局挂载的通知组件弹出。
 */
let pollTimer: ReturnType<typeof setInterval> | null = null;
let noticeSeq = 1;

function stopPolling(): void {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

export const useMarketBriefStore = create<MarketBriefState>((set, get) => {
  const finish = (job: MarketBriefJob): void => {
    stopPolling();
    if (job.status === 'succeeded' && job.result) {
      const degraded = job.result.degraded;
      set({
        job,
        result: job.result,
        notice: {
          id: noticeSeq++,
          tone: degraded ? 'warning' : 'success',
          title: degraded ? `${job.modeLabel}消息面汇总已完成（降级）` : `${job.modeLabel}消息面汇总已完成`,
          message: degraded
            ? job.result.degradedReason || '模型未能完整解读，已给出关键词覆盖。'
            : `已汇总 ${job.result.itemCount} 条消息，点击查看结果。`,
        },
      });
    } else {
      set({
        job,
        notice: {
          id: noticeSeq++,
          tone: 'error',
          title: '消息面汇总失败',
          message: job.error || job.message || '汇总未能完成，请重试。',
        },
      });
    }
  };

  const poll = async (jobId: string): Promise<void> => {
    try {
      const job = await marketBriefApi.getJob(jobId);
      set({ job });
      if (job.status === 'succeeded' || job.status === 'failed') {
        finish(job);
      }
    } catch (error) {
      // 轮询失败不立刻判定任务失败：可能只是网络抖动，下个周期再试。
      // 只有后端已过期清除（404）才停止轮询并提示。
      const parsed = getParsedApiError(error);
      if (parsed.status === 404) {
        stopPolling();
        set({
          notice: {
            id: noticeSeq++,
            tone: 'error',
            title: '消息面汇总中断',
            message: '后台任务已过期或丢失，请重新发起汇总。',
          },
        });
      }
    }
  };

  return {
    job: null,
    result: null,
    submitError: null,
    notice: null,

    submit: async (force = false) => {
      const current = get().job;
      if (current && (current.status === 'queued' || current.status === 'running')) {
        return;
      }
      stopPolling();
      set({ submitError: null });
      try {
        const job = await marketBriefApi.submitJob(force);
        set({ job });
        pollTimer = setInterval(() => {
          void poll(job.jobId);
        }, POLL_INTERVAL_MS);
        // 立即拉一次，缩短首屏空窗
        void poll(job.jobId);
      } catch (error) {
        set({ submitError: getParsedApiError(error) });
      }
    },

    setResult: (payload) => set({ result: payload }),
    dismissNotice: () => set({ notice: null }),
  };
});
