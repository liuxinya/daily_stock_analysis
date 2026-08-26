import type React from 'react';
import type { KlineRow } from '../../api/baiduFinance';
import { SupportResistancePanel } from './SupportResistancePanel';
import { DataTable } from './shared';
import { text, toNumber, trendClass } from './format';

/**
 * 价格动量：压力位/支撑位 + 技术面分析 + 资金面分析。
 *
 * 上游把这三块都塞在 analysis.priceScore 下，原站也是放在同一屏，因此这里合成一个
 * 区块，而不是拆成三张互不相关的卡。
 */

type IncreaseItem = { text?: string; name?: string; increase?: string; financeType?: string };

export type PriceScore = {
  title?: string;
  score?: string;
  updateTime?: string;
  technologyScore?: {
    title?: string;
    desc?: string;
    increase?: { title?: string; updateTime?: string; items?: IncreaseItem[] };
  };
  capitalScore?: {
    title?: string;
    desc?: string;
    panelProportion?: string;
    panelHeaders?: string[];
    fundflow?: { title?: string; header?: string[]; updateTime?: string; body?: Array<Record<string, string>> };
  };
};

/** 近 N 日涨跌幅对比：条长表示幅度绝对值，方向由正负号与颜色共同表达。 */
const IncreaseBars: React.FC<{ items: IncreaseItem[] }> = ({ items }) => {
  const peak = Math.max(0.01, ...items.map((item) => Math.abs(toNumber(item.increase))));
  return (
    <ul className="space-y-2">
      {items.map((item, index) => {
        const value = toNumber(item.increase);
        const width = `${Math.min(100, (Math.abs(value) / peak) * 100)}%`;
        return (
          <li key={`${item.name ?? index}`} className="grid grid-cols-[6rem_1fr_4.5rem] items-center gap-3">
            <span className="truncate text-xs text-secondary-text">{text(item.text ?? item.name)}</span>
            <span className="h-3 w-full overflow-hidden rounded-sm bg-hover/40">
              <span
                className="block h-full rounded-sm"
                style={{ width, background: value >= 0 ? 'var(--chart-up)' : 'var(--chart-down)' }}
              />
            </span>
            <span className={`text-right text-xs tabular-nums ${trendClass(item.increase)}`}>
              {text(item.increase)}
            </span>
          </li>
        );
      })}
    </ul>
  );
};

/** 控盘程度：单值落在四档区间上，档位名常驻，不靠颜色深浅表达。 */
const ControlGauge: React.FC<{ proportion?: string; headers?: string[] }> = ({ proportion, headers }) => {
  const ratio = Number(proportion);
  if (!Number.isFinite(ratio) || !headers?.length) return null;
  const pct = Math.max(0, Math.min(100, ratio * 100));
  const bucket = Math.min(headers.length - 1, Math.floor(ratio * headers.length));

  return (
    <div>
      <div className="mb-1.5 flex items-baseline justify-between text-xs">
        <span className="text-muted-text">控盘程度</span>
        <span className="font-medium text-foreground">
          {headers[bucket]}
          <span className="ml-1.5 tabular-nums text-muted-text">{pct.toFixed(1)}%</span>
        </span>
      </div>
      <div className="relative h-1.5 w-full overflow-hidden rounded-full bg-hover/40">
        <div className="h-full rounded-full bg-[hsl(var(--primary))]" style={{ width: `${pct}%` }} />
      </div>
      <ul className="mt-1 flex justify-between text-[11px] text-muted-text">
        {headers.map((label) => <li key={label}>{label}</li>)}
      </ul>
    </div>
  );
};

type Props = {
  priceScore?: PriceScore;
  klineRows: KlineRow[];
  pressure?: string;
  support?: string;
  price?: number;
  levelsUpdateTime?: string;
};

export const PriceMomentumSection: React.FC<Props> = ({
  priceScore,
  klineRows,
  pressure,
  support,
  price,
  levelsUpdateTime,
}) => {
  const tech = priceScore?.technologyScore;
  const capital = priceScore?.capitalScore;
  const flow = capital?.fundflow;

  return (
    <section className="space-y-5 rounded-2xl border border-border bg-elevated/40 p-4">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-sm font-semibold text-foreground">
          {text(priceScore?.title, '价格动量')}
          {priceScore?.score ? (
            <span className="ml-2 text-lg font-semibold tabular-nums text-[hsl(var(--primary))]">
              {priceScore.score}
              <span className="ml-0.5 text-xs font-normal text-muted-text">/10</span>
            </span>
          ) : null}
        </h3>
        {priceScore?.updateTime ? (
          <span className="text-xs text-muted-text">更新日期 {priceScore.updateTime}</span>
        ) : null}
      </div>

      <div>
        <h4 className="mb-2 text-sm font-medium text-foreground">压力位 / 支撑位</h4>
        <SupportResistancePanel
          rows={klineRows}
          pressure={pressure}
          support={support}
          price={price}
          updateTime={levelsUpdateTime}
        />
      </div>

      {tech ? (
        <div>
          <h4 className="mb-1.5 text-sm font-medium text-foreground">{text(tech.title, '技术面分析')}</h4>
          {tech.desc ? <p className="mb-3 text-sm leading-relaxed text-secondary-text">{tech.desc}</p> : null}
          {tech.increase?.items?.length ? (
            <>
              <div className="mb-2 flex items-baseline justify-between gap-3">
                <span className="text-xs text-muted-text">{text(tech.increase.title)}</span>
                {tech.increase.updateTime ? (
                  <span className="text-xs text-muted-text">{tech.increase.updateTime}</span>
                ) : null}
              </div>
              <IncreaseBars items={tech.increase.items} />
            </>
          ) : null}
        </div>
      ) : null}

      {capital ? (
        <div>
          <h4 className="mb-1.5 text-sm font-medium text-foreground">{text(capital.title, '资金面分析')}</h4>
          {capital.desc ? <p className="mb-3 text-sm leading-relaxed text-secondary-text">{capital.desc}</p> : null}
          <div className="grid gap-4 lg:grid-cols-2">
            {flow?.body?.length ? (
              <div>
                <div className="mb-1 flex items-baseline justify-between gap-3">
                  <span className="text-xs text-muted-text">{text(flow.title, '主力资金流向')}</span>
                  {flow.updateTime ? <span className="text-xs text-muted-text">{flow.updateTime}</span> : null}
                </div>
                <DataTable
                  header={flow.header ?? ['周期', '净流入', '净占比']}
                  rows={flow.body.map((row) => [
                    text(row.name),
                    <span className={trendClass(row.in)}>{text(row.in)}</span>,
                    <span className={trendClass(row.out)}>{text(row.out)}</span>,
                  ])}
                />
              </div>
            ) : null}
            <ControlGauge proportion={capital.panelProportion} headers={capital.panelHeaders} />
          </div>
        </div>
      ) : null}
    </section>
  );
};
