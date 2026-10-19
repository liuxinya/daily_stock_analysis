import type React from 'react';
import { AlertTriangle, CandlestickChart, Info, Lightbulb, MessageSquare, Search, ShieldCheck, TrendingDown, TrendingUp } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

import { Badge, Button, Card, InlineAlert } from '../common';
import { cn } from '../../utils/cn';
import type {
  CctvAnalysisEvidence,
  CctvIndustryOpportunity,
  CctvNewsAnalysisResponse,
  CctvStockCandidate,
} from '../../api/cctvNews';
import { buildBuyTimingPrompt, sanitizeFollowUpStockCode } from '../../utils/chatFollowUp';
import { resolveStockChartCode, useStockChartStore } from '../../stores/stockChartStore';
import {
  directionBadgeVariant,
  directionLabel,
  formatDateTime,
  shortDate,
  strengthLabel,
  toneSummary,
} from './format';

interface CctvNewsAnalysisPanelProps {
  result: CctvNewsAnalysisResponse;
  onOpenEvidence: (itemId: number) => void;
}

interface EvidenceChipsProps {
  ids: number[];
  evidenceById: Map<number, CctvAnalysisEvidence>;
  onOpenEvidence: (itemId: number) => void;
}

/** 引用编号 -> 可点击的原文入口。点开就是那条新闻的文字稿。 */
const EvidenceChips: React.FC<EvidenceChipsProps> = ({ ids, evidenceById, onOpenEvidence }) => {
  const resolved = ids
    .map((id) => evidenceById.get(id))
    .filter((entry): entry is CctvAnalysisEvidence => Boolean(entry));

  if (resolved.length === 0) {
    return null;
  }

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-xxs text-muted-text">原文依据</span>
      {resolved.map((entry) => (
        <button
          key={entry.id}
          type="button"
          onClick={() => onOpenEvidence(entry.itemId)}
          aria-label={`查看依据新闻：${shortDate(entry.newsDate)} ${entry.title}`}
          className="inline-flex max-w-[16rem] items-center gap-1 truncate rounded-md border border-cyan/25 bg-cyan/10 px-1.5 py-0.5 text-xxs text-cyan transition-colors hover:bg-cyan/20"
        >
          <span className="tabular-nums">[{entry.id}]</span>
          <span className="truncate">{entry.title}</span>
        </button>
      ))}
    </div>
  );
};

/** 原文出现的关键词，用于向用户展示"这条结论的依据词在原文里能查到"。 */
const TermChips: React.FC<{ terms: string[] }> = ({ terms }) => {
  if (terms.length === 0) {
    return null;
  }
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {terms.map((term) => (
        <span
          key={term}
          className="rounded-md border border-border/60 bg-elevated/70 px-1.5 py-0.5 text-xxs text-secondary-text"
        >
          {term}
        </span>
      ))}
    </div>
  );
};

interface IndustryListProps {
  industries: CctvIndustryOpportunity[];
  evidenceById: Map<number, CctvAnalysisEvidence>;
  onOpenEvidence: (itemId: number) => void;
  tone: 'bullish' | 'bearish';
}

/** 单个方向（利好/利空）的行业清单。 */
const IndustryList: React.FC<IndustryListProps> = ({
  industries,
  evidenceById,
  onOpenEvidence,
  tone,
}) => {
  const Icon = tone === 'bullish' ? TrendingUp : TrendingDown;
  const iconClass = tone === 'bullish' ? 'text-[var(--chart-up)]' : 'text-[var(--chart-down)]';
  return (
    <ul className="space-y-3">
      {industries.map((industry) => (
        <li
          key={industry.name}
          className="space-y-2 rounded-xl border border-border/50 bg-elevated/40 p-3"
        >
          <div className="flex flex-wrap items-center gap-2">
            <Icon className={cn('h-4 w-4', iconClass)} aria-hidden="true" />
            <span className="text-sm font-semibold text-foreground">{industry.name}</span>
            <Badge variant={directionBadgeVariant(industry.direction)} size="sm">
              {directionLabel(industry.direction)}
            </Badge>
            <span className="text-xxs text-muted-text">{strengthLabel(industry.strength)}</span>
          </div>
          {industry.reason ? (
            <p className="text-sm leading-relaxed text-secondary-text">{industry.reason}</p>
          ) : null}
          <TermChips terms={industry.mentionedTerms} />
          <EvidenceChips
            ids={industry.evidenceIds}
            evidenceById={evidenceById}
            onOpenEvidence={onOpenEvidence}
          />
        </li>
      ))}
    </ul>
  );
};

