import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Briefcase,
  CheckSquare,
  ChevronDown,
  Home,
  LineChart,
  LogOut,
  Menu,
  MessageSquare,
  Moon,
  Settings as SettingsIcon,
  Sun,
  UserPlus,
  X,
} from 'lucide-react';

import { useAuth } from '../../context/AuthContext';
import { organizations as organizationsApi } from '../api/orgClient';
import {
  readDarkModePreference,
  setDarkModePreference,
  subscribeThemePreference,
} from '../../lib/themePreference';
import { isPreviewNavSurface } from '../../lib/previewNav';
import {
  AnimatePresence,
  AgentLoop,
  backdropVariants,
  createSheetVariants,
  m,
  popoverVariants,
} from '../motion';
import { TaaliTile } from '../ui/Branding';
import { PageLink } from '../ui/PageLink';
import { useAgentStatusOrg } from './AgentBar';
import { GlobalSearch } from './GlobalSearch';
import { formatHeaderOrgLabel, normalizeHeaderOrgName } from './headerIdentity';

// Home is the agent-first landing — see docs/HOME_HUB_DESIGN.md. It
// absorbs the old Reporting tab and surfaces the agent's pending review
// queue. The pending badge is reactive (polled below).
// Clients moved out of the top nav into Settings → Clients (managed there);
// the per-client view is reached via the Jobs page's client filter. See the
// requisition->job bridge work.
const NAV_TABS = [
  { id: 'home',     label: 'Home',     Icon: Home },
  { id: 'jobs',     label: 'Jobs',     Icon: Briefcase },
  { id: 'candidates', label: 'Candidates', Icon: UserPlus },
  { id: 'chat',     label: 'Chat',     Icon: MessageSquare, badge: 'AI' },
  { id: 'tasks',    label: 'Tasks',    Icon: CheckSquare },
  { id: 'analytics', label: 'Analytics', Icon: LineChart },
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
    <m.div
      ref={ref}
      className="mc-avatar-menu"
      role="menu"
      initial="hidden"
      animate="visible"
      exit="exit"
      variants={popoverVariants}
    >
      <div className="mc-avatar-menu-header">
        <div className="name" title={displayName}>{displayName}</div>
        <div className="org" title={orgName}>{orgLabel}</div>
      </div>
      <ThemeMenuItem />
      <button type="button" onClick={onLogout}>
        <LogOut size={15} strokeWidth={1.7} />
        Sign out
      </button>
    </m.div>
  );
};

