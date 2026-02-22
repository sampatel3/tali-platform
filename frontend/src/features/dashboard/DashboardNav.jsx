import React, { useEffect, useMemo, useState } from 'react';
import { LogOut, Menu, X } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { organizations as organizationsApi } from '../../shared/api';
import { Logo } from '../../shared/ui/Branding';
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

const NAV_ITEMS = [
  { id: 'dashboard', label: 'Assessments' },
  { id: 'candidates', label: 'Candidates' },
  { id: 'tasks', label: 'Tasks' },
  { id: 'analytics', label: 'Analytics' },
  { id: 'settings', label: 'Settings' },
];

export const DashboardNav = ({ currentPage, onNavigate }) => {
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
    <nav className="taali-nav border-b-2 border-[var(--taali-border)] bg-[var(--taali-surface)]">
      <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between gap-4">
        <div className="flex items-center gap-8">
          <Logo onClick={() => onNavigate('dashboard')} />
          <div className="hidden md:flex items-center gap-1">
            {NAV_ITEMS.map((item) => (
              <Button
                key={item.id}
                variant={currentPage === item.id ? 'primary' : 'ghost'}
                size="sm"
                className="font-mono min-w-[92px]"
                onClick={() => onNavigate(item.id)}
              >
                {item.label}
              </Button>
            ))}
          </div>
        </div>
        <div className="flex items-center gap-4">
          <div className="hidden sm:flex flex-col text-right leading-tight">
            <span className="font-mono text-sm text-[var(--taali-text)]">{displayName}</span>
            <span className="font-mono text-xs text-[var(--taali-muted)]">{orgName}</span>
          </div>
          <div className="w-9 h-9 border-2 border-[var(--taali-border)] flex items-center justify-center text-white font-bold text-sm bg-[var(--taali-purple)]">
            {initials}
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
        </div>
      </div>
      {mobileOpen && (
        <div className="md:hidden border-t-2 border-[var(--taali-border)] bg-[var(--taali-surface)] px-6 py-4 flex flex-col gap-2">
          {NAV_ITEMS.map((item) => (
            <Button
              key={item.id}
              variant={currentPage === item.id ? 'primary' : 'ghost'}
              size="sm"
              className="font-mono w-full justify-start"
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
