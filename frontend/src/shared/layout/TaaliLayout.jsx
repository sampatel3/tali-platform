import React, { useEffect, useMemo, useState } from 'react';
import { LogOut, Moon, Sun } from 'lucide-react';

import { useAuth } from '../../context/AuthContext';
import { organizations as organizationsApi } from '../../shared/api';
import {
  readDarkModePreference,
  setDarkModePreference,
  subscribeThemePreference,
} from '../../lib/themePreference';
import { navigateToMarketingSection } from '../../lib/marketingScroll';
import { formatHeaderOrgLabel } from './headerIdentity';

const APP_TABS = [
  { id: 'jobs', label: 'Jobs' },
  { id: 'candidates', label: 'Candidates' },
  { id: 'tasks', label: 'Tasks' },
  { id: 'reporting', label: 'Reporting' },
  { id: 'settings', label: 'Settings' },
];

const MARKETING_TABS = [
  { id: 'problem', label: 'The problem' },
  { id: 'platform', label: 'Platform' },
  { id: 'how-it-works', label: 'How it works' },
  { id: 'proof', label: 'Why Taali' },
];

const initialsFor = (...values) => {
  const seed = values
    .filter(Boolean)
    .join(' ')
    .trim()
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0])
    .join('');
  return seed.toUpperCase() || 'TA';
};

const resolveUserName = (user) => {
  const direct = String(user?.full_name || user?.name || '').trim();
  if (direct) return direct;
  const localPart = String(user?.email || '').split('@')[0]?.trim();
  return localPart || 'Taali user';
};

const resolveOrgName = (user) => String(
  user?.organization?.name
  || user?.organization_name
  || user?.company_name
  || ''
).trim();

const Mark = () => (
  <span className="logo-mark" aria-hidden="true">
    <svg viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round">
      <line x1="6" y1="4.5" x2="6" y2="19.5" />
      <line x1="10" y1="4.5" x2="10" y2="19.5" />
      <line x1="14" y1="4.5" x2="14" y2="19.5" />
      <line x1="18" y1="4.5" x2="18" y2="19.5" />
      <line x1="4" y1="18.5" x2="20" y2="5.5" />
    </svg>
  </span>
);

export const TaaliLogo = ({ onClick, wordmarkClassName = '' }) => (
  <button type="button" className="logo" onClick={onClick} aria-label="Taali home">
    <Mark />
    <span className={`logo-word ${wordmarkClassName}`.trim()}>
      taali<em>.</em>
    </span>
  </button>
);

export const ThemeToggleButton = ({ title = 'Toggle theme' }) => {
  const [darkMode, setDarkMode] = useState(() => readDarkModePreference());

  useEffect(() => subscribeThemePreference((next) => setDarkMode(Boolean(next))), []);

  return (
    <button
      type="button"
      className="icon-btn"
      title={title}
      aria-label={title}
      onClick={() => setDarkModePreference(!darkMode)}
    >
      {darkMode ? <Sun size={15} strokeWidth={1.7} /> : <Moon size={15} strokeWidth={1.7} />}
    </button>
  );
};

const AppUser = ({ onNavigate }) => {
  const { user, logout } = useAuth();
  const fallbackOrgName = resolveOrgName(user);
  const [orgName, setOrgName] = useState(() => fallbackOrgName);
  const displayName = resolveUserName(user);
  const displayOrgName = orgName || fallbackOrgName || 'Taali';
  const orgLabel = formatHeaderOrgLabel(displayOrgName, 'Taali');
  const initials = useMemo(() => initialsFor(displayName, displayOrgName), [displayName, displayOrgName]);

  useEffect(() => {
    let cancelled = false;
    setOrgName(fallbackOrgName);

    if (!user) return undefined;

    const loadOrg = async () => {
      try {
        const res = await organizationsApi.get();
        if (!cancelled) {
          const resolved = String(res?.data?.name || '').trim();
          setOrgName(resolved || fallbackOrgName || 'Taali');
        }
      } catch {
        if (!cancelled) setOrgName(fallbackOrgName || 'Taali');
      }
    };

    void loadOrg();
    return () => {
      cancelled = true;
    };
  }, [fallbackOrgName, user?.id, user?.organization_id]);

  return (
    <div className="app-user">
      <div className="name hidden sm:block">
        <div className="n" title={displayName}>{displayName}</div>
        <div className="sub" title={displayOrgName}>{orgLabel}</div>
      </div>
      <div className="app-avatar" aria-hidden="true">{initials}</div>
      <button
        type="button"
        className="icon-btn"
        title="Sign out"
        aria-label="Sign out"
        onClick={() => {
          logout();
          onNavigate('landing');
        }}
      >
        <LogOut size={15} strokeWidth={1.7} />
      </button>
      <ThemeToggleButton />
    </div>
  );
};

export const AppNav = ({ currentPage, onNavigate }) => (
  <div className="app-nav">
    <div className="app-nav-inner">
      <TaaliLogo onClick={() => onNavigate('landing')} />
      <div className="app-tabs hidden md:inline-flex">
        {APP_TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            className={`app-tab ${currentPage === tab.id ? 'active' : ''}`.trim()}
            onClick={() => onNavigate(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <AppUser onNavigate={onNavigate} />
    </div>
  </div>
);

export const AppShell = ({ currentPage, onNavigate, children }) => (
  <div>
    <AppNav currentPage={currentPage} onNavigate={onNavigate} />
    {children}
  </div>
);

export const MarketingNav = ({ onNavigate }) => (
  <div className="app-nav">
    <div className="app-nav-inner">
      <TaaliLogo onClick={() => onNavigate('landing')} />
      <div className="row hidden md:flex" style={{ gap: 32 }}>
        {MARKETING_TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => navigateToMarketingSection(tab.id, onNavigate)}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <div className="row" style={{ gap: 10 }}>
        <button type="button" className="btn btn-ghost btn-sm hidden sm:inline-flex" onClick={() => onNavigate('login')}>
          Sign in
        </button>
        <button type="button" className="btn btn-primary btn-sm" onClick={() => onNavigate('demo')}>
          Book a demo <span className="arrow">→</span>
        </button>
        <ThemeToggleButton />
      </div>
    </div>
  </div>
);

export const CandidateMiniNav = ({ label = 'Candidate assessment · secure session', onHomeClick = null }) => (
  <div className="app-nav">
    <div className="app-nav-inner">
      <TaaliLogo onClick={onHomeClick || (() => {})} />
      <div className="hidden md:block mono-label">{label}</div>
      <ThemeToggleButton />
    </div>
  </div>
);
