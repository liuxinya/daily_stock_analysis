import { create } from 'zustand';

import { cctvNewsApi } from '../api/cctvNews';
import type {
  CctvAnalysisSubmit,
  CctvNewsAnalysisJob,
  CctvNewsAnalysisResponse,
} from '../api/cctvNews';
import { getParsedApiError } from '../api/error';
import type { ParsedApiError } from '../api/error';

const POLL_INTERVAL_MS = 2000;

export type CctvAnalysisNotice = {
  id: number;
  tone: 'success' | 'warning' | 'error';
  title: string;
  message: string;
};

interface CctvAnalysisState {
  /** 当前后台任务（进行中或最近一次）。 */
  job: CctvNewsAnalysisJob | null;
  /** 成功后的分析结果，供分析页渲染。 */
  result: CctvNewsAnalysisResponse | null;
  submitError: ParsedApiError | null;
  /** 待展示的全局通知，任何路由都能读到。 */
  notice: CctvAnalysisNotice | null;

  submit: (params: CctvAnalysisSubmit) => Promise<void>;
  dismissNotice: () => void;
  clearResult: () => void;
}

/**
 * CCTV 新闻分析的跨路由任务 store。
 *
 * 轮询定时器挂在模块作用域而不是组件里，所以用户切到别的路由、分析页组件卸载后，
 * 轮询仍在继续；任务结束时写入 notice，由全局挂载的通知组件弹出——无论用户当前
 * 在哪个页面都能收到。
 */
let pollTimer: ReturnType<typeof setInterval> | null = null;
let noticeSeq = 1;

function stopPolling(): void {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

export const useCctvAnalysisStore = create<CctvAnalysisState>((set, get) => {
  const finish = (job: CctvNewsAnalysisJob): void => {
    stopPolling();
    if (job.status === 'succeeded' && job.result) {
      const degraded = job.result.degraded;
      set({
        job,
        result: job.result,
        notice: {
          id: noticeSeq++,
          tone: degraded ? 'warning' : 'success',
          title: degraded ? 'CCTV 新闻分析已完成（降级）' : 'CCTV 新闻分析已完成',
          message: degraded
            ? job.result.degradedReason || '模型未能完整解读，已给出关键词覆盖。'
            : `已分析 ${job.result.itemCount} 篇新闻，点击查看结果。`,
        },
      });
    } else {
      set({
        job,
        notice: {
          id: noticeSeq++,
          tone: 'error',
          title: 'CCTV 新闻分析失败',
          message: job.error || job.message || '分析未能完成，请重试。',
        },
      });
    }
  };

  const poll = async (jobId: string): Promise<void> => {
    try {
      const job = await cctvNewsApi.getAnalysisJob(jobId);
      set({ job });
      if (job.status === 'succeeded' || job.status === 'failed') {
        finish(job);
      }
    } catch (error) {
      // 轮询失败不立刻判定任务失败：可能只是一次网络抖动，下个周期再试。
      // 但若任务已被后端过期清除（404），停止轮询并提示。
      const parsed = getParsedApiError(error);
      if (parsed.status === 404) {
        stopPolling();
        set({
          notice: {
            id: noticeSeq++,
            tone: 'error',
            title: 'CCTV 新闻分析中断',
            message: '后台任务已过期或丢失，请重新发起分析。',
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

    submit: async (params) => {
      // 已有进行中的任务时不重复提交
      const current = get().job;
      if (current && (current.status === 'queued' || current.status === 'running')) {
        return;
      }
      stopPolling();
      set({ submitError: null, result: null });
      try {
        const job = await cctvNewsApi.submitAnalysisJob(params);
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

    dismissNotice: () => set({ notice: null }),
    clearResult: () => set({ result: null, job: null }),
  };
});
