import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import { MarketBriefPanel } from '../MarketBriefPanel';
import type { BriefEvidence, BriefWatchItem, MarketBriefPayload } from '../../../api/marketBrief';
import { useStockChartStore } from '../../../stores/stockChartStore';

const renderPanel = (payload: MarketBriefPayload) => render(
  <MemoryRouter>
    <MarketBriefPanel payload={payload} />
  </MemoryRouter>,
);

const evidence: BriefEvidence[] = [
  {
    id: 1,
    title: '工信部：全面建成新一代通信网',
    summary: '工信部发文提出通信网建设目标。',
    source: '财联社电报',
    publishedAt: '2026-09-06T10:00:00',
    url: 'https://news.example.com/a',
  },
  {
    id: 2,
    title: '某公司中标重大项目',
    summary: '合同金额约 12 亿元。',
    source: '公司公告',
    publishedAt: '2026-09-06T11:00:00',
    url: '',
  },
  {
    id: 3,
    title: '央行开展逆回购',
    summary: '净投放 2000 亿元。',
    source: '央行',
    publishedAt: '2026-09-06T09:30:00',
    url: 'https://news.example.com/c',
  },
];

const makePayload = (overrides: Partial<MarketBriefPayload> = {}): MarketBriefPayload => ({
  schemaVersion: '2026-09-08',
  mode: 'weekend',
  modeLabel: '周末',
  window: null,
  itemCount: 3,
  chunkCount: 1,
  model: 'stub-model',
  provider: 'stub',
  result: {
    schemaVersion: '2026-09-08',
    mode: 'weekend',
    modeLabel: '周末',
    windowStart: '2026-09-06T00:00:00',
    windowEnd: '2026-09-06T23:59:00',
    overallTone: 'bullish',
    summary: '政策面偏积极。',
    // 三层分别引用不同条数，便于用 aria-label 精确定位各自的「信源 N」
    events: [
      { topic: '工信部发文', detail: '通信网', category: '政策', importance: 'high', evidenceIds: [1, 2], mentionedTerms: ['通信网'] },
    ],
    sectors: [
      { name: '通信', direction: 'bullish', strength: 'medium', reason: '政策支持', evidenceIds: [1, 2, 3], mentionedTerms: ['通信网'] },
    ],
    watchList: [],
    takeaways: [
      { topic: '关注通信', detail: '看放量', evidenceIds: [1], mentionedTerms: ['通信网'] },
    ],
    risks: [],
    keywords: [],
    notCovered: [],
    watchListDisclaimer: '仅供参考。',
  },
  grounding: { dropped: [], droppedCount: 0, unverifiedTerms: [], invalidEvidenceIds: [] },
  coverage: { totalItems: 3, citedItems: 3 },
  evidence,
  degraded: false,
  degradedReason: '',
  llmCalls: 1,
  durationMs: 100,
  cached: false,
  createdAt: '2026-09-06T12:00:00',
  ...overrides,
});

describe('MarketBriefPanel evidence', () => {
  afterEach(() => cleanup());

  it('renders a 信源 button per fact-layer entry sized by resolvable references', () => {
    renderPanel(makePayload());
    // events→2、sectors→3、takeaways→1
    expect(screen.getByRole('button', { name: '查看 2 条引用信源原文' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '查看 3 条引用信源原文' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '查看 1 条引用信源原文' })).toBeInTheDocument();
  });

  it('opens the drawer with the referenced originals and only links real urls', () => {
    renderPanel(makePayload());
    // 点关键消息的「信源 2」→ 抽屉里出现 id=1、id=2 两条原文
    fireEvent.click(screen.getByRole('button', { name: '查看 2 条引用信源原文' }));
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('工信部：全面建成新一代通信网')).toBeInTheDocument();
    expect(within(dialog).getByText('某公司中标重大项目')).toBeInTheDocument();
    // 只有 id=1 带真实链接 -> 恰好一个「查看原文」外链；id=2 的 url 为空不渲染死链
    const links = within(dialog).getAllByRole('link', { name: '查看原文' });
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute('href', 'https://news.example.com/a');
  });

  it('renders no 信源 button and does not crash when evidence is absent', () => {
    // 旧版本生成的汇总没有 evidence 字段：按钮整体不出现，面板照常渲染
    renderPanel(makePayload({ evidence: undefined }));
    expect(screen.queryByRole('button', { name: /引用信源原文/ })).not.toBeInTheDocument();
    expect(screen.getByText('板块方向')).toBeInTheDocument();
  });
});

const withWatchList = (watchList: BriefWatchItem[]): MarketBriefPayload => {
  const payload = makePayload();
  return { ...payload, result: { ...payload.result, watchList } };
};

describe('MarketBriefPanel 关注股票', () => {
  afterEach(() => cleanup());

  beforeEach(() => {
    useStockChartStore.setState({ target: null });
  });

  it('点个股名把标的交给全局行情弹窗', () => {
    renderPanel(withWatchList([
      { name: '掌阅科技', code: '603533', sector: '传媒', direction: 'bullish', rationale: '主题标的', evidenceIds: [1] },
    ]));

    fireEvent.click(screen.getByRole('button', { name: '查看 掌阅科技(603533) 的实时行情与 K 线' }));

    // 弹窗挂在 App 全局，面板只负责把标的写进 store
    expect(useStockChartStore.getState().target).toEqual({ code: '603533', name: '掌阅科技' });
  });

  it('代码缺失或非 A 股时保持纯文本，不给行情入口', () => {
    renderPanel(withWatchList([
      { name: '无代码标的', code: null, sector: '传媒', direction: 'neutral', rationale: '', evidenceIds: [] },
      { name: '腾讯控股', code: 'HK00700', sector: '传媒', direction: 'bullish', rationale: '', evidenceIds: [] },
    ]));

    expect(screen.getByText('无代码标的')).toBeInTheDocument();
    expect(screen.getByText('HK00700')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /的实时行情与 K 线/ })).not.toBeInTheDocument();
    expect(useStockChartStore.getState().target).toBeNull();
  });
});
