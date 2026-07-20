import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { vi } from 'vitest';

vi.mock('../shared/api/authClient', () => ({
  auth: {
    me: vi.fn(),
    login: vi.fn(),
    register: vi.fn(),
    acceptInvite: vi.fn(),
  },
}));

import { AuthProvider, useAuth } from './AuthContext';
import { auth as authApi } from '../shared/api/authClient';
import {
  getOptimisticDecisions,
  resetOptimisticDecisions,
  updateOptimisticDecisions,
} from '../features/home/optimisticDecisionStore';
import { readCache, writeCache } from '../shared/api/resourceCache';
import { SESSION_BOUNDARY_STORAGE_KEY } from '../shared/auth/sessionBoundary';

const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
};

function TestConsumer() {
  const {
    acceptInvite,
    isAuthenticated,
    login,
    logout,
    user,
  } = useAuth();
  return (
    <div>
      <span data-testid="auth-state">{String(isAuthenticated)}</span>
      <span data-testid="auth-email">{user?.email || ''}</span>
      <button type="button" onClick={logout}>logout</button>
      <button
        type="button"
        onClick={() => login('first@example.com', 'password').catch(() => {})}
      >
        first login
      </button>
      <button
        type="button"
        onClick={() => login('second@example.com', 'password').catch(() => {})}
      >
        second login
      </button>
      <button
        type="button"
        onClick={() => acceptInvite('invite-token', 'password').catch(() => {})}
      >
        accept invite
      </button>
    </div>
  );
}

