import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const listMock = vi.fn();
const createMock = vi.fn();
const revokeMock = vi.fn();
const showToast = vi.fn();

vi.mock('../../shared/api', () => ({
  apiKeys: {
    list: (...a) => listMock(...a),
    create: (...a) => createMock(...a),
    revoke: (...a) => revokeMock(...a),
  },
}));

vi.mock('../../context/ToastContext', () => ({
  useToast: () => ({ showToast }),
}));

import ApiKeysPanel from './ApiKeysPanel';

const SCOPES = [
  'roles:read',
  'applications:read',
  'assessments:read',
  'assessments:write',
  'share-links:write',
];

beforeEach(() => {
  listMock.mockReset();
  createMock.mockReset();
  revokeMock.mockReset();
  showToast.mockReset();
  listMock.mockResolvedValue({ data: { keys: [], available_scopes: SCOPES } });
});

describe('ApiKeysPanel', () => {
  it('loads scopes and shows the empty state', async () => {
    render(<ApiKeysPanel />);
    await waitFor(() => expect(listMock).toHaveBeenCalled());
    expect(await screen.findByText('No API keys yet.')).toBeInTheDocument();
    expect(screen.getByText('assessments:write')).toBeInTheDocument();
  });

  it('lists existing keys with masked prefix', async () => {
    listMock.mockResolvedValue({
      data: {
        keys: [
          {
            id: 1,
            name: 'Warehouse',
            prefix: 'tali_live_ab',
            is_test: false,
            scopes: ['roles:read'],
            last_used_at: null,
            revoked_at: null,
          },
        ],
        available_scopes: SCOPES,
      },
    });
    render(<ApiKeysPanel />);
    expect(await screen.findByText('Warehouse')).toBeInTheDocument();
    expect(screen.getByText('tali_live_ab…')).toBeInTheDocument();
  });

  it('creates a key and reveals the secret exactly once', async () => {
    createMock.mockResolvedValue({ data: { secret: 'tali_live_supersecret' } });
    render(<ApiKeysPanel />);
    await waitFor(() => expect(listMock).toHaveBeenCalled());

    fireEvent.change(screen.getByPlaceholderText(/Data warehouse sync/i), {
      target: { value: 'CI' },
    });
    fireEvent.click(screen.getByRole('button', { name: /create key/i }));

    await waitFor(() => expect(createMock).toHaveBeenCalled());
    expect(await screen.findByText('tali_live_supersecret')).toBeInTheDocument();

    const payload = createMock.mock.calls[0][0];
    expect(payload.name).toBe('CI');
    expect(payload.scopes).toContain('roles:read');
  });

  it('revokes a key after confirmation', async () => {
    listMock.mockResolvedValue({
      data: {
        keys: [
          {
            id: 7,
            name: 'Old key',
            prefix: 'tali_live_zz',
            is_test: false,
            scopes: [],
            last_used_at: null,
            revoked_at: null,
          },
        ],
        available_scopes: SCOPES,
      },
    });
    revokeMock.mockResolvedValue({ data: {} });
    vi.spyOn(window, 'confirm').mockReturnValue(true);

    render(<ApiKeysPanel />);
    await screen.findByText('Old key');
    fireEvent.click(screen.getByRole('button', { name: /revoke/i }));

    await waitFor(() => expect(revokeMock).toHaveBeenCalledWith(7));
  });
});
