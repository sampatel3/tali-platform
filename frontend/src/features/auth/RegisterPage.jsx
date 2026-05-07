import React, { useState } from 'react';
import { AlertTriangle, CheckCircle, Mail } from 'lucide-react';

import { useAuth } from '../../context/AuthContext';
import { auth } from '../../shared/api';
import { AuthShell, AuthField } from './AuthShell';

export const RegisterPage = ({ onNavigate }) => {
  const { register } = useAuth();
  const [form, setForm] = useState({ email: '', password: '', full_name: '', organization_name: '' });
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState(false);
  const [resending, setResending] = useState(false);
  const [resent, setResent] = useState(false);

  const updateField = (field) => (e) => setForm((prev) => ({ ...prev, [field]: e.target.value }));

  const handleRegister = async () => {
    setError('');
    if (!form.email || !form.password || !form.full_name) {
      setError('Email, password, and full name are required');
      return;
    }
    if (form.password.length < 8) {
      setError('Password must be at least 8 characters long.');
      return;
    }
    setLoading(true);
    try {
      await register(form);
      setSuccess(true);
    } catch (err) {
      const detail = err.response?.data?.detail;
      const status = err.response?.status;
      let msg = 'Registration failed';

      const errorMessages = {
        REGISTER_USER_ALREADY_EXISTS: 'An account with this email already exists. Sign in instead or use a different email.',
        INVALID_PASSWORD: 'Password must be at least 8 characters long.',
      };
      if (typeof detail === 'string' && errorMessages[detail]) {
        msg = errorMessages[detail];
      } else if (typeof detail === 'string') {
        msg = detail;
      } else if (Array.isArray(detail) && detail.length > 0) {
        const parts = detail.map((e) => {
          const m = e.msg ?? e.message;
          if (typeof m === 'string') return m;
          if (e.type === 'string_too_short' && e.ctx?.min_length === 8 && e.loc?.includes?.('password')) {
            return 'Password must be at least 8 characters long.';
          }
          return m ? String(m) : JSON.stringify(e);
        });
        msg = parts.join('. ');
      } else if (status === 404 || status === 0) {
        msg = 'Cannot reach server. The app may be misconfigured - please try again later.';
      } else if (err.message && !err.message.includes('Network Error')) {
        msg = err.message;
      } else if (err.code === 'ERR_NETWORK' || err.message === 'Network Error') {
        msg = 'Cannot connect to server. Check your connection and try again.';
      }
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  const handleResend = async () => {
    setResending(true);
    try {
      await auth.resendVerification(form.email);
      setResent(true);
      setTimeout(() => setResent(false), 5000);
    } catch {
      // endpoint always returns 200
    } finally {
      setResending(false);
    }
  };

  if (success) {
    return (
      <AuthShell
        onNavigate={onNavigate}
        kicker="VERIFY EMAIL"
        title="Check your inbox"
        sub="We sent a verification link to your work email."
      >
        <div className="mc-auth-success-card" style={{ display: 'flex', alignItems: 'flex-start', gap: 12, padding: 16 }}>
          <div
            style={{
              width: 36,
              height: 36,
              borderRadius: 10,
              background: 'var(--green)',
              color: '#fff',
              display: 'grid',
              placeItems: 'center',
              flexShrink: 0,
            }}
          >
            <Mail size={16} strokeWidth={1.8} />
          </div>
          <div>
            <p style={{ margin: 0, fontSize: 15, fontWeight: 600, color: 'var(--ink)' }}>{form.email}</p>
            <p style={{ margin: '4px 0 0', fontSize: 13, lineHeight: 1.5, color: 'var(--ink-2)' }}>
              Click the link in the email to activate your account. The link expires in 24 hours.
            </p>
          </div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 18 }}>
          <button type="button" className="mc-auth-cta" onClick={() => onNavigate('login')}>
            Go to sign in →
          </button>
          <button
            type="button"
            className="mc-auth-cta mc-auth-cta-outline"
            onClick={handleResend}
            disabled={resending}
          >
            {resending ? 'Sending...' : resent ? (
              <>
                <CheckCircle size={14} /> Sent
              </>
            ) : 'Resend verification email'}
          </button>
        </div>
      </AuthShell>
    );
  }

  return (
    <AuthShell
      onNavigate={onNavigate}
      kicker="START FREE"
      title="Create your team"
      sub="14-day trial. No card. Bring your roles in or start with one of ours."
      topRight={(
        <span>
          Already with us?{' '}
          <button
            type="button"
            onClick={() => onNavigate('login')}
            style={{ background: 'none', border: 0, color: 'var(--purple)', fontWeight: 500, cursor: 'pointer', padding: 0, font: 'inherit' }}
          >
            Sign in
          </button>
        </span>
      )}
    >
      {error ? (
        <div className="mc-auth-error-card" role="alert">
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <AlertTriangle size={16} strokeWidth={1.8} style={{ color: 'var(--red)', flexShrink: 0 }} />
            <span style={{ fontSize: 13, color: 'var(--ink-2)' }}>{error}</span>
          </div>
        </div>
      ) : null}

      <AuthField
        label="Work email"
        name="email"
        type="email"
        autoComplete="email"
        placeholder="you@company.com"
        value={form.email}
        onChange={updateField('email')}
        helper="We'll use your domain to find teammates to invite."
      />
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
        <AuthField
          label="Full name"
          name="full_name"
          autoComplete="name"
          placeholder="Sam Patel"
          value={form.full_name}
          onChange={updateField('full_name')}
        />
        <AuthField
          label="Company"
          name="organization_name"
          autoComplete="organization"
          placeholder="Deeplight AI"
          value={form.organization_name}
          onChange={updateField('organization_name')}
        />
      </div>
      <AuthField
        label="Password"
        name="password"
        type="password"
        autoComplete="new-password"
        placeholder="••••••••"
        value={form.password}
        onChange={updateField('password')}
        helper="Strong · 12+ chars, 1 number"
      />

      <div style={{ display: 'flex', gap: 4, margin: '-8px 0 14px' }}>
        {[1, 2, 3, 4, 5].map((step) => (
          <span
            key={step}
            style={{
              flex: 1,
              height: 3,
              borderRadius: 2,
              background: step <= Math.min(4, Math.floor(form.password.length / 3)) ? 'var(--purple)' : 'var(--line)',
            }}
          />
        ))}
      </div>

      <button
        type="button"
        className="mc-auth-cta"
        onClick={handleRegister}
        disabled={loading}
        onKeyDown={(e) => e.key === 'Enter' && handleRegister()}
      >
        {loading ? 'Creating account...' : 'Create account →'}
      </button>

      <p style={{ marginTop: 16, fontSize: 12, lineHeight: 1.5, color: 'var(--mute)' }}>
        By creating an account you agree to our Terms and Privacy. We never train models on your candidate data.
      </p>
    </AuthShell>
  );
};
