import { render, screen, fireEvent } from '@testing-library/react';
import { vi } from 'vitest';

// AuthProvider's mount effect calls authApi.me() to validate the cached
// token. In jsdom that fires a real HTTP request via undici and the
// resolution races test teardown, surfacing as an "invalid onError
// method" unhandled rejection that fails CI even though every test
// passed. Mock the module so the call is a no-op.
vi.mock('../shared/api', () => ({
  auth: {
    me: vi.fn().mockResolvedValue({ data: { id: 1, email: 'user@example.com' } }),
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
  },
}));

import { AuthProvider, useAuth } from './AuthContext';

function TestConsumer() {
  const { isAuthenticated, logout } = useAuth();
  return (
    <div>
      <span data-testid="auth-state">{String(isAuthenticated)}</span>
      <button onClick={logout}>logout</button>
    </div>
  );
}

describe('AuthContext', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('taali_user', JSON.stringify({ id: 1, email: 'user@example.com' }));
    localStorage.setItem('taali_access_token', 'token');
  });

  it('hydrates auth state from localStorage and clears on logout', () => {
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
  });
});