/**
 * 个股名 + 代码。代码可用时做成按钮，点开全局行情弹窗（与新闻分析的关注股票同一个入口）。
 *
 * 代码来自模型推断，`canOpenStockChart` 按项目既有规则校验；校验不过就退回纯文本，
 * 不把一段不确定的文本丢给行情接口。操作列的「问股 / 查询」保持不变。
 */
const StockNameCell: React.FC<{ stock: CctvStockCandidate }> = ({ stock }) => {
  const openStockChart = useStockChartStore((state) => state.open);
  const code = resolveStockChartCode(stock.code);

  if (!code) {
    return (
      <>
        <span className="font-medium text-foreground">{stock.name}</span>
        {stock.code ? <span className="text-xxs tabular-nums text-muted-text">{stock.code}</span> : null}
      </>
    );
  }

  return (
    <button
      type="button"
      onClick={() => openStockChart(code, stock.name)}
      aria-label={`查看 ${stock.name}(${code}) 的实时行情与 K 线`}
      className="group inline-flex items-center gap-1.5 rounded text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan/60"
    >
      <span className="font-medium text-foreground underline decoration-dotted underline-offset-4 group-hover:text-cyan">
        {stock.name}
      </span>
      <span className="text-xxs tabular-nums text-muted-text group-hover:text-cyan">{code}</span>
      <CandlestickChart className="h-3 w-3 text-muted-text group-hover:text-cyan" aria-hidden="true" />
    </button>
  );
};

/**
 * 推断个股的两个下一步动作：问股（择时提问）与查询（行情看板）。
 *
 * 代码来自模型推断，因此先按项目既有代码规则校验，校验不过就不给入口，
 * 而不是把一段不确定的文本丢给问股或行情接口。股票查询页只认 A 股 6 位裸代码，
 * 港股 / 美股候选只保留“问股”。
 */
const StockActions: React.FC<{ stock: CctvStockCandidate }> = ({ stock }) => {
  const navigate = useNavigate();
  const code = sanitizeFollowUpStockCode(stock.code);

  if (!code) {
    return <span className="text-xxs text-muted-text">暂无代码</span>;
  }

  const name = stock.name.trim() || null;
  const askPrompt = buildBuyTimingPrompt(code, name);
  const canOpenQuery = /^\d{6}$/.test(code);
  const openChat = () => {
    const params = new URLSearchParams({ stock: code, q: askPrompt, send: '1' });
    if (name) {
      params.set('name', name);
    }
    navigate(`/chat?${params.toString()}`);
  };

  return (
    <div className="flex items-center gap-1.5">
      <Button
        variant="outline"
        size="xsm"
        className="gap-1 text-xxs"
        onClick={openChat}
        aria-label={`问股：${askPrompt}`}
      >
        <MessageSquare className="h-3.5 w-3.5" aria-hidden="true" />
        问股
      </Button>
      {canOpenQuery ? (
        <Button
          variant="secondary"
          size="xsm"
          className="gap-1 text-xxs"
          onClick={() => navigate(`/stock-query?code=${encodeURIComponent(code)}`)}
          aria-label={`查询 ${name ?? code}(${code}) 行情`}
        >
          <Search className="h-3.5 w-3.5" aria-hidden="true" />
          查询
        </Button>
      ) : null}
    </div>
  );
};

/**
 * AI 分析结果面板。
 *
 * 刻意分成两层呈现：政策信号 / 行业方向是"新闻事实层"，每条都带可点开的原文依据；
 * 关注个股是"模型推断层"，公司名不一定出现在新闻里，必须单独标注。
 */
