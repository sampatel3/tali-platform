import React, { useState } from 'react';
import { AlertTriangle, CheckCircle, Mail } from 'lucide-react';

import { useAuth } from '../../context/AuthContext';
import { auth } from '../../shared/api';
import { SignInLayout, AuthCard } from './AuthLayout';

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

  if (err?.code === 'ERR_NETWORK' || message === 'Network Error') {
    return 'Unable to reach the Taali API. Please refresh and try again.';
  }

  if (err?.response?.status === 404 || err?.response?.status === 502 || err?.response?.status === 503) {
    return 'Unable to reach the Taali API. Please try again in a moment.';
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
  const [showSsoInput, setShowSsoInput] = useState(false);
  const [ssoEmail, setSsoEmail] = useState('');
  const [ssoChecking, setSsoChecking] = useState(false);
  const [ssoMessage, setSsoMessage] = useState('');

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

  const handleSsoCheck = async () => {
    const targetEmail = (ssoEmail || email || '').trim().toLowerCase();
    if (!targetEmail) {
      setSsoMessage('Enter your work email to continue with SSO.');
      return;
    }
    setSsoChecking(true);
    setSsoMessage('');
    try {
      const res = await auth.ssoCheck(targetEmail);
      const payload = res?.data || {};
      if (payload?.sso_enabled && payload?.redirect_url) {
        window.location.href = payload.redirect_url;
        return;
      }
      setSsoMessage(payload?.message || 'No SSO configured for this domain. Use email/password instead.');
    } catch (err) {
      setSsoMessage(err?.response?.data?.detail || 'Unable to check SSO right now. Please try again.');
    } finally {
      setSsoChecking(false);
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
    <SignInLayout onNavigate={onNavigate}>
      <AuthCard kicker="01 · SIGN IN" title={<>Sign in<em>.</em></>} subtitle="Access your TAALI dashboard.">
        {error ? (
          <div className="mb-5 rounded-[14px] border border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] p-4">
            <div className="flex items-start gap-3">
              <AlertTriangle size={18} className="mt-0.5 shrink-0 text-[var(--taali-danger)]" />
              <div>
                <p className="text-sm font-semibold text-[var(--taali-danger)]">Sign-in failed</p>
                <p className="mt-1 text-sm text-[var(--ink)]">{error}</p>
              </div>
            </div>
            {needsVerification ? (
              <button
                type="button"
                className="btn btn-outline mt-3 w-full justify-center"
                onClick={handleResendVerification}
                disabled={resending}
              >
                {resending ? 'Sending...' : resent ? (
                  <>
                    <CheckCircle size={14} />
                    Verification email sent
                  </>
                ) : (
                  <>
                    <Mail size={14} />
                    Resend verification email
                  </>
                )}
              </button>
            ) : null}
          </div>
        ) : null}

        <div className="space-y-4">
          <label className="field">
            <span className="k">Work email</span>
            <input
              type="email"
              placeholder="you@company.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </label>
          <label className="field">
            <span className="k">Password</span>
            <input
              type="password"
              placeholder="••••••••"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleLogin()}
            />
          </label>
        </div>

        <div className="mt-6">
          <button type="button" className="btn btn-purple w-full justify-center py-[13px] text-[14.5px]" onClick={handleLogin} disabled={loading}>
            {loading ? 'Signing in...' : <>Sign in <span className="arrow">→</span></>}
          </button>
        </div>

        <div className="my-5 flex items-center gap-3 font-[var(--font-mono)] text-[11.5px] uppercase tracking-[0.1em] text-[var(--mute-2)]">
          <div className="h-px flex-1 bg-[var(--line)]" />
          <span>or</span>
          <div className="h-px flex-1 bg-[var(--line)]" />
        </div>

        <button
          type="button"
          className="flex w-full items-center justify-center gap-2 rounded-[10px] border border-[var(--line)] bg-[var(--bg-2)] px-4 py-3 text-sm font-medium text-[var(--ink)] transition-colors hover:border-[var(--ink)]"
          onClick={() => {
            setShowSsoInput((prev) => !prev);
            setSsoMessage('');
          }}
        >
          Sign in with SSO
        </button>

        {showSsoInput ? (
          <div className="mt-3 space-y-2">
            <input
              type="email"
              className="w-full rounded-[10px] border border-[var(--line)] bg-[var(--bg-2)] px-4 py-3 text-sm"
              placeholder="you@company.com"
              value={ssoEmail}
              onChange={(event) => setSsoEmail(event.target.value)}
            />
            <button
              type="button"
              className="btn btn-outline w-full justify-center"
              onClick={handleSsoCheck}
              disabled={ssoChecking}
            >
              {ssoChecking ? 'Checking SSO...' : 'Continue to SSO'}
            </button>
            {ssoMessage ? <p className="text-xs text-[var(--mute)]">{ssoMessage}</p> : null}
          </div>
        ) : null}

        <div className="mt-6 text-center text-[13px] text-[var(--mute)]">
          <button type="button" className="text-[var(--purple)] hover:underline" onClick={() => onNavigate('forgot-password')}>
            Forgot password?
          </button>
          <span> · </span>
          No account?{' '}
          <button type="button" className="font-medium text-[var(--purple)] hover:underline" onClick={() => onNavigate('register')}>
            Request access
          </button>
        </div>
      </AuthCard>
    </SignInLayout>
  );
};
