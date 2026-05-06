import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Bell,
  Briefcase,
  CheckSquare,
  ChevronDown,
  LineChart,
  LogOut,
  MessageSquare,
  Moon,
  Search,
  Settings as SettingsIcon,
  Sun,
} from 'lucide-react';

import { useAuth } from '../../context/AuthContext';
import { organizations as organizationsApi } from '../api';
import {
  readDarkModePreference,
  setDarkModePreference,
  subscribeThemePreference,
} from '../../lib/themePreference';
import { TaaliTile } from '../ui/Branding';
import { AgentBar } from './AgentBar';
import { formatHeaderOrgLabel, normalizeHeaderOrgName } from './headerIdentity';

const NAV_TABS = [
  { id: 'jobs',      label: 'Jobs',      Icon: Briefcase },
  { id: 'chat',      label: 'Chat',      Icon: MessageSquare, badge: 'AI' },
  { id: 'tasks',     label: 'Tasks',     Icon: CheckSquare },
  { id: 'reporting', label: 'Reporting', Icon: LineChart },
  { id: 'settings',  label: 'Settings',  Icon: SettingsIcon },
];

const pickUserName = (user) => {
  const direct = String(user?.full_name || user?.name || '').trim();
  if (direct) return direct;
  const local = String(user?.email || '').split('@')[0]?.trim();
  return local || '';
};

const pickOrganizationName = (user) =>
  String(user?.organization?.name || user?.organization_name || user?.company_name || '').trim();

const initialsFor = (name, org) => {
  const seed = `${name} ${org}`.trim();
  const letters = seed.split(/\s+/).filter(Boolean).map((w) => w[0]).join('');
  return letters.slice(0, 2).toUpperCase() || 'TA';
};

const useOrgName = (user) => {
  const fallback = useMemo(
    () => normalizeHeaderOrgName(pickOrganizationName(user), 'No company'),
    [user],
  );
  const [orgName, setOrgName] = useState(fallback);
  useEffect(() => {
    let cancelled = false;
    setOrgName(fallback);
    if (!user) return undefined;
    const load = async () => {
      try {
        const res = await organizationsApi.get();
        if (cancelled) return;
        const resolved = String(res?.data?.name || '').trim();
        setOrgName(normalizeHeaderOrgName(resolved || fallback, 'No company'));
      } catch {
        if (!cancelled) setOrgName(normalizeHeaderOrgName(fallback, 'No company'));
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [fallback, user?.id, user?.organization_id]);
  return orgName;
};

const ThemeMenuItem = () => {
  const [dark, setDark] = useState(() => readDarkModePreference());
  useEffect(() => subscribeThemePreference((next) => setDark(Boolean(next))), []);
  return (
    <button type="button" onClick={() => setDarkModePreference(!dark)}>
      {dark ? <Sun size={15} strokeWidth={1.7} /> : <Moon size={15} strokeWidth={1.7} />}
      {dark ? 'Light mode' : 'Dark mode'}
    </button>
  );
};

const AvatarMenu = ({ user, orgName, onClose, onLogout }) => {
  const ref = useRef(null);
  useEffect(() => {
    const onDocClick = (e) => {
      if (ref.current && !ref.current.contains(e.target)) onClose();
    };
    const onKey = (e) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [onClose]);

  const displayName = pickUserName(user) || 'User';
  const orgLabel = formatHeaderOrgLabel(orgName, 'No company');

  return (
    <div ref={ref} className="mc-avatar-menu" role="menu">
      <div className="mc-avatar-menu-header">
        <div className="name" title={displayName}>{displayName}</div>
        <div className="org" title={orgName}>{orgLabel}</div>
      </div>
      <ThemeMenuItem />
      <button type="button" onClick={onLogout}>
        <LogOut size={15} strokeWidth={1.7} />
        Sign out
      </button>
    </div>
  );
};

// Drop-in replacement for the legacy DashboardNav. Same prop signature
// (`currentPage`, `onNavigate`) so pages don't need to change.
// Renders the Mission Control top nav: logo + 5 tabs with icons + search
// pill (⌘K placeholder) + notifications bell + avatar menu (user/org,
// theme toggle, sign out).
export const Shell = ({ currentPage, onNavigate }) => {
  const { user, logout } = useAuth();
  const orgName = useOrgName(user);
  const displayName = pickUserName(user) || 'User';
  const initials = useMemo(() => initialsFor(displayName, orgName), [displayName, orgName]);
  const [menuOpen, setMenuOpen] = useState(false);

  // Map legacy page identifiers onto canonical tabs.
  const resolvedPage = currentPage === 'assessments' ? 'candidates' : currentPage;
  const handleNav = (id) => onNavigate?.(id);
  const handleLogout = () => {
    setMenuOpen(false);
    logout?.();
    onNavigate?.('landing');
  };

  // Hide the global AgentBar on the role detail page — that page renders
  // its own role-scoped AgentBar / cockpit rail and an extra org bar would
  // double-stack. Same logic for the role pipeline at /jobs/:roleId.
  const hideGlobalAgentBar = resolvedPage === 'role-detail' || resolvedPage === 'role-pipeline';

  return (
    <>
    <header className="mc-nav" role="banner">
      <button
        type="button"
        className="mc-nav-logo"
        onClick={() => handleNav('jobs')}
        aria-label="Taali home"
      >
        <TaaliTile
          className="h-7 w-7 rounded-[6px]"
          fillClassName="text-[var(--purple)]"
          lineClassName="text-white"
          strokeWidth={2.4}
          cornerRadius={5.4}
        />
        <span>taali<em>.</em></span>
      </button>
      <nav className="mc-nav-tabs" aria-label="Primary">
        {NAV_TABS.map(({ id, label, Icon: TabIcon, badge }) => (
          <button
            key={id}
            type="button"
            className={`mc-nav-tab ${resolvedPage === id ? 'on' : ''}`.trim()}
            onClick={() => handleNav(id)}
            aria-current={resolvedPage === id ? 'page' : undefined}
          >
            <TabIcon size={15} strokeWidth={1.8} aria-hidden="true" />
            <span>{label}</span>
            {badge ? <span className="mc-badge" aria-hidden="true">{badge}</span> : null}
          </button>
        ))}
      </nav>
      <div className="mc-nav-grow" />
      <div className="mc-nav-right">
        <button type="button" className="mc-nav-search" aria-label="Open command palette">
          <Search size={13} strokeWidth={2} />
          <span>Search candidates, roles, tasks…</span>
          <kbd>⌘K</kbd>
        </button>
        <button type="button" className="mc-icon-btn" aria-label="Notifications">
          <Bell size={15} strokeWidth={1.7} />
          <span className="mc-dot" aria-hidden="true" />
        </button>
        <div className="mc-nav-avatar-wrap">
          <button
            type="button"
            className="mc-avatar"
            onClick={() => setMenuOpen((open) => !open)}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            aria-label="Account menu"
          >
            {initials}
            <ChevronDown size={11} strokeWidth={1.8} style={{ marginLeft: 2 }} />
          </button>
          {menuOpen ? (
            <AvatarMenu
              user={user}
              orgName={orgName}
              onClose={() => setMenuOpen(false)}
              onLogout={handleLogout}
            />
          ) : null}
        </div>
      </div>
    </header>
    {hideGlobalAgentBar ? null : (
      <div className="mc-agent-bar-wrap">
        <AgentBar />
      </div>
    )}
    </>
  );
};

export default Shell;
