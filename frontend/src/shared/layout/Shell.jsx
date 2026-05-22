import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Bell,
  Briefcase,
  CheckSquare,
  ChevronDown,
  Home,
  LogOut,
  MessageSquare,
  Moon,
  Settings as SettingsIcon,
  Sun,
} from 'lucide-react';

import { useAuth } from '../../context/AuthContext';
import { agent as agentApi, organizations as organizationsApi } from '../api';
import {
  readDarkModePreference,
  setDarkModePreference,
  subscribeThemePreference,
} from '../../lib/themePreference';
import { TaaliTile } from '../ui/Branding';
import { PageLink } from '../ui/PageLink';
import { useAgentStatusOrg } from './AgentBar';
import { GlobalSearch } from './GlobalSearch';
import { formatHeaderOrgLabel, normalizeHeaderOrgName } from './headerIdentity';

// Home is the agent-first landing — see docs/HOME_HUB_DESIGN.md. It
// absorbs the old Reporting tab and surfaces the agent's pending review
// queue. The pending badge is reactive (polled below).
const NAV_TABS = [
  { id: 'home',     label: 'Home',     Icon: Home },
  { id: 'jobs',     label: 'Jobs',     Icon: Briefcase },
  { id: 'chat',     label: 'Search',   Icon: MessageSquare, badge: 'AI' },
  { id: 'tasks',    label: 'Tasks',    Icon: CheckSquare },
  { id: 'settings', label: 'Settings', Icon: SettingsIcon },
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

// Poll the org-wide pending count for the Home tab badge. 30s cadence is
// the same as AgentBar — cheap aggregation, fine for a top-of-page nav.
const useHomePendingCount = (isAuthenticated) => {
  const [count, setCount] = useState(0);
  useEffect(() => {
    if (!isAuthenticated) {
      setCount(0);
      return undefined;
    }
    let cancelled = false;
    const tick = async () => {
      try {
        const res = await agentApi.orgStatus();
        if (cancelled) return;
        setCount(Number(res?.data?.pending || 0));
      } catch {
        // Silent — a transient 401/5xx shouldn't make the nav badge flicker.
      }
    };
    void tick();
    const id = window.setInterval(tick, 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [isAuthenticated]);
  return count;
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
  const homePending = useHomePendingCount(Boolean(user));

  // Map legacy page identifiers onto canonical tabs.
  // 'reporting' / 'analytics' fold into 'home' (the Hub) — keep the icon
  // highlighted while users on those legacy paths still hit the redirect.
  const resolvedPage = (currentPage === 'assessments')
    ? 'candidates'
    : (currentPage === 'reporting' || currentPage === 'analytics')
      ? 'home'
      : currentPage;
  const handleLogout = () => {
    setMenuOpen(false);
    logout?.();
    onNavigate?.('landing');
  };

  // HANDOFF unified-headers.md §6 — the global org-scoped AgentBar is gone.
  // Agent state now lives inside the per-page AgentHeader's right-side panel
  // (Jobs / Role detail). The lightweight "Agent running" chip in the nav is
  // also scoped to those two surfaces — anywhere else, the chip is hidden so
  // the nav doesn't double-signal what the page hero already shows.
  const showAgentChip = resolvedPage === 'jobs' || resolvedPage === 'role-pipeline' || resolvedPage === 'role-detail';
  const { status: orgAgentStatus } = useAgentStatusOrg();
  const agentChipOn = Boolean(
    orgAgentStatus
      && orgAgentStatus.active_role_count > 0
      && !orgAgentStatus.paused,
  );

  return (
    <>
    <header className="mc-nav" role="banner">
      <PageLink
        page="jobs"
        className="mc-nav-logo"
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
      </PageLink>
      <nav className="mc-nav-tabs" aria-label="Primary">
        {NAV_TABS.map(({ id, label, Icon: TabIcon, badge }) => {
          // Live pending-count badge on Home — overrides the static badge.
          const liveBadge = (id === 'home' && homePending > 0) ? String(homePending) : null;
          const visibleBadge = liveBadge ?? badge;
          return (
            <PageLink
              key={id}
              page={id}
              className={`mc-nav-tab ${resolvedPage === id ? 'on' : ''}`.trim()}
              aria-current={resolvedPage === id ? 'page' : undefined}
            >
              <TabIcon size={15} strokeWidth={1.8} aria-hidden="true" />
              <span>{label}</span>
              {visibleBadge
                ? (
                  <span
                    className="mc-badge"
                    aria-label={liveBadge ? `${liveBadge} pending` : undefined}
                    aria-hidden={liveBadge ? undefined : true}
                  >
                    {visibleBadge}
                  </span>
                )
                : null}
            </PageLink>
          );
        })}
      </nav>
      <div className="mc-nav-grow" />
      <div className="mc-nav-right">
        {showAgentChip && agentChipOn ? (
          <PageLink
            page="jobs"
            className="mc-nav-agent-chip"
            title="Agent mode is ON · click to manage on Jobs"
            aria-label="Agent mode is on"
          >
            <span className="dot" aria-hidden="true" />
            Agent running
          </PageLink>
        ) : null}
        <GlobalSearch onNavigate={onNavigate} />
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
    </>
  );
};

export default Shell;
