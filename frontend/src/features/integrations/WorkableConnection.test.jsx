import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { connectWorkable } = vi.hoisted(() => ({ connectWorkable: vi.fn() }));

vi.mock('../../shared/api', () => ({
  organizations: {
    connectWorkable,
  },
}));

import { ConnectWorkableButton, WorkableCallbackPage } from './WorkableConnection';

describe('ConnectWorkableButton', () => {
  it('keeps the owner connection action working', () => {
    const onClick = vi.fn();
    render(<ConnectWorkableButton onClick={onClick} />);

    fireEvent.click(screen.getByRole('button', { name: 'Connect Workable' }));

    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('replaces the connection action with owner guidance for members', () => {
    const onClick = vi.fn();
    render(<ConnectWorkableButton onClick={onClick} canManage={false} />);

    expect(screen.getByText(/Only a workspace owner can connect Workable/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Connect Workable' })).not.toBeInTheDocument();
    expect(onClick).not.toHaveBeenCalled();
  });
});

describe('WorkableCallbackPage', () => {
  beforeEach(() => {
    connectWorkable.mockReset();
    connectWorkable.mockResolvedValue({ data: { connected: true } });
  });

  it('returns the OAuth state with the authorization code', async () => {
    const onNavigate = vi.fn();
    render(
      <WorkableCallbackPage
        code="oauth-code"
        state="single-use-state"
        onNavigate={onNavigate}
      />,
    );

    await waitFor(() => {
      expect(connectWorkable).toHaveBeenCalledWith('oauth-code', 'single-use-state');
    });
    expect(onNavigate).toHaveBeenCalledWith('settings', { replace: true });
  });

  it('fails closed when the callback has no state', async () => {
    render(<WorkableCallbackPage code="oauth-code" state="" onNavigate={vi.fn()} />);

    expect(await screen.findByText(/Missing security state/i)).toBeInTheDocument();
    expect(connectWorkable).not.toHaveBeenCalled();
  });
});
