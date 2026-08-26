/**
 * useAutocomplete hook tests.
 */

import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useAutocomplete } from '../useAutocomplete';
import type { StockIndexItem } from '../../types/stockIndex';

const searchStocksMock = vi.fn();

vi.mock('../../utils/searchStocks', () => ({
  searchStocks: (...args: unknown[]) => searchStocksMock(...args),
}));

const mockIndex: StockIndexItem[] = [
  {
    canonicalCode: '600519.SH',
    displayCode: '600519',
    nameZh: '贵州茅台',
    pinyinFull: 'guizhoumaotai',
    pinyinAbbr: 'gzmt',
    aliases: ['茅台'],
    market: 'CN',
    assetType: 'stock',
    active: true,
    popularity: 100,
  },
];

describe('useAutocomplete', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('activates runtime fallback when search throws', () => {
    searchStocksMock.mockImplementation(() => {
      throw new Error('Search exploded');
    });

    const { result } = renderHook(() => useAutocomplete(mockIndex, { debounceMs: 10 }));

    act(() => {
      result.current.setQuery('600519');
    });

    act(() => {
      vi.advanceTimersByTime(10);
    });

    expect(result.current.runtimeFallback).toBe(true);
    expect(result.current.error).toBeInstanceOf(Error);
    expect(result.current.isOpen).toBe(false);
    expect(result.current.suggestions).toEqual([]);
  });

  it('keeps suggestions open without auto-highlighting the first result', () => {
    searchStocksMock.mockReturnValue([
      {
        canonicalCode: '600519.SH',
        displayCode: '600519',
        nameZh: '贵州茅台',
        market: 'CN',
        matchType: 'exact',
        matchField: 'code',
        score: 100,
      },
    ]);

    const { result } = renderHook(() => useAutocomplete(mockIndex, { debounceMs: 10 }));

    act(() => {
      result.current.setQuery('600519');
    });

    act(() => {
      vi.advanceTimersByTime(10);
    });

    expect(result.current.isOpen).toBe(true);
    expect(result.current.suggestions).toHaveLength(1);
    expect(result.current.highlightedIndex).toBe(-1);
  });

  it('setQuerySilently syncs the query without reopening the dropdown', () => {
    searchStocksMock.mockReturnValue([
      {
        canonicalCode: '600519.SH',
        displayCode: '600519',
        nameZh: '贵州茅台',
        market: 'CN',
        matchType: 'exact',
        matchField: 'code',
        score: 100,
      },
    ]);

    const { result } = renderHook(() => useAutocomplete(mockIndex, { debounceMs: 10 }));

    act(() => {
      result.current.setQuery('600519');
    });
    act(() => {
      vi.advanceTimersByTime(10);
    });
    expect(result.current.isOpen).toBe(true);

    // 选中候选项后，外部 value 回填会再同步一次 query。
    act(() => {
      result.current.close();
      result.current.setQuerySilently('600519');
    });

    // 关键回归点：静默同步不得安排搜索，防抖窗口过后下拉框仍须保持关闭。
    act(() => {
      vi.advanceTimersByTime(50);
    });

    expect(result.current.query).toBe('600519');
    expect(result.current.isOpen).toBe(false);
    expect(result.current.suggestions).toHaveLength(0);
  });

  it('setQuerySilently cancels a debounced search that is still pending', () => {
    searchStocksMock.mockReturnValue([
      {
        canonicalCode: '600519.SH',
        displayCode: '600519',
        nameZh: '贵州茅台',
        market: 'CN',
        matchType: 'exact',
        matchField: 'code',
        score: 100,
      },
    ]);

    const { result } = renderHook(() => useAutocomplete(mockIndex, { debounceMs: 10 }));

    // 最后一次按键的搜索还在防抖窗口里就选中了候选项。
    act(() => {
      result.current.setQuery('60051');
    });
    act(() => {
      result.current.setQuerySilently('600519');
    });
    act(() => {
      vi.advanceTimersByTime(50);
    });

    expect(searchStocksMock).not.toHaveBeenCalled();
    expect(result.current.isOpen).toBe(false);
  });
});
