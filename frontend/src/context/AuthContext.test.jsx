import { act, render, screen, fireEvent, waitFor } from '@testing-library/react';
import { vi } from 'vitest';

// AuthProvider's mount effect calls authApi.me() to validate the cached
// token. In jsdom that fires a real HTTP request via undici and the
// resolution races test teardown, surfacing as an "invalid onError
// method" unhandled rejection that fails CI even though every test
// passed. Mock the module so the call is a no-op.
vi.mock('../shared/api/authClient', () => ({
  auth: {
    me: vi.fn().mockResolvedValue({ data: { id: 1, email: 'user@example.com' } }),
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
  },
}));

import { AuthProvider, useAuth } from './AuthContext';
import { auth as authApi } from '../shared/api/authClient';

function TestConsumer() {
  const { isAuthenticated, user, logout, completeLogin } = useAuth();
  return (
    <div>
      <span data-testid="auth-state">{String(isAuthenticated)}</span>
      <span data-testid="auth-email">{user?.email || ''}</span>
      <button onClick={logout}>logout</button>
      <button onClick={() => completeLogin('new-token').catch(() => {})}>complete login</button>
    </div>
  );
}

describe('AuthContext', () => {
  beforeEach(() => {
    authApi.me.mockReset().mockResolvedValue({ data: { id: 1, email: 'user@example.com' } });
    localStorage.clear();
    localStorage.setItem('taali_user', JSON.stringify({ id: 1, email: 'user@example.com' }));
    localStorage.setItem('taali_access_token', 'token');
  });

  it('keeps a late profile bootstrap from restoring a logged-out session', async () => {
    let resolveProfile;
    authApi.me.mockImplementationOnce(() => new Promise((resolve) => {
      resolveProfile = resolve;
    }));
    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>
    );

    expect(screen.getByTestId('auth-state')).toHaveTextContent('true');
    fireEvent.click(screen.getByText('logout'));
    expect(screen.getByTestId('auth-state')).toHaveTextContent('false');
    expect(localStorage.getItem('taali_user')).toBeNull();
    expect(localStorage.getItem('taali_access_token')).toBeNull();

    await act(async () => {
      resolveProfile({ data: { id: 1, email: 'user@example.com' } });
    });

    expect(screen.getByTestId('auth-state')).toHaveTextContent('false');
    expect(localStorage.getItem('taali_user')).toBeNull();
    expect(localStorage.getItem('taali_access_token')).toBeNull();
  });

  it('rolls back a token when profile bootstrap fails', async () => {
    authApi.me.mockRejectedValueOnce(new Error('profile unavailable'));
    localStorage.clear();

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>,
    );
    fireEvent.click(screen.getByText('complete login'));

    await waitFor(() => expect(localStorage.getItem('taali_access_token')).toBeNull());
    expect(localStorage.getItem('taali_user')).toBeNull();
    expect(screen.getByTestId('auth-state')).toHaveTextContent('false');
  });

  it('keeps a stale bootstrap failure from clearing a newer login', async () => {
    let rejectOldProfile;
    authApi.me
      .mockImplementationOnce(() => new Promise((resolve, reject) => {
        void resolve;
        rejectOldProfile = reject;
      }))
      .mockResolvedValueOnce({ data: { id: 2, email: 'new@example.com' } });

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>,
    );
    fireEvent.click(screen.getByText('complete login'));

    await waitFor(() => expect(screen.getByTestId('auth-email')).toHaveTextContent('new@example.com'));
    expect(localStorage.getItem('taali_access_token')).toBe('new-token');

    await act(async () => {
      rejectOldProfile(new Error('old profile request failed late'));
    });

    expect(screen.getByTestId('auth-state')).toHaveTextContent('true');
    expect(screen.getByTestId('auth-email')).toHaveTextContent('new@example.com');
    expect(localStorage.getItem('taali_access_token')).toBe('new-token');
    expect(JSON.parse(localStorage.getItem('taali_user'))).toMatchObject({
      id: 2,
      email: 'new@example.com',
    });
  });

  it('accepts the profile when a sliding refresh rotates the same session token', async () => {
    let resolveProfile;
    authApi.me.mockImplementationOnce(() => new Promise((resolve) => {
      resolveProfile = resolve;
    }));
    localStorage.removeItem('taali_user');

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>,
    );
    localStorage.setItem('taali_access_token', 'refreshed-token');

    await act(async () => {
      resolveProfile({ data: { id: 1, email: 'refreshed@example.com' } });
    });

    expect(screen.getByTestId('auth-state')).toHaveTextContent('true');
    expect(screen.getByTestId('auth-email')).toHaveTextContent('refreshed@example.com');
    expect(localStorage.getItem('taali_access_token')).toBe('refreshed-token');
  });
});
