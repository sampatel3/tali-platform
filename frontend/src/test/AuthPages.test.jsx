import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';

// Mock the API module
vi.mock('../lib/api.js', () => ({
  auth: {
    login: vi.fn(),
    register: vi.fn(),
    me: vi.fn(),
    verifyEmail: vi.fn(),
    resendVerification: vi.fn(),
    forgotPassword: vi.fn(),
    resetPassword: vi.fn(),
  },
  assessments: {
    list: vi.fn().mockResolvedValue({ data: { items: [], total: 0 } }),
    get: vi.fn(),
    create: vi.fn(),
    remove: vi.fn(),
    resend: vi.fn(),
    downloadReport: vi.fn(),
    addNote: vi.fn(),
    uploadCv: vi.fn(),
    postToWorkable: vi.fn(),
  },
  billing: { usage: vi.fn() },
  organizations: { get: vi.fn(), update: vi.fn() },
  analytics: { get: vi.fn().mockResolvedValue({ data: {} }) },
  tasks: { list: vi.fn().mockResolvedValue({ data: [] }), get: vi.fn(), create: vi.fn(), update: vi.fn(), delete: vi.fn(), generate: vi.fn() },
  candidates: { list: vi.fn().mockResolvedValue({ data: { items: [] } }), get: vi.fn(), create: vi.fn(), createWithCv: vi.fn(), update: vi.fn(), remove: vi.fn(), uploadCv: vi.fn(), uploadJobSpec: vi.fn() },
  team: { list: vi.fn(), invite: vi.fn() },
  default: {
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn() },
    },
    get: vi.fn(),
    post: vi.fn(),
    create: vi.fn().mockReturnValue({
      interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
    }),
  },
}));

// Mock recharts to avoid canvas issues
vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }) => <div>{children}</div>,
  RadarChart: () => <div data-testid="radar-chart" />,
  PolarGrid: () => <div />,
  PolarAngleAxis: () => <div />,
  PolarRadiusAxis: () => <div />,
  Radar: () => <div />,
  LineChart: () => <div data-testid="line-chart" />,
  Line: () => <div />,
  XAxis: () => <div />,
  YAxis: () => <div />,
  CartesianGrid: () => <div />,
  Tooltip: () => <div />,
}));

// Mock monaco editor
vi.mock('@monaco-editor/react', () => ({
  default: () => <div data-testid="code-editor" />,
}));

import { auth } from '../lib/api.js';
import App from '../App';
import { AuthProvider } from '../context/AuthContext';

const renderApp = () => {
  return render(
    <AuthProvider>
      <App />
    </AuthProvider>
  );
};

