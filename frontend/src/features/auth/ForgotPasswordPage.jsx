import React, { useState } from 'react';
import { AlertTriangle } from 'lucide-react';

import { auth } from '../../shared/api';
import { AuthShell, AuthField } from './AuthShell';

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

  if (sent) {
    return (
      <AuthShell
        onNavigate={onNavigate}
        kicker="ACCOUNT RECOVERY"
        title="Check your email"
        sub="If an account exists for that email, we sent a link to reset your password."
      >
        <button type="button" className="mc-auth-cta" onClick={() => onNavigate('login')}>
          Back to sign in →
        </button>
      </AuthShell>
    );
  }

  return (
    <AuthShell
      onNavigate={onNavigate}
      kicker="ACCOUNT RECOVERY"
      title="Forgot your password?"
      sub="Enter your work email and we'll send a single-use link. The link expires in 30 minutes."
      topRight={(
        <span>
          Remembered?{' '}
          <button
            type="button"
            onClick={() => onNavigate('login')}
            style={{ background: 'none', border: 0, color: 'var(--purple)', fontWeight: 500, cursor: 'pointer', padding: 0, font: 'inherit' }}
          >
            Back to sign in
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
      <form onSubmit={handleSubmit}>
        <AuthField
          label="Work email"
          name="email"
          type="email"
          autoComplete="email"
          placeholder="you@company.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <button type="submit" className="mc-auth-cta" disabled={loading}>
          {loading ? 'Sending...' : 'Send reset link →'}
        </button>
      </form>
    </AuthShell>
  );
};
