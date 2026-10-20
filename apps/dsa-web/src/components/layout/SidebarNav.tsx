import React, { useEffect, useState } from 'react';
import { Activity, BarChart3, Bell, BriefcaseBusiness, CandlestickChart, Crown, Gauge, Home, Landmark, LogOut, MessageSquareQuote, Radio, Search, Settings2 } from 'lucide-react';
import { NavLink } from 'react-router-dom';
import { dabanApi } from '../../api/daban';
import { industryFundsApi } from '../../api/industryFunds';
import { SCREENING_CONFIG_CHANGED_EVENT, SYSTEM_CONFIG_CHANGED_EVENT, screeningApi } from '../../api/screening';
import { useAuth } from '../../contexts/AuthContext';
import { useAgentChatStore } from '../../stores/agentChatStore';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import type { UiTextKey } from '../../i18n/uiText';
import { cn } from '../../utils/cn';
import { ConfirmDialog } from '../common/ConfirmDialog';
import { StatusDot } from '../common/StatusDot';
import { UiLanguageToggle } from '../i18n/UiLanguageToggle';
import { ThemeToggle } from '../theme/ThemeToggle';

type SidebarNavProps = {
  collapsed?: boolean;
  onNavigate?: () => void;
  variant?: 'default' | 'rail';
};

type NavItem = {
  key: string;
  labelKey: UiTextKey;
  to: string;
  icon: React.ComponentType<{ className?: string }>;
  exact?: boolean;
  badge?: 'completion';
};

const NAV_ITEMS: NavItem[] = [
  { key: 'home', labelKey: 'layout.nav.home', to: '/', icon: Home, exact: true },
  { key: 'stock-query', labelKey: 'layout.nav.stockQuery', to: '/stock-query', icon: CandlestickChart },
  { key: 'chat', labelKey: 'layout.nav.chat', to: '/chat', icon: MessageSquareQuote, badge: 'completion' },
  { key: 'news-analysis', labelKey: 'layout.nav.newsAnalysis', to: '/news-analysis', icon: Radio },
  { key: 'screening', labelKey: 'layout.nav.screening', to: '/screening', icon: Search },
  { key: 'industry-funds', labelKey: 'layout.nav.industryFunds', to: '/industry-funds', icon: Landmark },
  { key: 'daban-king', labelKey: 'layout.nav.dabanKing', to: '/daban-king', icon: Crown },
  { key: 'portfolio', labelKey: 'layout.nav.portfolio', to: '/portfolio', icon: BriefcaseBusiness },
  { key: 'decision-signals', labelKey: 'layout.nav.decisionSignals', to: '/decision-signals', icon: Activity },
  { key: 'backtest', labelKey: 'layout.nav.backtest', to: '/backtest', icon: BarChart3 },
  { key: 'alerts', labelKey: 'layout.nav.alerts', to: '/alerts', icon: Bell },
  { key: 'usage', labelKey: 'layout.nav.usage', to: '/usage', icon: Gauge },
  { key: 'settings', labelKey: 'layout.nav.settings', to: '/settings', icon: Settings2 },
];

