import type React from 'react';
import { AlertTriangle, Sparkles } from 'lucide-react';
import type { UiTextKey } from '../../i18n/uiText';
import type { Conclusion, StockCandidate, StockCandidates } from '../../types/industryFunds';
import { cn } from '../../utils/cn';
import { Badge } from '../common/Badge';
import { InlineAlert } from '../common/InlineAlert';
import { SectionCard } from '../common/SectionCard';
import { STANCE_LABELS, formatPct, formatSignedYi, formatYiFromYuan, inOutTone } from './format';

type Translate = (key: UiTextKey) => string;

type Props = {
  conclusion: Conclusion;
  candidates: StockCandidates;
  disclaimers: { flow?: string; investment?: string };
  t: Translate;
};

const STANCE_BADGE: Record<string, 'success' | 'default' | 'danger'> = {
  bullish: 'success',
  neutral: 'default',
  bearish: 'danger',
};

/** 第三段：结论 —— 关注方向、关注个股、风险提示、免责声明。 */
export const ConclusionSection: React.FC<Props> = ({
  conclusion,
  candidates,
  disclaimers,
  t,
}) => {
  const isFallback = conclusion.generatedBy === 'rule_fallback';
  const rows = candidates.candidates ?? [];

  return (
    <SectionCard
      title={t('industryFunds.sectionConclusion')}
      subtitle={t('industryFunds.sectionConclusionHint')}
      actions={
        <Badge variant={isFallback ? 'warning' : 'info'} size="sm">
          <Sparkles className="mr-1 inline h-3 w-3" aria-hidden="true" />
          {isFallback ? t('industryFunds.rulesFallback') : t('industryFunds.llmGenerated')}
        </Badge>
      }
    >
      {conclusion.summary ? (
        <p className="mb-5 whitespace-pre-wrap text-sm leading-relaxed text-foreground">
          {conclusion.summary}
        </p>
      ) : null}

      {isFallback ? (
        <InlineAlert
          variant="info"
          className="mb-4"
          message={t('industryFunds.rulesFallbackHint')}
        />
      ) : null}

      {conclusion.warnings?.length ? (
        <InlineAlert
          variant="warning"
          className="mb-4"
          message={
            <ul className="list-disc space-y-1 pl-4">
              {conclusion.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          }
        />
      ) : null}

      {conclusion.directions?.length ? (
        <div className="mb-6">
          <h3 className="mb-2 text-sm font-semibold text-foreground">
            {t('industryFunds.directions')}
          </h3>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {conclusion.directions.map((direction, index) => (
              <div
                key={`${direction.name}-${index}`}
                className="rounded-xl border border-border/50 bg-elevated/30 p-3"
              >
                <div className="mb-1.5 flex items-center justify-between gap-2">
                  <span className="font-medium text-foreground">{direction.name}</span>
                  <Badge variant={STANCE_BADGE[direction.stance] ?? 'default'} size="sm">
                    {STANCE_LABELS[direction.stance] ?? direction.stance}
                  </Badge>
                </div>
                <p className="text-xs leading-relaxed text-secondary-text">{direction.reason}</p>
                {direction.evidence?.length ? (
                  <ul className="mt-2 space-y-0.5 text-[11px] text-secondary-text">
                    {direction.evidence.map((item, evidenceIndex) => (
                      <li key={`${item}-${evidenceIndex}`}>· {item}</li>
                    ))}
                  </ul>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}

      <div className="mb-6">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-semibold text-foreground">
            {t('industryFunds.watchStocks')}
          </h3>
          {Object.entries(candidates.sources ?? {}).map(([label, count]) => (
            <Badge key={label} variant="default" size="sm">
              {label} {count}
            </Badge>
          ))}
          {candidates.outflowFlaggedCount > 0 ? (
            <Badge variant="danger" size="sm">
              <AlertTriangle className="mr-1 inline h-3 w-3" aria-hidden="true" />
              {t('industryFunds.outflowFlagged').replace(
                '{count}',
                String(candidates.outflowFlaggedCount)
              )}
            </Badge>
          ) : null}
        </div>

        {candidates.warnings?.length ? (
          <InlineAlert
            variant="warning"
            className="mb-3"
            message={
              <ul className="list-disc space-y-1 pl-4">
                {candidates.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            }
          />
        ) : null}

        {rows.length === 0 ? (
          <p className="text-sm text-secondary-text">{t('industryFunds.noCandidates')}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[62rem] border-collapse text-sm">
              <thead>
                <tr className="border-b border-border/60 text-left text-xs text-secondary-text">
                  <th className="py-2 pr-3 font-medium">{t('industryFunds.colCode')}</th>
                  <th className="py-2 pr-3 font-medium">{t('industryFunds.colName')}</th>
                  <th className="py-2 pr-3 font-medium">{t('industryFunds.colReason')}</th>
                  <th className="py-2 pr-3 text-right font-medium">
                    {t('industryFunds.colInstitutionNet')}
                  </th>
                  <th className="py-2 pr-3 text-right font-medium">
                    {t('industryFunds.colMainNet')}
                  </th>
                  <th className="py-2 pr-3 font-medium">{t('industryFunds.colIndustry')}</th>
                  <th className="py-2 pr-3 text-right font-medium">{t('industryFunds.colChange')}</th>
                  <th className="py-2 pr-3 font-medium">{t('industryFunds.colNote')}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <CandidateRow key={row.code} row={row} />
                ))}
              </tbody>
            </table>
          </div>
        )}

        {candidates.droppedCount > 0 ? (
          <p className="mt-3 text-xs text-secondary-text">
            {t('industryFunds.droppedByIndustry').replace(
              '{count}',
              String(candidates.droppedCount)
            )}
          </p>
        ) : null}
      </div>

      {conclusion.risks?.length ? (
        <div className="mb-5">
          <h3 className="mb-2 text-sm font-semibold text-foreground">{t('industryFunds.risks')}</h3>
          <ul className="space-y-1 text-xs leading-relaxed text-secondary-text">
            {conclusion.risks.map((risk) => (
              <li key={risk} className="flex gap-1.5">
                <span className="text-danger">·</span>
                <span>{risk}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="space-y-2 rounded-xl border border-border/40 bg-elevated/30 p-3 text-[11px] leading-relaxed text-secondary-text">
        {disclaimers.flow ? <p>{disclaimers.flow}</p> : null}
        {disclaimers.investment ? <p>{disclaimers.investment}</p> : null}
      </div>
    </SectionCard>
  );
};

const CandidateRow: React.FC<{ row: StockCandidate }> = ({ row }) => {
  const metrics = row.metrics ?? {};
  return (
    <tr className={cn('border-b border-border/30', row.flowWarning && 'bg-danger/5')}>
      <td className="py-2 pr-3 font-mono text-xs text-secondary-text">{row.code}</td>
      <td className="py-2 pr-3 font-medium text-foreground">{row.name}</td>
      <td className="py-2 pr-3 text-xs text-secondary-text">
        <ul className="space-y-0.5">
          {row.reasons.map((reason) => (
            <li key={reason}>· {reason}</li>
          ))}
        </ul>
      </td>
      <td className={cn('py-2 pr-3 text-right tabular-nums', inOutTone(metrics.institutionNet))}>
        {formatYiFromYuan(metrics.institutionNet)}
      </td>
      <td className={cn('py-2 pr-3 text-right tabular-nums', inOutTone(metrics.mainNet))}>
        {formatYiFromYuan(metrics.mainNet)}
      </td>
      <td className="py-2 pr-3 text-xs text-secondary-text">
        {metrics.industry ? (
          <span>
            {metrics.industry}
            <span className={cn('ml-1', inOutTone(metrics.industryNet))}>
              {formatSignedYi(metrics.industryNet, 1)}
            </span>
          </span>
        ) : (
          '—'
        )}
      </td>
      <td className={cn('py-2 pr-3 text-right tabular-nums', inOutTone(metrics.changePct))}>
        {formatPct(metrics.changePct, 2, true)}
      </td>
      <td className="py-2 pr-3 text-xs">
        {row.flowWarning ? (
          <span className="text-danger">{row.flowWarning}</span>
        ) : metrics.reason ? (
          <span className="text-secondary-text">{metrics.reason}</span>
        ) : (
          <span className="text-secondary-text">—</span>
        )}
      </td>
    </tr>
  );
};
