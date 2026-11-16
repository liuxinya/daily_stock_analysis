import type React from 'react';
import { Tooltip } from '../common';

/** 表头旁的小问号：悬停显示这个指标的口径（怎么算、含什么、不含什么）。 */
export const HelpTip: React.FC<{ text: string }> = ({ text }) => (
  <Tooltip content={<span className="block max-w-[16rem] text-[11px] leading-relaxed">{text}</span>} side="top">
    <span
      data-testid="help-tip"
      className="ml-1 inline-flex h-3.5 w-3.5 cursor-help items-center justify-center rounded-full border border-border/70 text-[9px] leading-none text-muted-text"
    >
      ?
    </span>
  </Tooltip>
);
