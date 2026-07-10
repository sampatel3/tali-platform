import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../shared/api', () => ({
  roles: {
    listSubmittalPacks: vi.fn().mockResolvedValue({ data: { packs: [] } }),
    createSubmittalPack: vi.fn(),
    revokeSubmittalPack: vi.fn(),
  },
}));

import { roles as rolesApi } from '../../shared/api';
import { ToastProvider } from '../../context/ToastContext';
import SubmittalPackDialog from './SubmittalPackDialog';

const applications = [
  { id: 11, candidate_name: 'Alice One', candidate_email: 'a@example.com' },
  { id: 22, candidate_name: 'Bob Two', candidate_email: 'b@example.com' },
];

const renderDialog = (props = {}) =>
  render(
    <ToastProvider>
      <SubmittalPackDialog
        open
        roleId={7}
        roleTitle="Backend Engineer"
        applications={applications}
        onClose={() => {}}
        {...props}
      />
    </ToastProvider>,
  );

describe('SubmittalPackDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('lists selected candidates and mints a pack, then shows the public URL', async () => {
    rolesApi.createSubmittalPack.mockResolvedValue({
      data: {
        id: 3,
        token: 'sub_abc',
        url_path: '/submittal/sub_abc',
        expires_at: '2026-07-17T10:00:00Z',
      },
    });

    renderDialog();

    // Both selected candidates are listed with a note input each.
    expect(await screen.findByText('Alice One')).toBeInTheDocument();
    expect(screen.getByText('Bob Two')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Create link \(2\)/ }));

    await waitFor(() => {
      expect(rolesApi.createSubmittalPack).toHaveBeenCalledWith(7, {
        applicationIds: [11, 22],
        title: 'Backend Engineer',
        notes: null,
        expiresIn: '7d',
      });
    });

    // The copyable public URL surfaces (origin + url_path).
    const urlInput = await screen.findByLabelText('Submittal pack public URL');
    expect(urlInput.value).toContain('/submittal/sub_abc');
  });

  it('passes per-candidate notes through when provided', async () => {
    rolesApi.createSubmittalPack.mockResolvedValue({
      data: { id: 4, token: 'sub_xyz', url_path: '/submittal/sub_xyz', expires_at: null },
    });

    renderDialog();

    await screen.findByText('Alice One');
    const noteInputs = screen.getAllByPlaceholderText(/Optional one-line note/);
    fireEvent.change(noteInputs[0], { target: { value: 'Strong signal' } });

    fireEvent.click(screen.getByRole('button', { name: /Create link \(2\)/ }));

    await waitFor(() => {
      expect(rolesApi.createSubmittalPack).toHaveBeenCalledWith(
        7,
        expect.objectContaining({ notes: { '11': 'Strong signal' } }),
      );
    });
  });
});
