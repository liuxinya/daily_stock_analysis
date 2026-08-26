import type React from 'react';
import { DataTable } from './shared';
import { text } from './format';

/**
 * 股评：看涨/看跌投票 + 股吧帖子。
 *
 * 投票用单条占比条表达（两段 + 2px 间隙），两侧常驻人数与百分比 —— 红绿在
 * 色盲视角下 ΔE≈8，不能只靠颜色区分多空。
 */

type VotePeriod = {
  title?: string;
  type?: string;
  voteUp?: string;
  voteDown?: string;
  voteUpRate?: string;
  voteDownRate?: string;
};

type VoteData = {
  voteUp?: string;
  voteDown?: string;
  voteUpRate?: string;
  voteDownRate?: string;
  totalNum?: string;
  voteRecords?: { voteRes?: VotePeriod[] };
};

type Post = {
  author?: { name?: string };
  content?: { items?: Array<{ type?: string; data?: string }> };
  like_count?: string;
  reply_count?: string;
  provider?: string;
  create_show_time?: string;
  thread_id?: string;
  comment_id?: string;
};

const postText = (post: Post): string =>
  (post.content?.items ?? [])
    .filter((item) => item.type === 'text' || !item.type)
    .map((item) => item.data ?? '')
    .join('')
    .trim();

const ratioToPercent = (value?: string): number => {
  const parsed = Number(String(value ?? '').replace('%', ''));
  return Number.isFinite(parsed) ? Math.max(0, Math.min(100, parsed)) : 50;
};

const VoteBar: React.FC<{ upRate?: string; downRate?: string }> = ({ upRate, downRate }) => {
  const up = ratioToPercent(upRate);
  return (
    <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-hover/40" role="img" aria-label={`看涨 ${text(upRate)}，看跌 ${text(downRate)}`}>
      <div className="h-full rounded-l-full bg-[var(--chart-up)]" style={{ width: `${up}%` }} />
      {/* 2px 表面间隙：相邻填充之间留缝，避免两段看起来是一条 */}
      <div className="h-full w-0.5 shrink-0 bg-elevated" />
      <div className="h-full grow rounded-r-full bg-[var(--chart-down)]" />
    </div>
  );
};

export const TalksTab: React.FC<{ vote?: VoteData; posts?: Post[] }> = ({ vote, posts }) => {
  const periods = vote?.voteRecords?.voteRes ?? [];
  const list = posts ?? [];

  return (
    <div className="space-y-4">
      <section className="rounded-2xl border border-border bg-elevated/40 p-4">
        <div className="mb-3 flex items-baseline justify-between gap-3">
          <h3 className="text-sm font-semibold text-foreground">全网股民热议</h3>
          <span className="text-xs text-muted-text">共 {text(vote?.totalNum)} 人参与</span>
        </div>

        {vote ? (
          <>
            <div className="mb-2 flex items-baseline justify-between gap-3 text-sm">
              <span className="text-[var(--chart-up)]">
                看涨 <span className="tabular-nums font-semibold">{text(vote.voteUp)}</span>
                <span className="ml-1 text-xs">{text(vote.voteUpRate)}</span>
              </span>
              <span className="text-[var(--chart-down)]">
                <span className="mr-1 text-xs">{text(vote.voteDownRate)}</span>
                <span className="tabular-nums font-semibold">{text(vote.voteDown)}</span> 看跌
              </span>
            </div>
            <VoteBar upRate={vote.voteUpRate} downRate={vote.voteDownRate} />

            {periods.length ? (
              <div className="mt-4">
                <DataTable
                  header={['周期', '看涨', '看跌', '看涨占比']}
                  rows={periods.map((period) => [
                    text(period.title),
                    <span className="text-[var(--chart-up)]">{text(period.voteUp)}</span>,
                    <span className="text-[var(--chart-down)]">{text(period.voteDown)}</span>,
                    text(period.voteUpRate),
                  ])}
                />
              </div>
            ) : null}
          </>
        ) : (
          <p className="text-sm text-muted-text">暂无投票数据</p>
        )}
      </section>

      <section className="rounded-2xl border border-border bg-elevated/40 p-4">
        <h3 className="mb-3 text-sm font-semibold text-foreground">股吧讨论</h3>
        {list.length ? (
          <ul className="divide-y divide-border/60">
            {list.map((post, index) => {
              const body = postText(post);
              if (!body) return null;
              return (
                <li key={post.comment_id ?? post.thread_id ?? index} className="py-3 first:pt-0 last:pb-0">
                  <p className="text-sm leading-relaxed text-foreground">{body}</p>
                  <p className="mt-1.5 flex flex-wrap items-center gap-x-3 text-xs text-muted-text">
                    <span>{text(post.author?.name, '匿名')}</span>
                    <span>{text(post.create_show_time)}</span>
                    {post.provider ? <span>来自 {post.provider}</span> : null}
                    <span className="tabular-nums">赞 {text(post.like_count, '0')}</span>
                    <span className="tabular-nums">评 {text(post.reply_count, '0')}</span>
                  </p>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="py-6 text-center text-sm text-muted-text">暂无讨论</p>
        )}
      </section>
    </div>
  );
};
