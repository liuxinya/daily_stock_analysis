import type React from 'react';
import { useMemo, useState } from 'react';
import { CandlestickChart } from 'lucide-react';

import { Badge, Card, InlineAlert } from '../common';
import type { BriefEvidence, BriefWatchItem, MarketBriefPayload } from '../../api/marketBrief';
import { resolveStockChartCode, useStockChartStore } from '../../stores/stockChartStore';
import { BriefEvidenceButton } from './BriefEvidenceButton';
import { BriefEvidenceDrawer } from './BriefEvidenceDrawer';
import {
  directionBadgeVariant,
  directionLabel,
  importanceLabel,
  shortDateTime,
  strengthLabel,
  toneSummary,
} from './format';

interface MarketBriefPanelProps {
  payload: MarketBriefPayload;
}

const TermChips: React.FC<{ terms: string[] }> = ({ terms }) => {
  if (!terms.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {terms.map((term) => (
        <span key={term} className="rounded bg-elevated/70 px-1.5 py-0.5 text-xxs text-muted-text">
          {term}
        </span>
      ))}
    </div>
  );
};

/**
 * 关注股票的名称 + 代码。代码可用时做成按钮，点开全局行情弹窗。
 *
 * 代码来自模型推断（服务端只放过 6 位数字，其余置空），`canOpenStockChart` 再按
 * 项目既有规则校验一次；校验不过就退回纯文本，而不是把一段不确定的文本丢给行情
 * 接口。行情与图表接口只覆盖 A 股 / 场内基金，所以非 6 位码一律不给入口。
 */
const WatchStockName: React.FC<{
  item: BriefWatchItem;
  onOpen: (code: string, name: string) => void;
}> = ({ item, onOpen }) => {
  const code = resolveStockChartCode(item.code);
  if (!code) {
    return (
      <>
        <span className="text-sm font-medium text-foreground">{item.name}</span>
        {item.code ? <span className="text-xxs tabular-nums text-muted-text">{item.code}</span> : null}
      </>
    );
  }

  return (
    <button
      type="button"
      onClick={() => onOpen(code, item.name)}
      aria-label={`查看 ${item.name}(${code}) 的实时行情与 K 线`}
      className="group inline-flex items-center gap-1.5 rounded text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan/60"
    >
      <span className="text-sm font-medium text-foreground underline decoration-dotted underline-offset-4 group-hover:text-cyan">
        {item.name}
      </span>
      <span className="text-xxs tabular-nums text-muted-text group-hover:text-cyan">{code}</span>
      <CandlestickChart className="h-3 w-3 text-muted-text group-hover:text-cyan" aria-hidden="true" />
    </button>
  );
};

/**
 * 汇总结果面板。
 *
 * 事实层（关键消息 / 板块方向 / 要点）与推断层（关注股票）刻意分开呈现：
 * 事实层每条都经过服务端 grounding 校验，引用词必须在原文里逐字出现；
 * 推断层是模型基于板块的知识映射，原文未必点名，必须显式标注。
 */
