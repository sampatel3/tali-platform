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
  it('opens a compact menu with the production app tabs and preserves active state', () => {
    const onNavigate = vi.fn();

    render(
      <DashboardNav
        currentPage="tasks"
        onNavigate={onNavigate}
        workflowV2Enabled
      />
    );

    fireEvent.click(screen.getByRole('button', { name: 'Open navigation menu' }));

    const mobileMenu = document.querySelector('.dashboard-nav-mobile');
    expect(mobileMenu).toBeInTheDocument();
    expect(within(mobileMenu).getByText('Sam Patel')).toBeInTheDocument();
    expect(within(mobileMenu).getByText('DEEPLIGHT_AI')).toBeInTheDocument();

    const mobileLinks = within(mobileMenu).getAllByRole('menuitem')
      .filter((button) => button.className.includes('dashboard-nav-mobile-link'));
    expect(mobileLinks.map((button) => button.textContent)).toEqual([
      'Jobs',
      'Candidates',
      'Tasks',
      'Reporting',
      'Settings',
    ]);
    expect(within(mobileMenu).getByRole('menuitem', { name: 'Tasks' })).toHaveClass('active');

    fireEvent.click(within(mobileMenu).getByRole('menuitem', { name: 'Candidates' }));

    expect(onNavigate).toHaveBeenCalledWith('candidates');
    expect(document.querySelector('.dashboard-nav-mobile')).not.toBeInTheDocument();
  });
});
