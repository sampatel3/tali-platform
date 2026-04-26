import React, { useState } from 'react';
import { AlertTriangle, CheckCircle, Mail } from 'lucide-react';

import { useAuth } from '../../context/AuthContext';
import { auth } from '../../shared/api';
import { FlowLayout, AuthCard } from './AuthLayout';

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

  return (
    <FlowLayout onNavigate={onNavigate} activePane="register">
      {success ? (
        <AuthCard
          kicker="VERIFY EMAIL"
          title={<>Check your inbox<em>.</em></>}
          subtitle="We sent a verification link to your work email."
          widthClassName="max-w-[560px]"
        >
          <div className="mb-6 flex items-start gap-4 rounded-[14px] border border-[color-mix(in_oklab,var(--green)_30%,var(--line))] bg-[color-mix(in_oklab,var(--green)_10%,var(--bg-2))] p-5">
            <div className="grid h-9 w-9 place-items-center rounded-[10px] bg-[var(--green)] text-[var(--taali-inverse-text)]">
              <Mail size={18} />
            </div>
            <div>
              <p className="text-[15px] font-semibold text-[var(--ink)]">{form.email}</p>
              <p className="mt-1 text-[13px] leading-6 text-[var(--ink-2)]">Click the link in the email to activate your account. The link expires in 24 hours.</p>
            </div>
          </div>
          <div className="flex flex-col gap-3">
            <button type="button" className="btn btn-purple btn-lg w-full justify-center" onClick={() => onNavigate('login')}>
              Go to sign in <span className="arrow">→</span>
            </button>
            <button type="button" className="btn btn-outline btn-lg w-full justify-center" onClick={handleResend} disabled={resending}>
              {resending ? 'Sending...' : resent ? <><CheckCircle size={16} /> Sent</> : 'Resend verification email'}
            </button>
          </div>
        </AuthCard>
      ) : (
        <AuthCard
          kicker="CREATE ACCOUNT"
          title={<>Start hiring with <em>evidence</em>.</>}
          subtitle="90 seconds to set up. No credit card. Your first 5 assessments are free."
          widthClassName="max-w-[560px]"
        >
          {error ? (
            <div className="mb-5 flex items-center gap-2 rounded-[14px] border border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] p-4">
              <AlertTriangle size={18} className="shrink-0 text-[var(--taali-danger)]" />
              <span className="text-sm text-[var(--ink)]">{error}</span>
            </div>
          ) : null}

          <div className="grid gap-4 md:grid-cols-2">
            <label className="field md:col-span-2">
              <span className="k">Work email</span>
              <input type="email" placeholder="you@company.com" value={form.email} onChange={updateField('email')} />
              <span className="mt-1 block text-[11.5px] text-[var(--mute)]">We&apos;ll use your domain to find teammates to invite.</span>
            </label>
            <label className="field">
              <span className="k">Full name</span>
              <input type="text" placeholder="Sam Patel" value={form.full_name} onChange={updateField('full_name')} />
            </label>
            <label className="field">
              <span className="k">Company</span>
              <input type="text" placeholder="Deeplight AI" value={form.organization_name} onChange={updateField('organization_name')} />
            </label>
            <label className="field md:col-span-2">
              <span className="k">Password</span>
              <input
                type="password"
                placeholder="••••••••"
                value={form.password}
                onChange={updateField('password')}
                onKeyDown={(e) => e.key === 'Enter' && handleRegister()}
              />
              <div className="mt-2 flex gap-1">
                {[1, 2, 3, 4, 5].map((step) => (
                  <i
                    key={step}
                    className="block h-1 flex-1 rounded-[2px]"
                    style={{ background: step <= Math.min(4, Math.floor(form.password.length / 3)) ? 'var(--purple)' : 'var(--line)' }}
                  />
                ))}
              </div>
              <span className="mt-1 block text-[11.5px] text-[var(--mute)]">Strong · 12+ chars, 1 number</span>
            </label>
          </div>

          <button type="button" className="btn btn-purple btn-lg mt-5 w-full justify-center" onClick={handleRegister} disabled={loading}>
            {loading ? 'Creating account...' : <>Create account <span className="arrow">→</span></>}
          </button>

          <p className="mt-4 text-[12.5px] leading-6 text-[var(--mute)]">
            By creating an account you agree to our Terms and Privacy. We never train models on your candidate data.
          </p>

          <div className="mt-5 text-center text-[13.5px] text-[var(--mute)]">
            Already have an account?{' '}
            <button type="button" className="font-medium text-[var(--purple)] hover:underline" onClick={() => onNavigate('login')}>
              Sign in
            </button>
          </div>
        </AuthCard>
      )}
    </FlowLayout>
  );
};
