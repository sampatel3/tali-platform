import React, { useState } from 'react';
import { AlertTriangle } from 'lucide-react';

import { auth } from '../../shared/api';
import { AuthShell, AuthField } from './AuthShell';

export const ResetPasswordPage = ({ onNavigate, token }) => {
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [success, setSuccess] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    if (password.length < 8) {
      setError('Password must be at least 8 characters long.');
      return;
    }
    if (password !== confirm) {
      setError('Passwords do not match');
      return;
    }
    if (!token) {
      setError('Invalid reset link. Request a new one.');
      return;
    }
    setLoading(true);
    try {
      await auth.resetPassword(token, password);
      setSuccess(true);
    } catch (err) {
      setError(err.response?.data?.detail || err.message || 'Reset failed');
    } finally {
      setLoading(false);
    }
  };

  if (!token) {
    return (
      <AuthShell onNavigate={onNavigate} kicker="SET A NEW PASSWORD" title="Invalid link" sub="This reset link is missing or invalid. Request a new one from the login page.">
        <button type="button" className="mc-auth-cta" onClick={() => onNavigate('forgot-password')}>
          Request new link →
        </button>
      </AuthShell>
    );
  }

  if (success) {
    return (
      <AuthShell onNavigate={onNavigate} kicker="SET A NEW PASSWORD" title="Password updated" sub="You can now sign in with your new password.">
        <button type="button" className="mc-auth-cta" onClick={() => onNavigate('login')}>
          Sign in →
        </button>
      </AuthShell>
    );
  }

  return (
    <AuthShell
      onNavigate={onNavigate}
      kicker="SET A NEW PASSWORD"
      title="Choose something memorable"
      sub="Use a passphrase, not a word. Mix at least 12 characters; we won't make you add a symbol."
      topRight={(
        <span>
          Back to{' '}
          <button
            type="button"
            onClick={() => onNavigate('login')}
            style={{ background: 'none', border: 0, color: 'var(--purple)', fontWeight: 500, cursor: 'pointer', padding: 0, font: 'inherit' }}
          >
            sign in
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
          label="New password"
          name="password"
          type="password"
          autoComplete="new-password"
          placeholder="••••••••"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          helper="12+ characters. Strong: a string of words you'll remember."
        />
        <AuthField
          label="Confirm new password"
          name="confirm"
          type="password"
          autoComplete="new-password"
          placeholder="••••••••"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
        />
        <button type="submit" className="mc-auth-cta" disabled={loading}>
          {loading ? 'Updating password...' : 'Update password →'}
        </button>
      </form>
    </AuthShell>
  );
};
