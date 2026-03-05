import React, { useEffect, useMemo, useState } from 'react';
import { LogOut, Menu, X } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { organizations as organizationsApi } from '../../shared/api';
import { Logo } from '../../shared/ui/Branding';
import { GlobalThemeToggle } from '../../shared/ui/GlobalThemeToggle';
import { Button } from '../../shared/ui/TaaliPrimitives';

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

const NAV_ITEMS_V2 = [
  { id: 'jobs', label: 'Jobs' },
  { id: 'candidates', label: 'Candidates' },
  { id: 'reporting', label: 'Reporting' },
  { id: 'settings', label: 'Settings' },
];

const NAV_ITEMS_LEGACY = [
  { id: 'assessments', label: 'Assessments' },
  { id: 'candidates', label: 'Candidates' },
  { id: 'tasks', label: 'Tasks' },
  { id: 'reporting', label: 'Reporting' },
  { id: 'settings', label: 'Settings' },
];

export const DashboardNav = ({ currentPage, onNavigate, workflowV2Enabled = false }) => {
  const { user, logout } = useAuth();
  const userName = pickUserName(user);
  const fallbackOrgName = pickOrganizationName(user);
  const [resolvedOrgName, setResolvedOrgName] = useState(fallbackOrgName);
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    setResolvedOrgName(fallbackOrgName);
  }, [fallbackOrgName]);

  useEffect(() => {
    if (!user || fallbackOrgName) return;
    let cancelled = false;
    const loadOrganizationName = async () => {
      try {
        const response = await organizationsApi.get();
        const orgName = (response?.data?.name || '').trim();
        if (!cancelled && orgName) {
          setResolvedOrgName(orgName);
        }
      } catch {
        // Ignore org lookup failures; keep existing fallback label.
      }
    };
    loadOrganizationName();
    return () => {
      cancelled = true;
    };
  }, [fallbackOrgName, user]);

  const orgName = resolvedOrgName || 'No company';
  const navItems = workflowV2Enabled ? NAV_ITEMS_V2 : NAV_ITEMS_LEGACY;
  const homePage = workflowV2Enabled ? 'jobs' : 'assessments';
  const displayName = userName || 'User';
  const initials = useMemo(() => {
    const seed = `${displayName} ${orgName}`.trim();
    const letters = seed.split(/\s+/).filter(Boolean).map((w) => w[0]).join('');
    return letters.slice(0, 2).toUpperCase() || 'U';
  }, [displayName, orgName]);

  const handleLogout = () => {
    logout();
    onNavigate('landing');
  };

  const handleNav = (id) => {
    onNavigate(id);
    setMobileOpen(false);
  };

  return (
    <nav className="taali-nav sticky top-0 z-40">
      <div className="mx-auto flex max-w-[88rem] items-center justify-between gap-3 px-4 py-3 md:px-5">
        <div className="flex items-center gap-6">
          <Logo onClick={() => onNavigate(homePage)} />
          <div className="hidden md:flex items-center gap-2">
            {navItems.map((item) => (
              <Button
                key={item.id}
                variant={currentPage === item.id ? 'secondary' : 'ghost'}
                size="xs"
                className={currentPage === item.id
                  ? 'min-w-[84px] !rounded-full !bg-[var(--taali-surface)] !text-[var(--taali-text)] shadow-[var(--taali-shadow-soft)]'
                  : 'min-w-[84px] !rounded-full !text-[var(--taali-muted)] hover:!bg-[var(--taali-surface-hover)] hover:!text-[var(--taali-text)]'}
                onClick={() => onNavigate(item.id)}
              >
                {item.label}
              </Button>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-3">
          <div className="hidden md:flex items-center gap-2 rounded-full border border-[var(--taali-border-soft)] bg-[var(--taali-nav-pill-bg)] px-2 py-1.5 shadow-[var(--taali-shadow-soft)] backdrop-blur-md">
            <div className="flex flex-col text-right leading-tight">
              <span className="font-mono text-xs text-[var(--taali-text)]">{displayName}</span>
              <span className="font-mono text-xs text-[var(--taali-muted)]">{orgName}</span>
            </div>
            <div className="flex h-9 w-9 items-center justify-center rounded-full border border-[var(--taali-border-soft)] bg-[linear-gradient(145deg,var(--taali-purple),#6b4dff)] text-xs font-bold text-white shadow-[var(--taali-shadow-soft)]">
              {initials}
            </div>
          </div>
          <Button
            variant="ghost"
            size="sm"
            className="!p-2 md:hidden"
            onClick={() => setMobileOpen((o) => !o)}
            aria-label={mobileOpen ? 'Close menu' : 'Open menu'}
          >
            {mobileOpen ? <X size={20} /> : <Menu size={20} />}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="!p-2 hidden md:flex"
            onClick={handleLogout}
            title="Sign out"
          >
            <LogOut size={16} />
          </Button>
          <GlobalThemeToggle className="shrink-0" />
        </div>
      </div>
      {mobileOpen && (
        <div className="md:hidden flex flex-col gap-2 border-t border-[var(--taali-border-soft)] bg-[var(--taali-nav-bg)] px-6 py-4 backdrop-blur-md">
          <div className="mb-1 flex items-center justify-between rounded-[var(--taali-radius-card)] border border-[var(--taali-border-soft)] bg-[var(--taali-surface)] px-3 py-2">
            <div className="min-w-0">
              <div className="truncate font-mono text-xs text-[var(--taali-text)]">{displayName}</div>
              <div className="truncate font-mono text-xs text-[var(--taali-muted)]">{orgName}</div>
            </div>
            <div className="flex h-8 w-8 items-center justify-center rounded-full border border-[var(--taali-border-soft)] bg-[linear-gradient(145deg,var(--taali-purple),#6b4dff)] text-xs font-bold text-white shadow-[var(--taali-shadow-soft)]">
              {initials}
            </div>
          </div>
          {navItems.map((item) => (
            <Button
              key={item.id}
              variant={currentPage === item.id ? 'secondary' : 'ghost'}
              size="sm"
              className={currentPage === item.id
                ? 'font-mono w-full justify-start !rounded-[var(--taali-radius-control)] !bg-[var(--taali-purple-soft)]'
                : 'font-mono w-full justify-start'}
              onClick={() => handleNav(item.id)}
            >
              {item.label}
            </Button>
          ))}
          <Button
            variant="ghost"
            size="sm"
            className="font-mono w-full justify-start mt-2"
            onClick={handleLogout}
          >
            <LogOut size={16} className="mr-2" /> Sign out
          </Button>
        </div>
      )}
    </nav>
  );
};
