import type React from 'react';
import { PolarAngleAxis, PolarGrid, PolarRadiusAxis, Radar, RadarChart, ResponsiveContainer } from 'recharts';
import type { KlineRow } from '../../api/baiduFinance';
import { PriceMomentumSection } from './PriceMomentumSection';
import type { PriceScore } from './PriceMomentumSection';
import { text } from './format';

/**
 * 分析：综合评分雷达 + 五个分项评分。
 *
 * 雷达只有一条序列，标题已经点明它是什么，因此不放图例；评分同时以数字给出，
 * 并附表格视图 —— 雷达面积容易被高估，数字才是可核对的那一份。
 */

type Radar5 = {
  headers?: string[];
  keys?: string[];
  data?: Array<{ name?: string; values?: string[] }>;
};

type ScoreEntry = { title?: string; score?: string; desc?: string; updateTime?: string };

type AnalysisData = {
  synthesisScore?: {
    title?: string;
    rating?: string;
    desc?: string;
    industryRanking?: string;
    marketRanking?: string;
    firstIndustryName?: string;
    updateTime?: string;
    radar?: Radar5;
  };
  priceScore?: PriceScore;
  incomeScore?: ScoreEntry;
  valuationScore?: ScoreEntry;
  financeScore?: ScoreEntry;
  riskScore?: ScoreEntry;
};

/** 价格动量单独成块（见 PriceMomentumSection），这里只放其余四项。 */
const SUB_SCORES: Array<keyof AnalysisData> = [
  'incomeScore',
  'valuationScore',
  'financeScore',
  'riskScore',
];

const clampScore = (value: unknown): number => {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return 0;
  return Math.max(0, Math.min(10, parsed));
};

export const AnalysisTab: React.FC<{
  data?: AnalysisData;
  klineRows?: KlineRow[];
  pressure?: string;
  support?: string;
  price?: number;
  levelsUpdateTime?: string;
}> = ({ data, klineRows, pressure, support, price, levelsUpdateTime }) => {
  if (!data?.synthesisScore) {
    return <p className="py-10 text-center text-sm text-muted-text">暂无分析数据</p>;
  }

  const synthesis = data.synthesisScore;
  const radar = synthesis.radar;
  const labels = radar?.headers ?? [];
  const values = radar?.data?.[0]?.values ?? [];
  const radarRows = labels.map((label, index) => ({
    dimension: label,
    score: clampScore(values[index]),
  }));

  const subScores = SUB_SCORES
    .map((key) => data[key] as ScoreEntry | undefined)
    .filter((entry): entry is ScoreEntry => Boolean(entry?.title));

  return (
    <div className="space-y-4">
      <section className="grid items-start gap-4 rounded-2xl border border-border bg-elevated/40 p-4 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div>
          <h3 className="text-sm font-semibold text-foreground">{text(synthesis.title, '综合评分')}</h3>
          <div className="mt-2 flex items-baseline gap-2">
            <span className="text-4xl font-semibold tabular-nums text-[hsl(var(--primary))]">
              {text(synthesis.rating)}
            </span>
            <span className="text-sm text-muted-text">/ 10</span>
          </div>
          <p className="mt-1 text-sm text-secondary-text">{text(synthesis.desc)}</p>
          <dl className="mt-4 space-y-2 text-sm">
            <div className="flex items-baseline justify-between gap-3">
              <dt className="text-muted-text">行业排名（{text(synthesis.firstIndustryName)}）</dt>
              <dd className="tabular-nums text-foreground">{text(synthesis.industryRanking)}</dd>
            </div>
            <div className="flex items-baseline justify-between gap-3">
              <dt className="text-muted-text">全市场排名</dt>
              <dd className="tabular-nums text-foreground">{text(synthesis.marketRanking)}</dd>
            </div>
          </dl>
          {synthesis.updateTime ? (
            <p className="mt-3 text-xs text-muted-text">更新于 {synthesis.updateTime}</p>
          ) : null}
        </div>

        {radarRows.length ? (
          <div>
            <div className="h-52 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <RadarChart data={radarRows} outerRadius="72%">
                  <PolarGrid stroke="var(--chart-grid)" />
                  <PolarAngleAxis dataKey="dimension" tick={{ fontSize: 11, fill: 'hsl(var(--secondary-text))' }} />
                  <PolarRadiusAxis domain={[0, 10]} tick={false} axisLine={false} />
                  <Radar
                    name="综合评分"
                    dataKey="score"
                    stroke="hsl(var(--primary))"
                    strokeWidth={2}
                    fill="hsl(var(--primary))"
                    fillOpacity={0.18}
                  />
                </RadarChart>
              </ResponsiveContainer>
            </div>
            {/* 各维度的具体分值由下方分项卡片直接给出，这里不再重复一张表 */}
            <p className="text-center text-xs text-muted-text">各维度 0-10 分，具体见下方分项</p>
          </div>
        ) : null}
      </section>

      <PriceMomentumSection
        priceScore={data.priceScore}
        klineRows={klineRows ?? []}
        pressure={pressure}
        support={support}
        price={price}
        levelsUpdateTime={levelsUpdateTime}
      />

      <div className="grid gap-3 sm:grid-cols-2">
        {subScores.map((entry) => (
          <section key={entry.title} className="rounded-2xl border border-border bg-elevated/40 p-4">
            <div className="flex items-baseline justify-between gap-3">
              <h4 className="text-sm font-semibold text-foreground">{entry.title}</h4>
              <span className="text-lg font-semibold tabular-nums text-[hsl(var(--primary))]">
                {text(entry.score)}
                <span className="ml-0.5 text-xs font-normal text-muted-text">/10</span>
              </span>
            </div>
            {/* 评分条：单序列，长度即量级，数字已在上方直接标注 */}
            <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-hover/40">
              <div
                className="h-full rounded-full bg-[hsl(var(--primary))]"
                style={{ width: `${clampScore(entry.score) * 10}%` }}
              />
            </div>
            {entry.desc ? (
              <p className="mt-2.5 text-sm leading-relaxed text-secondary-text">{entry.desc}</p>
            ) : null}
          </section>
        ))}
      </div>
    </div>
  );
};
