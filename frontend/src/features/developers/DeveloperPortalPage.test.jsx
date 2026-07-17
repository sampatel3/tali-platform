import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { DeveloperPortalPage } from './DeveloperPortalPage';

const renderPortal = (initialPath = '/developers') => render(
  <MemoryRouter initialEntries={[initialPath]}>
    <DeveloperPortalPage />
  </MemoryRouter>,
);

describe('DeveloperPortalPage focused sections', () => {
  it('restores a hash-linked section and only exposes its focused content', async () => {
    renderPortal('/developers#authentication');

    const sectionNav = screen.getByRole('navigation', {
      name: 'Developer documentation sections',
    });
    const authenticationLink = within(sectionNav).getByRole('link', { name: 'Authentication' });

    expect(authenticationLink).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('heading', { name: 'Authentication' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Taali Developer Portal' })).not.toBeInTheDocument();

    fireEvent.click(within(sectionNav).getByRole('link', { name: 'Endpoints' }));

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Endpoints' })).toBeInTheDocument();
    });
    expect(within(sectionNav).getByRole('link', { name: 'Endpoints' }))
      .toHaveAttribute('aria-current', 'page');
  });
});
