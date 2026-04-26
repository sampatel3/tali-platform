import React, { useEffect, useMemo, useState } from 'react';
import { LogOut, MoreHorizontal, X } from 'lucide-react';

import { useAuth } from '../../context/AuthContext';
import { organizations as organizationsApi } from '../../shared/api';
import { GlobalThemeToggle } from '../../shared/ui/GlobalThemeToggle';
import { TaaliLines } from '../../shared/ui/Branding';
import { formatHeaderOrgLabel, normalizeHeaderOrgName } from '../../shared/layout/headerIdentity';

const pickUserName = (user) => {
  const directName = (user?.full_name || user?.name || '').trim();
  if (directName) return directName;
  const emailLocalPart = (user?.email || '').split('@')[0]?.trim();
  return emailLocalPart || '';
};

const pickOrganizationName = (user) => String(
  user?.organization?.name
  || user?.organization_name
  || user?.company_name
  || ''
).trim();

const NAV_ITEMS = [
  { id: 'jobs', label: 'Jobs' },
  { id: 'candidates', label: 'Candidates' },
  { id: 'tasks', label: 'Tasks' },
  { id: 'reporting', label: 'Reporting' },
  { id: 'settings', label: 'Settings' },
];

export const DashboardNav = ({ currentPage, onNavigate }) => {
  const { user, logout } = useAuth();
  const userName = pickUserName(user);
  const fallbackOrgName = normalizeHeaderOrgName(pickOrganizationName(user), 'No company');
  const [resolvedOrgName, setResolvedOrgName] = useState(fallbackOrgName);
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setResolvedOrgName(fallbackOrgName);

    if (!user) return undefined;

    const loadOrganizationName = async () => {
      try {
        const response = await organizationsApi.get();
        const orgName = (response?.data?.name || '').trim();
        if (!cancelled) {
          setResolvedOrgName(normalizeHeaderOrgName(orgName || fallbackOrgName, 'No company'));
        }
      } catch {
        if (!cancelled) setResolvedOrgName(normalizeHeaderOrgName(fallbackOrgName, 'No company'));
      }
    };
    void loadOrganizationName();
    return () => {
      cancelled = true;
    };
  }, [fallbackOrgName, user?.id, user?.organization_id]);

  const orgName = normalizeHeaderOrgName(resolvedOrgName || fallbackOrgName, 'No company');
  const orgLabel = formatHeaderOrgLabel(orgName, 'No company');
  const navItems = NAV_ITEMS;
  const homePage = 'jobs';
  const displayName = userName || 'User';
  const initials = useMemo(() => {
    const seed = `${displayName} ${orgName}`.trim();
    const letters = seed.split(/\s+/).filter(Boolean).map((word) => word[0]).join('');
    return letters.slice(0, 2).toUpperCase() || 'U';
  }, [displayName, orgName]);
  // Map the legacy "assessments" current-page identifier to the canonical
  // "candidates" tab so deep links from outside the app land on a real nav item.
  const resolvedCurrentPage = currentPage === 'assessments' ? 'candidates' : currentPage;
  const activeNavItem = navItems.find((item) => item.id === resolvedCurrentPage) || navItems[0];

  const handleLogout = () => {
    logout();
    onNavigate('landing');
  };

  const handleNav = (id) => {
    onNavigate(id);
    setMobileOpen(false);
  };

  return (
    <nav className="app-nav">
      <div className="app-nav-inner">
        <button type="button" className="logo" onClick={() => handleNav(homePage)} aria-label="Go to home">
          <span className="logo-mark">
            <TaaliLines className="h-5 w-5" lineClassName="text-white" />
          </span>
          <span className="logo-word">
            taali<em>.</em>
          </span>
        </button>

        <div className="app-tabs dashboard-nav-tabs desktop-only">
          {navItems.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`app-tab ${resolvedCurrentPage === item.id ? 'active' : ''}`}
              onClick={() => handleNav(item.id)}
            >
              {item.label}
            </button>
          ))}
        </div>

        <div className="app-user">
          <div className="name desktop-only">
            <div className="n" title={displayName}>{displayName}</div>
            <div className="sub" title={orgName}>{orgLabel}</div>
          </div>
          <div className="app-avatar desktop-only">{initials}</div>
          <div className="mobile-page-pill mobile-only">{activeNavItem?.label || 'Menu'}</div>
          <button
            type="button"
            className="icon-btn mobile-only nav-more-btn"
            aria-label={mobileOpen ? 'Close navigation menu' : 'Open navigation menu'}
            aria-expanded={mobileOpen}
            aria-haspopup="menu"
            onClick={() => setMobileOpen((open) => !open)}
          >
            {mobileOpen ? <X size={15} /> : <MoreHorizontal size={17} />}
          </button>
          <button type="button" className="icon-btn desktop-only" onClick={handleLogout} title="Sign out">
            <LogOut size={15} />
          </button>
          <GlobalThemeToggle className="dashboard-theme-toggle desktop-theme-toggle" appearance="single" />
        </div>
      </div>

      {mobileOpen ? (
        <div className="dashboard-nav-mobile" role="menu">
          <div className="dashboard-nav-mobile-user">
            <div>
              <div className="n" title={displayName}>{displayName}</div>
              <div className="sub" title={orgName}>{orgLabel}</div>
            </div>
            <div className="app-avatar">{initials}</div>
          </div>
          <div className="dashboard-nav-mobile-links">
            {navItems.map((item) => (
              <button
                key={item.id}
                type="button"
                className={`dashboard-nav-mobile-link ${resolvedCurrentPage === item.id ? 'active' : ''}`}
                onClick={() => handleNav(item.id)}
                role="menuitem"
              >
                {item.label}
              </button>
            ))}
          </div>
          <div className="dashboard-nav-mobile-actions">
            <button type="button" className="dashboard-nav-mobile-signout" onClick={handleLogout} role="menuitem">
              <LogOut size={15} />
              Sign out
            </button>
            <GlobalThemeToggle className="dashboard-theme-toggle mobile-theme-toggle" appearance="single" />
          </div>
        </div>
      ) : null}
    </nav>
  );
};