describe('AuthContext', () => {
  beforeEach(() => {
    resetOptimisticDecisions();
    localStorage.clear();
    authApi.me.mockReset().mockResolvedValue({
      data: { id: 1, email: 'user@example.com' },
    });
    authApi.login.mockReset();
    authApi.register.mockReset();
    authApi.acceptInvite.mockReset();
  });

  it('keeps a late profile bootstrap from restoring a logged-out session', async () => {
    const profile = deferred();
    authApi.me.mockImplementationOnce(() => profile.promise);
    localStorage.setItem('taali_user', JSON.stringify({
      id: 1,
      email: 'user@example.com',
    }));
    localStorage.setItem('taali_access_token', 'old-token');
    localStorage.setItem('tali_tracked_batch_roles', '[42]');
    localStorage.setItem('taali_theme', 'dark');
    updateOptimisticDecisions(() => new Map([
      [204991, { scopeKey: 'role:135', settleAfter: null }],
    ]));

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>,
    );

    fireEvent.click(screen.getByText('logout'));
    expect(screen.getByTestId('auth-state')).toHaveTextContent('false');
    expect(localStorage.getItem('taali_user')).toBeNull();
    expect(localStorage.getItem('taali_access_token')).toBeNull();
    expect(localStorage.getItem('tali_tracked_batch_roles')).toBeNull();
    expect(localStorage.getItem('taali_theme')).toBe('dark');
    expect(getOptimisticDecisions().size).toBe(0);

    await act(async () => {
      profile.resolve({ data: { id: 1, email: 'user@example.com' } });
    });

    expect(screen.getByTestId('auth-state')).toHaveTextContent('false');
    expect(localStorage.getItem('taali_user')).toBeNull();
    expect(localStorage.getItem('taali_access_token')).toBeNull();
  });

  it('rolls back a token when profile bootstrap fails', async () => {
    authApi.me.mockRejectedValueOnce(new Error('profile unavailable'));
    authApi.login.mockResolvedValueOnce({ data: { access_token: 'new-token' } });

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>,
    );
    fireEvent.click(screen.getByText('first login'));

    await waitFor(() => {
      expect(localStorage.getItem('taali_access_token')).toBeNull();
    });
    expect(localStorage.getItem('taali_user')).toBeNull();
    expect(screen.getByTestId('auth-state')).toHaveTextContent('false');
  });

  it('keeps a stale bootstrap failure from clearing a newer login', async () => {
    const oldProfile = deferred();
    authApi.me
      .mockImplementationOnce(() => oldProfile.promise)
      .mockResolvedValueOnce({ data: { id: 2, email: 'new@example.com' } });
    authApi.login.mockResolvedValue({ data: { access_token: 'new-token' } });
    localStorage.setItem('taali_access_token', 'old-token');
    localStorage.setItem('taali_user', JSON.stringify({
      id: 1,
      email: 'old@example.com',
    }));

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>,
    );
    fireEvent.click(screen.getByText('second login'));

    await waitFor(() => {
      expect(screen.getByTestId('auth-email')).toHaveTextContent('new@example.com');
    });
    await act(async () => {
      oldProfile.reject(new Error('old profile request failed late'));
    });

    expect(screen.getByTestId('auth-state')).toHaveTextContent('true');
    expect(localStorage.getItem('taali_access_token')).toBe('new-token');
    expect(JSON.parse(localStorage.getItem('taali_user'))).toMatchObject({
      id: 2,
      email: 'new@example.com',
    });
  });

  it('orders concurrent sign-ins by invocation rather than response time', async () => {
    const firstLogin = deferred();
    const secondLogin = deferred();
    authApi.login.mockImplementation((email) => (
      email === 'first@example.com' ? firstLogin.promise : secondLogin.promise
    ));
    authApi.me.mockImplementation(() => Promise.resolve({
      data: {
        id: 2,
        email: localStorage.getItem('taali_access_token') === 'second-token'
          ? 'second@example.com'
          : 'unexpected@example.com',
      },
    }));

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>,
    );
    fireEvent.click(screen.getByText('first login'));
    fireEvent.click(screen.getByText('second login'));

    await act(async () => {
      secondLogin.resolve({ data: { access_token: 'second-token' } });
    });
    await waitFor(() => {
      expect(screen.getByTestId('auth-email')).toHaveTextContent('second@example.com');
    });

    await act(async () => {
      firstLogin.resolve({ data: { access_token: 'first-token' } });
    });

    expect(localStorage.getItem('taali_access_token')).toBe('second-token');
    expect(screen.getByTestId('auth-email')).toHaveTextContent('second@example.com');
    expect(authApi.me).toHaveBeenCalledTimes(1);
  });

  it('does not complete an invite token exchange after logout supersedes it', async () => {
    const invite = deferred();
    authApi.acceptInvite.mockImplementationOnce(() => invite.promise);

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>,
    );
    fireEvent.click(screen.getByText('accept invite'));
    fireEvent.click(screen.getByText('logout'));
    await act(async () => {
      invite.resolve({ data: { access_token: 'stale-invite-token' } });
    });

    expect(localStorage.getItem('taali_access_token')).toBeNull();
    expect(screen.getByTestId('auth-state')).toHaveTextContent('false');
    expect(authApi.me).not.toHaveBeenCalled();
  });

  it('accepts a profile after sliding refresh rotates the same session token', async () => {
    const profile = deferred();
    authApi.me.mockImplementationOnce(() => profile.promise);
    localStorage.setItem('taali_access_token', 'initial-token');

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>,
    );
    localStorage.setItem('taali_access_token', 'refreshed-token');
    await act(async () => {
      profile.resolve({ data: { id: 3, email: 'refreshed@example.com' } });
    });

    expect(screen.getByTestId('auth-email')).toHaveTextContent('refreshed@example.com');
    expect(localStorage.getItem('taali_access_token')).toBe('refreshed-token');
  });

  it('clears account-A private state before committing an account-B invite session', async () => {
    const accountA = { id: 1, email: 'a@example.com', organization_id: 10 };
    const accountB = { id: 2, email: 'b@example.com', organization_id: 20 };
    localStorage.setItem('taali_access_token', 'account-a-token');
    localStorage.setItem('taali_user', JSON.stringify(accountA));
    localStorage.setItem('tali_tracked_batch_roles', '[42]');
    writeCache('home:org-status', { organization_id: 10, pending: 7 });
    updateOptimisticDecisions(() => new Map([
      [42, { scopeKey: 'org:10', settleAfter: null }],
    ]));
    authApi.me
      .mockReset()
      .mockResolvedValueOnce({ data: accountA })
      .mockResolvedValueOnce({ data: accountB });
    authApi.acceptInvite.mockResolvedValueOnce({
      data: { access_token: 'account-b-token' },
    });

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>,
    );
    await waitFor(() => {
      expect(screen.getByTestId('auth-email')).toHaveTextContent('a@example.com');
    });

    fireEvent.click(screen.getByText('accept invite'));

    await waitFor(() => {
      expect(screen.getByTestId('auth-email')).toHaveTextContent('b@example.com');
    });
    expect(localStorage.getItem('taali_access_token')).toBe('account-b-token');
    expect(readCache('home:org-status')).toBeNull();
    expect(getOptimisticDecisions().size).toBe(0);
    expect(localStorage.getItem('tali_tracked_batch_roles')).toBeNull();
  });

  it('invalidates this tab without erasing the new account when another tab switches session', async () => {
    const accountA = { id: 1, email: 'a@example.com', organization_id: 10 };
    const accountB = { id: 2, email: 'b@example.com', organization_id: 20 };
    localStorage.setItem('taali_access_token', 'account-a-token');
    localStorage.setItem('taali_user', JSON.stringify(accountA));
    writeCache('home:org-status', { organization_id: 10, pending: 7 });
    updateOptimisticDecisions(() => new Map([
      [42, { scopeKey: 'org:10', settleAfter: null }],
    ]));

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>,
    );
    await waitFor(() => {
      expect(screen.getByTestId('auth-email')).toHaveTextContent('a@example.com');
    });

    const oldBoundary = localStorage.getItem(SESSION_BOUNDARY_STORAGE_KEY);
    const externalBoundary = 'account-b-boundary';
    localStorage.setItem(SESSION_BOUNDARY_STORAGE_KEY, externalBoundary);
    localStorage.setItem('taali_access_token', 'account-b-token');
    localStorage.setItem('taali_user', JSON.stringify(accountB));
    await act(async () => {
      window.dispatchEvent(new StorageEvent('storage', {
        key: SESSION_BOUNDARY_STORAGE_KEY,
        oldValue: oldBoundary,
        newValue: externalBoundary,
      }));
    });

    expect(screen.getByTestId('auth-state')).toHaveTextContent('false');
    expect(screen.getByTestId('auth-email')).toHaveTextContent('');
    expect(readCache('home:org-status')).toBeNull();
    expect(getOptimisticDecisions().size).toBe(0);
    // The stale tab invalidates only its in-memory state. It must not race the
    // initiating tab by deleting the new account's shared credentials.
    expect(localStorage.getItem('taali_access_token')).toBe('account-b-token');
    expect(JSON.parse(localStorage.getItem('taali_user'))).toEqual(accountB);
  });
});
