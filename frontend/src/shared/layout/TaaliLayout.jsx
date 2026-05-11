import React, { useEffect, useMemo, useState } from 'react';
import { LogOut, Menu, Moon, Sun, X } from 'lucide-react';

import { useAuth } from '../../context/AuthContext';
import { organizations as organizationsApi } from '../../shared/api';
import {
  readDarkModePreference,
  setDarkModePreference,
  subscribeThemePreference,
} from '../../lib/themePreference';
import { navigateToMarketingSection } from '../../lib/marketingScroll';
import { TaaliTile } from '../ui/Branding';
import { formatHeaderOrgLabel, normalizeHeaderOrgName } from './headerIdentity';

// The standalone Candidates directory was deprecated 2026-05-09 — the
// triage drawer now lives on the role detail page, so there is no
// separate "all candidates" tab any more. Drill-downs from a role still
// link to the per-candidate standing report.
const APP_TABS = [
  { id: 'jobs', label: 'Jobs' },
  { id: 'tasks', label: 'Tasks' },
  { id: 'reporting', label: 'Reporting' },
  { id: 'settings', label: 'Settings' },
];

const MARKETING_TABS = [
  { id: 'platform', label: 'Product' },
  { id: 'how-it-works', label: 'How it works' },
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

// Use the canonical brand glyph (`TaaliTile`) so every header logo across
// marketing, auth, runtime, and the recruiter app renders from the same
// SVG path. The legacy `Mark` component used inline lines with hard-coded
// `stroke="white"` — kept here historically so existing `.logo-mark` CSS
// could supply the purple backdrop. Now we let `TaaliTile` render the
// backdrop too so there's exactly one source of truth.
export const TaaliLogo = ({ onClick, wordmarkClassName = '' }) => (
  <button type="button" className="logo" onClick={onClick} aria-label="Taali home">
    <TaaliTile
      className="h-7 w-7 rounded-[7px]"
      fillClassName="text-[var(--purple)]"
      lineClassName="text-white"
      strokeWidth={2.4}
      cornerRadius={5.4}
    />
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
  const fallbackOrgName = normalizeHeaderOrgName(resolveOrgName(user), 'No company');
  const [orgName, setOrgName] = useState(() => fallbackOrgName);
  const displayName = resolveUserName(user);
  const displayOrgName = normalizeHeaderOrgName(orgName || fallbackOrgName, 'No company');
  const orgLabel = formatHeaderOrgLabel(displayOrgName, 'No company');
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
          setOrgName(normalizeHeaderOrgName(resolved || fallbackOrgName, 'No company'));
        }
      } catch {
        if (!cancelled) setOrgName(normalizeHeaderOrgName(fallbackOrgName, 'No company'));
      }
    };

    void loadOrg();
    return () => {
      cancelled = true;
    };
  }, [fallbackOrgName, user?.id, user?.organization_id]);

  return (
    <div className="app-user">
      <div className="name !hidden sm:!block">
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
      <div className="app-tabs !hidden md:!inline-flex">
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

export const MarketingNav = ({ onNavigate }) => {
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  useEffect(() => {
    if (!mobileMenuOpen) return undefined;
    const onKey = (event) => {
      if (event.key === 'Escape') setMobileMenuOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [mobileMenuOpen]);

  const closeMenu = () => setMobileMenuOpen(false);

  return (
    <div className="app-nav">
      <div className="app-nav-inner">
        <TaaliLogo onClick={() => { closeMenu(); onNavigate('landing'); }} />
        <div className="row !hidden md:!flex" style={{ gap: 22 }}>
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
          <button type="button" className="btn btn-ghost btn-sm !hidden sm:!inline-flex" onClick={() => onNavigate('login')}>
            Sign in
          </button>
          <button type="button" className="btn btn-primary btn-sm" onClick={() => { closeMenu(); onNavigate('demo-lead'); }}>
            Book a demo <span className="arrow">→</span>
          </button>
          <div className="!hidden md:!block">
            <ThemeToggleButton />
          </div>
          <button
            type="button"
            className="icon-btn md:!hidden"
            aria-label={mobileMenuOpen ? 'Close menu' : 'Open menu'}
            aria-expanded={mobileMenuOpen}
            aria-controls="marketing-mobile-menu"
            onClick={() => setMobileMenuOpen((open) => !open)}
          >
            {mobileMenuOpen ? <X size={16} strokeWidth={1.8} /> : <Menu size={16} strokeWidth={1.8} />}
          </button>
        </div>
      </div>

      {mobileMenuOpen ? (
        <div
          id="marketing-mobile-menu"
          className="md:!hidden border-t border-[var(--line)] bg-[var(--bg)] px-5 pb-5 pt-3"
        >
          <div className="flex flex-col">
            {MARKETING_TABS.map((tab) => (
              <button
                key={tab.id}
                type="button"
                className="w-full rounded-[10px] px-3 py-3 text-left text-[15px] font-medium text-[var(--ink)] transition hover:bg-[var(--bg-3)]"
                onClick={() => {
                  closeMenu();
                  navigateToMarketingSection(tab.id, onNavigate);
                }}
              >
                {tab.label}
              </button>
            ))}
          </div>
          <div className="mt-3 flex items-center gap-3 border-t border-[var(--line)] pt-4">
            <button
              type="button"
              className="btn btn-outline btn-sm flex-1 justify-center"
              onClick={() => { closeMenu(); onNavigate('login'); }}
            >
              Sign in
            </button>
            <ThemeToggleButton />
          </div>
        </div>
      ) : null}
    </div>
  );
};

export const CandidateMiniNav = ({ label = 'Candidate assessment · secure session', onHomeClick = null }) => (
  <div className="app-nav">
    <div className="app-nav-inner">
      <TaaliLogo onClick={onHomeClick || (() => {})} />
      <div className="!hidden md:!block mono-label">{label}</div>
      <ThemeToggleButton />
    </div>
  </div>
);
