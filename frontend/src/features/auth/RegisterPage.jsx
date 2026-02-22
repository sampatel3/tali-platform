import React, { useState } from 'react';
import { AlertTriangle, CheckCircle, Mail } from 'lucide-react';

import { useAuth } from '../../context/AuthContext';
import { auth } from '../../shared/api';
import { BRAND } from '../../config/brand';
import { Logo } from '../../shared/ui/Branding';
import { Button, Input, Spinner } from '../../shared/ui/TaaliPrimitives';

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
    <div className="min-h-screen bg-[var(--taali-surface)] flex flex-col">
      <nav className="border-b-2 border-[var(--taali-border)] bg-[var(--taali-surface)]">
        <div className="max-w-7xl mx-auto px-6 py-4">
          <Logo onClick={() => onNavigate('landing')} />
        </div>
      </nav>
      <div className="flex-1 flex items-center justify-center p-6">
        <div className="w-full max-w-md">
          {success ? (
            <div className="border-2 border-[var(--taali-border)] p-8 text-center bg-[var(--taali-surface)]">
              <Mail size={48} className="mx-auto mb-4 text-[var(--taali-purple)]" />
              <h2 className="text-2xl font-bold mb-2">Check your email</h2>
              <p className="text-sm text-[var(--taali-muted)] mb-2">We sent a verification link to</p>
              <p className="font-mono text-sm font-bold mb-6">{form.email}</p>
              <p className="text-xs text-[var(--taali-muted)] mb-6">
                Click the link in the email to activate your account. The link expires in 24 hours.
              </p>
              <Button variant="primary" className="w-full mb-3" onClick={() => onNavigate('login')}>
                Go to Sign In
              </Button>
              <Button
                variant="secondary"
                className="w-full"
                onClick={handleResend}
                disabled={resending}
              >
                {resending ? <><Spinner size={16} /> Sending...</> : resent ? <><CheckCircle size={16} className="shrink-0 text-[var(--taali-purple)]" /> Sent!</> : 'Resend verification email'}
              </Button>
            </div>
          ) : (
            <div className="border-2 border-[var(--taali-border)] p-8 bg-[var(--taali-surface)]">
              <h2 className="text-3xl font-bold mb-2">Create Account</h2>
              <p className="text-sm text-[var(--taali-muted)] mb-8">Start using {BRAND.name} for your team</p>
              {error && (
                <div className="border-2 border-[var(--taali-danger)] bg-[var(--taali-danger-soft)] p-4 mb-6 flex items-center gap-2">
                  <AlertTriangle size={18} className="text-[var(--taali-danger)] flex-shrink-0" />
                  <span className="text-sm text-[var(--taali-text)]">{error}</span>
                </div>
              )}
              <div className="space-y-4">
                <div>
                  <label className="block font-mono text-sm mb-1">Full Name *</label>
                  <Input
                    type="text"
                    placeholder="Jane Smith"
                    value={form.full_name}
                    onChange={updateField('full_name')}
                  />
                </div>
                <div>
                  <label className="block font-mono text-sm mb-1">Email *</label>
                  <Input
                    type="email"
                    placeholder="you@company.com"
                    value={form.email}
                    onChange={updateField('email')}
                  />
                </div>
                <div>
                  <label className="block font-mono text-sm mb-1">Password *</label>
                  <Input
                    type="password"
                    placeholder="••••••••"
                    value={form.password}
                    onChange={updateField('password')}
                    onKeyDown={(e) => e.key === 'Enter' && handleRegister()}
                  />
                  <p className="font-mono text-xs text-[var(--taali-muted)] mt-1">Minimum 8 characters</p>
                </div>
                <div>
                  <label className="block font-mono text-sm mb-1">Organization Name</label>
                  <Input
                    type="text"
                    placeholder="Acme Corp"
                    value={form.organization_name}
                    onChange={updateField('organization_name')}
                  />
                </div>
                <Button
                  variant="primary"
                  className="w-full mt-4"
                  onClick={handleRegister}
                  disabled={loading}
                >
                  {loading ? <><Spinner size={18} /> Creating account...</> : 'Create Account'}
                </Button>
              </div>
              <div className="mt-6 text-center">
                <span className="text-sm text-[var(--taali-muted)]">Already have an account? </span>
                <button
                  className="text-sm font-bold hover:underline text-[var(--taali-purple)]"
                  onClick={() => onNavigate('login')}
                >
                  Sign In
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
