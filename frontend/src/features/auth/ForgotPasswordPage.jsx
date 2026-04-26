import React, { useState } from 'react';
import { AlertTriangle, CheckCircle } from 'lucide-react';

import { auth } from '../../shared/api';
import { FlowLayout, AuthCard } from './AuthLayout';

export const ForgotPasswordPage = ({ onNavigate }) => {
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    if (!email.trim()) {
      setError('Enter your email address');
      return;
    }
    setLoading(true);
    try {
      await auth.forgotPassword(email.trim());
      setSent(true);
    } catch (err) {
      setError(err.response?.data?.detail || err.message || 'Request failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <FlowLayout onNavigate={onNavigate} activePane="forgot">
      <AuthCard
        kicker="FORGOT PASSWORD"
        title={sent ? <>Check your <em>email</em>.</> : <>Forgot your <em>password</em>?</>}
        subtitle={sent
          ? 'If an account exists for that email, we sent a link to reset your password.'
          : 'Enter your email and we’ll send a reset link. Links expire in 30 minutes.'}
        widthClassName="max-w-[560px]"
      >
        {sent ? (
          <button type="button" className="btn btn-purple btn-lg w-full justify-center" onClick={() => onNavigate('login')}>
            Back to sign in <span className="arrow">→</span>
          </button>
        ) : (
          <>
            {error ? (
              <div className="mb-4 flex items-center gap-2 rounded-[14px] border border-[var(--taali-danger-border)] bg-[var(--taali-danger-soft)] p-3">
                <AlertTriangle size={18} className="shrink-0 text-[var(--taali-danger)]" />
                <span className="text-sm text-[var(--ink)]">{error}</span>
              </div>
            ) : null}
            <form onSubmit={handleSubmit} className="space-y-4">
              <label className="field">
                <span className="k">Email</span>
                <input type="email" placeholder="you@company.com" value={email} onChange={(e) => setEmail(e.target.value)} />
              </label>
              <button type="submit" className="btn btn-purple btn-lg w-full justify-center" disabled={loading}>
                {loading ? 'Sending...' : <>Send reset link <span className="arrow">→</span></>}
              </button>
            </form>
            <div className="mt-5 text-center text-[13.5px] text-[var(--mute)]">
              Remembered it?{' '}
              <button type="button" className="font-medium text-[var(--purple)] hover:underline" onClick={() => onNavigate('login')}>
                Back to sign in
              </button>
            </div>
          </>
        )}
      </AuthCard>
    </FlowLayout>
  );
};
