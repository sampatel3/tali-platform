import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AuthProvider } from '../context/AuthContext';
import {
  ForgotPasswordPage,
  LoginPage,
  RegisterPage,
  ResetPasswordPage,
  VerifyEmailPage,
} from '../features/auth';
import { auth } from '../shared/api';

vi.mock('../shared/api', () => ({
  auth: {
    login: vi.fn(),
    register: vi.fn(),
    me: vi.fn(),
    verifyEmail: vi.fn(),
    resendVerification: vi.fn(),
    forgotPassword: vi.fn(),
    resetPassword: vi.fn(),
    ssoCheck: vi.fn(),
  },
}));

const renderWithAuth = (ui) => render(
  <MemoryRouter>
    <AuthProvider>{ui}</AuthProvider>
  </MemoryRouter>
);

describe('Auth page redesign', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it('renders the redesigned sign-in page and submits through AuthContext', async () => {
    const onNavigate = vi.fn();
    auth.login.mockResolvedValue({ data: { access_token: 'tok_123' } });
    auth.me.mockResolvedValue({
      data: { id: 1, email: 'sam@taali.ai', full_name: 'Sam Patel' },
    });

    renderWithAuth(<LoginPage onNavigate={onNavigate} />);

    expect(screen.getByRole('heading', { name: /Sign in/i })).toBeInTheDocument();
    expect(screen.getByText('Request access')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText('you@company.com'), {
      target: { value: 'sam@taali.ai' },
    });
    fireEvent.change(screen.getByPlaceholderText('••••••••'), {
      target: { value: 'password123' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Sign in →' }));

    await waitFor(() => {
      expect(auth.login).toHaveBeenCalledWith('sam@taali.ai', 'password123');
      expect(auth.me).toHaveBeenCalled();
      expect(onNavigate).toHaveBeenCalledWith('dashboard');
    });
  });

  it('shows the redesigned verification recovery state on sign-in failure', async () => {
    auth.login.mockRejectedValue({
      response: { status: 403, data: { detail: 'Please verify your email before logging in' } },
    });

    renderWithAuth(<LoginPage onNavigate={vi.fn()} />);

    fireEvent.change(screen.getByPlaceholderText('you@company.com'), {
      target: { value: 'pending@taali.ai' },
    });
    fireEvent.change(screen.getByPlaceholderText('••••••••'), {
      target: { value: 'password123' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Sign in →' }));

    await waitFor(() => {
      expect(screen.getByText('Sign-in failed')).toBeInTheDocument();
      expect(screen.getByText('Please verify your email before logging in')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Resend verification email' })).toBeInTheDocument();
    });
  });

  it('renders the redesigned registration success state after create account', async () => {
    auth.register.mockResolvedValue({ data: { success: true } });

    renderWithAuth(<RegisterPage onNavigate={vi.fn()} />);

    fireEvent.change(screen.getByPlaceholderText('you@company.com'), {
      target: { value: 'sam@taali.ai' },
    });
    fireEvent.change(screen.getByPlaceholderText('Sam Patel'), {
      target: { value: 'Sam Patel' },
    });
    fireEvent.change(screen.getByPlaceholderText('Deeplight AI'), {
      target: { value: 'Taali' },
    });
    fireEvent.change(screen.getByPlaceholderText('••••••••'), {
      target: { value: 'password123' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Create account →' }));

    await waitFor(() => {
      expect(auth.register).toHaveBeenCalledWith({
        email: 'sam@taali.ai',
        password: 'password123',
        full_name: 'Sam Patel',
        organization_name: 'Taali',
      });
      expect(screen.getByRole('heading', { name: /Check your inbox/i })).toBeInTheDocument();
      expect(screen.getByText('sam@taali.ai')).toBeInTheDocument();
    });
  });

  it('shows the redesigned forgot-password confirmation state', async () => {
    auth.forgotPassword.mockResolvedValue({ data: { success: true } });

    render(<ForgotPasswordPage onNavigate={vi.fn()} />);

    expect(screen.getByRole('heading', { name: /Forgot your password/i })).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText('you@company.com'), {
      target: { value: 'sam@taali.ai' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Send reset link/i }));

    await waitFor(() => {
      expect(auth.forgotPassword).toHaveBeenCalledWith('sam@taali.ai');
      expect(screen.getByRole('heading', { name: /Check your email/i })).toBeInTheDocument();
    });
  });

  it('updates the password and shows the redesigned reset success state', async () => {
    auth.resetPassword.mockResolvedValue({ data: { success: true } });

    render(<ResetPasswordPage token="reset-token" onNavigate={vi.fn()} />);

    fireEvent.change(screen.getAllByPlaceholderText('••••••••')[0], {
      target: { value: 'password123' },
    });
    fireEvent.change(screen.getAllByPlaceholderText('••••••••')[1], {
      target: { value: 'password123' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Update password/i }));

    await waitFor(() => {
      expect(auth.resetPassword).toHaveBeenCalledWith('reset-token', 'password123');
      expect(screen.getByRole('heading', { name: /Password updated/i })).toBeInTheDocument();
    });
  });

  it('verifies email and lands on the redesigned success screen', async () => {
    auth.verifyEmail.mockResolvedValue({ data: { detail: 'Email verified successfully.' } });

    render(<VerifyEmailPage token="verify-token" onNavigate={vi.fn()} />);

    await waitFor(() => {
      expect(auth.verifyEmail).toHaveBeenCalledWith('verify-token');
      expect(screen.getByRole('heading', { name: /Welcome to Taali/i })).toBeInTheDocument();
      expect(screen.getByText('Email verified.')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Create your first role/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Connect Workable/i })).toBeInTheDocument();
    });
  });
});
