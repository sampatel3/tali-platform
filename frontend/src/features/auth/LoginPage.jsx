import React, { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { AlertTriangle, CheckCircle, Mail } from 'lucide-react';

import { useAuth } from '../../context/AuthContext';
import { auth } from '../../shared/api';
import { AuthShell, AuthField } from './AuthShell';

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

const resolveSafeNextPath = (rawValue) => {
  if (typeof rawValue !== 'string') return '';
  const nextPath = rawValue.trim();
  if (!nextPath.startsWith('/') || nextPath.startsWith('//') || nextPath.includes('://')) {
    return '';
  }
  return nextPath;
};

export const LoginPage = ({ onNavigate }) => {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
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
      const nextPath = resolveSafeNextPath(searchParams.get('next'));
      if (nextPath) {
        navigate(nextPath, { replace: true });
      } else {
        onNavigate('dashboard');
      }
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
    <AuthShell
      kicker="WELCOME BACK"
      title="Sign in to Taali"
      sub="Pick up where you left off. Your agent is waiting."
      topRight={(
        <span>
          New here?{' '}
          <button
            type="button"
            onClick={() => onNavigate('demo')}
            style={{ background: 'none', border: 0, color: 'var(--purple)', fontWeight: 500, cursor: 'pointer', padding: 0, font: 'inherit' }}
          >
            Book a demo
          </button>
        </span>
      )}
    >
      {error ? (
        <div className="mc-auth-error-card" role="alert">
          <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
            <AlertTriangle size={16} strokeWidth={1.8} style={{ color: 'var(--red)', flexShrink: 0, marginTop: 2 }} />
            <div style={{ flex: 1 }}>
              <div className="title">Sign-in failed</div>
              <div className="body">{error}</div>
              {needsVerification ? (
                <button
                  type="button"
                  className="mc-auth-cta mc-auth-cta-outline"
                  style={{ marginTop: 12, height: 36, fontSize: 13 }}
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
          </div>
        </div>
      ) : null}

      <AuthField
        label="Work email"
        name="email"
        type="email"
        autoComplete="email"
        placeholder="you@company.com"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
      />
      <AuthField
        label="Password"
        name="password"
        type="password"
        autoComplete="current-password"
        placeholder="••••••••"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
      />

      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 18 }}>
        <button
          type="button"
          onClick={() => onNavigate('forgot-password')}
          style={{ background: 'none', border: 0, color: 'var(--purple)', fontSize: 12.5, fontWeight: 500, cursor: 'pointer', padding: 0, font: 'inherit' }}
        >
          Forgot password?
        </button>
      </div>

      <button
        type="button"
        className="mc-auth-cta"
        onClick={handleLogin}
        disabled={loading}
        onKeyDown={(e) => e.key === 'Enter' && handleLogin()}
      >
        {loading ? 'Signing in...' : 'Sign in →'}
      </button>

      <div className="mc-auth-divider">
        <span>OR</span>
      </div>

      <button
        type="button"
        className="mc-auth-cta mc-auth-cta-outline"
        onClick={() => {
          setShowSsoInput((prev) => !prev);
          setSsoMessage('');
        }}
      >
        Sign in with SSO
      </button>

      {showSsoInput ? (
        <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
          <input
            type="email"
            className="mc-auth-input"
            placeholder="you@company.com"
            value={ssoEmail}
            onChange={(event) => setSsoEmail(event.target.value)}
          />
          <button
            type="button"
            className="mc-auth-cta mc-auth-cta-outline"
            onClick={handleSsoCheck}
            disabled={ssoChecking}
          >
            {ssoChecking ? 'Checking SSO...' : 'Continue to SSO'}
          </button>
          {ssoMessage ? (
            <p style={{ fontSize: 12, color: 'var(--mute)', margin: 0 }}>{ssoMessage}</p>
          ) : null}
        </div>
      ) : null}

      <div style={{ marginTop: 24, textAlign: 'center', fontSize: 13, color: 'var(--mute)' }}>
        No account?{' '}
        <button
          type="button"
          onClick={() => onNavigate('register')}
          style={{ background: 'none', border: 0, color: 'var(--purple)', fontWeight: 500, cursor: 'pointer', padding: 0, font: 'inherit' }}
        >
          Request access
        </button>
      </div>
    </AuthShell>
  );
};