// Phone nav. The desktop top bar (tabs + 380px search pill + bell + avatar)
// can't fit a ~375px screen, so below 720px those collapse and this slide-in
// drawer carries the 5 tabs, search, theme toggle, and sign out.
const MobileNavDrawer = ({
  open,
  onClose,
  initials,
  displayName,
  orgName,
  resolvedPage,
  homePending,
  onLogout,
  onNavigate,
}) => {
  const panelRef = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const onKey = (e) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    window.requestAnimationFrame(() => panelRef.current?.focus());
    return () => {
      document.body.style.overflow = prevOverflow;
      document.removeEventListener('keydown', onKey);
    };
  }, [open, onClose]);

  const orgLabel = formatHeaderOrgLabel(orgName, 'No company');
  return (
    <m.div
      className="mc-drawer-root"
      role="dialog"
      aria-modal="true"
      aria-label="Menu"
      initial="hidden"
      animate="visible"
      exit="exit"
    >
      <m.div className="mc-drawer-backdrop" onClick={onClose} variants={backdropVariants} />
      <m.div
        className="mc-drawer-panel"
        ref={panelRef}
        tabIndex={-1}
        variants={createSheetVariants('right')}
      >
        <div className="mc-drawer-head">
          <div className="mc-drawer-id">
            <span className="mc-drawer-avatar" aria-hidden="true">{initials}</span>
            <div className="mc-drawer-id-text">
              <div className="name" title={displayName}>{displayName}</div>
              <div className="org" title={orgName}>{orgLabel}</div>
            </div>
          </div>
          <button type="button" className="mc-icon-btn" onClick={onClose} aria-label="Close menu">
            <X size={18} strokeWidth={1.8} />
          </button>
        </div>
        <div className="mc-drawer-search">
          <GlobalSearch onNavigate={(page, opts) => { onClose(); onNavigate?.(page, opts); }} />
        </div>
        <nav className="mc-drawer-tabs" aria-label="Primary">
          {NAV_TABS.map(({ id, label, Icon: TabIcon, badge }) => {
            const liveBadge = (id === 'home' && homePending > 0) ? String(homePending) : null;
            const visibleBadge = liveBadge ?? badge;
            return (
              <PageLink
                key={id}
                page={id}
                className={`mc-drawer-tab ${resolvedPage === id ? 'on' : ''}`.trim()}
                aria-current={resolvedPage === id ? 'page' : undefined}
                onClick={onClose}
              >
                <TabIcon size={18} strokeWidth={1.8} aria-hidden="true" />
                <span>{label}</span>
                {visibleBadge ? <span className="mc-badge">{visibleBadge}</span> : null}
              </PageLink>
            );
          })}
        </nav>
        <div className="mc-drawer-divider" />
        <div className="mc-drawer-actions">
          <ThemeMenuItem />
          <button type="button" onClick={onLogout}>
            <LogOut size={16} strokeWidth={1.7} />
            Sign out
          </button>
        </div>
      </m.div>
    </m.div>
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
  const [drawerOpen, setDrawerOpen] = useState(false);
  const {
    status: orgAgentStatus,
    payload: orgAgentPayload,
  } = useAgentStatusOrg(Boolean(user));
  // The Home badge covers both recommendation decisions and agent questions,
  // matching the queue's canonical `pending` aggregate. AgentBar's decision
  // metric remains the narrower pending_decisions value.
  const homePending = Number(
    orgAgentPayload?.pending ?? orgAgentStatus?.pending_decisions ?? 0,
  );
  const navLocked = isPreviewNavSurface();

  // Map non-tab page identifiers onto the canonical nav tab that owns them.
  //   - 'analytics' IS a real tab now — keep it (do NOT fold into Home).
  //   - legacy 'reporting' → the Analytics tab (its route redirects there).
  //   - assessments inbox and requisitions live under the Jobs tab, so
  //     highlight Jobs on those surfaces. 'candidates' is its own top-level
  //     tab now (the Prospects surface) — no longer folded into Jobs.
  const JOBS_TAB_PAGES = new Set(['assessments', 'requisitions']);
  const resolvedPage = JOBS_TAB_PAGES.has(currentPage)
    ? 'jobs'
    : currentPage === 'reporting'
      ? 'analytics'
      : currentPage;
  const handleLogout = () => {
    setMenuOpen(false);
    setDrawerOpen(false);
    logout?.();
    onNavigate?.('landing');
  };

  // HANDOFF unified-headers.md §6 — the global org-scoped AgentBar is gone.
  // Agent state now lives inside the per-page AgentHeader's right-side panel
  // (Jobs / Role detail). The lightweight "Agent running" chip in the nav is
  // also scoped to those two surfaces — anywhere else, the chip is hidden so
  // the nav doesn't double-signal what the page hero already shows.
  const showAgentChip = resolvedPage === 'jobs' || resolvedPage === 'role-pipeline' || resolvedPage === 'role-detail';
  const agentChipOn = Boolean(
    orgAgentStatus
      && orgAgentStatus.active_role_count > 0
      && !orgAgentStatus.paused,
  );

  return (
    <>
    <header
      className="mc-nav"
      role="banner"
      style={navLocked ? { pointerEvents: 'none' } : undefined}
      title={navLocked ? 'Preview — navigation disabled' : undefined}
    >
      <PageLink
        page="home"
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
            <AgentLoop kind="pulse" className="dot" />
            Agent running
          </PageLink>
        ) : null}
        <GlobalSearch onNavigate={onNavigate} />
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
          <AnimatePresence initial={false}>
            {menuOpen ? (
              <AvatarMenu
                key="account-menu"
                user={user}
                orgName={orgName}
                onClose={() => setMenuOpen(false)}
                onLogout={handleLogout}
              />
            ) : null}
          </AnimatePresence>
        </div>
        <button
          type="button"
          className="mc-mobile-trigger"
          onClick={() => setDrawerOpen(true)}
          aria-label="Open menu"
          aria-expanded={drawerOpen}
        >
          <Menu size={20} strokeWidth={1.8} />
        </button>
      </div>
    </header>
    <AnimatePresence initial={false}>
      {drawerOpen ? (
        <MobileNavDrawer
          key="mobile-navigation"
          open
          onClose={() => setDrawerOpen(false)}
          initials={initials}
          displayName={displayName}
          orgName={orgName}
          resolvedPage={resolvedPage}
          homePending={homePending}
          onLogout={handleLogout}
          onNavigate={onNavigate}
        />
      ) : null}
    </AnimatePresence>
    </>
  );
};

export default Shell;
