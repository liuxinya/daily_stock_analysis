import type React from 'react';
import { useMemo, useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import type { UiTextKey } from '../../i18n/uiText';
import type { IndustryResult, IndustryRow } from '../../types/industryFunds';
import { cn } from '../../utils/cn';
import { InlineAlert } from '../common/InlineAlert';
import { SectionCard } from '../common/SectionCard';
import { Tooltip } from '../common/Tooltip';
import { formatPct, formatPercentile, formatScore, formatSignedYi, formatWeight, inOutTone } from './format';

type Translate = (key: UiTextKey) => string;

type Props = {
  result: IndustryResult;
  t: Translate;
};

const INITIAL_ROWS = 15;

/**
 * 行业得分表。
 *
 * 这里如实写明一件事：行业分类统一到同花顺的 90 个行业，而**这套分类拿不到成分股与
 * 行业 PE**，所以行业分只由资金流自身的字段构成，没有龙虎榜与估值因子。这是选定口径
 * 后的固有限制，不能默默省略。
 */
export const IndustryScoreTable: React.FC<Props> = ({ result, t }) => {
  const [expanded, setExpanded] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  const rows = useMemo(() => result.industries ?? [], [result.industries]);
  const visible = showAll ? rows : rows.slice(0, INITIAL_ROWS);

  if (rows.length === 0) {
    return (
      <SectionCard title={t('industryFunds.industryScore')} subtitle={t('industryFunds.sectionSignal')}>
        <p className="text-sm text-secondary-text">{t('industryFunds.industryUnavailable')}</p>
      </SectionCard>
    );
  }

  return (
    <SectionCard
      title={t('industryFunds.industryScore')}
      subtitle={t('industryFunds.industryScoreHint')}
    >
      <div className="mb-3 flex flex-wrap items-center gap-2 text-xs text-secondary-text">
        <span>{t('industryFunds.industryWeights')}</span>
        {Object.entries(result.weights ?? {}).map(([key, weight]) => (
          <span key={key} className="rounded-lg border border-border/50 bg-elevated/50 px-2 py-0.5">
            {result.labels?.[key] ?? key} {formatWeight(weight)}
          </span>
        ))}
      </div>

      {result.warnings?.length ? (
        <InlineAlert
          variant="warning"
          className="mb-3"
          message={
            <ul className="list-disc space-y-1 pl-4">
              {result.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          }
        />
      ) : null}

      <div className="overflow-x-auto">
        <table className="w-full min-w-[54rem] border-collapse text-sm">
          <thead>
            <tr className="border-b border-border/60 text-left text-xs text-secondary-text">
              <th className="py-2 pr-3 font-medium">{t('industryFunds.colRank')}</th>
              <th className="py-2 pr-3 font-medium">{t('industryFunds.colIndustry')}</th>
              <th className="py-2 pr-3 font-medium">{t('industryFunds.colIndustryScore')}</th>
              <th className="py-2 pr-3 text-right font-medium">{t('industryFunds.colNet')}</th>
              <th className="py-2 pr-3 text-right font-medium">{t('industryFunds.colNet5d')}</th>
              <th className="py-2 pr-3 text-right font-medium">{t('industryFunds.colChange')}</th>
              <th className="w-8 py-2" />
            </tr>
          </thead>
          <tbody>
            {visible.map((row) => (
              <IndustryScoreRow
                key={row.name}
                row={row}
                expanded={expanded === row.name}
                onToggle={() => setExpanded(expanded === row.name ? null : row.name)}
                t={t}
              />
            ))}
          </tbody>
        </table>
      </div>

      {rows.length > INITIAL_ROWS ? (
        <button
          type="button"
          onClick={() => setShowAll(!showAll)}
          className="mt-3 text-xs text-cyan transition-colors hover:text-cyan/80"
        >
          {showAll
            ? t('industryFunds.showLess')
            : t('industryFunds.showAll').replace('{count}', String(rows.length))}
        </button>
      ) : null}
    </SectionCard>
  );
};

const IndustryScoreRow: React.FC<{
  row: IndustryRow;
  expanded: boolean;
  onToggle: () => void;
  t: Translate;
}> = ({ row, expanded, onToggle, t }) => (
  <>
    <tr className="border-b border-border/30 hover:bg-hover/40">
      <td className="py-2 pr-3 text-xs text-secondary-text">{row.rank}</td>
      <td className="py-2 pr-3">
        <span className="font-medium text-foreground">{row.name}</span>
        {row.leader ? (
          <span className="ml-2 text-xs text-secondary-text">
            {t('industryFunds.leaderShort')} {row.leader}
          </span>
        ) : null}
      </td>
      <td className="py-2 pr-3">
        <div className="flex items-center gap-2">
          <span className="w-10 font-semibold tabular-nums text-foreground">
            {formatScore(row.score, 1)}
          </span>
          <span className="h-1.5 w-20 overflow-hidden rounded-full bg-elevated">
            <span
              className="block h-full rounded-full bg-cyan"
              style={{ width: `${Math.max(0, Math.min(100, row.score ?? 0))}%` }}
            />
          </span>
        </div>
      </td>
      <td className={cn('py-2 pr-3 text-right tabular-nums', inOutTone(row.net))}>
        {formatSignedYi(row.net)}
      </td>
      <td className={cn('py-2 pr-3 text-right tabular-nums', inOutTone(row.net5D))}>
        {formatSignedYi(row.net5D)}
      </td>
      <td className={cn('py-2 pr-3 text-right tabular-nums', inOutTone(row.changePct))}>
        {formatPct(row.changePct, 2, true)}
      </td>
      <td className="py-2">
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={expanded}
          aria-label={t('industryFunds.scoreBreakdown')}
          className="rounded p-0.5 text-secondary-text transition-colors hover:text-foreground"
        >
          {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        </button>
      </td>
    </tr>

    {expanded ? (
      <tr className="border-b border-border/30 bg-elevated/40">
        <td colSpan={7} className="px-3 py-3">
          <p className="mb-2 text-xs text-secondary-text">{t('industryFunds.scoreBreakdown')}</p>
          <div className="grid gap-x-6 gap-y-1.5 sm:grid-cols-2 lg:grid-cols-3">
            {(row.components ?? []).map((component) => (
              // 归一化说明走 Tooltip 而不是原生 title —— 项目有 UI 治理测试禁止原生 title。
              <Tooltip key={component.key} content={component.normalization}>
                <div className="flex items-baseline justify-between gap-3 text-xs">
                  <span className="text-secondary-text">
                    {component.label}（{formatWeight(component.weight)}）
                  </span>
                  <span className="tabular-nums text-foreground">
                    {component.score === null ? '—' : `${formatScore(component.score, 1)} 分`}
                    <span className="ml-1 text-secondary-text">
                      {t('industryFunds.percentileShort')} {formatPercentile(component.percentile, 0)}
                    </span>
                  </span>
                </div>
              </Tooltip>
            ))}
          </div>
          <p className="mt-2 text-[11px] text-secondary-text">
            {t('industryFunds.industryScoreLimitation')}
          </p>
        </td>
      </tr>
    ) : null}
  </>
);
