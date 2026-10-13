import apiClient from './index';

/**
 * 百度财经代理客户端。
 *
 * 后端 /api/v1/baidu-finance/{key} 只做透传，不做字段归一化，因此这里的类型
 * 只声明页面实际读取的字段，其余保持 unknown —— 上游字段随时可能变动，过度
 * 声明只会制造虚假的类型安全。
 */

const BASE = '/api/v1/baidu-finance';

/**
 * A 股 ETF 代码前缀，与后端 data_provider 的 ETF 判定（base.ETF_PREFIXES）保持一致：
 * 上交所 51/52/56/58，深交所 15/16/18。ETF 与股票在百度上游走不同的接口和判别
 * 参数，页面据此分流。
 */
const ETF_PREFIXES = ['51', '52', '56', '58', '15', '16', '18'] as const;

/** 判断是否为 A 股 ETF 代码（6 位数字且命中 ETF 前缀）。 */
export const isEtfCode = (code: string): boolean => {
  const normalized = code.trim();
  return /^\d{6}$/.test(normalized) && ETF_PREFIXES.some((prefix) => normalized.startsWith(prefix));
};

/** 上游统一包装：真实数据在 Result 里。 */
export type BaiduEnvelope<T> = {
  ResultCode?: number | string;
  QueryID?: string;
  Result?: T;
};

export type PankouItem = {
  ename?: string;
  name?: string;
  value?: string;
  status?: 'up' | 'down' | string;
  originValue?: string;
};

/** K 线：上游用 keys[] + 分号分隔的 CSV 字符串，需自行解析。 */
export type MarketDataBlock = {
  keys?: string[];
  headers?: string[];
  marketData?: string;
};

export type KlineRow = {
  time: string;
  open: number;
  close: number;
  high: number;
  low: number;
  volume: number;
  amount: number;
  ratio: number;
  ma5: number | null;
  ma10: number | null;
  ma20: number | null;
};

export type FundFlowGroup = {
  netTurnover?: string;
  turnoverIn?: string;
  turnoverOut?: string;
  turnoverInRate?: string;
  turnoverOutRate?: string;
};

export type FundFlowSpread = {
  superGrp?: FundFlowGroup;
  largeGrp?: FundFlowGroup;
  mediumGrp?: FundFlowGroup;
  littleGrp?: FundFlowGroup;
  todayMainFlow?: { mainIn?: string; mainOut?: string; mainNetIn?: string };
  turnoverInTotal?: string;
  turnoverOutTotal?: string;
  turnoverNetTotal?: string;
  mainNetTotal?: string;
  unit?: string;
  updateTime?: string;
  analysis?: { content?: string; list?: Array<{ content?: string; desc?: string }> };
};

export type ScoreBlock = {
  title?: string;
  score?: string;
  rating?: string;
  desc?: string;
  updateTime?: string;
};

/** 舆情条目；benefitType: 1=利好 2=利空 其它=中性。 */
export type SentimentItem = {
  title?: string;
  abstract?: string;
  provider?: string;
  publishTime?: string;
  originUrl?: string;
  benefitType?: string;
};

/** 三表 / 主要指标的通用结构：header 是指标名，body 每行是 [期间, 值, 同比, 值, 同比, …]。 */
export type SheetChartInfo = Array<{
  type?: string;
  header?: string[];
  body?: string[][];
}>;

export type FinanceSheet = {
  title?: string;
  unit?: string;
  chartInfo?: SheetChartInfo;
};

const get = async <T,>(key: string, params: Record<string, string>): Promise<T> => {
  const response = await apiClient.get(`${BASE}/${key}`, { params, timeout: 25000 });
  return response.data as T;
};

/** 把 keys[] + CSV 字符串解析成对象数组。 */
export const parseMarketData = (block?: MarketDataBlock): KlineRow[] => {
  const keys = block?.keys;
  const raw = block?.marketData;
  if (!keys?.length || !raw) return [];

  const num = (value: string | undefined): number => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : 0;
  };
  const optional = (value: string | undefined): number | null => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  };

  return raw
    .split(';')
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const cells = line.split(',');
      const at = (name: string): string | undefined => {
        const index = keys.indexOf(name);
        return index >= 0 ? cells[index] : undefined;
      };
      return {
        time: at('time') ?? '',
        open: num(at('open')),
        close: num(at('close')),
        high: num(at('high')),
        low: num(at('low')),
        volume: num(at('volume')),
        amount: num(at('amount')),
        ratio: num(at('ratio')),
        ma5: optional(at('ma5avgprice')),
        ma10: optional(at('ma10avgprice')),
        ma20: optional(at('ma20avgprice')),
      };
    })
    .filter((row) => Boolean(row.time));
};

