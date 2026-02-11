import { render, screen, fireEvent } from '@testing-library/react';
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
    localStorage.setItem('tali_user', JSON.stringify({ id: 1, email: 'user@example.com' }));
    localStorage.setItem('tali_access_token', 'token');
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
    expect(localStorage.getItem('tali_user')).toBeNull();
    expect(localStorage.getItem('tali_access_token')).toBeNull();
  });
});