describe('AuthPages', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    window.location.hash = '';
    // Ensure auth.me rejects by default so we stay unauthenticated
    auth.me.mockRejectedValue(new Error('Not authenticated'));
  });

  afterEach(() => {
    window.location.hash = '';
  });

  // ============================================================
  // LOGIN PAGE
  // ============================================================
  describe('LoginPage', () => {
    const navigateToLogin = async () => {
      renderApp();
      // On landing page, click "Sign In" button
      const signInButtons = screen.getAllByText('Sign In');
      fireEvent.click(signInButtons[0]);
    };

    it('renders email and password fields', async () => {
      await navigateToLogin();
      await waitFor(() => {
        expect(screen.getByPlaceholderText('you@company.com')).toBeInTheDocument();
        expect(screen.getByPlaceholderText('••••••••')).toBeInTheDocument();
      });
    });

    it('renders Sign In heading', async () => {
      await navigateToLogin();
      await waitFor(() => {
        expect(screen.getByText('Sign In', { selector: 'h2' })).toBeInTheDocument();
      });
    });

    it('renders forgot password link', async () => {
      await navigateToLogin();
      await waitFor(() => {
        expect(screen.getByText('Forgot password?')).toBeInTheDocument();
      });
    });

    it('renders register link', async () => {
      await navigateToLogin();
      await waitFor(() => {
        expect(screen.getByText('Register')).toBeInTheDocument();
      });
    });

    it('calls auth.login when form submitted with valid data', async () => {
      auth.login.mockResolvedValue({ data: { access_token: 'tok123' } });
      auth.me.mockResolvedValue({ data: { id: 1, email: 'test@test.com', full_name: 'Test User' } });

      await navigateToLogin();

      await waitFor(() => {
        expect(screen.getByPlaceholderText('you@company.com')).toBeInTheDocument();
      });

      fireEvent.change(screen.getByPlaceholderText('you@company.com'), {
        target: { value: 'test@test.com' },
      });
      fireEvent.change(screen.getByPlaceholderText('••••••••'), {
        target: { value: 'password123' },
      });

      const signInButton = screen.getByRole('button', { name: 'Sign In' });
      fireEvent.click(signInButton);

      await waitFor(() => {
        // The AuthContext.login calls auth.login internally
        expect(auth.login).toHaveBeenCalledWith('test@test.com', 'password123');
      });
    });

    it('shows loading state during submission', async () => {
      // Make login hang indefinitely
      auth.login.mockReturnValue(new Promise(() => {}));

      await navigateToLogin();

      await waitFor(() => {
        expect(screen.getByPlaceholderText('you@company.com')).toBeInTheDocument();
      });

      fireEvent.change(screen.getByPlaceholderText('you@company.com'), {
        target: { value: 'test@test.com' },
      });
      fireEvent.change(screen.getByPlaceholderText('••••••••'), {
        target: { value: 'password123' },
      });

      const signInButton = screen.getByRole('button', { name: 'Sign In' });
      fireEvent.click(signInButton);

      await waitFor(() => {
        expect(screen.getByText('Signing in...')).toBeInTheDocument();
      });
    });

    it('shows error message on API failure', async () => {
      auth.login.mockRejectedValue({
        response: { status: 401, data: { detail: 'Invalid credentials' } },
      });

      await navigateToLogin();

      await waitFor(() => {
        expect(screen.getByPlaceholderText('you@company.com')).toBeInTheDocument();
      });

      fireEvent.change(screen.getByPlaceholderText('you@company.com'), {
        target: { value: 'wrong@test.com' },
      });
      fireEvent.change(screen.getByPlaceholderText('••••••••'), {
        target: { value: 'wrong' },
      });

      const signInButton = screen.getByRole('button', { name: 'Sign In' });
      fireEvent.click(signInButton);

      await waitFor(() => {
        expect(screen.getByText('Invalid credentials')).toBeInTheDocument();
      });
    });

    it('shows "needs verification" message on 403 with verify detail', async () => {
      auth.login.mockRejectedValue({
        response: { status: 403, data: { detail: 'Please verify your email before logging in' } },
      });

      await navigateToLogin();

      await waitFor(() => {
        expect(screen.getByPlaceholderText('you@company.com')).toBeInTheDocument();
      });

      fireEvent.change(screen.getByPlaceholderText('you@company.com'), {
        target: { value: 'unverified@test.com' },
      });
      fireEvent.change(screen.getByPlaceholderText('••••••••'), {
        target: { value: 'password123' },
      });

      const signInButton = screen.getByRole('button', { name: 'Sign In' });
      fireEvent.click(signInButton);

      await waitFor(() => {
        expect(screen.getByText('Resend verification email')).toBeInTheDocument();
      });
    });

    it('navigates to register page when Register is clicked', async () => {
      await navigateToLogin();

      await waitFor(() => {
        expect(screen.getByText('Register')).toBeInTheDocument();
      });

      fireEvent.click(screen.getByText('Register'));

      await waitFor(() => {
        expect(screen.getByText('Create Account', { selector: 'h2' })).toBeInTheDocument();
      });
    });
  });

  // ============================================================
  // REGISTER PAGE
  // ============================================================
  describe('RegisterPage', () => {
    const navigateToRegister = async () => {
      renderApp();
      // From landing, click Sign In then Register
      const signInButtons = screen.getAllByText('Sign In');
      fireEvent.click(signInButtons[0]);
      await waitFor(() => {
        expect(screen.getByText('Register')).toBeInTheDocument();
      });
      fireEvent.click(screen.getByText('Register'));
    };

    it('renders all form fields', async () => {
      await navigateToRegister();
      await waitFor(() => {
        expect(screen.getByPlaceholderText('Jane Smith')).toBeInTheDocument();
        expect(screen.getByPlaceholderText('you@company.com')).toBeInTheDocument();
        expect(screen.getByPlaceholderText('••••••••')).toBeInTheDocument();
        expect(screen.getByPlaceholderText('Acme Corp')).toBeInTheDocument();
      });
    });

    it('renders Create Account heading', async () => {
      await navigateToRegister();
      await waitFor(() => {
        expect(screen.getByText('Create Account', { selector: 'h2' })).toBeInTheDocument();
      });
    });

    it('shows error for empty required fields', async () => {
      await navigateToRegister();
      await waitFor(() => {
        expect(screen.getByText('Create Account', { selector: 'h2' })).toBeInTheDocument();
      });

      const createButton = screen.getByRole('button', { name: 'Create Account' });
      fireEvent.click(createButton);

      await waitFor(() => {
        expect(screen.getByText('Email, password, and full name are required')).toBeInTheDocument();
      });
    });

    it('shows error for short password', async () => {
      await navigateToRegister();

      await waitFor(() => {
        expect(screen.getByPlaceholderText('Jane Smith')).toBeInTheDocument();
      });

      fireEvent.change(screen.getByPlaceholderText('Jane Smith'), { target: { value: 'Test User' } });
      fireEvent.change(screen.getByPlaceholderText('you@company.com'), { target: { value: 'test@test.com' } });
      fireEvent.change(screen.getByPlaceholderText('••••••••'), { target: { value: 'short' } });

      const createButton = screen.getByRole('button', { name: 'Create Account' });
      fireEvent.click(createButton);

      await waitFor(() => {
        expect(screen.getByText('Password must be at least 8 characters')).toBeInTheDocument();
      });
    });

    it('shows success message after successful registration', async () => {
      auth.register.mockResolvedValue({ data: { id: 1, email: 'test@test.com' } });

      await navigateToRegister();

      await waitFor(() => {
        expect(screen.getByPlaceholderText('Jane Smith')).toBeInTheDocument();
      });

      fireEvent.change(screen.getByPlaceholderText('Jane Smith'), { target: { value: 'Test User' } });
      fireEvent.change(screen.getByPlaceholderText('you@company.com'), { target: { value: 'test@test.com' } });
      fireEvent.change(screen.getByPlaceholderText('••••••••'), { target: { value: 'longpassword123' } });
      fireEvent.change(screen.getByPlaceholderText('Acme Corp'), { target: { value: 'Test Corp' } });

      const createButton = screen.getByRole('button', { name: 'Create Account' });
      fireEvent.click(createButton);

      await waitFor(() => {
        expect(screen.getByText('Check your email')).toBeInTheDocument();
      });
    });

    it('shows specific error from 422 responses with validation error array', async () => {
      auth.register.mockRejectedValue({
        response: {
          status: 422,
          data: {
            detail: [
              { msg: 'value is not a valid email address' },
              { msg: 'ensure this value has at least 8 characters' },
            ],
          },
        },
      });

      await navigateToRegister();

      await waitFor(() => {
        expect(screen.getByPlaceholderText('Jane Smith')).toBeInTheDocument();
      });

      fireEvent.change(screen.getByPlaceholderText('Jane Smith'), { target: { value: 'Test User' } });
      fireEvent.change(screen.getByPlaceholderText('you@company.com'), { target: { value: 'test@test.com' } });
      fireEvent.change(screen.getByPlaceholderText('••••••••'), { target: { value: 'longpassword123' } });

      const createButton = screen.getByRole('button', { name: 'Create Account' });
      fireEvent.click(createButton);

      await waitFor(() => {
        expect(screen.getByText(/value is not a valid email address/)).toBeInTheDocument();
      });
    });

    it('shows loading state during registration', async () => {
      auth.register.mockReturnValue(new Promise(() => {}));

      await navigateToRegister();

      await waitFor(() => {
        expect(screen.getByPlaceholderText('Jane Smith')).toBeInTheDocument();
      });

      fireEvent.change(screen.getByPlaceholderText('Jane Smith'), { target: { value: 'Test User' } });
      fireEvent.change(screen.getByPlaceholderText('you@company.com'), { target: { value: 'test@test.com' } });
      fireEvent.change(screen.getByPlaceholderText('••••••••'), { target: { value: 'longpassword123' } });

      const createButton = screen.getByRole('button', { name: 'Create Account' });
      fireEvent.click(createButton);

      await waitFor(() => {
        expect(screen.getByText('Creating account...')).toBeInTheDocument();
      });
    });

    it('shows Sign In link to navigate back to login', async () => {
      await navigateToRegister();

      await waitFor(() => {
        expect(screen.getByText('Already have an account?')).toBeInTheDocument();
      });

      // Clicking "Sign In" goes back to login
      const signInLink = screen.getByText('Sign In', { selector: 'button' });
      fireEvent.click(signInLink);

      await waitFor(() => {
        expect(screen.getByText('Sign In', { selector: 'h2' })).toBeInTheDocument();
      });
    });
  });

  // ============================================================
  // FORGOT PASSWORD PAGE
  // ============================================================
  describe('ForgotPasswordPage', () => {
    const navigateToForgotPassword = async () => {
      renderApp();
      // Landing -> Login -> Forgot password
      const signInButtons = screen.getAllByText('Sign In');
      fireEvent.click(signInButtons[0]);
      await waitFor(() => {
        expect(screen.getByText('Forgot password?')).toBeInTheDocument();
      });
      fireEvent.click(screen.getByText('Forgot password?'));
    };

    it('renders email input and heading', async () => {
      await navigateToForgotPassword();
      await waitFor(() => {
        expect(screen.getByText('Forgot password?', { selector: 'h2' })).toBeInTheDocument();
        expect(screen.getByPlaceholderText('you@company.com')).toBeInTheDocument();
      });
    });

    it('renders send reset link button', async () => {
      await navigateToForgotPassword();
      await waitFor(() => {
        expect(screen.getByRole('button', { name: 'Send reset link' })).toBeInTheDocument();
      });
    });

    it('shows success after submit', async () => {
      // ForgotPasswordPage uses dynamic import, mock the module resolution
      const apiModule = await import('../lib/api.js');
      apiModule.auth.forgotPassword.mockResolvedValue({ data: {} });

      await navigateToForgotPassword();

      await waitFor(() => {
        expect(screen.getByPlaceholderText('you@company.com')).toBeInTheDocument();
      });

      fireEvent.change(screen.getByPlaceholderText('you@company.com'), {
        target: { value: 'test@test.com' },
      });

      const submitButton = screen.getByRole('button', { name: 'Send reset link' });
      fireEvent.click(submitButton);

      await waitFor(() => {
        expect(screen.getByText('Check your email')).toBeInTheDocument();
        expect(screen.getByText(/If an account exists for that email/)).toBeInTheDocument();
      });
    });

    it('shows back to sign in link', async () => {
      await navigateToForgotPassword();
      await waitFor(() => {
        expect(screen.getByText('Back to Sign In')).toBeInTheDocument();
      });
    });
  });

  // ============================================================
  // RESET PASSWORD PAGE
  // ============================================================
  describe('ResetPasswordPage', () => {
    it('shows invalid link message when no token is present', async () => {
      window.location.hash = '#/reset-password';
      renderApp();

      await waitFor(() => {
        expect(screen.getByText('Invalid link')).toBeInTheDocument();
      });
    });

    it('renders password fields when token is present', async () => {
      window.location.hash = '#/reset-password?token=valid-token-123';
      renderApp();

      await waitFor(() => {
        expect(screen.getByText('Set new password')).toBeInTheDocument();
        const passwordFields = screen.getAllByPlaceholderText('••••••••');
        expect(passwordFields.length).toBe(2);
      });
    });

    it('validates password confirmation mismatch', async () => {
      window.location.hash = '#/reset-password?token=valid-token-123';
      renderApp();

      await waitFor(() => {
        expect(screen.getByText('Set new password')).toBeInTheDocument();
      });

      const passwordInputs = screen.getAllByPlaceholderText('••••••••');
      fireEvent.change(passwordInputs[0], { target: { value: 'newpassword123' } });
      fireEvent.change(passwordInputs[1], { target: { value: 'differentpassword' } });

      const resetButton = screen.getByRole('button', { name: 'Reset password' });
      fireEvent.click(resetButton);

      await waitFor(() => {
        expect(screen.getByText('Passwords do not match')).toBeInTheDocument();
      });
    });

    it('validates password minimum length', async () => {
      window.location.hash = '#/reset-password?token=valid-token-123';
      renderApp();

      await waitFor(() => {
        expect(screen.getByText('Set new password')).toBeInTheDocument();
      });

      const passwordInputs = screen.getAllByPlaceholderText('••••••••');
      fireEvent.change(passwordInputs[0], { target: { value: 'short' } });
      fireEvent.change(passwordInputs[1], { target: { value: 'short' } });

      const resetButton = screen.getByRole('button', { name: 'Reset password' });
      fireEvent.click(resetButton);

      await waitFor(() => {
        expect(screen.getByText('Password must be at least 8 characters')).toBeInTheDocument();
      });
    });
  });
});
