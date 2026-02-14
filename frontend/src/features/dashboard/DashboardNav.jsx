import React from 'react';
import { LogOut } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { Logo } from '../../shared/ui/Branding';
import { Button } from '../../shared/ui/TaaliPrimitives';

export const DashboardNav = ({ currentPage, onNavigate }) => {
  const { user, logout } = useAuth();
  const orgName = user?.organization?.name || '--';
  const initials = orgName.split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase();

  const handleLogout = () => {
    logout();
    onNavigate('landing');
  };

  return (
    <nav className="taali-nav">
      <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between gap-4">
        <div className="flex items-center gap-8">
          <Logo onClick={() => onNavigate('dashboard')} />
          <div className="hidden md:flex items-center gap-1">
            {[
              { id: 'dashboard', label: 'Dashboard' },
              { id: 'candidates', label: 'Candidates' },
              { id: 'tasks', label: 'Tasks' },
              { id: 'analytics', label: 'Analytics' },
              { id: 'settings', label: 'Settings' },
            ].map((item) => (
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
          <span className="font-mono text-sm hidden sm:inline">{orgName}</span>
          <div
            className="w-9 h-9 border-2 border-black flex items-center justify-center text-white font-bold text-sm"
            style={{ backgroundColor: 'var(--taali-purple)' }}
          >
            {initials}
          </div>
          <Button
            variant="ghost"
            size="sm"
            className="!p-2"
            onClick={handleLogout}
            title="Sign out"
          >
            <LogOut size={16} />
          </Button>
        </div>
      </div>
    </nav>
  );
};
