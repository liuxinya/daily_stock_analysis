import apiClient from './index';
import { toCamelCase } from './utils';

/**
 * 图表数据客户端（`/api/v1/chart/*`）。
 *
 * 后端走同花顺行情 CDN 并做字段归一化 + 指标计算，所以这里的类型是稳定契约，
 * 不像 `baiduFinance.ts` 那样只能照上游原样声明。响应为 snake_case，统一过
 * `toCamelCase`。
 */

const BASE = '/api/v1/chart';

/** K 线周期；`timeshare` 不是 K 线，走单独的分时接口。 */
export type KlinePeriod = 'day' | 'week' | 'month' | 'quarter';
export type ChartPeriod = 'timeshare' | KlinePeriod;

export type KlineAdjust = 'none' | 'qfq' | 'hfq';

export type ChartIndicator = 'ma' | 'macd' | 'kdj';

export type KlineBar = {
  time: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number;
  amount: number;
  turnoverRate: number | null;
  /** 未走完的末根（日 K 由盘口快照合成，周/月/季为本期进行中）。 */
  partial: boolean;
  ma5: number | null;
  ma10: number | null;
  ma20: number | null;
  dif: number | null;
  dea: number | null;
  macd: number | null;
  k: number | null;
  d: number | null;
  j: number | null;
};

export type KlineResponse = {
  code: string;
  name: string | null;
  period: KlinePeriod;
  /** 实际生效的复权口径：周/月/季上游只有前复权一份，会回报 `qfq`。 */
  adjust: KlineAdjust;
  indicators: ChartIndicator[];
  rows: KlineBar[];
  /** `snapshot` = 日 K 末根由盘口快照合成；`upstream` = 本周期未走完；null = 末根已收盘。 */
  todaySource: 'snapshot' | 'upstream' | null;
  listedSince: string | null;
  tradingHours: string | null;
  source: string;
};

export type TimesharePoint = {
  time: string;
  price: number;
  avgPrice: number | null;
  volume: number;
  amount: number;
};

export type TimeshareResponse = {
  code: string;
  name: string | null;
  tradeDate: string | null;
  prevClose: number | null;
  tradeTimes: string[];
  isTrading: boolean;
  suspended: boolean;
  points: TimesharePoint[];
  source: string;
};

type KlineParams = {
  period?: KlinePeriod;
  adjust?: KlineAdjust;
  limit?: number;
  indicators?: ChartIndicator[];
  withToday?: boolean;
};

export const chartApi = {
  /** 多周期 K 线 + 指标。`indicators` 缺省只算均线。 */
  async kline(code: string, params: KlineParams = {}): Promise<KlineResponse> {
    const response = await apiClient.get(`${BASE}/kline`, {
      params: {
        code,
        period: params.period ?? 'day',
        adjust: params.adjust ?? 'qfq',
        limit: params.limit ?? 250,
        ...(params.indicators?.length ? { indicators: params.indicators.join(',') } : {}),
        ...(params.withToday === undefined ? {} : { with_today: params.withToday }),
      },
      timeout: 25000,
    });
    return toCamelCase<KlineResponse>(response.data);
  },

  /** 分时。上游实时，含昨收与交易时段。 */
  async timeshare(code: string): Promise<TimeshareResponse> {
    const response = await apiClient.get(`${BASE}/timeshare`, {
      params: { code },
      timeout: 25000,
    });
    return toCamelCase<TimeshareResponse>(response.data);
  },
};
