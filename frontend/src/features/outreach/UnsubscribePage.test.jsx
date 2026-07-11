import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

vi.mock('../../shared/api/httpClient', () => ({
  fetchUnsubscribe: vi.fn(),
  submitUnsubscribe: vi.fn(),
}));

import { fetchUnsubscribe, submitUnsubscribe } from '../../shared/api/httpClient';
import UnsubscribePage from './UnsubscribePage';

const renderAt = (path) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/unsubscribe/:token" element={<UnsubscribePage />} />
      </Routes>
    </MemoryRouter>,
  );

describe('UnsubscribePage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows the org name + masked email and confirms after unsubscribe', async () => {
    fetchUnsubscribe.mockResolvedValue({
      data: { organization_name: 'Acme Corp', email_masked: 'j***@acme.com' },
    });
    submitUnsubscribe.mockResolvedValue({ data: { status: 'unsubscribed' } });

    renderAt('/unsubscribe/tok_valid');

    await waitFor(() => expect(fetchUnsubscribe).toHaveBeenCalledWith('tok_valid'));
    expect(await screen.findByText(/Unsubscribe from Acme Corp/i)).toBeInTheDocument();
    expect(screen.getByText('j***@acme.com')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /^unsubscribe$/i }));

    await waitFor(() => expect(submitUnsubscribe).toHaveBeenCalledWith('tok_valid'));
    expect(await screen.findByText(/You're unsubscribed/i)).toBeInTheDocument();
  });

  it('shows an invalid-link state on 404', async () => {
    fetchUnsubscribe.mockRejectedValue({ response: { status: 404 } });
    renderAt('/unsubscribe/tok_bad');
    expect(await screen.findByText(/invalid or has expired/i)).toBeInTheDocument();
  });
});
