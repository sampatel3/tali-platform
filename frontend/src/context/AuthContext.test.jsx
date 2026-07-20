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
import {
  activateSessionBoundary,
  beginSessionTransition,
  captureStoredSessionBoundary,
  getStoredSessionSnapshot,
  initializeSessionBoundary,
  SESSION_BOUNDARY_STORAGE_KEY,
  SESSION_CREDENTIALS_PREFIX,
  SESSION_MIGRATION_BOUNDARY_STORAGE_KEY,
  SESSION_PROFILE_PREFIX,
  storeSessionProfile,
  updateSessionAccessToken,
} from '../shared/auth/sessionBoundary';

const deferred = () => {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
};

const legacyJwt = (subject, issuedAt) => {
  const encode = (value) => btoa(JSON.stringify(value))
    .replace(/=/g, '')
    .replace(/\+/g, '-')
    .replace(/\//g, '_');
  return `${encode({ alg: 'HS256', typ: 'JWT' })}.${encode({
    sub: subject,
    aud: ['fastapi-users:auth'],
    iat: issuedAt,
  })}.signature`;
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
    const activeBoundary = getStoredSessionSnapshot()?.boundary;
    const scopedJobKey = `taali_session_jobs:${encodeURIComponent(activeBoundary)}:tali_tracked_batch_roles`;
    localStorage.setItem(scopedJobKey, '[42]');

    fireEvent.click(screen.getByText('logout'));
    expect(screen.getByTestId('auth-state')).toHaveTextContent('false');
    // V2 logout clears only the scoped session. Shared legacy keys are left to
    // a still-open old bundle and are never read again by the new bundle.
    expect(localStorage.getItem('taali_user')).toContain('user@example.com');
    expect(localStorage.getItem('taali_access_token')).toBe('old-token');
    expect(localStorage.getItem('tali_tracked_batch_roles')).toBe('[42]');
    expect(localStorage.getItem(scopedJobKey)).toBeNull();
    expect(localStorage.getItem('taali_theme')).toBe('dark');
    expect(getOptimisticDecisions().size).toBe(0);

    await act(async () => {
      profile.resolve({ data: { id: 1, email: 'user@example.com' } });
    });

    expect(screen.getByTestId('auth-state')).toHaveTextContent('false');
    expect(localStorage.getItem('taali_user')).toContain('user@example.com');
    expect(localStorage.getItem('taali_access_token')).toBe('old-token');
  });

  it('rolls back a token when profile bootstrap reports invalid credentials', async () => {
    authApi.me.mockRejectedValueOnce({ response: { status: 401 } });
    authApi.login.mockResolvedValueOnce({ data: { access_token: 'new-token' } });

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>,
    );
    fireEvent.click(screen.getByText('first login'));
    const failedBoundary = captureStoredSessionBoundary();

    await waitFor(() => {
      expect(authApi.me).toHaveBeenCalledTimes(1);
      expect(getStoredSessionSnapshot()).toBeNull();
    });
    expect(localStorage.getItem(`${SESSION_CREDENTIALS_PREFIX}${failedBoundary}`)).toBeNull();
    expect(localStorage.getItem(`${SESSION_PROFILE_PREFIX}${failedBoundary}`)).toBeNull();
    expect(screen.getByTestId('auth-state')).toHaveTextContent('false');
  });

  it('keeps a validated cached session during a transient profile outage', async () => {
    const boundary = beginSessionTransition();
    activateSessionBoundary(boundary, 'still-valid-token');
    storeSessionProfile(boundary, { id: 1, email: 'cached@example.com' });
    authApi.me.mockRejectedValueOnce({ response: { status: 503 } });

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>,
    );

    await waitFor(() => expect(authApi.me).toHaveBeenCalledTimes(1));
    expect(screen.getByTestId('auth-state')).toHaveTextContent('true');
    expect(screen.getByTestId('auth-email')).toHaveTextContent('cached@example.com');
    expect(getStoredSessionSnapshot()).toMatchObject({
      boundary,
      token: 'still-valid-token',
    });
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
    expect(getStoredSessionSnapshot()).toMatchObject({
      token: 'new-token',
      profile: { id: 2, email: 'new@example.com' },
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
        email: getStoredSessionSnapshot()?.token === 'second-token'
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

    expect(getStoredSessionSnapshot()?.token).toBe('second-token');
    expect(screen.getByTestId('auth-email')).toHaveTextContent('second@example.com');
    expect(authApi.me).toHaveBeenCalledTimes(1);
  });

  it('does not let an older login response reverse a newer cross-tab session', async () => {
    const loginExchange = deferred();
    authApi.login.mockImplementationOnce(() => loginExchange.promise);

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>,
    );
    fireEvent.click(screen.getByText('first login'));

    let newerBoundary;
    const newerProfile = {
      id: 2,
      email: 'newer-tab@example.com',
    };
    act(() => {
      newerBoundary = beginSessionTransition();
      activateSessionBoundary(newerBoundary, 'newer-tab-token');
      storeSessionProfile(newerBoundary, newerProfile);
      localStorage.setItem('tali_tracked_batch_roles', '[99]');
    });
    await act(async () => {
      loginExchange.resolve({ data: { access_token: 'older-tab-token' } });
    });

    expect(authApi.me).not.toHaveBeenCalled();
    expect(getStoredSessionSnapshot()).toMatchObject({
      boundary: newerBoundary,
      token: 'newer-tab-token',
      profile: newerProfile,
    });
    expect(localStorage.getItem('tali_tracked_batch_roles')).toBe('[99]');
  });

  it('does not let an old mount profile overwrite a newer cross-tab profile', async () => {
    const oldProfile = deferred();
    const accountA = { id: 1, email: 'a@example.com', organization_id: 10 };
    const accountB = { id: 2, email: 'b@example.com', organization_id: 20 };
    authApi.me.mockImplementationOnce(() => oldProfile.promise);
    localStorage.setItem('taali_access_token', 'account-a-token');
    localStorage.setItem('taali_user', JSON.stringify(accountA));

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>,
    );
    let accountBBoundary;
    act(() => {
      accountBBoundary = beginSessionTransition();
      activateSessionBoundary(accountBBoundary, 'account-b-token');
      storeSessionProfile(accountBBoundary, accountB);
    });
    await act(async () => {
      oldProfile.resolve({ data: accountA });
    });

    expect(getStoredSessionSnapshot()).toMatchObject({
      boundary: accountBBoundary,
      token: 'account-b-token',
      profile: accountB,
    });
  });

  it('does not let an old mount failure revoke a newer cross-tab session', async () => {
    const oldProfile = deferred();
    const accountB = { id: 2, email: 'b@example.com', organization_id: 20 };
    authApi.me.mockImplementationOnce(() => oldProfile.promise);
    localStorage.setItem('taali_access_token', 'account-a-token');

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>,
    );
    let accountBBoundary;
    act(() => {
      accountBBoundary = beginSessionTransition();
      activateSessionBoundary(accountBBoundary, 'account-b-token');
      storeSessionProfile(accountBBoundary, accountB);
    });
    await act(async () => {
      oldProfile.reject(new Error('old account profile failed'));
    });

    expect(getStoredSessionSnapshot()).toMatchObject({
      boundary: accountBBoundary,
      token: 'account-b-token',
      profile: accountB,
    });
  });

  it('does not let a later old-bundle refresh revive a failed v2 session', async () => {
    const profile = deferred();
    const originalToken = legacyJwt('account-a', 10);
    const refreshedToken = legacyJwt('account-a', 20);
    authApi.me.mockImplementationOnce(() => profile.promise);
    localStorage.setItem('taali_access_token', originalToken);
    localStorage.setItem('taali_token_issued_at', String(Date.now()));

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>,
    );
    localStorage.setItem('taali_access_token', refreshedToken);
    await act(async () => {
      profile.reject({ response: { status: 401 } });
    });

    expect(localStorage.getItem('taali_access_token')).toBe(refreshedToken);
    expect(getStoredSessionSnapshot()).toBeNull();
    expect(screen.getByTestId('auth-state')).toHaveTextContent('false');
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
    const boundary = getStoredSessionSnapshot()?.boundary;
    updateSessionAccessToken(boundary, 'refreshed-token', {
      expectedToken: 'initial-token',
    });
    await act(async () => {
      profile.resolve({ data: { id: 3, email: 'refreshed@example.com' } });
    });

    expect(screen.getByTestId('auth-email')).toHaveTextContent('refreshed@example.com');
    expect(getStoredSessionSnapshot()?.token).toBe('refreshed-token');
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
    expect(getStoredSessionSnapshot()).toMatchObject({
      token: 'account-b-token',
      profile: accountB,
    });
    expect(readCache('home:org-status')).toBeNull();
    expect(getOptimisticDecisions().size).toBe(0);
    expect(localStorage.getItem('tali_tracked_batch_roles')).toBe('[42]');
    const accountBBoundary = getStoredSessionSnapshot()?.boundary;
    expect(localStorage.getItem(
      `taali_session_jobs:${encodeURIComponent(accountBBoundary)}:tali_tracked_batch_roles`,
    )).toBeNull();
  });

  it('validates a migration that another tab completes after this provider mounted', async () => {
    const token = 'legacy-peer-token';
    localStorage.setItem('taali_access_token', token);
    initializeSessionBoundary();
    const tokenId = JSON.parse(
      localStorage.getItem(SESSION_MIGRATION_BOUNDARY_STORAGE_KEY),
    ).migration.tokenId;
    localStorage.clear();

    const boundary = 'peer-migration';
    localStorage.setItem(SESSION_MIGRATION_BOUNDARY_STORAGE_KEY, JSON.stringify({
      version: 2,
      marker: boundary,
      migration: { version: 1, tokenId },
    }));
    authApi.me.mockResolvedValueOnce({
      data: { id: 5, email: 'peer@example.com' },
    });
    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>,
    );
    expect(screen.getByTestId('auth-state')).toHaveTextContent('false');

    const credentialsKey = `${SESSION_CREDENTIALS_PREFIX}${boundary}`;
    const credentialsValue = JSON.stringify({
      version: 1,
      token,
      issuedAt: 0,
      migrationTokenId: tokenId,
      migrationComplete: true,
    });
    await act(async () => {
      localStorage.setItem(credentialsKey, credentialsValue);
      window.dispatchEvent(new StorageEvent('storage', {
        key: credentialsKey,
        oldValue: null,
        newValue: credentialsValue,
      }));
    });

    await waitFor(() => {
      expect(screen.getByTestId('auth-email')).toHaveTextContent('peer@example.com');
    });
    expect(getStoredSessionSnapshot()).toMatchObject({ boundary, token });
  });

  it('invalidates this tab without erasing the new account when another tab switches session', async () => {
    const accountA = { id: 1, email: 'a@example.com', organization_id: 10 };
    const accountB = { id: 2, email: 'b@example.com', organization_id: 20 };
    authApi.me.mockResolvedValueOnce({ data: accountA });
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
    const externalBoundaryValue = JSON.stringify({ version: 2, marker: externalBoundary });
    const accountBJobsKey = `taali_session_jobs:${encodeURIComponent(externalBoundary)}:tali_tracked_batch_roles`;
    localStorage.setItem(SESSION_BOUNDARY_STORAGE_KEY, externalBoundaryValue);
    localStorage.setItem(`${SESSION_CREDENTIALS_PREFIX}${externalBoundary}`, JSON.stringify({
      version: 1,
      token: 'account-b-token',
      issuedAt: Date.now(),
    }));
    localStorage.setItem(`${SESSION_PROFILE_PREFIX}${externalBoundary}`, JSON.stringify(accountB));
    localStorage.setItem(accountBJobsKey, '[99]');
    await act(async () => {
      window.dispatchEvent(new StorageEvent('storage', {
        key: SESSION_BOUNDARY_STORAGE_KEY,
        oldValue: oldBoundary,
        newValue: externalBoundaryValue,
      }));
    });

    expect(screen.getByTestId('auth-state')).toHaveTextContent('false');
    expect(screen.getByTestId('auth-email')).toHaveTextContent('');
    expect(readCache('home:org-status')).toBeNull();
    expect(getOptimisticDecisions().size).toBe(0);
    // The stale tab invalidates only its in-memory state. It must not race the
    // initiating tab by deleting the new account's shared credentials.
    expect(getStoredSessionSnapshot()).toMatchObject({
      boundary: externalBoundary,
      token: 'account-b-token',
      profile: accountB,
    });
    expect(localStorage.getItem(accountBJobsKey)).toBe('[99]');
  });
});
