/**
 * 实时新闻模块的格式化工具。
 *
 * 与组件分文件：Vite 的 react-refresh 规则要求一个模块只导出组件，
 * 混着导出普通函数会让热更新退化成整页刷新。
 */

const MINUTE = 60 * 1000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/**
 * 相对时间：「刚刚 / 12分钟前 / 3小时前 / 2天前」。
 *
 * 后端返回的是带时区偏移的 ISO8601（如 2026-09-07T19:13:00+08:00），
 * 这里按浏览器本地时间算差值，所以用户在任何时区看到的都是正确的相对时间。
 * 超过 7 天退回绝对日期，避免出现「48天前」这种读不出信息的文案。
 */
export const timeAgo = (iso: string | null | undefined, now: number = Date.now()): string => {
  if (!iso) return '';
  const parsed = new Date(iso);
  const timestamp = parsed.getTime();
  if (Number.isNaN(timestamp)) return '';

  const diff = now - timestamp;
  // 上游偶尔会给出略超前的时间戳（各家服务器时钟不完全同步），当作「刚刚」处理
  if (diff < MINUTE) return '刚刚';
  if (diff < HOUR) return `${Math.floor(diff / MINUTE)}分钟前`;
  if (diff < DAY) return `${Math.floor(diff / HOUR)}小时前`;
  if (diff < 7 * DAY) return `${Math.floor(diff / DAY)}天前`;
  return parsed.toLocaleDateString('zh-CN');
};

/** 悬浮/无障碍用的完整时间文案。 */
export const absoluteTime = (iso: string | null | undefined): string => {
  if (!iso) return '';
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return '';
  return parsed.toLocaleString('zh-CN', { hour12: false });
};
