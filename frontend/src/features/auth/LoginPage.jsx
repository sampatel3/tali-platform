import React, { useState } from 'react';
import { AlertTriangle, CheckCircle, Mail } from 'lucide-react';

import { useAuth } from '../../context/AuthContext';
import { auth } from '../../shared/api';
import { BRAND } from '../../config/brand';
import { Logo } from '../../shared/ui/Branding';
import { Button, Input, Spinner } from '../../shared/ui/TaaliPrimitives';

const LOGIN_ERROR_MESSAGES = {
  LOGIN_BAD_CREDENTIALS: 'Incorrect email or password. Please try again.',
  INVALID_CREDENTIALS: 'Incorrect email or password. Please try again.',
};

const getLoginErrorMessage = (err) => {
  const detail = err?.response?.data?.detail;
  const message = err?.message;

  if (typeof detail === 'string') {
    const normalizedDetail = detail.trim();
    const mappedMessage = LOGIN_ERROR_MESSAGES[normalizedDetail.toUpperCase()];
    return mappedMessage || normalizedDetail;
  }

  if (Array.isArray(detail) && detail.length > 0) {
    const joined = detail
      .map((item) => (typeof item?.msg === 'string' ? item.msg : String(item)))
      .join(' · ')
      .trim();
    if (joined) return joined;
  }

  if (typeof message === 'string' && message.trim()) {
    const normalizedMessage = message.trim();
    const mappedMessage = LOGIN_ERROR_MESSAGES[normalizedMessage.toUpperCase()];
    return mappedMessage || normalizedMessage;
  }

  return 'Unable to sign in. Please try again.';
};

export const LoginPage = ({ onNavigate }) => {
  const { login } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [needsVerification, setNeedsVerification] = useState(false);
  const [resending, setResending] = useState(false);
  const [resent, setResent] = useState(false);

  const handleLogin = async () => {
    setError('');
    setNeedsVerification(false);
    setLoading(true);
    try {
      await login(email, password);
      onNavigate('dashboard');
    } catch (err) {
      const status = err.response?.status;
      const rawDetail = err.response?.data?.detail;
      if (status === 403 && typeof rawDetail === 'string' && rawDetail.toLowerCase().includes('verify')) {
        setNeedsVerification(true);
      }
      setError(getLoginErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  const handleResendVerification = async () => {
    if (!email) return;
    setResending(true);
    try {
      await auth.resendVerification(email);
      setResent(true);
      setTimeout(() => setResent(false), 5000);
    } catch {
      // endpoint always returns 200
    } finally {
      setResending(false);
    }
  };

  return (
    <div className="min-h-screen bg-[var(--taali-surface)] flex flex-col">
      <nav className="border-b-2 border-[var(--taali-border)] bg-[var(--taali-surface)]">
        <div className="max-w-7xl mx-auto px-6 py-4">
          <Logo onClick={() => onNavigate('landing')} />
        </div>
      </nav>
      <div className="flex-1 flex items-center justify-center p-6">
        <div className="w-full max-w-md">
          {error && (
            <div className="mb-6 border-2 border-[var(--taali-danger)] bg-[var(--taali-danger-soft)] p-4">
              <div className="flex items-start gap-3">
                <AlertTriangle size={18} className="text-[var(--taali-danger)] mt-0.5 flex-shrink-0" />
                <div>
                  <p className="text-sm font-semibold text-[var(--taali-danger)]">Sign-in failed</p>
                  <p className="text-sm text-[var(--taali-text)]">{error}</p>
                </div>
              </div>
              {needsVerification && (
                <Button
                  variant="danger"
                  className="mt-3 w-full"
                  onClick={handleResendVerification}
                  disabled={resending}
                >
                  {resending ? <><Spinner size={14} /> Sending...</> : resent ? <><CheckCircle size={14} /> Verification email sent!</> : <><Mail size={14} /> Resend verification email</>}
                </Button>
              )}
            </div>
          )}
          <div className="border-2 border-[var(--taali-border)] p-8 bg-[var(--taali-surface)]">
            <h2 className="text-3xl font-bold mb-2">Sign In</h2>
            <p className="text-sm text-[var(--taali-muted)] mb-8">Access your {BRAND.name} dashboard</p>
            <div className="space-y-4">
              <div>
                <label className="block font-mono text-sm mb-1">Email</label>
                <Input
                  type="email"
                  placeholder="you@company.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </div>
              <div>
                <label className="block font-mono text-sm mb-1">Password</label>
                <Input
                  type="password"
                  placeholder="••••••••"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleLogin()}
                />
              </div>
              <Button
                variant="primary"
                className="w-full mt-4"
                onClick={handleLogin}
                disabled={loading}
              >
                {loading ? <><Spinner size={18} /> Signing in...</> : 'Sign In'}
              </Button>
            </div>
            <div className="mt-6 text-center space-y-2">
              <button
                type="button"
                className="text-sm hover:underline text-[var(--taali-purple)]"
                onClick={() => onNavigate('forgot-password')}
              >
                Forgot password?
              </button>
              <div>
                <span className="text-sm text-[var(--taali-muted)]">No account? </span>
                <button
                  className="text-sm font-bold hover:underline text-[var(--taali-purple)]"
                  onClick={() => onNavigate('register')}
                >
                  Register
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
