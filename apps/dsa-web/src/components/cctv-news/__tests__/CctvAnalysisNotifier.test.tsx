import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { CctvAnalysisNotifier } from '../CctvAnalysisNotifier';
import { useCctvAnalysisStore } from '../../../stores/cctvAnalysisStore';

vi.mock('../../../api/cctvNews', () => ({
  cctvNewsApi: { submitAnalysisJob: vi.fn(), getAnalysisJob: vi.fn() },
}));

beforeEach(() => {
  useCctvAnalysisStore.setState({ job: null, result: null, submitError: null, notice: null });
});

describe('CctvAnalysisNotifier', () => {
  const LocationProbe = () => {
    const location = useLocation();
    return <div data-testid="location">{`${location.pathname}${location.search}`}</div>;
  };

  it('renders nothing without a notice', () => {
    const { container } = render(
      <MemoryRouter>
        <CctvAnalysisNotifier />
      </MemoryRouter>,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('shows the success notice from any route', async () => {
    useCctvAnalysisStore.setState({
      notice: { id: 1, tone: 'success', title: 'CCTV 新闻分析已完成', message: '已分析 20 篇新闻，点击查看结果。' },
    });

    // 挂在别的路由下也要能弹出，这是"无论用户在哪都能收到通知"的关键
    const { container } = render(
      <MemoryRouter initialEntries={['/portfolio']}>
        <CctvAnalysisNotifier />
        <LocationProbe />
      </MemoryRouter>,
    );

    expect(screen.getByText('CCTV 新闻分析已完成')).toBeInTheDocument();
    expect(screen.getByText('已分析 20 篇新闻，点击查看结果。')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '查看结果' })).toBeInTheDocument();
    expect(container.querySelector('.fixed')).toHaveClass('top-6', 'right-6');
    expect(container.querySelector('.fixed')).not.toHaveClass('bottom-6');

    screen.getByRole('button', { name: '查看结果' }).click();
    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent('/news-analysis?tab=cctv&ctab=analysis');
    });
  });

  it('hides the jump link for failures', () => {
    useCctvAnalysisStore.setState({
      notice: { id: 2, tone: 'error', title: 'CCTV 新闻分析失败', message: '模型调用失败' },
    });

    render(
      <MemoryRouter>
        <CctvAnalysisNotifier />
      </MemoryRouter>,
    );

    expect(screen.getByText('CCTV 新闻分析失败')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '查看结果' })).not.toBeInTheDocument();
  });

  it('dismisses on close', () => {
    useCctvAnalysisStore.setState({
      notice: { id: 3, tone: 'warning', title: '降级完成', message: '已改用关键词覆盖' },
    });

    render(
      <MemoryRouter>
        <CctvAnalysisNotifier />
      </MemoryRouter>,
    );

    screen.getByRole('button', { name: '关闭' }).click();
    expect(useCctvAnalysisStore.getState().notice).toBeNull();
  });
});
