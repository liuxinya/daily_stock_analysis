import type React from 'react';
import { Collapsible } from '../common';
import type { UiTextKey } from '../../i18n/uiText';
import type { DabanExcluded, DabanPrinciple } from '../../types/daban';
import { PrincipleTags, StockLink } from './shared';

type Translate = (key: UiTextKey, params?: Record<string, string | number>) => string;

/** ⑦ 排除名单：中位股、跟风、死亡换手……每只都写明为什么不做。 */
export const ExcludedList: React.FC<{
  excluded: DabanExcluded[];
  principles: Record<string, DabanPrinciple>;
  t: Translate;
}> = ({ excluded, principles, t }) => {
  if (!excluded?.length) return null;
  return (
    <Collapsible title={t('dabanKing.excludedTitle', { count: excluded.length })}>
      <ul className="space-y-1.5 px-4 pb-4">
        {excluded.map((item) => (
          <li key={item.code} className="flex flex-col gap-0.5 text-xs sm:flex-row sm:items-center sm:gap-3">
            <span className="w-44 shrink-0">
              <StockLink code={item.code} name={item.name} />
            </span>
            <span className="flex-1 text-secondary-text">
              {item.reason} <PrincipleTags ids={item.principleIds} principles={principles} />
            </span>
          </li>
        ))}
      </ul>
    </Collapsible>
  );
};
