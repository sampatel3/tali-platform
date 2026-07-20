import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ connectWorkable: vi.fn() }));

vi.mock('../../shared/api', () => ({
  organizations: api,
}));

import { WorkableCallbackPage } from './WorkableConnection';

describe('WorkableCallbackPage OAuth state handling', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('passes the returned state with the authorization code', async () => {
    api.connectWorkable.mockResolvedValue({ data: { success: true } });
    const onNavigate = vi.fn();

    render(
      <React.StrictMode>
        <WorkableCallbackPage
          code="workable-code"
          state="signed-one-time-state"
          onNavigate={onNavigate}
        />
      </React.StrictMode>,
    );

    await waitFor(() => {
      expect(api.connectWorkable).toHaveBeenCalledWith(
        'workable-code',
        'signed-one-time-state',
      );
      expect(onNavigate).toHaveBeenCalledWith('settings', { replace: true });
    });
    expect(api.connectWorkable).toHaveBeenCalledTimes(1);
  });

  it('fails locally when Workable returns no security state', async () => {
    render(
      <WorkableCallbackPage
        code="workable-code"
        state=""
        onNavigate={vi.fn()}
      />,
    );

    expect(
      await screen.findByText(/missing security state/i),
    ).toBeInTheDocument();
    expect(api.connectWorkable).not.toHaveBeenCalled();
  });

  it('surfaces an invalid or replayed state response without navigating', async () => {
    api.connectWorkable.mockRejectedValue({
      response: {
        data: {
          detail: 'Invalid, expired, or already used Workable OAuth state. Start the connection again.',
        },
      },
    });
    const onNavigate = vi.fn();

    render(
      <WorkableCallbackPage
        code="workable-code"
        state="replayed-state"
        onNavigate={onNavigate}
      />,
    );

    expect(
      await screen.findByText(/invalid, expired, or already used/i),
    ).toBeInTheDocument();
    expect(onNavigate).not.toHaveBeenCalled();
  });
});