export const SidebarNav: React.FC<SidebarNavProps> = ({ collapsed = false, onNavigate, variant = 'default' }) => {
  const { authEnabled, logout } = useAuth();
  const { t } = useUiLanguage();
  const completionBadge = useAgentChatStore((state) => state.completionBadge);
  const [showLogoutConfirm, setShowLogoutConfirm] = useState(false);
  const [showScreeningNav, setShowScreeningNav] = useState(false);
  // 行业资金默认开启，所以这里的初值是 true 而不是 false：状态接口偶发失败时
  // 菜单不该凭空消失（选股那个是默认关闭的功能，语义相反）。
  const [showIndustryFundsNav, setShowIndustryFundsNav] = useState(true);
  // 打板王同样默认开启，语义与行业资金一致。
  const [showDabanNav, setShowDabanNav] = useState(true);

  useEffect(() => {
    let active = true;

    const refreshScreeningStatus = async () => {
      try {
        const status = await screeningApi.getStatus();
        if (active) {
          setShowScreeningNav(status.enabled);
        }
      } catch {
        if (active) {
          setShowScreeningNav(false);
        }
      }
    };

    const refreshIndustryFundsStatus = async () => {
      try {
        const status = await industryFundsApi.getStatus();
        if (active) {
          setShowIndustryFundsNav(status.enabled);
        }
      } catch {
        // 保持上一次的状态，不因为一次请求失败就把入口藏掉。
      }
    };

    const refreshDabanStatus = async () => {
      try {
        const status = await dabanApi.getStatus();
        if (active) {
          setShowDabanNav(status.enabled);
        }
      } catch {
        // 同行业资金：一次请求失败不藏入口。
      }
    };

    void refreshScreeningStatus();
    void refreshIndustryFundsStatus();
    void refreshDabanStatus();
    window.addEventListener(SCREENING_CONFIG_CHANGED_EVENT, refreshScreeningStatus);
    window.addEventListener(SYSTEM_CONFIG_CHANGED_EVENT, refreshScreeningStatus);
    window.addEventListener(SYSTEM_CONFIG_CHANGED_EVENT, refreshIndustryFundsStatus);
    window.addEventListener(SYSTEM_CONFIG_CHANGED_EVENT, refreshDabanStatus);

    return () => {
      active = false;
      window.removeEventListener(SCREENING_CONFIG_CHANGED_EVENT, refreshScreeningStatus);
      window.removeEventListener(SYSTEM_CONFIG_CHANGED_EVENT, refreshScreeningStatus);
      window.removeEventListener(SYSTEM_CONFIG_CHANGED_EVENT, refreshIndustryFundsStatus);
      window.removeEventListener(SYSTEM_CONFIG_CHANGED_EVENT, refreshDabanStatus);
    };
  }, []);

  const navItems = NAV_ITEMS.filter((item) => {
    if (item.key === 'screening') return showScreeningNav;
    if (item.key === 'industry-funds') return showIndustryFundsNav;
    if (item.key === 'daban-king') return showDabanNav;
    return true;
  });
  const isRail = variant === 'rail';
  const itemBaseClass = cn(
    'group relative flex h-[var(--nav-item-height)] w-full items-center overflow-hidden rounded-2xl border border-transparent text-sm leading-none text-secondary-text transition-all',
    isRail
      ? 'justify-center gap-2.5 px-2'
      : collapsed
        ? 'justify-center px-0'
        : 'gap-3 px-[var(--nav-item-padding-x)]'
  );
  const itemInteractiveClass = cn(
    itemBaseClass,
    'hover:bg-[var(--nav-hover-bg)] hover:text-foreground'
  );
  const itemActiveClass = 'border-[var(--nav-active-border)] bg-[var(--nav-active-bg)] font-medium text-[hsl(var(--primary))]';
  const itemIconClass = cn(isRail ? 'h-[18px] w-[18px]' : 'h-5 w-5', 'shrink-0');
  const itemLabelClass = cn('truncate', isRail ? 'text-center' : '');

  return (
    <div className="flex h-full flex-col">
      <div
        className={cn(
          'flex items-center',
          isRail ? 'mb-5 justify-center gap-2 pt-1' : 'mb-4 gap-2 px-1',
          collapsed || isRail ? 'justify-center' : ''
        )}
      >
        <div
          className={cn(
            'flex items-center justify-center bg-primary-gradient text-[hsl(var(--primary-foreground))] shadow-[0_12px_28px_var(--nav-brand-shadow)]',
            isRail ? 'h-9 w-9 rounded-[1rem]' : 'h-10 w-10 rounded-2xl'
          )}
        >
          <BarChart3 className={cn(isRail ? 'h-[19px] w-[19px]' : 'h-5 w-5')} />
        </div>
        {!collapsed ? (
          <p className={cn('min-w-0 truncate font-semibold text-foreground', isRail ? 'text-[0.95rem] leading-none' : 'text-sm')}>DSA</p>
        ) : null}
      </div>

      <nav className={cn('flex flex-col gap-1.5', isRail ? '' : 'flex-1')} aria-label={t('layout.mainNav')}>
        {navItems.map(({ key, labelKey, to, icon: Icon, exact, badge }) => {
          const label = t(labelKey);
          return (
          <NavLink
            key={key}
            to={to}
            end={exact}
            onClick={onNavigate}
            aria-label={label}
            className={({ isActive }) =>
              cn(
                itemInteractiveClass,
                isActive ? itemActiveClass : ''
              )
            }
          >
            {({ isActive }) => (
              <>
                <Icon className={cn(itemIconClass, isActive ? 'text-[var(--nav-icon-active)]' : 'text-current')} />
                {!collapsed ? <span className={itemLabelClass}>{label}</span> : null}
                {badge === 'completion' && completionBadge ? (
                  <StatusDot
                    tone="info"
                    data-testid="chat-completion-badge"
                    className={cn(
                      'absolute right-3 border-2 border-background shadow-[0_0_10px_var(--nav-indicator-shadow)]',
                      collapsed ? 'right-2 top-2' : ''
                    )}
                    aria-label={t('layout.newChatMessage')}
                  />
                ) : null}
              </>
            )}
          </NavLink>
        );
        })}

        <ThemeToggle
          variant={isRail ? 'rail' : 'nav'}
          collapsed={collapsed}
          wrapperClassName="w-full"
          triggerClassName={itemInteractiveClass}
          triggerActiveClassName={itemActiveClass}
          iconClassName={itemIconClass}
          labelClassName={itemLabelClass}
        />
        <UiLanguageToggle
          variant={isRail ? 'rail' : 'nav'}
          collapsed={collapsed}
          wrapperClassName="w-full"
          triggerClassName={itemInteractiveClass}
          triggerActiveClassName={itemActiveClass}
          iconClassName={itemIconClass}
          labelClassName={itemLabelClass}
        />
      </nav>

      {authEnabled ? (
        <button
          type="button"
          onClick={() => setShowLogoutConfirm(true)}
          className={cn(
            itemInteractiveClass,
            isRail ? 'mt-1.5' : 'mt-5'
          )}
        >
          <LogOut className={itemIconClass} />
          {!collapsed ? <span className={itemLabelClass}>{t('layout.logout')}</span> : null}
        </button>
      ) : null}

      <ConfirmDialog
        isOpen={showLogoutConfirm}
        title={t('layout.logoutTitle')}
        message={t('layout.logoutMessage')}
        confirmText={t('layout.logoutConfirm')}
        cancelText={t('common.cancel')}
        isDanger
        onConfirm={() => {
          setShowLogoutConfirm(false);
          onNavigate?.();
          void logout();
        }}
        onCancel={() => setShowLogoutConfirm(false)}
      />
    </div>
  );
};
