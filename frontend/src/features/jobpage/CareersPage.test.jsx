import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-router-dom', () => ({ useParams: () => ({ slug: 'acme' }) }));
vi.mock('../../shared/seo/useDocumentMeta', () => ({ useDocumentMeta: vi.fn() }));
vi.mock('../requisitions/api', () => ({
  publicCareersApi: { get: vi.fn() },
}));

import { publicCareersApi } from '../requisitions/api';
import { CareersPage } from './CareersPage';

describe('CareersPage pagination', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    publicCareersApi.get
      .mockResolvedValueOnce({
        organization_name: 'Acme',
        slug: 'acme',
        jobs: [{ token: 'one', title: 'First role' }],
        has_more: true,
        next_offset: 24,
      })
      .mockResolvedValueOnce({
        organization_name: 'Acme',
        slug: 'acme',
        jobs: [{ token: 'two', title: 'Later role' }],
        has_more: false,
        next_offset: null,
      });
  });

  it('keeps the first page while appending a requested later page', async () => {
    render(<CareersPage />);
    await screen.findByText('First role');
    fireEvent.click(screen.getByRole('button', { name: 'Load more roles' }));

    await waitFor(() => expect(screen.getByText('Later role')).toBeInTheDocument());
    expect(screen.getByText('First role')).toBeInTheDocument();
    expect(publicCareersApi.get).toHaveBeenNthCalledWith(1, 'acme', { limit: 24, offset: 0 });
    expect(publicCareersApi.get).toHaveBeenNthCalledWith(2, 'acme', { limit: 24, offset: 24 });
    expect(screen.queryByRole('button', { name: 'Load more roles' })).not.toBeInTheDocument();
  });
});
