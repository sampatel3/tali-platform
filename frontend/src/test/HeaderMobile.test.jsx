import React from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('../context/AuthContext', () => ({
  useAuth: () => ({
    user: {
      email: 'sam@taali.ai',
      full_name: 'Sam Patel',
      organization: { name: 'DeepLight AI' },
    },
    logout: vi.fn(),
  }),
}));

vi.mock('../shared/api', () => ({
  organizations: {
    get: vi.fn(),
  },
}));

import { DashboardNav } from '../features/dashboard/DashboardNav';

describe('DashboardNav mobile header', () => {
  it('keeps desktop avatar and icon buttons on the same centered circle grid', () => {
    render(
      <DashboardNav
        currentPage="jobs"
        onNavigate={vi.fn()}
      />
    );

    const avatar = document.querySelector('.app-user .app-avatar.desktop-only');
    const signOutButton = screen.getByTitle('Sign out');

    expect(avatar).toHaveClass('app-avatar', 'desktop-only');
    expect(signOutButton).toHaveClass('icon-btn', 'desktop-only');
  });

  it('opens a compact menu with the production app tabs and preserves active state', () => {
    const onNavigate = vi.fn();

    render(
      <DashboardNav
        currentPage="tasks"
        onNavigate={onNavigate}
      />
    );

    fireEvent.click(screen.getByRole('button', { name: 'Open navigation menu' }));

    const mobileMenu = document.querySelector('.dashboard-nav-mobile');
    expect(mobileMenu).toBeInTheDocument();
    expect(within(mobileMenu).getByText('Sam Patel')).toBeInTheDocument();
    expect(within(mobileMenu).getByText('DeepLight AI')).toBeInTheDocument();

    const mobileLinks = within(mobileMenu).getAllByRole('menuitem')
      .filter((button) => button.className.includes('dashboard-nav-mobile-link'));
    // The chat tab is labelled "Chat" (same `id: chat`, same /chat route —
    // it was briefly "Search"; only the user-facing label moved).
    expect(mobileLinks.map((button) => button.textContent)).toEqual([
      'Jobs',
      'Chat',
      'Tasks',
      'Reporting',
      'Settings',
    ]);
    expect(within(mobileMenu).getByRole('menuitem', { name: 'Tasks' })).toHaveClass('active');

    fireEvent.click(within(mobileMenu).getByRole('menuitem', { name: /Chat/ }));

    expect(onNavigate).toHaveBeenCalledWith('chat');
    expect(document.querySelector('.dashboard-nav-mobile')).not.toBeInTheDocument();
  });
});
