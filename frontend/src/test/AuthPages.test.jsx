import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AuthProvider } from '../context/AuthContext';
import {
  AcceptInvitePage,
  ForgotPasswordPage,
  LoginPage,
  RegisterPage,
  ResetPasswordPage,
  VerifyEmailPage,
} from '../features/auth';
import { PasswordStrength } from '../features/auth/PasswordStrength';
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
    acceptInvite: vi.fn(),
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

  it('submits sign-in when Enter is pressed inside a field (form submit)', async () => {
    auth.login.mockResolvedValue({ data: { access_token: 'tok_123' } });
    auth.me.mockResolvedValue({ data: { id: 1, email: 'sam@taali.ai', full_name: 'Sam Patel' } });

    const { container } = renderWithAuth(<LoginPage onNavigate={vi.fn()} />);

    fireEvent.change(screen.getByPlaceholderText('you@company.com'), {
      target: { value: 'sam@taali.ai' },
    });
    fireEvent.change(screen.getByPlaceholderText('••••••••'), {
      target: { value: 'password123' },
    });
    // Submitting the form (what Enter-in-a-field does) must sign in — the two
    // primary auth pages previously had no <form>, so Enter did nothing.
    fireEvent.submit(container.querySelector('form'));

    await waitFor(() => {
      expect(auth.login).toHaveBeenCalledWith('sam@taali.ai', 'password123');
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

  it('renders the accept-invite page and prompts for a password', () => {
    renderWithAuth(<AcceptInvitePage token="invite-token" onNavigate={vi.fn()} />);

    expect(screen.getByRole('heading', { name: /Set a password to get started/i })).toBeInTheDocument();
    expect(screen.getByText(/You've been invited to join your team on Taali/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Set password & continue/i })).toBeInTheDocument();
  });

  it('shows a friendly missing-token state with a sign-in link', () => {
    renderWithAuth(<AcceptInvitePage token="" onNavigate={vi.fn()} />);

    expect(screen.getByRole('heading', { name: /Invite link missing/i })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Go to sign in/i })).toBeInTheDocument();
  });

  it('accepts the invite, stores the token, loads the profile, and lands on home', async () => {
    const onNavigate = vi.fn();
    auth.acceptInvite.mockResolvedValue({ data: { access_token: 'invite_tok', token_type: 'bearer' } });
    auth.me.mockResolvedValue({ data: { id: 5, email: 'newbie@taali.ai', full_name: 'New Bie' } });

    renderWithAuth(<AcceptInvitePage token="invite-token" onNavigate={onNavigate} />);

    fireEvent.change(screen.getAllByPlaceholderText('••••••••')[0], { target: { value: 'password123' } });
    fireEvent.change(screen.getAllByPlaceholderText('••••••••')[1], { target: { value: 'password123' } });
    fireEvent.click(screen.getByRole('button', { name: /Set password & continue/i }));

    await waitFor(() => {
      expect(auth.acceptInvite).toHaveBeenCalledWith('invite-token', 'password123');
      expect(auth.me).toHaveBeenCalled();
      expect(onNavigate).toHaveBeenCalledWith('home');
    });
    expect(localStorage.getItem('taali_access_token')).toBe('invite_tok');
  });

  it('shows the invalid-token error message when the invite is expired', async () => {
    auth.acceptInvite.mockRejectedValue({
      response: { status: 400, data: { detail: 'INVITE_TOKEN_INVALID' } },
    });

    renderWithAuth(<AcceptInvitePage token="stale-token" onNavigate={vi.fn()} />);

    fireEvent.change(screen.getAllByPlaceholderText('••••••••')[0], { target: { value: 'password123' } });
    fireEvent.change(screen.getAllByPlaceholderText('••••••••')[1], { target: { value: 'password123' } });
    fireEvent.click(screen.getByRole('button', { name: /Set password & continue/i }));

    await waitFor(() => {
      expect(screen.getByText(/This invite link is invalid or has expired/i)).toBeInTheDocument();
    });
  });

  it('offers a sign-in link when the invite was already accepted', async () => {
    auth.acceptInvite.mockRejectedValue({
      response: { status: 400, data: { detail: 'INVITE_ALREADY_ACCEPTED' } },
    });

    renderWithAuth(<AcceptInvitePage token="used-token" onNavigate={vi.fn()} />);

    fireEvent.change(screen.getAllByPlaceholderText('••••••••')[0], { target: { value: 'password123' } });
    fireEvent.change(screen.getAllByPlaceholderText('••••••••')[1], { target: { value: 'password123' } });
    fireEvent.click(screen.getByRole('button', { name: /Set password & continue/i }));

    await waitFor(() => {
      expect(screen.getByText(/This invite was already accepted/i)).toBeInTheDocument();
      expect(screen.getByRole('link', { name: /Go to sign in/i })).toBeInTheDocument();
    });
  });

  it('points SSO-enforced workspaces at the sign-in page', async () => {
    auth.acceptInvite.mockRejectedValue({
      response: { status: 400, data: { detail: 'INVITE_SSO_REQUIRED' } },
    });

    renderWithAuth(<AcceptInvitePage token="sso-token" onNavigate={vi.fn()} />);

    fireEvent.change(screen.getAllByPlaceholderText('••••••••')[0], { target: { value: 'password123' } });
    fireEvent.change(screen.getAllByPlaceholderText('••••••••')[1], { target: { value: 'password123' } });
    fireEvent.click(screen.getByRole('button', { name: /Set password & continue/i }));

    await waitFor(() => {
      expect(screen.getByText(/Your workspace requires single sign-on/i)).toBeInTheDocument();
      expect(screen.getByRole('link', { name: /Go to sign in/i })).toBeInTheDocument();
    });
  });

  it('PasswordStrength renders nothing when the password is empty', () => {
    const { container } = render(<PasswordStrength password="" />);
    expect(container.querySelector('.mc-auth-strength')).toBeNull();
  });

  it('PasswordStrength flags a too-common password', () => {
    render(<PasswordStrength password="password" />);
    expect(screen.getByText(/too common/i)).toBeInTheDocument();
  });

  it('PasswordStrength reports a strong password for a long varied string', () => {
    render(<PasswordStrength password="Tr0ub4dor-passphrase-xyz" />);
    expect(screen.getByText(/strong password/i)).toBeInTheDocument();
  });

  it('PasswordStrength warns when the email is inside the password', () => {
    render(<PasswordStrength password="samsmith-secret-99" email="samsmith@company.com" />);
    expect(screen.getByText(/email in your password/i)).toBeInTheDocument();
  });

  it('verifies email and lands on the redesigned success screen', async () => {
    auth.verifyEmail.mockResolvedValue({ data: { detail: 'Email verified successfully.' } });

    render(<VerifyEmailPage token="verify-token" onNavigate={vi.fn()} />);

    await waitFor(() => {
      expect(auth.verifyEmail).toHaveBeenCalledWith('verify-token');
      expect(screen.getByRole('heading', { name: /Welcome to Taali/i })).toBeInTheDocument();
      expect(screen.getByText('Email verified.')).toBeInTheDocument();
      expect(screen.getByRole('link', { name: /Create your first role/i })).toBeInTheDocument();
      expect(screen.getByRole('link', { name: /Connect Workable/i })).toBeInTheDocument();
    });
  });
});
