import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { RoleViewTabs, useRoleView } from './RoleViewTabs';

const RoleViewsHarness = ({ onBeforeNavigate }) => {
  const [activeView] = useRoleView();
  return <RoleViewTabs activeView={activeView} onBeforeNavigate={onBeforeNavigate} />;
};

const renderAt = (entry, props = {}) => render(
  <MemoryRouter initialEntries={[entry]}>
    <Routes>
      <Route path="/jobs/:roleId" element={<RoleViewsHarness {...props} />} />
    </Routes>
  </MemoryRouter>,
);

describe('RoleViewTabs', () => {
  it('marks the URL-selected view current and preserves unrelated query params', () => {
    renderAt('/jobs/101?view=pipeline&from=home');

    const navigation = screen.getByRole('navigation', { name: 'Job views' });
    expect(within(navigation).getByRole('link', { name: 'Pipeline' }))
      .toHaveAttribute('aria-current', 'page');
    expect(within(navigation).getByRole('link', { name: 'Candidates' }))
      .toHaveAttribute('href', '/jobs/101?from=home');
    expect(within(navigation).getByRole('link', { name: 'Job spec' }))
      .toHaveAttribute('href', '/jobs/101?view=activity&from=home');
    expect(within(navigation).getByRole('link', { name: 'Hiring team' }))
      .toHaveAttribute('href', '/jobs/101?view=hiring-team&from=home');
  });

  it('falls back to Candidates when the view query is unknown', () => {
    renderAt('/jobs/101?view=unknown&from=home');

    const navigation = screen.getByRole('navigation', { name: 'Job views' });
    expect(within(navigation).getByRole('link', { name: 'Candidates' }))
      .toHaveAttribute('aria-current', 'page');
    expect(within(navigation).getByRole('link', { name: 'Agent settings' }))
      .toHaveAttribute('href', '/jobs/101?view=role-fit&from=home');
  });

  it('keeps guarded navigation behavior on the shared peer bar', () => {
    const onBeforeNavigate = vi.fn((event) => event.preventDefault());
    renderAt('/jobs/101?view=activity', { onBeforeNavigate });

    fireEvent.click(screen.getByRole('link', { name: 'Candidates' }));

    expect(onBeforeNavigate).toHaveBeenCalledWith(expect.any(Object), 'table');
    expect(screen.getByRole('link', { name: 'Job spec' }))
      .toHaveAttribute('aria-current', 'page');
  });
});