export const baiduFinanceApi = {
  /**
   * 日 K 线 + 压力位/支撑位。
   *
   * 股票走 analysis-quotation（含 analysisData 的压力位/支撑位）；ETF 在该接口
   * 恒为空，改走 getquotation 并带 ETF 判别参数。两者的 newMarketData 结构一致，
   * 可复用 parseMarketData；但 ETF 响应没有 analysisData（无压力位/支撑位）。
   */
  kline(code: string, market = 'ab') {
    type KlinePayload = BaiduEnvelope<{
      analysisData?: { price?: number; updateTime?: string; yl?: string; zc?: string };
      newMarketData?: MarketDataBlock;
    }>;
    if (isEtfCode(code)) {
      return get<KlinePayload>('quotation', {
        srcid: '5353',
        all: '1',
        code,
        query: code,
        financeType: 'etf',
        stock_type: market,
        group: `quotation_kline_${market}`,
        ktype: 'day',
      });
    }
    return get<KlinePayload>('analysis-quotation', {
      all: '1',
      newFormat: '1',
      ktype: 'day',
      group: 'quotation_analysis_kline',
      code,
      market_type: market,
    });
  },

  /** 资金流向：大单拆分 + 分钟级资金流 + 行业资金。 */
  fundFlow(code: string, market = 'ab') {
    const financeType = isEtfCode(code) ? 'etf' : 'stock';
    return get<BaiduEnvelope<{
      content?: {
        fundFlowSpread?: { result?: FundFlowSpread };
        fundFlowMinute?: MarketDataBlock & { unit?: string; updateTime?: string };
        fundFlowBlock?: { title?: string; result?: unknown[] };
      };
    }>>('fundflow', {
      finance_type: financeType,
      fund_flow_type: '',
      market,
      code,
      type: financeType,
    });
  },

  /** 简况：公司基本信息 / 股东 / 机构评级 / 高管 / 分红。 */
  company(code: string, market = 'ab') {
    return get<BaiduEnvelope<{ content?: Record<string, unknown> }>>('stock-widget', {
      code,
      market,
      type: 'stock',
      widgetType: 'company',
    });
  },

  /** 股评：股吧帖子 + 看涨看跌投票。 */
  talks(code: string, market = 'ab') {
    return get<BaiduEnvelope<{ content?: Record<string, unknown>; voteData?: Record<string, unknown> }>>(
      'stock-widget',
      { code, market, type: 'stock', widgetType: 'talks' },
    );
  },

  /** 财务：三表 + 主要指标 + 估值 + 主营构成。 */
  finance(code: string, market = 'ab') {
    return get<BaiduEnvelope<{ content?: Record<string, unknown> }>>('stock-widget', {
      code,
      market,
      type: 'stock',
      widgetType: 'finance',
    });
  },

  /** 主营构成（按行业/产品/地区）。 */
  mainOperation(code: string, market = 'ab') {
    return get<BaiduEnvelope<Record<string, unknown>>>('mainop', { market, code });
  },

  /** 分析：综合评分 + 价格动量 / 收益预测 / 估值 / 财务 / 风险。 */
  analysis(code: string, market = 'ab') {
    return get<BaiduEnvelope<Record<string, unknown>>>('analysis', { code, market, isNew: '1' });
  },

  /** 资讯：AI 舆情列表。 */
  sentiment(code: string, market = 'ab', rows = 20) {
    return get<BaiduEnvelope<Array<{
      TplData?: {
        aiSentimentXcxListInfo?: {
          basicInfo?: { code?: string; name?: string };
          sentimentListInfo?: SentimentItem[];
        };
      };
    }>>>('sentiment-list', {
      market,
      code,
      query: code,
      financeType: isEtfCode(code) ? 'etf' : 'stock',
      benefitType: '',
      pn: '0',
      rn: String(rows),
    });
  },

  /** 弹幕/快评。 */
  bulletScreen(code: string, market = 'ab') {
    return get<BaiduEnvelope<Record<string, unknown>>>('bulletscreen', {
      financeType: 'stock',
      code,
      market,
      interval: '120',
    });
  },
};