export const CctvNewsAnalysisPanel: React.FC<CctvNewsAnalysisPanelProps> = ({
  result,
  onOpenEvidence,
}) => {
  const { analysis, evidence, grounding } = result;
  const evidenceById = new Map(evidence.map((entry) => [entry.id, entry]));
  const droppedCount = grounding?.droppedCount ?? 0;
  const bullish = analysis.industries.filter((row) => row.direction === 'bullish');
  const bearish = analysis.industries.filter((row) => row.direction === 'bearish');

  return (
    <div className="space-y-4">
      <Card variant="gradient" padding="md">
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={directionBadgeVariant(analysis.overallTone)} size="md">
              {toneSummary(analysis.overallTone)}
            </Badge>
            <span className="text-xxs text-muted-text tabular-nums">
              {result.startDate}
              {result.startDate !== result.endDate ? ` ~ ${result.endDate}` : ''} · {result.itemCount} 篇新闻
            </span>
            {result.cached ? (
              <Badge variant="default" size="sm">
                复用已有结果
              </Badge>
            ) : null}
          </div>

          {analysis.summary ? (
            <p className="text-sm leading-relaxed text-secondary-text">{analysis.summary}</p>
          ) : null}

          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xxs text-muted-text">
            <span>模型 {result.model ?? '--'}</span>
            <span>生成于 {formatDateTime(result.createdAt)}</span>
            {analysis.keywords.length > 0 ? <span>高频词 {analysis.keywords.join('、')}</span> : null}
          </div>
        </div>
      </Card>

      {result.degraded ? (
        <InlineAlert
          variant="warning"
          title="本次为降级结果"
          message={
            result.degradedReason
            || '模型未能完整解读本批新闻（常见于网络波动或内容策略），已改用关键词覆盖统计。可稍后重试。'
          }
        />
      ) : null}

      {droppedCount > 0 ? (
        <InlineAlert
          variant="info"
          title="已过滤无原文依据的结论"
          message={`本次分析丢弃了 ${droppedCount} 条在文字稿里查不到依据的结论（${(grounding?.dropped ?? [])
            .slice(0, 5)
            .join('、')}），只保留能对上原文的部分。`}
        />
      ) : null}

      {bullish.length > 0 ? (
        <Card
          title="利好行业方向"
          subtitle="政策扶持、需求扩张等受益方向，每条都能在原文找到依据"
          variant="bordered"
          padding="md"
        >
          <IndustryList
            industries={bullish}
            evidenceById={evidenceById}
            onOpenEvidence={onOpenEvidence}
            tone="bullish"
          />
        </Card>
      ) : null}

      {bearish.length > 0 ? (
        <Card
          title="利空行业方向"
          subtitle="受限、承压、监管收紧等受损方向"
          variant="bordered"
          padding="md"
        >
          <IndustryList
            industries={bearish}
            evidenceById={evidenceById}
            onOpenEvidence={onOpenEvidence}
            tone="bearish"
          />
        </Card>
      ) : null}

      {analysis.stocks.length > 0 ? (
        <Card
          title="关注个股（含行业龙头）"
          subtitle="由已确认的行业方向推断，非新闻原文内容"
          variant="bordered"
          padding="md"
        >
          <div className="overflow-x-auto">
            <table className="w-full min-w-[820px] text-left text-sm">
              <thead className="border-b border-border/60 text-xs uppercase text-muted-text">
                <tr>
                  <th className="px-3 py-2 font-medium">个股</th>
                  <th className="px-3 py-2 font-medium">方向</th>
                  <th className="px-3 py-2 font-medium">对应行业</th>
                  <th className="px-3 py-2 font-medium">推荐逻辑</th>
                  <th className="px-3 py-2 font-medium">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/40">
                {analysis.stocks.map((stock) => (
                  <tr key={`${stock.name}-${stock.code ?? 'na'}`}>
                    <td className="px-3 py-3 align-top">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <StockNameCell stock={stock} />
                        {stock.isLeader ? (
                          <Badge variant="warning" size="sm">
                            龙头
                          </Badge>
                        ) : null}
                      </div>
                    </td>
                    <td className="whitespace-nowrap px-3 py-3 align-top">
                      <Badge variant={directionBadgeVariant(stock.direction)} size="sm">
                        {directionLabel(stock.direction)}
                      </Badge>
                    </td>
                    <td className="whitespace-nowrap px-3 py-3 align-top text-secondary-text">{stock.industry}</td>
                    <td className="px-3 py-3 align-top">
                      <p className="leading-relaxed text-secondary-text">{stock.rationale || '--'}</p>
                      <div className="mt-1.5">
                        <EvidenceChips
                          ids={stock.evidenceIds}
                          evidenceById={evidenceById}
                          onOpenEvidence={onOpenEvidence}
                        />
                      </div>
                    </td>
                    <td className="whitespace-nowrap px-3 py-3 align-top">
                      <StockActions stock={stock} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {analysis.stockDisclaimer ? (
            <p className="mt-3 flex items-start gap-2 rounded-lg border border-warning/20 bg-warning/5 p-2.5 text-xxs leading-relaxed text-secondary-text">
              <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" aria-hidden="true" />
              {analysis.stockDisclaimer}
            </p>
          ) : null}
        </Card>
      ) : null}

      {analysis.investorTakeaways.length > 0 ? (
        <Card
          title="对股民重要的信息"
          subtitle="文字稿里真实出现、且可能影响交易判断的要点"
          variant="bordered"
          padding="md"
        >
          <ul className="space-y-3">
            {analysis.investorTakeaways.map((takeaway) => (
              <li key={takeaway.topic} className="space-y-1.5">
                <div className="flex items-center gap-2">
                  <Info className="h-3.5 w-3.5 text-cyan" aria-hidden="true" />
                  <span className="text-sm font-medium text-foreground">{takeaway.topic}</span>
                </div>
                {takeaway.detail ? (
                  <p className="text-sm leading-relaxed text-secondary-text">{takeaway.detail}</p>
                ) : null}
                <EvidenceChips
                  ids={takeaway.evidenceIds}
                  evidenceById={evidenceById}
                  onOpenEvidence={onOpenEvidence}
                />
              </li>
            ))}
          </ul>
        </Card>
      ) : null}

      {analysis.policySignals.length > 0 ? (
        <Card title="政策与会议信号" variant="bordered" padding="md">
          <ul className="space-y-3">
            {analysis.policySignals.map((signal) => (
              <li key={signal.topic} className="space-y-1.5">
                <div className="flex items-center gap-2">
                  <Lightbulb className="h-3.5 w-3.5 text-purple" aria-hidden="true" />
                  <span className="text-sm font-medium text-foreground">{signal.topic}</span>
                </div>
                {signal.detail ? (
                  <p className="text-sm leading-relaxed text-secondary-text">{signal.detail}</p>
                ) : null}
                <EvidenceChips
                  ids={signal.evidenceIds}
                  evidenceById={evidenceById}
                  onOpenEvidence={onOpenEvidence}
                />
              </li>
            ))}
          </ul>
        </Card>
      ) : null}

      <div className={cn('grid gap-4', analysis.notCovered.length > 0 ? 'lg:grid-cols-2' : '')}>
        {analysis.risks.length > 0 ? (
          <Card title="风险提示" variant="bordered" padding="md">
            <ul className="space-y-2">
              {analysis.risks.map((risk) => (
                <li key={risk} className="flex items-start gap-2 text-sm leading-relaxed text-secondary-text">
                  <AlertTriangle
                    className="mt-1 h-3.5 w-3.5 shrink-0 text-warning"
                    aria-hidden="true"
                  />
                  {risk}
                </li>
              ))}
            </ul>
          </Card>
        ) : null}

        {analysis.notCovered.length > 0 ? (
          <Card title="文字稿未涉及" subtitle="这些方向本次新闻没有提到，因此不做判断" variant="bordered" padding="md">
            <ul className="space-y-2">
              {analysis.notCovered.map((topic) => (
                <li key={topic} className="flex items-start gap-2 text-sm leading-relaxed text-muted-text">
                  <Info className="mt-1 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                  {topic}
                </li>
              ))}
            </ul>
          </Card>
        ) : null}
      </div>
    </div>
  );
};
