import type React from 'react';
import { DataTable, KeyValueGrid } from './shared';
import { text, trendClass } from './format';

/**
 * 简况：公司资料 / 股本股东 / 机构评级 / 高管。
 *
 * 上游把这些块塞在 content.newCompany 下，且字段随标的类型变化，因此这里全部
 * 走可选读取 —— 缺哪块就不渲染哪块，不整页报错。
 */

type LinkItem = { text?: string };
type InfoItem = { text?: string; type?: string; value?: unknown };

type NewCompany = {
  basicInfo?: {
    companyName?: string;
    releaseDate?: string;
    issuePrice?: string;
    issueNumber?: string;
    region?: string;
    mainBusiness?: string;
    industry?: LinkItem[];
    area?: LinkItem[];
    concepts?: LinkItem[];
  };
  shareholderEquity?: { title?: string; info?: InfoItem[] };
  organRating?: {
    title?: string;
    organNum?: string;
    curPrice?: string;
    avgPrice?: string;
    maxPrice?: string;
    minPrice?: string;
    header?: string[];
    body?: Array<{ organ?: string; date?: string; rating?: string; price?: string }>;
  };
  executiveInfo?: {
    title?: string;
    header?: string[];
    body?: Array<{ executive?: string; post?: string; holdingCapital?: string; holdingCapitalChange?: string }>;
  };
};

const Block: React.FC<{ title: string; children: React.ReactNode; aside?: React.ReactNode }> = ({
  title,
  children,
  aside,
}) => (
  <section className="rounded-2xl border border-border bg-elevated/40 p-4">
    <div className="mb-3 flex items-baseline justify-between gap-3">
      <h3 className="text-sm font-semibold text-foreground">{title}</h3>
      {aside}
    </div>
    {children}
  </section>
);

const Chips: React.FC<{ items?: LinkItem[] }> = ({ items }) => {
  const labels = (items ?? []).map((item) => item.text).filter(Boolean) as string[];
  if (!labels.length) return <span className="text-sm text-muted-text">--</span>;
  return (
    <div className="flex flex-wrap gap-1.5">
      {labels.map((label) => (
        <span key={label} className="rounded-full border border-border px-2 py-0.5 text-xs text-secondary-text">
          {label}
        </span>
      ))}
    </div>
  );
};

/** shareholderEquity.info 的 value 可能是字符串，也可能是 {sum, qoq} 结构。 */
const InfoValue: React.FC<{ item: InfoItem }> = ({ item }) => {
  const value = item.value;
  if (value && typeof value === 'object' && 'sum' in (value as Record<string, unknown>)) {
    const composite = value as { sum?: string; qoq?: string };
    return (
      <span className="tabular-nums">
        {text(composite.sum)}
        {composite.qoq ? <span className={`ml-1.5 text-xs ${trendClass(composite.qoq)}`}>{composite.qoq}</span> : null}
      </span>
    );
  }
  return <span className="tabular-nums">{text(value)}</span>;
};

export const ProfileTab: React.FC<{ data?: NewCompany }> = ({ data }) => {
  if (!data) {
    return <p className="py-10 text-center text-sm text-muted-text">暂无简况数据</p>;
  }

  const basic = data.basicInfo;
  const rating = data.organRating;

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Block title="公司资料">
        <KeyValueGrid
          columns={2}
          items={[
            { label: '公司名称', value: text(basic?.companyName) },
            { label: '所属行业', value: text(basic?.industry?.[0]?.text) },
            { label: '上市日期', value: text(basic?.releaseDate) },
            { label: '发行价', value: text(basic?.issuePrice) },
            { label: '发行数量', value: text(basic?.issueNumber) },
            { label: '所属地区', value: text(basic?.region) },
          ]}
        />
        <div className="mt-4 space-y-3">
          <div>
            <p className="mb-1.5 text-xs text-muted-text">概念板块</p>
            <Chips items={basic?.concepts} />
          </div>
          <div>
            <p className="mb-1.5 text-xs text-muted-text">经营范围</p>
            <p className="text-sm leading-relaxed text-secondary-text">{text(basic?.mainBusiness)}</p>
          </div>
        </div>
      </Block>

      <Block title={text(data.shareholderEquity?.title, '股本股东')}>
        <dl className="space-y-2.5">
          {(data.shareholderEquity?.info ?? []).map((item, index) => (
            <div key={item.text ?? index} className="flex items-baseline justify-between gap-3 text-sm">
              <dt className="text-muted-text">{text(item.text)}</dt>
              <dd className="text-right font-medium text-foreground">
                <InfoValue item={item} />
              </dd>
            </div>
          ))}
          {!data.shareholderEquity?.info?.length ? (
            <p className="text-sm text-muted-text">暂无数据</p>
          ) : null}
        </dl>
      </Block>

      {rating ? (
        <Block
          title={text(rating.title, '机构评级')}
          aside={<span className="text-xs text-muted-text">{text(rating.organNum)} 家机构</span>}
        >
          <KeyValueGrid
            columns={2}
            className="mb-3"
            items={[
              { label: '当前价', value: text(rating.curPrice) },
              { label: '平均目标价', value: text(rating.avgPrice) },
              { label: '最高目标价', value: text(rating.maxPrice) },
              { label: '最低目标价', value: text(rating.minPrice) },
            ]}
          />
          <DataTable
            header={rating.header ?? ['发布机构', '发布日期', '评级 / 目标价']}
            rows={(rating.body ?? []).map((row) => [
              text(row.organ),
              text(row.date),
              `${text(row.rating)} / ${text(row.price)}`,
            ])}
          />
        </Block>
      ) : null}

      {data.executiveInfo ? (
        <Block title={text(data.executiveInfo.title, '高管')}>
          <DataTable
            header={data.executiveInfo.header ?? ['高管', '职务', '持股变动']}
            rows={(data.executiveInfo.body ?? []).slice(0, 12).map((row) => [
              text(row.executive),
              <span className="text-xs leading-snug">{text(row.post)}</span>,
              `${text(row.holdingCapital)} / ${text(row.holdingCapitalChange)}`,
            ])}
          />
        </Block>
      ) : null}
    </div>
  );
};
