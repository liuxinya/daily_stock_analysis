import type { AnalysisReport } from '../types/analysis';
import { historyApi } from '../api/history';
import { validateStockCode } from './validation';

export interface ChatFollowUpContext {
  stock_code: string;
  stock_name: string | null;
  previous_analysis_summary?: unknown;
  previous_strategy?: unknown;
  previous_price?: number;
  previous_change_pct?: number;
  market_structure_context?: unknown;
}

type ResolveChatFollowUpContextParams = {
  stockCode: string;
  stockName: string | null;
  recordId?: number;
};

const MAX_FOLLOW_UP_NAME_LENGTH = 80;
const MAX_FOLLOW_UP_QUESTION_LENGTH = 200;

function hasInvalidFollowUpCharacter(value: string): boolean {
  return Array.from(value).some((character) => {
    const code = character.charCodeAt(0);
    return code < 32 || code === 127;
  });
}

export function sanitizeFollowUpStockCode(stockCode: string | null): string | null {
  if (!stockCode) {
    return null;
  }

  const { valid, normalized } = validateStockCode(stockCode);
  return valid ? normalized : null;
}

export function sanitizeFollowUpStockName(stockName: string | null): string | null {
  const normalized = stockName?.trim().replace(/\s+/g, ' ') ?? '';
  if (!normalized) {
    return null;
  }

  if (
    normalized.length > MAX_FOLLOW_UP_NAME_LENGTH
    || hasInvalidFollowUpCharacter(normalized)
  ) {
    return null;
  }

  return normalized;
}

/**
 * 由来源页指定的追问文案，例如新闻分析里的“现在适合购买吗”。
 *
 * 只做长度与控制字符校验；拿不到合法问题时由调用方回落到默认追问，
 * 而不是把一段可疑文本直接塞进输入框。
 */
export function sanitizeFollowUpQuestion(question: string | null): string | null {
  const normalized = question?.trim().replace(/\s+/g, ' ') ?? '';
  if (!normalized) {
    return null;
  }

  if (
    normalized.length > MAX_FOLLOW_UP_QUESTION_LENGTH
    || hasInvalidFollowUpCharacter(normalized)
  ) {
    return null;
  }

  return normalized;
}

function toSnakeCaseKey(value: string): string {
  return value
    .replace(/([a-z\d])([A-Z])/g, '$1_$2')
    .replace(/-/g, '_')
    .toLowerCase();
}

function convertMarketStructureToSnakeCase(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map((item) => convertMarketStructureToSnakeCase(item));
  }
  if (value === null || typeof value !== 'object') {
    return value;
  }

  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>).map(([key, item]) => [
      toSnakeCaseKey(key),
      convertMarketStructureToSnakeCase(item),
    ]),
  );
}

function getMarketStructureContextForAgent(report?: AnalysisReport | null): unknown | undefined {
  const marketStructure = report?.details?.marketStructure;
  if (marketStructure === null || typeof marketStructure !== 'object') {
    return undefined;
  }
  if (Array.isArray(marketStructure)) {
    return undefined;
  }
  if (!Object.keys(marketStructure).length) {
    return undefined;
  }
  return convertMarketStructureToSnakeCase(marketStructure);
}

export function parseFollowUpRecordId(recordId: string | null): number | undefined {
  if (!recordId || !/^\d+$/.test(recordId)) {
    return undefined;
  }

  const parsed = Number(recordId);
  if (!Number.isSafeInteger(parsed) || parsed <= 0) {
    return undefined;
  }

  return parsed;
}

export function buildFollowUpPrompt(stockCode: string, stockName: string | null): string {
  const displayName = stockName ? `${stockName}(${stockCode})` : stockCode;
  return `请深入分析 ${displayName}`;
}

/** “现在适合买吗”这类择时提问的统一文案，供各处跳转问股的入口复用。 */
export function buildBuyTimingPrompt(stockCode: string, stockName: string | null): string {
  const displayName = stockName ? `${stockName}(${stockCode})` : stockCode;
  return `${displayName}现在适合购买吗？`;
}

export function buildChatFollowUpContext(
  stockCode: string,
  stockName: string | null,
  report?: AnalysisReport | null,
): ChatFollowUpContext {
  const context: ChatFollowUpContext = {
    stock_code: stockCode,
    stock_name: stockName,
  };

  if (!report) {
    return context;
  }

  if (report.summary) {
    context.previous_analysis_summary = report.summary;
  }

  if (report.strategy) {
    context.previous_strategy = report.strategy;
  }

  if (report.meta) {
    context.previous_price = report.meta.currentPrice;
    context.previous_change_pct = report.meta.changePct;
  }

  const marketStructureContext = getMarketStructureContextForAgent(report);
  if (marketStructureContext) {
    context.market_structure_context = marketStructureContext;
  }

  return context;
}

export async function resolveChatFollowUpContext({
  stockCode,
  stockName,
  recordId,
}: ResolveChatFollowUpContextParams): Promise<ChatFollowUpContext> {
  if (!recordId) {
    return buildChatFollowUpContext(stockCode, stockName);
  }

  try {
    const report = await historyApi.getDetail(recordId);
    return buildChatFollowUpContext(stockCode, stockName, report);
  } catch {
    return buildChatFollowUpContext(stockCode, stockName);
  }
}
