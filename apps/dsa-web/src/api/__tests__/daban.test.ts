import { beforeEach, describe, expect, it, vi } from 'vitest';

import { DABAN_NOT_GENERATED, dabanApi } from '../daban';

const { get, post } = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));

vi.mock('../index', () => ({
  default: { get, post },
}));

const SNAKE_OVERVIEW = {
  schema_version: 'daban-v1',
  trade_date: '2026-10-20',
  metrics: { zt_count: 52, yzt_premium: 2.77 },
  lanes: { chen: { position_cap_label: '30–50%', actionable: [{ reasons: [{ text: 'x', principle_ids: ['C04'] }] }] } },
  // 以下三个字典的键是 id，不能被转成 k01 / m1。
  principles: { K01: { author: '92科比', ref: '#3' }, C04: { author: '陈小群' } },
  mode_labels: { M1: '龙头·分歧介入' },
  style_labels: { chen: '陈小群打法' },
};

describe('dabanApi', () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
  });

  it('converts snake_case but keeps id-keyed dictionaries intact', async () => {
    get.mockResolvedValue({ data: SNAKE_OVERVIEW });
    const overview = await dabanApi.getOverview();
    expect(get).toHaveBeenCalledWith('/api/v1/daban/overview', { params: undefined });
    expect(overview.metrics.ztCount).toBe(52);
    expect(overview.lanes.chen.positionCapLabel).toBe('30–50%');
    expect(overview.lanes.chen.actionable[0].reasons[0].principleIds).toEqual(['C04']);
    expect(Object.keys(overview.principles)).toEqual(['K01', 'C04']);
    expect(overview.modeLabels).toEqual({ M1: '龙头·分歧介入' });
  });

  it('passes the trade date as snake_case query', async () => {
    get.mockResolvedValue({ data: SNAKE_OVERVIEW });
    await dabanApi.getOverview('2026-10-19');
    expect(get).toHaveBeenCalledWith('/api/v1/daban/overview', { params: { trade_date: '2026-10-19' } });
  });

  it('turns 404 into the not-generated marker', async () => {
    get.mockRejectedValue({ response: { status: 404 } });
    await expect(dabanApi.getOverview()).rejects.toMatchObject({ code: DABAN_NOT_GENERATED });
  });

  it('keeps principle ids inside a finished job result', async () => {
    get.mockResolvedValue({ data: { job_id: 'abc', status: 'succeeded', result: SNAKE_OVERVIEW } });
    const job = await dabanApi.getJob('abc');
    expect(job.jobId).toBe('abc');
    expect(Object.keys(job.result?.principles ?? {})).toEqual(['K01', 'C04']);
  });

  it('submits a historical date in the body', async () => {
    post.mockResolvedValue({ data: { job: null, reused: false, cooling_down: true, retry_after_seconds: 5, message: 'wait' } });
    const result = await dabanApi.submitJob('2026-10-19');
    expect(post).toHaveBeenCalledWith('/api/v1/daban/jobs', { trade_date: '2026-10-19' });
    expect(result.coolingDown).toBe(true);
    expect(result.retryAfterSeconds).toBe(5);
  });

  it('returns null when no job is active', async () => {
    get.mockRejectedValue({ response: { status: 404 } });
    await expect(dabanApi.getActiveJob()).resolves.toBeNull();
  });
});