export const MarketBriefPanel: React.FC<MarketBriefPanelProps> = ({ payload }) => {
  const { result, grounding, coverage, window } = payload;
  const droppedCount = grounding?.droppedCount ?? 0;

  // 旧版本生成的汇总没有 evidence 字段，`?? []` 兜底 —— 表现为「暂时没有信源入口」，重新汇总即恢复。
  const evidenceById = useMemo(
    () => new Map<number, BriefEvidence>((payload.evidence ?? []).map((item) => [item.id, item])),
    [payload.evidence],
  );
  const [drawerItems, setDrawerItems] = useState<BriefEvidence[]>([]);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const openEvidence = (items: BriefEvidence[]) => {
    setDrawerItems(items);
    setDrawerOpen(true);
  };
  // 行情弹窗是全局的（挂在 App 里），这里只负责触发，不自己持有开关状态。
  const openStockChart = useStockChartStore((state) => state.open);

  return (
    <div className="space-y-4">
      <Card variant="bordered" padding="md">
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="info" size="sm">{payload.modeLabel || result.modeLabel}</Badge>
            <Badge variant={directionBadgeVariant(result.overallTone)} size="sm">
              {toneSummary(result.overallTone)}
            </Badge>
            {payload.cached ? <Badge size="sm">复用已有结果</Badge> : null}
            <span className="text-xxs text-muted-text">
              {shortDateTime(result.windowStart)} → {shortDateTime(result.windowEnd)}
              {window?.windowHint ? ` · ${window.windowHint}` : ''}
            </span>
            <span className="ml-auto text-xxs text-muted-text">
              {coverage?.citedItems ?? 0}/{coverage?.totalItems ?? payload.itemCount} 条被引用
              {payload.model ? ` · ${payload.model}` : ''}
            </span>
          </div>

          {result.summary ? (
            <p className="text-sm leading-relaxed text-foreground">{result.summary}</p>
          ) : null}

          {result.keywords.length > 0 ? <TermChips terms={result.keywords} /> : null}
        </div>
      </Card>

      {payload.degraded ? (
        <InlineAlert
          variant="warning"
          title="本次为降级结果"
          message={payload.degradedReason || '模型未能完整解读，已尽量给出可用信息。'}
        />
      ) : null}

      {window?.degraded ? (
        <InlineAlert
          variant="info"
          title="时间窗已退化"
          message={`${window.degradedReason}。当前窗口为 ${shortDateTime(window.start)} 至 ${shortDateTime(window.end)}。`}
        />
      ) : null}

      {droppedCount > 0 ? (
        <InlineAlert
          variant="info"
          title="已过滤无原文依据的结论"
          message={`本次丢弃了 ${droppedCount} 条在资讯原文里查不到依据的结论（${(grounding?.dropped ?? [])
            .slice(0, 5)
            .join('、')}），只保留能对上原文的部分。`}
        />
      ) : null}

      {result.events.length > 0 ? (
        <Card variant="bordered" padding="md">
          <h3 className="mb-3 text-sm font-semibold text-foreground">关键消息</h3>
          <ul className="space-y-3">
            {result.events.map((event) => (
              <li key={`${event.topic}-${event.evidenceIds.join(',')}`} className="space-y-1.5">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium text-foreground">{event.topic}</span>
                  {event.category ? <Badge size="sm">{event.category}</Badge> : null}
                  <Badge variant="info" size="sm">{importanceLabel(event.importance)}</Badge>
                </div>
                {event.detail ? (
                  <p className="text-sm leading-relaxed text-secondary-text">{event.detail}</p>
                ) : null}
                <div className="flex flex-wrap items-center gap-2">
                  <TermChips terms={event.mentionedTerms} />
                  <BriefEvidenceButton
                    ids={event.evidenceIds}
                    evidenceById={evidenceById}
                    onOpen={openEvidence}
                  />
                </div>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}

      {result.sectors.length > 0 ? (
        <Card variant="bordered" padding="md">
          <h3 className="mb-3 text-sm font-semibold text-foreground">板块方向</h3>
          <ul className="space-y-3">
            {result.sectors.map((sector) => (
              <li key={sector.name} className="space-y-1.5">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium text-foreground">{sector.name}</span>
                  <Badge variant={directionBadgeVariant(sector.direction)} size="sm">
                    {directionLabel(sector.direction)}
                  </Badge>
                  <span className="text-xxs text-muted-text">{strengthLabel(sector.strength)}</span>
                </div>
                {sector.reason ? (
                  <p className="text-sm leading-relaxed text-secondary-text">{sector.reason}</p>
                ) : null}
                <div className="flex flex-wrap items-center gap-2">
                  <TermChips terms={sector.mentionedTerms} />
                  <BriefEvidenceButton
                    ids={sector.evidenceIds}
                    evidenceById={evidenceById}
                    onOpen={openEvidence}
                  />
                </div>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}

      {result.watchList.length > 0 ? (
        <Card variant="bordered" padding="md">
          <div className="mb-1 flex flex-wrap items-baseline gap-2">
            <h3 className="text-sm font-semibold text-foreground">关注股票</h3>
            <span className="text-xxs text-muted-text">由已确认的板块方向推断，非资讯原文内容 · 点个股名看实时行情与 K 线</span>
          </div>
          <ul className="mt-3 space-y-2">
            {result.watchList.map((item) => (
              <li key={`${item.name}-${item.sector}`} className="flex flex-wrap items-center gap-2">
                <WatchStockName item={item} onOpen={openStockChart} />
                <Badge size="sm">{item.sector}</Badge>
                <Badge variant={directionBadgeVariant(item.direction)} size="sm">
                  {directionLabel(item.direction)}
                </Badge>
                {item.rationale ? (
                  <span className="text-xxs text-secondary-text">{item.rationale}</span>
                ) : null}
              </li>
            ))}
          </ul>
          <InlineAlert
            variant="warning"
            title="推断内容提示"
            message={result.watchListDisclaimer}
            className="mt-3"
          />
        </Card>
      ) : null}

      {result.takeaways.length > 0 ? (
        <Card variant="bordered" padding="md">
          <h3 className="mb-3 text-sm font-semibold text-foreground">下一步关注</h3>
          <ul className="space-y-3">
            {result.takeaways.map((item) => (
              <li key={item.topic} className="space-y-1.5">
                <span className="text-sm font-medium text-foreground">{item.topic}</span>
                {item.detail ? (
                  <p className="text-sm leading-relaxed text-secondary-text">{item.detail}</p>
                ) : null}
                <div className="flex flex-wrap items-center gap-2">
                  <TermChips terms={item.mentionedTerms} />
                  <BriefEvidenceButton
                    ids={item.evidenceIds}
                    evidenceById={evidenceById}
                    onOpen={openEvidence}
                  />
                </div>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}

      {result.risks.length > 0 || result.notCovered.length > 0 ? (
        <Card variant="bordered" padding="md">
          <h3 className="mb-2 text-sm font-semibold text-foreground">风险与未覆盖</h3>
          <ul className="space-y-1.5 text-sm leading-relaxed text-secondary-text">
            {result.risks.map((risk) => (
              <li key={`risk-${risk}`}>· {risk}</li>
            ))}
            {result.notCovered.map((item) => (
              <li key={`nc-${item}`} className="text-muted-text">· 未覆盖：{item}</li>
            ))}
          </ul>
        </Card>
      ) : null}

      <BriefEvidenceDrawer
        isOpen={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        items={drawerItems}
      />
    </div>
  );
};
